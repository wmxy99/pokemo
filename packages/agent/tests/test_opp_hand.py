# -*- coding: utf-8 -*-
"""PokéLab AI · K2「对手手牌操作」解析用例（2026-10-30 第三十二批）

覆盖 ``opp_hand:<op>:<sel>[:<n>|:all]`` 的五张真实卡，以及**负向守卫**：

  · 只有「揭示手牌」而**没有**后续操作 → 必须继续未建模（状态变化为零，
    收进池只会给 AI 多出一个零收益合法招式）；
  · 句中出现的「reveals their hand」（前置语 / 多句式）→ 不得认领；
  · 整句多一个尾巴 → 不得认领（只认句首却不做整句匹配会静默吞掉后半句）；
  · 特性形态（Mandibuzz / Lillie's Ribombee）→ 本批不认领。

运行：cd packages/agent && python -m unittest tests.test_opp_hand -v
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


def _tags(text: str, kind: str = "item") -> dict:
    out = E.to_engine_tags([E.parse_rule(text, kind=kind, name="X")])
    return {"tags": out["tags"],
            "unsupported": [u["reason"] for u in out["unsupported"]]}


# ---- 真实卡面（与数据集 cards_challenge.json 逐字一致） ----
ROTOM = ("Your opponent reveals their hand. Discard all Item cards and "
         "Pok\u00e9mon Tool cards you find there.")
PURRLOIN = ("Your opponent reveals their hand, and you choose a card you find "
            "there and put it on the bottom of their deck.")
LUXRAY = "Your opponent reveals their hand, and you discard a card you find there."
SWATTER = ("Your opponent reveals their hand, and you choose an Energy card "
           "you find there and put it on the bottom of their deck.")
ERI = ("Your opponent reveals their hand, and you discard up to 2 Item cards "
       "you find there.")
# 只有揭示，没有后续操作
SEE_THROUGH = "Your opponent reveals their hand."
# 特性形态（前置语 + 往对手后备放）——本批**不**认领
MANDIBUZZ = ("Once during your turn, you may use this Ability. Your opponent "
             "reveals their hand, and you put a Basic Pok\u00e9mon with 70 HP "
             "or less that you find there onto your opponent\u2019s Bench.")
RIBOMBEE = ("When you play this Pok\u00e9mon from your hand to evolve 1 of your "
            "Pok\u00e9mon during your turn, you may have your opponent reveal "
            "their hand and you put any number of Basic Pok\u00e9mon you find "
            "there onto their Bench.")


class TestRealCards(unittest.TestCase):
    """五张真实卡 → 精确 tag"""

    def test_rotom_discard_all_item_and_tool(self):
        self.assertEqual(_tags(ROTOM, "attack")["tags"],
                         ["opp_hand:discard:item+tool:all"])

    def test_purrloin_bottom_any(self):
        self.assertEqual(_tags(PURRLOIN, "attack")["tags"],
                         ["opp_hand:bottom:any"])

    def test_luxray_discard_any(self):
        self.assertEqual(_tags(LUXRAY, "attack")["tags"],
                         ["opp_hand:discard:any"])

    def test_energy_swatter_bottom_energy(self):
        self.assertEqual(_tags(SWATTER, "item")["tags"],
                         ["opp_hand:bottom:energy"])

    def test_eri_up_to_two_items(self):
        self.assertEqual(_tags(ERI, "supporter")["tags"],
                         ["opp_hand:discard:item:2"])

    def test_no_unsupported_on_recognized(self):
        for text, kind in ((ROTOM, "attack"), (PURRLOIN, "attack"),
                           (LUXRAY, "attack"), (SWATTER, "item"),
                           (ERI, "supporter")):
            self.assertEqual(_tags(text, kind)["unsupported"], [], text)


class TestRevealOnlyNotMapped(unittest.TestCase):
    """只有揭示、没有后续操作 → 落 `info:reveal_hand`（第一百三十四批改判）

    旧口径「继续未建模」的理由是「不拿**进池但无效果**充数」。改判后仍然守住
    同一条底线：**不得**落成 `opp_hand:` 之类的操作 tag（那才是「进池但行为错」）；
    `info:reveal_hand` 是完美信息下的**显式**等价，引擎里真有派发与日志。
    """

    def test_see_through_has_only_info_tag(self):
        r = _tags(SEE_THROUGH, "attack")
        self.assertEqual([t for t in r["tags"] if t.startswith("opp_hand:")], [])
        self.assertEqual(r["tags"], ["info:reveal_hand"])
        self.assertEqual(r["unsupported"], [])


class TestNegativeGuards(unittest.TestCase):
    """凡不能整句精确表达的同族写法，必须继续落成未建模"""

    def test_trailing_clause_rejected(self):
        """多一句别的效果 → 不认领（否则那句被静默吞掉）"""
        text = LUXRAY + " Then, draw a card."
        r = _tags(text, "attack")
        self.assertEqual([t for t in r["tags"] if t.startswith("opp_hand:")], [])

    def test_mid_sentence_occurrence_rejected(self):
        """句中（非句首）出现 → 不认领：否则前半句的正当动作会被整段吞掉"""
        text = ("This attack does 30 damage. Your opponent reveals their hand, "
                "and you discard a card you find there.")
        r = _tags(text, "attack")
        self.assertEqual([t for t in r["tags"] if t.startswith("opp_hand:")], [])

    def test_ability_forms_rejected(self):
        for text in (MANDIBUZZ, RIBOMBEE):
            r = _tags(text, "ability")
            self.assertEqual([t for t in r["tags"] if t.startswith("opp_hand:")],
                             [], text[:40])

    def test_numeric_discard_without_verb_is_not_confused(self):
        """「discard 2 cards you find there」不是本族句式（缺 you/choose 语序），
        不得被放宽认领成 `opp_hand:discard:any:2`"""
        text = ("Your opponent reveals their hand, and you discard 2 cards you "
                "find there.")
        r = _tags(text, "supporter")
        self.assertEqual([t for t in r["tags"] if t.startswith("opp_hand:")], [])

    def test_special_energy_not_confused_with_energy(self):
        """「a Special Energy card」不得被当成任意能量（类别限定丢失 = 拿错牌）"""
        text = ("Your opponent reveals their hand, and you choose a Special "
                "Energy card you find there and put it on the bottom of their "
                "deck.")
        r = _tags(text, "item")
        self.assertEqual([t for t in r["tags"] if t.startswith("opp_hand:")], [])


class TestTextNormalization(unittest.TestCase):
    """`_parse_rule_en` 传入的文本**未归一化**：弯引号 / 换行 / 大小写都要自己折叠

    这一族卡面本身没有撇号，但同一函数后面还要跑 recon 等族（Hassel 的
    ``opponent’s`` 曾因此整句失配），所以归一化必须由 `_norm_prose` 统一负责。
    """

    def test_norm_prose_folds_quotes_and_case(self):
        raw = "Your  Opponent\u2019s Reveals\u2014Their\n\nHand"
        self.assertEqual(E._norm_prose(raw),
                         "your opponent's reveals-their hand")

    def test_multiline_text_matches(self):
        text = LUXRAY.replace(" ", "\n ", 3)
        self.assertEqual(_tags(text, "attack")["tags"],
                         ["opp_hand:discard:any"])


if __name__ == "__main__":
    unittest.main()
