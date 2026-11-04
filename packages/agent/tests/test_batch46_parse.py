# -*- coding: utf-8 -*-
"""第四十六批（2026-11-03）· K2 解析用例：限时减伤族

  · Aurorus「Tundra Wall」→ ``fx:team_reduce:50:energy:water:nonstack``
    （全队常驻减伤；末尾「The effect … doesn't stack」必须落 `:nonstack`）
  · Iron Defender → ``team_shield:reduce:30:type:metal``（时限制、玩家级）
  · Acerola's Mischief → ``shield_choose:prevent_all:from:ex:effects:gate:opp_prizes_le:2``

负向守卫：
  · 缺「during your opponent's next turn」的全队减伤不得落成 `team_shield`；
  · 缺能量条件的常驻减伤不得落成 `fx:team_reduce:…:energy:`；
  · 奖赏数条件写成「exactly 2」不是「2 or fewer」→ 不认领。

运行：cd packages/agent && python -m unittest tests.test_batch46_parse -v
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


def _m(text: str, kind: str) -> dict:
    sp = E.parse_rule(text, kind=kind, name="X")
    out = E.to_engine_tags([sp])
    return {"tags": out["tags"],
            "unsupported": [u["reason"] for u in out["unsupported"]]}


AURORUS = ("All of your Pokémon that have any {W} Energy attached take 50 less "
           "damage from attacks from your opponent's Pokémon (after applying "
           "Weakness and Resistance). The effect of Tundra Wall doesn't stack.")
IRON = ("During your opponent's next turn, all of your {M} Pokémon take 30 less "
        "damage from attacks from your opponent's Pokémon (after applying "
        "Weakness and Resistance). (This includes new Pokémon that come into play.)")
ACEROLA = ("You can use this card only if your opponent has 2 or fewer Prize "
           "cards remaining. Choose 1 of your Pokémon in play. During your "
           "opponent's next turn, prevent all damage from and effects of attacks "
           "done to that Pokémon by your opponent's Pokémon {ex}.")


class TestAurorusTundraWall(unittest.TestCase):
    def test_tundra_wall_parsed(self):
        r = _m(AURORUS, "ability")
        self.assertEqual(r["tags"],
                         ["fx:team_reduce:50:energy:water:nonstack"])
        self.assertEqual(r["unsupported"], [])

    def test_without_energy_condition_rejected(self):
        t = ("All of your Pokémon take 50 less damage from attacks from your "
             "opponent's Pokémon (after applying Weakness and Resistance).")
        r = _m(t, "ability")
        self.assertFalse([x for x in r["tags"] if "team_reduce" in x])


class TestIronDefender(unittest.TestCase):
    def test_team_shield_parsed(self):
        r = _m(IRON, "item")
        self.assertEqual(r["tags"], ["team_shield:reduce:30:type:metal"])
        self.assertEqual(r["unsupported"], [])

    def test_persistent_form_not_team_shield(self):
        """缺「during your opponent's next turn」→ 不是时限制，不得落 team_shield"""
        t = ("All of your {M} Pokémon take 30 less damage from attacks from your "
             "opponent's Pokémon (after applying Weakness and Resistance).")
        r = _m(t, "item")
        self.assertFalse([x for x in r["tags"] if x.startswith("team_shield:")])


class TestAcerolaMischief(unittest.TestCase):
    def test_shield_choose_parsed(self):
        r = _m(ACEROLA, "supporter")
        self.assertEqual(
            r["tags"],
            ["shield_choose:prevent_all:from:ex:effects:gate:opp_prizes_le:2"])
        self.assertEqual(r["unsupported"], [])

    def test_exactly_gate_rejected(self):
        """「exactly 2」不是「2 or fewer」→ 前置条件语义不同，不得认领"""
        t = ACEROLA.replace("2 or fewer", "exactly 2")
        r = _m(t, "supporter")
        self.assertFalse([x for x in r["tags"] if x.startswith("shield_choose:")])


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
