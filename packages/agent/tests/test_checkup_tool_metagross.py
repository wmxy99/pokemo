# -*- coding: utf-8 -*-
"""第十三批（2026-10-28）· 解析侧：Checkup 改写 / 道具静态加伤 / 具名后备条件

覆盖：
  · ``passive:checkup_plus:<status>:<n>[:side:opp][:active_only]``  Pecharunt / Magmortar
  · ``checkup_set:poison:80``                                       Nidoking ex
  · ``tool:dmg_plus:N[:target:ex][:holder:…]``                      4 张道具
  · ``dmg_plus:150:if:own_bench_named_all:beldum+metang``           Metagross
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

PECHARUNT = ("As long as this Pokémon is in the Active Spot, put 5 more damage "
             "counters on your opponent’s Poisoned Pokémon during Pokémon "
             "Checkup.")
MAGMORTAR = ("During Pokémon Checkup, put 3 more damage counters on your "
             "opponent’s Burned Pokémon.")
NIDOKING = ("Your opponent’s Active Pokémon is now Poisoned. During Pokémon "
            "Checkup, put 8 damage counters on that Pokémon instead of 1.")
MAX_BELT = ("Attacks used by the Pokémon this card is attached to do 50 more "
            "damage to your opponent’s Active Pokémon {ex} (before applying "
            "Weakness and Resistance).")
LIGHT_BALL = ("Attacks used by the Pikachu {ex} this card is attached to do 50 "
              "more damage to your opponent’s Active Pokémon {ex} (before "
              "applying Weakness and Resistance).")
MOCHI = ("Attacks used by the Poisoned Pokémon this card is attached to do 40 "
         "more damage to your opponent’s Active Pokémon (before applying "
         "Weakness and Resistance).")
BANGLE = ("If the Pokémon this card is attached to doesn’t have a Rule Box, "
          "the attacks it uses do 30 more damage to your opponent’s Active "
          "Pokémon {ex} (before applying Weakness and Resistance). (Pokémon "
          "{ex}, Pokémon {V}, etc. have Rule Boxes.)")
METAGROSS = ("If Beldum and Metang are on your Bench, this attack does 150 "
             "more damage.")


def _tags(text, kind="ability", name="X"):
    return E.to_engine_tags([E.parse_rule(text, kind=kind, name=name)])


class TestCheckupPlusParse(unittest.TestCase):
    def test_pecharunt(self):
        r = _tags(PECHARUNT)
        self.assertEqual(
            r["tags"], ["passive:checkup_plus:poison:50:side:opp:active_only"])
        self.assertEqual(r["unsupported"], [])

    def test_magmortar(self):
        r = _tags(MAGMORTAR)
        self.assertEqual(r["tags"], ["passive:checkup_plus:burn:30:side:opp"])
        self.assertEqual(r["unsupported"], [])

    def test_own_side_not_claimed(self):
        """「**your** Poisoned Pokémon」不是对手 → 必须保持未建模"""
        r = _tags(PECHARUNT.replace("your opponent’s", "your"))
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])

    def test_nidoking_attack(self):
        r = _tags(NIDOKING, kind="attack")
        self.assertEqual(r["tags"], ["poison", "checkup_set:poison:80"])
        self.assertEqual(r["unsupported"], [])


class TestToolDamagePlusParse(unittest.TestCase):
    def test_maximum_belt(self):
        r = _tags(MAX_BELT, kind="card")
        self.assertEqual(r["tags"], ["tool:dmg_plus:50:target:ex"])
        self.assertEqual(r["unsupported"], [])

    def test_light_ball(self):
        r = _tags(LIGHT_BALL, kind="card")
        self.assertEqual(
            r["tags"], ["tool:dmg_plus:50:holder:name:pikachu:target:ex"])
        self.assertEqual(r["unsupported"], [])

    def test_binding_mochi(self):
        r = _tags(MOCHI, kind="card")
        self.assertEqual(r["tags"], ["tool:dmg_plus:40:holder:status:poison"])
        self.assertEqual(r["unsupported"], [])

    def test_brave_bangle(self):
        r = _tags(BANGLE, kind="card")
        self.assertEqual(
            r["tags"], ["tool:dmg_plus:30:holder:no_rule_box:target:ex"])
        self.assertEqual(r["unsupported"], [])

    def test_other_holder_still_unmodeled(self):
        """阵营/其它卡名持有者限定暂不支持 → 不得当成无条件的 +N"""
        text = LIGHT_BALL.replace("the Pikachu {ex}",
                                  "the Team Rocket's Pokémon")
        r = _tags(text, kind="card")
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])


class TestMetagrossParse(unittest.TestCase):
    def test_named_bench_pair(self):
        r = _tags(METAGROSS, kind="attack")
        self.assertEqual(
            r["tags"], ["dmg_plus:150:if:own_bench_named_all:beldum+metang"])
        self.assertEqual(r["unsupported"], [])


if __name__ == "__main__":
    unittest.main()
