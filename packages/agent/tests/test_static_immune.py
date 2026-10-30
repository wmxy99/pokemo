# -*- coding: utf-8 -*-
"""静态免伤族（第八批 · 2026-10-28）—— 解析侧单测（正例 + 负向守卫）

纪律：**只认可精确判定的整句**。来源/门槛写法认不出时一律不产出 tag，
由 `_EN_MODIFY_RE` 兜底登记为未建模 —— 绝不退化成「无条件全额免疫」。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG = HERE.parent
for p in (str(PKG), str(PKG.parent / "engine")):
    if p not in sys.path:
        sys.path.insert(0, p)

from agent import effects as E  # noqa: E402

FARIGIRAF = ("Prevent all damage done to this Pokémon by attacks from your "
             "opponent’s Basic Pokémon {ex}.")
SYLVEON = ("Prevent all damage done to this Pokémon by attacks from your "
           "opponent’s Pokémon {ex}.")
MILOTIC = ("Prevent all damage from and effects of attacks from your "
           "opponent’s Tera Pokémon done to this Pokémon.")
CORNERSTONE = ("Prevent all damage from attacks done to this Pokémon by your "
               "opponent’s Pokémon that have an Ability.")
CARRACOSTA = ("Prevent all damage from and effects of attacks done to this "
              "Pokémon by your opponent’s Pokémon that have any Special "
              "Energy attached.")
DREDNAW = ("Prevent all damage done to this Pokémon by attacks from your "
           "opponent’s Pokémon if that damage is 200 or more.")
RABSCA = ("Prevent all damage from and effects of attacks from your "
          "opponent’s Pokémon done to your Benched Pokémon.")
SHAYMIN = ("Prevent all damage done to your Benched Pokémon that don’t have a "
           "Rule Box by attacks from your opponent’s Pokémon. (Pokémon {ex}, "
           "Pokémon {V}, etc. have Rule Boxes.)")
METAPOD = ("During your opponent’s next turn, prevent all damage done to this "
           "Pokémon by attacks if that damage is 60 or less.")
TERAPAGOS = ("During your opponent’s next turn, prevent all damage done to "
             "this Pokémon by attacks from Basic non-{C} Pokémon.")
MANECTRIC = ("During your opponent’s next turn, prevent all damage done to "
             "this Pokémon by attacks from Basic Pokémon.")
DUNSPARCE = ("Flip a coin. If heads, during your opponent’s next turn, prevent "
             "all damage from and effects of attacks done to this Pokémon.")
ALTARIA = ("Flip a coin. If heads, during your opponent’s next turn, prevent "
           "all damage done to this Pokémon by attacks.")
BENCH_SELF = ("As long as this Pokémon is on your Bench, prevent all damage "
              "from and effects of attacks from your opponent’s Pokémon done "
              "to this Pokémon.")
TERA_RULE = ("As long as this Pokémon is on your Bench, prevent all damage "
             "done to this Pokémon by attacks (both yours and your opponent’s).")


def _tags(text, kind="ability", name="X"):
    return E.to_engine_tags([E.parse_rule(text, kind=kind, name=name)])


class TestStaticImmuneParse(unittest.TestCase):
    def test_basic_ex(self):
        self.assertEqual(_tags(FARIGIRAF)["tags"],
                         ["passive:dmg_immune:self:from:basic_ex"])

    def test_ex(self):
        self.assertEqual(_tags(SYLVEON)["tags"],
                         ["passive:dmg_immune:self:from:ex"])

    def test_tera_with_effects(self):
        self.assertEqual(_tags(MILOTIC)["tags"],
                         ["passive:dmg_immune:self:from:tera:effects"])

    def test_has_ability(self):
        self.assertEqual(_tags(CORNERSTONE)["tags"],
                         ["passive:dmg_immune:self:from:has_ability"])

    def test_special_energy_with_effects(self):
        self.assertEqual(_tags(CARRACOSTA)["tags"],
                         ["passive:dmg_immune:self:from:special_energy:effects"])

    def test_damage_threshold(self):
        self.assertEqual(_tags(DREDNAW)["tags"],
                         ["passive:dmg_immune:self:min:200"])

    def test_team_bench(self):
        self.assertEqual(_tags(RABSCA)["tags"],
                         ["passive:dmg_immune:team_bench:effects"])

    def test_team_bench_no_rule_box(self):
        self.assertEqual(_tags(SHAYMIN)["tags"],
                         ["passive:dmg_immune:team_bench:no_rule_box"])

    def test_bench_self_effects_variant(self):
        """「from and effects of attacks」→ 伤害+效果（旧 bench_immune）"""
        self.assertEqual(_tags(BENCH_SELF)["tags"], ["passive:bench_immune"])

    def test_tera_rule_damage_only(self):
        """Tera 规则只挡伤害 → bench_immune_dmg（不得冒充伤害+效果）"""
        self.assertEqual(_tags(TERA_RULE, kind="rule")["tags"],
                         ["passive:bench_immune_dmg"])

    # ---- 下回合护盾的来源/门槛限定 ----
    def test_shield_if_damage_le(self):
        self.assertEqual(_tags(METAPOD, kind="attack")["tags"],
                         ["shield:prevent_all:if_damage:le:60"])

    def test_shield_basic_non_colorless(self):
        self.assertEqual(_tags(TERAPAGOS, kind="attack")["tags"],
                         ["shield:prevent_all:from:basic_non_colorless"])

    def test_shield_from_basic(self):
        self.assertEqual(_tags(MANECTRIC, kind="attack")["tags"],
                         ["shield:prevent_all:from:basic"])

    def test_coin_branch_shield_keeps_effects(self):
        self.assertEqual(_tags(DUNSPARCE, kind="attack")["tags"],
                         ["coin:heads:shield:prevent_all:effects"])

    def test_coin_branch_shield_damage_only(self):
        self.assertEqual(_tags(ALTARIA, kind="attack")["tags"],
                         ["coin:heads:shield:prevent_all"])

    # ---- 负向守卫：不可精确表达的写法必须如实未建模 ----
    def test_no_source_must_not_become_full_immunity(self):
        r = _tags("Prevent all damage done to this Pokémon by attacks.")
        self.assertEqual(r["tags"], [], "缺来源限定不得退化成全额免疫")
        self.assertTrue(r["unsupported"], r)

    def test_named_source_rejected(self):
        r = _tags("Prevent all damage done to this Pokémon by attacks from "
                  "your opponent’s Pokémon named Zoroark.")
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])

    def test_type_source_rejected(self):
        r = _tags("Prevent all damage done to this Pokémon by attacks from "
                  "your opponent’s {R} Pokémon.")
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])

    def test_unknown_shield_source_rejected(self):
        r = _tags("During your opponent’s next turn, prevent all damage done "
                  "to this Pokémon by attacks from your opponent’s {R} "
                  "Pokémon.", kind="attack")
        self.assertEqual(r["tags"], [], "未知护盾来源不得退化成挡全部")
        self.assertTrue(r["unsupported"])


if __name__ == "__main__":
    unittest.main()
