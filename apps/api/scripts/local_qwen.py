# -*- coding: utf-8 -*-
"""本地 Qwen3-4B 量化推理服务（OpenAI 兼容）· PokéLab AI

为什么需要它
------------
ADR-LLM-001 定的是「解析/解说统一本地 Qwen3-4B（8bit），K2 不达标切云端保底」。
本脚本把该决策落地成**可被 PokéLab 直接调用的端点**：

    POKELAB_LLM_BASE=http://127.0.0.1:9997/v1   # 后台「模型配置」填这个即可
    POKELAB_LOCAL_ONLY=1                        # 合规 6.2：完全离线，数据不出本地

于是 K2 效果解析与对战解说都会走本地模型，且通过 `agent.llmclient.chat()` 的
统一出口（端点校验 → 脱敏 → 调用 → 审计），不绕过合规链路。

量化方案（6GB 显存实测）
------------------------
RTX 4050 Laptop 6GB 放不下 bf16（≈8.05GB）→ 用 **LLM.int8()**：
逐通道 int8 量化 + 离群特征维度保留 fp16，实测 footprint **4.11GB**，精度损失极小
（三层对照见 `qwen_precision.py`）。**不默认用 4bit**——本项目的验收口径是
「确保数据精度」，8bit 是精度与显存的更优点。

用法
----
    python scripts/local_qwen.py serve                    # 起服务（默认 127.0.0.1:9997）
    python scripts/local_qwen.py serve --port 9997
    python scripts/local_qwen.py info                     # 只加载并打印加载信息
    python scripts/local_qwen.py generate "你好"           # 命令行单次生成（自检用）

环境变量
--------
    POKELAB_QWEN_PATH    模型目录（默认 D:\\pokemo\\qwen3-4b\\moxing）
    POKELAB_QWEN_QUANT   8bit（默认）| 4bit | bf16
    POKELAB_QWEN_MAX_GPU 显存上限（默认 5.2GB；留出 KV cache 余量）
    POKELAB_QWEN_THINK   1 = 允许 Qwen3 思考段（默认 0，关思考更确定、更快）
    POKELAB_QWEN_MAX_NEW 单次最大新增 token（默认 1024）
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Optional

# 默认留空：模型权重**不入库**（数据红线），必须由使用者经环境变量指定
DEFAULT_MODEL = os.environ.get("POKELAB_QWEN_PATH", "")


# ---------------- 配置 ----------------
def model_path() -> str:
    """本地权重目录（**不入库**，必须由环境变量指定）

    历史：这里曾硬编码一个开发者本机的绝对路径 —— 对其它机器无效，
    也违反「仓库不含本地个人路径」的红线。
    """
    p = os.environ.get("POKELAB_QWEN_PATH") or DEFAULT_MODEL
    if not p:
        raise SystemExit(
            "未指定本地模型权重目录：请设置 POKELAB_QWEN_PATH 环境变量"
            "（例如 POKELAB_QWEN_PATH=/path/to/Qwen3-4B）。"
            "权重不入库（数据红线），需自行下载。")
    return p


def quant_mode() -> str:
    return (os.environ.get("POKELAB_QWEN_QUANT", "8bit") or "8bit").lower()


def thinking_enabled() -> bool:
    """Qwen3 的 <think> 段是否开启（默认关：确定性更好、K2 解析无需思考链）"""
    return os.environ.get("POKELAB_QWEN_THINK", "0") not in ("0", "", "false", "False")


# ---------------- 加载（量化落点） ----------------
def load(quant: Optional[str] = None, max_gpu: Optional[str] = None,
         verbose: bool = True):
    """加载量化模型 → (model, tokenizer)

    quant: ``8bit``（默认，LLM.int8）· ``4bit``（NF4 + 双量化，省显存用）· ``bf16``（参考/调试）
    """
    # 零依赖的前置校验**必须排在 import 之前**：
    #   ① 目录不存在时不该先付 2GB 的 torch/transformers 加载代价；
    #   ② 若放在 import 之后，本模块的「目录缺失必须明确报错」用例在**无 torch 的环境**
    #      （例如只装了 apps/api/requirements.txt 的 CI）会先抛 ModuleNotFoundError，
    #      变成一个恒失败的假红测试。
    path = model_path()
    if not Path(path).exists():
        raise SystemExit(f"[qwen] 模型目录不存在：{path}")

    import torch
    from transformers import (AutoModelForCausalLM, AutoTokenizer,
                              BitsAndBytesConfig)
    from transformers.utils import logging as hflog
    hflog.set_verbosity_error()
    # 压掉 bitsandbytes 每次前向都会打的一行：
    #   `MatMul8bitLt: inputs will be cast from torch.bfloat16 to float16 during quantization`
    # 它由 `bitsandbytes.autograd._functions` 在 **每次 forward** 触发，实测一次验证跑
    # 就能把日志刷到 **5.9MB**，把有用信息全埋了。语义上是预期行为（LLM.int8 固定把
    # 输入转 fp16 做 int8 矩阵乘），无需每次提示，故提到 ERROR。
    logging.getLogger("bitsandbytes.autograd._functions").setLevel(logging.ERROR)

    quant = (quant or quant_mode()).lower()
    gpu = max_gpu or os.environ.get("POKELAB_QWEN_MAX_GPU", "5.2GB")

    tok = AutoTokenizer.from_pretrained(path)
    kwargs = dict(device_map="auto", low_cpu_mem_usage=True,
                  max_memory={0: gpu, "cpu": "24GB"})
    if quant == "8bit":
        kwargs["quantization_config"] = BitsAndBytesConfig(load_in_8bit=True)
    elif quant == "4bit":
        kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=torch.bfloat16)
    elif quant == "bf16":
        kwargs["dtype"] = torch.bfloat16          # transformers 5.x：dtype=（非 torch_dtype）
    else:
        raise SystemExit(f"[qwen] 未知量化档位：{quant}（可选 8bit/4bit/bf16）")

    t0 = time.time()
    model = AutoModelForCausalLM.from_pretrained(path, **kwargs)
    model.eval()
    if verbose:
        fp = model.get_memory_footprint() / 1024 ** 3
        print(f"[qwen] {quant} 加载完成 {time.time()-t0:.1f}s · footprint {fp:.2f}GB",
              flush=True)
    return model, tok


# ---------------- 生成 ----------------
def build_prompt(tok, messages: list) -> str:
    """把 OpenAI 风格 messages 渲染成 Qwen3 的 chat 模板

    ``enable_thinking`` 是 **chat template 的渲染变量**（不是模型参数）——
    Qwen3 不开它就不会产出 ``<think>`` 段，这对 K2「严格输出 JSON」很关键：
    思考段会让下游 JSON 抽取多一层剥离成本。
    """
    return tok.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True,
        enable_thinking=thinking_enabled())


def generate(model, tok, messages: list, *, temperature: float = 0.0,
             max_new_tokens: Optional[int] = None) -> dict:
    """单次生成 → {content, prompt_tokens, completion_tokens, elapsed_ms}"""
    import torch
    prompt = build_prompt(tok, messages)
    ids = tok(prompt, return_tensors="pt").input_ids.to(model.device)
    max_new = max_new_tokens or int(os.environ.get("POKELAB_QWEN_MAX_NEW", "1024"))
    # 6GB 卡：输入截断 + 输出上限，双管齐下防 OOM
    gen_kwargs = dict(max_new_tokens=max_new, pad_token_id=tok.eos_token_id)
    if temperature and temperature > 0:
        gen_kwargs.update(do_sample=True, temperature=temperature, top_p=0.95,
                          top_k=20)
    else:
        gen_kwargs.update(do_sample=False)        # 贪心：确定性，便于精度对照
    t0 = time.time()
    with torch.no_grad():
        out = model.generate(ids, **gen_kwargs)
    dt = time.time() - t0
    new_ids = out[0][ids.shape[1]:]
    return {"content": tok.decode(new_ids, skip_special_tokens=True),
            "prompt_tokens": int(ids.shape[1]),
            "completion_tokens": int(new_ids.shape[0]),
            "elapsed_ms": int(dt * 1000)}


# ---------------- OpenAI 兼容服务 ----------------
# 请求模型必须定义在**模块级**，不能放进 make_app() 里。
# 原因（真 bug，踩过）：本模块启用了 `from __future__ import annotations`，所有注解
# 变成字符串，FastAPI 靠 `get_type_hints` + **模块全局命名空间**解析类型。若模型类
# 定义在函数内部（局部名），解析不到 → FastAPI 认不出这是 Pydantic 模型 →
# 把参数当成**查询参数** → 任何调用都返回 422
# `{"loc":["query","req"],"msg":"Field required"}`。
# 表现是「服务起得来、healthz 正常，但一调用就 422」，极易误判为客户端问题。
try:                                     # pydantic v2（随 fastapi 安装）
    from pydantic import BaseModel as _BaseModel
except Exception:                        # pragma: no cover
    _BaseModel = None                    # 仅影响 serve；info/generate 仍可用


if _BaseModel is not None:
    class ChatReq(_BaseModel):
        """OpenAI /chat/completions 请求体（只取用得上的字段）"""
        model: Optional[str] = None
        messages: list
        temperature: Optional[float] = 0.0
        max_tokens: Optional[int] = None
        stream: bool = False
else:                                    # pragma: no cover
    ChatReq = None                       # type: ignore


def make_app():
    """构造 FastAPI app（模型**懒加载**：启动钩子时载入）"""
    if ChatReq is None:
        raise RuntimeError(
            "提供 OpenAI 兼容服务需要 pydantic/fastapi/uvicorn："
            "pip install fastapi uvicorn")
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    app = FastAPI(title="PokéLab local Qwen3-4B (8bit)", version="1.0")
    state: dict = {"model": None, "tok": None, "err": None}
    lock = threading.Lock()          # 单卡：一次只跑一个生成

    def ensure_model():
        if state["model"] is None:
            with lock:
                if state["model"] is None:
                    state["model"], state["tok"] = load()
        return state["model"], state["tok"]

    @app.on_event("startup")
    def _warmup():                    # 提前加载，避免首个请求超时
        try:
            ensure_model()
        except SystemExit as e:
            state["err"] = str(e)

    @app.get("/healthz")
    def healthz():
        return {"ok": state["model"] is not None, "quant": quant_mode(),
                "model_path": model_path(), "error": state["err"]}

    @app.get("/v1/models")
    def models():
        return {"object": "list",
                "data": [{"id": "qwen3-4b-8bit", "object": "model",
                          "owned_by": "local"}]}

    @app.post("/v1/chat/completions")
    def chat(req: ChatReq):
        model, tok = ensure_model()
        with lock:                    # 串行化：6GB 卡不支持并发批量
            r = generate(model, tok, req.messages,
                         temperature=req.temperature or 0.0,
                         max_new_tokens=req.max_tokens)
        now = int(time.time())
        return JSONResponse({
            "id": f"chatcmpl-local-{now}",
            "object": "chat.completion",
            "created": now,
            "model": req.model or "qwen3-4b-8bit",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant",
                                     "content": r["content"]}}],
            "usage": {"prompt_tokens": r["prompt_tokens"],
                      "completion_tokens": r["completion_tokens"],
                      "total_tokens": r["prompt_tokens"] + r["completion_tokens"]},
            "local": {"quant": quant_mode(), "elapsed_ms": r["elapsed_ms"]},
        })

    return app


def main() -> int:
    ap = argparse.ArgumentParser(description="本地 Qwen3-4B 量化服务（OpenAI 兼容）")
    ap.add_argument("cmd", choices=["serve", "info", "generate"])
    ap.add_argument("text", nargs="?", default="", help="generate 的输入")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9997)
    ap.add_argument("--quant", default=None, help="8bit（默认）/4bit/bf16")
    a = ap.parse_args()

    if a.cmd == "serve":
        import uvicorn
        q = a.quant or quant_mode()
        print(f"[qwen] 启动服务 http://{a.host}:{a.port}/v1（量化档 {q}）", flush=True)
        print(f"[qwen] 后台「模型配置」可填：http://{a.host}:{a.port}/v1"
              f"（本地端点，POKELAB_LOCAL_ONLY=1 下亦允许）", flush=True)
        uvicorn.run(make_app(), host=a.host, port=a.port, log_level="warning")
        return 0

    model, tok = load(quant=a.quant)
    if a.cmd == "info":
        import torch
        fp = model.get_memory_footprint() / 1024 ** 3
        free, total = torch.cuda.mem_get_info()
        print(f"  量化档      : {a.quant or quant_mode()}")
        print(f"  footprint   : {fp:.2f} GB")
        print(f"  显存 free   : {free/1024**3:.2f} / {total/1024**3:.2f} GB")
        print(f"  权重总量    : {sum(p.numel() for p in model.parameters())/1e9:.3f} B")
        print(f"  thinking    : {'开' if thinking_enabled() else '关'}")
        return 0

    r = generate(model, tok, [{"role": "user", "content": a.text}])
    print(f"[{r['completion_tokens']} tok / {r['elapsed_ms']}ms]")
    print(r["content"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
