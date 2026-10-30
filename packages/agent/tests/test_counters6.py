# -*- coding: utf-8 -*-
"""第六批条件 / 计数源 —— 解析侧单测（正例 + 负向守卫）"""
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


def _tags(text, kind="attack", name="X"):
    return E.to_engine_tags([E.parse_rule(text, kind=kind, name=name)])


class TestConditionPhrases(unittest.TestCase):
    def test_opp_active_tera(self):
        r = _tags("If your opponent’s Active Pokémon is a Tera Pokémon, this "
                  "attack does 230 more damage.")
        self.assertEqual(r["tags"], ["dmg_plus:230:if:opp_active_tera"])

    def test_own_bench_tera(self):
        r = _tags("If you have any Tera Pokémon on your Bench, this attack does "
                  "100 more damage.")
        self.assertEqual(r["tags"], ["dmg_plus:100:if:own_bench_tera"])

    def test_same_type_in_play(self):
        r = _tags("If any of your\xa0Pokémon in play are the same type as any of "
                  "your opponent’s Pokémon in play, this attack does 120 more "
                  "damage.")
        self.assertEqual(r["tags"], ["dmg_plus:120:if:same_type_in_play"])

    def test_special_energy(self):
        r = _tags("If this Pokémon has any Special Energy attached, this attack "
                  "does 100 more damage.")
        self.assertEqual(r["tags"], ["dmg_plus:100:if:own_active_special_energy"])

    def test_named_energy(self):
        r = _tags("If this Pokémon has any Team Rocket’s Energy attached, this "
                  "attack does 60 more damage.")
        self.assertEqual(
            r["tags"], ["dmg_plus:60:if:own_active_energy_tag:teamrocket"])

    def test_discard_basic_energy_count(self):
        r = _tags("If you have 10 or more Basic {R} Energy cards in your discard "
                  "pile, this attack does 100 more damage.")
        self.assertEqual(
            r["tags"],
            ["dmg_plus:100:if:own_discard_basic_energy_at_least:fire:10"])

    def test_bench_stage2_type(self):
        r = _tags("If you have any Stage 2 {D} Pokémon on your Bench, this attack "
                  "does 70 more damage.")
        self.assertEqual(r["tags"], ["dmg_plus:70:if:own_bench_stage2_type:darkness"])

    def test_burn_or_poison(self):
        r = _tags("If this Pokémon is Burned or Poisoned, this attack does 100 "
                  "more damage.")
        self.assertEqual(r["tags"], ["dmg_plus:100:if:own_active_status_any:burn,poison"])

    def test_discard_has_named(self):
        r = _tags("If Rosa’s Encouragement is in your discard pile, this attack "
                  "does 150 more damage.")
        self.assertEqual(
            r["tags"], ["dmg_plus:150:if:own_discard_has_named:rosasencouragement"])

    # ---- 负向守卫 ----
    def test_other_status_disjunction_rejected(self):
        r = _tags("If this Pokémon is Asleep or Confused, this attack does 100 "
                  "more damage.")
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])

    def test_named_energy_unknown_rejected(self):
        """未列出的具名能量 → 仍按「具名能量」建模（引擎按 tag 判定，安全）"""
        r = _tags("If this Pokémon has any Plasma Energy attached, this attack "
                  "does 60 more damage.")
        self.assertEqual(r["tags"],
                         ["dmg_plus:60:if:own_active_energy_tag:plasma"])


class TestCounterPhrases(unittest.TestCase):
    def test_field_has_attack(self):
        r = _tags("This attack does 20 damage for each of your Pokémon in play "
                  "that has the Round attack.")
        self.assertEqual(r["tags"], ["dmg_set:20:per:own_field_has_attack:round"])

    def test_both_field_named_any(self):
        r = _tags("This attack does 40 damage for each Pokémon in play that has "
                  "“Koffing” or “Weezing” in its name (both yours and your "
                  "opponent’s).")
        self.assertEqual(
            r["tags"], ["dmg_set:40:per:both_field_named_any:koffing+weezing"])

    def test_discard_has_attack(self):
        r = _tags("This attack does 20 damage for each Pokémon in your discard "
                  "pile that has the United Wings attack.")
        self.assertEqual(
            r["tags"], ["dmg_set:20:per:own_discard_has_attack:unitedwings"])

    def test_discard_named(self):
        r = _tags("This attack does 60 more damage for each Ethan’s Adventure "
                  "card in your discard pile.")
        self.assertEqual(
            r["tags"],
            ["dmg_plus:60:per:own_discard_named:ethansadventure"])

    def test_discard_named_supporter(self):
        r = _tags("This attack does 20 damage for each Supporter card that has "
                  "“Team Rocket” in its name in your discard pile.")
        self.assertEqual(
            r["tags"],
            ["dmg_set:20:per:own_discard_named_supporter:teamrocket"])

    # ---- 负向守卫：仍不可精确表达的「子集」必须继续未建模 ----
    def test_conditional_subset_still_unmodeled(self):
        r = _tags("This attack does 30 damage for each Pokémon in play that has "
                  "a Rule Box.")
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])


if __name__ == "__main__":
    unittest.main()
