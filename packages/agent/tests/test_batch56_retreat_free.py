# -*- coding: utf-8 -*-
"""第五十六批（2026-11-04）· 解析用例：**撤退费用归零**（fx:retreat_free）

三种常驻形态与 `passive:no_retreat`（只作用于持有者自己）严格区分：
  · ``fx:retreat_free:energy:<color>``   己方带该属性能量者（Archaludon）
  · ``fx:retreat_free:basic``            己方全部基础宝可梦（Latias ex）
  · ``fx:retreat_free:self:if:no_energy`` 持有者无能量时（Ethan's Magcargo）
"""
from __future__ import annotations
import unittest

from agent import effects as E


def _tags(text, kind="ability", name="x"):
    return E.to_engine_tags([E.parse_rule(text, kind=kind, name=name)])


class TestRetreatFree(unittest.TestCase):
    def test_energy_gate(self):
        self.assertEqual(
            _tags("All of your Pokémon that have {M} Energy attached have no "
                  "Retreat Cost.")["tags"],
            ["fx:retreat_free:energy:metal"])

    def test_energy_gate_with_any(self):
        """`any` 可有可无：同一句两种写法（Archaludon / Klinklang 语序）"""
        self.assertEqual(
            _tags("All of your Pokémon that have any {M} Energy attached have "
                  "no Retreat Cost.")["tags"],
            ["fx:retreat_free:energy:metal"])

    def test_basic_team(self):
        self.assertEqual(
            _tags("Your Basic Pokémon in play have no Retreat Cost.")["tags"],
            ["fx:retreat_free:basic"])

    def test_self_no_energy(self):
        self.assertEqual(
            _tags("If this Pokémon has no Energy attached, it has no Retreat "
                  "Cost.")["tags"],
            ["fx:retreat_free:self:if:no_energy"])

    def test_timed_form_rejected(self):
        """限时写法是 `shield:*` 语义，落成常驻 = 永久免费撤退"""
        r = _tags("During your opponent’s next turn, this Pokémon has no "
                  "Retreat Cost.")
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])

    def test_faction_form_rejected(self):
        """按阵营限定（your Team Rocket's Pokémon）本族没有阵营参数 → 拒绝"""
        r = _tags("All of your Team Rocket’s Pokémon have no Retreat Cost.")
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])
