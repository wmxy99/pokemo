# -*- coding: utf-8 -*-
"""PokéLab Agent · 「能量代价去向」与「代价子句守卫」用例（2026-11-02 第三十四批）

本批修掉两类**静默错映射**，两类都会让对局算错而测试全绿：

  A. 「Discard (up to) N Energy cards from **your hand**」被映射成
     ``discard_energy:N`` —— 引擎该 tag 的语义是「先弃**战斗位宝可梦身上的**
     能量，不足再从手牌补」。于是「从手牌弃」的卡会去拔自己场上宝可梦的能量，
     手牌反而没动（Wugtrio ex / Mawile / Mega Clefable ex 三张真实卡）。
     正确去向是 ``discard_hand_energy:N``（只动手牌、不足则弃现有的）。

  B. 「Discard / Shuffle … Energy …, **and** this attack does …」里的**代价**
     整句被吞掉，招式退化成「不付代价白拿附加效果」（静默加强）：
       · Mega Eelektross ex「You may discard 2 {L} Energy … and make … Paralyzed」
       · Mega Skarmory ex「Shuffle all Energy attached to this Pokémon into your
         deck, and this attack does 220 damage …」
       · Mega Charizard X ex「Discard any amount of {R} Energy …」（连弃能动作
         都没有，伤害计数恒 0，反向削弱）
     三者都无法用现有 tag 精确表达 → 新增通用守卫如实拒绝。

运行：cd packages/agent && python -m unittest tests.test_energy_cost_guard -v
"""
from __future__ import annotations

import unittest

from agent.effects import parse_rule, to_engine_tags


def _tags(text: str, kind: str = "attack") -> dict:
    return to_engine_tags([parse_rule(text, kind=kind, name="X")])


WUGTRIO = ("Discard up to 3 Energy cards from your hand. This attack does 60 "
           "damage to 1 of your opponent's Pokémon for each Energy card you "
           "discarded in this way.")
MAWILE = ("Discard up to 2 Energy cards from your hand, and this attack does "
          "60 damage for each card you discarded in this way.")
CLEFABLE = ("You may discard up to 4 Energy cards from your hand, and this "
            "attack does 40 more damage for each card you discarded in this way.")
DIANCIE = ("Discard up to 2 Energy cards from this Pokémon, and this attack "
           "does 120 damage for each card you discarded in this way.")
EELEKTROSS = ("You may discard 2 {L} Energy from this Pokémon and make your "
              "opponent's Active Pokémon Paralyzed.")
SKARMORY = ("Shuffle all Energy attached to this Pokémon into your deck, and "
            "this attack does 220 damage to 1 of your opponent's Pokémon.")
CHARIZARD_X = ("Discard any amount of {R} Energy from among your Pokémon, and "
               "this attack does 90 damage for each card you discarded in this "
               "way.")


class TestHandEnergyDestination(unittest.TestCase):
    """A：「从手牌弃能量」必须落成 discard_hand_energy，而不是 discard_energy"""

    def test_wugtrio_uses_hand_energy(self):
        tags = _tags(WUGTRIO)["tags"]
        self.assertIn("discard_hand_energy:3", tags)
        # 反向守卫：绝不能同时出现「从场上弃」的同名动作
        self.assertFalse([t for t in tags if t.startswith("discard_energy:")],
                         f"手牌弃能不该产出 discard_energy：{tags}")

    def test_mawile_uses_hand_energy(self):
        self.assertIn("discard_hand_energy:2", _tags(MAWILE)["tags"])

    def test_clefable_uses_hand_energy(self):
        self.assertIn("discard_hand_energy:4", _tags(CLEFABLE)["tags"])

    def test_from_this_pokemon_stays_on_field(self):
        """反向：从**自身**弃的卡不能落成手牌弃能（否则代价落到手牌上）"""
        tags = _tags(DIANCIE)["tags"]
        self.assertIn("discard_energy:2", tags)
        self.assertNotIn("discard_hand_energy:2", tags)

    def test_discard_all_energy_from_self(self):
        """独立代价句（无 and 绑定）仍按自身弃置，不被手牌分支抢走"""
        self.assertIn("discard_energy:all",
                      _tags("Discard all Energy from this Pokémon.")["tags"])

    def test_discard_energy_from_opponent(self):
        """对手战斗位仍带 :opp，不能被手牌分支或自身分支吞掉"""
        self.assertIn("discard_energy:1:opp",
                      _tags("Discard an Energy from your opponent's Active "
                            "Pokémon.")["tags"])


class TestEnergyCostGuard(unittest.TestCase):
    """B：代价子句被吞掉时如实拒绝（宁可未建模，绝不白嫖附加效果）"""

    def _has_cost_gap(self, text: str) -> bool:
        return any(u.get("type") == "cost"
                   for u in _tags(text)["unsupported"])

    def test_eelektross_rejected(self):
        """可选能量代价 + 麻痹：代价不可表达 → 必须报 unsupported"""
        self.assertTrue(self._has_cost_gap(EELEKTROSS))

    def test_skarmory_rejected(self):
        """「洗回牌库」引擎无对应语义 → 必须报 unsupported"""
        self.assertTrue(self._has_cost_gap(SKARMORY))

    def test_charizard_x_rejected(self):
        """「any amount」需交互层 → 必须报 unsupported"""
        self.assertTrue(self._has_cost_gap(CHARIZARD_X))

    def test_mapped_cost_is_not_flagged(self):
        """代价已被表达的三张不得误报（否则会白丢可用卡）"""
        for text in (DIANCIE, CLEFABLE, MAWILE):
            self.assertFalse(self._has_cost_gap(text),
                             f"代价已映射却被判缺失：{text[:40]}")

    def test_standalone_cost_clause_not_flagged(self):
        """代价自成一句（句号分开）不属于守卫范围"""
        self.assertFalse(self._has_cost_gap(
            "Discard up to 3 Energy cards from your hand. This attack does 60 "
            "damage to 1 of your opponent's Pokémon."))
        self.assertFalse(self._has_cost_gap(
            "Discard all Energy from this Pokémon."))

    def test_ability_not_affected(self):
        """守卫只作用于招式（特性侧的代价另有 evolve_act 等通道）"""
        r = _tags(EELEKTROSS, kind="ability")
        self.assertFalse([u for u in r["unsupported"] if u.get("type") == "cost"])


if __name__ == "__main__":
    unittest.main()
