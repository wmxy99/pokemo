# -*- coding: utf-8 -*-
"""fx 族：静态伤害增减 —— 引擎侧单测（范围 / 条件 / 非叠加 / 伤害顺序）

覆盖 S1 静态修正层的两类钩子：
  · `fx:static_dmg:*` 由效果族在 `_attack_mods` 中叠加（弱点/抗性之前）；
  · `passive:dmg_reduce:N:from:type:*` 在弱抗之后按攻击方属性判定。

纪律：未知 tag 形态必须抛 ValueError（不能静默忽略）；0 基础伤害的招式
不得因为常驻加伤凭空打出伤害。
"""
from __future__ import annotations

import random
import unittest

from engine.battle import Battle
from engine.cards import (AbilityDef, AttackDef, PokemonDef, make_pokemon)
from engine.families import fx_static_dmg as F
from engine.state import GameConfig, PlayerState


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
    b.players[0].active = make_pokemon(PokemonDef(
        "Attacker", hp=200, ptype="colorless",
        attacks=(AttackDef("Hit", cost=0, damage=100),)))
    b.players[1].active = make_pokemon(PokemonDef(
        "Defender", hp=300, ptype="colorless"))
    return b


def _mon(name, tag=None, ptype="colorless", stage=0, hp=100, damage=0):
    abs_ = (AbilityDef("A", tag),) if tag else ()
    fp = make_pokemon(PokemonDef(name, hp=hp, ptype=ptype, stage=stage,
                                 abilities=abs_))
    fp.damage = damage
    return fp


def _with_source(tag, **kw):
    b = _arena()
    b.players[0].bench.append(_mon("Src", tag, **kw))
    return b


class TestStaticDamagePlus(unittest.TestCase):
    def test_team_add_from_bench(self):
        b = _with_source("fx:static_dmg:add:20:team")
        self.assertEqual(F.static_dmg_plus(
            b, b.players[0].active, b.players[1].active), 20)

    def test_team_type_match_and_mismatch(self):
        b = _with_source("fx:static_dmg:add:30:team_type:fire,water")
        b.players[0].active.pdef = PokemonDef(
            "Fire", hp=200, ptype="fire",
            attacks=(AttackDef("Hit", cost=0, damage=100),))
        self.assertEqual(F.static_dmg_plus(
            b, b.players[0].active, b.players[1].active), 30)
        b.players[0].active.pdef = PokemonDef(
            "Grass", hp=200, ptype="grass",
            attacks=(AttackDef("Hit", cost=0, damage=100),))
        self.assertEqual(F.static_dmg_plus(
            b, b.players[0].active, b.players[1].active), 0)

    def test_team_evolution_type(self):
        b = _with_source("fx:static_dmg:add:10:team_evolution_type:fire")
        b.players[0].active.pdef = PokemonDef(
            "Evo", hp=200, ptype="fire", stage=2,
            attacks=(AttackDef("Hit", cost=0, damage=100),))
        self.assertEqual(F.static_dmg_plus(
            b, b.players[0].active, b.players[1].active), 10)
        b.players[0].active.pdef = PokemonDef(
            "Basic", hp=200, ptype="fire", stage=0,
            attacks=(AttackDef("Hit", cost=0, damage=100),))
        self.assertEqual(F.static_dmg_plus(
            b, b.players[0].active, b.players[1].active), 0)

    def test_team_name_prefix_not_partial_word(self):
        b = _with_source("fx:static_dmg:add:30:team_name:hops")
        b.players[0].active.pdef = PokemonDef(
            "Hop's Snorlax", hp=200, ptype="colorless",
            attacks=(AttackDef("Hit", cost=0, damage=100),))
        self.assertEqual(F.static_dmg_plus(
            b, b.players[0].active, b.players[1].active), 30)
        b.players[0].active.pdef = PokemonDef(
            "Hoppip", hp=200, ptype="colorless",
            attacks=(AttackDef("Hit", cost=0, damage=100),))
        self.assertEqual(F.static_dmg_plus(
            b, b.players[0].active, b.players[1].active), 0,
            "Hoppip 不能被 Hop's 前缀误算")

    def test_nonstack_counts_once(self):
        b = _arena()
        for _ in range(2):
            b.players[0].bench.append(_mon(
                "Src", "fx:static_dmg:add:30:team_name:hops:nonstack"))
        b.players[0].active.pdef = PokemonDef(
            "Hop's Snorlax", hp=200, ptype="colorless",
            attacks=(AttackDef("Hit", cost=0, damage=100),))
        self.assertEqual(F.static_dmg_plus(
            b, b.players[0].active, b.players[1].active), 30)

    def test_self_scope_requires_source_is_attacker(self):
        b = _with_source("fx:static_dmg:add:50:self")
        self.assertEqual(F.static_dmg_plus(
            b, b.players[0].active, b.players[1].active), 0)
        src = b.players[0].bench.pop(0)
        b.players[0].active = src
        self.assertEqual(F.static_dmg_plus(
            b, b.players[0].active, b.players[1].active), 50)

    def test_self_if_damage_counters(self):
        b = _arena()
        att = _mon("A", "fx:static_dmg:add:120:self_if_damage:2")
        b.players[0].active = att
        att.damage = 10
        self.assertEqual(F.static_dmg_plus(
            b, att, b.players[1].active), 0)
        att.damage = 20
        self.assertEqual(F.static_dmg_plus(
            b, att, b.players[1].active), 120)

    def test_self_vs_ability(self):
        b = _arena()
        att = _mon("A", "fx:static_dmg:add:50:self_vs_ability")
        b.players[0].active = att
        self.assertEqual(F.static_dmg_plus(
            b, att, b.players[1].active), 0, "目标无特性时不加伤")
        b.players[1].active.pdef = PokemonDef(
            "AbilityMon", hp=300, ptype="colorless",
            abilities=(AbilityDef("X", "recon:2"),))
        self.assertEqual(F.static_dmg_plus(
            b, att, b.players[1].active), 50)

    def test_per_prize_taken(self):
        b = _arena()
        att = _mon("A", "fx:static_dmg:add:30:self_per_prize")
        b.players[0].active = att
        b.players[1].prizes_left = 4          # 对手已取 2 张
        self.assertEqual(F.static_dmg_plus(
            b, att, b.players[1].active), 60)

    def test_opponent_active_suppression(self):
        b = _arena()
        b.players[0].active = _mon("P", "fx:static_dmg:sub:30:opp_active")
        self.assertEqual(F.static_dmg_plus(
            b, b.players[1].active, b.players[0].active), -30)
        # 效果源不在战斗位 → 不生效
        b.players[0].bench.append(b.players[0].active)
        b.players[0].active = _mon("Other")
        self.assertEqual(F.static_dmg_plus(
            b, b.players[1].active, b.players[0].active), 0)

    def test_unknown_tag_raises(self):
        b = _with_source("fx:static_dmg:add:20:whatever")
        with self.assertRaises(ValueError):
            F.static_dmg_plus(b, b.players[0].active, b.players[1].active)

    def test_zero_damage_attack_gets_no_bonus(self):
        """常驻加伤只作用于本身有基础伤害的招式"""
        b = _with_source("fx:static_dmg:add:20:team")
        b.players[0].active = make_pokemon(PokemonDef(
            "A", hp=200, ptype="colorless",
            attacks=(AttackDef("NoDamage", cost=0, damage=0),)))
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.damage, 0)

    def test_add_before_weakness(self):
        """before applying Weakness and Resistance → 先加伤再 ×2"""
        b = _with_source("fx:static_dmg:add:20:team")
        b.players[0].active = make_pokemon(PokemonDef(
            "A", hp=200, ptype="lightning",
            attacks=(AttackDef("Hit", cost=0, damage=100),)))
        b.players[1].active = make_pokemon(PokemonDef(
            "D", hp=300, ptype="water", weakness="lightning"))
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.damage, 240)   # (100+20)×2


class TestTypedPassiveReduce(unittest.TestCase):
    def _battle(self, atk_type):
        b = _arena()
        b.players[0].active = make_pokemon(PokemonDef(
            "A", hp=200, ptype=atk_type,
            attacks=(AttackDef("Hit", cost=0, damage=100),)))
        b.players[1].active = make_pokemon(PokemonDef(
            "D", hp=300, ptype="colorless",
            abilities=(AbilityDef(
                "Thick Fat",
                "passive:dmg_reduce:30:from:type:fire,water"),)))
        return b

    def test_matching_type_reduces(self):
        b = self._battle("fire")
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.damage, 70)

    def test_nonmatching_type_does_not_reduce(self):
        b = self._battle("lightning")
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.damage, 100)

    def test_unknown_attacker_is_conservative(self):
        from engine.battle import _static_mods
        fp = _mon("D", "passive:dmg_reduce:30:from:type:fire")
        self.assertEqual(_static_mods(fp)["dmg_reduce"], 0)


if __name__ == "__main__":
    unittest.main()
