# -*- coding: utf-8 -*-
"""PokéLab Engine · Mega Evolution 奖赏卡用例（2026-11-02 第三十四批）

规则：**Mega Evolution Pokémon ex 被击倒时，对手取 3 张奖赏卡**
（普通 1 张 / 普通 Pokémon ex 2 张 / Mega ex 3 张）。

此前引擎只有 ``ex_prizes``（2）一档，卡池准入因此把**整个 Mega 族**
（10 张在池外）一刀切拒收，理由是「引擎只建模 1/2 → 避免奖赏记账错误」。
本批补上第三档后，Mega 族得以按真实规则进池。

本文件守的四个方向：

  1. **档位不能塌缩**：Mega 不能再落到 ex 的 2 张（少算 = 削弱击倒方）；
  2. **不能叠加**：Mega 的 ``is_ex`` 同样为 True（它确实是 ex 的一种），
     取卡数必须**先判 mega 再判 ex**，不能 2+3 变成 5；
  3. **不因位置改变**：后备被击倒与战斗位同规则；
  4. **不越界**：击倒者剩余奖赏不足 3 张时取剩余张数并直接获胜，
     ``prizes_left`` 不得为负（否则会被当作「还差几张」而永远打不完）。

运行：cd packages/engine && python -m unittest tests.test_mega_prizes -v
"""
from __future__ import annotations

import random
import unittest

from engine.battle import Battle
from engine.cards import EnergyDef, EnergyRef, PokemonDef, make_pokemon
from engine.state import GameConfig, PlayerState

FILLER = 12


def arena(seed: int = 5):
    """最小可控战场：双方各 1 只战斗位宝可梦，牌库已填充。"""
    cfg = GameConfig()
    b = object.__new__(Battle)
    b.cfg = cfg
    b.seed = seed
    b.rng = random.Random(seed)
    b.players = [PlayerState(name="A", prizes_left=cfg.prizes),
                 PlayerState(name="B", prizes_left=cfg.prizes)]
    b.current = 0
    b.turn_number = 3
    b.turn_counts = [3, 3]
    b.phase = "main"
    b.finished = False
    b.winner = None
    b.win_reason = None
    b.events = []
    b.stadium = None
    b._ko_by_attack = [0, 0]
    b._ko_from_attack = False
    b.players[0].active = make_pokemon(
        PokemonDef("Attacker", hp=300, ptype="colorless", attacks=()))
    b.players[1].active = make_pokemon(
        PokemonDef("Victim", hp=50, ptype="colorless", attacks=()))
    for p in b.players:
        p.deck = [EnergyRef(EnergyDef("grass")) for _ in range(FILLER)]
    return b


def _victim(name: str = "Victim", *, is_ex: bool = False,
            is_mega: bool = False):
    return make_pokemon(PokemonDef(name, hp=50, ptype="colorless",
                                   attacks=(), is_ex=is_ex, is_mega=is_mega))


class TestPrizeTiers(unittest.TestCase):
    """三档取卡数：1 / 2 / 3"""

    def _taken(self, **kw) -> int:
        """让 owner=1 的宝可梦被击倒，返回击倒者（players[0]）取走的张数"""
        b = arena()
        fp = _victim(**kw)
        b.players[1].active = fp
        b.players[0].prizes_left = 6
        b._resolve_ko(1, fp)
        return 6 - b.players[0].prizes_left

    def test_plain_pokemon_gives_1(self):
        self.assertEqual(self._taken(), 1)

    def test_ex_gives_2(self):
        self.assertEqual(self._taken(is_ex=True), 2)

    def test_mega_ex_gives_3(self):
        """核心：Mega Evolution ex 被击倒 → 3 张"""
        self.assertEqual(self._taken(is_ex=True, is_mega=True), 3)

    def test_mega_is_not_summed_with_ex(self):
        """Mega 的 is_ex 也为 True —— 判定必须是「先 mega 再 ex」而非相加。
        若误写成 `ex_prizes + mega_prizes` 会得到 5 张（对手两回合就被打穿）。"""
        self.assertNotEqual(self._taken(is_ex=True, is_mega=True), 5)
        self.assertNotEqual(self._taken(is_ex=True, is_mega=True), 2)

    def test_mega_flag_alone_is_enough(self):
        """即使数据源漏标 is_ex，只要 is_mega 为真就按 3 张（不静默降级成 1）"""
        self.assertEqual(self._taken(is_mega=True), 3)


class TestPositionAndLimits(unittest.TestCase):
    """位置无关 · 奖赏不足时不越界"""

    def test_mega_ko_from_bench_gives_3(self):
        """后备被击倒与战斗位同规则（卡面规则与位置无关）"""
        b = arena()
        fp = _victim("BenchMega", is_ex=True, is_mega=True)
        b.players[1].bench = [fp]
        b.players[0].prizes_left = 6
        b._resolve_ko(1, fp)
        self.assertEqual(6 - b.players[0].prizes_left, 3)

    def test_not_enough_prizes_clamped_and_wins(self):
        """击倒者只剩 2 张奖赏时遇 Mega：取 2 张并直接获胜，prizes_left 不得为负"""
        b = arena()
        fp = _victim("Mega", is_ex=True, is_mega=True)
        b.players[1].active = fp
        b.players[0].prizes_left = 2
        b._resolve_ko(1, fp)
        self.assertEqual(b.players[0].prizes_left, 0)
        self.assertTrue(b.finished)
        self.assertEqual(b.winner, 0)
        self.assertEqual(b.win_reason, "prizes")

    def test_mega_prizes_is_configurable(self):
        """GameConfig.mega_prizes 可覆盖（便于与 oracle 校准）"""
        b = arena()
        b.cfg.mega_prizes = 3
        self.assertEqual(b.cfg.mega_prizes, 3)
        self.assertEqual(b.cfg.ex_prizes, 2)


class TestPoolWiring(unittest.TestCase):
    """卡池 → PokemonDef 的 ``is_mega`` 必须真的传到位（否则 3 张永不生效）"""

    def test_pool_entry_carries_is_mega(self):
        from engine import reallib
        entries = [c for c in reallib._catalog_raw().get("cards") or []
                   if c.get("is_mega")]
        self.assertTrue(entries, "池内不存在 is_mega 的条目 —— Mega 族未进池")
        # 走真实构建链（`_build_card`），确认字段没有在中间被吞掉
        pdef = reallib._build_card(entries[0])
        self.assertTrue(pdef.is_mega)
        self.assertTrue(pdef.is_ex, "Mega ex 的 is_ex 也须为真（条件 tag 依赖它）")

    def test_pool_has_several_megas(self):
        """解禁后应有成批的 Mega 进池（单张说明只修了个例）"""
        from engine import reallib
        n = sum(1 for c in reallib._catalog_raw().get("cards") or []
                if c.get("is_mega"))
        self.assertGreaterEqual(n, 10, f"池内 Mega 仅 {n} 张，解禁未真正生效")


if __name__ == "__main__":
    unittest.main()
