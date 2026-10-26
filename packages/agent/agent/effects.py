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
import re
import time
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
                "coin_flip", "damage_mod", "shield", "passive",
                # S2 道具（2026-10-26）：附着在宝可梦上的常驻修正
                "tool",
                # S3 场地（2026-10-27）：全场唯一、双方共用
                "stadium")
TARGETS = ("active_opponent", "self", "bench", "active_opponent_bench",
           "deck", "hand", "discard", "opponent_hand", "any")
COND_OPS = ("eq", "gt", "gte", "lt", "lte", "in", "contains")

# 引擎可执行 tag 映射（与 engine/data/cards_real.json 的 meta.engine_tags 对齐）
ENGINE_STATUS_TAGS = ("poison", "burn", "sleep", "paralyze", "confuse")

# ---- target 别名归一化 ----
# 为什么需要：LLM 常输出「枚举外但语义明确」的值。实测 A 类失败里有
# `action.target 非法：opponent_bench` —— 它想表达「对手后备区」，合法值叫
# `active_opponent_bench`。整条效果因一个词而作废，性价比极低。
#
# **边界（重要）**：只收**含义唯一**的别名。像 `active`（分不清敌我）、`bench`
# （虽合法但语义宽）这类**一律不猜** —— 让校验如实报错，远好过「猜错了静默用错目标」：
# 目标错了引擎会结算到错的宝可梦身上，那是**无声的错误行为**，比解析失败更危险。
_TARGET_ALIASES = {
    # 对手侧
    "opponent_bench": "active_opponent_bench",
    "opp_bench": "active_opponent_bench",
    "opponents_bench": "active_opponent_bench",
    "opponent_active": "active_opponent",
    "opp_active": "active_opponent",
    "opponents_active": "active_opponent",
    "defending_pokemon": "active_opponent",
    "opp_hand": "opponent_hand",
    # 自己侧
    "own_active": "self",
    "my_active": "self",
    "self_active": "self",
    "own_bench": "bench",
    "my_bench": "bench",
    "your_bench": "bench",
    "own_hand": "hand",
    "my_hand": "hand",
    "your_hand": "hand",
    "own_discard": "discard",
    "my_discard": "discard",
    # 牌库 / 任意
    "library": "deck",
    "deck_top": "deck",
    "anywhere": "any",
}


# ---- extra 取值字段归一化 ----
# 卡池重跑实测：模型拿到取值域后**仍会自创变体** —— 计数源 `own_all_pokemon`（引擎为
# `own_field`）、能量属性 `w` / `basic`。这类错误在引擎侧是**硬失败**（`_eval_counter`
# 遇未知计数源直接抛 ValueError），所以救回它们收益明确。
#
# **边界同上：只收语义唯一者。** 特别注意 `f`（Fire? Fighting?）与 `d`
# （Darkness? Dragon?）在 TCG 缩写里都存在歧义 → **一律不猜**。
_COUNTER_ALIASES = {
    # 「场上全部宝可梦」引擎叫 *_field
    "own_all_pokemon": "own_field",
    "opp_all_pokemon": "opp_field",
    "own_all_pokemons": "own_field",
    "opp_all_pokemons": "opp_field",
    # 「伤害指示物」的冗长写法
    "own_active_damage_counters": "own_active_counters",
    "opp_active_damage_counters": "opp_active_counters",
    "own_damage_counters": "own_active_counters",
    "opp_damage_counters": "opp_active_counters",
    # 「后备宝可梦数」
    "own_bench_pokemon": "own_bench",
    "opp_bench_pokemon": "opp_bench",
    # 「手牌张数」
    "own_hand_cards": "own_hand",
}

# 能量属性单字母缩写 → 引擎属性名（TCG 官方符号的常见转写）
# 只列**无歧义**的：f 与 d 故意不收（见上）。
_ENERGY_TYPE_ALIASES = {
    "g": "grass", "w": "water", "l": "lightning", "p": "psychic",
    "m": "metal", "c": "colorless", "r": "fire",
    "grass_energy": "grass", "fire_energy": "fire", "water_energy": "water",
    "lightning_energy": "lightning", "psychic_energy": "psychic",
    "fighting_energy": "fighting", "darkness_energy": "darkness",
    "metal_energy": "metal", "dragon_energy": "dragon",
    "colorless_energy": "colorless",
}


def _norm_key(v) -> str:
    """统一小写 / 连字符 / 空格 → 下划线（归一化用的比较键）"""
    return str(v).strip().lower().replace("-", "_").replace(" ", "_")


def normalize_counter(c):
    """计数源（`dmg_plus:…:per:<counter>`）归一化到引擎合法取值

    三级：① 已是合法值 → 原样；② 命中别名表；③ 剥掉 `_count/_num/…` 冗余后缀后
    为合法值。**③ 不会误伤** `own_discard_count` 这类「本身合法、又恰好以 `_count`
    结尾」的计数源 —— 剥离后若不合法就放弃剥离。
    """
    if not isinstance(c, str):
        return c
    key = _norm_key(c)
    valid = set(engine_vocab().get("counters") or ())
    if not valid:                       # 引擎不可用 → 不猜，保持原样
        return c
    if key in valid:
        return key
    if key in _COUNTER_ALIASES:
        return _COUNTER_ALIASES[key]
    for suf in ("_count", "_cnt", "_num", "_number"):
        if key.endswith(suf) and key[: -len(suf)] in valid:
            return key[: -len(suf)]
    return c


def normalize_energy_type(t):
    """能量属性归一化（单字母缩写 / `xx_energy` → 引擎属性名）

    合法属性名**从引擎读取**（`engine.cards.TYPES`），不在此硬编码。
    """
    if not isinstance(t, str):
        return t
    key = _norm_key(t)
    valid = set(engine_vocab().get("energy_types") or ())
    if not valid:                       # 引擎不可用 → 不猜，保持原样
        return t
    if key in valid:
        return key
    return _ENERGY_TYPE_ALIASES.get(key, t)


def normalize_extra(extra):
    """归一化 `actions[].extra` 里可救回的取值字段（计数源 / 能量属性）

    **刻意不做**的：`extra.what` / `extra.kind`（检索/回收的目标种类）——
    实测模型给的是 `from_discard`（那是**来源**，不是种类），属语义不同；
    猜它会把「回收什么」弄错 → 保持原样让引擎如实报未建模。
    """
    if not isinstance(extra, dict):
        return extra
    out = dict(extra)
    if "per" in out:
        out["per"] = normalize_counter(out["per"])
    if "energy_type" in out:
        out["energy_type"] = normalize_energy_type(out["energy_type"])
    return out


def normalize_target(t):
    """把 `action.target` 的自然表达归一化到合法枚举值（**只处理语义唯一者**）

    返回原值当无法确定——绝不猜。（详见 `_TARGET_ALIASES` 上方的边界说明。）
    """
    if not isinstance(t, str):
        return t
    key = t.strip().lower().replace("-", "_").replace(" ", "_")
    if key in TARGETS:
        return key
    return _TARGET_ALIASES.get(key, t)


def normalize_specs(specs: list) -> list:
    """归一化 spec 里可救回的字段 → **返回新对象**

    当前覆盖：`action.target`（别名表）与 `action.extra` 的 `per`（计数源）、
    `energy_type`（能量属性）。各别名表都遵守「**只映射语义唯一者，歧义不猜**」。

    放在 **validate 之前**，且必须是**无副作用**的：`parse_effects` 里 `raw` 与
    `specs` 引用的是**同一批 dict**（`specs = [s for s in raw if isinstance(s, dict)]`
    只筛不拷），若就地改 dict，`raw` 会被一起改掉 —— 而评测
    （`k2_local_eval`）正是用 `raw` 判断「模型有没有真产出结果」。
    **首版就是就地改的，被 `test_llm_target_alias_rescued_on_real_path` 抓出来了。**
    """
    out: list = []
    for s in specs or ():
        if not isinstance(s, dict):
            out.append(s)
            continue
        acts = s.get("actions")
        if not isinstance(acts, list):
            out.append(s)
            continue
        new_acts = []
        for a in acts:
            if not isinstance(a, dict):
                new_acts.append(a)
                continue
            a2 = dict(a)                           # 浅拷贝，不动原对象
            if "target" in a2:
                a2["target"] = normalize_target(a2["target"])
            if "extra" in a2:
                a2["extra"] = normalize_extra(a2["extra"])
            new_acts.append(a2)
        s2 = dict(s)
        s2["actions"] = new_acts
        out.append(s2)
    return out


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
_DISCARD_ENERGY_RE = re.compile(
    r"(?:弃(?:掉)?|移除|去掉|拿掉)[^。；]*?(\d+)\s*个能量")
_HEAL_RE = re.compile(r"回(?:复|收)[^。；]{0,12}?(\d+)\s*点")
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


def _status_word_is_condition(text: str, word: str) -> bool:
    """状态词是否只出现在「If …, this attack does N more damage」这类**条件句**里

    只看词所在的那一句（不跨句），且要求同时满足：
      · 该句以 `if` 开头（条件从句）
      · 该句含 `more damage`（伤害缩放）
    满足两条即视为「条件」，效果层必须让位；否则（如「is now Burned.」）照常产出。
    """
    for sent in re.split(r"(?<=[.!?])\s+", text):
        low_s = _norm_apostrophe(sent).lower()
        if word in low_s and re.search(r"more\s+damage", low_s) \
                and low_s.strip().startswith("if "):
            return True
    return False
# ⚠ 旧的 `_EN_DAMAGE_RE`（`does\s+(\d+)\s+damage`，恒定取第一个且恒标 active_opponent）
# 已于 2026-09-22 删除：它无法区分反伤 / 后备 / 全体目标，会静默算错（详见 `_DMG_ANY_RE`）。
_EN_SPREAD_RE = re.compile(r"(?:put|place)\s+(\d+)\s+damage\s+counters?", re.I)
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
#
# 2026-09-22 扩召回：原正则要求「数字 + 固定名词序」，漏掉真实卡面最常见的两种写法
# （B3 `recover_from_discard` 8 条全落在这里）：
#   · 冠词型：`Put a Trainer card from your discard pile into your hand.`
#   · 组合型：`Put up to 5 in any combination of Pokémon and Basic Energy cards
#              from your discard pile into your hand.`
# → 改为「先定位回收子句，再从**子句内**（不看全文）取数量与类别」。
_EN_RECOVER_RE = re.compile(
    r"put\s+(?:up\s+to\s+)?(?P<n>\d+)?\s*(?P<body>[^.]{0,130}?)"
    r"from\s+your\s+discard\s+pile\s+(?:into|to)\s+your\s+hand", re.I)

# 回收子句里的**子集限定**（「that don't have a Rule Box」「{F} Pokémon」…）：
# 引擎 `recover:N[:kind]` 只能按大类取，无法按子集 → 一律不映射（宁缺勿多算）
_EN_RECOVER_SUBSET_RE = re.compile(
    r"that\s+(?:don[\u2019']?t|do\s+not|doesn[\u2019']?t|does\s+not|has|have|"
    r"is|are)\b|\{\s*[A-Za-z]\s*\}", re.I)


def _en_recover_kind(body: str) -> Optional[str]:
    """回收子句的类别（**只看子句**）→ 引擎 kind：any|pokemon|energy|trainer|supporter|item

    判不出返回 None（绝不兜底成 any —— 那会从弃牌堆取到**错类别**的卡）。
    """
    b = _norm_apostrophe(body or "").lower()
    has_poke = "pokémon" in b or "pokemon" in b
    has_energy = "energy" in b
    if has_poke and has_energy:
        return "any"
    if has_poke:
        return "pokemon"
    if has_energy:
        return "energy"
    if "supporter" in b:
        return "supporter"
    if "trainer" in b:
        return "trainer"
    if " item" in b or b.strip().startswith("item"):
        return "item"
    if re.search(r"\bcards?\b|\bin any combination\b", b):
        return "any"
    return None


_EN_MOVE_RE = re.compile(r"move\s+(?:up to\s+)?(\d+)\s+damage\s+counters?",
                         re.I)
_EN_MOVE_ALL_RE = re.compile(r"move all damage counters", re.I)
# v1.3 未建模、2026-09-22 补上：**磨牌**（Discard the top N cards of your /
# your opponent's deck）。此前整类落进「静态/未建模（英文）」→ 卡不可对战
# （B3 的 `discard_self` 29 条里有 20 条是它）。引擎新增 `mill:N[:opp]`。
_EN_MILL_RE = re.compile(
    r"discard\s+the\s+top\s+(?:(\d+)\s+)?cards?\s+of\s+"
    r"(your\s+opponent[\u2019']?s?|your)\s+deck", re.I)
_EN_SWITCH_RE = re.compile(r"switch\s+(?:your|out|it)|with\s+(?:your|1 of your)\s+"
                           r"benched|switch your active", re.I)
# v1.3：把对手后备拉上前台（Switch in 1 of your opponent's Benched Pokémon）
_EN_GUST_RE = re.compile(
    r"switch in\s+(?:1 of\s+)?(?:your\s+)?opponent|"
    r"switch (?:1 of\s+)?your opponent'?s bench", re.I)
_EN_KNOCKOUT_RE = re.compile(r"(?:is|are|was|were)\s+knocked out|knocked out by",
                             re.I)
_EN_EVOLVE_RE = re.compile(r"\bevolve[sd]?\b|evolution", re.I)
# 「进化」出现在**触发时机从句**里时**不是进化效果**：
#   `When you play this Pokémon from your hand to evolve 1 of your Pokémon, …`
#   `Whenever your opponent plays a Pokémon from their hand to evolve 1 of their Pokémon, …`
# 它描述「何时生效」。早期未做区分 → 给整条特性塞一条 evolve 动作 →
# 落成「招式/特性驱动的进化需卡级 tag」未建模 → **整卡不进池**
# （实测 25 张卡全部栽在这一条上，是本批最大的单一假阳性）。
_EN_EVOLVE_TRIGGER_RE = re.compile(
    r"(?:when|whenever)\b[^.]{0,120}?\bto\s+evolve\b[^.]{0,80}", re.I)
_EN_MODIFY_RE = re.compile(r"weakness|resistance|prevents? all damage|"
                           r"can'?t retreat|cannot retreat|isn'?t affected",
                           re.I)
_EN_BENCH_RE = re.compile(r"bench(?:ed)?\s+pok|onto your bench", re.I)
_EN_HAND_RE = re.compile(r"(?:into|to)\s+your\s+hand", re.I)

# ---------------- 规则提醒括号（**不是**卡面效果） ----------------
# 官方卡面在「对后备造成伤害 / 计算伤害」的效果后固定跟一句**规则复述**：
#   (Don't apply Weakness and Resistance for Benched Pokémon.)
#   (before applying Weakness and Resistance)
# 它们不是效果。但 `_EN_MODIFY_RE` 会命中其中的 "Resistance" →
# 给整条效果塞一条 `rule` 未建模动作 → **整卡不可对战**（准入是「整卡不收」）。
# 实测 2026-09-22：全库 133 张卡含「静态规则修改」未建模条目，其中 **84 张纯属括号假阳性**。
# 中文解析器早就在剔括号注解（`_parse_rule_zh` 的 `t_clean`），英文侧此前漏了。
_RULES_PAREN_RE = re.compile(
    r"\((?:don[\u2019']?t\s+apply|before\s+applying)[^)]*?"
    r"(?:weakness|resistance)[^)]*?\)", re.I)


def _strip_rules_paren(text: str) -> str:
    """剔掉「规则提醒括号」——仅供**语义权威判定**（modify / 触发时机）使用。

    注意：**不改写 `source_text`**。卡面原文照旧落库（差异透明 C5）；
    这里只是在判断「这段文本有没有静态改写效果」时，不把规则复述算进去。
    """
    return _RULES_PAREN_RE.sub(" ", str(text or ""))


# ---------------- 附加伤害子句的目标形态 ----------------
# 历史缺陷（2026-09-22 审计）：早期只取**第一个** `does N damage` 且**恒定**标为
# `active_opponent`。而 `damage` 动作**不产 tag**（只产一条 note）→ 实际后果是：
#   · `does N damage to itself`（反伤，全库 47 处）被记成打对手战斗位 → 反伤**完全消失**
#   · `does N damage to 1 of your opponent's Benched Pokémon` 被记成打战斗位 → 效果消失
#   · `also does N damage to 1 of your **Benched** Pokémon` 被记成打**对手**战斗位
# 更危险的是：若该招式还有别的 tag（如 `discard_energy:2`），卡会被收进可对战池并
# **静默丢效果** —— 本项目最忌的口径问题。实测 12 张卡命中该风险（Greninja ex /
# Mega Charizard Y ex / Ambipom …），故必须先按**子句**判目标，再决定映射或如实登记。
_DMG_ANY_RE = re.compile(
    r"does\s+(\d+)\s+(more\s+)?damage(?:\s+to\s+([^.;,()]{0,90}))?", re.I)


def _en_damage_target_kind(clause: str) -> str:
    """附加伤害子句的目标形态 —— **只在子句内判断**，绝不看全文

    返回 ``opp_active`` / ``self``（反伤）/ ``opp_bench_all`` / ``opp_bench_one`` /
    ``opp_all`` / ``opp_n`` / ``opp_one`` / ``own_bench_all`` / ``own_bench_one`` /
    ``both_bench_all`` / ``dynamic`` / ``own_other`` / ``unknown``。
    """
    c = _norm_apostrophe(clause).lower()
    if re.search(r"\bitself\b", c):
        return "self"
    if "active pok" in c and "bench" not in c:
        return "opp_active"
    if re.search(r"each of your opponent'?s? benched", c):
        return "opp_bench_all"
    if re.search(r"1 of your opponent'?s? benched", c):
        return "opp_bench_one"
    if re.search(r"each of your opponent'?s? pok", c):
        return "opp_all"
    if re.search(r"\d+ of your opponent'?s? pok", c):
        return "opp_n"
    if re.search(r"1 of your opponent'?s? pok", c):
        return "opp_one"
    if re.search(r"each of your benched", c):
        return "own_bench_all"
    if re.search(r"1 of your (?:own )?benched", c):
        return "own_bench_one"
    if re.search(r"each benched", c):
        return "both_bench_all"
    if re.search(r"\bthat pok", c) or re.search(r"the new active", c):
        return "dynamic"
    if re.search(r"your pok", c):
        return "own_other"
    return "unknown"


# ---------------- 「选定目标承受伤害」的统一语法 ----------------
# 真实卡面里「对某只宝可梦造成伤害」的写法很多，但**目标形态**只有几种：
#   does N damage to 1 of your opponent's Pokémon              选 1（含战斗位）
#   does N damage to 2 of your opponent's Pokémon              选 2
#   does N damage to each of your opponent's Pokémon           全部（含战斗位）
#   does N damage to 1 of your opponent's Benched Pokémon      选 1 只后备
#   put N damage counters on each of your opponent's Pokémon   （同上，指示物）
#   … to each of your Benched Pokémon / 1 of your Benched …    己方后备
#   … to each Benched Pokémon (both yours and your opponent's) 双方后备
# 引擎此前只有 `spread:N:per`（**随机** n 只对手后备）与 `spread_free:N`（任意分配，
# 仅对手后备），**没有一种能表达"选定"** → 这一整类只能落进 unsupported。
# 2026-09-22 新增 `snipe:<scope>:<per>[:<count>]` 精确表达（见 engine/battle.py）。
_SNIPE_SCOPES = ("opp", "opp_bench", "own_bench", "both_bench")
# 子集/条件限定词：出现即**不可精确表达**（宁缺勿错）
_SNIPE_SUBSET_RE = re.compile(
    r"\{|\bthat (?:has|have|is|are|don|doesn)\b|\bnamed\b|"
    r"\bex\b|\bv\b|for each|in any way", re.I)


# 子句/句尾的**缩放**条件。⚠ 「for each heads / tails」**不算** —— 那是抛币枚数，
# 由 `_detect_coin` 产出 `coin_each:...:dmg_plus:N`；若在这里也当成"条件未识别"，
# 会把已经正确建模的卡（Team Rocket's Hypno）重新判成未建模并踢出卡池。
_SCALE_TAIL_RE = re.compile(r"for each\s+(?!heads?\b|tails?\b)", re.I)


def _en_snipe_spec(clause: str):
    """附加伤害 / 放置指示物子句 → ``(scope, count)``；不可精确表达 → ``None``

    ``scope``：
      ``"opp_active"`` 打对手战斗位（= 招式 damage 字段承载，**无需 tag**）
      ``"self"``       打自己（反伤 / 自身放置指示物）
      ``"opp"`` / ``"opp_bench"`` / ``"own_bench"`` / ``"both_bench"``  → `snipe:` tag
    其它不可精确表达的形态（子集限定 / 缩放 / 动态目标）返回 ``None``，
    由调用方**如实登记未建模**（宁缺勿错）。
    """
    c = _norm_apostrophe(clause).lower()
    # ⚠ 顺序要紧：先判「战斗位 / 自身」，再判子集 —— 否则
    #   「does 80 damage to your opponent's Active Pokémon for each tails」
    #   会因 "for each" 被判成不可表达（而那本来就是普通主伤害）。
    if "bench" not in c and ("active pok" in c or "defending pok" in c):
        return ("opp_active", None)
    if re.search(r"\bitself\b", c) or "this pok" in c or "your active pok" in c:
        return ("self", None)
    if _SNIPE_SUBSET_RE.search(c):
        return None
    if re.search(r"each of your opponent'?s? benched", c):
        return ("opp_bench", "all")
    if re.search(r"each of your opponent'?s? pok", c):
        return ("opp", "all")
    if re.search(r"each of your (?:own )?benched", c):
        return ("own_bench", "all")
    if re.search(r"each benched", c):
        return ("both_bench", "all")
    m = re.search(r"(\d+)\s+of your opponent'?s? benched", c)
    if m:
        return ("opp_bench", int(m.group(1)))
    m = re.search(r"(\d+)\s+of your opponent'?s? pok", c)
    if m:
        return ("opp", int(m.group(1)))
    m = re.search(r"(\d+)\s+of your (?:own )?benched", c)
    if m:
        return ("own_bench", int(m.group(1)))
    return None


# 「放置伤害指示物」子句（与「造成伤害」是两种写法，语义等价：1 指示物 = 10 点）
_PLACE_COUNTERS_RE = re.compile(
    r"(?:put|place)\s+(\d+)\s+damage\s+counters?\s+(?:on|onto)\s+"
    r"([^.;,()]{0,90})", re.I)


# 冠词型能量弃置：「Discard an Energy from this Pokémon.」——**不写数字**，
# 故 `_EN_DISCARD_ENERGY_RE`（要求 \d+）匹配不到。⚠ 只认「来自自身」的写法：
# 引擎 `discard_energy:N` 的语义是**弃自己战斗位（无则手牌）的能量**，
# 对「弃对手的能量」「弃手牌能量」都会弃错对象 → 那些一律不映射（保持未建模）。
_EN_DISCARD_SELF_ENERGY_RE = re.compile(
    r"discard\s+(?:an?|1)\s+(?:basic\s+|special\s+)?(?:\{\s*[A-Za-z]\s*\}\s*)?"
    r"energy(?:\s+card)?\s+from\s+(?:this|your\s+active|1\s+of\s+your)\s*pok",
    re.I)
# 冠词型「弃**对手**战斗位能量」→ discard_energy:1:opp（引擎已支持该变体，
# 否则会退化成"弃自身"——4 张真实卡曾因此静默弃错对象）
_EN_DISCARD_OPP_ENERGY_RE = re.compile(
    r"discard\s+(?:an?|1)\s+(?:basic\s+|special\s+)?(?:\{\s*[A-Za-z]\s*\}\s*)?"
    r"energy(?:\s+card)?\s+from\s+your\s+opponent",
    re.I)
# 条件加伤：英文条件 → 引擎 `dmg_plus:N:if:<cond>` 的合法条件（白名单内才映射）
_CONDITION_PHRASES = (
    (r"if your opponent has any benched", "opp_has_bench"),
    (r"if you have any benched", "own_has_bench"),
    (r"if you have more prize cards? remaining", "own_behind_prizes"),
    (r"if you have all (?:of )?your prize cards", "own_prizes_full"),
)

def _discard_target_of(text: str, m) -> str:
    """弃牌动作的目标 —— **只看向「弃牌子句」本身**，不看全文

    为什么必须限定子句（真实反例）：Regice ex 的 Ice Prison =
    「Discard 2 Energy **from this Pokémon**, and your opponent's Active Pokémon
    is now Paralyzed.」——弃的是**自身**，但同一句里提到了对手。早期用全文的
    `opponent` 检查判目标 → 判成"弃对手能量"，引擎就去扣对手的能量（静默算错）。
    修法：从匹配处截到**第一个句读**（, . ;），只看这一小段。
    """
    seg = re.split(r"[.,;]", text[m.start():], maxsplit=1)[0].lower()
    if "opponent" in seg:
        return "active_opponent"
    if "from your hand" in seg:
        return "hand"
    return "self"


def _in_coin_branch(text: str, pos: int) -> bool:
    """位置 ``pos`` 是否落在「If heads/tails, …」的**同一分支**内

    为什么需要：抛币分支里的子效果由 coin 层产出（`coin:heads:<sub>`）。
    若解析器**再产出一次无条件动作**，效果就从「正面才生效」变成「必定生效」——
    属实质性加强（口径错误）。护盾早已有同类守卫，弃能量此前没有。

    判据：向前找最近的 `if heads|if tails`，若它与 ``pos`` 之间**没有句号**，
    说明同属一个分支从句。
    """
    head = text[:pos]
    last = None
    for m in re.finditer(r"if\s+(heads|tails)\b", head, re.I):
        last = m
    if last is None:
        return False
    return "." not in head[last.end():]


_EN_OPP_RE = re.compile(r"opponent[\u2019']?s?\b", re.I)

# 能量符号单字母 → 引擎色（数据集的 {G}/{P}… 记号）
_EN_ENERGY_LETTER = {"G": "grass", "R": "fire", "W": "water", "L": "lightning",
                     "P": "psychic", "F": "fighting", "D": "darkness",
                     "M": "metal", "C": "colorless", "N": "dragon",
                     "Y": "psychic"}


def _en_search_what(lower: str) -> str:
    """英文检索目标归类（供训练家/招式检索 tag 选择）

    ⚠ **只看「search … for …」之后的子句**，不看整段文本。
    历史缺陷（2026-09-22）：早期对**全文**判断，于是
    「Search your deck for an **Energy** card and attach it to 1 of your Benched
    {G} **Pokémon**」因为文本里出现 Pokémon 被判成 `search_pokemon`
    → 引擎去牌库拿**宝可梦**（静默拿错东西）。修法：截到「for」之后、
    在第一个 `,` / ` and ` / ` then ` / 句号 处截断，取**名词短语**再判类别。
    """
    seg = lower
    m = re.search(r"search\s+(?:your\s+deck|it)\s+for\s+(.{0,120})", lower)
    if m:
        seg = m.group(1)
    seg = re.split(r",| and | then |\.|\(", seg, maxsplit=1)[0]
    if "pokémon" in seg or "pokemon" in seg:
        return "pokemon"
    if "trainer" in seg:
        return "trainer"
    if "energy" in seg:
        return "energy"
    return "any"


def _parse_rule_en(text: str, kind: str = "attack", name: str = "") -> dict:
    """规则后端（英文卡面）：抽取 spec（伤害/指示物/抽弃/检索/回复/附能/状态/
    互换/击倒/静态规则 + 触发时机）"""
    t = str(text or "")
    actions: list = []
    conditions: list = []
    lower = t.lower()

    # 附加伤害子句（含反伤 `to itself` / 选定目标 / 全体）：目标**必须按子句判**，
    # 判不出可表达的形态时如实登记未建模 —— 绝不静默标成「打战斗位」。
    for md in _DMG_ANY_RE.finditer(t):
        if md.group(2):
            # 「does N **more** damage …」属修正器（缩放/抛币）范畴，不在此产出
            continue
        amt = int(md.group(1))
        clause = md.group(3)
        if not clause:
            actions.append(act("damage", amt, "active_opponent"))
            continue
        # 「条件性附加伤害」判定：可映射的 tag（`self:N` / `snipe:`）都是**无条件**
        # 语义，因此只在子句无条件时才映射。两类条件都不放过：
        #   · 句首条件：「If a Stadium is in play, … also does 30 damage …」（Ting-Lu）
        #   · 子句/句尾缩放：「… for each Prize card your opponent has taken」（Kyurem ex）
        #     —— 若按固定值映射会**静默低估**伤害。
        head = re.split(r"[.!]", t[:md.start()])[-1]
        tail = t[md.end():md.end() + 70]
        conditional = bool(
            re.match(r"\s*(if|during|once|when|whenever|as long as|while)\b",
                     head, re.I)
            or _SCALE_TAIL_RE.search(clause + tail)
            or re.search(r"if you |if this |if your ", clause + tail, re.I))
        sp = None if conditional else _en_snipe_spec(clause)
        if sp is None:
            # 引擎无法精确表达（选定 n 只中的动态形态 / 子集限定 / 带条件 / 带缩放）
            # → 保留伤害数值与目标形态，由 `to_engine_tags` 登记未建模。
            # 这条守卫是「修假阳性不得导致静默丢效果」的关键：一旦某招式含此类子句，
            # 该 item 必有 unsupported → 整卡不收（宁缺勿错）。
            actions.append(act("damage", amt, "active_opponent",
                               {"unaligned_target": clause.strip()[:60],
                                "clause": md.group(0)[:90]}))
        elif sp[0] == "opp_active":
            actions.append(act("damage", amt, "active_opponent"))
        elif sp[0] == "self":
            # 反伤：引擎 `self:N` 的语义即「攻击者自身受到 N 点伤害」→ 精确对应
            actions.append(act("damage", amt, "self", {"recoil": True}))
        else:
            # 选定 / 全体目标 → 引擎 `snipe:<scope>:<per>[:<count>]`
            actions.append(act("damage", amt, "active_opponent_bench",
                               {"snipe_scope": sp[0], "snipe_count": sp[1]}))

    m = _EN_SPREAD_RE.search(t)
    if m:
        # 「放置伤害指示物 on <目标>」——与「造成伤害」语义等价（1 指示物 = 10 点），
        # 目标同样**只看子句**。⚠ 早期实现把整句话都按「随机 n 只对手后备」映射
        # （`spread:N:per`），于是「put 2 damage counters on **1 of your opponent's
        # Pokémon**」「put 9 damage counters on **the Defending Pokémon**」这类全部
        # 算到随机后备身上 —— 口径完全错（池内曾存在 6 条这样的错映射）。
        n = int(m.group(1))
        pcm = _PLACE_COUNTERS_RE.search(t)
        clause = pcm.group(2) if pcm else ""
        head = re.split(r"[.!]", t[:m.start()])[-1]
        tail = t[m.end():m.end() + 70]
        conditional = bool(
            re.match(r"\s*(if|during|once|when|whenever|at the end|as long as)\b",
                     head, re.I)
            or _SCALE_TAIL_RE.search(clause + tail)
            or re.search(r"instead of|if you |if this |if your |"
                         r"that pok|in this way", clause + tail, re.I))
        free_alloc = bool(re.search(r"in any way you like|as you like|"
                                    r"anyway you like", t, re.I))
        sp = None if conditional else _en_snipe_spec(clause)
        if not conditional and free_alloc:
            # 「… on your opponent's **Benched** Pokémon in any way you like」
            # → 引擎 `spread_free:N`（任意分配，**仅对手后备**）—— 精确对应。
            # ⚠ 但「… on your opponent's Pokémon in any way you like」（**含战斗位**）
            #   引擎的 spread_free 只覆盖后备 → 不可代用，如实登记未建模。
            if re.search(r"bench", _norm_apostrophe(clause).lower()):
                actions.append(act("spread", n * 10, "active_opponent_bench",
                                   {"free_distribute": True, "counters": n}))
            else:
                actions.append(act("rule", None, "any", {
                    "note": "放置伤害指示物：任意分配含**战斗位**，"
                            "引擎 spread_free 仅覆盖后备（B4）"}))
        elif sp is None:
            actions.append(act("rule", None, "any",
                               {"note": "放置伤害指示物：目标/条件未识别"
                                        f"（{(clause or t[:40]).strip()[:40]}）"}))
        elif sp[0] == "self":
            # 「put N damage counters on this Pokémon」= 自身受 N×10 点伤害
            actions.append(act("damage", n * 10, "self", {"recoil": True}))
        elif sp[0] == "opp_active":
            actions.append(act("rule", None, "any",
                               {"note": "放置伤害指示物：仅对手战斗位，需引擎"
                                        "新机制（B4）"}))
        else:
            actions.append(act("damage", n * 10, "active_opponent_bench",
                               {"snipe_scope": sp[0], "snipe_count": sp[1]}))

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
        actions.append(act("draw", None, "deck",
                           {"to_hand_size": int(_EN_DRAW_TO_RE.search(t).group(1))}))
    elif re.search(r"draw (?:a|1) card", t, re.I):
        actions.append(act("draw", 1, "deck"))

    # 弃牌堆回收（Put [up to] N / a … from your discard pile into your hand）
    mr = _EN_RECOVER_RE.search(t)
    if mr:
        body = mr.group("body") or ""
        # 数量：显式数字 → 用之；冠词型「a/an/1 + 单数名词」→ 1；
        # 其余（如「a number of cards up to the number of heads」）→ 判不出，不猜
        if mr.group("n"):
            n = int(mr.group("n"))
        elif re.match(r"\s*(?:a|an|1)\s+", body) and "number of" not in body.lower():
            n = 1
        else:
            n = None
        # 变量名用 rk（recover kind）：**不可**用 kind——那会遮蔽函数参数
        # `kind`，使最后 spec(kind=…, …) 写进 "pokemon"/"energy"，触发 Schema
        # 非法（真 bug：Recovery Net 一类卡曾因此报「kind 非法：pokemon」）。
        rk = _en_recover_kind(body)
        if n is None:
            actions.append(act("rule", None, "discard",
                               {"note": "弃牌堆回收：回收数量未识别"}))
        elif _EN_RECOVER_SUBSET_RE.search(body):
            # 子集限定（「that don't have a Rule Box」/「{F} Pokémon」）引擎无法表达
            actions.append(act("rule", None, "discard",
                               {"note": "弃牌堆回收：限定子集未识别"
                                        f"（{body.strip()[:30]}）"}))
        elif rk is None:
            actions.append(act("rule", None, "discard",
                               {"note": "弃牌堆回收：可回收类别未识别"}))
        else:
            actions.append(act("recover", n, "discard", {"kind": rk}))

    # 磨牌（Discard the top N cards of your / your opponent's deck）
    # ⚠ 用 `discard` + `from_deck` 表达而不是新增 action type：Schema 里的
    #   `ACTION_TYPES` 会进提示词，新增类型会牵动提示词长度守卫（无谓的漂移）。
    mm = _EN_MILL_RE.search(t)
    if mm:
        n = int(mm.group(1)) if mm.group(1) else 1
        side = "opp" if "opponent" in mm.group(2).lower() else "own"
        actions.append(act("discard", n, "deck",
                           {"from_deck": True, "side": side}))

    if _EN_DISCARD_ALL_RE.search(t):
        actions.append(act("discard", None, "hand", {"all": True}))
    elif (_m := _EN_DISCARD_OPP_ENERGY_RE.search(t)) and not _in_coin_branch(t, _m.start()):
        # 冠词型「弃对手战斗位能量」——必须带 target，否则 tag 会退化成弃自身。
        # ⚠ 落在 `If heads, …` 分支内时**不在这里产出**（否则从"正面才弃"变成"必定弃"）
        actions.append(act("discard", 1, "active_opponent",
                           {"card_type": "energy"}))
    elif (_m := _EN_DISCARD_SELF_ENERGY_RE.search(t)) and not _in_coin_branch(t, _m.start()):
        # 冠词型「弃自身能量」（无数字）——引擎语义即「弃自己战斗位能量」，可精确对应
        actions.append(act("discard", 1, "self", {"card_type": "energy"}))
    elif _EN_DISCARD_ALL_ENERGY_RE.search(t):
        actions.append(act("discard", None, "self", {"card_type": "energy",
                                                     "all": True}))
    elif (_m := _EN_DISCARD_UPTO_ENERGY_RE.search(t)):
        n = int(_m.group(1))
        actions.append(act("discard", n, _discard_target_of(t, _m),
                           {"card_type": "energy"}))
    elif (_m := _EN_DISCARD_ENERGY_RE.search(t)):
        n = int(_m.group(1))
        actions.append(act("discard", n, _discard_target_of(t, _m),
                           {"card_type": "energy"}))
    elif (_m := _EN_DISCARD_RE.search(t)):
        n = int(_m.group(1))
        # 弃牌（非能量）：同样只看子句 —— 对手手牌 vs 自己手牌
        seg = re.split(r"[.,;]", t[_m.start():], maxsplit=1)[0].lower()
        tgt = "opponent_hand" if ("opponent" in seg and "hand" in seg) else "hand"
        actions.append(act("discard", n, tgt))

    m = _EN_SEARCH_TOP_RE.search(t)
    if m:
        _look = int(m.group(1))
        # ⚠「检视牌库顶 N 张」只有在**去向是入手**时才等于引擎的 `recon:N`
        #（引擎 recon = 顶部 N 张里取 1 入手）。反面例子：
        #   · Morpeko「look at the top card. You may **discard** that card.」
        #   · Metang「look at the top 4 cards and **attach** any number of {M} Energy」
        # 早期一并映射成 recon → 引擎把牌**白送进手牌**（凭空多一张手牌）。
        if re.search(r"(?:into|to)\s+your\s+hand", t[m.end():], re.I):
            actions.append(act("search", 1, "deck", {"look_top": _look}))
        else:
            actions.append(act("rule", None, "any", {
                "note": "检视牌库顶：去向非「入手」（弃置/附着等），"
                        "引擎 recon 仅支持取 1 入手（B4）"}))
    elif (_lm := _EN_LOOK_TOP_ONE_RE.search(t)):
        if re.search(r"(?:into|to)\s+your\s+hand", t[_lm.end():], re.I):
            actions.append(act("search", 1, "deck", {"look_top": 1}))
        else:
            actions.append(act("rule", None, "any", {
                "note": "检视牌库顶：去向非「入手」（弃置/附着等），"
                        "引擎 recon 仅支持取 1 入手（B4）"}))
    elif _EN_SEARCH_RE.search(t):
        g = _EN_SEARCH_RE.search(t).group(1)
        tgt = "discard" if "discard pile" in lower else "deck"
        actions.append(act("search", int(g) if g else 1, tgt,
                           {"what": _en_search_what(lower)}))

    m = _EN_ATTACH_RE.search(t)
    if m and "energ" in lower:
        # ⚠ 排除**触发时机从句**里的 attach：「whenever they attach an Energy card
        #   from their hand to the Defending Pokémon, place 8 damage counters on that
        #   Pokémon」（Pachirisu）描述的是**何时触发**，不是附能效果。
        #   早期给整条效果塞了 `attach:1` → 引擎会**给自己白贴一个能量**
        #   （既错又漏：真正的 8 个指示物同时被丢掉）。
        #   判据：attach 必须紧跟在触发词之后（**跨不过逗号**）才算"条件"——
        #   否则会误杀「When you play this Pokémon …, you may attach …」
        #   这类"上场时附能"的真效果（Bloodmoon Ursaluna）。
        _trig = re.compile(r"(?:when|whenever|if)\b[^.,]{0,40}?\battach\b", re.I)
        _spans = [x.span() for x in _trig.finditer(t)]
        if not any(a <= m.start() < b for a, b in _spans):
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
            # 2026-10-26 修：条件从句里的状态词是**条件**不是效果。
            # 反例（真实卡面）：「If your opponent's Active Pokémon is Burned, this
            # attack does 90 more damage.」——早期实现只做 `word in lower`，于是
            # 既产出了 `dmg_plus:90:if:opp_active_status:burn`，又额外产出一个
            # 无条件的 `burn` 动作 → **把条件当成效果施加**（静默加强）。
            # 判定只看该词所在的**那一句**：以 if 开头且含 more damage 才算条件。
            if _status_word_is_condition(t, word):
                continue
            tgt = "active_opponent" if _EN_OPP_RE.search(t) else "self"
            actions.append(act("status", None, tgt, {"status": tag}))
            break

    if _EN_GUST_RE.search(t):
        # 把对手后备拉上前台（gust）
        actions.append(act("switch", None, "active_opponent_bench"))
    elif (_sm := _EN_SWITCH_RE.search(t)):
        # 换位目标**只看换位子句**（截到句号/换行）。
        # 历史缺陷（2026-09-22）：原先用全文判 `mine`，于是多选项卡
        # 「• Switch your Active Pokémon with 1 of your Benched Pokémon.
        #   • …do 30 more damage to your opponent's Active Pokémon {ex}…」
        # （Kieran）因**另一条**选项提到 opponent → 判成 gust，
        # 把**对手**后备拉上前台（我方换位效果变成干扰对手，完全反了）。
        seg = re.split(r"[.\n]", t[_sm.start():], maxsplit=1)[0][:140]
        opp_here = bool(re.search(r"opponent", seg, re.I))
        mine_here = bool(re.search(r"your|switch out|switch it", seg, re.I))
        if opp_here:
            actions.append(act("switch", None, "active_opponent_bench"))
        elif mine_here:
            actions.append(act("switch", None, "self"))
        else:
            # 双方均可（场地卡）或判不出 → 不猜，如实登记
            actions.append(act("rule", None, "any",
                               {"note": "换位目标未识别（非我方/对手单一侧）"}))

    if _EN_KNOCKOUT_RE.search(t) and not re.search(r"if .{0,40}knocked out",
                                                   lower):
        actions.append(act("knockout", None, "active_opponent"))

    if _EN_EVOLVE_RE.search(t):
        # 逐个匹配判定，**跳过触发时机从句里的 evolve**（见 `_EN_EVOLVE_TRIGGER_RE`）
        spans = [mm.span() for mm in _EN_EVOLVE_TRIGGER_RE.finditer(t)]
        for me in re.finditer(r"evolve (?:it|this|your|1 of|a )", t, re.I):
            if any(a <= me.start() < b for a, b in spans):
                continue
            actions.append(act("evolve", None, "self"))
            break

    if _EN_MODIFY_RE.search(_strip_rules_paren(t)):
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
    elif _EN_MODIFY_RE.search(_strip_rules_paren(t)) or "as long as" in lower:
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
    stadium = _detect_stadium(str(text or ""))
    if stadium:
        actions = list(stadium)
    tools = _detect_tool(str(text or ""))
    if tools:
        # 道具整段文本就是这一条效果 → **整体替换**通用解析结果。
        # 若不替换：诸如「put 12 damage counters on the Attacking Pokémon」会被
        # 通用分支再解析成 spread/其他动作，产出多余 tag 而破坏"道具只认 tool:"
        # 的准入约束（或更糟：把触发式效果当成无条件效果）。
        actions = list(tools)
    mods = _detect_modifiers(str(text or ""), kind, name)
    actions = actions + tools
    if mods:
        # 修正器出现时，移除「静态未建模」兜底 rule（已被真正识别）
        has_coin = any(m.get("type") == "coin_flip" for m in mods)
        drop = list(_RULE_PLACEHOLDER_NOTES) + (
            list(_COIN_COVERED_NOTES) if has_coin else [])
        actions = [a for a in actions
                   if not (a.get("type") == "rule"
                           and _note_starts_with(a, drop))]
        spec["actions"] = actions + mods
    elif tools or stadium:
        # 只有道具/场地时也要写回（否则识别到了却没进 spec —— 实测踩过）
        spec["actions"] = actions
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
                           "条件伤害缩放：条件未识别",
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

# 兜底计数短语：**只放「不可能指对手」的措辞**。
#
# 历史教训（2026-09-21 审计）：本表原是一张「纯子串表」，同时收了我方与对手侧的
# 笼统措辞（`benched pok` / `prize card` / `card in your discard pile` …），
# 结果 `for each of **your** Benched Pokémon` 命中 `benched pok` → 被算成**对手后备数**；
# `Prize card … has taken` 命中 `prize card` → 被算成**剩余**奖赏卡。
# 实测池内 48 条缩放招式里 10 条映射错（阵营反转 / 口径反转 / 子集当总数）。
# → 现在一切有阵营歧义的措辞都走 `_CONCEPT_TABLE` + `_phrase_side`，
#   本表只保留语义上**必然属于我方**的「in this way」「you placed」句式。
_COUNTER_PHRASES = (
    ("damage counter you placed", "own_discarded_this_turn"),
    ("energy card you discarded in this way", "own_discarded_energy_this_turn"),
    ("energy card discarded in this way", "own_discarded_energy_this_turn"),
    ("card you discarded in this way", "own_discarded_this_turn"),
    ("card discarded in this way", "own_discarded_this_turn"),
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


# 专名所有格（Cynthia’s / Iono’s / Ethan’s / Team Rocket’s）→ 「某一小类」而非总数
# 大小写不敏感：短语可能来自小写化的文本，但专名判定不能因此失效。
_SUBSET_NAME_RE = re.compile(r"\b[A-Za-z][A-Za-z\u2019']+[\u2019']s\b")
# 这些所有格**不是**子集限定（限定的是「谁的东西」，双方语义已由阵营判定处理）
_SUBSET_NAME_ALLOWED = ("Your", "You", "Opponent", "The", "This", "Its", "Their",
                        "Defending", "Active", "Benched", "Each", "Both",
                        "Pokémon", "Pokemon", "Attack", "Attack’s")
_SUBSET_STEM_ALLOWED = {s.lower() for s in _SUBSET_NAME_ALLOWED}
# Pokémon 前的类别限定词 → 子集（stage N 单独处理：可表达为 own_bench_stage2）
_SUBSET_POKE_ADJ_RE = re.compile(
    r"\b(ancient|future|tera|rule\s*box|evolution|basic|ex|v|vmax|vstar)\b", re.I)
# 颜色符号 + Pokémon（如 {F} Pokémon）→ 子集（能量颜色可表达，宝可梦颜色不可）
_SUBSET_COLOR_POKE_RE = re.compile(r"\{[A-Za-z]\}\s*pok[eé]mon", re.I)


def _phrase_side(low: str) -> Optional[str]:
    """短语的阵营：``opp`` / ``own`` / None（判不出 → 调用方**不得猜**）

    只看短语里有没有 ``opponent``：卡面凡涉及对手必写 "your opponent's"，
    而 "your"/"you"/"this Pokémon" 归我方。两者都不出现时返回 None —— 例如光秃秃的
    "Benched Pokémon" 无法判断是谁的，早期按 opp 兜底，导致
    「for each of **your** Benched Pokémon」被算成对手后备数（实测 3 张卡受影响）。

    ⚠ ``you`` 与 ``your`` 都算我方：卡面常见 "card **you** discarded" /
    "Prize card **you** have taken"，只认 ``your`` 会把这类判成"无阵营"而放弃。
    """
    if "opponent" in low:
        return "opp"
    if re.search(r"\byour?\b", low) or "this pok" in low:
        return "own"
    return None


def _is_subset_phrase(raw: str, low: str) -> bool:
    """是否在数「某一小类」（子集）→ 不可按总数映射（否则系统性多算）

    三类特征（均为实测踩坑）：
      · 具名所有格：``Cynthia’s Pokémon`` / ``Iono’s Pokémon`` / ``Ethan’s Adventure card``
      · 类别限定：``Ancient`` / ``Future`` / ``Tera`` / ``Rule Box`` / ``Basic`` …
        （``Stage 2`` 例外：引擎有 ``own_bench_stage2``，可精确表达）
      · 颜色符号 + 宝可梦：``{F} Pokémon``（能量颜色可表达，宝可梦颜色不可）
    """
    for m in _SUBSET_NAME_RE.finditer(raw):
        # 比的必须是**词干**（去掉 ’s）：否则 `opponent’s`（我方/对手的合法所有格）
        # 会因为带上了 ’s 而不等于白名单里的 `opponent`，被误判成专名子集。
        stem = re.split(r"[\u2019']", m.group(0))[0].strip().lower()
        if stem and stem not in _SUBSET_STEM_ALLOWED:
            return True
    if _SUBSET_COLOR_POKE_RE.search(raw):
        return True
    if _SUBSET_POKE_ADJ_RE.search(low) and not re.search(r"stage\s*2", low):
        return True
    return False


# 「伤害指示物」计数：按**部位**细分（总数与部位不可混用）
_COUNTER_ON_PHRASES = (
    ("damage counter on this pok", "own_active_counters"),
    ("damage counter on your active", "own_active_counters"),
    ("damage counter on your opponent's active", "opp_active_counters"),
    ("damage counter on your opponent", "opp_active_counters"),
    ("damage counter on all of your benched", "own_bench_counters"),
    ("damage counter on your benched", "own_bench_counters"),
)

# 阵营感知的概念表：正则 → (我方计数源, 对手计数源)。对手侧为 None = 引擎无此源
# （如「对手手牌数」不可见、「对手弃牌堆」引擎未建），**如实返回 None 而非硬套我方值**。
_CONCEPT_TABLE = (
    (r"benched pok", "own_bench", "opp_bench"),
    (r"pok[eé]mon in play", "own_field", "opp_field"),
    (r"pok[eé]mon in play|on your field", "own_field", "opp_field"),
    (r"stage 2 pok", "own_bench_stage2", None),
    (r"card in your hand|cards in your hand", "own_hand", None),
    (r"card in your discard pile|cards in your discard pile",
     "own_discard_count", None),
    (r"energy in your discard pile|energy card in your discard pile",
     "own_discard_energy", None),
    (r"prize card", None, None),          # 统一走下表的 taken/left 细分
)
# 奖赏卡：**「已取得」与「剩余」口径相反**，必须分开
_PRIZE_TAKEN_RE = re.compile(r"prize cards?[^.]{0,24}?\b(taken|took)\b", re.I)
_PRIZE_LEFT_RE = re.compile(r"prize cards?[^.]{0,24}?\b(remaining|left)\b", re.I)


def _counter_from_phrase(phrase: str) -> Optional[str]:
    """英文短语 → 引擎计数源（未识别 → None，调用方如实标注不支持）

    **三条硬约束**（每条都由实测的错映射换来的）：
      ① **阵营**：用 `_phrase_side` 定 own/opp；判不出就 None，绝不兜底成某一方；
      ② **子集**：具名/类别/颜色限定 → None（总数 ≠ 子集，笼统映射会系统性多算）；
      ③ **口径**：「已取得奖赏卡」≠「剩余奖赏卡」，分别映射到 `*_prizes_taken`
         / `*_prizes_left`（早期按 left 映射导致伤害随对手取卡而**反向变化**）。
    （``energy card in your discard pile`` 例外：限定词就是能量本身，等价于总能量数。）
    """
    raw = _norm_apostrophe(phrase)
    low = raw.lower()
    side = _phrase_side(low)

    # ③ 奖赏卡：先判口径（taken/left），再按阵营取源
    if "prize card" in low:
        # 「上一回合取走的奖赏卡」是**回合范围内**的计数，引擎无此口径 → 如实放弃
        if re.search(r"(last|previous|their|your)\s+turn", low):
            return None
        if _PRIZE_TAKEN_RE.search(low):
            return {"own": "own_prizes_taken", "opp": "opp_prizes_taken"}.get(side)
        if _PRIZE_LEFT_RE.search(low):
            return {"own": "own_prizes_left", "opp": "opp_prizes_left"}.get(side)
        return None

    # ② 子集限定 → 不可按总数算
    if _is_subset_phrase(raw, low):
        return None
    if _FILTERED_DISCARD_RE.search(low) and "energy" not in low and \
            "discard" in low:
        return None

    # 能量相关：颜色限定可表达（<zone>_energy_<color>）
    mcolor = re.search(r"\{([A-Za-z])\}\s*energy", raw, re.I)
    if "energy attached" in low or "energy card" in low:
        zone = _energy_zone_from_phrase(low)
        color = _EN_SYMBOL_COLOR.get((mcolor.group(1).upper() if mcolor else ""))
        if zone == "own_discard":
            return None if color else "own_discard_energy"
        if zone:
            return f"{zone}_energy" + (f"_{color}" if color else "")

    # 伤害指示物：按部位
    for needle, counter in _COUNTER_ON_PHRASES:
        if needle in low:
            return counter

    # 通用概念表（阵营感知）
    for pat, own, opp in _CONCEPT_TABLE:
        if re.search(pat, low):
            got = {"own": own, "opp": opp}.get(side)
            if got:
                return got
            break

    # 兜底：**天然属于我方**的句式（不含阵营歧义）。
    # ⚠ 这里只允许放「不可能指对手」的措辞（"you discarded" / "you placed"）——
    #   任何可能被对手侧使用的措辞（benched pok / prize card / card in discard pile…）
    #   都必须走上面的阵营感知分支，否则会重演「your Benched → opp_bench」的错映射。
    for needle, counter in _COUNTER_PHRASES:
        if needle in low:
            return counter
    return None


def _energy_zone_from_phrase(low: str) -> Optional[str]:
    """能量计数短语 → 引擎能量 zone（未识别 → None）

    ⚠ **顺序即语义**：``attached to all of your opponent's Pokémon`` 同时含有
    ``attached to all of your``，若先判我方会把对手侧算成我方（实测 Leafeon ex /
    Delcatty 两张卡因此按我方能量数加伤）。故**所有 opponent 分支必须排在前面**。
    """
    if "attached to this pok" in low or "attached to your active" in low:
        return "own_active"
    if "attached to your opponent's active" in low:
        return "opp_active"
    if "attached to your opponent" in low:       # 泛指对手（active 之外）
        return "opp_active"
    if "attached to both active" in low:
        return "both_active"
    if "attached to all of your opponent" in low or "attached to all opponent" in low:
        return "opp_all"
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


def _condition_from_text(text: str) -> Optional[str]:
    """英文条件从句 → 引擎 `dmg_plus:N:if:<cond>` 的合法条件（未识别 → None）

    只在白名单内映射；引擎没有的条件（场地 / 曾打出某卡 / 对手宝可梦属性 /
    「额外 N 个能量（在招式费用之外）」…）一律返回 None，由调用方登记为未建模。

    **带参数条件**（2026-09-22 新增）：卡面最常见的能量条件
    「If this Pokémon has any {L} Energy attached, this attack does 80 more damage」
    → `own_active_energy_type:lightning`（引擎侧按色计数，精确对应）。
    ⚠ 与之形近但**不可**近似的两种写法**故意不映射**（宁缺勿错）：
      · `has at least N extra Energy attached (in addition to this attack's cost)`
        —— 条件里含**招式费用**，用「总数 ≥ N」会提前触发（静默加强）
      · `has any Special Energy attached` —— 引擎无「特殊能量」计数源
    """
    low = _norm_apostrophe(text).lower()
    m = re.search(r"if this pok[eé]mon has any \{\s*([a-z])\s*\}\s*energy attached",
                  low)
    if m:
        color = _EN_SYMBOL_COLOR.get(m.group(1).upper())
        return f"own_active_energy_type:{color}" if color else None
    # 「has at least N **extra** Energy attached」→ 含费用，不映射
    if re.search(r"extra\s+energy\s+attached", low):
        return None
    m = re.search(r"if this pok[eé]mon has any energy attached", low)
    if m:
        return "own_active_energy_at_least:1"
    for pat, cond in _CONDITION_PHRASES:
        if re.search(pat, low):
            return cond
    return _condition_from_clause(text)


# ---- 第四批（2026-10-26）：条件伤害缩放的高频条件 ----
# 卡面原型：「If <条件>, this attack does N more damage.」
# 设计要求：条件必须能由**当下局面**直接读出（不依赖「本回合是否被治疗 / 是否从后备换入」
# 这类引擎没有的回合级历史），否则宁可返回 None 由调用方登记未建模。
_STATUS_WORD_TO_TAG = {"burned": "burn", "poisoned": "poison", "asleep": "sleep",
                       "paralyzed": "paralyze", "confused": "confuse"}
# 句子边界：只取「含 more damage 的那一句」做条件匹配 ——
# 同一张卡可能有两个「If …」从句（如「If A, 招式伤害 +N。If B, 再 +M。」），
# 用全文匹配会把 B 的条件套到 A 的加伤上（**静默错映射**，比漏建模更糟）。
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
# 「this attack does N more damage」（条件加伤从句）——逐个从句处理，见 _detect_modifiers
_MORE_DMG_RE = re.compile(r"does\s+(\d+)\s+more\s+damage", re.I)


def _sentence_at(text: str, pos: int) -> str:
    """取 `pos` 所在的那一句（用于把条件限定在本从句内，避免跨句串味）"""
    start = 0
    for m in _SENT_SPLIT_RE.finditer(text):
        if m.end() > pos:
            return text[start:m.start()]
        start = m.end()
    return text[start:]


def _clause_around_more_damage(text: str) -> str:
    """取「含 more damage 的那一句」；取不到则回退全文（保持旧行为）"""
    sentences = _SENT_SPLIT_RE.split(text)
    for s in sentences:
        if re.search(r"more\s+damage", s, re.I):
            return s
    return text


def _condition_from_clause(text: str) -> Optional[str]:
    """第四批条件表（在**单句**范围内匹配，避免跨句串味）"""
    seg = _norm_apostrophe(_clause_around_more_damage(text)).lower()

    opp_active = r"if your opponent'?s active pok[eé]mon"

    # 1) 对手战斗位是 ex —— 注意「{ex} or {V}」是**并集**，引擎无法区分 V，故意不映射
    if re.search(opp_active + r" is a pok[eé]mon \{ex\}", seg) \
            and not re.search(r"\{ex\}\s*or\s+pok[eé]mon\s*\{v\}", seg):
        return "opp_active_is_ex"

    # 2) 对手战斗位是进化宝可梦
    if re.search(opp_active + r" is an evolution pok[eé]mon", seg):
        return "opp_active_is_evolution"

    # 3) 对手战斗位已有伤害指示物
    if re.search(opp_active + r" (?:already )?has any damage counters? on it", seg):
        return "opp_active_has_damage"

    # 4) 自身战斗位有伤害指示物（「this Pokémon」= 攻击方，引擎语义 own_active）
    if re.search(r"if this pok[eé]mon has any damage counters? on it", seg):
        return "own_active_has_damage"

    # 5) 对手战斗位处于某特殊状态
    m = re.search(opp_active + r" is (burned|poisoned|asleep|paraly[sz]ed|confused)", seg)
    if m:
        word = m.group(1).replace("paralysed", "paralyzed")
        tag = _STATUS_WORD_TO_TAG.get(word)
        return f"opp_active_status:{tag}" if tag else None

    # 6) 对手战斗位是某属性的宝可梦（{P} / {D} …）
    m = re.search(opp_active + r" is an? \{([a-z])\} pok[eé]mon", seg)
    if m:
        color = _EN_SYMBOL_COLOR.get(m.group(1).upper())
        return f"opp_active_type:{color}" if color else None

    # 7) 对手后备数量下限
    m = re.search(r"if your opponent has (\d+) or more benched pok[eé]mon", seg)
    if m:
        return f"opp_bench_at_least:{int(m.group(1))}"

    # 8) 我方全场能量下限（「you have N or more Energy in play」）
    m = re.search(r"if you have (\d+) or more energy in play", seg)
    if m:
        return f"own_all_energy_at_least:{int(m.group(1))}"
    return None


_TOOL_HOLDER_RE = re.compile(r"this card is attached to", re.I)


def _detect_stadium(text: str) -> list:
    """场地（Stadium）静态修正识别 → ``stadium:*`` 动作（S3 · 2026-10-27）

    只认**全场静态修正**两类（其余形态需要"每回合可选动作 / checkup 钩子 /
    规则改写"，本轮尚未实现）：
      · ``Each Basic Pokémon in play (both yours and your opponent's) gets +30 HP.``
        → ``stadium:hp_plus:30:stage:0``
      · ``Each Stage 2 Pokémon in play … gets -30 HP.``
        → ``stadium:hp_plus:-30:stage:2``
      · ``{M} Pokémon (both yours and your opponent's) take 30 less damage from
         attacks from the opponent's Pokémon …`` → ``stadium:dmg_reduce:30:type:metal``
    ⚠ 带**卡名阵营**限定（Steven's / N's / Hop's / Team Rocket's）一律放弃。
    """
    t = _norm_apostrophe(text)
    low = t.lower()

    # 卡名/阵营限定 → 引擎无卡名归属元数据
    if re.search(r"\b(steven|n|hop|marnie|erika|team rocket)'?s\s+pok[eé]mon", low):
        return []

    # C 每回合一次的主动使用（S3 第二步 2026-10-27）
    if re.search(r"once during each player'?s turn", low):
        # C1 Academy at Night：把手牌 1 张放到牌库顶
        if re.search(r"put a card from their hand on top of their deck", low):
            return [act("stadium", None, "any", {"kind": "use",
                                                 "use_kind": "top_deck"})]
        # C2 Levincia：弃牌堆取回 ≤2 张基本能量
        m = re.search(r"put up to (\d+) basic \{([a-z])\} energy cards? from "
                      r"their discard pile into their hand", low)
        if m:
            color = _EN_SYMBOL_COLOR.get(m.group(2).upper())
            if color:
                return [act("stadium", int(m.group(1)), "any",
                            {"kind": "use", "use_kind": "recover_energy",
                             "ptype": color})]
        # C3 Community Center：本回合用过支援者则可回复
        m = re.search(r"if they played a supporter card from their hand this turn, "
                      r"they may heal (\d+) damage from each of their pok[eé]mon", low)
        if m:
            return [act("stadium", int(m.group(1)), "any",
                        {"kind": "use", "use_kind": "heal_all",
                         "require": "supporter"})]
        # 其余「每回合一次」形态（检索/换位/弃牌换抽）需要目标选择或额外代价，
        # 本轮不建模 —— 让整卡落进未建模，而不是硬套成上面三种。
        return []

    # A 上限修正（按阶段）
    m = re.search(r"each (basic|stage 1|stage 2) pok[eé]mon in play[^.]*?"
                  r"gets ([+-]\d+) hp", low)
    if m:
        stage = {"basic": 0, "stage 1": 1, "stage 2": 2}[m.group(1)]
        return [act("stadium", int(m.group(2)), "any",
                    {"kind": "hp_plus", "stage": stage})]

    # B 减伤（按属性）
    m = re.search(r"\{([a-z])\}\s*pok[eé]mon \(both yours and your opponent'?s\)"
                  r" take (\d+) less damage", low)
    if m:
        color = _EN_SYMBOL_COLOR.get(m.group(1).upper())
        if color:
            return [act("stadium", int(m.group(2)), "any",
                        {"kind": "dmg_reduce", "ptype": color})]
    return []


def _detect_tool(text: str) -> list:
    """道具（Pokémon Tool）识别 → ``tool:*`` 动作（S2 · 2026-10-26）

    两类形态：
      A. **静态**（附着即常驻）
      B. **触发**（S2 第二阶段）：被招式伤害时 / 满 HP 将被击倒时
    """
    return (_detect_tool_hook(text) or _detect_tool_static(text))


def _detect_tool_hook(text: str) -> list:
    """触发式道具识别（S2 第二阶段 · 2026-10-26）

    只认**单一效果、且不依赖额外限定**的两类钩子：
      · ``… is in the Active Spot and is damaged by an attack …, draw N cards.``
        → ``tool:on_damaged:draw:N``（Lucky Helmet）
      · ``… put N damage counters on the Attacking Pokémon.``（可选尾句
        ``If you placed any damage counters in this way, discard this card.``）
        → ``tool:on_damaged:counter:<N*10>[:discard]``（Deluxe Bomb）
      · ``If … has full HP and would be Knocked Out by damage …, it is not
        Knocked Out, and its remaining HP becomes M.``
        → ``tool:survive:<M>``（Survival Brace）

    ⚠ 故意不认：带**持有者属性/卡名限定**的（``the {D} Pokémon`` /
    ``the Team Rocket's Pokémon``）、以及换能量 / 移能量 / 检索类触发效果 ——
    这些要么需要"攻击方属性"过滤层，要么需要跨侧移动，本轮都还没有。
    """
    t = _norm_apostrophe(text)
    low = t.lower()
    if not _TOOL_HOLDER_RE.search(low):
        return []
    # 持有者限定：只支持**属性**（`the {D} Pokémon this card is attached to`）。
    # 卡名/阵营限定（`the Team Rocket's Pokémon` / `the Hop's Pokémon`）需要按卡名
    # 归属判定，引擎没有这套元数据 → 一律放弃。
    holder_color = None
    mh = re.search(r"the\s+\{([a-z])\}\s*pok[eé]mon this card is attached to", low)
    if mh:
        holder_color = _EN_SYMBOL_COLOR.get(mh.group(1).upper())
        if not holder_color:
            return []
    elif re.search(r"the\s+(?!retreat cost)(?!cost)[a-z][a-z' ]{2,24}"
                   r"pok[eé]mon this card is attached to", low):
        return []

    # B1 满 HP 免击倒（Survival Brace）
    m = re.search(r"has full hp and would be knocked out", low)
    if m:
        m2 = re.search(r"remaining hp becomes (\d+)", low)
        if m2:
            return [act("tool", int(m2.group(1)), "self", {"kind": "survive"})]
        return []

    # 触发前提：在战斗位 + 被对手招式伤害
    if not re.search(r"is in the active spot and is damaged by an attack", low):
        return []

    # B2 受伤时抽牌（Lucky Helmet）
    m = re.search(r"damaged by an attack[^.]{0,80}?draw (\d+) cards?", low)
    if m:
        return [act("tool", int(m.group(1)), "self",
                    {"kind": "on_damaged_draw"})]

    # B3b 受伤时把**攻击方的 1 个能量移到其后备**（Handheld Fan）
    #   卡面：「move an Energy from the Attacking Pokémon to 1 of your opponent's
    #   Benched Pokémon」——"your opponent" 指道具持有者的对手 = 攻击方本人，
    #   所以是「攻击方 → 攻击方自己的后备」，不是跨方移动。
    if re.search(r"move an energy from the attacking pok[eé]mon", low):
        return [act("tool", 1, "self", {"kind": "on_damaged_move_energy"})]

    # B3 受伤时把伤害指示物放到攻击方（Deluxe Bomb）
    m = re.search(r"(?:put|place) (\d+) damage counters? on the attacking pok[eé]mon",
                  low)
    if m:
        extra = {"kind": "on_damaged_counter",
                 "holder_type": holder_color}
        if re.search(r"discard this card", low):
            extra["discard"] = True
        return [act("tool", int(m.group(1)) * 10, "self", extra)]
    return []


def _detect_tool_static(text: str) -> list:
    """道具静态修正识别（S2 第一步）

    **只认"附着后常驻、且不依赖任何额外条件"的形态**：
      · ``The Pokémon this card is attached to gets +N HP.`` → ``tool:hp_plus:N``
      · ``The Retreat Cost of the Pokémon this card is attached to is {C} less.``
        → ``tool:retreat_less:<{C} 的个数>``

    ⚠ 故意不认（宁可未建模）：
      * 带对手属性/特性限定的减伤（``from your opponent's {P} Pokémon``）——
        引擎没有"攻击方属性/特性"这一层过滤；
      * 带自身状态前置的（``If that Pokémon's remaining HP is 30 or less``）；
      * 触发式道具（被击倒/受伤时…）—— 需要事件钩子，属 S2 第二阶段。
    这类一律返回空表，由调用方如实登记未建模。
    """
    t = _norm_apostrophe(text)
    low = t.lower()
    if not _TOOL_HOLDER_RE.search(low):
        return []
    out: list = []
    m = re.search(r"this card is attached to gets \+(\d+)\s*hp", low)
    if m:
        out.append(act("tool", int(m.group(1)), "self", {"kind": "hp_plus"}))
    m = re.search(r"retreat cost of the pok[eé]mon this card is attached to is "
                  r"((?:\{\s*[a-z]\s*\}\s*)+)less", low)
    if m:
        n = len(re.findall(r"\{[a-z]\}", m.group(1)))
        if n:
            out.append(act("tool", n, "self", {"kind": "retreat_less"}))
    # 持有者属性限定（`The {N} Pokémon this card is attached to …`）
    holder_color = None
    mh = re.search(r"the \{([a-z])\} pok[eé]mon this card is attached to", low)
    if mh:
        holder_color = _EN_SYMBOL_COLOR.get(mh.group(1).upper())
    elif re.search(r"the\s+(?!retreat cost)(?!cost)[a-z][a-z' ]{2,24}"
                   r"pok[eé]mon this card is attached to", low):
        # 卡名/阵营限定（`The Team Rocket's Pokémon this card is attached to`）：
        # 引擎没有"卡名归属"元数据 → 不能当成普通道具。**必须整卡放弃**，
        # 否则「+30 HP」会变成任何宝可梦附着都生效（实测踩过）。
        return []

    # C1 浆果类：**条件性一次性**减伤（被特定属性攻击时 −N，然后弃置本卡）
    #   「If the Pokémon this card is attached to is damaged by an attack from your
    #     opponent's {P} Pokémon, it takes 60 less damage …, and discard this card.」
    #   与常驻减伤的净效果等价（只在攻击方是该属性时生效），故映射为带 from 的减伤。
    m = re.search(r"if the pok[eé]mon this card is attached to is damaged by an "
                  r"attack from your opponent'?s? \{([a-z])\} pok[eé]mon, it takes "
                  r"(\d+) less damage", low)
    if m:
        color = _EN_SYMBOL_COLOR.get(m.group(1).upper())
        if color:
            extra = {"kind": "dmg_reduce", "from_type": color,
                     "discard": bool(re.search(r"discard this card", low))}
            if holder_color and holder_color != color:
                extra["holder_type"] = holder_color
            return [act("tool", int(m.group(2)), "self", extra)]

    # C2 「from … Pokémon that have an Ability」→ 只对**有特性**的攻击方减伤
    m = re.search(r"takes (\d+) less damage from attacks from your opponent'?s? "
                  r"pok[eé]mon that have an ability", low)
    if m:
        extra = {"kind": "dmg_reduce", "from_ability": True}
        if holder_color:
            extra["holder_type"] = holder_color
        return [act("tool", int(m.group(1)), "self", extra)]

    # C3 「from your opponent's {G}, {R}, {W}, or {L} Pokémon」→ 攻击方属性集合
    m = re.search(r"takes (\d+) less damage from attacks from your opponent'?s? "
                  r"((?:\{[a-z]\}(?:\s*,\s*|\s*or\s*|\s*)+)+)pok[eé]mon", low)
    if m:
        colors = [_EN_SYMBOL_COLOR.get(c.upper()) for c in re.findall(r"\{([a-z])\}", m.group(2))]
        colors = [c for c in colors if c]
        if colors:
            extra = {"kind": "dmg_reduce", "from_types": colors}
            if holder_color:
                extra["holder_type"] = holder_color
            return [act("tool", int(m.group(1)), "self", extra)]

    # ⚠ 只认「纯静态」整卡文本：只要还有条件/触发从句（if / when / during / …），
    #   就说明这张道具**还有别的作用没被建模**（例：Rescue Board 在剩余 HP ≤30 时
    #   额外「无撤退费用」）。此时若仍返回静态 tag，卡会被准入并**静默丢掉那段效果**
    #   —— 属"部分实现却标成已实现"，比不建模更糟。这里整体放弃，如实登记。
    if out and re.search(r"\b(if|when|whenever|during|after|at the end|"
                         r"as long as|unless|instead)\b", low):
        return []
    return out


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
    elif not _COIN_RE.search(t) and _MORE_DMG_RE.search(t):
        # 「If <条件>, this attack does N more damage」——**只在条件落到引擎白名单时映射**。
        # ⚠ 条件不在白名单（场地在场上 / 曾打出某张卡 / 对手宝可梦的属性 …）时必须放弃：
        #   若退化成无条件的 `dmg_plus:N` 会凭空加伤，属**静默算错**，比"未建模"更糟。
        # ⚠ 文本含抛币时必须让位（「Flip a coin. If heads, … does N more damage」
        #   已由 `_detect_coin` 产出 `coin:heads:dmg_plus:N`；此处再报一次"条件未识别"
        #   会变成重复计入未建模 —— 实测 test_coin_heads_plus_damage 因此失败）。
        # ⚠ **逐个从句**处理（2026-10-26）：同一张卡可能有两个「If …, does N more damage」
        #   （Kieran 式）。早期只 `re.search` 取第一个 → 第二条加伤**被静默丢弃**，
        #   卡却被当作"已建模"收进卡池（少算伤害，且无任何 unsupported 提示）。
        #   现在每个从句各自找条件：任一从句条件不可表达 → 该从句计入 unsupported，
        #   整卡准入自然被拒（宁可未建模，不可少算）。
        for mm in _MORE_DMG_RE.finditer(t):
            n = int(mm.group(1))
            seg = _sentence_at(t, mm.start())
            cond = _condition_from_text(seg) or _condition_from_text(t)
            if cond:
                out.append(act("damage_mod", n, "active_opponent",
                               {"mode": "plus_if", "cond": cond}))
            elif _KO_LAST_TURN_RE.search(seg) or re.search(
                    r"were\s+knocked\s+out", seg, re.I):
                out.append(act("damage_mod", n, "active_opponent",
                               {"mode": "plus_if", "cond": "opp_ko_last_turn"}))
            else:
                out.append(act("rule", None, "active_opponent",
                               {"note": "条件伤害缩放：条件未识别（引擎无该条件）"}))

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

    # 3b) 静态修正（S1 静态修正层 · 2026-10-26）
    #     与上一段的 `shield:*` **必须严格区分**：
    #       · 「During your opponent's next turn, this Pokémon takes 50 less damage」
    #         → 时限制护盾 `shield:reduce:50`（在对手下个回合结束时失效）
    #       · 「This Pokémon takes 30 less damage from attacks (after applying
    #          Weakness and Resistance).」→ 常驻 `passive:dmg_reduce:30`
    #     把常驻写成护盾（或反之）都会让减伤在错误的时间窗生效 —— 静默错算。
    if not _opp_next_turn(t):
        if mred and re.search(r"this pok[eé]mon takes", low):
            out.append(act("passive", int(mred.group(1)), "self",
                           {"kind": "dmg_reduce"}))
        if re.search(r"this pok[eé]mon has no weakness", low):
            out.append(act("passive", None, "self", {"kind": "no_weakness"}))
        if re.search(r"this pok[eé]mon has no retreat cost", low):
            out.append(act("passive", None, "self", {"kind": "no_retreat"}))
        mhp = re.search(r"this pok[eé]mon gets? \+(\d+)\s*hp", low)
        if mhp:
            out.append(act("passive", int(mhp.group(1)), "self",
                           {"kind": "hp_plus"}))

    # 4) 抽到指定手牌数
    m = _DRAW_UNTIL_RE.search(t)
    if m and kind == "ability":
        out.append(act("draw", None, "deck",
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
        # 目标是**对手**时用 `:opp` 变体（引擎会弃对手战斗位能量）；
        # 不带目标信息的 `discard_energy:N` 语义是「弃自身」，会弃错对象。
        opp = bool(_EN_OPP_RE.search(text))
        out.append(act("coin_flip", None, "active_opponent",
                       {"side": "heads", "mode": "single",
                        "sub": "discard_energy_opp" if opp else "discard_energy",
                        "sub_amount": 1}))
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
            actions.append(act("draw", None, "deck",
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
    # target：出现「对手」→ 回复对手宝可梦（active_opponent）；否则自身。
    # （此前一律 self，对「回复对手…」类卡面是错的；K2 待补句式 ho_heal_hp 暴露了
    #   这一分支的另一个问题：数字须允许与「回复」之间隔着受事对象，
    #   如「回复自己宝可梦 30 点 HP」——见 _HEAL_RE。）
    m = _HEAL_RE.search(t_norm)
    if m:
        tgt = "active_opponent" if "对手" in t_norm else "self"
        actions.append(act("heal", int(m.group(1)), tgt))

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

    # 互换 / 换下 / 交换（自己 → self；对手/未指明 → active_opponent_bench）
    # 「交换」是「互换」的常见同义写法（K2 待补句式 ho_swap 即因只认「互换」而失配）。
    if any(k in t_norm for k in ("互换", "换下", "交替", "交换")):
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
# 配置与调用统一收在 `agent.llmclient`（含**运行时注入点**：后台配置的云端模型
# 由 apps/api 注入，优先于环境变量）。此处仅做**向后兼容再导出**，避免既有
# 调用方（apps/api、测试）因搬迁而破坏。
from .llmclient import LLMConfigError, chat as _llm_chat  # noqa: E402


def llm_config() -> dict:
    """取生效的 LLM 配置（后台配置优先 → 环境变量兜底）

    POKELAB_LLM_BASE 例：https://api.deepseek.com/v1  或  http://127.0.0.1:9997/v1
    POKELAB_LLM_KEY  API Key（本地服务可留空）
    POKELAB_LLM_MODEL 模型名（默认 deepseek-chat）
    POKELAB_LLM_TIMEOUT 超时秒数（默认 60）
    """
    from . import llmclient as _c
    return _c.config()


def llm_config_source() -> str:
    """配置来源：``runtime``（后台）/ ``env`` / ``none``"""
    from . import llmclient as _c
    return _c.source()


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


_ENGINE_VOCAB: dict = {}


def engine_vocab() -> dict:
    """引擎侧取值域（计数源 / 条件 / 能量区 / 被动形态）—— **动态读取，不硬编码**

    为什么这样做：K2 提示词必须告诉模型 `dmg_plus:N:per:<counter>` 里的 `<counter>`
    能填什么。若把取值**手抄进提示词**，引擎加了新计数源而提示词忘了同步，就会出现
    「模型按提示词填了、引擎却抛错」的错配 —— 本项目已因「提示词与代码不同步」踩过
    一次（旧 `pokelab/effects.py` 的枚举写成引号内 `a|b|c`，模型照抄导致整条作废）。
    → 故取值域只在引擎侧定义一次（`engine.battle.dmg_vocab()`），渲染时注入。

    缓存 + 容错：取值域是静态的，而 `parse_llm` 是热路径（每条都要渲染提示词）；
    引擎不可用时返回空 dict —— 提示词退化为「见引擎文档」，而不是让整条解析链挂掉。
    """
    if _ENGINE_VOCAB:
        return _ENGINE_VOCAB
    try:
        import sys as _sys
        eng = str(REPO / "packages" / "engine")
        if eng not in _sys.path:
            _sys.path.insert(0, eng)
        from engine import battle as _battle              # noqa: E402
        _ENGINE_VOCAB.update(_battle.dmg_vocab())
        # 能量属性名也取自引擎（`engine.cards.TYPES`）—— 与「单一来源」原则一致：
        # 引擎加了新属性，归一化自动跟上，不会出现「提示词/归一化表里还是旧列表」。
        from engine import cards as _cards                # noqa: E402
        _ENGINE_VOCAB["energy_types"] = list(_cards.TYPES)
    except Exception:                                     # noqa: BLE001
        pass
    return _ENGINE_VOCAB


def _vocab_text(key: str) -> str:
    """把某类取值域渲染成提示词片段；缺数据时给**明确占位**（而非空白枚举）"""
    vals = engine_vocab().get(key) or []
    return "、".join(vals) if vals else "（引擎未提供，遇此类效果请在 extra.note 说明）"


def render_system_prompt(retrieved_rules: str = "") -> str:
    """渲染 K2 系统提示词 —— 枚举与取值域**从代码/引擎注入**

    抽成独立函数是为了**可离线测试**：不起 LLM 服务就能验证
    「枚举是否完整注入」「示例的 JSON 是否真能被引擎映射」。否则这类问题
    只能在真跑模型时暴露，而那时已经烧掉几十分钟 GPU 时间。
    """
    from . import prompts as _prompts
    return _prompts.render(
        "parse_effects_system",
        strict=True,                # 残留占位符 = 提示词里出现乱码，必须报错
        retrieved_rules=retrieved_rules or "（无）",
        # 枚举表：单一来源 = 本模块常量（避免 .md 与校验层漂移）
        enum_kind=" · ".join(KINDS),
        enum_trigger=" · ".join(TRIGGERS),
        enum_cond_op=" · ".join(COND_OPS),
        enum_action_type=" · ".join(ACTION_TYPES),
        enum_target=" · ".join(TARGETS),
        enum_status=" · ".join(ENGINE_STATUS_TAGS),
        # 取值域：单一来源 = 引擎（避免 .md 与引擎漂移）
        vocab_counters=_vocab_text("counters"),
        vocab_conditions=_vocab_text("conditions"),
        vocab_energy_zones=_vocab_text("energy_zones"),
        vocab_passive_tags=_vocab_text("passive_tags"),
    )


def render_user_prompt(card_name: str = "", card_kind: str = "attack",
                       rules_text: str = "",
                       card_meta: Optional[dict] = None) -> str:
    """渲染 K2 用户提示词 —— **卡牌类别必须随文本一起给出**

    这是 2026-09-16 修掉的一个真 bug：模板原先声明 `@@series@@`/`@@number@@`/
    `@@supertype@@`/`@@hp@@`/`@@attacks_meta@@`，而 `parse_llm` 只传 `card_name`/
    `card_kind`/`rules_text` → 那些占位符**原样发给了模型**（模板渲染默认保留未替换
    的占位符，且不报错）。

    后果：模型**从未收到卡牌类别**，只能从文本猜；而「回复自己宝可梦 30 点 HP」
    这类文本本身无法判定是招式还是物品 → **必然猜错**（云端 4 条 kind 失败全由此而来）。
    也就是说 `kind_acc` 当时测的不是"回填能力"而是"盲猜能力"。

    现在：① 模板只用调用方确实提供的键；② 走 `strict=True`，再出现残留就抛错；
    ③ 类别显式标注并**要求原样回填**。`card_meta` 是留给card-db 的扩展位
    （系列/编号/HP 等），无则显示「（无）」。
    """
    from . import prompts as _prompts
    meta = "（无）"
    if card_meta:
        meta = " · ".join(f"{k}={v}" for k, v in card_meta.items() if v not in (None, "")) \
            or "（无）"
    return _prompts.render("parse_effects_user", strict=True,
                           card_name=card_name or "(未命名)",
                           card_kind=card_kind,
                           card_meta=meta,
                           rules_text=rules_text)


def parse_llm(text: str, kind: str = "attack", name: str = "",
              retrieved_rules: str = "", meta_out: Optional[dict] = None,
              extra_sensitive: Optional[dict] = None,
              card_meta: Optional[dict] = None) -> list:
    """LLM 后端：按 prompts/parse_effects_*.md 模板调用 OpenAI 兼容接口

    合规 6.2：调用前先**脱敏**（``agent.privacy.desensitize``），并校验端点是否被
    本地模式允许（``POKELAB_LOCAL_ONLY=1`` → 禁止外部调用）；每次调用写**审计记录**
    （仅端点/模型/字符数/脱敏计数，**不含原文**）。

    ``meta_out``：可选 dict，原地回填 ``{endpoint, model, privacy, audit}``。
    ``card_meta``：可选卡牌元数据（系列/编号/HP 等），随用户提示词一并给出。

    **枚举与取值域从代码常量注入**（而非写在 .md 里）—— 见 `engine_vocab()` 的说明。
    """
    # 渲染提示词后交给统一客户端（客户端内部完成：端点校验 → 脱敏 → 调用 → 审计）
    sys_t = render_system_prompt(retrieved_rules)
    usr_t = render_user_prompt(name, kind, text, card_meta=card_meta)
    res = _llm_chat([{"role": "system", "content": sys_t},
                     {"role": "user", "content": usr_t}],
                    temperature=0, purpose="llm_call")
    if meta_out is not None:
        meta_out.update({"endpoint": res["endpoint"], "model": res["model"],
                         "privacy": res["privacy"], "audit": True,
                         "elapsed_ms": res["elapsed_ms"],
                         "config_source": res.get("source")})
    return extract_json(res["content"]) or []


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
    # 归一化（不改 raw）：把 LLM 语义明确但枚举外的写法救回来，再校验。
    # 例：`opponent_bench` → `active_opponent_bench`（实测确有这样一条整条作废）。
    specs = normalize_specs(specs)
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
            # `target` 允许**缺省或显式 null**，表示「未指定」（与 `amount` 一致）。
            # 原先 `a.get("target") not in TARGETS` 把「没填」也判为非法 —— 而真值里
            # rule 类动作本就不填 target（「未指定」是数据模型里的合法状态）。
            # 只惩罚「给了但非法」的值，否则模型输出 `"target": null` 就整条作废
            # （实测 ru_energy_once 即因 `action.target 非法：None` 落进 A 类失败）。
            tgt = a.get("target")
            if tgt is not None and tgt not in TARGETS:
                errs.append(f"[{i}] action.target 非法：{tgt}")
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
    if sub == "discard_energy_opp":
        # 弃对手战斗位能量（`:opp` 变体）——卡面「…discard an Energy from your
        # opponent's Active Pokémon」若落成普通 discard_energy 会弃到自己头上
        return f"discard_energy:{n or 1}:opp"
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
                if extra.get("unaligned_target"):
                    # 附加伤害子句的目标引擎无法精确表达（子集限定 / 带条件 /
                    # 带缩放 / 动态目标）→ **必须如实登记**。
                    # 若在这里放过，含其它 tag 的招式（如「弃 2 能量并打 2 只各 60」）
                    # 会进池并**静默丢掉这段伤害** —— 实测 12 张卡命中该风险。
                    unsupported.append({
                        "type": t,
                        "reason": "附加伤害目标未建模"
                                  f"（{extra.get('unaligned_target')}）"})
                elif extra.get("snipe_scope"):
                    # 选定 / 全体目标 → `snipe:<scope>:<per>:<count>`
                    # （count **总是显式写出**：便于卡池数据自解释与对账脚本比对）
                    scope = extra["snipe_scope"]
                    cnt = extra.get("snipe_count")
                    tags.append(f"snipe:{scope}:{int(amt)}:{cnt if cnt else 1}")
                elif extra.get("recoil"):
                    # 反伤「This Pokémon does N damage to itself.」→ 引擎 `self:N`
                    # （语义即「攻击者自身受到 N 点伤害」，精确对应）。
                    # 全库 49 条此类招式，此前**完全没建模**：卡不进池，或更糟——
                    # 早期版本把它读成「打对手战斗位」，反伤静默消失。
                    if amt is None:
                        unsupported.append({"type": t, "reason": "反伤数值未识别"})
                    else:
                        tags.append(f"self:{int(amt)}")
                else:
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
                if extra.get("from_deck"):
                    # 磨牌：牌库顶 N 张进弃牌堆（`mill:N[:opp]`）
                    tags.append(f"mill:{n}"
                                + (":opp" if extra.get("side") == "opp" else ""))
                else:
                    is_energy = extra.get("card_type") == "energy"
                    # 弃能量的**对象**必须带进 tag：引擎的 `discard_energy:N` 是「弃自身」，
                    # 弃对手战斗位能量是 `discard_energy:N:opp`。若不带对象，卡面写
                    # 「discard an Energy from your opponent's Active Pokémon」会被结算成
                    # 弃自己（4 张真实卡曾因此静默算错）。
                    side = ":opp" if str(tgt or "") == "active_opponent" else ""
                    if extra.get("all"):
                        tags.append("discard_energy:all" + side if is_energy
                                    else "discard:all")
                    elif is_energy:
                        tags.append(f"discard_energy:{n}{side}")
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
                    # ⚠ 特性侧的检索有**两种语义**，早期一律映射成 `recon:N`，
                    #   结果把「全牌库检索」错当成「检视牌库顶 N 取 1」：
                    #   引擎的 recon 只能从**顶部 N 张里随机**拿 1 张 →
                    #   「Search your deck for a Pokémon」变成"只能从牌堆顶碰运气"。
                    #   实测池内 **16 条**此类错映射（Sawsbuck / Drilbur / Farfetch'd /
                    #   Mamoswine ex / Genesect ex / Toxtricity / Dragonair …）。
                    look_top = extra.get("look_top")
                    if look_top:
                        tags.append(f"recon:{int(look_top)}")
                    elif re.search(r"search[^.]{0,80}?\battach\b",
                                    str(s.get("source_text") or ""), re.I):
                        # 「Search your deck for a Basic {D} Energy card **and attach
                        # it** to 1 of your Benched {D} Pokémon」——效果是**附能**，
                        # 不是「检索入手」。映射成 `search_energy:N` 会让引擎把能量
                        # 拿进手牌（少打出一张能量）→ 如实登记未建模。
                        unsupported.append({
                            "type": t,
                            "reason": "检索并**附着**（非入手）需引擎新机制（B4）"})
                    else:
                        what = extra.get("what") or "any"
                        n = amt if amt is not None else 1
                        tags.append(f"search_{what}:{n}")
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
            elif t == "stadium":
                k = extra.get("kind")
                if k == "hp_plus" and amt is not None and extra.get("stage") is not None:
                    tags.append(f"stadium:hp_plus:{int(amt)}:stage:{extra['stage']}")
                elif k == "dmg_reduce" and amt is not None and extra.get("ptype"):
                    tags.append(f"stadium:dmg_reduce:{int(amt)}:type:{extra['ptype']}")
                elif k == "use" and extra.get("use_kind"):
                    tail = ""
                    if amt is not None:
                        tail += f":{int(amt)}"
                    if extra.get("ptype"):
                        tail += f":type:{extra['ptype']}"
                    if extra.get("require"):
                        tail += f":require:{extra['require']}"
                    tags.append(f"stadium:use:{extra['use_kind']}{tail}")
                else:
                    unsupported.append({"type": t,
                                        "reason": f"场地形态未建模（{k}）"})
            elif t == "tool":
                # S2 道具（Pokémon Tool）
                k = extra.get("kind")
                if k == "dmg_reduce" and amt is not None:
                    # 限定减伤：from 攻击方（类型集合 / 有特性）、holder 持有者属性
                    tail = ""
                    if extra.get("from_type"):
                        tail += f":from:type:{extra['from_type']}"
                    elif extra.get("from_types"):
                        tail += ":from:type:" + ",".join(extra["from_types"])
                    elif extra.get("from_ability"):
                        tail += ":from:ability"
                    if extra.get("holder_type"):
                        tail += f":holder:type:{extra['holder_type']}"
                    if extra.get("discard"):
                        tail += ":discard"
                    tags.append(f"tool:dmg_reduce:{int(amt)}{tail}")
                elif k in ("hp_plus", "retreat_less") and amt is not None:
                    tags.append(f"tool:{k}:{int(amt)}")
                elif k == "no_retreat":
                    tags.append("tool:no_retreat")
                elif k == "on_damaged_draw" and amt is not None:
                    tags.append(f"tool:on_damaged:draw:{int(amt)}")
                elif k == "on_damaged_move_energy":
                    tags.append("tool:on_damaged:move_energy:1")
                elif k == "on_damaged_counter" and amt is not None:
                    tail = ""
                    if extra.get("holder_type"):
                        tail += f":holder:type:{extra['holder_type']}"
                    if extra.get("discard"):
                        tail += ":discard"
                    tags.append(f"tool:on_damaged:counter:{int(amt)}{tail}")
                elif k == "survive" and amt is not None:
                    tags.append(f"tool:survive:{int(amt)}")
                else:
                    unsupported.append({"type": t,
                                        "reason": f"道具形态未建模（{k}）"})
            elif t == "passive":
                kind_ = extra.get("kind")
                if kind_ == "bench_immune":
                    tags.append("passive:bench_immune")
                elif kind_ == "dmg_reduce" and amt is not None:
                    # S1 静态修正：常驻减伤（卡面「after applying Weakness and
                    # Resistance」→ 引擎在弱抗之后结算，见 battle._do_attack）
                    tags.append(f"passive:dmg_reduce:{int(amt)}")
                elif kind_ == "hp_plus" and amt is not None:
                    tags.append(f"passive:hp_plus:{int(amt)}")
                elif kind_ == "no_weakness":
                    tags.append("passive:no_weakness")
                elif kind_ == "no_retreat":
                    tags.append("passive:no_retreat")
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


def _targets(spec: dict, declared_only: bool = False) -> dict:
    """按动作类型聚合 target

    ``declared_only=True``：**跳过没有显式声明 `target` 的动作** ——
    用于「某些动作类型的 target 无法从文本判定，不该计入严格比对」的场景
    （如 `rule` 类动作的 `any` 只是约定，不是从文本读出来的）。

    不加这个开关的话，未声明的动作会被收集成 `None`，反而与实际的字符串值
    比对失败 —— 那是**假失败**：模型没错，是评测把"没声明"当成了"期望 None"。
    """
    out: dict = {}
    for a in spec.get("actions") or []:
        if declared_only and "target" not in a:
            continue
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
        # target：**只比对「expect 里显式声明了 target」的动作类型**。
        # 有些动作的 target 无法从文本判定（典型是 `rule` 类取 `any`，那是约定而非
        # 文本可读信息）—— 这类动作在 expect 里**不写 target** 即自动移出严格比对，
        # 避免把「评测约定」当成「模型错误」。
        _exp_t = _targets(exp, declared_only=True)
        if not _exp_t:
            r["target"] = None                     # 全部未声明 → 不计入
        else:
            _got_t = _targets(got)
            _g = {k: _got_t.get(k, []) for k in _exp_t}
            ok = _exp_t == _g
            r["target"] = ok
            if not ok:
                r["diffs"].append(f"target: 期望 {_exp_t!r} ≠ 实际 {_g!r}")
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


def collect_pool_items() -> list:
    """真实卡池的全部招式/特性条目（取有**展示文本**者）—— K2 的真实语料口径

    注意：`display_desc` 是「中文优先、无中文回退英文」，所以本语料**中英混杂**——
    实测 328 条里中文仅 34 条，其余 294 条是英文（扩展池没有 `desc_zh`）。
    评测时按语言分组统计，别混在一起报一个总数。

    抽成独立函数是为了让「全量跑」(``parse_card_pool``) 与「抽样评测」
    （``apps/api/scripts/k2_local_eval.py``）**共用同一取数逻辑**，
    否则两处各写一份、日后口径悄悄漂移，数字就不可比了。
    """
    import sys
    eng = REPO / "packages" / "engine"
    if str(eng) not in sys.path:
        sys.path.insert(0, str(eng))
    from engine import reallib                     # noqa: E402
    items: list = []
    for name, item in reallib.catalog().items():
        d = item["def"]
        for a in getattr(d, "attacks", ()) or ():
            if a.display_desc:
                items.append({"kind": "attack", "name": a.name,
                              "name_zh": a.name_zh, "text": a.display_desc})
        for ab in getattr(d, "abilities", ()) or ():
            if ab.display_desc:
                items.append({"kind": "ability", "name": ab.name,
                              "name_zh": ab.name_zh, "text": ab.display_desc})
    return items


def parse_card_pool(backend: str = "rule") -> dict:
    """对**真实卡池**的全部招式/特性 desc_zh 跑解析（K2 的真实语料口径）"""
    items = collect_pool_items()
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
