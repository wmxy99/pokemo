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
        opp = self._tags("Discard an Energy from your opponent\u2019s "
                         "Active Pok\u00e9mon.")
        self.assertIn("discard_energy:1:opp", opp["tags"])

    def test_discard_special_energy_uses_special_tag(self):
        """第二十四批：引擎已能区分特殊/基本（`senergy`）→ 必须落 `:special`

        此前的守卫（2026-10-27）是「引擎分不出基本/特殊 → 拒绝」。现在特殊能量
        按**卡**存在 `senergy` 里，故改为精确映射 `discard_energy:1:opp:special`；
        普通 `discard_energy:1:opp` 仍会先弃基本能量（对手只贴基本时错误生效）。
        实测 Cobalion「Discard a Special Energy from your opponent's Active Pokémon」。
        """
        r = self._tags("Discard a Special Energy from your opponent\u2019s "
                       "Active Pok\u00e9mon.")
        self.assertEqual(r["tags"], ["discard_energy:1:opp:special"])
        self.assertEqual(r["unsupported"], [])

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
        """子集措辞（具名/类别/颜色限定）不得按总数映射（否则系统性多算）

        2026-10-29 第二十六批起，「Benched Cynthia's Pokémon」「Benched {F}
        Pokémon」这两句已有**逐项**计数源（`own_bench_counters_named:cynthia` /
        `own_bench_counters_type:fighting`，见 `TestScalingCounters`）；本条改用
        引擎仍**无法逐项**判定的子集措辞守护同一不变量。
        """
        for text in (
            "This attack does 30 more damage for each damage counter on all of "
            "your Benched Tera Pok\u00e9mon.",           # 类别子集（无指示物版）
            "This attack does 30 more damage for each damage counter on all of "
            "your Benched Pok\u00e9mon that have an Ability.",   # 特性子集
        ):
            r = self._tags(text)
            self.assertEqual(r["tags"], [],
                             f"子集措辞不得映射出 tag：{text[:50]}")
            self.assertTrue(r["unsupported"], "应如实登记为未建模")

    def test_public_count_sources(self):
        """新增的公开计数源必须按卡面语义精确落 tag（2026-10-27）

        这些都不是"子集近似"：Basic/Evolution 是进化阶段，对手手牌是**张数**
        （公开信息），全场指示物 / 特殊状态数 / 道具数都是确定性可数的。
        """
        cases = (
            ("This attack does 20 damage for each of your Basic Pok\u00e9mon "
             "in play.", "dmg_set:20:per:own_basic"),
            ("This attack does 40 more damage for each of your Evolution "
             "Pok\u00e9mon in play.", "dmg_plus:40:per:own_evolution"),
            ("This attack does 30 damage for each card in your opponent\u2019s "
             "hand.", "dmg_set:30:per:opp_hand"),
            ("This attack does 10 more damage for each damage counter on all "
             "of your opponent\u2019s Pok\u00e9mon.",
             "dmg_plus:10:per:opp_field_counters"),
            ("This attack does 100 damage for each Special Condition affecting "
             "your opponent\u2019s Active Pok\u00e9mon.",
             "dmg_set:100:per:opp_active_status_count"),
            ("This attack does 30 damage for each Pok\u00e9mon Tool attached "
             "to all of your Pok\u00e9mon.",
             "dmg_set:30:per:own_field_tools"),
        )
        for text, tag in cases:
            r = self._tags(text)
            self.assertEqual(r["tags"], [tag], text)
            self.assertEqual(r["unsupported"], [], text)

    def test_subset_qualified_basic_still_rejected(self):
        """「Basic Pokémon in your discard pile」不是在场计数 → 不得套用"""
        r = self._tags("This attack does 20 damage for each Basic Pok\u00e9mon "
                       "in your discard pile.")
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])

    def test_any_number_of_not_silently_modeled(self):
        """「any number of」是自由数量，不得静默压成固定 N（2026-10-27）

        实测踩坑：Rotom「search … any number of Pokémon that have Rotom in
        their name」被压成 `search_bench:1`（少检索 + 忽略卡名筛选）；
        Gorebyss / Honchkrow / Alakazam 的任意数量子句则被整段丢弃。
        全部必须留下 unsupported，整卡不进池。
        """
        # 检索：数量自由 + 卡名筛选
        # 第一百批起：落 `search_bench:all:…:name_has:rotom`（**不压成固定数**）
        r = self._tags("You may search your deck for any number of Pok\u00e9mon "
                       "that have \u201cRotom\u201d in their name and put them "
                       "onto your Bench. Then, shuffle your deck.")
        self.assertEqual(r["tags"], ["search_bench:all:name_has:rotom"],
                         "应为 `all` + 卡名筛选，不得产出固定数量")
        self.assertEqual(r["unsupported"], [], "该形态已完整建模")

        # 附着 / 移动 / 弃牌：任意数量子句不得被静默丢弃
        for text in (
            "Before doing damage, you may attach any number of Basic {W} "
            "Energy cards from your hand to this Pok\u00e9mon.",
            "You may move any number of damage counters on your opponent\u2019s "
            "Pok\u00e9mon to their other Pok\u00e9mon in any way you like.",
            "You may discard any number of Supporter cards that have "
            "\u201cTeam Rocket\u201d in their name from your hand, and this "
            "attack does 60 damage for each card you discarded in this way.",
        ):
            r = self._tags(text)
            self.assertTrue(r["unsupported"],
                            f"任意数量子句未被登记：{text[:40]}")

    def test_unmappable_condition_not_emitted_as_flat_bonus(self):
        """条件无法表达时**不得**退化成无条件加伤（凭空加伤 = 静默算错）

        ⚠ 「本回合被治疗 / 本回合换入」自 2026-10-28 起**已建模**（回合记账），
        这里改用仍不可表达的「本回合打出过某张卡」形态。
        """
        r = self._tags("If you played a Supporter card from your hand during "
                       "this turn, this attack does 60 more damage.")
        self.assertEqual(r["tags"], [], "条件未知却产出了加伤 tag")
        self.assertTrue(r["unsupported"])

    def test_secondary_action_on_stadium_is_modeled(self):
        """「Then, discard that Stadium.」自 2026-10-28 起可精确执行

        旧行为：引擎没有弃场地动作 → 只映射加伤会把这段效果静默丢掉
        （Chi-Yu / Mamoswine 实测）。现在产出 `discard_stadium`，两段都执行。
        """
        r = self._tags("If a Stadium is in play, this attack does 60 more "
                       "damage. Then, discard that Stadium.")
        self.assertEqual(sorted(r["tags"]),
                         ["discard_stadium", "dmg_plus:60:if:stadium_in_play"])
        self.assertEqual(r["unsupported"], [], "两段都已建模 → 不得再登记未建模")

    def test_condition_mappable_to_engine_whitelist(self):
        """条件在引擎白名单内时才映射为 `dmg_plus:N:if:<cond>`"""
        r = self._tags("If your opponent has any Benched Pok\u00e9mon, this "
                       "attack does 30 more damage.")
        self.assertIn("dmg_plus:30:if:opp_has_bench", r["tags"])
        self.assertEqual(r["unsupported"], [])

    # ---- 第四批（2026-10-26）：高频条件 ----
    # ---- 第五批（2026-10-27）：其余「当下局面可直读」的条件 ----
    def test_fifth_batch_conditions(self):
        """第五批条件：场地 / 道具 / 手牌 / 奖赏 / 阶段 / 能量色 / 抗性 / 后备名字"""
        cases = [
            ("If a Stadium is in play, this attack does 60 more damage.",
             "dmg_plus:60:if:stadium_in_play"),
            ("If you have a Stadium in play, this attack does 50 more damage.",
             "dmg_plus:50:if:stadium_in_play"),
            ("If this Pok\u00e9mon has a Pok\u00e9mon Tool attached, this attack "
             "does 40 more damage.", "dmg_plus:40:if:own_active_has_tool"),
            ("If this Pok\u00e9mon has no damage counters on it, this attack does "
             "120 more damage.", "dmg_plus:120:if:own_active_full_hp"),
            ("If this Pok\u00e9mon is Poisoned, this attack does 130 more damage.",
             "dmg_plus:130:if:own_active_status:poison"),
            ("If your opponent\u2019s Active Pok\u00e9mon is affected by a Special "
             "Condition, this attack does 120 more damage.",
             "dmg_plus:120:if:opp_active_has_status"),
            ("If your Benched Pok\u00e9mon have any damage counters on them, this "
             "attack does 60 more damage.", "dmg_plus:60:if:own_bench_has_damage"),
            ("If you have the same number of cards in your hand as your opponent, "
             "this attack does 90 more damage.",
             "dmg_plus:90:if:own_hand_eq_opp_hand"),
            ("If this Pok\u00e9mon and your opponent\u2019s Active Pok\u00e9mon "
             "have the same amount of Energy attached, this attack does 120 more "
             "damage.", "dmg_plus:120:if:own_opp_active_energy_equal"),
            ("If your opponent has 3 or fewer cards in their hand, this attack does "
             "120 more damage.", "dmg_plus:120:if:opp_hand_at_most:3"),
            ("If your opponent has 3 or fewer Prize cards remaining, this attack "
             "does 90 more damage.", "dmg_plus:90:if:opp_prizes_at_most:3"),
            ("If you have exactly 6 Prize cards remaining, this attack does 200 "
             "more damage.", "dmg_plus:200:if:own_prizes_exactly:6"),
            ("If your opponent\u2019s Active Pok\u00e9mon is a Stage 2 Pok\u00e9mon, "
             "this attack does 140 more damage.",
             "dmg_plus:140:if:opp_active_is_stage:2"),
            ("If this Pok\u00e9mon\u2019s remaining HP is 50 or less, this attack "
             "does 170 more damage.", "dmg_plus:170:if:own_active_hp_at_most:50"),
            ("If this Pok\u00e9mon has 2 or more {G} Energy attached, this attack "
             "does 120 more damage.",
             "dmg_plus:120:if:own_active_energy_type_at_least:grass:2"),
            ("If you have at least 4 {L} Energy in play, this attack does 90 more "
             "damage.", "dmg_plus:90:if:own_all_energy_type_at_least:lightning:4"),
            ("If your opponent\u2019s Active Pok\u00e9mon has {F} Resistance, this "
             "attack does 50 more damage.",
             "dmg_plus:50:if:opp_active_resistance:fighting"),
            ("If Illumise is on your Bench, this attack does 60 more damage.",
             "dmg_plus:60:if:own_bench_name:illumise"),
            ("If a Pok\u00e9mon that has \"Nidoking\" in its name is on your "
             "Bench, this attack does 120 more damage.",
             "dmg_plus:120:if:own_bench_name:nidoking"),
        ]
        for text, want in cases:
            with self.subTest(text=text[:44]):
                r = self._tags(text)
                self.assertEqual(r["tags"], [want])
                self.assertEqual(r["unsupported"], [])

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
        # ⚠ 2026-10-27 起「场地在场上」「有道具附着」已进引擎词表（第五批），
        #   从拒绝清单移到 `test_fifth_batch_conditions` 的正向用例。
        # ⚠ 2026-10-29（第十九批）起「额外能量（费用之外）」也已精确建模：
        #   引擎按「总能量 − 本次招式费用」求值 → 从拒绝清单移出。
        # ⚠ 2026-11-05（第九十三批）起「{ex} 或 {V}」已**精确建模**：
        #   引擎新增独立的 `opp_active_is_ex_or_v` 条件（与 `opp_active_is_ex`
        #   分开命名，将来加 V 支持只改一处）→ 从拒绝清单移到正向用例
        #   `test_batch93_94_parse.TestBatch93Conditions.test_ex_or_v_bonus`。
        texts = [
            # 后备与本方战斗位「同属性」：引擎没有该关系判定 → 仍必须拒绝
            "If your opponent's Benched Pokémon are the same type as your "
            "Active Pokémon, this attack does 30 more damage.",
        ]
        for text in texts:
            with self.subTest(text=text[:40]):
                r = self._tags(text)
                self.assertEqual(r["tags"], [], "条件不可表达却产出了 tag（静默加强）")
                self.assertTrue(r["unsupported"])

    def test_extra_energy_condition_exact(self):
        """第十九批：「额外能量（费用之外）」→ 计数按费用扣除后的余量"""
        r = self._tags(
            "If this Pok\u00e9mon has at least 2 extra Energy attached (in "
            "addition to this attack\u2019s cost), this attack does 90 more damage.")
        self.assertEqual(
            r["tags"], ["dmg_plus:90:if:own_active_extra_energy_at_least:2"])
        self.assertEqual(r["unsupported"], [])

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

    def test_tool_static_modifiers(self):
        """S2 道具：附着型静态修正 → `tool:*`"""
        cases = [
            ("The Pok\u00e9mon this card is attached to gets +100 HP.",
             "tool:hp_plus:100"),
            ("The Retreat Cost of the Pok\u00e9mon this card is attached to is "
             "{C}{C} less.", "tool:retreat_less:2"),
        ]
        for text, tag in cases:
            with self.subTest(tag=tag):
                r = self._tags(text, kind="item", name="T")
                self.assertIn(tag, r["tags"])
                self.assertEqual(r["unsupported"], [])

    def test_tool_with_extra_clause_is_refused(self):
        """⚠ 道具卡面若还有**未建模**的条件/触发从句 → 整张放弃

        反例（真实卡）Rescue Board：撤退 −1 之外还有「剩余 HP ≤30 时无撤退费用」。
        只映射前半段会把卡放进池并静默丢掉后半段 —— 属"部分实现冒充已实现"。
        ⚠ 2026-10-28 起该条**已建模**（`tool:retreat_less:N:lowhp:M`，引擎按
        `hp_remaining` 判定），故这里改用另一种仍有未建模从句的道具做反例。
        """
        r = self._tags("The Pok\u00e9mon this card is attached to gets +30 HP. "
                       "When you play this card, your opponent reveals their "
                       "hand.", kind="item", name="X")
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])

    def test_rescue_board_now_modeled(self):
        """Rescue Board 的后半段已可执行（引擎按剩余 HP 判定）"""
        r = self._tags("The Retreat Cost of the Pok\u00e9mon this card is attached "
                       "to is {C} less. If that Pok\u00e9mon\u2019s remaining HP is "
                       "30 or less, it has no Retreat Cost.", kind="item", name="RB")
        self.assertEqual(r["tags"], ["tool:retreat_less:1:lowhp:30"])

    def test_tool_damage_reduction_with_qualifier_mapped(self):
        """带对手属性/特性限定的减伤：2026-10-27 起映射为**带限定**的减伤

        关键是不能退化成无条件减伤（那是对所有攻击方都生效，静默加强）——
        限定必须原样进 tag（`from:type:` / `from:ability` / `holder:type:`）。
        """
        r = self._tags("If the Pok\u00e9mon this card is attached to is damaged by an "
                       "attack from your opponent\u2019s {P} Pok\u00e9mon, it takes 60 "
                       "less damage (after applying Weakness and Resistance), and "
                       "discard this card.", kind="item", name="Payapa")
        self.assertIn("tool:dmg_reduce:60:from:type:psychic:discard", r["tags"])

        r = self._tags("The Pok\u00e9mon this card is attached to takes 30 less "
                       "damage from attacks from your opponent\u2019s Pok\u00e9mon "
                       "that have an Ability (after applying Weakness and Resistance).",
                       kind="item", name="Sacred Charm")
        self.assertIn("tool:dmg_reduce:30:from:ability", r["tags"])

    def test_stadium_static_modifiers(self):
        """S3 场地：全场静态修正 → `stadium:*`"""
        cases = [
            ("Each Basic Pok\u00e9mon in play (both yours and your opponent\u2019s) "
             "gets +30 HP.", "stadium:hp_plus:30:stage:0"),
            ("Each Stage 2 Pok\u00e9mon in play (both yours and your opponent\u2019s) "
             "gets -30 HP.", "stadium:hp_plus:-30:stage:2"),
            ("{M} Pok\u00e9mon (both yours and your opponent\u2019s) take 30 less "
             "damage from attacks from the opponent\u2019s Pok\u00e9mon (after "
             "applying Weakness and Resistance).",
             "stadium:dmg_reduce:30:type:metal"),
        ]
        for text, tag in cases:
            with self.subTest(tag=tag):
                r = self._tags(text, kind="card", name="S")
                self.assertIn(tag, r["tags"])
                self.assertEqual(r["unsupported"], [])

    def test_stadium_faction_reduction(self):
        """阵营限定场地（2026-10-28）：按**卡名前缀**精确表达，不再整卡拒绝"""
        r = self._tags("Steven\u2019s Pok\u00e9mon (both yours and your "
                       "opponent\u2019s) take 30 less damage from attacks from the "
                       "opponent\u2019s Pok\u00e9mon.", kind="card", name="Granite")
        self.assertEqual(r["tags"], ["stadium:dmg_reduce:30:faction:stevens"])
        self.assertEqual(r["unsupported"], [])

    def test_stadium_faction_unrecognized_refused(self):
        """未知阵营写法仍拒绝（不得猜成无条件修正）"""
        r = self._tags("Cynthia\u2019s Pok\u00e9mon (both yours and your "
                       "opponent\u2019s) have no Retreat Cost.",
                       kind="card", name="X")
        # 已知阵营集合外的写法 → 不产出
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])

    # ---- S3 第四步：需要代价/目标的「每回合一次」（2026-10-27） ----
    def test_stadium_use_switch_typed(self):
        """Surfing Beach：战斗位 ↔ 后备**同属性**换位（要选目标）"""
        r = self._tags("Once during each player\u2019s turn, that player may switch "
                       "their Active {W} Pok\u00e9mon with 1 of their Benched {W} "
                       "Pok\u00e9mon.", kind="stadium", name="Surfing Beach")
        self.assertEqual(r["tags"], ["stadium:use:switch_typed:type:water"])
        self.assertEqual(r["unsupported"], [])

    def test_stadium_use_draw_to_count_with_cost(self):
        """Mystery Garden：弃 1 张手牌能量 → 抽到「手牌数 = 该属性在场数」"""
        r = self._tags(
            "Once during each player\u2019s turn, that player may discard an "
            "Energy card from their hand in order to draw cards until they "
            "have as many cards in their hand as they have {P} Pok\u00e9mon "
            "in play.", kind="stadium", name="Mystery Garden")
        self.assertEqual(
            r["tags"],
            ["stadium:use:draw_to_count:1:type:psychic:cost:discard_energy"])

    def test_stadium_use_search_basic_bench_ends_turn(self):
        """Lumiose City：检索基础宝可梦直铺后备 + **回合结束**"""
        r = self._tags(
            "Once during each player\u2019s turn, that player may search their "
            "deck for a Basic Pok\u00e9mon and put it onto their Bench. Then, "
            "that player shuffles their deck. If a player searches their deck "
            "in this way, their turn ends.", kind="stadium", name="Lumiose")
        self.assertEqual(
            r["tags"], ["stadium:use:search_basic_bench:1:end:1"])

    def test_stadium_use_draw_requires_supporter_name(self):
        """Team Rocket's Factory：只认名字含 "Team Rocket" 的支援者"""
        r = self._tags(
            "Once during each player\u2019s turn, if they played a Supporter "
            "card that has \"Team Rocket\" in its name from their hand this "
            "turn, they may draw 2 cards.", kind="stadium", name="TR Factory")
        self.assertEqual(
            r["tags"],
            ["stadium:use:draw:2:require:supporter_name:name:teamrocket"])

    def test_stadium_use_search_by_name(self):
        """Spikemuth Gym：检索专名指定的宝可梦入手（专名是**检索目标**，可表达）"""
        r = self._tags(
            "Once during each player\u2019s turn, that player may search their "
            "deck for a Marnie\u2019s Pok\u00e9mon, reveal it, and put it into "
            "their hand. Then, that player shuffles their deck.",
            kind="stadium", name="Spikemuth")
        self.assertEqual(r["tags"], ["stadium:use:search_name:1:name:marnie"])

    def test_stadium_kind_never_falls_back_to_generic_parse(self):
        """**硬分流**：场地卡未识别时必须落未建模，绝不能退回通用解析

        两个实测过的静默错映射（局面正常、无报错，只有对账才发现）：
          · Dizzying Valley「Confused Pokémon don't recover from that condition…」
            被读成 `confuse`（凭空让某只宝可梦混乱）。

        2026-10-28 第十一批：Grand Tree 已由 `stadium:use:evolve_from_deck` 精确建模，
        故不再属于本负向清单（它仍**不得**落成 `candy` —— 见
        `test_flygon_leafeon_stadium` 的正向用例）。
        """
        cases = [
            ("Confused Pok\u00e9mon (both yours and your opponent\u2019s) "
             "don\u2019t recover from that Special Condition when they evolve "
             "or devolve.", "Dizzying Valley", "confuse"),
        ]
        for text, name, bad in cases:
            with self.subTest(name=name):
                r = self._tags(text, kind="stadium", name=name)
                # 第一百零六批起 Dizzying Valley 已精确建模为
                # `stadium:keep_confuse`（进化不解除混乱）—— 它**不再是**通用
                # 解析的兜底结果，而是场地专属 kind。
                self.assertEqual(r["tags"], ["stadium:keep_confuse"])
                self.assertFalse(any(t == bad for t in r["tags"]),
                                 f"{name} 不得落成裸 `{bad}` 状态")
                self.assertEqual(r["unsupported"], [])

    def test_stadium_lumiose_needs_turn_ends_clause(self):
        """负向守卫：少了「their turn ends」就不是 Lumiose 形态（不得硬套 end）"""
        r = self._tags(
            "Once during each player\u2019s turn, that player may search their "
            "deck for a Basic Pok\u00e9mon and put it onto their Bench. Then, "
            "that player shuffles their deck.", kind="stadium", name="X")
        self.assertEqual(r["tags"], [])

    def test_tool_faction_qualifier_now_qualified(self):
        """按**卡名阵营**限定的（Team Rocket's / Hop's）：第一百三十四批起产带限定 tag

        旧口径是「引擎无卡名归属元数据 → 必须拒收」。本批给引擎补了
        `cards._tool_hp_plus()`（比对持有者卡名前缀），因此现在应当产
        `tool:hp_plus:N:holder:name:<k>`；守的性质不变 —— **不得**无条件生效。
        """
        r = self._tags("The Team Rocket\u2019s Pok\u00e9mon this card is attached to "
                       "gets +30 HP.", kind="item", name="TR Tool")
        self.assertEqual(r["tags"], ["tool:hp_plus:30:holder:name:teamrocket"])
        self.assertNotIn("tool:hp_plus:30", r["tags"])

    def test_static_modifiers_self_scope(self):
        """S1 静态修正层：常驻自身修正 → `passive:*`（与时限护盾严格区分）"""
        cases = [
            ("This Pok\u00e9mon takes 30 less damage from attacks (after applying "
             "Weakness and Resistance).", "passive:dmg_reduce:30"),
            ("This Pok\u00e9mon has no Weakness.", "passive:no_weakness"),
            ("This Pok\u00e9mon has no Retreat Cost.", "passive:no_retreat"),
            ("This Pok\u00e9mon gets +40 HP.", "passive:hp_plus:40"),
        ]
        for text, tag in cases:
            with self.subTest(tag=tag):
                r = self._tags(text, kind="ability")
                self.assertIn(tag, r["tags"])
                self.assertEqual(r["unsupported"], [])

    def test_temporary_reduction_stays_shield(self):
        """带「对手的下个回合」时限制的减伤必须仍是 `shield:reduce`（不是常驻）"""
        r = self._tags("During your opponent\u2019s next turn, this Pok\u00e9mon takes "
                       "50 less damage from attacks (after applying Weakness and "
                       "Resistance).", kind="ability")
        self.assertIn("shield:reduce:50", r["tags"])
        self.assertNotIn("passive:dmg_reduce:50", r["tags"])

    def test_field_wide_static_still_refused(self):
        """全体/带条件的静态修正**不许**当自身常驻（会变成全队白嫖）

        2026-11-04 第五十六批补充：撤退费用归零已有**全队**实现
        （``fx:retreat_free:``），因此那两句现在会被**正确认领** —— 判定不变的是
        「不得落成 ``passive:no_retreat``」：`passive:` 只作用于持有者自己，
        用它顶替全队效果 = 效果静默缩水（卡照样能打、测试全绿）。
        HP 加成目前**没有**全队实现，继续如实未建模。
        """
        texts = [
            "All of your Pok\u00e9mon that have {M} Energy attached have no "
            "Retreat Cost.",
            "Your Basic Pok\u00e9mon in play have no Retreat Cost.",
        ]
        for text in texts:
            with self.subTest(text=text[:40]):
                r = self._tags(text, kind="ability")
                self.assertEqual(
                    [t for t in r["tags"] if t.startswith("passive:")], [],
                    "全体范围被误映射成自身常驻")
        # D4（2026-11-08）：**队伍 +HP 已建模** → `passive:team_hp_plus:N`
        # （此前这里是「如实未建模」。改判原因：引擎新增全队 +HP 的唯一消费点
        #  `_refresh_stadium_hp`，并按卡面「doesn't stack」取 max 不求和。）
        r = self._tags("All of your Pok\u00e9mon in play get +40 HP.",
                       kind="ability")
        self.assertEqual(r["tags"], ["passive:team_hp_plus:40"])
        self.assertEqual(r["unsupported"], [])

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
        # 2026-10-28：卡面写「from and effects of attacks」→ 子 tag 必带 `:effects`
        self.assertIn("coin:heads:shield:prevent_all:effects", r["tags"])
        self.assertNotIn("shield:prevent_all:effects", r["tags"])

    def test_coin_each_tails(self):
        r = self._tags("Your opponent flips a coin for each of their Benched "
                       "Pok\u00e9mon. This attack does 80 damage to your "
                       "opponent\u2019s Active Pok\u00e9mon for each tails.")
        self.assertIn("coin_each:opp_bench:tails:dmg_set:80", r["tags"])

    def test_scaling_opp_bench(self):
        r = self._tags("This attack does 20 more damage for each of your "
                       "opponent\u2019s Benched Pok\u00e9mon.")
        self.assertIn("dmg_plus:20:per:opp_bench", r["tags"])

    def test_scaling_opp_active_energy(self):
        r = self._tags("This attack does 30 more damage for each Energy "
                       "attached to your opponent\u2019s Active Pok\u00e9mon.")
        self.assertIn("dmg_plus:30:per:opp_active_energy", r["tags"])

    def test_scaling_unrecognized_counter_is_honest(self):
        """计数源无法识别 → 如实报 unsupported，不得猜一个计数源出来

        ⚠ 第四十三批：原来的例子是「Ancient card in your discard pile」，
        现已被 **`own_discard_category:ancient`** 精确建模（Roaring Moon），
        故改用仍无法识别的「you placed in this way」（Walking Wake，可选数量）。
        """
        r = self._tags("This attack does 20 more damage for each damage counter "
                       "you placed in this way.")
        self.assertEqual(r["tags"], [])
        self.assertTrue(any("缩放" in u["reason"] or "未识别" in u["reason"]
                            for u in r["unsupported"]), r["unsupported"])

    def test_shield_reduce(self):
        r = self._tags("During your opponent\u2019s next turn, this Pok\u00e9mon "
                       "takes 30 less damage from attacks (after applying "
                       "Weakness and Resistance).")
        self.assertIn("shield:reduce:30", r["tags"])

    def test_passive_bench_immune(self):
        """Tera 规则写法只写 prevent all damage → **只挡伤害**（2026-10-28 拆分）"""
        r = self._tags("As long as this Pok\u00e9mon is on your Bench, prevent "
                       "all damage done to this Pok\u00e9mon by attacks (both "
                       "yours and your opponent\u2019s).", kind="ability",
                       name="BenchGuard")
        self.assertIn("passive:bench_immune_dmg", r["tags"])

    def test_passive_bench_immune_with_effects(self):
        """写明「from and effects of attacks」→ 伤害+效果一起挡"""
        r = self._tags("As long as this Pok\u00e9mon is on your Bench, prevent "
                       "all damage from and effects of attacks from your "
                       "opponent\u2019s Pok\u00e9mon done to this Pok\u00e9mon.",
                       kind="ability", name="BenchGuard2")
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
        """条件性反伤（抛币/条件从句）不得产出**无条件** `self:N`

        否则从「两面都是反面才自伤」变成「必定自伤」，属实质性加强。
        2026-10-29 第十八批起改为精确的 coin 分支（`coin_all:…:tails:self:N`）。
        """
        r = self._tags("Flip 2 coins. If both of them are tails, this "
                       "Pok\u00e9mon also does 30 damage to itself.")
        self.assertNotIn("self:30", r["tags"])
        self.assertIn("coin_all:2:tails:self:30", r["tags"])
        self.assertEqual(r["unsupported"], [])

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
        card your opponent has taken」）。2026-10-29 第十七批起改为精确缩放：
        `snipe:opp_bench:10:all:per:opp_prizes_taken`（不再整条拒绝）。
        """
        r = self._tags("This attack also does 10 damage to each of your "
                       "opponent\u2019s Benched Pok\u00e9mon for each Prize "
                       "card your opponent has taken. "
                       "(Don\u2019t apply Weakness and Resistance for "
                       "Benched Pok\u00e9mon.)")
        self.assertEqual(r["tags"],
                         ["snipe:opp_bench:10:all:per:opp_prizes_taken"])
        self.assertEqual(r["unsupported"], [])

    def test_conditional_bench_all_not_emitted(self):
        """句首带条件的全体后备伤害不得产出无条件 tag（Ting-Lu）

        2026-10-29 第十七批：条件可精确表达时落 `:if:<cond>`，仍然**不是**无条件。
        """
        r = self._tags("If a Stadium is in play, this attack also does 30 "
                       "damage to each of your opponent\u2019s Benched "
                       "Pok\u00e9mon, and discard that Stadium.")
        self.assertNotIn("spread:all:30", r["tags"])
        self.assertIn("snipe:opp_bench:30:all:if:stadium_in_play", r["tags"])
        self.assertEqual(r["unsupported"], [])

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

    def test_ex_subset_now_modeled_via_target_ex(self):
        """带 `{ex}` 限定的目标：引擎有 `:target:ex`，第九十八批起可精确表达

        旧注释说「snipe 只能表达任意 1 只 / 全部」——**已过期**：第四十一批起
        `_apply_snipe` 就支持 `:target:ex` 目标筛选（先筛后选）。
        本卡池无 Pokémon V，故「{ex} or {V}」与「{ex}」等价（见 test_batch98
        的 `TestNoVInDataset` 守卫）。
        """
        r = self._tags("This attack does 60 damage to 1 of your opponent\u2019s "
                       "Benched Pok\u00e9mon {ex} or Benched Pok\u00e9mon {V}. "
                       "(Don\u2019t apply Weakness and Resistance for "
                       "Benched Pok\u00e9mon.)")
        self.assertEqual(r["tags"], ["snipe:opp_bench:60:1:target:ex"])
        self.assertEqual(r["unsupported"], [])

    def test_ability_subset_still_unmodeled(self):
        """反例：`that has an Ability` 这类子集**仍**不可表达（必须拒收）"""
        r = self._tags("This attack does 60 damage to 1 of your opponent\u2019s "
                       "Benched Pok\u00e9mon that has an Ability.")
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
        第二十批（2026-10-29）：指示物**不吃弱点/抗性** → 落 `counters:…:scope:opp`，
        **不得**落 `snipe:opp`（snipe 打战斗位会吃弱抗，属静默多算）。
        """
        one = self._tags("Put 2 damage counters on 1 of your opponent\u2019s "
                         "Pok\u00e9mon. (Don\u2019t apply Weakness and "
                         "Resistance for Benched Pok\u00e9mon.)")
        self.assertIn("counters:2:scope:opp:1", one["tags"])
        self.assertNotIn("snipe:opp:20:1", one["tags"], "指示物不得走伤害字段")
        each = self._tags("Put 2 damage counters on each of your opponent\u2019s "
                          "Pok\u00e9mon.")
        self.assertIn("counters:2:scope:opp:all", each["tags"])
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
        # 第一百三十五批（S1c）改判：含战斗位的「任意分配」现在可由决策分解
        # 精确表达（`choose:counters_any:N`），因此**不再**是「无 tag」。
        # 守的性质不变：**不得**落成只覆盖后备的 `spread_free`（那会漏掉战斗位）。
        self.assertEqual(anyp["tags"], ["choose:counters_any:4:opp_any"])
        self.assertFalse(any(t.startswith("spread_free") for t in anyp["tags"]))
        # 已可精确表达 → 不再登记 unsupported（旧断言「必须未建模」随改判失效）
        self.assertEqual(anyp["unsupported"], [])


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

        ⚠ 2026-10-28 更新（第十五批）：`:target:bench_type:<c>` 落地后已可精确
        表达（Shaymin「Send Flowers」）——现在**正向**要求 `attach:…:target:
        bench_type:grass:anyenergy`；关键回归点仍是绝不能出现 `search_pokemon`
        （拿错东西）。
        """
        r = self._tags("Search your deck for an Energy card and attach it to "
                       "1 of your Benched {G} Pok\u00e9mon. Then, shuffle "
                       "your deck.")
        self.assertNotIn("search_pokemon:1", r["tags"])
        self.assertNotIn("search_energy:1", r["tags"])
        self.assertIn("attach:1:deck:target:bench_type:grass:anyenergy",
                      r["tags"])
        self.assertEqual(r["unsupported"], [])

    def test_search_target_pokemon_still_works(self):
        """类别判定仍归 pokemon（不被 energy 抢走）；抗性限定已可精确表达"""
        r = self._tags("Search your deck for up to 2 Pok\u00e9mon with {F} "
                       "Resistance, reveal them, and put them into your hand. "
                       "Then, shuffle your deck.")
        self.assertNotIn("search_energy:2", r["tags"])
        self.assertEqual(r["tags"], ["search_pokemon:2:resistance:fighting"],
                         "B2 剩余（2026-10-28）：抗性筛选可精确表达")

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
        # B3（2026-10-28）：组合写法映射成 **combo**（宝可梦 + 基本能量），
        # 比旧的 `any` 更精确 —— `any` 还会取到训练家卡，超出卡面范围。
        r = self._tags("Put up to 5 in any combination of Pok\u00e9mon and "
                       "Basic Energy cards from your discard pile into your "
                       "hand.")
        self.assertIn("recover:5:combo:pokemon+basic_energy", r["tags"])
        self.assertEqual(r["unsupported"], [])

    def test_pokemon_or_basic_energy(self):
        r = self._tags("Put a Pok\u00e9mon or a Basic Energy card from your "
                       "discard pile into your hand.")
        self.assertIn("recover:1:any", r["tags"])

    def test_subset_qualifier_mapped_as_combo(self):
        """子集限定（「that don't have a Rule Box」/「{F} Pokémon」）→ combo

        2026-10-28（B3）：引擎新增 `recover:N:combo:<a>+<b>[:type:<c>]`，
        可以**精确**表达「无 Rule Box 的宝可梦 + 基本能量」这类组合 ——
        但**绝不能**退化成 `any`（会取到 ex）。
        """
        r = self._tags("Put up to 3 in any combination of Pok\u00e9mon that "
                       "don\u2019t have a Rule Box and Basic Energy cards "
                       "from your discard pile into your hand.")
        self.assertEqual(r["tags"],
                         ["recover:3:combo:no_rule_pokemon+basic_energy"])
        self.assertEqual(r["unsupported"], [])

    def test_typed_subset_combo(self):
        r = self._tags("Put up to 4 in any combination of {F} Pok\u00e9mon and "
                       "Basic {F} Energy cards from your discard pile into your "
                       "hand.")
        self.assertEqual(
            r["tags"],
            ["recover:4:combo:pokemon+basic_energy:type:fighting"])
        self.assertEqual(r["unsupported"], [])

    def test_unknown_count_not_guessed(self):
        """回收数量判不出（抛币决定）→ 用 `recover:heads:N`，绝不兜底成 1"""
        r = self._tags("Flip 3 coins. Put a number of cards up to the number "
                       "of heads from your discard pile into your hand.")
        self.assertEqual(r["tags"], ["recover:heads:3"])
        self.assertEqual(r["unsupported"], [])


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
        # 2026-10-28 第十批：进化触发特性必须包裹成 `evolve_act:`（只在进化时执行），
        # 否则引擎会把它当「每回合一次」的 field 特性（静默加强）。
        self.assertIn("evolve_act:draw:2", r["tags"])
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
        self.assertIn("evolve_act:evolve_from_deck", r["tags"])


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

        2026-10-29（第十九批）：引擎按「总能量 − 本次招式费用」求值 → 精确建模，
        不再落成「总数 ≥ N」的近似（那会提前触发）。
        """
        r = self._tags("If this Pok\u00e9mon has at least 2 extra Energy "
                       "attached (in addition to this attack\u2019s cost), "
                       "this attack does 100 more damage.")
        self.assertEqual(
            r["tags"], ["dmg_plus:100:if:own_active_extra_energy_at_least:2"])
        self.assertEqual(r["unsupported"], [])

    def test_special_energy_condition_is_modeled(self):
        """「any Special Energy attached」—— 2026-10-28 起由特殊能量子系统精确判定"""
        r = self._tags("If this Pok\u00e9mon has any Special Energy attached, "
                       "this attack does 100 more damage.")
        self.assertEqual(r["tags"], ["dmg_plus:100:if:own_active_special_energy"])
        self.assertEqual(r["unsupported"], [])


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


class TestHealScopeParsing(unittest.TestCase):
    """回复「范围」解析（2026-10-27）

    历史缺陷：只要文本含 ``heal (\\d+) damage`` 就产出 ``heal:N``，
    而引擎的 ``heal:N`` 恒指**施术者自身** → 「回己方全部」被压成「只回一只」。
    池内实测 5 处（Leafeon ex / Ethan's Ho-Oh ex / Leavanny / Meloetta / Floragato）。
    """

    @staticmethod
    def _tags(text, kind="attack", name="X"):
        return E.to_engine_tags([E.parse_rule(text, kind=kind, name=name)])

    def test_each_of_your_pokemon(self):
        r = self._tags("Heal 50 damage from each of your Pok\u00e9mon.")
        self.assertIn("heal:50:all", r["tags"])

    def test_each_of_your_benched(self):
        r = self._tags("Heal 100 damage from each of your Benched Pok\u00e9mon.")
        self.assertIn("heal:100:bench", r["tags"])

    def test_each_of_your_basic(self):
        r = self._tags("Heal 100 damage from each of your Basic Pok\u00e9mon.")
        self.assertIn("heal:100:basic", r["tags"])

    def test_one_of_your_pokemon_is_choose(self):
        r = self._tags("Heal 30 damage from 1 of your Pok\u00e9mon.", "item")
        self.assertIn("heal:30:choose", r["tags"])

    def test_benched_one_with_type(self):
        r = self._tags("Heal 120 damage from 1 of your Benched {P} Pok\u00e9mon.")
        self.assertIn("heal:120:bench_one:type:psychic", r["tags"])

    def test_this_pokemon_has_no_scope(self):
        """`this Pokémon` 必须保持无 scope（引擎回施术者自身）——不能被当成 choose"""
        r = self._tags("Heal 30 damage from this Pok\u00e9mon.")
        self.assertEqual(r["tags"], ["heal:30"])

    def test_active_pokemon_uses_active_scope(self):
        r = self._tags("Heal 70 damage from your Active Pok\u00e9mon.",
                       "supporter")
        self.assertIn("heal:70:active", r["tags"])

    def test_active_with_type_symbol(self):
        r = self._tags("Heal 60 damage from your Active {N} Pok\u00e9mon.",
                       "item")
        self.assertIn("heal:60:active:type:dragon", r["tags"])

    def test_conditional_heal_modeled(self):
        """第六十二批：「that has 3 or more Energy」= **目标前提**（Jumbo Ice Cream）

        旧断言要求拒收 —— 当时 heal tag 没有「前提条件」段。现在落
        `heal:80:active:need:<cond>`：前提不满足时卡不进合法动作表（而不是照常回血）。
        """
        r = self._tags("Heal 80 damage from your Active Pok\u00e9mon that has "
                       "3 or more Energy attached.", "item")
        self.assertEqual(r["tags"],
                         ["heal:80:active:need:own_active_energy_at_least:3"])
        self.assertFalse(r["unsupported"])

    def test_dependent_followup_modeled(self):
        """Super Potion（2026-10-28 第十二批）：回复 + 「in this way」弃能已可精确表达

        训练家只有单 tag，两个动作合并成 `heal:60:choose:discard`；`:discard`
        隐含「仅当真的回复过才弃能」。旧实现按「无法只做一半」拒绝 —— 现改为
        **正向**守卫，防止退回未建模。
        """
        r = self._tags("Heal 60 damage from 1 of your Pok\u00e9mon. If you "
                       "healed any damage in this way, discard an Energy from "
                       "that Pok\u00e9mon.", "item")
        self.assertEqual(r["tags"], ["heal:60:choose:discard"])
        self.assertEqual(r["unsupported"], [])

    def test_coin_branch_heal_not_unconditional(self):
        """Floragato：抛币分支内的回复**不得退化成无条件 heal**

        2026-10-29 第十八批：回复与加伤合并进**同一条** coin tag（共享一次抛币）。
        """
        r = self._tags("Flip a coin. If heads, this attack does 30 more "
                       "damage, and heal 30 damage from this Pok\u00e9mon.")
        self.assertEqual([t for t in r["tags"] if t.startswith("heal")], [],
                         "不得产出裸 heal（那是无条件回复）")
        self.assertEqual(r["tags"], ["coin:heads:dmg_plus:30|heal:30"])
        self.assertEqual(r["unsupported"], [])


class TestDrawCompletenessGuard(unittest.TestCase):
    """抽牌「完整性」解析守卫（2026-10-27）

    此前只要出现 "draw a card" 就产出 ``draw:1``，完全不看后面的 ``for each``：
    「Draw a card for each of your Ancient Pokémon in play」→ ``draw:1``。
    不报错、局面上"能跑"，只是少抽。
    """

    @staticmethod
    def _tags(text, kind="item", name="X"):
        return E.to_engine_tags([E.parse_rule(text, kind=kind, name=name)])

    def test_plain_draw_ok(self):
        self.assertIn("draw:3", self._tags("Draw 3 cards.", "supporter")["tags"])

    def test_for_each_mapped_per_counter(self):
        """`for each` 抽牌 → `draw:1:per:<counter>`（B3 · 2026-10-28）

        旧行为一律拒绝；现在计数源是**当下可读出**的（Ancient 计数 / 对手后备数），
        可以精确表达。未知计数源仍拒绝。
        """
        r = self._tags("Draw a card for each of your Ancient Pok\u00e9mon "
                       "in play.")
        self.assertEqual(r["tags"], ["draw:1:per:own_ancient"])
        self.assertEqual(r["unsupported"], [])

    def test_for_each_unknown_counter_refused(self):
        r = self._tags("Draw a card for each of your {G} Pok\u00e9mon in play.")
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])

    def test_each_player_draw(self):
        r = self._tags("Once during your turn, you may have each player draw "
                       "a card.", "ability")
        self.assertEqual(r["tags"], ["draw_both:1"])

    def test_conditional_more_modeled(self):
        """第五十八批：条件**追加**抽牌已可精确表达（Billy & O'Nare）

        旧断言要求拒收 —— 当时引擎没有「先抽 base、满足条件再抽 more」的单 tag；
        现在走 `draw_cond:<keep|shuffle>:<base>:<plus|set>:<n>:if:<cond>`。
        ⚠ `more` 与 `instead` 是**相反**方向（追加 vs 换值），所以这里逐字锁定。
        """
        r = self._tags("Draw 2 cards. Then, if you have 10 or more cards in "
                       "your hand, draw 2 more cards.", "supporter")
        self.assertEqual(r["tags"],
                         ["draw_cond:keep:2:plus:2:if:own_hand_at_least:10"])
        self.assertFalse(r["unsupported"])

    def test_opponent_split_draw_modeled(self):
        """第五十八批：双方洗回后**各抽不同张数**（Unfair Stamp）

        ⚠ 与 `draw_both:N`（双方各抽同数）差一个量纲 —— 旧实现因为「无同数形态」
        只能拒收；现在落 `shuffle_draw_split:<mine>:<theirs>`。
        """
        r = self._tags("Each player shuffles their hand into their deck. "
                       "Then, you draw 5 cards, and your opponent draws 2 "
                       "cards.")
        self.assertEqual(r["tags"], ["shuffle_draw_split:5:2"])
        self.assertFalse(r["unsupported"])

    def test_coin_branch_draw_refused(self):
        r = self._tags("Flip a coin. If heads, draw 2 cards.")
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])


class TestActionOrdering(unittest.TestCase):
    """多动作卡的**执行顺序**必须与卡面词序一致（2026-10-27）

    Carmine「Discard your hand and draw 5 cards.」原先产出
    ``[draw:5, discard:all]`` → 引擎先抽 5 张再把整手牌（含刚抽的）弃光，
    效果从「弃光后重抽 5」变成「直接空手」。
    """

    @staticmethod
    def _tags(text, kind="supporter", name="X"):
        return E.to_engine_tags([E.parse_rule(text, kind=kind, name=name)])

    def test_discard_before_draw(self):
        tags = self._tags("Discard your hand and draw 5 cards.")["tags"]
        # 第五十一批**决策变更**：训练家只认**一个**卡级 tag，原来拆成
        # `discard:all` + `draw:5` 两条会被建池整卡拒收（且不记录原因）。
        # 现在合并成引擎早有的 `research:N`（弃全部手牌再抽 N）。
        self.assertEqual(tags, ["research:5"])

    def test_draw_before_discard_when_text_says_so(self):
        tags = self._tags("Draw 2 cards. Then, discard 2 cards from your "
                          "hand.")["tags"]
        self.assertEqual(tags[0], "draw:2")
        self.assertIn("discard:2", tags)

    def test_status_before_draw(self):
        tags = self._tags("Your opponent\u2019s Active Pok\u00e9mon is now "
                          "Burned. Draw 2 cards.", "attack")["tags"]
        self.assertEqual(tags[0], "burn")

    def test_delayed_discard_now_modeled(self):
        """Amarys：「回合末才弃手牌」—— **改判**（S2a，2026-11-08）

        原判据：引擎没有延迟队列 → 整卡拒收（绝不能在**抽牌前**就弃手牌，
        那会让「抽 4 张再判 5 张」变成「先弃光再抽」，两条线强度完全不同）。
        S2a 落地后 `queue:turn_end:<tag>` 可以精确表达，因此：
          · tag 必须是**复合**形态（先 draw、后 queue），顺序不能反；
          · 弃手牌必须带 `if:own_hand_at_least:5`（条件在**结算时**求值）。
        断言「不得产出立即执行的 `discard:all`」这一条仍然保留 —— 那是这条用例
        真正要钉死的东西（历史缺陷：把延迟句当立即句）。
        """
        r = self._tags("Draw 4 cards. At the end of this turn, if you have 5 "
                       "or more cards in your hand, discard your hand.")
        self.assertNotIn("discard:all", r["tags"])
        self.assertEqual(r["unsupported"], [])
        self.assertEqual(
            r["tags"],
            ["draw:4|queue:turn_end:discard_hand:all:if:own_hand_at_least:5"])


class TestReactiveDamageSplit(unittest.TestCase):
    """「受击反应」语义分流（2026-10-27）

    卡面：「If this Pokémon is in the Active Spot and is damaged by an attack from
    your opponent's Pokémon (even if this Pokémon is Knocked Out), …」——
    效果落在**攻击方**身上，不是「发动特性让对手陷入状态」。

    历史缺陷：只按「句子含状态词」判定 → Heatran / Numel / Scolipede 产出了
    **无条件** `burn`/`poison`，一旦特性状态 tag 放进白名单，就会在**没人挨打**
    的时候凭空给对手上状态。
    """

    P = ("If this Pok\u00e9mon is in the Active Spot and is damaged by an attack "
         "from your opponent's Pok\u00e9mon (even if this Pok\u00e9mon is "
         "Knocked Out), ")

    @classmethod
    def _tags(cls, text, kind="ability", name="X"):
        return E.to_engine_tags([E.parse_rule(text, kind=kind, name=name)])

    # ---- 正向：三类可表达形态 → on_damaged ----
    def test_status_reaction(self):
        r = self._tags(self.P + "the Attacking Pok\u00e9mon is now Burned.")
        self.assertEqual(r["tags"], ["on_damaged:status:burn"])
        self.assertEqual(r["unsupported"], [])

    def test_counter_reaction(self):
        r = self._tags(self.P + "put 3 damage counters on the Attacking Pok\u00e9mon.")
        self.assertEqual(r["tags"], ["on_damaged:counter:30"])

    def test_discard_energy_reaction(self):
        r = self._tags(self.P + "discard an Energy from the Attacking Pok\u00e9mon.")
        self.assertEqual(r["tags"], ["on_damaged:discard_energy:1"])

    # ---- 反向：不可表达形态必须**如实未建模**，且不得产出状态 tag ----
    def test_no_active_status_leak(self):
        """状态 tag 必须挂在 `on_damaged:status:*` 下，**不能**是裸 `poison`

        （裸 `poison` 在特性侧＝「主动让对手战斗位中毒」，会被每回合无代价发动）
        """
        r = self._tags(self.P + "the Attacking Pok\u00e9mon is now Poisoned.")
        self.assertNotIn("poison", r["tags"], "不得当作主动施加状态")
        self.assertEqual(r["tags"], ["on_damaged:status:poison"])

    def test_scaled_counters_per_energy(self):
        """「… for each {M} Energy attached to this Pokémon」→ `counter_per`（第二十九批）

        数值随**持有者**的该色能量数变化 —— 引擎按 `energy_of_type` 逐点精确计算，
        绝不能压成固定值。
        """
        r = self._tags(self.P + "place 2 damage counters on the Attacking "
                                "Pok\u00e9mon for each {M} Energy attached to "
                                "this Pok\u00e9mon.")
        self.assertEqual(r["tags"], ["on_damaged:counter_per:2:metal"])
        self.assertEqual(r["unsupported"], [])

    def test_next_turn_retaliate(self):
        """「During your opponent's next turn, … put 6 damage counters」→ 下回合反制

        2026-10-29 第二十九批：引擎新增 `retaliate_next:<n|equal>`（窗口 = 对手的
        下一个回合），这类**临时**反制不再是「无延迟队列」的未建模。
        """
        r = self._tags("During your opponent's next turn, if this Pok\u00e9mon is "
                       "damaged by an attack (even if this Pok\u00e9mon is "
                       "Knocked Out), put 6 damage counters on the Attacking "
                       "Pok\u00e9mon.", kind="attack")
        self.assertEqual(r["tags"], ["retaliate_next:60"])
        self.assertEqual(r["unsupported"], [])

    def test_next_turn_retaliate_equal(self):
        r = self._tags("During your opponent's next turn, if this Pok\u00e9mon is "
                       "damaged by an attack (even if this Pok\u00e9mon is "
                       "Knocked Out), put damage counters on the Attacking "
                       "Pok\u00e9mon equal to the damage done to this "
                       "Pok\u00e9mon.", kind="attack")
        self.assertEqual(r["tags"], ["retaliate_next:equal"])
        self.assertEqual(r["unsupported"], [])

    def test_search_reaction_now_modeled(self):
        """受击即检索（Koffing）→ 第一百零一批起 `on_damaged:search_bench:`

        决策反转留痕：旧口径要求「受击即检索」的新钩子。现在 `on_damaged:`
        新增 `search_bench` kind，检索目标用 `:name_has:` **子串**筛选
        （卡面 "have Koffing in their name"，不是精确名）。
        """
        r = self._tags(self.P + "search your deck for up to 2 Pok\u00e9mon that "
                                "have \u201cKoffing\u201d in their name and put "
                                "them onto your Bench. Then, shuffle your deck.")
        self.assertEqual(r["tags"],
                         ["on_damaged:search_bench:2:name_has:koffing"])
        self.assertEqual(r["unsupported"], [])

    def test_prevent_damage_reaction_modeled_as_coin_prevent(self):
        """抛币免伤（第四十五批）：由 `passive:coin_prevent:` 承载，不得无条件免伤

        决策反转留痕：本用例原断言「prevent that damage 需要伤害预防层 → 未建模」。
        Fezandipiti 的抛币免伤现已在**伤害结算内**实现（正面才归零），因此改为
        锁定该 tag；无条件 `passive:dmg_immune` 才是静默加强。
        """
        r = self._tags("If this Pok\u00e9mon has any {D} Energy attached and is "
                       "damaged by an attack, flip a coin. If heads, prevent "
                       "that damage.")
        self.assertEqual(r["tags"], ["passive:coin_prevent:darkness"])
        self.assertEqual(r["unsupported"], [])

    def test_history_condition_now_modeled(self):
        """「was damaged … last turn」→ 第一百零一批起 `dmg_plus_prev_taken`

        决策反转留痕：旧口径要求「回合级受击历史」，引擎没有 → 如实拒绝。
        现在 `FieldPokemon.dmg_taken_prev` 在**回合开始**把上回合累计量滚过来
        （Mega Heracross ex「Juggernaut Horn」）。

        ⚠ 关键仍是「**不按固定值**映射」：卡面是 "that much"，落成常数就是
          静默改数值。这里断言产的是**动态** tag，而不是 `dmg_plus:<N>`。
        """
        r = self._tags("If this Pok\u00e9mon was damaged by an attack during "
                       "your opponent's last turn, this attack does that much "
                       "more damage.", kind="attack")
        self.assertEqual(r["tags"], ["dmg_plus_prev_taken"])
        self.assertFalse([t for t in r["tags"]
                          if t.startswith("dmg_plus:")],
                         "不得按固定值映射「that much more」")
        self.assertEqual(r["unsupported"], [])

    # ---- 特性状态词：只有「主动施加」才放通 ----
    def test_active_infliction_passes(self):
        r = self._tags("Once during your turn, if this Pok\u00e9mon is in the "
                       "Active Spot, you may make your opponent's Active "
                       "Pok\u00e9mon Burned.")
        self.assertEqual(r["tags"], ["burn"])

    def test_immunity_is_not_infliction(self):
        """「This Pokémon can't be Asleep.」= 免疫，不是让对手睡眠

        第一百一十一批起由 `passive:status_immune:sleep` 承载（Hoothoot）。
        断言重点仍是**方向**：绝不能产出裸 `sleep`（那是让对手睡眠）。
        """
        r = self._tags("This Pok\u00e9mon can't be Asleep.")
        self.assertNotIn("sleep", r["tags"])
        self.assertEqual(r["tags"], ["passive:status_immune:sleep"])
        self.assertEqual(r["unsupported"], [])

    def test_checkup_extra_damage_is_not_infliction(self):
        """「put 3 more damage counters on your opponent's Burned Pokémon」= 追加伤害

        2026-10-28 第十三批起已精确建模（`passive:checkup_plus:*`）；关键是
        **不得**产出裸 `burn`（那是「让对手陷入烧伤」，会静默加强）。
        """
        r = self._tags("During Pok\u00e9mon Checkup, put 3 more damage counters "
                       "on your opponent's Burned Pok\u00e9mon.")
        self.assertEqual(r["tags"], ["passive:checkup_plus:burn:30:side:opp"])
        self.assertNotIn("burn", r["tags"])
        self.assertEqual(r["unsupported"], [])

    def test_switch_trigger_is_not_infliction(self):
        """换位触发 —— **改判**（S2b，2026-11-08）

        原判据：引擎没有「别人换位」这个事件 → 整卡拒收（关键仍是**不得**产出
        裸 `confuse`：那是「立刻让对手战斗位混乱」，与卡面「换上来的那只混乱」
        是两回事，会静默加强）。S2b 接上 switch_in 事件后可以精确表达。
        """
        r = self._tags("As long as this Pok\u00e9mon is in the Active Spot, "
                       "whenever your opponent's Active Pok\u00e9mon moves to the "
                       "Bench during their turn, their new Active Pok\u00e9mon is "
                       "now Confused.")
        self.assertNotIn("confuse", r["tags"])
        self.assertEqual(r["unsupported"], [])
        self.assertEqual(
            r["tags"],
            ["on_event:switch_in:status:confuse:on:entering"
             ":scope:opp_their_turn:while:active"])

    def test_ability_cost_mapped(self):
        """**决策反转**（2026-11-04 第四十九批）：带着代价的特性现在可建模

        当时拒收的理由是「引擎无代价结算」—— 直接放通等于**免费发动**，是对的。
        第四十九批补上了 `AbilityDef.cost`（付账在效果**之前**、付不出不进合法
        动作表），于是这条从「必须未建模」变成「必须建模」，且代价要跟着走。
        """
        r = self._tags("You must discard a Basic {R} Energy card from your hand "
                       "in order to use this Ability. Once during your turn, you "
                       "may make your opponent's Active Pok\u00e9mon Burned.")
        self.assertEqual(r["tags"], ["burn"])
        self.assertEqual(r["ability_cost"], "discard_hand_energy:1:fire")
        self.assertEqual(r["unsupported"], [])

    def test_evolve_trigger_status_wrapped(self):
        """2026-10-28 第十批：进化触发的状态特性已可建模（包裹 `evolve_act:`）

        引擎在**进化那一刻**执行一次，因此不再有「每回合免费再用」的问题。
        """
        r = self._tags("Once during your turn, when you play this Pok\u00e9mon "
                       "from your hand to evolve 1 of your Pok\u00e9mon, you may "
                       "use this Ability. Make your opponent's Active "
                       "Pok\u00e9mon Confused.")
        self.assertEqual(r["tags"], ["evolve_act:confuse"])
        self.assertEqual(r["unsupported"], [])

    # ---- 道具侧不受影响（另有 tool:on_damaged 实现）----
    def test_tool_reaction_unchanged(self):
        r = self._tags("If the Pok\u00e9mon this card is attached to is in the "
                       "Active Spot and is damaged by an attack from your "
                       "opponent's Pok\u00e9mon (even if this Pok\u00e9mon is "
                       "Knocked Out), draw 2 cards.", kind="item")
        self.assertEqual(r["tags"], ["tool:on_damaged:draw:2"])


class TestSearchFilters(unittest.TestCase):
    """全牌库检索的属性 / 阶段 / HP 筛选（2026-10-28）

    引擎 `_poke_matches_filter` 支持 type / stage / hp 三层后，解析侧放通
    对应句式；其它类别（能量/训练家）仍无筛选层，必须继续拒绝。
    """

    @staticmethod
    def _tags(text, kind="ability"):
        return E.to_engine_tags([E.parse_rule(text, kind=kind, name="X")])

    def test_type_filter(self):
        r = self._tags("Search your deck for up to 2 {L} Pokémon, reveal them, "
                       "and put them into your hand. Then, shuffle your deck.")
        self.assertEqual(r["tags"], ["search_pokemon:2:type:lightning"])
        self.assertEqual(r["unsupported"], [])

    def test_stage_filter_with_type_between_words(self):
        r = self._tags("Search your deck for up to 2 Evolution {M} Pokémon, "
                       "reveal them, and put them into your hand.")
        self.assertEqual(r["tags"],
                         ["search_pokemon:2:type:metal:stage:evolution"])

    def test_hp_filter(self):
        r = self._tags("Search your deck for up to 3 {C} Pokémon with 100 HP "
                       "or less, reveal them, and put them into your hand.")
        self.assertEqual(r["tags"],
                         ["search_pokemon:3:type:colorless:hp:100"])

    def test_energy_type_filter(self):
        """B2 剩余（2026-10-28）：能量检索支持属性筛选（Aromatisse / Firebreather）"""
        r = self._tags("Search your deck for up to 2 Basic {W} Energy cards, "
                       "reveal them, and put them into your hand.")
        self.assertEqual(r["tags"], ["search_energy:2:type:water"])

    def test_energy_untyped_still_plain(self):
        r = self._tags("Search your deck for up to 3 Basic Energy cards, "
                       "reveal them, and put them into your hand.")
        self.assertEqual(r["tags"], ["search_energy:3"])

    def test_resistance_filter(self):
        """`{F} Resistance` 是**抗性**筛选，不得当成属性筛选"""
        r = self._tags("Search your deck for up to 2 Pok\u00e9mon with {F} "
                       "Resistance, reveal them, and put them into your hand. "
                       "Then, shuffle your deck.")
        self.assertEqual(r["tags"], ["search_pokemon:2:resistance:fighting"])
        self.assertEqual(r["unsupported"], [], "抗性筛选不得再登记为静态规则修改")

    def test_count_based_search(self):
        """张数由计数源决定 → `search_any:N:per:<counter>`（Scrafty）"""
        r = self._tags("Search your deck for a number of cards up to the number "
                       "of your Benched Pok\u00e9mon and put them into your hand. "
                       "Then, shuffle your deck.", kind="attack")
        self.assertEqual(r["tags"], ["search_any:1:per:own_bench"])

    def test_any_number_named_search_modeled(self):
        """「any number of **具名** cards」→ 第一百批起可建模（Musharna）

        为什么这类「任意张」不需要交互层（与弃牌/对攻类不同）：
          · 备选集合被**卡名**封死（这里只有 Fennel 系列）；
          · 这是**检索**——拿到手是纯增益，不存在「取 0 更好」的场合。
        与 Precious Trolley 的 `search_bench:all:basic` 同一口径。

        ⚠ 不带卡名的「any number of cards」仍必须拒绝（见 `_search_any_named`
          的类别词黑名单），否则会把「Basic Energy」当成卡名。
        """
        r = self._tags("You may search your deck for any number of Fennel "
                       "cards, reveal them, and put them into your hand. Then, "
                       "shuffle your deck.")
        self.assertEqual(r["tags"], ["search_trainer:all:name_has:fennel"])
        self.assertEqual(r["unsupported"], [])


class TestSelfStatusParsing(unittest.TestCase):
    """「This Pokémon is now …」必须是自身状态（2026-10-28）

    裸状态 tag 恒作用于对手战斗位；自身状态必须走 `status_self:<s>`，
    否则方向反转（Larry's Komala 曾长期给对手上睡眠）。
    """

    @staticmethod
    def _tags(text, kind="attack"):
        return E.to_engine_tags([E.parse_rule(text, kind=kind, name="X")])

    def test_self_status_tag(self):
        r = self._tags("This Pokémon is now Confused.")
        self.assertEqual(r["tags"], ["status_self:confuse"])
        self.assertEqual(r["unsupported"], [])

    def test_opponent_status_remains_bare(self):
        r = self._tags("Your opponent’s Active Pokémon is now Burned.")
        self.assertEqual(r["tags"], ["burn"])
        self.assertNotIn("status_self:burn", r["tags"])


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
        # ⚠ 必须遍历**全部印刷**（`printings()`），不能只看卡名默认印刷：
        # 黄金集是照人工池写的，而一些卡名的默认印刷已切换为数据集新印刷，
        # 只看 catalog 会让人工印刷的招式文本缺席 → 与同名异卡的文本误比。
        for _uid, item in reallib.printings().items():
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
        for _uid, item in reallib.printings().items():
            d = item["def"]
            for a in getattr(d, "attacks", ()) or ():
                # 纯伤害招式没有效果文本，desc_zh 与 desc 同为空是正常情况；
                # 只有卡面**本来就有**效果文本时才要求中文译文。
                if a.name_zh and a.desc:
                    self.assertTrue(a.display_desc,
                                    f"{item['name']} 的招式 {a.name} 缺中文效果文本")


class TestDiscardToolStadium(unittest.TestCase):
    """S6 弃道具 / 弃场地族（2026-10-28）

    引擎新增 `discard_tool:N:<scope>` 与 `discard_stadium` 后，这一族从
    「静态/未建模（英文）」兜底桶里出来了。范围（哪一侧/是否含特殊能量）
    必须精确进 tag，否则会弃错人的道具。
    """

    @staticmethod
    def _tags(text, kind="attack", name=""):
        return E.to_engine_tags([E.parse_rule(text, kind=kind, name=name)])

    def test_stadium_discard(self):
        r = self._tags("Discard a Stadium in play.")
        self.assertEqual(r["tags"], ["discard_stadium"])
        self.assertEqual(r["unsupported"], [])

    def test_tools_from_opponent_active(self):
        """「Before doing damage」→ `pre:` 伤害前阶段（2026-10-29 第二十五批）"""
        r = self._tags("Before doing damage, discard all Pok\u00e9mon Tools "
                       "from your opponent\u2019s Active Pok\u00e9mon.")
        self.assertEqual(r["tags"], ["pre:discard_tool:all:opp_active"])

    def test_choose_up_to_tools_both_sides(self):
        r = self._tags("Choose up to 2 Pok\u00e9mon Tools attached to "
                       "Pok\u00e9mon (yours or your opponent\u2019s) and "
                       "discard them.", kind="item")
        self.assertEqual(r["tags"], ["discard_tool:2:both"])

    def test_tools_plus_special_energy_pre_phase(self):
        """「…Tools **and Special Energy**…」→ 弃道具 + 弃特殊能量（Mow Rotom）

        第二十四批起引擎能区分特殊能量卡（`senergy`），第二十五批起有伤害前阶段
        —— 两条 tag 都必须带 `pre:` 前缀（伤害后才弃 = 本次仍吃道具效果）。
        """
        r = self._tags("Before doing damage, discard all Pok\u00e9mon Tools and "
                       "Special Energy from your opponent\u2019s Active "
                       "Pok\u00e9mon.")
        self.assertEqual(r["tags"],
                         ["pre:discard_energy:all:opp:special",
                          "pre:discard_tool:all:opp_active"])
        self.assertEqual(r["unsupported"], [])

    def test_cetitan_stadium_bonus(self):
        """「You may discard a Stadium in play. If you do, … +140」——两段都要建模"""
        r = self._tags("You may discard a Stadium in play. If you do, this "
                       "attack does 140 more damage.")
        self.assertEqual(sorted(r["tags"]),
                         ["discard_stadium",
                          "dmg_plus:140:if:stadium_in_play"])
        self.assertEqual(r["unsupported"], [])


class TestBounceParsing(unittest.TestCase):
    """S6 回手 / 洗回牌库族（2026-10-28）

    这一族的关键是**附加卡去向**：卡面写「and all attached cards」时附加卡随行，
    写「(Discard all cards attached …)」时进弃牌堆 —— 两者必须区分，否则
    要么多回一张、要么少回一张。
    """

    @staticmethod
    def _tags(text, kind="attack", name=""):
        return E.to_engine_tags([E.parse_rule(text, kind=kind, name=name)])

    def test_self_to_hand_with_attached(self):
        r = self._tags("Put this Pok\u00e9mon and all attached cards into your "
                       "hand.")
        self.assertEqual(r["tags"], ["bounce:to_hand:self"])
        self.assertEqual(r["unsupported"], [])

    def test_self_to_hand_discard_attached(self):
        r = self._tags("You may put this Pok\u00e9mon into your hand. (Discard "
                       "all cards attached to this Pok\u00e9mon.)")
        self.assertEqual(r["tags"], ["bounce:to_hand:self:discard_attached"])

    def test_shuffle_self(self):
        r = self._tags("Shuffle this Pok\u00e9mon and all attached cards into "
                       "your deck.")
        self.assertEqual(r["tags"], ["bounce:shuffle_self"])

    def test_shuffle_pronoun_form(self):
        """Abra 用代词「shuffle it and all attached cards into your deck」"""
        r = self._tags("Once during your turn, if this Pok\u00e9mon is in the "
                       "Active Spot, you may shuffle it and all attached cards "
                       "into your deck.", kind="ability")
        self.assertEqual(r["tags"], ["bounce:shuffle_self"])

    def test_bench_bounce(self):
        r = self._tags("Put 1 of your Benched Pok\u00e9mon and all attached "
                       "cards into your hand.")
        self.assertEqual(r["tags"], ["bounce:to_hand:bench"])

    def test_choose_bounce(self):
        r = self._tags("Put 1 of your Pok\u00e9mon and all attached cards into "
                       "your hand.", kind="item")
        self.assertEqual(r["tags"], ["bounce:to_hand:choose"])

    def test_discard_to_bench_by_name(self):
        r = self._tags("Put up to 3 Duskull from your discard pile onto your "
                       "Bench.")
        self.assertEqual(r["tags"], ["bounce:discard_to_bench:3:name:duskull"])

    def test_discard_to_bench_by_hp(self):
        r = self._tags("Once during your turn, if this Pok\u00e9mon is in the "
                       "Active Spot, you may put a Basic Pok\u00e9mon with 70 HP "
                       "or less from your discard pile onto your Bench.",
                       kind="ability")
        self.assertEqual(r["tags"], ["bounce:discard_to_bench:1:hp:70"])

    def test_discard_to_bench_unknown_limit_rejected(self):
        r = self._tags("Put up to 2 Pok\u00e9mon from your discard pile onto "
                       "your Bench.")
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])


class TestScalingCounters(unittest.TestCase):
    """S6 计数缩放剩余源（2026-10-28）

    新增计数源全部是**公开且确定性**的：数据集分类字段（Ancient / {ex}）·
    卡名阵营（own_named_*）· 对手弃牌堆类别（opp_discard_energy / item）·
    双方道具数 · 战斗位有效撤退费用。
    """

    @staticmethod
    def _tags(text, kind="attack", name=""):
        return E.to_engine_tags([E.parse_rule(text, kind=kind, name=name)])

    def test_less_damage(self):
        """「does N less damage for each X」→ `dmg_minus:N:per:<counter>`"""
        r = self._tags("This attack does 60 less damage for each Energy attached "
                       "to your opponent\u2019s Active Pok\u00e9mon.")
        self.assertEqual(r["tags"], ["dmg_minus:60:per:opp_active_energy"])

    def test_retreat_cost_counter(self):
        r = self._tags("This attack does 30 less damage for each {C} in your "
                       "opponent\u2019s Active Pok\u00e9mon\u2019s Retreat Cost.")
        self.assertEqual(r["tags"], ["dmg_minus:30:per:opp_active_retreat"])

    def test_opponent_discard_energy(self):
        r = self._tags("This attack does 30 damage for each Basic Energy card "
                       "in your opponent\u2019s discard pile.")
        self.assertEqual(r["tags"], ["dmg_set:30:per:opp_discard_energy"])

    def test_opponent_discard_item(self):
        r = self._tags("This attack does 30 damage for each Item card in your "
                       "opponent\u2019s discard pile.")
        self.assertEqual(r["tags"], ["dmg_set:30:per:opp_discard_item"])

    def test_named_affiliation(self):
        r = self._tags("This attack does 30 damage for each of your Team "
                       "Rocket\u2019s Pok\u00e9mon in play.")
        self.assertEqual(r["tags"], ["dmg_set:30:per:own_named_teamrocket"])
        r2 = self._tags("This attack does 40 damage for each of your Erika\u2019s "
                        "Pok\u00e9mon in play.")
        self.assertEqual(r2["tags"], ["dmg_set:40:per:own_named_erika"])

    def test_ancient_counter(self):
        r = self._tags("This attack does 30 damage for each of your Ancient "
                       "Pok\u00e9mon in play.")
        self.assertEqual(r["tags"], ["dmg_set:30:per:own_ancient"])

    def test_opponent_ex_counter(self):
        r = self._tags("This attack does 60 damage for each of your "
                       "opponent\u2019s Pok\u00e9mon {ex} in play.")
        self.assertEqual(r["tags"], ["dmg_set:60:per:opp_ex"])

    def test_ex_and_v_uses_dedicated_counter(self):
        """{ex} 与 {V} 混合：第九十四批起落**独立命名**的 `opp_ex_or_v`

        2026-11-05 之前这里是「拒绝」用例（引擎无 V 标记）。本批起引擎新增
        `opp_ex_or_v`，与 `opp_ex` 当前同值，但**独立命名**：将来加 V 类卡支持时
        只改那一处，不会让已有的 `opp_ex` 静默变错。故此处从拒绝改为正向断言。
        """
        r = self._tags("This attack does 60 damage for each of your "
                       "opponent\u2019s Pok\u00e9mon {ex} and Pok\u00e9mon {V} "
                       "in play.")
        self.assertEqual(r["tags"], ["dmg_set:60:per:opp_ex_or_v"])
        self.assertEqual(r["unsupported"], [])

    def test_both_field_tools(self):
        r = self._tags("This attack does 40 more damage for each Pok\u00e9mon "
                       "Tool attached to all Pok\u00e9mon in play.")
        self.assertEqual(r["tags"], ["dmg_plus:40:per:both_field_tools"])

    def test_unknown_counter_still_rejected(self):
        r = self._tags("This attack does 30 damage for each card you revealed "
                       "in this way.")
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])

    # ---- 第二十六批（2026-10-29）：**限定后**的指示物计数源 ----
    def test_bench_counters_by_type(self):
        """「damage counter on all of your Benched {F} Pokémon」（Gigalith）"""
        r = self._tags("This attack does 20 damage for each damage counter on "
                       "all of your Benched {F} Pok\u00e9mon.")
        self.assertEqual(r["tags"],
                         ["dmg_set:20:per:own_bench_counters_type:fighting"])

    def test_bench_counters_named(self):
        """具名所有格的后备指示物（Cynthia's Spiritomb）"""
        r = self._tags("This attack does 10 damage for each damage counter on "
                       "all of your Benched Cynthia\u2019s Pok\u00e9mon. This "
                       "attack\u2019s damage isn\u2019t affected by Weakness.")
        self.assertIn("dmg_set:10:per:own_bench_counters_named:cynthia",
                      r["tags"])
        self.assertIn("ignore_weakness", r["tags"])

    def test_field_named_with_damage(self):
        """「each of your Pokémon that has “Tauros” in its name that has any
        damage counters on it」（Paldean Tauros）→ 数**只数**"""
        r = self._tags("This attack does 40 damage for each of your Pok\u00e9mon "
                       "that has \u201cTauros\u201d in its name that has any "
                       "damage counters on it.")
        self.assertEqual(r["tags"],
                         ["dmg_set:40:per:own_field_named_with_damage:tauros"])

    def test_plain_bench_counters_unchanged(self):
        """无限定词的全体后备仍走 `own_bench_counters`（不得被新规则改写）"""
        r = self._tags("This attack does 10 damage for each damage counter on "
                       "all of your Benched Pok\u00e9mon.")
        self.assertEqual(r["tags"], ["dmg_set:10:per:own_bench_counters"])


class TestSnipeTargetFilterParse(unittest.TestCase):
    """第二十七批（2026-10-29）：`snipe:` 目标筛选 / 逐目标缩放"""

    @staticmethod
    def _tags(text, kind="attack", name=""):
        return E.to_engine_tags([E.parse_rule(text, kind=kind, name=name)])

    def test_target_damaged(self):
        """Excadrill ex：「1 of … Benched Pokémon **that has any damage
        counters on it**」→ `:target:damaged`（先筛目标再选 1 个）"""
        r = self._tags("This attack also does 60 damage to 1 of your "
                       "opponent\u2019s Benched Pok\u00e9mon that has any damage "
                       "counters on it. (Don\u2019t apply Weakness and "
                       "Resistance for Benched Pok\u00e9mon.)")
        self.assertEqual(r["tags"], ["snipe:opp_bench:60:1:target:damaged"])
        self.assertEqual(r["unsupported"], [])

    def test_both_bench_target_damaged(self):
        """Hippowdon：「each Benched Pokémon that has any damage counters」
        （双方）→ `both_bench` + 全量 + 筛选"""
        r = self._tags("This attack also does 40 damage to each Benched "
                       "Pok\u00e9mon that has any damage counters on it (both "
                       "yours and your opponent\u2019s). (Don\u2019t apply "
                       "Weakness and Resistance for Benched Pok\u00e9mon.)")
        self.assertEqual(r["tags"], ["snipe:both_bench:40:all:target:damaged"])
        self.assertEqual(r["unsupported"], [])

    def test_per_target_counters(self):
        """Team Rocket's Sneasel：「for each damage counter **on that
        Pokémon**」→ 逐目标缩放哨兵"""
        r = self._tags("This attack does 20 damage to 1 of your opponent\u2019s "
                       "Benched Pok\u00e9mon for each damage counter on that "
                       "Pok\u00e9mon. (Don\u2019t apply Weakness and Resistance "
                       "for Benched Pok\u00e9mon.)")
        self.assertEqual(r["tags"],
                         ["snipe:opp_bench:20:1:per:target_counters"])
        self.assertEqual(r["unsupported"], [])

    def test_other_qualifier_still_unmodeled(self):
        """别的「that has …」限定（如挂能量）仍不得被当成本次的筛选"""
        r = self._tags("This attack does 60 damage to 1 of your opponent\u2019s "
                       "Benched Pok\u00e9mon that has any Energy attached to it.")
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])

    def test_plain_snipe_unchanged(self):
        r = self._tags("This attack does 60 damage to 1 of your opponent\u2019s "
                       "Benched Pok\u00e9mon. (Don\u2019t apply Weakness and "
                       "Resistance for Benched Pok\u00e9mon.)")
        self.assertEqual(r["tags"], ["snipe:opp_bench:60:1"])


class TestEnergyToDeckAndAbilityCountersParse(unittest.TestCase):
    """第二十八批（2026-10-29）：弃牌堆能量洗回牌库 · 双方「有特性者」指示物"""

    @staticmethod
    def _tags(text, kind="attack", name=""):
        return E.to_engine_tags([E.parse_rule(text, kind=kind, name=name)])

    def test_kyogre_riptide(self):
        """Kyogre：按弃牌堆水能量计数 + **洗回牌库**（缺洗回 = 下次重复计数）"""
        r = self._tags("This attack does 20 damage for each Basic {W} Energy card "
                       "in your discard pile. Then, shuffle those cards into your "
                       "deck.")
        self.assertEqual(sorted(r["tags"]),
                         ["dmg_set:20:per:own_discard_energy_water",
                          "energy_to_deck:water:all"])
        self.assertEqual(r["unsupported"], [])

    def test_sinistcha_ex_rebrew(self):
        """Sinistcha ex：指示物按弃牌堆草能量计数 + 洗回牌库"""
        r = self._tags("Put 2 damage counters on 1 of your opponent\u2019s "
                       "Pok\u00e9mon for each Basic {G} Energy card in your discard "
                       "pile. Then, shuffle those Energy cards into your deck.")
        self.assertEqual(sorted(r["tags"]),
                         ["counters:2:per:own_discard_energy_grass:scope:opp:1",
                          "energy_to_deck:grass:all"])
        self.assertEqual(r["unsupported"], [])

    def test_ability_counters_both_sides(self):
        """Cofagrigus：双方全场「有特性」的逐个各 6 个指示物"""
        r = self._tags("Put 6 damage counters on each Pok\u00e9mon that has an "
                       "Ability (both yours and your opponent\u2019s).")
        self.assertEqual(r["tags"], ["counters:6:scope:ability:all"])
        self.assertEqual(r["unsupported"], [])

    def test_shuffle_back_without_discard_energy_rejected(self):
        """「shuffle those cards」但计数源不是弃牌堆基本能量 → 不猜"""
        r = self._tags("Discard 2 cards from your hand. Then, shuffle those cards "
                       "into your deck.")
        self.assertEqual(r["tags"], ["discard:2"])
        self.assertTrue(r["unsupported"])


class TestEnergyMoveParsing(unittest.TestCase):
    """S6 能量移动族（2026-10-28）

    源与目标区域必须分别进 tag：把「对手战斗位 → 对手后备」判成己方，
    或把「后备 → 战斗位」反向，都会让能量落到错误的一方身上（静默算错）。
    """

    @staticmethod
    def _tags(text, kind="attack", name=""):
        return E.to_engine_tags([E.parse_rule(text, kind=kind, name=name)])

    def test_active_to_bench(self):
        r = self._tags("Move an Energy from this Pok\u00e9mon to 1 of your "
                       "Benched Pok\u00e9mon.")
        self.assertEqual(r["tags"], ["energy_move:1:from:active:to:bench"])

    def test_multiple_energy(self):
        r = self._tags("Move 3 Energy from this Pok\u00e9mon to 1 of your "
                       "Benched Pok\u00e9mon.")
        self.assertEqual(r["tags"], ["energy_move:3:from:active:to:bench"])

    def test_typed(self):
        r = self._tags("Move a {W} Energy from this Pok\u00e9mon to 1 of your "
                       "Benched Pok\u00e9mon.")
        self.assertEqual(r["tags"],
                         ["energy_move:1:type:water:from:active:to:bench"])

    def test_opponent_side(self):
        r = self._tags("You may move an Energy from your opponent\u2019s Active "
                       "Pok\u00e9mon to 1 of their Benched Pok\u00e9mon.")
        self.assertEqual(r["tags"],
                         ["energy_move:1:from:opp_active:to:opp_bench"])

    def test_opponent_any(self):
        r = self._tags("Move an Energy from 1 of your opponent\u2019s "
                       "Pok\u00e9mon to another of their Pok\u00e9mon.")
        self.assertEqual(r["tags"], ["energy_move:1:from:opp_any:to:opp_any"])

    def test_own_any(self):
        r = self._tags("Move a Basic Energy from 1 of your Pok\u00e9mon to "
                       "another of your Pok\u00e9mon.", kind="item")
        self.assertEqual(r["tags"], ["energy_move:1:from:any:to:any"])

    def test_bench_to_active(self):
        r = self._tags("Move up to 2 Energy from your Benched Pok\u00e9mon to "
                       "your Active Pok\u00e9mon.", kind="supporter")
        self.assertEqual(r["tags"], ["energy_move:2:from:bench:to:active"])

    def test_any_amount_rejected(self):
        """「any amount … in any way you like」= 玩家自由数量 → 必须拒绝"""
        r = self._tags("You may move any amount of {M} Energy from your "
                       "Pok\u00e9mon to your other Pok\u00e9mon in any way you "
                       "like.")
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])


class TestToolCostRetreat(unittest.TestCase):
    """S6 道具费用 / 撤退修正（2026-10-28）"""

    @staticmethod
    def _tags(text):
        return E.to_engine_tags([E.parse_rule(text, kind="item", name="T")])

    def test_sparkling_crystal_tera_cost(self):
        r = self._tags("When the Tera Pok\u00e9mon this card is attached to uses "
                       "an attack, that attack costs 1 Energy less. (The Energy "
                       "can be of any type.)")
        self.assertEqual(r["tags"], ["tool:cost_less:1:tera"])

    def test_counter_gain_behind(self):
        r = self._tags("If you have more Prize cards remaining than your "
                       "opponent, attacks used by the Pok\u00e9mon this card is "
                       "attached to cost {C} less.")
        self.assertEqual(r["tags"], ["tool:cost_less:1:behind"])

    def test_gravity_gemstone(self):
        r = self._tags("As long as the Pok\u00e9mon this card is attached to is "
                       "in the Active Spot, the Retreat Cost of both Active "
                       "Pok\u00e9mon is {C} more.")
        self.assertEqual(r["tags"], ["tool:retreat_plus:1:both"])

    def test_rescue_board_low_hp(self):
        r = self._tags("The Retreat Cost of the Pok\u00e9mon this card is "
                       "attached to is {C}{C} less. If that Pok\u00e9mon\u2019s "
                       "remaining HP is 30 or less, it has no Retreat Cost.")
        self.assertEqual(r["tags"], ["tool:retreat_less:2:lowhp:30"])


class TestOppHandParsing(unittest.TestCase):
    """S5 对手手牌信息族（2026-10-28）

    只映射**卡面本身写 random** 或**数量确定**的形态；「你选一张」属隐藏信息
    交互，引擎绝不代替玩家选择。纯「reveal hand」无局面效果 → 如实未建模。
    """

    @staticmethod
    def _tags(text, kind="item"):
        return E.to_engine_tags([E.parse_rule(text, kind=kind, name="X")])

    def test_discard_random(self):
        r = self._tags("Discard a random card from your opponent\u2019s hand.")
        self.assertEqual(r["tags"], ["discard_opp_random:1"])

    def test_shuffle_random_into_deck(self):
        r = self._tags("Choose a random card from your opponent\u2019s hand. "
                       "Your opponent reveals that card and shuffles it into "
                       "their deck.")
        self.assertEqual(r["tags"], ["shuffle_opp_hand:1"])

    def test_discard_until_n(self):
        r = self._tags("Your opponent discards cards from their hand until "
                       "they have 3 cards in their hand.")
        self.assertEqual(r["tags"], ["discard_opp_to:3"])

    def test_hand_shuffle_same_count(self):
        r = self._tags("Your opponent counts the cards in their hand, shuffles "
                       "those cards, and puts them on the bottom of their deck. "
                       "If they do, they draw that many cards.")
        self.assertEqual(r["tags"], ["opp_shuffle_draw:same"])

    def test_reveal_only_rejected(self):
        r = self._tags("Your opponent reveals their hand.")
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])

    def test_choose_specific_card_now_mapped(self):
        """「你从对手手牌挑一张弃掉」—— **2026-10-30 第三十二批起改为映射**

        此前这里断言「必须拒绝」，理由是「隐藏信息选择」。该理由在**引擎现状**
        下不成立：引擎根本不建模信息隐藏（``Battle.view()`` 里双方手牌对决策方
        都是可见的），所以「我方选」并不带来额外信息优势；真正缺的只是「选哪张」。

        选择内容按项目既有 choose 类口径处理（同 ``snipe``）：动作 dict 可带
        ``hand_pick``，缺省取**手牌顺序第一张**（确定性、可复现），绝不静默不生效。
        「随机」口径留给卡面真写「对手自选 / at random」的句式
        （``discard_opp_random`` / ``discard_opp_to`` / ``shuffle_opp_hand``）。
        """
        r = self._tags("Your opponent reveals their hand, and you discard a "
                       "card you find there.")
        self.assertEqual(r["tags"], ["opp_hand:discard:any"])
        self.assertEqual(r["unsupported"], [])


class TestCoinAll(unittest.TestCase):
    """「Flip N coins. If **both/all** of them are heads …」（Bewear，2026-10-28）

    与 `coin_each`（每枚正面各生效一次）语义不同：这里是**全部命中**才生效一次。
    """

    def test_both_heads_damage(self):
        r = E.to_engine_tags([E.parse_rule(
            "Flip 2 coins. If both of them are heads, this attack does 100 more "
            "damage.", kind="attack", name="Bewear")])
        self.assertEqual(r["tags"], ["coin_all:2:heads:dmg_plus:100"])
        self.assertEqual(r["unsupported"], [])

    def test_each_still_distinct(self):
        r = E.to_engine_tags([E.parse_rule(
            "Flip 3 coins. This attack does 30 more damage for each heads.",
            kind="attack", name="X")])
        self.assertIn("coin_each:3:heads:dmg_plus:30", r["tags"])


class TestCrossTurnLockParsing(unittest.TestCase):
    """跨回合锁解析（2026-10-27）：攻击 / 撤退 / 手牌禁打

    语音：锁定窗口按**持有者**回合计数，故 tag 必须带方向（self / opp），
    不能把「自己下回合」和「对手下回合」压成同一种状态。
    """

    @staticmethod
    def _tags(text, kind="attack", name=""):
        r = E.parse_effects(text, kind=kind, name=name)
        return E.to_engine_tags(r["specs"])

    def test_self_attack_locks(self):
        tags = self._tags("During your next turn, this Pokémon can’t use attacks.")
        self.assertEqual(tags["tags"], ["attack_lock:all:turn"])
        self.assertEqual(tags["unsupported"], [])

        tags = self._tags("During your next turn, this Pokémon can’t use Giant Wave.",
                          name="Giant Wave")
        self.assertEqual(tags["tags"], ["attack_lock:name:turn:Giant Wave"])

        tags = self._tags("This Pokémon can’t use Blaze Blitz again until it "
                          "leaves the Active Spot.", name="Blaze Blitz")
        self.assertEqual(tags["tags"], ["attack_lock:name:active:Blaze Blitz"])

    def test_opponent_attack_and_retreat_locks(self):
        tags = self._tags("During your opponent’s next turn, the Defending "
                          "Pokémon can’t use attacks.")
        self.assertEqual(tags["tags"], ["attack_lock:opp_all:turn"])

        tags = self._tags("During your opponent’s next turn, the Defending "
                          "Pokémon can’t retreat.")
        self.assertEqual(tags["tags"], ["retreat_lock:opp:turn"])

        # Poison Ring：「that Pokémon」指代前文的对手战斗位
        tags = self._tags("Your opponent’s Active Pokémon is now Poisoned. "
                          "During your opponent’s next turn, that Pokémon "
                          "can’t retreat.")
        self.assertEqual(tags["tags"], ["poison", "retreat_lock:opp:turn"])

    def test_choose_opponent_attack_lock(self):
        """Klefki「Memory Lock」/ Murkrow「Torment」：锁对手战斗位**指定**招式，
        招式名运行时选择 → `attack_lock:opp_name:turn`（不得当具体卡名）。"""
        tags = self._tags(
            "Choose 1 of your opponent’s Active Pokémon’s attacks. "
            "During your opponent’s next turn, that Pokémon can’t use that "
            "attack.")
        self.assertEqual(tags["tags"], ["attack_lock:opp_name:turn"])
        self.assertEqual(tags["unsupported"], [])

    def test_choose_opponent_attack_lock_negative(self):
        """负向守卫：具体招式名的对手封锁仍属 B4（无运行时选择，不得误放通）"""
        tags = self._tags("During your opponent’s next turn, the Defending "
                          "Pokémon can’t use Slash.")
        self.assertEqual(tags["tags"], [])
        self.assertTrue(tags["unsupported"], "具体卡名的对手封锁应保持未建模")

    def test_self_retreat_lock(self):
        tags = self._tags("Heal 50 damage from this Pokémon. During your next "
                          "turn, this Pokémon can’t retreat.")
        self.assertEqual(tags["tags"], ["heal:50", "retreat_lock:self:turn"])

    def test_item_and_evolution_play_locks(self):
        tags = self._tags("During your opponent’s next turn, they can’t play "
                          "any Item cards from their hand.")
        self.assertEqual(tags["tags"], ["play_lock:item:opp:turn"])

        tags = self._tags("During your opponent’s next turn, they can’t play "
                          "any Pokémon from their hand to evolve their Pokémon.")
        self.assertEqual(tags["tags"], ["play_lock:evolution:opp:turn"])

    def test_static_locks_stay_unmodeled(self):
        tags = self._tags("As long as this Pokémon is in the Active Spot, your "
                          "opponent’s Active Pokémon can’t retreat.",
                          kind="ability", name="Static")
        self.assertEqual(tags["tags"], [])
        self.assertTrue(tags["unsupported"], "常驻静态锁必须如实登记未建模")

        # 交叉写法（你的回合 + 对手战斗位）没有真实卡例 → 宁缺勿错
        tags = self._tags("During your next turn, the Defending Pokémon "
                          "can’t retreat.")
        self.assertEqual(tags["tags"], [])
        self.assertTrue(tags["unsupported"])

    def test_first_turn_and_typed_discard_guards(self):
        tags = self._tags("If you go second, you can’t use this attack during "
                          "your first turn. This attack does 30 damage for each "
                          "of your Benched Pokémon.")
        self.assertIn("dmg_set:30:per:own_bench", tags["tags"])
        # 首回合限制已是引擎条件层（`_attack_usable`，第一百零七批），
        # 不再登记未建模 —— 但仍须落 `use_not:` 这条**动态**条件。
        self.assertIn("use_not:went_second_first_turn", tags["tags"])
        self.assertFalse(any("首回合" in u.get("reason", "")
                             for u in tags["unsupported"]))

        tags = self._tags("Discard 6 Basic {G} Energy cards from your hand, and "
                          "Knock Out your opponent’s Active Pokémon. If you "
                          "can’t discard 6 cards in this way, this attack does "
                          "nothing.")
        # B3（2026-10-28）：弃能可精确表达；第九批起**击倒也已建模**
        # （`knockout:opp_active`）→ Hydrapple 的两段都精确可执行。
        self.assertIn("discard_hand_energy:6:type:grass:required", tags["tags"])
        self.assertIn("knockout:opp_active", tags["tags"])
        self.assertEqual(tags["unsupported"], [])


class TestWeaknessResistanceBypassParsing(unittest.TestCase):
    """弱点 / 抗性无视（2026-10-27）

    引擎按招式 tag 在伤害结算时跳过弱抗步骤；解析侧必须区分：
      · 干净的无条件句 → bypass tag；
      · 带「or by any effects on …」→ 引擎无效果无视层，如实未建模；
      · 带「this attack does nothing」失败门 → 失败门未建模，整卡不得静默收池。
    """

    @staticmethod
    def _tags(text, kind="attack", name=""):
        r = E.parse_effects(text, kind=kind, name=name)
        return E.to_engine_tags(r["specs"])

    def test_both_weakness_and_resistance(self):
        tags = self._tags("This attack’s damage isn’t affected by "
                          "Weakness or Resistance.")
        self.assertEqual(tags["tags"], ["ignore_weakness",
                                        "ignore_resistance"])
        self.assertEqual(tags["unsupported"], [])

    def test_single_axis_variants(self):
        tags = self._tags("This attack’s damage isn’t affected by Weakness.")
        self.assertEqual(tags["tags"], ["ignore_weakness"])
        self.assertEqual(tags["unsupported"], [])

        tags = self._tags("This attack’s damage isn’t affected by Resistance.")
        self.assertEqual(tags["tags"], ["ignore_resistance"])
        self.assertEqual(tags["unsupported"], [])

        # 「This damage …」写法（Arboliva ex）
        tags = self._tags("This damage isn’t affected by Weakness or Resistance.")
        self.assertEqual(tags["tags"], ["ignore_weakness",
                                        "ignore_resistance"])

    def test_bypass_combines_with_other_effects(self):
        tags = self._tags("If you have 3 or more Energy in play, this attack "
                          "does 70 more damage. This attack’s damage isn’t "
                          "affected by Weakness.", name="Magnetic Burst")
        self.assertIn("ignore_weakness", tags["tags"])
        self.assertIn("dmg_plus:70:if:own_all_energy_at_least:3",
                      tags["tags"])
        self.assertEqual(tags["unsupported"], [])

    def test_effect_bypass_clause_is_wide_form(self):
        """第六十五批（2026-11-04）：混合句式精确建模为 `ignore_effects:wr`

        旧断言要求「效果无视未建模时不得只放通 W/R 绕过」—— 当时引擎没有
        「无视守方效果」层，混合句式一律如实未建模。现在引擎补齐两档后，
        继续拒绝就是**把已实现的能力压回灰名单**。改为要求精确落宽口径：
        既不能退化成窄 `ignore_effects`（弱抗那一半会静默漏掉），也不能
        只放通 W/R 绕过（效果那一半会静默漏掉）。
        """
        for text in (
                "This attack’s damage isn’t affected by Weakness or "
                "Resistance, or by any effects on those Pokémon.",
                "This attack’s damage isn’t affected by Weakness or "
                "Resistance, or by any effects on your opponent’s "
                "Active Pokémon."):
            tags = self._tags(text)
            self.assertEqual(tags["tags"], ["ignore_effects:wr"],
                             "混合句式必须落宽口径（弱抗 + 效果一并无视）")
            self.assertEqual(tags["unsupported"], [])

        # 只写「any effects」、完全没有 W/R 的形态：2026-10-28 起**已建模**
        # （`ignore_effects`，Koraidon「Shred」）—— 无视减伤/护盾等**效果**，
        # 但仍吃弱点抗性。两档是**反口径**，绝不能互换。
        tags = self._tags("This attack’s damage isn’t affected by any "
                          "effects on your opponent’s Active Pokémon.")
        self.assertEqual(tags["tags"], ["ignore_effects"])
        self.assertEqual(tags["unsupported"], [])

    def test_attack_does_nothing_gate_is_modeled(self):
        """2026-10-28：条件可精确求值的失败门已由 `nullify_if:<cond>` 建模"""
        tags = self._tags("If your opponent’s Active Pokémon isn’t a "
                          "Pokémon {ex}, this attack does nothing. This "
                          "attack’s damage isn’t affected by Weakness or "
                          "Resistance.")
        self.assertIn("nullify_if:not:opp_active_is_ex", tags["tags"])
        self.assertIn("ignore_weakness", tags["tags"])
        self.assertFalse(any("失败门" in u.get("reason", "")
                             for u in tags["unsupported"]))

        # 状态前置条件：既产出失败门，也**不得**被当成主动施加状态
        tags = self._tags("If your opponent’s Active Pokémon isn’t Burned, "
                          "this attack does nothing.")
        self.assertEqual(tags["tags"], ["nullify_if:not:opp_active_status:burn"])

    def test_coin_failure_gate_remains_modeled(self):
        tags = self._tags("Flip a coin. If tails, this attack does nothing.")
        self.assertEqual(tags["tags"], ["coin:tails:dmg_zero"])
        self.assertEqual(tags["unsupported"], [])


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
