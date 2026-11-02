# -*- coding: utf-8 -*-
"""PokéLab Engine · 「特性自爆」用例（2026-11-02 第三十七批）

卡面「If you use this Ability, this Pokémon is Knocked Out.」全库仅 **3 张**
（Dusclops SFA 19 / Dusknoir SFA 20 / Magneton SSP 59），措辞逐字相同。

本批做两件事：

1. **把自爆抽成公共机制** `_resolve_self_ko` —— 原先它只写在
   `_apply_counters` 里（`counters:…:selfko`），别的 tag 想自爆只能复制代码。
2. **让 `attach_spread:` 支持 `:selfko`** —— Magneton 卡在这一处。

三个容易做错的点，各有用例守着：

  · 自爆**不是「被对手击倒」**：不进 `_ko_flags`（不该喂
    `dmg_plus:*:if:opp_ko_last_turn` 一类条件），也不该触发 Legacy Energy
    的奖赏修正（那限「被招式伤害击倒」）；
  · 「用掉就付」与「效果有没有生效」无关 —— 写成 `moved > 0 才自爆`
    等于白送一次试探（静默削弱代价）；
  · 缺省分配**不得**把能量附给即将自爆的持有者（附了就直接进弃牌堆，
    净效果为零还白丢 1 张奖赏）。

运行：cd packages/engine && python -m unittest tests.test_self_ko -v
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

from engine.battle import (Battle, _parse_attach_spread_tag,  # noqa: E402
                           _spread_targets)
from engine.cards import (AbilityDef, EnergyDef, EnergyRef, PokemonDef,  # noqa: E402
                          make_pokemon)
from engine.state import GameConfig, PlayerState  # noqa: E402

TAG = "attach_spread:3:any:discard:target:type-lightning:selfko"
TAG_PLAIN = "attach_spread:3:any:discard:target:type-lightning"


def arena(seed: int = 7, turn: int = 2):
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
    b._ko_by_attack = [0, 0]
    b._ko_from_attack = False
    for p in b.players:
        p.deck = [EnergyRef(EnergyDef("colorless")) for _ in range(12)]
    b.players[1].active = make_pokemon(
        PokemonDef("Opp", hp=200, ptype="colorless", attacks=()))
    return b


def _mag(tag: str = TAG, name: str = "Magneton"):
    return make_pokemon(PokemonDef(name, hp=100, ptype="lightning", attacks=(),
                                   abilities=(AbilityDef("Overvolt Discharge",
                                                         tag),)))


def _e(color: str = "lightning"):
    return EnergyRef(EnergyDef(color))


def _energies(refs) -> list:
    """从 `discard_refs` 里只挑能量（击倒会把 PokeRef 也塞进来）"""
    return [x for x in refs if isinstance(x, EnergyRef)]


def _abilities(b):
    return [a for a in b.legal_actions() if a["verb"] == "ability"]


class TestParseTag(unittest.TestCase):
    """`:selfko` 是尾段；`:target:` 后面不能被它污染"""

    def test_selfko_tail(self):
        self.assertEqual(_parse_attach_spread_tag(TAG),
                         (3, None, "discard", "type-lightning", True))

    def test_without_selfko(self):
        self.assertEqual(_parse_attach_spread_tag(TAG_PLAIN),
                         (3, None, "discard", "type-lightning", False))

    def test_scope_not_polluted(self):
        """`target:type-lightning:selfko` 若没先剥 selfko，属性名会变成
        `type-lightning:selfko` → 认不出 → 抛错"""
        n, et, zone, scope, sko = _parse_attach_spread_tag(
            "attach_spread:2:any:deck:target:bench:selfko")
        self.assertEqual((scope, sko), ("bench", True))


class TestSelfKoCore(unittest.TestCase):
    """`_resolve_self_ko`：击倒、奖赏、补位、判负"""

    def test_knocks_out_and_gives_prize(self):
        b = arena()
        holder = _mag()
        b.players[0].active = holder
        b.players[0].bench = [make_pokemon(
            PokemonDef("Backup", hp=100, ptype="lightning", attacks=()))]
        before = b.players[1].prizes_left
        b._resolve_self_ko(holder)
        self.assertTrue(holder.is_ko)
        self.assertEqual(b.players[1].prizes_left, before - 1, "对手应取 1 张奖赏")
        self.assertEqual(b.players[0].active.name, "Backup", "后备应补位")

    def test_no_bench_loses(self):
        """自爆且无后备 → `no_pokemon` 判负（与其他击倒同路径）"""
        b = arena()
        holder = _mag()
        b.players[0].active = holder
        b.players[0].bench = []
        b._resolve_self_ko(holder)
        self.assertTrue(b.finished)
        self.assertEqual(b.winner, 1)
        self.assertEqual(b.win_reason, "no_pokemon")

    def test_not_counted_as_opponent_ko(self):
        """自爆**不是**「对手击倒了你」：不得进 `_ko_flags`

        该标记喂 `dmg_plus:*:if:opp_ko_last_turn` 一类条件；算进去等于
        凭空给下一回合加伤。
        """
        b = arena()
        holder = _mag()
        b.players[0].active = holder
        b.players[0].bench = [make_pokemon(
            PokemonDef("Backup", hp=100, ptype="lightning", attacks=()))]
        b._resolve_self_ko(holder)
        self.assertEqual(b._ko_flags()[0], 0)

    def test_bench_holder_also_works(self):
        """自爆的不必是战斗位（Magneton 可以是后备）"""
        b = arena()
        holder = _mag()
        b.players[0].active = make_pokemon(
            PokemonDef("Front", hp=100, ptype="lightning", attacks=()))
        b.players[0].bench = [holder]
        b._resolve_self_ko(holder)
        self.assertTrue(holder.is_ko)
        self.assertEqual(b.players[0].active.name, "Front", "战斗位不该被动过")

    def test_idempotent_when_already_ko(self):
        b = arena()
        holder = _mag()
        b.players[0].active = holder
        b.players[0].bench = [make_pokemon(
            PokemonDef("Backup", hp=100, ptype="lightning", attacks=()))]
        before = b.players[1].prizes_left
        b._resolve_self_ko(holder)
        b._resolve_self_ko(holder)          # 第二次必须无副作用
        self.assertEqual(b.players[1].prizes_left, before - 1)


class TestCountersSelfkoStillWorks(unittest.TestCase):
    """回归：`counters:…:selfko`（Dusclops / Dusknoir）走同一 helper 后行为不变"""

    def test_counters_selfko(self):
        b = arena()
        holder = make_pokemon(
            PokemonDef("Dusclops", hp=90, ptype="psychic", attacks=(),
                       abilities=(AbilityDef(
                           "Cursed Blast", "counters:5:scope:opp:1:selfko"),)))
        b.players[0].active = holder
        b.players[0].bench = [make_pokemon(
            PokemonDef("Backup", hp=100, ptype="psychic", attacks=()))]
        acts = _abilities(b)
        self.assertTrue(acts)
        b.step(acts[0])
        self.assertEqual(b.players[1].active.damage, 50)
        self.assertTrue(holder.is_ko)


class TestTypeScope(unittest.TestCase):
    """`type-<属性>` 按 `pdef.ptype` 筛选（不是 category）"""

    def test_filters_by_type(self):
        b = arena()
        p = b.players[0]
        p.active = make_pokemon(PokemonDef("L", hp=100, ptype="lightning"))
        p.bench = [make_pokemon(PokemonDef("L2", hp=100, ptype="lightning")),
                   make_pokemon(PokemonDef("P", hp=100, ptype="psychic"))]
        names = [fp.name for fp in _spread_targets(p, "type-lightning")]
        self.assertEqual(names, ["L", "L2"])

    def test_unknown_type_raises(self):
        """认不出的属性名必须抛错 —— 空集合会被当成「无合法目标 ⇒ 不执行」"""
        b = arena()
        with self.assertRaises(ValueError):
            _spread_targets(b.players[0], "type-nonsense")


class TestAttachSpreadSelfko(unittest.TestCase):
    """`attach_spread:…:selfko` 全链路"""

    def _setup(self):
        b = arena()
        holder = _mag()
        b.players[0].active = holder
        lan = make_pokemon(PokemonDef("Lanturn", hp=100, ptype="lightning"))
        b.players[0].bench = [
            lan, make_pokemon(PokemonDef("Psy", hp=100, ptype="psychic"))]
        b.players[0].discard_refs = [_e() for _ in range(4)]
        b.players[0].discard = ["x"] * 4
        return b, holder, lan

    def test_attach_then_self_ko(self):
        b, holder, lan = self._setup()
        acts = _abilities(b)
        self.assertTrue(acts, "有能量有目标时该特性应可发动")
        b.step(acts[0])
        self.assertTrue(holder.is_ko)
        # 缺省分配排除自爆者 → 3 张全给唯一的幸存闪电宝可梦
        self.assertEqual(lan.total_energy(), 3)
        self.assertEqual(holder.total_energy(), 0)

    def test_leftover_energy_after_pickup(self):
        b, holder, lan = self._setup()
        b.step(_abilities(b)[0])
        # 4 取 3 剩 1；自爆者身上没有能量，故弃牌堆能量仍是 1
        self.assertEqual(len(_energies(b.players[0].discard_refs)), 1)

    def test_not_listed_without_energy(self):
        b, holder, lan = self._setup()
        b.players[0].discard_refs = []
        b.players[0].discard = []
        self.assertEqual(_abilities(b), [])

    def test_not_listed_without_other_target(self):
        """只有自己是合法目标 → 列出来就是「净效果为零的自杀」，必须不列"""
        b, holder, lan = self._setup()
        b.players[0].bench = [
            make_pokemon(PokemonDef("Psy", hp=100, ptype="psychic"))]
        self.assertEqual(_abilities(b), [])

    def test_not_listed_without_any_target(self):
        b, holder, lan = self._setup()
        b.players[0].active = make_pokemon(
            PokemonDef("Psy", hp=100, ptype="psychic", attacks=(),
                       abilities=(AbilityDef("Overvolt Discharge", TAG),)))
        b.players[0].bench = []
        self.assertEqual(_abilities(b), [])

    def test_zero_moved_still_self_ko(self):
        """绕过合法动作表直接派发、且一张都没附上 → **仍然自爆**

        卡面是「If you **use** this Ability …」，与效果是否生效无关。
        写成 `moved > 0 才自爆` 等于白送一次免费试探（静默削弱代价）。
        """
        b = arena()
        holder = _mag()
        b.players[0].active = holder
        b.players[0].bench = [make_pokemon(
            PokemonDef("L2", hp=100, ptype="lightning", attacks=()))]
        b.players[0].discard_refs = []
        detail = b._do_attach_spread(b.players[0], TAG, {}, holder=holder)
        self.assertTrue(holder.is_ko)
        self.assertEqual(detail, "self KO")

    def test_explicit_plan_may_target_holder(self):
        """显式 `attach_plan` 允许指定持有者（规则上合法，只是亏）——
        缺省不替他做这个选择，但不能禁止"""
        b, holder, lan = self._setup()
        acts = _abilities(b)
        b.step({**acts[0], "attach_plan": [0]})
        self.assertEqual(holder.total_energy(), 1)
        self.assertTrue(holder.is_ko)

    def test_plan_violating_scope_raises(self):
        b, holder, lan = self._setup()
        acts = _abilities(b)
        r = b.step({**acts[0], "attach_plan": [2]})   # 2 = 超能属性，不在限定内
        self.assertFalse(r["ok"])
        self.assertIn("目标限定", r["error"])

    def test_missing_holder_raises(self):
        """`:selfko` 但没有持有者 → 抛错，绝不静默跳过自爆"""
        b, holder, lan = self._setup()
        with self.assertRaises(ValueError):
            b._do_attach_spread(b.players[0], TAG, {}, holder=None)


class TestPlainSpreadUnaffected(unittest.TestCase):
    """回归：不带 `:selfko` 的 attach_spread 不得自爆"""

    def test_no_self_ko(self):
        b = arena()
        holder = make_pokemon(
            PokemonDef("Holder", hp=100, ptype="lightning", attacks=(),
                       abilities=(AbilityDef("Overvolt Discharge",
                                             TAG_PLAIN),)))
        b.players[0].active = holder
        b.players[0].bench = [make_pokemon(
            PokemonDef("L2", hp=100, ptype="lightning"))]
        b.players[0].discard_refs = [_e() for _ in range(4)]
        acts = _abilities(b)
        b.step(acts[0])
        self.assertFalse(holder.is_ko)
        # 无 selfko → 缺省分配不排除持有者，2 只轮流分 3 张
        self.assertEqual(holder.total_energy() + b.players[0].bench[0].total_energy(),
                         3)


if __name__ == "__main__":
    unittest.main()
