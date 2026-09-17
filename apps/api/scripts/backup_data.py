#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""PokéLab AI · 运行数据备份（`~/.pokelab/`）

**为什么需要它**：运行库**不在仓库内**（`.gitignore` 忽略仓库内同名文件），
真实数据在数据目录（优先级：`POKELAB_DATA_DIR` → `~/.pokelab` → 临时目录 → `apps/api/data`）。
其中 **`.field_key` 一旦丢失，`settings` 表里已加密的 LLM 密钥将无法解密**（不可再生）。

用法::

    python apps/api/scripts/backup_data.py                 # 备份到 ~/.pokelab_backup/<时间戳>/
    python apps/api/scripts/backup_data.py --out D:/bak    # 指定目录
    python apps/api/scripts/backup_data.py --keep 5        # 只保留最近 5 份（默认 10）
    python apps/api/scripts/backup_data.py --check         # 只体检，不备份

退出码：0 成功 / 2 无数据目录 / 3 备份后自检失败。
"""
from __future__ import annotations
import argparse
import shutil
import sqlite3
import sys
import time
from pathlib import Path

# 复用 API 的数据目录选择逻辑（避免两处规则漂移）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    from app.store import DATA_DIR as _DATA_DIR, DB_PATH as _DB_PATH
except Exception:                                    # noqa: BLE001
    _DATA_DIR = _DB_PATH = None

# 必需（丢失即不可再生或数据丢失）
CRITICAL = (".field_key", ".jwt_secret")
# 重要（可重建但成本高）
IMPORTANT = ("api.db", "llm_audit.jsonl", "train_jobs")


def _sqlite_backup(src: Path, dst: Path) -> None:
    """用 SQLite 官方 backup API 复制 —— 对**运行中的库**也安全（不用裸拷文件）"""
    con = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    try:
        out = sqlite3.connect(str(dst))
        try:
            con.backup(out)
        finally:
            out.close()
    finally:
        con.close()


def _human(n: int) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} TB"


def main() -> int:
    ap = argparse.ArgumentParser(description="备份 PokéLab 运行数据")
    ap.add_argument("--data-dir", default=None, help="数据目录（默认走 API 的自动选择）")
    ap.add_argument("--out", default=None, help="备份根目录（默认 <数据目录>_backup）")
    ap.add_argument("--keep", type=int, default=10, help="保留最近 N 份（默认 10；0=不清理）")
    ap.add_argument("--check", action="store_true", help="只体检，不备份")
    a = ap.parse_args()

    data = Path(a.data_dir) if a.data_dir else (_DATA_DIR or Path.home() / ".pokelab")
    if not data.is_dir():
        print(f"[backup] 数据目录不存在：{data}", file=sys.stderr)
        return 2

    print(f"[backup] 数据目录：{data}")
    print(f"[backup] 运行库  ：{_DB_PATH or (data / 'api.db')}")

    # ---- 体检 ----
    missing = [n for n in CRITICAL if not (data / n).exists()]
    for n in missing:
        print(f"  ⚠ **缺少 {n}** —— 若已启用字段加密，历史密钥将不可解密；"
              f"若未加密则是正常状态（首启用时会生成）")
    for n in IMPORTANT:
        p = data / n
        if p.exists():
            size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.is_dir() else p.stat().st_size
            print(f"  ✓ {n:<18} {_human(size)}")
        else:
            print(f"  · {n:<18} 不存在")
    if a.check:
        return 0

    # ---- 备份 ----
    out_root = Path(a.out) if a.out else data.parent / (data.name + "_backup")
    stamp = time.strftime("%Y%m%d_%H%M%S")
    dst = out_root / stamp
    dst.mkdir(parents=True, exist_ok=False)
    print(f"[backup] 目标目录：{dst}")

    copied = []
    for n in ("api.db",):
        src = data / n
        if src.is_file():
            _sqlite_backup(src, dst / n)             # 在线安全复制
            copied.append(n)
    for n in CRITICAL + ("llm_audit.jsonl",):
        src = data / n
        if src.is_file():
            shutil.copy2(src, dst / n)
            copied.append(n)
    tj = data / "train_jobs"
    if tj.is_dir():
        shutil.copytree(tj, dst / "train_jobs")
        copied.append("train_jobs/")
    print(f"[backup] 已复制 {len(copied)} 项：{'、'.join(copied)}")

    # ---- 自检（拒绝产出"看起来成功其实为空"的备份）----
    fail = []
    dbk = dst / "api.db"
    if (data / "api.db").is_file():
        if not dbk.is_file() or dbk.stat().st_size == 0:
            fail.append("api.db 缺失或为空")
        else:
            try:
                c = sqlite3.connect(f"file:{dbk}?mode=ro", uri=True)
                n = c.execute("select count(*) from users").fetchone()[0]
                tabs = [r[0] for r in c.execute("select name from sqlite_master where type='table'")]
                c.close()
                print(f"[backup] 自检：表 {len(tabs)} 张 · users {n} 行 ✓")
            except Exception as e:                    # noqa: BLE001
                fail.append(f"备份库不可读：{e}")
    if fail:
        print(f"[backup] ❌ 自检失败：{fail}", file=sys.stderr)
        return 3

    # ---- 轮转 ----
    if a.keep > 0:
        snaps = sorted([d for d in out_root.iterdir() if d.is_dir()], reverse=True)
        for old in snaps[a.keep:]:
            print(f"[backup] 轮转删除旧份：{old.name}")
            shutil.rmtree(old, ignore_errors=True)

    print(f"[backup] ✅ 完成：{dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
