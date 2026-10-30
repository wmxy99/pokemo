# -*- coding: utf-8 -*-
"""PokéLab Engine · 「对手手牌操作」用例（2026-10-30 第三十二批）

覆盖 ``opp_hand:<op>:<sel>[:<n>|:all]``：

  · 弃牌 / 放牌库底两种操作；
  · 单类别筛选与 ``+`` 并集（Rotom 的 item+tool）；
  · ``all`` 与显式张数（Eri 的 "up to 2"）；
  · ``action['hand_pick']`` 显式选择及其两类非法输入（越界 / 不匹配筛选）；
  · 无符合条件的牌 → 效果不执行（不是错误）；
  · 招式侧与训练家侧两条全链路（``step``）；
  · 合法动作表过滤：对手手里没有符合的牌时训练家卡**不得**可打出。

三条**方向/口径**守卫（这一族最容易错的地方）：
  1. 改的是**对手**手牌，不是自己的（``discard_opp_*`` 与自己的 ``discard`` 相邻）；
  2. 取的是**手牌顺序**第一张（确定性），不是随机 —— 与本文件同时对照
     ``discard_opp_random`` 的随机口径，二者不可混用；
  3. 弃掉的牌进的是**对手**弃牌堆。

运行：cd packages/engine && python -m unittest tests.test_opp_hand -v
"""
from __future__ import annotations

import random
import unittest

from engine.battle import (Battle, _opp_hand_matches, _parse_opp_hand_tag)
from engine.cards import (EnergyDef, EnergyRef, PokeRef, PokemonDef,
                          TrainerDef, TrainerRef, make_pokemon)
from engine.state import GameConfig, PlayerState

FILLER = 15


def arena(seed: int = 7, turn: int = 2):
    """最小可控战场：双方各 1 只 200HP 无色宝可梦，双方牌库均已填充。"""
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
        p.deck = [EnergyRef() for _ in range(FILLER)]
    return b


def _item(name: str = "ItemA"):
    return TrainerRef(TrainerDef("x", kind="item", name=name))


def _tool(name: str = "ToolB"):
    return TrainerRef(TrainerDef("x", kind="tool", name=name))


def _supporter(name: str = "SupC"):
    return TrainerRef(TrainerDef("x", kind="supporter", name=name))


def _stadium(name: str = "StadD"):
    return TrainerRef(TrainerDef("x", kind="stadium", name=name))


def _energy(color: str = "grass", special: bool = False):
    if special:
        return EnergyRef(EnergyDef(color, name="Special E", tag="senergy:test"))
    return EnergyRef(EnergyDef(color, name=f"{color} Energy"))


def _poke(name: str = "Pikachu", stage: int = 0):
    return PokeRef(PokemonDef(name, hp=100, ptype="lightning", stage=stage))


def _with_attack(b, effects, damage: int = 0):
    from engine.cards import AttackDef
    b.players[0].active = make_pokemon(PokemonDef(
        "Attacker", hp=500, ptype="colorless",
        attacks=(AttackDef("Test", cost=0, damage=damage,
                           effects=tuple(effects)),)))


def _opp_hand(b, *cards):
    """直接设定玩家 1（对手）的手牌，顺序即「手牌顺序」。"""
    b.players[1].hand = list(cards)
    return b.players[1]


def _trainer(b, tag: str, kind: str = "item", name: str = "T"):
    ref = TrainerRef(TrainerDef(tag, kind=kind, name=name))
    b.players[0].hand = [ref]
    return ref


class TestParse(unittest.TestCase):
    """tag 解析：合法形态 / 非法形态"""

    def test_minimal_defaults_to_one(self):
        self.assertEqual(_parse_opp_hand_tag("opp_hand:discard:any"),
                         ("discard", "any", 1))

    def test_all_and_count(self):
        self.assertEqual(_parse_opp_hand_tag("opp_hand:discard:item+tool:all"),
                         ("discard", "item+tool", "all"))
        self.assertEqual(_parse_opp_hand_tag("opp_hand:discard:item:2"),
                         ("discard", "item", 2))

    def test_bad_op_raises(self):
        with self.assertRaises(ValueError):
            _parse_opp_hand_tag("opp_hand:burn:any")

    def test_bad_count_raises(self):
        for bad in ("0", "x", "-1"):
            with self.assertRaises(ValueError):
                _parse_opp_hand_tag(f"opp_hand:discard:item:{bad}")

    def test_unknown_selector_raises(self):
        """未知筛选词**必须抛错** —— 静默按「任意牌」处理等于效果凭空变宽"""
        with self.assertRaises(ValueError):
            _opp_hand_matches(_item(), "nonsense")

    def test_selector_kinds(self):
        self.assertTrue(_opp_hand_matches(_item(), "item"))
        self.assertFalse(_opp_hand_matches(_tool(), "item"))
        self.assertTrue(_opp_hand_matches(_tool(), "item+tool"))
        self.assertTrue(_opp_hand_matches(_supporter(), "trainer"))
        self.assertFalse(_opp_hand_matches(_stadium(), "supporter"))
        self.assertTrue(_opp_hand_matches(_energy(), "energy"))
        self.assertTrue(_opp_hand_matches(_energy(), "basic_energy"))
        self.assertFalse(_opp_hand_matches(_energy(special=True), "basic_energy"))
        self.assertTrue(_opp_hand_matches(_energy(special=True), "special_energy"))
        self.assertTrue(_opp_hand_matches(_poke(), "pokemon"))
        self.assertTrue(_opp_hand_matches(_poke(), "basic_pokemon"))
        self.assertFalse(_opp_hand_matches(_poke(stage=2), "basic_pokemon"))


class TestDiscard(unittest.TestCase):
    """``op = discard``"""

    def test_union_of_item_and_tool(self):
        """Rotom：弃掉对手手牌里**所有** Item 与 Tool（其余保留）"""
        b = arena()
        o = _opp_hand(b, _item("I1"), _tool("T1"), _energy(), _poke(), _item("I2"))
        detail = b._do_opp_hand(b.players[0], "opp_hand:discard:item+tool:all", {})
        self.assertEqual(detail, "opp discard 3")
        self.assertEqual(len(o.hand), 2)                    # 只剩能量 + 宝可梦
        self.assertIn("I1", o.discard)                      # 进的是**对手**弃牌堆
        self.assertIn("T1", o.discard)
        self.assertIn("I2", o.discard)
        self.assertEqual(b.players[0].discard, [])          # 自己弃牌堆不受影响

    def test_count_limit(self):
        """Eri：最多 2 张 → 有 3 张时只取 2 张"""
        b = arena()
        o = _opp_hand(b, _item("I1"), _item("I2"), _item("I3"), _energy())
        b._do_opp_hand(b.players[0], "opp_hand:discard:item:2", {})
        self.assertEqual(len(o.hand), 2)
        self.assertEqual(sorted(o.discard), ["I1", "I2"])   # 手牌顺序前两张

    def test_upto_less_than_n_takes_all(self):
        """「up to N」在只有 1 张符合时不报错，取满实际可用的"""
        b = arena()
        o = _opp_hand(b, _item("I1"), _energy())
        b._do_opp_hand(b.players[0], "opp_hand:discard:item:2", {})
        self.assertEqual(o.discard, ["I1"])
        self.assertEqual(len(o.hand), 1)

    def test_no_match_is_noop(self):
        b = arena()
        o = _opp_hand(b, _energy(), _poke())
        detail = b._do_opp_hand(b.players[0], "opp_hand:discard:item", {})
        self.assertEqual(detail, "")
        self.assertEqual(len(o.hand), 2)
        self.assertEqual(o.discard, [])

    def test_empty_hand_is_noop(self):
        b = arena()
        o = _opp_hand(b)
        self.assertEqual(b._do_opp_hand(b.players[0],
                                        "opp_hand:discard:any:all", {}), "")
        self.assertEqual(o.discard, [])

    def test_targets_opponent_not_self(self):
        """方向守卫：动的必须是对手手牌"""
        b = arena()
        _opp_hand(b, _item("I1"))
        b.players[0].hand = [_item("Mine")]
        b._do_opp_hand(b.players[0], "opp_hand:discard:item:all", {})
        self.assertEqual([getattr(c.tdef, "name", "") for c in b.players[0].hand],
                         ["Mine"])

    def test_deterministic_by_hand_order(self):
        """口径守卫：**不是随机** —— 同一局面重复执行结果必须一致"""
        picks = []
        for seed in (1, 2, 3, 99):
            b = arena(seed=seed)
            o = _opp_hand(b, _item("I1"), _item("I2"), _item("I3"))
            b._do_opp_hand(b.players[0], "opp_hand:discard:item", {})
            picks.append(o.discard[:])
        self.assertEqual(picks, [["I1"], ["I1"], ["I1"], ["I1"]])


class TestBottom(unittest.TestCase):
    """``op = bottom`` —— 放到对手**牌库底**"""

    def test_energy_goes_to_bottom(self):
        b = arena()
        o = _opp_hand(b, _energy("water"), _poke())
        deck_before = list(o.deck)
        b._do_opp_hand(b.players[0], "opp_hand:bottom:energy", {})
        self.assertEqual(len(o.hand), 1)
        self.assertEqual(len(o.deck), len(deck_before) + 1)
        # 牌库顶是列表**末尾**，底是**前端** —— 方向错了会让「放底」变成
        # 「必定抽到」，是静默加强
        self.assertIsInstance(o.deck[0], EnergyRef)
        self.assertNotIn("water Energy", o.discard)

    def test_no_energy_is_noop(self):
        b = arena()
        o = _opp_hand(b, _item(), _poke())
        deck_before = len(o.deck)
        self.assertEqual(b._do_opp_hand(b.players[0],
                                        "opp_hand:bottom:energy", {}), "")
        self.assertEqual(len(o.deck), deck_before)
        self.assertEqual(len(o.hand), 2)


class TestHandPick(unittest.TestCase):
    """``action['hand_pick']`` 显式选择（供策略侧挑最优）"""

    def test_explicit_pick(self):
        b = arena()
        o = _opp_hand(b, _item("I1"), _item("I2"), _item("I3"))
        b._do_opp_hand(b.players[0], "opp_hand:discard:item",
                       {"hand_pick": [2]})
        self.assertEqual(o.discard, ["I3"])

    def test_pick_multiple_respects_n(self):
        b = arena()
        o = _opp_hand(b, _item("I1"), _item("I2"), _item("I3"))
        b._do_opp_hand(b.players[0], "opp_hand:discard:item:2",
                       {"hand_pick": [0, 2]})
        self.assertEqual(sorted(o.discard), ["I1", "I3"])

    def test_pick_out_of_range_raises(self):
        b = arena()
        _opp_hand(b, _item("I1"))
        with self.assertRaises(ValueError):
            b._do_opp_hand(b.players[0], "opp_hand:discard:item",
                           {"hand_pick": [5]})

    def test_pick_not_matching_selector_raises(self):
        """显式选了一张不符合筛选的牌 → 抛错，**不得**悄悄换成别的牌"""
        b = arena()
        _opp_hand(b, _item("I1"), _energy())
        with self.assertRaises(ValueError):
            b._do_opp_hand(b.players[0], "opp_hand:discard:item",
                           {"hand_pick": [1]})

    def test_pick_non_int_raises(self):
        b = arena()
        _opp_hand(b, _item("I1"))
        with self.assertRaises(ValueError):
            b._do_opp_hand(b.players[0], "opp_hand:discard:item",
                           {"hand_pick": ["0"]})


class TestTrainerPath(unittest.TestCase):
    """训练家全链路（``step``）—— 同样按内容断言，不数张数（回合推进会抽牌）"""

    def test_supporter_discards_then_goes_to_own_discard(self):
        """Eri：弃对手 2 张 Item，自己那张支援者卡进**自己的**弃牌堆"""
        b = arena()
        o = _opp_hand(b, _item("I1"), _item("I2"), _item("I3"))
        _trainer(b, "opp_hand:discard:item:2", kind="supporter", name="Eri")
        r = b.step({"verb": "trainer", "hand": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(sorted(o.discard), ["I1", "I2"])
        self.assertEqual(len(b.players[0].discard_refs), 1)   # 只有 Eri 自己
        self.assertIn("Eri", b.players[0].discard)
        self.assertTrue(b.players[0].supporter_used)

    def test_item_bottom_energy(self):
        """Energy Swatter：把对手 1 张能量放牌库底"""
        b = arena()
        o = _opp_hand(b, _energy("fire"), _item("I1"))
        target = o.hand[0]
        _trainer(b, "opp_hand:bottom:energy", kind="item", name="Energy Swatter")
        r = b.step({"verb": "trainer", "hand": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertIs(o.deck[0], target)
        self.assertNotIn(target, o.hand)

    def test_not_legal_when_no_matching_card(self):
        """对手手里没有 Item → Eri **不得**出现在合法动作表"""
        b = arena()
        _opp_hand(b, _energy(), _poke())
        _trainer(b, "opp_hand:discard:item", kind="supporter", name="Eri")
        self.assertEqual([a for a in b.legal_actions()
                          if a.get("verb") == "trainer"], [])

    def test_legal_when_matching_card(self):
        b = arena()
        _opp_hand(b, _item("I1"))
        _trainer(b, "opp_hand:discard:item", kind="supporter", name="Eri")
        self.assertEqual(len([a for a in b.legal_actions()
                              if a.get("verb") == "trainer"]), 1)


class TestAttackPath(unittest.TestCase):
    """招式全链路（``step``）

    ⚠ 断言只按**内容**判断、不数手牌张数：``step`` 打完招式会推进回合
    （turn_end → 对手回合开始抽 1 张），直接断言 ``len(hand)`` 会把抽牌算进去。
    """

    def test_rotom_discards_item_and_tool(self):
        b = arena()
        o = _opp_hand(b, _item("I1"), _tool("T1"), _energy(), _poke())
        _with_attack(b, ["opp_hand:discard:item+tool:all"])
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(sorted(o.discard), ["I1", "T1"])
        left = [getattr(getattr(c, "tdef", None), "name", None) for c in o.hand]
        self.assertNotIn("I1", left)
        self.assertNotIn("T1", left)

    def test_attack_with_no_match_still_resolves(self):
        """招式本身仍成立（不因「无匹配牌」而失败），且不误动别的牌"""
        b = arena()
        o = _opp_hand(b, _energy())
        _with_attack(b, ["opp_hand:discard:item"], damage=30)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(o.discard, [])

    def test_bottom_from_attack(self):
        """N's Purrloin：任一 1 张放牌库底（取手牌顺序第一张）"""
        b = arena()
        o = _opp_hand(b, _item("I1"), _energy())
        first = o.hand[0]
        _with_attack(b, ["opp_hand:bottom:any"], damage=30)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertIs(o.deck[0], first)          # 前端 = 牌库底
        self.assertNotIn(first, o.hand)


class TestBadTagSurfaced(unittest.TestCase):
    """非法 tag 必须让出手**失败**，而不是静默不生效"""

    def test_unknown_op_fails_step(self):
        b = arena()
        _opp_hand(b, _item("I1"))
        _with_attack(b, ["opp_hand:burn:any"])
        r = b.step({"verb": "attack", "attack": 0})
        self.assertFalse(r["ok"])
        self.assertIn("opp_hand", str(r.get("error")))


if __name__ == "__main__":
    unittest.main()
