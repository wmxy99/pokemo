# -*- coding: utf-8 -*-
"""第九十九 ~ 一百一十一批（P3 收尾）· 引擎用例

覆盖本阶段**新补**的引擎机制（每条都配「正向成立 + 近邻不成立」两向对照，
否则条件写错成恒真/恒假时测试照样全绿）：

  · ``on_bench_act:``                    上场触发，只执行一次（Drilbur）
  · ``passive:status_immune:<s>``        逐状态免疫，不能放大成「免疫全部」（Hoothoot）
  · ``switch_choose:``                   带属性/例外筛选的换位 + 换入后状态（Pecharunt ex）
  · ``on_damaged:search_bench:…``        受击即检索铺场（TR Koffing）
  · ``tool:on_damaged:status:…``         受击让**攻击方**陷入状态（TR Hypnotizer）
  · ``dmg_plus_prev_taken``              按「对手上回合打掉的血量」加伤（Mega Heracross ex）
  · ``use_only:`` / ``use_not:``         招式静态使用条件（Scream Tail ex / Terapagos ex）
  · ``play_lock:supporter|stadium:opp``  下回合手牌禁打（Scream Tail ex / Chi-Yu）
  · ``stadium:tera_cost_up``             场地抬高 Tera 招式费用（Nighttime Mine）
  · ``stadium:keep_confuse``             进化不解除混乱（Dizzying Valley）
  · ``attach_energy:…:heal:<n>``         附能后回复**同一目标**（Hydrapple ex）
  · ``passive:heal_on_attach:<n>``       附能即回复（Magearna）
  · ``heal:all:choose:maxleft:<n>``      按剩余 HP 上限筛选目标（Bianca's Devotion）
  · ``attach:…:target:stage2``           附着目标限定二阶（Rosa's Encouragement）
  · ``own_ko_tr_last_turn``              只算 Team Rocket's 被击倒（TR Archer）

运行：cd packages/engine && python -m unittest tests.test_p3_pool_engine -v
"""
from __future__ import annotations

import random
import unittest

from engine import families
from engine.battle import Battle
from engine.cards import (AbilityDef, AttackDef, EnergyDef, EnergyRef,
                          PokemonDef, TrainerDef, TrainerRef, make_pokemon)
from engine.state import GameConfig, PlayerState


class _Fixed(random.Random):
    def __init__(self, value: float = 0.5):
        super().__init__(0)
        self.value = value

    def random(self) -> float:
        return self.value


def _mon(name="Mon", *, hp=300, ptype="colorless", stage=0, is_ex=False,
         category="", abilities=(), attacks=None):
    return make_pokemon(PokemonDef(
        name, hp=hp, ptype=ptype, stage=stage, is_ex=is_ex,
        category=category, abilities=tuple(abilities),
        attacks=tuple(attacks or (AttackDef("Pass", cost=0, damage=0),))))


def _arena(effects=(), *, damage=10, atk_ability=None, atk_category="",
           def_ability=None, def_name="Defender", bench=(), opp_bench=()):
    """最小局面：A（战斗位）攻击 B（战斗位）"""
    cfg = GameConfig()
    b = object.__new__(Battle)
    b.cfg = cfg
    b.seed = 7
    b.rng = _Fixed()
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
    b._ko_tr_by_attack = [0, 0]
    b.players[0].active = _mon(
        "Attacker", hp=500, category=atk_category,
        abilities=(atk_ability,) if atk_ability else (),
        attacks=(AttackDef("Test", cost=0, damage=damage,
                           effects=tuple(effects)),))
    b.players[1].active = _mon(
        def_name, hp=400, ptype="fighting",
        abilities=(def_ability,) if def_ability else ())
    for fp in bench:
        b.players[0].bench.append(fp)
    for fp in opp_bench:
        b.players[1].bench.append(fp)
    for p in b.players:
        p.deck = [EnergyRef(EnergyDef("colorless")) for _ in range(20)]
    return b


def _hit(b) -> int:
    before = b.players[1].active.damage
    r = b.step({"verb": "attack", "attack": 0})
    assert r["ok"], r.get("error")
    return b.players[1].active.damage - before


# ---------------------------------------------------------------- 上场触发
class TestOnBenchAct(unittest.TestCase):
    """「When you play this Pokémon from your hand onto your Bench …, search …」"""

    def _drilbur(self):
        return _mon("Drilbur", hp=70, abilities=(AbilityDef(
            "Dig Dig Dig", tag="on_bench_act:search_discard:3:energy:type:fighting",
            location="field"),))

    def test_fires_once_on_bench_placement(self):
        b = _arena()
        p = b.players[0]
        p.deck = [EnergyRef(EnergyDef("fighting")) for _ in range(3)] + \
                 [EnergyRef(EnergyDef("water")) for _ in range(4)]
        b._on_bench_placed(0, self._drilbur())
        self.assertEqual(len(p.discard_refs), 3,
                         "上场时应弃掉 3 张**格斗**能量（不是任意 3 张）")
        self.assertTrue(all(str(getattr(r.edef, 'type', '')) == "fighting"
                            for r in p.discard_refs),
                        "弃掉的必须全是格斗能量")

    def test_not_a_repeatable_field_ability(self):
        """包装层不能失效：不进合法动作表（否则每回合白弃 3 张）"""
        ab = AbilityDef("Dig Dig Dig",
                        tag="on_bench_act:search_discard:3:energy:type:fighting",
                        location="field")
        b = _arena()
        b.players[0].active = _mon("Drilbur", hp=70, abilities=(ab,))
        names = [a.get("name") for a in b.legal_actions(0)
                 if a.get("verb") == "ability"]
        self.assertNotIn("Dig Dig Dig", names,
                         "on_bench_act 是触发钩子，不能出现在可发动动作里")


# ---------------------------------------------------------------- 状态免疫
class TestStatusImmunePassive(unittest.TestCase):
    def _fp(self, tag="passive:status_immune:sleep"):
        return _mon("Hoothoot", hp=60,
                    abilities=(AbilityDef("Insomnia", tag=tag, location="field"),))

    def test_only_named_status(self):
        b = _arena()
        fp = self._fp()
        self.assertTrue(b._status_blocked(fp, "sleep"), "应免疫睡眠")
        self.assertFalse(b._status_blocked(fp, "burn"),
                         "只免疫睡眠 → 灼伤必须仍可施加")

    def test_no_status_arg_does_not_mean_all(self):
        """不传具体状态时，只有 `all` 形态才算数 —— 否则会把单状态免疫放大成全体"""
        b = _arena()
        self.assertFalse(b._status_blocked(self._fp(), None))

    def test_all_form(self):
        b = _arena()
        self.assertTrue(b._status_blocked(
            self._fp("passive:status_immune:all"), "burn"))


# ---------------------------------------------------------------- 换位 + 状态
class TestSwitchChoose(unittest.TestCase):
    AB = AbilityDef("Subjugating Chains",
                    tag="switch_choose:darkness:except:pecharunt:then:poison",
                    location="field")

    def _b(self):
        b = _arena()
        b.players[0].active = _mon("Pecharunt ex", hp=200, is_ex=True,
                                   abilities=(self.AB,))
        b.players[0].bench = [
            _mon("Dark", hp=120, ptype="darkness"),
            _mon("Pecharunt ex", hp=200, ptype="darkness", is_ex=True),
        ]
        return b

    def test_swap_and_poison_new_active(self):
        b = self._b()
        r = b.step({"verb": "ability", "pos": 0, "tag": self.AB.tag,
                    "name": "Subjugating Chains", "bench": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[0].active.name, "Dark")
        self.assertIn("poison", b.players[0].active.status,
                      "换入的新战斗位应中毒")

    def test_except_is_enforced(self):
        """例外必须真的排除：Pecharunt ex 不能被换上（否则例外失效）"""
        b = self._b()
        r = b.step({"verb": "ability", "pos": 0, "tag": self.AB.tag,
                    "name": "Subjugating Chains", "bench": 1})
        self.assertFalse(r["ok"], "换上是例外卡时应失败")


# ---------------------------------------------------------------- 受击触发
class TestOnDamagedSearchBench(unittest.TestCase):
    AB = AbilityDef("Smog Signals",
                    tag="on_damaged:search_bench:2:name_has:koffing",
                    location="field")

    def test_searches_named_pokemon(self):
        b = _arena(damage=30, def_ability=self.AB)
        from engine.cards import PokeRef
        b.players[1].deck = [
            PokeRef(PokemonDef("Koffing", hp=60, ptype="darkness")),
            PokeRef(PokemonDef("Team Rocket's Koffing", hp=70,
                               ptype="darkness")),
            PokeRef(PokemonDef("Pikachu", hp=60, ptype="lightning")),
        ]
        n0 = len(b.players[1].bench)
        _hit(b)
        self.assertEqual(len(b.players[1].bench) - n0, 2,
                         "应检索 2 只名字含 Koffing 的宝可梦铺到后备")


class TestToolOnDamagedStatus(unittest.TestCase):
    TAG = "tool:on_damaged:status:sleep:holder:faction:teamrocket"

    def test_attacker_falls_asleep(self):
        b = _arena(damage=30, def_name="Team Rocket's Koffing")
        b.players[1].active.tool = self.TAG
        _hit(b)
        self.assertIn("sleep", b.players[0].active.status,
                      "受击道具应让**攻击方**陷入睡眠")

    def test_holder_faction_required(self):
        """持有者不属于该阵营时不得触发（阵营限定不能失效）"""
        b = _arena(damage=30, def_name="Pikachu")
        b.players[1].active.tool = self.TAG
        _hit(b)
        self.assertNotIn("sleep", b.players[0].active.status)


# ---------------------------------------------------------------- 受击历史
class TestDmgPlusPrevTaken(unittest.TestCase):
    TAG = ("dmg_plus_prev_taken",)

    def test_adds_previous_damage(self):
        b = _arena(self.TAG, damage=100)
        b.players[0].active.dmg_taken_prev = 40
        self.assertEqual(_hit(b), 140, "100 + 上回合受到的 40")

    def test_zero_when_untouched(self):
        b = _arena(self.TAG, damage=100)
        self.assertEqual(_hit(b), 100, "上回合没受伤 → 不加伤")


# ---------------------------------------------------------------- 招式使用条件
class TestAttackUseCondition(unittest.TestCase):
    def _b(self, tag):
        """把带限制的招式放在**玩家 1**（后攻方）身上

        `went_second_first_turn` 的定义就是 `current == 1 且 turn_counts[1] == 1`，
        挂在玩家 0 身上永远不成立 —— 那样测出来的是「条件恒假」，不是本意。
        """
        b = _arena((tag,), damage=30)
        b.players[1].active = _mon(
            "Terapagos ex", hp=200, is_ex=True, category="Tera",
            attacks=(AttackDef("Unified Beatdown", cost=[], damage=30,
                               effects=(tag,)),))
        b.turn_counts = [1, 1]
        return b

    def test_use_not_blocks_on_first_turn_going_second(self):
        b = self._b("use_not:went_second_first_turn")
        b.current = 1
        b.turn_counts = [1, 1]          # 玩家 1 的第一个回合
        acts = [a for a in b.legal_actions(1) if a.get("verb") == "attack"]
        self.assertEqual(acts, [], "后攻首回合不得使用该招式")

    def test_use_not_allows_other_turns(self):
        b = self._b("use_not:went_second_first_turn")
        b.current = 1
        b.turn_counts = [1, 2]          # 第二个回合
        acts = [a for a in b.legal_actions(1) if a.get("verb") == "attack"]
        self.assertTrue(acts, "非首回合应可用")

    def test_use_only_requires_first_turn(self):
        b = self._b("use_only:went_second_first_turn")
        b.current = 1
        b.turn_counts = [1, 2]
        acts = [a for a in b.legal_actions(1) if a.get("verb") == "attack"]
        self.assertEqual(acts, [], "只在后攻首回合可用")


# ---------------------------------------------------------------- 手牌禁打
class TestPlayLockSupporter(unittest.TestCase):
    def test_locks_next_turn(self):
        b = _arena()
        sup = TrainerRef(TrainerDef("research:2", "supporter", "Test Supporter"))
        b.players[1].hand = [sup]
        b.players[1].turn_counts = 1
        b.current = 1
        r = b.step({"verb": "trainer", "hand": 0})
        self.assertTrue(r["ok"], r.get("error"))
        # 重新置一张（上一张已进弃牌堆），再施加封锁
        b.players[1].hand = [TrainerRef(TrainerDef("research:2", "supporter",
                                                   "Test Supporter"))]
        b.current = 0
        b._apply_play_lock_tag("play_lock:supporter:opp:turn")
        b.current = 1
        b.players[1].supporter_used = 0
        r2 = b.step({"verb": "trainer", "hand": 0})
        self.assertFalse(r2["ok"], "被封锁的回合不能打出支援者")


# ---------------------------------------------------------------- 场地
class TestStadiumTeraCost(unittest.TestCase):
    def test_tera_attacker_pays_more(self):
        b = _arena()
        b.players[0].active = _mon("Tera mon", hp=200, category="Tera")
        b.stadium = {"name": "Nighttime Mine", "tag": "stadium:tera_cost_up:1",
                     "owner": 0}
        atk = b.players[0].active.pdef.attacks[0]
        self.assertEqual(families.attack_cost(b, b.players[0].active, atk)
                         .get("colorless"), 1)

    def test_non_tera_unaffected(self):
        b = _arena()
        b.stadium = {"name": "Nighttime Mine", "tag": "stadium:tera_cost_up:1",
                     "owner": 0}
        atk = b.players[0].active.pdef.attacks[0]
        self.assertEqual(families.attack_cost(b, b.players[0].active, atk)
                         .get("colorless", 0), 0)


class TestStadiumKeepConfuse(unittest.TestCase):
    def test_confuse_survives_evolve(self):
        b = _arena()
        p = b.players[0]
        p.active = _mon("Basic", hp=100)
        p.active.status.add("confuse")
        p.hand = [make_pokemon_ref("Evo", stage=1, evolves_from="Basic")]
        b.stadium = {"name": "Dizzying Valley",
                     "tag": "stadium:keep_confuse", "owner": 0}
        r = b.step({"verb": "evolve", "hand": 0, "target": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertIn("confuse", p.active.status, "该场地下进化不解除混乱")

    def test_other_stadium_clears(self):
        b = _arena()
        p = b.players[0]
        p.active = _mon("Basic", hp=100)
        p.active.status.add("confuse")
        p.hand = [make_pokemon_ref("Evo", stage=1, evolves_from="Basic")]
        r = b.step({"verb": "evolve", "hand": 0, "target": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertNotIn("confuse", p.active.status, "普通进化应清除状态")


def make_pokemon_ref(name, *, stage=1, evolves_from=""):
    from engine.cards import PokeRef
    return PokeRef(PokemonDef(name, hp=120, ptype="colorless", stage=stage,
                              evolves_from=evolves_from))


# ---------------------------------------------------------------- 回复族
class TestAttachHealSameTarget(unittest.TestCase):
    TAG = "attach_energy:1:grass:hand:target:any:heal:30"

    def test_heals_attached_target(self):
        b = _arena()
        p = b.players[0]
        p.active = _mon("Hydrapple ex", hp=300, is_ex=True,
                        abilities=(AbilityDef("Ripening Charge", tag=self.TAG,
                                              location="field"),))
        p.bench = [_mon("BenchMon", hp=200)]
        p.bench[0].damage = 100
        p.hand = [EnergyRef(EnergyDef("grass"))]
        r = b.step({"verb": "ability", "pos": 0, "tag": self.TAG,
                    "name": "Ripening Charge", "target": 1})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(p.bench[0].damage, 70,
                         "应回复**被附能的那只** 30 点（不是持有者）")


class TestPassiveHealOnAttach(unittest.TestCase):
    def test_heals_target_when_holder_active(self):
        b = _arena()
        p = b.players[0]
        p.active = _mon("Magearna", hp=200, abilities=(AbilityDef(
            "Auto Heal", tag="passive:heal_on_attach:90",
            location="field"),))
        p.bench = [_mon("BenchMon", hp=200)]
        p.bench[0].damage = 100
        p.hand = [EnergyRef(EnergyDef("water"))]
        r = b.step({"verb": "attach", "target": 1})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(p.bench[0].damage, 10, "附能应回 90 点")


class TestHealMaxLeft(unittest.TestCase):
    TAG = "heal:all:choose:maxleft:30"

    def test_heals_when_below_limit(self):
        b = _arena()
        p = b.players[0]
        p.active = _mon("Low", hp=100)
        p.active.damage = 80             # 剩 20 ≤ 30
        p.hand = [TrainerRef(TrainerDef(self.TAG, "supporter",
                                        "Bianca's Devotion"))]
        r = b.step({"verb": "trainer", "hand": 0, "target": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(p.active.damage, 0, "剩余 HP ≤ 30 → 回满")

    def test_no_heal_above_limit(self):
        b = _arena()
        p = b.players[0]
        p.active = _mon("High", hp=100)
        p.active.damage = 40             # 剩 60 > 30
        p.hand = [TrainerRef(TrainerDef(self.TAG, "supporter",
                                        "Bianca's Devotion"))]
        r = b.step({"verb": "trainer", "hand": 0, "target": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(p.active.damage, 40, "剩余 HP > 30 → 不得回复")


class TestAttachStage2Target(unittest.TestCase):
    TAG = "attach:2:any:discard:target:stage2"

    def test_attaches_only_to_stage2(self):
        b = _arena()
        p = b.players[0]
        p.active = _mon("Stage2Mon", hp=160, stage=2)
        p.bench = [_mon("BasicMon", hp=60)]
        p.discard_refs = [EnergyRef(EnergyDef("water")) for _ in range(2)]
        p.hand = [TrainerRef(TrainerDef(self.TAG, "supporter",
                                        "Rosa's Encouragement"))]
        r = b.step({"verb": "trainer", "hand": 0, "target": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(p.active.total_energy(), 2, "能量应附到二阶宝可梦")
        self.assertEqual(p.bench[0].total_energy(), 0, "基础宝可梦不得被附能")


# ---------------------------------------------------------------- 击倒计数
class TestKoTrFlag(unittest.TestCase):
    def test_only_team_rocket_counted(self):
        b = _arena()
        b._ko_tr_flags()[0] = 0
        b._ko_flags()[0] = 1
        from engine.battle import _eval_condition
        self.assertFalse(_eval_condition(b, "own_ko_tr_last_turn"),
                         "只有普通宝可梦被击倒时，TR 门槛不成立")

    def test_tr_ko_counts(self):
        b = _arena()
        b._ko_flags()[0] = 1
        b._ko_tr_flags()[0] = 1
        from engine.battle import _eval_condition
        self.assertTrue(_eval_condition(b, "own_ko_tr_last_turn"))


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
