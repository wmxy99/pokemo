# -*- coding: utf-8 -*-
"""第四十一批（2026-11-03）· K2 解析用例：{ex} 目标筛选 / 自身能量洗回牌库

覆盖 3 张真实卡：
  · Vaporeon ex PRE 23「Severe Squall」→ `snipe:opp:60:all:target:ex` + 无视弱抗
  · Zeraora DRI 78「Thunder Raid」  → `discard_energy:all` + `snipe:opp_bench:210:1:target:ex`
  · Mega Skarmory ex POR 55「Sonic Ripper」
    → `snipe:opp:220:1` + `energy_self_to_deck:all`（**强制代价**，不得只剩伤害）

负向守卫：
  · 与 {V} 混写（「{ex} or Benched Pokémon {V}」/「{ex} and Pokémon {V}」）→ 仍不可表达；
  · 没有 {ex} 限定的目标**不得**带上 `:target:ex`（会凭空缩小目标集合）；
  · 卡面没有「Shuffle all Energy attached …」不得产出 `energy_self_to_deck`。

运行：cd packages/agent && python -m unittest tests.test_batch41_parse -v
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


def _m(text: str, kind: str = "attack") -> dict:
    sp = E.parse_rule(text, kind=kind, name="X")
    out = E.to_engine_tags([sp])
    return {"tags": out["tags"],
            "unsupported": [u["reason"] for u in out["unsupported"]]}


VAPOREON = ("This attack does 60 damage to each of your opponent’s Pokémon {ex}. "
            "This attack’s damage isn’t affected by Weakness or Resistance.")
ZERAORA = ("Discard all Energy from this Pokémon, and this attack does 210 damage "
           "to 1 of your opponent’s Benched Pokémon {ex}. (Don’t apply Weakness "
           "and Resistance for Benched Pokémon.)")
SKARMORY = ("Shuffle all Energy attached to this Pokémon into your deck, and this "
            "attack does 220 damage to 1 of your opponent’s Pokémon. (Don’t apply "
            "Weakness and Resistance for Benched Pokémon.)")
SHAYMIN = ("This attack does 60 damage to 1 of your opponent’s Benched Pokémon "
           "{ex} or Benched Pokémon {V}. (Don’t apply Weakness and Resistance for "
           "Benched Pokémon.)")
FLYGON = ("This attack does 100 damage to each of your opponent’s Pokémon {ex} "
          "and Pokémon {V}. This attack’s damage isn’t affected by Weakness or "
          "Resistance.")


class TestSnipeTargetEx(unittest.TestCase):
    def test_each_of_ex(self):
        r = _m(VAPOREON)
        self.assertIn("snipe:opp:60:all:target:ex", r["tags"])
        self.assertEqual(r["unsupported"], [])

    def test_one_benched_ex(self):
        r = _m(ZERAORA)
        self.assertIn("snipe:opp_bench:210:1:target:ex", r["tags"])
        self.assertIn("discard_energy:all", r["tags"])
        self.assertEqual(r["unsupported"], [])

    def test_plain_target_has_no_ex_filter(self):
        """没有 {ex} 限定 → 不得带 `:target:ex`（会凭空缩小目标集合）"""
        r = _m("This attack does 60 damage to each of your opponent’s Pokémon.")
        self.assertIn("snipe:opp:60:all", r["tags"])
        self.assertNotIn(":target:ex", " ".join(r["tags"]))

    def test_ex_or_v_now_modeled(self):
        """与 {V} 混写：第九十八批起按 `:target:ex` 建模

        旧口径（第四十一批）拒绝它，理由是「引擎没有 V 标记、无法确认意图」。
        但引擎**四十一批起就有 `:target:ex`**，缺的只是并集写法的短语映射；
        且本卡池 1267 张**没有 Pokémon V**（`test_batch98_100_110_parse.
        TestNoVInDataset` 守着这条前提）—— 并集与 {ex} 在当前数据上等价。
        """
        r = _m(SHAYMIN)
        self.assertEqual(r["tags"], ["snipe:opp_bench:60:1:target:ex"])
        self.assertEqual(r["unsupported"], [])

    def test_ex_and_v_now_modeled(self):
        r = _m(FLYGON)
        self.assertIn("snipe:opp:100:all:target:ex", r["tags"])
        self.assertEqual(r["unsupported"], [])


class TestEnergySelfToDeck(unittest.TestCase):
    def test_cost_is_modeled(self):
        r = _m(SKARMORY)
        self.assertIn("energy_self_to_deck:all", r["tags"])
        self.assertIn("snipe:opp:220:1", r["tags"])
        self.assertEqual(r["unsupported"], [],
                         "代价已建模，不得再报「能量代价子句未被映射」")

    def test_no_sentence_no_tag(self):
        r = _m("This attack does 220 damage to 1 of your opponent’s Pokémon.")
        self.assertNotIn("energy_self_to_deck:all", r["tags"])

    def test_discard_energy_is_still_distinct(self):
        """「Discard all Energy from this Pokémon」是**弃置**，不是洗回牌库"""
        r = _m(ZERAORA)
        self.assertIn("discard_energy:all", r["tags"])
        self.assertNotIn("energy_self_to_deck:all", r["tags"])

    def test_optional_cost_with_more_damage_still_unmodeled(self):
        """Dialga MEG 95：**You may** 洗回能量 → +80 伤害，属可选代价 + 加伤

        落成固定代价会静默削掉 +80（卡仍可打但更弱），故必须如实未建模。
        """
        t = ("You may shuffle all Energy attached to this Pokémon into your deck "
             "and have this attack do 80 more damage.")
        r = _m(t)
        self.assertNotIn("energy_self_to_deck:all", r["tags"])
        self.assertTrue(r["unsupported"])


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
