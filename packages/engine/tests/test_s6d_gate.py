"""D2：多条件组合门槛（`play_gate` 的合取形式）

卡面：「You can use this card only if you have A, B, and C in play.」
（Anthea & Concordia 要求 **6 张具名宝可梦**同时在场）

这条以前落到「认不出的 gate → 整卡拒收」：拒收本身是对的（不猜单条件），
但这张卡因此永远进不了池。现在 gate 支持 `all_in_play:<名1>|<名2>|…`，
本文件盯三件事：**缺一张就不许打**、**名字写法差异不算缺**、**合法动作表与
派发两侧同判**（只判一侧会出现「AI 反复尝试必然抛错的动作」）。
"""
import unittest

from engine.battle import Battle
from engine.cards import (AttackDef, PokemonDef, PokeRef, TrainerDef,
                          TrainerRef, make_pokemon)
from engine.state import GameConfig

NAMES = ["N's Darmanitan", "N's Zoroark ex", "N's Vanilluxe",
         "N's Klinklang", "N's Reshiram", "N's Zekrom"]
GATE = "all_in_play:" + "|".join(NAMES)


def _deck(tag="A"):
    return [PokeRef(PokemonDef(f"Filler{tag}{i}", hp=100, ptype="colorless"))
            for i in range(40)]


def _battle(seed=5):
    return Battle(_deck("A"), _deck("B"), seed=seed,
                  cfg=GameConfig(first_turn_no_attack=False,
                                 first_turn_no_supporter=False))


def _card(gate=GATE):
    return TrainerRef(TrainerDef("prize_bonus:3:nteam", kind="supporter",
                                 name="Anthea & Concordia", play_gate=gate))


def _fill(me, names, *, bench=True):
    """把指定卡名的宝可梦摆到场上（1 只在战斗位，其余进后备）"""
    mons = [make_pokemon(PokemonDef(n, hp=100, ptype="colorless"))
            for n in names]
    me.active = mons[0]
    if bench:
        me.bench = mons[1:]
    return mons


class TestAllInPlayGate(unittest.TestCase):
    def test_gate_blocks_when_one_missing(self):
        b = _battle()
        me = b.players[b.current]
        _fill(me, NAMES[:-1])          # 少一只 N's Zekrom
        me.hand = [_card()]
        acts = [a for a in b.legal_actions() if a.get("verb") == "trainer"]
        self.assertEqual(acts, [], "条件不满足时不该进合法动作表")
        r = b.step({"verb": "trainer", "hand": 0})
        self.assertFalse(r["ok"], "派发侧也必须拒绝")

    def test_gate_passes_with_all_six(self):
        b = _battle()
        me = b.players[b.current]
        me.bench = []                   # 清掉起手铺的后备，避免凑数
        _fill(me, NAMES)
        me.hand = [_card()]
        acts = [a for a in b.legal_actions() if a.get("verb") == "trainer"]
        self.assertEqual(len(acts), 1, acts)
        r = b.step(acts[0])
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(me.prize_bonus_n, 3, "打出后应记下 +3 奖赏旗标")

    def test_name_spelling_is_normalized(self):
        """`{ex}` 与非 `{ex}`、弯撇号与直撇号都算同一张（否则永远判不满足）"""
        b = _battle()
        me = b.players[b.current]
        me.bench = []
        # 引擎定义侧用 ` ex` 后缀 + 直撇号；卡面写的是 `{ex}` + 弯撇号
        _fill(me, ["N\u2019s Darmanitan", "N\u2019s Zoroark ex",
                   "N\u2019s Vanilluxe", "N\u2019s Klinklang",
                   "N\u2019s Reshiram", "N\u2019s Zekrom"])
        me.hand = [_card()]
        acts = [a for a in b.legal_actions() if a.get("verb") == "trainer"]
        self.assertEqual(len(acts), 1, "写法差异不该让门槛判不满足")

    def test_opponent_pokemon_do_not_count(self):
        """「you have」= **自己**场上：对手有也不算"""
        b = _battle()
        me, opp = b.players[b.current], b.players[1 - b.current]
        _fill(me, NAMES[:3])
        _fill(opp, NAMES[3:])
        me.hand = [_card()]
        acts = [a for a in b.legal_actions() if a.get("verb") == "trainer"]
        self.assertEqual(acts, [], "对手场上的具名卡不能凑数")

    def test_unknown_gate_raises(self):
        """未知条件必须抛错（不默认放行）—— 静默放行 = 门槛消失"""
        from engine.battle import _eval_condition
        b = _battle()
        with self.assertRaises(Exception):
            _eval_condition(b, "totally_unknown_condition")


if __name__ == "__main__":      # pragma: no cover
    unittest.main()
