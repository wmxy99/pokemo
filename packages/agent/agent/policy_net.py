# -*- coding: utf-8 -*-
"""PokéLab Agent · 小策略网络（PyTorch，P2 RL 管线起点）

架构：共享躯干(state → ctx) + 动作打分头(state·ctx ⊕ action → logit) + 价值头。
  - 输入：状态向量 state_vec + 动作特征 action_feats
  - 输出：每候选动作 logits（softmax 得先验分布）+ 状态价值 scalar
v0.1 说明：本文件提供**可训练结构与推理路径**；权重由后续自对弈 RL 管线产出。
未训练时策略先验 ≈ 弱随机（demo/测试用）；MCTS 会依赖该先验加速收敛。
"""
from __future__ import annotations
import torch
import torch.nn as nn
import torch.nn.functional as F


class SmallPolicyValueNet(nn.Module):
    """小策略-价值网络（MLP，可 CPU/GPU 运行）"""

    def __init__(self, state_dim: int, action_dim: int,
                 hidden: int = 64, value_head: bool = True):
        super().__init__()
        self.state_dim = state_dim
        self.action_dim = action_dim
        self.ctx = nn.Sequential(
            nn.Linear(state_dim, hidden),
            nn.ReLU(),
            nn.Linear(hidden, hidden // 2),
            nn.ReLU(),
        )
        self.action_enc = nn.Sequential(
            nn.Linear(action_dim, hidden // 2),
            nn.ReLU(),
        )
        # 打分：ctx(h/2) ⊕ act(h/2) → 1
        self.act_head = nn.Linear(hidden, 1)
        self.value_head = nn.Linear(hidden // 2, 1) if value_head else None

    def forward(self, state: torch.Tensor, act_feats: torch.Tensor) -> tuple:
        """state: (B,S) · act_feats: (B,A,D) → (logits(B,A), value(B))

        采用共享躯干 + 每候选拼接打分：A 随当前合法动作数变化。
        """
        ctx = self.ctx(state)                       # (B,H2)
        n_act = act_feats.shape[1]
        ctx_r = ctx.unsqueeze(1).expand(-1, n_act, -1)
        act = self.action_enc(act_feats)            # (B,A,H2)
        score = self.act_head(torch.cat([ctx_r, act], dim=-1)).squeeze(-1)
        value = None
        if self.value_head is not None:
            value = self.value_head(ctx).squeeze(-1)
        return score, value

    @torch.no_grad()
    def action_priors(self, state: torch.Tensor, act_feats: torch.Tensor,
                      temperature: float = 1.0) -> torch.Tensor:
        """候选动作先验概率（softmax over logits）"""
        logits, _ = self.forward(state, act_feats)
        return F.softmax(logits / max(temperature, 1e-6), dim=-1)

    @torch.no_grad()
    def evaluate_state(self, state: torch.Tensor) -> torch.Tensor:
        if self.value_head is None:
            return torch.zeros(state.shape[0])
        _, value = self.forward(state,
                                torch.zeros(state.shape[0], 1,
                                            self.action_dim))
        return value


def make_net(state_dim: int, action_dim: int, hidden: int = 64,
             device: str = "cpu") -> SmallPolicyValueNet:
    net = SmallPolicyValueNet(state_dim, action_dim, hidden=hidden)
    net = net.to(device)
    net.eval()
    return net
