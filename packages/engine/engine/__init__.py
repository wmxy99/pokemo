# -*- coding: utf-8 -*-
"""PokéLab Engine"""
__version__ = "0.1.0"
# 顶层再导出 = 对外公开面。刻意把「构造卡/牌组所需的定义类」一并列出
# （下游要自建卡组/环境时会直接用到，见 packages/gym），而不是作为意外泄漏的名字挂着。
__all__ = ["Battle", "GameConfig", "Agent", "RandomAgent", "BaselineAI",
           "HumanAgent", "run_match", "run_many", "summarize", "decklib",
           "commands", "find_action", "command_catalog", "command_label",
           "split_sequence", "sequence_hint", "MAX_SEQUENCE_STEPS",
           # 卡与状态定义（公开面）
           "PokemonDef", "AttackDef", "AbilityDef", "TrainerDef", "EnergyDef",
           "PokeRef", "EnergyRef", "TrainerRef"]

from .cards import (PokemonDef, AttackDef, AbilityDef, TrainerDef, EnergyDef,
                    PokeRef, EnergyRef, TrainerRef)
from .state import GameConfig
from .battle import Battle
from .agents import Agent, RandomAgent, BaselineAI, HumanAgent
from .simulator import run_match, run_many, summarize
from . import decklib
from . import commands
from .commands import (find_action, command_catalog, command_label,
                       split_sequence, sequence_hint, MAX_SEQUENCE_STEPS)
