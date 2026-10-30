# -*- coding: utf-8 -*-
"""化石卡（Fossil）—— 引擎侧单测

覆盖卡面三条规则 + 上场形态：
  1. 「Play this card as if it were a 60-HP Basic {C} Pokémon」→ 铺到后备，
     HP / 属性 / 特性按卡面，**卡本身留在场上**（不进弃牌堆）；
  2. 「can't be affected by any Special Conditions」→ 状态施加被拦截；
  3. 「can't retreat」→ 撤退被拒绝（合法动作表也不列）；
  4. 「At any time during your turn, you may discard this card from play」
     → `discard_fossil` 把**训练家卡本体**送进弃牌堆；
  5. 进化后化石特权消失（不再免疫状态 / 不再不可撤退）。
"""
from __future__ import annotations

import random
import unittest

from engine.battle import Battle
from engine.cards import (AbilityDef, AttackDef, PokemonDef, TrainerDef,
                          TrainerRef, make_pokemon)
from engine.families import fx_attack_cost as C
from engine.state import GameConfig, PlayerState


def _arena(seed: int = 7):
    cfg = GameConfig()
    b = object.__new__(Battle)
    b.cfg = cfg
    b.seed = seed
    b.rng = random.Random(seed)
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
    b.players[0].active = make_pokemon(PokemonDef(
        "Attacker", hp=200, ptype="colorless",
        attacks=(AttackDef("Hit", cost=0, damage=60, effects=("poison",)),)))
    b.players[1].active = make_pokemon(PokemonDef(
        "Defender", hp=300, ptype="colorless"))
    return b


def _fossil(name="Fossil", ability_tag="", hp=60):
    abilities = ((AbilityDef("Fossil Ability", ability_tag),)
                 if ability_tag else ())
    pdef = PokemonDef(name, hp=hp, ptype="colorless", retreat=0, stage=0,
                      abilities=abilities)
    tdef = TrainerDef("fossil:60:colorless", kind="item", name=name,
                      fossil_pdef=pdef)
    return TrainerRef(tdef)


def _play(b, ref):
    p = b.players[b.current]
    p.hand.append(ref)
    return b.step({"verb": "trainer", "hand": len(p.hand) - 1})


def _fossil_active(b, owner: int = 1, ability_tag: str = ""):
    """把化石直接摆到 `owner` 的战斗位（等价于「上场后又被换到前排」）"""
    ref = _fossil(ability_tag=ability_tag)
    p = b.players[owner]
    fp = make_pokemon(ref.tdef.fossil_pdef)
    fp.fossil = True
    fp.fossil_ref = ref
    if p.active is not None:
        p.bench.append(p.active)
    p.active = fp
    return fp, ref


class TestFossilPlay(unittest.TestCase):
    def test_play_as_basic_pokemon(self):
        b = _arena()
        ref = _fossil()
        r = _play(b, ref)
        self.assertTrue(r["ok"], r.get("error"))
        p = b.players[0]
        self.assertEqual(len(p.bench), 1)
        fp = p.bench[0]
        self.assertTrue(fp.fossil)
        self.assertEqual(fp.max_hp, 60)
        self.assertEqual(fp.stage, 0)
        self.assertEqual(fp.pdef.ptype, "colorless")
        self.assertEqual(ref.tdef.name, fp.name)
        # 卡本体留在场上：手牌空、弃牌堆空
        self.assertEqual(len(p.hand), 0)
        self.assertEqual(len(p.discard), 0)
        self.assertTrue(fp.just_played)

    def test_bench_full_rejected(self):
        b = _arena()
        p = b.players[0]
        for i in range(b.cfg.max_bench):
            p.bench.append(make_pokemon(PokemonDef(f"B{i}", hp=10,
                                                   ptype="colorless")))
        ref = _fossil()
        p.hand.append(ref)
        r = b.step({"verb": "trainer", "hand": len(p.hand) - 1})
        self.assertFalse(r["ok"])
        self.assertEqual(len(p.bench), b.cfg.max_bench)

    def test_legals_hidden_when_bench_full(self):
        b = _arena()
        p = b.players[0]
        p.hand.append(_fossil())
        self.assertTrue(any(a["verb"] == "trainer" and
                            str(a.get("tag", "")).startswith("fossil:")
                            for a in b.legal_actions()))
        for i in range(b.cfg.max_bench):
            p.bench.append(make_pokemon(PokemonDef(f"B{i}", hp=10,
                                                   ptype="colorless")))
        self.assertFalse(any(str(a.get("tag", "")).startswith("fossil:")
                             for a in b.legal_actions()))


class TestFossilRestrictions(unittest.TestCase):
    def _with_fossil_active(self):
        b = _arena()
        _play(b, _fossil())
        p = b.players[0]
        p.active, p.bench[0] = p.bench[0], p.active
        self.assertTrue(p.active.fossil)
        return b

    def test_cannot_retreat(self):
        b = self._with_fossil_active()
        self.assertFalse(any(a["verb"] == "retreat" for a in b.legal_actions()))
        r = b.step({"verb": "retreat", "bench": 0})
        self.assertFalse(r["ok"])
        self.assertIn("化石", r["error"])

    def test_status_immune_from_attack(self):
        b = _arena()
        _fossil_active(b, owner=1)                 # 化石在对手（被攻击方）战斗位
        b.players[0].active = make_pokemon(PokemonDef(
            "Poisoner", hp=200, ptype="colorless",
            attacks=(AttackDef("Tox", cost=0, damage=0, effects=("poison",)),)))
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(sorted(b.players[1].active.status), [])

    def test_status_immune_from_ability(self):
        b = _arena()
        _fossil_active(b, owner=1)
        b.players[0].active = make_pokemon(PokemonDef(
            "Caster", hp=200, ptype="colorless",
            abilities=(AbilityDef("Hex", "poison"),)))
        r = b.step({"verb": "ability", "pos": 0, "name": "Hex", "tag": "poison"})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(sorted(b.players[1].active.status), [])


class TestFossilDiscard(unittest.TestCase):
    def test_discard_fossil_puts_trainer_card_in_discard(self):
        b = _arena()
        ref = _fossil()
        _play(b, ref)
        p = b.players[0]
        acts = [a for a in b.legal_actions() if a["verb"] == "discard_fossil"]
        self.assertEqual(len(acts), 1)
        self.assertEqual(acts[0]["target"], 1)      # 后备 0 → 位置码 1
        r = b.step({"verb": "discard_fossil", "target": 1})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(p.bench), 0)
        self.assertEqual(p.discard, [ref.tdef.name])
        self.assertIsInstance(p.discard_refs[0], TrainerRef)

    def test_discard_active_with_spare_promotes(self):
        b = _arena()
        _play(b, _fossil())
        p = b.players[0]
        p.active, p.bench[0] = p.bench[0], p.active   # 化石上前排，Attacker 进后备
        r = b.step({"verb": "discard_fossil", "target": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(p.active.name, "Attacker")
        self.assertEqual(len(p.bench), 0)
        self.assertEqual(p.discard, ["Fossil"])

    def test_discard_active_without_spare_rejected(self):
        """战斗位无替补时不得弃置（否则直接判负，属强制非法动作）"""
        b = _arena()
        _play(b, _fossil())
        p = b.players[0]
        p.active = p.bench.pop(0)                     # 前排化石，后备为空
        self.assertEqual(len(p.bench), 0)
        self.assertFalse(any(a["verb"] == "discard_fossil"
                             for a in b.legal_actions()))
        r = b.step({"verb": "discard_fossil", "target": 0})
        self.assertFalse(r["ok"])
        self.assertEqual(len(p.discard), 0)

    def test_discard_non_fossil_rejected(self):
        b = _arena()
        r = b.step({"verb": "discard_fossil", "target": 1})
        self.assertFalse(r["ok"])

    def test_knockout_returns_trainer_card_to_discard(self):
        """被击倒的化石按宝可梦结算奖赏卡，弃牌堆里放回**训练家卡对象**"""
        b = _arena()
        _play(b, _fossil("Antique Test Fossil"))
        p, opp = b.players[0], b.players[1]
        fp = p.bench[0]
        fp.damage = fp.max_hp
        b._ko_from_attack = True
        b._resolve_ko(0, fp)
        self.assertEqual(p.bench, [])
        self.assertEqual(p.discard, ["Antique Test Fossil"])
        self.assertIsInstance(p.discard_refs[0], TrainerRef)
        self.assertEqual(opp.prizes_left, b.cfg.prizes - 1)


class TestFossilEvolution(unittest.TestCase):
    def test_evolve_clears_fossil_privileges(self):
        b = _arena()
        p = b.players[0]
        _play(b, _fossil("Antique Test Fossil"))
        p.bench[0].just_played = False        # 模拟「下个回合再进化」
        from engine.cards import PokeRef
        p.hand.append(PokeRef(PokemonDef(
            "Test Evolution", hp=100, ptype="colorless", stage=1,
            evolves_from="Antique Test Fossil")))
        r = b.step({"verb": "evolve", "hand": len(p.hand) - 1, "target": 1})
        self.assertTrue(r["ok"], r.get("error"))
        fp = p.bench[0]
        self.assertEqual(fp.name, "Test Evolution")
        self.assertFalse(fp.fossil)
        self.assertIsNone(fp.fossil_ref)


class TestFossilAttackCost(unittest.TestCase):
    def _battle(self, fossil_active: bool, attacker_stage: int = 0):
        b = _arena()
        p = b.players[0]
        b.players[0].active = make_pokemon(PokemonDef(
            "BasicAttacker" if attacker_stage == 0 else "StageAttacker",
            hp=200, ptype="colorless", stage=attacker_stage,
            attacks=(AttackDef("Hit", cost=(("fire", 1),), damage=10),)))
        b.players[1].active = make_pokemon(PokemonDef(
            "FossilHolder", hp=60, ptype="colorless", stage=0,
            abilities=(AbilityDef("Primal Root",
                                  "fx:attack_cost:opp_basic:1:colorless"),)))
        b.players[1].active.fossil = True
        if not fossil_active:
            b.players[1].bench.append(b.players[1].active)
            b.players[1].active = make_pokemon(PokemonDef("Other", hp=60,
                                                          ptype="colorless"))
        return b

    def test_basic_attack_costs_more(self):
        b = self._battle(fossil_active=True)
        p = b.players[0]
        p.active.add_energy("fire")
        # 只有 1 个火能量：原本够付 {R}，被 +1{C} 后不够
        self.assertFalse(b._can_pay_attack(p.active, p.active.pdef.attacks[0]))
        self.assertFalse(any(a["verb"] == "attack" for a in b.legal_actions()))
        p.active.add_energy("colorless")
        self.assertTrue(b._can_pay_attack(p.active, p.active.pdef.attacks[0]))

    def test_stage1_attack_unaffected(self):
        b = self._battle(fossil_active=True, attacker_stage=1)
        p = b.players[0]
        p.active.add_energy("fire")
        self.assertTrue(b._can_pay_attack(p.active, p.active.pdef.attacks[0]))

    def test_not_active_no_effect(self):
        b = self._battle(fossil_active=False)
        p = b.players[0]
        p.active.add_energy("fire")
        self.assertTrue(b._can_pay_attack(p.active, p.active.pdef.attacks[0]))


class TestAttackCostTagValidation(unittest.TestCase):
    def test_unknown_scope_raises(self):
        b = _arena()
        fp = make_pokemon(PokemonDef(
            "X", hp=10, ptype="colorless",
            abilities=(AbilityDef("A", "fx:attack_cost:team:1:colorless"),)))
        b.players[1].active = fp
        with self.assertRaises(ValueError):
            C.attack_cost(b, b.players[0].active, None)

    def test_unknown_color_raises(self):
        b = _arena()
        fp = make_pokemon(PokemonDef(
            "X", hp=10, ptype="colorless",
            abilities=(AbilityDef("A", "fx:attack_cost:opp_basic:1:fire"),)))
        b.players[1].active = fp
        with self.assertRaises(ValueError):
            C.attack_cost(b, b.players[0].active, None)

    def test_no_tag_zero(self):
        b = _arena()
        self.assertEqual(C.attack_cost(b, b.players[0].active, None), {})


if __name__ == "__main__":
    unittest.main()
