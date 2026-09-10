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
DECKS_FILE = _DATA_DIR / "decklists.json"

# 引擎 v1 支持的训练家 tag 前缀（与 battle._trainer_legals/_do_trainer 对齐）
_SUPPORTED_TRAINER_TAGS = ("poffin", "ultra_ball", "hammer", "boss",
                           "research", "shuffle_draw")

# 基本能量中文名（占位替换时也用中文展示）
_ENERGY_ZH = {"psychic": "超", "fire": "火", "grass": "草", "darkness": "恶",
              "water": "水", "lightning": "雷", "fighting": "斗",
              "metal": "钢", "colorless": ""}

# 基本能量中文名（占位替换时也用中文展示）
_ENERGY_ZH = {"psychic": "超", "fire": "火", "grass": "草", "darkness": "恶",
              "water": "水", "lightning": "雷", "fighting": "斗",
              "metal": "钢", "colorless": ""}


# ---------------- 数据加载 ----------------
def _read(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def _catalog_raw() -> dict:
    return _read(CARDS_FILE)


@lru_cache(maxsize=1)
def _decklists_raw() -> dict:
    return _read(DECKS_FILE)


def _cost(raw: Optional[dict]):
    """{"colorless":2,"fire":1} → (("colorless",2),("fire",1))"""
    if not raw:
        return 1
    return tuple((k, int(v)) for k, v in raw.items())


def _build_card(entry: dict):
    """卡池条目 → 引擎卡定义对象（PokemonDef / TrainerDef / EnergyDef）"""
    st = entry.get("supertype")
    if st == "Pokemon":
        attacks = tuple(
            AttackDef(a["name"], cost=_cost(a.get("cost")),
                      damage=int(a.get("damage", 0)),
                      effects=tuple(a.get("effects", ())),
                      desc=a.get("desc", ""),
                      name_zh=a.get("name_zh", ""))
            for a in entry.get("attacks", []))
        abilities = tuple(
            AbilityDef(ab["name"], tag=ab.get("tag", ""),
                       location=ab.get("location", "field"),
                       desc=ab.get("desc", ""),
                       name_zh=ab.get("name_zh", ""))
            for ab in entry.get("abilities", []))
        return PokemonDef(
            entry["name"], hp=int(entry["hp"]),
            ptype=(entry.get("types") or ["colorless"])[0],
            weakness=entry.get("weakness"), resistance=entry.get("resistance"),
            retreat=int(entry.get("retreat", 1)), attacks=attacks,
            is_ex=bool(entry.get("is_ex")), stage=int(entry.get("stage", 0)),
            evolves_from=entry.get("evolves_from"), abilities=abilities,
            desc=entry.get("note", ""), name_zh=entry.get("name_zh", ""))
    if st == "Trainer":
        return TrainerDef(entry["tag"], kind=entry.get("kind", "item"),
                          name=entry["name"], name_zh=entry.get("name_zh", ""))
    if st == "Energy":
        return EnergyDef(entry.get("type", "colorless"), name=entry["name"],
                         name_zh=entry.get("name_zh", ""))
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
def catalog() -> dict:
    """卡名 → {"def": 卡定义, "meta": 原始条目}（含 unsupported 标记）"""
    out = {}
    for entry in _catalog_raw()["cards"]:
        cdef = _build_card(entry)
        unsupported = False
        reason = ""
        if isinstance(cdef, TrainerDef) and cdef.tag == "unsupported":
            unsupported, reason = True, entry.get("note", "引擎未实现")
        elif isinstance(cdef, PokemonDef):
            bad = [ab for ab in cdef.abilities if ab.tag == "unsupported"]
            if bad:
                # 特性未实现不算替换（招式仍可用），仅记为近似
                reason = entry.get("note", "特性未实现（保留招式）")
        out[entry["name"]] = {"def": cdef, "meta": entry,
                              "unsupported": unsupported, "reason": reason}
    return out


def display_name(name: str) -> str:
    """卡名中文展示（英文为数据键；未收录中文时回退英文）"""
    item = catalog().get(name)
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
    cat = catalog()
    fb_type = a.get("fallback_energy", "colorless")
    fb = EnergyDef(fb_type, name=f"Basic {fb_type.title()} Energy",
                   name_zh=f"基本{_ENERGY_ZH[fb_type]}能量")
    out: list = []
    for entry in decklist(arch_id):
        item = cat.get(entry["name"])
        n = int(entry["count"])
        if item is None or item["unsupported"]:
            out.extend(EnergyRef(EnergyDef(fb.type, name=fb.name)) for _ in range(n))
            continue
        out.extend(_ref(item["def"]) for _ in range(n))
    return out


def coverage(arch_id: str) -> dict:
    """映射覆盖率报告：可执行 / 占位替换 / 近似（诚实标注，K2 与 M1 验收口径）"""
    a = _arch_raw(arch_id)
    cat = catalog()
    replaced, approximated = [], []
    total = unique = 0
    for entry in decklist(arch_id):
        n = int(entry["count"])
        total += n
        unique += 1
        item = cat.get(entry["name"])
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
    cat = catalog()
    cards = []
    for entry in decklist(arch_id):
        item = cat.get(entry["name"])
        status = "replaced" if (item is None or item["unsupported"]) else (
            "approximated" if item["reason"] else "mapped")
        cards.append({"name": entry["name"],
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
    """把（可能是中文的）卡名解析为英文数据键；无法解析时原样返回"""
    if not name:
        return name
    key = str(name).strip()
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
