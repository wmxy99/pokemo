# -*- coding: utf-8 -*-
"""fx 族：**效果免疫**（引擎侧 · 2026-10-27）

解析侧只产出两种可完整表达的自身形态：

    fx:effect_immune:attack:self    对手宝可梦的**招式效果**不作用于自身
    fx:effect_immune:ability:self   对手宝可梦的**特性效果**不作用于自身

语义边界（与卡面括号一致）：
  · **伤害不是效果** —— 普通招式伤害照常结算；被免疫的只有状态、弃能、
    换位、指示物等「效果」；
  · 只免疫**做给这只宝可梦的**效果，攻击方自身的效果（抽牌/自伤/护盾等）
    不受影响；
  · 训练家卡的效果免疫、全队范围、Tera 等来源子集**不在此族**，解析侧不产 tag。

未知 tag 形态必须抛 ValueError —— 静默忽略会让「免疫」变成无效果。
"""
from __future__ import annotations

PREFIXES = ("fx:effect_immune:",)

_SOURCES = {"attack", "ability"}


def _parse_tag(tag: str) -> str:
    """校验并返回来源类型（``attack`` / ``ability``）"""
    parts = str(tag).split(":")
    if len(parts) != 4 or parts[0] != "fx" or parts[1] != "effect_immune":
        raise ValueError(f"非法 effect_immune tag：{tag}")
    source, scope = parts[2], parts[3]
    if source not in _SOURCES:
        raise ValueError(f"未识别的 effect_immune 来源：{tag}")
    if scope != "self":
        raise ValueError(f"未识别的 effect_immune 范围：{tag}")
    return source


def effect_immune(battle, fp, source: str) -> bool:
    """``fp`` 是否免疫来自 ``source``（attack / ability）的效果

    只看特性上的静态 tag；不在场上/无特性返回 False。
    """
    if fp is None:
        return False
    want = str(source or "")
    if want not in _SOURCES:
        raise ValueError(f"未识别的效果来源：{source}")
    for ab in getattr(fp.pdef, "abilities", ()) or ():
        tag = str(getattr(ab, "tag", "") or "")
        if not tag.startswith("fx:effect_immune:"):
            continue
        if _parse_tag(tag) == want:
            return True
    return False
