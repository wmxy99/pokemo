# -*- coding: utf-8 -*-
"""第九十五批（2026-11-06）· `team_category` 静态加伤引擎用例

Iron Crown ex「Cobalt Command」：
「Attacks used by your **Future** Pokémon, except any Iron Crown {ex}, do 20 more
damage to your opponent's Active Pokémon」→
`fx:static_dmg:add:20:team_category:future:except:ironcrown`

三条断言分别对应三个容易写错的地方：
  ① 子集范围：只对 `category` 含 future 的攻击者生效（全体加伤 = 静默过强）；
  ② 例外：Iron Crown ex **自己**不吃这 20（丢掉例外 = 加强）；
  ③ 来源可以在后备：特性持有者不必是攻击者（Iron Crown 常在后备）。

运行：cd packages/engine && python -m unittest tests.test_batch95_static_category -v
"""
from __future__ import annotations

import random
import unittest

from engine.battle import Battle
from engine.cards import (AbilityDef, AttackDef, EnergyDef, EnergyRef,
                          PokemonDef, make_pokemon)
from engine.state import GameConfig, PlayerState

TAG = "fx:static_dmg:add:20:team_category:future:except:ironcrown"


class _Fixed(random.Random):
    def __init__(self, value: float = 0.5):
        super().__init__(0)
        self.value = value

    def random(self) -> float:
        return self.value


def _arena(attacker_def, source_def, damage=50):
    cfg = GameConfig()
    b = object.__new__(Battle)
    b.cfg = cfg
    b.seed = 7
    b.rng = _Fixed()
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
    b._ko_by_attack = [0, 0]
    b.players[0].active = make_pokemon(attacker_def)
    # 特性持有者放在**后备**：确认「来源 ≠ 攻击者」也能生效
    b.players[0].bench.append(make_pokemon(source_def))
    b.players[1].active = make_pokemon(
        PokemonDef("Defender", hp=400, ptype="colorless"))
    for p in b.players:
        p.deck = [EnergyRef(EnergyDef("colorless")) for _ in range(20)]
    return b


def _hit(b) -> int:
    before = b.players[1].active.damage
    r = b.step({"verb": "attack", "attack": 0})
    assert r["ok"], r.get("error")
    return b.players[1].active.damage - before


def _attacker(name="FutureMon", category="Future"):
    return PokemonDef(name, hp=200, ptype="psychic", category=category,
                      attacks=(AttackDef("Test", cost=0, damage=50),))


def _source(name="Iron Crown ex"):
    return PokemonDef(name, hp=200, ptype="metal", category="Future",
                      abilities=(AbilityDef("Cobalt Command", tag=TAG,
                                            location="field"),))


class TestTeamCategoryStaticDmg(unittest.TestCase):
    def test_future_attacker_gets_bonus(self):
        self.assertEqual(_hit(_arena(_attacker(), _source())), 70,
                         "Future 攻击者 → 50 + 20")

    def test_non_future_attacker_no_bonus(self):
        self.assertEqual(_hit(_arena(_attacker(category=""), _source())), 50,
                         "非 Future 攻击者 → 不得加伤（子集范围）")

    def test_exception_applies_to_iron_crown_itself(self):
        b = _arena(_attacker(name="Iron Crown ex", category="Future"), _source())
        self.assertEqual(_hit(b), 50,
                         "Iron Crown ex 自己必须被 except 排除（不得 +20）")

    def test_similar_name_not_excluded(self):
        """反例：名字相近但不同（Iron Crowned）不得被当成例外"""
        b = _arena(_attacker(name="Iron Crowned", category="Future"), _source())
        self.assertEqual(_hit(b), 70, "例外只对 Iron Crown ex 生效")


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
