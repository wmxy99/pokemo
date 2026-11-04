# -*- coding: utf-8 -*-
"""第四十九批（2026-11-04）· 引擎用例：特性发动代价（`AbilityDef.cost`）

卡面：「**You must** <代价> **in order to use this Ability.** Once during your
turn, you may …」

五种代价形态，两个关键分界：
  · `discard_self_energy` vs `discard_hand_energy` —— 「from this Pokémon」动场上
    能量、「from your hand」动手牌，写反就是**付错来源**；
  · 代价 vs gate —— 代价必须真的扣掉，当 gate 实现 = 免费发动。
"""
from __future__ import annotations
import random
import unittest

from engine.battle import (Battle, _ability_cost_ok, _parse_ability_cost)
from engine.cards import (AbilityDef, EnergyDef, EnergyRef, PokemonDef,
                          TrainerDef, TrainerRef, make_pokemon)
from engine.state import GameConfig, PlayerState


def _arena(seed=7):
    cfg = GameConfig()
    b = object.__new__(Battle)
    b.cfg = cfg
    b.seed = seed
    b.rng = random.Random(seed)
    b.players = [PlayerState(name="A", prizes_left=cfg.prizes),
                 PlayerState(name="B", prizes_left=cfg.prizes)]
    b.current = 0
    b.turn_number = 1
    b.turn_counts = [1, 1]
    b.phase = "main"
    b.finished = False
    b.winner = None
    b.win_reason = None
    b.events = []
    b.stadium = None
    b._ko_by_attack = [0, 0]
    b.players[1].active = make_pokemon(
        PokemonDef("B", hp=100, ptype="colorless", attacks=()))
    return b


def _hand_card(name, kind="item"):
    return TrainerRef(TrainerDef("draw:1", kind=kind, name=name))


class TestParseAbilityCost(unittest.TestCase):
    def test_shapes(self):
        self.assertEqual(_parse_ability_cost("discard:2"), ("discard", 2, None))
        self.assertEqual(_parse_ability_cost("hand_bottom:1"),
                         ("hand_bottom", 1, None))
        self.assertEqual(
            _parse_ability_cost("discard_named:1:chillteasertoy"),
            ("discard_named", 1, "chillteasertoy"))
        self.assertEqual(
            _parse_ability_cost("discard_self_energy:1:lightning"),
            ("discard_self_energy", 1, "lightning"))
        self.assertEqual(
            _parse_ability_cost("discard_hand_energy:1:fire"),
            ("discard_hand_energy", 1, "fire"))

    def test_empty_is_none(self):
        self.assertIsNone(_parse_ability_cost(""))

    def test_unknown_raises(self):
        """未知形态抛错，不得静默当作「无代价」（那等于免费发动）"""
        with self.assertRaises(ValueError):
            _parse_ability_cost("sacrifice_a_prize")
        with self.assertRaises(ValueError):
            _parse_ability_cost("discard:0")


class TestCostAvailability(unittest.TestCase):
    def test_discard_counts_hand(self):
        b = _arena()
        p = b.players[0]
        p.active = make_pokemon(PokemonDef("A", hp=100, ptype="water", attacks=()))
        p.hand = [_hand_card(f"C{i}") for i in range(2)]
        self.assertTrue(_ability_cost_ok(p, p.active, "discard:2"))
        self.assertFalse(_ability_cost_ok(p, p.active, "discard:3"))

    def test_named_requires_that_card(self):
        b = _arena()
        p = b.players[0]
        p.active = make_pokemon(PokemonDef("A", hp=100, ptype="psychic", attacks=()))
        p.hand = [_hand_card("Chill Teaser Toy"), _hand_card("Other")]
        self.assertTrue(_ability_cost_ok(p, p.active,
                                        "discard_named:1:chillteasertoy"))
        p.hand = [_hand_card("Other"), _hand_card("Another")]
        self.assertFalse(_ability_cost_ok(p, p.active,
                                         "discard_named:1:chillteasertoy"))

    def test_self_energy_only_counts_basic(self):
        """「from this Pokémon」只认**基本**能量 —— 特殊能量不能当代价"""
        b = _arena()
        p = b.players[0]
        fp = make_pokemon(PokemonDef("A", hp=100, ptype="lightning", attacks=()))
        p.active = fp
        fp.add_energy("lightning", 1)
        self.assertTrue(_ability_cost_ok(p, fp, "discard_self_energy:1:lightning"))
        fp.energy.clear()
        # 只挂一张特殊能量（记在 senergy，不进 energy；tag 非空即特殊）
        fp.senergy.append(EnergyRef(EnergyDef("lightning", tag="senergy:test")))
        self.assertFalse(_ability_cost_ok(p, fp,
                                          "discard_self_energy:1:lightning"))

    def test_self_and_hand_energy_are_different(self):
        b = _arena()
        p = b.players[0]
        fp = make_pokemon(PokemonDef("A", hp=100, ptype="fire", attacks=()))
        p.active = fp
        p.hand = [EnergyRef(EnergyDef("fire"))]
        # 手上有火能量、身上没有 → hand 形态可付，self 形态不可付
        self.assertTrue(_ability_cost_ok(p, fp, "discard_hand_energy:1:fire"))
        self.assertFalse(_ability_cost_ok(p, fp, "discard_self_energy:1:fire"))
        p.hand = []
        fp.add_energy("fire", 1)
        self.assertFalse(_ability_cost_ok(p, fp, "discard_hand_energy:1:fire"))
        self.assertTrue(_ability_cost_ok(p, fp, "discard_self_energy:1:fire"))


class TestPayCost(unittest.TestCase):
    def _with_ability(self, cost, hand_n=3):
        b = _arena()
        p = b.players[0]
        p.active = make_pokemon(PokemonDef(
            "A", hp=100, ptype="fire", attacks=(),
            abilities=(AbilityDef("Test", tag="draw:1", location="field",
                                  cost=cost),)))
        p.hand = [_hand_card(f"C{i}") for i in range(hand_n)]
        p.deck = [EnergyRef(EnergyDef("fire")) for _ in range(5)]
        return b, p

    def test_discard_pays_before_effect(self):
        b, p = self._with_ability("discard:2")
        before = len(p.hand)
        b._pay_ability_cost(p, p.active, "discard:2", {})
        self.assertEqual(len(p.hand), before - 2)
        self.assertEqual(len(p.discard_refs), 2)

    def test_hand_bottom_goes_to_bottom(self):
        b, p = self._with_ability("hand_bottom:1")
        top = p.deck[-1]
        card = p.hand[-1]          # 缺省从手牌末尾取（与「任意 N 张」同口径）
        b._pay_ability_cost(p, p.active, "hand_bottom:1", {})
        self.assertIs(p.deck[0], card, "「bottom of your deck」= 列表头")
        self.assertIs(p.deck[-1], top, "牌库顶不得被改动")

    def test_self_energy_leaves_field(self):
        b, p = self._with_ability("discard_self_energy:1:fire")
        p.active.add_energy("fire", 2)
        b._pay_ability_cost(p, p.active, "discard_self_energy:1:fire", {})
        self.assertEqual(p.active.energy.get("fire"), 1)
        self.assertEqual(len(p.discard_refs), 1)

    def test_unpayable_raises(self):
        b, p = self._with_ability("discard:9", hand_n=1)
        with self.assertRaises(ValueError):
            b._pay_ability_cost(p, p.active, "discard:9", {})

    def test_do_ability_charges_cost(self):
        b, p = self._with_ability("discard:2", hand_n=3)
        before = len(p.hand)
        r = b._do_ability(p, {"name": "Test", "tag": "draw:1", "pos": 0})
        self.assertTrue(r["ok"], r.get("error"))
        # 付帐 2 张 + 效果抽 1 张 → 净 −1
        self.assertEqual(len(p.hand), before - 2 + 1)

    def test_do_ability_refuses_when_unpayable(self):
        b, p = self._with_ability("discard:9", hand_n=1)
        with self.assertRaises(ValueError):
            b._do_ability(p, {"name": "Test", "tag": "draw:1", "pos": 0})

    def test_legals_exclude_unpayable(self):
        b, p = self._with_ability("discard:9", hand_n=1)
        names = [a.get("name") for a in b._ability_legals(p, b.players[1])]
        self.assertNotIn("Test", names)
        b2, p2 = self._with_ability("discard:1", hand_n=3)
        names2 = [a.get("name") for a in b2._ability_legals(p2, b2.players[1])]
        self.assertIn("Test", names2)


class TestAbilityDrawTo(unittest.TestCase):
    """特性侧 `draw_to:N`（第四十九批补白）"""

    def test_draws_until_n(self):
        b = _arena()
        p = b.players[0]
        p.active = make_pokemon(PokemonDef(
            "A", hp=100, ptype="water", attacks=(),
            abilities=(AbilityDef("Up-Tempo", tag="draw_to:5",
                                  location="field"),)))
        p.hand = []
        p.deck = [EnergyRef(EnergyDef("water")) for _ in range(10)]
        r = b._do_ability(p, {"name": "Up-Tempo", "tag": "draw_to:5", "pos": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(p.hand), 5)

    def test_no_extra_draw_when_already_enough(self):
        b = _arena()
        p = b.players[0]
        p.active = make_pokemon(PokemonDef(
            "A", hp=100, ptype="water", attacks=(),
            abilities=(AbilityDef("Up-Tempo", tag="draw_to:5",
                                  location="field"),)))
        p.hand = [_hand_card(f"C{i}") for i in range(6)]
        p.deck = [EnergyRef(EnergyDef("water")) for _ in range(10)]
        b._do_ability(p, {"name": "Up-Tempo", "tag": "draw_to:5", "pos": 0})
        self.assertEqual(len(p.hand), 6)


if __name__ == "__main__":
    unittest.main()
