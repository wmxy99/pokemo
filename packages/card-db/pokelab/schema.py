# -*- coding: utf-8 -*-
"""PokéLab Data · Pydantic Schema（对齐数据库设计 v1.1：cards / rule_chunks）

效果结构化（effects_json）分层设计：
  list[CardEffect]；每条 = trigger(触发点) + conditions(前置条件) + actions(效果动作)
  + source_text(溯源)。宽松字段 + 基础类型校验；引擎级合法校验在解析流水线后置层。
"""
from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field, field_validator


class CardCondition(BaseModel):
    """条件：如 energy_attached>=2 / target=="active" / coin=="heads" 等"""
    field: str = Field(..., description="条件字段，如 hp / energy / pokemon_type / turn")
    op: str = Field("eq", description="eq/ne/gt/gte/lt/lte/in/contains")
    value: object = None


class CardAction(BaseModel):
    """动作：draw/attach/damage/heal/discard/switch/search/evolve/knockout/..."""
    type: str = Field(..., description="动作类型")
    amount: Optional[object] = None          # 数值或表达式
    target: Optional[str] = "active_opponent"  # 目标域
    extra: dict = Field(default_factory=dict)  # 动作专属参数


class CardEffect(BaseModel):
    """单条卡牌效果（招式/特性/道具/支援者/规则文本的结构化表达）"""
    kind: str = Field("effect", description="attack / ability / item / supporter / energy / rule")
    name: Optional[str] = None
    trigger: str = Field("on_use", description="触发点，如 on_use / on_ability / on_knockout / static")
    conditions: list[CardCondition] = Field(default_factory=list)
    actions: list[CardAction] = Field(default_factory=list)
    source_text: str = Field(..., description="对应的原始文本片段（自述转写或原文，溯源用）")
    citations: list[str] = Field(default_factory=list, description="引用的规则条款 source_ref")

    @field_validator("actions")
    @classmethod
    def _require_action_or_static(cls, v, info):
        # 允许无动作的 static 效果（规则性文本），其余至少一个动作
        if not v and info.data.get("trigger", "") != "static":
            raise ValueError("非 static 效果至少需要一个 action")
        return v


class CardRecord(BaseModel):
    """入库卡记录（loader 清洗产物）——字段对齐 DB v1.1 cards 表"""
    name: str
    series: str
    number: str
    supertype: Optional[str] = None
    types: Optional[str] = None                 # JSON 数组文本
    hp: Optional[int] = None
    rules_text: Optional[str] = None            # 原始效果文本（双轨之一）
    attacks_json: Optional[str] = None          # 招式元信息（渲染用）
    source: str = "unknown"


class RuleChunk(BaseModel):
    """规则知识块——字段对齐 DB v1.1 rule_chunks"""
    card_id: Optional[int] = None
    source_ref: str = Field(..., description="出处，如 rulebook:Turn_Structure / compendium:id")
    chunk_text: str
    status: str = "active"
