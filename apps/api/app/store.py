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


def _writable_dir(d: Path) -> bool:
    """探测目录能否容纳可写 SQLite（新建+写入+清理）"""
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / "_wprobe.db"
        con = sqlite3.connect(str(probe))
        con.execute("CREATE TABLE IF NOT EXISTS t(x)")
        con.execute("INSERT INTO t VALUES (1)")
        con.commit()
        con.close()
        try:
            probe.unlink()
        except Exception:
            pass
        return True
    except Exception:
        return False


def _pick_data_dir() -> Path:
    """数据目录候选（首个可写者胜）：env → 主目录 → 临时目录 → 项目内 data/

    受限环境（协同/沙箱写保护）下项目盘常不可写，自动回退保证服务可用。
    """
    import os as _os
    import tempfile
    cands = []
    env = _os.environ.get("POKELAB_DATA_DIR")
    if env:
        cands.append(Path(env))
    cands += [Path.home() / ".pokelab",
              Path(tempfile.gettempdir()) / "pokelab",
              Path(__file__).resolve().parents[1] / "data"]
    for d in cands:
        if _writable_dir(d):
            return d
    return cands[-1]


DATA_DIR = _pick_data_dir()


def _open_db(d: Path):
    """打开可写 SQLite：优先 api.db；若既有文件被环境写保护（readonly）→
    回退到带时间戳的新文件（新建文件总是可写，代价是重启不继承数据）。
    """
    last_err = None
    for name in ("api.db", f"api_{int(time.time())}.db"):
        p = d / name
        try:
            con = sqlite3.connect(str(p), check_same_thread=False)
            con.execute("CREATE TABLE IF NOT EXISTS _wprobe(x INTEGER)")
            con.execute("INSERT INTO _wprobe VALUES (1)")
            con.execute("DELETE FROM _wprobe")
            con.commit()
            return p, con
        except sqlite3.OperationalError as e:        # readonly / locked
            last_err = e
            continue
    raise RuntimeError(f"无可用可写数据库（{d}）：{last_err}")


DB_PATH, _conn = _open_db(DATA_DIR)
DB_FALLBACK = DB_PATH.name != "api.db"
_conn.row_factory = sqlite3.Row
_lock = threading.RLock()
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
    CREATE TABLE IF NOT EXISTS decks(
      id TEXT PRIMARY KEY, owner TEXT, name TEXT, text TEXT, valid INTEGER,
      cards TEXT, summary TEXT, created REAL);
    CREATE TABLE IF NOT EXISTS settings(
      key TEXT PRIMARY KEY, value TEXT, encrypted INTEGER DEFAULT 0,
      updated REAL, updated_by TEXT);
    """
)
# 迁移：真 JWT 需要口令哈希与创建时间列（幂等）
_ucols = {r["name"] for r in _conn.execute("PRAGMA table_info(users)")}
if "password_hash" not in _ucols:
    _conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
if "created" not in _ucols:
    _conn.execute("ALTER TABLE users ADD COLUMN created REAL")
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
            "SELECT id,kind,owner,status,params,created,updated FROM jobs "
            "WHERE kind=? AND owner=? ORDER BY created DESC LIMIT ?",
            (kind, owner, limit)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        # params 用于前端区分「单对位 / 卡组池矩阵」（此前未返回 → 类型列恒为单对位）
        if d.get("params"):
            try:
                d["params"] = json.loads(d["params"])
            except (TypeError, ValueError):
                d["params"] = {}
        out.append(d)
    return out


# ---------- 任务事件流（SSE 断线续传：Last-Event-ID 重放） ----------
# 事件表在内存中（与任务执行体同生命周期），seq 单调递增，形如
#   {seq: 1, kind: "progress", data: {...}, t: 1699...}
JOB_EVENTS: dict = {}
_EVENTS_LOCK = threading.RLock()
MAX_EVENTS = 500           # 每任务保留的最近事件数（防内存无限增长）


def append_event(jid: str, kind: str, data: dict) -> int:
    """追加一条任务事件，返回其 seq（作为 SSE 的 id 字段）"""
    with _EVENTS_LOCK:
        buf = JOB_EVENTS.setdefault(jid, [])
        seq = (buf[-1]["seq"] + 1) if buf else 1
        buf.append({"seq": seq, "kind": kind, "data": data, "t": now()})
        if len(buf) > MAX_EVENTS:
            del buf[:len(buf) - MAX_EVENTS]
        return seq


def events_after(jid: str, last_seq: int = 0) -> list:
    """返回 seq > last_seq 的事件（断线续传重放）"""
    with _EVENTS_LOCK:
        return [dict(e) for e in JOB_EVENTS.get(jid, [])
                if e["seq"] > int(last_seq or 0)]


def drop_events(jid: str) -> None:
    with _EVENTS_LOCK:
        JOB_EVENTS.pop(jid, None)


# ---------- decks ----------
def save_deck(rec: dict) -> None:
    # 合规 6.2：用户卡表原文（可能含个人构筑信息）按需加密存储
    from . import crypto
    with _lock:
        _conn.execute(
            "INSERT OR REPLACE INTO decks(id,owner,name,text,valid,cards,"
            "summary,created) VALUES(?,?,?,?,?,?,?,?)",
            (rec["id"], rec["owner"], rec["name"],
             crypto.encrypt(rec.get("text")),
             1 if rec.get("valid") else 0,
             json.dumps(rec.get("cards", []), ensure_ascii=False),
             json.dumps(rec.get("summary", {}), ensure_ascii=False),
             rec["created"]))
        _conn.commit()


def get_deck(did: str) -> Optional[dict]:
    from . import crypto
    with _lock:
        row = _conn.execute("SELECT * FROM decks WHERE id=?", (did,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["cards"] = json.loads(d["cards"] or "[]")
    d["summary"] = json.loads(d["summary"] or "{}")
    d["valid"] = bool(d["valid"])
    d["text"] = crypto.decrypt(d.get("text"))     # 密文自动解密；历史明文原样
    return d


def list_decks(owner: str, limit: int = 100) -> list:
    with _lock:
        rows = _conn.execute(
            "SELECT id,owner,name,valid,summary,created FROM decks "
            "WHERE owner=? ORDER BY created DESC LIMIT ?", (owner, limit)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["summary"] = json.loads(d["summary"] or "{}")
        d["valid"] = bool(d["valid"])
        out.append(d)
    return out


# ---------- users ----------
def create_user(name: str, password_hash: str, role: str = "user") -> None:
    with _lock:
        _conn.execute(
            "INSERT INTO users(name,role,quota_day,password_hash,created) "
            "VALUES(?,?,10,?,?)", (name, role, password_hash, now()))
        _conn.commit()


def get_user(name: str) -> Optional[dict]:
    with _lock:
        row = _conn.execute("SELECT * FROM users WHERE name=?", (name,)).fetchone()
    return dict(row) if row else None


def count_users() -> int:
    with _lock:
        return int(_conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"])


def list_users() -> list:
    with _lock:
        rows = _conn.execute(
            "SELECT name,role,quota_day,created FROM users "
            "ORDER BY created ASC").fetchall()
    return [dict(r) for r in rows]


def set_user_role(name: str, role: str) -> None:
    with _lock:
        _conn.execute("UPDATE users SET role=? WHERE name=?", (role, name))
        _conn.commit()


# ---------------- 键值设置（后台可视化配置用） ----------------
# 用途：由后台「模型配置」页保存云端模型端点/模型名/密钥。
# 约定：**含密值一律加密后再入库**（列 encrypted=1），读取时由调用方解密；
#       本模块不做加解密（保持 store 只负责存取，避免各处各写一遍）。
def get_setting(key: str) -> Optional[dict]:
    """读取一条设置 → {"key","value","encrypted","updated","updated_by"}"""
    with _lock:
        row = _conn.execute("SELECT * FROM settings WHERE key=?",
                            (key,)).fetchone()
    return dict(row) if row else None


def set_setting(key: str, value: str, *, encrypted: bool = False,
                updated_by: str = "system") -> None:
    with _lock:
        _conn.execute(
            "INSERT OR REPLACE INTO settings(key,value,encrypted,updated,"
            "updated_by) VALUES(?,?,?,?,?)",
            (key, value, 1 if encrypted else 0, now(), updated_by))
        _conn.commit()


def delete_setting(key: str) -> None:
    with _lock:
        _conn.execute("DELETE FROM settings WHERE key=?", (key,))
        _conn.commit()


def list_settings(prefix: str = "") -> list:
    with _lock:
        if prefix:
            rows = _conn.execute(
                "SELECT * FROM settings WHERE key LIKE ? ORDER BY key",
                (prefix + "%",)).fetchall()
        else:
            rows = _conn.execute("SELECT * FROM settings ORDER BY key").fetchall()
    return [dict(r) for r in rows]


# ---------------- 键值设置（后台可视化配置用） ----------------
# 用途：由后台「模型配置」页保存云端模型端点/模型名/密钥。
# 约定：**含密值一律加密后再入库**（列 encrypted=1），读取时由调用方解密；
#       本模块不做加解密（保持 store 只负责存取，避免各处各写一遍）。
def get_setting(key: str) -> Optional[dict]:
    """读取一条设置 → {"key","value","encrypted","updated","updated_by"}"""
    with _lock:
        row = _conn.execute("SELECT * FROM settings WHERE key=?",
                            (key,)).fetchone()
    return dict(row) if row else None


def set_setting(key: str, value: str, *, encrypted: bool = False,
                updated_by: str = "system") -> None:
    with _lock:
        _conn.execute(
            "INSERT OR REPLACE INTO settings(key,value,encrypted,updated,"
            "updated_by) VALUES(?,?,?,?,?)",
            (key, value, 1 if encrypted else 0, now(), updated_by))
        _conn.commit()


def delete_setting(key: str) -> None:
    with _lock:
        _conn.execute("DELETE FROM settings WHERE key=?", (key,))
        _conn.commit()


def list_settings(prefix: str = "") -> list:
    with _lock:
        if prefix:
            rows = _conn.execute(
                "SELECT * FROM settings WHERE key LIKE ? ORDER BY key",
                (prefix + "%",)).fetchall()
        else:
            rows = _conn.execute("SELECT * FROM settings ORDER BY key").fetchall()
    return [dict(r) for r in rows]
