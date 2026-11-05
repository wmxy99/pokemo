# -*- coding: utf-8 -*-
"""建池逻辑守卫（第九十批 · 2026-11-05 起）

覆盖 `gen_engine_pool.build_pokemon` 对**上游数据集分类错误**的纠正：

上游把 Pokémon ex 的 **Tera 规则文本**
（「As long as this Pokémon is on your Bench, prevent all damage done to this
Pokémon by attacks」）归进了 `attacks[]`，解析产物只有一条
`passive:bench_immune_dmg`。该文本是**常驻被动**，引擎只在
`_is_bench_immune()`（battle.py:10367）读 `pdef.abilities[].tag`；
留在 `attacks.effects` 里会被 `_supported_attack_tags` 滤空 → 整卡拒收
（PalossandEX / Miraidon ex / Koraidon ex 因此长期在池外）。

本文件锁定三条口径：
  · **全部** tag 都是 `passive:*` 的伪招式 → 转写为卡级被动词条
    （`location="field"`，legals 已跳过 `passive:` 前缀）；
  · **混合** tag（passive + 招式效果）→ **不**转写，走原逻辑
    （宁可整卡拒收，也不猜哪条是招式、哪条是被动）；
  · `passive:*` 绝不得出现在 `attacks[].effects` 里
    —— 否则免伤只在**出招时**生效，是语义反转的静默错位。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

import gen_engine_pool as G  # noqa: E402


def _items(card: dict) -> dict:
    return {(it.get("target"), it.get("name")): it
            for it in ((card.get("parse") or {}).get("items") or [])}


def _mon_card(attacks, parse_items, name="TestMon", **extra):
    """构造最小宝可梦卡（字段与 cards_challenge.json 对齐）"""
    card = {
        "name": name,
        "supertype": "Pokemon",
        "hp": 280,
        "types": ["fighting"],
        "stage": 1,
        "retreat": 3,
        "series": "TST",
        "number": "1",
        "rule": "Pokémon ex",
        "attacks": attacks,
        "abilities": [],
        "parse": {"items": parse_items},
    }
    card.update(extra)
    return card


class TestPseudoAttackToPassive(unittest.TestCase):
    """伪招式（全 `passive:*` tag）应转写为卡级被动，且不进 attacks"""

    def test_tera_rule_becomes_passive_ability(self):
        card = _mon_card(
            attacks=[
                {"name": "Tera", "damage": None, "cost": [],
                 "effect": "As long as this Pokémon is on your Bench, "
                           "prevent all damage done to this Pokémon by attacks."},
                {"name": "Sand Tomb", "damage": 160,
                 "cost": ["colorless", "colorless", "colorless"],
                 "effect": "During your opponent's next turn, the Defending "
                           "Pokémon can't retreat."},
            ],
            parse_items=[
                {"target": "attack", "name": "Tera",
                 "engine_tags": ["passive:bench_immune_dmg"], "unsupported": []},
                {"target": "attack", "name": "Sand Tomb",
                 "engine_tags": ["retreat_lock:opp:turn"], "unsupported": []},
            ],
        )
        out = G.build_pokemon(card, _items(card), "TestMon")
        self.assertIsNotNone(out, "伪招式转写后应可建池")

        # 伪招式不再作为招式出现
        self.assertEqual([a["name"] for a in out["attacks"]], ["Sand Tomb"])

        # 转写为卡级被动，location 必须是 field（legals 跳过 passive: 前缀）
        self.assertEqual(len(out["abilities"]), 1)
        ab = out["abilities"][0]
        self.assertEqual(ab["tag"], "passive:bench_immune_dmg")
        self.assertEqual(ab["location"], "field")
        self.assertEqual(ab["name"], "Tera")
        self.assertEqual(ab["cost"], "")

    def test_no_passive_leaks_into_attacks(self):
        """反向守卫：任何 attacks.effects 都不得含 passive:*（语义反转）"""
        card = _mon_card(
            attacks=[
                {"name": "Tera", "damage": None, "cost": [], "effect": "…"},
                {"name": "Strike", "damage": 100, "cost": ["colorless"],
                 "effect": ""},
            ],
            parse_items=[
                {"target": "attack", "name": "Tera",
                 "engine_tags": ["passive:bench_immune_dmg"], "unsupported": []},
            ],
        )
        out = G.build_pokemon(card, _items(card), "TestMon")
        self.assertIsNotNone(out)
        leaked = [t for a in out["attacks"] for t in a["effects"]
                  if str(t).startswith("passive:")]
        self.assertEqual(leaked, [],
                         "被动 tag 落进 attacks.effects = 出招才免伤（语义反转）")

    def test_mixed_tags_not_converted(self):
        """混合 tag 不转写：宁可整卡拒收，也不猜哪条是招式效果

        ⚠ 这里的第二个 tag **必须**用白名单内前缀（`retreat_lock:`），否则
        `_supported_attack_tags` 会因另一条非法 tag 返回 [] 而整卡拒，
        掩盖「`passive:` 被误放行到攻击白名单」这个真正的错误
        —— 负向自测已实证：用 `draw:` 时注入错误守卫仍绿（假守卫）。
        """
        card = _mon_card(
            attacks=[
                {"name": "Weird", "damage": 50, "cost": ["colorless"],
                 "effect": "混合句式"},
            ],
            parse_items=[
                {"target": "attack", "name": "Weird",
                 "engine_tags": ["passive:bench_immune_dmg",
                                 "retreat_lock:opp:turn"],
                 "unsupported": []},
            ],
        )
        out = G.build_pokemon(card, _items(card), "TestMon")
        # 当前：`passive:` 不在 ATTACK_TAG_PREFIXES → 白名单返回 [] →
        # 有效果文本却无可执行 tag → 整卡拒收（不静默丢弃被动）
        self.assertIsNone(out, "混合 tag 应如实拒收，不得只保 passive 或只保招式效果")


class TestRealTeraCards(unittest.TestCase):
    """真实数据集：3 张 Tera 卡必须可建池且被动挂到 ability"""

    def test_three_tera_cards_buildable(self):
        import json
        db = HERE.parent / "data" / "cleaned" / "cards_challenge.json"
        if not db.exists():
            self.skipTest("cards_challenge.json 不存在（离线环境）")
        data = json.loads(db.read_text(encoding="utf-8"))
        targets = {("PalossandEX", "SSP", "91"),
                   ("Miraidon ex", "ASC", "73"),
                   ("Koraidon ex", "ASC", "121")}
        found = set()
        for c in data["cards"]:
            key = (c["name"], c.get("series"), c.get("number"))
            if key not in targets:
                continue
            out = G.build_pokemon(c, _items(c), c.get("name_key") or c["name"])
            self.assertIsNotNone(out, f"{c['name']} 应可建池")
            tags = [ab["tag"] for ab in out["abilities"]]
            self.assertIn("passive:bench_immune_dmg", tags,
                          f"{c['name']} 的 Tera 被动必须挂到 abilities")
            for a in out["attacks"]:
                self.assertNotIn("Tera", a["name"],
                                 f"{c['name']} 的伪招式不应作为招式保留")
            found.add(key)
        self.assertEqual(found, targets, f"未找到目标卡：{targets - found}")


class TestTrainerWhitelist(unittest.TestCase):
    """第九十一批：`shuffle_top:` 必须在**训练家侧**白名单里

    该前缀此前只出现在 `ATTACK_TAG_PREFIXES`（招式侧），`TRAINER_TAGS` 缺失
    → Deduction Kit 即便解析产出 `shuffle_top:3:own` 也被 `build_trainer`
    判成「无可执行 tag」→ 静默拒收、对账全绿、卡凭空消失。
    """

    @staticmethod
    def _trainer_card(tags, name="Deduction Kit", subtype="Item"):
        return {
            "name": name,
            "supertype": "Trainer",
            "subtype": subtype,
            "series": "SSP",
            "number": "171",
            "parse": {"items": [
                {"target": "card", "name": name, "engine_tags": list(tags),
                 "unsupported": [], "play_cost": {}, "play_gate": {}},
            ]},
        }

    def test_shuffle_top_accepted(self):
        card = self._trainer_card(["shuffle_top:3:own"])
        out = G.build_trainer(card, _items(card))
        self.assertIsNotNone(out, "shuffle_top: 应在训练家白名单内")
        self.assertEqual(out["tag"], "shuffle_top:3:own")

    def test_shuffle_top_in_trainer_tags_constant(self):
        self.assertIn("shuffle_top:", G.TRAINER_TAGS,
                      "TRAINER_TAGS 必须显式放行 shuffle_top:（过滤式白名单，"
                      "漏写即静默丢弃）")

    def test_unknown_tag_still_rejected(self):
        """反向守卫：白名单是**过滤**，未知前缀仍须拒收（不得为放行 shuffle_top
        而放宽成「任意 tag 都收」）"""
        card = self._trainer_card(["totally_made_up_tag:1"])
        self.assertIsNone(G.build_trainer(card, _items(card)))


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
