# -*- coding: utf-8 -*-
"""PokéLab AI · K2 卡牌效果解析链

把**中文效果文本**（`desc_zh`，自述转写）转写为结构化 spec，并映射到引擎可执行 tag。

设计要点（与 PRD K2 对齐）：
  - **可插拔后端**：`rule`（离线确定性基线，零依赖）· `llm`（OpenAI 兼容 /chat/completions，
    可接 DeepSeek 或本地部署）· `auto`（配了 `POKELAB_LLM_BASE`/`POKELAB_LLM_KEY` 走 llm，
    否则 rule）；两条后端产出**同一 Schema**，可直接对比准确率。
  - **诚实标注**：解析出的 spec 经 `to_engine_tags` 映射时，引擎未实现的动作类型会
    显式列出（`unsupported`），不假装支持。
  - **黄金集**：`data/k2_golden.json`（中文文本 → 期望字段），`evaluate()` 逐字段比对，
    由 `examples/run_k2.py` 产出 `artifacts/k2_report.{md,json}`。

指标口径（与 K2「卡牌效果解析准确率 ≥85%」对齐）：
  - `parse_rate`：能产出合法 Schema JSON 的比例；
  - `kind_acc` / `trigger_acc` / `action_set_acc` / `amount_acc` / `target_acc`：逐字段准确率；
  - `strict_acc`：**全部关键字段**一致（作为 K2 主指标）。
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parents[3]          # pokelab-ai
GOLDEN_FILE = Path(__file__).resolve().parents[1] / "data" / "k2_golden.json"
ART_DIR = REPO / "packages" / "agent" / "artifacts"

# 合规 6.1：AI/机器生成内容须显著标识（本模块所有解析结果均带此提示）
AI_NOTICE = ("本解析结果由机器/AI 自动生成，仅供研究参考，可能不完全准确；"
             "请以官方规则与卡面原文为准。")

# ---------------- Schema 常量 ----------------
KINDS = ("attack", "ability", "item", "supporter", "energy", "rule")
TRIGGERS = ("on_use", "on_knockout", "static", "on_attach", "on_evolve")
ACTION_TYPES = ("damage", "draw", "attach", "heal", "discard", "switch",
                "search", "evolve", "spread", "move_damage", "knockout",
                "status", "rule")
TARGETS = ("active_opponent", "self", "bench", "active_opponent_bench",
           "deck", "hand", "discard", "opponent_hand", "any")
COND_OPS = ("eq", "gt", "gte", "lt", "lte", "in", "contains")

# 引擎可执行 tag 映射（与 engine/data/cards_real.json 的 meta.engine_tags 对齐）
ENGINE_STATUS_TAGS = ("poison", "burn", "sleep", "paralyze", "confuse")


# ---------------- 工具 ----------------
def _num(text: str) -> Optional[int]:
    """中文数字/阿拉伯数字 → int（覆盖 0–20 与常见中文数字）"""
    if text is None:
        return None
    s = str(text).strip()
    if s.isdigit():
        return int(s)
    zh = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
          "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    if s in zh:
        return zh[s]
    m = re.match(r"^([一二两三四五六七八九])?十([一二三四五六七八九])?$", s)
    if m:
        tens = zh.get(m.group(1) or "一", 1)
        ones = zh.get(m.group(2) or "", 0)
        return tens * 10 + ones
    return None


def spec(kind: str, name: str, trigger: str, actions: list,
         conditions: Optional[list] = None, source_text: str = "",
         citations: Optional[list] = None) -> dict:
    """构造一条规范 spec（字段顺序与提示词 Schema 一致）"""
    return {
        "kind": kind,
        "name": name,
        "trigger": trigger,
        "conditions": conditions or [],
        "actions": actions,
        "source_text": source_text,
        "citations": citations or [],
    }


def act(type_: str, amount: Optional[int] = None, target: str = "self",
        extra: Optional[dict] = None) -> dict:
    d = {"type": type_, "amount": amount, "target": target}
    if extra:
        d["extra"] = extra
    return d


# ---------------- 规则后端（离线基线） ----------------
_STATUS_WORDS = (("中毒", "poison"), ("灼伤", "burn"), ("睡眠", "sleep"),
                 ("麻痹", "paralyze"), ("混乱", "confuse"))
_DAMAGE_RE = re.compile(r"(?:造成|受到)\s*(\d+)\s*点伤害")
_SPREAD_RE = re.compile(r"放置\s*(\d+)\s*个伤害指示物")
_DRAW_RE = re.compile(r"抽(?:至手牌)?\s*(\d+)\s*张?")
_DRAW_TO_RE = re.compile(r"抽至手牌\s*(\d+)\s*张")
_SEARCH_RE = re.compile(r"检视牌库顶\s*(\d+)\s*张取\s*(\d+)")
_SEARCH_ANY_RE = re.compile(
    r"(?:检索|从牌库|从弃牌堆)[^\d。；]{0,8}?(\d+)\s*张")
_DISCARD_RE = re.compile(r"弃(?:掉)?\s*(\d+)\s*张")
_DISCARD_ALL_HAND_RE = re.compile(r"弃(?:掉)?(?:全部)?手牌")
_DISCARD_SELF_RE = re.compile(r"弃此卡")
_MOVE_RE = re.compile(r"移动(?:至多)?\s*(\d+)\s*个")
_EVOLVE_RE = re.compile(r"进化")
_MODIFY_RE = re.compile(r"弱点|抗性|免疫|不能撤退|无法撤退")
_PROVIDE_RE = re.compile(r"提供\s*(\d+)\s*点?\s*(火|超|草|恶|水|雷|斗|钢)?能量")
_ACCEL_RE = re.compile(r"加速\s*(\d+)?\s*个?\s*能量")
_DISCARD_ENERGY_RE = re.compile(r"弃(?:掉)?[^。；]*?(\d+)\s*个能量")
_HEAL_RE = re.compile(r"回(?:复|收)\s*(\d+)\s*点")
_ATTACH_RE = re.compile(r"附\s*(\d+)?\s*个?\s*(火|超|草|恶|水|雷|斗|钢)?能量")
_PLACE_RE = re.compile(r"放(?:置)?\s*(\d+)\s*个?\s*(?:伤害)?指示物")


# ---------------- 规则后端：英文句式（真实卡池为英文卡面） ----------------
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

_EN_STATUS = (("asleep", "sleep"), ("burned", "burn"), ("confused", "confuse"),
              ("paralyzed", "paralyze"), ("poisoned", "poison"))
_EN_DAMAGE_RE = re.compile(r"does\s+(\d+)\s+damage", re.I)
_EN_SPREAD_RE = re.compile(r"put\s+(\d+)\s+damage\s+counters?", re.I)
_EN_DRAW_RE = re.compile(r"draw\s+(?:up to\s+)?(\d+)\s+cards?", re.I)
_EN_DISCARD_ENERGY_RE = re.compile(r"discard\s+(\d+)\s+(?:\w+\s+)?energ", re.I)
_EN_DISCARD_RE = re.compile(r"discard\s+(?:up to\s+)?(\d+)\s+cards?", re.I)
_EN_DISCARD_ALL_RE = re.compile(r"discard your (?:whole )?hand", re.I)
_EN_SEARCH_TOP_RE = re.compile(r"(?:look at|search)\s+the top\s+(\d+)\s+cards",
                               re.I)
_EN_SEARCH_RE = re.compile(r"search your deck for\s+(?:up to\s+)?(\d+)?", re.I)
_EN_SEARCH_ANY_RE = re.compile(r"search your deck for\s+(?:up to\s+)?(\d+)?"
                               r"[^.]*", re.I)
_EN_HEAL_RE = re.compile(r"heal\s+(\d+)\s+damage", re.I)
_EN_ATTACH_RE = re.compile(r"attach\s+(?:up to\s+)?(\d+)?\s*"
                           r"(?:\{[A-Za-z]+\}\s*)?(?:basic\s+)?"
                           r"(grass|fire|water|lightning|psychic|fighting|"
                           r"darkness|metal|dragon|colorless)?\s*energy", re.I)
_EN_MOVE_RE = re.compile(r"move\s+(?:up to\s+)?(\d+)\s+damage\s+counters?",
                         re.I)
_EN_SWITCH_RE = re.compile(r"switch\s+(?:your|out|it)|with\s+(?:your|1 of your)\s+"
                           r"benched|switch your active", re.I)
_EN_KNOCKOUT_RE = re.compile(r"(?:is|are|was|were)\s+knocked out|knocked out by",
                             re.I)
_EN_EVOLVE_RE = re.compile(r"\bevolve[sd]?\b|evolution", re.I)
_EN_MODIFY_RE = re.compile(r"weakness|resistance|prevents? all damage|"
                           r"can'?t retreat|cannot retreat|isn'?t affected",
                           re.I)
_EN_BENCH_RE = re.compile(r"bench(?:ed)?\s+pok|onto your bench", re.I)
_EN_HAND_RE = re.compile(r"(?:into|to)\s+your\s+hand", re.I)
_EN_OPP_RE = re.compile(r"opponent[\u2019']?s?\b", re.I)


def _parse_rule_en(text: str, kind: str = "attack", name: str = "") -> dict:
    """规则后端（英文卡面）：抽取 spec（伤害/指示物/抽弃/检索/回复/附能/状态/
    互换/击倒/静态规则 + 触发时机）"""
    t = str(text or "")
    actions: list = []
    conditions: list = []
    lower = t.lower()

    m = _EN_DAMAGE_RE.search(t)
    if m:
        actions.append(act("damage", int(m.group(1)), "active_opponent"))

    m = _EN_SPREAD_RE.search(t)
    if m:
        n = int(m.group(1))
        tgt = "active_opponent_bench" if _EN_BENCH_RE.search(t) else "active_opponent"
        extra = {"counters": n}
        if re.search(r"in any way you like|as you like|anyway you like", t, re.I):
            extra["free_distribute"] = True
        actions.append(act("spread", n * 10, tgt, extra))

    m = _EN_MOVE_RE.search(t)
    if m:
        tgt = "self" if re.search(r"your (?:own )?pok", t, re.I) else "active_opponent"
        actions.append(act("move_damage", int(m.group(1)), tgt))

    m = _EN_HEAL_RE.search(t)
    if m:
        actions.append(act("heal", int(m.group(1)), "self"))

    m = _EN_DRAW_RE.search(t)
    if m:
        actions.append(act("draw", int(m.group(1)), "deck"))
    elif re.search(r"draw (?:a|1) card", t, re.I):
        actions.append(act("draw", 1, "deck"))

    if _EN_DISCARD_ALL_RE.search(t):
        actions.append(act("discard", None, "hand", {"all": True}))
    elif _EN_DISCARD_ENERGY_RE.search(t):
        n = int(_EN_DISCARD_ENERGY_RE.search(t).group(1))
        tgt = "active_opponent" if _EN_OPP_RE.search(t) else "self"
        actions.append(act("discard", n, tgt, {"card_type": "energy"}))
    elif _EN_DISCARD_RE.search(t):
        n = int(_EN_DISCARD_RE.search(t).group(1))
        tgt = "opponent_hand" if ("opponent" in lower and "hand" in lower) else "hand"
        actions.append(act("discard", n, tgt))

    m = _EN_SEARCH_TOP_RE.search(t)
    if m:
        actions.append(act("search", 1, "deck", {"look_top": int(m.group(1))}))
    elif _EN_SEARCH_RE.search(t):
        g = _EN_SEARCH_RE.search(t).group(1)
        tgt = "discard" if "discard pile" in lower else "deck"
        actions.append(act("search", int(g) if g else 1, tgt))

    m = _EN_ATTACH_RE.search(t)
    if m and "energy" in lower:
        amt = int(m.group(1)) if m.group(1) else 1
        et = (m.group(2) or "").lower() or None
        actions.append(act("attach", amt, "self",
                           {"energy_type": et} if et else None))

    for word, tag in _EN_STATUS:
        if word in lower:
            tgt = "active_opponent" if _EN_OPP_RE.search(t) else "self"
            actions.append(act("status", None, tgt, {"status": tag}))
            break

    if _EN_SWITCH_RE.search(t):
        mine = bool(re.search(r"your|switch out", t, re.I)) and \
            not _EN_OPP_RE.search(t)
        actions.append(act("switch", None,
                           "self" if mine else "active_opponent_bench"))

    if _EN_KNOCKOUT_RE.search(t) and not re.search(r"if .{0,20}knocked out",
                                                   lower):
        actions.append(act("knockout", None, "active_opponent"))

    if _EN_EVOLVE_RE.search(t) and re.search(
            r"evolve (?:it|this|your|1 of|a )", t, re.I):
        actions.append(act("evolve", None, "self"))

    if _EN_MODIFY_RE.search(t):
        actions.append(act("rule", None, "any",
                           {"note": "静态规则修改（弱点/抗性/伤害减免/限制）"}))

    # 触发时机（英文）
    if re.search(r"if .{0,30}(?:is|are) knocked out", lower) or \
            re.search(r"when .{0,30}knocked out", lower):
        trigger = "on_knockout"
    elif re.search(r"when you play .{0,40}to evolve", lower) or \
            re.search(r"when you evolve", lower):
        trigger = "on_evolve"
    elif re.search(r"once during your turn|once per turn", lower):
        trigger = "static"
    elif kind in ("attack", "item", "supporter", "energy"):
        trigger = "on_use"
    elif _EN_MODIFY_RE.search(t) or "as long as" in lower:
        trigger = "static"
    else:
        trigger = "on_use"

    if trigger == "static" and re.search(
            r"once during your turn|once per turn", lower):
        conditions.append({"field": "limit", "op": "eq",
                           "value": "once_per_turn"})

    if not actions:
        actions.append(act("rule", None, "any", {"note": "静态/未建模（英文）"}))

    return {"kind": kind, "name": name, "trigger": trigger,
            "conditions": conditions, "actions": actions,
            "source_text": t.strip()[:200]}


def _trigger_of(text: str, kind: str) -> str:
    """触发时机判定顺序：被击倒/进化时 → 使用类卡 → 招式 → 静态规则 → 默认"""
    if "被击倒" in text or "气绝" in text:
        return "on_knockout"
    if "进化时" in text:
        return "on_evolve"
    if kind in ("item", "supporter", "energy"):
        return "on_use"
    if kind == "attack":
        return "on_use"
    if re.search(r"每回合(?:一次|只能|仅能)", text) or "常驻" in text \
            or "只要" in text or "所有" in text:
        return "static"
    if re.search(r"弱点|抗性|免疫", text):
        return "static"
    if kind == "rule":
        return "static"
    if "附" in text and "能量" in text and kind == "energy":
        return "on_attach"
    return "on_use"


def parse_rule(text: str, kind: str = "attack", name: str = "",
               name_zh: str = "") -> dict:
    """规则后端：从中文效果文本抽取 spec（确定性、可离线、零依赖）

    覆盖「造成 N 点伤害 / 放置 N 个伤害指示物（可任意分配）/ 抽 N 张 /
    弃 N 张·弃 N 个能量 / 回复 N 点 / 附 N 个能量 / 检视牌库顶 N 取 M /
    检索·从弃牌堆取 N / 互换·换下 / 状态（中毒·灼伤·睡眠·麻痹·混乱）/
    击倒 / 每回合一次」等句式。

    **语言分流**：文本不含中日韩字符时走英文卡面路径（``_parse_rule_en``），
    因为真实卡池（challenge 数据集）为英文卡面。
    """
    if text and not _CJK_RE.search(str(text)):
        return _parse_rule_en(text, kind=kind, name=name)
    t = str(text or "").strip()
    # 剔除括号内注解（如「（引擎待实现）」「（合计 60 点）」），它们不是卡面效果
    t_clean = re.sub(r"[（(][^）)]*[）)]", "", t)
    t_norm = t_clean.replace(" ", "").replace("　", "")
    actions: list = []
    conditions: list = []

    # 主伤害
    m = _DAMAGE_RE.search(t_norm)
    if m:
        actions.append(act("damage", int(m.group(1)), "active_opponent"))

    # 伤害指示物（含任意分配）
    m = _SPREAD_RE.search(t_norm)
    if m:
        n = int(m.group(1))
        free = "任意" in t_norm or "随意" in t_norm
        tgt = "active_opponent_bench" if ("后备" in t_norm or "任意" in t_norm) \
            else "active_opponent"
        extra = {"free_distribute": True, "counters": n} if free else {"counters": n}
        actions.append(act("spread", n * 10, tgt, extra))
    elif "伤害指示物" in t_norm or "指示物" in t_norm:
        m2 = _PLACE_RE.search(t_norm)
        if m2:
            n = int(m2.group(1))
            actions.append(act("spread", n * 10, "active_opponent_bench",
                               {"counters": n}))

    # 抽牌
    if "抽" in t_norm:
        m = _DRAW_TO_RE.search(t_norm)
        if m:
            actions.append(act("draw", None, "hand",
                               {"to_hand_size": int(m.group(1))}))
        else:
            m = _DRAW_RE.search(t_norm)
            if m:
                actions.append(act("draw", int(m.group(1)), "deck"))
            elif "抽" in t_norm:
                actions.append(act("draw", 1, "deck"))

    # 附能量（先判，便于抑制同一句里的 search 重复计数）
    m = _ATTACH_RE.search(t_norm)
    has_attach = bool(m) and "能量" in t_norm and ("附" in t_norm)
    if not has_attach:
        m = _PROVIDE_RE.search(t_norm) or _ACCEL_RE.search(t_norm)
        has_attach = bool(m)

    # 检索（牌库顶检视 / 通用检索；从弃牌堆 → target=discard）
    if not has_attach:
        m = _SEARCH_RE.search(t_norm)
        if m:
            actions.append(act("search", int(m.group(2)), "deck",
                               {"look_top": int(m.group(1))}))
        elif "检视" in t_norm and "牌库" in t_norm:
            m2 = re.search(r"检视牌库顶\s*(\d+)", t_norm)
            actions.append(act("search", 1, "deck",
                               {"look_top": int(m2.group(1)) if m2 else None}))
        elif re.search(r"检索|从牌库|从弃牌堆", t_norm):
            m2 = _SEARCH_ANY_RE.search(t_norm)
            amt = _num(m2.group(1)) if (m2 and m2.group(1)) else 1
            tgt = "discard" if "从弃牌堆" in t_norm else "deck"
            actions.append(act("search", amt, tgt))

    # 弃牌 / 弃能量
    if _DISCARD_SELF_RE.search(t_norm):
        actions.append(act("discard", 1, "hand", {"card": "self"}))
    m = _DISCARD_ENERGY_RE.search(t_norm)
    if m:
        tgt = "active_opponent" if "对手" in t_norm else "self"
        actions.append(act("discard", int(m.group(1)), tgt,
                           {"card_type": "energy"}))
    elif _DISCARD_ALL_HAND_RE.search(t_norm):
        actions.append(act("discard", None, "hand", {"all": True}))
    else:
        m = _DISCARD_RE.search(t_norm)
        if m:
            tgt = "opponent_hand" if "对手" in t_norm and "手牌" in t_norm else "hand"
            actions.append(act("discard", int(m.group(1)), tgt))

    # 回复
    m = _HEAL_RE.search(t_norm)
    if m:
        actions.append(act("heal", int(m.group(1)), "self"))

    # 附能量（附 N 个X能量 / 提供 N 点X能量 / 加速 N 个能量）
    if has_attach and not any(a["type"] == "attach" and a.get("extra", {}).get(
            "energy_type") for a in actions):
        mm = _ATTACH_RE.search(t_norm)
        amt = None
        etype = None
        if mm:
            amt = int(mm.group(1)) if mm.group(1) else 1
            etype = _ENERGY_ZH.get(mm.group(2) or "", None)
        else:
            mp = _PROVIDE_RE.search(t_norm)
            if mp:
                amt = int(mp.group(1))
                etype = _ENERGY_ZH.get(mp.group(2) or "", None)
            else:
                ma = _ACCEL_RE.search(t_norm)
                amt = int(ma.group(1)) if (ma and ma.group(1)) else None
        extra = {"energy_type": etype} if etype else None
        actions.append(act("attach", amt, "self", extra))

    # 状态
    for word, tag in _STATUS_WORDS:
        if word in t_norm:
            # 「此宝可梦随后陷入混乱」→ 自身；否则对手战斗宝可梦
            tgt = "self" if ("此宝可梦" in t_norm or "自身" in t_norm
                             or "自己" in t_norm) else "active_opponent"
            actions.append(act("status", None, tgt, {"status": tag}))

    # 互换 / 换下（自己 → self；对手/未指明 → active_opponent_bench）
    if "互换" in t_norm or "换下" in t_norm or "交替" in t_norm:
        mine = any(k in t_norm for k in ("自己", "己方", "我方", "自身的"))
        actions.append(act("switch", None,
                           "self" if mine else "active_opponent_bench"))

    # 移动伤害指示物
    m = _MOVE_RE.search(t_norm)
    if m and "指示物" in t_norm:
        tgt = "self" if any(k in t_norm for k in ("自己", "己方", "我方")) \
            else "active_opponent"
        actions.append(act("move_damage", int(m.group(1)), tgt))

    # 进化（仅「施动」语境；「进化时」属触发时机，不产出动作）
    if "进化" in t_norm and not re.search(r"进化时|进化后", t_norm):
        if re.search(r"直接进化|使其进化|进化自己|进化己方|进化我方|进化自己的", t_norm):
            actions.append(act("evolve", None, "self"))

    # 「附着…宝可梦」句式（能量词可能出现在前面，如「检索 2 张基础能量并附着到…」）
    if "附着" in t_norm and "能量" in t_norm and \
            not any(a["type"] == "attach" for a in actions):
        actions.append(act("attach", None, "self"))

    # 静态规则修改（弱点/抗性/免疫/禁止撤退）
    if _MODIFY_RE.search(t_norm):
        actions.append(act("rule", None, "any",
                           {"note": "静态规则修改（弱点/抗性/限制）"}))

    # 击倒（主动造成；「被击倒」属触发时机，不产出动作）
    if "被击倒" not in t_norm and (re.search(r"(?:造成|使其|将其|直接)击倒", t_norm)
                                   or "气绝" in t_norm):
        actions.append(act("knockout", None, "active_opponent"))

    # 每回合一次
    if re.search(r"每回合(?:一次|只能|仅能)", t_norm):
        conditions.append({"field": "limit", "op": "eq",
                           "value": "once_per_turn"})

    # 触发时机
    trigger = _trigger_of(t_norm, kind)

    # 无显式动作但属于常驻/规则类：给一条 rule 动作便于统计
    if not actions and kind in ("rule", "ability"):
        actions.append(act("rule", None, "any", {"note": "静态/未建模"}))

    return spec(kind, name or name_zh or "", trigger, actions,
                conditions, source_text=t)


_ENERGY_ZH = {"火": "fire", "超": "psychic", "草": "grass", "恶": "darkness",
              "水": "water", "雷": "lightning", "斗": "fighting",
              "钢": "metal", "无": "colorless"}


# ---------------- LLM 后端（OpenAI 兼容） ----------------
class LLMConfigError(RuntimeError):
    pass


def llm_config() -> dict:
    """从环境变量读 LLM 配置（未配置 → 抛错，由 auto 回退 rule）

    POKELAB_LLM_BASE 例：https://api.deepseek.com/v1  或  http://127.0.0.1:9997/v1
    POKELAB_LLM_KEY  API Key（本地服务可留空）
    POKELAB_LLM_MODEL 模型名（默认 deepseek-chat）
    """
    base = os.environ.get("POKELAB_LLM_BASE", "").rstrip("/")
    if not base:
        raise LLMConfigError("未配置 POKELAB_LLM_BASE（LLM 后端不可用）")
    return {"base": base,
            "key": os.environ.get("POKELAB_LLM_KEY", ""),
            "model": os.environ.get("POKELAB_LLM_MODEL", "deepseek-chat"),
            "timeout": float(os.environ.get("POKELAB_LLM_TIMEOUT", "60"))}


def extract_json(text: str) -> Optional[list]:
    """从模型输出里稳健提取 JSON 数组（容忍围栏/前后缀/thinking 残留）"""
    if not text:
        return None
    s = str(text)
    s = re.sub(r"```(?:json)?", "", s)
    i, k = s.find("["), s.rfind("]")
    if i == -1 or k == -1 or k <= i:
        return None
    raw = s[i:k + 1]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, list) else None


def parse_llm(text: str, kind: str = "attack", name: str = "",
              retrieved_rules: str = "", meta_out: Optional[dict] = None,
              extra_sensitive: Optional[dict] = None) -> list:
    """LLM 后端：按 prompts/parse_effects_*.md 模板调用 OpenAI 兼容接口

    合规 6.2：调用前先**脱敏**（``agent.privacy.desensitize``），并校验端点是否被
    本地模式允许（``POKELAB_LOCAL_ONLY=1`` → 禁止外部调用）；每次调用写**审计记录**
    （仅端点/模型/字符数/脱敏计数，**不含原文**）。

    ``meta_out``：可选 dict，原地回填 ``{endpoint, model, privacy, audit}``。
    """
    from . import prompts as _prompts
    from . import privacy as _privacy
    cfg = llm_config()
    # 1) 端点校验（本地模式下外部端点直接拒绝）
    ep = _privacy.check_endpoint(cfg["base"])
    # 2) 渲染提示词 → 脱敏后再外发
    sys_t = _prompts.render("parse_effects_system",
                            retrieved_rules=retrieved_rules or "（无）")
    usr_t = _prompts.render("parse_effects_user", card_name=name or "(未命名)",
                            card_kind=kind, rules_text=text)
    usr_clean, rep = _privacy.desensitize(usr_t, extra=extra_sensitive)
    sys_clean, rep2 = _privacy.desensitize(sys_t)
    rep["total"] += rep2["total"]
    body = json.dumps({
        "model": cfg["model"],
        "messages": [{"role": "system", "content": sys_clean},
                     {"role": "user", "content": usr_clean}],
        "temperature": 0,
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        cfg["base"] + "/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {cfg['key']}"} if cfg["key"] else {})})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=cfg["timeout"]) as r:
            payload = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        _privacy.audit("llm_call", host=ep["host"], model=cfg["model"], ok=False,
                       error=type(e).__name__,
                       elapsed_ms=int((time.time() - t0) * 1000))
        raise LLMConfigError(f"LLM 请求失败：{e}") from e
    content = (payload.get("choices") or [{}])[0].get("message", {}).get("content", "")
    # 3) 审计（不记录原文）
    _privacy.audit("llm_call", host=ep["host"], external=ep["external"],
                   model=cfg["model"], ok=True,
                   prompt_chars=rep["chars_in"], sent_chars=rep["chars_out"],
                   desensitized=rep["total"], response_chars=len(content or ""),
                   elapsed_ms=int((time.time() - t0) * 1000))
    if meta_out is not None:
        meta_out.update({"endpoint": ep, "model": cfg["model"],
                         "privacy": rep, "audit": True})
    return extract_json(content) or []


def parse_effects(text: str, kind: str = "attack", name: str = "",
                  backend: str = "rule", retrieved_rules: str = "") -> dict:
    """统一入口：返回 {backend, specs[], errors[], raw, ai_generated, ai_notice …}

    backend: `rule` · `llm` · `auto`（有 LLM 配置走 llm，失败自动回退 rule）

    合规 6.1：结果一律带 ``ai_generated=True`` 与 ``ai_notice``（显著标识）；
    合规 6.2：``llm`` 路径下附 ``privacy``（脱敏计数）与 ``endpoint``（审计信息）。
    """
    used = backend
    meta: dict = {}
    if backend == "auto":
        try:
            llm_config()
            used = "llm"
        except LLMConfigError:
            used = "rule"
    try:
        if used == "llm":
            raw = parse_llm(text, kind=kind, name=name,
                            retrieved_rules=retrieved_rules, meta_out=meta)
            specs = [s for s in raw if isinstance(s, dict)] or \
                [parse_rule(text, kind=kind, name=name)]
        else:
            specs = [parse_rule(text, kind=kind, name=name)]
            raw = specs
    except LLMConfigError as e:
        used, raw = "rule(fallback)", [parse_rule(text, kind=kind, name=name)]
        specs = raw
        return {"backend": used, "specs": specs, "raw": raw,
                "errors": [str(e)], "generated_by": used,
                "ai_generated": True, "ai_notice": AI_NOTICE,
                "model": None, "endpoint": None, "privacy": None}
    except Exception as e:                                   # noqa: BLE001
        # 合规 6.2：本地模式拒绝外部端点（PrivacyError）等隐私策略拦截
        # → 不抛出，回退规则基线并**如实报告**，
        from .privacy import PrivacyError
        if not isinstance(e, PrivacyError):
            raise
        used, raw = "rule(privacy-blocked)", [parse_rule(text, kind=kind,
                                                         name=name)]
        specs = raw
        return {"backend": used, "specs": specs, "raw": raw,
                "errors": [str(e)], "generated_by": used,
                "ai_generated": True, "ai_notice": AI_NOTICE,
                "model": None, "endpoint": None, "privacy": None}
    errs = validate_specs(specs)
    return {"backend": used, "specs": specs, "raw": raw, "errors": errs,
            "generated_by": used, "ai_generated": True, "ai_notice": AI_NOTICE,
            "model": meta.get("model"), "endpoint": meta.get("endpoint"),
            "privacy": meta.get("privacy")}


# ---------------- 校验 ----------------
def validate_specs(specs: list) -> list:
    """Schema 级校验（字段名/枚举/数值单位）→ 错误信息列表"""
    errs: list = []
    if not isinstance(specs, list):
        return ["输出不是数组"]
    for i, s in enumerate(specs):
        if not isinstance(s, dict):
            errs.append(f"[{i}] 不是对象")
            continue
        for k in ("kind", "name", "trigger", "conditions", "actions",
                  "source_text"):
            if k not in s:
                errs.append(f"[{i}] 缺字段 {k}")
        if s.get("kind") not in KINDS:
            errs.append(f"[{i}] kind 非法：{s.get('kind')}")
        if s.get("trigger") not in TRIGGERS:
            errs.append(f"[{i}] trigger 非法：{s.get('trigger')}")
        if not isinstance(s.get("conditions"), list):
            errs.append(f"[{i}] conditions 应为数组")
        else:
            for c in s["conditions"]:
                if not isinstance(c, dict):
                    errs.append(f"[{i}] condition 不是对象")
                    continue
                if c.get("op") and c["op"] not in COND_OPS:
                    errs.append(f"[{i}] condition.op 非法：{c['op']}")
        if not isinstance(s.get("actions"), list):
            errs.append(f"[{i}] actions 应为数组")
            continue
        for a in s["actions"]:
            if not isinstance(a, dict):
                errs.append(f"[{i}] action 不是对象")
                continue
            if a.get("type") not in ACTION_TYPES:
                errs.append(f"[{i}] action.type 非法：{a.get('type')}")
            if a.get("target") not in TARGETS:
                errs.append(f"[{i}] action.target 非法：{a.get('target')}")
            amt = a.get("amount")
            if amt is not None and not isinstance(amt, int):
                errs.append(f"[{i}] action.amount 应为整数（HP 计）：{amt!r}")
    return errs


# ---------------- spec → 引擎 tag ----------------
def to_engine_tags(specs: list) -> dict:
    """把 spec 映射为引擎可执行 tag；返回 {tags[], unsupported[], notes[]}

    引擎支持（见 cards_real.json meta.engine_tags）：
      attack: 状态 tag（poison/burn/sleep/paralyze/confuse）· spread_free:N
      ability: recon:N · cursed:N
    """
    tags: list = []
    unsupported: list = []
    notes: list = []
    for s in specs or []:
        kind = s.get("kind")
        for a in s.get("actions") or []:
            t = a.get("type")
            tgt = a.get("target")
            amt = a.get("amount")
            extra = a.get("extra") or {}
            if t == "damage":
                notes.append("damage 由招式 damage 字段承载，无需 tag")
            elif t == "status":
                st = extra.get("status")
                if st in ENGINE_STATUS_TAGS:
                    tags.append(st)
                else:
                    unsupported.append({"type": t, "reason": f"未知状态 {st}"})
            elif t == "spread":
                if extra.get("free_distribute") and amt is not None:
                    tags.append(f"spread_free:{int(amt)}")
                else:
                    unsupported.append(
                        {"type": t, "reason": "仅支持任意分配的自由式（spread_free:N）"})
            elif t == "draw":
                if kind == "ability":
                    unsupported.append({"type": t, "reason": "特性抽牌未建 tag（可用 recon:N 近似）"})
                else:
                    tags.append(f"draw:{amt if amt is not None else 1}")
            elif t == "heal" and amt is not None:
                tags.append(f"heal:{int(amt)}")
            elif t == "search":
                if kind == "ability":
                    tags.append(f"recon:{amt if amt is not None else 1}")
                else:
                    unsupported.append({"type": t, "reason": "训练家检索仅支持预设 tag"})
            else:
                unsupported.append({"type": t, "reason": "引擎未建模该动作类型"})
    # 按出现顺序去重
    seen, uniq = set(), []
    for x in tags:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return {"tags": uniq, "unsupported": unsupported, "notes": notes}


# ---------------- 黄金集与评测 ----------------
def load_golden(path: Optional[str] = None) -> dict:
    p = Path(path) if path else GOLDEN_FILE
    return json.loads(p.read_text(encoding="utf-8"))


def _action_type_multiset(actions: list) -> list:
    return sorted(str(a.get("type")) for a in actions or [])


def _amounts(spec: dict) -> dict:
    """按动作类型聚合 amount（同类型多动作时取多重集）"""
    out: dict = {}
    for a in spec.get("actions") or []:
        out.setdefault(a.get("type"), []).append(a.get("amount"))
    return {k: sorted(v, key=lambda x: (x is None, x)) for k, v in out.items()}


def _targets(spec: dict) -> dict:
    out: dict = {}
    for a in spec.get("actions") or []:
        out.setdefault(a.get("type"), []).append(a.get("target"))
    return {k: sorted(v, key=lambda x: str(x)) for k, v in out.items()}


def _limit_of(spec: dict) -> Optional[str]:
    for c in spec.get("conditions") or []:
        if c.get("field") == "limit":
            return str(c.get("value"))
    return None


def compare(case: dict, spec: Optional[dict]) -> dict:
    """逐字段比对单条用例（expect 只声明关心的字段，未声明则跳过）"""
    exp = case.get("expect") or {}
    got = spec or {}
    r = {"case": case.get("id"), "kind": None, "trigger": None,
         "action_set": None, "amount": None, "target": None, "limit": None,
         "diffs": []}
    if not spec:
        r["diffs"].append("未产出 spec（解析失败）")
        for k in ("kind", "trigger", "action_set", "amount", "target", "limit"):
            r[k] = False
        return r

    def _cmp(field, e, g):
        if e is None:
            r[field] = None            # 未声明 → 不计入
            return
        ok = e == g
        r[field] = ok
        if not ok:
            r["diffs"].append(f"{field}: 期望 {e!r} ≠ 实际 {g!r}")

    _cmp("kind", exp.get("kind"), got.get("kind"))
    _cmp("trigger", exp.get("trigger"), got.get("trigger"))
    if exp.get("actions") is not None:
        _cmp("action_set", _action_type_multiset(exp.get("actions")),
             _action_type_multiset(got.get("actions")))
        _cmp("amount", _amounts(exp), _amounts(got))
        _cmp("target", _targets(exp), _targets(got))
    if "limit" in exp:
        _cmp("limit", exp.get("limit"), _limit_of(got))
    return r


def evaluate(backend: str = "rule", golden: Optional[dict] = None,
             path: Optional[str] = None) -> dict:
    """在黄金集上评测指定后端，返回指标 + 逐例明细

    指标：parse_rate / kind_acc / trigger_acc / action_set_acc / amount_acc /
    target_acc / strict_acc（全部已声明字段均一致）。
    """
    data = golden or load_golden(path)
    cases = data.get("cases") or []
    rows: list = []
    backend_used = None
    parse_ok = 0
    for c in cases:
        res = parse_effects(c.get("text", ""), kind=c.get("kind", "attack"),
                            name=c.get("name", ""), backend=backend)
        backend_used = res.get("backend") or backend_used
        specs = res.get("specs") or []
        spec0 = specs[0] if specs else None
        schema_errs = res.get("errors") or []
        if spec0 and not schema_errs:
            parse_ok += 1
        row = compare(c, spec0)
        row["schema_errors"] = schema_errs
        row["engine_tags"] = to_engine_tags(specs) if specs else {"tags": [],
                                                                  "unsupported": []}
        rows.append(row)

    def acc(field: str) -> Optional[float]:
        vals = [r[field] for r in rows if r[field] is not None]
        return round(sum(1 for v in vals if v) / len(vals), 4) if vals else None

    strict_vals = []
    for r in rows:
        fields = [r[k] for k in ("kind", "trigger", "action_set", "amount",
                                 "target", "limit") if r[k] is not None]
        strict_vals.append(all(fields) if fields else False)

    # 分组统计：seen（开发期覆盖句式） vs holdout（留出句式，观测泛化）
    def _strict_of(rs):
        vals = []
        for r in rs:
            fields = [r[k] for k in ("kind", "trigger", "action_set", "amount",
                                     "target", "limit") if r[k] is not None]
            vals.append(all(fields) if fields else False)
        return round(sum(1 for x in vals if x) / len(vals), 4) if vals else None

    seen_rows = [r for r, c in zip(rows, cases) if not c.get("holdout")]
    hold_rows = [r for r, c in zip(rows, cases) if c.get("holdout")]
    n = len(rows)
    unsupported: dict = {}
    for r in rows:
        for u in (r.get("engine_tags") or {}).get("unsupported") or []:
            unsupported[u["type"]] = unsupported.get(u["type"], 0) + 1

    return {
        "backend": backend_used or backend,
        "n": n,
        "parse_rate": round(parse_ok / n, 4) if n else None,
        "kind_acc": acc("kind"),
        "trigger_acc": acc("trigger"),
        "action_set_acc": acc("action_set"),
        "amount_acc": acc("amount"),
        "target_acc": acc("target"),
        "limit_acc": acc("limit"),
        "strict_acc": round(sum(1 for x in strict_vals if x) / n, 4) if n else None,
        "seen_n": len(seen_rows), "seen_strict_acc": _strict_of(seen_rows),
        "holdout_n": len(hold_rows), "holdout_strict_acc": _strict_of(hold_rows),
        "unsupported_action_types": unsupported,
        "rows": rows,
    }


def parse_card_pool(backend: str = "rule") -> dict:
    """对**真实卡池**的全部招式/特性 desc_zh 跑解析（K2 的真实语料口径）"""
    import sys
    eng = REPO / "packages" / "engine"
    if str(eng) not in sys.path:
        sys.path.insert(0, str(eng))
    from engine import reallib                     # noqa: E402
    items: list = []
    for name, item in reallib.catalog().items():
        d = item["def"]
        kind = {"pokemon": None}.get(item.get("kind"), None)
        for a in getattr(d, "attacks", ()) or ():
            if a.display_desc:
                items.append({"kind": "attack", "name": a.name,
                              "name_zh": a.name_zh, "text": a.display_desc})
        for ab in getattr(d, "abilities", ()) or ():
            if ab.display_desc:
                items.append({"kind": "ability", "name": ab.name,
                              "name_zh": ab.name_zh, "text": ab.display_desc})
    out = []
    for it in items:
        res = parse_effects(it["text"], kind=it["kind"], name=it["name"],
                            backend=backend)
        tags = to_engine_tags(res["specs"]) if res["specs"] else {
            "tags": [], "unsupported": []}
        out.append({**it, "backend": res["backend"],
                    "spec": res["specs"][0] if res["specs"] else None,
                    "errors": res["errors"], **tags})
    supported = sum(1 for x in out if not x["unsupported"])
    return {"n": len(out), "items": out,
            "fully_supported": supported,
            "support_rate": round(supported / len(out), 4) if out else None}
