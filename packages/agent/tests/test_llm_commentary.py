# -*- coding: utf-8 -*-
"""PokéLab Agent · 云端模型配置注入与对战解说用例

覆盖
  · 配置优先级：运行时注入（后台）> 环境变量；两者皆无 → 明确报错
  · 注入方抛异常/返回 None → 安全回退环境变量（不连带崩掉调用链）
  · 本地模式：外部端点被隐私策略拒绝（PrivacyError）
  · 解说：离线降级可用且带 C4 标识；不泄露对手手牌内容；角色（我方/对手）标注正确
  · 脱敏与审计：调用链必经 desensitize 与 audit

运行：python -m unittest tests.test_llm_commentary -v
"""
from __future__ import annotations
import json
import os
import tempfile
import unittest
from pathlib import Path

from agent import commentary as C
from agent import llmclient as L
from agent import privacy as P

ENV_KEYS = ("POKELAB_LLM_BASE", "POKELAB_LLM_KEY", "POKELAB_LLM_MODEL",
            "POKELAB_LLM_TIMEOUT", "POKELAB_LOCAL_ONLY", "POKELAB_AUDIT_LOG",
            "POKELAB_AUDIT")


class _EnvMixin(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in ENV_KEYS}
        for k in ENV_KEYS:
            os.environ.pop(k, None)
        L.clear_config_provider()

    def tearDown(self):
        L.clear_config_provider()
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class TestConfigPriority(_EnvMixin):
    def test_none_when_unconfigured(self):
        self.assertEqual(L.source(), "none")
        with self.assertRaises(L.LLMConfigError):
            L.config()

    def test_env_only(self):
        os.environ["POKELAB_LLM_BASE"] = "https://api.deepseek.com/v1/"
        os.environ["POKELAB_LLM_MODEL"] = "deepseek-chat"
        self.assertEqual(L.source(), "env")
        cfg = L.config()
        self.assertEqual(cfg["base"], "https://api.deepseek.com/v1")  # 去掉尾斜杠
        self.assertEqual(cfg["model"], "deepseek-chat")

    def test_runtime_wins_over_env(self):
        os.environ["POKELAB_LLM_BASE"] = "https://env.example/v1"
        os.environ["POKELAB_LLM_MODEL"] = "env-model"
        L.set_config_provider(lambda: {"base": "http://127.0.0.1:9/v1",
                                       "model": "rt-model", "key": "k",
                                       "timeout": 5})
        self.assertEqual(L.source(), "runtime")
        cfg = L.config()
        self.assertEqual(cfg["model"], "rt-model")
        self.assertEqual(cfg["base"], "http://127.0.0.1:9/v1")

    def test_provider_returning_none_falls_back_to_env(self):
        os.environ["POKELAB_LLM_BASE"] = "https://env.example/v1"
        L.set_config_provider(lambda: None)
        self.assertEqual(L.source(), "env")

    def test_provider_raising_falls_back_to_env(self):
        """注入方出错（如 DB 不可用）不得让调用链崩掉 → 回退 env"""
        os.environ["POKELAB_LLM_BASE"] = "https://env.example/v1"
        L.set_config_provider(lambda: (_ for _ in ()).throw(RuntimeError("db down")))
        self.assertEqual(L.source(), "env")

    def test_blank_base_from_provider_is_unconfigured(self):
        os.environ["POKELAB_LLM_BASE"] = "https://env.example/v1"
        L.set_config_provider(lambda: {"base": "   ", "model": "x"})
        self.assertEqual(L.source(), "env")


class TestComplianceOnChat(_EnvMixin):
    def test_local_only_blocks_external(self):
        os.environ["POKELAB_LOCAL_ONLY"] = "1"
        L.set_config_provider(lambda: {"base": "https://api.deepseek.com/v1",
                                       "model": "m", "key": "k", "timeout": 5})
        with self.assertRaises(P.PrivacyError):
            L.chat([{"role": "user", "content": "hi"}])

    def test_local_endpoint_allowed_but_fails_to_connect(self):
        """本机端点不被拦截；不可达时抛 LLMConfigError 并写审计（失败也审计）"""
        with tempfile.TemporaryDirectory() as d:
            os.environ["POKELAB_AUDIT_LOG"] = str(Path(d) / "a.jsonl")
            L.set_config_provider(lambda: {"base": "http://127.0.0.1:9/v1",
                                           "model": "m", "key": "", "timeout": 2})
            with self.assertRaises(L.LLMConfigError):
                L.chat([{"role": "user", "content": "hi"}])
            log = Path(d) / "a.jsonl"
            self.assertTrue(log.exists(), "失败调用也应写审计")
            rec = json.loads(log.read_text(encoding="utf-8").strip().splitlines()[-1])
            self.assertFalse(rec.get("ok"))
            self.assertNotIn("hi", log.read_text(encoding="utf-8"))


class TestCommentaryOffline(_EnvMixin):
    @staticmethod
    def _view():
        return {
            "me": 0, "turn": 12, "finished": False,
            "me_state": {"hand": [{"name": "a"}, {"name": "b"}],
                         "active": {"display": "电光鼠", "hp": 70, "dmg": 20,
                                    "energy": 2, "status": ["burn"]},
                         "bench": [{"display": "电花", "hp": 60, "dmg": 0,
                                    "energy": 0, "status": []}],
                         "prizes_left": 4, "deck_count": 30, "discard_count": 5},
            "opp_state": {"hand_count": 3,
                          "active": {"display": "火苗兽", "hp": 70, "dmg": 0,
                                     "energy": 1, "status": []},
                          "bench": [], "prizes_left": 6,
                          "deck_count": 28, "discard_count": 2},
        }

    @staticmethod
    def _events():
        return [
            {"type": "turn_start", "player": 0, "turn": 12},
            {"type": "attack", "player": 0, "turn": 12, "attack": "Spark Punch"},
            {"type": "damage", "player": 1, "turn": 12, "target": "火苗兽",
             "dmg": 30},
            {"type": "finish", "turn": 12, "winner": 0, "reason": "prizes"},
        ]

    def test_offline_always_available_with_marker(self):
        r = C.explain_turn(self._view(), self._events(), backend="auto")
        self.assertTrue(r["text"])
        self.assertTrue(r["ai_generated"], "C4：必须带 AI 生成标识")
        self.assertTrue(r["ai_notice"])
        self.assertEqual(r["generated_by"], "rule(offline)")
        self.assertIn("未启用云端模型", r["text"])

    def test_board_summary_hides_opponent_hand(self):
        s = C.board_summary(self._view())
        self.assertIn("手牌 ? 张", s)                 # 对手手牌只有数量
        self.assertIn("手牌 2 张", s)                 # 我方手牌可见
        self.assertIn("电光鼠", s)

    def test_event_roles_are_correct(self):
        """attack.player=攻击方，damage.player=受害方 —— 不能都说成「我方」"""
        lines = C.recent_events(self._events(), 4, my_side=0)
        self.assertIn("我方发动招式", lines)
        self.assertIn("对手宝可梦受到伤害", lines)   # damage 的 player=1=对手（受害方）

    def test_victim_role_flips_with_side(self):
        lines = C.recent_events(self._events(), 4, my_side=1)
        self.assertIn("对手发动招式", lines)
        self.assertIn("我方宝可梦受到伤害", lines)

    def test_llm_backend_without_config_falls_back(self):
        r = C.explain_turn(self._view(), self._events(), backend="llm")
        self.assertTrue(r["text"])
        self.assertTrue(r["errors"])
        self.assertIn("fallback", r["generated_by"])

    def test_llm_blocked_by_local_only(self):
        os.environ["POKELAB_LOCAL_ONLY"] = "1"
        L.set_config_provider(lambda: {"base": "https://api.deepseek.com/v1",
                                       "model": "m", "key": "k", "timeout": 5})
        r = C.explain_turn(self._view(), self._events(), backend="llm")
        self.assertEqual(r["generated_by"], "rule(privacy-blocked)")
        self.assertTrue(r["ai_generated"], "降级也必须带 AI 标识")
        self.assertTrue(r["errors"])


if __name__ == "__main__":
    unittest.main()
