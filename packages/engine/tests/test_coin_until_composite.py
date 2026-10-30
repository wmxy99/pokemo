# -*- coding: utf-8 -*-
"""第十八批（2026-10-29）· 引擎用例：连续掷币缩放 / 抛币复合子效果

覆盖：
  · ``coin_until_tails:dmg_plus:N``  连续抛币直到反面，每个正面 +N（Breloom / Tyrogue / Voltorb ex）
  · ``coin:heads:dmg_plus:N|heal:M`` 同一次抛币里的**复合**子效果（Floragato）
  · ``coin_all:N:tails:self:M``      「两枚都是反面」才生效的反伤（Team Rocket's Raticate）
"""
from __future__ import annotations
import random
import unittest

from engine.battle import Battle
from engine.cards import AttackDef, EnergyRef, PokemonDef, make_pokemon
from engine.state import GameConfig, PlayerState


class _Fixed(random.Random):
    """固定抛币结果（0.4 → 正面；0.6 → 反面）"""

    def __init__(self, value: float):
        super().__init__(0)
        self.value = value

    def random(self) -> float:
        return self.value


def _arena(seed: int = 7, rng=None):
    cfg = GameConfig()
    b = object.__new__(Battle)
    b.cfg = cfg
    b.seed = seed
    b.rng = rng or random.Random(seed)
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
        PokemonDef("Attacker", hp=500, ptype="colorless", attacks=()))
    b.players[1].active = make_pokemon(
        PokemonDef("Defender", hp=900, ptype="colorless", attacks=()))
    for p in b.players:
        p.deck = [EnergyRef() for _ in range(20)]
    return b


def _with_attack(b, effects, damage=0):
    b.players[0].active = make_pokemon(PokemonDef(
        "Attacker", hp=500, ptype="colorless",
        attacks=(AttackDef("Test", cost=0, damage=damage,
                           effects=tuple(effects)),)))


class TestCoinUntilTails(unittest.TestCase):
    def test_plus_scales_with_heads(self):
        b = _arena()
        _with_attack(b, ["coin_until_tails:dmg_plus:50"], damage=10)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        flips = [e for e in b.events
                 if e.get("type") == "coin"
                 and e.get("tag") == "coin_until_tails:dmg_plus:50"]
        self.assertTrue(flips, "必须记录每一次抛币（可复现）")
        self.assertEqual(flips[-1]["result"], "tails", "最后一次必须是反面")
        heads = sum(1 for f in flips if f["result"] == "heads")
        self.assertEqual(b.players[1].active.damage, 10 + 50 * heads)

    def test_never_negative_on_immediate_tails(self):
        b = _arena(rng=_Fixed(0.9))         # 第一枚即反面
        _with_attack(b, ["coin_until_tails:dmg_plus:50"], damage=10)
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 10, "0 正面 → 无加伤")


class _Seq(random.Random):
    """按序返回固定抛币结果（0.4 → 正面；0.9 → 反面）"""

    def __init__(self, values):
        super().__init__(0)
        self.values = list(values)
        self.i = 0
        self.consumed = 0

    def random(self) -> float:
        v = self.values[min(self.i, len(self.values) - 1)]
        self.i += 1
        self.consumed += 1
        return v


class TestCoinStatusAndMultiCond(unittest.TestCase):
    """第二十二批（2026-10-29）：抛币状态名归一 + 多币条件附加状态

    卡面原型：
      · ``coin:heads:paralyze``（单币）—— 历史数据里的 ``paralyzed`` 必须归一，
        否则会被效果派发的「未知 tag」分支静默忽略（状态永不生效）；
      · Slurpuff「Flip 2 coins. … If both of them are tails, … now Confused.」
        → ``coin_each:2:heads:dmg_set:90|cond:all_tails:confuse``
      · Butterfree「Flip 4 coins. … If at least 2 of them are heads, … Paralyzed.」
        → ``coin_each:4:heads:dmg_set:60|cond:at_least_heads:2:paralyze``
    """

    SLURP = "coin_each:2:heads:dmg_set:90|cond:all_tails:confuse"
    BUTTER = "coin_each:4:heads:dmg_set:60|cond:at_least_heads:2:paralyze"

    def _status(self, b):
        return set(b.players[1].active.status)

    def test_legacy_spelling_is_normalized(self):
        b = _arena(rng=_Fixed(0.4))
        _with_attack(b, ["coin:heads:paralyzed"], damage=10)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertIn("paralyze", self._status(b), "paralyzed 必须归一为 paralyze")

    def test_canonical_spelling_applies(self):
        b = _arena(rng=_Fixed(0.4))
        _with_attack(b, ["coin:heads:paralyze"], damage=10)
        b.step({"verb": "attack", "attack": 0})
        self.assertIn("paralyze", self._status(b))

    def test_all_tails_confuses(self):
        b = _arena(rng=_Seq([0.9, 0.9]))
        _with_attack(b, [self.SLURP], damage=0)
        b.step({"verb": "attack", "attack": 0})
        self.assertIn("confuse", self._status(b))
        self.assertEqual(
            len([e for e in b.events if e.get("type") == "coin"]), 2,
            "两枚币只能掷两次（条件与伤害共用同一次结果）")

    def test_all_tails_not_triggered_with_a_head(self):
        b = _arena(rng=_Seq([0.4, 0.9]))
        _with_attack(b, [self.SLURP], damage=0)
        b.step({"verb": "attack", "attack": 0})
        self.assertNotIn("confuse", self._status(b))

    def test_at_least_two_heads_paralyzes(self):
        b = _arena(rng=_Seq([0.4, 0.4, 0.9, 0.9]))
        _with_attack(b, [self.BUTTER], damage=0)
        b.step({"verb": "attack", "attack": 0})
        self.assertIn("paralyze", self._status(b))
        self.assertEqual(
            len([e for e in b.events if e.get("type") == "coin"]), 4)

    def test_at_least_two_heads_not_triggered_with_one(self):
        b = _arena(rng=_Seq([0.4, 0.9, 0.9, 0.9]))
        _with_attack(b, [self.BUTTER], damage=0)
        b.step({"verb": "attack", "attack": 0})
        self.assertNotIn("paralyze", self._status(b))

    def test_unknown_coin_cond_raises(self):
        b = _arena(rng=_Fixed(0.4))
        _with_attack(b, ["coin_each:2:heads:dmg_set:90|cond:whatever:x"],
                     damage=0)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertFalse(r["ok"], "未知多币条件必须报错，不能静默不生效")


class TestCompositeCoin(unittest.TestCase):
    def test_heads_damage_and_heal(self):
        b = _arena(rng=_Fixed(0.4))         # 恒正面
        _with_attack(b, ["coin:heads:dmg_plus:30|heal:30"], damage=10)
        b.players[0].active.damage = 40
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 40, "10 + 30")
        self.assertEqual(b.players[0].active.damage, 10, "40 − 30（回复）")

    def test_tails_no_effect(self):
        b = _arena(rng=_Fixed(0.6))         # 恒反面
        _with_attack(b, ["coin:heads:dmg_plus:30|heal:30"], damage=10)
        b.players[0].active.damage = 40
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 10)
        self.assertEqual(b.players[0].active.damage, 40, "反面不回复")


class TestCoinAllSelf(unittest.TestCase):
    def test_both_tails_self_damage(self):
        b = _arena(rng=_Fixed(0.6))
        _with_attack(b, ["coin_all:2:tails:self:90"])
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[0].active.damage, 90)

    def test_heads_no_self_damage(self):
        b = _arena(rng=_Fixed(0.4))
        _with_attack(b, ["coin_all:2:tails:self:90"])
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[0].active.damage, 0)


if __name__ == "__main__":
    unittest.main()
