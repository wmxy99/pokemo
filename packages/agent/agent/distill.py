# -*- coding: utf-8 -*-
"""PokéLab Agent · MCTS 教练蒸馏（阶段 3 · expert-iteration 式）

思路：比策略更强的 **MCTS（引擎 rollout）** 作为专家，在 vs 固定对手对局中
逐决策点用 MCTS 选出动作 → 生成监督帧 (state, feats_all, chosen)；
随后用 BC 同款 masked-CE（bc.train_bc）把 MCTS 决策蒸馏进策略网络。

与冻结快照阶段（阶段 2）互补：阶段 2 用 RL 自己探索 vs 冻结自我；
阶段 3 用**更高强度的搜索教师**纠正策略盲区，收敛更稳（技术方案 D1 路径）。

- collect_mcts_expert：跑 episodes 局，agent0 决策 = mcts_select_action(iters)
  （无先验时均匀展开，纯搜索）；opponent 为固定 AI（baseline/random）。
- 学习：frames → bc.train_bc（one-hot argmax 蒸馏，v1；soft-visits 目标为后续升级）。
"""
from __future__ import annotations
import copy
import random
from typing import Optional

from .state_encoder import encode_state, encode_action
from .mcts import mcts_select_action


def collect_mcts_expert(deck_a: list, deck_b: list, opponent,
                        episodes: int = 30, iters: int = 8,
                        seed: Optional[int] = None,
                        max_steps: int = 500) -> list:
    """MCTS 教师采样对局；返回可喂给 bc.train_bc 的帧列表"""
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
                act = mcts_select_action(b, iters=iters,
                                         seed=rng.randrange(1 << 30))
                try:
                    chosen = next(i for i, a in enumerate(legal) if a == act)
                except StopIteration:
                    act = {"verb": "pass"}
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
