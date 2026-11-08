# -*- coding: utf-8 -*-
"""第十五批（2026-10-28）· 解析侧：检索附能的目标限定

覆盖：
  · ``attach:N[:type]:deck:target:bench``            Smoochum
  · ``attach:N[:type]:deck:target:any``              Teal Mask Ogerpon
  · ``attach_energy:N:type:deck:target:bench_type:<c>[:counters:N]``  Toxtricity
  · ``attach:…:anyenergy``                            Shaymin（an Energy card）
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

SMOOCHUM = ("Search your deck for up to 2 Basic {P} Energy cards and attach "
            "them to 1 of your Benched Pokémon. Then, shuffle your deck.")
OGERPON = ("Search your deck for a Basic {G} Energy card and attach it to 1 of "
           "your Pokémon. Then, shuffle your deck.")
TOXTRICITY = ("Once during your turn, you may use this Ability. Search your "
              "deck for a Basic {D} Energy card and attach it to 1 of your "
              "Benched {D} Pokémon. Then, shuffle your deck. If you attached "
              "Energy to a Pokémon in this way, place 2 damage counters on that "
              "Pokémon.")
SHAYMIN = ("Search your deck for an Energy card and attach it to 1 of your "
           "Benched {G} Pokémon. Then, shuffle your deck.")
ALTARIA = ("Search your deck for up to 2 Basic Energy cards and attach them to "
           "your Pokémon in any way you like. Then, shuffle your deck.")


def _tags(text, kind="attack", name="X"):
    return E.to_engine_tags([E.parse_rule(text, kind=kind, name=name)])


class TestAttachTargetParse(unittest.TestCase):
    def test_smoochum_bench(self):
        r = _tags(SMOOCHUM, kind="attack")
        self.assertEqual(r["tags"], ["attach:2:psychic:deck:target:bench"])
        self.assertEqual(r["unsupported"], [])

    def test_ogerpon_any(self):
        r = _tags(OGERPON, kind="attack")
        self.assertEqual(r["tags"], ["attach:1:grass:deck:target:any"])
        self.assertEqual(r["unsupported"], [])

    def test_toxtricity_ability(self):
        r = _tags(TOXTRICITY, kind="ability")
        self.assertEqual(
            r["tags"],
            ["attach_energy:1:darkness:deck:target:bench_type:darkness:"
             "counters:20"])
        self.assertEqual(r["unsupported"], [])

    def test_shaymin_any_energy(self):
        r = _tags(SHAYMIN, kind="attack")
        self.assertEqual(
            r["tags"],
            ["attach:1:deck:target:bench_type:grass:anyenergy"])
        self.assertEqual(r["unsupported"], [])

    def test_distribution_now_mapped(self):
        """「in any way you like」—— **2026-10-30 第三十三批起改为映射**

        此前这里断言「要玩家分配 → 必须保持未建模」。现在引擎侧提供两条通道：
        动作可带 ``attach_plan`` 显式指定落点，缺省按 **round-robin**
        （active → bench 顺序）确定分配 —— 与 `snipe` / `opp_hand` 同一口径
        （「choose 类缺省取确定性默认，绝不静默不生效」）。

        ⚠ 近似性质要披露：确定性默认**不等于**最优分配。所以同族里句式只要
        多一个未建模的修饰（如 Terapagos 的「of different types」、Magneton 的
        自爆代价）就仍然如实拒绝，绝不因为「能分配了」就放行。
        """
        r = _tags(ALTARIA, kind="attack")
        self.assertEqual(r["tags"], ["attach_spread:2:any:deck"])
        self.assertEqual(r["unsupported"], [])


class TestEnergyToHandParse(unittest.TestCase):
    """第二十三批（2026-10-29）：附着能量收回手牌"""

    ONE = "Put an Energy attached to this Pokémon into your hand."
    TWO_FIRE = "Put 2 {R} Energy attached to this Pokémon into your hand."
    OPP = ("Put an Energy attached to your opponent’s Active Pokémon into "
           "their hand.")
    COIN = ("Once during your turn, you may use this Ability. Flip a coin. If "
            "heads, put an Energy attached to your opponent’s Active Pokémon "
            "into their hand.")
    OPTIONAL = ("You may put all Energy attached to this Pokémon into your hand "
                "to have this attack do 80 more damage.")

    def test_self_one(self):
        r = _tags(self.ONE)
        self.assertEqual(r["tags"], ["bounce:energy:self:1"])
        self.assertEqual(r["unsupported"], [])

    def test_typed_two(self):
        r = _tags(self.TWO_FIRE)
        self.assertEqual(r["tags"], ["bounce:energy:self:2:type:fire"])
        self.assertEqual(r["unsupported"], [])

    def test_opp_active(self):
        r = _tags(self.OPP)
        self.assertEqual(r["tags"], ["bounce:energy:opp_active:1"])

    def test_coin_branch_modeled_as_coin_not_unconditional(self):
        """抛币分支内的回手（第四十四批）：由 `coin:` 承载，**不得**退化成无条件

        决策反转留痕：本用例原断言「整段未建模」。Dustox 的抛币回手现在有
        引擎执行器（`_do_ability` 抛一次后派发 `bounce:`），因此改为锁定
        「仍然是 50% 分支」——无条件 `bounce:energy:...` 才是静默加强。
        """
        r = _tags(self.COIN, kind="ability")
        self.assertEqual(r["tags"], ["coin:heads:bounce:energy:opp_active:1"])
        self.assertEqual(r["unsupported"], [])

    def test_optional_cost_modeled_as_choice(self):
        """「You may … to have this attack do N more」= **可选**代价 + 加伤

        ⚠ B4（2026-11-08）改判：原先断言「如实未建模」。现在由
        `pre:b4:all_energy:to_hand` + 挂起选择承载（付 → 能量回手 → +N）。
        关键仍是「**可选**」：绝不能落成无条件 `bounce:energy`（那是白赚回手）。
        """
        r = _tags(self.OPTIONAL)
        self.assertIn("pre:b4:all_energy:to_hand", r["tags"])
        self.assertIn("dmg_plus:80:per:chosen_this_attack", r["tags"])
        self.assertNotIn("bounce:energy:self:all", r["tags"])
        self.assertEqual(r["unsupported"], [])


class TestSpecialEnergyDiscardParse(unittest.TestCase):
    """第二十四批（2026-10-29）：弃置对手战斗位**特殊**能量"""

    DESC = "Discard a Special Energy from your opponent’s Active Pokémon."
    COMBINED = ("Before doing damage, discard all Pokémon Tools and Special Energy "
                "from your opponent’s Active Pokémon.")

    def test_special_only(self):
        r = _tags(self.DESC)
        self.assertEqual(r["tags"], ["discard_energy:1:opp:special"])
        self.assertEqual(r["unsupported"], [])

    def test_combined_is_pre_phase(self):
        """「Before doing damage」+ 道具与特殊能量同时弃 → 两条伤害前 tag（Mow Rotom）

        第二十五批起引擎有了 `pre:` 伤害前阶段，不再需要「如实未建模」。
        """
        r = _tags(self.COMBINED)
        self.assertEqual(r["tags"],
                         ["pre:discard_energy:all:opp:special",
                          "pre:discard_tool:all:opp_active"])
        self.assertEqual(r["unsupported"], [])


class TestPreAttackPhaseParse(unittest.TestCase):
    """第二十五批（2026-10-29）：`pre:` 伤害前阶段 + `:if_pre_tool` 条件状态"""

    TOOLS_ONLY = ("Before doing damage, discard all Pokémon Tools from your "
                  "opponent’s Active Pokémon.")
    JOLTIK = ("Before doing damage, discard all Pokémon Tools from your opponent’s "
              "Active Pokémon. If you discarded a Pokémon Tool in this way, your "
              "opponent’s Active Pokémon is now Paralyzed.")

    def test_tools_discarded_before_damage(self):
        r = _tags(self.TOOLS_ONLY)
        self.assertEqual(r["tags"], ["pre:discard_tool:all:opp_active"])
        self.assertEqual(r["unsupported"], [])

    def test_conditional_status_marks_if_pre_tool(self):
        r = _tags(self.JOLTIK)
        self.assertIn("pre:discard_tool:all:opp_active", r["tags"])
        self.assertIn("paralyze:if_pre_tool", r["tags"])
        self.assertNotIn("paralyze", r["tags"],
                         "不得退化成无条件麻痹（静默加强）")
        self.assertEqual(r["unsupported"], [])

    def test_plain_discard_after_damage_keeps_old_shape(self):
        """不带「Before doing damage」的同义句仍按伤害后弃置（不误升级为 pre）"""
        r = _tags("Discard all Pokémon Tools from your opponent’s Active Pokémon.")
        self.assertEqual(r["tags"], ["discard_tool:all:opp_active"])


if __name__ == "__main__":
    unittest.main()
