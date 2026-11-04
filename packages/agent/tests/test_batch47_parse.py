# -*- coding: utf-8 -*-
"""第四十七批（2026-11-03）· K2 解析用例：可选加伤 / 反伤 / 抛币选状态全集

  · Gurdurr「Superpower」/ Dondozo ex「Dynamic Dive」
    「You may do N more damage. If you do, this Pokémon also does M damage to
     itself.」→ ``dmg_plus:N:recoil:M``（同一条 tag，保住「If you do」联动）
  · Grafaiai「Miraculous Paint」
    「Flip a coin. If heads, choose a Special Condition. …」
    → ``coin:heads:status_choose:poison+burn+sleep+paralyze+confuse``

负向守卫：
  · 无「You may」的纯反伤仍是 `self:M`（不得误落成 `dmg_plus:recoil`）；
  · 只有「choose Burned, Confused, or Poisoned」的三选一不得被全集正则吞掉。

运行：cd packages/agent && python -m unittest tests.test_batch47_parse -v
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
    return {"tags": out["tags"],
            "unsupported": [u["reason"] for u in out["unsupported"]]}


GURDURR = ("You may do 30 more damage. If you do, this Pokémon also does 30 "
           "damage to itself.")
DONDOZO = ("You may do 120 more damage. If you do, this Pokémon also does 50 "
           "damage to itself.")
GRAFAIAI = ("Flip a coin. If heads, choose a Special Condition. Your opponent's "
            "Active Pokémon is now affected by that Special Condition.")


class TestOptionalRecoil(unittest.TestCase):
    def test_gurdurr(self):
        r = _m(GURDURR)
        self.assertEqual(r["tags"], ["dmg_plus:30:recoil:30"])
        self.assertEqual(r["unsupported"], [])

    def test_dondozo(self):
        r = _m(DONDOZO)
        self.assertEqual(r["tags"], ["dmg_plus:120:recoil:50"])
        self.assertEqual(r["unsupported"], [])

    def test_plain_recoil_still_self(self):
        """没有「You may」→ 仍走既有 `self:M`（不得误落成联动 tag）"""
        r = _m("This Pokémon also does 30 damage to itself.")
        self.assertEqual(r["tags"], ["self:30"])


class TestCoinStatusChooseAll(unittest.TestCase):
    def test_grafaiai(self):
        r = _m(GRAFAIAI)
        self.assertEqual(
            r["tags"],
            ["coin:heads:status_choose:"
             "poison+burn+sleep+paralyze+confuse"])
        self.assertEqual(r["unsupported"], [])

    def test_fixed_three_status_not_swallowed(self):
        """Cradily 的三选一不得被「a Special Condition」全集正则冒充"""
        t = ("Flip a coin. If heads, choose Burned, Confused, or Poisoned. Your "
             "opponent's Active Pokémon is now affected by that Special Condition.")
        r = _m(t)
        self.assertEqual(r["tags"],
                         ["coin:heads:status_choose:burn+confuse+poison"])


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
