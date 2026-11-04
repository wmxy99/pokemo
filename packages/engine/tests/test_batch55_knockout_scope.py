# -*- coding: utf-8 -*-
"""第五十五批（2026-11-04）· 引擎用例：击倒族三个限定

  · ``knockout:opp_all:if:hp_le:N``   **每一只**剩余 HP ≤ N 的对手宝可梦都被击倒
  · ``knockout:opp_active:if:senergy`` 只在对手战斗位带**特殊**能量时击倒
  · 未知限定必须**抛错**（静默忽略 = 无条件击倒，直接改胜负）
"""
from __future__ import annotations
import random
import unittest

from engine.battle import Battle
from engine.cards import PokemonDef, make_pokemon
from engine.state import GameConfig, PlayerState


def _arena(seed=7):
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
    b._ko_by_attack = [0, 0]
    b.players[0].active = make_pokemon(
        PokemonDef("A", hp=100, ptype="grass", attacks=()))
    b.players[1].active = make_pokemon(
        PokemonDef("B", hp=100, ptype="colorless", attacks=()))
    # 后备 1：剩 40 HP（≤50，应被击倒）；后备 2：剩 80 HP（>50，不该被击倒）
    b1 = make_pokemon(PokemonDef("B1", hp=100, ptype="water", attacks=()))
    b1.damage = 60
    b2 = make_pokemon(PokemonDef("B2", hp=100, ptype="fire", attacks=()))
    b2.damage = 20
    b.players[1].bench = [b1, b2]
    return b


class TestKnockoutOppAll(unittest.TestCase):
    def test_kills_each_qualifying(self):
        b = _arena()
        # ⚠ 先取引用：击倒会把该只从 `bench` 里**移走**，事后再用 bench[0]
        #   索引会落到原本的 bench[1] 上（误判为「第一只没死」）。
        low, high = b.players[1].bench[0], b.players[1].bench[1]
        b._apply_knockout("knockout:opp_all:if:hp_le:50", None,
                          lambda fp: False)
        self.assertTrue(low.is_ko, "剩 40 HP 的后备应被击倒")
        self.assertFalse(high.is_ko, "剩 80 HP 的后备不该被击倒")

    def test_no_match_logs_no_effect(self):
        b = _arena()
        before = list(b.players[1].bench)
        b._apply_knockout("knockout:opp_all:if:hp_le:5", None, lambda fp: False)
        self.assertFalse(any(fp.is_ko for fp in before))
        self.assertEqual(b.players[1].bench, before)


class TestKnockoutSenergy(unittest.TestCase):
    def test_requires_special_energy(self):
        b = _arena()
        target = b.players[1].active
        b._apply_knockout("knockout:opp_active:if:senergy", None,
                          lambda fp: False)
        self.assertFalse(target.is_ko, "无特殊能量时不该被击倒")

    def test_fires_with_special_energy(self):
        from engine.cards import EnergyDef, EnergyRef
        b = _arena()
        edef = EnergyDef("Spiky Energy", tag="senergy:spiky")
        self.assertTrue(getattr(edef, "is_special", False))
        b.players[1].active.senergy.append(EnergyRef(edef))
        # 同样先取引用：击倒后战斗位会被后备顶上
        target = b.players[1].active
        b._apply_knockout("knockout:opp_active:if:senergy", None,
                          lambda fp: False)
        self.assertTrue(target.is_ko)

    def test_basic_energy_not_enough(self):
        """基本能量不算「Special Energy」—— 用 energy_counts 判会静默变成无条件"""
        b = _arena()
        b.players[1].active.energy = {"water": 2}
        target = b.players[1].active
        b._apply_knockout("knockout:opp_active:if:senergy", None,
                          lambda fp: False)
        self.assertFalse(target.is_ko)


class TestKnockoutUnknownQual(unittest.TestCase):
    def test_unknown_qual_raises(self):
        b = _arena()
        with self.assertRaises(ValueError):
            b._apply_knockout("knockout:opp_active:if:whatever", None,
                              lambda fp: False)

    def test_unknown_scope_raises(self):
        b = _arena()
        with self.assertRaises(ValueError):
            b._apply_knockout("knockout:opp_everything", None, lambda fp: False)
