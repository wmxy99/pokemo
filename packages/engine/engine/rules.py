# -*- coding: utf-8 -*-
"""PokéLab Engine · 结算规则（纯函数：伤害/状态检查/击倒）

伤害公式（v1 简化子集）：
  dmg = base × 2（防守方 weakness == 攻击方 primary_type）
       − 30（防守方 resistance == 攻击方 primary_type，最低 0）
  spread/状态自伤不做弱抗修正。
"""
from __future__ import annotations
from .cards import FieldPokemon


def compute_damage(attacker: FieldPokemon, defender: FieldPokemon, base: int,
                   weakness_override: str | None = None) -> int:
    """结算一次基础伤害（含弱点 ×2 / 抗性 −30）

    ``weakness_override``：场上静态效果改写守方弱点时的生效弱点
    （由 ``weak_change:<type>[:<filter>]`` 特性提供，见 battle._weakness_override）。
    传 ``""``（空串）表示**弱点被移除**（无弱点）。
    """
    dmg = base
    at = attacker.pdef.ptype
    weak = defender.pdef.weakness if weakness_override is None else weakness_override
    if weak and weak == at:
        dmg *= 2
    if defender.pdef.resistance == at:
        dmg = max(0, dmg - 30)
    return dmg


def confusion_self_damage(attacker: FieldPokemon) -> int:
    """混乱状态自伤：固定 30（无弱抗修正）"""
    return 30


def distribute_damage(bench_count: int, total: int, step: int = 10) -> list:
    """E5：把 total 点"任意分配"伤害确定性拆到后备（每份 step 的整数倍）。

    默认轮询分配（按后备索引序）；供引擎兜底与 Agent 启发式共用。
    返回与后备一一对应的伤害列表（长度 = bench_count）。
    """
    dmg = [0] * max(0, bench_count)
    if bench_count <= 0 or total <= 0:
        return dmg
    counters = total // step          # 每 counter = step 点
    i = 0
    for _ in range(counters):
        dmg[i % bench_count] += step
        i += 1
    return dmg


def status_checkup(field: FieldPokemon, rng) -> dict:
    """宝可梦检查阶段：结算中毒/烧伤伤害并处理睡眠/麻痹苏醒。

    返回事件描述 dict 列表（空表=无事件）。规则要点：
      - poison:   -10 HP（恒定，v1 简化）
      - burn:     -20 HP（恒定不移除，v1 简化）
      - sleep:    抛币，正面醒
      - paralyzed: 自动恢复
    """
    events = []
    if not field or field.is_ko:
        return events
    if "poison" in field.status:
        field.damage += 10
        events.append({"type": "poison_damage", "target": field.name, "dmg": 10})
    if "burn" in field.status:
        field.damage += 20
        events.append({"type": "burn_damage", "target": field.name, "dmg": 20})
    if "sleep" in field.status:
        if rng.random() < 0.5:
            field.status.discard("sleep")
            events.append({"type": "sleep_wake", "target": field.name})
    if "paralyzed" in field.status:
        field.status.discard("paralyzed")
        events.append({"type": "paralyze_recover", "target": field.name})
    return events


def check_ko_then_prize() -> None:  # pragma: no cover
    """击倒-奖赏结算在 battle.resolve_knockout 实现（需访问场地上下文）"""
    raise NotImplementedError


# 便捷常量
STATUSES = ("poison", "burn", "sleep", "paralyze", "confuse")
