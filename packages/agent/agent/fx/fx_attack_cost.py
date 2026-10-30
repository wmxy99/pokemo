# -*- coding: utf-8 -*-
"""解析链 fx 族：**招式费用抬高**（2026-10-28）

只认领一种卡面：持有者位于**战斗位**时，对手**基础宝可梦**使用招式要多付 {C}：

  · ``As long as this Pokémon is in the Active Spot, attacks used by your
    opponent's Basic Pokémon cost {C} more.``
      → ``fx:attack_cost:opp_basic:1:colorless``

**故意拒绝**（返回 None，整卡如实落未建模）：
  · 多付的不是无色（``cost {R} more``）—— 引擎费用模型只能加无色门槛，
    换成彩色会改变「哪些能量付得起」的语义；
  · 只抬某一个招式 / 某一类宝可梦（``attacks that have …``）—— 引擎无该筛选层。
"""
from __future__ import annotations

import re

from . import normalize

PREFIXES = ("fx:attack_cost:",)

_BASIC_COST_RE = re.compile(
    r"^as long as this pok[eé]mon is in the active spot, attacks used by your "
    r"opponent's basic pok[eé]mon cost \{([a-z])\} more\.$")

# 自身减费（2026-10-28）：
#   「Blood Moon used by this Pokémon costs {C} less for each Prize card your
#    opponent has taken.」→ 限定招式名
#   「Attacks used by this Pokémon cost {C} less for each of your opponent's
#    Benched Pokémon.」→ 全招式
_SELF_LESS_NAMED_RE = re.compile(
    r"^(.+?) used by this pok[eé]mon costs \{([a-z])\} less for each (.+)\.$")
_SELF_LESS_ALL_RE = re.compile(
    r"^attacks used by this pok[eé]mon cost \{([a-z])\} less for each (.+)\.$")

# 卡面计数短语 → 引擎计数源（**只收语义唯一者**；未列出的一律不认）
_COUNTERS = {
    "prize card your opponent has taken": "opp_prizes_taken",
    "prizes card your opponent has taken": "opp_prizes_taken",
    "of your opponent's benched pokemon": "opp_bench",
    "of your opponent's benched pokémon": "opp_bench",
}


def _name_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(name or "").lower())


def _self_less_spec(t: str):
    """自身减费的两种句式 → spec（认不出返回 None）"""
    m = _SELF_LESS_NAMED_RE.match(t)
    named = None
    if m:
        named, color, phrase = m.group(1), m.group(2), m.group(3)
    else:
        m = _SELF_LESS_ALL_RE.match(t)
        if not m:
            return None
        color, phrase = m.group(1), m.group(2)
    if color.upper() != "C":
        return None                      # 只支持无色减费（与引擎费用模型一致）
    counter = _COUNTERS.get(phrase.strip())
    if counter is None:
        return None
    tag = f"fx:attack_cost:self:less:per:{counter}"
    if named:
        tag += f":name:{_name_key(named)}"
    return {
        "trigger": "static",
        "actions": [{
            "type": "fx", "target": None, "amount": None,
            "extra": {"tag": tag, "family": "attack_cost"},
        }],
        "family": "attack_cost",
    }


def detect(text: str, kind: str = "attack", name: str = "") -> dict | None:
    """返回 None（不是本族句式）或完整 spec。仅特性侧；只认整段。"""
    if kind != "ability":
        return None
    t = normalize(text).replace("é", "e")
    hit = _self_less_spec(t)
    if hit is not None:
        return hit
    m = _BASIC_COST_RE.match(t)
    if not m:
        return None
    if m.group(1).upper() != "C":
        return None                      # 非无色加价：引擎无法精确表达
    tag = "fx:attack_cost:opp_basic:1:colorless"
    return {
        "trigger": "static",
        "actions": [{
            "type": "fx", "target": None, "amount": None,
            "extra": {"tag": tag, "family": "attack_cost"},
        }],
        "family": "attack_cost",
    }
