# -*- coding: utf-8 -*-
"""第八十五~八十九批（2026-11-05）· K2 解析用例

按 class 分批，每批都含**正向落地**与**负向自测**（错误写法必须不被认领）。
运行：cd packages/agent && python -m unittest tests.test_batch85_89_parse -v
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


def _m(text: str, kind: str = "ability") -> dict:
    sp = E.parse_rule(text, kind=kind, name="X")
    out = E.to_engine_tags([sp])
    return {"tags": out["tags"],
            "unsupported": [u["reason"] for u in out["unsupported"]]}


# ------------------------------ 第八十五批 ------------------------------
class TestBatch85LilliePearl(unittest.TestCase):
    """Lillie's Pearl → ko_prize_minus:1（道具所附宝可梦被击倒，对手少拿 1）"""

    def test_lillies_pearl(self):
        o = _m("If the Lillie's Pokémon this card is attached to is Knocked Out "
               "by damage from an attack from your opponent's Pokémon, that "
               "player takes 1 fewer Prize card.", kind="item")
        self.assertEqual(o["tags"], ["ko_prize_minus:1"])
        self.assertEqual(o["unsupported"], [])

    def test_non_ko_prize_untouched(self):
        """其它「道具所附宝可梦」句式不得被误认领（如 +HP 类持有限定）"""
        o = _m("If the Pokémon this card is attached to has a Retreat Cost of "
               "exactly 4, it gets +30 HP.", kind="item")
        self.assertEqual(o["tags"], [])
        self.assertTrue(any("静态" in r for r in o["unsupported"]))

    def test_ex_form_still_recognized(self):
        o = _m("If 1 of your {D} Pokémon is Knocked Out by damage from an attack "
               "from your opponent's Pokémon {ex}, that player can't take any "
               "Prize cards.", kind="ability")
        # 「your {D} Pokémon」+「opponent's Pokémon {ex}」→ 同队+from_ex 双条件
        self.assertTrue(o["tags"][0].startswith("ko_prize_minus:all:from_ex"))
        self.assertIn("team_darkness", o["tags"][0])
        self.assertEqual(o["unsupported"], [])


# ------------------------------ 第八十六批 ------------------------------
class TestBatch86Inteleon(unittest.TestCase):
    """Inteleon「Bring Down」→ knockout:least_hp（剩余 HP 最少者，排除自身）"""

    TEXT = ("Choose a Pokémon in play (yours or your opponent's) that has the "
            "least HP remaining, except for this Pokémon, and it is Knocked Out.")

    def test_bring_down(self):
        o = _m(self.TEXT, kind="attack")
        self.assertEqual(o["tags"], ["knockout:least_hp"])
        self.assertEqual(o["unsupported"], [])

    def test_most_hp_rejected(self):
        """语义相反（most / 最高 HP）不得被误认领为 least_hp"""
        o = _m("Choose a Pokémon in play (yours or your opponent's) that has the "
               "most HP remaining, except for this Pokémon, and it is Knocked "
               "Out.", kind="attack")
        self.assertEqual(o["tags"], [])
        self.assertTrue(any("未建模" in r or "静态" in r or "击倒" in r
                            for r in o["unsupported"]))


# ------------------------------ 第八十七批 ------------------------------
class TestBatch87Hydreigon(unittest.TestCase):
    """Hydreigon ex「Greedy Eater」→ ko_prize_plus:1:opp_basic（对手基础被击倒多拿）"""

    TEXT = ("If your opponent's Basic Pokémon is Knocked Out by damage from an "
            "attack used by this Pokémon, take 1 more Prize card.")

    def test_greedy_eater(self):
        o = _m(self.TEXT, kind="ability")
        self.assertEqual(o["tags"], ["ko_prize_plus:1:opp_basic"])
        self.assertEqual(o["unsupported"], [])

    def test_evolved_rejected(self):
        """限定词「Basic」缺失 / 换成 Evolved → 不得认领为 opp_basic"""
        o = _m("If your opponent's Evolved Pokémon is Knocked Out by damage from "
               "an attack used by this Pokémon, take 1 more Prize card.",
               kind="ability")
        self.assertEqual(o["tags"], [])
        self.assertTrue(o["unsupported"], "非 Basic 限定应如实未建模")


# ------------------------------ 第八十八批 ------------------------------
class TestBatch88HandEnergy(unittest.TestCase):
    """单数「Discard a Basic {G} Energy card from your hand」→ discard_hand_energy"""

    TEXT = ("Discard a Basic {G} Energy card from your hand. If you can't, "
            "this attack does nothing.")

    def test_singular_required(self):
        o = _m(self.TEXT, kind="attack")
        self.assertEqual(o["tags"],
                         ["discard_hand_energy:1:type:grass:required"])
        self.assertEqual(o["unsupported"], [])

    def test_singular_no_gate(self):
        o = _m("Discard a Basic {G} Energy card from your hand.", kind="attack")
        self.assertEqual(o["tags"], ["discard_hand_energy:1:type:grass"])

    def test_typed_variant(self):
        o = _m("Discard a Basic {R} Energy card from your hand.", kind="attack")
        self.assertEqual(o["tags"], ["discard_hand_energy:1:type:fire"])


# ------------------------------ 第八十九批 ------------------------------
class TestBatch89PrizeBonus(unittest.TestCase):
    """训练家本回合多拿奖赏：Briar（Tera）/ Anthea & Concordia（N's）"""

    BRIAR = ("During this turn, if your opponent's Active Pokémon is Knocked Out "
             "by damage from an attack used by your Tera Pokémon, take 1 more "
             "Prize card.")
    BRIAR_FULL = ("You can use this card only if your opponent has exactly 2 "
                  "Prize cards remaining.\n\n" + BRIAR)

    def test_briar_tera(self):
        o = _m(self.BRIAR, kind="supporter")
        self.assertEqual(o["tags"], ["prize_bonus:1:tera"])
        self.assertEqual(o["unsupported"], [])

    def test_briar_full_text_gate_stripped(self):
        """真实卡面含「only if 对手剩 2 奖赏」gate —— `_split_play_gate` 剥走后
        效果句仍应正常认领（gate 由 play_gate 承载，不是本函数）"""
        o = _m(self.BRIAR_FULL, kind="supporter")
        self.assertEqual(o["tags"], ["prize_bonus:1:tera"])

    def test_nteam_effect_sentence(self):
        """单独的效果句识别为 nteam（Anthea 的效果侧）"""
        o = _m("During this turn, if your opponent's Active Pokémon is Knocked "
               "Out by damage from an attack used by your N's Pokémon, take 3 "
               "more Prize cards.", kind="supporter")
        self.assertEqual(o["tags"], ["prize_bonus:3:nteam"])

    def test_anthea_unmodeled_gate_rejected(self):
        """Anthea 真实卡面含**未建模**的「6 张具名宝可梦在场」gate → 整卡拒收
        （否则 gate 被静默丢弃 = 无条件 +3 奖赏的静默 buff）"""
        o = _m("You can use this card only if you have N's Darmanitan, N's "
               "Zoroark {ex}, N's Vanilluxe, N's Klinklang, N's Reshiram, and "
               "N's Zekrom in play.\n\nDuring this turn, if your opponent's "
               "Active Pokémon is Knocked Out by damage from an attack used by "
               "your N's Pokémon, take 3 more Prize cards.", kind="supporter")
        self.assertEqual(o["tags"], [])
        self.assertTrue(o["unsupported"], "未建模 gate 应使整卡如实未建模")

    def test_attack_side_not_claimed(self):
        """该句式只在训练家卡级认领，招式/特性侧不得冒充（否则整卡错位）"""
        o = _m(self.BRIAR, kind="attack")
        self.assertEqual(o["tags"], [])


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
