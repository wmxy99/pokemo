# -*- coding: utf-8 -*-
"""第四十二批（2026-11-03）· 引擎用例：对手宝可梦连同附着卡洗回牌库

覆盖 ``coin:heads:shuffle_opp_pokemon:<opp|opp_bench>``（Sylveon / Shiftry）：

  · 后备目标：宝可梦 + 附着能量/特殊能量/道具**全部**回对手牌库，后备减 1；
  · 战斗位目标：洗走后从后备补位（**不取奖赏**），无后备则对手判负；
  · 抛币反面：什么都不发生（合法无效果）。
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


class TestShuffleOppPokemon(unittest.TestCase):
    def test_bench_target_moves_with_attachments(self):
        b = _arena(rng=_Fixed(0.4))          # 正面
        opp = b.players[1]
        cached = make_pokemon(PokemonDef("Benched", hp=120, ptype="water"))
        cached.energy = {"water": 2}
        cached.senergy = [EnergyRef(EnergyDef("water", tag="senergy:legacy"))]
        cached.tool_ref = object()           # 道具对象（只验证会随行回牌库）
        cached.tool = "tool:belt"
        opp.bench = [cached, make_pokemon(PokemonDef("Bench2", hp=100,
                                                     ptype="water"))]
        _with_attack(b, ["coin:heads:shuffle_opp_pokemon:opp_bench"])
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(opp.bench), 1, "被洗走的宝可梦必须移出后备")
        self.assertEqual(opp.bench[0].name, "Bench2")
        logs = [e for e in b.events if e.get("type") == "shuffle_opp_pokemon"]
        self.assertEqual(logs[-1]["n"], 5,
                         "宝可梦 1 + 基本能量 2 + 特殊能量 1 + 道具 1")
        # 不是击倒：奖赏不变、弃牌堆不增
        self.assertEqual(b.players[0].prizes_left, b.cfg.prizes)
        self.assertEqual(opp.discard, [])

    def test_active_target_promotes_without_prize(self):
        b = _arena(rng=_Fixed(0.4))
        opp = b.players[1]
        opp.active = make_pokemon(PokemonDef("ActiveMon", hp=150,
                                             ptype="water"))
        opp.bench = [make_pokemon(PokemonDef("BenchMon", hp=100,
                                             ptype="water"))]
        _with_attack(b, ["coin:heads:shuffle_opp_pokemon:opp"])
        before_prizes = b.players[0].prizes_left
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(opp.active.name, "BenchMon", "必须从后备补位")
        self.assertEqual(b.players[0].prizes_left, before_prizes,
                         "洗回牌库**不取奖赏**")

    def test_active_without_bench_loses(self):
        b = _arena(rng=_Fixed(0.4))
        opp = b.players[1]
        opp.bench = []
        _with_attack(b, ["coin:heads:shuffle_opp_pokemon:opp"])
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(b.finished, "战斗位被洗走且无后备 → 对手判负")
        self.assertEqual(b.winner, 0)

    def test_tails_is_noop(self):
        b = _arena(rng=_Fixed(0.9))          # 反面
        opp = b.players[1]
        opp.bench = [make_pokemon(PokemonDef("BenchMon", hp=100,
                                             ptype="water"))]
        _with_attack(b, ["coin:heads:shuffle_opp_pokemon:opp_bench"])
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(opp.bench), 1)
        self.assertFalse([e for e in b.events
                          if e.get("type") == "shuffle_opp_pokemon"])


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
