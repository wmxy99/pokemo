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
                      desc=a.get("desc", ""))
            for a in entry.get("attacks", []))
        abilities = tuple(
            AbilityDef(ab["name"], tag=ab.get("tag", ""),
                       location=ab.get("location", "field"),
                       desc=ab.get("desc", ""))
            for ab in entry.get("abilities", []))
        return PokemonDef(
            entry["name"], hp=int(entry["hp"]),
            ptype=(entry.get("types") or ["colorless"])[0],
            weakness=entry.get("weakness"), resistance=entry.get("resistance"),
            retreat=int(entry.get("retreat", 1)), attacks=attacks,
            is_ex=bool(entry.get("is_ex")), stage=int(entry.get("stage", 0)),
            evolves_from=entry.get("evolves_from"), abilities=abilities,
            desc=entry.get("note", ""))
    if st == "Trainer":
        return TrainerDef(entry["tag"], kind=entry.get("kind", "item"),
                          name=entry["name"])
    if st == "Energy":
        return EnergyDef(entry.get("type", "colorless"), name=entry["name"])
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
            "id": a["id"], "label": a["label"], "role": a.get("role", ""),
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
    fb = EnergyDef(a.get("fallback_energy", "colorless"),
                   name=f"Basic {a.get('fallback_energy', 'colorless').title()} Energy")
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
        "id": a["id"], "label": a["label"], "total": total,
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
        cards.append({"name": entry["name"], "count": int(entry["count"]),
                      "status": status,
                      "engine": (item["meta"].get("supertype")
                                 if item else None),
                      "note": (item["reason"] if item else "卡池未收录")})
    base = {k: v for k, v in a.items() if k != "cards"}
    base["cards"] = cards
    base["coverage"] = coverage(arch_id)
    return base
