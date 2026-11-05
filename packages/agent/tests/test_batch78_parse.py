# -*- coding: utf-8 -*-
"""第七十八批（2026-11-03）· K2 解析用例：抛币免击倒

Mega Hawlucha ex「Tenacious Body」→ ``passive:coin_survive:10``。
负向：缺「flip a coin / If heads」的纯免击倒不得被认领（那是另一机制）。

运行：cd packages/agent && python -m unittest tests.test_batch78_parse -v
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


def _m(text: str) -> dict:
    sp = E.parse_rule(text, kind="ability", name="X")
    out = E.to_engine_tags([sp])
    return {"tags": out["tags"],
            "unsupported": [u["reason"] for u in out["unsupported"]]}


TEXT = ("If this Pokémon would be Knocked Out by damage from an attack, flip a "
        "coin. If heads, this Pokémon is not Knocked Out, and its remaining HP "
        "becomes 10.")


class TestCoinSurvive(unittest.TestCase):
    def test_mega_hawlucha(self):
        r = _m(TEXT)
        self.assertEqual(r["tags"], ["passive:coin_survive:10"])
        self.assertEqual(r["unsupported"], [])

    def test_no_coin_rejected(self):
        t = ("If this Pokémon would be Knocked Out by damage from an attack, it "
             "is not Knocked Out, and its remaining HP becomes 10.")
        r = _m(t)
        self.assertFalse([x for x in r["tags"] if "coin_survive" in x])


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
