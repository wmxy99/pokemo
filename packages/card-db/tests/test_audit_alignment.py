# -*- coding: utf-8 -*-
"""对账脚本（audit_pool_alignment）回归守卫

目的：对账脚本本身必须有**负向自测**——否则它只是橡皮图章。
本文件锁定 `spread_free`（E5 任意分配）的识别口径：

  · 引擎 `spread_free:N` = 把 N 点伤害**自由分配**到对手后备（位置码由 plan 指定，
    不含战斗位）。卡面「Put N damage counters on your opponent's Benched Pokémon
    in any way you like」正是此语义 → 必须判为精确可建模，不再误报「应整卡不收」。
  · 守卫不可过宽：不含 bench / 含条件（that has / for each）的「in any way」仍须判未建模；
    有 spread_free 子句但缺 tag 的卡仍须被抓出。

这些用例保证：改过解析链或引擎 tag 后重跑对账，既能放行真·精确建模，
也不让旧问题静默通过。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

import audit_pool_alignment as A  # noqa: E402


class TestAuditSpreadFree(unittest.TestCase):
    DESC = ("Put 2 damage counters on your opponent’s Benched Pokémon "
            "in any way you like.")

    def test_spread_free_recognized(self):
        """自由分配 + 对手后备 → spread_free 精确可建模，不应报错"""
        errs = A.audit_extra_damage("Flutter Mane", "Hex Hurl", self.DESC,
                                    ["spread_free:20"])
        self.assertEqual(errs, [], "spread_free 精确建模被误报为未建模")

    def test_missing_spread_free_tag_still_flagged(self):
        """有「in any way」子句却无 spread_free tag → 仍须抓出"""
        errs = A.audit_extra_damage("Flutter Mane", "Hex Hurl", self.DESC, [])
        self.assertTrue(errs, "缺 spread_free tag 却放行")

    def test_in_any_way_without_bench_still_unmodeled(self):
        """含战斗位的「in any way」：spread_free 只覆盖后备 → 仍须判未建模"""
        desc = ("Put 2 damage counters on your opponent’s Pokémon "
                "in any way you like.")
        errs = A.audit_extra_damage("X", "Y", desc, ["spread_free:20"])
        self.assertTrue(errs, "非后备目标不应被 spread_free 放行")

    def test_conditional_in_any_way_still_unmodeled(self):
        """带条件（that has）的「in any way」→ 子集，仍须判未建模"""
        desc = ("Put 2 damage counters on your opponent’s Benched Pokémon "
                "that has a Tool attached in any way you like.")
        errs = A.audit_extra_damage("X", "Y", desc, ["spread_free:20"])
        self.assertTrue(errs, "含条件的目标不应被 spread_free 放行")

    def test_each_benched_unchanged(self):
        """「each of benched」语义不变 → snipe:opp_bench（与 spread_free 区分）"""
        desc = ("Put 2 damage counters on each of your opponent’s "
                "Benched Pokémon.")
        errs = A.audit_extra_damage("X", "Y", desc, ["snipe:opp_bench:20"])
        self.assertEqual(errs, [], "each-of 语义回归")


class TestAuditStaticDamage(unittest.TestCase):
    ADD = ("Attacks used by your Pokémon do 20 more damage to your opponent’s "
           "Active Pokémon (before applying Weakness and Resistance).")

    def test_static_add_tag_recognized(self):
        errs = A.audit_static_damage("Serperior ex", "Regal Cheer", self.ADD,
                                     ["fx:static_dmg:add:20:team"])
        self.assertEqual(errs, [])

    def test_missing_static_add_tag_flagged(self):
        errs = A.audit_static_damage("Serperior ex", "Regal Cheer", self.ADD, [])
        self.assertTrue(errs, "静态加伤缺 tag 却放行")

    def test_wrong_direction_flagged(self):
        errs = A.audit_static_damage("P", "X", self.ADD,
                                     ["fx:static_dmg:sub:20:opp_active"])
        self.assertTrue(errs, "加伤写成减伤仍放行")

    def test_typed_reduction_exact(self):
        desc = ("This Pokémon takes 30 less damage from attacks from your "
                "opponent’s {R} or {W} Pokémon (after applying Weakness and "
                "Resistance).")
        errs = A.audit_static_damage(
            "Dewgong", "Thick Fat", desc,
            ["passive:dmg_reduce:30:from:type:fire,water"])
        self.assertEqual(errs, [])

    def test_unconditional_reduction_flagged(self):
        desc = ("This Pokémon takes 30 less damage from attacks from your "
                "opponent’s {R} or {W} Pokémon (after applying Weakness and "
                "Resistance).")
        errs = A.audit_static_damage("Dewgong", "Thick Fat", desc,
                                     ["passive:dmg_reduce:30"])
        self.assertTrue(errs, "来源限定减伤退化成无条件仍放行")


class TestAuditStaticImmune(unittest.TestCase):
    """"静态免伤来源 ↔ 卡面"对账（第八批）"""

    FARIGIRAF = ("Prevent all damage done to this Pokémon by attacks from your "
                 "opponent’s Basic Pokémon {ex}.")
    RABSCA = ("Prevent all damage from and effects of attacks from your "
              "opponent’s Pokémon done to your Benched Pokémon.")
    METAPOD = ("During your opponent’s next turn, prevent all damage done to "
               "this Pokémon by attacks if that damage is 60 or less.")

    def test_source_matches(self):
        errs = A.audit_static_immune(
            "Farigiraf ex", "Armor Tail", self.FARIGIRAF,
            ["passive:dmg_immune:self:from:basic_ex"])
        self.assertEqual(errs, [])

    def test_wrong_source_flagged(self):
        """tag 说「基础 ex」、卡面却写 Tera → 静默错映射，必须抓出"""
        errs = A.audit_static_immune(
            "Farigiraf ex", "Armor Tail", self.FARIGIRAF,
            ["passive:dmg_immune:self:from:tera:effects"])
        self.assertTrue(errs, "来源限定与卡面不符仍放行")

    def test_missing_tag_flagged(self):
        errs = A.audit_static_immune("X", "Y", self.FARIGIRAF, [])
        self.assertTrue(errs, "卡面写了免伤却无 tag 仍放行")

    def test_team_bench_scope_mismatch_flagged(self):
        errs = A.audit_static_immune("Rabsca", "Spherical Shield", self.RABSCA,
                                     ["passive:dmg_immune:self:from:ex"])
        self.assertTrue(errs, "全队后备免伤写成自身免伤仍放行")

    def test_shield_threshold_flagged(self):
        errs = A.audit_static_immune(
            "Metapod", "Harden", self.METAPOD,
            ["shield:prevent_all:if_damage:le:40"])
        self.assertTrue(errs, "护盾伤害门槛与卡面不符仍放行")

    def test_effects_flag_flagged(self):
        """标了 :effects（连效果一起挡）但卡面只写 prevent all damage"""
        errs = A.audit_static_immune(
            "Metapod", "Harden", self.METAPOD,
            ["shield:prevent_all:if_damage:le:60:effects"])
        self.assertTrue(errs, "护盾多挡了效果仍放行")


class TestAuditKnockout(unittest.TestCase):
    """第九批：击倒范围 / 全量回复范围 ↔ 卡面"""

    HYDRA = ("Discard 6 Basic {G} Energy cards from your hand, and Knock Out "
             "your opponent’s Active Pokémon. If you can’t discard 6 cards in "
             "this way, this attack does nothing.")
    GLACEON = ("Knock Out 1 of your opponent’s Pokémon that has exactly 6 "
               "damage counters on it.")

    def test_scope_matches(self):
        errs = A.audit_knockout("Hydrapple", "Hydra Breath", self.HYDRA,
                                ["knockout:opp_active"])
        self.assertEqual(errs, [])

    def test_wrong_scope_flagged(self):
        """卡面写 Active、tag 却是任意一只 → 静默改变击倒目标，必须抓出"""
        errs = A.audit_knockout("Hydrapple", "Hydra Breath", self.HYDRA,
                                ["knockout:opp_any"])
        self.assertTrue(errs)

    def test_counter_threshold_exact(self):
        errs = A.audit_knockout("Glaceon ex", "Euclase", self.GLACEON,
                                ["knockout:opp_counters:6"])
        self.assertEqual(errs, [])
        errs = A.audit_knockout("Glaceon ex", "Euclase", self.GLACEON,
                                ["knockout:opp_counters:3"])
        self.assertTrue(errs)

    def test_missing_knockout_tag_flagged(self):
        errs = A.audit_knockout("Hydrapple", "Hydra Breath", self.HYDRA, [])
        self.assertTrue(errs)


class TestAuditStadiumForm(unittest.TestCase):
    """第九批：场地静态规则 ↔ 卡面（阵营键尤其危险）"""

    NSC = ("N’s Pokémon in play (both yours and your opponent’s) have no "
           "Retreat Cost.")

    def test_faction_matches(self):
        errs = A.audit_stadium_form("N’s Castle", self.NSC,
                                    "stadium:no_retreat:faction:ns")
        self.assertEqual(errs, [])

    def test_wrong_faction_flagged(self):
        errs = A.audit_stadium_form("N’s Castle", self.NSC,
                                    "stadium:no_retreat:faction:hops")
        self.assertTrue(errs)

    def test_unknown_faction_key_flagged(self):
        errs = A.audit_stadium_form("N’s Castle", self.NSC,
                                    "stadium:no_retreat:faction:xyz")
        self.assertTrue(errs)


class TestAuditFossil(unittest.TestCase):
    DESC = ("Play this card as if it were a 60-HP Basic {C} Pokémon. This card "
            "can’t be affected by any Special Conditions and can’t retreat.\n\n"
            "At any time during your turn, you may discard this card from play.")
    PAYLOAD = {"hp": 60, "types": ["colorless"], "abilities": []}

    def test_fossil_consistent(self):
        errs = A.audit_fossil("Antique Root Fossil", self.DESC,
                              "fossil:60:colorless", self.PAYLOAD)
        self.assertEqual(errs, [])

    def test_hp_mismatch_flagged(self):
        errs = A.audit_fossil("Antique Root Fossil", self.DESC,
                              "fossil:70:colorless", {"hp": 70})
        self.assertTrue(errs, "卡面 60HP ↔ tag 70 仍放行")

    def test_payload_hp_mismatch_flagged(self):
        errs = A.audit_fossil("Antique Root Fossil", self.DESC,
                              "fossil:60:colorless", {"hp": 50})
        self.assertTrue(errs, "上场形态 HP 与 tag 不一致仍放行")

    def test_type_mismatch_flagged(self):
        errs = A.audit_fossil("Antique Root Fossil", self.DESC,
                              "fossil:60:fire", self.PAYLOAD)
        self.assertTrue(errs, "卡面 {C} ↔ tag fire 仍放行")

    def test_missing_restriction_text_flagged(self):
        desc = "Play this card as if it were a 60-HP Basic {C} Pokémon."
        errs = A.audit_fossil("X", desc, "fossil:60:colorless", self.PAYLOAD)
        self.assertTrue(errs, "卡面未写「不可撤退 / 状态免疫」却按该语义执行")

    def test_no_fossil_text_flagged(self):
        errs = A.audit_fossil("X", "Draw 2 cards.", "fossil:60:colorless",
                              self.PAYLOAD)
        self.assertTrue(errs, "有 fossil tag 但卡面不是化石文本仍放行")


class TestAuditNextTurnDmg(unittest.TestCase):
    """第十六批：跨回合伤害修正 ↔ 卡面（三句各一项，顺序/对象都不同）"""

    TAKEN = ("During your next turn, the Defending Pokémon takes 50 more damage "
             "from attacks (after applying Weakness and Resistance).")
    MINUS = ("During your opponent’s next turn, attacks used by the Defending "
             "Pokémon do 20 less damage (before applying Weakness and "
             "Resistance).")
    SELF = ("During your next turn, this Pokémon’s Echoed Voice attack does 80 "
            "more damage (before applying Weakness and Resistance).")

    def test_taken_matches(self):
        errs = A.audit_next_turn_dmg("Vibrava", "Screech", self.TAKEN,
                                     ["dmg_taken_plus_next:50"])
        self.assertEqual(errs, [])

    def test_taken_missing_flagged(self):
        errs = A.audit_next_turn_dmg("Vibrava", "Screech", self.TAKEN, [])
        self.assertTrue(errs, "跨回合加伤缺 tag 仍放行")

    def test_taken_amount_mismatch_flagged(self):
        errs = A.audit_next_turn_dmg("Vibrava", "Screech", self.TAKEN,
                                     ["dmg_taken_plus_next:100"])
        self.assertTrue(errs, "数值不符仍放行")

    def test_minus_matches(self):
        errs = A.audit_next_turn_dmg("Hop’s Rookidee", "Intimidating Stare",
                                     self.MINUS, ["atk_minus_next:20"])
        self.assertEqual(errs, [])

    def test_minus_not_counted_as_static(self):
        """这三句不是常驻静态层 → 静态伤害对账必须让位（否则双重误报）"""
        errs = A.audit_static_damage("Hop’s Rookidee", "Intimidating Stare",
                                     self.MINUS, ["atk_minus_next:20"])
        self.assertEqual(errs, [])

    def test_minus_missing_flagged(self):
        errs = A.audit_next_turn_dmg("Hop’s Rookidee", "Intimidating Stare",
                                     self.MINUS, [])
        self.assertTrue(errs, "跨回合减伤缺 tag 仍放行")

    def test_self_named_attack_matches(self):
        errs = A.audit_next_turn_dmg("Meloetta ex", "Echoed Voice", self.SELF,
                                     ["self_attack_plus_next:80:name:echoed voice"])
        self.assertEqual(errs, [])

    def test_self_missing_flagged(self):
        errs = A.audit_next_turn_dmg("Meloetta ex", "Echoed Voice", self.SELF, [])
        self.assertTrue(errs, "具名招式跨回合加伤缺 tag 仍放行")

    def test_nbsp_text_matches(self):
        """实测卡面在括号前有不换行空格（Pawmi）→ 对账不得静默失配"""
        desc = ("During your opponent’s next turn, attacks used by the Defending "
                "Pokémon do 30 less damage\xa0(before applying Weakness and "
                "Resistance).")
        errs = A.audit_next_turn_dmg("Pawmi", "Growl", desc, ["atk_minus_next:30"])
        self.assertEqual(errs, [])


class TestAuditSnipeScale(unittest.TestCase):
    """第十七批：选定目标的缩放 / 条件（`for each` 不再一律判未建模）"""

    BOLT = ("This attack does 30 damage to 1 of your opponent’s Pokémon for each "
            "Energy attached to this Pokémon. (Don’t apply Weakness and "
            "Resistance for Benched Pokémon.)")
    PALAFIN = ("This Pokémon also does 10 damage to itself for each damage "
               "counter on it.")

    def test_scaled_snipe_recognized(self):
        errs = A.audit_extra_damage(
            "Raging Bolt", "Thunderburst Storm", self.BOLT,
            ["snipe:opp:30:1:per:own_active_energy"])
        self.assertEqual(errs, [], "带缩放的 snipe 被误报未建模")

    def test_scaled_missing_per_flagged(self):
        errs = A.audit_extra_damage("Raging Bolt", "Thunderburst Storm",
                                    self.BOLT, ["snipe:opp:30:1"])
        self.assertTrue(errs, "缩放子句缺 `:per:` 仍放行")

    def test_scaled_self_recognized(self):
        errs = A.audit_extra_damage(
            "Palafin", "Vanguard Punch", self.PALAFIN,
            ["self:10:per:own_active_counters"])
        self.assertEqual(errs, [], "按计数源缩放的反伤被误报")

    def test_scaled_self_missing_per_flagged(self):
        errs = A.audit_extra_damage("Palafin", "Vanguard Punch", self.PALAFIN,
                                    ["self:10"])
        self.assertTrue(errs, "按计数源缩放的反伤缺 `:per:` 仍放行")

    def test_subset_target_still_unmodeled(self):
        desc = ("This attack does 60 damage to 1 of your opponent’s Benched "
                "Pokémon {ex}. (Don’t apply Weakness and Resistance for "
                "Benched Pokémon.)")
        errs = A.audit_extra_damage("Zeraora", "Bolt Flare", desc,
                                    ["snipe:opp_bench:60:1"])
        self.assertTrue(errs, "{ex} 子集目标不应被 snipe 放行")

    # ---- 第二十七批（2026-10-29）：目标筛选 / 逐目标缩放 ----
    EXCADRILL = ("This attack also does 60 damage to 1 of your opponent’s "
                 "Benched Pokémon that has any damage counters on it. (Don’t "
                 "apply Weakness and Resistance for Benched Pokémon.)")
    HIPPOWDON = ("This attack also does 40 damage to each Benched Pokémon that "
                 "has any damage counters on it (both yours and your "
                 "opponent’s). (Don’t apply Weakness and Resistance for "
                 "Benched Pokémon.)")
    SNEASEL = ("This attack does 20 damage to 1 of your opponent’s Benched "
               "Pokémon for each damage counter on that Pokémon. (Don’t apply "
               "Weakness and Resistance for Benched Pokémon.)")

    def test_target_damaged_recognized(self):
        errs = A.audit_extra_damage(
            "Excadrill ex", "Piercing Drill", self.EXCADRILL,
            ["snipe:opp_bench:60:1:target:damaged"])
        self.assertEqual(errs, [], "带 `:target:damaged` 仍被误报未建模")

    def test_target_damaged_missing_flagged(self):
        errs = A.audit_extra_damage("Excadrill ex", "Piercing Drill",
                                    self.EXCADRILL, ["snipe:opp_bench:60:1"])
        self.assertTrue(errs, "缺 `:target:damaged` 仍放行（会打没指示物的目标）")

    def test_both_bench_filter(self):
        errs = A.audit_extra_damage(
            "Hippowdon", "Super Sandstorm", self.HIPPOWDON,
            ["snipe:both_bench:40:all:target:damaged"])
        self.assertEqual(errs, [])

    def test_per_target_counters_recognized(self):
        errs = A.audit_extra_damage(
            "Team Rocket’s Sneasel", "Strike the Sleeper", self.SNEASEL,
            ["snipe:opp_bench:20:1:per:target_counters"])
        self.assertEqual(errs, [])

    def test_per_target_counters_required(self):
        """用全局计数源（如 opp_bench_counters）会把别人的指示物也算进来"""
        errs = A.audit_extra_damage(
            "Team Rocket’s Sneasel", "Strike the Sleeper", self.SNEASEL,
            ["snipe:opp_bench:20:1:per:opp_bench_counters"])
        self.assertTrue(errs, "逐目标缩放却用了全局计数源仍被放行")


class TestAuditCoinForms(unittest.TestCase):
    """第十八批：连续掷币缩放 / 抛币复合子效果 ↔ 卡面"""

    UNTIL = ("Flip a coin until you get tails. This attack does 50 more damage "
             "for each heads.")
    COMPOSITE = ("Flip a coin. If heads, this attack does 30 more damage, and "
                 "heal 30 damage from this Pokémon.")

    def test_until_tails_recognized(self):
        errs = A.audit_coin_forms("Breloom", "Damage Rush", self.UNTIL,
                                  ["coin_until_tails:dmg_plus:50"])
        self.assertEqual(errs, [])

    def test_until_tails_missing_flagged(self):
        errs = A.audit_coin_forms("Breloom", "Damage Rush", self.UNTIL, [])
        self.assertTrue(errs, "连续掷币缺 tag 仍放行")

    def test_until_tails_flattened_to_each_flagged(self):
        """把「连续正面累加」压成一次 +N（coin_each/dmg_plus）→ 必须抓出"""
        errs = A.audit_coin_forms("Breloom", "Damage Rush", self.UNTIL,
                                  ["coin_each:1:heads:dmg_plus:50"])
        self.assertTrue(errs, "连续掷币被压成一次加伤仍放行")

    def test_composite_single_tag_recognized(self):
        errs = A.audit_coin_forms("Floragato", "Magical Leaf", self.COMPOSITE,
                                  ["coin:heads:dmg_plus:30|heal:30"])
        self.assertEqual(errs, [])

    def test_composite_split_tags_flagged(self):
        """拆成两条独立 coin tag → 会抛两次币，必须抓出"""
        errs = A.audit_coin_forms("Floragato", "Magical Leaf", self.COMPOSITE,
                                  ["coin:heads:dmg_plus:30", "coin:heads:heal:30"])
        self.assertTrue(errs, "复合子效果被拆成两条 coin tag 仍放行")


class TestAuditDmgLayer(unittest.TestCase):
    """第十九批：全队常驻减伤 / 额外能量条件加伤 ↔ 卡面"""

    CURLY = ("As long as you have at least 1 other Bouffalant in play, all of "
             "your Basic {C} Pokémon take 60 less damage from attacks from your "
             "opponent’s Pokémon (after applying Weakness and Resistance). The "
             "effect of Curly Wall doesn’t stack.")
    EXTRA = ("If this Pokémon has at least 2 extra Energy attached (in addition "
             "to this attack’s cost), this attack does 100 more damage.")

    def test_team_reduce_recognized(self):
        errs = A.audit_dmg_layer(
            "Bouffalant", "Curly Wall", self.CURLY,
            ["fx:team_reduce:60:basic:colorless:other:bouffalant:nonstack"])
        self.assertEqual(errs, [])

    def test_team_reduce_missing_flagged(self):
        errs = A.audit_dmg_layer("Bouffalant", "Curly Wall", self.CURLY, [])
        self.assertTrue(errs, "全队减伤缺 tag 仍放行")

    def test_team_reduce_wrong_scope_flagged(self):
        """写成受击者自身的 passive（只减自己）→ 语义不符，必须抓出"""
        errs = A.audit_dmg_layer("Bouffalant", "Curly Wall", self.CURLY,
                                 ["passive:dmg_reduce:60"])
        self.assertTrue(errs, "全队减伤退化成自身减伤仍放行")

    def test_extra_energy_recognized(self):
        errs = A.audit_dmg_layer(
            "Electivire ex", "High-Voltage Press", self.EXTRA,
            ["dmg_plus:100:if:own_active_extra_energy_at_least:2"])
        self.assertEqual(errs, [])

    def test_extra_energy_unconditional_flagged(self):
        errs = A.audit_dmg_layer("Electivire ex", "High-Voltage Press",
                                 self.EXTRA, ["dmg_plus:100"])
        self.assertTrue(errs, "额外能量加伤退化成无条件仍放行")

    def test_extra_energy_wrong_threshold_flagged(self):
        errs = A.audit_dmg_layer(
            "Electivire ex", "High-Voltage Press", self.EXTRA,
            ["dmg_plus:100:if:own_active_extra_energy_at_least:3"])
        self.assertTrue(errs, "额外能量门槛与卡面不符仍放行")


class TestAuditCountersAnyTarget(unittest.TestCase):
    """第二十批（2026-10-29）：指示物直放**任意**对手宝可梦（第十八类对账）

    · 卡面「N damage counters on 1 of your opponent's Pokémon」→ 必须
      `counters:N:scope:opp:1`；落 `snipe:opp` 会**静默多算**（snipe 打战斗位
      吃弱点/抗性，而指示物不吃）。
    · 缩放子句必须带 `:per:<counter>`。
    """

    DESC = "Place 1 damage counter on 1 of your opponent’s Pokémon."
    SCALED = ("Put 2 damage counters on 1 of your opponent’s Pokémon for each "
              "Basic {G} Energy card in your discard pile.")

    def test_counters_scope_opp_matches(self):
        errs = A.audit_extra_damage("Bramblin", "Sneaky Placement", self.DESC,
                                    ["counters:1:scope:opp:1"])
        self.assertEqual(errs, [])

    def test_snipe_mapping_flagged(self):
        """旧错映射（snipe 走伤害字段）必须被抓出"""
        errs = A.audit_extra_damage("Bramblin", "Sneaky Placement", self.DESC,
                                    ["snipe:opp:10:1"])
        self.assertTrue(errs, "指示物被映射成 snipe 仍放行")

    def test_missing_scope_flagged(self):
        errs = A.audit_extra_damage("Bramblin", "Sneaky Placement", self.DESC,
                                    ["counters:1"])
        self.assertTrue(errs, "缺 `:scope:opp`（只能打战斗位）仍放行")

    def test_many_target_matches(self):
        """「on **each of** your opponent's Pokémon」→ `:scope:opp:all`"""
        desc = "Put 2 damage counters on each of your opponent’s Pokémon."
        errs = A.audit_extra_damage("Uxie", "Painful Memories", desc,
                                    ["counters:2:scope:opp:all"])
        self.assertEqual(errs, [])

    def test_many_target_snipe_flagged(self):
        desc = "Put 2 damage counters on each of your opponent’s Pokémon."
        errs = A.audit_extra_damage("Uxie", "Painful Memories", desc,
                                    ["snipe:opp:20:all"])
        self.assertTrue(errs, "全体指示物被映射成 snipe 仍放行")

    def test_many_target_single_scope_flagged(self):
        desc = "Put 2 damage counters on each of your opponent’s Pokémon."
        errs = A.audit_extra_damage("Uxie", "Painful Memories", desc,
                                    ["counters:2:scope:opp:1"])
        self.assertTrue(errs, "「each of」被写成只打 1 只仍放行")

    def test_scaled_matches(self):
        errs = A.audit_extra_damage(
            "Sinistcha ex", "Life Bristles", self.SCALED,
            ["counters:2:per:own_discard_energy:grass:scope:opp:1"])
        self.assertEqual(errs, [])

    def test_scaled_missing_per_flagged(self):
        errs = A.audit_extra_damage("Sinistcha ex", "Life Bristles", self.SCALED,
                                    ["counters:2:scope:opp:1"])
        self.assertTrue(errs, "缩放子句缺 `:per:` 仍放行")


class TestAuditCoinStatus(unittest.TestCase):
    """第二十二批：抛币状态子效果（第二十类对账）

    历史缺陷：`coin:heads:paralyzed` 这类卡面英文状态名会走到引擎「未知 tag」
    分支被**静默忽略**（状态永不施加）；多币条件（`cond:`）形态非法则抛错。
    """

    SINGLE = ("Flip a coin. If heads, your opponent’s Active Pokémon is now "
              "Paralyzed.")
    SLURP = ("Flip 2 coins. This attack does 90 damage for each heads. If both "
             "of them are tails, your opponent’s Active Pokémon is now Confused.")

    def test_canonical_status_passes(self):
        errs = A.audit_coin_status("X", "Y", self.SINGLE, ["coin:heads:paralyze"])
        self.assertEqual(errs, [])

    def test_legacy_status_word_flagged(self):
        errs = A.audit_coin_status("X", "Y", self.SINGLE,
                                   ["coin:heads:paralyzed"])
        self.assertTrue(any("非规范" in e for e in errs), errs)

    def test_missing_status_sub_flagged(self):
        """卡面写了抛币状态、tag 里却没有状态子效果 → 静默少施加"""
        errs = A.audit_coin_status("X", "Y", self.SINGLE,
                                   ["coin:heads:dmg_plus:30"])
        self.assertTrue(errs, "缺 coin 状态子效果仍放行")

    def test_multi_cond_passes(self):
        errs = A.audit_coin_status(
            "Slurpuff", "Slurp Slurp", self.SLURP,
            ["coin_each:2:heads:dmg_set:90|cond:all_tails:confuse"])
        self.assertEqual(errs, [])

    def test_multi_cond_bad_mode_flagged(self):
        errs = A.audit_coin_status(
            "Slurpuff", "Slurp Slurp", self.SLURP,
            ["coin_each:2:heads:dmg_set:90|cond:whatever:confuse"])
        self.assertTrue(errs, "未知多币条件仍放行")

    def test_multi_cond_legacy_status_flagged(self):
        errs = A.audit_coin_status(
            "Slurpuff", "Slurp Slurp", self.SLURP,
            ["coin_each:2:heads:dmg_set:90|cond:all_tails:confused"])
        self.assertTrue(any("非规范" in e for e in errs), errs)


class TestAuditSpecialEnergyDiscard(unittest.TestCase):
    """第二十四批：弃置对手战斗位特殊能量（第二十二类对账）"""

    DESC = "Discard a Special Energy from your opponent’s Active Pokémon."

    def test_special_matches(self):
        errs = A.audit_special_energy_discard(
            "Cobalion", "Righteous Edge", self.DESC,
            ["discard_energy:1:opp:special"])
        self.assertEqual(errs, [])

    def test_plain_discard_flagged(self):
        """普通 `discard_energy:…:opp` 会先弃基本能量 → 必须拦下"""
        errs = A.audit_special_energy_discard(
            "Cobalion", "Righteous Edge", self.DESC, ["discard_energy:1:opp"])
        self.assertTrue(errs, "缺 `:special` 仍放行")

    def test_missing_flagged(self):
        errs = A.audit_special_energy_discard(
            "Cobalion", "Righteous Edge", self.DESC, [])
        self.assertTrue(errs)


class TestAuditOnDamaged(unittest.TestCase):
    """第二十九批：受击反制（第二十六类对账）"""

    ORTHWORM = ("If this Pokémon is damaged by an attack from your opponent’s "
                "Pokémon (even if this Pokémon is Knocked Out), place 2 damage "
                "counters on the Attacking Pokémon for each {M} Energy attached "
                "to this Pokémon.")
    BOUFFALANT = ("During your opponent’s next turn, if this Pokémon is damaged by "
                  "an attack (even if this Pokémon is Knocked Out), put 6 damage "
                  "counters on the Attacking Pokémon.")
    ZAMAZENTA = ("During your opponent’s next turn, if this Pokémon is damaged by "
                 "an attack (even if this Pokémon is Knocked Out), put damage "
                 "counters on the Attacking Pokémon equal to the damage done to "
                 "this Pokémon.")

    def test_counter_per_matches(self):
        errs = A.audit_on_damaged("Orthworm ex", "Pummeling Payback",
                                  self.ORTHWORM,
                                  ["on_damaged:counter_per:2:metal"])
        self.assertEqual(errs, [])

    def test_counter_per_fixed_value_flagged(self):
        errs = A.audit_on_damaged("Orthworm ex", "Pummeling Payback",
                                  self.ORTHWORM, ["on_damaged:counter:20"])
        self.assertTrue(errs, "固定值会少算（能量越多反制越强）")

    def test_retaliate_fixed_matches(self):
        errs = A.audit_on_damaged("Bouffalant", "Ready to Ram", self.BOUFFALANT,
                                  ["retaliate_next:60"])
        self.assertEqual(errs, [])

    def test_retaliate_missing_flagged(self):
        errs = A.audit_on_damaged("Bouffalant", "Ready to Ram", self.BOUFFALANT,
                                  [])
        self.assertTrue(errs)

    def test_retaliate_equal_matches(self):
        errs = A.audit_on_damaged("Zamazenta", "Strong Bash", self.ZAMAZENTA,
                                  ["retaliate_next:equal"])
        self.assertEqual(errs, [])

    def test_retaliate_equal_wrong_form_flagged(self):
        errs = A.audit_on_damaged("Zamazenta", "Strong Bash", self.ZAMAZENTA,
                                  ["retaliate_next:60"])
        self.assertTrue(errs)


class TestAuditEnergyToDeck(unittest.TestCase):
    """第二十八批：弃牌堆基本能量洗回牌库（第二十五类对账）"""

    KYOGRE = ("This attack does 20 damage for each Basic {W} Energy card in your "
              "discard pile. Then, shuffle those cards into your deck.")
    SINISTCHA = ("Put 2 damage counters on 1 of your opponent’s Pokémon for each "
                 "Basic {G} Energy card in your discard pile. Then, shuffle those "
                 "Energy cards into your deck.")

    def test_kyogre_matches(self):
        errs = A.audit_energy_to_deck(
            "Kyogre", "Riptide", self.KYOGRE,
            ["dmg_set:20:per:own_discard_energy_water",
             "energy_to_deck:water:all"])
        self.assertEqual(errs, [])

    def test_missing_shuffle_back_flagged(self):
        """Kyogre 曾经的池内状态：只计数不洗回 → 下次重复计数（静默加强）"""
        errs = A.audit_energy_to_deck(
            "Kyogre", "Riptide", self.KYOGRE,
            ["dmg_set:20:per:own_discard_energy_water"])
        self.assertTrue(errs, "缺 `energy_to_deck:` 仍被放行")

    def test_wrong_color_flagged(self):
        errs = A.audit_energy_to_deck(
            "Kyogre", "Riptide", self.KYOGRE, ["energy_to_deck:grass:all"])
        self.assertTrue(errs)

    def test_sinistcha_matches(self):
        errs = A.audit_energy_to_deck(
            "Sinistcha ex", "Re-Brew", self.SINISTCHA,
            ["counters:2:per:own_discard_energy_grass:scope:opp:1",
             "energy_to_deck:grass:all"])
        self.assertEqual(errs, [])

    def test_non_energy_source_flagged(self):
        """计数源不是「弃牌堆基本能量」→ 不该进池"""
        errs = A.audit_energy_to_deck(
            "X", "Y", "Discard 2 cards from your hand. Then, shuffle those cards "
            "into your deck.", ["discard:2"])
        self.assertTrue(errs)


class TestAuditAbilityCounters(unittest.TestCase):
    """第二十八批：双方「有特性者」逐个放指示物（Cofagrigus）"""

    DESC = ("Put 6 damage counters on each Pokémon that has an Ability (both "
            "yours and your opponent’s).")

    def test_matches(self):
        errs = A.audit_extra_damage("Cofagrigus", "Law of the Underworld",
                                    self.DESC, ["counters:6:scope:ability:all"])
        self.assertEqual(errs, [])

    def test_missing_flagged(self):
        errs = A.audit_extra_damage("Cofagrigus", "Law of the Underworld",
                                    self.DESC, ["counters:6"])
        self.assertTrue(errs)


class TestAuditCounterSubset(unittest.TestCase):
    """第二十六批：限定后的指示物计数源（第二十四类对账）"""

    TYPE_TXT = ("This attack does 20 damage for each damage counter on all of "
                "your Benched {F} Pokémon.")
    NAMED_TXT = ("This attack does 10 damage for each damage counter on all of "
                 "your Benched Cynthia’s Pokémon.")
    TAUROS_TXT = ("This attack does 40 damage for each of your Pokémon that has "
                  "“Tauros” in its name that has any damage counters on it.")

    def test_type_matches(self):
        errs = A.audit_counter_subset(
            "Gigalith", "Vengeful Cannon", self.TYPE_TXT,
            ["dmg_set:20:per:own_bench_counters_type:fighting"])
        self.assertEqual(errs, [])

    def test_type_plain_bench_flagged(self):
        """用全体后备计数会多算别的属性 → 必须拦下"""
        errs = A.audit_counter_subset(
            "Gigalith", "Vengeful Cannon", self.TYPE_TXT,
            ["dmg_set:20:per:own_bench_counters"])
        self.assertTrue(errs)

    def test_named_matches(self):
        errs = A.audit_counter_subset(
            "Cynthia’s Spiritomb", "Raging Curse", self.NAMED_TXT,
            ["dmg_set:10:per:own_bench_counters_named:cynthia"])
        self.assertEqual(errs, [])

    def test_named_plain_bench_flagged(self):
        errs = A.audit_counter_subset(
            "Cynthia’s Spiritomb", "Raging Curse", self.NAMED_TXT,
            ["dmg_set:10:per:own_bench_counters"])
        self.assertTrue(errs)

    def test_field_named_with_damage_matches(self):
        errs = A.audit_counter_subset(
            "Paldean Tauros", "Raging Charge", self.TAUROS_TXT,
            ["dmg_set:40:per:own_field_named_with_damage:tauros"])
        self.assertEqual(errs, [])

    def test_field_named_missing_flagged(self):
        errs = A.audit_counter_subset(
            "Paldean Tauros", "Raging Charge", self.TAUROS_TXT,
            ["dmg_set:40:per:own_field"])
        self.assertTrue(errs)


class TestAuditPrePhase(unittest.TestCase):
    """第二十五批：伤害前阶段（第二十三类对账）"""

    TOOLS = ("Before doing damage, discard all Pokémon Tools from your "
             "opponent’s Active Pokémon.")
    COMBINED = ("Before doing damage, discard all Pokémon Tools and Special Energy "
                "from your opponent’s Active Pokémon.")
    JOLTIK = ("Before doing damage, discard all Pokémon Tools from your opponent’s "
              "Active Pokémon. If you discarded a Pokémon Tool in this way, your "
              "opponent’s Active Pokémon is now Paralyzed.")

    def test_pre_tool_matches(self):
        errs = A.audit_pre_phase("Spearow", "Pluck", self.TOOLS,
                                 ["pre:discard_tool:all:opp_active"])
        self.assertEqual(errs, [])

    def test_post_damage_discard_flagged(self):
        """卡面写了「Before doing damage」，落成伤害后 `discard_tool:` 必须报错"""
        errs = A.audit_pre_phase("Spearow", "Pluck", self.TOOLS,
                                 ["discard_tool:all:opp_active"])
        self.assertTrue(errs, "伤害后弃道具仍被放行")

    def test_special_energy_missing_flagged(self):
        errs = A.audit_pre_phase("Mow Rotom", "Reaping Dash", self.COMBINED,
                                 ["pre:discard_tool:all:opp_active"])
        self.assertTrue(errs, "同时弃特殊能量却缺 pre:discard_energy")

    def test_special_energy_matches(self):
        errs = A.audit_pre_phase(
            "Mow Rotom", "Reaping Dash", self.COMBINED,
            ["pre:discard_tool:all:opp_active",
             "pre:discard_energy:all:opp:special"])
        self.assertEqual(errs, [])

    def test_conditional_status_requires_if_pre_tool(self):
        """「If you discarded a Pokémon Tool in this way」缺后缀 = 无条件麻痹"""
        errs = A.audit_pre_phase(
            "N’s Joltik", "Zapping Short", self.JOLTIK,
            ["pre:discard_tool:all:opp_active", "paralyze"])
        self.assertTrue(errs, "无条件麻痹（静默加强）仍被放行")

    def test_conditional_status_matches(self):
        errs = A.audit_pre_phase(
            "N’s Joltik", "Zapping Short", self.JOLTIK,
            ["pre:discard_tool:all:opp_active", "paralyze:if_pre_tool"])
        self.assertEqual(errs, [])


class TestAuditEnergyToHand(unittest.TestCase):
    """第二十三批：附着能量收回手牌（第二十一类对账）"""

    DESC = "Put an Energy attached to this Pokémon into your hand."
    TYPED = "Put 2 {R} Energy attached to this Pokémon into your hand."

    def test_bounce_matches(self):
        errs = A.audit_energy_to_hand("Landorus", "Screw Knuckle", self.DESC,
                                      ["bounce:energy:self:1"])
        self.assertEqual(errs, [])

    def test_missing_flagged(self):
        errs = A.audit_energy_to_hand("Landorus", "Screw Knuckle", self.DESC, [])
        self.assertTrue(errs, "能量回手缺 tag 仍放行")

    def test_discard_energy_is_not_bounce(self):
        """写成弃置能量（会丢掉可再用的卡）必须被拦下"""
        errs = A.audit_energy_to_hand("Landorus", "Screw Knuckle", self.DESC,
                                      ["discard_energy:1"])
        self.assertTrue(errs)

    def test_typed(self):
        errs = A.audit_energy_to_hand("Volcanion", "Backfire", self.TYPED,
                                      ["bounce:energy:self:2:type:fire"])
        self.assertEqual(errs, [])


class TestAuditPrintingIdentity(unittest.TestCase):
    """第二十一批：印刷身份对账（同一 SET+编号 不得被不同卡名占用）

    实测事故：人工池 Crispin 误写 `SSP 164`，而数据集 SSP 164 是 Brilliant Blender
    → 按编号精确匹配会把名字/效果算到另一张卡头上（静默错映射）。
    """

    MANUAL = [{"name": "Crispin", "real": {"set": "SCR", "number": "133"}}]
    DATASET = [{"name": "Crispin", "series": "SCR", "number": "133"},
               {"name": "Brilliant Blender", "series": "SSP", "number": "164"}]

    def test_consistent_printing_passes(self):
        errs = A.audit_printing_identity(self.MANUAL, self.DATASET)
        self.assertEqual(errs, [])

    def test_wrong_manual_printing_flagged(self):
        manual = [{"name": "Crispin", "real": {"set": "SSP", "number": "164"}}]
        errs = A.audit_printing_identity(manual, self.DATASET)
        self.assertTrue(any("编号撞号" in e for e in errs), errs)

    def test_manual_duplicate_printing_flagged(self):
        manual = [{"name": "A", "real": {"set": "SSP", "number": "1"}},
                  {"name": "B", "real": {"set": "SSP", "number": "1"}}]
        errs = A.audit_printing_identity(manual, [])
        self.assertTrue(any("共用 SSP 1" in e for e in errs), errs)

    def test_naming_variant_is_same_card(self):
        """同卡不同命名（Ogerpon ex ↔ Teal Mask Ogerpon ex）不算撞号"""
        manual = [{"name": "Ogerpon ex", "real": {"set": "TWM", "number": "25"}}]
        dataset = [{"name": "Teal Mask Ogerpon ex", "series": "TWM",
                    "number": "25"}]
        self.assertEqual(A.audit_printing_identity(manual, dataset), [])

    def test_short_name_prefix_not_treated_as_same(self):
        """「Meowth ex」不能被「Meowth」放过（不以后缀结尾）"""
        manual = [{"name": "Meowth ex", "real": {"set": "SSP", "number": "1"}}]
        dataset = [{"name": "Meowth", "series": "SSP", "number": "1"}]
        self.assertTrue(A.audit_printing_identity(manual, dataset))

    def test_dataset_internal_collision_flagged(self):
        dataset = [{"name": "A", "series": "SSP", "number": "1"},
                   {"name": "B", "series": "SSP", "number": "1"}]
        errs = A.audit_printing_identity([], dataset)
        self.assertTrue(any("数据集 SSP 1" in e for e in errs), errs)


if __name__ == "__main__":
    unittest.main()
