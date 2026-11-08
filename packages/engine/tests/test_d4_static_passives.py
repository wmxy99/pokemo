"""D4：静态被动族（队伍 +HP / 双属性 / 能力封锁 / 工具上限 / 条件加伤）

每个机制都是「整句卡面 → 一个 passive tag → **唯一**消费点」。本文件盯两件事：
机制本身生效，以及**不该生效时真的不生效**（这类卡最贵的错法是「条件写成常驻」）。
"""
import unittest

from engine.battle import Battle
from engine.cards import (AbilityDef, AttackDef, PokemonDef, PokeRef,
                          TrainerDef, TrainerRef, make_pokemon)
from engine.rules import compute_damage
from engine.state import GameConfig


def _deck(tag="A"):
    return [PokeRef(PokemonDef(f"Filler{tag}{i}", hp=100, ptype="colorless"))
            for i in range(40)]


def _battle(seed=5):
    return Battle(_deck("A"), _deck("B"), seed=seed,
                  cfg=GameConfig(first_turn_no_attack=False,
                                 first_turn_no_supporter=False))


class TestTeamHpPlus(unittest.TestCase):
    def test_team_hp_applies_to_all_and_does_not_stack(self):
        b = _battle()
        me = b.players[b.current]
        holder = make_pokemon(PokemonDef(
            "Dancer", hp=100, ptype="grass",
            abilities=(AbilityDef("Vibrant Dance",
                                  tag="passive:team_hp_plus:40",
                                  location="field"),)))
        me.active = holder
        mate = make_pokemon(PokemonDef("Mate", hp=100, ptype="grass"))
        me.bench = [mate]
        b._refresh_stadium_hp()
        self.assertEqual(me.active.max_hp, 140)
        self.assertEqual(mate.max_hp, 140, "队伍 +HP 对所有己方场上生效")
        # 第二张同名在场：卡面「doesn't stack」→ 上限不变
        me.bench.append(make_pokemon(PokemonDef(
            "Dancer2", hp=100, ptype="grass",
            abilities=(AbilityDef("Vibrant Dance",
                                  tag="passive:team_hp_plus:40",
                                  location="field"),))))
        b._refresh_stadium_hp()
        self.assertEqual(me.active.max_hp, 140, "不得叠加")


class TestDualType(unittest.TestCase):
    def test_dual_type_hits_both_weaknesses(self):
        attacker = make_pokemon(PokemonDef(
            "Double", hp=100, ptype="fighting",
            abilities=(AbilityDef("Double Type",
                                  tag="passive:dual_type:fighting,psychic",
                                  location="field"),)))
        # 守方弱点 = 超能（单一格斗属性打不出翻倍）
        defender = make_pokemon(PokemonDef("Psy", hp=100, ptype="psychic",
                                           weakness="psychic"))
        self.assertEqual(compute_damage(attacker, defender, 100), 200)

    def test_single_type_unchanged(self):
        attacker = make_pokemon(PokemonDef("Plain", hp=100, ptype="fighting"))
        defender = make_pokemon(PokemonDef("Psy", hp=100, ptype="psychic",
                                           weakness="psychic"))
        self.assertEqual(compute_damage(attacker, defender, 100), 100)


class TestAbilitySuppression(unittest.TestCase):
    def _holder(self, tag, *, active=True):
        return make_pokemon(PokemonDef(
            "Suppressor", hp=100, ptype="lightning", is_ex=True,
            abilities=(AbilityDef("Init", tag=tag, location="field"),)))

    def test_rulebox_ability_blocked(self):
        b = _battle()
        me, opp = b.players[b.current], b.players[1 - b.current]
        # ⚠ 封锁者放**对面**、被封锁的特性放**自己**这边：`legal_actions()`
        # 只列当前玩家的动作，把两者放同一边会让断言恒成立（假守卫）。
        opp.active = self._holder("passive:no_ability_rulebox:while:active"
                                  ":except:future")
        me.active = make_pokemon(PokemonDef(
            "FoeEx", hp=100, ptype="colorless", is_ex=True,
            abilities=(AbilityDef("Draw", tag="draw:1", location="field"),)))
        acts = [a for a in b.legal_actions()
                if a.get("verb") == "ability" and a.get("name") == "Draw"]
        self.assertEqual(acts, [], "Rule Box 宝可梦的特性应被封锁")
        r = b.step({"verb": "ability", "name": "Draw", "pos": 0,
                    "tag": "draw:1"})
        self.assertFalse(r["ok"])

    def test_future_pokemon_exempt(self):
        b = _battle()
        me, opp = b.players[b.current], b.players[1 - b.current]
        opp.active = self._holder("passive:no_ability_rulebox:while:active"
                                  ":except:future")
        me.active = make_pokemon(PokemonDef(
            "FutureEx", hp=100, ptype="colorless", is_ex=True,
            category="Future",
            abilities=(AbilityDef("Draw", tag="draw:1", location="field"),)))
        acts = [a for a in b.legal_actions()
                if a.get("verb") == "ability" and a.get("name") == "Draw"]
        self.assertEqual(len(acts), 1, "except for Future Pokémon")

    def test_selfko_cost_ability_blocked(self):
        b = _battle()
        me, opp = b.players[b.current], b.players[1 - b.current]
        opp.active = make_pokemon(PokemonDef(
            "Damp", hp=100, ptype="water",
            abilities=(AbilityDef("Damp", tag="passive:no_ability_selfko",
                                  location="field"),)))
        me.active = make_pokemon(PokemonDef(
            "Boomer", hp=100, ptype="colorless",
            abilities=(AbilityDef("Boom", tag="draw:1", location="field",
                                  cost="selfko"),)))
        acts = [a for a in b.legal_actions()
                if a.get("verb") == "ability" and a.get("name") == "Boom"]
        self.assertEqual(acts, [], "需要自身击倒的特性应失效")


class TestToolLimit(unittest.TestCase):
    def test_rotom_may_hold_two_tools(self):
        b = _battle()
        me = b.players[b.current]
        me.active = make_pokemon(PokemonDef("Rotom Fan", hp=100,
                                            ptype="colorless"))
        me.bench = [make_pokemon(PokemonDef("Damp", hp=100, ptype="water",
                                            abilities=(AbilityDef(
                                                "Multi Adapter",
                                                tag="passive:tool_limit:2"
                                                    ":name:rotom",
                                                location="field"),)))]
        tool = TrainerDef("tool:survive:10", kind="tool",
                           name="Tool A")
        me.hand = [TrainerRef(tool), TrainerRef(tool)]
        r1 = b.step({"verb": "trainer", "hand": 0, "target": 0})
        self.assertTrue(r1["ok"], r1.get("error"))
        r2 = b.step({"verb": "trainer", "hand": 0, "target": 0})
        self.assertTrue(r2["ok"], "名字含 Rotom → 可附第 2 张")

    def test_non_rotom_still_one(self):
        b = _battle()
        me = b.players[b.current]
        me.active = make_pokemon(PokemonDef("Plain", hp=100, ptype="colorless"))
        me.bench = [make_pokemon(PokemonDef("Damp", hp=100, ptype="water",
                                            abilities=(AbilityDef(
                                                "Multi Adapter",
                                                tag="passive:tool_limit:2"
                                                    ":name:rotom",
                                                location="field"),)))]
        tool = TrainerDef("tool:survive:10", kind="tool",
                           name="Tool A")
        me.hand = [TrainerRef(tool), TrainerRef(tool)]
        b.step({"verb": "trainer", "hand": 0, "target": 0})
        r2 = b.step({"verb": "trainer", "hand": 0, "target": 0})
        self.assertFalse(r2["ok"], "非 Rotom 仍然只能 1 张")


class TestStaticDmgPlus(unittest.TestCase):
    def test_conditional_dmg_plus_needs_energy(self):
        """`passive:dmg_plus:N:if:energy:<t>`：条件只依赖本只附着能量"""
        b = _battle()
        me, opp = b.players[b.current], b.players[1 - b.current]
        me.active = make_pokemon(PokemonDef(
            "Okidogi", hp=200, ptype="darkness",
            abilities=(AbilityDef("Adrena-Power",
                                  tag="passive:dmg_plus:100:if:energy:darkness",
                                  location="field"),),
            attacks=(AttackDef("Bash", cost=0, damage=50),)))
        opp.active = make_pokemon(PokemonDef("Target", hp=400,
                                             ptype="colorless"))
        # 没恶能量 → 不加伤
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(opp.active.damage, 50, "无恶能量时不加伤")
        # 附上恶能量 → +100
        b2 = _battle()
        me2, opp2 = b2.players[b2.current], b2.players[1 - b2.current]
        me2.active = make_pokemon(PokemonDef(
            "Okidogi", hp=200, ptype="darkness",
            abilities=(AbilityDef("Adrena-Power",
                                  tag="passive:dmg_plus:100:if:energy:darkness",
                                  location="field"),),
            attacks=(AttackDef("Bash", cost=0, damage=50),)))
        me2.active.add_energy("darkness", 1)
        opp2.active = make_pokemon(PokemonDef("Target", hp=400,
                                              ptype="colorless"))
        b2.step({"verb": "attack", "attack": 0})
        self.assertEqual(opp2.active.damage, 150, "有恶能量 → +100")


if __name__ == "__main__":      # pragma: no cover
    unittest.main()
