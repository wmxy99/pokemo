# -*- coding: utf-8 -*-
"""PokéLab AI · 博弈智能教学环境（benchmark + RL 课程作业环境）。

把宝可梦 TCG 对战封装成**单智能体**环境：智能体只在自己的回合决策，
对手由内置 AI 自动走子。接口对齐 gymnasium 的 `reset/step` 五元组，
但不依赖 gymnasium（零运行时依赖，仅依赖 pokelab-engine）。

动作空间 = 当前合法动作的**离散下标**；观测 = `Battle.view(side)` 玩家视角 dict。
"""
from .agents import make_agent, AGENT_NAMES
from .env import PokelabEnv, EnvConfig, run_episode

__version__ = "0.1.0"
__all__ = ["PokelabEnv", "EnvConfig", "make_agent", "AGENT_NAMES",
           "run_episode"]
