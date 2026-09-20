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

from fastapi import APIRouter, HTTPException, Depends

from . import store, schemas
from .auth import Principal, current_user, ensure_owner

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
            400, f"未知卡组名 {spec}（预设对手池：A1/A2/A3/A4（真实卡表，"
                 f"M1）；演示卡组：lightning/fire/psychic/draco 等）"
        ) from e


def _deck_of(deck_id, name, p):
    """返回 (引擎卡组, 标签)：优先 F3 已导入卡组 id（owner 隔离 + 未收录卡拦截）"""
    if not deck_id:
        return _resolve_deck(name), name
    d = store.get_deck(deck_id)
    if not d:
        raise HTTPException(404, f"卡组 {deck_id} 不存在（F3 需先导入）")
    ensure_owner(d["owner"], p)
    from .decks import build_engine_deck
    try:
        return build_engine_deck(deck_id), f"{d['name']}[{deck_id[:10]}]"
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


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
                  p: Principal = Depends(current_user)):
    from engine.battle import Battle
    seed = req.seed if req.seed is not None else random.randrange(1 << 30)
    da, label_a = _deck_of(req.deck_a_id, req.deck_a, p)
    db, label_b = _deck_of(req.deck_b_id, req.deck_b, p)
    battle = Battle(da, db, seed=seed)
    bid = store.new_id("btl")
    store.save_battle({"id": bid, "owner": p.name, "deck_a": label_a,
                       "deck_b": label_b, "seed": seed, "status": "open",
                       "created": store.now(),
                       "payload": {"ai_iters": req.ai_iters,
                                   "player_side": req.player_side}})
    with store.sessions_lock:
        store.battle_sessions[bid] = {
            "battle": battle,
            "seed": seed,
            "ai_iters": req.ai_iters,
            "player_side": req.player_side, "owner": p.name,
        }
    # ⚠ 视角必须显式指定为**玩家侧**：battle.view() 默认取 `battle.current`，
    #   而对局可能在 AI 回合结束（此时 current 仍是 AI）→ 会把 AI 的手牌当"我方手牌"返回，
    #   同时棋盘左右互换。这是信息泄露 + 显示错乱（有回归测试守着）。
    view = battle.view(req.player_side)
    opening = {"steps": [], "stopped": None}
    if battle.current != req.player_side and not battle.finished:
        opening = _run_ai_until_human(bid)      # 对手先手则自动走至人类回合
        view = battle.view(req.player_side)
    return {"id": bid, "seed": seed, "player_side": req.player_side,
            "turn": view["turn"], "phase": view["phase"],
            "winner": None,
            # 开局对手动作：ai_steps 带中间态（供逐帧回放）；ai_auto_first 保留旧形态
            "ai_steps": opening["steps"],
            "ai_auto_first": [x["action"] for x in opening["steps"] if x["action"]],
            "ai_stopped": opening["stopped"],
            "commands": battle.command_catalog(), "view": view}


def _slim_view(view: dict) -> dict:
    """回放用的**中间态**：剔除只对"当前可操作方"有意义的字段（legal 等）。

    AI 一轮可能有 5–10 步，每步都带一份 view；去掉 legal 可显著压小响应体积，
    且回放期间前端本就锁定操作（`busy`），不会用到 legal。
    """
    out = dict(view)
    out.pop("legal", None)
    return out


def _slim_steps(steps: list) -> list:
    """组合指令的逐步记录：把每步附带的中间态也精简掉 `legal`（控响应体积）。

    注意保留逐步 `view` 本身 —— 前端要靠它把「我方多步指令」也逐帧演出来。
    """
    out = []
    for st in steps or []:
        if isinstance(st, dict) and isinstance(st.get("view"), dict):
            st = {**st, "view": _slim_view(st["view"])}
        out.append(st)
    return out


def _run_ai_until_human(bid: str) -> dict:
    """AI 侧自动步直至轮到玩家或对局结束。

    返回 `{"steps": [...], "stopped": str|None}`，其中每个 step 是
    `{"action": {...} | None, "view": {...} | None, "note": str | None}`：
      - `action` 为该步**实际生效**的动作（AI 给非法动作而改走 pass 时记录 pass，
        不会回放"没有真正发生"的动作）
      - `view` 为该步执行**之后**的局面（已精简）→ 前端据此**逐帧回放**
      - `note` 用于「因防死循环而中止」这类非动作事件
    """
    s = store.battle_sessions.get(bid)
    if not s:
        raise HTTPException(404, "对局不存在或已过期（服务重启后需重开）")
    battle = s["battle"]
    player_side = s["player_side"]
    ai = _opp_agent(battle, s["ai_iters"], s["seed"])
    steps = []
    guard = 0
    while not battle.finished and battle.current != player_side and guard < 500:
        guard += 1
        act = ai.decide(battle.view())
        res = battle.step(copy.deepcopy(act))
        if not res["ok"]:
            # AI 给出非法动作：改走 pass。若连 pass 都不被接受，则退出（避免死循环）
            fallback = battle.step({"verb": "pass"})
            if not fallback["ok"]:
                steps.append({"action": None, "view": None,
                              "note": "AI 连续给出非法动作，已中止本轮自动步进"})
                break
            act = {"verb": "pass"}
        steps.append({"action": copy.deepcopy(act),
                      "view": _slim_view(battle.view(player_side))})
    stopped = None
    if guard >= 500:
        stopped = "guard"
        steps.append({"action": None, "view": None,
                      "note": "自动步进达到 500 步上限，已中止"})
    return {"steps": steps, "stopped": stopped}


@router.post("/{bid}/actions")
def act(bid: str, req: schemas.ActionReq,
        p: Principal = Depends(current_user)):
    s = store.battle_sessions.get(bid)
    if not s:
        raise HTTPException(404, "对局不存在或已过期")
    ensure_owner(s.get("owner", "guest"), p)
    battle = s["battle"]
    if battle.finished:
        raise HTTPException(409, "对局已结束")
    if battle.current != s["player_side"]:
        raise HTTPException(409, "当前不是你的回合")
    side = s["player_side"]
    # 此处 current == side（上面已校验），但显式传 side 以免日后默认值含义变化时又出问题
    legal = battle.view(side).get("legal") or []

    # ---- 情况 A：直接给引擎动作（保持 F5 严格 1:1，支持名称补全） ----
    if req.action is not None:
        action = req.action
        if action not in legal:
            from engine.commands import resolve_action
            fixed = resolve_action(legal, action)
            if fixed is None or fixed not in legal:
                raise HTTPException(
                    422, "非法动作：动作必须是引擎 legal_actions 之一"
                         "（不一致即 bug，PRD F5）；"
                         "也可改用 command 传入中文指令")
            action = fixed
        res = battle.step(copy.deepcopy(action))
        if not res["ok"]:
            raise HTTPException(422, f"引擎拒绝动作：{res}")
        matched = None
        seq = {"steps": [{"input": str(action.get("verb")), "ok": True,
                          "matched": None, "used_action": action}],
               "executed": 1, "planned": 1, "remaining": [], "stopped": None}
    else:
        # ---- 情况 B：自由文本指令（可组合多步） ----
        raw = req.commands if req.commands else req.command
        from engine.commands import split_sequence, sequence_hint
        parts = split_sequence(raw)
        if not parts:
            raise HTTPException(422, "需提供 action（引擎动作）、"
                                     "command（自由指令）或 commands（步骤数组）")
        # 首步先解析：失败则整单不执行（避免"半执行 + 422"的歧义）
        first = battle.resolve_command(parts[0], verb=req.verb)
        if not first.get("ok"):
            raise HTTPException(422, {
                "error": first.get("error", "指令无法解析"),
                "matched": first.get("matched"),
                "ambiguous": bool(first.get("ambiguous")),
                "candidates": list(first.get("candidates") or [])[:12],
                "steps": parts,
                "hint": sequence_hint(),
                "legal": legal,
            })
        seq = battle.step_sequence(parts, actor=side)
        if seq.get("executed", 0) == 0:
            # 理论上首步已校验通过；兜底（引擎拒绝等）
            bad = next((x for x in seq["steps"] if not x.get("ok")), {})
            raise HTTPException(422, {
                "error": bad.get("error", "指令未被执行"),
                "ambiguous": bool(bad.get("ambiguous")),
                "candidates": list(bad.get("candidates") or [])[:12],
                "steps": seq.get("steps") or parts,
                "legal": legal,
            })
        matched = " 然后 ".join(
            (x.get("matched") or x.get("input")) for x in seq["steps"]
            if x.get("ok")) or None

    # AI 回应（玩家回合结束/移交后才会有 AI 动作）
    player_view = _slim_view(battle.view(side))   # 我方动作后的局面 = 回放第一帧
    ai = _run_ai_until_human(bid)
    view = battle.view(side)                      # 显式玩家视角（勿用默认=当前行动方）
    store.update_battle(bid, status="finished" if battle.finished else "open",
                        payload={"winner": battle.winner})
    my_turn = (not battle.finished and battle.current == side)
    from engine.commands import sequence_hint
    return {"ok": True, "winner": battle.winner,
            "matched": matched,
            "used_action": (seq["steps"][-1].get("used_action")
                            if seq.get("steps") else None),
            # 组合指令详情（单步时 steps 长度为 1，字段形状一致）
            # 每步带 view（该步执行后的中间态，已精简）→ 前端可逐帧演出我方多步指令
            "steps": _slim_steps(seq.get("steps", [])),
            "executed": seq.get("executed", 0),
            "planned": seq.get("planned", 0),
            "remaining": seq.get("remaining", []),
            "stopped": seq.get("stopped"),
            "sequence_hint": sequence_hint(),
            # AI 回应：ai_steps 带逐步中间态（供逐帧回放）；ai_actions 保留旧形态
            "player_view": player_view,
            "ai_steps": ai["steps"],
            "ai_actions": [x["action"] for x in ai["steps"] if x["action"]],
            "ai_stopped": ai["stopped"],
            "commands": battle.command_catalog() if my_turn else [],
            "view": view}


@router.get("/{bid}/commands")
def list_commands(bid: str, p: Principal = Depends(current_user)):
    """当前可用指令清单（中文可读文案 + 对应动作）——供前端提示/自动补全"""
    s = store.battle_sessions.get(bid)
    if not s:
        raise HTTPException(404, "对局不存在或已过期")
    ensure_owner(s.get("owner", "guest"), p)
    battle = s["battle"]
    if battle.finished:
        return {"id": bid, "finished": True, "commands": []}
    if battle.current != s["player_side"]:
        return {"id": bid, "waiting_ai": True, "commands": []}
    return {"id": bid, "finished": False,
            "commands": battle.command_catalog(),
            "hint": "可直接把 label 作为 command 提交（也支持招式英文名/卡名）"}


@router.get("/{bid}")
def get_state(bid: str, p: Principal = Depends(current_user)):
    s = store.battle_sessions.get(bid)
    if not s:
        raise HTTPException(404, "对局不存在或已过期")
    ensure_owner(s.get("owner", "guest"), p)
    battle = s["battle"]
    view = battle.view(s["player_side"])          # 显式玩家视角（勿用默认=当前行动方）
    my_turn = (not battle.finished
               and battle.current == s["player_side"])
    return {"id": bid, "seed": s["seed"], "turn": view["turn"],
            "phase": view["phase"], "winner": battle.winner,
            "finished": battle.finished,
            # 可用指令清单（中文可读）——可直接作为 command 提交
            "commands": battle.command_catalog() if my_turn else [],
            "view": view}


@router.post("/{bid}/commentary")
def commentary(bid: str, req: schemas.CommentaryReq,
               p: Principal = Depends(current_user)):
    """对战解说（F5 内 · 模块 3）

    用最近的事件流 + 公开局面生成一段中文解说。**纯展示，可降级**：
    未配置云端模型（或本地模式拦截外发）时自动回退离线模板，不阻断对局。

    合规：
      · 6.2 —— 外发前脱敏、调用后写审计（不含原文）；本地模式禁止外部端点；
      · C4  —— 返回值始终带 ``ai_generated`` / ``generated_by`` / ``ai_notice``，
               前端必须显式展示「AI 生成」徽标。
    """
    from agent import commentary as _comm
    s = store.battle_sessions.get(bid)
    if not s:
        raise HTTPException(404, "对局不存在或已过期（服务重启后需重开）")
    ensure_owner(s.get("owner", "guest"), p)
    battle = s["battle"]
    view = battle.view(s["player_side"])
    res = _comm.explain_turn(view, battle.events,
                             backend=req.backend,
                             opponent_hint=req.opponent_hint,
                             current_side=battle.current)
    return {"id": bid, "turn": view["turn"],
            "winner": battle.winner, "finished": battle.finished,
            "board_summary": _comm.board_summary(view),
            "recent_events": _comm.recent_events(battle.events, req.n_events,
                                                 view.get("me")),
            **res}
