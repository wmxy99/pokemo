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


class TestAuditBatch40(unittest.TestCase):
    """第四十批（2026-11-02 · 第三十六类）：连续抛币计数源 / 选 N 只进化 / 任意张检索

    这一族的共同失效模式是「随机张数被折算成常数」——不报错、测试全绿，只是强度
    变了。下列用例逐条证明对账能拦。
    """

    DMG_SET = ("Flip a coin until you get tails. This attack does 40 damage "
               "for each heads.")
    DMG_PLUS = ("Flip a coin until you get tails. This attack does 40 more damage "
                "for each heads.")
    MILL = ("Flip a coin until you get tails. For each heads, discard the top "
            "card of your opponent’s deck.")
    HAND = ("Flip a coin until you get tails. For each heads, choose a random "
            "card from your opponent’s hand. Your opponent reveals those cards "
            "and shuffles them into their deck.")
    SEARCH = ("Flip a coin until you get tails. Search your deck for a number of "
              "cards up to the number of heads and put them into your hand. "
              "Then, shuffle your deck.")
    DISCARD = ("Flip a coin until you get tails. For each heads, discard an Energy "
               "from your opponent’s Active Pokémon.")
    EVOLVE = ("Choose up to 2 of your {D} Pokémon. For each of those Pokémon, "
              "search your deck for a card that evolves from that Pokémon and put "
              "it onto that Pokémon to evolve it. Then, shuffle your deck.")
    ANY_NUM = ("Search your deck for any number of Basic Energy cards of different "
               "types, reveal them, and put them into your hand. Then, shuffle "
               "your deck.")

    # ---- ① 伤害型：more 有无是两种语义 ----
    def test_damage_without_more_needs_set(self):
        self.assertEqual(
            A.audit_batch40("Marill", "Ball Roll", self.DMG_SET,
                            ["coin_until_tails:dmg_set:40"]), [])

    def test_damage_without_more_flattened_to_plus_flagged(self):
        """「does N damage」落成 dmg_plus → 0 正面仍打 N（静默加强）"""
        errs = A.audit_batch40("Marill", "Ball Roll", self.DMG_SET,
                               ["coin_until_tails:dmg_plus:40"])
        self.assertTrue(errs, errs)

    def test_damage_with_more_needs_plus(self):
        self.assertEqual(
            A.audit_batch40("Breloom", "X", self.DMG_PLUS,
                            ["coin_until_tails:dmg_plus:40"]), [])

    def test_damage_with_more_but_set_flagged(self):
        errs = A.audit_batch40("Breloom", "X", self.DMG_PLUS,
                               ["coin_until_tails:dmg_set:40"])
        self.assertTrue(any("dmg_plus" in e for e in errs), errs)

    # ---- ② 非伤害三形态 ----
    def test_non_damage_forms_recognized(self):
        for desc, tag in ((self.MILL, "coin_until_tails:mill:opp"),
                          (self.HAND, "coin_until_tails:shuffle_opp_hand"),
                          (self.SEARCH, "coin_until_tails:search:any"),
                          (self.DISCARD, "coin_until_tails:discard_energy:opp")):
            self.assertEqual(A.audit_batch40("X", "Y", desc, [tag]), [],
                             f"{tag} 被误报")

    def test_mill_folded_to_constant_flagged(self):
        """「for each heads 磨牌」落成 mill:1:opp → 恒定磨 1 张"""
        errs = A.audit_batch40("Diglett", "Relentless Burrowing", self.MILL,
                               ["mill:1:opp"])
        self.assertTrue(any("coin_until_tails:mill:opp" in e for e in errs), errs)

    def test_discard_folded_to_constant_flagged(self):
        errs = A.audit_batch40("LugiaEX", "Hyper Whirlpool", self.DISCARD,
                               ["discard_energy:1:opp"])
        self.assertTrue(any("discard_energy:opp" in e for e in errs), errs)

    def test_unclaimed_until_tails_flagged(self):
        """写了连续掷币却完全没有本族 tag → 张数静默丢失"""
        errs = A.audit_batch40("X", "Y", self.MILL, [])
        self.assertTrue(any("没有" in e for e in errs), errs)

    def test_attach_flip_until_not_flagged(self):
        """Snorlax 的「检索能量并附着」是 attach_flip_until，不属本族"""
        desc = ("Flip a coin until you get tails. Search your deck for an amount "
                "of Basic Energy up to the number of heads and attach it to this "
                "Pokémon. Then, shuffle your deck.")
        self.assertEqual(
            A.audit_batch40("Snorlax", "Gormandizer", desc,
                            ["attach_flip_until:any:deck"]), [])

    # ---- ③ 选 N 只各进化一次 ----
    def test_evolve_each_pick_recognized(self):
        self.assertEqual(
            A.audit_batch40("Nidorina", "Dark Awakening", self.EVOLVE,
                            ["evolve_each_pick:2:type-darkness"]), [])

    def test_evolve_each_pick_missing_flagged(self):
        errs = A.audit_batch40("Nidorina", "Dark Awakening", self.EVOLVE, [])
        self.assertTrue(any("evolve_each_pick" in e for e in errs), errs)

    def test_evolve_each_bench_substitution_flagged(self):
        """拿「全部后备」顶替「至多 2 只」→ 静默加强"""
        errs = A.audit_batch40("Nidorina", "Dark Awakening", self.EVOLVE,
                               ["evolve_each_bench"])
        self.assertTrue(any("全部后备" in e for e in errs), errs)

    def test_evolve_each_pick_wrong_cap_flagged(self):
        errs = A.audit_batch40("Nidorina", "Dark Awakening", self.EVOLVE,
                               ["evolve_each_pick:3:type-darkness"])
        self.assertTrue(any("上限" in e for e in errs), errs)

    def test_attach_each_pick_sentence_not_matched(self):
        """「Choose up to 2 … attach energy」是附着族，不是进化族"""
        desc = ("Choose up to 2 of your {D} Pokémon. For each of those Pokémon, "
                "search your deck for a Basic {D} Energy card and attach it to "
                "that Pokémon. Then, shuffle your deck.")
        self.assertEqual(
            A.audit_batch40("Janine’s Secret Art", "Janine’s Secret Art", desc,
                            ["attach_each_pick:2:1:darkness:deck:"
                             "target:type-darkness"]), [])

    # ---- ④ any number of different types ----
    def test_any_number_distinct_recognized(self):
        self.assertEqual(
            A.audit_batch40("Energy Search Pro", "Energy Search Pro",
                            self.ANY_NUM, ["search_energy:all:distinct"]), [])

    def test_any_number_folded_to_constant_flagged(self):
        errs = A.audit_batch40("Energy Search Pro", "Energy Search Pro",
                               self.ANY_NUM, ["search_energy:3:distinct"])
        self.assertTrue(any("search_energy:all:distinct" in e for e in errs), errs)

    def test_any_number_without_distinct_flagged(self):
        errs = A.audit_batch40("Energy Search Pro", "Energy Search Pro",
                               self.ANY_NUM, ["search_energy:all"])
        self.assertTrue(errs, errs)


class TestAuditBatch41(unittest.TestCase):
    """第四十一批（2026-11-03 · 第三十七类）：{ex} 目标筛选 / 自身能量洗回牌库"""

    EX_EACH = ("This attack does 60 damage to each of your opponent’s Pokémon "
               "{ex}. This attack’s damage isn’t affected by Weakness or "
               "Resistance.")
    EX_BENCH = ("Discard all Energy from this Pokémon, and this attack does 210 "
                "damage to 1 of your opponent’s Benched Pokémon {ex}. (Don’t apply "
                "Weakness and Resistance for Benched Pokémon.)")
    EX_OR_V = ("This attack does 60 damage to 1 of your opponent’s Benched Pokémon "
               "{ex} or Benched Pokémon {V}. (Don’t apply Weakness and Resistance "
               "for Benched Pokémon.)")
    SKARMORY = ("Shuffle all Energy attached to this Pokémon into your deck, and "
                "this attack does 220 damage to 1 of your opponent’s Pokémon. "
                "(Don’t apply Weakness and Resistance for Benched Pokémon.)")

    # ---- ① {ex} 目标 ----
    def test_ex_target_recognized(self):
        self.assertEqual(
            A.audit_extra_damage("Vaporeon ex", "Severe Squall", self.EX_EACH,
                                 ["snipe:opp:60:all:target:ex"]), [])

    def test_ex_target_missing_flagged(self):
        """缺 `:target:ex` → 会打非 ex 目标（静默超范围）"""
        errs = A.audit_extra_damage("Vaporeon ex", "Severe Squall", self.EX_EACH,
                                    ["snipe:opp:60:all"])
        self.assertTrue(any(":target:ex" in e for e in errs), errs)

    def test_benched_ex_target_recognized(self):
        self.assertEqual(
            A.audit_extra_damage("Zeraora", "Thunder Raid", self.EX_BENCH,
                                 ["snipe:opp_bench:210:1:target:ex"]), [])

    def test_ex_or_v_requires_target_ex(self):
        """与 {V} 混写：第九十八批起可表达（`:target:ex`），但**缺它必须报错**

        措辞从「不可精确表达」变成「缺 `:target:ex`」—— 判定本身没变：
        卡面限定了 ex，落成不带筛选的 snipe 就会打非 ex 目标（静默超范围）。
        """
        errs = A.audit_extra_damage("Shaymin", "Pinpoint Dive", self.EX_OR_V,
                                    ["snipe:opp_bench:60:1"])
        self.assertTrue(any(":target:ex" in e or "不可精确表达" in e
                            for e in errs), errs)

    def test_ex_or_v_with_target_ex_ok(self):
        self.assertEqual(
            A.audit_extra_damage("Shaymin", "Pinpoint Dive", self.EX_OR_V,
                                 ["snipe:opp_bench:60:1:target:ex"]), [])

    def test_no_ex_qualifier_plain_target(self):
        """没有 {ex} 限定 → 普通 snipe 不应被要求 `:target:ex`"""
        desc = "This attack does 60 damage to each of your opponent’s Pokémon."
        self.assertEqual(
            A.audit_extra_damage("X", "Y", desc, ["snipe:opp:60:all"]), [])

    # ---- ② 自身能量洗回牌库（强制代价，双向核对）----
    def test_energy_self_to_deck_recognized(self):
        self.assertEqual(
            A.audit_energy_self_to_deck("Mega Skarmory ex", "Sonic Ripper",
                                        self.SKARMORY,
                                        ["snipe:opp:220:1",
                                         "energy_self_to_deck:all"]), [])

    def test_cost_missing_flagged(self):
        """只建模伤害、不洗回能量 → 「不付代价白打」"""
        errs = A.audit_energy_self_to_deck("Mega Skarmory ex", "Sonic Ripper",
                                           self.SKARMORY, ["snipe:opp:220:1"])
        self.assertTrue(any("energy_self_to_deck" in e for e in errs), errs)

    def test_tag_without_sentence_flagged(self):
        """卡面没写该句却带 tag → 凭空削掉自己全部能量（认领越界）"""
        desc = "This attack does 220 damage to 1 of your opponent’s Pokémon."
        errs = A.audit_energy_self_to_deck("X", "Y", desc,
                                           ["energy_self_to_deck:all"])
        self.assertTrue(any("认领越界" in e for e in errs), errs)


class TestAuditBatch42(unittest.TestCase):
    """第四十二批（2026-11-03 · 第三十八类）：对手宝可梦连同附着卡洗回牌库"""

    SYLVEON = ("Flip a coin. If heads, choose 1 of your opponent’s Benched Pokémon. "
               "Shuffle that Pokémon and all attached cards into their deck.")
    SHIFTRY = ("Flip a coin. If heads, choose 1 of your opponent’s Pokémon. "
               "Shuffle that Pokémon and all attached cards into their deck.")

    def test_benched_scope_recognized(self):
        self.assertEqual(
            A.audit_shuffle_opp_pokemon(
                "Sylveon", "Ribbon", self.SYLVEON,
                ["coin:heads:shuffle_opp_pokemon:opp_bench"]), [])

    def test_any_scope_recognized(self):
        self.assertEqual(
            A.audit_shuffle_opp_pokemon(
                "Shiftry", "X", self.SHIFTRY,
                ["coin:heads:shuffle_opp_pokemon:opp"]), [])

    def test_scope_mismatch_flagged(self):
        """Benched 限定却落成 `:opp` → 战斗位也会进候选集合（打错对象）"""
        errs = A.audit_shuffle_opp_pokemon(
            "Sylveon", "Ribbon", self.SYLVEON,
            ["coin:heads:shuffle_opp_pokemon:opp"])
        self.assertTrue(any("opp_bench" in e for e in errs), errs)

    def test_missing_tag_flagged(self):
        """卡面有该句却没有任何 tag → 打出去什么都不发生"""
        errs = A.audit_shuffle_opp_pokemon("Sylveon", "Ribbon", self.SYLVEON, [])
        self.assertTrue(any("无该 tag" in e for e in errs), errs)

    def test_tag_without_sentence_flagged(self):
        desc = "Flip a coin. If heads, this attack does 30 more damage."
        errs = A.audit_shuffle_opp_pokemon(
            "X", "Y", desc, ["coin:heads:shuffle_opp_pokemon:opp"])
        self.assertTrue(any("认领越界" in e for e in errs), errs)


class TestAuditBatch43(unittest.TestCase):
    """第四十三批（2026-11-03）：Ancient 弃牌堆计数 / 具名阵营能量计数"""

    ANCIENT = ("This attack does 10 more damage for each Ancient card in your "
               "discard pile.")
    IONO = ("This attack does 20 more damage for each {L} Energy attached to all "
            "of your Iono’s Pokémon.")

    def test_ancient_category_recognized(self):
        self.assertEqual(
            A.audit_counter_subset("Roaring Moon", "X", self.ANCIENT,
                                   ["dmg_plus:10:per:own_discard_category:ancient"]),
            [])

    def test_ancient_as_card_name_flagged(self):
        """Ancient 不是卡名 → `own_discard_named:ancient` 恒为 0（静默少算）"""
        errs = A.audit_counter_subset("Roaring Moon", "X", self.ANCIENT,
                                      ["dmg_plus:10:per:own_discard_named:ancient"])
        self.assertTrue(any("own_discard_category:ancient" in e for e in errs), errs)

    def test_named_energy_recognized(self):
        self.assertEqual(
            A.audit_counter_subset("Iono’s Voltorb", "X", self.IONO,
                                   ["dmg_plus:20:per:own_field_named_energy:"
                                    "iono:lightning"]), [])

    def test_named_energy_missing_flagged(self):
        """落成全队能量（无具名限定）→ 会把别的宝可梦的能量也算进来"""
        errs = A.audit_counter_subset("Iono’s Voltorb", "X", self.IONO,
                                      ["dmg_plus:20:per:own_field_energy_lightning"])
        self.assertTrue(any("own_field_named_energy" in e for e in errs), errs)


class TestAuditBatch44(unittest.TestCase):
    """第四十四批（2026-11-03）：同一枚币的双分支 / 抛币子效果对账"""

    VENTURE_BOMB = ("Flip a coin. If heads, put 2 damage counters on 1 of your "
                    "opponent’s Pokémon. If tails, put 2 damage counters on your "
                    "Active Pokémon.")
    DUSTOX = ("Once during your turn, you may use this Ability. Flip a coin. "
              "If heads, put an Energy attached to your opponent’s Active Pokémon "
              "into their hand.")
    FORK = ("coin_flip:heads:counters:2:scope:opp:1"
            "|tails:counters:2:scope:self")

    def test_fork_single_tag_recognized(self):
        self.assertEqual(
            A.audit_coin_forms("Team Rocket's Venture Bomb", "X",
                               self.VENTURE_BOMB, [self.FORK]), [])

    def test_split_branches_flagged(self):
        """拆成两条 `coin:` = 抛两次（可能两个分支同时生效/都不生效）"""
        errs = A.audit_coin_forms(
            "Team Rocket's Venture Bomb", "X", self.VENTURE_BOMB,
            ["coin:heads:counters:2:scope:opp:1",
             "coin:tails:counters:2:scope:self"])
        self.assertTrue(any("coin_flip:" in e for e in errs), errs)

    def test_counters_recognized_through_coin(self):
        self.assertEqual(
            A.audit_extra_damage("Team Rocket's Venture Bomb", "X",
                                 self.VENTURE_BOMB, [self.FORK]), [])

    def test_counters_missing_flagged(self):
        errs = A.audit_extra_damage("Team Rocket's Venture Bomb", "X",
                                    self.VENTURE_BOMB, [])
        self.assertTrue(any("counters:" in e for e in errs), errs)

    def test_energy_bounce_recognized_through_coin(self):
        self.assertEqual(
            A.audit_energy_to_hand("Dustox", "Boisterous Wind", self.DUSTOX,
                                   ["coin:heads:bounce:energy:opp_active:1"]), [])

    def test_energy_bounce_missing_flagged(self):
        errs = A.audit_energy_to_hand("Dustox", "Boisterous Wind", self.DUSTOX,
                                      ["coin:heads:draw:1"])
        self.assertTrue(any("bounce:energy:" in e for e in errs), errs)


class TestAuditBatch45(unittest.TestCase):
    """第四十五批（2026-11-03）：逐目标抛币 / 抛币选状态 / 抛币免伤 / 洗回分抽"""

    ZYGARDE = ("For each of your opponent’s Pokémon, flip a coin. If heads, this "
               "attack does 150 damage to that Pokémon. (Don’t apply Weakness and "
               "Resistance for Benched Pokémon.)")
    CRADILY = ("Once during your turn, you may flip a coin. If heads, choose "
               "Burned, Confused, or Poisoned. Your opponent’s Active Pokémon is "
               "now affected by that Special Condition.")
    FEZANDIPITI = ("If this Pokémon has any {D} Energy attached and is damaged by "
                   "an attack, flip a coin. If heads, prevent that damage.")
    HARLEQUIN = ("Each player shuffles their hand into their deck. Then, flip a "
                 "coin. If heads, you draw 5 cards, and your opponent draws 3 cards. "
                 "If tails, you draw 3 cards, and your opponent draws 5 cards.")

    # ---- 逐目标抛币 ----
    def test_per_target_recognized(self):
        self.assertEqual(
            A.audit_coin_forms("Mega Zygarde ex", "Nullifying Zero",
                               self.ZYGARDE,
                               ["coin_per_target:150:scope:opp"]), [])
        self.assertEqual(
            A.audit_extra_damage("Mega Zygarde ex", "Nullifying Zero",
                                 self.ZYGARDE,
                                 ["coin_per_target:150:scope:opp"]), [])

    def test_per_target_folded_to_snipe_flagged(self):
        """落成 `snipe:opp:150:all` = 每只必中（静默加强）"""
        errs = A.audit_coin_forms(
            "Mega Zygarde ex", "Nullifying Zero", self.ZYGARDE,
            ["snipe:opp:150:all"])
        self.assertTrue(any("coin_per_target" in e for e in errs), errs)

    def test_per_target_missing_flags_damage(self):
        errs = A.audit_extra_damage(
            "Mega Zygarde ex", "Nullifying Zero", self.ZYGARDE, [])
        self.assertTrue(any("不可精确表达" in e for e in errs), errs)

    # ---- 抛币选状态 ----
    def test_status_choose_recognized(self):
        self.assertEqual(
            A.audit_coin_forms("Cradily", "Selective Slime", self.CRADILY,
                               ["coin:heads:status_choose:"
                                "burn+confuse+poison"]), [])

    def test_status_choose_split_flagged(self):
        """拆成三条状态 tag = 三种状态全中"""
        errs = A.audit_coin_forms(
            "Cradily", "Selective Slime", self.CRADILY,
            ["coin:heads:burn", "coin:heads:confuse", "coin:heads:poison"])
        self.assertTrue(any("status_choose" in e for e in errs), errs)

    # ---- 抛币免伤 ----
    def test_coin_prevent_recognized(self):
        self.assertEqual(
            A.audit_coin_forms("Fezandipiti", "Adrena-Pheromone",
                               self.FEZANDIPITI,
                               ["passive:coin_prevent:darkness"]), [])

    def test_coin_prevent_post_damage_flagged(self):
        """落成 `on_damaged:` 时伤害已加上，事后无法撤销"""
        errs = A.audit_coin_forms(
            "Fezandipiti", "Adrena-Pheromone", self.FEZANDIPITI,
            ["on_damaged:counter:0"])
        self.assertTrue(any("coin_prevent" in e for e in errs), errs)

    # ---- 双方洗回 + 抛币分抽 ----
    def test_shuffle_both_recognized(self):
        self.assertEqual(
            A.audit_coin_forms("Harlequin", "Harlequin", self.HARLEQUIN,
                               ["shuffle_both_coin_draw:5:3:3:5"]), [])

    def test_shuffle_both_counts_mismatch_flagged(self):
        """张数写错（把反面也写成 5/3）→ 静默改强度"""
        errs = A.audit_coin_forms(
            "Harlequin", "Harlequin", self.HARLEQUIN,
            ["shuffle_both_coin_draw:5:3:5:3"])
        self.assertTrue(any("shuffle_both_coin_draw" in e for e in errs), errs)


class TestAuditBatch46(unittest.TestCase):
    """第四十六批（2026-11-03）：全队限时减伤 / 单体限时护盾 / 常驻减伤 nonstack"""

    AURORUS = ("All of your Pokémon that have any {W} Energy attached take 50 less "
               "damage from attacks from your opponent’s Pokémon (after applying "
               "Weakness and Resistance). The effect of Tundra Wall doesn't stack.")
    IRON = ("During your opponent’s next turn, all of your {M} Pokémon take 30 "
            "less damage from attacks from your opponent’s Pokémon (after "
            "applying Weakness and Resistance). (This includes new Pokémon that "
            "come into play.)")
    ACEROLA = ("You can use this card only if your opponent has 2 or fewer Prize "
               "cards remaining. Choose 1 of your Pokémon in play. During your "
               "opponent’s next turn, prevent all damage from and effects of "
               "attacks done to that Pokémon by your opponent’s Pokémon {ex}.")
    ACEROLA_TAG = ("shield_choose:prevent_all:from:ex:effects:"
                   "gate:opp_prizes_le:2")

    # ---- 全队常驻减伤（Aurorus）----
    def test_aurorus_recognized(self):
        self.assertEqual(
            A.audit_dmg_layer("Aurorus", "Tundra Wall", self.AURORUS,
                              ["fx:team_reduce:50:energy:water:nonstack"]), [])

    def test_missing_nonstack_flagged(self):
        """卡面写明 doesn't stack 却漏标 `:nonstack` → 多只同族在场重复减伤"""
        errs = A.audit_dmg_layer("Aurorus", "Tundra Wall", self.AURORUS,
                                 ["fx:team_reduce:50:energy:water"])
        self.assertTrue(any("nonstack" in e for e in errs), errs)

    # ---- 全队限时减伤（Iron Defender）----
    def test_iron_defender_recognized(self):
        self.assertEqual(
            A.audit_dmg_layer("Iron Defender", "Iron Defender", self.IRON,
                              ["team_shield:reduce:30:type:metal"]), [])

    def test_iron_defender_as_persistent_flagged(self):
        """落成 `fx:team_reduce` = 永久减伤（时限制被静默放大）"""
        errs = A.audit_dmg_layer("Iron Defender", "Iron Defender", self.IRON,
                                 ["fx:team_reduce:30:type:metal"])
        self.assertTrue(any("team_shield" in e for e in errs), errs)

    # ---- 单体限时护盾（Acerola's Mischief）----
    def test_acerola_recognized(self):
        self.assertEqual(
            A.audit_dmg_layer("Acerola's Mischief", "Acerola's Mischief",
                              self.ACEROLA, [self.ACEROLA_TAG]), [])

    def test_acerola_partial_tag_flagged(self):
        """缺 gate / 缺 effects / 换来源 → 可用性或护盾范围被静默改变"""
        for bad in ("shield_choose:prevent_all:from:ex",
                    "shield_choose:prevent_all:from:ex:effects",
                    "shield_choose:prevent_all:from:basic_ex:effects:"
                    "gate:opp_prizes_le:2"):
            errs = A.audit_dmg_layer("Acerola's Mischief", "Acerola's Mischief",
                                     self.ACEROLA, [bad])
            self.assertTrue(any("shield_choose" in e for e in errs), (bad, errs))


class TestAuditBatch47(unittest.TestCase):
    """第四十七批（2026-11-03）：可选加伤 + 反伤 / 抛币选状态全集"""

    GURDURR = ("You may do 30 more damage. If you do, this Pokémon also does 30 "
               "damage to itself.")
    GRAFAIAI = ("Flip a coin. If heads, choose a Special Condition. Your "
                "opponent’s Active Pokémon is now affected by that Special "
                "Condition.")

    def test_optional_recoil_recognized(self):
        self.assertEqual(
            A.audit_extra_damage("Gurdurr", "Superpower", self.GURDURR,
                                 ["dmg_plus:30:recoil:30"]), [])

    def test_optional_recoil_missing_flagged(self):
        errs = A.audit_extra_damage("Gurdurr", "Superpower", self.GURDURR, [])
        self.assertTrue(any("dmg_plus:30:recoil:30" in e for e in errs), errs)

    def test_optional_recoil_split_flagged(self):
        """拆成 `dmg_plus` + `self` = 丢掉「If you do」联动"""
        errs = A.audit_extra_damage("Gurdurr", "Superpower", self.GURDURR,
                                    ["dmg_plus:30", "self:30"])
        self.assertTrue(any("dmg_plus:30:recoil:30" in e for e in errs), errs)

    def test_plain_recoil_still_self(self):
        """无「You may」的纯反伤仍由 `self:M` 承载（不误报为联动 tag）"""
        self.assertEqual(
            A.audit_extra_damage(
                "Test", "Take Down",
                "This Pokémon also does 30 damage to itself.", ["self:30"]), [])

    def test_full_status_set_recognized(self):
        self.assertEqual(
            A.audit_coin_forms(
                "Grafaiai", "Miraculous Paint", self.GRAFAIAI,
                ["coin:heads:status_choose:"
                 "poison+burn+sleep+paralyze+confuse"]), [])

    def test_partial_status_set_flagged(self):
        """只列三种状态 = 静默缩小选择范围"""
        errs = A.audit_coin_forms(
            "Grafaiai", "Miraculous Paint", self.GRAFAIAI,
            ["coin:heads:status_choose:burn+confuse+poison"])
        self.assertTrue(any("status_choose" in e for e in errs), errs)


class TestAuditBatch78(unittest.TestCase):
    """第七十八批（2026-11-03）：抛币免击倒"""

    TEXT = ("If this Pokémon would be Knocked Out by damage from an attack, flip a "
            "coin. If heads, this Pokémon is not Knocked Out, and its remaining HP "
            "becomes 10.")

    def test_recognized(self):
        self.assertEqual(
            A.audit_coin_forms("Mega Hawlucha ex", "Tenacious Body",
                               self.TEXT, ["passive:coin_survive:10"]), [])

    def test_tool_tag_flagged(self):
        """道具 `tool:survive` 要求满 HP、不抛币 → 不能冒充这张特性"""
        errs = A.audit_coin_forms("Mega Hawlucha ex", "Tenacious Body",
                                  self.TEXT, ["tool:survive:10"])
        self.assertTrue(any("coin_survive" in e for e in errs), errs)


if __name__ == "__main__":
    unittest.main()


class TestAuditBatch53(unittest.TestCase):
    """第五十三批对账的**负向自测**：六族新 tag + 特性发动代价

    每组都覆盖「正确 → 不报」与「静默削弱 → 必须报」两侧；只测正向等于橡皮图章。
    """

    KPM = ("If this Pokémon is Knocked Out by damage from an attack from your "
           "opponent’s Pokémon, and if you have any Pecharunt {ex} in play, your "
           "opponent takes 1 fewer Prize card.")
    KPM_TEAM = ("If 1 of your {D} Pokémon is Knocked Out by damage from an attack "
                "from your opponent’s Pokémon {ex}, that player takes 1 fewer "
                "Prize card.")
    PAIR = "Your opponent’s Active Pokémon is now Burned and Poisoned."
    FORK = ("Flip a coin. If heads, your opponent’s Active Pokémon is now "
            "Paralyzed and Poisoned. If tails, your opponent’s Active Pokémon "
            "is now Confused.")
    SWITCH_DRAW = ("Switch your Active Pokémon with 1 of your Benched Pokémon. "
                   "If you do, draw cards until you have 5 cards in your hand.")
    GUST = ("Switch in 1 of your opponent’s Benched Basic Pokémon to the Active "
            "Spot. If you do, the new Active Pokémon is now Confused.")

    def test_ko_prize_minus_ok(self):
        self.assertEqual(A.audit_batch53(
            "Munkidori ex", "Oh No You Don’t", self.KPM,
            ["ko_prize_minus:1:if_named:pecharunt"]), [])

    def test_ko_prize_minus_missing_flagged(self):
        errs = A.audit_batch53("Munkidori ex", "Oh No You Don’t", self.KPM, [])
        self.assertTrue(any("ko_prize_minus:1:if_named:pecharunt" in e
                            for e in errs), errs)

    def test_ko_prize_minus_number_flagged(self):
        """少给 2 张写成 1 张 = 量纲错（奖赏数是胜负量纲）"""
        errs = A.audit_batch53(
            "X", "Y",
            self.KPM.replace("1 fewer", "2 fewer"),
            ["ko_prize_minus:1:if_named:pecharunt"])
        self.assertTrue(any("fewer Prize card" in e for e in errs), errs)

    def test_ko_prize_minus_team_ok(self):
        self.assertEqual(A.audit_batch53(
            "Mega Gengar ex", "Shadowy Concealment", self.KPM_TEAM,
            ["ko_prize_minus:1:from_ex:team_darkness"]), [])

    def test_ko_prize_minus_team_missing_flagged(self):
        """丢掉同队限定 = 全队都能少给奖赏（静默加强）"""
        errs = A.audit_batch53("Mega Gengar ex", "Shadowy Concealment",
                               self.KPM_TEAM, ["ko_prize_minus:1:from_ex"])
        self.assertTrue(any("team_darkness" in e for e in errs), errs)

    def test_status_multi_ok(self):
        self.assertEqual(A.audit_batch53("Drapion", "Hazardous Tail", self.PAIR,
                                         ["status_multi:burn+poison"]), [])

    def test_status_multi_embedded_in_coin_ok(self):
        """双状态嵌在抛币子效果里也算（Lilligant 形态）"""
        self.assertEqual(A.audit_batch53(
            "Lilligant", "Bemusing Aroma", self.FORK,
            ["coin_each:1:heads:status_multi:paralyze+poison"
             "|cond:all_tails:confuse"]), [])

    def test_single_status_flagged(self):
        errs = A.audit_batch53("Drapion", "Hazardous Tail", self.PAIR, ["burn"])
        self.assertTrue(any("status_multi:burn+poison" in e for e in errs), errs)

    def test_switch_draw_to_ok(self):
        self.assertEqual(A.audit_batch53("Surfer", "Surfer", self.SWITCH_DRAW,
                                         ["switch_draw_to:5"]), [])

    def test_switch_draw_split_flagged(self):
        """拆成 switch + draw:5 = 「补到 5 张」变成「多抽 5 张」"""
        errs = A.audit_batch53("Surfer", "Surfer", self.SWITCH_DRAW,
                               ["switch_self", "draw:5"])
        self.assertTrue(any("switch_draw_to:5" in e for e in errs), errs)

    def test_gust_confuse_ok(self):
        self.assertEqual(A.audit_batch53("Lisia’s Appeal", "Lisia’s Appeal",
                                         self.GUST, ["gust_confuse"]), [])

    def test_bare_gust_flagged(self):
        errs = A.audit_batch53("Lisia’s Appeal", "Lisia’s Appeal", self.GUST,
                               ["gust"])
        self.assertTrue(any("gust_confuse" in e for e in errs), errs)

    def test_research_ok(self):
        self.assertEqual(A.audit_batch53(
            "Tapu Koko", "Fast Flight",
            "If you go first, you can use this attack during your first turn. "
            "Discard your hand and draw 5 cards.", ["research:5"]), [])

    def test_research_split_flagged(self):
        errs = A.audit_batch53(
            "Tapu Koko", "Fast Flight",
            "Discard your hand and draw 5 cards.", ["discard:all", "draw:5"])
        self.assertTrue(any("research:5" in e for e in errs), errs)

    def test_ability_cost_ok(self):
        desc = ("You must discard a Basic {L} Energy from this Pokémon in order "
                "to use this Ability. Once during your turn, you may draw cards "
                "until you have 6 cards in your hand.")
        self.assertEqual(A.audit_batch53("Iono’s Kilowattrel", "Flashing Draw",
                                         desc, ["draw_to:6"],
                                         cost="discard_self_energy:1:lightning"),
                         [])

    def test_ability_cost_empty_flagged(self):
        """漏了 cost = 特性**免费发动**（比不建模更糟）"""
        desc = ("You must discard a card from your hand in order to use this "
                "Ability. Once during your turn, you may draw 2 cards.")
        errs = A.audit_batch53("N’s Zoroark ex", "Trade", desc, ["draw:2"],
                               cost="")
        self.assertTrue(any("免费发动" in e for e in errs), errs)

    def test_ability_cost_overclaim_flagged(self):
        """卡面无代价句却写了 cost → 认领越界"""
        errs = A.audit_batch53("X", "Y", "Draw 2 cards.", ["draw:2"],
                               cost="discard:1")
        self.assertTrue(any("认领越界" in e for e in errs), errs)


class TestAuditBatch63_67(unittest.TestCase):
    """第六十三~六十七批对账的**负向自测**（第四十一类）

    五族各测两侧：正确映射不报、静默错映射必报。尤其 `ignore_effects` 两档
    反口径 —— 用窄口径接宽句式会静默漏掉「无视弱点/抗性」那一半。
    """

    B63 = "Discard an Energy from your opponent’s Active Pokémon. " \
          "During your next turn, this Pokémon can’t attack."
    B63_NAMED = "During your next turn, this Pokémon can’t use Haymaker."
    B64 = ("Your opponent chooses 3 cards from their hand and shuffles "
           "those cards into their deck.")
    B65_WR = ("This attack’s damage isn’t affected by Weakness or "
              "Resistance, or by any effects on your opponent’s Active "
              "Pokémon.")
    B65_NARROW = ("This attack’s damage isn’t affected by any effects on "
                  "your opponent’s Active Pokémon.")
    B66 = ("Attacks used by this Pokémon cost {C} less for each Kofu "
           "card in your discard pile.")
    KOFU_TAG = "fx:attack_cost:self:less:per:own_discard_named:kofu"
    B67_JYNX = "move an Energy from this Pokémon to the new Benched Pokémon."
    B67_AZU = "Move an Energy from 1 of your other Pokémon to this Pokémon."

    # ---- ① 自身跨回合禁攻 ----
    def test_b63_ok(self):
        self.assertEqual(A.audit_batch63_67(
            "Floatzel", "Aqua Slash", self.B63,
            ["discard_energy:1:opp", "attack_lock:all:turn"]), [])

    def test_b63_missing_flagged(self):
        errs = A.audit_batch63_67("Floatzel", "Aqua Slash", self.B63,
                                  ["discard_energy:1:opp"])
        self.assertTrue(any("attack_lock:all:turn" in e for e in errs), errs)

    def test_b63_named_lock_written_as_all_flagged(self):
        """只锁 Haymaker 却落 all：整只宝可梦一回合不能出招（静默加强）"""
        errs = A.audit_batch63_67("Crabominable", "Haymaker", self.B63_NAMED,
                                  ["attack_lock:all:turn"])
        self.assertTrue(any("静默加强" in e for e in errs), errs)

    # ---- ② 对手自选洗回牌库 ----
    def test_b64_ok(self):
        self.assertEqual(A.audit_batch63_67(
            "Banette", "Cursed Words", self.B64, ["shuffle_opp_hand:3"]), [])

    def test_b64_wrong_count_flagged(self):
        errs = A.audit_batch63_67("Banette", "Cursed Words", self.B64,
                                  ["shuffle_opp_hand:2"])
        self.assertTrue(any("shuffle_opp_hand:3" in e for e in errs), errs)

    def test_b64_missing_flagged(self):
        self.assertTrue(A.audit_batch63_67("Banette", "Cursed Words",
                                           self.B64, []))

    # ---- ③ 无视效果两档反口径 ----
    def test_b65_wr_ok(self):
        self.assertEqual(A.audit_batch63_67(
            "Mega Starmie ex", "Jetting Blow", self.B65_WR,
            ["ignore_effects:wr"]), [])

    def test_b65_wr_folded_to_narrow_flagged(self):
        """宽句式用窄 tag → 弱点/抗性照常结算，静默漏掉一半效果"""
        errs = A.audit_batch63_67("Mega Starmie ex", "Jetting Blow",
                                  self.B65_WR, ["ignore_effects"])
        self.assertTrue(any("ignore_effects:wr" in e for e in errs), errs)

    def test_b65_narrow_ok(self):
        self.assertEqual(A.audit_batch63_67(
            "Veluza", "Sonic Edge", self.B65_NARROW, ["ignore_effects"]), [])

    def test_b65_narrow_widened_flagged(self):
        """只写 any effects 却带 :wr → 连弱抗一起无视（静默加强）"""
        errs = A.audit_batch63_67("Veluza", "Sonic Edge", self.B65_NARROW,
                                  ["ignore_effects:wr"])
        self.assertTrue(any("静默加强" in e for e in errs), errs)

    def test_b65_tag_without_sentence_flagged(self):
        errs = A.audit_batch63_67("X", "Y", "Draw 2 cards.",
                                  ["ignore_effects:wr"])
        self.assertTrue(any("认领越界" in e for e in errs), errs)

    # ---- ④ Kofu 计数源减费 ----
    def test_b66_ok(self):
        self.assertEqual(A.audit_batch63_67(
            "Veluza", "Food Prep", self.B66, [self.KOFU_TAG]), [])

    def test_b66_wrong_counter_flagged(self):
        errs = A.audit_batch63_67(
            "Veluza", "Food Prep", self.B66,
            ["fx:attack_cost:self:less:per:opp_bench"])
        self.assertTrue(any("own_discard_named:kofu" in e for e in errs), errs)

    def test_b66_tag_without_sentence_flagged(self):
        errs = A.audit_batch63_67("X", "Y", "Draw 2 cards.", [self.KOFU_TAG])
        self.assertTrue(any("认领越界" in e for e in errs), errs)

    # ---- ⑤ 能量移动区域 ----
    def test_b67_jynx_ok(self):
        self.assertEqual(A.audit_batch63_67(
            "Jynx", "X", self.B67_JYNX,
            ["energy_move:1:from:active:to:bench"]), [])

    def test_b67_jynx_faction_flipped_flagged(self):
        """己方新后备写成 opp_bench → 能量搬到对手场上（阵营反转）"""
        errs = A.audit_batch63_67(
            "Jynx", "X", self.B67_JYNX,
            ["energy_move:1:from:active:to:opp_bench"])
        self.assertTrue(any("to:bench" in e for e in errs), errs)

    def test_b67_azumarill_ok(self):
        self.assertEqual(A.audit_batch63_67(
            "Azumarill ex", "X", self.B67_AZU,
            ["energy_move:1:from:any:to:active"]), [])

    def test_b67_azumarill_wrong_src_flagged(self):
        errs = A.audit_batch63_67(
            "Azumarill ex", "X", self.B67_AZU,
            ["energy_move:1:from:active:to:active"])
        self.assertTrue(any("from:any" in e for e in errs), errs)


class TestAuditBatch68_77(unittest.TestCase):
    """第六十八~七十七批对账的**负向自测**（正确→不报 + 错误→必报）

    ⚠ 69 / 72 是**负向守卫**：阵营限定 +HP 与复合指示物句都属「引擎无法精确
    表达 → 必须整卡拒收」，所以它们在池里出现**带 tag** 才是错误（会把
    「只给某阵营加血」放大成「谁都加」、把「部分实现」冒充成「已实现」）。
    """

    def test_b68_judge_ok(self):
        self.assertEqual(A.audit_batch68_77(
            "Judge", "Judge", "Each player shuffles their hand into their "
            "deck and draws 4 cards.", ["shuffle_draw_split:4:4"]), [])

    def test_b68_judge_missing_flagged(self):
        errs = A.audit_batch68_77(
            "Judge", "Judge", "Each player shuffles their hand into their "
            "deck and draws 4 cards.", [])
        self.assertTrue(any("shuffle_draw_split:4:4" in e for e in errs), errs)

    # ---- 69：阵营限定 +HP（负向守卫）----
    def test_b69_faction_tagged_flagged(self):
        """阵营限定 +HP 落 `tool:hp_plus` → 会给任意持有者加血（静默加强）"""
        errs = A.audit_batch68_77(
            "Cynthia's Power Weight", "X",
            "The Cynthia’s Pokémon this card is attached to gets +70 HP.",
            ["tool:hp_plus:70"])
        self.assertTrue(any("阵营限定" in e for e in errs), errs)

    def test_b69_faction_refused_ok(self):
        """如实拒收（无 tag）→ 不报错"""
        self.assertEqual(A.audit_batch68_77(
            "Cynthia's Power Weight", "X",
            "The Cynthia’s Pokémon this card is attached to gets +70 HP.",
            []), [])

    def test_b69_plain_tagged_ok(self):
        """无阵营限定 +HP → tag 正确"""
        self.assertEqual(A.audit_batch68_77(
            "Plain Tool", "X",
            "The Pokémon this card is attached to gets +30 HP.",
            ["tool:hp_plus:30"]), [])

    def test_b69_plain_missing_flagged(self):
        errs = A.audit_batch68_77(
            "Plain Tool", "X",
            "The Pokémon this card is attached to gets +30 HP.", [])
        self.assertTrue(any("tool:hp_plus:30" in e for e in errs), errs)

    def test_b70_recycle_ok(self):
        self.assertEqual(A.audit_batch68_77(
            "Energy Recycler", "X",
            "Shuffle up to 5 Basic Energy cards from your discard pile into "
            "your deck.", ["energy_to_deck:any:5"]), [])

    def test_b70_recycle_missing_flagged(self):
        errs = A.audit_batch68_77(
            "Energy Recycler", "X",
            "Shuffle up to 5 Basic Energy cards from your discard pile into "
            "your deck.", [])
        self.assertTrue(any("energy_to_deck:any:5" in e for e in errs), errs)

    def test_b71_counters_defending_ok(self):
        self.assertEqual(A.audit_batch68_77(
            "Glaceon", "Permeating Chill",
            "At the end of your opponent’s next turn, put 9 damage counters "
            "on the Defending Pokémon.", ["counters:9"]), [])

    def test_b71_counters_defending_wrong_flagged(self):
        """指示物误走 snipe（会吃弱抗）→ 必须抓出"""
        errs = A.audit_batch68_77(
            "Glaceon", "Permeating Chill",
            "At the end of your opponent’s next turn, put 9 damage counters "
            "on the Defending Pokémon.", ["snipe:opp_active:90"])
        self.assertTrue(errs, "指示物误走 snipe 却放行")

    # ---- 72：复合指示物句（负向守卫）----
    def test_b72_clean_tagged_ok(self):
        """干净单句 → `:scope:self` 正确"""
        self.assertEqual(A.audit_batch68_77(
            "Clean On-This", "X",
            "Put 5 damage counters on this Pokémon.",
            ["counters:5:scope:self"]), [])

    def test_b72_clean_missing_flagged(self):
        errs = A.audit_batch68_77(
            "Clean On-This", "X",
            "Put 5 damage counters on this Pokémon.", [])
        self.assertTrue(any("counters:5:scope:self" in e for e in errs), errs)

    def test_b72_compound_tagged_flagged(self):
        """复合句（后接 +120）仍落 tag → 部分实现冒充已实现"""
        errs = A.audit_batch68_77(
            "Feraligatr", "Torrential Heart",
            "Once during your turn, you may put 5 damage counters on this "
            "Pokémon. If you do, during this turn, attacks used by this "
            "Pokémon do 120 more damage.", ["counters:5:scope:self"])
        self.assertTrue(any("部分实现冒充已实现" in e for e in errs), errs)

    def test_b72_compound_refused_ok(self):
        self.assertEqual(A.audit_batch68_77(
            "Feraligatr", "Torrential Heart",
            "Once during your turn, you may put 5 damage counters on this "
            "Pokémon. If you do, during this turn, attacks used by this "
            "Pokémon do 120 more damage.", []), [])

    def test_b72_embedded_coin_ok(self):
        """Venture Bomb 的 tails 分支把 scope:self 嵌进 coin_flip → 不算错"""
        self.assertEqual(A.audit_batch68_77(
            "Team Rocket's Venture Bomb", "X",
            "Flip a coin. If tails, put 2 damage counters on this Pokémon.",
            ["coin_flip:heads:counters:2:scope:opp:1|"
             "tails:counters:2:scope:self"]), [])


class TestAuditBatch79(unittest.TestCase):
    """第七十九批（2026-11-05）：搜索族对账（第四十三类）"""

    ROTO = ("Look at the top 4 cards of your deck. You may reveal any number of "
            "Supporter cards you find there and put them into your hand. Shuffle "
            "the other cards back into your deck.")
    SECRET = ("Search your deck for an Item card, a Pokémon Tool card, a "
              "Supporter card, and a Stadium card, reveal them, and put them "
              "into your hand. Then, shuffle your deck.")
    TROLLEY = ("Search your deck for any number of Basic Pokémon and put them "
               "onto your Bench. Then, shuffle your deck.")
    TOP = ("Search your deck for 2 cards, shuffle your deck, then put those "
           "cards on top of it in any order.")

    def test_roto_ok(self):
        self.assertEqual(A.audit_batch79("Roto-Stick", "Roto-Stick", self.ROTO,
                                         ["recon:4:all:supporter"]), [])

    def test_roto_old_form_flagged(self):
        errs = A.audit_batch79("Roto-Stick", "Roto-Stick", self.ROTO,
                               ["recon:4"])
        self.assertTrue(any("all:supporter" in e for e in errs), errs)

    def test_secret_ok(self):
        self.assertEqual(A.audit_batch79(
            "Secret Box", "Secret Box", self.SECRET,
            ["search_multi:item+tool+supporter+stadium:1"]), [])

    def test_secret_folded_flagged(self):
        errs = A.audit_batch79("Secret Box", "Secret Box", self.SECRET,
                               ["search_trainer:1"])
        self.assertTrue(any("静默少拿 3 张" in e for e in errs), errs)

    def test_trolley_ok(self):
        self.assertEqual(A.audit_batch79(
            "Precious Trolley", "Precious Trolley", self.TROLLEY,
            ["search_bench:all:basic"]), [])

    def test_trolley_fixed_one_flagged(self):
        errs = A.audit_batch79("Precious Trolley", "Precious Trolley",
                               self.TROLLEY, ["search_bench:1:basic"])
        self.assertTrue(any("search_bench:all:basic" in e for e in errs), errs)

    def test_top_ok(self):
        self.assertEqual(A.audit_batch79("Ciphermaniac’s Codebreaking", "X",
                                         self.TOP, ["search_top:2"]), [])

    def test_top_written_as_hand_flagged(self):
        errs = A.audit_batch79("Ciphermaniac’s Codebreaking", "X", self.TOP,
                               ["search_any:2"])
        self.assertTrue(any("静默多抽" in e for e in errs), errs)

    def test_bottom_with_top_tag_flagged(self):
        desc = ("Search your deck for 2 cards, shuffle your deck, then put "
                "those cards on the bottom of your deck in any order.")
        errs = A.audit_batch79("X", "Y", desc, ["search_top:2"])
        self.assertTrue(any("方向相反" in e for e in errs), errs)


class TestAuditBatch80_84(unittest.TestCase):
    """第八十~八十四批对账（第四十四类）：正确态放行 + 反口径必报"""

    FULL_HP = ("If this Pokémon has full HP and would be Knocked Out by damage "
               "from an attack, it is not Knocked Out, and its remaining HP "
               "becomes 10.")
    CURE = "This Pokémon recovers from all Special Conditions."
    PTD = "Shuffle up to 5 Pokémon from your discard pile into your deck."
    SD = ("Search your deck for up to 5 cards and discard them. "
          "Then, shuffle your deck.")
    CTH_BENCH = ("Put damage counters on each of your opponent’s Benched "
                 "Pokémon until its remaining HP is 100.")
    CTH_ACTIVE = ("Place damage counters on your opponent's Active Pokémon "
                  "until its remaining HP is 10.")

    # 80
    def test_full_hp_ok(self):
        self.assertEqual(A.audit_batch80_84("Pikachu ex", "X", self.FULL_HP,
                                            ["passive:survive:10"]), [])

    def test_full_hp_coin_version_flagged(self):
        errs = A.audit_batch80_84("Pikachu ex", "X", self.FULL_HP,
                                  ["passive:coin_survive:10"])
        self.assertTrue(any("口径相反" in e for e in errs), errs)

    # 81
    def test_cure_ok(self):
        self.assertEqual(A.audit_batch80_84("Arboliva ex", "X", self.CURE,
                                            ["cure:self"]), [])

    def test_cure_missing_flagged(self):
        errs = A.audit_batch80_84("Arboliva ex", "X", self.CURE, [])
        self.assertTrue(any("cure:self" in e for e in errs), errs)

    # 82
    def test_pokemon_to_deck_ok(self):
        self.assertEqual(A.audit_batch80_84("Sacred Ash", "X", self.PTD,
                                            ["pokemon_to_deck:5"]), [])

    def test_pokemon_to_deck_wrong_source_flagged(self):
        errs = A.audit_batch80_84("Sacred Ash", "X", self.PTD,
                                  ["energy_to_deck:any:5"])
        self.assertTrue(any("来源不同" in e for e in errs), errs)

    def test_shuffle_all_refused_flagged(self):
        d = "Shuffle all Pokémon from your discard pile into your deck."
        errs = A.audit_batch80_84("X", "Y", d, ["pokemon_to_deck:5"])
        self.assertTrue(any("shuffle all" in e for e in errs), errs)

    # 83
    def test_search_discard_ok(self):
        self.assertEqual(A.audit_batch80_84("Brilliant Blender", "X", self.SD,
                                            ["search_discard:5"]), [])

    def test_search_discard_written_as_hand_flagged(self):
        errs = A.audit_batch80_84("Brilliant Blender", "X", self.SD,
                                  ["search_any:5"])
        self.assertTrue(any("去向相反" in e for e in errs), errs)

    def test_into_hand_not_discard_flagged(self):
        d = ("Search your deck for up to 5 cards and put them into your hand. "
             "Then, shuffle your deck.")
        errs = A.audit_batch80_84("X", "Y", d, ["search_discard:5"])
        self.assertTrue(any("去向相反" in e for e in errs), errs)

    # 84
    def test_counters_to_hp_bench_ok(self):
        self.assertEqual(A.audit_batch80_84("PalossandEX", "X", self.CTH_BENCH,
                                            ["counters_to_hp:100:opp_bench"]),
                         [])

    def test_counters_to_hp_active_ok(self):
        self.assertEqual(A.audit_batch80_84("Scolipede", "X", self.CTH_ACTIVE,
                                            ["counters_to_hp:10:opp_active"]),
                         [])

    def test_counters_to_hp_wrong_scope_flagged(self):
        errs = A.audit_batch80_84("PalossandEX", "X", self.CTH_BENCH,
                                  ["counters_to_hp:100:opp_active"])
        self.assertTrue(any("opp_bench" in e for e in errs), errs)

    def test_fixed_counters_not_to_hp_flagged(self):
        d = "Put 3 damage counters on your opponent's Active Pokémon."
        errs = A.audit_batch80_84("X", "Y", d, ["counters_to_hp:10:opp_active"])
        self.assertTrue(any("口径不同" in e for e in errs), errs)

    def test_to_hp_written_as_fixed_flagged(self):
        errs = A.audit_batch80_84("Scolipede", "X", self.CTH_ACTIVE,
                                  ["counters:9"])
        self.assertTrue(any("逐只差额" in e for e in errs), errs)


# ------------------------------ 第八十五批 ------------------------------
class TestBatch85(unittest.TestCase):
    KPM = ("If the Lillie's Pokémon this card is attached to is Knocked Out by "
           "damage from an attack from your opponent's Pokémon, that player takes "
           "1 fewer Prize card.")

    def test_lillies_pearl_ok(self):
        self.assertEqual(A.audit_batch85("Lillie's Pearl", "X", self.KPM,
                                        ["ko_prize_minus:1"]), [])

    def test_lillies_pearl_missing_flagged(self):
        errs = A.audit_batch85("Lillie's Pearl", "X", self.KPM,
                               ["unsupported"])
        self.assertTrue(any("ko_prize_minus" in e for e in errs), errs)


class TestBatch86(unittest.TestCase):
    TXT = ("Choose a Pokémon in play (yours or your opponent's) that has the "
           "least HP remaining, except for this Pokémon, and it is Knocked Out.")
    BAD = ("Choose a Pokémon in play (yours or your opponent's) that has the "
           "most HP remaining, except for this Pokémon, and it is Knocked Out.")

    def test_least_hp_ok(self):
        self.assertEqual(
            A.audit_knockout("Inteleon", "Bring Down", self.TXT,
                             ["knockout:least_hp"]), [])

    def test_least_hp_mismatch_flagged(self):
        # 卡面写的是 most（语义相反）→ 不应落到 least_hp
        errs = A.audit_knockout("Inteleon", "Bring Down", self.BAD,
                                ["knockout:least_hp"])
        self.assertTrue(any("least_hp" in e or "目标" in e for e in errs), errs)

    def test_surface_without_tag_flagged(self):
        # 卡面写了 Knock Out 却没 tag → 静默丢效果
        errs = A.audit_knockout("Inteleon", "Bring Down", self.TXT, [])
        self.assertTrue(any("knockout" in e for e in errs), errs)

    def test_nbsp_surface_matches(self):
        # 卡面含**不间断空格**（\xa0）也要能对上（真实卡池 Inteleon 实测）
        txt = ("Choose a Pokémon in play\u00a0(yours or your opponent's) that "
               "has the least HP remaining, except for this Pokémon, and it is "
               "Knocked Out.")
        self.assertEqual(
            A.audit_knockout("Inteleon", "Bring Down", txt,
                             ["knockout:least_hp"]), [])


class TestBatch87(unittest.TestCase):
    TXT = ("If your opponent's Basic Pokémon is Knocked Out by damage from an "
           "attack used by this Pokémon, take 1 more Prize card.")

    def test_opp_basic_ok(self):
        self.assertEqual(
            A.audit_knockout("Hydreigon ex", "Greedy Eater", self.TXT,
                             ["ko_prize_plus:1:opp_basic"]), [])

    def test_opp_basic_no_surface_flagged(self):
        # tag 含 opp_basic 但卡面无「Basic Pokémon」→ 静默错映射
        errs = A.audit_knockout("Hydreigon ex", "Greedy Eater",
                                "If your opponent's Pokémon is Knocked Out by "
                                "damage from an attack used by this Pokémon, "
                                "take 1 more Prize card.",
                                ["ko_prize_plus:1:opp_basic"])
        self.assertTrue(any("opp_basic" in e for e in errs), errs)


class TestBatch88(unittest.TestCase):
    TXT = ("Discard a Basic {G} Energy card from your hand. If you can't, "
           "this attack does nothing.")

    def test_type_ok(self):
        self.assertEqual(
            A.audit_energy_cost("Decidueye", "Power Shot", self.TXT,
                                ["discard_hand_energy:1:type:grass:required"]),
            [])

    def test_type_mismatch_flagged(self):
        # 卡面 {G} 却落成 fire → 弃错属性
        errs = A.audit_energy_cost("Decidueye", "Power Shot", self.TXT,
                                   ["discard_hand_energy:1:type:fire:required"])
        self.assertTrue(any("属性" in e for e in errs), errs)


class TestBatch89(unittest.TestCase):
    TXT = ("During this turn, if your opponent's Active Pokémon is Knocked Out "
           "by damage from an attack used by your Tera Pokémon, take 1 more "
           "Prize card.")

    def test_briar_ok(self):
        self.assertEqual(
            A.audit_batch89("Briar", "X", self.TXT, ["prize_bonus:1:tera"]), [])

    def test_briar_missing_flagged(self):
        errs = A.audit_batch89("Briar", "X", self.TXT, ["unsupported"])
        self.assertTrue(any("prize_bonus" in e for e in errs), errs)

    def test_scope_mismatch_flagged(self):
        # 卡面是 Tera 却落成 nteam → 静默错映射
        errs = A.audit_batch89("Briar", "X", self.TXT,
                               ["prize_bonus:1:nteam"])
        self.assertTrue(any("prize_bonus" in e for e in errs), errs)


class TestBatch93_94(unittest.TestCase):
    """条件型 / 计数源型伤害缩放：卡面有该句式 → 必须产对应 tag"""

    EXV = ("If your opponent's Active Pokémon is a Pokémon {ex} or Pokémon {V}, "
           "this attack does 110 more damage.")
    SUP = ("If you played a Supporter card that has “Team Rocket” in its name from "
           "your hand during this turn, this attack does 100 more damage.")
    PAN = ("If any of your Benched Pancham have any damage counters on them, this "
           "attack does 120 more damage.")
    ZORO = ("This attack does 60 damage for each of your opponent's Pokémon {ex} "
            "and Pokémon {V} in play.")
    TOOL = ("This attack does 40 damage for each Pokémon Tool attached to all "
            "Pokémon.")

    def test_ex_or_v_ok(self):
        self.assertEqual(A.audit_batch93_94(
            "Galvantula ex", "Charged Web", self.EXV,
            ["dmg_plus:110:if:opp_active_is_ex_or_v"]), [])

    def test_ex_or_v_missing_flagged(self):
        errs = A.audit_batch93_94("Galvantula ex", "Charged Web", self.EXV, [])
        self.assertTrue(any("opp_active_is_ex_or_v" in e for e in errs), errs)

    def test_supporter_ok(self):
        self.assertEqual(A.audit_batch93_94(
            "TR Kangaskhan ex", "Wicked Impact", self.SUP,
            ["dmg_plus:100:if:played_supporter_named:teamrocket"]), [])

    def test_supporter_wrong_name_flagged(self):
        errs = A.audit_batch93_94(
            "TR Kangaskhan ex", "Wicked Impact", self.SUP,
            ["dmg_plus:100:if:played_supporter_named:n"])
        self.assertTrue(any("played_supporter_named" in e for e in errs), errs)

    def test_bench_named_ok(self):
        self.assertEqual(A.audit_batch93_94(
            "Pangoro", "Master's Punch", self.PAN,
            ["dmg_plus:120:if:own_bench_named_has_damage:pancham"]), [])

    def test_bench_named_missing_flagged(self):
        errs = A.audit_batch93_94("Pangoro", "Master's Punch", self.PAN,
                                  ["dmg_plus:120:if:own_bench_has_damage"])
        self.assertTrue(any("own_bench_named_has_damage" in e for e in errs), errs)

    def test_opp_ex_count_ok(self):
        self.assertEqual(A.audit_batch93_94(
            "Zoroark", "Illusory Hijacking", self.ZORO,
            ["dmg_set:60:per:opp_ex_or_v"]), [])

    def test_opp_ex_count_missing_flagged(self):
        errs = A.audit_batch93_94("Zoroark", "Illusory Hijacking", self.ZORO, [])
        self.assertTrue(any("opp_ex_or_v" in e for e in errs), errs)

    def test_tools_ok(self):
        self.assertEqual(A.audit_batch93_94(
            "Bronzong", "Tool Drop", self.TOOL,
            ["dmg_set:40:per:both_field_tools"]), [])

    def test_tools_wrong_scope_flagged(self):
        # 落成「己方道具数」→ 系统性少算
        errs = A.audit_batch93_94("Bronzong", "Tool Drop", self.TOOL,
                                  ["dmg_set:40:per:own_field_tools"])
        self.assertTrue(any("both_field_tools" in e for e in errs), errs)


if __name__ == "__main__":
    unittest.main()
