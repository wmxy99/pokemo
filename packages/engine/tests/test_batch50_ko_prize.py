# -*- coding: utf-8 -*-
"""第五十批（2026-11-04）· 引擎用例：被击倒时「对手少拿奖赏卡」

`ko_prize_minus:<n|all>[:from_ex][:team_<color>][:if_named:<key>]`

⚠ 与 `ko_prize_plus:` 方向相反、挂载方也不同：多拿挂在**取奖赏方**，
少拿挂在**被击倒方**（含同队特性与道具）。四条条件各自独立，缺一不可：
  · `from_ex`     必须是**对手 {ex} 宝可梦的招式伤害**击倒（Mega 也算 ex）
  · `team_<c>`    「1 of your {X} Pokémon」= 同队判定（特性挂在没被击倒的那只上）
  · `if_named`    场上有指定卡（Munkidori 要求 Pecharunt ex）
  · 无前缀        道具形态（Lillie's Pearl）
"""
from __future__ import annotations
import random
import unittest

from engine.battle import Battle
from engine.cards import AbilityDef, EnergyDef, EnergyRef, PokemonDef, make_pokemon
from engine.state import GameConfig, PlayerState


def _arena(seed=7):
    cfg = GameConfig()
    b = object.__new__(Battle)
    b.cfg = cfg
    b.seed = seed
    b.rng = random.Random(seed)
    b.players = [PlayerState(name="A", prizes_left=cfg.prizes),
                 PlayerState(name="B", prizes_left=cfg.prizes)]
    b.current = 0
    b.turn_number = 2
    b.turn_counts = [2, 2]
    b.phase = "main"
    b.finished = False
    b.winner = None
    b.win_reason = None
    b.events = []
    b.stadium = None
    b._ko_by_attack = [0, 0]
    b._ko_from_attack = True
    b.players[0].deck = [EnergyRef(EnergyDef("fire")) for _ in range(5)]
    b.players[1].deck = [EnergyRef(EnergyDef("fire")) for _ in range(5)]
    return b


def _mon(name, ptype="darkness", ex=False, abilities=()):
    d = PokemonDef(name, hp=100, ptype=ptype, attacks=(), abilities=tuple(abilities))
    return make_pokemon(d)


class TestKoPrizeReduction(unittest.TestCase):
    def _setup(self, victim_ab=None, attacker_ex=True, victim_type="darkness"):
        b = _arena()
        p, o = b.players[0], b.players[1]
        o.active = _mon("Attacker", ptype="colorless")
        o.active.pdef = PokemonDef("Attacker", hp=100, ptype="colorless",
                                   attacks=(), is_ex=attacker_ex)
        ab = (AbilityDef("Husk", tag=victim_ab, location="field")
              if victim_ab else None)
        victim = _mon("Victim", ptype=victim_type, abilities=(ab,) if ab else ())
        p.active = victim
        p.bench = []
        return b, p, o, victim

    def test_no_prize(self):
        b, p, o, v = self._setup("ko_prize_minus:all:from_ex")
        self.assertEqual(b._ko_prize_reduction(0, v), 10 ** 6)

    def test_one_fewer_with_ex(self):
        b, p, o, v = self._setup("ko_prize_minus:1:from_ex")
        self.assertEqual(b._ko_prize_reduction(0, v), 1)

    def test_from_ex_requires_ex_attacker(self):
        """非 ex 攻击方 → 条件不成立，不得减少"""
        b, p, o, v = self._setup("ko_prize_minus:1:from_ex", attacker_ex=False)
        self.assertEqual(b._ko_prize_reduction(0, v), 0)

    def test_from_ex_requires_attack(self):
        """不是招式击倒（如指示物/自爆）→ 同样不成立"""
        b, p, o, v = self._setup("ko_prize_minus:1:from_ex")
        b._ko_from_attack = False
        self.assertEqual(b._ko_prize_reduction(0, v), 0)

    def test_team_variant_lives_on_other_mon(self):
        """同队形态：特性挂在**没被击倒**的那只身上也要生效（Mega Gengar）"""
        b = _arena()
        p, o = b.players[0], b.players[1]
        o.active = make_pokemon(PokemonDef("Att", hp=100, ptype="colorless",
                                           attacks=(), is_ex=True))
        p.active = _mon("Victim", ptype="darkness")
        p.bench = [_mon("Gengar", ptype="psychic", abilities=(
            AbilityDef("Shadowy", tag="ko_prize_minus:1:from_ex:team_darkness",
                       location="field"),))]
        self.assertEqual(b._ko_prize_reduction(0, p.active), 1)

    def test_team_variant_wrong_type(self):
        """同队限定按**被击倒那只**的属性判定 —— 属性不符不得减少"""
        b = _arena()
        p, o = b.players[0], b.players[1]
        o.active = make_pokemon(PokemonDef("Att", hp=100, ptype="colorless",
                                           attacks=(), is_ex=True))
        p.active = _mon("Victim", ptype="water")
        p.bench = [_mon("Gengar", ptype="psychic", abilities=(
            AbilityDef("Shadowy", tag="ko_prize_minus:1:from_ex:team_darkness",
                       location="field"),))]
        self.assertEqual(b._ko_prize_reduction(0, p.active), 0)

    def test_if_named_requires_card_in_play(self):
        tag = "ko_prize_minus:1:if_named:pecharunt"
        b, p, o, v = self._setup(tag)
        self.assertEqual(b._ko_prize_reduction(0, v), 0)
        p.bench = [make_pokemon(PokemonDef("Pecharunt ex", hp=100,
                                           ptype="darkness", attacks=()))]
        self.assertEqual(b._ko_prize_reduction(0, v), 1)

    def test_tool_variant(self):
        b, p, o, v = self._setup(None)
        v.tool = "ko_prize_minus:1"
        self.assertEqual(b._ko_prize_reduction(0, v), 1)

    def test_disabled_tool_ignored(self):
        """Jamming Tower：道具失效时该钩子也不生效"""
        b, p, o, v = self._setup(None)
        v.tool = "ko_prize_minus:1"
        v.tool_disabled = True
        self.assertEqual(b._ko_prize_reduction(0, v), 0)

    def test_no_tag_no_reduction(self):
        b, p, o, v = self._setup(None)
        self.assertEqual(b._ko_prize_reduction(0, v), 0)


class TestResolveKoIntegration(unittest.TestCase):
    def test_prizes_left_respects_reduction(self):
        b = _arena()
        p, o = b.players[0], b.players[1]
        o.active = make_pokemon(PokemonDef("Att", hp=100, ptype="colorless",
                                           attacks=(), is_ex=True))
        victim = _mon("Shedinja", ptype="psychic", abilities=(
            AbilityDef("Fragile Husk", tag="ko_prize_minus:all:from_ex",
                       location="field"),))
        p.active = victim
        p.bench = []
        before = o.prizes_left
        b._resolve_ko(0, victim)
        # 「一张也不拿」
        self.assertEqual(o.prizes_left, before)

    def test_baseline_still_takes_prize(self):
        b = _arena()
        p, o = b.players[0], b.players[1]
        o.active = make_pokemon(PokemonDef("Att", hp=100, ptype="colorless",
                                           attacks=()))
        victim = _mon("Plain", ptype="water")
        p.active = victim
        p.bench = []
        before = o.prizes_left
        b._resolve_ko(0, victim)
        self.assertEqual(o.prizes_left, before - 1)


if __name__ == "__main__":
    unittest.main()
