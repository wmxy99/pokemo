# -*- coding: utf-8 -*-
"""第四十批（2026-11-02）· 引擎用例：连续掷币计数源 / 选 N 只进化 / 不同属性检索

覆盖：
  · ``coin_until_tails:dmg_set:N``      印刷伤害**按正面数重算**（0 正面 = 0）
  · ``coin_until_tails:mill:opp``       磨对手牌库（正面数 = 张数）
  · ``coin_until_tails:shuffle_opp_hand`` 对手手牌随机 N 张洗回其牌库
  · ``coin_until_tails:search:any``     牌库顶 N 张入手（**含能量卡**）
  · ``evolve_each_pick:<max>:<scope>``  **选**至多 N 只、各进化一次
  · ``search_energy:all:distinct``      每种属性各取 1 张（且**只取基本能量**）

负向守卫：`iter` 全反面 → 合法无效果（不报错、不改状态）；属性限定判不出 → 抛错。
"""
from __future__ import annotations
import random
import unittest

from engine.battle import Battle, _take_energy_from_deck
from engine.cards import (AttackDef, EnergyDef, EnergyRef, PokeRef, PokemonDef,
                          make_pokemon)
from engine.state import GameConfig, PlayerState


class _Fixed(random.Random):
    """固定抛币结果（0.4 → 正面；0.9 → 反面）"""

    def __init__(self, value: float):
        super().__init__(0)
        self.value = value

    def random(self) -> float:
        return self.value


class _Seq(random.Random):
    """按序返回固定抛币结果（0.4 → 正面；0.9 → 反面）"""

    def __init__(self, values):
        super().__init__(0)
        self.values = list(values)
        self.i = 0

    def random(self) -> float:
        v = self.values[min(self.i, len(self.values) - 1)]
        self.i += 1
        return v


def _arena(seed: int = 7, rng=None):
    cfg = GameConfig()
    b = object.__new__(Battle)
    b.cfg = cfg
    b.seed = seed
    b.rng = rng or random.Random(seed)
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


def _with_attack(b, effects, damage=0, cost=0):
    b.players[0].active = make_pokemon(PokemonDef(
        "Attacker", hp=500, ptype="colorless",
        attacks=(AttackDef("Test", cost=cost, damage=damage,
                           effects=tuple(effects)),)))


class TestUntilTailsDamageSet(unittest.TestCase):
    """`does N damage for each heads`（**没有 more**）→ 印刷伤害按正面数重算"""

    def test_zero_heads_means_zero(self):
        b = _arena(rng=_Fixed(0.9))      # 第一枚即反面
        _with_attack(b, ["coin_until_tails:dmg_set:40"], damage=40)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.damage, 0,
                         "0 正面必须是 0 伤害（保留印刷伤害就是静默加强）")

    def test_scales_by_heads_not_base(self):
        b = _arena(rng=_Seq([0.4, 0.4, 0.9]))     # 2 正面
        _with_attack(b, ["coin_until_tails:dmg_set:40"], damage=40)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.damage, 80)

    def test_plus_form_keeps_base(self):
        """对照：`dmg_plus` 是「在印刷伤害上**加**」，两者不可互换"""
        b = _arena(rng=_Fixed(0.9))
        _with_attack(b, ["coin_until_tails:dmg_plus:40"], damage=40)
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 40)


class TestUntilTailsNonDamage(unittest.TestCase):
    def test_mill_opponent_deck(self):
        b = _arena(rng=_Seq([0.4, 0.4, 0.9]))     # 2 正面 → 磨 2 张
        _with_attack(b, ["coin_until_tails:mill:opp"])
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        mills = [e for e in b.events if e.get("type") == "mill"]
        self.assertEqual(mills[-1]["n"], 2, "正面数 = 张数（不是常数 1）")
        self.assertEqual(len(b.players[1].discard), 2)

    def test_mill_all_tails_is_noop(self):
        b = _arena(rng=_Fixed(0.9))
        _with_attack(b, ["coin_until_tails:mill:opp"])
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertFalse([e for e in b.events if e.get("type") == "mill"],
                         "全反面 = 合法无效果，不磨牌也不报错")
        self.assertEqual(len(b.players[1].discard), 0)

    def test_shuffle_opponent_hand_into_deck(self):
        b = _arena(rng=_Seq([0.4, 0.4, 0.9]))     # 2 正面
        opp = b.players[1]
        opp.hand = [EnergyRef() for _ in range(3)]
        _with_attack(b, ["coin_until_tails:shuffle_opp_hand"], damage=30)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        moved = [e for e in b.events if e.get("type") == "shuffle_opp_hand"]
        self.assertEqual(moved[-1]["n"], 2)
        # 3 - 2 + 1（回合结束对手抽 1）= 2
        self.assertEqual(len(opp.hand), 2)

    def test_search_any_includes_energy(self):
        b = _arena(rng=_Seq([0.4, 0.4, 0.9]))     # 2 正面 → 取 2 张
        me = b.players[0]
        _with_attack(b, ["coin_until_tails:search:any"])
        before = len(me.hand)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(me.hand), before + 2, "「any cards」连能量卡也算")
        self.assertEqual(len(me.deck), 18)

    def test_unknown_sub_kind_raises(self):
        """未知子效果必须报错，不能静默不生效（step 会把 ValueError 转成 error）"""
        b = _arena(rng=_Fixed(0.9))
        _with_attack(b, ["coin_until_tails:bogus"])
        r = b.step({"verb": "attack", "attack": 0})
        self.assertFalse(r["ok"])
        self.assertIn("未支持的子效果", str(r.get("error")))


class TestEvolveEachPick(unittest.TestCase):
    """「Choose up to N of your {D} Pokémon. For each of those … evolve it」"""

    def _setup(self, b, effects):
        def mon(name, ptype="darkness", stage=0, evolves_from=None, attacks=()):
            return PokemonDef(name, hp=90, ptype=ptype, stage=stage,
                              evolves_from=evolves_from, attacks=attacks)
        active = mon("BaseDark", attacks=(
            AttackDef("Test", cost=0, damage=0, effects=tuple(effects)),))
        bench = [mon("BenchDark"), mon("WaterMon", ptype="water")]
        b.players[0].active = make_pokemon(active)
        b.players[0].bench = [make_pokemon(x) for x in bench]
        b.players[0].deck = [
            PokeRef(mon("EvoA", stage=1, evolves_from="BaseDark")),
            PokeRef(mon("EvoB", stage=1, evolves_from="BenchDark")),
            PokeRef(mon("EvoWater", stage=1, evolves_from="WaterMon")),
        ]

    def test_only_darkness_and_up_to_max(self):
        b = _arena()
        self._setup(b, ["evolve_each_pick:2:type-darkness"])
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        me = b.players[0]
        self.assertEqual(me.active.name, "EvoA", "战斗位也是合法目标")
        self.assertEqual(me.bench[0].name, "EvoB")
        self.assertEqual(me.bench[1].name, "WaterMon",
                         "属性限定外的那只**不得**进化")
        self.assertEqual(len(me.deck), 1, "用掉的进化卡应移出牌库")

    def test_max_zero_or_bad_shape_raises(self):
        b = _arena()
        self._setup(b, ["evolve_each_pick:0:type-darkness"])
        r = b.step({"verb": "attack", "attack": 0})
        self.assertFalse(r["ok"], "上限 0 的形态非法，不得静默当成「不进化」")
        self.assertIn("evolve_each_pick", str(r.get("error")))


class TestSearchEnergyDistinctAll(unittest.TestCase):
    def test_takes_one_per_type_and_skips_special(self):
        p = PlayerState(name="A", prizes_left=6)
        p.deck = [EnergyRef(EnergyDef("fire")),
                  EnergyRef(EnergyDef("water")),
                  EnergyRef(EnergyDef("fire")),
                  EnergyRef(EnergyDef("water")),
                  EnergyRef(EnergyDef("water", tag="senergy:legacy"))]
        got = _take_energy_from_deck(p, 999, None, distinct=True)
        self.assertEqual(got, 2, "每种属性各 1 张（「any number」上限被属性种类封死）")
        types = sorted(x.edef.type for x in p.hand)
        self.assertEqual(types, ["fire", "water"])
        left = [x for x in p.deck if isinstance(x, EnergyRef)]
        self.assertTrue(any(getattr(x.edef, "tag", "") for x in left),
                        "特殊能量不是 Basic Energy，必须留在牌库")

    def test_special_only_deck_yields_nothing(self):
        p = PlayerState(name="A", prizes_left=6)
        p.deck = [EnergyRef(EnergyDef("water", tag="senergy:legacy"))]
        self.assertEqual(_take_energy_from_deck(p, 2, None), 0)

    def test_constant_n_still_works(self):
        p = PlayerState(name="A", prizes_left=6)
        p.deck = [EnergyRef(EnergyDef("fire")) for _ in range(3)]
        self.assertEqual(_take_energy_from_deck(p, 2, None), 2)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
