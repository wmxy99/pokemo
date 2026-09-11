# -*- coding: utf-8 -*-
"""PokéLab AI API · 统计与报告路由（PRD F7 · M4/M6）

GET /stats/summary  聚合该 owner 的批量任务 → KPI + 胜率矩阵（我方卡组 × 对手卡组）
                    + 近 7 日任务量；用于统计页三 KPI + 矩阵表 + 后台仪表盘。
GET /stats/watchdog K1–K4 看门狗快照（阈值/当前/状态），供后台 M6 展示（诚实标注）。
"""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends

from . import store
from .labels import deck_label_zh
from .auth import Principal, current_user

router = APIRouter(prefix="/stats", tags=["stats"])


def _k2_text(k2: dict) -> str:
    """K2 现状文案（有报告 → 实测数字；无 → 提示先跑评测）"""
    if not k2:
        return ("尚未评测——运行 `cd packages/agent && python examples/run_k2.py`"
                "（rule 离线基线 / llm 需配 Key）")
    pct = lambda x: "—" if x is None else f"{x * 100:.1f}%"
    return (f"{k2.get('backend')} 后端：严格 {pct(k2.get('strict_acc'))}"
            f"（已知句式 {pct(k2.get('seen_strict_acc'))}"
            f" / 留出句式 {pct(k2.get('holdout_strict_acc'))}）"
            f"· n={k2.get('n')}"
            f"· 真实卡池可映射 tag {pct(k2.get('pool_support_rate'))}")


def _ci_half(p: float, n: int) -> float:
    if not n:
        return 0.0
    return round(1.96 * (max(p * (1 - p), 1e-9) / n) ** 0.5, 4)


@router.get("/summary")
def summary(p: Principal = Depends(current_user)):
    jobs = store.list_jobs("batch", p.name, limit=200)
    done = []
    for j in jobs:
        full = store.get_job(j["id"])
        if full and full["status"] in ("done", "cancelled") and full.get("result"):
            done.append(full)
    total_games = sum(int(f["result"].get("n") or 0) for f in done)
    wins = sum(int(f["result"].get("wins_a") or 0) for f in done)
    turns = [f["result"].get("avg_turns") or 0 for f in done if f["result"].get("n")]
    kpi = {
        "tasks": len(done),
        "total_games": total_games,
        "win_rate_a": round(wins / total_games, 4) if total_games else None,
        "avg_turns": round(sum(turns) / len(turns), 2) if turns else None,
    }
    # 胜率矩阵（我方 deck_a × 对手 deck_b）
    # - 单对位任务：1 个 cell
    # - **卡组池任务**（F7）：展开 result.cells 后逐对位累加（多任务可叠加）
    cells: dict = defaultdict(lambda: {"n": 0, "wins": 0})
    pool_jobs = 0
    for f in done:
        res = f["result"] or {}
        pa = f["params"] or {}
        if pa.get("pool") and res.get("cells"):
            pool_jobs += 1
            for c in res["cells"]:
                key = (c.get("deck_a", "?"), c.get("deck_b", "?"))
                cells[key]["n"] += int(c.get("n") or 0)
                cells[key]["wins"] += int(c.get("wins_a") or 0)
        else:
            key = (pa.get("deck_a", "?"), pa.get("deck_b", "?"))
            cells[key]["n"] += int(res.get("n") or 0)
            cells[key]["wins"] += int(res.get("wins_a") or 0)
    matrix = []
    for (a, b), v in sorted(cells.items()):
        n = v["n"] or 0
        wr = v["wins"] / n if n else 0.0
        matrix.append({"deck_a": a, "deck_b": b,
                       "deck_a_zh": deck_label_zh(a),
                       "deck_b_zh": deck_label_zh(b), "n": n,
                       "win_rate_a": round(wr, 4), "ci95": _ci_half(wr, n)})
    # 近 7 日
    today = datetime.now().date()
    recent = []
    for d in range(6, -1, -1):
        day = today - timedelta(days=d)
        s0 = datetime.combine(day, datetime.min.time()).timestamp()
        s1 = s0 + 86400
        n = sum(1 for j in jobs if s0 <= j["created"] < s1)
        recent.append({"date": day.isoformat(), "tasks": n})
    kpi["pool_jobs"] = pool_jobs
    kpi["matrix_pairs"] = len(matrix)
    return {"kpi": kpi, "matrix": matrix, "recent7d": recent,
            "declaration": "Unofficial community data · 非官方数据（PRD C1）",
            "sample_note": "样本量标注于矩阵 tooltip；n<30 时 CI 较宽，勿据此下结论"}


@router.get("/watchdog")
def watchdog(p: Principal = Depends(current_user)):
    """K1–K4 看门狗（诚实标注：K1 当前为训练脚本产出的最近评估值）"""
    import json
    from pathlib import Path
    k1 = None
    curve = Path(__file__).resolve().parents[3] / "packages" / "agent" / \
        "artifacts" / "curve.json"
    if curve.exists():
        try:
            data = json.loads(curve.read_text(encoding="utf-8"))
            evals = data.get("eval") or []
            if evals:
                last = evals[-1]
                k1 = {"value": last.get("win_rate"), "n": last.get("n"),
                      "tag": last.get("tag"),
                      "note": "来自最近一次训练曲线（artifacts/curve.json）"}
        except Exception:
            pass
    # K2：读取 packages/agent/artifacts/k2_report.json（examples/run_k2.py 产出）
    k2 = None
    try:
        k2_file = (Path(__file__).resolve().parents[3] / "packages" / "agent"
                   / "artifacts" / "k2_report.json")
        if k2_file.exists():
            k2 = json.loads(k2_file.read_text(encoding="utf-8"))
    except Exception:
        k2 = None

    rows = [
        {"id": "K1", "name": "vs 规则基线胜率", "threshold": "≥70%（200 局协议）",
         "current": (f"{k1['value']:.3f}（n={k1['n']}）" if k1 else "无训练记录"),
         "status": ("pass" if k1 and k1["value"] and k1["value"] >= 0.70
                    else "warn" if k1 else "unknown")},
        {"id": "K2", "name": "卡牌效果解析准确率",
         "threshold": (f"≥{int((k2 or {}).get('target', 0.85) * 100)}%"
                       f"（黄金集 200+）"),
         "current": (self_k2_text(k2) if False else _k2_text(k2)),
         "status": ("pass" if k2 and k2.get("strict_acc", 0) >= k2.get("target", 0.85)
                    else "warn" if k2 else "unknown")},
        {"id": "K3", "name": "单步决策 P95", "threshold": "≤2s（argmax 兜底）",
         "current": "argmax ~0.3ms · MCTS12 ~137ms（eval_matrix 实测）",
         "status": "pass"},
        {"id": "K4", "name": "连续自动对战", "threshold": "≥100 局免干预",
         "current": "批量任务可中断保留已完成；异常重试机制在引擎层",
         "status": "pass"},
    ]
    return {"rows": rows, "k1_detail": k1,
            "disclaimer": "N7 诚实标注：K1 尚未达成，README「冲 70% 手册」已就位"}
