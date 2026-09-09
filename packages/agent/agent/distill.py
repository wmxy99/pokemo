# -*- coding: utf-8 -*-
"""PokéLab Agent · MCTS 教练蒸馏（阶段 3 · expert-iteration 式）

思路：比策略更强的 **MCTS（引擎 rollout）** 作为专家，在 vs 固定对手对局中
逐决策点用 MCTS 生成动作分布 → 监督帧 (state, feats_all, chosen[, probs])；
再用 masked-CE（bc.train_bc）或 **soft-visits 交叉熵（bc.train_bc_soft）**
把 MCTS 决策蒸馏进策略网络。

与冻结快照阶段（阶段 2）互补：阶段 2 用 RL 自己探索 vs 冻结自我；
阶段 3 用**更高强度的搜索教师**纠正策略盲区，收敛更稳（技术方案 D1 路径）。

- collect_mcts_expert(soft=True)：probs = 根节点 visits 归一化（软标签，
  比 one-hot argmax 保留更多"次优动作也合理"的梯度信息，训练更平滑）；
- soft=False：仅 argmax 动作（bc.train_bc 兼容）。
"""
from __future__ import annotations
import copy
import random
from typing import Optional

from .state_encoder import encode_state, encode_action
from .mcts import mcts_policy_probs, mcts_select_action


def collect_mcts_expert(deck_a: list, deck_b: list, opponent,
                        episodes: int = 30, iters: int = 8,
                        seed: Optional[int] = None, soft: bool = True,
                        max_steps: int = 500) -> list:
    """MCTS 教师采样对局；返回帧列表（soft=True 时含 visits 归一化 probs）"""
    from .trainer import _fresh_battle
    rng = random.Random(seed)
    frames = []
    for _ in range(episodes):
        b = _fresh_battle(deck_a, deck_b, seed=rng.randrange(1 << 30))
        guard = 0
        while not b.finished and guard < max_steps:
            guard += 1
            if b.current == 0:
                view = b.view()
                legal = view.get("legal") or [{"verb": "pass"}]
                if not legal:
                    break
                if soft:
                    probs, _, _ = mcts_policy_probs(
                        b, iters=iters, seed=rng.randrange(1 << 30))
                    if not probs:
                        break
                    chosen = max(range(len(legal)), key=lambda i: probs[i])
                    frames.append({"state_vec": encode_state(view),
                                   "feats_all": [encode_action(view, a)
                                                 for a in legal],
                                   "chosen": chosen, "probs": probs})
                    act = dict(legal[chosen])
                else:
                    act = mcts_select_action(b, iters=iters,
                                             seed=rng.randrange(1 << 30))
                    chosen = next(i for i, a in enumerate(legal) if a == act)
                    frames.append({"state_vec": encode_state(view),
                                   "feats_all": [encode_action(view, a)
                                                 for a in legal],
                                   "chosen": chosen})
                res = b.step(copy.deepcopy(act))
            else:
                res = b.step(copy.deepcopy(opponent.decide(b.view())))
            if not res["ok"]:
                b.step({"verb": "pass"})
    return frames
