# -*- coding: utf-8 -*-
"""第九十八 / 一百 / 一百一十批（2026-11-06）· 引擎用例

三族机制各自的关键判据：

  · `snipe:…:target:ex` —— 只打 **ex** 目标（非 ex 一个点都不能吃）；
  · `coin_each:…|cond:at_least_heads:1:<status>` —— ≥1 正面才施加状态，
    0 正面**不得**施加（写成 `all_heads` 会把「至少 1」变成「全部」，反之亦然）；
  · `draw_to_branch:<base>:<alt>:if:<cond>` —— 条件成立补到 alt，不成立补到 base，
    且**从不弃牌**（若误用「洗回再抽」语义，手牌会先被清空）。

运行：cd packages/engine && python -m unittest tests.test_batch98_engine -v
"""
from __future__ import annotations

import random
import unittest

from engine.battle import Battle
from engine.cards import (AttackDef, EnergyDef, EnergyRef, PokemonDef,
                          TrainerDef, TrainerRef, make_pokemon)
from engine.state import GameConfig, PlayerState


class _Fixed(random.Random):
    def __init__(self, value: float):
        super().__init__(0)
        self.value = value

    def random(self) -> float:
        return self.value


def _base(seed_rng=None):
    cfg = GameConfig()
    b = object.__new__(Battle)
    b.cfg = cfg
    b.seed = 7
    b.rng = seed_rng or _Fixed(0.4)
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
    for p in b.players:
        p.deck = [EnergyRef(EnergyDef("colorless")) for _ in range(20)]
    return b


def _mon(name, hp=200, is_ex=False, category=""):
    return PokemonDef(name, hp=hp, ptype="colorless", is_ex=is_ex,
                      category=category)


class TestSnipeTargetEx(unittest.TestCase):
    """Shaymin / Flygon ex：`snipe:opp:100:all:target:ex`"""

    TAG = ("snipe:opp:100:all:target:ex",)

    def test_only_ex_targets_take_damage(self):
        b = _base()
        b.players[0].active = make_pokemon(PokemonDef(
            "Attacker", hp=300, ptype="colorless",
            attacks=(AttackDef("Test", cost=0, damage=0,
                               effects=self.TAG),)))
        b.players[1].active = make_pokemon(_mon("NonExActive", is_ex=False))
        b.players[1].bench.append(make_pokemon(_mon("ExBench", is_ex=True)))
        b.players[1].bench.append(make_pokemon(_mon("PlainBench", is_ex=False)))
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.damage, 0, "非 ex 战斗位不得吃伤害")
        self.assertEqual(b.players[1].bench[0].damage, 100, "ex 后备应吃 100")
        self.assertEqual(b.players[1].bench[1].damage, 0, "非 ex 后备不得吃伤害")


class TestCoinEither(unittest.TestCase):
    """Vanilluxe：「If either of them is heads … Paralyzed」"""

    TAG = ("coin_each:2:heads:dmg_set:90|cond:at_least_heads:1:paralyze",)

    def _arena(self, rng):
        b = _base(rng)
        b.players[0].active = make_pokemon(PokemonDef(
            "Attacker", hp=300, ptype="colorless",
            attacks=(AttackDef("Test", cost=0, damage=0,
                               effects=self.TAG),)))
        b.players[1].active = make_pokemon(_mon("Defender", hp=400))
        return b

    def test_one_head_paralyzes(self):
        b = self._arena(_Fixed(0.4))       # 两枚都正面 → ≥1
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertIn("paralyze", b.players[1].active.status)

    def test_no_head_no_paralyze(self):
        b = self._arena(_Fixed(0.9))       # 两枚都反面 → 0 个正面
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertNotIn("paralyze", b.players[1].active.status,
                         "0 个正面时不得施加麻痹（at_least_heads:1）")


class TestDrawToBranch(unittest.TestCase):
    """Ariana：`draw_to_branch:5:8:if:own_all_name:teamrockets`"""

    def _arena(self, team_rocket_all: bool):
        b = _base()
        b.players[0].active = make_pokemon(
            _mon("Team Rocket's Honchkrow" if team_rocket_all else "Zoroark"))
        if team_rocket_all:
            b.players[0].bench.append(make_pokemon(_mon("Team Rocket's Koffing")))
        else:
            b.players[0].bench.append(make_pokemon(_mon("Team Rocket's Koffing")))
            b.players[0].bench.append(make_pokemon(_mon("Pangoro")))
        b.players[0].hand = [TrainerRef(TrainerDef(
            tag="draw_to_branch:5:8:if:own_all_name:teamrockets",
            kind="supporter", name="Ariana"))]
        b.players[0].deck = [EnergyRef(EnergyDef("colorless")) for _ in range(20)]
        return b

    def test_all_team_rocket_draws_to_8(self):
        b = self._arena(True)
        r = b.step({"verb": "trainer", "hand": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(b.players[0].hand), 8,
                         f"全队是 Team Rocket's → 应补到 8（实得 {len(b.players[0].hand)}）")

    def test_mixed_field_draws_to_5(self):
        b = self._arena(False)
        r = b.step({"verb": "trainer", "hand": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(b.players[0].hand), 5,
                         f"混编队伍 → 只补到 5（实得 {len(b.players[0].hand)}）")


class TestDiscardSpecialEnergyAny(unittest.TestCase):
    """Enhanced Hammer：`discard_energy:1:opp:special:any`

    要点：目标含**后备** —— 对手把特殊能量贴在后面时，只打战斗位的实现会
    静默变成空效果（卡面允许弃它）。
    """

    def _arena(self, on_active: bool):
        b = _base()
        b.players[0].hand = [TrainerRef(TrainerDef(
            tag="discard_energy:1:opp:special:any", kind="item",
            name="Enhanced Hammer"))]
        b.players[1].active = make_pokemon(_mon("Defender", hp=300))
        b.players[1].bench.append(make_pokemon(_mon("Bench", hp=300)))
        holder = b.players[1].active if on_active else b.players[1].bench[0]
        # 特殊能量按**卡**存在 senergy（这里用带 senergy tag 的定义）
        holder.senergy.append(EnergyRef(EnergyDef("fire", tag="senergy:mist")))
        return b, holder

    def test_discards_from_active(self):
        b, holder = self._arena(True)
        r = b.step({"verb": "trainer", "hand": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(holder.senergy), 0, "战斗位特殊能量应被弃置")

    def test_falls_back_to_bench(self):
        b, holder = self._arena(False)
        r = b.step({"verb": "trainer", "hand": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(holder.senergy), 0,
                         "战斗位没有特殊能量时应继续找后备（:any）")


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
