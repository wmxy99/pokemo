# -*- coding: utf-8 -*-
"""第四十七批（2026-11-03）· 引擎用例：可选加伤 + 反伤 / 抛币选状态全集

  · ``dmg_plus:30:recoil:30``（Gurdurr「Superpower」）/ ``dmg_plus:120:recoil:50``
    （Dondozo ex「Dynamic Dive」）—— 加伤进伤害修正层（吃弱抗），反伤按 `self:N`
    口径（不吃弱抗、可自爆）；两者**同一条** tag，保持「If you do」联动。
  · ``coin:heads:status_choose:poison+burn+sleep+paralyze+confuse``（Grafaiai）
    —— 正面后五选一；确定性默认取 `rules.STATUSES` 首个（poison），可覆盖。

运行：cd packages/engine && python -m unittest tests.test_batch47_recoil -v
"""
from __future__ import annotations

import random
import unittest

from engine.battle import Battle
from engine.cards import (AttackDef, EnergyDef, EnergyRef, PokemonDef,
                          make_pokemon)
from engine.state import GameConfig, PlayerState


class _Fixed(random.Random):
    def __init__(self, value: float):
        super().__init__(0)
        self.value = value

    def random(self) -> float:
        return self.value


def _arena(rng=None):
    cfg = GameConfig()
    b = object.__new__(Battle)
    b.cfg = cfg
    b.seed = 7
    b.rng = rng or random.Random(7)
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
        PokemonDef("Attacker", hp=500, ptype="colorless", attacks=()))
    b.players[1].active = make_pokemon(
        PokemonDef("Defender", hp=900, ptype="colorless", attacks=()))
    for p in b.players:
        p.deck = [EnergyRef(EnergyDef("colorless")) for _ in range(20)]
    return b


def _attack(b, damage, effects, hp=500, ptype="colorless"):
    b.players[0].active = make_pokemon(PokemonDef(
        "Attacker", hp=hp, ptype=ptype,
        attacks=(AttackDef("Test", cost=0, damage=damage,
                           effects=tuple(effects)),)))


class TestRecoil(unittest.TestCase):
    RECOIL = ("dmg_plus:30:recoil:30",)

    def test_extra_damage_and_recoil(self):
        b = _arena()
        _attack(b, 50, self.RECOIL)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.damage, 80,
                         "印刷 50 + 可选加伤 30")
        self.assertEqual(b.players[0].active.damage, 30, "反伤 30")

    def test_weakness_applies_to_extra_damage_only(self):
        b = _arena()
        b.players[1].active = make_pokemon(
            PokemonDef("Defender", hp=900, ptype="water", weakness="fire"))
        _attack(b, 50, self.RECOIL, ptype="fire")
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.damage, 160,
                         "（50+30）×2 = 160；反伤不吃弱点")
        self.assertEqual(b.players[0].active.damage, 30)

    def test_recoil_can_knock_out_attacker(self):
        b = _arena()
        _attack(b, 50, self.RECOIL, hp=20)
        attacker = b.players[0].active
        before = b.players[1].prizes_left
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertTrue(attacker.is_ko, "30 反伤击倒 HP 20 的攻击者")
        self.assertIsNone(b.players[0].active, "被击倒的战斗位已清空")
        self.assertEqual(b.players[1].prizes_left, before - 1,
                         "自爆由对手取 1 奖赏")


class TestCoinStatusChooseAll(unittest.TestCase):
    TAG = ("coin:heads:status_choose:"
           "poison+burn+sleep+paralyze+confuse")

    def _graf(self, b):
        b.players[0].active = make_pokemon(PokemonDef(
            "Grafaiai", hp=300, ptype="darkness",
            attacks=(AttackDef("Miraculous Paint", cost=0, damage=0,
                               effects=(self.TAG,)),)))

    def test_heads_defaults_to_first_status(self):
        b = _arena(rng=_Fixed(0.4))
        self._graf(b)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertIn("poison", b.players[1].active.status,
                      "全集默认取 rules.STATUSES 首个（poison）")

    def test_tails_no_status(self):
        b = _arena(rng=_Fixed(0.9))
        self._graf(b)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.status, set())


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
