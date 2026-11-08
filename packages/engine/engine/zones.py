# -*- coding: utf-8 -*-
"""区域对象化（S3）· 奖赏卡槽 / 牌库底 / 对手区域检视

为什么需要它
------------
引擎原来把奖赏卡当成一个**计数器**（`PlayerState.prizes_left` 从 6 递减），
牌库则是「开局抽 7 张后剩下的 53 张」—— 那 6 张奖赏卡**从来没离开过牌库**。
于是下面这些卡面**根本无处落地**：

  · 「Count your Prize cards, shuffle them, and put them on the bottom of your
     deck. Then, take that many cards from the top of your deck and put them
     face down as your Prize cards.」（Redeemable Ticket）
  · 「Turn 1 of your opponent's face-down Prize cards face up …」
    （Team Rocket's Bother-Bot）
  · 「Look at 1 of your opponent's face-down Prize cards.」（Blipbug）

本模块给三件事补上「一等公民」身份：

  1. **奖赏卡槽** `PrizeSlot`：真卡对象 + `face_up` 标志（官方规则里奖赏卡
     可以被打成正面向上的，且**保持到对局结束**）；
  2. **牌库底**：`deck_bottom_take/put`（`deck[0]` 是牌库底，`deck[-1]` 是顶）；
  3. **只读检视** `peek_top/peek_bottom`：不改动任何区域，供「reveal / look at」
     类效果与决策分解的选项构造使用。

⚠ 两条口径（写在这里免得后面踩）
  · `PlayerState.prizes` 为**空**时进入「只计数」兼容模式：早期测试夹具直接
    `PlayerState(name=..., prizes_left=6)` 装配，没有从牌库发牌的过程。此时
    `prize_count()` 退化为读 `prizes_left`，`take_prizes()` 只减计数。
    真正做到对象化的对局（`Battle.__init__` + `GameConfig.real_prizes=True`）
    两者**恒等**，由 `test_zones_s3.py` 的不变量断言守护。
  · **取哪一张**：面朝下的奖赏卡对牌手是**不可区分信息**（官方规则：面朝下时
    不得查看），因此「取哪张」在全部面朝下时**不构成决策**；存在面朝上的卡时，
    取哪张**是**决策 —— 本阶段先固定为「先面朝下、后面朝上」并写日志，
    真正的选择留 S6（`RULE_CONFORMANCE` 有登记）。绝不假装已经做了选择。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PrizeSlot:
    """一张奖赏卡：真卡对象 + 是否已翻到正面（翻面后保持到对局结束）"""
    ref: object
    face_up: bool = False


def has_real_prizes(p) -> bool:
    """该玩家是否处于「奖赏卡已对象化」模式

    ⚠ 判据是**显式旗标** `prizes_real`，不是 `bool(p.prizes)` —— 用后者会在
    「最后一张奖赏卡被取走」的瞬间把模式翻回「只计数」，于是 `_sync` 被跳过、
    `prizes_left` 停在 1（实测：第 64 回合 1 张奖赏卡变成 `prizes=0/left=1`，
    整局随后的取奖赏判断全部错位）。这个 bug 只有跑满一局才暴露。
    """
    return bool(getattr(p, "prizes_real", False))


def prize_count(p) -> int:
    """剩余奖赏卡数（对象化模式取真实张数，否则读历史计数）"""
    return len(p.prizes) if has_real_prizes(p) else int(p.prizes_left)


def _sync(p) -> None:
    """把 `prizes_left` 与真实张数对齐（**唯一**的同步点）"""
    if has_real_prizes(p):
        p.prizes_left = len(p.prizes)


def _label(ref) -> str:
    """卡名标签（复用 battle 的同名单一实现，避免两处漂移）

    延迟 import：`battle` 在模块加载期就 import 了本模块（循环前提）。
    """
    from .battle import _card_label
    return _card_label(ref)


def deal_prizes(battle, pi: int, n: int) -> int:
    """开局（或补发）从牌库**顶**取 n 张作为面朝下奖赏卡

    官方 setup 第 7 步：「Set aside 6 prize cards face down.」——这 6 张是
    **真从牌库取走**的，因此之后 `deck_count` 上限是 60-7-6=47（不是 53）。
    """
    p = battle.players[pi]
    p.prizes_real = True          # 一旦发过牌就永久是对象化模式（哪怕取空）
    got = 0
    while len(p.prizes) < n and p.deck:
        p.prizes.append(PrizeSlot(p.deck.pop()))
        got += 1
    _sync(p)
    battle._log("prize_deal", {"player": pi, "n": got,
                              "left": p.prizes_left, "deck": len(p.deck)})
    # 牌库不足 6 张（只会出现在小型测试卡组里）→ 如实记录，**不**悄悄补齐
    if got < n:
        battle._log("prize_deal_short", {"player": pi, "want": n, "got": got})
    return got


def take_prizes(battle, taker: int, n: int) -> int:
    """取 n 张奖赏卡入手（击倒结算 / 效果取卡共用）

    返回**实际**取到的张数（剩余不足时按剩余算，绝不凭空生成）。
    """
    n = int(n)
    if n <= 0:
        return 0
    p = battle.players[taker]
    real = has_real_prizes(p)     # ⚠ 先取快照：中途 `p.prizes` 可能被取空
    if not real:
        # 只计数兼容模式（历史夹具）：语义与旧实现逐字节一致
        got = min(n, int(p.prizes_left))
        p.prizes_left = max(0, int(p.prizes_left) - got)
        return got
    # 面朝下优先（不可区分信息），其后才是面朝上的
    order = ([i for i, s in enumerate(p.prizes) if not s.face_up]
             + [i for i, s in enumerate(p.prizes) if s.face_up])
    sel = order[:n]
    got = []
    for i in sorted(sel, reverse=True):
        slot = p.prizes.pop(i)
        ref = battle._rebuild_ref(slot.ref)
        p.hand.append(ref)
        got.append(slot)
    _sync(p)
    battle._log("prize_take", {
        "player": taker, "n": len(got), "left": p.prizes_left,
        "faces_up": sum(1 for s in got if s.face_up),
        "cards": [_label(s.ref) for s in got]})
    # 手牌上限是引擎的**防御性**简化（官方无上限，S6 处理）；这里与抽牌同口径
    if len(p.hand) > battle.cfg.max_hand:
        over = p.hand[battle.cfg.max_hand:]
        for c in over:
            battle._to_discard(p, c)
        del p.hand[battle.cfg.max_hand:]
        battle._log("hand_overflow", {"player": taker, "dropped": len(over)})
    return len(got)


# ---------------- 牌库底原语 ----------------
# ⚠ 约定：`deck[0]` = **牌库底**，`deck[-1]` = **牌库顶**（`pop()` 取顶）。
#   历史代码（`recon_bottom` / `_trainer_recon_bottom`）已经是这个约定，此处只是
#   把它变成具名原语，避免以后有人按「列表第 0 个是顶」理解而**静默反序**。

def deck_bottom_take(p, n: int) -> list:
    """从牌库**底**取 n 张（按「底→上」顺序返回），取走即离开牌库"""
    out = []
    for _ in range(max(0, int(n))):
        if not p.deck:
            break
        out.append(p.deck.pop(0))
    return out


def deck_bottom_put(p, refs) -> int:
    """把若干张牌放到牌库**底**（保持传入顺序：第一个参数成为最底）"""
    refs = list(refs or ())
    for ref in reversed(refs):
        p.deck.insert(0, ref)
    return len(refs)


def put_on_top(p, refs) -> int:
    """把若干张牌放到牌库**顶**（保持传入顺序：最后一个成为最顶）"""
    refs = list(refs or ())
    for ref in refs:
        p.deck.append(ref)
    return len(refs)


# ---------------- 只读检视 ----------------

def peek_top(p, n: int) -> list:
    """查看牌库顶 n 张（**只读**，返回「顶→下」顺序）"""
    n = min(max(0, int(n)), len(p.deck))
    return list(reversed(p.deck[-n:])) if n else []


def peek_bottom(p, n: int) -> list:
    """查看牌库底 n 张（**只读**，返回「底→上」顺序）"""
    n = min(max(0, int(n)), len(p.deck))
    return list(p.deck[:n])


def peek_prizes(p, n: int = 0) -> list:
    """查看奖赏卡（**只读**）；n=0 表示全部"""
    slots = list(p.prizes)
    return slots if not n else slots[:n]


# ---------------- 守恒校验（守卫用） ----------------

def card_total(p) -> int:
    """该玩家各区域的**张数总和**（供守恒断言用，不是引擎状态）

    ⚠ 口径限制（如实记录，别当成 60 恒等式）：基本能量附着后引擎只保存
    属性计数（`fp.energy[type]`），**不保留卡对象**；宝可梦被击倒时其附着卡
    也没有逐张进 `discard_refs`。因此本函数只覆盖「牌库 / 手牌 / 奖赏 / 弃牌 /
    场上宝可梦本体」，用来守护**奖赏与牌库底这些新原语不吞牌**。
    """
    field = len(p.all_field())
    return (len(p.deck) + len(p.hand) + len(p.prizes)
            + len(p.discard_refs) + field)
