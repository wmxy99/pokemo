# -*- coding: utf-8 -*-
"""fx 族：效果免疫 —— 引擎侧单测

覆盖三条边界：
  1. 普通伤害照常结算（卡面括号「Damage is not an effect」）；
  2. 状态 / 弃能 / 换位 / 指示物等**效果**被拦截；
  3. 特性效果免疫只拦对手特性做给自身的部分。
"""
from __future__ import annotations

import random
import unittest

from engine.battle import Battle
from engine.cards import AbilityDef, AttackDef, PokemonDef, make_pokemon
from engine.families import fx_effect_immune as F
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
        attacks=(AttackDef("Hit", cost=0, damage=60,
                           effects=("poison",)),)))
    b.players[1].active = make_pokemon(PokemonDef(
        "Defender", hp=300, ptype="colorless"))
    return b


def _immune(tag="fx:effect_immune:attack:self", ptype="colorless"):
    return make_pokemon(PokemonDef(
        "Wall", hp=300, ptype=ptype,
        abilities=(AbilityDef("Immunity", tag),)))


def _attack(b, effects, **action):
    b.players[0].active = make_pokemon(PokemonDef(
        "Attacker", hp=200, ptype="colorless",
        attacks=(AttackDef("Hit", cost=0, damage=60, effects=effects),)))
    act = {"verb": "attack", "attack": 0}
    act.update(action)
    return b.step(act)


class TestAttackEffectImmunity(unittest.TestCase):
    def test_damage_still_applies(self):
        b = _arena()
        b.players[1].active = _immune()
        r = _attack(b, ("poison",))
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.damage, 60)
        self.assertEqual(list(b.players[1].active.status), [])

    def test_status_blocked(self):
        b = _arena()
        b.players[1].active = _immune()
        r = _attack(b, ("poison",))
        self.assertTrue(r["ok"], r.get("error"))
        self.assertNotIn("poison", b.players[1].active.status)

    def test_discard_energy_blocked(self):
        b = _arena()
        b.players[1].active = _immune()
        b.players[1].active.add_energy("water")
        r = _attack(b, ("discard_energy:1:opp",))
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.total_energy(), 1)

    def test_gust_blocked(self):
        b = _arena()
        b.players[1].bench.append(_immune())
        r = _attack(b, ("gust",), target=0)
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.name, "Defender")
        self.assertEqual(len(b.players[1].bench), 1)

    def test_spread_counters_blocked(self):
        b = _arena()
        b.players[1].bench.append(_immune())
        r = _attack(b, ("spread:1:20",))
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].bench[0].damage, 0)

    def test_snipe_counters_blocked(self):
        b = _arena()
        b.players[1].bench.append(_immune())
        r = _attack(b, ("snipe:opp_bench:20:1",), snipe=[0])
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].bench[0].damage, 0)

    def test_self_retreat_lock_not_blocked_by_opponent(self):
        """`retreat_lock:self` 是攻击方自己的限制，不能因对手免疫而失效"""
        b = _arena()
        b.players[1].active = _immune()
        r = _attack(b, ("retreat_lock:self",))
        self.assertTrue(r["ok"], r.get("error"))
        self.assertGreater(b.players[0].active.retreat_lock_until, 0)


class TestAbilityEffectImmunity(unittest.TestCase):
    def _ability_battle(self, tag, dmg=0):
        b = _arena()
        b.players[0].active = make_pokemon(PokemonDef(
            "Caster", hp=200, ptype="colorless",
            abilities=(AbilityDef("Hex", tag),)))
        b.players[0].active.damage = dmg
        b.players[1].active = _immune("fx:effect_immune:ability:self")
        return b

    def test_ability_status_blocked(self):
        b = self._ability_battle("poison")
        r = b.step({"verb": "ability", "pos": 0, "name": "Hex", "tag": "poison"})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertNotIn("poison", b.players[1].active.status)

    def test_ability_move_damage_blocked(self):
        b = self._ability_battle("move_damage:1", dmg=10)
        r = b.step({"verb": "ability", "pos": 0, "name": "Hex",
                    "tag": "move_damage:1"})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[0].active.damage, 10)
        self.assertEqual(b.players[1].active.damage, 0)

    def test_attack_immunity_does_not_block_ability(self):
        b = _arena()
        b.players[0].active = make_pokemon(PokemonDef(
            "Caster", hp=200, ptype="colorless",
            abilities=(AbilityDef("Hex", "poison"),)))
        b.players[1].active = _immune("fx:effect_immune:attack:self")
        r = b.step({"verb": "ability", "pos": 0, "name": "Hex", "tag": "poison"})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertIn("poison", b.players[1].active.status)


class TestTagValidation(unittest.TestCase):
    def test_unknown_source_raises(self):
        b = _arena()
        fp = _immune("fx:effect_immune:hack:self")
        with self.assertRaises(ValueError):
            F.effect_immune(b, fp, "attack")

    def test_unknown_scope_raises(self):
        b = _arena()
        fp = _immune("fx:effect_immune:attack:team")
        with self.assertRaises(ValueError):
            F.effect_immune(b, fp, "attack")

    def test_no_tag_is_false(self):
        b = _arena()
        self.assertFalse(F.effect_immune(b, b.players[1].active, "attack"))


if __name__ == "__main__":
    unittest.main()
