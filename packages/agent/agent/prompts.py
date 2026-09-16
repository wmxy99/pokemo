# -*- coding: utf-8 -*-
"""PokéLab Agent · 提示词模板库（模块 3 用：卡牌效果解析 / 对战解说）

模板存放于 packages/agent/prompts/*.md，变量用 @@name@@ 占位（避免与 JSON 花括号冲突）。
用法：render("parse_effects_user", card_kind="attack", rules_text="…")

**模板里的每个 @@占位符，调用方都必须提供**（K2 路径用 `strict=True` 强制校验）。
历史上 `parse_effects_user.md` 声明了 `series`/`number`/`supertype`/`hp` 等调用方
并不提供的键，导致这些占位符**原样发给了模型**、且不报错。
"""
from __future__ import annotations
import re
from pathlib import Path

_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"
_CACHE: dict = {}
_TOKEN_RE = re.compile(r"@@\w+@@")


class PromptRenderError(RuntimeError):
    """模板渲染后仍有未替换的 @@占位符@@（strict 模式）"""


def leftover_tokens(text: str) -> list:
    """渲染后残留的占位符（去重、保序）"""
    return list(dict.fromkeys(_TOKEN_RE.findall(text)))


def load(name: str) -> str:
    if name not in _CACHE:
        path = _PROMPT_DIR / f"{name}.md"
        _CACHE[name] = path.read_text(encoding="utf-8")
    return _CACHE[name]


def render(name: str, strict: bool = False, **kwargs) -> str:
    """读取模板并按 @@key@@ 替换；缺失变量保留原样（便于人工核查）

    ``strict=True``：渲染后**若仍有残留占位符就抛 `PromptRenderError`**。

    为什么需要 strict：默认「保留原样」是为人工核查方便，但**渲染结果是要直接发给
    模型的**，残留的 `@@supertype@@` 就成了提示词里的乱码，而且**不报错**。
    本项目真的踩过：`parse_llm` 传的键是 `card_kind`，而 `parse_effects_user.md`
    用的是 `@@supertype@@` → 模型**从未收到过卡牌类别**（4 条 kind 失败全由此而来），
    且因不报错而长期未被发现。K2 调用路径一律使用 strict。
    """
    text = load(name)
    for k, v in kwargs.items():
        text = text.replace(f"@@{k}@@", "" if v is None else str(v))
    if strict:
        left = leftover_tokens(text)
        if left:
            raise PromptRenderError(
                f"模板 {name} 渲染后仍有未替换占位符：{left}"
                f"（本次传入的键：{sorted(kwargs)}）")
    return text


def templates() -> dict:
    return {p.stem: p for p in sorted(_PROMPT_DIR.glob("*.md"))}
