# -*- coding: utf-8 -*-
"""文档计数守卫 —— 「计数类文档必须实证」

背景（历史痛点）
----------------
README / CONTRIBUTING 里的**测试项数**、**卡池覆盖数**这类计数，已经**失真 3 次**：
它们是手写的，改完代码没人回头数，于是文档与实现长期不一致（对外承诺失真）。
本脚本把「实证」变成 CI 里的一条硬检查：

  · 测试项数：直接数各包 `tests/` 下的 `def test_*`，与 README 声明的数字逐项比对；
  · 口径固定：只数**仓库内真实存在的测试方法**，不跑测试（免依赖、秒级）。

用法::

    python scripts/check_doc_counts.py            # 校验（退出码 0 通过 / 1 不一致）
    python scripts/check_doc_counts.py --print    # 只打印实测值，供人工同步文档

为什么不做成「自动改写 README」
------------------------------
自动改写会让**人为笔误**也一并被写进文档（守卫变成橡皮图章）。这里选择
「只校验、报错时打印实测值」，由人决定怎么改 —— 守住的是**一致性**而非省事。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
README = REPO / "README.md"

# 包名 → 测试目录（与 CI 的 4 个测试 job 一一对应）
PKG_DIRS = {
    "engine": REPO / "packages" / "engine" / "tests",
    "agent": REPO / "packages" / "agent" / "tests",
    "api": REPO / "apps" / "api" / "tests",
    "gym": REPO / "packages" / "gym" / "tests",
}

_DEF_TEST = re.compile(r"^\s*(?:async\s+)?def (test_[A-Za-z0-9_]+)\s*\(", re.M)


def count_tests() -> dict:
    """各包 `def test_*` 计数（实证）"""
    out = {}
    for pkg, d in PKG_DIRS.items():
        n = 0
        if d.exists():
            for f in sorted(d.rglob("*.py")):
                n += len(_DEF_TEST.findall(f.read_text(encoding="utf-8")))
        out[pkg] = n
    return out


def parse_readme(text: str) -> dict:
    """从 README 抽出声明的数字（正则刻意写紧，格式一改就报「解析不到」）"""
    claims = {}
    m = re.search(r"结算与机制测试\s*\*\*(\d+)\*\*\s*项", text)
    claims["engine_line"] = int(m.group(1)) if m else None
    m = re.search(r"全仓\s*\*\*(\d+)\*\*\s*项测试", text)
    claims["total"] = int(m.group(1)) if m else None
    m = re.search(r"（engine\s*(\d+)\s*·\s*agent\s*(\d+)\s*·\s*api\s*(\d+)\s*"
                  r"·\s*gym\s*(\d+)）", text)
    if m:
        claims["breakdown"] = {k: int(v) for k, v in
                               zip(("engine", "agent", "api", "gym"),
                                   m.groups())}
    else:
        claims["breakdown"] = None
    return claims


def main() -> int:
    ap = argparse.ArgumentParser(description="文档计数守卫（测试项数）")
    ap.add_argument("--print", action="store_true", dest="just_print",
                    help="只打印实测值，不校验")
    args = ap.parse_args()

    actual = count_tests()
    total = sum(actual.values())
    if args.just_print:
        print(f"实测：engine {actual['engine']} · agent {actual['agent']} · "
              f"api {actual['api']} · gym {actual['gym']} · "
              f"全仓 {total}")
        return 0

    if not README.exists():
        print(f"[error] 找不到 {README}", file=sys.stderr)
        return 2
    claims = parse_readme(README.read_text(encoding="utf-8"))

    errs = []
    if claims["engine_line"] is None or claims["total"] is None \
            or claims["breakdown"] is None:
        errs.append("README 的测试计数行**格式变了**，守卫无法解析 —— "
                    "请保持「结算与机制测试 **N** 项」/「全仓 **N** 项测试」/"
                    "「（engine N · agent N · api N · gym N）」的写法。")
    else:
        if claims["engine_line"] != actual["engine"]:
            errs.append(f"引擎测试项数：README 写 {claims['engine_line']}，"
                        f"实测 {actual['engine']}")
        if claims["total"] != total:
            errs.append(f"全仓测试项数：README 写 {claims['total']}，"
                        f"实测 {total}")
        for k, v in claims["breakdown"].items():
            if v != actual.get(k):
                errs.append(f"{k} 测试项数：README 写 {v}，实测 {actual.get(k)}")

    if errs:
        print("::error::文档计数与实现不一致（计数类文档必须实证）")
        for e in errs:
            print(f"  - {e}")
        print(f"\n实测值：engine {actual['engine']} · agent {actual['agent']} · "
              f"api {actual['api']} · gym {actual['gym']} · 全仓 {total}")
        print("修正 README 第 7–8 行的数字后重跑本脚本。")
        return 1
    print(f"OK: 文档测试计数与实现一致（engine {actual['engine']} · "
          f"agent {actual['agent']} · api {actual['api']} · "
          f"gym {actual['gym']} · 全仓 {total}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
