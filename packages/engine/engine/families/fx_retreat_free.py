# -*- coding: utf-8 -*-
"""fx 族：**撤退费用归零**（引擎侧 · 2026-11-04 第五十六批）

与 `passive:no_retreat` 的分工：
  · ``passive:no_retreat`` 只作用于**特性持有者自己**（写在持有者的特性 tag 上）；
  · 本族覆盖**全队**（「All of your Pokémon …」/「Your Basic Pokémon in play …」），
    效果源是场上任意一只的特性，**受益者不是持有者**——所以不能挂在 `passive:` 上，
    必须由 `battle._retreat_cost` 扫己方阵营。

tag 形态（逐字解析，未知形态抛 ValueError —— 绝不静默忽略）：
    fx:retreat_free:energy:<color>     己方**带该属性能量**的宝可梦撤退费为 0
    fx:retreat_free:basic              己方全部**基础**宝可梦撤退费为 0
    fx:retreat_free:self:if:no_energy  持有者自身「身上没有能量时」撤退费为 0

⚠ 「带 {M} Energy attached」用 `energy_of_type` 判定（含提供该属性的特殊能量），
与卡面「attached」口径一致；用 `energy_of`（只数基本能量）会漏掉特殊能量。
"""
from __future__ import annotations

PREFIXES = ("fx:retreat_free:",)

_TYPES = {"grass", "fire", "water", "lightning", "psychic", "fighting",
          "darkness", "metal", "colorless", "dragon"}


def _parse_tag(tag: str) -> dict:
    parts = str(tag).split(":")
    if len(parts) < 3 or parts[0] != "fx" or parts[1] != "retreat_free":
        raise ValueError(f"非法 retreat_free tag：{tag}")
    if parts[2] == "energy":
        if len(parts) != 4 or parts[3] not in _TYPES:
            raise ValueError(f"retreat_free 属性非法：{tag}")
        return {"kind": "energy", "color": parts[3]}
    if parts[2] == "basic":
        if len(parts) != 3:
            raise ValueError(f"retreat_free basic 形态非法：{tag}")
        return {"kind": "basic"}
    if parts[2] == "self":
        if len(parts) != 5 or parts[3] != "if" or parts[4] != "no_energy":
            raise ValueError(f"retreat_free self 形态非法：{tag}")
        return {"kind": "self_no_energy"}
    raise ValueError(f"未识别的 retreat_free 范围：{tag}")


def _side_of(battle, fp) -> int | None:
    for i, p in enumerate(battle.players):
        if fp is p.active or any(fp is b for b in p.bench):
            return i
    return None


def retreat_cost(battle, fp):
    """返回 ``0``（本族判定免费）或 ``None``（不干预）"""
    if battle is None or fp is None:
        return None
    side = _side_of(battle, fp)
    if side is None:
        return None
    # 自身形态只看**自己**的特性；全队形态看己方场上任意一只的特性。
    for holder in battle.players[side].all_field():
        if holder is None:
            continue
        for ab in holder.pdef.abilities:
            tag = str(getattr(ab, "tag", "") or "")
            if not tag.startswith("fx:retreat_free:"):
                continue
            info = _parse_tag(tag)
            if info["kind"] == "self_no_energy":
                if holder is fp and fp.total_energy() == 0:
                    return 0
            elif info["kind"] == "basic":
                if fp.stage == 0:
                    return 0
            elif info["kind"] == "energy":
                if fp.energy_of_type(info["color"]) > 0:
                    return 0
    return None
