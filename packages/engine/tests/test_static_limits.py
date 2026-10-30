# -*- coding: utf-8 -*-
"""静态规则 / 下回合限制 —— 引擎侧单测（2026-10-28）

覆盖：
  · `attack_denial:coin:turn`（Sand Attack 家族）：每次尝试抛币，反面攻击不发生；
  · `attack_cost_up` / `retreat_cost_up`（Rillaboom）：对手下个回合费用/撤退 +1；
  · `ignore_effects`（Koraidon「Shred」）：无视减伤/护盾等**效果**，但不无视弱点；
  · `fx:attack_cost:self:less:per:<counter>`：按计数源减费（可限定招式名）。
"""
from __future__ import annotations

import random
import unittest

from engine.battle import Battle, _retreat_cost
from engine.cards import (AbilityDef, AttackDef, PokemonDef, PokeRef,
                          make_pokemon)
from engine.state import GameConfig, PlayerState


def _arena(seed: int = 5):
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
    b.players[0].active = make_pokemon(PokemonDef(
        "Attacker", hp=300, ptype="colorless",
        attacks=(AttackDef("Hit", cost=0, damage=20),)))
    b.players[1].active = make_pokemon(PokemonDef(
        "Defender", hp=300, ptype="colorless",
        attacks=(AttackDef("Back", cost=1, damage=10),)))
    # 给双方若干牌，避免 `_begin_turn` 抽牌即 deckout 结束对局
    for i in range(2):
        b.players[i].deck = [
            PokeRef(PokemonDef(f"D{i}{k}", hp=10, ptype="colorless"))
            for k in range(6)]
    return b


class TestAttackDenial(unittest.TestCase):
    def _apply(self, b):
        b.players[0].active = make_pokemon(PokemonDef(
            "Sandy", hp=200, ptype="psychic",
            attacks=(AttackDef("Sand Attack", cost=0, damage=10,
                               effects=("attack_denial:coin:turn",)),)))
        return b.step({"verb": "attack", "attack": 0})

    def test_tails_denies_attack(self):
        saw_deny = False
        for seed in range(30):
            b = _arena(seed=seed)
            self._apply(b)
            # 轮到对手，其 active 带 denial；用同一个 rng 直接推进到攻击
            if b.finished:
                continue
            p1 = b.players[1]
            p1.active.attack_denial_until = 99      # 保证生效
            # 能量足够，确保流程走到抛币而不是"能量不足"
            p1.active.add_energy("colorless")
            # 抛币结果决定是否真的攻击
            r = b.step({"verb": "attack", "attack": 0})
            denied = any(e.get("type") == "attack_denied" for e in b.events)
            if denied:
                saw_deny = True
                self.assertTrue(r["ok"])
                self.assertFalse(any(e.get("type") == "damage" and
                                     e.get("player") == 0 for e in b.events))
        self.assertTrue(saw_deny, "30 个种子内未出现反面（抛币未生效）")

    def test_missing_denial_attacks_normally(self):
        b = _arena()
        b.current = 1
        b.players[1].active.add_energy("colorless")
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertFalse(any(e.get("type") == "attack_denied" for e in b.events))


class TestNextTurnCostUp(unittest.TestCase):
    def test_cost_and_retreat_up(self):
        b = _arena()
        b.players[0].active = make_pokemon(PokemonDef(
            "Drummer", hp=200, ptype="grass",
            attacks=(AttackDef("Drum Beating", cost=0, damage=10,
                               effects=("attack_cost_up:opp:turn:1",
                                        "retreat_cost_up:opp:turn:1")),)))
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        opp = b.players[1]
        # 对手回合：其 active 的攻击费用 +1（原本 {C}1 → {C}2，0 能量付不起）
        self.assertEqual(b.current, 1)
        self.assertFalse(b._can_pay_attack(opp.active, opp.active.pdef.attacks[0]))
        opp.active.add_energy("colorless")
        self.assertFalse(b._can_pay_attack(opp.active, opp.active.pdef.attacks[0]))
        opp.active.add_energy("colorless")
        self.assertTrue(b._can_pay_attack(opp.active, opp.active.pdef.attacks[0]))
        # 撤退费用 +1（pdef.retreat=1 → 2）
        self.assertEqual(_retreat_cost(opp.active, b), 2)

    def test_expires_after_next_turn(self):
        b = _arena()
        b.players[0].active = make_pokemon(PokemonDef(
            "Drummer", hp=200, ptype="grass",
            attacks=(AttackDef("Drum Beating", cost=0, damage=10,
                               effects=("attack_cost_up:opp:turn:1",)),)))
        b.step({"verb": "attack", "attack": 0})
        fp = b.players[1].active
        self.assertTrue(fp.attack_cost_up_until > b.turn_counts[1])
        b.step({"verb": "pass"})                 # 对手回合结束
        b.step({"verb": "pass"})                 # 我方回合结束
        b.step({"verb": "pass"})                 # 对手再一个回合结束
        self.assertFalse(fp.attack_cost_up_until > b.turn_counts[1])


class TestIgnoreEffects(unittest.TestCase):
    def _battle(self, effects):
        b = _arena()
        b.players[0].active = make_pokemon(PokemonDef(
            "Koraidon", hp=200, ptype="fighting",
            attacks=(AttackDef("Shred", cost=0, damage=130,
                               effects=effects),)))
        return b

    def test_bypasses_static_reduce(self):
        b = self._battle(("ignore_effects",))
        b.players[1].active = make_pokemon(PokemonDef(
            "Wall", hp=300, ptype="colorless",
            abilities=(AbilityDef("Walls", "passive:dmg_reduce:30"),)))
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 130)

    def test_without_tag_reduction_applies(self):
        b = self._battle(())
        b.players[1].active = make_pokemon(PokemonDef(
            "Wall", hp=300, ptype="colorless",
            abilities=(AbilityDef("Walls", "passive:dmg_reduce:30"),)))
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 100)

    def test_weakness_still_applies(self):
        b = self._battle(("ignore_effects",))
        b.players[1].active = make_pokemon(PokemonDef(
            "GrassWall", hp=300, ptype="grass", weakness="fighting"))
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 260)   # 130 × 2


class TestSelfCostLess(unittest.TestCase):
    def test_named_attack_by_prizes_taken(self):
        b = _arena()
        b.players[0].active = make_pokemon(PokemonDef(
            "Ursaluna", hp=300, ptype="fighting",
            abilities=(AbilityDef(
                "Seasoned Skill",
                "fx:attack_cost:self:less:per:opp_prizes_taken:name:bloodmoon"),),
            attacks=(AttackDef("Blood Moon", cost=5, damage=240),
                     AttackDef("Other", cost=5, damage=10))))
        b.players[1].prizes_left = b.cfg.prizes - 4      # 对手已取 4 张
        self.assertFalse(b._can_pay_attack(
            b.players[0].active, b.players[0].active.pdef.attacks[0]))
        b.players[0].active.add_energy("colorless")
        self.assertTrue(b._can_pay_attack(
            b.players[0].active, b.players[0].active.pdef.attacks[0]))
        # 限定了招式名：其它招式（同费用）不受减费影响
        self.assertFalse(b._can_pay_attack(
            b.players[0].active, b.players[0].active.pdef.attacks[1]))

    def test_all_attacks_by_opp_bench(self):
        b = _arena()
        b.players[0].active = make_pokemon(PokemonDef(
            "Incineroar", hp=300, ptype="fire",
            abilities=(AbilityDef(
                "Hustle Play",
                "fx:attack_cost:self:less:per:opp_bench"),),
            attacks=(AttackDef("Blaze Blast", cost=4, damage=240),)))
        b.players[1].bench = [make_pokemon(PokemonDef(f"B{i}", hp=50,
                                                      ptype="colorless"))
                              for i in range(3)]
        self.assertFalse(b._can_pay_attack(
            b.players[0].active, b.players[0].active.pdef.attacks[0]))
        b.players[0].active.add_energy("colorless")
        self.assertTrue(b._can_pay_attack(
            b.players[0].active, b.players[0].active.pdef.attacks[0]))

    def test_no_opp_bench_no_discount(self):
        b = _arena()
        b.players[0].active = make_pokemon(PokemonDef(
            "Incineroar", hp=300, ptype="fire",
            abilities=(AbilityDef("Hustle Play",
                                  "fx:attack_cost:self:less:per:opp_bench"),),
            attacks=(AttackDef("Blaze Blast", cost=1, damage=240),)))
        self.assertFalse(b._can_pay_attack(
            b.players[0].active, b.players[0].active.pdef.attacks[0]))


if __name__ == "__main__":
    unittest.main()
