# -*- coding: utf-8 -*-
"""第四十二批（2026-11-03）· K2 解析用例：对手宝可梦连同附着卡洗回牌库

覆盖 2 张真实卡（抛币门内的一次性效果）：

  · Sylveon「… choose 1 of your opponent's **Benched** Pokémon. Shuffle that
    Pokémon and all attached cards into their deck.」→
    `coin:heads:shuffle_opp_pokemon:opp_bench`
  · Shiftry「… choose 1 of your opponent's Pokémon. …」→
    `coin:heads:shuffle_opp_pokemon:opp`

负向守卫（都不得被认领成上面任何一种）：
  · 去向不是「their deck」（洗回**手牌**）→ 语义不同；
  · 「choose **2**」→ 只支持 1 只；
  · 洗的是**自己**的宝可梦（`your deck`）→ 完全不同。

运行：cd packages/agent && python -m unittest tests.test_batch42_parse -v
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


SYLVEON = ("Flip a coin. If heads, choose 1 of your opponent's Benched Pokémon. "
           "Shuffle that Pokémon and all attached cards into their deck.")
SHIFTRY = ("Flip a coin. If heads, choose 1 of your opponent's Pokémon. "
           "Shuffle that Pokémon and all attached cards into their deck.")


class TestShuffleOppPokemon(unittest.TestCase):
    def test_benched_target(self):
        self.assertEqual(_m(SYLVEON)["tags"],
                         ["coin:heads:shuffle_opp_pokemon:opp_bench"])

    def test_any_target(self):
        self.assertEqual(_m(SHIFTRY)["tags"],
                         ["coin:heads:shuffle_opp_pokemon:opp"])

    def test_hand_destination_rejected(self):
        """洗回**手牌**不是洗回牌库 → 不得认领"""
        t = ("Flip a coin. If heads, choose 1 of your opponent's Benched Pokémon. "
             "Shuffle that Pokémon into their hand.")
        r = _m(t)
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])

    def test_two_targets_rejected(self):
        """「choose 2」不是本族（只支持 1 只）"""
        t = ("Flip a coin. If heads, choose 2 of your opponent's Benched Pokémon. "
             "Shuffle those Pokémon and all attached cards into their deck.")
        r = _m(t)
        self.assertEqual(r["tags"], [])

    def test_own_deck_rejected(self):
        """洗的是**自己**的宝可梦（your deck）→ 完全不同，不得认领"""
        t = ("Flip a coin. If heads, choose 1 of your Pokémon. Shuffle that "
             "Pokémon and all attached cards into your deck.")
        r = _m(t)
        self.assertEqual(r["tags"], [])


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
