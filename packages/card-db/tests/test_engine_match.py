# -*- coding: utf-8 -*-
"""引擎匹配回归守卫：同名回落不得跨印刷冒充

背景（2026-10-27）：卡库 1267 张里存在大量同名不同印刷的宝可梦（例如
ASC 104 Medicham 与 DRI 100 Medicham，招式完全不同）。此前 `_match_engine`
只按卡名回落，导致另一印刷的卡被标为 `engine_ready`，而引擎按名字取池内
条目、实际执行的是**另一张卡**的招式 —— 静默错映射。

本文件锁定修复口径：
  · name_key / alias 回落：招式名 + 特性名集合必须一致；
  · (series, number) 精确命中仍以编号为准（同名但编号相同视为同一印刷）；
  · 同名歧义（ambiguous）继续拒绝；
  · 训练家按名字回落（无招式签名）。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

import import_challenge_dataset as IMP  # noqa: E402


class TestEngineNameKeySignature(unittest.TestCase):
    @staticmethod
    def _entry(name, attacks=(), abilities=(), supertype="Pokemon", **extra):
        e = {
            "name": name,
            "supertype": supertype,
            "status": "approximated",
            "signature": IMP._pokemon_signature({
                "attacks": [{"name": a} for a in attacks],
                "abilities": [{"name": a} for a in abilities],
            }),
        }
        e.update(extra)
        return e

    @staticmethod
    def _card(name, attacks=(), abilities=(), series="TEF", number="1",
              supertype="Pokemon", aliases=()):
        return {
            "name": name,
            "name_key": IMP.norm_key(name),
            "supertype": supertype,
            "series": series,
            "number": number,
            "aliases": list(aliases),
            "attacks": [{"name": a} for a in attacks],
            "abilities": [{"name": a} for a in abilities],
        }

    def test_same_printing_is_accepted(self):
        entry = self._entry("Medicham", attacks=("Seventh Kick",))
        idx = {IMP.norm_key("Medicham"): entry}
        card = self._card("Medicham", attacks=("Seventh Kick",))
        issues: dict = {}
        got, how = IMP._match_engine(card, idx, issues)
        self.assertIs(got, entry)
        self.assertEqual(how, "name_key")
        self.assertNotIn("engine_namekey_mismatch", issues)

    def test_different_printing_is_rejected(self):
        entry = self._entry("Medicham", attacks=("Harmonious Spirit Palm",))
        idx = {IMP.norm_key("Medicham"): entry}
        card = self._card("Medicham", attacks=("Seventh Kick",))
        issues: dict = {}
        got, how = IMP._match_engine(card, idx, issues)
        self.assertIsNone(got)
        self.assertIsNone(how)
        self.assertEqual(issues.get("engine_namekey_mismatch"), 1)

    def test_exact_set_number_still_wins(self):
        entry = self._entry("Medicham", attacks=("Harmonious Spirit Palm",))
        idx = {
            ("__setnum__", "TEF", "1"): entry,
            IMP.norm_key("Medicham"): entry,
        }
        card = self._card("Medicham", attacks=("Seventh Kick",))
        got, how = IMP._match_engine(card, idx, {})
        self.assertIs(got, entry)
        self.assertEqual(how, "set:number")

    def test_ambiguous_name_still_rejected(self):
        entry = self._entry("Medicham", attacks=("Seventh Kick",))
        entry["ambiguous"] = ["TEF 1", "DRI 2"]
        idx = {IMP.norm_key("Medicham"): entry}
        card = self._card("Medicham", attacks=("Seventh Kick",))
        got, how = IMP._match_engine(card, idx, {})
        self.assertIsNone(got)
        self.assertIsNone(how)

    def test_supertype_mismatch_rejected(self):
        entry = self._entry("Medicham", attacks=("Seventh Kick",))
        idx = {IMP.norm_key("Medicham"): entry}
        card = self._card("Medicham", supertype="Trainer")
        got, how = IMP._match_engine(card, idx, {})
        self.assertIsNone(got)
        self.assertIsNone(how)

    def test_trainer_name_fallback_still_works(self):
        entry = self._entry("Boss's Orders", supertype="Trainer")
        idx = {IMP.norm_key("Boss's Orders"): entry}
        card = self._card("Boss's Orders", supertype="Trainer")
        got, how = IMP._match_engine(card, idx, {})
        self.assertIs(got, entry)
        self.assertEqual(how, "name_key")

    def test_alias_also_requires_signature(self):
        entry = self._entry("Medicham", attacks=("Seventh Kick",))
        idx = {IMP.norm_key("Medicham"): entry}
        card = self._card("ASC Medicham", attacks=("Seventh Kick",),
                          aliases=("Medicham",))
        got, how = IMP._match_engine(card, idx, {})
        self.assertIs(got, entry)
        self.assertEqual(how, "alias:Medicham")

        bad = self._card("ASC Medicham", attacks=("Harmonious Spirit Palm",),
                         aliases=("Medicham",))
        got, how = IMP._match_engine(bad, idx, {})
        self.assertIsNone(got)
        self.assertIsNone(how)


if __name__ == "__main__":
    unittest.main()
