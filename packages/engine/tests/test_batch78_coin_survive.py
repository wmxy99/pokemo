# -*- coding: utf-8 -*-
"""第七十八批（2026-11-03）· 引擎用例：抛币免击倒

``passive:coin_survive:10``（Mega Hawlucha ex「Tenacious Body」）：
「If this Pokémon would be Knocked Out by damage from an attack, flip a coin.
 If heads, this Pokémon is not Knocked Out, and its remaining HP becomes 10.」

关键差异：**不要求受伤前满 HP**（`tool:survive` 才有该限制）、反面无效果。

运行：cd packages/engine && python -m unittest tests.test_batch78_coin_survive -v
"""
from __future__ import annotations

import random
import unittest

from engine.battle import Battle
from engine.cards import (AbilityDef, AttackDef, EnergyDef, EnergyRef,
                          PokemonDef, make_pokemon)
from engine.state import GameConfig, PlayerState


class _Fixed(random.Random):
    def __init__(self, value: float):
        super().__init__(0)
        self.value = value

    def random(self) -> float:
        return self.value


def _arena(rng):
    cfg = GameConfig()
    b = object.__new__(Battle)
    b.cfg = cfg
    b.seed = 7
    b.rng = rng
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
    b.players[0].active = make_pokemon(PokemonDef(
        "Attacker", hp=500, ptype="colorless",
        attacks=(AttackDef("Test", cost=0, damage=150),)))
    b.players[1].active = make_pokemon(PokemonDef(
        "Mega Hawlucha ex", hp=100, ptype="fighting",
        abilities=(AbilityDef("Tenacious Body",
                              tag="passive:coin_survive:10",
                              location="field"),)))
    for p in b.players:
        p.deck = [EnergyRef(EnergyDef("colorless")) for _ in range(20)]
    return b


class TestCoinSurvive(unittest.TestCase):
    def test_heads_survives_with_ten_hp(self):
        b = _arena(_Fixed(0.4))
        before = b.players[0].prizes_left
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.damage, 90,
                         "正面：剩 10 HP（damage = max_hp - 10）")
        self.assertFalse(b.players[1].active.is_ko)
        self.assertEqual(b.players[0].prizes_left, before, "未击倒不取奖赏")

    def test_tails_knocked_out(self):
        b = _arena(_Fixed(0.9))
        before = b.players[0].prizes_left
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertIsNone(b.players[1].active, "反面照常击倒")
        self.assertEqual(b.players[0].prizes_left, before - 1)

    def test_no_full_hp_requirement(self):
        """与 `tool:survive` 不同：受伤前不必满 HP"""
        b = _arena(_Fixed(0.4))
        b.players[1].active.damage = 40
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.damage, 90)
        self.assertFalse(b.players[1].active.is_ko)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
