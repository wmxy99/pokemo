# -*- coding: utf-8 -*-
"""第四十八批（2026-11-04）· 解析用例：`coin_each` 的**币数**由计数源决定

卡面原型：
  · 「Flip a coin for each Energy attached to this Pokémon. This attack does
    70 damage for each heads.」（Heliolisk ASC 64 / Larry's Dudunsparce ex）
  · 「… for each {W} Energy attached to this Pokémon」（Volcanion）
  · 「… for each {D} Pokémon you have in play」（Scrafty WHT 58）

⚠ 这批卡此前是**静默丢卡**：`_COIN_PER_SIDE_RE` 的 `(\\d+)?` 留空 →
当成 **1 枚币**，而且只产出 damage 动作、既没有 tag 也没有 unsupported。
「7 个能量 = 7 枚币」被压成 1 枚，在登记表里却只剩一个 `unmatched`。
"""
from __future__ import annotations
import unittest

from agent import effects as E


def _tags(text, kind="attack", name="x"):
    sp = E.parse_rule(text, kind=kind, name=name)
    return E.to_engine_tags([sp])


POWERFUL_BOLT = ("Flip a coin for each Energy attached to this Pokémon. "
                 "This attack does 70 damage for each heads.")
POWERFUL_STEAM = ("Flip a coin for each {W} Energy attached to this Pokémon. "
                  "This attack does 90 damage for each heads.")
RUFFIANS = ("Flip a coin for each {D} Pokémon you have in play. "
            "This attack does 60 damage for each heads.")
MORE_FORM = ("Flip a coin for each Energy attached to this Pokémon. "
             "This attack does 30 more damage for each heads.")


class TestCoinEachCounter(unittest.TestCase):
    def test_energy_on_self(self):
        self.assertEqual(_tags(POWERFUL_BOLT)["tags"],
                         ["coin_each:own_active_energy:heads:dmg_set:70"])

    def test_colored_energy_on_self(self):
        self.assertEqual(_tags(POWERFUL_STEAM)["tags"],
                         ["coin_each:own_active_energy_water:heads:dmg_set:90"])

    def test_pokemon_of_type_in_play(self):
        self.assertEqual(_tags(RUFFIANS)["tags"],
                         ["coin_each:own_field_type_darkness:heads:dmg_set:60"])

    def test_more_damage_stays_plus(self):
        """有 `more` → 加伤；没有 → 基础伤害按正面数重算（同一条纪律）"""
        self.assertEqual(_tags(MORE_FORM)["tags"],
                         ["coin_each:own_active_energy:heads:dmg_plus:30"])

    def test_no_reason_after_mapping(self):
        """修好之后不得再留任何 unsupported（此前是零 tag 零理由）"""
        r = _tags(POWERFUL_BOLT)
        self.assertEqual(r["unsupported"], [])

    def test_fixed_coin_count_still_works(self):
        """回归：固定枚数（「Flip 3 coins …」）不得被新分支抢走"""
        r = _tags("Flip 3 coins. This attack does 20 damage for each heads.")
        self.assertEqual(r["tags"], ["coin_each:3:heads:dmg_set:20"])

    def test_until_tails_still_works(self):
        """回归：until-tails 仍优先于 for-each 分支"""
        r = _tags("Flip a coin until you get tails. "
                  "This attack does 10 damage for each heads.")
        self.assertEqual(r["tags"], ["coin_until_tails:dmg_set:10"])

    def test_unknown_source_not_claimed(self):
        """认不出的「for each」短语 → 不认领（不得退化成 1 枚币）"""
        r = _tags("Flip a coin for each of your Benched Pokémon. "
                  "This attack does 30 damage for each heads.")
        self.assertFalse(any(t.startswith("coin_each:")
                             and "heads:dmg_set:30" in t
                             for t in r["tags"]))


class TestSilenceGuard(unittest.TestCase):
    """零 tag 零理由 → 必须登记一条兜底 unsupported（第四十八批）

    这是**安全网**而不是主路径：真实卡池里目前没有命中项（未就绪的卡都已各自
    带着具体原因）。它的价值在于「以后解析链再出现没认出的句式时，不会又变成
    一条没有任何理由的 `unmatched`」。
    """

    def test_unmapped_text_is_recorded(self):
        # 直接构造「有文本、零动作、零理由」的 spec，隔离验证安全网本身
        # （走完整解析时通常已有更具体的原因抢先登记，如「静态/未建模」）。
        sp = {"kind": "ability", "name": "x", "trigger": "on_use",
              "conditions": [], "actions": [],
              "source_text": "Something the engine has never seen before.",
              "citations": []}
        r = E.to_engine_tags([sp])
        self.assertFalse(r["tags"])
        self.assertTrue(r["unsupported"],
                        "有文本却既无 tag 又无理由 = 静默丢卡")
        self.assertIn("未映射", r["unsupported"][0]["reason"])

    def test_damage_only_spec_is_not_flagged(self):
        """`damage` 类动作由招式 damage 字段承载 —— 不得被安全网误判"""
        sp = E.parse_rule("", kind="attack", name="x")
        sp["actions"] = [{"type": "damage", "amount": 30,
                          "target": "active_opponent"}]
        sp["source_text"] = "This attack does 30 damage."
        r = E.to_engine_tags([sp])
        self.assertEqual(r["unsupported"], [])

    def test_mapped_card_has_no_false_reason(self):
        self.assertEqual(_tags(POWERFUL_BOLT)["unsupported"], [])


if __name__ == "__main__":
    unittest.main()
