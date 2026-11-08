"""D2/D3 解析侧：退化 devolve + 多具名卡 gate

两条退化卡面的**数量语义不同**，是最容易写错的地方：
  · 「highest Stage Evolution card」= 固定退回 1 阶；
  · 「any number of Evolution cards」= 数量由玩家选（`:any`）。
把后者写成 `1` 是静默削弱；把前者写成 `any` 是多给了玩家选择。
"""
import unittest

from agent import effects as E

ARCH = ("Once during your turn, if this Pokémon is in the Active Spot, you may "
        "devolve 1 of your opponent’s evolved Pokémon by putting the highest "
        "Stage Evolution card on it into your opponent’s hand.")
TIME = ("Devolve 1 of your evolved {P} Pokémon by putting any number of "
        "Evolution cards on it into your hand. (That Pokémon can’t evolve this "
        "turn.)")
ANTHEA = ("You can use this card only if you have N’s Darmanitan, N’s Zoroark "
          "{ex}, N’s Vanilluxe, N’s Klinklang, N’s Reshiram, and N’s Zekrom in "
          "play.\n\nDuring this turn, if your opponent’s Active Pokémon is "
          "Knocked Out by damage from an attack used by your N’s Pokémon, take "
          "3 more Prize cards.")


def _tags(text, kind):
    sp = E.parse_rule(text, kind=kind, name="X")
    return E.to_engine_tags([sp])


class TestDevolveParse(unittest.TestCase):
    def test_opponent_highest_stage(self):
        r = _tags(ARCH, "ability")
        self.assertEqual(r["tags"], ["devolve:opp:1:active_only"])
        self.assertEqual(r["unsupported"], [])

    def test_self_any_number_with_type(self):
        r = _tags(TIME, "item")
        self.assertEqual(r["tags"], ["devolve:self:any:type:psychic"])
        self.assertEqual(r["unsupported"], [])

    def test_shuffle_into_deck_is_not_devolve(self):
        """「shuffle that Pokémon into their deck」不是退化（是洗回牌库），
        两者都带「put … into hand/deck」，最容易互相误认"""
        r = _tags("Flip a coin. If heads, choose 1 of your opponent’s Pokémon. "
                  "Shuffle that Pokémon and all attached cards into their deck.",
                  "attack")
        self.assertFalse([t for t in r["tags"] if t.startswith("devolve:")])

    def test_no_extra_tags_from_claimed_sentence(self):
        """认领之后必须抹掉整句：否则「Evolution card」被读成一次进化、
        尾注被读成 `candy` → 一卡两 tag → 整卡拒收"""
        for txt, kind in ((ARCH, "ability"), (TIME, "item")):
            self.assertEqual(len(_tags(txt, kind)["tags"]), 1, txt[:40])


class TestMultiNameGateParse(unittest.TestCase):
    def test_six_names(self):
        sp = E.parse_rule(ANTHEA, kind="supporter", name="X")
        gate = sp.get("play_gate") or ""
        self.assertTrue(gate.startswith("all_in_play:"), gate)
        self.assertEqual(len(gate.split("|")), 6)
        # 主效果仍在（gate 只是门槛，不能把效果吞掉）
        self.assertEqual(_tags(ANTHEA, "supporter")["tags"],
                         ["prize_bonus:3:nteam"])

    def test_single_condition_unchanged(self):
        """单条件不能被新分支抢走（回归：Tera / 奖赏数 那几条）"""
        sp = E.parse_rule(
            "You can use this card only if you have any Tera Pokémon in play. "
            "Draw 1 card.", kind="supporter", name="X")
        self.assertEqual(sp.get("play_gate"), "tera_in_play")


if __name__ == "__main__":      # pragma: no cover
    unittest.main()
