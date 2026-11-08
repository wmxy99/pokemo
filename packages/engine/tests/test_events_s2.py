# -*- coding: utf-8 -*-
"""S2a · 事件总线 + 延迟队列（2026-11-08）

守的三件事
----------
1. **延迟队列**（Amarys「Draw 4 cards. At the end of this turn, if you have 5 or
   more cards in your hand, discard your hand.」）：效果必须**到本回合结束**才
   执行、条件在**结算时**求值、队列不许泄漏（过期未结算 → 抛错）。
2. **事件订阅**（Team Rocket's Dugtrio「Holes」：对手的战斗位在**对手回合**
   换下时，在那只身上放 2 个指示物）：触发方必须是「对手 + 对手回合」，
   **不能**被自己回合的拉人（gust）触发；同一回合多次换位只结算 1 次。
3. **一处结算**：换位事件用「分发前快照 / 分发后比对」检测，所有换位路径
   （撤退 / Switch / 拉人 / 回手）自动覆盖 —— 新增路径不会静默漏触发。

运行：cd packages/engine && python -m unittest tests.test_events_s2 -v
"""
from __future__ import annotations

import random
import unittest

from engine import events
from engine.battle import Battle
from engine.cards import (AbilityDef, EnergyDef, EnergyRef, PokemonDef,
                          TrainerDef, TrainerRef, make_pokemon)
from engine.state import GameConfig, PlayerState

FILLER = 12


def _pdef(name="A", hp=100, **kw):
    return PokemonDef(name, hp=hp, ptype="colorless", attacks=(), **kw)


def arena(seed: int = 3) -> Battle:
    b = object.__new__(Battle)
    b.cfg = GameConfig()
    b.seed = seed
    b.rng = random.Random(seed)
    b.players = [PlayerState(name="A", prizes_left=6),
                 PlayerState(name="B", prizes_left=6)]
    b.current = 0
    b.turn_number = 1
    b.turn_counts = [1, 1]
    b.phase = "main"
    b._pending = None
    b._defer_end_turn = False
    b._queue = events.EffectQueue()
    b._watch_fired = {}
    b._switch_snap = None
    b.finished = False
    b.winner = None
    b.win_reason = None
    b.events = []
    b.stadium = None
    b._stadium_used = [0, 0]
    b._ko_by_attack = [0, 0]
    b._ko_tr_by_attack = [0, 0]
    b._ko_from_attack = False
    b._legacy_used = False
    b._resolving_attack = None
    b._pre_tools_discarded = 0
    b.players[0].active = make_pokemon(_pdef("Attacker", hp=300))
    b.players[1].active = make_pokemon(_pdef("Victim", hp=100))
    for p in b.players:
        p.deck = [EnergyRef(EnergyDef("grass")) for _ in range(FILLER)]
    return b


def _supporter(tag: str, name: str = "Amarys") -> TrainerRef:
    return TrainerRef(TrainerDef(tag=tag, kind="supporter", name=name,
                                 name_zh=name))


def _watcher(name: str, tag: str):
    """带一条 field 特性的宝可梦（订阅方）"""
    return make_pokemon(PokemonDef(
        name, hp=100, ptype="colorless", attacks=(),
        abilities=(AbilityDef(name, tag=tag, location="field"),)))


class TestDelayQueue(unittest.TestCase):
    def test_amarys_pushes_and_resolves_at_turn_end(self):
        b = arena()
        p = b.players[0]
        p.hand = [_supporter(
            "draw:4|queue:turn_end:discard_hand:all:if:own_hand_at_least:5")]
        r = b.step({"verb": "trainer", "hand": 0})
        self.assertTrue(r["ok"], r)
        # 打出后只抽牌，**还没弃手牌**
        self.assertGreaterEqual(len(p.hand), 4)
        self.assertEqual(len(b._queue), 1, "延迟效果应入队")
        self.assertEqual(len(p.hand), 4, "draw:4 → 手牌 4 张（本回合不弃）")
        # 结束回合 → 手牌 4 张 < 5 → 条件不成立 → 不弃
        b.step({"verb": "pass"})
        self.assertEqual(len(p.hand), 4, "条件不满足时不得弃手牌")
        self.assertEqual(len(b._queue), 0, "到期项必须离队")

    def test_amarys_discards_when_hand_is_five_or_more(self):
        b = arena()
        p = b.players[0]
        p.hand = [_supporter(
            "draw:4|queue:turn_end:discard_hand:all:if:own_hand_at_least:5"),
            EnergyRef(EnergyDef("water")), EnergyRef(EnergyDef("water"))]
        b.step({"verb": "trainer", "hand": 0})
        self.assertEqual(len(p.hand), 6)          # 2 + 4
        b.step({"verb": "pass"})
        self.assertEqual(len(p.hand), 0, "回合末手牌 ≥5 → 全部弃掉")
        self.assertEqual(len(b._queue), 0)
        logs = [e for e in b.events if e.get("type") == "discard_hand"]
        self.assertTrue(logs and logs[-1]["n"] == 6, logs)

    def test_stale_item_raises_instead_of_silently_dropping(self):
        """★ 过期未结算必须**抛错**：它是「效果被吞掉」的唯一可见信号"""
        b = arena()
        events.push(b, 0, 0, "discard_hand:all", source="test")
        b.turn_counts[0] = 2          # 回合序被推过 → 该条目已成过期
        with self.assertRaises(ValueError) as cm:
            events.resolve_turn_end(b, 0)
        self.assertIn("泄漏", str(cm.exception))

    def test_future_item_is_carried_not_executed(self):
        b = arena()
        p = b.players[0]
        p.hand = [EnergyRef(EnergyDef("water"))]
        events.push(b, 0, 1, "discard_hand:all", source="test")   # 下回合末
        events.resolve_turn_end(b, 0)
        self.assertEqual(len(p.hand), 1, "未到期的条目不得提前执行")
        self.assertEqual(len(b._queue), 1, "未到期条目显式结转")

    def test_unknown_delayed_tag_raises(self):
        b = arena()
        events.push(b, 0, 0, "totally_unknown_effect:1", source="test")
        with self.assertRaises(ValueError) as cm:
            events.resolve_turn_end(b, 0)
        self.assertIn("未实现的延迟 tag", str(cm.exception))


class TestSwitchSubscription(unittest.TestCase):
    def _with_watcher(self, tag: str):
        b = arena()
        watcher = _watcher("TR Dugtrio", tag)
        b.players[0].bench = [watcher]        # 订阅方在**我方后备**（观察对手）
        opp = b.players[1]
        opp.bench = [make_pokemon(_pdef("Bench1", hp=100))]
        opp.active.add_energy("colorless")
        return b, watcher, opp

    def test_opponent_retreat_triggers_counters(self):
        b, w, opp = self._with_watcher(
            "on_event:switch_out:counters:2:on:leaving:scope:opp_their_turn")
        leaving = opp.active
        b.current = 1                     # 对手的回合
        b.turn_counts = [1, 1]
        r = b.step({"verb": "retreat", "bench": 0})
        self.assertTrue(r["ok"], r)
        self.assertEqual(leaving.damage, 20, "换下的那只吃 2 个指示物")
        self.assertIn(leaving, opp.bench)

    def test_own_turn_gust_does_not_trigger(self):
        b, w, opp = self._with_watcher(
            "on_event:switch_out:counters:2:on:leaving:scope:opp_their_turn")
        leaving = opp.active
        b.current = 0                     # **我**的回合（对手被拉人）
        events.snapshot_switch(b)
        b._trainer_boss(opp, 0)
        events.emit_switch_diff(b)
        self.assertEqual(leaving.damage, 0, "卡面是 during their turn，我方回合不触发")

    def test_scope_skip_is_logged(self):
        b, w, opp = self._with_watcher(
            "on_event:switch_out:counters:2:on:leaving:scope:opp_their_turn")
        b.current = 0
        events.snapshot_switch(b)          # 与 `step()` 同样先快照再比对
        b._trainer_boss(opp, 0)
        events.emit_switch_diff(b)
        logs = [e for e in b.events if e.get("type") == "event_scope_skip"]
        self.assertTrue(logs, "被作用域挡下也要留日志（否则看不出来）")

    def test_at_most_once_per_turn(self):
        b, w, opp = self._with_watcher(
            "on_event:switch_out:counters:2:on:leaving:scope:opp_their_turn")
        b.current = 1
        opp.bench = [make_pokemon(_pdef("B1", hp=100)),
                     make_pokemon(_pdef("B2", hp=100))]
        for fp in opp.bench:              # 两次撤退都要付得出能量，否则第二次根本没换位
            fp.add_energy("colorless")
        self.assertTrue(b.step({"verb": "retreat", "bench": 0})["ok"])
        self.assertTrue(b.step({"verb": "retreat", "bench": 0})["ok"])
        total = sum(fp.damage for fp in opp.bench)
        self.assertEqual(total, 20, "同一回合只触发 1 次（防无限链 / doesn't stack）")
        self.assertTrue([e for e in b.events if e.get("type") == "event_suppressed"])

    def test_status_on_entering(self):
        b = arena()
        b.players[0].bench = [_watcher(
            "Mismagius", "on_event:switch_in:status:confuse:on:entering:"
                         "scope:opp_their_turn")]
        opp = b.players[1]
        opp.bench = [make_pokemon(_pdef("B1", hp=100))]
        opp.active.add_energy("colorless")
        b.current = 1
        b.step({"verb": "retreat", "bench": 0})
        self.assertIn("confuse", opp.active.status, "换上来的战斗位应混乱")

    def test_unknown_body_raises(self):
        b = arena()
        b.players[0].bench = [_watcher("X", "on_event:switch_out:bogus:1")]
        opp = b.players[1]
        opp.bench = [make_pokemon(_pdef("B1", hp=100))]
        opp.active.add_energy("colorless")
        b.current = 1
        with self.assertRaises(ValueError):
            b.step({"verb": "retreat", "bench": 0})

    def test_unknown_event_kind_raises(self):
        b = arena()
        with self.assertRaises(ValueError):
            events.emit(b, "not_a_real_event")


class TestSnapshotBoundaries(unittest.TestCase):
    def test_ko_promotion_is_not_a_switch(self):
        """击倒后的补位不是「换下」—— 否则每次 KO 都会误触发订阅"""
        b = arena()
        b.players[0].bench = [
            _watcher("TR Dugtrio",
                     "on_event:switch_out:counters:2:on:leaving:scope:any")]
        entrant = make_pokemon(_pdef("Promoted", hp=100))
        b.players[1].bench = [entrant]
        victim = b.players[1].active
        b.current = 1
        events.snapshot_switch(b)
        b._resolve_ko(1, victim)
        events.emit_switch_diff(b)
        self.assertEqual(entrant.damage, 0, "补位不该被当成 switch_out")

    def test_no_switch_no_event(self):
        b = arena()
        b.players[0].bench = [_watcher(
            "TR Dugtrio",
            "on_event:switch_out:counters:2:on:leaving:scope:any")]
        b.step({"verb": "attach", "energy": 0, "target": 0})
        self.assertFalse([e for e in b.events if e.get("type") == "event"])


if __name__ == "__main__":
    unittest.main()
