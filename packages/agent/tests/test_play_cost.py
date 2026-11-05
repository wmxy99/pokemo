# -*- coding: utf-8 -*-
"""PokéLab Agent · 「打出代价」解析用例（2026-11-02 第三十六批）

卡面句式：``You can use this card only if you discard N other cards from your
hand.`` —— 三张真实卡同句（Morty's Conviction TEF 155 / Iris's Fighting
Spirit JTG 149 / Canari ASC 185，措辞都是 ``another card`` = 1 张）。

核心约定：
1. 它是**代价**，不是效果 —— 不进 `actions`、不进 tag，而是 spec 的
   ``play_cost``，最终落到卡级 ``TrainerDef.play_cost``，由引擎在派发**之前**
   执行。留在文本里会被通用分支读成一个 `discard:` 动作，后果有两个：
   训练家只认**一个**卡级 tag（多出来的 `discard:` 会让整卡「多 tag」不收）；
   即便收下，弃牌也会在效果**之后**发生（卡面要求先付账）。
2. **先剥离再解析** —— 剥掉后主效果才能正常识别（否则 `^` 锚定的整句正则失配）。
3. 限定能量属性的代价（``discard a Basic {R} Energy card``，Blowtorch）
   在第三十六批**故意不认领**（落成 ``discard:1`` 会把「只能弃基本火能量」
   放宽成「弃任意牌」= 静默放宽代价）；第三十八批补上
   ``discard_energy:<n>:<etype>`` 形态后才认领，见 ``TestSplit`` 与
   ``TestModeled*``。

运行：cd packages/agent && python -m unittest tests.test_play_cost -v
"""
from __future__ import annotations

import unittest

from agent.effects import _split_play_cost, parse_rule, to_engine_tags


def _m(text: str, kind: str = "supporter") -> dict:
    return to_engine_tags([parse_rule(text, kind=kind, name="X")])


MORTY = ("You can use this card only if you discard another card from your "
         "hand.\n\nDraw a card for each of your opponent\u2019s Benched Pokémon.")
IRIS = ("You can use this card only if you discard another card from your "
        "hand.\n\nDraw cards until you have 6 cards in your hand.")
CANARI = ("You can use this card only if you discard another card from your "
          "hand.\n\nSearch your deck for up to 4 {L} Pokémon, reveal them, "
          "and put them into your hand. Then, shuffle your deck.")
ULTRA = ("You can use this card only if you discard 2 other cards from your "
         "hand.\n\nSearch your deck for a Pokémon, reveal it, and put it into "
         "your hand. Then, shuffle your deck.")
SECRET = ("You can use this card only if you discard 3 other cards from your "
          "hand.\n\nSearch your deck for an Item card, a Pokémon Tool card, "
          "a Supporter card, and a Stadium card, reveal them, and put them "
          "into your hand. Then, shuffle your deck.")
BLOWTORCH = ("You can use this card only if you discard a Basic {R} Energy "
             "card from your hand.\n\nDiscard a Pokémon Tool or Special "
             "Energy card from 1 of your opponent\u2019s Pokémon, or discard "
             "a Stadium in play.")


class TestSplit(unittest.TestCase):
    """``_split_play_cost`` 的剥离行为"""

    def test_another_card_is_one(self):
        rest, pc = _split_play_cost(MORTY, "supporter")
        self.assertEqual(pc, "discard:1")
        self.assertTrue(rest.startswith("Draw a card"), rest[:40])

    def test_numeric_count(self):
        _, pc = _split_play_cost(ULTRA, "item")
        self.assertEqual(pc, "discard:2")

    def test_energy_limited_cost_claimed_with_type(self):
        """`discard a Basic {R} Energy card` → 认领成**带属性**的代价

        第三十六批**故意不认领**它：当时只有 `discard:<n>` 一种形态，落下去
        会把「只能弃基本火能量」放宽成「弃任意一张手牌」（静默放宽代价）。
        第三十八批新增 `discard_energy:<n>:<etype>` 后才有正确的表达方式。
        """
        rest, pc = _split_play_cost(BLOWTORCH, "item")
        self.assertEqual(pc, "discard_energy:1:fire")
        self.assertTrue(rest.startswith("Discard a Pokémon Tool"), rest[:40])

    def test_not_at_sentence_start_not_claimed(self):
        """只认**句首**的整句 —— 句中出现的不能认领（否则会吞掉前半句）"""
        t = ("This attack does 30 damage. You can use this card only if you "
             "discard another card from your hand.")
        _, pc = _split_play_cost(t, "item")
        self.assertIsNone(pc)

    def test_attack_kind_not_claimed(self):
        """代价只存在于训练家卡；招式/特性侧不认领（否则会凭空少一个动作）"""
        _, pc = _split_play_cost(MORTY, "attack")
        self.assertIsNone(pc)
        _, pc = _split_play_cost(MORTY, "ability")
        self.assertIsNone(pc)

    def test_no_clause(self):
        rest, pc = _split_play_cost("Draw 3 cards.", "supporter")
        self.assertIsNone(pc)
        self.assertEqual(rest, "Draw 3 cards.")


class TestRealCards(unittest.TestCase):
    """三张真实卡 + Ultra Ball / Secret Box / Blowtorch"""

    def test_morty(self):
        m = _m(MORTY)
        self.assertEqual(m["tags"], ["draw:1:per:opp_bench"])
        self.assertEqual(m["play_cost"], "discard:1")
        self.assertEqual(m["unsupported"], [])

    def test_iris(self):
        m = _m(IRIS)
        self.assertEqual(m["tags"], ["draw_to:6"])
        self.assertEqual(m["play_cost"], "discard:1")

    def test_canari(self):
        m = _m(CANARI)
        self.assertEqual(m["tags"], ["search_pokemon:4:type:lightning"])
        self.assertEqual(m["play_cost"], "discard:1")

    def test_ultra_ball_two(self):
        m = _m(ULTRA, kind="item")
        self.assertEqual(m["play_cost"], "discard:2")
        self.assertEqual(m["tags"], ["search_pokemon:1"])

    def test_secret_box_modeled(self):
        """第七十九批决策反转：代价 + 四类别各取 1 张**都已建模**

        旧断言要求「多类别并列检索 → 整卡未建模」，理由是通用分支只看第一个
        类别短语（4→1 张、逐类→任一，双错）。第七十九批新增引擎
        `search_multi:<sel>:<k>` 精确执行「逐类别各取 k 张」，旧断言随之
        改为锁定新 tag；代价 `discard:3` 仍必须保留（不得变成免费）。
        """
        m = _m(SECRET, kind="item")
        self.assertEqual(m["tags"],
                         ["search_multi:item+tool+supporter+stadium:1"])
        self.assertEqual(m["play_cost"], "discard:3")
        self.assertEqual(m["unsupported"], [])

    def test_blowtorch_energy_cost_modeled(self):
        """Blowtorch（第三十八批）：带属性的代价 + 主效果「二选一」都已建模

        主效果 ``Discard a Pokémon Tool or Special Energy card from 1 of your
        opponent's Pokémon, **or** discard a Stadium in play.`` 是**两支** ——
        此前只认出后半句（`discard_stadium`），前半句被静默吞掉，所以这张卡
        一直停在「打出前置条件未建模」。本批两支都进 `discard_tool_or_senergy`。
        """
        m = _m(BLOWTORCH, kind="item")
        self.assertEqual(m["play_cost"], "discard_energy:1:fire")
        self.assertEqual(m["tags"], ["discard_tool_or_senergy:1:opp"])
        self.assertEqual(m["unsupported"], [])


class TestNoCost(unittest.TestCase):
    """无代价句的卡不得被凭空加上代价（静默削弱）"""

    def test_plain_supporter(self):
        m = _m("Draw 3 cards.")
        self.assertEqual(m["play_cost"], "")
        self.assertEqual(m["tags"], ["draw:3"])

    def test_search_without_cost(self):
        m = _m("Search your deck for up to 2 Basic Pokémon, reveal them, "
               "and put them into your hand.")
        self.assertEqual(m["play_cost"], "")


class TestConflictingCosts(unittest.TestCase):
    """多 spec 出现互相矛盾的代价 → 如实拒绝（取其一都是静默改变强度）"""

    def test_conflict_rejected(self):
        specs = [{"kind": "supporter", "trigger": "on_use", "conditions": [],
                  "actions": [{"type": "draw", "amount": 2, "target": "deck"}],
                  "play_cost": "discard:1",
                  "source_text": "Draw 2 cards.", "citations": []},
                 {"kind": "supporter", "trigger": "on_use", "conditions": [],
                  "actions": [{"type": "draw", "amount": 1, "target": "deck"}],
                  "play_cost": "discard:3",
                  "source_text": "Draw a card.", "citations": []}]
        m = to_engine_tags(specs)
        self.assertEqual(m["play_cost"], "")
        self.assertTrue(any("多种打出代价" in u["reason"]
                            for u in m["unsupported"]))


class TestEffectStillParsed(unittest.TestCase):
    """剥离代价句后主效果必须仍被正常识别（不是「剥了就不管」）"""

    def test_search_kind_preserved(self):
        m = _m(CANARI)
        self.assertIn("search_pokemon:4:type:lightning", m["tags"])

    def test_draw_per_counter_preserved(self):
        m = _m(MORTY)
        self.assertIn("draw:1:per:opp_bench", m["tags"])


if __name__ == "__main__":
    unittest.main()
