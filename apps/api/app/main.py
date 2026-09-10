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
import sys
from pathlib import Path

_APP_DIR = Path(__file__).resolve()
_REPO = _APP_DIR.parents[3]                       # apps/api/app → pokelab-ai
for pkg in ("engine", "agent"):
    sys.path.insert(0, str(_REPO / "packages" / pkg))

from fastapi import FastAPI, Depends, HTTPException, Query  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from . import store, schemas, auth  # noqa: E402

app = FastAPI(
    title="PokéLab AI API",
    version="0.1.0",
    description="宝可梦卡牌 AI 对战智能体后端服务（自研引擎 + AI 决策链路封装）。"
                "Unofficial fan project · Non-commercial · 无官方卡图素材（PRD C1/C2）。",
)
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])


# ---------------- auth（真 JWT + PBKDF2 + RBAC，见 auth.py） ----------------
@app.post("/auth/register", tags=["auth"])
def register(req: schemas.RegisterReq):
    if store.get_user(req.name) is not None:
        raise HTTPException(409, "用户名已存在")
    role = "admin" if store.count_users() == 0 else "user"  # 首个注册者为管理员
    store.create_user(req.name, auth.hash_password(req.password), role)
    return {"name": req.name, "role": role,
            "token": auth.create_token(req.name, role)}


@app.post("/auth/login", tags=["auth"])
def login(req: schemas.LoginReq):
    u = store.get_user(req.name)
    if not u or not auth.verify_password(req.password, u.get("password_hash")):
        raise HTTPException(401, "用户名或密码错误")
    return {"name": u["name"], "role": u["role"],
            "token": auth.create_token(u["name"], u["role"])}


@app.get("/auth/me", tags=["auth"])
def me(p: auth.Principal = Depends(auth.current_user)):
    return {"name": p.name, "role": p.role,
            "is_admin": p.is_admin}


@app.get("/admin/users", tags=["admin"])
def admin_users(p: auth.Principal = Depends(auth.require_roles("admin"))):
    return {"users": store.list_users()}


@app.patch("/admin/users/{name}", tags=["admin"])
def admin_set_role(name: str, role: str = Query("user"),
                   p: auth.Principal = Depends(auth.require_roles("admin"))):
    if role not in ("admin", "user"):
        raise HTTPException(422, "role 仅支持 admin/user")
    if store.get_user(name) is None:
        raise HTTPException(404, "用户不存在")
    store.set_user_role(name, role)
    return {"name": name, "role": role}


# ---------------- 子路由 ----------------
from .battles import router as battles_router  # noqa: E402
from .batch import router as batch_router      # noqa: E402
from .train import router as train_router      # noqa: E402
from .decks import router as decks_router      # noqa: E402
from .stats import router as stats_router      # noqa: E402
app.include_router(battles_router)
app.include_router(batch_router)
app.include_router(train_router)
app.include_router(decks_router)
app.include_router(stats_router)


@app.get("/", tags=["meta"])
def root():
    return {"service": "PokéLab AI API", "version": "0.1.0",
            "docs": "/docs", "openapi": "/openapi.json",
            "web": "/app（若 apps/web/dist 已构建）",
            "compliance": "Unofficial community project · Non-commercial · "
                          "Pokémon © Nintendo/Creatures/GAME FREAK/TPC"}


# ---------------- 前端静态托管（apps/web 构建产物，同源部署） ----------------
# 优先 dist2（Windows 上 dist 偶发被进程占用时用 VITE_OUTDIR=dist2 重建），回退 dist
_WEB_DIST = next((p for p in (_REPO / "apps" / "web" / "dist2",
                              _REPO / "apps" / "web" / "dist") if p.exists()), None)
if _WEB_DIST is not None:
    from fastapi.staticfiles import StaticFiles  # noqa: E402
    app.mount("/app", StaticFiles(directory=str(_WEB_DIST), html=True),
              name="web")
