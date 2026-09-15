# -*- coding: utf-8 -*-
"""[已废弃 · DEPRECATED 2026-09-15] 解析 pending 卡 → effects_json

⚠ **本脚本已废弃，默认拒绝执行**（需 `--force-deprecated` 显式越过）。三条原因：

1. **状态机已空转**：它筛选 `cards.status='pending'`，而现库 1267 张卡**全部为
   `active`**（由 ETL `import_challenge_dataset.py` 一次性导入）→ `--all` 恒输出
   「无待解析卡」，实际功能为零。
2. **写入格式与现库不兼容 —— 误跑会静默破坏下游统计（最危险）**：
   本脚本写 `[{kind,trigger,conditions,actions,source_text,citations}]`
   （`pokelab.schema.CardEffect` 数组）；而现库 `effects_json` 的真实结构是
   `{"library": {…}, "parse": {items,specs,engine_tags,unsupported}}`，
   下游 `scripts/coverage_backlog.py` 读的正是 `effects_json.parse`。
   **一旦误跑，卡片会被改成没有 `parse` 键的形状 → coverage 统计悄悄归零**
   （不抛错，只是数字变 0，极难发现）。
3. **已被取代**：效果解析的现行入口是 `packages/agent/agent/effects.py::parse_effects`
   （`backend=rule|llm|auto`），经 `llmclient.chat()` 调 OpenAI 兼容端点 —— 既可指向
   本地 Qwen3-4B 服务，也可指云端；评测走 `packages/agent/examples/run_k2.py`。
   旧路径的注入式客户端 `pokelab/effects.py::llm_parse_text` 亦随之废弃。

替代路径
--------
  # ① 起本地模型服务（OpenAI 兼容，127.0.0.1 属本机端点，离线模式可用）
  python apps/api/scripts/local_qwen.py serve
  # ② 解析 + 评测（不落库，产出 artifacts/k2_report.*）
  POKELAB_LLM_BASE=http://127.0.0.1:9997/v1 python packages/agent/examples/run_k2.py --backend llm

自检（**零副作用**：只读库、不加载模型、不写入）
--------------------------------------------
  python scripts/parse_effects.py --check

保留旧实现仅为可追溯（历史 commit 与设计文档仍引用它）；如需真正跑通，
请改用上面的替代路径，而不是本脚本。
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pokelab.db import connect  # noqa: E402

DEPRECATION_NOTICE = """\
[已废弃] parse_effects.py 自 2026-09-15 起停止使用，默认拒绝执行。

原因：
  1) 状态机空转：它找 cards.status='pending'，而现库全部为 active → 恒无待解析卡；
  2) **写入格式不兼容**：本脚本写 [{CardEffect}] 数组，而现库 effects_json 为
     {"library":…, "parse":…}，下游 coverage_backlog.py 读的是 .parse
     → 误跑会把该键抹掉，coverage 统计静默归零（不报错）；
  3) 已被 packages/agent/agent/effects.py::parse_effects（rule|llm|auto）取代。

请改用：
  python apps/api/scripts/local_qwen.py serve
  POKELAB_LLM_BASE=http://127.0.0.1:9997/v1 \\
      python packages/agent/examples/run_k2.py --backend llm

查看当前库状态（零副作用）：python scripts/parse_effects.py --check
确要执行旧逻辑（**会破坏 effects_json 结构，仅限明确的迁移/调试场景**）：
      python scripts/parse_effects.py --all --force-deprecated
"""


def check() -> int:
    """零副作用自检：证明本脚本确实空转，并暴露 effects_json 的真实结构

    只读，不加载模型、不写库。之所以做这个而不是写一个「废弃即跳过」的测试：
    card-db 包没有测试框架，为一个死脚本新建一套不划算；而把「为什么废弃」
    变成可随时复跑的证据，比一句注释更有用。
    """
    con = connect()
    print("=" * 62)
    print("parse_effects.py 状态自检（已废弃）")
    print("=" * 62)
    tbl = con.execute(
        "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='cards'"
    ).fetchone()[0]
    if not tbl:
        print("cards 表不存在 —— 请先执行导库（见 README）")
        con.close()
        return 1

    total = con.execute("SELECT count(*) FROM cards").fetchone()[0]
    print(f"\n① 卡数：{total}")
    by_status = con.execute(
        "SELECT status, count(*) FROM cards GROUP BY status ORDER BY 2 DESC").fetchall()
    for s, n in by_status:
        print(f"     status={s!r:<14} {n}")

    pend = con.execute(
        "SELECT count(*) FROM cards WHERE status='pending'").fetchone()[0]
    print(f"\n② 本脚本的筛选条件 status='pending' 命中：{pend} 条")
    if pend == 0:
        print("     → 恒输出「无待解析卡」，功能为零（这就是空转的直接证据）")
    else:
        print(f"     → 有 {pend} 条 pending；但注意第 ③ 条的格式冲突依然存在")

    print("\n③ effects_json 的真实结构（本脚本写入的格式与之**不兼容**）")
    shapes: dict = {}
    for (ej,) in con.execute("SELECT effects_json FROM cards"):
        if not ej or not str(ej).strip():
            k = "<空>"
        else:
            try:
                d = json.loads(ej)
            except Exception:                                # noqa: BLE001
                k = "<非法 JSON>"
            else:
                if isinstance(d, list):
                    k = "list[...] ← 本脚本写的就是这种"
                elif isinstance(d, dict):
                    k = "{" + ",".join(sorted(d.keys())) + "}"
                else:
                    k = type(d).__name__
        shapes[k] = shapes.get(k, 0) + 1
    for k, n in sorted(shapes.items(), key=lambda x: -x[1]):
        print(f"     {k:<46} {n}")

    parsed = 0
    for (ej,) in con.execute("SELECT effects_json FROM cards"):
        if not ej:
            continue
        try:
            d = json.loads(ej)
        except Exception:                                    # noqa: BLE001
            continue
        # 必须先判 dict 再取键：list 形态（本脚本写的格式）没有 .get()
        if isinstance(d, dict) and d.get("parse"):
            parsed += 1
    print(f"\n④ 下游 coverage_backlog.py 依赖的 effects_json.parse 可用卡数：{parsed}")
    print("     误跑本脚本会让这个数字掉到 0（且不报错）")
    print("\n结论：本脚本应废弃，效果解析请走 agent.effects.parse_effects + run_k2.py")
    con.close()
    return 0


def load_qwen():
    """[已废弃路径] 旧版自带 8bit 加载（现由 apps/api/scripts/local_qwen.py 统一承担）"""
    import time
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from transformers.utils import logging as hflog
    hflog.set_verbosity_error()
    MODEL = r"D:\pokemo\qwen3-4b\moxing"
    tok = AutoTokenizer.from_pretrained(MODEL)
    q = BitsAndBytesConfig(load_in_8bit=True)
    t0 = time.time()
    model = AutoModelForCausalLM.from_pretrained(
        MODEL, quantization_config=q, device_map="auto",
        max_memory={0: "5.2GB", "cpu": "24GB"}, low_cpu_mem_usage=True)
    print(f"[qwen] 8bit 加载完成 {time.time()-t0:.0f}s (footprint "
          f"{model.get_memory_footprint()/1024**3:.2f}GB)", flush=True)
    return model, tok


def main():
    ap = argparse.ArgumentParser(
        description="[已废弃] 解析 pending 卡 → effects_json（见文件头说明）")
    ap.add_argument("--card-id", type=int, default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="零副作用自检：报告废弃理由与库现状（推荐）")
    ap.add_argument("--force-deprecated", action="store_true",
                    help="越过废弃保护执行旧逻辑（会破坏 effects_json 结构）")
    a = ap.parse_args()

    if a.check:
        return check()

    if not a.force_deprecated:
        print(DEPRECATION_NOTICE, file=sys.stderr)
        return 2

    print("[警告] 已越过废弃保护：旧逻辑写入的格式与现库不兼容，"
          "会破坏 coverage_backlog.py 的输入。", file=sys.stderr)

    con = connect()
    if a.card_id:
        rows = con.execute("SELECT id,name,rules_text FROM cards WHERE id=? AND status='pending'", (a.card_id,)).fetchall()
    elif a.all:
        rows = con.execute("SELECT id,name,rules_text FROM cards WHERE status='pending' AND rules_text IS NOT NULL LIMIT 20").fetchall()
    else:
        ap.error("需 --card-id N 或 --all")
    if not rows:
        print("[parse] 无待解析卡"); return 0
    model, tok = load_qwen()
    from pokelab.effects import llm_parse_text
    done = 0
    for r in rows:
        try:
            effs = llm_parse_text(r["rules_text"], model, tok)
            con.execute(
                "UPDATE cards SET effects_json=?, status='active', updated_at=datetime('now','localtime') WHERE id=?",
                (json.dumps([e.model_dump() for e in effs], ensure_ascii=False), r["id"]))
            con.commit()
            print(f"[parse][OK] {r['name']} → {len(effs)} 条效果", flush=True)
            done += 1
        except Exception as e:
            print(f"[parse][FAIL] {r['name']}: {type(e).__name__}: {str(e)[:160]}（保持 pending，转人工）", flush=True)
    con.close()
    print(f"[parse] 完成 {done}/{len(rows)}")


if __name__ == "__main__":
    sys.exit(main())
