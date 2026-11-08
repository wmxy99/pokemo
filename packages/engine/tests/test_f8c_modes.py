# -*- coding: utf-8 -*-
"""F8（第三批）· 二选一模式支援者 + 具名能量代价（2026-11-08）

1. `mode_pick:a|b`（Kieran「Choose 1」）
   - 两个模式**互斥**，选哪个是玩家的决策：合法动作表必须**各列一条**，
     且 `mode` 字段指明选的是哪一条；
   - 不给 `mode` 就抛错（默认其中一个 = 白送一次换位或白送 30 伤害）；
   - 不可执行的模式要摘掉（没有后备时不该出现「换位」选项）。
2. `discard_named_energy:<key>:then:discard_defender`（TR Moltres ex）
   - 「If you do」是**条件后续**：没弃到具名能量 → 后续整段不执行；
   - 弃置对手战斗位**不是击倒**：不取奖赏、不发 `ko` 事件，但后备要顶上。

运行：cd packages/engine && python -m unittest tests.test_f8c_modes -v
"""
from __future__ import annotations

import unittest

from engine.battle import Battle
from engine.cards import (AbilityDef, AttackDef, EnergyDef, EnergyRef, PokeRef,
                          PokemonDef, TrainerDef, TrainerRef, make_pokemon)
from engine.state import GameConfig

KIERAN = "mode_pick:switch_self|team_boost:30:cat:ex+v"
MOLTRES = "discard_named_energy:teamrockets:then:discard_defender"


def _deck(tag: str = "A", n: int = 30) -> list:
    return [PokeRef(PokemonDef(f"Fill{tag}{i}", hp=100, ptype="colorless"))
            for i in range(n)]


def _battle(seed: int = 5) -> Battle:
    return Battle(_deck("A"), _deck("B"), seed=seed,
                  cfg=GameConfig(first_turn_no_attack=False,
                                 first_turn_no_supporter=False))


class TestKieran(unittest.TestCase):
    def _setup(self, with_bench=True):
        b = _battle()
        me = b.players[b.current]
        opp = b.players[1 - b.current]
        me.active = make_pokemon(PokemonDef(
            "Mine", hp=300, ptype="colorless",
            attacks=(AttackDef("Hit", cost=0, damage=50),)))
        me.active.add_energy("colorless")
        me.bench = ([make_pokemon(PokemonDef("Buddy", hp=100,
                                             ptype="colorless"))]
                    if with_bench else [])
        me.hand = [TrainerRef(TrainerDef(KIERAN, kind="supporter",
                                         name="Kieran"))]
        me.deck = _deck()
        opp.active = make_pokemon(PokemonDef("Foe", hp=900, ptype="colorless"))
        opp.deck = _deck("B")
        return b, me, opp

    def _acts(self, b):
        return [a for a in b.legal_actions()
                if a.get("verb") == "trainer" and a.get("name") == "Kieran"]

    def test_lists_one_action_per_mode(self):
        b, me, opp = self._setup()
        modes = sorted(a.get("mode") for a in self._acts(b))
        self.assertEqual(modes, ["switch_self", "team_boost:30:cat:ex+v"],
                         "两个模式必须各列一条（否则玩家没得选）")

    def test_switch_mode_swaps_active(self):
        b, me, opp = self._setup()
        r = b.step({"verb": "trainer", "hand": 0, "mode": "switch_self"})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(me.active.name, "Buddy", "选了换位却没换")

    def test_boost_mode_does_not_switch(self):
        b, me, opp = self._setup()
        r = b.step({"verb": "trainer", "hand": 0,
                    "mode": "team_boost:30:cat:ex+v"})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(me.active.name, "Mine", "选了增伤却也换了位（两个模式同时生效）")

    def test_boost_only_vs_ex_or_v(self):
        """增伤只对 {ex} / {V} 守方生效（`cat:ex,v`）"""
        b, me, opp = self._setup()
        r = b.step({"verb": "trainer", "hand": 0,
                    "mode": "team_boost:30:cat:ex+v"})
        self.assertTrue(r["ok"], r.get("error"))
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(opp.active.damage, 50,
                         "对普通宝可梦也 +30 了（{ex}/{V} 限定丢了）")

        b2, me2, opp2 = self._setup()
        opp2.active = make_pokemon(PokemonDef("FoeEx", hp=900,
                                              ptype="colorless", is_ex=True))
        b2.step({"verb": "trainer", "hand": 0, "mode": "team_boost:30:cat:ex+v"})
        b2.step({"verb": "attack", "attack": 0})
        self.assertEqual(opp2.active.damage, 80, "对 {ex} 没 +30")

    def test_missing_mode_is_error(self):
        b, me, opp = self._setup()
        r = b.step({"verb": "trainer", "hand": 0})
        self.assertFalse(r["ok"], "没选模式也执行了（引擎替玩家做了选择）")

    def test_switch_not_listed_without_bench(self):
        b, me, opp = self._setup(with_bench=False)
        modes = [a.get("mode") for a in self._acts(b)]
        self.assertNotIn("switch_self", modes, "没有后备却列出换位（必然失败）")


class TestNamedEnergyThen(unittest.TestCase):
    def _setup(self, with_tr=True):
        b = _battle()
        me = b.players[b.current]
        opp = b.players[1 - b.current]
        me.active = make_pokemon(PokemonDef(
            "Moltres", hp=220, ptype="fire",
            attacks=(AttackDef("Evil Incineration", cost=0, damage=0,
                               effects=(MOLTRES,)),)))
        if with_tr:
            me.active.senergy = [EnergyRef(EnergyDef(
                "fire", name="Team Rocket's Energy", tag="senergy:team_rocket"))]
        me.active.add_energy("fire")
        me.bench = [make_pokemon(PokemonDef("Backup", hp=100,
                                            ptype="colorless"))]
        me.hand, me.deck = [], _deck()
        opp.active = make_pokemon(PokemonDef("Victim", hp=200,
                                             ptype="colorless"))
        opp.bench = [make_pokemon(PokemonDef("Victim2", hp=100,
                                             ptype="colorless"))]
        opp.deck = _deck("B")
        return b, me, opp

    def test_discards_opponent_active_without_prizes(self):
        b, me, opp = self._setup(with_tr=True)
        prizes_before = me.prizes_left
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(opp.active.name, "Victim2", "后备没顶上")
        self.assertIn("Victim", opp.discard, "对手战斗位没进弃牌堆")
        self.assertEqual(me.prizes_left, prizes_before,
                         "弃置被当成了击倒（白拿奖赏）")
        self.assertFalse([e for e in b.events if e.get("type") == "knockout"],
                         "弃置不该发 knockout 事件")
        self.assertEqual(me.active.senergy, [], "具名能量没被弃掉")

    def test_without_named_energy_nothing_happens(self):
        b, me, opp = self._setup(with_tr=False)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(opp.active.name, "Victim",
                         "没有具名能量却仍弃了对手战斗位（If you do 没判）")
        self.assertEqual(opp.discard, [], "对手场上卡被弃掉了")


if __name__ == "__main__":
    unittest.main()
