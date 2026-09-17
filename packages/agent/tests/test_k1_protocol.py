# -*- coding: utf-8 -*-
"""K1 评估口径用例 —— 双盲 vs 单侧（防止口径误用）

背景：K1 曾长期用**单侧口径**（策略恒为先手），而本引擎先手优势约 6pp，
于是「与基线打平的策略」也会显示 ~0.61，**系统性高估**。PRD 要的是 200 局**双盲**。
本组用例把「口径不能被混用」变成可执行断言。

运行：cd packages/agent && python -m unittest tests.test_k1_protocol -v
"""
from __future__ import annotations
import sys
import unittest
from pathlib import Path

_ENGINE = Path(__file__).resolve().parents[2] / "engine"
sys.path.insert(0, str(_ENGINE))

from engine import decklib                                    # noqa: E402
from agent import trainer                                     # noqa: E402
from agent.policy_net import make_net                         # noqa: E402
from agent.state_encoder import ACTION_DIM, STATE_DIM         # noqa: E402


class TestK1Protocol(unittest.TestCase):
    def setUp(self):
        self.da = decklib.make_deck("lightning")
        self.db = list(self.da)
        # 未训练网络即可 —— 本组只验证**口径与统计逻辑**，不验证棋力
        self.net = make_net(STATE_DIM, ACTION_DIM, hidden=32, device="cpu")

    def test_blind_alternates_seats(self):
        """双盲必须**两侧都打**（n/2 先手 + n/2 后手）"""
        r = trainer.eval_winrate_blind(self.da, self.db, self.net, n=8,
                                       device="cpu", seed0=0)
        self.assertEqual(r["as_first"], 4)
        self.assertEqual(r["as_second"], 4)
        self.assertTrue(r["blind"])
        self.assertEqual(r["wins"] + r["losses"] + r["draws"], 8)

    def test_blind_gives_reference_line(self):
        """必须给出**基线自对局先手参照** —— 否则无法判断「0.61 是不是只是先手优势」"""
        r = trainer.eval_winrate_blind(self.da, self.db, self.net, n=8,
                                       device="cpu", seed0=0)
        self.assertIn("ref_first_winrate", r)
        self.assertGreater(r["ref_n"], 0)
        self.assertGreaterEqual(r["ref_first_winrate"], 0.0)
        self.assertLessEqual(r["ref_first_winrate"], 1.0)

    def test_blind_and_one_sided_are_distinguishable(self):
        """两个口径的返回值必须能区分（避免下游把单侧当 K1 用）"""
        blind = trainer.eval_winrate_blind(self.da, self.db, self.net, n=8,
                                           device="cpu", seed0=0)
        one = trainer.eval_winrate(self.da, self.db, self.net, n=8,
                                   device="cpu")
        self.assertIn("blind", blind)
        self.assertNotIn("blind", one)          # 单侧不带 blind 标记
        self.assertIn("win_rate", blind)
        self.assertIn("win_rate", one)

    def test_alternate_sides_training_runs_both_seats(self):
        """训练侧：`alternate_sides=True` 时策略也要学会打后手

        判据：整段训练里，**后手局的占比**应接近一半（用 win_tail 无法直接观测座位，
        故这里直接跑极小样本，验证不报错且胜负有记录）。
        """
        st = trainer.train_ppo_vs_fixed(
            self.da, self.db, self.net, opponent=None or __import__(
                "engine.agents", fromlist=["BaselineAI"]).BaselineAI("b"),
            episodes=4, ppo_epochs=1, lr=1e-4, seed=0, device="cpu",
            log_every=0, alternate_sides=True)
        self.assertTrue(st["alternate_sides"])
        self.assertEqual(st["episodes"], 4)
        self.assertEqual(st["wins"] + st["losses"] + st["draws"], 4)


class TestLoadNetInfersWidth(unittest.TestCase):
    """`load_net` 必须能从 checkpoint 推断宽度（否则非默认宽度的权重加载即报错）"""

    def test_infer_hidden_from_checkpoint(self):
        import tempfile
        import torch
        from agent.trainer import load_net
        net = make_net(STATE_DIM, ACTION_DIM, hidden=48, device="cpu")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "w.pt"
            torch.save(net.state_dict(), p)
            got = load_net(str(p), device="cpu")
            self.assertEqual(int(got.ctx[0].out_features), 48)


if __name__ == "__main__":
    unittest.main(verbosity=2)
