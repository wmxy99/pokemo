# -*- coding: utf-8 -*-
"""第十八批（2026-10-29）· 解析侧：连续掷币缩放 / 抛币复合子效果

覆盖：
  · ``coin_until_tails:dmg_plus:N``     Breloom / Tyrogue / Voltorb ex
  · ``coin:heads:dmg_plus:N|heal:M``    Floragato（同一次抛币的两个子效果）
  · ``coin_all:2:tails:self:M``         Team Rocket's Raticate

负向守卫：可选加伤（Gurdurr「You may … If you do」）仍如实未建模；
「Flip N coins. If both … does N more damage」保持原 coin_all 语义不回归。
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

BRELOOM = ("Flip a coin until you get tails. This attack does 50 more damage "
           "for each heads.")
TYROGUE = ("Flip a coin until you get tails. This attack does 30 more damage "
           "for each heads.")
VOLTORB = ("Flip a coin until you get tails. This attack does 100 more damage "
           "for each heads.")
FLORAGATO = ("Flip a coin. If heads, this attack does 30 more damage, and heal "
             "30 damage from this Pokémon.")
RATICATE = ("Flip 2 coins. If both of them are tails, this Pokémon also does 90 "
            "damage to itself.")
GURDURR = ("You may do 30 more damage. If you do, this Pokémon also does 30 "
           "damage to itself.")
BEWEAR = ("Flip 2 coins. If both of them are heads, this attack does 100 more "
          "damage.")
PLAIN_HEADS = ("Flip a coin. If heads, this attack does 30 more damage.")


def _tags(text, kind="attack", name="X"):
    return E.to_engine_tags([E.parse_rule(text, kind=kind, name=name)])


class TestCoinUntilTailsParse(unittest.TestCase):
    def test_breloom(self):
        r = _tags(BRELOOM)
        self.assertEqual(r["tags"], ["coin_until_tails:dmg_plus:50"])
        self.assertEqual(r["unsupported"], [])

    def test_tyrogue(self):
        r = _tags(TYROGUE)
        self.assertEqual(r["tags"], ["coin_until_tails:dmg_plus:30"])
        self.assertEqual(r["unsupported"], [])

    def test_voltorb(self):
        r = _tags(VOLTORB)
        self.assertEqual(r["tags"], ["coin_until_tails:dmg_plus:100"])
        self.assertEqual(r["unsupported"], [])

    def test_not_confused_with_coin_each(self):
        """「until you get tails」不得被 1 枚币的 each 分支吞掉"""
        r = _tags(BRELOOM)
        self.assertFalse(any(t.startswith("coin_each:") for t in r["tags"]))


class TestCompositeCoinParse(unittest.TestCase):
    def test_floragato_single_coin_two_effects(self):
        r = _tags(FLORAGATO)
        self.assertEqual(r["tags"], ["coin:heads:dmg_plus:30|heal:30"])
        self.assertEqual(r["unsupported"], [])

    def test_plain_heads_unchanged(self):
        """没有第二个子效果时不得凭空加 `|` 段"""
        r = _tags(PLAIN_HEADS)
        self.assertEqual(r["tags"], ["coin:heads:dmg_plus:30"])
        self.assertEqual(r["unsupported"], [])


class TestCoinAllSelfParse(unittest.TestCase):
    def test_raticate(self):
        r = _tags(RATICATE)
        self.assertEqual(r["tags"], ["coin_all:2:tails:self:90"])
        self.assertEqual(r["unsupported"], [])

    def test_bewear_regression(self):
        """既有 coin_all 加伤语义不得被本批改动破坏"""
        r = _tags(BEWEAR)
        self.assertEqual(r["tags"], ["coin_all:2:heads:dmg_plus:100"])
        self.assertEqual(r["unsupported"], [])

    def test_optional_extra_still_unmodeled(self):
        """「You may … If you do」是可选代价 → 仍如实未建模，不得退化成无条件"""
        r = _tags(GURDURR)
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])


class TestCoinStatusParse(unittest.TestCase):
    """第二十二批：抛币状态名归一 + 多币条件状态（Slurpuff / Butterfree）"""

    SLURP = ("Flip 2 coins. This attack does 90 damage for each heads. If both "
             "of them are tails, your opponent’s Active Pokémon is now Confused.")
    BUTTER = ("Flip 4 coins. This attack does 60 damage for each heads. If at "
              "least 2 of them are heads, your opponent’s Active Pokémon is now "
              "Paralyzed.")
    SINGLE = ("Flip a coin. If heads, your opponent’s Active Pokémon is now "
              "Paralyzed.")
    MULTI_STATUS = ("Flip 2 coins. If both of them are tails, your opponent’s "
                    "Active Pokémon is now Paralyzed and Poisoned.")

    def test_slurpuff_all_tails_confuse(self):
        r = _tags(self.SLURP)
        self.assertEqual(r["tags"],
                         ["coin_each:2:heads:dmg_set:90|cond:all_tails:confuse"])
        self.assertEqual(r["unsupported"], [])

    def test_butterfree_at_least_heads(self):
        r = _tags(self.BUTTER)
        self.assertEqual(
            r["tags"],
            ["coin_each:4:heads:dmg_set:60|cond:at_least_heads:2:paralyze"])
        self.assertEqual(r["unsupported"], [])

    def test_single_coin_status_is_canonical(self):
        """历史 `coin:heads:paralyzed` 引擎不识别（静默不生效）→ 必须规范名"""
        r = _tags(self.SINGLE)
        self.assertEqual(r["tags"], ["coin:heads:paralyze"])
        self.assertEqual(r["unsupported"], [])

    def test_multi_status_still_unmodeled(self):
        """多状态必须拒绝（只落其中一个会静默少施加）"""
        r = _tags(self.MULTI_STATUS)
        self.assertTrue(r["unsupported"])


if __name__ == "__main__":
    unittest.main()
