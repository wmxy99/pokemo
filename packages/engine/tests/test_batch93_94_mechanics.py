# -*- coding: utf-8 -*-
"""第九十三/九十四批（2026-11-05）· 引擎用例

覆盖 5 个「引擎侧本来就没有、本批才补上」的条件 / 计数源：

  · ``opp_active_is_ex_or_v``       对手战斗位是 Pokémon ex（或 V）
  · ``played_supporter_named:<k>``  本回合打出过名字含 k 的支援者
  · ``own_bench_named_has_damage:<k>`` 后备上**具名**宝可梦带指示物
  · ``opp_ex_or_v``                 对手场上 ex（或 V）只数
  · ``both_field_tools``            双方场上道具总数（引擎已有，本批首次被解析产出）

前两个条件 / 前一个计数源是**新增**的，第 5 个是复用既有实现；每个都用
「正向成立 + 近邻不成立」两条对照，避免把条件写成了恒真。

运行：cd packages/engine && python -m unittest tests.test_batch93_94_mechanics -v
"""
from __future__ import annotations

import random
import unittest

from engine.battle import Battle
from engine.cards import (AttackDef, EnergyDef, EnergyRef, PokemonDef,
                          make_pokemon)
from engine.state import GameConfig, PlayerState


class _Fixed(random.Random):
    def __init__(self, value: float):
        super().__init__(0)
        self.value = value

    def random(self) -> float:
        return self.value


def _arena(effects, *, defender_ex=False, defender_hp=400, damage=10,
           supporter_name="", opp_bench=(), own_bench=(), opp_tool=False,
           own_tool=False):
    """最小局面：A 攻击 B；可选对手 ex / 后备 / 道具 / 本回合支援者名

    道具用 `tool:noop`（不是 `tool:dmg_plus:`）—— 后者本身会给攻击 +N 伤害，
    混进断言里会让「按道具个数缩放」的期望值凭空多出那份加成。
    """
    cfg = GameConfig()
    b = object.__new__(Battle)
    b.cfg = cfg
    b.seed = 7
    b.rng = _Fixed(0.5)
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
        attacks=(AttackDef("Test", cost=0, damage=damage, effects=tuple(effects)),)))
    b.players[1].active = make_pokemon(PokemonDef(
        "Defender", hp=defender_hp, ptype="fighting", is_ex=defender_ex))
    b.players[0].supporter_name = supporter_name
    for fp in opp_bench:
        b.players[1].bench.append(fp)
    for fp in own_bench:
        b.players[0].bench.append(fp)
    if opp_tool:
        b.players[1].active.tool = "tool:noop"
    if own_tool:
        b.players[0].active.tool = "tool:noop"
    for p in b.players:
        p.deck = [EnergyRef(EnergyDef("colorless")) for _ in range(20)]
    return b


def _hit(b) -> int:
    """打一招，返回对防守方造成的伤害"""
    before = b.players[1].active.damage
    r = b.step({"verb": "attack", "attack": 0})
    assert r["ok"], r.get("error")
    return b.players[1].active.damage - before


# ------------------------------ 第九十三批 ------------------------------
class TestOppActiveIsExOrV(unittest.TestCase):
    """「If your opponent's Active Pokémon is a Pokémon {ex} or Pokémon {V} … +110」

    卡池 1267 张里没有 V 类卡，因此 `_or_v` 变体与 `opp_active_is_ex` 同值；
    但必须**独立求值**：将来加 V 支持时只改一处，不会让已有条件静默变错。
    """

    TAG = ("dmg_plus:110:if:opp_active_is_ex_or_v",)

    def test_bonus_against_ex(self):
        self.assertEqual(_hit(_arena(self.TAG, defender_ex=True, defender_hp=400)),
                         120, "对手战斗位是 ex → 10 + 110")

    def test_no_bonus_against_non_ex(self):
        self.assertEqual(_hit(_arena(self.TAG, defender_ex=False, defender_hp=400)),
                         10, "非 ex → 不得加伤")


class TestPlayedSupporterNamed(unittest.TestCase):
    """「If you played a Supporter card that has “Team Rocket” in its name … +100」

    `supporter_name` 在打出支援者时写入、回合开始清空 → 天然是「本回合」语义。
    """

    TAG = ("dmg_plus:100:if:played_supporter_named:teamrocket",)

    def test_bonus_when_played(self):
        b = _arena(self.TAG, supporter_name="Team Rocket's Ariana")
        self.assertEqual(_hit(b), 110, "本回合打过名字含 Team Rocket 的支援者 → 10 + 100")

    def test_no_bonus_for_other_supporter(self):
        b = _arena(self.TAG, supporter_name="Professor's Research")
        self.assertEqual(_hit(b), 10, "支援者名字不含 Team Rocket → 不得加伤")

    def test_no_bonus_when_none_played(self):
        b = _arena(self.TAG, supporter_name="")
        self.assertEqual(_hit(b), 10, "本回合没打支援者 → 不得加伤")


class TestOwnBenchNamedHasDamage(unittest.TestCase):
    """「If any of your Benched **Pancham** have any damage counters on them … +120」

    ⚠ 与 `own_bench_has_damage`（任意后备有伤）反口径：后备上坐着**别的**受伤
    宝可梦时不能凭空加伤。
    """

    TAG = ("dmg_plus:120:if:own_bench_named_has_damage:pancham",)

    def _bench(self, name, damage):
        fp = make_pokemon(PokemonDef(name, hp=100, ptype="fighting"))
        fp.damage = damage
        return fp

    def test_bonus_when_named_bench_damaged(self):
        b = _arena(self.TAG, own_bench=(self._bench("Pancham", 30),))
        self.assertEqual(_hit(b), 130, "后备 Pancham 有指示物 → 10 + 120")

    def test_no_bonus_when_named_bench_healthy(self):
        b = _arena(self.TAG, own_bench=(self._bench("Pancham", 0),))
        self.assertEqual(_hit(b), 10, "后备 Pancham 无指示物 → 不得加伤")

    def test_no_bonus_for_other_named_damaged(self):
        b = _arena(self.TAG, own_bench=(self._bench("Pangoro", 40),))
        self.assertEqual(_hit(b), 10, "受伤的是别的宝可梦 → 不得加伤（子集口径）")


# ------------------------------ 第九十四批 ------------------------------
class TestOppExOrVCount(unittest.TestCase):
    """「This attack does 60 damage for each of your opponent's Pokémon {ex} and
    Pokémon {V} in play.」（Zoroark「Illusory Hijacking」）

    `dmg_set:*:per:*` 是**基础伤害重算**（不是加伤）：0 只 → 0 伤害。
    """

    TAG = ("dmg_set:60:per:opp_ex_or_v",)

    def test_counts_active_and_bench(self):
        ex1 = make_pokemon(PokemonDef("EX1", hp=200, ptype="darkness", is_ex=True))
        b = _arena(self.TAG, defender_ex=True, opp_bench=(ex1,), defender_hp=400)
        self.assertEqual(_hit(b), 120, "场上 2 只 ex → 60 × 2")

    def test_zero_when_no_ex(self):
        b = _arena(self.TAG, defender_ex=False, defender_hp=400)
        self.assertEqual(_hit(b), 0, "无 ex → 60 × 0 = 0（不是印刷伤害）")


class TestBothFieldTools(unittest.TestCase):
    """「This attack does 40 damage for each Pokémon Tool attached to all
    Pokémon.」（Bronzong「Tool Drop」）

    必须是**双方**合计：只数己方会系统性少算。
    """

    TAG = ("dmg_set:40:per:both_field_tools",)

    def test_counts_both_sides(self):
        b = _arena(self.TAG, own_tool=True, opp_tool=True, defender_hp=400)
        self.assertEqual(_hit(b), 80, "己方 1 + 对手 1 = 2 个道具 → 40 × 2")

    def test_single_side_only(self):
        b = _arena(self.TAG, own_tool=True, opp_tool=False, defender_hp=400)
        self.assertEqual(_hit(b), 40, "只有己方 1 个道具 → 40 × 1")


class TestBatch97CountersChooseN(unittest.TestCase):
    """「choose 2 of your opponent's Pokémon and put 2 damage counters on each of
    them」→ ``counters:2:scope:opp:2``

    指示物**不吃弱点/抗性**：boss 是 ptype=fighting、我方招式属性无关，
    每只各吃 2×10=20。用「对手战斗位 + 2 只后备」确认是**每只各 N 个**，
    不是把总数摊开。
    """

    TAG = ("counters:2:scope:opp:2",)

    def _bench(self, name):
        return make_pokemon(PokemonDef(name, hp=200, ptype="colorless"))

    def test_two_targets_each_get_two_counters(self):
        b = _arena(self.TAG, damage=0,
                   opp_bench=(self._bench("B1"), self._bench("B2")))
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        dmg = [b.players[1].active.damage] + [fp.damage for fp in b.players[1].bench]
        self.assertEqual(sorted(dmg), [0, 20, 20],
                         "应恰好 2 只各吃 20（2 个指示物），第 3 只不受影响")

    def test_explicit_choice_respected(self):
        """显式 `snipe` 位置码应能改选目标（玩家可挑，不是写死前两只）"""
        b = _arena(self.TAG, damage=0,
                   opp_bench=(self._bench("B1"), self._bench("B2")))
        r = b.step({"verb": "attack", "attack": 0, "snipe": [1, 2]})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.damage, 0, "战斗位不应被选中")
        self.assertEqual([fp.damage for fp in b.players[1].bench], [20, 20])


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
