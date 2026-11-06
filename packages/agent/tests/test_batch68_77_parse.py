# -*- coding: utf-8 -*-
"""第六十八~七十七批（2026-11-04）· 解析用例

每族都覆盖「正确形态 → 精确 tag」与「近似/越界写法 → 必须不认领」两侧。
只测正向等于橡皮图章：本轮最危险的失败方向都是**静默改强度/改对象** ——
  ① 双方洗抽落成单方洗抽（对手那半静默消失）；
  ② 道具 +HP 被「卡名/阵营限定」守卫误杀（整卡不进池）；
  ③ 弃牌堆**能量**洗回误套到**宝可梦**洗回（类别错）；
  ④ 指示物直放误走 damage/snipe（指示物不吃弱点/抗性，走伤害字段会静默多算）；
  ⑤ 「on this Pokémon」落成对手口径（阵营反转）。
"""
from __future__ import annotations
import unittest

from agent import effects as E


def _tags(text: str, kind: str = "attack", name: str = "X") -> dict:
    out = E.to_engine_tags([E.parse_rule(text, kind=kind, name=name)])
    return {"tags": out["tags"],
            "unsupported": [u["reason"] for u in out["unsupported"]]}


class TestEachPlayerShuffleDraw(unittest.TestCase):
    """第六十八批：双方洗回手牌各抽 N"""

    def test_judge(self):
        r = _tags("Each player shuffles their hand into their deck and "
                  "draws 4 cards.", kind="supporter", name="Judge")
        self.assertEqual(r["tags"], ["shuffle_draw_split:4:4"])
        self.assertEqual(r["unsupported"], [])

    def test_other_count(self):
        r = _tags("Each player shuffles their hand into their deck and "
                  "draws 6 cards.", kind="supporter")
        self.assertEqual(r["tags"], ["shuffle_draw_split:6:6"])

    def test_single_player_not_claimed(self):
        """「Shuffle your hand … draw N」是单方洗抽，不能落成双方分抽"""
        r = _tags("Shuffle your hand into your deck. Then, draw 4 cards.",
                  kind="supporter")
        self.assertNotIn("shuffle_draw_split:4:4", r["tags"])


class TestToolHpPlus(unittest.TestCase):
    """第六十九批：道具 +HP（**阵营限定必须拒绝**）

    ⚠ 核实时发现的关键纪律：`tool:hp_plus:N` 在引擎里由 `_tool_value()` 无条件
    取值，**没有持有者归属元数据**。因此「The Cynthia's / Team Rocket's Pokémon …
    gets +N HP」只能给**该阵营**持有者加血，落成 `tool:hp_plus:N` 会让**任意**
    持有者都加血（静默加强）→ 必须如实拒收（原 `out.append` + 落守卫的行为是对的）。
    """

    def test_plain_holder_modeled(self):
        """无阵营限定的旧句式照常建模（不能因本批被误拒）"""
        r = _tags("The Pokémon this card is attached to gets +30 HP.",
                  kind="item")
        self.assertEqual(r["tags"], ["tool:hp_plus:30"])
        self.assertEqual(r["unsupported"], [])

    def test_faction_qualifier_refused(self):
        """「Cynthia's / Team Rocket's」限定 → 引擎判不出持有者阵营 → 必须拒收"""
        for holder in ("The Cynthia’s", "The Team Rocket’s"):
            with self.subTest(holder=holder):
                r = _tags(f"{holder} Pokémon this card is attached to "
                          "gets +70 HP.", kind="item", name="Tool")
                self.assertNotIn("tool:hp_plus:70", r["tags"])
                self.assertTrue(r["unsupported"])

    def test_extra_clause_refused(self):
        """+HP 之后还有未建模从句 → 整张放弃（不能部分实现冒充已实现）"""
        r = _tags("The Pokémon this card is attached to gets +30 HP. "
                  "When you play this card, your opponent reveals their "
                  "hand.", kind="item", name="X")
        self.assertEqual(r["tags"], [])
        self.assertTrue(r["unsupported"])

    def test_damage_not_hp_not_claimed(self):
        """「gets +30 damage」不是 HP，绝不能落 hp_plus"""
        r = _tags("The Pokémon this card is attached to gets +30 damage.",
                  kind="item")
        self.assertNotIn("tool:hp_plus:30", r["tags"])


class TestEnergyRecycle(unittest.TestCase):
    """第七十批：弃牌堆基本能量洗回牌库（训练家侧）"""

    def test_energy_recycler(self):
        r = _tags("Shuffle up to 5 Basic Energy cards from your discard pile "
                  "into your deck.", kind="item", name="Energy Recycler")
        self.assertEqual(r["tags"], ["energy_to_deck:any:5"])
        self.assertEqual(r["unsupported"], [])

    def test_pokemon_recycle_not_claimed(self):
        """「Pokémon from your discard pile」不是能量，不能落 energy_to_deck"""
        r = _tags("Shuffle up to 5 Pokémon from your discard pile into your "
                  "deck.", kind="item")
        self.assertNotIn("energy_to_deck:any:5", r["tags"])


class TestCountersOnDefending(unittest.TestCase):
    """第七十一批：指示物直放 the Defending Pokémon（对手战斗位）"""

    def test_glaceon(self):
        r = _tags("At the end of your opponent’s next turn, put 9 damage "
                  "counters on the Defending Pokémon.", kind="attack",
                  name="Glaceon")
        self.assertEqual(r["tags"], ["counters:9"])
        self.assertEqual(r["unsupported"], [])

    def test_not_damage_not_snipe(self):
        """指示物不吃弱抗 → 绝不能落 self/snipe/damage"""
        r = _tags("Put 9 damage counters on the Defending Pokémon.",
                  kind="attack")
        self.assertNotIn("self:90", r["tags"])
        self.assertNotIn("snipe:opp_active:90", r["tags"])
        self.assertIn("counters:9", r["tags"])

    def test_opp_active_unchanged(self):
        """「on your opponent's Active Pokémon」旧口径不能改坏"""
        r = _tags("Put 3 damage counters on your opponent’s Active Pokémon.",
                  kind="attack")
        self.assertEqual(r["tags"], ["counters:3"])


class TestCountersOnThis(unittest.TestCase):
    """第七十二批：指示物直放 this Pokémon（己方战斗位）

    机制就位（引擎 `:scope:self` 早已支持，本批补解析侧 + `to_engine_tags` 口径），
    但**只有「干净的单句」才认领**：后续带未建模从句（「If you do, during this
    turn … +120」/「This attack does … for each counter placed」）一律整条放弃 ——
    Feraligatr / Walking Wake 的另一半都是 B4，部分映射即「冒充已实现」。
    """

    def test_clean_single_clause_modeled(self):
        """干净单句 → `:scope:self`（放到自己战斗位，不是对手口径）"""
        r = _tags("Put 5 damage counters on this Pokémon.", kind="attack")
        self.assertIn("counters:5:scope:self", r["tags"])
        self.assertNotIn("counters:5:scope:opp", r["tags"])
        self.assertEqual(r["unsupported"], [])

    def test_feraligatr_compound_now_modeled(self):
        """Feraligatr：指示物 + 「本回合 +120」→ 第九十五批起**单条复合 tag**

        旧口径（第六十八~七十七批）把「本回合 +N 伤害」判为 B4 而整卡拒收；
        第九十五批新增回合级加伤旗标 `self_dmg_bonus:N`（`dmg_bonus_until`
        随 turn_number 过期），两半可同时精确表达 → 改为正向断言。
        """
        r = _tags("Once during your turn, you may put 5 damage counters on "
                  "this Pokémon. If you do, during this turn, attacks used by "
                  "this Pokémon do 120 more damage to your opponent’s Active "
                  "Pokémon (before applying Weakness and Resistance).",
                  kind="ability", name="Feraligatr")
        self.assertEqual(
            r["tags"], ["counters:5:scope:self|self_dmg_bonus:120:self"])
        self.assertEqual(r["unsupported"], [])

    def test_walking_wake_scaling_refused(self):
        """Walking Wake：指示物 + 「按放置数重算伤害」→ 后半段 B4 → 如实拒收"""
        r = _tags("Put up to 9 damage counters on this Pokémon. This attack "
                  "does 20 damage for each damage counter you placed in this "
                  "way.", kind="attack", name="Walking Wake")
        self.assertNotIn("counters:9:scope:self", r["tags"])

    def test_conditional_not_claimed(self):
        """带 if 条件的「on this Pokémon」→ 条件门未建模，不能无条件认领"""
        r = _tags("If this Pokémon has 3 or more Energy attached, put 5 "
                  "damage counters on this Pokémon.", kind="ability")
        self.assertNotIn("counters:5:scope:self", r["tags"])

    def test_coin_branch_not_double_mapped(self):
        """Venture Bomb 的抛币分支不能被本支再叠一条独立 scope:self"""
        r = _tags("Flip a coin. If heads, put 2 damage counters on 1 of your "
                  "opponent’s Pokémon. If tails, put 2 damage counters on "
                  "this Pokémon.", kind="attack")
        self.assertNotIn("counters:2:scope:self", r["tags"])


if __name__ == "__main__":
    unittest.main()
