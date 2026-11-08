# -*- coding: utf-8 -*-
"""解析链 fx 族：**效果免疫**（2026-10-27）

只认领两种「自身范围、来源明确、伤害与效果边界写清」的常驻特性：

  · ``Prevent all effects of attacks used by your opponent's Pokémon done to
    this Pokémon. (Damage is not an effect.)``
      → ``fx:effect_immune:attack:self``
  · ``Prevent all effects of your opponent's Pokémon's Abilities done to this
    Pokémon.``
      → ``fx:effect_immune:ability:self``

S4（2026-11-08）又补了两种此前「故意拒绝」的形态：

  · ``… done to your Basic Team Rocket's Pokémon.``
      → ``fx:effect_immune:attack:team:basic_teamrocket``（团队范围免疫）
  · ``Whenever your opponent plays an Item or Supporter card from their hand,
    prevent all effects of that card done to this Pokémon.``
      → ``fx:effect_immune:trainer:item+supporter:self``（训练家卡效果免疫；
        只写 Supporter 的印刷 → ``trainer:supporter:self``）

**仍然故意拒绝**（返回 None，整卡如实落未建模）：
  · ``Prevent all damage from and effects of attacks …`` —— 连伤害一起免疫，
    需要伤害免疫层，不能只挂效果免疫（会把伤害静默放过）；
  · ``… from your opponent's Tera Pokémon`` —— 来源子集，池内无 Tera 元数据；
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
# S4：全队范围（Team Rocket's Articuno「Repelling Veil」）
_TEAM_TR_RE = re.compile(
    r"^prevent all effects of attacks used by your opponent's pok[eé]mon done "
    r"to your basic team rocket's pok[eé]mon\. \(existing effects are not "
    r"removed\. damage is not an effect\.\)$")
# S4：训练家卡效果免疫（Cetitan ex「Snow Camouflage」/ Fraxure「Unnerve」/
# Antique Sail Fossil「Protective Sail」）
_TRAINER_RE = re.compile(
    # ⚠ 冠词要写 `an?`：两种印刷分别是 "**an** Item or Supporter card" 与
    #   "**a** Supporter card"，只写 `an ` 会把后者静默漏掉（实测：整句落到
    #   「静态/未建模」兜底，卡被拒收而看不出原因）。
    r"^whenever your opponent plays an? (item or supporter|supporter) card from "
    r"their hand,? prevent all effects of that card done to this pok[eé]mon\.$")


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


def _raw_spec(tag: str) -> dict:
    """任意 tag 形态的 spec（S4 的两种新形态参数不在固定枚举里）"""
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
    if _TEAM_TR_RE.match(t):
        # 「你的**基础**火箭队宝可梦」——团队范围，引擎侧按
        # 「基础阶段 + 卡名 Team Rocket's」判定（`_is_basic_teamrocket`）
        return _raw_spec("fx:effect_immune:attack:team:basic_teamrocket")
    m = _TRAINER_RE.match(t)
    if m:
        kinds = "item+supporter" if m.group(1).startswith("item") else "supporter"
        return _raw_spec(f"fx:effect_immune:trainer:{kinds}:self")
    return None
