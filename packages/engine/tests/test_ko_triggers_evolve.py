# -*- coding: utf-8 -*-
"""第十批（2026-10-28）· 引擎用例：进化触发 / 「in this way」后续 / 被击倒触发

覆盖：
  · ``evolve_act:<tag1>|<tag2>``       进化那一刻执行（Whimsicott / Dachsbun / Kadabra）
  · ``:if_prev``                         「If you healed any damage in this way」
  · ``heal:all:evolved``                 己方全部进化宝可梦
  · ``search_bench:1:name:<k>+<k>``      按卡名铺后备（Silcoon / Ninjask）
  · ``ko_prize_plus:1:coin``             Togekiss（对手战斗位被击倒 → 抛币多取 1）
  · ``on_ko:counter:N``                  Maractus（自身被击倒 → 反制攻击方）
  · ``on_ko:energy_to_hand:water``       Huntail（{W} 被击倒 → 基础{W}能量回手）
"""
from __future__ import annotations
import random
import unittest

from engine.battle import Battle
from engine.cards import (AbilityDef, AttackDef, EnergyDef, EnergyRef, PokeRef,
                          PokemonDef, make_pokemon)
from engine.state import GameConfig, PlayerState


def _arena(seed: int = 7):
    cfg = GameConfig()
    b = object.__new__(Battle)
    b.cfg = cfg
    b.seed = seed
    b.rng = random.Random(seed)
    b.players = [PlayerState(name="A", prizes_left=cfg.prizes),
                 PlayerState(name="B", prizes_left=cfg.prizes)]
    b.current = 0
    b.turn_number = 1
    b.turn_counts = [1, 1]
    b.phase = "main"
    b.finished = False
    b.winner = None
    b.win_reason = None
    b.events = []
    b.stadium = None
    b.players[0].active = make_pokemon(
        PokemonDef("Attacker", hp=300, ptype="colorless", attacks=()))
    b.players[1].active = make_pokemon(
        PokemonDef("Defender", hp=300, ptype="colorless", attacks=()))
    for p in b.players:
        p.deck = [EnergyRef() for _ in range(20)]
    return b


def _mon(name, hp=300, ptype="colorless", stage=0, abilities=(), attacks=()):
    return make_pokemon(PokemonDef(
        name, hp=hp, ptype=ptype, stage=stage,
        abilities=tuple(abilities), attacks=tuple(attacks)))


def _ab(tag: str) -> AbilityDef:
    return AbilityDef("A", tag=tag, location="field")


class TestEvolveAct(unittest.TestCase):
    def test_draw_on_evolve(self):
        b = _arena()
        fp = _mon("Evolved", stage=1, abilities=(_ab("evolve_act:draw:2"),))
        b.players[0].bench.append(fp)
        before = len(b.players[0].hand)
        b._trigger_evolve_attach(fp)
        self.assertEqual(len(b.players[0].hand), before + 2)

    def test_evolve_act_is_not_usable_every_turn(self):
        """进化触发的特性**不得**出现在每回合合法特性表里（否则等于免费再用）"""
        b = _arena()
        fp = _mon("Evolved", stage=1, abilities=(_ab("evolve_act:draw:2"),))
        b.players[0].active = fp
        acts = b.legal_actions()
        self.assertFalse(any(a.get("verb") == "ability" for a in acts))

    def test_heal_then_discard_if_healed(self):
        b = _arena()
        fp = _mon("Whimsicott", stage=1,
                  abilities=(_ab("evolve_act:heal:all:active:type:grass|"
                                 "discard_energy:all:if_prev"),))
        active = _mon("GrassActive", hp=100, ptype="grass")
        active.damage = 60
        active.add_energy("grass", 2)
        b.players[0].active = active
        b.players[0].bench.append(fp)
        b._trigger_evolve_attach(fp)
        self.assertEqual(active.damage, 0, "应回满")
        self.assertEqual(active.total_energy(), 0, "回复发生后应弃光能量")

    def test_discard_skipped_when_nothing_healed(self):
        b = _arena()
        fp = _mon("Whimsicott", stage=1,
                  abilities=(_ab("evolve_act:heal:all:active:type:grass|"
                                 "discard_energy:all:if_prev"),))
        active = _mon("GrassActive", hp=100, ptype="grass")
        active.add_energy("grass", 2)          # 满 HP → 没回任何伤害
        b.players[0].active = active
        b.players[0].bench.append(fp)
        b._trigger_evolve_attach(fp)
        self.assertEqual(active.total_energy(), 2,
                         "没回复 → 不得弃能（in this way 条件）")

    def test_heal_evolved_scope(self):
        b = _arena()
        fp = _mon("Dachsbun", stage=1,
                  abilities=(_ab("evolve_act:heal:all:evolved|"
                                 "discard_energy:all:evolved:if_prev"),))
        evo = _mon("Stage1", hp=120, stage=1)
        evo.damage = 50
        evo.add_energy("colorless", 1)
        basic = _mon("Basic", hp=100, stage=0)
        basic.damage = 40
        basic.add_energy("colorless", 1)
        b.players[0].bench.extend([fp, evo, basic])
        b._trigger_evolve_attach(fp)
        self.assertEqual(evo.damage, 0, "进化宝可梦应回满")
        self.assertEqual(evo.total_energy(), 0, "进化宝可梦的能量应被弃光")
        self.assertEqual(basic.damage, 40, "基础宝可梦不在范围内")

    def test_search_bench_by_name(self):
        b = _arena()
        fp = _mon("Silcoon", stage=1,
                  abilities=(_ab("evolve_act:search_bench:1:name:silcoon+"
                                 "cascoon"),))
        deck = []
        for n in ("Silcoon", "Cascoon", "Wurmple"):
            deck.append(PokeRef(PokemonDef(n, hp=60, ptype="grass")))
        b.players[0].deck = deck
        before = len(b.players[0].bench)
        b._trigger_evolve_attach(fp)
        self.assertEqual(len(b.players[0].bench), before + 1)
        self.assertIn(b.players[0].bench[-1].name, ("Silcoon", "Cascoon"))


class TestKnockoutTriggers(unittest.TestCase):
    def test_prize_bonus_coin(self):
        """Togekiss：对手战斗位被击倒 → 抛币正面多取 1 张"""
        b = _arena(seed=7)          # random(7) ≈ 0.32 → heads
        kiss = _mon("Togekiss", hp=100, abilities=(_ab("ko_prize_plus:1:coin"),))
        b.players[0].bench.append(kiss)
        b.players[1].active = _mon("Victim", hp=100)
        b.players[1].bench.append(_mon("Reserve", hp=100))
        b.players[0].active = _mon("Hitter", attacks=(AttackDef(
            "A", cost=0, damage=100),))
        before = b.players[0].prizes_left
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[0].prizes_left, before - 2,
                         "基础 1 + 抛币正面 1")

    def test_prize_bonus_not_stacking(self):
        """卡面「doesn't stack」→ 两张也只多取 1 张"""
        b = _arena(seed=7)
        for i in range(2):
            b.players[0].bench.append(
                _mon(f"Togekiss{i}", hp=100,
                     abilities=(_ab("ko_prize_plus:1:coin"),)))
        b.players[1].active = _mon("Victim", hp=100)
        b.players[1].bench.append(_mon("Reserve", hp=100))
        b.players[0].active = _mon("Hitter", attacks=(AttackDef(
            "A", cost=0, damage=100),))
        before = b.players[0].prizes_left
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[0].prizes_left, before - 2)

    def test_on_ko_counter_hits_attacker(self):
        b = _arena()
        cactus = _mon("Maractus", hp=100, abilities=(_ab("on_ko:counter:60"),))
        b.players[1].active = cactus
        b.players[0].active = _mon("Hitter", hp=300, attacks=(AttackDef(
            "A", cost=0, damage=100),))
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[0].active.damage, 60,
                         "被击倒时应给攻击方 6 个指示物")

    def test_on_ko_counter_only_when_active(self):
        """卡面限定「in the Active Spot」→ 后备被击倒不触发"""
        b = _arena()
        cactus = _mon("Maractus", hp=100, abilities=(_ab("on_ko:counter:60"),))
        b.players[1].bench.append(cactus)
        b.players[1].active.damage = 10
        b.players[0].active = _mon("Hitter", hp=300, attacks=(AttackDef(
            "A", cost=0, damage=100, effects=("snipe:opp_bench:100:1",)),))
        b.step({"verb": "attack", "attack": 0, "snipe": [1]})
        self.assertNotEqual(b.players[0].active.damage, 60)

    def test_on_ko_energy_to_hand(self):
        b = _arena()
        huntail = _mon("Huntail", hp=100, abilities=(_ab(
            "on_ko:energy_to_hand:water"),))
        b.players[1].bench.append(huntail)
        target = _mon("WaterMon", hp=100, ptype="water")
        target.add_energy("water", 2)
        b.players[1].active = target
        b.players[0].active = _mon("Hitter", attacks=(AttackDef(
            "A", cost=0, damage=100),))
        before = len(b.players[1].hand)
        b.step({"verb": "attack", "attack": 0})
        # +2 来自回收的基础水能量；+1 来自对手回合开始的常规抽牌
        self.assertEqual(len(b.players[1].hand), before + 3,
                         "被击倒者身上的 2 个基础水能量应回手")

    def test_on_ko_energy_wrong_type(self):
        b = _arena()
        huntail = _mon("Huntail", hp=100, abilities=(_ab(
            "on_ko:energy_to_hand:water"),))
        b.players[1].bench.append(huntail)
        target = _mon("FireMon", hp=100, ptype="fire")
        target.add_energy("fire", 2)
        b.players[1].active = target
        b.players[0].active = _mon("Hitter", attacks=(AttackDef(
            "A", cost=0, damage=100),))
        before = len(b.players[1].hand)
        b.step({"verb": "attack", "attack": 0})
        # 只 +1（对手回合开始的常规抽牌）；不回收火能量
        self.assertEqual(len(b.players[1].hand), before + 1,
                         "非 {W} 宝可梦被击倒不触发")


if __name__ == "__main__":
    unittest.main()
