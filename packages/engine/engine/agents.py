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


class RuleAI(SupportAI):
    """规则 AI · 会**进化**的版本（2026-11-10）

    为什么另开一个类而不是改 ``SupportAI``：``SupportAI`` 是 K1 训练的**可选
    对手口径**（``--opponent support``），行为一改，历史训练分布就不可比。
    而「AI 从不进化」对 Stage 2 卡组是致命的：10 套预设里 7 套主力在二阶，
    40 局实测中有 26 局以 ``no_pokemon`` 收场 —— 卡组再怎么按官方模板优化，
    玩家体感都等于零。

    与 ``SupportAI`` 的两点差别：

      1. **进化最先做**（在攻击之前）：进化不消耗攻击机会，晚进化等于白丢
         一回合伤害；目标位置优先战斗位（先让主力能打）。
      2. ``SupportAI`` 放弃回合（返回 pass）时，再试**道具 / 场地**：它们的
         收益是常驻的（HP +100 / 减伤 / 增伤），不抢关键检索的时机。

    ⚠ 这个类**不进 K1 评测口径**：``k1_accept.py`` 仍用 ``BaselineAI`` 双盲。
    """

    name = "rule_ai"

    # 牌库见底保护：抽牌类动作用到只剩这么几张时停手
    LOW_DECK = 15
    # 会**减少牌库**的动作前缀（抽牌 / 弃牌重抽 / 洗回重抽）。
    # 检索类（search_*）不在此列：它只改变牌的**位置**，不消耗牌库总量。
    _DRAW_TAGS = ("recon:", "draw:", "draw_to:", "draw_cond:", "draw_to_branch:",
                  "research:", "shuffle_draw:", "shuffle_draw_split:",
                  "opp_shuffle_draw:", "discard_to_both:")

    @classmethod
    def _is_draw(cls, action: dict) -> bool:
        tag = str(action.get("tag") or "")
        return tag.startswith(cls._DRAW_TAGS)

    def decide(self, view: dict) -> dict:
        legal = view.get("legal") or [{"verb": "pass"}]
        me = view.get("me_state") or {}

        # 0) 进化：先把主力推到最终形态（战斗位优先）
        evo = [a for a in legal if a.get("verb") == "evolve"]
        if evo:
            return dict(next((a for a in evo if a.get("target") == 0), evo[0]))

        # 0.5) 空招防护：可付的招式**全是 0 伤害** → 那不是攻击，是空转
        #
        # A1 自战实测：Duskull 的「Come and Get You」（0 伤害，从弃牌堆铺后备）
        # 被当成攻击用了 70 次 —— 每用一次就少一次换人/附能的机会，两只 330 HP
        # 的 ex 谁也打不死，平均 37 回合后靠牌库抽空分胜负。
        # 正确做法是先把主力换上来（switch / 撤退），而不是拿辅助宝可梦空转。
        act_fp = me.get("active") or {}
        atts = act_fp.get("attacks") or []
        atk_acts = [a for a in legal if a.get("verb") == "attack"]

        def _dmg(a):
            i = a.get("attack")
            if isinstance(i, int) and 0 <= i < len(atts):
                return int(atts[i].get("damage") or 0)
            return 0

        if atk_acts and max(_dmg(a) for a in atk_acts) == 0:
            for a in legal:
                tag = str(a.get("tag") or "")
                if a.get("verb") == "retreat" or (
                        a.get("verb") == "trainer" and tag.startswith("switch_self")):
                    return dict(a)

        # 0.7) 牌库见底 → 抽牌类一律不碰
        #
        # 为什么必要：Drakloak 的 Recon 每回合抽 2、Iono / Research 再抽 5–7，
        # SupportAI 会一直用（对它是"白拿牌"）。60 张牌 20 回合就被抽空 ——
        # 实测 40 局里 28 局以 `deckout` 收场，玩家看到的是"卡组还没打就输了"。
        # 真实玩家会留 10 张以上缓冲，这里按 15 张划线。
        deck_n = int(me.get("deck_count") or 99)
        if deck_n <= self.LOW_DECK:
            kept = [a for a in legal if not self._is_draw(a)]
            if len(kept) != len(legal):
                view = dict(view)
                view["legal"] = kept or [{"verb": "pass"}]

        act = super().decide(view)

        # 1) 没别的事可做 → 附道具 / 打场地（常驻收益）
        if act.get("verb") == "pass":
            for a in view["legal"]:
                tag = str(a.get("tag") or "")
                if a.get("verb") == "trainer" and tag.startswith(("tool:", "stadium:")):
                    return dict(a)
        return act


class HumanAgent(Agent):
    """交互式 Agent：由上层（CLI/Web）提供动作回调"""
    def __init__(self, name: str = "human", chooser=None):
        self.name = name
        self.chooser = chooser  # fn(view) -> action

    def decide(self, view: dict) -> dict:
        if self.chooser is None:
            raise RuntimeError("HumanAgent 需注入 chooser")
        return self.chooser(view)
