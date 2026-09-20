# -*- coding: utf-8 -*-
"""内置对手：对 engine.agents 的薄封装，便于按名字取用。"""
from engine.agents import Agent, BaselineAI, RandomAgent, SupportAI

_AGENTS = {
    "baseline": BaselineAI,
    "random": RandomAgent,
    "support": SupportAI,
}

AGENT_NAMES = tuple(_AGENTS)


def make_agent(name: str) -> Agent:
    """按名字构造对手。

    - `baseline`：规则基线 AI（K1 参照对手，白盒启发式）
    - `random`：均匀随机
    - `support`：会用训练家与特性的基线（v1.3）
    """
    cls = _AGENTS.get(name)
    if cls is None:
        raise KeyError(f"未知对手 {name!r}（可用：{'/'.join(_AGENTS)}）")
    if cls is RandomAgent:
        return cls(name=name)
    return cls(name=name)
