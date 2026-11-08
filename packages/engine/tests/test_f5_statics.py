# -*- coding: utf-8 -*-
"""F5 · 静态属性改写（2026-11-08）

两张卡的共同点：改的是**规则属性**（弱点 / 能量点数），不是某一招的数值。
错法都不报错，只是「数值差了 2 倍」或「能量够/不够」：

1. `weak_change:<new>:<filter>`（Lillie's Clefairy ex「Fairy Zone」）
   —— 对手场上**指定属性**的宝可梦，弱点被改成 `<new>`。
   两个方向都要验：本来打不痛的现在 ×2；本来 ×2 的（原弱点）现在**不** ×2。
   过滤属性是**守方**的属性，写反（用新弱点当过滤）会让全队弱点被改。
2. `passive:energy_double:<type>`（Meganium「Wild Growth」）
   —— 每张基本草能量**提供** 2 点：影响**支付**（招式费用 / 撤退费），
   不影响「附着了几张」的计数；且卡面明文**不叠加**。

运行：cd packages/engine && python -m unittest tests.test_f5_statics -v
"""
from __future__ import annotations

import unittest

from engine.battle import Battle
from engine.cards import (AbilityDef, AttackDef, EnergyDef, EnergyRef, PokeRef,
                          PokemonDef, make_pokemon)
from engine.state import GameConfig


def _deck(tag: str = "A", n: int = 30) -> list:
    return [PokeRef(PokemonDef(f"Fill{tag}{i}", hp=100, ptype="colorless"))
            for i in range(n)]


def _battle(seed: int = 5) -> Battle:
    return Battle(_deck("A"), _deck("B"), seed=seed,
                  cfg=GameConfig(first_turn_no_attack=False,
                                 first_turn_no_supporter=False))


def _arena(me_type="psychic", me_dmg=50, me_abilities=(),
           opp_type="dragon", opp_weakness="dragon"):
    b = _battle()
    me = b.players[b.current]
    opp = b.players[1 - b.current]
    me.active = make_pokemon(PokemonDef(
        "Attacker", hp=300, ptype=me_type,
        attacks=(AttackDef("Hit", cost=0, damage=me_dmg),),
        abilities=tuple(me_abilities)))
    me.hand, me.deck = [], _deck()
    me.bench = []
    opp.active = make_pokemon(PokemonDef(
        "Dragon", hp=900, ptype=opp_type, weakness=opp_weakness))
    opp.hand, opp.deck, opp.bench = [], _deck("B"), []
    return b, me, opp


FAIRY = AbilityDef("Fairy Zone", tag="weak_change:psychic:dragon",
                   location="field")


class TestWeakChange(unittest.TestCase):
    def _hit(self, b):
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))

    def test_new_weakness_applies(self):
        """超能打龙：原弱点不是超能（打不痛）→ 改写后 ×2"""
        b, me, opp = _arena(me_type="psychic")
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(opp.active.damage, 50, "基线应当是 50（不命中弱点）")

        b2, me2, opp2 = _arena(me_type="psychic", me_abilities=(FAIRY,))
        self._hit(b2)
        self.assertEqual(opp2.active.damage, 100, "弱点没被改写成超能")

    def test_original_weakness_no_longer_applies(self):
        """龙打龙：原弱点本应 ×2 → 改写后**不**再 ×2"""
        b, me, opp = _arena(me_type="dragon")
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(opp.active.damage, 100, "基线应当是 100（命中原弱点）")

        b2, me2, opp2 = _arena(me_type="dragon", me_abilities=(FAIRY,))
        self._hit(b2)
        self.assertEqual(opp2.active.damage, 50,
                         "改写后仍按原弱点结算（同一只宝可梦不该有两个弱点）")

    def test_filter_is_defender_type(self):
        """过滤属性是**守方**的属性：非龙守方不受影响"""
        b, me, opp = _arena(me_type="psychic", opp_type="colorless",
                            opp_weakness="psychic")
        # 无色守方、印的是超能弱点：改写不适用 → 仍按卡面 ×2
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(opp.active.damage, 100, "无干属性的守方不该被改写")


class TestEnergyDouble(unittest.TestCase):
    """Meganium「Wild Growth」：每张基本 {G} 能量提供 {G}{G}"""

    TAG = "passive:energy_double:grass"

    def _setup(self, meganium=True, grass=1):
        b = _battle()
        me = b.players[b.current]
        opp = b.players[1 - b.current]
        me.active = make_pokemon(PokemonDef(
            "Attacker", hp=300, ptype="grass",
            attacks=(AttackDef("Leaf", cost=[("grass", 2)], damage=50),)))
        me.active.add_energy("grass", grass)
        me.hand, me.deck = [], _deck()
        me.bench = ([make_pokemon(PokemonDef(
            "Meganium", hp=160, ptype="grass",
            abilities=(AbilityDef("Wild Growth", tag=self.TAG,
                                  location="field"),)))] if meganium else [])
        opp.active = make_pokemon(PokemonDef("Wall", hp=900, ptype="colorless"))
        opp.deck = _deck("B")
        b._refresh_team_statics()
        return b, me, opp

    def test_one_grass_pays_two_cost(self):
        b, me, opp = self._setup(meganium=True)
        self.assertTrue(b._can_pay_attack(me.active, me.active.pdef.attacks[0]),
                        "1 张草能量应能付 {G}{G}")
        b2, me2, opp2 = self._setup(meganium=False)
        self.assertFalse(
            b2._can_pay_attack(me2.active, me2.active.pdef.attacks[0]),
            "没有 Wild Growth 时 1 张草只能付 1 点")

    def test_card_count_unchanged(self):
        """「provides」不是「counts as」：附着张数不变"""
        b, me, opp = self._setup(meganium=True)
        self.assertEqual(me.active.total_energy(), 1,
                         "能量张数被算成 2（影响 for each 类计数）")
        self.assertEqual(me.active.pay_total(), 2, "支付点数应为 2")

    def test_not_stacking(self):
        """卡面明文「doesn't stack」：两只 Meganium 仍是 ×2"""
        b, me, opp = self._setup(meganium=True)
        me.bench.append(make_pokemon(PokemonDef(
            "Meganium2", hp=160, ptype="grass",
            abilities=(AbilityDef("Wild Growth", tag=self.TAG,
                                  location="field"),))))
        b._refresh_team_statics()
        self.assertEqual(me.active.pay_total(), 2, "两只 Meganium 叠加了（应取 max）")

    def test_refresh_after_meganium_leaves(self):
        """Meganium 离场后支付池要回落（刷新点漏了就会多一回合）"""
        b, me, opp = self._setup(meganium=True)
        self.assertEqual(me.active.pay_total(), 2)
        me.bench.clear()
        b._refresh_team_statics()
        self.assertEqual(me.active.pay_total(), 1, "离场后仍按增值算（刷新漏了）")

    def test_retreat_uses_boosted_pool(self):
        """撤退费同样按增值后的点数：1 张草能量付得起 2 点撤退费"""
        b, me, opp = self._setup(meganium=True)
        me.active.pdef = PokemonDef("Attacker", hp=300, ptype="grass",
                                    retreat=2, attacks=())
        me.bench = [make_pokemon(PokemonDef("Backup", hp=100,
                                            ptype="colorless"))]
        acts = [a for a in b.legal_actions() if a.get("verb") == "retreat"]
        self.assertTrue(acts, "1 张草能量（增值后 2 点）应够付 2 点撤退费")


class TestEvolveAs(unittest.TestCase):
    """Eevee ex「Rainbow DNA」：可进化成**任意** `evolves_from: Eevee` 的 {ex}"""

    TAG = "passive:evolve_as:eevee:ex"

    def _setup(self, *, with_passive=True, target_ex=True, from_eevee=True):
        b = _battle()
        me = b.players[b.current]
        opp = b.players[1 - b.current]
        me.active = make_pokemon(PokemonDef(
            "Eevee ex", hp=200, ptype="colorless", is_ex=True,
            abilities=((AbilityDef("Rainbow DNA", tag=self.TAG,
                                   location="field"),) if with_passive else ())))
        me.active.just_played = False
        me.bench, me.deck = [], _deck()
        from_name = "Eevee" if from_eevee else "Pikachu"
        target = PokemonDef("Espeon ex" if target_ex else "Espeon", hp=270,
                            ptype="psychic", stage=1, evolves_from=from_name,
                            is_ex=target_ex)
        me.hand = [PokeRef(target)]
        opp.active = make_pokemon(PokemonDef("Foe", hp=200, ptype="colorless"))
        opp.deck = _deck("B")
        return b, me, opp

    def _acts(self, b):
        return [a for a in b.legal_actions() if a.get("verb") == "evolve"]

    def test_can_evolve_into_other_eevee_ex(self):
        b, me, opp = self._setup()
        self.assertTrue(self._acts(b), "Rainbow DNA 生效时却没列出进化动作")
        r = b.step({"verb": "evolve", "hand": 0, "target": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(me.active.name, "Espeon ex")

    def test_without_passive_not_listed(self):
        b, me, opp = self._setup(with_passive=False)
        self.assertFalse(self._acts(b), "没有 Rainbow DNA 也能跨名进化")

    def test_non_ex_target_rejected(self):
        """卡面限定「any Pokémon **{ex}**」：非 ex 的 Eevee 进化型不能上来"""
        b, me, opp = self._setup(target_ex=False)
        self.assertFalse(self._acts(b), "非 {ex} 目标也被允许（限定丢了）")

    def test_other_chain_rejected(self):
        b, me, opp = self._setup(from_eevee=False)
        self.assertFalse(self._acts(b), "改写名只对卡面点名的进化来源有效")


if __name__ == "__main__":
    unittest.main()
