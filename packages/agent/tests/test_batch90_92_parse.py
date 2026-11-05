# -*- coding: utf-8 -*-
"""第九十~九十二批（2026-11-05）· 解析用例

覆盖 P1 阶段三批的解析侧口径：

  · 批 90：伪招式（全 `passive:*` tag）的处理在**建池侧**（见 card-db
    `test_pool_build.py`），解析侧本就不产 attack 标签，无需额外用例；
  · 批 91：`shuffle_top:3:own`（Deduction Kit）解析已有，缺口在建池白名单；
  · 批 92：本文件重点 —— 训练家**多动作合成一条复合 tag**。

批 92 的两处关键修正：
  1. Salvatore 旧解析误产 `candy`（Rare Candy **跳阶**）—— 实为「从牌库检索并
     正常进化」，且卡面限定「has no Abilities」必须带 `:no_ability`；
  2. Larry's Skill 旧解析只产 `discard:all` + `search_pokemon:1`，
     **静默丢掉**了支援者与基本能量 → 合成 `discard_search_multi:`。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from agent import effects as E  # noqa: E402


def _tags(text, kind="supporter", name="X"):
    sp = E.parse_rule(text, kind=kind, name=name)
    return E.to_engine_tags([sp])


SALVATORE = (
    "Search your deck for a card that has no Abilities and evolves from 1 of your "
    "Pokémon, and put it onto that Pokémon to evolve it. Then, shuffle your deck. "
    "You can use this card on a Pokémon you put down when you were setting up to "
    "play or on a Pokémon that was put into play this turn.")

LARRY = (
    "Discard your hand and search your deck for a Pokémon, a Supporter card, and a "
    "Basic Energy card, reveal them, and put them into your hand. Then, shuffle "
    "your deck.")

SECRET_BOX = (
    "Search your deck for an Item card, a Pokémon Tool card, a Supporter card, and "
    "a Stadium card, reveal them, and put them into your hand. Then, shuffle your "
    "deck.")


class TestBatch92Salvatore(unittest.TestCase):
    def test_no_ability_suffix(self):
        o = _tags(SALVATORE, name="Salvatore")
        self.assertEqual(o["tags"], ["evolve_from_deck:no_ability"])
        self.assertEqual(o["unsupported"], [])

    def test_candy_must_not_be_produced(self):
        """`candy` 是 Rare Candy 的**跳阶**语义；Salvatore 是正常阶段进化。
        误产 candy 会让引擎把手牌二阶卡直接放上去（跳过一阶），是静默加强。"""
        o = _tags(SALVATORE, name="Salvatore")
        self.assertNotIn("candy", o["tags"],
                         "Salvatore 不跳阶，绝不能产 candy")
        self.assertFalse(any(str(t).startswith("candy") for t in o["tags"]))

    def test_plain_evolve_has_no_suffix(self):
        """负向对照：卡面**没有**「has no Abilities」时不得带 `:no_ability`
        （否则会无谓缩小检索范围）"""
        o = _tags("Search your deck for a card that evolves from 1 of your "
                  "Pokémon, and put it onto that Pokémon to evolve it. Then, "
                  "shuffle your deck.", name="Plain")
        self.assertEqual(o["tags"], ["evolve_from_deck"])

    def test_only_one_evolve_tag(self):
        """同一句 search(dest=evolve) + evolve 会各产一次 → 必须归一为一条
        （两条会被 `build_trainer` 按「多 tag → 整卡拒」处理）"""
        o = _tags(SALVATORE, name="Salvatore")
        ev = [t for t in o["tags"] if str(t).startswith("evolve_from_deck")]
        self.assertEqual(len(ev), 1, f"evolve_from_deck 族只能留一条：{o['tags']}")


class TestBatch92LarrySkill(unittest.TestCase):
    def test_compound_tag(self):
        o = _tags(LARRY, name="Larry’s Skill")
        self.assertEqual(
            o["tags"],
            ["discard_search_multi:pokemon+supporter+basic_energy:1"])
        self.assertEqual(o["unsupported"], [])

    def test_must_not_lose_supporter_and_energy(self):
        """反向守卫：不得退化成 `search_pokemon:1`（少拿 2 张 = 静默削弱）"""
        o = _tags(LARRY, name="Larry’s Skill")
        self.assertNotIn("search_pokemon:1", o["tags"])
        joined = " ".join(str(t) for t in o["tags"])
        self.assertIn("supporter", joined)
        self.assertIn("basic_energy", joined)

    def test_must_not_leave_two_tags(self):
        """不得同时留 `discard:all` 与 `search_multi:`（多 tag → 整卡拒）"""
        o = _tags(LARRY, name="Larry’s Skill")
        self.assertNotIn("discard:all", o["tags"])
        self.assertFalse(any(str(t).startswith("search_multi:") for t in o["tags"]),
                         "已合成为 discard_search_multi:，不应再有裸 search_multi:")


class TestBatch92Regressions(unittest.TestCase):
    """放宽 `_multi_search_alts` 后的回归：既有口径必须一字不变"""

    def test_secret_box_four_kinds_unchanged(self):
        o = _tags(SECRET_BOX, kind="item", name="Secret Box")
        self.assertEqual(o["tags"],
                         ["search_multi:item+tool+supporter+stadium:1"])

    def test_single_kind_stays_plain_search(self):
        """单类别检索不得变成 search_multi（否则张数语义被改写）"""
        o = _tags("Search your deck for a Pokémon, reveal it, and put it into "
                  "your hand. Then, shuffle your deck.", name="Plain")
        self.assertEqual(o["tags"], ["search_pokemon:1"])

    def test_basic_energy_not_swallowed_by_energy(self):
        """`Basic Energy card` 必须判成 basic_energy，不能被 `energy card` 吃掉
        （否则会连**特殊能量**一起检索 = 静默放宽）"""
        kinds = E._multi_search_alts(
            "search your deck for a Pokémon, a Supporter card, and a Basic Energy "
            "card, reveal them, and put them into your hand")
        self.assertIn("basic_energy", kinds)
        self.assertNotIn("energy", kinds)

    def test_unknown_kind_returns_none(self):
        """负向：出现不认识的类别短语 → 返回 None（绝不猜类别）"""
        self.assertIsNone(E._multi_search_alts(
            "search your deck for a Fossil card and a Trainer card, reveal them, "
            "and put them into your hand"))


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
