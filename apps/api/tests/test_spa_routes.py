# -*- coding: utf-8 -*-
"""前端单页应用（SPA）**深链接回退**守卫

背景
----
前端从「按钮切 state」改成真路由后，`/app/battles/<id>`、`/app/library?q=…`
这类地址在磁盘上**没有同名文件**。若后端仍只挂 `StaticFiles(html=True)`，
浏览器一刷新深链接就 404 —— 表现是「地址栏能进、刷新白屏、发给别人打不开」，
而前端单测/构建全绿，只有人工点浏览器才发现。

本组测试把三条性质钉死：
  1. 深链接（无后缀路径）→ 200 + index.html（应用外壳）；
  2. 磁盘上真实存在的产物 → 按文件返回（**不能**变成 HTML）；
  3. **不存在的 `.js` 仍要真 404** —— 若把带后缀的路径也回退成 index.html，
     浏览器会把 HTML 当脚本执行（Mismatched tag），且构建产物缺失被彻底掩盖。

负向自测（已做）
----------------
把 `_spa_entry` 里的「带后缀即 404」分支去掉，`test_missing_asset_is_404_not_index`
必须变红；还原后即绿。守卫写坏了会永远绿，这一步不能省。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

# 与 test_api 一致：把 SQLite 指到临时目录，避免污染开发库
os.environ.setdefault("POKELAB_DATA_DIR", tempfile.mkdtemp(prefix="pokelab_spa_test_"))

from fastapi.testclient import TestClient  # noqa: E402

from apps.api.app import main as api_main  # noqa: E402

client = TestClient(api_main.app)
DIST = api_main._WEB_DIST          # None = 还没构建前端


@unittest.skipIf(DIST is None, "apps/web/dist 未构建 → 无静态产物可验")
class SpaDeepLinkTest(unittest.TestCase):
    def test_deep_link_returns_app_shell(self):
        """无后缀路径一律返回应用外壳（由前端路由解析）"""
        for p in ("/app", "/app/", "/app/battles/abc123", "/app/batch/job-1",
                  "/app/library?q=1", "/app/train/t-9"):
            r = client.get(p)
            self.assertEqual(r.status_code, 200, f"{p} 应回退到 index.html")
            self.assertIn("text/html", r.headers.get("content-type", ""), p)
            self.assertIn('id="root"', r.text, f"{p} 返回的不是应用外壳")

    def test_real_asset_served_as_file(self):
        """真实产物按文件返回（内容类型不能是 html）"""
        assets = sorted((DIST / "assets").glob("*.js"))
        self.assertTrue(assets, "构建产物里没有 assets/*.js")
        rel = assets[0].relative_to(DIST).as_posix()
        r = client.get(f"/app/{rel}")
        self.assertEqual(r.status_code, 200, rel)
        self.assertNotIn("text/html", r.headers.get("content-type", ""))

    def test_missing_asset_is_404_not_index(self):
        """缺失的 .js 必须真 404 —— 回退成 HTML 会被浏览器当脚本执行"""
        r = client.get("/app/assets/definitely-missing-xyz.js")
        self.assertEqual(r.status_code, 404,
                         "缺失产物回退成了 index.html：浏览器会把 HTML 当 JS 执行")

    def test_traversal_blocked(self):
        """路径穿越不得读到仓库外的文件"""
        r = client.get("/app/%2e%2e/%2e%2e/README.md")
        self.assertNotEqual(r.status_code, 200, "路径穿越未被拦住")


if __name__ == "__main__":
    unittest.main()
