# -*- coding: utf-8 -*-
"""可选层：「观测 / 动作 → torch 张量」适配（复用 packages/agent 的编码器）

⚠ **这是可选的旁路模块，不在核心路径上。**
   `pokegym.env` 是零依赖的（仅 engine）；只有显式导入本模块才会经
   `agent.state_encoder` 引入 torch。这样「零依赖的教学环境」与「需要张量的
   RL 训练」互不污染：不想装 torch 的人完全不受影响。

    sys.path 需要同时包含 `packages/engine`、`packages/gym`、`packages/agent`。

用法：
    from pokegym import PokelabEnv
    from pokegym.torch_obs import TensorEnv

    env = TensorEnv(PokelabEnv("lightning", "fire", seed=7))
    obs, info = env.reset()                 # obs: FloatTensor[1, state_dim]
    mask = env.action_mask()                # BoolTensor[n_legal]
    obs, r, term, trunc, info = env.step(0)
"""
from __future__ import annotations

from typing import Optional, Tuple


def _encoder():
    """惰性取到 agent 的状态编码器（首次调用才 import torch）"""
    try:
        from agent import state_encoder as se
    except Exception as exc:                                  # noqa: BLE001
        raise ImportError(
            "张量适配需要 packages/agent 在 sys.path 上且已安装 torch"
            "（核心 pokegym.env 不需要）") from exc
    return se


def state_dim(cfg=None) -> int:
    """状态特征维度（51 起，随 cfg.features 增加）"""
    return _encoder().encoder_dim(cfg)


def action_dim(cfg=None) -> int:
    """动作特征维度（basic 14 / rich 31）—— 构网必须用它，勿用常量"""
    return _encoder().action_dim(cfg)


def encode_obs(obs: dict, cfg=None):
    """view → FloatTensor[1, state_dim]"""
    import torch
    vec = _encoder().encode_state(obs, cfg)
    return torch.tensor([vec], dtype=torch.float32)


def encode_actions(obs: dict, cfg=None):
    """view 里的合法动作 → (动作特征 FloatTensor[n, action_dim], n)

    变长离散空间：n = 当前合法动作数。策略网对 n 个动作打分后取 argmax。
    """
    import torch
    se = _encoder()
    acts = se.legal_actions_from_view(obs)
    vecs = [se.encode_action(obs, a, cfg) for a in acts]
    return torch.tensor(vecs, dtype=torch.float32), len(acts)


class TensorEnv:
    """在 :class:`PokelabEnv` 之上包一层：观测/动作掩码都是张量。

    只做「编码」，不改环境语义（奖励、终止、种子确定性都与原环境一致）。
    """

    def __init__(self, env, cfg=None):
        self.env = env
        self.cfg = cfg

    # ---- 便捷透传（与 PokelabEnv 同形：方法而非属性）----
    def legal_actions(self):
        return self.env.legal_actions()

    def __getattr__(self, name):                 # 其余属性透传给内部环境
        return getattr(self.env, name)

    # ---- 张量接口 ----
    def reset(self, seed: Optional[int] = None):
        obs, info = self.env.reset(seed)
        return encode_obs(obs, self.cfg), info

    def step(self, index: int) -> Tuple[object, float, bool, bool, dict]:
        obs, reward, terminated, truncated, info = self.env.step(index)
        return encode_obs(obs, self.cfg), reward, terminated, truncated, info

    def action_tensor(self, obs: dict):
        """当前合法动作的动作特征张量（在 step 之前调用，用同一个 obs）"""
        return encode_actions(obs, self.cfg)

    def action_mask(self):
        """当前合法动作掩码（全 True；变长空间的长度即合法动作数）"""
        import torch
        n = len(self.env.legal_actions())
        return torch.ones(max(1, n), dtype=torch.bool)
