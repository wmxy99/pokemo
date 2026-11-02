# -*- coding: utf-8 -*-
"""PokéLab Engine · 自由分配附着的两个变体（2026-11-02 第三十八批）

覆盖两条新 tag，它们**都不是** `attach_spread` 能表达的形态：

1. ``attach_spread_flip:<coins>:<per>:<etype>:<zone>[:target:<scope>]``
   （Mega Audino ex `Kaleidowaltz`）
   「Flip 3 coins. For each heads, search your deck for up to 2 Basic Energy
    cards and attach them to your Pokémon in any way you like.」
   → 张数 = 正面枚数 × per，**结算时**现算。解析期折算成任何常数（期望 3 /
   上限 6 / 每正面 2）都是静默改变强度。

2. ``attach_each_bench:<per>:<etype>:<zone>``
   （Mega Gardevoir ex `Overflowing Wishes`）
   「For each of your Benched Pokémon, search your deck for a Basic {P} Energy
    card and attach it to that Pokémon.」
   → **每只后备各 per 张、绑定给那一只**。与 spread 的「n 张任意分配」是两种
   语义：spread 允许漏掉某只、允许把多张堆在同一只上，卡面都不允许。

3. 进化触发形态的 `evolve_act:attach_spread:...`（Marnie's Grimmsnarl ex）
   必须由 `_apply_attack_effects` 在**进化那一刻**执行一次，不能退化成
   「每回合一次的 field 特性」。

运行：cd packages/engine && python -m unittest tests.test_attach_variants -v
"""
from __future__ import annotations

import random
import unittest

from engine.battle import (Battle, _parse_attach_each_bench_tag,
                           _parse_attach_spread_flip_tag)
from engine.cards import EnergyDef, EnergyRef, PokemonDef, make_pokemon
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
    b.players[0].deck = [EnergyRef(EnergyDef("psychic", name=f"P{i}"))
                         for i in range(10)]
    b.players[1].deck = [EnergyRef() for _ in range(10)]
    return b


class TestParseFlip(unittest.TestCase):

    def test_minimal(self):
        self.assertEqual(
            _parse_attach_spread_flip_tag("attach_spread_flip:3:2:any:deck"),
            (3, 2, None, "deck", "any"))

    def test_with_scope(self):
        self.assertEqual(
            _parse_attach_spread_flip_tag(
                "attach_spread_flip:3:2:grass:deck:target:bench"),
            (3, 2, "grass", "deck", "bench"))

    def test_bad_fields(self):
        for bad in ("attach_spread_flip:3:2:any",          # 字段不足
                    "attach_spread_flip:0:2:any:deck",     # 抛币数非法
                    "attach_spread_flip:3:0:any:deck",     # 每正面张数非法
                    "attach_spread_flip:x:2:any:deck",
                    "attach_spread_flip:3:2:any:trash",    # 区域非法
                    "attach_spread_flip:3:2:any:deck:target:"):
            with self.assertRaises(ValueError, msg=bad):
                _parse_attach_spread_flip_tag(bad)


class TestParseEachBench(unittest.TestCase):

    def test_minimal(self):
        self.assertEqual(_parse_attach_each_bench_tag("attach_each_bench:1:psychic:deck"),
                         (1, "psychic", "deck"))

    def test_any_energy(self):
        self.assertEqual(_parse_attach_each_bench_tag("attach_each_bench:2:any:deck"),
                         (2, None, "deck"))

    def test_bad_fields(self):
        for bad in ("attach_each_bench:1:psychic",              # 字段数不对
                    "attach_each_bench:1:psychic:deck:target:bench",
                    "attach_each_bench:0:psychic:deck",
                    "attach_each_bench:1:psychic:trash"):
            with self.assertRaises(ValueError, msg=bad):
                _parse_attach_each_bench_tag(bad)


class TestFlip(unittest.TestCase):
    """张数 = heads × per，由**抛币结果**决定"""

    def _expected_heads(self, seed: int, coins: int) -> int:
        r = random.Random(seed)
        return sum(1 for _ in range(coins) if r.random() < 0.5)

    def test_count_follows_coin_flips(self):
        for seed in range(8):
            b = arena(seed=seed, bench=2)
            b.rng = random.Random(seed)
            heads = self._expected_heads(seed, 3)
            b._do_attach_spread_flip(
                b.players[0], "attach_spread_flip:3:2:any:deck", {})
            p = b.players[0]
            total = p.active.total_energy() + sum(fp.total_energy()
                                                  for fp in p.bench)
            self.assertEqual(total, heads * 2,
                             f"seed={seed} heads={heads}")

    def test_coin_is_logged(self):
        b = arena(bench=1)
        b._do_attach_spread_flip(b.players[0],
                                 "attach_spread_flip:3:2:any:deck", {})
        coins = [e for e in b.events if e["type"] == "coin"]
        self.assertEqual(len(coins), 1)
        self.assertIn("heads", coins[0]["result"])

    def test_zero_heads_is_noop_and_does_not_shuffle(self):
        """全反面 → 不动牌库（卡面「Then, shuffle」只在真的检索过之后才有意义）"""
        b = arena(bench=2)
        # 找 3 连正面数 = 0 的种子
        seed = next(s for s in range(200)
                    if self._expected_heads(s, 3) == 0)
        b = arena(seed=seed, bench=2)
        b.rng = random.Random(seed)
        before = list(b.players[0].deck)
        detail = b._do_attach_spread_flip(
            b.players[0], "attach_spread_flip:3:2:any:deck", {})
        self.assertEqual(detail, "")
        self.assertEqual(b.players[0].deck, before)

    def test_respects_plan(self):
        """显式 `attach_plan` 优先于轮转；计划外的位置不会被分配到"""
        seed = next(s for s in range(200) if self._expected_heads(s, 3) >= 2)
        heads = self._expected_heads(seed, 3)
        b = arena(seed=seed, bench=2)
        b.rng = random.Random(seed)
        b._do_attach_spread_flip(
            b.players[0], "attach_spread_flip:3:1:any:deck",
            {"attach_plan": [1, 2]})
        p = b.players[0]
        # 计划只给了 2 个位置 → 战斗位一张都不该拿到
        self.assertEqual(p.active.total_energy(), 0)
        self.assertEqual(sum(fp.total_energy() for fp in p.bench),
                         min(heads, 2))


class TestEachBench(unittest.TestCase):
    """每只后备**各** per 张，绑定给那一只"""

    def test_one_each(self):
        b = arena(bench=2)
        p = b.players[0]
        detail = b._do_attach_each_bench(p, "attach_each_bench:1:psychic:deck", {})
        self.assertEqual(detail, "attach 2 psychic to 2 bench")
        self.assertEqual(p.active.total_energy(), 0)      # 不碰战斗位
        self.assertEqual(p.bench[0].total_energy(), 1)
        self.assertEqual(p.bench[1].total_energy(), 1)

    def test_per_two(self):
        b = arena(bench=2)
        p = b.players[0]
        b._do_attach_each_bench(p, "attach_each_bench:2:psychic:deck", {})
        self.assertEqual(p.bench[0].total_energy(), 2)
        self.assertEqual(p.bench[1].total_energy(), 2)
        self.assertEqual(len(p.deck), 6)

    def test_short_deck_only_affects_one(self):
        """牌库只剩 3 张：第一只取满 2、第二只取 1 —— 剩下的继续分给后面的"""
        b = arena(bench=2)
        p = b.players[0]
        p.deck = [EnergyRef(EnergyDef("psychic", name=f"L{i}")) for i in range(3)]
        b._do_attach_each_bench(p, "attach_each_bench:2:psychic:deck", {})
        self.assertEqual(p.bench[0].total_energy(), 2)
        self.assertEqual(p.bench[1].total_energy(), 1)
        self.assertEqual(p.deck, [])

    def test_no_bench_is_noop(self):
        b = arena(bench=0)
        p = b.players[0]
        self.assertEqual(
            b._do_attach_each_bench(p, "attach_each_bench:1:psychic:deck", {}), "")
        self.assertEqual(p.deck.__len__(), 10)

    def test_attach_plan_rejected(self):
        """分配不可选：显式给计划要抛错（不能「收下但忽略」）"""
        b = arena(bench=2)
        with self.assertRaises(ValueError):
            b._do_attach_each_bench(
                b.players[0], "attach_each_bench:1:psychic:deck",
                {"attach_plan": [1]})

    def test_only_basic_energy(self):
        b = arena(bench=1)
        p = b.players[0]
        p.deck = [EnergyRef(EnergyDef("psychic", name="SpecialP",
                                      tag="senergy:test"))]
        self.assertEqual(
            b._do_attach_each_bench(p, "attach_each_bench:1:psychic:deck", {}), "")


class TestEvolveActSpread(unittest.TestCase):
    """`evolve_act:attach_spread:...` 在进化那一刻执行一次（Marnie's Grimmsnarl ex）"""

    def _marnie(self, b):
        fp = make_pokemon(PokemonDef(
            "Marnie's Grimmsnarl ex", hp=320, ptype="darkness", attacks=(),
            category="Trainer's Pokémon(Marnie)"))
        b.players[0].active = fp
        return fp

    def test_runs_attach_spread(self):
        b = arena(bench=0)
        fp = self._marnie(b)
        b.players[0].deck = [
            EnergyRef(EnergyDef("darkness", name=f"D{i}")) for i in range(6)]
        b._apply_attack_effects(
            fp, ("attach_spread:5:darkness:deck:target:cat-marnie",), action={})
        self.assertEqual(fp.total_energy(), 5)

    def test_cat_scope_excludes_non_marnie(self):
        """限定 `cat-marnie`：非玛俐宝可梦不能当目标（否则能量无处可去）"""
        b = arena(bench=1)
        fp = make_pokemon(PokemonDef("Plain", hp=200, ptype="darkness",
                                     attacks=()))
        b.players[0].active = fp
        b.players[0].bench = [make_pokemon(PokemonDef(
            "Other", hp=100, ptype="darkness", attacks=()))]
        b.players[0].deck = [
            EnergyRef(EnergyDef("darkness", name=f"D{i}")) for i in range(6)]
        b._apply_attack_effects(
            fp, ("attach_spread:5:darkness:deck:target:cat-marnie",), action={})
        self.assertEqual(fp.total_energy(), 0)
        self.assertEqual(b.players[0].bench[0].total_energy(), 0)


if __name__ == "__main__":
    unittest.main()
