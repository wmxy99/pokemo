# -*- coding: utf-8 -*-
"""PokéLab Agent · RL 训练管线用例（PolicyAgent / trainer）

运行：cd packages/agent && python -m unittest discover -s tests -v
"""
from __future__ import annotations
import sys
import tempfile
import unittest
from pathlib import Path

_ENGINE = Path(__file__).resolve().parents[3] / "packages" / "engine"
sys.path.insert(0, str(_ENGINE))

from engine import decklib
from engine.battle import Battle
from engine.cards import PokemonDef, AttackDef, PokeRef, make_pokemon

from agent.state_encoder import STATE_DIM, ACTION_DIM
from agent.policy_net import make_net
from agent.policy_agent import PolicyAgent, build_spread_plan
from agent import trainer


class TestPolicyAgent(unittest.TestCase):
    def test_decide_returns_legal(self):
        b = Battle(decklib.deck_lightning_demo(), decklib.deck_fire_demo(),
                   seed=3)
        net = make_net(STATE_DIM, ACTION_DIM)
        agent = PolicyAgent(net, name="pol")
        act = agent.decide(b.view())
        self.assertIn(act, b.legal_actions())

    def test_decide_untrained_differs_little(self):
        # 未训练网络决策仍为合法且确定（argmax 路径）
        b = Battle(decklib.deck_lightning_demo(), decklib.deck_fire_demo(),
                   seed=5)
        net = make_net(STATE_DIM, ACTION_DIM)
        a1 = PolicyAgent(net).decide(b.view())
        a2 = PolicyAgent(net).decide(b.view())
        self.assertEqual(a1, a2)      # argmax 确定性

    def test_spread_plan_applied(self):
        # 构造 Draco ex（spread_free 攻击）场景验证 targets 生成
        b = Battle(decklib.deck_draco_demo(), decklib.deck_fire_demo(), seed=9)
        me = b.players[b.current]
        me.active = make_pokemon(decklib.DRACO_EX)
        me.active.add_energy("fire", 1)
        me.active.add_energy("psychic", 1)
        me.active.just_played = False
        me.hand.clear()
        opp = b.players[1 - b.current]
        opp.bench = [make_pokemon(PokemonDef("T1", hp=150,
                                             ptype="colorless"))]
        v = b.view()
        atk_acts = [a for a in v["legal"] if a["verb"] == "attack"]
        self.assertTrue(atk_acts)
        planned = build_spread_plan(v, dict(atk_acts[0]))
        self.assertIn("targets", planned)
        self.assertEqual(sum(x["dmg"] for x in planned["targets"]), 60)


class TestTrainer(unittest.TestCase):
    def test_smoke_episode(self):
        b = Battle(decklib.deck_lightning_demo(),
                   decklib.deck_fire_demo(), seed=1)
        net = make_net(STATE_DIM, ACTION_DIM)
        steps = trainer.collect_episode(b, net)
        self.assertGreater(len(steps), 0)
        for s in steps:
            self.assertEqual(len(s["state_vec"]), STATE_DIM)
            self.assertGreaterEqual(s["chosen"], 0)

    def test_ppo_smoke_and_save(self):
        da, db = decklib.deck_lightning_demo(), decklib.deck_fire_demo()
        net = make_net(STATE_DIM, ACTION_DIM)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "policy.pt"
            st = trainer.train_ppo(da, db, net, episodes=2, ppo_epochs=2,
                                   seed=7, save_path=str(path))
            self.assertEqual(st["episodes"], 2)
            self.assertTrue(path.exists())
            self.assertTrue(all(math_isfinite(x) for x in st["loss"]),
                            f"loss={st['loss']}")

    def test_eval_interface(self):
        da, db = decklib.deck_lightning_demo(), decklib.deck_fire_demo()
        net = make_net(STATE_DIM, ACTION_DIM)
        st = trainer.eval_winrate(da, db, net, n=2, opponent="random")
        self.assertEqual(st["n"], 2)
        self.assertEqual(st["wins"] + st["losses"] + st["draws"], 2)


def math_isfinite(x):
    import math
    return math.isfinite(x)


if __name__ == "__main__":
    unittest.main(verbosity=2)
