# -*- coding: utf-8 -*-
"""PokéLab Agent · 状态编码器

把 engine.Battle.view() 的结构化观测编码为**定长特征向量**（供策略网络/MCTS 先验），
并把 legal_actions 编码为**动作特征**（每候选一向量）。

v1 编码设计（实测 STATE_DIM=51 / ACTION_DIM=14，以模块常量推断值为准）：
  - 状态向量：双方 active（血量比/伤害/分色能量/状态/阶段）· 后备数量与前3槽血量 ·
    奖赏 · 资源计数（牌库/手牌/supporter/附能/回合）
  - 动作向量：verb one-hot(7) + 参数数值归一(3) + 训练家 tag one-hot(4)
    对「不可知细节」（对手手牌）一律不编码 → 保证仅用公开信息。
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Optional

ENGINE_TYPES = ("grass", "fire", "water", "lightning", "psychic", "fighting",
                "darkness", "metal", "dragon", "colorless")

VERBS = ("play", "attach", "attack", "retreat", "evolve", "trainer", "pass")
TRAINER_TAGS = ("poffin", "ultra_ball", "hammer", "boss")


def _norm_hp(d: dict) -> float:
    """血量归一化（参考系 max 320 → [0,1]）"""
    if not d:
        return 0.0
    return min(1.0, (d.get("hp", 0) - d.get("dmg", 0)) / 320.0)


def _energy_vec(d: dict) -> list:
    """能量分色计数 → 10 维（截断 6/色）"""
    em = (d or {}).get("energy_map") or {}
    return [min(6.0, float(em.get(t, 0))) for t in ENGINE_TYPES]


def encode_state(view: dict, cfg: Optional["EncoderConfig"] = None) -> list:
    """view → 定长状态特征 list[float]

    无 cfg 时回退到会话激活配置（set_active_encoder）→ 默认 None = 基础 51 维。
    cfg.features 追加特征域（bad-case 迭代入口：'hand' / 'meta'）。
    """
    cfg = cfg if cfg is not None else _ACTIVE_CFG
    me, opp = view["me_state"], view["opp_state"]
    s: list[float] = []
    ma, oa = me["active"], opp["active"]
    # 双方 active：血量/伤害比/能量(10)/状态(5)/阶段
    s += [_norm_hp(ma)]
    s += [min(1.0, (ma.get("dmg", 0) or 0) / 320.0) if ma else 0.0]
    s += _energy_vec(ma)
    s += [1.0 if t in (ma.get("status") or []) else 0.0
          for t in ("poison", "burn", "sleep", "paralyze", "confuse")] \
        if ma else [0.0] * 5
    s += [float((ma or {}).get("stage", 0))]
    s += [_norm_hp(oa)]
    s += [min(1.0, (oa.get("dmg", 0) or 0) / 320.0) if oa else 0.0]
    s += _energy_vec(oa)
    s += [1.0 if t in (oa.get("status") or []) else 0.0
          for t in ("poison", "burn", "sleep", "paralyze", "confuse")] \
        if oa else [0.0] * 5
    s += [float((oa or {}).get("stage", 0))]
    # 后备（固定前 3 槽：数量 + 每槽血比）
    mb, ob = me["bench"], opp["bench"]
    s += [float(len(mb))]
    for fp in mb[:3]:
        s += [_norm_hp(fp)]
    for _ in range(3 - min(3, len(mb))):
        s += [0.0]
    s += [float(len(ob))]
    for fp in ob[:3]:
        s += [_norm_hp(fp)]
    for _ in range(3 - min(3, len(ob))):
        s += [0.0]
    # 奖赏与资源
    s += [float(me["prizes_left"]), float(opp["prizes_left"])]
    s += [float(me.get("deck_count", 0)), float(me.get("hand_count", 0))]
    s += [float(me.get("supporter_used", 0)),
          float(me.get("attached_energy_this_turn", 0))]
    s += [float(view.get("turn", 0))]
    # 可选特征域（bad-case 迭代：cfg.features）
    if cfg is not None:
        for name in cfg.features:
            s += _extra_features(view, name)
    return s


def _extra_features(view: dict, name: str) -> list:
    me, opp = view["me_state"], view["opp_state"]
    if name == "hand":
        # 我方手牌构成（公开：我方手牌全可见）
        hand = me.get("hand") or []
        pk = sum(1 for c in hand if c.get("type") == "pokemon")
        en = sum(1 for c in hand if c.get("type") == "energy")
        tr = sum(1 for c in hand if c.get("type") in ("item", "supporter"))
        return [min(1.0, pk / 10.0), min(1.0, en / 10.0),
                min(1.0, tr / 10.0), min(1.0, len(hand) / 10.0)]
    if name == "meta":
        mp = me.get("prizes_left", 6)
        op = opp.get("prizes_left", 6)
        turn = float(view.get("turn", 0))
        return [min(1.0, turn / 100.0),
                mp / max(1.0, mp + op),
                min(1.0, me.get("deck_count", 0) / 60.0),
                min(1.0, me.get("discard_count", 0) / 60.0)]
    raise ValueError(f"未知特征域 {name}（可选 hand/meta）")


def encode_action(view: dict, action: dict) -> list:
    """单个合法动作 → 动作特征（verb one-hot + 参数归一 + tag one-hot）"""
    verb = action.get("verb", "pass")
    a: list[float] = [0.0] * len(VERBS)
    if verb in VERBS:
        a[VERBS.index(verb)] = 1.0
    # 数值参数（归一化到 [0,1]）
    a += [min(1.0, action.get("index", 0) / 10.0),
          min(1.0, action.get("attack", 0) / 5.0),
          min(1.0, action.get("target", 0) / 6.0)]
    tag = action.get("tag", "")
    tg = [1.0 if tag == t else 0.0 for t in TRAINER_TAGS] if tag else [0.0] * 4
    a += tg
    return a


def legal_actions_from_view(view: dict) -> list:
    return view.get("legal") or [{"verb": "pass"}]


STATE_DIM = None  # 惰性求值（由首次 encode_state 推断，见模块底部探针）


@dataclass(frozen=True)
class EncoderConfig:
    """状态编码配置（bad-case 迭代入口）。

    features 可选域：'hand'（我方手牌构成 +4）· 'meta'（回合/资源比例 +4）。
    组合维度 = 51 + 4·n，由 encoder_dim() 给出。
    """
    features: tuple = ()
    device: str = "cpu"


# 会话级激活配置：set_active_encoder(cfg) 后，无参 encode_state/encoder_dim
# 自动采用扩展特征域（训练/eval 用 --features full 开启；默认 None=51 维 legacy）。
_ACTIVE_CFG: Optional["EncoderConfig"] = None


def set_active_encoder(cfg: Optional["EncoderConfig"]) -> None:
    """设置会话级编码配置（传 None 恢复 legacy 51 维）"""
    global _ACTIVE_CFG
    _ACTIVE_CFG = cfg


def active_encoder() -> Optional["EncoderConfig"]:
    return _ACTIVE_CFG


def encoder_dim(cfg: Optional[EncoderConfig] = None) -> int:
    """状态维度：传入 cfg 优先，否则用会话激活配置；均无 → 51 legacy"""
    cfg = cfg if cfg is not None else _ACTIVE_CFG
    if cfg is None:
        return STATE_DIM
    return STATE_DIM + sum(
        4 for f in cfg.features
        if f in ("hand", "meta"))  # 未知域抛错交给 encode 路径


def _probe_dim() -> int:
    probe = {"me": 0, "turn": 1, "phase": "main", "finished": False,
             "me_state": {"hand": [], "active": None, "bench": [],
                          "prizes_left": 6, "deck_count": 60, "hand_count": 0,
                          "discard_count": 0, "supporter_used": 0,
                          "attached_energy_this_turn": 0},
             "opp_state": {"hand_count": 0, "active": None, "bench": [],
                           "prizes_left": 6, "deck_count": 60,
                           "discard_count": 0},
             "legal": []}
    return len(encode_state(probe))


STATE_DIM = _probe_dim()
ACTION_DIM = len(encode_action({}, {"verb": "pass"}))
