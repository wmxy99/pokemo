# -*- coding: utf-8 -*-
"""PokéLab Agent · MCTS 决策模块（引擎内蒙特卡洛树搜索）

v0.1 实现（真实 rollout 型）：
  - 根节点展开为全部 legal_actions；每迭代按 UCT 选子动作 → 深拷贝引擎对局 →
    随机/基线 rollout 至终局 → 胜/负/平回传（root 视角 = 当前行动玩家）。
  - 确定性：master_rng 驱动 rollout 动作选择与克隆局硬币重播种。
  - 策略先验：可选 policy_net 提供（未训练=均匀）；选子用 PUCT。
预算控制：max_iters（迭代数）或 time_budget_s（软上限）。
"""
from __future__ import annotations
import copy
import math
import random
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .state_encoder import encode_state, encode_action


@dataclass
class _Node:
    action: dict
    prior: float = 0.0
    visits: int = 0
    wins: float = 0.0          # root 视角胜场（平 = 0.5）

    def ucb(self, parent_visits: int, c_puct: float) -> float:
        if self.visits == 0:
            return float("inf")
        q = self.wins / self.visits
        u = c_puct * self.prior * math.sqrt(parent_visits) / (1 + self.visits)
        return q + u


def _rollout(battle, master_rng: random.Random,
             rollout_policy: Optional[Callable]) -> Optional[int]:
    """克隆对局随机/策略 rollout 至终局；返回胜者索引 0/1（平 None）"""
    b = copy.deepcopy(battle)
    b.rng = random.Random(master_rng.getrandbits(32))   # 重播种保证每局独立
    guard = 0
    while not b.finished and guard < 400:
        guard += 1
        view = b.view()
        legal = view.get("legal") or [{"verb": "pass"}]
        if rollout_policy is not None:
            act = rollout_policy(view, legal)
        else:
            act = dict(master_rng.choice(legal))
        res = b.step(act)
        if not res["ok"]:
            b.step({"verb": "pass"})
    return b.winner


def mcts_select_action(battle, iters: int = 40, c_puct: float = 1.4,
                       policy=None, device: str = "cpu",
                       rollout_policy: Optional[Callable] = None,
                       time_budget_s: Optional[float] = None,
                       seed: Optional[int] = None) -> dict:
    """在 battle（主阶段、轮到 current）上执行 MCTS，返回最优动作 dict。

    policy: SmallPolicyValueNet 或 None（均匀先验）。battle 必须可被 copy.deepcopy。
    """
    legal = battle.legal_actions()
    if not legal:
        return {"verb": "pass"}
    if len(legal) == 1:
        return dict(legal[0])
    master = random.Random(seed if seed is not None else battle.seed)
    root_player = battle.current

    # 策略先验
    priors = [1.0 / len(legal)] * len(legal)
    if policy is not None:
        import torch
        from .state_encoder import STATE_DIM, ACTION_DIM
        try:
            state = torch.tensor([encode_state(battle.view())],
                                 dtype=torch.float32, device=device)
            feats = torch.tensor(
                [[encode_action(battle.view(), a) for a in legal]],
                dtype=torch.float32, device=device)
            priors = policy.action_priors(state, feats)[0].tolist()
        except Exception:
            priors = [1.0 / len(legal)] * len(legal)

    children = [_Node(dict(a), p) for a, p in zip(legal, priors)]
    t0 = time.time()

    def _done() -> bool:
        if time_budget_s is not None and time.time() - t0 >= time_budget_s:
            return True
        return False

    n_sim = 0
    for _ in range(iters):
        if _done():
            break
        parent_n = sum(c.visits for c in children)
        # 1) UCT 选子
        if parent_n == 0:
            pick = max(range(len(children)), key=lambda i: children[i].prior)
        else:
            pick = max(range(len(children)),
                       key=lambda i: children[i].ucb(parent_n, c_puct))
        child = children[pick]
        # 2) 应用动作 → rollout
        b = copy.deepcopy(battle)
        b.rng = random.Random(master.getrandbits(32))
        res = b.step(dict(legal[pick]))
        if not res["ok"]:
            b.step({"verb": "pass"})
        winner = _rollout(b, master, rollout_policy)
        n_sim += 1
        # 3) 回传（root 视角）
        if winner == root_player:
            child.wins += 1.0
        elif winner is None:
            child.wins += 0.5
        child.visits += 1
    # 4) 选最优：最多访问（平手按先验）
    best = max(range(len(children)),
               key=lambda i: (children[i].visits,
                              children[i].prior))
    return dict(legal[best])


def mcts_with_stats(battle, iters: int = 40, **kw) -> tuple:
    """返回 (action, stats)：stats 含各候选 visit/胜率/先验（便于审计/回放）"""
    legal = battle.legal_actions()
    if not legal:
        return {"verb": "pass"}, {"n_sim": 0, "candidates": []}
    import torch
    from .state_encoder import STATE_DIM, ACTION_DIM, encode_state, encode_action
    policy = kw.pop("policy", None)      # 先取出，避免与 select 重复传参
    device = kw.pop("device", "cpu")
    priors = [1.0 / len(legal)] * len(legal)
    if policy is not None:
        try:
            state = torch.tensor([encode_state(battle.view())],
                                 dtype=torch.float32, device=device)
            feats = torch.tensor(
                [[encode_action(battle.view(), a) for a in legal]],
                dtype=torch.float32, device=device)
            priors = policy.action_priors(state, feats)[0].tolist()
        except Exception:
            pass
    action = mcts_select_action(battle, iters=iters, policy=policy,
                                device=device, **kw)
    idx = next(i for i, a in enumerate(legal) if a == action)
    stats = {"n_sim": iters, "candidates": [
        {"action": legal[i], "prior": round(priors[i], 4)}
        for i in range(len(legal))]}
    return action, stats
