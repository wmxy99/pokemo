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
                "status", "rule", "recover",
                # v1.4 修正器类（B4 批次）
                "coin_flip", "damage_mod", "shield", "passive")
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
_ATTACH_RE = re.compile(r"附(?:着)?\s*(\d+)?\s*[张枚个]?\s*(?:基本)?\s*"
                        r"(火|超|草|恶|水|雷|斗|钢)?\s*能量")
# v1.3：「从弃牌堆/牌库取 N 张（基本X）能量」（能量词在动词之后）
_DISCARD_ATTACH_RE = re.compile(
    r"从(?:弃牌堆|牌库|手牌)[^。；]{0,16}?(\d+)\s*[张枚个]?\s*"
    r"(?:基本)?\s*(火|超|草|恶|水|雷|斗|钢)?\s*能量")
# v1.3：弃牌堆回收（Put … from your discard pile into your hand）
_RECOVER_RE = re.compile(r"(?:取|回收|放入?)[^。；]{0,12}?(\d+)?\s*[张枚个]?\s*"
                         r"(?:宝可梦|能量)?[^。；]{0,8}?"
                         r"(?:弃牌堆)[^。；]{0,12}?(?:入手|手牌)")
_RECOVER_RE2 = re.compile(r"从[^。；，,]{0,8}?弃牌堆[^。；]{0,14}?(?:至多|最多)?\s*"
                          r"(\d+)\s*[张枚个]?"
                          r"[^。；]{0,12}?(?:入手|加入手牌|加入自己的手牌|拿)")
# v1.3：弱点改写（对手X系宝可梦弱点变为Y）
_WEAK_CHANGE_RE = re.compile(r"弱点(?:变|改)为\s*([火超草恶水雷斗钢无])")
_PLACE_RE = re.compile(r"放(?:置)?\s*(\d+)\s*个?\s*(?:伤害)?指示物")


# ---------------- 规则后端：英文句式（真实卡池为英文卡面） ----------------
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

_EN_STATUS = (("asleep", "sleep"), ("burned", "burn"), ("confused", "confuse"),
              ("paralyzed", "paralyze"), ("poisoned", "poison"))
_EN_DAMAGE_RE = re.compile(r"does\s+(\d+)\s+damage", re.I)
_EN_SPREAD_RE = re.compile(r"put\s+(\d+)\s+damage\s+counters?", re.I)
# v1.3：指示物放在**自身**（Self-spread，需新引擎机制 → 如实标注不支持）
_EN_SELF_SPREAD_RE = re.compile(
    r"(?:put|place)\s+\d+\s+damage\s+counters?\s+(?:on|onto)\s+"
    r"(?:this|your active)\s+pok", re.I)
_EN_DRAW_RE = re.compile(r"draw\s+(?:up to\s+)?(\d+)\s+cards?", re.I)
# v1.3：抽到指定手牌数（Draw cards until you have N cards in your hand）
_EN_DRAW_TO_RE = re.compile(r"draw cards until you have (\d+) cards?", re.I)
_EN_DISCARD_ENERGY_RE = re.compile(r"discard\s+(\d+)\s+(?:\w+\s+)?energ", re.I)
# v1.3：升级版能量弃置句式（up to / all / from your hand）
_EN_DISCARD_UPTO_ENERGY_RE = re.compile(
    r"discard\s+(?:up to\s+)?(\d+)\s+(?:basic\s+)?energy", re.I)
_EN_DISCARD_ALL_ENERGY_RE = re.compile(r"discard all energy", re.I)
_EN_DISCARD_RE = re.compile(r"discard\s+(?:up to\s+)?(\d+)\s+cards?", re.I)
_EN_DISCARD_ALL_RE = re.compile(r"discard your (?:whole )?hand", re.I)
_EN_SEARCH_TOP_RE = re.compile(r"(?:look at|search)\s+the top\s+(\d+)\s+cards",
                               re.I)
# v1.3：单数形式（Look at the top card of your deck）
_EN_LOOK_TOP_ONE_RE = re.compile(r"look at the top card\b", re.I)
_EN_SEARCH_RE = re.compile(r"search your deck for\s+(?:up to\s+)?(\d+)?", re.I)
_EN_SEARCH_ANY_RE = re.compile(r"search your deck for\s+(?:up to\s+)?(\d+)?"
                               r"[^.]*", re.I)
_EN_HEAL_RE = re.compile(r"heal\s+(\d+)\s+damage", re.I)
# v1.3：附着句式（含 "Attach a Basic {G} Energy" / "attach up to 2 Energy"）
_EN_ATTACH_RE = re.compile(
    r"attach\s+(?:up to\s+)?(?:\d+\s+)?(?:a\s+|an\s+)?(?:basic\s*)?"
    r"(?:\{\s*([A-Za-z])\s*\}|"
    r"\b(grass|fire|water|lightning|psychic|fighting|darkness|metal|dragon|"
    r"colorless)\b)?\s*energ", re.I)
_EN_ATTACH_AMT_RE = re.compile(r"attach\s+(?:up to\s+)?(\d+)", re.I)
# v1.3：弃牌堆回收（Put up to N … from your discard pile into your hand）
_EN_RECOVER_RE = re.compile(
    r"put\s+(?:up to\s+)?(\d+)?\s*(?:basic\s+)?"
    r"(?:pok[eé]mon|energy|card)[^.]{0,40}?discard pile[^.]{0,24}?"
    r"(?:into|to)\s+your\s+hand", re.I)
_EN_MOVE_RE = re.compile(r"move\s+(?:up to\s+)?(\d+)\s+damage\s+counters?",
                         re.I)
# v1.3：移动全部指示物（Move all damage counters …）
_EN_MOVE_ALL_RE = re.compile(r"move all damage counters", re.I)
_EN_SWITCH_RE = re.compile(r"switch\s+(?:your|out|it)|with\s+(?:your|1 of your)\s+"
                           r"benched|switch your active", re.I)
# v1.3：把对手后备拉上前台（Switch in 1 of your opponent's Benched Pokémon）
_EN_GUST_RE = re.compile(
    r"switch in\s+(?:1 of\s+)?(?:your\s+)?opponent|"
    r"switch (?:1 of\s+)?your opponent'?s bench", re.I)
_EN_KNOCKOUT_RE = re.compile(r"(?:is|are|was|were)\s+knocked out|knocked out by",
                             re.I)
_EN_EVOLVE_RE = re.compile(r"\bevolve[sd]?\b|evolution", re.I)
_EN_MODIFY_RE = re.compile(r"weakness|resistance|prevents? all damage|"
                           r"can'?t retreat|cannot retreat|isn'?t affected",
                           re.I)
_EN_BENCH_RE = re.compile(r"bench(?:ed)?\s+pok|onto your bench", re.I)
_EN_HAND_RE = re.compile(r"(?:into|to)\s+your\s+hand", re.I)
_EN_OPP_RE = re.compile(r"opponent[\u2019']?s?\b", re.I)

# 能量符号单字母 → 引擎色（数据集的 {G}/{P}… 记号）
_EN_ENERGY_LETTER = {"G": "grass", "R": "fire", "W": "water", "L": "lightning",
                     "P": "psychic", "F": "fighting", "D": "darkness",
                     "M": "metal", "C": "colorless", "N": "dragon",
                     "Y": "psychic"}


def _en_search_what(lower: str) -> str:
    """英文检索目标归类（供训练家检索 tag 选择）"""
    if "pokémon" in lower or "pokemon" in lower:
        return "pokemon"
    if "trainer" in lower:
        return "trainer"
    if "energy" in lower:
        return "energy"
    return "any"


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
        if _EN_SELF_SPREAD_RE.search(t):
            # 指示物放在自身 → 需新引擎机制（B4），如实标注而非误判为对手
            actions.append(act("rule", None, "self",
                               {"note": "自身放置伤害指示物（需引擎新机制 · "
                                        "未建模动作清单 B4）"}))
        else:
            tgt = "active_opponent_bench" if _EN_BENCH_RE.search(t) \
                else "active_opponent"
            extra = {"counters": n}
            if re.search(r"in any way you like|as you like|anyway you like", t,
                         re.I):
                extra["free_distribute"] = True
            actions.append(act("spread", n * 10, tgt, extra))

    m = _EN_MOVE_RE.search(t)
    if m:
        tgt = "self" if re.search(r"your (?:own )?pok", t, re.I) \
            else "active_opponent"
        actions.append(act("move_damage", int(m.group(1)), tgt))
    elif _EN_MOVE_ALL_RE.search(t):
        # 移动全部指示物 → 引擎全量移动（move_damage:all）
        actions.append(act("move_damage", None, "active_opponent",
                           {"all": True}))

    m = _EN_HEAL_RE.search(t)
    if m:
        actions.append(act("heal", int(m.group(1)), "self"))
    elif re.search(r"heal all damage", t, re.I):
        actions.append(act("rule", None, "self",
                           {"note": "全量回复（需引擎新机制 · B4）"}))

    m = _EN_DRAW_RE.search(t)
    if m:
        actions.append(act("draw", int(m.group(1)), "deck"))
    elif _EN_DRAW_TO_RE.search(t):
        actions.append(act("draw", None, "hand",
                           {"to_hand_size": int(_EN_DRAW_TO_RE.search(t).group(1))}))
    elif re.search(r"draw (?:a|1) card", t, re.I):
        actions.append(act("draw", 1, "deck"))

    # 弃牌堆回收（Put up to N … from your discard pile into your hand）
    mr = _EN_RECOVER_RE.search(t)
    if mr:
        n = int(mr.group(1)) if mr.group(1) else 1
        low = t.lower()
        # 变量名用 rk（recover kind）：**不可**用 kind——那会遮蔽函数参数
        # `kind`，使最后 spec(kind=…, …) 写进 "pokemon"/"energy"，触发 Schema
        # 非法（真 bug：Recovery Net 一类卡曾因此报「kind 非法：pokemon」）。
        rk = "any"
        if "pokémon" in low or "pokemon" in low:
            rk = "pokemon"
        elif "energy" in low:
            rk = "energy"
        actions.append(act("recover", n, "discard", {"kind": rk}))

    if _EN_DISCARD_ALL_RE.search(t):
        actions.append(act("discard", None, "hand", {"all": True}))
    elif _EN_DISCARD_ALL_ENERGY_RE.search(t):
        actions.append(act("discard", None, "self", {"card_type": "energy",
                                                     "all": True}))
    elif _EN_DISCARD_UPTO_ENERGY_RE.search(t):
        n = int(_EN_DISCARD_UPTO_ENERGY_RE.search(t).group(1))
        # 「from your hand」→ 弃手牌能量；否则弃自身 / 对手身上的能量
        if "from your hand" in t.lower():
            actions.append(act("discard", n, "hand", {"card_type": "energy"}))
        else:
            tgt = "active_opponent" if _EN_OPP_RE.search(t) else "self"
            actions.append(act("discard", n, tgt, {"card_type": "energy"}))
    elif _EN_DISCARD_ENERGY_RE.search(t):
        n = int(_EN_DISCARD_ENERGY_RE.search(t).group(1))
        tgt = "active_opponent" if _EN_OPP_RE.search(t) else "self"
        actions.append(act("discard", n, tgt, {"card_type": "energy"}))
    elif _EN_DISCARD_RE.search(t):
        n = int(_EN_DISCARD_RE.search(t).group(1))
        tgt = "opponent_hand" if ("opponent" in lower and "hand" in lower) \
            else "hand"
        actions.append(act("discard", n, tgt))

    m = _EN_SEARCH_TOP_RE.search(t)
    if m:
        actions.append(act("search", 1, "deck", {"look_top": int(m.group(1))}))
    elif _EN_LOOK_TOP_ONE_RE.search(t):
        actions.append(act("search", 1, "deck", {"look_top": 1}))
    elif _EN_SEARCH_RE.search(t):
        g = _EN_SEARCH_RE.search(t).group(1)
        tgt = "discard" if "discard pile" in lower else "deck"
        actions.append(act("search", int(g) if g else 1, tgt,
                           {"what": _en_search_what(lower)}))

    m = _EN_ATTACH_RE.search(t)
    if m and "energ" in lower:
        amt_m = _EN_ATTACH_AMT_RE.search(t)
        amt = int(amt_m.group(1)) if amt_m else 1
        letter, word = m.group(1), m.group(2)
        et = _EN_ENERGY_LETTER.get((letter or "").upper()) or \
            ((word or "").lower() or None)
        zone = ("hand" if "from your hand" in lower
                else "discard" if "discard pile" in lower
                else "deck" if "your deck" in lower else None)
        extra = {}
        if et:
            extra["energy_type"] = et
        if zone:
            extra["zone"] = zone
        actions.append(act("attach", amt, "self", extra or None))

    for word, tag in _EN_STATUS:
        if word in lower:
            tgt = "active_opponent" if _EN_OPP_RE.search(t) else "self"
            actions.append(act("status", None, tgt, {"status": tag}))
            break

    if _EN_GUST_RE.search(t):
        # 把对手后备拉上前台（gust）
        actions.append(act("switch", None, "active_opponent_bench"))
    elif _EN_SWITCH_RE.search(t):
        mine = bool(re.search(r"your|switch out", t, re.I)) and \
            not _EN_OPP_RE.search(t)
        actions.append(act("switch", None,
                           "self" if mine else "active_opponent_bench"))

    if _EN_KNOCKOUT_RE.search(t) and not re.search(r"if .{0,40}knocked out",
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
    # 「被击倒」允许中间插入限定语（如「被招式击倒时」）——此前仅匹配字面
    # 「被击倒」，漏掉真实卡面最常见的「被招式击倒」写法。
    if re.search(r"被[^。；，,]{0,6}击倒", text) or "气绝" in text:
        return "on_knockout"
    if re.search(r"进化[^。；，,]{0,8}时", text) or "进化时" in text:
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
    """规则后端入口：语言分流 + **修正器后置识别**（v1.4）

    「修正器」= 不产出独立动作、而是改写伤害或防护结果的句式：
      抛币（coin_flip）· 条件/计数伤害缩放（damage_mod）·
      护盾（shield）· 被动静态（passive）

    这类句式原先统一落进 ``rule`` 而成为「未建模」，是 B4 批次（212 条）的主体。
    单独做后置识别的好处：**不改动**既有抽取顺序（既有 555 条已映射 tag 的行为
    保持逐字节可复现），只在末尾追加新动作。
    """
    spec = (_parse_rule_en(text, kind=kind, name=name)
            if text and not _CJK_RE.search(str(text))
            else _parse_rule_zh(text, kind=kind, name=name))
    actions = spec.get("actions") or []
    # 已有具体动作的句式不再追加 rule 兜底之外的东西；无论有无都尝试识别修正器
    mods = _detect_modifiers(str(text or ""), kind, name)
    if mods:
        # 修正器出现时，移除「静态未建模」兜底 rule（已被真正识别）
        has_coin = any(m.get("type") == "coin_flip" for m in mods)
        drop = list(_RULE_PLACEHOLDER_NOTES) + (
            list(_COIN_COVERED_NOTES) if has_coin else [])
        actions = [a for a in actions
                   if not (a.get("type") == "rule"
                           and _note_starts_with(a, drop))]
        spec["actions"] = actions + mods
    return spec


def _note_starts_with(action: dict, prefixes: tuple) -> bool:
    note = str((action.get("extra") or {}).get("note") or "")
    return any(note.startswith(p) for p in prefixes)


# 抛币分支已覆盖的兜底 note：这些 note 描述的正是抛币某分支里的子效果，
# 若同时保留会重复计入未建模（污染覆盖率口径）。
_COIN_COVERED_NOTES = ("自身放置伤害指示物",)


# ---------- v1.4 修正器识别（抛币 / 伤害缩放 / 护盾 / 被动） ----------
_RULE_PLACEHOLDER_NOTES = ("静态/未建模（英文）",
                           "静态规则修改（弱点/抗性/伤害减免/限制）",
                           "静态/未建模")

# 抛币分支已覆盖的兜底 note：这些 note 描述的正是抛币某分支里的子效果，
# 若同时保留会重复计入未建模（污染覆盖率口径）。
_COIN_COVERED_NOTES = ("自身放置伤害指示物",)

# 抛币
# 注意：真实卡池用**弯撇号**（’）与复数形式（flips），两者都必须覆盖——
# 归一化统一在 `_norm_apostrophe()` 完成，正则本身按直撇号书写。
_COIN_RE = re.compile(r"flips?\s+(?:a\s+|\d+\s+)?coins?|抛硬币|掷硬币", re.I)
_COIN_EACH_RE = re.compile(
    r"flips?\s+a\s+coin\s+for\s+each|for\s+each[^.]{0,40}?flips?\s+a\s+coin",
    re.I)
# 「Flip a coin. If heads, ...」的四个具体子效果
_COIN_HEADS_MORE_DMG_RE = re.compile(
    r"flip\s+a\s+coin[^.]*?\.\s*if\s+heads[^.]*?does\s+(\d+)\s+more\s+damage",
    re.I)
_COIN_TAILS_NOTHING_RE = re.compile(
    r"flip\s+a\s+coin[^.]*?\.\s*if\s+tails[^.]*?does\s+nothing", re.I)
_COIN_HEADS_DISCARD_ENERGY_RE = re.compile(
    r"if\s+heads[^.]*?discard\s+(?:an?\s+)?(?:energy|energy card)", re.I)
_COIN_HEADS_PREVENT_RE = re.compile(
    r"if\s+heads[^.]*?prevent\s+all\s+damage", re.I)
_COIN_TAILS_PREVENT_RE = re.compile(
    r"if\s+tails[^.]*?prevent\s+all\s+damage", re.I)
_COIN_HEADS_COUNTERS_RE = re.compile(
    r"if\s+heads[^.]*?put\s+(\d+)\s+damage\s+counters?\s+on\s+1\s+of\s+your\s+opponent",
    re.I)
_COIN_TAILS_SELF_COUNTERS_RE = re.compile(
    r"if\s+tails[^.]*?put\s+(\d+)\s+damage\s+counters?\s+on\s+your\s+active",
    re.I)
# 「Flip N coins. This attack does M more damage for each heads.」（v1.4）
# 语义 = 抛 N 枚、每个正面重复一次加伤 → coin_each:N:heads:dmg_plus:M
# 注意：卡面在 "Flip N coins." 与 "This attack does…" 之间**有句号**，
# 故必须允许跨句点匹配（用 [\s\S] 而非 [^.]）。
_COIN_PER_SIDE_RE = re.compile(
    r"flip\s+(\d+)?\s*coins?[\s\S]{0,80}?does\s+(\d+)\s+(?:more\s+)?damage"
    r"[\s\S]{0,40}?for\s+each\s+(heads|tails)", re.I)
# 伤害缩放
_SCALING_MORE_RE = re.compile(
    r"does\s+(\d+)\s+more\s+damage\s+for\s+each\s+([^.]{4,90})", re.I)
_SCALING_BASE_RE = re.compile(
    r"this\s+attack\s+does\s+(\d+)\s+damage\s+for\s+each\s+([^.]{4,90})", re.I)
# 护盾 / 被动
_SHIELD_REDUCE_RE = re.compile(r"takes\s+(\d+)\s+less\s+damage", re.I)
_BENCH_IMMUNE_RE = re.compile(
    r"as\s+long\s+as\s+this\s+pok[eé]mon\s+is\s+on\s+your\s+bench[^.]*?"
    r"prevent\s+all\s+damage", re.I)
_SHIELD_PREVENT_RE = re.compile(
    r"prevent\s+all\s+damage(?:\s+from\s+and\s+effects\s+of\s+attacks)?"
    r"[^.]*?(?:done\s+)?to\s+this\s+pok[eé]mon", re.I)
# 条件：上回合被击倒
_KO_LAST_TURN_RE = re.compile(
    r"knocked\s+out[^.]*?opponent'?s?\s+last\s+turn", re.I)
# 招式内进化
_EVOLVE_FROM_DECK_RE = re.compile(
    r"search\s+your\s+deck\s+for\s+a\s+card\s+that\s+evolves\s+from\s+this\s+"
    r"pok[eé]mon", re.I)
# 抽到指定手牌数
_DRAW_UNTIL_RE = re.compile(r"draw cards until you have (\d+) cards?", re.I)

# 缩放计数源：短语（小写）→ 引擎计数源；**按顺序**匹配，先具体后笼统
_COUNTER_PHRASES = (
    ("energy attached to this pok", "own_active_energy"),
    ("energy attached to your opponent", "opp_active_energy"),
    ("energy attached to both active", "both_active_energy"),
    ("energy attached to your active", "own_active_energy"),
    ("energy attached to all of your pok", "own_all_energy"),
    ("energy attached to all pok", "both_active_energy"),
    ("damage counter on this pok", "own_active_counters"),
    ("damage counter on your opponent's active", "opp_active_counters"),
    ("damage counter on all of your benched", "own_bench_counters"),
    ("damage counter on all of your", "own_bench_counters"),
    ("damage counter on your benched", "own_bench_counters"),
    ("damage counter you placed", "own_discarded_this_turn"),
    ("energy card you discarded in this way", "own_discarded_energy_this_turn"),
    ("energy card discarded in this way", "own_discarded_energy_this_turn"),
    ("card you discarded in this way", "own_discarded_this_turn"),
    ("card discarded in this way", "own_discarded_this_turn"),
    ("energy card in your discard pile", "own_discard_energy"),
    ("energy in your discard pile", "own_discard_energy"),
    ("card in your discard pile", "own_discard_count"),
    ("card in your hand", "own_hand"),
    ("stage 2 pok", "own_bench_stage2"),
    ("benched pok", "opp_bench"),
    ("of your pokémon in play", "own_field"),
    ("of your pokemon in play", "own_field"),
    ("prize card", "opp_prizes_left"),
)

# 「带限定词 = 只数某个子集」的短语特征：类别词 + 具名/条件修饰。
# 这些**不可**笼统按总数映射（否则系统性多算），一律返回 None 登记为未建模。
_FILTER_QUALIFIER_RE = re.compile(
    r"\b(that has|that have|that is|with a|with an|named|“|”)", re.I)

# 「每有 1 个能量」类颜色限定：{W} 等符号 → 引擎色代码
_EN_SYMBOL_COLOR = {"G": "grass", "R": "fire", "W": "water", "L": "lightning",
                    "P": "psychic", "F": "fighting", "D": "darkness",
                    "M": "metal", "C": "colorless", "N": "dragon",
                    "Y": "psychic"}


def _counter_from_phrase(phrase: str) -> Optional[str]:
    """英文短语 → 引擎计数源（未识别 → None，调用方如实标注不支持）

    **反假映射守卫**：``card in your discard pile`` 这类笼统短语若前面带**类别限定词**
    （Ancient / Pokémon / Trainer / Item / Supporter / Stadium / Tool / Stage n / Tera…），
    语义是「弃牌堆中某一小类的数量」，与「弃牌堆总卡数」不等——若笼统映射会**系统性多算**
    伤害（60 条以上卡面受影响）。这类一律返回 None，登记为未建模，绝不猜。
    （``energy card in your discard pile`` 例外：限定词就是能量本身，等价于总能量数。）
    """
    raw = _norm_apostrophe(phrase)
    low = raw.lower()
    if _FILTERED_DISCARD_RE.search(low) and "energy" not in low:
        return None
    if _FILTER_QUALIFIER_RE.search(low):
        return None           # 「…that has “X”」「…with a Rule Box」= 子集，不可按总数算
    # 带能量颜色限定（{W} Energy attached to this Pokémon）→ <zone>_energy_<color>
    mcolor = re.search(r"\{([A-Za-z])\}\s*energy", raw, re.I)
    if "energy attached" in low or "energy card" in low:
        zone = _energy_zone_from_phrase(low)
        color = _EN_SYMBOL_COLOR.get((mcolor.group(1).upper() if mcolor else ""))
        if zone == "own_discard":
            # 弃牌堆能量：指定颜色 = 子集 → 不可按总能量数算
            return None if color else "own_discard_energy"
        if zone:
            return f"{zone}_energy" + (f"_{color}" if color else "")
    for needle, counter in _COUNTER_PHRASES:
        if needle in low:
            return counter
    return None


def _energy_zone_from_phrase(low: str) -> Optional[str]:
    """能量计数短语 → 引擎能量 zone（未识别 → None）"""
    if "attached to this pok" in low or "attached to your active" in low:
        return "own_active"
    if "attached to your opponent" in low:
        return "opp_active"
    if "attached to both active" in low:
        return "both_active"
    if "attached to all of your" in low:
        return "own_all"
    if "attached to all pok" in low:
        return "both_active"
    if "in your discard pile" in low:
        return "own_discard"          # 走通用 discard 计数，非能量 zone
    return None


# 带类别限定词的计数短语 → 不可笼统按总数映射
_FILTERED_DISCARD_RE = re.compile(
    r"\b(ancient|future|pok[eé]mon|trainer|basic|item|supporter|stadium|tool|"
    r"stage\s*\d|tera|rule\s*box)\b[^.]{0,24}?\b(cards?|pok[eé]mon)\b", re.I)


def _norm_apostrophe(s: str) -> str:
    """弯撇号 → 直撇号（真实卡池为 ``opponent’s``，正则按 ``opponent's`` 书写）

    不做这步会让 ``opponent'?s?``、``opponent's benched`` 之类的模式**静默失配**，
    表现为「明明写了识别规则却识别不出来」——这类问题在英文卡面语料上极易踩。
    """
    return str(s or "").replace("\u2019", "'").replace("\u2018", "'")


def _detect_modifiers(text: str, kind: str, name: str) -> list:
    """从卡面文本识别 v1.4 修正器 → 动作列表（未识别返回空表，不猜）"""
    t = _norm_apostrophe(text)
    low = t.lower()
    out: list = []
    if not t.strip():
        return out

    # 1) 条件/计数伤害缩放（先算，便于抛币分支复用同一伤害修正语义）
    m = _SCALING_MORE_RE.search(t) or _SCALING_BASE_RE.search(t)
    # 「for each heads/tails」不是静态计数源，而是抛币枚数 → 交给 coin 分支处理，
    # 这里必须让位，否则会被误判成「计数源未识别」而丢掉一个可执行 tag
    coin_per_side = _COIN_PER_SIDE_RE.search(t)
    if m and re.fullmatch(r"\s*(heads|tails)\s*\.?\s*", m.group(2), re.I):
        m = None
    if m and coin_per_side:
        m = None
    if m:
        n, phrase = int(m.group(1)), m.group(2)
        counter = _counter_from_phrase(phrase)
        if counter:
            mode = "plus_per"
            # 「does 10 damage for each …」= 基础伤害按计数源计算（非加成）
            if _SCALING_BASE_RE.search(t) and not _SCALING_MORE_RE.search(t):
                mode = "set_per"
            out.append(act("damage_mod", n, "active_opponent",
                           {"mode": mode, "per": counter,
                            "phrase": phrase.strip()[:40]}))
        else:
            out.append(act("rule", None, "active_opponent",
                           {"note": f"条件伤害缩放：计数源未识别（{phrase.strip()[:30]}）"}))
    elif _KO_LAST_TURN_RE.search(t) or re.search(r"were\s+knocked\s+out", t, re.I):
        add = re.search(r"does\s+(\d+)\s+more\s+damage", t, re.I)
        if add and _KO_LAST_TURN_RE.search(t):
            out.append(act("damage_mod", int(add.group(1)), "active_opponent",
                           {"mode": "plus_if", "cond": "opp_ko_last_turn"}))

    # 2) 抛币
    if _COIN_RE.search(t):
        out.extend(_detect_coin(t, low))

    # 3) 护盾 / 被动
    #    注意：若护盾写在抛币分支里（「if heads, … prevent all damage …」），它只在
    #    正面生效 → 已由 coin_flip 动作承载，**不得**再产出无条件 shield（否则变成
    #    必中，属实质性加强）。这里显式去重。
    coin_covers_shield = any(
        a.get("type") == "coin_flip"
        and str((a.get("extra") or {}).get("sub", "")).startswith("shield")
        for a in out)
    bench_immune = bool(_BENCH_IMMUNE_RE.search(t))
    mred = _SHIELD_REDUCE_RE.search(t)
    mprev = _SHIELD_PREVENT_RE.search(t)
    if bench_immune:
        out.append(act("passive", None, "self", {"kind": "bench_immune"}))
    elif coin_covers_shield:
        pass
    elif mred and _opp_next_turn(t):
        out.append(act("shield", int(mred.group(1)), "self",
                       {"kind": "reduce"}))
    elif mprev and _opp_next_turn(t):
        out.append(act("shield", None, "self", {"kind": "prevent_all"}))

    # 4) 抽到指定手牌数
    m = _DRAW_UNTIL_RE.search(t)
    if m and kind == "ability":
        out.append(act("draw", None, "hand",
                       {"to_hand_size": int(m.group(1))}))

    # 5) 招式内检索进化
    if _EVOLVE_FROM_DECK_RE.search(t):
        out.append(act("evolve", None, "self", {"from_deck": True}))

    return out


def _opp_next_turn(text: str) -> bool:
    """是否限定「在对手的下个回合内」（护盾类语义的前提）"""
    low = _norm_apostrophe(text).lower()
    return bool(re.search(r"opponent'?s?\s+next\s+turn", low)
                or re.search(r"opponent'?s?\s+turn", low))


def _detect_coin(text: str, low: str) -> list:
    """抛币分支识别 → coin_flip 动作（每枚币一个动作，含 side/sub）"""
    out: list = []
    # 「Flip N coins … for each heads/tails」优先（否则会被 each 分支吞掉）
    mps = _COIN_PER_SIDE_RE.search(text)
    if mps:
        n = int(mps.group(1)) if mps.group(1) else 1
        out.append(act("coin_flip", None, "active_opponent",
                       {"side": mps.group(3).lower(), "mode": "each",
                        "counter": str(n), "sub": "dmg_plus",
                        "sub_amount": int(mps.group(2))}))
        return out
    each = _COIN_EACH_RE.search(text)
    if each:
        # 「对手为每只后备各抛 1 枚，每个反面 …」→ 计数源 + 反面枚数生效
        m = re.search(r"does\s+(\d+)\s+damage[^.]*?for\s+each\s+tails", text,
                      re.I)
        if m and "for each tails" in low:
            out.append(act("coin_flip", None, "active_opponent",
                           {"side": "tails", "mode": "each",
                            "counter": "opp_bench", "sub": "dmg_plus",
                            "sub_amount": int(m.group(1))}))
            return out
        return out
    m = _COIN_HEADS_MORE_DMG_RE.search(text)
    if m:
        out.append(act("coin_flip", None, "active_opponent",
                       {"side": "heads", "mode": "single",
                        "sub": "dmg_plus", "sub_amount": int(m.group(1))}))
    if _COIN_TAILS_NOTHING_RE.search(text):
        out.append(act("coin_flip", None, "active_opponent",
                       {"side": "tails", "mode": "single", "sub": "dmg_zero"}))
    if _COIN_HEADS_DISCARD_ENERGY_RE.search(text):
        out.append(act("coin_flip", None, "active_opponent",
                       {"side": "heads", "mode": "single",
                        "sub": "discard_energy", "sub_amount": 1}))
    if _COIN_HEADS_PREVENT_RE.search(text):
        out.append(act("coin_flip", None, "self",
                       {"side": "heads", "mode": "single",
                        "sub": "shield_prevent_all"}))
    elif _COIN_TAILS_PREVENT_RE.search(text):
        out.append(act("coin_flip", None, "self",
                       {"side": "tails", "mode": "single",
                        "sub": "shield_prevent_all"}))
    mh = _COIN_HEADS_COUNTERS_RE.search(text)
    if mh:
        out.append(act("coin_flip", None, "active_opponent_bench",
                       {"side": "heads", "mode": "single", "sub": "spread_free",
                        "sub_amount": int(mh.group(1)) * 10}))
    mt = _COIN_TAILS_SELF_COUNTERS_RE.search(text)
    if mt:
        out.append(act("coin_flip", None, "self",
                       {"side": "tails", "mode": "single", "sub": "self",
                        "sub_amount": int(mt.group(1)) * 10}))
    return out


def _parse_rule_zh(text: str, kind: str = "attack", name: str = "",
                   name_zh: str = "") -> dict:
    """规则后端（中文卡面）：从中文效果文本抽取 spec（确定性、可离线、零依赖）

    覆盖「造成 N 点伤害 / 放置 N 个伤害指示物（可任意分配）/ 抽 N 张 /
    弃 N 张·弃 N 个能量 / 回复 N 点 / 附 N 个能量 / 检视牌库顶 N 取 M /
    检索·从弃牌堆取 N / 互换·换下 / 状态（中毒·灼伤·睡眠·麻痹·混乱）/
    击倒 / 每回合一次」等句式。

    注意：**语言分流与修正器识别在 ``parse_rule`` 完成**，本函数只处理中文抽取。
    """
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
    # v1.3：「从弃牌堆/牌库取 N 张（基本X）能量」（Blaze Dance 等）——
    # 能量词在动词之后，_ATTACH_RE 命不中，此前会误落到 search 分支
    m_zone_attach = _DISCARD_ATTACH_RE.search(t_norm)
    if not has_attach and m_zone_attach:
        has_attach = True

    # 弃牌堆回收（v1.3）：取 N 张…从弃牌堆…入手（Night Stretcher / 招式回收）
    has_recover = False
    mr = _RECOVER_RE2.search(t_norm) or _RECOVER_RE.search(t_norm)
    if mr and "弃牌堆" in t_norm and not has_attach:
        n = _num(mr.group(1)) if (mr.groups() and mr.group(1)) else 1
        # 变量名用 rk：**不可**用 kind（会遮蔽函数参数 kind → spec.kind 非法）
        rk = "any"
        if "能量" in t_norm and "宝可梦" not in t_norm:
            rk = "energy"
        elif "宝可梦" in t_norm and "能量" not in t_norm:
            rk = "pokemon"
        actions.append(act("recover", n, "discard", {"kind": rk}))
        has_recover = True

    # 检索（牌库顶检视 / 通用检索；从弃牌堆 → target=discard）
    if not has_attach and not has_recover:
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

    # 附能量（附 N 个X能量 / 提供 N 点X能量 / 加速 N 个能量 / 从弃牌堆取 N 张X能量）
    if has_attach and not any(a["type"] == "attach" and a.get("extra", {}).get(
            "energy_type") for a in actions):
        mm = _ATTACH_RE.search(t_norm)
        amt = None
        etype = None
        zone = None
        if mm:
            amt = int(mm.group(1)) if mm.group(1) else 1
            etype = _ENERGY_ZH.get(mm.group(2) or "", None)
        elif m_zone_attach:
            # 「从弃牌堆/牌库/手牌取 N 张（基本X）能量」
            amt = int(m_zone_attach.group(1))
            etype = _ENERGY_ZH.get(m_zone_attach.group(2) or "", None)
            zone = ("discard" if "弃牌堆" in t_norm
                    else "hand" if "手牌" in t_norm else "deck")
        else:
            mp = _PROVIDE_RE.search(t_norm)
            if mp:
                amt = int(mp.group(1))
                etype = _ENERGY_ZH.get(mp.group(2) or "", None)
            else:
                ma = _ACCEL_RE.search(t_norm)
                amt = int(ma.group(1)) if (ma and ma.group(1)) else None
        extra = {}
        if etype:
            extra["energy_type"] = etype
        if zone:
            extra["zone"] = zone
        actions.append(act("attach", amt, "self", extra or None))

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
    mw = _WEAK_CHANGE_RE.search(t_norm)
    if mw:
        # v1.3：弱点改写可映射到引擎 weak_change:<type>[:<filter>]
        to_t = _ENERGY_ZH.get(mw.group(1), None)
        filt = "all"
        if "龙" in t_norm:
            filt = "dragon"
        actions.append(act("rule", None, "any",
                           {"note": "弱点改写", "weak_change": to_t,
                            "filter": filt}))
    elif _MODIFY_RE.search(t_norm):
        actions.append(act("rule", None, "any",
                           {"note": "静态规则修改（弱点/抗性/限制）"}))

    # 击倒（主动造成；「被击倒」属触发时机，不产出动作）
    if not re.search(r"被[^。；，,]{0,6}击倒", t_norm) and \
            (re.search(r"(?:造成|使其|将其|直接)击倒", t_norm) or "气绝" in t_norm):
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
def _coin_sub_tag(sub: str, amount) -> str:
    """抛币子效果 → 引擎子 tag

    ``dmg_plus`` → ``dmg_plus:N``；``discard_energy`` → ``discard_energy:N``；
    ``self``/``spread_free`` 带额；``shield_prevent_all`` → ``shield:prevent_all``；
    ``dmg_zero`` 原样。
    """
    n = int(amount) if amount else 0
    if sub == "dmg_plus":
        return f"dmg_plus:{n}" if n else "dmg_plus"
    if sub == "dmg_zero":
        return "dmg_zero"
    if sub == "discard_energy":
        return f"discard_energy:{n or 1}"
    if sub in ("self", "spread_free", "heal", "draw"):
        return sub if not n else f"{sub}:{n}"
    if sub == "shield_prevent_all":
        return "shield:prevent_all"
    if sub.startswith("shield:") or sub.startswith("dmg_plus:"):
        return sub
    return sub or ""


def to_engine_tags(specs: list) -> dict:
    """把 spec 映射为引擎可执行 tag；返回 {tags[], unsupported[], notes[]}

    引擎支持（见 `engine/data/cards_real.json` meta.engine_tags，v1.3 全集）：

    招式效果（`kind=attack`）
      ``poison``/``burn``/``sleep``/``paralyze``/``confuse`` · ``spread_free:N`` ·
      ``spread:N:per`` · ``heal:N`` · ``self:N`` · ``draw:N`` ·
      ``discard:N`` · ``discard_energy:N`` · ``switch_self`` · ``gust`` ·
      ``attach:N[:type]`` · ``move_damage:N``

    特性（`kind=ability`）
      ``recon:N``（检视牌库顶）· ``draw:N``（每回合一次抽牌）·
      ``ko_draw:N``（被击倒时抽牌，trigger=on_knockout）·
      ``move_damage:N`` · ``attach_energy:N[:type]``（每回合一次附能）·
      ``evolve_attach:N[:type]:discard``（进化时从弃牌堆附能）·
      ``weak_change:<type>[:<filter>]``（改写对手弱点）
      （``cursed:N`` 为手牌特性，由卡级 tag 指定，不由此映射产出）

    训练家（`kind=item`/`supporter`）
      ``candy``（跳阶进化）· 其余训练家语义需卡级 tag（poffin/ultra_ball/
      hammer/boss/research:N/shuffle_draw:N/switch_self/recover:N/
      discard_search_energy:N/search_energy:N）——**解析链不臆造**，
      统一列入 unsupported 并给出理由（诚实标注，C5）。

    未建模（P1/P2 批次）：``knockout``（击倒效果）· 条件伤害缩放 · 抛币 ·
    伤害减免/免疫等静态修正——一律如实列入 ``unsupported``。
    """
    tags: list = []
    unsupported: list = []
    notes: list = []
    # 同一 spec 里多条 evolve 动作时，只要有一条是「从牌库检索进化」，
    # 即按 evolve_from_deck 处理（避免同句另一条裸 evolve 误报「需卡级 tag」）
    has_evolve_from_deck = any(
        a.get("type") == "evolve" and (a.get("extra") or {}).get("from_deck")
        for s0 in (specs or []) for a in (s0.get("actions") or []))
    for s in specs or []:
        kind = s.get("kind")
        trigger = s.get("trigger")
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
                counters = extra.get("counters")
                if extra.get("free_distribute") and amt is not None:
                    tags.append(f"spread_free:{int(amt)}")
                elif counters and amt is not None:
                    per = int(amt) // max(1, int(counters))
                    tags.append(f"spread:{int(counters)}:{per}")
                else:
                    unsupported.append(
                        {"type": t,
                         "reason": "放置指示物缺少份数（需 counters 元数据）"})
            elif t == "draw":
                if extra.get("to_hand_size"):
                    tags.append(f"draw_to:{int(extra['to_hand_size'])}")
                elif amt is None:
                    unsupported.append({"type": t, "reason": "抽牌量未识别"})
                elif kind == "ability":
                    if trigger == "on_knockout":
                        tags.append(f"ko_draw:{int(amt)}")
                    else:
                        tags.append(f"draw:{int(amt)}")
                else:
                    tags.append(f"draw:{int(amt)}")
            elif t == "heal":
                if amt is None:
                    unsupported.append({"type": t, "reason": "回复量未识别"})
                else:
                    tags.append(f"heal:{int(amt)}")
            elif t == "attach":
                n = int(amt) if amt is not None else 1
                et = extra.get("energy_type")
                zone = extra.get("zone")
                suffix = f":{et}" if et else ""
                zsuffix = f":{zone}" if zone else ""
                if kind == "ability":
                    if trigger == "on_evolve":
                        tags.append(f"evolve_attach:{n}{suffix}"
                                    f"{zsuffix or ':discard'}")
                    else:
                        tags.append(f"attach_energy:{n}{suffix}{zsuffix}")
                else:
                    tags.append(f"attach:{n}{suffix}{zsuffix}")
            elif t == "discard":
                n = int(amt) if amt is not None else 1
                is_energy = extra.get("card_type") == "energy"
                if extra.get("all"):
                    tags.append("discard_energy:all" if is_energy
                                else "discard:all")
                elif is_energy:
                    tags.append(f"discard_energy:{n}")
                else:
                    tags.append(f"discard:{n}")
            elif t == "move_damage":
                if extra.get("all"):
                    tags.append("move_damage:all")
                elif amt is None:
                    unsupported.append({"type": t, "reason": "移动份数未识别"})
                else:
                    tags.append(f"move_damage:{int(amt)}")
            elif t == "switch":
                tags.append("switch_self" if tgt == "self" else "gust")
            elif t == "evolve":
                if extra.get("from_deck") or has_evolve_from_deck:
                    tags.append("evolve_from_deck")
                elif kind in ("item", "supporter"):
                    tags.append("candy")
                else:
                    unsupported.append(
                        {"type": t, "reason": "招式/特性驱动的进化需卡级 tag"})
            elif t == "search":
                if kind == "ability":
                    tags.append(f"recon:{amt if amt is not None else 1}")
                else:
                    # 训练家检索：按检索目标选 tag（pokemon/trainer/energy/any）
                    what = extra.get("what") or "any"
                    n = amt if amt is not None else 1
                    if tgt == "discard":
                        unsupported.append(
                            {"type": t, "reason": "从弃牌堆检索（非回收）需引擎"
                                                  "新机制（B4）"})
                    elif what == "energy":
                        tags.append(f"search_energy:{n}")
                    elif what == "pokemon":
                        tags.append(f"search_pokemon:{n}")
                    elif what == "trainer":
                        tags.append(f"search_trainer:{n}")
                    else:
                        tags.append(f"search_any:{n}")
            elif t == "recover":
                n = int(amt) if amt is not None else 1
                kd = extra.get("kind") or "any"
                tags.append(f"recover:{n}:{kd}")
            elif t == "rule":
                if extra.get("weak_change"):
                    f = extra.get("filter")
                    tags.append("weak_change:" + str(extra["weak_change"])
                                + (f":{f}" if f else ""))
                else:
                    unsupported.append(
                        {"type": t,
                         "reason": (str(extra.get("note"))
                                    or "静态规则/元游戏效果（P1–P2 批次）")})
            # ---- v1.4 修正器（抛币 / 伤害缩放 / 护盾 / 被动） ----
            elif t == "coin_flip":
                side = extra.get("side") or "heads"
                sub = extra.get("sub") or ""
                if extra.get("mode") == "each":
                    counter = extra.get("counter") or "opp_bench"
                    sub_tag = _coin_sub_tag(sub, extra.get("sub_amount"))
                    tags.append(f"coin_each:{counter}:{side}:{sub_tag}")
                elif sub:
                    sub_tag = _coin_sub_tag(sub, extra.get("sub_amount"))
                    tags.append(f"coin:{side}:{sub_tag}")
                else:
                    unsupported.append({"type": t, "reason": "抛币子效果未识别"})
            elif t == "damage_mod":
                mode = extra.get("mode") or "plus"
                if mode == "plus_per":
                    if not extra.get("per"):
                        unsupported.append({"type": t,
                                            "reason": "缩放计数源未识别"})
                    else:
                        tags.append(f"dmg_plus:{int(amt or 0)}:per:"
                                    f"{extra['per']}")
                elif mode == "set_per":
                    if not extra.get("per"):
                        unsupported.append({"type": t,
                                            "reason": "缩放计数源未识别"})
                    else:
                        tags.append(f"dmg_plus:{int(amt or 0)}:per:"
                                    f"{extra['per']}")
                elif mode == "plus_if":
                    if not extra.get("cond"):
                        unsupported.append({"type": t, "reason": "条件未识别"})
                    else:
                        tags.append(f"dmg_plus:{int(amt or 0)}:if:"
                                    f"{extra['cond']}")
                else:
                    tags.append(f"dmg_plus:{int(amt or 0)}")
            elif t == "shield":
                kind_ = extra.get("kind")
                if kind_ == "reduce":
                    tags.append(f"shield:reduce:{int(amt or 0)}")
                elif kind_ == "prevent_all":
                    tags.append("shield:prevent_all")
                else:
                    unsupported.append({"type": t, "reason": "护盾类型未识别"})
            elif t == "passive":
                kind_ = extra.get("kind")
                if kind_ == "bench_immune":
                    tags.append("passive:bench_immune")
                else:
                    unsupported.append(
                        {"type": t,
                         "reason": "静态封锁（无特性/禁用道具等）需引擎新机制"
                                   "（未建模动作清单 B4）"})
            elif t == "knockout":
                unsupported.append(
                    {"type": t,
                     "reason": "击倒效果需引擎新机制（未建模动作清单 B4）"})
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
