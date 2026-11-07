# -*- coding: utf-8 -*-
"""第一百三十四批（2026-11-08）· 引擎用例：八类整句规则

每条都配「成立 / 不成立」两向对照（负向不是摆设：把实现改回旧行为时必须变红）：

  · ``info:reveal_hand``        完美信息等价 —— **状态零变化**（只记日志）
  · ``counters_double:opp``     按**指示物**翻倍（不是 `damage*2` 的近似）
  · ``atk_energy_block:2``      能量 ≤ 2 的宝可梦不能出招；3 个能量照常出招
  · ``win_if:own_prizes_eq:1``  恰好剩 1 张奖赏 → 立刻结束并判胜；否则**什么都不做**
  · ``recon_bottom:7``          从**牌库底**取 1 张宝可梦入手（顶部不动）
  · ``discard_tool_senergy:1``  同一只身上的道具 + 特殊能量一起弃；不同时具备则不做
  · ``passive:hp_plus:N:if:special_energy``  条件式 +HP（拿去特殊能量要**回落**）
  · ``tool:hp_plus:N:holder:name:<k>``       阵营限定 +HP（不匹配的持有者不加）

运行：cd packages/engine && python -m unittest tests.test_batch134_engine -v
"""
from __future__ import annotations

import random
import unittest

from engine.battle import Battle
from engine.cards import (AbilityDef, AttackDef, EnergyDef, EnergyRef, PokeRef,
                          PokemonDef, TrainerDef, TrainerRef, make_pokemon)
from engine.state import GameConfig, PlayerState


def _mon(name="Mon", *, hp=300, ptype="colorless", stage=0, abilities=(),
         attacks=None):
    return make_pokemon(PokemonDef(
        name, hp=hp, ptype=ptype, stage=stage, abilities=tuple(abilities),
        attacks=tuple(attacks or (AttackDef("Pass", cost=(), damage=0),))))


def _arena(*, atk_abilities=(), atk_attacks=None, atk_energy=None,
           atk_name="Attacker", opp_active=None):
    cfg = GameConfig()
    b = object.__new__(Battle)
    b.cfg = cfg
    b.seed = 7
    b.rng = random.Random(7)
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
        atk_name, hp=500, abilities=tuple(atk_abilities),
        attacks=atk_attacks or (AttackDef("Test", cost=(), damage=10),))
    if atk_energy:
        for t, n in atk_energy.items():
            b.players[0].active.energy[t] = n
    b.players[1].active = opp_active or _mon("Defender", hp=400)
    for p in b.players:
        p.deck = [EnergyRef(EnergyDef("colorless")) for _ in range(20)]
    return b


class TestInfoReveal(unittest.TestCase):
    """`info:reveal_hand` = 完美信息等价 → 施加前后**状态完全一致**"""

    def _snap(self, b):
        def one(fp):
            return (fp.name, fp.damage, dict(fp.energy), sorted(fp.status),
                    len(fp.senergy))
        return ([one(f) for f in b.players[0].all_field()],
                [one(f) for f in b.players[1].all_field()],
                len(b.players[0].hand), len(b.players[1].hand),
                len(b.players[0].deck), len(b.players[1].deck),
                b.players[0].prizes_left, b.players[1].prizes_left)

    def test_no_state_change(self):
        b = _arena()
        b.players[1].active.damage = 30
        before = self._snap(b)
        b._apply_attack_effects(b.players[0].active, ("info:reveal_hand",))
        self.assertEqual(self._snap(b), before, "纯信息类不应改变任何状态")
        self.assertEqual(b.events[-1]["type"], "info",
                         "必须留下显式日志（未建模的卡连日志都没有）")


class TestCountersDouble(unittest.TestCase):
    def test_doubles_counters_on_both_active_and_bench(self):
        b = _arena()
        opp = b.players[1]
        opp.active.damage = 30                      # 3 枚 → 6 枚
        bench = _mon("Bench", hp=400)
        bench.damage = 20                           # 2 枚 → 4 枚
        opp.bench.append(bench)
        b.players[0].active.damage = 50             # 我方不动
        b._apply_attack_effects(b.players[0].active, ("counters_double:opp",))
        self.assertEqual(opp.active.damage, 60)
        self.assertEqual(bench.damage, 40)
        self.assertEqual(b.players[0].active.damage, 50, "只翻倍对手场上")

    def test_counter_aligned_not_double_total_damage(self):
        """非整十伤害只翻倍「指示物那部分」——直接 *2 会多算余数"""
        b = _arena()
        b.players[1].active.damage = 25             # 2 枚 + 余 5
        b._apply_attack_effects(b.players[0].active, ("counters_double:opp",))
        self.assertEqual(b.players[1].active.damage, 45, "2 枚→4 枚 = 40 + 余 5")


class TestAtkEnergyBlock(unittest.TestCase):
    def _legals(self, b):
        return b.legal_actions()

    def _b(self, opp_energy: int):
        b = _arena()
        for _ in range(opp_energy):
            b.players[1].active.energy["colorless"] = \
                b.players[1].active.energy.get("colorless", 0) + 1
        # 施加封锁（记在对手 player 上，窗口 = 对手的下个回合）
        b.players[1].atk_energy_block_n = 2
        b.players[1].atk_energy_block_until = b._lock_until(1)
        b.current = 1                                # 轮到被封锁方
        b.turn_counts[1] = 2                          # 进入窗口内
        return b

    def test_blocked_when_energy_at_most_n(self):
        b = self._b(opp_energy=2)
        have = [a for a in self._legals(b) if a.get("verb") == "attack"]
        self.assertEqual(have, [], "能量 ≤ 2 → 不得出现 attack 动作")

    def test_allowed_when_energy_above_n(self):
        b = self._b(opp_energy=3)
        have = [a for a in self._legals(b) if a.get("verb") == "attack"]
        self.assertTrue(have, "3 个能量 ≥ 3 → 可以出招")

    def test_expired_after_window(self):
        b = self._b(opp_energy=1)
        b.turn_counts[1] = 9                         # 窗口已过
        have = [a for a in self._legals(b) if a.get("verb") == "attack"]
        self.assertTrue(have, "到期的封锁不得继续生效")


class TestWinIfPrizes(unittest.TestCase):
    def test_wins_with_exactly_one_prize_left(self):
        b = _arena()
        b.players[0].prizes_left = 1
        b._apply_attack_effects(
            b.players[0].active, ("win_if:own_prizes_eq:1",))
        self.assertTrue(b.finished)
        self.assertEqual(b.winner, 0)
        self.assertEqual(b.win_reason, "victory_symbol")

    def test_does_nothing_at_two_prizes(self):
        b = _arena()
        b.players[0].prizes_left = 2
        b._apply_attack_effects(
            b.players[0].active, ("win_if:own_prizes_eq:1",))
        self.assertFalse(b.finished, "条件不成立时不得判胜")
        self.assertEqual(b.players[1].active.damage, 0)


class TestReconBottom(unittest.TestCase):
    def _b(self):
        b = _arena()
        p = b.players[0]
        mon = PokeRef(PokemonDef("BottomMon", hp=100, ptype="water"))
        # deck[0] 是**底**、deck[-1] 是**顶**：把宝可梦塞进底部 7 张里
        p.deck[2] = mon
        return b, mon

    def test_takes_from_bottom(self):
        b, mon = self._b()
        p = b.players[0]
        top_before = p.deck[-1]
        b._trainer_recon_bottom(p, "recon_bottom:7")
        self.assertIn(mon, p.hand, "应从牌库底 7 张里取到宝可梦")
        self.assertEqual(len(p.deck), 19, "只取 1 张，其余 19 张仍在牌库")
        self.assertIn(top_before, p.deck, "牌库顶的牌不该被取走（卡面只动底部）")

    def test_no_pokemon_in_bottom_does_nothing(self):
        b = _arena()
        p = b.players[0]
        p.deck = [EnergyRef(EnergyDef("colorless")) for _ in range(10)]
        top_before = p.deck[-1]
        b._trainer_recon_bottom(p, "recon_bottom:7")
        self.assertEqual(len(p.hand), 0)
        self.assertEqual(len(p.deck), 10)
        self.assertIs(p.deck[-1], top_before, "没取到牌时不得洗牌")


class TestDiscardToolSenergy(unittest.TestCase):
    def _b(self, *, tool=True, senergy=True):
        b = _arena()
        fp = _mon("Target", hp=300)
        if tool:
            fp.tool = "tool:hp_plus:30"
            fp.tool_name = "Fake Tool"
        if senergy:
            fp.senergy.append(EnergyRef(EnergyDef("colorless", name="Ignition Energy", tag="senergy:ignition")))
        b.players[1].active = fp
        return b, fp

    def test_discards_both_from_same_pokemon(self):
        b, fp = self._b()
        b._trainer_discard_tool_senergy(b.players[1])
        self.assertIsNone(fp.tool, "道具应被弃")
        self.assertEqual(fp.senergy, [], "特殊能量应被弃")

    def test_does_nothing_when_not_both_on_one(self):
        """只有道具 / 只有特殊能量 → 什么都不做（不得分跨两只各弃一张）"""
        for tool, se in ((True, False), (False, True), (False, False)):
            b, fp = self._b(tool=tool, senergy=se)
            before_tool = fp.tool
            before_se = list(fp.senergy)
            b._trainer_discard_tool_senergy(b.players[1])
            self.assertEqual(fp.tool, before_tool, (tool, se))
            self.assertEqual(fp.senergy, before_se, (tool, se))


class TestConditionalHp(unittest.TestCase):
    def test_special_energy_raises_max_hp_and_removal_drops_it(self):
        ab = AbilityDef("Tyrannically Gutsy",
                        tag="passive:hp_plus:150:if:special_energy",
                        location="field")
        fp = _mon("Tyrantrum", hp=200, abilities=(ab,))
        self.assertEqual(fp.max_hp, 200, "没有特殊能量 → 不加血")
        fp.senergy.append(EnergyRef(EnergyDef("colorless", name="Ignition Energy", tag="senergy:ignition")))
        self.assertEqual(fp.max_hp, 350, "有特殊能量 → +150")
        fp.senergy.clear()
        self.assertEqual(fp.max_hp, 200, "去掉特殊能量必须**回落**（不能固化）")

    def test_energy_typed_condition(self):
        ab = AbilityDef("Adrena-Power",
                        tag="passive:hp_plus:100:if:energy:darkness",
                        location="field")
        fp = _mon("Okidogi", hp=130, abilities=(ab,))
        self.assertEqual(fp.max_hp, 130)
        fp.energy["darkness"] = 1
        self.assertEqual(fp.max_hp, 230)
        fp.energy["darkness"] = 0
        self.assertEqual(fp.max_hp, 130, "能量被弃掉后上限回落")

    def test_unknown_condition_never_adds(self):
        """条件词不认识 → **不加血**（静默多给上限会让「被打到 0」变成还活着）"""
        ab = AbilityDef("Weird", tag="passive:hp_plus:100:if:some_new_cond",
                        location="field")
        fp = _mon("Weird", hp=130, abilities=(ab,))
        self.assertEqual(fp.max_hp, 130)


class TestToolHpHolder(unittest.TestCase):
    def test_holder_qualified(self):
        cynthia = _mon("Cynthia's Garchomp", hp=200)
        other = _mon("Pikachu", hp=100)
        for fp in (cynthia, other):
            fp.tool = "tool:hp_plus:70:holder:name:cynthias"
        self.assertEqual(cynthia.max_hp, 270, "Cynthia's 宝可梦应 +70")
        self.assertEqual(other.max_hp, 100, "非阵营持有者不得加血")

    def test_unqualified_still_works(self):
        fp = _mon("Any", hp=100)
        fp.tool = "tool:hp_plus:70"
        self.assertEqual(fp.max_hp, 170)


class TestToolDisabledIgnored(unittest.TestCase):
    """Jamming Tower 让道具失效时，限定/无条件的 +HP 都不得生效"""

    def test_disabled_tool_no_hp(self):
        fp = _mon("Cynthia's Garchomp", hp=200)
        fp.tool = "tool:hp_plus:70:holder:name:cynthias"
        fp.tool_disabled = True
        self.assertEqual(fp.max_hp, 200)


if __name__ == "__main__":
    unittest.main()
