# -*- coding: utf-8 -*-
"""第九十五批（2026-11-06）· Feraligatr「Torrential Heart」引擎用例

卡面：「Once during your turn, you may put 5 damage counters on this Pokémon. If
you do, during this turn, attacks used by this Pokémon do 120 more damage to your
opponent's Active Pokémon (before applying Weakness and Resistance).」

→ `counters:5:scope:self|self_dmg_bonus:120:self`（**单条**复合 tag）

三条断言对应三个容易写错的点：
  ① 代价真付：发动后自身吃 5 个指示物（= 50 伤害，不吃弱点/抗性）；
  ② 加伤真生效：本回合自身招式 +120，且**战斗位以外**的己方宝可梦不吃；
  ③ 只本回合：下一回合旗标过期（turn_number 已前进）。

运行：cd packages/engine && python -m unittest tests.test_batch95_self_buff -v
"""
from __future__ import annotations

import random
import unittest

from engine.battle import Battle
from engine.cards import (AbilityDef, AttackDef, EnergyDef, EnergyRef,
                          PokemonDef, make_pokemon)
from engine.state import GameConfig, PlayerState

TAG = "counters:5:scope:self|self_dmg_bonus:120:self"


class _Fixed(random.Random):
    def __init__(self, value: float = 0.5):
        super().__init__(0)
        self.value = value

    def random(self) -> float:
        return self.value


def _arena():
    cfg = GameConfig()
    b = object.__new__(Battle)
    b.cfg = cfg
    b.seed = 7
    b.rng = _Fixed()
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
        "Feraligatr", hp=300, ptype="water",
        attacks=(AttackDef("Test", cost=0, damage=50),),
        abilities=(AbilityDef("Torrential Heart", tag=TAG, location="field"),)))
    b.players[0].bench.append(make_pokemon(PokemonDef(
        "Buddy", hp=200, ptype="water",
        attacks=(AttackDef("Test", cost=0, damage=50),))))
    b.players[1].active = make_pokemon(PokemonDef(
        "Defender", hp=500, ptype="colorless"))
    for p in b.players:
        p.deck = [EnergyRef(EnergyDef("colorless")) for _ in range(20)]
    return b


class TestSelfCountersAndBuff(unittest.TestCase):
    def test_ability_pays_cost_and_sets_buff(self):
        b = _arena()
        r = b.step({"verb": "ability", "pos": 0, "name": "Torrential Heart",
                                "tag": TAG})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[0].active.damage, 50,
                         "代价：自身吃 5 个指示物 = 50 伤害（不吃弱点/抗性）")
        self.assertEqual(b.players[0].dmg_bonus_n, 120)
        self.assertEqual(b.players[0].dmg_bonus_until, b.turn_number)

    def test_attack_gains_bonus_same_turn(self):
        b = _arena()
        self.assertTrue(b.step({"verb": "ability", "pos": 0,
                                "name": "Torrential Heart", "tag": TAG})["ok"])
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        # 战斗位 50 + 120 = 170；对手 500 HP 未死
        self.assertEqual(b.players[1].active.damage, 170,
                         "本回合自身招式应 +120")

    def test_bonus_does_not_apply_to_bench(self):
        """旗标是 `self` 范围：换下场的同伴不得沾光"""
        b = _arena()
        self.assertTrue(b.step({"verb": "ability", "pos": 0,
                                "name": "Torrential Heart", "tag": TAG})["ok"])
        # 手动把战斗位与后备互换，攻击者变成 Buddy
        b.players[0].active, b.players[0].bench[0] = (
            b.players[0].bench[0], b.players[0].active)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.damage, 50,
                         "非旗标持有者的攻击不得 +120")

    def test_bonus_expires_next_turn(self):
        b = _arena()
        self.assertTrue(b.step({"verb": "ability", "pos": 0,
                                "name": "Torrential Heart", "tag": TAG})["ok"])
        b.turn_number += 1          # 模拟进入下一回合
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.damage, 50,
                         "跨回合后旗标必须过期（dmg_bonus_until）")


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
