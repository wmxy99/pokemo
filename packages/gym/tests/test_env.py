# -*- coding: utf-8 -*-
"""PokéLab 博弈环境测试。运行：cd packages/gym && python -m unittest discover -s tests"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
for pkg in (REPO / "packages" / "engine", REPO / "packages" / "gym"):
    if str(pkg) not in sys.path:
        sys.path.insert(0, str(pkg))

from pokegym import PokelabEnv, EnvConfig, run_episode  # noqa: E402


class TestPokelabEnv(unittest.TestCase):
    def test_reset_returns_player_perspective(self):
        env = PokelabEnv("lightning", "fire", seed=7)
        obs, info = env.reset()
        self.assertEqual(obs["me"], env.cfg.side)
        self.assertIn("legal", obs)
        self.assertIn("hand", obs["me_state"])
        self.assertNotIn("hand", obs["opp_state"])   # 对手手牌不可见
        self.assertEqual(info["finished"], False)

    def test_deterministic_seed(self):
        a = PokelabEnv("lightning", "fire", seed=123)
        b = PokelabEnv("lightning", "fire", seed=123)
        oa, _ = a.reset()
        ob, _ = b.reset()
        self.assertEqual(oa["me_state"]["hand"], ob["me_state"]["hand"])
        self.assertEqual([x["verb"] for x in oa["legal"]],
                         [x["verb"] for x in ob["legal"]])

    def test_action_by_index_and_dict_equivalent(self):
        """同一个动作，用下标与用 dict 提交，结果一致。"""
        def play_one(use_index):
            env = PokelabEnv("lightning", "fire", seed=7)
            obs, _ = env.reset()
            acts = env.legal_actions()
            if not acts:
                return obs
            a = acts[0]
            if use_index:
                env.step(0)
            else:
                env.step(a)
            return env.observation()
        self.assertEqual(play_one(True), play_one(False))

    def test_illegal_action_rejected(self):
        env = PokelabEnv("lightning", "fire", seed=7)
        env.reset()
        with self.assertRaises(ValueError):
            env.step({"verb": "not_a_verb"})
        with self.assertRaises(IndexError):
            env.step(9999)

    def test_episode_terminates_with_reward_sign(self):
        env = PokelabEnv("lightning", "fire", seed=3)
        info = run_episode(env, seed=3)
        self.assertTrue(info["finished"])
        self.assertIn(info["reward"], (-1.0, 0.0, 1.0))
        self.assertIn(info["winner"], (0, 1, None))

    def test_perspective_never_leaks(self):
        """与 API 层同一条不变量：观测永远是玩家视角。"""
        env = PokelabEnv("lightning", "fire", seed=11)
        obs, _ = env.reset()
        guard = 0
        while guard < 400:
            guard += 1
            self.assertEqual(obs["me"], env.cfg.side, "观测视角偏移")
            self.assertNotIn("hand", obs["opp_state"], "对手手牌泄露")
            _, r, term, trunc, _ = env.step(env.sample_action_index())
            if term or trunc:
                break
            obs = env.observation()

    def test_custom_cfg(self):
        cfg = EnvConfig(side=1, opponent="random", reward_win=3.0,
                        reward_loss=-2.0)
        env = PokelabEnv("fire", "lightning", cfg=cfg)
        obs, _ = env.reset()
        self.assertEqual(obs["me"], 1)
        self.assertEqual(env.cfg.reward_win, 3.0)


if __name__ == "__main__":
    unittest.main()
