# -*- coding: utf-8 -*-
"""解析链「效果族」插件层（fx 检测器）· 2026-10-27

与 `engine/families/` 一一对应：这里负责**卡面句式 → fx 动作**，引擎层负责**执行**。

模块约定
--------
    PREFIXES = ("fx:foo:",)                  # 必填：本族产出的 tag 前缀（必须以 fx: 开头）
    def detect(text, kind, name):
        '''返回 None（不是本族的句式）或完整 spec：
           {"trigger": "on_use", "actions": [{"type": "fx", "target": None,
            "amount": None, "extra": {"tag": "fx:foo:...", "family": "foo"}}],
            "family": "foo"}
        '''

**总原则：整段认领，认不出就返回 None**
  · 一个 detector 只在自己**完全理解整段卡面文本**时返回结果；
    「只理解一半」必须返回 None（让整卡如实落未建模），绝不允许部分动作 + 静默丢弃。
  · 文本归一化统一用 `normalize()`（弯引号 → 直引号、空白折叠、小写）。
  · 各族匹配模式必须**互斥**（同一个文本最多被一族认领）。

纪律（与项目 K2 一致）
----------------------
1. 判不出返回 None，绝不兜底成某一方；
2. 子集限定/条件不可表达时不映射；
3. 目标/类别/阵营判定只看**本族匹配到的子句**，不看全文；
4. 每个族必须带负向守卫用例：近邻写法必须仍返回 None。
"""
from __future__ import annotations

import importlib
import pkgutil
import re
from typing import Optional

_MODULES: Optional[list] = None

_WS_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """卡面文本归一化（匹配用）：弯引号/破折号 → ASCII，空白折叠，转小写"""
    t = str(text or "")
    t = (t.replace("\u2019", "'").replace("\u2018", "'")
          .replace("\u201c", '"').replace("\u201d", '"')
          .replace("\u2013", "-").replace("\u2014", "-"))
    t = _WS_RE.sub(" ", t)
    return t.strip().lower()


def modules() -> list:
    global _MODULES
    if _MODULES is None:
        found = []
        for mi in pkgutil.iter_modules(__path__, __name__ + "."):
            short = mi.name.rsplit(".", 1)[-1]
            if not short.startswith("fx_"):
                continue
            try:
                found.append(importlib.import_module(mi.name))
            except Exception as exc:
                raise RuntimeError(f"fx 检测器加载失败：{mi.name}：{exc}") from exc
        _MODULES = found
    return _MODULES


def reload() -> None:
    global _MODULES
    _MODULES = None


def detect_all(text: str, kind: str = "attack", name: str = ""):
    """按族顺序尝试认领整段文本；返回 spec dict 或 None

    ⚠ 各族必须互斥：一旦某族返回非 None，其它族不再尝试（首个命中生效）。
    """
    norm = normalize(text)
    if not norm:
        return None
    for m in modules():
        fn = getattr(m, "detect", None)
        if fn is None:
            continue
        try:
            hit = fn(norm, kind, name)
        except Exception as exc:
            raise RuntimeError(f"fx 检测器 {m.__name__} 抛错：{exc}") from exc
        if hit is None:
            continue
        if not isinstance(hit, dict) or not hit.get("actions"):
            raise ValueError(f"fx 检测器 {m.__name__} 返回格式非法：{hit!r}")
        return hit
    return None


def covers(text: str, kind: str = "attack", name: str = "") -> bool:
    return detect_all(text, kind, name) is not None
