# -*- coding: utf-8 -*-
"""PokéLab Agent · DQN 对照训练 CLI（技术方案：PPO 主选 / DQN 对照）

用法（packages/agent）：
  python examples/train_dqn.py --episodes 300 --save artifacts/dqn_policy.pt
  python examples/train_dqn.py --eval --weights artifacts/dqn_policy.pt --games 20
"""
from __future__ import annotations
import argparse
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
for pkg in ("engine", "agent"):
    sys.path.insert(0, str(_ROOT / "packages" / pkg))

from engine import decklib
from engine.agents import BaselineAI, RandomAgent
from agent.state_encoder import STATE_DIM, ACTION_DIM
from agent.policy_net import make_net
from agent import dqn, trainer


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--decks", default="lightning")
    ap.add_argument("--episodes", type=int, default=300)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--gamma", type=float, default=0.99)
    ap.add_argument("--eps", type=float, default=0.3)
    ap.add_argument("--eps-min", type=float, default=0.05)
    ap.add_argument("--eps-decay", type=float, default=0.995)
    ap.add_argument("--shaping", type=float, default=0.0)
    ap.add_argument("--target-update", type=int, default=10)
    ap.add_argument("--opponent", default="baseline")
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--save", default="artifacts/dqn_policy.pt")
    ap.add_argument("--eval", action="store_true")
    ap.add_argument("--games", type=int, default=20)
    args = ap.parse_args()

    da = decklib.make_deck(args.decks)
    db = list(da)
    if args.eval:
        net = trainer.load_net(args.save)
        ev = trainer.eval_winrate(da, db, net, n=args.games,
                                  opponent=args.opponent)
        print(f"eval: DQN vs {args.opponent} {ev}")
        return
    net = make_net(STATE_DIM, ACTION_DIM)
    opp = (BaselineAI("baseline") if args.opponent == "baseline"
           else RandomAgent("random", seed=0))
    t0 = time.time()
    st = dqn.train_dqn_vs_fixed(
        da, db, net, opp, episodes=args.episodes, lr=args.lr,
        gamma=args.gamma, eps=args.eps, eps_min=args.eps_min,
        eps_decay=args.eps_decay, shaping=args.shaping,
        target_update=args.target_update, seed=args.seed,
        device=args.device, save_path=args.save,
        log_every=max(1, args.episodes // 5))
    print(f"[dqn] done {st['episodes']} eps in {time.time()-t0:.0f}s | "
          f"wins={st['wins']} losses={st['losses']} draws={st['draws']} "
          f"q_tail={st['q_loss'][-3:]}" if st["q_loss"] else f"[dqn] done {st}")
    print(f"weights -> {args.save}")


if __name__ == "__main__":
    main()
