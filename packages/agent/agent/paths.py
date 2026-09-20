# -*- coding: utf-8 -*-
"""PokéLab Agent · 产物路径规范化

训练 / 验收脚本会把**权重、价值头标定、曲线**等路径写进 `artifacts/` 下的报告与 JSON。
若直接落调用方传入的绝对路径（如 `D:/xxx/pokelab-ai/packages/...`），产物就会：
  ① **绑定某台机器** —— 别人 clone 后按产物里的路径找不到文件，证据不可复现；
  ② **泄露本机目录结构** —— 这类产物是要入库/公开的。

因此统一规范化为**相对仓库根**的 POSIX 路径，例如：
    D:/xxx/pokelab-ai/packages/agent/artifacts/k1_rich.pt
        → packages/agent/artifacts/k1_rich.pt
相对路径本身就照原样保留（`artifacts/k1_rich.pt` → `artifacts/k1_rich.pt`）；
若目标不在仓库内（例如临时目录里的权重），则退化为 POSIX 形式的原路径。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Union


def repo_root() -> Path:
    """仓库根目录（本文件位于 ``<repo>/packages/agent/agent/paths.py``）"""
    return Path(__file__).resolve().parents[3]


def portable_path(p: Union[str, Path],
                  base: Optional[Union[str, Path]] = None) -> str:
    """把路径规范化为可移植形式（相对仓库根的 POSIX 路径）

    base 可显式指定基准目录（默认仓库根）；不在基准目录内时原样返回 POSIX 形式。
    """
    path = Path(p)
    root = Path(base) if base is not None else repo_root()
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        # 不在基准目录内（如临时文件）或路径不可解析 → 保底给 POSIX 形式
        return path.as_posix()
