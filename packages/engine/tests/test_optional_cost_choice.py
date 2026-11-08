# -*- coding: utf-8 -*-
"""第一百三十五批（S1b）· 引擎用例：可选代价（confirm 选择）+ 回合延后

卡面原型：Mega Eelektross ex「Disaster Shock」
「You may discard 2 {L} Energy from this Pokémon and make your opponent's
Active Pokémon Paralyzed.」

三条性质：
  1. **不默认**：挂起 confirm，牌手点「不支付」时能量与状态都不变；
  2. **付账才有效果**：点「支付」才弃 2 个该色能量并使对手战斗位麻痹；
  3. **回合延后**：选择未决时 `_end_turn` 不得换手（否则选择落到对手回合）。
"""
from __future__ import annotations

import random
import unittest

from engine.battle import Battle
from engine.cards import (AttackDef, EnergyDef, EnergyRef, PokemonDef,
                          make_pokemon)
from engine.state import GameConfig, PlayerState

TAG = "choose:optional_energy_cost:2:lightning:then:paralyze"


def _b(energy: int = 3):
    cfg = GameConfig()
    b = object.__new__(Battle)
    b.cfg = cfg
    b.seed = 7
    b.rng = random.Random(7)
    b.players = [PlayerState(name="A", prizes_left=6),
                 PlayerState(name="B", prizes_left=6)]
    b.current = 0
    b.turn_number = 1
    b.turn_counts = [1, 1]
    b.phase = "main"
    b._pending = None
    b._defer_end_turn = False
    b.finished = False
    b.winner = None
    b.win_reason = None
    b.events = []
    b.stadium = None
    b._ko_by_attack = [0, 0]
    b._ko_tr_by_attack = [0, 0]
    b.players[0].active = make_pokemon(PokemonDef(
        "Mega Eelektross ex", hp=330, ptype="lightning",
        attacks=(AttackDef("Disaster Shock", cost=(), damage=190),)))
    b.players[0].active.energy["lightning"] = energy
    b.players[1].active = make_pokemon(PokemonDef(
        "Defender", hp=400, ptype="fighting"))
    for p in b.players:
        p.deck = [EnergyRef(EnergyDef("colorless")) for _ in range(20)]
    return b


class TestOptionalCost(unittest.TestCase):
    def test_choice_opened_not_defaulted(self):
        b = _b()
        b._apply_attack_effects(b.players[0].active, (TAG,))
        self.assertEqual(b.phase, "choice")
        self.assertEqual(b._pending.kind, "confirm")
        self.assertEqual(b.players[0].active.total_energy(), 3,
                         "挂起期间不得预先扣能量（不默认「付」）")
        self.assertEqual(b.players[1].active.status, set(),
                         "挂起期间不得预先施加状态")

    def test_decline_changes_nothing(self):
        b = _b()
        b._apply_attack_effects(b.players[0].active, (TAG,))
        b.step({"verb": "stop"})          # 不选 = 不支付
        self.assertEqual(b.players[0].active.total_energy(), 3)
        self.assertEqual(b.players[1].active.status, set())
        self.assertEqual(b.phase, "main")

    def test_pay_discards_and_paralyzes(self):
        b = _b()
        b._apply_attack_effects(b.players[0].active, (TAG,))
        r = b.step({"verb": "choose", "pick": 0})
        self.assertTrue(r["ok"], r)
        self.assertEqual(b.players[0].active.energy.get("lightning"), 1)
        self.assertIn("paralyze", b.players[1].active.status)
        # max=1 → 选完自动闭合
        self.assertEqual(b.phase, "main")

    def test_unpayable_is_logged_not_paralyzed(self):
        b = _b(energy=1)                  # 只有 1 个雷能量，付不出 2 个
        b._apply_attack_effects(b.players[0].active, (TAG,))
        b.step({"verb": "choose", "pick": 0})
        self.assertNotIn("paralyze", b.players[1].active.status)
        types = [e["type"] for e in b.events]
        self.assertIn("optional_unpaid", types,
                      "付不出必须留下日志（否则「选了却没发生」无法追查）")

    def test_end_turn_deferred_until_choice_closes(self):
        b = _b()
        b._apply_attack_effects(b.players[0].active, (TAG,))
        b._end_turn()
        self.assertEqual(b.current, 0, "未决选择时不得换手")
        self.assertEqual(b.phase, "choice")
        b.step({"verb": "stop"})
        self.assertEqual(b.current, 1, "闭合后才换手")
        self.assertIn("turn_end_deferred", [e["type"] for e in b.events])


if __name__ == "__main__":
    unittest.main()
