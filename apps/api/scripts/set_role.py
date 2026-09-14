# -*- coding: utf-8 -*-
"""账号角色管理 / **管理员引导**（CLI）

为什么需要它：`/admin/users` 全部要求 admin 权限，因此**当实例里没有任何管理员时
无法通过 API 自救**（鸡生蛋问题）。本脚本直接操作本地库，用于：

    # 查看现有管理员
    python scripts/set_role.py --list-admins

    # 把已有账号提升为管理员（最常用）
    python scripts/set_role.py --user 你的账号 --role admin

    # 新建一个管理员（口令由你指定，不落明文）
    python scripts/set_role.py --user admin --role admin --create

    # 清理测试账号（按前缀，需显式确认）
    python scripts/set_role.py --purge-prefix adm_ --yes

安全约束
--------
  · 口令经 ``auth.hash_password``（PBKDF2-SHA256，随机盐）后入库，脚本不回显；
  · ``--purge`` 属破坏性操作：必须同时给出 ``--yes``，且拒绝删除最后一个管理员。
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from apps.api.app import auth, store  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="账号角色管理 / 管理员引导")
    ap.add_argument("--user", help="账号名")
    ap.add_argument("--role", choices=["admin", "user"], default="admin")
    ap.add_argument("--create", action="store_true", help="账号不存在时新建")
    ap.add_argument("--set-password", action="store_true",
                    help="交互式重设口令（不回显）")
    ap.add_argument("--list-admins", action="store_true")
    ap.add_argument("--purge-prefix", help="删除名称以该前缀开头的账号（测试残留清理）")
    ap.add_argument("--yes", action="store_true", help="确认破坏性操作")
    a = ap.parse_args()

    if a.list_admins:
        admins = [u["name"] for u in store.list_users() if u["role"] == "admin"]
        print(f"管理员 {len(admins)} 个：{'、'.join(admins) or '（无）'}")
        return 0

    if a.purge_prefix:
        if not a.yes:
            print("[拒绝] --purge-prefix 是破坏性操作，请同时给出 --yes",
                  file=sys.stderr)
            return 2
        users = store.list_users()
        targets = [u["name"] for u in users
                   if u["name"].startswith(a.purge_prefix)]
        admins = {u["name"] for u in users if u["role"] == "admin"}
        remaining = len(admins) - len([t for t in targets if t in admins])
        if remaining <= 0:
            print("[拒绝] 该操作会删除**全部**管理员，实例将无法经 API 恢复",
                  file=sys.stderr)
            return 2
        removed = [t for t in targets if store.delete_user(t)]
        print(f"已删除 {len(removed)} 个账号（前缀 {a.purge_prefix}）："
              f"{'、'.join(removed) or '（无匹配）'}")
        return 0

    if not a.user:
        ap.error("需给出 --user（或 --list-admins / --purge-prefix）")
    exists = store.get_user(a.user) is not None

    if a.set_password:
        if not exists:
            print(f"[错误] 账号 {a.user} 不存在", file=sys.stderr)
            return 2
        pw = getpass.getpass(f"为 {a.user} 设置新口令（不回显）：")
        if len(pw) < 8:
            print("[错误] 口令至少 8 位", file=sys.stderr)
            return 2
        if pw != getpass.getpass("再输入一次确认："):
            print("[错误] 两次输入不一致", file=sys.stderr)
            return 2
        store.set_user_password(a.user, auth.hash_password(pw))
        print(f"[ok] 已重设 {a.user} 的口令")
        return 0

    if not exists and not a.create:
        print(f"[错误] 账号 {a.user} 不存在；如需新建请加 --create", file=sys.stderr)
        return 2
    if not exists:
        pw = getpass.getpass(f"为 {a.user} 设置口令（不回显）：")
        if len(pw) < 8:
            print("[错误] 口令至少 8 位", file=sys.stderr)
            return 2
        store.create_user(a.user, auth.hash_password(pw), a.role)
        print(f"[ok] 已创建 {a.user}（角色 {a.role}）")
    else:
        store.set_user_role(a.user, a.role)
        print(f"[ok] 已把 {a.user} 角色设为 {a.role}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
