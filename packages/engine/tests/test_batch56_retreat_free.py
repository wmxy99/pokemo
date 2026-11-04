# -*- coding: utf-8 -*-
"""第五十六批（2026-11-04）· 引擎用例：`fx:retreat_free:`（撤退费用归零）

与 `passive:no_retreat` 的关键差别：后者只作用于**特性持有者自己**，本族是
**全队**——受益者常常不是持有者（Archaludon 让带 {M} 能量的**队友**免费撤退）。
落成 `passive:` 会让该效果只剩「自己能用」，卡照样能打、测试全绿。
"""
from __future__ import annotations
import random
import unittest

from engine import families
from engine.battle import Battle, _retreat_cost
from engine.cards import AbilityDef, PokemonDef, make_pokemon
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
    b.players[0].active = make_pokemon(
        PokemonDef("A", hp=100, ptype="grass", attacks=()))
    b.players[1].active = make_pokemon(
        PokemonDef("B", hp=100, ptype="colorless", attacks=()))
    return b


def _holder(tag, name="H", ptype="metal"):
    return make_pokemon(PokemonDef(
        name, hp=100, ptype=ptype, attacks=(),
        abilities=(AbilityDef("Bridge", tag=tag),)))


class TestRetreatFreeEnergy(unittest.TestCase):
    def test_team_member_with_energy(self):
        b = _arena()
        b.players[0].bench = [_holder("fx:retreat_free:energy:metal")]
        mate = make_pokemon(PokemonDef("Mate", hp=100, ptype="metal",
                                       attacks=(), retreat=3))
        mate.energy = {"metal": 1}
        b.players[0].bench.append(mate)
        self.assertEqual(families.retreat_cost(b, mate), 0)

    def test_team_member_without_energy_pays(self):
        b = _arena()
        b.players[0].bench = [_holder("fx:retreat_free:energy:metal")]
        mate = make_pokemon(PokemonDef("Mate", hp=100, ptype="metal",
                                       attacks=(), retreat=3))
        b.players[0].bench.append(mate)
        self.assertIsNone(families.retreat_cost(b, mate))
        self.assertEqual(_retreat_cost(mate, b), 3)

    def test_opponent_not_helped(self):
        """全队形态只作用于**己方**：对手场上同一特性不该让我方免费"""
        b = _arena()
        b.players[1].bench = [_holder("fx:retreat_free:energy:metal")]
        mine = make_pokemon(PokemonDef("Mine", hp=100, ptype="metal",
                                       attacks=(), retreat=2))
        mine.energy = {"metal": 1}
        b.players[0].active = mine
        self.assertIsNone(families.retreat_cost(b, mine))


class TestRetreatFreeBasic(unittest.TestCase):
    def test_basic_free(self):
        b = _arena()
        b.players[0].bench = [_holder("fx:retreat_free:basic")]
        basic = make_pokemon(PokemonDef("Basic", hp=100, ptype="grass",
                                        attacks=(), retreat=2))
        b.players[0].bench.append(basic)   # ⚠ 必须在场：`_side_of` 靠场上定位阵营
        self.assertEqual(families.retreat_cost(b, basic), 0)

    def test_evolution_not_free(self):
        b = _arena()
        b.players[0].bench = [_holder("fx:retreat_free:basic")]
        evo = make_pokemon(PokemonDef("Evo", hp=100, ptype="grass",
                                      stage=1, attacks=(), retreat=2))
        b.players[0].bench.append(evo)
        self.assertIsNone(families.retreat_cost(b, evo))


class TestRetreatFreeSelf(unittest.TestCase):
    def test_self_without_energy(self):
        b = _arena()
        holder = _holder("fx:retreat_free:self:if:no_energy")
        b.players[0].active = holder
        self.assertEqual(families.retreat_cost(b, holder), 0)

    def test_self_with_energy_pays(self):
        b = _arena()
        holder = _holder("fx:retreat_free:self:if:no_energy")
        holder.energy = {"metal": 1}
        b.players[0].active = holder
        self.assertIsNone(families.retreat_cost(b, holder))

    def test_self_does_not_leak_to_team(self):
        """`self:` 形态**不得**让队友也免费（那是 `basic`/`energy` 的语义）"""
        b = _arena()
        b.players[0].active = _holder("fx:retreat_free:self:if:no_energy")
        mate = make_pokemon(PokemonDef("Mate", hp=100, ptype="grass",
                                       attacks=(), retreat=3))
        b.players[0].bench.append(mate)
        self.assertIsNone(families.retreat_cost(b, mate))


class TestUnknownTagRaises(unittest.TestCase):
    def test_bad_color(self):
        b = _arena()
        b.players[0].bench = [_holder("fx:retreat_free:energy:nope")]
        mate = make_pokemon(PokemonDef("Mate", hp=100, ptype="metal",
                                       attacks=(), retreat=3))
        b.players[0].bench.append(mate)
        with self.assertRaises(ValueError):
            families.retreat_cost(b, mate)
