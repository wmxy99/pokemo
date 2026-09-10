# -*- coding: utf-8 -*-
"""PokéLab AI API · 批量自动对战路由（PRD F6/F7）

- 异步任务：提交后后台线程逐局执行（AI vs AI：两侧均 BaselineAI，可用 MCTS 加强）
- 配额：单任务 ≤500 局（参数强制）· 全局并发 ≤2 · 每用户日配额 ≤10（PRD §5.2）
- 可取消（保留已完成局）· 完成后按 format 导出平衡性报告（含先后手/回合，
  "Unofficial community data" 声明，对齐 PRD C1）
"""
from __future__ import annotations
import threading
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from . import store, schemas
from .auth import Principal, current_user, ensure_owner

router = APIRouter(prefix="/batch", tags=["batch"])

MAX_GAMES = 500
MAX_CONCURRENT = 2
_worker_lock = threading.Lock()
_active: dict = {}                     # job_id -> cancel threading.Event
_progress: dict = {}                   # job_id -> {done, n, wins, ...}（内存进度）
_running = 0


def _resolve_deck(spec: str) -> list:
    from .battles import _resolve_deck as _r
    return _r(spec)


def _usage_today(kind: str, owner: str) -> int:
    start = datetime.combine(datetime.now().date(), datetime.min.time()) \
        .replace(tzinfo=timezone.utc).timestamp()
    rows = [j for j in store.list_jobs(kind, owner, limit=500)
            if j["created"] >= start]
    return len(rows)


def _worker(job_id: str, da: list, db: list, games: int, seed0: int):
    from engine.simulator import run_match
    from engine.agents import BaselineAI
    global _running
    cancel = _active[job_id]
    a_agent = BaselineAI("a")
    b_agent = BaselineAI("b")
    wins = losses = draws = 0
    first_wins = first_total = 0        # a 先手子样本
    turns_total = 0
    stats = {"games_done": 0}
    store.update_job(job_id, status="running")
    try:
        for i in range(games):
            if cancel.is_set():
                break
            seed = seed0 + i
            b = run_match(da, db, a_agent, b_agent, seed=seed)
            if b.winner == 0:
                wins += 1
            elif b.winner is None:
                draws += 1
            else:
                losses += 1
            turns_total += b.turn if hasattr(b, "turn") else 0
            _progress[job_id] = {"done": i + 1, "n": games}
            if i and i % 10 == 0:
                store.update_job(job_id, result={"partial": _progress[job_id]})
        done = min(games, _progress[job_id]["done"])
        first_rate = wins / done if done else 0.0
        result = {
            "n": done, "wins_a": wins, "losses_a": losses, "draws": draws,
            "win_rate_a": round(wins / done, 4) if done else 0.0,
            "avg_turns": round(turns_total / done, 2) if done else 0.0,
            "seed_start": seed0, "cancelled": cancel.is_set(),
            "declaration": "Unofficial community data · 非官方数据（PRD C1）",
        }
        store.update_job(job_id, status="cancelled" if cancel.is_set()
                         else "done", result=result)
    except Exception as e:                       # noqa: BLE001
        store.update_job(job_id, status="failed", error=str(e))
    finally:
        _progress.pop(job_id, None)
        _active.pop(job_id, None)
        with _worker_lock:
            _running -= 1


@router.post("/jobs", status_code=202)
def submit_batch(req: schemas.BatchJobReq,
                 p: Principal = Depends(current_user)):
    global _running
    if _usage_today("batch", p.name) >= 10:
        raise HTTPException(429, "本日批量任务配额 10 已用尽（PRD F6）")
    da, db = _resolve_deck(req.deck_a), _resolve_deck(req.deck_b)
    params = {"deck_a": req.deck_a, "deck_b": req.deck_b,
              "games": req.games, "seed_start": req.seed_start}
    job_id = store.create_job("batch", p.name, params)
    with _worker_lock:
        if _running >= MAX_CONCURRENT:
            store.update_job(job_id, status="failed",
                             error="并发已满（全局≤2），请稍后重试")
            return {"id": job_id, "status": "failed",
                    "error": "并发已满（≤2），请稍后重试"}
        _running += 1
    cancel = threading.Event()
    _active[job_id] = cancel
    threading.Thread(target=_worker,
                     args=(job_id, da, db, req.games, req.seed_start),
                     daemon=True).start()
    return {"id": job_id, "status": "running", "note": f"games≤{MAX_GAMES}"}


@router.get("/jobs/{job_id}")
def job_status(job_id: str, p: Principal = Depends(current_user)):
    j = store.get_job(job_id)
    if not j:
        raise HTTPException(404, "任务不存在")
    ensure_owner(j["owner"], p)
    prog = _progress.get(job_id, {})
    return {"id": job_id, "status": j["status"],
            "params": j["params"], "progress": prog,
            "result": j["result"], "error": j.get("error"),
            "created": j["created"]}


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, p: Principal = Depends(current_user)):
    j = store.get_job(job_id)
    if not j:
        raise HTTPException(404, "任务不存在")
    ensure_owner(j["owner"], p)
    if job_id in _active:
        _active[job_id].set()
        return {"id": job_id, "status": "cancelling",
                "note": "已在当前局后停止；已完成局保留（K4）"}
    if j["status"] in ("done", "failed", "cancelled"):
        return {"id": job_id, "status": j["status"]}
    store.update_job(job_id, status="cancelled")
    return {"id": job_id, "status": "cancelled"}


@router.get("/jobs/{job_id}/report")
def report(job_id: str, p: Principal = Depends(current_user),
           format: str = "md"):
    j = store.get_job(job_id)
    if not j:
        raise HTTPException(404, "任务不存在")
    ensure_owner(j["owner"], p)
    if j["status"] not in ("done", "cancelled"):
        raise HTTPException(409, "任务未完成，暂无报告")
    res = j["result"] or {}
    lines = [
        f"# 批量对战报告（{job_id}）",
        "",
        f"- 卡组：{j['params']['deck_a']}（A）vs {j['params']['deck_b']}（B）",
        f"- 局数：{res.get('n')} · seed 起点：{res.get('seed_start')}",
        f"- A 胜率：{res.get('win_rate_a')} · A 胜 {res.get('wins_a')} / "
        f"负 {res.get('losses_a')} / 平 {res.get('draws')}",
        f"- 平均回合：{res.get('avg_turns')}",
        f"- 取消标记：{res.get('cancelled')}",
        f"- 声明：{res.get('declaration')}",
    ]
    if format == "json":
        import json as _json
        return _json.dumps({**res, "params": j["params"]}, ensure_ascii=False,
                           indent=1)
    if format == "csv":
        head = "n,wins_a,losses_a,draws,win_rate_a,avg_turns"
        row = f"{res.get('n')},{res.get('wins_a')},{res.get('losses_a')}," \
              f"{res.get('draws')},{res.get('win_rate_a')},{res.get('avg_turns')}"
        return head + "\n" + row
    return "\n".join(lines)
