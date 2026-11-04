# -*- coding: utf-8 -*-
"""第四十四批（2026-11-03）· K2 解析用例：抛币子效果补完

覆盖 2 张真实卡：

  · Team Rocket's Venture Bomb（道具）
    「Flip a coin. If heads, put 2 damage counters on 1 of your opponent's
     Pokémon. If tails, put 2 damage counters on your Active Pokémon.」→
    `coin_flip:heads:counters:2:scope:opp:1|tails:counters:2:scope:self`
    （**同一枚币的双分支**必须是一条 tag；拆成两条 `coin:` 会抛两次）
  · Dustox（特性「Boisterous Wind」）
    「Flip a coin. If heads, put an Energy attached to your opponent's Active
     Pokémon into their hand.」→ `coin:heads:bounce:energy:opp_active:1`

负向守卫：
  · 「into **your** hand」（把自己能量拿回来）→ 不是对手侧回手，不得认领；
  · 两个独立的单分支（不是同一枚币）不得被合并成 fork；
  · 「put N damage counters on your Active Pokémon」不得落成 `self:N`
    （那是「受到伤害」，会走伤害修正）。

运行：cd packages/agent && python -m unittest tests.test_batch44_parse -v
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


def _m(text: str, kind: str) -> dict:
    sp = E.parse_rule(text, kind=kind, name="X")
    out = E.to_engine_tags([sp])
    return {"tags": out["tags"],
            "unsupported": [u["reason"] for u in out["unsupported"]]}


VENTURE_BOMB = ("Flip a coin. If heads, put 2 damage counters on 1 of your "
                "opponent's Pokémon. If tails, put 2 damage counters on your "
                "Active Pokémon.")
DUSTOX = ("Once during your turn, you may use this Ability. Flip a coin. "
          "If heads, put an Energy attached to your opponent's Active Pokémon "
          "into their hand.")


class TestVentureBomb(unittest.TestCase):
    def test_fork_single_tag(self):
        r = _m(VENTURE_BOMB, "item")
        self.assertEqual(
            r["tags"],
            ["coin_flip:heads:counters:2:scope:opp:1|tails:counters:2:scope:self"])
        self.assertEqual(r["unsupported"], [],
                         "双分支已精确建模，不得再留「指示物未识别」占位")

    def test_no_self_damage_mapping(self):
        """反面是**指示物**，不得落 `self:20`（那是受到伤害）"""
        r = _m(VENTURE_BOMB, "item")
        self.assertNotIn("coin:tails:self:20", r["tags"])
        self.assertNotIn("coin:tails:spread_free:20", r["tags"])

    def test_heads_only_single_branch(self):
        """只有正面分支时仍是普通 `coin:`（不生成 fork）"""
        r = _m("Flip a coin. If heads, put 2 damage counters on 1 of your "
               "opponent's Pokémon.", "attack")
        self.assertEqual(r["tags"], ["coin:heads:counters:2:scope:opp:1"])


class TestDustox(unittest.TestCase):
    def test_ability_coin_bounce(self):
        r = _m(DUSTOX, "ability")
        self.assertEqual(r["tags"], ["coin:heads:bounce:energy:opp_active:1"])
        self.assertEqual(r["unsupported"], [],
                         "能量回手已建模，不得再留「需 coin 嵌套」占位")

    def test_own_hand_rejected(self):
        """「into your hand」不是对手侧回手 → 不得认领成 bounce:opp_active"""
        t = ("Once during your turn, you may use this Ability. Flip a coin. "
             "If heads, put an Energy attached to your opponent's Active "
             "Pokémon into your hand.")
        r = _m(t, "ability")
        self.assertNotIn("coin:heads:bounce:energy:opp_active:1", r["tags"])


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
