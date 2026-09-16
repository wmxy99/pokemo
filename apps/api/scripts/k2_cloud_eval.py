#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""K2 云端保底验证 —— 用真实云端模型跑黄金集，回答「切云端能否达标」

用法::

    POKELAB_LLM_BASE=https://api.deepseek.com/v1 \
    POKELAB_LLM_KEY=sk-xxx \
    POKELAB_LLM_MODEL=deepseek-chat \
    python apps/api/scripts/k2_cloud_eval.py --yes

为什么单独成脚本（而不是直接跑 `run_k2.py`）：

1. **合规前置**：切云端 = **数据外发**。本脚本在**发出任何请求之前**校验配置、
   打印外发对象与脱敏说明，并要求显式 `--yes` —— 避免「顺手跑一下」就把卡面文本发出去。
   `POKELAB_LOCAL_ONLY=1` 时**直接拒绝**（该模式下云端保底不可用）。
2. **结果要有参照**：只报一个 `strict=92%` 没有意义，必须与**本地基线**并排看。
   本地 8bit 的历史最好成绩是 **40%**（见 `k2_report_llm_v6.md`）。
3. 保存到**独立文件名**（`k2_report_cloud.*`），不覆盖本地各版报告 ——
   保留本地→云端的对照链。

注意：本脚本**不**替你保管密钥；请用环境变量传入，不要写进文件。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]          # pokelab-ai
AGENT = REPO / "packages" / "agent"

# 本地 8bit 的历史最好成绩（黄金集 50 条，strict）—— 用于并排对照
LOCAL_BEST_STRICT = 40.0
K2_TARGET = 85.0


def main() -> int:
    ap = argparse.ArgumentParser(description="K2 云端保底验证")
    ap.add_argument("--yes", action="store_true",
                    help="确认已知「卡面文本将外发到云端端点」")
    ap.add_argument("--golden", default=None,
                    help="黄金集路径（默认用仓库自带的那份）")
    ap.add_argument("--out-tag", default="cloud",
                    help="产物后缀（默认 cloud → k2_report_cloud.md）")
    a = ap.parse_args()

    base = (os.environ.get("POKELAB_LLM_BASE") or "").strip()
    key = (os.environ.get("POKELAB_LLM_KEY") or "").strip()
    model = (os.environ.get("POKELAB_LLM_MODEL") or "").strip()
    missing = [n for n, v in (("POKELAB_LLM_BASE", base),
                              ("POKELAB_LLM_KEY", key),
                              ("POKELAB_LLM_MODEL", model)) if not v]
    if missing:
        print("[cloud-k2] ✗ 缺少配置：" + "、".join(missing))
        print("\n  示例（DeepSeek，端点可在后台「模型配置」页里看到预设清单）：")
        print("    export POKELAB_LLM_BASE=https://api.deepseek.com/v1")
        print("    export POKELAB_LLM_KEY=<你的 key>")
        print("    export POKELAB_LLM_MODEL=deepseek-chat")
        return 2

    sys.path[:0] = [str(AGENT), str(REPO / "packages" / "engine")]
    from agent import privacy as P                     # noqa: E402

    if P.local_only():
        print("[cloud-k2] ✗ POKELAB_LOCAL_ONLY=1 → 云端保底**不可用**"
              "（该模式禁止一切外部调用）；请取消本地模式或改用本机端点。")
        return 2

    info = P.check_endpoint(base)
    print(f"[cloud-k2] 端点   : {info['host']}（external={info['external']}）")
    print(f"[cloud-k2] 模型   : {model}")
    if info["external"]:
        print("[cloud-k2] ⚠ 即将把**卡面文本**发送到上述外部端点。")
        print("           · 外发前会过 `privacy.desensitize`（邮箱/手机号等替换为占位符）")
        print("           · 每次调用写审计（仅端点/模型/字符数/脱敏计数，**不含原文**）")
        print("           · 卡面语料已是**自述转写**（非官方文案，合规 C2/C5）")
        if not a.yes:
            print("\n[cloud-k2] 确认无误请加 `--yes` 重跑。")
            return 2
    else:
        print("[cloud-k2] 该端点属本机/内网 → 不外发，直接执行。")

    print(f"\n[cloud-k2] 开始跑黄金集（50 条，云端通常 3–10 分钟）…", flush=True)
    cmd = [sys.executable, str(AGENT / "examples" / "run_k2.py"),
           "--backend", "llm", "--no-pool",
           "--out", f"../../artifacts/k2_report_{a.out_tag}.md",
           "--json", f"../../artifacts/k2_report_{a.out_tag}.json"]
    if a.golden:
        cmd += ["--golden", a.golden]
    rc = subprocess.run(cmd, cwd=str(AGENT), env=dict(os.environ)).returncode

    jp = REPO / "artifacts" / f"k2_report_{a.out_tag}.json"
    if jp.is_file():
        d = json.loads(jp.read_text(encoding="utf-8"))
        # 产物格式：`run_k2.py` 把指标**平铺在顶层**（strict_acc / seen_strict_acc …），
        # 不是 `metrics: {...}`。首版按 `metrics.strict` 读 → 永远得 0.0%，
        # 而报告里明明写着 58%（差到会被当成"云端完全不能打"）。
        def _acc(*keys):
            for k in keys:
                v = d.get(k)
                if isinstance(v, (int, float)):
                    return float(v) * 100
            return 0.0
        strict = _acc("strict_acc", "strict")
        seen = _acc("seen_strict_acc")
        hold = _acc("holdout_strict_acc")
        print("\n" + "=" * 62)
        print(f"  云端 strict = {strict:.1f}%   （seen {seen:.1f}% / holdout {hold:.1f}%）")
        print(f"  本地最好   = {LOCAL_BEST_STRICT:.1f}%   ← 8bit，见 k2_report_llm_v6.md")
        verdict = "云端达标 ✅" if strict >= K2_TARGET else "仍未达标 ❌"
        print(f"  门槛 {K2_TARGET:.0f}% → **{verdict}**")
        print("=" * 62)
        if strict < K2_TARGET:
            if strict > LOCAL_BEST_STRICT:
                print(f"  ⚠ 云端比本地高 {strict - LOCAL_BEST_STRICT:.1f}pp，但**仍未达标** ——")
                print("    这说明瓶颈**不在模型规模**，而在任务定义 / 真值口径 / Schema 设计。")
                print("    下一步应查：① `target` 语义是否定义清楚（实测最常见的分歧点）")
                print("               ② 黄金集标注与 Schema 的约定是否一致")
                print("               ③ 是否有无法从文本判定的字段（应移出严格比对）")
            else:
                print("  ⚠ 云端未超过本地最好成绩，建议先核对端点/模型是否选对。")
    else:
        print(f"[cloud-k2] 未找到机读产物 {jp.name}（run_k2 退出码 {rc}）")
    return rc


if __name__ == "__main__":
    sys.exit(main())
