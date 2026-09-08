# -*- coding: utf-8 -*-
"""PokéLab Engine · Agent 策略层

v1 提供：RandomAgent（随机合法动作）、BaselineAI（规则基线，K1 的参照对手）、
HumanAgent 接口（交互由上层 App 实现，此处仅协议）。
BaselineAI 仅基于 view 快照决策（无越权信息）。
"""
from __future__ import annotations
import random
from abc import ABC, abstractmethod


class Agent(ABC):
    name: str = "agent"

    @abstractmethod
    def decide(self, view: dict) -> dict:
        """输入 view（见 Battle.view），返回动作 dict（verb + 参数）"""
        raise NotImplementedError


class RandomAgent(Agent):
    def __init__(self, name: str = "random", seed: int = 0):
        self.name = name
        self.rng = random.Random(seed)

    def decide(self, view: dict) -> dict:
        legal = view.get("legal") or [{"verb": "pass"}]
        return dict(self.rng.choice(legal))


class BaselineAI(Agent):
    """规则基线 AI（白盒启发式；K1 参照对手）

    优先级：能攻击(有能量)则攻击 > 补能量 > 上后备 > 撤退(伤重/被克) > 过。
    """
    def __init__(self, name: str = "baseline_ai", aggressiveness: float = 1.0):
        self.name = name
        self.aggressiveness = aggressiveness

    def decide(self, view: dict) -> dict:
        legal = view.get("legal") or [{"verb": "pass"}]
        me = view["me_state"]
        opp = view["opp_state"]
        act = me.get("active")
        # 1) 能攻击 → 优先攻击（按伤害+一击击杀加成排序）
        attacks = [a for a in legal if a["verb"] == "attack"]
        if attacks and act:
            def _score(a):
                atk = act["attacks"][a["attack"]]
                oa = opp.get("active")
                bonus = 0.0
                if oa and oa.get("hp"):
                    if atk["damage"] >= oa["hp"] - oa.get("dmg", 0):
                        bonus = 10.0
                return atk["damage"] + bonus * self.aggressiveness
            return dict(max(attacks, key=_score))
        # 2) 能量未足 → 附能量（优先 active）
        act_attach = [a for a in legal if a["verb"] == "attach" and a.get("target") == 0]
        if act_attach and act and act["attacks"] and act["energy"] < max(
                a["cost"] for a in act["attacks"]):
            return dict(act_attach[0])
        # 3) 有后备空位 & 手牌有基础 → 上后备
        play = [a for a in legal if a["verb"] == "play"]
        if play and len(me["bench"]) < 5:
            return dict(play[0])
        # 4) 主战受伤较重 → 撤退（若有替补）
        retreat = [a for a in legal if a["verb"] == "retreat"]
        if retreat and act:
            ratio = act.get("dmg", 0) / max(1, act.get("hp", 1))
            if ratio > 0.5:
                return dict(retreat[0])
        # 5) 继续附能量 / 过
        if act_attach:
            return dict(act_attach[0])
        return {"verb": "pass"}


class HumanAgent(Agent):
    """交互式 Agent：由上层（CLI/Web）提供动作回调"""
    def __init__(self, name: str = "human", chooser=None):
        self.name = name
        self.chooser = chooser  # fn(view) -> action

    def decide(self, view: dict) -> dict:
        if self.chooser is None:
            raise RuntimeError("HumanAgent 需注入 chooser")
        return self.chooser(view)
