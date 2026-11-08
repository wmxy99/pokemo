# -*- coding: utf-8 -*-
"""引擎「效果族」插件层（fx）· 2026-10-27

为什么存在
----------
未建模动作清单里 600+ 条属于**长尾机制**（信息类、规则改写、自我回收、能量搬移、
攻击限制、特殊能量……）。继续把它们堆进 `battle.py` 的 if/elif 长链，会变成：
  · 每次改动都动同一个三千行文件 → 多人协作必然冲突；
  · 未识别的 tag 没有强制报错点 → 容易静默忽略；
  · 单个族的语义与测试散落各处，无法独立验收。

本层把「一个机制族」做成**独立模块** `engine/families/fx_<name>.py`，由
`pkgutil` 自动发现（无需改注册表，避免共享文件写冲突）。

模块约定（全部可选，按需实现）
------------------------------
    PREFIXES = ("fx:foo:",)                  # 必填：本族负责的 tag 前缀（必须以 fx: 开头）
    def act(battle, tag, ctx):               # 一次性动作（招式/特性/训练家）
        return "可读描述"                     # 返回写入事件日志的 detail
    def static_dmg_plus(battle, attacker, defender):
        return 0                             # 静态加伤（「攻击 +N 伤害」类）
    def retreat_cost(battle, fp):            # 覆盖撤退费用（None = 不干预）
        return 0
    def attack_cost(battle, attacker, atk):  # 覆盖招式费用
        return {"ignore_all": True}          # 或 {"colorless": -1}
    def prize_delta(battle, taker, victim):  # 击倒取奖赏卡数修正
        return -1
    def blocked_play(battle, p, card):       # 静态禁令（不能打出某类卡）
        return True
    def ability_blocked(battle, fp, ab):     # 静态特性失效
        return True
    def audit_item(desc, effects):           # 对账（返回错误字符串列表）
        return []

纪律（不可放松）
----------------
1. **只认 fx: 前缀**：其它前缀一律不接管，防止误改既有语义。
2. **认不出的参数形态必须抛 ValueError**（绝不静默忽略/近似）。
3. 解析链侧（`agent/fx/fx_<name>.py`）必须产出与这里**逐字一致**的 tag；
   未注册的 fx tag 会被 `to_engine_tags` 记为 unsupported（见 effects.py）。
4. 每个族必须带**正例 + 负向守卫**用例（近邻写法必须仍被拒绝）。
"""
from __future__ import annotations

import importlib
import pkgutil
from typing import Optional

_MODULES: Optional[list] = None


def modules() -> list:
    """惰性发现并加载 `fx_*.py` 模块（只加载一次）"""
    global _MODULES
    if _MODULES is None:
        found = []
        for mi in pkgutil.iter_modules(__path__, __name__ + "."):
            short = mi.name.rsplit(".", 1)[-1]
            if not short.startswith("fx_"):
                continue
            try:
                found.append(importlib.import_module(mi.name))
            except Exception as exc:      # 加载失败必须炸掉，不能静默少一族
                raise RuntimeError(f"效果族模块加载失败：{mi.name}：{exc}") from exc
        _MODULES = found
    return _MODULES


def reload() -> None:
    """测试用：强制重新发现（新增族文件后调用）"""
    global _MODULES
    _MODULES = None


def known(tag) -> bool:
    """该 tag 是否已被某个已注册的族接管（解析链准入/派发共用）"""
    t = str(tag or "")
    if not t.startswith("fx:"):
        return False
    return any(t.startswith(p) for m in modules()
               for p in getattr(m, "PREFIXES", ()))


def module_for(tag):
    t = str(tag or "")
    for m in modules():
        if any(t.startswith(p) for p in getattr(m, "PREFIXES", ())):
            return m
    return None


def has_act(tag) -> bool:
    """该 fx tag 是否有可执行的一次性动作（若无 act 则为纯静态钩子，如 ability_blocked）"""
    m = module_for(tag)
    return m is not None and hasattr(m, "act")


def apply_action(battle, tag, ctx):
    """执行一次性 fx 动作；返回 detail 字符串。无实现则抛错（不静默）"""
    m = module_for(tag)
    if m is None or not hasattr(m, "act"):
        raise ValueError(f"未知 fx tag（无实现）：{tag}")
    return m.act(battle, tag, ctx)


def action_available(battle, tag, ctx=None) -> bool:
    """该 fx 动作此刻是否可用（用于合法动作表过滤；默认可用）"""
    m = module_for(tag)
    if m is None:
        return False
    fn = getattr(m, "available", None)
    return bool(fn(battle, tag, ctx)) if fn else True


def static_dmg_plus(battle, attacker, defender) -> int:
    extra = 0
    for m in modules():
        fn = getattr(m, "static_dmg_plus", None)
        if fn is not None:
            extra += int(fn(battle, attacker, defender) or 0)
    return extra


def static_dmg_minus(battle, defender, attacker=None) -> int:
    """守方受到的**全队常驻减伤**（≥0，在弱点/抗性之后扣减）"""
    extra = 0
    for m in modules():
        fn = getattr(m, "static_dmg_minus", None)
        if fn is not None:
            extra += int(fn(battle, defender, attacker) or 0)
    return extra


def retreat_cost(battle, fp) -> Optional[int]:
    best: Optional[int] = None
    for m in modules():
        fn = getattr(m, "retreat_cost", None)
        if fn is None:
            continue
        v = fn(battle, fp)
        if v is not None:
            v = int(v)
            best = v if best is None else min(best, v)
    return best


def attack_cost(battle, attacker, atk) -> dict:
    """费用修正的**聚合器**（各 fx 族各报一段，这里合并）

    ⚠ 必须把 ``set`` / ``omit`` 一并透传：它们是「**改写**费用」而不是
      「加减无色」。第一版只累加 ``colorless``，于是 `self_set` / `self_omit`
      在解析层看着正常、池子里也进了，但**运行时一点效果都没有** ——
      花 5 张卡（Kyurem / Azumarill / Decidueye / Conkeldurr / Crawdaunt）
      的代价换来 0 收益，且任何测试都不红。
    """
    out = {"colorless": 0, "ignore_all": False}
    for m in modules():
        fn = getattr(m, "attack_cost", None)
        if fn is None:
            continue
        r = fn(battle, attacker, atk) or {}
        out["colorless"] += int(r.get("colorless", 0) or 0)
        if r.get("ignore_all"):
            out["ignore_all"] = True
        if r.get("set") is not None:
            out["set"] = r["set"]
        if r.get("omit"):
            out["omit"] = r["omit"]
    return out


def prize_delta(battle, taker, victim) -> int:
    d = 0
    for m in modules():
        fn = getattr(m, "prize_delta", None)
        if fn is not None:
            d += int(fn(battle, taker, victim) or 0)
    return d


def blocked_play(battle, p, card) -> bool:
    for m in modules():
        fn = getattr(m, "blocked_play", None)
        if fn is not None and fn(battle, p, card):
            return True
    return False


def ability_blocked(battle, fp, ab) -> bool:
    for m in modules():
        fn = getattr(m, "ability_blocked", None)
        if fn is not None and fn(battle, fp, ab):
            return True
    return False


def effect_immune(battle, fp, source: str, kinds: str = "") -> bool:
    """``fp`` 是否免疫来自 ``source`` 的效果（S4：可限定训练家子类别）

    解析侧只产出可完整表达的形态；各族自己校验 tag，未知形态抛错。
    `kinds` 只有 `trainer` 来源用（`item` / `supporter`），其它来源忽略。
    """
    for m in modules():
        fn = getattr(m, "effect_immune", None)
        if fn is None:
            continue
        try:
            if fn(battle, fp, source, kinds):
                return True
        except TypeError:
            # 旧族只接受三个参数（没有来源子类别的概念）
            if fn(battle, fp, source):
                return True
    return False


def audit_item(card_name, item_name, desc, effects, tags) -> list:
    """所有族对本条目的**自定义对账**；没有 fx tag 时返回空

    只调用「effects 里出现了 fx tag」的族的 audit_item —— 各族的对账规则
    只对本族负责的卡面生效，避免互相误判。
    """
    errs: list = []
    fx_tags = [str(t) for t in (effects or ()) if str(t).startswith("fx:")]
    if not fx_tags:
        return errs
    for m in modules():
        fn = getattr(m, "audit_item", None)
        if fn is None:
            continue
        errs += list(fn(card_name, item_name, desc, effects, fx_tags) or [])
    return errs
