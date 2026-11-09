# -*- coding: utf-8 -*-
"""D1 · mulligan 补偿 = 「对手**可以**抽」（2026-11-09）

官方：起手没有基础宝可梦 → 洗回重抽（mulligan），**对手每遇到一次 mulligan
可以抽 1 张**。此前引擎是**自动抽**（`_safe_draw` 直接发牌），属简化偏差：
玩家没有拒绝的机会，而「多抽牌」在对局里并非永远有利（牌库耗尽 = 判负）。

现在改成开局决策（复用 F8c 的 setup 决策队列）：

  · `mulligan_count > 0` → 问**对手**「抽 0..N 张」（一次问完，官方是逐张问，
    但两次问之间没有新信息、抽牌单调有利 → 等价，见 `_setup_ask_next` 注释）；
  · 选 0 → 什么都不发生（`optional_declined` 日志）；
  · 牌库不够 → 选项数量按实际牌库收缩（不产生「抽 5 张」但只能抽 2 张的假动作）。

三条必须守住的边界：
  1. **不抽就是真的不抽**（手牌/牌库都不变）——写成「反正也抽」等于没改；
  2. **抽满 = 旧行为**（张数与 RNG 顺序一致）——否则历史对拍/K1 口径断掉；
  3. `mulligan_compensation=False` 时**不挂选择**（回滚开关仍然有效）。

运行：cd packages/engine && python -m unittest tests.test_d1_mulligan -v
"""
from __future__ import annotations

import unittest

from engine.battle import Battle
from engine.cards import (AbilityDef, AttackDef, PokeRef, PokemonDef,
                          make_pokemon)
from engine.state import GameConfig


def _basic(name: str = "Bunny", hp: int = 60) -> PokemonDef:
    return PokemonDef(name, hp=hp, ptype="fire", stage=0)


def _filler(n: int, hp: int = 100, stage: int = 0) -> list:
    """填充卡。

    ⚠ ``stage`` 默认 0 = **基础宝可梦**（`PokemonDef` 的默认值也是 0）。
      要造「起手没有基础宝可梦」必须显式给 ``stage=1``（进化卡不算基础），
      否则牌库全是基础，永远不会 mulligan —— 这个坑第一次就踩了。
    """
    return [PokeRef(PokemonDef("Filler", hp=hp, ptype="colorless",
                               stage=stage, evolves_from="Filler"
                               if stage else None))
            for _ in range(n)]


def _mulligan_deck() -> list:
    """只有 1 张基础宝可梦的牌库 → 大概率 mulligan（便于按种子构造）"""
    return [PokeRef(_basic())] + _filler(25, stage=1)


def _find_mulligan_seed(want: int = 1, seeds=range(400)):
    """找一个「某方恰好 mulligan ``want`` 次」的种子（固定表 → 结果确定）"""
    for s in seeds:
        b = Battle(_mulligan_deck(), list(_filler(26, 120)), seed=s,
                   cfg=GameConfig(first_turn_no_attack=False,
                                  first_turn_no_supporter=False))
        for pi in (0, 1):
            if b.players[pi].mulligan_count == want:
                return s, pi
    return None, None


class TestMulliganChoice(unittest.TestCase):
    def test_choice_offered_to_opponent(self):
        seed, who = _find_mulligan_seed(1)
        self.assertIsNotNone(seed, "400 个种子里应有恰 1 次 mulligan 的局面")
        b = Battle(_mulligan_deck(), list(_filler(26, 120)), seed=seed,
                   cfg=GameConfig(first_turn_no_attack=False,
                                  first_turn_no_supporter=False))
        ch = b._pending
        self.assertIsNotNone(ch, "有 mulligan 却没问补抽")
        self.assertEqual(ch.kind, "mulligan_draw")
        self.assertEqual(ch.owner, 1 - who, "补抽应由**对手**决定，不是自己")
        self.assertFalse(b.setup_done, "决策没闭合就开始了第一回合")
        self.assertEqual(len(ch.options), 2,
                         f"1 次 mulligan 应只有「不抽/抽 1 张」两个选项：{ch.options}")
        self.assertEqual(sorted(o["n"] for o in ch.options), [0, 1])

    def test_take_all_matches_old_autodraw(self):
        """抽满 = 旧行为（张数与 RNG 顺序一致）

        ⚠ 手牌 delta 要**扣掉首回合抽牌**（决策方恰好是先手时会多 1 张），
          所以断言分两半：日志里的 `drawn`（补抽本身）+ 牌库/手牌的自洽。
        """
        seed, who = _find_mulligan_seed(1)
        b = Battle(_mulligan_deck(), list(_filler(26, 120)), seed=seed,
                   cfg=GameConfig(first_turn_no_attack=False,
                                  first_turn_no_supporter=False))
        decider = 1 - who
        me = b.players[decider]
        hand0, deck0 = len(me.hand), len(me.deck)
        pick = next(i for i, o in enumerate(b._pending.options)
                    if o.get("n") == 1)
        r = b.step({"verb": "choose", "pick": pick})     # 抽 1
        self.assertTrue(r["ok"], r.get("error"))
        self.assertTrue(b.setup_done, "选择闭合后没有收尾")
        drawn = [e for e in b.events if e.get("type") == "mulligan_draw"][-1]
        self.assertEqual(drawn["drawn"], 1, f"补抽张数不对：{drawn}")
        turn_draw = 1 if b.current == decider else 0     # 先手首回合抽 1
        self.assertEqual(len(me.hand), hand0 + 1 + turn_draw,
                         "手牌增量与「补抽 + 首回合抽」不符")
        # 牌库还要少 6 张奖赏卡（`_finish_setup` 在决策闭合后才发牌）
        self.assertEqual(len(me.deck), deck0 - 1 - turn_draw - b.cfg.prizes,
                         "牌库减少量不符（凭空生成/吞卡）")

    def test_decline_draws_nothing(self):
        seed, who = _find_mulligan_seed(1)
        b = Battle(_mulligan_deck(), list(_filler(26, 120)), seed=seed,
                   cfg=GameConfig(first_turn_no_attack=False,
                                  first_turn_no_supporter=False))
        decider = 1 - who
        me = b.players[decider]
        hand0, deck0 = len(me.hand), len(me.deck)
        pick = next(i for i, o in enumerate(b._pending.options)
                    if o.get("n") == 0)
        r = b.step({"verb": "choose", "pick": pick})     # 不抽
        self.assertTrue(r["ok"], r.get("error"))
        self.assertTrue(b.setup_done)
        turn_draw = 1 if b.current == decider else 0     # 先手首回合抽 1（与补抽无关）
        self.assertEqual(len(me.hand), hand0 + turn_draw,
                         "拒绝了却还是抽了（等于没改）")
        self.assertEqual(len(me.deck), deck0 - turn_draw - b.cfg.prizes,
                         "拒绝了却还是动了牌库（应只少奖赏卡）")
        self.assertFalse([e for e in b.events
                          if e.get("type") == "mulligan_draw"],
                         "拒绝了却写了补抽日志")

    def test_two_mulligans_offer_range(self):
        """N 次 mulligan → 选项 0..N（一次问完，等价于逐张问）"""
        seed, who = _find_mulligan_seed(2)
        if seed is None:
            self.skipTest("400 个种子里没有恰 2 次 mulligan 的局面")
        b = Battle(_mulligan_deck(), list(_filler(26, 120)), seed=seed,
                   cfg=GameConfig(first_turn_no_attack=False,
                                  first_turn_no_supporter=False))
        ch = b._pending
        self.assertEqual(sorted(o["n"] for o in ch.options), [0, 1, 2])
        decider = 1 - who
        me = b.players[decider]
        hand0 = len(me.hand)
        pick = next(i for i, o in enumerate(ch.options) if o.get("n") == 2)
        b.step({"verb": "choose", "pick": pick})      # 抽 2
        turn_draw = 1 if b.current == decider else 0
        self.assertEqual(len(me.hand), hand0 + 2 + turn_draw)

    def test_option_count_shrinks_when_deck_short(self):
        """牌库不够 N 张 → 只列得出的张数（不产生假动作）

        白盒构造：正常牌库下写不出「mulligan 5 次但牌库只剩 1 张」，所以直接
        把决策方的牌库削到 1 张再问一次 —— 验的是**钳制**那一行。
        """
        seed, who = _find_mulligan_seed(1)
        b = Battle(_mulligan_deck(), list(_filler(26, 120)), seed=seed,
                   cfg=GameConfig(first_turn_no_attack=False,
                                  first_turn_no_supporter=False))
        owner = b._pending.owner
        dp = b.players[owner]
        dp.deck = dp.deck[:1]
        b._pending = None
        b.phase = "main"
        b._setup_queue = [("mulligan_draw", int(owner), 5)]
        b._setup_ask_next()
        ch = b._pending
        self.assertIsNotNone(ch, "牌库还有 1 张却没问")
        self.assertEqual(sorted(o["n"] for o in ch.options), [0, 1],
                         f"牌库只有 1 张却列了更多选项：{ch.options}")
        b.step({"verb": "choose", "pick": next(i for i, o in enumerate(ch.options)
                                              if o.get("n") == 1)})
        self.assertEqual(len(dp.deck), 0,
                         "牌库只剩 1 张时抽 1 张应恰好抽空")

    def test_no_choice_when_deck_empty(self):
        """牌库为空 → 不挂空选择（选项只剩「不抽」= 噪声动作）"""
        seed, who = _find_mulligan_seed(1)
        b = Battle(_mulligan_deck(), list(_filler(26, 120)), seed=seed,
                   cfg=GameConfig(first_turn_no_attack=False,
                                  first_turn_no_supporter=False))
        owner = b._pending.owner
        b.players[owner].deck = []
        b._pending = None
        b.phase = "main"
        b._setup_queue = [("mulligan_draw", int(owner), 2)]
        b._setup_ask_next()
        self.assertIsNone(b._pending, "牌库为空仍挂了空选择")

    def test_switch_off_means_no_choice(self):
        """`mulligan_compensation=False` → 不挂选择（回滚开关仍然有效）"""
        seed, who = _find_mulligan_seed(3)
        if seed is None:
            self.skipTest("没有恰 3 次 mulligan 的局面")
        b = Battle(_mulligan_deck(), list(_filler(26, 120)), seed=seed,
                   cfg=GameConfig(first_turn_no_attack=False,
                                  first_turn_no_supporter=False,
                                  mulligan_compensation=False))
        self.assertIsNone(b._pending, "关掉补偿却仍挂了选择")
        self.assertTrue(b.setup_done)


class TestPendingOwnerAccessor(unittest.TestCase):
    """`pending_owner()`：服务端/UI 判断「该谁动」的唯一可靠来源

    开局决策发生在**硬币之前**，`current` 此时恒为 0、不代表任何人的回合。
    只看 `current` 会把一方的选择派给另一方（API 实测会静默让 AI 替人类决定）。
    """

    def test_none_without_choice(self):
        b = _MulliganFree.dummy()
        self.assertIsNone(b.pending_owner())

    def test_matches_choice_owner(self):
        seed, who = _find_mulligan_seed(1)
        b = Battle(_mulligan_deck(), list(_filler(26, 120)), seed=seed,
                   cfg=GameConfig(first_turn_no_attack=False,
                                  first_turn_no_supporter=False))
        self.assertEqual(b.pending_owner(), b._pending.owner)
        self.assertNotEqual(b.pending_owner(), b.current,
                            "开局决策归属与 current 相同 → 这个用例失去意义")

    def test_becomes_none_after_choice(self):
        seed, who = _find_mulligan_seed(1)
        b = Battle(_mulligan_deck(), list(_filler(26, 120)), seed=seed,
                   cfg=GameConfig(first_turn_no_attack=False,
                                  first_turn_no_supporter=False))
        pick = next(i for i, o in enumerate(b._pending.options)
                    if o.get("n") == 0)
        b.step({"verb": "choose", "pick": pick})
        self.assertIsNone(b.pending_owner(), "选择闭合后仍报有挂起")


class _MulliganFree:
    @staticmethod
    def dummy():
        return Battle(list(_filler(26)), list(_filler(26)), seed=1,
                      cfg=GameConfig(first_turn_no_attack=False,
                                     first_turn_no_supporter=False))


class TestSetupOrder(unittest.TestCase):
    """既有 F8c 面朝下上场、又有 mulligan 时：**先摆场、后补抽**（官方顺序）"""

    CIN = PokemonDef("Cinderace", hp=160, ptype="fire", stage=2,
                     evolves_from="Raboot",
                     abilities=(AbilityDef("Explosiveness",
                                           tag="passive:setup_active",
                                           location="field"),),
                     attacks=(AttackDef("Turbo Flare", cost=1, damage=50),))

    def test_setup_active_asked_before_mulligan_draw(self):
        for s in range(400):
            deck = ([PokeRef(self.CIN) for _ in range(13)]
                    + [PokeRef(_basic()) for _ in range(13)])
            b = Battle(deck, list(_filler(26, 120)), seed=s,
                       cfg=GameConfig(first_turn_no_attack=False,
                                      first_turn_no_supporter=False))
            kinds = [t[0] for t in getattr(b, "_setup_queue", [])]
            if kinds == ["setup_active", "mulligan_draw"]:
                self.assertEqual(b._pending.kind, "setup_active",
                                 "先问的必须是摆场，不是补抽")
                # 做完第一步 → 第二步才轮到补抽
                idx = next(i for i, o in enumerate(b._pending.options)
                           if o.get("yes"))
                b.step({"verb": "choose", "pick": idx})
                self.assertEqual(b._pending.kind, "mulligan_draw")
                return
            # 关掉上一局残留无副作用（每个种子独立构造）
        self.skipTest("没有同时命中「面朝下上场 + mulligan」的种子")


class TestAgentHandlesChoice(unittest.TestCase):
    """规则基线 AI 必须能自己走完开局决策（否则对局卡在选择阶段）"""

    def test_baseline_draws_compensation(self):
        from engine.agents import BaselineAI
        seed, who = _find_mulligan_seed(1)
        b = Battle(_mulligan_deck(), list(_filler(26, 120)), seed=seed,
                   cfg=GameConfig(first_turn_no_attack=False,
                                  first_turn_no_supporter=False))
        me = b.players[1 - who]
        hand0 = len(me.hand)
        ai = BaselineAI("x")
        for _ in range(4):
            if b.finished:
                break
            # ⚠ 视角必须取**该出手的一方**（`acting_side()`）：开局决策期间
            #   `current` 恒为 0，用 `view()`（默认 current）会让非决策方拿到
            #   空 legal（严格归属之后），Agent 只能回 pass → 被引擎拒绝。
            r = b.step(ai.decide(b.view(b.acting_side())))
            self.assertTrue(r["ok"], r.get("error"))
        self.assertTrue(b.setup_done, "AI 没能走完开局决策")
        self.assertGreaterEqual(len(me.hand), hand0 + 1,
                                "AI 应该抽满补抽（与旧自动抽等价）")


if __name__ == "__main__":
    unittest.main()
