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
from engine.battle import Battle, _retreat_cost, _eval_counter
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

    # ---- 2026-10-27：新增公开计数源 ----
    def test_plus_per_own_basic(self):
        """「Basic Pokémon in play」= 场上（含战斗位）stage==0 的数量"""
        b = _arena()
        _bench(b, 0, 2)                     # active + 2 bench = 3 basic
        b.players[0].bench.append(make_pokemon(PokemonDef(
            "Stage1", hp=100, ptype="colorless", stage=1)))
        _equip(b, ("dmg_plus:10:per:own_basic",))
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 10 + 30,
                         "进化型不得计入 Basic 数")

    def test_plus_per_own_evolution(self):
        b = _arena()
        for name, stage in (("Stage1", 1), ("Stage2", 2)):
            b.players[0].bench.append(make_pokemon(PokemonDef(
                name, hp=100, ptype="colorless", stage=stage)))
        _equip(b, ("dmg_plus:40:per:own_evolution",))
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 10 + 80,
                         "战斗位是基础型，不应计入 Evolution 数")

    def test_plus_per_opp_hand(self):
        b = _arena()
        b.players[1].hand = [EnergyRef() for _ in range(4)]
        _equip(b, ("dmg_plus:30:per:opp_hand",))
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 10 + 120)

    def test_plus_per_opp_field_counters(self):
        """对手全场指示物 = 战斗位 + 后备（按 10 点/个）"""
        b = _arena()
        _bench(b, 1, 3)
        b.players[1].active.damage = 20
        b.players[1].bench[0].damage = 30
        b.players[1].bench[1].damage = 10
        _equip(b, ("dmg_plus:10:per:opp_field_counters",))
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 20 + 10 + 60)

    def test_plus_per_opp_active_status_count(self):
        """多特殊状态并存时按**个数**计（不是"有无"）

        用 sleep/confuse 而非 poison/burn：后者在回合结束的 checkup 里会追加
        伤害，干扰对加伤数值的断言。
        """
        b = _arena()
        b.players[1].active.status.update({"sleep", "confuse"})
        _equip(b, ("dmg_plus:50:per:opp_active_status_count",))
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 10 + 100)

    def test_plus_per_own_field_tools(self):
        b = _arena()
        _bench(b, 0, 2)
        holder = _equip(b, ("dmg_plus:30:per:own_field_tools",))
        holder.tool = "tool:test"
        b.players[0].bench[0].tool = "tool:test"
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 10 + 60)

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

    def test_ignore_resistance(self):
        b = _arena()
        b.players[1].active = make_pokemon(
            PokemonDef("ResistGuy", hp=200, ptype="water",
                       resistance="colorless", attacks=()))
        _equip(b, ("ignore_resistance",))
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 10,
                         "忽略抗性后不应 −30")

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
        """「prevent all damage **from and effects of** attacks」→ 连效果一起挡"""
        b, _ = self._hit(damage=80, effects=("poison",))
        b.players[1].active.shield_prevent_all = True
        b.players[1].active.shield_prevent_effects = True
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.status, set(), "免疫应挡住效果")

    def test_shield_prevent_damage_only_lets_effects_through(self):
        """只写「prevent all damage …」的护盾**不挡效果**（2026-10-28 语义拆分）

        把两者合成一个标志会静默放大防护范围：Metapod「Harden」只挡伤害，
        原先却连中毒/睡眠一起挡掉。
        """
        b, _ = self._hit(damage=80, effects=("poison",))
        b.players[1].active.shield_prevent_all = True
        b.step({"verb": "attack", "attack": 0})
        # 招式伤害被挡下；10 来自对手回合开始时的中毒检查
        self.assertEqual(b.players[1].active.damage, 10, "伤害应被挡下")
        self.assertEqual(b.players[1].active.status, {"poison"},
                         "只挡伤害的护盾不应挡住效果")

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

    def _filter_battle(self, tag, cards):
        b = _arena()
        ab = AbilityDef("Fetch", name_zh="检索", tag=tag, location="field")
        b.players[0].active = make_pokemon(
            PokemonDef("Fetcher", hp=200, ptype="colorless", attacks=(),
                       abilities=(ab,)))
        b.players[0].deck = list(cards) + [EnergyRef(E_FIRE) for _ in range(4)]
        b.players[0].hand = []
        b._test_tag = tag
        return b

    def test_search_pokemon_type_filter(self):
        fire = PokeRef(PokemonDef("FireGuy", hp=60, ptype="fire", attacks=()))
        water = PokeRef(PokemonDef("WaterGuy", hp=60, ptype="water", attacks=()))
        b = self._filter_battle("search_pokemon:1:type:fire", [water, fire])
        r = self._use(b)
        self.assertTrue(r["ok"], r.get("error"))
        names = [c.pdef.name for c in b.players[0].hand if isinstance(c, PokeRef)]
        self.assertEqual(names, ["FireGuy"])

    def test_search_pokemon_stage_and_hp_filter(self):
        basic = PokeRef(PokemonDef("SmallBasic", hp=60, ptype="fire", attacks=()))
        evolved = PokeRef(PokemonDef("BigStage1", hp=120, ptype="fire", attacks=(),
                                     stage=1))
        b = self._filter_battle("search_pokemon:2:type:fire:hp:60", [evolved, basic])
        r = self._use(b)
        self.assertTrue(r["ok"], r.get("error"))
        names = [c.pdef.name for c in b.players[0].hand if isinstance(c, PokeRef)]
        self.assertEqual(names, ["SmallBasic"], "HP 60 筛选不得带出 120 HP 的卡")

    def test_filtered_search_not_offered_without_match(self):
        water = PokeRef(PokemonDef("WaterGuy", hp=60, ptype="water", attacks=()))
        b = self._filter_battle("search_pokemon:1:type:fire", [water])
        names = [a.get("name") for a in b.legal_actions()]
        self.assertNotIn("Fetch", names, "牌库无匹配属性 → 不得列为合法动作")

    def test_search_resistance_filter(self):
        """B2 剩余：`{F} Resistance` 筛选（Fletchling）"""
        resist = PokeRef(PokemonDef("Resistor", hp=60, ptype="colorless",
                                    resistance="fighting", attacks=()))
        other = PokeRef(PokemonDef("Other", hp=60, ptype="colorless",
                                   attacks=()))
        b = self._filter_battle("search_pokemon:2:resistance:fighting",
                                [other, resist])
        r = self._use(b)
        self.assertTrue(r["ok"], r.get("error"))
        names = [c.pdef.name for c in b.players[0].hand if isinstance(c, PokeRef)]
        self.assertEqual(names, ["Resistor"])

    def test_search_count_from_counter(self):
        """B2 剩余：`search_any:N:per:<counter>`（Scrafty「至多后备数」）"""
        b = self._filter_battle("search_any:1:per:own_bench", [])
        # `search_any` 的引擎约定是「任意**非能量**卡」（见 `_kind_matches`）
        b.players[0].deck = [
            PokeRef(PokemonDef(f"Any{i}", hp=60, ptype="colorless", attacks=()))
            for i in range(6)]
        _bench(b, 0, 2)
        r = self._use(b)
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(b.players[0].hand), 2, "后备 2 只 → 至多检索 2 张")

    def test_search_energy_typed(self):
        """B2 剩余：`search_energy:N:type:<c>`（Aromatisse / Firebreather）"""
        from engine.cards import EnergyDef
        b = self._filter_battle("search_energy:2:type:water", [])
        water = EnergyDef("water")
        b.players[0].deck = ([EnergyRef(water) for _ in range(2)]
                             + [EnergyRef(E_FIRE) for _ in range(3)])
        r = self._use(b)
        self.assertTrue(r["ok"], r.get("error"))
        got = [c.edef.type for c in b.players[0].hand if isinstance(c, EnergyRef)]
        self.assertEqual(got, ["water", "water"])


class TestSelfStatusEffect(unittest.TestCase):
    """`status_self:<s>` —— 状态作用于**出招者自己**（2026-10-28）"""

    def test_attack_status_self_targets_attacker(self):
        b = _arena()
        me = _equip(b, ("status_self:confuse",))
        foe = b.players[1].active
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertIn("confuse", me.status)
        self.assertNotIn("confuse", foe.status, "不得给对手上状态")

    def test_ability_status_self_targets_holder(self):
        b = _arena()
        ab = AbilityDef("SelfPoison", name_zh="自毒", tag="status_self:poison",
                        location="field")
        b.players[0].active = make_pokemon(
            PokemonDef("Holder", hp=200, ptype="colorless", attacks=(),
                       abilities=(ab,)))
        b.players[0].deck = [EnergyRef(E_FIRE) for _ in range(5)]
        r = b.step({"verb": "ability", "name": "SelfPoison", "pos": 0,
                    "tag": "status_self:poison"})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertIn("poison", b.players[0].active.status)
        self.assertNotIn("poison", b.players[1].active.status)


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

    def test_public_counters_listed_in_vocab(self):
        """新增计数源同样必须进入提示词取值域"""
        from engine.battle import dmg_vocab
        v = dmg_vocab()
        for c in ("own_basic", "own_evolution", "opp_hand",
                  "opp_field_counters", "opp_active_status_count",
                  "own_field_tools"):
            self.assertIn(c, v["counters"])


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
        """道具减伤与特性减伤可叠加（都是「受招式伤害 −N」）

        ⚠ 两者分属不同层：`_static_mods` 只算**与攻击方无关**的特性减伤；
        道具减伤走 `_tool_reduce_vs`（可能带 from/holder 限定）。因此这里分别断言，
        并在真实攻击里验总减伤 = 20 + 30。
        """
        from engine.battle import _static_mods
        b = _arena()
        b.players[0].active = make_pokemon(PokemonDef(
            "Atk", hp=200, ptype="colorless",
            attacks=(AttackDef("Hit", cost=0, damage=100, effects=()),)))
        d = make_pokemon(PokemonDef("D", hp=200, ptype="colorless",
                                    abilities=(AbilityDef("A", "passive:dmg_reduce:20"),)))
        d.tool = "tool:dmg_reduce:30"
        b.players[1].active = d
        self.assertEqual(_static_mods(d)["dmg_reduce"], 20)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(d.damage, 50, "道具 30 + 特性 20 应叠加为 50")


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


class TestToolQualifiers(unittest.TestCase):
    """道具限定过滤（2026-10-27）：减伤只对特定攻击方生效、反制只对特定持有者

    这张表最容易出错的地方是"限定条件被忽略 → 全类型生效"（静默加强），
    所以每条都同时验命中与不命中。
    """

    def _battle(self, tool_tag, atk_type="colorless", atk_ability=False,
                def_type="colorless"):
        b = _arena()
        atk = PokemonDef("Atk", hp=200, ptype=atk_type,
                         attacks=(AttackDef("Hit", cost=0, damage=100, effects=()),),
                         abilities=((AbilityDef("Ab", "x"),) if atk_ability else ()))
        b.players[0].active = make_pokemon(atk)
        d = make_pokemon(PokemonDef("Def", hp=200, ptype=def_type))
        d.tool = tool_tag
        b.players[1].active = d
        return b, d

    def test_from_type_list(self):
        tag = "tool:dmg_reduce:50:from:type:grass,fire,water,lightning"
        b, d = self._battle(tag, atk_type="fire")
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(d.damage, 50, "攻击方是 fire → 应减 50")
        b2, d2 = self._battle(tag, atk_type="psychic")
        b2.step({"verb": "attack", "attack": 0})
        self.assertEqual(d2.damage, 100, "攻击方是 psychic（不在集合）→ 不减伤")

    def test_from_ability(self):
        tag = "tool:dmg_reduce:30:from:ability"
        b, d = self._battle(tag, atk_ability=True)
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(d.damage, 70, "攻击方有特性 → 减 30")
        b2, d2 = self._battle(tag, atk_ability=False)
        b2.step({"verb": "attack", "attack": 0})
        self.assertEqual(d2.damage, 100, "攻击方无特性 → 不减伤")

    def test_holder_type_required(self):
        tag = "tool:dmg_reduce:50:from:type:grass:holder:type:dragon"
        b, d = self._battle(tag, atk_type="grass", def_type="colorless")
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(d.damage, 100, "持有者不是 dragon → 限定额不成立")
        b2, d2 = self._battle(tag, atk_type="grass", def_type="dragon")
        b2.step({"verb": "attack", "attack": 0})
        self.assertEqual(d2.damage, 50)

    def test_once_tool_discarded_after_use(self):
        tag = "tool:dmg_reduce:60:from:type:psychic:discard"
        b, d = self._battle(tag, atk_type="psychic")
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(d.damage, 40)
        self.assertIsNone(d.tool, "浆果类道具生效后应弃置")

    def test_holder_qualified_counter(self):
        tag = "tool:on_damaged:counter:40:holder:type:darkness"
        b, d = self._battle(tag, atk_type="water", def_type="darkness")
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[0].active.damage, 40, "持有者符合 → 反制 40")
        b2, d2 = self._battle(tag, atk_type="water", def_type="psychic")
        b2.step({"verb": "attack", "attack": 0})
        self.assertEqual(b2.players[0].active.damage, 0, "持有者不符合 → 不反制")

    def test_on_damaged_move_energy(self):
        """Handheld Fan：攻击方 1 个能量被移到**攻击方自己的后备**"""
        b = _arena()
        b.players[0].active = make_pokemon(PokemonDef(
            "Atk", hp=200, ptype="colorless",
            attacks=(AttackDef("Hit", cost=0, damage=30, effects=()),)))
        b.players[0].active.add_energy("colorless")
        bench = _bench(b, 0, 1)[0]
        d = make_pokemon(PokemonDef("Def", hp=200, ptype="colorless"))
        d.tool = "tool:on_damaged:move_energy:1"
        b.players[1].active = d
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[0].active.total_energy(), 0, "攻击方能量应被移走")
        self.assertEqual(bench.total_energy(), 1, "能量应进攻击方后备")

    def test_move_energy_without_bench_is_noop(self):
        b = _arena()
        b.players[0].active = make_pokemon(PokemonDef(
            "Atk", hp=200, ptype="colorless",
            attacks=(AttackDef("Hit", cost=0, damage=30, effects=()),)))
        b.players[0].active.add_energy("colorless")
        d = make_pokemon(PokemonDef("Def", hp=200, ptype="colorless"))
        d.tool = "tool:on_damaged:move_energy:1"
        b.players[1].active = d
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[0].active.total_energy(), 1, "无后备时能量不动")

    def test_unknown_qualifier_is_inert(self):
        """未知限定形态一律不生效（宁可不减伤，不可乱减伤）"""
        b, d = self._battle("tool:dmg_reduce:50:from:faction:rocket")
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(d.damage, 100)


class TestStadium(unittest.TestCase):
    """S3 场地（2026-10-27）：全局唯一 / 顶替 / 全场静态修正

    场地是**双方共用**的一张卡：不打在某只宝可梦身上，效果作用于"全场符合条件的"。
    """

    def _battle(self, tag, name="S"):
        b = _arena()
        b.players[0].hand.append(
            TrainerRef(TrainerDef(tag, kind="stadium", name=name)))
        return b

    def test_play_sets_stadium_and_card_leaves_hand(self):
        b = self._battle("stadium:hp_plus:30:stage:0")
        r = b.step({"verb": "trainer", "hand": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.stadium["tag"], "stadium:hp_plus:30:stage:0")
        self.assertEqual(len(b.players[0].hand), 0)
        self.assertFalse(any(isinstance(c, TrainerRef)
                             for c in b.players[0].discard_refs),
                         "场地道具语义：打出后留在场上，不进弃牌堆")

    def test_hp_plus_applies_to_basic_both_sides(self):
        b = self._battle("stadium:hp_plus:30:stage:0")
        basic = make_pokemon(PokemonDef("B", hp=100, ptype="colorless"))
        evo = make_pokemon(PokemonDef("E", hp=100, ptype="colorless", stage=1))
        b.players[0].bench = [basic, evo]
        b.step({"verb": "trainer", "hand": 0})
        self.assertEqual(basic.max_hp, 130, "基础宝可梦 +30")
        self.assertEqual(evo.max_hp, 100, "一阶不受影响")
        # 对手侧同样生效
        opp_basic = b.players[1].active
        opp_basic.pdef = PokemonDef("OB", hp=100, ptype="colorless")
        b._refresh_stadium_hp()
        self.assertEqual(opp_basic.max_hp, 130, "场地对双方都生效")

    def test_gravity_mountain_minus_hp(self):
        b = self._battle("stadium:hp_plus:-30:stage:2")
        st2 = make_pokemon(PokemonDef("S2", hp=150, ptype="colorless", stage=2))
        b.players[0].active = st2
        b.step({"verb": "trainer", "hand": 0})
        self.assertEqual(st2.max_hp, 120, "二阶 −30")

    def test_stadium_damage_reduce_by_type(self):
        b = self._battle("stadium:dmg_reduce:30:type:metal")
        b.players[0].active = make_pokemon(PokemonDef(
            "Atk", hp=200, ptype="colorless",
            attacks=(AttackDef("Hit", cost=0, damage=100, effects=()),)))
        metal = make_pokemon(PokemonDef("M", hp=200, ptype="metal"))
        b.players[1].active = metal
        b.step({"verb": "trainer", "hand": 0})
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(metal.damage, 70, "钢系受招式伤害 −30")

    def test_stadium_damage_reduce_other_type_unaffected(self):
        b = self._battle("stadium:dmg_reduce:30:type:metal")
        b.players[0].active = make_pokemon(PokemonDef(
            "Atk", hp=200, ptype="colorless",
            attacks=(AttackDef("Hit", cost=0, damage=100, effects=()),)))
        grass = make_pokemon(PokemonDef("G", hp=200, ptype="grass"))
        b.players[1].active = grass
        b.step({"verb": "trainer", "hand": 0})
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(grass.damage, 100, "非钢系不减伤")

    def test_new_stadium_replaces_old(self):
        b = self._battle("stadium:hp_plus:30:stage:0")
        b.step({"verb": "trainer", "hand": 0})
        b.players[0].hand.append(
            TrainerRef(TrainerDef("stadium:hp_plus:-30:stage:2",
                                  kind="stadium", name="Gravity")))
        r = b.step({"verb": "trainer", "hand": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.stadium["tag"], "stadium:hp_plus:-30:stage:2",
                         "新场地必须顶替旧场地（全场只能有 1 张）")

    def test_stadium_use_top_deck_once_per_turn(self):
        """Academy at Night：把手牌 1 张放到牌库顶，每回合只能 1 次"""
        b = self._battle("stadium:use:top_deck", "Academy")
        b.players[0].hand.append(EnergyRef())
        b.step({"verb": "trainer", "hand": 0})
        hand_before = len(b.players[0].hand)
        deck_top_before = b.players[0].deck[-1]
        self.assertTrue(any(x["verb"] == "stadium_use"
                            for x in b.legal_actions(0)))
        r = b.step({"verb": "stadium_use", "hand": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(b.players[0].hand), hand_before - 1)
        self.assertIsNot(b.players[0].deck[-1], deck_top_before,
                         "手牌应被放到牌库顶（列表末尾）")
        # 同回合第二次不可用
        self.assertFalse(any(x["verb"] == "stadium_use"
                             for x in b.legal_actions(0)))

    def test_stadium_use_recover_energy(self):
        """Levincia：从弃牌堆取回 ≤2 张基本 {L} 能量"""
        b = self._battle("stadium:use:recover_energy:2:type:lightning", "Levincia")
        b.step({"verb": "trainer", "hand": 0})
        b.players[0].discard_refs.extend([EnergyRef(EnergyDef("lightning")),
                                          EnergyRef(EnergyDef("lightning")),
                                          EnergyRef(EnergyDef("fire"))])
        before = len(b.players[0].hand)
        r = b.step({"verb": "stadium_use"})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(b.players[0].hand), before + 2, "只取 2 张闪电能量")
        left = [c for c in b.players[0].discard_refs if isinstance(c, EnergyRef)]
        self.assertEqual([c.edef.type for c in left], ["fire"],
                         "非闪电能量不应被取走")

    def test_stadium_use_heal_all_requires_supporter(self):
        """Community Center：必须本回合用过支援者才能回复"""
        b = self._battle("stadium:use:heal_all:10:require:supporter", "Center")
        b.players[0].active.damage = 40
        b.step({"verb": "trainer", "hand": 0})
        self.assertFalse(any(x["verb"] == "stadium_use"
                             for x in b.legal_actions(0)),
                         "未使用支援者时不应出现场地使用动作")
        b.players[0].supporter_used = 1
        r = b.step({"verb": "stadium_use"})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[0].active.damage, 30)

    def test_stadium_use_resets_next_turn(self):
        b = self._battle("stadium:use:top_deck", "Academy")
        b.players[0].hand.append(EnergyRef())
        b.step({"verb": "trainer", "hand": 0})
        b.step({"verb": "stadium_use", "hand": 0})
        b.players[0].hand.append(EnergyRef())
        # 结束回合 → 对手回合 → 再回到我方：标志应重置
        b.step({"verb": "pass"})
        b.step({"verb": "pass"})
        self.assertTrue(any(x["verb"] == "stadium_use"
                            for x in b.legal_actions(0)),
                        "新回合应可再次使用")

    def test_stadium_poison_extra_at_checkup(self):
        """Perilous Jungle：checkup 时中毒的非暗属性额外受 20（追加，不是替换）"""
        b = self._battle("stadium:poison_extra:20:type:darkness", "Jungle")
        b.step({"verb": "trainer", "hand": 0})
        fp = b.players[1].active = make_pokemon(
            PokemonDef("Poisoned", hp=200, ptype="grass"))
        fp.status.add("poison")
        self.assertEqual(fp.damage, 0)
        b.current = 1            # checkup 结算的是「回合归属方」的战斗位
        b._begin_turn()
        # 常规中毒 10 + 场地追加 20
        self.assertEqual(fp.damage, 30)

    def test_stadium_poison_extra_exempts_type(self):
        b = self._battle("stadium:poison_extra:20:type:darkness", "Jungle")
        b.step({"verb": "trainer", "hand": 0})
        dark = b.players[1].active = make_pokemon(
            PokemonDef("Dark", hp=200, ptype="darkness"))
        dark.status.add("poison")
        b.current = 1
        b._begin_turn()
        self.assertEqual(dark.damage, 10, "暗属性只受常规毒伤，不吃追加")

    def test_stadium_on_bench_basic(self):
        """Risky Ruins：基础（非暗）宝可梦上场即受 20"""
        b = self._battle("stadium:on_bench_basic:20:type:darkness", "Ruins")
        b.step({"verb": "trainer", "hand": 0})
        # 手牌放一张基础宝可梦到后备
        basic = PokemonDef("Fresh", hp=100, ptype="water", stage=0)
        b.players[0].hand.append(PokeRef(basic))
        r = b.step({"verb": "play", "index": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[0].bench[-1].damage, 20, "上场应放 2 个指示物")

    def test_stadium_on_bench_dark_exempt(self):
        b = self._battle("stadium:on_bench_basic:20:type:darkness", "Ruins")
        b.step({"verb": "trainer", "hand": 0})
        dark_basic = PokemonDef("DarkFresh", hp=100, ptype="darkness", stage=0)
        b.players[0].hand.append(PokeRef(dark_basic))
        r = b.step({"verb": "play", "index": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[0].bench[-1].damage, 0, "暗属性豁免")

    def test_unknown_stadium_tag_is_inert(self):
        """未实现的场地形态不得产生任何修正（宁可无效）"""
        b = self._battle("stadium:search_basic:1")
        basic = make_pokemon(PokemonDef("B2", hp=100, ptype="colorless"))
        b.players[0].bench = [basic]
        b.step({"verb": "trainer", "hand": 0})
        self.assertEqual(basic.max_hp, 100)

    # ---- S3 第四步：需要代价/目标的场地（2026-10-27） ----
    def test_stadium_use_switch_typed_only_same_type(self):
        """Surfing Beach：只能换上**同属性**后备，且不消耗撤退能量"""
        b = self._battle("stadium:use:switch_typed:type:water", "Beach")
        b.players[0].active = make_pokemon(
            PokemonDef("WActive", hp=100, ptype="water"))
        w = make_pokemon(PokemonDef("WBench", hp=100, ptype="water"))
        p = make_pokemon(PokemonDef("PBench", hp=100, ptype="psychic"))
        b.players[0].bench = [w, p]
        b.step({"verb": "trainer", "hand": 0})
        acts = [a for a in b.legal_actions(0) if a["verb"] == "stadium_use"]
        self.assertEqual([a.get("bench") for a in acts], [0],
                         "只有同属性的后备可被换上")
        r = b.step({"verb": "stadium_use", "bench": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[0].active.name, "WBench")
        self.assertIn("WActive", [fp.name for fp in b.players[0].bench])

    def test_stadium_use_switch_typed_requires_water_active(self):
        """负向守卫：战斗位不是该属性时不可用（不能凭空换位）"""
        b = self._battle("stadium:use:switch_typed:type:water", "Beach")
        b.players[0].bench = [make_pokemon(
            PokemonDef("WBench", hp=100, ptype="water"))]
        b.step({"verb": "trainer", "hand": 0})
        self.assertFalse(any(a["verb"] == "stadium_use"
                             for a in b.legal_actions(0)))

    def test_stadium_use_draw_to_count_pays_energy(self):
        """Mystery Garden：弃 1 张手牌能量 → 抽到手牌数 = 该属性在场数"""
        b = self._battle("stadium:use:draw_to_count:1:type:psychic:"
                         "cost:discard_energy", "Garden")
        b.players[0].active = make_pokemon(
            PokemonDef("PsyActive", hp=100, ptype="psychic"))
        b.step({"verb": "trainer", "hand": 0})
        b.players[0].hand = [EnergyRef(), EnergyRef()]   # 手牌 2，在场 psychic 1
        self.assertFalse(any(a["verb"] == "stadium_use"
                             for a in b.legal_actions(0)),
                         "弃 1 后已无可抽空间 → 不应列为可用")
        b.players[0].bench = [make_pokemon(
            PokemonDef("PsyBench", hp=100, ptype="psychic"))]  # 在场 psychic 2
        self.assertTrue(any(a["verb"] == "stadium_use"
                            for a in b.legal_actions(0)))
        r = b.step({"verb": "stadium_use"})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(b.players[0].hand), 2, "弃 1 抽 1 后仍是 2 张")
        self.assertEqual(len(b.players[0].discard_refs), 1, "代价必须真的进弃牌堆")

    def test_stadium_use_draw_to_count_no_energy_not_available(self):
        """负向守卫：手牌无能量时无法支付代价"""
        b = self._battle("stadium:use:draw_to_count:1:type:psychic:"
                         "cost:discard_energy", "Garden")
        b.players[0].active = make_pokemon(
            PokemonDef("PsyActive", hp=100, ptype="psychic"))
        b.players[0].bench = [make_pokemon(
            PokemonDef("P2", hp=100, ptype="psychic"))]
        b.step({"verb": "trainer", "hand": 0})
        b.players[0].hand = [PokeRef(PokemonDef("Filler", hp=60,
                                               ptype="colorless"))]
        self.assertFalse(any(a["verb"] == "stadium_use"
                             for a in b.legal_actions(0)))

    def test_stadium_use_search_basic_bench_ends_turn(self):
        """Lumiose City：检索基础宝可梦直铺后备，**回合立刻结束**"""
        b = self._battle("stadium:use:search_basic_bench:1:end:1", "Lumiose")
        b.step({"verb": "trainer", "hand": 0})
        basic = PokemonDef("DeckBasic", hp=100, ptype="water", stage=0)
        b.players[0].deck.append(PokeRef(basic))
        r = b.step({"verb": "stadium_use"})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual([fp.name for fp in b.players[0].bench], ["DeckBasic"])
        self.assertEqual(b.current, 1, "检索铺场后本回合必须结束")

    def test_stadium_use_search_basic_bench_needs_room(self):
        """负向守卫：后备已满时不可用"""
        b = self._battle("stadium:use:search_basic_bench:1:end:1", "Lumiose")
        b.players[0].bench = [make_pokemon(
            PokemonDef(f"F{i}", hp=60, ptype="colorless")) for i in range(5)]
        b.step({"verb": "trainer", "hand": 0})
        b.players[0].deck.append(PokeRef(
            PokemonDef("DeckBasic", hp=100, ptype="water", stage=0)))
        self.assertFalse(any(a["verb"] == "stadium_use"
                             for a in b.legal_actions(0)))

    def test_stadium_use_search_by_name(self):
        """Spikemuth Gym：按名字检索（归一化子串匹配，忽略大小写/撇号）"""
        b = self._battle("stadium:use:search_name:1:name:marnie", "Gym")
        b.step({"verb": "trainer", "hand": 0})
        want = PokeRef(PokemonDef("Marnie\u2019s Impidimp", hp=60,
                                  ptype="darkness", stage=0))
        other = PokeRef(PokemonDef("Other Mon", hp=60, ptype="water", stage=0))
        b.players[0].deck.extend([other, want])
        b.players[0].hand = []
        r = b.step({"verb": "stadium_use"})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(b.players[0].hand), 1)
        self.assertEqual(b.players[0].hand[0].pdef.name, "Marnie\u2019s Impidimp",
                         "必须拿到名字匹配的那张，不是随便一张")

    def test_stadium_use_draw_requires_supporter_name(self):
        """Team Rocket's Factory：只认名字含 "Team Rocket" 的支援者

        两个独立对局（一回合只能打 1 张支援者，不能在同一局里连打两张验证）。
        """
        # ① 名字不符 → 不可用
        b = self._battle("stadium:use:draw:2:require:supporter_name:"
                         "name:teamrocket", "Factory")
        b.step({"verb": "trainer", "hand": 0})
        self.assertFalse(any(a["verb"] == "stadium_use"
                             for a in b.legal_actions(0)),
                         "未打出支援者时不应可用")
        b.players[0].hand.append(TrainerRef(
            TrainerDef("research:5", kind="supporter", name="Professor X")))
        b.step({"verb": "trainer", "hand": 0})
        self.assertEqual(b.players[0].supporter_name, "Professor X",
                         "打出的支援者卡名必须被记录（供场地上判定）")
        self.assertFalse(any(a["verb"] == "stadium_use"
                             for a in b.legal_actions(0)),
                         "名字不符的支援者不应满足条件")

        # ② 名字含 Team Rocket → 可用，抽 2
        b = self._battle("stadium:use:draw:2:require:supporter_name:"
                         "name:teamrocket", "Factory")
        b.step({"verb": "trainer", "hand": 0})
        b.players[0].hand.append(TrainerRef(
            TrainerDef("research:5", kind="supporter",
                       name="Team Rocket\u2019s Ariana")))
        b.step({"verb": "trainer", "hand": 0})
        self.assertEqual(b.players[0].supporter_name,
                         "Team Rocket\u2019s Ariana")
        hand_before = len(b.players[0].hand)
        deck_now = len(b.players[0].deck)
        r = b.step({"verb": "stadium_use"})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(b.players[0].hand), hand_before + 2)
        self.assertEqual(len(b.players[0].deck), deck_now - 2)

    def test_stadium_use_end_turn_does_not_leak_to_opponent(self):
        """回归守卫：场地结束回合时，日志归属必须是**使用者**而不是对手"""
        b = self._battle("stadium:use:search_basic_bench:1:end:1", "Lumiose")
        b.step({"verb": "trainer", "hand": 0})
        b.players[0].deck.append(PokeRef(
            PokemonDef("DeckBasic", hp=100, ptype="water", stage=0)))
        b.step({"verb": "stadium_use"})
        use_ev = [e for e in b.events if e.get("type") == "stadium_use"]
        self.assertEqual(len(use_ev), 1)
        self.assertEqual(use_ev[0]["player"], 0, "日志应记在动作发起方名下")
        self.assertEqual(b.current, 1)


class TestHealScope(unittest.TestCase):
    """回复「范围」语义（2026-10-27）

    历史缺陷：``heal:N`` 恒指**攻击方自身**，于是「回复己方全部」被压成「只回一只」。
    池内实测 5 处错映射（Leafeon ex / Ethan's Ho-Oh ex / Leavanny / Meloetta /
    Floragato）——局面正常、不报错，只能靠对账发现。
    """

    def _battle_heal(self, tag):
        b = _arena()
        b.players[0].active = make_pokemon(PokemonDef(
            "A", hp=200, ptype="lightning",
            attacks=(AttackDef("Mend", cost=0, damage=0, effects=(tag,)),)))
        b.players[1].active = make_pokemon(PokemonDef("D", hp=200,
                                                     ptype="colorless"))
        return b

    def _add_bench(self, b, name, ptype, stage=0):
        fp = make_pokemon(PokemonDef(name, hp=200, ptype=ptype, stage=stage))
        b.players[0].bench.append(fp)
        return fp

    def test_heal_without_scope_hits_attacker_only(self):
        """省略 scope = 回**施术者自身**（历史语义，不能被范围扩展改掉）"""
        b = self._battle_heal("heal:30")
        self._add_bench(b, "B1", "water")
        b.players[0].active.damage = 100
        b.players[0].bench[0].damage = 100
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[0].active.damage, 70)
        self.assertEqual(b.players[0].bench[0].damage, 100, "无 scope 不得波及后备")

    def test_heal_all_hits_every_own_pokemon(self):
        b = self._battle_heal("heal:30:all")
        self._add_bench(b, "B1", "water")
        b._bench_dummy = self._add_bench(b, "B2", "grass", stage=1)
        for fp in b.players[0].all_field():
            fp.damage = 100
        b.step({"verb": "attack", "attack": 0})
        for fp in b.players[0].all_field():
            self.assertEqual(fp.damage, 70, fp.name)

    def test_heal_bench_excludes_active(self):
        """Leafeon ex：Heal 100 from **each of your Benched** Pokémon"""
        b = self._battle_heal("heal:100:bench")
        self._add_bench(b, "B1", "water")
        self._add_bench(b, "B2", "grass")
        for fp in b.players[0].all_field():
            fp.damage = 150
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[0].active.damage, 150, "战斗位不应被回复")
        self.assertEqual([fp.damage for fp in b.players[0].bench], [50, 50])

    def test_heal_basic_only_hits_stage0(self):
        """Leavanny：Heal 100 from each of your **Basic** Pokémon"""
        b = self._battle_heal("heal:100:basic")
        self._add_bench(b, "B1", "water", stage=0)
        self._add_bench(b, "B2", "grass", stage=2)
        for fp in b.players[0].all_field():
            fp.damage = 150
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[0].active.damage, 50)       # stage 0
        self.assertEqual(b.players[0].bench[0].damage, 50)     # stage 0
        self.assertEqual(b.players[0].bench[1].damage, 150)    # stage 2 不动

    def test_heal_choose_uses_target_codes(self):
        """`choose`：位置码 0=战斗位，i+1=后备 i"""
        b = self._battle_heal("heal:40:choose")
        self._add_bench(b, "B1", "water")
        self._add_bench(b, "B2", "grass")
        for fp in b.players[0].all_field():
            fp.damage = 100
        b.step({"verb": "attack", "attack": 0, "target": 2})
        self.assertEqual(b.players[0].active.damage, 100)
        self.assertEqual(b.players[0].bench[0].damage, 100)
        self.assertEqual(b.players[0].bench[1].damage, 60)

    def test_heal_bench_one_filters_by_type(self):
        """Meloetta：Heal 120 from 1 of your Benched **{P}** Pokémon"""
        b = self._battle_heal("heal:120:bench_one:type:psychic")
        self._add_bench(b, "W", "water")
        self._add_bench(b, "P", "psychic")
        for fp in b.players[0].bench:
            fp.damage = 150
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[0].bench[0].damage, 150, "属性不符不得被回复")
        self.assertEqual(b.players[0].bench[1].damage, 30)

    def test_heal_bench_one_no_target_is_noop(self):
        """负向守卫：后备没有该属性 → **无效果**（不是抛错，也不是"回第一只"）

        官方规则：效果无合法目标时只是不执行，招式仍可使用。若抛错，Meloetta
        在无 {P} 后备时整张招式不可用（池级执行用例会捕获）。
        """
        b = self._battle_heal("heal:120:bench_one:type:psychic")
        self._add_bench(b, "W", "water")
        b.players[0].bench[0].damage = 150
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[0].bench[0].damage, 150, "属性不符不得被回复")

    def test_heal_active_scope(self):
        """`active`：卡面「your Active Pokémon」，与施术者是谁无关"""
        b = self._battle_heal("heal:70:active")
        b.players[0].active.damage = 100
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[0].active.damage, 30)

    def test_heal_all_never_goes_negative(self):
        """回复不得超过已受伤害（damage 下限 0）"""
        b = self._battle_heal("heal:100:all")
        b.players[0].active.damage = 10
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[0].active.damage, 0)


class TestTrainerNonSearch(unittest.TestCase):
    """训练家侧「非检索类」效果派发（2026-10-27）

    这些能力引擎早就在招式侧实现，只是训练家派发链没接 —— 于是
    「Draw 3 cards.」「Heal 30 …」这类最常见的效果卡全部进不了池。
    """

    def _trainer_battle(self, tag, kind="item", name="T"):
        b = _arena()
        b.players[0].active = make_pokemon(PokemonDef("A", hp=200,
                                                      ptype="lightning"))
        b.players[1].active = make_pokemon(PokemonDef("D", hp=200,
                                                      ptype="colorless"))
        b.players[0].hand = [TrainerRef(TrainerDef(tag, kind=kind, name=name))]
        return b

    def test_trainer_draw(self):
        b = self._trainer_battle("draw:3", "supporter", "Cheren")
        before = len(b.players[0].hand)
        r = b.step({"verb": "trainer", "hand": 0})
        self.assertTrue(r["ok"], r.get("error"))
        # 手牌：-1（用掉）+3（抽）
        self.assertEqual(len(b.players[0].hand), before - 1 + 3)

    def test_trainer_draw_to(self):
        b = self._trainer_battle("draw_to:5", "supporter")
        r = b.step({"verb": "trainer", "hand": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(b.players[0].hand), 5)

    def test_trainer_heal_active(self):
        b = self._trainer_battle("heal:70:active", "supporter", "Cook")
        b.players[0].active.damage = 100
        r = b.step({"verb": "trainer", "hand": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[0].active.damage, 30)

    def test_trainer_heal_choose(self):
        b = self._trainer_battle("heal:30:choose", "item", "Potion")
        bp = make_pokemon(PokemonDef("B", hp=100, ptype="water"))
        bp.damage = 50
        b.players[0].bench = [bp]
        r = b.step({"verb": "trainer", "hand": 0, "target": 1})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(bp.damage, 20)

    def test_trainer_discard_all_then_draw(self):
        """Carmine：先弃光手牌再抽 5 —— 顺序反了会变成"直接空手" """
        b = self._trainer_battle("discard:all", "supporter", "Carmine")
        b.players[0].hand.append(TrainerRef(TrainerDef("hammer", kind="item",
                                                      name="Filler")))
        r = b.step({"verb": "trainer", "hand": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[0].hand, [], "手牌应被弃光")
        self.assertTrue(any("Filler" in str(x) for x in b.players[0].discard))

    def test_trainer_gust(self):
        b = self._trainer_battle("gust", "item", "Catcher")
        opp_bench = make_pokemon(PokemonDef("OB", hp=100, ptype="water"))
        b.players[1].bench = [opp_bench]
        r = b.step({"verb": "trainer", "hand": 0, "target": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.name, "OB")

    def test_trainer_mill_opponent(self):
        b = self._trainer_battle("mill:2:opp", "item")
        n = len(b.players[1].deck)
        r = b.step({"verb": "trainer", "hand": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(b.players[1].deck), n - 2)

    def test_trainer_unknown_tag_still_raises(self):
        """负向守卫：未知训练家 tag 必须抛错，不得静默忽略"""
        b = self._trainer_battle("definitely_not_a_tag", "item")
        r = b.step({"verb": "trainer", "hand": 0})
        self.assertFalse(r["ok"])


class TestAbilityOnDamaged(unittest.TestCase):
    """受击反应特性（``on_damaged:<kind>:<arg>``，2026-10-27）

    卡面：「If this Pokémon is in the Active Spot and is damaged by an attack from
    your opponent's Pokémon (even if this Pokémon is Knocked Out), …」——效果落在
    **攻击方**身上。三条纪律：
      · 只在**真受伤**时触发（被盾挡下不算）；
      · 被动特性**不进合法动作表**（不能主动发动）；
      · 「even if Knocked Out」→ 先在攻击方身上结算，再处理击倒。
    """

    def _setup(self, tag, def_hp=200, atk_damage=30, ab_name="Revenge"):
        b = _arena()
        b.players[0].active = make_pokemon(PokemonDef(
            "Atk", hp=200, ptype="colorless",
            attacks=(AttackDef("Hit", cost=0, damage=atk_damage, effects=()),)))
        b.players[1].active = make_pokemon(PokemonDef(
            "Def", hp=def_hp, ptype="colorless",
            abilities=(AbilityDef(ab_name, tag=tag, location="field"),)))
        b.players[0].active.energy["fire"] = 1
        return b

    def test_status_goes_to_attacker(self):
        """「the Attacking Pokémon is now Burned」→ 攻击方被烧伤（不是防守方）"""
        b = self._setup("on_damaged:status:burn")
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertIn("burn", b.players[0].active.status, "烧伤应落在攻击方")
        self.assertNotIn("burn", b.players[1].active.status, "防守方不该被烧伤")

    def test_counter_damages_attacker(self):
        b = self._setup("on_damaged:counter:30")
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[0].active.damage, 30)

    def test_discard_energy_from_attacker(self):
        b = self._setup("on_damaged:discard_energy:1")
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[0].active.total_energy(), 0,
                         "攻击方的能量应被弃置")

    def test_not_triggered_without_damage(self):
        """被打 0 点（盾全挡）不算"被伤害" → 不触发"""
        b = self._setup("on_damaged:status:burn", atk_damage=0)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertNotIn("burn", b.players[0].active.status)

    def test_passive_ability_not_in_legal_actions(self):
        """被动特性不得出现在合法动作表里（否则可无限主动发动）"""
        b = self._setup("on_damaged:status:burn")
        b.current = 1
        names = [a.get("name") for a in b.legal_actions() if a.get("verb") == "ability"]
        self.assertNotIn("Revenge", names)

    def test_triggers_even_if_defender_knocked_out(self):
        """「even if this Pokémon is Knocked Out」→ 防守方被击倒也要先结算反应"""
        b = self._setup("on_damaged:counter:30", def_hp=10, atk_damage=50)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertIsNone(b.players[1].active, "防守方应被击倒")
        self.assertEqual(b.players[0].active.damage, 30,
                         "击倒前仍须在攻击方身上放指示物")


class TestScalingConditions5(unittest.TestCase):
    """第五批条件伤害缩放（2026-10-27）

    卡面原型「If <局面条件>, this attack does N more damage.」
    纪律：**只认当下局面**能判定的条件；依赖回合历史的（本回合换入 / 被治疗 /
    进化 / 上回合用过某招）一律不进白名单。
    """

    def _b(self):
        from engine.battle import _eval_condition
        b = _arena()
        return b, _eval_condition

    def test_stadium_in_play(self):
        b, ev = self._b()
        self.assertFalse(ev(b, "stadium_in_play"))
        b.stadium = {"tag": "stadium:hp_plus:30:stage:0", "name": "X"}
        self.assertTrue(ev(b, "stadium_in_play"))

    def test_own_active_has_tool(self):
        b, ev = self._b()
        self.assertFalse(ev(b, "own_active_has_tool"))
        b.players[0].active.tool = "tool:hp_plus:30"
        self.assertTrue(ev(b, "own_active_has_tool"))

    def test_own_active_full_hp(self):
        b, ev = self._b()
        self.assertTrue(ev(b, "own_active_full_hp"))
        b.players[0].active.damage = 10
        self.assertFalse(ev(b, "own_active_full_hp"))

    def test_opp_active_has_status(self):
        b, ev = self._b()
        self.assertFalse(ev(b, "opp_active_has_status"))
        b.players[1].active.status.add("poison")
        self.assertTrue(ev(b, "opp_active_has_status"))

    def test_own_bench_has_damage(self):
        b, ev = self._b()
        _bench(b, 0, 2)
        self.assertFalse(ev(b, "own_bench_has_damage"))
        b.players[0].bench[1].damage = 20
        self.assertTrue(ev(b, "own_bench_has_damage"))

    def test_own_hand_eq_opp_hand(self):
        b, ev = self._b()
        self.assertTrue(ev(b, "own_hand_eq_opp_hand"))
        b.players[1].hand.append(EnergyRef())
        self.assertFalse(ev(b, "own_hand_eq_opp_hand"))

    def test_own_opp_active_energy_equal(self):
        b, ev = self._b()
        b.players[0].active.energy["fire"] = 2
        self.assertFalse(ev(b, "own_opp_active_energy_equal"))
        b.players[1].active.energy["water"] = 2
        self.assertTrue(ev(b, "own_opp_active_energy_equal"))

    def test_opp_hand_at_most(self):
        b, ev = self._b()
        b.players[1].hand.extend([EnergyRef() for _ in range(3)])
        self.assertTrue(ev(b, "opp_hand_at_most:3"))
        self.assertFalse(ev(b, "opp_hand_at_most:2"))

    def test_opp_prizes_at_most(self):
        b, ev = self._b()
        self.assertTrue(ev(b, "opp_prizes_at_most:6"))
        self.assertFalse(ev(b, "opp_prizes_at_most:3"))
        b.players[1].prizes_left = 3
        self.assertTrue(ev(b, "opp_prizes_at_most:3"))

    def test_own_prizes_exactly(self):
        b, ev = self._b()
        self.assertTrue(ev(b, "own_prizes_exactly:6"))
        self.assertFalse(ev(b, "own_prizes_exactly:5"))

    def test_opp_active_is_stage(self):
        b, ev = self._b()
        self.assertTrue(ev(b, "opp_active_is_stage:0"))
        self.assertFalse(ev(b, "opp_active_is_stage:2"))

    def test_own_active_hp_at_most(self):
        b, ev = self._b()
        self.assertFalse(ev(b, "own_active_hp_at_most:50"))
        b.players[0].active.damage = 160
        self.assertTrue(ev(b, "own_active_hp_at_most:50"))

    def test_own_active_status_param(self):
        b, ev = self._b()
        self.assertFalse(ev(b, "own_active_status:poison"))
        b.players[0].active.status.add("poison")
        self.assertTrue(ev(b, "own_active_status:poison"))
        with self.assertRaises(ValueError):
            ev(b, "own_active_status:poisoned")     # 非标准拼写必须抛错

    def test_own_active_energy_type_at_least(self):
        b, ev = self._b()
        b.players[0].active.energy["grass"] = 1
        self.assertFalse(ev(b, "own_active_energy_type_at_least:grass:2"))
        b.players[0].active.energy["grass"] = 2
        self.assertTrue(ev(b, "own_active_energy_type_at_least:grass:2"))
        self.assertFalse(ev(b, "own_active_energy_type_at_least:fire:1"))

    def test_own_all_energy_type_at_least(self):
        b, ev = self._b()
        b.players[0].active.energy["lightning"] = 2
        _bench(b, 0, 1)
        b.players[0].bench[0].energy["lightning"] = 3
        self.assertTrue(ev(b, "own_all_energy_type_at_least:lightning:4"))
        self.assertFalse(ev(b, "own_all_energy_type_at_least:lightning:6"))

    def test_opp_active_resistance(self):
        b, ev = self._b()
        b.players[1].active.pdef = PokemonDef(
            "Def", hp=200, ptype="colorless", resistance="fighting")
        self.assertTrue(ev(b, "opp_active_resistance:fighting"))
        self.assertFalse(ev(b, "opp_active_resistance:grass"))

    def test_own_bench_name_substring(self):
        """「has "Nidoking" in its name」→ 归一化后子串匹配"""
        b, ev = self._b()
        b.players[0].bench.append(make_pokemon(PokemonDef(
            "Team Rocket's Nidoking", hp=100, ptype="darkness", attacks=())))
        self.assertTrue(ev(b, "own_bench_name:nidoking"))
        self.assertFalse(ev(b, "own_bench_name:nidoqueen"))

    def test_unknown_condition_still_raises(self):
        """负向守卫：未知条件必须抛错（静默 False 会让加伤永不生效）"""
        b, ev = self._b()
        with self.assertRaises(ValueError):
            ev(b, "definitely_not_a_condition")


class TestCrossTurnLocks(unittest.TestCase):
    """跨回合锁定：攻击封锁 / 撤退封锁（2026-10-27）

    时间窗必须按**卡面**结算：
      · 「During your next turn」→ 只在**自己**的下一个回合锁；
      · 「During your opponent's next turn」→ 只在**对手**的下一个回合锁；
      · 「until it leaves the Active Spot」→ 离场/进化才解除。
    不能折算成「本回合不能攻击/撤退」——那会在错误回合生效。
    """

    def _fighter(self, *attacks):
        return make_pokemon(PokemonDef(
            "Fighter", hp=200, ptype="colorless", attacks=tuple(attacks)))

    def test_self_attack_lock_expires_after_next_turn(self):
        b = _arena()
        _equip(b, ("attack_lock:all:turn",), damage=10, owner=0)
        self.assertTrue(b.step({"verb": "attack", "attack": 0})["ok"])
        # 对手回合 → 自己下一个回合：仍锁
        b.step({"verb": "pass"})
        acts = b.legal_actions()
        self.assertFalse(any(a["verb"] == "attack" for a in acts),
                         "自己下个回合仍应被锁")
        self.assertTrue(b.step({"verb": "pass"})["ok"])
        b.step({"verb": "pass"})
        acts = b.legal_actions()
        self.assertTrue(any(a["verb"] == "attack" for a in acts),
                        "再下一个回合应解除")

    def test_named_attack_lock_only_blocks_named_attack(self):
        b = _arena()
        b.players[0].active = self._fighter(
            AttackDef("Giant Wave", cost=0, damage=10,
                      effects=("attack_lock:name:turn:Giant Wave",)),
            AttackDef("Tackle", cost=0, damage=10, effects=()))
        self.assertTrue(b.step({"verb": "attack", "attack": 0})["ok"])
        b.step({"verb": "pass"})
        idx = [a.get("attack") for a in b.legal_actions()
               if a["verb"] == "attack"]
        self.assertEqual(idx, [1], "只锁指定招式，不得连带其它招式")

    def test_opponent_attack_lock_applies_on_their_turn(self):
        b = _arena()
        atk = AttackDef("Powder Snow", cost=0, damage=10, effects=())
        b.players[1].active = self._fighter(atk)
        _equip(b, ("attack_lock:opp_all:turn",), damage=10, owner=0)
        self.assertTrue(b.step({"verb": "attack", "attack": 0})["ok"])
        self.assertEqual(b.current, 1)
        self.assertFalse(any(a["verb"] == "attack"
                             for a in b.legal_actions()),
                         "对手的战斗位在其下个回合不能攻击")

    def test_opp_named_attack_lock_runtime_choice(self):
        """`attack_lock:opp_name:turn`：锁对手战斗位**指定**招式，招式名由
        出招动作 `lock_attack` 运行时选择（Klefki「Memory Lock」/ Murkrow「Torment」）。"""
        b = _arena()
        b.players[1].active = self._fighter(
            AttackDef("Slash", cost=0, damage=10),
            AttackDef("Bite", cost=0, damage=10))
        _equip(b, ("attack_lock:opp_name:turn",), damage=10, owner=0)
        self.assertTrue(b.step({"verb": "attack", "attack": 0,
                                "lock_attack": "Bite"})["ok"])
        self.assertEqual(b.current, 1)
        idx = [a.get("attack") for a in b.legal_actions()
               if a["verb"] == "attack"]
        self.assertEqual(idx, [0], "只锁被选中的 Bite，Slash 仍可用")

    def test_opp_named_attack_lock_defaults_to_first_attack(self):
        """未给 `lock_attack` 时取确定性默认（对手战斗位首个招式），绝不静默不生效"""
        b = _arena()
        b.players[1].active = self._fighter(
            AttackDef("Slash", cost=0, damage=10),
            AttackDef("Bite", cost=0, damage=10))
        _equip(b, ("attack_lock:opp_name:turn",), damage=10, owner=0)
        self.assertTrue(b.step({"verb": "attack", "attack": 0})["ok"])
        idx = [a.get("attack") for a in b.legal_actions()
               if a["verb"] == "attack"]
        self.assertEqual(idx, [1], "默认锁首个招式 Slash")

    def test_opp_named_attack_lock_expires_after_their_turn(self):
        """窗口只覆盖对手**下一个**回合，再下一个回合解除"""
        b = _arena()
        b.players[1].active = self._fighter(
            AttackDef("Slash", cost=0, damage=10),
            AttackDef("Bite", cost=0, damage=10))
        _equip(b, ("attack_lock:opp_name:turn",), damage=10, owner=0)
        b.step({"verb": "attack", "attack": 0, "lock_attack": "Bite"})
        self.assertEqual(b.current, 1)
        idx = [a.get("attack") for a in b.legal_actions()
               if a["verb"] == "attack"]
        self.assertEqual(idx, [0], "对手当前回合 Bite 被锁")
        b.step({"verb": "pass"})          # 回到己方
        b.step({"verb": "pass"})          # 对手**下一个**回合 → 应已解除
        self.assertEqual(b.current, 1)
        idx = [a.get("attack") for a in b.legal_actions()
               if a["verb"] == "attack"]
        self.assertEqual(idx, [0, 1], "窗口只覆盖对手一个回合")

    def test_active_lock_persists_until_leaving_active(self):
        b = _arena()
        atk = AttackDef("Blaze Blitz", cost=0, damage=10,
                        effects=("attack_lock:name:active:Blaze Blitz",))
        b.players[0].active = self._fighter(atk)
        self.assertTrue(b.step({"verb": "attack", "attack": 0})["ok"])
        b.step({"verb": "pass"})
        self.assertFalse(any(a["verb"] == "attack"
                             for a in b.legal_actions()))
        fp = b.players[0].active
        self.assertIn(fp._attack_key("Blaze Blitz"),
                      fp.attack_lock_names_active)
        fp.clear_active_locks()          # 模拟离场（撤退/换位走同一清理点）
        self.assertNotIn(fp._attack_key("Blaze Blitz"),
                         fp.attack_lock_names_active)
        self.assertTrue(any(a["verb"] == "attack"
                            for a in b.legal_actions()))

    def test_retreat_lock_blocks_retreat_for_one_turn(self):
        b = _arena()
        b.players[1].active.pdef = PokemonDef(
            "Defender", hp=200, ptype="colorless", retreat=1, attacks=())
        b.players[1].active.add_energy("colorless")
        _bench(b, 1, 1)
        _equip(b, ("retreat_lock:opp:turn",), damage=10, owner=0)
        self.assertTrue(b.step({"verb": "attack", "attack": 0})["ok"])
        self.assertEqual(b.current, 1)
        self.assertFalse(any(a["verb"] == "retreat"
                             for a in b.legal_actions()),
                         "对手下个回合不能撤退")
        b.step({"verb": "pass"})
        b.step({"verb": "pass"})
        self.assertEqual(b.current, 1)
        self.assertTrue(any(a["verb"] == "retreat"
                            for a in b.legal_actions()),
                        "时限过后应恢复撤退")

    def test_self_retreat_lock_blocks_own_next_turn(self):
        b = _arena()
        _equip(b, ("retreat_lock:self:turn",), damage=10, owner=0)
        b.players[0].active.add_energy("colorless")
        _bench(b, 0, 1)
        self.assertTrue(b.step({"verb": "attack", "attack": 0})["ok"])
        self.assertEqual(b.current, 1)
        b.step({"verb": "pass"})        # 回到自己：下个回合
        self.assertEqual(b.current, 0)
        self.assertFalse(any(a["verb"] == "retreat"
                             for a in b.legal_actions()),
                         "自己下个回合不能撤退")
        b.step({"verb": "pass"})
        b.step({"verb": "pass"})
        self.assertEqual(b.current, 0)
        self.assertTrue(any(a["verb"] == "retreat"
                            for a in b.legal_actions()),
                        "时限过后应恢复撤退")

    def test_item_lock_blocks_opponent_items_only(self):
        b = _arena()
        _equip(b, ("play_lock:item:opp:turn",), damage=10, owner=0)
        opp = b.players[1]
        opp.hand = [TrainerRef(TrainerDef("poffin", kind="item",
                                          name="Buddy")),
                    TrainerRef(TrainerDef("tool:hp_plus:20", kind="tool",
                                          name="Tool")),
                    TrainerRef(TrainerDef("research:3", kind="supporter",
                                          name="Prof"))]
        self.assertTrue(b.step({"verb": "attack", "attack": 0})["ok"])
        kinds = [a.get("kind") for a in b.legal_actions()
                 if a["verb"] == "trainer"]
        self.assertNotIn("item", kinds, "对手下个回合不能打物品")
        self.assertNotIn("tool", kinds, "物品封锁应覆盖 Pokémon Tool")
        self.assertIn("supporter", kinds, "物品封锁不应误伤支援者")
        b.step({"verb": "pass"})
        b.step({"verb": "pass"})
        kinds = [a.get("kind") for a in b.legal_actions()
                 if a["verb"] == "trainer"]
        self.assertIn("item", kinds, "时限过后物品应恢复")

    def test_evolution_lock_blocks_opponent_evolution(self):
        b = _arena()
        _equip(b, ("play_lock:evolution:opp:turn",), damage=10, owner=0)
        opp = b.players[1]
        opp.hand = [PokeRef(PokemonDef("Evolved", hp=250, ptype="colorless",
                                       stage=1, evolves_from="Defender"))]
        self.assertTrue(b.step({"verb": "attack", "attack": 0})["ok"])
        self.assertFalse(any(a["verb"] == "evolve"
                             for a in b.legal_actions()),
                         "对手下个回合不能从手牌进化")
        b.step({"verb": "pass"})
        b.step({"verb": "pass"})
        self.assertTrue(any(a["verb"] == "evolve"
                            for a in b.legal_actions()),
                        "时限过后应恢复进化")

    def test_evolve_clears_locks(self):
        fp = make_pokemon(PokemonDef("Old", hp=100, ptype="colorless",
                                     attacks=()))
        fp.attack_lock_all_until = 9
        fp.attack_lock_names_until["x"] = 9
        fp.attack_lock_names_active.add("x")
        fp.retreat_lock_until = 9
        fp.evolve_to(PokemonDef("New", hp=120, ptype="colorless", attacks=()))
        self.assertFalse(fp.attack_locked("x", 1))
        self.assertFalse(fp.retreat_locked(1))


class TestB3DrawFamily(unittest.TestCase):
    """B3（2026-10-28）：抽牌完整性 —— 双方同抽 / 对手洗回手牌后抽 /
    按计数源抽 / 条件式抽"""

    def _ability(self, tag, deck=20, hand=()):
        b = _arena()
        ab = AbilityDef("Drawer", name_zh="抽", tag=tag, location="field")
        b.players[0].active = make_pokemon(
            PokemonDef("Holder", hp=200, ptype="colorless", attacks=(),
                       abilities=(ab,)))
        b.players[0].deck = [EnergyRef(E_FIRE) for _ in range(deck)]
        b.players[1].deck = [EnergyRef(E_FIRE) for _ in range(deck)]
        b.players[0].hand = list(hand)
        return b, {"verb": "ability", "name": "Drawer", "pos": 0, "tag": tag}

    def test_draw_both(self):
        b, act = self._ability("draw_both:1")
        r = b.step(act)
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(b.players[0].hand), 1)
        self.assertEqual(len(b.players[1].hand), 1, "对手也必须抽 1 张")

    def test_draw_per_counter(self):
        b, act = self._ability("draw:1:per:own_bench")
        _bench(b, 0, 2)
        r = b.step(act)
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(b.players[0].hand), 2)

    def test_draw_conditional_not_met(self):
        """`draw:N:if:<cond>` 条件不满足 → 特性不可发动（Fezandipiti 的前置条件）"""
        b, act = self._ability("draw:3:if:own_ko_last_turn")
        r = b.step(act)
        self.assertFalse(r["ok"], "前置条件不满足时不得发动")
        self.assertEqual(len(b.players[0].hand), 0)

    def test_draw_conditional_met(self):
        b, act = self._ability("draw:3:if:own_ko_last_turn")
        b._ko_flags()[0] = 1
        r = b.step(act)
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(b.players[0].hand), 3)

    def test_conditional_draw_not_offered_when_false(self):
        b, _act = self._ability("draw:3:if:own_ko_last_turn")
        self.assertNotIn("Drawer", [a.get("name") for a in b.legal_actions()])

    def test_opp_shuffle_draw_replaces_hand(self):
        """对手把手牌洗回牌库后抽 N（Gothitelle）：手牌总数变成 N"""
        b, act = self._ability("opp_shuffle_draw:3")
        b.players[1].hand = [EnergyRef(E_FIRE) for _ in range(5)]
        r = b.step(act)
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(b.players[1].hand), 3)
        self.assertEqual(b.players[0].hand, [], "我方不应该抽牌")

    def test_opp_shuffle_draw_if_hand_skips_empty(self):
        """Vivillon：手牌为空时「if they put any cards」不成立 → 不抽"""
        b, act = self._ability("opp_shuffle_draw:4:if_hand")
        b.players[1].hand = []
        r = b.step(act)
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(b.players[1].hand), 0)


class TestB3RecoverFamily(unittest.TestCase):
    """B3：`recover:heads:N` 与 `recover:N:combo:<a>+<b>[:type:<c>]`"""

    def _attack_battle(self, effects):
        b = _arena()
        _equip(b, effects, damage=0)
        return b

    def test_recover_heads_counts_coins(self):
        b = self._attack_battle(("recover:heads:3",))
        p = b.players[0]
        p.discard_refs = [EnergyRef(E_FIRE) for _ in range(5)]
        p.discard = ["Fire Energy"] * 5
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        coin = [e for e in b.events if e.get("type") == "coin"]
        self.assertEqual(len(coin), 1, "3 枚硬币只记一条事件（结果含正面枚数）")
        heads = int(str(coin[0]["result"]).split("/")[0])
        self.assertEqual(len(p.hand), heads)
        self.assertLessEqual(len(p.hand), 3)

    def test_combo_excludes_rule_box_pokemon(self):
        """Lana's Aid：ex（有 Rule Box）不得被回收"""
        b = self._attack_battle(("recover:3:combo:no_rule_pokemon+basic_energy",))
        p = b.players[0]
        ex = PokeRef(PokemonDef("Big ex", hp=300, ptype="colorless", attacks=(),
                                is_ex=True))
        plain = PokeRef(PokemonDef("Plain", hp=60, ptype="colorless", attacks=()))
        p.discard_refs = [ex, plain, EnergyRef(E_FIRE)]
        p.discard = ["Big ex", "Plain", "Fire Energy"]
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        names = sorted(c.pdef.name for c in p.hand if isinstance(c, PokeRef))
        self.assertEqual(names, ["Plain"])
        self.assertTrue(any(isinstance(c, EnergyRef) for c in p.hand))
        self.assertEqual(len(p.hand), 2)

    def test_combo_type_filter(self):
        b = self._attack_battle(
            ("recover:4:combo:pokemon+basic_energy:type:fighting",))
        p = b.players[0]
        fighting = PokeRef(PokemonDef("Fighter", hp=60, ptype="fighting",
                                      attacks=()))
        water = PokeRef(PokemonDef("Water", hp=60, ptype="water", attacks=()))
        p.discard_refs = [fighting, water,
                          EnergyRef(EnergyDef("fighting")),
                          EnergyRef(EnergyDef("water"))]
        p.discard = ["Fighter", "Water", "Fighting Energy", "Water Energy"]
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(p.hand), 2, "只应回收 {F} 宝可梦与 {F} 能量")
        names = sorted(getattr(c, "pdef", None).name if hasattr(c, "pdef")
                       else c.edef.type for c in p.hand)
        self.assertEqual(names, ["Fighter", "fighting"])


class TestB3TypedHandEnergyDiscard(unittest.TestCase):
    """B3：带类型手牌弃能 + 失败门（Lurantis / Ceruledge）"""

    def _battle(self, effects, damage=130, hand=()):
        b = _arena()
        _equip(b, effects, damage=damage)
        b.players[0].hand = list(hand)
        return b

    def test_discards_typed_energy_only(self):
        fire = EnergyRef(EnergyDef("fire"))
        grass = [EnergyRef(EnergyDef("grass")) for _ in range(2)]
        b = self._battle(("discard_hand_energy:2:type:grass:required",),
                         hand=[fire] + grass)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.damage, 130, "弃得出 → 正常伤害")
        self.assertTrue(any(isinstance(c, EnergyRef) and c.edef.type == "fire"
                            for c in b.players[0].hand), "火能量不得被弃")
        self.assertEqual(sum(1 for c in b.players[0].hand
                             if isinstance(c, EnergyRef)
                             and c.edef.type == "grass"), 0)

    def test_fail_gate_nulls_damage(self):
        """手牌只有 1 张草能量 → 本招式什么都不做（且不弃那张）"""
        b = self._battle(("discard_hand_energy:2:type:grass:required",),
                         hand=[EnergyRef(EnergyDef("grass"))])
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.damage, 0)
        self.assertEqual(len(b.players[0].hand), 1, "失败门触发时不得弃牌")


class TestB3AbilityBenchSwitch(unittest.TestCase):
    """B3：后备特性自换位 `switch_self[:if:<cond>]`（Linoone「Excited Dash」）"""

    def _battle(self, tag, mega=False, on_bench=True):
        b = _arena()
        ab = AbilityDef("Dash", name_zh="冲刺", tag=tag, location="field")
        holder = make_pokemon(PokemonDef("Linoone", hp=100, ptype="colorless",
                                         attacks=(), abilities=(ab,)))
        if on_bench:
            b.players[0].bench = [holder]
        else:
            b.players[0].active = holder
        if mega:
            b.players[0].bench.append(make_pokemon(
                PokemonDef("Mega X ex", hp=300, ptype="colorless", attacks=(),
                           is_ex=True, is_mega=True)))
        return b, {"verb": "ability", "name": "Dash",
                   "pos": 1 if on_bench else 0, "tag": tag}

    def test_bench_switch_swaps_with_active(self):
        b, act = self._battle("switch_self")
        old = b.players[0].active
        r = b.step(act)
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[0].active.pdef.name, "Linoone")
        self.assertIn(old, b.players[0].bench)

    def test_mega_condition_blocks_without_mega(self):
        b, act = self._battle("switch_self:if:own_mega_in_play", mega=False)
        self.assertNotIn("Dash", [a.get("name") for a in b.legal_actions()],
                         "场上没有 Mega 时该特性不可发动")
        r = b.step(act)
        self.assertFalse(r["ok"], "条件不满足必须拒绝执行")

    def test_mega_condition_allows_with_mega(self):
        b, act = self._battle("switch_self:if:own_mega_in_play", mega=True)
        r = b.step(act)
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[0].active.pdef.name, "Linoone")

    def test_active_holder_not_offered(self):
        b, act = self._battle("switch_self", on_bench=False)
        self.assertNotIn("Dash", [a.get("name") for a in b.legal_actions()])


class TestDiscardToolStadium(unittest.TestCase):
    """S6（2026-10-28）：`discard_tool:N:<scope>` 与 `discard_stadium`"""

    def test_discard_stadium_clears_field(self):
        b = _arena()
        _equip(b, ("discard_stadium",), damage=0)
        b.stadium = {"name": "Test Stadium", "tag": "stadium:hp_plus:10:stage:1",
                     "owner": "A"}
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertIsNone(b.stadium, "场地必须被弃置")
        self.assertIn("Test Stadium", b.players[0].discard)

    def test_discard_tool_opp_active(self):
        b = _arena()
        _equip(b, ("discard_tool:all:opp_active",), damage=0)
        target = b.players[1].active
        target.tool = "tool:hp_plus:30"
        target.tool_name = "Cape of Toughness"
        # 我方战斗位也放一个道具（不得被误弃）
        b.players[0].active.tool = "tool:hp_plus:20"
        b.players[0].active.tool_name = "Own Cape"
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertIsNone(target.tool, "对手战斗位道具应被弃")
        self.assertIsNotNone(b.players[0].active.tool, "不得弃我方道具")

    def test_discard_tool_up_to_two_both_sides(self):
        b = _arena()
        _equip(b, ("discard_tool:2:both",), damage=0)
        b.players[0].active.tool = "tool:hp_plus:20"
        b.players[1].active.tool = "tool:hp_plus:20"
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertIsNone(b.players[0].active.tool)
        self.assertIsNone(b.players[1].active.tool)

    def test_discard_stadium_noop_without_stadium(self):
        b = _arena()
        _equip(b, ("discard_stadium",), damage=0)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error") or "无场地时不应抛错")


class TestRetaliateAndCounterPer(unittest.TestCase):
    """第二十九批（2026-10-29）：下回合受击反制 / 按能量数反制

    · `retaliate_next:<n|equal>` —— 招式挂在自己身上，只在**对手的下一个回合**内
      生效（Bouffalant「Ready to Ram」/ Zamazenta「Strong Bash」）。
    · `on_damaged:counter_per:<n>:<color>` —— 每次被攻击伤害，攻击者吃
      n×10×**持有者该色能量数**（Orthworm ex）。
    """

    def test_retaliate_fixed_next_turn(self):
        b = _arena()
        me = _equip(b, ("retaliate_next:60",), damage=10)
        enemy = _equip(b, (), owner=1, damage=10)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(me.retaliate_next_n, 60)
        r = b.step({"verb": "attack", "attack": 0})       # 对手回合反击
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(me.damage, 10, "被对手打 10")
        self.assertEqual(enemy.damage, 70, "反制 60 + 之前的 10")

    def test_retaliate_equal_to_damage(self):
        b = _arena()
        me = _equip(b, ("retaliate_next:equal",), damage=10)
        enemy = _equip(b, (), owner=1, damage=70)
        b.step({"verb": "attack", "attack": 0})
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(me.damage, 70)
        self.assertEqual(enemy.damage, 80, "10（首击）+ 70（等于所受伤害）")

    def test_retaliate_expires_after_next_turn(self):
        b = _arena()
        _equip(b, ("retaliate_next:60",), damage=10)
        enemy = _equip(b, (), owner=1, damage=10)
        b.step({"verb": "attack", "attack": 0})
        # 对手回合 → 我方回合 → 再回到对手回合（tc[1] 已 +2，窗口到期）
        for _ in range(2):
            self.assertTrue(b.step({"verb": "pass"})["ok"])
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(enemy.damage, 10, "窗口过后不再反制")

    def test_ability_counter_per_energy(self):
        b = _arena()
        _equip(b, (), damage=10)                      # 攻击方
        d = b.players[1].active
        d.pdef = PokemonDef("Orthworm", hp=200, ptype="metal", attacks=(),
                            abilities=(AbilityDef("Pummeling Payback",
                                                  "on_damaged:counter_per:2:metal"),))
        d.energy["metal"] = 2
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[0].active.damage, 40, "2 点金属能量 × 20")

    def test_ability_counter_per_zero_energy_noop(self):
        b = _arena()
        _equip(b, (), damage=10)
        d = b.players[1].active
        d.pdef = PokemonDef("Orthworm", hp=200, ptype="metal", attacks=(),
                            abilities=(AbilityDef("Pummeling Payback",
                                                  "on_damaged:counter_per:2:metal"),))
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[0].active.damage, 0, "无该色能量则该段不生效")


class TestEnergyToDeckAndAbilityCounters(unittest.TestCase):
    """第二十八批（2026-10-29）：弃牌堆能量洗回牌库 · 双方「有特性者」指示物

    · `energy_to_deck:<color>:all` —— Kyogre「Riptide」/ Sinistcha ex「Re-Brew」：
      被计数的**基本能量**洗回牌库。⚠ 不洗回会让下一次攻击**重复计数**（静默加强）。
    · `counters:N:scope:ability:all` —— Cofagrigus「Law of the Underworld」：
      双方全场**印有特性**的宝可梦各吃 N 个指示物（己方那侧被击倒也由对手取奖赏）。
    """

    @staticmethod
    def _energy(color, name="", tag=""):
        return EnergyRef(EnergyDef(color, name=name, tag=tag))

    def test_energy_to_deck_only_basic_color(self):
        b = _arena()
        _equip(b, ("energy_to_deck:water:all",), damage=10)
        p = b.players[0]
        w1, w2 = self._energy("water"), self._energy("water")
        fire = self._energy("fire")
        sp = self._energy("colorless", "Mist Energy", "senergy:mist")
        for ref in (w1, w2, fire, sp):
            Battle._to_discard(p, ref)
        deck_before = len(p.deck)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual([c.edef.type for c in p.discard_refs], ["fire", "colorless"],
                         "只洗回**指定属性的基本能量**（特殊能量/其它颜色不动）")
        self.assertEqual(len(p.deck), deck_before + 2)
        self.assertFalse(any(x in p.discard for x in ("Water Energy",)),
                         "文本层弃牌堆也要同步移除")

    def test_counters_each_with_ability_both_sides(self):
        b = _arena()
        _equip(b, ("counters:6:scope:ability:all",), damage=0)
        mine = make_pokemon(PokemonDef("Mine", hp=50, ptype="colorless",
                                       attacks=(),
                                       abilities=(AbilityDef("A", "draw:1"),)))
        plain = make_pokemon(PokemonDef("Plain", hp=100, ptype="colorless",
                                        attacks=()))
        b.players[0].bench += [mine, plain]
        theirs = make_pokemon(PokemonDef("Theirs", hp=100, ptype="colorless",
                                         attacks=(),
                                         abilities=(AbilityDef("B", "draw:1"),)))
        b.players[1].bench.append(theirs)
        prizes_before = b.players[1].prizes_left
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(mine.damage, 60, "己方有特性者也被放（自毁式效果）")
        self.assertEqual(plain.damage, 0, "没有特性的不得被放")
        self.assertEqual(theirs.damage, 60)
        self.assertEqual(b.players[1].prizes_left, prizes_before - 1,
                         "己方宝可梦被击倒 → 由**对手**取奖赏")

    def test_ability_scope_rejects_count(self):
        b = _arena()
        _equip(b, ("counters:6:scope:ability:1",), damage=0)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertFalse(r["ok"], "ability 范围只支持 all")
        self.assertIn("ability", r.get("error") or "")


class TestSnipeTargetFilter(unittest.TestCase):
    """第二十七批（2026-10-29）：`snipe:` 的**目标筛选**与**逐目标缩放**

    · `:target:damaged` —— 只打「身上已有伤害指示物」的目标（Excadrill ex /
      Hippowdon）。先筛选再选数量：卡面的「1 of …」是**在合格目标里**选 1 个。
    · `:per:target_counters` —— 每目标按**它自己**的指示物数缩放
      （Team Rocket's Sneasel）；用全局计数源会把别人的指示物也算进来。
    """

    def test_filter_before_choice(self):
        b = _arena()
        _equip(b, ("snipe:opp_bench:60:1:target:damaged",), damage=10)
        clean, hurt = _bench(b, 1, 2)
        hurt.damage = 10
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(clean.damage, 0, "没指示物的目标不得被打")
        self.assertEqual(hurt.damage, 70, "唯一合格目标应吃满 60")

    def test_no_eligible_target_is_noop(self):
        b = _arena()
        _equip(b, ("snipe:opp_bench:60:1:target:damaged",), damage=10)
        mons = _bench(b, 1, 2)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual([m.damage for m in mons], [0, 0])

    def test_per_target_counters(self):
        b = _arena()
        _equip(b, ("snipe:opp_bench:20:1:per:target_counters",), damage=10)
        a, c = _bench(b, 1, 2)
        a.damage = 10        # 1 个指示物 → 20
        c.damage = 30        # 3 个指示物 → 60（确定性默认打伤得最重的）
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(c.damage, 90, "3 个指示物 × 20 = 60")
        self.assertEqual(a.damage, 10, "未被选中")

    def test_per_target_zero_counters_is_noop(self):
        b = _arena()
        _equip(b, ("snipe:opp_bench:20:1:per:target_counters",), damage=10)
        mons = _bench(b, 1, 1)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(mons[0].damage, 0, "0 个指示物 → 0 伤害")

    def test_both_bench_filtered(self):
        """Hippowdon：双方后备里**只有带指示物的**各吃 40"""
        b = _arena()
        _equip(b, ("snipe:both_bench:40:all:target:damaged",), damage=10)
        mine = _bench(b, 0, 2)
        theirs = _bench(b, 1, 2)
        mine[0].damage = 10
        theirs[1].damage = 20
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(mine[0].damage, 50, "己方合格目标也吃伤害（自己效果）")
        self.assertEqual(mine[1].damage, 0)
        self.assertEqual(theirs[1].damage, 60)
        self.assertEqual(theirs[0].damage, 0)


class TestBenchCounterSubsets(unittest.TestCase):
    """第二十六批（2026-10-29）：**限定后**的指示物计数源

    卡面三句都带限定词，用全体后备 `own_bench_counters` 会**多算伤害**
    （把别的属性/阵营/没指示物的也算进来）。
    """

    @staticmethod
    def _mon(name, ptype="colorless", hp=100):
        return make_pokemon(PokemonDef(name, hp=hp, ptype=ptype, attacks=()))

    def test_bench_counters_by_type(self):
        b = _arena()
        fight = self._mon("F1", "fighting")
        fight.damage = 30
        water = self._mon("W1", "water")
        water.damage = 10
        b.players[0].bench += [fight, water]
        self.assertEqual(_eval_counter(b, "own_bench_counters_type:fighting"), 3)
        self.assertEqual(_eval_counter(b, "own_bench_counters_type:water"), 1)

    def test_bench_counters_named(self):
        b = _arena()
        a = self._mon("Cynthia's Garchomp", hp=150)
        a.damage = 20
        c = self._mon("Garchomp", hp=150)
        c.damage = 40
        b.players[0].bench += [a, c]
        self.assertEqual(_eval_counter(b, "own_bench_counters_named:cynthia"), 2)

    def test_field_named_with_damage_counts_pokemon(self):
        """「for each of your Pokémon that has “Tauros” … that has any damage
        counters on it」——数的是**只数**（一只带 5 个指示物也只算 1）"""
        b = _arena()
        b.players[0].active = self._mon("Paldean Tauros", "fighting")
        b.players[0].active.damage = 10
        t2 = self._mon("Tauros", "fighting")
        t2.damage = 50
        clean = self._mon("Tauros", "fighting")
        b.players[0].bench += [t2, clean]
        self.assertEqual(_eval_counter(b, "own_field_named_with_damage:tauros"), 2)

    def test_unknown_counter_still_raises(self):
        b = _arena()
        with self.assertRaises(ValueError):
            _eval_counter(b, "own_bench_counters_type:rainbow")


class TestPreAttackPhase(unittest.TestCase):
    """第二十五批（2026-10-29）：`pre:` **伤害前阶段**

    卡面原型：「Before doing damage, discard all Pokémon Tools [and Special
    Energy] from your opponent's Active Pokémon.」——这类效果必须在**伤害计算
    之前**执行：守方道具（减伤/免疫）此刻仍挂着，放到伤害之后执行就等于
    「先按道具减伤一次，再把道具弃掉」，属静默少做。
    """

    def test_pre_discard_tool_removes_tool_before_damage(self):
        b = _arena()
        _equip(b, ("pre:discard_tool:all:opp_active",), damage=50)
        d = b.players[1].active
        d.tool = "tool:dmg_reduce:30"
        d.tool_name = "Rugged Helmet"
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertIsNone(d.tool, "对手战斗位道具应被弃置")
        self.assertEqual(d.damage, 50, "道具须在减伤之前被弃（不得先减 30）")

    def test_post_damage_discard_is_late(self):
        """对照：同一条效果若落在伤害之后（旧行为），伤害会先被道具减掉"""
        b = _arena()
        _equip(b, ("discard_tool:all:opp_active",), damage=50)
        d = b.players[1].active
        d.tool = "tool:dmg_reduce:30"
        d.tool_name = "Rugged Helmet"
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertIsNone(d.tool)
        self.assertEqual(d.damage, 20, "伤害后才弃道具 → 本次仍吃满 30 减伤")

    def test_if_pre_tool_status_only_with_tool(self):
        """「If you discarded a Pokémon Tool in this way, … is now Paralyzed.」

        有道具 → 弃置成功 → 施加麻痹；没有道具 → **不得**施加（此前无条件麻痹）。
        """
        b = _arena()
        _equip(b, ("paralyze:if_pre_tool", "pre:discard_tool:all:opp_active"),
               damage=30)
        d = b.players[1].active
        d.tool = "tool:hp_plus:30"
        d.tool_name = "Cape"
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertIn("paralyze", d.status)

    def test_if_pre_tool_status_without_tool_is_noop(self):
        b = _arena()
        _equip(b, ("paralyze:if_pre_tool", "pre:discard_tool:all:opp_active"),
               damage=30)
        d = b.players[1].active
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertNotIn("paralyze", d.status, "没弃到道具就不得施加状态")

    def test_pre_discard_special_energy_keeps_basic(self):
        """Mow Rotom：「… all Pokémon Tools **and Special Energy** …」"""
        b = _arena()
        _equip(b, ("pre:discard_energy:all:opp:special",
                   "pre:discard_tool:all:opp_active"), damage=30)
        d = b.players[1].active
        d.add_energy("fire")
        d.senergy.append(EnergyRef(EnergyDef(
            "colorless", name="Enriching Energy", tag="senergy:enriching")))
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(d.senergy, [], "特殊能量应被弃置")
        self.assertEqual(d.energy_of("fire"), 1, "基本能量不得被弃")

    def test_unknown_pre_form_raises(self):
        b = _arena()
        _equip(b, ("pre:teleport:all",), damage=10)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertFalse(r["ok"])
        self.assertIn("未知伤害前效果", r.get("error") or "")

    def test_wrong_scope_raises(self):
        """伤害前弃道具只支持对手**战斗位**；`:both` 会弃到自家道具 → 必须拒绝"""
        b = _arena()
        _equip(b, ("pre:discard_tool:all:both",), damage=10)
        b.players[0].active.tool = "tool:hp_plus:20"
        r = b.step({"verb": "attack", "attack": 0})
        self.assertFalse(r["ok"])
        self.assertIn("未知伤害前效果", r.get("error") or "")
        self.assertIsNotNone(b.players[0].active.tool, "被拒绝时不得弃自家道具")


class TestBounceFamily(unittest.TestCase):
    """S6（2026-10-28）：回手 / 洗回牌库 / 弃牌堆铺后备"""

    def test_self_to_hand_promotes_bench_and_keeps_attached(self):
        b = _arena()
        me = _equip(b, ("bounce:to_hand:self",), damage=0)
        me.add_energy("fire", 2)
        ally = _bench(b, 0, 1)[0]
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertIs(b.players[0].active, ally, "后备应顶上战斗位")
        names = [c.pdef.name for c in b.players[0].hand
                 if isinstance(c, PokeRef)]
        self.assertIn("Holder0", names)
        self.assertEqual(sum(1 for c in b.players[0].hand
                             if isinstance(c, EnergyRef)), 2,
                         "附加能量应随宝可梦回手")

    def test_self_to_hand_with_tool_ref(self):
        b = _arena()
        me = _equip(b, ("bounce:to_hand:self",), damage=0)
        tool = TrainerRef(TrainerDef("tool:hp_plus:20", kind="item",
                                     name="Cape"))
        me.tool = "tool:hp_plus:20"
        me.tool_name = "Cape"
        me.tool_ref = tool
        _bench(b, 0, 1)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertTrue(any(isinstance(c, TrainerRef)
                            and c.tdef.name == "Cape"
                            for c in b.players[0].hand),
                        "道具应随宝可梦回手（不是进弃牌堆）")

    def test_self_discard_attached(self):
        """Crobat ex：附加卡进弃牌堆"""
        b = _arena()
        me = _equip(b, ("bounce:to_hand:self:discard_attached",), damage=0)
        me.add_energy("fire", 1)
        _bench(b, 0, 1)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len([c for c in b.players[0].hand
                              if isinstance(c, EnergyRef)]), 0)
        self.assertIn("Fire Energy", b.players[0].discard)

    def test_shuffle_self_goes_to_deck(self):
        b = _arena()
        _equip(b, ("bounce:shuffle_self",), damage=0)
        _bench(b, 0, 1)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertIn("Holder0", [c.pdef.name for c in b.players[0].deck
                                  if isinstance(c, PokeRef)])
        self.assertEqual([c for c in b.players[0].hand
                          if isinstance(c, PokeRef)], [])

    def test_bench_bounce(self):
        b = _arena()
        _equip(b, ("bounce:to_hand:bench",), damage=0)
        bench = _bench(b, 0, 2)
        r = b.step({"verb": "attack", "attack": 0, "target": 2})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertNotIn(bench[1], b.players[0].bench, "选中的后备应回手")
        self.assertIn(bench[0], b.players[0].bench)

    def test_choose_bounce_trainer(self):
        b = _arena()
        b.players[0].hand = [TrainerRef(TrainerDef("bounce:to_hand:choose",
                                                   kind="item",
                                                   name="Scoop Up Cyclone"))]
        ally = _bench(b, 0, 1)[0]
        r = b.step({"verb": "trainer", "hand": 0, "target": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertIs(b.players[0].active, ally)

    def test_active_bounce_without_bench_is_noop(self):
        """战斗位无替补 → 效果**不执行**（不得直接判负，也不能让场上空掉）"""
        b = _arena()
        me = _equip(b, ("bounce:to_hand:self",), damage=0)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertIs(b.players[0].active, me, "场上必须仍有战斗位")
        self.assertEqual([c for c in b.players[0].hand
                          if isinstance(c, PokeRef)], [])

    def test_discard_to_bench_by_name(self):
        b = _arena()
        _equip(b, ("bounce:discard_to_bench:2:name:duskull",), damage=0)
        d = PokeRef(PokemonDef("Duskull", hp=60, ptype="psychic", attacks=()))
        other = PokeRef(PokemonDef("Other", hp=60, ptype="psychic", attacks=()))
        b.players[0].discard_refs = [d, other]
        b.players[0].discard = ["Duskull", "Other"]
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual([fp.pdef.name for fp in b.players[0].bench],
                         ["Duskull"])

    def test_discard_to_bench_hp_filter(self):
        b = _arena()
        _equip(b, ("bounce:discard_to_bench:1:hp:70",), damage=0)
        big = PokeRef(PokemonDef("Big", hp=100, ptype="psychic", attacks=()))
        small = PokeRef(PokemonDef("Small", hp=60, ptype="psychic", attacks=()))
        b.players[0].discard_refs = [big, small]
        b.players[0].discard = ["Big", "Small"]
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual([fp.pdef.name for fp in b.players[0].bench], ["Small"])


class TestScalingCountersAndMinus(unittest.TestCase):
    """S6（2026-10-28）：`dmg_minus` 与新增计数源"""

    def _damage_after(self, effects, prepare=None):
        b = _arena()
        fp = _equip(b, effects, damage=100)
        if prepare:
            prepare(b, fp)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        return b.players[1].active.damage, b

    def test_dmg_minus_per_energy(self):
        def prep(b, fp):
            b.players[1].active.add_energy("water", 1)
        dmg, _ = self._damage_after(
            ("dmg_minus:60:per:opp_active_energy",), prep)
        self.assertEqual(dmg, 40, "100 − 60×1 = 40")

    def test_dmg_minus_floors_at_zero(self):
        def prep(b, fp):
            b.players[1].active.add_energy("water", 3)
        dmg, _ = self._damage_after(
            ("dmg_minus:60:per:opp_active_energy",), prep)
        self.assertEqual(dmg, 0, "减伤下限为 0，不得出现负伤害")

    def test_named_counter(self):
        """`own_named_<key>`：按卡名子串计数（含阵营前缀）"""
        b = _arena()
        atk = AttackDef("T", cost=0, damage=10,
                        effects=("dmg_plus:30:per:own_named_teamrocket",))
        b.players[0].active = make_pokemon(
            PokemonDef("Team Rocket's Meowth", hp=200, ptype="colorless",
                       attacks=(atk,)))
        b.players[0].bench.append(make_pokemon(
            PokemonDef("Team Rocket's Porygon", hp=200, ptype="colorless",
                       attacks=())))
        b.players[0].bench.append(make_pokemon(
            PokemonDef("Plain", hp=200, ptype="colorless", attacks=())))
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.damage, 10 + 30 * 2)

    def test_opp_discard_item_counter(self):
        b = _arena()
        _equip(b, ("dmg_set:30:per:opp_discard_item",), damage=0)
        b.players[1].discard_refs = [
            TrainerRef(TrainerDef("hammer", kind="item", name="Item A")),
            TrainerRef(TrainerDef("hammer", kind="supporter", name="Sup B"))]
        b.players[1].discard = ["Item A", "Sup B"]
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.damage, 30, "只数 item")


class TestEnergyMove(unittest.TestCase):
    """S6（2026-10-28）：`energy_move:N[:type:<c>]:from:<src>:to:<dst>`"""

    def _battle(self, effects):
        b = _arena()
        me = _equip(b, effects, damage=0)
        ally = _bench(b, 0, 1)[0]
        return b, me, ally

    def test_active_to_bench(self):
        b, me, ally = self._battle(("energy_move:2:from:active:to:bench",))
        me.add_energy("fire", 3)
        r = b.step({"verb": "attack", "attack": 0, "target": 1})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(me.total_energy(), 1)
        self.assertEqual(ally.total_energy(), 2)

    def test_typed_moves_only_that_color(self):
        b, me, ally = self._battle(
            ("energy_move:1:type:water:from:active:to:bench",))
        me.add_energy("water", 1)
        me.add_energy("fire", 2)
        r = b.step({"verb": "attack", "attack": 0, "target": 1})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(ally.energy_of("water"), 1)
        self.assertEqual(me.energy_of("fire"), 2)
        self.assertEqual(me.energy_of("water"), 0)

    def test_bench_to_active(self):
        b, me, ally = self._battle(
            ("energy_move:2:from:bench:to:active",))
        ally.add_energy("grass", 3)
        r = b.step({"verb": "attack", "attack": 0, "target": 1})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(me.total_energy(), 2)
        self.assertEqual(ally.total_energy(), 1)

    def test_opponent_side_move(self):
        b, me, _ = self._battle(
            ("energy_move:1:from:opp_active:to:opp_bench",))
        opp = b.players[1]
        target = _bench(b, 1, 1)[0]
        opp.active.add_energy("darkness", 1)
        r = b.step({"verb": "attack", "attack": 0, "target": 1})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(opp.active.total_energy(), 0)
        self.assertEqual(target.energy_of("darkness"), 1)

    def test_any_to_any_picks_source_and_target_trainer(self):
        b = _arena()
        me = make_pokemon(PokemonDef("Me", hp=200, ptype="colorless",
                                     attacks=()))
        me.add_energy("fire", 1)
        b.players[0].active = me
        ally = _bench(b, 0, 1)[0]
        b.players[0].hand = [TrainerRef(TrainerDef(
            "energy_move:1:from:any:to:any", kind="item", name="Energy Switch"))]
        r = b.step({"verb": "trainer", "hand": 0, "source": 0, "target": 1})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(ally.total_energy(), 1)
        self.assertEqual(me.total_energy(), 0)

    def test_invalid_target_falls_back_to_first_bench(self):
        """缺省/越界 target 取第一个后备（保证无目标选择时也能执行）"""
        b, me, ally = self._battle(("energy_move:1:from:active:to:bench",))
        me.add_energy("fire", 1)
        r = b.step({"verb": "attack", "attack": 0, "target": 5})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(ally.total_energy(), 1)


class TestToolCostRetreat(unittest.TestCase):
    """S6（2026-10-28）：道具费用 / 撤退修正"""

    def _cost_battle(self, tool, category=""):
        b = _arena()
        atk = AttackDef("T", cost=2, damage=10, effects=())
        fp = make_pokemon(PokemonDef("Holder", hp=100, ptype="colorless",
                                     attacks=(atk,), category=category))
        b.players[0].active = fp
        fp.tool = tool
        return b, fp, atk

    def test_tera_cost_reduced(self):
        b, fp, atk = self._cost_battle("tool:cost_less:1:tera",
                                       category="Tera(Fire)")
        fp.add_energy("fire", 1)
        self.assertTrue(b._can_pay_attack(fp, atk), "费用 2 → 1，1 能量应可支付")

    def test_non_tera_not_reduced(self):
        b, fp, atk = self._cost_battle("tool:cost_less:1:tera", category="")
        fp.add_energy("fire", 1)
        self.assertFalse(b._can_pay_attack(fp, atk))

    def test_counter_gain_only_when_behind(self):
        b, fp, atk = self._cost_battle("tool:cost_less:1:behind")
        fp.add_energy("fire", 1)
        b.players[0].prizes_left, b.players[1].prizes_left = 5, 4
        self.assertTrue(b._can_pay_attack(fp, atk))
        b.players[0].prizes_left = 3
        self.assertFalse(b._can_pay_attack(fp, atk))

    def test_retreat_less_and_low_hp(self):
        b = _arena()
        fp = make_pokemon(PokemonDef("Holder", hp=100, ptype="colorless",
                                     attacks=(), retreat=2))
        b.players[0].active = fp
        fp.tool = "tool:retreat_less:2:lowhp:30"
        self.assertEqual(_retreat_cost(fp, b), 0, "2 − 2 = 0")
        fp.damage = 80
        self.assertEqual(_retreat_cost(fp, b), 0, "HP ≤30 时无撤退费用")

    def test_gravity_gemstone_both_active(self):
        b = _arena()
        mine = make_pokemon(PokemonDef("Mine", hp=100, ptype="colorless",
                                       attacks=(), retreat=1))
        b.players[0].active = mine
        mine.tool = "tool:retreat_plus:1:both"
        b.players[1].active.pdef = PokemonDef("Foe", hp=100, ptype="colorless",
                                              attacks=(), retreat=1)
        self.assertEqual(_retreat_cost(mine, b), 2)
        self.assertEqual(_retreat_cost(b.players[1].active, b), 2,
                         "双方战斗位都 +1")
        mine.tool = None
        mine.tool_ref = None
        self.assertEqual(_retreat_cost(b.players[1].active, b), 1)


class TestOppHandOps(unittest.TestCase):
    """S5（2026-10-28）：对手手牌的随机弃置 / 洗回 / 弃到 N 张"""

    def _battle(self, tag):
        b = _arena()
        b.players[0].hand = [TrainerRef(TrainerDef(tag, kind="item",
                                                  name="Op"))]
        return b, {"verb": "trainer", "hand": 0}

    def test_discard_random(self):
        b, act = self._battle("discard_opp_random:1")
        b.players[1].hand = [EnergyRef(E_FIRE) for _ in range(3)]
        r = b.step(act)
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(b.players[1].hand), 2)
        self.assertEqual(len(b.players[1].discard_refs), 1)

    def test_shuffle_random_into_deck(self):
        b, act = self._battle("shuffle_opp_hand:1")
        b.players[1].hand = [EnergyRef(E_FIRE) for _ in range(2)]
        deck_before = len(b.players[1].deck)
        r = b.step(act)
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(b.players[1].hand), 1)
        self.assertEqual(len(b.players[1].deck), deck_before + 1)

    def test_discard_until_n(self):
        b, act = self._battle("discard_opp_to:2")
        b.players[1].hand = [EnergyRef(E_FIRE) for _ in range(5)]
        r = b.step(act)
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(b.players[1].hand), 2)

    def test_hand_shuffle_same_keeps_count(self):
        b, act = self._battle("opp_shuffle_draw:same")
        b.players[1].hand = [EnergyRef(E_FIRE) for _ in range(4)]
        r = b.step(act)
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(b.players[1].hand), 4, "张数不变，内容随机化")


class TestCoinAll(unittest.TestCase):
    """S6（2026-10-28）：`coin_all:N:<side>:<sub>` —— 全部命中才生效"""

    def _run(self, seed):
        b = _arena(seed=seed)
        _equip(b, ("coin_all:2:heads:dmg_plus:100",), damage=10)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        coins = [e for e in b.events if e.get("type") == "coin"]
        self.assertEqual(len(coins), 2, "N 枚硬币各记一条事件")
        return b.players[1].active.damage, all(
            c["result"] == "heads" for c in coins)

    def test_all_heads_adds_damage(self):
        # 找一个两枚皆正面的 seed
        for seed in range(1, 40):
            dmg, all_heads = self._run(seed)
            if all_heads:
                self.assertEqual(dmg, 110)
                return
        self.fail("未找到两枚皆正面的 seed")

    def test_not_all_heads_no_bonus(self):
        for seed in range(1, 40):
            dmg, all_heads = self._run(seed)
            if not all_heads:
                self.assertEqual(dmg, 10)
                return
        self.fail("未找到非全正面的 seed")


if __name__ == "__main__":
    unittest.main()
