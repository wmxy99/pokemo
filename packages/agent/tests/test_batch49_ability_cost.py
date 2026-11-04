# -*- coding: utf-8 -*-
"""第四十九批（2026-11-04）· 解析用例：特性发动代价

卡面：「**You must** <代价> **in order to use this Ability.** Once during your
turn, you may …」

这批卡此前**整卡进不了池**（`in order to use this ability` 一出现就被守卫拒收），
或者更糟：代价句被通用分支读成一个动作、挤掉主效果。现在改成「**先剥句**，
代价挂 `ability_cost` 字段」，与训练家 `play_cost` 同一套纪律。
"""
from __future__ import annotations
import unittest

from agent import effects as E


def _m(text, kind="ability", name="x"):
    sp = E.parse_rule(text, kind=kind, name=name)
    return E.to_engine_tags([sp])


UP_TEMPO = ("You must put a card from your hand on the bottom of your deck in "
            "order to use this Ability. Once during your turn, you may draw "
            "cards until you have 5 cards in your hand.")
BECKON = ("You must discard a Chill Teaser Toy card from your hand in order to "
          "use this Ability. Once during your turn, you may switch in 1 of your "
          "opponent’s Benched Pokémon to the Active Spot.")
FLASHING = ("You must discard a Basic {L} Energy from this Pokémon in order to "
            "use this Ability. Once during your turn, you may draw cards until "
            "you have 6 cards in your hand.")
RECONSTITUTE = ("You must discard 2 cards from your hand in order to use this "
                "Ability. Once during your turn, you may draw a card.")
TORRID = ("You must discard a Basic {R} Energy card from your hand in order to "
          "use this Ability. Once during your turn, you may make your "
          "opponent’s Active Pokémon Burned.")


class TestAbilityCost(unittest.TestCase):
    def test_hand_bottom(self):
        r = _m(UP_TEMPO)
        self.assertEqual(r["ability_cost"], "hand_bottom:1")
        self.assertEqual(r["tags"], ["draw_to:5"])

    def test_discard_named(self):
        r = _m(BECKON)
        self.assertEqual(r["ability_cost"], "discard_named:1:chillteasertoy")
        self.assertEqual(r["tags"], ["gust"])

    def test_self_energy(self):
        r = _m(FLASHING)
        self.assertEqual(r["ability_cost"], "discard_self_energy:1:lightning")
        self.assertEqual(r["tags"], ["draw_to:6"])

    def test_discard_cards(self):
        r = _m(RECONSTITUTE)
        self.assertEqual(r["ability_cost"], "discard:2")
        self.assertEqual(r["tags"], ["draw:1"])

    def test_hand_energy(self):
        r = _m(TORRID)
        self.assertEqual(r["ability_cost"], "discard_hand_energy:1:fire")
        self.assertEqual(r["tags"], ["burn"])

    def test_cost_removed_from_effects(self):
        """代价句**不得**残留成动作（否则会挤掉主效果 / 在效果之后才弃牌）"""
        r = _m(RECONSTITUTE)
        self.assertEqual(r["unsupported"], [])
        self.assertFalse([t for t in r["tags"] if t.startswith("discard")])

    def test_self_vs_hand_energy_not_interchanged(self):
        """「from this Pokémon」与「from your hand」是两个来源，不可互相顶替"""
        self.assertNotEqual(_m(FLASHING)["ability_cost"],
                            _m(TORRID)["ability_cost"])

    def test_unparsed_cost_still_rejected(self):
        """认不出的代价形态 → 保留拒收（绝不当作「无代价」免费发动）"""
        r = _m("You must sacrifice a Prize card in order to use this Ability. "
               "Once during your turn, you may draw a card.")
        self.assertEqual(r["ability_cost"], "")
        self.assertTrue(any("代价" in u["reason"] for u in r["unsupported"]))

    def test_no_cost_clause_untouched(self):
        """没有该句的特性不得凭空加代价（那是静默削弱）"""
        r = _m("Once during your turn, you may draw a card.")
        self.assertEqual(r["ability_cost"], "")


if __name__ == "__main__":
    unittest.main()
