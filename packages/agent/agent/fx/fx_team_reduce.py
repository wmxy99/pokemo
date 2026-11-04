# -*- coding: utf-8 -*-
"""解析链 fx 族：**全队静态减伤**（2026-10-29 第十九批）

只认领「整段卡面可完整表达」的常驻减伤句式（`take N less damage from attacks
from your opponent's Pokémon (after applying Weakness and Resistance)`）：

  · Bouffalant「Curly Wall」：
    ``As long as you have at least 1 other Bouffalant in play, all of your
    Basic {C} Pokémon take 60 less damage … The effect of Curly Wall doesn't stack.``
  · Klinklang「Gear Coating」：
    ``All of your Pokémon that have any {M} Energy attached take 20 less damage …``
  · Steven's Carbink「Stone Palace」：
    ``As long as this Pokémon is on your Bench, all of your Steven's Pokémon
    take 30 less damage … The effect of Stone Palace doesn't stack.``

**故意拒绝**（返回 None）：
  · ``During your opponent's next turn, all of your {M} Pokémon take 30 less …``
    （Iron Defender）—— 时限制，不是常驻；
  · 攻击方来源限定（``from {R} Pokémon``）/ 带伤害门槛 / 全队「+N 伤害」等
    本族范围外的写法。

tag 形态（引擎 `engine/families/fx_team_reduce.py` 逐字解析）：
    fx:team_reduce:<n>:basic:<color>[:other:<namekey>][:nonstack]
    fx:team_reduce:<n>:energy:<color>[:nonstack]
    fx:team_reduce:<n>:faction:<namekey>[:source_bench][:nonstack]
"""
from __future__ import annotations

import re

from . import normalize

PREFIXES = ("fx:team_reduce:",)

_TYPE = {"G": "grass", "R": "fire", "W": "water", "L": "lightning",
         "P": "psychic", "F": "fighting", "D": "darkness", "M": "metal",
         "C": "colorless", "N": "dragon", "Y": "psychic"}

_POKE = r"pok[eé]mon"
_TAIL = (r" take (?P<n>\d+) less damage from attacks from your opponent's "
         + _POKE + r" \(after applying weakness and resistance\)\.")
# 卡面末尾可带「The effect of <特性名> doesn't stack.」（Aurorus「Tundra Wall」）
# → `:nonstack`（同名效果不叠加）。Klinklang「Gear Coating」无此句。
_NONSTACK_TAIL = r"(?: the effect of [^.]+ doesn't stack\.)?"


def _type_of(token: str) -> str | None:
    if not token or len(token) != 3 or token[0] != "{" or token[2] != "}":
        return None
    return _TYPE.get(token[1].upper())


def _name_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(name or "").lower())


_CURLY = re.compile(
    r"^as long as you have at least 1 other (?P<other>[a-z][a-z .'-]{2,25}?) "
    r"in play, all of your basic \{(?P<type>[a-z])\} " + _POKE + _TAIL
    + r" the effect of [^.]+ doesn't stack\.$")
_GEAR = re.compile(
    r"^all of your " + _POKE + r" that have any \{(?P<type>[a-z])\} energy "
    r"attached" + _TAIL + _NONSTACK_TAIL + r"$")
_STONE = re.compile(
    r"^as long as this " + _POKE + r" is on your bench, all of your "
    r"(?P<faction>[a-z][a-z .'-]{2,25}?)'s " + _POKE + _TAIL
    + r" the effect of [^.]+ doesn't stack\.$")


def _spec(tag: str) -> dict:
    return {
        "trigger": "static",
        "actions": [{
            "type": "fx", "target": None, "amount": None,
            "extra": {"tag": tag, "family": "team_reduce"},
        }],
        "family": "team_reduce",
    }


def detect(text: str, kind: str = "attack", name: str = "") -> dict | None:
    if kind != "ability":
        return None
    t = normalize(text)

    m = _CURLY.match(t)
    if m:
        color = _type_of("{" + m.group("type") + "}")
        other = _name_key(m.group("other"))
        if color is None or not other:
            return None
        return _spec(f"fx:team_reduce:{int(m.group('n'))}:basic:{color}"
                     f":other:{other}:nonstack")

    m = _GEAR.match(t)
    if m:
        color = _type_of("{" + m.group("type") + "}")
        if color is None:
            return None
        tail = ":nonstack" if "doesn't stack" in t else ""
        return _spec(f"fx:team_reduce:{int(m.group('n'))}:energy:{color}{tail}")

    m = _STONE.match(t)
    if m:
        key = _name_key(m.group("faction") + "s")
        if not key:
            return None
        return _spec(f"fx:team_reduce:{int(m.group('n'))}:faction:{key}"
                     f":source_bench:nonstack")

    return None
