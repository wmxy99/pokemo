# -*- coding: utf-8 -*-
"""PokéLab Engine v0.2 · E1/E2/E4 固定结算用例集

覆盖：
  E1 彩色能量：分色 cost 不足/满足、无色 cost 可被彩色能支付、撤退扣能无色优先
  E2 进化：链匹配合法、保留能量与伤害/清状态、刚放置/刚进化不可再进化、错链拒绝
  E4 训练家：poffin 检索铺后备 / ultra_ball 弃2检索 / hammer 抛币弃能 /
              boss 拉后备 + supporter 每回合一次 / item 不限次

运行：python -m unittest discover -s tests -v
"""
from __future__ import annotations
import random
import unittest
from engine.cards import (PokemonDef, AttackDef, EnergyDef, EnergyRef,
                          PokeRef, TrainerDef, TrainerRef, make_pokemon)
from engine.state import GameConfig, PlayerState
from engine.battle import Battle

# ---------------- v0.2 测试卡 ----------------
G1 = AttackDef("Slash", 1, 30)
G2 = AttackDef("Big Leaf", 2, 80)
G3 = AttackDef("Leaf Storm", (("grass", 2),), 110)
FB = AttackDef("Fire Bolt", (("fire", 1), ("psychic", 1)), 200)

BASIC_A = PokemonDef("BasicA", hp=80, ptype="grass", attacks=(G1,))
STAGE1_A = PokemonDef("Stage1A", hp=130, ptype="grass", attacks=(G2,),
                      stage=1, evolves_from="BasicA")
STAGE2_A = PokemonDef("Stage2A", hp=200, ptype="grass", attacks=(G3,),
                      stage=2, evolves_from="Stage1A")
COLOR_USER = PokemonDef("ColorUser", hp=120, ptype="fire",
                        attacks=(FB,), stage=0)
NEUTRAL = PokemonDef("NeutralT", hp=150, ptype="colorless")
BIG_HP = PokemonDef("BigHP", hp=500, ptype="colorless")   # 高血量：验证不 KO 的完整伤害
COLORED_NEED = PokemonDef("Need2", hp=100, ptype="colorless",
                          attacks=(G3,))     # 需求 2 草能
_EF = EnergyDef("fire")
_EG = EnergyDef("grass")
_EC = EnergyDef()

T_POFFIN = TrainerDef("poffin", kind="item", name="Buddy Ball")
T_BOSS = TrainerDef("boss", kind="supporter", name="Orders")
T_HAMMER = TrainerDef("hammer", kind="item", name="Energy Crush")
T_UBALL = TrainerDef("ultra_ball", kind="item", name="Hyper Ball")


class ArenaV2:
    """白盒测试台（v0.2）"""
    def __init__(self, seed: int = 1):
        c = GameConfig()
        self.b = object.__new__(Battle)
        b = self.b
        b.cfg = c
        b.seed = seed
        b.rng = random.Random(seed)
        b.players = [PlayerState(name="A", prizes_left=c.prizes),
                     PlayerState(name="B", prizes_left=c.prizes)]
        b.current = 0
        b.turn_number = 1
        b.turn_counts = [1, 1]
        b.phase = "main"
        b.finished = False
        b.winner = None
        b.win_reason = None
        b.events = []
        for p in b.players:
            p.deck = [EnergyRef() for _ in range(20)]
        self.place(0, BASIC_A)
        self.place(1, NEUTRAL)

    def place(self, pi, pdef, active=True, energy=None, status=()):
        fp = make_pokemon(pdef)
        for t, n in (energy or {}).items():
            fp.add_energy(t, n)
        fp.status.update(status)
        p = self.b.players[pi]
        if active:
            p.active = fp
        else:
            p.bench.append(fp)
        return fp

    def hand_add(self, pi, *refs):
        self.b.players[pi].hand.extend(refs)

    def step(self, action):
        return self.b.step(action)

    def set_coin(self, heads: bool):
        self.b.rng.random = lambda: 0.2 if heads else 0.9


class TestColorEnergy(unittest.TestCase):
    def test_color_cost_insufficient(self):
        a = ArenaV2()
        a.place(0, COLOR_USER)               # 招式需 F+P
        a.b.players[0].active.add_energy("fire", 1)   # 只给火
        legal = a.b.legal_actions(0)
        self.assertTrue(all(x["verb"] != "attack" for x in legal))
        res = a.step({"verb": "attack", "attack": 0})
        self.assertFalse(res["ok"])
        self.assertIn("能量不足", res["error"])

    def test_color_cost_satisfied(self):
        a = ArenaV2()
        a.place(0, COLOR_USER)
        a.place(1, BIG_HP)
        atk = a.b.players[0].active
        atk.add_energy("fire", 1)
        atk.add_energy("psychic", 1)
        res = a.step({"verb": "attack", "attack": 0})
        self.assertTrue(res["ok"])
        self.assertEqual(a.b.players[1].active.damage, 200)
        self.assertFalse(a.b.finished)

    def test_colorless_cost_payable_by_colored(self):
        a = ArenaV2()
        a.place(0, COLORED_NEED)             # cost=2 草（彩色）而非无色
        a.b.players[0].active.add_energy("grass", 2)
        res = a.step({"verb": "attack", "attack": 0})
        self.assertTrue(res["ok"])

    def test_retreat_colorless_priority(self):
        a = ArenaV2()
        a.place(1, NEUTRAL)
        a.b.players[0].bench.append(make_pokemon(NEUTRAL))
        atk = a.b.players[0].active
        atk.add_energy("colorless", 1)
        atk.add_energy("fire", 2)            # retreat=1 → 应优先扣无色
        res = a.step({"verb": "retreat", "bench": 0})
        self.assertTrue(res["ok"])
        self.assertEqual(atk.energy_of("colorless"), 0)
        self.assertEqual(atk.energy_of("fire"), 2)


class TestEvolution(unittest.TestCase):
    def test_evolve_legal_and_retain(self):
        a = ArenaV2()
        base = a.b.players[0].active
        base.damage = 30
        base.add_energy("grass", 2)
        base.status.add("poison")
        base.just_played = False
        a.hand_add(0, PokeRef(STAGE1_A))
        res = a.step({"verb": "evolve", "hand": len(a.b.players[0].hand) - 1,
                      "target": 0})
        self.assertTrue(res["ok"])
        self.assertEqual(base.name, "Stage1A")
        self.assertEqual(base.damage, 30)         # 伤害保留
        self.assertEqual(base.total_energy(), 2)  # 能量保留
        self.assertEqual(base.energy_of("grass"), 2)
        self.assertNotIn("poison", base.status)   # 状态清除
        self.assertTrue(base.just_played)

    def test_cannot_evolve_twice_same_turn(self):
        a = ArenaV2()
        base = a.b.players[0].active
        base.just_played = False
        a.hand_add(0, PokeRef(STAGE1_A), PokeRef(STAGE2_A))

        def find(name):
            return next(i for i, c in enumerate(a.b.players[0].hand)
                        if isinstance(c, PokeRef) and c.pdef.name == name)

        hi1 = find("Stage1A")
        self.assertTrue(a.step({"verb": "evolve", "hand": hi1,
                                "target": 0})["ok"])
        # 刚进化（just_played）→ 不可再进化（index 需按变化后手牌重算）
        hi2 = find("Stage2A")
        res = a.step({"verb": "evolve", "hand": hi2, "target": 0})
        self.assertFalse(res["ok"])

    def test_cannot_evolve_just_placed(self):
        a = ArenaV2()
        fp = make_pokemon(BASIC_A)
        fp.just_played = True
        a.b.players[0].bench.append(fp)          # 刚放置
        a.hand_add(0, PokeRef(STAGE1_A))
        hi = len(a.b.players[0].hand) - 1
        res = a.step({"verb": "evolve", "hand": hi, "target": 1})
        self.assertFalse(res["ok"])
        self.assertIn("刚放置", res["error"])

    def test_wrong_chain_rejected(self):
        a = ArenaV2()
        base = a.b.players[0].active            # BasicA
        base.just_played = False
        wrong = PokemonDef("Stage1X", hp=120, ptype="grass", stage=1,
                           evolves_from="Other")
        a.hand_add(0, PokeRef(wrong))
        res = a.step({"verb": "evolve", "hand": len(a.b.players[0].hand) - 1,
                      "target": 0})
        self.assertFalse(res["ok"])
        self.assertIn("进化链", res["error"])


class TestTrainers(unittest.TestCase):
    def test_poffin_searches_two_basics_to_bench(self):
        a = ArenaV2()
        # 牌库预置 3 只基础
        a.b.players[0].deck = ([PokeRef(BASIC_A)] * 3
                               + [EnergyRef() for _ in range(10)])
        a.hand_add(0, TrainerRef(T_POFFIN))
        hi = len(a.b.players[0].hand) - 1
        res = a.step({"verb": "trainer", "hand": hi})
        self.assertTrue(res["ok"])
        self.assertEqual(len(a.b.players[0].bench), 2)

    def test_ultra_ball_discards_and_searches(self):
        a = ArenaV2()
        a.b.players[0].deck = [PokeRef(BASIC_A)] + [EnergyRef() for _ in range(5)]
        h0 = len(a.b.players[0].discard)
        a.hand_add(0, TrainerRef(T_UBALL), EnergyRef(), EnergyRef())
        hi = next(i for i, c in enumerate(a.b.players[0].hand)
                  if isinstance(c, TrainerRef))
        res = a.step({"verb": "trainer", "hand": hi})
        self.assertTrue(res["ok"])
        # 弃 2 能量 + 弃自身 → 检索到的基础入手（手牌 1 张宝可梦）
        self.assertEqual(len(a.b.players[0].hand), 1)
        self.assertIsInstance(a.b.players[0].hand[0], PokeRef)
        self.assertEqual(len(a.b.players[0].discard) - h0, 3)  # 自身+弃2

    def test_hammer_heads_removes_energy(self):
        a = ArenaV2()
        a.place(1, NEUTRAL)
        a.b.players[1].active.add_energy("grass", 2)
        a.hand_add(0, TrainerRef(T_HAMMER))
        a.set_coin(True)
        res = a.step({"verb": "trainer", "hand": len(a.b.players[0].hand) - 1})
        self.assertTrue(res["ok"])
        self.assertEqual(a.b.players[1].active.total_energy(), 1)

    def test_hammer_tails_no_effect(self):
        a = ArenaV2()
        a.place(1, NEUTRAL)
        a.b.players[1].active.add_energy("grass", 2)
        a.hand_add(0, TrainerRef(T_HAMMER))
        a.set_coin(False)
        a.step({"verb": "trainer", "hand": len(a.b.players[0].hand) - 1})
        self.assertEqual(a.b.players[1].active.total_energy(), 2)

    def test_boss_drags_bench_and_supporter_once(self):
        a = ArenaV2()
        a.place(1, NEUTRAL)
        a.b.players[1].bench.append(make_pokemon(BASIC_A))
        a.hand_add(0, TrainerRef(T_BOSS), TrainerRef(T_BOSS))
        hi = next(i for i, c in enumerate(a.b.players[0].hand)
                  if isinstance(c, TrainerRef))
        res = a.step({"verb": "trainer", "hand": hi, "target": 0})
        self.assertTrue(res["ok"])
        self.assertEqual(a.b.players[1].active.name, "BasicA")  # 被拉上场
        self.assertEqual(a.b.players[0].supporter_used, 1)
        # 第二个支援者同回合被拒
        hi2 = next(i for i, c in enumerate(a.b.players[0].hand)
                   if isinstance(c, TrainerRef))
        res2 = a.step({"verb": "trainer", "hand": hi2, "target": 0})
        self.assertFalse(res2["ok"])
        self.assertIn("支援者", res2["error"])

    def test_item_unlimited_per_turn(self):
        a = ArenaV2()
        a.b.players[0].deck = ([PokeRef(BASIC_A)] * 5
                               + [EnergyRef() for _ in range(5)])
        a.hand_add(0, TrainerRef(T_POFFIN), TrainerRef(T_POFFIN))
        idx = [i for i, c in enumerate(a.b.players[0].hand)
               if isinstance(c, TrainerRef)]
        self.assertTrue(a.step({"verb": "trainer", "hand": idx[0]})["ok"])
        # 第二个物品仍可用（bench 空位 → 放 1 只）
        res2 = a.step({"verb": "trainer", "hand": idx[1] - 1})
        self.assertTrue(res2["ok"])


class TestDracoDeckSmoke(unittest.TestCase):
    """Y 版 Draco ex 卡组端到端：彩色能量+进化线+训练家在真实对局中可跑"""
    def test_draco_deck_matches_finish(self):
        from engine import decklib
        from engine.simulator import run_match
        from engine.agents import BaselineAI
        for seed in range(8):
            b = run_match(decklib.deck_draco_demo(),
                          decklib.deck_fire_demo(),
                          BaselineAI("draco-b"), BaselineAI("fire-b"),
                          seed=seed)
            self.assertTrue(b.finished, f"seed={seed} 未终局")
            self.assertGreater(b.turn_number, 0)

    def test_draco_evolve_path_available(self):
        # 构造确定性局面：我方 active=Drakling、手牌=Draco(一阶) → evolve 必合法
        from engine.cards import PokeRef as PR
        from engine import decklib as dl
        b = Battle(dl.deck_draco_demo(), dl.deck_fire_demo(), seed=3)
        cur = b.players[b.current]
        cur.active = make_pokemon(dl.DRAKLING)
        cur.active.just_played = False
        cur.hand[:] = [PR(dl.DRACO)]
        v = b.view()
        evs = [x for x in v["legal"] if x["verb"] == "evolve"]
        self.assertTrue(len(evs) >= 1)
        hi = evs[0]["hand"]
        res = b.step({"verb": "evolve", "hand": hi, "target": 0})
        self.assertTrue(res["ok"])
        self.assertEqual(cur.active.name, "Draco")


if __name__ == "__main__":
    unittest.main(verbosity=2)
