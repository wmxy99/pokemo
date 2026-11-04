# -*- coding: utf-8 -*-
"""第六十三~六十七批 · 引擎侧行为测试（2026-11-04）

重点在唯一改到**伤害计算**的第六十五批：`ignore_effects` 两档反口径。
  · `ignore_effects`     只无视守方身上的效果（护盾/减伤/道具/场地），
                         **弱点/抗性照常结算**（Koraidon「Shred」旧语义）；
  · `ignore_effects:wr`  卡面多写了「Weakness or Resistance」，连印在卡上的
                         弱抗一并无视。用窄口径接宽句式会静默漏掉一半效果。
两处伤害计算（战斗位 / snipe）共用 `_ignore_effects_flags`，未知形态抛错。
另含第六十六批 `own_discard_named:kofu` 计数源的求值回归。
"""
from __future__ import annotations

import random
import unittest

from engine.battle import Battle, _eval_counter, _ignore_effects_flags
from engine.cards import (AbilityDef, AttackDef, PokemonDef, PokeRef,
                          make_pokemon)
from engine.state import GameConfig, PlayerState


def _arena(seed: int = 5) -> Battle:
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
        "Attacker", hp=300, ptype="colorless",
        attacks=(AttackDef("Hit", cost=0, damage=20),)))
    b.players[1].active = make_pokemon(PokemonDef(
        "Defender", hp=300, ptype="colorless",
        attacks=(AttackDef("Back", cost=1, damage=10),)))
    for i in range(2):
        b.players[i].deck = [
            PokeRef(PokemonDef(f"D{i}{k}", hp=10, ptype="colorless"))
            for k in range(6)]
    return b


class TestIgnoreEffectsWR(unittest.TestCase):
    def _battle(self, effects):
        b = _arena()
        b.players[0].active = make_pokemon(PokemonDef(
            "Koraidon", hp=200, ptype="fighting",
            attacks=(AttackDef("Shred", cost=0, damage=130,
                               effects=effects),)))
        return b

    def _wall(self, b, *, weakness=None, reduce_n=0):
        abilities = ()
        if reduce_n:
            abilities = (AbilityDef("Walls",
                                    f"passive:dmg_reduce:{reduce_n}"),)
        b.players[1].active = make_pokemon(PokemonDef(
            "GrassWall", hp=300, ptype="grass", weakness=weakness,
            abilities=abilities))
        return b

    # ---- 两档反口径 ----
    def test_wr_bypasses_weakness(self):
        """宽口径：弱点 ×2 不生效 → 130（不是 260）"""
        b = self._battle(("ignore_effects:wr",))
        self._wall(b, weakness="fighting")
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 130)

    def test_narrow_still_applies_weakness(self):
        """窄口径回归：弱点照常 ×2 → 260（旧语义不能被改坏）"""
        b = self._battle(("ignore_effects",))
        self._wall(b, weakness="fighting")
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 260)

    def test_no_tag_still_applies_weakness(self):
        b = self._battle(())
        self._wall(b, weakness="fighting")
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 260)

    def test_wr_still_bypasses_defender_effects(self):
        """宽口径仍要无视守方减伤效果（不能只顾弱抗、丢了旧能力）"""
        b = self._battle(("ignore_effects:wr",))
        self._wall(b, reduce_n=30)
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 130)

    def test_wr_bypasses_both_weakness_and_reduce(self):
        b = self._battle(("ignore_effects:wr",))
        self._wall(b, weakness="fighting", reduce_n=30)
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.players[1].active.damage, 130)

    # ---- 严格校验（纪律：认不出的形态必须抛错，绝不静默降级）----
    def test_flags_helper(self):
        self.assertEqual(_ignore_effects_flags(("ignore_effects",)),
                         (True, False))
        self.assertEqual(_ignore_effects_flags(("ignore_effects:wr",)),
                         (True, True))
        self.assertEqual(_ignore_effects_flags(()), (False, False))
        self.assertEqual(
            _ignore_effects_flags(("draw:2", "ignore_effects:wr", "snipe:opp:10:1")),
            (True, True))

    def test_unknown_suffix_raises(self):
        """未知后缀若被当成窄口径 → 弱抗那一半静默漏掉，必须抛错"""
        with self.assertRaises(ValueError):
            _ignore_effects_flags(("ignore_effects:bogus",))

    def test_unknown_suffix_rejected_in_battle(self):
        """`step()` 把异常转成 `ok=False`，但错误信息必须来自严格校验"""
        b = self._battle(("ignore_effects:bogus",))
        r = b.step({"verb": "attack", "attack": 0})
        self.assertFalse(r["ok"], r)
        self.assertIn("未识别的 ignore_effects 形态",
                      str(r.get("error", "")), r)


class TestKofuCounter(unittest.TestCase):
    """第六十六批：`own_discard_named:kofu` 按卡名子串数弃牌堆"""

    def test_counts_named_cards_only(self):
        b = _arena()
        b.players[0].discard_refs = [
            PokeRef(PokemonDef("Kofu's Psyduck", hp=10, ptype="colorless")),
            PokeRef(PokemonDef("Kofu's Psyduck", hp=10, ptype="colorless")),
            PokeRef(PokemonDef("Misty's Psyduck", hp=10, ptype="colorless")),
        ]
        self.assertEqual(_eval_counter(b, "own_discard_named:kofu"), 2)

    def test_zero_when_no_kofu(self):
        b = _arena()
        b.players[0].discard_refs = [
            PokeRef(PokemonDef("Pikachu", hp=10, ptype="colorless"))]
        self.assertEqual(_eval_counter(b, "own_discard_named:kofu"), 0)

    def test_discount_applies_to_attack_cost(self):
        """2 张 Kofu → 无色门槛 -2；3 能量的招式只需 1 能量即可出手"""
        b = _arena()
        b.players[0].active = make_pokemon(PokemonDef(
            "Crabominable", hp=200, ptype="fighting",
            abilities=(AbilityDef(
                "Food Prep",
                "fx:attack_cost:self:less:per:own_discard_named:kofu"),),
            attacks=(AttackDef("Punch", cost=3, damage=10),)))
        b.players[0].discard_refs = [
            PokeRef(PokemonDef("Kofu's Lesson", hp=10, ptype="colorless")),
            PokeRef(PokemonDef("Kofu's Lesson", hp=10, ptype="colorless")),
        ]
        atk = b.players[0].active.pdef.attacks[0]
        self.assertFalse(b._can_pay_attack(b.players[0].active, atk))
        b.players[0].active.add_energy("colorless")
        self.assertTrue(b._can_pay_attack(b.players[0].active, atk))
        b.players[0].discard_refs.clear()
        self.assertFalse(b._can_pay_attack(b.players[0].active, atk))


if __name__ == "__main__":
    unittest.main()
