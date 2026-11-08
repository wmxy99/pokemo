# -*- coding: utf-8 -*-
"""S6a · 官方规则对齐：开局回合 / 手牌上限 / setup 进化（2026-11-08）

为什么这些用例必须用**真实对局**（`Battle(...)` 而不是 `object.__new__` 摆场）：
「先手第一回合」是**对局阶段**，只有走过 setup（起手/铺场/发奖赏）的对局才存在
这个阶段。合成对局没有它 —— 所以规则的判据是 `setup_done and turn_number == 1`，
而不是 `turn_counts[pi] == 1`（后者会连**后手**玩家的第 2 回合一起锁住，
而后手第一回合官方允许攻击与打支援者）。

覆盖
----
1. 真实对局 `setup_done` 必为 True（`_setup_openings` 漏置位 = 整条规则静默失效）
2. 先手 T1：合法动作表**没有**攻击；直接下发会被拒（两处同挡）
3. 先手 T1：合法动作表**没有** Supporter；直接下发会被拒
4. 后手 T2：可以攻击、可以打 Supporter（守护 `turn_counts` 的旧 bug）
5. 先手 T2（第 3 回合）：恢复可攻击
6. 手牌**无上限**（官方）：抽到 30+ 张不丢牌；`max_hand>0` 时仍按历史口径丢
7. setup 上场的宝可梦 `just_played=True`；先手 T1 不能进化；后手 T2 可以进化
8. `first_turn_no_draw` 恒 False（官方每回合都抽；该字段只留作对拍）
9. 关掉开关 → 回到旧口径（对拍基线）

运行：cd packages/engine && python -m unittest tests.test_s6a_rules -v
"""
from __future__ import annotations

import unittest

from engine.battle import Battle
from engine.cards import (AttackDef, PokemonDef, PokeRef, TrainerDef,
                          TrainerRef, make_pokemon)
from engine.state import GameConfig

JAB = AttackDef("Jab", cost=0, damage=10)
STARTER = PokemonDef("Starter", hp=120, ptype="colorless", attacks=(JAB,))
# 进化用：Stage1 的 evolves_from 指向 Starter
EVO = PokemonDef("StarterEvo", hp=180, ptype="colorless", stage=1,
                 evolves_from="Starter", attacks=(JAB,))
# 一个「无目标就能打」的支援者（抽 1 张）；tag 用既有实现
SUPPORTER = TrainerDef("draw:1", kind="supporter",
                       name="Test Supporter")


def deck(n_basic: int = 20, n_evo: int = 8, n_sup: int = 0) -> list:
    return ([PokeRef(STARTER) for _ in range(n_basic)]
            + [PokeRef(EVO) for _ in range(n_evo)]
            + [TrainerRef(SUPPORTER) for _ in range(n_sup)])


def real_battle(seed: int = 1, **cfg_kw) -> Battle:
    return Battle(deck(), deck(), seed=seed, cfg=GameConfig(**cfg_kw))


def _fill_energy(fp, t="colorless", n=1):
    fp.add_energy(t, n)


def _attacks(b: Battle) -> list:
    return [a for a in b.legal_actions() if a.get("verb") == "attack"]


def _trainer_acts(b: Battle) -> list:
    return [a for a in b.legal_actions() if a.get("verb") == "trainer"]


class TestOpeningTurnMarker(unittest.TestCase):
    def test_real_battle_marks_setup_done(self):
        """`Battle(...)` 必须置 `setup_done`（漏置位 = 整条先手规则静默失效）"""
        b = real_battle(3)
        self.assertTrue(getattr(b, "setup_done", False))
        self.assertEqual(b.turn_number, 1)

    def test_synthetic_arena_has_no_opening_turn(self):
        """合成对局（无 setup）不存在「开局回合」—— 规则不适用，语义明确"""
        b = object.__new__(Battle)
        b.cfg = GameConfig()
        self.assertFalse(b._opening_turn())


class TestFirstTurnNoAttack(unittest.TestCase):
    def test_legal_actions_have_no_attack(self):
        b = real_battle(5)
        p = b.players[b.current]
        _fill_energy(p.active, n=2)
        self.assertTrue(b._opening_turn())
        self.assertEqual(_attacks(b), [], "先手 T1 的合法动作表不得列出攻击")

    def test_dispatch_rejects_attack(self):
        b = real_battle(5)
        p = b.players[b.current]
        _fill_energy(p.active, n=2)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertFalse(r["ok"])
        self.assertIn("先手第一回合", r["error"])

    def test_second_player_may_attack_on_their_first_turn(self):
        """后手第一回合 = 全剧第 2 回合 → **可以**攻击

        旧实现用 `turn_counts[current] == 1` 判「先手第一回合」，旗标打开后
        会把后手玩家的第一个回合也锁住（多锁一整个回合的攻击）。
        """
        b = real_battle(5)
        self.assertEqual(b.turn_number, 1)
        b.step({"verb": "pass"})              # 结束先手回合
        self.assertEqual(b.turn_number, 2)
        p = b.players[b.current]
        _fill_energy(p.active, n=2)
        self.assertFalse(b._opening_turn(), "第 2 回合不是开局回合")
        self.assertTrue(_attacks(b), "后手 T1 必须能列出攻击")
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))

    def test_attack_returns_on_turn_three(self):
        b = real_battle(5)
        b.step({"verb": "pass"})
        b.step({"verb": "pass"})
        self.assertEqual(b.turn_number, 3)
        p = b.players[b.current]
        _fill_energy(p.active, n=2)
        self.assertTrue(_attacks(b))
        self.assertTrue(b.step({"verb": "attack", "attack": 0})["ok"])

    def test_flag_off_restores_old_behaviour(self):
        """关闭开关 → 回到「先手 T1 可攻击」的对拍基线"""
        b = real_battle(5, first_turn_no_attack=False)
        p = b.players[b.current]
        _fill_energy(p.active, n=2)
        self.assertTrue(b._opening_turn())      # 仍在开局回合
        self.assertTrue(_attacks(b))            # 但规则被关掉
        self.assertTrue(b.step({"verb": "attack", "attack": 0})["ok"])


class TestFirstTurnNoSupporter(unittest.TestCase):
    def _with_supporter(self, b: Battle):
        p = b.players[b.current]
        p.hand.append(TrainerRef(SUPPORTER))
        return len(p.hand) - 1

    def test_legal_actions_hide_supporter(self):
        b = real_battle(5)
        hi = self._with_supporter(b)
        tags = [a.get("tag") for a in _trainer_acts(b)]
        self.assertNotIn("draw:1", tags, "先手 T1 的合法动作表不得列出 Supporter")
        # 直接下发也要被拒（两处同挡）
        r = b.step({"verb": "trainer", "hand": hi})
        self.assertFalse(r["ok"])
        self.assertIn("支援者", r["error"])

    def test_second_player_may_play_supporter(self):
        b = real_battle(5)
        b.step({"verb": "pass"})
        self.assertEqual(b.turn_number, 2)
        hi = self._with_supporter(b)
        self.assertIn("draw:1", [a.get("tag") for a in _trainer_acts(b)])
        r = b.step({"verb": "trainer", "hand": hi})
        self.assertTrue(r["ok"], r.get("error"))

    def test_flag_off_allows_supporter_on_turn_one(self):
        b = real_battle(5, first_turn_no_supporter=False)
        hi = self._with_supporter(b)
        r = b.step({"verb": "trainer", "hand": hi})
        self.assertTrue(r["ok"], r.get("error"))


class TestNoHandLimit(unittest.TestCase):
    def test_no_limit_by_default(self):
        """官方：无手牌上限 → `max_hand=0` 抽到 40 张也不丢"""
        b = real_battle(5)
        p = b.players[b.current]
        p.hand.clear()
        p.deck = [PokeRef(STARTER) for _ in range(45)]
        b._safe_draw(b.current, 40)
        self.assertEqual(len(p.hand), 40)
        self.assertEqual(p.discard_refs, [])

    def test_legacy_cap_still_works_when_configured(self):
        """`max_hand>0` 保留历史防御口径（对拍/压力测试用）"""
        b = real_battle(5, max_hand=5)
        p = b.players[b.current]
        p.hand.clear()
        p.deck = [PokeRef(STARTER) for _ in range(45)]
        b._safe_draw(b.current, 10)
        self.assertEqual(len(p.hand), 5)
        self.assertEqual(len(p.discard_refs), 5)

    def test_first_turn_no_draw_is_false(self):
        """`first_turn_no_draw` 是**伪需求**（官方每回合都抽）→ 必须恒 False"""
        self.assertFalse(GameConfig().first_turn_no_draw)
        b = real_battle(5)
        # 开局回合确实抽了 1 张（抽牌事件的 player 就是开局方）
        deals = [e for e in b.events
                 if e.get("type") == "draw" and e.get("player") == b.current]
        self.assertTrue(deals, "第 1 回合必须有抽牌事件")
        self.assertGreaterEqual(sum(int(e.get("n") or 0) for e in deals), 1)


class TestSetupPokemonCannotEvolve(unittest.TestCase):
    def test_setup_pokemon_marked_just_played(self):
        b = real_battle(5)
        for pi in (0, 1):
            for fp in b.players[pi].all_field():
                self.assertTrue(fp.just_played,
                                f"P{pi} 的 setup 宝可梦应带 just_played")

    def test_first_player_cannot_evolve_on_turn_one(self):
        b = real_battle(5)
        p = b.players[b.current]
        # 手里放一张能进化 active 的卡（setup 之后手牌里仍可能有 Stage1）
        p.hand = [TrainerRef(SUPPORTER)] + [c for c in p.hand
                                            if isinstance(c, PokeRef)]
        if not any(isinstance(c, PokeRef) and c.pdef.stage == 1
                   for c in p.hand):
            p.hand.append(PokeRef(EVO))
        self.assertFalse([a for a in b.legal_actions()
                          if a.get("verb") == "evolve"],
                         "先手 T1 不得列出「进化 setup 宝可梦」的动作")

    def test_second_player_can_evolve_on_their_first_turn(self):
        """后手 T2：其 setup 宝可梦的 `just_played` 已在 `_begin_turn` 清零"""
        b = real_battle(5)
        b.step({"verb": "pass"})
        p = b.players[b.current]
        self.assertFalse(p.active.just_played)
        p.hand = [c for c in p.hand if not isinstance(c, TrainerRef)]
        p.hand.append(PokeRef(EVO))
        evo = [a for a in b.legal_actions() if a.get("verb") == "evolve"]
        self.assertTrue(evo, "后手 T1 应当能进化 setup 宝可梦")
        r = b.step(evo[0])
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(p.active.pdef.name, "StarterEvo")


class TestCommandCatalogDisambiguation(unittest.TestCase):
    """指令清单的往返不变量（S6a 暴露的既有缺口）

    先手禁攻之后，清单第 1 条变成「进化：A → B」——两只**同名**宝可梦都能进化时
    两条动作的 label 完全相同，用户点第一条会被 422（歧义）。`command_label` 的
    文档声称「label 原样回投必须可还原」，所以这是真缺口而不是测试问题。
    """

    LEGAL = [
        {"verb": "evolve", "hand": 2, "target": 0, "name": "Drakloak",
         "onto": "Dreepy"},
        {"verb": "evolve", "hand": 2, "target": 1, "name": "Drakloak",
         "onto": "Dreepy"},
        {"verb": "retreat", "bench": 0},
        {"verb": "pass"},
    ]

    def test_duplicate_labels_get_slot_suffix(self):
        from engine.commands import command_catalog
        labels = [r["label"] for r in command_catalog(self.LEGAL)]
        self.assertEqual(len(set(labels)), len(labels), labels)
        self.assertIn("（战斗位）", labels[0])
        self.assertIn("（后备1）", labels[1])

    def test_each_label_round_trips(self):
        from engine.commands import command_catalog, find_action
        for row in command_catalog(self.LEGAL):
            res = find_action(self.LEGAL, row["label"])
            self.assertTrue(res["ok"], f"{row['label']} 无法回投：{res}")
            self.assertEqual(res["matched"], row["label"])

    def test_duplicate_copies_of_same_card_stay_clean(self):
        """同名的多张手牌副本可互换 → 不加后缀（否则文案变噪声）"""
        from engine.commands import command_catalog
        legal = [{"verb": "trainer", "hand": 0, "name": "Iono",
                  "tag": "shuffle_draw:5"},
                 {"verb": "trainer", "hand": 4, "name": "Iono",
                  "tag": "shuffle_draw:5"}]
        labels = [r["label"] for r in command_catalog(legal)]
        self.assertEqual(labels[0], labels[1])
        self.assertNotIn("（", labels[0])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


# ---------------- 先手首回合的**例外许可**（use_any:first_turn） ----------------
BYPASS_ATK = AttackDef("Quick Sign", cost=0, damage=0,
                       effects=("search_bench:2:basic", "use_any:first_turn"))


class TestFirstTurnBypass(unittest.TestCase):
    """「If you go first, you can use this attack / may use this card …」"""

    def _prep(self, *attacks):
        b = real_battle(5)
        p = b.players[b.current]
        p.active = make_pokemon(PokemonDef("Bypasser", hp=100,
                                          ptype="colorless",
                                          attacks=tuple(attacks)))
        return b

    def test_bypass_attack_is_legal_on_opening_turn(self):
        b = self._prep(BYPASS_ATK)
        p = b.players[b.current]
        _fill_energy(p.active, n=2)
        acts = _attacks(b)
        self.assertEqual(len(acts), 1, acts)
        r = b.step(acts[0])
        self.assertTrue(r["ok"], r.get("error"))

    def test_non_bypass_attack_still_blocked_when_mixed(self):
        """同一只身上有例外招与普通招 → 只列例外那一个（按**招式**放行）"""
        b = self._prep(BYPASS_ATK, JAB)
        p = b.players[b.current]
        _fill_energy(p.active, n=2)
        names = [a.get("name") for a in _attacks(b)]
        self.assertEqual(names, ["Quick Sign"])
        r = b.step({"verb": "attack", "attack": 1})   # 普通招
        self.assertFalse(r["ok"])
        self.assertIn("先手第一回合", r["error"])

    def test_bypass_supporter_is_playable(self):
        """卡级例外：复合 tag `draw:1|use_any:first_turn` 在开场回合可打"""
        b = real_battle(5)
        p = b.players[b.current]
        card = TrainerRef(TrainerDef("draw:1|use_any:first_turn",
                                     kind="supporter", name="Carmine"))
        p.hand.append(card)
        hi = len(p.hand) - 1
        self.assertIn("draw:1|use_any:first_turn",
                      [a.get("tag") for a in _trainer_acts(b)])
        r = b.step({"verb": "trainer", "hand": hi})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertNotIn(card, p.hand)          # 已使用并进弃牌堆

    def test_plain_supporter_still_blocked(self):
        b = real_battle(5)
        p = b.players[b.current]
        p.hand.append(TrainerRef(SUPPORTER))
        r = b.step({"verb": "trainer", "hand": len(p.hand) - 1})
        self.assertFalse(r["ok"])
        self.assertIn("支援者", r["error"])
