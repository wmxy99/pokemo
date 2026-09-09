# -*- coding: utf-8 -*-
"""PokéLab Agent · BC 预热（行为克隆）

思路：让策略网络先**模仿规则基线 AI（BaselineAI）的决策**，把"均匀先验"预热成
"从合理动作开始的先验"，再进 PPO 自对弈（比从零 RL 收敛快得多）。

- collect_demonstration：多局 基线AI vs 基线AI/随机，逐帧记录
  (state_vec, feats_all, chosen_idx) —— 与 trainer.collect_episode 同构，
  保证 BC→PPO 无缝衔接。
- train_bc：对每步在"该步全部合法候选"上做 masked softmax CE（padding 位屏蔽）。
- 权重经 train_bc 保存后可直接传给 train_ppo / PolicyAgent。
"""
from __future__ import annotations
import copy
import random
from pathlib import Path
from typing import Optional

import torch
import torch.nn.functional as F

from .state_encoder import encode_state, encode_action
from .policy_net import SmallPolicyValueNet


def collect_demonstration(deck_a: list, deck_b: list, agent_a, agent_b,
                          episodes: int = 40, seed: Optional[int] = None,
                          max_steps: int = 500) -> list:
    """采集 基线AI 对局演示数据（每帧含全候选 feats，供 masked CE 训练）"""
    from engine.battle import Battle
    rng = random.Random(seed)
    frames = []
    for _ in range(episodes):
        b = Battle(deck_a, deck_b, seed=rng.randrange(1 << 30))
        guard = 0
        while not b.finished and guard < max_steps:
            guard += 1
            view = b.view()
            legal = view.get("legal") or [{"verb": "pass"}]
            if not legal:
                break
            agent = agent_a if b.current == 0 else agent_b
            act = agent.decide(view)
            # chosen 必须在 legal 中（基线 AI 应返回合法动作；异常回退 pass）
            try:
                chosen = next(i for i, a in enumerate(legal) if a == act)
            except StopIteration:
                act = {"verb": "pass"}
                chosen = next(i for i, a in enumerate(legal) if a == act)
            frames.append({
                "state_vec": encode_state(view),
                "feats_all": [encode_action(view, a) for a in legal],
                "chosen": chosen,
            })
            res = b.step(copy.deepcopy(act))
            if not res["ok"]:
                b.step({"verb": "pass"})
    return frames


def _pad(frames: list, device: str):
    max_a = max(len(f["feats_all"]) for f in frames)
    d = len(frames[0]["feats_all"][0])
    S = torch.tensor([f["state_vec"] for f in frames], dtype=torch.float32,
                     device=device)
    FA = torch.zeros(len(frames), max_a, d, dtype=torch.float32, device=device)
    MASK = torch.ones(len(frames), max_a, dtype=torch.bool, device=device)
    C = torch.zeros(len(frames), dtype=torch.long, device=device)
    for i, f in enumerate(frames):
        n = len(f["feats_all"])
        FA[i, :n] = torch.tensor(f["feats_all"], dtype=torch.float32,
                                 device=device)
        MASK[i, :n] = False
        C[i] = f["chosen"]
    return S, FA, MASK, C


def train_bc(net: SmallPolicyValueNet, frames: list, epochs: int = 10,
             lr: float = 1e-3, batch: int = 256, device: str = "cpu",
             seed: Optional[int] = None) -> dict:
    """掩码 softmax CE 预训练；返回 loss/acc 轨迹"""
    S, FA, MASK, C = _pad(frames, device)
    opt = torch.optim.Adam(net.parameters(), lr=lr)
    rng = random.Random(seed)
    n = len(frames)
    loss_track, acc_track = [], []
    for ep in range(epochs):
        net.train()
        order = list(range(n))
        rng.shuffle(order)
        ep_loss = 0.0
        ep_correct = 0
        ep_total = 0
        for start in range(0, n, batch):
            idx = order[start:start + batch]
            sb = S[idx]
            fb = FA[idx]
            mb = MASK[idx]
            cb = C[idx]
            logits, _ = net.forward(sb, fb)
            logits = logits.masked_fill(mb, -1e9)
            loss = F.cross_entropy(logits, cb)
            opt.zero_grad()
            loss.backward()
            opt.step()
            ep_loss += loss.item() * len(idx)
            pred = logits.argmax(-1)
            ep_correct += (pred == cb).sum().item()
            ep_total += len(idx)
        loss_track.append(ep_loss / max(1, ep_total))
        acc_track.append(ep_correct / max(1, ep_total))
    return {"loss": loss_track, "acc": acc_track, "frames": n,
            "final_acc": acc_track[-1] if acc_track else None}


def train_bc_soft(net: SmallPolicyValueNet, frames: list, epochs: int = 10,
                  lr: float = 1e-3, batch: int = 256, device: str = "cpu",
                  seed: Optional[int] = None) -> dict:
    """soft 目标蒸馏（MCTS visits 归一化作教师信号）。

    frames 每项含 probs（与 feats_all 索引对齐，和为 1）；
    loss = -Σ p_teacher · log p_model（掩码只作用于合法候选，padding 位不计）。
    """
    max_a = max(len(f["feats_all"]) for f in frames)
    d = len(frames[0]["feats_all"][0])
    S = torch.tensor([f["state_vec"] for f in frames], dtype=torch.float32,
                     device=device)
    FA = torch.zeros(len(frames), max_a, d, dtype=torch.float32, device=device)
    MASK = torch.ones(len(frames), max_a, dtype=torch.bool, device=device)
    TG = torch.zeros(len(frames), max_a, dtype=torch.float32, device=device)
    for i, f in enumerate(frames):
        n = len(f["feats_all"])
        FA[i, :n] = torch.tensor(f["feats_all"], dtype=torch.float32,
                                 device=device)
        MASK[i, :n] = False
        TG[i, :n] = torch.tensor(f["probs"], dtype=torch.float32,
                                 device=device)
    opt = torch.optim.Adam(net.parameters(), lr=lr)
    rng = random.Random(seed)
    n = len(frames)
    order_all = list(range(n))
    rng.shuffle(order_all)
    loss_track = []
    for ep in range(epochs):
        net.train()
        order = list(order_all)
        rng.shuffle(order)
        ep_loss = 0.0
        for start in range(0, n, batch):
            idx = order[start:start + batch]
            logits, _ = net.forward(S[idx], FA[idx])
            logits = logits.masked_fill(MASK[idx], -1e9)  # 排除 padding 归一化
            logp = F.log_softmax(logits, dim=-1).masked_fill(
                MASK[idx], 0.0)              # padding 位贡献为 0
            loss = -(TG[idx] * logp).sum(-1).mean()
            opt.zero_grad()
            loss.backward()
            opt.step()
            ep_loss += loss.item() * len(idx)
        loss_track.append(ep_loss / max(1, n))
    return {"loss": loss_track, "frames": n,
            "final_loss": loss_track[-1] if loss_track else None}


def save_frames(frames: list, path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    torch.save(frames, path)


def load_frames(path: str) -> list:
    return torch.load(path)
