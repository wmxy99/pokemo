# -*- coding: utf-8 -*-
"""第五十四批（2026-11-04）· 解析用例：**对手 / 双方**弃手牌

引擎早就有 `discard_opp_random:N`（对手自选 ⇒ 随机）与 `discard_opp_to:N`
（弃到剩 N 张），但**解析侧从来没产出过** —— 6 张卡因此整卡进不了池。

⚠ 口径方向（本批最容易错的地方）：
  · 「Your opponent discards … from their hand」= **对手选** ⇒ 随机（本批）；
  · 「Your opponent reveals their hand, and **you discard** a card you find
    there」= **我方选** ⇒ `opp_hand:`（第三十二批）——两者不可互换。
"""
from __future__ import annotations
import unittest

from agent import effects as E


def _tags(text, kind="attack", name="x"):
    sp = E.parse_rule(text, kind=kind, name=name)
    return E.to_engine_tags([sp])


class TestOppDiscard(unittest.TestCase):
    def test_single(self):
        self.assertEqual(
            _tags("Your opponent discards a card from their hand.")["tags"],
            ["discard_opp_random:1"])

    def test_two(self):
        self.assertEqual(
            _tags("Your opponent discards 2 cards from their hand.")["tags"],
            ["discard_opp_random:2"])

    def test_discard_to_five(self):
        r = _tags("Discard random cards from your opponent’s hand until they "
                  "have 5 cards in their hand.")
        self.assertEqual(r["tags"], ["discard_opp_to:5"])

    def test_both_players_to_five(self):
        """「Each player」必须是**双方**，只弃对手 = 我方手牌没减（静默变弱）"""
        r = _tags("Each player discards cards from their hand until they have 5 "
                  "cards in their hand. Your opponent discards first. "
                  "(If a player has 5 or fewer cards in their hand, they do not "
                  "discard.)", kind="item")
        self.assertEqual(r["tags"], ["discard_to_both:5"])

    def test_pay_then_discard(self):
        """付账在前：`discard:1` 必须先扣自己手牌，否则「If you do」是空话"""
        r = _tags("Discard a card from your hand. If you do, your opponent "
                  "discards a card from their hand.")
        self.assertEqual(r["tags"], ["discard:1", "discard_opp_random:1"])

    def test_self_discard_not_claimed(self):
        """近邻写法：只弃**自己**手牌不能落成本族（否则凭空弃对手）"""
        self.assertEqual(_tags("Discard 2 cards from your hand.")["tags"],
                         ["discard:2"])

    def test_pure_reveal_is_info_only(self):
        """只有揭示 → `info:reveal_hand`（第一百三十四批改判）

        旧口径：状态变化为零 → **故意**保持未建模（C5「不拿进池但无效果充数」）。
        改判理由：本引擎是**完美信息**的（双方手牌/牌库对决策方完全可见），
        「展示手牌」在状态层面等价于无效果 —— 但**等价要显式写出来**：
        现在落 `info:reveal_hand`，引擎有对应派发与日志，因此
        「进池 + 永远不会调用到」与「未建模」可区分（对账能看出来）。
        ⚠ 仍然**不得**落成任何 `opp_hand:` / 弃牌类 tag（那才是造假）。
        """
        r = _tags("Your opponent reveals their hand.")
        self.assertEqual(r["tags"], ["info:reveal_hand"])
        self.assertFalse(any(t.startswith("opp_hand:") for t in r["tags"]),
                         "纯展示不得落成手牌操作 tag")
        self.assertEqual(r["unsupported"], [])
