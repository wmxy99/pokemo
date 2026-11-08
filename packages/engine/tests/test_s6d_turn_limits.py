"""S6d：每回合限制 + 进化解除状态（矩阵里此前只有散文、没有用例的几条）

这些规则都在引擎里跑了很久，但**没有一条专项断言** —— 也就是说「改坏了不会红」。
矩阵（`docs/RULE_CONFORMANCE.md`）要求每行都有守卫测试，所以补在这里：

  · 每回合只能附着 1 张能量；
  · 每回合只能使用 1 张支援者；
  · 每只宝可梦最多附着 1 张道具；
  · 场上同时只有 1 张场地卡（新的替换旧的）；
  · 进化解除全部特殊状态（Dizzying Valley 的例外另有用例守）。
"""
import unittest

from engine.battle import Battle
from engine.cards import (AttackDef, EnergyDef, EnergyRef, PokemonDef, PokeRef,
                          TrainerDef, TrainerRef, make_pokemon)
from engine.state import GameConfig


def _deck(tag: str = "A") -> list:
    return [PokeRef(PokemonDef(f"Filler{tag}{i}", hp=100, ptype="colorless"))
            for i in range(40)]


def _battle(seed: int = 5) -> Battle:
    return Battle(_deck("A"), _deck("B"), seed=seed,
                  cfg=GameConfig(first_turn_no_attack=False,
                                 first_turn_no_supporter=False))


def _energy(name: str = "grass") -> EnergyRef:
    return EnergyRef(EnergyDef(name))


class TestTurnLimits(unittest.TestCase):
    def test_one_energy_attach_per_turn(self):
        """官方：每回合**只能**从手牌附着 1 张能量"""
        b = _battle()
        p = b.players[b.current]
        p.hand = [_energy(), _energy()]
        r = b.step({"verb": "attach", "hand": 0, "target": 0})
        self.assertTrue(r["ok"], r.get("error"))
        r2 = b.step({"verb": "attach", "hand": 0, "target": 0})
        self.assertFalse(r2["ok"], "第二张能量必须被拒")
        self.assertIn("能量", r2["error"])

    def test_one_supporter_per_turn(self):
        b = _battle()
        p = b.players[b.current]
        sup = TrainerDef("draw:1", kind="supporter", name="Test Supporter")
        p.hand = [TrainerRef(sup), TrainerRef(sup)]
        r = b.step({"verb": "trainer", "hand": 0})
        self.assertTrue(r["ok"], r.get("error"))
        r2 = b.step({"verb": "trainer", "hand": 0})
        self.assertFalse(r2["ok"], "第二张支援者必须被拒")
        self.assertIn("支援者", r2["error"])

    def test_one_tool_per_pokemon(self):
        """每只宝可梦最多 1 张宝可梦道具（第二张要报错，不是静默替换）"""
        b = _battle()
        me = b.players[b.current]
        me.active = make_pokemon(PokemonDef("Holder", hp=120,
                                           ptype="colorless"))
        # 用**真实形态**的 tool tag：`fp.tool_name` 只有真工具 tag 才会置位，
        # 拿普通效果 tag 冒充工具时「每只 1 张」这条根本走不到（假绿）。
        tool = TrainerDef("tool:survive:10", kind="tool", name="Test Tool")
        me.hand = [TrainerRef(tool), TrainerRef(tool)]
        r = b.step({"verb": "trainer", "hand": 0, "target": 0})
        self.assertTrue(r["ok"], r.get("error"))
        r2 = b.step({"verb": "trainer", "hand": 0, "target": 0})
        self.assertFalse(r2["ok"], "同一只身上第二张道具必须被拒")
        self.assertIn("道具", r2["error"])

    def test_only_one_stadium_in_play(self):
        """场地卡：新的一张**替换**旧的一张（场上恒只有 1 张）"""
        b = _battle()
        me = b.players[b.current]
        # 场地同样要真 tag（`stadium:` 前缀才会写进 `battle.stadium`）
        me.hand = [TrainerRef(TrainerDef("stadium:dmg_reduce:10", kind="stadium",
                                         name="Stadium A")),
                   TrainerRef(TrainerDef("stadium:dmg_reduce:20", kind="stadium",
                                         name="Stadium B"))]
        r = b.step({"verb": "trainer", "hand": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(b.stadium["name"], "Stadium A")
        r2 = b.step({"verb": "trainer", "hand": 0})
        self.assertTrue(r2["ok"], r2.get("error"))
        self.assertEqual(b.stadium["name"], "Stadium B", "新的场地替换旧的")
        # 旧的进**拥有者**的弃牌堆（文本层 + 类型化层都要有：从弃牌堆回收的
        # 效果读的是 `discard_refs`，只推文本层等于「看得见但拿不到」）
        self.assertIn("Stadium A", me.discard, me.discard)
        self.assertTrue(any(getattr(x, "tdef", None) is not None
                            and x.tdef.name == "Stadium A"
                            for x in me.discard_refs), me.discard_refs)


class TestEvolutionClearsStatus(unittest.TestCase):
    def test_evolve_clears_all_special_conditions(self):
        """官方：进化解除**全部**特殊状态（含睡眠/混乱/麻痹与中毒/灼伤）"""
        b = _battle()
        me = b.players[b.current]
        base = make_pokemon(PokemonDef("Basic", hp=100, ptype="colorless"))
        base.status.update({"poison", "burn", "sleep"})
        me.active = base
        me.hand = [PokeRef(PokemonDef("Evo", hp=140, ptype="colorless",
                                      stage=1, evolves_from="Basic"))]
        r = b.step({"verb": "evolve", "hand": 0, "target": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(me.active.status, set(), "进化后不得残留任何特殊状态")

    def test_bench_clear_on_retreat_is_covered_elsewhere(self):
        """换下战斗位同样解除状态 —— 由 `_do_retreat` 的 `clear_status()` 承担

        这里只做**存在性**断言：清状态调用点仍在（删掉就会静默让状态跟到后备）。
        """
        import inspect

        from engine import battle as mod
        src = inspect.getsource(mod.Battle._do_retreat)
        self.assertIn("clear_status()", src)


if __name__ == "__main__":      # pragma: no cover
    unittest.main()
