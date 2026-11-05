# -*- coding: utf-8 -*-
"""运行档案一致性守卫（2026-11-05）

运行档案是**跨会话的长期记忆**。它最容易在三种情况下悄悄失真：

1. **数据与 DB 脱节**：某张卡被别的批次顺带解禁后仍留在「待办」清单里，
   或清单里写了库里根本不存在的名字 → 后续批次对着幻影推进。
2. **重复计槽**：同一张卡被两个批次都计入「计划张数」，导致进度永远算不平
   （跨批卡是**已知例外**，必须显式登记在 `cross_batch`）。
3. **档案被手改**：直接编辑 Markdown 而不改 JSON，两边逐渐分叉。

本文件把这三件事变成可执行的断言：

  · JSON ↔ DB：每张在册卡必须存在；`done` 必须已 ready；`todo` 必须未 ready；
    结构性边界 / 待新能力不得同时出现在批次里。
  · 口径闭合：批次内待做 + 结构性 + 待新能力 == 未 ready 卡数（差 1 就报）。
  · 渲染一致：`render_runbook.py --check` 必须通过（档案 == 数据 + 实测）。

刻意**不做**的事：自动改写档案。守卫只报警不改写 —— 否则人为笔误会被当成
「正确」写进文档，守卫退化成橡皮图章。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import render_runbook as R  # noqa: E402

DATA = R.DATA
DB = R.DB
RUNBOOK = R.RUNBOOK
PHASES = {"P1", "P2", "P3", "P4", "P5"}
FIRST_BATCH, LAST_BATCH = 90, 133


def _norm(s: str) -> str:
    return re.sub(r"[\u2018\u2019]", "'", str(s)).lower().strip()


def _key(c: dict) -> tuple:
    return (_norm(c["name"]), c["series"], str(c["number"]))


class RunbookFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(DATA.read_text(encoding="utf-8"))
        cards = json.loads(DB.read_text(encoding="utf-8"))["cards"]
        cls.db = {_key(c): c for c in cards}
        cls.unready = {k for k, c in cls.db.items() if not c.get("engine_ready")}
        cls.batches = cls.data["batches"]


class TestStructure(RunbookFixture):
    def test_batch_ids_contiguous(self):
        ids = [b["id"] for b in self.batches]
        self.assertEqual(ids, sorted(ids), "批次必须按编号升序")
        self.assertEqual(ids, list(range(FIRST_BATCH, LAST_BATCH + 1)),
                         f"批次必须覆盖 {FIRST_BATCH}–{LAST_BATCH} 且无缺号")
        self.assertEqual(len(ids), 44, "档案规划为 44 批")

    def test_every_batch_has_phase_and_theme(self):
        for b in self.batches:
            self.assertIn(b["phase"], PHASES, f"批 {b['id']} 阶段非法：{b['phase']}")
            self.assertTrue(str(b.get("theme") or "").strip(),
                            f"批 {b['id']} 缺主题")

    def test_status_values(self):
        for b in self.batches:
            self.assertIn(b["status"], ("todo", "done"),
                          f"批 {b['id']} 状态非法：{b['status']}")
            if b["status"] == "done":
                self.assertIsNotNone(b["actual_pool"],
                                     f"批 {b['id']} 标 done 但未填 actual_pool")
                self.assertIsNotNone(b["actual_refused"],
                                     f"批 {b['id']} 标 done 但未填 actual_refused")


class TestCardsExistInDb(RunbookFixture):
    def test_no_ghost_cards(self):
        """卡名 + 系列 + 编号必须能在 DB 里查到（同名不同印刷靠编号区分）"""
        ghosts = [_key(c) for b in self.batches for c in b["cards"]
                  if _key(c) not in self.db]
        self.assertEqual(ghosts, [], f"清单里存在库中没有的卡：{ghosts}")

    def test_structural_and_deferred_exist(self):
        for group in ("structural", "deferred"):
            for c in self.data[group]:
                self.assertIn(_key(c), self.db,
                              f"{group} 中的卡不在库中：{_key(c)}")


class TestStatusMatchesDb(RunbookFixture):
    def test_done_batches_are_ready(self):
        for b in self.batches:
            if b["status"] != "done":
                continue
            for c in b["cards"]:
                card = self.db[_key(c)]
                self.assertTrue(card.get("engine_ready"),
                                f"批 {b['id']} 已标完成，但 {c['name']} 仍未 ready")

    def test_todo_cards_are_unready(self):
        """反向：待办批次里的卡如果已经 ready，说明它在别处被解禁 →
        必须从该批移除（或把该批标完成），否则进度会重复计算。"""
        stale = []
        for b in self.batches:
            if b["status"] == "done":
                continue
            for c in b["cards"]:
                if self.db[_key(c)].get("engine_ready"):
                    stale.append((b["id"], c["name"]))
        self.assertEqual(stale, [],
                         f"待办批次里含已 ready 的卡（应移出并更新 JSON）：{stale}")

    def test_structural_not_ready(self):
        for c in self.data["structural"]:
            self.assertFalse(self.db[_key(c)].get("engine_ready"),
                             f"结构性边界卡 {c['name']} 竟然已 ready —— 请更新档案")

    def test_deferred_not_ready(self):
        for c in self.data["deferred"]:
            self.assertFalse(self.db[_key(c)].get("engine_ready"),
                             f"待新能力卡 {c['name']} 竟然已 ready —— 请更新档案")


class TestNoDoubleCount(RunbookFixture):
    def test_duplicates_are_registered(self):
        """跨批卡之外的任何重复都是计槽错误"""
        seen, dups = {}, []
        for b in self.batches:
            for c in b["cards"]:
                k = _key(c)
                if k in seen:
                    dups.append((seen[k], b["id"], k))
                else:
                    seen[k] = b["id"]
        allowed = {_key(c): set(c["batches"]) for c in self.data["cross_batch"]}
        for b1, b2, k in dups:
            self.assertIn(k, allowed,
                          f"{k[0]} 同时出现在批 {b1} 与批 {b2}，但未登记 cross_batch")
            self.assertTrue({b1, b2} <= allowed[k],
                            f"{k[0]} 出现在批 {b1}/{b2}，登记为 {sorted(allowed[k])}")

    def test_registered_cross_cards_actually_appear(self):
        for c in self.data["cross_batch"]:
            hits = {b["id"] for b in self.batches
                    for x in b["cards"] if _key(x) == _key(c)}
            self.assertTrue(hits, f"跨批登记的 {c['name']} 未出现在任何批次中")
            self.assertTrue(hits <= set(c["batches"]),
                            f"{c['name']} 实际出现在 {sorted(hits)}，登记为 {c['batches']}")


class TestReconciliation(RunbookFixture):
    def test_totals_close(self):
        """批次内待做 + 结构性 + 待新能力 == 未 ready 卡数（口径必须闭合）"""
        in_batches = {_key(c) for b in self.batches for c in b["cards"]}
        todo = {k for k in in_batches if not self.db[k].get("engine_ready")}
        struct = {_key(c) for c in self.data["structural"]}
        defer = {_key(c) for c in self.data["deferred"]}
        self.assertEqual(todo & struct, set(), "结构性边界不得同时列入批次")
        self.assertEqual(todo & defer, set(), "待新能力不得同时列入批次")
        self.assertEqual(len(todo) + len(struct) + len(defer), len(self.unready),
                         f"口径不闭合：批次待做 {len(todo)} + 结构性 {len(struct)}"
                         f" + 待新能力 {len(defer)} != 未 ready {len(self.unready)}")
        self.assertEqual(todo | struct | defer, self.unready,
                         "存在未 ready 但既不在批次、也不在边界清单里的卡（会永久漏掉）")

    def test_plan_slots_match_card_lists(self):
        """槽位与唯一卡之差，必须等于**已登记跨批卡的实际重复次数**

        注意用「实际出现次数」而不是「登记批次数」：登记可以写依赖关系
        （如 Feraligatr 依赖 95 的静态修正层但主清单只在 97），
        真正决定槽位的是它在多少个批次里出现过。
        """
        slots = sum(len(b["cards"]) for b in self.batches)
        self.assertGreater(slots, 0)
        uniq = {_key(c) for b in self.batches for c in b["cards"]}
        registered = {_key(c) for c in self.data["cross_batch"]}
        extra = 0
        for k in registered:
            n = sum(1 for b in self.batches for c in b["cards"] if _key(c) == k)
            self.assertGreater(n, 1, f"跨批登记的 {k[0]} 只出现在 {n} 个批次，登记失效")
            extra += n - 1
        self.assertEqual(slots - len(uniq), extra,
                         "槽位与唯一卡之差应恰好等于跨批卡的实际重复次数")


class TestRenderedDocFresh(RunbookFixture):
    def test_doc_matches_renderer(self):
        """档案必须等于「数据 + 实测」的渲染结果（防止手改 Markdown）"""
        r = subprocess.run([sys.executable, str(SCRIPTS / "render_runbook.py"), "--check"],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0,
                         f"档案与数据不一致，请重跑 render_runbook.py\n{r.stdout}{r.stderr}")


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
