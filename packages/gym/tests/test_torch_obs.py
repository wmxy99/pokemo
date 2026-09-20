# -*- coding: utf-8 -*-
"""张量适配层测试（**需要 torch + packages/agent**，缺失则整体跳过）

核心环境 pokegym.env 不依赖 torch —— 这里同时守住这一点：
导入 pokegym 后 sys.modules 里不应出现 torch。
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
for pkg in ("engine", "gym", "agent"):
    p = REPO / "packages" / pkg
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from pokegym import PokelabEnv  # noqa: E402

_HAS_TORCH = importlib.util.find_spec("torch") is not None
_HAS_AGENT = (REPO / "packages" / "agent" / "agent" / "state_encoder.py").exists()


class TestCoreStaysZeroDep(unittest.TestCase):
    """核心环境必须保持零依赖（不把 torch 拖进来）

    用**子进程**验证：这样即使本进程后续导入了 torch（张量测试），结论也不受影响。
    """

    def test_importing_pokegym_does_not_import_torch(self):
        import os
        import subprocess
        code = ("import sys; import pokegym; "
                "print('torch' in sys.modules, 'pokegym.torch_obs' in sys.modules)")
        env = {**os.environ,
               "PYTHONPATH": os.pathsep.join([
                   str(REPO / "packages" / "engine"),
                   str(REPO / "packages" / "gym"),
                   str(REPO / "packages" / "agent"),
               ])}
        out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                             text=True, env=env, timeout=120)
        self.assertIn("False False", out.stdout,
                      f"stdout={out.stdout!r} stderr={out.stderr[-400:]!r}")


@unittest.skipUnless(_HAS_TORCH, "需要 torch")
@unittest.skipUnless(_HAS_AGENT, "需要 packages/agent")
class TestTensorObs(unittest.TestCase):
    def _env(self):
        from pokegym.torch_obs import TensorEnv
        return TensorEnv(PokelabEnv("lightning", "fire", opponent="baseline",
                                    seed=7))

    def test_reset_returns_tensor_with_expected_dim(self):
        from pokegym import torch_obs
        env = self._env()
        obs, info = env.reset()
        self.assertEqual(tuple(obs.shape), (1, torch_obs.state_dim(env.cfg)))
        self.assertEqual(obs.dtype.__str__(), "torch.float32")
        self.assertFalse(info["finished"])

    def test_action_tensor_shape_and_mask(self):
        from pokegym import torch_obs
        env = self._env()
        obs, _ = env.reset()
        raw = env.env.observation()
        acts, n = env.action_tensor(raw)
        self.assertEqual(n, len(env.legal_actions()))
        self.assertEqual(tuple(acts.shape), (n, torch_obs.action_dim(env.cfg)))
        self.assertEqual(int(env.action_mask().sum()), max(1, n))

    def test_step_through_tensor_env_terminates(self):
        env = self._env()
        obs, _ = env.reset()
        done = False
        guard = 0
        while not done and guard < 500:
            guard += 1
            obs, reward, terminated, truncated, info = env.step(
                env.env.sample_action_index())
            done = terminated or truncated
        self.assertTrue(info["finished"])
        self.assertEqual(tuple(obs.shape)[0], 1)

    def test_semantics_match_raw_env(self):
        """张量层不改语义：同一 seed + 同一动作序列 → 同一结果"""
        from pokegym.torch_obs import TensorEnv

        def play(wrapped):
            env = TensorEnv(PokelabEnv("lightning", "fire", seed=11)) if wrapped \
                else PokelabEnv("lightning", "fire", seed=11)
            env.reset()
            seq = [0, 0, 1, 0, 2, 0, 0, 1]
            info = {}
            for a in seq:
                if info.get("finished"):
                    break
                n = len(env.legal_actions())
                _, _, term, trunc, info = env.step(min(a, max(0, n - 1)))
                if term or trunc:
                    break
            return info["winner"], info["turn"], info["prizes_left"]

        self.assertEqual(play(False), play(True))


if __name__ == "__main__":
    unittest.main()
