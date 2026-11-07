# -*- coding: utf-8 -*-
"""fx 族：**招式费用抬高**（引擎侧 · 2026-10-28）

解析侧 `agent/fx/fx_attack_cost.py` 产出 tag，本模块通过 ``attack_cost`` 钩子
（`battle._can_pay_attack` 每次校验费用时调用）抬高**对手**的招式费用。

tag 形态（逐字解析，未知形态抛 ValueError —— 绝不静默忽略）：
    fx:attack_cost:opp_basic:<n>:colorless            对手基础宝可梦 +n
    fx:attack_cost:self:less:per:<counter>[:name:<k>]  自身 -n×计数源（可限定招式名）

``<counter>`` 交给 ``battle._eval_counter`` 求值，**可以自带冒号**
（第六十六批：`own_discard_named:kofu` 按弃牌堆 Kofu 卡数减费）；
只有末尾的 ``:name:<k>`` 是招式名限定，从尾部剥离，不与计数源混淆。

语义边界：
  · 前一种只在**持有者位于战斗位**时生效（卡面「is in the Active Spot」）；
  · 只作用于**基础宝可梦**（stage == 0）的招式；
  · 只加/减**无色**门槛（引擎费用模型里无色可由任意能量支付）。
"""
from __future__ import annotations

import re

PREFIXES = ("fx:attack_cost:",)


def _name_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(name or "").lower())


def _parse_tag(tag: str) -> tuple:
    """校验 tag → (scope, n, counter, attack_key)"""
    parts = str(tag).split(":")
    if len(parts) < 3 or parts[0] != "fx" or parts[1] != "attack_cost":
        raise ValueError(f"非法 attack_cost tag：{tag}")
    scope = parts[2]
    if scope == "opp_basic":
        if len(parts) != 5 or not parts[3].isdigit():
            raise ValueError(f"非法 attack_cost tag：{tag}")
        n, color = int(parts[3]), parts[4]
        if color != "colorless":
            raise ValueError(f"attack_cost 只支持无色加价：{tag}")
        if n <= 0:
            raise ValueError(f"attack_cost 费用增量非法：{tag}")
        return scope, n, None, None
    if scope in ("self_set", "self_omit"):
        # `fx:attack_cost:self_set:<n>:<color>[:if:<cond>][:name:<k>]`
        # `fx:attack_cost:self_omit:<color>[:if:<cond>][:name:<k>]`
        # （第一百一十二批起）——**改写**费用而不是加减：
        #   · set  = 「can use the X attack for {C}」/「ignore all Energy in
        #            this attack's cost」→ 费用整体换成 <n> 个 <color>（0 = 免费）；
        #   · omit = 「ignore all {C} Energy in the costs」→ 去掉该色，其余照付。
        # ⚠ 用「减固定值」表达 set 会在费用构成不同时算错（[C,C] 减 1 仍要 1 C，
        #   而卡面是「整个费用改为」）。
        # ⚠ **先**切 `:if:`：条件本身可含冒号（`opp_discard_has_named:colress`），
        #   而 `:name:<k>` 必须排在 `if` **之前**。不先切就会把 `:name:trifrost`
        #   吞进条件里（实测：Kyurem 的 cond 变成
        #   `opp_discard_has_named:colress:name:trifrost`，引擎报未知计数源）。
        _body = ":".join(parts[3:]) if parts[3:] else ""
        if ":if:" in _body:
            _head, _cond = _body.split(":if:", 1)
        else:
            _head, _cond = _body, None
        segs = _head.split(":") if _head else []
        val = None
        if scope == "self_set":
            if not segs or not segs[0].isdigit():
                raise ValueError(f"attack_cost set 缺数值：{tag}")
            val = int(segs[0]); segs = segs[1:]
        if not segs:
            raise ValueError(f"attack_cost 缺颜色：{tag}")
        color, segs = segs[0], segs[1:]
        cond, atk_key, i = _cond, None, 0
        while i < len(segs):
            if segs[i] == "name" and i + 1 < len(segs):
                atk_key = re.sub(r"[^a-z0-9]", "", segs[i + 1].lower())
                i += 2; continue
            raise ValueError(f"attack_cost 未识别限定：{tag}")
        return scope, val, (color, cond), atk_key
    if scope == "self":
        # fx:attack_cost:self:less:per:<counter>[:name:<k>]
        # ⚠ 第六十六批：``<counter>`` **本身可以含冒号**（`_eval_counter` 的
        #   `own_discard_named:kofu` / `own_discard_has_attack:<key>` 等）。
        #   旧写法把 `parts[5]` 当整个计数源、并硬性要求 `len(parts)==8`，
        #   会把这类合法 tag 判成「非法」——卡进池却一打就抛 ValueError。
        #   改为从尾部剥离**可选**的 `:name:<k>` 后缀，其余整段交给
        #   `_eval_counter`（它自己对未知计数源抛错，不在本层重复白名单）。
        if len(parts) < 6 or parts[3] != "less" or parts[4] != "per":
            raise ValueError(f"非法 attack_cost tag：{tag}")
        rest = ":".join(parts[5:])
        key = None
        if ":name:" in rest:
            rest, key = rest.rsplit(":name:", 1)
            if not key:
                raise ValueError(f"attack_cost 缺少招式名：{tag}")
        counter = rest
        if not counter:
            raise ValueError(f"attack_cost 缺少计数源：{tag}")
        return scope, 1, counter, key
    raise ValueError(f"未识别的 attack_cost 范围：{tag}")


def attack_cost(battle, attacker, atk) -> dict:
    """攻击方是否要为**对手战斗位**上的常驻效果多付 / 自行为常驻效果少付无色"""
    if attacker is None:
        return {}
    owner = None
    for i, p in enumerate(battle.players):
        if p.active is attacker:
            owner = i
            break
    if owner is None:
        return {}
    delta = 0
    # ① 对手战斗位的「基础宝可梦 +n 无色」
    holder = battle.players[1 - owner].active
    if holder is not None:
        for ab in holder.pdef.abilities:
            tag = str(getattr(ab, "tag", "") or "")
            if not tag.startswith("fx:attack_cost:"):
                continue
            scope, n, _c, _k = _parse_tag(tag)
            if scope == "opp_basic" and attacker.stage == 0:
                delta += n
    # ①b/①c 攻击方自身的**费用改写**（第一百一十二批）
    # ⚠ 来源有两处：**特性**（Kyurem / Azumarill / Decidueye，整只生效）与
    #   **招式自身的 effects**（Conkeldurr「ignore all Energy in this attack's
    #   cost」/ Crawdaunt「this attack can be used for {D}」，只对这一招生效）。
    #   只扫特性会漏掉写在招式文本里的那类，且它们是**同一族**语义。
    _atk_name = _name_key(getattr(atk, "name", ""))
    _tag_sources = [str(getattr(ab, "tag", "") or "")
                    for ab in attacker.pdef.abilities]
    _tag_sources += [str(t) for t in (getattr(atk, "effects", None) or ())]
    for tag in _tag_sources:
        if not tag.startswith(("fx:attack_cost:self_set:",
                               "fx:attack_cost:self_omit:")):
            continue
        scope, val, cspec, key = _parse_tag(tag)
        if key is not None and key != _atk_name:
            continue
        color, cond = cspec
        if cond:
            from ..battle import _eval_condition      # 惰性导入，避免循环依赖
            if not _eval_condition(battle, cond):
                continue
        if scope == "self_set":
            return {"set": (() if not val else ((color, int(val)),))}
        return {"omit": color}
    # ② 攻击方自身的「按计数源减费」
    _atk_name = _name_key(getattr(atk, "name", ""))
    for ab in attacker.pdef.abilities:
        tag = str(getattr(ab, "tag", "") or "")
        if not tag.startswith("fx:attack_cost:self:"):
            continue
        scope, _n, counter, key = _parse_tag(tag)
        if key is not None and key != _atk_name:
            continue                     # 卡面限定了招式名（如「Blood Moon」）
        from ..battle import _eval_counter           # 惰性导入，避免循环依赖
        delta -= int(_eval_counter(battle, counter))
    # ③ 场地：**双方** Tera 宝可梦的招式 +{C}（第一百零六批，Nighttime Mine）
    #    卡面「each Tera Pokémon in play (both yours and your opponent's)」——
    #    不看归属，只看攻击方是不是 Tera。
    _m = battle._stadium_meta()
    if _m.get("kind") == "tera_cost_up" and _m.get("n"):
        if str(getattr(attacker.pdef, "category", "") or "").startswith("Tera"):
            delta += int(_m["n"])
    return {"colorless": delta} if delta else {}
