# -*- coding: utf-8 -*-
"""第八十~八十四批（2026-11-05）· K2 解析用例

按 class 分批，每批都含**正向落地**与**负向自测**（错误写法必须不被认领）。
运行：cd packages/agent && python -m unittest tests.test_batch80_84_parse -v
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


def _m(text: str, kind: str = "ability") -> dict:
    sp = E.parse_rule(text, kind=kind, name="X")
    out = E.to_engine_tags([sp])
    return {"tags": out["tags"],
            "unsupported": [u["reason"] for u in out["unsupported"]]}


# ------------------------------ 第八十批 ------------------------------
SURVIVE_FULL_HP = (
    "If this Pokémon has full HP and would be Knocked Out by damage from an "
    "attack, it is not Knocked Out, and its remaining HP becomes 10.")


class TestBatch80Survive(unittest.TestCase):
    """满 HP 免击倒·特性版；三种相邻句式必须互不冒充"""

    def test_pikachu_ex(self):
        r = _m(SURVIVE_FULL_HP)
        self.assertEqual(r["tags"], ["passive:survive:10"])
        self.assertEqual(r["unsupported"], [])

    def test_coin_version_not_rebranded(self):
        t = ("If this Pokémon would be Knocked Out by damage from an attack, "
             "flip a coin. If heads, this Pokémon is not Knocked Out, and its "
             "remaining HP becomes 10.")
        r = _m(t)
        self.assertEqual(r["tags"], ["passive:coin_survive:10"])

    def test_without_full_hp_is_refused(self):
        """缺「has full HP」→ 既不是本形态也不是抛币版，如实拒绝"""
        t = ("If this Pokémon would be Knocked Out by damage from an attack, "
             "it is not Knocked Out, and its remaining HP becomes 10.")
        r = _m(t)
        self.assertFalse([x for x in r["tags"] if "survive" in x])
        self.assertTrue(r["unsupported"])


# ------------------------------ 第八十一批 ------------------------------
class TestBatch81CureSelf(unittest.TestCase):
    """清除自身全部特殊状态；换对象 / 单数范围都不得认领"""

    def test_arboliva_ex(self):
        r = _m("This Pokémon recovers from all Special Conditions.",
               kind="attack")
        self.assertEqual(r["tags"], ["cure:self"])
        self.assertEqual(r["unsupported"], [])

    def test_opponent_scope_refused(self):
        r = _m("Your opponent's Active Pokémon recovers from all Special "
               "Conditions.", kind="attack")
        self.assertNotIn("cure:self", r["tags"])
        self.assertTrue(r["unsupported"])

    def test_singular_scope_refused(self):
        r = _m("This Pokémon recovers from a Special Condition.",
               kind="attack")
        self.assertNotIn("cure:self", r["tags"])
        self.assertTrue(r["unsupported"])


# ------------------------------ 第八十二批 ------------------------------
class TestBatch82PokemonToDeck(unittest.TestCase):
    """Sacred Ash：只认「up to N + from your discard pile + into your deck」"""

    def test_sacred_ash(self):
        r = _m("Shuffle up to 5 Pokémon from your discard pile into your deck.",
               kind="item")
        self.assertEqual(r["tags"], ["pokemon_to_deck:5"])
        self.assertEqual(r["unsupported"], [])

    def test_all_is_refused(self):
        r = _m("Shuffle all Pokémon from your discard pile into your deck.",
               kind="item")
        self.assertNotIn("pokemon_to_deck:5", r["tags"])
        self.assertTrue(r["unsupported"])

    def test_into_hand_is_refused(self):
        r = _m("Shuffle up to 5 Pokémon from your discard pile into your hand.",
               kind="item")
        self.assertNotIn("pokemon_to_deck:5", r["tags"])
        self.assertTrue(r["unsupported"])


# ------------------------------ 第八十三批 ------------------------------
class TestBatch83SearchDiscard(unittest.TestCase):
    """检索后弃置：只认训练家卡级的「up to N 任意卡 + discard them」"""

    def test_brilliant_blender(self):
        r = _m("Search your deck for up to 5 cards and discard them. "
               "Then, shuffle your deck.", kind="item")
        self.assertEqual(r["tags"], ["search_discard:5"])
        self.assertEqual(r["unsupported"], [])

    def test_drilbur_bench_trigger_now_modeled(self):
        """上场触发 + Basic {F} Energy 筛选 → 第一百批起建模

        决策反转留痕：第八十三批只做了**训练家卡级**的「检索后弃置」
        （Brilliant Blender），特性侧的上场触发没有钩子 → 当时如实拒绝。
        第一百批补了 `on_bench_act:` 包装层（与 `evolve_act:` 同构，只在
        **放到后备那一刻**执行一次），并给 `search_discard` 加了类别/属性筛选。

        ⚠ 包装层不能省：不包就是普通 field 特性 → 每回合白弃 3 张牌库。
        """
        r = _m("When you play this Pokémon from your hand onto your Bench "
               "during your turn, you may search your deck for up to 3 "
               "Basic {F} Energy cards and discard them. Then, shuffle your "
               "deck.", kind="ability")
        self.assertEqual(
            r["tags"], ["on_bench_act:search_discard:3:energy:type:fighting"])
        self.assertEqual(r["unsupported"], [])

    def test_into_hand_not_rebranded(self):
        r = _m("Search your deck for up to 5 cards and put them into your "
               "hand. Then, shuffle your deck.", kind="item")
        self.assertNotIn("search_discard:5", r["tags"])


# ------------------------------ 第八十四批 ------------------------------
class TestBatch84CountersToHp(unittest.TestCase):
    """逐只放到「剩余 HP = N」；固定个数 / 范围不明都不得认领"""

    def test_palossand_bench(self):
        r = _m("Put damage counters on each of your opponent’s Benched "
               "Pokémon until its remaining HP is 100.", kind="attack")
        self.assertEqual(r["tags"], ["counters_to_hp:100:opp_bench"])
        self.assertEqual(r["unsupported"], [])

    def test_scolipede_active(self):
        r = _m("Place damage counters on your opponent's Active Pokémon "
               "until its remaining HP is 10.", kind="attack")
        self.assertEqual(r["tags"], ["counters_to_hp:10:opp_active"])
        self.assertEqual(r["unsupported"], [])

    def test_fixed_counter_count_not_rebranded(self):
        r = _m("Put 3 damage counters on your opponent's Active Pokémon.",
               kind="attack")
        self.assertEqual(r["tags"], ["counters:3"])

    def test_unknown_scope_refused(self):
        r = _m("Put damage counters on each Pokémon in play until its "
               "remaining HP is 100.", kind="attack")
        self.assertFalse([x for x in r["tags"]
                          if x.startswith("counters_to_hp")])
        self.assertTrue(r["unsupported"])


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
