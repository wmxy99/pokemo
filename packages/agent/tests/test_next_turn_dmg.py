# -*- coding: utf-8 -*-
"""第十六批（2026-10-29）· 解析侧：跨回合伤害修正

覆盖：
  · ``dmg_taken_plus_next:N``                 Vibrava / Stunfisk（弱抗之后）
  · ``atk_minus_next:N``                      Hop's Rookidee / Sylveon ex（弱抗之前）
  · ``self_attack_plus_next:N:name:<招式>``   Meloetta ex / Pecharunt（具名招式）

三道守卫：
  · 括号里必须写明结算顺序（``after/before applying Weakness…``），否则不猜；
  · 「During your next turn」这三句已认领后**不得**再被通用伤害缩放重复登记
    （这正是 Meloetta ex / Pecharunt 之前被整卡踢出卡池的根因）；
  · 具名招式键必须保留空格并与引擎 `_attack_key` 同口径（否则永远匹配不上）。
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

VIBRAVA = ("During your next turn, the Defending Pokémon takes 50 more damage "
           "from attacks (after applying Weakness and Resistance).")
STUNFISK = ("During your opponent’s next turn, the Defending Pokémon can’t "
            "retreat. During your next turn, the Defending Pokémon takes 100 "
            "more damage from attacks (after applying Weakness and "
            "Resistance).")
ROOKIDEE = ("During your opponent’s next turn, attacks used by the Defending "
            "Pokémon do 20 less damage (before applying Weakness and "
            "Resistance).")
SYLVEON = ("During your opponent’s next turn, attacks used by the Defending "
           "Pokémon do 100 less damage (before applying Weakness and "
           "Resistance).")
MELOETTA = ("During your next turn, this Pokémon’s Echoed Voice attack does "
            "80 more damage (before applying Weakness and Resistance).")
PECHARUNT = ("During your next turn, this Pokémon’s Mochi Rush attack does 50 "
             "more damage (before applying Weakness and Resistance).")
WISHIWASHI = ("During your opponent’s next turn, attacks used by your Pokémon "
              "do 30 less damage.")
NO_ORDER = ("During your next turn, the Defending Pokémon takes 50 more damage "
            "from attacks.")


def _tags(text, kind="attack", name="X"):
    return E.to_engine_tags([E.parse_rule(text, kind=kind, name=name)])


class TestNextTurnDamageParse(unittest.TestCase):
    def test_vibrava(self):
        r = _tags(VIBRAVA)
        self.assertEqual(r["tags"], ["dmg_taken_plus_next:50"])
        self.assertEqual(r["unsupported"], [])

    def test_stunfisk_two_clauses(self):
        """撤退封锁 + 跨回合加伤必须同时保留（逐子句认领）"""
        r = _tags(STUNFISK)
        self.assertEqual(
            sorted(r["tags"]),
            sorted(["retreat_lock:opp:turn", "dmg_taken_plus_next:100"]))
        self.assertEqual(r["unsupported"], [])

    def test_rookidee(self):
        r = _tags(ROOKIDEE)
        self.assertEqual(r["tags"], ["atk_minus_next:20"])
        self.assertEqual(r["unsupported"], [])

    def test_sylveon(self):
        r = _tags(SYLVEON)
        self.assertEqual(r["tags"], ["atk_minus_next:100"])
        self.assertEqual(r["unsupported"], [])

    def test_meloetta_named_attack(self):
        r = _tags(MELOETTA)
        self.assertEqual(
            r["tags"], ["self_attack_plus_next:80:name:echoed voice"])
        self.assertEqual(r["unsupported"], [],
                         "已认领的句子不得被通用缩放分支重复登记")

    def test_pecharunt_named_attack(self):
        r = _tags(PECHARUNT)
        self.assertEqual(
            r["tags"], ["self_attack_plus_next:50:name:mochi rush"])
        self.assertEqual(r["unsupported"], [])

    def test_global_scope_still_unmodeled(self):
        """「attacks used by **your** Pokémon」是全体而非受击方 → 保持未建模"""
        r = _tags(WISHIWASHI)
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])

    def test_without_order_paren_unmodeled(self):
        """没写 after/before applying Weakness… → 结算顺序不可确定，不猜"""
        r = _tags(NO_ORDER)
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])

    def test_ability_side_not_claimed(self):
        """特性侧没有派发点 → 不得产出招式 tag（避免两份真相）"""
        r = _tags(VIBRAVA, kind="ability")
        self.assertFalse(any("dmg_taken_plus_next" in t for t in r["tags"]))


if __name__ == "__main__":
    unittest.main()
