# -*- coding: utf-8 -*-
"""第十五批（2026-10-28）· 引擎用例：检索附能的目标限定

覆盖：
  · ``attach:N[:type]:deck:target:bench``            Smoochum（附到后备）
  · ``attach:N[:type]:deck:target:any``              Teal Mask Ogerpon（己方任意一只）
  · ``attach_energy:N:type:deck:target:bench_type:<c>[:counters:N]``  Toxtricity
  · ``attach:…:anyenergy``                            Shaymin（可附特殊能量）
"""
from __future__ import annotations
import random
import unittest

from engine.battle import Battle, _attach_target_fp
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


class TestAttachTarget(unittest.TestCase):
    def test_target_bench(self):
        b = _arena()
        p = b.players[0]
        bench = _mon("BenchMon")
        p.bench = [bench]
        p.deck = [EnergyRef(EnergyDef("psychic")) for _ in range(3)]
        b._apply_attack_effects(p.active, ("attach:2:psychic:deck:target:bench",))
        self.assertEqual(bench.total_energy(), 2)
        self.assertEqual(p.active.total_energy(), 0, "不应附给攻击方")

    def test_target_any(self):
        b = _arena()
        p = b.players[0]
        bench = _mon("BenchMon")
        p.bench = [bench]
        p.deck = [EnergyRef(EnergyDef("grass")) for _ in range(3)]
        # 位置码 1 = 后备第一只
        b._apply_attack_effects(p.active, ("attach:1:grass:deck:target:any",),
                                action={"target": 1})
        self.assertEqual(bench.total_energy(), 1)
        self.assertEqual(p.active.total_energy(), 0)

    def test_no_target_is_no_effect(self):
        b = _arena()
        p = b.players[0]
        p.bench = []
        b._apply_attack_effects(p.active,
                                ("attach:1:grass:deck:target:bench",))
        self.assertEqual(p.active.total_energy(), 0,
                         "无后备时不附给攻击方（避免静默附错对象）")

    def test_target_bench_type(self):
        b = _arena()
        p = b.players[0]
        dark = _mon("DarkBench", ptype="darkness")
        grass = _mon("GrassBench", ptype="grass")
        p.bench = [grass, dark]
        tag = "attach_energy:1:darkness:deck:target:bench_type:darkness"
        self.assertIs(_attach_target_fp(tag, p, {}, p.active), dark)


class TestSpecialEnergyAllowed(unittest.TestCase):
    def test_anyenergy_attaches_special(self):
        b = _arena()
        p = b.players[0]
        special = EnergyRef(EnergyDef("water", name="Mist Energy",
                                      tag="senergy:mist"))
        p.deck = [special]
        bench = _mon("GrassBench", ptype="grass")
        p.bench = [bench]
        b._apply_attack_effects(
            p.active, ("attach:1:deck:target:bench_type:grass:anyenergy",))
        self.assertEqual(len(bench.senergy), 1, "「an Energy card」可取特殊能量")

    def test_basic_only_by_default(self):
        b = _arena()
        p = b.players[0]
        special = EnergyRef(EnergyDef("water", name="Mist Energy",
                                      tag="senergy:mist"))
        p.deck = [special]
        bench = _mon("GrassBench", ptype="grass")
        p.bench = [bench]
        b._apply_attack_effects(
            p.active, ("attach:1:deck:target:bench_type:grass",))
        self.assertEqual(len(bench.senergy), 0, "未写 an Energy 时只取基本能量")


class TestToxtricityAbility(unittest.TestCase):
    def _setup(self):
        b = _arena()
        p = b.players[0]
        p.active = _mon("Toxtricity", abilities=(_ab(
            "attach_energy:1:darkness:deck:target:bench_type:darkness:"
            "counters:20"),))
        dark = _mon("DarkBench", ptype="darkness", hp=100)
        p.bench = [dark]
        p.deck = [EnergyRef(EnergyDef("darkness")) for _ in range(3)]
        return b, p, dark

    def test_attach_then_counters(self):
        b, p, dark = self._setup()
        b.step({"verb": "ability", "pos": 0, "name": "A",
                "tag": "attach_energy:1:darkness:deck:target:bench_type:"
                       "darkness:counters:20"})
        self.assertEqual(dark.total_energy(), 1)
        self.assertEqual(dark.damage, 20, "附能成功后放 2 个指示物")

    def test_no_energy_no_counters(self):
        b, p, dark = self._setup()
        p.deck = [EnergyRef(EnergyDef("grass")) for _ in range(3)]
        # 无可附能量：合法动作表里不应列出该特性
        acts = [a for a in b.legal_actions() if a.get("verb") == "ability"]
        self.assertEqual(acts, [])
        self.assertEqual(dark.damage, 0)

    def test_not_legal_without_matching_bench(self):
        b, p, dark = self._setup()
        p.bench = [_mon("GrassBench", ptype="grass")]
        acts = [a for a in b.legal_actions() if a.get("verb") == "ability"]
        self.assertEqual(acts, [], "没有后备 {D} 时不列动作")


class TestEnergyToHand(unittest.TestCase):
    """第二十三批（2026-10-29）：附着能量收回手牌 ``bounce:energy:``

    卡面原型：「Put an Energy attached to this Pokémon into your hand.」
    （Chien-Pao / Dipplin / Landorus）·「Put 2 {R} Energy …」（Volcanion）·
    「… your opponent's Active Pokémon into their hand.」（Dustox，需 coin 嵌套，
    本批只建模非抛币形态）。
    """

    def test_self_returns_colorless_first(self):
        b = _arena()
        atk = b.players[0].active
        atk.add_energy("fire")
        atk.add_energy("colorless")
        b._apply_attack_effects(atk, ("bounce:energy:self:1",))
        self.assertEqual(atk.total_energy(), 1)
        self.assertEqual(len(b.players[0].hand), 1)
        self.assertEqual(b.players[0].hand[0].edef.type, "colorless",
                         "确定性默认：无色优先")

    def test_typed_two_fire(self):
        b = _arena()
        atk = b.players[0].active
        atk.add_energy("fire", 2)
        atk.add_energy("water")
        b._apply_attack_effects(atk, ("bounce:energy:self:2:type:fire",))
        self.assertEqual(atk.energy.get("fire", 0), 0)
        self.assertEqual(atk.energy.get("water", 0), 1, "不得拿别的颜色顶替")
        self.assertEqual([c.edef.type for c in b.players[0].hand],
                         ["fire", "fire"])

    def test_opp_active_goes_to_their_hand(self):
        b = _arena()
        opp = b.players[1].active
        opp.add_energy("water")
        b._apply_attack_effects(b.players[0].active,
                                ("bounce:energy:opp_active:1",))
        self.assertEqual(opp.total_energy(), 0)
        self.assertEqual(len(b.players[1].hand), 1)
        self.assertEqual(b.players[0].hand, [])

    def test_special_energy_card_returned(self):
        b = _arena()
        atk = b.players[0].active
        ref = EnergyRef(EnergyDef("grass", name="Spiky Energy",
                                  tag="senergy:spiky"))
        atk.senergy.append(ref)
        b._apply_attack_effects(atk, ("bounce:energy:self:1",))
        self.assertEqual(atk.senergy, [])
        self.assertEqual(len(b.players[0].hand), 1, "特殊能量按整张卡回手")

    def test_no_energy_is_noop(self):
        b = _arena()
        b._apply_attack_effects(b.players[0].active,
                                ("bounce:energy:self:1",))
        self.assertEqual(b.players[0].hand, [])


if __name__ == "__main__":
    unittest.main()
