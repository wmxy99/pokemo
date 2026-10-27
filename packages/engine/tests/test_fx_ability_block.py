# -*- coding: utf-8 -*-
"""fx 族：特性 / 场地「no Abilities」静态封锁 —— 引擎侧单测 + 合法动作表集成

覆盖三条规则的正例 / 反例，以及「认不出必须返回 False」的纪律
（错误方向会让该放的特性被禁，比不实现更糟）。
"""
from __future__ import annotations

import random
import unittest

from engine.battle import Battle
from engine.cards import AbilityDef, FieldPokemon, PokemonDef
from engine.state import GameConfig, PlayerState
from engine.families import fx_ability_block as F


def _battle():
    cfg = GameConfig()
    b = object.__new__(Battle)
    b.cfg = cfg
    b.seed = 1
    b.rng = random.Random(1)
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
    return b


def _mon(name, ptype="colorless", stage=0, ability=None):
    abs_ = (ability,) if ability else ()
    return FieldPokemon(PokemonDef(name, hp=100, ptype=ptype, stage=stage,
                                   abilities=abs_))


class TestAbilityBlockEngine(unittest.TestCase):
    # ---- 规则 1：Watchtower 场地 —— 无色宝可梦无特性 ----
    def test_watchtower_blocks_colorless(self):
        b = _battle()
        b.stadium = {"name": "Team Rocket's Watchtower",
                     "tag": "stadium:ability_block:colorless", "owner": 0}
        ab = AbilityDef("Recon", tag="recon:2")
        self.assertTrue(F.ability_blocked(b, _mon("Celebi", ptype="colorless"), ab))

    def test_watchtower_ignores_noncolorless(self):
        b = _battle()
        b.stadium = {"name": "Team Rocket's Watchtower",
                     "tag": "stadium:ability_block:colorless", "owner": 0}
        ab = AbilityDef("Recon", tag="recon:2")
        self.assertFalse(F.ability_blocked(b, _mon("Charizard", ptype="fire"), ab))

    def test_watchtower_ignores_without_stadium(self):
        b = _battle()
        ab = AbilityDef("Recon", tag="recon:2")
        self.assertFalse(F.ability_blocked(b, _mon("Celebi", ptype="colorless"), ab))

    # ---- 规则 2：Gastrodon 在任意后备 —— 双方后备 Stage 2 无特性 ----
    def test_gastrodon_bench_stage2_blocked(self):
        b = _battle()
        b.players[0].bench = [_mon("Gastrodon", stage=1)]
        fp = _mon("Metagross", stage=2)
        b.players[1].bench = [fp]
        self.assertTrue(F.ability_blocked(b, fp, AbilityDef("Recon", tag="recon:2")))

    def test_gastrodon_bench_stage1_not_blocked(self):
        b = _battle()
        b.players[0].bench = [_mon("Gastrodon", stage=1)]
        fp = _mon("Pikachu", stage=0)
        b.players[1].bench = [fp]
        self.assertFalse(F.ability_blocked(b, fp, AbilityDef("Recon", tag="recon:2")))

    def test_gastrodon_absent_not_blocked(self):
        b = _battle()
        fp = _mon("Metagross", stage=2)
        b.players[1].bench = [fp]
        self.assertFalse(F.ability_blocked(b, fp, AbilityDef("Recon", tag="recon:2")))

    # ---- 规则 3：Flutter Mane 在任意战斗位 —— 对手战斗位无特性（except Midnight）----
    def test_flutter_opp_active_blocked(self):
        b = _battle()
        b.players[0].active = _mon("Flutter Mane", ptype="psychic")
        fp = _mon("Mewtwo", ptype="psychic")
        b.players[1].active = fp
        self.assertTrue(F.ability_blocked(b, fp, AbilityDef("SomeAbility", tag="recon:2")))

    def test_flutter_except_midnight_fluttering(self):
        b = _battle()
        b.players[0].active = _mon("Flutter Mane", ptype="psychic")
        fp = _mon("Flutter Mane", ptype="psychic")
        b.players[1].active = fp
        self.assertFalse(
            F.ability_blocked(b, fp, AbilityDef("Midnight Fluttering", tag="recon:2")))

    def test_flutter_absent_not_blocked(self):
        b = _battle()
        fp = _mon("Mewtwo", ptype="psychic")
        b.players[1].active = fp
        self.assertFalse(F.ability_blocked(b, fp, AbilityDef("SomeAbility", tag="recon:2")))

    # ---- 集成：被封锁的特性不进合法动作表 ----
    def test_legal_actions_excludes_blocked(self):
        b = _battle()
        b.stadium = {"name": "Team Rocket's Watchtower",
                     "tag": "stadium:ability_block:colorless", "owner": 0}
        ab = AbilityDef("Recon", tag="recon:2", location="field")
        b.players[0].active = _mon("Celebi", ptype="colorless", ability=ab)
        b.players[1].active = _mon("Opp", ptype="fire")
        acts = b.legal_actions()
        ability_acts = [a for a in acts if a.get("verb") == "ability"]
        self.assertFalse(any(a.get("name") == "Recon" for a in ability_acts),
                         "被 Watchtower 封锁的特性不应出现在合法动作表")


if __name__ == "__main__":
    unittest.main()
