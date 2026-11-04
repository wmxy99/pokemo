# -*- coding: utf-8 -*-
"""第五十五批（2026-11-04）· 解析用例：击倒族三个限定

  · ``opp_counters:<n>``            恰好 n 个伤害指示物（Mega Absol ex）
  · ``opp_active:if:senergy``       对手战斗位带**特殊**能量（Haxorus）
  · ``opp_all:if:hp_le:<n>``        **每一只**剩余 HP ≤ n（Yveltal ex）

⚠ 「each」与「1 of」在卡面上只差一个词，落错就是多杀削成单杀 / 单杀放大成多杀。
"""
from __future__ import annotations
import unittest

from agent import effects as E


def _tags(text, kind="attack", name="x"):
    return E.to_engine_tags([E.parse_rule(text, kind=kind, name=name)])


class TestKnockoutForms(unittest.TestCase):
    def test_exact_counters(self):
        self.assertEqual(
            _tags("If your opponent’s Active Pokémon has exactly 6 damage "
                  "counters on it, that Pokémon is Knocked Out.")["tags"],
            ["knockout:opp_counters:6"])

    def test_special_energy_gate(self):
        self.assertEqual(
            _tags("If your opponent’s Active Pokémon has any Special Energy "
                  "attached, it is Knocked Out.")["tags"],
            ["knockout:opp_active:if:senergy"])

    def test_each_hp_threshold(self):
        self.assertEqual(
            _tags("Knock Out each of your opponent’s Pokémon that has 50 HP or "
                  "less remaining.")["tags"],
            ["knockout:opp_all:if:hp_le:50"])

    def test_basic_gate_unchanged(self):
        """近邻写法不能被新分支吞掉（无条件/基础限定仍是旧口径）"""
        self.assertEqual(
            _tags("If your opponent’s Active Pokémon is a Basic Pokémon, it is "
                  "Knocked Out.")["tags"],
            ["knockout:opp_active:basic"])

    def test_single_target_not_widened(self):
        """「1 of」不能落成 `opp_all`（会把单杀放大成多杀）"""
        self.assertEqual(
            _tags("Knock Out 1 of your opponent’s Pokémon.")["tags"],
            ["knockout:opp_any"])
