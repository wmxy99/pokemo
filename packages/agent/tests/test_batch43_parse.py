# -*- coding: utf-8 -*-
"""第四十三批（2026-11-03）· K2 解析用例：两个限定计数源

  · Roaring Moon「for each Ancient card in your discard pile」→
    `dmg_plus:10:per:own_discard_category:ancient`
  · Iono's Voltorb「for each {L} Energy attached to all of your Iono's Pokémon」→
    `dmg_plus:20:per:own_field_named_energy:iono:lightning`

负向守卫：
  · Ancient **不是卡名** → 不得落成 `own_discard_named:ancient`（恒 0）；
  · 没有「Iono's Pokémon」具名限定的能量短语 → 不得套用本计数源。
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


def _tags(text: str) -> list:
    return E.to_engine_tags([E.parse_rule(text, kind="attack", name="X")])["tags"]


class TestBatch43Counters(unittest.TestCase):
    def test_ancient_card_in_discard(self):
        self.assertEqual(
            _tags("This attack does 10 more damage for each Ancient card in your "
                  "discard pile."),
            ["dmg_plus:10:per:own_discard_category:ancient"])

    def test_named_faction_energy(self):
        self.assertEqual(
            _tags("This attack does 20 more damage for each {L} Energy attached "
                  "to all of your Iono's Pokémon."),
            ["dmg_plus:20:per:own_field_named_energy:iono:lightning"])

    def test_plain_energy_phrase_untouched(self):
        """没有具名阵营限定 → 仍走通用能量计数（不得套用新计数源）"""
        tags = _tags("This attack does 20 more damage for each {L} Energy "
                     "attached to your Pokémon.")
        self.assertFalse([t for t in tags if "own_field_named_energy" in t], tags)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
