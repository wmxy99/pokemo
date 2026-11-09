# -*- coding: utf-8 -*-
"""PokéLab Agent · DQN 对照训练（vs 固定对手，MC 目标）

技术方案：PPO 主选 / DQN 对照。本实现为轻量价值型对照：
  - 网络复用 SmallPolicyValueNet：Q(s, a_candidate) = act_head(ctx ⊕ act)（掩码后取 argmax）
  - 收集：ε-greedy（ε 衰减）vs 固定对手（BaselineAI/Random），只记 agent0 步
  - 目标：单侧 MC 回报（同 PPO vs-fixed），Q 回归 MSE；每 target_update 局软/硬刷新目标网
  - 产出：episode 胜率曲线 + 权重保存（可被 eval_matrix 直接评测对照）
"""
from __future__ import annotations
import copy
import random
from pathlib import Path
from typing import Optional

import torch
import torch.nn.functional as F

from .state_encoder import encode_state, encode_action
from .policy_agent import build_spread_plan
from .policy_net import SmallPolicyValueNet
from . import reward as rw


def _pad(frames, device: str):
    max_a = max(len(f["feats_all"]) for f in frames)
    d = len(frames[0]["feats_all"][0])
    S = torch.tensor([f["state_vec"] for f in frames], dtype=torch.float32,
                     device=device)
    FA = torch.zeros(len(frames), max_a, d, dtype=torch.float32, device=device)
    MASK = torch.ones(len(frames), max_a, dtype=torch.bool, device=device)
    C = torch.zeros(len(frames), dtype=torch.long, device=device)
    R = torch.zeros(len(frames), dtype=torch.float32, device=device)
    for i, f in enumerate(frames):
        n = len(f["feats_all"])
        FA[i, :n] = torch.tensor(f["feats_all"], dtype=torch.float32,
                                 device=device)
        MASK[i, :n] = False
        C[i] = f["chosen"]
        R[i] = f["ret"]
    return S, FA, MASK, C, R


def _choose(net, view, legal, device, eps, rng):
    """ε-greedy 依 Q 选动作（chosen index + action dict）"""
    if len(legal) == 1:
        return 0, dict(legal[0])
    if rng.random() < eps:
        idx = rng.randrange(len(legal))
        return idx, dict(legal[idx])
    S = torch.tensor([encode_state(view)], dtype=torch.float32, device=device)
    FA = torch.tensor([[encode_action(view, a) for a in legal]],
                      dtype=torch.float32, device=device)
    with torch.no_grad():
        q, _ = net.forward(S, FA)                 # (1, len(legal))
    idx = int(q[0].argmax().item())
    return idx, build_spread_plan(view, dict(legal[idx]))


def train_dqn_vs_fixed(deck_a: list, deck_b: list, net: SmallPolicyValueNet,
                       opponent, episodes: int = 150, lr: float = 1e-3,
                       gamma: float = 0.99, eps: float = 0.3,
                       eps_min: float = 0.05, eps_decay: float = 0.995,
                       shaping: float = 0.0, target_update: int = 10,
                       seed: Optional[int] = None, device: str = "cpu",
                       log_every: int = 20,
                       save_path: Optional[str] = None) -> dict:
    from .trainer import _fresh_battle
    rng = random.Random(seed)
    opt = torch.optim.Adam(net.parameters(), lr=lr)
    target = copy.deepcopy(net)          # 目标网（硬更新）
    target.eval()
    stats = {"episodes": 0, "wins": 0, "losses": 0, "draws": 0,
             "q_loss": [], "win_tail": []}
    e = eps
    for ep in range(episodes):
        e = max(eps_min, e * eps_decay)
        battle = _fresh_battle(deck_a, deck_b, seed=rng.randrange(1 << 30))
        net.eval()
        frames = []
        guard = 0
        while not battle.finished and guard < 500:
            guard += 1
            if battle.acting_side() == 0:
                view = battle.view(0)
                legal = view.get("legal") or [{"verb": "pass"}]
                idx, act = _choose(net, view, legal, device, e, rng)
                frames.append({
                    "state_vec": encode_state(view),
                    "feats_all": [encode_action(view, a) for a in legal],
                    "chosen": idx,
                    "r": shaping * rw.state_reward(view),
                })
                res = battle.step(copy.deepcopy(act))
            else:
                res = battle.step(copy.deepcopy(opponent.decide(battle.view())))
            if not res["ok"]:
                battle.step({"verb": "pass"})
        if not frames:
            continue
        # MC 目标（单侧）
        term = 1.0 if battle.winner == 0 else (-1.0 if battle.winner is not None
                                               else 0.0)
        g = 0.0
        for f in reversed(frames):
            g = f["r"] + (term if f is frames[-1] else 0.0) + gamma * g
            f["ret"] = g
        S, FA, MASK, C, R = _pad(frames, device)
        net.train()
        q_all, _ = net.forward(S, FA)
        q_all = q_all.masked_fill(MASK, -1e9)
        q_chosen = q_all.gather(1, C.unsqueeze(1)).squeeze(1)
        loss = F.mse_loss(q_chosen, R)
        opt.zero_grad()
        loss.backward()
        opt.step()
        net.eval()
        stats["episodes"] += 1
        stats["q_loss"].append(loss.item())
        if battle.winner == 0:
            stats["wins"] += 1
        elif battle.winner is None:
            stats["draws"] += 1
        else:
            stats["losses"] += 1
        if (ep + 1) % target_update == 0:
            target.load_state_dict(net.state_dict())
        if log_every and (ep + 1) % log_every == 0:
            cur = stats["wins"] / max(1, stats["episodes"])
            stats["win_tail"].append(cur)
            print(f"[dqn] ep {ep + 1}/{episodes} wins={stats['wins']}"
                  f"/{stats['episodes']} win={cur:.2f} "
                  f"q={loss.item():.4f} eps={e:.2f}")
    if save_path:
        Path(save_path).parent.mkdir(parents=True, exist_ok=True)
        torch.save(net.state_dict(), save_path)
        stats["save_path"] = save_path
    return stats
