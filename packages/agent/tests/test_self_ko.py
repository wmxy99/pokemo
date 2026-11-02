# -*- coding: utf-8 -*-
"""PokéLab Agent · 「特性自爆 + 属性限定附能」解析用例（2026-11-02 第三十七批）

Magneton SSP 59「Overvolt Discharge」：
    Once during your turn, you may attach up to 3 Basic Energy cards from your
    discard pile to your **{L} Pokémon** in any way you like.
    **If you use this Ability, this Pokémon is Knocked Out.**

与既有 `attach_spread` 的三个差异，各有用例守着：

  A. **`{L} Pokémon` 是属性限定** → `target:type-lightning`（按 `pdef.ptype`），
     不是 `cat-*`（那是按 `pdef.category`：Future / Tera / Marnie's）。
  B. **末尾自爆句** → `:selfko`，必须是**尾段**（引擎先剥它再解析 scope）。
  C. **特性侧开放**：本批把 `_detect_attach_spread` 的认领从 `kind == "attack"`
     放开到 `ability`。进化触发形态（Marnie's Grimmsnarl ex）因此**仍不被认领**
     —— 三条正则全部 `^` 锚定，它的句首是 `when you play this Pokémon …`。

⚠ 最危险的近邻：全库另有 **19 处** `this Pokémon is Knocked Out`，但那些是
「**即使**被击倒」（受击反制的主语，如 Heatran / Bruxish），与「用掉就自爆」
语义相反。把它们读成 selfko 会让一堆静态特性变成自杀技。

运行：cd packages/agent && python -m unittest tests.test_self_ko -v
"""
from __future__ import annotations

import unittest

from agent.effects import (_detect_attach_spread, _spread_scope_of,
                           parse_rule, to_engine_tags)


def _m(text: str, kind: str = "ability") -> dict:
    return to_engine_tags([parse_rule(text, kind=kind, name="X")])


MAGNETON = ("Once during your turn, you may attach up to 3 Basic Energy cards "
            "from your discard pile to your {L} Pokémon in any way you like. "
            "If you use this Ability, this Pokémon is Knocked Out.")
MESPIRIT = ("Attach up to 2 Basic {P} Energy cards from your hand to your "
            "Pokémon in any way you like.")
ALTARIA = ("Search your deck for up to 2 Basic Energy cards and attach them to "
           "your Pokémon in any way you like. Then, shuffle your deck.")
CINDERACE = ("Search your deck for up to 3 Basic Energy cards and attach them "
             "to your Benched Pokémon in any way you like. Then, shuffle your "
             "deck.")
JOLTIK = ("Search your deck for up to 2 Basic {G} Energy cards and up to 2 "
          "Basic {L} Energy cards and attach them to your Pokémon in any way "
          "you like. Then, shuffle your deck.")
MIRAIDON = ("Search your deck for up to 2 Basic Energy cards and attach them to "
            "your Future Pokémon in any way you like. Then, shuffle your deck.")
MARNIE = ("Once during your turn, when you play this Pokémon from your hand to "
          "evolve 1 of your Pokémon, you may use this Ability. Search your deck "
          "for up to 2 Basic Energy cards and attach them to your Pokémon in "
          "any way you like. Then, shuffle your deck.")
# 「即使被击倒」——受击反制，**不是**自爆
HEATRAN = ("If this Pokémon is in the Active Spot and is damaged by an attack "
           "from your opponent's Pokémon (even if this Pokémon is Knocked Out), "
           "the Attacking Pokémon is now Burned.")


class TestMagneton(unittest.TestCase):
    def test_tag(self):
        m = _m(MAGNETON)
        self.assertEqual(m["tags"],
                         ["attach_spread:3:any:discard:"
                          "target:type-lightning:selfko"])
        self.assertEqual(m["unsupported"], [])

    def test_detector(self):
        got = _detect_attach_spread(MAGNETON)
        self.assertIsNotNone(got)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["n"], 3)
        self.assertIsNone(got[0]["etype"])        # 「Basic Energy」不限属性
        self.assertEqual(got[0]["zone"], "discard")
        self.assertEqual(got[0]["scope"], "type-lightning")
        self.assertTrue(got[0]["selfko"])

    def test_selfko_is_tail(self):
        tag = _m(MAGNETON)["tags"][0]
        self.assertTrue(tag.endswith(":selfko"))


class TestTypeScope(unittest.TestCase):
    def test_lightning(self):
        self.assertEqual(_spread_scope_of("{L} Pokémon"), "type-lightning")

    def test_other_types(self):
        self.assertEqual(_spread_scope_of("{G} Pokémon"), "type-grass")
        self.assertEqual(_spread_scope_of("{P} Pokémon"), "type-psychic")

    def test_category_still_cat(self):
        """category 与属性是两套判定依据，不能互相顶替"""
        self.assertEqual(_spread_scope_of("Future Pokémon"), "cat-future")
        self.assertEqual(_spread_scope_of("Marnie\u2019s Pokémon"), "cat-marnie")

    def test_unknown_type_symbol_not_guessed(self):
        self.assertIsNone(_spread_scope_of("{?} Pokémon"))


class TestExistingCardsUnchanged(unittest.TestCase):
    """既有 5 张卡的 tag 必须**逐字不变**（行为冻结）"""

    def test_mesprit(self):
        m = _m(MESPIRIT, kind="attack")
        self.assertEqual(m["tags"], ["attach_spread:2:psychic:hand"])
        self.assertNotIn("selfko", str(m["tags"]))

    def test_altaria(self):
        self.assertEqual(_m(ALTARIA, kind="attack")["tags"],
                         ["attach_spread:2:any:deck"])

    def test_cinderace(self):
        self.assertEqual(_m(CINDERACE, kind="attack")["tags"],
                         ["attach_spread:3:any:deck:target:bench"])

    def test_joltik_two_tags(self):
        self.assertEqual(_m(JOLTIK, kind="attack")["tags"],
                         ["attach_spread:2:grass:deck",
                          "attach_spread:2:lightning:deck"])

    def test_miraidon(self):
        self.assertEqual(_m(MIRAIDON, kind="attack")["tags"],
                         ["attach_spread:2:any:deck:target:cat-future"])


class TestAbilityOpening(unittest.TestCase):
    """本批把认领放开到 ability，但进化触发形态必须仍被排除"""

    def test_evolve_trigger_not_claimed(self):
        m = _m(MARNIE)
        self.assertEqual(m["tags"], [])
        self.assertTrue(m["unsupported"])

    def test_ability_selfko_claimed(self):
        self.assertTrue(_m(MAGNETON)["tags"])


class TestKnockedOutNeighbour(unittest.TestCase):
    """「**即使**被击倒」≠「用掉自爆」—— 19 张受击反制卡不得被读成 selfko"""

    def test_even_if_knocked_out_not_selfko(self):
        m = _m(HEATRAN)
        self.assertNotIn("selfko", str(m["tags"]))
        for t in m["tags"]:
            self.assertNotIn(":selfko", t)

    def test_no_attach_spread_detected(self):
        self.assertIsNone(_detect_attach_spread(HEATRAN))


class TestNoSelfKoWhenAbsent(unittest.TestCase):
    """卡面没有自爆句时不得凭空加上（静默削弱：把普通加速变成自杀）"""

    def test_mesprit_no_selfko(self):
        for t in _m(MESPIRIT, kind="attack")["tags"]:
            self.assertNotIn("selfko", t)


if __name__ == "__main__":
    unittest.main()
