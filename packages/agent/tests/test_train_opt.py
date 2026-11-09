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
                                 EncoderConfig, encoder_dim, action_dim,
                                 set_active_encoder, active_encoder)
from agent.policy_net import SmallPolicyValueNet  # noqa: E402
from agent import reward as rw  # noqa: E402
from agent import dqn, trainer, distill, bc  # noqa: E402


class TestActiveEncoder(unittest.TestCase):
    def tearDown(self):
        set_active_encoder(None)

    def test_full_active_pipeline(self):
        from agent.state_encoder import ACTION_DIM
        from agent.policy_net import make_net
        set_active_encoder(EncoderConfig(features=("hand", "meta")))
        b = Battle(decklib.deck_lightning_demo(),
                   decklib.deck_fire_demo(), seed=4)
        v = b.view()
        self.assertEqual(encoder_dim(), STATE_DIM + 8)
        self.assertEqual(len(encode_state(v)), encoder_dim())
        net = make_net(encoder_dim(), ACTION_DIM)
        st = torch.randn(1, encoder_dim())
        fa = torch.randn(1, 3, ACTION_DIM)
        self.assertEqual(net(st, fa)[0].shape, (1, 3))
        set_active_encoder(None)
        self.assertIsNone(active_encoder())
        self.assertEqual(len(encode_state(v)), STATE_DIM)


class TestMctsDistill(unittest.TestCase):
    def test_collect_hard_and_train(self):
        from engine.agents import BaselineAI
        from agent.state_encoder import ACTION_DIM
        from agent.policy_net import make_net
        da, db = decklib.deck_lightning_demo(), decklib.deck_lightning_demo()
        frames = distill.collect_mcts_expert(da, db,
                                             opponent=BaselineAI("b"),
                                             episodes=2, iters=3, seed=1,
                                             soft=False)
        self.assertGreater(len(frames), 10)
        self.assertNotIn("probs", frames[0])
        net = make_net(STATE_DIM, ACTION_DIM)
        st = bc.train_bc(net, frames, epochs=2, lr=1e-3, seed=1)
        self.assertLess(st["loss"][-1], st["loss"][0])

    def test_collect_soft_and_train_soft(self):
        from engine.agents import BaselineAI
        from agent.state_encoder import ACTION_DIM
        from agent.policy_net import make_net
        da, db = decklib.deck_lightning_demo(), decklib.deck_lightning_demo()
        frames = distill.collect_mcts_expert(da, db,
                                             opponent=BaselineAI("b"),
                                             episodes=2, iters=3, seed=2,
                                             soft=True)
        self.assertGreater(len(frames), 10)
        self.assertIn("probs", frames[0])
        self.assertAlmostEqual(sum(frames[0]["probs"]), 1.0, places=5)
        net = make_net(STATE_DIM, ACTION_DIM)
        st = bc.train_bc_soft(net, frames, epochs=2, lr=1e-3, seed=1)
        self.assertLess(st["loss"][-1], st["loss"][0] + 1e-9)


class TestMctsVisits(unittest.TestCase):
    def test_policy_probs_aligned(self):
        from agent.mcts import mcts_policy_probs
        b = Battle(decklib.deck_lightning_demo(),
                   decklib.deck_lightning_demo(), seed=6)
        b.players[b.current].hand = b.players[b.current].hand or []
        legal = b.legal_actions()
        if len(legal) > 1:
            probs, visits, _ = mcts_policy_probs(b, iters=5, seed=1)
            self.assertEqual(len(probs), len(legal))
            self.assertEqual(len(visits), len(legal))
            self.assertAlmostEqual(sum(probs), 1.0, places=6)


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

    def test_cosine_lr_schedule(self):
        from engine.agents import BaselineAI
        from agent.state_encoder import ACTION_DIM
        from agent.policy_net import make_net
        da, db = decklib.deck_lightning_demo(), decklib.deck_lightning_demo()
        net = make_net(STATE_DIM, ACTION_DIM)
        st = trainer.train_ppo_vs_fixed(da, db, net,
                                        opponent=BaselineAI("b"),
                                        episodes=2, lr=1e-3, lr_decay=0.1,
                                        lr_schedule="cosine", seed=3)
        self.assertEqual(st["episodes"], 2)

    def test_full_features_dim(self):
        b = Battle(decklib.deck_lightning_demo(),
                   decklib.deck_fire_demo(), seed=2)
        cfg = EncoderConfig(features=("hand", "meta"))
        s = encode_state(b.view(), cfg)
        self.assertEqual(len(s), encoder_dim(cfg))
        self.assertEqual(encoder_dim(cfg), STATE_DIM + 8)
        # full-dim 网络可前向（动作维度**取实际值**：S1b 之后是 16，不是 14）
        net = SmallPolicyValueNet(encoder_dim(cfg), action_dim(cfg))
        st = torch.randn(1, encoder_dim(cfg))
        fa = torch.randn(1, 4, action_dim(cfg))
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
        net = SmallPolicyValueNet(STATE_DIM, action_dim())
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
