# -*- coding: utf-8 -*-
"""第五十七批（2026-11-04）· 引擎用例：`fx:play_block:`（常驻手牌禁打）

三处必须成立：
  · 封锁源在**对手**场上（受害者是「想打牌的那一方」）；
  · ``:active`` = 只有持有者在**战斗位**时才生效（在后备不生效）；
  · ``ability+tool`` / ``ability_pokemon`` 的对象判定要按卡牌子类与「有特性」；
  · `:except:<key>` 的卡名例外必须生效（Team Rocket's Arbok）。

⚠ 判定错方向 = 要么把玩家的合法动作全禁掉（对局卡死），要么封锁整段消失。
"""
from __future__ import annotations
import random
import unittest

from engine import families
from engine.battle import Battle
from engine.cards import (AbilityDef, PokemonDef, TrainerDef, TrainerRef,
                          make_pokemon)
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


def _blocker(tag, name="Blocker"):
    return make_pokemon(PokemonDef(
        name, hp=120, ptype="darkness", attacks=(),
        abilities=(AbilityDef("Gaze", tag=tag),)))


def _item(name="Item"):
    return TrainerRef(TrainerDef("draw:1", kind="item", name=name))


def _tool(name="Tool"):
    return TrainerRef(TrainerDef("tool:flat:0", kind="tool", name=name))


class TestPlayBlockItem(unittest.TestCase):
    def test_blocks_item_when_active(self):
        b = _arena()
        b.players[1].active = _blocker("fx:play_block:item:active")
        self.assertTrue(families.blocked_play(b, b.players[0], _item()))

    def test_no_block_when_bench(self):
        """:active —— 在后备时不生效（否则等于永久封锁）"""
        b = _arena()
        b.players[1].bench = [_blocker("fx:play_block:item:active")]
        self.assertFalse(families.blocked_play(b, b.players[0], _item()))

    def test_holder_side_not_blocked(self):
        """封锁源**不封自己人**：持有者那侧打物品卡应当允许"""
        b = _arena()
        b.players[1].active = _blocker("fx:play_block:item:active")
        self.assertFalse(families.blocked_play(b, b.players[1], _item()))

    def test_supporter_unaffected(self):
        b = _arena()
        b.players[1].active = _blocker("fx:play_block:item:active")
        sup = TrainerRef(TrainerDef("draw:3", kind="supporter", name="Sup"))
        self.assertFalse(families.blocked_play(b, b.players[0], sup))


class TestPlayBlockItemTool(unittest.TestCase):
    def test_blocks_both_kinds(self):
        b = _arena()
        b.players[1].active = _blocker("fx:play_block:item+tool:active")
        self.assertTrue(families.blocked_play(b, b.players[0], _item()))
        self.assertTrue(families.blocked_play(b, b.players[0], _tool()))


class TestPlayBlockAbilityPokemon(unittest.TestCase):
    TAG = "fx:play_block:ability_pokemon:active:except:teamrockets"

    def test_blocks_pokemon_with_ability(self):
        b = _arena()
        b.players[1].active = _blocker(self.TAG)
        from engine.cards import PokeRef
        mon = PokeRef(PokemonDef("Some ex", hp=200, ptype="colorless",
                                 attacks=(),
                                 abilities=(AbilityDef("X", tag="draw:1"),)))
        self.assertTrue(families.blocked_play(b, b.players[0], mon))

    def test_plain_pokemon_allowed(self):
        b = _arena()
        b.players[1].active = _blocker(self.TAG)
        from engine.cards import PokeRef
        mon = PokeRef(PokemonDef("Plain", hp=60, ptype="colorless", attacks=()))
        self.assertFalse(families.blocked_play(b, b.players[0], mon))

    def test_exception_faction_allowed(self):
        b = _arena()
        b.players[1].active = _blocker(self.TAG)
        from engine.cards import PokeRef
        mon = PokeRef(PokemonDef("Team Rocket's Kangaskhan ex", hp=230,
                                 ptype="colorless", attacks=(),
                                 abilities=(AbilityDef("Y", tag="draw:1"),)))
        self.assertFalse(families.blocked_play(b, b.players[0], mon))


class TestUnknownTagRaises(unittest.TestCase):
    def test_bad_what(self):
        b = _arena()
        b.players[1].active = _blocker("fx:play_block:stadium:active")
        with self.assertRaises(ValueError):
            families.blocked_play(b, b.players[0], _item())

    def test_bad_when(self):
        b = _arena()
        b.players[1].active = _blocker("fx:play_block:item:sometimes")
        with self.assertRaises(ValueError):
            families.blocked_play(b, b.players[0], _item())
