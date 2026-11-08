# -*- coding: utf-8 -*-
"""第一百三十五批（S1a）· 解析用例：决策分解（Naveen）

要点：
  · 「弃任意数量手牌」→ `choose:discard_hand:any:then:draw_to:5`（**不是**默认值）；
  · 括注「If you can't draw any cards in this way, you can't use this card.」
    → `play_gate: own_hand_at_most:4`（抽到 5 张；手牌已 ≥5 就一张都抽不到）。
反例：只写「抽到 N 张」、没有弃牌句 → 不得产出 `choose:`（否则会凭空多出选择）。
"""
from __future__ import annotations

import unittest

from agent import effects as E

NAVEEN = ("Draw cards until you have 5 cards in your hand. Before drawing cards, "
          "you may discard any number of cards from your hand. (If you can't "
          "draw any cards in this way, you can't use this card.)")


def _tags(text: str, kind: str = "supporter") -> dict:
    return E.to_engine_tags([E.parse_rule(text, kind=kind, name="X")])


class TestNaveen(unittest.TestCase):
    def test_tag_and_gate(self):
        r = _tags(NAVEEN)
        self.assertEqual(r["tags"], ["choose:discard_hand:any:then:draw_to:5"])
        self.assertEqual(r["play_gate"], "own_hand_at_most:4")
        self.assertEqual(r["unsupported"], [])

    def test_plain_draw_to_is_not_claimed(self):
        r = _tags("Draw cards until you have 5 cards in your hand.")
        self.assertEqual([t for t in r["tags"] if t.startswith("choose:")], [])

    def test_without_gate_sentence_no_gate(self):
        """少了括注 → 不得凭空加门槛（否则卡会「莫名其妙不能用」）"""
        r = _tags("Draw cards until you have 5 cards in your hand. Before drawing "
                  "cards, you may discard any number of cards from your hand.")
        self.assertEqual(r.get("play_gate") or "", "")


if __name__ == "__main__":
    unittest.main()
