# -*- coding: utf-8 -*-
"""K1 · **迁移性验证**：rich 特征带来的增益是"整体更强"还是"只会打基线"？

单靠"对规则基线 0.790"无法区分这两种情况，所以本脚本给三组独立证据：

1. **策略对策略（head-to-head，双盲）**：rich vs basic（**同 seed 训练的唯一变量对照**）
   与 rich vs 上一代最佳 —— 双方都是学习出来的策略，直接比强弱。
2. **换对手**：`support`（会用训练家/特性的强化规则 AI，与 baseline 决策逻辑不同）、
   `random`（弱对照）。**注意**：K1 的参照始终是 `BaselineAI`（口径冻结），
   这里只是为了看泛化。
3. **同 seed 对照**：每个对手都同时评 rich 与 basic，避免"换个对手数字变了"被误读成
   策略强弱变化。

用法::

    python examples/k1_migrate.py --games 200            # 全部评估
    python examples/k1_migrate.py --weights artifacts/k1_rich.pt --games 100
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_PKG = Path(__file__).resolve().parents[2]          # …/packages
for _sub in ("engine", "agent"):
    sys.path.insert(0, str(_PKG / _sub))

from engine import decklib                                    # noqa: E402
from agent import trainer                                     # noqa: E402
from agent.trainer import load_net                            # noqa: E402

# 默认候选：rich（本轮）与 basic（同 seed 对照）+ 上一代最佳
DEFAULT = {
    "rich": "artifacts/k1_rich.pt",
    "basic(同seed对照)": "artifacts/k1_basic707.pt",
    "cont40k(上代最佳)": "artifacts/k1_cont40k.pt",
}


def _ci(p: float, n: int) -> float:
    return 1.96 * (p * (1 - p) / max(1, n)) ** 0.5


def main() -> int:
    ap = argparse.ArgumentParser(description="K1 rich 特征迁移性验证")
    ap.add_argument("--games", type=int, default=200)
    ap.add_argument("--decks", default="lightning")
    ap.add_argument("--seed0", type=int, default=0)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--report", default="artifacts/k1_migrate.md")
    a = ap.parse_args()

    da = decklib.make_deck(a.decks)
    db = list(da)
    nets, rows = {}, []
    for label, path in DEFAULT.items():
        if not Path(path).exists():
            print(f"  [skip] {label}: {path} 不存在")
            continue
        nets[label] = load_net(path, device=a.device)
        print(f"  [load] {label} ← {path}")

    def run(name, fn):
        t0 = time.time()
        r = fn()
        r["case"] = name
        r["secs"] = round(time.time() - t0, 1)
        rows.append(r)
        print(f"  {name:<34} {r['win_rate']:.3f} "
              f"({r['wins']}/{r['n']}) ±{_ci(r['win_rate'], r['n']):.3f} "
              f"[{r['secs']}s]", flush=True)

    print(f"\n=== ① 策略对策略（双盲，n={a.games}）===")
    rich = nets.get("rich")
    for label, other in nets.items():
        if label == "rich":
            continue
        run(f"rich vs {label}", lambda o=other: trainer.eval_h2h_blind(
            da, db, rich, o, n=a.games, device=a.device, seed0=a.seed0))

    print(f"\n=== ② 换对手（双盲，n={a.games}）===")
    for opp in ("baseline", "support", "random"):
        for label, net in nets.items():
            run(f"{label} vs {opp}", lambda n_=net, o=opp:
                trainer.eval_winrate_blind(da, db, n_, n=a.games,
                                           opponent=o, device=a.device,
                                           seed0=a.seed0))

    best = max((r for r in rows if r["case"].startswith("rich")),
               key=lambda x: 0, default=None)
    L = ["# K1 · rich 特征迁移性验证", "",
         f"- 生成：{time.strftime('%Y-%m-%d %H:%M:%S')}",
         f"- 口径：**双盲 {a.games} 局**（交替先后手）· 镜像卡组 `{a.decks}` · "
         f"seed0={a.seed0} · device={a.device}", "",
         "> 目的：区分「**整体更强**」与「**只会打基线**」。",
         "> K1 的参照对手始终是 `BaselineAI`（口径冻结）；本表的 support/random 与",
         "> head-to-head 都只是泛化性证据，**不替代 K1 判定**。", "",
         "| 用例 | 胜率 | n | 95%CI | 耗时 |",
         "|---|---:|---:|---|---:|"]
    for r in rows:
        L.append(f"| {r['case']} | **{r['win_rate']:.3f}** | {r['n']} | "
                 f"±{_ci(r['win_rate'], r['n']):.3f} | {r['secs']}s |")
    L += ["", "## 读表", "",
          "- **rich vs basic(同seed对照)** 是唯一变量对照（只差动作特征）：",
          "  显著 >0.5 说明 rich 是**整体更强**，而不只是换了个对手偏好。",
          "- 每组都同时评 rich 与 basic → 跨对手的差异不会被误读成策略强弱变化。",
          "- head-to-head 双方都是学习策略，其双盲值同样**不受先手优势污染**。"]
    Path(a.report).parent.mkdir(parents=True, exist_ok=True)
    Path(a.report).write_text("\n".join(L) + "\n", encoding="utf-8")
    Path(a.report).with_suffix(".json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n[k1-migrate] 报告 → {a.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
