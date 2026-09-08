# -*- coding: utf-8 -*-
"""PokéLab Engine · 固定结算用例集（unit fixtures）

覆盖核心结算：伤害(普攻/弱点×2/抗性−30/下限0) · 能量门槛 · 特殊状态
(毒/烧/睡/麻/混乱 及检查阶段) · 击倒→奖赏(ex=2/普=1) · 替补/无后备判负 ·
牌库空判负 · 撤退消耗 · 先手禁攻 · 状态/散布/抽牌/自愈招式效果。

运行：python -m unittest discover -s tests -v
"""
from __future__ import annotations
import random
import unittest
from engine.cards import (PokemonDef, AttackDef, EnergyRef, make_pokemon)
from engine.state import GameConfig, PlayerState
from engine.battle import Battle
from engine.rules import status_checkup, compute_damage


# ---------------- 测试专用卡 ----------------
def A(name, cost, dmg, fx=()):
    return AttackDef(name, cost, dmg, effects=tuple(fx))


def P(name, hp=100, ptype="lightning", weakness=None, resistance=None,
      atks=(), ex=False, retreat=1):
    return PokemonDef(name, hp=hp, ptype=ptype, weakness=weakness,
                      resistance=resistance, attacks=tuple(atks), is_ex=ex,
                      retreat=retreat)


SMASH = A("Smash", 1, 10)
PUNCH = A("Punch", 1, 30)
BIG = A("Big", 2, 90)
TOXIC = A("Toxic", 1, 10, ("poison",))
BURN = A("Flare", 1, 10, ("burn",))
SLEEPY = A("Powder", 1, 0, ("sleep",))
PARA = A("Fang", 1, 0, ("paralyze",))
JUMBLE = A("Jumble", 1, 0, ("confuse",))
DRAIN = A("Drain", 1, 30, ("heal:10",))
SURGE = A("Surge", 1, 10, ("draw:2",))
SPREADER = A("Raid", 2, 40, ("spread:2:10",))
RECOIL = A("Inferno", 3, 100, ("self:20",))

L_BOLT = P("LBolt", hp=100, ptype="lightning", atks=(PUNCH,))
NEUTRAL = P("NeutralT", hp=150, ptype="colorless")   # 无弱抗：基础伤害断言用
G_TARGET = P("GrassT", hp=100, ptype="grass", weakness="lightning")
R_TARGET = P("RockT", hp=100, ptype="rock", resistance="lightning")
EX_T = P("ExT", hp=60, ptype="colorless", ex=True)
KO_T = P("KoT", hp=30, ptype="colorless")
BENCH1 = P("B1", hp=50, ptype="colorless")
BENCH2 = P("B2", hp=50, ptype="colorless")
RECOILER = P("SelfP", hp=60, ptype="fire", atks=(RECOIL,))
SLEEPER = P("Sleeper", hp=80, ptype="grass", atks=(SLEEPY, PARA))
PARALYZER = P("Paralyzer", hp=80, ptype="fighting", atks=(PARA,))
JUMBLER = P("Jumbler", hp=80, ptype="psychic", atks=(JUMBLE,))
HEALER = P("Healer", hp=90, ptype="colorless", atks=(DRAIN,))
DRAWER = P("Drawer", hp=70, ptype="lightning", atks=(SURGE,))
SPREAD_ATK = P("Raider", hp=120, ptype="lightning", atks=(SPREADER,))


class Arena:
    """白盒测试台：跳过自动开局，直接摆场地"""
    def __init__(self, cfg: GameConfig | None = None, seed: int = 1):
        c = cfg or GameConfig()
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
        # 默认各给 20 能量牌库（避免回合结束 draw 触发 deckout）
        for p in b.players:
            p.deck = [EnergyRef() for _ in range(20)]
        # 双方默认站场（大部分用例可覆盖）：A=攻击方 L_BOLT，B=无弱抗 NeutralT
        self.place(0, L_BOLT)
        self.place(1, NEUTRAL)

    def place(self, pi, pdef, active=True, status=()):
        fp = make_pokemon(pdef)
        fp.status.update(status)
        p = self.b.players[pi]
        if active:
            p.active = fp
        else:
            p.bench.append(fp)
        return fp

    def hand_energy(self, pi, n=1):
        self.b.players[pi].hand.extend(EnergyRef() for _ in range(n))

    def attack(self, ai=0):
        """当前方(0)发起 attack(ai)；返回 step 结果"""
        return self.b.step({"verb": "attack", "attack": ai})


def addE(fp, n, t="colorless"):
    """测试助手：直接给场上宝可梦加 n 点（默认无色）能量"""
    fp.add_energy(t, n)
    return fp


def totE(fp):
    return fp.total_energy()


# ================= 用例组 =================
class TestDamage(unittest.TestCase):
    def test_plain_damage(self):
        a = Arena(); addE(a.b.players[0].active, 1)
        res = a.attack()
        self.assertTrue(res["ok"])
        d = a.b.players[1].active
        self.assertEqual(d.damage, 30)

    def test_weakness_double(self):
        # 用 compute_damage 直测弱/抗公式
        atk, dfn = make_pokemon(L_BOLT), make_pokemon(G_TARGET)
        self.assertEqual(compute_damage(atk, dfn, 30), 60)

    def test_resistance_minus30(self):
        atk, dfn = make_pokemon(L_BOLT), make_pokemon(R_TARGET)
        self.assertEqual(compute_damage(atk, dfn, 30), 0)
        self.assertEqual(compute_damage(atk, dfn, 100), 70)

    def test_resistance_floor_zero(self):
        atk, dfn = make_pokemon(L_BOLT), make_pokemon(R_TARGET)
        self.assertEqual(compute_damage(atk, dfn, 10), 0)

    def test_no_energy_cannot_attack(self):
        a = Arena()  # energy=0
        legal = a.b.legal_actions(0)
        self.assertNotIn({"verb": "attack", "attack": 0}, legal)
        res = a.attack()
        self.assertFalse(res["ok"])
        self.assertIn("能量不足", res["error"])


class TestSpecialConditions(unittest.TestCase):
    def test_status_checkup_poison_burn(self):
        fp = make_pokemon(KO_T)
        fp.status.update({"poison", "burn"})
        evs = status_checkup(fp, random.Random(1))
        self.assertEqual(fp.damage, 30)          # 10 + 20
        self.assertEqual(len(evs), 2)
        self.assertEqual(sorted(e["type"] for e in evs),
                         ["burn_damage", "poison_damage"])

    def test_sleep_wake_coin(self):
        fp = make_pokemon(KO_T)
        fp.status.add("sleep")
        rng = random.Random(2026)
        woken = False
        for _ in range(100):       # 伪随机序列确定性终止（必然出现正面）
            evs = status_checkup(fp, rng)
            if any(e["type"] == "sleep_wake" for e in evs):
                woken = True
                break
        self.assertTrue(woken)

    def test_sleep_blocks_attack_and_retreat(self):
        a = Arena(); addE(a.b.players[0].active, 1)
        a.b.players[0].active.status.add("sleep")
        legal = a.b.legal_actions(0)
        self.assertTrue(all(x["verb"] != "attack" for x in legal))
        res = a.attack()
        self.assertFalse(res["ok"])

    def test_paralyze_blocks_attack(self):
        a = Arena(); addE(a.b.players[0].active, 1)
        a.b.players[0].active.status.add("paralyzed")
        res = a.attack()
        self.assertFalse(res["ok"])
        self.assertIn("禁止攻击", res["error"])

    def test_confuse_tails_self_damage(self):
        a = Arena()
        atk = a.b.players[0].active
        addE(atk, 1)
        atk.status.add("confuse")
        a.b.rng.random = lambda: 0.1          # tails
        res = a.attack()
        self.assertTrue(res["ok"])
        self.assertEqual(atk.damage, 30)      # 自伤
        self.assertEqual(a.b.players[1].active.damage, 0)  # 攻击失败
        self.assertIn("confuse", atk.status)  # tails 后混乱保留

    def test_confuse_heads_attack_and_remove(self):
        a = Arena()
        atk = a.b.players[0].active
        addE(atk, 1)
        atk.status.add("confuse")
        a.b.rng.random = lambda: 0.9          # heads
        a.attack()
        self.assertEqual(a.b.players[1].active.damage, 30)
        self.assertNotIn("confuse", atk.status)

    def test_poison_apply_via_attack(self):
        a = Arena()
        a.b.players[0].active = make_pokemon(P("Poker", 80, ptype="grass",
                                                atks=(TOXIC,)))
        addE(a.b.players[0].active, 1)
        a.attack()
        self.assertIn("poison", a.b.players[1].active.status)


class TestKnockoutPrizes(unittest.TestCase):
    def test_ko_prize_one_and_promote(self):
        a = Arena()
        a.place(1, KO_T)          # hp30 换血；先移 default 后放新的会覆盖 active
        a.place(0, P("Beater", 100, ptype="lightning", atks=(BIG,)))
        a.b.players[1].bench.append(make_pokemon(BENCH1))
        addE(a.b.players[0].active, 2)
        res = a.attack()
        self.assertTrue(res["ok"])
        self.assertEqual(a.b.players[0].prizes_left, 5)     # 拿 1 奖
        self.assertIsNotNone(a.b.players[1].active)          # 已替补
        self.assertEqual(a.b.players[1].active.name, "B1")
        self.assertFalse(a.b.finished)

    def test_ko_ex_two_prizes(self):
        a = Arena()
        a.place(1, EX_T)          # ex 60hp
        a.place(0, P("Beater2", 100, ptype="lightning", atks=(BIG,)))
        addE(a.b.players[0].active, 2)
        a.attack()
        self.assertEqual(a.b.players[0].prizes_left, 4)     # 拿 2 奖

    def test_ko_no_bench_loses(self):
        a = Arena()
        a.place(1, KO_T)          # 无后备
        a.place(0, P("Beater3", 100, ptype="lightning", atks=(BIG,)))
        addE(a.b.players[0].active, 2)
        a.attack()
        self.assertTrue(a.b.finished)
        self.assertEqual(a.b.winner, 0)
        self.assertEqual(a.b.win_reason, "no_pokemon")

    def test_prizes_empty_wins(self):
        a = Arena()
        a.b.players[0].prizes_left = 1      # 只差 1 奖
        a.place(1, EX_T)
        a.place(0, P("Beater4", 100, ptype="lightning", atks=(BIG,)))
        addE(a.b.players[0].active, 2)
        a.attack()
        self.assertTrue(a.b.finished)
        self.assertEqual(a.b.winner, 0)
        self.assertEqual(a.b.win_reason, "prizes")


class TestWinConditions(unittest.TestCase):
    def test_deckout_loss(self):
        a = Arena()
        a.b.players[0].deck = []
        a.b._safe_draw(0, 1, source="test")
        self.assertTrue(a.b.finished)
        self.assertEqual(a.b.winner, 1)
        self.assertEqual(a.b.win_reason, "deckout")

    def test_no_pokemon_at_turn_start(self):
        a = Arena()
        a.b.players[1].active = None
        a.b.players[1].bench.clear()
        a.b.current = 1
        a.b._begin_turn()
        self.assertTrue(a.b.finished)
        self.assertEqual(a.b.winner, 0)


class TestActions(unittest.TestCase):
    def test_retreat_consumes_energy(self):
        a = Arena()
        a.b.players[0].bench.append(make_pokemon(BENCH1))
        atk = a.b.players[0].active
        addE(atk, 2)
        res = a.b.step({"verb": "retreat", "bench": 0})
        self.assertTrue(res["ok"])
        self.assertEqual(totE(atk), 1)     # retreat1 → 弃1能
        self.assertEqual(a.b.players[0].active.name, "B1")
        self.assertIn(atk, a.b.players[0].bench)  # 旧 active 退到后备

    def test_retreat_impossible_without_energy(self):
        a = Arena()
        a.b.players[0].bench.append(make_pokemon(BENCH1))
        res = a.b.step({"verb": "retreat", "bench": 0})
        self.assertFalse(res["ok"])

    def test_attach_once_per_turn(self):
        a = Arena()
        a.hand_energy(0, 3)
        ok1 = a.b.step({"verb": "attach", "target": 0})
        ok2 = a.b.step({"verb": "attach", "target": 0})
        self.assertTrue(ok1["ok"])
        self.assertFalse(ok2["ok"])
        self.assertEqual(totE(a.b.players[0].active), 1)

    def test_play_to_bench(self):
        a = Arena()
        a.b.players[0].hand.append(PokeRef_(BENCH1))
        res = a.b.step({"verb": "play", "index": len(a.b.players[0].hand) - 1})
        self.assertTrue(res["ok"])
        self.assertEqual(len(a.b.players[0].bench), 1)

    def test_first_turn_no_attack_flag(self):
        a = Arena(GameConfig(first_turn_no_attack=True))
        addE(a.b.players[0].active, 1)
        res = a.attack()
        self.assertFalse(res["ok"])
        self.assertIn("先手第一回合", res["error"])


class TestAttackEffects(unittest.TestCase):
    def test_spread_bench(self):
        a = Arena()
        a.b.players[0].active = make_pokemon(SPREAD_ATK)
        addE(a.b.players[0].active, 2)
        a.b.players[1].bench = [make_pokemon(BENCH1), make_pokemon(BENCH2)]
        a.attack()
        self.assertEqual(a.b.players[1].active.damage, 40)   # 主目标
        bench_dmg = sum(b.damage for b in a.b.players[1].bench)
        self.assertEqual(bench_dmg, 20)                       # 2只各10 → 至少命中1只

    def test_recoil_self(self):
        a = Arena()
        a.place(0, RECOILER)
        addE(a.b.players[0].active, 3)
        a.attack()
        self.assertEqual(a.b.players[0].active.damage, 20)   # 反伤
        self.assertEqual(a.b.players[1].active.damage, 100)

    def test_heal_self(self):
        a = Arena()
        a.place(0, HEALER)
        addE(a.b.players[0].active, 1)
        a.b.players[0].active.damage = 30
        a.attack()
        self.assertEqual(a.b.players[0].active.damage, 20)   # 30-10

    def test_draw_effect(self):
        a = Arena()
        a.place(0, DRAWER)
        addE(a.b.players[0].active, 1)
        h0 = len(a.b.players[0].hand)
        a.attack()
        self.assertEqual(len(a.b.players[0].hand), h0 + 2)


def PokeRef_(pdef):
    from engine.cards import PokeRef
    return PokeRef(pdef)


if __name__ == "__main__":
    unittest.main(verbosity=2)
