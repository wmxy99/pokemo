# -*- coding: utf-8 -*-
"""第九批（2026-10-28）· 解析侧：全量回复 / 招式击倒 / 场地形态 / 牌库顶重排

纪律：只认可精确判定的句式；判不出的写法**不产出 tag**（由兜底登记未建模）。
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


def _tags(text, kind="attack", name="X"):
    return E.to_engine_tags([E.parse_rule(text, kind=kind, name=name)])


class TestHealAllParse(unittest.TestCase):
    def test_bench_one(self):
        r = _tags("Heal all damage from 1 of your Benched Pokémon.")
        self.assertEqual(r["tags"], ["heal:all:bench_one"])

    def test_bench_plural(self):
        r = _tags("Heal all damage from your Benched Pokémon.")
        self.assertEqual(r["tags"], ["heal:all:bench"])

    def test_active_typed_but_chain_rejected(self):
        """「If you healed any damage in this way, …」后续无法精确表达 → 整卡拒绝"""
        r = _tags("Heal all damage from your Active {G} Pokémon. If you healed "
                  "any damage in this way, discard all Energy from that "
                  "Pokémon.", kind="ability")
        self.assertEqual(r["tags"], ["discard_energy:all"])
        self.assertTrue(r["unsupported"], "必须在途中有未建模占位")

    def test_coin_all_heal(self):
        r = _tags("Flip 2 coins. If both of them are heads, heal all damage "
                  "from 1 of your Pokémon.")
        self.assertEqual(r["tags"], ["coin_all:2:heads:heal:all:choose"])
        self.assertEqual(r["unsupported"], [])

    def test_hp_condition_rejected(self):
        r = _tags("Heal all damage from 1 of your Pokémon that has 30 HP or "
                  "less remaining.", kind="supporter")
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])


class TestKnockoutParse(unittest.TestCase):
    def test_opp_active(self):
        r = _tags("Discard 6 Basic {G} Energy cards from your hand, and Knock "
                  "Out your opponent\u2019s Active Pokémon. If you can\u2019t "
                  "discard 6 cards in this way, this attack does nothing.")
        self.assertIn("knockout:opp_active", r["tags"])
        self.assertIn("discard_hand_energy:6:type:grass:required", r["tags"])
        self.assertEqual(r["unsupported"], [])

    def test_both_active(self):
        r = _tags("Both Active Pokémon are Knocked Out.")
        self.assertEqual(r["tags"], ["knockout:both_active"])

    def test_exactly_counters(self):
        r = _tags("Knock Out 1 of your opponent\u2019s Pokémon that has "
                  "exactly 6 damage counters on it.")
        self.assertEqual(r["tags"], ["knockout:opp_counters:6"])

    def test_coin_all_any(self):
        r = _tags("Flip 3 coins. If all of them are heads, Knock Out 1 of your "
                  "opponent\u2019s Pokémon.")
        self.assertEqual(r["tags"], ["coin_all:3:heads:knockout:opp_any"])

    def test_two_coin_branches(self):
        r = _tags("Flip a coin. If heads, Knock Out your opponent\u2019s Active "
                  "Basic Pokémon. If tails, Knock Out 1 of your opponent\u2019s "
                  "Benched Basic Pokémon.")
        self.assertEqual(r["tags"], ["coin:heads:knockout:opp_active:basic",
                                     "coin:tails:knockout:opp_bench:basic"])

    def test_coin_passive_voice(self):
        """被动语态只在**抛币分支内**才算主动击倒（Iono's Electrode）"""
        r = _tags("This Pokémon does 100 damage to itself. Flip a coin. If "
                  "heads, your opponent\u2019s Active Pokémon is Knocked Out.")
        self.assertEqual(r["tags"], ["self:100", "coin:heads:knockout:opp_active"])

    def test_trigger_clause_not_knockout(self):
        """Togekiss「When … is Knocked Out, flip a coin …」是**触发**而非主动击倒

        2026-10-28 第十批起该触发已建模为 `ko_prize_plus:1:coin`（被击倒触发），
        但仍**不得**产出 `knockout:*`（那会凭空多击倒一只）。
        """
        r = _tags("When your opponent\u2019s Active Pokémon is Knocked Out, flip "
                  "a coin. If heads, take 1 more Prize card.", kind="ability")
        self.assertNotIn("knockout:opp_active", r["tags"])
        self.assertEqual(r["tags"], ["ko_prize_plus:1:coin"])


class TestShuffleTopParse(unittest.TestCase):
    def test_opponent_deck(self):
        r = _tags("Look at the top 5 cards of your opponent\u2019s deck and "
                  "put them back in any order.")
        self.assertEqual(r["tags"], ["shuffle_top:5:opp"])

    def test_own_deck(self):
        r = _tags("Look at the top 3 cards of your deck and put them back in "
                  "any order.")
        self.assertEqual(r["tags"], ["shuffle_top:3:own"])

    def test_discard_destination_still_unmodeled(self):
        """「look at the top card. You may discard that card.」仍拒绝（玩家选择）"""
        r = _tags("Once during your turn, you may look at the top card of your "
                  "deck. You may discard that card.", kind="ability")
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])


class TestStadiumFormsParse(unittest.TestCase):
    def test_no_tool_effect(self):
        r = _tags("Pokémon Tools attached to each Pokémon (both yours and your "
                  "opponent\u2019s) have no effect.", kind="stadium")
        self.assertEqual(r["tags"], ["stadium:no_tool_effect"])

    def test_no_retreat_faction(self):
        r = _tags("N\u2019s Pokémon in play (both yours and your opponent\u2019s) "
                  "have no Retreat Cost.", kind="stadium")
        self.assertEqual(r["tags"], ["stadium:no_retreat:faction:ns"])

    def test_attack_plus_faction(self):
        r = _tags("Attacks used by Hop\u2019s Pokémon (both yours and your "
                  "opponent\u2019s) do 30 more damage to the opponent\u2019s "
                  "Active Pokémon (before applying Weakness and Resistance).",
                  kind="stadium")
        self.assertEqual(r["tags"], ["stadium:attack_plus:30:faction:hops"])

    def test_dmg_reduce_faction(self):
        r = _tags("Steven\u2019s Pokémon (both yours and your opponent\u2019s) "
                  "take 30 less damage from attacks from the opponent\u2019s "
                  "Pokémon (after applying Weakness and Resistance).",
                  kind="stadium")
        self.assertEqual(r["tags"],
                         ["stadium:dmg_reduce:30:faction:stevens"])

    def test_status_immune_energy(self):
        r = _tags("Each Pokémon that has any Energy attached (both yours and "
                  "your opponent\u2019s) recovers from all Special Conditions "
                  "and can\u2019t be affected by any Special Conditions.",
                  kind="stadium")
        self.assertEqual(r["tags"],
                         ["stadium:status_immune:require:energy"])

    def test_evolve_same_turn(self):
        r = _tags("Each player\u2019s {G} Pokémon can evolve into {G} Pokémon "
                  "during the turn they play those Pokémon, except during "
                  "their first turn.", kind="stadium")
        self.assertEqual(r["tags"], ["stadium:evolve_same_turn:type:grass"])

    def test_dmg_immune_no_rule_box(self):
        r = _tags("Prevent all damage done to Pokémon that don\u2019t have a "
                  "Rule Box (both yours and your opponent\u2019s) by attacks "
                  "from the opponent\u2019s Pokémon {ex} and Pokémon {V}. "
                  "(Pokémon {ex}, Pokémon {V}, etc. have Rule Boxes.)",
                  kind="stadium")
        self.assertEqual(r["tags"],
                         ["stadium:dmg_immune:box:none:from:rulebox"])

    def test_unknown_faction_rejected(self):
        r = _tags("Cynthia\u2019s Pokémon in play (both yours and your "
                  "opponent\u2019s) have no Retreat Cost.", kind="stadium")
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])


if __name__ == "__main__":
    unittest.main()
