# -*- coding: utf-8 -*-
"""第八十五~八十九批（2026-11-05）· 引擎用例

按 class 分批；所有 arena 用 `object.__new__(Battle)` 手工搭最小局面，
不依赖卡库，保证零 I/O 可复跑。
运行：cd packages/engine && python -m unittest tests.test_batch85_89_mechanics -v
"""
from __future__ import annotations

import random
import unittest

from engine.battle import Battle
from engine.cards import (AbilityDef, AttackDef, EnergyDef, EnergyRef,
                          PokemonDef, make_pokemon)
from engine.state import GameConfig, PlayerState


class _Fixed(random.Random):
    def __init__(self, value: float):
        super().__init__(0)
        self.value = value

    def random(self) -> float:
        return self.value


def _arena(rng, defender_tag=None, defender_hp=100, attack_damage=150,
           attack_effects=(), tool=None):
    cfg = GameConfig()
    b = object.__new__(Battle)
    b.cfg = cfg
    b.seed = 7
    b.rng = rng
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
        "Attacker", hp=500, ptype="colorless",
        attacks=(AttackDef("Test", cost=0, damage=attack_damage,
                           effects=tuple(attack_effects)),)))
    abilities = ((AbilityDef("Survive", tag=defender_tag, location="field"),)
                 if defender_tag else ())
    b.players[1].active = make_pokemon(PokemonDef(
        "Defender", hp=defender_hp, ptype="fighting", abilities=abilities))
    if tool is not None:
        b.players[1].active.tool = tool
    for p in b.players:
        p.deck = [EnergyRef(EnergyDef("colorless")) for _ in range(20)]
    return b


# ------------------------------ 第八十五批 ------------------------------
class TestBatch85LilliePearl(unittest.TestCase):
    """ko_prize_minus:1（道具所附宝可梦被击倒，取奖赏方少拿 1）"""

    def test_tool_reduces_prize(self):
        b = _arena(_Fixed(0.4), tool="ko_prize_minus:1")
        before = b.players[0].prizes_left
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        # 基础 100 HP 非 ex/mega → 本应取 1；道具减 1 → 取 0
        self.assertEqual(b.players[0].prizes_left, before,
                         "ko_prize_minus:1 应使取奖赏方少拿 1")

    def test_without_tool_normal_prize(self):
        b = _arena(_Fixed(0.4))
        before = b.players[0].prizes_left
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[0].prizes_left, before - 1,
                         "无道具时正常取 1 张奖赏")


def _arena_least_hp(rng, attacker_hp=500, opp_active_hp=100, opp_bench_hps=()):
    cfg = GameConfig()
    b = object.__new__(Battle)
    b.cfg = cfg
    b.seed = 7
    b.rng = rng
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
        "Inteleon", hp=attacker_hp, ptype="water",
        attacks=(AttackDef("Bring Down", cost=0, damage=0,
                           effects=("knockout:least_hp",)),)))
    b.players[1].active = make_pokemon(PokemonDef(
        "Opp", hp=opp_active_hp, ptype="fighting"))
    for i, hp in enumerate(opp_bench_hps):
        b.players[1].bench.append(make_pokemon(PokemonDef(
            f"OppBench{i}", hp=hp, ptype="fighting")))
    for p in b.players:
        p.deck = [EnergyRef(EnergyDef("colorless")) for _ in range(20)]
    return b


# ------------------------------ 第八十六批 ------------------------------
class TestBatch86Inteleon(unittest.TestCase):
    """knockout:least_hp：场上剩余 HP 最少者被击倒，排除自身；取 1 张奖赏"""

    def test_knocks_out_lowest(self):
        b = _arena_least_hp(_Fixed(0.4), attacker_hp=200,
                            opp_active_hp=50, opp_bench_hps=(150,))
        # 对手战斗位 50 HP 是场上最低（除自身外）→ 应被击倒
        opp_active = b.players[1].active
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertTrue(opp_active.is_ko, "对手战斗位应被击倒")
        self.assertEqual(b.players[0].prizes_left,
                         b.cfg.prizes - 1, "击倒后应取 1 张奖赏")

    def test_excludes_self(self):
        # 攻击者自身 HP 最低（30），但「except for this Pokémon」必须排除
        b = _arena_least_hp(_Fixed(0.4), attacker_hp=30,
                            opp_active_hp=100, opp_bench_hps=())
        att = b.players[0].active
        opp_active = b.players[1].active
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertFalse(att.is_ko, "自身不得被击倒")
        self.assertTrue(opp_active.is_ko, "对手战斗位应被击倒")


def _arena_ko_prize_plus(rng, bonus_tag="ko_prize_plus:1:opp_basic",
                         opp_active_hp=50, opp_active_stage=0, attack_damage=200):
    cfg = GameConfig()
    b = object.__new__(Battle)
    b.cfg = cfg
    b.seed = 7
    b.rng = rng
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
    abilities = (AbilityDef("Greedy", tag=bonus_tag, location="field"),)
    b.players[0].active = make_pokemon(PokemonDef(
        "Hydreigon", hp=500, ptype="darkness",
        attacks=(AttackDef("Hit", cost=0, damage=attack_damage, effects=()),),
        abilities=abilities))
    b.players[1].active = make_pokemon(PokemonDef(
        "Opp", hp=opp_active_hp, ptype="fighting", stage=opp_active_stage))
    for p in b.players:
        p.deck = [EnergyRef(EnergyDef("colorless")) for _ in range(20)]
    return b


# ------------------------------ 第八十七批 ------------------------------
class TestBatch87Hydreigon(unittest.TestCase):
    """ko_prize_plus:1:opp_basic：对手基础宝可梦被击倒，取奖赏方多拿 1 张"""

    def test_basic_ko_gives_bonus(self):
        b = _arena_ko_prize_plus(_Fixed(0.4), opp_active_stage=0)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        # 基础 50 HP → 正常 1 张；opp_basic 加成 +1 → 共 2 张
        self.assertEqual(b.players[0].prizes_left, b.cfg.prizes - 2,
                         "对手基础被击倒应多拿 1 张")

    def test_non_basic_no_bonus(self):
        b = _arena_ko_prize_plus(_Fixed(0.4), opp_active_stage=2)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        # 进化宝可梦（非基础）→ 不触发 opp_basic 加成，只拿 1 张
        self.assertEqual(b.players[0].prizes_left, b.cfg.prizes - 1,
                         "非基础被击倒不应多拿")


def _arena_hand_energy(rng, hand_types=("grass",), required=True):
    eff = ("discard_hand_energy:1:type:grass:required" if required
           else "discard_hand_energy:1:type:grass")
    b = _arena(rng, defender_hp=400, attack_damage=170,
               attack_effects=(eff,))
    b.players[0].hand = [EnergyRef(EnergyDef(c)) for c in hand_types]
    return b


# ------------------------------ 第八十八批 ------------------------------
class TestBatch88HandEnergy(unittest.TestCase):
    """discard_hand_energy:1:type:grass[:required]（单数手牌弃能，Decidueye）"""

    def test_paid_discards_and_damages(self):
        b = _arena_hand_energy(_Fixed(0.4), hand_types=("grass", "fire"))
        defender = b.players[1].active
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(defender.damage, 170, "付得起代价应造成 170 伤害")
        hand_types = [getattr(x, "edef").type for x in b.players[0].hand]
        self.assertNotIn("grass", hand_types, "草能量应被弃掉")
        self.assertIn("fire", hand_types, "火能量不应被弃")

    def test_required_fail_zero_damage(self):
        # 手牌只有火能量，付不起「弃草能量」的 required 代价 → 招式无效
        b = _arena_hand_energy(_Fixed(0.4), hand_types=("fire",))
        defender = b.players[1].active
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(defender.damage, 0,
                         "付不起 required 代价 → 招式应无效（0 伤害）")


def _arena_prize_bonus(rng, tera=True, until_offset=0):
    cfg = GameConfig()
    b = object.__new__(Battle)
    b.cfg = cfg
    b.seed = 7
    b.rng = rng
    b.players = [PlayerState(name="A", prizes_left=cfg.prizes),
                 PlayerState(name="B", prizes_left=cfg.prizes)]
    b.current = 0
    b.turn_number = 5
    b.turn_counts = [1, 1]
    b.phase = "main"
    b.finished = False
    b.winner = None
    b.win_reason = None
    b.events = []
    b.stadium = None
    b._ko_by_attack = [0, 0]
    b.players[0].active = make_pokemon(PokemonDef(
        "Attacker", hp=500, ptype="psychic",
        category="Tera(Stellar)" if tera else "",
        attacks=(AttackDef("Hit", cost=0, damage=200, effects=()),)))
    b.players[1].active = make_pokemon(PokemonDef(
        "Opp", hp=100, ptype="fighting"))
    for p in b.players:
        p.deck = [EnergyRef(EnergyDef("colorless")) for _ in range(20)]
    # 模拟「本回合已打出 Briar」：旗标在本回合有效
    b.players[0].prize_bonus_n = 1
    b.players[0].prize_bonus_tera = True
    b.players[0].prize_bonus_until = b.turn_number + until_offset
    return b


# ------------------------------ 第八十九批 ------------------------------
class TestBatch89PrizeBonus(unittest.TestCase):
    """prize_bonus:1:tera：Tera 攻击方击倒对手战斗位 → 多拿 1 张奖赏"""

    def test_tera_bonus(self):
        b = _arena_prize_bonus(_Fixed(0.4), tera=True)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        # 基础 100 HP → 正常 1 张；Briar 加成 +1 → 共 2 张
        self.assertEqual(b.players[0].prizes_left, b.cfg.prizes - 2,
                         "Tera 击倒应多拿 1 张")

    def test_non_tera_no_bonus(self):
        b = _arena_prize_bonus(_Fixed(0.4), tera=False)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[0].prizes_left, b.cfg.prizes - 1,
                         "非 Tera 攻击方不应多拿")

    def test_expired_no_bonus(self):
        b = _arena_prize_bonus(_Fixed(0.4), tera=True, until_offset=-1)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[0].prizes_left, b.cfg.prizes - 1,
                         "旗标过期（上回合）不应多拿")


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
