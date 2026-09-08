# -*- coding: utf-8 -*-
"""PokéLab Agent · RL 自对弈训练脚本（PPO）

用法（仓库根）：
  cd packages/agent
  python examples/train_ppo.py --episodes 60 --decks lightning --save artifacts/policy.pt
  python examples/train_ppo.py --eval --weights artifacts/policy.pt --games 20

默认小规模（--episodes 4）用于闭环冒烟；正式收敛请加大 episodes（数百局量级）。
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
from agent.state_encoder import STATE_DIM, ACTION_DIM
from agent.policy_net import make_net
from agent import trainer


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--episodes", type=int, default=4)
    ap.add_argument("--decks", default="lightning")
    ap.add_argument("--opp-deck", default="fire")
    ap.add_argument("--save", default="artifacts/policy.pt")
    ap.add_argument("--eval", action="store_true")
    ap.add_argument("--weights", default="artifacts/policy.pt")
    ap.add_argument("--games", type=int, default=10)
    ap.add_argument("--opponent", default="baseline")
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--seed", type=int, default=2026)
    args = ap.parse_args()

    da = decklib.make_deck(args.decks)
    db = decklib.make_deck(args.opp_deck)
    if args.eval:
        net = trainer.load_net(args.weights)
        st = trainer.eval_winrate(da, db, net, n=args.games,
                                  opponent=args.opponent)
        print(f"eval: policy vs {args.opponent} {st}")
        return
    net = make_net(STATE_DIM, ACTION_DIM)
    t0 = time.time()
    st = trainer.train_ppo(da, db, net, episodes=args.episodes,
                           ppo_epochs=args.epochs, seed=args.seed,
                           save_path=args.save, log_every=max(1, args.episodes // 5))
    print(f"train done in {time.time() - t0:.1f}s | {st['episodes']} episodes | "
          f"agent0_wins={st['agent0_wins']} | loss tail={st['loss'][-3:]}"
          if st["loss"] else f"train done | {st}")
    print(f"weights -> {args.save}")


if __name__ == "__main__":
    main()
