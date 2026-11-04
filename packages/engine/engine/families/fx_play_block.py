# -*- coding: utf-8 -*-
"""fx 族：**对手手牌禁打（常驻）**（引擎侧 · 2026-11-04 第五十七批）

与已有的「限时」形态（`play_lock:<kind>:turn`，Budew / Frillish）分工：
  · 限时形态写在**招式**上，只封「对手的下个回合」；
  · 本族是**常驻**形态：「As long as this Pokémon is in the Active Spot, your
    opponent can't play any Item cards from their hand.」——只要持有者在战斗位
   就一直生效，且**不写在本方卡上**（受害者是对手），因此必须由
    `families.blocked_play` 在「合法性检查」时按对手阵营扫描。

tag 形态（逐字解析，未知形态抛 ValueError —— 绝不静默忽略）：
    fx:play_block:item:active               对手不能从手牌打出物品卡
    fx:play_block:item+tool:active          物品卡 + 宝可梦道具
    fx:play_block:ability_pokemon:active[:except:<namekey>]
                                            对手不能打出**有特性**的宝可梦
                                            （`:except:` = 卡名以该键开头者除外）

⚠ 「有特性的宝可梦」按 `pdef.abilities` 非空判定（卡面「a Pokémon that has an
Ability」）；`except:teamrocket` 按**卡名归一化前缀**匹配，与引擎其它阵营判定
（`faction:` 系列）同一口径。
"""
from __future__ import annotations

import re

PREFIXES = ("fx:play_block:",)

_KINDS = {"item": ("item",), "item+tool": ("item", "tool")}


def _name_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(name or "").lower())


def _parse_tag(tag: str) -> dict:
    parts = str(tag).split(":")
    if len(parts) < 4 or parts[0] != "fx" or parts[1] != "play_block":
        raise ValueError(f"非法 play_block tag：{tag}")
    what, when = parts[2], parts[3]
    if when not in ("active", "always"):
        raise ValueError(f"play_block 生效时机非法：{tag}")
    info = {"what": what, "active_only": when == "active", "except": None}
    i = 4
    while i < len(parts):
        if parts[i] == "except":
            if i + 1 >= len(parts) or not parts[i + 1]:
                raise ValueError(f"play_block except 缺参数：{tag}")
            info["except"] = parts[i + 1]
            i += 2
        else:
            raise ValueError(f"未识别的 play_block 限定：{tag}")
    if what not in _KINDS and what != "ability_pokemon":
        raise ValueError(f"未识别的 play_block 对象：{tag}")
    return info


def _card_matches(info: dict, card) -> bool:
    what = info["what"]
    if what in _KINDS:
        kind = str(getattr(getattr(card, "tdef", None), "kind", "") or "")
        return kind in _KINDS[what]
    # 有特性的宝可梦
    pdef = getattr(card, "pdef", None)
    if pdef is None or not pdef.abilities:
        return False
    if info["except"] and _name_key(pdef.name).startswith(info["except"]):
        return False
    return True


def blocked_play(battle, p, card) -> bool:
    """受害方 ``p`` 想打 ``card``；封锁源在**对手**场上"""
    if battle is None or p is None or card is None:
        return False
    idx = None
    for i, pl in enumerate(battle.players):
        if pl is p:
            idx = i
            break
    if idx is None:
        return False
    o = battle.players[1 - idx]
    for holder in o.all_field():
        if holder is None:
            continue
        for ab in holder.pdef.abilities:
            tag = str(getattr(ab, "tag", "") or "")
            if not tag.startswith("fx:play_block:"):
                continue
            info = _parse_tag(tag)
            if info["active_only"] and holder is not o.active:
                continue
            if _card_matches(info, card):
                battle._log("blocked_play", {"player": idx,
                                             "by": holder.name, "tag": tag})
                return True
    return False
