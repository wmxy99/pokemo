# -*- coding: utf-8 -*-
"""PokéLab Data · 卡牌效果解析（LLM + Schema 校验）

⚠ **[已废弃 2026-09-15] 本模块的 LLM 路径（`llm_parse_text`）不再使用。**

废弃原因：它是**注入式**客户端（要求调用方先把 model/tokenizer 传进来），并内嵌了一份
与产品提示词**不同步**的 system prompt —— 那份 prompt 把枚举写成引号内的
`"target":"active_opponent|self|bench|any"`，实测会让模型把 `|` 当字面量抄下来
（`"active|bench"`）而整条 Schema 校验失败。该缺陷已在
`packages/agent/prompts/parse_effects_system.md` 修正（改为独立枚举表 + 单选硬约束），
但**本模块内的旧 prompt 未同步**——继续使用它会持续产出非法结果。

唯一调用方 `scripts/parse_effects.py` 亦已废弃（见该文件头部：状态机空转 +
写入格式与现库 `effects_json` 不兼容）。

现行入口
--------
- 解析：`packages/agent/agent/effects.py::parse_effects(text, backend="rule|llm|auto")`
  —— 经 `llmclient.chat()` 走 OpenAI 兼容端点（本地 Qwen3-4B 或云端），
  复用 `packages/agent/prompts/parse_effects_*.md` 单一提示词源；
- 评测：`packages/agent/examples/run_k2.py`。

保留本模块仅为可追溯（历史 commit / 设计文档引用），**新代码请勿依赖**。
下面两个纯函数（`extract_json` / `validate_effects`）本身无副作用、仍可复用，
但 `agent.effects` 已有更完善的同名实现（含围栏/thinking 残留容错），优先用后者。

---
原说明：解析流水线：卡牌 rules_text → LLM → 效果 JSON → pydantic 校验 →
入库 effects_json + status=active；校验失败 → 转人工（status 保持 pending）。
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
    """[已废弃 2026-09-15] 经注入的 Qwen3-4B 解析效果文本

    **勿在新代码中使用**（见模块 docstring：内嵌 prompt 与产品提示词不同步，
    且把枚举写成引号内 `a|b|c`，实测导致模型照抄字面量而校验失败）。
    改用 `agent.effects.parse_effects(text, backend="llm")`。

    保留实现以便追溯；`model/tokenizer` 为空时抛 NotImplementedError。
    """
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
