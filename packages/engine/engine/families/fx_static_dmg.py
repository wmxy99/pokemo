# -*- coding: utf-8 -*-
"""fx 族：静态伤害增减（S1 静态修正层 · 引擎侧）

解析侧 `agent/fx/fx_static_dmg.py` 产出 tag，本模块在**每次招式伤害结算前**
（弱点/抗性之前，见 `battle._attack_mods`）计算常驻加伤 / 压制减伤。

tag 形态（逐字解析，未知形态抛 ValueError —— 绝不静默忽略）：
    fx:static_dmg:add:<n>:self
    fx:static_dmg:add:<n>:self_if_damage:<counters>
    fx:static_dmg:add:<n>:self_vs_ability
    fx:static_dmg:add:<n>:self_if_mega_type:<t>[:nonstack]
    fx:static_dmg:add:<n>:self_per_prize
    fx:static_dmg:add:<n>:team
    fx:static_dmg:add:<n>:team_type:<t1,t2>
    fx:static_dmg:add:<n>:team_evolution_type:<t>
    fx:static_dmg:add:<n>:team_vs_evolution
    fx:static_dmg:add:<n>:team_name:<key>[:nonstack]
    fx:static_dmg:sub:<n>:opp_active

纪律：只看卡面明示的范围；认不出的范围返回 0（宁可少算，不可乱加）。
"""
from __future__ import annotations

import re

PREFIXES = ("fx:static_dmg:",)

_TYPES = {"grass", "fire", "water", "lightning", "psychic", "fighting",
          "darkness", "metal", "colorless", "dragon"}

_ADD_SCOPES = {"self", "self_if_damage", "self_vs_ability",
               "self_if_mega_type", "self_per_prize",
               "team", "team_type", "team_evolution_type",
               "team_vs_evolution", "team_name"}
_SUB_SCOPES = {"opp_active"}


def _name_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(name or "").lower())


def _side_of(battle, fp) -> int | None:
    """对象 → 所属玩家下标（按对象同一性；不在场上返回 None）"""
    for i, p in enumerate(battle.players):
        if fp is p.active or any(fp is b for b in p.bench):
            return i
    return None


def _parse_tag(tag: str) -> dict:
    parts = str(tag).split(":")
    if len(parts) < 5 or parts[0] != "fx" or parts[1] != "static_dmg":
        raise ValueError(f"非法 static_dmg tag：{tag}")
    nonstack = parts[-1] == "nonstack"
    body = parts[:-1] if nonstack else parts
    if len(body) < 5 or len(body) > 6:
        raise ValueError(f"static_dmg tag 字段数非法：{tag}")
    direction = body[2]
    raw_n = body[3]
    scope = body[4]
    if not raw_n.isdigit():
        raise ValueError(f"static_dmg tag 缺少数值：{tag}")
    n = int(raw_n)
    arg = body[5] if len(body) > 5 else None
    if direction == "add":
        if scope not in _ADD_SCOPES:
            raise ValueError(f"未识别的 static_dmg 范围：{tag}")
    elif direction == "sub":
        if scope not in _SUB_SCOPES:
            raise ValueError(f"未识别的 static_dmg 减伤范围：{tag}")
        n = -n
    else:
        raise ValueError(f"未识别的 static_dmg 方向：{tag}")

    # 参数校验：该带参数的必须带，且内容合法
    if scope in ("self_if_damage",) and (arg is None or not arg.isdigit()):
        raise ValueError(f"static_dmg 缺条件参数：{tag}")
    if scope in ("team_type", "team_evolution_type"):
        if not arg:
            raise ValueError(f"static_dmg 缺属性参数：{tag}")
        for t in arg.split(","):
            if t not in _TYPES:
                raise ValueError(f"static_dmg 属性非法：{tag}")
    if scope == "self_if_mega_type" and arg not in _TYPES:
        raise ValueError(f"static_dmg 缺属性参数：{tag}")
    if scope == "team_name" and not arg:
        raise ValueError(f"static_dmg 缺阵营参数：{tag}")
    return {"direction": direction, "n": n, "scope": scope, "arg": arg,
            "nonstack": nonstack}


def _is_active(battle, fp) -> bool:
    return any(fp is p.active for p in battle.players)


def _prizes_taken(battle, side: int) -> int:
    """side 玩家**已取得**的奖赏卡数（与 `prizes_left` 口径相反）"""
    return max(0, int(battle.cfg.prizes) - int(battle.players[side].prizes_left))


def _scope_applies(info: dict, battle, source, source_side: int,
                   attacker, attacker_side: int, defender) -> bool:
    scope = info["scope"]
    n = info["n"]

    # ---- 自身范围：效果源必须就是攻击者 ----
    if scope == "self":
        return source is attacker
    if scope == "self_if_damage":
        return source is attacker and attacker.damage >= int(info["arg"]) * 10
    if scope == "self_vs_ability":
        return source is attacker and bool(
            getattr(defender.pdef, "abilities", ()) if defender else ())
    if scope == "self_if_mega_type":
        # 「If you have any {D} Mega Evolution Pokémon {ex} in play, …」
        # （Seviper「Excited Power」）—— 只看**本阵营**场上是否有该属性的 Mega。
        if source is not attacker:
            return False
        side_p = battle.players[source_side]
        return any(fp is not None and bool(getattr(fp.pdef, "is_mega", False))
                   and str(fp.pdef.ptype).lower() == info["arg"]
                   for fp in (side_p.active, *side_p.bench))
    if scope == "self_per_prize":
        return source is attacker

    # ---- 压制对手战斗位：效果源在战斗位，攻击者是对方战斗位 ----
    if scope == "opp_active":
        return (_is_active(battle, source) and source_side != attacker_side
                and attacker is battle.players[attacker_side].active)

    # ---- 全队范围：效果源与攻击者同阵营 ----
    if source_side != attacker_side:
        return False
    if scope == "team":
        return True
    if scope == "team_type":
        return attacker.pdef.ptype in set((info["arg"] or "").split(","))
    if scope == "team_evolution_type":
        return attacker.pdef.stage > 0 and \
            attacker.pdef.ptype in set((info["arg"] or "").split(","))
    if scope == "team_vs_evolution":
        return bool(defender and defender.pdef.stage > 0)
    if scope == "team_name":
        return _name_key(attacker.pdef.name).startswith(info["arg"] or "")
    return False


def static_dmg_plus(battle, attacker, defender) -> int:
    """返回本次招式的静态伤害修正（正=加伤，负=压制）。无效果源返回 0。"""
    if attacker is None:
        return 0
    attacker_side = _side_of(battle, attacker)
    if attacker_side is None:
        return 0
    total = 0
    seen_nonstack: set = set()
    for side, p in enumerate(battle.players):
        for source in (p.active, *p.bench):
            if source is None:
                continue
            for ab in getattr(source.pdef, "abilities", ()):
                tag = str(getattr(ab, "tag", "") or "")
                if not tag.startswith("fx:static_dmg:"):
                    continue
                info = _parse_tag(tag)
                if info["nonstack"] and tag in seen_nonstack:
                    continue
                if not _scope_applies(info, battle, source, side, attacker,
                                      attacker_side, defender):
                    continue
                if info["scope"] == "self_per_prize":
                    total += info["n"] * _prizes_taken(battle, 1 - attacker_side)
                else:
                    total += info["n"]
                if info["nonstack"]:
                    seen_nonstack.add(tag)
    return total
