# -*- coding: utf-8 -*-
"""第九批（2026-10-28）· 引擎用例：全量回复 / 招式击倒 / 场地形态 / 牌库顶重排

覆盖卡面原型（每条都必须**精确**结算，不得退化成「回 M 点 / 击倒战斗位 / 挡全部」）：

  · ``heal:all[:<scope>][:type:<c>]``      Whimsicott「Healing Fluff」/ Miltank（coin_all）
  · ``knockout:<scope>``                   Hydrapple / Annihilape / Glaceon ex /
                                           Alolan Exeggutor ex / Team Rocket's Exeggutor
  · ``shuffle_top:N:opp``                  Team Rocket's Dottler
  · ``stadium:attack_plus:N:faction:<k>``  Postwick（Hop's）
  · ``stadium:dmg_reduce:N:faction:<k>``   Granite Cave（Steven's）
  · ``stadium:no_retreat:faction:<k>``     N's Castle
  · ``stadium:status_immune:require:energy``  Festival Grounds
  · ``stadium:evolve_same_turn:type:grass``   Forest of Vitality
  · ``stadium:dmg_immune:box:none:from:rulebox`` Neutralization Zone
  · ``stadium:no_tool_effect``             Jamming Tower
"""
from __future__ import annotations
import random
import unittest

from engine.battle import Battle, _retreat_cost
from engine.cards import (AbilityDef, AttackDef, EnergyDef, EnergyRef,
                          PokeRef, PokemonDef, make_pokemon)
from engine.state import GameConfig, PlayerState

E_GRASS = EnergyDef("grass", name_zh="基本草能量")


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
        PokemonDef("Attacker", hp=300, ptype="colorless", attacks=()))
    b.players[1].active = make_pokemon(
        PokemonDef("Defender", hp=300, ptype="colorless", attacks=()))
    for p in b.players:
        p.deck = [EnergyRef() for _ in range(20)]
    return b


def _mon(name, hp=300, ptype="colorless", stage=0, is_ex=False, abilities=(),
         attacks=()):
    return make_pokemon(PokemonDef(
        name, hp=hp, ptype=ptype, stage=stage, is_ex=is_ex,
        abilities=tuple(abilities), attacks=tuple(attacks)))


def _atk(damage=100, effects=()):
    return AttackDef("Test Atk", cost=0, damage=damage, effects=tuple(effects))


def _stadium(b, tag: str):
    b.stadium = {"name": "S", "tag": tag, "owner": 0}
    b._refresh_stadium_hp()          # 场地进入后统一重算（HP / 道具失效标志）


class TestHealAll(unittest.TestCase):
    def test_full_heal_bench(self):
        b = _arena()
        b.players[0].active = _mon("Healer", attacks=(_atk(0, ("heal:all:bench",)),))
        hurt = _mon("Bench1", hp=100)
        hurt.damage = 80
        b.players[0].bench.append(hurt)
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(hurt.damage, 0, "全量回复应回满（不是回 N 点）")

    def test_full_heal_does_not_touch_others(self):
        b = _arena()
        b.players[0].active = _mon("Healer", attacks=(_atk(0, ("heal:all:bench",)),))
        bench = _mon("Bench1", hp=100)
        bench.damage = 10
        active_hurt = b.players[0].active
        active_hurt.damage = 40
        b.players[0].bench.append(bench)
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(bench.damage, 0)
        self.assertEqual(active_hurt.damage, 40, "范围外不应被回复")

    def test_full_heal_choose_uses_action_target(self):
        b = _arena()
        b.players[0].active = _mon("Healer", attacks=(_atk(0, ("heal:all:choose",)),))
        b1, b2 = _mon("B1", hp=100), _mon("B2", hp=100)
        b1.damage = b2.damage = 50
        b.players[0].bench.extend([b1, b2])
        b.step({"verb": "attack", "attack": 0, "target": 2})
        self.assertEqual(b2.damage, 0, "action.target 指定第二只后备")
        self.assertEqual(b1.damage, 50)


class TestKnockout(unittest.TestCase):
    def test_knockout_opp_active(self):
        b = _arena()
        b.players[1].bench.append(_mon("Reserve", hp=100))
        b.players[0].active = _mon(
            "Killer", attacks=(_atk(0, ("knockout:opp_active",)),))
        before = b.players[0].prizes_left          # 奖赏由**击倒方**领取
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[0].prizes_left, before - 1, "击倒应给 1 张奖赏")
        self.assertIsNotNone(b.players[1].active)
        self.assertEqual(b.players[1].active.name, "Reserve", "后备应补位")

    def test_knockout_is_logged_as_attack_ko(self):
        """被招式击倒 → 事件日志里必须有 `knockout`（供回放/审计）"""
        b = _arena()
        b.players[1].bench.append(_mon("Reserve"))
        b.players[0].active = _mon(
            "Killer", attacks=(_atk(0, ("knockout:opp_active",)),))
        b.step({"verb": "attack", "attack": 0})
        kos = [e for e in b.events if e.get("type") == "knockout"]
        self.assertEqual(len(kos), 1)
        self.assertEqual(kos[0]["victim_owner"], 1)
        self.assertEqual(kos[0]["pos"], "active")

    def test_knockout_both_active(self):
        b = _arena()
        b.players[0].bench.append(_mon("MyReserve", hp=100))
        b.players[1].bench.append(_mon("OppReserve", hp=100))
        b.players[0].active = _mon(
            "Fighter", attacks=(_atk(0, ("knockout:both_active",)),))
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.name, "OppReserve")
        self.assertEqual(b.players[0].active.name, "MyReserve")

    def test_knockout_exactly_counters(self):
        """Glaceon ex：只击倒「恰好 N 个指示物」的目标；不满足则无效果"""
        b = _arena()
        b.players[1].active.damage = 60
        b.players[1].bench.append(_mon("Exact", hp=100))
        b.players[1].bench[-1].damage = 60
        b.players[0].active = _mon(
            "Killer", attacks=(_atk(0, ("knockout:opp_counters:6",)),))
        b.step({"verb": "attack", "attack": 0})
        # 默认目标：战斗位优先（恰好 6 个指示物 → 被击倒）
        self.assertEqual(b.players[1].active.name, "Exact")

    def test_knockout_counters_no_target(self):
        b = _arena()
        b.players[1].active.damage = 10
        b.players[1].bench.append(_mon("B", hp=100))
        b.players[0].active = _mon(
            "Killer", attacks=(_atk(0, ("knockout:opp_counters:6",)),))
        before = b.players[1].prizes_left
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].prizes_left, before, "无满足目标 → 不击倒")

    def test_knockout_bench_basic(self):
        b = _arena()
        b.players[1].bench.append(_mon("BasicBench", hp=100))
        b.players[1].bench.append(_mon("Stage1Bench", hp=100, stage=1))
        b.players[0].active = _mon(
            "Killer", attacks=(_atk(0, ("knockout:opp_bench:basic",)),))
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(len(b.players[1].bench), 1)
        self.assertEqual(b.players[1].bench[0].name, "Stage1Bench")

    def test_knockout_respects_effect_immunity(self):
        """击倒是招式**效果**：目标写明了效果免疫则不应被击倒"""
        guard = _mon("Guard", hp=100,
                     abilities=(AbilityDef(
                         "Imm", tag="fx:effect_immune:attack:self",
                         location="field"),))
        b = _arena()
        b.players[1].active = guard
        b.players[0].active = _mon(
            "Killer", attacks=(_atk(0, ("knockout:opp_active",)),))
        before = b.players[1].prizes_left
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].prizes_left, before)

    def test_knockout_via_coin_sub_tag(self):
        """coin:heads:knockout:opp_active（Iono's Electrode 的下半段）"""
        b = _arena()
        b.players[1].bench.append(_mon("Reserve"))
        b.players[0].active = _mon("Killer")
        b._apply_attack_effects(
            b.players[0].active, ("coin:heads:knockout:opp_active",),
            action={}, pre_mods={"coins": [{"tag": "coin:heads:knockout:opp_active",
                                            "side": "heads",
                                            "sub": "knockout:opp_active",
                                            "result": "heads", "hits": 1}]})
        self.assertEqual(b.players[1].active.name, "Reserve")


class TestShuffleTop(unittest.TestCase):
    def test_shuffle_top_is_permutation(self):
        b = _arena()
        b.players[1].deck = [EnergyRef(edef=E_GRASS) for _ in range(10)]
        # 用可区分的对象标记顺序
        marked = []
        for i in range(10):
            fp = _mon(f"Card{i}", hp=10)
            marked.append(fp)
        b.players[1].deck = marked
        b._apply_attack_effects(b.players[0].active, ("shuffle_top:5:opp",))
        self.assertEqual(len(b.players[1].deck), 10, "不移动任何卡")
        self.assertEqual(sorted(x.name for x in b.players[1].deck[-5:]),
                         sorted(f"Card{i}" for i in range(5, 10)),
                         "只重排顶部 5 张")


class TestStadiumForms(unittest.TestCase):
    def test_attack_bonus_faction(self):
        b = _arena()
        _stadium(b, "stadium:attack_plus:30:faction:hops")
        b.players[0].active = _mon("Hop's Snorlax", attacks=(_atk(100),))
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 130)

    def test_attack_bonus_other_faction_not_affected(self):
        b = _arena()
        _stadium(b, "stadium:attack_plus:30:faction:hops")
        b.players[0].active = _mon("Snorlax", attacks=(_atk(100),))
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 100)

    def test_damage_reduce_faction(self):
        b = _arena()
        _stadium(b, "stadium:dmg_reduce:30:faction:stevens")
        b.players[1].active = _mon("Steven's Carbink", hp=300)
        b.players[0].active = _mon("Hitter", attacks=(_atk(100),))
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 70)

    def test_no_retreat_faction(self):
        b = _arena()
        _stadium(b, "stadium:no_retreat:faction:ns")
        other = PokemonDef("Zoroark", hp=100, ptype="darkness", retreat=2)
        self.assertEqual(_retreat_cost(
            make_pokemon(PokemonDef("N's Zoroark", hp=100, ptype="darkness",
                                    retreat=2)), b), 0)
        self.assertEqual(_retreat_cost(make_pokemon(other), b), 2)

    def test_status_immune_with_energy(self):
        b = _arena()
        _stadium(b, "stadium:status_immune:require:energy")
        b.players[1].active.add_energy("colorless", 1)
        b.players[0].active = _mon("Hitter", attacks=(_atk(10, ("poison",)),))
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.status, set(),
                         "附着能量者不受特殊状态")

    def test_status_applies_without_energy(self):
        b = _arena()
        _stadium(b, "stadium:status_immune:require:energy")
        b.players[0].active = _mon("Hitter", attacks=(_atk(10, ("poison",)),))
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.status, {"poison"})

    def test_status_recovered_at_turn_start(self):
        b = _arena()
        _stadium(b, "stadium:status_immune:require:energy")
        b.players[1].active.add_energy("colorless", 1)
        b.players[1].active.status.add("poison")
        b.current = 1
        b._recover_stadium_statuses()
        self.assertEqual(b.players[1].active.status, set())

    def test_evolve_same_turn_grass(self):
        b = _arena()
        _stadium(b, "stadium:evolve_same_turn:type:grass")
        b.turn_counts[0] = 2
        basic = _mon("Bulbasaur", hp=70, ptype="grass")
        basic.just_played = True
        b.players[0].bench.append(basic)
        b.players[0].hand.append(PokeRef(
            PokemonDef("Ivysaur", hp=100, ptype="grass", stage=1,
                       evolves_from="Bulbasaur")))
        acts = b.legal_actions()
        self.assertTrue(any(a.get("verb") == "evolve" for a in acts),
                        "Forest of Vitality 应允许上场当回合进化")

    def test_evolve_same_turn_first_turn_blocked(self):
        b = _arena()
        _stadium(b, "stadium:evolve_same_turn:type:grass")
        b.turn_counts[0] = 1
        basic = _mon("Bulbasaur", hp=70, ptype="grass")
        basic.just_played = True
        b.players[0].bench.append(basic)
        b.players[0].hand.append(PokeRef(
            PokemonDef("Ivysaur", hp=100, ptype="grass", stage=1,
                       evolves_from="Bulbasaur")))
        acts = b.legal_actions()
        self.assertFalse(any(a.get("verb") == "evolve" for a in acts),
                         "首个回合不放宽")

    def test_stadium_immune_no_rule_box(self):
        b = _arena()
        _stadium(b, "stadium:dmg_immune:box:none:from:rulebox")
        b.players[0].active = _mon("ExAttacker", hp=300, is_ex=True,
                                   attacks=(_atk(100),))
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 0,
                         "无规则框目标不受规则框来源伤害")

    def test_stadium_immune_only_from_rule_box(self):
        b = _arena()
        _stadium(b, "stadium:dmg_immune:box:none:from:rulebox")
        b.players[0].active = _mon("PlainAttacker", attacks=(_atk(100),))
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 100)

    def test_jamming_tower_disables_tool(self):
        b = _arena()
        b.players[1].active.tool = "tool:dmg_reduce:30"
        _stadium(b, "stadium:no_tool_effect")
        b.players[0].active = _mon("Hitter", attacks=(_atk(100),))
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 100, "道具失效 → 不减伤")

    def test_tool_works_without_jamming_tower(self):
        b = _arena()
        b.players[1].active.tool = "tool:dmg_reduce:30"
        b.players[0].active = _mon("Hitter", attacks=(_atk(100),))
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 70)

    def test_jamming_tower_disables_hp_tool(self):
        b = _arena()
        defender = _mon("Guard", hp=100)
        defender.tool = "tool:hp_plus:40"
        b.players[1].active = defender
        _stadium(b, "stadium:no_tool_effect")
        self.assertEqual(defender.max_hp, 100, "道具 +HP 失效")


if __name__ == "__main__":
    unittest.main()
