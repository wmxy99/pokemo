# -*- coding: utf-8 -*-
"""PokéLab Engine · 「弃手牌能量」与「弃场上能量」的行为区分（2026-11-02）

解析侧把「Discard N Energy cards from **your hand**」映射成
``discard_hand_energy:N``（而不是 ``discard_energy:N``）。本文件在**引擎侧**
证明这个区分是必要的、且新映射真的只动手牌：

  · ``discard_hand_energy:N``  → 只从**手牌**弃；手牌能量不足时弃现有的，
    场上宝可梦的能量**一张不动**（卡面「up to N from your hand」）；
  · ``discard_energy:N``       → 先弃**战斗位宝可梦身上**的能量，
    不足再从手牌补（这是既有语义，用于「Discard 2 Energy from this Pokémon」）。

若两者再被混用，就会复现修复前的静默错算：卡面写「从手牌弃」，
实际却把自家场上宝可梦的能量拔掉。

运行：cd packages/engine && python -m unittest tests.test_hand_energy_cost -v
"""
from __future__ import annotations

import random
import unittest

from engine.battle import Battle
from engine.cards import (AttackDef, EnergyDef, EnergyRef, PokemonDef,
                          make_pokemon)
from engine.state import GameConfig, PlayerState

FILLER = 10


def arena(seed: int = 3):
    cfg = GameConfig()
    b = object.__new__(Battle)
    b.cfg = cfg
    b.seed = seed
    b.rng = random.Random(seed)
    b.players = [PlayerState(name="A", prizes_left=cfg.prizes),
                 PlayerState(name="B", prizes_left=cfg.prizes)]
    b.current = 0
    b.turn_number = 2
    b.turn_counts = [2, 2]
    b.phase = "main"
    b.finished = False
    b.winner = None
    b.win_reason = None
    b.events = []
    b.stadium = None
    b._ko_by_attack = [0, 0]
    b._ko_from_attack = False
    b.players[1].active = make_pokemon(
        PokemonDef("Opp", hp=300, ptype="colorless", attacks=()))
    for p in b.players:
        p.deck = [EnergyRef(EnergyDef("grass")) for _ in range(FILLER)]
    return b


def _with_attack(b, effects):
    b.players[0].active = make_pokemon(PokemonDef(
        "Attacker", hp=500, ptype="colorless",
        attacks=(AttackDef("Test", cost=0, damage=0,
                           effects=tuple(effects)),)))


def _hand_energies(p, n: int, color: str = "fire"):
    p.hand = [EnergyRef(EnergyDef(color)) for _ in range(n)]


def _attach(p, n: int, color: str = "grass"):
    p.active.add_energy(color, n)


class TestHandEnergyOnly(unittest.TestCase):
    """discard_hand_energy:N 只动手牌"""

    def test_takes_from_hand_not_field(self):
        b = arena()
        p = b.players[0]
        _with_attack(b, ["discard_hand_energy:2"])

        _attach(p, 3)
        _hand_energies(p, 2)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(p.active.total_energy(), 3,
                         "手牌弃能把场上能量也拔掉了（正是修复前的错算）")
        self.assertEqual(len([c for c in p.hand if isinstance(c, EnergyRef)]), 0)

    def test_up_to_semantics_when_fewer(self):
        """卡面「up to N」：手牌只有 1 张能量时弃 1 张，不报错、不动场上"""
        b = arena()
        p = b.players[0]
        _with_attack(b, ["discard_hand_energy:4"])

        _attach(p, 2)
        _hand_energies(p, 1)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(p.active.total_energy(), 2)
        self.assertEqual(len(p.discarded_refs if False else p.discard_refs), 1)

    def test_empty_hand_is_not_an_error(self):
        b = arena()
        p = b.players[0]
        _with_attack(b, ["discard_hand_energy:3"])
        _attach(p, 1)
        p.hand = []
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(p.active.total_energy(), 1)

    def test_counts_towards_discarded_energy_this_turn(self):
        """「for each Energy card you discarded in this way」依赖本回合计数"""
        b = arena()
        p = b.players[0]
        _hand_energies(p, 3)
        _with_attack(b, ["discard_hand_energy:3"])
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(p.discarded_energy_this_turn, 3)

    def test_energy_type_filter(self):
        """指定属性时只弃该属性的手牌能量"""
        b = arena()
        p = b.players[0]
        p.hand = [EnergyRef(EnergyDef("fire")), EnergyRef(EnergyDef("water"))]
        _with_attack(b, ["discard_hand_energy:2:type:water"])
        b.step({"verb": "attack", "attack": 0})
        left = [c.edef.type for c in p.hand if isinstance(c, EnergyRef)]
        self.assertEqual(left, ["fire"])


class TestFieldEnergyContrast(unittest.TestCase):
    """对照组：discard_energy:N 的既有语义（先场上、再手牌兜底）不变"""

    def test_takes_from_field_first(self):
        b = arena()
        p = b.players[0]
        _with_attack(b, ["discard_energy:2"])

        _attach(p, 2)
        _hand_energies(p, 3)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(p.active.total_energy(), 0, "应从场上弃")
        self.assertEqual(len([c for c in p.hand if isinstance(c, EnergyRef)]), 3,
                         "场上够时不该动手牌")

    def test_falls_back_to_hand(self):
        """既有语义：场上不够时从手牌补 —— 正因如此才不能用于「只从手牌弃」"""
        b = arena()
        p = b.players[0]
        _with_attack(b, ["discard_energy:3"])

        _attach(p, 1)
        _hand_energies(p, 3)
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(p.active.total_energy(), 0)
        self.assertEqual(len([c for c in p.hand if isinstance(c, EnergyRef)]), 1)


if __name__ == "__main__":
    unittest.main()
