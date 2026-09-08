# -*- coding: utf-8 -*-
"""PokéLab Agent · BC 预热用例

运行：cd packages/agent && python -m unittest discover -s tests -v
"""
from __future__ import annotations
import sys
import unittest
from pathlib import Path

_ENGINE = Path(__file__).resolve().parents[3] / "packages" / "engine"
sys.path.insert(0, str(_ENGINE))

from engine import decklib
from engine.agents import BaselineAI
from agent.state_encoder import STATE_DIM, ACTION_DIM
from agent.policy_net import make_net
from agent import bc


class TestBC(unittest.TestCase):
    def test_collect_frames(self):
        da, db = decklib.deck_lightning_demo(), decklib.deck_fire_demo()
        fr = bc.collect_demonstration(da, db, BaselineAI("a"), BaselineAI("b"),
                                      episodes=3, seed=1)
        self.assertGreater(len(fr), 30)
        self.assertEqual(len(fr[0]["state_vec"]), STATE_DIM)

    def test_train_bc_loss_decreases(self):
        da, db = decklib.deck_lightning_demo(), decklib.deck_fire_demo()
        fr = bc.collect_demonstration(da, db, BaselineAI("a"), BaselineAI("b"),
                                      episodes=5, seed=1)
        net = make_net(STATE_DIM, ACTION_DIM)
        st = bc.train_bc(net, fr, epochs=3, lr=1e-3, seed=1)
        self.assertLess(st["loss"][-1], st["loss"][0])
        self.assertGreater(st["final_acc"], 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
