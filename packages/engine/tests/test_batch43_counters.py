# -*- coding: utf-8 -*-
"""第四十三批（2026-11-03）· 引擎用例：两个限定计数源

  · ``own_discard_category:ancient``  —— Ancient 是**卡牌子类**（category），按
    卡名找恒为 0、按弃牌堆总数又多算（Roaring Moon）
  · ``own_field_named_energy:<key>:<color>`` —— 阵营 + 属性 + 具名三重限定
    （Iono's Voltorb），用全队能量会多算
"""
from __future__ import annotations
import random
import unittest

from engine.battle import Battle, _eval_counter
from engine.cards import EnergyDef, EnergyRef, PokeRef, PokemonDef, make_pokemon
from engine.state import GameConfig, PlayerState


def _arena():
    cfg = GameConfig()
    b = object.__new__(Battle)
    b.cfg = cfg
    b.seed = 7
    b.rng = random.Random(7)
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
    b.players[0].active = make_pokemon(PokemonDef("A0", hp=100, ptype="colorless"))
    b.players[1].active = make_pokemon(PokemonDef("B0", hp=100, ptype="colorless"))
    for p in b.players:
        p.deck = [EnergyRef() for _ in range(10)]
    return b


class TestDiscardCategory(unittest.TestCase):
    def test_counts_category_not_name_not_total(self):
        b = _arena()
        p = b.players[0]
        p.discard_refs = [
            PokeRef(PokemonDef("AncientMon", hp=100, ptype="fighting",
                               category="Ancient")),
            PokeRef(PokemonDef("AnotherAncient", hp=100, ptype="fighting",
                               category="Ancient")),
            PokeRef(PokemonDef("PlainMon", hp=100, ptype="water")),
        ]
        self.assertEqual(_eval_counter(b, "own_discard_category:ancient"), 2,
                         "只数 category 含 Ancient 的卡")

    def test_empty_discard_is_zero(self):
        b = _arena()
        self.assertEqual(_eval_counter(b, "own_discard_category:ancient"), 0)


class TestNamedFactionEnergy(unittest.TestCase):
    def test_only_named_faction_energy(self):
        b = _arena()
        p = b.players[0]
        iono = make_pokemon(PokemonDef("Iono's Voltorb", hp=70,
                                       ptype="lightning"))
        iono.energy = {"lightning": 2}
        other = make_pokemon(PokemonDef("OtherMon", hp=100, ptype="lightning"))
        other.energy = {"lightning": 3}
        p.active = iono
        p.bench = [other]
        self.assertEqual(
            _eval_counter(b, "own_field_named_energy:iono:lightning"), 2,
            "别的宝可梦身上的能量不得算进来")

    def test_bad_color_raises(self):
        b = _arena()
        from engine.battle import _eval_counter as ev
        with self.assertRaises(ValueError):
            ev(b, "own_field_named_energy:iono:rainbow")


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
