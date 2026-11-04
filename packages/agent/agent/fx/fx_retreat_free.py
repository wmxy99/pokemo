# -*- coding: utf-8 -*-
"""解析链 fx 族：**撤退费用归零**（2026-11-04 第五十六批）

只认领三种「整段可完整表达」的常驻句式；其余一律返回 None（整卡如实未建模）：

  · Archaludon「Metal Bridge」
    ``All of your Pokémon that have {M} Energy attached have no Retreat Cost.``
    → ``fx:retreat_free:energy:metal``
  · Latias ex「Skyliner」
    ``Your Basic Pokémon in play have no Retreat Cost.``
    → ``fx:retreat_free:basic``
  · Ethan's Magcargo / Charmander「Agile」
    ``If this Pokémon has no Energy attached, it has no Retreat Cost.``
    → ``fx:retreat_free:self:if:no_energy``

**故意拒绝**（返回 None）：
  · 带时限制的写法（``During your opponent's next turn, … no Retreat Cost``）——
    那是 `shield:*` 的语义，不是常驻；
  · 只对**特定阵营/卡名**生效的写法（``your Team Rocket's Pokémon …``）——
    本族没有阵营参数，硬套会作用到不该受益的宝可梦上；
  · 带费用门槛/条件（``if this Pokémon has 3 or more Energy``）的写法。
"""
from __future__ import annotations

import re

PREFIXES = ("fx:retreat_free:",)

_POKE = r"pok[eé]mon"
_TYPE = {"G": "grass", "R": "fire", "W": "water", "L": "lightning",
         "P": "psychic", "F": "fighting", "D": "darkness", "M": "metal",
         "C": "colorless", "N": "dragon", "Y": "psychic"}

# 「All of your Pokémon that have {M} Energy attached have no Retreat Cost.」
# ⚠ `any` 是**可选**的：Archaludon 写「have {M} Energy attached」，而
# `fx:team_reduce` 的同源句式写「have any {M} Energy attached」——两者都存在，
# 写死 `any` 会让前者静默落回未建模。
_ENERGY = re.compile(
    r"^all of your " + _POKE + r" that have (?:any )?\{(?P<t>[a-z])\} energy "
    r"attached have no retreat cost[.]$")
# 「Your Basic Pokémon in play have no Retreat Cost.」
_BASIC = re.compile(
    r"^your basic " + _POKE + r" in play have no retreat cost[.]$")
# 「If this Pokémon has no Energy attached, it has no Retreat Cost.」
_SELF = re.compile(
    r"^if this " + _POKE + r" has no energy attached, it has no retreat cost[.]$")


def _spec(tag: str) -> dict:
    return {
        "trigger": "static",
        "actions": [{
            "type": "fx", "target": None, "amount": None,
            "extra": {"tag": tag, "family": "retreat_free"},
        }],
        "family": "retreat_free",
    }


def detect(text: str, kind: str = "attack", name: str = "") -> dict | None:
    if kind != "ability":
        return None
    t = str(text or "").strip()
    m = _ENERGY.match(t)
    if m:
        color = _TYPE.get(m.group("t").upper())
        return _spec(f"fx:retreat_free:energy:{color}") if color else None
    if _BASIC.match(t):
        return _spec("fx:retreat_free:basic")
    if _SELF.match(t):
        return _spec("fx:retreat_free:self:if:no_energy")
    return None
