# -*- coding: utf-8 -*-
"""PokéLab Agent · 决策流水线（状态编码 → 候选生成 → MCTS 评估 → 最优执行）

双形态编排：
  1) `decide()` —— 纯函数编排（零依赖，本机/单测/演示通用）；
  2) `build_langgraph_pipeline()` —— 同一组节点以 LangGraph StateGraph 组装
     （对应技术方案模块 2「LangGraph 编排多步决策流程」，P2 部署形态）。

节点（两种形态共用同一函数，保证行为一致）：
  encode   : view → state_vec（状态编码）
  generate : legal_actions → candidates（候选动作）
  evaluate : MCTS 评估（可选 policy 先验）→ 动作价值分布
  select   : 最优动作 + 审计 stats
"""
from __future__ import annotations
from typing import Optional

from . import state_encoder as enc
from . import mcts as mcts_mod


# ---------------- 节点函数（共享） ----------------
def node_encode(ctx: dict) -> dict:
    ctx["state_vec"] = enc.encode_state(ctx["view"])
    return ctx


def node_generate(ctx: dict) -> dict:
    ctx["candidates"] = enc.legal_actions_from_view(ctx["view"])
    return ctx


def node_evaluate(ctx: dict) -> dict:
    action, stats = mcts_mod.mcts_with_stats(
        ctx["battle"], iters=ctx.get("iters", 40),
        policy=ctx.get("policy"), seed=ctx.get("seed"))
    ctx["action"] = action
    ctx["stats"] = stats
    return ctx


def node_select(ctx: dict) -> dict:
    # 演示节点：从 stats 提取 top3（决策已定，此节点做审计快照）
    cands = ctx.get("stats", {}).get("candidates", [])
    ctx["audit"] = sorted(cands, key=lambda c: -c["prior"])[:3]
    return ctx


# ---------------- 纯函数编排 ----------------
def decide(battle, policy=None, iters: int = 40, seed: Optional[int] = None,
           rollout_policy=None) -> dict:
    """单步决策（纯编排，无 LangGraph 依赖）。返回 {action, state_vec, audit, stats}"""
    ctx = {"view": battle.view(), "battle": battle, "policy": policy,
           "iters": iters, "seed": seed}
    ctx = node_encode(ctx)
    ctx = node_generate(ctx)
    ctx = node_evaluate(ctx)
    ctx = node_select(ctx)
    return {"action": ctx["action"], "state_vec": ctx["state_vec"],
            "audit": ctx["audit"], "stats": ctx["stats"]}


# ---------------- LangGraph 编排 ----------------
def build_langgraph_pipeline(iters: int = 40, seed: Optional[int] = None):
    """组装 LangGraph StateGraph（需已安装 langgraph；未装时抛带指引的 ImportError）"""
    try:
        from langgraph.graph import StateGraph, END
    except ImportError as e:  # pragma: no cover
        raise ImportError(
            "未安装 langgraph。本地/测试可用 decide()（纯编排）代替；"
            "如需 LangGraph 形态：pip install langgraph") from e

    from typing import TypedDict

    class DecCtx(TypedDict, total=False):
        view: dict
        battle: object
        policy: object
        iters: int
        seed: Optional[int]
        state_vec: list
        candidates: list
        action: dict
        stats: dict
        audit: list

    g = StateGraph(DecCtx)
    g.add_node("encode", lambda c: node_encode(dict(c)))
    g.add_node("generate", lambda c: node_generate(dict(c)))
    g.add_node("evaluate", lambda c: node_evaluate(dict(c)))
    g.add_node("select", lambda c: node_select(dict(c)))
    g.set_entry_point("encode")
    g.add_edge("encode", "generate")
    g.add_edge("generate", "evaluate")
    g.add_edge("evaluate", "select")
    g.add_edge("select", END)
    app = g.compile()
    return app


def decide_langgraph(battle, policy=None, iters: int = 40,
                     seed: Optional[int] = None) -> dict:
    """LangGraph 形态的单步决策"""
    app = build_langgraph_pipeline(iters=iters, seed=seed)
    out = app.invoke({"view": battle.view(), "battle": battle,
                      "policy": policy, "iters": iters, "seed": seed})
    return {"action": out.get("action"), "audit": out.get("audit"),
            "state_vec": out.get("state_vec")}
