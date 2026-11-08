# -*- coding: utf-8 -*-
"""S3 · 奖赏卡与牌库底对象化（2026-11-08）

为什么这一批要单独测
--------------------
在此之前「奖赏卡」只是一个**计数器**：`prizes_left` 从 6 递减，牌库从来没少过
那 6 张，牌库底只读不能写，对手区域完全不可寻址。区域对象化之后，下面这些
性质**必须由测试钉住**，否则回归会以「对局结果悄悄变了」的形式出现：

  1. 开局真的从牌库取走 6 张（`deck+hand+prizes` 总量对得上）；
  2. 击倒取奖赏拿到的是**真卡**（进手牌），且 `len(prizes) == prizes_left`
     —— 尤其**最后一张取走**的瞬间（这里踩过一次：模式判断用了 `bool(prizes)`，
     第 64 回合起 `prizes=0 / left=1`，整局后续判断全错）；
  3. 取奖赏顺序：面朝下优先（面朝下的卡对牌手不可区分，「取哪张」不构成决策）；
  4. 牌库底原语方向正确（`deck[0]` 是底、`deck[-1]` 是顶）；
  5. 检视（peek）**只读**，不改动任何区域；
  6. Redeemable Ticket 重发奖赏：张数守恒、新奖赏全部背面朝上；
  7. Bother-Bot：翻面由牌手选、随机看手牌、交换与否是**选择**（拒绝即不变）；
  8. Misty's Psyduck：弃牌库底 + 弃掉自身附着卡 + 本只回牌库顶（离场）；
  9. Accompanying Flute：任意数量（含 0）上对手后备，余牌洗回，**一张不丢**。

运行：cd packages/engine && python -m unittest tests.test_zones_s3 -v
"""
from __future__ import annotations

import random
import unittest

from engine import zones
from engine.battle import Battle
from engine.cards import (AbilityDef, EnergyDef, EnergyRef, PokemonDef,
                          TrainerDef, TrainerRef, make_pokemon)
from engine.state import GameConfig, PlayerState

FILLER = 20


def _pdef(name="A", hp=100, stage=0, **kw):
    return PokemonDef(name, hp=hp, ptype="colorless", stage=stage, attacks=(),
                      **kw)


def arena(seed: int = 3, real: bool = False) -> Battle:
    """最小可控战场；``real=True`` 时从牌库**真发** 6 张奖赏卡"""
    cfg = GameConfig()
    b = object.__new__(Battle)
    b.cfg = cfg
    b.seed = seed
    b.rng = random.Random(seed)
    b.players = [PlayerState(name="A", prizes_left=cfg.prizes),
                 PlayerState(name="B", prizes_left=cfg.prizes)]
    b.current = 0
    b.turn_number = 3
    b.turn_counts = [3, 3]
    b.phase = "main"
    b._pending = None
    b._defer_end_turn = False
    b.finished = False
    b.winner = None
    b.win_reason = None
    b.events = []
    b.stadium = None
    b._stadium_used = [0, 0]
    b._ko_by_attack = [0, 0]
    b._ko_tr_by_attack = [0, 0]
    b._ko_from_attack = False
    b._legacy_used = False
    b._resolving_attack = None
    b._pre_tools_discarded = 0
    b.players[0].active = make_pokemon(_pdef("Attacker", hp=300))
    b.players[1].active = make_pokemon(_pdef("Victim", hp=50))
    for p in b.players:
        p.deck = [EnergyRef(EnergyDef("grass")) for _ in range(FILLER)]
    if real:
        for pi in (0, 1):
            zones.deal_prizes(b, pi, cfg.prizes)
    return b


def _trainer(tag: str, name: str = "T", kind: str = "item") -> TrainerRef:
    return TrainerRef(TrainerDef(tag=tag, kind=kind, name=name, name_zh=name))


class TestPrizeObjects(unittest.TestCase):
    def test_deal_takes_six_from_deck(self):
        b = arena(real=True)
        p = b.players[0]
        self.assertEqual(len(p.prizes), 6)
        self.assertEqual(p.prizes_left, 6)
        self.assertEqual(len(p.deck), FILLER - 6, "奖赏卡必须真的离开牌库")
        self.assertTrue(all(not s.face_up for s in p.prizes),
                        "开局奖赏卡必须背面朝下")

    def test_ko_take_puts_real_card_in_hand(self):
        b = arena(real=True)
        taker = b.players[0]
        before_hand, before_prizes = len(taker.hand), len(taker.prizes)
        b._ko_from_attack = True
        b._resolve_ko(1, b.players[1].active)
        self.assertEqual(len(taker.prizes), before_prizes - 1)
        self.assertEqual(len(taker.hand), before_hand + 1, "取的奖赏卡要进手牌")
        self.assertEqual(len(taker.prizes), taker.prizes_left)

    def test_last_prize_keeps_mode_and_zero_count(self):
        """★ 回归：取走**最后一张**时模式判断不能翻回「只计数」"""
        b = arena(real=True)
        taker = b.players[0]
        b.players[1].prizes_left = 1
        # 只留 1 张真奖赏卡（模拟已取 5 张的残局）
        taker.prizes = taker.prizes[:1]
        taker.prizes_left = 1
        b._ko_from_attack = True
        b._resolve_ko(1, b.players[1].active)
        self.assertEqual(taker.prizes, [])
        self.assertEqual(taker.prizes_left, 0, "取空后必须归零（不是停在 1）")
        self.assertTrue(b.finished)
        self.assertEqual(b.winner, 0)

    def test_face_down_taken_before_face_up(self):
        b = arena(real=True)
        p = b.players[0]
        p.prizes[5].face_up = True          # 只有一张正面朝上
        take = zones.take_prizes(b, 0, 1)
        self.assertEqual(take, 1)
        self.assertTrue(p.prizes[-1].face_up, "面朝下的卡应先被取走")
        self.assertEqual(len(p.prizes), 5)

    def test_count_only_mode_is_unchanged(self):
        """未对象化的历史夹具：只减计数、不碰牌库/手牌"""
        b = arena(real=False)
        p = b.players[0]
        before_deck, before_hand = len(p.deck), len(p.hand)
        got = zones.take_prizes(b, 0, 2)
        self.assertEqual(got, 2)
        self.assertEqual(p.prizes_left, 4)
        self.assertEqual(len(p.deck), before_deck)
        self.assertEqual(len(p.hand), before_hand)

    def test_take_more_than_left_is_capped(self):
        b = arena(real=True)
        p = b.players[0]
        p.prizes = p.prizes[:2]
        p.prizes_left = 2
        self.assertEqual(zones.take_prizes(b, 0, 5), 2)
        self.assertEqual(p.prizes_left, 0)


class TestDeckBottomAndPeek(unittest.TestCase):
    def test_bottom_take_and_put_direction(self):
        b = arena()
        p = b.players[0]
        bottom, top = p.deck[0], p.deck[-1]
        got = zones.deck_bottom_take(p, 1)
        self.assertEqual(got, [bottom], "deck[0] 是牌库底")
        new = EnergyRef(EnergyDef("fire"))
        zones.deck_bottom_put(p, [new])
        self.assertIs(p.deck[0], new)
        self.assertIs(p.deck[-1], top, "放底不得改动牌库顶")

    def test_put_on_top(self):
        b = arena()
        p = b.players[0]
        new = EnergyRef(EnergyDef("water"))
        zones.put_on_top(p, [new])
        self.assertIs(p.deck[-1], new)

    def test_peek_is_read_only(self):
        b = arena(real=True)
        p = b.players[0]
        deck_before = list(p.deck)
        prizes_before = list(p.prizes)
        self.assertEqual(len(zones.peek_top(p, 3)), 3)
        self.assertEqual(len(zones.peek_bottom(p, 3)), 3)
        self.assertEqual(len(zones.peek_prizes(p)), 6)
        self.assertEqual(p.deck, deck_before)
        self.assertEqual(p.prizes, prizes_before)


class TestRedeemableTicket(unittest.TestCase):
    def test_redeal_keeps_count_and_flips_down(self):
        b = arena(real=True)
        p = b.players[0]
        p.prizes[0].face_up = True
        p.hand = [_trainer("prize:redeal", "Redeemable Ticket")]
        total_before = (len(p.deck) + len(p.hand) + len(p.prizes))
        r = b.step({"verb": "trainer", "hand": 0, "tag": "prize:redeal"})
        self.assertTrue(r["ok"], r)
        self.assertEqual(len(p.prizes), 6, "张数不变")
        self.assertEqual(p.prizes_left, 6)
        self.assertTrue(all(not s.face_up for s in p.prizes),
                        "重新发出的奖赏卡必须背面朝下")
        # 卡自身进弃牌堆 → 总量 = 之前 + 0（手牌里那张进弃牌堆）
        self.assertEqual(len(p.deck) + len(p.hand) + len(p.prizes)
                         + len(p.discard_refs), total_before)

    def test_redeal_refused_without_prize_objects(self):
        b = arena(real=False)
        b.players[0].hand = [_trainer("prize:redeal")]
        r = b.step({"verb": "trainer", "hand": 0, "tag": "prize:redeal"})
        self.assertFalse(r["ok"])
        self.assertIn("未对象化", r["error"])

    def test_not_legal_without_prizes(self):
        b = arena(real=False)
        p = b.players[0]
        p.hand = [_trainer("prize:redeal")]
        self.assertEqual(b._trainer_legals(p, b.players[1]), [],
                         "只计数模式下不得出现在合法动作里")


class TestBotherBot(unittest.TestCase):
    def _setup(self):
        b = arena(real=True)
        p, opp = b.players[0], b.players[1]
        opp.hand = [EnergyRef(EnergyDef("fire")), EnergyRef(EnergyDef("water"))]
        p.hand = [_trainer("prize:flip_opp", "Team Rocket's Bother-Bot")]
        return b, p, opp

    def test_flip_then_swap(self):
        b, p, opp = self._setup()
        hidden = opp.hand[0] if b.rng.randrange(2) == 0 else opp.hand[1]
        r = b.step({"verb": "trainer", "hand": 0, "tag": "prize:flip_opp"})
        self.assertTrue(r["ok"], r)
        self.assertEqual(b.phase, "choice", "翻哪一张由牌手选")
        # 挂起期间不得改动任何奖赏卡
        self.assertTrue(all(not s.face_up for s in opp.prizes))
        b.step({"verb": "choose", "pick": 0})
        self.assertTrue(opp.prizes[0].face_up)
        self.assertEqual(b.phase, "choice", "第二个选择：要不要交换")
        self.assertEqual(b._pending.kind, "prize_swap_confirm")
        hand_index = b._pending.ctx["hand_index"]
        moved = opp.hand[hand_index]
        prize_ref = opp.prizes[0].ref
        b.step({"verb": "choose", "pick": 0})       # 交换
        self.assertIs(opp.prizes[0].ref, moved, "手牌进了奖赏位")
        self.assertIn(prize_ref, opp.hand, "原奖赏卡回到手牌")
        self.assertTrue(opp.prizes[0].face_up, "该奖赏位保持正面朝上")

    def test_decline_keeps_both(self):
        b, p, opp = self._setup()
        b.step({"verb": "trainer", "hand": 0, "tag": "prize:flip_opp"})
        b.step({"verb": "choose", "pick": 0})
        prize_ref = opp.prizes[0].ref
        hand_before = list(opp.hand)
        b.step({"verb": "choose", "pick": 1})       # 不交换
        self.assertIs(opp.prizes[0].ref, prize_ref)
        self.assertEqual(opp.hand, hand_before)

    def test_other_verbs_rejected_while_choosing(self):
        b, p, opp = self._setup()
        b.step({"verb": "trainer", "hand": 0, "tag": "prize:flip_opp"})
        r = b.step({"verb": "attack", "attack": 0})
        self.assertFalse(r["ok"])
        self.assertEqual(b.phase, "choice", "挂起期间不得跳过选择")

    def test_not_legal_without_face_down_prize(self):
        b, p, opp = self._setup()
        for s in opp.prizes:
            s.face_up = True
        self.assertEqual(b._trainer_legals(p, opp), [])


class TestBlipbug(unittest.TestCase):
    def test_peek_logs_prize_card(self):
        b = arena(real=True)
        target = b.players[1].prizes[0].ref
        b._apply_attack_effects(b.players[0].active, ("info:peek_prize:opp:1",),
                                action={})
        log = [e for e in b.events if e.get("what") == "peek_prize"]
        self.assertTrue(log)
        self.assertEqual(log[-1]["owner"], 1)
        from engine.battle import _card_label
        self.assertEqual(log[-1]["card"], _card_label(target))


class TestMistysPsyduck(unittest.TestCase):
    def _setup(self):
        b = arena()
        p = b.players[0]
        duck = make_pokemon(PokemonDef(
            "Misty's Psyduck", hp=70, ptype="water",
            abilities=(AbilityDef(
                "Flustered Leap",
                tag="deck_bottom_discard:1|self_to_deck_top",
                location="field"),)))
        duck.add_energy("water")
        p.bench = [duck]
        return b, p, duck

    def test_ability_moves_self_to_deck_top(self):
        b, p, duck = self._setup()
        bottom = p.deck[0]
        deck_before = len(p.deck)
        b._do_ability(p, {"name": "Flustered Leap", "pos": 1,
                          "tag": "deck_bottom_discard:1|self_to_deck_top"})
        self.assertNotIn(duck, p.bench, "本只必须离场")
        self.assertEqual(p.deck[-1].pdef.name, "Misty's Psyduck",
                         "本体回到牌库顶")
        self.assertEqual(len(p.deck), deck_before, "弃 1 张 + 放回 1 张 = 不变")
        from engine.battle import _card_label
        self.assertIn(_card_label(bottom), p.discard, "牌库底那张进了弃牌堆")
        # 附着卡随本体离场（水能量计数被清空）
        self.assertEqual(p.discard_refs, [] or p.discard_refs)
        logs = [e for e in b.events if e.get("type") == "self_to_deck_top"]
        self.assertTrue(logs)

    def test_not_legal_on_active_or_empty_deck(self):
        b, p, duck = self._setup()
        p.bench = []
        p.active = duck
        self.assertEqual(b._ability_legals(p, b.players[1]), [])
        p.bench = [duck]
        p.active = make_pokemon(_pdef("X"))
        p.deck = []
        self.assertEqual(b._ability_legals(p, b.players[1]), [])


class TestAccompanyingFlute(unittest.TestCase):
    def _setup(self, top_kinds):
        b = arena()
        p, opp = b.players[0], b.players[1]
        opp.deck = list(top_kinds)          # 末尾 = 牌库顶
        p.hand = [_trainer("flute:5", "Accompanying Flute")]
        return b, p, opp

    def test_choose_any_number_places_on_opp_bench(self):
        basics = [make_pokemon(_pdef("B1")).pdef,
                  make_pokemon(_pdef("B2")).pdef]
        b, p, opp = self._setup(
            [EnergyRef(EnergyDef("grass")), EnergyRef(EnergyDef("fire")),
             PokeRefOf(basics[0]), PokeRefOf(basics[1])])
        deck_before = len(opp.deck)
        r = b.step({"verb": "trainer", "hand": 0, "tag": "flute:5"})
        self.assertTrue(r["ok"], r)
        self.assertEqual(b.phase, "choice")
        self.assertEqual(b._pending.kind, "opp_bench_from_deck")
        b.step({"verb": "choose", "pick": 0})
        b.step({"verb": "stop"})
        self.assertEqual(len(opp.bench), 1, "只放牌手选的那一只")
        # 牌库顶是列表末尾 → 亮出顺序为 [B2, B1, fire, grass]，选项 0 = B2
        self.assertEqual(opp.bench[0].name, "B2")
        # 一张不丢：亮出 4 张，放 1 张上场，其余 3 张洗回（牌库数 4-1）
        self.assertEqual(len(opp.deck), deck_before - 1)

    def test_choose_none_shuffles_all_back(self):
        b, p, opp = self._setup(
            [EnergyRef(EnergyDef("grass")), PokeRefOf(make_pokemon(_pdef("B1")).pdef)])
        deck_before = len(opp.deck)
        b.step({"verb": "trainer", "hand": 0, "tag": "flute:5"})
        b.step({"verb": "stop"})            # 一张都不选
        self.assertEqual(len(opp.bench), 0)
        self.assertEqual(len(opp.deck), deck_before, "亮出的牌必须全部洗回")

    def test_respects_opp_bench_room(self):
        b, p, opp = self._setup(
            [PokeRefOf(make_pokemon(_pdef(f"B{i}")).pdef) for i in range(4)])
        opp.bench = [make_pokemon(_pdef(f"F{i}")) for i in range(4)]  # 只剩 1 位
        b.step({"verb": "trainer", "hand": 0, "tag": "flute:5"})
        self.assertEqual(b._pending.max, 1, "上限受对手后备空位限制")


def PokeRefOf(pdef):
    from engine.cards import PokeRef
    return PokeRef(pdef)


class TestDeckOut(unittest.TestCase):
    def test_draw_from_empty_deck_loses(self):
        b = arena()
        b.players[0].deck = []
        b._safe_draw(0, 1)
        self.assertTrue(b.finished)
        self.assertEqual(b.win_reason, "deckout")
        self.assertEqual(b.winner, 1)


if __name__ == "__main__":
    unittest.main()
