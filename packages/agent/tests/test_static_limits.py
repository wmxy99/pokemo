# -*- coding: utf-8 -*-
"""静态规则 / 下回合限制 —— 解析侧单测（正例 + 负向守卫）"""
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
from agent.fx import fx_attack_cost as AC  # noqa: E402

SAND = ("During your opponent’s next turn, if the Defending Pokémon tries to "
        "use an attack, your opponent flips a coin. If tails, that attack "
        "doesn’t happen.")
DRUM = ("During your opponent’s next turn, attacks used by the Defending "
        "Pokémon cost {C} more, and its Retreat Cost is {C} more.")
SHRED = ("This attack’s damage isn’t affected by any effects on your "
         "opponent’s Active Pokémon.")
SEASON = ("Blood Moon used by this Pokémon costs {C} less for each Prize card "
          "your opponent has taken.")
HUSTLE = ("Attacks used by this Pokémon cost {C} less for each of your "
          "opponent’s Benched Pokémon.")


def _tags(text, kind="attack", name="X"):
    return E.to_engine_tags([E.parse_rule(text, kind=kind, name=name)])


class TestStaticRules(unittest.TestCase):
    def test_attack_denial(self):
        self.assertEqual(_tags(SAND)["tags"], ["attack_denial:coin:turn"])

    def test_cost_up_bundle(self):
        self.assertEqual(_tags(DRUM)["tags"],
                         ["attack_cost_up:opp:turn:1",
                          "retreat_cost_up:opp:turn:1"])

    def test_ignore_effects(self):
        self.assertEqual(_tags(SHRED)["tags"], ["ignore_effects"])

    def test_self_less_named(self):
        self.assertEqual(_tags(SEASON, kind="ability")["tags"],
                         ["fx:attack_cost:self:less:per:opp_prizes_taken"
                          ":name:bloodmoon"])

    def test_self_less_all_attacks(self):
        self.assertEqual(_tags(HUSTLE, kind="ability")["tags"],
                         ["fx:attack_cost:self:less:per:opp_bench"])

    # ---- 负向守卫 ----
    def test_denial_without_tails_clause_rejected(self):
        text = ("During your opponent’s next turn, if the Defending Pokémon "
                "tries to use an attack, flip a coin.")
        self.assertEqual(_tags(text)["tags"], [])

    def test_cost_up_only_one_clause_rejected(self):
        """只写「费用 +1」而没写撤退 → 整句未建模（不得只做一半）"""
        text = ("During your opponent’s next turn, attacks used by the "
                "Defending Pokémon cost {C} more.")
        self.assertEqual(_tags(text)["tags"], [])

    def test_self_less_unknown_counter_rejected(self):
        text = ("Attacks used by this Pokémon cost {C} less for each of your "
                "Benched Pokémon.")
        self.assertIsNone(AC.detect(text, "ability", "X"))

    def test_self_less_non_colorless_rejected(self):
        text = ("Attacks used by this Pokémon cost {R} less for each of your "
                "opponent’s Benched Pokémon.")
        self.assertIsNone(AC.detect(text, "ability", "X"))


if __name__ == "__main__":
    unittest.main()
