# -*- coding: utf-8 -*-
"""第五十八~五十九批（2026-11-04）· 引擎用例

第五十八批：`draw_cond:`（条件抽牌）与 `shuffle_draw_split:`（双方洗回后各抽不同张数）
第五十九批：`TrainerDef.play_gate`（打出**前置条件**，与 `play_cost` 相反）

三处必须成立：
  · `plus`（再抽 more）与 `set`（改成 instead）**方向相反**，落反 = 静默改强度；
  · 条件不满足时 `draw_cond` 仍抽 base 张（卡面没写 only if，不能变成摆设）；
  · `play_gate` 不满足时卡**不进**合法动作表（不是打出去才发现）；
  · `play_gate` **不扣任何牌** —— 与 `play_cost` 是本批最容易混的两件事。
"""
from __future__ import annotations
import random
import unittest

from engine.battle import Battle
from engine.cards import PokemonDef, TrainerDef, TrainerRef, make_pokemon
from engine.state import GameConfig, PlayerState


def _arena(seed=7):
    cfg = GameConfig()
    b = object.__new__(Battle)
    b.cfg = cfg
    b.seed = seed
    b.rng = random.Random(seed)
    b.players = [PlayerState(name="A", prizes_left=cfg.prizes),
                 PlayerState(name="B", prizes_left=cfg.prizes)]
    b.current = 0
    b.turn_number = 1
    b.turn_counts = [1, 1]
    b.phase = "main"
    b.finished = False
    b.winner = None
    b.win_reason = None
    b.events = []
    b.stadium = None
    b._ko_by_attack = [0, 0]
    b.players[0].active = make_pokemon(
        PokemonDef("A", hp=100, ptype="grass", attacks=()))
    b.players[1].active = make_pokemon(
        PokemonDef("B", hp=100, ptype="colorless", attacks=()))
    return b


def _pad(b, who=0, n=10):
    """给某一方补牌库（抽牌类效果需要牌库非空）"""
    p = b.players[who]
    p.deck = [TrainerRef(TrainerDef(f"draw:{i}", kind="item", name=f"D{i}"))
              for i in range(n)]
    return p


class TestDrawCond(unittest.TestCase):
    """`draw_cond:<keep|shuffle>:<base>:<plus|set>:<n>:if:<cond>`"""

    def _run(self, tag, who_hand=0, target=10):
        b = _arena()
        p = _pad(b)
        from engine.cards import TrainerDef as TD
        card = TrainerRef(TD(tag, kind="supporter", name="T"))
        p.hand = [card] + [
            TrainerRef(TD(f"draw:{i}", kind="item", name=f"H{i}"))
            for i in range(who_hand - 1)]
        before = len(p.hand)
        detail = b._trainer_draw_cond(p, tag, 0)
        return b, p, before, detail

    def test_plus_branch_draws_more(self):
        """10 张手牌 ⇒ 条件满足 ⇒ 抽 2 + 2 = 4（不是 2）"""
        b, p, before, detail = self._run(
            "draw_cond:keep:2:plus:2:if:own_hand_at_least:5", who_hand=10)
        # ⚠ 本函数**不**负责把本卡移出手牌（那是 `_do_trainer` 派发层的事），
        # 所以这里只验「真的抽了 4 张」= before + 4。
        self.assertEqual(len(p.hand), before + 4, detail)

    def test_plus_branch_condition_false_draws_base(self):
        """手牌不足 ⇒ 只抽 base 2 张（卡面没写 only if，不能变摆设）"""
        b, p, before, detail = self._run(
            "draw_cond:keep:2:plus:2:if:own_hand_at_least:50", who_hand=3)
        self.assertEqual(len(p.hand), before + 2, detail)

    def test_set_branch_replaces_not_adds(self):
        """`set` 是**换值**：条件满足时抽 8，不是 4 + 8 = 12"""
        b, p, before, detail = self._run(
            "draw_cond:keep:4:set:8:if:own_hand_at_least:0", who_hand=3)
        self.assertEqual(len(p.hand), before + 8, detail)

    def test_shuffle_returns_hand_first(self):
        """`shuffle` 模式：手牌洗回牌库（不含自身）后再抽"""
        b, p, before, detail = self._run(
            "draw_cond:shuffle:4:set:8:if:own_hand_at_least:50", who_hand=6)
        # 洗回 5 张（本卡除外）→ 牌库 10 + 5 = 15，再抽走 4 → 11；手牌剩本卡 + 4
        self.assertEqual(len(p.hand), 1 + 4, detail)
        self.assertEqual(len(p.deck), 10 + 5 - 4, detail)

    def test_unknown_mode_raises(self):
        b = _arena()
        p = _pad(b)
        with self.assertRaises(ValueError):
            b._trainer_draw_cond(p, "draw_cond:bogus:2:plus:2:if:x", 0)

    def test_missing_if_raises(self):
        """缺 `if:` 段 = 形态非法（不带条件的抽牌应走 `draw:`）"""
        b = _arena()
        p = _pad(b)
        with self.assertRaises(ValueError):
            b._trainer_draw_cond(p, "draw_cond:keep:2:plus:2", 0)


class TestShuffleSplitDraw(unittest.TestCase):
    """`shuffle_draw_split:<mine>:<theirs>`"""

    def test_different_counts(self):
        """我方 5 / 对手 2 —— **不是**双方各 5（`draw_both` 的量纲）"""
        b = _arena()
        p = _pad(b, 0, 10)
        o = _pad(b, 1, 10)
        me = [TrainerRef(TrainerDef("shuffle_draw_split:5:2", kind="item",
                                    name="Unfair Stamp"))]
        p.hand = me + [TrainerRef(TrainerDef("draw:1", kind="item",
                                             name=f"H{i}")) for i in range(3)]
        o.hand = [TrainerRef(TrainerDef("draw:1", kind="item", name=f"O{i}"))
                  for i in range(4)]
        b._trainer_shuffle_draw_split(p, "shuffle_draw_split:5:2", 0)
        self.assertEqual(len(p.hand), 1 + 5)
        self.assertEqual(len(o.hand), 2)

    def test_bad_tag_raises(self):
        b = _arena()
        p = _pad(b)
        with self.assertRaises(ValueError):
            b._trainer_shuffle_draw_split(p, "shuffle_draw_split:5", 0)


class TestPlayGate(unittest.TestCase):
    """`TrainerDef.play_gate` —— 打出**前置条件**（无状态变化）"""

    def _hand(self, b, gate):
        card = TrainerRef(TrainerDef("draw:1", kind="item", name="Gated",
                                     play_gate=gate))
        b.players[0].hand = [card]
        _pad(b)
        return card

    def test_gate_ok_lists_action(self):
        b = _arena()
        self._hand(b, "own_behind_prizes")
        b.players[0].prizes_left = 6
        b.players[1].prizes_left = 3
        acts = b._trainer_legals(b.players[0], b.players[1])
        self.assertTrue(any(a.get("name") == "Gated" for a in acts))

    def test_gate_fail_hides_action(self):
        """不满足时**不列**动作（AI 就不会反复尝试一张必然失败的卡）"""
        b = _arena()
        self._hand(b, "own_behind_prizes")
        b.players[0].prizes_left = 2
        b.players[1].prizes_left = 5
        acts = b._trainer_legals(b.players[0], b.players[1])
        self.assertFalse(any(a.get("name") == "Gated" for a in acts))

    def test_gate_check_has_no_side_effect(self):
        """⚠ 本批最容易混的一处：gate **不扣牌**（扣牌的是 `play_cost`）

        `_play_gate_ok` 是**纯查询** —— 调用后手牌 / 弃牌堆 / 奖赏数都必须不变。
        落成「顺手弃 1 张」就是把条件当成了代价（玩家白亏一张牌）。
        """
        b = _arena()
        self._hand(b, "own_behind_prizes")
        b.players[0].prizes_left = 6
        b.players[1].prizes_left = 3
        before = (len(b.players[0].hand), len(b.players[0].discard))
        self.assertTrue(b._play_gate_ok(b.players[0], "own_behind_prizes"))
        self.assertEqual((len(b.players[0].hand), len(b.players[0].discard)),
                         before)

    def test_unknown_gate_raises(self):
        """认不出的 gate 形态**抛错**（不默认放行）"""
        b = _arena()
        with self.assertRaises(ValueError):
            b._play_gate_ok(b.players[0], "no_such_gate")

    def test_went_second_first_turn(self):
        b = _arena()
        p = b.players[0]
        b.current = 0
        self.assertFalse(b._play_gate_ok(p, "went_second_first_turn"))
        b.current = 1                      # 后手
        b.turn_counts = [1, 1]
        self.assertTrue(b._play_gate_ok(b.players[1],
                                        "went_second_first_turn"))
        b.turn_counts = [1, 2]             # 后手的第三回合起失效
        self.assertFalse(b._play_gate_ok(b.players[1],
                                         "went_second_first_turn"))

    def test_opp_prizes_exactly(self):
        """`exactly` 与 `at most` 差一个词：残局（更少）不该放行"""
        b = _arena()
        p = b.players[0]
        b.players[1].prizes_left = 2
        self.assertTrue(b._play_gate_ok(p, "opp_prizes_exactly:2"))
        b.players[1].prizes_left = 1
        self.assertFalse(b._play_gate_ok(p, "opp_prizes_exactly:2"))

    def test_tera_in_play_includes_bench(self):
        b = _arena()
        b.players[0].bench.append(make_pokemon(
            PokemonDef("Tera", hp=100, ptype="colorless", attacks=(),
                       category="Tera")))
        self.assertTrue(b._play_gate_ok(b.players[0], "tera_in_play"))

    def test_tera_in_play_absent(self):
        b = _arena()
        self.assertFalse(b._play_gate_ok(b.players[0], "tera_in_play"))


if __name__ == "__main__":
    unittest.main()
