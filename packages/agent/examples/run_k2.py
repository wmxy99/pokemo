#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""PokéLab AI · K2 卡牌效果解析评测 CLI

用法（在 packages/agent 下）：
  python examples/run_k2.py                       # 规则基线 + 真实卡池解析
  python examples/run_k2.py --backend auto        # 有 LLM 配置走 LLM，否则回退规则
  POKELAB_LLM_BASE=https://api.deepseek.com/v1 \\
  POKELAB_LLM_KEY=sk-xxx POKELAB_LLM_MODEL=deepseek-chat \\
      python examples/run_k2.py --backend llm     # LLM 后端实测
  python examples/run_k2.py --no-pool --strict    # 只跑黄金集，并按下限返回退出码

产物：artifacts/k2_report.md（人读）+ artifacts/k2_report.json（机读，供看门狗）
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENT_PKG = HERE.parent
for p in (str(AGENT_PKG), str(AGENT_PKG.parent / "engine")):
    if p not in sys.path:
        sys.path.insert(0, p)

from agent import effects as E  # noqa: E402

K2_TARGET = 0.85          # PRD K2：卡牌效果解析准确率 ≥85%


def _pct(x):
    return "—" if x is None else f"{x * 100:.1f}%"


def main() -> int:
    ap = argparse.ArgumentParser(description="K2 卡牌效果解析评测")
    ap.add_argument("--backend", default="rule",
                    choices=["rule", "llm", "auto"],
                    help="rule=离线规则基线；llm=OpenAI 兼容；auto=有配置则 llm")
    ap.add_argument("--golden", default=None, help="黄金集路径（默认 data/k2_golden.json）")
    ap.add_argument("--out", default="artifacts/k2_report.md")
    ap.add_argument("--json", default="artifacts/k2_report.json")
    ap.add_argument("--no-pool", action="store_true", help="跳过真实卡池解析")
    ap.add_argument("--strict", action="store_true",
                    help="严格模式：严格准确率低于 85% 时退出码 1")
    args = ap.parse_args()

    t0 = time.time()
    golden = E.load_golden(args.golden)
    ev = E.evaluate(args.backend, golden=golden)

    pool = None
    if not args.no_pool:
        try:
            pool = E.parse_card_pool(backend=args.backend)
        except Exception as e:                        # noqa: BLE001
            pool = {"error": str(e), "n": 0, "items": []}

    # ---------------- 报告（Markdown） ----------------
    L: list = []
    L.append("# PokéLab AI · K2 卡牌效果解析报告")
    L.append("")
    L.append(f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}"
             f"（耗时 {time.time() - t0:.1f}s）")
    L.append(f"- 后端：**{ev['backend']}**"
             f"（`llm` 需配置 `POKELAB_LLM_BASE` / `POKELAB_LLM_KEY` / "
             f"`POKELAB_LLM_MODEL`；`auto` 无配置自动回退规则）")
    L.append(f"- 黄金集：{golden['meta'].get('version', '?')}"
             f"（{ev['n']} 条；手工标注真值，非解析器生成）")
    L.append(f"- K2 目标：严格准确率 ≥{int(K2_TARGET * 100)}%（PRD）")
    L.append("")
    L.append("## 1. 指标汇总")
    L.append("")
    L.append("| 指标 | 数值 | 说明 |")
    L.append("|---|---|---|")
    L.append(f"| **严格准确率（K2 主指标）** | **{_pct(ev['strict_acc'])}** "
             f"| 全部已声明字段一致 |")
    L.append(f"| 解析成功率 parse_rate | {_pct(ev['parse_rate'])} "
             f"| 产出合法 Schema JSON 的比例 |")
    L.append(f"| kind 准确率 | {_pct(ev['kind_acc'])} | 招式/特性/物品/支援者/能量/规则 |")
    L.append(f"| trigger 准确率 | {_pct(ev['trigger_acc'])} | on_use / static / on_evolve / on_knockout … |")
    L.append(f"| 动作类型集合 | {_pct(ev['action_set_acc'])} | 多重集比对 |")
    L.append(f"| 数值（伤害/张数） | {_pct(ev['amount_acc'])} | 指示物 1 个 = 10 点 |")
    L.append(f"| 目标（target） | {_pct(ev['target_acc'])} | active_opponent / self / deck … |")
    L.append(f"| 每回合限制 | {_pct(ev['limit_acc'])} | once_per_turn |")
    L.append("")
    L.append("### 分组（诚实口径）")
    L.append("")
    L.append(f"- **已知句式（seen，{ev['seen_n']} 条）**：{_pct(ev['seen_strict_acc'])}"
             f" —— 开发期已覆盖的句式，仅说明回归稳定，不代表泛化能力；")
    L.append(f"- **留出句式（holdout，{ev['holdout_n']} 条）**：{_pct(ev['holdout_strict_acc'])}"
             f" —— 与开发期**不同**的表述，未做针对性适配，是更可信的泛化估计。")
    L.append("")
    verdict = "PASS ✅" if (ev["strict_acc"] or 0) >= K2_TARGET else "未达 ❌"
    L.append(f"**K2 判定（{ev['backend']} 后端）**：{verdict}"
             f"（{_pct(ev['strict_acc'])} vs 门槛 {int(K2_TARGET * 100)}%）")
    L.append("")

    L.append("## 2. 逐例结果")
    L.append("")
    L.append("| # | 用例 | 组 | kind | trigger | 动作集 | 数值 | 目标 | 限制 | 结论 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")

    def mark(v):
        return "—" if v is None else ("✓" if v else "✗")

    for i, (row, c) in enumerate(zip(ev["rows"], golden["cases"]), 1):
        fields = [row[k] for k in ("kind", "trigger", "action_set", "amount",
                                   "target", "limit") if row[k] is not None]
        ok = all(fields) if fields else False
        L.append(f"| {i} | {c['id']} | {'ho' if c.get('holdout') else 'seen'} "
                 f"| {mark(row['kind'])} | {mark(row['trigger'])} "
                 f"| {mark(row['action_set'])} | {mark(row['amount'])} "
                 f"| {mark(row['target'])} | {mark(row['limit'])} "
                 f"| {'✅' if ok else '❌'} |")
    L.append("")

    fails = []
    for row, c in zip(ev["rows"], golden["cases"]):
        fields = [row[k] for k in ("kind", "trigger", "action_set", "amount",
                                   "target", "limit") if row[k] is not None]
        if not (all(fields) if fields else False):
            fails.append((c, row))
    L.append("## 3. 失败用例明细（= 待补句式清单）")
    L.append("")
    if not fails:
        L.append("无。")
    else:
        for c, row in fails:
            L.append(f"- **{c['id']}**"
                     f"{'（holdout）' if c.get('holdout') else ''}：`{c['text']}`")
            for d in row["diffs"]:
                L.append(f"  - {d}")
            if (c.get("note")):
                L.append(f"  - 备注：{c['note']}")
            if row.get("schema_errors"):
                L.append(f"  - Schema 错误：{row['schema_errors'][:2]}")
    L.append("")

    L.append("## 4. 真实卡池解析（desc_zh → spec → 引擎 tag）")
    L.append("")
    if pool is None or pool.get("error"):
        L.append(f"跳过（{'-' if pool is None else pool.get('error')}）。")
    else:
        L.append(f"- 卡片条目：**{pool['n']}**（招式 + 特性，均有中文效果文本）")
        L.append(f"- **可直接映射到引擎 tag 的比例：{_pct(pool['support_rate'])}**"
                 f"（{pool['fully_supported']}/{pool['n']}）")
        L.append("")
        L.append("| 卡片 | 类别 | 效果文本（截断） | 解析出的引擎 tag | 未支持动作 |")
        L.append("|---|---|---|---|---|")
        for it in pool["items"]:
            tags = " ".join(it["tags"]) or "—"
            uns = "、".join(sorted({u["type"] for u in it["unsupported"]})) or "—"
            txt = (it["text"][:34] + "…") if len(it["text"]) > 34 else it["text"]
            L.append(f"| {it['name_zh']} | {it['kind']} | {txt} | {tags} | {uns} |")
        L.append("")
        L.append("> 「未支持动作」= 解析正确但**本引擎尚未建模**（如实列出，不假装支持）；"
                 "这些是引擎侧（非解析侧）的后续工作。")
    L.append("")

    L.append("## 5. 诚实标注（必读）")
    L.append("")
    L.append("- **后端差异**：`rule` 是**离线规则基线**（零依赖、确定性），"
             "对开发期已覆盖句式接近满分；它**不能**代表 LLM 的泛化水平。"
             "LLM 后端的数字需配置 API Key 后实测（本报告不臆测）。")
    L.append(f"- **样本量**：黄金集仅 {ev['n']} 条（PRD 目标 200+，扩充属 P1）；"
             "留出集仅 "
             f"{ev['holdout_n']} 条，置信区间较宽，勿据此下强结论。")
    L.append("- **seen vs holdout**：seen 高不代表可用——请以 **holdout 准确率**"
             "作为当前泛化水平的参考，并把「失败用例明细」当作下一轮补句式的 TODO。")
    L.append("- **Schema 扩展**：为表达中文效果，本实现相对提示词初始 Schema 增加了 "
             "`status`（状态）与 `rule`（静态规则修改）两种 action.type；"
             "`packages/agent/prompts/parse_effects_system.md` 已同步（见 commit）。")
    L.append("- 数据来源：`packages/engine/engine/data/cards_real.json` 的 `desc_zh`"
             "（**自述转写**，非官方文案；C2/C5）。")
    L.append("")

    out_md = AGENT_PKG / args.out
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(L) + "\n", encoding="utf-8")

    # ---------------- 机读产物（供 M6 看门狗） ----------------
    summary = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "backend": ev["backend"],
        "n": ev["n"], "target": K2_TARGET,
        "strict_acc": ev["strict_acc"], "parse_rate": ev["parse_rate"],
        "kind_acc": ev["kind_acc"], "trigger_acc": ev["trigger_acc"],
        "action_set_acc": ev["action_set_acc"], "amount_acc": ev["amount_acc"],
        "target_acc": ev["target_acc"], "limit_acc": ev["limit_acc"],
        "seen_n": ev["seen_n"], "seen_strict_acc": ev["seen_strict_acc"],
        "holdout_n": ev["holdout_n"],
        "holdout_strict_acc": ev["holdout_strict_acc"],
        "failed_cases": [c["id"] for c, _ in fails],
        "pool_support_rate": (pool or {}).get("support_rate"),
        "pool_n": (pool or {}).get("n"),
        "passed": bool((ev["strict_acc"] or 0) >= K2_TARGET),
        "notes": ("rule 为离线基线；LLM 数字需配置 Key 实测；"
                  "以 holdout_strict_acc 作为泛化参考"),
    }
    out_json = AGENT_PKG / args.json
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=1),
                        encoding="utf-8")

    print(f"[k2] backend={ev['backend']} n={ev['n']} "
          f"strict={_pct(ev['strict_acc'])} "
          f"seen={_pct(ev['seen_strict_acc'])}({ev['seen_n']}) "
          f"holdout={_pct(ev['holdout_strict_acc'])}({ev['holdout_n']}) "
          f"-> {verdict}")
    if fails:
        print(f"[k2] 失败用例 {len(fails)}: "
              f"{', '.join(c['id'] for c, _ in fails)}")
    if pool and not pool.get("error"):
        print(f"[k2] 真实卡池 {pool['n']} 条，可直接映射引擎 tag "
              f"{_pct(pool['support_rate'])}")
    print(f"[k2] 报告 -> {out_md} ；机读 -> {out_json}")

    if args.strict and (ev["strict_acc"] or 0) < K2_TARGET:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
