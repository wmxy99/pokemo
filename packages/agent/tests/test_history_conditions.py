# -*- coding: utf-8 -*-
"""第七批：回合历史条件 / 招式失败门 / 对手手牌类别 —— 解析侧单测"""
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


class TestHistoryPhrases(unittest.TestCase):
    def test_moved_to_active(self):
        r = _tags("If this Pokémon moved from your Bench to the Active Spot "
                  "this turn, this attack does 120 more damage.")
        self.assertEqual(r["tags"], ["dmg_plus:120:if:moved_to_active"])

    def test_evolved_from(self):
        r = _tags("If this Pokémon evolved from Gimmighoul during this turn, this "
                  "attack does 90 more damage.")
        self.assertEqual(r["tags"], ["dmg_plus:90:if:evolved_from:gimmighoul"])

    def test_evolved_generic(self):
        r = _tags("If this Pokémon evolved during this turn, this attack does 50 "
                  "more damage.")
        self.assertEqual(r["tags"], ["dmg_plus:50:if:evolved_this_turn"])

    def test_healed(self):
        r = _tags("If this Pokémon was healed during this turn, this attack does "
                  "100 more damage.")
        self.assertEqual(r["tags"], ["dmg_plus:100:if:healed_this_turn"])

    def test_used_attack_last_turn(self):
        r = _tags("If this Pokémon used Spiky Rolling during your last turn, this "
                  "attack does 80 more damage.")
        self.assertEqual(r["tags"],
                         ["dmg_plus:80:if:used_attack_last_turn:spikyrolling"])

    def test_other_ancient(self):
        r = _tags("If 1 of your other Ancient Pokémon used an attack during your "
                  "last turn, this attack does 150 more damage.")
        self.assertEqual(
            r["tags"], ["dmg_plus:150:if:own_other_ancient_attacked_last_turn"])

    def test_prizes_last_turn_counter(self):
        r = _tags("This attack does 60 more damage for each Prize card your "
                  "opponent took during their last turn.")
        self.assertEqual(r["tags"],
                         ["dmg_plus:60:per:opp_prizes_taken_last_turn"])

    def test_opp_hand_kind(self):
        r = _tags("Your opponent reveals their hand. This attack does 50 damage "
                  "for each Trainer card you find there.")
        self.assertEqual(r["tags"], ["dmg_set:50:per:opp_hand_kind:trainer"])
        r2 = _tags("Your opponent reveals their hand, and this attack does 80 "
                   "damage for each Energy card you find there.")
        self.assertEqual(r2["tags"], ["dmg_set:80:per:opp_hand_kind:energy"])


class TestNullifyGates(unittest.TestCase):
    CASES = (
        ("If there is no Stadium in play, this attack does nothing.",
         "not:stadium_in_play"),
        ("If you don't have exactly 7 cards in your hand, this attack does "
         "nothing.", "not:own_hand_exactly:7"),
        ("If you don't have the same number of cards in your hand as your "
         "opponent, this attack does nothing.", "not:own_hand_eq_opp_hand"),
        ("If your opponent's Active Pokémon has no damage counters on it before "
         "this attack does damage, this attack does nothing.",
         "not:opp_active_has_damage"),
        ("If your opponent's Active Pokémon isn't a Pokémon {ex}, this attack "
         "does nothing.", "not:opp_active_is_ex"),
        ("If your opponent's Active Pokémon isn't Burned, this attack does "
         "nothing.", "not:opp_active_status:burn"),
        ("If you don't have Lunatone on your Bench, this attack does nothing.",
         "not:own_bench_name:lunatone"),
        ("If you have 4 or fewer Benched Pokémon, this attack does nothing.",
         "own_bench_at_most:4"),
        ("If your opponent doesn't have exactly 3 or 4 Prize cards remaining, "
         "this attack does nothing.", "not:opp_prizes_in:3,4"),
        ("If you don't have 10 or more Basic {F} Energy cards in your discard "
         "pile, this attack does nothing.",
         "not:own_discard_basic_energy_at_least:fighting:10"),
    )

    def test_gates(self):
        for text, cond in self.CASES:
            with self.subTest(text=text[:40]):
                r = _tags(text)
                self.assertEqual(r["tags"], [f"nullify_if:{cond}"])
                self.assertEqual(r["unsupported"], [])

    def test_conjunction_single_condition(self):
        """Mesprit：合取词「Uxie and Azelf」只能产出**一条**条件（不得再拆单名）"""
        r = _tags("If you don't have Uxie and Azelf on your Bench, this attack "
                  "does nothing.")
        self.assertEqual(r["tags"],
                         ["nullify_if:not:own_bench_named_all:uxie+azelf"])

    # ---- 负向守卫：条件不可求值 → 必须仍登记未建模 ----
    def test_unknown_gate_condition_stays_unmodeled(self):
        r = _tags("If you don't have any Pokémon in play that has a Rule Box, "
                  "this attack does nothing.")
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])


if __name__ == "__main__":
    unittest.main()
