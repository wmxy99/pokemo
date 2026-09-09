# -*- coding: utf-8 -*-
"""PokéLab Agent · 局面得分（reward shaping / 价值监督用）

用**公开信息**做静态估值 state_reward(view) ∈ [0,1]：
  - 奖赏进度：1 - me.prizes/(me.prizes+opp.prizes)（奖赏领先占大头）
  - 场面：我方场上怪数 - 对手场上怪数（归一 0.2 权重）
  - 血量：我方场上总剩余 hp 与对手之差的 sigmoid（0.15 权重）
  - 能量：我方全场能量与对手之差 sigmoid（0.1 权重）
稀疏 ±1 终端奖励之外，训练时可加 c·state_reward 作每步 shaping（c 默认 0，
由 --reward-shaping 打开）。仅用于训练，不改变引擎。
"""
from __future__ import annotations


def _sigmoid(x: float) -> float:
    import math
    return 1.0 / (1.0 + math.exp(-x))


def _hp_total(state: dict) -> float:
    out = 0.0
    act = state.get("active") or {}
    if act:
        out += max(0.0, act.get("hp", 0) - act.get("dmg", 0))
    for fp in state.get("bench") or []:
        out += max(0.0, fp.get("hp", 0) - fp.get("dmg", 0))
    return out


def _energy_total(state: dict) -> float:
    out = 0.0
    act = state.get("active") or {}
    out += act.get("energy", 0)
    out += sum((b or {}).get("energy", 0) for b in state.get("bench") or [])
    return out


def state_reward(view: dict) -> float:
    """估值视角 = view['me']（当前决策方）。返回 [0,1]，0.5=均势。"""
    me, opp = view["me_state"], view["opp_state"]
    # 奖赏进度（主成分）
    mp = me.get("prizes_left", 6)
    op = opp.get("prizes_left", 6)
    prize_score = 1.0 - mp / max(1.0, mp + op)
    # 场面差
    me_pk = (1 if (me.get("active") or {}).get("hp") else 0) + len(me.get("bench") or [])
    op_pk = (1 if (opp.get("active") or {}).get("hp") else 0) + len(opp.get("bench") or [])
    board = _sigmoid(0.4 * (me_pk - op_pk))
    # 血量差 / 能量差
    hp = _sigmoid(0.01 * (_hp_total(me) - _hp_total(opp)))
    en = _sigmoid(0.15 * (_energy_total(me) - _energy_total(opp)))
    return 0.55 * prize_score + 0.2 * board + 0.15 * hp + 0.1 * en
