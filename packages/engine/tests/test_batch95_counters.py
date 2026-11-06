# -*- coding: utf-8 -*-
"""第九十五批（2026-11-06）· 条件门前置 + 后备专用指示物

Mismagius「Assassin's Magic」：「If your opponent's Active Pokémon is affected by a
Special Condition, place 6 damage counters on 1 of your opponent's Benched
Pokémon.」→ `counters:6:scope:opp_bench:1:gate:opp_active_has_status`

两处容易写错的地方：
  ① `:scope:opp` 的候选取「战斗位优先」→ 会把指示物放到**战斗位**（卡面明写
     Benched），且战斗位吃弱点，数值也会不同；
  ② 条件必须真的**吞掉**效果（gate 为假时一个指示物都不放），
     不能退化成「放置 0 个」那样仍触发受击反应/日志。

运行：cd packages/engine && python -m unittest tests.test_batch95_counters -v
"""
from __future__ import annotations

import random
import unittest

from engine.battle import Battle
from engine.cards import (AttackDef, EnergyDef, EnergyRef, PokemonDef,
                          make_pokemon)
from engine.state import GameConfig, PlayerState

TAG = "counters:6:scope:opp_bench:1:gate:opp_active_has_status"


class _Fixed(random.Random):
    def __init__(self, value: float = 0.5):
        super().__init__(0)
        self.value = value

    def random(self) -> float:
        return self.value


def _arena(status=(), bench=2):
    cfg = GameConfig()
    b = object.__new__(Battle)
    b.cfg = cfg
    b.seed = 7
    b.rng = _Fixed()
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
    b._ko_by_attack = [0, 0]
    b.players[0].active = make_pokemon(PokemonDef(
        "Attacker", hp=300, ptype="psychic",
        attacks=(AttackDef("Test", cost=0, damage=0, effects=(TAG,)),)))
    b.players[1].active = make_pokemon(PokemonDef(
        "Defender", hp=300, ptype="colorless"))
    b.players[1].active.status = list(status)
    for i in range(bench):
        b.players[1].bench.append(
            make_pokemon(PokemonDef(f"Bench{i}", hp=200, ptype="colorless")))
    # ⚠ 状态用 `confuse` 而不是 `poison`：毒在回合末检查期会额外打 10 点，
    #   混进断言会让「战斗位应为 0」的用例假失败（与指示物无关）。
    for p in b.players:
        p.deck = [EnergyRef(EnergyDef("colorless")) for _ in range(20)]
    return b


class TestGatedBenchCounters(unittest.TestCase):
    def test_gate_true_places_on_bench(self):
        b = _arena(status=("confuse",))
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.damage, 0,
                         "战斗位不得吃到指示物（卡面 Benched）")
        self.assertEqual([fp.damage for fp in b.players[1].bench], [60, 0],
                         "后备第 1 只吃 6 个指示物 = 60")

    def test_gate_false_does_nothing(self):
        b = _arena(status=())
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual([fp.damage for fp in b.players[1].bench], [0, 0],
                         "条件不成立 → 一个指示物都不放")

    def test_explicit_choice_can_pick_second_bench(self):
        """显式 `snipe` 位置码可改选目标（1 = 后备第 1 只，2 = 第 2 只）"""
        b = _arena(status=("confuse",))
        r = b.step({"verb": "attack", "attack": 0, "snipe": [2]})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual([fp.damage for fp in b.players[1].bench], [0, 60],
                         "指定第 2 只后备 → 指示物应落在它身上")


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
