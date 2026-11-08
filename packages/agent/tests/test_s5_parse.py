# -*- coding: utf-8 -*-
"""S5（2026-11-08）· K2 解析用例：递归效果 + 复制招式

  · Ethan's Sudowoodo「Try to Imitate」：「Flip a coin. **If heads**, choose 1 of
    your opponent's Active Pokémon's attacks and use it as this attack.」→
    ``coin:heads:borrow_attack:opp_active``（币必须在**外面**）
  · 同一张卡的「Impound」：「During your opponent's next turn, the Defending
    Pokémon can't retreat.」→ ``retreat_lock:opp:turn``（此前被记成
    「action.type 非法」的**假错误**）
  · Team Rocket's Persian ex「Haughty Order」：「Reveal the top 10 cards … You
    **may** choose an attack from a Pokémon you find there …」→
    ``borrow_attack:opp_deck_top:10``
  · Team Rocket's Great Ball（物品）：「Flip a coin. If heads … an Evolution
    Team Rocket's Pokémon … If tails … a Basic Team Rocket's Pokémon …」→
    ``coin_search:evolution:basic:category:teamrocket``
  · Lucian（支援者）：「Each player shuffles their hand and puts it on the
    **bottom** of their deck … **each player flips a coin** … draws 6 / 3」→
    ``shuffle_both_bottom_coin_draw:6:3``

负向守卫：缺「if heads」的普通借用不得产抛币门、牌库顶借用缺张数不得认领、
两侧类别不一致不认领、Lucian 的措辞不得落进 Harlequin 那条（洗牌 vs 放底）。

运行：cd packages/agent && python -m unittest tests.test_s5_parse -v
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

SUDOWOODO = ("Flip a coin. If heads, choose 1 of your opponent’s Active "
             "Pokémon’s attacks and use it as this attack.")
PERSIAN = ("Reveal the top 10 cards of your opponent’s deck. You may choose an "
           "attack from a Pokémon you find there and use it as this attack. "
           "Shuffle the revealed cards into your opponent’s deck.")
GREAT_BALL = ("Flip a coin. If heads, search your deck for an Evolution Team "
              "Rocket’s Pokémon, reveal it, and put it into your hand. If tails, "
              "search your deck for a Basic Team Rocket’s Pokémon, reveal it, "
              "and put it into your hand. Then, shuffle your deck.")
LUCIAN = ("Each player shuffles their hand and puts it on the bottom of their "
          "deck. If either player put any cards on the bottom of their deck in "
          "this way, each player flips a coin. If heads, that player draws 6 "
          "cards. If tails, they draw 3 cards.")


def _m(text: str, kind: str) -> dict:
    sp = E.parse_rule(text, kind=kind, name="X")
    out = E.to_engine_tags([sp])
    return {"tags": out["tags"],
            "unsupported": [u["reason"] for u in out["unsupported"]]}


class TestBorrowForms(unittest.TestCase):
    def test_coin_gate_borrow(self):
        r = _m(SUDOWOODO, "attack")
        self.assertEqual(r["tags"], ["coin:heads:borrow_attack:opp_active"])
        self.assertEqual(r["unsupported"], [])

    def test_plain_borrow_has_no_coin(self):
        """没有「Flip a coin」的借用不得凭空长出一道门（= 静默削弱）"""
        r = _m("Choose 1 of your opponent’s Active Pokémon’s attacks and use "
               "it as this attack.", "attack")
        self.assertEqual(r["tags"], ["borrow_attack:opp_active"])

    def test_retreat_lock(self):
        r = _m("During your opponent’s next turn, the Defending Pokémon "
               "can’t retreat.", "attack")
        self.assertEqual(r["tags"], ["retreat_lock:opp:turn"])

    def test_deck_top_borrow(self):
        r = _m(PERSIAN, "attack")
        self.assertEqual(r["tags"], ["borrow_attack:opp_deck_top:10"])
        self.assertEqual(r["unsupported"], [])

    def test_deck_top_borrow_needs_count(self):
        """缺「the top N cards」→ 不认领（回落兜底，如实未建模）"""
        r = _m("Reveal the top cards of your opponent’s deck. You may choose an "
               "attack from a Pokémon you find there and use it as this "
               "attack. Shuffle the revealed cards into your opponent’s deck.",
               "attack")
        self.assertEqual(r["tags"], [])


class TestCoinSearch(unittest.TestCase):
    def test_split_search(self):
        r = _m(GREAT_BALL, "item")
        self.assertEqual(r["tags"],
                         ["coin_search:evolution:basic:category:teamrocket"])
        # 分词器会额外产出一条无条件 `search`（= 两侧都搜）→ 必须被移除，
        # 否则会登记成「抛币分支内的检索」而整卡拒收。
        self.assertEqual(r["unsupported"], [])

    def test_category_mismatch_refused(self):
        """两侧阵营不同 → 不认领（单条 tag 装不下两种类别）"""
        txt = ("Flip a coin. If heads, search your deck for an Evolution Team "
               "Rocket’s Pokémon, reveal it, and put it into your hand. If "
               "tails, search your deck for a Basic N’s Pokémon, reveal it, "
               "and put it into your hand. Then, shuffle your deck.")
        r = _m(txt, "item")
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"], r)   # 如实列为未建模，不是静默丢弃
        # 两层都要拦：解析层本身不得产出 coin_search —— 只靠 tag 层兜住的话，
        # 「解析认了、tag 拒收」会以 unsupported 的形式**掩盖**解析判据失效。
        _acts = [a.get("type")
                 for a in E.parse_rule(txt, kind="item", name="X")["actions"]]
        self.assertNotIn("coin_search", _acts)

    def test_category_key(self):
        self.assertEqual(E._category_text_key(" Team Rocket’s"), "teamrocket")
        self.assertEqual(E._category_text_key("N’s"), "n")


class TestShuffleBothBottom(unittest.TestCase):
    def test_lucian(self):
        r = _m(LUCIAN, "supporter")
        self.assertEqual(r["tags"], ["shuffle_both_bottom_coin_draw:6:3"])
        self.assertEqual(r["unsupported"], [])

    def test_not_confused_with_harlequin(self):
        """Lucian 的措辞**不得**落进 `shuffle_both_coin_draw`（洗整副 + 单币）"""
        h = ("Each player shuffles their hand into their deck. Then, flip a "
             "coin. If heads, you draw 5 cards, and your opponent draws 3 "
             "cards. If tails, you draw 3 cards, and your opponent draws 5 "
             "cards.")
        r = _m(h, "supporter")
        self.assertEqual(r["tags"], ["shuffle_both_coin_draw:5:3:3:5"])


class TestSchemaNoFalseErrors(unittest.TestCase):
    """ACTION_TYPES / TARGETS 的补登记：池内卡不再带「非法」假错误"""

    def test_new_action_types_registered(self):
        for t in ("retreat_lock", "coin_search",
                  "shuffle_both_bottom_coin_draw", "shuffle_both_coin_draw"):
            self.assertIn(t, E.ACTION_TYPES, t)

    def test_both_is_a_target(self):
        self.assertIn("both", E.TARGETS)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
