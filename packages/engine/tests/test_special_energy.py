# -*- coding: utf-8 -*-
"""特殊能量（Special Energy）—— 引擎侧单测

覆盖子系统边界：
  · 提供量按**持有者**实时计算（Neo Upper / Prism / Ignition 在进化前后不同）；
  · 「任意属性 / 双色任选」能参与彩色费用支付；
  · 静态效果：+HP（Grow Grass）、招式效果免疫（Mist / Rock Fighting）；
  · 触发：附着抽牌（Enriching）、受击反制（Spiky）、检索铺场（Telepath）；
  · 奖赏卡修正（Legacy，每局一次）、回合结束弃置（Ignition）；
  · 附着限制（Team Rocket's Energy）与合法动作表；
  · 未知 kind 必须抛错（不得当成无色能量静默处理）。
"""
from __future__ import annotations

import random
import unittest

from engine import special_energy as SE
from engine.battle import Battle
from engine.cards import (AttackDef, EnergyDef, EnergyRef, PokemonDef,
                          PokeRef, make_pokemon)
from engine.state import GameConfig, PlayerState


def _arena(seed: int = 3):
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
    b.players[0].active = make_pokemon(PokemonDef(
        "Holder", hp=200, ptype="colorless",
        attacks=(AttackDef("Hit", cost=0, damage=10),)))
    b.players[1].active = make_pokemon(PokemonDef(
        "Defender", hp=300, ptype="colorless",
        attacks=(AttackDef("Tox", cost=0, damage=0, effects=("poison",)),)))
    return b


def _energy(kind: str, name: str = "", ptype: str = "colorless"):
    return EnergyRef(EnergyDef(ptype, name=name or f"{kind} Energy",
                               tag=f"senergy:{kind}"))


def _attach(b, ref, target: int = 0):
    p = b.players[b.current]
    p.hand.append(ref)
    return b.step({"verb": "attach", "target": target,
                   "energy": len(p.hand) - 1})


class TestProvides(unittest.TestCase):
    def test_basic_energy_unchanged(self):
        fp = make_pokemon(PokemonDef("X", hp=10, ptype="colorless"))
        fp.add_energy("fire")
        self.assertEqual(fp.total_energy(), 1)
        self.assertTrue(fp.can_pay((("fire", 1),)))

    def test_colorless_special(self):
        b = _arena()
        self.assertTrue(_attach(b, _energy("mist", "Mist Energy"))["ok"])
        fp = b.players[0].active
        self.assertEqual(fp.total_energy(), 1)
        self.assertEqual(fp.energy_of("colorless"), 1)
        self.assertTrue(fp.can_pay((("colorless", 1),)))

    def test_any_type_pays_colored(self):
        b = _arena()
        self.assertTrue(_attach(b, _energy("legacy", "Legacy Energy"))["ok"])
        fp = b.players[0].active
        # 「provides every type of Energy but only 1 at a time」
        self.assertTrue(fp.can_pay((("water", 1),)))
        self.assertFalse(fp.can_pay((("water", 1), ("colorless", 1))))

    def test_prism_only_basic(self):
        b = _arena()
        fp = b.players[0].active
        fp.pdef = PokemonDef("Basic", hp=50, ptype="colorless", stage=0)
        _attach(b, _energy("prism", "Prism Energy"))
        self.assertTrue(fp.can_pay((("fire", 1),)))
        fp.pdef = PokemonDef("Stage1", hp=80, ptype="colorless", stage=1,
                             evolves_from="Basic")
        # 进化后只提供 {C}
        self.assertEqual(fp.energy_of("colorless"), 1)
        self.assertFalse(fp.can_pay((("fire", 1),)))

    def test_neo_upper_stage2(self):
        b = _arena()
        fp = b.players[0].active
        fp.pdef = PokemonDef("S1", hp=80, ptype="colorless", stage=1)
        _attach(b, _energy("neo_upper", "Neo Upper Energy"))
        self.assertEqual(fp.total_energy(), 1)
        fp.pdef = PokemonDef("S2", hp=140, ptype="colorless", stage=2,
                             evolves_from="S1")
        self.assertEqual(fp.total_energy(), 2)
        self.assertTrue(fp.can_pay((("water", 1), ("psychic", 1))))

    def test_ignition_evolution_three(self):
        b = _arena()
        fp = b.players[0].active
        fp.pdef = PokemonDef("Evo", hp=120, ptype="colorless", stage=2)
        _attach(b, _energy("ignition", "Ignition Energy"))
        self.assertEqual(fp.total_energy(), 3)

    def test_team_rocket_flex(self):
        b = _arena()
        fp = b.players[0].active
        fp.pdef = PokemonDef("TR", hp=90, ptype="darkness", stage=0,
                             category="Trainer's Pokémon(Team Rocket)")
        _attach(b, _energy("team_rocket", "Team Rocket's Energy"))
        self.assertEqual(fp.total_energy(), 2)
        self.assertTrue(fp.can_pay((("psychic", 1), ("darkness", 1))))
        self.assertFalse(fp.can_pay((("darkness", 3),)))

    def test_unknown_kind_raises(self):
        b = _arena()
        ref = EnergyRef(EnergyDef("colorless", name="X", tag="senergy:hack"))
        with self.assertRaises(ValueError):
            b.players[0].active.senergy.append(ref)
            b.players[0].active.total_energy()


class TestAttachRestriction(unittest.TestCase):
    def test_team_rocket_rejected_on_non_tr(self):
        b = _arena()
        r = _attach(b, _energy("team_rocket", "TR Energy"))
        self.assertFalse(r["ok"])
        p = b.players[0]
        self.assertEqual(len(p.hand), 1, "非法附着不得丢弃手牌")
        self.assertTrue(p.active.senergy == [] or
                        all(not r.edef.is_special for r in p.active.senergy))

    def test_team_rocket_allowed_on_tr(self):
        b = _arena()
        b.players[0].active.pdef = PokemonDef(
            "TR mon", hp=100, ptype="darkness", stage=1,
            category="Trainer's Pokémon(Team Rocket)")
        self.assertTrue(_attach(b, _energy("team_rocket"))["ok"])

    def test_legals_hide_illegal_target(self):
        b = _arena()
        p = b.players[0]
        p.bench.append(make_pokemon(PokemonDef(
            "TR mon", hp=100, ptype="darkness", stage=0,
            category="Trainer's Pokémon(Team Rocket)")))
        p.hand.append(_energy("team_rocket", "Team Rocket's Energy"))
        acts = [a for a in b.legal_actions() if a["verb"] == "attach"]
        self.assertEqual({a["target"] for a in acts}, {1})

    def test_choice_when_multiple_energy_kinds(self):
        b = _arena()
        p = b.players[0]
        p.hand.append(EnergyRef(EnergyDef("fire", name="Basic Fire Energy")))
        p.hand.append(_energy("mist", "Mist Energy"))
        labels = {(a.get("energy_name") or "") for a in b.legal_actions()
                  if a["verb"] == "attach"}
        self.assertIn("Mist Energy", labels)
        self.assertIn("Basic Fire Energy", labels)


class TestStaticEffects(unittest.TestCase):
    def test_grow_grass_hp_only_on_grass(self):
        b = _arena()
        fp = b.players[0].active
        fp.pdef = PokemonDef("G", hp=100, ptype="grass", stage=0)
        _attach(b, _energy("grow_grass", "Grow Grass Energy", "grass"))
        self.assertEqual(fp.max_hp, 120)
        self.assertEqual(fp.energy_of("grass"), 1)

    def test_grow_grass_no_bonus_off_type(self):
        b = _arena()
        fp = b.players[0].active
        _attach(b, _energy("grow_grass", "Grow Grass Energy", "grass"))
        self.assertEqual(fp.max_hp, 200)

    def test_mist_blocks_effects_not_damage(self):
        b = _arena()
        # 化石/效果免疫：对手（玩家 1）攻击我方带 Mist 的战斗位
        _attach(b, _energy("mist", "Mist Energy"))
        b.current = 1
        p1 = b.players[1]
        p1.active = make_pokemon(PokemonDef(
            "Attacker", hp=200, ptype="colorless",
            attacks=(AttackDef("Hit", cost=0, damage=40,
                               effects=("poison",)),)))
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        victim = b.players[0].active
        self.assertEqual(victim.damage, 40)
        self.assertEqual(sorted(victim.status), [])

    def test_rock_fighting_only_fighting_holder(self):
        b = _arena()
        fp = b.players[0].active
        _attach(b, _energy("rock_fighting", "Rock Fighting Energy", "fighting"))
        self.assertFalse(SE.effect_immune(fp, "attack"))
        fp.pdef = PokemonDef("F", hp=100, ptype="fighting", stage=0)
        self.assertTrue(SE.effect_immune(fp, "attack"))


class TestTriggers(unittest.TestCase):
    def test_enriching_draw_on_attach(self):
        b = _arena()
        p = b.players[0]
        for i in range(5):
            p.deck.append(PokeRef(PokemonDef(f"D{i}", hp=10, ptype="colorless")))
        before = len(p.hand)
        r = _attach(b, _energy("enriching", "Enriching Energy"))
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(p.hand), before + 4)

    def test_spiky_counters_attacker(self):
        b = _arena()
        _attach(b, _energy("spiky", "Spiky Energy"))
        b.current = 1
        b.players[1].active = make_pokemon(PokemonDef(
            "Attacker", hp=200, ptype="colorless",
            attacks=(AttackDef("Hit", cost=0, damage=30),)))
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.damage, 20)

    def test_telepath_search_bench(self):
        b = _arena()
        p = b.players[0]
        p.active.pdef = PokemonDef("P", hp=100, ptype="psychic", stage=0)
        p.deck.append(PokeRef(PokemonDef("PBasic", hp=60, ptype="psychic",
                                        stage=0)))
        p.deck.append(PokeRef(PokemonDef("PBasic2", hp=60, ptype="psychic",
                                         stage=0)))
        p.deck.append(PokeRef(PokemonDef("Fire", hp=60, ptype="fire", stage=0)))
        r = _attach(b, _energy("telepath", "Telepath Psychic Energy", "psychic"))
        self.assertTrue(r["ok"], r.get("error"))
        # 检索从牌库**尾部**（顶部）开始扫，顺序不作要求；{F} 的那张不得入选
        self.assertEqual(sorted(fp.name for fp in p.bench),
                         ["PBasic", "PBasic2"])

    def test_ignition_discarded_at_end_of_turn(self):
        b = _arena()
        p = b.players[0]
        p.active.pdef = PokemonDef("Evo", hp=120, ptype="colorless", stage=2)
        _attach(b, _energy("ignition", "Ignition Energy"))
        self.assertEqual(p.active.senergy[0].edef.name, "Ignition Energy")
        b.step({"verb": "pass"})
        self.assertEqual(p.active.senergy, [])
        self.assertIn("Ignition Energy", p.discard)

    def test_legacy_prize_once_per_game(self):
        b = _arena()
        _attach(b, _energy("legacy", "Legacy Energy"))
        victim = b.players[0].active
        victim.damage = victim.max_hp
        b._ko_from_attack = True
        b._resolve_ko(0, victim)
        # 1 张奖赏卡 - 1（Legacy）= 0 张：对手的剩余奖赏数不变
        self.assertEqual(b.players[1].prizes_left, b.cfg.prizes)
        self.assertTrue(b._legacy_used)

    def test_legacy_second_ko_takes_normal(self):
        b = _arena()
        _attach(b, _energy("legacy", "Legacy Energy"))
        p0 = b.players[0]
        v1 = p0.active
        v1.damage = v1.max_hp
        b._ko_from_attack = True
        b._resolve_ko(0, v1)
        # 第二只（后备，无 Legacy）正常取 1
        p0.bench.append(make_pokemon(PokemonDef("B", hp=50, ptype="colorless")))
        v2 = p0.bench[0]
        v2.damage = v2.max_hp
        remaining = b.players[1].prizes_left
        b._resolve_ko(0, v2)
        self.assertEqual(b.players[1].prizes_left, remaining - 1)


class TestDiscardSpecialEnergy(unittest.TestCase):
    """第二十四批（2026-10-29）：``discard_energy:<n|all>:opp:special``

    卡面原型（Cobalion）：「Discard a Special Energy from your opponent's Active
    Pokémon.」——必须按 **senergy 里的卡** 弃置；走普通 `remove_any` 会先弃
    基本能量（对手只贴基本能量时本不该生效却生效 = 静默错算）。
    """

    def _prep(self):
        b = _arena()
        return b, b.players[1].active

    def test_only_special_is_discarded(self):
        b, opp = self._prep()
        opp.add_energy("water")
        # ⚠ 不能用 Mist / Rock Fighting：它们本身就让持有者免疫招式**效果**
        # （包括这次弃能），测试会被 `effect_immune` 正确拦截而不是测到弃置。
        opp.senergy.append(_energy("grow_grass", "Grow Grass Energy", "grass"))
        b._apply_attack_effects(b.players[0].active,
                                ("discard_energy:1:opp:special",))
        self.assertEqual(opp.senergy, [], "特殊能量卡必须被弃置")
        self.assertEqual(opp.energy.get("water", 0), 1, "基本能量不得被弃")

    def test_effect_immune_special_energy_blocks_discard(self):
        """Mist Energy：招式效果不作用于持有者 → 特殊能量弃置被拦"""
        b, opp = self._prep()
        opp.senergy.append(_energy("mist", "Mist Energy", "water"))
        b._apply_attack_effects(b.players[0].active,
                                ("discard_energy:1:opp:special",))
        self.assertEqual(len(opp.senergy), 1, "免疫效果 → 不得弃置")

    def test_basic_energy_untouched_without_special(self):
        b, opp = self._prep()
        opp.add_energy("water", 2)
        b._apply_attack_effects(b.players[0].active,
                                ("discard_energy:1:opp:special",))
        self.assertEqual(opp.energy.get("water", 0), 2, "无特殊能量 → 不生效")

    def test_all_specials(self):
        b, opp = self._prep()
        opp.senergy.append(_energy("grow_grass", "Grow Grass Energy", "grass"))
        opp.senergy.append(_energy("spiky", "Spiky Energy", "lightning"))
        b._apply_attack_effects(b.players[0].active,
                                ("discard_energy:all:opp:special",))
        self.assertEqual(opp.senergy, [])


if __name__ == "__main__":
    unittest.main()
