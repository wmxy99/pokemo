# -*- coding: utf-8 -*-
"""PokéLab Agent · 对战解说（F5 内，模块 3 · 纯展示可降级）

设计要点
--------
1. **只喂公开信息**：场上局面 / 能量 / 奖赏 / 牌库与手牌**数量** / 本回合事件流。
   绝不把对手手牌内容或牌库顶传给模型（提示词也重申该约束）。
2. **可降级**：未配置云端模型、或本地模式拦截外发时，回退**离线模板解说**
   （``generated_by="rule(offline)"``），保证该功能永不阻断对局。
3. **合规 C4**：返回值始终带 ``ai_generated`` / ``generated_by`` / ``ai_notice``，
   前端必须以徽标展示，不得与人工内容无标注混排。
4. **合规 6.2**：外发经 ``agent.llmclient.chat`` → 端点校验（本地模式禁止外部）+
   脱敏 + 审计（不含原文）。

本模块**不依赖** apps/api；配置由 apps/api 通过 ``llmclient.set_config_provider`` 注入。
"""
from __future__ import annotations

from typing import Optional

from .llmclient import LLMConfigError, chat as _chat, config as _config

AI_NOTICE = ("本内容由 AI 自动生成，仅供研究参考；可能不完全准确，"
             "以官方规则与卡面原文为准。")

# 事件类型 → 中文叙述（离线解说用；也作为给模型的“可读化”素材）
_EVENT_ZH = {
    "turn_start": "回合开始",
    "turn_end": "回合结束",
    "draw": "抽牌",
    "play": "放置宝可梦",
    "evolve": "进化",
    "attach": "附着能量",
    "retreat": "撤退",
    "switch": "换位",
    "gust": "拉对手后备上场",
    "attack": "发动招式",
    "damage": "造成伤害",
    "status": "陷入特殊状态",
    "checkup": "状态检查",
    "knockout": "宝可梦被击倒",
    "prize": "取得奖赏卡",
    "promote": "推后备上场",
    "trainer": "使用训练家卡",
    "ability": "发动特性",
    "coin": "抛币",
    "shield": "获得防护",
    "blocked": "效果被阻止",
    "no_effect": "效果无效（无对象）",
    "heal": "回复伤害",
    "discard": "弃牌",
    "discard_energy": "弃置能量",
    "move_damage": "移动伤害指示物",
    "recover": "从弃牌堆回收",
    "draw_to": "补充手牌",
    "finish": "对局结束",
    "setup": "开局",
    "hand_overflow": "手牌超限弃牌",
}


def board_summary(view: dict) -> str:
    """把 view 的**公开信息**压缩成可读局面（双方对称，不泄露隐藏信息）

    字段以 `Battle.view()` 为准：``me``（我方座位号）/ ``me_state`` /
    ``opp_state``；宝可梦摘要用 ``display/hp/dmg/energy/status``。
    """

    def _mon(d: Optional[dict]) -> str:
        if not d:
            return "空"
        name = d.get("display") or d.get("name")
        hp, dmg = d.get("hp", "?"), d.get("dmg", 0)
        left = (hp - dmg) if isinstance(hp, int) else "?"
        st = "/".join(d.get("status") or []) or "无"
        return f"{name} {left}/{hp}HP 能量{d.get('energy', 0)} 状态{st}"

    def side(d: Optional[dict], label: str, *, hidden_hand: bool) -> str:
        if not d:
            return f"{label}：无战斗宝可梦"
        hand = "手牌 ? 张" if hidden_hand else f"手牌 {len(d.get('hand') or [])} 张"
        return (f"{label}：战斗位 {_mon(d.get('active'))}｜"
                f"后备 {len(d.get('bench') or [])} 只"
                + ("（" + "、".join(_mon(b) for b in (d.get("bench") or [])[:3])
                   + ("…" if len(d.get("bench") or []) > 3 else "") + "）"
                   if d.get("bench") else "")
                + f"｜奖赏剩 {d.get('prizes_left', '?')}｜{hand}｜"
                f"牌库 {d.get('deck_count', '?')} 张｜弃牌 {d.get('discard_count', '?')} 张")

    return "\n".join([side(view.get("me_state"), "我方", hidden_hand=False),
                      side(view.get("opp_state"), "对手", hidden_hand=True)])


# 事件里 ``player`` 字段的**语义角色**——决定「我方/对手」该怎么念。
# 关键：不同事件的 player 含义不同（attack.player 是攻击方，damage.player 却是
# **受害方**，prize.player 是**取得方**）。不区分就会把「我方受到伤害」说成
# 「我方造成伤害」，属于实质性错误，故显式建表。
_EVENT_PLAYER_ROLE = {
    "attack": "actor", "trainer": "actor", "ability": "actor",
    "draw": "actor", "play": "actor", "evolve": "actor", "attach": "actor",
    "retreat": "actor", "switch": "actor", "gust": "actor", "coin": "actor",
    "turn_start": "actor", "turn_end": "actor", "heal": "actor",
    "discard": "actor", "discard_energy": "actor", "move_damage": "actor",
    "recover": "actor", "draw_to": "actor", "shield": "actor",
    "promote": "actor", "checkup": "actor",
    "damage": "victim", "knockout": "taker", "prize": "taker",
    "finish": "neutral", "setup": "neutral",
}


def _event_line(ev: dict, my_side: Optional[int] = None) -> str:
    """单条事件 → 可读叙述（**角色感知**：分清攻击方 / 受害方 / 取得方）"""
    t = ev.get("type") or "?"
    zh = _EVENT_ZH.get(t, t)
    role = _EVENT_PLAYER_ROLE.get(t, "actor")
    if role == "neutral" or ev.get("player") is None:
        head = zh
    else:
        side = "我方" if ev.get("player") == my_side else "对手"
        if role == "victim":
            head = f"{side}宝可梦受到伤害"
        elif role == "taker":
            head = f"{side}取得奖赏" if t == "prize" else f"{side}击倒对手宝可梦"
        else:
            head = f"{side}{zh}"
    bits = []
    for k in ("card", "attack", "target", "from", "to", "status", "dmg",
              "n", "amt", "name", "result", "tag", "reason"):
        v = ev.get(k)
        if v not in (None, "", [], {}):
            bits.append(f"{k}={v}")
    return f"T{ev.get('turn', '?')} {head}" + (f"（{'，'.join(bits)}）" if bits else "")


def recent_events(events: list, n: int = 8,
                  my_side: Optional[int] = None) -> str:
    """最近 n 条事件的可读叙述

    事件流本身即**公开记录**（引擎只记录公开发生的事），故可安全外发；
    但为防模型误读，这里已把角色（我方/对手）标注清楚。
    """
    tail = [e for e in (events or [])][-max(1, n):]
    if not tail:
        return "（本回合暂无事件）"
    return "\n".join(_event_line(e, my_side) for e in tail)


def offline_commentary(view: dict, events: list) -> str:
    """离线模板解说（未配置云端模型 / 本地模式拦截时的降级路径）

    只做**事实性复述 + 一句要点**，不做预测、不编造未发生的事（不假装有模型）。
    """
    me = view.get("me_state") or {}
    opp = view.get("opp_state") or {}
    turn = view.get("turn", "?")
    my_side = view.get("me")
    act = (me.get("active") or {}).get("display") \
        or (me.get("active") or {}).get("name") or "空"
    key = [e for e in (events or []) if e.get("type") in
           ("attack", "damage", "knockout", "prize", "promote", "finish")]
    lines = [f"第 {turn} 回合。",
             f"我方战斗位 {act}，奖赏剩 {me.get('prizes_left', '?')}；"
             f"对手奖赏剩 {opp.get('prizes_left', '?')}。"]
    if key:
        lines.append("最近的关键动作：" + _event_line(key[-1], my_side) + "。")
    lines.append("（离线解说：未启用云端模型，仅复述公开信息。）")
    return "".join(lines)


def explain_turn(view: dict, events: list, *, backend: str = "auto",
                 opponent_hint: str = "", current_side: Optional[int] = None) -> dict:
    """生成一段对局解说

    ``backend``：``auto``（有配置走云端、失败回退离线）· ``llm``（强制云端，
    不可用则如实报错并回退）· ``rule``（强制离线模板）。
    ``current_side``：当前行动方座位号（``view`` 不含该字段，由调用方传入
    ``battle.current``）；缺省时按「我方」叙述。

    返回 ``{text, backend, ai_generated, generated_by, ai_notice, model,
    endpoint, privacy, elapsed_ms, errors}``——**始终带 C4 标识**。
    """
    from . import prompts as _prompts
    base = {"text": "", "backend": backend, "ai_generated": True,
            "ai_notice": AI_NOTICE, "model": None, "endpoint": None,
            "privacy": None, "elapsed_ms": None, "errors": [],
            "generated_by": None}

    if backend == "auto":
        try:
            _config()
            use = "llm"
        except LLMConfigError:
            use = "offline"
    elif backend == "llm":
        use = "llm"
    else:
        use = "offline"

    if use == "llm":
        try:
            sys_t = _prompts.render("commentator_system")
            side = view.get("me") if current_side is None else current_side
            usr_t = _prompts.render(
                "commentator_user", turn=view.get("turn", "?"),
                player_name="我方" if side == view.get("me") else "对手",
                board_summary=board_summary(view),
                recent_events=recent_events(events, 8, view.get("me")),
                opponent_hint=opponent_hint or "（无）")
            res = _chat([{"role": "system", "content": sys_t},
                         {"role": "user", "content": usr_t}],
                        temperature=0.4, purpose="commentary")
            text = (res.get("content") or "").strip()
            if not text:
                raise LLMConfigError("云端模型返回空解说")
            base.update({"text": text, "generated_by": "llm",
                         "model": res["model"], "endpoint": res["endpoint"],
                         "privacy": res["privacy"],
                         "elapsed_ms": res["elapsed_ms"]})
            return base
        except Exception as e:                             # noqa: BLE001
            # 降级而非报错：解说属「纯展示可降级」功能，不得阻断对局
            from .privacy import PrivacyError
            tag = "rule(privacy-blocked)" if isinstance(e, PrivacyError) \
                else "rule(fallback)"
            base["errors"].append(str(e))
            base.update({"text": offline_commentary(view, events),
                         "backend": f"{backend}→offline", "generated_by": tag})
            return base

    base.update({"text": offline_commentary(view, events),
                 "generated_by": "rule(offline)"})
    return base
