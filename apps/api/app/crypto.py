# -*- coding: utf-8 -*-
"""数据库敏感字段加密（合规 6.2「数据库存储加密」）

设计
----
- **可选启用**：设置 ``POKELAB_FIELD_KEY`` 或存在密钥文件即启用；未启用时
  ``encrypt()`` 原样返回（不破坏既有数据），``status()`` 如实报告 ``enabled=False``；
- **可辨识前缀**：密文形如 ``enc:v1:<fernet-token>``，故**混合状态**（部分字段加密、
  部分历史明文）可安全读取——``decrypt()`` 对明文原样返回；
- **依赖降级**：``cryptography`` 缺失时可用性为 False，写库不报错但会明确告警，
  由 ``/compliance`` 暴露真实状态（不假装已加密）；
- 存储位置：``POKELAB_FIELD_KEY_FILE`` → 否则 ``<DATA_DIR>/.field_key``（0600 尽力而为）。

字段级加密用于**用户内容**（如卡表原文 ``decks.deck_text_raw``）。口令不入此列
（已用 PBKDF2 单向哈希，无需可逆加密）。
"""
from __future__ import annotations

import base64
import os
import secrets
import stat
from pathlib import Path
from typing import Optional

from . import store

PREFIX = "enc:v1:"
_KEY_CACHE: dict = {"key": None, "source": None}


def _key_file() -> Path:
    p = os.environ.get("POKELAB_FIELD_KEY_FILE")
    if p:
        return Path(p)
    return Path(store.DATA_DIR) / ".field_key"


def _read_or_create_key() -> tuple[Optional[bytes], str]:
    """密钥来源优先级：env → 密钥文件（不存在则生成）"""
    env = os.environ.get("POKELAB_FIELD_KEY", "").strip()
    if env:
        try:
            raw = base64.urlsafe_b64decode(env.encode())
            if len(raw) == 32:
                return raw, "env"
        except Exception:                                    # noqa: BLE001
            pass
        return env.encode()[:32].ljust(32, b"0"), "env(raw)"
    f = _key_file()
    try:
        if f.exists():
            data = f.read_text(encoding="utf-8").strip()
            raw = base64.urlsafe_b64decode(data.encode())
            if len(raw) == 32:
                return raw, f"file:{f.name}"
        else:
            raw = secrets.token_bytes(32)
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(base64.urlsafe_b64encode(raw).decode(), encoding="utf-8")
            try:
                os.chmod(f, stat.S_IRUSR | stat.S_IWUSR)     # 0600（Windows 尽力）
            except Exception:                                # noqa: BLE001
                pass
            return raw, f"file-new:{f.name}"
    except Exception:                                        # noqa: BLE001
        return None, "unavailable"
    return None, "unavailable"


def _fernet():
    """惰性构造 Fernet；不可用返回 None（调用方降级）"""
    global _KEY_CACHE
    try:
        from cryptography.fernet import Fernet        # noqa: WPS433
    except Exception:                                    # noqa: BLE001
        _KEY_CACHE = {"key": None, "source": "cryptography-missing"}
        return None
    if _KEY_CACHE["key"] is None:
        raw, src = _read_or_create_key()
        if raw is None:
            _KEY_CACHE = {"key": None, "source": src}
            return None
        _KEY_CACHE = {"key": raw, "source": src}
    return Fernet(base64.urlsafe_b64encode(_KEY_CACHE["key"]))


def available() -> bool:
    """cryptography 可用且已取得密钥"""
    return _fernet() is not None


def enabled() -> bool:
    """显式启用开关：``POKELAB_FIELD_ENC=1``（默认 0，避免破坏既有明文数据）"""
    return str(os.environ.get("POKELAB_FIELD_ENC", "0")).strip().lower() in (
        "1", "true", "yes", "on")


def is_encrypted(value) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def encrypt(text) -> str:
    """加密用户内容字段；未启用/不可用/空值 → 原样返回"""
    if text is None:
        return None                                          # type: ignore[return-value]
    if not enabled() or is_encrypted(text):
        return text
    f = _fernet()
    if f is None:
        return text
    try:
        tok = f.encrypt(str(text).encode("utf-8")).decode()
        return PREFIX + tok
    except Exception:                                        # noqa: BLE001
        return text


def decrypt(text) -> Optional[str]:
    """解密；非密文原样返回（兼容历史明文）"""
    if text is None:
        return None
    if not is_encrypted(text):
        return text
    f = _fernet()
    if f is None:
        return "［无法解密：cryptography 不可用］"
    try:
        from cryptography.fernet import InvalidToken      # noqa: WPS433
        try:
            return f.decrypt(text[len(PREFIX):].encode()).decode("utf-8")
        except (InvalidToken, Exception):                 # noqa: BLE001
            return "［无法解密：密钥不匹配］"
    except Exception:                                     # noqa: BLE001
        return "［无法解密：依赖缺失］"


def status() -> dict:
    """加密现状（供 /compliance 如实展示）"""
    avail = available()
    return {
        "available": avail,
        "enabled": enabled() and avail,
        "switch_env": "POKELAB_FIELD_ENC=1",
        "key_source": _KEY_CACHE.get("source") or "unresolved",
        "key_file": str(_key_file()),
        "algorithm": "Fernet (AES-128-CBC + HMAC-SHA256)",
        "prefix": PREFIX,
        "scope": ["decks.deck_text_raw（用户卡表原文）"],
        "mixed_safe": True,
        "note": ("启用后新写入的敏感字段加密存储；历史明文仍可读（混合状态安全）。"
                 "口令使用 PBKDF2 单向哈希，不在此列。"),
    }


def encrypt_secret(text) -> Optional[str]:
    """**无条件**加密凭据（如云端模型 API Key）

    与 ``encrypt()`` 的差别：后者受 ``POKELAB_FIELD_ENC`` 开关控制（用户内容字段，
    默认关以兼容历史明文）；而**凭据类**必须始终加密——开关忘记打开就把 API Key
    明文写进数据库，属不可接受的失误。依赖不可用时返回 None（调用方应拒绝落库）。
    """
    if text is None or text == "":
        return None
    if is_encrypted(text):
        return text
    f = _fernet()
    if f is None:
        return None
    try:
        return PREFIX + f.encrypt(str(text).encode("utf-8")).decode()
    except Exception:                                        # noqa: BLE001
        return None


def encrypt_secret(text) -> Optional[str]:
    """**无条件**加密凭据（如云端模型 API Key）

    与 ``encrypt()`` 的差别：后者受 ``POKELAB_FIELD_ENC`` 开关控制（用户内容字段，
    默认关以兼容历史明文）；而**凭据类**必须始终加密——开关忘记打开就把 API Key
    明文写进数据库，属不可接受的失误。依赖不可用时返回 None（调用方应拒绝落库）。
    """
    if text is None or text == "":
        return None
    if is_encrypted(text):
        return text
    f = _fernet()
    if f is None:
        return None
    try:
        return PREFIX + f.encrypt(str(text).encode("utf-8")).decode()
    except Exception:                                        # noqa: BLE001
        return None
