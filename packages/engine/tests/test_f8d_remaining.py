# -*- coding: utf-8 -*-
"""F8b / F8c · 剩余 2 张（2026-11-09）

1. `pre:b4:shuffle_energy:<n>:then:snipe:<scope>:<per>:<count>`
   （Wellspring Mask Ogerpon ex「Torrential Pump」）
   - 「You may shuffle 3 Energy … **If you do**, this attack **also** does 120
     damage to 1 of your opponent's Benched Pokémon.」
   - 三件事各自都能独立错：① 代价没付却打后备（白送 120）；② 付了却打**战斗位**
     （目标错）；③ 能量洗回后没进牌库（代价凭空消失）。
   - 「don't apply Weakness and Resistance for Benched Pokémon」= 后备按原始值。

2. `passive:setup_active`（Cinderace「Explosiveness」）
   - 「If this Pokémon is **in your hand** when you are setting up to play,
     **you may** put it face down in the Active Spot.」
   - 开局铺场原本是**全自动**的，没有决策层 → 必须新增一次选择（you may）。
   - 选「是」：这张成为战斗位，原战斗位那只进后备；选「否」：一切照旧。
   - 硬币/第一回合/奖赏卡都必须等选择闭合后才开始（否则选择落到回合中途）。

运行：cd packages/engine && python -m unittest tests.test_f8d_remaining -v
"""
from __future__ import annotations

import unittest

from engine.battle import Battle
from engine.cards import (AbilityDef, AttackDef, EnergyDef, EnergyRef, PokeRef,
                          PokemonDef, make_pokemon)
from engine.state import GameConfig

OGERPON = "pre:b4:shuffle_energy:3:then:snipe:opp_bench:120:1"
CINDERACE_TAG = "passive:setup_active"


def _deck(tag: str = "A", n: int = 30, hp: int = 100) -> list:
    return [PokeRef(PokemonDef(f"Fill{tag}{i}", hp=hp, ptype="colorless"))
            for i in range(n)]


def _battle(seed: int = 5) -> Battle:
    return Battle(_deck("A"), _deck("B"), seed=seed,
                  cfg=GameConfig(first_turn_no_attack=False,
                                 first_turn_no_supporter=False))


class TestGatedSnipe(unittest.TestCase):
    """Ogerpon：可选代价（洗回 3 点能量）+ 门控的后备狙击"""

    def _setup(self, *, bench=("B1", "B2"), energy=4, special=0):
        b = _battle()
        me = b.players[b.current]
        opp = b.players[1 - b.current]
        me.active = make_pokemon(PokemonDef(
            "Ogerpon", hp=210, ptype="water",
            attacks=(AttackDef("Torrential Pump", cost=0, damage=100,
                               effects=(OGERPON,)),)))
        for _ in range(energy):
            me.active.add_energy("water")
        for i in range(special):
            me.active.senergy.append(EnergyRef(EnergyDef(
                "water", name=f"Special{i}", tag="senergy:prism")))
        me.bench, me.hand = [], []
        me.deck = _deck()
        opp.active = make_pokemon(PokemonDef("Foe", hp=900, ptype="colorless"))
        opp.bench = [make_pokemon(PokemonDef(nm, hp=150, ptype="colorless"))
                     for nm in bench]
        opp.deck = _deck("B")
        return b, me, opp

    def _attack(self, b):
        return b.step({"verb": "attack", "name": "Torrential Pump"})

    def test_pay_puts_damage_on_chosen_bench_target(self):
        b, me, opp = self._setup()
        deck_before = len(me.deck)
        r = self._attack(b)
        self.assertTrue(r.get("suspended"), "应在算伤害前挂起选择")
        self.assertEqual(b.phase, "choice")
        # 选项 0 = 不支付，1.. = 逐个后备目标
        r = b.step({"verb": "choose", "pick": 2})       # 选第二只后备
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(opp.active.damage, 100, "主伤害应按印刷值结算")
        self.assertEqual(opp.bench[0].damage, 0, "打错了目标")
        self.assertEqual(opp.bench[1].damage, 120, "被选中的后备没吃到 120")
        # 代价真的付了：3 张能量进牌库
        self.assertEqual(dict(me.active.energy), {"water": 1})
        self.assertEqual(len(me.deck), deck_before + 3, "能量没进牌库")
        self.assertEqual(sum(1 for c in me.deck if isinstance(c, EnergyRef)), 3)

    def test_decline_keeps_everything(self):
        b, me, opp = self._setup()
        deck_before = len(me.deck)
        self._attack(b)
        r = b.step({"verb": "choose", "pick": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(opp.active.damage, 100)
        self.assertEqual([f.damage for f in opp.bench], [0, 0],
                         "没付代价却打了后备（白送 120）")
        self.assertEqual(dict(me.active.energy), {"water": 4}, "能量被白扣")
        self.assertEqual(len(me.deck), deck_before + 0)

    def test_no_bench_skips_choice_and_keeps_cost(self):
        b, me, opp = self._setup(bench=())
        self._attack(b)
        self.assertEqual(b.phase, "main", "没有后备却挂了空选择")
        self.assertEqual(opp.active.damage, 100)
        self.assertEqual(dict(me.active.energy), {"water": 4},
                         "没有目标时不该付代价")

    def test_not_enough_energy_skips_choice(self):
        b, me, opp = self._setup(energy=2)
        self._attack(b)
        self.assertEqual(b.phase, "main", "能量不足却挂了空选择")
        self.assertEqual(opp.active.damage, 100)
        self.assertEqual([f.damage for f in opp.bench], [0, 0])

    def test_special_energy_is_kept_last(self):
        """洗回顺序：基本能量先走、特殊能量卡最后（特殊能量是严格上位）"""
        b, me, opp = self._setup(energy=4, special=2)
        self._attack(b)
        b.step({"verb": "choose", "pick": 1})
        # 付 3 点 → 应全部来自基本能量（4 → 1），特殊能量一张不动
        self.assertEqual(me.active.energy.get("water", 0), 1,
                         "洗回没有先走基本能量")
        self.assertEqual(len(me.active.senergy), 2,
                         "特殊能量被洗走了（应先洗基本能量 —— 特殊能量是"
                         "「基本能量 + 额外效果」的严格上位）")

    def test_bench_target_not_weakness_boosted(self):
        """「Don't apply Weakness and Resistance for Benched Pokémon」"""
        b, me, opp = self._setup(bench=("Weak",))
        opp.bench[0].pdef = PokemonDef("Weak", hp=300, ptype="water",
                                       weakness="metal")
        self._attack(b)
        b.step({"verb": "choose", "pick": 1})
        self.assertEqual(opp.bench[0].damage, 120,
                         "后备吃了弱点加成（卡面明文不加）")

    def test_snipe_can_knock_out_and_take_prize(self):
        b, me, opp = self._setup(bench=("Tiny",))
        tiny = make_pokemon(PokemonDef("Tiny", hp=100, ptype="colorless"))
        opp.bench = [tiny]
        prizes_before = me.prizes_left
        self._attack(b)
        b.step({"verb": "choose", "pick": 1})
        self.assertTrue(tiny.is_ko)
        self.assertNotIn(tiny, opp.bench, "被击倒的后备没离场")
        self.assertEqual(me.prizes_left, prizes_before - 1, "击倒后备没取奖赏")


class TestSetupActive(unittest.TestCase):
    """Cinderace：开局「you may」把这张从手牌放到战斗位"""

    CIN = PokemonDef("Cinderace", hp=160, ptype="fire", stage=2,
                     evolves_from="Raboot",
                     abilities=(AbilityDef("Explosiveness", tag=CINDERACE_TAG,
                                           location="field"),),
                     attacks=(AttackDef("Turbo Flare", cost=1, damage=50),))

    def _find(self, want_queue: int = 1):
        """搜种子构造「开局需要决策」的对局（固定种子表 → 结果确定）"""
        basic = PokemonDef("Bunny", hp=60, ptype="fire", stage=0)
        filler = PokemonDef("Filler", hp=100, ptype="colorless")
        for seed in range(400):
            deck_a = ([PokeRef(self.CIN) for _ in range(13)]
                      + [PokeRef(basic) for _ in range(13)])
            deck_b = ([PokeRef(filler) for _ in range(26)])
            b = Battle(deck_a, deck_b, seed=seed,
                       cfg=GameConfig(first_turn_no_attack=False,
                                      first_turn_no_supporter=False))
            if len(getattr(b, "_setup_queue", [])) == want_queue:
                return b
        self.skipTest("未能构造出开局决策局面")

    def test_choice_offered_and_card_becomes_active(self):
        b = self._find(1)
        ch = b._pending
        self.assertIsNotNone(ch, "当手牌里有 Explosiveness 卡时没有问玩家")
        self.assertEqual(ch.kind, "setup_active")
        self.assertFalse(b.setup_done, "决策没闭合就开始了第一回合")
        # 常规铺场已经先做好（引擎任何时刻都有合法局面）
        self.assertIsNotNone(b.players[ch.owner].active)
        labels = [o["display"] for o in ch.options]
        self.assertEqual(len(set(labels)), len(labels), f"选项重名：{labels}")
        idx = next(i for i, o in enumerate(ch.options) if o.get("yes"))
        me = b.players[ch.owner]
        old_active = me.active
        r = b.step({"verb": "choose", "pick": idx})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(me.active.name, "Cinderace")
        self.assertIn(old_active, me.bench, "原战斗位那只没进后备")
        self.assertTrue(b.setup_done, "选择闭合后没有收尾（setup_done）")
        self.assertEqual(b.turn_number, 1)
        self.assertEqual(sum(1 for p in b.players if p.active), 2,
                         "双方都应有战斗位")

    def test_decline_keeps_normal_setup(self):
        b = self._find(1)
        ch = b._pending
        me = b.players[ch.owner]
        act_before = me.active
        bench_before = list(me.bench)
        r = b.step({"verb": "choose", "pick": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertIs(me.active, act_before)
        self.assertEqual(me.bench, bench_before)
        self.assertTrue(b.setup_done)

    def test_prizes_dealt_after_choice(self):
        b = self._find(1)
        ch = b._pending
        idx = next(i for i, o in enumerate(ch.options) if o.get("yes"))
        b.step({"verb": "choose", "pick": idx})
        for pi in (0, 1):
            self.assertEqual(len(b.players[pi].prizes), b.cfg.prizes,
                             f"玩家 {pi} 的奖赏卡没发")

    def test_no_choice_without_the_card(self):
        """对照组：没有 Explosiveness 的开局**不**挂任何选择（旧行为不变）"""
        b = _battle()
        self.assertIsNone(getattr(b, "_pending", None))
        self.assertTrue(b.setup_done)


if __name__ == "__main__":
    unittest.main()
