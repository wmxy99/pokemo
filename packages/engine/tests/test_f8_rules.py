# -*- coding: utf-8 -*-
"""F8 · 规则改写 / 去向改写（2026-11-08）

这批卡的共同点：**改的不是数值，而是规则或去向**。所以错法也都不报错 ——
卡「看起来」结算了，只是去了错的地方、或该被挡住时没挡住：

1. `discard_self`（Revavroom ex）—— 「弃自身与全部附加卡」**不是击倒**：
   不取奖赏、不发 `ko` 事件；但战斗位空出时后备要顶上（与击倒一致）。
2. `devolve … :each:to:deck`（Espeon ex）—— **全体**退化，且进化卡进**牌库**。
   方向写错（进手牌）是纯静默差异：手牌能再用、牌库不能。
3. `passive:no_hand_return`（Milotic）—— 禁令要挡住**宝可梦和附加卡**两条路；
   只挡本体等于半个禁令。
4. `passive:ko_to_hand`（Gengar）—— 只有「被对手招式击倒」才进手牌；
   自身效果/状态击倒照常进弃牌堆。
5. `hand_bench`（Klinklang）—— 在手牌里发动、直接铺后备；条件（对手有 2 阶）
   与「后备没满」两处都要挡。

运行：cd packages/engine && python -m unittest tests.test_f8_rules -v
"""
from __future__ import annotations

import unittest

from engine.battle import Battle
from engine.cards import (AbilityDef, AttackDef, EnergyDef, EnergyRef, PokeRef,
                          PokemonDef, TrainerDef, TrainerRef, make_pokemon)
from engine.state import GameConfig


def _deck(tag: str = "A", n: int = 30) -> list:
    return [PokeRef(PokemonDef(f"Fill{tag}{i}", hp=100, ptype="colorless"))
            for i in range(n)]


def _battle(seed: int = 5) -> Battle:
    return Battle(_deck("A"), _deck("B"), seed=seed,
                  cfg=GameConfig(first_turn_no_attack=False,
                                 first_turn_no_supporter=False))


BASIC = PokemonDef("Tiny", hp=70, ptype="psychic")
STAGE1 = PokemonDef("Mid", hp=110, ptype="psychic", stage=1,
                    evolves_from="Tiny")
STAGE2 = PokemonDef("Big", hp=160, ptype="psychic", stage=2, evolves_from="Mid")


def _evolved() -> object:
    fp = make_pokemon(BASIC)
    fp.evolve_to(STAGE1)
    return fp


# --------------------------- Revavroom ex ---------------------------
class TestDiscardSelf(unittest.TestCase):
    """`Discard this Pokémon and all attached cards.`"""

    def _setup(self):
        b = _battle()
        me = b.players[b.current]
        opp = b.players[1 - b.current]
        me.active = make_pokemon(PokemonDef(
            "SelfBurner", hp=280, ptype="metal",
            attacks=(AttackDef("Shattering Speed", cost=0, damage=10,
                               effects=("discard_self:all",)),)))
        me.bench = [make_pokemon(PokemonDef("Backup", hp=100,
                                            ptype="colorless"))]
        me.active.tool = "tool:hp_plus:30"
        me.active.tool_ref = TrainerRef(TrainerDef("tool:hp_plus:30",
                                                   kind="tool", name="T"))
        me.active.add_energy("metal")
        opp.active = make_pokemon(PokemonDef("Wall", hp=900, ptype="colorless"))
        me.hand, me.deck, opp.deck = [], _deck(), _deck()
        return b, me, opp

    def test_discards_self_and_attached_but_is_not_a_ko(self):
        b, me, opp = self._setup()
        prizes_before = opp.prizes_left
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(me.active.name, "Backup", "后备没有顶上")
        self.assertIn("SelfBurner", me.discard, "本体没进弃牌堆")
        # 附加卡（道具 + 能量）也进弃牌堆
        self.assertTrue(any(isinstance(x, TrainerRef) for x in me.discard_refs),
                        "道具卡没进弃牌堆")
        self.assertTrue(any(isinstance(x, EnergyRef) for x in me.discard_refs),
                        "能量卡没进弃牌堆")
        # **不是击倒**：不取奖赏、不发 ko 事件
        self.assertEqual(opp.prizes_left, prizes_before,
                         "弃自身被当成了击倒（对手白拿奖赏）")
        self.assertFalse([e for e in b.events if e.get("type") == "knockout"],
                         "弃自身不该发 knockout 事件")

    def test_no_bench_loses(self):
        """场上没有替补时弃掉战斗位 = 场上无宝可梦 → 判负（不是抛错）"""
        b, me, opp = self._setup()
        me.bench = []
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertTrue(b.finished)
        self.assertEqual(b.winner, 1 - b.current)


# --------------------------- Espeon ex ---------------------------
class TestDevolveEachToDeck(unittest.TestCase):
    """`Devolve each … by shuffling the highest Stage card into their deck.`"""

    def _setup(self, with_passive=True):
        b = _battle()
        me = b.players[b.current]
        opp = b.players[1 - b.current]
        eff = ("devolve:opp:1:each:to:deck",) if with_passive else ()
        me.active = make_pokemon(PokemonDef(
            "Espeon", hp=270, ptype="psychic",
            attacks=(AttackDef("Amazez", cost=0, damage=0, effects=eff),)))
        opp.active = _evolved()
        opp.bench = [_evolved()]
        opp.hand = []
        opp.deck = _deck("B")
        me.hand, me.deck = [], _deck()
        return b, me, opp

    def test_all_evolved_devolve_into_deck(self):
        b, me, opp = self._setup()
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(opp.active.pdef.name, "Tiny", "战斗位没退化")
        self.assertEqual(opp.bench[0].pdef.name, "Tiny", "后备没退化（each 漏了）")
        # 进化卡进**牌库**，不能进手牌（回合切换会正常抽 1 张，故只查进化卡名）
        self.assertNotIn("Mid", [c.pdef.name for c in opp.hand
                                 if isinstance(c, PokeRef)],
                         "进化卡进了手牌（方向错：应洗回牌库）")
        deck_names = [c.pdef.name for c in opp.deck if isinstance(c, PokeRef)]
        self.assertEqual(deck_names.count("Mid"), 2, "进化卡没洗回牌库")


# --------------------------- Milotic ---------------------------
class TestNoHandReturn(unittest.TestCase):
    """`Your opponent's Pokémon in play and all attached cards can't be put
    into your opponent's hand.`"""

    def _setup(self, milotic=True):
        b = _battle()
        me = b.players[b.current]
        opp = b.players[1 - b.current]
        me.active = make_pokemon(PokemonDef("Mine", hp=120, ptype="water"))
        me.active.add_energy("water")
        me.hand, me.deck = [], _deck()
        ab = ((AbilityDef("Mentally Calm", tag="passive:no_hand_return",
                          location="field"),) if milotic else ())
        opp.active = make_pokemon(PokemonDef("Calm", hp=120, ptype="water",
                                             abilities=ab))
        opp.deck = _deck("B")
        return b, me, opp

    def test_bounce_blocked(self):
        b, me, opp = self._setup()
        b._bounce_owner(b.current, me.active, "hand")
        self.assertIsNotNone(me.active, "被回手了（禁令没生效）")
        self.assertEqual([c for c in me.hand if isinstance(c, PokeRef)], [],
                         "本体进了手牌")

    def test_bounce_allowed_without_milotic(self):
        b, me, opp = self._setup(milotic=False)
        b._bounce_owner(b.current, me.active, "hand")
        self.assertEqual([c.pdef.name for c in me.hand if isinstance(c, PokeRef)],
                         ["Mine"], "无禁令时应当回手")

    def test_attached_energy_also_blocked(self):
        """禁令覆盖「all attached cards」—— 只挡本体是半个禁令"""
        b, me, opp = self._setup()
        got = _energy_to_hand(b, b.current, me.active, 1)
        self.assertEqual(got, 0, "能量照常回手（禁令只挡了本体）")


def _energy_to_hand(b, owner, fp, n):
    from engine.battle import _energy_to_hand as fn
    return fn(b, owner, fp, n)


# --------------------------- Gengar ---------------------------
class TestKoToHand(unittest.TestCase):
    """`If this Pokémon is Knocked Out by damage from an attack …, put it into
    your hand instead of the discard pile.`"""

    def _setup(self):
        b = _battle()
        me = b.players[b.current]
        opp = b.players[1 - b.current]
        me.active = make_pokemon(PokemonDef(
            "Attacker", hp=500, ptype="darkness",
            attacks=(AttackDef("Mind Jack", cost=0, damage=200),)))
        me.hand, me.deck = [], _deck()
        opp.active = make_pokemon(PokemonDef(
            "Gengar", hp=130, ptype="psychic",
            abilities=(AbilityDef("Infinite Shadow", tag="passive:ko_to_hand",
                                  location="field"),)))
        opp.hand, opp.deck = [], _deck("B")
        return b, me, opp

    def test_ko_by_attack_returns_to_hand(self):
        b, me, opp = self._setup()
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertIn("Gengar", [c.pdef.name for c in opp.hand
                                 if isinstance(c, PokeRef)],
                      "被招式击倒后没进手牌")
        self.assertNotIn("Gengar", opp.discard, "既进手牌又进弃牌堆")
        # 仍然是「被击倒」：取奖赏方（我方）少 1 张
        self.assertEqual(me.prizes_left, b.cfg.prizes - 1, "击倒没取奖赏")

    def test_non_attack_ko_goes_to_discard(self):
        """不是被对手招式击倒（自身效果/状态）→ 照常进弃牌堆"""
        b, me, opp = self._setup()
        b._ko_from_attack = False
        b._resolve_ko(1 - b.current, opp.active)
        self.assertIn("Gengar", opp.discard, "非招式击倒也进了手牌")


# --------------------------- Klinklang ---------------------------
class TestHandToBench(unittest.TestCase):
    """`Once during your turn, if this Pokémon is in your hand and your opponent
    has any Stage 2 Pokémon in play, you may put this Pokémon onto your Bench.`"""

    def _setup(self, opp_stage2=True, bench_full=False):
        b = _battle()
        me = b.players[b.current]
        opp = b.players[1 - b.current]
        me.active = make_pokemon(PokemonDef("Mine", hp=100, ptype="colorless"))
        me.bench = ([make_pokemon(PokemonDef(f"B{i}", hp=100,
                                             ptype="colorless"))
                     for i in range(b.cfg.max_bench)] if bench_full else [])
        me.deck = _deck()
        me.hand = [PokeRef(PokemonDef(
            "Klinklang", hp=140, ptype="metal", stage=2, evolves_from="Klang",
            abilities=(AbilityDef("Emergency Rotation", tag="hand_bench:opp_stage2",
                                  location="hand"),)))]
        opp.active = make_pokemon(PokemonDef(
            "Big", hp=160, ptype="colorless",
            stage=2 if opp_stage2 else 1))
        opp.deck = _deck("B")
        return b, me, opp

    def _legals(self, b):
        return [a for a in b.legal_actions()
                if a.get("verb") == "ability"
                and a.get("name") == "Emergency Rotation"]

    def test_listed_and_played(self):
        b, me, opp = self._setup()
        self.assertTrue(self._legals(b), "条件满足却没进合法动作表")
        r = b.step({"verb": "ability", "name": "Emergency Rotation", "hand": 0,
                    "tag": "hand_bench:opp_stage2"})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual([fp.name for fp in me.bench], ["Klinklang"])
        self.assertEqual(me.hand, [], "上场后卡还留在手牌")
        self.assertNotIn("Klinklang", me.discard, "上场被当成了弃置")
        self.assertTrue(me.bench[0].just_played, "当回合应标记刚上场")

    def test_not_listed_without_stage2(self):
        b, me, opp = self._setup(opp_stage2=False)
        self.assertFalse(self._legals(b), "对手没有 2 阶却可发动")

    def test_not_listed_when_bench_full(self):
        b, me, opp = self._setup(bench_full=True)
        self.assertFalse(self._legals(b), "后备已满却还列出动作（假动作）")


if __name__ == "__main__":
    unittest.main()
