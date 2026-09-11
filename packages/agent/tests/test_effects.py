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
        """引擎未建模的动作必须如实列入 unsupported，不假装支持"""
        specs = [E.spec("item", "X", "on_use",
                        [E.act("search", 2, "deck"),
                         E.act("move_damage", 3, "self")])]
        r = E.to_engine_tags(specs)
        kinds = {u["type"] for u in r["unsupported"]}
        self.assertIn("move_damage", kinds)
        self.assertTrue(all(u.get("reason") for u in r["unsupported"]))

    def test_ability_search_maps_to_recon(self):
        specs = [E.spec("ability", "侦察指令", "static",
                        [E.act("search", 2, "deck")],
                        [{"field": "limit", "op": "eq", "value": "once_per_turn"}])]
        r = E.to_engine_tags(specs)
        self.assertIn("recon:2", r["tags"])


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
        """
        from engine import reallib
        pool: dict = {}
        for name, item in reallib.catalog().items():
            d = item["def"]
            for a in getattr(d, "attacks", ()) or ():
                if a.display_desc:
                    pool[a.name] = a.display_desc
            for ab in getattr(d, "abilities", ()) or ():
                if ab.display_desc:
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
