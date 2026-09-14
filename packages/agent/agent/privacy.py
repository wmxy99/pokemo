# -*- coding: utf-8 -*-
"""数据隐私保护（合规 6.2 落地）

设计原则
--------
1. **本地化优先**：``POKELAB_LOCAL_ONLY=1`` 时**禁止任何外部网络调用**
   （LLM 只能指向本机/内网），确保用户数据不出本地；
2. **脱敏后再外发**：调用外部大模型前，对文本中的敏感信息（邮箱/手机/证件/
   卡号/密钥/用户名/卡组名）做脱敏替换，只发送占位符；
3. **可审计**：每次外部调用写一条审计记录（端点主机、模型、字符数、脱敏计数），
   不记录原文；``POKELAB_AUDIT=0`` 可关闭；
4. **不用于服务以外用途**：本模块只做"最小必要发送"，不落盘原始对话内容。

环境变量
--------
``POKELAB_LOCAL_ONLY``   ``1`` = 仅本地（默认 ``0``）
``POKELAB_AUDIT``        ``0`` = 关闭审计日志（默认开启）
``POKELAB_AUDIT_LOG``    审计日志路径（默认 ``~/.pokelab/llm_audit.jsonl``）
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

__all__ = ["desensitize", "local_only", "check_endpoint", "audit",
           "audit_path", "privacy_status", "SENSITIVE_KEYS"]

# ---------------- 脱敏规则 ----------------
# 顺序有意义：先长模式（卡号/证件），后通用模式
_RULES: list[tuple[str, re.Pattern, str]] = [
    ("email", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}"), "[邮箱]"),
    ("phone_cn", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "[手机号]"),
    ("id_cn", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"), "[证件号]"),
    ("card_no", re.compile(r"(?<!\d)\d{4}[ -]?\d{4}[ -]?\d{4}[ -]?\d{4}(?!\d)"),
     "[卡号]"),
    ("api_key", re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}\b"), "[密钥]"),
    ("bearer", re.compile(r"(?i)\b(bearer|api[_-]?key|access[_-]?token|secret)"
                          r"\b\s*[:=]?\s*\S+"), r"\1=[已遮蔽]"),
    ("win_path", re.compile(r"(?i)\b[A-Za-z]:[\\/]{1,2}(?:Users|用户)[\\/][^\s,;\"']*"),
     "[本地路径]"),
    ("user_at_host", re.compile(r"\b[A-Za-z0-9._%-]+@(?:\d{1,3}\.){3}\d{1,3}\b"),
     "[账号@主机]"),
]
# 调用方可显式传入的业务敏感词（如用户名、卡组名）
SENSITIVE_KEYS = ("user", "username", "deck", "deck_name", "owner", "email",
                  "token", "secret", "key")


def desensitize(text: str, extra: Optional[dict] = None) -> tuple[str, dict]:
    """脱敏文本中的敏感信息。

    返回 ``(脱敏后文本, 报告)``；报告含 ``counts``（各规则命中次数）、
    ``total``、``extra_hits``。**原文不落盘**。
    """
    src = "" if text is None else str(text)
    out = src
    counts: dict = {}
    for name, pat, repl in _RULES:
        out, n = pat.subn(repl, out)
        if n:
            counts[name] = counts.get(name, 0) + n
    extra_hits = 0
    if extra:
        for _, val in extra.items():
            v = (val or "").strip() if isinstance(val, str) else ""
            if len(v) >= 2 and v in out:
                out = out.replace(v, "[用户信息]")
                extra_hits += 1
    report = {
        "counts": counts,
        "total": sum(counts.values()) + extra_hits,
        "extra_hits": extra_hits,
        "chars_in": len(src),
        "chars_out": len(out),
    }
    return out, report


# ---------------- 本地模式 ----------------
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0", "host.docker.internal"}


def _truthy(v: str) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def local_only() -> bool:
    """``POKELAB_LOCAL_ONLY=1`` → 仅允许本机/内网端点（数据不出本地）"""
    return _truthy(os.environ.get("POKELAB_LOCAL_ONLY", "0"))


def is_external(url: str) -> bool:
    """判断 URL 是否指向外部（非本机）"""
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:                                    # noqa: BLE001
        return True
    if host in _LOCAL_HOSTS:
        return False
    # 私有网段视为内网
    if re.match(r"^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)", host):
        return False
    return True


class PrivacyError(RuntimeError):
    """隐私策略拒绝（如本地模式下尝试外部调用）"""


def check_endpoint(url: str) -> dict:
    """校验端点是否被允许；本地模式下外部端点直接拒绝"""
    external = is_external(url)
    info = {"url": url, "host": (urlparse(url).hostname or ""), "external": external}
    if external and local_only():
        raise PrivacyError(
            f"本地模式（POKELAB_LOCAL_ONLY=1）禁止外部调用：{info['host']}；"
            "请改用本机/内网 LLM 端点，或取消本地模式")
    return info


# ---------------- 审计 ----------------
def audit_path() -> Path:
    return Path(os.environ.get("POKELAB_AUDIT_LOG")
                or (Path.home() / ".pokelab" / "llm_audit.jsonl"))


def audit_enabled() -> bool:
    return _truthy(os.environ.get("POKELAB_AUDIT", "1"))


def audit(event: str, **fields) -> Optional[Path]:
    """追加一条审计记录（JSONL，不含原文）。失败静默（不影响主流程）"""
    if not audit_enabled():
        return None
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": event, **fields}
    p = audit_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return p
    except Exception:                                    # noqa: BLE001
        return None


def read_audit(limit: int = 100) -> dict:
    """读审计日志尾部（供 /compliance 展示与自检）"""
    p = audit_path()
    if not p.exists():
        return {"path": str(p), "exists": False, "entries": [], "total": 0}
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except Exception:                                    # noqa: BLE001
        return {"path": str(p), "exists": True, "entries": [], "total": 0,
                "error": "unreadable"}
    out = []
    for ln in lines[-limit:]:
        try:
            out.append(json.loads(ln))
        except Exception:                                # noqa: BLE001
            continue
    return {"path": str(p), "exists": True, "entries": out, "total": len(lines)}


def privacy_status() -> dict:
    """隐私措施现状（供 /compliance 机器可读声明）"""
    ap = audit_path()
    return {
        "local_only": local_only(),
        "desensitize_rules": [name for name, _, _ in _RULES],
        "audit_enabled": audit_enabled(),
        "audit_log": str(ap),
        "audit_exists": ap.exists(),
        "notes": ("外部 LLM 调用前对文本脱敏；本地模式禁止任何外部网络调用；"
                  "审计仅记录端点/字符数/脱敏计数，不记录原文"),
    }
