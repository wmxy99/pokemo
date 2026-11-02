# -*- coding: utf-8 -*-
"""PokéLab Engine · Blowtorch 的**二选一**弃置（2026-11-02 第三十八批）

卡面：
    Discard a Pokémon Tool or Special Energy card from 1 of your opponent's
    Pokémon, **or** discard a Stadium in play.

这条效果最容易被写成「只做后半句」—— 而它不会报错、测试也不会红：
`discard_stadium` 一直是既有能力，看起来「已经实现了」。后果是这张卡在
「对手没有场地」的一半局面上打出去**什么都不发生**（静默削弱），且对账脚本
查不出来（它只核对已产出的 tag 与卡面是否**一致**，不核对卡面有几句、
产出了几条）。

用例覆盖：
  · 缺省取「对手身上的道具」（先战斗位、后后备；同一只先道具再特殊能量）；
  · 对手没有道具但有**特殊**能量 → 弃特殊能量（基本能量**不是**合法对象）；
  · 两支都没有 → 弃场上竞技场；
  · `mode="stadium"` 显式强制走竞技场分支（对手有道具时也一样）；
  · 三者皆无 → 不进合法动作表；强行下达则抛错（不静默空转）。

运行：cd packages/engine && python -m unittest tests.test_discard_tool_or_senergy -v
"""
from __future__ import annotations

import random
import unittest

from engine.battle import Battle
from engine.cards import (EnergyDef, EnergyRef, PokemonDef, TrainerDef,
                          TrainerRef, make_pokemon)
from engine.state import GameConfig, PlayerState

BLOWTORCH = "discard_tool_or_senergy:1:opp"


def arena(seed: int = 7):
    cfg = GameConfig()
    b = object.__new__(Battle)
    b.cfg = cfg
    b.seed = seed
    b.rng = random.Random(seed)
    b.players = [PlayerState(name="A", prizes_left=cfg.prizes),
                 PlayerState(name="B", prizes_left=cfg.prizes)]
    b.current = 0
    b.turn_number = 1
    b.turn_counts = [2, 2]
    b.phase = "main"
    b.finished = False
    b.winner = None
    b.win_reason = None
    b.events = []
    b.stadium = None
    b._ko_by_attack = [0, 0]
    b.players[0].active = make_pokemon(
        PokemonDef("Holder", hp=200, ptype="colorless", attacks=()))
    b.players[0].deck = [EnergyRef() for _ in range(5)]
    b.players[1].active = make_pokemon(
        PokemonDef("OppAct", hp=200, ptype="colorless", attacks=()))
    b.players[1].deck = [EnergyRef() for _ in range(5)]
    return b


def _tool(name: str = "Air Balloon") -> TrainerRef:
    return TrainerRef(TrainerDef("tool:retreat_minus:2", kind="tool", name=name))


def _special(color: str = "fire", name: str = "Special F") -> EnergyRef:
    return EnergyRef(EnergyDef(color, name=name, tag="senergy:legacy"))


def _card(pc: str = "discard_energy:1:fire") -> TrainerRef:
    return TrainerRef(TrainerDef(BLOWTORCH, kind="item", name="Blowtorch",
                                 play_cost=pc))


def _legals(b):
    return [a for a in b.legal_actions() if a["verb"] == "trainer"]


class TestToolBranch(unittest.TestCase):
    def test_discards_tool_from_active(self):
        b = arena()
        opp = b.players[1]
        ref = _tool()
        opp.active.tool = "tool:retreat_minus:2"
        opp.active.tool_name = "Air Balloon"
        opp.active.tool_ref = ref
        detail = b._trainer_discard_tool_or_senergy(
            b.players[0], BLOWTORCH, {})
        self.assertIn("tool:Air Balloon", detail)
        self.assertIsNone(opp.active.tool)
        self.assertTrue(any(x is ref for x in opp.discard_refs))

    def test_active_before_bench(self):
        """确定性顺序：先战斗位、后后备"""
        b = arena()
        opp = b.players[1]
        opp.bench = [make_pokemon(PokemonDef("OppB", hp=100,
                                             ptype="colorless", attacks=()))]
        opp.bench[0].tool = "tool:x"
        opp.bench[0].tool_name = "Bench Tool"
        opp.active.tool = "tool:y"
        opp.active.tool_name = "Active Tool"
        detail = b._trainer_discard_tool_or_senergy(b.players[0], BLOWTORCH, {})
        self.assertIn("Active Tool", detail)
        self.assertIsNotNone(opp.bench[0].tool)      # 后备那张不动


class TestSpecialEnergyBranch(unittest.TestCase):
    def test_discards_special_energy_when_no_tool(self):
        b = arena()
        opp = b.players[1]
        ref = _special()
        opp.active.senergy.append(ref)
        detail = b._trainer_discard_tool_or_senergy(b.players[0], BLOWTORCH, {})
        self.assertIn("senergy", detail)
        self.assertEqual(opp.active.senergy, [])
        self.assertTrue(any(x is ref for x in opp.discard_refs))

    def test_basic_energy_is_not_a_target(self):
        """卡面写 Special Energy → 基本能量不是合法对象"""
        b = arena()
        opp = b.players[1]
        opp.active.energy = {"fire": 2}
        b.stadium = {"name": "Some Stadium", "tag": "stadium:hp_plus:10",
                     "owner": 0}
        detail = b._trainer_discard_tool_or_senergy(b.players[0], BLOWTORCH, {})
        # 基本能量不能弃 → 退到竞技场分支
        self.assertIn("stadium", detail)
        self.assertEqual(opp.active.energy, {"fire": 2})
        self.assertIsNone(b.stadium)


class TestStadiumBranch(unittest.TestCase):
    def test_falls_back_to_stadium(self):
        b = arena()
        b.stadium = {"name": "Jamming Tower", "tag": "stadium:no_tool_effect",
                     "owner": 0}
        detail = b._trainer_discard_tool_or_senergy(b.players[0], BLOWTORCH, {})
        self.assertIn("stadium:Jamming Tower", detail)
        self.assertIsNone(b.stadium)
        self.assertIn("Jamming Tower", b.players[0].discard)

    def test_mode_stadium_forced_even_with_tool(self):
        """显式 `mode="stadium"`：即使对手有道具也走竞技场（玩家可选）"""
        b = arena()
        opp = b.players[1]
        opp.active.tool = "tool:x"
        opp.active.tool_name = "Air Balloon"
        b.stadium = {"name": "S", "tag": "stadium:hp_plus:10", "owner": 0}
        detail = b._trainer_discard_tool_or_senergy(
            b.players[0], BLOWTORCH, {"mode": "stadium"})
        self.assertIn("stadium", detail)
        self.assertIsNotNone(opp.active.tool)        # 道具没被动
        self.assertIsNone(b.stadium)

    def test_nothing_to_discard_raises(self):
        b = arena()
        with self.assertRaises(ValueError):
            b._trainer_discard_tool_or_senergy(b.players[0], BLOWTORCH, {})


class TestLegals(unittest.TestCase):
    """两支都不可用时**不进**合法动作表（否则 AI 会反复尝试空转的卡）"""

    def test_not_listed_when_nothing(self):
        b = arena()
        b.players[0].hand = [_card(), EnergyRef(EnergyDef("fire", name="F"))]
        self.assertEqual(_legals(b), [])

    def test_listed_when_stadium_present(self):
        b = arena()
        b.stadium = {"name": "S", "tag": "stadium:hp_plus:10", "owner": 0}
        b.players[0].hand = [_card(), EnergyRef(EnergyDef("fire", name="F"))]
        self.assertEqual([a["name"] for a in _legals(b)], ["Blowtorch"])

    def test_listed_when_opp_has_tool(self):
        b = arena()
        b.players[1].active.tool = "tool:x"
        b.players[0].hand = [_card(), EnergyRef(EnergyDef("fire", name="F"))]
        self.assertEqual([a["name"] for a in _legals(b)], ["Blowtorch"])

    def test_not_listed_without_energy_cost(self):
        """代价付不出（没有基本火能量）→ 不进表，即使有可弃目标"""
        b = arena()
        b.stadium = {"name": "S", "tag": "stadium:hp_plus:10", "owner": 0}
        b.players[0].hand = [_card(), EnergyRef(EnergyDef("water", name="W"))]
        self.assertEqual(_legals(b), [])


if __name__ == "__main__":
    unittest.main()
