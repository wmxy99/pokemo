# -*- coding: utf-8 -*-
"""PokéLab Engine · 真实卡池（X 版）与预设对手池 A1–A4

M1「卡池补齐」的引擎侧落地：把 **真实卡名 + 引擎映射数值** 定义在
`engine/data/cards_real.json`，把 **60 卡真实卡表** 定义在
`engine/data/decklists.json`，本模块负责把两者组装为可直接进 Battle 的卡组。

设计要点
  - 数据与代码分离：卡池/卡表是 JSON（可被 Limitless 收录 loader 覆盖，
    替换为逐卡精确版本），本模块只做加载与映射；
  - 引擎能力之外的卡（tag=unsupported 或未收录）→ **以基础能量占位**，
    逐卡记录在 `coverage()` 报告中（诚实标注，不静默降级）；
  - 引用不可共享：每张牌构造独立 ref 实例（与 decklib._deck 同约定）。

对应文档：PRD §10 预设对手池 · P0 复核清单 §5.2「两阶段忠实度策略」X 版。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

from .cards import (AbilityDef, AttackDef, EnergyDef, EnergyRef, PokeRef,
                    PokemonDef, TrainerDef, TrainerRef)

_DATA_DIR = Path(__file__).resolve().parent / "data"
CARDS_FILE = _DATA_DIR / "cards_real.json"
# 卡池扩容（v1.4）：由清洗数据集 + K2 解析链生成的**扩展卡池**。
# 人工卡池（CARDS_FILE）**永远优先**，同名自动条目被忽略；扩展池条目在
# catalog() 中标记 engine_status="approximated"（保真度低于人工逐卡校对）。
POOL_EXT_FILE = _DATA_DIR / "cards_pool_ext.json"
DECKS_FILE = _DATA_DIR / "decklists.json"

# 引擎 v1 支持的训练家 tag 前缀（与 battle._trainer_legals/_do_trainer 对齐）
_SUPPORTED_TRAINER_TAGS = ("poffin", "ultra_ball", "hammer", "boss",
                           "research", "shuffle_draw",
                           # v1.3（B1 预设卡组关键卡批次）
                           "switch_self", "recover", "discard_search_energy",
                           "search_energy", "candy",
                           # v1.3（B2 通用检索批次）
                           "search_pokemon", "search_trainer", "search_any",
                           # 化石卡（2026-10-28）：当作 N-HP 基础宝可梦上场
                           "fossil")

# 基本能量中文名（占位替换时也用中文展示）
_ENERGY_ZH = {"psychic": "超", "fire": "火", "grass": "草", "darkness": "恶",
              "water": "水", "lightning": "雷", "fighting": "斗",
              "metal": "钢", "colorless": ""}


# ---------------- 数据加载 ----------------
def _read(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------- 印刷身份（2026-10-29 第二十一批） ----------------
# 卡名仍是**展示 / 规则**键（PokemonDef.name 不变，进化链与「同名」类规则不受影响）；
# `uid` 只用于**跨印刷寻址**：同名不同印刷（SIT 69 / SFA 19）可以共存放行，
# 卡组用 uid 精确选卡，未指定印刷时按卡名解析到「数据集最新印刷」。
def _uid_of(name: str, real: Optional[dict]) -> str:
    real = real or {}
    s = str(real.get("set") or "").strip().upper()
    n = str(real.get("number") or "").strip()
    return f"{name}@{s} {n}" if s and n else f"{name}@-"


# 系列发行顺序（**新的在前**）。未列出的系列按「最旧」处理，保证排序确定。
# ⚠ 这是一份人工维护的顺序表：只影响「同名多印刷时默认取哪张」，不影响卡面数值。
_SERIES_ORDER = ("POR", "ASC", "PFL", "MEG", "DRI", "WHT", "BLK", "JTG", "PRE",
                 "SSP", "SCR", "SFA", "TWM", "TEF", "PAR", "PAL", "SVI", "SVE",
                 "SVP", "SIT", "PROMO")
_SERIES_RANK = {s: i for i, s in enumerate(_SERIES_ORDER)}
_SERIES_RANK_OLDEST = len(_SERIES_ORDER) + 1


def _print_rank(item: dict) -> tuple:
    """同名条目「首选」排序键（越小越优先）：系列新 → 编号大 → 效果完整"""
    pr = item.get("printing") or {}
    rank = _SERIES_RANK.get(str(pr.get("set") or "").upper(),
                            _SERIES_RANK_OLDEST)
    try:
        num = -int(str(pr.get("number") or "0"))
    except ValueError:
        num = 0
    meta = item.get("meta") or {}
    effects = sum(len(a.get("effects") or [])
                  for a in meta.get("attacks") or [])
    effects += len(meta.get("abilities") or [])
    return (rank, num, -effects)


def _raw_uid(entry: dict) -> str:
    return entry.get("uid") or _uid_of(entry.get("name") or "",
                                       entry.get("real"))


@lru_cache(maxsize=1)
def _catalog_raw() -> dict:
    """人工卡池 + 扩展卡池合并（人工优先，**按印刷 uid** 判重）

    同名不同印刷的条目**全部保留**（2026-10-29 第二十一批）：
    旧实现按卡名过滤扩展条目，导致 SFA 19 Dusclops 这类同名新印刷永远进不了池。
    """
    base = _read(CARDS_FILE)
    if not POOL_EXT_FILE.exists():
        return base
    try:
        ext = _read(POOL_EXT_FILE)
    except Exception:                                  # noqa: BLE001
        return base
    have = {_raw_uid(c) for c in base.get("cards") or []}
    extra = [c for c in (ext.get("cards") or []) if _raw_uid(c) not in have]
    merged = dict(base)
    merged["cards"] = list(base.get("cards") or []) + extra
    meta = dict(base.get("meta") or {})
    meta["pool_ext"] = {
        "file": POOL_EXT_FILE.name,
        "count": len(extra),
        "provenance": (ext.get("meta") or {}).get("provenance"),
        "fidelity": (ext.get("meta") or {}).get("fidelity"),
        "admission": (ext.get("meta") or {}).get("admission"),
    }
    merged["meta"] = meta
    return merged


@lru_cache(maxsize=1)
def _decklists_raw() -> dict:
    return _read(DECKS_FILE)


def _cost(raw: Optional[dict]):
    """{"colorless":2,"fire":1} → (("colorless",2),("fire",1))"""
    if not raw:
        return 1
    return tuple((k, int(v)) for k, v in raw.items())


def _fossil_def(entry: dict) -> PokemonDef:
    """化石卡的「宝可梦形态」定义（卡池条目 `fossil: {hp, types, abilities}`）"""
    raw = entry.get("fossil") or {}
    hp = int(raw.get("hp") or 0)
    if hp <= 0:
        raise ValueError(f"化石卡缺少 HP：{entry.get('name')}")
    abilities = tuple(
        AbilityDef(ab["name"], tag=ab.get("tag", ""),
                   location=ab.get("location", "field"),
                   desc=ab.get("desc", ""),
                   name_zh=ab.get("name_zh", ""),
                   desc_zh=ab.get("desc_zh", ""))
        for ab in raw.get("abilities") or ())
    return PokemonDef(
        entry["name"], hp=hp,
        ptype=(raw.get("types") or ["colorless"])[0],
        retreat=0, stage=0, abilities=abilities,
        desc=entry.get("desc", ""), name_zh=entry.get("name_zh", ""))


def _build_card(entry: dict):
    """卡池条目 → 引擎卡定义对象（PokemonDef / TrainerDef / EnergyDef）"""
    st = entry.get("supertype")
    if st == "Pokemon":
        attacks = tuple(
            AttackDef(a["name"], cost=_cost(a.get("cost")),
                      damage=int(a.get("damage", 0)),
                      effects=tuple(a.get("effects", ())),
                      desc=a.get("desc", ""),
                      name_zh=a.get("name_zh", ""),
                      desc_zh=a.get("desc_zh", ""))
            for a in entry.get("attacks", []))
        abilities = tuple(
            AbilityDef(ab["name"], tag=ab.get("tag", ""),
                       location=ab.get("location", "field"),
                       desc=ab.get("desc", ""),
                       name_zh=ab.get("name_zh", ""),
                       desc_zh=ab.get("desc_zh", ""))
            for ab in entry.get("abilities", []))
        return PokemonDef(
            entry["name"], hp=int(entry["hp"]),
            ptype=(entry.get("types") or ["colorless"])[0],
            weakness=entry.get("weakness"), resistance=entry.get("resistance"),
            retreat=int(entry.get("retreat", 1)), attacks=attacks,
            is_ex=bool(entry.get("is_ex")), stage=int(entry.get("stage", 0)),
            is_mega=bool(entry.get("is_mega")),
            category=str(entry.get("category") or ""),
            evolves_from=entry.get("evolves_from"), abilities=abilities,
            desc=entry.get("note", ""), name_zh=entry.get("name_zh", ""))
    if st == "Trainer":
        if str(entry.get("tag") or "").startswith("fossil:"):
            # 化石卡：训练家卡 + 「宝可梦形态」定义（上场时实例化）
            return TrainerDef(entry["tag"], kind=entry.get("kind", "item"),
                              name=entry["name"],
                              name_zh=entry.get("name_zh", ""),
                              fossil_pdef=_fossil_def(entry))
        return TrainerDef(entry["tag"], kind=entry.get("kind", "item"),
                          name=entry["name"], name_zh=entry.get("name_zh", ""))
    if st == "Energy":
        return EnergyDef(entry.get("type", "colorless"), name=entry["name"],
                         name_zh=entry.get("name_zh", ""),
                         tag=entry.get("tag", ""))
    raise ValueError(f"未知 supertype: {st} ({entry.get('name')})")


def _ref(cdef):
    if isinstance(cdef, PokemonDef):
        return PokeRef(cdef)
    if isinstance(cdef, EnergyDef):
        return EnergyRef(cdef)
    if isinstance(cdef, TrainerRef) or isinstance(cdef, TrainerDef):
        return TrainerRef(cdef)
    raise TypeError(f"未知卡定义 {type(cdef)}")


@lru_cache(maxsize=1)
def _catalog_items() -> list:
    """**逐印刷**的卡池条目（人工 + 扩展），每项带 `uid` / `printing`"""
    out = []
    for entry in _catalog_raw()["cards"]:
        cdef = _build_card(entry)
        unsupported = False
        reason = ""
        derived = entry.get("provenance") == "dataset-derived"
        if isinstance(cdef, TrainerDef) and cdef.tag == "unsupported":
            unsupported, reason = True, entry.get("note", "引擎未实现")
        elif isinstance(cdef, PokemonDef):
            bad = [ab for ab in cdef.abilities if ab.tag == "unsupported"]
            if bad:
                # 特性未实现不算替换（招式仍可用），仅记为近似
                reason = entry.get("note", "特性未实现（保留招式）")
        if derived and not reason:
            reason = "由解析链自动生成（非人工逐卡校对，保真度近似）"
        real = entry.get("real") or {}
        pset = str(real.get("set") or "").strip().upper()
        pnum = str(real.get("number") or "").strip()
        out.append({
            "uid": _raw_uid(entry),
            "name": entry["name"],
            "printing": {"set": pset, "number": pnum},
            "def": cdef, "meta": entry,
            "unsupported": unsupported, "reason": reason,
            "derived": derived,
        })
    return out


@lru_cache(maxsize=1)
def catalog() -> dict:
    """卡名 → 首选印刷条目（同名多印刷时取**数据集最新印刷**，见 `_print_rank`）

    条目结构同旧版（`def` / `meta` / `unsupported` / `reason` / `derived`），
    另带 `uid` 与 `printing`；按印刷精确寻址请用 `printings()`。
    """
    best: dict = {}
    for item in _catalog_items():
        cur = best.get(item["name"])
        if cur is None or _print_rank(item) < _print_rank(cur):
            best[item["name"]] = item
    return best


@lru_cache(maxsize=1)
def printings() -> dict:
    """印刷 uid（``<卡名>@<SET> <number>``）→ 条目（含同名不同印刷全部）"""
    return {item["uid"]: item for item in _catalog_items()}


def item_for(key: str) -> Optional[dict]:
    """按 **uid** 或**卡名**取条目（uid 优先；未指定印刷 → 首选印刷）"""
    k = str(key or "")
    return printings().get(k) or catalog().get(k)


def display_name(name: str) -> str:
    """卡名中文展示（英文为数据键；未收录中文时回退英文）"""
    item = item_for(name)
    if item is None:
        return name
    return item["def"].display


@lru_cache(maxsize=1)
def catalog_meta() -> dict:
    return _catalog_raw()["meta"]


# ---------------- 预设卡组 ----------------
def _arch_raw(arch_id: str) -> dict:
    key = arch_id.upper()
    for a in _decklists_raw()["archetypes"]:
        if a["id"].upper() == key:
            return a
    raise KeyError(f"未知预设卡组 {arch_id}（可用：A1–A4）")


def archetype_ids() -> list:
    return [a["id"] for a in _decklists_raw()["archetypes"]]


def archetypes() -> list:
    """API/前端用的预设元信息（不含逐卡清单）"""
    out = []
    for a in _decklists_raw()["archetypes"]:
        cov = coverage(a["id"])
        out.append({
            "id": a["id"],
            "label": a.get("label_zh") or a["label"],       # 中文（前端直接展示）
            "label_en": a["label"],                          # 英文原型名（数据/出处追溯）
            "role": a.get("role", ""),
            "style": a.get("style", ""), "note": a.get("meta_note", ""),
            "cards_total": cov["total"],
            "unique_cards": cov["unique_cards"],
            "executable_rate": cov["executable_rate"],
            "replaced": cov["replaced"],
            "approximated": cov["approximated"],
            "battle_ready": cov["battle_ready"],
            "fidelity": _decklists_raw()["meta"]["fidelity_note"],
        })
    return out


def decklist(arch_id: str) -> list:
    """原始逐卡清单 [{name,count}]（校验 60 张）"""
    a = _arch_raw(arch_id)
    total = sum(int(c["count"]) for c in a["cards"])
    if total != 60:
        raise ValueError(f"预设 {a['id']} 卡表共 {total} 张（应为 60）")
    return a["cards"]


def build_deck(arch_id: str) -> list:
    """预设卡组 → 可直接进 Battle 的 60 张 ref 列表

    引擎不支持的卡以 `fallback_energy` 占位（逐卡记录于 coverage）。
    """
    a = _arch_raw(arch_id)
    fb_type = a.get("fallback_energy", "colorless")
    # 占位能量同样带中文展示名（否则前端/日志会回退显示英文名）
    fb = EnergyDef(fb_type, name=f"Basic {fb_type.title()} Energy",
                   name_zh=f"基本{_ENERGY_ZH.get(fb_type, '')}能量")
    out: list = []
    for entry in decklist(arch_id):
        # 卡表可写 `uid` 精确锁定印刷；只写卡名时取首选印刷（数据集最新）
        item = item_for(entry.get("uid") or entry["name"])
        n = int(entry["count"])
        if item is None or item["unsupported"]:
            # 复用同一个冻结的 EnergyDef（此前重建时漏掉 name_zh → 显示英文）
            out.extend(EnergyRef(fb) for _ in range(n))
            continue
        out.extend(_ref(item["def"]) for _ in range(n))
    return out


def coverage(arch_id: str) -> dict:
    """映射覆盖率报告：可执行 / 占位替换 / 近似（诚实标注，K2 与 M1 验收口径）"""
    a = _arch_raw(arch_id)
    replaced, approximated = [], []
    total = unique = 0
    for entry in decklist(arch_id):
        n = int(entry["count"])
        total += n
        unique += 1
        item = item_for(entry.get("uid") or entry["name"])
        if item is None:
            replaced.append({"name": entry["name"], "count": n,
                             "reason": "卡池未收录"})
            continue
        if item["unsupported"]:
            replaced.append({"name": entry["name"], "count": n,
                             "reason": item["reason"] or "引擎未实现"})
            continue
        if item["reason"]:
            approximated.append({"name": entry["name"], "count": n,
                                 "reason": item["reason"]})
    replaced_n = sum(r["count"] for r in replaced)
    playable = total - replaced_n
    return {
        "id": a["id"], "label": a.get("label_zh") or a["label"],
        "label_en": a["label"], "total": total,
        "unique_cards": unique, "playable": playable,
        "executable_rate": round(playable / total, 4) if total else 0.0,
        "replaced": replaced, "approximated": approximated,
        "battle_ready": bool(playable >= 54),      # 验收：可执行 ≥90%
        "fallback_energy": a.get("fallback_energy"),
    }


def describe(arch_id: str) -> dict:
    """API 用完整描述：元信息 + 逐卡清单（含引擎映射状态）"""
    a = _arch_raw(arch_id)
    cards = []
    for entry in decklist(arch_id):
        item = item_for(entry.get("uid") or entry["name"])
        status = "replaced" if (item is None or item["unsupported"]) else (
            "approximated" if item["reason"] else "mapped")
        cards.append({"name": entry["name"],
                      "uid": (item["uid"] if item else None),
                      "printing": (item["printing"] if item else None),
                      "name_zh": (item["meta"].get("name_zh") if item else ""),
                      "display": (item["def"].display if item else entry["name"]),
                      "count": int(entry["count"]),
                      "status": status,
                      "engine": (item["meta"].get("supertype")
                                 if item else None),
                      "note": (item["reason"] if item else "卡池未收录")})
    base = {k: v for k, v in a.items() if k != "cards"}
    base["cards"] = cards
    base["coverage"] = coverage(arch_id)
    return base

# ---------------- 中文卡名 → 英文键（别名解析） ----------------
@lru_cache(maxsize=1)
def zh_index() -> dict:
    """中文展示名 → 英文数据键（覆盖真实卡池 + Y 版演示卡）

    用途：让「粘贴中文卡表」也能被识别（F3 导入既接受英文规范名，
    也接受中文展示名；数据键仍统一为英文）。
    """
    out: dict = {}
    for name, item in catalog().items():
        zh = getattr(item["def"], "name_zh", "") or item["meta"].get("name_zh", "")
        if zh:
            out[zh] = name
    # Y 版演示卡（decklib 模块级定义对象；无 JSON 数据）
    try:
        from . import decklib as _dl
        for v in vars(_dl).values():
            nm = getattr(v, "name", None)
            zh = getattr(v, "name_zh", "")
            if nm and zh:
                out.setdefault(zh, nm)
            for atk in getattr(v, "attacks", ()) or ():
                if getattr(atk, "name", None) and getattr(atk, "name_zh", ""):
                    out.setdefault(atk.name_zh, atk.name)
            for ab in getattr(v, "abilities", ()) or ():
                if getattr(ab, "name", None) and getattr(ab, "name_zh", ""):
                    out.setdefault(ab.name_zh, ab.name)
    except Exception:                                # noqa: BLE001
        pass
    return out


def resolve_name(name: str) -> str:
    """把（可能是中文的）卡名解析为英文数据键；无法解析时原样返回

    已是**印刷 uid**（含 `@`）时原样返回 —— 调用方据此走 `printings()` 精确寻址。
    """
    if not name:
        return name
    key = str(name).strip()
    if "@" in key and key in printings():             # 已是印刷 uid
        return key
    if key in catalog():                             # 已是英文键
        return key
    hit = zh_index().get(key)
    if hit:
        return hit
    lowered = {k.lower(): k for k in catalog()}
    return lowered.get(key.lower(), key)


def is_zh_name(name: str) -> bool:
    """判断输入是否为中文卡名（用于提示/归一化）"""
    return bool(name) and any("\u4e00" <= ch <= "\u9fff" for ch in str(name))
