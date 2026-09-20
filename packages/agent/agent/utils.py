# -*- coding: utf-8 -*-
"""PokéLab Agent · 设备解析（GPU 优先）

--device 默认 "auto"：有可用 CUDA 且显存余量 ≥ min_free_gb 时用 GPU，
否则回退 CPU 并打印原因。小策略网络显存占用 <200MB，6GB 卡可安心并行。
"""
from __future__ import annotations


def resolve_device(name: str = "auto", min_free_gb: float = 1.2,
                   verbose: bool = True) -> str:
    if name != "auto":
        return name
    try:
        import torch
    except Exception:  # pragma: no cover
        return "cpu"
    if not torch.cuda.is_available():
        if verbose:
            print("[device] auto → cpu（CUDA 不可用）")
        return "cpu"
    free_gb = torch.cuda.mem_get_info()[0] / (1024 ** 3)
    if free_gb >= min_free_gb:
        if verbose:
            print(f"[device] auto → cuda（{torch.cuda.get_device_name(0)}，"
                  f"可用 {free_gb:.1f}GB）")
        return "cuda"
    if verbose:
        print(f"[device] auto → cpu（显存余量 {free_gb:.2f}GB < "
              f"{min_free_gb}GB，避免 OOM）")
    return "cpu"


def resolve(args) -> None:
    """就地为 argparse Namespace 解析 args.device（auto）"""
    args.device = resolve_device(args.device)
