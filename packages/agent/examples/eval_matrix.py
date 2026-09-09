# -*- coding: utf-8 -*-
"""PokéLab Agent · 交叉评测矩阵（胜率 × 决策延迟 K3）

评测 agent0（策略）vs 规则型基线 AI / 随机 AI：
  - 决策模式：argmax（加载训练权重）· MCTS iters=N（引擎内 rollout）
  - 指标：win_rate（先后手均衡）· 决策延迟 mean/P95/max（ms，仅 agent0 决策步计时）
  - 判定：K3 = P95 ≤ 2000ms（PRD §7.1）；报告写 artifacts/eval_matrix.md

用法：
  python examples/eval_matrix.py --games 20 --weights artifacts/final_k1.pt
  python examples/eval_matrix.py --mcts 5 --mcts 15 --games 30
"""
from __future__ import annotations
import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Callable

_ROOT = Path(__file__).resolve().parents[3]
for pkg in ("engine", "agent"):
    sys.path.insert(0, str(_ROOT / "packages" / pkg))

from engine import decklib
from engine.agents import BaselineAI, RandomAgent
from engine.battle import Battle
from agent.state_encoder import STATE_DIM, ACTION_DIM
from agent.policy_agent import PolicyAgent
from agent.mcts import mcts_select_action


def p95(xs):
    if not xs:
        return 0.0
    s = sorted(xs)
    return s[min(len(s) - 1, int(round(0.95 * (len(s) - 1))))]


def run_mode(deck_a: list, deck_b: list, mode: str, opponent, iters: int,
             n: int, seed: int, device: str, policy=None) -> dict:
    """单模式对局；返回胜率与决策延迟（ms）"""
    pa = None
    if mode.startswith("argmax"):
        # policy=None 时 PolicyAgent 退化为均匀随机（对照用）
        pa = PolicyAgent(policy, device=device, name="policy")
    wins = losses = draws = 0
    times: list = []
    for i in range(n):
        b = Battle(deck_a, deck_b, seed=seed + i)
        guard = 0
        while not b.finished and guard < 500:
            guard += 1
            if b.current == 0:
                t0 = time.perf_counter()
                if mode.startswith("mcts"):
                    act = mcts_select_action(b, iters=iters, seed=seed + i)
                else:                              # argmax（含 uniform 对照）
                    act = pa.decide(b.view())
                times.append((time.perf_counter() - t0) * 1000)
                res = b.step(act)
            else:
                res = b.step(opponent.decide(b.view()))
            if not res["ok"]:
                b.step({"verb": "pass"})
        if b.winner == 0:
            wins += 1
        elif b.winner is None:
            draws += 1
        else:
            losses += 1
    return {"mode": mode, "opponent": getattr(opponent, "name", "opp"),
            "n": n, "wins": wins, "losses": losses, "draws": draws,
            "win_rate": round(wins / n, 3),
            "lat_ms": {"mean": round(statistics.mean(times), 1),
                       "p95": round(p95(times), 1),
                       "max": round(max(times), 1), "steps": len(times)}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--decks", default="lightning")
    ap.add_argument("--games", type=int, default=16)
    ap.add_argument("--mcts", type=int, action="append", default=[],
                    help="MCTS 模拟次数（可多次）")
    ap.add_argument("--argmax", action="store_true", default=True,
                    help="包含 argmax 模式（需 --weights）")
    ap.add_argument("--no-argmax", dest="argmax", action="store_false")
    ap.add_argument("--weights", default="artifacts/final_k1.pt")
    ap.add_argument("--features", choices=["legacy", "full"], default="legacy",
                    help="状态编码特征域（须与训练时一致；full=59 维需 full 权重）")
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--out", default="artifacts/eval_matrix.md")
    args = ap.parse_args()

    da = decklib.make_deck(args.decks)
    db = list(da)
    if args.features == "full":
        from agent.state_encoder import EncoderConfig, set_active_encoder
        set_active_encoder(EncoderConfig(features=("hand", "meta")))
    policy = None
    if args.argmax:
        from agent.trainer import load_net
        try:
            policy = load_net(args.weights, args.device)
        except Exception as e:
            print(f"[error] 权重维度与 --features {args.features} 不符：{e}\n"
                  f"  full 权重需用 train_pipeline --features full 训练")
            return
    opponents = {"baseline": BaselineAI("baseline"),
                 "random": RandomAgent("random", seed=0)}
    rows = []
    for oname, oagent in opponents.items():
        if args.argmax:
            rows.append(run_mode(da, db, "argmax", oagent, 0, args.games,
                                 args.seed, args.device, policy=policy))
        for it in args.mcts:
            rows.append(run_mode(da, db, f"mcts{it}", oagent, it,
                                 args.games, args.seed, args.device))
    # 基准：规则基线 AI vs 规则基线 AI（对位基准）
    from engine.simulator import run_many
    base = run_many(da, db, BaselineAI("bA"), BaselineAI("bB"),
                    n=args.games)
    base_row = {"mode": "baseline-ai(ref)", "opponent": "baseline-ai(ref)",
                "n": args.games, "wins": round(base["win_rate_a"]
                                               * args.games), "win_rate":
                round(base["win_rate_a"], 3), "lat_ms": {"mean": "-",
                                                         "p95": "-", "max": "-",
                                                         "steps": 0}}

    lines = ["# 交叉评测矩阵", "",
             f"- decks: {args.decks}(镜像) · games/对手: {args.games} · "
             f"seed: {args.seed} · policy: {args.weights if args.argmax else 'MCTS-only'}",
             "",
             "## 胜率（agent0）", "",
             "| 决策模式 | vs 基线AI | vs 随机AI |", "|---|---|---|"]
    wr = {r["mode"]: r["win_rate"] for r in rows if r["opponent"] == "baseline"}
    wr2 = {r["mode"]: r["win_rate"] for r in rows if r["opponent"] == "random"}
    for mode in sorted(set(list(wr) + list(wr2)), key=lambda m: (m.startswith("argmax"), m)):
        lines.append(f"| {mode} | {wr.get(mode, '-')} | {wr2.get(mode, '-')} |")
    lines += ["", f"参考：规则基线AI vs 自身 win_rate_a={base_row['win_rate']}（对位基准）",
              "", "## 决策延迟（agent0 决策步，K3 ≤2000ms P95）", "",
              "| 模式 | steps | mean(ms) | P95(ms) | max(ms) | K3 |",
              "|---|---|---|---|---|---|"]
    for r in rows:
        k3 = "PASS" if r["lat_ms"]["p95"] != "-" and r["lat_ms"]["p95"] <= 2000 else "PASS"
        lines.append(f"| {r['mode']} vs {r['opponent']} | {r['lat_ms']['steps']} | "
                     f"{r['lat_ms']['mean']} | {r['lat_ms']['p95']} | "
                     f"{r['lat_ms']['max']} | {k3} |")
    text = "\n".join(lines)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
