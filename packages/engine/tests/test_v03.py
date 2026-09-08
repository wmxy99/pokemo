# -*- coding: utf-8 -*-
"""PokéLab Engine v0.3 · E3 特性机制用例

覆盖：
  field 特性（recon:2 检视牌库顶2取1，每回合一次）
  hand  特性（cursed:130 手牌弃自身 → 指定对手宝可梦 130 指示物伤害）
  view 暴露 abilities · 特性卡组端到端 smoke
运行：python -m unittest discover -s tests -v
"""
from __future__ import annotations
import random
import unittest
from engine.cards import (PokemonDef, AttackDef, EnergyDef, EnergyRef,
                          PokeRef, make_pokemon)
from engine.state import GameConfig, PlayerState
from engine.battle import Battle
from engine import decklib as dl


class ArenaV3:
    def __init__(self, seed: int = 1):
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
            p.deck = [EnergyRef() for _ in range(20)]
        self.place(make_pokemon(dl.DRAKLING), 0)
        self.place(make_pokemon(dl.SEEDLING), 1)

    def place(self, fp, pi=0, active=True):
        p = self.b.players[pi]
        if active:
            p.active = fp
        else:
            p.bench.append(fp)
        return fp

    def step(self, action):
        return self.b.step(action)


def _hp100(name="Hp100"):
    return PokemonDef(name, hp=100, ptype="colorless", attacks=())


class TestFieldAbility(unittest.TestCase):
    def test_recon_draws_one_top(self):
        a = ArenaV3()
        draco = make_pokemon(dl.DRACO)          # 场上 recon
        a.place(draco, 0)
        # 牌库顶 3 张：依次放入（list 末尾为顶）
        a.b.players[0].deck = [EnergyRef() for _ in range(5)]
        a.b.players[0].deck.append(PokeRef(dl.DRAKLING))
        a.b.players[0].deck.append(PokeRef(dl.SPECTOR))
        deck_n = len(a.b.players[0].deck)
        hand_n = len(a.b.players[0].hand)
        legal = a.b.legal_actions(0)
        ab = [x for x in legal if x.get("verb") == "ability"]
        self.assertTrue(ab)
        res = a.step({"verb": "ability", "name": "Recon Scout",
                      "pos": 0, "tag": "recon:2"})
        self.assertTrue(res["ok"])
        self.assertEqual(len(a.b.players[0].hand), hand_n + 1)
        self.assertEqual(len(a.b.players[0].deck), deck_n - 1)  # 1 入手，另 1 放回
        self.assertIn("Recon Scout", draco.ability_used)

    def test_recon_once_per_turn(self):
        a = ArenaV3()
        draco = make_pokemon(dl.DRACO)
        a.place(draco, 0)
        ok1 = a.step({"verb": "ability", "name": "Recon Scout",
                      "pos": 0, "tag": "recon:2"})
        self.assertTrue(ok1["ok"])
        res = a.step({"verb": "ability", "name": "Recon Scout",
                      "pos": 0, "tag": "recon:2"})
        self.assertFalse(res["ok"])
        self.assertIn("已发动", res["error"])

    def test_recon_reset_next_turn(self):
        a = ArenaV3()
        draco = make_pokemon(dl.DRACO)
        a.place(draco, 0)
        a.step({"verb": "ability", "name": "Recon Scout",
                "pos": 0, "tag": "recon:2"})
        # 结束回合 → 轮到对方再回到我方 → 标记应清空
        a.b._end_turn()
        a.b._end_turn()
        legal = a.b.legal_actions(0)
        self.assertTrue(any(x.get("verb") == "ability"
                            and x.get("name") == "Recon Scout"
                            for x in legal))


class TestHandAbility(unittest.TestCase):
    def _cursed_setup(self, target_hp=150):
        a = ArenaV3()
        target = make_pokemon(_hp100())
        target.pdef = PokemonDef("Tgt", hp=target_hp, ptype="colorless")
        a.place(target, 1)
        a.place(make_pokemon(dl.SEEDLING), 1, active=False)   # 后备目标候选
        a.b.players[0].hand.append(PokeRef(dl.SPECTOR))        # hand 特性卡
        return a

    def test_cursed_targets_opp_field(self):
        a = self._cursed_setup()
        legal = a.b.legal_actions(0)
        cursed = [x for x in legal if x.get("verb") == "ability"
                  and x.get("tag", "").startswith("cursed")]
        # 目标 = 对手 active + 1 后备 = 2 个
        self.assertEqual(len(cursed), 2)

    def test_cursed_deals_damage_and_discards_self(self):
        a = self._cursed_setup()
        hand_n = len(a.b.players[0].hand)
        disc_n = len(a.b.players[0].discard)
        tgt = a.b.players[1].active
        res = a.step({"verb": "ability", "name": "Cursed Blast",
                      "hand": hand_n - 1, "tag": "cursed:130", "target": 0})
        self.assertTrue(res["ok"])
        self.assertEqual(tgt.damage, 130)
        self.assertEqual(len(a.b.players[0].hand), hand_n - 1)   # 弃自身
        self.assertEqual(len(a.b.players[0].discard), disc_n + 1)
        self.assertFalse(a.b.finished)                            # 未击倒(150hp)

    def test_cursed_ko_bench_gives_prize(self):
        a = ArenaV3()
        bench = make_pokemon(_hp100())
        bench.pdef = PokemonDef("Small", hp=60, ptype="colorless")
        a.place(bench, 1, active=False)
        a.b.players[0].hand.append(PokeRef(dl.SPECTOR))
        hand_n = len(a.b.players[0].hand)
        prizes_before = a.b.players[0].prizes_left
        res = a.step({"verb": "ability", "name": "Cursed Blast",
                      "hand": hand_n - 1, "tag": "cursed:130", "target": 1})
        self.assertTrue(res["ok"])
        self.assertNotIn(bench, a.b.players[1].bench)            # 已移除
        self.assertEqual(a.b.players[0].prizes_left, prizes_before - 1)
        self.assertFalse(a.b.finished)                            # 后备被击倒不判负


class TestAbilitySmoke(unittest.TestCase):
    def test_ability_deck_matches_finish(self):
        from engine.simulator import run_match
        from engine.agents import RandomAgent
        for seed in range(4):
            b = run_match(dl.deck_ability_demo(), dl.deck_fire_demo(),
                          RandomAgent(seed=0), RandomAgent(seed=1),
                          seed=seed)
            self.assertTrue(b.finished, f"seed={seed}")

    def test_view_exposes_abilities(self):
        b = Battle(dl.deck_ability_demo(), dl.deck_fire_demo(), seed=5)
        v = b.view()
        for fp in [v["me_state"]["active"]]:
            self.assertIsInstance(fp["abilities"], list)
        # 手牌 Pokemon 也应带 abilities 字段
        pk = [c for c in v["me_state"]["hand"]
              if c["type"] == "pokemon"]
        if pk:
            self.assertIn("abilities", pk[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
