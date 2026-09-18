#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""K1 验收：对规则基线的**双盲 200 局**胜率（PRD 口径）

为什么单独一个脚本：K1 是**验收指标**，必须与训练脚本解耦、可重复、口径固定。
本引擎先手优势显著（基线自对局先手胜率约 0.56–0.61），因此：

- **双盲**：一半局数作先手、一半作后手 → 先手优势被对称抵消，得真实相对强度
- 报告同时给出**单侧口径**（历史上用过的口径）与**基线自对局参照**，便于对照，
  但 **K1 判定只看双盲值**（≥0.70）

用法::

    python examples/k1_accept.py                              # 默认 artifacts/final_long.pt
    python examples/k1_accept.py artifacts/a.pt artifacts/b.pt  # 多权重对比
    python examples/k1_accept.py --games 200 --decks lightning
    python examples/k1_accept.py --report artifacts/K1_accept.md

退出码：0 = 达标（双盲 ≥0.70） / 1 = 未达 / 2 = 权重缺失。
"""
from __future__ import annotations
import argparse
import json
import sys
import time
from pathlib import Path

_ENGINE = Path(__file__).resolve().parents[2] / "engine"
sys.path.insert(0, str(_ENGINE))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import decklib                                   # noqa: E402
from agent import trainer                                    # noqa: E402
from agent.trainer import load_net                           # noqa: E402

TARGET = 0.70


def _ci(p: float, n: int) -> float:
    return 1.96 * (p * (1 - p) / max(1, n)) ** 0.5


def main() -> int:
    ap = argparse.ArgumentParser(description="K1 双盲验收（对规则基线）")
    ap.add_argument("weights", nargs="*", default=["artifacts/final_long.pt"],
                    help="一个或多个权重路径")
    ap.add_argument("--games", type=int, default=200,
                    help="双盲局数（PRD 协议 = 200）")
    ap.add_argument("--decks", default="lightning", help="镜像卡组")
    ap.add_argument("--seed0", type=int, default=0, help="起始 seed（可复现）")
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--report", default=None, help="写 Markdown 报告")
    ap.add_argument("--mcts-iters", type=int, default=0,
                    help=">0 时用 MCTS 搜索决策（**而不是**网络 argmax）→ 评估搜索后的强度")
    ap.add_argument("--max-depth", type=int, default=1,
                    help="MCTS 搜索深度（ply）；1 = 历史行为（只给动作后局面估值）")
    ap.add_argument("--mcts-calib", default=None,
                    help="价值头胜率标定 JSON（examples/calibrate_value.py 产出）")
    ap.add_argument("--c-puct", type=float, default=1.4)
    a = ap.parse_args()

    da = decklib.make_deck(a.decks)
    db = list(da)
    calib = None
    if a.mcts_iters and a.mcts_calib:
        from agent.mcts import load_value_calib
        calib = load_value_calib(a.mcts_calib)
        if calib is None:
            print(f"[k1-accept] 标定文件无效：{a.mcts_calib}", file=sys.stderr)
            return 2
        print(f"[k1-accept] 价值头标定 (a,b) = {calib}")
    rows = []
    ref = None
    for w in a.weights:
        if not Path(w).exists():
            print(f"[k1-accept] 权重不存在：{w}", file=sys.stderr)
            return 2
        net = load_net(w, device=a.device)
        t0 = time.time()
        if a.mcts_iters:
            r = trainer.eval_winrate_blind_mcts(
                da, db, net, n=a.games, iters=a.mcts_iters,
                value_calib=calib, c_puct=a.c_puct, max_depth=a.max_depth,
                opponent="baseline", device=a.device, seed0=a.seed0)
            r["one_sided"] = float("nan")
        else:
            r = trainer.eval_winrate_blind(da, db, net, n=a.games,
                                           opponent="baseline",
                                           device=a.device, seed0=a.seed0)
            one = trainer.eval_winrate(da, db, net, n=a.games,
                                       opponent="baseline", device=a.device)
            r["one_sided"] = one["win_rate"]
        r["secs"] = round(time.time() - t0, 1)
        r["weights"] = w
        rows.append(r)
        if ref is None:
            ref = r["ref_first_winrate"]
        cfg = (f"MCTS{a.mcts_iters}×d{a.max_depth}"
               if a.mcts_iters else "网络 argmax")
        print(f"[k1-accept] {w} [{cfg}]: 双盲 {r['win_rate']:.3f} "
              f"({r['wins']}/{r['n']}) ±{_ci(r['win_rate'], r['n']):.3f} | "
              f"{r['secs']}s")

    best = max(rows, key=lambda x: x["win_rate"])
    passed = best["win_rate"] >= TARGET

    L = ["# PokéLab AI · K1 验收报告（对规则基线 · 双盲协议）", "",
         f"- 生成：{time.strftime('%Y-%m-%d %H:%M:%S')}",
         f"- 口径：**双盲 {a.games} 局**（交替先后手）· 镜像卡组 `{a.decks}` · "
         f"seed0={a.seed0} · device={a.device}",
         f"- 决策配置：**{'MCTS iters=%d × depth=%d（价值头标定 %s）' % (a.mcts_iters, a.max_depth, a.mcts_calib or '未用') if a.mcts_iters else '网络 argmax（无搜索）'}**",
         f"- KPI：**K1 对规则基线胜率 ≥ {TARGET:.2f}**（PRD）", "",
         "## 结果", "",
         "| 权重 | 决策 | 双盲胜率 | n | 95%CI | 单侧胜率（对照） | 耗时 |",
         "|---|---|---:|---:|---|---:|---:|"]
    import math as _m
    for r in rows:
        mark = " ✅" if r["win_rate"] >= TARGET else ""
        cfg = (f"MCTS{a.mcts_iters}×d{a.max_depth}"
               if a.mcts_iters else "argmax")
        one = ("—" if not _m.isfinite(r["one_sided"])
               else f"{r['one_sided']:.3f}")
        L.append(f"| `{r['weights']}` | {cfg} | **{r['win_rate']:.3f}**{mark} | "
                 f"{r['n']} | ±{_ci(r['win_rate'], r['n']):.3f} | {one} | "
                 f"{r['secs']}s |")
    L += ["",
          f"- **基线自对局先手参照**：{ref:.3f}"
          f"（说明该引擎先手优势约 {abs(ref - 0.5) * 100:.0f}pp；"
          f"单侧口径会把这份优势算进策略战绩，故不能用于 K1 判定）",
          f"- 混合座位构成：{rows[0]['as_first']} 局先手 + {rows[0]['as_second']} 局后手", "",
          "## K1 判定", "",
          f"**结论：{'PASS ✅' if passed else '未达 ❌'}** —— "
          f"最佳双盲胜率 {best['win_rate']:.3f}（{best['weights']}）",
          f"- 判据：双盲胜率 ≥ {TARGET:.2f}（PRD「200 局双盲口径」）"
          + ("；已满足" if passed else f"；差 {TARGET - best['win_rate']:.3f}")]
    txt = "\n".join(L) + "\n"
    if a.report:
        Path(a.report).parent.mkdir(parents=True, exist_ok=True)
        Path(a.report).write_text(txt, encoding="utf-8")
        Path(a.report).with_suffix(".json").write_text(
            json.dumps({"rows": rows, "ref_first_winrate": ref,
                        "target": TARGET, "passed": passed},
                       ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[k1-accept] 报告 → {a.report}")
    print()
    print(txt)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
