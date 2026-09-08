# -*- coding: utf-8 -*-
"""PokéLab Agent · PolicyAgent —— 让策略网络真正驱动决策

- 对 view.legal 用网络先验打分（采样或 argmax）选出动作；
- 选中攻击若含 spread_free（任意分配）效果，用启发式 distribute_damage 生成 targets；
- 权重来自 RL 训练管线（trainer.py）或 BC 加载；net=None 时退化为随机。

实现引擎 Agent 协议（decide(view)->action），可直接用于 run_match / 自对弈。
"""
from __future__ import annotations
import random
from typing import Optional
import torch
import torch.nn.functional as F

from .state_encoder import (encode_state, encode_action, STATE_DIM,
                            ACTION_DIM, legal_actions_from_view)


def build_spread_plan(view: dict, action: dict) -> dict:
    """E5：若选中的攻击带 spread_free tag → 附加 targets 计划（启发式轮询）"""
    if action.get("verb") != "attack":
        return action
    act = view["me_state"]["active"]
    if act is None or action["attack"] >= len(act["attacks"]):
        return action
    atk = act["attacks"][action["attack"]]
    total = None
    for t in atk.get("tags", []):
        if t.startswith("spread_free:"):
            total = int(t.split(":")[1])
    if total is None:
        return action
    from engine.rules import distribute_damage
    bench_n = len(view["opp_state"]["bench"])
    dmg = distribute_damage(bench_n, total)
    plan = [{"pos": i, "dmg": d} for i, d in enumerate(dmg) if d > 0]
    return {**action, "targets": plan}


class PolicyAgent:
    """策略网络驱动的 Agent（共享网络，供自对弈双方复用实例）"""

    def __init__(self, net=None, device: str = "cpu", name: str = "policy",
                 sample: bool = False, temperature: float = 1.0,
                 seed: Optional[int] = None):
        self.net = net
        self.device = device
        self.name = name
        self.sample = sample
        self.temperature = max(temperature, 1e-6)
        self.rng = random.Random(seed)

    def decide(self, view: dict) -> dict:
        legal = legal_actions_from_view(view)
        if len(legal) == 1:
            return build_spread_plan(view, dict(legal[0]))
        if self.net is None:
            pick = self.rng.randrange(len(legal))
            return build_spread_plan(view, dict(legal[pick]))
        with torch.no_grad():
            state = torch.tensor([encode_state(view)], dtype=torch.float32,
                                 device=self.device)
            feats = torch.tensor(
                [[encode_action(view, a) for a in legal]],
                dtype=torch.float32, device=self.device)
            probs = self.net.action_priors(state, feats,
                                           temperature=self.temperature)[0]
        if self.sample:
            idx = int(torch.multinomial(probs, 1).item())
        else:
            idx = int(probs.argmax().item())
        return build_spread_plan(view, dict(legal[idx]))


def load_policy(path: str, device: str = "cpu"):
    """加载训练产物 → 网络实例"""
    from .policy_net import SmallPolicyValueNet
    net = SmallPolicyValueNet(STATE_DIM, ACTION_DIM).to(device)
    net.load_state_dict(torch.load(path, map_location=device))
    net.eval()
    return net
