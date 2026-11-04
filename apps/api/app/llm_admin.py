# -*- coding: utf-8 -*-
"""PokéLab AI API · 后台「云端模型」配置路由（admin）

职责
----
让管理员在**后台界面**配置云端大模型（OpenAI 兼容），无需改环境变量即可切换：

    GET    /admin/llm/config    当前生效配置（密钥脱敏）+ 来源 + 预设列表 + 合规状态
    PUT    /admin/llm/config    保存配置（密钥加密落库）
    DELETE /admin/llm/config    清除后台配置（回退环境变量）
    POST   /admin/llm/test      连通性测试（真实调用一次，含脱敏与审计）

合规联动（6.2 · 与 /compliance 同源）
------------------------------------
1. **本地模式硬禁**：``POKELAB_LOCAL_ONLY=1`` 时拒绝保存**外部**端点（403），
   仅允许本机/内网端点（自建 vLLM / Ollama 等，数据不出本地）；
2. **凭据必加密**：API Key 无条件 Fernet 加密后入库（不走 ``POKELAB_FIELD_ENC``
   开关）；加密不可用则**拒绝落库**而不是明文写入；
3. **脱敏 + 审计**：实际调用统一走 ``agent.llmclient.chat``，外发前脱敏、
   之后写审计（不含原文）；
4. **不泄露**：GET 只回显密钥尾 4 位；错误信息不回显密钥。
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import crypto, store
from .auth import Principal, require_roles

router = APIRouter(prefix="/admin/llm", tags=["admin"])

# settings 键（前缀统一，便于 list_settings("llm.") 审计）
K_BASE = "llm.base"
K_MODEL = "llm.model"
K_KEY = "llm.key"
K_TIMEOUT = "llm.timeout"

# 预设供应商（当前阶段只接 DeepSeek：外发主机白名单同步在 privacy.ALLOWED_EXTERNAL_HOSTS）
PRESETS = [
    {"id": "deepseek", "label": "DeepSeek",
     "base": "https://api.deepseek.com/v1", "model": "deepseek-chat",
     "note": "国内直连、中文效果好；需 API Key。K2 效果解析默认推荐。"},
]


class LLMConfigReq(BaseModel):
    base: str = Field(..., description="OpenAI 兼容端点，如 https://api.deepseek.com/v1")
    model: str = Field(..., description="模型名，如 deepseek-chat")
    key: str = Field("", description="API Key（留空=不修改已存密钥；自建服务可空）")
    timeout: float = Field(60.0, ge=5, le=600, description="超时秒数")
    keep_key: bool = Field(True, description="true=沿用已存密钥（key 留空时）")


class LLMTestReq(BaseModel):
    prompt: str = Field("回复「连通正常」四个字。",
                        description="测试用提示词（会经脱敏后外发）")
    backend: str = Field("llm", description="固定 llm；保留字段便于扩展")


# ---------------- 配置读写（供注入与路由共用） ----------------
def _mask(key: str) -> str:
    if not key:
        return ""
    tail = key[-4:] if len(key) > 4 else ""
    return f"{'*' * max(0, min(len(key), 12) - len(tail))}{tail}"


def load_runtime_config() -> dict | None:
    """读取后台配置（供 ``agent.llmclient.set_config_provider`` 注入）

    未配置后台 → 返回 None（调用方回退环境变量）。
    **本函数会被 LLM 调用链高频调用**，故只读 DB + 解密，不做任何网络/校验。
    """
    base = store.get_setting(K_BASE)
    model = store.get_setting(K_MODEL)
    if not base or not (base.get("value") or "").strip():
        return None
    key_row = store.get_setting(K_KEY)
    key = ""
    if key_row and key_row.get("value"):
        key = crypto.decrypt(key_row["value"]) or ""
        if key.startswith("［无法解密"):        # 密钥文件变更等 → 视为无密钥
            key = ""
    to = store.get_setting(K_TIMEOUT)
    try:
        timeout = float((to or {}).get("value") or 60)
    except (TypeError, ValueError):
        timeout = 60.0
    return {"base": (base["value"] or "").strip(),
            "model": (model or {}).get("value") or "deepseek-chat",
            "key": key, "timeout": timeout}


def install_provider() -> None:
    """把后台配置注入 agent 的 LLM 客户端（main.py 启动时调用一次）"""
    from agent import llmclient
    llmclient.set_config_provider(load_runtime_config)


def _compliance_state() -> dict:
    from agent import privacy
    from agent import llmclient
    return {
        "local_only": privacy.local_only(),
        "local_only_env": "POKELAB_LOCAL_ONLY=1",
        "audit_enabled": privacy.audit_enabled(),
        "audit_forced_for_external": privacy.external_audit_forced(),
        "allowed_external_hosts": sorted(privacy.ALLOWED_EXTERNAL_HOSTS),
        "audit_log": str(privacy.audit_path()),
        "encryption": crypto.status(),
        "config_source": llmclient.source(),
        "note": ("本地模式下仅允许本机/内网端点；外部外发仅允许 DeepSeek 且必须 HTTPS；"
                 "所有外发经脱敏并强制写审计（不含原文）"),
    }


@router.get("/config")
def get_config(p: Principal = Depends(require_roles("admin"))):
    """当前生效配置（密钥仅回显尾 4 位）+ 来源 + 预设 + 合规状态"""
    from agent import llmclient, privacy
    env_base = (os.environ.get("POKELAB_LLM_BASE") or "").rstrip("/")
    src = llmclient.source()
    try:
        eff = llmclient.config()
    except llmclient.LLMConfigError:
        eff = None
    rows = {r["key"]: r for r in store.list_settings("llm.")}
    return {
        "configured": eff is not None,
        "source": src,                      # runtime | env | none
        "source_label": {"runtime": "后台配置", "env": "环境变量",
                         "none": "未配置"}.get(src, src),
        "effective": None if eff is None else {
            "base": eff["base"], "model": eff["model"],
            "timeout": eff["timeout"], "key_hint": _mask(eff["key"]),
            "has_key": bool(eff["key"]),
            "external": privacy.is_external(eff["base"]),
        },
        "saved": {                          # 后台已保存的原始值（密钥脱敏）
            "base": (rows.get(K_BASE) or {}).get("value") or "",
            "model": (rows.get(K_MODEL) or {}).get("value") or "",
            "timeout": (rows.get(K_TIMEOUT) or {}).get("value") or "",
            "key_hint": _mask(crypto.decrypt(
                (rows.get(K_KEY) or {}).get("value") or "") or ""),
            "updated_by": (rows.get(K_BASE) or {}).get("updated_by") or "",
            "updated": (rows.get(K_BASE) or {}).get("updated"),
        },
        "env": {"base": env_base,
                "model": os.environ.get("POKELAB_LLM_MODEL") or ""},
        "presets": PRESETS,
        "compliance": _compliance_state(),
    }


@router.put("/config")
def put_config(req: LLMConfigReq,
               p: Principal = Depends(require_roles("admin"))):
    """保存后台配置（本地模式硬禁外部端点；密钥无条件加密落库）"""
    from agent import privacy
    base = (req.base or "").strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        raise HTTPException(422, "端点必须以 http:// 或 https:// 开头")
    model = (req.model or "").strip()
    if not model:
        raise HTTPException(422, "模型名不能为空")
    external = privacy.is_external(base)
    # 1) 本地模式硬禁外部端点（6.2：支持本地化部署 = 数据不出本地）
    if external and privacy.local_only():
        raise HTTPException(
            403, "已启用本地化部署（POKELAB_LOCAL_ONLY=1），禁止配置外部云端"
                 "端点；如需使用云端模型请先关闭该开关（或改为本机/内网端点）")
    # 1b) 外部外发白名单 + 强制 HTTPS（确保数据只到 DeepSeek、且不明文传输）
    if external:
        host = (urlparse(base).hostname or "").lower()
        if not base.startswith("https://"):
            raise HTTPException(422, "外部端点必须使用 HTTPS，拒绝明文外发")
        allowed = sorted(privacy.ALLOWED_EXTERNAL_HOSTS)
        if host not in privacy.ALLOWED_EXTERNAL_HOSTS:
            raise HTTPException(
                422, f"外发主机 {host} 不在白名单内：当前仅允许 "
                     f"{' / '.join(allowed)}（DeepSeek）")
    # 2) 密钥处理：留空=沿用已存；显式提供则无条件加密
    if req.key:
        enc = crypto.encrypt_secret(req.key)
        if enc is None:
            raise HTTPException(
                409, "密钥加密不可用（cryptography 未安装或密钥文件不可写），"
                     "拒绝明文存储；请改用环境变量 POKELAB_LLM_KEY")
        store.set_setting(K_KEY, enc, encrypted=True, updated_by=p.name)
    store.set_setting(K_BASE, base, updated_by=p.name)
    store.set_setting(K_MODEL, model, updated_by=p.name)
    store.set_setting(K_TIMEOUT, str(float(req.timeout)), updated_by=p.name)
    install_provider()                       # 立即生效，无需重启
    return {"ok": True, "source": "runtime", "external": external,
            "note": "已保存并即时生效（后台配置优先于环境变量）"}


@router.delete("/config")
def delete_config(p: Principal = Depends(require_roles("admin"))):
    """清除后台配置 → 回退环境变量（密钥一并清除）"""
    from agent import llmclient
    for k in (K_BASE, K_MODEL, K_KEY, K_TIMEOUT):
        store.delete_setting(k)
    install_provider()
    return {"ok": True, "source": llmclient.source(),
            "note": "已清除后台配置；如设置了环境变量将回退使用"}


@router.post("/test")
def test_config(req: LLMTestReq,
                p: Principal = Depends(require_roles("admin"))):
    """连通性测试：真实调用一次（经脱敏 + 审计）

    失败也返回 200 + ``ok=false``，便于后台展示原因（网络/密钥/模型名错误）。
    """
    from agent import llmclient
    from agent.privacy import PrivacyError
    try:
        res = llmclient.chat([{"role": "user", "content": req.prompt}],
                             temperature=0, timeout=20,
                             purpose="llm_test")
    except PrivacyError as e:
        raise HTTPException(403, f"隐私策略拒绝：{e}") from e
    except llmclient.LLMConfigError as e:
        return {"ok": False, "error": str(e), "hint": _hint_for(str(e))}
    return {"ok": True, "model": res["model"],
            "endpoint_host": res["endpoint"]["host"],
            "external": res["endpoint"].get("external"),
            "elapsed_ms": res["elapsed_ms"],
            "source": res.get("source"),
            "desensitized": res["privacy"].get("total", 0),
            "reply_preview": (res["content"] or "").strip()[:200],
            "audited": True}


def _hint_for(err: str) -> str:
    low = err.lower()
    if "401" in err or "403" in err or "unauthorized" in low:
        return "鉴权失败：请检查 API Key 是否正确/是否过期"
    if "404" in err:
        return "端点路径可能不对：OpenAI 兼容端点通常以 /v1 结尾"
    if "timed out" in low or "timeout" in low:
        return "连接超时：检查网络/代理，或把超时时间调大"
    if "name or service not known" in low or "getaddrinfo" in low:
        return "域名解析失败：检查端点拼写与网络"
    return "请核对端点、模型名与密钥"


@router.get("/presets")
def presets(p: Principal = Depends(require_roles("admin"))):
    """预设供应商模板（前端一键填充用）"""
    return {"presets": PRESETS}
