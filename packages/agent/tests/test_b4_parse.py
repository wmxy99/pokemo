# -*- coding: utf-8 -*-
"""B4 · 「任意数量」交互层的**解析**约定（2026-11-08）

守什么
------
1. 五个族各自的标签形状（`pre:b4:*` / `pre:attach_all_basic:*` /
   `choose:alloc:*` / `use_any:first_turn` / `use_not:*`）。
2. **句式差一个词就不认**：「you may」= 可选、「all Energy」= 全量、
   「any amount」= 数量自由、「to **this** Pokémon」= 目标唯一。混用会静默改强度。
3. **`in this way` 只按本次攻击计**（`chosen_this_attack`），而**带子集限定**的
   写法（`Energy card you discarded in this way`）仍如实未建模 —— 那是另一种口径。
4. 认领之后**同一句不得被别的检测器二次认领**（`counters:` / `heal:` 范围 /
   `any number of` 兜底）。

运行：cd packages/agent && python -m unittest tests.test_b4_parse -v
"""
from __future__ import annotations

import unittest

from agent import effects as E


def _tags(text: str, kind: str = "attack", name: str = "X") -> dict:
    spec = E.parse_rule(text, kind=kind, name=name)
    return E.to_engine_tags([spec])


class TestSelfCounters(unittest.TestCase):
    """Walking Wake：最多 9 枚指示物 → 每枚 20 伤害"""

    TXT = ("Heal 20 damage from this Pokémon. Put up to 9 damage counters on this "
           "Pokémon. This attack does 20 damage for each damage counter you "
           "placed in this way.")

    def test_tags(self):
        r = _tags(self.TXT, name="Undulating Slice")
        self.assertIn("pre:b4:self_counters:9", r["tags"])
        self.assertIn("dmg_set:20:per:chosen_this_attack", r["tags"])

    def test_heal_not_pushed_to_unmodeled(self):
        """下一句的「in this way」不得把本句的回复判成「依赖前序动作」"""
        r = _tags(self.TXT, name="Undulating Slice")
        self.assertIn("heal:20", r["tags"])
        self.assertEqual(r["unsupported"], [])

    def test_no_second_claim_by_any_number_fallback(self):
        """认领之后必须把句子**抹掉**：否则「any number of」兜底会把已建模的卡
        重新标成未建模（表现为整卡不进池，而卡面/日志全都正常）

        ⚠ 这条原来是「`counters:` 不得二次认领」，但实测那**不成立** ——
        `counters:` 只认「on your opponent's Pokémon」，Walking Wake 的写法
        本来就不会命中，所以那个断言**两个方向都绿**（假守卫）。
        真正被抹句保护的是「any number of」兜底与「in this way」的 heal 范围。
        """
        r = _tags(self.TXT, name="Undulating Slice")
        self.assertFalse([t for t in r["tags"] if t.startswith("counters")],
                         r["tags"])
        self.assertEqual(r["unsupported"], [], "已建模的句子不得再进未建模")


class TestDiscardEnergy(unittest.TestCase):
    """Mega Charizard X ex：弃任意张 {R} 能量 → 每张 90"""

    def test_tags(self):
        r = _tags("Discard any amount of {R} Energy from among your Pokémon, and "
                  "this attack does 90 damage for each card you discarded in this "
                  "way.")
        self.assertIn("pre:b4:discard_energy:fire:scope:own", r["tags"])
        self.assertIn("dmg_set:90:per:chosen_this_attack", r["tags"])
        self.assertEqual(r["unsupported"], [], "代价已建模 → 不得再报能量代价缺口")

    def test_scope_comes_from_text(self):
        """「from among your Pokémon」= own；写死成 opp 就是退对手的能量"""
        r = _tags("Discard any amount of {W} Energy from among your Pokémon, and "
                  "this attack does 40 damage for each card you discarded in this "
                  "way.")
        self.assertIn("pre:b4:discard_energy:water:scope:own", r["tags"])


class TestDiscardHand(unittest.TestCase):
    """TR Honchkrow：弃任意张「名字带 Team Rocket」的支援者 → 每张 60"""

    def test_tags(self):
        r = _tags("You may discard any number of Supporter cards that have "
                  "\u201cTeam Rocket\u201d in their name from your hand, and this "
                  "attack does 60 damage for each card you discarded in this way.")
        self.assertIn("pre:b4:discard_hand:supporter:teamrocket", r["tags"])
        self.assertIn("dmg_set:60:per:chosen_this_attack", r["tags"])
        self.assertEqual(r["unsupported"], [])


class TestAllEnergy(unittest.TestCase):
    """Zarude（回手）/ Dialga（洗回牌库）：可选代价 + 加伤"""

    def test_to_hand(self):
        r = _tags("Heal 20 damage from this Pokémon. You may put all Energy "
                  "attached to this Pokémon into your hand to have this attack do "
                  "80 more damage.")
        self.assertIn("pre:b4:all_energy:to_hand", r["tags"])
        self.assertIn("dmg_plus:80:per:chosen_this_attack", r["tags"])
        self.assertEqual(r["unsupported"], [])

    def test_to_deck(self):
        r = _tags("You may shuffle all Energy attached to this Pokémon into your "
                  "deck and have this attack do 80 more damage.")
        self.assertIn("pre:b4:all_energy:to_deck", r["tags"])
        self.assertIn("dmg_plus:80:per:chosen_this_attack", r["tags"])

    def test_forced_cost_is_not_optional(self):
        """**强制**洗回（无 you may）走旧 tag，不得被本族抢走"""
        r = _tags("Shuffle all Energy attached to this Pokémon into your deck, and "
                  "this attack does 220 damage to 1 of your opponent's Pokémon.")
        self.assertNotIn("pre:b4:all_energy:to_deck", r["tags"])


class TestAttachAll(unittest.TestCase):
    """Gorebyss：伤害前「任意数量基本 {W}」贴到**这只** → 全贴"""

    def test_tags(self):
        r = _tags("This attack does 30 damage for each {W} Energy attached to this "
                  "Pokémon. Before doing damage, you may attach any number of "
                  "Basic {W} Energy cards from your hand to this Pokémon.")
        self.assertIn("pre:attach_all_basic:self:type:water", r["tags"])
        self.assertIn("dmg_set:30:per:own_active_energy_water", r["tags"])
        self.assertEqual(r["unsupported"], [])
        # 不是「决策」→ 不得挂起选择
        self.assertFalse([t for t in r["tags"] if t.startswith("pre:b4:")])

    def test_any_way_is_not_this_pokemon(self):
        """「to **your** Pokémon in any way you like」是**分布**，不是全贴到这只"""
        r = _tags("You may attach any number of Basic Energy cards from your hand "
                  "to your Pokémon in any way you like.")
        self.assertNotIn("pre:attach_all_basic:self:type:water", r["tags"])
        self.assertIn("choose:alloc:attach_hand:basic", r["tags"])


class TestRevealNamed(unittest.TestCase):
    """Doublade：亮出任意张具名卡 → 每张 60（亮牌无代价 → 取满）"""

    def test_tags(self):
        r = _tags("Reveal any number of Honedge, Doublade, and Aegislash from your "
                  "hand, and this attack does 60 damage for each card you revealed "
                  "in this way.")
        self.assertEqual(
            r["tags"],
            ["dmg_set:60:per:own_hand_named_any:honedge+doublade+aegislash"])
        self.assertEqual(r["unsupported"], [])

    def test_revealed_is_not_chosen_counter(self):
        """「revealed in this way」不得落 `chosen_this_attack`（亮牌不弃牌）"""
        r = _tags("Reveal any number of Honedge, Doublade, and Aegislash from your "
                  "hand, and this attack does 60 damage for each card you revealed "
                  "in this way.")
        self.assertFalse([t for t in r["tags"] if "chosen_this_attack" in t])


class TestAllocFamily(unittest.TestCase):
    """分配 / 多目标：目标不唯一 → 必须由玩家指"""

    def test_move_counters(self):
        r = _tags("Your opponent's Active Pokémon is now Confused. You may move "
                  "any number of damage counters on your opponent's Pokémon to "
                  "their other Pokémon in any way you like.")
        self.assertIn("choose:alloc:move_counters", r["tags"])
        self.assertIn("confuse", r["tags"])

    def test_move_energy(self):
        r = _tags("You may move any amount of {M} Energy from your Pokémon to "
                  "your other Pokémon in any way you like. Flip 4 coins. This "
                  "attack does 80 damage for each heads.")
        self.assertIn("choose:alloc:move_energy:metal", r["tags"])

    def test_shuffle_picked(self):
        r = _tags("Choose 2 of your opponent's Benched Pokémon. Shuffle those "
                  "Pokémon and all attached cards into your opponent's deck.")
        self.assertIn("choose:alloc:shuffle_opp_bench:2", r["tags"])

    def test_shuffle_kept(self):
        r = _tags("Choose 3 of your opponent's Benched Pokémon. If you do, shuffle "
                  "all of your opponent's Benched Pokémon that you didn't choose, "
                  "and all cards attached to those Pokémon, into their deck.")
        self.assertIn("choose:alloc:shuffle_opp_bench:3:keep", r["tags"])

    def test_self_lock_kept(self):
        """「上回合用过就不能再用」丢了 = 每回合连放（静默加强）"""
        r = _tags("Choose 2 of your opponent's Benched Pokémon. Shuffle those "
                  "Pokémon and all attached cards into your opponent's deck. If 1 "
                  "of your Pokémon used Angelite during your last turn, this "
                  "attack can't be used.", name="Angelite")
        self.assertIn("use_not:used_attack_last_turn:angelite", r["tags"])


class TestPokemonWideFirstTurn(unittest.TestCase):
    """Meloetta ex「Debut Performance」：许可在**特性**上"""

    def test_ability_tag(self):
        r = _tags("If you go first, this Pokémon can use attacks during your "
                  "first turn.", kind="ability", name="Debut Performance")
        self.assertEqual(r["tags"], ["use_any:first_turn"])
        self.assertEqual(r["unsupported"], [])

    def test_attack_level_wording_unaffected(self):
        """招式级写法（this **attack**）仍走原来的 `use_any` 路径"""
        r = _tags("If you go first, you can use this attack during your first "
                  "turn. Discard your hand and draw 5 cards.", name="Fast Flight")
        self.assertIn("use_any:first_turn", r["tags"])


class TestCounterScope(unittest.TestCase):
    """`chosen_this_attack` 的口径边界"""

    def test_bare_in_this_way(self):
        r = _tags("Discard any amount of {R} Energy from among your Pokémon, and "
                  "this attack does 90 damage for each card you discarded in this "
                  "way.")
        self.assertIn("dmg_set:90:per:chosen_this_attack", r["tags"])

    def test_subset_still_unmodeled(self):
        """带子集的「X card you discarded in this way」不能映射成总数"""
        r = _tags("Discard the top 3 cards of your deck. This attack does 30 more "
                  "damage for each Pokémon Tool card you discarded in this way.")
        # 磨牌本身照常建模（`mill:3`）；**缩放**必须如实未建模
        self.assertIn("mill:3", r["tags"])
        self.assertFalse([t for t in r["tags"] if "chosen_this_attack" in t])
        self.assertTrue(r["unsupported"], "子集限定的缩放不得被映射成总数")


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
