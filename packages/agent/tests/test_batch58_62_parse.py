# -*- coding: utf-8 -*-
"""第五十八~六十二批（2026-11-04）· 解析用例

每族都覆盖「正确形态 → 精确 tag」与「近似写法 → 必须不认领」两侧。
只测正向等于橡皮图章：这五批里有四处失败方向是**静默改强度**
（plus/set 反向、gate 与 cost 混淆、team_boost 当 dmg_plus、alt 当加值）。
"""
from __future__ import annotations
import unittest

from agent import effects as E


def _tags(text: str, kind: str = "item", name: str = "X") -> dict:
    out = E.to_engine_tags([E.parse_rule(text, kind=kind, name=name)])
    return {"tags": out["tags"], "play_gate": out.get("play_gate", ""),
            "unsupported": [u["reason"] for u in out["unsupported"]]}


class TestDrawCond(unittest.TestCase):
    """第五十八批：条件抽牌（`more` vs `instead` 方向相反）"""

    def test_more_is_plus(self):
        r = _tags("Draw 2 cards. Then, if you have 10 or more cards in your "
                  "hand, draw 2 more cards.", "supporter")
        self.assertEqual(r["tags"],
                         ["draw_cond:keep:2:plus:2:if:own_hand_at_least:10"])

    def test_instead_is_set(self):
        """`instead` = 换值 ⇒ `set`（落成 `plus` 就是多抽 4 张）"""
        r = _tags("Shuffle your hand into your deck. Then, draw 4 cards. If "
                  "your opponent has 3 or fewer Prize cards remaining, draw 8 "
                  "cards instead.", "supporter")
        self.assertEqual(
            r["tags"], ["draw_cond:shuffle:4:set:8:if:opp_prizes_at_most:3"])

    def test_exactly_prizes(self):
        r = _tags("Shuffle your hand into your deck. Then, draw 6 cards. If "
                  "you have exactly 6 Prize cards remaining, draw 8 cards "
                  "instead.", "supporter")
        self.assertEqual(
            r["tags"], ["draw_cond:shuffle:6:set:8:if:own_prizes_exactly:6"])

    def test_unknown_condition_not_claimed(self):
        """引擎没有的条件 ⇒ 整句不认领（绝不猜一个近似条件顶上）"""
        r = _tags("Draw 2 cards. Then, if you have a Stadium in play, draw 2 "
                  "more cards.", "supporter")
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])


class TestShuffleSplit(unittest.TestCase):
    """第五十八批：双方洗回后各抽**不同**张数"""

    def test_split(self):
        r = _tags("Each player shuffles their hand into their deck. Then, you "
                  "draw 5 cards, and your opponent draws 2 cards.")
        self.assertEqual(r["tags"], ["shuffle_draw_split:5:2"])

    def test_same_count_unchanged(self):
        """同数形态仍走 `draw_both`（本批不得把它也改成 split）"""
        r = _tags("Each player shuffles their hand into their deck. Then, "
                  "draw 3 cards.")
        self.assertNotIn("shuffle_draw_split", str(r["tags"]))


class TestPlayGate(unittest.TestCase):
    """第五十九批：`only if` 前置条件 → `play_gate`（不是 tag，不是 play_cost）"""

    def test_tera_gate(self):
        r = _tags("You can use this card only if you have any Tera Pokémon in "
                  "play. Search your deck for a Supporter card, reveal it, and "
                  "put it into your hand. Then, shuffle your deck.")
        self.assertEqual(r["play_gate"], "tera_in_play")
        self.assertEqual(r["tags"], ["search_trainer:1"])
        self.assertEqual(r["unsupported"], [])

    def test_second_first_turn_gate(self):
        r = _tags("You can use this card only if you go second, and only "
                  "during your first turn. Search your deck for a Supporter "
                  "card, reveal it, and put it into your hand. Then, shuffle "
                  "your deck.")
        self.assertEqual(r["play_gate"], "went_second_first_turn")

    def test_ko_last_turn_gate(self):
        r = _tags("You can use this card only if any of your Pokémon were "
                  "Knocked Out during your opponent’s last turn. Each player "
                  "shuffles their hand into their deck. Then, you draw 5 "
                  "cards, and your opponent draws 2 cards.")
        self.assertEqual(r["play_gate"], "own_ko_last_turn")

    def test_exactly_not_at_most(self):
        """`exactly` 与 `at most` 差一个词 —— 落错 = 门槛放宽到残局"""
        r = _tags("You can use this card only if your opponent has exactly 2 "
                  "Prize cards remaining. Draw 1 card.")
        self.assertEqual(r["play_gate"], "opp_prizes_exactly:2")

    def test_unknown_gate_stays_unmodeled(self):
        """引擎没有的组合条件（六张具名卡全在场）⇒ 如实未建模"""
        r = _tags("You can use this card only if you have N’s Darmanitan, N’s "
                  "Zoroark {ex} and N’s Zekrom in play. Draw 1 card.")
        self.assertTrue(any("打出前置条件" in u for u in r["unsupported"]))

    def test_cost_word_not_taken_as_gate(self):
        """`only if you **discard**` 是**代价**，不是 gate（本批最易混）"""
        r = _tags("You can use this card only if you discard a card from your "
                  "hand. Draw 2 cards.")
        self.assertEqual(r["play_gate"], "")
        self.assertNotEqual(r["tags"], [])


class TestOppEnergyToHand(unittest.TestCase):
    """第五十九批：对手**任意 1 只**身上的能量回其手牌"""

    def test_opp_any(self):
        r = _tags("Put an Energy attached to 1 of your opponent’s Pokémon into "
                  "their hand.")
        self.assertEqual(r["tags"], ["bounce:energy:opp_any:1"])

    def test_self_form_unchanged(self):
        r = _tags("Put an Energy attached to this Pokémon into your hand.",
                  kind="attack")
        self.assertEqual(r["tags"], ["bounce:energy:self:1"])


class TestTeamBoost(unittest.TestCase):
    """第六十批：本回合全队增伤（不是 `dmg_plus`）"""

    def test_type_scope(self):
        r = _tags("During this turn, attacks used by your {F} Pokémon do 30 "
                  "more damage to your opponent’s Active Pokémon (before "
                  "applying Weakness and Resistance).")
        self.assertEqual(r["tags"], ["team_boost:30:type:fighting"])

    def test_cat_scope(self):
        r = _tags("During this turn, attacks used by your Pokémon do 40 more "
                  "damage to your opponent’s Active Pokémon {ex} (before "
                  "applying Weakness and Resistance).",
                  kind="supporter")
        self.assertEqual(r["tags"], ["team_boost:40:cat:ex"])

    def test_two_categories_not_claimed(self):
        """`{ex} and {V}` 是两类目标 —— 单 `cat` 装不下，如实不认领"""
        r = _tags("During this turn, attacks used by your Pokémon do 30 more "
                  "damage to your opponent’s Active Pokémon {ex} and Active "
                  "Pokémon {V} (before applying Weakness and Resistance).",
                  kind="supporter")
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])


class TestExtraAttack(unittest.TestCase):
    """第六十一批：Festival Lead → `passive:extra_attack`"""

    CARD = ("If Festival Grounds is in play, this Pokémon may use an attack it "
            "has twice. If the first attack Knocks Out your opponent’s Active "
            "Pokémon, you may attack again after your opponent chooses a new "
            "Active Pokémon.")

    def test_festival_lead(self):
        r = _tags(self.CARD, kind="ability")
        self.assertEqual(r["tags"], ["passive:extra_attack"])
        self.assertEqual(r["unsupported"], [])

    def test_in_succession_variant(self):
        r = _tags(self.CARD.replace("it has twice.",
                                    "it has twice in succession."),
                  kind="ability")
        self.assertEqual(r["tags"], ["passive:extra_attack"])

    def test_other_stadium_not_claimed(self):
        """点名的不是 Festival Grounds ⇒ 不认领"""
        r = _tags(self.CARD.replace("Festival Grounds", "Some Stadium"),
                  kind="ability")
        self.assertEqual(r["tags"], [])


class TestConditionalHeal(unittest.TestCase):
    """第六十二批：`need`（前提）/ `alt`（instead 换值）"""

    def test_need(self):
        r = _tags("Heal 80 damage from your Active Pokémon that has 3 or more "
                  "Energy attached.")
        self.assertEqual(r["tags"],
                         ["heal:80:active:need:own_active_energy_at_least:3"])

    def test_alt_instead(self):
        r = _tags("Heal 30 damage from your Active Pokémon. If that Pokémon is "
                  "an Arven’s Pokémon, heal 100 damage from it instead.")
        self.assertEqual(r["tags"], ["heal:30:active:alt:100:if_name:arvens"])

    def test_plain_heal_unchanged(self):
        r = _tags("Heal 30 damage from your Active Pokémon.")
        self.assertEqual(r["tags"], ["heal:30:active"])


if __name__ == "__main__":
    unittest.main()
