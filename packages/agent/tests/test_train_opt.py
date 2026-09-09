# -*- coding: utf-8 -*-
"""PokéLab Agent · 训练与评测优化用例（reward shaping / EncoderConfig / DQN / eval 矩阵）

运行：cd packages/agent && python -m unittest discover -s tests -v
"""
from __future__ import annotations
import sys
import unittest
from pathlib import Path

_ENGINE = Path(__file__).resolve().parents[3] / "packages" / "engine"
sys.path.insert(0, str(_ENGINE))

import torch  # noqa: E402
from engine import decklib  # noqa: E402
from engine.battle import Battle  # noqa: E402

from agent.state_encoder import (encode_state, STATE_DIM,  # noqa: E402
                                 EncoderConfig, encoder_dim)
from agent.policy_net import SmallPolicyValueNet  # noqa: E402
from agent import reward as rw  # noqa: E402
from agent import dqn, trainer  # noqa: E402


class TestRewardShaping(unittest.TestCase):
    def _view(self, me_prizes=3, opp_prizes=3, me_hp=200, opp_hp=200):
        base = {"turn": 5, "legal": []}
        base["me_state"] = {"hand": [], "active": {"hp": 200, "dmg": 200 - me_hp,
                            "energy": 2}, "bench": [], "prizes_left": me_prizes,
                            "deck_count": 30, "hand_count": 5, "discard_count": 5}
        base["opp_state"] = {"active": {"hp": 200, "dmg": 200 - opp_hp,
                             "energy": 2}, "bench": [], "prizes_left": opp_prizes,
                             "deck_count": 30, "hand_count": 5, "discard_count": 5}
        return base

    def test_even_near_mid(self):
        v = self._view()
        s = rw.state_reward(v)
        self.assertGreaterEqual(s, 0.35)
        self.assertLessEqual(s, 0.65)

    def test_prize_lead_raises(self):
        v = self._view(me_prizes=1, opp_prizes=6)
        self.assertGreater(rw.state_reward(v),
                           rw.state_reward(self._view(me_prizes=6, opp_prizes=1)))

    def test_range(self):
        for v in (self._view(0, 6), self._view(6, 0)):
            self.assertGreaterEqual(rw.state_reward(v), 0.0)
            self.assertLessEqual(rw.state_reward(v), 1.0)


class TestEncoderConfig(unittest.TestCase):
    def test_legacy_default(self):
        b = Battle(decklib.deck_lightning_demo(),
                   decklib.deck_fire_demo(), seed=1)
        v = b.view()
        self.assertEqual(len(encode_state(v)), STATE_DIM)
        self.assertEqual(encoder_dim(None), STATE_DIM)

    def test_full_features_dim(self):
        b = Battle(decklib.deck_lightning_demo(),
                   decklib.deck_fire_demo(), seed=2)
        cfg = EncoderConfig(features=("hand", "meta"))
        s = encode_state(b.view(), cfg)
        self.assertEqual(len(s), encoder_dim(cfg))
        self.assertEqual(encoder_dim(cfg), STATE_DIM + 8)
        # full-dim 网络可前向
        net = SmallPolicyValueNet(encoder_dim(cfg), 14)
        st = torch.randn(1, encoder_dim(cfg))
        fa = torch.randn(1, 4, 14)
        logits, _ = net(st, fa)
        self.assertEqual(logits.shape, (1, 4))

    def test_unknown_flag_raises(self):
        b = Battle(decklib.deck_lightning_demo(),
                   decklib.deck_fire_demo(), seed=3)
        with self.assertRaises(ValueError):
            encode_state(b.view(), EncoderConfig(features=("bogus",)))


class TestDqnSmoke(unittest.TestCase):
    def test_train_short(self):
        import tempfile
        from engine.agents import BaselineAI
        da, db = decklib.deck_lightning_demo(), decklib.deck_lightning_demo()
        net = SmallPolicyValueNet(STATE_DIM, 14)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "dqn.pt"
            st = dqn.train_dqn_vs_fixed(da, db, net,
                                        opponent=BaselineAI("b"),
                                        episodes=3, target_update=2,
                                        seed=5, save_path=str(path))
            self.assertEqual(st["episodes"], 3)
            self.assertTrue(path.exists())
            self.assertTrue(all(x == x for x in st["q_loss"]))


class TestEvalHelpers(unittest.TestCase):
    def test_run_mode_returns_latency(self):
        from engine.agents import BaselineAI
        from examples.eval_matrix import run_mode
        da, db = decklib.deck_lightning_demo(), decklib.deck_lightning_demo()
        res = run_mode(da, db, "argmax", BaselineAI("b"), 0, n=2, seed=1,
                       device="cpu", policy=None)
        self.assertEqual(res["n"], 2)
        self.assertGreater(res["lat_ms"]["steps"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
