# -*- coding: utf-8 -*-
"""解析链 fx 族：**对手手牌禁打（常驻）**（2026-11-04 第五十七批）

只认领「As long as this Pokémon is in the Active Spot, your opponent can't play
any … from their hand.」这一句可完整表达的常驻禁令：

  · Tyranitar「Daunting Gaze」→ ``fx:play_block:item:active``
  · Jellicent ex「Oceanic Curse」→ ``fx:play_block:item+tool:active``
  · Team Rocket's Arbok「Potent Glare」
    → ``fx:play_block:ability_pokemon:active:except:teamrocket``

**故意拒绝**（返回 None）：
  · ``During your opponent's next turn, they can't play …``（Budew / Frillish）——
    限时形态已由 `play_lock:<kind>:turn` 承载，**不能**落成常驻（会永久封锁）；
  · ``{ACE SPEC}``（Genesect）—— 引擎没有 ACE SPEC 卡牌子类，判不出来就如实拒绝；
  · 带「if you discarded …」/ 抛币门等前置条件的写法。
"""
from __future__ import annotations

import re

PREFIXES = ("fx:play_block:",)

_POKE = r"pok[eé]mon"

_ITEM = re.compile(
    r"^as long as this " + _POKE + r" is in the active spot, your opponent "
    r"can'?t play any item cards from their hand[.]$")
_ITEM_TOOL = re.compile(
    r"^as long as this " + _POKE + r" is in the active spot, your opponent "
    r"can'?t play any item cards or " + _POKE + r" tool cards from their hand[.]$")
_ABILITY_POKE = re.compile(
    r"^as long as this " + _POKE + r" is in the active spot, your opponent "
    r"can'?t play any " + _POKE + r" that has an ability from their hand, "
    r"except for (?P<faction>[a-z][a-z .'-]{2,25}?)'?s? " + _POKE + r"[.]$")


def _name_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(name or "").lower())


def _spec(tag: str) -> dict:
    return {
        "trigger": "static",
        "actions": [{
            "type": "fx", "target": None, "amount": None,
            "extra": {"tag": tag, "family": "play_block"},
        }],
        "family": "play_block",
    }


# 「If this Pokémon has a Pokémon Tool attached, your opponent can't play any
#  {ACE SPEC} cards from their hand.」（第一百零七批，Genesect「ACE Nullifier」）
# ⚠ 与上面三条的生效时机不同：不是「在战斗位」，而是「**持有者贴着道具**」
#   → 引擎 `where` 段写 `tool`，由 `blocked_play` 判 `holder.tool`。
_ACE_TOOL = re.compile(
    r"^if this " + _POKE + r" has a " + _POKE + r" tool attached, your opponent "
    r"can'?t play any \{ace spec\} cards? from their hand[.]$", re.I)


def detect(text: str, kind: str = "attack", name: str = "") -> dict | None:
    if kind != "ability":
        return None
    t = str(text or "").strip()
    if _ITEM.match(t):
        return _spec("fx:play_block:item:active")
    if _ITEM_TOOL.match(t):
        return _spec("fx:play_block:item+tool:active")
    if _ACE_TOOL.match(t):
        return _spec("fx:play_block:acespec:tool")
    m = _ABILITY_POKE.match(t)
    if m:
        key = _name_key(m.group("faction") + "s")
        if not key:
            return None
        return _spec(f"fx:play_block:ability_pokemon:active:except:{key}")
    return None
