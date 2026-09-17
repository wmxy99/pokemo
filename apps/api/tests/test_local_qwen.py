# -*- coding: utf-8 -*-
"""本地 Qwen3-4B 量化服务 · 测试

分两档：
  · **常跑**：脚本存在性 / 语法 / 配置解析（不加载模型，毫秒级）；
  · **重档**：真正加载 8bit 模型并跑生成（约 20s 加载 + 若干秒生成）——
    默认**跳过**，因为常规测试套件不应被 4GB 权重的推理拖慢。

启用重档：
    POKELAB_TEST_LOCAL_QWEN=1 python -m unittest tests.test_local_qwen -v

注意：重档会占用显存（8bit ≈4.11GB），**与 RL 训练/其它推理不可同时跑**。
"""
from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve()
API_DIR = HERE.parents[1]                      # apps/api
SCRIPTS = API_DIR / "scripts"

HEAVY = os.environ.get("POKELAB_TEST_LOCAL_QWEN") == "1"

# 模型目录必须存在，重档才有意义
_MODEL_OK = bool(os.environ.get("POKELAB_QWEN_PATH")) and \
    Path(os.environ["POKELAB_QWEN_PATH"]).exists()


def _load_module():
    """按文件路径加载 scripts/local_qwen.py（scripts/ 不是包）"""
    spec = importlib.util.spec_from_file_location(
        "local_qwen_under_test", SCRIPTS / "local_qwen.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["local_qwen_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


class TestLocalQwenScripts(unittest.TestCase):
    """常跑档：不加载模型"""

    def setUp(self):
        self.LQ = _load_module()

    def test_scripts_exist_and_compile(self):
        for name in ("local_qwen.py", "qwen_precision.py"):
            p = SCRIPTS / name
            self.assertTrue(p.is_file(), f"缺少脚本 {name}")
            compile(p.read_text(encoding="utf-8"), str(p), "exec")

    def test_default_model_path_overridable(self):
        old = os.environ.get("POKELAB_QWEN_PATH")
        try:
            os.environ["POKELAB_QWEN_PATH"] = r"X:\fake\model"
            self.assertEqual(self.LQ.model_path(), r"X:\fake\model")
        finally:
            if old is None:
                os.environ.pop("POKELAB_QWEN_PATH", None)
            else:
                os.environ["POKELAB_QWEN_PATH"] = old

    def test_quant_mode_default_is_8bit(self):
        """默认必须是 8bit —— ADR-LLM-001 的精度取舍，不允许被悄悄改成 4bit"""
        old = os.environ.pop("POKELAB_QWEN_QUANT", None)
        try:
            self.assertEqual(self.LQ.quant_mode(), "8bit")
        finally:
            if old is not None:
                os.environ["POKELAB_QWEN_QUANT"] = old

    def test_thinking_flag_parsing(self):
        """Qwen3 思考段默认关闭（K2 要严格 JSON，思考段是干扰）"""
        old = os.environ.get("POKELAB_QWEN_THINK")
        try:
            for val, expect in (("0", False), ("", False), ("false", False),
                                ("1", True), ("true", True)):
                os.environ["POKELAB_QWEN_THINK"] = val
                self.assertEqual(self.LQ.thinking_enabled(), expect,
                                 f"POKELAB_QWEN_THINK={val!r} 解析错误")
        finally:
            if old is None:
                os.environ.pop("POKELAB_QWEN_THINK", None)
            else:
                os.environ["POKELAB_QWEN_THINK"] = old

    def test_missing_model_dir_fails_loudly(self):
        """模型目录不存在时必须明确报错（不能静默走到别处或崩溃成难懂异常）"""
        old = os.environ.get("POKELAB_QWEN_PATH")
        try:
            os.environ["POKELAB_QWEN_PATH"] = r"X:\definitely\not\here"
            with self.assertRaises(SystemExit) as cm:
                self.LQ.load(quant="8bit", verbose=False)
            self.assertIn("模型目录不存在", str(cm.exception))
        finally:
            if old is None:
                os.environ.pop("POKELAB_QWEN_PATH", None)
            else:
                os.environ["POKELAB_QWEN_PATH"] = old

    def test_chat_endpoint_declares_request_body(self):
        """回归守卫：/v1/chat/completions 的请求体必须被识别为 **body**

        这是踩过的真 bug：模块有 `from __future__ import annotations`，而请求模型
        原先定义在 `make_app()` **函数内部** → FastAPI 用模块全局命名空间解析注解时
        找不到该类 → 认不出是 Pydantic 模型 → 退化为**查询参数** → 一切调用返回
        422 `{"loc":["query","req"],"msg":"Field required"}`。
        症状是「服务起得来、/healthz 正常，但一调用就 422」，极易误判成客户端问题。

        本用例只读 OpenAPI schema（**不加载模型、不占 GPU**），毫秒级。
        """
        app = self.LQ.make_app()
        post = app.openapi()["paths"]["/v1/chat/completions"]["post"]
        self.assertIn("requestBody", post,
                      "请求体未被声明为 body（会退化成查询参数 → 422）")
        ref = (post["requestBody"]["content"]["application/json"]["schema"]
               .get("$ref", ""))
        self.assertTrue(ref.endswith("ChatReq"), f"请求体 schema 异常：{ref}")
        names = [p.get("name") for p in post.get("parameters", [])]
        self.assertNotIn("req", names,
                         "req 被当成了查询参数 —— 注解解析失败（见本用例说明）")


@unittest.skipUnless(HEAVY and _MODEL_OK,
                     "重档测试默认跳过：设 POKELAB_TEST_LOCAL_QWEN=1 并确保模型目录存在")
class TestLocalQwenHeavy(unittest.TestCase):
    """重档：真加载 8bit 模型"""

    @classmethod
    def setUpClass(cls):
        cls.LQ = _load_module()
        cls.model, cls.tok = cls.LQ.load(quant="8bit", verbose=False)

    def test_footprint_within_budget(self):
        """8bit footprint 必须显著低于 bf16 原版，且能装进 6GB 卡"""
        fp = self.model.get_memory_footprint() / 1024 ** 3
        self.assertLess(fp, 5.0, f"footprint {fp:.2f}GB 超出预期（6GB 卡余量不足）")

    def test_lm_head_kept_high_precision(self):
        """输出层必须未被量化 —— 这是 8bit 精度可信的关键一环"""
        import torch
        self.assertIs(type(self.model.lm_head), torch.nn.Linear,
                      "lm_head 被量化了：logits 精度会受 int8 误差影响")
        self.assertEqual(self.model.lm_head.weight.dtype, torch.bfloat16)

    def test_generation_is_deterministic(self):
        """贪心解码必须可复现（精度对照的前提）"""
        msgs = [{"role": "user", "content": "只回答两个字：正常。"}]
        a = self.LQ.generate(self.model, self.tok, msgs, max_new_tokens=8)
        b = self.LQ.generate(self.model, self.tok, msgs, max_new_tokens=8)
        self.assertEqual(a["content"], b["content"])

    def test_openai_compatible_endpoint(self):
        """服务返回必须能被 agent.llmclient 消费（choices[0].message.content）"""
        from fastapi.testclient import TestClient
        app = self.LQ.make_app()
        with TestClient(app) as c:
            r = c.post("/v1/chat/completions",
                       json={"messages": [{"role": "user", "content": "你好"}],
                             "max_tokens": 8, "temperature": 0})
            self.assertEqual(r.status_code, 200, r.text)
            d = r.json()
            self.assertIn("choices", d)
            self.assertIsInstance(d["choices"][0]["message"]["content"], str)
            self.assertIn("usage", d)
            self.assertEqual(c.get("/v1/models").json()["object"], "list")


if __name__ == "__main__":
    unittest.main(verbosity=2)
