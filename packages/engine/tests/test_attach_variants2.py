# -*- coding: utf-8 -*-
"""PokéLab Engine · 附着的四个新形态 + 「不同属性」约束（2026-11-02 第三十九批）

覆盖五条新 tag，它们各自对应一种此前**无法表达**的语义：

1. ``attach_spread:...:distinct``（Terapagos SSP 161）
   「up to 3 Basic Energy cards **of different types**」——拿到的每张必须是**不同颜色**。
   ⚠ 这条约束此前**完全没建模**，且已波及池内的 Eevee SFA 50（见第 6 节）。

2. ``attach_spread_multi:<per1>:<t1>:<per2>:<t2>:<zone>``（Steven's Metagross ex）
   「a Basic {P} Energy card, a Basic {M} Energy card, **or 1 of each**」= 三选一
   ⇔「每类至多 per 张、总量可少」。拆成两条 `attach_spread:` 会强制每类各 1 张。

3. ``attach_each_pick:<max>:<per>:<etype>:<zone>:target:<scope>[:poison_if_active]``
   （Janine's Secret Art）——「Choose up to N … For each of those Pokémon」= **选出**
   至多 N 只，每只各 per 张；末尾的自我中毒是**条件性**的（只有附到战斗位才中毒）。

4. ``attach_flip_until:<etype>:<zone>``（Snorlax）—— 抛币到反面为止，正面数 = 张数。

5. ``coin_gate_attach:<coins>:<n>:<etype>:<zone>``（Energy Coin）—— 训练家侧抛币门。

6. ``search_energy:N:distinct``（Eevee SFA 50 的**既有缺陷修复**）—— 检索入手侧
   同样要「属性两两不同」。

运行：cd packages/engine && python -m unittest tests.test_attach_variants2 -v
"""
from __future__ import annotations

import random
import unittest

from engine import battle as B
from engine.battle import Battle
from engine.cards import EnergyDef, EnergyRef, PokemonDef, make_pokemon
from engine.state import GameConfig, PlayerState


def arena(seed: int = 7, bench: int = 2, energy: str = "psychic"):
    cfg = GameConfig()
    b = object.__new__(Battle)
    b.cfg = cfg
    b.seed = seed
    b.rng = random.Random(seed)
    b.players = [PlayerState(name="A", prizes_left=cfg.prizes),
                 PlayerState(name="B", prizes_left=cfg.prizes)]
    b.current = 0
    b.turn_number = 2
    b.turn_counts = [2, 2]
    b.phase = "main"
    b.finished = False
    b.winner = None
    b.win_reason = None
    b.events = []
    b.stadium = None
    b._ko_by_attack = [0, 0]
    b.players[0].active = make_pokemon(
        PokemonDef("Act", hp=200, ptype=energy, attacks=()))
    b.players[0].bench = [
        make_pokemon(PokemonDef(f"B{i}", hp=100, ptype=energy, attacks=()))
        for i in range(bench)]
    b.players[1].active = make_pokemon(
        PokemonDef("Opp", hp=200, ptype="colorless", attacks=()))
    b.players[0].deck = [EnergyRef(EnergyDef(energy, name=f"E{i}"))
                         for i in range(10)]
    b.players[1].deck = [EnergyRef() for _ in range(5)]
    return b


def _deck(b, *colors):
    b.players[0].deck = [EnergyRef(EnergyDef(c, name=f"{c}{i}"))
                         for i, c in enumerate(colors)]


def _heads(seed: int, cap: int = 40) -> int:
    """预演「抛到反面为止」的正面数（与引擎同口径：rng.random() < 0.5）"""
    r = random.Random(seed)
    n = 0
    for _ in range(cap):
        if r.random() < 0.5:
            n += 1
        else:
            break
    return n


def _all_heads(seed: int, coins: int = 2) -> bool:
    """预演「抛 coins 枚全正面」（与引擎同口径）"""
    r = random.Random(seed)
    return all(r.random() < 0.5 for _ in range(coins))


class TestDistinct(unittest.TestCase):
    """`of different types` = 拿到的每张必须换一个颜色"""

    def test_spread_takes_only_distinct(self):
        b = arena(bench=2)
        p = b.players[0]
        # 牌库：3 张草 + 1 张火（从末尾取 → 先遇到 fire 再是 grass）
        _deck(b, "psychic", "psychic", "psychic", "fire")
        b._do_attach_spread(
            p, "attach_spread:3:any:deck:distinct", {})
        total = p.active.total_energy() + sum(fp.total_energy() for fp in p.bench)
        self.assertEqual(total, 2, "同色能量被重复取用（distinct 未生效）")
        colors = set()
        for fp in [p.active] + list(p.bench):
            colors |= set(fp.energy)
        self.assertEqual(colors, {"psychic", "fire"})

    def test_spread_without_distinct_takes_same_color(self):
        """对照组：没有 `:distinct` 时必须能拿同色（不能凭空加限制）"""
        b = arena(bench=2)
        p = b.players[0]
        _deck(b, "psychic", "psychic", "psychic", "fire")
        b._do_attach_spread(p, "attach_spread:3:any:deck", {})
        total = p.active.total_energy() + sum(fp.total_energy() for fp in p.bench)
        self.assertEqual(total, 3)

    def test_search_energy_distinct_to_hand(self):
        b = arena()
        p = b.players[0]
        _deck(b, "grass", "grass", "grass", "water")
        got = B._take_energy_from_deck(p, 3, None, distinct=True)
        self.assertEqual(got, 2)
        got_colors = sorted(c.edef.type for c in p.hand)
        self.assertEqual(got_colors, ["grass", "water"])

    def test_search_energy_without_distinct(self):
        b = arena()
        p = b.players[0]
        _deck(b, "grass", "grass", "grass")
        self.assertEqual(B._take_energy_from_deck(p, 3, None), 3)


class TestSpreadMulti(unittest.TestCase):
    """双色「or 1 of each」= 每类至多 per 张、总量可少"""

    TAG = "attach_spread_multi:1:psychic:1:metal:deck:target:type-psychic+type-metal"

    def test_takes_one_of_each_when_both_available(self):
        b = arena(bench=1)
        p = b.players[0]
        p.bench[0] = make_pokemon(PokemonDef("M", hp=100, ptype="metal",
                                             attacks=()))
        _deck(b, "psychic", "metal")
        detail = b._do_attach_spread_multi(p, self.TAG, {})
        self.assertIn("attach 2", detail)
        self.assertEqual(p.active.total_energy(), 1)      # psychic
        self.assertEqual(p.bench[0].total_energy(), 1)    # metal

    def test_missing_color_is_not_an_error(self):
        """只有一种颜色时**取不满不算错**（卡面是「or 1 of each」三选一）"""
        b = arena(bench=1)
        p = b.players[0]
        p.bench[0] = make_pokemon(PokemonDef("M", hp=100, ptype="metal",
                                             attacks=()))
        _deck(b, "psychic", "psychic")
        detail = b._do_attach_spread_multi(p, self.TAG, {})
        self.assertIn("attach 1", detail)
        self.assertEqual(p.active.total_energy() + p.bench[0].total_energy(), 1)

    def test_target_scope_union(self):
        """`type-psychic+type-metal` 是**并集**：两类都能当目标"""
        b = arena(bench=2, energy="psychic")
        p = b.players[0]
        p.bench[1] = make_pokemon(PokemonDef("M", hp=100, ptype="metal",
                                             attacks=()))
        tg = B._spread_targets(p, "type-psychic+type-metal")
        self.assertEqual(len(tg), 3)          # active + bench0 + bench1

    def test_target_scope_union_excludes_others(self):
        b = arena(bench=2, energy="water")
        p = b.players[0]
        p.bench[1] = make_pokemon(PokemonDef("M", hp=100, ptype="metal",
                                             attacks=()))
        tg = B._spread_targets(p, "type-psychic+type-metal")
        self.assertEqual(len(tg), 1)          # 只有 bench1 的 metal
        self.assertIs(tg[0], p.bench[1])

    def test_bad_tag_rejected(self):
        b = arena()
        # 注意：**不带** `:target:` 是合法的（scope 默认为 any，与 `attach_spread`
        # 一致）；字段不足/张数非法/属性缺失/区域非法才应抛错。
        for bad in ("attach_spread_multi:1:psychic:1:metal",
                    "attach_spread_multi:0:psychic:1:metal:deck:target:any",
                    "attach_spread_multi:1:any:1:metal:deck:target:any",
                    "attach_spread_multi:1:psychic:1:metal:trash:target:any"):
            with self.assertRaises(ValueError, msg=bad):
                b._do_attach_spread_multi(b.players[0], bad, {})

    def test_no_target_means_any(self):
        b = arena(bench=1, energy="psychic")
        p = b.players[0]
        _deck(b, "psychic", "metal")
        detail = b._do_attach_spread_multi(
            p, "attach_spread_multi:1:psychic:1:metal:deck", {})
        self.assertIn("attach 2", detail)


class TestEachPick(unittest.TestCase):
    """「选至多 N 只、每只各 per 张」（Janine's Secret Art）"""

    TAG = "attach_each_pick:2:1:darkness:deck:target:type-darkness:poison_if_active"

    def test_picks_at_most_max(self):
        b = arena(bench=2, energy="darkness")
        p = b.players[0]
        _deck(b, "darkness", "darkness", "darkness")
        b._do_attach_each_pick(p, self.TAG, {})
        # max=2 → active + bench0 各 1；bench1 不该拿到
        self.assertEqual(p.active.total_energy(), 1)
        self.assertEqual(p.bench[0].total_energy(), 1)
        self.assertEqual(p.bench[1].total_energy(), 0)

    def test_poison_only_when_active_got_energy(self):
        b = arena(bench=2, energy="darkness")
        p = b.players[0]
        _deck(b, "darkness", "darkness", "darkness")
        self.assertNotIn("poison", p.active.status)
        b._do_attach_each_pick(p, self.TAG, {})
        self.assertIn("poison", p.active.status)

    def test_no_poison_when_active_not_targeted(self):
        """战斗位不是 {D} → 没附到它 → **不得**中毒（无条件中毒是错的）"""
        b = arena(bench=2, energy="darkness")
        p = b.players[0]
        p.active = make_pokemon(PokemonDef("W", hp=200, ptype="water",
                                           attacks=()))
        _deck(b, "darkness", "darkness")
        b._do_attach_each_pick(p, self.TAG, {})
        self.assertNotIn("poison", p.active.status)
        self.assertEqual(p.bench[0].total_energy() + p.bench[1].total_energy(), 2)

    def test_tag_without_poison_suffix(self):
        b = arena(bench=1, energy="darkness")
        p = b.players[0]
        _deck(b, "darkness", "darkness")
        b._do_attach_each_pick(
            p, "attach_each_pick:1:1:darkness:deck:target:type-darkness", {})
        self.assertNotIn("poison", p.active.status)

    def test_bad_tag_rejected(self):
        b = arena()
        for bad in ("attach_each_pick:2:1:darkness:deck:target",
                    "attach_each_pick:0:1:darkness:deck:target:type-darkness",
                    "attach_each_pick:2:0:darkness:deck:target:type-darkness",
                    "attach_each_pick:2:1:darkness:trash:target:type-darkness"):
            with self.assertRaises(ValueError, msg=bad):
                b._do_attach_each_pick(b.players[0], bad, {})


class TestFlipUntil(unittest.TestCase):
    """抛到反面为止，正面数 = 检索附能张数（Snorlax）"""

    def test_count_follows_heads(self):
        for seed in range(6):
            b = arena(seed=seed, bench=1)
            b.rng = random.Random(seed)
            expected = _heads(seed)
            b._do_attach_flip_until(b.players[0], "attach_flip_until:any:deck", {})
            self.assertEqual(b.players[0].active.total_energy(),
                             min(expected, 10), f"seed={seed}")

    def test_all_tails_means_noop(self):
        """第一次就反面 → 什么都不做（卡面「up to the number of heads」= 0）"""
        seed = next(s for s in range(200) if _heads(s) == 0)
        b = arena(seed=seed, bench=1)
        b.rng = random.Random(seed)
        before = len(b.players[0].deck)
        detail = b._do_attach_flip_until(
            b.players[0], "attach_flip_until:any:deck", {})
        self.assertEqual(detail, "")
        self.assertEqual(len(b.players[0].deck), before)

    def test_only_goes_to_active(self):
        b = arena(seed=3, bench=2)
        b.rng = random.Random(3)
        b._do_attach_flip_until(b.players[0], "attach_flip_until:any:deck", {})
        self.assertEqual(sum(fp.total_energy() for fp in b.players[0].bench), 0)


class TestCoinGate(unittest.TestCase):
    """训练家抛币门（Energy Coin）"""

    TAG = "coin_gate_attach:2:1:any:deck"

    def test_passes_when_all_heads(self):
        seed = next(s for s in range(200) if _all_heads(s))
        b = arena(seed=seed)
        b.rng = random.Random(seed)
        detail = b._trainer_coin_gate_attach(b.players[0], self.TAG, {})
        self.assertIn("attach 1", detail)
        self.assertEqual(b.players[0].active.total_energy(), 1)

    def test_fails_when_any_tails(self):
        seed = next(s for s in range(200) if not _all_heads(s))
        b = arena(seed=seed)
        b.rng = random.Random(seed)
        detail = b._trainer_coin_gate_attach(b.players[0], self.TAG, {})
        self.assertIn("no effect", detail)
        self.assertEqual(b.players[0].active.total_energy(), 0)

    def test_coin_is_logged(self):
        b = arena()
        b._trainer_coin_gate_attach(b.players[0], self.TAG, {})
        coins = [e for e in b.events if e["type"] == "coin"]
        self.assertEqual(len(coins), 1)

    def test_bad_tag_rejected(self):
        b = arena()
        for bad in ("coin_gate_attach:2:1:any", "coin_gate_attach:x:1:any:deck",
                    "coin_gate_attach:2:1:any:trash"):
            with self.assertRaises(ValueError, msg=bad):
                b._trainer_coin_gate_attach(b.players[0], bad, {})


if __name__ == "__main__":
    unittest.main()
