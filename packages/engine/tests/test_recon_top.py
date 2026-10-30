# -*- coding: utf-8 -*-
"""PokéLab Engine · 「检视牌库顶」用例（2026-10-30 第二十批）

覆盖 ``recon:<N>:<K>:<sel>[:dest:<d>][:rest:<r>][:gate:<g>]`` 的筛选、三种去向
（入手 / 铺场 / 附着）、三种余牌去向（洗回 / 放底 / 弃置）、两种打出前置条件，
以及旧口径 ``recon:<N>`` 的行为冻结守卫（池内已有卡依赖它，不得改语义）。

运行：cd packages/engine && python -m unittest tests.test_recon_top -v
"""
from __future__ import annotations

import random
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG = HERE.parent
for p in (str(PKG), str(PKG.parent / "agent")):
    if p not in sys.path:
        sys.path.insert(0, p)

from engine.battle import Battle, _parse_recon_tag  # noqa: E402
from engine.cards import (EnergyDef, EnergyRef, PokeRef, PokemonDef,  # noqa: E402
                          TrainerDef, TrainerRef, make_pokemon)
from engine.state import GameConfig, PlayerState  # noqa: E402

FILLER = 12          # 牌库底部的填充张数（无色能量）


def arena(seed: int = 7, turn: int = 2):
    """最小可控战场：双方各 1 只 200HP 无色宝可梦；玩家 0 的牌库由用例填充。"""
    cfg = GameConfig()
    b = object.__new__(Battle)
    b.cfg = cfg
    b.seed = seed
    b.rng = random.Random(seed)
    b.players = [PlayerState(name="A", prizes_left=cfg.prizes),
                 PlayerState(name="B", prizes_left=cfg.prizes)]
    b.current = 0
    b.turn_number = 1
    b.turn_counts = [turn, turn]
    b.phase = "main"
    b.finished = False
    b.winner = None
    b.win_reason = None
    b.events = []
    b.stadium = None
    b._ko_by_attack = [0, 0]        # `_ko_flags()` 的惰性字段（本文件直接构造战场）
    b.players[0].active = make_pokemon(
        PokemonDef("Holder", hp=200, ptype="colorless", attacks=()))
    b.players[1].active = make_pokemon(
        PokemonDef("Opp", hp=200, ptype="colorless", attacks=()))
    for p in b.players:
        p.deck = [EnergyRef() for _ in range(FILLER)]
    return b


def _trainer(b, tag: str, kind: str = "item", name: str = "T"):
    """把一张指定 tag 的训练家卡放进玩家 0 手牌并返回。"""
    ref = TrainerRef(TrainerDef(tag, kind=kind, name=name))
    b.players[0].hand = [ref]
    return ref


def _poke(name: str, ptype: str = "colorless"):
    return PokeRef(PokemonDef(name, hp=100, ptype=ptype))


def _energy(color: str = "grass"):
    return EnergyRef(EnergyDef(color, name=f"{color} Energy"))


def _supporter(name: str = "Prof"):
    return TrainerRef(TrainerDef("research:5", kind="supporter", name=name))


def _push(b, *cards):
    """按**给定顺序**压到牌库顶：第一个参数是最上面那张。"""
    for c in reversed(cards):
        b.players[0].deck.append(c)


def _legals(b):
    return [a for a in b.legal_actions() if a["verb"] == "trainer"]


class TestReconHand(unittest.TestCase):
    """去向 = 入手"""

    def test_supporter_filter_takes_only_supporter(self):
        b = arena()
        _trainer(b, "recon:7:1:supporter")
        _push(b, _energy(), _energy(), _supporter("Iono"), _energy())
        b.step({"verb": "trainer", "hand": 0})
        hand = b.players[0].hand
        self.assertEqual([c.tdef.name for c in hand if isinstance(c, TrainerRef)],
                         ["Iono"])
        self.assertEqual(len(hand), 1)
        self.assertEqual(len(b.players[0].deck), FILLER + 4 - 1)

    def test_supporter_beyond_top_n_not_taken(self):
        # 第 9 张的 Supporter 在「看 7 张」的范围外
        b = arena()
        _trainer(b, "recon:7:1:supporter")
        _push(b, *([_energy()] * 8), _supporter("Far"))
        b.step({"verb": "trainer", "hand": 0})
        self.assertFalse(any(isinstance(c, TrainerRef)
                             for c in b.players[0].hand))

    def test_mixed_union_grass_pokemon_or_basic_grass_energy(self):
        b = arena()
        _trainer(b, "recon:7:2:pokemon-grass+energy-grass")
        _push(b, _energy("fire"), _poke("FireGuy", "fire"), _poke("GrassGuy",
                                                                  "grass"),
              _energy("grass"), _energy("grass"))
        b.step({"verb": "trainer", "hand": 0})
        got = b.players[0].hand
        self.assertEqual(len(got), 2, "至多 2 张，且只认草系宝可梦/草能量")
        self.assertTrue(any(isinstance(c, PokeRef) and c.pdef.name == "GrassGuy"
                            for c in got))
        self.assertTrue(any(isinstance(c, EnergyRef)
                            and c.edef.type == "grass" for c in got))

    def test_one_each_never_returns_two_pokemon(self):
        # Drayton「a Pokémon and a Trainer card」——不能退化成「任意 2 张」
        b = arena()
        _trainer(b, "recon:7:2:pokemon+trainer@each", kind="supporter")
        _push(b, _poke("P1", "water"), _poke("P2", "water"),
              _supporter("Iono"))
        b.step({"verb": "trainer", "hand": 0})
        kinds = [type(c).__name__ for c in b.players[0].hand]
        self.assertEqual(sorted(kinds), ["PokeRef", "TrainerRef"],
                         "必须是 1 只宝可梦 + 1 张训练家")

    def test_one_each_does_not_fill_when_one_side_missing(self):
        b = arena()
        _trainer(b, "recon:7:2:pokemon+trainer@each", kind="supporter")
        _push(b, _poke("P1", "water"), _poke("P2", "water"))
        b.step({"verb": "trainer", "hand": 0})
        kinds = [type(c).__name__ for c in b.players[0].hand]
        self.assertEqual(kinds, ["PokeRef"], "没有训练家时只拿 1 只，不得补第 2 只")

    def test_rest_discard_puts_leftovers_in_discard(self):
        # Explorer's Guidance：取 2 张入手，**其余弃置**（不是洗回牌库）
        b = arena()
        _trainer(b, "recon:6:2:any:rest:discard", kind="supporter")
        _push(b, *([_energy()] * 6))
        b.step({"verb": "trainer", "hand": 0})
        self.assertEqual(len(b.players[0].hand), 2)
        # 4 张余牌 + 本卡自身（支援者用完进弃牌堆）
        self.assertEqual(len(b.players[0].discard_refs), 5)
        self.assertEqual(len(b.players[0].deck), FILLER)


class TestReconDestinations(unittest.TestCase):
    """去向 = 铺场 / 附着"""

    def test_bench_puts_selected_pokemon_on_bench(self):
        b = arena()
        _trainer(b, "recon:7:1:pokemon-darkness:dest:bench:rest:bottom",
                 kind="supporter")
        _push(b, _energy(), _poke("GrassGuy", "grass"),
              _poke("DarkGuy", "darkness"), _energy())
        b.step({"verb": "trainer", "hand": 0})
        self.assertEqual([fp.name for fp in b.players[0].bench], ["DarkGuy"])
        self.assertEqual(len(b.players[0].hand), 0, "不得顺手进手牌")

    def test_bench_full_keeps_card_in_deck(self):
        b = arena()
        _trainer(b, "recon:7:1:pokemon-darkness:dest:bench", kind="supporter")
        for i in range(b.cfg.max_bench):
            b.players[0].bench.append(
                make_pokemon(PokemonDef(f"B{i}", hp=60, ptype="colorless")))
        _push(b, _poke("DarkGuy", "darkness"))
        b.step({"verb": "trainer", "hand": 0})
        self.assertEqual(len(b.players[0].bench), b.cfg.max_bench)
        # 后备已满 → 这只没被拿走，仍在牌库里
        self.assertTrue(any(isinstance(c, PokeRef) and c.pdef.name == "DarkGuy"
                            for c in b.players[0].deck))

    def test_attach_destination_adds_energy(self):
        b = arena()
        _trainer(b, "recon:6:1:basic_energy:dest:attach", kind="supporter")
        _push(b, _energy("fire"))
        b.step({"verb": "trainer", "hand": 0})
        self.assertEqual(b.players[0].active.energy.get("fire"), 1)


class TestReconGates(unittest.TestCase):
    """打出前置条件：条件不满足时**不得出现在合法动作表**里"""

    def test_ko_last_turn_gate_blocks_without_ko(self):
        b = arena()
        _trainer(b, "recon:8:3:any:gate:ko_last_turn", kind="supporter")
        self.assertEqual(_legals(b), [])
        b._ko_by_attack[0] = 1
        self.assertEqual(len(_legals(b)), 1)

    def test_not_first_turn_gate(self):
        b = arena(turn=1)
        _trainer(b, "recon:7:1:pokemon-darkness:dest:bench:rest:bottom:"
                    "gate:not_first_turn", kind="supporter")
        self.assertEqual(_legals(b), [], "首回合不可打出")
        b.turn_counts[0] = 2
        self.assertEqual(len(_legals(b)), 1)

    def test_empty_deck_not_legal(self):
        b = arena()
        _trainer(b, "recon:7:1:supporter")
        b.players[0].deck = []
        self.assertEqual(_legals(b), [])

    def test_step_rejects_when_gate_fails(self):
        b = arena(turn=1)
        _trainer(b, "recon:7:1:pokemon-darkness:dest:bench:rest:bottom:"
                    "gate:not_first_turn", kind="supporter")
        res = b.step({"verb": "trainer", "hand": 0})
        # `step` 把 ValueError 收敛成 {"ok": False, "error": …}（不向上抛）
        self.assertFalse(res["ok"])
        self.assertIn("前置条件", res["error"])


class TestReconLegacyFrozen(unittest.TestCase):
    """旧口径 `recon:<N>` 行为冻结（池内已有卡依赖：随机 1 张入手、其余放底）"""

    def test_legacy_semantics_unchanged(self):
        b = arena()
        _trainer(b, "recon:3")
        _push(b, _energy(), _energy(), _energy())
        b.step({"verb": "trainer", "hand": 0})
        self.assertEqual(len(b.players[0].hand), 1, "取 1 张入手")
        self.assertEqual(len(b.players[0].deck), FILLER + 2, "其余放回牌库")
        self.assertEqual(b.events[-1]["detail"], "top3 pick1")

    def test_unknown_selector_rejected(self):
        # 解析阶段允许（只是字符串），**执行时必须拒绝**（未知筛选项不猜、不放行）
        b = arena()
        _trainer(b, "recon:7:1:bogus")
        _push(b, _energy())
        res = b.step({"verb": "trainer", "hand": 0})
        self.assertFalse(res["ok"])
        self.assertIn("recon", res["error"])

    def test_unknown_gate_rejected(self):
        b = arena()
        _trainer(b, "recon:7:1:any:gate:bogus")
        _push(b, _energy())
        res = b.step({"verb": "trainer", "hand": 0})
        self.assertFalse(res["ok"])
        self.assertIn("前置条件", res["error"])


if __name__ == "__main__":
    unittest.main()
