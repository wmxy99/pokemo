# -*- coding: utf-8 -*-
"""第十一批（2026-10-28）· 引擎用例：Flygon 双触发 / Leafeon 能量链 / 复杂场地

覆盖：
  · ``on_ko:replay_evolve_act``        战斗位被击倒时重放进化特性（Flygon）
  · ``attach:…:target:bench`` + ``heal:all:bench_one:if_prev``   Leafeon（同一只）
  · ``stadium:use:evolve_from_deck:chain:1``        Grand Tree（链式进化）
  · ``stadium:bench_limit:8:require:tera``          Area Zero（上限 8 / 裁回 5）
  · ``stadium:no_bench_counters``                   Battle Cage（后备指示物拦截）
"""
from __future__ import annotations
import random
import unittest

from engine.battle import Battle
from engine.cards import (AbilityDef, AttackDef, EnergyDef, EnergyRef, PokeRef,
                          PokemonDef, make_pokemon)
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
        PokemonDef("Attacker", hp=300, ptype="colorless", attacks=()))
    b.players[1].active = make_pokemon(
        PokemonDef("Defender", hp=300, ptype="colorless", attacks=()))
    for p in b.players:
        p.deck = [EnergyRef() for _ in range(20)]
    return b


def _mon(name, hp=300, ptype="colorless", stage=0, category="",
         abilities=(), attacks=()):
    return make_pokemon(PokemonDef(
        name, hp=hp, ptype=ptype, stage=stage, category=category,
        abilities=tuple(abilities), attacks=tuple(attacks)))


def _ab(tag: str) -> AbilityDef:
    return AbilityDef("A", tag=tag, location="field")


def _stadium(b, tag: str):
    b.stadium = {"name": "S", "tag": tag, "owner": 0}
    b._refresh_stadium_hp()


class TestFlygonReplay(unittest.TestCase):
    def test_replays_evolve_act_on_knockout(self):
        b = _arena()
        flygon = _mon("Flygon", hp=100,
                      abilities=(_ab("evolve_act:mill:2:opp"),
                                 _ab("on_ko:replay_evolve_act")))
        b.players[1].active = flygon
        b.players[1].deck = [EnergyRef() for _ in range(10)]
        b.players[0].deck = [EnergyRef() for _ in range(10)]
        b.players[0].active = _mon("Hitter", attacks=(AttackDef(
            "A", cost=0, damage=100),))
        before = len(b.players[0].deck)
        b.step({"verb": "attack", "attack": 0})
        self.assertTrue(b.finished or b.players[1].active is not None)
        self.assertEqual(len(b.players[0].deck), before - 2,
                         "Flygon 被击倒时应重放「磨对手牌库顶 2 张」")

    def test_no_replay_when_not_active(self):
        """卡面限定「in the Active Spot」→ 后备被击倒不重放"""
        b = _arena()
        fly = _mon("Flygon", hp=100,
                   abilities=(_ab("evolve_act:mill:2:opp"),
                              _ab("on_ko:replay_evolve_act")))
        b.players[1].bench.append(fly)
        b.players[1].active.damage = 10
        b.players[0].active = _mon("Hitter", attacks=(AttackDef(
            "A", cost=0, damage=100, effects=("snipe:opp_bench:100:1",)),))
        before = len(b.players[0].deck)
        b.step({"verb": "attack", "attack": 0, "snipe": [1]})
        self.assertEqual(len(b.players[0].deck), before)


class TestLeafeonChain(unittest.TestCase):
    def test_attach_to_bench_then_heal_same_target(self):
        b = _arena()
        b.players[0].active = _mon("Leafeon", attacks=(AttackDef(
            "Leaflet", cost=0, damage=0,
            effects=("attach:1:grass:hand:target:bench",
                     "heal:all:bench_one:if_prev")),))
        b.players[0].hand.append(EnergyRef(EnergyDef("grass")))
        b1, b2 = _mon("B1", hp=100), _mon("B2", hp=100)
        b1.damage = b2.damage = 50
        b.players[0].bench.extend([b1, b2])
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b1.total_energy(), 1, "能量应附到第一只后备")
        self.assertEqual(b1.damage, 0, "被附能的那只应回满")
        self.assertEqual(b2.damage, 50, "另一只不受影响")

    def test_no_energy_means_no_heal(self):
        b = _arena()
        b.players[0].active = _mon("Leafeon", attacks=(AttackDef(
            "Leaflet", cost=0, damage=0,
            effects=("attach:1:grass:hand:target:bench",
                     "heal:all:bench_one:if_prev")),))
        b1 = _mon("B1", hp=100)
        b1.damage = 50
        b.players[0].bench.append(b1)
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b1.damage, 50, "没有附到能量 → 不得回复（If you do）")


class TestGrandTree(unittest.TestCase):
    def test_chain_evolve_from_deck(self):
        b = _arena()
        _stadium(b, "stadium:use:evolve_from_deck:chain:1")
        basic = _mon("Bulbasaur", hp=70, ptype="grass")
        b.players[0].bench.append(basic)
        deck = [
            PokeRef(PokemonDef("Ivysaur", hp=100, ptype="grass", stage=1,
                               evolves_from="Bulbasaur")),
            PokeRef(PokemonDef("Venusaur", hp=160, ptype="grass", stage=2,
                               evolves_from="Ivysaur")),
        ]
        b.players[0].deck.extend(deck)
        acts = b.legal_actions()
        self.assertTrue(any(a.get("verb") == "stadium_use" for a in acts),
                        "Grand Tree 应列出可进化目标")
        r = b.step({"verb": "stadium_use", "target": 1})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(basic.name, "Venusaur", "应链式进化到二阶")


class TestAreaZero(unittest.TestCase):
    def test_bench_cap_with_tera(self):
        b = _arena()
        _stadium(b, "stadium:bench_limit:8:require:tera")
        b.players[0].active = _mon("TeraMon", hp=100, category="Tera(Stellar)")
        b.players[0].bench = [_mon(f"B{i}", hp=50) for i in range(7)]
        self.assertTrue(b._bench_has_room(b.players[0]), "有 Tera → 可到 8")
        b.players[0].bench.append(_mon("B7", hp=50))
        self.assertFalse(b._bench_has_room(b.players[0]), "8 只已满")

    def test_trim_to_five_without_tera(self):
        b = _arena()
        _stadium(b, "stadium:bench_limit:8:require:tera")
        b.players[0].active = _mon("TeraMon", hp=100, category="Tera(Stellar)")
        b.players[0].bench = [_mon(f"B{i}", hp=50) for i in range(8)]
        # 失去 Tera（例如被击倒/离场）
        b.players[0].active = _mon("Plain", hp=100)
        b._enforce_bench_limit()
        self.assertEqual(len(b.players[0].bench), b.cfg.max_bench,
                         "失去 Tera 后应裁回 5 只")


class TestBattleCage(unittest.TestCase):
    def test_blocks_bench_counters(self):
        b = _arena()
        _stadium(b, "stadium:no_bench_counters")
        bench = _mon("Bench", hp=100)
        b.players[1].bench.append(bench)
        b.players[0].active = _mon("Hitter", attacks=(AttackDef(
            "A", cost=0, damage=0, effects=("spread_free:60",)),))
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(bench.damage, 0, "后备指示物应被拦下")

    def test_allows_active_damage(self):
        b = _arena()
        _stadium(b, "stadium:no_bench_counters")
        b.players[0].active = _mon("Hitter", attacks=(AttackDef(
            "A", cost=0, damage=100),))
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 100,
                         "招式对战斗位的伤害不受影响")


if __name__ == "__main__":
    unittest.main()
