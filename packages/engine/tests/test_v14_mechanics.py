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
                          PokemonDef, TrainerDef, TrainerRef, make_pokemon)
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


class TestDiscardEnergyTarget(unittest.TestCase):
    """`discard_energy:N:opp` —— 弃**对手**战斗位能量（2026-09-21 新增）

    背景：卡面常见的「Discard an Energy from your opponent's Active Pokémon」
    早期只能映射成 `discard_energy:N`（语义 = 弃自身）→ 4 张真实卡在真实对局里
    把自家能量丢掉、对手毫发无损（静默算错）。这里锁定修正后的语义。
    """

    def _run(self, tag, own_e=2, opp_e=2):
        b = _arena()
        # 先 _equip（它会**替换** players[0].active），再给新的 active 加能量 ——
        # 顺序反了会拿到旧对象（本测试第一版就踩了这个，断言看起来"引擎没弃"）
        me = _equip(b, (tag,), owner=0)
        foe = b.players[1].active
        me.add_energy("fire", own_e)
        foe.add_energy("water", opp_e)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        return b, me, foe

    def test_opp_variant_discards_from_opponent(self):
        _, me, foe = self._run("discard_energy:1:opp")
        self.assertEqual(foe.total_energy(), 1, "应弃掉对手 1 个能量")
        self.assertEqual(me.total_energy(), 2, "自身能量不得被动到")

    def test_own_variant_discards_from_self(self):
        _, me, foe = self._run("discard_energy:1")
        self.assertEqual(me.total_energy(), 1, "应弃掉自身 1 个能量")
        self.assertEqual(foe.total_energy(), 2, "对手能量不得被动到")

    def test_opp_variant_logs_side(self):
        b, _, _ = self._run("discard_energy:2:opp")
        ev = [e for e in b.events if e.get("type") == "discard_energy"]
        self.assertTrue(ev, "应记录 discard_energy 事件")
        self.assertEqual(ev[-1].get("side"), "opp")
        self.assertEqual(ev[-1].get("n"), 2)

    def test_opp_variant_handles_empty_opp_energy(self):
        """对手无能量时不得抛错、不得转去弃自家能量"""
        b = _arena()
        me = _equip(b, ("discard_energy:1:opp",), owner=0)
        me.add_energy("fire", 2)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(me.total_energy(), 2, "对手没能量时不能改弃自家")


class TestPrizesTakenCounters(unittest.TestCase):
    """`own/opp_prizes_taken` —— 「已取得」奖赏卡数（与 `*_prizes_left` 口径相反）

    背景：`for each Prize card your opponent has taken` 早期被映射成 prizes_left
    （剩余）→ 伤害随对手取卡而**减少**，方向反了（4 张真实卡受影响）。
    """

    def _dmg_with(self, counter, taken):
        b = _arena()
        _equip(b, (f"dmg_plus:10:per:{counter}",), owner=0, damage=0)
        # 让对手已取得 `taken` 张奖赏卡（prizes_left 相应减少）
        b.players[1].prizes_left = b.cfg.prizes - taken
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        return b.players[1].active.damage

    def test_opp_prizes_taken_increases_with_taken(self):
        self.assertEqual(self._dmg_with("opp_prizes_taken", 0), 0)
        self.assertEqual(self._dmg_with("opp_prizes_taken", 3), 30)

    def test_opp_prizes_left_decreases_with_taken(self):
        """对照：left 口径方向相反（这正是不能混用的原因）"""
        total = _arena().cfg.prizes
        self.assertEqual(self._dmg_with("opp_prizes_left", 0), total * 10)
        self.assertEqual(self._dmg_with("opp_prizes_left", 3),
                         (total - 3) * 10)


class TestSnipeTargets(unittest.TestCase):
    """`snipe:<scope>:<per>[:<count>]` —— **选定**目标各受 per 点（2026-09-22 新增）

    背景：卡面「This attack does N damage to **1 of / each of** your opponent's
    Pokémon / Benched Pokémon」这一类此前**无法表达**（只能落进 unsupported）：
    引擎原有的 `spread:N:per` 是**随机** n 只对手后备、`spread_free:N` 只能任意
    分配到对手后备，**都没有"选定"语义**，也都不含战斗位。
    """

    def test_scope_opp_bench_all(self):
        b = _arena()
        bench = _bench(b, 1, 4)
        _equip(b, ("snipe:opp_bench:50:all",), owner=0, damage=0)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual([fp.damage for fp in bench], [50, 50, 50, 50])

    def test_scope_opp_bench_defaults_to_one(self):
        """`count` 缺省 = 1；未指定目标时取候选表第一个（确定性默认）"""
        b = _arena()
        bench = _bench(b, 1, 3)
        _equip(b, ("snipe:opp_bench:60:1",), owner=0, damage=0)
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual([fp.damage for fp in bench], [60, 0, 0])

    def test_explicit_target_selection(self):
        """动作里给 `snipe`（位置码）→ 打指定的那只，而不是默认第一只"""
        b = _arena()
        bench = _bench(b, 1, 3)
        _equip(b, ("snipe:opp_bench:60:1",), owner=0, damage=0)
        b.step({"verb": "attack", "attack": 0, "snipe": [2]})
        self.assertEqual([fp.damage for fp in bench], [0, 0, 60])

    def test_scope_opp_includes_active(self):
        """`opp` 作用域含战斗位（位置码 0 = 战斗位）—— spread 做不到这点"""
        b = _arena()
        bench = _bench(b, 1, 2)
        foe_active = b.players[1].active
        _equip(b, ("snipe:opp:100:1",), owner=0, damage=0)
        b.step({"verb": "attack", "attack": 0, "snipe": [0]})
        self.assertEqual(foe_active.damage, 100)
        self.assertEqual([fp.damage for fp in bench], [0, 0])

    def test_scope_opp_all_hits_active_and_bench(self):
        b = _arena()
        bench = _bench(b, 1, 3)
        foe_active = b.players[1].active
        _equip(b, ("snipe:opp:30:all",), owner=0, damage=0)
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(foe_active.damage, 30)
        self.assertEqual([fp.damage for fp in bench], [30, 30, 30])

    def test_scope_opp_count_two(self):
        b = _arena()
        bench = _bench(b, 1, 3)
        _equip(b, ("snipe:opp:50:2",), owner=0, damage=0)
        b.step({"verb": "attack", "attack": 0})
        hits = [b.players[1].active.damage] + [fp.damage for fp in bench]
        self.assertEqual(hits.count(50), 2, "应恰好命中 2 只（含战斗位）")

    def test_scope_own_bench(self):
        """打**我方**后备（卡面「also does N damage to each of your Benched」）"""
        b = _arena()
        mine = _bench(b, 0, 2)
        foe_bench = _bench(b, 1, 2)
        _equip(b, ("snipe:own_bench:30:all",), owner=0, damage=0)
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual([fp.damage for fp in mine], [30, 30])
        self.assertEqual([fp.damage for fp in foe_bench], [0, 0],
                         "不得误伤对手后备")

    def test_scope_both_bench(self):
        """「each Benched Pokémon (both yours and your opponent's)」"""
        b = _arena()
        mine = _bench(b, 0, 2)
        foe = _bench(b, 1, 2)
        _equip(b, ("snipe:both_bench:20:all",), owner=0, damage=0)
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual([fp.damage for fp in mine], [20, 20])
        self.assertEqual([fp.damage for fp in foe], [20, 20])

    def test_no_candidates_is_not_an_error(self):
        """对手后备为空 → 无效果，不得抛错（真实对局常见）"""
        b = _arena()
        _equip(b, ("snipe:opp_bench:50:all",), owner=0, damage=0)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))

    def test_respects_bench_immune(self):
        b = _arena()
        ab = AbilityDef("Guard", name_zh="后备护盾",
                        tag="passive:bench_immune", location="field")
        shielded = make_pokemon(PokemonDef("Shielded", hp=100,
                                           ptype="colorless", attacks=(),
                                           abilities=(ab,)))
        plain = make_pokemon(PokemonDef("Plain", hp=100, ptype="colorless",
                                        attacks=()))
        b.players[1].bench.extend([shielded, plain])
        _equip(b, ("snipe:opp_bench:30:all",), owner=0, damage=0)
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(shielded.damage, 0)
        self.assertEqual(plain.damage, 30)

    def test_own_bench_damage_ignores_bench_immune(self):
        """后备免疫是「防对手」的被动 → 我方自己打自家后备时**不该**被它挡住"""
        b = _arena()
        ab = AbilityDef("Guard", name_zh="后备护盾",
                        tag="passive:bench_immune", location="field")
        mine = make_pokemon(PokemonDef("Mine", hp=100, ptype="colorless",
                                       attacks=(), abilities=(ab,)))
        b.players[0].bench.append(mine)
        _equip(b, ("snipe:own_bench:20:all",), owner=0, damage=0)
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(mine.damage, 20)

    def test_snipe_can_ko_and_awards_prize(self):
        b = _arena()
        bench = _bench(b, 1, 1, hp=50)
        _equip(b, ("snipe:opp_bench:60:1",), owner=0, damage=0)
        before = b.players[0].prizes_left
        b.step({"verb": "attack", "attack": 0})
        self.assertTrue(bench[0].is_ko or not b.players[1].bench)
        self.assertEqual(b.players[0].prizes_left, before - 1,
                         "击倒后备应正常给奖赏卡")

    def test_invalid_scope_raises(self):
        """未知作用域 → 抛错（禁止静默无效果）"""
        b = _arena()
        _equip(b, ("snipe:nosuch:30:1",), owner=0, damage=0)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertFalse(r["ok"])
        self.assertIn("snipe scope", str(r.get("error")))

    def test_random_spread_still_bounded(self):
        """对照：`spread:N:per` 仍是**随机** n 只（不得被 snipe 语义污染）"""
        b = _arena()
        bench = _bench(b, 1, 4)
        _equip(b, ("spread:2:30",), owner=0, damage=0)
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(sum(1 for fp in bench if fp.damage == 30), 2)



class TestRecoilSelfDamage(unittest.TestCase):
    """`self:N` 由**卡面反伤**产出的路径（2026-09-22）

    背景：「This Pokémon does N damage to itself.」全库 49 条，
    早期被读成「打对手战斗位」→ 反伤**静默消失**。
    """

    def test_recoil_hits_attacker_not_defender(self):
        b = _arena()
        me = _equip(b, ("self:20",), owner=0, damage=40)
        foe = b.players[1].active
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(me.damage, 20, "反伤应落在攻击者自身")
        self.assertEqual(foe.damage, 40, "对手只受招式伤害")

    def test_recoil_can_self_ko(self):
        b = _arena()
        me = _equip(b, ("self:210",), owner=0, damage=0, hp=200)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r.get("ok", True))
        self.assertTrue(me.is_ko, "反伤超过剩余 HP 应自爆")


class TestRecoverKinds(unittest.TestCase):
    """`recover:N[:kind]` 的 kind 家族（2026-09-22 新增 trainer / supporter / item）

    背景：真实卡面有「Put a **Trainer** card from your discard pile into your
    hand.」（Dedenne）与「Put up to 2 **Supporter** cards …」（Miracle Headset）
    两类写法，此前只能登记未建模 —— 而引擎本来就有 TrainerRef。
    """

    @staticmethod
    def _state():
        p = PlayerState(name="A", prizes_left=6)
        p.discard_refs = [
            PokeRef(PokemonDef("P1", hp=60, ptype="colorless", attacks=())),
            EnergyRef(E_FIRE),
            TrainerRef(TrainerDef("ultra_ball", kind="item", name="item_a")),
            TrainerRef(TrainerDef("boss", kind="supporter", name="sup_a")),
        ]
        return p

    def test_pokemon_kind(self):
        from engine.battle import _recover_candidates
        state = self._state()
        idx = _recover_candidates(state, "recover:5:pokemon")
        self.assertEqual([type(state.discard_refs[i]).__name__ for i in idx],
                         ["PokeRef"])

    def test_energy_kind(self):
        from engine.battle import _recover_candidates
        state = self._state()
        idx = _recover_candidates(state, "recover:5:energy")
        self.assertEqual([type(state.discard_refs[i]).__name__ for i in idx],
                         ["EnergyRef"])

    def test_trainer_kind_takes_both_item_and_supporter(self):
        from engine.battle import _recover_candidates
        state = self._state()
        idx = _recover_candidates(state, "recover:5:trainer")
        self.assertEqual([type(state.discard_refs[i]).__name__ for i in idx],
                         ["TrainerRef", "TrainerRef"])

    def test_supporter_kind_filters(self):
        from engine.battle import _recover_candidates
        state = self._state()
        idx = _recover_candidates(state, "recover:5:supporter")
        self.assertEqual(len(idx), 1)
        self.assertEqual(state.discard_refs[idx[0]].tdef.kind, "supporter")

    def test_any_kind_does_not_cross_into_trainers(self):
        """`any` 对应「宝可梦或基本能量」，**不得**把训练家也捞进来（多算）"""
        from engine.battle import _recover_candidates
        state = self._state()
        idx = _recover_candidates(state, "recover:5:any")
        kinds = {type(state.discard_refs[i]).__name__ for i in idx}
        self.assertEqual(kinds, {"PokeRef", "EnergyRef"})

    def test_count_is_respected(self):
        from engine.battle import _recover_candidates
        state = self._state()
        self.assertEqual(len(_recover_candidates(state, "recover:1:any")), 1)


class TestMill(unittest.TestCase):
    """`mill:N[:opp]` —— 牌库顶 N 张进弃牌堆（2026-09-22 新增）

    背景：卡面「Discard the top N cards of your / your opponent's deck」整类此前
    落进「静态/未建模（英文）」（B3 `discard_self` 29 条里 20 条是它）。
    牌库被磨空**不立即判负** —— 与官方一致：判负发生在「该抽牌却抽不到」时。
    """

    def test_mill_own_deck(self):
        b = _arena()
        before = len(b.players[0].deck)
        _equip(b, ("mill:3",), owner=0, damage=0)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(b.players[0].deck), before - 3)
        self.assertEqual(len(b.players[0].discard), 3)
        # 对手牌库只因为**回合开始的正常抽牌**少 1 张（不是被磨的）
        self.assertEqual(len(b.players[1].deck), 19)
        self.assertEqual(len(b.players[1].discard), 0)

    def test_mill_opponent_deck(self):
        b = _arena()
        before = len(b.players[1].deck)
        _equip(b, ("mill:1:opp",), owner=0, damage=0)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        # 被磨 1 张 + 自己回合开始正常抽 1 张
        self.assertEqual(len(b.players[1].deck), before - 2)
        self.assertEqual(len(b.players[1].discard), 1)
        self.assertEqual(len(b.players[0].deck), 20, "不得动自己牌库")

    def test_mill_more_than_deck_is_not_an_error(self):
        """牌库不足 N 张 → 磨到空即可，不得抛错"""
        b = _arena()
        b.players[0].deck = b.players[0].deck[:2]
        _equip(b, ("mill:7",), owner=0, damage=0)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(b.players[0].deck), 0)
        self.assertEqual(len(b.players[0].discard), 2)

    def test_mill_to_empty_does_not_end_game_immediately(self):
        """磨空自己不立即判负（判负在抽牌失败时）"""
        b = _arena()
        b.players[0].deck = []
        _equip(b, ("mill:1",), owner=0, damage=0)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertFalse(b.finished)

    def test_mill_logs_event(self):
        b = _arena()
        _equip(b, ("mill:2:opp",), owner=0, damage=0)
        b.step({"verb": "attack", "attack": 0})
        ev = [e for e in b.events if e.get("type") == "mill"]
        self.assertTrue(ev)
        self.assertEqual(ev[-1]["n"], 2)
        self.assertEqual(ev[-1]["player"], 1)


class TestAbilitySearch(unittest.TestCase):
    """特性侧**全牌库检索**（2026-09-22 新增）—— 与 `recon:N` 语义不同

    历史缺陷：特性侧检索一律映射成 `recon:N`，而引擎的 recon 只能从牌库顶 N 张里
    **随机**拿 1 张 → 「Search your deck for a Pokémon」变成"从牌堆顶碰运气"。
    实测池内 16 条此类错映射。现在 `search_pokemon|trainer|any|energy:N` 走真正的
    牌库检索。
    """

    @staticmethod
    def _ability_battle(tag, deck_kinds=("pokemon",), hand=()):
        from engine.cards import TrainerRef, TrainerDef
        b = _arena()
        ab = AbilityDef("Fetch", name_zh="检索", tag=tag, location="field")
        b.players[0].active = make_pokemon(
            PokemonDef("Fetcher", hp=200, ptype="colorless", attacks=(),
                       abilities=(ab,)))
        deck = []
        for k in deck_kinds:
            if k == "pokemon":
                deck.append(PokeRef(PokemonDef("Target", hp=60,
                                               ptype="colorless", attacks=())))
            elif k == "trainer":
                deck.append(TrainerRef(TrainerDef("poffin", kind="item",
                                                  name="Buddy")))
            else:
                deck.append(EnergyRef(E_FIRE))
        b.players[0].deck = deck + [EnergyRef(E_FIRE) for _ in range(5)]
        b.players[0].hand = list(hand)
        b._test_tag = tag
        return b

    def _use(self, b):
        # 引擎的特性动作带 `tag`（由 `_ability_legals` 给出，客户端原样回传）
        return b.step({"verb": "ability", "name": "Fetch", "pos": 0,
                       "tag": b._test_tag})

    def test_search_pokemon_takes_from_deck(self):
        b = self._ability_battle("search_pokemon:1")
        r = self._use(b)
        self.assertTrue(r["ok"], r.get("error"))
        self.assertTrue(any(getattr(c, "pdef", None) and c.pdef.name == "Target"
                            for c in b.players[0].hand),
                        "应从牌库拿到宝可梦")

    def test_search_trainer(self):
        b = self._ability_battle("search_trainer:1", deck_kinds=("trainer",))
        r = self._use(b)
        self.assertTrue(r["ok"], r.get("error"))
        self.assertTrue(any(getattr(c, "tdef", None) for c in b.players[0].hand))

    def test_search_energy(self):
        b = self._ability_battle("search_energy:1", deck_kinds=("energy",))
        r = self._use(b)
        self.assertTrue(r["ok"], r.get("error"))
        self.assertTrue(any(isinstance(c, EnergyRef) for c in b.players[0].hand))

    def test_recon_still_takes_from_top(self):
        """对照：`recon:N` 仍是「牌库顶 N 取 1」，语义未被污染"""
        b = self._ability_battle("recon:2")
        r = self._use(b)
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(b.players[0].hand), 1)

    def test_ability_search_not_offered_when_deck_lacks_kind(self):
        """牌库没有该类别的卡 → 不列为合法动作（不得发动后抛错）"""
        b = self._ability_battle("search_pokemon:1", deck_kinds=())
        names = [a.get("name") for a in b.legal_actions()]
        self.assertNotIn("Fetch", names)


class TestParameterizedConditions(unittest.TestCase):
    """带参数条件（2026-09-22 新增）：卡面「If this Pokémon has any {L} Energy
    attached, this attack does 80 more damage」→ `own_active_energy_type:lightning`
    """

    def _dmg(self, effects, energies):
        b = _arena()
        me = _equip(b, effects, owner=0, damage=0)
        for e in energies:
            me.add_energy(e)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        return b.players[1].active.damage

    def test_energy_type_condition_true(self):
        self.assertEqual(self._dmg(("dmg_plus:80:if:own_active_energy_type:fire",),
                                   ["fire"]), 80)

    def test_energy_type_condition_false(self):
        self.assertEqual(self._dmg(("dmg_plus:80:if:own_active_energy_type:fire",),
                                   ["water"]), 0)

    def test_energy_type_condition_any_one_suffices(self):
        self.assertEqual(self._dmg(("dmg_plus:80:if:own_active_energy_type:fire",),
                                   ["fire", "water", "water"]), 80)

    def test_energy_at_least_condition(self):
        self.assertEqual(self._dmg(("dmg_plus:100:if:own_active_energy_at_least:2",),
                                   ["fire"]), 0)
        self.assertEqual(self._dmg(("dmg_plus:100:if:own_active_energy_at_least:2",),
                                   ["fire", "water"]), 100)

    def test_unknown_condition_still_raises(self):
        """未知条件必须抛错（禁止静默 False）"""
        b = _arena()
        _equip(b, ("dmg_plus:80:if:nosuchcond",), owner=0, damage=0)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertFalse(r["ok"])
        self.assertIn("未知条件", str(r.get("error")))


class TestFourthBatchConditions(unittest.TestCase):
    """第四批条件（2026-10-26）：全部为「当下局面可直读」的条件

    覆盖 `opp_active_is_ex` / `opp_active_is_evolution` / `opp_active_has_damage` /
    `own_active_has_damage` / `opp_active_status:<s>` / `opp_active_type:<c>` /
    `opp_bench_at_least:<n>` / `own_all_energy_at_least:<n>`。
    每条都同时验「命中」与「不命中」，避免只测单边导致条件恒真/恒假。
    """

    def _run(self, cond, setup):
        b = _arena()
        _equip(b, (f"dmg_plus:50:if:{cond}",), owner=0, damage=0)
        setup(b)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        return b.players[1].active.damage

    # --- 对手战斗位是 ex / 进化 ---
    def test_opp_active_is_ex(self):
        self.assertEqual(self._run("opp_active_is_ex", lambda b: None), 0)
        got = self._run("opp_active_is_ex", lambda b: setattr(
            b.players[1], "active",
            make_pokemon(PokemonDef("ExP", hp=200, ptype="colorless", is_ex=True))))
        self.assertEqual(got, 50)

    def test_opp_active_is_evolution(self):
        self.assertEqual(self._run("opp_active_is_evolution", lambda b: None), 0)
        got = self._run("opp_active_is_evolution", lambda b: setattr(
            b.players[1], "active",
            make_pokemon(PokemonDef("EvoP", hp=200, ptype="colorless", stage=1))))
        self.assertEqual(got, 50)

    # --- 伤害指示物（双方各一侧）---
    def test_opp_active_has_damage(self):
        self.assertEqual(self._run("opp_active_has_damage", lambda b: None), 0)
        got = self._run("opp_active_has_damage",
                        lambda b: setattr(b.players[1].active, "damage", 30))
        # 预置 30 + 条件加伤 50 = 80（攻击后对手回合开始的检查阶段不再叠加伤害）
        self.assertEqual(got, 80)

    def test_own_active_has_damage(self):
        self.assertEqual(self._run("own_active_has_damage", lambda b: None), 0)
        got = self._run("own_active_has_damage",
                        lambda b: setattr(b.players[0].active, "damage", 20))
        self.assertEqual(got, 50)

    # --- 特殊状态（参数化）---
    def test_opp_active_status_burn(self):
        self.assertEqual(self._run("opp_active_status:burn", lambda b: None), 0)
        got = self._run("opp_active_status:burn",
                        lambda b: b.players[1].active.status.add("burn"))
        # 条件加伤 50 + 对手回合开始时的灼伤结算 20 = 70（后者的存在本身即证明状态未被误改）
        self.assertEqual(got, 70)
        # 别的状态不算：中毒只留 10 点中毒结算，无 50 加伤
        self.assertEqual(self._run("opp_active_status:burn",
                                   lambda b: b.players[1].active.status.add("poison")), 10)

    def test_opp_active_status_paralyze_alias(self):
        """`paralyze` 条件对历史拼写 `paralyzed` 同样成立（兼容旧回放）"""
        got = self._run("opp_active_status:paralyze",
                        lambda b: b.players[1].active.status.add("paralyzed"))
        self.assertEqual(got, 50)

    def test_opp_active_status_param_validated(self):
        """状态参数必须是标准名（拼错要抛错，不能静默 False）"""
        b = _arena()
        _equip(b, ("dmg_plus:50:if:opp_active_status:burnt",), owner=0, damage=0)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertFalse(r["ok"])
        self.assertIn("未知特殊状态", str(r.get("error")))

    # --- 属性（参数化）---
    def test_opp_active_type(self):
        got = self._run("opp_active_type:psychic", lambda b: setattr(
            b.players[1], "active",
            make_pokemon(PokemonDef("PsyP", hp=200, ptype="psychic"))))
        self.assertEqual(got, 50)
        self.assertEqual(self._run("opp_active_type:psychic", lambda b: None), 0)

    # --- 计数下限（参数化）---
    def test_opp_bench_at_least(self):
        self.assertEqual(self._run("opp_bench_at_least:2", lambda b: _bench(b, 1, 1)), 0)
        self.assertEqual(self._run("opp_bench_at_least:2", lambda b: _bench(b, 1, 2)), 50)

    def test_own_all_energy_at_least(self):
        def add(b, n):
            for _ in range(n):
                b.players[0].active.add_energy("colorless")
        self.assertEqual(self._run("own_all_energy_at_least:3", lambda b: add(b, 2)), 0)
        self.assertEqual(self._run("own_all_energy_at_least:3", lambda b: add(b, 3)), 50)

    def test_conditions_listed_in_vocab(self):
        """取值域必须把新增条件暴露给提示词层（避免文档/引擎漂移）"""
        from engine.battle import dmg_vocab
        v = dmg_vocab()
        for c in ("opp_active_is_ex", "opp_active_is_evolution",
                  "opp_active_has_damage", "own_active_has_damage"):
            self.assertIn(c, v["conditions"])
        self.assertTrue(any(x.startswith("opp_active_type:") for x in v["condition_params"]))


class TestStaticMods(unittest.TestCase):
    """S1 静态修正层（2026-10-26）：常驻减伤 / 无弱点 / 无撤退 / 加 HP

    与 `shield:*`（时限制、至该玩家下个回合开始）**必须区分开**：
    卡面「During your opponent's next turn, …」走 shield；不带时限的常驻写法走 passive。
    """

    def _battle(self, defender, key=()):
        b = _arena()
        b.players[1].active = make_pokemon(defender)
        b.players[0].active = make_pokemon(PokemonDef(
            "A", hp=200, ptype="lightning",
            attacks=(AttackDef("Hit", cost=0, damage=100, effects=key),)))
        return b

    def test_passive_dmg_reduce(self):
        b = self._battle(PokemonDef("D", hp=200, ptype="colorless"))
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.damage, 100)     # 无修正

        b2 = self._battle(PokemonDef("D2", hp=200, ptype="colorless",
                                     abilities=(AbilityDef("Armor", "passive:dmg_reduce:30"),)))
        r = b2.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b2.players[1].active.damage, 70)

    def test_passive_dmg_reduce_after_weakness(self):
        """卡面写 after applying Weakness and Resistance → 先 ×2 再减"""
        b = self._battle(PokemonDef("D3", hp=200, ptype="grass",
                                    weakness="lightning",
                                    abilities=(AbilityDef("Armor", "passive:dmg_reduce:30"),)))
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.damage, 200 - 30)  # 100×2 − 30

    def test_passive_no_weakness(self):
        b = self._battle(PokemonDef("D4", hp=200, ptype="grass",
                                    weakness="lightning",
                                    abilities=(AbilityDef("Hollow", "passive:no_weakness"),)))
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.damage, 100)        # 弱点不生效

    def test_passive_no_retreat(self):
        b = _arena()
        me = b.players[0].active = make_pokemon(PokemonDef(
            "R", hp=100, ptype="colorless", retreat=3,
            abilities=(AbilityDef("Float", "passive:no_retreat"),)))
        _bench(b, 0, 1)
        self.assertTrue(any(x["verb"] == "retreat" for x in b.legal_actions(0)),
                        "撤退费用应视为 0，无需能量即可撤退")
        r = b.step({"verb": "retreat", "bench": 0})
        self.assertTrue(r["ok"], r.get("error"))

    def test_passive_hp_plus(self):
        fp = make_pokemon(PokemonDef("H", hp=100, ptype="colorless",
                                     abilities=(AbilityDef("Tough", "passive:hp_plus:40"),)))
        self.assertEqual(fp.max_hp, 140)
        fp.damage = 130
        self.assertFalse(fp.is_ko)
        fp.damage = 140
        self.assertTrue(fp.is_ko)

    def test_static_mods_unknown_passive_is_ignored(self):
        """未知 passive 不得被当成任何一种修正（宁可无效，不可乱套）"""
        from engine.battle import _static_mods
        fp = make_pokemon(PokemonDef("U", hp=100, ptype="colorless",
                                     abilities=(AbilityDef("X", "passive:whatever:9"),)))
        m = _static_mods(fp)
        self.assertEqual(m["dmg_reduce"], 0)
        self.assertFalse(m["no_weakness"])
        self.assertFalse(m["no_retreat"])


class TestToolAttachment(unittest.TestCase):
    """S2 道具子系统（2026-10-26）：附着 / 每只 1 张 / 修正生效 / 合法动作

    道具是**训练家卡**，但用后不弃置（留在持有者身上），与普通物品的关键差别就在这里。
    """

    def _battle_with_tool(self, tag, name="Cape"):
        b = _arena()
        b.players[0].active = make_pokemon(PokemonDef("Holder", hp=100,
                                                      ptype="colorless"))
        b.players[0].hand.append(
            TrainerRef(TrainerDef(tag, kind="tool", name=name)))
        return b

    def test_attach_tool_sets_field_and_keeps_card(self):
        b = self._battle_with_tool("tool:hp_plus:100", "Hero's Cape")
        r = b.step({"verb": "trainer", "hand": 0, "target": 0})
        self.assertTrue(r["ok"], r.get("error"))
        fp = b.players[0].active
        self.assertEqual(fp.tool, "tool:hp_plus:100")
        self.assertEqual(fp.tool_name, "Hero's Cape")
        self.assertEqual(fp.max_hp, 200, "道具 +HP 必须叠加到上限")
        self.assertEqual(len(b.players[0].hand), 0, "道具应离开手牌")
        self.assertFalse(any(isinstance(c, TrainerRef)
                             for c in b.players[0].discard_refs),
                         "道具不得进弃牌堆（它附着在场上）")

    def test_one_tool_per_pokemon(self):
        b = self._battle_with_tool("tool:hp_plus:100")
        self.assertTrue(b.step({"verb": "trainer", "hand": 0, "target": 0})["ok"])
        b.players[0].hand.append(
            TrainerRef(TrainerDef("tool:hp_plus:70", kind="tool", name="W2")))
        r = b.step({"verb": "trainer", "hand": 0, "target": 0})
        self.assertFalse(r["ok"])
        self.assertIn("已附着道具", str(r.get("error")))

    def test_retreat_less_applies(self):
        b = _arena()
        b.players[0].active = make_pokemon(PokemonDef(
            "Heavy", hp=100, ptype="colorless", retreat=3))
        _bench(b, 0, 1)          # 撤退必须有后备可换（否则 legal 里没有 retreat）
        b.players[0].hand.append(
            TrainerRef(TrainerDef("tool:retreat_less:2", kind="tool",
                                  name="Air Balloon")))
        self.assertFalse(any(x["verb"] == "retreat"
                             for x in b.legal_actions(0)), "3 费时无能量不能撤退")
        self.assertTrue(b.step({"verb": "trainer", "hand": 0, "target": 0})["ok"])
        b.players[0].active.add_energy("colorless")     # 剩 1 费 → 够
        self.assertTrue(any(x["verb"] == "retreat" for x in b.legal_actions(0)))
        r = b.step({"verb": "retreat", "bench": 0})
        self.assertTrue(r["ok"], r.get("error"))

    def test_legals_list_only_pokemon_without_tool(self):
        b = self._battle_with_tool("tool:hp_plus:100")
        _bench(b, 0, 1)
        b.players[0].bench[0].tool = "tool:hp_plus:70"   # 后备已有道具
        legal = [x for x in b.legal_actions(0) if x["verb"] == "trainer"]
        self.assertEqual([x.get("target") for x in legal], [0],
                         "已有道具的位置不应再出现在可选目标里")

    def test_tool_dmg_reduce_stacks_with_passive(self):
        """道具减伤与特性减伤可叠加（都是「受招式伤害 −N」）"""
        from engine.battle import _static_mods
        fp = make_pokemon(PokemonDef("D", hp=100, ptype="colorless",
                                     abilities=(AbilityDef("A", "passive:dmg_reduce:20"),)))
        fp.tool = "tool:dmg_reduce:30"
        self.assertEqual(_static_mods(fp)["dmg_reduce"], 50)


class TestToolTriggers(unittest.TestCase):
    """S2 第二阶段：触发式道具（2026-10-26）

    覆盖 `tool:on_damaged:draw:N` / `tool:on_damaged:counter:N[:discard]` /
    `tool:survive:M`。三条都要求"受伤前/受伤后"的时序正确，否则会静默错算。
    """

    def _setup(self, tool_tag, def_hp=200, atk_damage=30, tool_name="T"):
        b = _arena()
        b.players[0].active = make_pokemon(PokemonDef(
            "Atk", hp=200, ptype="colorless",
            attacks=(AttackDef("Hit", cost=0, damage=atk_damage, effects=()),)))
        b.players[1].active = make_pokemon(PokemonDef(
            "Def", hp=def_hp, ptype="colorless"))
        b.players[1].active.tool = tool_tag
        b.players[1].active.tool_name = tool_name
        # 双方牌库充足，避免抽牌/磨牌触发判负
        b.players[1].deck = [EnergyRef() for _ in range(20)]
        return b

    def test_on_damaged_draw(self):
        b = self._setup("tool:on_damaged:draw:2")
        before = len(b.players[1].hand)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        # 攻击结束会切到对手回合 → 对手回合开始抽 1 张，所以增量 = 1 + 2
        self.assertEqual(len(b.players[1].hand), before + 3,
                         "持有者应在被伤害后抽 2 张（另 1 张是回合开始抽牌）")
        self.assertEqual(b.players[1].active.tool, "tool:on_damaged:draw:2",
                         "抽牌型道具**不弃置**")

    def test_on_damaged_not_triggered_without_damage(self):
        """被打 0 点（伤害被完全挡下）不算"被伤害" → 不触发"""
        b = self._setup("tool:on_damaged:draw:2", atk_damage=0)
        b.players[0].active = make_pokemon(PokemonDef(
            "Atk0", hp=200, ptype="colorless",
            attacks=(AttackDef("Tap", cost=0, damage=0, effects=()),)))
        before = len(b.players[1].hand)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        # 只有对手回合开始的抽 1 张，没有道具的 +2
        self.assertEqual(len(b.players[1].hand), before + 1)

    def test_on_damaged_counter_and_discard(self):
        b = self._setup("tool:on_damaged:counter:120:discard", atk_damage=10)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[0].active.damage, 120, "攻击方应受 120 点反制伤害")
        self.assertIsNone(b.players[1].active.tool, "带 :discard 的道具触发后应弃置")

    def test_tool_survive_prevents_ko_when_full_hp(self):
        b = self._setup("tool:survive:10", def_hp=100, atk_damage=150)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        defp = b.players[1].active
        self.assertFalse(defp.is_ko, "满 HP 时 Survival Brace 应免击倒")
        self.assertEqual(defp.hp_remaining, 10)
        self.assertIsNone(defp.tool, "免击倒后道具应弃置")

    def test_tool_survive_not_when_not_full_hp(self):
        """非满 HP 时不该救（卡面前提是 has full HP）"""
        b = self._setup("tool:survive:10", def_hp=100, atk_damage=150)
        b.players[1].active.damage = 30       # 先掉 30，非满 HP
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        # 击倒已发生：active 被清空 + 攻击方拿到奖赏卡（免击倒时这两条都不成立）
        self.assertIsNone(b.players[1].active)
        self.assertLess(b.players[0].prizes_left, 10)

    def test_unknown_tool_hook_is_inert(self):
        """未识别的道具形态不得产生任何副作用（宁可无效，不可乱套）"""
        b = self._setup("tool:on_damaged:teleport:5")
        before = len(b.players[1].hand)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(b.players[1].hand), before + 1)   # 仅回合开始抽牌


if __name__ == "__main__":
    unittest.main()
