# -*- coding: utf-8 -*-
"""PokéLab Agent（决策链路 v0.1）

模块：
  state_encoder  观测(view) → 定长状态向量 + 动作特征（仅公开信息）
  policy_net     PyTorch 小策略-价值网络（候选打分 + 价值头）
  mcts           引擎内蒙特卡洛树搜索（PUCT + 真实 rollout）
  pipeline       决策流水线（纯编排 decide / LangGraph build_langgraph_pipeline）
  prompts        提示词模板库（效果解析 / 解说，prompts/*.md）
  llmclient      LLM 客户端（本地/云端统一入口，零三方依赖）
  effects        K2 卡牌效果解析（规则基线 / LLM 后端）
  commentary     对战解说生成（llmclient 之上）
  privacy        数据隐私保护（脱敏 / 本地模式 / 调用审计，合规 6.2）
"""
__version__ = "0.1.0"

# ---- 轻量模块：顶层导入（零三方依赖，或仅标准库）----
from . import state_encoder
from . import mcts
from . import pipeline
from . import prompts
from . import reward
from . import distill
from . import effects
from . import privacy

# ---- 重量模块：顶层 `import torch` → 惰性导入（PEP 562）----
# 为什么必须惰性：
#   apps/api 的启动路径会经由 `llm_admin.install_provider()` → `from agent import llmclient`
#   触发本包的 __init__。若在此顶层拉 torch，则「只装了 apps/api/requirements.txt 的部署
#   （含 CI 的 api job）」会直接 ModuleNotFoundError —— 而 API 侧只用到
#   llmclient / effects / privacy / mcts，**不该为 torch 买单**。
# 惰性后 `from agent import policy_net` 与 `agent.policy_net` 仍照常工作：
# 首次访问时才真正导入，并把结果缓存进 globals()（后续访问不再走 __getattr__）。
_LAZY_TORCH_MODULES = ("policy_net", "policy_agent", "trainer", "bc", "dqn")


def __getattr__(name):
    if name in _LAZY_TORCH_MODULES:
        import importlib
        mod = importlib.import_module("." + name, __name__)
        globals()[name] = mod
        return mod
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | set(_LAZY_TORCH_MODULES))


__all__ = ["state_encoder", "policy_net", "mcts", "pipeline", "prompts",
           "policy_agent", "trainer", "bc", "reward", "dqn", "distill",
           "effects", "privacy"]
