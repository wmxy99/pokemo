# -*- coding: utf-8 -*-
"""借用招式（borrow_attack）—— 引擎侧单测

覆盖四种来源与费用口径：
  · `opp_active`（Clefable / Zoroark）：复制对手战斗位的招式；
  · `opp_active_tera`（Team Rocket's Mimikyu）：仅当对手战斗位是 Tera 才有候选；
  · `bench_ally_n`（N's Zoroark ex）：只能借后备的 N's 宝可梦；
  · `passive:prev_attacks`（Relicanth）：进化前形态的招式，**付它自己的费用**。
复制招式的费用按**本招式**结算（卡面 "use it as this attack"）。
"""
from __future__ import annotations

import random
import unittest

from engine.battle import Battle
from engine.cards import (AbilityDef, AttackDef, PokemonDef, make_pokemon)
from engine.state import GameConfig, PlayerState


def _arena(seed: int = 11):
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
    b.players[1].active = make_pokemon(PokemonDef(
        "Victim", hp=300, ptype="colorless",
        attacks=(AttackDef("Borrowed", cost=1, damage=70,
                           effects=("poison",)),)))
    return b


def _copy_attacker(scope="opp_active", cost=2):
    return make_pokemon(PokemonDef(
        "Copier", hp=200, ptype="colorless",
        attacks=(AttackDef("Copy", cost=cost, effects=(f"borrow_attack:{scope}",)),)))


class TestOppActiveBorrow(unittest.TestCase):
    def test_copy_damage_and_effect(self):
        b = _arena()
        b.players[0].active = _copy_attacker()
        b.players[0].active.add_energy("colorless", 2)
        acts = [a for a in b.legal_actions() if a["verb"] == "attack"]
        self.assertEqual(len(acts), 1)
        self.assertEqual(acts[0]["borrow"], {"src": "opp_active", "attack": 0})
        r = b.step({"verb": "attack", "attack": 0, "borrow":
                    {"src": "opp_active", "attack": 0}})
        self.assertTrue(r["ok"], r.get("error"))
        dmg = [e for e in b.events if e.get("type") == "damage"]
        self.assertEqual(dmg[-1]["dmg"], 70)
        self.assertIn("poison", [e.get("status") for e in b.events
                                 if e.get("type") == "status"])
        # 攻击结束回合 → 对手回合开始触发中毒掉血，故当前伤害总量为 70 + 10
        self.assertEqual(b.players[1].active.damage, 80)

    def test_cost_is_copying_attack(self):
        """借用的招式费用再高，也按本招式（Copy）的费用结算"""
        b = _arena()
        b.players[0].active = _copy_attacker(cost=2)
        b.players[0].active.add_energy("colorless", 2)
        self.assertTrue(any(a["verb"] == "attack" for a in b.legal_actions()))
        b.players[0].active.remove_any(1)
        self.assertFalse(any(a["verb"] == "attack" for a in b.legal_actions()))

    def test_no_options_when_opponent_has_no_attacks(self):
        b = _arena()
        b.players[1].active = make_pokemon(PokemonDef(
            "Statue", hp=100, ptype="colorless"))
        b.players[0].active = _copy_attacker()
        b.players[0].active.add_energy("colorless", 2)
        self.assertFalse(any(a["verb"] == "attack" for a in b.legal_actions()))

    def test_invalid_borrow_rejected(self):
        b = _arena()
        b.players[0].active = _copy_attacker()
        b.players[0].active.add_energy("colorless", 2)
        r = b.step({"verb": "attack", "attack": 0,
                    "borrow": {"src": "opp_active", "attack": 9}})
        self.assertFalse(r["ok"])
        r2 = b.step({"verb": "attack", "attack": 0,
                     "borrow": {"src": "ally", "attack": 0}})
        self.assertFalse(r2["ok"])


class TestTeraGating(unittest.TestCase):
    def test_non_tera_blocks(self):
        b = _arena()
        b.players[0].active = _copy_attacker("opp_active_tera")
        b.players[0].active.add_energy("colorless", 2)
        self.assertFalse(any(a["verb"] == "attack" for a in b.legal_actions()))

    def test_tera_allows(self):
        b = _arena()
        b.players[1].active = make_pokemon(PokemonDef(
            "Tera mon", hp=200, ptype="fire", category="Tera(Fire)",
            attacks=(AttackDef("TeraHit", cost=1, damage=50),)))
        b.players[0].active = _copy_attacker("opp_active_tera")
        b.players[0].active.add_energy("colorless", 2)
        acts = [a for a in b.legal_actions() if a["verb"] == "attack"]
        self.assertEqual(len(acts), 1)
        r = b.step(acts[0])
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.damage, 50)


class TestBenchAllyBorrow(unittest.TestCase):
    def test_only_n_ally(self):
        b = _arena()
        b.players[0].active = _copy_attacker("bench_ally_n")
        b.players[0].active.add_energy("colorless", 2)
        b.players[0].bench.append(make_pokemon(PokemonDef(
            "Other ally", hp=100, ptype="colorless",
            attacks=(AttackDef("AllyHit", cost=1, damage=99),))))
        self.assertFalse(any(a["verb"] == "attack" for a in b.legal_actions()))
        b.players[0].bench.append(make_pokemon(PokemonDef(
            "N ally", hp=100, ptype="darkness",
            category="Trainer's Pokémon(N)",
            attacks=(AttackDef("N Hit", cost=1, damage=40),))))
        acts = [a for a in b.legal_actions() if a["verb"] == "attack"]
        self.assertEqual(len(acts), 1)
        self.assertEqual(acts[0]["borrow"]["src"], "ally")
        self.assertEqual(acts[0]["borrow"]["pos"], 1)
        r = b.step(acts[0])
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.damage, 40)


class TestPrevAttacks(unittest.TestCase):
    BASE = PokemonDef("PrevBase", hp=60, ptype="colorless", stage=0,
                      attacks=(AttackDef("Old Hit", cost=1, damage=30),))
    MID = PokemonDef("PrevMid", hp=90, ptype="colorless", stage=1,
                     evolves_from="PrevBase",
                     attacks=(AttackDef("Mid Hit", cost=2, damage=50),))

    def _battle(self, with_relicanth=True, stage=1):
        b = _arena()
        evo = make_pokemon(PokemonDef(
            "Final", hp=140, ptype="colorless", stage=2,
            evolves_from="PrevMid",
            attacks=(AttackDef("New Hit", cost=3, damage=80),)))
        b.players[0].active = evo
        if with_relicanth:
            b.players[0].bench.append(make_pokemon(PokemonDef(
                "Relicanth", hp=90, ptype="fighting", stage=0,
                abilities=(AbilityDef("Memory Dive", "passive:prev_attacks"),))))
        evo.add_energy("colorless", 1)
        return b

    def test_prev_attacks_listed_and_paid_by_own_cost(self):
        b = self._battle()
        acts = [a for a in b.legal_actions() if a["verb"] == "attack"]
        borrows = [a for a in acts if a.get("borrow")]
        # 1 点能量只够付 Old Hit（cost 1），Mid Hit 需要 2
        self.assertEqual([a["name"] for a in borrows], ["Old Hit"])
        self.assertEqual(borrows[0]["name"], "Old Hit")
        r = b.step(borrows[0])
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.damage, 30)

    def test_without_relicanth_no_prev(self):
        b = self._battle(with_relicanth=False)
        acts = [a for a in b.legal_actions() if a["verb"] == "attack"]
        self.assertFalse(any(a.get("borrow") for a in acts))
        # 普通招式仍在（能量不足 → 也不列）
        self.assertEqual(acts, [])

    def test_chain_second_stage_included(self):
        b = self._battle()
        b.players[0].active.add_energy("colorless", 1)   # 共 2 点
        names = {a["name"] for a in b.legal_actions() if a.get("borrow")}
        self.assertEqual(names, {"Old Hit", "Mid Hit"})


if __name__ == "__main__":
    unittest.main()
