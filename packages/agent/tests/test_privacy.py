# -*- coding: utf-8 -*-
"""合规 6.2（数据隐私）与 6.1（AI 标识）用例

覆盖：脱敏规则 · 本地模式拦截 · 端点判定 · 调用审计 · 解析结果的 AI 标识 ·
英文卡面解析（真实卡池为英文）。
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))            # packages/agent
sys.path.insert(0, str(_HERE.parents[2] / "engine"))  # packages/engine

from agent import effects as E       # noqa: E402
from agent import privacy as P       # noqa: E402


class TestDesensitize(unittest.TestCase):
    """6.2：调用大模型前对敏感信息脱敏"""

    def test_email_phone_id_card_key(self):
        t = ("邮箱 a.b@example.com 手机 13812345678 证件 11010519491231002X "
             "卡号 4111 1111 1111 1111 key=sk-abcdefgh12345678")
        out, rep = P.desensitize(t)
        for leaked in ("a.b@example.com", "13812345678", "11010519491231002X",
                       "4111 1111 1111 1111", "sk-abcdefgh12345678"):
            self.assertNotIn(leaked, out, f"未脱敏：{leaked}")
        self.assertIn("[邮箱]", out)
        self.assertIn("[手机号]", out)
        self.assertGreaterEqual(rep["total"], 5)
        self.assertEqual(rep["chars_in"], len(t))

    def test_windows_path(self):
        t = r"路径 D:\Users\somebody\decks\a.txt 与 C:/Users/x/y"
        out, rep = P.desensitize(t)
        self.assertNotIn("somebody", out)
        self.assertIn("[本地路径]", out)
        self.assertEqual(rep["counts"].get("win_path"), 2)

    def test_extra_sensitive_words(self):
        t = "用户 张三 的卡组「我的龙组」"
        out, rep = P.desensitize(t, extra={"user": "张三", "deck": "我的龙组"})
        self.assertNotIn("我的龙组", out)
        self.assertIn("[用户信息]", out)
        self.assertGreaterEqual(rep["extra_hits"], 1)

    def test_no_false_positive_on_card_text(self):
        """卡面效果文本不应被误脱敏（否则会影响解析质量）"""
        t = ("Put 6 damage counters on your opponent's Benched Pokémon in any "
             "way you like. Search your deck for up to 2 Basic Pokémon with "
             "70 HP or less.")
        out, rep = P.desensitize(t)
        self.assertEqual(out, t)
        self.assertEqual(rep["total"], 0)

    def test_report_has_no_plaintext(self):
        t = "邮箱 leak@example.com"
        _, rep = P.desensitize(t)
        self.assertNotIn("leak@example.com", json.dumps(rep))


class TestLocalOnlyAndEndpoint(unittest.TestCase):
    """6.2：支持本地化部署（本地模式禁止任何外部调用）"""

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in
                       ("POKELAB_LOCAL_ONLY", "POKELAB_LLM_BASE")}
        for k in self._saved:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_is_external(self):
        self.assertTrue(P.is_external("https://api.deepseek.com/v1"))
        self.assertFalse(P.is_external("http://127.0.0.1:9997/v1"))
        self.assertFalse(P.is_external("http://localhost:8000"))
        self.assertFalse(P.is_external("http://192.168.1.9:8000/v1"))

    def test_local_only_blocks_external(self):
        os.environ["POKELAB_LOCAL_ONLY"] = "1"
        self.assertTrue(P.local_only())
        with self.assertRaises(P.PrivacyError):
            P.check_endpoint("https://api.deepseek.com/v1")
        # 本机端点仍允许
        self.assertFalse(P.check_endpoint("http://127.0.0.1:9997/v1")["external"])

    def test_parse_llm_blocked_falls_back_to_rule(self):
        """本地模式下 llm 后端被拦截 → 回退规则基线并如实报告（不抛异常）"""
        os.environ["POKELAB_LLM_BASE"] = "https://api.deepseek.com/v1"
        os.environ["POKELAB_LOCAL_ONLY"] = "1"
        r = E.parse_effects("造成 30 点伤害。", kind="attack", backend="llm")
        self.assertEqual(r["backend"], "rule(privacy-blocked)")
        self.assertTrue(r["errors"] and "本地模式" in r["errors"][0])
        self.assertTrue(r["specs"], "仍应产出规则基线结果")
        self.assertTrue(r["ai_generated"])


class TestAudit(unittest.TestCase):
    """6.2：外部调用可审计（不记录原文）"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._saved = {k: os.environ.get(k) for k in
                       ("POKELAB_AUDIT", "POKELAB_AUDIT_LOG")}
        os.environ["POKELAB_AUDIT_LOG"] = str(Path(self.tmp.name) / "a.jsonl")
        os.environ.pop("POKELAB_AUDIT", None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def test_write_and_read(self):
        P.audit("llm_call", host="api.example.com", model="m1", ok=True,
                prompt_chars=100, sent_chars=80, desensitized=3)
        got = P.read_audit(10)
        self.assertTrue(got["exists"])
        self.assertEqual(got["total"], 1)
        e = got["entries"][0]
        self.assertEqual(e["event"], "llm_call")
        self.assertEqual(e["host"], "api.example.com")
        self.assertNotIn("prompt", e)          # 不记录原文
        self.assertNotIn("content", e)

    def test_disable(self):
        os.environ["POKELAB_AUDIT"] = "0"
        self.assertFalse(P.audit_enabled())
        P.audit("x", a=1)
        self.assertFalse(P.read_audit(5)["exists"])


class TestAiLabeling(unittest.TestCase):
    """6.1：AI/机器生成内容必须带显著标识"""

    def test_parse_effects_has_ai_fields(self):
        r = E.parse_effects("造成 50 点伤害。", kind="attack", name="T")
        self.assertTrue(r["ai_generated"])
        self.assertIn(r["generated_by"], ("rule", "llm"))
        self.assertTrue(r["ai_notice"])
        self.assertIn("研究参考", r["ai_notice"])

    def test_ai_notice_mentions_official_rules(self):
        r = E.parse_effects("造成 10 点伤害。", kind="attack")
        self.assertIn("官方规则", r["ai_notice"])


class TestEnglishRuleParser(unittest.TestCase):
    """真实卡池为**英文卡面**：规则后端须能解析英文句式"""

    def _types(self, text, kind="attack"):
        return [a["type"] for a in E.parse_rule(text, kind=kind)["actions"]]

    def test_damage(self):
        self.assertIn("damage", self._types("This attack does 200 damage."))

    def test_spread_with_free_distribute(self):
        r = E.parse_rule("Put 6 damage counters on your opponent's Benched "
                         "Pokémon in any way you like.", kind="attack")
        a = r["actions"][0]
        self.assertEqual(a["type"], "spread")
        self.assertEqual(a["amount"], 60)                 # 6 指示物 = 60 点
        self.assertTrue(a["extra"]["free_distribute"])
        self.assertEqual(a["target"], "active_opponent_bench")

    def test_status_target_and_engine_tag(self):
        r = E.parse_rule("Your opponent's Active Pokémon is now Asleep.",
                         kind="attack")
        act = r["actions"][0]
        self.assertEqual(act["type"], "status")
        self.assertEqual(act["extra"]["status"], "sleep")
        self.assertEqual(act["target"], "active_opponent")
        self.assertEqual(E.to_engine_tags([r])["tags"], ["sleep"])

    def test_discard_energy_vs_cards(self):
        self.assertTrue(any(a.get("extra", {}).get("card_type") == "energy"
                            for a in E.parse_rule(
                                "Discard 2 Energy from this Pokémon.",
                                kind="attack")["actions"]))
        self.assertTrue(any(a["type"] == "discard" and a["target"] == "hand"
                            for a in E.parse_rule(
                                "Discard 2 cards from your hand.",
                                kind="item")["actions"]))

    def test_search_and_draw_and_heal(self):
        self.assertIn("search", self._types(
            "Search your deck for a Pokémon, reveal it.", "item"))
        self.assertIn("draw", self._types("Draw 3 cards.", "item"))
        self.assertIn("heal", self._types("Heal 30 damage from this Pokémon.",
                                          "attack"))

    def test_triggers(self):
        self.assertEqual(E.parse_rule(
            "Once during your turn, you may put 1 damage counter on a Pokémon.",
            kind="ability")["trigger"], "static")
        self.assertEqual(E.parse_rule(
            "When your Pokémon is Knocked Out, draw a card.",
            kind="ability")["trigger"], "on_knockout")
        self.assertEqual(E.parse_rule("This attack does 10 damage.",
                                      kind="attack")["trigger"], "on_use")

    def test_once_per_turn_limit(self):
        r = E.parse_rule("Once during your turn, draw a card.", kind="ability")
        self.assertTrue(any(c.get("value") == "once_per_turn"
                            for c in r["conditions"]))

    def test_chinese_still_works(self):
        r = E.parse_rule("造成 30 点伤害。对手的战斗宝可梦陷入睡眠。",
                         kind="attack")
        types = [a["type"] for a in r["actions"]]
        self.assertIn("damage", types)
        self.assertIn("status", types)


if __name__ == "__main__":
    unittest.main(verbosity=2)
