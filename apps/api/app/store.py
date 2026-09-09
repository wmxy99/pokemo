# -*- coding: utf-8 -*-
"""PokéLab AI API · 存储层

- battle 会话：内存 dict（对局引擎对象不可序列化；进程内保活，重启即清，文档注明）
- jobs / battles 元数据 / 训练日志曲线：SQLite 持久化（重启可见完成态，进度字段周期性落库）
"""
from __future__ import annotations
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parents[3]          # pokelab-ai
DB_PATH = Path(__file__).resolve().parents[1] / "data" / "api.db"
# apps/api/data/api.db
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

_lock = threading.RLock()
_conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
_conn.row_factory = sqlite3.Row
_conn.executescript(
    """
    CREATE TABLE IF NOT EXISTS battles(
      id TEXT PRIMARY KEY, owner TEXT, deck_a TEXT, deck_b TEXT,
      seed INTEGER, status TEXT, created REAL, payload TEXT);
    CREATE TABLE IF NOT EXISTS jobs(
      id TEXT PRIMARY KEY, kind TEXT, owner TEXT, status TEXT,
      params TEXT, result TEXT, error TEXT, created REAL, updated REAL);
    CREATE TABLE IF NOT EXISTS users(
      name TEXT PRIMARY KEY, role TEXT, quota_day INTEGER DEFAULT 10);
    """
)
_conn.commit()

# 内存对局会话：battle_id -> Battle（引擎对象）
battle_sessions: dict = {}
sessions_lock = threading.RLock()


def now() -> float:
    return time.time()


def new_id(kind: str) -> str:
    return f"{kind}_{int(time.time())}_{uuid4().hex[:6]}"


from uuid import uuid4  # noqa: E402  (延迟导入保持头部长注释清晰)


# ---------- battles ----------
def save_battle(rec: dict) -> None:
    payload = rec.get("payload", {})
    if not isinstance(payload, str):
        payload = json.dumps(payload, ensure_ascii=False)
    with _lock:
        _conn.execute(
            "INSERT OR REPLACE INTO battles(id,owner,deck_a,deck_b,seed,"
            "status,created,payload) VALUES(?,?,?,?,?,?,?,?)",
            (rec["id"], rec.get("owner", "guest"), rec["deck_a"],
             rec["deck_b"], rec["seed"], rec["status"], rec["created"],
             payload))
        _conn.commit()


def get_battle(bid: str) -> Optional[dict]:
    with _lock:
        row = _conn.execute("SELECT * FROM battles WHERE id=?", (bid,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    if isinstance(d.get("payload"), str):
        d["payload"] = json.loads(d["payload"] or "{}")
    return d


def update_battle(bid: str, **kw) -> None:
    rec = get_battle(bid)
    if not rec:
        return
    if "payload" in kw:
        base = rec.get("payload") or {}
        base.update(kw["payload"])
        kw["payload"] = base
    rec.update(kw)
    save_battle(rec)


# ---------- jobs（batch / train 通用） ----------
def create_job(kind: str, owner: str, params: dict) -> str:
    jid = new_id(kind)
    with _lock:
        _conn.execute(
            "INSERT INTO jobs(id,kind,owner,status,params,result,error,"
            "created,updated) VALUES(?,?,?,?,?,?,?,?,?)",
            (jid, kind, owner, "queued",
             json.dumps(params, ensure_ascii=False), None, None,
             now(), now()))
        _conn.commit()
    return jid


def update_job(jid: str, **kw) -> None:
    fields, vals = [], []
    for k, v in kw.items():
        fields.append(f"{k}=?")
        vals.append(json.dumps(v, ensure_ascii=False) if k in ("params", "result")
                    else v)
    if not fields:
        return
    fields.append("updated=?")
    vals.append(now())
    vals.append(jid)
    with _lock:
        _conn.execute(
            f"UPDATE jobs SET {','.join(fields)} WHERE id=?", vals)
        _conn.commit()


def get_job(jid: str) -> Optional[dict]:
    with _lock:
        row = _conn.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
    if not row:
        return None
    d = dict(row)
    for k in ("params", "result"):
        if d.get(k):
            d[k] = json.loads(d[k])
    return d


def list_jobs(kind: str, owner: str, limit: int = 50) -> list:
    with _lock:
        rows = _conn.execute(
            "SELECT id,kind,owner,status,created,updated FROM jobs "
            "WHERE kind=? AND owner=? ORDER BY created DESC LIMIT ?",
            (kind, owner, limit)).fetchall()
    return [dict(r) for r in rows]


# ---------- users（demo） ----------
def upsert_user(name: str, role: str = "user") -> None:
    with _lock:
        _conn.execute(
            "INSERT INTO users(name,role,quota_day) VALUES(?,?,10) "
            "ON CONFLICT(name) DO UPDATE SET role=excluded.role", (name, role))
        _conn.commit()


def get_user(name: str) -> Optional[dict]:
    with _lock:
        row = _conn.execute("SELECT * FROM users WHERE name=?", (name,)).fetchone()
    return dict(row) if row else None
