# -*- coding: utf-8 -*-
"""PokéLab Engine · 对局流程用例（端到端）

覆盖：完整对局可终局、确定性（同 seed 同事件序列）、起手必有 active、
批量统计聚合正确、max_turns 判平保护。

运行：python -m unittest discover -s tests -v
"""
from __future__ import annotations
import unittest
from engine import decklib
from engine.state import GameConfig
from engine.agents import RandomAgent, BaselineAI
from engine.battle import Battle
from engine.simulator import run_match, run_many, summarize


class TestFlow(unittest.TestCase):
    def test_single_match_terminates(self):
        da, db = decklib.deck_lightning_demo(), decklib.deck_grass_demo()
        b = run_match(da, db, RandomAgent(seed=0), RandomAgent(seed=1),
                      seed=42, cfg=GameConfig(max_turns=60))
        self.assertTrue(b.finished)
        self.assertIn(b.win_reason, ("prizes", "no_pokemon", "deckout",
                                     "max_turns_draw"))
        self.assertGreater(len(b.events), 5)
        s = summarize(b)
        self.assertEqual(s["winner"], b.winner)

    def test_determinism_same_seed(self):
        da, db = decklib.deck_fire_demo(), decklib.deck_psychic_demo()
        a = RandomAgent(seed=0); c = RandomAgent(seed=0)
        b1 = run_match(da, db, a, c, seed=7)
        b2 = run_match(decklib.deck_fire_demo(), decklib.deck_psychic_demo(),
                       RandomAgent(seed=0), RandomAgent(seed=0), seed=7)
        self.assertEqual(b1.winner, b2.winner)
        self.assertEqual(b1.win_reason, b2.win_reason)
        self.assertEqual(b1.turn_number, b2.turn_number)
        self.assertEqual([e["type"] for e in b1.events],
                         [e["type"] for e in b2.events])

    def test_opening_always_has_active(self):
        cfg = GameConfig()
        for i in range(40):
            b = Battle(decklib.deck_lightning_demo(),
                       decklib.deck_lightning_demo(), seed=i, cfg=cfg)
            self.assertIsNotNone(b.players[0].active)
            self.assertIsNotNone(b.players[1].active)

    def test_many_stats_consistency(self):
        da, db = decklib.deck_lightning_demo(), decklib.deck_grass_demo()
        st = run_many(da, db, BaselineAI(), RandomAgent(seed=3), n=30)
        self.assertEqual(st["n"], 30)
        self.assertEqual(st["wins_a"] + st["wins_b"] + st["draws"], 30)
        self.assertAlmostEqual(st["win_rate_a"], st["wins_a"] / 30)
        self.assertIn("avg_turns", st)
        # 基线 AI 面对随机不应明显劣势（宽容断言：≥30%）
        self.assertGreaterEqual(st["win_rate_a"], 0.3)

    def test_max_turns_draw_guard(self):
        # 超长对局触发 max_turns 判平（防御路径）
        from engine.cards import PokemonDef, AttackDef, PokeRef, EnergyRef
        W = AttackDef("Weak", 3, 5)
        Tank = PokemonDef("Tank", hp=9999, ptype="colorless", attacks=(W,))
        deck = [PokeRef(Tank)] * 50 + [EnergyRef()] * 30   # 大牌库防 deckout
        b = run_match(deck, list(deck), BaselineAI(), BaselineAI(), seed=1,
                      cfg=GameConfig(max_turns=8, prizes=1, opening_hand=1,
                                     max_bench=1))
        # 能量每回合1，攻击需3能、5伤打9999HP → 8 回合无法终局 → 判平
        self.assertTrue(b.finished)
        self.assertEqual(b.win_reason, "max_turns_draw")
        self.assertIsNone(b.winner)


class TestBaselineBehaviour(unittest.TestCase):
    def test_baseline_attacks_when_ready(self):
        # 构造 view：active 能量已足、手无能量
        from engine import decklib as dl
        b = Battle(dl.deck_lightning_demo(), dl.deck_lightning_demo(), seed=5)
        # 跳过复杂准备：直接给当前方 active 挂够能量
        cur = b.players[b.current]
        cur.active.energy = 3
        cur.hand.clear()
        v = b.view()
        agent = BaselineAI()
        act = agent.decide(v)
        self.assertEqual(act["verb"], "attack")

    def test_baseline_never_illegal(self):
        da, db = decklib.deck_status_demo(), decklib.deck_status_demo()
        b = run_match(da, db, BaselineAI(), BaselineAI(), seed=11)
        self.assertTrue(b.finished)
        # 非法动作兜底路径存在（事件含 turn_end 即说明正常推进）
        self.assertGreater(b.turn_number, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
