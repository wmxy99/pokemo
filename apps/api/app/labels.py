# -*- coding: utf-8 -*-
"""PokéLab AI API · 展示层本地化（i18n）

原则：**英文名是数据键**（Limitless 文本匹配、文件/库字段、URL 参数都用英文），
对外展示（报告、矩阵、响应体里的展示字段）一律优先中文。

- `deck_label_zh(name)`：卡组名中文化（已知预设 A1–A4 / 演示卡组 → 中文；
  用户自建卡组名保持原样，`名字[deck_xxx]` 保留 id 便于区分）。
- `card_display(name)`：卡名中文化（走引擎真实卡池；未收录则回退英文）。
"""
from __future__ import annotations

KNOWN_DECKS = frozenset({
    "lightning", "grass", "fire", "psychic", "status", "draco", "ability",
    "A1", "A2", "A3", "A4",
})


def _decklib():
    import sys
    from pathlib import Path
    repo = Path(__file__).resolve().parents[3]
    pkg = repo / "packages" / "engine"
    if pkg.exists() and str(pkg) not in sys.path:
        sys.path.insert(0, str(pkg))
    from engine import decklib
    return decklib


def deck_label_zh(name: str) -> str:
    """卡组名中文化（无法识别 → 原样返回，保证用户命名不被改写）"""
    if not name:
        return name
    raw = str(name)
    base = raw.split("[")[0].strip()
    key = base if base in KNOWN_DECKS else base.upper()
    if key not in KNOWN_DECKS:
        return raw
    try:
        zh = _decklib().deck_label_zh(key)
        suffix = raw[len(base):]                     # 保留 [deck_xxx] 等后缀
        return zh + suffix
    except Exception:                                # noqa: BLE001
        return raw


def card_display(name: str) -> str:
    """卡名中文化（未收录 → 原样）"""
    try:
        import sys
        from pathlib import Path
        repo = Path(__file__).resolve().parents[3]
        pkg = repo / "packages" / "engine"
        if pkg.exists() and str(pkg) not in sys.path:
            sys.path.insert(0, str(pkg))
        from engine import reallib
        return reallib.display_name(name)
    except Exception:                                # noqa: BLE001
        return name
