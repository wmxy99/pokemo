# -*- coding: utf-8 -*-
"""第十四批（2026-10-28）· 解析侧：伤害指示物直放 + Checkup 群体放置

覆盖：
  · ``counters:<n>[:per:<counter>]``                      Haunter / Alakazam
  · ``passive:checkup_spread:<dmg>:<scope>[:except:|:active_only]``  Froslass / Tyranitar
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

HAUNTER = "Place 3 damage counters on your opponent’s Active Pokémon."
ALAKAZAM = ("Place 2 damage counters on your opponent’s Active Pokémon for "
            "each card in your hand.")
FROSLASS = ("During Pokémon Checkup, put 1 damage counter on each Pokémon that "
            "has an Ability (both yours and your opponent’s), except any "
            "Froslass.")
TYRANITAR = ("During Pokémon Checkup, if this Pokémon is in the Active Spot, put "
             "2 damage counters on each of your opponent’s Basic Pokémon.")


def _tags(text, kind="ability", name="X"):
    return E.to_engine_tags([E.parse_rule(text, kind=kind, name=name)])


class TestCountersOnActive(unittest.TestCase):
    def test_haunter(self):
        r = _tags(HAUNTER, kind="attack")
        self.assertEqual(r["tags"], ["counters:3"])
        self.assertEqual(r["unsupported"], [])

    def test_alakazam_per_hand(self):
        r = _tags(ALAKAZAM, kind="attack")
        self.assertEqual(r["tags"], ["counters:2:per:own_hand"])
        self.assertEqual(r["unsupported"], [])

    def test_conditional_still_unmodeled(self):
        """带前置条件的直放指示物不认（`counters:` 是无条件语义）"""
        text = ("If your opponent’s Active Pokémon is a Pokémon {ex}, place 3 "
                "damage counters on your opponent’s Active Pokémon.")
        r = _tags(text, kind="attack")
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])

    def test_bench_counters_not_claimed(self):
        """后备的指示物放置仍走既有 spread/snipe 口径，不得被本族抢走"""
        text = ("Put 2 damage counters on each of your opponent’s Benched "
                "Pokémon.")
        r = _tags(text, kind="attack")
        self.assertNotIn("counters:2", r["tags"])


class TestCheckupSpread(unittest.TestCase):
    def test_froslass(self):
        r = _tags(FROSLASS)
        self.assertEqual(
            r["tags"],
            ["passive:checkup_spread:10:ability_all:except:froslass"])
        self.assertEqual(r["unsupported"], [])

    def test_tyranitar(self):
        r = _tags(TYRANITAR)
        self.assertEqual(
            r["tags"],
            ["passive:checkup_spread:20:opp_basic:active_only"])
        self.assertEqual(r["unsupported"], [])

    def test_own_side_rejected(self):
        """「each of **your** Basic Pokémon」不是对手侧 → 保持未建模"""
        text = TYRANITAR.replace("your opponent’s", "your")
        r = _tags(text)
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])


class TestCountersAnyTarget(unittest.TestCase):
    """第二十批（2026-10-29）：`1 of your opponent's Pokémon` + 特性自毁 / 进化触发"""

    DUSCLOPS = ("Once during your turn, you may put 5 damage counters on 1 of "
                "your opponent’s Pokémon. If you use this Ability, this Pokémon "
                "is Knocked Out.")
    DUSKNOIR = DUSCLOPS.replace("5 damage", "13 damage")
    GOLBAT = ("When you play this Pokémon from your hand to evolve 1 of your "
              "Pokémon during your turn, you may put 2 damage counters on 1 of "
              "your opponent’s Pokémon.")
    BRAMBLIN = "Place 1 damage counter on 1 of your opponent’s Pokémon."
    SINISTCHA = ("Put 2 damage counters on 1 of your opponent’s Pokémon for each "
                 "Basic {G} Energy card in your discard pile. Then, shuffle those "
                 "Energy cards into your deck.")

    def test_dusclops_selfko(self):
        r = _tags(self.DUSCLOPS)
        self.assertEqual(r["tags"], ["counters:5:scope:opp:1:selfko"])
        self.assertEqual(r["unsupported"], [])

    def test_dusknoir_selfko(self):
        r = _tags(self.DUSKNOIR)
        self.assertEqual(r["tags"], ["counters:13:scope:opp:1:selfko"])

    def test_golbat_evolve_act(self):
        r = _tags(self.GOLBAT)
        self.assertEqual(r["tags"], ["evolve_act:counters:2:scope:opp:1"])
        self.assertEqual(r["unsupported"], [])

    def test_bramblin_any_target(self):
        r = _tags(self.BRAMBLIN, kind="attack")
        self.assertEqual(r["tags"], ["counters:1:scope:opp:1"])

    def test_shuffle_back_energy_is_modeled(self):
        """「Then, shuffle those Energy cards into your deck」已建模（第二十八批）

        弃牌堆基本能量在计数后**洗回牌库**由 `energy_to_deck:` 承载；若只建模
        指示物、丢掉洗回，下一次攻击会把同一批能量再数一遍（静默加强）。
        """
        r = _tags(self.SINISTCHA, kind="attack")
        self.assertEqual(sorted(r["tags"]),
                         ["counters:2:per:own_discard_energy_grass:scope:opp:1",
                          "energy_to_deck:grass:all"])
        self.assertEqual(r["unsupported"], [])

    def test_conditional_ability_rejected(self):
        """非两种固定触发前缀的特性（带 if 条件）不得被认领"""
        text = ("If your opponent’s Active Pokémon has no damage counters on it, "
                "you may put 3 damage counters on 1 of your opponent’s Pokémon.")
        r = _tags(text)
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])


if __name__ == "__main__":
    unittest.main()
