# -*- coding: utf-8 -*-
"""PokéLab AI · K2「附着」两个变体的解析用例（2026-11-02 第三十八批）

覆盖三张真实卡，三种此前都**进不了池**的形态：

1. **进化触发的自由分配**（Marnie's Grimmsnarl ex DRI 136 `Punk Up`）
   后半句与 `_ATTACH_SPREAD_SIMPLE` 逐字相同，只多了句首的进化从句 ——
   `_detect_attach_spread` 全部用 `.match`（隐式 `^`），所以匹配不上。
   必须由**必选前缀**的新正则认领，并产 `evolve_act:attach_spread:...`：
   不包 `evolve_act:` 的 field 特性是「每回合一次」，等于免费再用（静默加强）。

2. **每只后备各一份**（Mega Gardevoir ex MEG 60 `Overflowing Wishes`）
   「For each of your Benched Pokémon, … attach it to **that** Pokémon.」
   ⚠ 与自由分配是**两种语义**：spread 允许漏掉某只、允许堆在一只上，卡面都不允许。

3. **抛币计数**（Mega Audino ex ASC 172 `Kaleidowaltz`）
   「Flip 3 coins. For each heads, … up to 2 … in any way you like.」
   张数结算时才定 → 独立 tag `attach_spread_flip:3:2:...`，解析期绝不折算常数。

负向守卫：
  · 同一句式但**没有**进化前缀（Altaria）仍走 `attach_spread:`，不得被包 `evolve_act:`；
  · 「of different types」（Terapagos）→ 不认领；
  · 未知目标限定（Tera）→ 不认领；
  · 单目标形态（「to this Pokémon」）不得被本族抢走。

运行：cd packages/agent && python -m unittest tests.test_attach_variants -v
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


def _both(text: str, kind: str = "attack") -> dict:
    sp = E.parse_rule(text, kind=kind, name="X")
    out = E.to_engine_tags([sp])
    return {"tags": out["tags"], "trigger": sp["trigger"],
            "unsupported": [u["reason"] for u in out["unsupported"]]}


# ---- 真实卡面（与 cards_challenge.json 逐字一致） ----
GRIMMSNARL = ("When you play this Pokémon from your hand to evolve 1 of your "
              "Pokémon during your turn, you may search your deck for up to 5 "
              "Basic {D} Energy cards and attach them to your Marnie\u2019s "
              "Pokémon in any way you like. Then, shuffle your deck.")
GARDEVOIR = ("For each of your Benched Pokémon, search your deck for a Basic "
             "{P} Energy card and attach it to that Pokémon. Then, shuffle "
             "your deck.")
AUDINO = ("Flip 3 coins. For each heads, search your deck for up to 2 Basic "
          "Energy cards and attach them to your Pokémon in any way you like. "
          "Then, shuffle your deck.")
ALTARIA = ("Search your deck for up to 2 Basic Energy cards and attach them to "
           "your Pokémon in any way you like. Then, shuffle your deck.")
TERAPAGOS = ("Search your deck for up to 3 Basic Energy cards of different "
             "types and attach them to your Tera Pokémon in any way you like. "
             "Then, shuffle your deck.")
CINDERACE = ("Search your deck for up to 3 Basic Energy cards and attach them "
             "to your Benched Pokémon in any way you like. Then, shuffle your "
             "deck.")


class TestGrimmsnarlEvolve(unittest.TestCase):
    """进化触发的自由分配 → `evolve_act:attach_spread:...`"""

    def test_wrapped_in_evolve_act(self):
        r = _both(GRIMMSNARL, kind="ability")
        self.assertEqual(r["trigger"], "on_evolve")
        self.assertEqual(r["tags"],
                         ["evolve_act:attach_spread:5:darkness:deck:"
                          "target:cat-marnie"])
        self.assertEqual(r["unsupported"], [])

    def test_scope_is_category_not_type(self):
        """「your Marnie's Pokémon」是**卡牌子类**（category），不是属性"""
        tag = _both(GRIMMSNARL, kind="ability")["tags"][0]
        self.assertIn(":target:cat-marnie", tag)
        self.assertNotIn(":target:type-", tag)

    def test_plain_form_not_wrapped(self):
        """同一句式但无进化前缀 → 仍走普通 `attach_spread:`（不包 evolve_act）"""
        r = _both(ALTARIA)
        self.assertEqual(r["tags"], ["attach_spread:2:any:deck"])
        self.assertEqual(r["trigger"], "on_use")

    def test_bench_scope_still_plain(self):
        r = _both(CINDERACE)
        self.assertEqual(r["tags"], ["attach_spread:3:any:deck:target:bench"])


class TestGardevoirEachBench(unittest.TestCase):
    """「每只后备各 1 张、绑定给那一只」"""

    def test_tag(self):
        r = _both(GARDEVOIR)
        self.assertEqual(r["tags"], ["attach_each_bench:1:psychic:deck"])
        self.assertEqual(r["unsupported"], [])

    def test_detector_returns_per_and_type(self):
        self.assertEqual(E._detect_attach_each_bench(GARDEVOIR),
                         {"per": 1, "etype": "psychic", "zone": "deck"})

    def test_not_taken_by_spread(self):
        """必须落在 `attach_each_bench`，不能被 spread 抢走（语义不同）"""
        self.assertIsNone(E._detect_attach_spread(GARDEVOIR))
        tags = _both(GARDEVOIR)["tags"]
        self.assertFalse(any(t.startswith("attach_spread") for t in tags))


class TestAudinoFlip(unittest.TestCase):
    """抛币计数 → `attach_spread_flip:<coins>:<per>:...`"""

    def test_tag(self):
        r = _both(AUDINO)
        self.assertEqual(r["tags"], ["attach_spread_flip:3:2:any:deck"])
        self.assertEqual(r["unsupported"], [])

    def test_detector_keeps_constants(self):
        """必须保留「抛 3 枚 / 每正面 2 张」两个常量，不得折算成 6 或 2"""
        got = E._detect_attach_spread(AUDINO)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["coins"], 3)
        self.assertEqual(got[0]["per"], 2)
        self.assertNotIn("n", got[0])


class TestGuards(unittest.TestCase):
    """不得认领的形态（认了就是静默错映射）"""

    def test_different_types_not_claimed(self):
        """「of different types」是引擎未建模的限定（Terapagos）"""
        self.assertIsNone(E._detect_attach_spread(TERAPAGOS))
        self.assertIsNone(E._detect_attach_each_bench(TERAPAGOS))

    def test_tera_scope_not_claimed(self):
        self.assertIsNone(E._spread_scope_of("Tera Pokémon"))

    def test_each_bench_requires_that_pokemon(self):
        """「attach them to your Pokémon」不是 each-bench 形态"""
        self.assertIsNone(E._detect_attach_each_bench(ALTARIA))

    def test_flip_requires_coin_prefix(self):
        """没有「Flip N coins.」就不能走 flip 口径"""
        self.assertIsNone(E._detect_attach_spread(AUDINO.replace(
            "Flip 3 coins. ", "")))


if __name__ == "__main__":
    unittest.main()
