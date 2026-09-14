# -*- coding: utf-8 -*-
"""PokéLab Engine v1.4 · B4 新引擎机制用例

覆盖（本轮新增机制）
  抛币        coin:heads:<tag> / coin:tails:<tag> / coin_each:<n>:tails:<tag>
  伤害修正    dmg_plus:N · dmg_plus:N:per:<counter> · dmg_plus:N:if:<cond> ·
              dmg_zero · ignore_weakness / ignore_resistance
  护盾        shield:reduce:N · shield:prevent_all（持续到该玩家下个回合开始）
  被动免疫    passive:bench_immune（在后备时免疫招式伤害与效果）
  抽到指定数  draw_to:N
  招式内进化  evolve_from_deck（从牌库检索「可进化此宝可梦的卡」直接进化）

约定
  · 抛币：``rng.random() < 0.5`` 记为 **heads**（正面），否则 tails；结果写 `coin` 事件
  · 计数源（counter）为**公开信息**的确定性统计：opp_bench / own_bench /
    opp_active_energy / own_active_energy / both_active_energy /
    own_bench_counters / own_discard_energy / own_hand
  · 未知计数源 → 抛错（快速失败，禁止静默按 0 计算导致口径漂移）

运行：python -m unittest discover -s tests -v
"""
from __future__ import annotations
import random
import unittest

from engine import decklib as dl
from engine.battle import Battle
from engine.cards import (AbilityDef, AttackDef, EnergyDef, EnergyRef, PokeRef,
                          PokemonDef, make_pokemon)
from engine.state import GameConfig, PlayerState

E_FIRE = EnergyDef("fire", name_zh="基本火能量")


def _arena(seed: int = 7):
    """最小可控战场：双方各 1 只 200HP 无色宝可梦 + 空手牌 + 20 张牌库"""
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
    b.players[0].active = make_pokemon(
        PokemonDef("Attacker", hp=200, ptype="colorless", attacks=()))
    b.players[1].active = make_pokemon(
        PokemonDef("Defender", hp=200, ptype="colorless", attacks=()))
    for p in b.players:
        p.deck = [EnergyRef() for _ in range(20)]
    return b


def _equip(b, effects, owner=0, damage=10, hp=200):
    """给 owner 装一个带指定效果的招式并设为其 active"""
    atk = AttackDef("Test Atk", cost=0, damage=damage, effects=effects)
    fp = make_pokemon(PokemonDef(f"Holder{owner}", hp=hp, ptype="colorless",
                                 attacks=(atk,)))
    b.players[owner].active = fp
    return fp


def _bench(b, owner, n, hp=100):
    out = []
    for i in range(n):
        fp = make_pokemon(PokemonDef(f"B{owner}_{i}", hp=hp,
                                     ptype="colorless", attacks=()))
        b.players[owner].bench.append(fp)
        out.append(fp)
    return out


class TestCoinFlip(unittest.TestCase):
    """抛币：结果分支 + 可复现"""

    def _attack(self, effects, seed=7, damage=10):
        b = _arena(seed=seed)
        _equip(b, effects, damage=damage)
        return b, b.step({"verb": "attack", "attack": 0})

    def test_coin_records_event_and_branches(self):
        """coin:heads:<tag> —— 正面执行分支，反面不执行；两者都写 coin 事件"""
        b, r = self._attack(("coin:heads:dmg_plus:20",))
        self.assertTrue(r["ok"], r.get("error"))
        flips = [e for e in b.events if e["type"] == "coin"]
        self.assertEqual(len(flips), 1, "应记录 1 次抛币结果")
        dmg = b.players[1].active.damage
        if flips[0]["result"] == "heads":
            self.assertEqual(dmg, 30, "正面应 +20 伤害")
        else:
            self.assertEqual(dmg, 10, "反面应无加成")

    def test_coin_reproducible_same_seed(self):
        out = []
        for _ in range(3):
            b, _ = self._attack(("coin:heads:dmg_plus:40",), seed=11)
            out.append((b.players[1].active.damage,
                        [e for e in b.events if e["type"] == "coin"][0]["result"]))
        self.assertEqual(len(set(out)), 1, f"同 seed 必须完全一致：{out}")

    def test_coin_tails_zero_damage(self):
        """coin:tails:dmg_zero —— 反面则本次伤害归零"""
        b, r = self._attack(("coin:tails:dmg_zero",), damage=60)
        self.assertTrue(r["ok"], r.get("error"))
        flip = [e for e in b.events if e["type"] == "coin"][0]["result"]
        expect = 0 if flip == "tails" else 60
        self.assertEqual(b.players[1].active.damage, expect)

    def test_coin_each_tails_counters(self):
        """coin_each:N:tails:<tag> —— 按对手后备数各抛 1 枚，每反面执行一次"""
        b = _arena(seed=7)
        _bench(b, 1, 3)
        _equip(b, ("coin_each:3:tails:dmg_plus:20",), damage=10)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        flips = [e for e in b.events if e["type"] == "coin"]
        self.assertEqual(len(flips), 3, f"应为 3 只后备各抛 1 次：{flips}")
        tails = sum(1 for e in flips if e["result"] == "tails")
        self.assertEqual(b.players[1].active.damage, 10 + 20 * tails)


class TestDamageMods(unittest.TestCase):
    """伤害修正：计数源 / 条件 / 忽略弱抗"""

    def _go(self, effects, damage=10):
        b = _arena()
        _equip(b, effects, damage=damage)
        r = b.step({"verb": "attack", "attack": 0})
        return b, r

    def test_plus_per_opp_bench(self):
        b = _arena()
        _bench(b, 1, 4)
        _equip(b, ("dmg_plus:20:per:opp_bench",))
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 10 + 80)

    def test_plus_per_opp_active_energy(self):
        b = _arena()
        b.players[1].active.add_energy("water", 3)
        _equip(b, ("dmg_plus:30:per:opp_active_energy",))
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 10 + 90)

    def test_plus_per_both_active_energy(self):
        b = _arena()
        # 注意顺序：_equip 会替换 active → 必须先装招式再附能量
        holder = _equip(b, ("dmg_plus:30:per:both_active_energy",))
        holder.add_energy("fire", 2)
        b.players[1].active.add_energy("water", 3)
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 10 + 30 * 5)

    def test_plus_per_own_bench_counters(self):
        b = _arena()
        bench = _bench(b, 0, 3)
        bench[0].damage = 40
        bench[1].damage = 20
        _equip(b, ("dmg_plus:10:per:own_bench_counters",))
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 10 + 60)

    def test_plus_per_own_discard_energy(self):
        b = _arena()
        for _ in range(2):
            b.players[0].discard.append(E_FIRE.name)
            b.players[0].discard_refs.append(EnergyRef(E_FIRE))
        _equip(b, ("dmg_plus:10:per:own_discard_energy",))
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 10 + 20)

    def test_plus_per_own_hand(self):
        b = _arena()
        b.players[0].hand = [EnergyRef() for _ in range(5)]
        _equip(b, ("dmg_plus:10:per:own_hand",))
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 10 + 50)

    def test_plus_if_opp_ko_last_turn_not_set(self):
        b, r = self._go(("dmg_plus:60:if:opp_ko_last_turn",))
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.damage, 10, "未触发条件不应加伤")

    def test_plus_if_opp_ko_last_turn_set(self):
        b = _arena()
        b._ko_by_attack = [0, 0]
        b._ko_by_attack[1] = 1          # 对手上回合有宝可梦被击倒
        _equip(b, ("dmg_plus:60:if:opp_ko_last_turn",))
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.damage, 70)

    def test_ignore_weakness(self):
        b = _arena()
        b.players[1].active = make_pokemon(
            PokemonDef("WeakGuy", hp=200, ptype="water", weakness="colorless",
                       attacks=()))
        _equip(b, ("ignore_weakness",))
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 10, "忽略弱点后不应 ×2")

    def test_weakness_applies_without_ignore(self):
        b = _arena()
        b.players[1].active = make_pokemon(
            PokemonDef("WeakGuy2", hp=200, ptype="water", weakness="colorless",
                       attacks=()))
        _equip(b, (), damage=10)
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 20, "弱点应 ×2")

    def test_unknown_counter_fails_fast(self):
        """未知计数源不得静默按 0 处理 → 必须报错（防口径漂移）"""
        b, r = self._go(("dmg_plus:10:per:no_such_counter",))
        self.assertFalse(r["ok"])
        self.assertIn("no_such_counter", r["error"])

    def test_plus_per_without_counter_fails_fast(self):
        b, r = self._go(("dmg_plus:10:per:",))
        self.assertFalse(r["ok"], "缺少计数源应报错")


class TestShields(unittest.TestCase):
    """护盾：减伤 / 免疫（持续到该玩家下个回合开始）"""

    def _hit(self, damage=50, effects=()):
        b = _arena()
        atk = AttackDef("Hit", cost=0, damage=damage, effects=effects)
        b.players[0].active = make_pokemon(
            PokemonDef("Hitter", hp=200, ptype="colorless", attacks=(atk,)))
        return b, atk

    def test_shield_reduce_partial(self):
        b, _ = self._hit(damage=50)
        b.players[1].active.shield_reduce = 30
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 20)

    def test_shield_reduce_floors_at_zero(self):
        b, _ = self._hit(damage=10)
        b.players[1].active.shield_reduce = 30
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 0, "减伤下限为 0")

    def test_shield_prevent_all_blocks_damage(self):
        b, _ = self._hit(damage=80)
        b.players[1].active.shield_prevent_all = True
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 0)

    def test_shield_prevent_all_blocks_effects(self):
        b, _ = self._hit(damage=80, effects=("poison",))
        b.players[1].active.shield_prevent_all = True
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.status, set(), "免疫应挡住效果")

    def test_effects_land_without_shield(self):
        b, _ = self._hit(damage=80, effects=("poison",))
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.status, {"poison"})

    def test_shield_tag_sets_flag(self):
        """shield:reduce:N / shield:prevent_all 可通过招式效果施加"""
        b = _arena()
        _equip(b, ("shield:reduce:30",), owner=1, damage=0)
        b.step({"verb": "attack", "attack": 0})      # A 打 B（B 无盾）
        b.current = 1
        r = b.step({"verb": "attack", "attack": 0})  # B 给自己上盾
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.shield_reduce, 30)

    def test_shield_expires_on_owner_turn_start(self):
        """INV-1：护盾在该玩家**下个回合开始**时失效"""
        b = _arena()
        b.players[0].active.shield_prevent_all = True
        b.players[0].active.shield_reduce = 30
        b.step({"verb": "pass"})                     # A 过 → B 回合
        self.assertTrue(b.players[0].active.shield_prevent_all,
                        "B 的回合不应清除 A 的护盾")
        self.assertEqual(b.players[0].active.shield_reduce, 30)
        b.step({"verb": "pass"})                     # B 过 → A 回合
        self.assertFalse(b.players[0].active.shield_prevent_all,
                         "A 回合开始应清除 A 的护盾")
        self.assertEqual(b.players[0].active.shield_reduce, 0)


class TestPassiveBenchImmune(unittest.TestCase):
    """被动：在后备时免疫招式伤害与效果"""

    @staticmethod
    def _def(name="Guarded"):
        ab = AbilityDef("Bench Guard", name_zh="后备护盾",
                        tag="passive:bench_immune", location="field")
        return PokemonDef(name, hp=100, ptype="colorless", attacks=(),
                          abilities=(ab,))

    def test_bench_immune_blocks_spread(self):
        b = _arena()
        gp = make_pokemon(self._def())
        b.players[1].bench.append(gp)
        _equip(b, ("spread_free:60",), damage=0)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(gp.damage, 0, "后备免疫应挡住 spread 伤害")

    def test_bench_immune_blocks_fixed_spread(self):
        b = _arena()
        gp = make_pokemon(self._def())
        b.players[1].bench.append(gp)
        _equip(b, ("spread:1:30",), damage=0)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(gp.damage, 0)

    def test_bench_immune_not_effective_in_active(self):
        """卡面限定「在后备时」→ 位于战斗位时不生效"""
        b = _arena()
        gp = make_pokemon(self._def())
        b.players[1].active = gp
        atk = AttackDef("Hit", cost=0, damage=40, effects=())
        b.players[0].active = make_pokemon(
            PokemonDef("Hitter", hp=200, ptype="colorless", attacks=(atk,)))
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(gp.damage, 40)

    def test_push_damage_promotes_immune_pokemon(self):
        """被击倒后免疫宝可梦被推上前台 → 此后应可受伤（不再是后备）"""
        b = _arena()
        gp = make_pokemon(self._def())
        b.players[1].bench.append(gp)
        b.players[1].active.damage = 200          # 战斗位已倒
        b._resolve_ko(1, b.players[1].active)
        self.assertIs(b.players[1].active, gp)
        atk = AttackDef("Hit2", cost=0, damage=40, effects=())
        b.players[0].active = make_pokemon(
            PokemonDef("Hitter2", hp=200, ptype="colorless", attacks=(atk,)))
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(gp.damage, 40, "上台后免疫不再生效")

    def test_passive_not_in_legal_actions(self):
        b = _arena()
        b.players[1].bench.append(make_pokemon(self._def()))
        legal = [a for a in b.legal_actions(1) if a["verb"] == "ability"]
        self.assertEqual(legal, [], "被动特性不应出现在合法动作表")


class TestDrawTo(unittest.TestCase):
    """draw_to:N —— 抽到指定手牌数"""

    def _go(self, hand_n, n=5):
        b = _arena()
        p = b.players[0]
        p.hand = [EnergyRef() for _ in range(hand_n)]
        p.deck = [EnergyRef() for _ in range(10)]
        atk = AttackDef("Refill", cost=0, damage=0, effects=(f"draw_to:{n}",))
        b.players[0].active = make_pokemon(
            PokemonDef("Refiller", hp=200, ptype="colorless", attacks=(atk,)))
        r = b.step({"verb": "attack", "attack": 0})
        return p, r

    def test_draw_up_to_size(self):
        p, r = self._go(2, 5)
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(p.hand), 5)

    def test_noop_when_already_enough(self):
        p, r = self._go(7, 5)
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(p.hand), 7, "已达标不应再抽")

    def test_draw_to_capped_by_deck(self):
        b = _arena()
        p = b.players[0]
        p.hand = []
        p.deck = [EnergyRef() for _ in range(3)]
        atk = AttackDef("Refill", cost=0, damage=0, effects=("draw_to:8",))
        b.players[0].active = make_pokemon(
            PokemonDef("Refiller2", hp=200, ptype="colorless", attacks=(atk,)))
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(p.hand), 3, "牌库不足时抽空而非报错")


class TestEvolveFromDeck(unittest.TestCase):
    """evolve_from_deck —— 从牌库检索「可进化此宝可梦的卡」直接进化"""

    def _setup(self, deck_has_target=True):
        atk = AttackDef("Evolve Call", cost=0, damage=0,
                        effects=("evolve_from_deck",))
        base = PokemonDef("EvoBase", hp=70, ptype="dragon", stage=0,
                          attacks=(atk,))
        stage1 = PokemonDef("EvoTarget", hp=120, ptype="dragon", stage=1,
                            evolves_from="EvoBase", attacks=())
        b = _arena()
        p = b.players[0]
        p.active = make_pokemon(base)
        p.deck = [EnergyRef() for _ in range(5)]
        if deck_has_target:
            p.deck.append(PokeRef(stage1))
        p.deck_has = stage1
        return b, p, stage1

    def test_evolves_from_deck(self):
        b, p, stage1 = self._setup(True)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(p.active.name, "EvoTarget", "应从牌库检索并进化")
        self.assertFalse(
            any(isinstance(c, PokeRef) and c.pdef.name == "EvoTarget"
                for c in p.deck), "进化卡应已离开牌库")

    def test_no_target_is_noop_not_error(self):
        """牌库无进化卡 → 招式仍成立（不报错），这是卡面「可以」语义"""
        b, p, _ = self._setup(False)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(p.active.name, "EvoBase")

    def test_evolve_from_deck_keeps_energy(self):
        """进化保留能量与伤害（引擎 evolve_to 语义）"""
        b, p, _ = self._setup(True)
        p.active.add_energy("fire", 2)
        p.active.damage = 30
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(p.active.energy_of("fire"), 2)
        self.assertEqual(p.active.damage, 30)


if __name__ == "__main__":
    unittest.main()
