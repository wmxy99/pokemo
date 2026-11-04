# -*- coding: utf-8 -*-
"""第五十~五十二批（2026-11-04）· 解析用例：训练家复合句与两处「看起来已支持」

训练家卡**只认一个卡级 tag**。这些句子原来被通用分支拆成两条 tag
（`discard:all`+`draw:5` / `switch_self`+`draw_to:5` / `gust`+`confuse`）→
建池判「多 tag」→ **整卡不收，而且没有任何理由记录**（9 张训练家静默消失）。
"""
from __future__ import annotations
import unittest

from agent import effects as E


def _m(text, kind="supporter", name="x"):
    sp = E.parse_rule(text, kind=kind, name=name)
    return E.to_engine_tags([sp])


LASER = "Your opponent’s Active Pokémon is now Burned and Confused."
CARMINE = ("If you go first, you may use this card during your first turn.  "
           "Discard your hand and draw 5 cards.")
SURFER = ("Switch your Active Pokémon with 1 of your Benched Pokémon. If you "
          "do, draw cards until you have 5 cards in your hand.")
LISIA = ("Switch in 1 of your opponent’s Benched Basic Pokémon to the Active "
         "Spot. If you do, the new Active Pokémon is now Confused.")


class TestTrainerCompound(unittest.TestCase):
    def test_multi_status(self):
        r = _m(LASER, kind="item")
        self.assertEqual(r["tags"], ["status_multi:burn+confuse"])
        self.assertEqual(r["unsupported"], [])

    def test_multi_status_keeps_both(self):
        """第二个状态不得被丢掉（此前只落 `burn`）"""
        self.assertIn("confuse", _m(LASER, kind="item")["tags"][0])

    def test_research(self):
        self.assertEqual(_m(CARMINE)["tags"], ["research:5"])

    def test_permission_clause_stripped(self):
        """「If you go first …」是**许可**不是条件 —— 剥掉，且不进 unsupported"""
        r = _m(CARMINE)
        self.assertEqual(r["unsupported"], [])

    def test_switch_draw(self):
        self.assertEqual(_m(SURFER)["tags"], ["switch_draw_to:5"])

    def test_gust_confuse(self):
        self.assertEqual(_m(LISIA)["tags"], ["gust_confuse"])

    def test_single_tag_only(self):
        """四条都必须只产出一个卡级 tag（多 tag = 整卡不收）"""
        for t in (LASER, CARMINE, SURFER, LISIA):
            self.assertEqual(len(_m(t)["tags"]), 1, t)

    def test_unrelated_two_effect_text_not_claimed(self):
        """没覆盖的两动作句仍如实未建模（不得硬凑成一条复合 tag）"""
        r = _m("Draw 3 cards. Then, discard 2 cards from your hand.")
        self.assertTrue(len(r["tags"]) != 1 or r["unsupported"])


class TestCaseSensitivityLesson(unittest.TestCase):
    """`re.I` 只影响匹配、**不改捕获的大小写**（本批踩到的坑）"""

    def test_uppercase_status_words_mapped(self):
        # 卡面首字母大写（Burned / Confused），映射表是全小写键
        self.assertEqual(_m(LASER, kind="item")["tags"],
                         ["status_multi:burn+confuse"])

    def test_lowercase_form_also_mapped(self):
        self.assertEqual(_m("your opponent's active pokémon is now burned and "
                            "confused.", kind="item")["tags"],
                         ["status_multi:burn+confuse"])


if __name__ == "__main__":
    unittest.main()
