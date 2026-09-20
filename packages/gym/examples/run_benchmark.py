# -*- coding: utf-8 -*-
"""内置对手两两 benchmark：输出胜率矩阵 + 先手胜率。

运行：cd packages/gym && python examples/run_benchmark.py [局数]

依赖：仅 engine + 本包；结果自 `engine.simulator.run_many`（对局确定性）。
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
for pkg in (REPO / "packages" / "engine", REPO / "packages" / "gym"):
    if str(pkg) not in sys.path:
        sys.path.insert(0, str(pkg))

from engine import decklib                     # noqa: E402
from engine.simulator import run_many          # noqa: E402
from pokegym import make_agent, AGENT_NAMES    # noqa: E402


def _fmt_rate(w, n):
    return f"{w / n:.3f}" if n else "n/a"


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    deck_a = decklib.make_deck("lightning")
    deck_b = decklib.make_deck("fire")
    names = list(AGENT_NAMES)

    print(f"对手 benchmark（各 {n} 局 · lightning vs fire · 固定种子段）\n")
    header = "       " + "".join(f"{nm:>14}" for nm in names)
    print(header)
    for i, a in enumerate(names):
        row = f"{a:<7}"
        for j, b in enumerate(names):
            if i == j:
                row += f"{'—':>14}"
                continue
            r = run_many(deck_a, deck_b, make_agent(a), make_agent(b),
                         n=n, seeds=range(10, 10 + n))
            # 行 = 我方 a（座位 0），列 = 对手 b（座位 1）
            row += f"{_fmt_rate(r['wins_a'], n):>14}"
        print(row)

    # 先手优势参照线（镜像卡组自对局）
    r = run_many(deck_a, deck_a, make_agent("baseline"),
                 make_agent("baseline"), n=n, seeds=range(10, 10 + n))
    print(f"\n基线自对局（同卡组）先手胜率：{_fmt_rate(r['wins_a'], n)}"
          f"（参照：镜像卡组下先手优势约 0.14）")


if __name__ == "__main__":
    main()
