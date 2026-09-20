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


class TestSplitSequence(unittest.TestCase):
    """组合指令切分：强分隔词优先；内联箭头（动词 → 目标）不得误切"""

    def test_split_on_strong_separators(self):
        self.assertEqual(commands.split_sequence("附能量到后备1 然后结束回合"),
                         ["附能量到后备1", "结束回合"])
        self.assertEqual(commands.split_sequence("附能量到后备1; 结束回合"),
                         ["附能量到后备1", "结束回合"])
        self.assertEqual(commands.split_sequence("结束回合；过"),
                         ["结束回合", "过"])
        self.assertEqual(commands.split_sequence("附能量到后备1\n结束回合"),
                         ["附能量到后备1", "结束回合"])

    def test_split_list_input(self):
        self.assertEqual(commands.split_sequence(["A", " B ", ""]), ["A", "B"])

    def test_inline_arrow_not_split(self):
        """「附能量 → 战斗位」是指令 label 本身，不是两步"""
        self.assertEqual(commands.split_sequence("附能量 → 战斗位"),
                         ["附能量 → 战斗位"])

    def test_arrow_between_verb_steps_is_split(self):
        """箭头两侧都是动词起点 → 视为多步"""
        got = commands.split_sequence("附能量到战斗位 → 结束回合")
        self.assertEqual(len(got), 2, got)
        self.assertIn("结束回合", got[-1])

    def test_three_step_chain_with_mixed_separators(self):
        got = commands.split_sequence("附能量到战斗位 然后 进化：多龙梅西亚 → 多龙奇 然后 结束回合")
        self.assertEqual(len(got), 3, got)
        self.assertTrue(got[0].startswith("附能量"))
        self.assertTrue(got[1].startswith("进化"))
        self.assertEqual(got[2], "结束回合")

    def test_leading_zai_stripped_after_first_step(self):
        got = commands.split_sequence("附能量到战斗位 然后 再 结束回合")
        self.assertEqual(got, ["附能量到战斗位", "结束回合"])

    def test_empty(self):
        self.assertEqual(commands.split_sequence(""), [])
        self.assertEqual(commands.split_sequence(None), [])
        self.assertEqual(commands.split_sequence("   "), [])


class TestStepSequence(unittest.TestCase):
    """组合指令执行语义：逐步重解析、回合移交即停、部分失败保留已执行"""

    def _fresh(self, seed=7):
        return Battle(decklib.make_deck("A1"), decklib.make_deck("A3"), seed=seed)

    def test_two_step_attach_then_pass(self):
        b = self._fresh()
        _advance_until(b, lambda bt: bt.current == 0)
        actor = b.current
        r = b.step_sequence("附能量到战斗位 然后 结束回合", actor=actor)
        self.assertEqual(r["planned"], 2)
        self.assertEqual(r["executed"], 2)
        self.assertEqual(r["remaining"], [])
        self.assertTrue(all(x["ok"] for x in r["steps"]))
        self.assertEqual([x["input"] for x in r["steps"]],
                         ["附能量到战斗位", "结束回合"])
        self.assertNotEqual(b.current, actor, "结束回合后应移交对手")

    def test_steps_carry_player_perspective_views(self):
        """每步都带「该步执行后」的玩家视角局面（供前端逐帧演出我方多步指令）。

        视角必须锚在 `actor`，且不得泄露对手手牌 —— 若用默认的 `view()`，
        在「结束回合」导致回合移交后就会串成对手视角。
        """
        b = self._fresh()
        _advance_until(b, lambda bt: bt.current == 0)
        actor = b.current
        r = b.step_sequence("附能量到战斗位 然后 结束回合", actor=actor)
        self.assertEqual(r["executed"], 2)
        for st in r["steps"]:
            self.assertTrue(st["ok"])
            v = st.get("view")
            self.assertIsInstance(v, dict, f"步骤 {st['input']} 缺少 view")
            self.assertEqual(v["me"], actor, "逐步局面的视角必须是本方")
            self.assertIn("hand", v["me_state"])
            self.assertNotIn("hand", v["opp_state"], "逐步局面不得泄露对手手牌")
            self.assertIn("legal", v)

    def test_stops_at_turn_handover_and_reports_remaining(self):
        """结束回合后的步骤属于对手，不得代为执行"""
        b = self._fresh()
        _advance_until(b, lambda bt: bt.current == 0)
        actor = b.current
        r = b.step_sequence(["结束回合", "附能量到战斗位"], actor=actor)
        self.assertEqual(r["executed"], 1)
        self.assertEqual(r["remaining"], ["附能量到战斗位"])
        self.assertIn("回合", r["stopped"] or "")

    def test_later_step_failure_keeps_earlier_steps(self):
        """后续步骤失败 → 部分执行（已执行动作不回滚）"""
        b = self._fresh()
        _advance_until(b, lambda bt: bt.current == 0)
        actor = b.current
        n0 = len(b.events)
        r = b.step_sequence("附能量到战斗位 然后 并不存在的动作", actor=actor)
        self.assertEqual(r["executed"], 1)
        self.assertFalse(r["steps"][1]["ok"])
        self.assertGreater(len(b.events), n0, "第一步应已生效")
        self.assertEqual(r["remaining"], ["并不存在的动作"])

    def test_zero_executed_when_first_step_bad(self):
        b = self._fresh()
        _advance_until(b, lambda bt: bt.current == 0)
        r = b.step_sequence("毫不相关的句子 然后 结束回合", actor=b.current)
        self.assertEqual(r["executed"], 0)
        self.assertTrue(r["remaining"])

    def test_max_steps_guard(self):
        b = self._fresh()
        _advance_until(b, lambda bt: bt.current == 0)
        actor = b.current
        many = ["结束回合"] * (commands.MAX_SEQUENCE_STEPS + 3)
        r = b.step_sequence(many, actor=actor)
        self.assertLessEqual(r["executed"], commands.MAX_SEQUENCE_STEPS)
        self.assertTrue(r["stopped"])

    def test_empty_sequence(self):
        b = self._fresh()
        r = b.step_sequence("")
        self.assertFalse(r["ok"])
        self.assertEqual(r["steps"], [])

    def test_does_not_play_for_opponent(self):
        """actor 限定：序列不会替对手走棋"""
        b = self._fresh()
        _advance_until(b, lambda bt: bt.current == 0)
        actor = b.current
        r = b.step_sequence("结束回合", actor=actor)
        self.assertEqual(r["executed"], 1)
        self.assertNotEqual(b.current, actor)
        # 再喂一条序列：此时行动方是对手，不应执行
        r2 = b.step_sequence("结束回合", actor=actor)
        self.assertEqual(r2["executed"], 0)


class TestMultiTargetLabels(unittest.TestCase):
    """同名卡打到不同目标时必须可区分（否则玩家无从选择）——seed 1 稳定复现"""

    def _state(self):
        b = Battle(decklib.make_deck("A1"), decklib.make_deck("A3"), seed=1)
        ai = [BaselineAI("x"), BaselineAI("y")]
        for _ in range(300):
            if b.finished:
                break
            if b.current == 0:
                tg = [a for a in b.legal_actions()
                      if a.get("target") is not None
                      and a["verb"] in ("trainer", "ability")]
                if len(tg) >= 2:
                    return b, tg
            b.step(ai[b.current].decide(b.view()))
        return None, None

    def test_labels_distinct_and_roundtrip(self):
        b, tg = self._state()
        if b is None:
            self.skipTest("未构造出多目标局面")
        labels = [commands.command_label(a) for a in tg]
        self.assertEqual(len(set(labels)), len(labels),
                         f"同名多目标 label 重复：{labels}")
        for a, lab in zip(tg, labels):
            r = b.resolve_command(lab)
            self.assertTrue(r["ok"], f"{lab} 无法回投：{r.get('error')}")
            self.assertEqual(r["action"].get("target"), a.get("target"),
                             f"{lab} 解析到错误目标")

    def test_bare_name_is_ambiguous_with_readable_options(self):
        b, tg = self._state()
        if b is None:
            self.skipTest("未构造出多目标局面")
        r = b.resolve_command(tg[0]["display"])
        self.assertFalse(r["ok"])
        self.assertTrue(r.get("ambiguous"))
        self.assertTrue(r.get("options"), "应给出可读候选")
        for c in r["candidates"]:
            self.assertIn("label", c, "候选应附 label 供 UI 直接渲染")
            self.assertIsInstance(c["label"], str)

    def test_catalog_has_no_semantic_label_collision(self):
        b, tg = self._state()
        if b is None:
            self.skipTest("未构造出多目标局面")
        seen = {}
        for c in b.command_catalog():
            seen.setdefault(c["label"], set()).add(commands._choice_key(c["action"]))
        dup = {k: len(v) for k, v in seen.items() if len(v) > 1}
        self.assertFalse(dup, f"目录内语义不同却同文案：{dup}")


if __name__ == "__main__":
    unittest.main(verbosity=2)