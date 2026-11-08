# -*- coding: utf-8 -*-
"""PokéLab Engine v0.4 · E5 任意分配（spread_free）用例

覆盖：targets 计划按指定分配 / 计划总和校验 / 兜底确定性轮询 /
      后备 KO 结算 / distribute_damage 单元
运行：python -m unittest discover -s tests -v
"""
from __future__ import annotations
import random
import unittest
from engine.cards import (PokemonDef, AttackDef, make_pokemon)
from engine.state import GameConfig, PlayerState
from engine.battle import Battle
from engine.rules import distribute_damage

# 攻击：主目标 10 + spread_free 60
FX = AttackDef("FreeX", cost=2, damage=10, effects=("spread_free:60",))
USER = PokemonDef("UserX", hp=120, ptype="lightning", attacks=(FX,))
BENCH1 = PokemonDef("B1", hp=80, ptype="colorless")
BENCH2 = PokemonDef("B2", hp=80, ptype="colorless")
BENCH3 = PokemonDef("B3", hp=20, ptype="colorless")


class ArenaE5:
    def __init__(self, bench: list, seed: int = 1):
        c = GameConfig()
        self.b = object.__new__(Battle)
        b = self.b
        b.cfg = c
        b.seed = seed
        b.rng = random.Random(seed)
        b.players = [PlayerState(name="A", prizes_left=c.prizes),
                     PlayerState(name="B", prizes_left=c.prizes)]
        b.current = 0
        b.turn_number = 1
        b.turn_counts = [1, 1]
        b.phase = "main"
        b.finished = False
        b.winner = None
        b.win_reason = None
        b.events = []
        for p in b.players:
            p.deck = []
        a0 = make_pokemon(USER)
        a0.add_energy("colorless", 2)
        b.players[0].active = a0
        b.players[1].active = make_pokemon(PokemonDef("Act", hp=200,
                                                      ptype="colorless"))
        b.players[1].bench = [make_pokemon(x) for x in bench]

    def attack(self, targets=None):
        act = {"verb": "attack", "attack": 0}
        if targets is not None:
            act["targets"] = targets
        return self.b.step(act)


class TestDistribute(unittest.TestCase):
    def test_round_robin(self):
        self.assertEqual(distribute_damage(2, 60), [30, 30])
        self.assertEqual(distribute_damage(3, 60), [20, 20, 20])
        self.assertEqual(distribute_damage(4, 60), [20, 20, 10, 10])
        self.assertEqual(distribute_damage(0, 60), [])

    def test_total_preserved(self):
        d = distribute_damage(3, 60)
        self.assertEqual(sum(d), 60)


class TestSpreadFree(unittest.TestCase):
    def test_plan_honored(self):
        a = ArenaE5([BENCH1, BENCH2])
        plan = [{"pos": 1, "dmg": 60}]
        res = a.attack(targets=plan)
        self.assertTrue(res["ok"])
        self.assertEqual(a.b.players[1].bench[1].damage, 60)
        self.assertEqual(a.b.players[1].bench[0].damage, 0)

    def test_plan_split_two(self):
        a = ArenaE5([BENCH1, BENCH2])
        plan = [{"pos": 0, "dmg": 20}, {"pos": 1, "dmg": 40}]
        res = a.attack(targets=plan)
        self.assertTrue(res["ok"])
        self.assertEqual(a.b.players[1].bench[0].damage, 20)
        self.assertEqual(a.b.players[1].bench[1].damage, 40)

    def test_plan_total_mismatch_rejected(self):
        a = ArenaE5([BENCH1])
        res = a.attack(targets=[{"pos": 0, "dmg": 50}])   # ≠60
        self.assertFalse(res["ok"])
        self.assertIn("总和", res["error"])

    def test_fallback_deterministic(self):
        a = ArenaE5([BENCH1, BENCH2])
        ok = a.attack()                    # 无 plan → 兜底 [30,30]
        self.assertTrue(ok["ok"])
        self.assertEqual(a.b.players[1].bench[0].damage, 30)
        self.assertEqual(a.b.players[1].bench[1].damage, 30)

    def test_bench_ko_prize(self):
        a = ArenaE5([BENCH3])              # 20hp，60 点直接 KO
        prizes = a.b.players[0].prizes_left
        res = a.attack(targets=[{"pos": 0, "dmg": 60}])
        self.assertTrue(res["ok"])
        self.assertNotIn(a.b.players[1].bench, [BENCH3])
        self.assertEqual(a.b.players[0].prizes_left, prizes - 1)

    def test_draco_ex_uses_free_spread(self):
        # Draco ex Phantom Rush（F+P）携带 targets 的端到端
        from engine import decklib as dl
        from engine.cards import PokeRef, make_pokemon as mk
        # 本用例验 Draco ex 的 spread 端到端，与先手规则无关 → 关掉先手禁攻
        b = Battle(dl.deck_draco_demo(), dl.deck_fire_demo(), seed=7,
                   cfg=GameConfig(first_turn_no_attack=False))
        me = b.players[b.current]
        me.active = mk(dl.DRACO_EX)
        me.active.add_energy("fire", 1)
        me.active.add_energy("psychic", 1)
        me.active.just_played = False
        # 对手摆 2 高血量后备（显式重置，避开自动铺满/低血量被 KO 干扰断言）
        def _tank(name):
            return PokemonDef(name, hp=150, ptype="colorless")
        opp = b.players[1 - b.current]
        opp.bench = [mk(_tank("T1")), mk(_tank("T2"))]
        me.hand.clear()
        res = b.step({"verb": "attack", "attack": 0,
                      "targets": [{"pos": 1, "dmg": 60}]})   # T2 留在后备
        self.assertTrue(res["ok"])
        self.assertEqual(opp.active.name, "T1")               # KO 后 promote
        self.assertEqual(opp.bench[0].name, "T2")
        self.assertEqual(opp.bench[0].damage, 60)


if __name__ == "__main__":
    unittest.main(verbosity=2)
