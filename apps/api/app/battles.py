# -*- coding: utf-8 -*-
"""PokéLab AI API · 人机对战路由（PRD F5）

- 创建对局 → 内存 Battle 会话（进程内保活；重启清空，元数据落 SQLite）
- 人类通过 POST /actions 走引擎合法动作；AI 侧（对手）自动走一步直到再次轮到人
  AI 对手：ai_iters>0 → MCTS；否则规则基线 AI（BaselineAI）
- 全链路 seed 注入（同一 seed 可重放）；GET /battles/{id} 返回 view + legal
"""
from __future__ import annotations
import copy
import random
import time

from fastapi import APIRouter, HTTPException, Depends

from . import store, schemas
from .main import current_user

router = APIRouter(prefix="/battles", tags=["battle"])

DECK_BUILDER = None  # 延迟初始化：decklib.make_deck


def _decks() -> "module":
    global DECK_BUILDER
    if DECK_BUILDER is None:
        from engine import decklib
        DECK_BUILDER = decklib
    return DECK_BUILDER


def _resolve_deck(spec: str) -> list:
    d = _decks()
    try:
        return list(d.make_deck(spec))
    except Exception as e:                       # noqa: BLE001
        raise HTTPException(
            400, f"未知卡组名 {spec}（演示卡组：lightning/fire/psychic/"
                 f"darkness/ability_demo 等；文本卡表解析属 P3 Web MVP）"
        ) from e


def _opp_agent(battle, iters: int, seed: int):
    """AI 对手：MCTS(引擎 rollout) 或规则基线 AI"""
    from engine.agents import BaselineAI
    if iters > 0:
        def decide(view):
            from agent.mcts import mcts_select_action
            return mcts_select_action(battle, iters=iters, seed=seed)
        return decide
    return BaselineAI("baseline")


@router.post("", status_code=201)
def create_battle(req: schemas.BattleCreateReq,
                  user: str = Depends(current_user)):
    from engine.battle import Battle
    seed = req.seed if req.seed is not None else random.randrange(1 << 30)
    da = _resolve_deck(req.deck_a)
    db = _resolve_deck(req.deck_b)
    battle = Battle(da, db, seed=seed)
    bid = store.new_id("btl")
    store.save_battle({"id": bid, "owner": user, "deck_a": req.deck_a,
                       "deck_b": req.deck_b, "seed": seed, "status": "open",
                       "created": store.now(),
                       "payload": {"ai_iters": req.ai_iters,
                                   "player_side": req.player_side}})
    with store.sessions_lock:
        store.battle_sessions[bid] = {
            "battle": battle,
            "seed": seed,
            "ai_iters": req.ai_iters,
            "player_side": req.player_side,
        }
    view = battle.view()
    ai_first = []
    if battle.current != req.player_side and not battle.finished:
        ai_first = _run_ai_until_human(bid)     # 对手先手则自动走至人类回合
        view = battle.view()
    return {"id": bid, "seed": seed, "player_side": req.player_side,
            "turn": view["turn"], "phase": view["phase"],
            "winner": None, "ai_auto_first": ai_first, "view": view}


def _run_ai_until_human(bid: str):
    """AI 侧自动步直至轮到玩家或对局结束；返回本轮回合计（含 AI 日志动作）"""
    s = store.battle_sessions.get(bid)
    if not s:
        raise HTTPException(404, "对局不存在或已过期（服务重启后需重开）")
    battle = s["battle"]
    player_side = s["player_side"]
    ai = _opp_agent(battle, s["ai_iters"], s["seed"])
    ai_actions = []
    guard = 0
    while not battle.finished and battle.current != player_side and guard < 500:
        guard += 1
        act = ai.decide(battle.view())
        ai_actions.append(copy.deepcopy(act))
        res = battle.step(copy.deepcopy(act))
        if not res["ok"]:
            battle.step({"verb": "pass"})
    return ai_actions


@router.post("/{bid}/actions")
def act(bid: str, req: schemas.ActionReq,
        user: str = Depends(current_user)):
    s = store.battle_sessions.get(bid)
    if not s:
        raise HTTPException(404, "对局不存在或已过期")
    battle = s["battle"]
    if battle.finished:
        raise HTTPException(409, "对局已结束")
    if battle.current != s["player_side"]:
        raise HTTPException(409, "当前不是你的回合")
    legal = battle.view().get("legal") or []
    if req.action not in legal:
        raise HTTPException(422, "非法动作：动作必须是引擎 legal_actions 之一"
                                 "（不一致即 bug，PRD F5）")
    res = battle.step(copy.deepcopy(req.action))
    if not res["ok"]:
        raise HTTPException(422, f"引擎拒绝动作：{res}")
    # AI 回应
    ai_actions = _run_ai_until_human(bid)
    view = battle.view()
    store.update_battle(bid, status="finished" if battle.finished else "open",
                        payload={"winner": battle.winner})
    return {"ok": True, "winner": battle.winner,
            "ai_actions": ai_actions, "view": view}


@router.get("/{bid}")
def get_state(bid: str, user: str = Depends(current_user)):
    s = store.battle_sessions.get(bid)
    if not s:
        raise HTTPException(404, "对局不存在或已过期")
    battle = s["battle"]
    return {"id": bid, "seed": s["seed"], "turn": battle.view()["turn"],
            "phase": battle.view()["phase"], "winner": battle.winner,
            "finished": battle.finished,
            "view": battle.view()}
