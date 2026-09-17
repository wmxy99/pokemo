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


def _value_of(battle, value_fn: Callable, root_player: int) -> Optional[float]:
    """用**价值头**评估局面 → 归一化到 [0,1] 的「root 视角胜率」

    `value_fn(view)` 应返回**该 view 行动方视角**的价值（策略网的 value 头即如此，
    训练时以自身折扣回报为目标，量纲 ≈ [-1,1]）。这里换算成 root 视角的 [0,1]：
    `(±v + 1) / 2`。相比「rollout 到终局」，叶评估**没有 rollout 噪声、且快几个数量级**
    （一次前向 vs 一次完整对局），因此同样时间预算下能搜更多节点。
    """
    try:
        v = float(value_fn(battle.view()))
    except Exception:                                    # noqa: BLE001
        return None
    if battle.current != root_player:
        v = -v
    return max(0.0, min(1.0, (v + 1.0) / 2.0))


def _search(battle, iters: int = 40, c_puct: float = 1.4,
            policy=None, device: str = "cpu",
            rollout_policy: Optional[Callable] = None,
            time_budget_s: Optional[float] = None,
            seed: Optional[int] = None,
            value_fn: Optional[Callable] = None):
    """执行搜索；返回 (children, legal)。

    ``value_fn``：叶评估函数（**推荐**，见 `_value_of`）。给定后**不再 rollout**，
    直接以价值头估值回传 —— 更准（无 rollout 噪声）且更快（可用更多迭代）。
    children=None 表示无分支（<2 合法动作）；否则 children[i] 对应 legal[i]。
    """
    legal = battle.legal_actions()
    if not legal:
        return None, []
    if len(legal) == 1:
        return None, legal
    master = random.Random(seed if seed is not None else battle.seed)
    root_player = battle.current

    # 策略先验
    priors = [1.0 / len(legal)] * len(legal)
    if policy is not None:
        import torch
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
        # 2) 应用动作 → 叶评估（价值头优先；否则 rollout 到终局）
        b = copy.deepcopy(battle)
        b.rng = random.Random(master.getrandbits(32))
        res = b.step(dict(legal[pick]))
        if not res["ok"]:
            b.step({"verb": "pass"})
        if value_fn is not None and not b.finished:
            q = _value_of(b, value_fn, root_player)
            if q is None:                       # 价值不可用 → 退回 rollout
                winner = _rollout(b, master, rollout_policy)
                q = 1.0 if winner == root_player else (0.5 if winner is None
                                                       else 0.0)
        else:
            winner = b.winner if b.finished else _rollout(b, master,
                                                          rollout_policy)
            q = 1.0 if winner == root_player else (0.5 if winner is None else 0.0)
        # 3) 回传（root 视角，q ∈ [0,1]）
        child.wins += q
        child.visits += 1
    return children, legal


def net_value_fn(net, device: str = "cpu") -> Callable:
    """把策略网的价值头包装成 `_value_of` 需要的 `value_fn(view) -> float`

    注意：价值头是以**行动方视角**训练的，故直接喂 `battle.view()`（默认视角 = 行动方）。
    """
    import torch
    from .state_encoder import encode_state

    def _fn(view: dict) -> float:
        with torch.no_grad():
            s = torch.tensor([encode_state(view)], dtype=torch.float32,
                             device=device)
            return float(net.evaluate_state(s)[0].item())
    return _fn


def mcts_select_action(battle, iters: int = 40, c_puct: float = 1.4,
                       policy=None, device: str = "cpu",
                       rollout_policy: Optional[Callable] = None,
                       time_budget_s: Optional[float] = None,
                       seed: Optional[int] = None,
                       value_fn: Optional[Callable] = None,
                       use_value_head: bool = True) -> dict:
    """在 battle（主阶段、轮到 current）上执行 MCTS，返回最优动作 dict。

    policy: SmallPolicyValueNet 或 None（均匀先验）。battle 必须可被 copy.deepcopy。
    ``use_value_head``（默认 True）：给出 ``policy`` 时**自动**用价值头做叶评估
    （比 rollout 到终局更准更快 —— 同样时间可搜更多节点）。传
    ``value_fn`` 可覆盖（如自定义评估器）；传 ``use_value_head=False`` 则退回 rollout。
    """
    if value_fn is None and use_value_head and policy is not None:
        value_fn = net_value_fn(policy, device=device)
    children, legal = _search(battle, iters=iters, c_puct=c_puct,
                              policy=policy, device=device,
                              rollout_policy=rollout_policy,
                              time_budget_s=time_budget_s, seed=seed,
                              value_fn=value_fn)
    if not legal:
        return {"verb": "pass"}
    if children is None:
        return dict(legal[0])
    best = max(range(len(children)),
               key=lambda i: (children[i].visits, children[i].prior))
    return dict(legal[best])


def mcts_policy_probs(battle, iters: int = 40, c_puct: float = 1.4,
                      policy=None, device: str = "cpu",
                      rollout_policy: Optional[Callable] = None,
                      seed: Optional[int] = None):
    """返回 soft 教师信号：(probs, visits, legal)

    probs = 根节点子访问数归一化（软标签，供 MCTS 决策蒸馏/降噪）；
    visits 为原始计数（无访问时各置 1 → 均匀）。probs 与 legal 索引对齐。
    """
    children, legal = _search(battle, iters=iters, c_puct=c_puct,
                              policy=policy, device=device,
                              rollout_policy=rollout_policy, seed=seed)
    if not legal:
        return [], [], []
    if children is None:
        n = len(legal)
        return [1.0 / n] * n, [0] * n, legal
    vis = [c.visits for c in children]
    if sum(vis) == 0:
        vis = [1] * len(vis)
    tot = sum(vis)
    return [v / tot for v in vis], [c.visits for c in children], legal


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
