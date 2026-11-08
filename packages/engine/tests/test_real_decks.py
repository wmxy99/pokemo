# -*- coding: utf-8 -*-
"""A1–A4（现在使用的真实卡组）建模完整性守卫 + **真实卡**级效果接线验证

为什么需要这个文件：
  `reallib.coverage()` 报的 `executable_rate = 1.0` 只说明「每张卡都找到了引擎定义」，
  既不保证「效果在实现里真的被识别」（标签拼错会被**静默忽略**），也不保证
  「接线正确」（把效果挂到了错误的招式上）。本文件把三条不变量**固化成守卫**，
  并以 A1–A4 的**真实卡**为入口验证效果接线：

    INV-A  A1–A4 每张卡都有真实定义（无 replaced / approximated / unsupported）
    INV-B  这些卡用到的每个效果族都在引擎实现里出现过（防「静默忽略」）
    INV-C  这些卡用到的每个效果族都能在**测试代码**里找到引用（防「实现了但没测」）

  真实卡级验证（既有测试用的是合成持有者，如 `A("Toxic", 1, 10, ("poison",))`，
  **不能**发现「真实卡定义里标签挂错/漏挂」这类问题）：
    Budew·Itchy Pollen → poison · Dusclops·Nightmare → sleep
    Munkidori·Mind Bend → confuse · Blaziken ex·Blaze Dance → evolve_attach

运行：cd packages/engine && python -m unittest discover -s tests
"""
from __future__ import annotations

import random
import re
import unittest
from pathlib import Path

from engine import reallib
from engine.battle import Battle
from engine.cards import (AttackDef, EnergyDef, EnergyRef, PokeRef, PokemonDef,
                          TrainerDef, make_pokemon)
from engine.rules import status_checkup
from engine.state import GameConfig, PlayerState

REPO = Path(__file__).resolve().parents[3]
ENGINE_SRC = REPO / "packages" / "engine" / "engine"
ENGINE_TESTS = REPO / "packages" / "engine" / "tests"


def _preset_uid(name: str):
    """预设卡表为该卡锁定的**印刷 uid**（人工校对版；同名新印刷不会替换预设）"""
    for aid in reallib.archetype_ids():
        for e in reallib.decklist(aid):
            if e.get("name") == name and e.get("uid"):
                return e["uid"]
    return None


def _def_of(name: str):
    """取真实卡组**实际使用**的引擎定义（按预设锁定的印刷，而非卡名默认）"""
    item = reallib.item_for(_preset_uid(name) or name)
    if item is None:
        raise AssertionError(f"引擎卡池没有 {name}")
    if item.get("unsupported"):
        raise AssertionError(f"{name} 在引擎里是占位（{item.get('reason')}）")
    return item["def"]


def _arena(seed: int = 7):
    """最小可控战场：双方各 1 只空壳 + 20 张牌库（不触发首回合限制）"""
    cfg = GameConfig()
    b = object.__new__(Battle)
    b.cfg = cfg
    b.seed = seed
    b.rng = random.Random(seed)
    b.players = [PlayerState(name="A", prizes_left=cfg.prizes),
                 PlayerState(name="B", prizes_left=cfg.prizes)]
    b.current = 0
    b.turn_number = 2
    b.turn_counts = [2, 2]          # 避开「先手第一回合不可攻击」等首回合限制
    b.phase = "main"
    b.finished = False
    b.winner = None
    b.win_reason = None
    b.events = []
    b.players[0].active = make_pokemon(
        PokemonDef("DummyA", hp=200, ptype="colorless", attacks=()))
    b.players[1].active = make_pokemon(
        PokemonDef("DummyB", hp=200, ptype="colorless", attacks=()))
    for p in b.players:
        p.deck = [EnergyRef() for _ in range(20)]
    return b


class _FixedRng:
    """把 rng.random() 钉死成固定值（抛币分支确定性测试用）"""

    def __init__(self, value: float):
        self.value = value

    def random(self):                       # noqa: D102
        return self.value


class TestArchetypeCoverage(unittest.TestCase):
    """INV-A/B/C：A1–A4 建模完整性（当前使用的真实卡组）"""

    def _used_families(self) -> dict:
        """{效果族: {卡名…}} —— 仅统计 A1–A4 卡表里真实出现的卡"""
        fams: dict = {}
        names = set()
        for aid in reallib.archetype_ids():
            names |= {e["name"] for e in reallib.decklist(aid)}
        cat = reallib.catalog()
        for name in sorted(names):
            item = cat.get(name)
            self.assertIsNotNone(item, f"{name} 不在引擎卡池（占位）")
            self.assertFalse(item.get("unsupported"),
                             f"{name} 在引擎里是占位：{item.get('reason')}")
            d = item["def"]
            tags = []
            for a in getattr(d, "attacks", ()):
                tags += list(a.effects)
            tags += [ab.tag for ab in getattr(d, "abilities", ())]
            if isinstance(d, TrainerDef):
                tags.append(d.tag)
            for t in tags:
                fams.setdefault(str(t).split(":")[0], set()).add(name)
        return fams

    def test_inv_a_every_deck_card_is_modeled(self):
        """INV-A：四套卡组都 100% 可执行，零占位、零近似，运行时也无占位定义"""
        for aid in reallib.archetype_ids():
            cov = reallib.coverage(aid)
            self.assertEqual(cov["executable_rate"], 1.0,
                             f"{aid} 可执行率 {cov['executable_rate']}（应 1.0）")
            self.assertEqual(cov["replaced"], [], f"{aid} 存在占位卡：{cov['replaced']}")
            self.assertEqual(cov["approximated"], [],
                             f"{aid} 存在近似卡：{cov['approximated']}")
            self.assertTrue(cov["battle_ready"], f"{aid} 不满足可对战阈值")
            for card in reallib.build_deck(aid):
                d = (getattr(card, "pdef", None) or getattr(card, "tdef", None)
                     or getattr(card, "edef", None))
                self.assertNotEqual(getattr(d, "tag", None), "unsupported",
                                    f"{aid} 运行时出现占位卡 {getattr(d, 'name', '?')}")
                self.assertTrue(getattr(d, "name", None), f"{aid} 出现无名定义")

    def test_inv_b_every_used_family_is_implemented(self):
        """INV-B：卡上用到的每个效果族，都能在引擎实现代码里找到（防静默忽略）"""
        code = "\n".join(p.read_text(encoding="utf-8")
                         for p in ENGINE_SRC.glob("*.py"))
        missing = []
        for fam in self._used_families():
            pat = r"(?<![a-z_])" + re.escape(fam) + r"(?![a-z_])"
            if not re.search(pat, code):
                missing.append(fam)
        self.assertEqual(missing, [],
                         f"这些效果族在卡上出现但引擎实现里找不到：{missing}")

    def test_inv_c_every_used_family_is_covered_by_tests(self):
        """INV-C：卡上用到的每个效果族，测试代码里都有引用（防「实现了但没测」）

        注释行会被剔除 —— 不允许靠写注释「刷」覆盖。
        """
        raw = "\n".join(p.read_text(encoding="utf-8")
                        for p in ENGINE_TESTS.glob("*.py"))
        lines = [ln.split("#", 1)[0] for ln in raw.splitlines()]
        code = "\n".join(lines)
        missing = []
        for fam in self._used_families():
            pat = r"(?<![a-z_])" + re.escape(fam) + r"(?![a-z_])"
            if not re.search(pat, code):
                missing.append(fam)
        self.assertEqual(missing, [],
                         f"这些效果族有实现但没有任何测试引用：{missing}")


class TestRealCardEffectWiring(unittest.TestCase):
    """以**真实卡**为入口验证效果接线（合成持有者测不到"标签挂错招式"）"""

    def test_budew_itchy_pollen_poisons_and_ticks(self):
        """Budew·Itchy Pollen → poison：挂上中毒，并在回合开始扣 10（持续）"""
        atk = _def_of("Budew").attacks[0]
        self.assertIn("poison", atk.effects, "真实卡定义里应有 poison 效果")
        b = _arena()
        budew = make_pokemon(_def_of("Budew"))
        budew.add_energy("grass", atk.cost_total)
        b.players[0].active = budew
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertIn("poison", b.players[1].active.status)
        # 回合开始：中毒 −10（不自动解除）
        before = b.players[1].active.damage
        b.current = 1
        b._begin_turn()
        self.assertEqual(b.players[1].active.damage, before + 10)
        self.assertIn("poison", b.players[1].active.status, "中毒不应自动解除")
        # 事件层：checkup 里的子事件会以其自身类型记录（poison_damage），
        # 因为 _log 的 payload 会覆盖外层 type —— 断言要认这个类型。
        self.assertTrue(any(e["type"] == "poison_damage" and e.get("dmg") == 10
                            for e in b.events),
                        f"应记录中毒扣血事件，实际事件：{[e['type'] for e in b.events]}")

    def test_dusclops_nightmare_sleeps_and_blocks_attack(self):
        """Dusclops·Nightmare → sleep：挂上睡眠，且睡眠方**不能攻击**"""
        atk = _def_of("Dusclops").attacks[1]
        self.assertIn("sleep", atk.effects, "真实卡定义里应有 sleep 效果")
        b = _arena()
        dusclops = make_pokemon(_def_of("Dusclops"))
        dusclops.add_energy("psychic", 3)      # 彩色 2 + 无色 1
        b.players[0].active = dusclops
        r = b.step({"verb": "attack", "attack": 1})
        self.assertTrue(r["ok"], r.get("error"))
        # ⚠ 不能只看残留 status：攻击后回合移交，守方回合开始会做 checkup，
        #   抛币正面即**当场苏醒**（种子相关）→ 以事件为证更稳。
        self.assertTrue(any(e["type"] == "status" and e.get("status") == "sleep"
                            for e in b.events),
                        f"应记录陷入睡眠，实际事件：{[e['type'] for e in b.events]}")
        # 睡眠方没有攻击动作（legal_actions 里 attack 被屏蔽）
        b.players[1].active.status.add("sleep")
        self.assertNotIn("attack", {a["verb"] for a in b.legal_actions(1)})
        # 规则函数层：抛币正面即苏醒
        woke = status_checkup(b.players[1].active, _FixedRng(0.1))
        self.assertTrue(any(e["type"] == "sleep_wake" for e in woke))
        self.assertNotIn("sleep", b.players[1].active.status)
        # 反面则继续睡
        b.players[1].active.status.add("sleep")
        still = status_checkup(b.players[1].active, _FixedRng(0.9))
        self.assertFalse(any(e["type"] == "sleep_wake" for e in still))
        self.assertIn("sleep", b.players[1].active.status)

    def test_munkidori_mind_bend_confuses_then_self_hits(self):
        """Munkidori·Mind Bend → confuse：挂上混乱；混乱方攻击抛币反面 → 自伤 30"""
        atk = _def_of("Munkidori").attacks[0]
        self.assertIn("confuse", atk.effects, "真实卡定义里应有 confuse 效果")
        b = _arena()
        munk = make_pokemon(_def_of("Munkidori"))
        munk.add_energy("darkness", 2)         # 恶 1 + 无色 1
        b.players[0].active = munk
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertIn("confuse", b.players[1].active.status)

        # 让混乱的 B 攻击：抛币反面（random() < 0.5）→ 自伤 30 + 回合结束
        b.current = 1
        b.rng = _FixedRng(0.1)
        foe = make_pokemon(PokemonDef(
            "Foe", hp=200, ptype="colorless",
            attacks=(AttackDef("Tackle", cost=0, damage=10),)))
        foe.status.add("confuse")
        b.players[1].active = foe
        r2 = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r2["ok"], r2.get("error"))
        self.assertEqual(foe.damage, 30, "混乱反面应自伤 30")
        self.assertNotEqual(b.players[0].active.damage, 10,
                            "反面时攻击失败，不应打到对手")
        # 反面自伤时不清除混乱（与卡面一致：翻到反面则攻击失败且混乱不解除）
        self.assertIn("confuse", foe.status)

        # 抛币正面（random() >= 0.5）→ 正常攻击，**混乱仍不解除**（S6c 官方）
        b2 = _arena()
        b2.current = 1
        b2.rng = _FixedRng(0.9)
        hero = make_pokemon(PokemonDef(
            "Hero", hp=200, ptype="colorless",
            attacks=(AttackDef("Tackle", cost=0, damage=10),)))
        hero.status.add("confuse")
        b2.players[1].active = hero
        r3 = b2.step({"verb": "attack", "attack": 0})
        self.assertTrue(r3["ok"], r3.get("error"))
        self.assertIn("confuse", hero.status, "正面只是攻击成立，混乱不解除")
        self.assertEqual(b2.players[0].active.damage, 10, "正面应正常造成伤害")

    def test_blaziken_blaze_dance_attaches_from_discard_on_evolve(self):
        """Blaziken ex·Blaze Dance → evolve_attach:2:fire:discard：进化时从弃牌堆附 2 火能"""
        blaze = _def_of("Blaziken ex")
        ab = blaze.abilities[0]
        self.assertTrue(ab.tag.startswith("evolve_attach:"),
                        f"真实卡定义里应是 evolve_attach 族，实际 {ab.tag}")
        self.assertEqual(ab.location, "field")
        b = _arena()
        b.players[0].active = make_pokemon(_def_of("Combusken"))
        b.players[0].hand = [PokeRef(blaze)]
        fire = EnergyDef("fire", name_zh="基本火能量")
        # ⚠ 弃牌堆是**两层**的：`discard`（文本名）+ `discard_refs`（类型化对象）。
        #   从弃牌堆取能只认 discard_refs，必须走引擎自己的 _to_discard 保持同步。
        for _ in range(3):
            Battle._to_discard(b.players[0], EnergyRef(fire))
        self.assertEqual(len(b.players[0].discard_refs), 3)
        r = b.step({"verb": "evolve", "hand": 0, "target": 0})
        self.assertTrue(r["ok"], r.get("error"))
        new_active = b.players[0].active
        self.assertEqual(new_active.pdef.name, "Blaziken ex")
        self.assertEqual(new_active.energy_of("fire"), 2,
                         "进化时应从弃牌堆附 2 个火能量到自身")
        self.assertEqual(len(b.players[0].discard), 1,
                         "应从弃牌堆取走 2 张火能量")
        # 事件是以 ability + trigger=on_evolve 记录的（不是独立事件名）
        self.assertTrue(any(e["type"] == "ability"
                            and e.get("trigger") == "on_evolve"
                            and e.get("name") == ab.name for e in b.events),
                        "应记录进化触发的特性事件")


if __name__ == "__main__":
    unittest.main()
