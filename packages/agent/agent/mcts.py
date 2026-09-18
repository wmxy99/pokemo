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


def load_value_calib(path: str, group: str = "pooled") -> Optional[tuple]:
    """读标定参数 → (a, b)（委托 `agent.value_calib.load_calib`，单一来源）"""
    from .value_calib import load_calib
    return load_calib(path, group)


def _value_of(battle, value_fn: Callable, root_player: int,
              value_calib: Optional[tuple] = None) -> Optional[float]:
    """用**价值头**评估局面 → 归一化到 [0,1] 的「root 视角胜率」

    `value_fn(view)` 应返回**该 view 行动方视角**的价值（策略网的 value 头即如此）。
    相比「rollout 到终局」，叶评估**没有 rollout 噪声、且快几个数量级**
    （一次前向 vs 一次完整对局），因此同样时间预算下能搜更多节点。

    `value_calib=(a, b)`：**胜率标定**。默认映射 `(v+1)/2` 隐含两个未检验假设
    （输出量纲即胜率区间、且线性映射）；实测该假设不成立 —— 价值头均值 0.29
    而实际胜率 0.52（**系统性乐观约 12pp**），且 MCTS 迭代越多越差
    （iters=100 → 0.650，300 → 0.525）。传入标定后按 `p = sigmoid(a·v + b)`
    （Platt）换算；参数由 `examples/calibrate_value.py` 拟合。

    **顺序很重要**：先把行动方视角的价值转成**概率**，再对"非 root 行动方"
    取 `1-p`。非对称映射下这与"先取反再映射"**不等价**（旧实现是先取反）。
    """
    try:
        v = float(value_fn(battle.view()))
    except Exception:                                    # noqa: BLE001
        return None
    if value_calib is not None:
        a_, b_ = value_calib
        z = max(-30.0, min(30.0, a_ * v + b_))
        p = 1.0 / (1.0 + math.exp(-z))
    else:
        p = max(0.0, min(1.0, (v + 1.0) / 2.0))
    if battle.current != root_player:
        p = 1.0 - p
    return max(0.0, min(1.0, p))


def _search(battle, iters: int = 40, c_puct: float = 1.4,
            policy=None, device: str = "cpu",
            rollout_policy: Optional[Callable] = None,
            time_budget_s: Optional[float] = None,
            seed: Optional[int] = None,
            value_fn: Optional[Callable] = None,
            value_calib: Optional[tuple] = None):
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

    # 策略先验（编码配置取自网络对象，避免同进程多配置互踩）
    priors = [1.0 / len(legal)] * len(legal)
    if policy is not None:
        import torch
        _cfg = getattr(policy, "encoder_cfg", None)
        try:
            state = torch.tensor([encode_state(battle.view(), _cfg)],
                                 dtype=torch.float32, device=device)
            feats = torch.tensor(
                [[encode_action(battle.view(), a, _cfg) for a in legal]],
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
            q = _value_of(b, value_fn, root_player, value_calib)
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

    注意 1：价值头以**行动方视角**训练，故直接喂 `battle.view()`（默认视角 = 行动方）。
    注意 2：编码配置**取自网络对象**（`net.encoder_cfg`）—— 同进程里评估多个配置
    不同的网络时（如 `rich` vs `basic` 的 head-to-head），全局会话配置只可能对上
    其中一个，另一个必然被喂错维度的特征。
    """
    import torch
    from .state_encoder import encode_state
    cfg = getattr(net, "encoder_cfg", None)

    def _fn(view: dict) -> float:
        with torch.no_grad():
            s = torch.tensor([encode_state(view, cfg)], dtype=torch.float32,
                             device=device)
            return float(net.evaluate_state(s)[0].item())
    return _fn


# --------------------------------------------------------------------------
# 树搜索（多 ply）—— 1-ply 评估的搜索「几乎不产生信息增益」，故必须能加深
# --------------------------------------------------------------------------
class _TNode:
    """树搜索节点。

    语义（全部用**胜率**而非 [-1,1] 价值，便于跨节点翻转）：
      · `wins` = 「**本节点行动方**获胜」的累计概率，`q_self = wins / visits`
      · 父节点评估子节点时：若 `child.actor == parent.actor`（同一回合内多动作）
        直接用 `q_self`，否则取 `1 - q_self`
    """

    __slots__ = ("battle", "actor", "parent", "prior", "visits", "wins",
                 "children", "expanded", "legal")

    def __init__(self, battle, actor: int, parent=None, prior: float = 0.0):
        self.battle = battle
        self.actor = actor
        self.parent = parent
        self.prior = prior
        self.visits = 0
        self.wins = 0.0
        self.children: list = []
        self.expanded = False
        self.legal: list = []

    def q_self(self) -> float:
        return self.wins / self.visits if self.visits else 0.0


def _value_actor_p(battle, value_fn: Callable,
                   value_calib: Optional[tuple] = None) -> float:
    """价值头 → **该局面行动方**的胜率 [0,1]（标定可选）

    与 `_value_of` 的区别：**不**做 root 视角翻转 —— 树搜索在每一层自己翻转。
    """
    try:
        v = float(value_fn(battle.view()))
    except Exception:                                    # noqa: BLE001
        return 0.5
    if value_calib is not None:
        a_, b_ = value_calib
        z = max(-30.0, min(30.0, a_ * v + b_))
        return 1.0 / (1.0 + math.exp(-z))
    return max(0.0, min(1.0, (v + 1.0) / 2.0))


def _expand(node: _TNode, policy, device: str, master: random.Random) -> None:
    """展开节点：为每个合法动作建子节点（子局面 = 深拷贝 + 推进该动作）"""
    legal = node.battle.legal_actions()
    node.legal = legal
    node.expanded = True
    if not legal:
        return
    priors = [1.0 / len(legal)] * len(legal)
    if policy is not None:
        import torch
        _cfg = getattr(policy, "encoder_cfg", None)
        try:
            state = torch.tensor([encode_state(node.battle.view(), _cfg)],
                                 dtype=torch.float32, device=device)
            feats = torch.tensor(
                [[encode_action(node.battle.view(), a, _cfg) for a in legal]],
                dtype=torch.float32, device=device)
            priors = policy.action_priors(state, feats)[0].tolist()
        except Exception:                                # noqa: BLE001
            priors = [1.0 / len(legal)] * len(legal)
    for act, p in zip(legal, priors):
        b = copy.deepcopy(node.battle)
        b.rng = random.Random(master.getrandbits(32))
        res = b.step(dict(act))
        if not res["ok"]:
            b.step({"verb": "pass"})
        node.children.append(_TNode(b, b.current, parent=node, prior=p))


def _search_tree(battle, iters: int = 40, c_puct: float = 1.4, policy=None,
                 device: str = "cpu", value_fn: Optional[Callable] = None,
                 value_calib: Optional[tuple] = None, max_depth: int = 2,
                 rollout_policy: Optional[Callable] = None,
                 seed: Optional[int] = None):
    """**多 ply** 树搜索（UCT + 价值头叶评估），返回 (root, legal)。

    为什么需要它：1-ply 版（`_search`）只对「动作之后的那一个局面」估值，
    而实测**63% 的"动作后状态"行动方仍是我方**（回合内多动作），
    所以 1-ply 几乎等于"用价值头重排同一批动作"，信息增益很小 ——
    实测 MCTS(12) 仅 0.60–0.65，**打不赢网络单独 0.695**。

    加深后可覆盖：① 对手的最佳应对（回合交界的真实风险）
    ② 我方**回合内多动作配合**（附着→进化→出招的顺序问题）。
    """
    legal = battle.legal_actions()
    if not legal:
        return None, []
    master = random.Random(seed if seed is not None else battle.seed)
    root = _TNode(copy.deepcopy(battle), battle.current)
    _expand(root, policy, device, master)
    t0 = time.time()

    for _ in range(iters):
        if time.time() - t0 > 3600:                      # 兜底
            break
        node = root
        path = [root]
        depth = 0
        # 1) 选择：沿 UCB 下降到未展开节点 / 深度上限
        while node.expanded and node.children and depth < max_depth:
            parent_n = sum(c.visits for c in node.children)
            act = node.actor

            def score(c):
                q = c.q_self()
                if c.actor != act:                       # 换手 → 视角翻转
                    q = 1.0 - q
                return q + (c_puct * c.prior * math.sqrt(max(1, parent_n))
                            / (1 + c.visits))
            pick = max(range(len(node.children)),
                       key=lambda i: score(node.children[i]))
            node = node.children[pick]
            path.append(node)
            depth += 1
        # 2) 展开 + 叶值（叶值恒为**该叶行动方**视角）
        if node.battle.finished:
            w = node.battle.winner
            leaf_p = 0.5 if w is None else (1.0 if w == node.actor else 0.0)
        else:
            if not node.expanded and depth < max_depth:
                _expand(node, policy, device, master)
            if value_fn is not None:
                leaf_p = _value_actor_p(node.battle, value_fn, value_calib)
            else:                                        # 退回 rollout（慢但无偏）
                winner = _rollout(node.battle, master, rollout_policy)
                leaf_p = (0.5 if winner is None
                          else (1.0 if winner == node.actor else 0.0))
        # 3) 回传（按各节点自身行动方视角累计）
        same = node.actor
        for n in path:
            n.wins += leaf_p if n.actor == same else 1.0 - leaf_p
            n.visits += 1
    return root, legal


def mcts_select_action(battle, iters: int = 40, c_puct: float = 1.4,
                       policy=None, device: str = "cpu",
                       rollout_policy: Optional[Callable] = None,
                       time_budget_s: Optional[float] = None,
                       seed: Optional[int] = None,
                       value_fn: Optional[Callable] = None,
                       value_calib: Optional[tuple] = None,
                       use_value_head: bool = True,
                       max_depth: int = 1) -> dict:
    """在 battle（主阶段、轮到 current）上执行 MCTS，返回最优动作 dict。

    policy: SmallPolicyValueNet 或 None（均匀先验）。battle 必须可被 copy.deepcopy。
    ``use_value_head``（默认 True）：给出 ``policy`` 时**自动**用价值头做叶评估
    （比 rollout 到终局更准更快 —— 同样时间可搜更多节点）。传
    ``value_fn`` 可覆盖（如自定义评估器）；传 ``use_value_head=False`` 则退回 rollout。
    ``value_calib``：价值头胜率标定 `(a, b)`（见 `load_value_calib` /
    `examples/calibrate_value.py`）。
    ``max_depth``：**搜索深度（ply）**。默认 1 = 历史行为（只给"动作后的局面"估值）；
    ≥2 走 `_search_tree` 递进展开 —— 实测 1-ply 打不赢网络单独，加深才有意义。
    """
    if value_fn is None and use_value_head and policy is not None:
        value_fn = net_value_fn(policy, device=device)
    if max_depth and max_depth >= 2:
        root, legal = _search_tree(battle, iters=iters, c_puct=c_puct,
                                   policy=policy, device=device,
                                   value_fn=value_fn, value_calib=value_calib,
                                   max_depth=max_depth,
                                   rollout_policy=rollout_policy, seed=seed)
        if not legal:
            return {"verb": "pass"}
        if root is None or not root.children:
            return dict(legal[0])
        best = max(range(len(root.children)),
                   key=lambda i: (root.children[i].visits,
                                  root.children[i].prior))
        return dict(root.legal[best])
    children, legal = _search(battle, iters=iters, c_puct=c_puct,
                              policy=policy, device=device,
                              rollout_policy=rollout_policy,
                              time_budget_s=time_budget_s, seed=seed,
                              value_fn=value_fn, value_calib=value_calib)
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
    from .state_encoder import encode_state, encode_action
    policy = kw.pop("policy", None)      # 先取出，避免与 select 重复传参
    device = kw.pop("device", "cpu")
    priors = [1.0 / len(legal)] * len(legal)
    if policy is not None:
        _cfg = getattr(policy, "encoder_cfg", None)
        try:
            state = torch.tensor([encode_state(battle.view(), _cfg)],
                                 dtype=torch.float32, device=device)
            feats = torch.tensor(
                [[encode_action(battle.view(), a, _cfg) for a in legal]],
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
