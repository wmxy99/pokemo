# -*- coding: utf-8 -*-
"""PokéLab AI · K2「检索能量自由分配附着」解析用例（2026-10-30 第三十三批）

覆盖 ``attach_spread:<n>:<etype>:<zone>[:target:<scope>]`` 的 5 张真实卡，
以及**负向守卫**：

  · 单目标形态（「attach them to **this Pokémon**」/「to 1 of your Benched
    Pokémon」）**不得**被本族抢走 —— 它们走既有的 ``attach``；
  · 「of different types」等引擎未建模的限定 → 不认领；
  · 未知目标限定（Tera 等）→ 不认领；
  · 特性形态（on_evolve）→ 本批不认领（需 evolve_act 包装）。

运行：cd packages/agent && python -m unittest tests.test_attach_spread -v
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


def _tags(text: str, kind: str = "attack") -> dict:
    out = E.to_engine_tags([E.parse_rule(text, kind=kind, name="X")])
    return {"tags": out["tags"],
            "unsupported": [u["reason"] for u in out["unsupported"]]}


def _spread(text: str, kind: str = "attack") -> list:
    return [t for t in _tags(text, kind)["tags"] if t.startswith("attach_spread:")]


# ---- 真实卡面（与数据集 cards_challenge.json 逐字一致） ----
ALTARIA = ("Search your deck for up to 2 Basic Energy cards and attach them to "
           "your Pok\u00e9mon in any way you like. Then, shuffle your deck.")
WHIMSICOTT = ("Search your deck for up to 3 Basic Energy cards and attach them "
              "to your Pok\u00e9mon in any way you like. Then, shuffle your deck.")
CINDERACE = ("Search your deck for up to 3 Basic Energy cards and attach them to "
             "your Benched Pok\u00e9mon in any way you like. Then, shuffle your "
             "deck.")
MIRAIDON = ("Search your deck for up to 2 Basic Energy cards and attach them to "
            "your Future Pok\u00e9mon in any way you like. Then, shuffle your "
            "deck.")
JOLTIK = ("Search your deck for up to 2 Basic {G} Energy cards and up to 2 "
          "Basic {L} Energy cards and attach them to your Pok\u00e9mon in any "
          "way you like. Then, shuffle your deck.")
# 引擎未建模的限定
TERAPAGOS = ("Search your deck for up to 3 Basic Energy cards of different types "
             "and attach them to your Tera Pok\u00e9mon in any way you like. "
             "Then, shuffle your deck.")
# 单目标形态（必须继续走既有 attach）
SMOOCHUM = ("Search your deck for up to 2 Basic {P} Energy cards and attach "
            "them to 1 of your Benched Pok\u00e9mon. Then, shuffle your deck.")
CRESSELIA = ("Search your deck for up to 2 Basic {P} Energy cards and attach "
             "them to this Pok\u00e9mon. Then, shuffle your deck.")
# 特性形态（on_evolve）——本批不认领
MARNIE = ("When you play this Pok\u00e9mon from your hand to evolve 1 of your "
          "Pok\u00e9mon during your turn, you may search your deck for up to 5 "
          "Basic {D} Energy cards and attach them to your Marnie\u2019s "
          "Pok\u00e9mon in any way you like. Then, shuffle your deck.")


class TestRealCards(unittest.TestCase):
    def test_altaria(self):
        self.assertEqual(_spread(ALTARIA), ["attach_spread:2:any:deck"])

    def test_whimsicott(self):
        self.assertEqual(_spread(WHIMSICOTT), ["attach_spread:3:any:deck"])

    def test_cinderace(self):
        self.assertEqual(_spread(CINDERACE),
                         ["attach_spread:3:any:deck:target:bench"])

    def test_miraidon(self):
        self.assertEqual(_spread(MIRAIDON),
                         ["attach_spread:2:any:deck:target:cat-future"])

    def test_joltik_dual_limits(self):
        """双属性双限额 → 两条独立取（各 up to 2）"""
        self.assertEqual(_spread(JOLTIK),
                         ["attach_spread:2:grass:deck",
                          "attach_spread:2:lightning:deck"])

    def test_no_unsupported_on_recognized(self):
        for text in (ALTARIA, WHIMSICOTT, CINDERACE, MIRAIDON, JOLTIK):
            self.assertEqual(_tags(text)["unsupported"], [], text[:40])


class TestNegativeGuards(unittest.TestCase):
    def test_different_types_not_mapped(self):
        """「of different types」是数量约束（每种至多 1 张），引擎尚未建模 → 不认领"""
        self.assertEqual(_spread(TERAPAGOS), [])
        self.assertTrue(_tags(TERAPAGOS)["unsupported"])

    def test_single_target_forms_not_hijacked(self):
        """单目标形态必须继续走既有 attach，不得被本族抢走"""
        for text in (SMOOCHUM, CRESSELIA):
            self.assertEqual(_spread(text), [], text[:40])
            self.assertTrue([t for t in _tags(text)["tags"]
                             if t.startswith("attach")], text[:40])

    def test_ability_form_not_mapped(self):
        """特性形态需 evolve_act 包装，本批先不认领（否则进池但特性不生效）"""
        self.assertEqual(_spread(MARNIE, kind="ability"), [])

    def test_trailing_clause_rejected(self):
        text = ALTARIA + " If you do, draw a card."
        self.assertEqual(_spread(text), [])

    def test_missing_shuffle_suffix_rejected(self):
        """卡面没有「Then, shuffle your deck」→ 不是本族写法，不认领"""
        text = ("Search your deck for up to 2 Basic Energy cards and attach "
                "them to your Pok\u00e9mon in any way you like.")
        self.assertEqual(_spread(text), [])


class TestTextNormalization(unittest.TestCase):
    def test_multiline_matches(self):
        text = ALTARIA.replace(" ", "\n ", 4)
        self.assertEqual(_spread(text), ["attach_spread:2:any:deck"])

    def test_curly_apostrophe_in_owned_form(self):
        """「Marnie’s」用弯引号也必须识别出 cat- 限定

        注意主句不匹配是**预期**：Marnie's Grimmsnarl 的卡面带前置语
        （「When you play this Pokémon … you may search …」），句首锚定会拒绝它，
        正合本批「特性形态先不认领」的策略。
        """
        self.assertIsNone(E._ATTACH_SPREAD_SIMPLE.match(E._norm_prose(MARNIE)))
        self.assertEqual(E._spread_scope_of("Marnie\u2019s Pok\u00e9mon"),
                         "cat-marnie")
        self.assertEqual(E._spread_scope_of("Marnie's Pok\u00e9mon"),
                         "cat-marnie")


if __name__ == "__main__":
    unittest.main()
