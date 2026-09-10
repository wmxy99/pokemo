# -*- coding: utf-8 -*-
"""PokéLab AI API · 训练任务管理路由

训练以独立子进程执行仓库内 `examples/run_k1_longtrain.py`（成熟课程脚本），
API 负责：启动（参数透传）→ 进度/日志监控（tail + 解析 eval 曲线）→ 取消（kill）。
状态：queued/running/done/failed/cancelled · 曲线：从 stdout 解析 [k1:eval:*] 行。
"""
from __future__ import annotations
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from . import store, schemas, auth
from .auth import Principal, current_user, ensure_owner
from .sse import sse_response, FINAL as _FINAL

router = APIRouter(prefix="/train", tags=["train"])

_REPO = Path(__file__).resolve().parents[3]          # pokelab-ai
_AGENT_DIR = _REPO / "packages" / "agent"
_RUN_K1 = _AGENT_DIR / "examples" / "run_k1_longtrain.py"

_procs: dict = {}                # job_id -> Popen
_log_paths: dict = {}            # job_id -> Path
_tails: dict = {}                # job_id -> 最近 80 行
_lock = threading.Lock()
_RUNNING_MAX = 2

_EVAL_RE = re.compile(r"\[k1:eval:(\S+)\]\s+vs baseline win_rate=([\d.]+)")

_JOB_DIR = store.DATA_DIR / "train_jobs"          # 与 DB 同目录（可写位置）
_JOB_DIR.mkdir(parents=True, exist_ok=True)


def _tail(path: Path, n: int = 60) -> list:
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        return lines[-n:]
    except Exception:
        return []


def _parse_curve(lines: list) -> list:
    out = []
    for ln in lines:
        m = _EVAL_RE.search(ln)
        if m:
            out.append({"at": m.group(1), "win_rate": float(m.group(2))})
    return out


def _monitor(job_id: str, proc: subprocess.Popen, logf: Path):
    """等待子进程结束并收尾状态/曲线"""
    code = proc.wait()
    lines = _tail(logf, 400)
    curve = _parse_curve(lines)
    res = {"exit_code": code, "curve": curve,
           "weights": f"artifacts/k1_policy_{job_id}.pt"}
    status = "done" if code == 0 else "failed"
    store.update_job(job_id, status=status, result=res)
    with _lock:
        _procs.pop(job_id, None)      # 日志路径保留以便完成后仍可查询 /log


@router.post("/jobs", status_code=202)
def submit_train(req: schemas.TrainJobReq,
                 p: Principal = Depends(current_user)):
    if p.role == "guest":
        raise HTTPException(403, "训练管理需登录账号（F13/M5）")
    if not _RUN_K1.exists():
        raise HTTPException(500, "训练脚本缺失：" + str(_RUN_K1))
    with _lock:
        if len(_procs) >= _RUNNING_MAX:
            raise HTTPException(429, "训练并发已满（≤2）")
    params = req.model_dump()
    job_id = store.create_job("train", p.name, params)
    save = _JOB_DIR / f"policy_{job_id}.pt"
    report = _JOB_DIR / f"K1_report_{job_id}.md"
    logf = _JOB_DIR / f"{job_id}.log"
    cmd = [sys.executable, "-u", str(_RUN_K1),
           "--bc-episodes", str(req.bc_episodes),
           "--stage1-episodes", str(req.stage1_episodes),
           "--stage2-episodes", str(req.stage2_episodes),
           "--stage3-episodes", str(req.stage3_episodes),
           "--features", req.features,
           "--lr", str(req.lr),
           "--save", str(save),
           "--report", str(report)]
    try:
        fh = open(logf, "w", encoding="utf-8")
        proc = subprocess.Popen(cmd, cwd=str(_AGENT_DIR),
                                stdout=fh, stderr=subprocess.STDOUT)
        fh.close()
    except Exception as e:                       # noqa: BLE001
        store.update_job(job_id, status="failed", error=str(e))
        raise HTTPException(500, f"训练启动失败：{e}") from e
    with _lock:
        _procs[job_id] = proc
        _log_paths[job_id] = logf
    threading.Thread(target=_monitor, args=(job_id, proc, logf),
                     daemon=True).start()
    return {"id": job_id, "status": "running", "log": str(logf)}


@router.get("/jobs")
def list_trains(p: Principal = Depends(current_user), limit: int = 20):
    return {"jobs": store.list_jobs("train", p.name, limit)}


@router.get("/jobs/{job_id}")
def train_status(job_id: str, p: Principal = Depends(current_user)):
    j = store.get_job(job_id)
    if not j:
        raise HTTPException(404, "训练任务不存在")
    ensure_owner(j["owner"], p)
    logf = _log_paths.get(job_id)
    lines = _tail(logf, 40) if logf else []
    return {"id": job_id, "status": j["status"],
            "params": j["params"], "result": j["result"],
            "error": j.get("error"),
            "log_tail": lines, "created": j["created"]}


@router.get("/jobs/{job_id}/stream")
def train_stream(job_id: str, token: str = Query(default=""),
                 lines: int = Query(60, ge=5, le=400),
                 p: Principal = Depends(current_user)):
    """SSE 实时训练进度：状态 + 日志 tail + 评估曲线（替代前端轮询）"""
    if token:
        p = auth.user_from_token(token)
    j = store.get_job(job_id)
    if not j:
        raise HTTPException(404, "训练任务不存在")
    ensure_owner(j["owner"], p)

    def snap() -> dict:
        jj = store.get_job(job_id)
        logf = _log_paths.get(job_id)
        tail = _tail(logf, lines) if logf else []
        return {"id": job_id, "status": jj["status"],
                "log_tail": tail,
                "curve": _parse_curve(tail),
                "result": jj["result"] if jj["status"] in _FINAL else None,
                "error": jj.get("error")}

    return sse_response(snap, interval=1.0)


@router.get("/jobs/{job_id}/log")
def train_log(job_id: str, p: Principal = Depends(current_user),
              lines: int = 100):
    j = store.get_job(job_id)
    if not j:
        raise HTTPException(404, "训练任务不存在")
    ensure_owner(j["owner"], p)
    logf = _log_paths.get(job_id)
    return {"id": job_id, "lines": _tail(logf, lines) if logf else []}


@router.post("/jobs/{job_id}/cancel")
def cancel_train(job_id: str, p: Principal = Depends(current_user)):
    j = store.get_job(job_id)
    if not j:
        raise HTTPException(404, "训练任务不存在")
    ensure_owner(j["owner"], p)
    proc = _procs.get(job_id)
    if proc and proc.poll() is None:
        proc.kill()
        proc.wait(timeout=10)
        store.update_job(job_id, status="cancelled",
                         result={"note": "用户取消", "curve": []})
        return {"id": job_id, "status": "cancelled"}
    store.update_job(job_id, status="cancelled")
    return {"id": job_id, "status": "cancelled"}
