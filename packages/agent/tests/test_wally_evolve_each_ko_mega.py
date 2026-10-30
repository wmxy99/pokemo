# -*- coding: utf-8 -*-
"""第十二批（2026-10-28）· 解析侧：Wally's Compassion / 逐后备进化 / 条件击倒 / Mega 条件加伤

覆盖：
  · ``heal:all:mega:energy``               Wally's Compassion（回复 1 只 Mega → 其能量回手）
  · ``evolve_each_bench``                   Reuniclus「Cellular Ascension」
  · ``knockout:opp_active:basic``           Haxorus「Axe Blast」（只在对手基础时击倒）
  · ``fx:static_dmg:add:120:self_if_mega_type:darkness``  Seviper「Excited Power」
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG = HERE.parent
for p in (str(PKG), str(PKG.parent / "engine")):
    if p not in sys.path:
        sys.path.insert(0, p)

from agent import effects as E  # noqa: E402

WALLY = ("Heal all damage from 1 of your Mega Evolution Pokémon {ex}. If you "
         "healed any damage in this way, put all Energy attached to that "
         "Pokémon into your hand.")
REUNICLUS = ("For each of your Benched Pokémon, search your deck for a card "
             "that evolves from that Pokémon and put it onto that Pokémon to "
             "evolve it. Then, shuffle your deck.")
HAXORUS = ("If your opponent’s Active Pokémon is a Basic Pokémon, it is "
           "Knocked Out.")
SEVIPER = ("If you have any {D} Mega Evolution Pokémon {ex} in play, attacks "
           "used by this Pokémon do 120 more damage to your opponent’s Active "
           "Pokémon (before applying Weakness and Resistance).")


def _tags(text, kind="ability", name="X"):
    return E.to_engine_tags([E.parse_rule(text, kind=kind, name=name)])


class TestWallyCompassion(unittest.TestCase):
    def test_combined_tag(self):
        """训练家单 tag：回复 + 能量回手必须合并成一条"""
        r = _tags(WALLY, kind="supporter", name="Wally's Compassion")
        self.assertEqual(r["tags"], ["heal:all:mega:energy"])
        self.assertEqual(r["unsupported"], [])

    def test_plain_mega_heal_still_unmodeled(self):
        """缺「in this way」后续时，Mega 范围回复仍不可精确表达 → 不建模"""
        text = "Heal all damage from 1 of your Mega Evolution Pokémon {ex}."
        r = _tags(text, kind="supporter")
        self.assertTrue(r["unsupported"])


class TestEvolveEachBench(unittest.TestCase):
    def test_positive(self):
        r = _tags(REUNICLUS, kind="attack")
        self.assertEqual(r["tags"], ["evolve_each_bench"])
        self.assertEqual(r["unsupported"], [])

    def test_limited_variant_rejected(self):
        """带 stage / 属性限定的近邻写法不得被当成「逐只后备」"""
        text = REUNICLUS.replace("a card that evolves from that Pokémon",
                                 "a Stage 1 Pokémon that evolves from that Pokémon")
        r = _tags(text, kind="attack")
        self.assertNotIn("evolve_each_bench", r["tags"])


class TestHaxorusKnockout(unittest.TestCase):
    def test_conditional_knockout(self):
        r = _tags(HAXORUS, kind="attack")
        self.assertEqual(r["tags"], ["knockout:opp_active:basic"])
        self.assertEqual(r["unsupported"], [])

    def test_unconditional_passive_not_ko(self):
        """触发从句（When … is Knocked Out）不得被读成主动击倒"""
        r = _tags("When your opponent’s Active Pokémon is Knocked Out, draw 1 card.",
                  kind="ability")
        self.assertNotIn("knockout:opp_active", r["tags"])


class TestSeviperStaticBonus(unittest.TestCase):
    def test_positive(self):
        r = _tags(SEVIPER, kind="ability")
        self.assertEqual(r["tags"],
                         ["fx:static_dmg:add:120:self_if_mega_type:darkness"])
        self.assertEqual(r["unsupported"], [])

    def test_other_condition_still_unmodeled(self):
        """非 Mega 前置条件的静态加伤仍如实未建模"""
        text = SEVIPER.replace(
            "If you have any {D} Mega Evolution Pokémon {ex} in play, ",
            "If this Pokémon has 2 or more damage counters on it, ")
        r = _tags(text, kind="ability")
        self.assertEqual(len(r["tags"]), 1)
        self.assertTrue(r["tags"][0].startswith("fx:static_dmg:add:120:self_if_damage"))


class TestS6LongTail(unittest.TestCase):
    """S6 长尾：Munna「从 it 回复」/ Super Potion「回复后弃该目标 1 能量」"""

    def test_munna_heal_from_it(self):
        r = _tags("This Pokémon is now Asleep. Heal 30 damage from it.",
                  kind="attack")
        self.assertEqual(r["tags"], ["status_self:sleep", "heal:30"])
        self.assertEqual(r["unsupported"], [])

    def test_arven_sandwich_still_unmodeled(self):
        """「If that Pokémon is …」的品牌条件不得被 `from it` 顺手放行"""
        r = _tags("Heal 30 damage from your Active Pokémon. If that Pokémon is "
                  "an Arven's Pokémon, heal 100 damage from it instead.",
                  kind="card")
        self.assertTrue(r["unsupported"])

    def test_super_potion_discard_chain(self):
        r = _tags("Heal 60 damage from 1 of your Pokémon. If you healed any "
                  "damage in this way, discard an Energy from that Pokémon.",
                  kind="item", name="Super Potion")
        self.assertEqual(r["tags"], ["heal:60:choose:discard"])
        self.assertEqual(r["unsupported"], [])


if __name__ == "__main__":
    unittest.main()
