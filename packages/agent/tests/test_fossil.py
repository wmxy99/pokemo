# -*- coding: utf-8 -*-
"""化石卡 + 招式费用抬高 —— 解析侧单测（正例 + 负向守卫）

化石卡必须**整段认领**（两句缺一不可、不得多出第三句）；费用抬高只认
{ C } 单符号、对手基础宝可梦两种限定。
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
from agent.fx import fx_attack_cost as AC  # noqa: E402

FOSSIL = ("Play this card as if it were a 60-HP Basic {C} Pokémon. This card "
          "can’t be affected by any Special Conditions and can’t retreat.\n\n"
          "At any time during your turn, you may discard this card from play.")


def _tags(text, kind="item", name="X"):
    r = E.parse_effects(text, kind=kind, name=name)
    return E.to_engine_tags(r["specs"])


class TestFossilParse(unittest.TestCase):
    def test_whole_text_claimed(self):
        r = _tags(FOSSIL)
        self.assertEqual(r["tags"], ["fossil:60:colorless"])
        self.assertEqual(r["unsupported"], [])

    def test_no_placeholder_rule_left(self):
        spec = E.parse_rule(FOSSIL, kind="item", name="Antique Root Fossil")
        types = [a.get("type") for a in spec["actions"]]
        self.assertEqual(types, ["rule"])
        self.assertTrue(spec["actions"][0]["extra"].get("fossil"))
        self.assertFalse(E._needs_fx(spec))

    def test_other_hp_and_type(self):
        r = _tags(FOSSIL.replace("60-HP Basic {C}", "70-HP Basic {F}"))
        self.assertEqual(r["tags"], ["fossil:70:fighting"])

    # ---- 负向守卫 ----
    def test_missing_discard_sentence_rejected(self):
        text = FOSSIL.split("\n\n")[0]
        self.assertEqual(E._detect_fossil(text), [])

    def test_extra_sentence_rejected(self):
        self.assertEqual(E._detect_fossil(FOSSIL + " Draw 2 cards."), [])

    def test_unknown_symbol_rejected(self):
        self.assertEqual(E._detect_fossil(FOSSIL.replace("{C}", "{Z}")), [])


class TestAttackCostDetect(unittest.TestCase):
    def test_opponent_basic_cost_more(self):
        r = _tags("As long as this Pokémon is in the Active Spot, attacks used "
                  "by your opponent’s Basic Pokémon cost {C} more.",
                  kind="ability", name="Primal Root")
        self.assertEqual(r["tags"], ["fx:attack_cost:opp_basic:1:colorless"])
        self.assertEqual(r["unsupported"], [])

    def test_detector_direct(self):
        hit = AC.detect("as long as this pokemon is in the active spot, attacks "
                        "used by your opponent's basic pokemon cost {c} more.",
                        "ability", "X")
        self.assertEqual(hit["family"], "attack_cost")

    # ---- 负向守卫 ----
    def test_non_colorless_rejected(self):
        self.assertIsNone(AC.detect(
            "as long as this pokemon is in the active spot, attacks used by "
            "your opponent's basic pokemon cost {r} more.", "ability", "X"))

    def test_all_pokemon_rejected(self):
        """「your opponent's Pokémon」不带 Basic → 范围不同，不得近似成 Basic"""
        self.assertIsNone(AC.detect(
            "as long as this pokemon is in the active spot, attacks used by "
            "your opponent's pokemon cost {c} more.", "ability", "X"))

    def test_not_ability_kind_rejected(self):
        self.assertIsNone(AC.detect(
            "as long as this pokemon is in the active spot, attacks used by "
            "your opponent's basic pokemon cost {c} more.", "attack", "X"))


if __name__ == "__main__":
    unittest.main()
