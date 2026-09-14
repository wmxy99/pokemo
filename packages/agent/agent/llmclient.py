# -*- coding: utf-8 -*-
"""PokéLab Agent · LLM 客户端（OpenAI 兼容）与**运行时配置注入点**

设计动机
--------
`packages/agent` 是**独立包**，不得依赖 `apps/api`（否则 agent 无法单独安装、单独
测试）。但「后台可视化配置云端模型」要求配置能落到数据库——两条需求用一个
**注入点**解开：由 apps/api 在启动时把「读配置」的函数注入进来，agent 只依赖抽象。

配置优先级
----------
**运行时注入（后台保存，密钥加密存储）→ 环境变量兜底**。
环境变量兜底保证 CI / 容器 / 无界面部署仍可纯 env 运行；后台未配置时行为与从前一致。

合规（6.2）
-----------
所有外发都必须经本模块：调用前 `privacy.check_endpoint`（本地模式禁止外部端点）→
`privacy.desensitize`（脱敏）→ 调用后 `privacy.audit`（审计，**不含原文**）。
把「脱敏 + 审计」收在本模块内，避免各个调用点各写一遍而漏掉。
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Callable, Optional

DEFAULT_TIMEOUT = 60.0
DEFAULT_MODEL = "deepseek-chat"


class LLMConfigError(RuntimeError):
    """LLM 未配置或不可用（由调用方决定是回退还是报错）"""


# 后端的**运行时配置提供者**（由 apps/api 注入；返回 None 表示未配置）
_config_provider: Optional[Callable[[], Optional[dict]]] = None


def set_config_provider(fn: Optional[Callable[[], Optional[dict]]]) -> None:
    """注入运行时配置提供者（apps/api 启动时调用一次）

    ``fn() -> Optional[dict]``，dict 形如
    ``{"base": "...", "key": "...", "model": "...", "timeout": 60.0}``；
    返回 None / 抛异常都视为「未配置」，自动回退环境变量。
    """
    global _config_provider
    _config_provider = fn


def clear_config_provider() -> None:
    global _config_provider
    _config_provider = None


def provider_active() -> bool:
    return _config_provider is not None


def _from_env() -> Optional[dict]:
    base = (os.environ.get("POKELAB_LLM_BASE") or "").rstrip("/")
    if not base:
        return None
    return {"base": base,
            "key": os.environ.get("POKELAB_LLM_KEY", ""),
            "model": os.environ.get("POKELAB_LLM_MODEL", DEFAULT_MODEL),
            "timeout": float(os.environ.get("POKELAB_LLM_TIMEOUT",
                                            str(DEFAULT_TIMEOUT)))}


def _from_provider() -> Optional[dict]:
    if _config_provider is None:
        return None
    try:
        cfg = _config_provider()
    except Exception:                              # noqa: BLE001
        # 注入方出错（如 DB 不可用）不应让整个调用链崩掉 → 回退 env
        return None
    if not cfg or not (cfg.get("base") or "").strip():
        return None
    return {"base": str(cfg["base"]).rstrip("/"),
            "key": cfg.get("key") or "",
            "model": cfg.get("model") or DEFAULT_MODEL,
            "timeout": float(cfg.get("timeout") or DEFAULT_TIMEOUT)}


def source() -> str:
    """当前配置来源：``runtime``（后台配置）/ ``env`` / ``none``"""
    if _from_provider() is not None:
        return "runtime"
    if _from_env() is not None:
        return "env"
    return "none"


def config() -> dict:
    """取生效配置（运行时优先，env 兜底）；未配置 → 抛 LLMConfigError"""
    cfg = _from_provider() or _from_env()
    if cfg is None:
        raise LLMConfigError(
            "未配置云端模型（后台「模型配置」或环境变量 POKELAB_LLM_BASE）")
    return cfg


def chat(messages: list, *, temperature: float = 0.0,
         timeout: Optional[float] = None, purpose: str = "llm_call") -> dict:
    """OpenAI 兼容 ``/chat/completions`` 调用（含合规三步：校验→脱敏→审计）

    返回 ``{"content", "endpoint", "model", "privacy", "elapsed_ms", "usage"}``。

    **调用方必须**把返回的 ``ai_generated`` 语义透传给用户（合规 C4）：本模块只
    负责「调用 + 合规」，标识由各调用点的返回值承担。
    """
    from . import privacy as _privacy
    cfg = config()
    ep = _privacy.check_endpoint(cfg["base"])          # 1) 端点校验（本地模式拦截）
    clean_msgs, rep = [], {"total": 0, "chars_in": 0, "chars_out": 0, "hits": {}}
    for m in messages:                                 # 2) 消息级脱敏
        text, r = _privacy.desensitize(str(m.get("content") or ""))
        clean_msgs.append({"role": m.get("role", "user"), "content": text})
        rep["total"] += r.get("total", 0)
        rep["chars_in"] += r.get("chars_in", 0)
        rep["chars_out"] += r.get("chars_out", 0)
        for k, v in (r.get("hits") or {}).items():
            rep["hits"][k] = rep["hits"].get(k, 0) + v
    body = json.dumps({"model": cfg["model"], "messages": clean_msgs,
                       "temperature": temperature, "stream": False},
                      ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        cfg["base"] + "/chat/completions", data=body,
        headers={"Content-Type": "application/json; charset=utf-8",
                 **({"Authorization": f"Bearer {cfg['key']}"}
                    if cfg["key"] else {})})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout or cfg["timeout"]) as r:
            payload = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        _privacy.audit(purpose, host=ep["host"], external=ep.get("external"),
                       model=cfg["model"], ok=False, error=type(e).__name__,
                       elapsed_ms=int((time.time() - t0) * 1000))
        raise LLMConfigError(f"云端模型请求失败：{e}") from e
    except json.JSONDecodeError as e:
        _privacy.audit(purpose, host=ep["host"], external=ep.get("external"),
                       model=cfg["model"], ok=False,
                       error="bad_json_response",
                       elapsed_ms=int((time.time() - t0) * 1000))
        raise LLMConfigError("云端模型返回非 JSON（端点是否正确？）") from e
    elapsed = int((time.time() - t0) * 1000)
    choices = payload.get("choices") or [{}]
    content = (choices[0].get("message") or {}).get("content") or ""
    usage = payload.get("usage") or {}
    # 3) 审计（仅端点/模型/字符数/脱敏计数，**不含原文**）
    _privacy.audit(purpose, host=ep["host"], external=ep.get("external"),
                   model=cfg["model"], ok=True,
                   prompt_chars=rep["chars_in"], sent_chars=rep["chars_out"],
                   desensitized=rep["total"], response_chars=len(content),
                   elapsed_ms=elapsed)
    return {"content": content, "endpoint": ep, "model": cfg["model"],
            "privacy": rep, "elapsed_ms": elapsed, "usage": usage,
            "source": source()}
