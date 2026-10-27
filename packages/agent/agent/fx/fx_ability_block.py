# -*- coding: utf-8 -*-
"""解析链 fx 族：特性 / 场地「no Abilities」检测（与 engine/families/fx_ability_block.py 一一对应）

总原则：**整段认领，认不出返回 None**。只在完全理解整段卡面文本时才返回，
「只理解一半」必须返回 None（让整卡如实落未建模），绝不允许部分动作 + 静默丢弃。

匹配键为**归一化后的文本短语**（normalize 已做：弯引号→直引号、空白折叠、全小写）。
Watchtower 走 stadium 通道（引擎只认 ``stadium:`` 前缀才能把场地打出）；
Gastrodon / Flutter Mane 走 ``fx:`` 通道（特性侧静默封锁，由 engine 的
``ability_blocked`` 钩子具体判定范围/例外）。
"""
from __future__ import annotations

from . import normalize

PREFIXES = ("fx:ability_block:",)


def _fx(tag: str) -> dict:
    """特性侧 fx 动作（Gastrodon / Flutter Mane）"""
    return {
        "actions": [{
            "type": "fx", "target": None, "amount": None,
            "extra": {"tag": tag, "family": "ability_block"},
        }],
        "family": "ability_block",
    }


def _stadium(scope: str) -> dict:
    """场地侧动作（Watchtower）：tag 必须带 stadium: 前缀才能被引擎当场地打出"""
    return {
        "trigger": "on_use",
        "actions": [{
            "type": "stadium", "target": None, "amount": None,
            "extra": {"kind": "ability_block", "scope": scope},
        }],
        "family": "ability_block",
    }


def detect(text: str, kind: str = "attack", name: str = "") -> dict | None:
    """返回 None（不是本族句式）或完整 spec。

    仅认领三类已完整表达、无近邻歧义的句式；Iron Thorns ex（Rule Box + Future 例外）
    等数据模型无法精确表达的，一律返回 None，由整卡如实保持未建模。
    """
    t = normalize(text)
    if "no abilities" not in t:
        return None

    # Watchtower 场地：场上无色宝可梦无特性（{C}/colorless）
    if "{c} pokémon in play" in t or "colorless pokémon in play" in t:
        return _stadium("colorless")

    # Gastrodon 特性：自身在后备 → 双方后备 Stage 2 无特性
    if "benched stage 2 pokémon" in t:
        return _fx("fx:ability_block:gastrodon")

    # Flutter Mane 特性：自身在战斗位 → 对手战斗位无特性（except Midnight Fluttering）
    if "your opponent's active pokémon has no abilities" in t:
        return _fx("fx:ability_block:flutter_mane")

    return None
