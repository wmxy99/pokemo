# -*- coding: utf-8 -*-
"""PokéLab Agent · 「检视牌库顶」扩展解析用例（2026-11-02 第三十五批）

三组句式，各有一处**极易写错**的地方：

A. 「Look at the top card of your deck. You may discard that card.」
   （Morpeko TWM 72 / Rockruff JTG 84 / Litwick WHT 16）
   ⚠ 卡面**没有洗牌句** → `rest:none`。写成 `shuffle` 会让这张卡顺手重洗整副
     牌库。「may discard」是可选，引擎无交互层，缺省取「弃」（带这张卡就是
     为了弃），属确定性默认而非随机。

B. 「Look at the top N cards and attach any number of [Basic] [{X}] Energy ...
   in any way you like」（Lapras ex SCR 32 / Metang TEF 114）
   ⚠ 卡面限定 `Basic` 时必须产出 `basic_energy`，否则特殊能量也会被附上；
     `{M}` 等属性符号必须落到 `basic_energy-metal`，不能退化成 `any`。

C. 「Look at the top N cards. You may put any number of Pokémon onto your Bench」
   （Tatsugiri ex SSP 142）

另守两种**不得认领**的形态：
  · Grumpig MEG 63 的进化触发特性（前缀不同，需要 `evolve_act:` 包装）；
  · 「any number of」但**没有**「牌库顶 N 张」这个封闭上限的句式
    （那需要真正的交互层，不是本批的 `K = all`）。

运行：cd packages/agent && python -m unittest tests.test_recon_ext -v
"""
from __future__ import annotations

import unittest

from agent.effects import _detect_recon_top, parse_rule, to_engine_tags


def _tags(text: str, kind: str = "ability") -> dict:
    return to_engine_tags([parse_rule(text, kind=kind, name="X")])


MORPEKO = ("Once during your turn, you may look at the top card of your deck. "
           "You may discard that card.")
ROCKRUFF = "Look at the top card of your deck. You may discard that card."
LAPRAS = ("Look at the top 20 cards of your deck and attach any number of "
          "Energy cards you find there to your Pokémon in any way you like. "
          "Shuffle the other cards back into your deck.")
METANG = ("Once during your turn, you may look at the top 4 cards of your deck "
          "and attach any number of Basic {M} Energy cards you find there to "
          "your Pokémon in any way you like. Shuffle the other cards and put "
          "them on the bottom of your deck.")
TATSUGIRI = ("Look at the top 10 cards of your deck. You may put any number of "
             "Pokémon you find there onto your Bench. Shuffle the other cards "
             "back into your deck.")
GRUMPIG = ("Once during your turn, when you play this Pokémon from your hand "
           "to evolve 1 of your Pokémon, you may use this Ability. Look at the "
           "top 4 cards of your deck and attach any number of Basic Energy "
           "cards you find there to your Pokémon in any way you like. Shuffle "
           "the other cards back into your deck.")


class TestTopOneDiscard(unittest.TestCase):
    """A：看顶 1 张、可弃"""

    def test_morpeko(self):
        self.assertIn("recon:1:1:any:dest:discard:rest:none",
                      _tags(MORPEKO)["tags"])

    def test_rockruff(self):
        self.assertIn("recon:1:1:any:dest:discard:rest:none",
                      _tags(ROCKRUFF)["tags"])

    def test_no_shuffle_when_card_has_no_shuffle_clause(self):
        """卡面没有洗牌句 → 不得带 `rest:shuffle`（否则会重洗整副牌库）"""
        tags = _tags(ROCKRUFF)["tags"]
        self.assertFalse([t for t in tags if t.endswith(":rest:shuffle")],
                         f"不该洗牌：{tags}")

    def test_detector_shape(self):
        d = _detect_recon_top(ROCKRUFF)
        self.assertIsNotNone(d)
        self.assertEqual(d["look_top"], 1)
        self.assertEqual(d["dest"], "discard")
        self.assertEqual(d["rest"], "none")


class TestAttachAnyNumber(unittest.TestCase):
    """B：取顶 N 张里全部能量、自由分配附着"""

    def test_lapras_all_scan(self):
        tags = _tags(LAPRAS, kind="attack")["tags"]
        self.assertEqual(tags, ["recon:20:all:energy:dest:attach_spread"])

    def test_metang_basic_and_type(self):
        tags = _tags(METANG)["tags"]
        self.assertEqual(
            tags, ["recon:4:all:basic_energy-metal:dest:attach_spread"
                   ":rest:shuffle_bottom"])

    def test_basic_qualifier_is_kept(self):
        """卡面写 Basic → 必须 `basic_energy`，不能放宽成 `energy`"""
        self.assertIn("basic_energy-metal", _tags(METANG)["tags"][0])

    def test_shuffle_bottom_differs_from_shuffle(self):
        """「shuffle … and put them on the bottom」≠「shuffle back into deck」"""
        self.assertIn("rest:shuffle_bottom", _tags(METANG)["tags"][0])
        self.assertNotIn("rest:shuffle_bottom", _tags(LAPRAS, "attack")["tags"][0])

    def test_unknown_type_symbol_not_claimed(self):
        """属性符号认不出 → 不猜（宁可未建模）"""
        text = METANG.replace("{M}", "{Q}")
        self.assertIsNone(_detect_recon_top(text))


class TestBenchAnyNumber(unittest.TestCase):
    """C：取顶 N 张里全部宝可梦铺后备"""

    def test_tatsugiri(self):
        self.assertEqual(_tags(TATSUGIRI)["tags"],
                         ["recon:10:all:pokemon:dest:bench"])


class TestMustNotClaim(unittest.TestCase):
    """两种不得认领的形态"""

    def test_evolve_ability_not_claimed(self):
        """Grumpig 的进化触发特性要走 `evolve_act:` 包装，本批不认领"""
        self.assertIsNone(_detect_recon_top(GRUMPIG))

    def test_open_ended_any_number_not_claimed(self):
        """没有「牌库顶 N 张」上限的 any number 需要交互层 → 不认领"""
        text = ("Look at the top card of your deck. You may discard any number "
                "of cards you find there.")
        self.assertIsNone(_detect_recon_top(text))

    def test_top_n_without_any_number_still_supported(self):
        """同类但 K 固定（非 all）的既有形态不得被本批改动影响"""
        text = ("Look at the top 7 cards of your deck. You may reveal a "
                "Supporter card you find there and put it into your hand. "
                "Shuffle the other cards back into your deck.")
        self.assertEqual(_tags(text, "item")["tags"], ["recon:7:1:supporter"])


if __name__ == "__main__":
    unittest.main()
