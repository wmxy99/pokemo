# -*- coding: utf-8 -*-
"""PokéLab Agent · 决策链路演示

用法（仓库根目录）：
  python -m pip install -r packages/agent/requirements.txt   # 需已装 torch（pokemo 已有）
  cd packages/agent && python examples/demo_decision.py [iters]

输出：一次决策的 state_vec 维度、候选 top3 审计、选中的动作、耗时。
"""
from __future__ import annotations
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]          # 仓库根
for pkg in ("engine", "agent"):
    sys.path.insert(0, str(_ROOT / "packages" / pkg))

from engine import decklib
from engine.battle import Battle
from agent.state_encoder import STATE_DIM, action_dim
from agent.policy_net import make_net
from agent.pipeline import decide


def main(iters: int = 30):
    b = Battle(decklib.deck_lightning_demo(), decklib.deck_fire_demo(),
               seed=20260908)
    net = make_net(STATE_DIM, action_dim())        # 未训练：均匀先验 + MCTS
    t0 = time.time()
    out = decide(b, policy=net, iters=iters, seed=20260908)
    dt = time.time() - t0
    print(f"state_dim={STATE_DIM} action_dim={action_dim()} iters={iters} "
          f"time={dt:.2f}s")
    print(f"turn={b.view()['turn']} current={b.players[b.current].name}")
    print("top3 候选（按先验）:")
    for c in out["audit"]:
        print("   ", c["action"], "prior=", c["prior"])
    print("MCTS 选中:", out["action"])
    return out


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 30)
