# -*- coding: utf-8 -*-
"""PokéLab AI API · 认证与授权（真 JWT + PBKDF2 口令哈希 + RBAC）

- 口令：PBKDF2-HMAC-SHA256（200k 迭代，16B 随机盐），存储格式
  `pbkdf2_sha256$<iter>$<salt_hex>$<hash_hex>`（无明文）
- 令牌：HS256 JWT（sub/role/iat/exp）；密钥优先 env `POKELAB_JWT_SECRET`，
  否则写入 `apps/api/data/.jwt_secret`（0600 尽力，首次自动生成，gitignore）
- 角色：admin / user / guest；guest=无令牌访问（只读受限），
  设置 env `POKELAB_REQUIRE_AUTH=1` 可强制登录（无令牌 401）
- owner 隔离：任务/对局按 owner 校验，跨用户访问 403（admin 例外）
"""
from __future__ import annotations
import hashlib
import hmac
import os
import secrets
import time
from typing import Optional

import jwt
from fastapi import Depends, Header, HTTPException

from . import store

SECRET_FILE = store.DATA_DIR / ".jwt_secret"      # 与 DB 同目录（可写位置）
ALGO = "HS256"
TOKEN_TTL_H = int(os.environ.get("POKELAB_TOKEN_TTL_H", "24"))
REQUIRE_AUTH = os.environ.get("POKELAB_REQUIRE_AUTH", "0") == "1"


def _secret() -> str:
    env = os.environ.get("POKELAB_JWT_SECRET")
    if env:
        return env
    if SECRET_FILE.exists():
        return SECRET_FILE.read_text(encoding="utf-8").strip()
    SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    s = secrets.token_urlsafe(48)
    SECRET_FILE.write_text(s, encoding="utf-8")
    try:
        os.chmod(SECRET_FILE, 0o600)
    except Exception:
        pass
    return s


# ---------------- 口令 ----------------
def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 200_000)
    return f"pbkdf2_sha256$200000${salt.hex()}${dk.hex()}"


def verify_password(pw: str, stored: Optional[str]) -> bool:
    if not stored:
        return False
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode(),
                                 bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


# ---------------- JWT ----------------
def create_token(name: str, role: str) -> str:
    now = int(time.time())
    return jwt.encode({"sub": name, "role": role, "iat": now,
                       "exp": now + TOKEN_TTL_H * 3600},
                      _secret(), algorithm=ALGO)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, _secret(), algorithms=[ALGO])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "登录已过期，请重新登录")
    except Exception:
        raise HTTPException(401, "无效令牌")


class Principal:
    def __init__(self, name: str, role: str):
        self.name = name
        self.role = role

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def __repr__(self) -> str:                    # pragma: no cover
        return f"Principal({self.name},{self.role})"


def user_from_token(token: str) -> Principal:
    """从 JWT 字符串解析身份（供 SSE 等无法设置 header 的场景 ?token=）"""
    payload = decode_token(token)
    return Principal(payload["sub"], payload.get("role", "user"))


def current_user(authorization: str = Header(default="")) -> Principal:
    """依赖：解析 Bearer JWT → Principal；无令牌 = guest（除非强制登录）"""
    if authorization.startswith("Bearer "):
        payload = decode_token(authorization[7:].strip())
        return Principal(payload["sub"], payload.get("role", "user"))
    if REQUIRE_AUTH:
        raise HTTPException(401, "需要登录（POKELAB_REQUIRE_AUTH=1）")
    return Principal("guest", "guest")


def require_roles(*roles: str):
    """依赖工厂：要求指定角色（admin 恒允）

    注意：内部函数不写返回注解——FastAPI 会把 `-> Principal` 误当作
    response_model 校验（Principal 非 pydantic 模型）。
    """
    def dep(p: Principal = Depends(current_user)):
        if p.role != "admin" and p.role not in roles:
            raise HTTPException(403, f"需要角色 {'/'.join(roles)}（当前 {p.role}）")
        return p
    return dep


def ensure_owner(owner: str, p: Principal) -> None:
    """资源归属校验：跨用户访问 → 403（admin 例外）"""
    if p.is_admin or owner == p.name:
        return
    raise HTTPException(403, "无权访问他人资源")
