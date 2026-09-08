# -*- coding: utf-8 -*-
"""PokéLab Agent · RL 自对弈训练管线（PPO，v0.1）

目标：让策略网络从"均匀先验"长成会打牌的对手（决策链路转正）。

设定（双玩家零和）：
  1) collect_episode：用当前 net 以 PolicyAgent(采样) 驱动双方完成一局；
     每步记录 (state, **该步全部合法动作 feats**, chosen_idx, old_logp, actor)。
  2) 收益（对偶交替视角）：R_t = r_t - gamma·R_{t+1}；末步 r = winner 视角 ±1（平 0）。
  3) PPO（clip）按 episode 多 epoch：logp_new = log_softmax(全候选 logits)[chosen_idx]，
     adv = R - value（detach），actor/价值/熵三项损失。
  4) 保存 artifacts/policy.pt；eval_winrate vs BaselineAI / Random。
"""
from __future__ import annotations
import copy
import math
import random
from pathlib import Path
from typing import Optional

import torch
import torch.nn.functional as F

from .state_encoder import encode_state, encode_action
from .policy_net import SmallPolicyValueNet


def _fresh_battle(deck_a: list, deck_b: list, seed: int):
    from engine.battle import Battle
    return Battle(deck_a, deck_b, seed=seed)


def collect_episode(battle, net, device="cpu", temperature=1.0,
                    max_steps=500) -> list:
    """自对弈一局（双方共用 net，采样）；返回步记录列表"""
    steps = []
    guard = 0
    while not battle.finished and guard < max_steps:
        guard += 1
        view = battle.view()
        legal = view.get("legal") or [{"verb": "pass"}]
        if not legal:
            break
        state_vec = encode_state(view)
        feats_all = [encode_action(view, a) for a in legal]
        if len(legal) == 1:
            idx, logp = 0, 0.0
        else:
            S = torch.tensor([state_vec], dtype=torch.float32, device=device)
            FA = torch.tensor([feats_all], dtype=torch.float32, device=device)
            with torch.no_grad():
                logits, _ = net.forward(S, FA)          # (1, A)
                probs = F.softmax(logits / temperature, dim=-1)[0]
            probs = probs.clamp(min=1e-9)
            probs = probs / probs.sum()
            idx = int(torch.multinomial(probs, 1).item())
            logp = float(torch.log(probs[idx]).item())
        steps.append({
            "state_vec": state_vec,
            "feats_all": feats_all,
            "chosen": idx,
            "logp": logp,
            "actor": battle.current,
            "action": copy.deepcopy(legal[idx]),
        })
        res = battle.step(copy.deepcopy(legal[idx]))
        if not res["ok"]:
            battle.step({"verb": "pass"})
    return steps


def _returns(steps: list, winner: Optional[int], gamma: float) -> list:
    n = len(steps)
    targets = [0.0] * n
    g = 0.0
    for t in range(n - 1, -1, -1):
        actor = steps[t]["actor"]
        if t == n - 1:
            r = 1.0 if (winner == actor) else (-1.0 if winner is not None else 0.0)
        else:
            r = 0.0
        g = r - gamma * g            # 下一状态视角为对手 → 取负
        targets[t] = g
    return targets


def _to_batch(steps: list, device: str):
    """(state(B,S), feats padded(B,A,D), mask(B,A), chosen(B), old_logp(B), ret(B))"""
    max_a = max(len(s["feats_all"]) for s in steps)
    d_dim = len(steps[0]["feats_all"][0])
    S = torch.tensor([s["state_vec"] for s in steps], dtype=torch.float32,
                     device=device)
    FA = torch.zeros(len(steps), max_a, d_dim, dtype=torch.float32,
                     device=device)
    MASK = torch.ones(len(steps), max_a, dtype=torch.bool, device=device)
    for i, s in enumerate(steps):
        n = len(s["feats_all"])
        FA[i, :n] = torch.tensor(s["feats_all"], dtype=torch.float32,
                                 device=device)
        MASK[i, :n] = False
    C = torch.tensor([s["chosen"] for s in steps], dtype=torch.long,
                     device=device)
    O = torch.tensor([s["logp"] for s in steps], dtype=torch.float32,
                     device=device)
    R = torch.tensor([s["_ret"] for s in steps], dtype=torch.float32,
                     device=device)
    return S, FA, MASK, C, O, R


def _ppo_loss(net, S, FA, MASK, C, O, R, clip=0.2, ent_coef=0.01):
    logits, values = net.forward(S, FA)               # (B,A) / (B)
    logits = logits.masked_fill(MASK, -1e9)           # 屏蔽 padding 候选
    logp = F.log_softmax(logits, dim=-1).gather(1, C.unsqueeze(1)).squeeze(1)
    probs = F.softmax(logits, dim=-1)
    entropy = -(probs * (probs + 1e-9).log()).sum(-1).mean()
    adv = (R - values.detach()).clamp(-3, 3)
    ratio = (logp - O).exp()
    surr1 = ratio * adv
    surr2 = ratio.clamp(1 - clip, 1 + clip) * adv
    actor_loss = -torch.min(surr1, surr2).mean()
    value_loss = F.mse_loss(values, R)
    total = actor_loss + value_loss - ent_coef * entropy
    return total, actor_loss.item(), value_loss.item(), entropy.item()


def train_ppo(deck_a: list, deck_b: list, net: SmallPolicyValueNet,
              episodes: int = 40, ppo_epochs: int = 3, clip: float = 0.2,
              lr: float = 1e-3, gamma: float = 0.99, temperature: float = 1.0,
              seed: Optional[int] = None, device: str = "cpu",
              log_every: int = 10,
              save_path: Optional[str] = None) -> dict:
    rng = random.Random(seed)
    opt = torch.optim.Adam(net.parameters(), lr=lr)
    stats = {"episodes": 0, "agent0_wins": 0, "draws": 0, "loss": [],
             "mean_return": []}
    running = []
    for ep in range(episodes):
        battle = _fresh_battle(deck_a, deck_b, seed=rng.randrange(1 << 30))
        net.eval()
        steps = collect_episode(battle, net, device=device,
                                temperature=temperature)
        if not steps:
            continue
        ret = _returns(steps, battle.winner, gamma)
        for s, r in zip(steps, ret):
            s["_ret"] = r
        net.train()
        for _ in range(ppo_epochs):
            S, FA, MASK, C, O, R = _to_batch(steps, device)
            loss, a_loss, v_loss, ent = _ppo_loss(net, S, FA, MASK, C, O,
                                                  R, clip)
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(net.parameters(), 5.0)
            opt.step()
        net.eval()
        stats["episodes"] += 1
        if battle.winner == 0:
            stats["agent0_wins"] += 1
        elif battle.winner is None:
            stats["draws"] += 1
        stats["loss"].append(a_loss)
        stats["mean_return"].append(float(torch.tensor(ret).mean()))
        if log_every and (ep + 1) % log_every == 0:
            print(f"[ppo] ep {ep + 1}/{episodes} "
                  f"actor={a_loss:.4f} val={v_loss:.4f} ent={ent:.4f}")
    if save_path:
        Path(save_path).parent.mkdir(parents=True, exist_ok=True)
        torch.save(net.state_dict(), save_path)
        stats["save_path"] = save_path
    return stats


def eval_winrate(deck_a: list, deck_b: list, net,
                 n: int = 20, seeds: Optional[range] = None,
                 opponent="baseline", device: str = "cpu") -> dict:
    """评估 policy(agent0) vs 对手（baseline/random）胜率"""
    from engine import decklib as _  # noqa  (deck 由调用方提供)
    from engine.simulator import run_match
    from engine.agents import BaselineAI, RandomAgent
    from .policy_agent import PolicyAgent
    pa = PolicyAgent(net, device=device, name="policy")
    opp_agent = (BaselineAI("baseline") if opponent == "baseline"
                 else RandomAgent("random", seed=0))
    wins = losses = draws = 0
    for i in range(n):
        seed = i if seeds is None else seeds[i]
        b = run_match(deck_a, deck_b, pa, opp_agent, seed=seed)
        if b.winner == 0:
            wins += 1
        elif b.winner is None:
            draws += 1
        else:
            losses += 1
    return {"n": n, "wins": wins, "losses": losses, "draws": draws,
            "win_rate": wins / n}


def load_net(path: str, device: str = "cpu") -> SmallPolicyValueNet:
    from .state_encoder import STATE_DIM, ACTION_DIM
    net = SmallPolicyValueNet(STATE_DIM, ACTION_DIM).to(device)
    net.load_state_dict(torch.load(path, map_location=device))
    net.eval()
    return net
