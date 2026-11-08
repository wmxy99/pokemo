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


def apply_shield(defender: FieldPokemon, dmg: int,
                 block: bool | None = None) -> int:
    """护盾结算：全额挡下（``block``）；``shield_reduce`` 减伤（下限 0）

    规则要点：减伤发生在**弱点/抗性之后**（卡面「after applying Weakness and
    Resistance」）→ 必须在 ``compute_damage`` 之后调用。

    ``block`` 由调用方按卡面限定算好（`Battle._shield_blocks`）再传入：
    护盾可能只挡某类来源（「by attacks from Basic Pokémon」）或只挡
    伤害不超过 N 的攻击。``None`` = 按无条件的 ``shield_prevent_all`` 处理
    （兼容直接设置该标志的调用方 / 单元测试）。
    """
    if (defender.shield_prevent_all if block is None else block):
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


def status_checkup(field: FieldPokemon, rng, phase: str = "start", *,
                   bonus=None, override=None) -> list:
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

    ``bonus`` / ``override``（2026-10-28 第十三批 · Checkup 改写族）：
      · ``bonus = {"poison": +N, "burn": +N}``   追加伤害（「put N more damage
        counters … during Pokémon Checkup」，Pecharunt / Magmortar）
      · ``override = {"poison": N, "burn": N}``  取代默认值（Nidoking ex
        「put 8 damage counters … instead of 1」）
    两者都由 `battle._checkup_mods` 在调用前汇总，本函数只做算术。
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
        dmg = int((override or {}).get("poison") or 10) \
            + int((bonus or {}).get("poison") or 0)
        field.damage += dmg
        events.append({"type": "poison_damage", "target": field.name, "dmg": dmg})
    if "burn" in field.status:
        # S6c（官方，日月起）：**先放 2 个伤害指示物（20），再抛币；正面则治愈**。
        # ⚠ 与日月**之前**的规则方向相反（旧规则：抛币，**反面**才受伤）。
        #   按旧规则写会让灼伤变成「一半回合白挂」，而且卡面/日志/对拍全看不出来；
        #   旧实现更省事——恒定 -20 且永不治愈，等于把「灼伤」当成永久的毒。
        dmg = int((override or {}).get("burn") or 20) \
            + int((bonus or {}).get("burn") or 0)
        field.damage += dmg
        events.append({"type": "burn_damage", "target": field.name, "dmg": dmg})
        # 治愈判定（同一检查期内、伤害之后）：正面 → 移除灼伤。
        # 场地可禁止这次治愈（Wela Volcano Park「Burned Pokémon can't be cured
        # in this way」），由 `override["burn_no_cure"]` 表达。
        if not (override or {}).get("burn_no_cure"):
            if rng.random() < 0.5:
                field.status.discard("burn")
                events.append({"type": "burn_cure", "target": field.name})
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

# 特殊状态的**构成规则**（S6c；官方《玩法指引》+ SM7 规则书「Removing Special
# Conditions」一节）：
#   · 中毒 / 灼伤用**标记**记录 → 可以和任何状态共存，彼此也能共存
#     （「An unfortunate Pokémon could be Burned, Paralyzed, and Poisoned」）；
#   · 睡眠 / 混乱 / 麻痹靠**转动卡牌**表示 → 三者互斥，**后施加的取代先前的**。
# 因此同时最多是「中毒 + 灼伤 + 三者之一」。
STATUS_MARKER = ("poison", "burn")
STATUS_ORIENT = ("sleep", "confuse", "paralyze")

# 卡面/旧数据里的形容词拼写 → 引擎规范名
STATUS_ALIASES = {"poisoned": "poison", "burned": "burn", "asleep": "sleep",
                  "confused": "confuse", "paralyzed": "paralyze"}


def apply_status_set(field: FieldPokemon, statuses) -> tuple:
    """施加一组特殊状态（S6c）：按**构成规则**替换，不是无脑叠加

    返回 ``(applied, removed)``，两者都已排序（便于日志与断言）。

    ⚠ 三个容易写错的点，每一个都有对应测试：
      1. **不是「只能有一个」**：中毒/灼伤是标记，能与其它状态共存。
         写成「新状态清空全部」会顺带清掉**合法组合**（烧伤+中毒+麻痹）。
      2. **不是「全部叠加」**：睡眠/混乱/麻痹三选一，后施加的取代先前的。
         旧实现用 `status.add()`，于是「睡眠 + 混乱」能同时成立 —— 官方不允许
         （两者都要转动卡牌，物理上只能有一种朝向）。
      3. **同种状态重复施加既不叠加也不重置**：它本来就在，没有可重置的东西。
      4. **一次效果里写两个转动卡牌类状态**（假设卡面「now Asleep and
         Confused」）：按卡面**书写顺序**施加，所以只留**最后一个**。这也是本函数
         收「整个集合」而不是单个状态的原因 —— 传进来就知道「后面还有没有」，
         不必依赖调用方循环到最后一个才正确。
    """
    new: list = []
    for s in statuses or ():
        k = STATUS_ALIASES.get(str(s), str(s))
        if k not in STATUSES:
            # 静默丢弃 = 「施加了但没生效」永远查不出来（调用点已校验过状态词，
            # 走到这里说明**新增的施加路径**漏了校验）→ 直接抛错。
            raise ValueError(f"未知特殊状态：{s}")
        if k not in new:
            new.append(k)
    # 一次效果里的多个转动卡牌类只留**最后一个**（卡面书写顺序即施加顺序）。
    # 滤掉的是「被同组后来的顶掉」的那些 —— 与「移除旧状态」无关，别混在一起。
    _last_orient = [k for k in new if k in STATUS_ORIENT][-1:]
    new = [k for k in new if k not in STATUS_ORIENT] + _last_orient
    if not new:
        return [], []
    removed: list = []
    result: set = set()
    for old in field.status:
        k = STATUS_ALIASES.get(str(old), str(old))
        if k in new:
            continue                      # 已在结果里（新状态直接并进去）
        if k in STATUS_ORIENT and any(n in STATUS_ORIENT for n in new):
            removed.append(k)             # 转动卡牌类互斥：被新状态顶掉
            continue
        result.add(k)
    field.status = result | set(new)
    return sorted(new), sorted(removed)
