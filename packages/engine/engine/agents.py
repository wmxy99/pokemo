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
        # F8c/D1（2026-11-09）：`phase=choice`（开局上场 / mulligan 补抽 /
        # 伤害前决策 / 退化数量 …）期间**只接受** `choose` / `stop`，
        # 返回 `pass` 会被引擎拒绝（对局卡死）。
        #
        # 取法（规则基线只有启发式，不做搜索）：
        #   1. 多选（`min<max`，任意数量）已经选过东西 → 直接结束，避免无限选；
        #   2. 有 `n`（mulligan 补抽张数）→ 取**最多**：抽牌单调有利，等价于
        #      官方「每次都抽」。⚠ 这一点很重要 —— 旧实现是自动抽 N 张，
        #      取最大才让它与历史口径**逐局等价**（K1 可比）。
        #   3. 有 `yes`/`hand`（开局上场 / 支付代价）→ 取 `yes=True`；
        #   4. 其余（目标选择）→ 第一个（确定性）。
        if any(a.get("verb") == "choose" for a in legal):
            _stop = [a for a in legal if a.get("verb") == "stop"]
            if _stop and int(_stop[0].get("picked") or 0) > 0:
                return dict(_stop[0])
            _ch = [a for a in legal if a.get("verb") == "choose"]

            def _choice_score(a):
                opt = a.get("option") or {}
                return (1 if opt.get("n") is not None else 0,
                        int(opt.get("n") or 0),
                        1 if opt.get("yes") else 0)
            return dict(max(_ch, key=_choice_score))
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


class SupportAI(BaselineAI):
    """规则基线 AI · **会用训练家与特性**的版本（v1.3 新增）

    存在的理由：``BaselineAI`` 是 **K1 的冻结参照对手**（只做 攻击/附能/上场/撤退），
    行为一旦改动会使历史 K1 胜率口径不可比。而 v1.3 把 A1–A4 的可执行率提到 100%
    后，卡组里约 1/3 的是训练家与特性卡——若没有会使用它们的 AI，「可对战」就只是
    形式上的。故新增本类，**继承** BaselineAI 并在其之后追加：

      1) field 特性（无代价收益：抽牌 / 附能 / 移动伤害指示物）优先发动
      2) 训练家：跳阶进化 → 检索/附能 → 铺场 → 拉人 → 回收 → 换位 → 抽弃 → 干扰
      3) 之后完全沿用原基线优先级（附能 / 上场 / 撤退 / 过）

    默认**不**用于 K1 评测；评测口径仍以 ``BaselineAI`` 为准（口径冻结）。
    """
    name = "support_ai"

    def __init__(self, name: str = "support_ai", aggressiveness: float = 1.0):
        super().__init__(name=name, aggressiveness=aggressiveness)

    def decide(self, view: dict) -> dict:
        legal = view.get("legal") or [{"verb": "pass"}]
        me = view["me_state"]
        act = me.get("active")
        hand_n = me.get("hand_count", len(me.get("hand") or []))
        bench_n = len(me.get("bench") or [])

        # 0) 能攻击仍然最高优先（与基线一致）
        attacks = [a for a in legal if a["verb"] == "attack"]
        if attacks and act:
            return super().decide(view)

        # 1) field 特性：无能量代价的收益，直接发动
        for verb_name in ("recon", "draw", "attach_energy", "move_damage"):
            cand = [a for a in legal if a["verb"] == "ability"
                    and str(a.get("tag", "")).startswith(verb_name + ":")]
            if cand:
                return dict(cand[0])

        # 2) 训练家 / 支援者（按收益排序）
        trainers = [a for a in legal if a["verb"] == "trainer"]
        if trainers:
            def _rank(a):
                tag = str(a.get("tag") or "")
                key = (tag or str(a.get("name") or "")).lower()
                order = ("candy", "search_energy", "discard_search_energy",
                         "poffin", "ultra_ball", "boss", "recover",
                         "switch_self", "shuffle_draw", "research", "hammer")
                score = 100
                for i, pref in enumerate(order):
                    if pref in key:
                        score = i
                        break
                # 手牌很少时不再弃手牌（research 类自伤）
                if "research" in key and hand_n <= 4:
                    score += 50
                # 后备已满则不铺场
                if ("poffin" in key or "ultra_ball" in key) and bench_n >= 5:
                    score += 50
                return score
            best = min(trainers, key=_rank)
            if _rank(best) < 50:
                return dict(best)

        # 3) 其余沿用基线
        return super().decide(view)


class HumanAgent(Agent):
    """交互式 Agent：由上层（CLI/Web）提供动作回调"""
    def __init__(self, name: str = "human", chooser=None):
        self.name = name
        self.chooser = chooser  # fn(view) -> action

    def decide(self, view: dict) -> dict:
        if self.chooser is None:
            raise RuntimeError("HumanAgent 需注入 chooser")
        return self.chooser(view)
