# -*- coding: utf-8 -*-
"""PokéLab AI · K2 卡牌效果解析用例

覆盖：规则后端句式 · 触发时机 vs 动作 · 括号注解剔除 · Schema 校验 ·
引擎 tag 映射 · LLM 输出稳健提取 · 黄金集评测（含 seen/holdout 分组）·
黄金集与卡池 desc_zh 一致性不变量。

运行：cd packages/agent && python -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG = HERE.parent
for p in (str(PKG), str(PKG.parent / "engine")):
    if p not in sys.path:
        sys.path.insert(0, p)

from agent import effects as E  # noqa: E402


def _types(spec):
    return [a["type"] for a in spec["actions"]]


def _pairs(spec):
    return [(a["type"], a["amount"], a["target"]) for a in spec["actions"]]


class TestRuleParser(unittest.TestCase):
    """规则后端（离线基线）覆盖的中文句式"""

    def test_plain_damage(self):
        s = E.parse_rule("造成 120 点伤害。", kind="attack", name="X")
        self.assertEqual(s["trigger"], "on_use")
        self.assertEqual(_pairs(s), [("damage", 120, "active_opponent")])
        self.assertEqual(s["conditions"], [])

    def test_damage_with_second_clause(self):
        s = E.parse_rule("造成 50 点伤害。对手将战斗宝可梦与后备宝可梦互换。",
                         kind="attack", name="X")
        self.assertIn(("switch", None, "active_opponent_bench"), _pairs(s))
        self.assertIn(("damage", 50, "active_opponent"), _pairs(s))

    def test_spread_counters_to_hp(self):
        """1 个伤害指示物 = 10 点；任意分配 → free_distribute"""
        s = E.parse_rule("造成 200 点伤害；随后在对手后备宝可梦上以任意方式"
                         "放置 6 个伤害指示物（合计 60 点）。",
                         kind="attack", name="X")
        spread = [a for a in s["actions"] if a["type"] == "spread"][0]
        self.assertEqual(spread["amount"], 60)
        self.assertEqual(spread["target"], "active_opponent_bench")
        self.assertTrue(spread["extra"]["free_distribute"])
        self.assertEqual(spread["extra"]["counters"], 6)

    def test_parenthetical_notes_ignored(self):
        """「（引擎待实现）」「（合计 60 点）」是注解，不是卡面效果"""
        s = E.parse_rule("每回合一次：抽至手牌 5 张（引擎近似检索）",
                         kind="ability", name="X")
        self.assertEqual(_types(s), ["draw"])
        self.assertNotIn("search", _types(s), "括号里的「检索」不应触发 search")

    def test_draw_to_hand_size(self):
        s = E.parse_rule("每回合一次：抽至手牌 5 张", kind="ability", name="X")
        d = s["actions"][0]
        # 2026-09-16 schema 澄清：`target` 只表「动作的直接宾语」——
        # 抽牌的宾语是「牌」，来源固定为牌库，故 target 统一为 `deck`；
        # 「抽至手牌」这个**去向**由 `extra.to_hand_size` 承载，
        # 不再让 target 兼表方向（原先一处 hand、一处 deck 的不一致正是这么来的）。
        self.assertEqual((d["type"], d["amount"], d["target"]),
                         ("draw", None, "deck"))
        self.assertEqual(d["extra"]["to_hand_size"], 5)

    def test_status_target_self_vs_opponent(self):
        opp = E.parse_rule("造成 60 点伤害。对手的战斗宝可梦陷入睡眠。",
                           kind="attack", name="X")
        me = E.parse_rule("造成 30 点伤害。此宝可梦随后陷入混乱。",
                          kind="attack", name="X")
        self.assertIn(("status", None, "active_opponent"), _pairs(opp))
        self.assertIn(("status", None, "self"), _pairs(me))
        self.assertEqual([a["extra"]["status"] for a in me["actions"]
                          if a["type"] == "status"], ["confuse"])

    def test_search_from_deck_and_discard(self):
        deck = E.parse_rule("从牌库检索 1 张宝可梦。", kind="item", name="X")
        disc = E.parse_rule("从弃牌堆取 1 张宝可梦或基础能量放回手牌。",
                            kind="item", name="X")
        self.assertEqual(_pairs(deck), [("search", 1, "deck")])
        self.assertEqual(_pairs(disc), [("search", 1, "discard")])

    def test_switch_self_vs_opponent(self):
        mine = E.parse_rule("将自己的战斗宝可梦与后备宝可梦互换。",
                            kind="item", name="X")
        theirs = E.parse_rule("对手将战斗宝可梦与后备宝可梦互换，由你选择。",
                              kind="supporter", name="X")
        self.assertEqual(_pairs(mine), [("switch", None, "self")])
        self.assertEqual(_pairs(theirs), [("switch", None, "active_opponent_bench")])

    def test_discard_all_hand_and_energy(self):
        all_hand = E.parse_rule("弃掉手牌，然后抽 7 张。", kind="supporter", name="X")
        energy = E.parse_rule("抛硬币，正面则弃掉对手战斗宝可梦身上 1 个能量。",
                              kind="item", name="X")
        self.assertIn(("discard", None, "hand"), _pairs(all_hand))
        self.assertIn(("draw", 7, "deck"), _pairs(all_hand))
        self.assertEqual(_pairs(energy), [("discard", 1, "active_opponent")])

    def test_attach_variants(self):
        provide = E.parse_rule("提供 1 点超能量。", kind="energy", name="X")
        attach = E.parse_rule("每回合一次：从手牌/牌库附 1 草能量（引擎待实现）",
                              kind="ability", name="X")
        accel = E.parse_rule("进化时从弃牌堆加速能量（引擎待实现）",
                             kind="ability", name="X")
        self.assertEqual(_pairs(provide), [("attach", 1, "self")])
        self.assertEqual([a["extra"]["energy_type"] for a in attach["actions"]
                          if a["type"] == "attach"], ["grass"])
        self.assertEqual(_pairs(accel), [("attach", None, "self")])

    def test_evolve_action_requires_active_voice(self):
        """「进化时」是触发时机，不得产出 evolve 动作；施动语境才产出"""
        trig = E.parse_rule("进化时从弃牌堆加速能量", kind="ability", name="X")
        self.assertNotIn("evolve", _types(trig))
        self.assertEqual(trig["trigger"], "on_evolve")
        action = E.parse_rule("选择手牌 1 张 2 阶进化卡，直接进化自己的基础宝可梦。",
                              kind="item", name="X")
        self.assertIn("evolve", _types(action))

    def test_knockout_action_requires_active_voice(self):
        """「被击倒」是触发时机，不得产出 knockout 动作"""
        s = E.parse_rule("己方宝可梦被击倒时抽 3（引擎待实现）",
                         kind="ability", name="X")
        self.assertEqual(s["trigger"], "on_knockout")
        self.assertNotIn("knockout", _types(s))
        self.assertIn("draw", _types(s))

    def test_static_rule_modifier(self):
        s = E.parse_rule("对手龙系宝可梦弱点变为超", kind="ability", name="X")
        self.assertEqual(s["trigger"], "static")
        self.assertEqual(_types(s), ["rule"])

    def test_once_per_turn_limit(self):
        for text in ("每回合一次：检视牌库顶 2 张取 1 入手",
                     "每回合只能附着 1 次能量。"):
            s = E.parse_rule(text, kind="ability", name="X")
            self.assertEqual(s["conditions"],
                             [{"field": "limit", "op": "eq",
                               "value": "once_per_turn"}], text)

    def test_look_top_search(self):
        s = E.parse_rule("每回合一次：检视牌库顶 2 张取 1 入手",
                         kind="ability", name="X")
        a = s["actions"][0]
        self.assertEqual((a["type"], a["amount"], a["target"]),
                         ("search", 1, "deck"))
        self.assertEqual(a["extra"]["look_top"], 2)

    def test_range_and_bounds(self):
        self.assertEqual(E.parse_rule("", kind="attack").get("actions"), [])
        s = E.parse_rule("造成 9999 点伤害。", kind="attack")
        self.assertEqual(s["actions"][0]["amount"], 9999)


class TestValidateAndMap(unittest.TestCase):
    def test_validate_catches_bad_enums_and_amount(self):
        bad = [E.spec("attack", "X", "on_use",
                      [E.act("teleport", 10, "active_opponent")])]
        errs = E.validate_specs(bad)
        self.assertTrue(any("action.type 非法" in e for e in errs))
        bad2 = [E.spec("attack", "X", "on_use",
                       [E.act("damage", "10", "active_opponent")])]
        self.assertTrue(any("应为整数" in e for e in E.validate_specs(bad2)))
        bad3 = [{"kind": "attack", "name": "X", "trigger": "whenever",
                 "conditions": [], "actions": [], "source_text": ""}]
        self.assertTrue(any("trigger 非法" in e for e in E.validate_specs(bad3)))

    def test_valid_spec_has_no_errors(self):
        good = [E.spec("attack", "幻影俯冲", "on_use",
                       [E.act("damage", 200, "active_opponent"),
                        E.act("spread", 60, "active_opponent_bench",
                              {"free_distribute": True})],
                       source_text="…")]
        self.assertEqual(E.validate_specs(good), [])

    def test_to_engine_tags_supported(self):
        specs = [E.spec("attack", "X", "on_use",
                        [E.act("damage", 60, "active_opponent"),
                         E.act("status", None, "active_opponent",
                               {"status": "sleep"}),
                         E.act("spread", 60, "active_opponent_bench",
                               {"free_distribute": True})])]
        r = E.to_engine_tags(specs)
        self.assertIn("sleep", r["tags"])
        self.assertIn("spread_free:60", r["tags"])
        self.assertEqual(r["unsupported"], [], "damage 不需 tag，不应报未支持")

    def test_to_engine_tags_unsupported_is_honest(self):
        """仍未建模的动作必须如实列入 unsupported，不假装支持

        v1.3 起 ``move_damage`` / ``draw``（特性）已建模为引擎 tag，故本用例改用
        **确实尚未建模**的动作（击倒效果 · 真静态规则）守护同一不变量。
        """
        specs = [E.spec("attack", "X", "on_use",
                        [E.act("knockout", None, "active_opponent"),
                         E.act("rule", None, "any",
                               {"note": "静态规则修改（弱点/抗性/限制）"})])]
        r = E.to_engine_tags(specs)
        kinds = {u["type"] for u in r["unsupported"]}
        self.assertIn("knockout", kinds)
        self.assertIn("rule", kinds)
        self.assertTrue(all(u.get("reason") for u in r["unsupported"]),
                        "每条未建模都必须给出理由")
        self.assertEqual(r["tags"], [], "未建模动作不得产出任何 tag")

    def test_new_tag_families_are_mapped(self):
        """v1.3 招式侧 tag 家族：discard / switch / attach / move_damage / spread:N:per"""
        specs = [E.spec("attack", "X", "on_use", [
            E.act("damage", 60, "active_opponent"),
            E.act("discard", 2, "hand"),
            E.act("discard", 1, "self", {"card_type": "energy"}),
            E.act("switch", None, "self"),
            E.act("switch", None, "active_opponent_bench"),
            E.act("attach", 1, "self", {"energy_type": "fire"}),
            E.act("move_damage", 3, "active_opponent"),
            E.act("spread", 40, "active_opponent_bench", {"counters": 4}),
            E.act("draw", 2, "deck"),
        ])]
        r = E.to_engine_tags(specs)
        for want in ("discard:2", "discard_energy:1", "switch_self", "gust",
                     "attach:1:fire", "move_damage:3", "spread:4:10", "draw:2"):
            self.assertIn(want, r["tags"])
        self.assertEqual(r["unsupported"], [])

    def test_ability_tags_follow_trigger(self):
        """特性 tag 随触发时机分流：on_knockout→ko_draw · on_evolve→evolve_attach"""
        ko = E.to_engine_tags([E.spec(
            "ability", "Flip the Script", "on_knockout",
            [E.act("draw", 3, "deck")])])
        self.assertIn("ko_draw:3", ko["tags"])
        ev = E.to_engine_tags([E.spec(
            "ability", "Blaze Dance", "on_evolve",
            [E.act("attach", 2, "self",
                   {"energy_type": "fire", "zone": "discard"})])])
        self.assertIn("evolve_attach:2:fire:discard", ev["tags"])
        md = E.to_engine_tags([E.spec(
            "ability", "Adrena-Brain", "static",
            [E.act("move_damage", 3, "active_opponent")])])
        self.assertIn("move_damage:3", md["tags"])
        wc = E.to_engine_tags([E.spec(
            "ability", "Fairy Zone", "static",
            [E.act("rule", None, "any",
                   {"weak_change": "psychic", "filter": "dragon"})])])
        self.assertIn("weak_change:psychic:dragon", wc["tags"])

    def test_recover_tag(self):
        r = E.to_engine_tags([E.spec(
            "item", "Night Stretcher", "on_use",
            [E.act("recover", 1, "discard", {"kind": "any"})])])
        self.assertIn("recover:1:any", r["tags"])

    def test_ability_deck_search_is_not_recon(self):
        """特性侧「search your deck for …」**不是** `recon:N`

        引擎的 `recon:N` = 「检视牌库顶 N 张取 1」（且是**随机**取 1）——
        与「从整副牌库检索指定类别」语义不同。早期一律映射成 recon，
        实测池内 **16 条**特性因此把"想搜什么搜什么"变成"从牌堆顶碰运气"。
        """
        specs = [E.spec("ability", "侦察指令", "static",
                        [E.act("search", 2, "deck")],
                        [{"field": "limit", "op": "eq",
                          "value": "once_per_turn"}])]
        r = E.to_engine_tags(specs)
        self.assertIn("search_any:2", r["tags"])
        self.assertNotIn("recon:2", r["tags"])

    def test_ability_look_top_still_recon(self):
        """「look at the top N cards」才是 recon"""
        specs = [E.spec("ability", "侦察", "static",
                        [E.act("search", 1, "deck", {"look_top": 3})],
                        [{"field": "limit", "op": "eq",
                          "value": "once_per_turn"}])]
        r = E.to_engine_tags(specs)
        self.assertIn("recon:3", r["tags"])


class TestV14Modifiers(unittest.TestCase):
    """v1.4 修正器识别：抛币 / 伤害缩放 / 护盾 / 被动（B4 批次主体）

    真实卡池为**英文卡面且用弯撇号**（’）——这两个特性都曾让识别静默失配，
    故用例统一使用带弯撇号的英文原文。
    """

    @staticmethod
    def _tags(text, kind="attack", name="X"):
        return E.to_engine_tags([E.parse_rule(text, kind=kind, name=name)])

    def test_coin_heads_plus_damage(self):
        r = self._tags("Flip a coin. If heads, this attack does 20 more damage.")
        self.assertIn("coin:heads:dmg_plus:20", r["tags"])
        self.assertEqual(r["unsupported"], [])

    def test_coin_tails_zero(self):
        r = self._tags("Flip a coin. If tails, this attack does nothing.")
        self.assertIn("coin:tails:dmg_zero", r["tags"])

    def test_coin_heads_discard_energy(self):
        """弃对手战斗位能量 → `:opp` 变体，且**只能在正面分支**里

        两处修正（2026-09-21，均由审计真实卡池发现）：
          ① 卡面写「from your **opponent’s** Active Pokémon」，而引擎 `discard_energy:N`
             的语义是「弃自身」→ 曾把自家能量丢掉、对手毫发无损
             （Gastly / Larvitar / Accelgor / Croconaw 4 张卡受影响）→ 加 `:opp` 变体；
          ② 该动作属抛币分支，**不得**再产出无条件版本（从"正面才弃"变"必定弃"）。
        """
        r = self._tags("Flip a coin. If heads, discard an Energy from your "
                       "opponent\u2019s Active Pok\u00e9mon.")
        self.assertIn("coin:heads:discard_energy:1:opp", r["tags"])
        self.assertNotIn("discard_energy:1:opp", r["tags"],
                         "抛币分支内的动作不得同时产出无条件版本")

    def test_discard_target_from_clause_not_whole_text(self):
        """弃牌目标只看**子句**，不看全文（真实反例：Regice ex·Ice Prison）

        原文「Discard 2 Energy **from this Pokémon**, and your opponent's Active
        Pokémon is now Paralyzed.」——弃的是自身，但同一句里提到了对手。
        早期用全文的 opponent 检查判目标 → 判成"弃对手能量"，引擎去扣对手的能量
        （静默算错）。修法：从匹配处截到第一个句读，只看这一小段。
        """
        r = self._tags("Discard 2 Energy from this Pok\u00e9mon, and your "
                       "opponent\u2019s Active Pok\u00e9mon is now Paralyzed.")
        self.assertIn("discard_energy:2", r["tags"])
        self.assertNotIn("discard_energy:2:opp", r["tags"],
                         "子句说的是自身，不得被全文的 opponent 带偏")
        self.assertIn("paralyze", r["tags"])

    def test_discard_energy_self_vs_opponent(self):
        """冠词型弃能量：自身 / 对手必须落到不同 tag（引擎语义不同）"""
        own = self._tags("Discard an Energy from this Pok\u00e9mon.")
        self.assertIn("discard_energy:1", own["tags"])
        self.assertNotIn("discard_energy:1:opp", own["tags"])
        opp = self._tags("Discard a Special Energy from your opponent\u2019s "
                         "Active Pok\u00e9mon.")
        self.assertIn("discard_energy:1:opp", opp["tags"])

    def test_counter_side_is_not_reversed(self):
        """计数源阵营不得反转（your ↔ opponent's）

        实测：`for each of **your** Benched Pokémon` 曾命中笼统的 `benched pok`
        被映射成 **opp_bench** → 按对手后备数算伤害（Terapagos ex / Tapu Koko ex /
        Cinccino 三张卡受影响）。口径反转同理：`Prize card … has taken` 曾按
        `*_prizes_left`（剩余）映射，方向相反。
        """
        own = self._tags("This attack does 20 more damage for each of your "
                         "Benched Pok\u00e9mon.")
        self.assertIn("dmg_plus:20:per:own_bench", own["tags"])
        opp = self._tags("This attack does 20 more damage for each of your "
                         "opponent\u2019s Benched Pok\u00e9mon.")
        self.assertIn("dmg_plus:20:per:opp_bench", opp["tags"])
        taken = self._tags("This attack does 50 more damage for each Prize card "
                           "your opponent has taken.")
        self.assertIn("dmg_plus:50:per:opp_prizes_taken", taken["tags"],
                      "「已取得」不得映射成「剩余」")

    def test_subset_phrase_not_mapped_to_total(self):
        """子集措辞（具名/类别/颜色限定）不得按总数映射（否则系统性多算）"""
        for text in (
            "This attack does 30 more damage for each damage counter on all of "
            "your opponent\u2019s Pok\u00e9mon.",          # 部位：非"后备"也不是"active"
            "This attack does 30 more damage for each damage counter on all of "
            "your Benched Cynthia\u2019s Pok\u00e9mon.",   # 具名子集
            "This attack does 30 more damage for each damage counter on all of "
            "your Benched {F} Pok\u00e9mon.",              # 颜色子集
        ):
            r = self._tags(text)
            self.assertEqual(r["tags"], [],
                             f"子集措辞不得映射出 tag：{text[:50]}")
            self.assertTrue(r["unsupported"], "应如实登记为未建模")

    def test_unmappable_condition_not_emitted_as_flat_bonus(self):
        """条件无法表达时**不得**退化成无条件加伤（凭空加伤 = 静默算错）"""
        r = self._tags("If a Stadium is in play, this attack does 60 more "
                       "damage. Then, discard that Stadium.")
        self.assertEqual(r["tags"], [], "条件未知却产出了加伤 tag")
        self.assertTrue(r["unsupported"])

    def test_condition_mappable_to_engine_whitelist(self):
        """条件在引擎白名单内时才映射为 `dmg_plus:N:if:<cond>`"""
        r = self._tags("If your opponent has any Benched Pok\u00e9mon, this "
                       "attack does 30 more damage.")
        self.assertIn("dmg_plus:30:if:opp_has_bench", r["tags"])
        self.assertEqual(r["unsupported"], [])

    # ---- 第四批（2026-10-26）：高频条件 ----
    def test_fourth_batch_conditions(self):
        """第四批条件：能由当下局面直读的，映射到引擎条件"""
        cases = [
            ("If your opponent\u2019s Active Pok\u00e9mon is a Pok\u00e9mon {ex}, "
             "this attack does 30 more damage.", "dmg_plus:30:if:opp_active_is_ex"),
            ("If your opponent\u2019s Active Pok\u00e9mon is an Evolution "
             "Pok\u00e9mon, this attack does 60 more damage.",
             "dmg_plus:60:if:opp_active_is_evolution"),
            ("If your opponent\u2019s Active Pok\u00e9mon already has any damage "
             "counters on it, this attack does 120 more damage.",
             "dmg_plus:120:if:opp_active_has_damage"),
            ("If this Pok\u00e9mon has any damage counters on it, this attack "
             "does 40 more damage.", "dmg_plus:40:if:own_active_has_damage"),
            ("If your opponent\u2019s Active Pok\u00e9mon is Burned, this attack "
             "does 90 more damage.", "dmg_plus:90:if:opp_active_status:burn"),
            ("If your opponent\u2019s Active Pok\u00e9mon is a {P} Pok\u00e9mon, "
             "this attack does 50 more damage.",
             "dmg_plus:50:if:opp_active_type:psychic"),
            ("If your opponent has 3 or more Benched Pok\u00e9mon, this attack "
             "does 70 more damage.", "dmg_plus:70:if:opp_bench_at_least:3"),
            ("If you have 3 or more Energy in play, this attack does 90 more "
             "damage.", "dmg_plus:90:if:own_all_energy_at_least:3"),
        ]
        for text, tag in cases:
            with self.subTest(tag=tag):
                r = self._tags(text)
                self.assertIn(tag, r["tags"])
                self.assertEqual(r["unsupported"], [])

    def test_fourth_batch_keeps_refusing_unrepresentable(self):
        """⚠ 负向守卫：引擎表达不了的条件**必须继续拒绝**（宁可未建模，不可近似）"""
        texts = [
            # 场地：引擎无场地机制
            "If a Stadium is in play, this attack does 30 more damage.",
            # 「额外能量（费用之外）」：用总数会提前触发
            "If this Pok\u00e9mon has at least 2 extra Energy attached (in "
            "addition to this attack\u2019s cost), this attack does 90 more damage.",
            # {ex} 或 {V}：并集，引擎不区分 V
            "If your opponent\u2019s Active Pok\u00e9mon is a Pok\u00e9mon {ex} or "
            "Pok\u00e9mon {V}, this attack does 30 more damage.",
            # 道具附着：引擎无道具挂载模型
            "If this Pok\u00e9mon has a Pok\u00e9mon Tool attached, this attack "
            "does 40 more damage.",
            # 回合历史：本回合是否被治疗
            "If this Pok\u00e9mon was healed during this turn, this attack does "
            "50 more damage.",
            # 回合历史：本回合是否从后备换入
            "If this Pok\u00e9mon moved from your Bench to the Active Spot this "
            "turn, this attack does 60 more damage.",
        ]
        for text in texts:
            with self.subTest(text=text[:40]):
                r = self._tags(text)
                self.assertEqual(r["tags"], [], "条件不可表达却产出了 tag（静默加强）")
                self.assertTrue(r["unsupported"])

    def test_conditional_status_is_not_applied_as_effect(self):
        """⚠ 回归：条件句里的状态词**不得**被当成效果施加

        真实反例（本批同源缺陷）：「If your opponent's Active Pokémon is Burned,
        this attack does 90 more damage.」——早期只做 `word in text`，
        于是既产出条件加伤、又额外给对手挂上灼伤（条件当效果）。
        """
        r = self._tags("If your opponent\u2019s Active Pok\u00e9mon is Burned, "
                       "this attack does 90 more damage.")
        self.assertIn("dmg_plus:90:if:opp_active_status:burn", r["tags"])
        self.assertNotIn("burn", r["tags"], "条件词被当成效果施加了")

    def test_real_status_effect_still_applies(self):
        """对照：非条件句的「is now Burned」仍然要产出状态效果（不能修过头）"""
        r = self._tags("Your opponent\u2019s Active Pok\u00e9mon is now Burned.")
        self.assertIn("burn", r["tags"])

    def test_two_conditions_do_not_cross_contaminate(self):
        """同卡两个 if 从句时，条件只取「含 more damage 的那一句」"""
        r = self._tags("If your opponent\u2019s Active Pok\u00e9mon is a "
                       "Pok\u00e9mon {ex}, this attack does 30 more damage. "
                       "If this Pok\u00e9mon has any damage counters on it, "
                       "this attack does 40 more damage.")
        self.assertIn("dmg_plus:30:if:opp_active_is_ex", r["tags"])
        self.assertIn("dmg_plus:40:if:own_active_has_damage", r["tags"])

    def test_coin_heads_shield_not_unconditional(self):
        """护盾写在抛币分支内 → 只能有 coin:heads:…，不得再产出无条件 shield

        否则护盾从「正面才生效」变成「必定生效」，是实质性加强（口径错误）。
        """
        r = self._tags("Flip a coin. If heads, during your opponent\u2019s next "
                       "turn, prevent all damage from and effects of attacks "
                       "done to this Pok\u00e9mon.")
        self.assertIn("coin:heads:shield:prevent_all", r["tags"])
        self.assertNotIn("shield:prevent_all", r["tags"])

    def test_coin_each_tails(self):
        r = self._tags("Your opponent flips a coin for each of their Benched "
                       "Pok\u00e9mon. This attack does 80 damage to your "
                       "opponent\u2019s Active Pok\u00e9mon for each tails.")
        self.assertIn("coin_each:opp_bench:tails:dmg_plus:80", r["tags"])

    def test_scaling_opp_bench(self):
        r = self._tags("This attack does 20 more damage for each of your "
                       "opponent\u2019s Benched Pok\u00e9mon.")
        self.assertIn("dmg_plus:20:per:opp_bench", r["tags"])

    def test_scaling_opp_active_energy(self):
        r = self._tags("This attack does 30 more damage for each Energy "
                       "attached to your opponent\u2019s Active Pok\u00e9mon.")
        self.assertIn("dmg_plus:30:per:opp_active_energy", r["tags"])

    def test_scaling_unrecognized_counter_is_honest(self):
        """计数源无法识别 → 如实报 unsupported，不得猜一个计数源出来"""
        r = self._tags("This attack does 10 more damage for each Ancient card "
                       "in your discard pile.")
        self.assertEqual(r["tags"], [])
        self.assertTrue(any("缩放" in u["reason"] or "未识别" in u["reason"]
                            for u in r["unsupported"]), r["unsupported"])

    def test_shield_reduce(self):
        r = self._tags("During your opponent\u2019s next turn, this Pok\u00e9mon "
                       "takes 30 less damage from attacks (after applying "
                       "Weakness and Resistance).")
        self.assertIn("shield:reduce:30", r["tags"])

    def test_passive_bench_immune(self):
        r = self._tags("As long as this Pok\u00e9mon is on your Bench, prevent "
                       "all damage done to this Pok\u00e9mon by attacks (both "
                       "yours and your opponent\u2019s).", kind="ability",
                       name="BenchGuard")
        self.assertIn("passive:bench_immune", r["tags"])

    def test_ko_last_turn_condition(self):
        r = self._tags("If any of your Pok\u00e9mon were Knocked Out by damage "
                       "from an attack during your opponent\u2019s last turn, "
                       "this attack does 60 more damage.")
        self.assertIn("dmg_plus:60:if:opp_ko_last_turn", r["tags"])

    def test_draw_to_hand_size(self):
        r = self._tags("Draw cards until you have 7 cards in your hand.",
                       kind="ability", name="Refill")
        self.assertIn("draw_to:7", r["tags"])

    def test_evolve_from_deck(self):
        r = self._tags("Search your deck for a card that evolves from this "
                       "Pok\u00e9mon and put it onto this Pok\u00e9mon to "
                       "evolve it. Then, shuffle your deck.")
        self.assertIn("evolve_from_deck", r["tags"])

    def test_plain_text_unchanged(self):
        """不涉修正器的普通卡面文本不应被后置识别污染（回归保护）"""
        r = self._tags("This attack does 60 damage. Your opponent\u2019s "
                       "Active Pok\u00e9mon is now Asleep.")
        self.assertTrue(all("coin" not in t for t in r["tags"]), r["tags"])


class TestExtraDamageClauseTargets(unittest.TestCase):
    """附加伤害子句的**目标形态**（2026-09-22 审计修复）

    背景：早期 `_EN_DAMAGE_RE` 只取**第一个** `does N damage` 且**恒定**标为
    `active_opponent`，而 `damage` 动作不产 tag → 效果在引擎里**完全消失**：
      · 「does N damage to itself」（反伤，全库 49 处）读成打对手战斗位
      · 「to 1 of your opponent's Benched Pokémon」读成打战斗位
    —— 若该招式还有别的 tag，卡会进池并**静默丢效果**（实测 12 张卡命中）。
    """

    @staticmethod
    def _tags(text, kind="attack", name="X"):
        return E.to_engine_tags([E.parse_rule(text, kind=kind, name=name)])

    def test_recoil_maps_to_self_tag(self):
        """「This Pokémon does N damage to itself」→ 引擎 `self:N`（精确对应）"""
        r = self._tags("This attack does 30 damage to your opponent\u2019s "
                       "Active Pok\u00e9mon, and this Pok\u00e9mon also does "
                       "20 damage to itself.")
        self.assertIn("self:20", r["tags"])
        self.assertEqual(r["unsupported"], [])

    def test_conditional_recoil_not_emitted(self):
        """条件性反伤（抛币/条件从句）不得产出无条件 `self:N`

        否则从「两面都是反面才自伤」变成「必定自伤」，属实质性加强。
        """
        r = self._tags("Flip 2 coins. If both of them are tails, this "
                       "Pok\u00e9mon also does 30 damage to itself.")
        self.assertNotIn("self:30", r["tags"])
        self.assertTrue(r["unsupported"], "应如实登记为未建模")

    def test_bench_all_maps_to_snipe(self):
        """「to each of your opponent's Benched Pokémon」→ `snipe:opp_bench:N:all`"""
        r = self._tags("This attack does 50 damage to each of your "
                       "opponent\u2019s Benched Pok\u00e9mon. "
                       "(Don\u2019t apply Weakness and Resistance for "
                       "Benched Pok\u00e9mon.)")
        self.assertIn("snipe:opp_bench:50:all", r["tags"])
        self.assertEqual(r["unsupported"], [])

    def test_scaling_bench_all_not_flattened(self):
        """带缩放（for each …）的全体后备伤害**不得**按固定值映射

        否则会静默低估（Kyurem ex：「10 damage to each Benched for each Prize
        card your opponent has taken」）。
        """
        r = self._tags("This attack also does 10 damage to each of your "
                       "opponent\u2019s Benched Pok\u00e9mon for each Prize "
                       "card your opponent has taken. "
                       "(Don\u2019t apply Weakness and Resistance for "
                       "Benched Pok\u00e9mon.)")
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])

    def test_conditional_bench_all_not_emitted(self):
        """句首带条件的全体后备伤害不得产出无条件 tag（Ting-Lu）"""
        r = self._tags("If a Stadium is in play, this attack also does 30 "
                       "damage to each of your opponent\u2019s Benched "
                       "Pok\u00e9mon, and discard that Stadium.")
        self.assertNotIn("spread:all:30", r["tags"])
        self.assertTrue(r["unsupported"])

    def test_benched_one_maps_to_snipe_not_active(self):
        """「to 1 of your opponent's Benched Pokémon」→ `snipe:opp_bench:N:1`

        ⚠ 关键是**不能**再记成打战斗位（早期行为），也不能静默丢掉。
        """
        r = self._tags("Discard 2 Energy from this Pok\u00e9mon. This attack "
                       "does 90 damage to 1 of your opponent\u2019s Benched "
                       "Pok\u00e9mon. (Don\u2019t apply Weakness and "
                       "Resistance for Benched Pok\u00e9mon.)")
        self.assertIn("discard_energy:2", r["tags"])
        self.assertIn("snipe:opp_bench:90:1", r["tags"])
        self.assertEqual(r["unsupported"], [])

    def test_own_bench_damage_maps_to_own_scope(self):
        """打**我方**后备的伤害 → `snipe:own_bench:N:1`（不得当成打对手）"""
        r = self._tags("This attack also does 10 damage to 1 of your Benched "
                       "Pok\u00e9mon. (Don\u2019t apply Weakness and "
                       "Resistance for Benched Pok\u00e9mon.)")
        self.assertEqual(r["tags"], ["snipe:own_bench:10:1"])

    def test_subset_target_still_unmodeled(self):
        """带子集限定的目标（`{ex}` / `that has …`）仍**不可**映射

        `snipe` 只能表达「任意 1 只 / 全部」，无法表达「只打 ex」这类筛选。
        """
        r = self._tags("This attack does 60 damage to 1 of your opponent\u2019s "
                       "Benched Pok\u00e9mon {ex} or Benched Pok\u00e9mon {V}. "
                       "(Don\u2019t apply Weakness and Resistance for "
                       "Benched Pok\u00e9mon.)")
        self.assertEqual(r["tags"], [])
        self.assertTrue(any("附加伤害目标未建模" in u["reason"]
                            for u in r["unsupported"]), r["unsupported"])

    def test_selected_n_target_maps(self):
        """「to 2 of your opponent's Pokémon」（含战斗位）→ `snipe:opp:N:2`"""
        r = self._tags("This attack does 50 damage to 2 of your "
                       "opponent\u2019s Pok\u00e9mon. (Don\u2019t apply "
                       "Weakness and Resistance for Benched Pok\u00e9mon.)")
        self.assertIn("snipe:opp:50:2", r["tags"])

    def test_counters_placement_uses_clause_target(self):
        """「put N damage counters on <目标>」必须按子句判目标

        历史缺陷：整句按 `spread:N:per`（**随机** n 只对手后备）映射 →
        「on **1 of your opponent's Pokémon**」（含战斗位、由玩家选定）算错。
        """
        one = self._tags("Put 2 damage counters on 1 of your opponent\u2019s "
                         "Pok\u00e9mon. (Don\u2019t apply Weakness and "
                         "Resistance for Benched Pok\u00e9mon.)")
        self.assertIn("snipe:opp:20:1", one["tags"])
        each = self._tags("Put 2 damage counters on each of your opponent\u2019s "
                          "Pok\u00e9mon.")
        self.assertIn("snipe:opp:20:all", each["tags"])
        self.assertNotIn("spread:2:10", one["tags"] + each["tags"],
                         "不得退化成「随机 2 只后备」")

    def test_free_distribution_only_when_bench_only(self):
        """「in any way you like」只在**仅后备**时可用 `spread_free`

        含战斗位时引擎的 spread_free（只覆盖后备）不可代用 → 必须登记未建模。
        """
        bench = self._tags("Put 6 damage counters on your opponent\u2019s "
                           "Benched Pok\u00e9mon in any way you like.")
        self.assertIn("spread_free:60", bench["tags"])
        anyp = self._tags("Put 4 damage counters on your opponent\u2019s "
                          "Pok\u00e9mon in any way you like.")
        self.assertEqual(anyp["tags"], [])
        self.assertTrue(anyp["unsupported"])


class TestRulesParenIsNotAnEffect(unittest.TestCase):
    """规则提醒括号 `(Don't apply Weakness and Resistance …)` **不是**卡面效果

    它是官方规则复述，却会被 `_EN_MODIFY_RE` 命中 "Resistance" →
    给整条效果塞一条 rule 未建模 → **整卡不可对战**。
    实测全库 133 张卡含此类条目，其中 84 张纯属该括号假阳性。
    """

    @staticmethod
    def _tags(text, kind="attack", name="X"):
        return E.to_engine_tags([E.parse_rule(text, kind=kind, name=name)])

    def test_paren_alone_does_not_trigger_modify(self):
        r = self._tags("This attack does 50 damage to each of your "
                       "opponent\u2019s Benched Pok\u00e9mon. "
                       "(Don\u2019t apply Weakness and Resistance for "
                       "Benched Pok\u00e9mon.)")
        self.assertFalse(any("静态规则修改" in u["reason"]
                             for u in r["unsupported"]), r["unsupported"])

    def test_before_applying_paren_does_not_trigger_modify(self):
        r = self._tags("Attacks used by this Pok\u00e9mon do 50 more damage "
                       "to your opponent\u2019s Active Pok\u00e9mon {ex} "
                       "(before applying Weakness and Resistance).",
                       kind="item", name="Belt")
        self.assertFalse(any("静态规则修改" in u["reason"]
                             for u in r["unsupported"]), r["unsupported"])

    def test_real_weakness_change_still_detected(self):
        """真的改写弱点/抗性的效果仍须被识别（不得因修假阳性而漏真效果）"""
        r = self._tags("The Weakness of your opponent\u2019s Active "
                       "Pok\u00e9mon is now {G}.")
        self.assertTrue(any("静态规则修改" in u["reason"]
                            for u in r["unsupported"]), r["unsupported"])

    def test_source_text_keeps_original(self):
        """卡面原文照旧落库（差异透明 C5）——只影响语义判定，不改写文本"""
        text = ("This attack does 50 damage to each of your opponent\u2019s "
                "Benched Pok\u00e9mon. (Don\u2019t apply Weakness and "
                "Resistance for Benched Pok\u00e9mon.)")
        spec = E.parse_rule(text, kind="attack", name="X")
        self.assertIn("Don\u2019t apply Weakness", spec["source_text"])


class TestClauseScopedTargets(unittest.TestCase):
    """目标判定**只看子句**，不看全文（2026-09-22：检索目标 / 多选项卡换位）"""

    @staticmethod
    def _tags(text, kind="attack", name="X"):
        return E.to_engine_tags([E.parse_rule(text, kind=kind, name=name)])

    def test_search_target_from_for_clause(self):
        """「Search your deck for an **Energy** card and attach it to 1 of your
        Benched {G} **Pokémon**」——检索的是能量卡，此前因全文出现 Pokémon
        被映射成 `search_pokemon`（引擎去拿宝可梦，**静默拿错东西**）。
        全库 28 条此类误判。
        """
        r = self._tags("Search your deck for an Energy card and attach it to "
                       "1 of your Benched {G} Pok\u00e9mon. Then, shuffle "
                       "your deck.")
        self.assertIn("search_energy:1", r["tags"])
        self.assertNotIn("search_pokemon:1", r["tags"])

    def test_search_target_pokemon_still_works(self):
        r = self._tags("Search your deck for up to 2 Pok\u00e9mon with {F} "
                       "Resistance, reveal them, and put them into your hand. "
                       "Then, shuffle your deck.")
        self.assertIn("search_pokemon:2", r["tags"])

    def test_choose_one_switch_is_self_not_gust(self):
        """`Choose 1:` 多选项卡：换位目标只看换位子句

        真实反例 Kieran：「• Switch your Active Pokémon with 1 of your Benched
        Pokémon. • …do 30 more damage to your opponent's Active Pokémon {ex}…」
        早期用全文判 → 因**另一条**选项提到 opponent 而判成 gust，
        把对手后备拉上前台（我方换位变成干扰对手，完全反了）。
        """
        r = self._tags(
            "Choose 1:\n\n• Switch your Active Pok\u00e9mon with 1 of your "
            "Benched Pok\u00e9mon.\n• During this turn, attacks used by your "
            "Pok\u00e9mon do 30 more damage to your opponent\u2019s Active "
            "Pok\u00e9mon {ex} and Active Pok\u00e9mon {V} (before applying "
            "Weakness and Resistance).", kind="supporter", name="Kieran")
        self.assertIn("switch_self", r["tags"])
        self.assertNotIn("gust", r["tags"])

    def test_gust_still_detected(self):
        r = self._tags("Switch in 1 of your opponent\u2019s Benched "
                       "Pok\u00e9mon to the Active Spot.", kind="item",
                       name="Boss")
        self.assertIn("gust", r["tags"])


class TestDiscardPileRecovery(unittest.TestCase):
    """弃牌堆回收（`recover:N[:kind]`）——B3 解析召回补齐（2026-09-22）

    引擎早有 `recover:N[:kind]`，但正则要求「数字 + 固定名词序」，
    真实卡面最常见的冠词型与组合型全部漏掉（B3 的 8 条）。
    """

    @staticmethod
    def _tags(text, kind="item", name="X"):
        return E.to_engine_tags([E.parse_rule(text, kind=kind, name=name)])

    def test_article_form(self):
        r = self._tags("Put a Pok\u00e9mon from your discard pile into your "
                       "hand.")
        self.assertIn("recover:1:pokemon", r["tags"])

    def test_trainer_kind(self):
        r = self._tags("Put a Trainer card from your discard pile into your "
                       "hand.")
        self.assertIn("recover:1:trainer", r["tags"])

    def test_supporter_kind(self):
        r = self._tags("Put up to 2 Supporter cards from your discard pile "
                       "into your hand.")
        self.assertIn("recover:2:supporter", r["tags"])

    def test_any_combination(self):
        r = self._tags("Put up to 5 in any combination of Pok\u00e9mon and "
                       "Basic Energy cards from your discard pile into your "
                       "hand.")
        self.assertIn("recover:5:any", r["tags"])

    def test_pokemon_or_basic_energy(self):
        r = self._tags("Put a Pok\u00e9mon or a Basic Energy card from your "
                       "discard pile into your hand.")
        self.assertIn("recover:1:any", r["tags"])

    def test_subset_qualifier_not_mapped(self):
        """子集限定（「that don't have a Rule Box」/「{F} Pokémon」）不映射

        引擎只能按大类回收 → 映射出去会从弃牌堆取到**超出卡面范围**的卡。
        """
        r = self._tags("Put up to 3 in any combination of Pok\u00e9mon that "
                       "don\u2019t have a Rule Box and Basic Energy cards "
                       "from your discard pile into your hand.")
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])

    def test_unknown_count_not_guessed(self):
        """回收数量判不出（抛币决定）→ 绝不兜底成 1"""
        r = self._tags("Flip 3 coins. Put a number of cards up to the number "
                       "of heads from your discard pile into your hand.")
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])


class TestEvolveTriggerFalsePositive(unittest.TestCase):
    """触发时机从句里的 `evolve` **不是**进化效果（2026-09-22 修复）

    卡面：`When you play this Pokémon from your hand to evolve 1 of your Pokémon, …`
    这句说的是**何时生效**。早期未区分 → 给整条特性塞一条 evolve 动作 →
    落成「招式/特性驱动的进化需卡级 tag」→ **整卡不进池**。
    实测 **25 张卡全部栽在这一条上**，是本轮最大的单一假阳性。
    """

    @staticmethod
    def _tags(text, kind="ability", name="X"):
        return E.to_engine_tags([E.parse_rule(text, kind=kind, name=name)])

    def test_trigger_clause_does_not_produce_evolve(self):
        r = self._tags(
            "Once during your turn, when you play this Pok\u00e9mon from your "
            "hand to evolve 1 of your Pok\u00e9mon, you may use this Ability. "
            "Draw 2 cards.")
        self.assertIn("draw:2", r["tags"])
        self.assertEqual(r["unsupported"], [],
                         "触发从句不得产生 evolve 未建模条")

    def test_opponent_trigger_clause_too(self):
        r = self._tags(
            "Whenever your opponent plays a Pok\u00e9mon from their hand to "
            "evolve 1 of their Pok\u00e9mon, put 2 damage counters on that "
            "Pok\u00e9mon.")
        self.assertFalse(any("进化" in u["reason"] for u in r["unsupported"]),
                         r["unsupported"])

    def test_real_evolve_effect_still_detected(self):
        """真的进化效果仍须被识别（不得因修假阳性而漏真效果）"""
        r = self._tags(
            "When you play this Pok\u00e9mon from your hand to evolve 1 of "
            "your Pok\u00e9mon during your turn, you may search your deck for "
            "a card that evolves from this Pok\u00e9mon and put it onto this "
            "Pok\u00e9mon to evolve it.")
        self.assertIn("evolve_from_deck", r["tags"])


class TestMillMapping(unittest.TestCase):
    """磨牌（Discard the top N cards of your / your opponent's deck）→ `mill:N[:opp]`"""

    @staticmethod
    def _tags(text, kind="attack", name="X"):
        return E.to_engine_tags([E.parse_rule(text, kind=kind, name=name)])

    def test_own_deck(self):
        self.assertIn("mill:3", self._tags(
            "Discard the top 3 cards of your deck.")["tags"])

    def test_opponent_deck(self):
        self.assertIn("mill:1:opp", self._tags(
            "Discard the top card of your opponent\u2019s deck.")["tags"])

    def test_article_form(self):
        self.assertIn("mill:1", self._tags(
            "Discard the top card of your deck.")["tags"])

    def test_mill_plus_scaling_mill_still_mapped(self):
        """「磨 6 张 + 每张<子集>加伤」：磨牌必须映射

        缩放侧带**子集限定**（只数 Basic {W} Energy）而引擎只有「本回合弃的能量总数」
        → 缩放如实留未建模（宁缺勿错），但**磨牌本身不能因此丢掉**。
        """
        r = self._tags("Discard the top 6 cards of your deck, and this attack "
                       "does 100 damage for each Basic {W} Energy card that "
                       "you discarded in this way.")
        self.assertIn("mill:6", r["tags"])
        self.assertTrue(r["unsupported"], "子集限定的缩放应如实登记未建模")


class TestParameterizedEnergyCondition(unittest.TestCase):
    """带参数条件：「If this Pokémon has any {L} Energy attached …」（2026-09-22）"""

    @staticmethod
    def _tags(text, kind="attack", name="X"):
        return E.to_engine_tags([E.parse_rule(text, kind=kind, name=name)])

    def test_has_energy_of_color(self):
        r = self._tags("If this Pok\u00e9mon has any {L} Energy attached, "
                       "this attack does 80 more damage.")
        self.assertIn("dmg_plus:80:if:own_active_energy_type:lightning",
                      r["tags"])
        self.assertEqual(r["unsupported"], [])

    def test_extra_energy_condition_is_not_approximated(self):
        """「at least N **extra** Energy (in addition to this attack's cost)」
        ——条件含招式费用，用「总数 ≥ N」会**提前触发**（静默加强）→ 必须不映射
        """
        r = self._tags("If this Pok\u00e9mon has at least 2 extra Energy "
                       "attached (in addition to this attack\u2019s cost), "
                       "this attack does 100 more damage.")
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])

    def test_special_energy_condition_is_not_approximated(self):
        """「any Special Energy attached」—— 引擎无「特殊能量」计数源 → 不映射"""
        r = self._tags("If this Pok\u00e9mon has any Special Energy attached, "
                       "this attack does 100 more damage.")
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])


class TestExtractJson(unittest.TestCase):
    """LLM 输出容错：围栏 / 前后缀 / thinking 残留"""

    def test_plain(self):
        self.assertEqual(E.extract_json('[{"a":1}]'), [{"a": 1}])

    def test_with_fences(self):
        self.assertEqual(E.extract_json('```json\n[{"a":1}]\n```'), [{"a": 1}])

    def test_with_prefix_text(self):
        self.assertEqual(E.extract_json('好的，结果如下：[{"a":1}] 以上。'),
                         [{"a": 1}])

    def test_invalid(self):
        self.assertIsNone(E.extract_json("没有 JSON"))
        self.assertIsNone(E.extract_json('{"a":1}'))
        self.assertIsNone(E.extract_json(""))
        self.assertIsNone(E.extract_json("[{bad json}]"))


class TestGoldenSetIntegrity(unittest.TestCase):
    """黄金集自身的不变量（防止数据漂移导致评测失真）"""

    @classmethod
    def setUpClass(cls):
        cls.g = E.load_golden()
        cls.cases = cls.g["cases"]

    def test_ids_unique(self):
        ids = [c["id"] for c in self.cases]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_case_has_expectations(self):
        for c in self.cases:
            self.assertIn("text", c)
            self.assertIn("expect", c)
            self.assertIn(c["expect"].get("kind"), E.KINDS, c["id"])
            self.assertIn(c["expect"].get("trigger"), E.TRIGGERS, c["id"])
            self.assertTrue(c["kind"], c["id"])

    def test_non_synthetic_texts_match_card_pool(self):
        """非合成、非留出用例的文本必须与卡池 desc_zh 一致（数据漂移守卫）

        合成用例与留出集是**手写句式**，不来自卡池，故跳过。

        注意：卡池 = 人工池 + 自动生成的扩展池（卡池扩容）。黄金集是照**人工池**
        写的，而不同卡的招式/特性**可能同名**（例：多个厄诡椪形态都有「Teal Dance」），
        故按名称建表时必须**人工条目优先**，否则自动条目会覆盖人工中文转写。
        """
        from engine import reallib
        pool: dict = {}
        for name, item in reallib.catalog().items():
            d = item["def"]
            derived = bool(item.get("derived"))
            for a in getattr(d, "attacks", ()) or ():
                if a.display_desc and (a.name not in pool or not derived):
                    pool[a.name] = a.display_desc
            for ab in getattr(d, "abilities", ()) or ():
                if ab.display_desc and (ab.name not in pool or not derived):
                    pool[ab.name] = ab.display_desc
        for c in self.cases:
            if c.get("synthetic") or c.get("holdout"):
                continue
            if c["kind"] == "energy":
                continue
            self.assertIn(c["name"], pool, c["id"])
            self.assertEqual(pool[c["name"]], c["text"],
                             f"{c['id']} 文本与卡池 desc_zh 不一致（数据已变更）")

    def test_holdout_group_exists(self):
        self.assertGreaterEqual(
            sum(1 for c in self.cases if c.get("holdout")), 5,
            "应保留留出集以观测泛化")


class TestZhSynonymPatterns(unittest.TestCase):
    """中文同义句式回归（K2 报告「失败用例明细」登记的待补项）

    这三条原为 K2 留出集（holdout）的失败用例，期望值由 `artifacts/k2_report.md`
    明确给出。根因都是**只认一种措辞**：
      · 「移除…N 个能量」——`_DISCARD_ENERGY_RE` 只认动词「弃/弃掉」；
      · 「回复自己宝可梦 N 点 HP」——`_HEAL_RE` 要求数字**紧贴**「回复」，
        中间隔着受事对象就失配；
      · 「与…交换」——switch 分支只认「互换/换下/交替」，不认同义的「交换」。
    修好后 holdout 准确率 70% → 100%。此处逐条固化，防止再退化。
    """

    def _spec(self, text):
        res = E.parse_effects(text, kind="attack", name="T", backend="rule")
        return (res["specs"] or [None])[0], res["errors"]

    def _acts(self, spec):
        """{action_type: [amount, ...]} 与 {action_type: [target, ...]}"""
        amt, tgt = {}, {}
        for a in (spec or {}).get("actions", []):
            amt.setdefault(a["type"], []).append(a.get("amount"))
            if a.get("target"):
                tgt.setdefault(a["type"], []).append(a["target"])
        return amt, tgt

    def test_remove_energy_synonym(self):
        """「移除…N 个能量」= 弃能量（同义动词不可漏）"""
        spec, errs = self._spec("移除对手战斗宝可梦身上的 1 个能量。")
        self.assertEqual(errs, [])
        amt, tgt = self._acts(spec)
        self.assertEqual(amt.get("discard"), [1])
        self.assertEqual(tgt.get("discard"), ["active_opponent"])

    def test_swap_synonym(self):
        """「交换」同义于「互换」"""
        spec, errs = self._spec("将自己的后备宝可梦与战斗宝可梦交换。")
        self.assertEqual(errs, [])
        amt, tgt = self._acts(spec)
        self.assertIn("switch", amt)
        self.assertEqual(tgt.get("switch"), ["self"])

    def test_heal_phrase_with_intervening_target(self):
        """「回复 <受事对象> N 点 HP」——数字与动词之间允许有间隔"""
        spec, errs = self._spec("回复自己宝可梦 30 点 HP。")
        self.assertEqual(errs, [])
        amt, tgt = self._acts(spec)
        self.assertEqual(amt.get("heal"), [30])
        self.assertEqual(tgt.get("heal"), ["self"])

    def test_heal_target_follows_opponent_word(self):
        """「回复对手…」应指向对手（此前一律 self，是错的）"""
        spec, _ = self._spec("回复对手战斗宝可梦 30 点。")
        _, tgt = self._acts(spec)
        self.assertEqual(tgt.get("heal"), ["active_opponent"])


class TestEvaluation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ev = E.evaluate("rule")

    def test_meets_k2_target(self):
        self.assertIsNotNone(self.ev["strict_acc"])
        self.assertGreaterEqual(self.ev["strict_acc"], 0.85,
                                f"规则基线严格准确率未达 K2 门槛：{self.ev}")

    def test_parse_rate_is_full(self):
        self.assertEqual(self.ev["parse_rate"], 1.0)

    def test_grouped_metrics_reported(self):
        self.assertGreater(self.ev["seen_n"], 0)
        self.assertGreater(self.ev["holdout_n"], 0)
        self.assertIsNotNone(self.ev["seen_strict_acc"])
        self.assertIsNotNone(self.ev["holdout_strict_acc"])

    def test_holdout_acc_floor(self):
        """留出集准确率下限

        留出集是**未做针对性适配**的表述，比 seen 更能反映泛化。补句式的修复
        应让它上升而不是靠 seen 掩盖——设下限防止回归（当前 100%）。
        """
        self.assertGreaterEqual(
            self.ev["holdout_strict_acc"], 0.9,
            f"留出集准确率退化：{self.ev['holdout_strict_acc']}")

    def test_failures_are_traceable(self):
        """失败用例必须给出可读 diff（便于补句式）"""
        for row in self.ev["rows"]:
            fields = [row[k] for k in ("kind", "trigger", "action_set",
                                       "amount", "target", "limit")
                      if row[k] is not None]
            if not (all(fields) if fields else False):
                self.assertTrue(row["diffs"], f"{row['case']} 缺 diff 说明")

    def test_card_pool_parses_without_schema_errors(self):
        pool = E.parse_card_pool(backend="rule")
        self.assertGreater(pool["n"], 20)
        bad = [i["name"] for i in pool["items"] if i["errors"]]
        self.assertEqual(bad, [], f"卡池仍有 Schema 错误：{bad}")

    def test_card_pool_has_chinese_descs(self):
        from engine import reallib
        for name, item in reallib.catalog().items():
            d = item["def"]
            for a in getattr(d, "attacks", ()) or ():
                if a.name_zh:
                    self.assertTrue(a.display_desc,
                                    f"{name} 的招式 {a.name} 缺中文效果文本")


class TestTargetNormalization(unittest.TestCase):
    """`action.target` 别名归一化

    背景：实测 A 类失败有一条 `action.target 非法：opponent_bench` —— LLM 想表达
    「对手后备区」，而合法枚举值是 `active_opponent_bench`。整条效果因**一个词**作废。

    归一化必须**只收语义唯一者**：歧义取值（如 `active` 分不清敌我）宁可不收，
    因为「猜错了静默用错目标」比「解析失败」更危险——前者会让引擎把效果结算到
    错的宝可梦身上，且**不会报错**。
    """

    def test_known_alias_normalized(self):
        for raw, want in (("opponent_bench", "active_opponent_bench"),
                          ("Opp-Bench", "active_opponent_bench"),   # 大小写 + 连字符
                          ("my active", "self"),                    # 空格
                          ("library", "deck"),
                          ("opp_hand", "opponent_hand")):
            self.assertEqual(E.normalize_target(raw), want)

    def test_legal_value_untouched(self):
        for v in E.TARGETS:
            self.assertEqual(E.normalize_target(v), v)

    def test_ambiguous_value_not_guessed(self):
        """歧义取值**不猜**（见类 docstring 的边界说明）"""
        for v in ("active", "his_pokemon", "the_bench", ""):
            self.assertEqual(E.normalize_target(v), v,
                             f"{v!r} 属于语义不唯一，不应被归一化")
        self.assertEqual(E.normalize_target(None), None)

    def test_llm_target_alias_rescued_on_real_path(self):
        """走真实 llm 分支：别名被救回，且 **`raw` 保持模型原样**"""
        import json as _json
        from unittest import mock
        spec = [{"kind": "attack", "name": "Drag Off", "trigger": "on_use",
                 "conditions": [],
                 "actions": [{"type": "switch", "amount": None,
                              "target": "opponent_bench",
                              "extra": {"reason": "gust"}}],
                 "source_text": "自述转写：把对手的后备宝可梦换到战斗位。"}]
        fake = {"content": _json.dumps(spec, ensure_ascii=False),
                "endpoint": "http://127.0.0.1:1/v1", "model": "test",
                "privacy": {}, "elapsed_ms": 1, "source": "test"}
        with mock.patch.object(E, "_llm_chat", return_value=fake):
            r = E.parse_effects("把对手的后备宝可梦换到战斗位。", kind="attack",
                                name="Drag Off", backend="llm")
        self.assertEqual(r["backend"], "llm")
        self.assertEqual(r["errors"], [], f"本应被别名救回，却仍报错：{r['errors']}")
        self.assertEqual(r["specs"][0]["actions"][0]["target"],
                         "active_opponent_bench")
        # **raw 不得被改动**：评测（k2_local_eval）用 raw 判断「模型是否真产出了
        # 结果」，若把归一化也写进 raw，那个判据就失真了。
        self.assertEqual(r["raw"][0]["actions"][0]["target"], "opponent_bench")

    def test_non_string_and_missing_target_safe(self):
        """异常输入不得抛错（模型可能给 null / 数字 / 缺字段）"""
        specs = [{"kind": "attack", "actions": [
            {"type": "damage", "target": None},
            {"type": "damage"},
            {"type": "damage", "target": 123},
        ]}]
        E.normalize_specs(specs)          # 不抛异常即通过
        self.assertIsNone(specs[0]["actions"][0]["target"])


class TestValueNormalization(unittest.TestCase):
    """`actions[].extra` 取值字段归一化（计数源 / 能量属性）

    背景：卡池重跑实测发现，即使提示词给了取值域，模型**仍会自创变体**
    （计数源 `own_all_pokemon`、能量属性 `w`）。这类错误在引擎侧是**硬失败**
    （`_eval_counter` 遇未知计数源直接抛 ValueError）→ 救回收益明确。

    与 target 归一化同一条边界：**只映射语义唯一者，歧义一律不猜**。
    """

    def test_counter_alias_and_case(self):
        for raw, want in (("own_all_pokemon", "own_field"),
                          ("Opp-All-Pokemon", "opp_field"),   # 大小写+连字符
                          ("own_hand_count", "own_hand"),      # 冗余后缀
                          ("own_active_damage_counters", "own_active_counters")):
            self.assertEqual(E.normalize_counter(raw), want)

    def test_valid_counter_not_mangled(self):
        """合法计数源必须原样保留 —— 尤其 `own_discard_count` 恰以 `_count` 结尾，
        后缀剥离规则若写得糙就会把它改成非法的 `own_discard`。"""
        for v in E.engine_vocab().get("counters", []):
            self.assertEqual(E.normalize_counter(v), v, f"合法值 {v} 被改动了")

    def test_unknown_counter_left_alone(self):
        for raw in ("unknown_thing", "own_all_pokemon_x", "own_pokemon"):
            self.assertEqual(E.normalize_counter(raw), raw)

    def test_energy_type_alias(self):
        for raw, want in (("w", "water"), ("G", "grass"), ("metal_energy", "metal")):
            self.assertEqual(E.normalize_energy_type(raw), want)

    def test_ambiguous_energy_type_not_guessed(self):
        """`f`(Fire? Fighting?) / `d`(Darkness? Dragon?) 在 TCG 缩写里有歧义 → 不猜。

        `basic` 是「基本能量」泛称（不含属性）→ 同样不猜：猜成某个属性会**静默算错**。
        """
        for raw in ("f", "d", "basic", "colorless_x"):
            self.assertEqual(E.normalize_energy_type(raw), raw)

    def test_normalize_specs_covers_extra_and_is_pure(self):
        """端到端：extra 被归一化，且**原对象不被改动**（`raw` 依赖它）"""
        specs = [{"kind": "attack", "actions": [
            {"type": "damage_mod", "extra": {"mode": "plus_per",
                                             "per": "own_all_pokemon"}},
            {"type": "attach", "extra": {"energy_type": "w", "zone": "hand"}},
        ]}]
        out = E.normalize_specs(specs)
        self.assertEqual(out[0]["actions"][0]["extra"]["per"], "own_field")
        self.assertEqual(out[0]["actions"][1]["extra"]["energy_type"], "water")
        self.assertEqual(specs[0]["actions"][0]["extra"]["per"], "own_all_pokemon",
                         "原对象被就地改动了（会污染 raw）")
        # 归一化后应能映射到引擎 tag（此前 own_all_pokemon / w 会失败）
        tags = E.to_engine_tags(out)["tags"]
        self.assertTrue(any("own_field" in t for t in tags), tags)
        self.assertTrue(any("water" in t for t in tags), tags)


class TestTargetOptional(unittest.TestCase):
    """`target` 的「未指定」是合法状态（与 `amount` 一致）

    历史：`validate_specs` 原先写的是 `a.get("target") not in TARGETS`，
    把「**没填**」也判为非法 —— 而真值里 `rule` 类动作本就不填 target。
    后果：模型输出 `"target": null` 就整条作废（实测 `ru_energy_once` 因
    `action.target 非法：None` 落进 A 类失败，掩盖了真正的分歧）。
    只应惩罚「**给了但非法**」的值。
    """

    def _spec(self, action):
        return [{"kind": "rule", "name": "X", "trigger": "static", "conditions": [],
                 "actions": [action], "source_text": "t", "citations": []}]

    def test_missing_target_is_ok(self):
        errs = E.validate_specs(self._spec({"type": "draw", "amount": 2}))
        self.assertEqual(errs, [], f"缺 target 不应报错：{errs}")

    def test_explicit_null_target_is_ok(self):
        errs = E.validate_specs(self._spec({"type": "draw", "amount": 2, "target": None}))
        self.assertEqual(errs, [], f"显式 null 应与缺省等价：{errs}")

    def test_invalid_target_still_rejected(self):
        """放宽不能过头：枚举外的**非空**取值仍须报错"""
        errs = E.validate_specs(self._spec({"type": "draw", "amount": 2,
                                            "target": "opponent_bench"}))
        self.assertTrue(any("target 非法" in e for e in errs), errs)


if __name__ == "__main__":
    unittest.main(verbosity=2)
