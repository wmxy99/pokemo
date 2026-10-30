# -*- coding: utf-8 -*-
"""第十九批（2026-10-29）· 解析侧：额外能量条件加伤 / 全队常驻减伤

覆盖：
  · ``dmg_plus:N:if:own_active_extra_energy_at_least:K``  Electivire ex / Tangrowth
  · ``fx:team_reduce:…``                                 Curly Wall / Gear Coating /
                                                          Stone Palace

负向守卫：只写「extra Energy」而**没有** in addition to this attack's cost →
仍不猜；临时性减伤（During your opponent's next turn）→ 仍不猜。
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

ELECTIVIRE = ("If this Pokémon has at least 2 extra Energy attached (in addition "
              "to this attack’s cost), this attack does 100 more damage.")
TANGROWTH = ("If this Pokémon has at least 2 extra Energy attached (in addition "
             "to this attack’s cost), this attack does 140 more damage.")
CURLY = ("As long as you have at least 1 other Bouffalant in play, all of your "
         "Basic {C} Pokémon take 60 less damage from attacks from your "
         "opponent’s Pokémon (after applying Weakness and Resistance). The "
         "effect of Curly Wall doesn’t stack.")
GEAR = ("All of your Pokémon that have any {M} Energy attached take 20 less "
        "damage from attacks from your opponent’s Pokémon (after applying "
        "Weakness and Resistance).")
STONE = ("As long as this Pokémon is on your Bench, all of your Steven’s "
         "Pokémon take 30 less damage from attacks from your opponent’s Pokémon "
         "(after applying Weakness and Resistance). The effect of Stone Palace "
         "doesn’t stack.")
EXTRA_ONLY = ("If this Pokémon has at least 2 extra Energy attached, this attack "
              "does 100 more damage.")
IRON_DEFENDER = ("During your opponent’s next turn, all of your {M} Pokémon take "
                 "30 less damage from attacks from your opponent’s Pokémon "
                 "(after applying Weakness and Resistance).")


def _tags(text, kind="attack", name="X"):
    return E.to_engine_tags([E.parse_rule(text, kind=kind, name=name)])


class TestExtraEnergyParse(unittest.TestCase):
    def test_electivire(self):
        r = _tags(ELECTIVIRE)
        self.assertEqual(
            r["tags"], ["dmg_plus:100:if:own_active_extra_energy_at_least:2"])
        self.assertEqual(r["unsupported"], [])

    def test_tangrowth(self):
        r = _tags(TANGROWTH)
        self.assertEqual(
            r["tags"], ["dmg_plus:140:if:own_active_extra_energy_at_least:2"])
        self.assertEqual(r["unsupported"], [])

    def test_extra_without_cost_wording_unmodeled(self):
        """只写 extra Energy、没写 in addition to this attack's cost → 不猜"""
        r = _tags(EXTRA_ONLY)
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])


class TestTeamReduceParse(unittest.TestCase):
    def test_curly_wall(self):
        r = _tags(CURLY, kind="ability")
        self.assertEqual(
            r["tags"],
            ["fx:team_reduce:60:basic:colorless:other:bouffalant:nonstack"])
        self.assertEqual(r["unsupported"], [])

    def test_gear_coating(self):
        r = _tags(GEAR, kind="ability")
        self.assertEqual(r["tags"], ["fx:team_reduce:20:energy:metal"])
        self.assertEqual(r["unsupported"], [])

    def test_stone_palace(self):
        r = _tags(STONE, kind="ability")
        self.assertEqual(
            r["tags"],
            ["fx:team_reduce:30:faction:stevens:source_bench:nonstack"])
        self.assertEqual(r["unsupported"], [])

    def test_temporary_reduction_unmodeled(self):
        """「During your opponent's next turn」是时限制，不是常驻 → 仍不猜"""
        r = _tags(IRON_DEFENDER, kind="item")
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])


if __name__ == "__main__":
    unittest.main()
