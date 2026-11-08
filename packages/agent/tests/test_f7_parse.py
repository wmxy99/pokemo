# -*- coding: utf-8 -*-
"""F7 · 道具 / 特殊能量形态的**解析**约定（2026-11-08）

守什么
------
1. **一句两修正**必须落成**两条** `tool:` tag（池侧再拼 `a|b`，引擎静态层逐段
   读）—— 解析侧只认第一段就会静默丢效果。
2. **持有者限定必须写进 tag**：`holder:faction:hops`。不写 = 任何宝可梦挂上都
   生效（静默加强）。这一条与「引擎没有卡名归属元数据就整卡放弃」的旧守卫是
   同一个道理，只是现在引擎**有**这套判定（`_qual_ok` 的 faction）。
3. **道具提供招式**只记「谁能用」（`tool:provide_attack:<holder>`）；招式本体在
   卡的 `attacks[]` 上，由池生成侧搬运 —— 解析侧不重复序列化招式字段。
4. **一句三动作的物品**（Megaton Blower）必须合成**一条** tag，且**不再**额外
   产出 `discard_stadium`（两个 tag → 整卡被拒）。
5. 认领之后**同一句不得被别的检测器二次认领**（`discard_stadium` / `rule` 兜底）。

运行：cd packages/agent && python -m unittest tests.test_f7_parse -v
"""
from __future__ import annotations

import unittest

from agent import effects as E


def _tags(text: str, kind: str = "rule", name: str = "X") -> dict:
    spec = E.parse_rule(text, kind=kind, name=name)
    return E.to_engine_tags([spec])


class TestHopsChoiceBand(unittest.TestCase):
    TXT = ("Attacks used by the Hop's Pokémon this card is attached to cost "
           "{C} less and do 30 more damage to your opponent's Active Pokémon "
           "(before applying Weakness and Resistance).")

    def test_two_static_tags_with_holder(self):
        r = _tags(self.TXT, name="Hop's Choice Band")
        self.assertEqual(r["unsupported"], [])
        self.assertIn("tool:cost_less:1:holder:faction:hops", r["tags"])
        self.assertIn("tool:dmg_plus:30:holder:faction:hops", r["tags"])

    def test_holder_is_part_of_the_tag(self):
        """不写持有者 = 挂到任何宝可梦都生效（静默加强）"""
        r = _tags(self.TXT)
        for t in r["tags"]:
            self.assertIn("holder:faction:hops", t,
                          f"缺少持有者限定的 tag：{t}")


class TestCoreMemory(unittest.TestCase):
    TXT = ("The Mega Zygarde {ex} this card is attached to can use the attack "
           "on this card. (You still need the necessary Energy to use this "
           "attack.)")

    def test_provide_attack(self):
        r = _tags(self.TXT, name="Core Memory")
        self.assertEqual(r["tags"], ["tool:provide_attack:megazygarde"])
        self.assertEqual(r["unsupported"], [],
                         "括号里的费用提醒被当成未建模 → 整卡会被拒收")

    def test_other_holder_not_claimed(self):
        """换了持有者也要认（键跟着卡名走，不能写死 megazygarde）"""
        r = _tags("The Pikachu {ex} this card is attached to can use the attack "
                  "on this card.")
        self.assertEqual(r["tags"], ["tool:provide_attack:pikachu"])


class TestMegatonBlower(unittest.TestCase):
    TXT = ("Discard all Pokémon Tools and Special Energy from all of your "
           "opponent's Pokémon, and discard a Stadium in play.")

    def test_single_composite_tag(self):
        r = _tags(self.TXT, name="Megaton Blower")
        self.assertEqual(r["tags"], ["discard_tool_senergy_all:opp:stadium"])
        self.assertEqual(r["unsupported"], [])

    def test_no_duplicate_stadium_tag(self):
        """`discard_stadium` 已被并进复合 tag，不能再单独产出（两个 tag → 拒收）"""
        r = _tags(self.TXT, name="Megaton Blower")
        self.assertNotIn("discard_stadium", r["tags"])

    def test_without_stadium_clause(self):
        """同一句式但**没有**弃场地段：不能凭空加上 `:stadium`"""
        r = _tags("Discard all Pokémon Tools and Special Energy from all of your "
                  "opponent's Pokémon.")
        self.assertEqual(r["tags"], ["discard_tool_senergy_all:opp"])

    def test_own_side_is_not_opponent(self):
        r = _tags("Discard all Pokémon Tools and Special Energy from all of "
                  "your Pokémon.")
        self.assertEqual(r["tags"], ["discard_tool_senergy_all:own"])


class TestPreDamageStillWins(unittest.TestCase):
    """伤害前阶段的老写法不能被新分支吞掉（时序错会让守方道具先生效一次）"""

    def test_pre_damage_branch(self):
        r = _tags("Before doing damage, discard all Pokémon Tools and Special "
                  "Energy from your opponent's Active Pokémon.",
                  kind="attack", name="Mow Rotom")
        self.assertTrue(any(t.startswith("pre:discard_energy")
                            for t in r["tags"]), r["tags"])
        self.assertTrue(any(t.startswith("pre:discard_tool")
                            for t in r["tags"]), r["tags"])
        self.assertNotIn("discard_tool_senergy_all:opp:stadium", r["tags"])


if __name__ == "__main__":
    unittest.main()
