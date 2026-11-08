"""D3：退化 devolve（引擎侧）

覆盖两类卡面写法与四条「丢了限定词也不报错」的错法：

  · 对手 / 最高阶 1 张（Archeops，特性，需在战斗位）
  · 自己 / **任意张**（Strange Timepiece，道具，限超能属性）

以及官方两条时序规则：退化当回合**不能再进化**、退化后剩 HP ≤0 **直接击倒**。
"""
import unittest

from engine.battle import Battle
from engine.cards import (AbilityDef, AttackDef, PokemonDef, PokeRef,
                          TrainerDef, TrainerRef, make_pokemon)
from engine.state import GameConfig


def _deck(tag="A"):
    return [PokeRef(PokemonDef(f"Filler{tag}{i}", hp=100, ptype="colorless"))
            for i in range(40)]


def _battle(seed=5):
    return Battle(_deck("A"), _deck("B"), seed=seed,
                  cfg=GameConfig(first_turn_no_attack=False,
                                 first_turn_no_supporter=False))


BASIC = PokemonDef("Tiny", hp=60, ptype="psychic")
STAGE1 = PokemonDef("Mid", hp=100, ptype="psychic", stage=1,
                    evolves_from="Tiny")
STAGE2 = PokemonDef("Big", hp=160, ptype="psychic", stage=2, evolves_from="Mid")


def _evolved_to_stage2(*, hp_damage=0):
    """造一只「基础 → 一阶 → 二阶」的完整堆叠"""
    fp = make_pokemon(BASIC)
    fp.evolve_to(STAGE1)
    fp.evolve_to(STAGE2)
    assert [d.name for d in fp.under] == ["Tiny", "Mid"], fp.under
    fp.damage = hp_damage
    return fp


class TestDevolveBasics(unittest.TestCase):
    def test_one_step_returns_highest_card_to_hand(self):
        b = _battle()
        me = b.players[b.current]
        me.active = _evolved_to_stage2()
        me.hand = []                      # 起手牌会干扰「回手牌」断言
        r = b._do_devolve_now(b.current, 0, 1)
        self.assertIn("Big -> Mid", r)
        self.assertEqual(me.active.pdef.name, "Mid")
        self.assertEqual([d.name for d in me.active.under], ["Tiny"])
        self.assertEqual([c.pdef.name for c in me.hand], ["Big"])

    def test_keeps_damage_and_energy(self):
        """退化**不回血**、也不掉能量（官方：只有进化卡回手牌）"""
        b = _battle()
        me = b.players[b.current]
        me.active = _evolved_to_stage2(hp_damage=50)
        me.active.add_energy("psychic", 2)
        b._do_devolve_now(b.current, 0, 1)
        self.assertEqual(me.active.damage, 50)
        self.assertEqual(me.active.total_energy(), 2)

    def test_keeps_special_conditions(self):
        """退化**不清除**特殊状态（清状态是「进化」的明文规则，退化没有对应条文）"""
        b = _battle()
        me = b.players[b.current]
        me.active = _evolved_to_stage2()
        me.active.status.update({"poison"})
        b._do_devolve_now(b.current, 0, 1)
        self.assertIn("poison", me.active.status)

    def test_devolution_overflow_is_clamped(self):
        """要退的阶数超过堆叠深度 → 退到基础形态为止（不抛错、不凭空生成）"""
        b = _battle()
        me = b.players[b.current]
        me.active = _evolved_to_stage2()
        me.hand = []
        b._do_devolve_now(b.current, 0, 9)
        self.assertEqual(me.active.pdef.name, "Tiny")
        self.assertEqual(me.active.under, [])
        self.assertEqual(len(me.hand), 2)


class TestDevolveTiming(unittest.TestCase):
    def test_cannot_evolve_again_same_turn(self):
        """官方：退化过的宝可梦**当回合不能再进化**"""
        b = _battle()
        me = b.players[b.current]
        me.active = _evolved_to_stage2()
        me.active.just_played = False
        b._do_devolve_now(b.current, 0, 1)
        me.hand = [PokeRef(STAGE2)]
        r = b.step({"verb": "evolve", "hand": 0, "target": 0})
        self.assertFalse(r["ok"], "退化当回合不应能再进化")
        self.assertIn("退化", r["error"])

    def test_can_evolve_next_turn(self):
        b = _battle()
        me = b.players[b.current]
        me.active = _evolved_to_stage2()
        me.active.just_played = False
        b._do_devolve_now(b.current, 0, 1)
        b.current = 1 - b.current
        b.turn_number += 1
        b.current = 1 - b.current
        b.turn_number += 1
        me.hand = [PokeRef(STAGE2)]
        r = b.step({"verb": "evolve", "hand": 0, "target": 0})
        self.assertTrue(r["ok"], r.get("error"))

    def test_devolution_that_drops_hp_to_zero_is_ko(self):
        """退化后上限变低、伤害已超 → 直接击倒（官方：剩 HP ≤0 即被击倒）"""
        b = _battle()
        me, opp = b.players[b.current], b.players[1 - b.current]
        me.active = _evolved_to_stage2(hp_damage=120)   # 160 HP 形态剩 40
        opp.active = make_pokemon(PokemonDef("Wall", hp=200, ptype="colorless"))
        before = opp.prizes_left
        b._do_devolve_now(b.current, 0, 1)              # → 100 HP 形态，伤害 120
        # 击倒已结算：对手取 1 张奖赏，战斗位由后备补上（`is_ko` 的那只已离场，
        # 所以不能对 `me.active` 断言 is_ko —— 那是「补位后的新宝可梦」）
        self.assertEqual(opp.prizes_left, before - 1, "退化致击倒后对手取 1 张奖赏")
        self.assertTrue(me.active.pdef.name.startswith("Filler"), "后备补位")


class TestDevolveAnyNumber(unittest.TestCase):
    def test_any_number_opens_choice(self):
        """「any number of Evolution cards」= **玩家选数量**，引擎不得代选"""
        b = _battle()
        me = b.players[b.current]
        me.active = _evolved_to_stage2()
        me.hand = [TrainerRef(TrainerDef("devolve:self:any:type:psychic",
                                         kind="item", name="Strange Timepiece"))]
        r = b.step({"verb": "trainer", "hand": 0, "target": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.phase, "choice")
        opts = [a for a in b.legal_actions() if a.get("verb") == "choose"]
        self.assertEqual(len(opts), 2, "可退 1 阶或 2 阶 → 两个选项")

    def test_choose_two_devolves_to_basic(self):
        b = _battle()
        me = b.players[b.current]
        me.active = _evolved_to_stage2()
        me.hand = [TrainerRef(TrainerDef("devolve:self:any:type:psychic",
                                         kind="item", name="Strange Timepiece"))]
        b.step({"verb": "trainer", "hand": 0, "target": 0})
        opt = next(a for a in b.legal_actions()
                   if a.get("verb") == "choose"
                   and a["option"].get("count") == 2)
        r = b.step({"verb": "choose", "pick": opt["pick"]})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(me.active.pdef.name, "Tiny")
        self.assertEqual(sorted(c.pdef.name for c in me.hand), ["Big", "Mid"])

    def test_type_filter_rejects_wrong_type(self):
        """卡面限 `{P}`：非超能目标不进合法动作表"""
        b = _battle()
        me = b.players[b.current]
        me.active = make_pokemon(BASIC)
        me.active.evolve_to(STAGE1)
        me.active.pdef = PokemonDef("Water Evo", hp=100, ptype="water",
                                    stage=1, evolves_from="Tiny")
        me.hand = [TrainerRef(TrainerDef("devolve:self:any:type:psychic",
                                         kind="item", name="Strange Timepiece"))]
        acts = [a for a in b.legal_actions() if a.get("verb") == "trainer"]
        self.assertEqual(acts, [], "属性不符时不该列出这张卡")


class TestDevolveAbility(unittest.TestCase):
    def _mon(self, *, active_spot):
        return make_pokemon(PokemonDef(
            "Archeops", hp=150, ptype="colorless", stage=2, evolves_from="Archen",
            abilities=(AbilityDef("Ancient Wing", tag="devolve:opp:1:active_only",
                                  location="field"),)))

    def _setup(self):
        b = _battle()
        me, opp = b.players[b.current], b.players[1 - b.current]
        opp.active = _evolved_to_stage2()
        return b, me, opp

    def test_usable_when_active(self):
        b, me, opp = self._setup()
        me.active = self._mon(active_spot=True)
        acts = [a for a in b.legal_actions()
                if a.get("verb") == "ability" and a.get("name") == "Ancient Wing"]
        self.assertEqual(len(acts), 1, acts)
        r = b.step(acts[0])
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(opp.active.pdef.name, "Mid")
        self.assertIn("Big", [c.pdef.name for c in opp.hand],
                      "最高阶进化卡进**对手**手牌")

    def test_blocked_when_on_bench(self):
        """「if this Pokémon is in the Active Spot」是发动条件 → 备战位不列动作"""
        b, me, opp = self._setup()
        me.bench = [self._mon(active_spot=False)]
        acts = [a for a in b.legal_actions()
                if a.get("verb") == "ability" and a.get("name") == "Ancient Wing"]
        self.assertEqual(acts, [])

    def test_no_action_without_evolved_target(self):
        b = _battle()
        me, opp = b.players[b.current], b.players[1 - b.current]
        opp.active = make_pokemon(BASIC)         # 基础宝可梦，无可退
        me.active = self._mon(active_spot=True)
        acts = [a for a in b.legal_actions()
                if a.get("verb") == "ability" and a.get("name") == "Ancient Wing"]
        self.assertEqual(acts, [], "对手没有进化宝可梦时不该列动作")


if __name__ == "__main__":      # pragma: no cover
    unittest.main()
