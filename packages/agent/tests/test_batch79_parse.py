# -*- coding: utf-8 -*-
"""第七十九批（2026-11-05）· K2 解析用例：搜索族

  · Roto-Stick → ``recon:4:all:supporter``
  · Precious Trolley → ``search_bench:all:basic``
  · Secret Box → ``search_multi:item+tool+supporter+stadium:1``（+ 代价 discard:3）
  · Ciphermaniac's Codebreaking（训练家）/ Dialga「Time Manipulation」（招式）
    → ``search_top:2``

负向：普通「search … into your hand」不得落 `search_multi`；
「放牌库底」仍须如实未建模（`search_top` 方向相反）。

运行：cd packages/agent && python -m unittest tests.test_batch79_parse -v
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


def _m(text: str, kind: str = "item") -> dict:
    sp = E.parse_rule(text, kind=kind, name="X")
    out = E.to_engine_tags([sp])
    return {"tags": out["tags"],
            "unsupported": [u["reason"] for u in out["unsupported"]],
            "cost": sp.get("play_cost"), "gate": sp.get("play_gate")}


class TestSearchFamily(unittest.TestCase):
    def test_roto_stick(self):
        r = _m("Look at the top 4 cards of your deck. You may reveal any number "
               "of Supporter cards you find there and put them into your hand. "
               "Shuffle the other cards back into your deck.")
        self.assertEqual(r["tags"], ["recon:4:all:supporter"])
        self.assertEqual(r["unsupported"], [])

    def test_precious_trolley(self):
        r = _m("Search your deck for any number of Basic Pokémon and put them "
               "onto your Bench. Then, shuffle your deck.")
        self.assertEqual(r["tags"], ["search_bench:all:basic"])
        self.assertEqual(r["unsupported"], [])

    def test_secret_box(self):
        r = _m("You can use this card only if you discard 3 other cards from "
               "your hand.\n\nSearch your deck for an Item card, a Pokémon Tool "
               "card, a Supporter card, and a Stadium card, reveal them, and put "
               "them into your hand. Then, shuffle your deck.")
        self.assertEqual(
            r["tags"], ["search_multi:item+tool+supporter+stadium:1"])
        self.assertEqual(r["unsupported"], [])
        self.assertEqual(r["cost"], "discard:3")

    def test_ciphermaniac_top(self):
        r = _m("Search your deck for 2 cards, shuffle your deck, then put those "
               "cards on top of it in any order.", kind="supporter")
        self.assertEqual(r["tags"], ["search_top:2"])
        self.assertEqual(r["unsupported"], [])

    def test_dialga_attack_top(self):
        r = _m("Search your deck for 2 cards, shuffle your deck, then put those "
               "cards on top of it in any order.", kind="attack")
        self.assertEqual(r["tags"], ["search_top:2"])


class TestNegative(unittest.TestCase):
    def test_plain_search_not_multi(self):
        r = _m("Search your deck for a Pokémon, reveal it, and put it into your "
               "hand. Then, shuffle your deck.")
        self.assertEqual(r["tags"], ["search_pokemon:1"])
        self.assertFalse([t for t in r["tags"] if t.startswith("search_multi")])

    def test_bottom_still_unmodeled(self):
        r = _m("Search your deck for 2 cards, shuffle your deck, then put those "
               "cards on the bottom of your deck in any order.", kind="supporter")
        self.assertFalse([t for t in r["tags"] if t.startswith("search_top")],
                         "方向相反（放底 ≠ 放顶）不得落 search_top")
        self.assertTrue(r["unsupported"])

    def test_basic_search_not_trolley(self):
        """「up to 3 Basic Pokémon 铺后备」不得退化成 `all`"""
        r = _m("Search your deck for up to 3 Basic Pokémon and put them onto "
               "your Bench. Then, shuffle your deck.")
        self.assertEqual(r["tags"], ["search_bench:3:basic"])


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
