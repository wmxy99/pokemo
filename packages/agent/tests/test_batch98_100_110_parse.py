# -*- coding: utf-8 -*-
"""第九十八 / 一百 / 一百一十批（2026-11-06）· 解析用例

三族各自「引擎早就有、解析没产出」：

  · `snipe:…:target:ex`（Shaymin / Flygon ex）—— 引擎第四十一批已支持目标筛选，
    缺的是「{ex} or/and {V}」这种**并集写法**的短语映射；
  · `coin_each:…|cond:at_least_heads:1:<status>`（Vanilluxe）—— 引擎已支持
    多币阈值，缺的是「If either of them is heads」这句措辞；
  · `draw_to_branch:<base>:<alt>:if:<cond>`（Ariana）—— 新增：分支替换的
    「抽到 N 张」。两条 `draw_to` 叠加即等价（只补不弃），不需要回合状态。

⚠ `{V}` 等价性前提：本卡池 1267 张**没有 Pokémon V**。`TestNoVInDataset`
  是这条前提的守卫 —— 一旦数据集加了 V 类卡，这里立刻变红，强制回来重新评估
  （而不是让并集静默少算）。
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from agent import effects as E

DB = (Path(__file__).resolve().parents[2] / "card-db" / "data" / "cleaned"
      / "cards_challenge.json")


def _tags(text: str, kind: str = "attack") -> dict:
    sp = E.parse_rule(text, kind=kind, name="X")
    return E.to_engine_tags([sp])


class TestNoVInDataset(unittest.TestCase):
    """`{ex} or/and {V}` → `:target:ex` 的前提守卫"""

    def test_dataset_has_no_v_cards(self):
        cards = json.loads(DB.read_text(encoding="utf-8"))["cards"]
        v_cards = [c["name"] for c in cards
                   if re.search(r"\bPok[eé]mon V\b", c.get("other_text") or "")
                   or re.search(r"\sV(Max|STAR)?$", c["name"])]
        self.assertEqual(
            v_cards, [],
            "数据集出现了 Pokémon V 类卡：`{ex} or/and {V}` → `:target:ex` 的"
            "等价前提已不成立，必须重新评估 snipe/condition 的 V 支持")


class TestBatch98SnipeExTarget(unittest.TestCase):
    def test_bench_single_ex_or_v(self):
        """Shaymin「Pinpoint Dive」"""
        o = _tags("This attack does 60 damage to 1 of your opponent's Benched "
                  "Pokémon {ex} or Benched Pokémon {V}. (Don't apply Weakness and "
                  "Resistance for Benched Pokémon.)")
        self.assertEqual(o["tags"], ["snipe:opp_bench:60:1:target:ex"])
        self.assertEqual(o["unsupported"], [])

    def test_each_ex_and_v(self):
        """Flygon ex「Sonic Peridot」—— 全体 ex/V 各 100"""
        o = _tags("This attack does 100 damage to each of your opponent's Pokémon "
                  "{ex} and Pokémon {V}. This attack's damage isn't affected by "
                  "Weakness or Resistance.")
        self.assertIn("snipe:opp:100:all:target:ex", o["tags"])
        self.assertEqual(o["unsupported"], [])

    def test_plain_ex_still_works(self):
        """近邻：只写 {ex} 时形态不变"""
        o = _tags("This attack does 60 damage to 1 of your opponent's Benched "
                  "Pokémon {ex}. (Don't apply Weakness and Resistance for "
                  "Benched Pokémon.)")
        self.assertEqual(o["tags"], ["snipe:opp_bench:60:1:target:ex"])

    def test_non_ex_subset_still_refused(self):
        """近邻：「that has an Ability」这类子集仍不可表达 → 必须拒收"""
        o = _tags("This attack does 60 damage to 1 of your opponent's Benched "
                  "Pokémon that has an Ability.")
        self.assertEqual(o["tags"], [])
        self.assertTrue(o["unsupported"])


class TestBatch100CoinEither(unittest.TestCase):
    def test_either_heads_paralyze(self):
        """Vanilluxe「Double Freeze」：≥1 个正面即麻痹"""
        o = _tags("Flip 2 coins. This attack does 90 damage for each heads. If "
                  "either of them is heads, your opponent's Active Pokémon is now "
                  "Paralyzed.")
        self.assertEqual(
            o["tags"],
            ["coin_each:2:heads:dmg_set:90|cond:at_least_heads:1:paralyze"])
        self.assertEqual(o["unsupported"], [])

    def test_both_heads_unchanged(self):
        """近邻：both 仍是 `all_heads`（不得被 either 规则吞成 at_least）"""
        o = _tags("Flip 2 coins. This attack does 90 damage for each heads. If "
                  "both of them are heads, your opponent's Active Pokémon is now "
                  "Paralyzed.")
        self.assertIn("cond:all_heads:paralyze", o["tags"][0])

    def test_multi_status_not_claimed(self):
        """近邻：多状态（"Paralyzed and Poisoned"）不得认领成单状态"""
        o = _tags("Flip 2 coins. This attack does 90 damage for each heads. If "
                  "either of them is heads, your opponent's Active Pokémon is now "
                  "Paralyzed and Poisoned.")
        self.assertFalse(any("cond:" in t for t in o["tags"]), o["tags"])


class TestBatch110DrawToBranch(unittest.TestCase):
    TXT = ("Draw cards until you have 5 cards in your hand. If all of your "
           "Pokémon in play are Team Rocket's Pokémon, draw cards until you have "
           "8 cards in your hand instead.")

    def test_branch_tag(self):
        """Team Rocket's Ariana"""
        o = _tags(self.TXT, kind="supporter")
        self.assertEqual(
            o["tags"], ["draw_to_branch:5:8:if:own_all_name:teamrockets"])
        self.assertEqual(o["unsupported"], [])

    def test_plain_draw_to_unaffected(self):
        """近邻：无分支的「抽到 5 张」仍是 `draw_to:5`"""
        o = _tags("Draw cards until you have 5 cards in your hand.",
                  kind="supporter")
        self.assertEqual(o["tags"], ["draw_to:5"])

    def test_shuffle_draw_instead_not_hijacked(self):
        """近邻：「洗回手牌后抽 N 张，满足条件抽 M 张 instead」是**另一条**路
        （`draw_cond`），不得被分支抽牌规则吃掉。"""
        o = _tags("Shuffle your hand into your deck. Then, draw 4 cards. If your "
                  "opponent has 3 or fewer Prize cards remaining, draw 8 cards "
                  "instead.", kind="supporter")
        self.assertFalse(any(t.startswith("draw_to_branch:") for t in o["tags"]),
                         o["tags"])


class TestBatch105SpecialEnergy(unittest.TestCase):
    """Enhanced Hammer「Discard a Special Energy from 1 of your opponent's Pokémon」

    `senergy` 是**卡**列表，引擎本来就能区分特殊/基本；缺的是「训练家侧
    单点弃特殊能量」的出口 —— 该前缀（`discard_energy:`）此前只出现在
    ATTACK_TAG_PREFIXES，训练家白名单里没有，`build_trainer` 因此整卡拒收。
    """

    def test_one_of_opponent_pokemon(self):
        o = _tags("Discard a Special Energy from 1 of your opponent's Pokémon.",
                  kind="item")
        self.assertEqual(o["tags"], ["discard_energy:1:opp:special:any"])
        self.assertEqual(o["unsupported"], [])

    def test_active_only_variant_unchanged(self):
        """近邻：「from your opponent's Active Pokémon」不带 `:any`（只打战斗位）"""
        o = _tags("Discard a Special Energy from your opponent's Active Pokémon.",
                  kind="item")
        self.assertEqual(o["tags"], ["discard_energy:1:opp:special"])


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
