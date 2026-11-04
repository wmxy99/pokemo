# -*- coding: utf-8 -*-
"""第四十五批（2026-11-03）· K2 解析用例：四种抛币变体

  · Mega Zygarde ex（招式）：「For each of your opponent's Pokémon, flip a coin.
    If heads, this attack does 150 damage to that Pokémon.」→
    ``coin_per_target:150:scope:opp``
  · Cradily（特性）：「Once during your turn … choose Burned, Confused, or
    Poisoned …」→ ``coin:heads:status_choose:burn+confuse+poison``
  · Fezandipiti（特性）：「If this Pokémon has any {D} Energy attached and is
    damaged by an attack, flip a coin. If heads, prevent that damage.」→
    ``passive:coin_prevent:darkness``
  · Harlequin（支援者）：「Each player shuffles … flip a coin. If heads, you draw
    5 …, opponent 3. If tails, you draw 3 …, opponent 5.」→
    ``shuffle_both_coin_draw:5:3:3:5``

负向守卫：缺抛币句 / 非三选一 / 无能量条件 / 缺洗回手牌的近邻写法都不得认领。

运行：cd packages/agent && python -m unittest tests.test_batch45_parse -v
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


def _m(text: str, kind: str) -> dict:
    sp = E.parse_rule(text, kind=kind, name="X")
    out = E.to_engine_tags([sp])
    return {"tags": out["tags"],
            "unsupported": [u["reason"] for u in out["unsupported"]]}


ZYGARDE = ("For each of your opponent's Pokémon, flip a coin. If heads, this "
           "attack does 150 damage to that Pokémon. (Don't apply Weakness and "
           "Resistance for Benched Pokémon.)")
CRADILY = ("Once during your turn, you may flip a coin. If heads, choose Burned, "
           "Confused, or Poisoned. Your opponent's Active Pokémon is now affected "
           "by that Special Condition.")
FEZANDIPITI = ("If this Pokémon has any {D} Energy attached and is damaged by an "
               "attack, flip a coin. If heads, prevent that damage.")
HARLEQUIN = ("Each player shuffles their hand into their deck. Then, flip a coin. "
             "If heads, you draw 5 cards, and your opponent draws 3 cards. If tails, "
             "you draw 3 cards, and your opponent draws 5 cards.")


class TestCoinPerTarget(unittest.TestCase):
    def test_zygarde_single_tag(self):
        r = _m(ZYGARDE, "attack")
        self.assertEqual(r["tags"], ["coin_per_target:150:scope:opp"])
        self.assertEqual(r["unsupported"], [],
                         "「that Pokémon」不得再登记为不可表达的附加伤害")

    def test_missing_coin_rejected(self):
        """没有逐目标抛币句 → 仍是不可表达的附加伤害（整卡不收）"""
        t = ("For each of your opponent's Pokémon, this attack does 150 damage "
             "to that Pokémon.")
        r = _m(t, "attack")
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])


class TestStatusChoose(unittest.TestCase):
    def test_cradily_coin_status_choose(self):
        r = _m(CRADILY, "ability")
        self.assertEqual(r["tags"],
                         ["coin:heads:status_choose:burn+confuse+poison"])
        self.assertEqual(r["unsupported"], [])

    def test_different_status_set_rejected(self):
        """不是 Burned/Confused/Poisoned 三选一 → 不得落成 status_choose"""
        t = ("Once during your turn, you may flip a coin. If heads, choose Asleep "
             "or Burned. Your opponent's Active Pokémon is now affected by that "
             "Special Condition.")
        r = _m(t, "ability")
        self.assertFalse([x for x in r["tags"] if "status_choose" in x])


class TestCoinPreventPassive(unittest.TestCase):
    def test_fezandipiti_coin_prevent(self):
        r = _m(FEZANDIPITI, "ability")
        self.assertEqual(r["tags"], ["passive:coin_prevent:darkness"])
        self.assertEqual(r["unsupported"], [])

    def test_missing_energy_condition_rejected(self):
        """没有「has any {D} Energy attached」条件 → 不得落成 coin_prevent"""
        t = ("If this Pokémon is damaged by an attack, flip a coin. If heads, "
             "prevent that damage.")
        r = _m(t, "ability")
        self.assertFalse([x for x in r["tags"] if "coin_prevent" in x])


class TestShuffleBothCoinDraw(unittest.TestCase):
    def test_harlequin_merged_tag(self):
        r = _m(HARLEQUIN, "supporter")
        self.assertEqual(r["tags"], ["shuffle_both_coin_draw:5:3:3:5"])
        self.assertEqual(r["unsupported"], [])

    def test_missing_shuffle_rejected(self):
        """缺「Each player shuffles their hand」→ 不能落成该 tag"""
        t = ("Flip a coin. If heads, you draw 5 cards, and your opponent draws 3 "
             "cards. If tails, you draw 3 cards, and your opponent draws 5 cards.")
        r = _m(t, "supporter")
        self.assertFalse([x for x in r["tags"]
                          if "shuffle_both_coin_draw" in x])


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
