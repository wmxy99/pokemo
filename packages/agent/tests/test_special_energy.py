# -*- coding: utf-8 -*-
"""特殊能量 —— 解析侧单测（整段认领 + 未建模拒绝）

每张卡必须**整段**可执行；只写"提供 {C}"的残缺文本不得被认领
（否则会按 kind 执行一整套卡面没有的规则）。
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
from agent.fx import fx_special_energy as D  # noqa: E402

TEXTS = {
    "Neo Upper Energy": (
        "As long as this card is attached to a Pokémon, it provides {C} Energy."
        "\n\nIf this card is attached to a Stage 2 Pokémon, this card provides "
        "every type of Energy but provides only 2 Energy at a time.", "neo_upper"),
    "Mist Energy": (
        "As long as this card is attached to a Pokémon, it provides {C} Energy."
        "\n\nPrevent all effects of attacks used by your opponent’s Pokémon done "
        "to the Pokémon this card is attached to. (Existing effects are not "
        "removed. Damage is not an effect.)", "mist"),
    "Legacy Energy": (
        "As long as this card is attached to a Pokémon, it provides every type "
        "of Energy but provides only 1 Energy at a time.\n\nIf the Pokémon this "
        "card is attached to is Knocked Out by damage from an attack from your "
        "opponent’s Pokémon, that player takes 1 fewer Prize card. This effect "
        "of your Legacy Energy only works once per game.", "legacy"),
    "Enriching Energy": (
        "As long as this card is attached to a Pokémon, it provides {C} Energy."
        "\n\nWhen you attach this card from your hand to a Pokémon, draw 4 "
        "cards.", "enriching"),
    "Spiky Energy": (
        "As long as this card is attached to a Pokémon, it provides {C} Energy."
        "\n\nIf the Pokémon this card is attached to is in the Active Spot and "
        "is damaged by an attack from your opponent’s Pokémon (even if this "
        "Pokémon is Knocked Out), put 2 damage counters on the Attacking "
        "Pokémon.", "spiky"),
    "Team Rocket's Energy": (
        "This card can only be attached to a Team Rocket’s Pokémon. If this card "
        "is attached to anything other than a Team Rocket’s Pokémon, discard "
        "this card.\n\nAs long as this card is attached to a Pokémon, it "
        "provides 2 in any combination of {P} Energy and {D} Energy.",
        "team_rocket"),
    "Prism Energy": (
        "As long as this card is attached to a Pokémon, it provides {C} Energy."
        "\n\nIf this card is attached to a Basic Pokémon, this card provides "
        "every type of Energy but provides only 1 Energy at a time.", "prism"),
    "Ignition Energy": (
        "If this card is attached to 1 of your Pokémon, discard it at the end of "
        "your turn.\n\nAs long as this card is attached to a Pokémon, it "
        "provides {C} Energy.\n\nIf this card is attached to an Evolution "
        "Pokémon, it provides {C}{C}{C} Energy instead.", "ignition"),
    "Grow Grass Energy": (
        "As long as this card is attached to a Pokémon, it provides {G} Energy. "
        "The {G} Pokémon this card is attached to gets +20 HP.", "grow_grass"),
    "Telepath Psychic Energy": (
        "As long as this card is attached to a Pokémon, it provides {P} Energy. "
        "When you attach this card from your hand to a {P} Pokémon, search your "
        "deck for up to 2 Basic {P} Pokémon and put them onto your Bench. Then, "
        "shuffle your deck.", "telepath"),
    "Rock Fighting Energy": (
        "As long as this card is attached to a Pokémon, it provides {F} Energy. "
        "Prevent all effects of attacks used by your opponent’s Pokémon done to "
        "the {F} Pokémon this card is attached to. (Existing effects are not "
        "removed. Damage is not an effect.)", "rock_fighting"),
}

BOOMERANG = (
    "As long as this card is attached to a Pokémon, it provides {C} Energy."
    "\n\nIf this card is discarded by an effect of an attack used by the "
    "Pokémon this card is attached to, attach this card from your discard pile "
    "to that Pokémon after attacking.")


class TestSpecialEnergyDetect(unittest.TestCase):
    def test_all_modeled_kinds(self):
        for name, (text, kind) in TEXTS.items():
            with self.subTest(card=name):
                r = E.to_engine_tags([E.parse_rule(text, kind="energy",
                                                   name=name)])
                self.assertEqual(r["tags"], [f"senergy:{kind}"])
                self.assertEqual(r["unsupported"], [])

    def test_boomerang_now_modeled(self):
        """Boomerang Energy —— **改判**（S2d，2026-11-08）

        原判据：引擎的能量是计数、弃掉不回弃牌堆 → 「被弃置后回附」无法精确
        表达，整卡拒收。S2d 补上两件东西后可以认领：
          · `_discard_energy_from`：弃能量时**特殊能量卡**真的进 `discard_refs`；
          · `attack_done` 的差分快照：知道「这张卡是不是被**本招式**弃掉的」。
        仍然钉死「不得只认第一段（提供 {C}）」—— 那是把整条规则静默丢掉。
        """
        r = E.to_engine_tags([E.parse_rule(BOOMERANG, kind="energy",
                                           name="Boomerang Energy")])
        self.assertEqual(r["tags"], ["senergy:boomerang"])
        self.assertEqual(r["unsupported"], [])
        # 已知但未建模表里不该再有它
        self.assertEqual(D.unmodeled_kind(BOOMERANG), "")

    # ---- 负向守卫 ----
    def test_truncated_text_rejected(self):
        """只写「提供 {C}」→ 不得认领（后面的规则会静默丢失）"""
        text = "As long as this card is attached to a Pokémon, it provides {C} Energy."
        self.assertIsNone(D.detect(text, "energy", "Mist Energy"))
        r = E.to_engine_tags([E.parse_rule(text, kind="energy",
                                           name="Mist Energy")])
        self.assertEqual(r["tags"], [])

    def test_extra_sentence_rejected(self):
        text = TEXTS["Mist Energy"][0] + "\n\nDraw 2 cards."
        self.assertIsNone(D.detect(text, "energy", "Mist Energy"))

    def test_not_energy_kind_ignored(self):
        self.assertIsNone(D.detect(TEXTS["Mist Energy"][0], "item",
                                   "Mist Energy"))


if __name__ == "__main__":
    unittest.main()
