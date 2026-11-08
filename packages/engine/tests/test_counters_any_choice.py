# -*- coding: utf-8 -*-
"""S1c · 「任意分配指示物（含战斗位）」用决策分解表达（Sinistcha「Cursed Drop」）

性质：
  1. 挂起时**什么都不放**（引擎不自选分配）；
  2. 必须放**恰好 N 枚**（min=max=N），放到哪只由牌手逐枚决定，可重复同一只；
  3. 放完后总枚数正确，且不吃弱点/抗性（指示物直放）。
"""
from __future__ import annotations
import random
import unittest

from engine.battle import Battle
from engine.cards import AttackDef, EnergyDef, EnergyRef, PokemonDef, make_pokemon
from engine.state import GameConfig, PlayerState


def _b(bench=2):
    cfg = GameConfig()
    b = object.__new__(Battle)
    b.cfg = cfg; b.seed = 7; b.rng = random.Random(7)
    b.players = [PlayerState(name="A", prizes_left=6), PlayerState(name="B", prizes_left=6)]
    b.current = 0; b.turn_number = 1; b.turn_counts = [1, 1]
    b.phase = "main"; b._pending = None; b._defer_end_turn = False
    b.finished = False; b.winner = None; b.win_reason = None
    b.events = []; b.stadium = None
    b._ko_by_attack = [0, 0]; b._ko_tr_by_attack = [0, 0]
    b.players[0].active = make_pokemon(PokemonDef(
        "Sinistcha", hp=90, ptype="grass",
        attacks=(AttackDef("Cursed Drop", cost=(), damage=0),)))
    b.players[1].active = make_pokemon(PokemonDef("Active", hp=300, ptype="colorless"))
    for i in range(bench):
        b.players[1].bench.append(make_pokemon(PokemonDef(
            f"Bench{i}", hp=300, ptype="colorless")))
    for p in b.players:
        p.deck = [EnergyRef(EnergyDef("colorless")) for _ in range(10)]
    return b


class TestCountersAny(unittest.TestCase):
    def test_opens_choice_without_placing(self):
        b = _b()
        b._apply_attack_effects(b.players[0].active, ("choose:counters_any:4:opp_any",))
        self.assertEqual(b.phase, "choice")
        self.assertEqual(b.players[1].active.damage, 0, "挂起期间不得预先分配")
        self.assertEqual(sum(fp.damage for fp in b.players[1].bench), 0)

    def test_places_exactly_n_with_repeats(self):
        b = _b()
        b._apply_attack_effects(b.players[0].active, ("choose:counters_any:4:opp_any",))
        for _ in range(4):
            self.assertTrue(b.step({"verb": "choose", "pick": 0})["ok"])
        self.assertEqual(b.players[1].active.damage, 40, "4 枚都放战斗位")
        self.assertEqual(b.phase, "main")
        self.assertEqual(sum(fp.damage for fp in b.players[1].bench), 0)

    def test_cannot_stop_early(self):
        b = _b()
        b._apply_attack_effects(b.players[0].active, ("choose:counters_any:4:opp_any",))
        b.step({"verb": "choose", "pick": 1})
        r = b.step({"verb": "stop"})
        self.assertFalse(r["ok"], "min=max=4，提前 stop 必须被拒绝")
        self.assertEqual(b.phase, "choice")


if __name__ == "__main__":
    unittest.main()
