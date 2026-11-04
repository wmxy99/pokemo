# -*- coding: utf-8 -*-
"""第四十五批（2026-11-03）· 引擎用例：四种抛币变体

覆盖：

  · ``coin_per_target:150:scope:opp``（Mega Zygarde ex「Nullifying Zero」）
    —— 每个对手宝可梦**各抛一枚**，正面才打它；战斗位走弱抗、后备原始值。
  · ``status_choose:burn+confuse+poison``（Cradily「Selective Slime」）
    —— 抛币正面后三选一；引擎确定性默认取卡面首个，可用 action 覆盖。
  · ``passive:coin_prevent:darkness``（Fezandipiti「Adrena-Pheromone」）
    —— 持有 {D} 能量且被招式伤害时抛币，正面则本次伤害归零。
  · ``shuffle_both_coin_draw:5:3:3:5``（Harlequin）
    —— 双方手牌先洗回牌库，再抛**一次**币，按结果给双方抽不同张数。

运行：cd packages/engine && python -m unittest tests.test_batch45_mechanics -v
"""
from __future__ import annotations

import random
import unittest

from engine.battle import Battle
from engine.cards import (AbilityDef, AttackDef, EnergyDef, EnergyRef,
                          PokemonDef, TrainerDef, TrainerRef, make_pokemon)
from engine.state import GameConfig, PlayerState


class _Fixed(random.Random):
    def __init__(self, values):
        super().__init__(0)
        self.values = list(values)
        self.i = 0

    def random(self) -> float:
        v = self.values[self.i % len(self.values)]
        self.i += 1
        return v


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


def _attack(b, effects, damage=0):
    b.players[0].active = make_pokemon(PokemonDef(
        "Attacker", hp=500, ptype="colorless",
        attacks=(AttackDef("Test", cost=0, damage=damage,
                           effects=tuple(effects)),)))


class TestCoinPerTarget(unittest.TestCase):
    def _bench(self, b, hp=900):
        b.players[1].bench = [make_pokemon(
            PokemonDef("BenchMon", hp=hp, ptype="colorless"))]

    def test_all_heads_hit_active_and_bench(self):
        b = _arena(rng=_Fixed([0.4]))
        self._bench(b)
        _attack(b, ["coin_per_target:150:scope:opp"])
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.damage, 150)
        self.assertEqual(b.players[1].bench[0].damage, 150)
        coins = [e for e in b.events if e.get("type") == "coin"
                 and str(e.get("tag", "")).startswith("coin_per_target:")]
        self.assertEqual(len(coins), 2, "每个对手宝可梦各抛一枚")

    def test_all_tails_no_damage(self):
        b = _arena(rng=_Fixed([0.9]))
        self._bench(b)
        _attack(b, ["coin_per_target:150:scope:opp"])
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.damage, 0)
        self.assertEqual(b.players[1].bench[0].damage, 0)

    def test_per_target_independent(self):
        """第一枚正面、第二枚反面 → 只打战斗位（不是「全中/全不中」）"""
        b = _arena(rng=_Fixed([0.4, 0.9]))
        self._bench(b)
        _attack(b, ["coin_per_target:150:scope:opp"])
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.damage, 150)
        self.assertEqual(b.players[1].bench[0].damage, 0)

    def test_active_weakness_applies_bench_does_not(self):
        """卡面括号只豁免**后备**；战斗位照常吃弱点"""
        b = _arena(rng=_Fixed([0.4]))
        b.players[1].active = make_pokemon(
            PokemonDef("Defender", hp=900, ptype="water", weakness="lightning"))
        b.players[0].active = make_pokemon(PokemonDef(
            "Attacker", hp=500, ptype="lightning",
            attacks=(AttackDef("Test", cost=0, damage=0,
                               effects=("coin_per_target:150:scope:opp",)),)))
        b.players[1].bench = [make_pokemon(
            PokemonDef("BenchMon", hp=900, ptype="water"))]
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 300, "战斗位弱点 ×2")
        self.assertEqual(b.players[1].bench[0].damage, 150, "后备不吃弱点")


class TestStatusChoose(unittest.TestCase):
    TAG = "coin:heads:status_choose:burn+confuse+poison"

    def _cradily(self, b):
        b.players[0].active = make_pokemon(PokemonDef(
            "Cradily", hp=300, ptype="grass",
            abilities=(AbilityDef("Selective Slime", tag=self.TAG,
                                  location="field"),)))

    def test_heads_defaults_to_first_option(self):
        b = _arena(rng=_Fixed([0.4]))
        self._cradily(b)
        r = b.step({"verb": "ability", "pos": 0, "name": "Selective Slime",
                    "tag": self.TAG})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertIn("burn", b.players[1].active.status,
                      "确定性默认取卡面首个（Burned）")

    def test_explicit_choice(self):
        b = _arena(rng=_Fixed([0.4]))
        self._cradily(b)
        r = b.step({"verb": "ability", "pos": 0, "name": "Selective Slime",
                    "tag": self.TAG, "status_choice": "poison"})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertIn("poison", b.players[1].active.status)
        self.assertNotIn("burn", b.players[1].active.status)

    def test_invalid_choice_falls_back(self):
        b = _arena(rng=_Fixed([0.4]))
        self._cradily(b)
        r = b.step({"verb": "ability", "pos": 0, "name": "Selective Slime",
                    "tag": self.TAG, "status_choice": "sleep"})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertIn("burn", b.players[1].active.status,
                      "非法选项必须退回卡面首个，不能静默施加别的状态")

    def test_tails_no_status(self):
        b = _arena(rng=_Fixed([0.9]))
        self._cradily(b)
        b.step({"verb": "ability", "pos": 0, "name": "Selective Slime",
                "tag": self.TAG})
        self.assertEqual(b.players[1].active.status, set())


class TestCoinPreventPassive(unittest.TestCase):
    TAG = "passive:coin_prevent:darkness"

    def _target(self, b, energy=None):
        b.players[1].active = make_pokemon(PokemonDef(
            "Fezandipiti", hp=900, ptype="darkness",
            abilities=(AbilityDef("Adrena-Pheromone", tag=self.TAG,
                                  location="field"),)))
        if energy:
            b.players[1].active.energy = dict(energy)

    def _hit(self, b):
        _attack(b, [], damage=100)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))

    def test_heads_prevents_all_damage(self):
        b = _arena(rng=_Fixed([0.4]))
        self._target(b, {"darkness": 1})
        self._hit(b)
        self.assertEqual(b.players[1].active.damage, 0)

    def test_tails_damage_applies(self):
        b = _arena(rng=_Fixed([0.9]))
        self._target(b, {"darkness": 1})
        self._hit(b)
        self.assertEqual(b.players[1].active.damage, 100)

    def test_no_darkness_energy_no_flip(self):
        b = _arena(rng=_Fixed([0.4]))
        self._target(b, {"water": 1})
        self._hit(b)
        self.assertEqual(b.players[1].active.damage, 100)
        self.assertFalse([e for e in b.events if e.get("type") == "coin"])


class TestShuffleBothCoinDraw(unittest.TestCase):
    TAG = "shuffle_both_coin_draw:5:3:3:5"

    def _harlequin(self, b):
        card = TrainerRef(TrainerDef(self.TAG, kind="supporter",
                                     name="Harlequin"))
        filler = [EnergyRef(EnergyDef("colorless")) for _ in range(2)]
        b.players[0].hand = [card] + filler
        b.players[1].hand = [EnergyRef(EnergyDef("water")) for _ in range(3)]
        return card

    def test_heads_draws_five_and_three(self):
        b = _arena(rng=_Fixed([0.4]))
        self._harlequin(b)
        r = b.step({"verb": "trainer", "hand": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(b.players[0].hand), 5, "正面：我方抽 5")
        self.assertEqual(len(b.players[1].hand), 3, "正面：对手抽 3")
        self.assertEqual(len([e for e in b.events if e.get("type") == "coin"]), 1)

    def test_tails_draws_three_and_five(self):
        b = _arena(rng=_Fixed([0.9]))
        self._harlequin(b)
        r = b.step({"verb": "trainer", "hand": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(b.players[0].hand), 3, "反面：我方抽 3")
        self.assertEqual(len(b.players[1].hand), 5, "反面：对手抽 5")

    def test_hands_shuffled_into_decks(self):
        b = _arena(rng=_Fixed([0.4]))
        self._harlequin(b)
        before_opp_deck = len(b.players[1].deck)
        r = b.step({"verb": "trainer", "hand": 0})
        self.assertTrue(r["ok"], r.get("error"))
        # 对手 3 张手牌进了牌库、又抽走 3 → 牌库回到原数；手牌张数=3
        self.assertEqual(len(b.players[1].deck), before_opp_deck)
        self.assertEqual(len(b.players[1].hand), 3)

    def test_not_listed_when_deck_would_be_empty(self):
        b = _arena(rng=_Fixed([0.4]))
        card = TrainerRef(TrainerDef(self.TAG, kind="supporter",
                                     name="Harlequin"))
        b.players[0].hand = [card]
        b.players[0].deck = []
        acts = [a for a in b.legal_actions() if a["verb"] == "trainer"]
        self.assertFalse(acts, "洗牌后牌库为空 → 抽牌直接判负，不应列出")


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
