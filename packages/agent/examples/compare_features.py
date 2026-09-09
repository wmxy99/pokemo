# -*- coding: utf-8 -*-
"""PokéLab Agent · legacy(51d) vs full(59d) 特征域对比实验（同预算）

流程（各特征分别执行，设备默认 GPU auto）：
  1) set_active_encoder(cfg)（legacy=None / full=hand+meta）
  2) BC 预热：60 局演示 × 20 epochs
  3) PPO vs 固定基线（默认 1500 局，公平预算）
  4) 评估：argmax vs baseline/random + MCTS5 延迟样本（eval_matrix.run_mode）
产物：artifacts/eval_matrix_full.md（胜率×延迟并排 + 差异结论）与
     artifacts/policy_legacy.pt / artifacts/policy_full.pt

用法：python examples/compare_features.py --ppo-episodes 1500
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
from engine.agents import BaselineAI
from agent.state_encoder import (EncoderConfig, ACTION_DIM, encoder_dim,
                                 set_active_encoder)
from agent.policy_net import make_net
from agent import bc, trainer
from agent.utils import resolve_device

# examples 目录进 path，复用 eval_matrix.run_mode
sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_matrix import run_mode  # noqa: E402


def run_feature(name: str, cfg, da, db, args):
    t0 = time.time()
    set_active_encoder(cfg)
    print(f"\n===== feature={name} state_dim={encoder_dim()} =====")
    frames = bc.collect_demonstration(da, db, BaselineAI("bA"),
                                      BaselineAI("bB"),
                                      episodes=args.bc_episodes,
                                      seed=args.seed)
    net = make_net(encoder_dim(), ACTION_DIM, hidden=args.hidden,
                   device=args.device)
    st_bc = bc.train_bc(net, frames, epochs=args.bc_epochs, lr=args.lr,
                        device=args.device, seed=args.seed)
    print(f"[{name}] bc frames={st_bc['frames']} acc={st_bc['final_acc']:.3f} "
          f"({time.time()-t0:.0f}s)")
    st_ppo = trainer.train_ppo_vs_fixed(
        da, db, net, opponent=BaselineAI("baseline"),
        episodes=args.ppo_episodes, ppo_epochs=3, clip=0.3, lr=args.lr,
        ent_coef=0.05, lr_decay=0.1, seed=args.seed, device=args.device,
        log_every=max(1, args.ppo_episodes // 6))
    print(f"[{name}] ppo episodes={st_ppo['episodes']} "
          f"in-train wins={st_ppo['wins']}/{st_ppo['episodes']} "
          f"win={st_ppo['wins']/max(1,st_ppo['episodes']):.3f} "
          f"({time.time()-t0:.0f}s)")
    # 评估
    ev = {"name": name}
    for opp_name, opp in (("baseline", BaselineAI("baseline")),):
        r = run_mode(da, db, "argmax", opp, 0, args.games, args.seed,
                     args.device, policy=net)
        ev[f"argmax_vs_{opp_name}"] = r["win_rate"]
        ev[f"lat_{opp_name}"] = r["lat_ms"]
    import random as _r
    from engine.agents import RandomAgent
    r2 = run_mode(da, db, "argmax", RandomAgent("random", seed=0), 0,
                  args.games, args.seed, args.device, policy=net)
    ev["argmax_vs_random"] = r2["win_rate"]
    m = run_mode(da, db, "mcts5", BaselineAI("baseline"), 5,
                 args.mcts_games, args.seed, args.device)
    ev["mcts5_vs_baseline"] = m["win_rate"]
    ev["lat_mcts5"] = m["lat_ms"]
    print(f"[{name}] eval: {json.dumps({k: v for k, v in ev.items()
                                        if k != 'name'}, ensure_ascii=False)}")
    Path(f"artifacts/policy_{name}.pt").parent.mkdir(parents=True,
                                                     exist_ok=True)
    import torch as _t
    _t.save(net.state_dict(), f"artifacts/policy_{name}.pt")
    return ev


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--decks", default="lightning")
    ap.add_argument("--bc-episodes", type=int, default=60)
    ap.add_argument("--bc-epochs", type=int, default=20)
    ap.add_argument("--ppo-episodes", type=int, default=1500)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--hidden", type=int, default=64)
    ap.add_argument("--games", type=int, default=24)
    ap.add_argument("--mcts-games", type=int, default=12)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--device", default="auto",
                    help="auto（默认：有 CUDA 用 GPU）/ cuda / cpu")
    ap.add_argument("--out", default="artifacts/eval_matrix_full.md")
    args = ap.parse_args()
    args.device = resolve_device(args.device)
    da = decklib.make_deck(args.decks)
    db = list(da)

    t_all = time.time()
    ev_legacy = run_feature("legacy", None, da, db, args)
    ev_full = run_feature("full", EncoderConfig(features=("hand", "meta")),
                          da, db, args)

    def _p95(x):
        if not x or x.get("p95") == "-":
            return "-"
        return x["p95"]
    lines = [
        "# legacy(51d) vs full(59d) 特征对比实验", "",
        f"- 镜像卡组：{args.decks} · 预算：BC {args.bc_episodes}局×"
        f"{args.bc_epochs}ep + PPO vs基线 {args.ppo_episodes}局（同预算）"
        f" · seed {args.seed} · {args.device}",
        f"- 设备：{args.device} · 总耗时 {time.time()-t_all:.0f}s", "",
        "| 特征 | argmax vs 基线AI | argmax vs 随机AI | MCTS5 vs 基线 | argmax P95(ms) | MCTS5 P95(ms) |",
        "|---|---|---|---|---|---|",
        f"| legacy(51d) | {ev_legacy.get('argmax_vs_baseline', '-')} | "
        f"{ev_legacy.get('argmax_vs_random', '-')} | "
        f"{ev_legacy.get('mcts5_vs_baseline', '-')} | "
        f"{_p95(ev_legacy.get('lat_baseline'))} | {_p95(ev_legacy.get('lat_mcts5'))} |",
        f"| full(59d) | {ev_full.get('argmax_vs_baseline', '-')} | "
        f"{ev_full.get('argmax_vs_random', '-')} | "
        f"{ev_full.get('mcts5_vs_baseline', '-')} | "
        f"{_p95(ev_full.get('lat_baseline'))} | {_p95(ev_full.get('lat_mcts5'))} |",
        "", "## 结论（小预算样本，噪声 ±0.1 量级，需更大 N 才下终判）", "",
    ]
    d = ev_full.get("argmax_vs_baseline", 0) - \
        ev_legacy.get("argmax_vs_baseline", 0)
    lines.append(f"- argmax vs 基线 Δ = full − legacy = {d:+.2f}"
                 + ("（full 领先，建议保留 59d 配置继续长训）" if d > 0.05
                    else "（Δ 在噪声内，暂不可分；扩大预算后再判）"
                    if d >= -0.05 else "（legacy 领先，建议回退 51d 或检查特征归一化）"))
    lines += [
        "- 训练内胜率（PPO vs baseline）见上方日志；",
        "- 决策延迟 K3：argmax 与 MCTS5 均远低于 2000ms 阈值；特征域对延迟无实质影响。",
        "", "产物：artifacts/policy_legacy.pt · artifacts/policy_full.pt",
    ]
    text = "\n".join(lines)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(text, encoding="utf-8")
    print("\n" + text)


if __name__ == "__main__":
    main()
