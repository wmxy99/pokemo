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
    b._temp_watch = []          # S2d：限时订阅列表
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


# ---------------- S2b：进化事件 / 进化触发附能 / 限时无弱点 ----------------

from engine.cards import AttackDef, PokeRef  # noqa: E402


def _stage1(name="Evo", hp=140, evolves_from="Attacker", attacks=()):
    return PokemonDef(name, hp=hp, ptype="colorless", stage=1,
                      evolves_from=evolves_from, attacks=attacks)


class TestEvolveSubscription(unittest.TestCase):
    """Team Rocket's Ampharos「Darkest Impulse」——**对手**进化时放 4 个指示物"""

    def _setup(self, amp_on_field=True):
        b = arena()
        if amp_on_field:
            b.players[0].bench = [_watcher(
                "TR Ampharos",
                "on_event:evolve:counters:4:on:entering:scope:opp_their_turn")]
        opp = b.players[1]
        opp.hand = [PokeRef(_stage1("Evolved", evolves_from="Victim"))]
        b.current = 1
        return b, opp

    def test_opponent_evolve_places_counters(self):
        b, opp = self._setup()
        r = b.step({"verb": "evolve", "hand": 0, "target": 0})
        self.assertTrue(r["ok"], r)
        self.assertEqual(opp.active.damage, 40, "4 个指示物 = 40 点")
        self.assertEqual(opp.active.name, "Evolved")

    def test_subscriber_own_evolve_does_not_trigger(self):
        """订阅方的**自己**进化不该被自己的「对手进化」订阅打中"""
        b = arena()
        b.players[0].bench = [_watcher(
            "TR Ampharos",
            "on_event:evolve:counters:4:on:entering:scope:opp_their_turn")]
        p = b.players[0]
        p.hand = [PokeRef(_stage1("Evolved", evolves_from="Attacker"))]
        b.current = 0
        b.step({"verb": "evolve", "hand": 0, "target": 0})
        self.assertEqual(p.active.damage, 0,
                         "自己进化 → who == owner → 作用域必须挡下")

    def test_only_once_per_turn(self):
        b, opp = self._setup()
        b.step({"verb": "evolve", "hand": 0, "target": 0})
        first = opp.active.damage
        opp.hand = [PokeRef(_stage1("Evolved2", evolves_from="Evolved"))]
        b.step({"verb": "evolve", "hand": 0, "target": 0})
        self.assertEqual(opp.active.damage, first,
                         "同一回合第二次进化不再触发（doesn't stack）")


class TestWhileActiveGate(unittest.TestCase):
    """Mismagius ex「Swirling Prose」：**在战斗位时**才订阅（`while:active`）"""

    TAG = ("on_event:switch_in:status:confuse:on:entering"
           ":scope:opp_their_turn:while:active")

    def _run(self, on_active: bool):
        b = arena()
        mag = _watcher("Mismagius ex", self.TAG)
        if on_active:
            b.players[0].bench = [make_pokemon(_pdef("Bench0", hp=100))]
            b.players[0].active = mag
        else:
            b.players[0].bench = [mag]
        opp = b.players[1]
        opp.bench = [make_pokemon(_pdef("B1", hp=100))]
        opp.active.add_energy("colorless")
        b.current = 1
        b.step({"verb": "retreat", "bench": 0})
        return opp.active

    def test_confuses_when_active(self):
        self.assertIn("confuse", self._run(True).status)

    def test_no_effect_when_on_bench(self):
        """后备时不订阅 —— 少了这个判定就是静默加强（放后备也生效）"""
        self.assertNotIn("confuse", self._run(False).status)


class TestEvolveAttachSpread(unittest.TestCase):
    """Archaludon ex「Assemble Alloy」：从弃牌堆取 ≤2 张基本{M}能量自由分配"""

    def test_attaches_only_to_typed_targets(self):
        from engine.cards import EnergyRef
        b = arena()
        p = b.players[0]
        metal = make_pokemon(PokemonDef("Metal", hp=100, ptype="metal"))
        water = make_pokemon(PokemonDef("Water", hp=100, ptype="water"))
        b.players[0].bench = [metal, water]
        p.active = make_pokemon(PokemonDef("Attacker", hp=300, ptype="metal"))
        p.discard_refs = [EnergyRef(EnergyDef("metal")),
                          EnergyRef(EnergyDef("metal")),
                          EnergyRef(EnergyDef("fire"))]
        p.hand = [PokeRef(PokemonDef(
            "Archaludon ex", hp=180, ptype="metal", stage=1,
            evolves_from="Attacker", abilities=(AbilityDef(
                "Assemble Alloy",
                tag="evolve_act:attach_spread:2:metal:discard:target:type-metal",
                location="field"),)))]
        b._do_evolve(p, {"hand": 0, "target": 0})
        metal_total = (p.active.energy_of("metal") + metal.energy_of("metal")
                       + water.energy_of("metal"))
        self.assertEqual(metal_total, 2, "取满 2 张基本钢能量")
        self.assertEqual(water.energy_of("metal"), 0,
                         "卡面限定 your {M} Pokémon —— 水属性目标不得被附着")


class TestEvolveReconAttach(unittest.TestCase):
    """Grumpig「Energized Steps」：看顶 4 张，把基本能量自由分配，余牌洗回"""

    def test_attaches_found_energy_and_shuffles_rest(self):
        from engine.cards import EnergyRef
        b = arena()
        p = b.players[0]
        p.active = make_pokemon(PokemonDef("Attacker", hp=300, ptype="psychic"))
        p.hand = [PokeRef(PokemonDef(
            "Grumpig", hp=130, ptype="psychic", stage=1,
            evolves_from="Attacker", abilities=(AbilityDef(
                "Energized Steps",
                tag="evolve_act:recon:4:all:basic_energy:"
                    "dest:attach_spread:rest:shuffle",
                location="field"),)))]
        # 牌库：末尾 = 顶。顶 4 张 = 2 张非能量（在下）+ 2 张基本能量（在上）
        p.deck = ([EnergyRef(EnergyDef("grass")) for _ in range(FILLER)]
                  + [PokeRef(_pdef("T1")), PokeRef(_pdef("T2")),
                     EnergyRef(EnergyDef("water")), EnergyRef(EnergyDef("fire"))])
        deck_before = len(p.deck)
        b._do_evolve(p, {"hand": 0, "target": 0})
        self.assertEqual(p.active.total_energy(), 2, "顶 4 张里的 2 张能量全附着")
        self.assertEqual(len(p.deck), deck_before - 2,
                         "其余牌洗回（总数只少了被附着的 2 张）")


class TestTimedNoWeakness(unittest.TestCase):
    """Archaludon ex「Metal Defender」：限时无弱点（不是常驻）"""

    def _pair(self):
        b = arena()
        atk = AttackDef("Hit", cost=1, damage=100, effects=("shield:no_weakness",))
        b.players[0].active = make_pokemon(
            PokemonDef("Attacker", hp=300, ptype="fire", attacks=(atk,)))
        b.players[0].active.add_energy("fire")
        b.players[1].active = make_pokemon(
            PokemonDef("Victim", hp=900, ptype="grass", weakness="fire"))
        return b

    def test_sets_flag_and_does_not_change_this_hit(self):
        """卡面是「During your opponent's **next** turn」→ 本招命中仍吃弱点"""
        b = self._pair()
        defender = b.players[1].active
        attacker = b.players[0].active
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(defender.damage, 200, "本招的伤害照常算弱点")
        # 卡面「**this** Pokémon has no Weakness」→ 挂在**出招者**身上
        self.assertTrue(attacker.shield_no_weakness, "出招者获得限时无弱点")

    def test_active_flag_ignores_weakness(self):
        b = self._pair()
        defender = b.players[1].active
        defender.shield_no_weakness = True          # 模拟「对手的下个回合」
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(defender.damage, 100, "限时无弱点生效 → 不乘 2")

    def test_expires_at_owner_turn_start(self):
        b = self._pair()
        attacker = b.players[0].active
        b.step({"verb": "attack", "attack": 0})
        self.assertTrue(attacker.shield_no_weakness)
        b.step({"verb": "pass"})        # 换手到对手
        b.step({"verb": "pass"})        # 回到我的回合开始 → 清零
        self.assertFalse(attacker.shield_no_weakness,
                         "该护盾在自己下个回合开始时清除（只活过对手一个回合）")


# ---------------- S2c：击倒订阅 / 回合末被动道具 / 延迟弃置 ----------------

from engine.cards import TrainerDef, TrainerRef  # noqa: E402


def _tool(tag: str, name: str = "Tool") -> TrainerRef:
    return TrainerRef(TrainerDef(tag=tag, kind="tool", name=name,
                                 name_zh=name))


class TestKoSubscription(unittest.TestCase):
    """Amulet of Hope：被**招式伤害**击倒时检索 3 张（非招式击倒不触发）"""

    TAG = ("on_event:ko:search_any:3:on:leaving:scope:own_by_attack")

    def _setup(self):
        b = arena()
        p = b.players[0]
        p.active.tool = self.TAG
        p.active.tool_name = "Amulet of Hope"
        p.active.tool_ref = _tool(self.TAG, "Amulet of Hope")
        p.hand = []
        # 被击倒方是 p（owner=0）：让 p.active 直接 KO
        return b, p

    def test_search_on_attack_ko(self):
        b, p = self._setup()
        deck_before = len(p.deck)
        b._ko_from_attack = True
        b._resolve_ko(0, p.active)
        self.assertEqual(len(p.hand), 3, "检索至多 3 张入手")
        self.assertEqual(len(p.deck), deck_before - 3)

    def test_no_search_when_not_attack(self):
        b, p = self._setup()
        b._ko_from_attack = False          # 中毒 / 指示物击倒
        b._resolve_ko(0, p.active)
        self.assertEqual(len(p.hand), 0, "非招式击倒不该触发")

    def test_tool_attaches_as_trainer_action(self):
        b = arena()
        p = b.players[0]
        p.hand = [_tool(self.TAG, "Amulet of Hope")]
        self.assertTrue(b._trainer_legals(p, b.players[1]),
                        "订阅式道具必须能进合法动作表（否则池内死卡）")
        r = b.step({"verb": "trainer", "hand": 0, "target": 0})
        self.assertTrue(r["ok"], r)
        self.assertEqual(p.active.tool, self.TAG)


class TestTurnEndTool(unittest.TestCase):
    """Powerglass：回合末可选附着 1 个基本能量（**必须由牌手选**）"""

    TAG = ("on_event:turn_end:attach_basic:on:holder"
           ":scope:own_turn:while:active")

    def _setup(self, on_active=True):
        from engine.cards import EnergyRef
        b = arena()
        p = b.players[0]
        holder = p.active
        holder.tool = self.TAG
        holder.tool_name = "Powerglass"
        holder.tool_ref = _tool(self.TAG, "Powerglass")
        if not on_active:
            p.bench = [holder]
            p.active = make_pokemon(_pdef("Other", hp=100))
        p.discard_refs = [EnergyRef(EnergyDef("water")),
                          EnergyRef(EnergyDef("fire")),
                          EnergyRef(EnergyDef("fire"))]
        return b, p, holder

    def test_offers_choice_at_turn_end(self):
        b, p, holder = self._setup()
        b.step({"verb": "pass"})
        self.assertEqual(b.phase, "choice", "回合末挂起选择（不默认附着）")
        # ★ 换手必须**等选择闭合**：否则「我方回合末的选择」会落到对手回合
        #   （owner 错、窗口错，对局结果被静默改动）
        self.assertEqual(b.current, 0, "挂起期间不得换手")
        self.assertEqual(b._pending.kind, "attach_basic_confirm")
        kinds = sorted(o.get("etype") for o in b._pending.options
                       if o.get("etype"))
        self.assertEqual(kinds, ["fire", "water"], "每个颜色一个选项 + 不附着")

    def test_attach_then_turn_hands_over(self):
        b, p, holder = self._setup()
        b.step({"verb": "pass"})
        fire = next(i for i, o in enumerate(b._pending.options)
                    if o.get("etype") == "fire")
        b.step({"verb": "choose", "pick": fire})
        self.assertEqual(holder.energy_of("fire"), 1)
        self.assertEqual(b.current, 1, "选择闭合后补做换手")
        self.assertEqual(b.phase, "main")

    def test_decline_attaches_nothing(self):
        b, p, holder = self._setup()
        b.step({"verb": "pass"})
        self.assertEqual(b.current, 0, "挂起期间不得换手")
        stop = next(i for i, o in enumerate(b._pending.options) if o.get("pass"))
        b.step({"verb": "choose", "pick": stop})
        self.assertEqual(holder.total_energy(), 0)
        self.assertEqual(b.current, 1)

    def test_bench_holder_does_nothing(self):
        b, p, holder = self._setup(on_active=False)
        b.step({"verb": "pass"})
        self.assertEqual(b.phase, "main", "后备时不订阅（while:active）")
        self.assertEqual(b.current, 1)


class TestQueuedDiscardDefender(unittest.TestCase):
    """Team Rocket's Grimer：对手下回合末弃置当时的战斗位（**不取奖赏**）"""

    def _setup(self):
        b = arena()
        atk = AttackDef("Corrosive Sludge", cost=1,
                        effects=("queue:opp_next_turn_end:discard_defender:all",))
        b.players[0].active = make_pokemon(
            PokemonDef("Grimer", hp=80, ptype="darkness", attacks=(atk,)))
        b.players[0].active.add_energy("darkness")
        b.players[1].active = make_pokemon(_pdef("Defender", hp=200))
        b.players[1].bench = [make_pokemon(_pdef("Backup", hp=100))]
        b.current = 0
        # 「对手的**下个**回合末」= turn_counts[1] 还是 0（对手还没开过回合），
        # 入队时 due = 0+1 = 1，对手第一次回合结束就到期。夹具必须用真实计数。
        b.turn_counts = [1, 0]
        return b

    def test_discards_defender_without_prizes(self):
        b = self._setup()
        defender = b.players[1].active
        prizes_before = b.players[0].prizes_left
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r)
        self.assertEqual(len(b._queue), 1, "延迟效果应入队")
        self.assertIn(defender, b.players[1].all_field(), "攻击当回合不弃置")
        # 走到「对手的下个回合末」：攻击已结束我方回合 → 对手第一次回合
        b.step({"verb": "pass"})
        self.assertNotIn(defender, b.players[1].all_field(), "到点弃置")
        self.assertEqual(b.players[0].prizes_left, prizes_before,
                         "**弃置不是击倒**，不得取奖赏")
        self.assertEqual(len(b._queue), 0)


# ---------------- S2d：限时订阅 / 撤退费条件 / 具名能量 / Boomerang ----------------

ARM = ("arm:attach_energy:counters:8:on:target:scope:own_turn"
       ":match:event_target:until:opp_next_turn")


def _pachirisu_arena():
    b = arena()
    atk = AttackDef("Electrified Incisors", cost=1, damage=10, effects=(ARM,))
    b.players[0].active = make_pokemon(
        PokemonDef("Pachirisu", hp=70, ptype="lightning", attacks=(atk,)))
    b.players[0].active.add_energy("lightning")
    b.players[0].deck = [EnergyRef(EnergyDef("fire")) for _ in range(FILLER)]
    b.players[1].active = make_pokemon(_pdef("Defender", hp=400))
    b.players[1].bench = [make_pokemon(_pdef("Backup", hp=100))]
    b.players[1].hand = [EnergyRef(EnergyDef("water")),
                         EnergyRef(EnergyDef("water"))]
    return b


class TestTimedWatch(unittest.TestCase):
    """Pachirisu「Electrified Incisors」：**限时**订阅（窗口 = 对手下个回合）"""

    def test_armed_by_attack(self):
        b = _pachirisu_arena()
        self.assertTrue(b.step({"verb": "attack", "attack": 0})["ok"])
        self.assertEqual(len(b._temp_watch), 1, "出招应留下一条限时订阅")
        self.assertEqual(b._temp_watch[0]["kind"], "attach_energy")
        self.assertEqual(b.current, 1, "攻击结束 → 换到对手回合")

    def test_counters_when_they_attach_to_the_defender(self):
        b = _pachirisu_arena()
        b.step({"verb": "attack", "attack": 0})
        defender = b.players[1].active
        before = defender.damage
        self.assertTrue(b.step({"verb": "attach", "energy": 0,
                                "target": 0})["ok"])
        self.assertEqual(defender.damage - before, 80, "8 个指示物 = 80 点")

    def test_no_counters_when_attached_elsewhere(self):
        """卡面写死 `to the Defending Pokémon` —— 附给后备不该触发"""
        b = _pachirisu_arena()
        b.step({"verb": "attack", "attack": 0})
        defender, backup = b.players[1].active, b.players[1].bench[0]
        d0, b0 = defender.damage, backup.damage
        b.step({"verb": "attach", "energy": 0, "target": 1})
        self.assertEqual(defender.damage, d0)
        self.assertEqual(backup.damage, b0)

    def test_expires_after_their_turn(self):
        b = _pachirisu_arena()
        b.step({"verb": "attack", "attack": 0})
        b.step({"verb": "pass"})           # 对手回合结束 → 窗口关闭
        self.assertEqual(len(b._temp_watch), 0, "回合末必须剪掉（否则=永久）")
        b.step({"verb": "pass"})           # 回到对手的**下下个**回合
        b.players[1].hand = [EnergyRef(EnergyDef("water"))]
        defender = b.players[1].active
        d0 = defender.damage
        b.step({"verb": "attach", "energy": 0, "target": 0})
        self.assertEqual(defender.damage, d0, "过期后不得再触发")

    def test_own_attach_does_not_trigger(self):
        """`scope:own_turn`：出招方自己回合内的附着不该被自己的订阅打"""
        b = _pachirisu_arena()
        b.players[0].hand = [EnergyRef(EnergyDef("fire"))]
        b.current = 1
        b.step({"verb": "pass"})           # 回到 P0
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.current, 1)
        self.assertEqual(len(b._temp_watch), 1)
        # 此时是 P1 的回合；直接喂一条「P0 在自己回合附着」的事件验证 scope：
        # ⚠ `emit` 的返回值只表示「订阅被**调用**了」，被判 scope 挡下的也会
        #   出现在里面 —— 所以断言要看**效果**（伤害）与 `event_scope_skip` 日志。
        defender = b.players[1].active
        d0 = defender.damage
        events.emit(b, "attach_energy",
                    {"player": 0, "target": defender, "turn_owner": 0})
        self.assertEqual(defender.damage, d0, "P0 的附着不在窗口的回合里")
        self.assertTrue([e for e in b.events
                         if e["type"] == "event_scope_skip"])


BATON = ("on_event:ko:move_energy_to_bench:3:on:leaving:scope:own_by_attack"
         ":require:was_active:require:retreat:4")


def _baton_arena(retreat: int = 4, on_active: bool = True):
    b = arena()
    atk = AttackDef("Big", cost=1, damage=200)
    b.players[0].active = make_pokemon(
        PokemonDef("Attacker", hp=300, ptype="colorless", attacks=(atk,)))
    b.players[0].active.add_energy("colorless")
    victim = make_pokemon(PokemonDef("Victim", hp=100, ptype="colorless",
                                     retreat=retreat))
    for _ in range(3):
        victim.add_energy("fire")
    for _ in range(2):
        victim.add_energy("water")
    victim.tool = BATON
    victim.tool_name = "Heavy Baton"
    victim.tool_ref = TrainerRef(TrainerDef(tag=BATON, kind="tool",
                                            name="Heavy Baton", name_zh=""))
    b1 = make_pokemon(_pdef("B1"))
    b2 = make_pokemon(_pdef("B2"))
    b.players[1].bench = [b1, b2]
    if on_active:
        b.players[1].active = victim
    else:
        b.players[1].active = make_pokemon(_pdef("Front", hp=100))
        b.players[1].bench.insert(0, victim)
    return b, victim, b1, b2


class TestHeavyBaton(unittest.TestCase):
    """Heavy Baton：撤退费**恰好** 4 + 在战斗位 + 被招式击倒 → 搬最多 3 张"""

    def test_moves_three_round_robin(self):
        b, victim, b1, b2 = _baton_arena()
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b1.total_energy() + b2.total_energy(), 3)
        self.assertEqual((b1.total_energy(), b2.total_energy()), (2, 1),
                         "轮转分配（不会把 3 张全堆在一只身上）")
        self.assertNotIn(victim, b.players[1].all_field())

    def test_wrong_retreat_cost_does_nothing(self):
        b, victim, b1, b2 = _baton_arena(retreat=3)
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b1.total_energy() + b2.total_energy(), 0,
                         "撤退费不恰好是 4 → 不触发")

    def test_bench_victim_does_nothing(self):
        b, victim, b1, b2 = _baton_arena(on_active=False)
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b1.total_energy() + b2.total_energy(), 0,
                         "卡面限定 Active Spot → 后备被击倒不搬")

    def test_non_attack_ko_does_nothing(self):
        from engine import events
        b, victim, b1, b2 = _baton_arena()
        b._ko_from_attack = False
        b._resolve_ko(1, victim)
        self.assertEqual(b1.total_energy() + b2.total_energy(), 0,
                         "非招式击倒不触发（scope:own_by_attack）")


class TestAttachNamed(unittest.TestCase):
    """Lycanroc「Spike-Clad」：从弃牌堆附着 **2 张 Spiky Energy**"""

    TAG = "evolve_act:attach_named:2:Spiky Energy:discard:self"

    def _arena(self, n_spiky=3):
        b = arena()
        b.players[0].active = make_pokemon(
            PokemonDef("Rockruff", hp=70, ptype="fighting"))
        b.players[0].hand = [PokeRef(PokemonDef(
            "Lycanroc", hp=120, ptype="fighting", stage=1,
            evolves_from="Rockruff",
            abilities=(AbilityDef("Spike-Clad", tag=self.TAG,
                                  location="field"),)))]
        p = b.players[0]
        p.discard_refs = [EnergyRef(EnergyDef("colorless",
                                              name="Basic Colorless Energy"))]
        p.discard = ["Basic Colorless Energy"]
        for _ in range(n_spiky):
            p.discard_refs.append(EnergyRef(EnergyDef(
                "colorless", name="Spiky Energy", tag="senergy:spiky")))
            p.discard.append("Spiky Energy")
        b.current = 0
        b.turn_counts = [1, 1]
        return b, p

    def test_attaches_by_name_not_type(self):
        b, p = self._arena()
        self.assertTrue(b.step({"verb": "evolve", "hand": 0,
                                "target": 0})["ok"])
        names = [r.edef.name for r in p.active.senergy]
        self.assertEqual(names, ["Spiky Energy", "Spiky Energy"])
        self.assertIn("Basic Colorless Energy",
                      [r.edef.name for r in p.discard_refs],
                      "基本无色能量（同属性）不得被拿去顶替")

    def test_only_available_cards(self):
        b, p = self._arena(n_spiky=1)
        b.step({"verb": "evolve", "hand": 0, "target": 0})
        self.assertEqual(len(p.active.senergy), 1, "只有 1 张就只附 1 张")


class TestBoomerangEnergy(unittest.TestCase):
    """Boomerang Energy：被**自身招式效果**弃置 → 攻击结束后回附"""

    def _arena(self, effects):
        b = arena()
        atk = AttackDef("Toss", cost=1, damage=0, effects=effects)
        p = b.players[0]
        p.active = make_pokemon(
            PokemonDef("Attacker", hp=300, ptype="colorless", attacks=(atk,)))
        p.active.add_energy("colorless")
        ref = EnergyRef(EnergyDef("colorless", name="Boomerang Energy",
                                  tag="senergy:boomerang"))
        p.active.senergy.append(ref)
        b.players[1].active = make_pokemon(_pdef("Victim", hp=400))
        p.prizes_left = 12
        b.players[1].prizes_left = 12
        return b, p, ref

    def test_returns_after_own_attack_discards_it(self):
        b, p, ref = self._arena(("discard_energy:all",))
        b.step({"verb": "attack", "attack": 0})
        self.assertTrue(any(x is ref for x in p.active.senergy),
                        "被本招效果弃置 → 攻击结束后回附")
        self.assertNotIn(ref, p.discard_refs)

    def test_no_return_when_not_discarded(self):
        b, p, ref = self._arena(("discard_energy:1",))
        b.step({"verb": "attack", "attack": 0})
        self.assertTrue(any(x is ref for x in p.active.senergy),
                        "没被弃置就一直在身上；`discard_energy:1` 弃的是基本能量")
        self.assertFalse([e for e in b.events if e["type"] == "senergy_return"])

    def test_no_return_when_discarded_by_opponent(self):
        """「attack used by the Pokémon this card is **attached to**」——
        对手的招式效果弃掉它，不回附（回附的是**自己**的招式）"""
        b = arena()
        atk = AttackDef("Strip", cost=1, damage=0,
                        effects=("discard_energy:1:opp",))
        b.players[0].active = make_pokemon(
            PokemonDef("Opp", hp=300, ptype="colorless", attacks=(atk,)))
        b.players[0].active.add_energy("colorless")
        victim = b.players[1].active
        victim.senergy.append(EnergyRef(EnergyDef(
            "colorless", name="Boomerang Energy", tag="senergy:boomerang")))
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(len(victim.senergy), 0)
        self.assertIn("Boomerang Energy", b.players[1].discard,
                      "卡进弃牌堆，但不该回附到对手身上")
