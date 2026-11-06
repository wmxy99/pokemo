# -*- coding: utf-8 -*-
"""第九十三/九十四批（2026-11-05）· 解析用例

这 5 条句式此前全部落 `unsupported`（`条件伤害缩放：条件/计数源未识别`），
整卡因此被踢出卡池。真实缺口**不是**引擎缺能力 ——
`opp_active_is_ex` / `opp_ex` / `both_field_tools` / `supporter_name`
早就在引擎里，缺的是「英文短语 → 引擎条件名」这一层映射。

本文件同时钉住**负向近邻**：语义相邻但口径不同的写法必须仍被拒，
否则会变成「凭空加伤」的静默错映射。
"""
from __future__ import annotations

import unittest

from agent import effects as E


def _m(text: str, kind: str = "attack") -> dict:
    sp = E.parse_rule(text, kind=kind, name="X")
    return E.to_engine_tags([sp])


class TestBatch93Conditions(unittest.TestCase):
    def test_ex_or_v_bonus(self):
        """Galvantula ex「Charged Web」"""
        o = _m("If your opponent's Active Pokémon is a Pokémon {ex} or Pokémon "
               "{V}, this attack does 110 more damage.")
        self.assertEqual(o["tags"], ["dmg_plus:110:if:opp_active_is_ex_or_v"])
        self.assertEqual(o["unsupported"], [])

    def test_plain_ex_keeps_old_condition(self):
        """近邻：只写 {ex} 时必须仍落旧的 `opp_active_is_ex`（不得被新规则吃掉）"""
        o = _m("If your opponent's Active Pokémon is a Pokémon {ex}, this attack "
               "does 50 more damage.")
        self.assertEqual(o["tags"], ["dmg_plus:50:if:opp_active_is_ex"])

    def test_played_supporter_named(self):
        """Team Rocket's Kangaskhan ex「Wicked Impact」"""
        o = _m("If you played a Supporter card that has “Team Rocket” in its name "
               "from your hand during this turn, this attack does 100 more damage.")
        self.assertEqual(o["tags"],
                         ["dmg_plus:100:if:played_supporter_named:teamrocket"])
        self.assertEqual(o["unsupported"], [])

    def test_supporter_without_quoted_name_rejected(self):
        """近邻：没有引号卡名限定 → 不能当成「打过某类支援者」"""
        o = _m("If you played a Supporter card from your hand during this turn, "
               "this attack does 100 more damage.")
        self.assertEqual(o["tags"], [])
        self.assertTrue(o["unsupported"], "无具名限定应如实未建模")

    def test_bench_named_has_damage(self):
        """Pangoro「Master's Punch」"""
        o = _m("If any of your Benched Pancham have any damage counters on them, "
               "this attack does 120 more damage.")
        self.assertEqual(o["tags"],
                         ["dmg_plus:120:if:own_bench_named_has_damage:pancham"])
        self.assertEqual(o["unsupported"], [])

    def test_bench_without_name_rejected(self):
        """近邻：不具名（任意后备有伤）→ 不得落成具名条件（会漏判）"""
        o = _m("If any of your Benched Pokémon have any damage counters on them, "
               "this attack does 40 more damage.")
        self.assertFalse(any("own_bench_named_has_damage" in t for t in o["tags"]),
                         o["tags"])


class TestBatch94Counters(unittest.TestCase):
    def test_opp_ex_or_v_count(self):
        """Zoroark「Illusory Hijacking」——按计数源**重算**基础伤害（dmg_set）"""
        o = _m("This attack does 60 damage for each of your opponent's Pokémon {ex} "
               "and Pokémon {V} in play.")
        self.assertEqual(o["tags"], ["dmg_set:60:per:opp_ex_or_v"])
        self.assertEqual(o["unsupported"], [])

    def test_plain_ex_keeps_plain_counter(self):
        """近邻：只写 {ex} → 仍落既有的 `opp_ex`（不被新变体吃掉）"""
        o = _m("This attack does 30 damage for each of your opponent's Pokémon {ex} "
               "in play.")
        self.assertEqual(o["tags"], ["dmg_set:30:per:opp_ex"])

    def test_both_field_tools(self):
        """Bronzong「Tool Drop」——全部宝可梦身上的道具（双方合计）"""
        o = _m("This attack does 40 damage for each Pokémon Tool attached to all "
               "Pokémon.")
        self.assertEqual(o["tags"], ["dmg_set:40:per:both_field_tools"])
        self.assertEqual(o["unsupported"], [])

    def test_single_holder_tool_rejected(self):
        """近邻：「attached to this Pokémon」是单只口径 → 不得映射成全场总数"""
        o = _m("This attack does 30 damage for each Pokémon Tool attached to this "
               "Pokémon.")
        self.assertEqual(o["tags"], [])
        self.assertTrue(o["unsupported"], "单只道具不是全场总数，须如实拒收")


class TestBatch97CountersChooseN(unittest.TestCase):
    """「choose N of your opponent's Pokémon and put M damage counters on each of
    them」（Team Rocket's Crobat ex「Biting Spree」）

    引擎 `counters:M:scope:opp:N` 就是「选 N 只、每只各 M 个」；进化触发由
    `to_engine_tags` 依 trigger 自动加 `evolve_act:` 包装。
    """

    TXT = ("When you play this Pokémon from your hand to evolve 1 of your Pokémon "
           "during your turn, you may choose 2 of your opponent's Pokémon and put "
           "2 damage counters on each of them.")

    def test_evolve_trigger_wrapped(self):
        o = _m(self.TXT, kind="ability")
        self.assertEqual(o["tags"], ["evolve_act:counters:2:scope:opp:2"])
        self.assertEqual(o["unsupported"], [])

    def test_single_target_keeps_old_form(self):
        """近邻：「on 1 of your opponent's Pokémon」不得被新规则吞成 each-of"""
        o = _m("Put 3 damage counters on 1 of your opponent's Pokémon.",
               kind="attack")
        self.assertEqual(o["tags"], ["counters:3:scope:opp:1"])

    def test_all_targets_keeps_old_form(self):
        """近邻：「on each of your opponent's Pokémon」（无 choose N）保持不变"""
        o = _m("Place 2 damage counters on each of your opponent's Pokémon.",
               kind="attack")
        self.assertEqual(o["tags"], ["counters:2:scope:opp:all"])

    def test_conditional_choose_rejected(self):
        """带前置条件的 choose-N 不得当成 unconditional（会变成必然触发）"""
        o = _m("If your opponent's Active Pokémon is affected by a Special "
               "Condition, choose 2 of your opponent's Pokémon and put 2 damage "
               "counters on each of them.", kind="attack")
        self.assertEqual(o["tags"], [])
        self.assertTrue(o["unsupported"], "带 if 条件须如实未建模")


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
