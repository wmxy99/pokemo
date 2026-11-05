# -*- coding: utf-8 -*-
"""第九十一批（2026-11-05）· 引擎用例：训练家「检视牌库顶后重排」

覆盖 ``shuffle_top:N[:opp]`` 的**训练家侧派发**（Deduction Kit SSP 171）。

背景：该 tag 原先只在 ``_apply_attack_effects`` 派发（Team Rocket's Dottler
是招式卡），``_do_trainer`` 没有分支 → Deduction Kit 即便解析产出
``shuffle_top:3:own`` 也进不了池。与 ``energy_to_deck`` 属同一类陷阱：
判定「引擎支持」不能只看 ``battle.py`` 里出现过一次。

本文件锁定三条口径：
  · 只**重排顺序**：牌库张数不变、不弃置、不移动（这是 ``shuffle_top`` 的本义，
    与 ``mill`` / ``shuffle_draw`` 严格区分）；
  · 只动**顶部 N 张**：第 N+1 张及以下不得被 shuffle 波及；
  · ``:opp`` 后缀作用于对手牌库，缺省为自己。

⚠ 已知语义取舍（不假装完整实现）：Deduction Kit 卡面是「put them back in any
order, **or** shuffle them and put them on the bottom of your deck」——第二个
分支（洗回牌库底）未建模，按 choose 类「确定性默认」取第一分支。
"""
from __future__ import annotations
import random
import unittest

from engine.battle import Battle
from engine.cards import (EnergyDef, EnergyRef, PokemonDef, TrainerDef,
                          TrainerRef, make_pokemon)
from engine.state import GameConfig, PlayerState

KIT = "shuffle_top:3:own"


def _arena(seed: int = 7):
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
        PokemonDef("Attacker", hp=300, ptype="colorless", attacks=()))
    b.players[1].active = make_pokemon(
        PokemonDef("Defender", hp=300, ptype="colorless", attacks=()))
    # 用**可区分的卡**构造牌库（10 张各不同），便于验证「集合不变、顺序可变」
    for p in b.players:
        p.deck = [EnergyRef(EnergyDef(f"c{i}")) for i in range(10)]
    return b


def _in_hand(b, tag=KIT, name="Deduction Kit"):
    card = TrainerRef(TrainerDef(tag, kind="item", name=name))
    b.players[0].hand = [card]
    return card


class TestTrainerShuffleTop(unittest.TestCase):
    def test_reorders_top_without_moving_cards(self):
        """核心语义：只重排顶部 3 张，不移动、不弃置、不改变张数"""
        b = _arena()
        _in_hand(b)
        p = b.players[0]
        n_before = len(p.deck)
        top_before = {id(c) for c in p.deck[-3:]}
        deck_ids_before = {id(c) for c in p.deck}

        shuffled: list = []
        orig = b.rng.shuffle
        b.rng.shuffle = lambda x: (shuffled.append(list(x)), orig(x))[1]

        r = b.step({"verb": "trainer", "hand": 0})
        self.assertTrue(r["ok"], r.get("error"))
        b.rng.shuffle = orig

        self.assertTrue(shuffled, "必须真的调用 shuffle（否则等于没生效）")
        self.assertEqual(len(shuffled[0]), 3, "只重排顶部 3 张")
        self.assertEqual(len(p.deck), n_before,
                         "牌库张数不得变化（shuffle_top 不移动卡）")
        self.assertEqual({id(c) for c in p.deck}, deck_ids_before,
                         "牌库组成不得变化（没有卡被弃置或移走）")
        self.assertEqual({id(c) for c in p.deck[-3:]}, top_before,
                         "顶部 3 张仍在顶部，只是顺序可变")
        # 弃牌堆只有被打出的训练家卡本身（`discard` 是展示层 str、
        # `discard_refs` 是类型化层，同一张卡各记一份，不能相加）
        self.assertEqual(len(p.discard), 1, "弃牌堆应只有训练家卡本身")
        self.assertEqual(len(p.discard_refs), 1,
                         "类型化弃牌堆同样只应有训练家卡本身")

    def test_does_not_touch_beyond_top_n(self):
        """第 N+1 张及以下不得被波及"""
        b = _arena()
        _in_hand(b)
        p = b.players[0]
        below_before = [id(c) for c in p.deck[:-3]]
        r = b.step({"verb": "trainer", "hand": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual([id(c) for c in p.deck[:-3]], below_before,
                         "顶部 3 张以下的牌必须原封不动")

    def test_opp_variant_hits_opponent_deck(self):
        """``:opp`` 作用于对手牌库，己方不动"""
        b = _arena()
        _in_hand(b, tag="shuffle_top:2:opp")
        mine_before = [id(c) for c in b.players[0].deck]
        opp_called: list = []
        orig = b.rng.shuffle
        b.rng.shuffle = lambda x: (opp_called.append(len(x)), orig(x))[1]
        r = b.step({"verb": "trainer", "hand": 0})
        self.assertTrue(r["ok"], r.get("error"))
        b.rng.shuffle = orig
        self.assertEqual(opp_called, [2], "应重排对手牌库顶部 2 张")
        self.assertEqual([id(c) for c in b.players[0].deck], mine_before,
                         "己方牌库不得被动到")

    def test_n_ge_deck_size_does_not_crash(self):
        """N 超过牌库张数：夹到实际张数，不抛错、不越界"""
        b = _arena()
        _in_hand(b, tag="shuffle_top:99:own")
        b.players[0].deck = b.players[0].deck[:4]     # 只剩 4 张
        r = b.step({"verb": "trainer", "hand": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(b.players[0].deck), 4, "张数不变")

    def test_n_equals_one_is_noop(self):
        """N=1 时无需 shuffle（单张重排无意义），不得抛错"""
        b = _arena()
        _in_hand(b, tag="shuffle_top:1:own")
        called: list = []
        orig = b.rng.shuffle
        b.rng.shuffle = lambda x: (called.append(list(x)), orig(x))[1]
        r = b.step({"verb": "trainer", "hand": 0})
        self.assertTrue(r["ok"], r.get("error"))
        b.rng.shuffle = orig
        self.assertEqual(called, [], "N=1 不应调用 shuffle")


class TestShuffleTopIsNotMill(unittest.TestCase):
    """反向守卫：`shuffle_top` 与 `mill` 是反口径，绝不能互相退化"""

    def test_mill_does_reduce_deck(self):
        """对照：mill 会真的减少牌库（确认两者行为确实不同）"""
        b = _arena()
        _in_hand(b, tag="mill:3")
        n_before = len(b.players[0].deck)
        r = b.step({"verb": "trainer", "hand": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(b.players[0].deck), n_before - 3,
                         "mill 必须减少牌库（与 shuffle_top 区分）")


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
