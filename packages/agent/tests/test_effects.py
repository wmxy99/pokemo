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
        self.assertEqual((d["type"], d["amount"], d["target"]),
                         ("draw", None, "hand"))
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

    def test_ability_search_maps_to_recon(self):
        specs = [E.spec("ability", "侦察指令", "static",
                        [E.act("search", 2, "deck")],
                        [{"field": "limit", "op": "eq", "value": "once_per_turn"}])]
        r = E.to_engine_tags(specs)
        self.assertIn("recon:2", r["tags"])


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
        r = self._tags("Flip a coin. If heads, discard an Energy from your "
                       "opponent\u2019s Active Pok\u00e9mon.")
        self.assertIn("coin:heads:discard_energy:1", r["tags"])

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
