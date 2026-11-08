# -*- coding: utf-8 -*-
"""F5 · 静态属性改写的**解析**约定（2026-11-08）

守什么
------
1. `weak_change:<新弱点>:<过滤属性>` —— 两个属性**方向相反**且都在同一句里：
   过滤属性是「your opponent's **{N}** Pokémon」（守方的属性），新弱点是
   「is now **{P}**」。写反的后果是「对手全队的弱点被改成 N 属性」，
   在多数对局里根本打不出来差异。
2. 引擎读的是**不带** `passive:` 前缀的 `weak_change:`（`_weakness_override`
   用 `startswith("weak_change:")`）—— 套进 `static_passive` 会加前缀，
   于是「解析有 tag、引擎读不到」＝静默无效。这里逐字断言。
3. `passive:energy_double:<type>`：属性来自 `provides {G}{G}` 的那个符号；
   写错就是「草增值」变成别的属性增值。

运行：cd packages/agent && python -m unittest tests.test_f5_parse -v
"""
from __future__ import annotations

import unittest

from agent import effects as E


def _tags(text: str, kind: str = "ability", name: str = "X") -> dict:
    spec = E.parse_rule(text, kind=kind, name=name)
    return E.to_engine_tags([spec])


class TestWeakChange(unittest.TestCase):
    TXT = ("The Weakness of each of your opponent’s {N} Pokémon in play is now "
           "{P}. (Apply Weakness as ×2.)")

    def test_direction(self):
        r = _tags(self.TXT, name="Fairy Zone")
        self.assertEqual(r["tags"], ["weak_change:psychic:dragon"])
        self.assertEqual(r["unsupported"], [])

    def test_no_passive_prefix(self):
        """引擎读 `weak_change:` 前缀；带 `passive:` 就读不到（静默无效）"""
        r = _tags(self.TXT)
        for t in r["tags"]:
            self.assertFalse(t.startswith("passive:"),
                             f"多了 passive: 前缀，引擎读不到：{t}")

    def test_without_parenthetical_still_matches(self):
        """括号里的「Apply Weakness as ×2」是规则提醒，可有可无"""
        r = _tags("The Weakness of each of your opponent's {N} Pokémon in play "
                  "is now {P}.")
        self.assertEqual(r["tags"], ["weak_change:psychic:dragon"])


class TestEnergyDouble(unittest.TestCase):
    TXT = ("Each Basic {G} Energy attached to all of your Pokémon provides "
           "{G}{G} Energy. The effect of Wild Growth doesn’t stack.")

    def test_tag(self):
        r = _tags(self.TXT, name="Wild Growth")
        self.assertEqual(r["tags"], ["passive:energy_double:grass"])

    def test_single_gain_is_not_doubling(self):
        """反向：只提供 1 点（没有 {G}{G}）不是增值"""
        r = _tags("Each Basic {G} Energy attached to all of your Pokémon "
                  "provides {G} Energy.")
        self.assertNotIn("passive:energy_double:grass", r["tags"])


class TestEvolveAs(unittest.TestCase):
    """Eevee ex「Rainbow DNA」：绕过进化链，但仍限 {ex}"""

    TXT = ("This Pokémon can evolve into any Pokémon {ex} that evolves from "
           "Eevee if you play it from your hand onto this Pokémon. (This Pokémon "
           "can't evolve during your first turn or the turn you play it.)")

    def test_tag(self):
        r = _tags(self.TXT, name="Rainbow DNA")
        self.assertEqual(r["tags"], ["passive:evolve_as:eevee:ex"])
        self.assertEqual(r["unsupported"], [],
                         "括号里的「can't evolve during your first turn」被当成"
                         "未建模 → 整卡会被拒收")

    def test_key_follows_card_text(self):
        """改写的名字来自卡面，不能写死 eevee"""
        r = _tags("This Pokémon can evolve into any Pokémon {ex} that evolves "
                  "from Pikachu if you play it from your hand onto this Pokémon.")
        self.assertEqual(r["tags"], ["passive:evolve_as:pikachu:ex"])


if __name__ == "__main__":
    unittest.main()
