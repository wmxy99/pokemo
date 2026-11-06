# -*- coding: utf-8 -*-
"""fx 族：静态伤害增减解析检测 —— 正例 + 负向守卫

正例覆盖池内已确认的 10 种可完整表达范围；负向守卫锁定**近邻写法**：
Future 例外 / Mega 条件 / 回合内发动型 / 临时减伤 / 未限定来源的减伤，
都必须保持未建模，不能产出 fx tag。
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
from agent.fx import fx_static_dmg as D  # noqa: E402


def _tags(text, kind="ability", name="X"):
    r = E.parse_effects(text, kind=kind, name=name)
    return E.to_engine_tags(r["specs"])


class TestStaticDamageDetect(unittest.TestCase):
    # ---- 正例：整段可完整表达 ----
    def test_team(self):
        r = _tags("Attacks used by your Pokémon do 20 more damage to your "
                  "opponent’s Active Pokémon (before applying Weakness and "
                  "Resistance).")
        self.assertEqual(r["tags"], ["fx:static_dmg:add:20:team"])
        self.assertEqual(r["unsupported"], [])

    def test_self(self):
        r = _tags("Attacks used by this Pokémon do 50 more damage to your "
                  "opponent’s Active Pokémon (before applying Weakness and "
                  "Resistance).")
        self.assertEqual(r["tags"], ["fx:static_dmg:add:50:self"])

    def test_team_type_pair(self):
        r = _tags("Attacks used by your {G} Pokémon and {R} Pokémon do 20 "
                  "more damage to your opponent’s Active Pokémon (before "
                  "applying Weakness and Resistance).")
        self.assertEqual(r["tags"], ["fx:static_dmg:add:20:team_type:grass,fire"])

    def test_team_evolution_type(self):
        r = _tags("Attacks used by your Evolution {R} Pokémon do 10 more "
                  "damage to your opponent’s Active Pokémon (before applying "
                  "Weakness and Resistance).")
        self.assertEqual(r["tags"],
                         ["fx:static_dmg:add:10:team_evolution_type:fire"])

    def test_team_faction_nonstack(self):
        r = _tags("Attacks used by your Hop’s Pokémon do 30 more damage to "
                  "your opponent’s Active Pokémon (before applying Weakness "
                  "and Resistance). The effect of Extra Helpings doesn’t stack.")
        self.assertEqual(r["tags"],
                         ["fx:static_dmg:add:30:team_name:hops:nonstack"])

    def test_self_vs_ability(self):
        r = _tags("Attacks used by this Pokémon do 50 more damage to your "
                  "opponent’s Active Pokémon that has an Ability (before "
                  "applying Weakness and Resistance).")
        self.assertEqual(r["tags"], ["fx:static_dmg:add:50:self_vs_ability"])

    def test_self_if_damage(self):
        r = _tags("If this Pokémon has 2 or more damage counters on it, "
                  "attacks used by this Pokémon do 120 more damage to your "
                  "opponent’s Active Pokémon (before applying Weakness and "
                  "Resistance).")
        self.assertEqual(r["tags"],
                         ["fx:static_dmg:add:120:self_if_damage:2"])

    def test_self_per_prize(self):
        r = _tags("Attacks used by this Pokémon do 30 more damage to your "
                  "opponent’s Active Pokémon for each Prize card your opponent "
                  "has taken (before applying Weakness and Resistance).")
        self.assertEqual(r["tags"], ["fx:static_dmg:add:30:self_per_prize"])

    def test_team_vs_evolution(self):
        r = _tags("Attacks used by your Pokémon do 30 more damage to your "
                  "opponent’s Active Evolution Pokémon (before applying "
                  "Weakness and Resistance).")
        self.assertEqual(r["tags"], ["fx:static_dmg:add:30:team_vs_evolution"])

    def test_opponent_active_suppression(self):
        r = _tags("As long as this Pokémon is in the Active Spot, attacks "
                  "used by your opponent’s Active Pokémon do 30 less damage "
                  "(before applying Weakness and Resistance).")
        self.assertEqual(r["tags"], ["fx:static_dmg:sub:30:opp_active"])

    # ---- 负向守卫：近邻写法必须仍被拒绝 ----
    def test_future_exception_modeled(self):
        """2026-11-06（第九十五批）：`Future` 子类 + 「except any X」已可精确表达

        旧实现拒绝它的理由是「数据模型无 Future」——**该理由已过期**：
        数据集 `category` 自 B3 起就有 `Future`（10 张）/ `Ancient`（14 张）。
        现在落 `team_category:future:except:<key>`；例外必须进 tag，否则
        Iron Crown ex 自己的招式也会 +20（静默加强）。
        与本类 `test_mega_condition_modeled` 同性质：**能力到位就要正向建模**。
        """
        r = _tags("Attacks used by your Future Pokémon, except any Iron Crown "
                  "{ex}, do 20 more damage to your opponent’s Active Pokémon "
                  "(before applying Weakness and Resistance).")
        self.assertEqual(
            r["tags"],
            ["fx:static_dmg:add:20:team_category:future:except:ironcrown"])

    def test_category_without_before_applying_rejected(self):
        """负向守卫：缺「(before applying Weakness and Resistance)」→ 仍拒绝

        该括号声明「加伤在弱点/抗性之前」，缺了无法确定结算顺序 → 不认领。
        """
        r = _tags("Attacks used by your Future Pokémon, except any Iron Crown "
                  "{ex}, do 20 more damage to your opponent’s Active Pokémon.")
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"], "缺结算顺序声明必须保持未建模")

    def test_mega_condition_modeled(self):
        """2026-10-28 第十二批：`{D} Mega` 前置条件已可精确求值（Seviper）

        旧实现因「无 Mega 子类型」拒绝；引擎现已有 `is_mega` 字段与
        `self_if_mega_type` 范围 —— 这条必须**正向**落 tag，不得退回未建模。
        """
        r = _tags("If you have any {D} Mega Evolution Pokémon {ex} in play, "
                  "attacks used by this Pokémon do 120 more damage to your "
                  "opponent’s Active Pokémon (before applying Weakness and "
                  "Resistance).")
        self.assertEqual(r["tags"],
                         ["fx:static_dmg:add:120:self_if_mega_type:darkness"])
        self.assertEqual(r["unsupported"], [])

    def test_non_mega_condition_still_rejected(self):
        """其它前置条件（己方具名能量等）不得被当成 Mega 条件放行"""
        r = _tags("If you have any {D} Mega Evolution Pokémon {ex} in play, "
                  "attacks used by your Pokémon do 120 more damage to your "
                  "opponent’s Active Pokémon (before applying Weakness and "
                  "Resistance).")
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])

    def test_during_this_turn_rejected(self):
        r = _tags("Once during your turn, you may put 5 damage counters on "
                  "this Pokémon. If you do, during this turn, attacks used by "
                  "this Pokémon do 120 more damage to your opponent’s Active "
                  "Pokémon (before applying Weakness and Resistance).")
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])

    def test_temporary_debuff_rejected(self):
        r = _tags("During your opponent’s next turn, attacks used by the "
                  "Defending Pokémon do 20 less damage (before applying "
                  "Weakness and Resistance).")
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])

    def test_opponent_scope_not_misread_as_faction(self):
        r = _tags("As long as this Pokémon is in the Active Spot, attacks "
                  "used by your opponent’s Active Pokémon do 30 more damage "
                  "to your opponent’s Active Pokémon (before applying "
                  "Weakness and Resistance).")
        self.assertEqual(r["tags"], [])

    def test_unrelated_ability_rejected(self):
        self.assertIsNone(D.detect("Once during your turn, draw 2 cards.",
                                   "ability", "X"))
        self.assertIsNone(D.detect("Attacks used by your Pokémon do 20 more "
                                   "damage to your opponent’s Active Pokémon "
                                   "(before applying Weakness and Resistance).",
                                   "attack", "X"))


class TestTypedPassiveReduction(unittest.TestCase):
    """来源属性限定的常驻减伤：只挡指定属性，不能放大成挡所有"""

    def test_typed_reduction(self):
        r = _tags("This Pokémon takes 30 less damage from attacks from your "
                  "opponent’s {R} or {W} Pokémon (after applying Weakness and "
                  "Resistance).")
        self.assertEqual(r["tags"],
                         ["passive:dmg_reduce:30:from:type:fire,water"])
        self.assertEqual(r["unsupported"], [])

    def test_untyped_self_reduction_still_unconditional(self):
        r = _tags("This Pokémon takes 30 less damage from attacks (after "
                  "applying Weakness and Resistance).")
        self.assertEqual(r["tags"], ["passive:dmg_reduce:30"])

    def test_other_source_restriction_rejected(self):
        """非属性来源（道具/特性/特定卡）→ 不得退化成无条件减伤"""
        r = _tags("This Pokémon takes 30 less damage from attacks from your "
                  "opponent’s Pokémon that have any Special Energy attached "
                  "(after applying Weakness and Resistance).")
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])


if __name__ == "__main__":
    unittest.main()
