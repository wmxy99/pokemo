# -*- coding: utf-8 -*-
"""第六批计数源 / 条件（2026-10-28）—— 引擎侧单测

覆盖「带参数的子集计数」与「当下局面可读的条件」，并锁定一个真实缺陷：
`dmg_*:N:per:<counter>` 里的计数源**可能自带冒号**（`own_discard_named:xxx`），
只取 `parts[3]` 会截掉参数、把伤害算成 0。
"""
from __future__ import annotations

import random
import unittest

from engine.battle import Battle, _eval_condition, _eval_counter
from engine.cards import (AbilityDef, AttackDef, EnergyDef, EnergyRef,
                          PokemonDef, PokeRef, TrainerDef, TrainerRef,
                          make_pokemon)
from engine.state import GameConfig, PlayerState


def _arena(seed: int = 4):
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
    b.players[0].active = make_pokemon(PokemonDef(
        "Me", hp=200, ptype="colorless",
        attacks=(AttackDef("Hit", cost=0, damage=10),)))
    b.players[1].active = make_pokemon(PokemonDef(
        "Foe", hp=300, ptype="colorless"))
    return b


def _mon(name, hp=60, ptype="colorless", stage=0, category="",
         attacks=(), evolves_from=None):
    return make_pokemon(PokemonDef(name, hp=hp, ptype=ptype, stage=stage,
                                   category=category, attacks=attacks,
                                   evolves_from=evolves_from))


class TestDynamicCounters(unittest.TestCase):
    def test_field_has_attack(self):
        b = _arena()
        rnd = (AttackDef("Round", cost=1, damage=20),)
        b.players[0].bench.append(_mon("A", attacks=rnd))
        b.players[0].bench.append(_mon("B", attacks=rnd))
        b.players[0].bench.append(_mon("C"))
        # active「Me」的招式是 Hit，不具备 Round
        self.assertEqual(_eval_counter(b, "own_field_has_attack:round"), 2)
        self.assertEqual(
            _eval_counter(b, "own_field_has_attack:nonexistent"), 0)

    def test_both_field_named_any(self):
        b = _arena()
        b.players[0].bench.append(_mon("Koffing"))
        b.players[1].bench.append(_mon("Weezing"))
        b.players[1].bench.append(_mon("Other"))
        self.assertEqual(
            _eval_counter(b, "both_field_named_any:koffing+weezing"), 2)

    def test_discard_named(self):
        b = _arena()
        p = b.players[0]
        p.discard_refs.append(TrainerRef(TrainerDef(
            "ethansadventure", kind="supporter", name="Ethan's Adventure")))
        p.discard_refs.append(TrainerRef(TrainerDef(
            "other", kind="item", name="Other Item")))
        self.assertEqual(
            _eval_counter(b, "own_discard_named:ethansadventure"), 1)
        self.assertEqual(
            _eval_counter(b, "own_discard_named_supporter:ethansadventure"), 1)
        self.assertEqual(
            _eval_counter(b, "own_discard_named_supporter:other"), 0)

    def test_discard_has_attack(self):
        b = _arena()
        uw = (AttackDef("United Wings", cost=1, damage=20),)
        b.players[0].discard_refs.append(PokeRef(PokemonDef(
            "Bird", hp=60, ptype="colorless", attacks=uw)))
        b.players[0].discard_refs.append(PokeRef(PokemonDef(
            "Plain", hp=60, ptype="colorless")))
        self.assertEqual(
            _eval_counter(b, "own_discard_has_attack:unitedwings"), 1)

    def test_unknown_counter_raises(self):
        b = _arena()
        with self.assertRaises(ValueError):
            _eval_counter(b, "own_field_has_attack")


class TestDynamicConditions(unittest.TestCase):
    def test_opp_active_tera(self):
        b = _arena()
        b.players[1].active = _mon("T", ptype="fire", category="Tera(Fire)")
        self.assertTrue(_eval_condition(b, "opp_active_tera"))
        b.players[1].active = _mon("N")
        self.assertFalse(_eval_condition(b, "opp_active_tera"))

    def test_own_bench_tera(self):
        b = _arena()
        self.assertFalse(_eval_condition(b, "own_bench_tera"))
        b.players[0].bench.append(_mon("T", category="Tera(Stellar)"))
        self.assertTrue(_eval_condition(b, "own_bench_tera"))

    def test_same_type_in_play(self):
        b = _arena()
        b.players[1].active = _mon("Foe", ptype="fire")
        b.players[0].bench.append(_mon("G", ptype="grass"))
        self.assertFalse(_eval_condition(b, "same_type_in_play"))
        b.players[1].bench.append(_mon("G2", ptype="grass"))
        self.assertTrue(_eval_condition(b, "same_type_in_play"))

    def test_special_energy_condition(self):
        b = _arena()
        self.assertFalse(_eval_condition(b, "own_active_special_energy"))
        b.players[0].active.senergy.append(
            EnergyRef(EnergyDef("colorless", name="Mist Energy",
                                tag="senergy:mist")))
        self.assertTrue(_eval_condition(b, "own_active_special_energy"))
        self.assertFalse(_eval_condition(
            b, "own_active_energy_tag:teamrocket"))
        b.players[0].active.senergy.append(
            EnergyRef(EnergyDef("darkness", name="TR Energy",
                                tag="senergy:team_rocket")))
        self.assertTrue(_eval_condition(
            b, "own_active_energy_tag:teamrocket"))

    def test_discard_basic_energy_at_least(self):
        b = _arena()
        p = b.players[0]
        for _ in range(10):
            p.discard_refs.append(
                EnergyRef(EnergyDef("fire", name="Basic Fire Energy")))
        self.assertTrue(_eval_condition(
            b, "own_discard_basic_energy_at_least:fire:10"))
        self.assertFalse(_eval_condition(
            b, "own_discard_basic_energy_at_least:fire:11"))

    def test_bench_stage2_type(self):
        b = _arena()
        self.assertFalse(_eval_condition(b, "own_bench_stage2_type:darkness"))
        b.players[0].bench.append(_mon("S2", ptype="darkness", stage=2))
        self.assertTrue(_eval_condition(b, "own_bench_stage2_type:darkness"))
        self.assertFalse(_eval_condition(b, "own_bench_stage2_type:fire"))

    def test_own_active_status_any(self):
        b = _arena()
        self.assertFalse(_eval_condition(b, "own_active_status_any:burn,poison"))
        b.players[0].active.status.add("poison")
        self.assertTrue(_eval_condition(b, "own_active_status_any:burn,poison"))
        self.assertFalse(_eval_condition(b, "own_active_status_any:burn"))

    def test_discard_has_named(self):
        b = _arena()
        self.assertFalse(_eval_condition(
            b, "own_discard_has_named:rosasencouragement"))
        b.players[0].discard_refs.append(TrainerRef(TrainerDef(
            "rosa", kind="supporter", name="Rosa's Encouragement")))
        self.assertTrue(_eval_condition(
            b, "own_discard_has_named:rosasencouragement"))


class TestDmgSetParamCounter(unittest.TestCase):
    """`dmg_set:N:per:<带参数的计数源>` 必须算对（冒号截断回归）"""

    def test_round_damage(self):
        b = _arena()
        rnd = (AttackDef("Round", cost=0, damage=20),)
        b.players[0].active = make_pokemon(PokemonDef(
            "Rounder", hp=200, ptype="colorless",
            attacks=(AttackDef("Round", cost=0, effects=(
                "dmg_set:20:per:own_field_has_attack:round",)),)))
        b.players[0].bench.append(_mon("A", attacks=rnd))
        b.players[0].bench.append(_mon("B", attacks=rnd))
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        # 我方场上 3 只（active + 2 后备）都有 Round → 20 × 3
        self.assertEqual(b.players[1].active.damage, 60)

    def test_dmg_plus_named_counter(self):
        b = _arena()
        b.players[0].active = make_pokemon(PokemonDef(
            "Blaster", hp=200, ptype="colorless",
            attacks=(AttackDef("Buddy Blast", cost=0, damage=0, effects=(
                "dmg_set:60:per:own_discard_named:ethansadventure",)),)))
        b.players[0].discard_refs.append(TrainerRef(TrainerDef(
            "ethan", kind="supporter", name="Ethan's Adventure")))
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.damage, 60)


class TestLegalActionNeverRaises(unittest.TestCase):
    """`legal_actions` 里列出的动作必须能 step 成功（不得「合法但必然抛错」）"""

    def test_search_energy_with_empty_deck(self):
        b = _arena()
        b.players[0].active = make_pokemon(PokemonDef(
            "Searcher", hp=200, ptype="colorless",
            attacks=(AttackDef("Errand", cost=0, effects=("search_energy:2",)),)))
        # 牌库只剩宝可梦卡（无能量）
        b.players[0].deck = [PokeRef(PokemonDef("X", hp=10, ptype="colorless"))]
        acts = [a for a in b.legal_actions() if a["verb"] == "attack"]
        self.assertEqual(len(acts), 1)
        r = b.step(acts[0])
        self.assertTrue(r["ok"], r.get("error"))


if __name__ == "__main__":
    unittest.main()
