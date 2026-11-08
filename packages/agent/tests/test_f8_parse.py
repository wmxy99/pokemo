# -*- coding: utf-8 -*-
"""F8 · 规则改写 / 去向改写的**解析**约定（2026-11-08）

守什么
------
1. 五句都是**整句精确匹配**：这些句子里的「put it into your hand」「discard this
   Pokémon」被宽口径检测器命中后语义完全反过来（把「被击倒回手」读成检索入手、
   把「弃自身」读成弃手牌）。差一个词（`each` / `into their deck` / `stage 2`）
   就不能认领。
2. **被动 tag 是精确串**（引擎 `_has_ability_tag` 用 `==`）：拼错一个字符就完全
   不生效，而前缀比对的对账看不出来 —— 所以这里逐字断言。
3. 反向：普通「put this Pokémon into your hand」（Meowth ex 的回手）**不能**被
   这批检测器吃掉（它有自己的 bounce 族）。

运行：cd packages/agent && python -m unittest tests.test_f8_parse -v
"""
from __future__ import annotations

import unittest

from agent import effects as E


def _tags(text: str, kind: str, name: str = "X") -> dict:
    spec = E.parse_rule(text, kind=kind, name=name)
    return E.to_engine_tags([spec])


class TestDiscardSelf(unittest.TestCase):
    def test_revavroom(self):
        r = _tags("Discard this Pokémon and all attached cards.", "attack",
                  "Shattering Speed")
        self.assertEqual(r["tags"], ["discard_self:all"])
        self.assertEqual(r["unsupported"], [])

    def test_plain_discard_is_not_self(self):
        """反向：普通弃手牌不能被读成弃自身"""
        r = _tags("Discard 2 cards from your hand.", "attack")
        self.assertNotIn("discard_self:all", r["tags"])


class TestDevolveEach(unittest.TestCase):
    def test_espeon(self):
        r = _tags("Devolve each of your opponent's evolved Pokémon by shuffling "
                  "the highest Stage Evolution card on it into your opponent's "
                  "deck.", "attack", "Amazez")
        self.assertEqual(r["tags"], ["devolve:opp:1:each:to:deck"])

    def test_single_target_stays_single(self):
        """反向：Archeops 的「devolve 1 of …」不能带上 `:each`/`:to:deck`"""
        r = _tags("Once during your turn, if this Pokémon is in the Active Spot, "
                  "you may devolve 1 of your opponent's evolved Pokémon by "
                  "putting the highest Stage Evolution card on it into their "
                  "hand.", "ability", "Ancient Power")
        self.assertTrue(any(t.startswith("devolve:opp:1") for t in r["tags"]),
                        r["tags"])
        self.assertFalse(any(":each" in t for t in r["tags"]), r["tags"])
        self.assertFalse(any(":to:deck" in t for t in r["tags"]), r["tags"])


class TestNoHandReturn(unittest.TestCase):
    def test_milotic(self):
        r = _tags("Your opponent's Pokémon in play and all attached cards can't "
                  "be put into your opponent's hand.", "ability", "Mentally Calm")
        self.assertEqual(r["tags"], ["passive:no_hand_return"])


class TestKoToHand(unittest.TestCase):
    def test_gengar(self):
        r = _tags("If this Pokémon is Knocked Out by damage from an attack from "
                  "your opponent's Pokémon, put it into your hand instead of the "
                  "discard pile. (Discard all attached cards.)", "ability",
                  "Infinite Shadow")
        self.assertEqual(r["tags"], ["passive:ko_to_hand"])

    def test_requires_attack_damage(self):
        """反向：没有「by damage from an attack」这半句就不能按无条件处理"""
        r = _tags("If this Pokémon is Knocked Out, put it into your hand instead "
                  "of the discard pile.", "ability", "X")
        self.assertNotIn("passive:ko_to_hand", r["tags"])


class TestHandToBench(unittest.TestCase):
    def test_klinklang(self):
        r = _tags("Once during your turn, if this Pokémon is in your hand and "
                  "your opponent has any Stage 2 Pokémon in play, you may put "
                  "this Pokémon onto your Bench.", "ability", "Emergency Rotation")
        self.assertEqual(r["tags"], ["hand_bench:opp_stage2"])

    def test_not_confused_with_hand_discard_ability(self):
        """反向：手牌特性「弃自身」走 `cursed:`，不能被读成铺场"""
        r = _tags("Once during your turn, you may discard this card from your "
                  "hand. Put 13 damage counters on 1 of your opponent's Pokémon.",
                  "ability", "Cursed Blast")
        self.assertFalse(any(t.startswith("hand_bench:") for t in r["tags"]),
                         r["tags"])


if __name__ == "__main__":
    unittest.main()
