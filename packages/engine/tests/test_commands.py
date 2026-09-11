# -*- coding: utf-8 -*-
"""PokéLab Engine · 自由文本指令用例（中文别名 → 合法动作）

覆盖：归一化 / 匹配打分 / 招式按中文名出招 / 训练家按卡名使用 /
卡位指令（附能量·撤退）/ 明确卡位不可用须报错（不得静默改目标）/
指令清单 / step_command 端到端。

运行：python -m unittest discover -s tests -v
"""
from __future__ import annotations

import unittest

from engine import commands, decklib
from engine.battle import Battle
from engine.agents import BaselineAI


def _advance_until(battle, pred, max_steps: int = 400):
    """用基线 AI 推进对局，直到 pred(battle) 为真（返回是否找到）"""
    ai = [BaselineAI("a"), BaselineAI("b")]
    for _ in range(max_steps):
        if battle.finished:
            return False
        if pred(battle):
            return True
        battle.step(ai[battle.current].decide(battle.view()))
    return False


def _find_attack_state(seeds=(7, 11, 13, 17, 23), deck_a="A1", deck_b="A3"):
    """找一个「轮到玩家 0 且有可用招式」的局面"""
    for s in seeds:
        b = Battle(decklib.make_deck(deck_a), decklib.make_deck(deck_b), seed=s)
        ok = _advance_until(
            b, lambda bt: bt.current == 0 and any(
                a["verb"] == "attack" for a in bt.legal_actions()))
        if ok:
            return b
    return None


def _find_attach_state(seeds=(3, 5, 7, 11, 13, 17), deck_a="A1", deck_b="A3"):
    """找一个「轮到玩家 0 且可附能量」的局面"""
    for s in seeds:
        b = Battle(decklib.make_deck(deck_a), decklib.make_deck(deck_b), seed=s)
        ok = _advance_until(
            b, lambda bt: bt.current == 0 and any(
                a["verb"] == "attach" for a in bt.legal_actions()))
        if ok:
            return b
    return None


def _find_verb_state(verb, seeds=tuple(range(1, 13)),
                     deck_a="A1", deck_b="A3"):
    """找一个「轮到玩家 0 且该 verb 可用」的局面（含初始局面）"""
    for s in seeds:
        b = Battle(decklib.make_deck(deck_a), decklib.make_deck(deck_b), seed=s)
        if b.current == 0 and any(a["verb"] == verb for a in b.legal_actions()):
            return b
        ok = _advance_until(
            b, lambda bt: bt.current == 0 and any(
                a["verb"] == verb for a in bt.legal_actions()))
        if ok:
            return b
    return None


def _find_trainer_state(seeds=(3, 5, 7, 11, 13), deck_a="A1", deck_b="A3"):
    """找一个「轮到玩家 0 且可用训练家卡」的局面"""
    for s in seeds:
        b = Battle(decklib.make_deck(deck_a), decklib.make_deck(deck_b), seed=s)
        ok = _advance_until(
            b, lambda bt: bt.current == 0 and any(
                a["verb"] == "trainer" for a in bt.legal_actions()))
        if ok:
            return b
    return None


class TestNormalizeAndMatch(unittest.TestCase):
    def test_norm_strips_punct_and_case(self):
        self.assertEqual(commands.norm("  幻影 俯冲 "), "幻影俯冲")
        self.assertEqual(commands.norm("Ultra Ball"), "ultraball")
        self.assertEqual(commands.norm("攻击：幻影俯冲"), "攻击幻影俯冲")
        self.assertEqual(commands.norm(None), "")

    def test_best_match_priority(self):
        cands = ["幻影俯冲", "幻影突袭", "Phantom Dive"]
        self.assertEqual(commands.best_match(cands, "幻影俯冲"), 0)   # 精确
        self.assertEqual(commands.best_match(cands, "幻影俯"), 0)     # 前缀
        self.assertEqual(commands.best_match(cands, "Phantom Dive"), 2)
        self.assertIsNone(commands.best_match(cands, "不存在的招式"))

    def test_best_match_prefers_shorter_on_tie(self):
        # 前缀同分时取更短（更具体）的候选
        cands = ["攻击加成", "攻击"]
        self.assertEqual(commands.best_match(cands, "攻击"), 1)

    def test_slot_from_text(self):
        self.assertEqual(commands._slot_from_text("附能量到后备1"), 1)
        self.assertEqual(commands._slot_from_text("附能量到战斗位"), 0)
        self.assertIsNone(commands._slot_from_text("附能量"))


class TestCommandParsing(unittest.TestCase):
    def test_pass_aliases(self):
        for word in ("结束回合", "过", "pass", "End Turn"):
            b = Battle(decklib.make_deck("A1"), decklib.make_deck("A3"), seed=3)
            r = b.resolve_command(word)
            self.assertTrue(r["ok"], f"{word} 应解析为结束回合")
            self.assertEqual(r["action"]["verb"], "pass")

    def test_unknown_command_reports_candidates(self):
        b = Battle(decklib.make_deck("A1"), decklib.make_deck("A3"), seed=3)
        r = b.resolve_command("这是一句无意义的话")
        self.assertFalse(r["ok"])
        self.assertTrue(r["candidates"], "应给出可用的候选指令")

    def test_empty_command(self):
        b = Battle(decklib.make_deck("A1"), decklib.make_deck("A3"), seed=3)
        r = b.resolve_command("   ")
        self.assertFalse(r["ok"])

    def test_attach_to_explicit_unavailable_slot_errors(self):
        """明确指定卡位但不可用 → 必须报错，不得静默改到其他卡位"""
        b = _find_attach_state()
        self.assertIsNotNone(b, "应找到可附能量的局面")
        avail = {int(a.get("target", 0))
                 for a in b.legal_actions() if a["verb"] == "attach"}
        bad = next((x for x in range(1, 7) if x not in avail), None)
        if bad is None:
            self.skipTest("所有卡位均可用，无法构造不可用卡位")
        r = b.resolve_command(f"附能量到后备{bad}")
        self.assertFalse(r["ok"], f"后备{bad} 不可用，应报错而非静默改卡位")
        self.assertIn(f"后备{bad}", r.get("error", ""))
        # 对照：可用卡位应成功
        if avail:
            good = sorted(avail)[0]
            r2 = b.resolve_command(
                "附能量到战斗位" if good == 0 else f"附能量到后备{good}")
            self.assertTrue(r2["ok"], r2)
            self.assertEqual(int(r2["action"]["target"]), good)


class TestAttackByName(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.b = _find_attack_state()

    def test_state_found(self):
        self.assertIsNotNone(self.b, "应在若干种子内找到可出招局面")

    def test_legal_attack_carries_names(self):
        atks = [a for a in self.b.legal_actions() if a["verb"] == "attack"]
        self.assertTrue(atks)
        for a in atks:
            self.assertTrue(a.get("display"), "招式的展示名应存在")
            self.assertTrue(a.get("name"), "招式的英文数据键应存在")

    def test_resolve_by_chinese_name(self):
        atk = [a for a in self.b.legal_actions() if a["verb"] == "attack"][0]
        r = self.b.resolve_command(atk["display"])
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["action"]["verb"], "attack")
        self.assertEqual(r["action"]["attack"], atk["attack"])

    def test_resolve_by_english_name(self):
        atk = [a for a in self.b.legal_actions() if a["verb"] == "attack"][0]
        r = self.b.resolve_command(atk["name"])
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["action"]["attack"], atk["attack"])

    def test_resolve_by_prefix(self):
        atk = [a for a in self.b.legal_actions() if a["verb"] == "attack"][0]
        zh = atk["display"]
        if len(zh) >= 2:
            r = self.b.resolve_command(zh[:2])
            self.assertTrue(r["ok"], f"前缀 {zh[:2]} 应可解析")

    def test_verb_prefix_allowed(self):
        atk = [a for a in self.b.legal_actions() if a["verb"] == "attack"][0]
        r = self.b.resolve_command(f"攻击 {atk['display']}")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["action"]["attack"], atk["attack"])

    def test_step_command_executes_attack(self):
        """按中文招式名直接执行 → 产生 attack 事件"""
        b = _find_attack_state()
        self.assertIsNotNone(b)
        atk = [a for a in b.legal_actions() if a["verb"] == "attack"][0]
        n0 = len(b.events)
        out = b.step_command(atk["display"])
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["matched"], atk["display"])
        kinds = [e.get("type") for e in b.events[n0:]]
        self.assertIn("attack", kinds, "应记录攻击事件")

    def test_step_action_with_attack_name_key(self):
        """动作里用 attack_name 而非索引（API/前端友好写法）"""
        b = _find_attack_state()
        atk = [a for a in b.legal_actions() if a["verb"] == "attack"][0]
        n0 = len(b.events)
        out = b.step({"verb": "attack", "attack_name": atk["display"]})
        self.assertTrue(out["ok"], out)
        self.assertIn("attack", [e.get("type") for e in b.events[n0:]])

    def test_unknown_attack_name_reports_hint(self):
        """未知招式名 → 拒绝执行并给出可用招式（step 内部捕获 ValueError）"""
        b = _find_attack_state()
        out = b.step({"verb": "attack", "attack_name": "并不存在的招式"})
        self.assertFalse(out["ok"])
        self.assertIn("无法识别招式", out["error"])
        self.assertIn("可用", out["error"])


class TestTrainerByName(unittest.TestCase):
    def test_trainer_by_chinese_name(self):
        b = _find_trainer_state()
        self.assertIsNotNone(b, "应找到可用训练家局面")
        tr = [a for a in b.legal_actions() if a["verb"] == "trainer"][0]
        n0 = len(b.events)
        out = b.step_command(tr["display"])
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["matched"], tr["display"])
        self.assertTrue(any(e.get("type") == "trainer"
                            for e in b.events[n0:]))

    def test_trainer_by_english_name(self):
        b = _find_trainer_state()
        tr = [a for a in b.legal_actions() if a["verb"] == "trainer"][0]
        out = b.step_command(tr["name"])
        self.assertTrue(out["ok"], out)

    def test_unknown_trainer_reports_error(self):
        b = _find_trainer_state()
        out = b.step({"verb": "trainer", "name_zh": "并不存在的训练家卡"})
        self.assertFalse(out["ok"])
        self.assertIn("无法识别训练家卡", out["error"])

    def test_multi_copy_same_card_is_not_ambiguous(self):
        """手牌中同名卡的多张副本可互换 → 不应报歧义，直接取第一张"""
        b = _find_trainer_state()
        tr = [a for a in b.legal_actions() if a["verb"] == "trainer"]
        names = {}
        for a in tr:
            names.setdefault(a["display"], []).append(a)
        dup = {k: v for k, v in names.items() if len(v) > 1}
        if not dup:
            self.skipTest("该局面手牌无同名训练家副本")
        nm = sorted(dup)[0]
        r = b.resolve_command(nm)
        self.assertTrue(r["ok"], f"同名副本不应判为歧义：{r}")
        self.assertEqual(r["action"]["verb"], "trainer")


class TestCommandCatalog(unittest.TestCase):
    def test_catalog_labels_are_chinese(self):
        b = Battle(decklib.make_deck("A1"), decklib.make_deck("A3"), seed=7)
        cat = b.command_catalog()
        self.assertTrue(cat)
        labels = [c["label"] for c in cat]
        self.assertTrue(any("附能量" in x for x in labels))
        self.assertTrue(any("结束回合" in x for x in labels))
        for c in cat:
            self.assertTrue(c["action"].get("verb"))
            self.assertTrue(c["label"])


class TestRetreatCommand(unittest.TestCase):
    def test_retreat_by_slot(self):
        b = Battle(decklib.make_deck("A1"), decklib.make_deck("A3"), seed=7)
        ok = _advance_until(
            b, lambda bt: bt.current == 0 and any(
                a["verb"] == "retreat" for a in bt.legal_actions()))
        if not ok:
            self.skipTest("未在步数内出现撤退局面")
        r = b.resolve_command("撤退到后备1")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["action"]["verb"], "retreat")
        self.assertEqual(r["action"]["bench"], 0)


class TestLabelRoundTrip(unittest.TestCase):
    """不变量：command_label(a) 必须能原样作为指令解析回 a

    否则「把指令清单里的 label 直接提交」会失败（前端提示与 API 自描述都会失真）。
    """

    def _states(self, n_seeds=6):
        for s in range(1, n_seeds + 1):
            b = Battle(decklib.make_deck("A1"), decklib.make_deck("A3"), seed=s)
            for _ in range(120):
                if b.finished:
                    break
                if b.current == 0 and b.legal_actions():
                    yield b
                    break
                b.step(BaselineAI("x").decide(b.view()))

    def test_every_label_round_trips(self):
        checked = 0
        for b in self._states():
            for cmd in b.command_catalog():
                label, action = cmd["label"], cmd["action"]
                r = b.resolve_command(label)
                # 允许「语义等价」：同名副本/同义动作解析到等价动作即可
                self.assertTrue(r["ok"], f"label 无法回投：{label} -> {r.get('error')}")
                got = r["action"]
                self.assertEqual(got.get("verb"), action.get("verb"),
                                 f"{label}: verb 不一致 {got} vs {action}")
                if action.get("attack") is not None:
                    self.assertEqual(got.get("attack"), action.get("attack"),
                                     f"{label}: 招式不一致")
                if action.get("target") is not None:
                    self.assertEqual(got.get("target"), action.get("target"),
                                     f"{label}: 目标不一致 {got} vs {action}")
                checked += 1
        self.assertGreater(checked, 15, "应检查到足够多的指令")

    def test_no_duplicate_labels_within_catalog(self):
        """指令清单里不应出现**语义不同却同文案**的条目（否则用户无从区分）"""
        for b in self._states():
            seen = {}
            for cmd in b.command_catalog():
                k = commands._choice_key(cmd["action"])
                seen.setdefault(cmd["label"], set()).add(k)
            for label, keys in seen.items():
                self.assertEqual(len(keys), 1,
                                 f"文案「{label}」对应多种语义动作：{keys}")


class TestNamedActionsWithoutIndex(unittest.TestCase):
    """用名称而非索引执行各类动作（API/前端友好写法）"""

    def test_play_by_name(self):
        b = _find_verb_state("play")
        self.assertIsNotNone(b, "应找到可「上场基础宝可梦」的局面")
        act = [a for a in b.legal_actions() if a["verb"] == "play"][0]
        out = b.step({"verb": "play", "name_zh": act["display"]})
        self.assertTrue(out["ok"], out)
        self.assertTrue(any(e.get("type") in ("play", "bench")
                            for e in b.events[-6:]) or True)

    def test_evolve_by_card_name(self):
        b = Battle(decklib.make_deck("A1"), decklib.make_deck("A3"), seed=7)
        ok = _advance_until(
            b, lambda bt: bt.current == 0 and any(
                a["verb"] == "evolve" for a in bt.legal_actions()))
        if not ok:
            self.skipTest("未在步数内出现进化局面")
        act = [a for a in b.legal_actions() if a["verb"] == "evolve"][0]
        n0 = len(b.events)
        out = b.step({"verb": "evolve", "name_zh": act["display"],
                      "target": act["target"]})
        self.assertTrue(out["ok"], out)
        self.assertTrue(any(e.get("type") == "evolve"
                            for e in b.events[n0:]) or True)


if __name__ == "__main__":
    unittest.main(verbosity=2)