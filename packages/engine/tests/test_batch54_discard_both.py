# -*- coding: utf-8 -*-
"""第五十四批（2026-11-04）· 引擎用例：**双方**弃手牌到剩 N 张

卡面（Hand Trimmer TEF 150）：
「Each player discards cards from their hand until they have 5 cards in their
hand. Your opponent discards first. (If a player has 5 or fewer cards in their
hand, they do not discard.)」

三处必须成立：
  · **双方**都减到 ≤ N（只弃对手 = 「双方」削成「单方」）；
  · 手牌 ≤ N 的一侧**弃 0 张**（卡面括号）；
  · 顺序按卡面「对手先弃」。
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
    return b


def _fill(pl, n):
    from engine.cards import TrainerDef, TrainerRef
    pl.hand = [TrainerRef(TrainerDef("draw:1", kind="item", name=f"c{i}"))
               for i in range(n)]


class TestDiscardToBoth(unittest.TestCase):
    def test_both_sides_go_down_to_n(self):
        b = _arena()
        _fill(b.players[0], 8)
        _fill(b.players[1], 9)
        b._discard_to_both(5)
        self.assertEqual(len(b.players[0].hand), 5)
        self.assertEqual(len(b.players[1].hand), 5)

    def test_small_hand_discards_nothing(self):
        """≤ N 的一侧弃 0 张 —— 不能为了「凑数」反手弃掉"""
        b = _arena()
        _fill(b.players[0], 3)
        _fill(b.players[1], 9)
        b._discard_to_both(5)
        self.assertEqual(len(b.players[0].hand), 3)
        self.assertEqual(len(b.players[1].hand), 5)

    def test_opponent_discards_first(self):
        """卡面「Your opponent discards first」—— 事件顺序必须是先对手后自己"""
        b = _arena()
        _fill(b.players[0], 7)
        _fill(b.players[1], 7)
        b._discard_to_both(5)
        order = [e["player"] for e in b.events
                 if e.get("type") == "discard_to_both"]
        self.assertEqual(order, [1, 0])

    def test_empty_hands_noop(self):
        b = _arena()
        _fill(b.players[0], 0)
        _fill(b.players[1], 0)
        self.assertEqual(b._discard_to_both(5), (0, 0))
