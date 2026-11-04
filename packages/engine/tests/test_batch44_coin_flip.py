# -*- coding: utf-8 -*-
"""第四十四批（2026-11-03）· 引擎用例：单次抛币的双分支 / 特性抛币

覆盖两条新增 tag：

  · ``coin_flip:heads:<a>|tails:<b>``（训练家侧，Team Rocket's Venture Bomb）
    —— 「Flip a coin. If heads, put 2 damage counters on 1 of your opponent's
    Pokémon. If tails, put 2 damage counters on your Active Pokémon.」
    **同一枚币只抛一次**：正面 → 对手 1 只吃 2 个指示物；反面 → 己方战斗位吃
    2 个指示物。指示物不吃弱点/抗性（`counters:` 口径）。
  · ``coin:heads:bounce:energy:opp_active:1``（特性侧，Dustox「Boisterous Wind」）
    —— 正面把对手战斗位的 1 个能量收回**其**手牌；反面无效果。

运行：cd packages/engine && python -m unittest tests.test_batch44_coin_flip -v
"""
from __future__ import annotations

import random
import unittest

from engine.battle import Battle
from engine.cards import (AbilityDef, EnergyDef, EnergyRef, PokemonDef,
                          TrainerDef, TrainerRef, make_pokemon)
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
        PokemonDef("Attacker", hp=300, ptype="colorless", attacks=()))
    b.players[1].active = make_pokemon(
        PokemonDef("Defender", hp=300, ptype="colorless", attacks=()))
    for p in b.players:
        p.deck = [EnergyRef(EnergyDef("colorless")) for _ in range(20)]
    return b


VENTURE_BOMB = "coin_flip:heads:counters:2:scope:opp:1|tails:counters:2:scope:self"


def _bomb_in_hand(b):
    card = TrainerRef(TrainerDef(VENTURE_BOMB, kind="item",
                                 name="Team Rocket's Venture Bomb"))
    b.players[0].hand = [card]
    return card


class TestTrainerCoinFlip(unittest.TestCase):
    def test_heads_hits_opponent_not_self(self):
        b = _arena(rng=_Fixed(0.4))                      # 正面
        _bomb_in_hand(b)
        r = b.step({"verb": "trainer", "hand": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.damage, 20,
                         "正面：对手 1 只吃 2 个指示物（=20）")
        self.assertEqual(b.players[0].active.damage, 0, "反面分支不得同时生效")
        coins = [e for e in b.events if e.get("type") == "coin"]
        self.assertEqual(len(coins), 1, "同一枚币只能抛一次")

    def test_tails_hits_own_active(self):
        b = _arena(rng=_Fixed(0.9))                      # 反面
        _bomb_in_hand(b)
        r = b.step({"verb": "trainer", "hand": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[0].active.damage, 20,
                         "反面：己方战斗位吃 2 个指示物（=20）")
        self.assertEqual(b.players[1].active.damage, 0, "正面分支不得同时生效")
        self.assertEqual(len([e for e in b.events if e.get("type") == "coin"]), 1)

    def test_counters_ignore_weakness(self):
        """指示物不吃弱点/抗性：对手弱点是 grass（×2），仍只吃 20"""
        b = _arena(rng=_Fixed(0.4))
        b.players[1].active = make_pokemon(
            PokemonDef("Defender", hp=300, ptype="water", weakness="grass"))
        _bomb_in_hand(b)
        r = b.step({"verb": "trainer", "hand": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.damage, 20)

    def test_listed_in_legal_actions(self):
        b = _arena()
        _bomb_in_hand(b)
        names = [a["display"] for a in b.legal_actions() if a["verb"] == "trainer"]
        self.assertIn("Team Rocket's Venture Bomb", names)


class TestAbilityCoinBounce(unittest.TestCase):
    TAG = "coin:heads:bounce:energy:opp_active:1"

    def _dustox(self, b):
        b.players[0].active = make_pokemon(PokemonDef(
            "Dustox", hp=140, ptype="grass",
            abilities=(AbilityDef("Boisterous Wind", tag=self.TAG,
                                  location="field"),)))

    def test_heads_moves_energy_to_opponent_hand(self):
        b = _arena(rng=_Fixed(0.4))                      # 正面
        opp = b.players[1]
        opp.active.energy = {"water": 1}
        self._dustox(b)
        r = b.step({"verb": "ability", "pos": 0, "name": "Boisterous Wind",
                    "tag": self.TAG})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(opp.active.energy, {}, "能量必须离开对手战斗位")
        self.assertEqual(len(opp.hand), 1, "能量卡进**对手**手牌（not 我方）")
        self.assertEqual(len(b.players[0].hand), 0)
        coins = [e for e in b.events if e.get("type") == "coin"]
        self.assertEqual(len(coins), 1)

    def test_tails_is_noop(self):
        b = _arena(rng=_Fixed(0.9))                      # 反面
        opp = b.players[1]
        opp.active.energy = {"water": 1}
        self._dustox(b)
        r = b.step({"verb": "ability", "pos": 0, "name": "Boisterous Wind",
                    "tag": self.TAG})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(opp.active.energy, {"water": 1})
        self.assertEqual(opp.hand, [])

    def test_not_listed_without_opponent_energy(self):
        b = _arena()
        self._dustox(b)
        acts = [a for a in b.legal_actions() if a["verb"] == "ability"]
        self.assertFalse(acts, "对手战斗位没有能量 → 特性空转，不应列出")


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
