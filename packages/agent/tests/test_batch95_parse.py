# -*- coding: utf-8 -*-
"""第九十五批（2026-11-06）· Iron Crown ex「Cobalt Command」解析用例

卡面：「Attacks used by your **Future** Pokémon, except any Iron Crown {ex}, do 20
more damage to your opponent's Active Pokémon (before applying Weakness and
Resistance).」

历史：本族（`agent/fx/fx_static_dmg.py`）曾把这条列为「故意拒绝：数据模型无
Future」。**该理由已过期** —— 数据集 `category` 字段自 B3 起就有 `Future`（10 张）
/ `Ancient`（14 张）。现在按 `team_category` 范围建模。

必须钉住两点：
  ① 「except any X」**必须**进 tag —— 丢掉例外 = Iron Crown ex 自己的招式也 +20
     （静默加强）；
  ② 少半句（没有 "(before applying Weakness and Resistance)"）时不认领 ——
     该括号是「加伤发生在弱点/抗性之前」的口径声明，缺了就无法确保结算顺序。
"""
from __future__ import annotations

import unittest

from agent import effects as E

CROWN = ("Attacks used by your Future Pokémon, except any Iron Crown {ex}, do 20 "
         "more damage to your opponent\u2019s Active Pokémon\u00a0(before applying "
         "Weakness and Resistance).")


class TestBatch95IronCrown(unittest.TestCase):
    def _tags(self, text: str, kind: str = "ability") -> dict:
        sp = E.parse_rule(text, kind=kind, name="Cobalt Command")
        return E.to_engine_tags([sp])

    def test_future_with_exception(self):
        o = self._tags(CROWN)
        self.assertEqual(
            o["tags"],
            ["fx:static_dmg:add:20:team_category:future:except:ironcrown"])
        self.assertEqual(o["unsupported"], [])

    def test_ancient_variant(self):
        """同族另一子类（Ancient）也必须能表达"""
        o = self._tags("Attacks used by your Ancient Pokémon do 30 more damage to "
                       "your opponent's Active Pokémon (before applying Weakness "
                       "and Resistance).")
        self.assertEqual(o["tags"],
                         ["fx:static_dmg:add:30:team_category:ancient"])

    def test_dropping_exception_clause_rejected(self):
        """近邻：把 except 子句换成别的宝可梦限定 → 不认领（不得当成无例外）"""
        o = self._tags("Attacks used by your Future Pokémon, except any Iron "
                       "Crown, do 20 more damage to your opponent's Active "
                       "Pokémon (before applying Weakness and Resistance).")
        self.assertEqual(o["tags"], [], "缺 {ex} 的 except 子句不得静默跳过")
        self.assertTrue(o["unsupported"])

    def test_missing_before_applying_clause_rejected(self):
        """近邻：缺「(before applying Weakness and Resistance)」→ 不认领"""
        o = self._tags("Attacks used by your Future Pokémon, except any Iron "
                       "Crown {ex}, do 20 more damage to your opponent's Active "
                       "Pokémon.")
        self.assertEqual(o["tags"], [])
        self.assertTrue(o["unsupported"])

    def test_attack_side_not_claimed(self):
        """招式侧不得认领（该句式是常驻特性）"""
        o = self._tags(CROWN, kind="attack")
        self.assertFalse(any(t.startswith("fx:static_dmg:") for t in o["tags"]))


class TestBatch95Mismagius(unittest.TestCase):
    """条件式**后备**指示物：`:gate:` + `:scope:opp_bench`

    卡面：「If your opponent's Active Pokémon is affected by a Special Condition,
    place 6 damage counters on 1 of your opponent's Benched Pokémon.」
    """

    TXT = ("If your opponent's Active Pokémon is affected by a Special Condition, "
           "place 6 damage counters on 1 of your opponent's Benched Pokémon.")

    def _tags(self, text: str, kind: str = "attack") -> dict:
        sp = E.parse_rule(text, kind=kind, name="Assassin's Magic")
        return E.to_engine_tags([sp])

    def test_conditional_bench_counters(self):
        o = self._tags(self.TXT)
        self.assertEqual(
            o["tags"], ["counters:6:scope:opp_bench:1:gate:opp_active_has_status"])
        self.assertEqual(o["unsupported"], [])

    def test_unknown_condition_rejected(self):
        """近邻：条件不可表达时必须拒收，**不得**退化成无条件放置"""
        o = self._tags("If your opponent has exactly 3 cards in their hand, place "
                       "4 damage counters on 1 of your opponent's Benched "
                       "Pokémon.")
        self.assertEqual(o["tags"], [])
        self.assertTrue(o["unsupported"], "条件不可表达须如实未建模")

    def test_unconditional_bench_counters_unchanged(self):
        """近邻：无条件的后备指示物**不得**被新分支加上 gate

        （其既有形态是 `snipe:opp_bench:…`；本用例只钉「新分支没越界认领」，
        不钉旧形态本身 —— 旧口径是否精确不在本批范围内。）
        """
        o = self._tags("Put 3 damage counters on 1 of your opponent's Benched "
                       "Pokémon.")
        self.assertTrue(o["tags"], "无条件形态仍须产出 tag")
        self.assertFalse(any(":gate:" in t for t in o["tags"]),
                         f"无条件句式不得带 gate：{o['tags']}")


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
