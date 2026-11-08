# -*- coding: utf-8 -*-
"""S6a · 解析用例：先手首回合的**例外许可**

卡面两句（写法不同、都是「例外」而不是效果）：

  · 「If you go first, you can use this **attack** during your first turn.」
    → 招式级 `use_any:first_turn`（Volbeat「Quick Sign」/ Exeggcute
      「Precocious Evolution」/ Delibird「Quick Gift」/ Tapu Koko「Fast Flight」）
  · 「If you go first, you may use this **card** during your first turn.」
    → 卡级 `use_any:first_turn`（Carmine / Team Rocket's Proton，都是 Supporter）

为什么必须建模：S6a 打开了「先手 T1 禁攻 / 禁 Supporter」，这两句是**唯一的放行口**。
漏产 tag 时后半句仍照常解析 → `unsupported` 为空、覆盖率不掉，属于**看不见的削弱**
（S6 之前"剥掉即可"的前提是引擎不限制，已不成立）。

运行：cd packages/agent && python -m unittest tests.test_s6a_parse -v
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

ATK_PREFIX = ("If you go first, you can use this attack during your first "
              "turn. ")
CARD_PREFIX = ("If you go first, you may use this card during your first "
               "turn. ")


def _tags(text: str, kind: str) -> list:
    sp = E.parse_rule(text, kind=kind, name="X")
    return E.to_engine_tags([sp])["tags"]


class TestAttackBypass(unittest.TestCase):
    def test_four_shapes(self):
        cases = {
            "search_bench:2:basic":
                ATK_PREFIX + "Search your deck for up to 2 Basic Pokémon and "
                             "put them onto your Bench. Then, shuffle your deck.",
            "research:5":
                ATK_PREFIX + "Discard your hand and draw 5 cards.",
            "search_any:1":
                ATK_PREFIX + "Search your deck for a card and put it into your "
                             "hand. Then, shuffle your deck.",
            "evolve_from_deck":
                ATK_PREFIX + "Search your deck for a card that evolves from "
                             "this Pokémon and put it onto this Pokémon to "
                             "evolve it. Then, shuffle your deck.",
        }
        for want, text in cases.items():
            tags = _tags(text, "attack")
            self.assertIn(want, tags, f"{want} 主效果丢失：{tags}")
            self.assertIn("use_any:first_turn", tags,
                          f"{want} 缺例外许可：{tags}")

    def test_without_prefix_no_tag(self):
        """没有许可句 → 不得凭空产 tag（否则任何招式都能在 T1 打）"""
        tags = _tags("Discard your hand and draw 5 cards.", "attack")
        self.assertNotIn("use_any:first_turn", tags)


class TestCardBypass(unittest.TestCase):
    def test_supporter_forms(self):
        for text, want in (
            (CARD_PREFIX + "Discard your hand and draw 5 cards.", "research:5"),
            (CARD_PREFIX + "Search your deck for up to 3 Basic Team Rocket’s "
                           "Pokémon, reveal them, and put them into your hand. "
                           "Then, shuffle your deck.",
             "search_pokemon:3"),
        ):
            tags = _tags(text, "supporter")
            self.assertIn(want, tags, tags)
            self.assertIn("use_any:first_turn", tags, tags)

    def test_card_without_prefix(self):
        tags = _tags("Discard your hand and draw 5 cards.", "supporter")
        self.assertEqual(tags, ["research:5"])


class TestMainEffectStillParsed(unittest.TestCase):
    """例外句被剥掉后，主效果必须回到句首 —— 否则整句认领会退化成散动作"""

    def test_research_stays_single_tag(self):
        tags = _tags(ATK_PREFIX + "Discard your hand and draw 5 cards.",
                     "attack")
        self.assertEqual(tags, ["research:5", "use_any:first_turn"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
