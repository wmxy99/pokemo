# -*- coding: utf-8 -*-
"""F8b / F8c · 剩余 2 张的**解析**约定（2026-11-09）

守什么
------
1. Ogerpon「Torrential Pump」是**两句一体**：「You may shuffle 3 Energy … **If
   you do**, this attack **also** does 120 damage to 1 of your opponent's Benched
   Pokémon.」——只认后半句（读成一条独立伤害）会丢掉代价、整卡拒收；只认前半句
   则白送 120。必须整句成对认领成 `pre:b4:shuffle_energy:…:then:snipe:…`。
2. Cinderace「Explosiveness」是**开局**规则，不是每回合可发动的特性；差一个
   「when you are setting up to play」就完全不是同一件事，不能认领。
3. 反向：普通「洗回能量」（没有 If you do）与「手牌直接铺后备」（Klinklang）
   都不能被这两个检测器吃掉（各有自己的 tag）。

运行：cd packages/agent && python -m unittest tests.test_f8d_parse -v
"""
from __future__ import annotations

import unittest

from agent import effects as E


def _tags(text: str, kind: str, name: str = "X") -> dict:
    spec = E.parse_rule(text, kind=kind, name=name)
    return E.to_engine_tags([spec])


TORRENTIAL = ("You may shuffle 3 Energy attached to this Pokémon into your deck. "
              "If you do, this attack also does 120 damage to 1 of your "
              "opponent’s Benched Pokémon. (Don’t apply Weakness and Resistance "
              "for Benched Pokémon.)")

EXPLOSIVENESS = ("If this Pokémon is in your hand when you are setting up to "
                 "play, you may put it face down in the Active Spot.")


class TestGatedSnipe(unittest.TestCase):
    def test_ogerpon(self):
        r = _tags(TORRENTIAL, "attack", "Torrential Pump")
        self.assertEqual(r["tags"],
                         ["pre:b4:shuffle_energy:3:then:snipe:opp_bench:120:1"])
        self.assertEqual(r["unsupported"], [])

    def test_energy_count_and_damage_come_from_text(self):
        """数字来自卡面，不能写死 3 / 120"""
        r = _tags("You may shuffle 2 Energy attached to this Pokémon into your "
                  "deck. If you do, this attack also does 60 damage to 1 of "
                  "your opponent’s Benched Pokémon.", "attack")
        self.assertEqual(r["tags"],
                         ["pre:b4:shuffle_energy:2:then:snipe:opp_bench:60:1"])

    def test_shuffle_without_gate_is_not_claimed(self):
        """反向：没有「If you do」的洗回能量不是代价门控（不能白送狙击）"""
        r = _tags("Shuffle 3 Energy attached to this Pokémon into your deck.",
                  "attack")
        self.assertFalse(any(t.startswith("pre:b4:shuffle_energy:")
                             for t in r["tags"]), r["tags"])

    def test_active_target_is_not_bench_snipe(self):
        """反向：打**战斗位**的追加伤害不是本形态（那是 dmg_plus 族）"""
        r = _tags("You may shuffle 3 Energy attached to this Pokémon into your "
                  "deck. If you do, this attack also does 120 damage to your "
                  "opponent’s Active Pokémon.", "attack")
        self.assertFalse(any("opp_bench" in t for t in r["tags"]), r["tags"])

    def test_not_claimed_on_abilities(self):
        """反向：特性文本里出现同句也不该落成招式侧 tag"""
        r = _tags(TORRENTIAL, "ability")
        self.assertFalse(any(t.startswith("pre:b4:") for t in r["tags"]))


class TestSetupActive(unittest.TestCase):
    def test_cinderace(self):
        r = _tags(EXPLOSIVENESS, "ability", "Explosiveness")
        self.assertEqual(r["tags"], ["passive:setup_active"])
        self.assertEqual(r["unsupported"], [],
                         "整句应被完整认领（残留 → 整卡拒收）")

    def test_bench_variant_is_not_setup_active(self):
        """反向：「手牌直接铺后备」（Klinklang）是另一条规则"""
        r = _tags("Once during your turn, if this Pokémon is in your hand and "
                  "your opponent has any Stage 2 Pokémon in play, you may put "
                  "this Pokémon onto your Bench.", "ability")
        self.assertNotIn("passive:setup_active", r["tags"])
        self.assertEqual(r["tags"], ["hand_bench:opp_stage2"])

    def test_missing_phrase_is_not_claimed(self):
        """反向：少了「when you are setting up to play」就不是开局规则"""
        r = _tags("If this Pokémon is in your hand, you may put it face down in "
                  "the Active Spot.", "ability")
        self.assertNotIn("passive:setup_active", r["tags"])


if __name__ == "__main__":
    unittest.main()
