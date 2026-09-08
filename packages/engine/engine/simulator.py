# -*- coding: utf-8 -*-
"""PokéLab Engine · 对战模拟器

职责：驱动两个 Agent 完成整局（动作循环 → 事件日志 → 终局），
以及批量对局统计（胜率/平均回合/先手优势）——对应 F6 批量的本地内核。
"""
from __future__ import annotations
from typing import Optional
from .battle import Battle
from .state import GameConfig
from .agents import Agent


def run_match(deck_a: list, deck_b: list, agent_a: Agent, agent_b: Agent,
              seed: int = 0, cfg: Optional[GameConfig] = None,
              max_steps: int = 2000) -> Battle:
    """运行单局，返回已完成 Battle（结果在 .winner/.win_reason/.events）"""
    battle = Battle(deck_a, deck_b, seed=seed, cfg=cfg,
                    names=(agent_a.name, agent_b.name))
    guard = 0
    while not battle.finished and guard < max_steps:
        guard += 1
        view = battle.view()
        agent = agent_a if battle.current == 0 else agent_b
        action = agent.decide(view)
        res = battle.step(action)
        if not res["ok"]:
            # 非法动作（Agent bug 兜底）：强制过
            battle.step({"verb": "pass"})
    if not battle.finished:  # 步数保护（防御性，正常由 max_turns 判平）
        battle._finish(battle.winner, "max_turns_draw",
                       {"note": "simulator guard"})
    return battle


def summarize(battle: Battle) -> dict:
    return {
        "seed": battle.seed,
        "winner": battle.winner,          # 0/1/None(平)
        "win_reason": battle.win_reason,
        "turns": battle.turn_number,
        "names": [p.name for p in battle.players],
        "events": len(battle.events),
        "prizes_left": [p.prizes_left for p in battle.players],
        "last_action_ok": True,
    }


def run_many(deck_a: list, deck_b: list, agent_a: Agent, agent_b: Agent,
             n: int = 200, seeds: Optional[range] = None,
             cfg: Optional[GameConfig] = None) -> dict:
    """批量对局统计（对应 F6）：返回汇总 dict"""
    import statistics
    results = []
    for i in range(n):
        seed = i if seeds is None else seeds[i]
        b = run_match(deck_a, deck_b, agent_a, agent_b, seed=seed, cfg=cfg)
        results.append(b)
    wins = {0: 0, 1: 0, None: 0}
    turns = []
    first_wins = 0
    first_total = 0
    reasons = {}
    for b in results:
        wins[b.winner] += 1
        turns.append(b.turn_number)
        reasons[b.win_reason] = reasons.get(b.win_reason, 0) + 1
        first = next((e for e in b.events if e["type"] == "setup"), {}).get("first")
        if first is not None:
            first_total += 1
            if b.winner == first:
                first_wins += 1
    return {
        "n": n,
        "wins_a": wins[0],
        "wins_b": wins[1],
        "draws": wins[None],
        "win_rate_a": wins[0] / n,
        "win_rate_b": wins[1] / n,
        "avg_turns": round(statistics.mean(turns), 1),
        "p95_turns": sorted(turns)[int(n * 0.95) - 1] if n >= 20 else max(turns),
        "reason_dist": reasons,
        "first_player_winrate": round(first_wins / first_total, 3)
        if first_total else None,
        "avg_events": round(statistics.mean(len(b.events) for b in results), 0),
    }
