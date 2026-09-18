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

from .state_encoder import (encode_state, encode_action, action_dim,
                            legal_actions_from_view, encoder_dim)


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
    """策略网络驱动的 Agent（共享网络，供自对弈双方复用实例）

    ``net`` 可以是**单个网络**，也可以是**网络列表**（集成）：此时先验取各网络的
    平均 —— 目的是降低单权重方差（本项目实测多个 seed 的权重双盲胜率都在
    ±0.01 内浮动，平均先验常能稳住上限）。**注意所有网络必须共用同一套
    状态/动作编码器**（legacy 51 维 与 full 59 维的权重不可混用）。
    """

    def __init__(self, net=None, device: str = "cpu", name: str = "policy",
                 sample: bool = False, temperature: float = 1.0,
                 seed: Optional[int] = None):
        if isinstance(net, (list, tuple)):
            self.nets = [n for n in net if n is not None]
            self.net = self.nets[0] if self.nets else None
        else:
            self.nets = [net] if net is not None else []
            self.net = net
        self.device = device
        self.name = name
        self.sample = sample
        self.temperature = max(temperature, 1e-6)
        self.rng = random.Random(seed)

    def _encoder_cfg(self):
        """本 Agent 应使用的编码配置：**优先网络自带的**（见 `make_net` / `load_net`）

        为什么要绑在网络上：编码器是**全局会话状态**，而同一个进程里可能同时评估
        两个配置不同的网络（如 `rich` vs `basic` 的 head-to-head）—— 全局配置
        只可能等于其中一个，另一个必然被喂错维度的特征（实测报
        `mat1 and mat2 shapes cannot be multiplied (5x14 and 31x64)`）。
        """
        for n in self.nets:
            if hasattr(n, "encoder_cfg"):
                return n.encoder_cfg
        from .state_encoder import active_encoder
        return active_encoder()

    def decide(self, view: dict) -> dict:
        legal = legal_actions_from_view(view)
        if len(legal) == 1:
            return build_spread_plan(view, dict(legal[0]))
        if not self.nets:
            pick = self.rng.randrange(len(legal))
            return build_spread_plan(view, dict(legal[pick]))
        cfg = self._encoder_cfg()
        with torch.no_grad():
            state = torch.tensor([encode_state(view, cfg)],
                                 dtype=torch.float32, device=self.device)
            feats = torch.tensor(
                [[encode_action(view, a, cfg) for a in legal]],
                dtype=torch.float32, device=self.device)
            probs = None
            for n in self.nets:                      # 集成：先验取平均
                p = n.action_priors(state, feats,
                                    temperature=self.temperature)[0]
                probs = p if probs is None else probs + p
            probs = probs / len(self.nets)
        if self.sample:
            idx = int(torch.multinomial(probs, 1).item())
        else:
            idx = int(probs.argmax().item())
        return build_spread_plan(view, dict(legal[idx]))


def load_policy(path: str, device: str = "cpu"):
    """加载训练产物 → 网络实例（**宽度与编码配置都从权重推断**，见 trainer.load_net）"""
    from .policy_net import SmallPolicyValueNet
    from .state_encoder import (action_dim, apply_config_from_state_dict,
                                encoder_dim)
    sd = torch.load(path, map_location=device)
    hidden = int(sd["ctx.0.weight"].shape[0]) if "ctx.0.weight" in sd else 64
    cfg = apply_config_from_state_dict(sd)
    net = SmallPolicyValueNet(encoder_dim(), action_dim(),
                              hidden=hidden).to(device)
    net.encoder_cfg = cfg
    net.load_state_dict(sd)
    net.eval()
    return net
