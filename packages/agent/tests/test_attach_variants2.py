# -*- coding: utf-8 -*-
"""PokéLab AI · K2 附着的四个新形态 + 「不同属性」约束（2026-11-02 第三十九批）

5 张真实卡 + 1 处**既有缺陷修复**：

  · Terapagos SSP 161「Prism Charge」→ `attach_spread:3:any:deck:target:cat-tera:distinct`
  · Steven's Metagross ex DRI 145「X-Boot」→ `attach_spread_multi:1:psychic:1:metal:...`
  · Janine's Secret Art SFA 59 → `attach_each_pick:2:1:darkness:...:poison_if_active`
  · Snorlax POR 63「Gormandizer」→ `attach_flip_until:any:deck`
  · Energy Coin BLK 81 → `coin_gate_attach:2:1:any:deck`
  · Eevee SFA 50「Colorful Catch」→ `search_energy:3:distinct`（**修复**）

负向守卫：
  · 「of different types」只在该句出现时才加 `:distinct`（否则凭空削强度）；
  · 「or 1 of each」不得拆成两条 `attach_spread:`（会强制每类各拿 1 张）；
  · 目标 `Tera Pokémon` 走 `cat-tera`（卡牌子类），不是 `type-*`（属性）；
  · 无「Flip a coin until you get tails.」不得走 `attach_flip_until:`。

运行：cd packages/agent && python -m unittest tests.test_attach_variants2 -v
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


def _m(text: str, kind: str = "attack") -> dict:
    sp = E.parse_rule(text, kind=kind, name="X")
    out = E.to_engine_tags([sp])
    return {"tags": out["tags"], "trigger": sp["trigger"],
            "unsupported": [u["reason"] for u in out["unsupported"]]}


TERAPAGOS = ("Search your deck for up to 3 Basic Energy cards of different types "
             "and attach them to your Tera Pokémon in any way you like. Then, "
             "shuffle your deck.")
STEVEN = ("Once during your turn, you may search your deck for a Basic {P} "
          "Energy card, a Basic {M} Energy card, or 1 of each and attach them "
          "to your {P} Pokémon and {M} Pokémon in any way you like. Then, "
          "shuffle your deck.")
JANINE = ("Choose up to 2 of your {D} Pokémon. For each of those Pokémon, "
          "search your deck for a Basic {D} Energy card and attach it to that "
          "Pokémon. Then, shuffle your deck. If you attached Energy to your "
          "Active Pokémon in this way, it is now Poisoned.")
SNORLAX = ("Flip a coin until you get tails. Search your deck for an amount of "
           "Basic Energy up to the number of heads and attach it to this "
           "Pokémon. Then, shuffle your deck.")
ECOIN = ("Flip 2 coins. If both of them are heads, search your deck for a Basic "
         "Energy card and attach it to 1 of your Pokémon. Then, shuffle your "
         "deck.")
EEVEE = ("Search your deck for up to 3 Basic Energy cards of different types, "
         "reveal them, and put them into your hand. Then, shuffle your deck.")
ALTARIA = ("Search your deck for up to 2 Basic Energy cards and attach them to "
           "your Pokémon in any way you like. Then, shuffle your deck.")


class TestTerapagos(unittest.TestCase):
    def test_tag(self):
        r = _m(TERAPAGOS)
        self.assertEqual(r["tags"],
                         ["attach_spread:3:any:deck:target:cat-tera:distinct"])
        self.assertEqual(r["unsupported"], [])

    def test_tera_scope_is_category(self):
        """「Tera Pokémon」是卡牌子类（category），不是属性 `type-*`"""
        tag = _m(TERAPAGOS)["tags"][0]
        self.assertIn(":target:cat-tera", tag)
        self.assertNotIn("type-", tag)


class TestDifferentTypes(unittest.TestCase):
    def test_distinct_only_when_phrase_present(self):
        self.assertIn(":distinct", _m(TERAPAGOS)["tags"][0])
        self.assertNotIn(":distinct", _m(ALTARIA)["tags"][0])

    def test_eevee_search_energy_distinct(self):
        """Eevee SFA 50：检索**入手**侧同样要「属性两两不同」

        这是**既有缺陷修复**：此前忽略该句 → `search_energy:3` 允许拿 3 张同色。
        """
        self.assertEqual(_m(EEVEE)["tags"], ["search_energy:3:distinct"])

    def test_plain_search_energy_not_distinct(self):
        self.assertEqual(
            _m("Search your deck for up to 2 Basic Energy cards, reveal them, "
               "and put them into your hand. Then, shuffle your deck.")["tags"],
            ["search_energy:2"])


class TestSteven(unittest.TestCase):
    def test_tag(self):
        r = _m(STEVEN, kind="ability")
        self.assertEqual(
            r["tags"],
            ["attach_spread_multi:1:psychic:1:metal:deck:"
             "target:type-psychic+type-metal"])
        self.assertEqual(r["unsupported"], [])

    def test_not_split_into_two_spreads(self):
        """「or 1 of each」是三选一，拆成两条会**强制每类各拿 1 张**"""
        tags = _m(STEVEN, kind="ability")["tags"]
        self.assertFalse(any(t.startswith("attach_spread:") for t in tags))

    def test_scope_union(self):
        """`your {P} Pokémon and {M} Pokémon` 是**并集**，不是「各附一份」"""
        self.assertEqual(E._spread_scope_of("{P} Pokémon and {M} Pokémon"),
                         "type-psychic+type-metal")

    def test_single_type_scope_unchanged(self):
        self.assertEqual(E._spread_scope_of("{L} Pokémon"), "type-lightning")


class TestJanine(unittest.TestCase):
    def test_tag(self):
        r = _m(JANINE, kind="supporter")
        self.assertEqual(
            r["tags"],
            ["attach_each_pick:2:1:darkness:deck:target:type-darkness:"
             "poison_if_active"])
        self.assertEqual(r["unsupported"], [])

    def test_no_unconditional_self_poison(self):
        """卡面的中毒是**条件性**的（只在该能量附到 Active 时才发生），
        无条件 `status_self:poison` 会给自家战斗位凭空挂状态"""
        self.assertNotIn("status_self:poison", _m(JANINE, kind="supporter")["tags"])

    def test_detector_fields(self):
        got = E._detect_attach_each_pick(JANINE)
        self.assertEqual(got["max"], 2)
        self.assertEqual(got["per"], 1)
        self.assertEqual(got["etype"], "darkness")
        self.assertEqual(got["scope"], "type-darkness")
        self.assertTrue(got["poison_if_active"])


class TestSnorlax(unittest.TestCase):
    def test_tag(self):
        r = _m(SNORLAX)
        self.assertEqual(r["tags"], ["attach_flip_until:any:deck"])
        self.assertEqual(r["unsupported"], [])

    def test_requires_flip_until_tails_sentence(self):
        """没有「Flip a coin until you get tails.」不得走这个口径"""
        t = SNORLAX.replace("Flip a coin until you get tails. ", "")
        self.assertIsNone(E._detect_attach_flip_until(t))

    def test_single_flip_forms_not_claimed(self):
        """「Flip a coin. If heads, …」不是「抛到反面为止」，不得抢走"""
        t = ("Flip a coin. If heads, search your deck for an amount of Basic "
             "Energy up to the number of heads and attach it to this Pokémon. "
             "Then, shuffle your deck.")
        self.assertIsNone(E._detect_attach_flip_until(t))


class TestEnergyCoin(unittest.TestCase):
    def test_tag(self):
        r = _m(ECOIN, kind="item")
        self.assertEqual(r["tags"], ["coin_gate_attach:2:1:any:deck"])
        self.assertEqual(r["unsupported"], [])

    def test_detector_fields(self):
        got = E._detect_coin_gate_attach(ECOIN)
        self.assertEqual(got["coins"], 2)
        self.assertEqual(got["n"], 1)
        self.assertIsNone(got["etype"])

    def test_requires_both_heads(self):
        """门必须是「both of them are heads」，别的抛币条件不认领"""
        t = ECOIN.replace("If both of them are heads", "If at least 1 of them is heads")
        self.assertIsNone(E._detect_coin_gate_attach(t))


if __name__ == "__main__":
    unittest.main()
