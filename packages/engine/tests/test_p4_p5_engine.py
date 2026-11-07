# -*- coding: utf-8 -*-
"""P4/P5（第一百一十二 ~ 一百二十九批）· 引擎用例

覆盖本阶段新补的机制（每条都配「成立 / 不成立」两向对照）：

  · ``fx:attack_cost:self_set``     费用**整体改写**（不足费用也能出招）
  · ``fx:attack_cost:self_omit``    忽略某一色（其余照付）
  · ``passive:attack_block_unless`` 条件不成立时**整只不能出招**
  · ``passive:evolve_same_turn``    当回合/首回合进化（可限定战斗位）
  · ``dmg_set:N:if:<cond>``         条件基础伤害（不成立时保持印刷值）
  · ``self_attack_set_next``        下回合「基础伤害改为 N」（不是加伤）
  · ``heal:equal``                  回复量 = 本次实际伤害

运行：cd packages/engine && python -m unittest tests.test_p4_p5_engine -v
"""
from __future__ import annotations

import random
import unittest

from engine.battle import Battle, _eval_condition
from engine.cards import (AbilityDef, AttackDef, EnergyDef, EnergyRef,
                          PokemonDef, PokeRef, make_pokemon)
from engine.state import GameConfig, PlayerState


class _Fixed(random.Random):
    def __init__(self, value: float = 0.5):
        super().__init__(0)
        self.value = value

    def random(self) -> float:
        return self.value


def _mon(name="Mon", *, hp=300, ptype="colorless", stage=0, is_ex=False,
         category="", abilities=(), attacks=None):
    return make_pokemon(PokemonDef(
        name, hp=hp, ptype=ptype, stage=stage, is_ex=is_ex,
        category=category, abilities=tuple(abilities),
        attacks=tuple(attacks or (AttackDef("Pass", cost=(), damage=0),))))


def _arena(*, atk_abilities=(), atk_attacks=None, atk_energy=None,
           atk_name="Attacker", atk_category="", opp_active=None,
           stadium=None):
    cfg = GameConfig()
    b = object.__new__(Battle)
    b.cfg = cfg
    b.seed = 7
    b.rng = _Fixed()
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
    b.stadium = stadium
    b._ko_by_attack = [0, 0]
    b._ko_tr_by_attack = [0, 0]
    b.players[0].active = _mon(
        atk_name, hp=500, category=atk_category,
        abilities=tuple(atk_abilities),
        attacks=atk_attacks or (AttackDef("Test", cost=(), damage=10),))
    if atk_energy:
        for t, n in atk_energy.items():
            b.players[0].active.energy[t] = n
    b.players[1].active = opp_active or _mon("Defender", hp=400,
                                             ptype="fighting")
    for p in b.players:
        p.deck = [EnergyRef(EnergyDef("colorless")) for _ in range(20)]
    return b


# ---------------------------------------------------------------- 费用改写
class TestAttackCostSet(unittest.TestCase):
    """「… can use the X attack for {C}」= 费用整体改写"""

    def _b(self, *, cond=True, energy=None):
        # 条件用 `own_behind_prizes`（我方剩余奖赏更多）——比 `tera_in_play`
        # 好控制：后者会被「攻击方自己就是 Tera」意外满足。
        ab = AbilityDef("Plasma Bane",
                        tag="fx:attack_cost:self_set:1:colorless"
                            ":if:own_behind_prizes", location="field")
        atk = AttackDef("Test", cost=(("fire", 2), ("colorless", 1)),
                        damage=10)
        b = _arena(atk_abilities=(ab,), atk_attacks=(atk,),
                   atk_energy=energy or {"colorless": 1})
        b.players[0].prizes_left = 6
        b.players[1].prizes_left = 4 if cond else 6
        return b

    def test_cheaper_cost_when_condition_holds(self):
        b = self._b(cond=True)
        atk = b.players[0].active.pdef.attacks[0]
        self.assertTrue(b._can_pay_attack(b.players[0].active, atk),
                        "费用已被改写为 1 个无色，应可支付")

    def test_original_cost_when_condition_fails(self):
        b = self._b(cond=False)         # 双方奖赏相同 → 条件不成立 → 不改写
        atk = b.players[0].active.pdef.attacks[0]
        self.assertFalse(b._can_pay_attack(b.players[0].active, atk),
                         "条件不成立 → 仍按原费用（火火+无色），付不出")


class TestAttackCostOmit(unittest.TestCase):
    """「ignore all {C} Energy in the costs」= 去掉该色，其余照付"""

    def test_omit_colorless(self):
        ab = AbilityDef("Sniper's Eye",
                        tag="fx:attack_cost:self_omit:colorless:if:own_behind_prizes",
                        location="field")
        atk = AttackDef("Test", cost=(("grass", 1), ("colorless", 2)),
                        damage=10)
        b = _arena(atk_abilities=(ab,), atk_attacks=(atk,),
                   atk_energy={"grass": 1})
        b.players[0].prizes_left = 6
        b.players[1].prizes_left = 4          # 我方剩余更多 → 条件成立
        self.assertTrue(b._can_pay_attack(b.players[0].active, atk),
                        "无色已被忽略，1 个草能量足够")

    def test_condition_false_keeps_cost(self):
        ab = AbilityDef("Sniper's Eye",
                        tag="fx:attack_cost:self_omit:colorless:if:own_behind_prizes",
                        location="field")
        atk = AttackDef("Test", cost=(("grass", 1), ("colorless", 2)),
                        damage=10)
        b = _arena(atk_abilities=(ab,), atk_attacks=(atk,),
                   atk_energy={"grass": 1})
        self.assertFalse(b._can_pay_attack(b.players[0].active, atk),
                         "条件不成立 → 仍要 2 个无色，付不出")


# ---------------------------------------------------------------- 禁攻
class TestAttackBlockUnless(unittest.TestCase):
    def _b(self, cond):
        ab = AbilityDef("Born to Slack",
                        tag=f"passive:attack_block_unless:{cond}",
                        location="field")
        return _arena(atk_abilities=(ab,))

    def _attacks(self, b):
        return [a for a in b.legal_actions(0) if a.get("verb") == "attack"]

    def test_blocked_when_condition_false(self):
        # `attack_block_unless:<许可条件>`：条件是「对手场上有 ex」
        b = self._b("opp_has_ex_or_v")
        self.assertEqual(self._attacks(b), [], "对手没有 ex → 不得出招")

    def test_allowed_when_condition_true(self):
        b = self._b("opp_has_ex_or_v")
        b.players[1].active = _mon("Defender ex", hp=400, is_ex=True)
        self.assertTrue(self._attacks(b), "对手有 ex → 可以出招")


# ---------------------------------------------------------------- 当回合进化
class TestEvolveSameTurn(unittest.TestCase):
    def _b(self, tag, *, on_active=True):
        ab = AbilityDef("Stimulated Evolution", tag=tag, location="field")
        b = _arena()
        p = b.players[0]
        p.active = _mon("Basic", hp=100, abilities=(ab,))
        p.active.just_played = True
        if not on_active:
            p.bench = [p.active]
            p.active = _mon("Other", hp=100)
        return b, p

    def _evo(self):
        return PokeRef(PokemonDef("Evo", 120, "colorless", stage=1,
                                  evolves_from="Basic"))

    def test_evolves_on_first_turn(self):
        b, p = self._b("passive:evolve_same_turn:if:own_field_name:basic")
        p.hand = [self._evo()]
        r = b.step({"verb": "evolve", "hand": 0, "target": 0})
        self.assertTrue(r["ok"], r.get("error"))

    def test_active_only_rejects_bench(self):
        b, p = self._b("passive:evolve_same_turn:active", on_active=False)
        p.hand = [self._evo()]
        r = b.step({"verb": "evolve", "hand": 0, "target": 1})
        self.assertFalse(r["ok"], "该被动要求自身在战斗位，后备不得当回合进化")


# ---------------------------------------------------------------- 数值改写
class TestDmgSetConditional(unittest.TestCase):
    def test_set_when_condition_holds(self):
        b = _arena(atk_attacks=(AttackDef(
            "Test", cost=(), damage=260,
            effects=("dmg_set:30:if:opp_active_has_damage",)),))
        b.players[1].active.damage = 20
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.damage, 20 + 30,
                         "条件成立 → 基础伤害改为 30")

    def test_printed_damage_when_condition_fails(self):
        b = _arena(atk_attacks=(AttackDef(
            "Test", cost=(), damage=260,
            effects=("dmg_set:30:if:opp_active_has_damage",)),))
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.damage, 260,
                         "条件不成立 → 保持印刷伤害，不是 0")


class TestSelfAttackSetNext(unittest.TestCase):
    def test_next_turn_base_damage_replaced(self):
        b = _arena(atk_attacks=(
            AttackDef("Focus Energy", cost=(), damage=0,
                      effects=("self_attack_set_next:240:name:hyperfang",)),
            AttackDef("Hyper Fang", cost=(), damage=80),
        ))
        self.assertTrue(b.step({"verb": "attack", "attack": 0})["ok"])
        b.turn_number = 3                 # 下一个自己的回合
        b.turn_counts = [2, 1]
        b.current = 0
        # 手工推进回合（没走 `_begin_turn`）→ 必须自己清「本回合出招次数」，
        # 否则会被攻击次数上限挡住，测出来的是「次数用尽」而不是本机制。
        b.players[0].attacks_this_turn = 0
        before = b.players[1].active.damage
        self.assertTrue(b.step({"verb": "attack", "attack": 1})["ok"])
        self.assertEqual(b.players[1].active.damage - before, 240,
                         "应**替换**为 240，不是 80+240")


class TestHealEqual(unittest.TestCase):
    def test_heals_damage_dealt(self):
        b = _arena(atk_attacks=(AttackDef(
            "Test", cost=(), damage=30, effects=("heal:equal",)),))
        b.players[0].active.damage = 100
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[0].active.damage, 70,
                         "造成 30 → 回复自己 30")


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
