# -*- coding: utf-8 -*-
"""第十一批（2026-10-28）· 解析侧：Flygon 双触发 / Leafeon 能量链 / 复杂场地"""
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

FLYGON = ("Once during your turn, when you play this Pokémon from your hand to "
          "evolve 1 of your Pokémon, you may use this Ability. You may also use "
          "this Ability if this Pokémon is in the Active Spot and is Knocked Out "
          "by damage from an attack from your opponent’s Pokémon. Discard the "
          "top 2 cards of your opponent’s deck.")
LEAFEON = ("Attach a Basic {G} Energy card from your hand to 1 of your Benched "
           "Pokémon. If you do, heal all damage from that Pokémon.")
GRAND = ("Once during each player’s turn, that player may search their deck "
         "for a Stage 1 Pokémon that evolves from 1 of their Basic Pokémon and "
         "put it onto that Pokémon to evolve it. If that Pokémon was evolved in "
         "this way, that player may search their deck for a Stage 2 Pokémon "
         "that evolves from that Pokémon and put it onto that Pokémon to evolve "
         "it. Then, that player shuffles their deck.")
AREA = ("Each player who has any Tera Pokémon in play can have up to 8 Pokémon "
        "on their Bench. If a player no longer has any Tera Pokémon in play, "
        "that player discards Pokémon from their Bench until they have 5 "
        "Pokémon on their Bench.")
CAGE = ("Prevent all damage counters from being placed on Benched Pokémon "
        "(both yours and your opponent’s) by effects of attacks and Abilities "
        "from the opponent’s Pokémon. (Damage from attacks is still taken.)")


def _tags(text, kind="ability", name="X"):
    return E.to_engine_tags([E.parse_rule(text, kind=kind, name=name)])


class TestFlygonDualTrigger(unittest.TestCase):
    def test_both_triggers(self):
        r = _tags(FLYGON)
        self.assertEqual(r["tags"],
                         ["evolve_act:mill:2:opp", "on_ko:replay_evolve_act"])
        self.assertEqual(r["unsupported"], [])

    def test_other_second_trigger_still_unmodeled(self):
        text = FLYGON.replace("is in the Active Spot and is Knocked Out by "
                              "damage from an attack from your opponent’s "
                              "Pokémon", "is in the Active Spot")
        r = _tags(text)
        self.assertTrue(r["unsupported"])


class TestLeafeonChain(unittest.TestCase):
    def test_attach_bench_then_heal_same(self):
        r = _tags(LEAFEON, kind="attack")
        self.assertEqual(r["tags"], ["attach:1:grass:hand:target:bench",
                                     "heal:all:bench_one:if_prev"])
        self.assertEqual(r["unsupported"], [])


class TestComplexStadiums(unittest.TestCase):
    def test_grand_tree_chain(self):
        r = _tags(GRAND, kind="stadium")
        self.assertEqual(r["tags"],
                         ["stadium:use:evolve_from_deck:chain:1"])

    def test_area_zero(self):
        r = _tags(AREA, kind="stadium")
        self.assertEqual(r["tags"], ["stadium:bench_limit:8:require:tera"])

    def test_area_zero_without_trim(self):
        text = ("Each player who has any Tera Pokémon in play can have up to 8 "
                "Pokémon on their Bench.")
        self.assertEqual(_tags(text, kind="stadium")["tags"],
                         ["stadium:bench_limit:8:require:tera"])

    def test_battle_cage(self):
        r = _tags(CAGE, kind="stadium")
        self.assertEqual(r["tags"], ["stadium:no_bench_counters"])


if __name__ == "__main__":
    unittest.main()
