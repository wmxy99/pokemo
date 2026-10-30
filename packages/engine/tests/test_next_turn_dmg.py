# -*- coding: utf-8 -*-
"""第十六批（2026-10-29）· 引擎用例：跨回合伤害修正

覆盖：
  · ``dmg_taken_plus_next:N``      受击方在**我方下个回合**多受 N（弱抗**之后**）
  · ``atk_minus_next:N``           受击方在**它下个回合**的招式 -N（弱抗**之前**）
  · ``self_attack_plus_next:N:name:<招式>``  自身具名招式在**我方下个回合** +N

三者的结算位置与作用对象各不相同，混用会静默错算（尤其弱点翻倍时）。

回合口径：`b.step({"verb":"attack"})` 结束当前回合并切入对方回合；
`b.step({"verb":"pass"})` 也结束回合。下面用「attack / pass」交替把回合推回原玩家。
⚠ 到期用例必须用**另一个招式**推进：同一招式每打一次都会重新挂上修正
（卡面语义如此），用同一招永远测不到「到期」。
"""
from __future__ import annotations
import random
import unittest

from engine.battle import Battle
from engine.cards import AttackDef, EnergyRef, PokemonDef, make_pokemon
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
        PokemonDef("Attacker", hp=500, ptype="colorless", attacks=()))
    b.players[1].active = make_pokemon(
        PokemonDef("Defender", hp=900, ptype="colorless", attacks=()))
    for p in b.players:
        p.deck = [EnergyRef() for _ in range(20)]
    return b


def _mon(name, hp=900, ptype="colorless", weakness=None, attacks=()):
    return make_pokemon(PokemonDef(
        name, hp=hp, ptype=ptype, weakness=weakness, attacks=tuple(attacks)))


class TestDmgTakenPlusNext(unittest.TestCase):
    """Vibrava / Stunfisk：`+N` 在弱点/抗性**之后**叠加"""

    def _battle(self, weakness, n=50, base=10):
        b = _arena()
        b.players[0].active = _mon("Vibrava", ptype="fighting", attacks=(
            AttackDef("Screech", cost=0, damage=base,
                      effects=(f"dmg_taken_plus_next:{n}",)),
            AttackDef("Hit", cost=0, damage=base),
        ))
        b.players[1].active = _mon("Defender", weakness=weakness)
        return b

    def test_applied_after_weakness(self):
        b = self._battle(weakness="fighting")
        b.step({"verb": "attack", "attack": 0})       # 本回合：10×2 = 20
        self.assertEqual(b.players[1].active.damage, 20)
        b.step({"verb": "pass"})                      # 对手回合直接结束
        before = b.players[1].active.damage
        b.step({"verb": "attack", "attack": 1})       # 我方下个回合：Hit
        # 弱抗之后：(10 × 2) + 50 = 70 —— 若在弱抗前叠加会是 (10+50)×2 = 120
        self.assertEqual(b.players[1].active.damage - before, 70)

    def test_expires_after_next_turn(self):
        b = self._battle(weakness=None, base=10)
        b.step({"verb": "attack", "attack": 0})       # 留下修正（until = 3）
        b.step({"verb": "pass"})
        b.step({"verb": "attack", "attack": 1})       # 下个回合：10 + 50
        self.assertEqual(b.players[1].active.damage, 70)
        b.step({"verb": "pass"})
        before = b.players[1].active.damage
        b.step({"verb": "attack", "attack": 1})       # 再下一个回合：已到期
        self.assertEqual(b.players[1].active.damage - before, 10,
                         "过了「下个回合」就不得再加伤")

    def test_no_bonus_without_tag(self):
        b = _arena()
        b.players[0].active = _mon("Plain", attacks=(
            AttackDef("Hit", cost=0, damage=10),))
        b.step({"verb": "attack", "attack": 0})
        b.step({"verb": "pass"})
        before = b.players[1].active.damage
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage - before, 10)


class TestAtkMinusNext(unittest.TestCase):
    """Hop's Rookidee / Sylveon ex：对方招式 -N，在弱点/抗性**之前**"""

    def _battle(self, my_weakness=None):
        b = _arena()
        b.players[0].active = _mon(
            "Rookidee", hp=500, attacks=(
                AttackDef("Intimidating Stare", cost=0,
                          effects=("atk_minus_next:20",)),))
        b.players[1].active = _mon(
            "BigHitter", ptype="colorless", attacks=(
                AttackDef("Smash", cost=0, damage=100),))
        b.players[0].active = _mon(
            "Rookidee", hp=500, weakness=my_weakness, attacks=(
                AttackDef("Intimidating Stare", cost=0,
                          effects=("atk_minus_next:20",)),))
        return b

    def test_reduced_before_weakness(self):
        b = self._battle(my_weakness=None)
        b.step({"verb": "attack", "attack": 0})       # 我方施加 -20（不造成伤害）
        b.step({"verb": "attack", "attack": 0})       # 对手下个回合出招
        self.assertEqual(b.players[0].active.damage, 80, "100 - 20")

    def test_reduced_before_weakness_double(self):
        b = self._battle(my_weakness="colorless")
        b.step({"verb": "attack", "attack": 0})
        b.step({"verb": "attack", "attack": 0})
        # 弱抗之前：(100 - 20) × 2 = 160 —— 若在弱抗后会是 200 - 20 = 180
        self.assertEqual(b.players[0].active.damage, 160)

    def test_expires_after_next_turn(self):
        b = self._battle(my_weakness=None)
        b.step({"verb": "attack", "attack": 0})       # 我方施加
        b.step({"verb": "attack", "attack": 0})       # 对手下个回合：100-20
        self.assertEqual(b.players[0].active.damage, 80)
        b.step({"verb": "pass"})                      # 我方回合（不再施加）
        b.step({"verb": "attack", "attack": 0})       # 对手再下个回合：已到期
        self.assertEqual(b.players[0].active.damage, 180, "80 + 100")


class TestSelfAttackPlusNext(unittest.TestCase):
    """Meloetta ex / Pecharunt：自身**具名招式**在**我方下个回合** +N"""

    def _battle(self):
        b = _arena()
        b.players[0].active = _mon("Meloetta", attacks=(
            AttackDef("Echoed Voice", cost=0, damage=10, effects=(
                "self_attack_plus_next:80:name:echoed voice",)),
            AttackDef("Tackle", cost=0, damage=30),
        ))
        b.players[1].active = _mon("Defender")
        return b

    def test_only_named_attack(self):
        b = self._battle()
        b.step({"verb": "attack", "attack": 0})       # 本回合 Echoed Voice
        b.step({"verb": "pass"})
        before = b.players[1].active.damage
        b.step({"verb": "attack", "attack": 0})       # 我方下个回合
        self.assertEqual(b.players[1].active.damage - before, 90, "10 + 80")
        b.step({"verb": "pass"})
        before = b.players[1].active.damage
        b.step({"verb": "attack", "attack": 1})       # 另一招式不加伤
        self.assertEqual(b.players[1].active.damage - before, 30,
                         "Tackle 不受 Echoed Voice 的加伤影响")

    def test_expires_after_next_turn(self):
        b = self._battle()
        b.step({"verb": "attack", "attack": 0})       # 留下修正（until = 3）
        b.step({"verb": "pass"})
        b.step({"verb": "attack", "attack": 1})       # 下个回合打别的招（不刷新）
        self.assertEqual(b.players[1].active.damage, 40, "10 + 30")
        b.step({"verb": "pass"})
        before = b.players[1].active.damage
        b.step({"verb": "attack", "attack": 0})       # 再下个回合：已到期
        self.assertEqual(b.players[1].active.damage - before, 10)

    def test_evolve_clears(self):
        mon = _mon("Meloetta")
        mon.self_atk_plus_next = {"echoed voice": (80, 9)}
        mon.evolve_to(PokemonDef("Evolved", hp=900, ptype="colorless",
                                 stage=1))
        self.assertEqual(mon.self_atk_plus_next, {})


if __name__ == "__main__":
    unittest.main()
