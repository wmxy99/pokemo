# -*- coding: utf-8 -*-
"""特殊能量（Special Energy）子系统 · 2026-10-28

为什么单独成模块
----------------
特殊能量与基本能量的差别不在"属性"，而在**它是一张有规则的卡**：
  · 提供的能量点数/属性取决于**持有者**（Stage 2、基础、进化、{G} 属性…）；
  · 附着时可能触发效果（抽牌 / 检索铺场）；
  · 附着后可能改写上限、免疫、受击反应、奖赏卡数量、回合结束弃置；
  · 有的只能附着在某类宝可梦上。
把这些规则堆进 `battle.py` 会让"能量"语义散落各处，故集中在本模块。

数据形态
--------
池条目：`{"supertype":"Energy", "kind":"special", "tag":"senergy:<kind>"}`；
引擎侧 `EnergyDef.tag` 保存该 tag。tag 只编码**种类**（12 张卡 12 个种类），
数值/条件由本模块的规格表给出 —— 与 `tool:*` 把参数写进 tag 的做法不同，
这里参数属于"卡的身份"，写进 tag 只会让 12 条超长串更难核对。

能量键（`FieldPokemon.energy` / `energy_counts()`）
--------------------------------------------------
  · 具体属性      `"grass"` / `"colorless"` / …
  · 任意属性      `"any"`         —— 可当任意一色支付（每次 1 点）
  · 双色任选      `"flex:psychic,darkness"` —— 可当这两色之一支付
基本能量仍然直接落在具体属性键上（行为完全不变）。

纪律
----
· 未知 `senergy:<kind>` **抛 ValueError**（绝不当成无色能量静默放过）；
· `provides` 一律按**持有者当前物种**实时计算（进化后自动重算，不会残留旧值）；
· 未建模的形态（Boomerang：需要"能量卡身份 + 弃牌堆回收"）解析侧不产 tag。
"""
from __future__ import annotations

from typing import Optional

ANY = "any"
FLEX_PREFIX = "flex:"

# kind → 静态规格。字段含义见各函数；未列出的字段 = 该卡没有该机制。
_SPECS = {
    # 提供 {C}；对手招式效果不作用于持有者（伤害除外）
    "mist": {"provides": {"colorless": 1}, "immune_attack": True},
    # 提供 {F}；同上，且只对 {F} 持有者生效
    "rock_fighting": {"provides": {"fighting": 1}, "immune_attack": True,
                      "immune_if_type": "fighting"},
    # 提供 {G}；{G} 持有者 +20 HP
    "grow_grass": {"provides": {"grass": 1}, "hp_plus": 20,
                   "hp_if_type": "grass"},
    # 提供 {C}；从手牌附着时抽 4
    "enriching": {"provides": {"colorless": 1}, "on_attach_draw": 4},
    # 提供 {C}；持有者在战斗位被招式伤害时，攻击方受 20 点伤害（即使被击倒）
    "spiky": {"provides": {"colorless": 1}, "on_damaged_counter": 20},
    # 提供任意 1；被对手招式伤害击倒时对方少拿 1 张奖赏卡（每局一次）
    "legacy": {"provides": {ANY: 1}, "prize": -1},
    # 提供 {C}；附着于 Stage 2 → 任意属性 2
    "neo_upper": {"provides": {"colorless": 1}, "if_stage": (2, {ANY: 2})},
    # 提供 {C}；附着于基础宝可梦 → 任意属性 1
    "prism": {"provides": {"colorless": 1}, "if_stage": (0, {ANY: 1})},
    # 提供 {C}；进化宝可梦 → {C}{C}{C}；回合结束弃置
    "ignition": {"provides": {"colorless": 1},
                 "if_evolved": {"colorless": 3}, "discard_eot": True},
    # 只能附着于火箭队的宝可梦；提供 2 个 {P}/{D} 任意组合
    "team_rocket": {"provides": {"flex:psychic,darkness": 2},
                    "attach_only": "team_rocket"},
    # 提供 {P}；从手牌附着于 {P} 宝可梦时检索 2 张基础 {P} 铺后备
    "telepath": {"provides": {"psychic": 1},
                 "on_attach_search": {"n": 2, "type": "psychic",
                                      "holder_type": "psychic"}},
}

# 已建模的种类（Boomerang 不在内：需要"能量卡身份 + 攻击后从弃牌堆回附"）
KINDS = tuple(_SPECS)

TEAM_ROCKET_CATEGORY = "Trainer's Pokémon(Team Rocket)"


def kind_of(edef) -> Optional[str]:
    """能量定义 → 特殊能量 kind（基本能量返回 None）"""
    tag = str(getattr(edef, "tag", "") or "")
    if not tag.startswith("senergy:"):
        return None
    return tag.split(":", 1)[1]


def spec(kind: str) -> dict:
    """kind → 规格（未知种类抛错 —— 不允许当成"提供无色 1"静默处理）"""
    try:
        return _SPECS[kind]
    except KeyError as exc:
        raise ValueError(f"未建模的特殊能量：{kind}") from exc


def _merge(dst: dict, src: dict) -> None:
    for t, n in src.items():
        dst[t] = dst.get(t, 0) + n


def provides(edef, fp) -> dict:
    """该能量卡现在为 `fp` 提供多少、哪种能量（按持有者**当前**物种计算）"""
    kind = kind_of(edef)
    if kind is None:
        return {str(getattr(edef, "type", "colorless")): 1}
    s = spec(kind)
    out: dict = {}
    _merge(out, s.get("provides") or {})
    cond = s.get("if_stage")
    if cond and int(getattr(fp.pdef, "stage", 0)) == int(cond[0]):
        # 条件形态是**替换**而不是叠加（卡面写 "instead"/"provides every type"）
        out = dict(cond[1])
    if s.get("if_evolved") and int(getattr(fp.pdef, "stage", 0)) >= 1:
        out = dict(s["if_evolved"])
    return out


def attach_allowed(edef, fp) -> bool:
    """附着限制（当前只有「火箭队的能量」）"""
    kind = kind_of(edef)
    if kind is None:
        return True
    s = spec(kind)
    if s.get("attach_only") == "team_rocket":
        return str(getattr(fp.pdef, "category", "") or "") == TEAM_ROCKET_CATEGORY
    return True


def hp_plus(fp) -> int:
    """特殊能量带来的 HP 上限加成（按卡面限定：Grow Grass 只对 {G} 持有者）"""
    total = 0
    for ref in getattr(fp, "senergy", ()) or ():
        kind = kind_of(ref.edef)
        if kind is None:
            continue
        s = spec(kind)
        n = int(s.get("hp_plus") or 0)
        if not n:
            continue
        wt = s.get("hp_if_type")
        if wt and str(fp.pdef.ptype) != wt:
            continue
        total += n
    return total


def effect_immune(fp, source: str) -> bool:
    """该宝可梦是否因特殊能量而免疫来自 `source` 的**效果**（伤害不是效果）"""
    if source != "attack":
        return False
    for ref in getattr(fp, "senergy", ()) or ():
        kind = kind_of(ref.edef)
        if kind is None:
            continue
        s = spec(kind)
        if not s.get("immune_attack"):
            continue
        wt = s.get("immune_if_type")
        if wt and str(fp.pdef.ptype) != wt:
            continue
        return True
    return False


def on_damaged_counter(fp) -> int:
    """持有者在战斗位被招式伤害时，攻击方应受到的伤害（Spiky Energy）"""
    for ref in getattr(fp, "senergy", ()) or ():
        kind = kind_of(ref.edef)
        if kind is None:
            continue
        n = int(spec(kind).get("on_damaged_counter") or 0)
        if n:
            return n
    return 0


def prize_delta(battle, victim) -> int:
    """击倒时的奖赏卡修正（Legacy Energy：每局一次、少拿 1 张）

    调用点在 `_resolve_ko` 的「由招式击倒」分支内 —— 卡面限定 damage from an attack。
    """
    if getattr(battle, "_legacy_used", False):
        return 0
    for ref in getattr(victim, "senergy", ()) or ():
        kind = kind_of(ref.edef)
        if kind is None:
            continue
        d = int(spec(kind).get("prize") or 0)
        if d:
            battle._legacy_used = True          # 每局只生效一次
            return d
    return 0


def discard_at_end_of_turn(battle, p) -> list:
    """回合结束弃置的特殊能量（Ignition Energy）→ 返回被弃置的卡名列表"""
    dropped = []
    for fp in p.all_field():
        for ref in list(getattr(fp, "senergy", ()) or ()):
            kind = kind_of(ref.edef)
            if kind is None or not spec(kind).get("discard_eot"):
                continue
            fp.senergy.remove(ref)
            battle._to_discard(p, ref)
            dropped.append(ref.edef.name)
            battle._log("discard_energy", {"player": battle.current,
                                           "target": fp.name, "n": 1,
                                           "side": "own", "from": "end_turn",
                                           "etype": kind})
    return dropped


def on_attach(battle, p, fp, edef) -> str:
    """附着时的触发效果（从手牌附着才有意义）"""
    kind = kind_of(edef)
    if kind is None:
        return ""
    s = spec(kind)
    out = []
    n = int(s.get("on_attach_draw") or 0)
    if n:
        got = battle._safe_draw(battle.current, n, source=f"senergy:{kind}")
        out.append(f"draw {got}")
    sr = s.get("on_attach_search")
    if sr:
        if str(fp.pdef.ptype) != sr["holder_type"]:
            # 卡面限定「从手牌附着于 {P} 宝可梦时」：属性不符就不触发
            out.append(f"no search（持有者非 {sr['holder_type']}）")
        else:
            out.append(battle._search_bench(
                p, f"search_bench:{sr['n']}:basic:type:{sr['type']}"))
    return ("; ".join(out)) or ""
