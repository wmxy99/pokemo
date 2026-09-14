#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""清洗并导入「Pokémon TCG AI Battle Challenge Strategy」数据集（EN/JP CSV）。

数据集结构
----------
zip 内含：
  EN Card Data.csv   —— 2022 行 / 1267 张卡（**一行一招式或特性**）
  JP Card Data.csv   —— 与 EN **逐行同序**的日文对照（カード名 等）
  4 份 PDF           —— 卡 ID 列表（体积大，与本脚本无关，不处理）

清洗规则（质量报告见 data/cleaned/clean_report.md）
---------------------------------------------------
  · 占位值归一：``n/a`` / ``''`` / ``-`` → ``null``
  · 能量符号归一：``{G}{R}{W}{L}{P}{F}{D}{M}{N}{C}`` · ``●``（无色）·
    ``{A}``（任意能量）· ``{Team Rocket}``
  · 费用：``●`` 与 ``{X}`` 混排 → 有序能量表 + ``cost_total``
  · 伤害：纯数字 / ``N×``（倍数）/ ``-N``（变值减伤）→ ``{base, kind, raw}``
  · 招式名 ``[Ability] X`` → 特性；``[Tera]`` → 规则文本（非招式）
  · 同 Card ID 多行 → 聚合为一张卡（``attacks[]`` + ``abilities[]`` + ``other_text``）
  · EN + JP 按行序合并 → ``name``（英文，数据键）+ ``name_ja``
  · 与引擎自撰卡池（``engine/data/cards_real.json``）按名匹配 → 回填 ``name_zh``
    与 ``engine_ready``（能否直接进引擎对战）

产物
----
  data/cleaned/cards_challenge.json   规范化卡库（入库源，可版本管理）
  data/cleaned/clean_report.md        清洗质量报告（人读）
  data/db/pokelab.db · cards 表       写入/更新（网站 API 读取）

用法
----
  python scripts/import_challenge_dataset.py --zip <path.zip>
  python scripts/import_challenge_dataset.py --zip <path.zip> --dry-run
  python scripts/import_challenge_dataset.py --csv-dir <dir>      # 已解压的 CSV 目录
"""
from __future__ import annotations

import argparse
import collections
import csv
import io
import json
import re
import sqlite3
import sys
import zipfile
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]          # packages/card-db
REPO = PKG.parents[1]                              # pokelab-ai
DATA = PKG / "data"
OUT_JSON = DATA / "cleaned" / "cards_challenge.json"
OUT_REPORT = DATA / "cleaned" / "clean_report.md"
DB_PATH = DATA / "db" / "pokelab.db"
ENGINE_CARDS = REPO / "packages" / "engine" / "engine" / "data" / "cards_real.json"

SOURCE_TAG = ("M1 卡池补齐 · pokemon-tcg-ai-battle-challenge-strategy · "
              "仅卡名/数值/规则文本（无官方图像，C2）")

# ---------------- 常量表 ----------------

# 能量符号 → (代码, 中文, 是否基本能量)
ENERGY_MAP = {
    "G": ("grass", "草"),
    "R": ("fire", "火"),
    "W": ("water", "水"),
    "L": ("lightning", "雷"),
    "P": ("psychic", "超"),
    "F": ("fighting", "斗"),
    "D": ("darkness", "恶"),
    "M": ("metal", "钢"),
    "N": ("dragon", "龙"),
    "C": ("colorless", "无色"),
}
# 特殊占位（非具体属性）
SPECIAL_ENERGY = {
    "A": "any",                 # 任意能量（如 Legacy/Prism Energy）
    "Team Rocket": "team_rocket",
}

STAGE_MAP = {
    "Basic Pokémon": ("Pokemon", "Basic", 0),
    "Stage 1 Pokémon": ("Pokemon", "Stage 1", 1),
    "Stage 2 Pokémon": ("Pokemon", "Stage 2", 2),
    "Item": ("Trainer", "Item", None),
    "Supporter": ("Trainer", "Supporter", None),
    "Pokémon Tool": ("Trainer", "Pokémon Tool", None),
    "Stadium": ("Trainer", "Stadium", None),
    "Basic Energy": ("Energy", "Basic Energy", None),
    "Special Energy": ("Energy", "Special Energy", None),
}

NULLISH = {"", "n/a", "n/a.", "-", "—", "none", "null", "unknown"}
_SYM_RE = re.compile(r"\{([^}]+)\}")

# 属性代码 → 英文名（用于生成基础能量别名，如 Basic {P} Energy ↔ Basic Psychic Energy）
TYPE_EN = {
    "grass": "Grass", "fire": "Fire", "water": "Water", "lightning": "Lightning",
    "psychic": "Psychic", "fighting": "Fighting", "darkness": "Darkness",
    "metal": "Metal", "dragon": "Dragon", "colorless": "Colorless",
}


def norm_key(name: str) -> str:
    """匹配键：小写、弯撇号→直撇号、去非字母数字。

    数据集中 53 张卡用弯撇号（`N’s Zoroark ex`）、142 张用直撇号（`Arven's …`），
    必须归一才能与既有卡池/卡表对上。
    """
    s = (name or "").strip().lower()
    s = s.replace("’", "'").replace("‘", "'").replace("`", "'")
    return re.sub(r"[^a-z0-9]+", "", s)


def aliases_for(card: dict) -> list[str]:
    """额外匹配名（不含 name 本身）：基础能量按属性生成英文全名"""
    out: list[str] = []
    if card.get("subtype") == "Basic Energy":
        for t in card.get("types") or []:
            en = TYPE_EN.get(t)
            if en:
                out.append(f"Basic {en} Energy")
                out.append(f"{en} Energy")
        out.append("Basic Energy")
    return out


def norm(v) -> str | None:
    """占位值 → None；否则去首尾空白"""
    if v is None:
        return None
    s = str(v).strip()
    return None if s.lower() in NULLISH else s


def parse_energy(text) -> list[str]:
    """能量串 → 有序代码表。'●' 与 '{X}' 混排；未知符号单独记录。"""
    s = norm(text)
    if not s:
        return []
    out: list[str] = []
    pos = 0
    for m in _SYM_RE.finditer(s):
        # 符号前的裸 ● 逐个记为无色
        out.extend(["colorless"] * s[pos:m.start()].count("●"))
        sym = m.group(1)
        if sym in ENERGY_MAP:
            out.append(ENERGY_MAP[sym][0])
        elif sym in SPECIAL_ENERGY:
            out.append(SPECIAL_ENERGY[sym])
        else:
            out.append(f"unknown:{sym}")
        pos = m.end()
    out.extend(["colorless"] * s[pos:].count("●"))
    return out


def parse_cost(text) -> dict:
    """招式费用：{'cost': [...], 'cost_total': n, 'raw': str|None}"""
    raw = norm(text)
    seq = parse_energy(raw)
    return {"cost": seq, "cost_total": len(seq), "raw": raw}


def parse_damage(text) -> dict:
    """伤害：{base:int|None, kind:'fixed'|'multiplier'|'variable', raw:str|None}

    · '120'  → fixed 120
    · '20×'  → multiplier 20（每份 20）
    · '-120' → variable 120（变值：含「减少 N」类说明）
    """
    raw = norm(text)
    if raw is None:
        return {"base": None, "kind": None, "raw": None}
    m = re.match(r"^(-?)(\d+)\s*(×|x)?$", raw)
    if not m:
        return {"base": None, "kind": "other", "raw": raw}
    sign, num, mult = m.group(1), int(m.group(2)), m.group(3)
    if mult:
        return {"base": num, "kind": "multiplier", "raw": raw}
    if sign == "-":
        return {"base": num, "kind": "variable", "raw": raw}
    return {"base": num, "kind": "fixed", "raw": raw}


def parse_move_name(text) -> tuple[str | None, str | None]:
    """招式名 → (kind, name)；'[Ability] X' → ('ability','X')；'[Tera]' → ('rule', None)"""
    s = norm(text)
    if s is None:
        return None, None
    m = re.match(r"^\[([^\]]+)\]\s*(.*)$", s)
    if m:
        tag, rest = m.group(1).strip(), m.group(2).strip()
        if tag.lower() == "ability":
            return "ability", (rest or None)
        return "rule", (rest or None)          # [Tera] 等：规则说明，非招式
    return "attack", s


def parse_stage(text) -> tuple[str | None, str | None, int | None]:
    s = norm(text)
    if s is None:
        return None, None, None
    if s in STAGE_MAP:
        return STAGE_MAP[s]
    # 兜底：按关键词猜
    low = s.lower()
    if "pokémon" in low or "pokemon" in low:
        if "stage 2" in low:
            return "Pokemon", "Stage 2", 2
        if "stage 1" in low:
            return "Pokemon", "Stage 1", 1
        return "Pokemon", s, 0
    if "energy" in low:
        return "Energy", s, None
    return "Trainer", s, None


def is_special_rule_only(name: str, kind: str, effect: str | None) -> bool:
    """[Tera] / 无招式名的纯规则行 → 归入 other_text"""
    return kind == "rule" or (kind is None and bool(effect))


def build_card(rows: list[dict], jp_rows: list[dict], KE: list[str],
               engine_index: dict, issues: dict) -> dict:
    """同一 Card ID 的多行 → 一张卡"""
    r0 = rows[0]
    name = (r0[KE[1]] or "").strip()
    supertype, subtype, stage = parse_stage(r0[KE[4]])
    series = norm(r0[KE[2]]) or "UNKNOWN"
    number = norm(r0[KE[3]]) or name

    attacks: list[dict] = []
    abilities: list[dict] = []
    other_texts: list[str] = []

    for r in rows:
        kind, mname = parse_move_name(r[KE[13]])
        effect = norm(r[KE[16]])
        if kind == "attack":
            cost = parse_cost(r[KE[14]])
            dmg = parse_damage(r[KE[15]])
            attacks.append({
                "name": mname,
                "cost": cost["cost"],
                "cost_total": cost["cost_total"],
                "cost_raw": cost["raw"],
                "damage": dmg["base"],
                "damage_kind": dmg["kind"],
                "damage_raw": dmg["raw"],
                "effect": effect,
            })
        elif kind == "ability":
            abilities.append({"name": mname, "effect": effect})
        else:                                    # rule 行 / 无招式名的卡
            if effect:
                other_texts.append(effect)

    if stage == 0 and subtype != "Basic":
        issues["stage_basic_odd"] += 1
    if stage in (1, 2) and not norm(r0[KE[7]]):
        issues["evolve_missing_from"] += 1

    jp = jp_rows[0] if jp_rows else {}
    KJ = list(jp.keys()) if jp else []
    name_ja = (jp.get(KJ[1]) if jp else None) or None

    hp_raw = norm(r0[KE[8]])
    card = {
        "id": int(r0[KE[0]]) if (r0[KE[0]] or "").strip().isdigit() else None,
        "name": name,
        "name_key": norm_key(name),
        "name_ja": name_ja,
        "name_zh": "",
        "series": series,
        "number": number,
        "supertype": supertype,
        "subtype": subtype,
        "stage": stage,
        "types": parse_energy(r0[KE[9]]),
        "hp": int(hp_raw) if (hp_raw or "").isdigit() else None,
        "weakness": (parse_energy(r0[KE[10]]) or [None])[0],
        "resistance": (parse_energy(r0[KE[11]]) or [None])[0],
        "retreat": int(norm(r0[KE[12]])) if (norm(r0[KE[12]]) or "").isdigit() else None,
        "rule": norm(r0[KE[5]]),
        "category": norm(r0[KE[6]]),
        "evolves_from": norm(r0[KE[7]]),
        "attacks": attacks,
        "abilities": abilities,
        "other_text": "\n\n".join(other_texts) or None,
        "engine_ready": False,
        "source": SOURCE_TAG,
    }
    # 匹配引擎自撰卡池：先用归一匹配键，再用别名（基础能量命名差异）
    card["aliases"] = aliases_for(card)
    eng = engine_index.get(card["name_key"])
    matched_by = "name_key" if eng else None
    if not eng:
        for al in card["aliases"]:
            eng = engine_index.get(norm_key(al))
            if eng:
                matched_by = f"alias:{al}"
                break
    if eng:
        card["engine_ready"] = True
        card["name_zh"] = eng.get("name_zh") or ""
        card["engine_match"] = matched_by
    issues["zh_backfill" if eng else "engine_unmatched"] += 1
    return card


def load_engine_index() -> dict:
    """引擎自撰卡池（40 张）→ 按**归一匹配键**索引，供回填中文名与可对战标记"""
    idx: dict = {}
    try:
        j = json.loads(ENGINE_CARDS.read_text(encoding="utf-8"))
        for c in j.get("cards", []):
            idx[norm_key(c.get("name"))] = {
                "name": c.get("name"),
                "name_zh": c.get("name_zh") or "",
                "supertype": c.get("supertype"),
            }
    except Exception as e:                            # noqa: BLE001
        print(f"[warn] 引擎卡池读取失败（中文名回填跳过）：{e}")
    return idx


def read_rows(zip_path: Path | None, csv_dir: Path | None) -> tuple[list, list]:
    def _read(p: Path):
        with io.open(p, encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))

    if csv_dir is not None:
        en = _read(csv_dir / "EN Card Data.csv")
        jp = _read(csv_dir / "JP Card Data.csv")
        return en, jp
    assert zip_path is not None
    with zipfile.ZipFile(zip_path) as z:
        def _from_zip(suffix):
            n = next((x for x in z.namelist()
                      if x.endswith(suffix) and not x.endswith("_.csv")
                      and " " in Path(x).name), None)
            if n is None:
                raise SystemExit(f"zip 中未找到 {suffix}")
            with z.open(n) as fh:
                return list(csv.DictReader(io.TextIOWrapper(fh, "utf-8-sig")))
        return _from_zip("EN Card Data.csv"), _from_zip("JP Card Data.csv")


def clean(zip_path, csv_dir) -> tuple[list, dict]:
    en, jp = read_rows(zip_path, csv_dir)
    KE = list(en[0].keys())
    KJ = list(jp[0].keys()) if jp else []
    engine_index = load_engine_index()

    issues = collections.Counter()
    # 按 Card ID 聚合（保持出现顺序）
    grouped: dict[str, list] = collections.OrderedDict()
    for r in en:
        grouped.setdefault(r[KE[0]], []).append(r)
    jp_grouped: dict[str, list] = collections.OrderedDict()
    for r in jp:
        jp_grouped.setdefault(r[KJ[0]], []).append(r)

    cards: list[dict] = []
    for cid, rows in grouped.items():
        cards.append(build_card(rows, jp_grouped.get(cid, []), KE,
                                engine_index, issues))

    # 统计
    stats = {
        "rows_in": len(en),
        "cards_out": len(cards),
        "by_supertype": dict(collections.Counter(c["supertype"] for c in cards)),
        "by_subtype": dict(collections.Counter(c["subtype"] for c in cards)),
        "by_series_top": collections.Counter(c["series"] for c in cards).most_common(12),
        "attacks_total": sum(len(c["attacks"]) for c in cards),
        "abilities_total": sum(len(c["abilities"]) for c in cards),
        "no_move_cards": sum(1 for c in cards if not c["attacks"] and not c["abilities"]),
        "engine_ready": sum(1 for c in cards if c["engine_ready"]),
        "engine_match_alias": sum(1 for c in cards
                                  if str(c.get("engine_match") or "").startswith("alias:")),
        "with_zh": sum(1 for c in cards if c["name_zh"]),
        "apostrophe_curly": sum(1 for c in cards if "’" in (c["name"] or "")),
        "cards_with_aliases": sum(1 for c in cards if c.get("aliases")),
        "multi_move_cards": sum(1 for c in cards if len(c["attacks"]) >= 2),
        "damage_kinds": dict(collections.Counter(
            a["damage_kind"] for c in cards for a in c["attacks"])),
        "cost_symbols": dict(collections.Counter(
            e for c in cards for a in c["attacks"] for e in a["cost"]).most_common()),
        "issues": dict(issues),
    }
    # 质量问题清单（用于报告）
    ds_keys = set()
    for c in cards:
        ds_keys.add(c["name_key"])
        for al in c.get("aliases") or []:
            ds_keys.add(norm_key(al))
    engine_missing = sorted(
        v["name"] for k, v in engine_index.items() if k not in ds_keys)
    stats["qa"] = {
        "engine_pool_not_in_dataset": engine_missing,
        "missing_hp": [c["name"] for c in cards
                       if c["supertype"] == "Pokemon" and c["hp"] is None],
        "missing_type": [c["name"] for c in cards
                         if c["supertype"] == "Pokemon" and not c["types"]],
        "missing_ja": [c["name"] for c in cards if not c["name_ja"]],
        "no_effect_no_move": [c["name"] for c in cards
                              if not c["attacks"] and not c["abilities"]
                              and not c["other_text"]],
        "unknown_energy": sorted({e for c in cards for a in c["attacks"]
                                  for e in a["cost"] if e.startswith("unknown:")}),
        "dup_key": [f"{k[0]} · {k[1]}-{k[2]}" for k, n in collections.Counter(
            (c["name"], c["series"], c["number"]) for c in cards).items() if n > 1],
    }
    return cards, stats


def write_report(cards: list[dict], stats: dict, path: Path) -> None:
    L = ["# 卡牌数据集清洗报告",
         "",
         f"- 数据源：`pokemon-tcg-ai-battle-challenge-strategy.zip`（EN + JP Card Data.csv）",
         "- 产物：`data/cleaned/cards_challenge.json`（入库源）· `data/db/pokelab.db`（cards 表）",
         "- 合规：仅清洗与存储**卡名 / 数值 / 规则文本**，**不使用官方卡图**（C2）；"
         "效果文本为功能机制引用，非美术素材。",
         "",
         "## 1. 清洗结果汇总",
         "",
         "| 项 | 数值 |",
         "|---|---|",
         f"| 输入数据行 | {stats['rows_in']} |",
         f"| **聚合为卡牌数** | **{stats['cards_out']}** |",
         f"| 招式总数 | {stats['attacks_total']} |",
         f"| 特性总数 | {stats['abilities_total']} |",
         f"| 多招式卡（≥2 招式） | {stats['multi_move_cards']} |",
         f"| 无招式卡（训练家/能量/规则） | {stats['no_move_cards']} |",
         f"| 引擎可直接对战（已建模） | {stats['engine_ready']} |",
         f"| 回填中文名 | {stats['with_zh']} |",
         "",
         "### 卡牌构成",
         "",
         "| 超类型 | 数量 |",
         "|---|---|",
         ]
    for k, v in sorted(stats["by_supertype"].items(), key=lambda x: -x[1]):
        L.append(f"| {k} | {v} |")
    L += ["", "| 子类型 | 数量 |", "|---|---|"]
    for k, v in sorted(stats["by_subtype"].items(), key=lambda x: -x[1]):
        L.append(f"| {k} | {v} |")
    L += ["", "| 系列（Top 12） | 数量 |", "|---|---|"]
    for k, v in stats["by_series_top"]:
        L.append(f"| {k} | {v} |")
    L += ["", "### 规范化动作统计", "",
          "| 伤害类型 | 数量 | 说明 |", "|---|---|---|",
          f"| fixed | {stats['damage_kinds'].get('fixed', 0)} | 固定伤害 |",
          f"| multiplier | {stats['damage_kinds'].get('multiplier', 0)} | `N×` 每份 N |",
          f"| variable | {stats['damage_kinds'].get('variable', 0)} | `-N` 变值（含减伤/条件） |",
          f"| other | {stats['damage_kinds'].get('other', 0)} | 非常规写法（保留 raw） |",
          "",
          "费用符号（归一后）：" + ("、".join(
              f"{k}×{v}" for k, v in stats["cost_symbols"].items()) or "（无）"),
          "",
          "## 2. 已归一化的项（清洗动作）",
          "",
          "| 原数据 | 归一为 |",
          "|---|---|",
          "| `n/a` / 空串 / `-` | `null`（不再伪装成字符串） |",
          "| `●`、`{G}`… | 有序能量代码表（`colorless` / `grass` …）+ `cost_total` |",
          "| `{A}` | `any`（任意能量，如 Prism/Legacy Energy） |",
          "| `{Team Rocket}` | `team_rocket` |",
          "| `20×` | `{base:20, kind:'multiplier'}` |",
          "| `-120` | `{base:120, kind:'variable'}`（原值保留在 `raw`） |",
          "| `[Ability] X` | 拆入 `abilities[]`（与招式分离） |",
          "| `[Tera]` 等 | 归入 `other_text`（规则说明，非招式） |",
          "| 同 Card ID 多行 | 聚合为一张卡 |",
          "| EN + JP | `name`(EN 数据键) + `name_ja` |",
          "| 引擎自撰卡池命中 | 回填 `name_zh` + `engine_ready=true` |",
          "",
          "### 名称规范差异（重要）",
          "",
          f"- 数据集内撇号混用：弯撇号 `’` **{stats['apostrophe_curly']} 张** vs 直撇号 `'` 其余 —— "
          "统一由 `name_key`（小写 + 撇号归一 + 去符号）承担匹配，**原始卡名保持不变**；",
          f"- 基础能量命名差异：数据集用 `Basic {{P}} Energy`，既有卡池用 `Basic Psychic Energy` "
          f"—— 由 `aliases[]` 生成英文全名别名（共 {stats['cards_with_aliases']} 张带别名）；"
          f"其中 {stats['engine_match_alias']} 张**靠别名**匹配上引擎卡池；",
          "- 数据集为「AI Battle Challenge」**卡池子集**（1267 张）：`Professor's Research` / "
          "`Counter Catcher` / `Earthen Vessel` / `Nest Ball` 等**不在其中**，既有卡表中这些卡"
          "仍以引擎自撰数据为准（见 §3 诚实标注）。",
          "",
          "## 3. 质量问题清单（已如实记录，未静默丢弃）",
          "",
          ]
    qa = stats["qa"]
    L += [
        f"- 宝可梦缺 HP：{len(qa['missing_hp'])}"
        + (f" —— {', '.join(qa['missing_hp'][:6])}…" if qa["missing_hp"] else ""),
        f"- 宝可梦缺属性：{len(qa['missing_type'])}"
        + (f" —— {', '.join(qa['missing_type'][:6])}…" if qa["missing_type"] else ""),
        f"- 缺日文名：{len(qa['missing_ja'])}"
        + (f" —— {', '.join(qa['missing_ja'][:6])}…" if qa["missing_ja"] else ""),
        f"- 无招式且无效果文本：{len(qa['no_effect_no_move'])}"
        + (f" —— {', '.join(qa['no_effect_no_move'][:6])}…" if qa["no_effect_no_move"] else ""),
        f"- 未知能量符号：{qa['unknown_energy'] or '无'}",
        f"- (卡名,系列,编号) 重复：{len(qa['dup_key'])}"
        + (f" —— {', '.join(qa['dup_key'][:6])}…" if qa["dup_key"] else ""),
        f"- 进化卡缺「进化前」：{stats['issues'].get('evolve_missing_from', 0)}",
        f"- 引擎自撰卡池中**不在本数据集**的卡：{len(qa['engine_pool_not_in_dataset'])}"
        + (f" —— {', '.join(qa['engine_pool_not_in_dataset'])}"
           if qa["engine_pool_not_in_dataset"] else ""),
        "",
        "## 4. 诚实标注",
        "",
        "- 本数据集提供**卡面机制文本**（官方英文原文），项目**不使用官方卡图**（C2 红线）；",
        "- 引擎未建模的卡（"
        f"{stats['cards_out'] - stats['engine_ready']} 张）**可浏览/可组卡但不可对战**，"
        "由 `engine_ready` 标记，UI 与导入校验据此显示灰名单；",
        "- 中文名仅回填自引擎自撰卡池（"
        f"{stats['with_zh']} 张）；其余卡的中文名属**待译项**，不做机器硬译以免误导。",
        "",
        ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


def load_sqlite(cards: list[dict], db_path: Path) -> dict:
    """写 cards 表：按 (name, series, number) upsert（幂等）

    同时清理骨架期占位行（``source='demo-mock'``，非真实数据）。
    """
    if not db_path.exists():
        raise SystemExit(f"数据库不存在：{db_path}（先跑 schema.sql / seed）")
    con = sqlite3.connect(str(db_path))
    n_new = n_upd = 0
    try:
        n_demo = con.execute(
            "SELECT count(*) FROM cards WHERE source='demo-mock'").fetchone()[0]
        if n_demo:
            con.execute("DELETE FROM cards WHERE source='demo-mock'")
        for c in cards:
            types = json.dumps(c["types"], ensure_ascii=False)
            # rules_text：无招式卡的效果 / 宝可梦的其他说明（供检索与展示）
            rules_parts = [c["other_text"]] if c["other_text"] else []
            for a in c["attacks"]:
                if a["effect"]:
                    rules_parts.append(a["effect"])
            for ab in c["abilities"]:
                if ab["effect"]:
                    rules_parts.append(ab["effect"])
            rules_text = "\n\n".join(rules_parts)
            attacks_json = json.dumps(
                {"attacks": c["attacks"], "abilities": c["abilities"]},
                ensure_ascii=False)
            effects_json = json.dumps({
                "library": {
                    "card_id": c["id"], "subtype": c["subtype"],
                    "stage": c["stage"], "rule": c["rule"],
                    "category": c["category"], "evolves_from": c["evolves_from"],
                    "weakness": c["weakness"], "resistance": c["resistance"],
                    "retreat": c["retreat"], "name_ja": c["name_ja"],
                    "name_zh": c["name_zh"], "engine_ready": c["engine_ready"],
                    # 匹配辅助（网站卡池/导入校验复用，避免撇号与能量命名差异导致漏匹配）
                    "name_key": c["name_key"],
                    "aliases": c.get("aliases") or [],
                    "kind": {"Pokemon": "pokemon", "Trainer": "trainer",
                             "Energy": "energy"}.get(c["supertype"], "unknown"),
                    "is_basic": bool(c["supertype"] == "Pokemon" and c["stage"] == 0),
                    "is_basic_energy": c["subtype"] == "Basic Energy",
                },
            }, ensure_ascii=False)
            status = "active"
            row = con.execute(
                "SELECT id FROM cards WHERE name=? AND series=? AND number=?",
                (c["name"], c["series"], c["number"])).fetchone()
            if row:
                con.execute(
                    "UPDATE cards SET supertype=?, types=?, hp=?, rules_text=?, "
                    "attacks_json=?, effects_json=?, status=?, source=?, "
                    "updated_at=datetime('now','localtime') WHERE id=?",
                    (c["supertype"], types, c["hp"], rules_text, attacks_json,
                     effects_json, status, SOURCE_TAG, row[0]))
                n_upd += 1
            else:
                con.execute(
                    "INSERT INTO cards(name,series,number,supertype,types,hp,"
                    "rules_text,attacks_json,effects_json,status,source) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (c["name"], c["series"], c["number"], c["supertype"], types,
                     c["hp"], rules_text, attacks_json, effects_json, status,
                     SOURCE_TAG))
                n_new += 1
        con.commit()
    finally:
        con.close()
    return {"new": n_new, "updated": n_upd, "removed_demo": n_demo}


def main() -> int:
    ap = argparse.ArgumentParser(description="清洗并导入卡牌数据集")
    ap.add_argument("--zip", type=Path, help="数据集 zip 路径")
    ap.add_argument("--csv-dir", type=Path, help="已解压的 CSV 目录")
    ap.add_argument("--db", type=Path, default=DB_PATH, help="目标 SQLite")
    ap.add_argument("--out-json", type=Path, default=OUT_JSON)
    ap.add_argument("--out-report", type=Path, default=OUT_REPORT)
    ap.add_argument("--dry-run", action="store_true", help="只清洗与报告，不写库")
    args = ap.parse_args()

    if not args.zip and not args.csv_dir:
        raise SystemExit("需提供 --zip 或 --csv-dir")

    cards, stats = clean(args.zip, args.csv_dir)

    # 1) 规范化 JSON
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {
            "version": "1.0",
            "source": "pokemon-tcg-ai-battle-challenge-strategy.zip",
            "rows_in": stats["rows_in"],
            "cards": len(cards),
            "generated_by": "packages/card-db/scripts/import_challenge_dataset.py",
            "compliance": ("仅卡名/数值/规则文本，无官方图像（C2）；"
                           "引擎未建模卡由 engine_ready=false 标记"),
        },
        "stats": {k: v for k, v in stats.items() if k != "qa"},
        "cards": cards,
    }
    args.out_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    # 2) 报告
    write_report(cards, stats, args.out_report)

    # 3) 入库
    res = {"new": 0, "updated": 0, "removed_demo": 0}
    if not args.dry_run:
        res = load_sqlite(cards, args.db)

    print(f"[clean] 行 {stats['rows_in']} → 卡 {stats['cards_out']}")
    print(f"[clean] 招式 {stats['attacks_total']} · 特性 {stats['abilities_total']} · "
          f"引擎可对战 {stats['engine_ready']}（别名命中 {stats['engine_match_alias']}）· "
          f"中文名 {stats['with_zh']}")
    print(f"[clean] 弯撇号 {stats['apostrophe_curly']} 张 · 带别名 "
          f"{stats['cards_with_aliases']} 张")
    print(f"[out]   {args.out_json}")
    print(f"[out]   {args.out_report}")
    if args.dry_run:
        print("[db]    dry-run：未写库")
    else:
        print(f"[db]    cards 表：新增 {res['new']} · 更新 {res['updated']} · "
              f"清理占位 {res['removed_demo']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
