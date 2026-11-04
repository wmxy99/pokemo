# -*- coding: utf-8 -*-
"""第五十一~五十二批（2026-11-04）· 引擎用例：训练家复合句 + 白名补充

五十一批（一条 tag 承载两个动作，因为训练家**只认一个**卡级 tag）：
  · ``status_multi:<a>+<b>``   多状态（Dangerous Laser：Burned **and** Confused）
  · ``research:<n>``           弃全部手牌再抽 N（Carmine）
  · ``switch_draw_to:<n>``     换位并抽到 N（Surfer）
  · ``gust_confuse``           拉对手后备 + 新战斗位混乱（Lisia's Appeal）

五十二批（解析早已产出、引擎也早已支持，白名单漏了前缀 = 机制从未可用）：
  · 招式侧 ``research:``（Raging Bolt ex「Burst Roar」）
  · 特性侧 ``mill:``（Durant ex「Sudden Shearing」）/ ``gust``（Meowstic）
"""
from __future__ import annotations
import random
import unittest

from engine.battle import Battle
from engine.cards import (AbilityDef, EnergyDef, EnergyRef, PokemonDef,
                          TrainerDef, TrainerRef, make_pokemon)
from engine.state import GameConfig, PlayerState


def _arena(seed=7):
    cfg = GameConfig()
    b = object.__new__(Battle)
    b.cfg = cfg
    b.seed = seed
    b.rng = random.Random(seed)
    b.players = [PlayerState(name="A", prizes_left=cfg.prizes),
                 PlayerState(name="B", prizes_left=cfg.prizes)]
    b.current = 0
    b.turn_number = 2
    b.turn_counts = [2, 2]
    b.phase = "main"
    b.finished = False
    b.winner = None
    b.win_reason = None
    b.events = []
    b.stadium = None
    b._ko_by_attack = [0, 0]
    b.players[0].active = make_pokemon(
        PokemonDef("A", hp=100, ptype="fire", attacks=()))
    b.players[0].bench = [
        make_pokemon(PokemonDef("A2", hp=100, ptype="fire", attacks=()))]
    b.players[1].active = make_pokemon(
        PokemonDef("B", hp=100, ptype="water", attacks=()))
    b.players[1].bench = [
        make_pokemon(PokemonDef("B2", hp=100, ptype="water", attacks=()))]
    b.players[0].deck = [EnergyRef(EnergyDef("fire")) for _ in range(12)]
    b.players[1].deck = [EnergyRef(EnergyDef("water")) for _ in range(12)]
    return b


def _card(tag, kind="item"):
    return TrainerRef(TrainerDef(tag, kind=kind, name=tag))


class TestStatusMulti(unittest.TestCase):
    def test_applies_both(self):
        b = _arena()
        base = {"verb": "trainer", "hand": 0, "name": "x",
                "tag": "status_multi:burn+confuse"}
        b.players[0].hand = [_card("status_multi:burn+confuse")]
        r = b.step(base)
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.status, {"burn", "confuse"})

    def test_unknown_status_raises(self):
        """非法状态名必须抛错（静默忽略 = 卡照打但什么都不发生）"""
        b = _arena()
        b.players[0].hand = [_card("status_multi:burn+bogus")]
        r = b.step({"verb": "trainer", "hand": 0, "name": "x",
                    "tag": "status_multi:burn+bogus"})
        self.assertFalse(r["ok"])
        self.assertIn("未知状态", str(r.get("error")))


class TestResearch(unittest.TestCase):
    def test_discard_all_then_draw(self):
        b = _arena()
        b.players[0].hand = [_card("research:5", "supporter"),
                             EnergyRef(EnergyDef("fire")),
                             EnergyRef(EnergyDef("fire"))]
        r = b.step({"verb": "trainer", "hand": 0, "name": "x",
                    "tag": "research:5"})
        self.assertTrue(r["ok"], r.get("error"))
        # 弃掉另外 2 张，再抽 5 张（本卡自己也会离手）
        self.assertEqual(len(b.players[0].hand), 5)

    def test_attack_side_research(self):
        """招式侧 `research:`（第五十二批）：Raging Bolt ex 的 Burst Roar"""
        b = _arena()
        b.players[0].hand = [EnergyRef(EnergyDef("fire")),
                             EnergyRef(EnergyDef("fire"))]
        before_deck = len(b.players[0].deck)
        b._apply_attack_effects(b.players[0].active, ("research:6",),
                                action={})
        self.assertEqual(len(b.players[0].hand), 6)
        self.assertEqual(len(b.players[0].deck), before_deck - 6)


class TestSwitchDrawTo(unittest.TestCase):
    def test_switch_then_draw_to(self):
        b = _arena()
        b.players[0].hand = [_card("switch_draw_to:5", "supporter")]
        old_active = b.players[0].active
        r = b.step({"verb": "trainer", "hand": 0, "name": "x",
                    "tag": "switch_draw_to:5", "target": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertIsNot(b.players[0].active, old_active)
        # 训练家卡此刻还在手上 → 抽到「5 张（含本卡）」= 再抽 4 张
        self.assertEqual(len(b.players[0].hand), 5)


class TestGustConfuse(unittest.TestCase):
    def test_new_active_confused(self):
        b = _arena()
        b.players[0].hand = [_card("gust_confuse", "supporter")]
        r = b.step({"verb": "trainer", "hand": 0, "name": "x",
                    "tag": "gust_confuse", "target": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.name, "B2")
        self.assertIn("confuse", b.players[1].active.status)

    def test_legals_need_opp_bench(self):
        b = _arena()
        b.players[1].bench = []
        b.players[0].hand = [_card("gust_confuse", "supporter")]
        tags = [a.get("tag") for a in b.legal_actions() if a["verb"] == "trainer"]
        self.assertNotIn("gust_confuse", tags)


class TestAbilityMillGust(unittest.TestCase):
    """第五十二批：特性侧 `mill:` / `gust`（白名补充）"""

    def _with_ability(self, tag):
        b = _arena()
        b.players[0].active = make_pokemon(PokemonDef(
            "A", hp=100, ptype="water", attacks=(),
            abilities=(AbilityDef("Ab", tag=tag, location="field"),)))
        return b

    def test_ability_mill(self):
        b = self._with_ability("mill:2:opp")
        before = len(b.players[1].deck)
        r = b._do_ability(b.players[0], {"name": "Ab", "tag": "mill:2:opp",
                                         "pos": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(b.players[1].deck), before - 2)

    def test_ability_gust(self):
        b = self._with_ability("gust")
        r = b._do_ability(b.players[0], {"name": "Ab", "tag": "gust",
                                         "pos": 0, "target": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.name, "B2")

    def test_ability_gust_not_legal_without_bench(self):
        b = self._with_ability("gust")
        b.players[1].bench = []
        names = [a.get("name") for a in b._ability_legals(b.players[0],
                                                          b.players[1])]
        self.assertNotIn("Ab", names)


if __name__ == "__main__":
    unittest.main()
