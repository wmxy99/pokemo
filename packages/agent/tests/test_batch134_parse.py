# -*- coding: utf-8 -*-
"""第一百三十四批（2026-11-08）· 解析用例：八类「整句精确」规则

覆盖（每条都配**近似写法不得认领**的反例）：

  · ``info:reveal_hand``          纯信息类「对手展示手牌」（完美信息等价）
  · ``counters_double:opp``       对手全场指示物翻倍（N's Vanilluxe）
  · ``atk_energy_block:2``        能量 ≤ 2 的宝可梦不能出招（Walrein）
  · ``win_if:own_prizes_eq:1``    恰好剩 1 张奖赏时使用即获胜（N's Sigilyph）
  · ``recon_bottom:7``            看**牌库底** 7 张取 1 张宝可梦（Dusk Ball）
  · ``discard_tool_senergy:1``    同一只身上的道具 + 特殊能量一起弃（Ruffian）
  · ``passive:hp_plus:150:if:special_energy``  条件式 +HP（Tyrantrum）
  · ``tool:hp_plus:70:holder:name:cynthias``   阵营限定 +HP（Cynthia's Power Weight）

**为什么每条都要反例**：这一批全部是 `^…$` 整句匹配。整句匹配的好处是
「多一句就拒收」；坏处是——如果正则写得过宽（比如没写 `$`），它会把
「Your opponent reveals their hand **and discards a card**」这种**还有后续动作**
的文本也认成「纯展示手牌」→ 后续动作被静默吞掉。反例就是钉这条边界。
"""
from __future__ import annotations

import unittest

from agent import effects as E


def _tags(text: str, kind: str = "attack") -> dict:
    sp = E.parse_rule(text, kind=kind, name="X")
    return E.to_engine_tags([sp])


def _only(text: str, kind: str = "attack") -> list:
    out = _tags(text, kind)
    return out.get("tags") or []


class TestInfoReveal(unittest.TestCase):
    def test_plain_reveal_is_modeled(self):
        self.assertEqual(_only("Your opponent reveals their hand."),
                         ["info:reveal_hand"])
        # 「reveal **his or her** hand」这种旧写法也应收
        self.assertEqual(_only("Your opponent reveals his or her hand"),
                         ["info:reveal_hand"])

    def test_reveal_plus_action_is_not_claimed(self):
        """带后续动作 → 不得认成「纯展示」（否则会静默吞掉别的效果）"""
        for t in ("Your opponent reveals their hand and discards a card.",
                  "Your opponent reveals their hand. Draw 3 cards."):
            self.assertNotIn("info:reveal_hand", _only(t), t)


class TestCountersDouble(unittest.TestCase):
    def test_exact_sentence(self):
        self.assertEqual(
            _only("Double the number of damage counters on each of your "
                  "opponent's Pokémon."),
            ["counters_double:opp"])

    def test_scoped_to_one_target_is_not_claimed(self):
        """只对**一只**翻倍是另一套语义（引擎无对应 tag）→ 不得认领"""
        self.assertNotIn(
            "counters_double:opp",
            _only("Double the number of damage counters on your opponent's "
                  "Active Pokémon."))


class TestAtkEnergyBlock(unittest.TestCase):
    def test_full_sentence(self):
        self.assertEqual(
            _only("During your opponent's next turn, Pokémon that have 2 or "
                  "less Energy attached can't use attacks. (This includes new "
                  "Pokémon that come into play.)"),
            ["atk_energy_block:2"])

    def test_without_parenthetical_is_not_claimed(self):
        """括号那句是「对之后上场的宝可梦也生效」—— 少了它口径就变了"""
        self.assertNotIn(
            "atk_energy_block:2",
            _only("During your opponent's next turn, Pokémon that have 2 or "
                  "less Energy attached can't use attacks."))


class TestWinIf(unittest.TestCase):
    def test_exactly_one_prize(self):
        self.assertEqual(
            _only("If you use this attack when you have exactly 1 Prize card "
                  "remaining, you win this game."),
            ["win_if:own_prizes_eq:1"])

    def test_or_more_prizes_is_not_claimed(self):
        self.assertNotIn(
            "win_if:own_prizes_eq:1",
            _only("If you use this attack when you have 1 or more Prize cards "
                  "remaining, you win this game."))


class TestReconBottom(unittest.TestCase):
    def test_dusk_ball(self):
        self.assertEqual(
            _only("Look at the bottom 7 cards of your deck. You may reveal a "
                  "Pokémon you find there and put it into your hand. Shuffle "
                  "the other cards back into your deck.", kind="item"),
            ["recon_bottom:7"])

    def test_top_look_is_not_claimed(self):
        """「top」是**牌库顶**（`recon:`），方向反了会取错牌"""
        self.assertNotIn(
            "recon_bottom:7",
            _only("Look at the top 7 cards of your deck. You may reveal a "
                  "Pokémon you find there and put it into your hand. Shuffle "
                  "the other cards back into your deck.", kind="item"))


class TestDiscardToolSenergy(unittest.TestCase):
    def test_ruffian(self):
        self.assertEqual(
            _only("Discard a Pokémon Tool and a Special Energy from 1 of your "
                  "opponent's Pokémon.", kind="supporter"),
            ["discard_tool_senergy:1"])

    def test_single_kind_is_not_claimed(self):
        """只弃其中一样是另一个 tag（`discard_tool:` / `discard_energy:`）"""
        self.assertNotIn(
            "discard_tool_senergy:1",
            _only("Discard a Pokémon Tool from 1 of your opponent's Pokémon.",
                  kind="supporter"))


class TestConditionalHp(unittest.TestCase):
    def test_tyrantrum(self):
        self.assertEqual(
            _only("If this Pokémon has any Special Energy attached, it gets "
                  "+150 HP.", kind="ability"),
            ["passive:hp_plus:150:if:special_energy"])

    def test_unconditional_form_stays_unconditional(self):
        """无条件形态**不能**被这条误加 `:if:`（否则永远不生效）"""
        tags = _only("This Pokémon gets +30 HP.", kind="ability")
        self.assertNotIn("passive:hp_plus:30:if:special_energy", tags)


class TestToolHpHolder(unittest.TestCase):
    def test_cynthia_power_weight(self):
        self.assertEqual(
            _only("The Cynthia's Pokémon this card is attached to gets +70 HP.",
                  kind="tool"),
            ["tool:hp_plus:70:holder:name:cynthia"])

    def test_no_faction_stays_unqualified(self):
        self.assertEqual(
            _only("The Pokémon this card is attached to gets +70 HP.",
                  kind="tool"),
            ["tool:hp_plus:70"])


if __name__ == "__main__":
    unittest.main()
