# -*- coding: utf-8 -*-
"""第六十八~七十七批 · 引擎侧行为测试（2026-11-04）

只覆盖**真正动了引擎**的两处：
  · 第七十批 `energy_to_deck:` 训练家侧派发（复用招式侧 Kyogre 的
    `_energy_to_deck` helper）—— 把弃牌堆**基本能量**洗回牌库，只动基本能量、
    不动特殊能量 / 宝可梦 / 其它卡；
  · 第七十二批 `counters:N:scope:self` —— 指示物直放**己方**战斗位，
    不碰对手（阵营反转是历史高危缺陷）。
"""
from __future__ import annotations

import random
import unittest

from engine.battle import Battle
from engine.cards import (EnergyDef, EnergyRef, PokeRef, PokemonDef,
                          TrainerDef, TrainerRef, make_pokemon)
from engine.state import GameConfig, PlayerState


def _arena(seed: int = 9) -> Battle:
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
        PokemonDef("A", hp=300, ptype="colorless", attacks=()))
    b.players[1].active = make_pokemon(
        PokemonDef("B", hp=300, ptype="colorless", attacks=()))
    return b


class TestCountersScopeSelf(unittest.TestCase):
    def test_scope_self_hits_own_active(self):
        """counters:N:scope:self 放到**己方**战斗位（5 指示物 = 50 伤害）"""
        b = _arena()
        b._apply_counters("counters:5:scope:self")
        self.assertEqual(b.players[0].active.damage, 50)

    def test_scope_self_does_not_hit_opponent(self):
        """不能落成对手口径（阵营反转）"""
        b = _arena()
        b._apply_counters("counters:5:scope:self")
        self.assertEqual(b.players[1].active.damage, 0)


class TestEnergyToDeckTrainer(unittest.TestCase):
    def _battle(self):
        b = _arena()
        b.players[0].discard_refs = [
            EnergyRef(EnergyDef("fire")),
            EnergyRef(EnergyDef("fire")),
            EnergyRef(EnergyDef("water")),
            PokeRef(PokemonDef("P", hp=60, ptype="colorless")),
        ]
        b.players[0].deck = []
        b.players[0].hand = [
            TrainerRef(TrainerDef("energy_to_deck:any:5", kind="item",
                                  name="Energy Recycler"))]
        return b

    def test_trainer_recycles_basic_energy_only(self):
        """只把**基本能量**洗回牌库；宝可梦与用掉的训练家卡留在弃牌堆"""
        b = self._battle()
        b._do_trainer(b.players[0], {"hand": 0})
        self.assertEqual(len(b.players[0].deck), 3)
        # 弃牌堆 = 那只宝可梦 + 用掉的 Energy Recycler 本身（能量都被洗回）
        self.assertEqual(len(b.players[0].discard_refs), 2)
        self.assertFalse(any(isinstance(r, EnergyRef)
                             for r in b.players[0].discard_refs))

    def test_trainer_legal_action_gate(self):
        """弃牌堆没有基本能量 → 这张卡不进合法动作表（不空转）"""
        b = _arena()
        b.players[0].discard_refs = [
            PokeRef(PokemonDef("P", hp=60, ptype="colorless"))]
        b.players[0].hand = [
            TrainerRef(TrainerDef("energy_to_deck:any:5", kind="item",
                                  name="Energy Recycler"))]
        acts = b.legal_actions(0)
        verbs = {a.get("verb") for a in acts}
        self.assertNotIn("trainer", verbs)


if __name__ == "__main__":
    unittest.main()
