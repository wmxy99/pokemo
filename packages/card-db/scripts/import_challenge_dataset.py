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


def _pokemon_signature(card: dict) -> tuple:
    """招式名 + 特性名集合 —— 同名不同印刷的**硬判据**

    仅凭卡名匹配会把另一印刷的招式算到这张卡头上（ASC 104 Medicham 与
    DRI 100 Medicham 同名但招式完全不同）。签名一致才允许 name_key 回落。
    """
    atk = tuple(sorted(
        k for a in (card.get("attacks") or [])
        if (k := norm_key(a.get("name")))))
    ab = tuple(sorted(
        k for a in (card.get("abilities") or [])
        if (k := norm_key(a.get("name")))))
    return atk, ab


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
               engine_index: dict, issues: dict,
               images: dict | None = None) -> dict:
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
        # 卡图引用（可选侧车文件；合规 6.1：须标注权利方）
        "image_url": ((images or {}).get(f"{series}-{number}".upper())
                      or (images or {}).get(norm_key(name))
                      or None),
    }
    # 匹配引擎自撰卡池：先用归一匹配键，再用别名（基础能量命名差异）
    return apply_engine_match(card, engine_index, issues)


def _load_effects_module():
    """载入 agent.effects（K2 解析链）；失败返回 None（解析为可选项）"""
    import sys
    for pkg in ("agent", "engine"):
        p = REPO / "packages" / pkg
        if p.exists() and str(p) not in sys.path:
            sys.path.insert(0, str(p))
    try:
        from agent import effects                          # noqa: WPS433
        return effects
    except Exception as e:                                 # noqa: BLE001
        print(f"[warn] agent.effects 不可用，跳过效果解析：{e}")
        return None


def parse_effects_pass(cards: list[dict], backend: str = "rule") -> dict:
    """对全库招式/特性/卡牌效果跑 K2 解析，产物带 **AI 标识**（合规 6.1）

    每张卡的解析结果写入 ``card["parse"]``：
      ``{items[], specs[], errors[], tags[], unsupported[], generated_by,
         ai_generated, ai_notice}``
    """
    E = _load_effects_module()
    st = {"cards_parsed": 0, "items": 0, "specs": 0, "errors": 0,
          "engine_tags": 0, "unsupported": 0, "backend": backend,
          "ai_notice": None}
    if E is None:
        return st
    st["ai_notice"] = getattr(E, "AI_NOTICE", "")
    kind_map = {"Trainer": "item", "Energy": "energy"}
    for c in cards:
        items, specs, errs, tags, unsup = [], [], [], [], []
        # 招式
        for a in c["attacks"]:
            if not a.get("effect"):
                continue
            r = E.parse_effects(a["effect"], kind="attack",
                                name=a.get("name") or "", backend=backend)
            m = E.to_engine_tags(r["specs"])
            items.append({"target": "attack", "name": a.get("name"),
                          "specs": r["specs"], "errors": r["errors"],
                          "engine_tags": m["tags"],
                          "unsupported": m["unsupported"]})
            specs += r["specs"]; errs += r["errors"]
            tags += m["tags"]; unsup += m["unsupported"]
        # 特性
        for ab in c["abilities"]:
            if not ab.get("effect"):
                continue
            r = E.parse_effects(ab["effect"], kind="ability",
                                name=ab.get("name") or "", backend=backend)
            m = E.to_engine_tags(r["specs"])
            items.append({"target": "ability", "name": ab.get("name"),
                          "specs": r["specs"], "errors": r["errors"],
                          "engine_tags": m["tags"],
                          # 发动代价（2026-11-04 第四十九批）：「You must …
                          # in order to use this Ability.」—— 卡的属性而非效果，
                          # 由 `AbilityDef.cost` 承载、引擎在效果之前支付。
                          "cost": m.get("ability_cost") or "",
                          "unsupported": m["unsupported"]})
            specs += r["specs"]; errs += r["errors"]
            tags += m["tags"]; unsup += m["unsupported"]
        # 无招式卡（训练家/能量/规则）的整段效果
        if c["other_text"]:
            if c["subtype"] == "Supporter":
                k = "supporter"
            elif c["subtype"] == "Stadium":
                # 场地单独成 kind：卡面效果只能由 `_detect_stadium` 解释，
                # 落回通用解析会把「混乱不恢复」「检索进化」读成 confuse/candy
                k = "stadium"
            else:
                k = kind_map.get(c["supertype"], "rule")
            r = E.parse_effects(c["other_text"], kind=k,
                                name=c["name"], backend=backend)
            m = E.to_engine_tags(r["specs"])
            items.append({"target": "card", "name": c["name"],
                          "specs": r["specs"], "errors": r["errors"],
                          "engine_tags": m["tags"],
                          # 打出代价（2026-11-02 第三十六批）：
                          # 「only if you discard N other cards from your hand」。
                          # 它**不是** tag（不是效果动作），是卡的属性，由
                          # `TrainerDef.play_cost` 承载、引擎在派发前执行。
                          "play_cost": m.get("play_cost") or "",
                          "unsupported": m["unsupported"]})
            specs += r["specs"]; errs += r["errors"]
            tags += m["tags"]; unsup += m["unsupported"]
        if items:
            c["parse"] = {
                "items": items, "specs": specs, "errors": errs,
                "engine_tags": tags, "unsupported": unsup,
                "generated_by": backend,
                "ai_generated": True,
                "ai_notice": st["ai_notice"],
            }
            st["cards_parsed"] += 1
            st["items"] += len(items); st["specs"] += len(specs)
            st["errors"] += len(errs); st["engine_tags"] += len(tags)
            st["unsupported"] += len(unsup)
    return st


def load_image_map() -> dict:
    """卡图映射（**可选**）：``data/card_images.json``

    格式（两种键都支持）::

        { "TWM-130": "https://…/dragapult-ex.png",
          "多龙巴鲁托ex": "https://…/dragapult-ex.png" }

    合规 6.1：卡图**可用但须标注权利方**（见 ``docs/COMPLIANCE.md``）；
    本脚本**不抓取**图像，只做引用登记——URL 由使用者自行提供并自负合规责任。
    """
    f = DATA / "card_images.json"
    if not f.exists():
        return {}
    try:
        raw = json.loads(f.read_text(encoding="utf-8"))
    except Exception as e:                                 # noqa: BLE001
        print(f"[warn] card_images.json 解析失败：{e}")
        return {}
    out: dict = {}
    for k, v in (raw or {}).items():
        if not v:
            continue
        out[str(k).strip().upper()] = v
        out[norm_key(str(k))] = v
    return out


def load_engine_index() -> dict:
    """引擎卡池（人工 + 扩展）→ 按**归一匹配键**索引，供回填中文名与可对战状态

    返回每项含：``name`` / ``name_zh`` / ``supertype`` / ``tag`` /
    ``status``（``mapped`` | ``approximated`` | ``unsupported``）/ ``reason``。

    诚实口径（C5）：
      · 训练家 ``tag=unsupported``（引擎无对应派发）的卡**不算可对战**，
        即使卡名命中卡池也必须标为不可执行——否则「可浏览 ≠ 可对战」会被静默掩盖；
      · 卡池扩容（v1.4）的**自动生成条目**（``provenance=dataset-derived``，
        解析链推导、非人工逐卡校对）标记为 ``approximated``。
    """
    idx: dict = {}
    # 同名多卡计数：用于识别「靠卡名命中、实为另一张同名卡」的假就绪。
    # 真实反例（2026-10-27）：卡库里有 4 张 Applin（TWM 126 / TWM 17 / SCR 12 /
    # DRI 16），扩展池只收了其中 2 张，另外 2 张**凭卡名**被判为可对战——
    # 卡面招式完全不同（检索 vs 抛币加伤）。这类"同名冒充"在局面上看不出来。
    name_variants: dict = {}
    pool_files = [ENGINE_CARDS]
    _ext = ENGINE_CARDS.parent / "cards_pool_ext.json"
    if _ext.exists():
        pool_files.append(_ext)
    try:
        for _pf in pool_files:
            j = json.loads(_pf.read_text(encoding="utf-8"))
            derived = (j.get("meta") or {}).get("provenance") == "dataset-derived"
            for c in j.get("cards", []):
                st, reason = "mapped", ""
                if c.get("supertype") == "Trainer":
                    if (c.get("tag") or "").strip() in ("", "unsupported"):
                        st, reason = "unsupported", (c.get("note")
                                                     or "训练家 tag 未实现")
                elif c.get("supertype") == "Pokemon":
                    bad = [ab for ab in (c.get("abilities") or [])
                           if (ab.get("tag") or "").strip() == "unsupported"]
                    if bad:
                        st = "approximated"
                        reason = (c.get("note")
                                  or "特性未实现（保留招式，灰名单近似）")
                if derived:
                    if st == "mapped":
                        st = "approximated"
                    reason = reason or "由解析链自动生成（非人工逐卡校对）"
                key = norm_key(c.get("name"))
                real = c.get("real") or {}
                _ps = str(real.get("set") or "").strip().upper()
                _pn = str(real.get("number") or "").strip()
                entry = {
                    "name": c.get("name"),
                    "uid": (c.get("uid")
                            or (f"{c.get('name')}@{_ps} {_pn}"
                                if _ps and _pn else f"{c.get('name')}@-")),
                    "printing": (_ps, _pn),
                    "name_zh": c.get("name_zh") or "",
                    "supertype": c.get("supertype"),
                    "tag": c.get("tag") or "",
                    "status": st,
                    "reason": reason,
                    # 同名回落时用于拒绝「同名不同印刷」：招式/特性名集合
                    "signature": _pokemon_signature(c),
                }
                # (set, number) 精确索引：与卡库的 series/number 同源，可消除同名歧义
                sn = (_ps, _pn)
                if sn[0] and sn[1]:
                    name_variants.setdefault(key, set()).add(sn)
                    idx[("__setnum__",) + sn] = entry
                # 人工卡池优先：同名时人工条目胜出，且不被扩展池降级
                if key in idx and derived and idx[key]["status"] != "unsupported":
                    continue
                idx[key] = entry
    except Exception as e:                            # noqa: BLE001
        print(f"[warn] 引擎卡池读取失败（中文名回填跳过）：{e}")
    # 打上歧义标记：同名但对应**多张不同卡**时，卡名不足以确认身份
    for key, names in name_variants.items():
        if len(names) > 1 and isinstance(idx.get(key), dict):
            idx[key]["ambiguous"] = sorted(names)
    return idx


def _match_engine(card: dict, engine_index: dict, issues: dict | None = None) -> tuple:
    """卡 → 引擎卡池条目 → ``(entry|None, matched_by|None)``

    匹配顺序（严格）：
      1. ``(series, number)`` —— 精确到印刷批次，**且卡名必须一致**；
         数据集存在个别 series/number 与卡名不匹配的脏行（如 SSP 164
         ``Brilliant Blender`` 与手工池 ``Crispin`` 同号），只看编号会把
         另一张卡的效果算到这张卡头上 —— 属静默错映射；
      2. 卡名 / 别名 —— 仅在**该卡名在池里唯一**时接受；
         同名多卡（``ambiguous``）一律拒绝，宁可判未收录也不冒充。
         对宝可梦还要求**招式/特性名集合一致**：同名不同印刷（如 ASC 104
         Medicham 与 DRI 100 Medicham）不满足时同样拒绝。仅凭名字接受会让
         引擎按名字取到另一印刷的招式 —— 属静默错映射。

    为什么必须有第 2 步的歧义拒绝：卡库 1267 张里同名不同卡很常见
    （同名但不同 set/number、招式完全不同），靠卡名匹配会把「另一张卡的能力」
    算到这张卡头上 —— 属静默错映射，且只有按编号对账才看得出。
    """
    series = str(card.get("series") or "").strip().upper()
    number = str(card.get("number") or "").strip()
    aliases = {card.get("name_key")} | {norm_key(al)
                                        for al in (card.get("aliases") or [])}
    if series and number:
        e = engine_index.get(("__setnum__", series, number))
        if e is not None:
            if norm_key(e.get("name")) in aliases:
                return e, "set:number"
            if issues is not None:
                issues["engine_setnum_name_mismatch"] = \
                    issues.get("engine_setnum_name_mismatch", 0) + 1
    for cand, how in [(card.get("name_key"), "name_key")] + [
            (norm_key(al), f"alias:{al}") for al in (card.get("aliases") or [])]:
        e = engine_index.get(cand)
        if e is None:
            continue
        if e.get("ambiguous"):
            return None, None          # 同名歧义：拒绝冒充
        if e.get("supertype") != card.get("supertype"):
            continue
        if card.get("supertype") == "Pokemon" and \
                e.get("signature") is not None and \
                _pokemon_signature(card) != e["signature"]:
            # 同名不同印刷：招式/特性名集合不一致 → 拒绝拿另一张卡的招式冒充
            if issues is not None:
                issues["engine_namekey_mismatch"] = \
                    issues.get("engine_namekey_mismatch", 0) + 1
            continue
        return e, how
    return None, None


def apply_engine_match(card: dict, engine_index: dict, issues: dict) -> dict:
    """回填引擎映射（中文名 / 可对战状态）；幂等，可脱离原始 CSV 重跑"""
    card["aliases"] = aliases_for(card)
    eng, matched_by = _match_engine(card, engine_index, issues)
    card["engine_ready"] = False
    card["engine_status"] = "unmatched"
    card["engine_note"] = "引擎卡池未收录"
    card.pop("engine_match", None)
    card.pop("engine_uid", None)
    if eng:
        st = eng.get("status", "mapped")
        card["engine_match"] = matched_by
        # 印刷身份键：卡组/引擎按 uid 精确选印刷（缺省按卡名取最新印刷）
        card["engine_uid"] = eng.get("uid")
        card["engine_status"] = st
        card["engine_note"] = eng.get("reason") or ""
        # 引擎池中文名只作**兜底**：已有 i18n 译文不被空值/旧名覆盖
        # （卡库中文语料是展示口径，引擎池名字是历史人工口径）。
        if eng.get("name_zh"):
            card["name_zh"] = eng["name_zh"]
        card.setdefault("name_zh", "")
        # 只有 mapped/approximated 才算「可进引擎对战」；unsupported 一律不可
        card["engine_ready"] = st in ("mapped", "approximated")
    issues["zh_backfill" if eng else "engine_unmatched"] = \
        issues.get("zh_backfill" if eng else "engine_unmatched", 0) + 1
    if card["engine_status"] == "unsupported":
        issues["engine_unsupported"] = issues.get("engine_unsupported", 0) + 1
    elif card["engine_status"] == "approximated":
        issues["engine_approximated"] = issues.get("engine_approximated", 0) + 1
    return card


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
    images = load_image_map()

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
                                engine_index, issues, images))

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
        "with_image": sum(1 for c in cards if c.get("image_url")),
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
         "- 合规 6.1 / C2（v1.1）：卡牌**名称 / 规则文本 / 图像**可在遵守合规声明的前提下"
         "使用，须**标注权利方**（Nintendo · Creatures Inc. · GAME FREAK inc. · "
         "The Pokémon Company），且不得声称关联或获其背书；本项目为非商业社区研究项目。",
         "- 合规 6.1：**机器/AI 生成的解析结果**（`parse` 字段）均带 `ai_generated` / "
         "`generated_by` / `ai_notice` 显著标识，仅供研究参考。",
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
         f"| 中文化效果字段 | {stats.get('with_zh_effects', 0)} |",
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
    qa = stats.get("qa")
    if qa:
        L += [
            f"- 宝可梦缺 HP：{len(qa['missing_hp'])}"
            + (f" —— {', '.join(qa['missing_hp'][:6])}…" if qa["missing_hp"] else ""),
            f"- 宝可梦缺属性：{len(qa['missing_type'])}"
            + (f" —— {', '.join(qa['missing_type'][:6])}…" if qa["missing_type"] else ""),
            f"- 缺日文名：{len(qa['missing_ja'])}"
            + (f" —— {', '.join(qa['missing_ja'][:6])}…" if qa["missing_ja"] else ""),
            f"- 无招式且无效果文本：{len(qa['no_effect_no_move'])}"
            + (f" —— {', '.join(qa['no_effect_no_move'][:6])}…"
               if qa["no_effect_no_move"] else ""),
            f"- 未知能量符号：{qa['unknown_energy'] or '无'}",
            f"- (卡名,系列,编号) 重复：{len(qa['dup_key'])}"
            + (f" —— {', '.join(qa['dup_key'][:6])}…" if qa["dup_key"] else ""),
            f"- 进化卡缺「进化前」：{stats['issues'].get('evolve_missing_from', 0)}",
            f"- 引擎自撰卡池中**不在本数据集**的卡："
            f"{len(qa['engine_pool_not_in_dataset'])}"
            + (f" —— {', '.join(qa['engine_pool_not_in_dataset'])}"
               if qa["engine_pool_not_in_dataset"] else ""),
            "",
        ]
    else:
        # --reparse-only 路径：输入 JSON 不含 qa（写盘时已剔除），跳过该节
        L += ["- （本次为 `--reparse-only` 增量重跑；质量问题清单见上一次全量清洗报告）",
              ""]
    L += [
        "## 4. K2 效果解析覆盖（机器生成 · 合规 6.1 已标识）",
        "",
        ]
    ps = stats.get("parse") or {}
    if ps:
        L += [
            "| 项 | 数值 |", "|---|---|",
            f"| 后端 | `{ps.get('backend')}` |",
            f"| 已解析卡数 | {ps.get('cards_parsed')} |",
            f"| 解析条目（招式/特性/整卡） | {ps.get('items')} |",
            f"| 产出 spec 总数 | {ps.get('specs')} |",
            f"| Schema 校验错误 | {ps.get('errors')} |",
            f"| 可映射引擎 tag | {ps.get('engine_tags')} |",
            f"| **未建模（待补）动作** | **{ps.get('unsupported')}** |",
            "",
            f"> AI 标识：本表全部数字来自 `{ps.get('backend')}` 后端自动解析，"
            "结果带 `ai_generated=true` 与免责提示，**仅供研究参考**。",
            "",
        ]
    L += [
        "## 5. 诚实标注",
        "",
        "- 本数据集提供**卡面机制文本**（官方英文原文），按合规 6.1 / C2(v1.1) "
        "**标注权利方后可用于研究演示**，项目不打包与本研究无关的官方美术素材；",
        "- 机器/AI 解析结果（`parse`）在库、API 与 UI 三处均带显著 AI 标识，"
        "不得作为官方规则呈现；",
        "- 引擎未建模的卡（"
        f"{stats['cards_out'] - stats['engine_ready']} 张）**可浏览/可组卡但不可对战**，"
        "由 `engine_ready` 标记，UI 与导入校验据此显示灰名单；",
        "- 中文名与效果文本来自 `data/i18n/zh_card_translations.json`（人工翻译语料，"
        f"卡名 {stats['with_zh']} 张）；缺译文时回退英文，不做机器硬译以免误导。",
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
        # 迁移：补 image_url 列（合规 6.1 卡图引用）
        cols = {r[1] for r in con.execute("PRAGMA table_info(cards)")}
        if "image_url" not in cols:
            con.execute("ALTER TABLE cards ADD COLUMN image_url TEXT")
            print("[migrate] cards 表新增列 image_url")
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
            # 中文对应文本（缺译文回退英文由前端负责；这里只拼已有中文）
            rules_zh_parts = [c["other_text_zh"]] if c.get("other_text_zh") else []
            for a in c["attacks"]:
                if a.get("effect_zh"):
                    rules_zh_parts.append(a["effect_zh"])
            for ab in c["abilities"]:
                if ab.get("effect_zh"):
                    rules_zh_parts.append(ab["effect_zh"])
            rules_text_zh = "\n\n".join(rules_zh_parts)
            attacks_json = json.dumps(
                {"attacks": c["attacks"], "abilities": c["abilities"]},
                ensure_ascii=False)
            effects_json = json.dumps({
                "library": {
                    "card_id": c["id"], "subtype": c["subtype"],
                    "stage": c["stage"], "rule": c["rule"],
                    "rule_zh": c.get("rule_zh") or "",
                    "category": c["category"], "evolves_from": c["evolves_from"],
                    "weakness": c["weakness"], "resistance": c["resistance"],
                    "retreat": c["retreat"], "name_ja": c["name_ja"],
                    "name_zh": c["name_zh"], "engine_ready": c["engine_ready"],
                    "other_text_zh": c.get("other_text_zh") or "",
                    "rules_text_zh": rules_text_zh,
                    # 诚实口径（C5）：mapped | approximated | unsupported | unmatched
                    "engine_status": c.get("engine_status", "unmatched"),
                    "engine_note": c.get("engine_note", ""),
                    # 匹配辅助（网站卡池/导入校验复用，避免撇号与能量命名差异导致漏匹配）
                    "name_key": c["name_key"],
                    "aliases": c.get("aliases") or [],
                    "kind": {"Pokemon": "pokemon", "Trainer": "trainer",
                             "Energy": "energy"}.get(c["supertype"], "unknown"),
                    "is_basic": bool(c["supertype"] == "Pokemon" and c["stage"] == 0),
                    "is_basic_energy": c["subtype"] == "Basic Energy",
                },
                # 合规 6.1：机器/AI 解析结果单独存放并带显著标识
                **({"parse": c["parse"]} if c.get("parse") else {}),
            }, ensure_ascii=False)
            status = "active"
            row = con.execute(
                "SELECT id FROM cards WHERE name=? AND series=? AND number=?",
                (c["name"], c["series"], c["number"])).fetchone()
            if row:
                con.execute(
                    "UPDATE cards SET supertype=?, types=?, hp=?, rules_text=?, "
                    "attacks_json=?, effects_json=?, image_url=?, status=?, "
                    "source=?, updated_at=datetime('now','localtime') WHERE id=?",
                    (c["supertype"], types, c["hp"], rules_text, attacks_json,
                     effects_json, c.get("image_url"), status, SOURCE_TAG,
                     row[0]))
                n_upd += 1
            else:
                con.execute(
                    "INSERT INTO cards(name,series,number,supertype,types,hp,"
                    "rules_text,attacks_json,effects_json,image_url,status,"
                    "source) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (c["name"], c["series"], c["number"], c["supertype"], types,
                     c["hp"], rules_text, attacks_json, effects_json,
                     c.get("image_url"), status, SOURCE_TAG))
                n_new += 1
        con.commit()
    finally:
        con.close()
    return {"new": n_new, "updated": n_upd, "removed_demo": n_demo}


def _reparse_only(args) -> int:
    """仅重跑 K2 效果解析并回写（幂等；无需原始 CSV）

    用途：解析链（规则基线 / tag 映射 / 引擎 tag 家族）升级后，刷新
    已入库卡表的 `parse` 产物与覆盖率统计，避免重新清洗数据集。
    """
    src = Path(args.in_json)
    if not src.exists():
        raise SystemExit(f"--in-json 不存在：{src}")
    payload = json.loads(src.read_text(encoding="utf-8"))
    cards = payload.get("cards") or []
    stats = dict(payload.get("stats") or {})
    # 1) 刷新引擎映射（可对战状态口径随卡池变化；无需原始 CSV）
    if "issues" not in stats or not isinstance(stats.get("issues"), dict):
        stats["issues"] = {}
    # 重跑映射时不要沿用上一次的累计计数（否则每跑一次数字翻倍）
    for _k in ("zh_backfill", "engine_unmatched", "engine_unsupported",
               "engine_approximated", "engine_setnum_name_mismatch",
               "engine_namekey_mismatch"):
        stats["issues"].pop(_k, None)
    eng_idx = load_engine_index()
    for c in cards:
        apply_engine_match(c, eng_idx, stats["issues"])
    stats["engine_ready"] = sum(1 for c in cards if c.get("engine_ready"))
    stats["engine_unsupported"] = sum(
        1 for c in cards if c.get("engine_status") == "unsupported")
    stats["engine_approximated"] = sum(
        1 for c in cards if c.get("engine_status") == "approximated")
    # 2) 清除旧解析产物（幂等）后重跑 K2 解析
    for c in cards:
        c.pop("parse", None)
    stats["parse"] = parse_effects_pass(cards, backend=args.parse_backend)
    payload["stats"] = stats
    payload.setdefault("meta", {})["parse_backend"] = \
        stats["parse"].get("backend")
    payload["meta"]["reparsed_at"] = _now()
    args.out_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    write_report(cards, stats, args.out_report)
    res = load_sqlite(cards, args.db)
    ps = stats["parse"]
    print(f"[reparse] 卡 {len(cards)} · 解析卡 {ps['cards_parsed']} · "
          f"条目 {ps['items']} · 引擎 tag {ps['engine_tags']} · "
          f"未建模 {ps['unsupported']}（AI 生成，已标识）")
    print(f"[db]      cards 表：新增 {res['new']} · 更新 {res['updated']}")
    print(f"[out]     {args.out_json}")
    print(f"[out]     {args.out_report}")
    return 0


def _now() -> str:
    import datetime as _dt
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _db_only(args) -> int:
    """**仅灌库**：从 --in-json 读已清洗卡表 → 写 SQLite，不重跑解析、不回写 JSON/报告

    为什么需要这个模式：`data/db/pokelab.db` 是 gitignored 的运行产物，
    而新机器 / CI 上并不存在 → 卡库类测试会因「卡库为空」(503) 失败。
    已清洗的 `data/cleaned/cards_challenge.json` **是入库的**，因此可以完全离线地
    重建同一份库；相比 `--reparse-only`，这里刻意**不碰解析链与产物**：
    解析链升级要走 `--reparse-only`（那才会刷新 JSON/报告并留痕）。
    """
    src = Path(args.in_json)
    if not src.exists():
        raise SystemExit(f"--in-json 不存在：{src}")
    payload = json.loads(src.read_text(encoding="utf-8"))
    cards = payload.get("cards") or []
    if not cards:
        raise SystemExit(f"--in-json 里没有 cards：{src}")
    res = load_sqlite(cards, args.db)
    print(f"[db-only] 卡 {len(cards)} → {args.db}"
          f"（新增 {res['new']} · 更新 {res['updated']} · 清占位 {res['removed_demo']}）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="清洗并导入卡牌数据集")
    ap.add_argument("--zip", type=Path, help="数据集 zip 路径")
    ap.add_argument("--csv-dir", type=Path, help="已解压的 CSV 目录")
    ap.add_argument("--db", type=Path, default=DB_PATH, help="目标 SQLite")
    ap.add_argument("--out-json", type=Path, default=OUT_JSON)
    ap.add_argument("--out-report", type=Path, default=OUT_REPORT)
    ap.add_argument("--dry-run", action="store_true", help="只清洗与报告，不写库")
    ap.add_argument("--parse-effects", action="store_true",
                    help="跑 K2 效果解析（规则基线），产物带 AI 标识（合规 6.1）")
    ap.add_argument("--parse-backend", default="rule",
                    choices=["rule", "llm", "auto"], help="解析后端（默认 rule）")
    ap.add_argument("--reparse-only", action="store_true",
                    help="**仅重跑解析**：从 --in-json 读取已清洗卡表，重跑 K2 解析后"
                         "回写 JSON/报告/DB（无需原始 CSV，供解析链升级后刷新产物）")
    ap.add_argument("--db-only", action="store_true",
                    help="**仅灌库**：从 --in-json 读取已清洗卡表，只写 SQLite"
                         "（不重跑解析、不回写 JSON/报告）—— 供 CI / 新机器离线重建卡库")
    ap.add_argument("--in-json", type=Path, default=OUT_JSON,
                    help="--reparse-only / --db-only 时的输入（已清洗 JSON）")
    args = ap.parse_args()

    if args.db_only:
        return _db_only(args)

    if args.reparse_only:
        return _reparse_only(args)

    if not args.zip and not args.csv_dir:
        raise SystemExit("需提供 --zip 或 --csv-dir")

    cards, stats = clean(args.zip, args.csv_dir)

    # 0) 可选：K2 效果解析（机器生成 → 必须显著标识）
    if args.parse_effects:
        stats["parse"] = parse_effects_pass(cards, backend=args.parse_backend)

    # 1) 规范化 JSON
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {
            "version": "1.1",
            "source": "pokemon-tcg-ai-battle-challenge-strategy.zip",
            "rows_in": stats["rows_in"],
            "cards": len(cards),
            "generated_by": "packages/card-db/scripts/import_challenge_dataset.py",
            "compliance": ("合规 6.1/C2(v1.1)：卡名/规则文本/图像可用但须标注权利方"
                           "（Nintendo · Creatures Inc. · GAME FREAK inc. · "
                           "The Pokémon Company），不得声称关联或获背书；"
                           "机器/AI 解析结果带 ai_generated/ai_notice 标识"),
            "ai_generated_fields": ["parse.ai_generated", "parse.generated_by",
                                    "parse.ai_notice"],
            "parse_backend": (stats.get("parse") or {}).get("backend"),
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
    ps = stats.get("parse")
    if ps:
        print(f"[k2]    {ps['backend']} 后端：解析卡 {ps['cards_parsed']} · "
              f"条目 {ps['items']} · spec {ps['specs']} · 引擎 tag {ps['engine_tags']} · "
              f"未建模 {ps['unsupported']}（AI 生成，已标识）")
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
