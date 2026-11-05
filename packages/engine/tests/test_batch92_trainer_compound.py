# -*- coding: utf-8 -*-
"""第九十二批（2026-11-05）· 引擎用例：训练家复合动作

覆盖两条 tag：

  · ``evolve_from_deck[:no_ability]`` —— Salvatore TEF 160
    「Search your deck for a card that has no Abilities and evolves from 1 of
     your Pokémon, and put it onto that Pokémon to evolve it.」
    ⚠ 该 tag 此前**三处全缺**（只在攻击白名单 / 只在攻击侧派发 / 无训练家分支）。

  · ``discard_search_multi:<sel>:<k>`` —— Larry's Skill PRE 115
    「Discard your hand and search your deck for a Pokémon, a Supporter card,
     and a Basic Energy card…」
    ⚠ 两条动作必须合成一条复合 tag：`TrainerDef.tag` 单值、派发链只执行一条。

本文件锁定：
  · `:no_ability` 只检索**无特性**的进化卡（带特性的必须跳过）；
  · 牌库无合法目标 → 抛错（不可静默变成「进化了别的卡」）；
  · 复合动作两步都执行（弃手牌 + 各取 1 张），且本卡自身不被算进弃牌数。
"""
from __future__ import annotations
import random
import unittest

from engine.battle import Battle
from engine.cards import (AbilityDef, EnergyDef, EnergyRef, PokeRef, PokemonDef,
                          TrainerDef, TrainerRef, make_pokemon)
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
        PokemonDef("BaseMon", hp=100, ptype="colorless", stage=0, attacks=()))
    b.players[1].active = make_pokemon(
        PokemonDef("Defender", hp=300, ptype="colorless", attacks=()))
    b.players[0].deck = []
    b.players[1].deck = [EnergyRef() for _ in range(10)]
    return b


def _mon(name, stage=0, evolves_from=None, abilities=(), hp=120):
    return PokemonDef(name, hp=hp, ptype="colorless", stage=stage,
                      evolves_from=evolves_from, abilities=tuple(abilities))


def _hand(b, *extra):
    b.players[0].hand = list(extra)
    return b


def _trainer_in_hand(b, tag, name="T"):
    card = TrainerRef(TrainerDef(tag, kind="supporter", name=name))
    b.players[0].hand = [card] + [x for x in b.players[0].hand if x is not card]
    return card


class TestEvolveFromDeckNoAbility(unittest.TestCase):
    def test_picks_evolution_without_abilities(self):
        b = _arena()
        good = _mon("GoodEvo", stage=1, evolves_from="BaseMon")          # 无特性
        bad = _mon("BadEvo", stage=1, evolves_from="BaseMon",
                   abilities=(AbilityDef("SomeAbility", tag="draw:1"),))  # 有特性
        # ⚠ `_evolve_from_deck` 从**牌尾往前**扫描，故把带特性的卡放在末尾，
        # 让「不认 :no_ability 限定」时**必定**先命中它 —— 否则守卫是假的
        # （负向自测实证：反序排列时忽略限定也能通过）。
        b.players[0].deck = [PokeRef(good), PokeRef(bad)]
        _trainer_in_hand(b, "evolve_from_deck:no_ability")
        active = b.players[0].active
        r = b.step({"verb": "trainer", "hand": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(active.name, "GoodEvo",
                         "必须跳过带特性的进化卡（卡面：has no Abilities）")

    def test_without_suffix_may_pick_any(self):
        """负向对照：无 `:no_ability` 时不应排除带特性的进化卡"""
        b = _arena()
        bad = _mon("BadEvo", stage=1, evolves_from="BaseMon",
                   abilities=(AbilityDef("SomeAbility", tag="draw:1"),))
        b.players[0].deck = [PokeRef(bad)]
        _trainer_in_hand(b, "evolve_from_deck")
        r = b.step({"verb": "trainer", "hand": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[0].active.name, "BadEvo")

    def test_no_legal_target_fails_not_silently(self):
        """牌库只有带特性的进化卡 + `:no_ability` → 必须失败，
        不得退化成「进化了那张带特性的卡」（静默放宽）"""
        b = _arena()
        bad = _mon("BadEvo", stage=1, evolves_from="BaseMon",
                   abilities=(AbilityDef("SomeAbility", tag="draw:1"),))
        b.players[0].deck = [PokeRef(bad)]
        _trainer_in_hand(b, "evolve_from_deck:no_ability")
        r = b.step({"verb": "trainer", "hand": 0})
        self.assertFalse(r["ok"], "无合法目标时应失败")
        self.assertEqual(b.players[0].active.name, "BaseMon",
                         "不得进化（失败必须无副作用）")

    def test_consumes_one_card_from_deck(self):
        """进化卡必须**离开牌库**（不是复制）"""
        b = _arena()
        good = _mon("GoodEvo", stage=1, evolves_from="BaseMon")
        b.players[0].deck = [PokeRef(good),
                             EnergyRef(EnergyDef("c1")),
                             EnergyRef(EnergyDef("c2"))]
        _trainer_in_hand(b, "evolve_from_deck:no_ability")
        r = b.step({"verb": "trainer", "hand": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(b.players[0].deck), 2,
                         "进化卡被取走后牌库应少 1 张")


class TestDiscardSearchMulti(unittest.TestCase):
    def _setup(self):
        b = _arena()
        p = b.players[0]
        # 牌库：1 宝可梦 + 1 支援者 + 1 基本能量 + 1 特殊能量（诱饵）
        p.deck = [
            PokeRef(_mon("FoundMon", stage=0)),
            TrainerRef(TrainerDef("draw:2", kind="supporter", name="FoundSup")),
            EnergyRef(EnergyDef("grass")),
            EnergyRef(EnergyDef("fire", tag="senergy:reversal")),
        ]
        # 手牌：训练家卡 + 2 张待弃牌
        _hand(b,
              EnergyRef(EnergyDef("x1")),
              EnergyRef(EnergyDef("x2")))
        _trainer_in_hand(
            b, "discard_search_multi:pokemon+supporter+basic_energy:1")
        return b

    def test_discards_hand_then_takes_one_of_each(self):
        b = self._setup()
        p = b.players[0]
        r = b.step({"verb": "trainer", "hand": 0})
        self.assertTrue(r["ok"], r.get("error"))
        names = sorted(getattr(x, "pdef", None).name if isinstance(x, PokeRef)
                       else (x.tdef.name if isinstance(x, TrainerRef)
                             else f"E:{x.edef.type}")
                       for x in p.hand)
        self.assertEqual(len(p.hand), 3, f"应各取 1 张共 3 张，实际 {names}")
        self.assertIn("FoundMon", names)
        self.assertIn("FoundSup", names)
        self.assertIn("E:grass", names)

    def test_special_energy_not_taken_for_basic_slot(self):
        """`basic_energy` 不得吃掉特殊能量（否则是静默放宽）"""
        b = self._setup()
        p = b.players[0]
        # 清掉基本能量，只留特殊能量
        p.deck = [c for c in p.deck
                  if not (isinstance(c, EnergyRef) and not c.edef.is_special)]
        r = b.step({"verb": "trainer", "hand": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertFalse(any(isinstance(x, EnergyRef) and x.edef.is_special
                             for x in p.hand),
                         "特殊能量不得被 basic_energy 槽位取走")

    def test_old_hand_goes_to_discard(self):
        """原手牌必须真的进弃牌堆（不是凭空消失）"""
        b = self._setup()
        p = b.players[0]
        before = len(p.discard)
        r = b.step({"verb": "trainer", "hand": 0})
        self.assertTrue(r["ok"], r.get("error"))
        # 弃了 2 张旧手牌 + 打出后训练家卡自身
        self.assertGreaterEqual(len(p.discard), before + 3,
                                f"弃牌堆应 +3，实际 {p.discard}")


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
