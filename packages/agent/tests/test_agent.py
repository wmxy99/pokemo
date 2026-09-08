# -*- coding: utf-8 -*-
"""PokéLab Agent · 决策链路用例（encoder / policy / MCTS / pipeline / prompts）

运行：cd packages/agent && python -m unittest discover -s tests -v
"""
from __future__ import annotations
import sys
import unittest
from pathlib import Path

# 注入同级 engine（monorepo 未装包时的本地运行方式）
_ENGINE = Path(__file__).resolve().parents[3] / "packages" / "engine"
sys.path.insert(0, str(_ENGINE))

from engine import decklib
from engine.battle import Battle

from agent.state_encoder import (encode_state, encode_action, STATE_DIM,
                                 ACTION_DIM, legal_actions_from_view)
from agent.policy_net import SmallPolicyValueNet
from agent.mcts import mcts_select_action, mcts_with_stats
from agent.pipeline import decide, build_langgraph_pipeline, decide_langgraph
from agent import prompts


def _battle(seed: int = 1) -> Battle:
    return Battle(decklib.deck_lightning_demo(), decklib.deck_fire_demo(),
                  seed=seed)


class TestStateEncoder(unittest.TestCase):
    def test_state_dim_stable(self):
        b = _battle()
        v = b.view()
        s = encode_state(v)
        self.assertEqual(len(s), STATE_DIM)
        self.assertGreater(STATE_DIM, 50)      # 定长且信息量足够
        self.assertTrue(all(isinstance(x, float) for x in s))

    def test_action_dim_stable(self):
        for verb in ("play", "attach", "attack", "retreat", "evolve",
                     "trainer", "pass"):
            a = encode_action({}, {"verb": verb})
            self.assertEqual(len(a), ACTION_DIM)

    def test_legal_proxy(self):
        b = _battle()
        v = b.view()
        self.assertEqual(legal_actions_from_view(v), v["legal"])


class TestPolicyNet(unittest.TestCase):
    def test_forward_shapes(self):
        import torch
        net = SmallPolicyValueNet(STATE_DIM, ACTION_DIM)
        B, A = 2, 6
        state = torch.randn(B, STATE_DIM)
        feats = torch.randn(B, A, ACTION_DIM)
        logits, value = net(state, feats)
        self.assertEqual(logits.shape, (B, A))
        self.assertEqual(value.shape, (B,))

    def test_priors_are_prob(self):
        import torch
        net = SmallPolicyValueNet(STATE_DIM, ACTION_DIM)
        state = torch.randn(1, STATE_DIM)
        feats = torch.randn(1, 4, ACTION_DIM)
        p = net.action_priors(state, feats)
        self.assertAlmostEqual(p.sum().item(), 1.0, places=4)

    def test_torch_cuda_if_available(self):
        import torch
        if torch.cuda.is_available():
            net = SmallPolicyValueNet(STATE_DIM, ACTION_DIM).cuda()
            self.assertEqual(next(net.parameters()).device.type, "cuda")
        self.assertTrue(True)   # 环境无关：无卡时跳过


class TestMCTS(unittest.TestCase):
    def test_returns_legal_action(self):
        b = _battle(seed=7)
        legal = b.legal_actions()
        act = mcts_select_action(b, iters=6, seed=7)
        self.assertIn(act, legal)

    def test_deterministic_same_seed(self):
        a1 = mcts_select_action(_battle(seed=9), iters=8, seed=99)
        a2 = mcts_select_action(_battle(seed=9), iters=8, seed=99)
        self.assertEqual(a1, a2)

    def test_stats_report(self):
        b = _battle(seed=3)
        act, stats = mcts_with_stats(b, iters=5, seed=3)
        self.assertIn(act, b.legal_actions())
        self.assertGreaterEqual(len(stats["candidates"]), 1)


class TestPipeline(unittest.TestCase):
    def test_decide_pure(self):
        b = _battle(seed=11)
        out = decide(b, iters=4, seed=11)
        self.assertIn(out["action"], b.legal_actions())
        self.assertEqual(len(out["state_vec"]), STATE_DIM)
        self.assertTrue(out["audit"])

    def test_langgraph_matches_pure(self):
        try:
            build_langgraph_pipeline()      # 探测是否安装
        except ImportError:
            self.skipTest("langgraph 未安装")
        b = _battle(seed=13)
        pure = decide(b, iters=4, seed=13)["action"]
        lg = decide_langgraph(_battle(seed=13), iters=4, seed=13)["action"]
        self.assertEqual(pure, lg)


class TestPrompts(unittest.TestCase):
    def test_templates_exist(self):
        names = prompts.templates()
        self.assertIn("parse_effects_system", names)
        self.assertIn("parse_effects_user", names)
        self.assertIn("commentator_system", names)

    def test_render_replaces_tokens(self):
        out = prompts.render(
            "parse_effects_user", card_name="Dragapult ex",
            series="TWM", number="130", supertype="Pokemon", hp=330,
            rules_text="200 伤害…", attacks_meta="")
        self.assertIn("Dragapult ex", out)
        self.assertNotIn("@@card_name@@", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
