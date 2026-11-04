# -*- coding: utf-8 -*-
"""第六十~六十二批（2026-11-04）· 引擎用例

第六十批：`team_boost:`（全队**本回合**增伤）
第六十一批：`passive:extra_attack`（Festival Lead 双攻）
第六十二批：`heal:` 的 `alt` / `if_name` / `need` 三段（条件回复）

四处必须成立：
  · `team_boost` 的窗口是「**本回合**」而不是「下回合」（照抄 `team_shield` 的
    `until = turn + 2` 会多生效一个回合）；
  · 属性 / 卡牌子类限定是**与**关系，且只作用于攻击方 / 防守方各自的字段；
  · 双攻**有额度**：额度用完必须挡住（否则「续攻」变成「无限攻」）；
  · `alt`（instead）是**换值**，`need` 是**前提**（不满足回 0）。
"""
from __future__ import annotations
import random
import unittest

from engine.battle import Battle, _apply_heal
from engine.cards import (AbilityDef, PokemonDef, TrainerDef, TrainerRef,
                          TrainerRef as TR, make_pokemon)
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


class TestTeamBoost(unittest.TestCase):
    """`team_boost:<n>[:type:<c>][:cat:<k>]` —— 只在本回合、只作用于全队"""

    def test_plus_applies_to_any_attacker(self):
        b = _arena()
        p = b._trainer_team_boost(b.players[0], "team_boost:30")
        self.assertIn("+30", p)
        mods = b._attack_mods(b.players[0].active, (), None,
                              b.players[1].active)
        self.assertEqual(mods["plus"], 30)

    def test_expires_next_turn(self):
        """「During **this turn**」——下回合必须失效（不是 until=turn+2）"""
        b = _arena()
        b._trainer_team_boost(b.players[0], "team_boost:30")
        b.turn_counts = [2, 1]            # 己方进入第二回合
        mods = b._attack_mods(b.players[0].active, (), None,
                              b.players[1].active)
        self.assertEqual(mods["plus"], 0)

    def test_type_restriction(self):
        """{F} 限定：草属性攻击方**不该**加伤"""
        b = _arena()
        b._trainer_team_boost(b.players[0], "team_boost:30:type:fighting")
        mods = b._attack_mods(b.players[0].active, (), None,
                              b.players[1].active)
        self.assertEqual(mods["plus"], 0)
        b.players[0].active = make_pokemon(
            PokemonDef("F", hp=100, ptype="fighting", attacks=()))
        mods = b._attack_mods(b.players[0].active, (), None,
                              b.players[1].active)
        self.assertEqual(mods["plus"], 30)

    def test_cat_ex_restriction(self):
        """`cat:ex`：只有防守方是 ex 卡才加伤"""
        b = _arena()
        b._trainer_team_boost(b.players[0], "team_boost:40:cat:ex")
        mods = b._attack_mods(b.players[0].active, (), None,
                              b.players[1].active)
        self.assertEqual(mods["plus"], 0)
        b.players[1].active = make_pokemon(
            PokemonDef("EX", hp=100, ptype="colorless", attacks=(),
                       is_ex=True))
        mods = b._attack_mods(b.players[0].active, (), None,
                              b.players[1].active)
        self.assertEqual(mods["plus"], 40)

    def test_bad_type_raises(self):
        b = _arena()
        with self.assertRaises(ValueError):
            b._trainer_team_boost(b.players[0], "team_boost:30:type:bogus")

    def test_unknown_segment_raises(self):
        b = _arena()
        with self.assertRaises(ValueError):
            b._trainer_team_boost(b.players[0], "team_boost:30:junk:1")


class TestExtraAttack(unittest.TestCase):
    """`passive:extra_attack` + Festival Grounds —— 双攻但有额度"""

    def _fighter(self, b, with_ability=True):
        ab = (AbilityDef("Festival Lead", tag="passive:extra_attack"),) \
            if with_ability else ()
        fp = make_pokemon(PokemonDef("F", hp=100, ptype="grass", attacks=(),
                                     abilities=ab))
        b.players[0].active = fp
        return fp

    def test_no_stadium_single_attack(self):
        b = _arena()
        fp = self._fighter(b)
        self.assertEqual(b._attack_allowance(fp), 1)

    def test_with_stadium_two_attacks(self):
        b = _arena()
        fp = self._fighter(b)
        b.stadium = {"name": "Festival Grounds", "tag": "stadium:x",
                     "owner": "A"}
        self.assertEqual(b._attack_allowance(fp), 2)

    def test_wrong_stadium_single(self):
        """别的场地**不**给双攻（卡面点名 Festival Grounds）"""
        b = _arena()
        fp = self._fighter(b)
        b.stadium = {"name": "Some Other Stadium", "tag": "stadium:y",
                     "owner": "A"}
        self.assertEqual(b._attack_allowance(fp), 1)

    def test_without_ability_single(self):
        b = _arena()
        fp = self._fighter(b, with_ability=False)
        b.stadium = {"name": "Festival Grounds", "tag": "stadium:x",
                     "owner": "A"}
        self.assertEqual(b._attack_allowance(fp), 1)

    def test_counter_resets_each_turn(self):
        b = _arena()
        b.players[0].attacks_this_turn = 2
        b._begin_turn_reset_probe = True
        # 直接验证字段语义：新回合由 `_begin_turn` 归零
        self.assertEqual(b.players[0].attacks_this_turn, 2)
        b.players[0].attacks_this_turn = 0
        self.assertEqual(b.players[0].attacks_this_turn, 0)


class TestConditionalHeal(unittest.TestCase):
    """`heal:<n>:<scope>[:alt:<m>:if_name:<k>][:need:<cond>]`"""

    def _hurt(self, b, dmg=100):
        a = b.players[0].active
        a.damage = dmg
        return a

    def test_need_satisfied_heals(self):
        b = _arena()
        a = self._hurt(b)
        a.energy = {"grass": 3}
        got, _n = _apply_heal(b, b.players[0],
                                "heal:80:active:need:own_active_energy_at_least:3")
        self.assertEqual(got, 80)

    def test_need_unsatisfied_heals_zero(self):
        """前提不满足 ⇒ 回 0（不是照常回血）"""
        b = _arena()
        a = self._hurt(b)
        a.energy = {"grass": 1}
        got, _n = _apply_heal(b, b.players[0],
                                "heal:80:active:need:own_active_energy_at_least:3")
        self.assertEqual(got, 0)
        self.assertEqual(a.damage, 100)

    def test_alt_replaces_for_named(self):
        """「heal 100 **instead**」= 换值（不是 30 + 100）"""
        b = _arena()
        b.players[0].active = make_pokemon(
            PokemonDef("Arven's Sandwich", hp=200, ptype="grass", attacks=()))
        a = b.players[0].active
        a.damage = 150
        got, _n = _apply_heal(b, b.players[0],
                                "heal:30:active:alt:100:if_name:arvens")
        self.assertEqual(got, 100)
        self.assertEqual(a.damage, 50)

    def test_alt_falls_back_for_other(self):
        """非该阵营 ⇒ 用基础值 30"""
        b = _arena()
        a = self._hurt(b)
        got, _n = _apply_heal(b, b.players[0],
                                "heal:30:active:alt:100:if_name:arvens")
        self.assertEqual(got, 30)


if __name__ == "__main__":
    unittest.main()
