# -*- coding: utf-8 -*-
"""第四十批（2026-11-02）· K2 解析用例

覆盖 5 张真实卡的句式（含 1 处**既有缺陷修复**）：

  · Marill TEF 64 / Mudbray DRI 107 / Beartic BLK 26 / Jigglypuff PFL 76
    「Flip a coin until you get tails. This attack does N damage for each heads.」
    → `coin_until_tails:dmg_set:N`（**没有 more** = 印刷伤害按正面数重算）
  · Team Rocket's Diglett ASC 100 → `coin_until_tails:mill:opp`
  · Houndstone MEG 66 → `coin_until_tails:shuffle_opp_hand`
  · Gholdengo MEG 99 → `coin_until_tails:search:any`
  · Team Rocket's Nidorina DRI 115 → `evolve_each_pick:2:type-darkness`
  · Energy Search Pro SSP 176 → `search_energy:all:distinct`（修复：此前「any number」
    被折算成常数或整卡落空）

负向守卫（都不得静默落成上面任何一种）：
  · 「flip until **2** tails」「flip until you get tails」+ 附着 → 不是本族；
  · 「Choose up to N …」但后半句不是进化 → 不认领；
  · 「up to 3 Basic Energy cards」→ `search_energy:3`，不得升级成 `all`。

运行：cd packages/agent && python -m unittest tests.test_batch40_parse -v
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


def _tags(text: str, kind: str = "attack") -> list:
    sp = E.parse_rule(text, kind=kind, name="X")
    return E.to_engine_tags([sp])["tags"]


MARILL = ("Flip a coin until you get tails. This attack does 10 damage "
          "for each heads.")
MUDD = ("Flip a coin until you get tails. This attack does 40 more damage "
        "for each heads.")
BEARTIC = ("Flip a coin until you get tails. This attack does 50 damage "
           "for each heads.")
DIGLETT = ("Flip a coin until you get tails. For each heads, discard the top "
           "card of your opponent's deck.")
HOUNDSTONE = ("Flip a coin until you get tails. For each heads, choose a random "
              "card from your opponent's hand. Your opponent reveals those cards "
              "and shuffles them into their deck.")
GHOLDENGO = ("Flip a coin until you get tails. Search your deck for a number of "
             "cards up to the number of heads and put them into your hand. Then, "
             "shuffle your deck.")
NIDORINA = ("Choose up to 2 of your {D} Pokémon. For each of those Pokémon, "
            "search your deck for a card that evolves from that Pokémon and put "
            "it onto that Pokémon to evolve it. Then, shuffle your deck.")
SPRO = ("Search your deck for any number of Basic Energy cards of different "
        "types, reveal them, and put them into your hand. Then, shuffle your deck.")


class TestUntilTailsDamageSet(unittest.TestCase):
    def test_without_more_is_set(self):
        self.assertEqual(_tags(MARILL), ["coin_until_tails:dmg_set:10"])
        self.assertEqual(_tags(BEARTIC), ["coin_until_tails:dmg_set:50"])

    def test_with_more_is_plus(self):
        """「more」有无是两种语义：加在印刷伤害上 vs 按正面数重算"""
        self.assertEqual(_tags(MUDD), ["coin_until_tails:dmg_plus:40"])


class TestUntilTailsNonDamage(unittest.TestCase):
    def test_mill(self):
        self.assertEqual(_tags(DIGLETT), ["coin_until_tails:mill:opp"])

    def test_shuffle_opp_hand(self):
        self.assertEqual(_tags(HOUNDSTONE),
                         ["coin_until_tails:shuffle_opp_hand"])

    def test_search_any(self):
        self.assertEqual(_tags(GHOLDENGO), ["coin_until_tails:search:any"])

    def test_two_tails_form_not_mapped(self):
        """「flip until **2** tails」张数口径不同，必须如实不认领"""
        t = ("Flip a coin until you get 2 tails. For each heads, discard the top "
             "card of your opponent's deck.")
        self.assertEqual(_tags(t), [])

    def test_attach_until_tails_not_misclassified(self):
        """Snorlax 的「检索能量并附着」不是本族（走 attach_flip_until）"""
        t = ("Flip a coin until you get tails. Search your deck for an amount of "
             "Basic Energy up to the number of heads and attach it to this "
             "Pokémon. Then, shuffle your deck.")
        self.assertEqual(_tags(t), ["attach_flip_until:any:deck"])


class TestEvolveEachPick(unittest.TestCase):
    def test_pick_up_to_two(self):
        self.assertEqual(_tags(NIDORINA), ["evolve_each_pick:2:type-darkness"])

    def test_unknown_target_scope_not_mapped(self):
        """目标限定判不出（「有特性的宝可梦」不在 scope 词表）→ 不得落成本族 tag"""
        t = ("Choose up to 2 of your Pokémon that have an Ability. For each of "
             "those Pokémon, search your deck for a card that evolves from that "
             "Pokémon and put it onto that Pokémon to evolve it. Then, shuffle "
             "your deck.")
        self.assertFalse(any(x.startswith("evolve_each_pick") for x in _tags(t)))

    def test_tera_scope_is_card_subtype(self):
        """Tera 是卡牌子类（`cat-tera`），不是属性 —— 必须按 category 判定"""
        t = ("Choose up to 2 of your Tera Pokémon. For each of those Pokémon, "
             "search your deck for a card that evolves from that Pokémon and put "
             "it onto that Pokémon to evolve it. Then, shuffle your deck.")
        self.assertEqual(_tags(t), ["evolve_each_pick:2:cat-tera"])

    def test_without_evolve_clause_not_mapped(self):
        t = ("Choose up to 2 of your {D} Pokémon. For each of those Pokémon, "
             "attach a Basic {D} Energy card from your discard pile to it.")
        self.assertFalse(any(x.startswith("evolve_each_pick")
                             for x in _tags(t)))


class TestSearchEnergyAnyNumber(unittest.TestCase):
    def test_any_number_of_different_types(self):
        self.assertEqual(_tags(SPRO, kind="item"), ["search_energy:all:distinct"])

    def test_up_to_three_stays_constant(self):
        """「up to 3」上限是常数，不得升级成 `all`（会拿满整库同色）"""
        t = ("Search your deck for up to 3 Basic Energy cards of different types, "
             "reveal them, and put them into your hand. Then, shuffle your deck.")
        self.assertEqual(_tags(t, kind="item"), ["search_energy:3:distinct"])

    def test_any_number_without_distinct_not_mapped(self):
        """「any number」+ 无「不同属性」→ 上限不受约束，需交互层，如实不认领"""
        t = ("Search your deck for any number of Basic Energy cards, reveal "
             "them, and put them into your hand. Then, shuffle your deck.")
        self.assertEqual(_tags(t, kind="item"), [])


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
