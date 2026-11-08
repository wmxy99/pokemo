# -*- coding: utf-8 -*-
"""S4 · 效果免疫单点（2026-11-08）

守的四件事
----------
1. **团队范围免疫**（Team Rocket's Articuno「Repelling Veil」）：对手**招式效果**
   不作用于我方**基础**火箭队宝可梦 —— 但**伤害照常**（卡面写死 damage is not
   an effect），且不保护非火箭队 / 进化形态。
2. **训练家来源免疫**（Cetitan ex「Snow Camouflage」/ Fraxure「Unnerve」/
   Antique Sail Fossil「Protective Sail」）：对手从手牌打出的 Item / Supporter
   **效果**不作用于自身。子类别要精确：只写 Supporter 的印刷不挡 Item。
3. **自己对自己不算「对手的效果」**：`_effect_blocked` 必须放行同侧操作，
   否则「自己弃自己能量」会被自己的免疫挡住（静默少做）。
4. **被免疫不是错误**：卡照样打得出，只是效果不结算（不得抛错，否则
   `legal_actions` 给出的动作会失败 = 池内死卡）。

运行：cd packages/engine && python -m unittest tests.test_effect_immune_s4 -v
"""
from __future__ import annotations

import random
import unittest

from engine.battle import Battle
from engine import events
from engine.cards import (AbilityDef, AttackDef, EnergyDef, EnergyRef, PokemonDef,
                          TrainerDef, TrainerRef, make_pokemon)
from engine.state import GameConfig, PlayerState

TR_TEAM = "fx:effect_immune:attack:team:basic_teamrocket"
CETITAN = "fx:effect_immune:trainer:item+supporter:self"
SAIL = "fx:effect_immune:trainer:supporter:self"


def arena(seed: int = 5) -> Battle:
    b = object.__new__(Battle)
    b.cfg = GameConfig()
    b.seed = seed
    b.rng = random.Random(seed)
    b.players = [PlayerState(name="A", prizes_left=6),
                 PlayerState(name="B", prizes_left=6)]
    b.current = 0
    b.turn_number = 2
    b.turn_counts = [2, 1]
    b.phase = "main"
    b._pending = None
    b._defer_end_turn = False
    b._queue = events.EffectQueue()
    b._watch_fired = {}
    b._temp_watch = []
    b._switch_snap = None
    b.finished = False
    b.winner = None
    b.win_reason = None
    b.events = []
    b.stadium = None
    b._stadium_used = [0, 0]
    b._ko_by_attack = [0, 0]
    b._ko_from_attack = False
    b._legacy_used = False
    b._resolving_attack = None
    b._pre_tools_discarded = 0
    for p in b.players:
        p.deck = [EnergyRef(EnergyDef("fire")) for _ in range(8)]
    return b


def _atk(effects, damage=0, name="Test Attack"):
    return AttackDef(name, cost=1, damage=damage, effects=effects)


def _articuno():
    return make_pokemon(PokemonDef(
        "Team Rocket's Articuno", hp=120, ptype="water",
        abilities=(AbilityDef("Repelling Veil", tag=TR_TEAM, location="field"),)))


class TestTeamImmunity(unittest.TestCase):
    """Repelling Veil：我方**基础**火箭队宝可梦免疫对手招式效果（伤害照常）"""

    def _arena(self, victim_name="Team Rocket's Grimer", stage=0):
        b = arena()
        b.players[0].active = make_pokemon(PokemonDef(
            "Attacker", hp=300, ptype="colorless",
            attacks=(_atk(("discard_energy:1:opp",), damage=50),)))
        b.players[0].active.add_energy("colorless")
        victim = make_pokemon(PokemonDef(victim_name, hp=400, ptype="darkness",
                                         stage=stage))
        victim.add_energy("fire")
        b.players[1].active = victim
        b.players[1].bench = [_articuno()]
        return b, victim

    def test_basic_teamrocket_is_protected(self):
        b, victim = self._arena()
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(victim.total_energy(), 1, "基础火箭队宝可梦的能量不被弃")
        self.assertEqual(victim.damage, 50, "**伤害照常**（damage is not an effect）")

    def test_non_teamrocket_not_protected(self):
        b, victim = self._arena(victim_name="Random mon")
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(victim.total_energy(), 0)

    def test_evolved_teamrocket_not_protected(self):
        """卡面限定 **Basic** —— 进化形态不受保护"""
        b, victim = self._arena(victim_name="Team Rocket's Grimer", stage=1)
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(victim.total_energy(), 0)

    def test_holder_itself_is_protected(self):
        """Articuno 自己是基础火箭队宝可梦 —— 同样受自己的光环保护"""
        b = arena()
        b.players[0].active = make_pokemon(PokemonDef(
            "Attacker", hp=300, ptype="colorless",
            attacks=(_atk(("discard_energy:1:opp",), damage=0),)))
        b.players[0].active.add_energy("colorless")
        art = _articuno()
        art.add_energy("water")
        b.players[1].active = art
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(art.total_energy(), 1)


def trainer_arena(tag=CETITAN, target_name="Cetitan ex"):
    """对手（P1）后备有一只带免疫特性的宝可梦，战斗位是普通宝可梦

    ⚠ 用模块级函数而不是 `TestCase` 继承：继承会让父类用例被子类**再跑一遍**，
      动态用例数就会与 `def test_*` 静态计数不一致（`check_doc_counts` 会红）。
    """
    b = arena()
    b.players[0].active = make_pokemon(PokemonDef("Opp", hp=300,
                                                  ptype="colorless"))
    ab = (AbilityDef("Snow Camouflage", tag=tag, location="field"),) if tag else ()
    big = make_pokemon(PokemonDef(target_name, hp=300, ptype="water",
                                  abilities=ab))
    big.senergy.append(EnergyRef(EnergyDef("colorless", name="Mist Energy",
                                           tag="senergy:mist")))
    b.players[1].active = make_pokemon(PokemonDef("Front", hp=100,
                                                  ptype="colorless"))
    b.players[1].bench = [big]
    return b, big


def _supporter(tag):
    return TrainerRef(TrainerDef(tag=tag, kind="supporter", name="S", name_zh=""))


class TestTrainerImmunity(unittest.TestCase):
    """Snow Camouflage / Unnerve / Protective Sail：对手的 Item/Supporter 效果不作用"""

    def test_immune_to_gust(self):
        b, big = trainer_arena()
        b.players[0].hand = [_supporter("gust")]
        r = b.step({"verb": "trainer", "hand": 0, "target": 0})
        self.assertTrue(r["ok"], r)
        self.assertEqual(b.players[1].active.name, "Front", "免疫者不得被拉出来")
        self.assertTrue([e for e in b.events if e["type"] == "blocked"])

    def test_not_immune_to_gust_without_tag(self):
        b, big = trainer_arena(tag="")
        b.players[0].hand = [_supporter("gust")]
        b.step({"verb": "trainer", "hand": 0, "target": 0})
        self.assertEqual(b.players[1].active.name, "Cetitan ex")

    def test_item_discard_blocked_but_card_playable(self):
        """被免疫 ≠ 卡打不出：效果不结算，但**不得抛错**（否则是池内死卡）"""
        b, big = trainer_arena()
        b.players[0].hand = [TrainerRef(TrainerDef(tag="discard_senergy:1",
                                                   kind="item", name="Hammer",
                                                   name_zh=""))]
        r = b.step({"verb": "trainer", "hand": 0, "target": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(big.senergy), 1, "特殊能量不得被弃")

    def test_supporter_only_print_does_not_block_items(self):
        """Protective Sail 只写 Supporter → 对手的 **Item** 照常生效"""
        b, big = trainer_arena(tag=SAIL)
        b.players[0].hand = [TrainerRef(TrainerDef(tag="discard_senergy:1",
                                                   kind="item", name="Hammer",
                                                   name_zh=""))]
        r = b.step({"verb": "trainer", "hand": 0, "target": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(big.senergy), 0, "Item 不在免疫范围内")

    def test_own_trainer_not_blocked(self):
        """自己对自己做的事不算「对手的效果」"""
        b, big = trainer_arena()
        self.assertFalse(b._effect_blocked(big, "trainer", 1, "supporter"),
                         "同侧操作必须放行（否则自己的卡会被自己的免疫挡住）")
        self.assertTrue(b._effect_blocked(big, "trainer", 0, "item"))
        # 忘传卡类别 → 直接抛错（静默失效比报错更危险）
        with self.assertRaises(ValueError):
            b._effect_blocked(big, "trainer", 0)


class TestGuardWiring(unittest.TestCase):
    """守卫生效点：至少这几种训练家效果都要经过 `_effect_blocked`"""

    def test_energy_move_from_opponent_blocked(self):
        b, big = trainer_arena()
        big.add_energy("water")
        b.players[0].hand = [TrainerRef(TrainerDef(
            tag="energy_move:1:from:opp_active:to:bench", kind="item",
            name="Move", name_zh=""))]
        b.players[1].bench = [big, make_pokemon(PokemonDef("B", hp=100,
                                                           ptype="colorless"))]
        b.players[1].active = big
        detail = b._apply_energy_move("energy_move:1:from:opp_active:to:bench",
                                      None, {}, source="trainer",
                                      kinds="item")
        self.assertIn("blocked", detail)
        self.assertEqual(big.total_energy(), 2, "water 1 + Mist Energy 1 都不动")
