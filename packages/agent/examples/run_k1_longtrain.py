# -*- coding: utf-8 -*-
"""PokéLab AI · K1 长训一体化脚本（供本机运行）

一条命令跑完整课程并给出 K1(≥70% vs 规则基线) 判定报告：
  阶段0 BC 预热（自动复用缓存）→ 阶段1 vs 规则基线(PPO)
  → 阶段2 冻结快照自对弈 → 阶段3 MCTS soft 蒸馏
  → 结束高置信评估(n=--final-games) vs 基线/随机 + 基线自对局参照 → K1_report.md

特性：设备默认 GPU(auto，显存不足回退 CPU)；周期权重落盘与进度 ETA；
     --from-weights <pt> 可跳过 BC 续跑（断点续训的近似入口）。

用法（Windows Git Bash / PowerShell 均可在 packages/agent 下）：
  conda activate pokemo
  python -u examples/run_k1_longtrain.py                    # 默认课程（约 1.5–3h）
  python -u examples/run_k1_longtrain.py --stage1-episodes 5000 \
      --stage2-episodes 3000 --stage3-episodes 100 --final-games 40   # 短试跑
产物：artifacts/k1_policy.pt（最终权重）· artifacts/K1_report.md（判定报告）

参数已按 README「冲 70% 手册」对齐（2026-09-09 调参）：
  ① 终评样本 ≥100（--final-games 默认 100；协议正式验收用 --final-games 200）
  ② 更长预算：stage1 40000 / stage2 24000 / stage3 400
  ③ lr 余弦衰减（--lr-schedule cosine，默认）+ entropy 0.05 + clip 0.3 + hidden 128
  ⑤ AlphaZero 式快照刷新（stage2-snapshot-every 2000）+ MCTS 教练 iters=10
  （特征扩展可选 --features full = 59 维，训练与评估须同配置）

预期总时长（RTX 4050 笔记本，引擎收集 CPU-bound，GPU 加速网络段）：
  stage1 40000 局 ≈ 25–55min · stage2 24000 局 ≈ 15–35min
  stage3 400 局（MCTS10 蒸馏，含大量引擎 rollout）≈ 40–90min
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
from agent.state_encoder import (EncoderConfig, ACTION_DIM, encoder_dim,
                                 set_active_encoder)
from agent.policy_net import make_net
from agent import bc, trainer, distill
from agent.utils import resolve_device


def main():
    ap = argparse.ArgumentParser(description="K1 长训一体化脚本")
    ap.add_argument("--decks", default="lightning")
    ap.add_argument("--bc-episodes", type=int, default=150)
    ap.add_argument("--bc-epochs", type=int, default=25)
    ap.add_argument("--stage1-episodes", type=int, default=40000)
    ap.add_argument("--stage2-episodes", type=int, default=24000)
    ap.add_argument("--stage2-snapshot-every", type=int, default=2000)
    ap.add_argument("--stage3-episodes", type=int, default=400)
    ap.add_argument("--stage3-mcts-iters", type=int, default=10)
    ap.add_argument("--stage3-bc-epochs", type=int, default=6)
    ap.add_argument("--ppo-epochs", type=int, default=3)
    ap.add_argument("--lr", type=float, default=3e-3)
    ap.add_argument("--gamma", type=float, default=0.99)
    ap.add_argument("--clip", type=float, default=0.3)
    ap.add_argument("--ent-coef", type=float, default=0.05)
    ap.add_argument("--lr-decay", type=float, default=0.1)
    ap.add_argument("--lr-schedule", choices=["linear", "cosine"],
                    default="cosine",
                    help="lr 调度：cosine（手册推荐）/ linear")
    ap.add_argument("--hidden", type=int, default=128)
    ap.add_argument("--reward-shaping", type=float, default=0.05)
    ap.add_argument("--features", choices=["legacy", "full"], default="legacy")
    ap.add_argument("--eval-every", type=int, default=5000)
    ap.add_argument("--games", type=int, default=30)
    ap.add_argument("--final-games", type=int, default=100,
                    help="终评样本量（手册≥100；正式验收协议请 --final-games 200）")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--device", default="auto")
    ap.add_argument("--save", default="artifacts/k1_policy.pt")
    ap.add_argument("--report", default="artifacts/K1_report.md")
    ap.add_argument("--from-weights", default=None,
                    help="从已有权重续跑（跳过 BC；stage1..3 按给定局数继续）")
    args = ap.parse_args()
    args.device = resolve_device(args.device)
    t_all = time.time()

    da = decklib.make_deck(args.decks)
    db = list(da)
    enc_cfg = EncoderConfig(features=("hand", "meta")) \
        if args.features == "full" else None
    set_active_encoder(enc_cfg)
    sdim = encoder_dim()
    print(f"[k1] decks={args.decks}(mirror) features={args.features} "
          f"state_dim={sdim} device={args.device} seed={args.seed}")
    Path(args.save).parent.mkdir(parents=True, exist_ok=True)

    net = make_net(sdim, ACTION_DIM, hidden=args.hidden, device=args.device)
    n_start = 1
    if args.from_weights:
        net.load_state_dict(torch_load(args.from_weights, args.device))
        print(f"[k1] 续跑：已加载 {args.from_weights}，跳过 BC")
    else:
        t0 = time.time()
        cache = (f"artifacts/bc_data_{args.decks}_mirror_"
                 f"{'full' if enc_cfg else 'base'}.pt")
        if Path(cache).exists() and enc_cfg is None and \
                not Path(f"artifacts/bc_data_{args.decks}_mirror.pt").exists():
            pass  # base 缓存缺失时下面 collect
        if Path(cache).exists():
            frames = bc.load_frames(cache)
            print(f"[bc] cache hit {cache} ({len(frames)} frames)")
        else:
            frames = bc.collect_demonstration(da, db, BaselineAI("bA"),
                                              BaselineAI("bB"),
                                              episodes=args.bc_episodes,
                                              seed=args.seed)
            bc.save_frames(frames, cache)
        st = bc.train_bc(net, frames, epochs=args.bc_epochs, lr=args.lr,
                         device=args.device, seed=args.seed)
        print(f"[bc] frames={st['frames']} acc={st['final_acc']:.3f} "
              f"({time.time()-t0:.0f}s)")
        n_start = 1

    curve = {"eval": [], "total_s": None, "passed_k1": None}

    def _save():
        import torch
        torch.save(net.state_dict(), args.save)

    def _eval(tag):
        r = trainer.eval_winrate(da, db, net, n=args.games,
                                 opponent="baseline", device=args.device)
        curve["eval"].append({"tag": tag, **r})
        _save()
        print(f"[k1:eval:{tag}] vs baseline win_rate={r['win_rate']:.3f} "
              f"({r['wins']}/{r['n']}) [{time.time()-t_all:.0f}s]")

    # ---- 阶段1：vs 规则基线（PPO）----
    if args.stage1_episodes > 0:
        t1 = time.time()
        st1 = trainer.train_ppo_vs_fixed(
            da, db, net, opponent=BaselineAI("baseline"),
            episodes=args.stage1_episodes, ppo_epochs=args.ppo_epochs,
            clip=args.clip, lr=args.lr, gamma=args.gamma,
            ent_coef=args.ent_coef, lr_decay=args.lr_decay,
            lr_schedule=args.lr_schedule,
            shaping=args.reward_shaping,
            eval_fn=lambda ep: _eval(f"stage1-{ep}"),
            eval_every=args.eval_every, seed=args.seed,
            device=args.device, save_path=args.save,
            log_every=max(1, args.stage1_episodes // 8))
        print(f"[k1] stage1 done {st1['episodes']} ep in "
              f"{time.time()-t1:.0f}s | wins={st1['wins']}"
              f"/{st1['episodes']} "
              f"({st1['wins']/max(1, st1['episodes']):.3f})")
    # ---- 阶段2：冻结快照自对弈 ----
    if args.stage2_episodes > 0:
        from agent.policy_agent import PolicyAgent
        seg = args.stage2_snapshot_every
        ran = 0
        while ran < args.stage2_episodes:
            n = min(seg, args.stage2_episodes - ran)
            t2 = time.time()
            frozen = trainer.clone_net(net, args.device)
            opp = PolicyAgent(frozen, device=args.device, name="frozen")
            trainer.train_ppo_vs_fixed(
                da, db, net, opponent=opp, episodes=n,
                ppo_epochs=args.ppo_epochs, clip=args.clip, lr=args.lr,
                gamma=args.gamma, ent_coef=args.ent_coef,
                lr_decay=args.lr_decay, lr_schedule=args.lr_schedule,
                shaping=args.reward_shaping,
                seed=args.seed, device=args.device, save_path=args.save,
                log_every=max(1, n // 4))
            ran += n
            _eval(f"stage2-{ran}")
            print(f"[k1] stage2 +{n} vs frozen in {time.time()-t2:.0f}s")
    # ---- 阶段3：MCTS soft 蒸馏 ----
    if args.stage3_episodes > 0 and args.stage3_mcts_iters > 0:
        seg = max(1, args.stage3_episodes // 4)
        ran3 = 0
        while ran3 < args.stage3_episodes:
            n = min(seg, args.stage3_episodes - ran3)
            t3 = time.time()
            frames = distill.collect_mcts_expert(
                da, db, opponent=BaselineAI("baseline"), episodes=n,
                iters=args.stage3_mcts_iters, seed=args.seed, soft=True)
            st3 = bc.train_bc_soft(net, frames,
                                   epochs=args.stage3_bc_epochs,
                                   lr=args.lr, device=args.device,
                                   seed=args.seed)
            ran3 += n
            _eval(f"stage3-{ran3}")
            print(f"[k1] stage3 +{n} mcts-soft demos in {time.time()-t3:.0f}s "
                  f"frames={st3['frames']} loss={st3['final_loss']:.4f}")
    _save()

    # ---- 结束高置信评估 + K1 判定 ----
    from engine.simulator import run_many
    ref = run_many(da, db, BaselineAI("bA"), BaselineAI("bB"),
                   n=args.final_games)
    ev_b = trainer.eval_winrate(da, db, net, n=args.final_games,
                                opponent="baseline", device=args.device)
    ev_r = trainer.eval_winrate(da, db, net,
                                n=max(20, args.final_games // 2),
                                opponent="random", device=args.device)
    ci_half = 1.96 * (ev_b["win_rate"] * (1 - ev_b["win_rate"])
                      / max(1, ev_b["n"])) ** 0.5
    passed = ev_b["win_rate"] >= 0.70
    curve["total_s"] = round(time.time() - t_all, 1)
    curve["passed_k1"] = passed
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# PokéLab AI · K1 长训报告", "",
        f"- 时间：{time.time():.0f} 生成 · 总耗时 {curve['total_s']}s · "
        f"device {args.device} · seed {args.seed}",
        f"- 配置：decks={args.decks}(镜像) features={args.features} "
        f"state_dim={sdim} · stage1={args.stage1_episodes} "
        f"stage2={args.stage2_episodes} stage3={args.stage3_episodes}"
        f"(mcts{args.stage3_mcts_iters}) · eval_every={args.eval_every}", "",
        "## 最终评估（高置信）", "",
        "| 对手 | n | 胜率 | 95%CI |",
        "|---|---|---|---|",
        f"| 规则基线 AI | {ev_b['n']} | {ev_b['win_rate']:.3f} | "
        f"±{ci_half:.3f} |",
        f"| 随机 AI | {ev_r['n']} | {ev_r['win_rate']:.3f} | — |",
        f"| 基线AI 自对局参照 | {ref['n']} | win_rate_a={ref['win_rate_a']:.3f} | — |",
        "", "## 训练过程 vs baseline", "",
        "| 阶段 | 胜率 |", "|---|---|"]
    for e in curve["eval"]:
        lines.append(f"| {e['tag']} | {e['win_rate']:.3f} "
                     f"({e['wins']}/{e['n']}) |")
    lines += ["", "## K1 判定（PRD：≥70%，200 局双盲口径为准）", ""]
    if passed:
        verdict = "PASS ✅（接近门槛，正式验收请按 200 局协议复核）"
    else:
        verdict = "未达 0.70 —— 继续长训或按 README「冲 70% 手册」调参"
    lines += [f"**结论：{verdict}**",
              f"- 本报告 n={ev_b['n']}（比协议 200 局小，95%CI ±{ci_half:.3f}）",
              f"- 权重：{args.save}", f"- 曲线：artifacts/curve.json 已由各阶段输出"]
    Path(args.report).write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[-7:]))


def torch_load(path, device):
    import torch
    return torch.load(path, map_location=device)


if __name__ == "__main__":
    main()
