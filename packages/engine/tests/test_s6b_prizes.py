# -*- coding: utf-8 -*-
"""S6b · 奖赏卡「取哪张」+ 面朝下的信息规则（2026-11-08）

规则（官方）
------------
* 击倒对手宝可梦后，从**自己的**奖赏区取卡；若其中有**面朝上**的，由牌手选择
  取哪一张。全是面朝下时彼此**不可区分**（不得查看）→ 不构成决策。
* 「看 1 张对手面朝下的奖赏卡」与「把 1 张翻到正面」是两件事：前者只产生信息，
  后者**改变可见性并保持到对局结束**（`PrizeSlot.face_up`）。

本阶段落地
----------
1. 有面朝上 + **本人**取 + 只取 1 张 → 挂起 `pick_prize` 选择（真决策）；
2. 取多张（如 ex=2）→ `prize_pick_auto` 日志 + 面朝下优先（**显式记录的简化**）；
3. 对手取奖赏（自伤/反伤击倒）→ 代理确定性取（与 `shuffle_opp_hand` 同口径）；
4. 挂起期间**别的宝可梦被击倒** → 入队，闭合后按序补做（不允许嵌套挂起）；
5. 信息规则：面朝下的奖赏卡身份不出现在 `view()` 里（双方都不许查看）。

运行：cd packages/engine && python -m unittest tests.test_s6b_prizes -v
"""
from __future__ import annotations

import unittest

from engine import zones
from engine.battle import Battle, _card_label
from engine.cards import AttackDef, PokemonDef, PokeRef, make_pokemon
from engine.state import GameConfig


def _pdef(name="Victim", hp=30, ex=False):
    return PokemonDef(name, hp=hp, ptype="colorless",
                      attacks=(AttackDef("Jab", cost=0, damage=10),), is_ex=ex)


def _battle(seed=5) -> Battle:
    """真实对局（要奖赏卡对象化）；关掉先手规则 —— 本套件验的是取奖赏。

    卡组用**唯一卡名**：信息规则断言要能区分「这一张」是不是面朝下奖赏卡，
    全同名时任何名字都会在场上/手牌里出现，断言就没有辨别力（假绿）。
    """
    def _deck(tag: str) -> list:
        return [PokeRef(PokemonDef(f"Filler{tag}{i}", hp=100, ptype="colorless"))
                for i in range(40)]
    # ⚠ 两副牌的卡名必须**不相交**：同名卡在两边各有一张时，
    #   「面朝下奖赏卡的身份」断言会被对面场上的同名卡命中（假红）。
    return Battle(_deck("A"), _deck("B"), seed=seed,
                  cfg=GameConfig(first_turn_no_attack=False,
                                 first_turn_no_supporter=False))


def _setup_ko(b: Battle, damage=999, ex=False, face_up=True):
    """让当前方一击击倒对手战斗位 → 返回 `(taker_pi, 面朝上的卡名 or None)`

    奖赏卡**取的是自己**的（官方：对手被击倒 → 你从自己的奖赏区取），
    所以「面朝上」的旗标打在**当前行动方**的奖赏区上。
    `taker_pi` 必须在**换手前**记下（攻击结算完会结束回合）。
    """
    taker = b.current
    me = b.players[taker]
    opp = b.players[1 - taker]
    me.active = make_pokemon(PokemonDef(
        "Striker", hp=300, ptype="colorless",
        attacks=(AttackDef("Finisher", cost=0, damage=damage),)))
    opp.active = make_pokemon(_pdef("Victim", hp=30, ex=ex))
    label = None
    if face_up:
        me.prizes[0].face_up = True
        label = _card_label(me.prizes[0].ref)
    return taker, label


def _choose(b: Battle, mode: str):
    pick = next(a for a in b.legal_actions()
                if a.get("verb") == "choose" and a["option"].get("mode") == mode)
    return b.step({"verb": "choose", "pick": pick["pick"]})


class TestPickPrizeChoice(unittest.TestCase):
    def test_choice_opens_with_face_up_option(self):
        b = _battle()
        taker, label = _setup_ko(b)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.phase, "choice")
        opts = [a for a in b.legal_actions() if a.get("verb") == "choose"]
        ups = [o for o in opts if o["option"].get("mode") == "up"]
        downs = [o for o in opts if o["option"].get("mode") == "down"]
        # 只有 1 张面朝上 → 恰好 1 个「正面」选项 + 1 个「背面」选项。
        # 数量断言能抓住「把所有槽位都当成正面」（那会给出 6 个正面选项 = 假装有选择）。
        self.assertEqual(len(ups), 1, ups)
        self.assertEqual(len(downs), 1, downs)
        self.assertEqual(ups[0]["option"].get("label"), label)

    def test_picking_face_up_card_gets_that_card(self):
        b = _battle()
        taker, label = _setup_ko(b)
        b.step({"verb": "attack", "attack": 0})
        r = _choose(b, "up")
        self.assertTrue(r["ok"], r.get("error"))
        names = [_card_label(c) for c in b.players[taker].hand]
        self.assertIn(label, names, "指定的面朝上卡必须进手牌")
        self.assertEqual(b.players[taker].prizes_left, 5)

    def test_picking_face_down_keeps_info_hidden(self):
        """选背面 = 随机一张：那张面朝上的卡**留在原处**（仍可见）"""
        b = _battle()
        taker, label = _setup_ko(b)
        b.step({"verb": "attack", "attack": 0})
        r = _choose(b, "down")
        self.assertTrue(r["ok"], r.get("error"))
        ups = zones.face_up_slots(b.players[taker])
        self.assertEqual(len(ups), 1, "没被取走的面朝上卡应仍在奖赏区")
        self.assertEqual(_card_label(b.players[taker].prizes[ups[0]].ref), label)

    def test_multi_prize_take_is_deterministic_with_log(self):
        """取 2 张（ex）→ 不逐张选择，但必须写 `prize_pick_auto`（显式简化）"""
        b = _battle()
        taker, _ = _setup_ko(b, ex=True)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.phase, "main", "多张取奖赏不挂选择")
        self.assertIn("prize_pick_auto", [e["type"] for e in b.events])
        self.assertEqual(b.players[taker].prizes_left, 4)

    def test_opponent_take_is_deterministic(self):
        """自伤击倒（自己的宝可梦）→ **对手**取奖赏，代理确定性取、不开选择"""
        b = _battle()
        taker = b.current
        other = 1 - taker
        me = b.players[taker]
        me.active = make_pokemon(PokemonDef(
            "Suicidal", hp=30, ptype="colorless",
            attacks=(AttackDef("Boom", cost=0, damage=0, effects=("self:999",)),)))
        me.active.just_played = False
        b.players[other].prizes[0].face_up = True   # 取卡方（对手）自己的奖赏区
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.phase, "main", "对手取奖赏时不得挂起本人选择")
        self.assertEqual(b.players[other].prizes_left, 5)

    def test_no_choice_when_all_face_down(self):
        b = _battle()
        _setup_ko(b, face_up=False)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.phase, "main")
        self.assertNotIn("prize_pick_auto", [e["type"] for e in b.events])


class TestKoQueueWhileChoosing(unittest.TestCase):
    """挂起期间的第二只击倒必须排队（否则撞「不允许嵌套挂起」）"""

    def test_two_kos_queue_and_resolve(self):
        b = _battle()
        taker = b.current
        me = b.players[taker]
        opp = b.players[1 - taker]
        me.active = make_pokemon(PokemonDef(
            "Striker", hp=300, ptype="colorless",
            attacks=(AttackDef("Sweep", cost=0, damage=999,
                               effects=("spread:1:999",)),)))
        opp.active = make_pokemon(_pdef("VictimA", hp=30))
        # 后备留 2 只：战斗位 + 1 只后备被同时击倒，另 1 只负责补位
        # （只留 1 只时两击倒后必然「无宝可梦」判负，测不到补位）。
        opp.bench = [make_pokemon(_pdef("VictimB", hp=30)),
                     make_pokemon(_pdef("VictimC", hp=200))]
        me.prizes[0].face_up = True          # 取卡方（自己）的奖赏区
        before = me.prizes_left
        b.step({"verb": "attack", "attack": 0})
        # 第一只 KO 后挂起选择；第二只应在队列里
        self.assertEqual(b.phase, "choice")
        self.assertEqual(len(b._ko_q()), 1, "第二只击倒必须入队")
        steps = 0
        while b.phase == "choice" and steps < 6:
            _choose(b, "up" if steps == 0 else "down")
            steps += 1
        self.assertEqual(b.phase, "main")
        self.assertEqual(b._ko_q(), [])
        self.assertEqual(me.prizes_left, before - 2, "两只都应结算取奖赏")
        self.assertIsNotNone(opp.active, "补位必须完成")


class TestPrizeInfoRule(unittest.TestCase):
    """面朝下的奖赏卡身份是**隐藏信息**（官方：不得查看）"""

    def _names_in_view(self, b: Battle) -> set:
        """`view()` 里出现的**全部字符串**（避免「Filler3 是 Filler34 的子串」误报）

        ⚠ 不能只扫 `name` / `display` 字段：泄漏可能发生在任何新加的字段
        （实测：往 `me_state` 塞一个 `"prizes": [卡名…]` 时，只扫 name 字段的
        守卫**照样全绿** —— 假守卫）。
        """
        view = b.view()
        found = set()

        def walk(node):
            if isinstance(node, dict):
                for k, v in node.items():
                    found.add(str(k))
                    walk(v)
            elif isinstance(node, (list, tuple)):
                for x in node:
                    walk(x)
            elif isinstance(node, str):
                found.add(node)
        walk(view)
        return found

    def test_view_has_no_face_down_identities(self):
        """双方的面朝下奖赏卡身份都不得出现在 `view()`（按**精确卡名**比对）"""
        b = _battle()
        names = self._names_in_view(b)
        for pi in (0, 1):
            for slot in b.players[pi].prizes:
                if slot.face_up:
                    continue
                label = _card_label(slot.ref)
                self.assertNotIn(label, names,
                                 f"面朝下奖赏卡身份泄漏（P{pi}）：{label}")

    def test_view_exposes_count_only(self):
        b = _battle()
        v = b.view()
        self.assertIn("prizes_left", v["me_state"])
        self.assertNotIn("prizes", v["me_state"])
        self.assertNotIn("prizes", v["opp_state"])

    def test_cannot_pick_face_down_slot(self):
        """指定面朝下的槽位 = 假装有选择 → 明确拒绝（不静默照做）"""
        b = _battle()
        me = b.players[b.current]
        with self.assertRaises(ValueError):
            zones.take_prizes(b, b.current, 1,
                              first_face_up=len(me.prizes) - 1)

    def test_face_up_flag_persists(self):
        """翻到正面的卡**保持**正面（Bother-Bot），直到被取走"""
        b = _battle()
        taker = b.current
        me = b.players[taker]
        me.prizes[2].face_up = True
        label = _card_label(me.prizes[2].ref)
        _setup_ko(b, face_up=False)
        b.step({"verb": "attack", "attack": 0})
        _choose(b, "down")
        ups = zones.face_up_slots(me)
        self.assertEqual(len(ups), 1, ups)
        self.assertEqual(_card_label(me.prizes[ups[0]].ref), label)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
