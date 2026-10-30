# -*- coding: utf-8 -*-
"""fx 族：**招式费用抬高**（引擎侧 · 2026-10-28）

解析侧 `agent/fx/fx_attack_cost.py` 产出 tag，本模块通过 ``attack_cost`` 钩子
（`battle._can_pay_attack` 每次校验费用时调用）抬高**对手**的招式费用。

tag 形态（逐字解析，未知形态抛 ValueError —— 绝不静默忽略）：
    fx:attack_cost:opp_basic:<n>:colorless            对手基础宝可梦 +n
    fx:attack_cost:self:less:per:<counter>[:name:<k>]  自身 -n×计数源（可限定招式名）

语义边界：
  · 前一种只在**持有者位于战斗位**时生效（卡面「is in the Active Spot」）；
  · 只作用于**基础宝可梦**（stage == 0）的招式；
  · 只加/减**无色**门槛（引擎费用模型里无色可由任意能量支付）。
"""
from __future__ import annotations

import re

PREFIXES = ("fx:attack_cost:",)


def _name_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(name or "").lower())


def _parse_tag(tag: str) -> tuple:
    """校验 tag → (scope, n, counter, attack_key)"""
    parts = str(tag).split(":")
    if len(parts) < 3 or parts[0] != "fx" or parts[1] != "attack_cost":
        raise ValueError(f"非法 attack_cost tag：{tag}")
    scope = parts[2]
    if scope == "opp_basic":
        if len(parts) != 5 or not parts[3].isdigit():
            raise ValueError(f"非法 attack_cost tag：{tag}")
        n, color = int(parts[3]), parts[4]
        if color != "colorless":
            raise ValueError(f"attack_cost 只支持无色加价：{tag}")
        if n <= 0:
            raise ValueError(f"attack_cost 费用增量非法：{tag}")
        return scope, n, None, None
    if scope == "self":
        # fx:attack_cost:self:less:per:<counter>[:name:<k>]
        if len(parts) < 6 or parts[3] != "less" or parts[4] != "per":
            raise ValueError(f"非法 attack_cost tag：{tag}")
        counter = parts[5]
        if not counter:
            raise ValueError(f"attack_cost 缺少计数源：{tag}")
        key = None
        if len(parts) > 6:
            if len(parts) != 8 or parts[6] != "name" or not parts[7]:
                raise ValueError(f"非法 attack_cost tag：{tag}")
            key = parts[7]
        return scope, 1, counter, key
    raise ValueError(f"未识别的 attack_cost 范围：{tag}")


def attack_cost(battle, attacker, atk) -> dict:
    """攻击方是否要为**对手战斗位**上的常驻效果多付 / 自行为常驻效果少付无色"""
    if attacker is None:
        return {}
    owner = None
    for i, p in enumerate(battle.players):
        if p.active is attacker:
            owner = i
            break
    if owner is None:
        return {}
    delta = 0
    # ① 对手战斗位的「基础宝可梦 +n 无色」
    holder = battle.players[1 - owner].active
    if holder is not None:
        for ab in holder.pdef.abilities:
            tag = str(getattr(ab, "tag", "") or "")
            if not tag.startswith("fx:attack_cost:"):
                continue
            scope, n, _c, _k = _parse_tag(tag)
            if scope == "opp_basic" and attacker.stage == 0:
                delta += n
    # ② 攻击方自身的「按计数源减费」
    _atk_name = _name_key(getattr(atk, "name", ""))
    for ab in attacker.pdef.abilities:
        tag = str(getattr(ab, "tag", "") or "")
        if not tag.startswith("fx:attack_cost:self:"):
            continue
        scope, _n, counter, key = _parse_tag(tag)
        if key is not None and key != _atk_name:
            continue                     # 卡面限定了招式名（如「Blood Moon」）
        from ..battle import _eval_counter           # 惰性导入，避免循环依赖
        delta -= int(_eval_counter(battle, counter))
    return {"colorless": delta} if delta else {}
