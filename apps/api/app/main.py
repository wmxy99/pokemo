# -*- coding: utf-8 -*-
"""PokéLab AI API · 主入口（FastAPI）

路由：
  /auth/*       注册/登录（demo token，MVP 级；正式需 JWT+哈希，见 README）
  /battles/*    人机对战（F5）：创建/动作/视图/seed
  /batch/*      批量自动对战（F6）：异步任务、进度、取消、报告导出（F7）
  /train/*      训练任务管理（PPO 课程）：启动/监控/日志/取消

运行（repo 根）：
  conda activate pokemo
  python -m uvicorn apps.api.app.main:app --reload --port 8000
OpenAPI 文档：http://127.0.0.1:8000/docs
"""
from __future__ import annotations
import hashlib
import sys
from pathlib import Path

_APP_DIR = Path(__file__).resolve()
_REPO = _APP_DIR.parents[3]                       # apps/api/app → pokelab-ai
for pkg in ("engine", "agent"):
    sys.path.insert(0, str(_REPO / "packages" / pkg))

from fastapi import FastAPI, Depends, Header, HTTPException, Query  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from . import store, schemas  # noqa: E402

app = FastAPI(
    title="PokéLab AI API",
    version="0.1.0",
    description="宝可梦卡牌 AI 对战智能体后端服务（自研引擎 + AI 决策链路封装）。"
                "Unofficial fan project · Non-commercial · 无官方卡图素材（PRD C1/C2）。",
)
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])


# ---------------- auth（demo token；生产升级见 README §6） ----------------
def _token(name: str) -> str:
    return "demo." + hashlib.sha256(f"{name}:pokelab".encode()).hexdigest()[:24]


def current_user(authorization: str = Header(default="")) -> str:
    """解析 demo token → owner 名（默认 guest）。404 越权由各路由 owner 检查。"""
    if authorization.startswith("Bearer "):
        t = authorization[7:]
        for row in _users_all():
            if _token(row["name"]) == t:
                return row["name"]
    return "guest"


def _users_all() -> list:
    # store 只按名读写；demo 认证从 store.users 枚举
    import sqlite3
    con = sqlite3.connect(str(store.DB_PATH))
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT name FROM users").fetchall()
    con.close()
    return [dict(r) for r in rows]


@app.post("/auth/register", tags=["auth"])
def register(req: schemas.RegisterReq):
    store.upsert_user(req.name)
    return {"name": req.name, "token": _token(req.name),
            "note": "demo 认证，仅用于原型联调"}


@app.post("/auth/login", tags=["auth"])
def login(req: schemas.LoginReq):
    if store.get_user(req.name) is None:
        raise HTTPException(404, "用户不存在，请先注册")
    return {"name": req.name, "token": _token(req.name)}


# ---------------- 子路由 ----------------
from .battles import router as battles_router  # noqa: E402
from .batch import router as batch_router      # noqa: E402
from .train import router as train_router      # noqa: E402
app.include_router(battles_router)
app.include_router(batch_router)
app.include_router(train_router)


@app.get("/", tags=["meta"])
def root():
    return {"service": "PokéLab AI API", "version": "0.1.0",
            "docs": "/docs", "openapi": "/openapi.json",
            "compliance": "Unofficial community project · Non-commercial · "
                          "Pokémon © Nintendo/Creatures/GAME FREAK/TPC"}
