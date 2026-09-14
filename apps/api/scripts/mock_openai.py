# -*- coding: utf-8 -*-
"""本地 OpenAI 兼容 mock 服务（**仅用于验证云端调用链**，不参与生产）

为什么要它：后台配置云端模型的「成功路径」（真实外发 → 解析返回 → 落库/展示）
无法用公网服务在离线环境验证。本 mock 起在 127.0.0.1，返回标准
``/chat/completions`` 响应，用于端到端验证：
  · POST /admin/llm/test        → ok=true
  · POST /battles/{id}/commentary → generated_by=llm
  · K2 parse_effects(backend=llm) → backend=llm

用法：
    python apps/api/scripts/mock_openai.py --port 9099
"""
from __future__ import annotations

import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):        # 静音（避免污染测试输出）
        pass

    def do_POST(self):                # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode("utf-8") if n else "{}"
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {}
        msgs = body.get("messages") or []
        user = "\n".join(str(m.get("content") or "") for m in msgs
                         if m.get("role") == "user")
        if "chat/completions" not in self.path:
            return self._send(404, {"error": {"message": "not found"}})
        # 按提示词内容返回可被解析的合法结果（模拟真实模型的两种用途）
        if "效果" in user or "JSON" in user or "spec" in user or "规则" in user:
            content = json.dumps([{
                "kind": "attack", "name": "mock", "trigger": "on_use",
                "conditions": [],
                "actions": [{"type": "damage", "amount": 42,
                             "target": "active_opponent"}],
            }], ensure_ascii=False)
        elif "解说" in user or "回合" in user:
            content = "（mock 模型）第若干回合，双方平稳过牌，节奏偏慢。"
        else:
            content = "连通正常"
        payload = {
            "id": "chatcmpl-mock", "object": "chat.completion",
            "model": body.get("model") or "mock-model",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": len(user) // 4,
                      "completion_tokens": len(content) // 4,
                      "total_tokens": (len(user) + len(content)) // 4},
        }
        self._send(200, payload)

    def _send(self, code: int, obj: dict):
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9099)
    a = ap.parse_args()
    srv = HTTPServer((a.host, a.port), Handler)
    print(f"[mock-openai] listening http://{a.host}:{a.port}/v1", flush=True)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
