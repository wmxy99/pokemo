# -*- coding: utf-8 -*-
"""官方构筑规则守卫：10 套预设卡组必须**真的能进赛场**

背景（2026-11-10）：`decklist()` 只校验「60 张」，可官方构筑有三条硬约束：

  1. 60 张整；
  2. **同名卡最多 4 张**（基本能量除外，卡面写明 "You may have any number"）；
  3. **一副牌最多 1 张 ACE SPEC**（不论同名与否）。

在此之前，B1–B5 每套塞了 `Master Ball ×2 + Prime Catcher ×2`、C1 还外加
`Neo Upper Energy ×4` —— 每套 8 张 ACE SPEC。这些卡表在真实规则里立不起来，
而引擎**不会**因此报错（构筑规则不在对局规则里），所以只能靠这道守卫。

注入式自测（`test_injected_*`）是刻意的：只断言「当前 10 套合法」不足以证明
规则在生效 —— 本项目栽过「把对账写进死代码，注入错误后照样全绿」的坑。
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from engine import decklib, reallib

REPO = Path(__file__).resolve().parents[3]
DATASET = REPO / "packages" / "card-db" / "data" / "cleaned" / "cards_challenge.json"


def all_deck_ids() -> list:
    return list(reallib.archetype_ids()) + list(reallib.example_deck_ids())


def senergy_kinds(deck_id: str) -> list:
    """卡组里出现的特殊能量种类（按卡实例统计，去重保序）"""
    out = []
    for ref in decklib.make_deck(deck_id):
        edef = getattr(ref, "edef", None)
        tag = str(getattr(edef, "tag", "") or "")
        if tag.startswith("senergy:"):
            kind = tag.split(":", 1)[1]
            if kind not in out:
                out.append(kind)
    return out


class TestPresetDeckLegality(unittest.TestCase):
    """10 套预设：合规 + 每套带特殊能量"""

    def test_all_presets_are_legal(self):
        for did in all_deck_ids():
            with self.subTest(deck=did):
                leg = reallib.deck_legality(did)
                self.assertTrue(leg["ok"],
                                f"{did} 违规：{json.dumps(leg['violations'], ensure_ascii=False)}")
                self.assertEqual(leg["total"], 60, f"{did} 不是 60 张")

    def test_each_preset_has_at_most_one_ace_spec(self):
        for did in all_deck_ids():
            with self.subTest(deck=did):
                leg = reallib.deck_legality(did)
                self.assertLessEqual(len(leg["ace_spec"]), 1,
                                     f"{did} 有 {len(leg['ace_spec'])} 张 ACE SPEC：{leg['ace_spec']}")

    def test_every_preset_uses_special_energy(self):
        """用户诉求：每套都要有特殊能量（不是「至少一套」）"""
        for did in all_deck_ids():
            with self.subTest(deck=did):
                self.assertTrue(senergy_kinds(did),
                                f"{did} 一张特殊能量都没有")

    def test_special_energy_kinds_are_varied(self):
        """全体覆盖 ≥8 种（卡池共 12 种建模完毕，别只在 C1 用 Neo Upper）"""
        kinds = set()
        for did in all_deck_ids():
            kinds.update(senergy_kinds(did))
        self.assertGreaterEqual(len(kinds), 8,
                                f"只用到 {len(kinds)} 种特殊能量：{sorted(kinds)}")

    def test_basic_energy_may_exceed_four_copies(self):
        """反向守卫：基本能量不受 4 张上限（卡面 "any number"）"""
        for did in all_deck_ids():
            with self.subTest(deck=did):
                leg = reallib.deck_legality(did)
                for name, n in leg["copies"].items():
                    self.assertNotIn("Basic ", name,
                                     f"{did} 把基本能量 {name} 计入了张数上限（{n}）")


class TestAceSpecList(unittest.TestCase):
    """ACE SPEC 清单必须与数据集逐名一致（防止清单悄悄过期）"""

    def test_ace_spec_list_matches_dataset(self):
        if not DATASET.exists():
            self.skipTest(f"数据集不在仓库（{DATASET.name}）：无法核对 ACE SPEC 清单")
        data = json.loads(DATASET.read_text(encoding="utf-8"))
        cards = data.get("cards") or data
        want = {c["name"] for c in cards if c.get("rule") == "ACE SPEC"}
        got = set(reallib.ace_spec_names())
        self.assertEqual(want - got, set(), f"数据集有而清单缺：{sorted(want - got)}")
        self.assertEqual(got - want, set(), f"清单有而数据集无：{sorted(got - want)}")

    def test_known_ace_spec_cards(self):
        """抽样核对（清单整体漂移时上面那条会红，这条给出人话定位）"""
        for name in ("Legacy Energy", "Neo Upper Energy", "Prime Catcher",
                     "Master Ball", "Enriching Energy", "Hero’s Cape"):
            with self.subTest(card=name):
                self.assertTrue(reallib.is_ace_spec(name), f"{name} 应被认作 ACE SPEC")
        for name in ("Ultra Ball", "Iono", "Boss's Orders", "Prism Energy",
                     "Ignition Energy", "Spiky Energy"):
            with self.subTest(card=name):
                self.assertFalse(reallib.is_ace_spec(name), f"{name} 不是 ACE SPEC")


class TestRuleInjection(unittest.TestCase):
    """注入式自测：规则必须真的能变红"""

    def _entries(self, pairs):
        return [{"name": n, "count": c} for n, c in pairs]

    def test_two_ace_spec_is_rejected(self):
        rep = reallib.legality_from_entries(
            self._entries([("Legacy Energy", 1), ("Prime Catcher", 1)]))
        self.assertFalse(rep["ok"], "两张不同名 ACE SPEC 竟被放行")
        self.assertIn("ace_spec", [v["rule"] for v in rep["violations"]])

    def test_same_ace_spec_twice_is_rejected(self):
        rep = reallib.legality_from_entries(
            self._entries([("Legacy Energy", 2)]))
        self.assertFalse(rep["ok"], "同名 ACE SPEC 两张竟被放行（规则是「不论同名与否」）")

    def test_five_copies_is_rejected(self):
        rep = reallib.legality_from_entries(self._entries([("Iono", 5)]))
        self.assertFalse(rep["ok"], "同名 5 张竟被放行")
        self.assertIn("copy_limit", [v["rule"] for v in rep["violations"]])

    def test_basic_energy_unlimited(self):
        rep = reallib.legality_from_entries(
            self._entries([("Basic Fire Energy", 40)]))
        self.assertNotIn("copy_limit", [v["rule"] for v in rep["violations"]])

    def test_wrong_size_is_rejected(self):
        rep = reallib.legality_from_entries(self._entries([("Iono", 4)]))
        self.assertFalse(rep["ok"])
        self.assertIn("deck_size", [v["rule"] for v in rep["violations"]])

    def test_build_deck_refuses_illegal_list(self):
        """构建路径上真的拦住了（不只是「报告函数算对了」）"""
        fake = {"id": "ZZ", "label": "fake", "label_zh": "fake",
                "fallback_energy": "fire",
                "cards": self._entries([("Legacy Energy", 1),
                                        ("Neo Upper Energy", 1)])}
        with mock.patch.object(reallib, "_arch_raw", lambda _aid: fake):
            with self.assertRaises(ValueError) as ctx:
                reallib.build_deck("ZZ")
        self.assertIn("ACE SPEC", str(ctx.exception))


class TestRuleAI(unittest.TestCase):
    """规则 AI（2026-11-10）：进化 / 空招防护 / 牌库保护

    这三条都是「卡组优化的实际兑现处」：10 套里 7 套主力在二阶，AI 不进化时
    40 局有 26 局以 `no_pokemon` 收场，卡组再合规也打不出来。
    """

    def test_evolves_when_possible(self):
        from engine.agents import RuleAI
        act = RuleAI("t").decide({
            "me_state": {"active": {"hp": 100, "energy": 1, "attacks": []},
                         "bench": [], "deck_count": 40},
            "legal": [
                {"verb": "evolve", "hand": 2, "target": 1, "name": "Dragapult ex"},
                {"verb": "evolve", "hand": 2, "target": 0, "name": "Dragapult ex"},
                {"verb": "pass"},
            ]})
        self.assertEqual(act["verb"], "evolve")
        self.assertEqual(act["target"], 0, "应优先把**战斗位**推成主力")

    def test_avoids_zero_damage_attack(self):
        """0 伤害招式被当成攻击 = 空转（Duskull「Come and Get You」实测 70 次）"""
        from engine.agents import RuleAI
        act = RuleAI("t").decide({
            "me_state": {
                "active": {"hp": 60, "energy": 1,
                           "attacks": [{"name": "Come and Get You", "damage": 0,
                                        "cost": 1}]},
                "bench": [{"hp": 330, "energy": 2, "attacks": []}],
                "deck_count": 40},
            "legal": [
                {"verb": "attack", "attack": 0},
                {"verb": "retreat", "target": 0},
            ]})
        self.assertEqual(act["verb"], "retreat",
                         f"0 伤害空招不该被选中：{act}")

    def test_stops_drawing_when_deck_is_low(self):
        """牌库见底还硬抽，60 张牌 20 回合就抽空（实测 deckout 占 28/40 局）"""
        from engine.agents import RuleAI
        act = RuleAI("t").decide({
            "me_state": {"active": {"hp": 100, "energy": 0, "attacks": []},
                         "bench": [], "deck_count": 8, "hand_count": 3},
            "opp_state": {"active": None, "bench": [], "hand_count": 4,
                          "prizes_left": 6, "deck_count": 40},
            "legal": [
                {"verb": "trainer", "hand": 0, "tag": "shuffle_draw:5", "name": "Iono"},
                {"verb": "trainer", "hand": 1, "tag": "poffin", "name": "Buddy-Buddy Poffin"},
                {"verb": "pass"},
            ]})
        self.assertNotIn(act.get("tag"), ("shuffle_draw:5",),
                         f"牌库只剩 8 张仍在抽牌：{act}")

    def test_still_draws_when_deck_is_healthy(self):
        """反向守卫：牌库充足时该抽就抽（别把保护写成「永不抽牌」）"""
        from engine.agents import RuleAI
        act = RuleAI("t").decide({
            "me_state": {"active": {"hp": 100, "energy": 0, "attacks": []},
                         "bench": [], "deck_count": 40, "hand_count": 3},
            "opp_state": {"active": None, "bench": [], "hand_count": 4,
                          "prizes_left": 6, "deck_count": 40},
            "legal": [
                {"verb": "trainer", "hand": 0, "tag": "shuffle_draw:5", "name": "Iono"},
                {"verb": "pass"},
            ]})
        self.assertEqual(act.get("tag"), "shuffle_draw:5")

    def test_puts_tool_or_stadium_when_nothing_else(self):
        from engine.agents import RuleAI
        act = RuleAI("t").decide({
            "me_state": {"active": {"hp": 100, "energy": 0, "attacks": []},
                         "bench": [], "deck_count": 40, "hand_count": 3},
            "opp_state": {"active": None, "bench": [], "hand_count": 4,
                          "prizes_left": 6, "deck_count": 40},
            "legal": [
                {"verb": "trainer", "hand": 0,
                 "tag": "stadium:dmg_reduce:30:type:metal", "name": "Full Metal Lab"},
                {"verb": "pass"},
            ]})
        self.assertEqual(act.get("tag"), "stadium:dmg_reduce:30:type:metal")


if __name__ == "__main__":
    unittest.main(verbosity=2)
