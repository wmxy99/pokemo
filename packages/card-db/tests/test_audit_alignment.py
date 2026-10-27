# -*- coding: utf-8 -*-
"""对账脚本（audit_pool_alignment）回归守卫

目的：对账脚本本身必须有**负向自测**——否则它只是橡皮图章。
本文件锁定 `spread_free`（E5 任意分配）的识别口径：

  · 引擎 `spread_free:N` = 把 N 点伤害**自由分配**到对手后备（位置码由 plan 指定，
    不含战斗位）。卡面「Put N damage counters on your opponent's Benched Pokémon
    in any way you like」正是此语义 → 必须判为精确可建模，不再误报「应整卡不收」。
  · 守卫不可过宽：不含 bench / 含条件（that has / for each）的「in any way」仍须判未建模；
    有 spread_free 子句但缺 tag 的卡仍须被抓出。

这些用例保证：改过解析链或引擎 tag 后重跑对账，既能放行真·精确建模，
也不让旧问题静默通过。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

import audit_pool_alignment as A  # noqa: E402


class TestAuditSpreadFree(unittest.TestCase):
    DESC = ("Put 2 damage counters on your opponent’s Benched Pokémon "
            "in any way you like.")

    def test_spread_free_recognized(self):
        """自由分配 + 对手后备 → spread_free 精确可建模，不应报错"""
        errs = A.audit_extra_damage("Flutter Mane", "Hex Hurl", self.DESC,
                                    ["spread_free:20"])
        self.assertEqual(errs, [], "spread_free 精确建模被误报为未建模")

    def test_missing_spread_free_tag_still_flagged(self):
        """有「in any way」子句却无 spread_free tag → 仍须抓出"""
        errs = A.audit_extra_damage("Flutter Mane", "Hex Hurl", self.DESC, [])
        self.assertTrue(errs, "缺 spread_free tag 却放行")

    def test_in_any_way_without_bench_still_unmodeled(self):
        """含战斗位的「in any way」：spread_free 只覆盖后备 → 仍须判未建模"""
        desc = ("Put 2 damage counters on your opponent’s Pokémon "
                "in any way you like.")
        errs = A.audit_extra_damage("X", "Y", desc, ["spread_free:20"])
        self.assertTrue(errs, "非后备目标不应被 spread_free 放行")

    def test_conditional_in_any_way_still_unmodeled(self):
        """带条件（that has）的「in any way」→ 子集，仍须判未建模"""
        desc = ("Put 2 damage counters on your opponent’s Benched Pokémon "
                "that has a Tool attached in any way you like.")
        errs = A.audit_extra_damage("X", "Y", desc, ["spread_free:20"])
        self.assertTrue(errs, "含条件的目标不应被 spread_free 放行")

    def test_each_benched_unchanged(self):
        """「each of benched」语义不变 → snipe:opp_bench（与 spread_free 区分）"""
        desc = ("Put 2 damage counters on each of your opponent’s "
                "Benched Pokémon.")
        errs = A.audit_extra_damage("X", "Y", desc, ["snipe:opp_bench:20"])
        self.assertEqual(errs, [], "each-of 语义回归")


if __name__ == "__main__":
    unittest.main()
