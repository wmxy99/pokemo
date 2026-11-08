# -*- coding: utf-8 -*-
"""B4 · 「任意数量」交互层（2026-11-08）

守什么
------
1. **伤害前决策**（`pre:b4:`）：数量由玩家定、**定完才**算伤害。挂起期间状态
   冻结，闭合后从**伤害段**续跑（不重付费用、不重复记攻击次数、只发一条
   `attack` 日志）。
2. **`chosen_this_attack` 是「本次」**：不能拿本回合累计（`own_discarded_this_turn`）
   代替 —— 否则本回合早些时候弃的牌会被算进伤害（静默加强，且难以复现）。
3. **分配 / 多目标**（`choose:alloc:`）：目标不唯一时由玩家指，不许引擎代选。
4. **宝可梦级先手授权**（Meloetta ex）：许可在**特性**上，改的是全部招式。

运行：cd packages/engine && python -m unittest tests.test_b4_interaction -v
"""
from __future__ import annotations

import unittest

from engine.battle import Battle
from engine.cards import (AbilityDef, AttackDef, EnergyDef, EnergyRef,
                          PokeRef, PokemonDef, TrainerDef, TrainerRef,
                          make_pokemon)
from engine.state import GameConfig


def _deck(tag: str, n: int = 40) -> list:
    return [PokeRef(PokemonDef(f"Fill{tag}{i}", hp=100, ptype="colorless"))
            for i in range(n)]


def _atk(name="Move", cost=0, damage=0, effects=()):
    return AttackDef(name, cost=cost, damage=damage, effects=tuple(effects))


def _battle(seed=5, cfg=None):
    """真实对局（关掉先手规则 —— 本套件验的是交互层，不是开局规则）"""
    return Battle(_deck("A"), _deck("B"), seed=seed,
                  cfg=cfg or GameConfig(first_turn_no_attack=False,
                                        first_turn_no_supporter=False))


def _prep(b, atk, *, hp_me=300, hp_opp=900, opp_attacks=()):
    me = b.players[b.current]
    opp = b.players[1 - b.current]
    me.active = make_pokemon(PokemonDef(
        "Me", hp=hp_me, ptype="colorless", attacks=(atk,)))
    opp.active = make_pokemon(PokemonDef(
        "Foe", hp=hp_opp, ptype="colorless", attacks=tuple(opp_attacks)))
    me.hand = []
    return me, opp


def _choose_opts(b):
    return [a for a in b.legal_actions() if a.get("verb") == "choose"]


def _logs(b, kind):
    return [e for e in b.events if e.get("type") == kind]


class TestSelfCounters(unittest.TestCase):
    """Walking Wake「Undulating Slice」：最多 9 枚指示物 → 每枚 20 伤害"""

    ATK = _atk("Undulating Slice", damage=0,
               effects=("pre:b4:self_counters:9",
                        "dmg_set:20:per:chosen_this_attack"))

    def test_suspends_before_damage(self):
        b = _battle()
        me, opp = _prep(b, self.ATK)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.phase, "choice", "必须先问数量再算伤害")
        self.assertEqual(opp.active.damage, 0, "挂起期间不得造成伤害")
        self.assertEqual(me.active.damage, 0, "挂起期间不得放指示物")

    def test_choice_count_drives_damage(self):
        b = _battle()
        me, opp = _prep(b, self.ATK)
        b.step({"verb": "attack", "attack": 0})
        opts = [o for o in _choose_opts(b) if o["option"].get("count") == 3]
        self.assertEqual(len(opts), 1, _choose_opts(b))
        r = b.step({"verb": "choose", "pick": opts[0]["pick"]})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(me.active.damage, 30, "3 枚指示物 = 自身 30 伤害")
        self.assertEqual(opp.active.damage, 60, "3 × 20 = 60")
        # 只发一条 attack（重入不能重复记账）
        self.assertEqual(len(_logs(b, "attack")), 1, _logs(b, "attack"))
        self.assertEqual(me.attacks_this_turn, 1)

    def test_zero_is_allowed_and_deals_zero(self):
        b = _battle()
        me, opp = _prep(b, self.ATK)
        b.step({"verb": "attack", "attack": 0})
        opt = next(o for o in _choose_opts(b) if o["option"].get("count") == 0)
        b.step({"verb": "choose", "pick": opt["pick"]})
        self.assertEqual(me.active.damage, 0)
        self.assertEqual(opp.active.damage, 0)


class TestDiscardEnergyCount(unittest.TestCase):
    """Mega Charizard X ex「Inferno X」：弃任意张 {R} 能量 → 每张 90"""

    ATK = _atk("Inferno X", damage=0,
               effects=("pre:b4:discard_energy:fire:scope:own",
                        "dmg_set:90:per:chosen_this_attack"))

    def test_count_is_this_attack_not_turn_total(self):
        """本回合先弃过别的牌 → 伤害仍只按**本次**弃的能量算"""
        b = _battle()
        me, opp = _prep(b, self.ATK)
        me.active.add_energy("fire", 3)
        # 本回合早些时候弃掉 2 张（模拟「先用了别的效果」）
        for _ in range(2):
            b._to_discard(me, EnergyRef(EnergyDef("water")))
        self.assertEqual(me.discarded_this_turn, 2)

        b.step({"verb": "attack", "attack": 0})
        picks = [o for o in _choose_opts(b)
                 if str(o["option"].get("etype")) == "fire"]
        # 引擎里能量**按点数计数**，所以 (位置, 属性) 只有**一个**选项；
        # 「弃 2 点」= 同一项选两次（`allow_repeat`）。
        self.assertEqual(len(picks), 1, picks)
        b.step({"verb": "choose", "pick": picks[0]["pick"]})
        b.step({"verb": "choose", "pick": picks[0]["pick"]})
        b.step({"verb": "stop"})
        self.assertEqual(me.active.energy.get("fire", 0), 1, "真弃掉 2 点")
        self.assertEqual(opp.active.damage, 180, "2 × 90（不含本回合早先弃的 2 张）")

    def test_decline_all_deals_zero(self):
        b = _battle()
        me, opp = _prep(b, self.ATK)
        me.active.add_energy("fire", 2)
        b.step({"verb": "attack", "attack": 0})
        b.step({"verb": "stop"})
        self.assertEqual(opp.active.damage, 0)
        self.assertEqual(me.active.energy.get("fire", 0), 2, "不弃就都不弃")


class TestDiscardHandCount(unittest.TestCase):
    """Team Rocket's Honchkrow「Rocket Feathers」：弃任意张 TR 支援者 → 每张 60"""

    ATK = _atk("Rocket Feathers", damage=0,
               effects=("pre:b4:discard_hand:supporter:teamrocket",
                        "dmg_set:60:per:chosen_this_attack"))

    def test_only_matching_supporters_offered(self):
        b = _battle()
        me, opp = _prep(b, self.ATK)
        me.hand = [
            TrainerRef(TrainerDef("draw:1", kind="supporter",
                                  name="Team Rocket's Ariana")),
            TrainerRef(TrainerDef("draw:1", kind="supporter",
                                  name="Team Rocket's Archer")),
            TrainerRef(TrainerDef("draw:1", kind="supporter", name="Iono")),
        ]
        b.step({"verb": "attack", "attack": 0})
        opts = _choose_opts(b)
        labels = " ".join(str(o["option"].get("display")) for o in opts)
        self.assertIn("Ariana", labels)
        self.assertIn("Archer", labels)
        self.assertNotIn("Iono", labels, "非 Team Rocket 支援者不得出现在选项里")

    def test_discard_two_deals_120(self):
        b = _battle()
        me, opp = _prep(b, self.ATK)
        me.hand = [
            TrainerRef(TrainerDef("draw:1", kind="supporter",
                                  name="Team Rocket's Ariana")),
            TrainerRef(TrainerDef("draw:1", kind="supporter",
                                  name="Team Rocket's Archer")),
        ]
        b.step({"verb": "attack", "attack": 0})
        opts = _choose_opts(b)
        b.step({"verb": "choose", "pick": opts[0]["pick"]})
        b.step({"verb": "choose", "pick": opts[1]["pick"]})
        b.step({"verb": "stop"})
        self.assertEqual(opp.active.damage, 120)
        self.assertEqual(len(me.hand), 0, "两张都进弃牌堆")


class TestAllEnergyCost(unittest.TestCase):
    """Zarude / Dialga：「You may 移开全部能量 → 本招 +80」"""

    def test_pay_adds_80(self):
        b = _battle()
        atk = _atk("Jungle Whip", damage=0,
                   effects=("pre:b4:all_energy:to_hand",
                            "dmg_plus:80:per:chosen_this_attack"))
        me, opp = _prep(b, atk)
        me.active.add_energy("grass", 2)
        b.step({"verb": "attack", "attack": 0})
        opt = next(o for o in _choose_opts(b) if o["option"].get("yes"))
        b.step({"verb": "choose", "pick": opt["pick"]})
        self.assertEqual(opp.active.damage, 80)
        self.assertEqual(me.active.total_energy(), 0, "能量回到手牌")
        self.assertEqual(sum(1 for c in me.hand if isinstance(c, EnergyRef)), 2)

    def test_decline_adds_zero(self):
        b = _battle()
        atk = _atk("Jungle Whip", damage=0,
                   effects=("pre:b4:all_energy:to_hand",
                            "dmg_plus:80:per:chosen_this_attack"))
        me, opp = _prep(b, atk)
        me.active.add_energy("grass", 2)
        b.step({"verb": "attack", "attack": 0})
        opt = next(o for o in _choose_opts(b) if not o["option"].get("yes"))
        b.step({"verb": "choose", "pick": opt["pick"]})
        self.assertEqual(opp.active.damage, 0)
        self.assertEqual(me.active.total_energy(), 2, "不付就都留着")

    def test_to_deck_shuffles(self):
        b = _battle()
        atk = _atk("Chrono Burst", damage=0,
                   effects=("pre:b4:all_energy:to_deck",
                            "dmg_plus:80:per:chosen_this_attack"))
        me, opp = _prep(b, atk)
        me.active.add_energy("metal", 1)
        n_deck = len(me.deck)
        b.step({"verb": "attack", "attack": 0})
        opt = next(o for o in _choose_opts(b) if o["option"].get("yes"))
        b.step({"verb": "choose", "pick": opt["pick"]})
        self.assertEqual(opp.active.damage, 80)
        self.assertEqual(len(me.deck), n_deck + 1, "能量进牌库（不是手牌）")
        self.assertEqual(len([c for c in me.hand if isinstance(c, EnergyRef)]), 0)


class TestPreAttachAll(unittest.TestCase):
    """Gorebyss：伤害前「任意数量基本 {W} 能量贴到**这只**」= 全贴（支配解）"""

    ATK = _atk("Crescendo Wave", damage=0,
               effects=("pre:attach_all_basic:self:type:water",
                        "dmg_set:30:per:own_active_energy_water"))

    def test_attaches_all_matching_before_damage(self):
        b = _battle()
        me, opp = _prep(b, self.ATK)
        me.hand = [EnergyRef(EnergyDef("water")),
                   EnergyRef(EnergyDef("water")),
                   EnergyRef(EnergyDef("fire"))]
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(me.active.energy.get("water", 0), 2, "两张水能量全贴上")
        self.assertEqual(opp.active.damage, 60, "2 × 30")
        self.assertEqual(len(me.hand), 1, "只留下那张火能量")


class TestRevealNamedCount(unittest.TestCase):
    """Doublade：亮出任意张具名卡 → 每张 60（亮牌无代价，取满是支配解）"""

    ATK = _atk("Weaponized Swords", damage=0,
               effects=("dmg_set:60:per:own_hand_named_any:"
                        "honedge+doublade+aegislash",))

    def test_counts_hand_names(self):
        b = _battle()
        me, opp = _prep(b, self.ATK)
        me.hand = [PokeRef(PokemonDef("Honedge", hp=60, ptype="metal")),
                   PokeRef(PokemonDef("Doublade", hp=90, ptype="metal")),
                   PokeRef(PokemonDef("Other", hp=60, ptype="fire"))]
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(opp.active.damage, 120, "2 张具名卡 × 60")


class TestAlloc(unittest.TestCase):
    """分配 / 多目标（Alakazam / Forretress / Sylveon / Shiftry）"""

    def test_move_counters(self):
        b = _battle()
        atk = _atk("Strange Hacking", damage=0,
                   effects=("choose:alloc:move_counters",))
        me, opp = _prep(b, atk)
        opp.bench = [make_pokemon(PokemonDef("Foe2", hp=300,
                                             ptype="colorless"))]
        opp.active.damage = 30
        b.step({"verb": "attack", "attack": 0})
        opts = _choose_opts(b)
        self.assertTrue(opts, "应给「搬指示物」的选项")
        b.step({"verb": "choose", "pick": opts[0]["pick"]})
        b.step({"verb": "stop"})
        self.assertEqual(opp.active.damage + opp.bench[0].damage, 30,
                         "总数守恒（搬动不是增减）")
        self.assertEqual(opp.bench[0].damage, 10)

    def test_move_energy(self):
        b = _battle()
        atk = _atk("Iron Shake-Up", damage=0,
                   effects=("choose:alloc:move_energy:metal",))
        me, opp = _prep(b, atk)
        me.bench = [make_pokemon(PokemonDef("Buddy", hp=100,
                                            ptype="metal"))]
        me.active.add_energy("metal", 1)
        b.step({"verb": "attack", "attack": 0})
        opts = _choose_opts(b)
        self.assertTrue(opts)
        b.step({"verb": "choose", "pick": opts[0]["pick"]})
        b.step({"verb": "stop"})
        self.assertEqual(me.active.total_energy(), 0)
        self.assertEqual(me.bench[0].total_energy(), 1)

    def test_attach_from_hand_any_way(self):
        b = _battle()
        atk = _atk("Tropical Frenzy", damage=0,
                   effects=("choose:alloc:attach_hand:basic",))
        me, opp = _prep(b, atk)
        me.bench = [make_pokemon(PokemonDef("Buddy", hp=100,
                                            ptype="grass"))]
        me.hand = [EnergyRef(EnergyDef("grass"))]
        b.step({"verb": "attack", "attack": 0})
        opts = _choose_opts(b)
        self.assertEqual(len(opts), 2, "两张目标各一个选项（战斗位 / 后备）")
        # 选「贴到后备」的那一项
        pick = next(o for o in opts if "Buddy" in str(o["option"].get("display")))
        b.step({"verb": "choose", "pick": pick["pick"]})
        b.step({"verb": "stop"})
        self.assertEqual(me.bench[0].total_energy(), 1)
        self.assertEqual(me.active.total_energy(), 0)

    def test_shuffle_opp_bench_picked(self):
        """Sylveon：选 2 只 → **被选中的**进对手牌库"""
        b = _battle()
        atk = _atk("Angelite", damage=0,
                   effects=("choose:alloc:shuffle_opp_bench:2",))
        me, opp = _prep(b, atk)
        opp.bench = [make_pokemon(PokemonDef(f"B{i}", hp=100,
                                            ptype="colorless"))
                     for i in range(3)]
        n_deck = len(opp.deck)
        b.step({"verb": "attack", "attack": 0})
        opts = _choose_opts(b)
        self.assertEqual(len(opts), 3, "三只后备各一个选项")
        b.step({"verb": "choose", "pick": opts[0]["pick"]})
        b.step({"verb": "choose", "pick": opts[1]["pick"]})
        # 已选满 2 个 → 不应再能 stop（min=max=2）
        self.assertEqual(len(opp.bench), 1, "2 只被洗回牌库")
        self.assertGreater(len(opp.deck), n_deck)

    def test_shuffle_opp_bench_kept(self):
        """Shiftry：选 3 只 → 洗**没被选中**的"""
        b = _battle()
        atk = _atk("Expelling Tornado", damage=0,
                   effects=("choose:alloc:shuffle_opp_bench:2:keep",))
        me, opp = _prep(b, atk)
        opp.bench = [make_pokemon(PokemonDef(f"K{i}", hp=100,
                                            ptype="colorless"))
                     for i in range(3)]
        b.step({"verb": "attack", "attack": 0})
        opts = _choose_opts(b)
        keep_name = str(opts[0]["option"].get("display"))
        b.step({"verb": "choose", "pick": opts[0]["pick"]})
        b.step({"verb": "choose", "pick": opts[1]["pick"]})
        # 3 只后备、选 2 只「保留」→ 只有**没被选中**的 1 只进牌库
        self.assertEqual(len(opp.bench), 2)
        self.assertIn(keep_name, [fp.name for fp in opp.bench],
                      "留下的是被选中的那些")


class TestAttachDiscard(unittest.TestCase):
    """Smeargle / Dedenne：从**弃牌堆**贴基本能量，上限由抛币或计数源给出"""

    def test_flip_cap_limits_picks(self):
        """`max:flip:N` = 抛 N 枚、上限为正面数（不是 N，也不是不限）"""
        b = _battle()
        atk = _atk("Energizing Sketch", damage=0,
                   effects=("choose:alloc:attach_discard:type:any"
                            ":targets:bench:max:flip:3",))
        me, opp = _prep(b, atk)
        me.bench = [make_pokemon(PokemonDef("Buddy", hp=100,
                                            ptype="grass"))]
        for _ in range(5):
            me.discard_refs.append(EnergyRef(EnergyDef("grass")))
            me.discard.append("grass energy")
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(b.phase, "choice", "上限来自抛币 → 必须问玩家")
        coins = _logs(b, "coin_each")
        self.assertTrue(coins, "抛币事件必须记（上限依据）")
        cap = coins[-1]["heads"]
        # 逐张选（选项是「某一**张**弃牌 + 某一目标」，**不能**重复选同一项 ——
        # 每个选项绑定的是一张具体卡，重复选它等于把同一张贴两次）。
        self.assertTrue([o for o in _choose_opts(b)
                         if "grass" in str(o["option"].get("display"))])
        for _ in range(cap):
            opts = [o for o in _choose_opts(b)
                    if "grass" in str(o["option"].get("display"))]
            self.assertTrue(opts, f"上限 {cap} 张，但第 {_ + 1} 张已无选项")
            b.step({"verb": "choose", "pick": opts[0]["pick"]})
        self.assertEqual(me.bench[0].total_energy(), cap,
                         "贴的张数必须等于抛币正面数")
        self.assertEqual(len([c for c in me.discard_refs
                              if getattr(c, "edef", None) is not None]), 5 - cap)
        # 达上限后必须自动收口（不再提供 choose）
        self.assertEqual([o for o in _choose_opts(b)
                          if "grass" in str(o["option"].get("display"))], [])

    def test_per_cap_from_counter(self):
        """`max:per:opp_all_energy` = 上限为对手全场能量数（Dedenne）"""
        b = _battle()
        atk = _atk("Tail Generator", damage=0,
                   effects=("choose:alloc:attach_discard:type:lightning"
                            ":targets:type:lightning"
                            ":max:per:opp_all_energy",))
        me, opp = _prep(b, atk)
        me.active.pdef = PokemonDef("Me", hp=300, ptype="lightning",
                                    attacks=(atk,))
        opp.active.add_energy("water", 2)
        # 放 3 张（上限 2 < 3）→ 选 1 张后仍应剩下可选项，才能验「还能继续」
        for _ in range(3):
            me.discard_refs.append(EnergyRef(EnergyDef("lightning")))
            me.discard.append("lightning energy")
        b.step({"verb": "attack", "attack": 0})
        picks = [o for o in _choose_opts(b)
                 if str(o["option"].get("etype")) == "lightning"]
        self.assertTrue(picks, "弃牌堆有闪电能量 → 应有选项")
        # 上限 2（对手 2 点能量）：选 1 张后仍可选（还可再选 1 张），
        # 必须仍提供「结束选择」（min=0 → 随时可收工）
        b.step({"verb": "choose", "pick": picks[0]["pick"]})
        verbs = [o.get("verb") for o in b.legal_actions()
                 if o.get("verb") in ("choose", "stop")]
        self.assertIn("stop", verbs, "min=0 → 必须能随时结束")
        self.assertIn("choose", verbs)
        b.step({"verb": "stop"})
        self.assertEqual(me.active.total_energy(), 1)


class TestSubsetDiscardCount(unittest.TestCase):
    """Misty's Gyarados / Mega Abomasnow ex：按**子集**数本次磨掉的牌"""

    ATK = _atk("Splashing Panic", damage=0,
               effects=("pre:mill:7:self",
                        "dmg_set:70:per:discarded_this_attack_named:mistys"))

    def test_counts_only_named_subset(self):
        b = _battle()
        me, opp = _prep(b, self.ATK)
        # ⚠ **列表末尾才是牌库顶**：想磨到 3 张 Misty's，就得把它们放在最后。
        # （第一版按自然顺序写，磨到的全是垫底的水能量 → 断言 0，看起来像计数源坏了。）
        me.deck = ([EnergyRef(EnergyDef("water")) for _ in range(10)]
                   + [PokeRef(PokemonDef("Other", hp=30, ptype="water"))] * 4
                   + [PokeRef(PokemonDef("Misty's Magikarp", hp=30,
                                         ptype="water"))] * 3)
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(opp.active.damage, 210, "3 张 Misty's × 70")

    def test_basic_energy_subset(self):
        b = _battle()
        atk = _atk("Hammer-lanche", damage=0,
                   effects=("pre:mill:6:self",
                            "dmg_set:100:per:"
                            "discarded_this_attack_basic_energy:water"))
        me, opp = _prep(b, atk)
        # 末尾 = 牌库顶：磨 6 张 → 2 水 + 2 火 + 2 非能量
        me.deck = ([EnergyRef(EnergyDef("water")) for _ in range(10)]
                   + [PokeRef(PokemonDef("Other", hp=30, ptype="water"))] * 2
                   + [EnergyRef(EnergyDef("fire"))] * 2
                   + [EnergyRef(EnergyDef("water"))] * 2)
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(opp.active.damage, 200, "只有 2 张**水**基本能量 × 100")

    def test_turn_total_not_used(self):
        """本回合早些时候磨掉的牌不得被算进伤害"""
        b = _battle()
        me, opp = _prep(b, self.ATK)
        me.deck = ([EnergyRef(EnergyDef("water")) for _ in range(10)]
                   + [PokeRef(PokemonDef("Misty's Magikarp", hp=30,
                                         ptype="water"))] * 2)
        # 先弃掉 3 张同名卡（模拟「本回合早先的效果」）
        for _ in range(3):
            b._to_discard(me, PokeRef(PokemonDef("Misty's Magikarp", hp=30,
                                                 ptype="water")))
        b.step({"verb": "attack", "attack": 0})
        self.assertEqual(opp.active.damage, 140, "只算本次磨出的 2 张 × 70")


class TestPokemonWideFirstTurn(unittest.TestCase):
    """Meloetta ex「Debut Performance」：许可在**特性**上 → 全部招式可用"""

    def test_ability_grants_first_turn_attack(self):
        b = Battle(_deck("A"), _deck("B"), seed=5)   # 官方先手规则：T1 禁攻
        me = b.players[b.current]
        me.active = make_pokemon(PokemonDef(
            "Meloetta ex", hp=200, ptype="psychic",
            attacks=(_atk("Echoed Voice", damage=30),),
            abilities=(AbilityDef("Debut Performance",
                                  tag="use_any:first_turn",
                                  location="field"),)))
        acts = [a for a in b.legal_actions() if a.get("verb") == "attack"]
        self.assertTrue(acts, "有宝可梦级许可 → 开局回合应能出招")
        r = b.step(acts[0])
        self.assertTrue(r["ok"], r.get("error"))

    def test_without_ability_still_blocked(self):
        b = Battle(_deck("A"), _deck("B"), seed=5)
        me = b.players[b.current]
        me.active = make_pokemon(PokemonDef(
            "Plain", hp=200, ptype="psychic",
            attacks=(_atk("Echoed Voice", damage=30),)))
        self.assertEqual([a for a in b.legal_actions()
                          if a.get("verb") == "attack"], [])

    def test_ability_not_offered_as_action(self):
        """静态许可不得出现在「发动特性」的合法动作里（否则必然抛错）"""
        b = _battle()
        me = b.players[b.current]
        me.active = make_pokemon(PokemonDef(
            "Meloetta ex", hp=200, ptype="psychic",
            attacks=(_atk("Echoed Voice", damage=30),),
            abilities=(AbilityDef("Debut Performance",
                                  tag="use_any:first_turn",
                                  location="field"),)))
        self.assertEqual([a for a in b.legal_actions()
                          if a.get("verb") == "ability"], [])


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
