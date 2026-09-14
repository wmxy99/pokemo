# -*- coding: utf-8 -*-
"""PokéLab AI API · Pydantic 请求/响应模型（PRD §5.2 对齐）"""
from __future__ import annotations
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class RegisterReq(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=4, max_length=64)


class LoginReq(RegisterReq):
    pass


class DeckImportReq(BaseModel):
    """卡组文本（Limitless 兼容，F3）"""
    text: str = Field(min_length=8, max_length=4000)


class BattleCreateReq(BaseModel):
    deck_a: Optional[str] = "lightning"      # 我方卡组（name / 文本）
    deck_b: Optional[str] = "fire"           # AI 卡组
    deck_a_id: Optional[str] = None          # F3 已导入卡组 id（优先于 deck_a）
    deck_b_id: Optional[str] = None          # 对手也可用导入卡组
    seed: Optional[int] = None
    ai_iters: Optional[int] = Field(0, ge=0, le=200,
                                    description="AI 决策 MCTS iters，0=策略网/uniform")
    player_side: Optional[int] = 0           # 0=deck_a 由人类操控


class ActionReq(BaseModel):
    """动作请求：`action` / `command` / `commands` 三选一

    - `action`：与 `GET /battles/{id}` 的 `view.legal` 严格 1:1（PRD F5）；
    - `command`：中文自然指令，如 `"幻影俯冲"` / `"高级球"` / `"附能量到后备1"` /
      `"结束回合"`；**可用「然后/接着/再/;」组合多步**，引擎逐句拆分执行；
    - `commands`：显式的步骤数组（等价于用分隔词串联的 `command`）；
    - `verb`：可选，限定动作类型（attack/trainer/attach/…）。
    """

    action: Optional[Dict[str, Any]] = None
    command: Optional[str] = None            # 自由文本（中文别名优先，支持组合句）
    commands: Optional[List[str]] = None     # 显式步骤数组（组合指令）
    verb: Optional[str] = None               # 可选：限定动作类型


class BatchJobReq(BaseModel):
    deck_a: Optional[str] = "lightning"
    deck_b: Optional[str] = "fire"
    deck_a_id: Optional[str] = None          # 已导入卡组（优先）
    deck_b_id: Optional[str] = None
    games: int = Field(100, ge=1, le=500)    # 单任务 ≤500（PRD F6）
    seed_start: Optional[int] = 20260909
    ai_iters: Optional[int] = Field(0, ge=0, le=200)


class PoolJobReq(BaseModel):
    """卡组池批量任务（F7 胜率矩阵）：decks_a × decks_b 全对位各 games_each 局"""
    decks_a: List[str] = Field(min_length=1, max_length=6)
    decks_b: List[str] = Field(min_length=1, max_length=6)
    games_each: int = Field(60, ge=1, le=500)
    seed_start: Optional[int] = 20260910


class BatchJobOut(BaseModel):
    id: str
    status: str
    progress: Optional[int] = None
    result: Optional[Dict[str, Any]] = None


class TrainJobReq(BaseModel):
    stage1_episodes: int = Field(2000, ge=0)
    stage2_episodes: int = Field(0, ge=0)
    stage3_episodes: int = Field(0, ge=0)
    bc_episodes: Optional[int] = 40
    lr: Optional[float] = 3e-3
    features: Optional[str] = "legacy"
    note: Optional[str] = None


class TrainOut(BaseModel):
    id: str
    status: str
    progress: Optional[Dict[str, Any]] = None
    log_tail: Optional[List[str]] = None


class CommentaryReq(BaseModel):
    """对战解说请求（F5 内 · 纯展示可降级）

    - ``backend``：``auto``（有配置走云端、失败回退离线，默认）· ``llm``（强制
      云端）· ``rule``（强制离线模板，便于演示与本地模式）；
    - ``n_events``：取最近多少条事件作为上下文（默认 8）。
    """

    backend: str = Field("auto", pattern="^(auto|llm|rule)$")
    n_events: int = Field(8, ge=1, le=40)
    opponent_hint: str = ""
