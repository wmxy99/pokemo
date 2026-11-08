# -*- coding: utf-8 -*-
"""F8（第二批）· 对手手牌铺场 + 双段条件静态（2026-11-08）

守什么
------
1. `opp_hand:bench`（Mandibuzz / Lillie's Ribombee）——把**对手手牌**里符合条件
   的基础宝可梦铺到**对手后备**。三个方向都容易写反：
     · 铺到**我方**后备（帮自己，等于凭空多一只）；
     · 铺到**弃牌堆**（把「铺场」写成「弃牌」）；
     · 忽略 `@hp<N>` 限定（把 80HP 的也铺上去）。
2. **后备空间**：卡面是「put onto their Bench」，没有「弃掉多余」的语义 ——
   空间不足时剩余的手牌要留在手里，且这条要在合法动作表里也挡（否则 AI 会
   反复尝试一个必然空转的特性）。
3. `passive:energy_static:<hp>:<dmg>:<type>`（Okidogi）——**同一条件两段效果**：
   只加血或只加伤都是静默半实现，且对局里完全看不出来。

运行：cd packages/engine && python -m unittest tests.test_f8b_mechanics -v
"""
from __future__ import annotations

import unittest

from engine.battle import Battle
from engine.cards import (AbilityDef, AttackDef, PokeRef, PokemonDef,
                          make_pokemon)
from engine.state import GameConfig


def _deck(tag: str = "A", n: int = 30) -> list:
    return [PokeRef(PokemonDef(f"Fill{tag}{i}", hp=100, ptype="colorless"))
            for i in range(n)]


def _battle(seed: int = 5) -> Battle:
    return Battle(_deck("A"), _deck("B"), seed=seed,
                  cfg=GameConfig(first_turn_no_attack=False,
                                 first_turn_no_supporter=False))


MAN_BENCH = "opp_hand:bench:basic_pokemon@hp70:1"
RIB_BENCH = "opp_hand:bench:basic_pokemon:all"


def _prep_opp_hand(hand_hps, ab_tag=MAN_BENCH, bench=None):
    """当前玩家 = 我（有该特性）；对手手牌 = 若干基础宝可梦"""
    b = _battle()
    me = b.players[b.current]
    opp = b.players[1 - b.current]
    me.active = make_pokemon(PokemonDef(
        "Mandibuzz", hp=110, ptype="darkness",
        abilities=(AbilityDef("Look for Prey", tag=ab_tag, location="field"),)))
    me.hand, me.deck = [], _deck()
    me.bench = []                    # 开局铺场会把起手里的基础宝可梦放上来
    opp.active = make_pokemon(PokemonDef("Foe", hp=200, ptype="colorless"))
    opp.bench = list(bench or [])
    opp.hand = [PokeRef(PokemonDef(f"Small{i}", hp=hp, ptype="colorless"))
                for i, hp in enumerate(hand_hps)]
    opp.deck = _deck("B")
    return b, me, opp


class TestOppHandBench(unittest.TestCase):
    def _acts(self, b):
        return [a for a in b.legal_actions()
                if a.get("verb") == "ability"
                and a.get("name") == "Look for Prey"]

    def test_puts_matching_basic_onto_opponent_bench(self):
        b, me, opp = _prep_opp_hand([70, 80])
        self.assertTrue(self._acts(b), "有符合条件的牌却没进合法动作表")
        r = b.step({"verb": "ability", "name": "Look for Prey", "pos": 0,
                    "tag": MAN_BENCH})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual([fp.name for fp in opp.bench], ["Small0"])
        # 铺上去的是**对手**的后备，我方后备不能凭空多一只
        self.assertEqual(me.bench, [], "铺到了我方后备（方向反了）")
        # 80HP 的那只不满足「70 HP or less」，必须留在手里
        self.assertEqual([c.pdef.name for c in opp.hand
                          if isinstance(c, PokeRef)], ["Small1"])

    def test_no_match_not_listed(self):
        b, me, opp = _prep_opp_hand([80, 90])
        self.assertFalse(self._acts(b), "没有 ≤70HP 的基础宝可梦却列出了动作")

    def test_bench_full_not_listed(self):
        b, me, opp = _prep_opp_hand(
            [60], bench=[make_pokemon(PokemonDef(f"B{i}", hp=100,
                                                 ptype="colorless"))
                         for i in range(5)])
        self.assertFalse(self._acts(b), "对手后备已满却仍列出动作（必然空转）")

    def test_any_number_takes_all(self):
        """Ribombee「any number」：全部铺上，且不超过后备上限"""
        b, me, opp = _prep_opp_hand([60, 60, 60], ab_tag=RIB_BENCH)
        r = b.step({"verb": "ability", "name": "Look for Prey", "pos": 0,
                    "tag": RIB_BENCH})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(opp.bench), min(3, b.cfg.max_bench))
        self.assertEqual([c for c in opp.hand if isinstance(c, PokeRef)], [],
                         "「any number」应把手牌里的基础宝可梦全部铺上")

    def test_overflow_stays_in_hand(self):
        """后备空间不足时：多出来的**留在手牌**（不是被弃掉）"""
        b, me, opp = _prep_opp_hand(
            [60, 60, 60], ab_tag=RIB_BENCH, bench=[
                make_pokemon(PokemonDef(f"B{i}", hp=100, ptype="colorless"))
                for i in range(4)])
        r = b.step({"verb": "ability", "name": "Look for Prey", "pos": 0,
                    "tag": RIB_BENCH})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(opp.bench), b.cfg.max_bench)
        self.assertEqual(len([c for c in opp.hand if isinstance(c, PokeRef)]), 2,
                         "溢出的两只没留在手牌")
        self.assertEqual(opp.discard, [], "溢出的牌被弃掉了（卡面没有这个语义）")


class TestEnergyStatic(unittest.TestCase):
    """Okidogi「Adrena-Power」：有 {D} 能量则 +100 HP 且 +100 伤害"""

    TAG = "passive:energy_static:100:100:darkness"

    def _setup(self, energy=False):
        b = _battle()
        me = b.players[b.current]
        opp = b.players[1 - b.current]
        me.active = make_pokemon(PokemonDef(
            "Okidogi", hp=130, ptype="fighting",
            attacks=(AttackDef("Good Punch", cost=0, damage=70),),
            abilities=(AbilityDef("Adrena-Power", tag=self.TAG,
                                  location="field"),)))
        me.hand, me.deck = [], _deck()
        if energy:
            me.active.add_energy("darkness")
        opp.active = make_pokemon(PokemonDef("Wall", hp=900, ptype="colorless"))
        opp.deck = _deck("B")
        return b, me, opp

    def test_with_energy_both_bonuses(self):
        b, me, opp = self._setup(energy=True)
        self.assertEqual(me.active.max_hp, 230, "没有 +100 HP")
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(opp.active.damage, 170, "没有 +100 伤害")

    def test_without_energy_no_bonus(self):
        b, me, opp = self._setup(energy=False)
        self.assertEqual(me.active.max_hp, 130, "没能量也加了血（静默加强）")
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(opp.active.damage, 70, "没能量也加了伤（静默加强）")


if __name__ == "__main__":
    unittest.main()
