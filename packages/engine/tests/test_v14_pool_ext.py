# -*- coding: utf-8 -*-
"""PokéLab Engine v1.4 · 扩展卡池（卡池扩容）可用性验证

卡池扩容把引擎原生卡池从人工 40 张扩到 +328 张自动生成卡。自动生成的东西
**必须逐卡实证**，否则就是把「未建模」换成「会崩」——比原来更糟。

本套件对 `catalog()` 里每一张卡做**穷举执行**：
  · 宝可梦：逐个招式 step 一次（付得起全部费用）→ 必须 ok；
            逐个 field 特性走 legal_actions → step → 必须 ok
  · 训练家：走 legal_actions → step → 必须 ok（不支持的 tag 应不可发动）
  · 能量：构造 ref 不报错

外加：全卡池随机对局冒烟（RandomAgent）→ 零 step 错误。

运行：python -m unittest tests.test_v14_pool_ext -v
"""
from __future__ import annotations
import random
import unittest

from engine import decklib
from engine import reallib
from engine.agents import RandomAgent
from engine.battle import Battle
from engine.battle import _borrow_optional, _borrow_scope
from engine.cards import (AttackDef, EnergyRef, PokeRef, PokemonDef,
                          TrainerRef, make_pokemon)
from engine.state import GameConfig, PlayerState


def _arena(seed: int = 1):
    cfg = GameConfig()
    b = object.__new__(Battle)
    b.cfg = cfg
    b.seed = seed
    b.rng = random.Random(seed)
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
    b.players[0].active = make_pokemon(
        PokemonDef("Probe", hp=400, ptype="colorless", attacks=()))
    # S5：对手战斗位必须**有招式** —— `borrow_attack:` 家族的候选全来自它，
    # 空招式会让「借用招式」类卡在穷举里必然执行不了（假失败）。
    b.players[1].active = make_pokemon(
        PokemonDef("Dummy", hp=400, ptype="colorless",
                   attacks=(AttackDef("Probe Jab", cost=0, damage=10),)))
    for p in b.players:
        p.deck = [EnergyRef() for _ in range(30)]
    return b


def _fill_energy(fp, n: int = 8):
    """塞足能量，保证任何招式都能支付"""
    fp.energy.clear()
    for t in ("grass", "fire", "water", "lightning", "psychic", "fighting",
              "darkness", "metal"):
        fp.add_energy(t, n)


class TestPoolExtRoster(unittest.TestCase):
    """卡池规模与来源标注（诚实口径）"""

    def test_pool_ext_loaded(self):
        meta = reallib.catalog_meta().get("pool_ext")
        self.assertIsNotNone(meta, "扩展卡池未装载")
        self.assertGreater(meta["count"], 200,
                           f"扩展卡池过小：{meta['count']}")
        self.assertEqual(meta["provenance"], "dataset-derived")
        self.assertEqual(meta["fidelity"], "rule-parser-derived")

    def test_manual_pool_precedence(self):
        """人工卡池条目必须仍可查、且**不得**被标记为自动生成"""
        cat = reallib.catalog()
        for name in ("Dreepy", "Dragapult ex", "Rare Candy", "Switch"):
            self.assertIn(name, cat, f"人工卡池条目被覆盖：{name}")
            self.assertFalse(cat[name].get("derived"),
                             f"人工卡池条目被误标为自动生成：{name}")

    def test_derived_cards_carry_reason(self):
        """自动生成卡必须带可追溯说明（不冒充人工校对）"""
        cat = reallib.catalog()
        derived = [k for k, v in cat.items() if v.get("derived")]
        self.assertGreater(len(derived), 200)
        for k in derived[:50]:
            self.assertTrue(cat[k]["reason"], f"{k} 缺少保真度说明")

    def test_archetypes_still_perfect(self):
        for aid in reallib.archetype_ids():
            cov = reallib.coverage(aid)
            self.assertEqual(cov["executable_rate"], 1.0, aid)


class TestPrintingIdentity(unittest.TestCase):
    """印刷身份（2026-10-29 第二十一批）：同名不同印刷共存放行

    规则：
      · `printings()` 按 uid（`<卡名>@<SET> <number>`）收录**全部**印刷；
      · `catalog()` 按卡名只返回**首选印刷**（数据集最新）；
      · 预设卡表用 `uid` 锁定人工校对印刷 → 同名新印刷不会替换预设行为。
    """

    def test_same_name_multiple_printings(self):
        pr = reallib.printings()
        self.assertIn("Dusknoir@SIT 70", pr, "人工印刷未收录")
        self.assertIn("Dusknoir@SFA 20", pr, "同名数据集印刷未共存放行")
        self.assertEqual(reallib.catalog()["Dusknoir"]["uid"], "Dusknoir@SFA 20",
                         "卡名默认应取数据集最新印刷")

    def test_uid_addresses_exact_printing(self):
        manual = reallib.item_for("Dusknoir@SIT 70")["def"]
        newest = reallib.item_for("Dusknoir@SFA 20")["def"]
        self.assertTrue(manual.abilities[0].tag.startswith("cursed:"),
                        f"人工印刷的特性应为 cursed（实际 {manual.abilities})")
        self.assertEqual(newest.abilities[0].tag, "counters:13:scope:opp:1:selfko")
        # 卡名本身保持真实卡名（进化链/同名规则不受印刷影响）
        self.assertEqual(manual.name, "Dusknoir")
        self.assertEqual(newest.name, "Dusknoir")

    def test_preset_pins_manual_printing(self):
        """A1 卡表锁定 SIT 70 → 运行时使用人工校对版（而非数据集新印刷）"""
        deck = reallib.build_deck("A1")
        dusk = [c for c in deck
                if getattr(getattr(c, "pdef", None), "name", "") == "Dusknoir"]
        self.assertTrue(dusk, "A1 未找到 Dusknoir")
        self.assertTrue(dusk[0].pdef.abilities[0].tag.startswith("cursed:"),
                        "预设应执行人工印刷的 Cursed Blast")

    def test_all_preset_entries_pin_a_printing(self):
        for aid in reallib.archetype_ids():
            for e in reallib.decklist(aid):
                self.assertTrue(e.get("uid"), f"{aid}·{e['name']} 未锁定印刷")
                self.assertIsNotNone(reallib.item_for(e["uid"]),
                                     f"{aid}·{e['uid']} 不在卡池")

    def test_pool_uids_unique(self):
        pr = reallib.printings()
        self.assertEqual(len(pr), len(reallib._catalog_items()))  # noqa: SLF001


class TestEveryCardExecutes(unittest.TestCase):
    """逐卡穷举：每张卡的每个招式 / 每个特性都必须能被 step 成功"""

    def test_all_pokemon_attacks_execute(self):
        cat = reallib.catalog()
        checked = errors = 0
        failures = []
        for name, item in cat.items():
            cdef = item["def"]
            if not isinstance(cdef, PokemonDef):
                continue
            for ai in range(len(cdef.attacks)):
                if not cdef.attacks[ai].damage and not cdef.attacks[ai].effects:
                    continue
                b = _arena()
                fp = make_pokemon(cdef)
                _fill_energy(fp)
                b.players[0].active = fp
                act = {"verb": "attack", "attack": ai}
                _scope = _borrow_scope(cdef.attacks[ai])
                if _scope is not None and not _borrow_optional(_scope):
                    # S5：**必须借**的招式（含抛币门）—— 裸动作在规则上非法，
                    # 从合法动作表取「同一招式的第一个可借选项」。
                    _cand = [a for a in b.legal_actions()
                             if a.get("verb") == "attack"
                             and a.get("attack") == ai]
                    if _cand:
                        act = _cand[0]
                r = b.step(act)
                checked += 1
                if not r.get("ok"):
                    errors += 1
                    failures.append(f"{name} 招式{ai}"
                                    f"({cdef.attacks[ai].name}): {r.get('error')}")
        self.assertEqual(errors, 0,
                         f"{errors}/{checked} 招式执行失败：\n" +
                         "\n".join(failures[:15]))
        self.assertGreater(checked, 200, f"覆盖招式过少：{checked}")

    def test_all_field_abilities_execute(self):
        """field 特性：合法动作里的每一个都必须能 step 成功（被动除外）"""
        cat = reallib.catalog()
        checked = errors = 0
        failures = []
        for name, item in cat.items():
            cdef = item["def"]
            if not isinstance(cdef, PokemonDef) or not cdef.abilities:
                continue
            b = _arena()
            fp = make_pokemon(cdef)
            _fill_energy(fp)
            fp.damage = 60                      # 让 move_damage 类也可用
            b.players[0].active = fp
            b.players[0].bench = [
                make_pokemon(PokemonDef("Pawn", hp=100, ptype="colorless",
                                        attacks=()))]
            b.players[1].bench = [
                make_pokemon(PokemonDef("Pawn2", hp=100, ptype="colorless",
                                        attacks=()))]
            for a in [x for x in b.legal_actions(0) if x["verb"] == "ability"]:
                checked += 1
                r = b.step(a)
                if not r.get("ok"):
                    errors += 1
                    failures.append(f"{name} 特性({a.get('name')}): "
                                    f"{r.get('error')}")
        self.assertEqual(errors, 0,
                         f"{errors}/{checked} 特性执行失败：\n" +
                         "\n".join(failures[:15]))

    def test_all_trainers_execute_or_absent(self):
        cat = reallib.catalog()
        checked = errors = 0
        failures = []
        for name, item in cat.items():
            cdef = item["def"]
            if not isinstance(cdef, TrainerRef) and not (
                    hasattr(cdef, "tag") and type(cdef).__name__ == "TrainerDef"):
                continue
            b = _arena()
            p = b.players[0]
            p.hand = [TrainerRef(cdef)]
            p.hand += [EnergyRef() for _ in range(4)]
            p.deck = ([PokeRef(decklib.DRAKLING)]
                      + [EnergyRef() for _ in range(20)])
            p.discard_refs.append(EnergyRef())
            p.discard.append("Energy")
            p.bench = [make_pokemon(PokemonDef("Pawn3", hp=100,
                                               ptype="colorless", attacks=()))]
            # ⚠ 只执行**快照里的第一条** trainer 动作：同一张卡可能产生多个合法动作
            #   （S2 道具按「可附着的宝可梦」逐个列 targets），首条执行后卡已离开手牌，
            #   后续动作的手牌索引即失效 —— 那是测试拿旧快照连发导致的假失败。
            acts = [x for x in b.legal_actions(0) if x["verb"] == "trainer"]
            if acts:
                a = acts[0]
                checked += 1
                r = b.step(a)
                if not r.get("ok"):
                    errors += 1
                    failures.append(f"{name} 训练家({a.get('tag')}): "
                                    f"{r.get('error')}")
        self.assertEqual(errors, 0,
                         f"{errors}/{checked} 训练家执行失败：\n" +
                         "\n".join(failures[:15]))


class TestPoolSmokeBattles(unittest.TestCase):
    """全卡池随机对局冒烟：零 step 错误"""

    def _deck_from(self, cdef, filler_basic, n_card=4):
        deck = [PokeRef(cdef)] * n_card if isinstance(cdef, PokemonDef) else []
        if not deck:
            deck = [PokeRef(filler_basic)] * n_card
        if not any(isinstance(c, PokeRef) and c.pdef.stage == 0 for c in deck):
            deck.append(PokeRef(filler_basic))
        deck += [PokeRef(filler_basic)] * 10
        deck += [TrainerRef(reallib.catalog()["Rare Candy"]["def"])] * 2 \
            if "Rare Candy" in reallib.catalog() else []
        deck += [EnergyRef() for _ in range(60 - len(deck))]
        return deck[:60]

    def test_random_battles_with_pool_cards(self):
        cat = reallib.catalog()
        filler = decklib.DRAKLING
        pool_names = [n for n, v in cat.items()
                      if v["derived"] and isinstance(v["def"], PokemonDef)]
        self.assertGreater(len(pool_names), 100)
        random.seed(7)
        sample = random.sample(pool_names, 40)
        errs = 0
        played = 0
        for nm in sample:
            cdef = cat[nm]["def"]
            for seed in (1, 2):
                a = self._deck_from(cdef, filler)
                cdef_b = cat[random.choice(pool_names)]["def"]
                d = self._deck_from(cdef_b, filler)
                bt = Battle(a, d, seed=seed)
                ag = [RandomAgent(seed=seed), RandomAgent(seed=seed + 1)]
                g = 0
                while not bt.finished and g < 300:
                    r = bt.step(ag[bt.current].decide(bt.view()))
                    if not r.get("ok"):
                        errs += 1
                        break
                    g += 1
                played += 1
        self.assertEqual(errs, 0, f"{errs}/{played} 对局出现 step 错误")
        self.assertGreaterEqual(played, 40)


if __name__ == "__main__":
    unittest.main()
