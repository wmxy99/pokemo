# -*- coding: utf-8 -*-
"""fx 族：**效果免疫**（引擎侧 · 2026-10-27，S4 扩到团队范围与训练家来源）

解析侧产出四种可完整表达的形态：

    fx:effect_immune:attack:self                    对手宝可梦的**招式效果**不作用于自身
    fx:effect_immune:ability:self                   对手宝可梦的**特性效果**不作用于自身
    fx:effect_immune:attack:team:basic_teamrocket   对手的招式效果不作用于**我方基础火箭队宝可梦**
    fx:effect_immune:trainer:item+supporter:self    对手打出的**道具/支援者卡效果**不作用于自身

语义边界（与卡面括号一致）：
  · **伤害不是效果** —— 普通招式伤害照常结算；被免疫的只有状态、弃能、
    换位、指示物等「效果」；
  · 只免疫**做给这只宝可梦的**效果，攻击方自身的效果（抽牌/自伤/护盾等）
    不受影响；
  · **训练家来源**只覆盖「对手从手牌打出的 Item / Supporter」——那是
    `Battle._do_trainer` 的语义域；场地与特性触发的效果不在此列（卡面写的是
    「plays an Item or Supporter card **from their hand**」）。
  · `team:<pred>` 的持有者是**施加者**：只要它在场上，整个满足条件的我方
    宝可梦群体都免疫（Team Rocket's Articuno「Repelling Veil」）。

未知 tag 形态必须抛 ValueError —— 静默忽略会让「免疫」变成无效果。
"""
from __future__ import annotations

PREFIXES = ("fx:effect_immune:",)

# 来源类型 → 该来源允许的「子类别」（空集合 = 不分子类）
_SOURCES = {
    "attack": frozenset(),
    "ability": frozenset(),
    "trainer": frozenset({"item", "supporter", "item+supporter"}),
}


def _is_basic_teamrocket(fp) -> bool:
    """「你的**基础**火箭队宝可梦」——基础阶段 + 卡名以 Team Rocket's 开头"""
    if fp is None:
        return False
    if int(getattr(fp.pdef, "stage", 0)) != 0:
        return False
    key = "".join(ch for ch in str(fp.pdef.name or "").lower() if ch.isalnum())
    return key.startswith("teamrocket")


_TEAM_PREDICATES = {
    "basic_teamrocket": _is_basic_teamrocket,
}

_PARSE_CACHE: dict = {}


def _parse_tag(tag: str) -> dict:
    """解析成 ``{"source", "kinds", "scope", "pred"}``（非法即抛错）

    文法::

        fx:effect_immune:<source>:self
        fx:effect_immune:<source>:team:<pred>
        fx:effect_immune:trainer:<kinds>:self
    """
    cached = _PARSE_CACHE.get(tag)
    if cached is not None:
        return cached
    parts = str(tag).split(":")
    if len(parts) < 4 or parts[0] != "fx" or parts[1] != "effect_immune":
        raise ValueError(f"非法 effect_immune tag：{tag}")
    source = parts[2]
    if source not in _SOURCES:
        raise ValueError(f"未识别的 effect_immune 来源：{tag}")
    rest = parts[3:]
    kinds = ""
    # trainer 的**子类别**写在 self/team 之前（`trainer:item+supporter:self`）
    if source == "trainer":
        if not rest or rest[0] not in _SOURCES["trainer"]:
            raise ValueError(f"trainer 免疫必须写明子类别：{tag}")
        kinds = rest[0]
        rest = rest[1:]
    if not rest:
        raise ValueError(f"effect_immune 缺少范围：{tag}")
    scope = rest[0]
    pred = ""
    if scope == "team":
        if len(rest) < 2 or not rest[1]:
            raise ValueError(f"team 免疫必须给出群体谓词：{tag}")
        pred = rest[1]
        if pred not in _TEAM_PREDICATES:
            raise ValueError(f"未识别的 team 免疫群体：{tag}")
    elif scope != "self":
        raise ValueError(f"未识别的 effect_immune 范围：{tag}")
    if kinds and scope != "self":
        # 目前只有「自身」形态带子类别（团队形态按招式结算）
        raise ValueError(f"trainer 免疫暂只支持 self 范围：{tag}")
    out = {"source": source, "kinds": kinds, "scope": scope, "pred": pred}
    _PARSE_CACHE[tag] = out
    return out


def _kind_matches(want: str, kind: str) -> bool:
    """免疫声明的子类别 vs 实际卡类别（``item+supporter`` 拆开比对）"""
    if not want:
        return True
    return str(kind or "") in want.split("+")


def effect_immune(battle, fp, source: str, kinds: str = "") -> bool:
    """``fp`` 是否免疫来自 ``source`` 的效果（可限定训练家子类别）

    先看**自身**的 tag；再看**同侧队友**上 `team:<pred>` 形态的 tag（团队免疫
    的施加者在场上即可，被保护者不必带 tag）。

    ⚠ 只判「谁免疫」，**不判**施加者是不是对手 —— 那一步由调用方做
      （`Battle._effect_blocked` 负责），否则「自己用招式弃自己的能量」
      会被自己的免疫挡住。
    """
    if fp is None:
        return False
    want = str(source or "")
    if want not in _SOURCES:
        raise ValueError(f"未识别的效果来源：{source}")

    def _hit(tag: str) -> bool:
        info = _parse_tag(tag)
        if info["source"] != want:
            return False
        if want == "trainer" and not _kind_matches(info["kinds"], kinds):
            return False
        if info["scope"] == "self":
            return True
        return bool(_TEAM_PREDICATES[info["pred"]](fp))

    for ab in getattr(fp.pdef, "abilities", ()) or ():
        tag = str(getattr(ab, "tag", "") or "")
        if tag.startswith("fx:effect_immune:") and _hit(tag):
            return True
    # 队友上的团队免疫：只在**同侧**扫描（`fp` 在谁的场上就扫谁）
    if battle is not None:
        for pi in (0, 1):
            pl = battle.players[pi]
            if fp not in pl.all_field():
                continue
            for mate in pl.all_field():
                if mate is None or mate is fp:
                    continue
                for ab in getattr(mate.pdef, "abilities", ()) or ():
                    tag = str(getattr(ab, "tag", "") or "")
                    if not tag.startswith("fx:effect_immune:"):
                        continue
                    info = _parse_tag(tag)
                    if info["scope"] != "team" or info["source"] != want:
                        continue
                    if _TEAM_PREDICATES[info["pred"]](fp):
                        return True
            break
    return False
