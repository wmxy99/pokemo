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
                   weakness_override: str | None = None,
                   ignore_weakness: bool = False,
                   ignore_resistance: bool = False) -> int:
    """结算一次基础伤害（含弱点 ×2 / 抗性 −30）

    ``weakness_override``：场上静态效果改写守方弱点时的生效弱点
    （由 ``weak_change:<type>[:<filter>]`` 特性提供，见 battle._weakness_override）。
    传 ``""``（空串）表示**弱点被移除**（无弱点）。
    ``ignore_weakness`` / ``ignore_resistance``：卡面「this attack's damage isn't
    affected by Weakness/Resistance」的显式忽略（v1.4）。
    """
    dmg = base
    at = attacker.pdef.ptype
    weak = defender.pdef.weakness if weakness_override is None else weakness_override
    if not ignore_weakness and weak and weak == at:
        dmg *= 2
    if not ignore_resistance and defender.pdef.resistance == at:
        dmg = max(0, dmg - 30)
    return dmg


def apply_shield(defender: FieldPokemon, dmg: int) -> int:
    """护盾结算：``shield_prevent_all`` 全额挡下；``shield_reduce`` 减伤（下限 0）

    规则要点：减伤发生在**弱点/抗性之后**（卡面「after applying Weakness and
    Resistance」）→ 必须在 ``compute_damage`` 之后调用。
    """
    if defender.shield_prevent_all:
        return 0
    if defender.shield_reduce:
        return max(0, dmg - defender.shield_reduce)
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


def status_checkup(field: FieldPokemon, rng, phase: str = "start") -> list:
    """宝可梦检查阶段：结算中毒/烧伤伤害并处理睡眠苏醒 / 麻痹恢复。

    返回事件描述 dict 列表（空表=无事件）。规则要点：
      - poison:   -10 HP（恒定，v1 简化）
      - burn:     -20 HP（恒定不移除，v1 简化）
      - sleep:    抛币，正面醒
      - paralyze: **回合结束**才恢复（`phase="end"`）

    ⚠ 为什么麻痹要按 phase 分开（2026-10-26 修）：
      此前的实现把「麻痹恢复」放在**回合开始**的检查阶段，而麻痹是在**对手回合**
      被打上的 → 轮到被麻痹方时，回合开始的检查阶段立刻把它清掉，于是被麻痹的
      宝可梦**从未真正被阻止过**（状态存在时间恒为 0 个行动窗口）。
      正确时序：麻痹持续整个「被麻痹方的下一回合」，在**该回合结束时**恢复。
      因此 `phase="start"` 只做毒/烧/睡眠，`phase="end"` 只做麻痹恢复。

    兼容性：`"paralyzed"` 是历史拼写（旧回放/外部构造可能写入），一并清除。
    """
    events = []
    if not field or field.is_ko:
        return events
    if phase == "end":
        if field.status & {"paralyze", "paralyzed"}:
            field.status -= {"paralyze", "paralyzed"}
            events.append({"type": "paralyze_recover", "target": field.name})
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
    return events


def check_ko_then_prize() -> None:  # pragma: no cover
    """击倒-奖赏结算在 battle.resolve_knockout 实现（需访问场地上下文）"""
    raise NotImplementedError


# 便捷常量
STATUSES = ("poison", "burn", "sleep", "paralyze", "confuse")
