# -*- coding: utf-8 -*-
"""静态免伤族（第八批 · 2026-10-28）· 引擎用例

覆盖卡面原型（每条都必须**按来源/门槛**精确结算，绝不退化成全额免疫）：

  · ``passive:dmg_immune:self:from:basic_ex``       Farigiraf ex「Armor Tail」
  · ``passive:dmg_immune:self:from:ex``              Sylveon「Safeguard」/ Crustle
  · ``passive:dmg_immune:self:from:tera:effects``    Milotic ex「Sparkling Scales」
  · ``passive:dmg_immune:self:from:has_ability``     Cornerstone Mask Ogerpon ex
  · ``passive:dmg_immune:self:from:special_energy:effects``  Carracosta
  · ``passive:dmg_immune:self:min:200``              Drednaw「Impervious Shell」
  · ``passive:dmg_immune:team_bench:effects``        Rabsca「Spherical Shield」
  · ``passive:dmg_immune:team_bench:no_rule_box``    Shaymin「Flower Curtain」
  · ``shield:prevent_all:from:basic``                Archaludon / Dipplin
  · ``shield:prevent_all:from:basic_non_colorless``  Terapagos ex「Crown Opal」
  · ``shield:prevent_all:if_damage:le:N``            Metapod / Roggenrola
  · ``passive:bench_immune_dmg``                     Tera 规则（**只挡伤害**）
"""
from __future__ import annotations
import random
import unittest

from engine.battle import Battle
from engine.cards import (AbilityDef, AttackDef, EnergyDef, EnergyRef,
                          PokemonDef, make_pokemon)
from engine.state import GameConfig, PlayerState

E_FIRE = EnergyDef("fire", name_zh="基本火能量")


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
    b.players[0].active = make_pokemon(
        PokemonDef("Attacker", hp=200, ptype="colorless", attacks=()))
    b.players[1].active = make_pokemon(
        PokemonDef("Defender", hp=300, ptype="colorless", attacks=()))
    for p in b.players:
        p.deck = [EnergyRef() for _ in range(20)]
    return b


def _mon(name, hp=300, ptype="colorless", stage=0, is_ex=False, category="",
         abilities=(), attacks=()):
    return make_pokemon(PokemonDef(
        name, hp=hp, ptype=ptype, stage=stage, is_ex=is_ex,
        category=category, abilities=tuple(abilities), attacks=tuple(attacks)))


def _imm(tag: str) -> AbilityDef:
    return AbilityDef("Imm", tag=tag, location="field")


def _hit(b: Battle, effects=(), damage=100):
    """给当前玩家（A）装招式并打出，返回 (ok, 守方)"""
    atk = AttackDef("Test Atk", cost=0, damage=damage, effects=tuple(effects))
    b.players[0].active = _mon(
        "Hitter", hp=300, attacks=(atk,))
    r = b.step({"verb": "attack", "attack": 0})
    return r["ok"], b.players[1].active


class TestSelfImmunity(unittest.TestCase):
    def test_basic_ex_source_only(self):
        """Farigiraf ex：只挡**基础** ex 的攻击"""
        b = _arena()
        b.players[1].active = _mon(
            "Guard", abilities=(_imm("passive:dmg_immune:self:from:basic_ex"),))
        b.players[0].active = _mon("BasicEx", hp=300, is_ex=True, stage=0,
                                   attacks=(AttackDef("A", cost=0, damage=100),))
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 0, "基础 ex 应被全额挡下")

    def test_basic_ex_source_not_matched_by_evolved_ex(self):
        b = _arena()
        b.players[1].active = _mon(
            "Guard", abilities=(_imm("passive:dmg_immune:self:from:basic_ex"),))
        b.players[0].active = _mon("Stage1Ex", hp=300, is_ex=True, stage=1,
                                   attacks=(AttackDef("A", cost=0, damage=100),))
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 100, "一阶 ex 不在限定内")

    def test_plain_attacker_not_blocked(self):
        b = _arena()
        b.players[1].active = _mon(
            "Guard", abilities=(_imm("passive:dmg_immune:self:from:basic_ex"),))
        _hit(b)
        self.assertEqual(b.players[1].active.damage, 100, "普通攻击方不应被挡")

    def test_ex_source(self):
        b = _arena()
        b.players[1].active = _mon(
            "Guard", abilities=(_imm("passive:dmg_immune:self:from:ex"),))
        b.players[0].active = _mon("Ex", hp=300, is_ex=True, stage=1,
                                   attacks=(AttackDef("A", cost=0, damage=100),))
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 0, "ex 攻击应被挡下")

    def test_tera_source(self):
        """Milotic ex：只挡**Tera** 宝可梦的攻击（伤害+效果）"""
        b = _arena()
        b.players[1].active = _mon(
            "Guard", abilities=(_imm(
                "passive:dmg_immune:self:from:tera:effects"),))
        b.players[0].active = _mon("Tera", hp=300, category="Tera(Stellar)",
                                   attacks=(AttackDef("A", cost=0,
                                                      damage=100,
                                                      effects=("poison",)),))
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 0)
        self.assertEqual(b.players[1].active.status, set(), "效果也应被挡")

    def test_tera_source_not_matched(self):
        b = _arena()
        b.players[1].active = _mon(
            "Guard", abilities=(_imm(
                "passive:dmg_immune:self:from:tera:effects"),))
        _hit(b, effects=("poison",))
        # 100 招式伤害 + 对手回合开始时的中毒检查 10
        self.assertEqual(b.players[1].active.damage, 110, "非 Tera 不应被挡")
        self.assertEqual(b.players[1].active.status, {"poison"})

    def test_has_ability_source(self):
        b = _arena()
        b.players[1].active = _mon(
            "Guard", abilities=(_imm(
                "passive:dmg_immune:self:from:has_ability"),))
        b.players[0].active = _mon(
            "AbilityMon", hp=300,
            abilities=(AbilityDef("X", tag="draw:1", location="field"),),
            attacks=(AttackDef("A", cost=0, damage=100),))
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 0, "有特性的攻击方应被挡")

    def test_has_ability_not_matched_without_ability(self):
        b = _arena()
        b.players[1].active = _mon(
            "Guard", abilities=(_imm(
                "passive:dmg_immune:self:from:has_ability"),))
        _hit(b)
        self.assertEqual(b.players[1].active.damage, 100)

    def test_min_damage_threshold(self):
        """Drednaw：伤害 ≥200 才全额挡（199 照常受伤）"""
        b = _arena()
        b.players[1].active = _mon(
            "Guard", abilities=(_imm("passive:dmg_immune:self:min:200"),))
        _hit(b, damage=200)
        self.assertEqual(b.players[1].active.damage, 0)

        b = _arena()
        b.players[1].active = _mon(
            "Guard", abilities=(_imm("passive:dmg_immune:self:min:200"),))
        _hit(b, damage=190)
        self.assertEqual(b.players[1].active.damage, 190, "低于门槛不免疫")

    def test_special_energy_source(self):
        """Carracosta：攻击方附着了**特殊能量**时才挡（伤害+效果）"""
        b = _arena()
        b.players[1].active = _mon(
            "Guard", abilities=(_imm(
                "passive:dmg_immune:self:from:special_energy:effects"),))
        b.players[0].active = _mon(
            "SpecialHolder", hp=300,
            attacks=(AttackDef("A", cost=0, damage=100, effects=("poison",)),))
        b.players[0].active.senergy.append(
            EnergyRef(EnergyDef("colorless", name="Mist Energy",
                                tag="senergy:mist")))
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 0)
        self.assertEqual(b.players[1].active.status, set())

    def test_damage_only_immunity_lets_effects_through(self):
        """Sylveon「Safeguard」只写 prevent all damage → 效果照常生效"""
        b = _arena()
        b.players[1].active = _mon(
            "Guard", abilities=(_imm("passive:dmg_immune:self:from:ex"),))
        b.players[0].active = _mon("Ex", hp=300, is_ex=True,
                                   attacks=(AttackDef("A", cost=0, damage=100,
                                                      effects=("poison",)),))
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 10,
                         "伤害被挡（仅剩回合开始的中毒检查 10）")
        self.assertEqual(b.players[1].active.status, {"poison"},
                         "只挡伤害的被动不挡效果")

    def test_unknown_attacker_does_not_block(self):
        """来源判不出来时不得凭空免疫（保守：按伤害照常结算）"""
        from engine.battle import _self_immune
        guard = _mon("Guard", abilities=(_imm(
            "passive:dmg_immune:self:from:ex"),))
        self.assertIsNone(_self_immune(guard, None, 100))


class TestTeamBenchImmunity(unittest.TestCase):
    def test_rabsca_blocks_spread_to_bench(self):
        b = _arena()
        b.players[1].bench.append(_mon("B1", hp=100))
        b.players[1].active = _mon(
            "Rabsca", abilities=(_imm(
                "passive:dmg_immune:team_bench:effects"),))
        atk = AttackDef("Spread", cost=0, damage=0, effects=("spread_free:60",))
        b.players[0].active = _mon("Hitter", hp=300, attacks=(atk,))
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].bench[0].damage, 0, "后备应被全队免伤挡下")

    def test_team_bench_does_not_cover_active(self):
        b = _arena()
        b.players[1].active = _mon(
            "Rabsca", hp=300,
            abilities=(_imm("passive:dmg_immune:team_bench:effects"),))
        _hit(b)
        self.assertEqual(b.players[1].active.damage, 100, "全队免伤只护后备")

    def test_shaymin_no_rule_box_only(self):
        """Shaymin：只护**没有规则框**的后备（ex 后备照常受伤）"""
        b = _arena()
        plain = _mon("Plain", hp=100)
        exmon = _mon("BenchEx", hp=100, is_ex=True)
        b.players[1].bench.extend([plain, exmon])
        b.players[1].active = _mon(
            "Shaymin", abilities=(_imm(
                "passive:dmg_immune:team_bench:no_rule_box"),))
        atk = AttackDef("Spread", cost=0, damage=0,
                        effects=("spread_free:60",))
        b.players[0].active = _mon("Hitter", hp=300, attacks=(atk,))
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(plain.damage, 0, "无规则框后备应被挡")
        # spread_free 把 60 任意分配 → 两只后备各 30
        self.assertEqual(exmon.damage, 30, "ex 后备不在限定内")


class TestShieldFilters(unittest.TestCase):
    def test_from_basic_only(self):
        b = _arena()
        b.players[1].active = _mon("Guard")
        b.players[1].active.shield_prevent_all = True
        b.players[1].active.shield_prevent_qual = "from:basic"
        _hit(b)
        self.assertEqual(b.players[1].active.damage, 0, "基础攻击方应被挡")

    def test_from_basic_not_matched_evolved(self):
        b = _arena()
        b.players[1].active = _mon("Guard")
        b.players[1].active.shield_prevent_all = True
        b.players[1].active.shield_prevent_qual = "from:basic"
        b.players[0].active = _mon("Stage1", hp=300, stage=1,
                                   attacks=(AttackDef("A", cost=0,
                                                      damage=100),))
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 100, "一阶攻击方不在限定内")

    def test_if_damage_le(self):
        """Metapod：伤害 ≤60 全挡，70 照常"""
        b = _arena()
        b.players[1].active = _mon("Guard")
        b.players[1].active.shield_prevent_all = True
        b.players[1].active.shield_prevent_qual = "if_damage:le:60"
        _hit(b, damage=60)
        self.assertEqual(b.players[1].active.damage, 0)

        b = _arena()
        b.players[1].active = _mon("Guard")
        b.players[1].active.shield_prevent_all = True
        b.players[1].active.shield_prevent_qual = "if_damage:le:60"
        _hit(b, damage=70)
        self.assertEqual(b.players[1].active.damage, 70)

    def test_shield_tag_carries_qualifier(self):
        """`shield:prevent_all:from:basic` 通过招式效果施加时必须带限定"""
        b = _arena()
        atk = AttackDef("Guard Up", cost=0, damage=0,
                        effects=("shield:prevent_all:from:basic",))
        b.players[0].active = _mon("Hitter", hp=300, attacks=(atk,))
        b.step({"verb": "attack", "attack": 0})
        me = b.players[0].active
        self.assertTrue(me.shield_prevent_all)
        self.assertEqual(me.shield_prevent_qual, "from:basic")
        self.assertFalse(me.shield_prevent_effects, "只挡伤害的护盾不挡效果")

    def test_shield_effects_flag_from_tag(self):
        b = _arena()
        atk = AttackDef("Guard Up", cost=0, damage=0,
                        effects=("shield:prevent_all:effects",))
        b.players[0].active = _mon("Hitter", hp=300, attacks=(atk,))
        b.step({"verb": "attack", "attack": 0})
        self.assertTrue(b.players[0].active.shield_prevent_effects)


class TestBenchImmuneDamageOnly(unittest.TestCase):
    def test_tera_rule_blocks_damage_not_effects(self):
        b = _arena()
        fp = _mon("TeraBench", hp=100,
                  abilities=(_imm("passive:bench_immune_dmg"),))
        b.players[1].bench.append(fp)
        atk = AttackDef("Snipe", cost=0, damage=0,
                        effects=("snipe:opp_bench:60:1", "poison"))
        b.players[0].active = _mon("Hitter", hp=300, attacks=(atk,))
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(fp.damage, 0, "Tera 规则的免伤应挡下后备伤害")
        # 状态只打战斗位，这里用战斗位验证「只挡伤害」不挡效果
        b2 = _arena()
        b2.players[1].active = _mon("TeraActive", hp=300,
                                    abilities=(_imm("passive:bench_immune_dmg"),))
        _hit(b2, effects=("poison",))
        self.assertEqual(b2.players[1].active.damage, 110,
                         "bench_immune_dmg 在战斗位不生效（100 + 中毒检查 10）")
        self.assertEqual(b2.players[1].active.status, {"poison"})


if __name__ == "__main__":
    unittest.main()
