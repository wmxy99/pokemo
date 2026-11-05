# -*- coding: utf-8 -*-
"""第八十~八十四批（2026-11-05）· 引擎用例

按 class 分批；所有 arena 用 `object.__new__(Battle)` 手工搭最小局面，
不依赖卡库，保证零 I/O 可复跑。
运行：cd packages/engine && python -m unittest tests.test_batch80_84_mechanics -v
"""
from __future__ import annotations

import random
import unittest

from engine.battle import (Battle, _pokemon_to_deck, _search_discard,
                           _card_label)
from engine.cards import (AbilityDef, AttackDef, EnergyDef, EnergyRef,
                          PokemonDef, PokeRef, TrainerDef, TrainerRef,
                          make_pokemon)
from engine.state import GameConfig, PlayerState


class _CountRng(random.Random):
    """记录 shuffle 次数：验证「空集合不洗牌」（静默改状态守卫）"""

    def __init__(self):
        super().__init__(0)
        self.shuffles = 0

    def shuffle(self, x):
        self.shuffles += 1
        return super().shuffle(x)


class _Fixed(random.Random):
    def __init__(self, value: float):
        super().__init__(0)
        self.value = value

    def random(self) -> float:
        return self.value


def _arena(rng, defender_tag=None, defender_hp=100, attack_damage=150,
           attack_effects=()):
    cfg = GameConfig()
    b = object.__new__(Battle)
    b.cfg = cfg
    b.seed = 7
    b.rng = rng
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
    b.players[0].active = make_pokemon(PokemonDef(
        "Attacker", hp=500, ptype="colorless",
        attacks=(AttackDef("Test", cost=0, damage=attack_damage,
                           effects=tuple(attack_effects)),)))
    abilities = ((AbilityDef("Survive", tag=defender_tag, location="field"),)
                 if defender_tag else ())
    b.players[1].active = make_pokemon(PokemonDef(
        "Defender", hp=defender_hp, ptype="fighting", abilities=abilities))
    for p in b.players:
        p.deck = [EnergyRef(EnergyDef("colorless")) for _ in range(20)]
    return b


# ------------------------------ 第八十批 ------------------------------
class TestBatch80Survive(unittest.TestCase):
    """passive:survive:10 —— 满 HP 免击倒、剩 10、不取奖赏、不弃道具"""

    def test_full_hp_survives_with_ten(self):
        b = _arena(_Fixed(0.4), "passive:survive:10")
        before = b.players[0].prizes_left
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.damage, 90)
        self.assertFalse(b.players[1].active.is_ko)
        self.assertEqual(b.players[0].prizes_left, before, "未击倒不取奖赏")

    def test_not_full_hp_is_knocked_out(self):
        """与 coin_survive 的反口径：受伤前不满 HP → 照常击倒"""
        b = _arena(_Fixed(0.4), "passive:survive:10")
        b.players[1].active.damage = 40
        before = b.players[0].prizes_left
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertIsNone(b.players[1].active)
        self.assertEqual(b.players[0].prizes_left, before - 1)

    def test_coin_version_still_ignores_full_hp(self):
        """回归：抛币版不受本次改动影响（不必满 HP 也能免死）"""
        b = _arena(_Fixed(0.4), "passive:coin_survive:10")
        b.players[1].active.damage = 40
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.damage, 90)
        self.assertFalse(b.players[1].active.is_ko)

    def test_malformed_tag_refused(self):
        """未知后缀必须报错（引擎把异常收敛为 ok=False），绝不静默放行"""
        b = _arena(_Fixed(0.4), "passive:survive:notanumber")
        r = b.step({"verb": "attack", "attack": 0})
        self.assertFalse(r["ok"])
        self.assertIn("形态非法", str(r.get("error")))


# ------------------------------ 第八十一批 ------------------------------
class TestBatch81CureSelf(unittest.TestCase):
    """cure:self —— 只清出招者自身的状态，不碰对手、不碰后备"""

    def test_clears_own_status(self):
        b = _arena(_Fixed(0.4), defender_hp=500, attack_damage=10,
                   attack_effects=("cure:self",))
        b.players[0].active.status.add("poison")
        b.players[0].active.status.add("burn")
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[0].active.status, set())

    def test_opponent_status_untouched(self):
        b = _arena(_Fixed(0.4), defender_hp=500, attack_damage=10,
                   attack_effects=("cure:self",))
        b.players[0].active.status.add("poison")
        b.players[1].active.status.add("poison")
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[0].active.status, set())
        self.assertIn("poison", b.players[1].active.status)

    def test_no_status_is_noop(self):
        b = _arena(_Fixed(0.4), defender_hp=500, attack_damage=10,
                   attack_effects=("cure:self",))
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[0].active.status, set())


# ------------------------------ 第八十二批 ------------------------------
class TestBatch82PokemonToDeck(unittest.TestCase):
    """pokemon_to_deck:N —— 只洗宝可梦、至多 N 张、空集合不洗牌"""

    @staticmethod
    def _refs():
        pokes = [PokeRef(PokemonDef("P%d" % i, hp=100, ptype="colorless"))
                 for i in range(6)]
        energies = [EnergyRef(EnergyDef("colorless")) for _ in range(2)]
        return pokes, energies

    def _setup(self, discard_refs):
        b = _arena(_CountRng())
        p = b.players[0]
        p.deck = []
        p.discard_refs = list(discard_refs)
        p.discard = [_card_label(x) for x in discard_refs]
        return b, p

    def test_up_to_five_only_pokemon(self):
        pokes, energies = self._refs()
        b, p = self._setup(pokes + energies)
        moved = _pokemon_to_deck(b, p, "5")
        self.assertEqual(moved, 5)
        self.assertEqual(len(p.deck), 5)
        self.assertTrue(all(isinstance(x, PokeRef) for x in p.deck))
        self.assertEqual(len(p.discard_refs), 3, "剩 1 宝可梦 + 2 能量")
        self.assertEqual(len(p.discard), 3, "文本层同步移除")
        self.assertEqual(b.rng.shuffles, 1, "洗回后必须洗牌")

    def test_all_takes_every_pokemon(self):
        pokes, energies = self._refs()
        b, p = self._setup(pokes + energies)
        self.assertEqual(_pokemon_to_deck(b, p, "all"), 6)
        self.assertEqual(len(p.discard_refs), 2)
        self.assertTrue(all(isinstance(x, EnergyRef) for x in p.discard_refs))

    def test_no_pokemon_no_shuffle(self):
        _, energies = self._refs()
        b, p = self._setup(list(energies))
        self.assertEqual(_pokemon_to_deck(b, p, "5"), 0)
        self.assertEqual(b.rng.shuffles, 0, "空集合不得顺手洗牌")
        self.assertEqual(len(p.deck), 0)

    def test_legals_gate(self):
        """弃牌堆无宝可梦 → 这张训练家不进合法动作表"""
        b = _arena(_CountRng())
        p = b.players[0]
        p.hand = [TrainerRef(TrainerDef(tag="pokemon_to_deck:5",
                                        kind="item", name="Sacred Ash"))]
        p.discard_refs = []
        acts = b._trainer_legals(p, b.players[1])
        self.assertFalse([a for a in acts if "Sacred Ash" in str(a)])
        p.discard_refs = self._refs()[0]
        acts = b._trainer_legals(p, b.players[1])
        self.assertTrue([a for a in acts if "Sacred Ash" in str(a)])


# ------------------------------ 第八十三批 ------------------------------
class TestBatch83SearchDiscard(unittest.TestCase):
    """search_discard:N —— 只弃不入手、至多 N 张、空集合不洗牌"""

    def _setup(self, n=8):
        b = _arena(_CountRng())
        p = b.players[0]
        p.deck = [EnergyRef(EnergyDef("colorless")) for _ in range(n)]
        p.discard = []
        p.discard_refs = []
        return b, p

    def test_up_to_five_goes_to_discard(self):
        b, p = self._setup()
        self.assertEqual(_search_discard(b, p, "5"), 5)
        self.assertEqual(len(p.deck), 3)
        self.assertEqual(len(p.discard_refs), 5)
        self.assertEqual(len(p.discard), 5, "文本层同步")
        self.assertEqual(len(p.hand), 0, "绝不能入手")
        self.assertEqual(b.rng.shuffles, 1)

    def test_short_deck_takes_available(self):
        b, p = self._setup(n=2)
        self.assertEqual(_search_discard(b, p, "5"), 2)
        self.assertEqual(len(p.deck), 0)

    def test_empty_deck_no_shuffle(self):
        b, p = self._setup(n=0)
        self.assertEqual(_search_discard(b, p, "5"), 0)
        self.assertEqual(b.rng.shuffles, 0)

    def test_legals_gate(self):
        b = _arena(_CountRng())
        p = b.players[0]
        p.hand = [TrainerRef(TrainerDef(tag="search_discard:5",
                                        kind="item", name="Brilliant Blender"))]
        p.deck = []
        acts = b._trainer_legals(p, b.players[1])
        self.assertFalse([a for a in acts if "Brilliant Blender" in str(a)])
        p.deck = [EnergyRef(EnergyDef("colorless"))]
        acts = b._trainer_legals(p, b.players[1])
        self.assertTrue([a for a in acts if "Brilliant Blender" in str(a)])


# ------------------------------ 第八十四批 ------------------------------
class TestBatch84CountersToHp(unittest.TestCase):
    """counters_to_hp:<hp>:<scope> —— 逐只算差额、不回血、不碰范围外"""

    def _setup(self):
        b = _arena(_Fixed(0.4), defender_hp=100)
        opp = b.players[1]
        opp.bench = [
            make_pokemon(PokemonDef("Big", hp=200, ptype="colorless")),
            make_pokemon(PokemonDef("Small", hp=80, ptype="colorless")),
        ]
        return b, opp

    def test_bench_each_to_100(self):
        b, opp = self._setup()
        b._apply_counters_to_hp(100, "opp_bench")
        self.assertEqual(opp.bench[0].damage, 100, "200 HP → 剩 100")
        self.assertEqual(opp.bench[1].damage, 0, "80 HP 已低于门槛，不放")
        self.assertEqual(opp.active.damage, 0, "战斗位不在 opp_bench 范围")

    def test_active_to_10(self):
        b, opp = self._setup()
        b._apply_counters_to_hp(10, "opp_active")
        self.assertEqual(opp.active.damage, 90)
        self.assertEqual(opp.bench[0].damage, 0, "后备不动")

    def test_already_below_threshold_no_heal(self):
        b, opp = self._setup()
        opp.bench[0].damage = 150          # 剩 50 HP，已低于 100
        b._apply_counters_to_hp(100, "opp_bench")
        self.assertEqual(opp.bench[0].damage, 150, "不得靠指示物回血")

    def test_unknown_scope_refused(self):
        b, _ = self._setup()
        with self.assertRaises(ValueError):
            b._apply_counters_to_hp(100, "both")


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
