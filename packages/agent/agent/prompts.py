# -*- coding: utf-8 -*-
"""PokéLab Agent · 提示词模板库（模块 3 用：卡牌效果解析 / 对战解说）

模板存放于 packages/agent/prompts/*.md，变量用 @@name@@ 占位（避免与 JSON 花括号冲突）。
用法：render("parse_effects_user", card_name="…", rules_text="…")
"""
from __future__ import annotations
from pathlib import Path

_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"
_CACHE: dict = {}


def load(name: str) -> str:
    if name not in _CACHE:
        path = _PROMPT_DIR / f"{name}.md"
        _CACHE[name] = path.read_text(encoding="utf-8")
    return _CACHE[name]


def render(name: str, **kwargs) -> str:
    """读取模板并按 @@key@@ 替换；缺失变量保留原样（便于人工核查）"""
    text = load(name)
    for k, v in kwargs.items():
        text = text.replace(f"@@{k}@@", "" if v is None else str(v))
    return text


def templates() -> dict:
    return {p.stem: p for p in sorted(_PROMPT_DIR.glob("*.md"))}
