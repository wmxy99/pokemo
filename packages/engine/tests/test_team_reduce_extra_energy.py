# -*- coding: utf-8 -*-
"""第十九批（2026-10-29）· 引擎用例：额外能量条件加伤 / 全队常驻减伤

覆盖：
  · ``dmg_plus:N:if:own_active_extra_energy_at_least:K`` —— 「in addition to this
    attack's cost」（Electivire ex / Jellicent ex / Tangrowth）
  · ``fx:team_reduce:`` —— Curly Wall / Gear Coating / Stone Palace：
    效果源在**守方阵营任意一只**身上，作用对象按类别 / 能量 / 阵营筛选。
"""
from __future__ import annotations
import random
import unittest

from engine import families
from engine.battle import Battle, _eval_condition
from engine.cards import (AbilityDef, AttackDef, EnergyRef, PokemonDef,
                          make_pokemon)
from engine.state import GameConfig, PlayerState


def _arena(seed: int = 7):
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
    b.players[0].active = make_pokemon(
        PokemonDef("Attacker", hp=500, ptype="colorless",
                   attacks=(AttackDef("Strike", cost=0, damage=100),)))
    b.players[1].active = make_pokemon(
        PokemonDef("Defender", hp=900, ptype="colorless", stage=1))
    for p in b.players:
        p.deck = [EnergyRef() for _ in range(20)]
    return b


def _mon(name, hp=900, ptype="colorless", stage=0, abilities=()):
    return make_pokemon(PokemonDef(name, hp=hp, ptype=ptype, stage=stage,
                                   abilities=tuple(abilities)))


def _ab(tag: str) -> AbilityDef:
    return AbilityDef("A", tag=tag, location="field")


class TestExtraEnergyCondition(unittest.TestCase):
    def _battle(self, cost=2, n_energy=4):
        b = _arena()
        b.players[0].active = make_pokemon(PokemonDef(
            "Electivire", hp=500, ptype="lightning",
            attacks=(AttackDef("Press", cost=cost, damage=10, effects=(
                "dmg_plus:100:if:own_active_extra_energy_at_least:2",)),)))
        b.players[0].active.add_energy("lightning", n_energy)
        b.players[1].active = _mon("Defender", stage=1)
        return b

    def test_extra_energy_met(self):
        b = self._battle(cost=2, n_energy=4)      # 4 - 2 = 2 ≥ 2
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 110, "10 + 100")

    def test_extra_energy_not_met(self):
        b = self._battle(cost=2, n_energy=3)      # 3 - 2 = 1 < 2
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 10, "条件不满足 → 不加伤")

    def test_condition_ignores_cost_when_no_attack(self):
        """没有「正在结算的招式」时按费用 0 计（只用于局面查询，不影响结算）"""
        b = self._battle(cost=2, n_energy=1)
        self.assertFalse(_eval_condition(
            b, "own_active_extra_energy_at_least:2"))


class TestTeamReduce(unittest.TestCase):
    def test_curly_wall_reduces_basic_colorless(self):
        b = _arena()
        b.players[1].active = _mon("BasicMon", ptype="colorless", stage=0)
        b.players[1].bench = [
            _mon("Bouffalant", abilities=(_ab(
                "fx:team_reduce:60:basic:colorless:other:bouffalant:nonstack"),)),
            _mon("Bouffalant"),
        ]
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 40, "100 − 60")

    def test_curly_wall_requires_other_bouffalant(self):
        b = _arena()
        b.players[1].active = _mon("BasicMon", ptype="colorless", stage=0)
        b.players[1].bench = [_mon("Bouffalant", abilities=(_ab(
            "fx:team_reduce:60:basic:colorless:other:bouffalant:nonstack"),))]
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 100,
                         "没有「另一只 Bouffalant」→ 不生效")

    def test_curly_wall_not_for_nonbasic(self):
        b = _arena()
        b.players[1].active = _mon("Stage1", ptype="colorless", stage=1)
        b.players[1].bench = [
            _mon("Bouffalant", abilities=(_ab(
                "fx:team_reduce:60:basic:colorless:other:bouffalant:nonstack"),)),
            _mon("Bouffalant"),
        ]
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 100, "非基础宝可梦不减伤")

    def test_gear_coating_needs_metal_energy(self):
        b = _arena()
        b.players[1].active = _mon("Steel", stage=1)
        b.players[1].bench = [_mon("Klinklang", abilities=(
            _ab("fx:team_reduce:20:energy:metal"),))]
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 100, "没有 {M} 能量不减伤")

        b2 = _arena()
        b2.players[1].active = _mon("Steel", stage=1)
        b2.players[1].active.add_energy("metal", 1)
        b2.players[1].bench = [_mon("Klinklang", abilities=(
            _ab("fx:team_reduce:20:energy:metal"),))]
        b2.step({"verb": "attack", "attack": 0})
        self.assertEqual(b2.players[1].active.damage, 80, "100 − 20")

    def test_stone_palace_source_on_bench_and_faction(self):
        b = _arena()
        b.players[1].active = _mon("Steven's Skarmory", stage=1)
        b.players[1].bench = [_mon("Carbink", abilities=(
            _ab("fx:team_reduce:30:faction:stevens:source_bench:nonstack"),))]
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 70, "100 − 30")

    def test_stone_palace_not_on_active(self):
        b = _arena()
        b.players[1].active = _mon(
            "Steven's Skarmory", hp=900, stage=1, abilities=(_ab(
                "fx:team_reduce:30:faction:stevens:source_bench:nonstack"),))
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 100,
                         "来源在战斗位 → Stone Palace 不生效")

    def test_stone_palace_other_faction(self):
        b = _arena()
        b.players[1].active = _mon("Charizard", stage=1)
        b.players[1].bench = [_mon("Carbink", abilities=(
            _ab("fx:team_reduce:30:faction:stevens:source_bench:nonstack"),))]
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 100, "非 Steven's 不减伤")

    def test_nonstack_deduped(self):
        b = _arena()
        b.players[1].active = _mon("Steven's Skarmory", stage=1)
        tag = "fx:team_reduce:30:faction:stevens:source_bench:nonstack"
        b.players[1].bench = [_mon("Carbink", abilities=(_ab(tag),)),
                              _mon("Carbink", abilities=(_ab(tag),))]
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 70, "同 tag 只减一次")

    def test_unknown_tag_raises(self):
        b = _arena()
        b.players[1].active = _mon("X", stage=1, abilities=(
            _ab("fx:team_reduce:20:weird:metal"),))
        with self.assertRaises(ValueError):
            families.static_dmg_minus(b, b.players[1].active, None)


if __name__ == "__main__":
    unittest.main()
