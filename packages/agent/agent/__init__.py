# -*- coding: utf-8 -*-
"""PokéLab Agent（决策链路 v0.1）

模块：
  state_encoder  观测(view) → 定长状态向量 + 动作特征（仅公开信息）
  policy_net     PyTorch 小策略-价值网络（候选打分 + 价值头）
  mcts           引擎内蒙特卡洛树搜索（PUCT + 真实 rollout）
  pipeline       决策流水线（纯编排 decide / LangGraph build_langgraph_pipeline）
  prompts        提示词模板库（效果解析 / 解说，prompts/*.md）
"""
__version__ = "0.1.0"

from . import state_encoder
from . import policy_net
from . import mcts
from . import pipeline
from . import prompts
from . import policy_agent
from . import trainer
from . import bc
from . import reward
from . import dqn

__all__ = ["state_encoder", "policy_net", "mcts", "pipeline", "prompts",
           "policy_agent", "trainer", "bc", "reward", "dqn"]
