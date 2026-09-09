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
    seed: Optional[int] = None
    ai_iters: Optional[int] = Field(0, ge=0, le=200,
                                    description="AI 决策 MCTS iters，0=策略网/uniform")
    player_side: Optional[int] = 0           # 0=deck_a 由人类操控


class ActionReq(BaseModel):
    action: Dict[str, Any]                   # 引擎 legal 动作 dict


class BatchJobReq(BaseModel):
    deck_a: Optional[str] = "lightning"
    deck_b: Optional[str] = "fire"
    games: int = Field(100, ge=1, le=500)    # 单任务 ≤500（PRD F6）
    seed_start: Optional[int] = 20260909
    ai_iters: Optional[int] = Field(0, ge=0, le=200)


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
