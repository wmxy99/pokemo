# -*- coding: utf-8 -*-
"""F7 · 道具 / 特殊能量形态（2026-11-08）

守什么
------
1. **复合静态道具**：Hop's Choice Band 一句两修正（费用 −{C} **且** +30 伤害）。
   `fp.tool` 只有一个字符串，复合形态是 `a|b` —— 静态层必须**逐段**读。
   只认首段的后果是「少一段静默消失」（卡面写着两个效果，实际只生效一个）。
2. **道具只对卡面指定的持有者生效**（`holder:faction:hops`）：挂到别的宝可梦
   身上**什么都不发生**（费用不减、伤害不加）。不判持有者 = 静默加强。
3. **一句三动作的物品**（Megaton Blower）：弃对手**全场**道具 + **全部**特殊
   能量 + 场上场地。三段的合法动作门槛要取**或**，只按首段判会让
   「对手只有特殊能量」时这张卡选不到（池内死卡）。
4. **道具提供招式**（Core Memory）：招式本体在道具卡上，只有卡面指定的持有者
   能用；合法动作枚举、`_do_attack` 下标、前端 view **必须同一口径**
   （谁直接读 `pdef.attacks`，谁的下标就错一位）。

运行：cd packages/engine && python -m unittest tests.test_f7_tool_forms -v
"""
from __future__ import annotations

import unittest

from engine.battle import Battle, _attack_cost_delta, _attacks_of
from engine.cards import (AttackDef, EnergyDef, EnergyRef, PokeRef, PokemonDef,
                          TrainerDef, TrainerRef, make_pokemon)
from engine.state import GameConfig


def _deck(n: int = 30) -> list:
    return [PokeRef(PokemonDef(f"Fill{i}", hp=100, ptype="colorless"))
            for i in range(n)]


def _battle(seed: int = 5) -> Battle:
    """真实对局（关掉先手规则 —— 本套件验的是道具形态，不是开局规则）"""
    return Battle(_deck(), _deck(), seed=seed,
                  cfg=GameConfig(first_turn_no_attack=False,
                                 first_turn_no_supporter=False))


def _prep(b: Battle, *, me_name="Me", me_hp=300, opp_name="Foe", opp_hp=900,
          me_attacks=(), opp_attacks=()):
    me = b.players[b.current]
    opp = b.players[1 - b.current]
    me.active = make_pokemon(PokemonDef(
        me_name, hp=me_hp, ptype="colorless", attacks=tuple(me_attacks)))
    opp.active = make_pokemon(PokemonDef(
        opp_name, hp=opp_hp, ptype="colorless", attacks=tuple(opp_attacks)))
    me.hand = []
    me.deck = _deck()
    opp.deck = _deck()
    return me, opp


def _attach_tool(fp, tag: str, tdef: TrainerDef = None):
    """装配道具（`tool_ref` 必须同时给：静态层可能要从卡定义里读招式/生效条件）"""
    fp.tool = tag
    fp.tool_disabled = False
    if tdef is None:
        tdef = TrainerDef(tag, kind="tool", name="Tool")
    fp.tool_ref = TrainerRef(tdef)
    fp.tool_name = tdef.name


# ------------------------------ Hop's Choice Band ------------------------------
HOP_TAG = ("tool:cost_less:1:holder:faction:hops"
           "|tool:dmg_plus:30:holder:faction:hops")


class TestHopsChoiceBand(unittest.TestCase):
    """一句两修正：费用 −{C} 且 +30 伤害，只对 Hop's 系列生效"""

    def test_cost_and_damage_both_apply(self):
        b = _battle()
        atk = AttackDef("HopMove", cost=((("colorless"), 2),), damage=100)
        me, opp = _prep(b, me_name="Hop's Zacian ex", me_attacks=(atk,))
        _attach_tool(me.active, HOP_TAG)
        # 只贴 1 个能量：费用 2 → 减 1 → 1，付得出
        me.active.add_energy("colorless")
        self.assertTrue(b._can_pay_attack(me.active, atk),
                        "费用 −{C} 未生效（仍按 2 点校验）")
        self.assertEqual(_attack_cost_delta(b, me.active), 1,
                         "复合道具的首段（cost_less）没被读到")
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(opp.active.damage, 130,
                         "复合道具的第二段（dmg_plus +30）没被读到")

    def test_other_holder_gets_nothing(self):
        b = _battle()
        atk = AttackDef("Move", cost=((("colorless"), 2),), damage=100)
        me, opp = _prep(b, me_name="Zacian", me_attacks=(atk,))
        _attach_tool(me.active, HOP_TAG)
        me.active.add_energy("colorless")
        self.assertFalse(b._can_pay_attack(me.active, atk),
                         "非 Hop's 持有者也减了费（静默加强）")
        self.assertEqual(_attack_cost_delta(b, me.active), 0)
        me.active.add_energy("colorless")       # 补足 2 点再打
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(opp.active.damage, 100,
                         "非 Hop's 持有者也 +30 伤害（静默加强）")

    def test_disabled_tool_has_no_effect(self):
        """Jamming Tower 让道具失效 → 两段一起失效（不能只失效一段）"""
        b = _battle()
        atk = AttackDef("HopMove", cost=((("colorless"), 2),), damage=100)
        me, opp = _prep(b, me_name="Hop's Zacian ex", me_attacks=(atk,))
        _attach_tool(me.active, HOP_TAG)
        me.active.tool_disabled = True
        me.active.add_energy("colorless")
        self.assertFalse(b._can_pay_attack(me.active, atk))
        self.assertEqual(_attack_cost_delta(b, me.active), 0)


# ------------------------------ Megaton Blower ------------------------------
MB_TAG = "discard_tool_senergy_all:opp:stadium"


def _mb_legals(b: Battle) -> bool:
    return any(a.get("verb") == "trainer" and a.get("name") == "Megaton Blower"
               for a in b.legal_actions())


class TestMegatonBlower(unittest.TestCase):
    """弃对手全场道具 + 全部特殊能量 + 场上场地"""

    def _setup(self, *, with_tool=True, with_senergy=True, with_stadium=True):
        b = _battle()
        me, opp = _prep(b)
        me.hand = [TrainerRef(TrainerDef(MB_TAG, kind="item",
                                         name="Megaton Blower"))]
        opp.active.tool = "tool:hp_plus:50" if with_tool else None
        if with_senergy:
            opp.active.senergy = [EnergyRef(EnergyDef(
                "water", name="Special Water", tag="senergy:test"))]
        if with_stadium:
            b.stadium = {"name": "Test Stadium", "tag": "stadium:hp_plus:30"}
        return b, me, opp

    def test_discards_all_three(self):
        b, me, opp = self._setup()
        self.assertTrue(_mb_legals(b), "三段都满足却没进合法动作表")
        r = b.step({"verb": "trainer", "hand": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertFalse(opp.active.tool, "对手道具没被弃掉")
        self.assertEqual(opp.active.senergy, [], "对手特殊能量没被弃掉")
        self.assertIsNone(b.stadium, "场上场地没被弃掉")

    def test_playable_with_only_special_energy(self):
        """合法动作门槛必须取**或** —— 只按首段（道具）判 = 池内死卡"""
        b, me, opp = self._setup(with_tool=False, with_stadium=False)
        self.assertTrue(_mb_legals(b), "对手只有特殊能量时这张卡选不到")

    def test_playable_with_only_stadium(self):
        b, me, opp = self._setup(with_tool=False, with_senergy=False)
        self.assertTrue(_mb_legals(b), "场上有场地时这张卡选不到")

    def test_not_listed_when_nothing_to_discard(self):
        b, me, opp = self._setup(with_tool=False, with_senergy=False,
                                 with_stadium=False)
        self.assertFalse(_mb_legals(b), "三个目标都没有时空转的卡不该可打")


# ------------------------------ Core Memory ------------------------------
GEO = AttackDef("Geobuster", cost=((("fighting"), 4),), damage=350,
                effects=("discard_energy:all",))
CM_TAG = "tool:provide_attack:megazygarde"


def _cm_tdef() -> TrainerDef:
    return TrainerDef(CM_TAG, kind="tool", name="Core Memory",
                      tool_attack=GEO, tool_attack_holder="megazygarde")


class TestCoreMemory(unittest.TestCase):
    """道具提供招式（Technical Machine）：只有卡面指定的持有者能用"""

    def test_holder_can_use_tool_attack(self):
        b = _battle()
        base = AttackDef("Gaia Wave", cost=0, damage=10)
        me, opp = _prep(b, me_name="Mega Zygarde ex", me_attacks=(base,))
        _attach_tool(me.active, CM_TAG, _cm_tdef())
        for _ in range(4):                    # 费用 4 点格斗，付得出才进 legals
            me.active.add_energy("fighting")
        atks = _attacks_of(me.active)
        self.assertEqual([a.name for a in atks], ["Gaia Wave", "Geobuster"])
        # 合法动作表必须用**同一口径**（否则下标错位到别的招式）
        hits = [a for a in b.legal_actions()
                if a.get("verb") == "attack"]
        self.assertIn(1, [a.get("attack") for a in hits],
                      "道具提供的招式没进合法动作表")

    def test_tool_attack_needs_energy(self):
        """「You still need the necessary Energy」—— 费用照常校验"""
        b = _battle()
        base = AttackDef("Gaia Wave", cost=0, damage=10)
        me, opp = _prep(b, me_name="Mega Zygarde ex", me_attacks=(base,))
        _attach_tool(me.active, CM_TAG, _cm_tdef())
        self.assertFalse(b._can_pay_attack(me.active, GEO),
                         "没贴格斗能量也能用 Geobuster（费用没校验）")
        for _ in range(4):
            me.active.add_energy("fighting")
        self.assertTrue(b._can_pay_attack(me.active, GEO))

    def test_non_holder_cannot_use(self):
        b = _battle()
        base = AttackDef("Move", cost=0, damage=10)
        me, opp = _prep(b, me_name="Zygarde", me_attacks=(base,))
        _attach_tool(me.active, CM_TAG, _cm_tdef())
        self.assertEqual([a.name for a in _attacks_of(me.active)], ["Move"],
                         "非指定持有者也拿到了道具招式（静默加强）")

    def test_index_consistency_when_attacking(self):
        """真正打出道具招式：伤害来自 Geobuster（350），不是第一个招式"""
        b = _battle()
        base = AttackDef("Gaia Wave", cost=0, damage=10)
        me, opp = _prep(b, me_name="Mega Zygarde ex", me_attacks=(base,))
        _attach_tool(me.active, CM_TAG, _cm_tdef())
        for _ in range(4):
            me.active.add_energy("fighting")
        r = b.step({"verb": "attack", "attack": 1})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(opp.active.damage, 350,
                         "下标错位：打出的是印刷招式而不是道具招式")


if __name__ == "__main__":
    unittest.main()
