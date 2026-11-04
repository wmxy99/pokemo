# -*- coding: utf-8 -*-
"""第四十六批（2026-11-03）· 引擎用例：限时减伤族

覆盖三种「减伤」机制：

  · ``team_shield:reduce:<n>:type:<color>``（Iron Defender）
    —— 玩家级全队减伤：{M} 宝可梦在**对手的下个回合**少受 N 点；新上场的也受益。
  · ``shield_choose:prevent_all:from:ex:effects:gate:opp_prizes_le:N``
    （Acerola's Mischief）—— 选 1 只己方宝可梦，对手的下个回合内来自对手
    **{ex}** 宝可梦的招式伤害与效果全部被挡；奖赏数 gate 不满足时不可打。
  · ``fx:team_reduce:50:energy:water:nonstack``（Aurorus）
    —— 己方**持 {W} 能量**的宝可梦少受 50 点（弱抗之后），沿用既有全队常驻减伤。

运行：cd packages/engine && python -m unittest tests.test_batch46_team_shield -v
"""
from __future__ import annotations

import random
import unittest

from engine.battle import Battle
from engine.cards import (AbilityDef, AttackDef, EnergyDef, EnergyRef,
                          PokemonDef, TrainerDef, TrainerRef, make_pokemon)
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
        PokemonDef("Defender", hp=900, ptype="metal", attacks=()))
    b.players[1].active = make_pokemon(
        PokemonDef("Attacker", hp=500, ptype="colorless", attacks=()))
    for p in b.players:
        p.deck = [EnergyRef(EnergyDef("colorless")) for _ in range(20)]
    return b


def _give_attacker(b, effects=(), damage=100, is_ex=False, ptype="colorless"):
    b.players[1].active = make_pokemon(PokemonDef(
        "Attacker", hp=500, ptype=ptype, is_ex=is_ex,
        attacks=(AttackDef("Test", cost=0, damage=damage,
                           effects=tuple(effects)),)))


def _opponent_turn(b):
    """切到玩家 1 的回合（护盾/团队窗口仍在）"""
    b.current = 1
    b.turn_counts[1] = 2


class TestTeamShield(unittest.TestCase):
    TAG = "team_shield:reduce:30:type:metal"

    def _card(self, b, tag=None):
        b.players[0].hand = [TrainerRef(TrainerDef(tag or self.TAG, kind="item",
                                                   name="Iron Defender"))]

    def test_metal_target_reduced(self):
        b = _arena()
        self._card(b)
        self.assertTrue(b.step({"verb": "trainer", "hand": 0})["ok"])
        _give_attacker(b)
        _opponent_turn(b)
        self.assertTrue(b.step({"verb": "attack", "attack": 0})["ok"])
        self.assertEqual(b.players[0].active.damage, 70)

    def test_non_metal_target_not_reduced(self):
        b = _arena()
        b.players[0].active = make_pokemon(
            PokemonDef("Defender", hp=900, ptype="water"))
        self._card(b)
        self.assertTrue(b.step({"verb": "trainer", "hand": 0})["ok"])
        _give_attacker(b)
        _opponent_turn(b)
        self.assertTrue(b.step({"verb": "attack", "attack": 0})["ok"])
        self.assertEqual(b.players[0].active.damage, 100)

    def test_new_pokemon_also_benefits(self):
        """「This includes new Pokémon that come into play.」→ 玩家级状态"""
        b = _arena()
        self._card(b)
        self.assertTrue(b.step({"verb": "trainer", "hand": 0})["ok"])
        fresh = make_pokemon(PokemonDef("Fresh", hp=900, ptype="metal"))
        b.players[0].bench = [b.players[0].active]
        b.players[0].active = fresh
        _give_attacker(b)
        _opponent_turn(b)
        self.assertTrue(b.step({"verb": "attack", "attack": 0})["ok"])
        self.assertEqual(fresh.damage, 70)

    def test_expires_after_window(self):
        b = _arena()
        self._card(b)
        self.assertTrue(b.step({"verb": "trainer", "hand": 0})["ok"])
        b.players[0].team_shield_until = b.turn_counts[0] + 1
        b.turn_counts[0] += 1                      # 已过窗口
        _give_attacker(b)
        _opponent_turn(b)
        self.assertTrue(b.step({"verb": "attack", "attack": 0})["ok"])
        self.assertEqual(b.players[0].active.damage, 100)


class TestAcerolaShield(unittest.TestCase):
    TAG = ("shield_choose:prevent_all:from:ex:effects:"
           "gate:opp_prizes_le:2")

    def _card(self, b):
        b.players[0].hand = [TrainerRef(TrainerDef(
            self.TAG, kind="supporter", name="Acerola's Mischief"))]

    def test_gate_blocks_when_too_many_prizes(self):
        b = _arena()
        self._card(b)
        b.players[1].prizes_left = 3
        acts = [a for a in b.legal_actions() if a["verb"] == "trainer"]
        self.assertFalse(acts, "对手剩余奖赏 > 2 → 不可使用")
        r = b.step({"verb": "trainer", "hand": 0})
        self.assertFalse(r["ok"], "直接调用也必须拒绝（前置条件不满足）")

    def test_gate_allows_at_two_or_fewer(self):
        b = _arena()
        self._card(b)
        b.players[1].prizes_left = 2
        acts = [a for a in b.legal_actions() if a["verb"] == "trainer"]
        self.assertEqual(len(acts), 1, "只有战斗位一个合法目标")

    def test_shield_on_active_blocks_ex_attack_and_effects(self):
        b = _arena()
        self._card(b)
        b.players[1].prizes_left = 2
        r = b.step({"verb": "trainer", "hand": 0, "target": 0})
        self.assertTrue(r["ok"], r.get("error"))
        _give_attacker(b, effects=("confuse",), is_ex=True)
        _opponent_turn(b)
        self.assertTrue(b.step({"verb": "attack", "attack": 0})["ok"])
        self.assertEqual(b.players[0].active.damage, 0, "来自 {ex} 的伤害被挡")
        self.assertEqual(b.players[0].active.status, set(), "来自 {ex} 的效果被挡")

    def test_non_ex_attack_passes_through(self):
        b = _arena()
        self._card(b)
        b.players[1].prizes_left = 2
        self.assertTrue(b.step({"verb": "trainer", "hand": 0,
                                "target": 0})["ok"])
        _give_attacker(b, effects=("confuse",), is_ex=False)
        _opponent_turn(b)
        self.assertTrue(b.step({"verb": "attack", "attack": 0})["ok"])
        self.assertEqual(b.players[0].active.damage, 100, "非 {ex} 不受护盾影响")
        self.assertIn("confuse", b.players[0].active.status)

    def test_target_bench_does_not_protect_active(self):
        b = _arena()
        b.players[0].bench = [make_pokemon(PokemonDef("Bench", hp=300,
                                                      ptype="metal"))]
        self._card(b)
        b.players[1].prizes_left = 2
        self.assertTrue(b.step({"verb": "trainer", "hand": 0,
                                "target": 1})["ok"])
        self.assertTrue(b.players[0].bench[0].shield_prevent_all)
        self.assertFalse(b.players[0].active.shield_prevent_all)
        _give_attacker(b, is_ex=True)
        _opponent_turn(b)
        self.assertTrue(b.step({"verb": "attack", "attack": 0})["ok"])
        self.assertEqual(b.players[0].active.damage, 100)


class TestAurorusTeamReduce(unittest.TestCase):
    TAG = "fx:team_reduce:50:energy:water:nonstack"

    def _aurorus(self, b, on_bench=False):
        fp = make_pokemon(PokemonDef(
            "Aurorus", hp=300, ptype="water",
            abilities=(AbilityDef("Tundra Wall", tag=self.TAG,
                                  location="field"),)))
        if on_bench:
            b.players[0].bench = [fp]
        else:
            b.players[0].active = fp
        return fp

    def test_water_energy_target_reduced(self):
        b = _arena()
        aur = self._aurorus(b)
        aur.energy = {"water": 1}
        _give_attacker(b)
        _opponent_turn(b)
        self.assertTrue(b.step({"verb": "attack", "attack": 0})["ok"])
        self.assertEqual(aur.damage, 50)

    def test_no_water_energy_not_reduced(self):
        b = _arena()
        aur = self._aurorus(b)
        aur.energy = {"metal": 1}
        _give_attacker(b)
        _opponent_turn(b)
        self.assertTrue(b.step({"verb": "attack", "attack": 0})["ok"])
        self.assertEqual(aur.damage, 100)

    def test_source_on_bench_still_applies(self):
        b = _arena()
        self._aurorus(b, on_bench=True)
        b.players[0].active = make_pokemon(
            PokemonDef("Defender", hp=900, ptype="water"))
        b.players[0].active.energy = {"water": 1}
        _give_attacker(b)
        _opponent_turn(b)
        self.assertTrue(b.step({"verb": "attack", "attack": 0})["ok"])
        self.assertEqual(b.players[0].active.damage, 50,
                         "效果源可在任意己方宝可梦身上")


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
