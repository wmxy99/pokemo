# -*- coding: utf-8 -*-
"""fx 族：特性 / 场地「no Abilities」静态封锁（2026-10-27）

数据模型可**完整表达**、且**无近邻歧义**的子集：
  · Watchtower 场地：无色（``ptype == colorless``）宝可梦无特性。
  · Gastrodon 特性 Sticky Bind：自身在**任意**后备时，双方后备 Stage 2 无特性。
  · Flutter Mane 特性 Midnight Fluttering：自身在**任意**战斗位时，对手战斗位无特性；
    但名为 ``"Midnight Fluttering"`` 的特性除外。

故意拒绝（数据模型缺 ``rule_box`` / ``Future`` 子类型，宁可不建模不静默近似）：
  · Iron Thorns ex「Rule Box 宝可梦无特性 except Future」→ 整卡留未建模。

纪律（与项目 K2 一致）：
  · 认不出的局面必须返回 ``False``，绝不兜底成「封锁」——错误方向会让该放的特性被禁。
  · 所有判定只看卡面明示的范围/例外，不臆测。
"""
from __future__ import annotations

from ..cards import COLORLESS

PREFIXES = ("fx:ability_block:",)

# 引擎侧识别的「场地封锁范围」词表（解析侧只能产出这里列出的 scope）
_STADIUM_ABILITY_BLOCK_SCOPES = ("colorless",)

_SOURCE_GASTRODON = "Gastrodon"
_SOURCE_FLUTTER = "Flutter Mane"
_EXCEPT_FLUTTER = "Midnight Fluttering"
_STADIUM_WATCHTOWER = "Team Rocket's Watchtower"


def _is_benched(battle, fp) -> bool:
    """fp 当前是否落在某方后备（按对象同一性判定）"""
    return any(fp is b for p in battle.players for b in p.bench)


def _has_benched(battle, name: str) -> bool:
    """任一玩家后备是否有名为 name 的宝可梦"""
    for p in battle.players:
        for b in p.bench:
            if b.pdef.name == name:
                return True
    return False


def _active_named(battle, name: str) -> bool:
    """任一玩家战斗位是否为名为 name 的宝可梦"""
    return any(p.active is not None and p.active.pdef.name == name
               for p in battle.players)


def ability_blocked(battle, fp, ab) -> bool:
    """返回 True 表示 fp 的特性 ab 当前被封锁（不进合法动作表）。

    fp：场上宝可梦实例（被检查者）；ab：其 AbilityDef（``ab.name`` 用于例外匹配）。
    """
    # 规则 1：Watchtower 场地 —— 无色宝可梦无特性
    stad = getattr(battle, "stadium", None) or {}
    if stad.get("name") == _STADIUM_WATCHTOWER \
            and fp.pdef.ptype == COLORLESS:
        return True

    # 规则 2：Gastrodon 在任意后备 —— 双方后备 Stage 2 无特性
    if _has_benched(battle, _SOURCE_GASTRODON) \
            and _is_benched(battle, fp) and fp.pdef.stage == 2:
        return True

    # 规则 3：Flutter Mane 在任意战斗位 —— 对手战斗位无特性（except Midnight Fluttering）
    if _active_named(battle, _SOURCE_FLUTTER):
        for i, p in enumerate(battle.players):
            if p.active is not None and p.active.pdef.name == _SOURCE_FLUTTER:
                opp = battle.players[1 - i]
                if fp is opp.active and ab.name != _EXCEPT_FLUTTER:
                    return True

    return False
