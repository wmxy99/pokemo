# -*- coding: utf-8 -*-
"""PokéLab Engine · 「检视牌库顶」扩展用例（2026-11-02 第三十五批）

本批把 ``recon:`` 补了三种能力，覆盖 6 张真实卡：

  1. ``K = all``（「any number of … you find there」）—— 备选集合被「牌库顶 N 张」
     封死，不是开放式选择，所以不需要交互层：取满即可。
       · Lapras ex SCR 32 / Metang TEF 114（能量 → 自由分配附着）
       · Tatsugiri ex SSP 142（宝可梦 → 铺后备）
  2. ``dest:discard``（「look at the top card. You may discard that card」）
       · Morpeko TWM 72 / Rockruff JTG 84 / Litwick WHT 16
     ⚠ 与 ``rest:discard``（Explorer's Guidance 的「discard **the other** cards」）
       **不是一回事**：这里弃的是**被选中的**那张。
  3. ``rest:shuffle_bottom``（「Shuffle the other cards **and put them on the
     bottom** of your deck」）—— 只洗余牌再整叠放底。用 ``shuffle`` 会把余牌
     混进整副牌库（卡面不符），用 ``bottom`` 又完全不洗。

另守 ``dest:attach_spread`` 的分配口径：与 ``attach_spread:`` 一致
（显式 ``attach_plan`` 或 round-robin），绝不把全部能量堆在 active 上。

运行：cd packages/engine && python -m unittest tests.test_recon_ext -v
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
from engine.cards import (EnergyDef, EnergyRef, PokemonDef,  # noqa: E402
                          make_pokemon)
from engine.state import GameConfig, PlayerState  # noqa: E402

FILLER = 8


def arena(seed: int = 7):
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
    b.players[0].active = make_pokemon(
        PokemonDef("Holder", hp=200, ptype="colorless", attacks=()))
    b.players[0].bench = []
    b.players[1].active = make_pokemon(
        PokemonDef("Opp", hp=200, ptype="colorless", attacks=()))
    b.players[1].bench = []
    for p in b.players:
        p.deck = [EnergyRef() for _ in range(FILLER)]
    return b


def _energy(color: str = "grass", special: bool = False):
    if special:
        return EnergyRef(EnergyDef(color, name="Spec E", tag="senergy:test"))
    return EnergyRef(EnergyDef(color, name=f"{color} Energy"))


def _poke(name: str, ptype: str = "colorless"):
    from engine.cards import PokeRef
    return PokeRef(PokemonDef(name, hp=100, ptype=ptype))


def _push(b, *cards):
    """按**给定顺序**压到牌库顶：第一个参数是最上面那张。"""
    for c in reversed(cards):
        b.players[0].deck.append(c)


def _bench(b, n: int):
    b.players[0].bench = [make_pokemon(
        PokemonDef(f"B{i}", hp=100, ptype="colorless", attacks=()))
        for i in range(n)]


class TestParseAll(unittest.TestCase):
    """`K = all` 的 tag 解析"""

    def test_all_maps_to_full_scan(self):
        spec = _parse_recon_tag("recon:20:all:energy:dest:attach_spread")
        self.assertEqual(spec["look_top"], 20)
        self.assertNotEqual(spec["max"], 1)
        self.assertGreaterEqual(spec["max"], 20, "`all` 必须能覆盖整段 top N")
        self.assertEqual(spec["dest"], "attach_spread")

    def test_legacy_unchanged(self):
        """旧口径 `recon:<N>` 仍是 legacy（行为冻结）"""
        self.assertTrue(_parse_recon_tag("recon:7")["legacy"])
        self.assertFalse(_parse_recon_tag("recon:7:1:supporter")["legacy"])

    def test_bad_k_still_raises(self):
        with self.assertRaises(ValueError):
            _parse_recon_tag("recon:4:x:any")


class TestDestDiscard(unittest.TestCase):
    """「Look at the top card. You may discard that card.」"""

    def test_top_card_goes_to_discard(self):
        b = arena()
        p = b.players[0]
        _push(b, _energy("water"))
        n_deck = len(p.deck)
        b._do_recon(p, "recon:1:1:any:dest:discard", {})
        self.assertEqual(len(p.deck), n_deck - 1)
        self.assertEqual(p.discard, ["water Energy"])
        self.assertEqual(len(p.discard_refs), 1)

    def test_not_into_hand(self):
        """弃掉的牌不得同时进手牌（方向反转）"""
        b = arena()
        p = b.players[0]
        _push(b, _poke("Pikachu"))
        b._do_recon(p, "recon:1:1:any:dest:discard", {})
        self.assertEqual(p.hand, [])

    def test_rest_none_keeps_next_card_on_top(self):
        """只看顶 1 张、`rest:none`：**下一张必须留在牌库顶**（卡面无洗牌句）

        反面教材：早期实现无条件 `shuffle(p.deck)`，会让这张卡顺手重洗整副
        牌库 —— 卡面根本没写 shuffle。
        """
        b = arena()
        p = b.players[0]
        _push(b, _energy("water"), _poke("Pikachu"))
        b._do_recon(p, "recon:1:1:any:dest:discard:rest:none", {})
        self.assertEqual(len(p.deck), FILLER + 1)
        self.assertIsInstance(p.deck[-1], type(_poke("X")),
                              "下一张不在牌库顶 —— 牌库被洗了")


class TestAllMatches(unittest.TestCase):
    """`K = all`：取顶 N 张里**所有**匹配的"""

    def test_takes_every_matching_energy(self):
        b = arena()
        p = b.players[0]
        _bench(b, 1)
        _push(b, _energy("water"), _energy("grass"), _energy("fire"))
        b._do_recon(p, "recon:3:all:energy:dest:attach_spread", {})
        self.assertEqual(p.active.total_energy() + p.bench[0].total_energy(), 3,
                         "3 张能量应**全部**被附着（不是只取 1 张）")

    def test_filter_keeps_non_matching_in_deck(self):
        """限定 `basic_energy-metal` 时，火能量必须留在牌库"""
        b = arena()
        p = b.players[0]
        _bench(b, 1)
        _push(b, _energy("metal"), _energy("fire"), _energy("metal"))
        b._do_recon(
            p, "recon:3:all:basic_energy-metal:dest:attach_spread", {})
        self.assertEqual(p.active.total_energy() + p.bench[0].total_energy(), 2)
        left = [c for c in p.deck if isinstance(c, EnergyRef)
                and c.edef.type == "fire"]
        self.assertEqual(len(left), 1, "火能量不该被附着")

    def test_special_energy_excluded_when_basic(self):
        """卡面写 Basic 时，特殊能量不得被附着（放宽 = 静默加强）"""
        b = arena()
        p = b.players[0]
        _bench(b, 1)
        _push(b, _energy("metal", special=True), _energy("metal"))
        b._do_recon(
            p, "recon:2:all:basic_energy-metal:dest:attach_spread", {})
        self.assertEqual(p.active.total_energy() + p.bench[0].total_energy(), 1)

    def test_takes_all_pokemon_to_bench(self):
        b = arena()
        p = b.players[0]
        _push(b, _poke("A"), _energy("fire"), _poke("B"))
        b._do_recon(p, "recon:3:all:pokemon:dest:bench", {})
        self.assertEqual(len(p.bench), 2)
        self.assertEqual(len(p.hand), 0)

    def test_bench_full_returns_cards_to_deck(self):
        """后备已满 → 未铺成的牌必须回到 rest（不得从牌库消失）"""
        b = arena()
        p = b.players[0]
        _bench(b, b.cfg.max_bench)
        _push(b, _poke("A"), _poke("B"))
        n = len(p.deck)
        b._do_recon(p, "recon:2:all:pokemon:dest:bench", {})
        self.assertEqual(len(p.deck), n, "牌既没进后备也没回牌库 = 静默丢牌")


class TestAttachSpreadDistribution(unittest.TestCase):
    """`dest:attach_spread` 的分配口径（同 `attach_spread:`）"""

    def test_round_robin_not_all_on_active(self):
        b = arena()
        p = b.players[0]
        _bench(b, 1)
        _push(b, _energy("water"), _energy("grass"), _energy("fire"))
        b._do_recon(p, "recon:3:all:energy:dest:attach_spread", {})
        self.assertEqual(p.active.total_energy(), 2)   # active → bench → active
        self.assertEqual(p.bench[0].total_energy(), 1)

    def test_explicit_plan(self):
        b = arena()
        p = b.players[0]
        _bench(b, 2)
        _push(b, _energy("water"), _energy("grass"))
        b._do_recon(p, "recon:2:all:energy:dest:attach_spread",
                    {"attach_plan": [2, 2]})
        self.assertEqual(p.active.total_energy(), 0, "计划指定了后备就不得附 active")
        self.assertEqual(p.bench[1].total_energy(), 2)

    def test_invalid_plan_raises(self):
        b = arena()
        p = b.players[0]
        _push(b, _energy("water"))
        with self.assertRaises(ValueError):
            b._do_recon(p, "recon:1:all:energy:dest:attach_spread",
                        {"attach_plan": [9]})


class TestRestShuffleBottom(unittest.TestCase):
    """`rest:shuffle_bottom` = 只洗余牌再放底（≠ 洗回整库，也 ≠ 直接放底）"""

    def test_rest_goes_below_filler(self):
        b = arena()
        p = b.players[0]
        _bench(b, 1)
        _push(b, _energy("fire"), _energy("water"))   # 顶：fire，其次 water
        b._do_recon(p, "recon:2:all:energy-fire:dest:attach_spread:rest:shuffle_bottom",
                    {})
        self.assertEqual(p.active.total_energy() + p.bench[0].total_energy(), 1)
        # 余牌（water）应在**牌库底**：牌库顶仍是填充层，不得是 water
        top_type = None
        for c in reversed(p.deck):
            if isinstance(c, EnergyRef) and c.edef.type == "water":
                top_type = "water"
                break
        self.assertIsNotNone(top_type, "余牌回到牌库了（不该丢）")
        self.assertNotEqual(str(p.deck[-1].edef.type), "water",
                            "余牌不该留在牌库顶")

    def test_rest_count_preserved(self):
        b = arena()
        p = b.players[0]
        _bench(b, 1)
        _push(b, _energy("fire"), _energy("water"), _energy("grass"))
        n = len(p.deck)
        b._do_recon(
            p, "recon:3:all:energy-fire:dest:attach_spread:rest:shuffle_bottom",
            {})
        self.assertEqual(len(p.deck), n - 1, "只应少掉被附着的那 1 张")


if __name__ == "__main__":
    unittest.main()
