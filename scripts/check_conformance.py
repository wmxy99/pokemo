#!/usr/bin/env python3
"""规则一致性矩阵守卫（S6d）：`docs/RULE_CONFORMANCE.md` 的每一行都必须能验证

为什么需要它：矩阵的价值全在「可被推翻」。一份手写的「已实现」表格，三个月后
没人知道哪行还是真的 —— 而且**写错不会报错**（这正是本项目最贵的教训：
「静默错映射 > 未建模」）。所以每一行都要能被机器核对三件事：

  1. **实现点存在**：`路径::符号` 里的文件真实存在，且文件里真的出现该符号
     （函数名 / 类名 / 常量名 / 关键表达式都行 —— 只要求「能找到」，不解析 AST，
     因为矩阵要能指向任何落点，包括一行分支判断）；
  2. **测试存在**：`路径::类.方法` 两头都能在文件里找到（改名/删除即红）；
  3. **覆盖卡数可核对**：带 `tag 前缀` 的行，用 `cards_pool_ext.json` 现算一遍，
     与表里写的数字**必须一致**（数据一改、卡池一重生成，数字对不上就红）。

状态列只允许 ✅ / ⚠️ / ❌ 三档（带不带说明都行），避免出现「差不多完成」这类
没法核对的措辞。

用法：
    python scripts/check_conformance.py            # 校验（CI 用）
    python scripts/check_conformance.py --counts   # 打印各 tag 前缀的覆盖卡数
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOC = REPO / "docs" / "RULE_CONFORMANCE.md"
POOL = REPO / "packages" / "engine" / "engine" / "data" / "cards_pool_ext.json"

STATUS_OK = ("✅", "⚠️", "⚠", "❌")
COLS = ("规则", "实现点", "守卫测试", "tag 前缀", "覆盖卡数", "状态")


def pool_cards() -> list:
    return json.loads(POOL.read_text(encoding="utf-8"))["cards"]


def _strings_of(card: dict) -> list:
    """卡条目里所有可能承载 tag 的字符串（卡级 tag / 招式 effects / 特性 tag）"""
    out = [str(card.get("tag") or "")]
    for atk in card.get("attacks") or ():
        out += [str(e) for e in (atk.get("effects") or ())]
    for ab in card.get("abilities") or ():
        out.append(str(ab.get("tag") or ""))
    return out


def count_prefix(cards: list, prefix: str) -> int:
    """有多少张卡的某个 tag 以该前缀开头（同一张卡只计一次）"""
    n = 0
    for c in cards:
        if any(s.startswith(prefix) for s in _strings_of(c)):
            n += 1
    return n


def read_rows() -> list:
    text = DOC.read_text(encoding="utf-8")
    rows = []
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("|") or s.startswith("|---") or "---" in s[:8]:
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) != len(COLS):
            continue
        if cells[0] in COLS:                      # 表头
            continue
        if set(cells[0]) <= set("-: "):           # 分隔行
            continue
        rows.append(dict(zip(COLS, cells)))
    return rows


def check_token_impl(tok: str) -> list:
    """`路径::符号` —— 文件存在且能搜到符号"""
    errs = []
    if "::" not in tok:
        return [f"实现点缺 `::`：{tok}"]
    path_s, sym = tok.split("::", 1)
    path = REPO / path_s.strip()
    if not path.is_file():
        return [f"实现点文件不存在：{path_s}"]
    body = path.read_text(encoding="utf-8")
    if sym.strip() not in body:
        errs.append(f"{path_s} 里找不到 `{sym.strip()}`")
    return errs


def check_token_test(tok: str) -> list:
    """`路径::类.方法`（方法可省）—— 文件、类、方法都要能找到"""
    if "::" not in tok:
        return [f"测试缺 `::`：{tok}"]
    path_s, sym = tok.split("::", 1)
    path = REPO / path_s.strip()
    if not path.is_file():
        return [f"测试文件不存在：{path_s}"]
    body = path.read_text(encoding="utf-8")
    errs = []
    for part in sym.strip().split("."):
        if part and part not in body:
            errs.append(f"{path_s} 里找不到 `{part}`")
    return errs


def main() -> int:
    cards = pool_cards()
    if "--counts" in sys.argv:
        prefixes = []
        for r in read_rows():
            for p in re.split(r"[,、]", r["tag 前缀"]):
                p = p.strip().strip("`")
                if p and p != "—":
                    prefixes.append(p)
        print(f"{'前缀':40s} 覆盖卡数")
        for p in prefixes:
            print(f"{p:40s} {count_prefix(cards, p)}")
        return 0

    rows = read_rows()
    errs: list = []
    tally = {"✅": 0, "⚠️": 0, "❌": 0}
    for i, r in enumerate(rows, 1):
        name = r["规则"][:34]
        # ---- 状态 ----
        st = r["状态"].strip()
        key = next((k for k in ("✅", "⚠️", "❌") if st.startswith(k)), None)
        if key is None:
            errs.append(f"第 {i} 行 `{name}`：状态必须是 ✅ / ⚠️ / ❌，实得 {st!r}")
        else:
            tally[key] += 1
        # ---- 实现点 ----
        impl = [t.strip().strip("`") for t in re.split(r"[、,]", r["实现点"]) if t.strip()]
        if not impl:
            errs.append(f"第 {i} 行 `{name}`：实现点为空")
        for t in impl:
            errs += [f"第 {i} 行：{e}" for e in check_token_impl(t)]
        # ---- 守卫测试 ----
        tests = [t.strip().strip("`") for t in re.split(r"[、,]", r["守卫测试"]) if t.strip()]
        if not tests:
            errs.append(f"第 {i} 行 `{name}`：守卫测试为空")
        for t in tests:
            errs += [f"第 {i} 行：{e}" for e in check_token_test(t)]
        # ---- 覆盖卡数（带 tag 前缀时才可核对）----
        nums = r["覆盖卡数"].strip().strip("`")
        prefixes = [p.strip().strip("`") for p in re.split(r"[、,]", r["tag 前缀"])]
        prefixes = [p for p in prefixes if p and p != "—"]
        if prefixes:
            want = 0
            for p in prefixes:
                want += count_prefix(cards, p)
            if not nums.isdigit():
                errs.append(f"第 {i} 行 `{name}`：覆盖卡数应为数字，实得 {nums!r}")
            elif int(nums) != want:
                errs.append(f"第 {i} 行 `{name}`：覆盖卡数写 {nums}，"
                            f"卡池现算 {want}（{'、'.join(prefixes)}）")
        elif nums != "—":
            errs.append(f"第 {i} 行 `{name}`：无 tag 前缀时覆盖卡数必须是 —，实得 {nums!r}")

    print(f"矩阵行数 {len(rows)} · ✅ {tally['✅']} · ⚠️ {tally['⚠️']} · ❌ {tally['❌']}")
    if errs:
        print(f"\n{len(errs)} 处不一致：")
        for e in errs:
            print("  ✗", e)
        return 1
    print("OK: 每条规则的实现点 / 守卫测试 / 覆盖卡数都能对上")
    return 0


if __name__ == "__main__":
    sys.exit(main())
