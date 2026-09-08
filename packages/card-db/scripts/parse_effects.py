# -*- coding: utf-8 -*-
"""用本地 Qwen3-4B（8bit，pokemo 环境）解析 cards 中 pending 卡 → effects_json
用法：
  python scripts/parse_effects.py [--card-id 1] [--all]
复用部署说明 Step 3 的 8bit 全 GPU 配置（max_memory 整数键）。
"""
import argparse, json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pokelab.db import connect


def load_qwen():
    import torch
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
    ap = argparse.ArgumentParser()
    ap.add_argument("--card-id", type=int, default=None)
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    con = connect()
    if a.card_id:
        rows = con.execute("SELECT id,name,rules_text FROM cards WHERE id=? AND status='pending'", (a.card_id,)).fetchall()
    elif a.all:
        rows = con.execute("SELECT id,name,rules_text FROM cards WHERE status='pending' AND rules_text IS NOT NULL LIMIT 20").fetchall()
    else:
        ap.error("需 --card-id N 或 --all")
    if not rows:
        print("[parse] 无待解析卡"); return
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
    main()
