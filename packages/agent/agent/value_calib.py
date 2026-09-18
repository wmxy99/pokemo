# -*- coding: utf-8 -*-
"""PokéLab Agent · 价值头**胜率标定**（Platt scaling）

## 为什么需要

`mcts._value_of` 默认用 `(v + 1) / 2` 把价值头输出当胜率，这隐含两个
**未经检验的假设**：

1. 输出量纲即"胜率区间" —— 网络里价值头是**线性层（无 tanh）**；
2. 映射是线性的（v=0 → 0.5）—— 训练目标是折扣回报，与"最终胜负概率"不等价。

实测这两条都不严格成立：价值头均值 **0.29** 而实际胜率 **0.52**
（**系统性乐观约 12pp**），且 MCTS 迭代越多越差（iters=100 → 0.650，
300 → 0.525；网络单独 0.695）。标定后用 `p = sigmoid(a·v + b)` 换算，
实测把 `leaf_opp` 组的 Brier 从 0.257 降到 0.189。

## 口径要点（容易做错的地方）

价值头吃的是「**行动方视角**」的状态向量 → 其输出恒表示"**该状态行动方**的
胜负预期"。因此：

- 拟合样本的标签必须按**该状态的行动方**回填（不是按"我方"）；
- MCTS 里对非 root 行动方要取 `1 - p**，且必须**先转概率再翻转**
  （非对称映射下与"先取反再映射"不等价）。
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Optional


def fit_platt(vs: list, ys: list, iters: int = 3000, lr: float = 0.5
              ) -> tuple:
    """Platt scaling：`p = sigmoid(a·v + b)`，用 BCE 梯度下降拟合

    `a` 承载"量纲/斜率"修正、`b` 承载"系统乐观/悲观"修正 —— 正好对应
    上面两个未被检验的假设。纯 Python 实现（样本量万级，无需 numpy）。
    """
    n = len(vs)
    if n == 0:
        return 1.0, 0.0
    a, b = 1.0, 0.0
    for _ in range(iters):
        ga = gb = 0.0
        for v, y in zip(vs, ys):
            z = max(-30.0, min(30.0, a * v + b))
            p = 1.0 / (1.0 + math.exp(-z))
            d = p - y
            ga += d * v
            gb += d
        a -= lr * ga / n
        b -= lr * gb / n
    return a, b


def _brier(vs, ys, a: float, b: float) -> float:
    if not vs:
        return float("nan")
    tot = 0.0
    for v, y in zip(vs, ys):
        z = max(-30.0, min(30.0, a * v + b))
        p = 1.0 / (1.0 + math.exp(-z))
        tot += (p - y) ** 2
    return tot / len(vs)


def calibration_metrics(vs: list, ys: list, a: float, b: float) -> dict:
    """标定前（`(v+1)/2`）vs 标定后（Platt）的标定质量对照"""
    def _pack(p_of):
        return [(v, y, min(1.0 - 1e-6, max(1e-6, p_of(v))))
                for v, y in zip(vs, ys)]

    def _stats(pairs, label):
        n = len(pairs)
        if not n:
            return {}
        brier = sum((p - y) ** 2 for _, y, p in pairs) / n
        ll = -sum(y * math.log(p) + (1 - y) * math.log(1 - p)
                  for _, y, p in pairs) / n
        pos = [p for _, y, p in pairs if y > 0.5]
        neg = [p for _, y, p in pairs if y < 0.5]
        if pos and neg:
            wins = sum(1.0 if p > q else (0.5 if p == q else 0.0)
                       for p in pos for q in neg)
            auc = wins / (len(pos) * len(neg))
        else:
            auc = float("nan")
        ece, bins = 0.0, []
        for k in range(10):
            lo, hi = k / 10, (k + 1) / 10
            sel = [(y, p) for _, y, p in pairs
                   if lo <= p < hi or (k == 9 and p == 1.0)]
            if not sel:
                continue
            conf = sum(p for _, p in sel) / len(sel)
            acc = sum(y for y, _ in sel) / len(sel)
            ece += abs(acc - conf) * len(sel) / n
            bins.append({"bucket": f"{lo:.1f}-{hi:.1f}", "n": len(sel),
                         "pred": round(conf, 3), "actual": round(acc, 3),
                         "gap": round(acc - conf, 3)})
        return {"label": label, "n": n, "brier": round(brier, 4),
                "logloss": round(ll, 4), "auc": round(auc, 4),
                "ece": round(ece, 4), "bins": bins}

    return {
        "raw": _stats(_pack(lambda v: (v + 1.0) / 2.0), "raw (v+1)/2"),
        "platt": _stats(_pack(lambda v: 1.0 / (1.0 + math.exp(
            -max(-30.0, min(30.0, a * v + b))))), "platt sigmoid(a·v+b)"),
    }


def load_calib(path: str, group: str = "pooled") -> Optional[tuple]:
    """读标定 JSON → `(a, b)`；文件缺失/结构不符 → None（调用方退回默认映射）"""
    p = Path(path)
    if not p.is_file():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        st = ((d.get("groups") or {}).get(group) or {}).get("stat") or {}
        if "platt_a" in st and "platt_b" in st:
            return (float(st["platt_a"]), float(st["platt_b"]))
    except Exception:                                    # noqa: BLE001
        return None
    return None


def prob_of(v: float, calib: Optional[tuple] = None) -> float:
    """行动方视角价值 → 胜率（标定可选）；与 `mcts._value_actor_p` 同源语义"""
    if calib is not None:
        a, b = calib
        z = max(-30.0, min(30.0, a * v + b))
        return 1.0 / (1.0 + math.exp(-z))
    return max(0.0, min(1.0, (v + 1.0) / 2.0))
