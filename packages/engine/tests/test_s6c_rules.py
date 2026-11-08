"""S6c：伤害流水线收敛 · 特殊状态全套 · 奖赏时机

这三块都是「数值/状态静默错」的重灾区 —— 写错了对局照样能跑完，卡面、
日志、覆盖率、对账**全都看不出来**，只有逐项断言才能守住：

  · **伤害顺序**：基础 → ×弱点 → −抗性 → 静态/道具/场地减伤 → 护盾 → 免疫。
    卡面「after applying Weakness and Resistance」只有在弱抗之后再减才对；
    顺序一换数值就变（例：先减 30 再 ×2 = 少打 60），而且没有任何报错。
  · **状态构成**：中毒/灼伤是**标记**（可与其它状态共存），睡眠/混乱/麻痹是
    **转动卡牌**（三者互斥，后施加的取代先前的）。写成「只能有一个」会清掉
    合法组合；写成「全部叠加」会造出官方不允许的组合。
  · **灼伤检查期**：先放 2 个伤害指示物（20），**再**抛币；正面治愈。
    （日月之前的旧规则方向相反：抛币，反面才受伤 —— 两者差得很多。）
"""
import random
import unittest

from engine import zones
from engine.battle import Battle
from engine.cards import (AbilityDef, AttackDef, PokemonDef, PokeRef,
                          TrainerDef, TrainerRef, make_pokemon)
from engine.rules import STATUSES, apply_status_set, status_checkup
from engine.state import GameConfig


class _Fixed(random.Random):
    """固定抛币结果（0.1 → 正面；0.9 → 反面）"""

    def __init__(self, value: float):
        super().__init__(0)
        self.value = value

    def random(self) -> float:
        return self.value

    def shuffle(self, seq) -> None:
        return None


def _deck(tag: str = "A") -> list:
    """两副牌卡名**不相交**（同名卡会让状态/名字断言互相污染）"""
    return [PokeRef(PokemonDef(f"Filler{tag}{i}", hp=100, ptype="colorless"))
            for i in range(40)]


def _battle(seed: int = 5) -> Battle:
    """真实对局；关掉先手规则 —— 本套件验的是伤害/状态/奖赏，不是开局限制。"""
    return Battle(_deck("A"), _deck("B"), seed=seed,
                  cfg=GameConfig(first_turn_no_attack=False,
                                 first_turn_no_supporter=False))


def _solo(*statuses):
    fp = make_pokemon(PokemonDef("Subject", hp=200, ptype="colorless"))
    fp.status.update(statuses)
    return fp


# ---------------- 伤害流水线：顺序 ----------------
class TestDamagePipelineOrder(unittest.TestCase):
    """`Battle._damage_pipeline` 的**顺序**与逐步记账"""

    def _pipe(self, *, base=100, weakness="fire", resistance="fire",
              static_reduce=0, shield=0, atk_effects=(), atk_type="fire"):
        b = _battle()
        me, opp = b.players[b.current], b.players[1 - b.current]
        striker = make_pokemon(PokemonDef("Striker", hp=300, ptype=atk_type))
        me.active = striker
        abilities = ((AbilityDef("Wall", tag=f"passive:dmg_reduce:{static_reduce}"),)
                     if static_reduce else ())
        defender = make_pokemon(PokemonDef("Wall", hp=300, ptype="colorless",
                                           weakness=weakness,
                                           resistance=resistance,
                                           abilities=abilities))
        defender.shield_reduce = shield
        opp.active = defender
        atk = AttackDef("Probe", cost=0, damage=base,
                        effects=tuple(atk_effects))
        mods = b._attack_mods(striker, atk.effects, {}, defender, atk)
        res = b._damage_pipeline(striker, defender, atk, mods, base)
        return b, res

    def test_full_order_matches_official_sequence(self):
        """100 → ×2(弱点) → −30(抗性) → −30(静态) → −50(护盾) = 90"""
        _b, res = self._pipe(base=100, weakness="fire", resistance="fire",
                             static_reduce=30, shield=50)
        self.assertEqual(res["dmg"], 90, res["steps"])
        self.assertEqual([s["step"] for s in res["steps"]],
                         ["base", "weak_res", "static_reduce", "shield"])
        # `weak_res` 一步同时含弱点与抗性（`compute_damage` 内部先 ×2 再 −30）
        self.assertEqual([s["value"] for s in res["steps"]],
                         [100, 170, -30, -50])

    def test_naive_order_would_differ(self):
        """把「先减伤再 ×2」当成实现会得到**不同**的数字 → 顺序必须被钉住

        这不是同义反复：本断言先证明两种顺序的数值确实不同，再断言引擎取的是
        官方那一种。否则「顺序」测试会因为两个顺序恰好同值而变成假守卫。
        """
        canonical = 90
        naive = max(0, (100 - 30 - 50) * 2 - 30)      # 先减伤、再弱点、再抗性
        self.assertNotEqual(canonical, naive, "本用例必须让两种顺序不同值")
        _b, res = self._pipe(base=100, weakness="fire", resistance="fire",
                             static_reduce=30, shield=50)
        self.assertEqual(res["dmg"], canonical)
        self.assertNotEqual(res["dmg"], naive)

    def test_resistance_applies_after_weakness(self):
        """弱点 ×2 后再 −30（≈「×2 减 30」而不是「−30 再 ×2」）"""
        _b, res = self._pipe(base=50, weakness="fire", resistance="fire")
        self.assertEqual(res["dmg"], 70)               # 50*2=100, -30
        self.assertNotEqual(res["dmg"], 40)            # (50-30)*2

    def test_no_weakness_match_keeps_base(self):
        _b, res = self._pipe(base=80, weakness="water", resistance="water")
        self.assertEqual(res["dmg"], 80)
        self.assertEqual([s["step"] for s in res["steps"]], ["base", "weak_res"])

    def test_ignore_effects_wr_skips_weakness_and_resistance(self):
        """`ignore_effects:wr`：连印在卡上的弱抗也一起无视（宽口径）"""
        _b, res = self._pipe(base=100, weakness="fire", resistance="fire",
                             atk_effects=("ignore_effects:wr",))
        self.assertEqual(res["dmg"], 100, res["steps"])
        self.assertEqual(res["steps"][-1]["value"], 100)

    def test_steps_are_written_into_damage_event(self):
        """逐项顺序要进 `damage` 事件 —— 回放/可视化靠它解释「为什么是这个数」"""
        b = _battle()
        me, opp = b.players[b.current], b.players[1 - b.current]
        me.active = make_pokemon(PokemonDef(
            "Striker", hp=300, ptype="colorless",
            attacks=(AttackDef("Probe", cost=0, damage=100),)))
        opp.active = make_pokemon(PokemonDef("Wall", hp=300,
                                             ptype="colorless",
                                             weakness="colorless"))
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        dmg_ev = [e for e in b.events if e.get("type") == "damage"][-1]
        self.assertEqual(dmg_ev["dmg"], 200)           # 100 ×2（无色打无色弱点）
        self.assertEqual([s["step"] for s in dmg_ev["steps"]],
                         ["base", "weak_res"])
        self.assertEqual(dmg_ev["steps"][0]["value"], 100)
        self.assertEqual(dmg_ev["steps"][1]["value"], 200)


# ---------------- 特殊状态：构成规则 ----------------
class TestStatusComposition(unittest.TestCase):
    """标记型（中毒/灼伤）可共存；转动卡牌型（睡眠/混乱/麻痹）三选一"""

    def test_marker_pair_coexist(self):
        fp = _solo()
        apply_status_set(fp, ["poison", "burn"])
        self.assertEqual(fp.status, {"poison", "burn"})

    def test_orient_replaces_orient(self):
        fp = _solo("sleep")
        applied, removed = apply_status_set(fp, ["confuse"])
        self.assertEqual(applied, ["confuse"])
        self.assertEqual(removed, ["sleep"])
        self.assertEqual(fp.status, {"confuse"})

    def test_marker_survives_orient_change(self):
        """中毒/灼伤不会被睡眠/混乱/麻痹顶掉（官方：只有转动卡牌类互斥）"""
        fp = _solo("poison", "burn", "sleep")
        apply_status_set(fp, ["paralyze"])
        self.assertEqual(fp.status, {"poison", "burn", "paralyze"})

    def test_three_way_combo_is_legal(self):
        """官方原文可出现的组合：Burned + Paralyzed + Poisoned"""
        fp = _solo()
        apply_status_set(fp, ["burn", "poison"])
        apply_status_set(fp, ["paralyze"])
        self.assertEqual(fp.status, {"burn", "poison", "paralyze"})

    def test_same_effect_multi_status_both_apply(self):
        """**同一次效果**里给两个状态（`status_multi:burn+confuse`）都要生效

        ⚠ 这是 `_set_status` 收「列表」而不是「单个状态」的原因：逐条调用会让
        这两个状态**互相顶掉**（只剩后一个），而且日志里两条都写着「已施加」。
        """
        fp = _solo()
        apply_status_set(fp, ["burn", "confuse"])
        self.assertEqual(fp.status, {"burn", "confuse"})

    def test_multi_status_via_card_both_apply(self):
        """走**真实卡路径**（`status_multi:burn+confuse`）：两个状态都落到目标身上

        与上面的纯函数用例互补：这里钉的是**引擎侧**「一次效果内不互相顶掉」。
        逐条 `_set_status` 的写法在这一层会把 burn 顶掉（只剩 confuse），而纯函数
        用例根本看不到 —— 曾经就是「只测纯函数」才让这条注入保持绿色。
        """
        b = _battle()
        me, opp = b.players[b.current], b.players[1 - b.current]
        opp.active = make_pokemon(PokemonDef("Target", hp=200,
                                             ptype="colorless"))
        me.hand = [TrainerRef(TrainerDef("status_multi:burn+confuse",
                                         kind="item",
                                         name="Dangerous Laser"))]
        r = b.step({"verb": "trainer", "hand": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(opp.active.status, {"burn", "confuse"})

    def test_two_orient_in_one_effect_last_wins(self):
        """一次效果写了两个转动卡牌类 → 只留**最后一个**（卡面书写顺序）"""
        fp = _solo()
        applied, removed = apply_status_set(fp, ["sleep", "confuse"])
        self.assertEqual(applied, ["confuse"])
        self.assertEqual(removed, [])
        self.assertEqual(fp.status, {"confuse"})

    def test_two_orient_via_card_last_wins(self):
        """卡路径同口径（`status_multi:sleep+confuse`）"""
        b = _battle()
        me, opp = b.players[b.current], b.players[1 - b.current]
        opp.active = make_pokemon(PokemonDef("Target", hp=200,
                                             ptype="colorless"))
        me.hand = [TrainerRef(TrainerDef("status_multi:sleep+confuse",
                                         kind="item", name="Sleep Powder"))]
        r = b.step({"verb": "trainer", "hand": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(opp.active.status, {"confuse"})

    def test_repeat_same_status_is_noop(self):
        fp = _solo("burn")
        applied, removed = apply_status_set(fp, ["burn"])
        self.assertEqual(applied, ["burn"])
        self.assertEqual(removed, [])
        self.assertEqual(fp.status, {"burn"})

    def test_alias_normalized(self):
        """卡面形容词拼写（burned / paralyzed）要归一到规范名"""
        fp = _solo()
        apply_status_set(fp, ["burned", "paralyzed"])
        self.assertEqual(fp.status, {"burn", "paralyze"})

    def test_unknown_status_raises(self):
        """未知状态词必须报错 —— 静默丢弃会让「施加了但没生效」查不出来"""
        fp = _solo()
        with self.assertRaises(ValueError):
            apply_status_set(fp, ["frozen"])

    def test_empty_keeps_everything(self):
        fp = _solo("poison", "confuse")
        applied, removed = apply_status_set(fp, [])
        self.assertEqual((applied, removed), ([], []))
        self.assertEqual(fp.status, {"poison", "confuse"})


class TestStatusEntryPoint(unittest.TestCase):
    """`Battle._set_status`：唯一施加入口 + 替换日志"""

    def test_replacement_is_logged(self):
        b = _battle()
        me = b.players[b.current]
        me.active = make_pokemon(PokemonDef("A", hp=100, ptype="colorless"))
        b._set_status(me.active, ["sleep"])
        b._set_status(me.active, ["confuse"])
        self.assertEqual(me.active.status, {"confuse"})
        rep = [e for e in b.events if e.get("type") == "status_replace"]
        self.assertEqual(len(rep), 1, rep)
        self.assertEqual(rep[0]["removed"], ["sleep"])
        self.assertEqual(rep[0]["applied"], ["confuse"])

    def test_no_log_when_nothing_replaced(self):
        b = _battle()
        me = b.players[b.current]
        me.active = make_pokemon(PokemonDef("A", hp=100, ptype="colorless"))
        b._set_status(me.active, ["poison"])
        b._set_status(me.active, ["burn"])
        self.assertFalse([e for e in b.events
                          if e.get("type") == "status_replace"])


# ---------------- 灼伤检查期（官方：先伤害，再抛币治愈） ----------------
class TestBurnCheckup(unittest.TestCase):
    def test_tails_damages_and_keeps_burn(self):
        fp = _solo("burn")
        evs = status_checkup(fp, _Fixed(0.9))
        self.assertEqual(fp.damage, 20)
        self.assertIn("burn", fp.status)
        self.assertEqual([e["type"] for e in evs], ["burn_damage"])

    def test_heads_damages_then_cures(self):
        """★ 关键：正面**也要**吃 20 点伤害，只是随后治愈

        「抛币输了才受伤」是日月**之前**的旧规则。按旧规则实现时，正面这一支
        的伤害会变成 0 —— 本断言就是抓这个的（顺序反了必然变红）。
        """
        fp = _solo("burn")
        evs = status_checkup(fp, _Fixed(0.1))
        self.assertEqual(fp.damage, 20, "灼伤伤害与抛币无关，恒定 2 个指示物")
        self.assertNotIn("burn", fp.status)
        self.assertEqual([e["type"] for e in evs],
                         ["burn_damage", "burn_cure"])

    def test_poison_then_burn_order(self):
        fp = _solo("poison", "burn")
        evs = status_checkup(fp, _Fixed(0.9))
        self.assertEqual([e["type"] for e in evs],
                         ["poison_damage", "burn_damage"])

    def test_burn_no_cure_override(self):
        """Wela Volcano Park：灼伤不能靠这次抛币治愈（照样吃伤害）"""
        fp = _solo("burn")
        evs = status_checkup(fp, _Fixed(0.1), override={"burn_no_cure": True})
        self.assertEqual(fp.damage, 20)
        self.assertIn("burn", fp.status)
        self.assertEqual([e["type"] for e in evs], ["burn_damage"])


# ---------------- 奖赏时机：不足时取剩余即胜 ----------------
class TestPrizeShortage(unittest.TestCase):
    def test_last_prize_wins_even_if_ko_gives_more(self):
        """ex 应给 2 张、但只剩 1 张 → 取剩余 1 张即胜（不越界、不凭空生成）"""
        b = _battle()
        me, opp = b.players[b.current], b.players[1 - b.current]
        me.active = make_pokemon(PokemonDef(
            "Striker", hp=300, ptype="colorless",
            attacks=(AttackDef("Finisher", cost=0, damage=999),)))
        opp.active = make_pokemon(PokemonDef("VictimEx", hp=30,
                                             ptype="colorless", is_ex=True))
        opp.bench = [make_pokemon(PokemonDef("Backup", hp=100,
                                             ptype="colorless"))]
        # 只留 1 张奖赏卡（走 `zones.take_prizes`，保持 `prizes_left` 同步）
        zones.take_prizes(b, b.current, me.prizes_left - 1)
        self.assertEqual(me.prizes_left, 1)
        r = b.step({"verb": "attack", "attack": 0})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertTrue(b.finished, "取完最后一张奖赏应立刻结束对局")
        self.assertEqual(b.winner, b.current)
        self.assertEqual(me.prizes_left, 0)


if __name__ == "__main__":      # pragma: no cover
    unittest.main()
