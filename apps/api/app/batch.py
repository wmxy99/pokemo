# -*- coding: utf-8 -*-
"""PokéLab AI API · 批量自动对战路由（PRD F6/F7）

- 单对位任务：POST /batch/jobs（AI vs AI，两侧规则基线）
- **卡组池矩阵任务**：POST /batch/pool-jobs（decks_a × decks_b 全对位 × games_each，
  直出 F7 胜率矩阵 cells，含 n 与 95%CI）
- 配额：单任务 ≤500 局 · 全局并发 ≤2 · 每用户日配额 ≤10（PRD §5.2）
- 可取消（保留已完成局，K4）· 报告 md/csv/json（含 "Unofficial community data"）
- **实时进度**：GET /batch/jobs/{id}/stream（SSE，替代轮询）
- 卡组入参支持：demo 卡组名 或 F3 已导入卡组 id（owner 隔离）
"""
from __future__ import annotations
import threading
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from . import store, schemas, auth
from .auth import Principal, current_user, ensure_owner
from .labels import deck_label_zh
from .sse import sse_response, FINAL, parse_last_event_id as sse_parse_last

router = APIRouter(prefix="/batch", tags=["batch"])

MAX_GAMES = 500
MAX_CONCURRENT = 2
DAILY_QUOTA = 10
_worker_lock = threading.Lock()
_active: dict = {}                     # job_id -> cancel threading.Event
_progress: dict = {}                   # job_id -> {done, n, ...}（内存进度）
_running = 0


def _deck_of(spec: str, p: Principal):
    """demo 卡组名 或 F3 已导入卡组 id → (引擎卡组列表, 标签)"""
    d = store.get_deck(spec) if spec else None
    if d:
        ensure_owner(d["owner"], p)
        from .decks import build_engine_deck
        try:
            return build_engine_deck(spec), d["name"]
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
    from .battles import _resolve_deck
    return _resolve_deck(spec), spec


def _usage_today(kind: str, owner: str) -> int:
    start = datetime.combine(datetime.now().date(), datetime.min.time()) \
        .replace(tzinfo=timezone.utc).timestamp()
    return len([j for j in store.list_jobs(kind, owner, limit=500)
                if j["created"] >= start])


def _ci_half(p: float, n: int) -> float:
    if not n:
        return 0.0
    return round(1.96 * (max(p * (1 - p), 1e-9) / n) ** 0.5, 4)


from .compliance import DATA_DECLARATION as DECLARATION  # 统一声明（合规 6.1）


def _turns_of(battle) -> int:
    """Battle → 回合数（容错读取）

    `Battle` 的字段名是 `turn_number`；**不要**用 `getattr(b, 'turn', 0)`
    之类的「静默兜底」——字段名打错时它会悄悄返回 0，使「平均回合」恒为 0
    而没有任何报错。这里显式取 `turn_number`，缺失即抛错（快速失败）。
    """
    try:
        return int(battle.turn_number)
    except AttributeError as e:              # noqa: PERF203
        raise AttributeError(
            "Battle 缺少 turn_number（接口变更？请同步 batch._turns_of）") from e


def _run_pairs(job_id: str, pairs: list, games_each: int, seed0: int,
               total_override: int | None = None) -> dict:
    """执行 (label_a, da, label_b, db) × games_each；返回 result（含 cells）"""
    from engine.simulator import run_match
    from engine.agents import BaselineAI
    cancel = _active[job_id]
    a_agent, b_agent = BaselineAI("a"), BaselineAI("b")
    total = total_override or len(pairs) * games_each
    cells, done = [], 0
    tot_w = tot_l = tot_d = 0
    tot_turns = 0.0
    for (la, da, lb, db) in pairs:
        w = l = dr = 0
        turns = 0.0
        for i in range(games_each):
            if cancel.is_set():
                break
            b = run_match(da, db, a_agent, b_agent, seed=seed0 + done)
            if b.winner == 0:
                w += 1
            elif b.winner is None:
                dr += 1
            else:
                l += 1
            # 真 bug 修复：Battle 的回合字段是 `turn_number`，此前写 `b.turn`
            # （不存在）→ 受 hasattr 兜底「静默吞掉」，导致报告/统计里的
            # 「平均回合」恒为 0.0，且不会报错（最难发现的一类缺陷）。
            turns += _turns_of(b)
            done += 1
            _progress[job_id] = {"done": done, "n": total,
                                 "pair": f"{la} vs {lb}"}
            if done % 10 == 0:
                store.update_job(job_id, result={"partial": _progress[job_id]})
                # 事件流（SSE 断线续传的锚点：重建连接可回放这段进度）
                store.append_event(job_id, "progress", dict(_progress[job_id]))
        k = w + l + dr
        wr = round(w / k, 4) if k else 0.0
        cells.append({"deck_a": la, "deck_b": lb, "n": k, "wins_a": w,
                      "losses_a": l, "draws": dr, "win_rate_a": wr,
                      "ci95": _ci_half(wr, k),
                      "avg_turns": round(turns / k, 2) if k else 0.0})
        tot_w += w
        tot_l += l
        tot_d += dr
        tot_turns += turns
        if cancel.is_set():
            break
    n = tot_w + tot_l + tot_d
    return {
        "n": n, "wins_a": tot_w, "losses_a": tot_l, "draws": tot_d,
        "win_rate_a": round(tot_w / n, 4) if n else 0.0,
        "avg_turns": round(tot_turns / n, 2) if n else 0.0,
        "cells": cells, "pairs": len(cells), "games_each": games_each,
        "seed_start": seed0, "cancelled": cancel.is_set(),
        "declaration": DECLARATION,
    }


def _worker(job_id: str, la: str, da: list, lb: str, db: list,
            games: int, seed0: int):
    global _running
    try:
        store.update_job(job_id, status="running")
        store.append_event(job_id, "status", {"status": "running"})
        res = _run_pairs(job_id, [(la, da, lb, db)], games, seed0)
        status = "cancelled" if res["cancelled"] else "done"
        store.update_job(job_id, status=status, result=res)
        store.append_event(job_id, "status", {"status": status,
                                              "n": res.get("n"),
                                              "win_rate_a": res.get("win_rate_a")})
    except Exception as e:                       # noqa: BLE001
        store.update_job(job_id, status="failed", error=str(e))
        store.append_event(job_id, "status", {"status": "failed", "error": str(e)})
    finally:
        _progress.pop(job_id, None)
        _active.pop(job_id, None)
        with _worker_lock:
            _running -= 1


def _pool_worker(job_id: str, pairs: list, games_each: int, seed0: int):
    global _running
    try:
        store.update_job(job_id, status="running")
        store.append_event(job_id, "status", {"status": "running",
                                              "pairs": len(pairs)})
        res = _run_pairs(job_id, pairs, games_each, seed0)
        status = "cancelled" if res["cancelled"] else "done"
        store.update_job(job_id, status=status, result=res)
        store.append_event(job_id, "status", {"status": status,
                                              "n": res.get("n"),
                                              "cells": len(res.get("cells") or [])})
    except Exception as e:                       # noqa: BLE001
        store.update_job(job_id, status="failed", error=str(e))
        store.append_event(job_id, "status", {"status": "failed", "error": str(e)})
    finally:
        _progress.pop(job_id, None)
        _active.pop(job_id, None)
        with _worker_lock:
            _running -= 1


def _slot(job_id: str) -> bool:
    """占用并发槽；满则标记失败并返回 False"""
    global _running
    with _worker_lock:
        if _running >= MAX_CONCURRENT:
            store.update_job(job_id, status="failed",
                             error="并发已满（全局≤2），请稍后重试")
            return False
        _running += 1
    return True


@router.post("/jobs", status_code=202)
def submit_batch(req: schemas.BatchJobReq,
                 p: Principal = Depends(current_user)):
    if _usage_today("batch", p.name) >= DAILY_QUOTA:
        raise HTTPException(429, f"本日批量任务配额 {DAILY_QUOTA} 已用尽（PRD F6）")
    da, la = _deck_of(req.deck_a_id or req.deck_a, p)
    db, lb = _deck_of(req.deck_b_id or req.deck_b, p)
    params = {"deck_a": la, "deck_b": lb, "games": req.games,
              "seed_start": req.seed_start}
    job_id = store.create_job("batch", p.name, params)
    if not _slot(job_id):
        return {"id": job_id, "status": "failed", "error": "并发已满（≤2）"}
    _active[job_id] = threading.Event()
    threading.Thread(target=_worker, args=(job_id, la, da, lb, db, req.games,
                                           req.seed_start), daemon=True).start()
    return {"id": job_id, "status": "running", "note": f"games≤{MAX_GAMES}"}


@router.post("/pool-jobs", status_code=202)
def submit_pool(req: schemas.PoolJobReq,
                p: Principal = Depends(current_user)):
    """卡组池矩阵任务：decks_a × decks_b 全对位，直出 F7 胜率矩阵"""
    if _usage_today("batch", p.name) >= DAILY_QUOTA:
        raise HTTPException(429, f"本日批量任务配额 {DAILY_QUOTA} 已用尽（PRD F6）")
    total = len(req.decks_a) * len(req.decks_b) * req.games_each
    if total > 2000:
        raise HTTPException(422, f"池任务总局数 {total} 超上限 2000"
                                 f"（{len(req.decks_a)}×{len(req.decks_b)}"
                                 f"×{req.games_each}）")
    pairs = []
    for a in req.decks_a:
        da, la = _deck_of(a, p)
        for b in req.decks_b:
            db, lb = _deck_of(b, p)
            pairs.append((la, da, lb, db))
    params = {"pool": True, "decks_a": req.decks_a, "decks_b": req.decks_b,
              "games_each": req.games_each, "pairs": len(pairs),
              "total_games": total, "seed_start": req.seed_start}
    job_id = store.create_job("batch", p.name, params)
    if not _slot(job_id):
        return {"id": job_id, "status": "failed", "error": "并发已满（≤2）"}
    _active[job_id] = threading.Event()
    threading.Thread(target=_pool_worker,
                     args=(job_id, pairs, req.games_each, req.seed_start),
                     daemon=True).start()
    return {"id": job_id, "status": "running",
            "pairs": len(pairs), "total_games": total}


@router.get("/jobs")
def list_batch(p: Principal = Depends(current_user), limit: int = 50):
    """该 owner 的批量任务列表（前端统计页 / 任务中心）"""
    return {"jobs": store.list_jobs("batch", p.name, limit)}


def _snapshot(job_id: str) -> dict:
    j = store.get_job(job_id)
    if not j:
        return {"id": job_id, "status": "failed", "error": "任务不存在"}
    return {"id": job_id, "status": j["status"],
            "progress": _progress.get(job_id, {}),
            "result": j["result"] if j["status"] in FINAL else None,
            "error": j.get("error")}


@router.get("/jobs/{job_id}")
def job_status(job_id: str, p: Principal = Depends(current_user)):
    j = store.get_job(job_id)
    if not j:
        raise HTTPException(404, "任务不存在")
    ensure_owner(j["owner"], p)
    return {"id": job_id, "status": j["status"], "params": j["params"],
            "progress": _progress.get(job_id, {}), "result": j["result"],
            "error": j.get("error"), "created": j["created"]}


@router.get("/jobs/{job_id}/stream")
def job_stream(job_id: str, request: Request, token: str = Query(default=""),
               last_event_id: str = Query(default=""),
               p: Principal = Depends(current_user)):
    """SSE 实时进度（EventSource 无自定义 header → 支持 ?token=<JWT>）

    断线续传：事件带 `id: <seq>`，浏览器重连自动回传 `Last-Event-ID`
    → 服务端重放该 seq 之后的进度事件（也可用 `?last_event_id=` 手工续传）。
    """
    if token:
        p = auth.user_from_token(token)
    j = store.get_job(job_id)
    if not j:
        raise HTTPException(404, "任务不存在")
    ensure_owner(j["owner"], p)
    last = sse_parse_last(request, last_event_id)
    return sse_response(
        lambda: _snapshot(job_id), interval=1.0, heartbeat_s=15.0,
        history=lambda seq: store.events_after(job_id, seq),
        last_event_id=last, request=request)


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
    if j["status"] in FINAL:
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
    cells = res.get("cells") or []
    params = j["params"]
    is_pool = bool(params.get("pool"))
    lines = [f"# {'卡组池矩阵' if is_pool else '批量对战'}报告（{job_id}）", ""]
    if is_pool:
        lines.append(f"- 卡组池：我方 {len(params.get('decks_a', []))} 组 × "
                     f"对手 {len(params.get('decks_b', []))} 组 × "
                     f"{params.get('games_each')} 局 = "
                     f"{params.get('total_games')} 局")
    else:
        lines.append(f"- 卡组：{deck_label_zh(params.get('deck_a'))}（A）vs "
                     f"{deck_label_zh(params.get('deck_b'))}（B）")
    lines += [f"- 实际完成：{res.get('n')} 局 · seed 起点：{res.get('seed_start')}",
              f"- 总胜率（A）：{res.get('win_rate_a')} · 平均回合："
              f"{res.get('avg_turns')}",
              f"- 取消标记：{res.get('cancelled')}",
              f"- 声明：{res.get('declaration')}", ""]
    if cells:
        lines += ["## 对位明细（F7 胜率矩阵数据源）", "",
                  "| 我方 \\ 对手 | n | A 胜率 | 95%CI | 平均回合 |",
                  "|---|---|---|---|---|"]
        for c in cells:
            lines.append(f"| {deck_label_zh(c['deck_a'])} vs "
                         f"{deck_label_zh(c['deck_b'])} | {c['n']} | "
                         f"{c['win_rate_a']:.4f} | ±{c['ci95']} | "
                         f"{c['avg_turns']} |")
    if format == "json":
        import json as _json
        # 保留英文原始键（数据键不变），另附中文展示字段
        cells_zh = [{**c, "deck_a_zh": deck_label_zh(c["deck_a"]),
                     "deck_b_zh": deck_label_zh(c["deck_b"])} for c in cells]
        return PlainTextResponse(
            _json.dumps({**res, "cells": cells_zh, "params": params},
                        ensure_ascii=False, indent=1),
            media_type="application/json")
    if format == "csv":
        # 报告面向人阅读 → 卡组名输出中文；表头亦中文
        head = "我方卡组,对手卡组,对局数,A胜场,A负场,平局,A胜率,95%CI,平均回合"
        rows = [",".join([deck_label_zh(c["deck_a"]), deck_label_zh(c["deck_b"]),
                          str(c.get("n")), str(c.get("wins_a")),
                          str(c.get("losses_a")), str(c.get("draws")),
                          str(c.get("win_rate_a")), str(c.get("ci95")),
                          str(c.get("avg_turns"))]) for c in cells]
        return PlainTextResponse(head + "\n" + "\n".join(rows),
                                 media_type="text/csv; charset=utf-8")
    return PlainTextResponse("\n".join(lines),
                             media_type="text/markdown; charset=utf-8")
