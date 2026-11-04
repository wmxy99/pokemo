# -*- coding: utf-8 -*-
"""PokéLab AI · K2 「检视牌库顶」解析用例（2026-10-30 第二十批）

覆盖 ``recon:<N>:<K>:<sel>[:dest:<d>][:rest:bottom][:gate:<g>]`` 的六张真实卡，
以及**负向守卫**：凡不能整句精确表达的同族写法，必须继续落成未建模
（检视牌库顶一旦被放宽成「任意 N 张入手」，等于凭空多给一张手牌）。

运行：cd packages/agent && python -m unittest tests.test_recon_top -v
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
            "play_gate": out.get("play_gate", ""),
            "unsupported": [u["reason"] for u in out["unsupported"]]}


# ---- 真实卡面（与数据集 cards_challenge.json 逐字一致） ----
POKEGEAR = ("Look at the top 7 cards of your deck. You may reveal a Supporter "
            "card you find there and put it into your hand. Shuffle the other "
            "cards back into your deck.")
BUG_SET = ("Look at the top 7 cards of your deck. You may reveal up to 2 in "
           "any combination of {G} Pokémon and Basic {G} Energy cards you find "
           "there and put them into your hand. Shuffle the other cards back "
           "into your deck.")
DRAYTON = ("Look at the top 7 cards of your deck. You may reveal a Pokémon and "
           "a Trainer card you find there and put them into your hand. Shuffle "
           "the other cards back into your deck.")
HASSEL = ("You can use this card only if any of your Pokémon were Knocked Out "
          "during your opponent\u2019s last turn.  Look at the top 8 cards of "
          "your deck and put up to 3 of them into your hand. Shuffle the other "
          "cards back into your deck.")
GRIMSLEY = ("Look at the top 7 cards of your deck and put a {D} Pokémon you "
            "find there onto your Bench. Shuffle the other cards and put them "
            "on the bottom of your deck. You can\u2019t use this card during "
            "your first turn.")
WAITRESS = ("Look at the top 6 cards of your deck and attach a Basic Energy "
            "card you find there to 1 of your Pokémon. Shuffle the other cards "
            "back into your deck.")
ROTO_STICK = ("Look at the top 4 cards of your deck. You may reveal any number "
              "of Supporter cards you find there and put them into your hand. "
              "Shuffle the other cards back into your deck.")
EXPLORER = ("Look at the top 6 cards of your deck and put 2 of them into your "
            "hand. Discard the other cards.")


class TestReconTopPositive(unittest.TestCase):
    """六张真实卡 → 精确 tag"""

    def test_pokegear_supporter_one(self):
        got = _tags(POKEGEAR)
        self.assertEqual(got["tags"], ["recon:7:1:supporter"])
        self.assertEqual(got["unsupported"], [])

    def test_bug_catching_set_mixed_union(self):
        got = _tags(BUG_SET)
        self.assertEqual(got["tags"],
                         ["recon:7:2:pokemon-grass+energy-grass"])
        self.assertEqual(got["unsupported"], [])

    def test_drayton_one_each(self):
        got = _tags(DRAYTON, kind="supporter")
        # ⚠ `@each`：一张宝可梦 + 一张训练家，**不是**「任意 2 张」
        self.assertEqual(got["tags"], ["recon:7:2:pokemon+trainer@each"])
        self.assertEqual(got["unsupported"], [])

    def test_hassel_with_ko_gate(self):
        """⚠ 第五十九批起，`only if` 句统一由 `_split_play_gate` 剥走并落到
        `play_gate` 字段（训练家卡级，与 `play_cost` 同一层），不再塞进
        `recon:` 的 `:gate:` 段 —— 否则同一个门槛有两套实现，改一处漏一处
        就会静默放行。这里同时锁定 tag 与 play_gate 两侧。
        """
        got = _tags(HASSEL, kind="supporter")
        self.assertEqual(got["tags"], ["recon:8:3:any"])
        self.assertEqual(got["play_gate"], "own_ko_last_turn")
        self.assertEqual(got["unsupported"], [])

    def test_grimsley_bench_bottom_first_turn_gate(self):
        got = _tags(GRIMSLEY, kind="supporter")
        self.assertEqual(
            got["tags"],
            ["recon:7:1:pokemon-darkness:dest:bench:rest:bottom:"
             "gate:not_first_turn"])
        self.assertEqual(got["unsupported"], [])

    def test_waitress_attach_destination(self):
        got = _tags(WAITRESS, kind="supporter")
        self.assertEqual(got["tags"],
                         ["recon:6:1:basic_energy:dest:attach"])
        self.assertEqual(got["unsupported"], [])

    def test_gate_is_not_invented_when_absent(self):
        # 同一句式但**没有**前置条件那句 → 不得凭空加上 ko_last_turn
        no_gate = HASSEL.split(". ", 1)[1]
        got = _tags(no_gate, kind="supporter")
        self.assertEqual(got["tags"], ["recon:8:3:any"])
        self.assertEqual(got["unsupported"], [])

    def test_explorers_guidance_discard_rest(self):
        # 「put 2 of them into your hand. Discard the other cards.」
        # 此前被静默放大成 `search_any:1`（整副牌库任取 1 张、其余不动）
        got = _tags(EXPLORER, kind="supporter")
        self.assertEqual(got["tags"], ["recon:6:2:any:rest:discard"])
        self.assertEqual(got["unsupported"], [])


class TestReconTopNegative(unittest.TestCase):
    """不可精确表达的同族写法必须继续未建模（不得放宽）"""

    def test_any_number_still_unmodeled(self):
        # 「any number of Supporter cards」= 玩家自由数量 → 不许退化成「全部」
        got = _tags(ROTO_STICK)
        self.assertEqual(got["tags"], [])
        self.assertTrue(got["unsupported"])

    def test_unknown_energy_symbol_not_guessed(self):
        bad = BUG_SET.replace("{G} Energy", "{Z} Energy")
        got = _tags(bad)
        self.assertEqual(got["tags"], [])
        self.assertTrue(got["unsupported"])

    def test_plain_top_n_into_hand_without_filter(self):
        # 「Look at the top 3 cards of your deck and put them into your hand.」
        # 是**全部 3 张**入手，不是 recon 的「取 1 张」
        got = _tags("Look at the top 3 cards of your deck and put them into "
                    "your hand. Shuffle the other cards back into your deck.")
        self.assertEqual(got["tags"], [])

    def test_extra_clause_rejected(self):
        # 多一句「Then, draw a card」→ 整句不再精确 → 不认领 recon，且必须带
        # unsupported 让整卡不进池（否则「抽 1 张」会静默顶掉「顶部 7 张检视」）。
        bad = POKEGEAR.replace("Shuffle the other cards back into your deck.",
                               "Shuffle the other cards back into your deck. "
                               "Then, draw a card.")
        got = _tags(bad)
        self.assertEqual([t for t in got["tags"] if t.startswith("recon:")], [])
        self.assertTrue(got["unsupported"])

    def test_discard_destination_now_modeled(self):
        """「You may discard that card」（Morpeko / Rockruff / Litwick）

        2026-11-02 第三十五批：`dest:discard` 已实现（取中那张进弃牌堆）。
        ⚠ `rest:none` 是**必须**的：卡面没有洗牌句，用 `shuffle` 会顺手重洗
          整副牌库。「may discard」在无交互引擎里取确定性默认（弃）。
        """
        got = _tags("Once during your turn, you may look at the top card of "
                    "your deck. You may discard that card.", kind="ability")
        self.assertEqual(got["tags"],
                         ["recon:1:1:any:dest:discard:rest:none"])
        self.assertFalse(got["unsupported"])

    def test_k_greater_than_n_rejected(self):
        bad = HASSEL.replace("up to 3 of them", "up to 9 of them")
        got = _tags(bad, kind="supporter")
        self.assertEqual(got["tags"], [])


if __name__ == "__main__":
    unittest.main()
