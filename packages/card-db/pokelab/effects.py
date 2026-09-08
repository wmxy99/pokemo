# -*- coding: utf-8 -*-
"""PokéLab Data · 卡牌效果解析（LLM + Schema 校验）

解析流水线：卡牌 rules_text → LLM（本地 Qwen3-4B 8bit，ADR-LLM-001）→
效果 JSON → pydantic 校验（本模块 validate）→ 引擎 Schema 校验（P1 后置层）→
入库 effects_json + status=active；校验失败 → 转人工（status 保持 pending）。

说明：effects_json 的「引擎可执行」合法性由引擎层负责，本模块只保证结构化 Schema 正确。
"""
from __future__ import annotations
import json, re
from .schema import CardEffect

_JSON_BLOCK = re.compile(r"```(?:json)?\s*(\[.*?\]|\{.*?\})\s*```", re.S)
_JSON_RAW = re.compile(r"(\[.*\]|\{.*\})", re.S)
_THINK = re.compile(r"<think>.*?</think>", re.S)


def _strip_think(text: str) -> str:
    """剥离 Qwen3 thinking 段（enable_thinking=False 未能生效时的兜底）"""
    return _THINK.sub("", text)


def extract_json(text: str) -> str:
    """从 LLM 输出剥离 JSON（围栏优先；其次剥 think 段后按括号深度截取）"""
    text = _strip_think(text)
    m = _JSON_BLOCK.search(text)
    if m:
        return m.group(1)
    for i, ch in enumerate(text):
        if ch in "[{":
            depth, j, close = 0, i, "]" if ch == "[" else "}"
            for j in range(i, len(text)):
                if text[j] == ch:
                    depth += 1
                elif text[j] == close:
                    depth -= 1
                    if depth == 0:
                        return text[i:j + 1]
            break
    return text.strip()


def validate_effects(text: str) -> list[CardEffect]:
    """解析 + pydantic 校验；失败抛 ValueError（调用方决定转人工）"""
    payload = json.loads(extract_json(text))
    if isinstance(payload, dict):          # 容错：单条对象包装为数组
        payload = [payload]
    return [CardEffect.model_validate(e) for e in payload]


def llm_parse_text(rules_text: str, model=None, tokenizer=None) -> list[CardEffect]:
    """经本地 Qwen3-4B 解析（model/tokenizer 由调用方注入，复用 8bit 加载）；
    必须走 chat template（Qwen3 强依赖）；model 为空时抛 NotImplemented。"""
    if model is None or tokenizer is None:
        raise NotImplementedError("需注入已加载的 Qwen3-4B model/tokenizer（见 scripts/parse_effects.py）")
    sys_prompt = (
        "你是宝可梦 TCG 规则工程师。把给定卡牌效果文本转写为结构化 JSON（严格输出数组，"
        '不要多余解释）：[{"kind":"attack|ability|item|supporter|rule","name":...,'
        '"trigger":"on_use|static|on_knockout|...","conditions":[{"field":...,"op":"eq|gt|gte|lt|lte|in|contains","value":...}],'
        '"actions":[{"type":"draw|attach|damage|heal|discard|switch|search|evolve|spread|...","amount":...,"target":"active_opponent|self|bench|any","extra":{...}}],'
        '"source_text":"原始文本（自述转写，勿逐字复制官方文案）","citations":[]}]。'
        "规则性文本用 trigger=static 且 actions 可空。"
    )
    msgs = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": f"卡牌效果：\n{rules_text}"},
    ]
    # Qwen3：enable_thinking 是 chat template 渲染变量（False 时不触发 <think> 思考段）
    prompt = tokenizer.apply_chat_template(
        msgs, tokenize=False, add_generation_prompt=True, enable_thinking=False)
    ids = tokenizer(prompt, return_tensors="pt").input_ids.to(model.device)
    out = model.generate(ids, max_new_tokens=1024, do_sample=False,
                         pad_token_id=tokenizer.eos_token_id)
    raw = tokenizer.decode(out[0][ids.shape[1]:], skip_special_tokens=True)
    return validate_effects(raw)  # extract_json 内含 think 剥离兜底
