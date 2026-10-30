# -*- coding: utf-8 -*-
"""第七批：回合历史条件 / 招式失败门 / 对手手牌类别计数 —— 引擎侧单测"""
from __future__ import annotations

import random
import unittest

from engine.battle import Battle, _eval_condition, _eval_counter
from engine.cards import (AttackDef, EnergyDef, EnergyRef, PokemonDef, PokeRef,
                          TrainerDef, TrainerRef, make_pokemon)
from engine.state import GameConfig, PlayerState


def _arena(seed: int = 6):
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
    b.players[0].active = make_pokemon(PokemonDef(
        "Me", hp=200, ptype="colorless",
        attacks=(AttackDef("Hit", cost=0, damage=10),)))
    b.players[1].active = make_pokemon(PokemonDef(
        "Foe", hp=300, ptype="colorless"))
    for i in range(2):
        b.players[i].deck = [PokeRef(PokemonDef(f"D{i}{k}", hp=10,
                                               ptype="colorless"))
                             for k in range(8)]
    return b


class TestHistoryConditions(unittest.TestCase):
    def test_moved_to_active(self):
        b = _arena()
        b.players[0].bench.append(make_pokemon(PokemonDef(
            "Spare", hp=90, ptype="colorless")))
        b.players[0].active.add_energy("colorless")      # 撤退费用 1
        self.assertFalse(_eval_condition(b, "moved_to_active"))
        r = b.step({"verb": "retreat", "bench": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertTrue(_eval_condition(b, "moved_to_active"))

    def test_evolved_from(self):
        b = _arena()
        base = PokemonDef("Gimmighoul", hp=60, ptype="psychic", stage=0)
        evo = PokemonDef("Gholdengo", hp=130, ptype="psychic", stage=1,
                         evolves_from="Gimmighoul")
        fp = make_pokemon(base)
        p = b.players[0]
        p.active = fp
        fp.just_played = False
        p.hand.append(PokeRef(evo))
        r = b.step({"verb": "evolve", "hand": len(p.hand) - 1, "target": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertTrue(_eval_condition(b, "evolved_from:gimmighoul"))
        self.assertFalse(_eval_condition(b, "evolved_from:magneton"))
        self.assertTrue(_eval_condition(b, "evolved_this_turn"))

    def test_healed_this_turn(self):
        b = _arena()
        b.players[0].active.damage = 50
        self.assertFalse(_eval_condition(b, "healed_this_turn"))
        b.players[0].hand.append(TrainerRef(TrainerDef(
            "heal:30", kind="item", name="Heal30")))
        r = b.step({"verb": "trainer", "hand": len(b.players[0].hand) - 1})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[0].active.damage, 20)
        self.assertTrue(_eval_condition(b, "healed_this_turn"))

    def test_used_attack_last_turn(self):
        b = _arena()
        b.players[0].active = make_pokemon(PokemonDef(
            "Roller", hp=200, ptype="colorless",
            attacks=(AttackDef("Spiky Rolling", cost=0, damage=10),)))
        b.step({"verb": "attack", "attack": 0})
        # 攻击结束回合 → 轮到对手；回到我方时「上回合」= 刚用过的招式
        b.step({"verb": "pass"})
        self.assertTrue(_eval_condition(b, "used_attack_last_turn:spikyrolling"))
        self.assertFalse(_eval_condition(b, "used_attack_last_turn:othermove"))

    def test_other_ancient_attacked_last_turn(self):
        b = _arena()
        anc = make_pokemon(PokemonDef(
            "Ancient mon", hp=200, ptype="fighting", category="Ancient",
            attacks=(AttackDef("AncHit", cost=0, damage=10),)))
        b.players[0].active = anc
        b.players[0].bench.append(make_pokemon(PokemonDef(
            "Spare", hp=90, ptype="colorless")))
        b.step({"verb": "attack", "attack": 0})
        b.step({"verb": "pass"})
        # 当前战斗位仍是「Ancient mon」→「**other**」不成立
        self.assertFalse(_eval_condition(
            b, "own_other_ancient_attacked_last_turn"))
        b.players[0].active, b.players[0].bench[0] = (
            b.players[0].bench[0], b.players[0].active)
        self.assertTrue(_eval_condition(
            b, "own_other_ancient_attacked_last_turn"))

    def test_prizes_taken_last_turn(self):
        """「对手**上回合**取走的奖赏卡」——按对手自己的回合滚动记账"""
        b = _arena()
        b.players[1].prizes_taken_this_turn = 2       # 对手本回合取走 2 张
        self.assertEqual(_eval_counter(b, "opp_prizes_taken_last_turn"), 0)
        b.step({"verb": "pass"})                       # 结束我方回合 → 对手回合开始（滚动）
        # 此刻是对手回合：本条件的「对手」= 我方，故读数为 0（口径按当前玩家算）
        self.assertEqual(_eval_counter(b, "opp_prizes_taken_last_turn"), 0)
        b.step({"verb": "pass"})                       # 对手回合结束 → 回到我方
        self.assertEqual(_eval_counter(b, "opp_prizes_taken_last_turn"), 2)


class TestNullifyGate(unittest.TestCase):
    def _attack(self, b, effects):
        b.players[0].active = make_pokemon(PokemonDef(
            "Gated", hp=200, ptype="colorless",
            attacks=(AttackDef("Gate", cost=0, damage=60,
                               effects=effects),)))
        return b.step({"verb": "attack", "attack": 0})

    def test_gate_blocks_damage_and_effects(self):
        b = _arena()          # 无场地 → not:stadium_in_play 成立 → 整招不结算
        r = self._attack(b, ("nullify_if:not:stadium_in_play", "poison"))
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.damage, 0)
        self.assertEqual(sorted(b.players[1].active.status), [])
        self.assertTrue(any(e.get("type") == "attack_nullified"
                            for e in b.events))

    def test_gate_passes_when_condition_false(self):
        b = _arena()
        b.stadium = {"name": "S", "tag": "stadium:hp_plus:10", "owner": "A"}
        self._attack(b, ("nullify_if:not:stadium_in_play", "poison"))
        dmg = [e for e in b.events if e.get("type") == "damage"]
        self.assertEqual(dmg[-1]["dmg"], 60)
        self.assertIn("poison", [e.get("status") for e in b.events
                                 if e.get("type") == "status"])
        # 攻击结束回合 → 对手回合开始中毒掉 10，故累计 70
        self.assertEqual(b.players[1].active.damage, 70)

    def test_not_prefix(self):
        b = _arena()
        self.assertTrue(_eval_condition(b, "not:stadium_in_play"))
        b.stadium = {"name": "S", "tag": "stadium:hp_plus:10", "owner": "A"}
        self.assertFalse(_eval_condition(b, "not:stadium_in_play"))

    def test_new_param_conditions(self):
        b = _arena()
        self.assertTrue(_eval_condition(b, "own_bench_at_most:4"))
        self.assertFalse(_eval_condition(b, "own_hand_exactly:7"))
        b.players[1].prizes_left = 3
        self.assertTrue(_eval_condition(b, "opp_prizes_in:3,4"))
        self.assertFalse(_eval_condition(b, "opp_prizes_in:5"))
        b.players[0].bench = [make_pokemon(PokemonDef("Uxie", hp=60,
                                                     ptype="psychic")),
                              make_pokemon(PokemonDef("Azelf", hp=60,
                                                     ptype="psychic"))]
        self.assertTrue(_eval_condition(b, "own_bench_named_all:uxie+azelf"))
        self.assertFalse(_eval_condition(b, "own_bench_named_all:uxie+mesprit"))


class TestOppHandKind(unittest.TestCase):
    def test_counts(self):
        b = _arena()
        o = b.players[1]
        o.hand = [PokeRef(PokemonDef("P", hp=10, ptype="colorless")),
                  EnergyRef(EnergyDef("fire")),
                  TrainerRef(TrainerDef("t", kind="item", name="T"))]
        self.assertEqual(_eval_counter(b, "opp_hand_kind:pokemon"), 1)
        self.assertEqual(_eval_counter(b, "opp_hand_kind:energy"), 1)
        self.assertEqual(_eval_counter(b, "opp_hand_kind:trainer"), 1)


if __name__ == "__main__":
    unittest.main()
