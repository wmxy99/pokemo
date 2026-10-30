# -*- coding: utf-8 -*-
"""第十四批（2026-10-28）· 引擎用例：伤害指示物直放 + Checkup 群体放置

覆盖：
  · ``counters:<n>[:per:<counter>]``                      Haunter / Alakazam
    （指示物**不吃弱点/抗性**，因此不走 damage 字段）
  · ``passive:checkup_spread:<dmg>:<scope>[:except:|:active_only]``  Froslass / Tyranitar
"""
from __future__ import annotations
import random
import unittest

from engine.battle import Battle
from engine.cards import (AbilityDef, AttackDef, EnergyDef, EnergyRef, PokeRef,
                          PokemonDef, make_pokemon)
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
        PokemonDef("Attacker", hp=300, ptype="colorless", attacks=()))
    b.players[1].active = make_pokemon(
        PokemonDef("Defender", hp=300, ptype="colorless", attacks=()))
    for p in b.players:
        p.deck = [EnergyRef() for _ in range(20)]
    return b


def _mon(name, hp=300, ptype="colorless", stage=0, abilities=(), attacks=()):
    return make_pokemon(PokemonDef(
        name, hp=hp, ptype=ptype, stage=stage,
        abilities=tuple(abilities), attacks=tuple(attacks)))


def _ab(tag: str) -> AbilityDef:
    return AbilityDef("A", tag=tag, location="field")


class TestCountersOnActive(unittest.TestCase):
    def test_fixed_counters_ignore_weakness(self):
        """指示物不吃弱点：30 点就是 30 点（弱点照常生效于伤害，不生效于指示物）"""
        b = _arena()
        b.players[0].active = _mon("Haunter", ptype="psychic")
        # 守方弱点是 psychic（×2），但指示物不受影响
        b.players[1].active = make_pokemon(PokemonDef(
            "Defender", hp=300, ptype="psychic", weakness="psychic"))
        b._apply_attack_effects(b.players[0].active, ("counters:3",))
        self.assertEqual(b.players[1].active.damage, 30)

    def test_per_hand(self):
        b = _arena()
        b.players[0].hand = [EnergyRef() for _ in range(3)]
        b._apply_attack_effects(b.players[0].active,
                                ("counters:2:per:own_hand",))
        self.assertEqual(b.players[1].active.damage, 60, "2×3 个指示物")

    def test_zero_hand_places_nothing(self):
        b = _arena()
        b.players[0].hand = []
        b._apply_attack_effects(b.players[0].active,
                                ("counters:2:per:own_hand",))
        self.assertEqual(b.players[1].active.damage, 0)

    def test_counters_can_ko_and_take_prize(self):
        b = _arena()
        b.players[1].active = _mon("Victim", hp=20)
        b.players[1].bench = [_mon("Reserve", hp=100)]
        before = b.players[0].prizes_left
        b._apply_attack_effects(b.players[0].active, ("counters:3",))
        self.assertEqual(b.players[1].active.name, "Reserve")
        self.assertEqual(b.players[0].prizes_left, before - 1)


class TestCheckupSpread(unittest.TestCase):
    def test_ability_all_both_sides_except_self(self):
        b = _arena()
        b.players[0].bench = [_mon("Froslass", abilities=(
            _ab("passive:checkup_spread:10:ability_all:except:froslass"),))]
        with_ab = _mon("HasAbility", abilities=(_ab("passive:x"),))
        without = _mon("NoAbility")
        b.players[0].active = with_ab
        b.players[1].active = _mon("OppAbility", abilities=(_ab("passive:y"),))
        b.players[1].bench = [without]
        b._apply_checkup_spreads(0)
        self.assertEqual(with_ab.damage, 10, "己方有特性者受 1 个指示物")
        self.assertEqual(b.players[1].active.damage, 10, "对手有特性者也受影响")
        self.assertEqual(without.damage, 0, "无特性者不受影响")
        self.assertEqual(b.players[0].bench[0].damage, 0, "Froslass 自己被排除")

    def test_opp_basic_only(self):
        b = _arena()
        b.players[0].active = _mon("Tyranitar", abilities=(
            _ab("passive:checkup_spread:20:opp_basic:active_only"),))
        basic = _mon("Basic", stage=0)
        evo = _mon("Evo", stage=1)
        b.players[1].active = basic
        b.players[1].bench = [evo]
        b._apply_checkup_spreads(0)
        self.assertEqual(basic.damage, 20)
        self.assertEqual(evo.damage, 0, "进化宝可梦不受影响")

    def test_active_only_requires_active(self):
        b = _arena()
        b.players[0].bench = [_mon("Tyranitar", abilities=(
            _ab("passive:checkup_spread:20:opp_basic:active_only"),))]
        b.players[1].active = _mon("Basic", stage=0)
        b._apply_checkup_spreads(0)
        self.assertEqual(b.players[1].active.damage, 0, "在后备不生效")

    def test_no_effect_for_other_owner(self):
        b = _arena()
        b.players[0].bench = [_mon("Froslass", abilities=(
            _ab("passive:checkup_spread:10:ability_all:except:froslass"),))]
        b.players[1].active = _mon("HasAbility", abilities=(_ab("passive:y"),))
        b._apply_checkup_spreads(1)      # 不是拥有者的回合起点
        self.assertEqual(b.players[1].active.damage, 0)


class TestCountersAnyTarget(unittest.TestCase):
    """第二十批（2026-10-29）：指示物直放**任意**对手宝可梦 + 特性自毁

    卡面原型：
      · Dusclops / Dusknoir「诅咒爆裂」：``counters:N:scope:opp:1:selfko``
      · Bramblin / Swirlix / Spectrier：``counters:N:scope:opp:1``
      · Team Rocket's Golbat：``evolve_act:counters:2:scope:opp:1``
    """

    def test_default_target_is_active(self):
        b = _arena()
        b.players[1].bench = [_mon("Reserve", hp=300)]
        b._apply_attack_effects(b.players[0].active,
                                ("counters:5:scope:opp:1",))
        self.assertEqual(b.players[1].active.damage, 50)
        self.assertEqual(b.players[1].bench[0].damage, 0, "默认战斗位优先")

    def test_explicit_snipe_picks_bench(self):
        b = _arena()
        bench = _mon("Reserve", hp=300)
        b.players[1].bench = [bench]
        b._apply_attack_effects(b.players[0].active,
                                ("counters:5:scope:opp:1",),
                                action={"snipe": [1]})
        self.assertEqual(bench.damage, 50)
        self.assertEqual(b.players[1].active.damage, 0)

    def test_any_target_ignores_weakness(self):
        """指示物不吃弱点：打**战斗位**也一样（snipe 走伤害字段会翻倍）"""
        b = _arena()
        b.players[0].active = _mon("Dusknoir", ptype="psychic")
        b.players[1].active = make_pokemon(PokemonDef(
            "Defender", hp=300, ptype="psychic", weakness="psychic"))
        b.players[1].bench = [_mon("Reserve", hp=300)]
        b._apply_attack_effects(b.players[0].active,
                                ("counters:13:scope:opp:1",))
        self.assertEqual(b.players[1].active.damage, 130,
                         "13 个指示物 = 130 点，弱点不得生效")

    def test_bench_ko_takes_prize(self):
        b = _arena()
        bench = _mon("Victim", hp=50)
        b.players[1].bench = [bench, _mon("Reserve", hp=100)]
        before = b.players[0].prizes_left
        b._apply_attack_effects(b.players[0].active,
                                ("counters:5:scope:opp:1",),
                                action={"snipe": [1]})
        self.assertNotIn(bench, b.players[1].bench)
        self.assertEqual(b.players[0].prizes_left, before - 1)

    def test_selfko_knocks_out_holder(self):
        """「If you use this Ability, this Pokémon is Knocked Out.」→ 对手取奖赏"""
        b = _arena()
        holder = _mon("Dusclops", abilities=(
            _ab("counters:5:scope:opp:1:selfko"),))
        b.players[0].active = holder
        before = b.players[1].prizes_left
        b._do_ability(b.players[0], {"name": "A", "tag": holder.pdef.abilities[0].tag,
                                     "pos": 0})
        self.assertEqual(b.players[1].active.damage, 50, "指示物照常放置")
        self.assertIsNone(b.players[0].active, "持有者被击倒")
        self.assertEqual(b.players[1].prizes_left, before - 1, "对手取 1 张奖赏")

    def test_ability_once_per_turn(self):
        b = _arena()
        holder = _mon("Dusknoir", abilities=(_ab("counters:5:scope:opp:1"),))
        b.players[0].active = holder
        b._do_ability(b.players[0], {"name": "A", "pos": 0,
                                     "tag": "counters:5:scope:opp:1"})
        self.assertEqual(b.players[1].active.damage, 50)
        with self.assertRaises(ValueError):
            b._do_ability(b.players[0], {"name": "A", "pos": 0,
                                         "tag": "counters:5:scope:opp:1"})
        self.assertEqual(b.players[1].active.damage, 50, "不得重复放置")

    def test_all_targets_each_opponent_pokemon(self):
        """Uxie：`counters:2:scope:opp:all` → 战斗位与全部后备各 2 个指示物"""
        b = _arena()
        b.players[1].active = make_pokemon(PokemonDef(
            "Defender", hp=300, ptype="psychic", weakness="psychic"))
        b.players[1].bench = [_mon("B1", hp=300), _mon("B2", hp=300)]
        b._apply_attack_effects(b.players[0].active,
                                ("counters:2:scope:opp:all",))
        self.assertEqual(b.players[1].active.damage, 20, "战斗位不吃弱点")
        self.assertEqual(b.players[1].bench[0].damage, 20)
        self.assertEqual(b.players[1].bench[1].damage, 20)

    def test_evolve_act_places_counters(self):
        """Golbat：进化触发一次性放置 2 个指示物（`evolve_act:` 包装）"""
        b = _arena()
        golbat = _mon("Golbat", abilities=(_ab("evolve_act:counters:2:scope:opp:1"),))
        b.players[0].active = golbat
        b._trigger_evolve_attach(golbat)
        self.assertEqual(b.players[1].active.damage, 20)


if __name__ == "__main__":
    unittest.main()
