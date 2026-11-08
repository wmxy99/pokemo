# -*- coding: utf-8 -*-
"""fx 族：效果免疫解析 —— 正例 + 负向守卫

正例必须**整段**可表达；近邻写法（连伤害一起免疫 / 非特性侧）一律拒绝，
不能产出半套免疫。

⚠ S4（2026-11-08）**改判了两条旧负向用例**：`全队子集` 与 `训练家卡效果`
过去因为引擎无法表达而整卡拒收，现在两组 tag（`…:team:basic_teamrocket` /
`…:trainer:item+supporter:self`）已进引擎，因此改为**正向**用例；
但判据更严了 —— 必须产出**精确**形态（落成 `:self` 或 `attack` 都会红）。

⚠ 2026-10-28 起：Tera 来源的「伤害+效果」免疫由**静态免伤族**
（`passive:dmg_immune:…:effects`）建模，本文件改为正向守卫 —— 它绝不能落到
`fx:effect_immune`（那样会丢掉「连伤害一起挡」的语义）。
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
from agent.fx import fx_effect_immune as D  # noqa: E402


def _tags(text, kind="ability", name="X"):
    r = E.parse_effects(text, kind=kind, name=name)
    return E.to_engine_tags(r["specs"])


class TestEffectImmuneDetect(unittest.TestCase):
    def test_attack_effects(self):
        r = _tags("Prevent all effects of attacks used by your opponent’s "
                  "Pokémon done to this Pokémon. (Damage is not an effect.)")
        self.assertEqual(r["tags"], ["fx:effect_immune:attack:self"])
        self.assertEqual(r["unsupported"], [])

    def test_ability_effects(self):
        r = _tags("Prevent all effects of your opponent’s Pokémon’s Abilities "
                  "done to this Pokémon.")
        self.assertEqual(r["tags"], ["fx:effect_immune:ability:self"])
        self.assertEqual(r["unsupported"], [])

    # ---- 负向守卫 ----
    def test_damage_included_rejected(self):
        r = _tags("Prevent all damage from and effects of attacks from your "
                  "opponent’s Pokémon done to this Pokémon.")
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])

    def test_team_scope_now_modeled(self):
        """**改判**（S4）：全队范围已可表达，但必须是 `team:basic_teamrocket`

        落成 `:self` 就只能保护持有者自己（少保护整队），是静默削弱。
        """
        r = _tags("Prevent all effects of attacks used by your opponent’s "
                  "Pokémon done to your Basic Team Rocket’s Pokémon. "
                  "(Existing effects are not removed. Damage is not an effect.)")
        self.assertEqual(r["tags"],
                         ["fx:effect_immune:attack:team:basic_teamrocket"])
        self.assertEqual(r["unsupported"], [])

    def test_trainer_card_effect_now_modeled(self):
        """**改判**（S4）：训练家卡来源已可表达，子类别必须精确"""
        r = _tags("Whenever your opponent plays an Item or Supporter card "
                  "from their hand, prevent all effects of that card done to "
                  "this Pokémon.")
        self.assertEqual(r["tags"],
                         ["fx:effect_immune:trainer:item+supporter:self"])
        self.assertEqual(r["unsupported"], [])

    def test_trainer_supporter_only_keeps_narrow_kind(self):
        """只写 Supporter 的印刷不得落成 `item+supporter`（那会顺带挡住 Item）"""
        r = _tags("Whenever your opponent plays a Supporter card from their "
                  "hand, prevent all effects of that card done to this Pokémon.")
        self.assertEqual(r["tags"],
                         ["fx:effect_immune:trainer:supporter:self"])

    def test_tera_source_now_modeled_as_dmg_immune(self):
        """2026-10-28 第八批：Tera 来源的「伤害+效果」免疫已由静态免伤族建模

        fx:effect_immune 只负责「纯效果免疫」（其余伤害照常）；Tera 版连伤害一起
        挡，语义务必落到 `passive:dmg_immune:…:effects`，不能用 fx 族顶替。
        """
        r = _tags("Prevent all damage from and effects of attacks from your "
                  "opponent’s Tera Pokémon done to this Pokémon.")
        self.assertEqual(r["tags"],
                         ["passive:dmg_immune:self:from:tera:effects"])
        self.assertFalse(any(str(t).startswith("fx:effect_immune")
                             for t in r["tags"]))

    def test_attack_kind_rejected(self):
        self.assertIsNone(D.detect(
            "Prevent all effects of attacks used by your opponent’s Pokémon "
            "done to this Pokémon. (Damage is not an effect.)",
            "attack", "X"))

    def test_unrelated_rejected(self):
        self.assertIsNone(D.detect("Draw 2 cards.", "ability", "X"))


class TestBothBenchCounter(unittest.TestCase):
    """「for each Benched Pokémon (both yours and your opponent's)」必须按双方"""

    def test_both_bench(self):
        r = _tags("This attack does 20 more damage for each Benched Pokémon "
                  "(both yours and your opponent’s).", kind="attack")
        self.assertEqual(r["tags"], ["dmg_plus:20:per:both_bench"])

    def test_one_side_still_one_side(self):
        r = _tags("This attack does 20 more damage for each of your Benched "
                  "Pokémon.", kind="attack")
        self.assertEqual(r["tags"], ["dmg_plus:20:per:own_bench"])

    def test_subset_qualifier_rejected(self):
        r = _tags("This attack does 20 more damage for each Benched Pokémon "
                  "that has any damage counters on it (both yours and your "
                  "opponent’s).", kind="attack")
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])


if __name__ == "__main__":
    unittest.main()
