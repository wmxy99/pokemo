# -*- coding: utf-8 -*-
"""第五十七批（2026-11-04）· 解析用例：**常驻禁打**（fx:play_block）

与**限时**形态（`play_lock:<kind>:opp:turn`，Budew / Frillish）严格区分：
前者「As long as … in the Active Spot」一直生效，后者只封下个回合。
落错方向 = 要么永久封锁，要么封锁一整段消失。
"""
from __future__ import annotations
import unittest

from agent import effects as E


def _tags(text, kind="ability", name="x"):
    return E.to_engine_tags([E.parse_rule(text, kind=kind, name=name)])


class TestPlayBlock(unittest.TestCase):
    def test_item(self):
        self.assertEqual(
            _tags("As long as this Pokémon is in the Active Spot, your opponent "
                  "can’t play any Item cards from their hand.")["tags"],
            ["fx:play_block:item:active"])

    def test_item_and_tool(self):
        self.assertEqual(
            _tags("As long as this Pokémon is in the Active Spot, your opponent "
                  "can’t play any Item cards or Pokémon Tool cards from their "
                  "hand.")["tags"],
            ["fx:play_block:item+tool:active"])

    def test_ability_pokemon_with_exception(self):
        self.assertEqual(
            _tags("As long as this Pokémon is in the Active Spot, your opponent "
                  "can’t play any Pokémon that has an Ability from their hand, "
                  "except for Team Rocket’s Pokémon.")["tags"],
            ["fx:play_block:ability_pokemon:active:except:teamrockets"])

    def test_timed_form_stays_play_lock(self):
        """限时形态仍走 `play_lock`，**不得**被本族认领成常驻"""
        self.assertEqual(
            _tags("During your opponent’s next turn, they can’t play any Item "
                  "cards from their hand.", kind="attack")["tags"],
            ["play_lock:item:opp:turn"])

    def test_ace_spec_rejected(self):
        """{ACE SPEC} 引擎判不出来（无卡牌子类）→ 如实未建模，不静默放行"""
        r = _tags("If this Pokémon has a Pokémon Tool attached, your opponent "
                  "can’t play any {ACE SPEC} cards from their hand.")
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])
