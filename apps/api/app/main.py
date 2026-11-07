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
from .library import router as library_router  # noqa: E402
from .compliance import router as compliance_router  # noqa: E402
from .llm_admin import router as llm_admin_router  # noqa: E402
from .llm_admin import install_provider  # noqa: E402
app.include_router(battles_router)
app.include_router(batch_router)
app.include_router(train_router)
app.include_router(decks_router)
app.include_router(stats_router)
app.include_router(library_router)
app.include_router(compliance_router)
app.include_router(llm_admin_router)

# 把「后台保存的云端模型配置」注入 agent 的 LLM 客户端（优先级：后台 → 环境变量）。
# 启动时执行一次即可：此后所有 LLM 调用（K2 效果解析 / 对战解说）自动使用后台配置，
# 且后台 PUT 保存后立即重新注入 → 无需重启、无需改环境变量。
install_provider()


@app.get("/", tags=["meta"])
def root():
    return {"service": "PokéLab AI API", "version": "0.1.0",
            "docs": "/docs", "openapi": "/openapi.json",
            "web": "/app（若 apps/web/dist 已构建）",
            "compliance": "Unofficial community project · Non-commercial · "
                          "Pokémon © Nintendo/Creatures/GAME FREAK/TPC",
            "compliance_detail": "/compliance（6.1 内容合规 · 6.2 数据隐私）",
            "ai_notice": "AI 生成的效果解析与对战解说均带显著标识"
                         "（ai_generated / ai_notice 字段）"}


# ---------------- 前端静态托管（apps/web 构建产物，同源部署） ----------------
# 自动挑选**最新构建**的 dist*（受限环境对已存在文件写保护 → 用
# VITE_OUTDIR=dist2/dist3/dist4… 重建后无需改代码即可生效）
def _pick_web_dist():
    """挑选**最新构建**的 dist*（按 index.html 的 mtime，而非目录 mtime）

    真 bug 修复：原先按目录 mtime 取最大，但 Windows 上**覆盖已存在的
    index.html 不会更新父目录 mtime** → 重新 `vite build` 到同一个 dist 后，
    服务仍会挑到更早的 dist{N}，表现为「改了前端没生效」（且不易察觉）。
    改按 index.html 自身的 mtime 判定，行为与直觉一致。
    """
    root = _REPO / "apps" / "web"
    cands = [d for d in root.glob("dist*")
             if (d / "index.html").is_file()]
    if not cands:
        return None
    return max(cands, key=lambda d: (d / "index.html").stat().st_mtime)


_WEB_DIST = _pick_web_dist()
if _WEB_DIST is not None:
    from fastapi.responses import FileResponse  # noqa: E402
    from fastapi.staticfiles import StaticFiles  # noqa: E402

    _WEB_ROOT = _WEB_DIST.resolve()

    @app.get("/app", include_in_schema=False)
    @app.get("/app/{full_path:path}", include_in_schema=False)
    def _spa_entry(full_path: str = ""):
        """前端单页应用入口（含 SPA **深链接回退**）

        为什么不再只靠 ``StaticFiles(html=True)``：前端已改成 history 路由，
        ``GET /app/battles/<id>`` 这种地址在磁盘上没有同名文件，StaticFiles 会
        直接 404 —— 表现为「地址栏能进，一刷新就白屏」，分享出去的深链接等于废的。

        规则（顺序不能颠倒）：
          ① 命中磁盘上的真实文件（``assets/*.js|css``）→ 原样返回；
          ② 带点后缀的路径仍按真 404 处理：**绝不能**把 ``/app/assets/x.js``
             回退成 index.html —— 浏览器会把 HTML 当 JS 执行（Mismatched tag），
             而且真正的构建产物缺失会被这条回退掩盖，排查成本极高；
          ③ 其余路径 → index.html，由前端路由自己解析。

        路径穿越（``/app/../secret``）由 ``_WEB_ROOT`` 前缀校验挡掉。
        """
        if full_path:
            target = (_WEB_ROOT / full_path).resolve()
            if target.is_file() and str(target).startswith(str(_WEB_ROOT)):
                return FileResponse(target)
            if Path(full_path).suffix:
                raise HTTPException(404, "Not Found")
        return FileResponse(_WEB_ROOT / "index.html")

    # 兜底挂载（HEAD / 目录索引等仍走 StaticFiles；GET 已被上面的路由接管）
    app.mount("/app", StaticFiles(directory=str(_WEB_DIST), html=True),
              name="web")
