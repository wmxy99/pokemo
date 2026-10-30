# -*- coding: utf-8 -*-
"""第十三批（2026-10-28）· 引擎用例：Pokémon Checkup 改写 / 道具静态加伤 / 具名后备条件

覆盖：
  · ``passive:checkup_plus:<status>:<n>[:side:opp][:active_only]``  Pecharunt / Magmortar
  · ``checkup_set:<status>:<n>``                                    Nidoking ex
  · ``tool:dmg_plus:N[:target:ex][:holder:…]``                      4 张道具
  · ``dmg_plus:150:if:own_bench_named_all:beldum+metang``           Metagross
"""
from __future__ import annotations
import random
import unittest

from engine.battle import Battle, _eval_condition, _tool_dmg_plus
from engine.cards import (AbilityDef, AttackDef, EnergyDef, EnergyRef, PokeRef,
                          PokemonDef, make_pokemon)
from engine.rules import status_checkup
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


def _mon(name, hp=300, ptype="colorless", stage=0, is_ex=False,
         abilities=(), attacks=()):
    return make_pokemon(PokemonDef(
        name, hp=hp, ptype=ptype, stage=stage, is_ex=is_ex,
        abilities=tuple(abilities), attacks=tuple(attacks)))


def _ab(tag: str) -> AbilityDef:
    return AbilityDef("A", tag=tag, location="field")


class TestCheckupPlus(unittest.TestCase):
    def test_pecharunt_bonus_on_opponent_active(self):
        b = _arena()
        b.players[0].active = _mon("Pecharunt", abilities=(
            _ab("passive:checkup_plus:poison:50:side:opp:active_only"),))
        b.players[1].active = _mon("Victim", hp=200)
        b.players[1].active.status.add("poison")
        bonus, override = b._checkup_mods(1)
        self.assertEqual(bonus, {"poison": 50, "burn": 0})
        self.assertEqual(override, {})
        status_checkup(b.players[1].active, b.rng, bonus=bonus,
                       override=override)
        self.assertEqual(b.players[1].active.damage, 60, "10 + 50 追加")

    def test_active_only(self):
        """Pecharunt 必须在战斗位（后备不生效）"""
        b = _arena()
        b.players[0].bench = [_mon("Pecharunt", abilities=(
            _ab("passive:checkup_plus:poison:50:side:opp:active_only"),))]
        self.assertEqual(b._checkup_mods(1)[0]["poison"], 0)

    def test_magmortar_burn_bonus(self):
        b = _arena()
        b.players[0].bench = [_mon("Magmortar", abilities=(
            _ab("passive:checkup_plus:burn:30:side:opp"),))]
        b.players[1].active.status.add("burn")
        bonus, _ = b._checkup_mods(1)
        self.assertEqual(bonus["burn"], 30)
        status_checkup(b.players[1].active, b.rng, bonus=bonus)
        self.assertEqual(b.players[1].active.damage, 50, "20 + 30 追加")

    def test_no_bonus_for_own_side(self):
        """卡面写的是「你的对手的」，不能加到持有者自己身上"""
        b = _arena()
        b.players[0].active = _mon("Pecharunt", abilities=(
            _ab("passive:checkup_plus:poison:50:side:opp:active_only"),))
        self.assertEqual(b._checkup_mods(0)[0]["poison"], 0)


class TestCheckupSet(unittest.TestCase):
    def test_override_poison(self):
        b = _arena()
        b.players[1].active.checkup_set["poison"] = 80
        b.players[1].active.status.add("poison")
        _, override = b._checkup_mods(1)
        self.assertEqual(override, {"poison": 80})
        status_checkup(b.players[1].active, b.rng, override=override)
        self.assertEqual(b.players[1].active.damage, 80, "8 个指示物取代 1 个")

    def test_evolve_clears_override(self):
        mon = _mon("Victim", hp=200)
        mon.checkup_set["poison"] = 80
        mon.evolve_to(PokemonDef("Evolved", hp=200, ptype="colorless",
                                 stage=1))
        self.assertEqual(mon.checkup_set, {})


class TestToolDamagePlus(unittest.TestCase):
    def test_target_ex(self):
        atk = _mon("Attacker")
        ex = _mon("TargetEx", is_ex=True)
        plain = _mon("TargetPlain")
        atk.tool = "tool:dmg_plus:50:target:ex"
        self.assertEqual(_tool_dmg_plus(atk, ex), 50)
        self.assertEqual(_tool_dmg_plus(atk, plain), 0, "非 ex 不生效")

    def test_holder_name(self):
        atk = _mon("Pikachu ex", is_ex=True)
        atk.tool = "tool:dmg_plus:50:holder:name:pikachu:target:ex"
        self.assertEqual(_tool_dmg_plus(atk, _mon("E", is_ex=True)), 50)
        other = _mon("Raichu ex", is_ex=True)
        other.tool = "tool:dmg_plus:50:holder:name:pikachu:target:ex"
        self.assertEqual(_tool_dmg_plus(other, _mon("E", is_ex=True)), 0)

    def test_holder_poisoned(self):
        atk = _mon("Attacker")
        atk.tool = "tool:dmg_plus:40:holder:status:poison"
        self.assertEqual(_tool_dmg_plus(atk, _mon("D")), 0)
        atk.status.add("poison")
        self.assertEqual(_tool_dmg_plus(atk, _mon("D")), 40)

    def test_holder_no_rule_box(self):
        plain = _mon("Plain")
        plain.tool = "tool:dmg_plus:30:holder:no_rule_box:target:ex"
        ex = _mon("ExMon", is_ex=True)
        ex.tool = "tool:dmg_plus:30:holder:no_rule_box:target:ex"
        target = _mon("E", is_ex=True)
        self.assertEqual(_tool_dmg_plus(plain, target), 30)
        self.assertEqual(_tool_dmg_plus(ex, target), 0, "有规则框的持有者不生效")

    def test_tool_disabled_by_stadium(self):
        atk = _mon("Attacker")
        atk.tool = "tool:dmg_plus:50:target:ex"
        atk.tool_disabled = True
        self.assertEqual(_tool_dmg_plus(atk, _mon("E", is_ex=True)), 0,
                         "Jamming Tower 下道具失效")


class TestMetagrossCondition(unittest.TestCase):
    def test_named_bench_pair(self):
        b = _arena()
        b.players[0].bench = [_mon("Beldum"), _mon("Metang")]
        self.assertTrue(_eval_condition(
            b, "own_bench_named_all:beldum+metang"))

    def test_missing_one(self):
        b = _arena()
        b.players[0].bench = [_mon("Beldum"), _mon("Other")]
        self.assertFalse(_eval_condition(
            b, "own_bench_named_all:beldum+metang"))


if __name__ == "__main__":
    unittest.main()
