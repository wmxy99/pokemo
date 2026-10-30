# -*- coding: utf-8 -*-
"""第十二批（2026-10-28）· 引擎用例：Mega 回复 + 能量回手 / 逐后备进化 / 条件击倒 / Mega 条件加伤

覆盖：
  · ``heal:all:mega:energy``    Wally's Compassion（回复 1 只 Mega → 其能量回手）
  · ``evolve_each_bench``        Reuniclus「Cellular Ascension」
  · ``knockout:opp_active:basic`` Haxorus「Axe Blast」
  · ``fx:static_dmg:add:120:self_if_mega_type:darkness`` Seviper「Excited Power」
"""
from __future__ import annotations
import random
import unittest

from engine import families
from engine.battle import Battle, _apply_heal
from engine.cards import (AbilityDef, AttackDef, EnergyDef, EnergyRef, PokeRef,
                          PokemonDef, TrainerDef, TrainerRef, make_pokemon)
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


def _mon(name, hp=300, ptype="colorless", stage=0, is_mega=False,
         abilities=(), attacks=()):
    return make_pokemon(PokemonDef(
        name, hp=hp, ptype=ptype, stage=stage, is_mega=is_mega,
        abilities=tuple(abilities), attacks=tuple(attacks)))


def _ab(tag: str) -> AbilityDef:
    return AbilityDef("A", tag=tag, location="field")


class TestWallyMegaHeal(unittest.TestCase):
    def test_heal_only_mega_and_move_energy(self):
        b = _arena()
        mega = _mon("MegaMon", hp=200, is_mega=True)
        mega.damage = 90
        mega.add_energy("fire", 2)
        basic = _mon("PlainMon", hp=200)
        basic.damage = 90
        basic.add_energy("water", 1)
        b.players[0].active = basic
        b.players[0].bench = [mega]
        p = b.players[0]
        before_hand = len(p.hand)
        healed, n = _apply_heal(b, p, "heal:all:mega:energy", action={})
        self.assertEqual(n, 1)
        self.assertEqual(healed, 90)
        self.assertEqual(mega.damage, 0, "Mega 应回满")
        self.assertEqual(basic.damage, 90, "非 Mega 不在范围内")
        self.assertEqual(mega.total_energy(), 0, "能量应回手（不是留在场上）")
        self.assertEqual(len(p.hand), before_hand + 2, "2 张能量回手")

    def test_no_mega_does_nothing(self):
        b = _arena()
        plain = _mon("PlainMon", hp=200)
        plain.damage = 90
        plain.add_energy("fire", 2)
        b.players[0].active = plain
        p = b.players[0]
        before_hand = len(p.hand)
        healed, n = _apply_heal(b, p, "heal:all:mega:energy", action={})
        self.assertEqual((healed, n), (0, 0))
        self.assertEqual(plain.damage, 90, "无 Mega → 不回复")
        self.assertEqual(len(p.hand), before_hand, "无回复 → 不搬能量")

    def test_full_hp_mega_keeps_energy(self):
        """满 HP 的 Mega 没回任何伤害 → 「in this way」不成立，能量不动"""
        b = _arena()
        mega = _mon("MegaMon", hp=200, is_mega=True)
        mega.add_energy("fire", 2)
        b.players[0].active = mega
        p = b.players[0]
        healed, _ = _apply_heal(b, p, "heal:all:mega:energy", action={})
        self.assertEqual(healed, 0)
        self.assertEqual(mega.total_energy(), 2, "没回复过 → 不得把能量回手")


class TestEvolveEachBench(unittest.TestCase):
    def test_evolves_each_bench(self):
        b = _arena()
        p = b.players[0]
        p.bench = [_mon("Wurmple", hp=60), _mon("Ralts", hp=60)]
        p.deck = [
            PokeRef(PokemonDef("Silcoon", hp=80, ptype="grass", stage=1,
                               evolves_from="Wurmple")),
            PokeRef(PokemonDef("Kirlia", hp=80, ptype="psychic", stage=1,
                               evolves_from="Ralts")),
        ]
        n = b._evolve_each_bench(p)
        self.assertEqual(n, 2)
        self.assertEqual(sorted(x.name for x in p.bench), ["Kirlia", "Silcoon"])

    def test_missing_evolution_is_not_an_error(self):
        b = _arena()
        p = b.players[0]
        p.bench = [_mon("Wurmple", hp=60)]
        p.deck = [PokeRef(PokemonDef("Kirlia", hp=80, ptype="psychic", stage=1,
                                     evolves_from="Ralts"))]
        n = b._evolve_each_bench(p)
        self.assertEqual(n, 0)
        self.assertEqual(p.bench[0].name, "Wurmple")


class TestHaxorusConditionalKO(unittest.TestCase):
    def test_ko_only_basic(self):
        b = _arena()
        b.players[1].active = _mon("BasicVictim", hp=200, stage=0)
        b.players[1].bench = [_mon("Reserve", hp=100)]
        b.players[0].active = _mon("Haxorus", attacks=(AttackDef(
            "Axe Blast", cost=0, damage=0,
            effects=("knockout:opp_active:basic",)),))
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.name, "Reserve",
                         "基础战斗位应被击倒并由后备顶上")

    def test_evolution_survives(self):
        b = _arena()
        b.players[1].active = _mon("EvoVictim", hp=200, stage=1)
        b.players[1].bench = [_mon("Reserve", hp=100)]
        b.players[0].active = _mon("Haxorus", attacks=(AttackDef(
            "Axe Blast", cost=0, damage=0,
            effects=("knockout:opp_active:basic",)),))
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.name, "EvoVictim",
                         "进化宝可梦不应被击倒")


class TestSeviperStaticBonus(unittest.TestCase):
    def test_bonus_with_dark_mega_in_play(self):
        b = _arena()
        atk = _mon("Seviper", hp=120, abilities=(
            _ab("fx:static_dmg:add:120:self_if_mega_type:darkness"),))
        b.players[0].active = atk
        b.players[0].bench = [_mon("DarkMega", hp=200, ptype="darkness",
                                   is_mega=True)]
        self.assertEqual(
            families.static_dmg_plus(b, atk, b.players[1].active), 120)

    def test_no_bonus_without_mega(self):
        b = _arena()
        atk = _mon("Seviper", hp=120, abilities=(
            _ab("fx:static_dmg:add:120:self_if_mega_type:darkness"),))
        b.players[0].active = atk
        b.players[0].bench = [_mon("DarkMon", hp=200, ptype="darkness")]
        self.assertEqual(
            families.static_dmg_plus(b, atk, b.players[1].active), 0)

    def test_no_bonus_with_wrong_type(self):
        b = _arena()
        atk = _mon("Seviper", hp=120, abilities=(
            _ab("fx:static_dmg:add:120:self_if_mega_type:darkness"),))
        b.players[0].active = atk
        b.players[0].bench = [_mon("FireMega", hp=200, ptype="fire",
                                   is_mega=True)]
        self.assertEqual(
            families.static_dmg_plus(b, atk, b.players[1].active), 0)


class TestSuperPotionDiscard(unittest.TestCase):
    def test_heal_then_discard_energy(self):
        b = _arena()
        p = b.players[0]
        mon = _mon("Hurt", hp=200)
        mon.damage = 100
        mon.add_energy("fire", 2)
        p.active = mon
        healed, _ = _apply_heal(b, p, "heal:60:choose:discard", action={})
        self.assertEqual(healed, 60)
        self.assertEqual(mon.damage, 40)
        self.assertEqual(mon.total_energy(), 1, "回复后应弃 1 个能量")

    def test_full_hp_keeps_energy(self):
        b = _arena()
        p = b.players[0]
        mon = _mon("Fine", hp=200)
        mon.add_energy("fire", 2)
        p.active = mon
        healed, _ = _apply_heal(b, p, "heal:60:choose:discard", action={})
        self.assertEqual(healed, 0)
        self.assertEqual(mon.total_energy(), 2, "没回复过 → 不得弃能")


class TestWallyLegality(unittest.TestCase):
    """Wally 的合法动作必须只在**有 Mega** 时列出（否则会在结算时抛错/空转）"""

    def _battle_with(self, tag, mega):
        b = _arena()
        p = b.players[0]
        p.active = _mon("MegaMon" if mega else "PlainMon", hp=200,
                        is_mega=mega)
        p.hand = [TrainerRef(TrainerDef(tag, kind="supporter",
                                        name="Wally's Compassion"))]
        return b

    def test_legal_with_mega(self):
        b = self._battle_with("heal:all:mega:energy", mega=True)
        acts = [a for a in b._trainer_legals(b.players[0], b.players[1])
                if a.get("verb") == "trainer"]
        self.assertEqual(len(acts), 1)
        self.assertEqual(acts[0]["target"], 0)

    def test_not_legal_without_mega(self):
        b = self._battle_with("heal:all:mega:energy", mega=False)
        acts = [a for a in b._trainer_legals(b.players[0], b.players[1])
                if a.get("verb") == "trainer"]
        self.assertEqual(acts, [])

    def test_super_potion_legal(self):
        b = _arena()
        p = b.players[0]
        p.active = _mon("Hurt", hp=200)
        p.hand = [TrainerRef(TrainerDef("heal:60:choose:discard",
                                        kind="item", name="Super Potion"))]
        acts = [a for a in b._trainer_legals(p, b.players[1])
                if a.get("verb") == "trainer"]
        self.assertTrue(acts, "Super Potion 必须可打出")


if __name__ == "__main__":
    unittest.main()
