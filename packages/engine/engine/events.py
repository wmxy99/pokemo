# -*- coding: utf-8 -*-
"""事件总线 + 延迟队列（S2）

为什么需要它
------------
引擎原来的效果只有两种时间语义：**立刻**结算，或者「到第 N 回合失效」的粗粒度
到期字段。于是卡面上这两类句子**无处挂载**：

  · 「Whenever your opponent's Active Pokémon moves to the Bench **during their
     turn**, place 2 damage counters on that Pokémon.」（Team Rocket's Dugtrio
    「Holes」）—— 这是「**别人的动作**触发的效果」，不是自己的特性；
  · 「Draw 4 cards. **At the end of this turn**, if you have 5 or more cards in
     your hand, discard your hand.」（Amarys）—— 这是「**未来某个时点**执行的
     动作」，需要一个容器先存着。

本模块给这两件事补上一等公民身份：

  1. **延迟队列** `EffectQueue`：`QueuedEffect{owner, due_count, when, tag, ctx}`，
     到「该玩家第 N 个回合结束」时结算。**过期未结算直接抛错** —— 绝不允许
     「卡在队列里永不执行」（那是最隐蔽的静默丢效果）。
  2. **事件订阅**：卡片（特性/道具）用 `on_event:<kind>:<body>` 声明订阅，
     `emit()` 在唯一结算点扫描并执行，每张卡每个事件**每回合最多 1 次**
     （防「A 触发 B、B 再触发 A」的无限链，也对应卡面「The effect … doesn't
     stack」）。

⚠ 三条不变量（`tests/test_events_s2.py` 守护）
  1. **队列不许泄漏**：回合结束时，到期项必须全部结算，未到期项显式结转并写日志；
     过期项（due_count 已过）→ 抛错。
  2. **一处结算**：延迟效果只在 `Battle._end_turn` 的固定点结算（不散落在各 helper 里）。
  3. **同一 seed 同序列**：订阅顺序按（玩家序 → 场上位置 → 插入序）固定，可复现。

事件点（`EMIT_POINTS`）目前只接线了两类：`switch_out` / `switch_in`（换位）。
其余（进化/附能/伤害/击倒）留 S2b —— **没接线的点不会静默生效**，因为订阅只能
通过 `emit()` 触发，未接线的 kind 永远不会被 emit（`test_emit_points_wired`）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

# 计划中的全部事件点（S2b 逐个接线；未接线的 kind 不会有 emit 调用）
EMIT_POINTS = ("turn_begin", "turn_end", "attack_done", "checkup",
               "switch_out", "switch_in", "evolve", "attach_energy",
               "play_trainer", "damage", "ko")

# 订阅前缀：卡片用它声明「我关心某个事件」
WATCH_PREFIX = "on_event:"

# 每张卡每个事件**每回合**的触发上限（防无限链 / 对应「doesn't stack」）
WATCH_LIMIT = 1


@dataclass
class QueuedEffect:
    """一条延迟效果

    owner      : 在**谁的**回合结束时结算
    due_count  : 该玩家第几个回合（`turn_counts[owner]` 的值）结束时到期
    when       : "turn_end"（当前唯一接线的时点）
    tag        : 到期后要执行的 tag
    ctx        : 执行上下文（如「当时的目标」）
    source     : 来源卡名（日志 / 回溯）
    """
    owner: int
    due_count: int
    tag: str
    when: str = "turn_end"
    ctx: dict = field(default_factory=dict)
    source: str = ""


class EffectQueue:
    """FIFO 延迟队列（同一时点多条按插入序结算）"""

    def __init__(self) -> None:
        self.items: list = []

    def push(self, eff: QueuedEffect) -> None:
        self.items.append(eff)

    def due(self, owner: int, count: int, when: str = "turn_end") -> list:
        """取出到期项（**按插入序**），并从队列移除"""
        out, rest = [], []
        for it in self.items:
            if it.owner == owner and it.due_count <= count and it.when == when:
                out.append(it)
            else:
                rest.append(it)
        self.items = rest
        return out

    def stale(self, owner: int, count: int) -> list:
        """已过期项（due_count 早于当前回合序 —— 说明该结算却从没结算）

        ⚠ `due()` 用 `<=` 取件是**为了把它们捞出来报错**：真正的到期判定是
          `due_count == count`，`< count` 一律视为泄漏。
        """
        return [it for it in self.items
                if it.owner == owner and it.due_count < count
                and it.when == "turn_end"]

    def pending(self, owner: int) -> list:
        return [it for it in self.items if it.owner == owner]

    def __len__(self) -> int:
        return len(self.items)


def queue_of(battle) -> EffectQueue:
    """惰性取队列（`object.__new__(Battle)` 手工装配的夹具也能用）"""
    q = getattr(battle, "_queue", None)
    if q is None:
        q = EffectQueue()
        battle._queue = q
    return q


# ---------------- 入队 ----------------

def push(battle, owner: int, delay: int, tag: str, *,
         when: str = "turn_end", source: str = "", ctx=None) -> None:
    """入队；`delay=0` 表示**本回合**末，`1` 表示该玩家的下个回合末"""
    q = queue_of(battle)
    due = int(battle.turn_counts[owner]) + int(delay)
    q.push(QueuedEffect(owner=int(owner), due_count=due, tag=str(tag),
                        when=str(when), ctx=dict(ctx or {}),
                        source=str(source)))
    battle._log("queue_push", {"owner": int(owner), "due": due,
                               "when": str(when), "tag": str(tag),
                               "source": str(source),
                               "pending": len(q)})


def resolve_turn_end(battle, owner: int) -> list:
    """结算该玩家**本回合**到期的延迟效果（唯一结算点由 `_end_turn` 调用）

    返回执行记录（供测试与日志核对）。执行体本身由
    `Battle._run_queued_tag` 负责 —— 未知形态**抛错**，绝不静默跳过。
    """
    q = queue_of(battle)
    count = int(battle.turn_counts[owner])
    # 先查过期：`due_count < count` 意味着「早该在上个回合结算却还躺在队列里」
    stale = q.stale(owner, count)
    if stale:
        raise ValueError(
            "延迟队列泄漏：以下效果已过期未结算 "
            + "、".join(f"{it.tag}@{it.due_count}" for it in stale))
    due = [it for it in q.due(owner, count) if it.due_count == count]
    done = []
    for it in due:
        battle._run_queued_tag(it)
        done.append(it)
    if done:
        battle._log("queue_resolve", {
            "owner": owner, "n": len(done),
            "tags": [it.tag for it in done], "pending": len(q)})
    return done


# ---------------- 订阅 ----------------

def _watchers(battle, kind: str) -> list:
    """扫描**双方**场上（特性 + 道具）的 `on_event:<kind>:<body>` 订阅

    顺序固定：玩家 0 → 玩家 1，各自「战斗位 → 后备」。同一张卡多条订阅按
    卡面上的顺序（`pdef.abilities` 的顺序）—— 可复现是硬要求。
    """
    from .battle import _field_iter     # 延迟 import：battle 在加载期就 import 了本模块
    out = []
    want = f"{WATCH_PREFIX}{kind}:"
    for pi in (0, 1):
        for tpos, fp in _field_iter(battle.players[pi]):
            if fp is None:
                continue
            for ab in fp.pdef.abilities:
                t = str(getattr(ab, "tag", ""))
                if not t.startswith(want):
                    continue
                body = t[len(want):]
                # `while:active` —— 卡面「As long as this Pokémon is in the
                # Active Spot」（Mismagius ex「Swirling Prose」）：持有者在后备
                # 时**不订阅**。不在扫描处判断就变成「放后备也生效」= 静默加强。
                if ":while:active" in body and tpos != 0:
                    continue
                out.append((pi, tpos, fp, ab.name, body))
            tool = str(getattr(fp, "tool", "") or "")
            if tool.startswith(want):
                body = tool[len(want):]
                # ⚠ 道具载体也**必须**过 `while:active` 这一关 —— 只在特性分支里
                # 判断会让「挂着道具、站后备」也触发（实测：Powerglass 放后备
                # 仍会在回合末挂起附着选择）。这条测试抓到的就是它。
                if ":while:active" in body and tpos != 0:
                    continue
                out.append((pi, tpos, fp,
                            getattr(fp, "tool_name", "") or "tool", body))
    return out


def emit(battle, kind: str, ctx=None) -> list:
    """触发一次事件（唯一入口）；返回实际执行的记录

    ⚠ 每张卡每个事件**每回合最多 `WATCH_LIMIT` 次**：既对应卡面
      「The effect … doesn't stack」，也防止「A 触发 B、B 再触发 A」的无限链。
      被抑制的次数**写日志**（静默抑制 = 看不出来）。
    """
    if kind not in EMIT_POINTS:
        raise ValueError(f"未登记的事件点：{kind}")
    ctx = dict(ctx or {})
    fired = []
    counts = getattr(battle, "_watch_fired", None)
    if counts is None:
        counts = {}
        battle._watch_fired = counts
    for pi, tpos, fp, name, body in _watchers(battle, kind):
        key = (id(fp), kind, body)
        turn, n = counts.get(key, (battle.turn_number, 0))
        if turn != battle.turn_number:
            turn, n = battle.turn_number, 0
        if n >= WATCH_LIMIT:
            battle._log("event_suppressed", {"kind": kind, "by": name,
                                             "owner": pi, "body": body})
            continue
        counts[key] = (turn, n + 1)
        battle._run_watch_body(pi, fp, name, body, kind, ctx)
        fired.append({"owner": pi, "by": name, "body": body})
    if fired:
        battle._log("event", {"kind": kind, "n": len(fired),
                              "by": [f["by"] for f in fired]})
    return fired


def reset_turn_counters(battle) -> None:
    """新回合开始：清空「每回合触发次数」记账（在 `_begin_turn` 调用）"""
    battle._watch_fired = {}


# ---------------- 换位事件（diff 检测：唯一落点，不散落在各 helper） ----------------

def snapshot_switch(battle) -> None:
    """在 `step()` 分发**之前**记录双方 active 的对象身份与回合归属

    为什么用「快照 + 比对」而不是在每个换位 helper 里手动 emit：
    引擎里「战斗位下台」的写法有 10 处（撤退 / Switch / 拉人 / 换位特性 / 弹回手牌…），
    逐处 emit 必漏（历史纪律：「静默漏效果」比未建模更糟）。diff 只有一个落点，
    新增换位路径**自动**被覆盖。
    """
    battle._switch_snap = {
        "current": battle.current,
        "turn": battle.turn_number,
        "actives": [p.active for p in battle.players],
    }


def emit_switch_diff(battle) -> list:
    """`step()` 执行完之后比对 active 身份 → emit switch_out / switch_in

    判据（每一条都必须写清，否则会误报）：
      · `old is new`            → 没换位，跳过；
      · `old is None`           → 之前空场（补位），不算「下台」；
      · `old.is_ko`             → 被击倒后的补位，不算「换下」（走 KO 流程）；
      · 其余                   → 发出一对事件（`leaving` = 原来那只，可能已在后备/手牌）。
    """
    snap = getattr(battle, "_switch_snap", None)
    if not snap:
        return []
    battle._switch_snap = None
    fired = []
    for pi, old in enumerate(snap["actives"]):
        new = battle.players[pi].active
        if old is new or old is None:
            continue
        if getattr(old, "is_ko", False):
            continue
        ctx = {"player": pi, "leaving": old, "entering": new,
               "turn_owner": snap["current"]}
        fired += emit(battle, "switch_out", ctx)
        if new is not None:
            fired += emit(battle, "switch_in", ctx)
    return fired
