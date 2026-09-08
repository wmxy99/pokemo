# -*- coding: utf-8 -*-
"""PokéLab Agent · 两阶段训练 CLI（BC 预热 → PPO 续训 → 周期评估）

用法（仓库根 packages/agent 下）：
  python examples/train_pipeline.py --bc-episodes 40 --ppo-episodes 200 \
      --decks lightning --opp-deck fire --save artifacts/final.pt \
      --eval-every 25 --games 8

阶段：
  BC    —— 采集 BaselineAI vs BaselineAI 演示 → masked CE 预热（先验"会做人"）
  PPO   —— 自对弈续训（加载 BC 权重继续），每 eval-every 局打 vs baseline 观测胜率
产物：artifacts/final.pt + artifacts/curve.json（训练曲线，供长训/回放）
"""
from __future__ import annotations
import argparse
import json
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
from agent import bc, trainer


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--decks", default="lightning")
    ap.add_argument("--opp-deck", default="fire")
    ap.add_argument("--bc-episodes", type=int, default=40)
    ap.add_argument("--bc-epochs", type=int, default=12)
    ap.add_argument("--ppo-episodes", type=int, default=200)
    ap.add_argument("--ppo-epochs", type=int, default=3)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--gamma", type=float, default=0.99)
    ap.add_argument("--clip", type=float, default=0.2)
    ap.add_argument("--ent-coef", type=float, default=0.02)
    ap.add_argument("--eval-every", type=int, default=25)
    ap.add_argument("--games", type=int, default=8)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--save", default="artifacts/final.pt")
    ap.add_argument("--bc-data-cache", default=None,
                    help="缺省自动按 deck/镜像派生文件名")
    ap.add_argument("--mirror", action="store_true", default=True,
                    help="镜像对局（deck_b=deck_a，基准 0.5，便于观测策略成长）")
    ap.add_argument("--no-mirror", dest="mirror", action="store_false")
    ap.add_argument("--train-mode", default="vs-fixed",
                    help="vs-fixed（推荐：先打败基线）/ selfplay（纯自对弈）")
    ap.add_argument("--seed", type=int, default=2026)
    args = ap.parse_args()

    da = decklib.make_deck(args.decks)
    db = list(da) if args.mirror else decklib.make_deck(args.opp_deck)
    cache = args.bc_data_cache or (
        f"artifacts/bc_data_{args.decks}_"
        f"{'mirror' if args.mirror else args.opp_deck}.pt")
    args.bc_data_cache = cache
    t0 = time.time()

    # ---- 基准对照：基线 AI vs 基线 AI（判断策略 vs 卡组对位） ----
    from engine.agents import BaselineAI as _BA
    from engine.simulator import run_many as _run_many
    base = _run_many(da, db, _BA("bA"), _BA("bB"), n=args.games)
    mode_note = "镜像同卡组" if args.mirror else f"vs {args.opp_deck}"
    print(f"[ref] baseline vs baseline ({mode_note}) "
          f"win_rate_a={base['win_rate_a']:.2f}（对位基准）")

    # ---- BC 阶段 ----
    bc_path = Path(args.bc_data_cache)
    if bc_path.exists():
        frames = bc.load_frames(str(bc_path))
        print(f"[bc] cache hit {bc_path} ({len(frames)} frames)")
    else:
        print(f"[bc] collect {args.bc_episodes} demos ...")
        frames = bc.collect_demonstration(da, db, BaselineAI("bA"),
                                          BaselineAI("bB"),
                                          episodes=args.bc_episodes,
                                          seed=args.seed)
        bc.save_frames(frames, str(bc_path))
    net = make_net(STATE_DIM, ACTION_DIM)
    st = bc.train_bc(net, frames, epochs=args.bc_epochs, lr=args.lr,
                     device=args.device, seed=args.seed)
    print(f"[bc] frames={st['frames']} loss_tail={st['loss'][-1]:.4f} "
          f"acc={st['final_acc']:.3f} ({time.time()-t0:.0f}s)")

    # ---- PPO 续训 + 周期评估 ----
    curve = {"bc_acc": st["final_acc"], "eval": [], "time_s": None}
    # 周期评估（BC 后基线 + PPO 每 eval-every）
    def _eval(tag):
        res = trainer.eval_winrate(da, db, net, n=args.games,
                                   opponent="baseline", device=args.device)
        curve["eval"].append({"tag": tag, **res})
        print(f"[eval:{tag}] vs baseline win_rate={res['win_rate']:.2f} "
              f"({res['wins']}/{res['n']})")

    _eval("bc-done")
    t1 = time.time()
    if args.train_mode == "vs-fixed":
        from engine.agents import BaselineAI as _BFixed
        ppo_st = trainer.train_ppo_vs_fixed(
            da, db, net, opponent=_BFixed("baseline"),
            episodes=args.ppo_episodes, ppo_epochs=args.ppo_epochs,
            clip=args.clip, lr=args.lr, gamma=args.gamma,
            seed=args.seed, device=args.device, save_path=args.save,
            log_every=max(1, args.ppo_episodes // 5))
    else:
        ppo_st = trainer.train_ppo(
            da, db, net, episodes=args.ppo_episodes,
            ppo_epochs=args.ppo_epochs, clip=args.clip, lr=args.lr,
            gamma=args.gamma, temperature=1.0, seed=args.seed,
            device=args.device, save_path=args.save,
            log_every=max(1, args.ppo_episodes // 6))
    print(f"[ppo:{args.train_mode}] {ppo_st['episodes']} episodes in "
          f"{time.time()-t1:.0f}s")
    _eval("ppo-done")
    curve["time_s"] = round(time.time() - t0, 1)
    Path(args.save).parent.mkdir(parents=True, exist_ok=True)
    Path("artifacts/curve.json").write_text(
        json.dumps(curve, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[done] weights={args.save} curve=artifacts/curve.json "
          f"total={curve['time_s']}s")


if __name__ == "__main__":
    main()
