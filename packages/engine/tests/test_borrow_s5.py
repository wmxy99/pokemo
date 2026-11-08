# -*- coding: utf-8 -*-
"""S5 · 递归效果 + 复制招式（2026-11-08）

守的四件事
----------
1. **抛币门的借用**（Ethan's Sudowoodo「Try to Imitate」）：币必须在**解析借用
   来源之前**抛；反面时 action 里带的 `borrow` 整条作废；正面必须真的借（引擎
   不得替玩家挑一个招式）。同一次攻击里**只能有一枚币**（`_attack_mods` 不得
   再为同一条 tag 抛第二次）。
2. **牌库顶借用**（TR Persian ex「Haughty Order」）：候选只来自对手牌库顶 N 张，
   结算时先记 reveal、招式跑完再洗回；卡面写「You **may**」→ 合法动作表必须
   同时列裸动作（放弃）。
3. **复制来的招式不得再复制**：被复制的招式自身带 `borrow_attack:` 时也只结算
   它自己的效果（借用解析只对**本次出招的招式**做一次）。
4. **顺序**：reveal → 用招式 → 洗回。洗牌放到招式之前会让复制的招式看到另一副牌。

运行：cd packages/engine && python -m unittest tests.test_borrow_s5 -v
"""
from __future__ import annotations

import random
import unittest

from engine.battle import Battle
from engine.cards import (AttackDef, EnergyDef, EnergyRef, PokeRef, PokemonDef,
                          TrainerDef, TrainerRef, make_pokemon)
from engine.state import GameConfig, PlayerState


class _Fixed(random.Random):
    """固定抛币结果（0.1 → 正面；0.9 → 反面）

    `shuffle` 只**计数**、不改顺序（顺序对断言无意义，但「有没有洗」必须可观测 ——
    否则「结算后忘了洗回」这条错法完全看不出来）。
    """

    def __init__(self, value: float):
        super().__init__(0)
        self.value = value
        self.shuffled = 0

    def random(self) -> float:
        return self.value

    def shuffle(self, seq) -> None:
        self.shuffled += 1


class _Seq(random.Random):
    """按序返回固定「随机数」（供「每人各抛一枚」这类多币场景）"""

    def __init__(self, values):
        super().__init__(0)
        self.values = list(values)
        self.i = 0
        self.shuffled = 0

    def random(self) -> float:
        v = self.values[min(self.i, len(self.values) - 1)]
        self.i += 1
        return v

    def shuffle(self, seq) -> None:
        self.shuffled += 1


def arena(seed: int = 7, rng=None):
    cfg = GameConfig()
    b = object.__new__(Battle)
    b.cfg = cfg
    b.seed = seed
    b.rng = rng or random.Random(seed)
    b.players = [PlayerState(name="A", prizes_left=cfg.prizes),
                 PlayerState(name="B", prizes_left=cfg.prizes)]
    b.current = 0
    b.turn_number = 1
    b.turn_counts = [1, 1]
    b.phase = "main"
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
    b.players[0].active = make_pokemon(
        PokemonDef("Attacker", hp=500, ptype="colorless", attacks=()))
    b.players[1].active = make_pokemon(
        PokemonDef("Defender", hp=900, ptype="colorless", attacks=()))
    for p in b.players:
        p.deck = [EnergyRef() for _ in range(20)]
    return b


def _atk(name="Copycat", cost=0, damage=0, effects=()):
    return AttackDef(name, cost=cost, damage=damage, effects=tuple(effects))


def _set_attacker(b, *attacks):
    b.players[0].active = make_pokemon(PokemonDef(
        "Attacker", hp=500, ptype="colorless", attacks=tuple(attacks)))


def _set_defender(b, *attacks):
    b.players[1].active = make_pokemon(PokemonDef(
        "Defender", hp=900, ptype="colorless", attacks=tuple(attacks)))


def _logs(b, kind):
    return [e for e in b.events if e.get("type") == kind]


class TestBorrowCoinGate(unittest.TestCase):
    """Ethan's Sudowoodo「Try to Imitate」—— 抛币门借用"""

    def _prep(self, rng):
        b = arena(rng=rng)
        _set_attacker(b, _atk("Try to Imitate", damage=0,
                              effects=("coin:heads:borrow_attack:opp_active",)))
        _set_defender(b, _atk("Big Punch", cost=0, damage=80))
        return b

    def test_heads_uses_copied_attack(self):
        b = self._prep(_Fixed(0.1))
        r = b.step({"verb": "attack", "attack": 0,
                    "borrow": {"src": "opp_active", "attack": 0}})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.damage, 80)
        atk_log = _logs(b, "attack")[-1]
        self.assertEqual(atk_log["attack"], "Big Punch")
        self.assertEqual(atk_log["via"], "Try to Imitate")

    def test_tails_ignores_supplied_borrow(self):
        b = self._prep(_Fixed(0.9))
        r = b.step({"verb": "attack", "attack": 0,
                    "borrow": {"src": "opp_active", "attack": 0}})
        self.assertTrue(r["ok"], r.get("error"))
        # 反面：整招什么都不做（0 伤害、也没有「打到对手」的 damage 事件）
        self.assertEqual(b.players[1].active.damage, 0)
        self.assertTrue(_logs(b, "borrow_skipped"))
        self.assertEqual(_logs(b, "attack")[-1]["attack"], "Try to Imitate")
        # 反面路径上 `atk` 仍是本招式（带 coin tag）→ `_attack_mods` 若不为它
        # 短路，就会**再抛一枚**（两枚互不相干、日志矛盾且不可复现）。
        coins = _logs(b, "coin")
        self.assertEqual(len(coins), 1, coins)
        self.assertEqual(coins[0]["tag"], "coin:heads:borrow_attack:opp_active")

    def test_one_coin_only(self):
        """同一次攻击里只能有一枚币（`_attack_mods` 不得再抛）"""
        b = self._prep(_Fixed(0.1))
        b.step({"verb": "attack", "attack": 0,
                "borrow": {"src": "opp_active", "attack": 0}})
        coins = _logs(b, "coin")
        self.assertEqual(len(coins), 1, coins)
        self.assertEqual(coins[0]["tag"], "coin:heads:borrow_attack:opp_active")

    def test_heads_without_borrow_raises(self):
        """正面却没给借用来源 → 拒绝（引擎不得替玩家挑一个招式）"""
        b = self._prep(_Fixed(0.1))
        r = b.step({"verb": "attack", "attack": 0})
        self.assertFalse(r["ok"])
        self.assertIn("必须指定借用的招式", r["error"])

    def test_legals_list_each_borrow(self):
        b = self._prep(_Fixed(0.1))
        _set_defender(b, _atk("Big Punch", damage=80), _atk("Small Kick",
                                                            damage=10))
        acts = [a for a in b.legal_actions()
                if a.get("verb") == "attack"]
        # 抛币门也是「必须借」语义 → 不列裸动作，只列逐个可借招式
        self.assertEqual(len(acts), 2, acts)
        self.assertTrue(all(a.get("borrow") for a in acts))
        self.assertEqual([a["borrow"]["attack"] for a in acts], [0, 1])

    def test_copied_attack_does_not_borrow_again(self):
        """复制来的招式自身带借用 tag → 只结算它自己的效果（不递归）"""
        b = arena(rng=_Fixed(0.1))
        _set_attacker(b, _atk("Try to Imitate",
                              effects=("coin:heads:borrow_attack:opp_active",)))
        # 对手这张招式的效果里也有「借用」——复制它不得再触发一次借用解析
        _set_defender(b, _atk("Echo Blade", damage=30,
                              effects=("borrow_attack:opp_active",)))
        r = b.step({"verb": "attack", "attack": 0,
                    "borrow": {"src": "opp_active", "attack": 0}})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.damage, 30)
        atks = _logs(b, "attack")
        self.assertEqual(len(atks), 1, atks)      # 只有一次出招记录
        self.assertEqual(atks[0]["attack"], "Echo Blade")
        # 日志里的 `borrow_src` 记的是**本次出招**（Try to Imitate）借了哪只 ——
        # 复制来的 Echo Blade 自己那条 `borrow_attack:` 不再解析（深度恒 1）。
        self.assertEqual(atks[0]["borrow_src"], "opp_active")
        coins = _logs(b, "coin")
        self.assertEqual(len(coins), 1, coins)


class TestBorrowDeckTop(unittest.TestCase):
    """TR Persian ex「Haughty Order」—— 对手牌库顶 N 张里借招式"""

    def _prep(self, rng, top_n=3):
        b = arena(rng=rng)
        _set_attacker(b, _atk("Haughty Order",
                              effects=(f"borrow_attack:opp_deck_top:{top_n}",)))
        _set_defender(b, _atk("Cruel Slash", damage=140))
        opp = b.players[1]
        # 牌库顶（列表末尾）= 3 张：Top One 在顶，中间两张能量；Deep One 在顶 3 张
        # 之外（用来验证「只看顶 N 张」——只凭「找到了一张宝可梦」看不出越界）。
        opp.deck = [EnergyRef() for _ in range(6)]
        opp.deck.append(make_ref(PokemonDef(
            "Deep One", hp=100, ptype="colorless",
            attacks=(_atk("Deep Jab", damage=999),))))
        opp.deck.extend(EnergyRef() for _ in range(2))
        opp.deck.append(make_ref(PokemonDef(
            "Top One", hp=100, ptype="colorless",
            attacks=(_atk("Top Jab", damage=50),))))
        return b

    def test_borrow_from_top(self):
        b = self._prep(_Fixed(0.5))
        r = b.step({"verb": "attack", "attack": 0,
                    "borrow": {"src": "opp_deck_top:3", "pos": 0,
                               "attack": 0}})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.damage, 50)
        rev = _logs(b, "reveal")[-1]
        self.assertEqual(rev["from"], "deck_top")
        self.assertEqual(rev["n"], 3)
        self.assertEqual(rev["cards"][0], "Top One")   # 列表首 = 牌库顶
        self.assertNotIn("Deep One", rev["cards"])     # 顶 3 张之外不看
        self.assertTrue(_logs(b, "deck_shuffle"))      # 结算完洗回
        self.assertGreaterEqual(b.rng.shuffled, 1)     # 真的调用了洗牌
        # 顺序：reveal → 出招 → 洗回（洗牌提到出招之前会让复制的招式看到别的牌）
        kinds = [e["type"] for e in b.events]
        self.assertLess(kinds.index("reveal"), kinds.index("attack"))
        self.assertLess(kinds.index("attack"), kinds.index("deck_shuffle"))

    def test_optional_decline(self):
        """卡面「You may」→ 必须能放弃（裸动作），且放弃也照常 reveal + 洗回"""
        b = self._prep(_Fixed(0.5))
        acts = [a for a in b.legal_actions() if a.get("verb") == "attack"]
        self.assertTrue(any(a.get("borrow") for a in acts))
        self.assertTrue(any(not a.get("borrow") for a in acts), acts)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.players[1].active.damage, 0)
        self.assertTrue(_logs(b, "reveal"))
        self.assertTrue(_logs(b, "deck_shuffle"))

    def test_borrow_outside_top_rejected(self):
        """候选只来自**牌库顶 N 张**：位置越界必须被拒（不能悄悄换成别的卡）"""
        b = self._prep(_Fixed(0.5))
        r = b.step({"verb": "attack", "attack": 0,
                    "borrow": {"src": "opp_deck_top:3", "pos": 5,
                               "attack": 0}})
        self.assertFalse(r["ok"])
        self.assertIn("不在可选范围内", r["error"])


def make_ref(pdef) -> PokeRef:
    return PokeRef(pdef)


class TestCoinSearch(unittest.TestCase):
    """TR Great Ball —— 抛币分岔检索（正面进化 / 反面基础）"""

    def _prep(self, rng):
        b = arena(rng=rng)
        p = b.players[0]
        p.hand = [TrainerRef(TrainerDef(
            "coin_search:evolution:basic:category:teamrocket", kind="item",
            name="Team Rocket's Great Ball"))]
        p.deck = []
        # 牌库顶（列表末尾）先放一张进化形态，深处放一张基础形态
        p.deck.append(PokeRef(PokemonDef(
            "TR Pupitar", hp=100, ptype="darkness", stage=1,
            category="Trainer's Pokémon(Team Rocket)")))
        p.deck.append(PokeRef(PokemonDef(
            "Other Pupitar", hp=100, ptype="darkness", stage=1,
            category="Trainer's Pokémon(N)")))
        p.deck.append(PokeRef(PokemonDef(
            "TR Larvitar", hp=60, ptype="darkness", stage=0,
            category="Trainer's Pokémon(Team Rocket)")))
        return b

    def _play(self, b):
        acts = [a for a in b.legal_actions() if a.get("verb") == "trainer"]
        self.assertTrue(acts, b.legal_actions())
        return b.step(acts[0])

    def test_heads_finds_evolution(self):
        b = self._prep(_Fixed(0.1))
        r = self._play(b)
        self.assertTrue(r["ok"], r.get("error"))
        names = [c.pdef.name for c in b.players[0].hand
                 if isinstance(c, PokeRef)]
        self.assertIn("TR Pupitar", names)
        self.assertNotIn("TR Larvitar", names)
        self.assertNotIn("Other Pupitar", names)   # 阵营不同不得命中

    def test_tails_finds_basic(self):
        b = self._prep(_Fixed(0.9))
        r = self._play(b)
        self.assertTrue(r["ok"], r.get("error"))
        names = [c.pdef.name for c in b.players[0].hand
                 if isinstance(c, PokeRef)]
        self.assertIn("TR Larvitar", names)
        self.assertNotIn("TR Pupitar", names)

    def test_no_target_not_legal(self):
        """牌库没有该阵营宝可梦 → 这张卡**不进**合法动作表

        与 `search_pokemon:` 同一口径（牌库无检索对象时不列动作）：否则 AI 会
        反复尝试一张必然空过的卡，训练信号被污染。
        """
        b = self._prep(_Fixed(0.1))
        b.players[0].deck = [EnergyRef()]
        acts = [a for a in b.legal_actions() if a.get("verb") == "trainer"]
        self.assertEqual(acts, [])


class TestShuffleBothBottomCoinDraw(unittest.TestCase):
    """Lucian —— 手牌放牌库底（不洗）+ 每人各抛一枚币分抽"""

    def _prep(self, rng):
        b = arena(rng=rng)
        p = b.players[0]
        p.hand = [TrainerRef(TrainerDef(
            "shuffle_both_bottom_coin_draw:6:3", kind="supporter",
            name="Lucian"))]
        p.deck = [EnergyRef(EnergyDef("grass")) for _ in range(10)]
        b.players[1].hand = [EnergyRef(EnergyDef("fire"))
                             for _ in range(2)]
        b.players[1].deck = [EnergyRef(EnergyDef("water"))
                             for _ in range(10)]
        return b

    def test_each_player_flips_own_coin(self):
        # 己方 0.1（正面 → 6 张）；对手 0.9（反面 → 3 张）
        b = self._prep(_Seq([0.1, 0.9]))
        r = b.step({"verb": "trainer", "hand": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(b.players[0].hand), 6)
        self.assertEqual(len(b.players[1].hand), 3)
        self.assertEqual(len(_logs(b, "coin")), 2)   # 每人各一枚
        self.assertEqual(len(_logs(b, "hand_bottom")), 2)

    def test_hand_goes_to_bottom_not_shuffled(self):
        b = self._prep(_Fixed(0.9))
        extra = EnergyRef(EnergyDef("psychic"))
        b.players[0].hand.append(extra)
        b.step({"verb": "trainer", "hand": 0})
        # 放底 = `deck[0]`（列表首），且**不洗牌** → 那张卡必然在最底
        self.assertIs(b.players[0].deck[0], extra)

    def test_no_cards_means_no_flip(self):
        """双方都没有可放底的牌 → 不抛币、不抽牌"""
        b = arena(rng=_Fixed(0.1))
        p = b.players[0]
        p.hand = [TrainerRef(TrainerDef(
            "shuffle_both_bottom_coin_draw:6:3", kind="supporter",
            name="Lucian"))]
        p.deck = [EnergyRef() for _ in range(3)]
        b.players[1].hand = []
        b.players[1].deck = [EnergyRef() for _ in range(3)]
        r = b.step({"verb": "trainer", "hand": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(_logs(b, "coin"), [])
        self.assertEqual(len(b.players[0].hand), 0)


class TestCategoryFilter(unittest.TestCase):
    """`category:` 筛选只比括号里的阵营名（不靠卡名前缀、不做子串）"""

    def test_exact_faction_only(self):
        from engine.battle import _poke_matches_filter
        tr = PokeRef(PokemonDef("X", hp=10, ptype="darkness", stage=0,
                                category="Trainer's Pokémon(Team Rocket)"))
        nn = PokeRef(PokemonDef("Y", hp=10, ptype="darkness", stage=0,
                                category="Trainer's Pokémon(N)"))
        plain = PokeRef(PokemonDef("Z", hp=10, ptype="darkness", stage=0))
        self.assertTrue(_poke_matches_filter(tr, {"category": "teamrocket"}))
        self.assertFalse(_poke_matches_filter(nn, {"category": "teamrocket"}))
        self.assertFalse(_poke_matches_filter(plain, {"category": "teamrocket"}))
        # ⚠ 子串口径会把每个阵营都判成 `n`（`trainerspokemon…` 里全是 n）
        self.assertFalse(_poke_matches_filter(tr, {"category": "n"}))
        self.assertTrue(_poke_matches_filter(nn, {"category": "n"}))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
