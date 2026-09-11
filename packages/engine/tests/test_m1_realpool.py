# -*- coding: utf-8 -*-
"""PokéLab Engine · M1 真实卡池（X 版）与预设对手池 A1–A4 用例

覆盖：
  - 卡池/卡表数据完整性（60 张、卡名可在卡池中解析）
  - 映射覆盖率报告（executable_rate ≥ 0.90 ⇒ battle_ready）
  - 进化线（Dreepy→Drakloak→Dragapult ex）与彩色能量费用（Phantom Dive F+P）
  - 新增训练家 tag：research:N（弃手抽 n）· shuffle_draw:N（洗手抽 n）
  - 训练家弃牌按身份移除（Ultra Ball 高频场景不再索引漂移，回归用例）
  - A1–A4 端到端对局可完成（无异常、正常终局）
运行：python -m unittest discover -s tests -v
"""
from __future__ import annotations

import random
import unittest

from engine import decklib as dl
from engine import reallib
from engine.battle import Battle
from engine.cards import EnergyDef, EnergyRef, PokeRef, TrainerRef
from engine.agents import BaselineAI


def _play(da, db, seed=1, max_steps=900):
    b = Battle(da, db, seed=seed)
    ags = [BaselineAI("a"), BaselineAI("b")]
    steps = 0
    while not b.finished and steps < max_steps:
        steps += 1
        b.step(ags[b.current].decide(b.view()))
    return b, steps


def _find_hand_card(deck_name: str, tag_prefix: str, need_hand: int = 1,
                    seeds=range(1, 120)):
    """扫描种子，找到起手含指定 tag 训练家的局面（避免用例依赖运气）"""
    for s in seeds:
        b = Battle(dl.make_deck(deck_name), dl.make_deck(deck_name), seed=s)
        p = b.players[b.current]
        if len(p.hand) < need_hand:
            continue
        for i, c in enumerate(p.hand):
            if isinstance(c, TrainerRef) and c.tdef.tag.startswith(tag_prefix):
                return b, i
    return None, None


class TestRealPoolData(unittest.TestCase):
    def test_all_archetypes_are_60_cards(self):
        for aid in reallib.archetype_ids():
            deck = reallib.build_deck(aid)
            self.assertEqual(len(deck), 60, f"{aid} 应为 60 张")
            self.assertEqual(sum(int(c["count"]) for c in reallib.decklist(aid)), 60)

    def test_every_card_is_in_catalog(self):
        cat = reallib.catalog()
        for aid in reallib.archetype_ids():
            for entry in reallib.decklist(aid):
                self.assertIn(entry["name"], cat, f"{aid} 卡名未收录: {entry['name']}")

    def test_coverage_threshold_and_replace_report(self):
        for aid in reallib.archetype_ids():
            cov = reallib.coverage(aid)
            self.assertGreaterEqual(cov["executable_rate"], 0.90, f"{aid} 可执行率不足")
            self.assertTrue(cov["battle_ready"])
            # 被替换的卡必须给出可追溯原因
            for r in cov["replaced"]:
                self.assertTrue(r["reason"])

    def test_make_deck_supports_archetype_ids(self):
        for aid in reallib.archetype_ids():
            self.assertEqual(len(dl.make_deck(aid)), 60)
            self.assertEqual(len(dl.make_deck(aid.lower())), 60)

    def test_unknown_deck_raises_helpful_error(self):
        with self.assertRaises(KeyError) as cm:
            dl.make_deck("nope")
        self.assertIn("未知卡组", str(cm.exception))


class TestRealCardMechanics(unittest.TestCase):
    def test_dragapult_line_and_colored_cost(self):
        cat = reallib.catalog()
        dragapult = cat["Dragapult ex"]["def"]
        self.assertEqual(dragapult.stage, 2)
        self.assertEqual(dragapult.evolves_from, "Drakloak")
        self.assertEqual(dragapult.hp, 330)
        phantom = [a for a in dragapult.attacks if a.name == "Phantom Dive"][0]
        self.assertEqual(phantom.cost, (("fire", 1), ("psychic", 1)))
        self.assertIn("spread_free:60", phantom.effects)
        # 进化链完整（基础→一阶→二阶）
        self.assertEqual(cat["Dreepy"]["def"].stage, 0)
        self.assertEqual(cat["Drakloak"]["def"].stage, 1)
        self.assertEqual(cat["Drakloak"]["def"].evolves_from, "Dreepy")
        self.assertTrue(any(a.tag == "recon:2"
                            for a in cat["Drakloak"]["def"].abilities))

    def test_research_trainer_discards_hand_then_draws(self):
        """Professor's Research：弃全部手牌 → 抽 n"""
        b = Battle(dl.make_deck("A1"), dl.make_deck("A1"), seed=5)
        # 造一个可控局面：手牌 = research + 3 张其他
        p = b.players[b.current]
        deck0 = len(p.deck)
        res = [c for c in p.hand if isinstance(c, TrainerRef)
               and c.tdef.tag.startswith("research:")]
        if not res:
            self.skipTest("起手未抽到 research 型支援者")
        hi = p.hand.index(res[0])
        hand_before = len(p.hand)
        out = b.step({"verb": "trainer", "hand": hi})
        self.assertTrue(out["ok"], out)
        # 弃牌堆增加（手牌-1）+ 抽 7 → 牌库减少
        self.assertLess(len(p.deck), deck0)
        self.assertGreaterEqual(len(p.hand), 1)
        self.assertLess(len(p.hand), hand_before + 7)

    def test_shuffle_draw_trainer(self):
        """Iono：手牌洗回 → 抽 n"""
        b, hi = _find_hand_card("A3", "shuffle_draw:")
        self.assertIsNotNone(b, "A3 卡组 120 种子内应能抽到 shuffle_draw 型支援者")
        p = b.players[b.current]
        deck0 = len(p.deck)
        out = b.step({"verb": "trainer", "hand": hi})
        self.assertTrue(out["ok"], out)
        self.assertGreater(len(p.deck), deck0 - 8)   # 洗回的手牌回到牌库

    def test_ultra_ball_keeps_hand_indexes_consistent(self):
        """回归：Ultra Ball 内部弃牌后，训练家卡须被正确移除（按身份）"""
        b, hi = _find_hand_card("A1", "ultra_ball", need_hand=3)
        self.assertIsNotNone(b, "A1 卡组 120 种子内应能抽到 Ultra Ball + 手牌 ≥3")
        p = b.players[b.current]
        card = p.hand[hi]
        out = b.step({"verb": "trainer", "hand": hi})
        self.assertTrue(out["ok"], out)
        self.assertNotIn(card, p.hand)               # 自身已被移除
        self.assertIn("Ultra Ball", p.discard)       # 且进入弃牌堆


class TestRealDeckBattles(unittest.TestCase):
    def test_pairs_finish_without_error(self):
        pairs = [("A1", "A1"), ("A1", "A3"), ("A2", "A4"), ("A3", "A4")]
        for a, c in pairs:
            b, steps = _play(dl.make_deck(a), dl.make_deck(c), seed=101)
            self.assertTrue(b.finished, f"{a} vs {c} 未终局（steps={steps}）")
            self.assertIn(b.winner, (0, 1))

    def test_replays_are_deterministic(self):
        b1, _ = _play(dl.make_deck("A1"), dl.make_deck("A2"), seed=42)
        b2, _ = _play(dl.make_deck("A1"), dl.make_deck("A2"), seed=42)
        self.assertEqual(b1.winner, b2.winner)
        self.assertEqual(b1.turn_counts, b2.turn_counts)


class TestI18nZhNames(unittest.TestCase):
    """i18n：卡名 / 招式 / 特性 / 卡组名 均提供中文展示层（英文保留为数据键）"""

    def test_catalog_has_zh_for_all_cards(self):
        cat = reallib.catalog()
        missing = [n for n, it in cat.items() if not it["def"].display]
        self.assertEqual(missing, [], f"缺中文展示名：{missing}")
        self.assertEqual(cat["Dragapult ex"]["def"].display, "多龙巴鲁托ex")
        self.assertEqual(cat["Dusknoir"]["def"].display, "黑夜魔灵")
        self.assertEqual(cat["Ogerpon ex"]["def"].display, "厄诡椪ex")
        self.assertEqual(cat["Ultra Ball"]["def"].display, "高级球")
        self.assertEqual(cat["Professor's Research"]["def"].display, "博士的研究")
        self.assertEqual(cat["Basic Fire Energy"]["def"].display, "基本火能量")

    def test_attacks_and_abilities_have_zh(self):
        cat = reallib.catalog()
        drag = cat["Dragapult ex"]["def"]
        names = {a.name: a.name_zh for a in drag.attacks}
        self.assertEqual(names["Phantom Dive"], "幻影俯冲")
        self.assertEqual(names["Jet Headbutt"], "喷射头击")
        self.assertEqual(cat["Drakloak"]["def"].abilities[0].name_zh, "侦察指令")
        self.assertEqual(cat["Dusknoir"]["def"].abilities[0].name_zh, "诅咒爆破")
        # 英文数据键必须保持不变（Limitless 文本匹配以此为准）
        self.assertEqual(drag.name, "Dragapult ex")

    def test_archetype_labels_are_chinese(self):
        rows = reallib.archetypes()
        self.assertEqual([r["id"] for r in rows], ["A1", "A2", "A3", "A4"])
        self.assertEqual(rows[0]["label"], "多龙巴鲁托ex / 黑夜魔灵")
        self.assertEqual(rows[3]["label"], "基础盒（厄诡椪ex / 皮皮ex）")
        self.assertEqual(rows[0]["label_en"], "Dragapult ex / Dusknoir")
        for r in rows:
            latin = [ch for ch in r["label"] if ch.isascii() and ch.isalpha()]
            self.assertTrue(set(latin) <= {"A", "N", "e", "x"},
                            f"{r['label']} 残留拉丁字母：{''.join(latin)}")

    def test_view_carries_display_names(self):
        b = Battle(dl.make_deck("A1"), dl.make_deck("A3"), seed=7)
        v = b.view()
        hand = v["me_state"]["hand"]
        self.assertTrue(hand)
        self.assertTrue(all(c.get("display") for c in hand))
        self.assertTrue(any(not c["display"].isascii() for c in hand),
                        "手牌展示名应出现中文")
        fp = v["me_state"]["active"] or (v["me_state"]["bench"] or [None])[0]
        if fp:
            self.assertTrue(fp["display"])
            for atk in fp["attacks"]:
                self.assertTrue(atk["display"])
            for ab in fp["abilities"]:
                self.assertTrue(ab["display"])

    def test_demo_deck_zh_labels(self):
        self.assertEqual(dl.deck_label_zh("draco"), "龙系进化演示卡组")
        self.assertEqual(dl.deck_label_zh("A3"), "A3 · N的索罗亚克ex")
        self.assertEqual(dl.deck_label_zh("unknown-x"), "unknown-x")
        b = Battle(dl.make_deck("draco"), dl.make_deck("lightning"), seed=3)
        names = [c.get("display") for c in b.view()["me_state"]["hand"]]
        self.assertTrue(any(n and not n.isascii() for n in names),
                        "演示卡组手牌也应有中文名")


class TestZhNameResolution(unittest.TestCase):
    """中文卡名可作为输入（resolve_name / resolve_deck_id）：数据键仍为英文"""

    def test_zh_index_covers_pool(self):
        idx = reallib.zh_index()
        self.assertIn("多龙巴鲁托ex", idx)
        self.assertEqual(idx["多龙巴鲁托ex"], "Dragapult ex")
        self.assertEqual(idx["高级球"], "Ultra Ball")
        self.assertGreaterEqual(len(idx), 40)

    def test_resolve_name_zh_to_en_key(self):
        self.assertEqual(reallib.resolve_name("多龙巴鲁托ex"), "Dragapult ex")
        self.assertEqual(reallib.resolve_name("高级球"), "Ultra Ball")
        self.assertEqual(reallib.resolve_name("基本火能量"), "Basic Fire Energy")

    def test_resolve_name_is_idempotent_for_en(self):
        """英文键原样返回（大小写不敏感回填规范键）"""
        self.assertEqual(reallib.resolve_name("Dragapult ex"), "Dragapult ex")
        self.assertEqual(reallib.resolve_name("dragapult ex"), "Dragapult ex")

    def test_resolve_name_unknown_passthrough(self):
        self.assertEqual(reallib.resolve_name("不存在的卡"), "不存在的卡")
        self.assertEqual(reallib.resolve_name(""), "")

    def test_is_zh_name(self):
        self.assertTrue(reallib.is_zh_name("多龙巴鲁托ex"))
        self.assertTrue(reallib.is_zh_name("高级球"))
        self.assertFalse(reallib.is_zh_name("Dragapult ex"))
        self.assertFalse(reallib.is_zh_name(""))

    def test_resolve_deck_id_supports_zh(self):
        self.assertEqual(dl.resolve_deck_id("A1"), "A1")
        self.assertEqual(dl.resolve_deck_id("a1"), "A1")
        self.assertEqual(dl.resolve_deck_id("A1 · 多龙巴鲁托ex / 黑夜魔灵"), "A1")
        self.assertEqual(dl.resolve_deck_id("龙系进化演示卡组"), "draco")
        self.assertEqual(dl.resolve_deck_id("多龙巴鲁托ex / 黑夜魔灵"), "A1")

    def test_make_deck_accepts_zh_deck_name(self):
        self.assertEqual(len(dl.make_deck("龙系进化演示卡组")), 60)
        self.assertEqual(len(dl.make_deck("A3 · N的索罗亚克ex")), 60)


class TestColorlessCostPayment(unittest.TestCase):
    """能量支付规则：彩色需求同色满足；**无色需求可由任意能量支付**

    回归守卫：曾因 can_pay 仅比对同名能量，导致真实卡组（彩色能量）
    的「2 无色」招式永远不可用（演示卡组用无色能量掩盖了该缺陷）。
    """

    def _fp(self, name):
        from engine.cards import make_pokemon
        from engine import reallib
        return make_pokemon(reallib.catalog()[name]["def"])

    def test_colorless_payable_by_any_color(self):
        fp = self._fp("Meowth ex")            # 乱抓：2 无色
        cost = fp.pdef.attacks[0].cost
        fp.add_energy("psychic")
        self.assertFalse(fp.can_pay(cost), "1 点能量不足以支付 2 无色")
        fp.add_energy("fire")
        self.assertTrue(fp.can_pay(cost), "2 无色应可由「超+火」支付")

    def test_colorless_payable_by_colorless_too(self):
        fp = self._fp("Meowth ex")
        fp.add_energy("colorless", 2)
        self.assertTrue(fp.can_pay(fp.pdef.attacks[0].cost))

    def test_colored_demand_not_substitutable(self):
        fp = self._fp("Dragapult ex")         # 幻影俯冲：火 + 超
        cost = fp.pdef.attacks[1].cost
        fp.add_energy("fire", 2)
        self.assertFalse(fp.can_pay(cost), "双火不可支付「火+超」")
        fp.add_energy("psychic")
        self.assertTrue(fp.can_pay(cost))

    def test_mixed_colored_plus_colorless(self):
        """彩色+无色：彩色须同色占用，无色吃剩余任意能量"""
        fp = self._fp("Dreepy")               # 龙之头击：超 + 无色
        cost = fp.pdef.attacks[1].cost
        fp.add_energy("psychic")
        self.assertFalse(fp.can_pay(cost))
        fp.add_energy("fire")
        self.assertTrue(fp.can_pay(cost), "剩余火能量应可支付无色部分")

    def test_real_deck_active_can_attack_after_two_energy(self):
        """真实卡组实战口径：附着 2 点彩色能量后应能出招（旧 bug 下恒为 False）"""
        b = Battle(dl.make_deck("A1"), dl.make_deck("A3"), seed=7)
        p0 = b.players[0]
        self.assertIsNotNone(p0.active)
        for t in ("psychic", "fire"):
            p0.active.add_energy(t)
        payable = [a for a in p0.active.pdef.attacks
                   if p0.active.can_pay(a.cost)]
        self.assertTrue(payable, "2 点彩色能量后应至少有一个可支付招式")


if __name__ == "__main__":
    unittest.main(verbosity=2)
