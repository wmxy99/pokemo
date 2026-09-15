# -*- coding: utf-8 -*-
"""本地 Qwen3-4B 端到端接入验证 · PokéLab AI

验证什么
--------
「模型能加载」≠「产品接得上」。本脚本把**真实调用链**跑一遍，逐环节断言：

  ① 端点契约   —— `agent.llmclient` 能按 OpenAI 协议打通本地服务
  ② 合规放行   —— `POKELAB_LOCAL_ONLY=1`（完全离线）下本机端点仍可用，
                 且被判为 **非外发**（这是 6.2「支持本地化部署」的判据）
  ③ K2 效果解析 —— `agent.effects.parse_effects(backend="llm")` 走通本地模型
  ④ 对战解说   —— `agent.commentary.explain_turn(backend="llm")` 走通，且带 C4 标识
  ⑤ 审计留痕   —— 调用写入审计（不含原文）

设计要点：**脚本自己把服务起在进程内**（uvicorn 线程 + 自动挑空闲端口），跑完即关，
不依赖外部已启动的服务，也不需要占用固定端口。

用法
----
    python scripts/verify_local_llm.py              # 全跑（约 2–5 分钟）
    python scripts/verify_local_llm.py --quant 4bit # 换档位复核
"""
from __future__ import annotations

import argparse
import io
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              write_through=True)   # 关缓冲：长任务要能实时看进度
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
for p in (str(HERE), str(REPO / "packages" / "agent"),
          str(REPO / "packages" / "engine")):
    if p not in sys.path:
        sys.path.insert(0, p)

import local_qwen as LQ                                     # noqa: E402

RESULTS: list = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append({"check": name, "ok": bool(ok), "detail": detail})
    print(f"  [{'PASS ✅' if ok else 'FAIL ❌'}] {name}"
          + (f" — {detail}" if detail else ""), flush=True)
    return bool(ok)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(port: int) -> threading.Thread:
    """在守护线程里起 uvicorn（模型由 startup 钩子加载）"""
    import uvicorn
    app = LQ.make_app()
    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    srv = uvicorn.Server(cfg)
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    return t


def wait_health(root: str, timeout: float = 600) -> dict:
    """轮询 /healthz 直到 ok

    **踩过的坑**：这里必须传**根地址**而不是 `/v1` 基址——healthz 路由在 `/healthz`，
    早先误传 `/v1` 基址拼成 `/v1/healthz` → 永远 404，而 except 把异常吞了，
    表现为「日志 4 分钟不动、没有任何报错」（最难查的那种）。故：
      ① 传根地址；② 每 20s 打一次进度，让「卡住」立刻可见。
    """
    import urllib.request
    import urllib.error
    t0 = time.time()
    last_log = 0.0
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(f"{root}/healthz", timeout=5) as r:
                d = json.loads(r.read().decode())
                if d.get("ok"):
                    return d
        except urllib.error.HTTPError as e:
            # 路由写错会走到这里（404）——明确报出来，别静默重试
            if e.code == 404:
                raise SystemExit(
                    f"[verify] /healthz 返回 404：URL 拼接有误（base={root}）")
        except Exception:                                    # noqa: BLE001
            pass
        if time.time() - last_log > 20:
            print(f"  … 等待服务就绪 {time.time()-t0:.0f}s", flush=True)
            last_log = time.time()
        time.sleep(3)
    raise SystemExit(f"[verify] 服务未在 {timeout}s 内就绪（模型加载失败？）")


def pick_card_texts(n: int = 2) -> list:
    """从人工池取真实效果文本（K2 的典型输入）

    **踩过的坑**：字段名是 `desc_zh`（中文），不是 `desc`。第一版按 `desc` 取值，
    结果**样例集为空、K2 检查被静默跳过**——8 项全 PASS 却漏掉了最关键的 K2 环节。
    教训：验证脚本里「取不到样例」必须显式失败，绝不能静默跳过（调用方已按此处理）。
    """
    p = REPO / "packages" / "engine" / "engine" / "data" / "cards_real.json"
    cards = json.loads(p.read_text(encoding="utf-8"))["cards"]
    out = []
    for c in cards:
        for a in (c.get("attacks") or []):
            txt = (a.get("desc_zh") or a.get("desc") or "").strip()
            if len(txt) >= 12 and a.get("damage"):
                out.append({"name": f"{c['name']} / {a['name']}", "text": txt})
            if len(out) >= n:
                return out
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="本地 Qwen3-4B 端到端接入验证")
    ap.add_argument("--quant", default=None, help="8bit（默认）/4bit")
    ap.add_argument("--out", default=str(REPO / "artifacts" / "local_llm_verify.json"))
    a = ap.parse_args()

    quant = (a.quant or "8bit").lower()
    port = free_port()
    root = f"http://127.0.0.1:{port}"                   # healthz 在根
    base = f"{root}/v1"                                 # OpenAI 端点在 /v1
    print(f"[verify] 量化档 {quant} · 服务 {root}", flush=True)

    # 环境：让 agent.llmclient 指向本地端点，并开完全离线模式
    os.environ["POKELAB_LLM_BASE"] = base
    os.environ["POKELAB_LLM_MODEL"] = f"qwen3-4b-{quant}"
    os.environ["POKELAB_LLM_TIMEOUT"] = "600"
    os.environ["POKELAB_LOCAL_ONLY"] = "1"
    os.environ["POKELAB_QWEN_QUANT"] = quant
    # 给生成设定上界：本地 8bit 约 3–4 tok/s，若不设界最坏要等 1024 token（≈5 分钟/次）。
    # 验证只需产出短 JSON / 一段解说，320 足够；同时也能顺带暴露「输出被截断」的问题。
    os.environ.setdefault("POKELAB_QWEN_MAX_NEW", "320")
    # 审计写到**系统临时目录**的独立文件：既避免污染真实审计日志
    # （`~/.pokelab/llm_audit.jsonl`），也不在受版本管理的 artifacts/ 里留空文件。
    # **不要用 unlink()**：本沙箱会拦截删除并要求移入回收站，回收站不可用时
    # fail-closed 抛 OSError（`SAFE_DELETE_FAIL_CLOSED`）。用「截断」达到同样效果。
    import tempfile
    audit = Path(tempfile.gettempdir()) / "pokelab_verify_audit.jsonl"
    with open(audit, "w", encoding="utf-8"):
        pass
    os.environ["POKELAB_AUDIT_LOG"] = str(audit)

    t0 = time.time()
    start_server(port)
    try:
        hz = wait_health(root)
        check("服务就绪（/healthz ok）", True,
              f"{time.time()-t0:.0f}s · quant={hz.get('quant')}")

        from agent import llmclient, effects, commentary, privacy

        # ① 端点契约 + ② 合规放行
        cfg = llmclient.config()
        check("llmclient 读到本地配置", cfg["base"] == base,
              f"source={llmclient.source()}")
        ext = privacy.is_external(base)
        check("本机端点被判为非外发（非 external）", ext is False,
              f"is_external={ext}")
        check("POKELAB_LOCAL_ONLY=1 下端点未被拒",
              privacy.check_endpoint(base)["external"] is False)

        r = llmclient.chat([{"role": "user", "content": "只回答两个字：正常。"}],
                           temperature=0, purpose="verify")
        check("OpenAI 协议调用返回内容", bool((r.get("content") or "").strip()),
              f"model={r.get('model')} · {r.get('elapsed_ms')}ms · "
              f"{(r.get('content') or '').strip()[:24]!r}")

        # ③ K2 效果解析（走 LLM 后端，不回退）
        #    注意：样例为空时必须**显式失败**——否则最关键的 K2 环节会被静默跳过，
        #    报告显示全 PASS 却什么也没验证（踩过）。
        texts = pick_card_texts(2)
        check("K2 样例集可用（从人工池取到效果文本）", len(texts) >= 1,
              f"取到 {len(texts)} 条")
        for c in texts:
            res = effects.parse_effects(c["text"], kind="attack",
                                        name=c["name"], backend="llm")
            ok = res["backend"] == "llm" and not res["errors"] and res["specs"]
            check(f"K2 解析（llm 后端）· {c['name'][:34]}", ok,
                  f"backend={res['backend']} · specs={len(res['specs'])} · "
                  f"errors={len(res['errors'])}")

        # ④ 对战解说（真实对局事件流）
        from engine import reallib
        from engine.agents import BaselineAI
        from engine.battle import Battle
        bt = Battle(reallib.build_deck("A1"), reallib.build_deck("A3"), seed=3)
        ai = [BaselineAI("a"), BaselineAI("b")]
        for _ in range(30):
            if bt.finished:
                break
            bt.step(ai[bt.current].decide(bt.view()))
        cm = commentary.explain_turn(bt.view(), bt.events, backend="llm",
                                     current_side=bt.current)
        check("对战解说走通本地模型（generated_by=llm）",
              cm.get("generated_by") == "llm",
              f"generated_by={cm.get('generated_by')} · "
              f"{(cm.get('text') or '')[:40]!r}")
        check("解说带 C4 标识（ai_generated/ai_notice）",
              cm.get("ai_generated") is True and bool(cm.get("ai_notice")),
              f"ai_notice={'有' if cm.get('ai_notice') else '无'}")

        # ⑤ 审计留痕（不含原文）
        n_audit = 0
        if audit.exists():
            lines = [ln for ln in audit.read_text(encoding="utf-8").splitlines() if ln.strip()]
            n_audit = len(lines)
            leaked = any("只回答两个字" in ln or "造成" in ln for ln in lines)
            check("审计已记录且不含原文", n_audit > 0 and not leaked,
                  f"{n_audit} 条 · 原文泄漏={'是 ❌' if leaked else '否 ✅'}")
        else:
            check("审计已记录", False, "审计文件未生成")
    finally:
        pass

    passed = sum(1 for r in RESULTS if r["ok"])
    total = len(RESULTS)
    payload = {"quant": quant, "base": base, "passed": passed, "total": total,
               "elapsed_s": round(time.time() - t0, 1), "results": RESULTS}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print(f"\n[verify] {passed}/{total} 通过 · 耗时 {payload['elapsed_s']}s"
          f" → {a.out}", flush=True)
    print("[verify] 提示：服务运行在本进程守护线程内，脚本退出即关闭。", flush=True)
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
