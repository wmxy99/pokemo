# -*- coding: utf-8 -*-
"""第一百三十五批（S1a）· 决策分解层：引擎用例

协议性质（每条都有「必须在」与「必须不在」两向）：

  1. 打出「任意数量」卡 → `phase="choice"`，合法动作**只有** choose/stop；
  2. **引擎从不自选**：挂起期间没有牌被弃掉（状态冻结）；
  3. 选择可以重复「选一张」直到 `stop`；`stop` 之后才执行后续 tag（抽牌）；
  4. 挂起期间做别的动作 → 被拒绝（跳过选择 = 静默默认，必须禁止）；
  5. `max` 达到时自动闭合（不逼玩家多点一次 stop）；
  6. 前置条件不满足时**不进**合法动作表（手牌 ≥ 5 的 Naveen 不能用）。

运行：cd packages/engine && python -m unittest tests.test_choice_protocol -v
"""
from __future__ import annotations

import random
import unittest

from engine.battle import Battle
from engine.cards import (AttackDef, EnergyDef, EnergyRef, PokemonDef,
                          TrainerDef, TrainerRef, make_pokemon)
from engine.state import GameConfig, PlayerState


def _arena(extra_hand: int = 3, *, gate: str = "own_hand_at_most:4",
           max_pick: int = -1):
    """一张「弃任意数量手牌，然后抽到 5 张」的支援者 + N 张手牌"""
    cfg = GameConfig()
    b = object.__new__(Battle)
    b.cfg = cfg
    b.seed = 7
    b.rng = random.Random(7)
    b.players = [PlayerState(name="A", prizes_left=6),
                 PlayerState(name="B", prizes_left=6)]
    b.current = 0
    b.turn_number = 1
    b.turn_counts = [1, 1]
    b.phase = "main"
    b._pending = None
    b.finished = False
    b.winner = None
    b.win_reason = None
    b.events = []
    b.stadium = None
    b._ko_by_attack = [0, 0]
    b._ko_tr_by_attack = [0, 0]
    b.players[0].active = make_pokemon(PokemonDef(
        "A", hp=300, ptype="colorless",
        attacks=(AttackDef("Pass", cost=(), damage=10),)))
    b.players[1].active = make_pokemon(PokemonDef(
        "B", hp=300, ptype="colorless"))
    b.players[0].deck = [EnergyRef(EnergyDef("colorless")) for _ in range(20)]
    b.players[1].deck = [EnergyRef(EnergyDef("colorless")) for _ in range(20)]
    tag = ("choose:discard_hand:any:then:draw_to:5" if max_pick < 0
           else f"choose:discard_hand:{max_pick}:then:draw_to:5")
    b.players[0].hand.append(TrainerRef(TrainerDef(
        tag, "supporter", "Naveen", play_gate=gate)))
    b.players[0].hand += [EnergyRef(EnergyDef("fire"))
                          for _ in range(extra_hand)]
    return b


def _play(b):
    act = [a for a in b.legal_actions() if a["verb"] == "trainer"][0]
    return b.step(act)


class TestChoiceOpens(unittest.TestCase):
    def test_play_opens_choice_and_freezes_state(self):
        b = _arena()
        before_hand = len(b.players[0].hand)
        r = _play(b)
        self.assertTrue(r["ok"], r)
        self.assertEqual(b.phase, "choice")
        self.assertIsNotNone(b._pending)
        # 打出后本卡进弃牌堆（-1），但**没有任何牌被选择性地弃掉**
        self.assertEqual(len(b.players[0].hand), before_hand - 1)
        self.assertEqual(len(b.players[0].discard), 1,
                         "只应有本卡进弃牌堆：挂起期间不得替玩家弃牌")

    def test_legals_only_choose_and_stop(self):
        b = _arena()
        _play(b)
        verbs = {a["verb"] for a in b.legal_actions()}
        self.assertEqual(verbs, {"choose", "stop"}, verbs)

    def test_play_gate_blocks_when_hand_too_big(self):
        b = _arena(extra_hand=5)          # 手牌 6 张 ≥ 5 → 抽不到牌 → 不可用
        self.assertEqual([a for a in b.legal_actions()
                          if a["verb"] == "trainer"], [])
        b2 = _arena(extra_hand=4)         # 恰好 5 张（含本卡）→ 也不可用
        self.assertEqual([a for a in b2.legal_actions()
                          if a["verb"] == "trainer"], [])

    def test_other_verbs_rejected_while_choice(self):
        b = _arena()
        _play(b)
        r = b.step({"verb": "pass"})
        self.assertFalse(r["ok"], "挂起期间必须拒绝别的动作（否则选择被跳过）")
        self.assertEqual(b.phase, "choice", "被拒绝后仍应停在 choice")
        self.assertEqual(len(b.players[0].discard), 1,
                         "拒绝的动作不得改变状态")


class TestChoicePicks(unittest.TestCase):
    def test_pick_records_but_does_not_move_cards(self):
        """选择期间状态**冻结**：只记录 picks，close 时才一次性落盘

        为什么这样设计：若边选边改状态，「选中第 i 张」的下标会在弃牌后失效
        （静默弃错牌），也无法满足「挂起即冻结」这条不变量。
        """
        b = _arena()
        _play(b)
        hand_before = len(b.players[0].hand)
        discard_before = len(b.players[0].discard)
        r = b.step({"verb": "choose", "pick": 0})
        self.assertTrue(r["ok"], r)
        self.assertEqual(len(b.players[0].hand), hand_before, "选的时候不改手牌")
        self.assertEqual(len(b.players[0].discard), discard_before)
        self.assertEqual(b.phase, "choice")
        # 结束选择后才真正弃掉
        b.step({"verb": "stop"})
        self.assertEqual(len(b.players[0].hand), 5,
                         "弃 1 张（剩 2）后抽到 5 张")

    def test_pick_out_of_range_rejected(self):
        b = _arena()
        _play(b)
        r = b.step({"verb": "choose", "pick": 99})
        self.assertFalse(r["ok"])
        self.assertEqual(b.phase, "choice")

    def test_pick_twice_same_index_rejected(self):
        b = _arena()
        _play(b)
        b.step({"verb": "choose", "pick": 0})
        r = b.step({"verb": "choose", "pick": 0})
        self.assertFalse(r["ok"], "同一张牌不得被弃两次")

    def test_stop_then_draw_to_five(self):
        b = _arena(extra_hand=3)          # 手牌：本卡 + 3 张 → 打出后 3 张
        _play(b)
        b.step({"verb": "choose", "pick": 0})   # 弃 1 → 2 张
        r = b.step({"verb": "stop"})
        self.assertTrue(r["ok"], r)
        self.assertEqual(b.phase, "main")
        self.assertEqual(len(b.players[0].hand), 5, "应在 stop 之后抽到 5 张")
        self.assertEqual(len(b.players[0].deck), 17)
        types = [e["type"] for e in b.events]
        self.assertIn("choice_close", types)
        self.assertIn("draw", types, "then 里的 draw_to 必须真的执行")

    def test_stop_without_pick_is_legal(self):
        """卡面「any number」包含 0 张 → 直接结束选择是合法的"""
        b = _arena(extra_hand=1)
        _play(b)
        r = b.step({"verb": "stop"})
        self.assertTrue(r["ok"], r)
        self.assertEqual(len(b.players[0].hand), 5)
        self.assertEqual(len(b.players[0].discard), 1, "只应有本卡")

    def test_max_pick_auto_closes(self):
        b = _arena(extra_hand=3, max_pick=1)
        _play(b)
        r = b.step({"verb": "choose", "pick": 0})
        self.assertTrue(r["ok"], r)
        self.assertEqual(b.phase, "main", "达到 max 应自动闭合，不要求再点 stop")
        self.assertEqual(len(b.players[0].hand), 5)


class TestCountersAnyTarget(unittest.TestCase):
    """第二种 kind：任意分配指示物（含战斗位）—— 顺序不限、可重复同一目标"""

    def _b(self):
        b = _arena()
        b.players[0].hand.pop()               # 不需要手牌
        bench = make_pokemon(PokemonDef("Bench", hp=200, ptype="water"))
        b.players[1].bench.append(bench)
        b._open_choice("counters_any_opp",
                       [{"target": 0, "display": "Active"},
                        {"target": 1, "display": "Bench"}],
                       min_pick=2, max_pick=4, allow_repeat=True)
        return b, bench

    def test_distribution_applied_on_close(self):
        b, bench = self._b()
        b.step({"verb": "choose", "pick": 0})
        b.step({"verb": "choose", "pick": 0})
        b.step({"verb": "choose", "pick": 1})
        self.assertEqual(b.players[1].active.damage, 0,
                         "选择期间不得改动指示物（挂起即冻结）")
        r = b.step({"verb": "stop"})
        self.assertTrue(r["ok"], r)
        self.assertEqual(b.players[1].active.damage, 20, "两枚在战斗位")
        self.assertEqual(bench.damage, 10, "第三枚放后备")
        self.assertEqual(b.phase, "main")

    def test_stop_before_min_rejected(self):
        b, _ = self._b()
        b.step({"verb": "choose", "pick": 0})
        r = b.step({"verb": "stop"})
        self.assertFalse(r["ok"], "min 未满足时不得结束选择")
        self.assertEqual(b.phase, "choice")


if __name__ == "__main__":
    unittest.main()
