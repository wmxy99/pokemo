# -*- coding: utf-8 -*-
"""第五十批（2026-11-04）· 解析用例：「对手少拿奖赏卡」族

四种写法只差条件：`from_ex`（被对手 {ex} 招式击倒）、`team_<color>`（同队
「1 of your {X} Pokémon」）、`if_named:<key>`（场上有指定卡）、无前缀（道具）。
"""
from __future__ import annotations
import unittest

from agent import effects as E


def _m(text, kind="ability", name="x"):
    sp = E.parse_rule(text, kind=kind, name=name)
    return E.to_engine_tags([sp])


SHEDINJA = ("If this Pokémon is Knocked Out by damage from an attack from your "
            "opponent’s Pokémon {ex}, your opponent can’t take any Prize cards "
            "for it.")
MEGA_GENGAR = ("If 1 of your {D} Pokémon is Knocked Out by damage from an attack "
               "from your opponent’s Pokémon {ex}, that player takes 1 fewer "
               "Prize card. The effect of Shadowy Concealment doesn’t stack.")
MUNKIDORI = ("If this Pokémon is Knocked Out by damage from an attack from your "
             "opponent’s Pokémon, and if you have any Pecharunt {ex} in play, "
             "your opponent takes 1 fewer Prize card.")
LILLIES_PEARL = ("If the Lillie’s Pokémon this card is attached to is Knocked "
                 "Out by damage from an attack from your opponent’s Pokémon, "
                 "that player takes 1 fewer Prize card.")


class TestKoPrizeMinus(unittest.TestCase):
    def test_no_prize(self):
        r = _m(SHEDINJA)
        self.assertEqual(r["tags"], ["ko_prize_minus:all:from_ex"])
        self.assertEqual(r["unsupported"], [])

    def test_team_variant(self):
        r = _m(MEGA_GENGAR)
        self.assertEqual(r["tags"],
                         ["ko_prize_minus:1:from_ex:team_darkness"])

    def test_if_named_strips_ex_marker(self):
        """「any Pecharunt {ex} in play」—— 键必须是 `pecharunt`，
        `{ex}` 是卡面标记不是卡名的一部分（否则按卡名匹配永远不成立）"""
        r = _m(MUNKIDORI)
        self.assertEqual(r["tags"], ["ko_prize_minus:1:if_named:pecharunt"])

    def test_no_from_ex_when_card_says_so(self):
        """Munkidori 的卡面**没有**「{ex}」限定 → 不得凭空加 `from_ex`"""
        self.assertNotIn("from_ex", _m(MUNKIDORI)["tags"][0])

    def test_tool_subject_form(self):
        r = _m(LILLIES_PEARL, kind="tool", name="Lillie’s Pearl")
        self.assertEqual(r["tags"], ["ko_prize_minus:1"])

    def test_tool_kind_not_claimed_as_item(self):
        """道具走 `item` kind 时**不认领** —— 卡级 tag 需要 `tool:` 前缀，
        在这里产出裸 `ko_prize_minus:` 会被建池判成非法训练家 tag（静默错映射）。"""
        r = _m(LILLIES_PEARL, kind="item", name="Lillie’s Pearl")
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])

    def test_other_counts_not_claimed(self):
        """别的张数（如「takes 2 fewer」）不认领 —— 奖赏数是胜负量纲"""
        r = _m("If this Pokémon is Knocked Out by damage from an attack from "
               "your opponent’s Pokémon, your opponent takes 2 fewer Prize "
               "cards.")
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])

    def test_more_prizes_not_hijacked(self):
        """「take 1 **more** Prize card」是另一条路径（`ko_prize_plus:`），
        不得被本族当成「少拿」"""
        r = _m("If your opponent’s Basic Pokémon is Knocked Out by damage from "
               "an attack used by this Pokémon, take 1 more Prize card.")
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])


if __name__ == "__main__":
    unittest.main()
