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
from . import reward as rw


def _fresh_battle(deck_a: list, deck_b: list, seed: int):
    from engine.battle import Battle
    return Battle(deck_a, deck_b, seed=seed)


def collect_episode(battle, net, device="cpu", temperature=1.0,
                    max_steps=500, shaping: float = 0.0) -> list:
    """自对弈一局（双方共用 net，采样）；返回步记录列表（可选 shaping 中间奖励）"""
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
            "r": shaping * rw.state_reward(view),
        })
        res = battle.step(copy.deepcopy(legal[idx]))
        if not res["ok"]:
            battle.step({"verb": "pass"})
    return steps


def _returns(steps: list, winner: Optional[int], gamma: float) -> list:
    """交替视角回报：R_t = (shaping_r_t + 终端±1) - gamma·R_{t+1}"""
    n = len(steps)
    targets = [0.0] * n
    g = 0.0
    for t in range(n - 1, -1, -1):
        actor = steps[t]["actor"]
        term = 0.0
        if t == n - 1:
            term = 1.0 if (winner == actor) else \
                (-1.0 if winner is not None else 0.0)
        r_t = steps[t].get("r", 0.0) + term
        g = r_t - gamma * g            # 下一状态视角为对手 → 取负
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
              log_every: int = 10, shaping: float = 0.0,
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
                                temperature=temperature, shaping=shaping)
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


def train_ppo_vs_fixed(deck_a: list, deck_b: list, net,
                       opponent, episodes: int = 150, ppo_epochs: int = 3,
                       clip: float = 0.2, lr: float = 1e-3,
                       gamma: float = 0.99, temperature: float = 1.0,
                       seed: Optional[int] = None, device: str = "cpu",
                       log_every: int = 25,
                       ent_coef: float = 0.02, lr_decay: float = 1.0,
                       eval_fn=None, eval_every: int = 0,
                       shaping: float = 0.0,
                       save_path: Optional[str] = None) -> dict:
    """课程式训练：policy(agent0) vs 固定对手（如 BaselineAI）。

    只更新 agent0 的策略步；回报为该视角单侧折扣（无交替），训练更稳定，
    胜率 vs 固定对手可单调观测（适合"先打败基线，再自对弈"的课程）。
    ent_coef：熵正则（防局部）；lr_decay：线性衰减终点系数（lr → lr*lr_decay）；
    eval_fn(steps)：每 eval_every 局回调（供 vs baseline 周期观测）。
    shaping：局面得分中间奖励权重（配合 agent.reward.state_reward）。
    """
    from .policy_agent import build_spread_plan
    from .state_encoder import STATE_DIM, ACTION_DIM as _AD
    rng = random.Random(seed)
    opt = torch.optim.Adam(net.parameters(), lr=lr)
    stats = {"episodes": 0, "wins": 0, "losses": 0, "draws": 0,
             "actor_loss": [], "win_tail": []}
    for ep in range(episodes):
        # 线性 lr 衰减：lr → lr*lr_decay
        frac = (ep + 1) / max(1, episodes)
        opt.param_groups[0]["lr"] = lr * (lr_decay + (1 - lr_decay)
                                          * (1 - frac))
        battle = _fresh_battle(deck_a, deck_b, seed=rng.randrange(1 << 30))
        net.eval()
        frames = []          # 仅 agent0 决策点
        guard = 0
        while not battle.finished and guard < 500:
            guard += 1
            if battle.current == 0:
                view = battle.view()
                legal = view.get("legal") or [{"verb": "pass"}]
                if len(legal) == 1:
                    chosen, logp, act = 0, 0.0, dict(legal[0])
                else:
                    S = torch.tensor([encode_state(view)],
                                     dtype=torch.float32, device=device)
                    FA = torch.tensor(
                        [[encode_action(view, a) for a in legal]],
                        dtype=torch.float32, device=device)
                    with torch.no_grad():
                        probs = net.action_priors(S, FA,
                                                  temperature=temperature)[0]
                    probs = probs.clamp(min=1e-9)
                    probs = probs / probs.sum()
                    chosen = int(torch.multinomial(probs, 1).item())
                    logp = float(torch.log(probs[chosen]).item())
                    act = build_spread_plan(view, dict(legal[chosen]))
                frames.append({"state_vec": encode_state(view),
                               "feats_all": [encode_action(view, a)
                                             for a in legal],
                               "chosen": chosen, "logp": logp,
                               "r": shaping * rw.state_reward(view)})
                res = battle.step(copy.deepcopy(act))
            else:
                opp_view = battle.view()
                res = battle.step(copy.deepcopy(opponent.decide(opp_view)))
            if not res["ok"]:
                battle.step({"verb": "pass"})
        # 单侧折扣回报（含 shaping 中间奖励）
        r = 1.0 if battle.winner == 0 else (-1.0 if battle.winner is not None
                                            else 0.0)
        g = 0.0
        targets = [0.0] * len(frames)
        for t in range(len(frames) - 1, -1, -1):
            term = r if t == len(frames) - 1 else 0.0
            g = frames[t].get("r", 0.0) + term + gamma * g
            targets[t] = g
        if not frames:
            continue
        stats["episodes"] += 1
        if battle.winner == 0:
            stats["wins"] += 1
        elif battle.winner is None:
            stats["draws"] += 1
        else:
            stats["losses"] += 1
        # PPO 更新
        for s, tg in zip(frames, targets):
            s["_ret"] = tg
        net.train()
        for _ in range(ppo_epochs):
            S, FA, MASK, C, O, R = _to_batch(frames, device)
            loss, a_loss, v_loss, ent = _ppo_loss(net, S, FA, MASK, C, O,
                                                  R, clip, ent_coef)
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(net.parameters(), 5.0)
            opt.step()
        net.eval()
        stats["actor_loss"].append(a_loss)
        if log_every and (ep + 1) % log_every == 0:
            cur = stats["wins"] / max(1, stats["episodes"])
            stats["win_tail"].append(cur)
            print(f"[vs-fixed] ep {ep + 1}/{episodes} "
                  f"wins={stats['wins']}/{stats['episodes']} "
                  f"win={cur:.2f} actor={a_loss:.4f}")
        if eval_every and eval_fn and (ep + 1) % eval_every == 0:
            eval_fn(ep + 1)
    if save_path:
        Path(save_path).parent.mkdir(parents=True, exist_ok=True)
        torch.save(net.state_dict(), save_path)
        stats["save_path"] = save_path
    return stats


def clone_net(net: SmallPolicyValueNet, device: str = "cpu") -> SmallPolicyValueNet:
    """复制网络（作冻结对手快照；与源网络独立可单独 eval）"""
    import copy as _copy
    new = SmallPolicyValueNet(net.state_dim, net.action_dim,
                              hidden=net.ctx[0].out_features).to(device)
    new.load_state_dict(_copy.deepcopy(net.state_dict()))
    new.eval()
    return new


def eval_winrate(deck_a: list, deck_b: list, net,
                 n: int = 20, seeds: Optional[range] = None,
                 opponent="baseline", device: str = "cpu") -> dict:
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
