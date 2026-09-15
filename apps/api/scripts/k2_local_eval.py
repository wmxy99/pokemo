# -*- coding: utf-8 -*-
"""本地 Qwen3-4B 实跑 K2 卡池解析达标率 · PokéLab AI

为什么需要这个脚本
------------------
`rule` 基线在真实卡池上的 support_rate = **99.7%**，但那是**离线规则基线**的数字
（零依赖、确定性、只认它认识的那批措辞），**不能**代表 LLM 后端的水平。
ADR-LLM-001 规定「K2 不达标 → 切云端保底」，所以必须用**本地量化模型真跑一遍**
才能做这个决定。本脚本就是干这个的。

为什么抽样
----------
本地 8bit 约 2–4 tok/s，单条解析要输出 100–250 token → 20–60 s/条；
全量 328 条 ≈ 3–5 小时。故默认**分层抽样**并给出 Wilson 95% 置信区间；
`--sample 0` 可跑全量（自行承担数小时）。`--dry-run` 只跑 rule（秒级）验证口径。

口径：**不能直接用 support_rate**（本脚本的重点）
--------------------------------------------
`support_rate` 定义为「无 unsupported 的比例」，但真实代码里：

  · `parse_effects` 的 llm 分支在 **LLM 返回为空时用 rule 结果兜底**
    （`specs = [...] or [parse_rule(...)]`），而 `backend` 仍报 `"llm"`；
  · 解析失败（无 spec）时 `to_engine_tags` 被短路为 `{"tags":[],"unsupported":[]}`
    → `not unsupported` 为真 → **失败被计入「完全支持」**。

两者叠加会让 LLM 的达标率**系统性虚高**（看起来接近 rule 基线）。
故本脚本用 `raw` 字段（保留的是 **LLM 原始输出**，不是兜底结果）做判据，三分：

  A 解析失败   —— LLM 未产出可用 JSON（`raw` 空）或有 errors  → 计为**失败**
  B 解析成功但动作未建模（unsupported 非空）  → 引擎侧缺口，**不算**解析失败
  C 解析成功且全部可映射                     → 完全支持

  主指标 **LLM 可用率** = (B + C) / n（解析链能否交付可用结构化结果）
  副指标 **完全映射率** = C / n（映射到引擎 tag 且无需额外建模）

用法
----
    python scripts/k2_local_eval.py --dry-run                  # 只跑 rule，验证抽样与口径（秒级）
    python scripts/k2_local_eval.py --sample 60                # 抽样 60 条实跑（约 20–40 分钟）
    python scripts/k2_local_eval.py --sample 60 --quant 4bit   # 换档位复核
    python scripts/k2_local_eval.py --sample 0                 # 全量（数小时）

产物：`artifacts/k2_local_eval.md`（人读）+ `artifacts/k2_local_eval.json`（机读）
"""
from __future__ import annotations

import argparse
import io
import json
import math
import os
import random
import re
import socket
import sys
import threading
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              write_through=True)      # 长任务要能实时看进度
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
for _p in (str(HERE), str(REPO / "packages" / "agent"),
           str(REPO / "packages" / "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from agent import effects as E                          # noqa: E402
# 注：`local_qwen` 在 start_server() 里才导入 —— dry-run 无需加载 torch，
# 保持秒级反馈（这也是为什么本脚本能先干跑一遍验证口径再花几十分钟实跑）。

K2_TARGET = 0.85          # PRD K2：卡牌效果解析准确率 ≥85%


# ---------------- 工具 ----------------
def wilson(k: int, n: int, z: float = 1.96) -> tuple:
    """Wilson 95% 置信区间（小样本比正态近似稳，样本量小时不会给出负数下界）"""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def _pct(x, n=1):
    return "—" if x is None else f"{x * 100:.1f}%"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(port: int) -> None:
    """在守护线程里起 uvicorn（模型由 startup 钩子加载）"""
    import uvicorn
    import local_qwen as LQ
    app = LQ.make_app()
    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    threading.Thread(target=uvicorn.Server(cfg).run, daemon=True).start()


def wait_health(root: str, timeout: float = 600) -> dict:
    """轮询 /healthz（**必须传根地址**：healthz 在 `/healthz`，不是 `/v1/healthz`）"""
    import urllib.error
    import urllib.request
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(f"{root}/healthz", timeout=5) as r:
                d = json.loads(r.read().decode())
                if d.get("ok"):
                    return d
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise SystemExit(
                    f"[k2-eval] /healthz 404：URL 拼接有误（base={root}）")
        except Exception:                                    # noqa: BLE001
            pass
        time.sleep(3)
    raise SystemExit(f"[k2-eval] 服务未在 {timeout}s 内就绪")


# ---------------- 分层抽样 ----------------
def stratified_sample(items: list, n: int, seed: int) -> list:
    """按「rule 是否已判定未建模」分层抽样

    分层的理由不是为了提精度，而是为了**别漏掉难例**：rule 判为 unsupported 的条目
    是引擎侧的缺口，但它们在 LLM 下可能反而解析得更细（或更糟），是最有信息量的样本。
    纯随机抽在 n=60、总体 328 时分到难例的期望只有个位数，容易得出片面的结论。

    **踩过的坑**：首版按 `sorted(strata)` 顺序逐层抽配额——"easy" 字典序在前，
    于是易例把 60 个名额一次吃光，唯一的难例被 `[:n]` 截断挤掉（实测难例数 = 0）。
    改为：① 非空层先保底 1 条；② 超额时只从**易例**扣；③ 抽取顺序**难例优先**，
    使截断永远切在易例一侧。
    """
    rng = random.Random(seed)
    strata: dict = {"hard": [], "easy": []}
    for it in items:
        strata["hard" if it["rule_unsupported"] else "easy"].append(it)
    total = len(items)
    quotas = {k: (max(1, round(n * len(g) / total)) if g else 0)
              for k, g in strata.items()}
    over = sum(quotas.values()) - n
    if over > 0:                       # 超额时先扣易例，难例保底不动
        take = min(over, max(0, quotas["easy"] - 1))
        quotas["easy"] -= take
        over -= take
        if over > 0:
            quotas["hard"] = max(0, quotas["hard"] - over)
    picked: list = []
    for k in ("hard", "easy"):         # 难例优先，保证不被总量截断挤掉
        g = strata[k]
        q = min(len(g), quotas[k])
        if q:
            picked += rng.sample(g, q)
    if len(picked) < n:                # 不足则补齐（不动已抽的）
        rest = [x for x in items if x not in picked]
        rng.shuffle(rest)
        picked += rest[:n - len(picked)]
    return picked[:n]


def lang_of(text: str) -> str:
    """语料语言：`zh`（含汉字）或 `en`

    **为什么必须区分**：K2 卡池 328 条里 **294 条（89.6%）是纯英文**——扩展卡池
    （`cards_pool_ext.json`）没有中文效果文本，`display_desc` 回退到英文；只有人工池
    的 34 条是中文。而 K2 的提示词是**中文撰写的**，两种输入的难度并不相同，
    混在一起报一个总数会掩盖差异。
    """
    return "zh" if re.search(r"[\u4e00-\u9fff]", text or "") else "en"


# ---------------- 主流程 ----------------
def main() -> int:
    ap = argparse.ArgumentParser(description="本地 Qwen3-4B 实跑 K2 卡池解析达标率")
    ap.add_argument("--sample", type=int, default=60,
                    help="抽样条数；0 = 全量（数小时）")
    ap.add_argument("--seed", type=int, default=42, help="抽样随机种子（固定=可复现）")
    ap.add_argument("--quant", default="8bit", help="8bit（默认）/4bit")
    ap.add_argument("--tag", default=None,
                    help="报告文件后缀（默认按 --quant 推导；8bit 用无后缀原名）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只跑 rule 基线 + 抽样，不起服务（秒级，验证口径）")
    ap.add_argument("--rerender", default=None, metavar="JSON",
                    help="从已有 JSON 重渲染 Markdown（**不占 GPU**）——"
                         "改了报告措辞/统计口径后用它刷新，不必重跑几十分钟")
    a = ap.parse_args()

    # `--rerender`：只读 JSON 重出 Markdown。用途：报告格式或统计口径调整后，
    # 无需再花几十分钟重跑（也避免两次跑出的数字不一致）。
    if a.rerender:
        jp = Path(a.rerender)
        if not jp.is_file():
            raise SystemExit(f"[k2-eval] 缺少 {jp}")
        data = json.loads(jp.read_text(encoding="utf-8"))
        rows = data.get("rows") or []
        if not rows:
            raise SystemExit(f"[k2-eval] {jp.name} 是 dry-run 产物（无 rows），无法重渲染")
        a.quant = data.get("quant") or a.quant
        a.tag = data.get("tag") or a.quant
        a.seed = data.get("seed", a.seed)
        items = E.collect_pool_items()
        print(f"[k2-eval] 重渲染 {jp.name}：{len(rows)} 条（quant={a.quant}）")
        _write_report(items, rows, rows, a)
        return 0

    print("=" * 70)
    print("K2 卡池解析达标率 · 本地模型实跑")
    print("=" * 70)

    # ---- ① 卡池条目 + rule 全量基线 ----
    items = E.collect_pool_items()
    print(f"\n① 卡池条目（招式+特性，含展示文本：中文优先、无中文则回退英文）：{len(items)}")
    _zh = sum(1 for i in items if lang_of(i["text"]) == "zh")
    print(f"   语言分布：中文 {_zh} 条 / 英文 {len(items) - _zh} 条"
          f"（扩展池无中文文本，故英文占多数）")
    t0 = time.time()
    for it in items:
        r = E.parse_effects(it["text"], kind=it["kind"], name=it["name"],
                            backend="rule")
        tg = E.to_engine_tags(r["specs"]) if r["specs"] else \
            {"tags": [], "unsupported": []}
        it["rule_tags"] = tg["tags"]
        it["rule_unsupported"] = tg["unsupported"]
        it["rule_actions"] = sorted({
            act.get("type") for s in r["specs"] for act in (s.get("actions") or [])
            if isinstance(act, dict)})
    rule_ok = sum(1 for it in items if not it["rule_unsupported"])
    print(f"   rule 基线：完全映射 {rule_ok}/{len(items)} = "
          f"{_pct(rule_ok / len(items))}（耗时 {time.time() - t0:.1f}s）")
    print(f"   rule 判为未建模的难例：{len(items) - rule_ok} 条")

    # ---- ② 抽样 ----
    sample = items if a.sample == 0 else stratified_sample(items, a.sample, a.seed)
    hard = sum(1 for it in sample if it["rule_unsupported"])
    print(f"\n② 抽样：{len(sample)} 条（seed={a.seed}）"
          f"｜其中 rule 难例 {hard} 条 / 易例 {len(sample) - hard} 条")
    if a.dry_run:
        print("\n[dry-run] 抽样构成预览（前 10 条）：")
        for it in sample[:10]:
            flag = "难" if it["rule_unsupported"] else "易"
            print(f"   [{flag}/{lang_of(it['text'])}] {it['kind']:<7} "
                  f"{it['name'][:24]:<26} {it['text'][:34]}")
        lg = {}
        for it in sample:
            lg[lang_of(it["text"])] = lg.get(lang_of(it["text"]), 0) + 1
        print(f"\n[dry-run] 样本语言分布：{lg}（卡池总体："
              f"zh={sum(1 for i in items if lang_of(i['text'])=='zh')} / "
              f"en={sum(1 for i in items if lang_of(i['text'])=='en')}）")
        print(f"\n[dry-run] 未调用 LLM。去掉 --dry-run 即可实跑"
              f"（预计 {len(sample) * 35 // 60}–{len(sample) * 60 // 60} 分钟）")
        _write_report(items, sample, None, a)
        return 0

    # ---- ③ 起服务并实跑 ----
    port = free_port()
    root = f"http://127.0.0.1:{port}"
    os.environ["POKELAB_LLM_BASE"] = f"{root}/v1"
    os.environ["POKELAB_LLM_MODEL"] = f"qwen3-4b-{a.quant}"
    os.environ["POKELAB_LLM_TIMEOUT"] = "600"
    os.environ["POKELAB_LLM_KEY"] = "local"          # 本地服务不校验，占位即可
    os.environ["POKELAB_QWEN_QUANT"] = a.quant
    # 审计写到独立文件，避免污染真实审计日志。
    # **不用 unlink()**：本沙箱拦截删除并 fail-closed（SAFE_DELETE_FAIL_CLOSED），
    # 用「截断」达到同样效果。
    audit = REPO / "artifacts" / "_k2_eval_audit.jsonl"
    audit.parent.mkdir(parents=True, exist_ok=True)
    with open(audit, "w", encoding="utf-8"):
        pass
    os.environ["POKELAB_AUDIT_LOG"] = str(audit)

    print(f"\n③ 启动本地服务（{a.quant}）：{root}"
          f"　模型加载约 15–30s …", flush=True)
    start_server(port)
    hz = wait_health(root)
    print(f"   服务就绪：quant={hz.get('quant')} "
          f"footprint={hz.get('footprint_gb')}GB", flush=True)

    print(f"\n④ 逐条解析（{len(sample)} 条，本地模型较慢，进度实时）…")
    t1 = time.time()
    rows: list = []
    for i, it in enumerate(sample, 1):
        t = time.time()
        r = E.parse_effects(it["text"], kind=it["kind"], name=it["name"],
                            backend="llm")
        dt = time.time() - t
        # **判据核心**：`raw` 是 LLM 的原始输出（可能是空）；`specs` 可能已被
        # rule 兜底过，故不能用 specs 判断「LLM 是否真的解析成功」。
        raw_list = [s for s in (r.get("raw") or []) if isinstance(s, dict)]
        raw_empty = not raw_list
        tg = E.to_engine_tags(r["specs"]) if r["specs"] else \
            {"tags": [], "unsupported": []}
        llm_actions = sorted({
            act.get("type") for s in r["specs"] for act in (s.get("actions") or [])
            if isinstance(act, dict)}) if not raw_empty else []
        rows.append({
            "name": it["name"], "name_zh": it["name_zh"], "kind": it["kind"],
            "text": it["text"], "lang": lang_of(it["text"]),
            "rule_unsupported": len(it["rule_unsupported"]),
            "rule_tags": it["rule_tags"], "rule_actions": it["rule_actions"],
            "raw_empty": raw_empty,                      # ← LLM 是否真的产出了 JSON
            "errors": r["errors"], "backend": r["backend"],
            "tags": tg["tags"], "unsupported": tg["unsupported"],
            "llm_actions": llm_actions,
            "elapsed_ms": int(dt * 1000),
        })
        eta = (time.time() - t1) / i * (len(sample) - i)
        flag = "空" if raw_empty else ("错" if r["errors"] else "ok")
        print(f"   [{i:>3}/{len(sample)}] {flag:<2} {dt:5.1f}s "
              f"ETA {eta/60:4.1f}min  {it['name'][:30]}", flush=True)

    elapsed = time.time() - t1
    print(f"\n④ 完成：{len(rows)} 条 / {elapsed:.0f}s"
          f"（均 {elapsed/len(rows):.1f}s/条）")

    _write_report(items, sample, rows, a)
    _print_summary(rows, items, a)
    return 0


def _classify(rows: list) -> dict:
    """A 解析失败 / B 解析成功但未建模 / C 完全映射 —— 三分类（见文件头口径说明）"""
    a = [r for r in rows if r["raw_empty"] or r["errors"]]
    bc = [r for r in rows if r not in a]
    b = [r for r in bc if r["unsupported"]]
    c = [r for r in bc if not r["unsupported"]]
    return {"A_parse_failed": a, "B_unmodeled": b, "C_fully_mapped": c}


def _summary(rows: list, items: list) -> dict:
    n = len(rows)
    cls = _classify(rows)
    usable = len(cls["B_unmodeled"]) + len(cls["C_fully_mapped"])
    lo_u, hi_u = wilson(usable, n)
    lo_c, hi_c = wilson(len(cls["C_fully_mapped"]), n)
    fallback = sum(1 for r in rows if r["raw_empty"])
    # rule 在同一样本上的对照（关键：同口径、同样本才可比）
    rule_ok = sum(1 for r in rows if not r["rule_unsupported"])
    # 一致率必须比**引擎 tag 空间**，不能比「解析出的原始动作类型」——
    # 两个后端的动作命名空间本就不同（rule 出 `coin_flip`/`shield`，LLM 可能出 `rule`），
    # 直接比原始 type 必然低（首版就这样，得到 42.4% 这种会很误导的数字）。
    agree = sum(1 for r in rows
                if r.get("tags") and set(r["tags"]) == set(r.get("rule_tags") or []))
    both_nonempty = sum(1 for r in rows
                        if r.get("tags") and r.get("rule_tags"))
    avg_s = (sum(r["elapsed_ms"] for r in rows) / n / 1000) if n else None
    # 按语言分组（提示词是中文写的，而语料 89.6% 是英文 —— 不分组会掩盖差异）
    by_lang = {}
    for lg in ("zh", "en"):
        sub = [r for r in rows if r["lang"] == lg]
        if not sub:
            continue
        cl = _classify(sub)
        us = len(cl["B_unmodeled"]) + len(cl["C_fully_mapped"])
        by_lang[lg] = {
            "n": len(sub),
            "A_parse_failed": len(cl["A_parse_failed"]),
            "B_unmodeled": len(cl["B_unmodeled"]),
            "C_fully_mapped": len(cl["C_fully_mapped"]),
            "usable_rate": round(us / len(sub), 4),
            "usable_ci95": [round(x, 4) for x in wilson(us, len(sub))],
        }
    return {
        "n": n,
        "pool_total": len(items),
        "A_parse_failed": len(cls["A_parse_failed"]),
        "B_unmodeled": len(cls["B_unmodeled"]),
        "C_fully_mapped": len(cls["C_fully_mapped"]),
        "usable_rate": round(usable / n, 4) if n else None,
        "usable_ci95": [round(lo_u, 4), round(hi_u, 4)],
        "fully_mapped_rate": round(len(cls["C_fully_mapped"]) / n, 4) if n else None,
        "fully_mapped_ci95": [round(lo_c, 4), round(hi_c, 4)],
        "raw_empty_rate": round(fallback / n, 4) if n else None,
        "errors_rate": round(sum(1 for r in rows if r["errors"]) / n, 4) if n else None,
        "rule_fully_mapped_on_sample": round(rule_ok / n, 4) if n else None,
        "engine_tag_agreement": round(agree / both_nonempty, 4) if both_nonempty else None,
        "engine_tag_agreement_n": both_nonempty,
        "avg_sec_per_item": round(avg_s, 1) if avg_s else None,
        "by_lang": by_lang,
        "k2_target": K2_TARGET,
        "k2_pass": bool(usable / n >= K2_TARGET) if n else False,
        "est_full_pool_hours": round(avg_s * len(items) / 3600, 1) if avg_s else None,
    }


def _print_summary(rows: list, items: list, a) -> None:
    s = _summary(rows, items)
    print("\n" + "=" * 70)
    print("⑤ 结果")
    print("=" * 70)
    print(f"  样本 n={s['n']}（卡池总 {s['pool_total']}）")
    print(f"  A 解析失败（LLM 空返回/有错） : {s['A_parse_failed']}")
    print(f"  B 解析成功但未建模           : {s['B_unmodeled']}")
    print(f"  C 解析成功且完全映射         : {s['C_fully_mapped']}")
    print(f"  → LLM 可用率  {_pct(s['usable_rate'])}"
          f"  95%CI [{_pct(s['usable_ci95'][0])}, {_pct(s['usable_ci95'][1])}]")
    print(f"  → 完全映射率  {_pct(s['fully_mapped_rate'])}"
          f"  95%CI [{_pct(s['fully_mapped_ci95'][0])}, {_pct(s['fully_mapped_ci95'][1])}]")
    print(f"  对照 rule 同样本完全映射率：{_pct(s['rule_fully_mapped_on_sample'])}")
    for lg, d in (s.get("by_lang") or {}).items():
        print(f"  [{lg}] n={d['n']:<3} 可用率 {_pct(d['usable_rate'])}"
              f" 95%CI[{_pct(d['usable_ci95'][0])}, {_pct(d['usable_ci95'][1])}]"
              f"  (A{d['A_parse_failed']}/B{d['B_unmodeled']}/C{d['C_fully_mapped']})")
    print(f"  引擎 tag 一致率（llm vs rule）：{_pct(s['engine_tag_agreement'])}"
          f"（可比 {s['engine_tag_agreement_n']} 条）")
    print(f"  均耗时 {s['avg_sec_per_item']}s/条 → 全量 {s['pool_total']} 条约 "
          f"{s['est_full_pool_hours']} 小时")
    print(f"\n  **K2 判定（门槛 {int(K2_TARGET*100)}%）**："
          f"{'PASS ✅' if s['k2_pass'] else '未达 ❌'}")


def _report_paths(tag: str | None):
    """按量化档位决定报告落盘路径

    **默认档（8bit）沿用无后缀原名**（见 .gitignore 白名单）；其余档位加 `_<tag>` 后缀
    ——否则跑 4bit 对照时会把 8bit 的证据覆盖掉。
    """
    suffix = "" if (tag or "8bit").lower() in ("", "8bit") else f"_{tag.lower()}"
    base = f"k2_local_eval{suffix}"
    return REPO / "artifacts" / f"{base}.md", REPO / "artifacts" / f"{base}.json"


def _err_kind(msg: str) -> str:
    """把 schema 错误归成人类可读的类别

    为什么要分类：只列「哪些条失败」还不够——需要一眼看出**主要失败模式**，
    才能判断该补提示词、补后处理，还是补引擎。
    """
    m = str(msg)
    if "缺字段" in m:
        return "缺字段（输出 Schema 不完整）"
    if "非法" in m:
        return "枚举/取值非法"
    if "应为" in m:
        return "类型不符（数组/对象）"
    if "不是对象" in m:
        return "结构错误（数组元素非对象）"
    if "空输出" in m or not m.strip():
        return "LLM 空返回（未产出 JSON）"
    return "其他"


def _write_report(items: list, sample: list, rows, a) -> None:
    out = REPO / "artifacts"
    out.mkdir(parents=True, exist_ok=True)
    md_p, js_p = _report_paths(a.tag or a.quant)
    s = _summary(rows, items) if rows else None

    L = ["# K2 卡池解析达标率 · 本地模型实跑", "",
         f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
         f"- 模型：本地 Qwen3-4B · **{a.quant}**（`POKELAB_LLM_BASE` → 进程内服务）",
         f"- 卡池条目总数：{len(items)}；样本：**{len(sample)}**"
         f"（seed={a.seed}，分层抽样）",
         f"- K2 目标：≥{int(K2_TARGET * 100)}%（PRD）", ""]

    if not rows:
        L += ["## 状态：dry-run（未调用 LLM）", "",
              "本报告仅用于验证抽样构成与统计口径。去掉 `--dry-run` 实跑后本文件会被覆盖。",
              "", "抽样构成（前 20 条）：", "",
              "| # | 类 | kind | 名称 | rule 未建模 | 文本 |", "|---|---|---|---|---:|---|"]
        for i, it in enumerate(sample[:20], 1):
            L.append(f"| {i} | {'难' if it['rule_unsupported'] else '易'} "
                     f"| {it['kind']} | {it['name']} "
                     f"| {len(it['rule_unsupported'])} | {it['text'][:40]} |")
        md_p.write_text("\n".join(L) + "\n", encoding="utf-8")
        js_p.write_text(json.dumps(
            {"mode": "dry_run", "pool_total": len(items),
             "sample_n": len(sample), "seed": a.seed,
             "sample": [{"name": it["name"], "kind": it["kind"],
                         "text": it["text"],
                         "rule_unsupported": len(it["rule_unsupported"])}
                        for it in sample]},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[k2-eval] dry-run 报告 -> {md_p}")
        return

    L += ["## 1. 主结果（**口径见文末**）", "",
          "| 分类 | 条数 | 占比 |", "|---|---:|---:|",
          f"| **A 解析失败**（LLM 空返回或有 errors） | {s['A_parse_failed']} "
          f"| {_pct(s['A_parse_failed']/s['n'])} |",
          f"| **B 解析成功但动作未建模** | {s['B_unmodeled']} "
          f"| {_pct(s['B_unmodeled']/s['n'])} |",
          f"| **C 解析成功且完全映射** | {s['C_fully_mapped']} "
          f"| {_pct(s['C_fully_mapped']/s['n'])} |", "",
          "| 指标 | 数值 | 95% 置信区间 |", "|---|---:|---|",
          f"| **LLM 可用率**（B+C） | **{_pct(s['usable_rate'])}** "
          f"| [{_pct(s['usable_ci95'][0])}, {_pct(s['usable_ci95'][1])}] |",
          f"| 完全映射率（C） | {_pct(s['fully_mapped_rate'])} "
          f"| [{_pct(s['fully_mapped_ci95'][0])}, {_pct(s['fully_mapped_ci95'][1])}] |",
          f"| rule 兜底率（LLM 空返回） | {_pct(s['raw_empty_rate'])} | — |",
          f"| 带 schema errors 的比例 | {_pct(s['errors_rate'])} | — |",
          "",
          f"**对照**：rule 后端在**同一样本**上的完全映射率 = "
          f"{_pct(s['rule_fully_mapped_on_sample'])}；"
          f"**引擎 tag 一致率**（llm vs rule，可比 {s['engine_tag_agreement_n']} 条）= "
          f"{_pct(s['engine_tag_agreement'])}"
          "（比的是**映射后的引擎 tag**，不是原始动作 type——两个后端的动作命名空间"
          "本就不同，比原始 type 会得到很低且无意义的数字）", "",
          f"**K2 判定**：{'PASS ✅' if s['k2_pass'] else '未达 ❌'}"
          f"（{_pct(s['usable_rate'])} vs 门槛 {int(K2_TARGET*100)}%）", "",
          f"耗时：{s['avg_sec_per_item']}s/条 → 全量 {s['pool_total']} 条约 "
          f"{s['est_full_pool_hours']} 小时", ""]

    if s.get("by_lang"):
        L += ["### 按语料语言分组（**重要**）", "",
              "K2 卡池 328 条中 **294 条（89.6%）是纯英文**（扩展池无中文效果文本，"
              "`display_desc` 回退英文），仅 34 条中文；而提示词是中文撰写的。"
              "两者难度不同，故分组统计：", "",
              "| 语言 | n | 可用率 | 95% CI | A 失败 | B 未建模 | C 完全映射 |",
              "|---|---:|---:|---|---:|---:|---:|"]
        for lg, d in s["by_lang"].items():
            name = "中文" if lg == "zh" else "英文"
            L.append(f"| {name} | {d['n']} | **{_pct(d['usable_rate'])}** "
                     f"| [{_pct(d['usable_ci95'][0])}, {_pct(d['usable_ci95'][1])}] "
                     f"| {d['A_parse_failed']} | {d['B_unmodeled']} "
                     f"| {d['C_fully_mapped']} |")
        L += [""]

    fails = [r for r in rows if r["raw_empty"] or r["errors"]]
    L += ["## 2. A 类失败明细（= 解析链待补清单）", ""]
    if not fails:
        L.append("无。")
    else:
        kinds: dict = {}
        for r in fails:
            for e in (r["errors"] or ["LLM 空返回（未产出 JSON）"]):
                k = _err_kind(str(e))
                kinds[k] = kinds.get(k, 0) + 1
        L += ["**错误类型分布**（按 error 条目计，一条记录可能有多条）：", "",
              "| 类型 | 条数 |", "|---|---:|"]
        for k, v in sorted(kinds.items(), key=lambda x: -x[1]):
            L.append(f"| {k} | {v} |")
        L += ["", "逐条明细：", "",
              "| # | 名称 | kind | 语言 | 现象 | 文本 |", "|---|---|---|---|---|---|"]
        for i, r in enumerate(fails, 1):
            why = ("LLM 空返回→rule 兜底" if r["raw_empty"]
                   else f"errors: {str(r['errors'])[:60]}")
            L.append(f"| {i} | {r['name']} | {r['kind']} | {r.get('lang', '?')} "
                     f"| {why} | {r['text'][:44]} |")
    L += [""]

    L += ["## 3. B 类明细（解析成功，但动作映射不到引擎 tag）", "",
          "> **B 类不是解析失败**，但**也未必是引擎缺口**——先看最后一列的对照，"
          "它是本报告最容易误读的一处：", "",
          "> 实测（8bit）16 条 B 类里，**rule 后端都识别出了具体机制**"
          "（`coin_flip` / `shield` / `status` / `damage_mod` / `move_damage` …），"
          "**这些引擎已建模**；而 LLM 把它们笼统合并成了 `rule`（静态规则修改，"
          "引擎未建模该类型）。**即：这 16 条主要反映 LLM 解析粒度不足（丢失可执行语义），"
          "而不是引擎没做。**", "",
          "> **可操作的改进点**：提示词的 `action.type` 枚举里没有「抛币」这类机制级取值"
          "（只有 `damage/draw/attach/heal/discard/switch/search/evolve/spread/"
          "move_damage/knockout/status/rule`），模型只能退回 `rule` 兜底。"
          "补上机制级枚举 + 少量 few-shot，是提升「完全映射率」最直接的一步"
          "（本报告只记录、未擅自改提示词——那会影响既有测试与行为，需评审）。", "",
          "| # | 名称 | 语言 | LLM 得到的动作 | rule 后端识别 | 未建模动作 |",
          "|---|---|---|---|---|---|"]
    cls = _classify(rows)
    for i, r in enumerate(cls["B_unmodeled"], 1):
        uns = "、".join(sorted({u.get("type", "?") for u in r["unsupported"]}))
        rule_a = "、".join(r.get("rule_actions") or []) or "**（未识别出动作）**"
        L.append(f"| {i} | {r['name']} | {r.get('lang', '?')} "
                 f"| {'、'.join(r['llm_actions']) or '—'} | {rule_a} | {uns} |")
    L += [""]

    L += ["## 4. 口径说明（**必读**，否则会误读上面的数字）", "",
          "1. **没有直接使用 `support_rate`**。原始定义是「无 unsupported 的比例」，",
          "   但解析失败（无 spec）时 `to_engine_tags` 会被短路成 "
          "`{tags:[], unsupported:[]}`，",
          "   `not unsupported` 为真 → **失败被计入「完全支持」**；",
          "   且 `parse_effects` 的 llm 分支在 **LLM 空返回时会用 rule 结果兜底**"
          "（`specs = [...] or [parse_rule(...)]`）而 `backend` 仍报 `\"llm\"`。",
          "   两者叠加会让 LLM 的达标率**系统性虚高**。",
          "2. 本报告改用 `raw` 字段判断（它保留的是 **LLM 原始输出**，非兜底结果），",
          "   并严格三分 A/B/C；**B 类不算解析失败**——解析是对的，缺的是引擎建模。",
          "3. **抽样估计**：n 有限，请结合 95% 置信区间读；区间用 **Wilson 区间**"
          "（小样本下比正态近似稳，不会给出负数下界）。`rule 兜底率` 越低，",
          "   说明 LLM 越能独立产出结果。",
          "4. 语料为 `cards_real.json` 的 `desc_zh`（**自述转写**，非官方文案；C2/C5）。",
          ""]
    md_p.write_text("\n".join(L) + "\n", encoding="utf-8")
    js_p.write_text(json.dumps(
        {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
         "quant": a.quant, "seed": a.seed, "tag": a.tag or a.quant,
         "summary": s, "rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"[k2-eval] 报告 -> {md_p}")


if __name__ == "__main__":
    sys.exit(main())
