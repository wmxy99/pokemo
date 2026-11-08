# -*- coding: utf-8 -*-
"""S1c · 手牌 ↔ 牌库顶互换（Gumshoos「Evidence Gathering」）

性质：① 挂起时**不交换**（引擎不自选哪张手牌）；② 选哪张由牌手定；
③ 交换是双向的（原手牌进牌库顶，原牌库顶入手）。
"""
from __future__ import annotations
import random
import unittest

from engine.battle import Battle
from engine.cards import (AbilityDef, EnergyDef, EnergyRef, PokemonDef,
                          make_pokemon)
from engine.state import GameConfig, PlayerState


def _b():
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
        "Gumshoos", hp=120, ptype="colorless",
        abilities=(AbilityDef("Evidence Gathering",
                             tag="choose:swap_hand_top", location="field"),)))
    b.players[1].active = make_pokemon(PokemonDef("B", hp=300, ptype="colorless"))
    b.players[0].hand = [EnergyRef(EnergyDef("fire")),
                         EnergyRef(EnergyDef("water"))]
    b.players[0].deck = [EnergyRef(EnergyDef("grass")),     # 底
                         EnergyRef(EnergyDef("lightning"))]  # 顶
    return b


class TestSwapHandTop(unittest.TestCase):
    def test_opens_without_swapping(self):
        b = _b()
        b._pending = None
        b._do_ability(b.players[0], {"name": "Evidence Gathering", "pos": 0,
                                     "tag": "choose:swap_hand_top"})
        self.assertEqual(b.phase, "choice")
        self.assertEqual(len(b.players[0].hand), 2, "挂起期间不得交换")

    def test_swap_is_bidirectional(self):
        b = _b()
        p = b.players[0]
        top = p.deck[-1]
        b._open_choice("swap_hand_top",
                       [{"ref": c, "display": "x"} for c in p.hand],
                       min_pick=1, max_pick=1)
        r = b.step({"verb": "choose", "pick": 0})
        self.assertTrue(r["ok"], r)
        self.assertEqual(len(p.hand), 2)
        self.assertIn(top, p.hand, "原牌库顶应入手")
        self.assertNotIn(p.deck[-1], p.hand,
                         "原手牌应离开手牌并放到牌库顶")

    def test_requires_nonempty_hand_and_deck(self):
        b = _b()
        b.players[0].hand = []
        with self.assertRaises(ValueError):
            b._do_ability(b.players[0], {"name": "Evidence Gathering",
                                         "pos": 0,
                                         "tag": "choose:swap_hand_top"})


if __name__ == "__main__":
    unittest.main()
