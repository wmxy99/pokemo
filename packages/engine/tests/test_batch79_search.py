# -*- coding: utf-8 -*-
"""第七十九批（2026-11-05）· 引擎用例：检索族

覆盖三条新 tag：
  · ``search_multi:item+tool+supporter+stadium:1``（Secret Box，四类别各 1 张）
  · ``search_bench:all:basic``（Precious Trolley，任意数量基础宝可梦铺后备）
  · ``search_top:2``（Ciphermaniac's Codebreaking / Dialga「Time Manipulation」）

口径守卫：
  · `search_multi` 未知筛选项抛错（不猜、不静默放行）；
  · `search_top` 不增加手牌（放牌库顶 ≠ 入手）；
  · `search_bench:all` 只铺基础宝可梦，不进化的也会被过滤。

运行：cd packages/engine && python -m unittest tests.test_batch79_search -v
"""
from __future__ import annotations

import random
import unittest

from engine.battle import Battle
from engine.cards import (EnergyDef, EnergyRef, PokemonDef, TrainerDef,
                          TrainerRef, PokeRef, make_pokemon)
from engine.state import GameConfig, PlayerState


def _battle():
    cfg = GameConfig()
    b = object.__new__(Battle)
    b.cfg = cfg
    b.seed = 7
    b.rng = random.Random(7)
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
    return b


def _poke(name, stage=0):
    return PokeRef(PokemonDef(name, hp=100, ptype="colorless", stage=stage))


def _trainer(name, kind):
    return TrainerRef(TrainerDef(tag="x", kind=kind, name=name))


class TestSearchMulti(unittest.TestCase):
    def test_each_category_one(self):
        b = _battle()
        p = b.players[0]
        p.deck = [_trainer("Item", "item"), _trainer("Tool", "tool"),
                  _trainer("Supporter", "supporter"),
                  _trainer("Stadium", "stadium"),
                  _poke("Pikachu"), EnergyRef(EnergyDef("colorless"))]
        detail = b._search_multi(
            p, "search_multi:item+tool+supporter+stadium:1")
        self.assertEqual(len(p.hand), 4, detail)
        kinds = sorted(c.tdef.kind for c in p.hand)
        self.assertEqual(kinds, ["item", "stadium", "supporter", "tool"],
                         "必须**逐类别各取 1 张**，不能只取任意 1 张")
        # 宝可梦与能量不得被拿走
        self.assertTrue(any(isinstance(c, PokeRef) for c in p.deck))

    def test_unknown_alt_raises(self):
        b = _battle()
        p = b.players[0]
        p.deck = [_trainer("Item", "item")]
        with self.assertRaises(ValueError):
            b._search_multi(p, "search_multi:artifact:1")


class TestSearchBenchAll(unittest.TestCase):
    def test_all_basics_only(self):
        b = _battle()
        p = b.players[0]
        p.deck = [_poke("Basic A"), _poke("Stage1", stage=1),
                  _poke("Basic B"), EnergyRef(EnergyDef("colorless")),
                  _poke("Basic C")]
        b._search_bench(p, "search_bench:all:basic")
        self.assertEqual(len(p.bench), 3, "全部 3 只**基础**宝可梦都应铺上")
        self.assertEqual(len(p.deck), 2, "进化型与能量留在牌库")

    def test_respects_bench_cap(self):
        b = _battle()
        p = b.players[0]
        p.deck = [_poke(f"B{i}") for i in range(10)]
        b._search_bench(p, "search_bench:all:basic")
        self.assertEqual(len(p.bench), b.cfg.max_bench)
        self.assertEqual(len(p.deck), 10 - b.cfg.max_bench)


class TestSearchTop(unittest.TestCase):
    def test_no_hand_gain_and_top_two(self):
        b = _battle()
        p = b.players[0]
        p.deck = [EnergyRef(EnergyDef("colorless", name=f"E{i}")) for i in range(6)]
        before_hand = len(p.hand)
        before_deck = len(p.deck)
        top_before = p.deck[-1].edef.name
        detail = b._search_top(p, "search_top:2")
        self.assertEqual(len(p.hand), before_hand,
                         "放回牌库顶**不得**增加手牌")
        self.assertEqual(len(p.deck), before_deck)
        self.assertEqual(p.deck[-1].edef.name, top_before,
                         "原牌库顶应仍在最顶（picked[0] 放回顶部）")

    def test_short_deck_takes_available(self):
        b = _battle()
        p = b.players[0]
        p.deck = [EnergyRef(EnergyDef("colorless"))]
        detail = b._search_top(p, "search_top:2")
        self.assertEqual(len(p.deck), 1, detail)
        self.assertEqual(len(p.hand), 0)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
