# -*- coding: utf-8 -*-
"""第十七批（2026-10-29）· 引擎用例：选定目标的缩放 / 条件 / 战斗位弱抗

覆盖：
  · ``snipe:<scope>:<per>:<count>:per:<counter>``  按计数源缩放（Raging Bolt /
    Kyurem ex / Wugtrio ex）
  · ``snipe:…:if:<cond>``                          条件不满足则整段不结算（Wo-Chien /
    Ting-Lu）
  · **战斗位目标吃弱点/抗性**（卡面括号只豁免后备）—— 修正此前一律按原始值的静默错算
  · ``self:N:per:<counter>``                        反伤按计数源缩放（Palafin）
  · ``nullify_if:<cond>`` 与 snipe 同招时的「失败门」语义（Coalossal）
"""
from __future__ import annotations
import random
import unittest

from engine.battle import Battle, _eval_condition
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


def _mon(name, hp=900, ptype="colorless", weakness=None, retreat=1):
    return make_pokemon(PokemonDef(name, hp=hp, ptype=ptype,
                                   weakness=weakness, retreat=retreat))


def _with_attack(b, effects, damage=0, ptype="colorless", name="Attacker"):
    b.players[0].active = make_pokemon(PokemonDef(
        name, hp=500, ptype=ptype,
        attacks=(AttackDef("Test", cost=0, damage=damage,
                           effects=tuple(effects)),)))


class TestSnipeActiveWeakness(unittest.TestCase):
    """战斗位目标必须吃弱点（此前 snipe 一律按原始值 → 静默少算）"""

    def test_active_gets_weakness(self):
        b = _arena()
        _with_attack(b, ["snipe:opp:50:1"], ptype="water")
        b.players[1].active = _mon("Weak", weakness="water")
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 100, "50 × 2（弱点）")

    def test_bench_target_no_weakness(self):
        b = _arena()
        _with_attack(b, ["snipe:opp_bench:50:1"], ptype="water")
        b.players[1].bench = [_mon("Bench", weakness="water")]
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].bench[0].damage, 50,
                         "后备不吃弱点（卡面括号豁免）")

    def test_ignore_weakness_tag_suppresses(self):
        b = _arena()
        _with_attack(b, ["snipe:opp:70:1", "ignore_weakness",
                         "ignore_resistance"], ptype="water")
        b.players[1].active = _mon("Weak", weakness="water")
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 70,
                         "卡面写明不受弱点/抗性影响时照原始值")

    def test_resistance_applies_to_active(self):
        b = _arena()
        defn = PokemonDef("Resisted", hp=900, ptype="colorless",
                          resistance="water")
        b.players[1].active = make_pokemon(defn)
        _with_attack(b, ["snipe:opp:70:1"], ptype="water")
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 40, "70 − 30（抗性）")


class TestSnipeScaled(unittest.TestCase):
    def test_per_active_energy(self):
        b = _arena()
        _with_attack(b, ["snipe:opp:30:1:per:own_active_energy"])
        b.players[0].active.add_energy("lightning", 2)
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 60, "30 × 2 能量")

    def test_per_zero_energy_is_noop(self):
        b = _arena()
        _with_attack(b, ["snipe:opp:30:1:per:own_active_energy"])
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 0)

    def test_per_prizes_taken_all_bench(self):
        b = _arena()
        _with_attack(b, ["snipe:opp_bench:10:all:per:opp_prizes_taken"])
        b.players[1].bench = [_mon("B1"), _mon("B2")]
        b.players[1].prizes_left = b.cfg.prizes - 3
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual([fp.damage for fp in b.players[1].bench], [30, 30])


class TestSnipeConditional(unittest.TestCase):
    def test_condition_false_no_damage(self):
        b = _arena()
        _with_attack(b, ["snipe:opp_bench:30:all:if:stadium_in_play"])
        b.players[1].bench = [_mon("B1")]
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].bench[0].damage, 0)

    def test_condition_true_damage(self):
        b = _arena()
        b.stadium = {"name": "Test", "tag": "stadium:no_retreat", "owner": 0}
        _with_attack(b, ["snipe:opp_bench:30:all:if:stadium_in_play"])
        b.players[1].bench = [_mon("B1")]
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].bench[0].damage, 30)

    def test_deck_at_most_condition(self):
        b = _arena()
        b.players[0].deck = [EnergyRef() for _ in range(3)]
        self.assertTrue(_eval_condition(b, "own_deck_at_most:3"))
        b.players[0].deck = [EnergyRef() for _ in range(4)]
        self.assertFalse(_eval_condition(b, "own_deck_at_most:3"))

    def test_nullify_gate_skips_snipe(self):
        """Coalossal：失败门成立时 **打战斗位与后备都不发生**"""
        b = _arena()
        b.players[1].bench = [_mon("B1")]
        _with_attack(b, [
            "snipe:opp:140:1",
            "nullify_if:not:own_discard_basic_energy_at_least:fighting:10"])
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 0)
        self.assertEqual(b.players[1].bench[0].damage, 0)


class TestSelfScaled(unittest.TestCase):
    def test_self_per_counter(self):
        b = _arena()
        _with_attack(b, ["self:10:per:own_active_counters"])
        b.players[0].active.damage = 20        # 2 个伤害指示物
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[0].active.damage, 40, "原有 20 + 反伤 20")

    def test_self_per_counter_can_ko_self(self):
        b = _arena()
        _with_attack(b, ["self:10:per:own_active_counters"])
        b.players[0].active.damage = b.players[0].active.max_hp - 10
        b.players[1].bench = []               # 己方无替补 → 自伤击倒即判负
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertTrue(b.finished)
        self.assertEqual(b.winner, 1, "出招方无替补且被自伤击倒 → 判负")


if __name__ == "__main__":
    unittest.main()
