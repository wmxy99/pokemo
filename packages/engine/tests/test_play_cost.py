# -*- coding: utf-8 -*-
"""PokéLab Engine · 「打出代价」用例（2026-11-02 第三十六批）

覆盖 ``TrainerDef.play_cost``（``"discard:<n>"``）的三件事：

1. **不满足不能打** —— 付不出时该卡**不进**合法动作表（卡面是
   「You can use this card **only if** you discard …」，不是「打了才发现付不出」）；
2. **付账真的发生** —— 手牌真的少 n 张，且进**自己的**弃牌堆；
3. **顺序是先付账、后效果** —— 这条最容易被写反且后果隐蔽：
   先做效果再弃牌时，``draw_to:6`` 会先把「本不该够的手牌」补满，再弃 1 张，
   最终手牌比正确顺序**少 1 张**（或等价地，用一张本不该能打的卡打出了效果）。

另有 ``ultra_ball`` 的**双重扣牌回归守卫**：该 tag 的代价内建在
``_trainer_ultra_ball`` 里（`cards_real.json` 是人工卡池、没有 `play_cost`
字段），若统一执行 play_cost 就会弃 4 张。

运行：cd packages/engine && python -m unittest tests.test_play_cost -v
"""
from __future__ import annotations

import random
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG = HERE.parent
for p in (str(PKG), str(PKG.parent / "agent")):
    if p not in sys.path:
        sys.path.insert(0, p)

from engine.battle import Battle, _parse_play_cost, _play_cost_ok  # noqa: E402
from engine.cards import (EnergyDef, EnergyRef, PokeRef, PokemonDef,  # noqa: E402
                          TrainerDef, TrainerRef, make_pokemon)
from engine.state import GameConfig, PlayerState  # noqa: E402

FILLER = 12          # 牌库填充张数（无色能量）


def arena(seed: int = 7, turn: int = 2):
    """最小可控战场：双方各 1 只 200HP 无色宝可梦；玩家 0 的牌库由用例填充。"""
    cfg = GameConfig()
    b = object.__new__(Battle)
    b.cfg = cfg
    b.seed = seed
    b.rng = random.Random(seed)
    b.players = [PlayerState(name="A", prizes_left=cfg.prizes),
                 PlayerState(name="B", prizes_left=cfg.prizes)]
    b.current = 0
    b.turn_number = 1
    b.turn_counts = [turn, turn]
    b.phase = "main"
    b.finished = False
    b.winner = None
    b.win_reason = None
    b.events = []
    b.stadium = None
    b._ko_by_attack = [0, 0]
    b.players[0].active = make_pokemon(
        PokemonDef("Holder", hp=200, ptype="colorless", attacks=()))
    b.players[1].active = make_pokemon(
        PokemonDef("Opp", hp=200, ptype="colorless", attacks=()))
    for p in b.players:
        p.deck = [EnergyRef(EnergyDef("colorless")) for _ in range(FILLER)]
    return b


def _energy(color: str = "grass"):
    return EnergyRef(EnergyDef(color, name=f"{color} Energy"))


def _cost_card(tag: str = "draw:2", cost: str = "discard:1",
               kind: str = "supporter", name: str = "CostCard"):
    return TrainerRef(TrainerDef(tag, kind=kind, name=name, play_cost=cost))


def _hand(b, *cards):
    b.players[0].hand = list(cards)


def _legals(b):
    return [a for a in b.legal_actions() if a["verb"] == "trainer"]


class TestParsePlayCost(unittest.TestCase):
    """``_parse_play_cost`` 的形态与非法输入"""

    def test_valid(self):
        self.assertEqual(_parse_play_cost("discard:1"), ("discard", 1, None))
        self.assertEqual(_parse_play_cost("discard:3"), ("discard", 3, None))

    def test_energy_cost_shape(self):
        """限定属性的基本能量代价（第三十八批，Blowtorch）

        第三项是**属性**，不是可选装饰：`discard_energy:1:fire` 与
        `discard:1` 是两种代价（前者只能弃基本火能量）。
        """
        self.assertEqual(_parse_play_cost("discard_energy:1:fire"),
                         ("discard_energy", 1, "fire"))
        self.assertEqual(_parse_play_cost("discard_energy:2:water"),
                         ("discard_energy", 2, "water"))

    def test_energy_cost_bad_shape_raises(self):
        for bad in ("discard_energy:1", "discard_energy:0:fire",
                    "discard_energy:1:", "discard_energy:x:fire"):
            with self.assertRaises(ValueError, msg=bad):
                _parse_play_cost(bad)

    def test_empty_is_none(self):
        self.assertIsNone(_parse_play_cost(""))
        self.assertIsNone(_parse_play_cost(None))

    def test_unknown_raises(self):
        """未知形态必须抛错 —— 静默当「无代价」会让该卡免费（静默加强）"""
        with self.assertRaises(ValueError):
            _parse_play_cost("burn:2")
        with self.assertRaises(ValueError):
            _parse_play_cost("discard:0")
        with self.assertRaises(ValueError):
            _parse_play_cost("discard:x")

    def test_payable_counts_self_as_occupied(self):
        """`another card` 意味着本卡不能当代价 → 判据是「手牌 - 1 >= n」"""
        b = arena()
        p = b.players[0]
        p.hand = [_energy("fire"), _energy("water")]
        self.assertTrue(_play_cost_ok(p, "discard:1"))
        # 只有 1 张手牌（就是本卡）→ 付不出 1 张
        p.hand = [_energy("fire")]
        self.assertFalse(_play_cost_ok(p, "discard:1"))
        p.hand = [_energy("fire"), _energy("water")]
        self.assertFalse(_play_cost_ok(p, "discard:2"))
        self.assertTrue(_play_cost_ok(p, "discard:2", self_in_hand=False))

    def test_no_cost_always_ok(self):
        b = arena()
        self.assertTrue(_play_cost_ok(b.players[0], ""))
        self.assertTrue(_play_cost_ok(b.players[0], "discard:5") is False)


class TestLegals(unittest.TestCase):
    """付不出 → **不进**合法动作表（而不是「打了才报错」）"""

    def test_listed_when_payable(self):
        b = arena()
        _hand(b, _cost_card(cost="discard:1"), _energy("fire"), _energy("water"))
        self.assertEqual([a["name"] for a in _legals(b)], ["CostCard"])

    def test_not_listed_when_short(self):
        b = arena()
        _hand(b, _cost_card(cost="discard:2"), _energy("fire"))
        self.assertEqual(_legals(b), [])

    def test_not_listed_when_exactly_self(self):
        """手牌只剩本卡一张：`another card` 无从可弃 → 不可打"""
        b = arena()
        _hand(b, _cost_card(cost="discard:1"))
        self.assertEqual(_legals(b), [])

    def test_zero_cost_unaffected(self):
        b = arena()
        _hand(b, _cost_card(cost=""), _energy("fire"))
        self.assertEqual([a["name"] for a in _legals(b)], ["CostCard"])

    def test_broken_cost_shape_not_listed(self):
        """代价形态不认识 → 不列出（宁可不可用，也不免费放行）"""
        b = arena()
        _hand(b, _cost_card(cost="burn:1"), _energy("fire"), _energy("water"))
        self.assertEqual(_legals(b), [])


class TestPayment(unittest.TestCase):
    """付账真的发生，且弃的是**自己的**、本卡不当代价"""

    def test_pays_one_card(self):
        b = arena()
        p = b.players[0]
        _hand(b, _cost_card(cost="discard:1"), _energy("fire"), _energy("water"))
        r = b.step({"verb": "trainer", "hand": 0})
        self.assertTrue(r["ok"], r.get("error"))
        # 初始 3：弃 1（代价）→ 2，抽 2 → 4，移除本卡 → 3
        self.assertEqual(len(p.hand), 3)
        # 弃牌堆 = 代价牌 + 本卡自己
        self.assertEqual(len(p.discard_refs), 2)
        self.assertIn("water Energy", p.discard)   # 缺省从末尾取
        self.assertIn("CostCard", p.discard)

    def test_default_picks_from_tail(self):
        """缺省从**末尾**往前取（与既有 Ultra Ball / Earthen Vessel 口径一致）"""
        b = arena()
        p = b.players[0]
        _hand(b, _cost_card(cost="discard:2"), _energy("fire"),
              _energy("water"), _energy("psychic"))
        b.step({"verb": "trainer", "hand": 0})
        self.assertIn("psychic Energy", p.discard)
        self.assertIn("water Energy", p.discard)
        self.assertNotIn("fire Energy", p.discard)

    def test_discard_pick(self):
        b = arena()
        p = b.players[0]
        _hand(b, _cost_card(cost="discard:1"), _energy("fire"), _energy("water"))
        r = b.step({"verb": "trainer", "hand": 0, "discard_pick": 1})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertIn("fire Energy", p.discard)
        self.assertNotIn("water Energy", p.discard)

    def test_discard_pick_list(self):
        b = arena()
        p = b.players[0]
        _hand(b, _cost_card(cost="discard:2"), _energy("fire"),
              _energy("water"), _energy("psychic"))
        r = b.step({"verb": "trainer", "hand": 0, "discard_pick": [1, 3]})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertIn("fire Energy", p.discard)
        self.assertIn("psychic Energy", p.discard)
        self.assertNotIn("water Energy", p.discard)

    def test_pick_self_rejected(self):
        """`discard_pick` 指到本卡 → 抛错（本卡不能当代价），不悄悄换别的牌"""
        b = arena()
        p = b.players[0]
        _hand(b, _cost_card(cost="discard:1"), _energy("fire"), _energy("water"))
        r = b.step({"verb": "trainer", "hand": 0, "discard_pick": 0})
        self.assertFalse(r["ok"])
        self.assertIn("discard_pick", r["error"])
        self.assertEqual(len(p.hand), 3)           # 什么都没发生

    def test_pick_out_of_range_rejected(self):
        b = arena()
        _hand(b, _cost_card(cost="discard:1"), _energy("fire"), _energy("water"))
        r = b.step({"verb": "trainer", "hand": 0, "discard_pick": 9})
        self.assertFalse(r["ok"])
        self.assertIn("discard_pick", r["error"])

    def test_pick_wrong_count_rejected(self):
        b = arena()
        _hand(b, _cost_card(cost="discard:2"), _energy("fire"),
              _energy("water"), _energy("psychic"))
        r = b.step({"verb": "trainer", "hand": 0, "discard_pick": [1]})
        self.assertFalse(r["ok"])
        self.assertIn("discard_pick", r["error"])

    def test_short_hand_raises(self):
        """绕过合法动作表直接下达 → 引擎抛错（守卫在 `_do_trainer` 也有一道）"""
        b = arena()
        _hand(b, _cost_card(cost="discard:2"), _energy("fire"))
        r = b.step({"verb": "trainer", "hand": 0})
        self.assertFalse(r["ok"])
        self.assertIn("代价", r["error"])

    def test_event_records_cost(self):
        b = arena()
        _hand(b, _cost_card(cost="discard:1"), _energy("fire"), _energy("water"))
        b.step({"verb": "trainer", "hand": 0})
        self.assertIn("cost", b.events[-1])
        self.assertIn("discard 1", b.events[-1]["cost"])


class TestOrder(unittest.TestCase):
    """**先付账、后效果** —— 写反了不会报错，只会让手牌数悄悄不同"""

    def test_cost_before_effect(self):
        """``draw_to:6`` 是判别顺序的探针

        ``draw_to:N`` 的口径是「结算后（**不含本卡**）手牌达到 N 张」，
        即 ``need = N - (len(hand) - 1)``。初始 3 张（本卡 + 2 能量），代价弃 1：
          · 正确（先付账）：3 − 1 = 2 → need = 6 − 1 = 5 → 7 → 移除本卡 → **6**
          · 错误（先效果）：3 → need = 6 − 2 = 4 → 7 → 弃 1 = 6 → 移除本卡 → **5**
        """
        b = arena()
        p = b.players[0]
        _hand(b, _cost_card(tag="draw_to:6", cost="discard:1"),
              _energy("fire"), _energy("water"))
        r = b.step({"verb": "trainer", "hand": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(p.hand), 6,
                         "手牌数说明代价在效果**之后**才执行（顺序反了）")

    def test_effect_not_granted_when_cost_unpayable(self):
        """付不出时**效果也不能发生**（不是「先拿效果再失败」）"""
        b = arena()
        p = b.players[0]
        deck_before = len(p.deck)
        _hand(b, _cost_card(tag="draw:3", cost="discard:2"), _energy("fire"))
        r = b.step({"verb": "trainer", "hand": 0})
        self.assertFalse(r["ok"])
        self.assertEqual(len(p.deck), deck_before, "代价没付却已经抽了牌")
        self.assertEqual(len(p.hand), 2)


class TestBuiltinCostTags(unittest.TestCase):
    """回归守卫：内建代价的 tag 不得被 play_cost 再扣一次"""

    def test_ultra_ball_pays_exactly_two(self):
        b = arena()
        p = b.players[0]
        b.players[0].deck.append(
            PokeRef(PokemonDef("Pikachu", hp=60, ptype="lightning", attacks=())))
        ref = TrainerRef(TrainerDef("ultra_ball", kind="item",
                                    name="Ultra Ball",
                                    play_cost="discard:2"))
        _hand(b, ref, _energy("fire"), _energy("water"),
              _energy("grass"), _energy("psychic"))
        r = b.step({"verb": "trainer", "hand": 0})
        self.assertTrue(r["ok"], r.get("error"))
        # 5 − 2（代价）= 3 → 检索 1 张入手 = 4 → 移除本卡 = 3
        # 若双重扣牌会得到 1（−4 +1 −1）
        self.assertEqual(len(p.hand), 3, "Ultra Ball 被双重扣牌了")
        self.assertEqual(len(p.discard_refs), 3)   # 2 张代价 + 本卡


class TestEnergyCost(unittest.TestCase):
    """限定属性的基本能量代价（2026-11-02 第三十八批 · Blowtorch PFL 86）

    卡面：「You can use this card only if you discard a **Basic {R} Energy** card
    from your hand.」

    与 `discard:<n>` 的三处差异各有用例守着：
      · 候选集**只有**该属性的基本能量（拿别的颜色不能顶）；
      · **特殊能量不能**当代价（卡面写的是 Basic）；
      · 属性不符 / 数量不够 → **不进**合法动作表（不是「打了才失败」）。
    """

    def _special(self, color: str = "fire"):
        return EnergyRef(EnergyDef(color, name=f"Special {color} Energy",
                                   tag="senergy:legacy"))

    def test_payable_only_with_matching_basic(self):
        b = arena()
        p = b.players[0]
        p.hand = [_energy("fire"), _energy("water")]
        self.assertTrue(_play_cost_ok(p, "discard_energy:1:fire"))
        self.assertFalse(_play_cost_ok(p, "discard_energy:2:fire"))
        # 属性不符 → 付不出（不能拿水能量顶火能量）
        self.assertFalse(_play_cost_ok(p, "discard_energy:1:water") is False)
        p.hand = [_energy("water")]
        self.assertFalse(_play_cost_ok(p, "discard_energy:1:fire"))

    def test_special_energy_not_valid_cost(self):
        """卡面写 Basic → 特殊能量卡不能当代价"""
        b = arena()
        p = b.players[0]
        p.hand = [self._special("fire")]
        self.assertFalse(_play_cost_ok(p, "discard_energy:1:fire"))

    def test_not_listed_without_matching_energy(self):
        b = arena()
        _hand(b, _cost_card(cost="discard_energy:1:fire", kind="item"),
              _energy("water"), _energy("grass"))
        self.assertEqual(_legals(b), [])

    def test_listed_and_pays_matching_energy(self):
        b = arena()
        p = b.players[0]
        _hand(b, _cost_card(cost="discard_energy:1:fire", kind="item"),
              _energy("water"), _energy("fire"))
        self.assertEqual([a["name"] for a in _legals(b)], ["CostCard"])
        r = b.step({"verb": "trainer", "hand": 0})
        self.assertTrue(r["ok"], r.get("error"))
        # 缺省从末尾（= fire）取 → 弃的必须是火能量
        self.assertIn("fire Energy", p.discard)
        self.assertNotIn("water Energy", p.discard)
        self.assertIn("CostCard", p.discard)

    def test_pick_wrong_type_rejected(self):
        """`discard_pick` 指到非火能量 → 抛错，不悄悄换牌"""
        b = arena()
        p = b.players[0]
        _hand(b, _cost_card(cost="discard_energy:1:fire", kind="item"),
              _energy("water"), _energy("fire"))
        r = b.step({"verb": "trainer", "hand": 0, "discard_pick": 1})
        self.assertFalse(r["ok"])
        self.assertIn("discard_pick", r["error"])
        self.assertEqual(len(p.hand), 3)


if __name__ == "__main__":
    unittest.main()
