# -*- coding: utf-8 -*-
"""PokéLab Agent · 价值头**胜率标定**（K1 攻坚 · 让 MCTS 可用）

## 为什么需要它

实测 MCTS 用价值头做叶评估时**迭代越多越差**（iters=100 → 0.650，
iters=300 → 0.525，均低于网络单独 0.685）。原因是 `mcts._value_of` 用
`(v + 1) / 2` 直接把价值头输出当胜率，而这隐含两个**未经检验的假设**：

1. 价值头的输出**量纲**就是 [-1, 1]（等价于"胜率区间"）—— 但网络里价值头是
   **线性层无 tanh**，输出可能整体偏大/偏小甚至越界；
2. 映射是**线性**的（v=0 → 胜率 0.5）—— 但训练目标是折扣回报（gamma<1），
   与"最终胜负概率"并不等价。

只有先**测出**这两条是否成立，才知道 MCTS 该不该用价值头。

## 输出

- 校准参数（Platt scaling：`p = sigmoid(a·v + b)`）与标定质量指标
  （Brier / LogLoss / ECE / AUC，**标定前 vs 标定后**）
- 可靠性表（按预测值分箱 → 实际胜率）→ 一眼看出"乐观/悲观/无信号"
- 两类样本分别拟合：
  · `own`：轮到我方决策的状态（价值头的同侧样本）
  · `leaf`：**我方走一步之后**的状态（MCTS 叶评估真正查询的分布）

## 用法

    python examples/calibrate_value.py artifacts/k1_polish_1e-4.pt --games 150
    # → artifacts/value_calib.json（含 a/b 与指标） + artifacts/value_calib.md

随后在 MCTS 里传 `value_calib=(a, b)` 即可用标定后的价值头。
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import random
import sys
import time
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parents[2]      # …/packages
for pkg in ("engine", "agent"):
    sys.path.insert(0, str(_PKG_ROOT / pkg))

from engine import decklib                                    # noqa: E402
from engine.agents import BaselineAI, RandomAgent             # noqa: E402
from agent.trainer import load_net                            # noqa: E402
from agent.value_calib import fit_platt, calibration_metrics  # noqa: E402
from agent.policy_agent import PolicyAgent                    # noqa: E402
from agent.paths import portable_path                         # noqa: E402


def _make_opp(name: str, seed: int = 0):
    if name == "baseline":
        return BaselineAI("baseline")
    if name == "random":
        return RandomAgent("random", seed=seed)
    raise SystemExit(f"未知对手：{name}")


def collect(net, deck_a, deck_b, games: int = 150, opponent: str = "baseline",
            seed0: int = 0, max_actions: int = 4, device: str = "cpu",
            sample: bool = False) -> dict:
    """采集 (v_actor, actor 视角胜负) 样本。

    **关键正确性**：价值头吃的是「**行动方视角**」的状态向量，所以它的输出
    恒表示"**该状态行动方**的胜负预期"。因此标签必须按**该状态的行动方**回填，
    而不是"我方" —— 否则一旦行动方是对手，样本就被贴上反向标签。
    （首版就踩了这个坑：`leaf` 组 AUC 被压到 0.66，而它其实含大量
    「我方回合内的后续状态」，行动方仍是我方。）

    分组：
      · `own`      —— 我方决策点（行动方=我方）
      · `leaf_opp` —— 我方走一步后、**行动方是对手**（MCTS 取反后的叶分布）
      · `leaf_same`—— 我方走一步后、行动方**仍是**我方（回合内状态）
    """
    import torch
    from agent.state_encoder import encode_state
    from engine.battle import Battle

    rec = []            # (组名, v_actor, actor, game_idx)
    games_out = []      # 每局 (me, winner)
    pa = PolicyAgent(net, device=device, name="policy", sample=sample)
    rng = random.Random(seed0 + 999)

    def _v(batt):
        with torch.no_grad():
            return float(net.evaluate_state(torch.tensor(
                [encode_state(batt.view())], dtype=torch.float32))[0])

    for i in range(games):
        seed = seed0 + i
        me = 0 if i % 2 == 0 else 1          # 交替座位（与 K1 双盲口径一致）
        b = Battle(deck_a, deck_b, seed=seed)
        opp = _make_opp(opponent, seed=seed)
        guard = 0
        while not b.finished and guard < 500:
            guard += 1
            if b.current == me:
                rec.append(("own", _v(b), b.current, i))
                # 我方走一步之后 → MCTS 叶评估真正查询的状态
                legal = b.legal_actions()
                picks = list(range(len(legal)))
                if len(picks) > max_actions:
                    picks = rng.sample(picks, max_actions)
                for p in picks:
                    b2 = copy.deepcopy(b)
                    b2.rng = random.Random(rng.randrange(1 << 30))
                    r = b2.step(dict(legal[p]))
                    if not r["ok"]:
                        b2.step({"verb": "pass"})
                    if b2.finished:
                        continue
                    grp = "leaf_same" if b2.current == me else "leaf_opp"
                    rec.append((grp, _v(b2), b2.current, i))
                act = pa.decide(b.view())
            else:
                act = opp.decide(b.view())
            res = b.step(dict(act))
            if not res["ok"]:
                b.step({"verb": "pass"})
        games_out.append((me, b.winner))

    out = {"own": ([], []), "leaf_opp": ([], []), "leaf_same": ([], [])}
    for grp, v, actor, gi in rec:
        me, winner = games_out[gi]
        y = 0.5 if winner is None else (1.0 if winner == actor else 0.0)
        out[grp][0].append(v)
        out[grp][1].append(y)
    out["_actor_is_opponent_share"] = sum(
        1 for g, _, a, gi in rec if a != games_out[gi][0]) / max(1, len(rec))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("weights", help="策略权重 .pt")
    ap.add_argument("--games", type=int, default=150)
    ap.add_argument("--opponent", default="baseline",
                    choices=["baseline", "random"])
    ap.add_argument("--decks", default="lightning")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--max-actions", type=int, default=4,
                    help="每个决策点模拟的动作数（MCTS 叶样本）")
    ap.add_argument("--out", default="artifacts/value_calib.json")
    ap.add_argument("--report", default="artifacts/value_calib.md")
    a = ap.parse_args()

    da = decklib.make_deck(a.decks)
    db = list(da)
    net = load_net(a.weights, device=a.device)
    print(f"[calib] 权重 {a.weights} · games={a.games} · opponent={a.opponent}")

    t0 = time.time()
    data = collect(net, da, db, games=a.games, opponent=a.opponent,
                   seed0=a.seed, max_actions=a.max_actions, device=a.device)
    print(f"[calib] 采样完成 {time.time()-t0:.0f}s")

    out = {"weights": portable_path(a.weights), "games": a.games, "opponent": a.opponent,
           "seed": a.seed, "groups": {},
           "actor_is_opponent_share": round(data.get("_actor_is_opponent_share", 0), 3)}
    # 汇总组（MCTS 标定要用的就是它）：所有**行动方视角**样本
    allv = [v for g in ("own", "leaf_opp", "leaf_same") for v in data[g][0]]
    ally = [y for g in ("own", "leaf_opp", "leaf_same") for y in data[g][1]]
    data["pooled"] = (allv, ally)
    L = ["# 价值头胜率标定（K1 攻坚）", "",
         f"- 权重：`{portable_path(a.weights)}` · games={a.games} · "
         f"opponent={a.opponent} · seed={a.seed}",
         f"- 生成：{time.strftime('%Y-%m-%d %H:%M:%S')}", "",
         "> 目的：`mcts._value_of` 用 `(v+1)/2` 把价值头输出当胜率，"
         "隐含「量纲=胜率区间」与「线性映射」两个**未检验假设**。",
         "> 本报告给出标定前/后的 Brier · LogLoss · AUC · ECE 对照。",
         "",
         "**口径说明**：价值头吃的是「**行动方视角**」的状态向量，故所有样本的标签"
         "按**该状态的行动方**回填（不是按我方）—— 否则行动方为对手的样本会被贴反向标签。", ""]

    LABEL = {"own": "我方决策点（行动方=我方）",
             "leaf_opp": "我方走一步后 · 行动方=对手（MCTS 取反叶）",
             "leaf_same": "我方走一步后 · 行动方仍是**我方**（回合内）",
             "pooled": "**汇总（行动方视角全部样本）← MCTS 标定用这组**"}
    for gname in ("own", "leaf_opp", "leaf_same", "pooled"):
        vs, ys = data[gname]
        if not vs:
            continue
        a_, b_ = fit_platt(vs, ys)
        m = calibration_metrics(vs, ys, a_, b_)
        vs_sorted = sorted(vs)
        stat = {"n": len(vs),
                "v_min": round(vs_sorted[0], 4), "v_max": round(vs_sorted[-1], 4),
                "v_mean": round(sum(vs) / len(vs), 4),
                "v_out_of_range": sum(1 for v in vs if not -1.0 <= v <= 1.0),
                "y_mean": round(sum(ys) / len(ys), 4),
                "platt_a": round(a_, 4), "platt_b": round(b_, 4)}
        out["groups"][gname] = {"stat": stat, "metrics": m}
        L += [f"## {LABEL[gname]}  ·  n={stat['n']}", "",
              f"- 价值头原始输出：min {stat['v_min']} / max {stat['v_max']} / "
              f"mean {stat['v_mean']} · **越界(|v|>1) {stat['v_out_of_range']} 个**",
              f"- 实际胜率均值 **{stat['y_mean']}**（与价值头均值对比 → 看系统性乐观/悲观）",
              f"- 拟合 Platt：`p = sigmoid({a_}·v + {b_})`", "",
              "| 映射 | Brier↓ | LogLoss↓ | AUC↑ | ECE↓ |",
              "|---|---:|---:|---:|---:|"]
        for key in ("raw", "platt"):
            s = m[key]
            if s:
                L.append(f"| {s['label']} | {s['brier']} | {s['logloss']} | "
                         f"{s['auc']} | {s['ece']} |")
        L += ["", "可靠性表（标定前 raw 映射）：", "",
              "| 预测区间 | n | 平均预测 | 实际胜率 | 差(实际-预测) |",
              "|---|---:|---:|---:|---:|"]
        for bn in (m["raw"].get("bins") or []):
            L.append(f"| {bn['bucket']} | {bn['n']} | {bn['pred']} | "
                     f"{bn['actual']} | {bn['gap']:+} |")
        L += [""]
    L += [f"> 全样本中「行动方=对手」的比例：**{out['actor_is_opponent_share']:.1%}**"
          f"（说明 MCTS 的取反分支有多常用）", ""]

    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    Path(a.report).write_text("\n".join(L), encoding="utf-8")
    print(f"[calib] -> {a.out}\n[calib] -> {a.report}")
    for gname, g in out["groups"].items():
        st, mm = g["stat"], g["metrics"]
        print(f"  [{gname}] n={st['n']} v∈[{st['v_min']},{st['v_max']}] "
              f"越界 {st['v_out_of_range']} · AUC_raw={mm['raw'].get('auc')} "
              f"→ AUC_cal={mm['platt'].get('auc')} · "
              f"Brier {mm['raw'].get('brier')} → {mm['platt'].get('brier')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
