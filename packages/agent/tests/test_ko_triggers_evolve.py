# -*- coding: utf-8 -*-
"""第十批（2026-10-28）· 解析侧：进化触发链 / 「in this way」后续 / 被击倒触发

纪律：
  · 进化触发特性**必须**包裹成 `evolve_act:`（否则引擎会当每回合一次的 field 特性）；
  · 「in this way」后续必须带 `:if_prev`（否则会变成无条件执行）；
  · 被击倒触发只认三种可精确表达的形态；其它写法如实未建模。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG = HERE.parent
for p in (str(PKG), str(PKG.parent / "engine")):
    if p not in sys.path:
        sys.path.insert(0, p)

from agent import effects as E  # noqa: E402

WHIMSICOTT = ("When you play this Pokémon from your hand to evolve 1 of your "
              "Pokémon during your turn, you may heal all damage from your "
              "Active {G} Pokémon. If you healed any damage in this way, "
              "discard all Energy from that Pokémon.")
DACHSBUN = ("Once during your turn, when you play this Pokémon from your hand "
            "to evolve 1 of your Pokémon, you may use this Ability. Heal all "
            "damage from each of your Evolution Pokémon. If you healed any "
            "damage in this way, discard all Energy from those Pokémon.")
KADABRA = ("Once during your turn, when you play this Pokémon from your hand "
           "to evolve 1 of your Pokémon, you may use this Ability. Draw 2 "
           "cards.")
NINJASK = ("Once during your turn, when you play this Pokémon from your hand "
           "to evolve 1 of your Pokémon, you may use this Ability. Search your "
           "deck for a Shedinja and put it onto your Bench. Then, shuffle your "
           "deck.")
SILCOON = ("Once during your turn, when you play this Pokémon from your hand "
           "to evolve 1 of your Pokémon, you may use this Ability. Search your "
           "deck for a Silcoon or a Cascoon and put it onto your Bench. Then, "
           "shuffle your deck.")
TOGEKISS = ("When your opponent’s Active Pokémon is Knocked Out, flip a coin. "
            "If heads, take 1 more Prize card. The effect of Wonder Kiss "
            "doesn’t stack.")
MARACTUS = ("If this Pokémon is in the Active Spot and is Knocked Out by "
            "damage from an attack from your opponent’s Pokémon, put 6 damage "
            "counters on the Attacking Pokémon.")
HUNTAIL = ("When 1 of your {W} Pokémon is Knocked Out by damage from an attack "
           "from your opponent’s Pokémon, you may put all Basic {W} Energy "
           "attached to that Pokémon into your hand instead of the discard "
           "pile.")


def _tags(text, kind="ability", name="X"):
    return E.to_engine_tags([E.parse_rule(text, kind=kind, name=name)])


class TestEvolveChainParse(unittest.TestCase):
    def test_whimsicott_heal_then_discard(self):
        r = _tags(WHIMSICOTT)
        self.assertEqual(r["tags"], [
            "evolve_act:heal:all:active:type:grass"
            "|discard_energy:all:if_prev"])
        self.assertEqual(r["unsupported"], [])

    def test_dachsbun_evolved_scope(self):
        r = _tags(DACHSBUN)
        self.assertEqual(r["tags"], [
            "evolve_act:heal:all:evolved"
            "|discard_energy:all:evolved:if_prev"])
        self.assertEqual(r["unsupported"], [])

    def test_simple_draw_wrapped(self):
        self.assertEqual(_tags(KADABRA)["tags"], ["evolve_act:draw:2"])

    def test_search_bench_by_name(self):
        self.assertEqual(_tags(NINJASK)["tags"],
                         ["evolve_act:search_bench:1:name:shedinja"])

    def test_search_bench_two_names(self):
        self.assertEqual(_tags(SILCOON)["tags"],
                         ["evolve_act:search_bench:1:name:silcoon+cascoon"])

    def test_condition_modeled_as_gate(self):
        """Noctowl 的 Tera 前置条件 → 第一百零二批起 `:gate:tera_in_play`

        决策反转留痕：旧口径说「引擎没有条件层」。现在 `evolve_act:` 接受
        `:gate:<cond>`（`_eval_condition` 可精确求值），不满足时整条不执行。

        ⚠ 认不出的条件仍必须整卡拒收 —— 无条件放通等于「有没有 Tera 都能检索」。
        """
        r = _tags("Once during your turn, when you play this Pokémon from your "
                  "hand to evolve 1 of your Pokémon, if you have any Tera "
                  "Pokémon in play, you may search your deck for up to 2 "
                  "Trainer cards, reveal them, and put them into your hand. "
                  "Then, shuffle your deck.")
        self.assertEqual(r["tags"],
                         ["evolve_act:search_trainer:2:gate:tera_in_play"])
        self.assertEqual(r["unsupported"], [])

    def test_unknown_followup_rejected(self):
        """回满后接「each of your Pokémon」——对象不配套 → 整卡保持未建模

        通用分支会把它读成**无条件** `discard_energy:all`；因为有未建模占位，
        整卡仍然不进池（准入是「整卡不收」），不会静默执行半套效果。
        """
        r = _tags("When you play this Pokémon from your hand to evolve 1 of "
                  "your Pokémon during your turn, you may heal all damage "
                  "from your Active {G} Pokémon. If you healed any damage in "
                  "this way, discard all Energy from each of your Pokémon.")
        self.assertTrue(r["unsupported"])
        self.assertFalse(any(t.startswith("heal:all") for t in r["tags"]),
                         "不得产出半套的 heal:all")


class TestKnockoutTriggerParse(unittest.TestCase):
    def test_prize_plus_coin(self):
        self.assertEqual(_tags(TOGEKISS)["tags"], ["ko_prize_plus:1:coin"])

    def test_on_ko_counter(self):
        self.assertEqual(_tags(MARACTUS)["tags"], ["on_ko:counter:60"])

    def test_on_ko_energy_to_hand(self):
        self.assertEqual(_tags(HUNTAIL)["tags"],
                         ["on_ko:energy_to_hand:water"])

    def test_color_mismatch_rejected(self):
        text = HUNTAIL.replace("Basic {W} Energy", "Basic {F} Energy")
        self.assertEqual(_tags(text)["tags"], [])
        self.assertTrue(_tags(text)["unsupported"])

    def test_counter_without_active_clause_rejected(self):
        text = ("If this Pokémon is Knocked Out by damage from an attack from "
                "your opponent’s Pokémon, put 6 damage counters on the "
                "Attacking Pokémon.")
        self.assertEqual(_tags(text)["tags"], [])
        self.assertTrue(_tags(text)["unsupported"])


if __name__ == "__main__":
    unittest.main()
