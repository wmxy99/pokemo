# -*- coding: utf-8 -*-
"""前端卡组下拉 ↔ 引擎卡组注册表 一致性守卫

背景
----
对战页的下拉选项来自前端硬编码 ``apps/web/src/api.js`` 的 ``DECKS`` 数组，
后端建局走 ``apps/api/app/battles.py::_resolve_deck`` → ``engine.decklib.make_deck``。
**两侧各写一份 id 列表** → 一旦前端写了后端没注册的 id（例如历史上把
``ability``/``status`` 误写成 ``ability_demo``/``status_demo``），
前端下拉能选、建局必然 400/500，表现为「选卡组就炸」。

这类错没有任何单测能拦住（前端 JS 不参与 Python 测试，后端也不读前端列表），
所以在这里补一条**跨栈对账**：把前端 ``DECKS`` 的 id 全量拉出来，逐个喂给
``decklib.make_deck``，任何 KeyError 都必须报错。

负向自测
--------
``test_guard_detects_unknown_id`` 用一个故意编造的 id 验证守卫**确实会报**，
避免「守卫本身写坏了 → 永远绿」这种假安全。
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
# engine 包在 packages/engine 下（与 apps.api.app.main 的注入方式一致）
sys.path.insert(0, str(REPO / "packages" / "engine"))

API_JS = REPO / "apps" / "web" / "src" / "api.js"

# 前端 DECKS 条目形如：  { id: 'A1', label: '…' }
_ID_RE = re.compile(r"\{\s*id:\s*'([^']+)'\s*,")


def _frontend_deck_ids() -> list:
    """从 api.js 里抽出 DECKS 数组的全部 id（按出现顺序，去重）"""
    src = API_JS.read_text(encoding="utf-8")
    m = re.search(r"export const DECKS\s*=\s*\[(.*?)\n\]", src, re.S)
    assert m, "api.js 里找不到 DECKS 数组（下拉选项来源）"
    seen, out = set(), []
    for raw in _ID_RE.findall(m.group(1)):
        if raw not in seen:
            seen.add(raw)
            out.append(raw)
    return out


def _short_deck_keys() -> list:
    """shortDeck 映射表的键（展示名回退表）"""
    src = API_JS.read_text(encoding="utf-8")
    m = re.search(r"export function shortDeck\(id\)\s*\{(.*?)\n\}", src, re.S)
    assert m, "api.js 里找不到 shortDeck"
    body = re.search(r"const m = \{(.*?)\}", m.group(1), re.S)
    assert body, "shortDeck 里找不到映射表"
    return re.findall(r"([A-Za-z0-9_]+)\s*:", body.group(1))


class TestWebDeckIdConsistency(unittest.TestCase):
    """前端下拉 id ↔ 引擎 make_deck 注册表"""

    def test_every_frontend_deck_id_is_buildable(self):
        """前端 DECKS 的每个 id 都必须能被引擎建出来（否则建局必炸）"""
        from engine import decklib
        ids = _frontend_deck_ids()
        self.assertGreaterEqual(len(ids), 5, "DECKS 解析异常，条目过少")
        broken = []
        for did in ids:
            try:
                deck = decklib.make_deck(did)
            except Exception as e:                       # noqa: BLE001
                broken.append(f"{did}（{type(e).__name__}）")
                continue
            if not deck:
                broken.append(f"{did}（构建结果为空）")
        self.assertEqual(broken, [], "前端下拉里有后端建不出的卡组 id：" + "、".join(broken))

    def test_short_deck_keys_are_subset_of_decks(self):
        """shortDeck 的键不能是 DECKS 里没有的 id（否则展示名回退查不到）"""
        ids = set(_frontend_deck_ids())
        orphan = [k for k in _short_deck_keys() if k not in ids]
        self.assertEqual(orphan, [], "shortDeck 里有 DECKS 未定义的 id：" + "、".join(orphan))

    def test_guard_detects_unknown_id(self):
        """负向自测：守卫对未知 id 确实会报（防止守卫本身写坏 → 永远绿）"""
        from engine import decklib
        with self.assertRaises(KeyError):
            decklib.make_deck("__不存在的卡组id__")


if __name__ == "__main__":
    unittest.main()
