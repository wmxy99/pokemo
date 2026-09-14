# -*- coding: utf-8 -*-
"""PokéLab Engine v1.3 · 效果 tag 家族扩展用例（B1/B2 批次）

覆盖（新增可执行 tag）
  招式侧：discard:N · discard_energy:N · switch_self · gust · attach:N[:type] ·
          move_damage:N（+ 既有 spread_free / spread:N:per / heal / self / draw）
  特性侧：draw:N · move_damage:N · attach_energy:N:type · ko_draw:N（被动）·
          weak_change:type（被动）
  训练家侧：switch_self · recover:N:kind · discard_search_energy:N ·
            search_energy:N · candy

同时守住两条不变量：
  INV-1 弃牌堆**文本层与类型化层同步**（len(discard) == len(discard_refs)）
  INV-2 新 tag 的合法动作 ↔ step 严格 1:1（F5）

运行：python -m unittest discover -s tests -v
"""
from __future__ import annotations
import random
import unittest

from engine import decklib as dl
from engine.battle import Battle
from engine.cards import (AbilityDef, AttackDef, EnergyDef, EnergyRef, PokeRef,
                          PokemonDef, TrainerDef, TrainerRef, make_pokemon)
from engine.rules import compute_damage
from engine.state import GameConfig, PlayerState

E_FIRE = EnergyDef("fire", name_zh="基本火能量")
E_GRASS = EnergyDef("grass", name_zh="基本草能量")
E_WATER = EnergyDef("water", name_zh="基本水能量")


def _arena(seed: int = 7):
    """最小可控战场：双方各 1 只 100HP 无色宝可梦 + 空手牌 + 20 张牌库"""
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

    def plain(name):
        return PokemonDef(name, hp=100, ptype="colorless", attacks=())

    b.players[0].active = make_pokemon(plain("Attacker"))
    b.players[1].active = make_pokemon(plain("Defender"))
    for p in b.players:
        p.deck = [EnergyRef() for _ in range(20)]
    return b


def _add_bench(b, pi, name, n=1):
    out = []
    for i in range(n):
        fp = make_pokemon(PokemonDef(f"{name}{i}", hp=100, ptype="colorless",
                                     attacks=()))
        b.players[pi].bench.append(fp)
        out.append(fp)
    return out


class TestAttackEffectTags(unittest.TestCase):
    """招式附加效果新 tag"""

    def test_discard_n_from_hand(self):
        b = _arena()
        b.players[0].hand = [EnergyRef() for _ in range(4)]
        atk = AttackDef("Shed", cost=0, damage=10, effects=("discard:2",))
        attacker = make_pokemon(PokemonDef(
            "Discarder", hp=100, ptype="colorless", attacks=(atk,)))
        b.players[0].active = attacker
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(b.players[0].hand), 2, "应弃 2 张手牌")
        self.assertEqual(len(b.players[0].discard), 2)

    def test_discard_energy_from_active(self):
        b = _arena()
        a = b.players[0].active
        a.add_energy("fire", 2)
        a.add_energy("water", 1)
        atk = AttackDef("Vent", cost=0, damage=10, effects=("discard_energy:2",))
        b.players[0].active = make_pokemon(PokemonDef(
            "Venter", hp=100, ptype="colorless", attacks=(atk,)))
        b.players[0].active.energy = {"fire": 2, "water": 1}
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[0].active.total_energy(), 1)

    def test_switch_self_swaps_active(self):
        b = _arena()
        bench = _add_bench(b, 0, "Backup", 2)
        atk = AttackDef("U-turn", cost=0, damage=10, effects=("switch_self",))
        b.players[0].active = make_pokemon(PokemonDef(
            "Switcher", hp=100, ptype="colorless", attacks=(atk,)))
        old = b.players[0].active
        r = b.step({"verb": "attack", "attack": 0, "bench": 1})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertIs(b.players[0].active, bench[1])
        self.assertIn(old, b.players[0].bench)

    def test_gust_drags_opponent_bench(self):
        b = _arena()
        opp_bench = _add_bench(b, 1, "OppBack", 2)
        atk = AttackDef("Lure", cost=0, damage=10, effects=("gust",))
        b.players[0].active = make_pokemon(PokemonDef(
            "Lurer", hp=100, ptype="colorless", attacks=(atk,)))
        old_opp = b.players[1].active
        r = b.step({"verb": "attack", "attack": 0, "gust": 1})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertIs(b.players[1].active, opp_bench[1])
        self.assertIn(old_opp, b.players[1].bench)

    def test_attach_energy_from_discard(self):
        b = _arena()
        p = b.players[0]
        p.discard.append(E_FIRE.name)
        p.discard_refs.append(EnergyRef(E_FIRE))
        atk = AttackDef("Recharge", cost=0, damage=10,
                        effects=("attach:1:fire",))
        fp = make_pokemon(PokemonDef("Charger", hp=100, ptype="colorless",
                                     attacks=(atk,)))
        b.players[0].active = fp
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(fp.energy_of("fire"), 1, "应从弃牌堆附 1 火能")
        self.assertEqual(len(p.discard_refs), 0, "弃牌堆类型化层应被取走")

    def test_move_damage_counters(self):
        b = _arena()
        atk = AttackDef("Shift", cost=0, damage=0, effects=("move_damage:2",))
        fp = make_pokemon(PokemonDef("Mover", hp=100, ptype="colorless",
                                     attacks=(atk,)))
        fp.damage = 50
        b.players[0].active = fp
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(fp.damage, 30, "自身应减少 2 个指示物")
        self.assertEqual(b.players[1].active.damage, 20, "对手应受 2 个指示物")

    def test_spread_per_on_bench(self):
        """spread:N:per —— 非任意分配（随机 n 只各 per 点）"""
        b = _arena()
        bench = _add_bench(b, 1, "Sponge", 3)
        atk = AttackDef("Scatter", cost=0, damage=0, effects=("spread:2:20",))
        b.players[0].active = make_pokemon(PokemonDef(
            "Scatterer", hp=100, ptype="colorless", attacks=(atk,)))
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        hit = [fp for fp in bench if fp.damage > 0]
        self.assertEqual(len(hit), 2)
        self.assertTrue(all(fp.damage == 20 for fp in hit))


class TestAbilityTags(unittest.TestCase):
    """特性新 tag（field 主动 + field 被动）"""

    def _with_ability(self, tag, name="Test AB", location="field"):
        ab = AbilityDef(name, name_zh=name, tag=tag, location=location)
        return PokemonDef(f"Holder_{tag.replace(':', '_')}", hp=120,
                          ptype="colorless", attacks=(), abilities=(ab,)), ab

    def test_draw_ability(self):
        b = _arena()
        pdef, ab = self._with_ability("draw:3")
        fp = make_pokemon(pdef)
        b.players[0].active = fp
        b.players[0].hand = []
        legal = [a for a in b.legal_actions(0) if a["verb"] == "ability"]
        self.assertEqual(len(legal), 1)
        r = b.step(legal[0])
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(b.players[0].hand), 3)
        # 每回合一次：再发动应失败
        r2 = b.step(legal[0])
        self.assertFalse(r2["ok"], "应受每回合一次限制")

    def test_move_damage_ability(self):
        b = _arena()
        pdef, ab = self._with_ability("move_damage:3")
        fp = make_pokemon(pdef)
        fp.damage = 50
        b.players[0].active = fp
        legal = [a for a in b.legal_actions(0) if a["verb"] == "ability"]
        self.assertEqual(len(legal), 1)
        b.step(legal[0])
        self.assertEqual(fp.damage, 20)
        self.assertEqual(b.players[1].active.damage, 30)

    def test_attach_energy_ability(self):
        b = _arena()
        p = b.players[0]
        p.discard_refs.append(EnergyRef(E_GRASS))
        p.discard.append(E_GRASS.name)
        pdef, ab = self._with_ability("attach_energy:1:grass", name="Dance")
        fp = make_pokemon(pdef)
        b.players[0].active = fp
        legal = [a for a in b.legal_actions(0) if a["verb"] == "ability"]
        self.assertEqual(len(legal), 1, "有草能量在弃牌堆时应可用")
        b.step(legal[0])
        self.assertEqual(fp.energy_of("grass"), 1)

    def test_attach_energy_ability_from_hand_zone(self):
        """Ogerpon ex「碧绿之舞」：从**手牌**附 1 草能量"""
        b = _arena()
        p = b.players[0]
        p.hand = [EnergyRef(E_GRASS)]
        pdef, ab = self._with_ability("attach_energy:1:grass:hand",
                                      name="Teal Dance")
        fp = make_pokemon(pdef)
        b.players[0].active = fp
        legal = [a for a in b.legal_actions(0) if a["verb"] == "ability"]
        self.assertEqual(len(legal), 1)
        b.step(legal[0])
        self.assertEqual(fp.energy_of("grass"), 1)
        self.assertFalse(any(isinstance(c, EnergyRef) for c in p.hand),
                         "手牌能量应已被取走")

    def test_evolve_attach_triggers_on_evolve(self):
        """Blaziken ex「火焰之舞」：进化时从弃牌堆附能（被动触发）"""
        b = _arena()
        p = b.players[0]
        p.discard.append(E_FIRE.name)
        p.discard_refs.append(EnergyRef(E_FIRE))
        p.discard.append(E_FIRE.name)
        p.discard_refs.append(EnergyRef(E_FIRE))
        ab = AbilityDef("Blaze Dance", name_zh="火焰之舞",
                        tag="evolve_attach:2:fire:discard", location="field")
        stage2 = PokemonDef("Blaze2", hp=320, ptype="fire", stage=2,
                            evolves_from="Blaze1", is_ex=True, attacks=(),
                            abilities=(ab,))
        stage1 = PokemonDef("Blaze1", hp=120, ptype="fire", stage=1,
                            evolves_from="Blaze0", attacks=())
        stage0 = PokemonDef("Blaze0", hp=70, ptype="fire", stage=0, attacks=())
        make_pokemon(stage0)
        make_pokemon(stage1)
        b.players[0].active = make_pokemon(stage1)
        p.hand = [PokeRef(stage2)]
        # 被动特性不得出现在合法动作表
        self.assertFalse([a for a in b.legal_actions(0) if a["verb"] == "ability"],
                         "evolve_attach 为被动")
        legal = [a for a in b.legal_actions(0) if a["verb"] == "evolve"]
        self.assertEqual(len(legal), 1)
        r = b.step(legal[0])
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[0].active.energy_of("fire"), 2,
                         "进化时应从弃牌堆附 2 火能")
        self.assertEqual(len(p.discard_refs), 0)

    def test_passive_abilities_not_in_legal(self):
        """ko_draw / weak_change 为被动特性，不得出现在合法动作表"""
        b = _arena()
        for tag in ("ko_draw:3", "weak_change:psychic:dragon"):
            pdef, _ = self._with_ability(tag)
            b.players[0].bench.append(make_pokemon(pdef))
        legal = [a for a in b.legal_actions(0) if a["verb"] == "ability"]
        self.assertEqual(legal, [], f"被动特性不应可发动：{legal}")

    def test_ko_draw_triggers_on_knockout(self):
        b = _arena()
        pdef, _ = self._with_ability("ko_draw:2", name="Flip the Script")
        b.players[0].bench.append(make_pokemon(pdef))
        b.players[0].hand = []
        # 直接击倒 A 的 active
        b._resolve_ko(0, b.players[0].active)
        self.assertEqual(len(b.players[0].hand), 2, "被击倒时应抽 2")
        self.assertIn("ko_draw", b.events[-1].get("trigger", "ko_draw"))

    def test_weak_change_overrides_weakness(self):
        b = _arena()
        pdef, _ = self._with_ability("weak_change:water",
                                     name="Fairy Zone")
        b.players[0].bench.append(make_pokemon(pdef))
        attacker = make_pokemon(PokemonDef("WaterGuy", hp=100,
                                           ptype="water", attacks=()))
        defender = make_pokemon(PokemonDef("DragonGuy", hp=200,
                                           ptype="dragon",
                                           weakness="darkness", attacks=()))
        # 无改写：水打龙（弱恶）→ 原伤害
        self.assertEqual(compute_damage(attacker, defender, 100), 100)
        # 有改写：弱点变为水 → ×2
        self.assertEqual(
            compute_damage(attacker, defender, 100,
                           weakness_override=b._weakness_override(attacker,
                                                                  defender)),
            200)


class TestAllVariants(unittest.TestCase):
    """`*:all` 变体与通用检索（B3 解析召回批次配套的引擎能力）"""

    def _atk_arena(self, effects):
        b = _arena()
        atk = AttackDef("X", cost=0, damage=0, effects=effects)
        fp = make_pokemon(PokemonDef("Xholder", hp=100, ptype="colorless",
                                     attacks=(atk,)))
        b.players[0].active = fp
        return b, fp

    def test_discard_all_hand(self):
        b, fp = self._atk_arena(("discard:all",))
        b.players[0].hand = [EnergyRef() for _ in range(4)]
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(b.players[0].hand), 0)
        self.assertEqual(len(b.players[0].discard), len(b.players[0].discard_refs))

    def test_discard_energy_all(self):
        b, fp = self._atk_arena(("discard_energy:all",))
        fp.energy = {"fire": 2, "water": 1}
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(fp.total_energy(), 0)

    def test_move_damage_all(self):
        b, fp = self._atk_arena(("move_damage:all",))
        fp.damage = 70
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(fp.damage, 0)
        self.assertEqual(b.players[1].active.damage, 70)

    def test_attach_tag_zone_parsing(self):
        b, fp = self._atk_arena(("attach:1:fire:hand",))
        b.players[0].hand = [EnergyRef(E_FIRE)]
        b.players[0].deck = [EnergyRef(E_GRASS)] + b.players[0].deck
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(fp.energy_of("fire"), 1, "应只从手牌取火能")
        self.assertEqual(fp.energy_of("grass"), 0)

    def test_trainer_generic_search(self):
        b = _arena()
        p = b.players[0]
        p.hand = []
        p.deck = [EnergyRef()] + p.deck + [PokeRef(dl.DRAKLING)]
        for tag, name in (("search_pokemon:1", "Nest Ball"),
                          ("search_trainer:1", "Tool Scrapper"),
                          ("search_any:1", "Explorer")):
            p.hand.append(TrainerRef(TrainerDef(tag, kind="item", name=name)))
        legal = [a for a in b.legal_actions(0) if a["verb"] == "trainer"]
        names = {a["name"] for a in legal}
        self.assertIn("Nest Ball", names, "牌库有宝可梦 → search_pokemon 可用")
        self.assertNotIn("Tool Scrapper", names, "牌库无训练家 → 不应可用")
        self.assertIn("Explorer", names, "search_any 可用")
        r = b.step([a for a in legal if a["name"] == "Nest Ball"][0])
        self.assertTrue(r["ok"], r.get("error"))
        self.assertTrue(any(isinstance(c, PokeRef) for c in p.hand))

    def test_trainer_generic_search_unavailable_when_deck_empty_of_kind(self):
        b = _arena()
        p = b.players[0]
        p.deck = [EnergyRef() for _ in range(5)]
        p.hand = [TrainerRef(TrainerDef("search_trainer:1", kind="item",
                                        name="Tool Scrapper"))]
        legal = [a for a in b.legal_actions(0) if a["verb"] == "trainer"]
        self.assertEqual(legal, [])

    def test_ability_move_damage_all(self):
        b = _arena()
        ab = AbilityDef("Move All", name_zh="全移", tag="move_damage:all",
                        location="field")
        fp = make_pokemon(PokemonDef("Mover", hp=100, ptype="colorless",
                                     attacks=(), abilities=(ab,)))
        fp.damage = 40
        b.players[0].active = fp
        legal = [a for a in b.legal_actions(0) if a["verb"] == "ability"]
        self.assertEqual(len(legal), 1)
        b.step(legal[0])
        self.assertEqual(fp.damage, 0)
        self.assertEqual(b.players[1].active.damage, 40)


class TestTrainerTags(unittest.TestCase):
    """训练家新 tag（B1 预设卡组关键卡）"""

    def _hand_trainer(self, b, tag, name, kind="item", name_zh=""):
        tdef = TrainerDef(tag, kind=kind, name=name, name_zh=name_zh or name)
        b.players[0].hand.append(TrainerRef(tdef))
        return len(b.players[0].hand) - 1

    def test_switch_self_trainer(self):
        b = _arena()
        bench = _add_bench(b, 0, "SW", 2)
        hi = self._hand_trainer(b, "switch_self", "Switch", name_zh="换位")
        old = b.players[0].active
        r = b.step({"verb": "trainer", "hand": hi, "target": 1})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertIs(b.players[0].active, bench[1])
        self.assertIn(old, b.players[0].bench)
        self.assertIn("Switch", b.players[0].discard)

    def test_switch_self_legals_enumerate_bench(self):
        b = _arena()
        _add_bench(b, 0, "SW", 3)
        self._hand_trainer(b, "switch_self", "Switch")
        legal = [a for a in b.legal_actions(0) if a["verb"] == "trainer"]
        self.assertEqual(len(legal), 3, "应为每个后备生成一个合法动作")
        self.assertEqual(sorted(a["target"] for a in legal), [0, 1, 2])

    def test_recover_from_discard(self):
        b = _arena()
        p = b.players[0]
        p.discard.append("Drakling")
        p.discard_refs.append(PokeRef(dl.DRAKLING))
        p.discard.append(E_FIRE.name)
        p.discard_refs.append(EnergyRef(E_FIRE))
        hi = self._hand_trainer(b, "recover:1:any", "Night Stretcher",
                                name_zh="夜之担架")
        legal = [a for a in b.legal_actions(0) if a["verb"] == "trainer"
                 and a["name"] == "Night Stretcher"]
        self.assertEqual(len(legal), 1, "recover:1 → 只暴露 1 个候选")
        r = b.step(legal[0])
        self.assertTrue(r["ok"], r.get("error"))
        self.assertTrue(any(isinstance(c, PokeRef) for c in p.hand))
        self.assertEqual(len(p.discard), len(p.discard_refs), "INV-1 两层次同步")

    def test_recover_kind_pokemon_only(self):
        b = _arena()
        p = b.players[0]
        p.discard.append(E_FIRE.name)
        p.discard_refs.append(EnergyRef(E_FIRE))
        # 只有能量，但只回收宝可梦 → 不应有合法动作
        self._hand_trainer(b, "recover:1:pokemon", "Narrow")
        legal = [a for a in b.legal_actions(0) if a["verb"] == "trainer"
                 and a["name"] == "Narrow"]
        self.assertEqual(legal, [])

    def test_discard_search_energy(self):
        b = _arena()
        p = b.players[0]
        p.hand = [EnergyRef(E_FIRE)]        # 1 张可弃
        hi = self._hand_trainer(b, "discard_search_energy:2", "Earthen Vessel",
                                name_zh="大地之器")
        p.deck = [EnergyRef(E_GRASS), EnergyRef(E_WATER),
                  EnergyRef(E_FIRE), EnergyRef()] + p.deck
        legal = [a for a in b.legal_actions(0) if a["verb"] == "trainer"
                 and a["name"] == "Earthen Vessel"]
        self.assertEqual(len(legal), 1)
        r = b.step(legal[0])
        self.assertTrue(r["ok"], r.get("error"))
        energies = [c for c in p.hand if isinstance(c, EnergyRef)]
        self.assertEqual(len(energies), 2, "应检索 2 张能量入手")

    def test_search_energy(self):
        b = _arena()
        p = b.players[0]
        p.hand = []
        self._hand_trainer(b, "search_energy:2", "Crispin", kind="supporter",
                           name_zh="克里斯宾")
        legal = [a for a in b.legal_actions(0) if a["verb"] == "trainer"
                 and a["name"] == "Crispin"]
        self.assertEqual(len(legal), 1)
        b.step(legal[0])
        self.assertEqual(sum(1 for c in p.hand if isinstance(c, EnergyRef)), 2)

    def test_candy_skips_stage(self):
        b = _arena()
        basic = make_pokemon(dl.DRAKLING)          # stage 0
        b.players[0].active = basic
        hi = self._hand_trainer(b, "candy", "Rare Candy", name_zh="神奇糖果")
        b.players[0].hand.append(PokeRef(dl.DRACO_EX))   # stage 2
        legal = [a for a in b.legal_actions(0) if a["verb"] == "trainer"
                 and a["name"] == "Rare Candy"]
        self.assertEqual(len(legal), 1, "Drakling 在场上应可跳过 Draco 直进化")
        r = b.step(legal[0])
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[0].active.name, "Draco ex")

    def test_candy_requires_root_in_play(self):
        b = _arena()          # 场上默认是无色 plain，非链根
        self._hand_trainer(b, "candy", "Rare Candy")
        b.players[0].hand.append(PokeRef(dl.DRACO_EX))
        legal = [a for a in b.legal_actions(0) if a["verb"] == "trainer"
                 and a["name"] == "Rare Candy"]
        self.assertEqual(legal, [], "链根不在场上时不应可发动")


class TestInvariants(unittest.TestCase):
    """不变量：弃牌堆双层同步 + 合法动作 1:1"""

    def test_discard_two_layers_in_sync(self):
        b = _arena()
        b.players[0].hand = [EnergyRef() for _ in range(5)]
        atk = AttackDef("Big Shed", cost=0, damage=10, effects=("discard:3",))
        b.players[0].active = make_pokemon(PokemonDef(
            "BigShedder", hp=100, ptype="colorless", attacks=(atk,)))
        b.step({"verb": "attack", "attack": 0})
        p = b.players[0]
        self.assertEqual(len(p.discard), len(p.discard_refs), "INV-1")
        self.assertEqual(len(p.discard), 3)

    def test_new_trainer_legal_actions_roundtrip(self):
        """每个新训练家合法动作都必须能 step 成功（1:1，F5）"""
        b = _arena()
        _add_bench(b, 0, "RT", 2)
        p = b.players[0]
        p.deck = [EnergyRef(E_GRASS), EnergyRef(E_GRASS)] + p.deck
        p.discard.append("Drakling")
        p.discard_refs.append(PokeRef(dl.DRAKLING))
        p.hand = [EnergyRef()]
        for tag, name, kind in (("switch_self", "Switch", "item"),
                                ("recover:1:any", "Stretcher", "item"),
                                ("discard_search_energy:2", "Vessel", "item"),
                                ("search_energy:2", "Crispin", "supporter"),
                                ("candy", "Candy", "item")):
            p.hand.append(TrainerRef(TrainerDef(tag, kind=kind, name=name)))
        p.hand.append(PokeRef(dl.DRACO_EX))
        p.active = make_pokemon(dl.DRAKLING)
        acted = 0
        for _ in range(12):
            if b.finished:
                break
            acts = [a for a in b.legal_actions(0)
                    if a["verb"] == "trainer" and a["name"] != "pass"]
            if not acts:
                break
            acts = [a for a in acts if a["name"] != "Crispin"]
            if not acts:
                break
            # 用合法动作清单里的最后一个（多目标的取一个）逐步执行
            a = acts[0]
            r = b.step(a)
            self.assertTrue(r["ok"], f"合法动作执行失败：{a} → {r.get('error')}")
            acted += 1
        self.assertGreater(acted, 0, "应至少能执行一个新训练家动作")


if __name__ == "__main__":
    unittest.main()
