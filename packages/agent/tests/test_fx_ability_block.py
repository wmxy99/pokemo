# -*- coding: utf-8 -*-
"""fx 族：特性 / 场地「no Abilities」解析检测 —— 正例 + 负向守卫

正例：三段已完整表达、无近邻歧义的卡面必须产出正确 tag。
负向守卫：近邻写法（Rule Box+Future 例外 / 只说 Benched 不限定 Stage 2 /
完全无关文本）必须仍被拒绝 —— 不静默认领、不泄漏 fx tag 进池。
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
from agent.fx import fx_ability_block as D  # noqa: E402


class TestAbilityBlockDetect(unittest.TestCase):
    def _tags(self, txt, kind, name):
        r = E.parse_effects(txt, kind=kind, name=name)
        m = E.to_engine_tags(r["specs"])
        return m["tags"], m["unsupported"]

    # ---- 正例 ----
    def test_gastrodon(self):
        tags, unsup = self._tags(
            "As long as this Pokémon is on your Bench, Benched Stage 2 Pokémon "
            "(both yours and your opponent’s) have no Abilities.",
            "ability", "Sticky Bind")
        self.assertEqual(tags, ["fx:ability_block:gastrodon"])
        self.assertEqual(unsup, [])

    def test_flutter_mane(self):
        tags, unsup = self._tags(
            "As long as this Pokémon is in the Active Spot, your opponent’s "
            "Active Pokémon has no Abilities, except for Midnight Fluttering.",
            "ability", "Midnight Fluttering")
        self.assertEqual(tags, ["fx:ability_block:flutter_mane"])
        self.assertEqual(unsup, [])

    def test_watchtower(self):
        tags, unsup = self._tags(
            "{C} Pokémon in play (both yours and your opponent’s) have no Abilities.",
            "stadium", "Team Rocket's Watchtower")
        self.assertEqual(tags, ["stadium:ability_block:colorless"])
        self.assertEqual(unsup, [])

    # ---- 负向守卫：近邻写法必须仍被拒绝 ----
    def test_iron_thorns_rejected(self):
        # Rule Box + Future 例外：数据模型无 rule_box / Future 子类型 → 必须落未建模
        tags, unsup = self._tags(
            "As long as this Pokémon is in the Active Spot, Pokémon with a Rule "
            "Box in play (both yours and your opponent’s) have no Abilities, "
            "except for Future Pokémon.",
            "ability", "Initialization")
        self.assertEqual(tags, [])
        self.assertTrue(unsup, "Iron Thorns 必须保持未建模")

    def test_benched_no_stage2_rejected(self):
        # 只说 "Benched Pokémon" 没限定 Stage 2 → 子集不可表达 → 拒绝
        tags, unsup = self._tags(
            "As long as this Pokémon is on your Bench, Benched Pokémon have no Abilities.",
            "ability", "Foo")
        self.assertEqual(tags, [])
        self.assertTrue(unsup)

    def test_unrelated_text_rejected(self):
        tags, _ = self._tags("This attack does 60 damage.", "attack", "Tackle")
        self.assertEqual(tags, [])

    def test_detect_does_not_hijack_other_text(self):
        # detect 直接调用也必须对无关文本返回 None
        self.assertIsNone(D.detect("Your opponent reveals their hand.", "ability", "X"))
        self.assertIsNone(D.detect("Each player shuffles their hand.", "stadium", "Y"))


if __name__ == "__main__":
    unittest.main()
