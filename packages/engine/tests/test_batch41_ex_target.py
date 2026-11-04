# -*- coding: utf-8 -*-
"""第四十一批（2026-11-03）· 引擎用例：`:target:ex` 目标筛选 / 自身能量洗回牌库

覆盖：
  · ``snipe:…:target:ex``            只挑对手的 Pokémon {ex}，且**先筛再选**
  · ``energy_self_to_deck:all``      自身附着能量（基本按点数 + 特殊整张）洗回牌库

负向守卫：没有 ex 目标 → 合法无效果（不报错、不打非 ex）；能量洗回必须真的
在牌库里生成卡（只清零计数 = 「洗回」变「消失」）。
"""
from __future__ import annotations
import random
import unittest

from engine.battle import Battle
from engine.cards import (AttackDef, EnergyDef, EnergyRef, PokemonDef,
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
        PokemonDef("Attacker", hp=500, ptype="colorless", attacks=()))
    b.players[1].active = make_pokemon(
        PokemonDef("Defender", hp=900, ptype="colorless", attacks=()))
    for p in b.players:
        p.deck = [EnergyRef() for _ in range(20)]
    return b


def _with_attack(b, effects, damage=0):
    b.players[0].active = make_pokemon(PokemonDef(
        "Attacker", hp=500, ptype="colorless",
        attacks=(AttackDef("Test", cost=0, damage=damage,
                           effects=tuple(effects)),)))


class TestSnipeTargetEx(unittest.TestCase):
    def test_only_ex_is_hit(self):
        b = _arena()
        opp = b.players[1]
        # 非 ex 排在前：若实现是「先取 1 个再筛」就会打错
        opp.bench = [make_pokemon(PokemonDef("Plain", hp=100, ptype="water")),
                     make_pokemon(PokemonDef("Plain2", hp=100, ptype="water")),
                     make_pokemon(PokemonDef("ExMon", hp=400, ptype="water",
                                             is_ex=True))]
        _with_attack(b, ["snipe:opp_bench:210:1:target:ex"])
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(opp.bench[2].damage, 210, "只有 ex 后备该挨打")
        self.assertEqual(opp.bench[0].damage, 0)
        self.assertEqual(opp.bench[1].damage, 0)

    def test_each_of_opponent_ex_only(self):
        b = _arena()
        opp = b.players[1]
        opp.active = make_pokemon(PokemonDef("ExActive", hp=200,
                                             ptype="water", is_ex=True))
        opp.bench = [make_pokemon(PokemonDef("Plain", hp=100, ptype="water")),
                     make_pokemon(PokemonDef("ExBench", hp=200, ptype="water",
                                             is_ex=True))]
        _with_attack(b, ["snipe:opp:60:all:target:ex"])
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertGreater(opp.active.damage, 0, "战斗位是 ex → 也要打")
        self.assertEqual(opp.bench[0].damage, 0, "非 ex 后备不得挨打")
        self.assertEqual(opp.bench[1].damage, 60)

    def test_no_ex_target_is_noop(self):
        b = _arena()
        opp = b.players[1]
        opp.bench = [make_pokemon(PokemonDef("Plain", hp=100, ptype="water"))]
        _with_attack(b, ["snipe:opp_bench:210:1:target:ex"])
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(opp.bench[0].damage, 0)
        self.assertTrue([e for e in b.events
                         if e.get("type") == "no_effect"
                         and "无可选目标" in str(e.get("reason"))])


class TestEnergySelfToDeck(unittest.TestCase):
    def test_basic_and_special_return_to_deck(self):
        b = _arena()
        me = b.players[0]
        _with_attack(b, ["energy_self_to_deck:all"])
        atk = me.active                  # ⚠ _with_attack 会替换 active，必须在此后取
        atk.energy = {"metal": 2, "colorless": 1}
        atk.senergy = [EnergyRef(EnergyDef("water", tag="senergy:legacy"))]
        before = len(me.deck)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(atk.energy, {}, "基本能量必须清零")
        self.assertEqual(atk.senergy, [], "特殊能量必须移出")
        self.assertEqual(len(me.deck), before + 4,
                         "基本 3 点 → 3 张 + 特殊整张 1 张，必须真的进牌库")
        logs = [e for e in b.events if e.get("type") == "energy_self_to_deck"]
        self.assertEqual(logs[-1]["n"], 4)

    def test_no_energy_is_noop(self):
        b = _arena()
        me = b.players[0]
        before = len(me.deck)
        _with_attack(b, ["energy_self_to_deck:all"])
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(me.deck), before)
        self.assertEqual(me.active.energy, {})


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
