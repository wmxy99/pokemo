# -*- coding: utf-8 -*-
"""第四十八批（2026-11-04）· 引擎用例：`coin_each` 的币数计数源

  · ``own_active_energy``          出招者身上的能量总数（Heliolisk / Dudunsparce ex）
  · ``own_active_energy_<color>``  出招者身上的指定属性能量（Volcanion）
  · ``own_field_type:<color>``     己方**在场**该属性宝可梦**只数**（Scrafty）

⚠ `own_field_type` 是新计数源：数**只数**且按属性筛。用 `own_field`（全队只数）
会多算别的属性；用能量计数更是量纲都错。拼错属性必须**抛错**——静默返回 0
会让整招变成「抛 0 枚币、打 0 点」。
"""
from __future__ import annotations
import random
import unittest

from engine.battle import Battle, _eval_counter
from engine.cards import EnergyDef, EnergyRef, PokemonDef, make_pokemon
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
        PokemonDef("A", hp=100, ptype="lightning", attacks=()))
    b.players[1].active = make_pokemon(
        PokemonDef("B", hp=100, ptype="colorless", attacks=()))
    return b


class TestFieldTypeCounter(unittest.TestCase):
    def test_counts_only_that_type(self):
        b = _arena()
        b.players[0].bench = [
            make_pokemon(PokemonDef("D1", hp=100, ptype="darkness", attacks=())),
            make_pokemon(PokemonDef("D2", hp=100, ptype="darkness", attacks=())),
            make_pokemon(PokemonDef("W", hp=100, ptype="water", attacks=())),
        ]
        self.assertEqual(_eval_counter(b, "own_field_type_darkness"), 2)
        self.assertEqual(_eval_counter(b, "own_field_type_water"), 1)
        self.assertEqual(_eval_counter(b, "own_field_type_fire"), 0)

    def test_active_counts_too(self):
        """"you have in play" 含战斗位（`all_field`），不是只有后备"""
        b = _arena()
        b.players[0].active = make_pokemon(
            PokemonDef("A", hp=100, ptype="darkness", attacks=()))
        self.assertEqual(_eval_counter(b, "own_field_type_darkness"), 1)

    def test_not_same_as_total_field(self):
        """与 `own_field` 的差别：别的属性不能算进来（静默加强的来源）"""
        b = _arena()
        b.players[0].bench = [
            make_pokemon(PokemonDef("D1", hp=100, ptype="darkness", attacks=())),
            make_pokemon(PokemonDef("W", hp=100, ptype="water", attacks=())),
        ]
        self.assertEqual(_eval_counter(b, "own_field"), 3)
        self.assertEqual(_eval_counter(b, "own_field_type_darkness"), 1)

    def test_bad_type_raises(self):
        b = _arena()
        with self.assertRaises(ValueError):
            _eval_counter(b, "own_field_type_rainbow")


class TestEnergyCounterOnSelf(unittest.TestCase):
    def test_counts_attacker_energy_only(self):
        b = _arena()
        b.players[0].active.add_energy("water", 2)
        b.players[0].bench = [
            make_pokemon(PokemonDef("W", hp=100, ptype="water", attacks=())),
        ]
        b.players[0].bench[0].add_energy("water", 3)
        # 只数出招者：后备身上的 3 个不能算（否则币数翻倍）
        self.assertEqual(_eval_counter(b, "own_active_energy"), 2)
        self.assertEqual(_eval_counter(b, "own_active_energy_water"), 2)
        self.assertEqual(_eval_counter(b, "own_all_energy"), 5)


class TestCoinEachUsesCounter(unittest.TestCase):
    def _flip(self, tag, seed=7):
        b = _arena(seed)
        self.b = b
        b.players[0].active.add_energy("water", 6)
        out = {"plus": 0, "minus": 0, "set": None, "zero": False,
               "nullify_all": False, "ignore_weakness": False,
               "ignore_resistance": False, "coins": []}
        b._resolve_coin_tag(b.players[0].active, tag, out)
        return out

    def test_coin_count_equals_energy(self):
        out = self._flip("coin_each:own_active_energy_water:heads:dmg_set:90")
        rec = out["coins"][0]
        # 6 个能量 → 必须抛 6 枚（不是 1 枚）。抛币次数从事件日志数，
        # 命中数不可能超过 6。
        coins = [e for e in self.b.events if e.get("type") == "coin"]
        self.assertEqual(len(coins), 6)
        self.assertLessEqual(rec["hits"], 6)

    def test_all_tails_sets_zero_damage(self):
        """全反面 → 「does N damage for each heads」必须是 0，不是印刷伤害。

        回归：`_apply_coin_sub` 此前只在 hits ≥ 1 时调用，`mods["set"]` 保持
        None，引擎回落到印刷伤害（实测 31 个招式在全反面时打出满伤）。
        """
        b = _arena(7)
        b.players[0].active.add_energy("water", 0)
        out = {"plus": 0, "minus": 0, "set": None, "zero": False,
               "nullify_all": False, "ignore_weakness": False,
               "ignore_resistance": False, "coins": []}
        # 1 枚币的 each：用固定 RNG 逼出反面
        class _Tails(random.Random):
            def random(self):
                return 0.9
        b.rng = _Tails(0)
        b._resolve_coin_tag(b.players[0].active,
                            "coin_each:1:heads:dmg_set:90", out)
        self.assertEqual(out["set"], 0)

    def test_zero_energy_means_zero_coins(self):
        b = _arena(7)
        out = {"plus": 0, "minus": 0, "set": None, "zero": False,
               "nullify_all": False, "ignore_weakness": False,
               "ignore_resistance": False, "coins": []}
        b._resolve_coin_tag(b.players[0].active,
                            "coin_each:own_active_energy:heads:dmg_set:70", out)
        self.assertEqual(out["set"], 0)


if __name__ == "__main__":
    unittest.main()
