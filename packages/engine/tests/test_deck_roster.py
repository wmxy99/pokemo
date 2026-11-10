# -*- coding: utf-8 -*-
"""卡组花名册守卫：前端下拉里的每一套，都必须**真的能打**

背景（2026-11-10）：对战下拉此前混着 7 套 `decklib` 的**合成演示卡组**
（draco / lightning / fire / psychic / ability / grass / status）—— 卡名是自造的
（Seedling「幼芽」、Leaf Cutter「叶刃」），卡库里查不到，玩家点开卡面会以为
数据出错；而真正由 engine_ready 卡池组建的只有 A1–A4 + B1–B5 九套，且**一套都
没有特殊能量**。

现在下拉只留 10 套真实卡组（A1–A4 元游戏锚点 + B1–B5 五属性实例 + C1 钢系特殊
能量）。这个文件把「10 套 · 60 张 · 全部可执行 · 至少一套带特殊能量」变成断言，
避免以后再加卡组时出现「下拉选得到、建不出局」或「悄悄用基础能量占位」。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from engine import decklib, reallib

REPO = Path(__file__).resolve().parents[3]
API_JS = REPO / "apps" / "web" / "src" / "api.js"

# 已从下拉移除的合成演示卡组（引擎侧保留：K1 训练/验收用 lightning 做固定基准）
DEMO_IDS = {"lightning", "grass", "fire", "psychic", "status", "draco", "ability"}


def frontend_decks() -> list:
    """从前端 `api.js` 的 `export const DECKS = [...]` 提取 (id, label)"""
    src = API_JS.read_text(encoding="utf-8")
    m = re.search(r"export const DECKS = \[(.*?)\n\]", src, re.S)
    if not m:
        raise AssertionError("api.js 里找不到 `export const DECKS = [` 块")
    rows = re.findall(r"\{\s*id:\s*'([^']+)'\s*,\s*label:\s*'([^']*)'", m.group(1))
    return rows


class TestDeckRoster(unittest.TestCase):
    """前端下拉 = 10 套真实卡组，且每套都能建出 60 张无占位"""

    def test_exactly_ten_real_decks(self):
        decks = frontend_decks()
        self.assertEqual(len(decks), 10,
                         f"下拉应为 10 套真实卡组，实际 {len(decks)}：{[d[0] for d in decks]}")

    def test_no_demo_decks_in_dropdown(self):
        ids = {i for i, _ in frontend_decks()}
        leaked = ids & DEMO_IDS
        self.assertFalse(leaked,
                         f"合成演示卡组仍在拉下里：{sorted(leaked)}（卡库里查不到这些卡名）")

    def test_every_deck_builds_60_cards(self):
        for did, label in frontend_decks():
            with self.subTest(deck=did):
                try:
                    deck = decklib.make_deck(did)
                except Exception as e:                        # noqa: BLE001
                    self.fail(f"{did}（{label}）建不出卡组：{type(e).__name__}: {e}")
                self.assertEqual(len(deck), 60, f"{did} 不是 60 张：{len(deck)}")

    def test_every_deck_is_fully_executable(self):
        """不得有卡被 fallback 成基础能量占位（那等于「看起来能打、实际换了卡」）"""
        for did, label in frontend_decks():
            with self.subTest(deck=did):
                cov = reallib.coverage(did)
                self.assertEqual(cov["replaced"], [],
                                 f"{did}（{label}）有卡被占位替换：{cov['replaced']}")
                self.assertGreaterEqual(cov["executable_rate"], 1.0,
                                        f"{did} 可执行率 {cov['executable_rate']}")

    def test_every_deck_has_enough_basics(self):
        """起手必须有基础宝可梦 —— 少于 8 张时 mulligan 会频繁，对局体验崩"""
        for did, _label in frontend_decks():
            with self.subTest(deck=did):
                n = 0
                for ref in decklib.make_deck(did):
                    pdef = getattr(ref, "pdef", None)
                    if pdef is not None and getattr(pdef, "stage", 0) == 0:
                        n += 1
                self.assertGreaterEqual(n, 8, f"{did} 只有 {n} 张基础宝可梦")

    def test_at_least_one_deck_has_special_energy(self):
        """至少一套带特殊能量（用户明确要求；官方卡组普遍会用 ACE SPEC 能量）"""
        found = {}
        for did, _label in frontend_decks():
            kinds = []
            for ref in decklib.make_deck(did):
                edef = getattr(ref, "edef", None)
                tag = str(getattr(edef, "tag", "") or "")
                if tag.startswith("senergy:"):
                    kinds.append(tag.split(":", 1)[1])
            if kinds:
                found[did] = kinds
        self.assertTrue(found, "10 套里没有任何一套使用特殊能量")
        # C1 是专门为特殊能量建的：必须真有，且种类可核对
        self.assertIn("C1", found, f"特殊能量卡组 C1 不在列表里或没有特殊能量：{found}")
        self.assertIn("neo_upper", found["C1"],
                      f"C1 的特殊能量应为 Neo Upper（2 阶提供任意 2 点），实际 {found['C1']}")

    def test_deck_labels_have_chinese_names(self):
        """每套都要有中文展示名（日志/对战标题会用它）"""
        for did, _label in frontend_decks():
            with self.subTest(deck=did):
                zh = decklib.deck_label_zh(did)
                self.assertTrue(zh and zh != did, f"{did} 没有中文展示名")


if __name__ == "__main__":
    unittest.main(verbosity=2)
