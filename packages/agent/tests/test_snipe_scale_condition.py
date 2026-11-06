# -*- coding: utf-8 -*-
"""第十七批（2026-10-29）· 解析侧：选定目标的缩放 / 条件 / 反伤缩放

覆盖：
  · ``snipe:opp:N:1:per:<counter>``        Raging Bolt / Ferrothorn / Genesect / Wugtrio ex
  · ``snipe:opp_bench:N:all:per:<counter>`` Kyurem ex
  · ``snipe:…:if:<cond>``                   Wo-Chien（牌库 ≤3）/ Ting-Lu（场上有场地）
  · ``self:N:per:<counter>``                Palafin（按自身伤害指示物）

负向守卫：条件认不出（「If you do」代价）/ 目标子集（{ex} / {V}）必须如实登记未建模。

第二十七批（2026-10-29）起，两类「目标相关」写法已可精确表达，故从负向守卫
改为正向用例：
  · ``:target:damaged``        「… that has any damage counters on it」（Excadrill ex）
  · ``:per:target_counters``   「for each damage counter on **that** Pokémon」（Sneasel）
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

RAGING_BOLT = ("This attack does 30 damage to 1 of your opponent’s Pokémon for "
               "each Energy attached to this Pokémon. (Don’t apply Weakness and "
               "Resistance for Benched Pokémon.)")
FERROTHORN = ("This attack does 20 damage to 1 of your opponent’s Pokémon for "
              "each Energy attached to this Pokémon. (Don’t apply Weakness and "
              "Resistance for Benched Pokémon.)")
GENESECT = ("This attack does 20 damage to 1 of your opponent’s Pokémon for "
            "each {G} Energy attached to this Pokémon. (Don’t apply Weakness "
            "and Resistance for Benched Pokémon.)")
WUGTRIO = ("Discard up to 3 Energy cards from your hand. This attack does 60 "
           "damage to 1 of your opponent’s Pokémon for each Energy card you "
           "discarded in this way. (Don’t apply Weakness and Resistance for "
           "Benched Pokémon.)")
KYUREM = ("This attack also does 10 damage to each of your opponent’s Benched "
          "Pokémon for each Prize card your opponent has taken. (Don’t apply "
          "Weakness and Resistance for Benched Pokémon.)")
WO_CHIEN = ("If there are 3 or fewer cards in your deck, this attack also does "
            "120 damage to 2 of your opponent’s Benched Pokémon. (Don’t apply "
            "Weakness and Resistance for Benched Pokémon.)")
TING_LU = ("If a Stadium is in play, this attack also does 30 damage to each of "
           "your opponent’s Benched Pokémon, and discard that Stadium. (Don’t "
           "apply Weakness and Resistance for Benched Pokémon.)")
PALAFIN = "This Pokémon also does 10 damage to itself for each damage counter on it."
OGERPON = ("You may shuffle 3 Energy attached to this Pokémon into your deck. If "
           "you do, this attack also does 120 damage to 1 of your opponent’s "
           "Benched Pokémon. (Don’t apply Weakness and Resistance for Benched "
           "Pokémon.)")
EXCADRILL = ("This attack also does 60 damage to 1 of your opponent’s Benched "
             "Pokémon that has any damage counters on it. (Don’t apply Weakness "
             "and Resistance for Benched Pokémon.)")
SHAYMIN = ("This attack does 60 damage to 1 of your opponent’s Benched Pokémon "
           "{ex} or Benched Pokémon {V}. (Don’t apply Weakness and Resistance "
           "for Benched Pokémon.)")
SNEASEL = ("This attack does 20 damage to 1 of your opponent’s Benched Pokémon "
           "for each damage counter on that Pokémon. (Don’t apply Weakness and "
           "Resistance for Benched Pokémon.)")


def _tags(text, kind="attack", name="X"):
    return E.to_engine_tags([E.parse_rule(text, kind=kind, name=name)])


class TestSnipeScaleParse(unittest.TestCase):
    def test_raging_bolt(self):
        r = _tags(RAGING_BOLT)
        self.assertEqual(r["tags"], ["snipe:opp:30:1:per:own_active_energy"])
        self.assertEqual(r["unsupported"], [])

    def test_ferrothorn(self):
        r = _tags(FERROTHORN)
        self.assertEqual(r["tags"], ["snipe:opp:20:1:per:own_active_energy"])
        self.assertEqual(r["unsupported"], [])

    def test_genesect_typed_energy(self):
        r = _tags(GENESECT)
        self.assertEqual(r["tags"],
                         ["snipe:opp:20:1:per:own_active_energy_grass"])
        self.assertEqual(r["unsupported"], [])

    def test_wugtrio_discarded_energy(self):
        r = _tags(WUGTRIO)
        self.assertIn("snipe:opp:60:1:per:own_discarded_energy_this_turn",
                      r["tags"])
        self.assertEqual(r["unsupported"], [])

    def test_kyurem_prize_scaled_all_bench(self):
        r = _tags(KYUREM)
        self.assertEqual(r["tags"],
                         ["snipe:opp_bench:10:all:per:opp_prizes_taken"])
        self.assertEqual(r["unsupported"], [])

    def test_wo_chien_conditional(self):
        r = _tags(WO_CHIEN)
        self.assertEqual(
            r["tags"], ["snipe:opp_bench:120:2:if:own_deck_at_most:3"])
        self.assertEqual(r["unsupported"], [])

    def test_ting_lu_stadium_condition(self):
        r = _tags(TING_LU)
        self.assertEqual(
            sorted(r["tags"]),
            sorted(["snipe:opp_bench:30:all:if:stadium_in_play",
                    "discard_stadium"]))
        self.assertEqual(r["unsupported"], [])

    def test_palafin_self_scaled(self):
        r = _tags(PALAFIN)
        self.assertEqual(r["tags"], ["self:10:per:own_active_counters"])
        self.assertEqual(r["unsupported"], [])


class TestSnipeNegativeGuards(unittest.TestCase):
    def test_optional_cost_condition_unmodeled(self):
        """「You may … If you do, …」是**可选代价**，条件不可直读 → 不猜"""
        r = _tags(OGERPON)
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])

    def test_target_has_damage_counters(self):
        """「… Benched Pokémon **that has any damage counters on it**」→ 目标筛选

        2026-10-29 第二十七批：引擎支持 `:target:damaged`（先筛目标再选 1 个），
        不再是「不可表达子集」。
        """
        r = _tags(EXCADRILL)
        self.assertEqual(r["tags"], ["snipe:opp_bench:60:1:target:damaged"])
        self.assertEqual(r["unsupported"], [])

    def test_ex_subset_now_modeled(self):
        """`{ex} or {V}` 目标：第九十八批起落 `:target:ex`（本卡池无 V 卡）"""
        r = _tags(SHAYMIN)
        self.assertEqual(r["tags"], ["snipe:opp_bench:60:1:target:ex"])
        self.assertEqual(r["unsupported"], [])

    def test_target_relative_scale(self):
        """「for each damage counter on **that** Pokémon」→ `:per:target_counters`

        第二十七批起引擎按**每个目标自身**的指示物数缩放（哨兵 `target_counters`），
        不再因为「依赖所选目标」而整体拒绝。
        """
        r = _tags(SNEASEL)
        self.assertEqual(r["tags"], ["snipe:opp_bench:20:1:per:target_counters"])
        self.assertEqual(r["unsupported"], [])

    def test_unknown_deck_condition_unmodeled(self):
        """牌库条件若不是「N or fewer」写法 → 不猜"""
        r = _tags("If there are exactly 2 cards in your deck, this attack does "
                  "30 damage to 1 of your opponent’s Benched Pokémon.")
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])


if __name__ == "__main__":
    unittest.main()
