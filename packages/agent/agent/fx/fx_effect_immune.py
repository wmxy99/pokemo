# -*- coding: utf-8 -*-
"""解析链 fx 族：**效果免疫**（2026-10-27）

只认领两种「自身范围、来源明确、伤害与效果边界写清」的常驻特性：

  · ``Prevent all effects of attacks used by your opponent's Pokémon done to
    this Pokémon. (Damage is not an effect.)``
      → ``fx:effect_immune:attack:self``
  · ``Prevent all effects of your opponent's Pokémon's Abilities done to this
    Pokémon.``
      → ``fx:effect_immune:ability:self``

**故意拒绝**（返回 None，整卡如实落未建模）：
  · ``Prevent all damage from and effects of attacks …`` —— 连伤害一起免疫，
    需要伤害免疫层，不能只挂效果免疫（会把伤害静默放过）；
  · ``… done to your Basic Team Rocket's Pokémon`` —— 全队/子集范围，
    引擎尚无「卡名阵营 + 阶段」的受保护范围；
  · ``… from your opponent's Tera Pokémon`` —— 来源子集，池内无 Tera 元数据；
  · ``Whenever your opponent plays an Item or Supporter card …`` —— 训练家卡
    的一次性效果免疫，派发目标语义尚未逐类接通；
  · 其它含 ``including damage`` / 条件 / 额外动作的写法。

tag 由 `engine/families/fx_effect_immune.py` 逐字解析；未知形态会抛错。
"""
from __future__ import annotations

import re

from . import normalize

PREFIXES = ("fx:effect_immune:",)

_ATTACK_RE = re.compile(
    r"^prevent all effects of attacks used by your opponent's pok[eé]mon "
    r"done to this pok[eé]mon\. \(damage is not an effect\.\)$")
_ABILITY_RE = re.compile(
    r"^prevent all effects of your opponent's pok[eé]mon's abilities done to "
    r"this pok[eé]mon\.$")


def _spec(kind: str) -> dict:
    tag = f"fx:effect_immune:{kind}:self"
    return {
        "trigger": "static",
        "actions": [{
            "type": "fx", "target": None, "amount": None,
            "extra": {"tag": tag, "family": "effect_immune"},
        }],
        "family": "effect_immune",
    }


def detect(text: str, kind: str = "attack", name: str = "") -> dict | None:
    """返回 None（不是本族句式）或完整 spec。仅特性侧；只认整段。"""
    if kind != "ability":
        return None
    t = normalize(text).replace("é", "e")
    if _ATTACK_RE.match(t):
        return _spec("attack")
    if _ABILITY_RE.match(t):
        return _spec("ability")
    return None
