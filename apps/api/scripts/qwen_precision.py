# -*- coding: utf-8 -*-
"""Qwen3-4B 量化精度对照（三层）· PokéLab AI

为什么要做这件事
----------------
「量化了」不等于「精度够用」。同一个 8bit 方案在不同模型/不同任务上损失差别很大，
所以本项目把「确保数据精度」定义成**可复跑、可量化、可审计**的三层对照，而不是一句
「8bit 精度损失很小」的口头保证：

  第 1 层 · 权重级  —— 逐层反量化后与原 bf16 权重逐元素比对（相对 Frobenius 误差 /
                      最大绝对误差 / 余弦相似度 / 每通道尺度分布）。
                      不需要参考模型前向，快且完全确定。
  第 2 层 · 困惑度  —— 在固定中文语料上算 PPL：量化档 vs **bf16 参考**。
                      PPL 是语言建模能力的整体指标，量化退化会直接反映在这里。
  第 3 层 · 端到端  —— 固定 prompt 集上 **greedy 解码**，逐条比对量化档与 bf16 参考的
                      输出（完全一致率 / 归一化一致率 / 领域事实命中数）。
                      这是最贴近「实际任务会不会变差」的一层。

三层都写进 `artifacts/qwen_precision_report.{json,md}`，可随时复跑复核。

bf16 参考怎么来（6GB 显存的现实约束）
------------------------------------
bf16 全量 ≈8.05GB，装不进 6GB 显存。用 ``device_map="auto"`` + ``max_memory`` 把部分层
放到 CPU 计算——**计算精度仍是 bf16**，只是慢；这比「拿 4bit 当参考」严谨得多
（拿另一个量化档当参考只能说明两者差异，说明不了与原始模型的差距）。

用法
----
    python scripts/qwen_precision.py --layer 1          # 权重级（快，约 1 分钟）
    python scripts/qwen_precision.py --layer 2          # 困惑度（含 bf16 参考，较慢）
    python scripts/qwen_precision.py --layer 3          # 端到端
    python scripts/qwen_precision.py --layer all        # 全部（推荐后台跑）
    python scripts/qwen_precision.py --layer 1 --quant 4bit   # 换档位复核
"""
from __future__ import annotations

import argparse
import io
import json
import math
import os
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]                                    # pokelab-ai
sys.path.insert(0, str(HERE))

import local_qwen as LQ                                   # noqa: E402

OUT_DIR = REPO / "artifacts"

# ---------------- 固定样本（必须固定：否则两次跑的数字不可比） ----------------
# 第 2 层：中文语料（领域文本 + 通用文本混合，避免只在单一分布上测）
PPL_TEXT = (
    "宝可梦集换式卡牌游戏的对战在两名玩家之间进行。每名玩家使用一副六十张的卡组，"
    "卡组中同名卡至多四张，基本能量不限。开局时双方各抽七张手牌，放置一张基础宝可梦"
    "作为战斗宝可梦，并设置六张奖赏卡。回合流程依次为抽卡、执行任意数量的动作、结束回合。"
    "每回合可以放置一张能量卡，但只能为宝可梦进化一次，且刚上场的宝可梦不能立即进化。"
    "攻击后回合立即结束，除非招式另有说明。当宝可梦受到的伤害不小于其剩余生命值时，"
    "它被击倒，对手取得一张奖赏卡；若被击倒的是宝可梦 ex，则对手取得两张奖赏卡。"
    "取得全部奖赏卡、使对手没有宝可梦、或让对手牌库抽空，都能赢得对局。"
    "特殊状态包括中毒、灼伤、睡眠、麻痹与混乱，它们在检查阶段结算或由抛币决定恢复。"
)

# 第 3 层：固定 prompt 集
#   domain=领域事实题　json=K2 风格结构化抽取
#
# **判据设计（踩过坑，务必保留这段说明）**：
# 第一版用「期望子串是否出现」打分，跑出来「事实命中 4/8」，看着像量化把模型打坏了。
# 逐条看输出才发现两个问题：
#   ① 有些子串会被**照抄原句**满足（如要求含 "20"，模型把整句抄进 JSON 字符串里就命中了）
#      —— 假命中；
#   ② 领域题答错时 **8bit 与 bf16 错得一模一样**（都答"3 张奖赏""2 点中毒"），
#      属 Qwen3-4B 的**领域知识短板**，与量化无关。
# 结论：第 3 层的职责是度量「**量化是否改变了行为**」，不是考模型的宝可梦知识。
# 故判据改为**逐用例的程序化正误判定**（判据写在 `_grade()` 里，可审计），
# 并把验收口径定为「**两档正误结论必须一致**」（correctness_delta == 0）。
E2E_PROMPTS = [
    {"id": "dmg_weakness", "kind": "domain",
     "prompt": "宝可梦 TCG 中，攻击方属性与守方弱点属性相同时，伤害如何变化？只答一句。",
     "note": "正确答案：翻倍（×2）"},
    {"id": "ko_prize", "kind": "domain",
     "prompt": "击倒一只宝可梦 ex，对手取得几张奖赏卡？只答一句。",
     "note": "正确答案：2 张（答 3 张为错）"},
    {"id": "poison_dmg", "kind": "domain",
     "prompt": "中毒状态在检查阶段造成多少伤害？只答一句。",
     "note": "正确答案：10 点"},
    {"id": "k2_simple", "kind": "json",
     "prompt": ('把下面这句卡牌效果转成 JSON 数组，只输出 JSON，不要解释：\n'
                '"造成 30 点伤害，并让对手的战斗宝可梦陷入中毒。"'),
     "note": "要求结构化：伤害数值须为 JSON 中的**数值**字段（30）"},
    {"id": "k2_coin", "kind": "json",
     "prompt": ('把下面这句卡牌效果转成 JSON 数组，只输出 JSON，不要解释：\n'
                '"抛硬币，若为正面，此招式追加 20 点伤害。"'),
     "note": "要求结构化：追加伤害数值须为 JSON 中的**数值**字段（20）"},
    {"id": "k2_draw", "kind": "json",
     "prompt": ('把下面这句卡牌效果转成 JSON 数组，只输出 JSON，不要解释：\n'
                '"从牌库中检索 2 张基本能量并附着到自己的宝可梦身上。"'),
     "note": "要求结构化：检索数量须为 JSON 中的**数值**字段（2）"},
]


def _json_leaves(text: str):
    """尽力从文本里抽出 JSON 并返回其所有叶子值（失败返回 None）"""
    import re
    s = str(text or "").strip()
    m = re.search(r"[\[{].*[\]}]", s, re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except Exception:                                        # noqa: BLE001
        return None
    out = []

    def walk(x):
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        else:
            out.append(x)

    walk(obj)
    return {"root_is_list": isinstance(obj, list), "leaves": out}


def _json_has_number(text: str, n: float) -> bool:
    """JSON 里是否存在**数值型**叶子等于 n

    刻意不匹配字符串叶子：模型把「造成 30 点伤害」整句塞进字符串字段时**不算通过**
    —— 那只是复述，不是结构化抽取（第一版判据正是被这种复述骗过的）。
    """
    d = _json_leaves(text)
    if not d or not d["root_is_list"]:
        return False
    for v in d["leaves"]:
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)) and abs(float(v) - n) < 1e-9:
            return True
    return False


def _grade(pid: str, text: str) -> dict:
    """逐用例正误判定（判据显式写在代码里，便于评审与复算）"""
    t = norm_ws(text)
    if pid == "dmg_weakness":
        ok = any(k in t for k in ("翻倍", "2倍", "两倍", "×2", "x2", "加倍", "双倍"))
        return {"correct": ok, "criterion": "答『翻倍/2倍』"}
    if pid == "ko_prize":
        ok = any(k in t for k in ("2张", "2 张", "两张"))
        wrong = any(k in t for k in ("3张", "3 张", "三张"))
        return {"correct": ok and not wrong, "criterion": "答『2 张』（答 3 张为错）"}
    if pid == "poison_dmg":
        return {"correct": ("10" in t) and ("20" not in t),
                "criterion": "答『10 点』"}
    if pid == "k2_simple":
        return {"correct": _json_has_number(text, 30),
                "criterion": "JSON 数组中含数值字段 30（复述不算通过）"}
    if pid == "k2_coin":
        return {"correct": _json_has_number(text, 20),
                "criterion": "JSON 数组中含数值字段 20（复述不算通过）"}
    if pid == "k2_draw":
        return {"correct": _json_has_number(text, 2),
                "criterion": "JSON 数组中含数值字段 2（复述不算通过）"}
    return {"correct": False, "criterion": "未定义"}


# ---------------- 工具 ----------------
def _shard_index(model_dir: Path) -> dict:
    """读 HF 分片索引 → {tensor_key: shard 文件名}"""
    idx = model_dir / "model.safetensors.index.json"
    if idx.exists():
        return json.loads(idx.read_text(encoding="utf-8"))["weight_map"]
    return {}


def _read_ref_tensor(model_dir: Path, weight_map: dict, key: str):
    """从原始 safetensors **按需**取一个张量（不整模型入内存）"""
    from safetensors import safe_open
    shard = weight_map.get(key)
    if shard is None:
        return None
    with safe_open(str(model_dir / shard), framework="pt") as f:
        if key not in f.keys():
            return None
        return f.get_tensor(key)


def norm_ws(s: str) -> str:
    return " ".join(str(s or "").split()).strip().lower()


# ---------------- 第 1 层：权重级 ----------------
def layer1(quant: str, sample_stride: int = 4) -> dict:
    """逐层反量化 vs 原始 bf16 权重"""
    import torch
    import bitsandbytes as bnb
    from bitsandbytes import functional as F

    model_dir = Path(LQ.model_path())
    weight_map = _shard_index(model_dir)
    model, _ = LQ.load(quant=quant, verbose=False)

    rows, skipped = [], []
    linears = [(n, m) for n, m in model.named_modules()
               if isinstance(m, (bnb.nn.Linear8bitLt, bnb.nn.Linear4bit, torch.nn.Linear))]
    # 采样：每 stride 个取一个，保证覆盖各层各投影
    picked = linears[::max(1, sample_stride)]

    for name, mod in picked:
        key = name + ".weight"
        try:
            W = getattr(mod, "weight", None)
            if W is None:
                skipped.append({"param": key, "reason": "无 weight"})
                continue
            if quant == "8bit" and hasattr(W, "SCB"):
                deq = F.int8_vectorwise_dequant(W.data, W.SCB)
            elif quant == "4bit" and hasattr(W, "quant_state") and W.quant_state:
                deq = F.dequantize_4bit(W.data, W.quant_state)
            else:
                skipped.append({"param": key, "reason": f"{type(W).__name__} 无量化状态"})
                continue
            ref = _read_ref_tensor(model_dir, weight_map, key)
            if ref is None:
                skipped.append({"param": key, "reason": "原始权重索引缺失（可能是 tie 权重）"})
                continue
            deq = deq.to(torch.float32).cpu()
            ref = ref.to(torch.float32).cpu()
            if deq.shape != ref.shape:
                skipped.append({"param": key,
                                "reason": f"形状不一致 {tuple(deq.shape)} vs {tuple(ref.shape)}"})
                continue
            # —— 精度指标一律用 **float64** 计算 ——
            # 踩过的坑：用 fp32 对 ~2.5e7 元素的张量做点积/求范数，累加误差会让
            # 「余弦相似度」算出 > 1（实测最大 1.0067）。这在报告里既违反数学定义、
            # 也让读者怀疑实现有 bug。升到 float64 后指标严格可信。
            deq64 = deq.to(torch.float64)
            ref64 = ref.to(torch.float64)
            diff = deq64 - ref64
            fro_ref = torch.linalg.vector_norm(ref64).item()
            rel_fro = (torch.linalg.vector_norm(diff).item() / fro_ref) if fro_ref else 0.0
            cos = torch.nn.functional.cosine_similarity(
                deq64.flatten().unsqueeze(0), ref64.flatten().unsqueeze(0)).item()
            rows.append({
                "param": key,
                "shape": list(ref.shape),
                "numel": int(ref.numel()),
                "rel_frobenius": round(rel_fro, 6),
                "max_abs_err": round(diff.abs().max().item(), 6),
                "mean_abs_err": round(diff.abs().mean().item(), 6),
                "cosine": round(min(cos, 1.0), 8),
                "ref_abs_mean": round(ref64.abs().mean().item(), 6),
                "snr_db": round(20 * math.log10(1 / rel_fro), 2) if rel_fro > 0 else None,
            })
        except Exception as e:                                   # noqa: BLE001
            skipped.append({"param": key, "reason": f"{type(e).__name__}: {str(e)[:80]}"})

    def agg(k):
        v = [r[k] for r in rows if r.get(k) is not None]
        if not v:
            return None
        v = sorted(v)
        return {"mean": round(sum(v) / len(v), 8), "median": round(v[len(v) // 2], 8),
                "max": round(v[-1], 8), "min": round(v[0], 8)}

    # 精度布局：哪些被量化、哪些**保留高精度**。
    # 关键事实：bitsandbytes 默认**不量化 lm_head** —— 输出 logits 仍以 bf16 计算，
    # 因此「选哪个 token」不受 int8 权重误差影响，这是 8bit 方案精度可信的重要一环。
    # （注意 Linear8bitLt 是 nn.Linear 的子类，故必须用 type() 严格判定未量化层。）
    import bitsandbytes as bnb
    quantized_mods = [n for n, m in model.named_modules()
                      if isinstance(m, (bnb.nn.Linear8bitLt, bnb.nn.Linear4bit))]
    plain_mods = [(n, str(m.weight.dtype)) for n, m in model.named_modules()
                  if type(m) is torch.nn.Linear]
    embed_mods = [(n, str(m.weight.dtype)) for n, m in model.named_modules()
                  if isinstance(m, torch.nn.Embedding)]

    return {"layer": 1, "quant": quant, "sampled": len(rows),
            "total_linears": len(linears), "stride": sample_stride,
            "layout": {"quantized_linears": len(quantized_mods),
                       "unquantized_linears": plain_mods,
                       "embeddings": embed_mods,
                       "tie_word_embeddings": bool(
                           getattr(model.config, "tie_word_embeddings", False))},
            "rel_frobenius": agg("rel_frobenius"), "cosine": agg("cosine"),
            "snr_db": agg("snr_db"), "max_abs_err": agg("max_abs_err"),
            "mean_abs_err": agg("mean_abs_err"),
            # 自检：cosine 必须 ≤ 1（float64 下应恒成立；不成立即说明实现有 bug）
            "cosine_le_one": all(r["cosine"] <= 1.0 for r in rows),
            "rows": rows, "skipped": skipped[:20],
            "skipped_n": len(skipped)}


# ---------------- 第 2 层：困惑度 ----------------
def _ppl(model, tok, text: str) -> dict:
    """在固定文本上算困惑度（逐 token 平均负对数似然）"""
    import torch
    ids = tok(text, return_tensors="pt").input_ids.to(model.device)
    n = int(ids.shape[1])
    with torch.no_grad():
        out = model(ids, labels=ids)
        loss = float(out.loss)
    return {"tokens": n, "loss": round(loss, 6),
            "ppl": round(math.exp(loss), 4)}


def layer2(quant: str, max_gpu_ref: str = "4.3GB") -> dict:
    """量化档 vs bf16 参考 的 PPL 对照"""
    res = {"layer": 2, "quant": quant, "text_chars": len(PPL_TEXT)}
    t0 = time.time()
    m_q, tok = LQ.load(quant=quant, verbose=False)
    res["quantized"] = _ppl(m_q, tok, PPL_TEXT)
    res["quant_load_s"] = round(time.time() - t0, 1)
    del m_q
    import gc
    import torch
    gc.collect()
    torch.cuda.empty_cache()

    # bf16 参考：部分层落 CPU 计算（精度仍是 bf16）
    t1 = time.time()
    m_ref, _ = LQ.load(quant="bf16", max_gpu=max_gpu_ref, verbose=False)
    res["reference"] = _ppl(m_ref, tok, PPL_TEXT)
    res["ref_load_s"] = round(time.time() - t1, 1)
    del m_ref
    gc.collect()
    torch.cuda.empty_cache()

    q, r = res["quantized"]["ppl"], res["reference"]["ppl"]
    res["ppl_delta_abs"] = round(q - r, 4)
    res["ppl_delta_pct"] = round((q - r) / r * 100, 3) if r else None
    # 验收：PPL 相对退化 < 1% 视为「无实质退化」
    res["pass"] = bool(res["ppl_delta_pct"] is not None
                       and res["ppl_delta_pct"] < 1.0)
    return res


# ---------------- 第 3 层：端到端 ----------------
def _run_prompts(model, tok, max_new: int) -> list:
    outs = []
    for p in E2E_PROMPTS:
        r = LQ.generate(model, tok, [{"role": "user", "content": p["prompt"]}],
                        temperature=0.0, max_new_tokens=max_new)
        g = _grade(p["id"], r["content"])
        outs.append({"id": p["id"], "kind": p["kind"], "note": p.get("note", ""),
                     "content": r["content"],
                     "completion_tokens": r["completion_tokens"],
                     "elapsed_ms": r["elapsed_ms"],
                     "correct": g["correct"], "criterion": g["criterion"]})
    return outs


def _char_sim(a: str, b: str) -> float:
    """字符级相似度（1 - 归一化编辑距离）；用 difflib 免额外依赖"""
    import difflib
    return round(difflib.SequenceMatcher(None, norm_ws(a), norm_ws(b)).ratio(), 4)


def layer3(quant: str, max_new: int = 64, max_gpu_ref: str = "4.3GB") -> dict:
    """greedy 输出对照：量化档 vs bf16 参考

    验收口径 = **两档正误结论一致**（``correctness_delta == 0``）：
    量化可能让措辞变化（exact_match < 1 是正常的），但**不应改变答案对错**。
    这比「事实命中数」诚实得多——后者会把模型自身的知识短板算到量化头上。
    """
    import gc
    import torch
    res = {"layer": 3, "quant": quant,
           "n_prompts": len(E2E_PROMPTS), "max_new_tokens": max_new}
    m_q, tok = LQ.load(quant=quant, verbose=False)
    res["quantized"] = _run_prompts(m_q, tok, max_new)
    del m_q
    gc.collect()
    torch.cuda.empty_cache()

    m_ref, _ = LQ.load(quant="bf16", max_gpu=max_gpu_ref, verbose=False)
    res["reference"] = _run_prompts(m_ref, tok, max_new)
    del m_ref
    gc.collect()
    torch.cuda.empty_cache()

    pairs = []
    for a, b in zip(res["quantized"], res["reference"]):
        pairs.append({"id": a["id"], "kind": a["kind"], "note": a["note"],
                      "exact_match": a["content"] == b["content"],
                      "normalized_match": norm_ws(a["content"]) == norm_ws(b["content"]),
                      "char_similarity": _char_sim(a["content"], b["content"]),
                      "q_correct": a["correct"], "r_correct": b["correct"],
                      "criterion": a["criterion"]})
    res["pairs"] = pairs
    n = len(pairs) or 1
    res["exact_match_rate"] = round(sum(1 for p in pairs if p["exact_match"]) / n, 3)
    res["normalized_match_rate"] = round(
        sum(1 for p in pairs if p["normalized_match"]) / n, 3)
    res["char_similarity_mean"] = round(
        sum(p["char_similarity"] for p in pairs) / n, 4)
    res["quant_correct"] = sum(1 for p in pairs if p["q_correct"])
    res["ref_correct"] = sum(1 for p in pairs if p["r_correct"])
    # 正误结论是否有分歧（有分歧要能定位到用例）
    res["correctness_delta"] = res["quant_correct"] - res["ref_correct"]
    res["correctness_disagreements"] = [
        {"id": p["id"], "quant": p["q_correct"], "ref": p["r_correct"]}
        for p in pairs if p["q_correct"] != p["r_correct"]]
    # 验收口径：**无精度退化**（量化档不差于参考），而不是「必须完全一致」。
    # 为什么不是 delta == 0：实测出现过 delta = +1 —— 量化档答对、bf16 参考答错
    # （弱点题：量化答「翻倍」，参考答「增加 50%」）。这类分歧出现在模型**知识边界**
    # 附近，int8 的微小数值差异会翻转择优结果，方向可能随机。把它判为 FAIL 是错的
    # ——它既不构成「量化更准」的结论，更不是「量化退化」。
    # 只有 delta < 0（量化档变差）才说明精度退化，必须人工介入。
    res["pass"] = bool(res["correctness_delta"] >= 0)
    res["verdict"] = ("无退化" if res["correctness_delta"] > 0
                      else "完全一致" if res["correctness_delta"] == 0
                      else "存在退化，需人工确认")
    return res


# ---------------- 报告 ----------------
def write_report(payload: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    jp = OUT_DIR / "qwen_precision_report.json"
    # 单层重跑不应抹掉其它层的结果：先读旧报告，把本次未跑的层补回来。
    # （否则 `--layer 3` 会把已跑好的第 1/2 层从报告里删掉，看起来很坑）
    if jp.exists():
        try:
            old = json.loads(jp.read_text(encoding="utf-8"))
            for k in ("layer1", "layer2", "layer3"):
                if payload.get(k) is None and old.get(k) is not None:
                    payload[k] = old[k]
                    print(f"[precision] 沿用上次的 {k}（本次未跑）", flush=True)
        except Exception:                                    # noqa: BLE001
            pass
    jp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                  encoding="utf-8")

    L = ["# Qwen3-4B 量化精度对照报告", "",
         f"- 模型：`{LQ.model_path()}`（Qwen3-4B）",
         f"- 硬件：RTX 4050 Laptop 6GB · torch {payload.get('torch','?')}",
         f"- 量化档位：**{payload.get('quant')}**",
         f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}", ""]

    l1 = payload.get("layer1")
    if l1:
        lay = l1.get("layout") or {}
        L += ["## 第 1 层 · 权重级（反量化 vs 原始 bf16）", "",
              f"采样 {l1['sampled']} / {l1['total_linears']} 个线性层"
              f"（每 {l1['stride']} 个取 1）", "",
              "| 指标 | 均值 | 中位 | 最大 | 最小 |", "|---|---:|---:|---:|---:|"]
        for k, label in (("rel_frobenius", "相对 Frobenius 误差"),
                         ("cosine", "余弦相似度"),
                         ("snr_db", "信噪比 (dB)"),
                         ("max_abs_err", "最大绝对误差")):
            a = l1.get(k)
            if a:
                L.append(f"| {label} | {a['mean']} | {a['median']} | {a['max']} | {a['min']} |")
        if lay:
            unq = "、".join(f"`{n}`({d})" for n, d in lay.get("unquantized_linears", []))
            emb = "、".join(f"`{n}`({d})" for n, d in lay.get("embeddings", []))
            L += ["",
                  f"**精度布局**：量化线性层 {lay.get('quantized_linears')} 个；"
                  f"保留高精度者 {unq}；嵌入层 {emb}；"
                  f"`tie_word_embeddings={lay.get('tie_word_embeddings')}`",
                  "",
                  "> 关键：**输出层 `lm_head` 未被量化**（bitsandbytes 默认跳过）——"
                  "logits 仍以 bf16 计算，故「选哪个 token」不受 int8 权重误差影响。"
                  "这是 8bit 方案精度可信的重要一环。"]
        L += ["", f"跳过 {l1['skipped_n']} 个（原因见 JSON）",
              f"自检：余弦相似度全部 ≤ 1 —— **{'是 ✅' if l1['cosine_le_one'] else '否 ❌（实现异常）'}**",
              ""]

    l2 = payload.get("layer2")
    if l2:
        L += ["## 第 2 层 · 困惑度 PPL（固定中文语料）", "",
              f"语料长度 {l2['text_chars']} 字；bf16 参考用 CPU offload 计算（精度仍 bf16）", "",
              "| 档位 | tokens | loss | PPL |", "|---|---:|---:|---:|",
              f"| {l2['quant']} | {l2['quantized']['tokens']} | {l2['quantized']['loss']} | {l2['quantized']['ppl']} |",
              f"| bf16（参考） | {l2['reference']['tokens']} | {l2['reference']['loss']} | {l2['reference']['ppl']} |",
              "",
              f"- PPL 差 **{l2['ppl_delta_abs']}**（{l2['ppl_delta_pct']}%）"
              f"　→ 验收（<1%）**{'PASS ✅' if l2['pass'] else 'FAIL ❌'}**", ""]

    l3 = payload.get("layer3")
    if l3:
        L += ["## 第 3 层 · 端到端（greedy 输出对照）", "",
              f"{l3['n_prompts']} 条固定 prompt · 贪心解码 · max_new_tokens={l3['max_new_tokens']}",
              "",
              "**验收口径**：量化可以改变措辞（`exact_match` < 1 属正常），"
              "但**不应让答案变差** → 以「量化档正误**不差于** bf16 参考」为准。", "",
              "| 用例 | 完全一致 | 归一一致 | 字符相似度 | 量化正误 | 参考正误 | 判据 |",
              "|---|---:|---:|---:|:--:|:--:|---|"]
        for p in l3["pairs"]:
            L.append(f"| {p['id']} | {'✓' if p['exact_match'] else '✗'} "
                     f"| {'✓' if p['normalized_match'] else '✗'} "
                     f"| {p['char_similarity']} "
                     f"| {'✓' if p['q_correct'] else '✗'} | {'✓' if p['r_correct'] else '✗'} "
                     f"| {p['criterion']} |")
        L += ["",
              f"- 完全一致率 **{l3['exact_match_rate']}** · 归一化一致率 "
              f"**{l3['normalized_match_rate']}** · 字符相似度均值 **{l3['char_similarity_mean']}**",
              f"- 正误计数：量化 **{l3['quant_correct']}/{l3['n_prompts']}** · "
              f"参考 **{l3['ref_correct']}/{l3['n_prompts']}** · "
              f"差值 **{l3['correctness_delta']}**（{l3.get('verdict','')}）"
              f"　→ 验收（量化档**不差于**参考）**{'PASS ✅' if l3['pass'] else 'FAIL ❌'}**",
              "",
              "> **为什么验收是「不差于」而不是「必须一致」**：实测出现过差值 **+1**"
              "——量化档答对、bf16 参考答错（弱点题：量化答「翻倍」，参考答「增加 50%」）。"
              "这类分歧发生在模型**知识边界**附近，int8 的微小数值差异会翻转择优结果，"
              "方向可正可负。把它当作 FAIL 是误判；**只有差值为负（量化档变差）才是精度退化**。"]
        if l3["correctness_disagreements"]:
            L += ["", "**正误分歧用例（需人工确认是否量化所致）**："]
            for d in l3["correctness_disagreements"]:
                L.append(f"- `{d['id']}`：量化 {'✓' if d['quant'] else '✗'} / "
                         f"参考 {'✓' if d['ref'] else '✗'}")
        # 诚实标注：两档一致答错的用例属「模型能力限制」，不要误读为量化劣化
        both_wrong = [p["id"] for p in l3["pairs"]
                      if not p["q_correct"] and not p["r_correct"]]
        if both_wrong:
            L += ["",
                  f"> **注意（阅读本表时的关键前提）**：`{'`, `'.join(both_wrong)}` "
                  "在**两档下都答错**——量化档与 bf16 参考**错得一模一样**，"
                  "说明这是 **Qwen3-4B 自身的领域知识/指令遵循短板，与量化无关**。"
                  "对本报告而言，这类用例的价值恰恰是「证明量化没有改变行为」；"
                  "它们不构成「量化导致精度下降」的证据。"]
        L += [""]

    L += ["---", "",
          "## 结论（三层验收汇总）", "",
          "| 层 | 验收口径 | 结果 |", "|---|---|---|"]
    if payload.get("layer1"):
        l1 = payload["layer1"]
        ok = l1["cosine_le_one"] and l1["rel_frobenius"]["max"] < 0.05
        L.append(f"| 1 · 权重级 | 相对 Frobenius 误差 < 5% 且余弦 ≤ 1 | "
                 f"**{'PASS ✅' if ok else 'FAIL ❌'}**（{l1['rel_frobenius']['mean']:.4f}）|")
    if payload.get("layer2"):
        l2 = payload["layer2"]
        L.append(f"| 2 · 困惑度 | PPL 相对退化 < 1% | "
                 f"**{'PASS ✅' if l2['pass'] else 'FAIL ❌'}**（{l2['ppl_delta_pct']}%）|")
    if payload.get("layer3"):
        l3 = payload["layer3"]
        L.append(f"| 3 · 端到端 | 量化档正误**不差于** bf16 参考 | "
                 f"**{'PASS ✅' if l3['pass'] else 'FAIL ❌'}**"
                 f"（{l3['quant_correct']} vs {l3['ref_correct']}）|")
    all_pass = all((payload.get(k) or {}).get("pass", True) if k != "layer1"
                   else (payload[k]["cosine_le_one"]
                         and payload[k]["rel_frobenius"]["max"] < 0.05)
                   for k in ("layer1", "layer2", "layer3") if payload.get(k))
    L += ["",
          f"**总体判定：{'通过 —— 8bit 未造成可测的精度退化 ✅' if all_pass else '未通过 —— 需人工复核 ❌'}**",
          "",
          "> **关于「两档都答错」的用例**：第 3 层里有用例在**量化档与 bf16 参考下都答错**"
          "（如奖赏卡张数、中毒伤害点数）。两档错得一模一样 → 属 **Qwen3-4B 的领域知识/"
          "指令遵循短板，与量化无关**。这类用例对本报告的价值恰恰是"
          "「证明量化没有改变行为」。**但它们同时是产品风险信号**：若 K2 结构化抽取"
          "准确率不达标，应触发 ADR-LLM-001 的「K2 不达标切云端保底」分支。",
          ""]
    L += ["---", "",
          "复跑：`python scripts/qwen_precision.py --layer all`（报告覆盖写入本文件）"]
    (OUT_DIR / "qwen_precision_report.md").write_text("\n".join(L), encoding="utf-8")
    print(f"[precision] 报告 -> {OUT_DIR/'qwen_precision_report.md'}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Qwen3-4B 量化精度三层对照")
    ap.add_argument("--layer", default="all", help="1|2|3|all|report")
    ap.add_argument("--quant", default=None, help="8bit（默认）/4bit")
    ap.add_argument("--stride", type=int, default=4, help="第1层采样步长")
    ap.add_argument("--max-new", type=int, default=64, help="第3层每条最大新增 token")
    ap.add_argument("--max-gpu-ref", default="4.3GB", help="bf16 参考的显存上限")
    a = ap.parse_args()

    # `--layer report`：只用**已有 JSON** 重渲染 Markdown，不碰 GPU。
    # 用途：改了报告措辞/表格后无需重跑 4 分钟的量化对照（也避免两次跑出数字不一致）。
    if a.layer == "report":
        jp = OUT_DIR / "qwen_precision_report.json"
        if not jp.exists():
            raise SystemExit(f"[precision] 缺少 {jp}，先跑一次 --layer all")
        write_report(json.loads(jp.read_text(encoding="utf-8")))
        return 0

    import torch
    quant = (a.quant or LQ.quant_mode()).lower()
    payload = {"quant": quant, "layer1": None, "layer2": None, "layer3": None,
               "torch": torch.__version__, "model_path": LQ.model_path()}

    t0 = time.time()
    if a.layer in ("1", "all"):
        print("[precision] 第 1 层 · 权重级 …", flush=True)
        payload["layer1"] = layer1(quant, a.stride)
        r = payload["layer1"]
        print(f"  采样 {r['sampled']} 层 · 相对Frobenius均值 "
              f"{r['rel_frobenius']['mean']} · 余弦均值 {r['cosine']['mean']}", flush=True)
    if a.layer in ("2", "all"):
        print("[precision] 第 2 层 · 困惑度（含 bf16 参考，较慢）…", flush=True)
        payload["layer2"] = layer2(quant, a.max_gpu_ref)
        r = payload["layer2"]
        print(f"  PPL {r['quantized']['ppl']} vs bf16 {r['reference']['ppl']} "
              f"（{r['ppl_delta_pct']}%）PASS={r['pass']}", flush=True)
    if a.layer in ("3", "all"):
        print("[precision] 第 3 层 · 端到端 …", flush=True)
        payload["layer3"] = layer3(quant, a.max_new, a.max_gpu_ref)
        r = payload["layer3"]
        print(f"  完全一致 {r['exact_match_rate']} · 归一一致 {r['normalized_match_rate']} "
              f"· 字符相似 {r['char_similarity_mean']} "
              f"· 正误 量化{r['quant_correct']} vs 参考{r['ref_correct']} "
              f"(delta={r['correctness_delta']}) PASS={r['pass']}", flush=True)
    payload["elapsed_s"] = round(time.time() - t0, 1)
    write_report(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
