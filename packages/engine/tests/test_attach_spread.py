# -*- coding: utf-8 -*-
"""PokéLab Engine · 「检索能量自由分配附着」用例（2026-10-30 第三十三批）

覆盖 ``attach_spread:<n>:<etype>:<zone>[:target:<scope>]``：

  · round-robin 分配（active → bench 顺序、可循环）；
  · 目标限定 ``bench`` / ``cat-<子串>``；
  · ``action['attach_plan']`` 显式分配及其两类非法输入；
  · 只取**基本**能量（卡面都写 Basic）与属性限定；
  · 无符合能量 / 目标集合为空 → 效果不执行（不是错误）；
  · 卡面「Then, shuffle your deck」→ 动过牌库必须洗。

一条**前缀守卫**：``attach_spread:`` 必须排在既有 ``attach`` 分支之前 ——
``tag.startswith("attach")`` 会把它吞掉，随后 ``_parse_attach_tag`` 把
``spread`` 当成能量属性名，效果整条静默失效（全附给 active 且找不到能量）。

运行：cd packages/engine && python -m unittest tests.test_attach_spread -v
"""
from __future__ import annotations

import random
import unittest

from engine.battle import (Battle, _parse_attach_spread_tag, _spread_targets)
from engine.cards import (AttackDef, EnergyDef, EnergyRef, PokemonDef,
                          make_pokemon)
from engine.state import GameConfig, PlayerState


def arena(seed: int = 7, bench: int = 2):
    cfg = GameConfig()
    b = object.__new__(Battle)
    b.cfg = cfg
    b.seed = seed
    b.rng = random.Random(seed)
    b.players = [PlayerState(name="A", prizes_left=cfg.prizes),
                 PlayerState(name="B", prizes_left=cfg.prizes)]
    b.current = 0
    b.turn_number = 1
    b.turn_counts = [2, 2]
    b.phase = "main"
    b.finished = False
    b.winner = None
    b.win_reason = None
    b.events = []
    b.stadium = None
    b._ko_by_attack = [0, 0]
    b.players[0].active = make_pokemon(
        PokemonDef("Act", hp=200, ptype="colorless", attacks=()))
    b.players[0].bench = [
        make_pokemon(PokemonDef(f"B{i}", hp=100, ptype="colorless", attacks=()))
        for i in range(bench)]
    b.players[1].active = make_pokemon(
        PokemonDef("Opp", hp=200, ptype="colorless", attacks=()))
    b.players[0].deck = [EnergyRef(EnergyDef("grass", name=f"G{i}"))
                         for i in range(10)]
    b.players[1].deck = [EnergyRef() for _ in range(10)]
    return b


def _deck_of(b, *colors):
    """把玩家 0 的牌库设为给定颜色序列（第一项 = **牌库顶**）"""
    b.players[0].deck = [EnergyRef(EnergyDef(c, name=f"{c}{i}"))
                         for i, c in enumerate(colors)]
    return b


def _with_attack(b, effects, damage: int = 0):
    b.players[0].active = make_pokemon(PokemonDef(
        "Act", hp=500, ptype="colorless",
        attacks=(AttackDef("Test", cost=0, damage=damage,
                           effects=tuple(effects)),)))


class TestParse(unittest.TestCase):
    # 2026-11-02 第三十七批：返回值多一项 `selfko`（`:selfko` 尾段）
    def test_minimal(self):
        self.assertEqual(_parse_attach_spread_tag("attach_spread:3:any:deck"),
                         (3, None, "deck", "any", False))

    def test_full(self):
        self.assertEqual(
            _parse_attach_spread_tag("attach_spread:2:grass:deck:target:bench"),
            (2, "grass", "deck", "bench", False))

    def test_bad_count(self):
        for bad in ("0", "x", "-1"):
            with self.assertRaises(ValueError):
                _parse_attach_spread_tag(f"attach_spread:{bad}:any:deck")

    def test_bad_zone(self):
        with self.assertRaises(ValueError):
            _parse_attach_spread_tag("attach_spread:2:any:trash")

    def test_short_tag(self):
        with self.assertRaises(ValueError):
            _parse_attach_spread_tag("attach_spread:2:any")

    def test_unknown_scope_raises(self):
        b = arena()
        with self.assertRaises(ValueError):
            _spread_targets(b.players[0], "nonsense")


class TestAllocation(unittest.TestCase):
    """round-robin 与目标限定"""

    def test_round_robin_over_active_and_bench(self):
        b = arena(bench=2)
        p = b.players[0]
        detail = b._do_attach_spread(p, "attach_spread:3:any:deck", {})
        self.assertEqual(detail, "attach 3 any spread over 3")
        self.assertEqual(p.active.total_energy(), 1)
        self.assertEqual(p.bench[0].total_energy(), 1)
        self.assertEqual(p.bench[1].total_energy(), 1)

    def test_wraps_when_more_energy_than_targets(self):
        b = arena(bench=1)
        p = b.players[0]
        b._do_attach_spread(p, "attach_spread:3:any:deck", {})
        # 目标 = active + 1 只后备 → 3 张按 2 2 1 分配
        self.assertEqual(p.active.total_energy(), 2)
        self.assertEqual(p.bench[0].total_energy(), 1)

    def test_bench_scope_excludes_active(self):
        b = arena(bench=2)
        p = b.players[0]
        b._do_attach_spread(p, "attach_spread:3:any:deck:target:bench", {})
        self.assertEqual(p.active.total_energy(), 0)
        self.assertEqual(p.bench[0].total_energy() + p.bench[1].total_energy(), 3)

    def test_cat_scope_filters_by_category(self):
        b = arena(bench=1)
        p = b.players[0]
        p.bench[0] = make_pokemon(PokemonDef(
            "Future1", hp=100, ptype="colorless", attacks=(), category="Future"))
        b._do_attach_spread(p, "attach_spread:2:any:deck:target:cat-future", {})
        self.assertEqual(p.bench[0].total_energy(), 2)
        self.assertEqual(p.active.total_energy(), 0)

    def test_cat_scope_empty_means_noop(self):
        b = arena(bench=1)             # 没有 Future 宝可梦
        p = b.players[0]
        self.assertEqual(
            b._do_attach_spread(p, "attach_spread:2:any:deck:target:cat-future",
                                {}), "")
        self.assertEqual(p.active.total_energy(), 0)

    def test_bench_scope_with_no_bench_is_noop(self):
        b = arena(bench=0)
        p = b.players[0]
        self.assertEqual(
            b._do_attach_spread(p, "attach_spread:2:any:deck:target:bench", {}), "")
        self.assertEqual(p.active.total_energy(), 0)

    def test_takes_only_basic_energy(self):
        b = arena()
        p = b.players[0]
        p.deck = [EnergyRef(EnergyDef("fire", name="SpecialF",
                                      tag="senergy:test")),
                  EnergyRef(EnergyDef("grass", name="BasicG"))]
        b._do_attach_spread(p, "attach_spread:2:any:deck", {})
        self.assertEqual(p.active.total_energy(), 1)     # 只取基本能量

    def test_energy_type_filter(self):
        b = arena()
        p = b.players[0]
        _deck_of(b, "fire", "fire", "grass", "grass")
        b._do_attach_spread(p, "attach_spread:2:grass:deck", {})
        total = p.active.total_energy() + sum(fp.total_energy() for fp in p.bench)
        self.assertEqual(total, 2)                       # 只取草能量
        self.assertEqual(len(p.deck), 2)                 # 2 张火能量留在牌库

    def test_no_matching_energy_is_noop(self):
        b = arena()
        p = b.players[0]
        _deck_of(b, "fire", "fire")
        self.assertEqual(
            b._do_attach_spread(p, "attach_spread:2:grass:deck", {}), "")
        self.assertEqual(p.active.total_energy(), 0)
        self.assertEqual(len(p.deck), 2)


class TestShuffle(unittest.TestCase):
    """卡面固定后缀「Then, shuffle your deck」"""

    def test_deck_is_shuffled(self):
        b = arena()
        p = b.players[0]
        before = b.rng.getstate()
        b._do_attach_spread(p, "attach_spread:2:any:deck", {})
        self.assertNotEqual(b.rng.getstate(), before)

    def test_no_shuffle_when_nothing_moved(self):
        b = arena()
        p = b.players[0]
        _deck_of(b, "fire")
        before = b.rng.getstate()
        b._do_attach_spread(p, "attach_spread:2:grass:deck", {})
        self.assertEqual(b.rng.getstate(), before)


class TestAttachPlan(unittest.TestCase):
    def test_explicit_plan(self):
        b = arena(bench=2)
        p = b.players[0]
        b._do_attach_spread(p, "attach_spread:2:any:deck",
                            {"attach_plan": [2, 2]})
        self.assertEqual(p.bench[1].total_energy(), 2)
        self.assertEqual(p.active.total_energy(), 0)

    def test_plan_position_not_in_scope_raises(self):
        """显式指到不符合卡面限定的宝可梦 → 抛错，不得悄悄改附别人"""
        b = arena(bench=1)
        p = b.players[0]
        with self.assertRaises(ValueError):
            b._do_attach_spread(p, "attach_spread:2:any:deck:target:bench",
                                {"attach_plan": [0]})

    def test_plan_out_of_range_raises(self):
        b = arena(bench=1)
        p = b.players[0]
        with self.assertRaises(ValueError):
            b._do_attach_spread(p, "attach_spread:2:any:deck",
                                {"attach_plan": [9]})

    def test_plan_non_int_raises(self):
        b = arena()
        p = b.players[0]
        with self.assertRaises(ValueError):
            b._do_attach_spread(p, "attach_spread:2:any:deck",
                                {"attach_plan": ["0"]})


class TestAttackPath(unittest.TestCase):
    """招式全链路 —— 同时是 `attach_spread:` 前缀守卫

    若新分支被 `startswith("attach")` 吞掉，`_parse_attach_tag` 会把 ``spread``
    当能量属性名 → 一张都取不到（或全部附给 active），断言会失败。
    """

    def test_spreads_over_field(self):
        b = arena(bench=2)
        p = b.players[0]
        _with_attack(b, ["attach_spread:3:any:deck"])
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(p.active.total_energy(), 1)
        self.assertEqual(p.bench[0].total_energy(), 1)
        self.assertEqual(p.bench[1].total_energy(), 1)

    def test_not_confused_with_single_target_attach(self):
        """前缀守卫的反面：普通 `attach` 仍走单目标语义（不被新分支抢走）"""
        b = arena(bench=2)
        p = b.players[0]
        _deck_of(b, "grass", "grass")
        _with_attack(b, ["attach:2:grass:deck"])
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(p.active.total_energy(), 2)     # 全在攻击方身上
        self.assertEqual(p.bench[0].total_energy(), 0)


class TestBadTagSurfaced(unittest.TestCase):
    def test_unknown_zone_fails_step(self):
        b = arena()
        _with_attack(b, ["attach_spread:2:any:trash"])
        r = b.step({"verb": "attack", "attack": 0})
        self.assertFalse(r["ok"])
        self.assertIn("attach_spread", str(r.get("error")))


if __name__ == "__main__":
    unittest.main()
