# -*- coding: utf-8 -*-
"""第六十三~六十七批（2026-11-04）· 解析用例

每族都覆盖「正确形态 → 精确 tag」与「近似写法 → 必须不认领」两侧。
只测正向等于橡皮图章：本轮最危险的四个失败方向都是**静默改强度** ——
  ① `can't attack` 漏认（封锁整段消失）/ 与具名招式封锁混淆（扩大范围）；
  ② 对手自选洗回牌库落成我方确定性操作（对象口径反了）；
  ③ 宽窄两档 `ignore_effects` 混用（漏掉弱抗 / 凭空无视弱抗）；
  ④ 「any amount」压成固定 1 张、`their Benched` 误判成己方 bench。
"""
from __future__ import annotations
import unittest

from agent import effects as E


def _tags(text: str, kind: str = "attack", name: str = "X") -> dict:
    out = E.to_engine_tags([E.parse_rule(text, kind=kind, name=name)])
    return {"tags": out["tags"],
            "unsupported": [u["reason"] for u in out["unsupported"]]}


class TestSelfAttackLock(unittest.TestCase):
    """第六十三批：自身跨回合禁攻（两种动词写法同一语义）"""

    def test_cant_attack(self):
        r = _tags("During your next turn, this Pokémon can’t attack.")
        self.assertEqual(r["tags"], ["attack_lock:all:turn"])
        self.assertEqual(r["unsupported"], [])

    def test_cant_use_attacks_unchanged(self):
        """旧句式不能被新正则改坏"""
        r = _tags("During your next turn, this Pokémon can’t use attacks.")
        self.assertEqual(r["tags"], ["attack_lock:all:turn"])

    def test_combined_with_other_effect(self):
        """Floatzel：弃能 + 下回合禁攻，两句都要落 tag"""
        r = _tags("Discard an Energy from your opponent’s Active Pokémon. "
                  "During your next turn, this Pokémon can’t attack.")
        self.assertIn("discard_energy:1:opp", r["tags"])
        self.assertIn("attack_lock:all:turn", r["tags"])

    def test_named_attack_lock_not_all(self):
        """「can't use Haymaker」只锁一个招式，不能落 all（静默加强）"""
        r = _tags("During your next turn, this Pokémon can’t use Haymaker.",
                  name="Crabominable")
        self.assertNotIn("attack_lock:all:turn", r["tags"])

    def test_constant_condition_not_claimed(self):
        """「can't attack unless …」是常驻条件，不是跨回合锁定 → 必须不认领"""
        r = _tags("This Pokémon can’t attack unless you have 4 or more "
                  "Team Rocket’s Pokémon in play.")
        self.assertNotIn("attack_lock:all:turn", r["tags"])


class TestOppChooseShuffleHand(unittest.TestCase):
    """第六十四批：对手自选洗回牌库 ⇒ 引擎随机"""

    def test_banette(self):
        r = _tags("Your opponent chooses 3 cards from their hand and "
                  "shuffles those cards into their deck.")
        self.assertEqual(r["tags"], ["shuffle_opp_hand:3"])
        self.assertEqual(r["unsupported"], [])

    def test_count_is_exact(self):
        r = _tags("Your opponent chooses 1 card from their hand and "
                  "shuffles those cards into their deck.")
        self.assertEqual(r["tags"], ["shuffle_opp_hand:1"])

    def test_my_choice_not_claimed(self):
        """「you choose」是我方选 ⇒ 确定性，绝不能落 `shuffle_opp_hand`"""
        r = _tags("You choose 3 cards from your opponent’s hand and "
                  "shuffle those cards into their deck.")
        self.assertNotIn("shuffle_opp_hand:3", r["tags"])


class TestIgnoreEffectsTwoForms(unittest.TestCase):
    """第六十五批：宽窄两档反口径"""

    WR = ("This attack’s damage isn’t affected by Weakness or Resistance, "
          "or by any effects on your opponent’s Active Pokémon.")
    NARROW = ("This attack’s damage isn’t affected by any effects on "
              "your opponent’s Active Pokémon.")

    def test_wide_form(self):
        r = _tags(self.WR)
        self.assertEqual(r["tags"], ["ignore_effects:wr"])
        self.assertEqual(r["unsupported"], [])

    def test_wide_form_those_pokemon(self):
        """Iron Crown ex 用「those Pokémon」指代本招目标，仍属宽口径"""
        r = _tags(self.WR.replace("your opponent’s Active Pokémon",
                                  "those Pokémon"))
        self.assertEqual(r["tags"], ["ignore_effects:wr"])

    def test_narrow_form_unchanged(self):
        """旧窄口径不能因为本批被改成 :wr（否则凭空无视弱抗）"""
        r = _tags(self.NARROW)
        self.assertIn("ignore_effects", r["tags"])
        self.assertNotIn("ignore_effects:wr", r["tags"])

    def test_wide_not_two_tags(self):
        """同一句只认领一次（两条 tag 会让 guard 拦下整卡）"""
        r = _tags(self.WR)
        self.assertEqual(r["tags"].count("ignore_effects:wr"), 1)


class TestKofuDiscountCounter(unittest.TestCase):
    """第六十六批：弃牌堆 Kofu 计数源减费"""

    KOFU = ("Attacks used by this Pokémon cost {C} less for each Kofu "
            "card in your discard pile.")

    def test_kofu_counter(self):
        r = _tags(self.KOFU, kind="ability")
        self.assertEqual(
            r["tags"],
            ["fx:attack_cost:self:less:per:own_discard_named:kofu"])
        self.assertEqual(r["unsupported"], [])

    def test_existing_counter_unchanged(self):
        r = _tags("Attacks used by this Pokémon cost {C} less for each "
                  "of your opponent’s Benched Pokémon.", kind="ability")
        self.assertEqual(
            r["tags"], ["fx:attack_cost:self:less:per:opp_bench"])

    def test_colored_discount_not_claimed(self):
        """只支持无色减费；{R} 会改变「哪些能量付得起」，必须不认领"""
        r = _tags(self.KOFU.replace("{C}", "{R}"), kind="ability")
        self.assertEqual(r["tags"], [])

    def test_unknown_counter_not_claimed(self):
        r = _tags("Attacks used by this Pokémon cost {C} less for each "
                  "Fish card in your discard pile.", kind="ability")
        self.assertEqual(r["tags"], [])


class TestEnergyMoveZoneAliases(unittest.TestCase):
    """第六十七批：能量移动区域别名（阵营不可反转）"""

    def test_jynx_new_benched(self):
        r = _tags("move an Energy from this Pokémon to the new Benched "
                  "Pokémon.")
        self.assertEqual(r["tags"],
                         ["energy_move:1:from:active:to:bench"])

    def test_azumarill_other_to_this(self):
        r = _tags("Move an Energy from 1 of your other Pokémon to this "
                  "Pokémon.", kind="ability")
        self.assertEqual(r["tags"],
                         ["energy_move:1:from:any:to:active"])

    def test_opponent_bench_stays_opponent(self):
        """`their Benched` 必须仍判 opp_bench（新别名不能覆盖旧阵营）"""
        r = _tags("Move an Energy from this Pokémon to 1 of their "
                  "Benched Pokémon.")
        self.assertNotIn("energy_move:1:from:active:to:bench", r["tags"])

    def test_any_amount_not_folded_to_one(self):
        """「any amount」**不是**压成 1 张，而是逐点让玩家搬（`choose:alloc`）

        ⚠ B4-b（2026-11-08）改判：以前断言「必须未建模」（当时没有分配层）。
        现在 `choose:alloc:move_energy:<type>` 承载 —— 选项 = 「把某只的 1 点
        某色能量搬到另一只」，玩家逐次选、`stop` 结束。
        本条真正要钉住的错法没变：不得出现 `energy_move:1:…`（压成 1 张）。
        """
        r = _tags("You may move any amount of {M} Energy from your Pokémon "
                  "to your other Pokémon in any way you like.")
        self.assertNotIn("energy_move:1:from:any:to:any", r["tags"])
        self.assertIn("choose:alloc:move_energy:metal", r["tags"])

    def test_conditional_hp_still_unmodeled(self):
        """条件式 +HP（Okidogi）按 C5 保持未建模，不能被本轮误收"""
        r = _tags("If this Pokémon has any {D} Energy attached, it gets "
                  "+100 HP, and the attacks it uses do 100 more damage.",
                  kind="ability")
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])


if __name__ == "__main__":
    unittest.main()
