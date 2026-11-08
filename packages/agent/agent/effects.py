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
KINDS = ("attack", "ability", "item", "supporter", "energy", "rule", "stadium")
TRIGGERS = ("on_use", "on_knockout", "static", "on_attach", "on_evolve")
ACTION_TYPES = ("damage", "draw", "attach", "heal", "discard", "switch",
                "search", "evolve", "spread", "move_damage", "knockout",
                "status", "rule", "recover",
                # v1.4 修正器类（B4 批次）
                "coin_flip", "damage_mod", "shield", "passive",
                # S2 道具（2026-10-26）：附着在宝可梦上的常驻修正
                "tool",
                # S3 场地（2026-10-27）：全场唯一、双方共用
                "stadium",
                # 受击反应（2026-10-27）：己方挨打时由**攻击者**承担后果
                "on_damaged",
                # 伤害指示物直放（2026-10-29 第二十批）：引擎 `counters:` 已执行，
                # 此前枚举漏了它 → 解析数据里一直带着「action.type 非法」的假错误
                "counters",
                # 附着能量收回手牌（2026-10-29 第二十三批）：引擎 `bounce:energy:`
                "bounce",
                # fx 效果族（2026-10-27 起）：解析侧产出、引擎 engine/families/fx_*.py 执行
                "fx",
                # 检索能量自由分配附着（2026-10-30 第三十三批）：引擎
                # `attach_spread:...` 已执行；漏登记会让解析数据一直带着
                # 「action.type 非法」的假错误（同 counters 那次）
                "attach_spread",
                # 2026-11-02 第三十八批：抛币计数分配（`attach_spread_flip:`
                # 抛 N 币 × per 张）与「每只后备各 per 张」（`attach_each_bench:`）
                "attach_spread_flip", "attach_each_bench",
                # 2026-11-02 第三十八批：弃对手道具/特殊能量 **或** 弃竞技场（二选一）
                "discard_tool_or_senergy",
                # 2026-11-02 第三十九批：双色 or-1-of-each / 选 N 只各附 /
                # 抛到反面为止 / 训练家抛币门
                "attach_spread_multi", "attach_each_pick",
                "attach_flip_until", "coin_gate_attach",
                # 2026-11-02 第四十批：「Flip a coin until you get tails」的
                # **非伤害**子效果（磨对手牌库 / 弃对手手牌 / 任意卡检索）
                "until_tails",
                # 2026-11-04 第五十批：被击倒时的奖赏卡减少（含同队与道具）
                "ko_prize")
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
# 「Your opponent's Active Pokémon is now Burned **and** Poisoned.」（2026-11-04
# 第五十三批）——一句挂两个异常。下面 `_EN_STATUS` 的循环**命中首个状态词就
# break**（`_EN_STATUS` 的遍历顺序与文本顺序无关），第二状态被静默丢掉：
# Salazzle ex「Burned and Poisoned」只剩烧伤、Drapion「Paralyzed and Poisoned」
# 只剩麻痹。两者都**不报错、测试全绿、卡照样能打**。
# 判定按**该词所在那一句**（`_sentence_at`），且以本正则的捕获为准 ——
# 不能靠「循环里先命中谁」推断谁是 a 谁是 b。
_EN_MULTI_STATUS_SENT = re.compile(
    r"opponent'?s? active pok[eé]mon is now "
    r"(?P<a>asleep|burned|confused|paralyzed|poisoned) and "
    r"(?P<b>asleep|burned|confused|paralyzed|poisoned)", re.I)


def _status_word_is_condition(text: str, word: str) -> bool:
    """状态词是否只出现在「If …, this attack does N more damage」这类**条件句**里

    只看词所在的那一句（不跨句），且要求同时满足：
      · 该句以 `if` 开头（条件从句）
      · 该句含 `more damage`（伤害缩放）或 `this attack does nothing`
        （状态是失败门的**前置条件**，不是要施加的效果）
    满足两条即视为「条件」，效果层必须让位；否则（如「is now Burned.」）照常产出。
    """
    for sent in re.split(r"(?<=[.!?])\s+", text):
        low_s = _norm_apostrophe(sent).lower()
        if word in low_s and low_s.strip().startswith("if ") and (
                re.search(r"more\s+damage", low_s)
                or re.search(r"this attack does nothing", low_s)):
            return True
    return False


# ---------- 「受击触发」从句分流（2026-10-27） ----------
# 卡面原型：「If this Pokémon is in the Active Spot and is damaged by an attack from
# your opponent's Pokémon (even if this Pokémon is Knocked Out), the Attacking
# Pokémon is now Burned.」
# 语义是**被动反应**：己方宝可梦挨打时由攻击者承担后果，不是「发动特性让对手陷入
# 状态」。早期实现只看「句子含状态词」就产出无条件 `status` 动作 —— 在特性状态
# tag 尚未放通时它表现为「未建模」而被白名单挡下，一旦放通就会**凭空给对手上状态**
# （Heatran / Numel / Scolipede 实测命中）。
# 引擎目前只有道具侧的 `tool:on_damaged:*`，宝可梦特性侧没有对应钩子 → 如实登记
# 未建模，绝不当成主动效果结算。
_REACTIVE_DAMAGE_RE = re.compile(
    r"\b(?:is|are|was|were)\s+damaged\s+by\s+an\s+attack", re.I)
_SENT_BOUND_RE = re.compile(r"[.!?](?=\s|$)")


def _sentence_spans(text: str) -> list:
    """句子级字符区间（左闭右开），用于「整句屏蔽」"""
    out, start = [], 0
    for m in _SENT_BOUND_RE.finditer(text):
        out.append((start, m.end()))
        start = m.end()
    if start < len(text):
        out.append((start, len(text)))
    return out


def _reactive_damage_spans(text: str) -> list:
    """「受击触发」所在**整句**的字符区间

    屏蔽整句而不是只屏蔽到触发词：该句的后半段（「the Attacking Pokémon is now
    Burned」/「put N damage counters on the Attacking Pokémon」）就是反应效果本身，
    留着同样会被通用分支读成主动效果。
    """
    if not text or not _REACTIVE_DAMAGE_RE.search(text):
        return []
    return [(a, b) for a, b in _sentence_spans(text)
            if _REACTIVE_DAMAGE_RE.search(text[a:b])]


def _mask_spans(text: str, spans: list) -> str:
    """把区间替换成**等长空格** —— 保持全部字符位置不变

    位置必须守恒：`_in_coin_branch` / `_order_actions_by_text` / `_sentence_at`
    都按下标判定「这段文字在哪个从句里」，整体删除会让后面的偏移全部错位。
    """
    if not spans:
        return text
    buf = list(text)
    for a, b in spans:
        for i in range(a, b):
            if buf[i] not in "\r\n":
                buf[i] = " "
    return "".join(buf)


# 特性侧「主动施加状态」的**正向**判据（2026-10-27）
# 只有明确写「让你的对手战斗位陷入某状态」才允许产出 `status`。反例（真实卡面）：
#   · Hoothoot「This Pokémon can't be Asleep.」            → 免疫，不是施加
#   · Magmortar「put 3 more damage counters on your opponent's Burned Pokémon」→ 追加伤害
#   · Mismagius ex「whenever … moves to the Bench … their new Active is now Confused」
#     → 换位触发，引擎没有该钩子
# 这三类若按「句子含状态词」放通，都会变成**主动给对手上状态**。
_STATUS_INFLICT_RE = re.compile(
    r"(?:make|makes|making|leave|leaves|leaving)\s+"
    r"(?:your opponent'?s|the opponent'?s|their|your)\s+active\s+pok[eé]mon\b"
    r"[^.]{0,60}?\b(?:asleep|burned|confused|paralyzed|poisoned)\b"
    r"|(?:your opponent'?s|the opponent'?s|their)\s+active\s+pok[eé]mon\s+"
    r"is\s+now\s+(?:asleep|burned|confused|paralyzed|poisoned)\b", re.I)


def _status_is_active_infliction(text: str, word: str) -> bool:
    """特性侧：状态词所在**句子**是否真的是「让对手战斗位陷入该状态」

    只看含该状态词的那一句，避免同一张卡另一句的措辞把结论带偏。
    """
    for sent in re.split(r"(?<=[.!?])\s+", _norm_apostrophe(text)):
        low_s = sent.lower()
        if word in low_s and _STATUS_INFLICT_RE.search(low_s):
            return True
    return False


# 「受击反应」的三类可表达形态（引擎 `on_damaged:<kind>:<arg>`）：
#   · 攻击者陷入状态：the Attacking Pokémon is now Burned   → status
#   · 攻击者吃伤害指示物：put N damage counters on the Attacking Pokémon → counter
#   · 攻击者弃能量：discard an Energy from the Attacking Pokémon → discard_energy
_ONDMG_STATUS_RE = re.compile(
    r"attacking\s+pok[eé]mon\s+is\s+now\s+(burned|asleep|poisoned|confused|paraly[sz]ed)",
    re.I)
_ONDMG_COUNTER_RE = re.compile(
    r"(?:put|place)\s+(\d+)\s+damage\s+counters?\s+on\s+(?:the\s+)?attacking\s+pok[eé]mon",
    re.I)
_ONDMG_DISCARD_RE = re.compile(
    r"discard\s+an?\s+energy\s+from\s+(?:the\s+)?attacking\s+pok[eé]mon", re.I)
# 「… for each {M} Energy attached to this Pokémon」（Orthworm ex，第二十九批）
_ONDMG_PER_ENERGY_RE = re.compile(
    r"(?:put|place)\s+(\d+)\s+damage\s+counters?\s+on\s+(?:the\s+)?attacking\s+"
    r"pok[eé]mon\s+for\s+each\s+\{([a-z])\}\s+energy\s+attached\s+to\s+this\s+"
    r"pok[eé]mon", re.I)
# 「put damage counters on the Attacking Pokémon **equal to the damage done to
#  this Pokémon**」（Zamazenta，第二十九批）
_ONDMG_EQUAL_RE = re.compile(
    r"put\s+damage\s+counters\s+on\s+(?:the\s+)?attacking\s+pok[eé]mon\s+"
    r"equal\s+to\s+the\s+damage\s+done\s+to\s+this\s+pok[eé]mon", re.I)


def _detect_on_damaged(text: str, kind: str = "ability") -> list:
    """把「受击触发」从句解析成动作（解析不出 → 空列表）

    两类来源，语义不同、引擎机制也不同：
      · **常驻**（特性侧）：「If this Pokémon is in the Active Spot and is damaged
        by an attack …, <反应>」→ ``on_damaged:<kind>:<arg>``。
      · **下回合反制**（招式侧）：「**During your opponent's next turn**, if this
        Pokémon is damaged by an attack …, put N damage counters on the Attacking
        Pokémon [equal to the damage done]」→ ``retaliate_next:<n|equal>``
        （2026-10-29 第二十九批；此前这一整类因「没有延迟队列」被拒）。

    其余形态仍如实拒绝（宁缺勿错）：
      · 反制数值来自**其它**计数源（如每枚硬币 / 每张手牌）；
      · 「prevent that damage」（Fezandipiti）——需要伤害**预防**层；
      · 「search your deck …」（Koffing）——需要受击即检索的钩子。
    """
    out = []
    for a, b in _reactive_damage_spans(text):
        low = _norm_apostrophe(text[a:b]).lower()
        if re.search(r"during your opponent'?s next turn", low):
            if kind != "attack":
                return []
            if _ONDMG_EQUAL_RE.search(low):
                out.append(act("on_damaged", None, "active_opponent",
                               {"kind": "retaliate", "equal": True}))
                continue
            m = _ONDMG_COUNTER_RE.search(low)
            if m and not re.search(r"for each|equal to",
                                   low[m.end():m.end() + 60]):
                out.append(act("on_damaged", int(m.group(1)) * 10,
                               "active_opponent",
                               {"kind": "retaliate",
                                "counters": int(m.group(1))}))
                continue
            return []
        if kind == "attack":
            # 「If this Pokémon was damaged by an attack during your opponent's
            #  last turn, this attack does **that much** more damage.」
            # （第一百零一批，Mega Heracross ex「Juggernaut Horn」）
            # 数值 = 攻击方上回合受到的招式伤害量，由引擎 `dmg_plus_prev_taken`
            # 现算（回合开始时把 mark 滚到 prev）。
            if re.search(r"this attack does that much more damage", low):
                out.append(act("rule", None, "self",
                               {"dmg_plus_prev_taken": True}))
                continue
            return []
        if kind != "ability":
            return []
        # 「search your deck for up to N Pokémon that have "X" in their name and
        #  put them onto your Bench」（第一百零一批，Team Rocket's Koffing）
        _m = re.search(r"search your deck for up to (\d+) pok[eé]mon that have "
                       r"[“\"']?([a-z' ]+?)[”\"']? in their name", low)
        if _m:
            key = _tag_name_key(_m.group(2))
            if key:
                out.append(act("on_damaged", None, "active_opponent",
                               {"kind": "search_bench", "n": int(_m.group(1)),
                                "name_has": key}))
                continue
            return []
        # ⚠ 顺序：`counter_per` 必须先于普通 counter —— 后者会匹配到前半句
        #   「put 2 damage counters on the Attacking Pokémon」，再把 for each 当
        #   「数值判不出」拒绝掉（Orthworm ex 会因此被误判未建模）。
        m = _ONDMG_PER_ENERGY_RE.search(low)
        if m:
            color = _EN_SYMBOL_COLOR.get(m.group(2).upper())
            if not color:
                return []
            out.append(act("on_damaged", int(m.group(1)) * 10, "active_opponent",
                           {"kind": "counter_per", "per_energy": color}))
            continue
        m = _ONDMG_STATUS_RE.search(low)
        if m:
            word = m.group(1).replace("paralysed", "paralyzed")
            tag = _STATUS_WORD_TO_TAG.get(word)
            if not tag:
                return []
            out.append(act("on_damaged", None, "active_opponent",
                           {"kind": "status", "status": tag}))
            continue
        m = _ONDMG_COUNTER_RE.search(low)
        if m:
            # 「for each …」= 数值随计数源变化 → 判不出固定值，拒绝
            if re.search(r"for each|equal to", low[m.end():m.end() + 60]):
                return []
            out.append(act("on_damaged", int(m.group(1)) * 10,
                           "active_opponent", {"kind": "counter",
                                               "counters": int(m.group(1))}))
            continue
        if _ONDMG_DISCARD_RE.search(low):
            out.append(act("on_damaged", 1, "active_opponent",
                           {"kind": "discard_energy"}))
            continue
        return []          # 形态判不出 → 由调用方登记未建模
    return out
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
# ---- B3（2026-10-28）：抽牌完整性句式 ----
# ① 双方同抽：「you may have each player draw a card」（Chandelure「Alluring Light」）
_EN_EACH_PLAYER_DRAW_RE = re.compile(
    r"each player draws?\s+(?:a|an|1|(\d+))\s+cards?", re.I)
# ② 对手把手牌洗/放回牌库后抽 N（Gothitelle「Distorted Future」/
#    Vivillon「Grand Wing」）。允许跨一个句号：Vivillon 的抽牌在第二句里。
_EN_OPP_SHUFFLE_DRAW_RE = re.compile(
    r"(?:have (?:your )?opponent|your opponent)\s+(?:shuffle|shuffles)\s+their hand"
    r"(?P<mid>.{0,220}?)\b(?:they\s+|your opponent\s+)?(?:draw|draws)\s+(\d+)\s+cards?",
    re.I | re.S)
# ③ 按计数源抽：「Draw a card for each of your Ancient Pokémon in play.」
#    （Awakening Drum）/「…for each of your opponent's Benched Pokémon.」
_EN_DRAW_PER_COUNTER_RE = re.compile(
    r"draw\s+(?:a|an|1)\s+cards?\s+for each[^.]{0,120}", re.I)

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

# ---------------- 检视牌库顶：筛选 / 去向 / 前置条件（2026-10-30 第二十批） ----------
# 引擎原有的 `recon:N` 只有一种形态：「顶部 N 张里**随机**取 1 张入手，其余放牌库
# 底」。卡池里另有「检视顶部 N 张 → 按类别/属性筛出 ≤K 张 → 入手 / 铺场 / 附着」
# 以及「其余**洗回**牌库（不是放牌库底）」等形态，此前一律拒绝（整卡不收）。
#
# 这里逐**整句**硬匹配：只有整段命中下面某一条才认领；多一个词、少一个从句都落到
# 既有的 `rule` 拒绝分支。理由：检视牌库顶一旦被放宽成「任意 N 张入手」，等于凭空
# 多给一张手牌（池内实测过这类静默加强），所以宁可不收。
_RECON_TAIL = r"shuffle the other cards back into your deck[.]?"


def _norm_prose(t: str) -> str:
    """归一化卡面文本，供**整句正则**匹配使用。

    `_parse_rule_en` 传进来的 `t` 是**未归一化**的（保留大小写、弯引号与换行），
    直接拿去跑整句正则会全部失配：实测踩过 `opponent’s` / `can’t` 的弯引号、
    以及 `.\n\n` 换行这两处（Hassel / Grimsley's Move / Luxray ex）。
    与 `agent/fx/__init__.py::normalize()` 口径一致（弯引号与破折号转 ASCII、
    折叠空白、转小写），只是这里额外要求**必须**先折叠再匹配。
    """
    s = (str(t or "").replace("\u2019", "'").replace("\u2018", "'")
         .replace("\u201c", '"').replace("\u201d", '"')
         .replace("\u2013", "-").replace("\u2014", "-"))
    return re.sub(r"\s+", " ", s).strip().lower()


# 「代价子句 + 绑定后续效果」的通用形状（2026-11-02）。
# 只认「同一句里 代价 → and（立刻）+ 效果」这一种：代价自成一句的
# （`Discard up to 3 Energy cards from your hand. This attack does …`）不在内，
# 那类由 `discard_hand_energy:` 正常映射，不能当成「代价丢失」误杀。
_ENERGY_COST_CLAUSE = re.compile(
    r"(?:you may )?(?:discard|shuffle)[^.,]*?\benergy\b[^.,]*?"
    r"(?:,\s*and this attack does|and this attack does|and make your opponent)",
    re.I)


# ---------------- 打出代价（2026-11-02 第三十六批） ----------------
# 「You can use this card only if you discard N other cards from your hand.」
#
# ⚠ 这与 `recon:` 的 `gate:` **不是同一类**，不能合并：
#   · `gate:ko_last_turn` 是**纯条件检查** —— 不满足不能打，满足时**没有状态变化**；
#   · 本句式是**代价** —— 不满足不能打，且打出时手牌**真的少 N 张**。
# 把代价当 gate 实现 → 卡照打、手牌一张不少（凭空手牌优势）；
# 当普通效果实现 → 先做效果再弃牌（顺序反了，且条件不足时已经白拿效果）。
#
# 只认**句首**的整句（三张真实卡都是这个形态）。`discard a Basic {R} Energy
# card from your hand`（Blowtorch）**故意不认领** —— 它的限定是「基本火能量」
# 而非「任意 N 张」，落成 `discard:1` 会允许弃掉任意牌（静默放宽代价）。
_PLAY_COST_DISCARD = re.compile(
    r"^you can use this card only if you discard "
    r"(?:(?P<n>\d+)|another|an?)\s+(?:other\s+)?cards?\s+from\s+your\s+hand"
    r"[.]\s*", re.I)
# 限定属性的**基本能量**代价（Blowtorch PFL 86，2026-11-02 第三十八批）：
# 「You can use this card only if you discard a Basic {R} Energy card from your
#  hand.」
# ⚠ 必须单独一条、并把属性写进 tag：落成 `discard:1` 会允许弃掉**任意**一张
# 手牌（静默放宽代价），而卡面只认「基本火能量」。
_PLAY_COST_ENERGY = re.compile(
    r"^you can use this card only if you discard "
    r"(?:(?P<n>\d+)|another|an?)\s+(?:other\s+)?basic\s+\{(?P<c>[a-z])\}\s+"
    r"energy cards?\s+from\s+your\s+hand[.]\s*", re.I)


# ---- 特性发动代价（2026-11-04 第四十九批）----
# 卡面：「**You must** <代价> **in order to use this Ability.** Once during your
# turn, you may …」—— 前半句是**代价**（有状态变化），后半句才是效果。
# ⚠ 与 gate 的区别见记忆纪律：代价必须真的扣掉；当 gate 实现 = 免费发动。
# ⚠ 必须**先剥句**再解析：留在文本里会被通用 discard 分支读成一个动作，
# 挤掉主效果 tag（特性只认一个 tag），且执行顺序会跑到效果之后。
_ABILITY_COST_RE = re.compile(
    # `must` 与 `may` 都是**代价**：「you may discard … in order to use this
    # Ability」里的 may 只是「可以选择不发动」，不是「可以免费发动」。
    # 引擎侧只在**发动时**扣代价、付不出就不列动作，语义一致。
    # （第一百一十一批，Lunatone「Lunar Cycle」实测：只认 must 会把代价句留在
    #  正文里，被通用 discard 分支读成一个效果 → 免费发动 + 挤掉主效果。）
    r"^you (?:must|may) (?P<clause>.+?) in order to use this ability[.]\s*",
    re.I)
_ABILITY_COST_ENERGY_SELF = re.compile(
    r"^discard (?:a|an) basic \{([a-z])\} energy(?: card)? from this pok[eé]mon$",
    re.I)
_ABILITY_COST_ENERGY_HAND = re.compile(
    r"^discard (?:a|an) basic \{([a-z])\} energy card from your hand$", re.I)
_ABILITY_COST_NAMED = re.compile(
    r"^discard (?:a|an) (?P<name>.+?) card from your hand$", re.I)
_ABILITY_COST_CARDS = re.compile(
    r"^discard (?:(?P<n>\d+)|an?|another) cards? from your hand$", re.I)
_ABILITY_COST_HAND_BOTTOM = re.compile(
    r"^put (?:a|(?P<n>\d+)) cards? from your hand on the bottom of your deck$",
    re.I)


# ---- 「对手少拿奖赏卡」族（2026-11-04 第五十批）----
# 卡面四种写法，差别全在**条件**上：
#   · Shedinja「Fragile Husk」    本只被对手 {ex} 招式击倒 → 对手**一张也不拿**
#   · Mega Gengar ex「Shadowy…」 己方 **{D}** 宝可梦被 {ex} 击倒 → 少拿 1 张（同队）
#   · Munkidori ex「Oh No You Don't」本只被击倒 **且** 场上有 Pecharunt ex → 少拿 1 张
#   · Lillie's Pearl（道具）      所附宝可梦被击倒 → 少拿 1 张
# ⚠ 这与「多拿奖赏」（`ko_prize_plus:`）不是同一条路径：多拿挂在**取奖赏方**、
# 少拿挂在**被击倒方**（含同队与道具）。方向搞反 = 奖赏数反向变化。
_KO_PRIZE_MINUS = re.compile(
    r"^if (?P<subj>this pok[eé]mon"
    r"|1 of your \{([a-z])\} pok[eé]mon"
    # 道具写法：「the Lillie's Pokémon this card is attached to」= 持有者本身
    r"|the .+? this card is attached to"
    r") is knocked out "
    r"by damage from an attack from your opponent'?s pok[eé]mon(?P<ex> \{ex\})?"
    r"(?:, and if you have any (?P<named>.+?) in play)?, "
    r"(?:your opponent|that player) "
    r"(?P<what>takes? 1 fewer prize cards?|can'?t take any prize cards?)"
    r"(?: for it)?[.]?$", re.I)


def _detect_ko_prize_minus(text: str, kind: str) -> list:
    """「对手少拿 N 张奖赏卡」→ ``ko_prize`` 动作（第五十批）

    只收**四种已确认条件组合**；其余写法（别的数量、别的条件）一律不认领 ——
    奖赏数是胜负量纲，估错比不建模危险得多。
    """
    if kind not in ("ability", "tool", "item"):
        return []
    t = re.sub(r"\s+", " ", _norm_apostrophe(text or "")).strip()
    # 「The effect of <Name> doesn't stack.」是**叠加限制**而非条件：引擎本来就不
    # 叠加同类钩子（`_ko_prize_reduction` 取 max），剥掉再匹配不影响语义。
    t = re.sub(r"\s*the effect of .+? doesn'?t stack[.]?$", "", t, flags=re.I)
    m = _KO_PRIZE_MINUS.match(t)
    if not m:
        return []
    what = m.group("what").lower()
    n = "all" if what.startswith("can") else 1
    extra = {"n": n}
    if m.group("ex"):
        extra["from_ex"] = True
    team_color = None
    if m.group(2):
        # 「1 of your {D} Pokémon」= **同队**判定（特性可能挂在没被击倒的那只身上）
        team_color = _EN_ENERGY_LETTER.get(m.group(2).upper())
        if team_color is None:
            return []
        extra["team"] = team_color
    if m.group("named"):
        # 「any Pecharunt {ex} in play」—— `{ex}` 是**卡面标记**不是卡名的一部分，
        # 归一化前必须剥掉，否则键变成 `pecharuntex`，按卡名匹配永远不成立。
        key = _tag_name_key(re.sub(r"\s*\{[^}]*\}", "",
                                   m.group("named")))
        if not key:
            return []
        extra["if_named"] = key
    return [act("ko_prize", None, "self", extra)]


# ---- 训练家复合句（2026-11-04 第五十一批）----
# 训练家卡**只认一个卡级 tag**（`build_trainer`）。这些句子被通用分支拆成两条
# （`discard:all`+`draw:5` / `switch_self`+`draw_to:5` / `gust`+`confuse`）→
# 建池判「多 tag」→ **整卡不收，而且没有任何理由记录**（9 张训练家因此静默消失）。
# 解法：整句认领成一条复合 tag（引擎侧有对应实现）。
_TC_MULTI_STATUS = re.compile(
    r"^your opponent'?s active pok[eé]mon is now "
    r"(?P<a>asleep|burned|confused|paralyzed|poisoned) and "
    r"(?P<b>asleep|burned|confused|paralyzed|poisoned)[.]$", re.I)
_TC_RESEARCH = re.compile(r"^discard your hand and draw (?P<n>\d+) cards?[.]$", re.I)
_TC_SWITCH_DRAW = re.compile(
    r"^switch your active pok[eé]mon with 1 of your benched pok[eé]mon[.] "
    r"if you do, draw cards until you have (?P<n>\d+) cards? in your hand[.]$",
    re.I)
_TC_GUST_STATUS = re.compile(
    r"^switch in 1 of your opponent'?s benched (?:basic )?pok[eé]mon to the "
    r"active spot[.] if you do, the new active pok[eé]mon is now "
    r"(?P<st>confused|poisoned|burned|asleep|paralyzed)[.]$", re.I)
# 「If you go first, you may use this card during your first turn.」（Carmine）——
# 这是**许可**（放宽回合限制），不是效果也不是代价。引擎本来就不限制支援者
# 首回合使用，故整句剥掉；留着会被读成条件，把主效果挤掉。
# 招式侧同义句是「you **can** use this attack during your first turn」
# （2026-11-04 第五十三批：Tapu Koko「Fast Flight」）——此前只剥训练家写法，
# 招式那句留在正文里，于是紧随其后的「Discard your hand and draw 5 cards.」
# 不再处于句首，`_TC_RESEARCH` 的 `^…$` 锚点失配 → 落成 `discard:all`+`draw:5`
# 两条（多 tag 对招式无害，但 `research:` 的**一条**语义丢了，且与卡面对账不符）。
_TURN1_PERMISSION = re.compile(
    r"^if you go first, you (?:may use this card|can use this attack) during "
    r"your first turn[.]\s*", re.I)


# ---- 条件抽牌（2026-11-04 第五十八批）----
# 两种**方向相反**的句式，只差 `more` / `instead` 一个词：
#   · 「Draw 2 cards. Then, if you have 10 or more cards in your hand, draw 2
#      **more** cards.」（Billy & O'Nare / Emcee's Hype）→ ``plus``
#   · 「Shuffle your hand into your deck. Then, draw 4 cards. If your opponent
#      has 3 or fewer Prize cards remaining, draw 8 cards **instead**.」
#      （Lacey / Lillie's Determination）→ ``set``
# 落反方向就是静默改强度：`plus` 当 `set` = 少抽；`set` 当 `plus` = 多抽。
# 通用分支把这类句子拆成两条（`draw:2` + `draw:2` / `shuffle_draw:4` + `draw:8`），
# 训练家多 tag ⇒ 整卡不收且不记录原因。
_TC_DRAW_MORE = re.compile(
    r"^draw (?P<base>\d+) cards?[.]\s*(?:then,?\s*)?if (?P<cond>[^,]+),\s*"
    r"draw (?P<more>\d+) more cards?[.]$", re.I)
# 「Draw cards until you have N cards in your hand. If all of your Pokémon in play
#  are X Pokémon, draw cards until you have M cards in your hand instead.」
# （第九十八批，Team Rocket's Ariana）—— 与上面 `shuffle + draw N` 形态**不同**
# 口径（那是「抽 N 张」，这是「补到 N 张」）：前者靠 `draw_cond`，后者靠
# `draw_to_branch`（两次 draw_to 叠加，只补不弃）。
_TC_DRAW_TO_BRANCH = re.compile(
    r"^draw cards until you have (?P<base>\d+) cards? in your hand[.]\s*"
    r"if all of your pok[eé]mon in play are (?P<who>[a-z' .-]{3,24}?) "
    r"pok[eé]mon,\s*draw cards until you have (?P<alt>\d+) cards? in your "
    r"hand instead[.]$", re.I)
_TC_DRAW_INSTEAD = re.compile(
    r"^shuffle your hand into your deck[.]\s*(?:then,?\s*)?"
    r"draw (?P<base>\d+) cards?[.]\s*if (?P<cond>[^,]+),\s*"
    r"draw (?P<alt>\d+) cards? instead[.]$", re.I)
# 「Each player shuffles their hand into their deck. Then, you draw N cards, and
#  your opponent draws M cards.」（Unfair Stamp SFA 71，第五十九批）
# ⚠ 与 `draw_both:N` 差一个量纲：那是「双方**各**抽 N」，本条是「我方 N / 对手 M」。
# 拿 `draw_both` 顶替 = 对手手牌数静默改变（对局里既无报错也不会红测试）。
_TC_SHUFFLE_SPLIT = re.compile(
    r"^each player shuffles their hand into their deck[.]\s*then,?\s*"
    r"you draw (?P<a>\d+) cards?,? and your opponent draws (?P<b>\d+) "
    r"cards?[.]$", re.I)


def _detect_shuffle_split_draw(text: str) -> list:
    """「双方洗回手牌，我方抽 N / 对手抽 M」→ 一条 ``shuffle_draw_split``"""
    t = re.sub(r"\s+", " ", _norm_apostrophe(text or "")).strip()
    m = _TC_SHUFFLE_SPLIT.match(t)
    if not m:
        return []
    return [act("draw", None, "both_split",
                {"split": (int(m.group("a")), int(m.group("b"))),
                 "shuffle_both": True})]


# 「Put an Energy attached to **1 of your opponent's Pokémon** into their hand.」
# （Chill Teaser Toy SFA 63，第五十九批）——目标是「对手任意 1 只」，**含后备**。
# ⚠ 落成 `bounce:energy:opp_active` 就只能拔战斗位：对手把能量放后备时整段
# 效果静默不生效（卡照打、不报错、测试全绿）。
_TC_OPP_ENERGY_TO_HAND = re.compile(
    r"^put (?:an|(?P<n>\d+)) energy attached to 1 of your opponent'?s "
    r"pok[eé]mon into their hand[.]$", re.I)


# 「Each player shuffles their hand into their deck and draws N cards.」
# （Judge / Professor Juniper 类）——双方各洗回手牌、各抽 N 张。引擎
# `shuffle_draw_split:<mine>:<theirs>` 已支持对称形态（第五十九批），且
# 派发必须排在 `shuffle_draw:` 之前；本族 `mine==theirs==N`，复用同一 tag。
_EACH_PLAYER_SHUFFLE_DRAW = re.compile(
    r"^each player shuffles their hand into their deck and draws (\d+) "
    r"cards[.]$", re.I)


def _detect_each_player_shuffle_draw(text: str) -> list:
    """双方洗牌各抽 N → ``shuffle_draw_split:N:N``"""
    t = re.sub(r"\s+", " ", _norm_apostrophe(text or "")).strip()
    m = _EACH_PLAYER_SHUFFLE_DRAW.match(t)
    if not m:
        return []
    n = int(m.group(1))
    return [act("draw", None, "both_split",
                {"split": (n, n), "shuffle_both": True})]


# 「Shuffle up to N Basic Energy cards from your discard pile into your deck.」
# （Energy Recycler / Energy Retrieval 类）——把弃牌堆里的**基本能量**洗回牌库。
# 引擎 `energy_to_deck:<color>:<n>` 支持 `any` 色（第二十八批：Kyogre「Riptide」），
# 本族 `up to N` 即洗回最多 N 张基本能量，忠实映射到 `energy_to_deck:any:N`。
_ENERGY_RECYCLE = re.compile(
    r"^shuffle up to (\d+) basic energy cards from your discard pile into "
    r"your deck[.]$", re.I)


def _detect_energy_recycle(text: str) -> list:
    """弃牌堆基本能量洗回牌库 → ``energy_to_deck:any:N``"""
    t = re.sub(r"\s+", " ", _norm_apostrophe(text or "")).strip()
    m = _ENERGY_RECYCLE.match(t)
    if not m:
        return []
    return [act("rule", None, "any",
                {"energy_to_deck": {"color": "any", "n": int(m.group(1))}})]


def _detect_opp_energy_to_hand(text: str) -> list:
    """「对手某只身上的能量回其手牌」→ ``bounce:energy:opp_any:<n>``"""
    t = re.sub(r"\s+", " ", _norm_apostrophe(text or "")).strip()
    m = _TC_OPP_ENERGY_TO_HAND.match(t)
    if not m:
        return []
    n = int(m.group("n")) if m.group("n") else 1
    return [act("bounce", n, "energy", {"scope": "opp_any", "what": "energy"})]


# 「During this turn, attacks used by your {F} Pokémon do 30 more damage to your
#  opponent's Active Pokémon (before applying Weakness and Resistance).」
# （Premium Power Pro / Black Belt's Training，第六十批）
# ⚠ 与 `dmg_plus:` 的分工：那是**这一招自己**的加伤；本条是「本回合内**全队**」
# 的临时修正。落成 `dmg_plus` 会让「只有这一只」加伤（其余队友少算），
# 且持续到招式结束而不是整回合。
_TEAM_BOOST = re.compile(
    r"^during this turn, attacks used by your (?P<scope>.+?) do (?P<n>\d+) "
    r"more damage to your opponent'?s active pok[eé]mon"
    r"(?: (?P<cat>[{]?[a-z]+[}]?))?"
    r"\s*\(before applying weakness and resistance\)[.]$", re.I)


def _detect_team_boost(text: str) -> list:
    """「本回合全队增伤」→ ``team_boost:<n>[:type:<c>][:cat:<k>]``

    认不出的 scope 一律返回 ``[]``（不认领）—— 半句条件比不建模更危险：
    「只有 {F} 加伤」若被当成「全队加伤」，每个回合都会多打一截。
    """
    t = re.sub(r"\s+", " ", _norm_apostrophe(text or "")).strip()
    m = _TEAM_BOOST.match(t)
    if not m:
        return []
    scope = str(m.group("scope") or "").strip().lower()
    extra: dict = {"n": int(m.group("n"))}
    if scope in ("pok\u00e9mon", "pokemon"):
        pass                                   # 全队，无属性限定
    else:
        ms = re.match(r"^\{([a-z])\} pok[eé]mon$", scope)
        if not ms:
            return []
        et = _EN_ENERGY_LETTER.get(str(ms.group(1)).upper())
        if not et:
            return []
        extra["etype"] = et
    cat = str(m.group("cat") or "").strip().lower()
    if cat:
        cat = cat.strip("{}")
        if cat not in ("ex", "v", "vmax", "vstar"):
            return []
        extra["cats"] = cat
    return [act("team_boost", extra["n"], "team", extra)]


# 「If Festival Grounds is in play, this Pokémon may use an attack it has twice.
#  If the first attack Knocks Out your opponent's Active Pokémon, you may attack
#  again after your opponent chooses a new Active Pokémon.」
# （Dipplin / Goldeen / Seaking / Swirlix，第六十一批）
# ⚠ 这是**被动额度**（每回合能攻几次），不是可发动的特性 —— 落成主动 tag 会让
# 随机对局报「未知 field tag」，落成普通 `passive:` 则完全不起作用。
_FESTIVAL_LEAD = re.compile(
    r"^if festival grounds is in play, this pok[eé]mon may use an attack it has "
    r"twice(?: in succession)?[.]\s*if the first attack knocks out your "
    r"opponent'?s active pok[eé]mon, you may (?:attack|use an attack) again "
    r"after your opponent chooses a new active pok[eé]mon[.]$", re.I)


def _detect_extra_attack(text: str) -> list:
    """「场地在场时本宝可梦可攻击两次」→ ``passive:extra_attack``"""
    t = re.sub(r"\s+", " ", _norm_apostrophe(text or "")).strip()
    if not _FESTIVAL_LEAD.match(t):
        return []
    return [act("passive", None, "self", {"kind": "extra_attack"})]


# ---- 条件回复（2026-11-04 第六十二批）----
# 「Heal 80 damage from your Active Pokémon **that has 3 or more Energy
#  attached**.」（Jumbo Ice Cream）——「that has …」是**目标前提**（不是效果条件）：
# 不满足时这张卡本来就不能打，落成无条件 `heal:80:active` 会白回一次。
_HEAL_ENERGY_NEED = re.compile(
    r"^heal (?P<n>\d+) damage from your active pok[eé]mon that has "
    r"(?P<k>\d+) or more energy attached[.]$", re.I)
# 「Heal 30 damage from your Active Pokémon. If that Pokémon is an **Arven's**
#  Pokémon, heal 100 damage from it **instead**.」（Arven's Sandwich）
# ⚠ `instead` 是**换值**不是加值；落成「30 + 100」会多回一倍。
_HEAL_NAME_ALT = re.compile(
    r"^heal (?P<n>\d+) damage from your active pok[eé]mon[.]\s*if that "
    r"pok[eé]mon is an? (?P<who>.+?) pok[eé]mon, heal (?P<alt>\d+) damage from "
    r"it instead[.]$", re.I)


def _detect_heal_cond(text: str) -> list:
    """条件回复 → ``heal:<n>:active[:alt:<m>:if_name:<k>|:need:<cond>]``"""
    t = re.sub(r"\s+", " ", _norm_apostrophe(text or "")).strip()
    m = _HEAL_ENERGY_NEED.match(t)
    if m:
        return [act("heal", int(m.group("n")), "self", {
            "heal_scope": "active",
            "heal_need": f"own_active_energy_at_least:{int(m.group('k'))}"})]
    m = _HEAL_NAME_ALT.match(t)
    if m:
        key = _tag_name_key(m.group("who"))
        if not key:
            return []
        return [act("heal", int(m.group("n")), "self", {
            "heal_scope": "active", "heal_alt": int(m.group("alt")),
            "heal_if_name": key})]
    return []


# 条件短语 → 引擎条件键。**只映射引擎确有的键**（`_CONDITION_PARAMS` 里没有的
# 一律返回 None ⇒ 整句不认领 ⇒ 如实未建模，绝不猜一个近似条件顶上）。
_DRAW_COND_KEYS = (
    (re.compile(r"^you have (?P<n>\d+) or more cards in your hand$"),
     "own_hand_at_least"),
    (re.compile(r"^your opponent has (?P<n>\d+) or fewer prize cards? "
                r"remaining$"), "opp_prizes_at_most"),
    (re.compile(r"^you have exactly (?P<n>\d+) prize cards? remaining$"),
     "own_prizes_exactly"),
)


def _draw_cond_key(phrase: str):
    """条件短语 → ``<key>:<n>``；引擎没有该条件时返回 None"""
    p = re.sub(r"\s+", " ", str(phrase or "").strip().lower())
    for rx, key in _DRAW_COND_KEYS:
        m = rx.match(p)
        if m:
            return f"{key}:{int(m.group('n'))}"
    return None


def _detect_draw_cond(text: str) -> list:
    """条件抽牌 → ``draw_cond:<keep|shuffle>:<base>:<plus|set>:<n>:if:<cond>``

    整句认领（``^…$``）：少一句都不叫这个形态 —— 句中残留的其它效果会被
    本函数静默吞掉，所以宁可整段不认领（退回未建模）。
    """
    t = re.sub(r"\s+", " ", _norm_apostrophe(text or "")).strip()
    m = _TC_DRAW_TO_BRANCH.match(t)
    if m:
        key = _tag_name_key(m.group("who"))
        if not key:
            return []
        return [act("draw", None, "deck",
                    {"draw_to_branch": (int(m.group("base")),
                                        int(m.group("alt"))),
                     "cond": f"own_all_name:{key}"})]
    m = _TC_DRAW_MORE.match(t)
    if m:
        cond = _draw_cond_key(m.group("cond"))
        if not cond:
            return []
        return [act("draw", int(m.group("base")), "deck",
                    {"cond_extra": int(m.group("more")), "cond_add": True,
                     "cond": cond})]
    m = _TC_DRAW_INSTEAD.match(t)
    if m:
        cond = _draw_cond_key(m.group("cond"))
        if not cond:
            return []
        return [act("draw", int(m.group("base")), "deck",
                    {"cond_extra": int(m.group("alt")), "cond_add": False,
                     "cond": cond, "shuffle_hand": True})]
    return []


# ---- 对手手牌弃置族（2026-11-04 第五十四批）----
# 引擎早已有 `discard_opp_random:N`（对手自选 ⇒ 随机）与 `discard_opp_to:N`
# （弃到剩 N 张），但**解析侧从来没产出过** —— 4 张卡（Sandile / Krokorok /
# Krookodile / Furfrou）因此整卡不收。
#
# ⚠ 口径方向：卡面写「**Your opponent** discards … from their hand」时，选择权
# 在对手手里 ⇒ 引擎按**随机**执行是对的（与 `opp_hand:`「you discard a card you
# find there」= 我方选 严格相反，别混用）。
#
# ⚠ 与 `_OPP_HAND_PATTERNS` 的分工：那边要求句首是「Your opponent reveals their
# hand」并整句收尾；这边**不带 reveal**，只认「对手自己弃」。两族正则互不重叠。
_OPP_DISCARD_N = re.compile(
    r"^your opponent discards (?P<n>\d+|a) cards? from their hand[.]$", re.I)
# 「Discard random cards from your opponent's hand until they have 5 cards in
# their hand.」（Furfrou「Hand Trim」）
_OPP_DISCARD_TO = re.compile(
    r"^discard random cards? from your opponent'?s hand until they have "
    r"(?P<n>\d+) cards? in their hand[.]$", re.I)
# 「Each player discards cards from their hand until they have N cards in their
# hand. Your opponent discards first. (If a player has N or fewer …)」
# （Hand Trimmer TEF 150）——顺序（对手先弃）在「同时弃到剩 N」口径下无影响，
# 但括号里的**免弃**说明必须允许。
_DISCARD_TO_BOTH = re.compile(
    r"^each player discards cards? from their hand until they have "
    r"(?P<n>\d+) cards? in their hand[.]"
    r"(?:\s*your opponent discards first[.])?"
    r"(?:\s*\([^)]*\))?$", re.I)
# 「Discard a card from your hand. If you do, your opponent discards a card
# from their hand.」（Team Rocket's Porygon「Hacking」）—— 我方付账 + 对手随机弃
_HACKING = re.compile(
    r"^discard a card from your hand[.]\s*if you do, your opponent discards a "
    r"card from their hand[.]$", re.I)


def _detect_opp_discard(text: str, kind: str) -> list:
    """「对手自己弃手牌」→ ``discard_opp_random:N`` / ``discard_opp_to:N``

    整句认领（``^…$``）：句首认领却不收尾，会把同一句后半段的其它效果
    静默吞掉；反过来若允许出现在句中，多句式卡面里的这一段又会被别条带偏。
    """
    t = re.sub(r"\s+", " ", _norm_apostrophe(text or "")).strip()
    m = _HACKING.match(t)
    if m:
        # 付账在前、效果在后：引擎按 tag 顺序派发，`discard:1` 必须先扣掉手牌，
        # 否则「If you do」的门形同虚设（付不出也能让对手弃）。
        return [act("discard", 1, "hand"),
                act("discard", 1, "opponent_hand")]
    m = _OPP_DISCARD_TO.match(t)
    if m:
        return [act("discard", None, "opponent_hand", {"to": int(m.group("n"))})]
    m = _DISCARD_TO_BOTH.match(t)
    if m:
        return [act("discard", None, "both_hand", {"to": int(m.group("n"))})]
    m = _OPP_DISCARD_N.match(t)
    if m:
        n = 1 if not str(m.group("n")).isdigit() else int(m.group("n"))
        return [act("discard", n, "opponent_hand")]
    return []


def _detect_trainer_compound(text: str) -> list:
    """训练家「一句话两个动作」→ **一条**复合动作（第五十一批）

    已实现四式（卡面原型见各正则）：
      · 多状态「is now Burned and Confused」→ ``status`` + ``status2``
        （⚠ 此前只落 `burn`，`confuse` 被**静默丢掉**）
      · 「Discard your hand and draw N cards.」→ ``draw`` + ``discard_all``
      · 「Switch … with 1 of your Benched … If you do, draw until N」→ ``switch`` + ``draw_to``
      · 「Switch in 1 of your opponent's Benched … is now Confused」→ ``switch`` + ``status``
    """
    t = re.sub(r"\s+", " ", _norm_apostrophe(text or "")).strip()
    m = _TC_MULTI_STATUS.match(t)
    if m:
        # ⚠ `re.I` 只影响匹配、**不改捕获的大小写**（"Burned" 原样返回），
        # 必须显式 lower —— 否则查不到映射、静默返回 []（本批实测踩到）。
        a = _STATUS_WORD_TO_TAG.get(str(m.group("a")).lower())
        b = _STATUS_WORD_TO_TAG.get(str(m.group("b")).lower())
        if a and b and a != b:
            return [act("status", None, "active_opponent",
                        {"status": a, "status2": b})]
        return []
    m = _TC_RESEARCH.match(t)
    if m:
        return [act("draw", int(m.group("n")), "deck", {"discard_all": True})]
    m = _TC_SWITCH_DRAW.match(t)
    if m:
        return [act("switch", None, "self", {"draw_to": int(m.group("n"))})]
    m = _TC_GUST_STATUS.match(t)
    if m:
        st = _STATUS_WORD_TO_TAG.get(str(m.group("st")).lower())
        if st:
            return [act("switch", None, "active_opponent", {"status": st})]
    return []


def _split_ability_cost(text: str, kind: str) -> tuple:
    """剥出特性发动代价句 → ``(剩余文本, ability_cost)``

    认不出的形态返回 ``(原文, None)`` —— 由调用方保留「代价未建模」的拒收，
    绝不当成「无代价」（那会让一张要付账的特性变成免费发动）。

    形态（与 `battle._parse_ability_cost` 一一对应）：
      ``hand_bottom:1``            手牌 1 张放牌库底（Quaquaval）
      ``discard_named:1:<key>``    弃 1 张具名卡（Meowstic「Chill Teaser Toy」）
      ``discard_self_energy:1:<c>`` 弃**自身身上** 1 个基本能量（Kilowattrel）
      ``discard_hand_energy:1:<c>`` 弃**手牌** 1 张基本能量（Volcarona）
      ``discard:<n>``              弃 n 张手牌（Porygon-Z）
    """
    t = str(text or "")
    if kind != "ability":
        return t, None
    m = _ABILITY_COST_RE.match(t)
    if not m:
        return t, None
    clause = re.sub(r"\s+", " ", _norm_apostrophe(m.group("clause"))).strip()
    rest = t[m.end():].strip()
    # ⚠ 能量代价的两条**先判**：「from this Pokémon」与「from your hand」是
    # 两个不同的弃置来源，写反就是「拔自家场上能量」vs「弃手牌」（方向反了）。
    mm = _ABILITY_COST_ENERGY_SELF.match(clause)
    if mm:
        color = _EN_ENERGY_LETTER.get(mm.group(1).upper())
        return (rest, f"discard_self_energy:1:{color}") if color else (t, None)
    mm = _ABILITY_COST_ENERGY_HAND.match(clause)
    if mm:
        color = _EN_ENERGY_LETTER.get(mm.group(1).upper())
        return (rest, f"discard_hand_energy:1:{color}") if color else (t, None)
    mm = _ABILITY_COST_HAND_BOTTOM.match(clause)
    if mm:
        n = int(mm.group("n")) if mm.group("n") else 1
        return (rest, f"hand_bottom:{n}") if n >= 1 else (t, None)
    mm = _ABILITY_COST_CARDS.match(clause)
    if mm:
        n = int(mm.group("n")) if mm.group("n") else 1
        return (rest, f"discard:{n}") if n >= 1 else (t, None)
    mm = _ABILITY_COST_NAMED.match(clause)
    if mm:
        key = _tag_name_key(mm.group("name"))
        return (rest, f"discard_named:1:{key}") if key else (t, None)
    return t, None


# 特性代价的合法形态（与 `battle._parse_ability_cost` 对齐）
_ABILITY_COST_SHAPE = re.compile(
    r"^(?:discard:[1-9]\d*"
    r"|discard_named:[1-9]\d*:[a-z0-9]+"
    r"|discard_(?:self|hand)_energy:[1-9]\d*:[a-z]+"
    r"|hand_bottom:[1-9]\d*)$")


# ---- 打出前置条件（2026-11-04 第五十九批）----
# 卡面统一写法：「You can use this card only if …」。判据值必须是
# `battle._eval_condition` **确有其键**的条件 —— 引擎里没有的（例如
# 「你场上有 N's Darmanitan、N's Zoroark {ex}… 这六张全部在场」）一律不认领，
# 让整卡如实未建模，而不是拿一个近似条件顶上（那会把门槛换成另一个意思）。
_PLAY_GATE_PATTERNS = (    # 「any of your Pokémon were Knocked Out during your opponent's last turn」
    (re.compile(r"^you can use this card only if any of your pok[eé]mon were "
                r"knocked out during your opponent'?s last turn[.]\s*", re.I),
     "own_ko_last_turn"),
    # 第一百零九批（TR Archer）：只算 **Team Rocket's** 宝可梦被击倒。
    # ⚠ 不能退化成上面那条「任意己方宝可梦」——门槛被放宽 = 静默加强。
    (re.compile(r"^you can use this card only if any of your team rocket'?s "
                r"pok[eé]mon were knocked out during your opponent'?s last "
                r"turn[.]\s*", re.I), "own_ko_tr_last_turn"),
    # 「you have any Tera Pokémon in play」（含后备 —— 与 `own_bench_tera` 不同）
    (re.compile(r"^you can use this card only if you have any tera pok[eé]mon "
                r"in play[.]\s*", re.I), "tera_in_play"),
    # 「you go second, and only during your first turn」
    (re.compile(r"^you can use this card only if you go second,? and only "
                r"during your first turn[.]\s*", re.I),
     "went_second_first_turn"),
    # 「your opponent has **exactly** N Prize cards remaining」
    # ⚠ 不能写成 `opp_prizes_le`：那是「N 张或更少」，残局里也放行 = 门槛放宽。
    (re.compile(r"^you can use this card only if your opponent has exactly "
                r"(?P<n>\d+) prize cards? remaining[.]\s*", re.I),
     "opp_prizes_exactly:{n}"),
    # 「you have more Prize cards remaining than your opponent」
    (re.compile(r"^you can use this card only if you have more prize cards? "
                r"remaining than your opponent[.]\s*", re.I),
     "own_behind_prizes"),
)


def _split_play_gate(text: str, kind: str) -> tuple:
    """剥出「打出**前置条件**」句，返回 ``(剩余文本, play_gate)``

    卡面：「You can use this card only if …」。与 `_split_play_cost` 的分工
    （两者都写成 `only if`，语义相反）：
      · **代价**（`play_cost`）：要真的付账，手牌少 N 张 —— 有状态变化；
      · **前置条件**（`play_gate`）：纯条件检查，满足时**什么都不发生**。

    ⚠ 为什么必须**剥掉**：这句留在文本里会被当成主句的一部分，通用分支要么
    把它读成一个动作（挤掉真正的效果），要么整卡判「未建模」。八张卡
    （Unfair Stamp / Glass Trumpet / Call Bell / Chill Teaser Toy / Briar /
    Team Rocket's Archer / Anthea & Concordia / Rosa's Encouragement）
    此前全部堵在这一句上。

    认不出的形态返回 ``(原文, None)`` —— 由调用方保留拒收，绝不「看着像条件
    就当条件」，那会把门槛换成另一个意思（例如把 `exactly` 当 `at most`）。

    只认引擎 `_eval_condition` 里**确有其键**的条件（见 `Battle._play_gate_ok`）。
    """
    # ⚠ 非训练家 kind **原样返回**（不做撇号归一化）：本函数只在训练家卡上剥句，
    # 对其它 kind 归一化会改变下游拿到的文本，进而让别的族的整句正则失配
    # （实测 8 张「This attack's damage isn't affected by …」卡因此掉出 ready）。
    if kind not in ("item", "supporter", "stadium", "tool"):
        return str(text or ""), None
    t = _norm_apostrophe(str(text or ""))
    for rx, key in _PLAY_GATE_PATTERNS:
        m = rx.match(t)
        if not m:
            continue
        g = key.format(n=int(m.group("n"))) if "{n}" in key else key
        return t[m.end():].strip(), g
    return t, None


def _split_play_cost(text: str, kind: str) -> tuple:
    """剥出「打出代价」句，返回 ``(剩余文本, play_cost)``

    ``play_cost`` 形如 ``"discard:1"``；无代价句时返回 ``("", None)`` 的第二项为 None。

    为什么**剥掉**而不是「识别后继续跑原解析」：代价句留在文本里会被通用分支读成
    一个真正的 `discard:` 动作，混进主效果 tag 里 —— 后果有两个，都是错的：
      · 训练家只认**一个**卡级 tag（`build_trainer`），多出来的 `discard:` 会让
        整卡被判「多 tag」而不收；
      · 即便收下，`discard:` 也会在效果**之后**执行，而卡面要求**先付代价**。
    """
    t = str(text or "")
    if kind not in ("item", "supporter", "stadium", "tool"):
        return t, None
    # 限定属性的基本能量代价**先判**：`a Basic {R} Energy card` 里没有
    # 「cards from your hand」这个连续片段以外的重合，但两条正则的语义不同
    # （任意牌 vs 指定属性基本能量），顺序写反会让代价静默放宽。
    m = _PLAY_COST_ENERGY.match(t)
    if m:
        n = int(m.group("n")) if m.group("n") else 1
        et = _EN_ENERGY_LETTER.get(str(m.group("c")).upper())
        if n < 1 or et is None:
            return t, None            # 认不出属性 → 不猜，留给 unsupported
        return t[m.end():].strip(), f"discard_energy:{n}:{et}"
    m = _PLAY_COST_DISCARD.match(t)
    if not m:
        return t, None
    n = int(m.group("n")) if m.group("n") else 1
    if n < 1:
        return t, None
    return t[m.end():].strip(), f"discard:{n}"


# ⚠ 这一组是**可选**的：卡面没有这句前置条件时绝不能凭空加上条件（否则一张
# 「随时可用」的卡会变成「只在被击倒过的回合可用」= 静默削弱），因此组要具名，
# 由 `_detect_recon_top` 按**是否命中**决定 gate 有无。
_RECON_GATE_KO = (r"(?:(?P<gate>you can use this card only if any of your "
                  r"pok[eé]mon were knocked out during your opponent's last "
                  r"turn)[.]\s*)?")

_RECON_TOP_PATTERNS: tuple = (
    # Pokégear 3.0 句式：筛出 1 张 Supporter → 入手
    (re.compile(
        r"^look at the top (?P<n>\d+) cards? of your deck[.] you may reveal a "
        r"supporter card you find there and put it into your hand[.] "
        + _RECON_TAIL + r"$"),
     {"k": 1, "sel": "supporter", "dest": "hand", "rest": "shuffle"}),
    # Roto-Stick（第七十九批）：顶部 4 张里**任意数量**的支援者 → 入手。
    # 「any number of」的备选集合被顶部 N 张封死 → 不需要交互层（同 Bug Catching
    # Set 的 `all` 口径）；不能退化成 `k=1`（那样会把多张支援者静默少拿）。
    (re.compile(
        r"^look at the top (?P<n>\d+) cards? of your deck[.] you may reveal any "
        r"number of supporter cards you find there and put them into your hand[.] "
        + _RECON_TAIL + r"$"),
     {"k": "all", "sel": "supporter", "dest": "hand", "rest": "shuffle"}),
    # Bug Catching Set：{G} 宝可梦 与 基本 {G} 能量「任意组合」至多 2 张 → 入手
    (re.compile(
        r"^look at the top (?P<n>\d+) cards? of your deck[.] you may reveal up to "
        r"(?P<k>\d+) in any combination of \{(?P<c1>[a-z])\} pok[eé]mon and "
        r"basic \{(?P<c2>[a-z])\} energy cards you find there and put them into "
        r"your hand[.] " + _RECON_TAIL + r"$"),
     {"sel": "mix", "dest": "hand", "rest": "shuffle"}),
    # Drayton：1 张宝可梦 + 1 张训练家（**各 1 张**，不是「任意 2 张」）→ 入手
    (re.compile(
        r"^look at the top (?P<n>\d+) cards? of your deck[.] you may reveal a "
        r"pok[eé]mon and a trainer card you find there and put them into your "
        r"hand[.] " + _RECON_TAIL + r"$"),
     {"k": 2, "sel": "pokemon+trainer@each", "dest": "hand", "rest": "shuffle"}),
    # Hassel：至多 K 张、不筛类别 → 入手（带「上回合有宝可梦被击倒」前置条件）
    (re.compile(
        r"^" + _RECON_GATE_KO +
        r"look at the top (?P<n>\d+) cards? of your deck and put up to "
        r"(?P<k>\d+) of them into your hand[.] " + _RECON_TAIL + r"$"),
     {"sel": "any", "dest": "hand", "rest": "shuffle",
      "gate": "ko_last_turn", "gate_grp": "gate"}),
    # Grimsley's Move：筛出 1 张 {D} 宝可梦**铺后备**，其余放牌库**底**，首回合禁用
    (re.compile(
        r"^look at the top (?P<n>\d+) cards? of your deck and put a "
        r"\{(?P<c1>[a-z])\} pok[eé]mon you find there onto your bench[.] "
        r"shuffle the other cards and put them on the bottom of your deck[.] "
        r"you can't use this card during your first turn[.]?$"),
     {"k": 1, "sel": "pokemon", "dest": "bench", "rest": "bottom",
      "gate": "not_first_turn"}),
    # Waitress：筛出 1 张**基本**能量 → 附着给己方 1 只
    (re.compile(
        r"^look at the top (?P<n>\d+) cards? of your deck and attach a basic "
        r"energy card you find there to 1 of your pok[eé]mon[.] "
        + _RECON_TAIL + r"$"),
     {"k": 1, "sel": "basic_energy", "dest": "attach", "rest": "shuffle"}),
    # Explorer's Guidance：取**恰好 K 张**入手，其余**弃置**（不是洗回牌库）
    (re.compile(
        r"^look at the top (?P<n>\d+) cards? of your deck and put (?P<k>\d+) of "
        r"them into your hand[.] discard the other cards[.]?$"),
     {"sel": "any", "dest": "hand", "rest": "discard", "k": 1}),
    # 「看牌库**顶 1 张**，你可以弃掉它」（2026-11-02 第三十五批）
    #   Morpeko TWM 72（特性）/ Rockruff JTG 84 / Litwick WHT 16
    # ⚠ 「may discard」是**可选**：引擎无交互层，缺省取「弃」（带这张卡就是为了弃），
    #   属确定性默认，不是随机；不弃的收益在引擎里无从表达。
    (re.compile(
        r"^(?:once during your turn, you may )?look at the top card of your "
        r"deck[.] you may discard that card[.]?$"),
     # ⚠ 卡面**没有**洗牌句 → `rest:none`（没被弃掉的牌原序留顶）。
     # 用 `shuffle` 会顺手重洗整副牌库，卡面不符。
     {"n": 1, "k": 1, "sel": "any", "dest": "discard", "rest": "none"}),
    # 「取顶 N 张里**全部**符合条件的能量，**自由分配**附着」（第三十五批）
    #   Lapras ex SCR 32（招式）/ Metang TEF 114（特性）/ Grumpig MEG 63（进化特性）
    # 两种余牌结尾：`back into your deck`（洗回整库）与
    # `and put them on the bottom of your deck`（**只洗余牌**再放底）。
    (re.compile(
        r"^(?:once during your turn, you may )?"
        r"look at the top (?P<n>\d+) cards? of your deck and attach any number "
        r"of (?:(?P<basic>basic) )?(?:\{(?P<c1>[a-z])\} )?energy cards? you find "
        r"there to your pok[eé]mon in any way you like[.] "
        r"(?P<tail>shuffle the other cards back into your deck"
        r"|shuffle the other cards and put them on the bottom of your deck)"
        r"[.]?$"),
     {"k": "all", "sel": "energy", "dest": "attach_spread", "rest": "shuffle"}),
    # 「取顶 N 张里**全部**宝可梦铺后备」（Tatsugiri ex SSP 142）
    (re.compile(
        r"^look at the top (?P<n>\d+) cards? of your deck[.] you may put any "
        r"number of pok[eé]mon you find there onto your bench[.] "
        + _RECON_TAIL + r"$"),
     {"k": "all", "sel": "pokemon", "dest": "bench", "rest": "shuffle"}),
)


def _detect_recon_top(t: str) -> Optional[dict]:
    """整句识别「检视牌库顶 N 张 → 筛选 → 入手/铺场/附着」；认不出返回 None。

    返回::

        {"look_top": N, "max": K, "sel": "<筛选式>",
         "dest": "hand|bench|attach", "rest": "shuffle|bottom", "gate": str|None}

    ``sel`` 语法（不含 ``:``，便于拼进 tag）：``any`` / ``supporter`` /
    ``pokemon`` / ``pokemon-<ptype>`` / ``basic_energy`` /
    ``pokemon-<ptype>+energy-<ptype>``（并集）/ ``pokemon+trainer@each``（各 1 张）。
    """
    t = _norm_prose(t)
    for rx, meta in _RECON_TOP_PATTERNS:
        m = rx.match(t)
        if not m:
            continue
        gd = m.groupdict()
        sel = meta["sel"]
        if sel == "mix":
            _pt = _EN_ENERGY_LETTER.get(str(gd.get("c1") or "").upper())
            _bt = _EN_ENERGY_LETTER.get(str(gd.get("c2") or "").upper())
            if _pt is None or _bt is None:
                return None             # 属性符号不认识 → 不猜
            sel = f"pokemon-{_pt}+energy-{_bt}"
        elif sel == "energy" and (gd.get("basic") or gd.get("c1")):
            # 「attach any number of [Basic] [{X}] Energy cards」：
            # 卡面限定「Basic」时必须带 `basic_` 前缀，否则特殊能量也会被附上
            # （把「基本能量」放宽成「任意能量」= 静默加强）。
            sel = "basic_energy" if gd.get("basic") else "energy"
            if gd.get("c1"):
                _pt = _EN_ENERGY_LETTER.get(str(gd["c1"]).upper())
                if _pt is None:
                    return None         # 属性符号不认识 → 不猜
                sel = f"{sel}-{_pt}"
        elif sel == "pokemon" and gd.get("c1"):
            _pt = _EN_ENERGY_LETTER.get(str(gd["c1"]).upper())
            if _pt is None:
                return None
            sel = f"pokemon-{_pt}"
        _n = int(gd["n"]) if gd.get("n") else int(meta["n"])
        _k_raw = gd["k"] if gd.get("k") else meta["k"]
        if str(_k_raw) == "all":
            _k = "all"                  # 「any number of … you find there」
        else:
            _k = int(_k_raw)
            if _k < 1 or _k > _n:
                return None
        gate = meta.get("gate")
        _grp = meta.get("gate_grp")
        if _grp and not gd.get(_grp):
            gate = None                 # 卡面没写这句前置条件 → 不得凭空加上
        _rest = meta["rest"]
        if "on the bottom" in str(gd.get("tail") or ""):
            # 「Shuffle the other cards **and put them on the bottom**」≠ 洗回整库
            _rest = "shuffle_bottom"
        return {"look_top": _n, "max": _k, "sel": sel, "dest": meta["dest"],
                "rest": _rest, "gate": gate}
    return None


# ---------------- 检索能量并自由分配附着（2026-10-30 第三十三批） ----------------
# 卡面原型：「Search your deck for up to 3 Basic Energy cards and attach them to
# your Pokémon **in any way you like**. Then, shuffle your deck.」
# 引擎原有的 `attach` 只能附给**单一**目标，而这一族允许把多张分给多只，
# 此前整族落到「检索附能需自行分配目标（B4）」被拒。
_ATTACH_SPREAD_TAIL = r"[.]?\s*then,?\s*shuffle your deck[.]?$"
# 「of different types」是**检索约束**（2026-11-02 第三十九批）：拿到的 N 张能量
# 属性必须**两两不同**。写成可选**具名**组，只有命中时才产出 `:distinct`
# —— 否则会给所有普通形态凭空加上「不能同色」的限制（静默削弱）。
_ATTACH_SPREAD_SIMPLE = re.compile(
    r"search your deck for up to (\d+) basic (?:[{]([a-z])[}] )?energy cards?"
    r"(?P<distinct> of different types)?"
    r" and attach them to your (.+?) in any way you like" + _ATTACH_SPREAD_TAIL)
# 双属性双限额（Joltik SCR 50：up to 2 Basic {G} + up to 2 Basic {L}）
_ATTACH_SPREAD_DUAL = re.compile(
    r"search your deck for up to (\d+) basic [{]([a-z])[}] energy cards? and "
    r"up to (\d+) basic [{]([a-z])[}] energy cards? and attach them to your "
    r"pok[eé]mon in any way you like" + _ATTACH_SPREAD_TAIL)
# 「从手牌 / 弃牌堆」形态（Mesprit SSP 79）：以 Attach 起首、无「then shuffle」
_ATTACH_SPREAD_FROM_ZONE = re.compile(
    r"attach up to (\d+) basic (?:[{]([a-z])[}] )?energy cards from your "
    r"(hand|discard pile) to your (.+?) in any way you like[.]?$")
# 带「用完自爆」的形态（Magneton SSP 59，2026-11-02 第三十七批）：
#   「Once during your turn, you may attach up to 3 Basic Energy cards from your
#    discard pile to your {L} Pokémon in any way you like. **If you use this
#    Ability, this Pokémon is Knocked Out.**」
# 与 `_ATTACH_SPREAD_FROM_ZONE` 的区别只有三处：可选前缀、`{X}` 属性目标、
# 末尾自爆句。做**独立正则**而不是给它加可选组 —— 那条已有多张卡依赖，
# 改动它的匹配范围属于「行为漂移」，代价不可控。
_ATTACH_SPREAD_SELFKO = re.compile(
    r"^(?:once during your turn, you may )?attach up to (\d+) basic "
    r"(?:[{]([a-z])[}] )?energy cards? from your (hand|discard pile) to your "
    r"(.+?) in any way you like[.]? if you use this ability, this pok[eé]mon "
    r"is knocked out[.]?$")
# 进化触发形态（Marnie's Grimmsnarl ex DRI 136，2026-11-02 第三十八批）：
#   「When you play this Pokémon from your hand to evolve 1 of your Pokémon during
#    your turn, you may search your deck for up to 5 Basic {D} Energy cards and
#    attach them to your Marnie's Pokémon in any way you like. Then, shuffle your
#    deck.」
# 后半句与 `_ATTACH_SPREAD_SIMPLE` **逐字相同**，唯一差异是句首多了一句进化
# 触发从句；而 `_detect_attach_spread` 全部用 `.match`（隐式 `^`），SIMPLE 因此
# 匹配不上（这是结构上的保证，不是靠额外 if 判断）。
# **不给 SIMPLE 加可选前缀**：那会把认领范围扩到所有带从句的卡 = 行为漂移，
# 代价不可控。新开一条把前缀写成**必选**组，只认领进化触发形态。
# 认领后由 `to_engine_tags` 的 `trigger == "on_evolve"` 分支包成
# `evolve_act:attach_spread:...`（引擎在**进化那一刻**执行一次）。
_ATTACH_SPREAD_EVOLVE = re.compile(
    r"^when you play this pok[eé]mon from your hand to evolve 1 of your "
    r"pok[eé]mon during your turn, you may search your deck for up to (\d+) basic "
    r"(?:[{]([a-z])[}] )?energy cards? and attach them to your (.+?) "
    r"in any way you like" + _ATTACH_SPREAD_TAIL)
# 抛币计数形态（Mega Audino ex ASC 172，2026-11-02 第三十八批）：
#   「Flip 3 coins. For each heads, search your deck for up to 2 Basic Energy cards
#    and attach them to your Pokémon in any way you like. Then, shuffle your deck.」
# 张数要到**结算时**才知道（正面枚数 × 每正面上限），解析期只能记下「抛几枚、
# 每正面几张」→ 单独一个 tag `attach_spread_flip:`，**不复用** `attach_spread:`
# 的 `n` 段：把它写成任何固定值（6 / 2 / …）都是静默改变强度。
_ATTACH_SPREAD_FLIP = re.compile(
    r"^flip (\d+) coins?[.]? for each heads,? search your deck for up to (\d+) "
    r"basic (?:[{]([a-z])[}] )?energy cards? and attach them to your (.+?) "
    r"in any way you like" + _ATTACH_SPREAD_TAIL)
# 「每只后备各取 per 张、**绑定给那一只**」（Mega Gardevoir ex MEG 60）：
#   「For each of your Benched Pokémon, search your deck for a Basic {P} Energy card
#    and attach it to that Pokémon. Then, shuffle your deck.」
# ⚠ 与 `attach_spread` 是**两种语义**，不可代用：spread 是「n 张任意分配」，
# 本条是「每只后备各 per 张、只能附给它自己」。用 spread 建模会既可漏掉某只、
# 也可把多张堆在同一只上 —— 卡面均不允许。
_ATTACH_EACH_BENCH = re.compile(
    r"^for each of your benched pok[eé]mon, search your deck for (?:a|an|up to (\d+)) "
    r"basic (?:[{]([a-z])[}] )?energy cards? and attach (?:it|them) to that "
    r"pok[eé]mon" + _ATTACH_SPREAD_TAIL)
# 「your Marnie's Pokémon」这类**训练家专属**限定：category 形如
# 「Trainer's Pokémon(Marnie)」，故按专属名做子串匹配
_ATTACH_SPREAD_OWNED = re.compile(r"^([a-z\u2019']+)['\u2019]s pok[eé]mon$")
# 「your {L} Pokémon」这类**属性**限定（Magneton SSP 59）：按 pdef.ptype 判定。
# ⚠ 与 `cat-` 是两套不同的判定依据：属性写在卡面 `{X}` 符号里，category 写的是
# 「Future / Tera / Trainer's Pokémon(Marnie)」这类**卡牌子类**。混用会漏附或错附。
# 2026-11-02 第三十九批：可选组接**并集**（Steven's Metagross ex 的
# 「your {P} Pokémon **and** {M} Pokémon」）—— 两组都能当目标，不是「各附一份」。
_ATTACH_SPREAD_TYPED = re.compile(
    r"^\{([a-z])\} pok[eé]mon(?: and \{(?P<c2>[a-z])\} pok[eé]mon)?$")
# 「your **Benched** {C} Pokémon」：后备 + 属性双限定（Glass Trumpet SFA 62）
_ATTACH_SPREAD_BENCH_TYPED = re.compile(
    r"^benched \{([a-z])\} pok[eé]mon$")


def _spread_scope_of(phrase: str) -> Optional[str]:
    """目标短语 → ``attach_spread`` 的 scope（认不出返回 None，绝不猜）"""
    ph = str(phrase or "").strip().lower()
    if ph in ("pok\u00e9mon", "pokemon"):
        return "any"
    if ph in ("benched pok\u00e9mon", "benched pokemon"):
        return "bench"
    if ph in ("future pok\u00e9mon", "future pokemon"):
        return "cat-future"
    # Terapagos SSP 161「your **Tera** Pokémon」：卡牌子类（category = "Tera"）。
    if ph in ("tera pok\u00e9mon", "tera pokemon"):
        return "cat-tera"
    m = _ATTACH_SPREAD_TYPED.match(ph)
    if m:
        et = _EN_ENERGY_LETTER.get(m.group(1).upper())
        if et is None:
            return None
        if m.group("c2"):
            et2 = _EN_ENERGY_LETTER.get(m.group("c2").upper())
            if et2 is None:
                return None
            return f"type-{et}+type-{et2}"     # 并集：两类都能当目标
        return f"type-{et}"
    # 「your **Benched** {C} Pokémon」（Glass Trumpet）——后备 ∩ 属性，**两个限定
    # 同时成立**。不能简化成 `type-<c>`（那样含战斗位，会把能量附错地方）。
    mb = _ATTACH_SPREAD_BENCH_TYPED.match(ph)
    if mb:
        et = _EN_ENERGY_LETTER.get(str(mb.group(1)).upper())
        return f"bench:type-{et}" if et else None
    m = _ATTACH_SPREAD_OWNED.match(ph)
    if m:
        return f"cat-{m.group(1)}"
    return None                   # 未知限定（Tera 以外的子类）→ 不认领


def _detect_attach_spread(t: str) -> Optional[list]:
    """整句识别「检索 N 张基本能量并自由分配附着」→ spec 列表；认不出返回 None

    返回 ``[{"n": int, "etype": str|None, "zone": "deck", "scope": str}, ...]``
    ——Joltik 这种双属性双限额会返回**两条**（各自独立取）。
    """
    t = _norm_prose(t)
    # 先匹**最长最具体**的形态（带自爆句），再退回通用形态 —— 通用形态的
    # `$` 收尾本来就匹配不到自爆句，但顺序写反时一旦某天给通用形态放宽收尾，
    # 就会静默把自爆句吞掉（代价消失）。
    m = _ATTACH_SPREAD_SELFKO.match(t)
    if m:
        scope = _spread_scope_of(m.group(4))
        if scope is None:
            return None
        et = None
        if m.group(2):
            et = _EN_ENERGY_LETTER.get(str(m.group(2)).upper())
            if et is None:
                return None
        zone = "discard" if "discard" in m.group(3).lower() else "hand"
        return [{"n": int(m.group(1)), "etype": et, "zone": zone,
                 "scope": scope, "selfko": True}]
    # 抛币计数形态（Mega Audino ex）：`coins`/`per` 是**解析期常量**，
    # 实际张数由引擎抛币决定 —— 这里绝不预先折算成一个整数。
    m = _ATTACH_SPREAD_FLIP.match(t)
    if m:
        scope = _spread_scope_of(m.group(4))
        if scope is None:
            return None
        et = None
        if m.group(3):
            et = _EN_ENERGY_LETTER.get(str(m.group(3)).upper())
            if et is None:
                return None
        return [{"coins": int(m.group(1)), "per": int(m.group(2)),
                 "etype": et, "zone": "deck", "scope": scope}]
    # 进化触发形态（Marnie's Grimmsnarl ex）：语义与 SIMPLE 完全一致，
    # 只是多了一层 `evolve_act:` 包装（由 `to_engine_tags` 按 trigger 加）。
    m = _ATTACH_SPREAD_EVOLVE.match(t)
    if m:
        scope = _spread_scope_of(m.group(3))
        if scope is None:
            return None
        et = None
        if m.group(2):
            et = _EN_ENERGY_LETTER.get(str(m.group(2)).upper())
            if et is None:
                return None
        return [{"n": int(m.group(1)), "etype": et, "zone": "deck",
                 "scope": scope}]
    m = _ATTACH_SPREAD_DUAL.match(t)
    if m:
        # 卡面只写「to your Pokémon」，双色形态没有其它限定
        out = []
        for n, c in ((int(m.group(1)), m.group(2)), (int(m.group(3)), m.group(4))):
            et = _EN_ENERGY_LETTER.get(str(c).upper())
            if et is None:
                return None           # 属性符号不认识 → 不猜
            out.append({"n": n, "etype": et, "zone": "deck", "scope": "any"})
        return out
    m = _ATTACH_SPREAD_SIMPLE.match(t)
    if m:
        # 组号说明：1=张数 2=属性符号 3(**具名** distinct) 4=目标短语。
        # `distinct` 用**具名**组，按是否命中决定语义 —— 没这句的卡绝不能
        # 被加上「不能同色」的限制（静默削弱）。
        scope = _spread_scope_of(m.group(4))
        if scope is None:
            return None
        et = None
        if m.group(2):
            et = _EN_ENERGY_LETTER.get(str(m.group(2)).upper())
            if et is None:
                return None
        return [{"n": int(m.group(1)), "etype": et, "zone": "deck",
                 "scope": scope, "distinct": bool(m.group("distinct"))}]
    m = _ATTACH_SPREAD_FROM_ZONE.match(t)
    if m:
        scope = _spread_scope_of(m.group(4))
        if scope is None:
            return None
        et = None
        if m.group(2):
            et = _EN_ENERGY_LETTER.get(str(m.group(2)).upper())
            if et is None:
                return None
        zone = "discard" if "discard" in m.group(3).lower() else "hand"
        return [{"n": int(m.group(1)), "etype": et, "zone": zone,
                 "scope": scope}]
    return None


def _detect_attach_each_bench(t: str) -> Optional[dict]:
    """整句识别「**每只后备各**检索 per 张基本能量并附给它自己」；认不出返回 None

    返回 ``{"per": int, "etype": str|None, "zone": "deck"}``。
    """
    m = _ATTACH_EACH_BENCH.match(_norm_prose(t))
    if not m:
        return None
    per = int(m.group(1)) if m.group(1) else 1      # 卡面「a Basic … card」= 1
    et = None
    if m.group(2):
        et = _EN_ENERGY_LETTER.get(str(m.group(2)).upper())
        if et is None:
            return None                             # 属性符号不认识 → 不猜
    return {"per": per, "etype": et, "zone": "deck"}


# 双色「**or 1 of each**」（Steven's Metagross ex DRI 145「X-Boot」，2026-11-02
# 第三十九批）：
#   「Once during your turn, you may search your deck for a Basic {P} Energy card,
#    a Basic {M} Energy card, **or 1 of each** and attach them to your {P} Pokémon
#    and {M} Pokémon in any way you like.」
# ⚠ 与 Joltik 的「up to 2 {G} **and** up to 2 {L}」不是一回事：那是「两类各取各的、
# 都能拿满」，本条是**三选一**（只 P / 只 M / 各 1）—— 每类**至多 1 张、总量可少**。
# 落成两条独立 `attach_spread:` 会**强制每类各拿 1 张**（比卡面强），所以新开 tag。
_ATTACH_SPREAD_MULTI = re.compile(
    r"^(?:once during your turn, you may )?search your deck for "
    r"(?:a|an|up to (\d+)) basic [{]([a-z])[}] energy card, "
    r"(?:a|an|up to (\d+)) basic [{]([a-z])[}] energy card,? or 1 of each "
    r"and attach them to your (.+?) in any way you like" + _ATTACH_SPREAD_TAIL)

# 「Choose up to N of your {X} Pokémon. **For each of those** Pokémon, …」
# （Janine's Secret Art SFA 59，2026-11-02 第三十九批）：
# 「Choose up to 2 of your {D} Pokémon. For each of those Pokémon, search your
#  deck for a Basic {D} Energy card and attach it to that Pokémon. Then, shuffle
#  your deck. If you attached Energy to your **Active** Pokémon in this way, it
#  is now Poisoned.」
# ⚠ 两处都与 `attach_each_bench` 不同：(a) 目标集合是「**选出**的至多 N 只」
# 而不是「全部后备」；(b) 末尾的**条件性自我中毒** —— 只有附到战斗位才中毒，
# 无条件 `status_self:poison` 是错的（会白给自家战斗位挂状态）。
_ATTACH_EACH_PICK = re.compile(
    r"^choose up to (\d+) of your (.+?)[.] for each of those pok[eé]mon, "
    r"search your deck for (?:a|an|up to (\d+)) basic (?:[{]([a-z])[}] )?energy "
    r"cards? and attach (?:it|them) to that pok[eé]mon[.]?\s*"
    r"then,?\s*shuffle your deck[.]?"
    r"(?P<poison>\s*if you attached energy to your active pok[eé]mon in this way, "
    r"it is now poisoned[.])?$")

# 「Flip a coin until you get tails. … up to the number of heads and attach it to
# **this Pokémon**」（Snorlax POR 63「Gormandizer」，2026-11-02 第三十九批）：
# 张数 = 正面枚数（**抛到反面为止**，几何分布），全部附给**自己**（不是自由分配）。
_ATTACH_FLIP_UNTIL = re.compile(
    r"^flip a coin until you get tails[.] search your deck for an amount of "
    r"basic (?:[{]([a-z])[}] )?energy up to the number of heads and attach it "
    r"to this pok[eé]mon" + _ATTACH_SPREAD_TAIL)

# 「Flip N coins. If **both of them** are heads, search your deck for a Basic
# Energy card and attach it to 1 of your Pokémon.」（Energy Coin BLK 81）
# 训练家卡：抛币门（全部正面才生效）+ 检索单张附能。子效果复用 `attach:`，
# 由 `_trainer_coin_all` 在门通过后派发。
_COIN_GATE_ATTACH = re.compile(
    r"^flip (\d+) coins?[.] if both of them are heads, search your deck for "
    r"(?:a|an|up to (\d+)) basic (?:[{]([a-z])[}] )?energy cards? and attach "
    r"(?:it|them) to 1 of your pok[eé]mon" + _ATTACH_SPREAD_TAIL)


def _detect_attach_multi(t: str) -> Optional[dict]:
    """整句识别「双色 **or 1 of each** 检索并自由分配」；认不出返回 None"""
    m = _ATTACH_SPREAD_MULTI.match(_norm_prose(t))
    if not m:
        return None
    t1 = _EN_ENERGY_LETTER.get(str(m.group(2)).upper())
    t2 = _EN_ENERGY_LETTER.get(str(m.group(4)).upper())
    scope = _spread_scope_of(m.group(5))
    if t1 is None or t2 is None or scope is None:
        return None
    return {"kinds": [(t1, int(m.group(1)) if m.group(1) else 1),
                      (t2, int(m.group(3)) if m.group(3) else 1)],
            "zone": "deck", "scope": scope}


def _detect_attach_each_pick(t: str) -> Optional[dict]:
    """整句识别「选至多 N 只、每只各取 per 张」；认不出返回 None"""
    m = _ATTACH_EACH_PICK.match(_norm_prose(t))
    if not m:
        return None
    scope = _spread_scope_of(m.group(2))
    if scope is None:
        return None
    et = None
    if m.group(4):
        et = _EN_ENERGY_LETTER.get(str(m.group(4)).upper())
        if et is None:
            return None
    return {"max": int(m.group(1)),
            "per": int(m.group(3)) if m.group(3) else 1,
            "etype": et, "zone": "deck", "scope": scope,
            "poison_if_active": bool(m.group("poison"))}


# 「Choose up to N of your Benched {C} Pokémon and attach a Basic Energy card
#  from your **discard pile** to each of them.」（Glass Trumpet SFA 62）
# 与上面的 `_ATTACH_EACH_PICK` 是**两条不同句式**：那条源是**牌库**且写成
# 「For each of those Pokémon, search your deck …」；这条源是**弃牌堆**且用
# 「and attach … to each of them」。共同点是「选出至多 N 只、每只各 1 张」，
# 所以落到同一个 `attach_each_pick:` tag（张数绑定到被选的那一只）。
_ATTACH_EACH_PICK_DISCARD = re.compile(
    r"^choose up to (?P<mx>\d+) of your (?P<scope>.+?) and attach a basic "
    r"(?:[{](?P<t>[a-z])[}] )?energy card from your discard pile to each of "
    r"them[.]$", re.I)


def _detect_attach_each_pick_discard(t: str) -> Optional[dict]:
    """整句识别「选至多 N 只，各从**弃牌堆**附 1 张」；认不出返回 None"""
    m = _ATTACH_EACH_PICK_DISCARD.match(_norm_prose(t))
    if not m:
        return None
    scope = _spread_scope_of(m.group("scope"))
    if scope is None:
        return None
    et = None
    if m.group("t"):
        et = _EN_ENERGY_LETTER.get(str(m.group("t")).upper())
        if et is None:
            return None
    return {"max": int(m.group("mx")), "per": 1, "etype": et,
            "zone": "discard", "scope": scope}


# 「Attach up to N Basic Energy cards from your discard pile to **1 of your
#  Stage N Pokémon**.」（Rosa's Encouragement）——目标有**进化阶段**限定。
# 通用 `attach:` 只按「任意己方位置」列目标，落上去就是静默加强（卡面只许二阶）。
# 目标限定层未建模前，整句登记为未建模，不产出会打错效果的 tag。
_ATTACH_STAGE_LIMIT = re.compile(
    r"^attach up to (?P<n>\d+) basic energy cards? from your discard pile to "
    r"1 of your stage (?P<st>\d+) pok[eé]mon[.]$", re.I)


def _detect_attach_flip_until(t: str) -> Optional[dict]:
    """整句识别「抛到反面为止、按正面数检索附给自己」；认不出返回 None"""
    m = _ATTACH_FLIP_UNTIL.match(_norm_prose(t))
    if not m:
        return None
    et = None
    if m.group(1):
        et = _EN_ENERGY_LETTER.get(str(m.group(1)).upper())
        if et is None:
            return None
    return {"etype": et, "zone": "deck"}


def _detect_coin_gate_attach(t: str) -> Optional[dict]:
    """整句识别「抛 N 币全正面 → 检索单张附能」；认不出返回 None"""
    m = _COIN_GATE_ATTACH.match(_norm_prose(t))
    if not m:
        return None
    et = None
    if m.group(3):
        et = _EN_ENERGY_LETTER.get(str(m.group(3)).upper())
        if et is None:
            return None
    return {"coins": int(m.group(1)),
            "n": int(m.group(2)) if m.group(2) else 1,
            "etype": et, "zone": "deck"}


# 「弃对手 1 只宝可梦身上的**道具或特殊能量**，**或**弃场上竞技场」
# （Blowtorch PFL 86，2026-11-02 第三十八批）：
#   「Discard a Pokémon Tool or Special Energy card from 1 of your opponent's
#    Pokémon, or discard a Stadium in play.」
# ⚠ 这是**二选一**，两支都必须建模。此前解析链只认出后半句（`discard_stadium`），
# 前半句被**静默吞掉** —— 卡一旦被收进池，玩家就少了一整条可用路径（静默削弱），
# 而且对账脚本也看不出来（它只核对已产出的 tag 与卡面是否一致）。
_DISCARD_TOOL_OR_SENERGY = re.compile(
    r"^discard a pok[eé]mon tool or special energy card from 1 of your "
    r"opponent's pok[eé]mon,? or discard a stadium in play[.]?$")


def _detect_discard_tool_or_senergy(t: str) -> bool:
    """整句识别「弃对手道具/特殊能量 **或** 弃竞技场」；认不出返回 False"""
    return bool(_DISCARD_TOOL_OR_SENERGY.match(_norm_prose(t)))


# ---------------- 对手手牌操作（2026-10-30 第三十二批） ----------------
# 引擎此前**完全没有**「我方选牌、改对手手牌」的能力：
#   · `opp_hand`     只给**张数**；
#   · `discard_opp_random:N` / `discard_opp_to:N` / `shuffle_opp_hand:N` 是
#     「对手自选 ⇒ 引擎按随机」的口径（见 battle 里同名方法的注释）。
# 而这一族卡面写的是 **"you discard a card you find there"** = **使用者选**，
# 用随机口径是**方向性错误**（我方能挑最有利的那张），必须新开 tag。
#
# 「揭示」本身在引擎里没有对应状态（引擎不建模信息隐藏，双方手牌对决策方
# 可见），因此真正要建模的只是后半句（弃牌 / 放回牌库底）。
# ⚠ 只有「揭示」而**没有**后续操作的卡（Hoothoot TEF 126 / Espurr SSP 84 /
# Pidove BLK 71）故意不映射：状态变化为零，收进池只会给 AI 多出一个零收益
# 合法招式 —— 按 C5 宁可留在未建模，见 docs/RULES_COMPLETION_PLAN.md。
_OPP_HAND_PATTERNS = (
    # Rotom SSP 61 → opp_hand:discard:item+tool:all
    (re.compile(r"your opponent reveals their hand[.]\s*discard all item cards? "
                r"and pok[eé]mon tool cards? you find there[.]?$"),
     {"op": "discard", "sel": "item+tool", "n": "all"}),
    # Energy Swatter POR 73 → opp_hand:bottom:energy:1
    (re.compile(r"your opponent reveals their hand,? and you choose an? energy card "
                r"you find there and put it on the bottom of their deck[.]?$"),
     {"op": "bottom", "sel": "energy", "n": 1}),
    # N's Purrloin JTG 96 → opp_hand:bottom:any:1
    (re.compile(r"your opponent reveals their hand,? and you choose a card "
                r"you find there and put it on the bottom of their deck[.]?$"),
     {"op": "bottom", "sel": "any", "n": 1}),
    # Eri TEF 146 → opp_hand:discard:item:<n>
    # "up to N" 取满 = 规则允许的最大效果（弃对手资源，对使用者总是更优）；
    # 实际张数受「手牌里符合条件的张数」约束，由引擎侧取 min。
    (re.compile(r"your opponent reveals their hand,? and you discard up to (\d+) "
                r"item cards? you find there[.]?$"),
     {"op": "discard", "sel": "item", "n": None}),
    # Luxray ex TWM 68 → opp_hand:discard:any:1
    (re.compile(r"your opponent reveals their hand,? and you discard a card "
                r"you find there[.]?$"),
     {"op": "discard", "sel": "any", "n": 1}),
)


def _detect_opp_hand_op(t: str) -> Optional[dict]:
    """整句识别「揭示对手手牌 + 对其操作」；认不出返回 None。

    返回 ``{"op": "discard|bottom", "sel": "<筛选式>", "n": int|"all"}``。

    两级约束缺一不可：
      · `match` 锚定**句首** —— 该族唯一标识是开头的 "your opponent reveals
        their hand"；若允许出现在句中，「A. Your opponent reveals … B.」这类
        多句式里前半句的正当动作会被整段吞掉；
      · 正则以 ``$`` 收尾 —— 只认句首却不做整句匹配时，后面若还跟着别的
        效果会被**静默丢弃**（与 recon 族「凭空加条件」是同一类陷阱的反面）。
    """
    t = _norm_prose(t)
    for rx, meta in _OPP_HAND_PATTERNS:
        m = rx.match(t)
        if not m:
            continue
        n = meta["n"]
        if n is None:                   # "up to N"
            n = int(m.group(1))
            if n < 1:
                return None
        return {"op": meta["op"], "sel": meta["sel"], "n": n}
    return None


_EN_SEARCH_RE = re.compile(r"search your deck for\s+(?:up to\s+)?(\d+)?", re.I)
_EN_SEARCH_ANY_RE = re.compile(r"search your deck for\s+(?:up to\s+)?(\d+)?"
                               r"[^.]*", re.I)
# 「any number of Basic Energy cards of different types」（2026-11-02 第四十批 ·
# Energy Search Pro SSP 176）：数量是自由选择，但**备选集合被封死**（每种属性
# 至多 1 张）→ 上限 = 牌库里出现的基本能量属性种类数，不需要交互层。
# ⚠ 与「up to N」是不同的写法：`up to` 的上限是常数，`any number` 不是。
_SEARCH_ANY_DISTINCT = re.compile(
    r"search your deck for any number of basic energy cards of different types",
    re.I)
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

# 附着能量收回**手牌**（2026-10-29 第二十三批）：
#   「Put an Energy attached to this Pokémon into your hand.」（Shiftry / Landorus 等）
#   「Put 2 {R} Energy attached to this Pokémon into your hand.」（Volcanion）
#   「Put an Energy attached to your opponent's Active Pokémon into their hand.」
# → `bounce:energy:<self|opp_active>:<n>[:type:<c>]`
_EN_ENERGY_TO_HAND_RE = re.compile(
    r"put\s+(an?|(?:\d+))\s+(?:\{([a-z])\}\s+)?energy\s+attached\s+to\s+"
    r"(this\s+pok[eé]mon|your\s+opponent[\u2019']?s\s+active\s+pok[eé]mon)\s+"
    r"into\s+(?:your|their)\s+hand", re.I)

# 弃置对手战斗位的**特殊**能量（2026-10-29 第二十四批）：
#   「Discard a Special Energy from your opponent's Active Pokémon.」（Cobalion）
# → `discard_energy:1:opp:special`（引擎按 `senergy` 的**卡**弃置）。
# 普通 `discard_energy:…:opp` 走 `remove_any`（无色/基本优先）——对手只贴基本
# 能量时会把「本不该生效」的效果生效掉，属静默错算。
_SP_OPP_ACTIVE_RE = re.compile(
    r"(?:discard|remove)\s+(?:(a|an|1|all)\s+)?special\s+energy\s+from\s+"
    r"your\s+opponent[\u2019']?s\s+active\s+pok[eé]mon", re.I)


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
# ---- S5 对手手牌信息族（2026-10-28）----
# 只做**卡面本身就写 random** 或**数量确定**的形态；「你选一张」属隐藏信息交互，
# 引擎绝不代替玩家选择（会变成"总能丢到对手关键卡"）。
_EN_OPP_DISCARD_RANDOM_RE = re.compile(
    r"discard a random card from your opponent'?s hand", re.I)
_EN_OPP_SHUFFLE_RANDOM_RE = re.compile(
    r"choose a random card from your opponent'?s hand.{0,90}?"
    r"shuffles? it into their deck", re.I | re.S)
_EN_OPP_DISCARD_TO_RE = re.compile(
    r"your opponent discards cards from their hand until they have (\d+) cards",
    re.I)
_EN_OPP_HAND_SAME_RE = re.compile(
    r"your opponent counts the cards in their hand, shuffles those cards, and "
    r"puts them on the bottom of their deck.{0,80}?they draw that many cards",
    re.I | re.S)
# ---- S6 能量移动（2026-10-28）----
# 「Move an Energy from this Pokémon to 1 of your Benched Pokémon.」（Iron Thorns ex）
# 「Move up to 2 Energy from your Benched Pokémon to your Active Pokémon.」（N's Plan）
# 「Move a Basic Energy from 1 of your Pokémon to another of your Pokémon.」（Energy Switch）
_EN_ENERGY_MOVE_RE = re.compile(
    r"move\s+(?:up to\s+)?(?P<n>\d+|an?|one)?\s*"
    r"(?:basic\s+)?(?:\{(?P<c>[a-z])\}\s*)?energ(?:y|ies)\s+from\s+"
    r"(?P<src>[^.]{0,70}?)\s+to\s+(?P<dst>[^.]{0,70})", re.I)
# v1.3 未建模、2026-09-22 补上：**磨牌**（Discard the top N cards of your /
# your opponent's deck）。此前整类落进「静态/未建模（英文）」→ 卡不可对战
# （B3 的 `discard_self` 29 条里有 20 条是它）。引擎新增 `mill:N[:opp]`。
_EN_MILL_RE = re.compile(
    r"discard\s+the\s+top\s+(?:(\d+)\s+)?cards?\s+of\s+"
    r"(your\s+opponent[\u2019']?s?|your)\s+deck", re.I)
# ---- S6 弃道具 / 弃场地（2026-10-28）----
# 「Before doing damage, discard all Pokémon Tools from your opponent's Active
#  Pokémon.」（Arven's Skwovet / Larvesta / Spearow / Raticate）
_EN_DISCARD_TOOLS_RE = re.compile(
    r"discard all pok[eé]mon tools?(?: and special energy)? from "
    r"(?P<where>[^.]{0,60})", re.I)
# 「Choose up to 2 Pokémon Tools attached to Pokémon (yours or your opponent's)
#  and discard them.」（Tool Scrapper）
_EN_CHOOSE_TOOLS_RE = re.compile(
    r"choose up to (\d+) pok[eé]mon tools? attached to pok[eé]mon", re.I)
# 伤害前阶段（2026-10-29 第二十五批）：
#   「Before doing damage, discard all Pokémon Tools from your opponent's Active
#    Pokémon.」→ `pre:discard_tool:all:opp_active`
#   「Before doing damage, discard all Pokémon Tools **and Special Energy** …」
#    → 再加上 `pre:discard_energy:all:opp:special`（Mow Rotom）
# 时序错（放到伤害之后）会让守方道具先减伤一次再被弃 —— 静默少做。
_IF_PRE_TOOL_RE = re.compile(
    r"if\s+you\s+discarded\s+a\s+pok[eé]mon\s+tool\s+in\s+this\s+way", re.I)
# 「Discard a Stadium in play.」（Mow Rotom / Eternatus）/
# 「You may discard a Stadium in play. If you do, …」（Cetitan ex）/
# 「Then, discard that Stadium.」（Chi-Yu / Ting-Lu / Mega Hawlucha ex）
_EN_DISCARD_STADIUM_RE = re.compile(
    r"discard (?:that|a) stadium", re.I)
# ---- S6 回手 / 洗回牌库（2026-10-28）----
# 「Put this Pokémon and all attached cards into your hand.」（Meowth ex）
# 「Put this Pokémon into your hand. (Discard all cards attached …)」（Crobat ex）
_EN_BOUNCE_SELF_HAND_RE = re.compile(
    r"put this pok[eé]mon(?: and all attached cards)? into your hand", re.I)
# 「Shuffle this Pokémon / it and all attached cards into your deck.」（Abra / Lokix）
_EN_BOUNCE_SELF_DECK_RE = re.compile(
    r"shuffle (?:this pok[eé]mon|it)(?: and all attached cards)? into your deck",
    re.I)
_EN_BOUNCE_BENCH_RE = re.compile(
    r"put 1 of your benched pok[eé]mon(?: and all attached cards)? into your hand",
    re.I)
_EN_BOUNCE_CHOOSE_RE = re.compile(
    r"put 1 of your pok[eé]mon(?: and all attached cards)? into your hand", re.I)
# 「Put up to 3 Duskull … onto your Bench.」/「put a Basic Pokémon with 70 HP or
#  less from your discard pile onto your Bench.」（Duskull / Alomomola）
_EN_DISCARD_TO_BENCH_RE = re.compile(
    r"put (?:up to )?(?P<n>\d+|a|an) (?P<body>[^.]{0,90}?)"
    r" from your discard pile onto your bench", re.I)
_EN_SWITCH_RE = re.compile(
    r"switch\s+(?:your|out|it)|with\s+(?:your|1 of your)\s+(?:benched|active)"
    r"|switch your active|switch this pok[eé]mon with", re.I)
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
# 检索**筛选**里的抗性短语（Fletchling「Pokémon with {F} Resistance」）不是
# 静态规则修改 —— 判定 `_EN_MODIFY_RE` 前把它屏蔽（等长空格，位置守恒）。
_EN_RESISTANCE_FILTER_RE = re.compile(r"with\s+\{[a-z]\}\s*resistance", re.I)
# ---------------- 锁定类跨回合效果（2026-10-27） ----------------
# 卡面原型：
#   During your next turn, this Pokémon can't use attacks.              （自身 1 回合）
#   During your next turn, this Pokémon can't use Giant Wave.           （指定招式 1 回合）
#   This Pokémon can't use Blaze Blitz again until it leaves the Active Spot.
#   During your opponent's next turn, the Defending Pokémon can't retreat.
# 这些**不是**一次性动作，而是跨回合状态：引擎按 `turn_counts` 到期，
# 不能折算成「本回合不能攻击 / 撤退」（那会在错误的时间窗生效）。
_LOCK_TARGET = (r"(?P<target>this pok[eé]mon|the defending pok[eé]mon|"
                r"your opponent'?s active pok[eé]mon|that pok[eé]mon)")
# 第六十三批（2026-11-04）：同一锁有两种动词写法 ——
#   「… can't use attacks.」（旧，复数名词） / 「… can't attack.」（Floatzel /
#   Steven's Metang，动词）。只差一个词，漏了后者整段落 rule 兜底。
# ⚠ 必须用 ``attack\b`` 而不是 ``attack``：否则会把 `can't use attacks` 的
#   前半截也吞掉（复数 s 不是词边界，故 \b 天然排除），且 `_ATTACK_LOCK_NAMED_RE`
#   的 ``(?!attacks\b)`` 与这里分工不变（它只管 `can't use <招式名>`）。
# ⚠ 「can't attack unless …」是**常驻条件**（Team Rocket's Mewtwo ex），不带
#   「During your next turn」前缀，本正则的前半段天然拒收，绝不能放宽。
_ATTACK_LOCK_ALL_RE = re.compile(
    r"during your (?P<who>opponent'?s\s+)?next turn,?\s*" + _LOCK_TARGET
    + r"\s+can'?t (?:use attacks|attack\b)", re.I)
_ATTACK_LOCK_NAMED_RE = re.compile(
    r"during your (?P<who>opponent'?s\s+)?next turn,?\s*" + _LOCK_TARGET
    # `that attack` 是**指代前文所选招式**，不是具体招式名 → 交给
    # `_ATTACK_LOCK_CHOOSE_RE`（scope=opp_name）处理，绝不能当卡名。
    + r"\s+can'?t use (?!attacks\b)(?!that attack\b)(?P<atk>[^.;]+?)\s*[.]",
    re.I)
_ATTACK_LOCK_ACTIVE_RE = re.compile(
    r"(?P<target>this pok[eé]mon)\s+can'?t use (?P<atk>[^.;]+?)\s+again "
    r"until it leaves the active spot", re.I)
# 「Choose 1 of your opponent's Active Pokémon's attacks. During your opponent's
# next turn, that Pokémon can't use that attack.」（Klefki「Memory Lock」/
# Team Rocket's Murkrow「Torment」）—— 锁对手战斗位的**指定**招式，但招式名在
# 出招时由玩家**运行时选择**（参考 snipe 的 action['snipe'] 选择机制），解析时
# 无法得知 → 记为 scope=opp_name，引擎侧从 action['lock_attack'] 取所选招式名。
_ATTACK_LOCK_CHOOSE_RE = re.compile(
    r"choose 1 of your opponent's active pok[eé]mon's attacks?\.?\s*"
    r"during your opponent's next turn,?\s*that pok[eé]mon "
    r"can'?t use that attack", re.I)
_RETREAT_LOCK_RE = re.compile(
    r"during your (?P<who>opponent'?s\s+)?next turn,?\s*" + _LOCK_TARGET
    + r"\s+can'?t retreat", re.I)
# 手牌禁打（跨回合）：只认**招式**明确写出的两种可执行形态。
#   · Item：Budew / Frillish / Galvantula ex
#   · 进化：Bronzong「Evolution Jammer」
# 常驻特性形态（Tyranitar / Jellicent ex / Genesect / Arbok）生命周期不同，
# 仍走「静态/未建模」兜底，不在这里映射。
_ITEM_LOCK_RE = re.compile(
    r"during your opponent'?s next turn,?\s*(?:they|your opponent) "
    r"can'?t play any item cards? from their hand", re.I)
_EVOLUTION_LOCK_RE = re.compile(
    r"during your opponent'?s next turn,?\s*(?:they|your opponent) "
    r"can'?t play any pok[eé]mon from their hand to evolve", re.I)
# 第一百零七批（2026-11-06）：支援者 / 场地两类（Scream Tail ex / Chi-Yu）。
# ⚠ 与 `fx:play_block:` 的**常驻**形态是两回事：这里封的是「下个回合」。
# ⚠ 语序**两种**都要认：前置（during your opponent's next turn, …）与后置
#   （… from their hand **during their next turn**）。Scream Tail ex / Chi-Yu
#   都是后置写法，只认前置会静默漏锁。
_SUPPORTER_LOCK_RE = re.compile(
    r"during your opponent'?s next turn,?\s*(?:they|your opponent) "
    r"can'?t play any supporter cards? from their hand|"
    r"can'?t play any supporter cards? from their hand during their next turn",
    re.I)
_STADIUM_LOCK_RE = re.compile(
    r"during your opponent'?s next turn,?\s*(?:they|your opponent) "
    r"can'?t play any stadium cards? from their hand|"
    r"can'?t play any stadium cards? from their hand during their next turn",
    re.I)
# 已识别为「伤害增减」但引擎**没有**静态修正层的写法（2026-10-27）：
# 这些句子不含 weakness/resistance，若只靠 `_EN_MODIFY_RE` 会漏登记，
# 导致「锁已建模、减伤却没建模」的卡被静默收进池。命中即如实登记。
_UNMODELED_DAMAGE_MOD_RE = re.compile(
    r"takes? \d+ more damage from attacks|"
    r"attacks used by [^.]{0,80}?do(?:es)? \d+ (?:more|less) damage",
    re.I)
# 已建模的 item/evolution 封锁句会被 span 屏蔽；剩余的手牌禁打（Supporter /
# Stadium / ACE SPEC / 带特性宝可梦…）以及常驻特性禁令仍需如实登记。
_STATIC_PLAY_LOCK_RE = re.compile(
    r"can'?t play any [^.]{1,80}? from (?:their|your) hand", re.I)
# 首回合限制：Terapagos ex「若后攻，首个回合不能用此招式」。
# 引擎没有「招式静态使用条件」层，且不能在招式结算时补记，命中即未建模。
_FIRST_TURN_ATTACK_RE = re.compile(
    r"can'?t use this attack during your first turn|"
    r"can use this attack only if you go second", re.I)
# 「从手牌弃 N 张基本能量」+「弃不够则此招式什么都不做」：
# 引擎 `discard:N` 弃任意手牌、`discard_energy:N` 不区分能量类型，
# 二者都不满足此语义 → 如实登记，不静默按近似执行。
_TYPED_HAND_DISCARD_RE = re.compile(
    r"discard \d+ basic \{[a-z]\} energy cards? from your hand", re.I)
# 第八十八批（2026-11-05）：单数形态「Discard a Basic {G} Energy card from your
# hand.」—— 上面那条要求数字，单数（a/an）会漏认领 → 整卡不收。引擎
# `discard_hand_energy:1:type:<c>[:required]` 已支持该弃置来源+属性限定。
_TYPED_HAND_DISCARD_ONE_RE = re.compile(
    r"discard (?:a|an) basic \{[a-z]\} energy card from your hand", re.I)
_FAILED_DISCARD_GATE_RE = re.compile(
    r"if you can'?t discard .{0,40}this attack does nothing", re.I)
# 第八十八批（2026-11-05）：单数手牌弃能常写「If you can't, this attack does
# nothing.」（Decidueye「Power Shot」，伤害 170）。与上面那条**同一语义**，只是
# 省略了 discard 宾语。⚠ 仅用于带类型手牌弃能分支的 `:required` 判定，**不并入**
# 全局失败门 —— 否则「无 typed 弃能」的招式会被误登记为 B5 未建模。
_FAILED_COST_NOTHING_RE = re.compile(
    r"if you can'?t, this attack does nothing", re.I)
# 「弱点 / 抗性无视」（2026-10-27）：引擎在伤害结算时跳过对应步骤
# （`ignore_weakness` / `ignore_resistance`，见 rules.apply_weakness_resistance）。
# 只认**无条件**的「This attack's/This damage isn't affected by …」写法。
_WR_BYPASS_RE = re.compile(
    r"(?:this attack[\u2019']?s|this) damage isn[\u2019']?t affected by "
    r"(?P<wr>weakness(?:\s+or\s+resistance)?|resistance)", re.I)
# 「… isn't affected by Weakness or Resistance, **or by any effects on …**」：
# 后半句是引擎**没有**的「无视目标身上效果」层（B5）。只映射 W/R 会静默
# 丢掉效果无视 → 命中即整条如实登记未建模，不产出 bypass tag。
_WR_BYPASS_EFFECTS_RE = re.compile(
    r"isn[\u2019']?t affected by weakness or resistance,?\s*"
    r"or by any effects on [^.]*\.", re.I)
# 未建模的「招式失败门」：「If …, this attack does nothing.」
# ⚠ 抛币形态（Flip a coin. If tails, … does nothing.）已由
# `coin:tails:dmg_zero` 精确建模，**不能**在这里重复登记（否则会误拒
# Timburr / Mankey 等已正确建模的卡）。
_ATTACK_DOES_NOTHING_RE = re.compile(r"this attack does nothing", re.I)
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
    """附加伤害 / 放置指示物子句 → ``(scope, count, need_damaged)``；不可精确表达 → ``None``

    ``scope``：
      ``"opp_active"`` 打对手战斗位（= 招式 damage 字段承载，**无需 tag**）
      ``"self"``       打自己（反伤 / 自身放置指示物）
      ``"opp"`` / ``"opp_bench"`` / ``"own_bench"`` / ``"both_bench"``  → `snipe:` tag
    ``need_damaged``：卡面带「that has any damage counters on it」→ 引擎
    ``:target:damaged`` 目标筛选（2026-10-29 第二十七批，Excadrill ex / Hippowdon）。
    其它不可精确表达的形态（其它子集限定 / 缩放 / 动态目标）返回 ``None``，
    由调用方**如实登记未建模**（宁缺勿错）。
    """
    c = _norm_apostrophe(clause).lower()
    # 「that has any damage counters on it」是引擎能**精确执行**的目标筛选
    # （打完再把指示物算进伤害是错的，必须只挑**已有**指示物的目标）。
    # ⚠ 必须在 `_SNIPE_SUBSET_RE` 之前剥离 —— 那条守卫会把任何 `that has`
    #   都当成「不可表达子集」拒绝。
    need_damaged = bool(
        re.search(r"that (?:has|have) any damage counters? on it", c))
    if need_damaged:
        c = re.sub(r"\s*that (?:has|have) any damage counters? on it", "", c)
    # 「…Pokémon {ex}」：引擎 `:target:ex` 精确筛选（2026-11-03 第四十一批）。
    # 第九十八批（2026-11-06）：**「{ex} or/and {V}」也按 `:target:ex` 处理** ——
    #   本卡池 1267 张**没有 Pokémon V**（引擎里也没有 `is_v` 这个判定），
    #   所以并集与 {ex} 在当前数据上等价。
    # ⚠ 这是**有前提的等价**：`test_batch98_parse` 里有一条守卫，一旦数据集
    #   出现 V 类卡就立刻失败，强制回来重新评估（而不是让并集静默算少）。
    need_ex = False
    if re.search(r"pok[eé]mon \{ex\}", c):
        need_ex = True
        # 先剥掉「Pokémon {V}」整段，再去掉残留的 or/and 与 `{ex}` 记号。
        # 「each of your opponent's Pokémon {ex} and Pokémon {V}」若只删 `{ex}`，
        # 剩下的 `and pokémon {v}` 会落进 `_SNIPE_SUBSET_RE` 被判成不可表达。
        c = re.sub(r"\s*(?:or|and)?\s*pok[eé]mon\s*\{v\}", "", c, flags=re.I)
        c = re.sub(r"\s*(?:or|and)\s*$", "", c)
        c = re.sub(r"\s*\{ex\}", "", c)
    # ⚠ 顺序要紧：先判「战斗位 / 自身」，再判子集 —— 否则
    #   「does 80 damage to your opponent's Active Pokémon for each tails」
    #   会因 "for each" 被判成不可表达（而那本来就是普通主伤害）。
    if "bench" not in c and ("active pok" in c or "defending pok" in c):
        return ("opp_active", None, need_damaged, need_ex)
    if re.search(r"\bitself\b", c) or "this pok" in c or "your active pok" in c:
        return ("self", None, need_damaged, need_ex)
    if _SNIPE_SUBSET_RE.search(c):
        return None
    if re.search(r"each of your opponent'?s? benched", c):
        return ("opp_bench", "all", need_damaged, need_ex)
    if re.search(r"each of your opponent'?s? pok", c):
        return ("opp", "all", need_damaged, need_ex)
    if re.search(r"each of your (?:own )?benched", c):
        return ("own_bench", "all", need_damaged, need_ex)
    if re.search(r"each benched", c):
        return ("both_bench", "all", need_damaged, need_ex)
    m = re.search(r"(\d+)\s+of your opponent'?s? benched", c)
    if m:
        return ("opp_bench", int(m.group(1)), need_damaged, need_ex)
    m = re.search(r"(\d+)\s+of your opponent'?s? pok", c)
    if m:
        return ("opp", int(m.group(1)), need_damaged, need_ex)
    m = re.search(r"(\d+)\s+of your (?:own )?benched", c)
    if m:
        return ("own_bench", int(m.group(1)), need_damaged, need_ex)
    return None


def _split_snipe_scale(clause: str, tail: str) -> tuple:
    """附加伤害子句 → ``(目标子句, 是否有缩放, 计数源)``

    「for each <phrase>」既可能写在子句内（Raging Bolt：「… for each Energy
    attached to this Pokémon」），也可能紧跟句尾（Kyurem ex：「… for each Prize
    card your opponent has taken」）。

    **必须区分「没有缩放」与「有缩放但计数源认不出」**：后者要如实登记未建模，
    否则会把 `for each Prize card` 按固定值映射 → 静默低估伤害。
    「for each heads / tails」不是计数缩放（那是抛币枚数，由 `_detect_coin` 处理）
    → 按**无缩放**返回，保持既有行为。
    """
    c = _norm_apostrophe(clause or "")
    m = re.search(r"\s+for each\s+(.+)$", c, re.I)
    if m:
        phr = re.split(r",|\.| \(", m.group(1))[0].strip()
        if re.fullmatch(r"heads?|tails?", phr.lower()):
            return c[:m.start()].strip(), False, None
        return c[:m.start()].strip(), True, _counter_from_phrase(phr)
    t = _norm_apostrophe(tail or "")
    mt = re.match(r"\s*for each\s+(.+?)(?:\.|,|\(|$)", t, re.I)
    if mt:
        phr = mt.group(1).strip()
        if re.fullmatch(r"heads?|tails?", phr.lower()):
            return c.strip(), False, None
        return c.strip(), True, _counter_from_phrase(phr)
    return c.strip(), False, None


def _detect_lock_actions(text: str, kind: str,
                         name: str = "") -> tuple:
    """攻击封锁 / 撤退封锁 / 手牌禁打 → ``(actions, spans)``

    只认 **招式效果**（kind=attack）。特性里的同名写法（「As long as this Pokémon
    is in the Active Spot, your opponent's Active Pokémon can't retreat.」）是
    常驻静态锁，生命周期完全不同（不随回合到期）→ 故意不映射，仍如实登记未建模。

    ``spans`` 用于在后续 `_EN_MODIFY_RE` / 禁打兜底判定时屏蔽这些已建模的句子，
    避免同一句既产出 lock 又落一条「静态规则修改 / 未建模」→ 整卡被误拒。
    """
    if kind != "attack":
        return [], []
    t = _norm_apostrophe(text or "")
    out: list = []
    spans: list = []

    def _target_of(m, who: bool | None = None) -> tuple:
        """返回 ``(action_target, owner_is_opponent, ok)``"""
        target = (m.group("target") or "").lower()
        if who is None:
            who = bool(m.group("who")) if "who" in m.groupdict() else False
        is_self = target.startswith("this")
        # 「your next turn」只能锁自己；「your opponent's next turn」只能锁对手。
        # 交叉写法没有真实卡例，判不出就拒绝（宁缺勿错）。
        if is_self == who:
            return "", False, False
        return ("self" if is_self else "active_opponent"), (not is_self), True

    for m in _ATTACK_LOCK_ALL_RE.finditer(t):
        tgt, _, ok = _target_of(m)
        if not ok:
            continue
        out.append(act("attack_lock", None, tgt,
                       {"scope": "all", "until": "turn"}))
        spans.append(m.span())

    for m in _ATTACK_LOCK_NAMED_RE.finditer(t):
        tgt, _, ok = _target_of(m)
        if not ok:
            continue
        atk = re.sub(r"\s+again\b.*$", "", m.group("atk").strip(), flags=re.I)
        canon = _canonical_attack_name(atk, name)
        if not canon:
            continue
        out.append(act("attack_lock", None, tgt,
                       {"scope": "name", "attack": canon, "until": "turn"}))
        spans.append(m.span())

    for m in _ATTACK_LOCK_ACTIVE_RE.finditer(t):
        atk = re.sub(r"\s+again\b.*$", "", m.group("atk").strip(), flags=re.I)
        canon = _canonical_attack_name(atk, name)
        if not canon:
            continue
        out.append(act("attack_lock", None, "self",
                       {"scope": "name", "attack": canon, "until": "active"}))
        spans.append(m.span())

    for m in _RETREAT_LOCK_RE.finditer(t):
        tgt, _, ok = _target_of(m)
        if not ok:
            continue
        if (m.group("target") or "").lower().startswith("that"):
            # 「that Pokémon」必须指代前文已出现的对手战斗位（Poison Ring 等），
            # 否则无法确认目标 → 不映射。
            if not re.search(r"your opponent'?s active pok[eé]mon",
                             t[:m.start()], re.I):
                continue
        out.append(act("retreat_lock", None, tgt,
                       {"until": "turn"}))
        spans.append(m.span())

    for m in _ITEM_LOCK_RE.finditer(t):
        out.append(act("play_lock", None, "active_opponent",
                       {"play": "item", "until": "turn"}))
        spans.append(m.span())

    for m in _EVOLUTION_LOCK_RE.finditer(t):
        out.append(act("play_lock", None, "active_opponent",
                       {"play": "evolution", "until": "turn"}))
        spans.append(m.span())

    for m in _SUPPORTER_LOCK_RE.finditer(t):
        out.append(act("play_lock", None, "active_opponent",
                       {"play": "supporter", "until": "turn"}))
        spans.append(m.span())

    for m in _STADIUM_LOCK_RE.finditer(t):
        _ex = {"play": "stadium", "until": "turn"}
        # 「…, discard it. **If you do,** your opponent can't play any Stadium …」
        # （Chi-Yu）—— 没弃到场地就没有锁。用 `:if_prev` 串起前一个动作。
        if re.search(r"if you do,?\s*$", t[:m.start()], re.I):
            _ex["if_prev"] = True
        out.append(act("play_lock", None, "active_opponent", _ex))
        spans.append(m.span())

    # 「Choose 1 of opponent's Active Pokémon's attacks … that Pokémon can't use
    # that attack」→ scope=opp_name（对手战斗位**指定**招式，招式名运行时选择）。
    # 必须放在最后：它认领整段，避免被上面的 named/all 分支漏判或重复登记。
    for m in _ATTACK_LOCK_CHOOSE_RE.finditer(t):
        out.append(act("attack_lock", None, "active_opponent",
                       {"scope": "opp_name", "until": "turn"}))
        spans.append(m.span())

    return out, spans


def _detect_wr_bypass(text: str) -> tuple:
    """弱点 / 抗性无视 → ``(actions, spans)``

    只处理**无条件**的 `This attack's damage isn't affected by …`。
    第六十五批（2026-11-04）之前，`… or by any effects on …` 一律如实登记
    未建模（当时引擎没有「无视守方效果」层）。现在引擎已有**两档**：
      · `ignore_effects`      只无视守方身上的**效果**（窄口径，旧句式）
      · `ignore_effects:wr`   连**弱点/抗性**一并无视（宽口径，本句式）
    故这里改为产出 `ignore_effects:wr`，且把 span 交给调用方 mask，避免
    同一句又被 `_EN_MODIFY_RE` 记成「静态规则修改」而整卡被拒收。
    ⚠ 认领权**只在这里**（`search` 可命中整段中的第二句）；
      `_detect_static_rules` 不再处理含 Weakness/Resistance 的句式，
      否则同一句会产出两条 tag。
    """
    t = _norm_apostrophe(text or "")
    m = _WR_BYPASS_RE.search(t)
    if not m:
        return [], []
    me = _WR_BYPASS_EFFECTS_RE.search(t)
    if me:
        return [act("rule", None, "any", {
            "ignore_effects_wr": True})], [me.span()]
    wr = m.group("wr").lower()
    out: list = []
    if "weakness" in wr:
        out.append(act("rule", None, "active_opponent",
                       {"ignore_weakness": True}))
    if "resistance" in wr:
        out.append(act("rule", None, "active_opponent",
                       {"ignore_resistance": True}))
    return out, [m.span()]


def _canonical_attack_name(captured: str, known: str = "") -> str:
    """卡面捕获的招式名 → tag 中的规范名（优先用 item 的招式名）"""
    cap = str(captured or "").strip()
    known = str(known or "").strip()
    if known and cap and _norm_apostrophe(cap).lower() == \
            _norm_apostrophe(known).lower():
        return known
    # 卡面名可能带弯撇号；tag 只保留可比较的稳定文本
    return _norm_apostrophe(cap).strip()


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
    # 「if you have any Tera Pokémon in play」（第一百零二批，Noctowl「Jewel
    # Seeker」）—— 含后备。与 `own_bench_tera`（只看后备）不同。
    (r"if you have any tera pok[eé]mon in play", "tera_in_play"),
    # 「if you have <具名> in play」（第一百一十一批，Lunatone「Lunar Cycle」）
    # —— 带参，由 `_condition_from_text` 的专用分支处理，不在此表。
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
    # 「flip 2 coins. If **both of them are** heads, …」也是分支——早期正则只认
    # 「if heads / if tails」，于是 Energy Coin 这类**双币**分支里的检索被当成
    # 无条件效果（必然发生的检索 + 附能，强度翻倍）。
    for m in re.finditer(
            r"if\s+(?:both|all)(?:\s+of\s+them\s+are)?\s+(heads|tails)\b"
            r"|if\s+(heads|tails)\b", head, re.I):
        last = m
    if last is None:
        return False
    return "." not in head[last.end():]


def _in_coin_branch_any(text: str, pos: int) -> bool:
    """同 `_in_coin_branch`，但额外识别多币条件（either / at least N）

    多币条件目前**没有**对应的 coin 子 tag；状态层用它把「不能精确表达的
    抛币状态」登记为未建模，而不是当成无条件状态施加。
    """
    head = text[:pos]
    last = None
    for m in re.finditer(
            r"if\s+(?:both|all)(?:\s+of\s+them\s+are)?\s+(heads|tails)\b"
            r"|if\s+(?:either|at least\s+\d+)\s+of\s+them\s+(?:is|are)\s+"
            r"(heads|tails)\b"
            r"|if\s+(heads|tails)\b", head, re.I):
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
    # ⚠ 顺序关键：**先判训练家子类**。「a Pokémon Tool card」「an Item card」
    # 都含 "pokémon" 字样，先判 pokemon 会把它们错映射成「检索宝可梦」
    # → 引擎去牌库拿宝可梦（静默拿错东西）。实测 4 张（Meltan / Wiglett /
    #   Team Rocket's Mareep / Hop's Silicobra / Team Rocket's Murkrow）。
    if re.search(r"\btool card\b|\bitem card\b|\bstadium card\b|"
                 r"\bsupporter card\b|\btrainer card\b", seg):
        return "trainer"
    if "pokémon" in seg or "pokemon" in seg:
        return "pokemon"
    if "trainer" in seg:
        return "trainer"
    if "energy" in seg:
        return "energy"
    return "any"


# 「多类别并列检索」（2026-11-02 第三十六批）：
#   Search your deck for **an Item card, a Pokémon Tool card, a Supporter card,
#   and a Stadium card** …
# 卡面要求**逐类别各取 1 张**（合计 4 张、类别互不相同）。通用分支只看第一个
# 类别短语，产出 `search_trainer:1` —— **张数（4→1）与类别（逐类各一→任一训练家）
# 双错**，是静默削弱。引擎没有「多类别各取 1」的检索形态 → 如实拒绝。
# 只认「an? … card, an? … card」这一种并列（同一类别写两次的卡不存在，
# 而 `search your deck for a Pokémon, reveal it` 之类不含第二个 `card`，不受影响）。
_MULTI_KIND_SEARCH = re.compile(
    r"search your deck for an? [^.,]*?card,\s*an? [^.,]*?card", re.I)


def _multi_search_alts(text: str) -> Optional[list]:
    """「逐类别各取 1 张」的并列检索 → 引擎筛选项列表（第七十九批）

    卡面原型（Secret Box）：「Search your deck for an Item card, a Pokémon Tool
    card, a Supporter card, and a Stadium card, reveal them, and put them into
    your hand.」——四个类别**各 1 张**，不能退化成「任意类别 1 张」或「共 1 张」。

    只认**完整且可枚举**的四类写法；出现任何不认识的类别短语（例如「a Basic
    Pokémon」）→ 返回 None，由调用方如实拒绝（绝不猜类别）。
    """
    t = _norm_prose(text)
    # 第九十二批（2026-11-05）：第一个类别可能**不带 "card"** 后缀 ——
    # Larry's Skill PRE 115「search your deck for **a Pokémon**, a Supporter
    # card, and a Basic Energy card」；旧正则要求每段都以 `card,` 结尾，
    # 于是整句漏判 → 退化成 `search_pokemon:1`（**少拿支援者与基本能量**，
    # 静默削弱）。放宽为「取到 reveal 子句前的整段并列」，再逐段判类别；
    # 认不出的类别仍返回 None（绝不猜）。
    m = re.search(
        r"search your deck for (?P<list>(?:an? )?[^.]*?),"
        r" reveal them, and put them into your hand", t)
    if not m:
        return None
    chunks = re.split(r",\s*(?:and\s+)?", m.group("list"))
    kinds: list = []
    for ch in chunks:
        ch = ch.strip(" .")
        found = None
        for pat, kind in ((r"pok[eé]mon tool card", "tool"),
                          (r"item card", "item"),
                          (r"supporter card", "supporter"),
                          (r"stadium card", "stadium"),
                          # ⚠ `basic energy card` 必须排在 `energy card` **之前**：
                          # 否则「a Basic Energy card」会被判成 `energy`，丢掉
                          # basic 限定 → 连特殊能量一起检索（静默放宽）。
                          (r"basic energy card", "basic_energy"),
                          (r"energy card", "energy"),
                          (r"trainer card", "trainer"),
                          (r"pok[eé]mon card", "pokemon"),
                          # 「a Pokémon」（无 card 后缀，Larry's Skill）
                          (r"pok[eé]mon(?!\w)", "pokemon")):
            if re.search(r"(?:a|an) " + pat, ch):
                found = kind
                break
        if not found:
            return None
        kinds.append(found)
    # 至少两类才算「逐类别各取」；单类别交给通用检索路径（避免改变既有口径）
    return kinds if len(kinds) >= 2 else None


def _search_any_named(src: str):
    """「any number of <具名> Pokémon/cards」→ dict | None（第一百/一百零一批）

    三种写法都要认：
      · ``any number of Basic Lillie's Pokémon``（Lillie's Comfey）
      · ``any number of Pokémon that have "Rotom" in their name``（Rotom）
      · ``any number of Fennel cards``（Musharna）

    ⚠ 为什么「任意张」这次**不需要交互层**（与 `any number of` 弃牌类不同）：
    备选集合被**卡名**封死，而且这是**检索**——拿到手/铺上场都是纯增益，
    不存在「取 0 更好」的场合（与 Precious Trolley 的 `search_bench:all` 同口径）。
    """
    t = re.sub(r"\s+", " ", _norm_apostrophe(src or "")).strip()
    m = re.search(r"any number of (?:basic )?pok[eé]mon that have "
                  r"[\u201c\u201d\"']?(?P<a>[a-z' ]+?)[\u201c\u201d\"']? "
                  r"in their name", t, re.I)
    if m:
        key = _tag_name_key(m.group("a"))
        return ({"name_has": key, "basic": False, "kind": "pokemon"}
                if key else None)
    m = re.search(r"any number of (?P<b>basic )?(?P<c>[a-z][a-z' ]+?) "
                  r"(?P<k>pok[eé]mon|cards)\b", t, re.I)
    if m:
        nm = m.group("c").strip()
        # 「any number of **Basic Energy** cards」这类是**类别**而不是卡名：
        # 当成 `name_has:energy` 会变成「检索任意能量入手」，与原句语义不同
        # （实测把 Energy Search Pro 的「不同属性各 1 张」错落成 name_has）。
        if nm.lower() in ("pokemon", "pok\u00e9mon", "energy", "basic energy",
                          "trainer", "item", "tool", "stadium", "supporter",
                          "card", "cards") or "energy" in nm.lower():
            return None
        key = _tag_name_key(nm)
        if not key:
            return None
        return {"name_has": key, "basic": bool(m.group("b")),
                "kind": ("pokemon" if m.group("k").lower().startswith("pok")
                         else "trainer")}
    return None


def _search_destination(text: str, pos: int) -> str:
    """检索的**去向** → ``hand`` | ``bench`` | ``attach`` | ``evolve`` |
    ``deck_top`` | ``unknown``

    为什么必须判（2026-10-27 审计）：早期只判「检索什么类别」，不看「放到哪里」，
    于是卡面
      · 「search for a Basic Pokémon **and put it onto your Bench**」→ 映射成
        ``search_pokemon:1``（**入手**）——实际应是**直接铺场**；
      · 「search for up to 2 Basic Energy **and attach them**」→ ``search_energy:2``
        （入手）——实际是**附着**；
      · 「search for a card that **evolves from this Pokémon** … to evolve it」→
        ``search_pokemon:1``——实际是``evolve_from_deck``。
    引擎据此会「把牌拿进手牌」，少执行一步（铺场/附能/进化），局面上看不出来。
    判不出返回 ``unknown``（调用方登记未建模），绝不默认成入手。
    """
    seg = re.split(r"[.]", text[pos:], maxsplit=1)[0].lower()
    if re.search(r"to evolve (?:it|that pok[eé]mon)", seg):
        return "evolve"
    if re.search(r"\band attach\b", seg):
        return "attach"
    if re.search(r"onto (?:your|their) bench", seg):
        return "bench"
    if re.search(r"on top of (?:it|your deck|their deck)|on the bottom of", seg):
        return "deck_top"
    if re.search(r"into (?:your|their) hand|into the hand", seg):
        return "hand"
    return "unknown"


def _is_pre_damage(text: str, pos: int) -> bool:
    """该动作是否被「Before doing damage」限定在**伤害前阶段**

    判据取 pos 所在的**那一句**（与 `_is_delayed` 同口径，不跨句）。
    卡面原型：「Before doing damage, discard all Pokémon Tools from your
    opponent's Active Pokémon.」——弃道具必须发生在伤害结算之前，
    放到伤害之后会让守方道具先减伤一次再被弃（静默少做）。
    """
    sent = re.split(r"[.]", text[:pos])[-1].lower()
    return "before doing damage" in sent


def _is_delayed(text: str, pos: int) -> bool:
    """该动作是否被「回合末 / 下个回合」**延后**执行

    引擎没有延迟队列（效果一律即时结算），所以延后类效果必须整条拒绝，
    否则会把「回合末再弃」变成「现在立刻弃」——方向相反，且不可见。
    真实案例：Amarys「Draw 4 cards. At the end of this turn, if you have 5 or more
    cards in your hand, discard your hand.」立即执行会把刚抽的 4 张一起弃掉。

    判据取 **pos 所在句子**（回溯到最近句号），不看全文。
    """
    sent = re.split(r"[.]", text[:pos])[-1].lower()
    return bool(re.search(
        r"at the end of (?:this|your) turn|during your next turn", sent))


def _draw_cond_of(text: str, pos: int) -> Optional[str]:
    """抽牌子句的**前置条件** → 引擎条件名；无条件/不可精确表达返回 None

    只扫描**同一句**里抽牌动作之前的从句。卡面原型（Fezandipiti ex「Flip the
    Script」）：「if any of your Pokémon were Knocked Out during your opponent's
    last turn, you may draw 3 cards」——此前该条件被丢弃，变成**无条件抽 3**。
    """
    head = _norm_apostrophe(text[max(text.rfind(".", 0, pos), 0):pos]).lower()
    if re.search(r"knocked out during your opponent'?s last turn", head):
        return "own_ko_last_turn"
    return None


def _en_recover_combo(body: str) -> Optional[str]:
    """回收子句的**子集组合** → 引擎 combo 串；非组合/不可精确表达返回 None

    卡面原型（B3）：
      · 「Put up to 3 in any combination of Pokémon that don't have a Rule Box
         and Basic Energy cards from your discard pile into your hand.」（Lana's Aid）
      · 「Put up to 4 in any combination of {F} Pokémon and Basic {F} Energy
         cards … into your hand.」（Tarragon）
    既不是 `pokemon`、也不是 `energy`，更不能是 `any`（`any` 会连 ex 一起取）。
    """
    b = _norm_apostrophe(body or "").lower()
    if "any combination" not in b:
        return None
    has_poke = "pokémon" in b or "pokemon" in b
    has_energy = "energy" in b
    if not (has_poke and has_energy):
        return None
    kind = "no_rule_pokemon" if "rule box" in b else "pokemon"
    tail = f"combo:{kind}+basic_energy"
    m = re.search(r"\{([a-z])\}", b)
    if m:
        pt = _EN_ENERGY_LETTER.get(m.group(1).upper())
        if pt is None:                      # 未知属性符号 → 不猜
            return None
        tail += f":type:{pt}"
    return tail


def _search_per_counter(src: str) -> Optional[str]:
    """检索张数的**计数源**（「up to the number of your Benched Pokémon」）

    B2 剩余（2026-10-28）：Scrafty「Search your deck for a number of cards up to
    the number of your Benched Pokémon ...」——张数是「至多 N（后备数）」，
    引擎可用计数源精确求值；「for each」型同理。判不出返回 None。
    """
    low = _norm_apostrophe(src or "").lower()
    if re.search(r"(?:up to the number of|for each of) your opponent'?s? "
                 r"benched pok", low):
        return "opp_bench"
    if re.search(r"(?:up to the number of|for each of) your benched pok", low):
        return "own_bench"
    return None


def _energy_move_zone(clause: str, *, dest: bool) -> Optional[str]:
    """能量移动的**源/目标**区域 → 引擎 zone；判不出返回 None

    · 源：``active``（this Pokémon）· ``bench``（your Benched）·
      ``any``（1 of your Pokémon / 1 of your **other** Pokémon，需选源）·
      ``opp_active`` · ``opp_any``
    · 目标：``bench``（your Benched / the **new** Benched Pokémon）·
      ``active``（your Active / this Pokémon）· ``any``（another of your）·
      ``opp_bench``（their Benched）· ``opp_any``（another of their）

    第六十七批（2026-11-04）补齐三个真实卡面别名（此前一律判不出 →
    整条落「能量移动的源/目标未识别」）：Jynx「… to the new Benched
    Pokémon」、Azumarill ex「from 1 of your **other** Pokémon to **this**
    Pokémon」。⚠ 顺序不能乱：`their benched` 必须先于 `benched pok`，
    否则对手后备会被静默当成己方后备（阵营反转）。
    ⚠ 「any amount of … Energy」（Forretress / Latios）**故意不认**：
    引擎 `energy_move:N` 是固定/至多 N，把「任意量」压成 1 张是静默缩小
    范围；按 C5 保留未建模，等引擎支持 `:all` 形态再做。
    """
    c = _norm_apostrophe(clause or "").lower()
    if dest:
        if "their benched" in c:
            return "opp_bench"
        if "their pok" in c or "another of their" in c:
            return "opp_any"
        if "your benched" in c or "benched pok" in c:
            return "bench"
        if "your active" in c:
            return "active"
        if "another of your" in c:
            return "any"
        if "this pok" in c:
            return "active"
        return None
    if "this pok" in c:
        return "active"
    if "opponent's active" in c:
        return "opp_active"
    if "1 of your opponent" in c or "their pok" in c:
        return "opp_any"
    if "your benched" in c:
        return "bench"
    if "1 of your other pok" in c:
        return "any"
    if "1 of your pok" in c or "your pok" in c:
        return "any"
    return None


def _draw_extra_of(text: str, m) -> Optional[dict]:
    """抽牌 tag 的**完整性守卫** → ``extra``（可精确表达）或 ``None``（不可）

    ``None`` 的四种情形都是有真实卡面的（2026-10-27 审计）：
      · ``for each …``        按计数源抽（Awakening Drum / Morty's Conviction）
      · ``each player …`` / ``opponent draws``  对手也抽（Unfair Stamp / Harlequin）
      · ``draw N more``       条件追加（Billy & O'Nare / Emcee's Hype）
      · ``draw M instead``    分支替换（Lacey / Lillie's Determination）
    此前这些统统被压成固定 ``draw:N`` ——局面上正常、不报错，只是**少抽/多抽**，
    属静默错映射。判不出就返回 None，由调用方登记未建模（绝不静默近似）。
    """
    low = text.lower()
    seg = re.split(r"[.]", text[m.start():], maxsplit=1)[0]
    tail = low[m.end():]
    if "for each" in seg:
        return None
    if "each player" in seg or "opponent draws" in low:
        return None
    # **抽牌方阵营**（2026-10-27）：`draw:` 在引擎里恒指**自己**抽。若本句里
    # 抽牌动作之前的主语是「对手 / 双方」，放通就会变成「对手洗回手牌，我方抽 N」
    # —— 实测 Gothitelle「have your opponent shuffle their hand … and draw 3
    # cards」/ Vivillon「Your opponent shuffles … they draw 4 cards」两张。
    head = text[max(text.rfind(".", 0, m.start()), 0):m.start()]
    _before = text[:m.start()].lower()
    # 「they/their」是代词主语：全文里若提到 opponent，指的就是对手（Vivillon
    # 「Your opponent shuffles their hand … they draw 4 cards」）
    if re.search(r"\bopponent\b|\beach player\b|\btheir hand\b", head) \
            or (re.search(r"\bthey\b|\btheir\b", head) and "opponent" in _before):
        return None
    if "instead" in seg or re.search(r"draw\s+\d+\s+more", seg):
        return None
    # 同一段后面还有第二段抽牌（"Draw 2 cards. Then … draw 2 more cards."）
    # 注意数字与 "cards" 之间可夹修饰词（"2 more cards"），不能要求紧邻
    if re.search(r"draw\s+(?:up to\s+)?(?:\d+|a|1)\s+(?:\w+\s+){0,2}cards?", tail):
        if re.search(r"\bmore\b|instead", tail):
            return None
    return {}


def _heal_scope_of(clause: str) -> tuple:
    """回复的**范围与属性限定** → ``(scope, ptype)``；不可精确表达返回 ``(None, None)``

    为什么必须按子句判（2026-10-27 真实缺陷）：早期只要文本里有
    ``heal (\\d+) damage`` 就无条件产出 ``heal:N``，而引擎侧 ``heal:N`` 恒指
    **施术者自身**。于是卡面
      · 「回复**己方全部**」   → 只回了攻击方一只（Leafeon ex / Ethan's Ho-Oh ex /
        Leavanny / Erika's Vileplume ex / Fennel）
      · 「回复**后备 {P} 一只**」→ 回了自身、且不限属性（Meloetta / Jacinthe）
    这类错映射在局面上完全正常、也不报错，只能靠对账发现 —— 属最高优先级缺陷。
    判不出的形态（条件限定 / 依赖前序动作的后续）一律返回 None，由调用方登记未建模。

    scope 取值：``""``（this Pokémon）· ``active`` · ``choose`` · ``all`` ·
    ``bench`` · ``basic`` · ``bench_one``
    """
    low = clause.lower()
    # ① 依赖前序动作的后续（Super Potion「若回复了就弃那张的能量」）——只能做一半，
    #    宁可未建模
    if re.search(r"in this way|from that pok[eé]mon|that pok[eé]mon['\u2019]s", low):
        return None, None
    # ② 条件限定（Jumbo Ice Cream「有 3 个以上能量的战斗位」）——引擎无该条件。
    #    2026-10-28：把「N or less HP remaining」「exactly N damage counters」等
    #    同族子集限定一并拒绝 —— 否则 Bianca's Devotion 的「30 HP 以下」会被
    #    静默当成「任意一只」，回复范围被放大。
    if re.search(r"that has \d+ (?:or more|or less|hp)", low) \
            or re.search(r"that has exactly \d+", low) \
            or re.search(r"with \d+ (?:or more|or less|hp)", low):
        return None, None
    # ③ 卡名阵营条件（Arven's Sandwich「若是 Arven 的宝可梦则改为 100」）
    if re.search(r"if that pok[eé]mon is", low):
        return None, None
    # ③之二 Mega 限定（2026-10-28 第十二批）：「from 1 of your **Mega Evolution**
    #    Pokémon {ex}」不是「任意一只」——若按 `choose` 映射就静默放大了范围
    #    （任意一只都能回满）。只有 Wally's Compassion 的完整形态由专门分支认领，
    #    其余一律不建模。
    if re.search(r"mega evolution pok[eé]mon", low):
        return None, None

    ptype = None
    m = re.search(r"\{([a-z])\}", low)
    if m:
        ptype = _EN_ENERGY_LETTER.get(m.group(1).upper())
        if ptype is None:                    # 未知属性符号 → 不猜
            return None, None

    # ④ 全体形态：不得带属性符号（「己方每只 {X}」无此句式，出现即异常）
    if re.search(r"from each of your benched pok[eé]mon", low):
        return ("bench", None) if ptype is None else (None, None)
    # 「Heal all damage from your Benched Pokémon.」（Whimsicott「Healing Fluff」）
    # —— 复数后备全体，且**允许**属性限定（your Benched {G} Pokémon）
    if re.search(r"from your benched pok[eé]mon", low):
        return "bench", ptype
    if re.search(r"from each of your basic pok[eé]mon", low):
        return ("basic", None) if ptype is None else (None, None)
    if re.search(r"from each of your pok[eé]mon", low):
        return ("all", None) if ptype is None else (None, None)
    # ⑤ 单只形态
    if re.search(r"from 1 of your benched", low):
        return "bench_one", ptype
    if re.search(r"from 1 of your", low):
        return "choose", ptype
    if re.search(r"from your active", low):
        # 「your Active {X} Pokémon」属性符号在中间，不能用固定词序匹配
        return "active", ptype
    if re.search(r"from this pok[eé]mon", low):
        return "", ptype
    # 「This Pokémon is now Asleep. Heal 30 damage from it.」（Munna）——
    # 招式/特性里的 it 指**本只**；含「if that Pokémon is」的写法已在 ③ 提前拒绝
    # （Arven's Sandwich），故这里不会误伤。
    if re.search(r"from it\b", low):
        return "", ptype
    return None, None


_ACTION_POS_PAT = {
    "coin_flip": r"\bflip a coin\b",
    "discard": r"\bdiscard\b",
    "draw": r"\bdraw\b",
    "heal": r"\bheal\b",
    "search": r"\bsearch\b",
    "attach": r"\battach\b",
    "switch": r"\bswitch\b",
    "recover": r"\binto (?:your|their) hand\b",
    "move_damage": r"\bmove\b[^.]{0,30}damage",
    "evolve": r"\bevolv",
    "damage": r"\bdoes \d+ damage\b|\bdamage counter",
    "spread": r"\bdamage counter",
    "status": r"\bis now (?:asleep|burned|confused|paralyzed|poisoned)\b",
    "knockout": r"\bknocked out\b|\bknocks out\b",
    "shield": r"\btakes? \d+ less damage\b|\bprevent all\b",
    "damage_mod": r"\bdoes \d+ more damage\b",
    "attack_lock": r"\bcan.t use\b",
    "retreat_lock": r"\bcan.t retreat\b",
    "play_lock": r"\bcan.t play any\b",
}


def _order_actions_by_text(actions: list, text: str) -> list:
    """按卡面**词序**重排 actions（稳定排序；判不出位置的保持相对原序）

    为什么必需（2026-10-27 真实缺陷）：解析器是**按代码分支顺序**追加 action 的，
    与卡面词序无关。「Discard your hand and draw 5 cards.」（Carmine）原先产出
    ``[draw:5, discard:all]`` → 引擎先抽 5 张，再把**整手牌（含刚抽到的 5 张）**
    弃掉，效果从「弃光后重抽 5」变成「直接空手」。不报错、局面上也"跑得通"，
    只能靠对账或逐局复盘发现 —— 属静默错映射。

    判不出位置时给一个大值（排在后面），不改变同位置元素的相对顺序。
    """
    low = str(text or "").lower()
    if not low or len(actions) < 2:
        return actions
    big = len(low) + 9999
    keyed = []
    for i, a in enumerate(actions):
        pat = _ACTION_POS_PAT.get(str(a.get("type")))
        pos = None
        if pat:
            m = re.search(pat, low)
            if m:
                pos = m.start()
        keyed.append((pos if pos is not None else big, i, a))
    keyed.sort(key=lambda x: (x[0], x[1]))
    return [a for _, _, a in keyed]


# 下回合限制 / 静态规则（2026-10-28）：整句认领
_ATK_DENIAL_RE = re.compile(
    r"^during your opponent's next turn, if the defending pok[eé]mon tries to "
    r"use an attack, your opponent flips a coin\. if tails, that attack doesn't "
    r"happen\.$", re.I)
_COST_UP_RE = re.compile(
    r"^during your opponent's next turn, attacks used by the defending "
    r"pok[eé]mon cost \{c\} more, and its retreat cost is \{c\} more\.$", re.I)
_IGNORE_EFFECTS_RE = re.compile(
    r"^this attack's damage isn't affected by any effects on your opponent's "
    r"active pok[eé]mon\.$", re.I)

# 第六十四批（2026-11-04）：对手**自选**洗回牌库（Banette「Cursed Words」）
#   「Your opponent chooses 3 cards from their hand and shuffles those cards
#    into their deck.」→ `shuffle_opp_hand:3`
# 口径：卡面是「**对手**选」⇒ 引擎按随机选（与 `_shuffle_opp_hand` docstring
# 同源）；**不是** `opp_hand:<op>:<sel>`（那是我方选 ⇒ 确定性默认）。两者反口径。
_OPP_CHOOSE_SHUFFLE_RE = re.compile(
    r"^your opponent chooses (?P<n>\d+) cards? from their hand and shuffles "
    r"those cards into their deck\.$", re.I)

# 第六十五批（2026-11-04）：「无视弱点/抗性 + 无视守方效果」由
# `_detect_wr_bypass`（见其 docstring）统一认领为 `ignore_effects:wr`。
# 与 `ignore_effects`（只无视效果、弱抗照常）**反口径**：混用会静默漏掉
# 一半效果。这里**故意不留** WR 分支，避免同一句产出两条 tag。


def _static_cond(phrase):
    """规则族里的条件短语 → 引擎条件名

    ⚠ 必须补回 ``if `` 前缀：正则把 ``if `` 放在分组**外面**（`(?:if (.+?), )?`），
      而 `_condition_from_text` 的多数模式以 ``if `` 开头 —— 不补就会静默返回
      None，整条规则被当成「判不出」丢掉（Azumarill 实测）。
    """
    if not phrase:
        return None
    return _condition_from_text("if " + phrase.strip())


# ---------------- 第一百三十四批（2026-11-08）：六类「单句精确」规则 ----------------
# 这一批的共同点：卡面**整句**就是一个可精确求值的规则，且引擎已（或只需极小改动）
# 能执行。因此一律**整句精确匹配**（`^…$`）：任何附加从句都不认领 → 整卡如实拒收，
# 绝不把「多句」文本截成其中一句来进池（那正是静默丢规则的老路）。
#
# 1) 纯信息类「对手展示手牌」：本引擎是完美信息的，展示不改变任何状态。
#    标记成 `info:reveal_hand` 是**显式建模的等价**（引擎里有对应派发与日志），
#    不是「未建模当无事发生」——后者连 tag 都不产出，对账看不出来。
_INFO_REVEAL_RE = re.compile(
    r"^your opponent reveals? (?:his or her |their )?hand\.?$", re.I)

# 2) 「Double the number of damage counters on each of your opponent's Pokémon.」
#    （N's Vanilluxe「Snow Coating」）—— 指示物数翻倍，按 10 为一枚换算。
_COUNTERS_DOUBLE_RE = re.compile(
    r"^double the number of damage counters on each of your opponent'?s "
    r"pok[eé]mon\.?$", re.I)

# 3) 「During your opponent's next turn, Pokémon that have N or less Energy
#     attached can't use attacks. (This includes new Pokémon that come into
#     play.)」（Walrein「Frigid Fangs」）—— 判据是能量数，作用面是**整名玩家**。
_ATK_EN_BLOCK_RE = re.compile(
    r"^during your opponent'?s next turn, pok[eé]mon that have (\d+) or less "
    r"energy attached can'?t use attacks\.?\s*\(this includes new pok[eé]mon "
    r"that come into play\.?\)$", re.I)

# 4) 「If you use this attack when you have exactly N Prize cards remaining,
#     you win this game.」（N's Sigilyph「Victory Symbol」）
_WIN_IF_RE = re.compile(
    r"^if you use this attack when you have exactly (\d+) prize cards? "
    r"remaining, you win this game\.?$", re.I)

# 5) 条件式 +HP（第一百三十四批）：判据只依赖**本只身上**的附着卡，因此可以在
#    `FieldPokemon.max_hp` 里现算（见 cards.cond_hp_bonus），不需要刷新点。
#    「If this Pokémon has any Special Energy attached, it gets +N HP.」
#    （Tyrantrum「Tyrannically Gutsy」）
_HP_PLUS_SPECIAL_RE = re.compile(
    r"^if this pok[eé]mon has any special energy attached, it gets \+(\d+) "
    r"hp\.?$", re.I)

# 6) 训练家「看**牌库底** N 张取 1 张宝可梦」（Dusk Ball）
_RECON_BOTTOM_RE = re.compile(
    r"^look at the bottom (\d+) cards of your deck\. you may reveal a "
    r"pok[eé]mon you find there and put it into your hand\. shuffle the other "
    r"cards back into your deck\.?$", re.I)

# 7) 「Discard a Pokémon Tool and a Special Energy from 1 of your opponent's
#     Pokémon.」（Ruffian）—— 两个条件必须落在**同一只**身上。
_RUFFIAN_RE = re.compile(
    r"^discard a pok[eé]mon tool and a special energy from 1 of your "
    r"opponent'?s pok[eé]mon\.?$", re.I)

# 8) 道具「The <阵营>'s Pokémon this card is attached to gets +N HP.」
#    （Cynthia's Power Weight）—— 不判持有者就变成「谁挂上都加」。
_TOOL_HP_HOLDER_RE = re.compile(
    r"^the ([a-z][a-z' ]{1,24}?)'s pok[eé]mon this card is attached to gets "
    r"\+(\d+) hp\.?$", re.I)


def _detect_b134(text: str, kind: str) -> list:
    """第一百三十四批的八类单句规则（整句精确匹配，否则不产出）"""
    t = re.sub(r"\s+", " ", _norm_apostrophe(text or "")).strip()
    if kind == "attack":
        if _INFO_REVEAL_RE.match(t):
            return [act("rule", None, "any", {"info_reveal_hand": True})]
        if _COUNTERS_DOUBLE_RE.match(t):
            return [act("rule", None, "any", {"counters_double": True})]
        m = _ATK_EN_BLOCK_RE.match(t)
        if m:
            return [act("rule", None, "any",
                        {"atk_energy_block": int(m.group(1))})]
        m = _WIN_IF_RE.match(t)
        if m:
            return [act("rule", None, "any",
                        {"win_if_prizes": int(m.group(1))})]
        return []
    if kind == "ability":
        m = _HP_PLUS_SPECIAL_RE.match(t)
        if m:
            return [act("passive", int(m.group(1)), "self",
                        {"kind": "hp_plus", "cond": "special_energy"})]
        return []
    if kind in ("item", "supporter", "stadium", "tool"):
        m = _RECON_BOTTOM_RE.match(t)
        if m:
            return [act("rule", None, "any",
                        {"recon_bottom": int(m.group(1))})]
        if _RUFFIAN_RE.match(t):
            return [act("rule", None, "any", {"discard_tool_senergy": 1})]
        m = _TOOL_HP_HOLDER_RE.match(t)
        if m:
            return [act("tool", int(m.group(2)), "self",
                        {"kind": "hp_plus", "holder_name": _k(m.group(1))})]
        return []
    return []


# ---------------- 第一百三十五批（2026-11-08 · S1a）：决策分解 ----------------
# 「Before drawing cards, you may discard any number of cards from your hand.」
# 是**选择**而不是默认值：引擎在 `phase="choice"` 里等牌手逐张选（可随时结束），
# 选完才继续抽牌。落成 `choose:discard_hand:any:then:draw_to:<n>`。
#
# 前置条件（同一句括号里的）：「If you can't draw any cards in this way, you
# can't use this card.」等价于「打出时手牌数 < n」—— 因为效果是「抽到 n 张」，
# 手牌已 ≥ n 时一张都抽不到 → 本卡不可使用。落成 `play_gate`
# `own_hand_at_most:<n-1>`（**不是**代价，满足时什么都不发生）。
_CHOOSE_NAVEEN_RE = re.compile(
    r"^draw cards until you have (\d+) cards? in your hand\. before drawing "
    r"cards, you may discard any number of cards from your hand\. "
    r"\(if you can't draw any cards in this way, you can't use this card\.\)$",
    re.I)


# 「You may discard 2 {L} Energy from this Pokémon and make your opponent's
# Active Pokémon Paralyzed.」（Mega Eelektross ex「Disaster Shock」）
# 这是**可选代价**：付不付由牌手决定 → `choose:optional_energy_cost:2:lightning
# :then:paralyze`（引擎挂起 confirm 选择）。绝不默认「付」（多弃能量）或
# 「不付」（少一个麻痹）—— 两种默认都会实质改结果。
_OPT_ENERGY_RE = re.compile(
    r"^you may discard (\d+) \{([a-z])\} energy from this pok[eé]mon and make "
    r"your opponent'?s active pok[eé]mon (paralyzed|confused|asleep)\.$", re.I)


def _detect_b135(text: str, kind: str) -> list:
    """第一百三十五批：带「任意数量」选择的卡（整句精确匹配）

    返回动作列表；`play_gate` 由调用方（`_parse_rule_en`）写进 spec ——
    这里通过动作的 `extra["play_gate"]` 携带，避免与其它族抢 spec 字段。
    """
    t = re.sub(r"\s+", " ", _norm_apostrophe(text or "")).strip()
    if kind == "attack":
        m = _OPT_ENERGY_RE.match(t)
        if m:
            _ty = _EN_SYMBOL_COLOR.get(m.group(2).upper())
            if _ty:
                # 卡面是过去分词（paralyzed/confused/asleep），引擎状态词是
                # 动词原形（paralyze/confuse/sleep）—— 不归一化会让状态**加不进去**
                # （`status.add("paralyzed")` 与 `_NO_ATTACK_STATUS` 对不上）。
                _st = {"paralyzed": "paralyze", "confused": "confuse",
                       "asleep": "sleep"}.get(m.group(3).lower(),
                                              m.group(3).lower())
                return [act("rule", None, "any", {"optional_energy_cost": (
                    int(m.group(1)), _ty, _st)})]
        return []
    if kind != "supporter":
        return []
    m = _CHOOSE_NAVEEN_RE.match(t)
    if m:
        n = int(m.group(1))
        return [act("rule", None, "any", {
            "choose_discard_then_draw_to": n,
            "play_gate": f"own_hand_at_most:{max(0, n - 1)}"})]
    return []


def _detect_static_rules(text: str, kind: str) -> list:
    """招式侧的「下回合限制 / 无视效果」（2026-10-28）

    · 「… if the Defending Pokémon tries to use an attack, your opponent flips a
      coin. If tails, that attack doesn't happen.」→ ``attack_denial:coin:turn``
    · 「… attacks used by the Defending Pokémon cost {C} more, and its Retreat
      Cost is {C} more.」→ ``attack_cost_up`` + ``retreat_cost_up``
    · 「This attack's damage isn't affected by any effects on your opponent's
      Active Pokémon.」→ ``ignore_effects``
    · 「Your opponent chooses N cards from their hand and shuffles those
      cards into their deck.」→ ``shuffle_opp_hand:N``（第六十四批，对手自选）
    · 「During your next turn, this Pokémon can't attack.」
      → ``attack_lock:all:turn``（第六十三批，由 `_ATTACK_LOCK_ALL_RE` 处理）

    只认**整句**；近似/多句写法一律不认（宁可未建模）。
    """
    if kind != "attack":
        return []
    t = re.sub(r"\s+", " ", _norm_apostrophe(text or "")).strip()
    if _ATK_DENIAL_RE.match(t):
        return [act("rule", None, "any", {"attack_denial": "coin"})]
    if _COST_UP_RE.match(t):
        return [act("rule", None, "any", {"attack_cost_up": 1,
                                          "retreat_cost_up": 1})]
    if _IGNORE_EFFECTS_RE.match(t):
        return [act("rule", None, "any", {"ignore_effects": True})]
    # 含 Weakness/Resistance 的宽口径句式**不在这里**认领（交给
    # `_detect_wr_bypass`，它是 search 级、能命中整段中的第二句）。
    m = _OPP_CHOOSE_SHUFFLE_RE.match(t)
    if m:
        return [act("rule", None, "any",
                    {"shuffle_opp_hand": int(m.group("n"))})]
    return []


# 招式失败门（2026-10-28）：「If <条件>, this attack does nothing.」
# 引擎 `nullify_if:<cond>`；`not:<cond>` 表示卡面写的是**不满足**才失败。
# 只收**条件可精确求值**的形态（与 `_CONDITION_PARAMS` 同一白名单）。
_GATE_PATTERNS = (
    (re.compile(r"if you don't have ([a-z][a-z' ]{2,20}?) and "
                r"([a-z][a-z' ]{2,20}?) on your bench", re.I),
     lambda m: "not:own_bench_named_all:{}+{}".format(
         _k(m.group(1)), _k(m.group(2)))),
    (re.compile(r"if there is no stadium in play", re.I),
     lambda m: "not:stadium_in_play"),
    (re.compile(r"if you don't have exactly (\d+) cards? in your hand", re.I),
     lambda m: f"not:own_hand_exactly:{int(m.group(1))}"),
    (re.compile(r"if you don't have the same number of cards in your hand as "
                r"your opponent", re.I),
     lambda m: "not:own_hand_eq_opp_hand"),
    (re.compile(r"if your opponent's active pok[eé]mon has no damage counters? "
                r"on it before this attack does damage", re.I),
     lambda m: "not:opp_active_has_damage"),
    (re.compile(r"if your opponent's active pok[eé]mon isn't a pok[eé]mon \{ex\}",
                re.I),
     lambda m: "not:opp_active_is_ex"),
    (re.compile(r"if your opponent's active pok[eé]mon isn't burned", re.I),
     lambda m: "not:opp_active_status:burn"),
    (re.compile(r"if you don't have ([a-z][a-z' ]{2,24}?) on your bench", re.I),
     lambda m: f"not:own_bench_name:{_k(m.group(1))}"),
    (re.compile(r"if you have (\d+) or fewer benched pok[eé]mon", re.I),
     lambda m: f"own_bench_at_most:{int(m.group(1))}"),
    (re.compile(r"if your opponent doesn't have exactly (\d+) or (\d+) prize "
                r"cards? remaining", re.I),
     lambda m: f"not:opp_prizes_in:{int(m.group(1))},{int(m.group(2))}"),
    (re.compile(r"if you don't have (\d+) or more basic \{([a-z])\} energy "
                r"cards? in your discard pile", re.I),
     lambda m: (f"not:own_discard_basic_energy_at_least:"
                f"{_EN_SYMBOL_COLOR.get(m.group(2).upper())}:{int(m.group(1))}"
                if _EN_SYMBOL_COLOR.get(m.group(2).upper()) else "")),
)


def _k(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


def _detect_nullify(text: str) -> list:
    """「… , this attack does nothing.」→ ``nullify_if:<cond>`` 动作

    只认**条件在引擎白名单内**的形态；条件不可求值时**不产出**（整条如实未建模），
    绝不退化成「无条件下伤害照打」。
    """
    t = re.sub(r"\s+", " ", _norm_apostrophe(text or "")).strip()
    if "this attack does nothing" not in t.lower():
        return []
    out = []
    spans: list = []
    for rx, fmt in _GATE_PATTERNS:
        m = rx.search(t)
        if not m:
            continue
        # 同一段文本只认**最具体**的一条：Mesprit「Uxie and Azelf」既命中合取式，
        # 也命中单名式 → 若都产出，会多出一个把 "Uxie and Azelf" 当卡名的错误条件
        if any(s <= m.start() and m.end() <= e for s, e in spans):
            continue
        cond = fmt(m)
        if cond:
            spans.append((m.start(), m.end()))
            out.append(act("rule", None, "any", {"nullify_if": cond}))
    return out


_BORROW_PATTERNS = (
    (re.compile(r"^choose 1 of your opponent's active tera pok[eé]mon's "
                r"attacks? and use it as this attack\.?$", re.I),
     "opp_active_tera"),
    (re.compile(r"^choose 1 of your opponent's active pok[eé]mon's attacks? "
                r"and use it as this attack\.?$", re.I), "opp_active"),
    (re.compile(r"^choose 1 of your benched n's pok[eé]mon's attacks? and use "
                r"it as this attack\.?$", re.I), "bench_ally_n"),
)

# Relicanth「Memory Dive」：进化前形态的招式都能用（能量照付）
_PREV_ATTACKS_RE = re.compile(
    r"^each of your evolved pok[eé]mon can use any attack from its previous "
    r"evolutions\. \(you still need the necessary energy to use each attack\.\)$",
    re.I)


def _detect_borrow(text: str, kind: str) -> list:
    """借用招式 / 进化前招式权限（2026-10-28）

    · 招式侧「Choose 1 of …'s attacks and use it as this attack.」→
      ``borrow_attack:<scope>``（scope 决定可借用的来源）；
    · 特性侧 Relicanth「… can use any attack from its previous Evolutions」→
      ``passive:prev_attacks``。

    仅**整句**匹配（招式效果文本通常就是这一句）；多句/近似写法一律不认。
    """
    t = re.sub(r"\s+", " ", _norm_apostrophe(text or "")).strip()
    if kind == "ability":
        if _PREV_ATTACKS_RE.match(t):
            return [act("passive", None, "self", {"kind": "prev_attacks"})]
        return []
    if kind != "attack":
        return []
    for rx, scope in _BORROW_PATTERNS:
        if rx.match(t):
            return [act("rule", None, "any", {"borrow": scope})]
    return []


# ---------------- 训练家「本回合多拿奖赏」（2026-11-05 第八十九批） ----------------
# 「During this turn, if your opponent's Active Pokémon is Knocked Out by damage
#  from an attack used by your Tera Pokémon, take N more Prize card.」（Briar
#  「白蕾」）。Anthea & Concordia 同形但限定「your N's Pokémon」，且带「6 张具名
#  宝可梦在场」的前置条件（gate 未建模，B4）→ 只映射效果侧，gate 侧照旧拒收。
_TRAINER_PRIZE_BONUS_RE = re.compile(
    r"during this turn, if your opponent'?s active pok[eé]mon is knocked out by "
    r"damage from an attack used by your (tera|n'?s) pok[eé]mon, take (\d+) "
    r"more prize card", re.I)


def _detect_prize_bonus(text: str, kind: str) -> list:
    """训练家卡级：本回合对手战斗位被 (Tera / N's) 招式击倒 → 多拿 N 张奖赏

    → ``prize_bonus:<n>:<tera|nteam>``（训练家卡级 tag，引擎按**本回合旗标**
    在 `_resolve_ko` 结算奖赏时消费）。
    """
    if kind not in ("supporter", "item", "stadium", "tool"):
        return []
    t = _norm_apostrophe(text or "")
    # ⚠ 前置条件守卫：`_split_play_gate` 已把**能识别**的 gate 剥走；若这里仍残留
    # 「You can use this card only if …」，说明是**未建模的前置条件**（Anthea &
    # Concordia「6 张具名宝可梦在场」，B4）。此时**绝不认领** —— 一旦 early return
    # 就会把这句未建模的 gate 静默丢掉，卡在没有前置条件下也能打出（+3 奖赏的
    # 静默 buff）。整卡如实未建模。
    if re.search(r"can use this card only if", t, re.I):
        return []
    m = _TRAINER_PRIZE_BONUS_RE.search(t)
    if not m:
        return []
    scope = "tera" if m.group(1).lower() == "tera" else "nteam"
    return [act("rule", None, "any",
                {"prize_bonus": int(m.group(2)), "scope": scope})]


def _parse_rule_en(text: str, kind: str = "attack", name: str = "") -> dict:
    """规则后端（英文卡面）：抽取 spec（伤害/指示物/抽弃/检索/回复/附能/状态/
    互换/击倒/静态规则 + 触发时机）"""
    t_raw = str(text or "")
    # 「受击触发」整句屏蔽（等长空格，位置守恒）：见 `_reactive_damage_spans`。
    # 屏蔽后正文扫描既不会把反应效果读成主动效果，也不会改变其余从句的相对位置。
    _re_spans = _reactive_damage_spans(t_raw)
    t = _mask_spans(t_raw, _re_spans)
    actions: list = []
    conditions: list = []
    lower = t.lower()
    # 「自伤指示物 + 本回合自身加伤」复合特性（第九十五批，Feraligatr）：
    # 必须在 `_claimed_ability` **之前**认领 —— 否则它会把两半拆成
    # 「放置指示物（this Pokémon）」+「静态伤害增减」两条未建模，整卡被拒。
    _sb = _detect_self_buff(t_raw, kind)
    if _sb:
        return {"kind": kind, "name": name, "trigger": "on_use",
                "conditions": conditions, "actions": _sb,
                "source_text": str(text or ""), "citations": []}
    # 状态免疫（第一百一十一批，Hoothoot「Insomnia」）：「This Pokémon can't be
    # Asleep.」—— 常驻被动。必须在这里认领：通用状态分支看到 "asleep" 会先判
    # 「非主动施加」并登记未建模，整卡被拒。
    _si = _detect_status_immune(t_raw, kind)
    if _si:
        return {"kind": kind, "name": name, "trigger": "static",
                "conditions": conditions, "actions": _si,
                "source_text": str(text or ""), "citations": []}
    # 带筛选的换位 + 换入后状态（第一百一十一批，Pecharunt ex）。
    # 同样必须前置：通用分支会把 "switch 1 of your Benched …" 认成 `switch_self`
    # （语义是「**持有者自己**从后备换上来」），并把 "is now Poisoned" 当非主动
    # 施加拒收 → 两个错叠在一起。
    _swc = _detect_switch_choose(t_raw, kind)
    if _swc:
        return {"kind": kind, "name": name, "trigger": "static",
                "conditions": conditions, "actions": _swc,
                "source_text": str(text or ""), "citations": []}
    # 附能 + 回复**同一目标**（第一百零四批，Hydrapple ex「Ripening Charge」）。
    _ah = _detect_attach_heal(t_raw, kind)
    if _ah:
        return {"kind": kind, "name": name, "trigger": "static",
                "conditions": conditions, "actions": _ah,
                "source_text": str(text or ""), "citations": []}
    # 附能触发回复（第一百零四批，Magearna「Auto Heal」）。
    _ho = _detect_heal_on_attach(t_raw, kind)
    if _ho:
        return {"kind": kind, "name": name, "trigger": "static",
                "conditions": conditions, "actions": _ho,
                "source_text": str(text or ""), "citations": []}
    # 规则型文本（费用改写 / 禁攻 / 当回合进化，第一百一十二批起）。
    # ⚠ 必须与既有的 `_detect_static_rules`（下回合限制 / 无视效果）**分开命名**：    #   第一版重名把那个探测器整个覆盖掉，14 张已进池的卡当场掉出（Shred /
    #   Drum Beating / Sand Attack …），且测试全绿 —— 只是池子少了 14 张。
    # 第一百三十四批：八类「整句精确」规则（纯信息 / 指示物翻倍 / 能量禁攻 /
    # 奖赏胜利 / 牌库底取牌 / 双弃同目标 / 条件 +HP / 道具阵营 +HP）。
    # 位置在 `_detect_value_rules` 之前：这些文本里出现的 "attacks"、"energy"
    # 等词会被后面的宽口径规则族误读成「静态伤害增减」或「费用改写」。
    _b134 = _detect_b134(t_raw, kind)
    if _b134:
        return {"kind": kind, "name": name, "trigger": "on_use",
                "conditions": conditions, "actions": _b134,
                "source_text": str(text or ""), "citations": []}
    # 第一百三十五批（S1a）：带「任意数量」选择的卡（Naveen）—— 挂起选择。
    _b135 = _detect_b135(t_raw, kind)
    if _b135:
        _pg = str((_b135[0].get("extra") or {}).get("play_gate") or "")
        return {"kind": kind, "name": name, "trigger": "on_use",
                "conditions": conditions, "actions": _b135,
                "play_gate": _pg,
                "source_text": str(text or ""), "citations": []}
    _vr = _detect_value_rules(t_raw, kind)
    if _vr:
        return {"kind": kind, "name": name, "trigger": "on_use",
                "conditions": conditions, "actions": _vr,
                "source_text": str(text or ""), "citations": []}
    _sr = _detect_cost_rules(t_raw, kind)
    if _sr:
        return {"kind": kind, "name": name, "trigger": "static",
                "conditions": conditions, "actions": _sr,
                "source_text": str(text or ""), "citations": []}
    # 弃场地 + 封场地（第一百零七批，Chi-Yu「Scorching Earth」）。
    # 必须整段认领：两条动作要**按卡面顺序**执行，且「封锁」依赖前一步真的
    # 弃掉了场地（`:if_prev`）。零散追加会排错序（实测把锁排到了弃置之前）。
    _ss = _detect_stadium_smash(t_raw, kind)
    if _ss:
        return {"kind": kind, "name": name, "trigger": "on_use",
                "conditions": conditions, "actions": _ss,
                "source_text": str(text or ""), "citations": []}
    # 特性代价 + 具名在场条件 + 抽牌（第一百一十一批，Lunatone「Lunar Cycle」）。
    _sol = _detect_solrock_draw(t_raw, kind)
    if _sol:
        return {"kind": kind, "name": name, "trigger": "static",
                "conditions": conditions, "actions": _sol[0],
                "ability_cost": _sol[1],
                "source_text": str(text or ""), "citations": []}
    # 被击倒触发 / 进化触发 +「in this way」后续（2026-10-28 第十批）：
    # 这类文本含**触发从句 + 条件后续**，通用分支会把后续当成无条件动作 →
    # 必须在最前面整段认领（认不出则不产出，整卡如实未建模）。
    _claimed = _claimed_ability(t, kind)
    if _claimed:
        trigger = ("on_knockout" if _detect_ko_trigger(t, kind)
                   else "on_evolve")
        return {"kind": kind, "name": name, "trigger": trigger,
                "conditions": conditions, "actions": _claimed,
                "source_text": str(text or ""), "citations": []}
    # Pokémon Checkup 追加伤害（2026-10-28 第十三批）：含「Poisoned/Burned」状态词，
    # 通用状态分支会把它当「非主动施加」拒绝 → 整段认领。
    _ckp = _detect_checkup_plus(t, kind)
    if _ckp:
        return {"kind": kind, "name": name, "trigger": "static",
                "conditions": conditions, "actions": _ckp,
                "source_text": str(text or ""), "citations": []}
    # Checkup 群体放置（2026-10-28 第十四批）：Froslass / Tyranitar
    _cks = _detect_checkup_spread(t, kind)
    if _cks:
        return {"kind": kind, "name": name, "trigger": "static",
                "conditions": conditions, "actions": _cks,
                "source_text": str(text or ""), "citations": []}
    # 跨回合伤害修正（2026-10-29 第十六批）：三句式可共存，逐句 search；
    # 认领后必须把句子从文本里去掉，否则会被通用伤害缩放分支重复解析。
    _ntd, _t_rest = _detect_next_turn_dmg(t, kind)
    if _ntd:
        actions += _ntd
        t = _t_rest
    # 逐后备进化（2026-10-28 第十二批）：含「for each」的检索+进化整句，
    # 通用分支会双路拒绝（检索数量由计数源决定 / 招式驱动的进化需卡级 tag）。
    _each_evo = _detect_evolve_each_bench(t, kind)
    if _each_evo:
        return {"kind": kind, "name": name, "trigger": "on_use",
                "conditions": conditions, "actions": _each_evo,
                "source_text": str(text or ""), "citations": []}
    # 「选**至多 N 只**，各进化一次」（2026-11-02 第四十批）：与上一分支同源，
    # 但目标集合是**选出**的 N 只（不是全部后备）→ 必须是独立 tag。
    _pick_evo = _detect_evolve_each_pick(t, kind)
    if _pick_evo:
        return {"kind": kind, "name": name, "trigger": "on_use",
                "conditions": conditions, "actions": _pick_evo,
                "source_text": str(text or ""), "citations": []}
    # 对手手牌弃置（2026-11-04 第五十四批）：已在引擎里有精确 tag，只缺解析。
    _od = _detect_opp_discard(t, kind)
    if _od:
        return {"kind": kind, "name": name, "trigger": "on_use",
                "conditions": conditions, "actions": _od,
                "source_text": str(text or ""), "citations": []}
    # 条件抽牌（2026-11-04 第五十八批）：必须排在 `_detect_trainer_compound` 之前
    # —— 那边只认「弃全部再抽 N」，与本族句式不重叠，但本族条件短语里可能含逗号，
    # 交给通用分支会被拆成两条。
    _dc = _detect_draw_cond(t)
    if _dc:
        return {"kind": kind, "name": name, "trigger": "on_use",
                "conditions": conditions, "actions": _dc,
                "source_text": str(text or ""), "citations": []}
    # 「双方洗回手牌，我方 N / 对手 M」（第五十九批，Unfair Stamp）
    _ss = _detect_shuffle_split_draw(t)
    if _ss:
        return {"kind": kind, "name": name, "trigger": "on_use",
                "conditions": conditions, "actions": _ss,
                "source_text": str(text or ""), "citations": []}
    # 「对手某只身上的能量回其手牌」（第五十九批，Chill Teaser Toy）
    _oe = _detect_opp_energy_to_hand(t)
    if _oe:
        return {"kind": kind, "name": name, "trigger": "on_use",
                "conditions": conditions, "actions": _oe,
                "source_text": str(text or ""), "citations": []}
    # 双方各洗牌抽 N（第六十八批，Judge / Professor Juniper）
    _epsd = _detect_each_player_shuffle_draw(t)
    if _epsd:
        return {"kind": kind, "name": name, "trigger": "on_use",
                "conditions": conditions, "actions": _epsd,
                "source_text": str(text or ""), "citations": []}
    # 弃牌堆基本能量洗回牌库（第七十批，Energy Recycler / Energy Retrieval）
    _er = _detect_energy_recycle(t)
    if _er:
        return {"kind": kind, "name": name, "trigger": "on_use",
                "conditions": conditions, "actions": _er,
                "source_text": str(text or ""), "citations": []}
    # 「本回合全队增伤」（第六十批，Premium Power Pro / Black Belt's Training）
    _tbt = _detect_team_boost(t)
    if _tbt:
        return {"kind": kind, "name": name, "trigger": "on_use",
                "conditions": conditions, "actions": _tbt,
                "source_text": str(text or ""), "citations": []}
    # 「场地在场时可攻击两次」（第六十一批，Festival Lead）
    _ea = _detect_extra_attack(t)
    if _ea:
        return {"kind": kind, "name": name, "trigger": "static",
                "conditions": conditions, "actions": _ea,
                "source_text": str(text or ""), "citations": []}
    # 条件回复（第六十二批，Jumbo Ice Cream / Arven's Sandwich）
    _hc = _detect_heal_cond(t)
    if _hc:
        return {"kind": kind, "name": name, "trigger": "on_use",
                "conditions": conditions, "actions": _hc,
                "source_text": str(text or ""), "citations": []}
    # 训练家复合句（2026-11-04 第五十一批）：整句认领成**一条**复合 tag。
    _tc = _detect_trainer_compound(t)
    if _tc:
        return {"kind": kind, "name": name, "trigger": "on_use",
                "conditions": conditions, "actions": _tc,
                "source_text": str(text or ""), "citations": []}
    # 「对手少拿奖赏卡」（2026-11-04 第五十批）：带条件的奖赏修正，整段认领。
    # 通用分支会把它读成「静态规则」占位（该族此前 4 张全部未建模）。
    _kpm = _detect_ko_prize_minus(t, kind)
    if _kpm:
        return {"kind": kind, "name": name, "trigger": "static",
                "conditions": conditions, "actions": _kpm,
                "source_text": str(text or ""), "citations": []}
    # 训练家「本回合多拿奖赏」（2026-11-05 第八十九批）：Briar / Anthea & Concordia
    _pb = _detect_prize_bonus(t, kind)
    if _pb:
        return {"kind": kind, "name": name, "trigger": "on_use",
                "conditions": conditions, "actions": _pb,
                "source_text": str(text or ""), "citations": []}
    # 「Flip a coin until you get tails」+ **非伤害**子效果（2026-11-02 第四十批）：
    # 张数结算时才知道，通用 mill / 检索分支会把它们折成常数（Tyrantrum 被折成
    # `mill:1:opp`）或直接不产出（Houndstone / Gholdengo 静默丢卡）。
    _ut = _detect_until_tails_effect(t)
    if _ut:
        return {"kind": kind, "name": name, "trigger": "on_use",
                "conditions": conditions, "actions": _ut,
                "source_text": str(text or ""), "citations": []}
    # ⚠ 未被认领的「flip a coin until you get tails」**不得**落进通用分支：
    #   张数（= 正面数）在结算时才知道，通用 mill / 检索会把它们折成常数 ——
    #   实测「flip until **2** tails … discard the top card」落到 `mill:1:opp`
    #   （恒定磨 1 张），既不报错也没进 unsupported，属**静默削弱**。
    #   本族只认 `_detect_until_tails_effect` 的三种精确形态，其余如实退回。
    if re.match(r"flip a coin until you get", t, re.I) \
            and not _detect_attach_flip_until(t) \
            and not _COIN_UNTIL_TAILS_RE.search(t):
        return {"kind": kind, "name": name, "trigger": "on_use",
                "conditions": conditions,
                "actions": [act("rule", None, "any", {
                    "note": "「Flip a coin until you get tails」的子效果未建模"
                            f"（{t[:60]}）"})],
                "source_text": str(text or ""), "citations": []}
    # 借用招式 / 进化前招式权限（2026-10-28）：整句认领，先于其它子句扫描
    actions += _detect_borrow(t, kind)
    # 下回合限制 / 无视效果（2026-10-28）
    actions += _detect_static_rules(t, kind)
    # 「Shuffle all Energy attached to this Pokémon into your deck, and this
    # attack does …」（2026-11-03 第四十一批 · Mega Skarmory ex「Sonic Ripper」）
    # ——**强制代价**：自身附着的全部能量（基本 + 特殊整张）洗回牌库。
    # ⚠ 只认同句带「, and this attack does」的**强制**形态：Dialga MEG 95
    #   「**You may** shuffle all Energy … **and have this attack do 80 more
    #   damage**」是**可选代价 + 加伤**（需交互层），落成固定代价会静默削掉 +80。
    if kind == "attack" and re.search(
            r"shuffle all energy attached to this pok[eé]mon into your deck, "
            r"and this attack does", t, re.I):
        actions.append(act("rule", None, "any", {"energy_self_to_deck": True}))
    elif kind == "attack" and re.search(
            r"you may shuffle all energy attached to this pok[eé]mon into your "
            r"deck[^.]*?more damage", t, re.I):
        actions.append(act("rule", None, "any", {
            "note": "「You may shuffle all Energy … and have this attack do N more "
                    "damage」是**可选代价 + 加伤**（Dialga MEG 95），需交互层（B4）"}))
    # 「Flip a coin. If heads, choose 1 of your opponent's [Benched] Pokémon.
    # Shuffle that Pokémon and all attached cards into their deck.」
    # （2026-11-03 第四十二批 · Sylveon / Shiftry）——把对手的宝可梦**连同附着卡**
    # 洗回其牌库（不是击倒、不取奖赏）。⚠ 必须整句精确匹配：只挑 1 只、且「all
    # attached cards」随行走；少一句（如「shuffle **it**」）语义就不同，不得认领。
    _sop = _detect_shuffle_opp_pokemon(t)
    if _sop:
        actions.append(act("rule", None, "any", _sop))
    # 招式失败门（2026-10-28）：「If …, this attack does nothing.」
    if kind == "attack":
        actions += _detect_nullify(t)

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
        # 「条件性附加伤害」判定（2026-10-29 第十七批扩展）：
        #   · 句首条件（If …）→ 若能精确表达则落 `:if:<cond>`（Ting-Lu / Wo-Chien）；
        #   · 子句/句尾缩放（for each …）→ 若能精确表达则落 `:per:<counter>`
        #     （Kyurem ex / Raging Bolt / Genesect）；
        #   · 两者都认不出 → 仍如实登记未建模（宁缺勿错）。
        head = re.split(r"[.!]", t[:md.start()])[-1]
        tail = t[md.end():md.end() + 70]
        # 「Flip N coins. If both of them are tails, this Pokémon also does N
        # damage to itself.」（Team Rocket's Raticate）——已由 `_detect_coin` 的
        # coin_all 分支承载；此处必须跳过，否则会被当成「条件不可表达」再登记一次
        # 未建模 → 整卡被踢出卡池（与第十六批的重复登记问题同源）。
        if re.search(r"if both of them are (heads|tails),", head, re.I) \
                and re.search(r"\bitself\b", clause, re.I):
            continue
        cond = None
        if re.match(r"\s*(if|during|once|when|whenever|as long as|while)\b",
                    head, re.I):
            cond = _condition_from_text(head)
        # 缩放短语：优先子句内「for each …」，其次紧跟的句尾
        core, has_scale, scale_counter = _split_snipe_scale(clause, tail)
        sp = _en_snipe_spec(core)
        if sp is not None and sp[0] == "self" and has_scale \
                and scale_counter is None:
            # 「does N damage to itself for each damage counter on it」——「it」
            # 只能指**这只出招宝可梦** → 自身战斗位的指示物数（Palafin）
            scale_counter = "own_active_counters"
        unresolved_cond = bool(
            re.match(r"\s*(if|during|once|when|whenever|as long as|while)\b",
                     head, re.I) and not cond)
        unresolved_scale = bool(has_scale and scale_counter is None)
        if sp is None or unresolved_cond or unresolved_scale:
            # 引擎无法精确表达（子集限定 / 条件认不出 / 缩放源认不出 / 动态目标）
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
            actions.append(act("damage", amt, "self",
                               {"recoil": True,
                                "per_counter": scale_counter}))
        else:
            # 选定 / 全体目标 → 引擎 `snipe:<scope>:<per>:<count>[:per:…][:target:damaged|ex][:if:…]`
            actions.append(act("damage", amt, "active_opponent_bench",
                               {"snipe_scope": sp[0], "snipe_count": sp[1],
                                "snipe_per": scale_counter,
                                "snipe_target": "damaged" if sp[2] else None,
                                "snipe_ex": bool(sp[3]),
                                "snipe_if": cond}))

    # 伤害指示物**直放**对手战斗位（2026-10-28 第十四批）：Haunter / Alakazam。
    # 指示物不吃弱点/抗性 → 不能走 `damage` 字段，也不走通用 spread（它的目标
    # 口径是后备）；由 `counters:` tag 精确表达。
    _ctr = _detect_counters_on_active(t, kind)
    if _ctr:
        actions.extend(_ctr)
    m = _EN_SPREAD_RE.search(t)
    if m and not _ctr:
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
            if _CHECKUP_SET_RE.search(t):
                # Pokémon Checkup 改写（Nidoking ex）：只产出 `checkup_set:…`，
                # 不登记「指示物目标未识别」（本就是同一句）。
                actions.extend(_detect_checkup_set(t, kind))
            elif re.search(r"if you attached energy to a pok[eé]mon in this "
                           r"way", t, re.I):
                # Toxtricity：「If you attached Energy …, place 2 damage counters
                # on that Pokémon」——由 `attach:…:counters:N` 承载，不单独登记。
                pass
            else:
                actions.append(act("rule", None, "any",
                                   {"note": "放置伤害指示物：目标/条件未识别"
                                            f"（{(clause or t[:40]).strip()[:40]}）"}))
        elif sp[0] == "self":
            # 「put N damage counters on this Pokémon」= 自身受 N×10 点伤害
            actions.append(act("damage", n * 10, "self", {"recoil": True}))
        elif sp[0] == "opp_active":
            # 「Place N damage counters on your opponent's Active Pokémon」
            # ⚠ **不能**映射成 `damage`：指示物不受弱点/抗性影响，招式的
            # damage 字段会（Haunter 会被弱点翻倍，属静默错算）→ 保持未建模。
            actions.append(act("rule", None, "any",
                               {"note": "放置伤害指示物：仅对手战斗位，"
                                        "指示物不吃弱点/抗性，需独立机制（B4）"}))
        else:
            # ⚠ 指示物族没有 `:target:ex` 能力（引擎 `counters:` 不支持子集筛选）→
            #   带「{ex}」限定的指示物放置如实退回，不得悄悄放宽成全体。
            if sp[3]:
                actions.append(act("rule", None, "any",
                                   {"note": "放置伤害指示物：目标是 {ex} 子集，"
                                            "引擎 counters 不支持筛选（B4）"}))
            else:
                actions.append(act("damage", n * 10, "active_opponent_bench",
                                   {"snipe_scope": sp[0], "snipe_count": sp[1],
                                    "snipe_target": "damaged" if sp[2] else None}))

    # 「Then, shuffle those Energy cards into your deck」（2026-10-29 第二十八批）
    # Kyogre「Riptide」/ Sinistcha ex「Re-Brew」——把刚被计数的**弃牌堆基本能量**
    # 洗回牌库。⚠ 必须**先**被计数（`dmg_set:…:per:own_discard_energy_*` /
    # `counters:…:per:…`），再洗回；只建模计数不洗回 = 下次再打会重复计数。
    # 只认「计数源就是弃牌堆基本能量」的形态；其它 `shuffle … into your deck`
    # （如 Abra 的「shuffle it and all attached cards」）不在此处。
    if re.search(r"shuffle those (?:energy )?cards? into your deck", t, re.I):
        _me = re.search(r"for each basic \{([a-z])\} energy card in your discard "
                        r"pile", t, re.I)
        _color = _EN_SYMBOL_COLOR.get(_me.group(1).upper()) if _me else None
        if _color:
            actions.append(act("rule", None, "any",
                               {"energy_to_deck": {"color": _color,
                                                   "n": "all"}}))
        else:
            actions.append(act("rule", None, "any", {
                "note": "洗回牌库的能量卡来源未识别（B4）"}))

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
        # 传**匹配处之后的整段**（不止首个句号）：跨句依赖如
        # 「Heal 60 … . If you healed any damage **in this way**, discard …」
        # 的后半段在下一句里，只截到句号会漏掉 → 会被当成可精确表达（Super Potion）。
        tail_txt = t[m.start():]
        if _in_coin_branch(t, m.start()):
            # 抛币分支内的回复：本应产出 `coin:heads:heal:N`（引擎 coin 层暂不支持
            # 非伤害子效果嵌套），**不能**退化成无条件 heal（那是实质性加强）。
            actions.append(act("rule", None, "self",
                               {"note": "抛币分支内的回复（需 coin 嵌套，B4）"}))
        else:
            # Super Potion（2026-10-28 第十二批）：
            # 「Heal 60 damage from 1 of your Pokémon. If you healed any damage in
            #   this way, discard an Energy from that Pokémon.」
            # 回复目标 = 己方 1 只（`choose`），后续弃能只在该目标**真的被回复**
            # 时执行 → 合并成 `heal:60:choose:discard`（`:discard` 隐含 if_prev）。
            if re.search(r"if you healed any damage in this way", tail_txt, re.I) \
                    and re.search(r"discard an? energy from that pok[eé]mon",
                                  tail_txt, re.I) \
                    and re.search(r"from 1 of your pok[eé]mon", tail_txt, re.I):
                actions.append(act("heal", int(m.group(1)), "self",
                                   {"heal_scope": "choose", "discard_one": True}))
            else:
                scope, ptype = _heal_scope_of(tail_txt)
                if scope is None:
                    actions.append(act("rule", None, "self",
                                       {"note": "回复范围/限定不可精确表达（B4）"}))
                else:
                    extra = {"heal_scope": scope} if scope else {}
                    if ptype:
                        extra["heal_type"] = ptype
                    actions.append(act("heal", int(m.group(1)), "self", extra))
    elif re.search(r"heal all damage", t, re.I):
        # 全量回复（2026-10-28 第九批）：`heal:all[:<scope>][:type:<c>]`
        # ——数值是「回满」，范围口径与 `heal:N` **完全一致**（共用 `_heal_scope_of`）。
        _ma = re.search(r"heal all damage", t, re.I)
        if _in_coin_branch(t, _ma.start()):
            # 抛币分支内：由 `_detect_coin` 产 `coin_all:…:heal:all:…`；
            # 这里如实登记，解析后置阶段若 coin 已覆盖会移除该占位。
            actions.append(act("rule", None, "self",
                               {"note": "抛币分支内的回复（需 coin 嵌套，B4）"}))
        else:
            # 「Attach … to 1 of your Benched Pokémon. **If you do**, heal all
            # damage from **that Pokémon**.」（Leafeon）——后续对象 = 前序附能的
            # 那一只；附能成功才回复。用 bench_one + `:if_prev` 精确表达。
            _handled = False
            # 前序必须是附能子句（附能解析在本分支之后，故按**文本**判定）
            _pre = t[:t.lower().find("heal all damage")]
            if re.search(r"from that pok[eé]mon", t[_ma.start():], re.I) \
                    and re.search(r"\battach\b[^.]{0,120}?energy", _pre, re.I):
                actions.append(act("heal", None, "self",
                                   {"heal_scope": "bench_one",
                                    "heal_all": True, "if_prev": True}))
                _handled = True
            # Wally's Compassion（2026-10-28 第十二批）：
            # 「Heal all damage from 1 of your Mega Evolution Pokémon {ex}.
            #   If you healed any damage in this way, put all Energy attached to
            #   that Pokémon into your hand.」
            # 训练家卡在引擎里**只有一个 tag**（`TrainerDef.tag`），两个动作必须
            # 合成一条：`heal:all:mega:energy`（`:energy` = 回复真的发生后才把
            # 该目标的全部能量收回手牌，见 `battle._apply_heal`）。
            _mega_tail = t[_ma.start():]
            if not _handled \
                    and re.search(r"from 1 of your mega evolution pok[eé]mon",
                                  _mega_tail, re.I) \
                    and re.search(r"if you healed any damage in this way",
                                  _mega_tail, re.I) \
                    and re.search(r"put all energy attached to that pok[eé]mon "
                                  r"into your hand", _mega_tail, re.I):
                actions.append(act("heal", None, "self",
                                   {"heal_scope": "mega", "heal_all": True,
                                    "to_hand": True}))
                _handled = True
            _ml = re.search(r"heal all damage from 1 of your pok[eé]mon that has "
                            r"(\d+) hp or less remaining", t, re.I)
            if not _handled and _ml:
                # 「Heal all damage from 1 of your Pokémon that has 30 HP or less
                #  remaining.」（第一百零四批，Bianca's Devotion）
                # 范围 = 己方 1 只，但**带剩余 HP 上限**：超出上限时该卡本就不
                # 该能打，引擎侧回 0（`_apply_heal` 的 `:maxleft:`）。
                actions.append(act("heal", None, "self",
                                   {"heal_scope": "choose", "heal_all": True,
                                    "maxleft": int(_ml.group(1))}))
                _handled = True
            if not _handled:
                _scope, _pt = _heal_scope_of(t[_ma.start():])
                if _scope is None:
                    actions.append(act("rule", None, "self", {
                        "note": "全量回复（范围/后续不可精确表达 · B4）"}))
                else:
                    _extra = {"heal_scope": _scope, "heal_all": True}
                    if _pt:
                        _extra["heal_type"] = _pt
                    actions.append(act("heal", None, "self", _extra))

    # ---- B3（2026-10-28）：抽牌完整性（按句式族精确表达）----
    # ① 双方同抽 / ② 对手洗回手牌后抽：两种句式都**不是** `draw:`（我方抽）
    _me = _EN_EACH_PLAYER_DRAW_RE.search(t)
    _mo = _EN_OPP_SHUFFLE_DRAW_RE.search(t)
    m = _EN_DRAW_RE.search(t)
    if _me:
        _n = int(_me.group(1)) if _me.group(1) else 1
        actions.append(act("draw", _n, "deck", {"each_player": True}))
    elif _mo:
        _n = int(_mo.group(2))
        _if_hand = bool(re.search(r"if (?:they|your opponent) put any cards",
                                  _mo.group("mid"), re.I))
        actions.append(act("draw", _n, "deck",
                           {"shuffle_opp": True, "if_hand": _if_hand}))
    elif m:
        _cond = _draw_cond_of(t, m.start())
        if _cond is not None:
            # 条件式抽牌（Fezandipiti ex）：`draw:N:if:<cond>`
            actions.append(act("draw", int(m.group(1)), "deck", {"cond": _cond}))
        elif _in_coin_branch(t, m.start()):
            actions.append(act("rule", None, "deck",
                               {"note": "抛币分支内的抽牌（需 coin 嵌套，B4）"}))
        elif _draw_extra_of(t, m) is None:
            actions.append(act("rule", None, "deck",
                               {"note": "抽牌句式不可精确表达（按计数/对手同抽/"
                                        "条件追加/分支替换，B4）"}))
        else:
            actions.append(act("draw", int(m.group(1)), "deck"))
    elif _EN_DRAW_TO_RE.search(t):
        mt = _EN_DRAW_TO_RE.search(t)
        if _in_coin_branch(t, mt.start()):
            actions.append(act("rule", None, "deck",
                               {"note": "抛币分支内的抽牌（需 coin 嵌套，B4）"}))
        elif "instead" in t.lower() or re.search(r"draw\s+\d+\s+more", t, re.I):
            actions.append(act("rule", None, "deck",
                               {"note": "抽到指定张数带分支替换（B4）"}))
        else:
            actions.append(act("draw", None, "deck",
                               {"to_hand_size": int(mt.group(1))}))
    elif _EN_DRAW_PER_COUNTER_RE.search(t):
        # ③ 按计数源抽：「Draw a card for each of your Ancient Pokémon in play.」
        _seg = _EN_DRAW_PER_COUNTER_RE.search(t).group(0).lower()
        _per = None
        if re.search(r"opponent[\u2019']?s? benched pok", _seg):
            _per = "opp_bench"
        elif re.search(r"your ancient pok", _seg):
            _per = "own_ancient"
        if _per:
            actions.append(act("draw", 1, "deck", {"per_counter": _per}))
        else:
            actions.append(act("rule", None, "deck",
                               {"note": "按计数源抽牌：计数源未识别（B4）"}))
    elif re.search(r"draw (?:a|1) card", t, re.I):
        ma = re.search(r"draw (?:a|1) card", t, re.I)
        if _in_coin_branch(t, ma.start()):
            actions.append(act("rule", None, "deck",
                               {"note": "抛币分支内的抽牌（需 coin 嵌套，B4）"}))
        elif _draw_extra_of(t, ma) is None:
            actions.append(act("rule", None, "deck",
                               {"note": "按计数源抽牌（for each，B4）"}))
        else:
            actions.append(act("draw", 1, "deck"))

    # ---- S5 对手手牌信息族（2026-10-28）----
    # ⚠ 卡面里的撇号可能是 `’`（U+2019）：`t` 未做归一化 → 这里先归一化，
    #   否则 `opponent's` 永远匹配不到（实测三条句式全部失效）。
    _ts5 = _norm_apostrophe(t)
    if _EN_OPP_DISCARD_RANDOM_RE.search(_ts5):
        actions.append(act("rule", None, "opponent_hand",
                           {"discard_opp_random": 1}))
    if _EN_OPP_SHUFFLE_RANDOM_RE.search(_ts5):
        actions.append(act("rule", None, "opponent_hand",
                           {"shuffle_opp_hand": 1}))
    if (_mto := _EN_OPP_DISCARD_TO_RE.search(_ts5)):
        actions.append(act("rule", None, "opponent_hand",
                           {"discard_opp_to": int(_mto.group(1))}))
    if _EN_OPP_HAND_SAME_RE.search(_ts5):
        actions.append(act("rule", None, "opponent_hand",
                           {"opp_shuffle_same": True}))

    # ---- S6 能量移动（2026-10-28）----
    _mv = _EN_ENERGY_MOVE_RE.search(t)
    if _mv:
        _n_raw = (_mv.group("n") or "1").lower()
        _n = int(_n_raw) if _n_raw.isdigit() else 1
        _src = _energy_move_zone(_mv.group("src"), dest=False)
        _dst = _energy_move_zone(_mv.group("dst"), dest=True)
        _c = _mv.group("c")
        _et = _EN_ENERGY_LETTER.get(_c.upper()) if _c else None
        if _c and _et is None:
            actions.append(act("rule", None, "any",
                               {"note": "能量移动属性符号未识别（B4）"}))
        elif _src is None or _dst is None or (
                _src == _dst and _src not in ("any", "opp_any")):
            # 「any / opp_any」两侧同名是合法的（源与目标由位置码区分）；
            # 固定区域同名（active→active 等）才是判错。
            actions.append(act("rule", None, "any",
                               {"note": "能量移动的源/目标未识别（B4）"}))
        else:
            _tail = f":type:{_et}" if _et else ""
            actions.append(act("rule", None, "any", {
                "energy_move": f"{_n}{_tail}:from:{_src}:to:{_dst}"}))

    # ---- S6 回手 / 洗回牌库（2026-10-28）----
    _bs = _EN_BOUNCE_SELF_HAND_RE.search(t)
    _bd = _EN_BOUNCE_SELF_DECK_RE.search(t)
    _bb = _EN_BOUNCE_BENCH_RE.search(t)
    _bc = _EN_BOUNCE_CHOOSE_RE.search(t)
    _md_bench = _EN_DISCARD_TO_BENCH_RE.search(t)
    if _md_bench:
        _n_raw = _md_bench.group("n").lower()
        _n = 1 if _n_raw in ("a", "an") else int(_n_raw)
        _body = _norm_apostrophe(_md_bench.group("body")).lower()
        if re.search(r"pok[eé]mon|card", _body):
            _hp = re.search(r"(\d+)\s*HP\s+or\s+less", _body, re.I)
            if _hp:
                actions.append(act("rule", None, "any",
                                   {"bounce": f"discard_to_bench:{_n}:"
                                              f"hp:{_hp.group(1)}"}))
            else:
                actions.append(act("rule", None, "any", {
                    "note": "弃牌堆铺后备的限定未识别（B4）"}))
        else:
            _key = re.sub(r"[^a-z0-9]", "", _body)
            if not _key:
                actions.append(act("rule", None, "any", {
                    "note": "弃牌堆铺后备的卡名未识别（B4）"}))
            else:
                actions.append(act("rule", None, "any",
                                   {"bounce": f"discard_to_bench:{_n}:"
                                              f"name:{_key}"}))
    elif _bs or _bd or _bb or _bc:
        _discard_attached = bool(re.search(
            r"discard all cards attached", t, re.I))
        if _bd:
            _kind = "shuffle_self"
        elif _bb:
            _kind = "to_hand:bench"
        elif _bc:
            _kind = "to_hand:choose"
        else:
            _kind = "to_hand:self"
        _suffix = ":discard_attached" if _discard_attached else ""
        actions.append(act("rule", None, "any",
                           {"bounce": f"{_kind}{_suffix}"}))

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
        _combo = _en_recover_combo(body)
        if n is None:
            # B3：抛币决定张数（Stoutland「Flip 3 coins. Put a number of cards
            # up to the number of heads from your discard pile into your hand.」）
            # ⚠ 抛币句在**回收句之前**，不能用 `t[mr.start():]`（只看后半句会漏）
            _mc = re.search(r"flip\s+(\d+)\s+coins",
                            t[max(0, mr.start() - 120):mr.start()], re.I)
            if _mc:
                actions.append(act("recover", None, "discard",
                                   {"coin_heads": int(_mc.group(1))}))
            else:
                actions.append(act("rule", None, "discard",
                                   {"note": "弃牌堆回收：回收数量未识别"}))
        elif _combo:
            # B3：子集组合（`recover:N:combo:<a>+<b>[:type:<c>]`）
            actions.append(act("recover", n, "discard", {"kind": _combo}))
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

    # 附着能量收回手牌（2026-10-29 第二十三批）
    meh = _EN_ENERGY_TO_HAND_RE.search(t)
    if meh and _in_coin_branch(t, meh.start()):
        # 抛币分支内的能量回手（Dustox「Flip a coin. If heads, put an Energy …」）：
        # 引擎 coin 子效果目前只承接已在 `_detect_coin` 注册的形态 → 这里**不得**
        # 退化成无条件回手（那是把 50% 变成 100%，静默加强）。
        actions.append(act("rule", None, "any", {
            "note": "抛币分支内的能量回手（需 coin 嵌套，B4）"}))
    elif meh:
        _raw = meh.group(1).lower()
        n = int(_raw) if _raw.isdigit() else 1
        _letter = (meh.group(2) or "").upper()
        scope = ("self" if "this pok" in meh.group(3).lower()
                 else "opp_active")
        extra = {"scope": scope, "what": "energy"}
        if _letter:
            etype = _EN_ENERGY_LETTER.get(_letter)
            if not etype:
                actions.append(act("rule", None, "any", {
                    "note": f"能量回手：属性符号未识别（{{{_letter}}}）"}))
                extra = None
            else:
                extra["energy_type"] = etype
        if extra is not None:
            actions.append(act("bounce", n,
                               "self" if scope == "self" else "active_opponent",
                               extra))

    # 磨牌（Discard the top N cards of your / your opponent's deck）
    # ⚠ 用 `discard` + `from_deck` 表达而不是新增 action type：Schema 里的
    #   `ACTION_TYPES` 会进提示词，新增类型会牵动提示词长度守卫（无谓的漂移）。
    mm = _EN_MILL_RE.search(t)
    if mm:
        n = int(mm.group(1)) if mm.group(1) else 1
        side = "opp" if "opponent" in mm.group(2).lower() else "own"
        actions.append(act("discard", n, "deck",
                           {"from_deck": True, "side": side}))

    # ---- S6 弃道具 / 弃场地（2026-10-28）----
    if _EN_DISCARD_STADIUM_RE.search(t):
        actions.append(act("rule", None, "any", {"discard_stadium": True}))
    _mt = _EN_DISCARD_TOOLS_RE.search(t)
    _mc_tools = _EN_CHOOSE_TOOLS_RE.search(t)
    if _mt:
        _where = _norm_apostrophe(_mt.group("where")).lower()
        _sp_tools = "special energy" in _mt.group(0).lower()
        _pre = _is_pre_damage(t, _mt.start())
        if _pre and "opponent" in _where and "active" in _where:
            # 「Before doing damage, discard all Pokémon Tools [and Special
            # Energy] from your opponent's Active Pokémon.」（第二十五批）
            # ⚠ 必须先于伤害结算（引擎 `pre:` 阶段），否则守方道具已生效过一次。
            if _sp_tools:
                actions.append(act("rule", None, "any", {
                    "pre_discard_energy_special": {"n": "all", "side": "opp"}}))
            actions.append(act("rule", None, "any", {
                "pre_discard_tool": {"n": "all", "scope": "opp_active"}}))
        elif _sp_tools:
            # 「…Tools **and Special Energy**…」但不在伤害前阶段 →
            # 引擎的普通 `discard_energy` 不区分基本/特殊，无法精确表达
            actions.append(act("rule", None, "any", {
                "note": "同时弃道具与**特殊**能量（引擎不区分基本/特殊，B4）"}))
        elif "opponent" in _where and "active" in _where:
            actions.append(act("rule", None, "any",
                               {"discard_tool": {"n": "all",
                                                 "scope": "opp_active"}}))
        elif "opponent" in _where:
            actions.append(act("rule", None, "any",
                               {"discard_tool": {"n": "all", "scope": "opp"}}))
        elif "your active" in _where:
            actions.append(act("rule", None, "any",
                               {"discard_tool": {"n": "all", "scope": "own"}}))
        else:
            actions.append(act("rule", None, "any", {
                "note": "弃道具的对象未识别（B4）"}))
    elif _mc_tools:
        actions.append(act("rule", None, "any",
                           {"discard_tool": {"n": int(_mc_tools.group(1)),
                                             "scope": "both"}}))

    _da = _EN_DISCARD_ALL_RE.search(t)
    # 「Discard a **Special** Energy …」——引擎能量不分基本/特殊，`discard_energy`
    # 会按任意色弃一张 → 对手只贴了基本能量时该效果**本不该生效却生效**。
    # （实测 Cobalion「Discard a Special Energy from your opponent's Active Pokémon」）
    _sp_e = re.search(r"(?:discard|remove)\s+(?:up to\s+\d+\s+)?(?:\d+\s+|a\s+|an\s+)?"
                      r"special energy", lower)
    _sp_opp = _SP_OPP_ACTIVE_RE.search(t)
    if _sp_opp:
        # 「Discard a Special Energy from your opponent's Active Pokémon.」
        raw = (_sp_opp.group(1) or "").lower()
        actions.append(act("rule", None, "any", {
            "discard_energy_special": {"n": "all" if raw == "all" else 1,
                                       "side": "opp"}}))
    elif _sp_e:
        # 「Discard a Special Energy from **1 of your opponent's Pokémon**」
        # （第九十八批，Enhanced Hammer）——目标含后备，用 `:opp:special:any`
        # 让引擎在战斗位没有特殊能量时继续找后备；只写 `:opp:special`
        # 会把「对手把特殊能量贴后备」的情形静默变成空效果。
        _one_of = re.search(r"from 1 of your opponent'?s pok[eé]mon", lower)
        if _one_of:
            actions.append(act("rule", None, "any", {
                "discard_energy_special": {"n": 1, "side": "opp",
                                           "any": True}}))
        else:
            actions.append(act("rule", None, "any", {
                "note": "弃置**特殊**能量（引擎不区分基本/特殊能量，B4）"}))
    elif _da and _is_delayed(t, _da.start()):
        # Amarys：「Draw 4 cards. At the end of this turn, … discard your hand.」
        # 引擎没有「回合末延迟队列」，立即弃手牌会把刚抽的牌一并弃掉（时序错）。
        actions.append(act("rule", None, "hand",
                           {"note": "回合末才执行的弃手牌（需延迟队列，B4）"}))
    elif _da:
        actions.append(act("discard", None, "hand", {"all": True}))
    elif (_m := _EN_DISCARD_OPP_ENERGY_RE.search(t)) and not _in_coin_branch(t, _m.start()):
        # 冠词型「弃对手战斗位能量」——必须带 target，否则 tag 会退化成弃自身。
        # ⚠ 落在 `If heads, …` 分支内时**不在这里产出**（否则从"正面才弃"变成"必定弃"）
        actions.append(act("discard", 1, "active_opponent",
                           {"card_type": "energy"}))
    elif (_m := _EN_DISCARD_SELF_ENERGY_RE.search(t)) and not _in_coin_branch(t, _m.start()):
        # 冠词型「弃自身能量」（无数字）——引擎语义即「弃自己战斗位能量」，可精确对应
        actions.append(act("discard", 1, "self", {"card_type": "energy"}))
    elif (_m := _EN_DISCARD_ALL_ENERGY_RE.search(t)):
        actions.append(act("discard", None, "self", {"card_type": "energy",
                                                     "all": True}))
    elif ((_m := _TYPED_HAND_DISCARD_RE.search(t))
          or (_m := _TYPED_HAND_DISCARD_ONE_RE.search(t))) \
            and not _in_coin_branch(t, _m.start()):
        # B3：「Discard 2 Basic {G} Energy cards from your hand.」
        # 第八十八批：单数「Discard a Basic {G} Energy card from your hand.」
        # 必须在通用 `discard N energy` 之前判定 —— 否则会退化成
        # 「弃任意手牌能量」，把火能量当草能量弃掉（静默算错）。
        _nm = re.search(r"discard\s+(\d+)", _m.group(0), re.I)
        n = int(_nm.group(1)) if _nm else 1
        pt = _EN_ENERGY_LETTER.get(
            re.search(r"\{([a-z])\}", _m.group(0), re.I).group(1).upper())
        if pt is None:
            actions.append(act("rule", None, "hand",
                               {"note": "手牌弃能属性符号未识别（B4）"}))
        else:
            _nt = _norm_apostrophe(t)
            required = bool(_FAILED_DISCARD_GATE_RE.search(_nt)
                            or _FAILED_COST_NOTHING_RE.search(_nt))
            actions.append(act("discard", n, "hand",
                               {"hand_energy_type": pt,
                                "required": required}))
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

    # 对手手牌操作（2026-10-30 第三十二批）：整段认领。
    # 放在上面的 if/elif 链**之后**用 `return` 覆盖，而不是插进链里 ——
    # 这一族是「整句认领」，链里先前为同一句误 append 的动作必须一并丢弃
    # （例：`discard a card you find there` 不会被 `discard <n>` 匹配，但
    # 卡面若写成 `discard 2 cards` 就会先被链吃掉，方向还是「自己手牌」）。
    # 对招式与训练家都生效；特性侧不开放（本族卡面的特性形态都带前置语，
    # 如 Mandibuzz「Once during your turn, you may use this Ability.」，
    # 且要做「放到对手后备」，不在本批范围）。
    if kind in ("attack", "item", "supporter", "stadium", "tool"):
        _oh = _detect_opp_hand_op(t)
        if _oh:
            return {"kind": kind, "name": name, "trigger": "on_use",
                    "conditions": conditions,
                    "actions": [act("opp_hand", None, "opponent_hand", _oh)],
                    "source_text": str(text or ""), "citations": []}

    # 检索能量并**自由分配**附着（2026-10-30 第三十三批）：整段认领。
    # 必须排在通用检索分支之前 —— 那一支会把「attach them to …」当成检索**入手**，
    # 少执行一步附着（手牌凭空多 N 张能量，场上一个都没加速）。
    #
    # 2026-11-02 第三十七批起**开放特性侧**：Magneton SSP 59「Overvolt Discharge」
    # 是场上主动特性（`Once during your turn, you may …`），不需要 `evolve_act:`
    # 包装。进化触发形态（Marnie's Grimmsnarl ex）因此**仍不会被认领** ——
    # 三条正则全部以 `^` 锚定，而它的句首是 `when you play this Pokémon from your
    # hand to evolve …`，匹配不上（这是结构上的保证，不是靠额外判断）。
    # ⚠ 以后若给某条正则加「可选前缀」，必须先确认它不会把进化触发形态放进来：
    # 那样会「进池但特性不生效」（引擎的 field 特性是**每回合一次**，不是进化时）。
    if kind in ("attack", "ability"):
        _as = _detect_attach_spread(t)
        # 触发时机与 `_parse_rule_en` **同源**（`_early_trigger_of`）：
        # 认出「被击倒时」就不认领 —— 那一形态需要 `on_ko:` 包装，本族还没有
        # （写成 `on_use` 会把一次性效果变成每回合一次 = 静默加强）。进化触发
        # 则是本批要的正解：`trigger == "on_evolve"` → 包 `evolve_act:`。
        _atrig = _early_trigger_of(_norm_prose(t)) or "on_use"
        if _as and _atrig == "on_knockout":
            _as = None
        if _as:
            # 抛币计数形态（Mega Audino ex）：张数在结算时才知道 → 走独立动作
            # 类型，绝不预折成一个整数（那会静默改变期望值与上限）。
            if _as[0].get("coins"):
                return {"kind": kind, "name": name, "trigger": _atrig,
                        "conditions": conditions,
                        "actions": [act("attach_spread_flip",
                                        int(_as[0]["per"]), "self", _as[0])],
                        "source_text": str(text or ""), "citations": []}
            return {"kind": kind, "name": name, "trigger": _atrig,
                    "conditions": conditions,
                    "actions": [act("attach_spread", _s["n"], "self", _s)
                                for _s in _as],
                    "source_text": str(text or ""), "citations": []}
        # 「每只后备各 per 张、绑定给那一只」（Mega Gardevoir ex）：与
        # `attach_spread` 的「n 张任意分配」是两种语义，必须分开认领。
        _ab = _detect_attach_each_bench(t)
        if _ab:
            return {"kind": kind, "name": name, "trigger": _atrig,
                    "conditions": conditions,
                    "actions": [act("attach_each_bench", int(_ab["per"]),
                                    "self", _ab)],
                    "source_text": str(text or ""), "citations": []}
        # 双色「or 1 of each」（Steven's Metagross ex）：每类至多 1 张、总量可少。
        # 绝不拆成两条 `attach_spread:` —— 那会强制每类各拿 1 张（比卡面强）。
        _am = _detect_attach_multi(t)
        if _am:
            return {"kind": kind, "name": name, "trigger": _atrig,
                    "conditions": conditions,
                    "actions": [act("attach_spread_multi", None, "self", _am)],
                    "source_text": str(text or ""), "citations": []}
        # 「抛到反面为止、按正面数附给自己」（Snorlax）：张数是**随机变量**，
        # 只能结算时抛；不能折算成固定值。
        _fu = _detect_attach_flip_until(t)
        if _fu:
            return {"kind": kind, "name": name, "trigger": _atrig,
                    "conditions": conditions,
                    "actions": [act("attach_flip_until", 1, "self", _fu)],
                    "source_text": str(text or ""), "citations": []}

    # 「Choose up to N of your {X} Pokémon. For each of those Pokémon, …」
    # （Janine's Secret Art，supporter）：目标集合是**选出**的至多 N 只，
    # 与 `attach_each_bench`（全部后备）不同；末尾条件性自我中毒另行标注。
    _ap = _detect_attach_each_pick(t)
    if _ap:
        return {"kind": kind, "name": name, "trigger": "on_use",
                "conditions": conditions,
                "actions": [act("attach_each_pick", int(_ap["per"]), "self", _ap)],
                "source_text": str(text or ""), "citations": []}
    # 同族但源是**弃牌堆**（Glass Trumpet，2026-11-04 第五十九批）：
    # 「Choose up to 2 of your Benched {C} Pokémon and attach a Basic Energy card
    #  from your discard pile to each of them.」
    # ⚠ 交给通用 `attach:` 分支会落成 `attach:1:discard`（只附 1 张、只给 1 只）
    # —— 卡面是「至多 2 只**各** 1 张」，少做一半且不报错。
    _apd = _detect_attach_each_pick_discard(t)
    if _apd:
        return {"kind": kind, "name": name, "trigger": "on_use",
                "conditions": conditions,
                "actions": [act("attach_each_pick", int(_apd["per"]), "self",
                                _apd)],
                "source_text": str(text or ""), "citations": []}
    # 「… to **1 of your Stage 2 Pokémon**」（Rosa's Encouragement）——目标有限定，
    # 而通用 `attach:` 只按「任意己方位置」列目标 ⇒ 落成 `attach:2:discard` 就是
    # 静默加强（卡面只许附给二阶）。目标限定层未建模，如实整句登记。
    _asl = (kind in ("item", "supporter", "stadium", "tool")
            and _ATTACH_STAGE_LIMIT.match(_norm_prose(t)))
    if _asl:
        # 「attach up to 2 Basic Energy cards from your discard pile to 1 of your
        #  **Stage 2** Pokémon.」（第一百零九批，Rosa's Encouragement）
        # 引擎 `_attach_target_fp` 新增 `target:stage<N>`（候选限该阶段）。
        _mst = re.search(r"to 1 of your stage (\d) pok[eé]mon",
                         _norm_prose(t), re.I)
        _mn = re.search(r"attach up to (\d+) basic energy cards?", t, re.I)
        _stage = int(_mst.group(1)) if _mst else None
        if _stage and _stage == 2 and _mn:
            return {"kind": kind, "name": name, "trigger": "on_use",
                    "conditions": conditions,
                    "actions": [act("attach", int(_mn.group(1)), "self",
                                    {"energy_type": "any", "zone": "discard",
                                     "target_scope": f"stage{_stage}"})],
                    "source_text": str(text or ""), "citations": []}
        return {"kind": kind, "name": name, "trigger": "on_use",
                "conditions": conditions,
                "actions": [act("rule", None, "any", {
                    "note": "附着目标限定（1 of your Stage N Pokémon）未建模（B5）"})],
                "source_text": str(text or ""), "citations": []}

    # 「Flip N coins. If both of them are heads, … attach …」（Energy Coin，item）：
    # 训练家侧抛币门，门通过后把子效果（`attach:`）交给派发链。
    if kind in ("item", "supporter", "stadium", "tool"):
        _cg = _detect_coin_gate_attach(t)
        if _cg:
            return {"kind": kind, "name": name, "trigger": "on_use",
                    "conditions": conditions,
                    "actions": [act("coin_gate_attach", int(_cg["n"]), "self", _cg)],
                    "source_text": str(text or ""), "citations": []}

    # 「弃对手道具/特殊能量 或 弃竞技场」二选一（Blowtorch）：整段认领。
    # 必须排在 S6 的 `_EN_DISCARD_STADIUM_RE` 内联分支**之前** —— 那一支只产出
    # `discard_stadium`，会把前半句静默吞掉（半张卡丢失）。
    if kind in ("item", "supporter", "stadium", "tool"):
        if _detect_discard_tool_or_senergy(t):
            return {"kind": kind, "name": name, "trigger": "on_use",
                    "conditions": conditions,
                    "actions": [act("discard_tool_or_senergy", 1, "any",
                                    {"n": 1, "scope": "opp"})],
                    "source_text": str(text or ""), "citations": []}

    # 检视牌库顶 → 筛选 → 入手/铺场/附着（2026-10-30 第二十批）：整段认领。
    # 必须排在通用 `_EN_SEARCH_TOP_RE` 分支之前 —— 那一支只认「入手」去向，
    # 其它去向会落成 `rule` 拒绝；而「入手」那一支又完全不看筛选词（会把
    # 「a Supporter card」当成任意 1 张取走 = 静默拿错牌）。
    # 只对**训练家**卡生效：招式/特性侧的 tag 白名单（`ATTACK_TAG_PREFIXES` /
    # `_supported_ability_tags`）不含 `recon:`，认领了反而会被池准入**静默丢弃**。
    # 2026-11-02 第三十五批：放开到**特性/招式** —— 这一族里 Morpeko TWM 72 /
    # Rockruff JTG 84 / Litwick WHT 16 是「Once during your turn, look at the top
    # card … may discard it」的特性形态，Metang TEF 114 同理，Lapras ex SCR 32
    # 是招式。只在 `_detect_recon_top` **整句精确匹配**时才认领，所以不会把
    # 特性侧既有的 `recon:<N>` 旧口径卡卷进来（那些走下面的通用分支）。
    if kind in ("item", "supporter", "stadium", "tool", "ability", "attack"):
        _rc = _detect_recon_top(t)
        if _rc:
            return {"kind": kind, "name": name, "trigger": "on_use",
                    "conditions": conditions,
                    "actions": [act("recon", _rc["max"], _rc["dest"], _rc)],
                    "source_text": str(text or ""), "citations": []}

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
        elif re.search(r"put (?:them|those cards) back in any order",
                       t[m.end():], re.I):
            # 「Look at the top N cards of your opponent's deck and put them back
            # in any order.」（Team Rocket's Dottler）——只改变**顺序**、不移动卡：
            # 引擎按随机重排顶部 N 张建模（与 `shuffle_opp_hand` 同口径）。
            side = "opp" if re.search(r"opponent[\u2019']?s deck", t, re.I) \
                else "own"
            actions.append(act("rule", None, "any",
                               {"shuffle_top": _look, "side": side}))
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
        _sex = {"what": _en_search_what(lower)}
        if re.search(r"\bof different types\b", t, re.I):
            # 「up to 3 Basic Energy cards **of different types**」（Eevee SFA 50 /
            # Energy Search Pro SSP 176）：这是**检索约束**，不是修饰语。
            # 早期完全忽略它 → 允许拿 3 张**同色**能量（静默加强；第三十九批修正）。
            _sex["distinct"] = True
        actions.append(act("search", int(g) if g else 1, tgt, _sex))

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
            # 「attach … to 1 of your **Benched** Pokémon」：引擎默认附到自身，
            # 必须把「后备」写进 tag（Leafeon「Leaflet Blessings」）。
            if re.search(r"to 1 of your benched", lower):
                extra["target_scope"] = "bench"
            if re.search(r"in any way you like", _norm_prose(t)):
                # 「in any way you like」= **自由分配**，语义是「多张分给多只」。
                # 没被上面的 `attach_spread` 精确认领就说明句式不在本批覆盖范围内
                # （典型：Magneton SSP 59 还带「If you use this Ability, this
                # Pokémon is Knocked Out」代价）——此时若落成单手 `attach`，
                # 会变成「全给一只 + 不付代价」，比未建模更糟。故如实拒绝。
                actions.append(act("rule", None, "any",
                                   {"note": "自由分配附着（句式未精确识别，B4）"}))
            else:
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
            # 特性侧**必须**是主动施加（2026-10-27）：免疫 / checkup 追加伤害 /
            # 换位触发 三类都含状态词，却不是「让对手战斗位陷入状态」。
            if kind == "ability" and not _status_is_active_infliction(t_raw, word):
                actions.append(act("rule", None, "any", {
                    "note": "状态词非主动施加（免疫/追加伤害/换位触发，B4）"}))
                break
            # 抛币分支内的状态：单币单状态已由 `_detect_coin` 产出
            # `coin:<side>:<status>`，这里**不得**再产出一次无条件 status（否则从
            # 「正面才麻痹」变成「必定麻痹」）。多币条件 / 多状态目前没有可精确
            # 表达的 coin 子 tag，如实登记未建模。
            pos = lower.find(word)
            tnorm = _norm_apostrophe(t)
            mcoin = _COIN_OPP_STATUS_RE.search(tnorm)
            if mcoin and mcoin.start() <= pos <= mcoin.end():
                continue
            if _in_coin_branch_any(t, pos):
                # 多币条件状态（both/all … tails、at least N … heads）已由
                # `_detect_coin` 落成 `coin_each:…|cond:…` 复合子效果 → 不得
                # 再登记一条未建模（否则整卡被踢出池）。
                _cc = _coin_cond_of(t)
                if _cc and _cc[1].start() <= pos <= _cc[1].end():
                    continue
                actions.append(act("rule", None, "any", {
                    "note": "抛币分支内的状态：多币条件/多状态/非对手战斗位，"
                            "需扩展 coin 子效果（B4）"}))
                break
            tgt = "active_opponent" if _EN_OPP_RE.search(t) else "self"
            extra = {"status": tag}
            # 「is now Burned and Poisoned」（2026-11-04 第五十三批）：同一句里
            # 第二个状态必须一起落成 `status_multi:<a>+<b>`。捕获以本正则为准
            # （循环命中顺序 ≠ 文本顺序），位置判定限制在**该词所在那一句**。
            _ms = _EN_MULTI_STATUS_SENT.search(_norm_apostrophe(t))
            if _ms and _ms.start() <= pos <= _ms.end():
                _a = _STATUS_WORD_TO_TAG.get(_ms.group("a").lower())
                _b = _STATUS_WORD_TO_TAG.get(_ms.group("b").lower())
                if _a and _b and _a != _b:
                    extra = {"status": _a, "status2": _b}
            # 「If you discarded a Pokémon Tool **in this way**, … is now
            # Paralyzed.」（N's Joltik，第二十五批）—— 状态以伤害前阶段**真的
            # 弃到道具**为前提。此前该从句被整段忽略，变成无条件麻痹（静默加强）。
            if _IF_PRE_TOOL_RE.search(_sentence_at(t, pos)):
                extra["if_pre_tool"] = True
            actions.append(act("status", None, tgt, extra))
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

    # 招式击倒（2026-10-28 第九批）：按**目标范围**精确表达，不再统一成
    # 「击倒对手战斗位」。抛币分支内的击倒由 coin 子 tag 承载（见 `_detect_knockout`）。
    actions += _detect_knockout(t, kind)

    if _EN_EVOLVE_RE.search(t):
        # 逐个匹配判定，**跳过触发时机从句里的 evolve**（见 `_EN_EVOLVE_TRIGGER_RE`）
        spans = [mm.span() for mm in _EN_EVOLVE_TRIGGER_RE.finditer(t)]
        for me in re.finditer(r"evolve (?:it|this|your|1 of|a )", t, re.I):
            if any(a <= me.start() < b for a, b in spans):
                continue
            actions.append(act("evolve", None, "self"))
            break

    # 跨回合锁定（攻击封锁 / 撤退封锁）：先建模，再把对应句子从
    # `_EN_MODIFY_RE` 的判定里屏蔽，避免同一句被重复记成「静态规则修改」。
    lock_actions, lock_spans = _detect_lock_actions(t, kind, name)
    if lock_actions:
        actions.extend(lock_actions)
    # 弱点 / 抗性无视（仅招式）：bypass tag 与已建模句子 span 一并返回，
    # 避免同一句又落入「静态规则修改」兜底。
    wr_actions, wr_spans = _detect_wr_bypass(t) if kind == "attack" else ([], [])
    if wr_actions:
        actions.extend(wr_actions)
    modify_text = _strip_rules_paren(
        _mask_spans(t, lock_spans + wr_spans
                    + [m.span() for m in _EN_RESISTANCE_FILTER_RE.finditer(t)]))
    modify_norm = _norm_apostrophe(modify_text)
    # B3：带类型手牌弃能 + 失败门**已精确建模**（Lurantis / Ceruledge）时，
    # 不再把同一段文本登记为「失败门未建模」——否则卡永远进不了池。
    _typed_modeled = any(
        a.get("type") == "discard"
        and (a.get("extra") or {}).get("hand_energy_type")
        for a in actions)
    if _EN_MODIFY_RE.search(modify_text):
        actions.append(act("rule", None, "any",
                           {"note": "静态规则修改（弱点/抗性/伤害减免/限制）"}))
    # 未建模的「招式失败门」：抛币形态已由 `coin:tails:dmg_zero` 建模，跳过；
    # 其余条件（能量/场地/后备/手牌数/状态…）引擎均无失败门 → 如实登记。
    # 2026-10-28：条件可精确求值的形态已由 `nullify_if:<cond>` 建模 → 不再登记。
    _has_gate = any(a.get("type") == "rule"
                    and (a.get("extra") or {}).get("nullify_if")
                    for a in actions)
    if kind == "attack" and _ATTACK_DOES_NOTHING_RE.search(modify_norm) \
            and not _COIN_TAILS_NOTHING_RE.search(modify_norm) \
            and not _typed_modeled and not _has_gate:
        actions.append(act("rule", None, "any", {
            "note": "未建模的招式失败门（条件不满足则此招式无效，B5）"}))
    if _UNMODELED_DAMAGE_MOD_RE.search(modify_norm):
        actions.append(act("rule", None, "any",
                           {"note": "静态伤害增减（需引擎静态修正层，B4）"}))
    # 第一百零六批起：item / evolution / supporter / stadium 四类「下个回合禁打」
    # 都已建模（`play_lock:<kind>:opp:turn`），只剩 ACE SPEC / 「有特性的宝可梦」
    # 这类没有筛选层的形态仍须如实登记 —— 否则「锁已建模」会被这条兜底再登记成
    # 未建模，整卡白拒。
    _lock_modeled = bool(
        _ITEM_LOCK_RE.search(modify_norm)
        or _EVOLUTION_LOCK_RE.search(modify_norm)
        or _SUPPORTER_LOCK_RE.search(modify_norm)
        or _STADIUM_LOCK_RE.search(modify_norm))
    if _STATIC_PLAY_LOCK_RE.search(modify_norm) and not _lock_modeled:
        actions.append(act("rule", None, "any",
                           {"note": "手牌禁打（ACE SPEC/特性宝可梦等，需对手手牌"
                                    "禁用层，B5）"}))
    # 首回合招式使用限制（Terapagos ex / Scream Tail ex）：第一百零七批起由
    # `use_not:` / `use_only:` 承载（引擎在合法动作生成时剔除）。
    _ftm = _FIRST_TURN_ATTACK_RE.search(modify_norm)
    if _ftm:
        if re.search(r"can use this attack only if you go second", modify_norm,
                     re.I):
            actions.append(act("rule", None, "any",
                               {"use_only": "went_second_first_turn"}))
        else:
            actions.append(act("rule", None, "any",
                               {"use_not": "went_second_first_turn"}))
    # 「You can use this card only if …」= 打出的**前置条件**（Morty's Conviction
    # 的弃 1 手牌 / Unfair Stamp 的上回合被击倒）。引擎没有「打牌前置条件」层 →
    # 放通等于白送（代价/门槛静默消失），必须如实登记。
    if re.search(r"can use this card only if", modify_norm, re.I):
        actions.append(act("rule", None, "any",
                           {"note": "打出前置条件（You can use this card only if …）"
                                    "未建模（B5）"}))
    if not _typed_modeled and (
            _TYPED_HAND_DISCARD_RE.search(modify_norm) or
            _FAILED_DISCARD_GATE_RE.search(modify_norm)):
        actions.append(act("rule", None, "any",
                           {"note": "从手牌弃指定类型能量 / 弃不够则无效"
                                    "（需带类型手牌弃置与失败门，B5）"}))
    # B3：招式**击倒**效果（「…, and Knock Out your opponent's Active Pokémon.」，
    # Hydrapple「Hydra Breath」）——引擎没有「招式击倒」机制。若不拦，
    # 该招式会带着弃能 tag 进池，但**击倒效果静默消失**（少赢 2 张奖赏）。
    # 2026-10-28：主动击倒已由 `knockout:<scope>` 精确建模；只有**认不出的形态**
    # （如「Knock Out each …」）才保留这条未建模占位，避免静默丢效果。
    _ko_modeled = any(
        a.get("type") == "knockout"
        or str((a.get("extra") or {}).get("sub", "")).startswith("knockout")
        for a in actions)
    if kind == "attack" and not _ko_modeled and re.search(
            r"knock out (?:your|1 of your) opponent", modify_norm, re.I):
        actions.append(act("rule", None, "active_opponent",
                           {"note": "招式击倒效果未建模（B4）"}))
    # 「any number of」= 数量由玩家自由选择；引擎的固定 N / 全量口径都不等价。
    # 这类文本若只映射固定 1，会**静默少做**（检索/弃牌/附着/指示物移动），
    # 数量交互层落地前一律如实登记。检索形态已由 `dynamic_n` 单独拒绝，
    # 这里跳过，避免同一段文本登记两条。
    if re.search(r"\bany number of\b", modify_norm, re.I) \
            and not any(a.get("type") == "search" for a in actions) \
            and not any(a.get("type") == "rule" for a in actions):
        actions.append(act("rule", None, "any", {
            "note": "「any number of」自由数量（弃牌/附着/指示物移动，"
                    "需交互层，B4）"}))

    # 「… this attack does N more damage. Then, discard that Stadium.」——条件加伤的
    # **附带动作**。引擎新增 `discard_stadium`（2026-10-28）后可精确执行，
    # 因此**已建模时不再登记**（否则整卡永远进不了池）。
    _stadium_modeled = any(
        (a.get("extra") or {}).get("discard_stadium") for a in actions)
    if re.search(r"\b(?:discard|remove)\s+that\s+stadium\b", lower) \
            and not _stadium_modeled:
        actions.append(act("rule", None, "any",
                           {"note": "附带动作「弃置该场地」需引擎弃场地动作（B4）"}))

    # 触发时机（英文）
    _early = _early_trigger_of(lower)
    if _early:
        trigger = _early
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

    if _re_spans:
        # 被屏蔽的「受击触发」是**真实存在的效果**：
        #   · 可表达（status / counter / discard_energy）→ 产出 `on_damaged` 动作；
        #   · 不可表达（回合限定 / 随计数源缩放 / 检索）→ 如实登记未建模。
        # 绝不能只屏蔽不登记 —— 那等于让卡进池却"挨打时什么都不发生"。
        _od = _detect_on_damaged(t_raw, kind)
        if _od:
            actions.extend(_od)
        else:
            actions.append(act("rule", None, "any", {
                "note": "受击/受击历史从句（is|was damaged by an attack）需引擎 "
                        "on_damaged 或回合级历史（B4）"}))

    if not actions:
        actions.append(act("rule", None, "any", {"note": "静态/未建模（英文）"}))
    else:
        actions = _order_actions_by_text(actions, t)

    return {"kind": kind, "name": name, "trigger": trigger,
            "conditions": conditions, "actions": actions,
            # 2026-10-28：200 → 400 —— 进化触发特性常把「第二个触发时机」写在
            # 第一句之后（Flygon），截断会让双触发判定失效。
            "source_text": t_raw.strip()[:400]}


def _early_trigger_of(lower: str) -> Optional[str]:
    """英文卡面的**早触发**时机（被击倒 / 进化时）；都不是返回 None

    抽成独立函数是因为有**两个**调用方要判：`_parse_rule_en` 的 if/elif 链，
    以及 parse_rule 里「整段认领」的分支（`attach_spread` 等会提前 return，
    根本走不到链上）。两份实现必然漂移 —— 认领分支一旦把进化触发写成
    `on_use`，特性就从「进化那一刻一次」变成「每回合一次」（静默加强）。
    """
    if re.search(r"if .{0,30}(?:is|are) knocked out", lower) or \
            re.search(r"when .{0,30}knocked out", lower):
        return "on_knockout"
    if re.search(r"when you play .{0,40}to evolve", lower) or \
            re.search(r"when you evolve", lower):
        return "on_evolve"
    if re.search(r"when you play this pok[eé]mon from your hand onto your bench",
                 lower):
        # 「上场触发」（第一百批）：与进化触发**不同事件**，不能复用
        # `evolve_act:`（那会在进化时执行，而本卡是「从手牌放到后备」）。
        return "on_bench"
    return None


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


# ---------------- 效果族插件层（fx · 2026-10-27） ----------------
def _fx_known(tag: str) -> bool:
    """该 fx tag 是否已被引擎的某个效果族注册（未注册即为未建模）"""
    try:
        import sys as _sys
        eng = str(REPO / "packages" / "engine")
        if eng not in _sys.path:
            _sys.path.insert(0, eng)
        from engine.families import known as _known          # noqa: E402
        return bool(_known(tag))
    except Exception:                                        # noqa: BLE001
        return False


def _needs_fx(spec: dict) -> bool:
    """解析结果是否「未完成」—— 交给效果族插件层再试一次

    判据：没有动作，或存在**未映射的 rule 兜底动作**（weak_change 之类已是
    真正映射，不算未完成）。
    """
    actions = spec.get("actions") or []
    if spec.get("kind") == "energy":
        # 特殊能量只能**整段**由 `agent/fx/fx_special_energy.py` 认领：
        # 通用解析可能只抓到「draw 4 / put counters」这些子句，把主规则（提供量
        # 与条件）丢掉 —— 那正是"部分建模"的静默错算。
        return True
    if not actions:
        return True
    # 已真正映射的 rule 形态（不是兜底）：weak_change（弱点改写）/
    # discard_stadium（弃场地）/ discard_tool（弃道具）
    _MAPPED = ("weak_change", "discard_stadium", "discard_tool", "bounce",
               "energy_move", "discard_opp_random", "shuffle_opp_hand",
               "discard_opp_to", "opp_shuffle_same", "fossil", "borrow",
               "attack_denial", "attack_cost_up", "retreat_cost_up",
               "ignore_effects", "ignore_effects_wr", "nullify_if", "shuffle_top",
               "ko_prize_plus", "on_ko_counter", "on_ko_energy",
               # 第二十五批（2026-10-29）：伤害前阶段（「Before doing damage」）
               "pre_discard_tool", "pre_discard_energy_special",
               # 第二十八批（2026-10-29）：弃牌堆基本能量洗回牌库
               "energy_to_deck")
    for a in actions:
        if a.get("type") == "rule":
            extra = a.get("extra") or {}
            if not any(extra.get(k) for k in _MAPPED):
                return True
    return False


def _senergy_known(tag: str) -> bool:
    """该 `senergy:<kind>` 是否已被引擎特殊能量子系统建模"""
    try:
        import sys as _sys
        eng = str(REPO / "packages" / "engine")
        if eng not in _sys.path:
            _sys.path.insert(0, eng)
        from engine import special_energy as _se       # noqa: E402
        kind = str(tag).split(":", 1)[-1]
        return kind in _se.KINDS
    except Exception:                                        # noqa: BLE001
        return False


def _apply_fx(spec: dict, text: str, kind: str, name: str) -> dict:
    """把未完成的解析交给「效果族」插件层（整段认领制）

    族模块约定见 `agent/fx/__init__.py`：认领整段文本才返回，否则 None。
    认领成功后写 `spec["actions"]` 并标注 `fx_family`（便于对账与度量）。
    """
    if not _needs_fx(spec):
        return spec
    from .fx import detect_all                              # noqa: WPS433
    hit = detect_all(text, kind, name)
    if not hit:
        return spec
    spec["actions"] = list(hit.get("actions") or [])
    if hit.get("trigger"):
        spec["trigger"] = hit["trigger"]
    spec["fx_family"] = hit.get("family") or ""
    return spec


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
    # 打出代价（2026-11-02 第三十六批）：**先剥离**再解析。
    # 代价不是「效果」，不进 actions；它挂在 spec 的 `play_cost` 上，由
    # `to_engine_tags` 汇总到卡级、`TrainerDef.play_cost` 承载、引擎在派发
    # **之前**执行（顺序：付账 → 效果）。
    # 「If you go first, you may use this card during your first turn.」（第五十一批）
    # 是**许可**不是效果、也不是代价 → 剥掉；留着会被读成条件并挤掉主效果。
    _t = str(text or "")
    # 首回合许可句：训练家与招式都剥（两者写法不同，见 `_TURN1_PERMISSION`）。
    # 招式侧也要剥的理由：剥掉后主效果才回到**句首**，能被整句认领式正则
    # （`_TC_RESEARCH` 等 `^…$`）命中；否则会退化成多条散动作。
    _t = _TURN1_PERMISSION.sub("", _t).strip()
    # 打出**前置条件**（2026-11-04 第五十九批）：「You can use this card only if …」
    # 必须先于 `_split_play_cost` 剥 —— 两者句式几乎相同（都是 only if），
    # 但代价句里含 `discard N cards from your hand` 这类**动作**，若不先把纯条件句
    # 摘走，代价正则会把条件句当成代价解析（反之亦然）。
    text, _pgate = _split_play_gate(_t, kind)
    text, _pcost = _split_play_cost(text, kind)
    # 特性发动代价（2026-11-04 第四十九批）：同样**先剥离**再解析（理由同上）。
    text, _acost = _split_ability_cost(text, kind)
    spec = (_parse_rule_en(text, kind=kind, name=name)
            if text and not _CJK_RE.search(str(text))
            else _parse_rule_zh(text, kind=kind, name=name))
    if _pcost:
        spec["play_cost"] = _pcost
    if _pgate:
        spec["play_gate"] = _pgate
    if _acost:
        spec["ability_cost"] = _acost
    actions = spec.get("actions") or []
    # 被击倒 / 进化触发的整段认领（2026-10-28 第十批）：不得再走修正器后置识别，
    # 否则「in this way」后续会被重复识别成无条件动作。
    if kind == "ability" and _claimed_ability(str(text or ""), kind):
        return _apply_fx(spec, text, kind, name)
    # 特殊能量（2026-10-28）：卡面几乎全部规则都写在「As long as …」之后，
    # 只认第一段（提供 {C}）会把后面整段规则静默丢掉 → 必须**整段认领**。
    # 认不出则保留通用解析结果（通常是未建模占位），绝不部分建模。
    if kind == "energy":
        spec = _apply_fx(spec, text, kind, name)
        if spec.get("fx_family") == "special_energy":
            return spec
    # 化石卡（Fossil）：「Play this card as if it were a N-HP Basic {C} Pokémon …」
    # 与场地同样**硬分流**（整段认领）。落到通用解析只会得到一条「静态未建模」，
    # 卡池随后会把化石当普通物品收进来 —— 卡永远不会作为宝可梦上场（静默错算）。
    fossil = _detect_fossil(str(text or ""))
    if fossil:
        spec["actions"] = list(fossil)
        spec["trigger"] = "on_use"
        return _apply_fx(spec, text, kind, name)
    # 场地卡（Stadium）：卡面效果**只能**由 `_detect_stadium` 解释。
    # ⚠ 绝不能落到通用解析 —— 实测踩到两例**静默错映射**（比未建模危险得多）：
    #   · Dizzying Valley「Confused Pokémon don't recover from that condition…」
    #     被通用分支读成 `confuse` → 凭空让某只宝可梦混乱；
    #   · Grand Tree「search … Stage 1 … to evolve it」被读成 `candy`
    #     → 变成跳过进化阶。
    # 两者都不报错、局面正常，只有对账才看得出，因此这里做**硬分流**。
    if kind == "stadium":
        stadium = _detect_stadium(str(text or ""))
        if stadium:
            spec["actions"] = list(stadium)
        else:
            spec["actions"] = [{"type": "rule", "target": None, "amount": None,
                                "extra": {"note": "场地形态未建模（S3 未覆盖）"}}]
        return _apply_fx(spec, text, kind, name)
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
    # ⚠ 这里**不能**再 `actions + tools`：上面已经整体替换成 tools，再加一次会让
    # 每条道具效果在 `specs.actions` 里出现两遍（tag 侧靠去重看不出来，但
    # `_amounts` / 黄金集比对会把它当成多重集 → 假失败）。
    if mods:
        # 修正器出现时，移除「静态未建模」兜底 rule（已被真正识别）
        has_coin = any(m.get("type") == "coin_flip" for m in mods)
        drop = list(_RULE_PLACEHOLDER_NOTES) + (
            list(_COIN_COVERED_NOTES) if has_coin else [])
        # 「抛币分支内的回复」占位：只有 coin 子效果**确实是 heal** 时才可移除
        # （否则整条回复会既没有 coin 承载、也没有占位 → 静默丢效果）。
        # 复合子效果（`dmg_plus:30|heal:30`）也按段检查（2026-10-29 第十八批）。
        def _coin_segs(m) -> list:
            ex = m.get("extra") or {}
            return str(ex.get("sub", "")).split("|") + \
                [str(ex.get("sub_tails") or "")] + \
                [str(x) for x in (ex.get("sub_more") or [])]

        def _has_heal(m) -> bool:
            segs = _coin_segs(m)
            return any(s.startswith(("heal", "heal:all")) for s in segs)
        if any(_has_heal(m) for m in mods):
            drop.append("抛币分支内的回复")
        # 抛币分支里的指示物 / 能量回手（2026-11-03 第四十四批）：
        # 子效果已由 coin tag 精确承载 → 移除对应的「未建模」占位。
        if any(s.startswith("counters") for m in mods for s in _coin_segs(m)):
            drop.append("放置伤害指示物")
        if any(s.startswith("bounce:energy") for m in mods for s in _coin_segs(m)):
            drop.append("抛币分支内的能量回手")
        # 第四十五批的三个新形态各自的「未建模」占位：
        #   · 逐目标抛币伤害 → 通用附加伤害分支会登记 `that Pokémon` 不可表达；
        #   · 抛币选状态 → 状态词分支登记「非主动施加」；
        #   · 抛币免伤被动 → 受击从句分支登记「需 on_damaged」；
        #   · 双方洗回 + 抛币分抽 → 抽牌分支登记「抛币分支内的抽牌」。
        # 只有在**对应 tag 真的产出**时才移除占位（否则整段效果会静默丢失）。
        if any(str((m.get("extra") or {}).get("mode")) == "per_target" for m in mods):
            actions = [a for a in actions
                       if not (a.get("type") == "damage"
                               and str((a.get("extra") or {})
                                       .get("unaligned_target") or "")
                               .lower() == "that pokémon")]
        # 可选加伤 + 反伤（第四十七批）：分词器把「does M damage to itself」登记成
        # `damage` 动作（target=itself）→ 已由 `dmg_plus:N:recoil:M` 承载，移除占位。
        if any(m.get("type") == "dmg_recoil" for m in mods):
            actions = [a for a in actions
                       if not (a.get("type") == "damage"
                               and str((a.get("extra") or {})
                                       .get("unaligned_target") or "")
                               .lower() == "itself")]
        if any(s.startswith("status_choose") for m in mods for s in _coin_segs(m)):
            drop.append("状态词非主动施加")
        if any(str((m.get("extra") or {}).get("kind")) == "coin_prevent"
               for m in mods):
            drop.append("受击/受击历史从句")
        if any(m.get("type") == "shuffle_both_coin_draw" for m in mods):
            drop.append("抛币分支内的抽牌")
        # 第四十六批：全队限时减伤 / 单体限时护盾（含奖赏数打出前置条件）
        if any(m.get("type") == "team_shield" for m in mods):
            drop.append("静态规则修改")
            drop.append("静态伤害增减")
        if any(m.get("type") == "shield_choose" for m in mods):
            drop.append("静态规则修改")
            drop.append("打出前置条件")
        # 抛币选状态·全集（第四十七批，Grafaiai）：分词器只留一条泛化的
        # 「静态/未建模（英文）」——整段已由 coin status_choose 承载，移除占位。
        if any(s.startswith("status_choose") for m in mods for s in _coin_segs(m)) \
                and _COIN_STATUS_CHOOSE_ANY_RE.fullmatch(
                    _norm_apostrophe(text).strip() or ""):
            drop.append("静态/未建模（英文）")
        # 免击倒家族（第七十八/八十批）：整段已由 `passive:*survive:N` 承载
        # → 移除分词器留下的泛化占位，否则整卡会因「有文本无 tag」被拒收。
        if any(str((m.get("extra") or {}).get("kind")) in
               ("coin_survive", "survive") for m in mods):
            drop.append("静态/未建模（英文）")
        # 清除自身状态（第八十一批）：整段已由 `cure:self` 承载。
        if any(m.get("type") == "cure" for m in mods):
            drop.append("静态/未建模（英文）")
        actions = [a for a in actions
                   if not (a.get("type") == "rule"
                           and _note_starts_with(a, drop))]
        spec["actions"] = actions + mods
    elif tools or stadium:
        # 只有道具/场地时也要写回（否则识别到了却没进 spec —— 实测踩过）
        spec["actions"] = actions
    return _apply_fx(spec, text, kind, name)


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
# 同一枚币的**双分支**（「Flip a coin. If heads, … If tails, …」，2026-11-03
# 第四十四批）：必须只抛**一次**。拆成 `coin:heads:` + `coin:tails:` 会变成
# 两次独立抛币（两个分支可能同时生效或都不生效），因此产出单条
# `coin_flip:heads:<a>|tails:<b>`。
_COIN_FORK_COUNTERS_RE = re.compile(
    r"flip\s+a\s+coin\.\s+if\s+heads,\s*put\s+(\d+)\s+damage\s+counters?\s+on\s+1\s+"
    r"of\s+your\s+opponent's\s+pok[eé]mon\.\s+if\s+tails,\s*put\s+(\d+)\s+damage\s+"
    r"counters?\s+on\s+your\s+active\s+pok[eé]mon", re.I)
# 「Flip a coin. If heads, put an Energy attached to your opponent's Active
# Pokémon into their hand.」（Dustox「Boisterous Wind」）→
# `coin:heads:bounce:energy:opp_active:1`（特性侧由 `_do_ability` 抛一次）。
_COIN_HEADS_OPP_ACTIVE_ENERGY_RE = re.compile(
    r"if\s+heads[^.]*?put\s+(?:an?|1)\s+(?:\{[a-z]\}\s+)?energy\s+attached\s+to\s+"
    r"your\s+opponent's\s+active\s+pok[eé]mon\s+into\s+their\s+hand", re.I)
# 逐目标抛币伤害（第四十五批，Mega Zygarde ex「Nullifying Zero」）：
# 「For each of your opponent's Pokémon, flip a coin. If heads, this attack does
#  N damage to that Pokémon.」→ 每个目标**各抛一枚**，正面才打它。
# ⚠ 不能折成 `snipe:opp:N:all`（那会变成「每只都必中」）或 `coin:heads:snipe:…`
#   （只抛一次，全打/全不打）。
_COIN_PER_TARGET_DMG_RE = re.compile(
    r"for\s+each\s+of\s+your\s+opponent's\s+pok[eé]mon,\s*flip\s+a\s+coin\.\s*"
    r"if\s+heads,\s*this\s+attack\s+does\s+(\d+)\s+damage\s+to\s+that\s+"
    r"pok[eé]mon", re.I)
# 抛币选状态（第四十五批，Cradily「Selective Slime」）：
# 「… flip a coin. If heads, choose Burned, Confused, or Poisoned. Your
#  opponent's Active Pokémon is now affected by that Special Condition.」
# → 「三选一」必须落成**一条** `status_choose:` 子效果（引擎确定性取首个）。
_COIN_STATUS_CHOOSE_RE = re.compile(
    r"if\s+heads,\s*choose\s+burned,\s*confused,\s*or\s+poisoned\.\s*"
    r"your\s+opponent's\s+active\s+pok[eé]mon\s+is\s+now\s+affected\s+by\s+that\s+"
    r"special\s+condition", re.I)
# 抛币免伤被动（第四十五批，Fezandipiti「Adrena-Pheromone」）：
# 「If this Pokémon has any {D} Energy attached and is damaged by an attack,
#  flip a coin. If heads, prevent that damage.」→ `passive:coin_prevent:<color>`
# **在伤害结算内**抛币（事后无法撤销已加的伤害）。
_COIN_PREVENT_PASSIVE_RE = re.compile(
    r"if\s+this\s+pok[eé]mon\s+has\s+any\s+\{([a-z])\}\s+energy\s+attached\s+and\s+"
    r"is\s+damaged\s+by\s+an\s+attack,\s*flip\s+a\s+coin\.\s*if\s+heads,\s*"
    r"prevent\s+that\s+damage", re.I)
# 双方洗回手牌 + 抛币分抽（第四十五批，Harlequin）：
# 「Each player shuffles their hand into their deck. Then, flip a coin. If heads,
#  you draw H1 cards, and your opponent draws H2 cards. If tails, … T1/T2.」
# 训练家只有**一个**卡级 tag → 洗牌 + 单次抛币 + 双方不同张数必须合并成一条。
_SHUFFLE_BOTH_COIN_DRAW_RE = re.compile(
    r"each\s+player\s+shuffles\s+their\s+hand\s+into\s+their\s+deck\.\s*then,\s*"
    r"flip\s+a\s+coin\.\s*if\s+heads,\s*you\s+draw\s+(\d+)\s+cards?,\s*and\s+your\s+"
    r"opponent\s+draws\s+(\d+)\s+cards?\.\s*if\s+tails,\s*you\s+draw\s+(\d+)\s+"
    r"cards?,\s*and\s+your\s+opponent\s+draws\s+(\d+)\s+cards?", re.I)
# 「Flip a coin **for each** X」（2026-11-04 第四十八批）—— 币数**本身**由计数源
# 决定。卡面三种写法：
#   · 「for each Energy attached to this Pokémon」（Heliolisk / Dudunsparce ex）
#   · 「for each {W} Energy attached to this Pokémon」（Volcanion）
#   · 「for each {D} Pokémon you have in play」（Scrafty）
# ⚠ 必须**先于** `_COIN_PER_SIDE_RE`：后者只认「Flip N coins」，遇到这条会
# 把 `(\d+)?` 留空 → 当成 **1 枚币**，也就是把「7 个能量 = 7 枚币」静默压成
# 「1 枚币」。更糟的是它只产出 damage 动作、**既没有 tag 也没有 unsupported**
# → 整卡在登记表里只剩一个 `unmatched`，看不出缺什么。
_COIN_EACH_SRC = re.compile(
    r"^flip a coin for each (?P<src>"
    r"\{[a-z]\} energy attached to this pok[eé]mon"
    r"|\{[a-z]\} pok[eé]mon you have in play"
    r"|energy attached to this pok[eé]mon"
    r")[.] this attack does (?P<n>\d+) (?P<more>more )?damage for each heads[.]$",
    re.I)


def _coin_each_src_of(src: str, norm: str) -> Optional[str]:
    """「for each X」短语 → 计数源名；认不出返回 None（绝不猜）

    ``own_active_energy`` 的语义是「**出招者**身上的能量」——卡面写的是
    「attached to **this** Pokémon」，不是「全队」。用 `own_all_energy`
    会多算其它宝可梦的能量（静默加强）。
    """
    low = str(src or "").lower()
    if low.startswith("energy attached"):
        return "own_active_energy"
    m = re.match(r"^\{([a-z])\} energy attached", low)
    if m:
        color = _EN_ENERGY_LETTER.get(m.group(1).upper())
        return f"own_active_energy_{color}" if color else None
    m = re.match(r"^\{([a-z])\} pok[eé]mon you have in play", low)
    if m:
        color = _EN_ENERGY_LETTER.get(m.group(1).upper())
        # ⚠ 下划线不用冒号：见 `_eval_counter` 里 `own_field_type_<color>` 的说明
        return f"own_field_type_{color}" if color else None
    return None
# 「Flip N coins. This attack does M more damage for each heads.」（v1.4）
# 语义 = 抛 N 枚、每个正面重复一次加伤 → coin_each:N:heads:dmg_plus:M
# 注意：卡面在 "Flip N coins." 与 "This attack does…" 之间**有句号**，
# 故必须允许跨句点匹配（用 [\s\S] 而非 [^.]）。
_COIN_PER_SIDE_RE = re.compile(
    r"flip\s+(\d+)?\s*coins?[\s\S]{0,80}?does\s+(\d+)\s+(more\s+)?damage"
    r"[\s\S]{0,40}?for\s+each\s+(heads|tails)", re.I)
# 「Flip a coin until you get tails. This attack does N [more] damage for each
# heads.」（2026-10-29 第十八批；2026-11-02 第四十批补 `dmg_set`）→
# `coin_until_tails:dmg_plus:N` / `coin_until_tails:dmg_set:N`。
# ⚠ 必须**先于** `_COIN_PER_SIDE_RE` 判定：后者会把「a coin until…」当成
# 1 枚币的 each 分支（语义变成「50% 一次 +N」而不是「连续正面累加」）。
# ⚠ `more` 的有无是**两种语义**（与 `_COIN_PER_SIDE_RE` 同一条纪律）：
#   · 有 more → 在印刷伤害上**加** per × 正面数（`dmg_plus`）
#   · 无 more → 印刷伤害**按正面数重算**（`dmg_set`，0 正面 = 0 伤害）
# 此前只认 more，于是 Marill TEF 64 / Mudbray DRI 107 / Beartic BLK 26 /
# Jigglypuff PFL 76 四张「does N damage for each heads」**什么都没产出、也没有
# 任何 unsupported 记录**（静默丢卡：整卡不收，界面上看不出原因）。
_COIN_UNTIL_TAILS_RE = re.compile(
    r"flip\s+a\s+coin\s+until\s+you\s+get\s+tails[^.]{0,40}?\.\s*"
    r"this\s+attack\s+does\s+(\d+)\s+(more\s+)?damage\s+for\s+each\s+heads",
    re.I)
# 「Flip a coin. If heads, your opponent's Active Pokémon is now Paralyzed.」
# → coin:heads:paralyze（单币 + 单状态 + 对手战斗位）。
# 末尾必须是句号/结尾：像 Lilligant「Paralyzed **and Poisoned**」这种多状态
# 不能被截成只麻痹（会静默丢掉中毒），交由状态层的未建模守卫处理。
_COIN_OPP_STATUS_RE = re.compile(
    r"flip\s+a\s+coin[\s\S]{0,80}?if\s+(heads|tails)[^.]*?"
    r"your\s+opponent'?s\s+active\s+pok[eé]mon\s+is\s+now\s+"
    r"(asleep|burned|confused|paralyzed|poisoned)\s*(?:[.!]|$)", re.I)
# 「Flip a coin. If heads, <状态(可两个)>. If tails, <状态>.」（2026-11-04
# 第五十三批，Lilligant「Bemusing Aroma」）——**同一枚币的双分支**。
# 此前 heads 分支匹配不上（其后跟的是 " and Poisoned" 而非句号），于是只落
# `coin:tails:confuse`：正面那半（麻痹+中毒）**整段静默消失**，且不掉卡。
# 表达方式：`coin_each:1:heads:<heads子效果>|cond:all_tails:<tails子效果>`
# —— 只抛 1 枚币，正面走 plain 子效果、反面走 `all_tails` 条件子效果，
# 共用同一次结果，既不重复抛币也不落成两条 `coin:`（= 两次独立抛币）。
_COIN_STATUS_FORK_RE = re.compile(
    r"flip a coin[.]\s*if heads,\s*your opponent'?s? active pok[eé]mon is now "
    r"(?P<h>(?:asleep|burned|confused|paralyzed|poisoned)"
    r"(?: and (?:asleep|burned|confused|paralyzed|poisoned))?)"
    r"[.]\s*if tails,\s*your opponent'?s? active pok[eé]mon is now "
    r"(?P<t>asleep|burned|confused|paralyzed|poisoned)", re.I)
# 多币条件（both / either / at least N）暂无法用现有 coin_each 精确表达
# （coin_each 只能表达「至少 1 个正面」的重复子效果）。
_MULTI_COIN_COND_RE = re.compile(
    r"if\s+(?:both|all)\s+of\s+them\s+are\s+(?:heads|tails)"
    r"|if\s+(?:either|at least\s+\d+)\s+of\s+them\s+(?:is|are)\s+(?:heads|tails)",
    re.I)
# 多币条件 + 对手战斗位状态（2026-10-29 第二十二批）：
#   「Flip 2 coins. This attack does 90 damage for each heads. If both of them are
#    tails, your opponent's Active Pokémon is now Confused.」（Slurpuff）
#   「Flip 4 coins. … If at least 2 of them are heads, … is now Paralyzed.」（Butterfree）
# → 复合进同一条 coin_each：`coin_each:2:heads:dmg_set:90|cond:all_tails:confuse`
_COIN_COND_STATUS = (r"your\s+opponent'?s\s+active\s+pok[eé]mon\s+is\s+now\s+"
                     r"(asleep|burned|confused|paralyzed|poisoned)\s*(?:[.!]|$)")
_COIN_MULTI_STATUS_RE = re.compile(
    r"if\s+(both|all)\s+of\s+them\s+are\s+(heads|tails)[^.]*?"
    + _COIN_COND_STATUS, re.I)
_COIN_MULTI_AT_LEAST_RE = re.compile(
    r"if\s+at\s+least\s+(\d+)\s+of\s+them\s+are\s+(heads|tails)[^.]*?"
    + _COIN_COND_STATUS, re.I)
# 「If **either** of them is heads, …」（第九十八批，Vanilluxe「Double Freeze」）
# —— 与 at_least 的区别只是措辞（`is` 而非 `are`），语义 = 「≥1 个正面」。
_COIN_MULTI_EITHER_RE = re.compile(
    r"if\s+either\s+of\s+them\s+is\s+(heads|tails)[^.]*?"
    + _COIN_COND_STATUS, re.I)


def _coin_cond_of(text: str):
    """多币条件状态 → ``(cond 子效果, 匹配对象)``；非该形态 → ``None``

    只收「对手战斗位**单一**状态」；多状态（"Paralyzed and Poisoned"）与
    其它目标一律不认（宁可未建模，不可静默少施加/错施加）。
    """
    tnorm = _norm_apostrophe(text or "")
    m = _COIN_MULTI_EITHER_RE.search(tnorm)
    if m:
        mode = ("at_least_heads" if m.group(1).lower() == "heads"
                else "at_least_tails")
        return (f"cond:{mode}:1:{_STATUS_WORD_TO_TAG[m.group(2).lower()]}", m)
    m = _COIN_MULTI_AT_LEAST_RE.search(tnorm)
    if m:
        mode = ("at_least_heads" if m.group(2).lower() == "heads"
                else "at_least_tails")
        return (f"cond:{mode}:{int(m.group(1))}:"
                f"{_STATUS_WORD_TO_TAG[m.group(3).lower()]}", m)
    m = _COIN_MULTI_STATUS_RE.search(tnorm)
    if m:
        mode = ("all_heads" if m.group(2).lower() == "heads" else "all_tails")
        return (f"cond:{mode}:{_STATUS_WORD_TO_TAG[m.group(3).lower()]}", m)
    return None
# 伤害缩放
_SCALING_MORE_RE = re.compile(
    r"does\s+(\d+)\s+more\s+damage\s+for\s+each\s+([^.]{4,90})", re.I)
_SCALING_BASE_RE = re.compile(
    r"this\s+attack\s+does\s+(\d+)\s+damage\s+for\s+each\s+([^.]{4,90})", re.I)
# 「This attack does N **less** damage for each X」（Tinkaton / Throh）
_SCALING_LESS_RE = re.compile(
    r"does\s+(\d+)\s+less\s+damage\s+for\s+each\s+([^.]{4,90})", re.I)
# 护盾 / 被动
_SHIELD_REDUCE_RE = re.compile(r"takes\s+(\d+)\s+less\s+damage", re.I)
_BENCH_IMMUNE_RE = re.compile(
    r"as\s+long\s+as\s+this\s+pok[eé]mon\s+is\s+on\s+your\s+bench[^.]*?"
    r"prevent\s+all\s+damage", re.I)
_SHIELD_PREVENT_RE = re.compile(
    r"prevent\s+all\s+damage(?:\s+from\s+and\s+effects\s+of\s+attacks)?"
    r"[^.]*?(?:done\s+)?to\s+this\s+pok[eé]mon", re.I)
# 全队限时减伤（第四十六批，Iron Defender）：
# 「During your opponent's next turn, all of your {M} Pokémon take 30 less damage
#  … (This includes new Pokémon that come into play.)」
# → `team_shield:reduce:N:type:<color>`（**玩家级**，新上场的宝可梦也受益）
_TEAM_SHIELD_RE = re.compile(
    r"during your opponent's next turn, all of your \{([a-z])\} pok[eé]mon take "
    r"(\d+) less damage from attacks from your opponent's pok[eé]mon "
    r"\(after applying weakness and resistance\)\.\s*"
    r"\(this includes new pok[eé]mon that come into play\.\)", re.I)
# 单体限时护盾 + 奖赏数打出前置条件（第四十六批，Acerola's Mischief）：
# 「You can use this card only if your opponent has N or fewer Prize cards
#  remaining. Choose 1 of your Pokémon in play. During your opponent's next
#  turn, prevent all damage from and effects of attacks done to that Pokémon
#  by your opponent's Pokémon {ex}.」
_SHIELD_CHOOSE_RE = re.compile(
    r"you can use this card only if your opponent has (\d+) or fewer prize "
    r"cards remaining\.\s*choose 1 of your pok[eé]mon in play\.\s*"
    r"during your opponent's next turn, prevent all damage from and effects of "
    r"attacks done to that pok[eé]mon by your opponent's pok[eé]mon \{ex\}\.",
    re.I)
# 可选加伤 + 反伤（第四十七批，Gurdurr / Dondozo ex）：
# 「You may do N more damage. If you do, this Pokémon also does M damage to itself.」
# 引擎无交互层 → 确定性**取用**（多打 N，同时承受 M 反伤），两者必须**同一条**
# tag（`dmg_plus:N:recoil:M`）——拆成 `dmg_plus:N` + `self:M` 会丢掉「If you do」
# 的联动，将来若引入选择层就会变成静默多算/少算。
_YOU_MAY_RECOIL_RE = re.compile(
    r"you\s+may\s+do\s+(\d+)\s+more\s+damage\.\s*if\s+you\s+do,\s*this\s+"
    r"pok[eé]mon\s+also\s+does\s+(\d+)\s+damage\s+to\s+itself\.", re.I)
# 「choose a Special Condition」全集（第四十七批，Grafaiai）：卡面不列举，
# 确定性默认取 `rules.STATUSES` 首个（poison）；五选一必须一条 status_choose。
_COIN_STATUS_CHOOSE_ANY_RE = re.compile(
    r"flip\s+a\s+coin\.\s*if\s+heads,\s*choose\s+a\s+special\s+condition\.\s*"
    r"your\s+opponent's\s+active\s+pok[eé]mon\s+is\s+now\s+affected\s+by\s+that\s+"
    r"special\s+condition\.", re.I)
# 抛币免击倒（第七十八批，Mega Hawlucha ex「Tenacious Body」）：
# 「If this Pokémon would be Knocked Out by damage from an attack, flip a coin.
#  If heads, this Pokémon is not Knocked Out, and its remaining HP becomes N.」
# ⚠ 与道具 `tool:survive:N` 不同：**不要求满 HP**、是特性不是道具、且要抛币。
_COIN_SURVIVE_RE = re.compile(
    r"if\s+this\s+pok[eé]mon\s+would\s+be\s+knocked\s+out\s+by\s+damage\s+from\s+"
    r"an\s+attack,\s*flip\s+a\s+coin\.\s*if\s+heads,\s*this\s+pok[eé]mon\s+is\s+"
    r"not\s+knocked\s+out,\s*and\s+its\s+remaining\s+hp\s+becomes\s+(\d+)\.", re.I)
# 满 HP 免击倒·特性版（第八十批，Pikachu ex「Resolute Heart」/ Crustle）：
# 「If this Pokémon has full HP and would be Knocked Out by damage from an attack,
#  it is not Knocked Out, and its remaining HP becomes N.」
# ⚠ 三个口径缺一不可：① 必须**受伤前满 HP**（调用点已算 was_full）；
# ② 是**特性**不是道具（不弃置自身）；③ **不抛币**（与 coin_survive 反口径）。
# 少了「full HP」短语就不是本形态 —— 不能放宽，否则会把「残血也免死」的卡
# 静默加强。
_SURVIVE_FULL_HP_RE = re.compile(
    r"if\s+this\s+pok[eé]mon\s+has\s+full\s+hp\s+and\s+would\s+be\s+knocked\s+out\s+"
    r"by\s+damage\s+from\s+an\s+attack,\s*it\s+is\s+not\s+knocked\s+out,\s*and\s+"
    r"its\s+remaining\s+hp\s+becomes\s+(\d+)\.", re.I)
# 清除自身全部特殊状态（第八十一批，Arboliva ex「Aroma Shot」）：
# 「This Pokémon recovers from all Special Conditions.」
# ⚠ 只认**整句且作用自身**的形态。「heal … from your Pokémon」「Your opponent's
# Active Pokémon recovers …」等换对象的写法一律不认领（否则会清错阵营）；
# 「recovers from **a** Special Condition」是单数，范围不同，也不认领。
_CURE_SELF_RE = re.compile(
    r"this\s+pok[eé]mon\s+recovers\s+from\s+all\s+special\s+conditions\.", re.I)
# 弃牌堆宝可梦洗回牌库（第八十二批，Sacred Ash）：
# 「Shuffle up to N Pokémon from your discard pile into your deck.」
# ⚠ 两头钉死（fullmatch）：「Shuffle **all** Pokémon」「into your **hand**」
# 都不是本形态；「up to N」由引擎确定性取至多 N 张（choose 类默认口径）。
_POKEMON_TO_DECK_RE = re.compile(
    r"shuffle\s+up\s+to\s+(\d+)\s+pok[eé]mon\s+from\s+your\s+discard\s+pile\s+"
    r"into\s+your\s+deck\.", re.I)
# 检索后弃置（第八十三批，Brilliant Blender）：
# 「Search your deck for up to N cards and discard them. Then, shuffle your deck.」
# ⚠ 与 `search_pokemon:*`（入手）/`search_top:*`（放牌库顶）都不同：
# 去向是**弃牌堆**。落成入手会凭空多 N 张手牌（静默加强）。
# ⚠ 只认「任意卡 + 训练家卡级」的干净整句：Drilbur 的「放到后备时触发 +
# 限 Basic {F} Energy」需要上场触发钩子与能量筛选层，本批如实拒绝。
_SEARCH_DISCARD_RE = re.compile(
    r"search\s+your\s+deck\s+for\s+up\s+to\s+(\d+)\s+cards?\s+and\s+discard\s+"
    r"them\.\s*then,?\s*shuffle\s+your\s+deck\.", re.I)
# 放指示物直到剩余 HP 为 N（第八十四批，PalossandEX / Scolipede）：
# 「Put/Place damage counters on each of your opponent's Benched Pokémon /
#  your opponent's Active Pokémon until its remaining HP is N.」
# ⚠ 与 `counters:<个数>` **不是同一口径**：这里是「目标剩余 HP」而非「放几个」，
# 且要逐只按各自当前 damage 计算差额；已有伤害更多时一只都不能回血。
# 只认两种明确的阵营+范围，其它范围（each Pokémon / 1 of …）一律不认领。
_COUNTERS_TO_HP_RE = re.compile(
    r"(?:put|place)\s+damage\s+counters\s+on\s+"
    r"(?P<scope>each\s+of\s+your\s+opponent's\s+benched\s+pok[eé]mon|"
    r"your\s+opponent's\s+active\s+pok[eé]mon)\s+until\s+its\s+remaining\s+"
    r"hp\s+is\s+(?P<hp>\d+)\.", re.I)
# 静态免伤（2026-10-28 第八批）：**整句**认领，只收「来源 / 门槛」可精确判定的形态。
#   卡面原型：
#     Prevent all damage done to this Pokémon by attacks from your opponent's
#     Basic Pokémon {ex}.                                    → self / from:basic_ex
#     Prevent all damage done to this Pokémon by attacks from your opponent's
#     Pokémon {ex}.                                          → self / from:ex
#     Prevent all damage from and effects of attacks from your opponent's Tera
#     Pokémon done to this Pokémon.                          → self / from:tera / +effects
#     Prevent all damage from attacks done to this Pokémon by your opponent's
#     Pokémon that have an Ability.                          → self / from:has_ability
#     Prevent all damage from and effects of attacks done to this Pokémon by
#     your opponent's Pokémon that have any Special Energy attached.
#                                                → self / from:special_energy / +effects
#     Prevent all damage done to this Pokémon by attacks from your opponent's
#     Pokémon if that damage is 200 or more.                 → self / min:200
#     Prevent all damage from and effects of attacks from your opponent's
#     Pokémon done to your Benched Pokémon.                  → team_bench / +effects
#     Prevent all damage done to your Benched Pokémon that don't have a Rule Box
#     by attacks from your opponent's Pokémon.               → team_bench / no_rule_box
# 其它来源限定（卡名阵营 / 特定招式 / 附着了道具…）**一律不认**：宁可未建模。
_IMMUNE_PATTERNS = (
    (r"prevent all damage done to this pok[eé]mon by attacks from your "
     r"opponent'?s basic pok[eé]mon \{ex\}", "self", "from:basic_ex", False),
    (r"prevent all damage done to this pok[eé]mon by attacks from your "
     r"opponent'?s pok[eé]mon \{ex\}", "self", "from:ex", False),
    (r"prevent all damage from and effects of attacks from your opponent'?s "
     r"tera pok[eé]mon done to this pok[eé]mon", "self", "from:tera", True),
    (r"prevent all damage from attacks done to this pok[eé]mon by your "
     r"opponent'?s pok[eé]mon that have an ability", "self",
     "from:has_ability", False),
    (r"prevent all damage from and effects of attacks done to this pok[eé]mon "
     r"by your opponent'?s pok[eé]mon that have any special energy attached",
     "self", "from:special_energy", True),
    (r"prevent all damage done to this pok[eé]mon by attacks from your "
     r"opponent'?s pok[eé]mon if that damage is (\d+) or more", "self",
     "min", False),
    (r"prevent all damage from and effects of attacks from your opponent'?s "
     r"pok[eé]mon done to your benched pok[eé]mon", "team_bench", "", True),
    (r"prevent all damage done to your benched pok[eé]mon that don'?t have a "
     r"rule box by attacks from your opponent'?s pok[eé]mon", "team_bench",
     "no_rule_box", False),
)
_RULE_BOX_NOTE_RE = re.compile(
    r"\(\s*pok[eé]mon \{[a-z]+\},?\s*pok[eé]mon \{[a-z]+\},?\s*etc\. "
    r"have rule boxes\.\s*\)", re.I)
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
    # ⚠ 「damage counter you placed in this way」**不是**弃牌数。早期把它映射到
    #   `own_discarded_this_turn`，于是 Walking Wake 按本回合弃牌数算伤害（若本回合
    #   没弃牌则恒为 0）。放置指示物的**数量**需交互层（up to N + 先放置后伤害），
    #   在机制落地前必须返回 None、如实登记未建模。
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


def _tag_name_key(name: str) -> str:
    """卡名 → tag 安全的检索键（小写 + 只留 a-z0-9）

    卡名里有空格/撇号（"Team Rocket"、"Marnie's"），而引擎 tag 是 ``:`` 分隔的
    紧凑串 —— 原样塞进去会让 ``split(":")`` 与审计脚本的键值解析变脆。
    归一化后 ``"Team Rocket"`` → ``teamrocket``，引擎侧用同样的归一化做子串匹配。
    ⚠ 必须与 `engine/battle.py::_name_key` **同规则**，否则匹配永远不成立。
    """
    return re.sub(r"[^a-z0-9]+", "", str(name or "").lower())


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

    # 第七批（2026-10-28）----
    # 「for each Prize card your opponent took **during their last turn**」（Okidogi）
    if re.search(r"prize cards? your opponent took during their last turn", low):
        return "opp_prizes_taken_last_turn"
    # 第二十七批（2026-10-29）：`snipe` 子句内的「for each damage counter on
    # **that Pokémon**」—— 每目标按**自身**指示物数缩放（Team Rocket's Sneasel）。
    # 返回**哨兵** `target_counters`（只在 `snipe:` 里解释）；绝不能用全局计数源，
    # 否则会把其它宝可梦身上的指示物也算进来（静默多算）。
    if re.fullmatch(r"damage counters? on that pok[eé]mon", low.strip()):
        return "target_counters"
    # 「… for each Trainer/Energy card you find there」（揭示对手手牌后计数）
    if "you find there" in low:
        if re.search(r"\btrainer card", low):
            return "opp_hand_kind:trainer"
        if re.search(r"\benergy card", low):
            return "opp_hand_kind:energy"

    # ---- 第九十四批（2026-11-05）：两个计数源短语 ----
    # 「for each of your opponent's Pokémon {ex} and Pokémon {V} in play」
    # （Zoroark「Illusory Hijacking」）——⚠ 本卡池无 V 类卡，引擎侧另起名
    # `opp_ex_or_v` 而非直接复用 `opp_ex`，便于将来加 V 时定点修改。
    if re.search(r"of your opponent'?s pok[eé]mon \{?\s*ex\s*\}?"
                 r"(?:\s*and\s*pok[eé]mon \{?\s*v\s*\})?\s*in play", low):
        # 只写 {ex} → 既有 `opp_ex`；写成「{ex} and {V}」→ 独立的 `opp_ex_or_v`
        # （引擎侧两名字当前同值，但分开命名，将来加 V 支持时只需改一处）。
        return "opp_ex_or_v" if re.search(r"\{?\s*v\s*\}", low) else "opp_ex"
    # 「for each Pokémon Tool attached to all Pokémon」（Bronzong「Tool Drop」）
    # 必须是 **all**（双方）—— 只数己方会系统性少算。
    if re.search(r"pok[eé]mon tool attached to all pok[eé]mon", low):
        return "both_field_tools"
    # 「双方后备」是引擎已支持的独立计数源；必须显式写出 both 才映射。
    # 不能先走阵营判定 —— 「both yours and your opponent's」同时含 opponent，
    # 早期会被算成**仅对手**后备（Skeledirge / Lillie's Clefairy ex 少算一半）。
    if re.search(r"benched pok[eé]mon\s*\(?both yours and your opponent'?s\)?",
                 low):
        return "both_bench"
    # 带限定词的后备（that has… / with…）是**子集**，总数不能代替子集。
    if re.search(r"benched pok[eé]mon\s+(?:that|with|which)\b", low):
        return None
    # 任何「that has / with a / named / 引号卡名」限定都是**子集**：
    #   · 「your Pokémon in play that has the Round attack」
    #   · 「Pokémon in play that has “Koffing” or “Weezing” in its name」
    # 按全场总数映射会系统性多算（Round 四张 + Weezing 曾因此被收进池）。
    # ⑤ 第六批（2026-10-28）：带限定词的**子集**计数 —— 引擎按逐项匹配精确求值
    #    （必须排在 `_FILTER_QUALIFIER_RE` 之前：那条会把 that has… 一律拒绝）
    m = re.search(r"pok[eé]mon in play that (?:has|have) the ([a-z][a-z' ]{2,20}?) "
                  r"attack", low)
    if m:
        key = re.sub(r"[^a-z0-9]", "", m.group(1))
        if key:
            return f"own_field_has_attack:{key}"
    m = re.search(r"pok[eé]mon in play that (?:has|have) [\"“]([a-z ]{2,20})[\"”] or "
                  r"[\"“]([a-z ]{2,20})[\"”] in its name", low)
    if m:
        k1 = re.sub(r"[^a-z0-9]", "", m.group(1))
        k2 = re.sub(r"[^a-z0-9]", "", m.group(2))
        if k1 and k2:
            return f"both_field_named_any:{k1}+{k2}"
    m = re.search(r"pok[eé]mon in your discard pile that (?:has|have) the "
                  r"([a-z][a-z' ]{2,20}?) attack", low)
    if m:
        key = re.sub(r"[^a-z0-9]", "", m.group(1))
        if key:
            return f"own_discard_has_attack:{key}"
    m = re.search(r"supporter card that has [\"“]([a-z ]{2,20})[\"”] in its name in "
                  r"your discard pile", low)
    if m:
        key = re.sub(r"[^a-z0-9]", "", m.group(1))
        if key:
            return f"own_discard_named_supporter:{key}"
    # 第四十三批（2026-11-03）：Ancient 是**卡牌子类**（`category`），不是卡名 ——
    # 「for each Ancient card in your discard pile」（Roaring Moon）。按卡名找会恒为 0
    # （静默少算），按弃牌堆总数又会多算别的卡。
    if re.search(r"\bancient card in your discard pile\b", low):
        return "own_discard_category:ancient"
    # 「for each {L} Energy attached to all of your Iono's Pokémon」（Iono's Voltorb）
    # —— 阵营 + **属性** + 具名三重限定；通用能量分支会退化成全队能量（多算）。
    m = re.search(r"energy attached to all of your "
                  r"((?!(?:opponent|benched)\b)[a-z][a-z' ]{1,20}?)'s "
                  r"pok[eé]mon", low)
    if m:
        _mc = re.search(r"\{([a-z])\}", raw, re.I)
        _color = _EN_SYMBOL_COLOR.get(_mc.group(1).upper()) if _mc else None
        key = _tag_name_key(m.group(1))
        if key and _color:
            return f"own_field_named_energy:{key}:{_color}"
    m = re.search(r"([a-z][a-z' ]{2,20}) card in your discard pile", low)
    if m:
        raw_name = m.group(1).strip()
        if raw_name not in _NON_NAME_WORDS:
            key = re.sub(r"[^a-z0-9]", "", raw_name)
            if key:
                return f"own_discard_named:{key}"

    # 第二十六批（2026-10-29）：**限定后**的指示物计数源
    #   · 「for each damage counter on all of your Benched {F} Pokémon」（Gigalith）
    #   · 「… all of your Benched Cynthia's Pokémon」（Cynthia's Spiritomb）
    #   · 「for each of your Pokémon that has “Tauros” in its name that has any
    #      damage counters on it」（Paldean Tauros，数**只数**而非指示物数）
    # ⚠ 必须排在 `_FILTER_QUALIFIER_RE` 与 `_is_subset_phrase` **之前**：那两道
    #   守卫会把「{F} Pokémon」「具名所有格」「that has …」一律当成不可精确子集
    #   拒绝，而这三条引擎都能**逐项**算出。
    m = re.search(r"damage counters? on all of your benched \{([a-z])\} "
                  r"pok[eé]mon", low)
    if m:
        color = _EN_SYMBOL_COLOR.get(m.group(1).upper())
        if color:
            return f"own_bench_counters_type:{color}"
    m = re.search(r"damage counters? on all of your benched "
                  r"([a-z][a-z' ]{1,20}?)'s pok[eé]mon", low)
    if m:
        key = _tag_name_key(m.group(1))
        if key:
            return f"own_bench_counters_named:{key}"
    m = re.search(r"(?:each )?of your pok[eé]mon that has [\"“]([a-z ]{2,20})[\"”] "
                  r"in its name that has any damage counters? on it", low)
    if m:
        key = re.sub(r"[^a-z0-9]", "", m.group(1))
        if key:
            return f"own_field_named_with_damage:{key}"

    # 通用拒绝：凡带「that has / with a / named / 引号卡名」的限定都可能是**子集**，
    # 总数不能代替子集（Round 四张 + Weezing 曾因此被收进池）
    if _FILTER_QUALIFIER_RE.search(raw):
        return None

    # ④ 类别限定的「在场」计数：Basic / Evolution 的判定依据是**进化阶段**
    #    （stage==0 / stage>0），引擎可精确数出，不属于「无法判定的子集」。
    #    必须排在 `_is_subset_phrase` 之前 —— 后者会把 basic/evolution 一律
    #    当成类别子集拒绝。
    if re.search(r"\b(?:of )?your basic pok[eé]mon in play\b", low):
        return "own_basic"
    if re.search(r"\b(?:of )?your evolution pok[eé]mon in play\b", low):
        return "own_evolution"

    # ⑥ S6 计数缩放（2026-10-28）：数据集分类字段 / 卡名阵营 / 弃牌堆类别。
    #    这些都必须排在 `_is_subset_phrase` 之前 —— 后者会把 Ancient / 具名
    #    所有格一律当成「不可按总数算」而拒绝，但它们其实可以**精确**数出来。
    if re.search(r"\b(?:of )?your ancient pok[eé]mon in play\b", low):
        return "own_ancient"
    if re.search(r"opponent'?s pok[eé]mon \{ex\} in play", low) \
            and "pok[eé]mon {v}" not in low:
        return "opp_ex"
    m = re.search(r"of your ((?!(?:opponent|benched)\b)[a-z][a-z' ]{1,20}?)"
                  r"'?s pok[eé]mon in play", low)
    if m:
        key = _tag_name_key(m.group(1))
        if key:
            return f"own_named_{key}"
    if re.search(r"pok[eé]mon tools? attached to all pok[eé]mon in play", low):
        return "both_field_tools"
    if re.search(r"in your opponent'?s active pok[eé]mon'?s retreat cost", low):
        return "opp_active_retreat"
    m = re.search(r"basic \{([a-z])\} energy card in your discard pile", low)
    if m:
        color = _EN_SYMBOL_COLOR.get(m.group(1).upper())
        if color:
            return f"own_discard_energy_{color}"
    if re.search(r"basic energy card in your opponent'?s discard pile", low):
        return "opp_discard_energy"
    if re.search(r"item card in your opponent'?s discard pile", low):
        return "opp_discard_item"

    # ⑤ 对手手牌**张数**是公开信息（内容不可见，但数量可见）；对手全场
    #    伤害指示物、对手战斗位特殊状态数、自身全场道具数同样是公开且确定性的。
    if re.search(r"\bcards? in your opponent'?s hand\b", low):
        return "opp_hand"
    if re.search(r"\bdamage counters? on all of your opponent'?s "
                 r"pok[eé]mon\b", low):
        return "opp_field_counters"
    if re.search(r"\bspecial conditions? affecting your opponent'?s active "
                 r"pok[eé]mon\b", low):
        return "opp_active_status_count"
    if re.search(r"\bpok[eé]mon tools? attached to all of your pok[eé]mon\b",
                 low):
        return "own_field_tools"

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
    return (str(s or "").replace("\u2019", "'").replace("\u2018", "'")
            # 不换行空格（\xa0）在真实卡面里极常见（如「your\xa0Pokémon」）：
            # 不归一化会让按空格书写的正则**静默失配**（Enamorus 实测踩到）
            .replace("\u00a0", " "))


def _condition_from_text(text: str) -> Optional[str]:
    """英文条件从句 → 引擎 `dmg_plus:N:if:<cond>` 的合法条件（未识别 → None）

    只在白名单内映射；引擎没有的条件（场地 / 曾打出某卡 / 对手宝可梦属性 …）
    一律返回 None，由调用方登记为未建模。

    **带参数条件**（2026-09-22 新增）：卡面最常见的能量条件
    「If this Pokémon has any {L} Energy attached, this attack does 80 more damage」
    → `own_active_energy_type:lightning`（引擎侧按色计数，精确对应）。
    ⚠ 与之形近但**不可**近似的写法**故意不映射**（宁缺勿错）：
      · `has any Special Energy attached` —— 引擎无「特殊能量」计数源
    「has at least N **extra** Energy attached **(in addition to this attack's
    cost)**」自 2026-10-29（第十九批）起可精确表达：引擎按
    「战斗位总能量 − 本次招式费用」求值 → `own_active_extra_energy_at_least:N`。
    ⚠ 必须同时出现 `in addition to this attack's cost`；只写「extra Energy」
    无法确认与费用无关 → 仍返回 None。
    """
    low = _norm_apostrophe(text).lower()
    m = re.search(r"if this pok[eé]mon has any \{\s*([a-z])\s*\}\s*energy attached",
                  low)
    if m:
        color = _EN_SYMBOL_COLOR.get(m.group(1).upper())
        return f"own_active_energy_type:{color}" if color else None
    # 「has at least N **extra** Energy attached (in addition to this attack's
    # cost)」→ 按「总能量 − 本次招式费用 ≥ N」精确求值（第十九批）
    m = re.search(r"has at least (\d+) extra energy attached", low)
    if m and re.search(r"in addition to this attack'?s cost", low):
        return f"own_active_extra_energy_at_least:{int(m.group(1))}"
    # 其余「extra Energy」写法语义不明（可能与招式费用无关）→ 不猜
    if re.search(r"extra\s+energy\s+attached", low):
        return None
    m = re.search(r"if this pok[eé]mon has any energy attached", low)
    if m:
        return "own_active_energy_at_least:1"
    # 「If <X> and <Y> are on your Bench, this attack does N more damage.」
    # （Metagross「Conjoined Beams」）→ `own_bench_named_all:x+y`
    m = re.search(r"if ([a-z][a-z' ]{2,24}?) and ([a-z][a-z' ]{2,24}?) are on "
                  r"your bench", low)
    if m:
        return (f"own_bench_named_all:{_k(m.group(1))}+{_k(m.group(2))}")
    # ---- 第九十三批（2026-11-05）：三个新条件 ----
    # 「If your opponent's Active Pokémon is affected by a Special Condition …」
    # （Mismagius「Assassin's Magic」/ 大量既有卡）——引擎条件早已存在，
    # 但**短语映射**一直缺，导致这类条件加伤/指示物全部落未建模。
    if re.search(r"opponent'?s active pok[eé]mon is affected by a special "
                 r"condition", low):
        return "opp_active_has_status"
    # 「If your opponent's Active Pokémon is a Pokémon {ex} or Pokémon {V}, this
    #  attack does 110 more damage.」（Galvantula ex「Charged Web」）
    if re.search(r"opponent'?s active pok[eé]mon is a pok[eé]mon \{?\s*ex\s*\}?"
                 r"\s*or\s*pok[eé]mon \{?\s*v\s*\}?", low):
        return "opp_active_is_ex_or_v"
    # 「If you played a Supporter card that has “Team Rocket” in its name from your
    #  hand during this turn, this attack does 100 more damage.」
    # （Team Rocket's Kangaskhan ex「Wicked Impact」）
    m = re.search(r"if you played a supporter card that has [\"“]([^\"”]{2,24})[\"”] "
                  r"in its name from your hand during this turn", low)
    if m:
        return f"played_supporter_named:{_k(m.group(1))}"
    # 「If any of your Benched Pancham have any damage counters on them, this
    #  attack does 120 more damage.」（Pangoro「Master's Punch」）
    m = re.search(r"if any of your benched ([a-z][a-z' ]{2,24}?) have any "
                  r"damage counters on them", low)
    if m:
        return f"own_bench_named_has_damage:{_k(m.group(1))}"
    # 「if you have a Stadium in play」是**类别**条件，不是卡名（Dhelmise 实测：
    # 泛化成 `own_field_name:astadium` 会让引擎报「无此条件」）。
    if re.search(r"if you have a stadium in play", low):
        return "stadium_in_play"
    # ---- 静态规则族用到的几种（第一百一十二批起）----
    m = re.search(r"opponent has any cards in their discard pile that have "
                  r"[\u201c\u201d\"']?(?P<k>[a-z' ]+?)[\u201c\u201d\"']? in the name",
                  low)
    if m:
        key = _tag_name_key(m.group("k"))
        if key:
            return f"opp_discard_has_named:{key}"
    if re.search(r"opponent has no pok[eé]mon \{ex\} or pok[eé]mon \{v\}", low):
        # 「no Pokémon {ex} or {V} in play」→ 正向条件的取反
        return "not:opp_has_ex_or_v"
    m = re.search(r"opponent has exactly (\d+) cards? in their hand", low)
    if m:
        return f"opp_hand_exactly:{int(m.group(1))}"
    if re.search(r"this pok[eé]mon is affected by a special condition", low):
        return "own_active_status_any"
    if re.search(r"this pok[eé]mon has any damage counters on it", low):
        return "own_active_has_damage"
    m = re.search(r"unless you have (\d+) or more (?P<f>[a-z][a-z' .-]{2,24}?)"
                  r"[\u2019']?s pok[eé]mon in play", low)
    if m:
        key = _tag_name_key(m.group("f"))
        if key:
            return f"own_field_name_at_least:{key}:{int(m.group(1))}"
    m = re.search(r"if you have (?P<mon>[a-z][a-z' .-]{2,24}?) in play", low)
    if m:
        # 「if you have Solrock in play」—— 带参条件 `own_field_name:<key>`。
        # ⚠ 冠词开头的（a/an/any）一定是**类别**而非卡名，交出给通用表处理。
        _nm = m.group("mon").strip()
        if not re.match(r"^(?:a|an|any)\b", _nm):
            key = _tag_name_key(_nm)
            if key:
                return f"own_field_name:{key}"
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
# 不是**卡名**的类别词：`<X> card in your discard pile` 里的 X 若落在下表，
# 说明卡面在按**类别**计数（Ancient/Future/…），引擎无该类别的弃牌堆元数据
# → 必须拒绝，绝不能当成「名字含 ancient 的卡」计数（静默错算）
_NON_NAME_WORDS = {
    "ancient", "future", "tera", "basic", "evolution", "evolved", "stage",
    "pokemon", "trainer", "item", "supporter", "stadium", "tool", "energy",
    "prize", "damage", "special", "rule", "box", "card", "other", "any",
}
# 具名能量（所有格写法 → 编辑器侧 `senergy:<kind>` 的 kind 归一键）
_NAMED_ENERGY_ALIASES = {
    "team rocket's": "teamrocket",
    "team rockets": "teamrocket",
}
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

    # ---- 第五批（2026-10-27）：其余「当下局面可直接读出」的条件 ----
    # 纪律不变：**只能**用当下棋盘状态判定。凡依赖「本回合是否换入 / 是否被治疗 /
    # 是否进化 / 上回合用过哪招 / 本回合是否打出某卡」的，一律返回 None（需回合记账）。
    # ⚠ 顺序有讲究：`this Pokémon has any {G} Energy attached`（第 22 条）必须
    #   **先于** 第 4 条的「has any damage counters」之后的通用匹配，否则会被
    #   `_condition_from_text` 的 `has any {X} Energy` 分支先截走（那条在更外层）。
    #   —— 实际上外层优先，此处仅保证同层内不互相抢占。

    # 9) 场上有场地（任意一方）。两种写法都真实存在：
    #    「If a Stadium is in play」/「If you have a Stadium in play」
    if re.search(r"if (?:a|any) stadium is in play"
                 r"|if you have a stadium in play", seg):
        return "stadium_in_play"

    # 10) 自身战斗位有道具（Pokémon Tool）
    if re.search(r"if this pok[eé]mon has a pok[eé]mon tool attached", seg):
        return "own_active_has_tool"

    # 11) 自身战斗位满 HP（「has no damage counters on it」）
    if re.search(r"if this pok[eé]mon has no damage counters? on it", seg):
        return "own_active_full_hp"

    # 12) 对手战斗位有**任意**特殊状态（不指定是哪一种）
    if re.search(opp_active + r" is affected by a special condition", seg):
        return "opp_active_has_status"

    # 13) 自身战斗位处于某特殊状态
    #     ⚠ 「Burned or Poisoned」是**析取**：引擎新增 `own_active_status_any` 精确表达；
    #        其它状态的析取仍是单值条件表达不了的 → 拒绝（不能取其一）
    m = re.search(r"if this pok[eé]mon is (burned|poisoned|asleep|paraly[sz]ed|confused)",
                  seg)
    if m:
        if re.search(r"if this pok[eé]mon is burned or poisoned"
                     r"|if this pok[eé]mon is poisoned or burned", seg):
            return "own_active_status_any:burn,poison"
        if re.search(r"if this pok[eé]mon is (?:burned|poisoned|asleep|paraly[sz]ed|"
                     r"confused)\s+or\s+", seg):
            return None
        word = m.group(1).replace("paralysed", "paralyzed")
        tag = _STATUS_WORD_TO_TAG.get(word)
        return f"own_active_status:{tag}" if tag else None

    # 14) 我方**后备**有伤害指示物
    if re.search(r"if your benched pok[eé]mon have any damage counters? on them", seg):
        return "own_bench_has_damage"

    # 15) 双方手牌数相同
    if re.search(r"if you have the same number of cards in your hand as your opponent",
                 seg):
        return "own_hand_eq_opp_hand"

    # 16) 双方战斗位能量数相同
    if re.search(r"if this pok[eé]mon and your opponent'?s active pok[eé]mon have the "
                 r"same amount of energy attached", seg):
        return "own_opp_active_energy_equal"

    # 17) 对手手牌数上限
    m = re.search(r"if your opponent has (\d+) or fewer cards? in (?:their|his or her) "
                  r"hand", seg)
    if m:
        return f"opp_hand_at_most:{int(m.group(1))}"

    # 18) 对手剩余奖赏卡上限
    m = re.search(r"if your opponent has (\d+) or fewer prize cards? remaining", seg)
    if m:
        return f"opp_prizes_at_most:{int(m.group(1))}"

    # 18b) 我方牌库剩余张数上限（2026-10-29 第十七批）
    #      Wo-Chien「If there are 3 or fewer cards in your deck, this attack also
    #      does 120 damage to 2 of your opponent's Benched Pokémon.」
    m = re.search(r"if there (?:are|is) (\d+) or fewer cards? in your deck", seg)
    if m:
        return f"own_deck_at_most:{int(m.group(1))}"

    # 19) 我方剩余奖赏卡恰为 N
    m = re.search(r"if you have exactly (\d+) prize cards? remaining", seg)
    if m:
        return f"own_prizes_exactly:{int(m.group(1))}"

    # 20) 对手战斗位的进化阶段
    m = re.search(opp_active + r" is a stage (\d) pok[eé]mon", seg)
    if m:
        return f"opp_active_is_stage:{int(m.group(1))}"

    # 21) 自身剩余 HP 上限（「remaining HP is 50 or less」）
    m = re.search(r"if this pok[eé]mon'?s remaining hp is (\d+) or less", seg)
    if m:
        return f"own_active_hp_at_most:{int(m.group(1))}"

    # 22) 自身战斗位某色能量下限（「has 2 or more {G} Energy attached」）
    m = re.search(r"if this pok[eé]mon has (\d+) or more \{([a-z])\} energy attached",
                  seg)
    if m:
        color = _EN_SYMBOL_COLOR.get(m.group(2).upper())
        return f"own_active_energy_type_at_least:{color}:{int(m.group(1))}" if color else None

    # 23) 我方全场某色能量下限（「you have at least 4 {L} Energy in play」）
    m = re.search(r"if you have at least (\d+) \{([a-z])\} energy in play", seg)
    if m:
        color = _EN_SYMBOL_COLOR.get(m.group(2).upper())
        return f"own_all_energy_type_at_least:{color}:{int(m.group(1))}" if color else None

    # 24) 对手战斗位有某属性抗性（「has {F} Resistance」）
    m = re.search(opp_active + r" has \{([a-z])\} resistance", seg)
    if m:
        color = _EN_SYMBOL_COLOR.get(m.group(1).upper())
        return f"opp_active_resistance:{color}" if color else None

    # 25) 我方后备有指定名字的宝可梦。两种写法：
    #     「If Illumise is on your Bench」/「If a Pokémon that has "Nidoking" in its
    #     name is on your Bench」。
    #     ⚠ 多名字并列（「If Beldum and Metang are on your Bench」）是**合取**，
    #       单条件表达不了 → 拒绝（否则会变成「有其一就加伤」）。
    if re.search(r" and [a-z][a-z'’ ]{2,20} (?:is|are) on your bench", seg):
        return None
    m = re.search(r"if a pok[eé]mon that has ['\"]?([a-z][a-z' ]{2,20}?)['\"]? in its "
                  r"name is on your bench", seg)
    if not m:
        m = re.search(r"if ([a-z][a-z' ]{2,20}?) is on your bench", seg)
    if m:
        key = re.sub(r"[^a-z0-9]", "", m.group(1).lower())
        return f"own_bench_name:{key}" if key else None

    # ---- 第六批（2026-10-28）：Tera / 同属性 / 特殊能量 / 分类在场 ----
    # 26) 「If your opponent's Active Pokémon is a Tera Pokémon」（Regigigas）
    if re.search(opp_active + r" is a tera pok[eé]mon", seg):
        return "opp_active_tera"
    # 27) 「If you have any Tera Pokémon on your Bench」（Ho-Oh）
    if re.search(r"if you have any tera pok[eé]mon on your bench", seg):
        return "own_bench_tera"
    # 28) 双方存在同属性宝可梦（Enamorus）
    if re.search(r"if any of your pok[eé]mon in play are the same type as any of "
                 r"your opponent'?s pok[eé]mon in play", seg):
        return "same_type_in_play"
    # 29) 「If this Pokémon has any Special Energy attached」（Stoutland）
    if re.search(r"if this pok[eé]mon has any special energy attached", seg):
        return "own_active_special_energy"
    # 30) 「If this Pokémon has any Team Rocket's Energy attached」（具名能量）
    m = re.search(r"if this pok[eé]mon has any ([a-z][a-z' ]{2,20}?) energy attached",
                  seg)
    if m:
        raw = m.group(1).lower()
        # 所有格归一：「Team Rocket's」→ 卡池 tag 的 `team_rocket`
        key = _NAMED_ENERGY_ALIASES.get(raw) or re.sub(r"[^a-z0-9]", "", raw)
        return f"own_active_energy_tag:{key}" if key else None
    # 31) 「If you have N or more Basic {R} Energy cards in your discard pile」
    m = re.search(r"if you have (\d+) or more basic \{([a-z])\} energy cards? in "
                  r"your discard pile", seg)
    if m:
        color = _EN_SYMBOL_COLOR.get(m.group(2).upper())
        return (f"own_discard_basic_energy_at_least:{color}:{int(m.group(1))}"
                if color else None)
    # 32) 「If you have any Stage 2 {D} Pokémon on your Bench」（Sableye）
    m = re.search(r"if you have any stage 2 \{([a-z])\} pok[eé]mon on your bench", seg)
    if m:
        color = _EN_SYMBOL_COLOR.get(m.group(1).upper())
        return f"own_bench_stage2_type:{color}" if color else None
    # 33) 「If <卡名> is in your discard pile」（Serperior「Rosa's Encouragement」）
    m = re.search(r"if ([a-z][a-z' ]{2,24}) is in your discard pile", seg)
    if m:
        raw_name = m.group(1).strip()
        if raw_name not in _NON_NAME_WORDS:
            key = re.sub(r"[^a-z0-9]", "", raw_name)
            return f"own_discard_has_named:{key}" if key else None

    # ---- 第七批（2026-10-28）：回合历史（引擎按回合序号记账，可精确判定）----
    # 34) 「If this Pokémon moved from your Bench to the Active Spot this turn」
    if re.search(r"if this pok[eé]mon moved from your bench to the active spot "
                 r"(?:this|during this) turn", seg):
        return "moved_to_active"
    # 35) 「If this Pokémon evolved from <X> during this turn」
    m = re.search(r"if this pok[eé]mon evolved from ([a-z][a-z' ]{2,24}?) during "
                  r"this turn", seg)
    if m:
        key = re.sub(r"[^a-z0-9]", "", m.group(1))
        return f"evolved_from:{key}" if key else None
    # 36) 「If this Pokémon evolved during this turn」
    if re.search(r"if this pok[eé]mon evolved during this turn", seg):
        return "evolved_this_turn"
    # 37) 「If this Pokémon was healed during this turn」
    if re.search(r"if this pok[eé]mon was healed (?:during this|this) turn", seg):
        return "healed_this_turn"
    # 38) 「If this Pokémon used <X> during your last turn」
    m = re.search(r"if this pok[eé]mon used ([a-z][a-z' ]{2,24}?) during your "
                  r"last turn", seg)
    if m:
        key = re.sub(r"[^a-z0-9]", "", m.group(1))
        return f"used_attack_last_turn:{key}" if key else None
    # 39) 「If 1 of your other Ancient Pokémon used an attack during your last turn」
    if re.search(r"if (?:1|one) of your other ancient pok[eé]mon used an attack "
                 r"during your last turn", seg):
        return "own_other_ancient_attacked_last_turn"
    return None


_TOOL_HOLDER_RE = re.compile(r"this card is attached to", re.I)


def _faction_key(word: str) -> str:
    """阵营限定 → 卡名前缀键（与引擎 `_name_key` 同口径）

    「N's Pokémon」的卡名键是 ``nszoroark``，故前缀为 ``ns``；
    Hop → ``hops``、Steven → ``stevens``。
    """
    return _tag_name_key(str(word or "").lower() + "s")


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

    # ---- 第一百零六批（2026-11-06）：招式费用 / 进化时状态保留 ----
    # Nighttime Mine：「Attacks used by each Tera Pokémon in play (both yours and
    # your opponent's) cost {C} more.」—— 只看攻击方是不是 Tera、不看归属。
    if re.search(r"attacks used by each tera pok[eé]mon in play \(both yours and "
                 r"your opponent'?s\) cost \{c\} more", low):
        return [act("stadium", 1, "any", {"kind": "tera_cost_up"})]
    # Dizzying Valley：「Confused Pokémon (both yours and your opponent's) don't
    # recover from that Special Condition when they evolve or devolve.」
    if re.search(r"confused pok[eé]mon \(both yours and your opponent'?s\) "
                 r"don'?t recover from that special condition when they evolve "
                 r"or devolve", low):
        return [act("stadium", None, "any", {"kind": "keep_confuse"})]

    # ---- 第九批（2026-10-28）：静态规则改写类场地 ----
    # 只认**范围唯一**的整句；阵营限定（Steven's / N's / Hop's）由卡名前缀精确判定，
    # 引擎侧与 `_name_key` 同口径（"N's Pokémon" → 前缀 "ns"）。
    # F1 Jamming Tower：道具全部失效
    if re.search(r"pok[eé]mon tools? attached to each pok[eé]mon \(both yours "
                 r"and your opponent'?s\) have no effect", low):
        return [act("stadium", None, "any", {"kind": "no_tool_effect"})]
    # F2 N's Castle：N 的宝可梦无撤退费用
    m = re.search(r"\b(steven|n|hop|marnie|erika)'?s pok[eé]mon in play \(both "
                  r"yours and your opponent'?s\) have no retreat cost", low)
    if m:
        return [act("stadium", None, "any",
                    {"kind": "no_retreat", "faction": _faction_key(m.group(1))})]
    # F3 Postwick：Hop 的宝可梦招式 +N（弱点/抗性**之前**）
    m = re.search(r"attacks used by \b(steven|n|hop|marnie|erika)'?s pok[eé]mon "
                  r"\(both yours and your opponent'?s\) do (\d+) more damage", low)
    if m:
        return [act("stadium", int(m.group(2)), "any",
                    {"kind": "attack_plus", "faction": _faction_key(m.group(1))})]
    # F4 Granite Cave：Steven 的宝可梦受招式伤害 −N（弱抗**之后**）
    m = re.search(r"\b(steven|n|hop|marnie|erika)'?s pok[eé]mon \(both yours and "
                  r"your opponent'?s\) take (\d+) less damage", low)
    if m:
        return [act("stadium", int(m.group(2)), "any",
                    {"kind": "dmg_reduce", "faction": _faction_key(m.group(1))})]
    # F5 Festival Grounds：附着了能量的宝可梦不受/解除特殊状态
    if re.search(r"each pok[eé]mon that has any energy attached \(both yours and "
                 r"your opponent'?s\) recovers from all special conditions and "
                 r"can'?t be affected by any special conditions", low):
        return [act("stadium", None, "any",
                    {"kind": "status_immune", "require": "energy"})]
    # F6 Forest of Vitality：{G} 宝可梦可在上场当回合进化（首个回合除外）
    m = re.search(r"each player'?s \{([a-z])\} pok[eé]mon can evolve into "
                  r"\{([a-z])\} pok[eé]mon during the turn they play those "
                  r"pok[eé]mon, except during their first turn", low)
    if m and m.group(1).upper() == m.group(2).upper():
        color = _EN_SYMBOL_COLOR.get(m.group(1).upper())
        if color:
            return [act("stadium", None, "any",
                        {"kind": "evolve_same_turn", "ptype": color})]
    # F7 Neutralization Zone：无规则框宝可梦不受「对手规则框宝可梦」招式伤害
    if re.search(r"prevent all damage done to pok[eé]mon that don'?t have a rule "
                 r"box \(both yours and your opponent'?s\) by attacks from the "
                 r"opponent'?s pok[eé]mon \{ex\}", low):
        return [act("stadium", None, "any",
                    {"kind": "dmg_immune", "box": "none", "src": "rulebox"})]
    # F8 Area Zero Underdepths：有 Tera 宝可梦者后备上限 8（失去 Tera 后裁到 5）
    m = re.search(r"each player who has any tera pok[eé]mon in play can have up "
                  r"to (\d+) pok[eé]mon on their bench", low)
    if m:
        trim = re.search(r"discard pok[eé]mon from their bench until they have "
                         r"(\d+) pok[eé]mon", low)
        return [act("stadium", None, "any",
                    {"kind": "bench_limit", "n": int(m.group(1)),
                     "require": "tera",
                     "trim": int(trim.group(1)) if trim else None})]
    # F9 Battle Cage：后备不受「对手招式/特性效果」放置的伤害指示物
    if re.search(r"prevent all damage counters from being placed on benched "
                 r"pok[eé]mon \(both yours and your opponent'?s\) by effects of "
                 r"attacks and abilities from the opponent'?s pok[eé]mon", low):
        return [act("stadium", None, "any", {"kind": "no_bench_counters"})]

    # C8（前置）Spikemuth Gym：检索**专名所有格**指定的宝可梦入手
    #   ⚠ 必须先于下面的「阵营限定一律放弃」守卫：那条守卫针对的是
    #   「Marnie's Pokémon 减伤」这类需要**卡名归属元数据**的修正器；而这里专名
    #   是**检索目标**，靠卡名归一化子串就能精确表达。放在守卫之后会被整卡吞掉。
    #   只认 "<Name>'s Pokémon" 且动作是「揭示后入手」——Grand Tree 的
    #   「search … Stage 1 … to evolve it」不匹配（那是进化，不是入手）。
    if re.search(r"once during each player'?s turn", low):
        m = re.search(r"search their deck for a ([a-z0-9]+)'s pok[eé]mon, "
                      r"reveal it, and put it into their hand", low)
        if m:
            return [act("stadium", 1, "any",
                        {"kind": "use", "use_kind": "search_name",
                         "name_key": m.group(1)})]

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
        # C4 Surfing Beach：己方战斗位 ↔ 后备**同属性**换位（要选目标）
        m = re.search(r"switch their active \{([a-z])\} pok[eé]mon with 1 of "
                      r"their benched \{([a-z])\} pok[eé]mon", low)
        if m and m.group(1) == m.group(2):
            color = _EN_SYMBOL_COLOR.get(m.group(1).upper())
            if color:
                return [act("stadium", None, "any",
                            {"kind": "use", "use_kind": "switch_typed",
                             "ptype": color})]
        # C5 Mystery Garden：弃 1 张手牌能量 → 抽到「手牌数 = 该属性在场数」
        m = re.search(r"discard an energy card from their hand in order to draw "
                      r"cards until they have as many cards in their hand as "
                      r"they have \{([a-z])\} pok[eé]mon in play", low)
        if m:
            color = _EN_SYMBOL_COLOR.get(m.group(1).upper())
            if color:
                return [act("stadium", 1, "any",
                            {"kind": "use", "use_kind": "draw_to_count",
                             "ptype": color, "cost": "discard_energy"})]
        # C6 Lumiose City：检索基础宝可梦直接铺后备，**然后回合结束**
        if re.search(r"search their deck for a basic pok[eé]mon and put it onto "
                     r"their bench", low) and re.search(r"their turn ends", low):
            return [act("stadium", 1, "any",
                        {"kind": "use", "use_kind": "search_basic_bench",
                         "end_turn": True})]
        # C7 Team Rocket's Factory：本回合打出过**名字含 X 的**支援者 → 抽 n
        m = re.search(r"if they played a supporter card that has \"([^\"]{1,40})\" "
                      r"in its name from their hand this turn, "
                      r"they may draw (\d+) cards?", low)
        if m:
            key = _tag_name_key(m.group(1))
            if key:
                return [act("stadium", int(m.group(2)), "any",
                            {"kind": "use", "use_kind": "draw",
                             "require": "supporter_name", "name_key": key})]
        # C9 Grand Tree：检索 Stage 1 直接进化，并可**链式**再进化到 Stage 2
        if re.search(r"search their deck for a stage 1 pok[eé]mon that evolves "
                     r"from 1 of their basic pok[eé]mon and put it onto that "
                     r"pok[eé]mon to evolve it", low):
            chain = bool(re.search(r"if that pok[eé]mon was evolved in this way, "
                                   r"that player may search their deck for a "
                                   r"stage 2 pok[eé]mon", low))
            return [act("stadium", None, "any",
                        {"kind": "use", "use_kind": "evolve_from_deck",
                         "chain": chain})]
        # C8 Spikemuth Gym：检索专名指定的宝可梦入手 —— 已在函数开头前置判定
        #   （必须早于阵营豁免守卫，见上方注释）
        # 其余「每回合一次」形态（进化检索/多步代价等）需要目标选择或额外代价，
        # 本轮不建模 —— 让整卡落进未建模，而不是硬套成上面几种。
        return []

    # B 触发式（S3 第三步 2026-10-27）
    # B1 Perilous Jungle：checkup 时中毒的非 <属性> 宝可梦**额外**受 n 点
    m = re.search(r"during pok[eé]mon checkup, put (\d+) more damage counters? on "
                  r"each poisoned non-\{([a-z])\}", low)
    if m:
        color = _EN_SYMBOL_COLOR.get(m.group(2).upper())
        if color:
            return [act("stadium", int(m.group(1)) * 10, "any",
                        {"kind": "poison_extra", "ptype": color})]

    # B2 Risky Ruins：基础（非 <属性>）宝可梦被放到后备时放 n 个指示物
    m = re.search(r"whenever any player puts a basic non-\{([a-z])\} pok[eé]mon "
                  r"onto their bench[^.]*?place (\d+) damage counters?", low)
    if m:
        color = _EN_SYMBOL_COLOR.get(m.group(1).upper())
        if color:
            return [act("stadium", int(m.group(2)) * 10, "any",
                        {"kind": "on_bench_basic", "ptype": color})]

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


_FOSSIL_RE = re.compile(
    r"^play this card as if it were a (\d+)-hp basic \{([a-z])\} pok[eé]mon\.\s*"
    r"this card can't be affected by any special conditions and can't retreat\.\s*"
    r"at any time during your turn, you may discard this card from play\.?$")


def _detect_fossil(text: str) -> list:
    """化石卡（Fossil）识别 → ``fossil:<hp>:<ptype>`` 动作（2026-10-28）

    卡面（5 张 Antique … Fossil 完全一致的两句）：
      ``Play this card as if it were a N-HP Basic {C} Pokémon. This card can't be
      affected by any Special Conditions and can't retreat. At any time during your
      turn, you may discard this card from play.``

    **必须整段认领**：这段文本一旦落到通用解析，只会得到一条「静态未建模」的
    rule，而卡池会把化石当普通物品收进来 —— 卡永远不会作为宝可梦上场，属于
    「静默错算」。第二句（弃置）与第一句是同一个机制（引擎用 `discard_fossil`
    动作表达），因此两句都必须在，且不得有第三句。
    """
    t = re.sub(r"\s+", " ", _norm_apostrophe(text or "")).strip().lower()
    m = _FOSSIL_RE.match(t)
    if not m:
        return []
    color = _EN_SYMBOL_COLOR.get(m.group(2).upper())
    if not color:
        return []
    hp = int(m.group(1))
    if hp <= 0:
        return []
    return [act("rule", hp, "self", {"fossil": True, "hp": hp, "ptype": color})]


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
    holder_faction = None
    mh = re.search(r"the\s+\{([a-z])\}\s*pok[eé]mon this card is attached to", low)
    if mh:
        holder_color = _EN_SYMBOL_COLOR.get(mh.group(1).upper())
        if not holder_color:
            return []
    else:
        # 阵营限定（`the Team Rocket's Pokémon this card is attached to`）：
        # 引擎 `_tool_meta` 早就有 `holder:faction:<key>`（`_qual_ok` 按卡名
        # 归一化前缀判定），只是解析侧一直整条拒收 → 第一百零一批起放行。
        _mf = re.search(r"the\s+(?!retreat cost)(?!cost)(?P<fac>[a-z][a-z' ]{2,24}?)"
                        r"\s*pok[eé]mon this card is attached to", low)
        if _mf:
            _fk = _tag_name_key(_mf.group("fac"))
            # "team rocket's" → "teamrockets" → 去掉所有格尾 s
            holder_faction = _fk[:-1] if _fk.endswith("s") else _fk
        elif re.search(r"the\s+(?!retreat cost)(?!cost)[a-z][a-z' ]{2,24}"
                       r"pok[eé]mon this card is attached to", low):
            return []
        else:
            holder_faction = None

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

    # B4 受伤时让**攻击方**陷入状态（第一百零一批，Team Rocket's Hypnotizer）
    _ms = re.search(r"the attacking pok[eé]mon is now "
                    r"(asleep|poisoned|burned|confused|paralyzed)", low)
    if _ms:
        _st = _STATUS_WORD_TO_TAG.get(_ms.group(1))
        if not _st:
            return []
        return [act("tool", None, "self",
                    {"kind": "on_damaged_status", "status": _st,
                     "holder_faction": holder_faction})]

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
    # ---- 静态加伤（2026-10-28 第十三批）----
    #   · Maximum Belt「Attacks used by the Pokémon this card is attached to do
    #     50 more damage to your opponent's Active Pokémon {ex}」
    #   · Light Ball「… the **Pikachu {ex}** this card is attached to …」
    #   · Binding Mochi「… the **Poisoned** Pokémon this card is attached to …」
    #   · Brave Bangle「If the Pokémon this card is attached to **doesn't have a
    #     Rule Box**, the attacks it uses do 30 more damage … {ex}」
    # 加伤发生在弱点/抗性**之前**；受击方 {ex} 限定必须写进 tag（target:ex），
    # 否则会变成对任意目标都 +N（静默加强）。
    _dp_tail = (r" do (?P<dmg>\d+) more damage to your opponent'?s active "
                r"pok[eé]mon(?P<ex> \{ex\})?\s*"
                r"\(before applying weakness and resistance\)\.")
    _m = re.search(r"^attacks used by the pok[eé]mon this card is attached to"
                   + _dp_tail, low)
    if _m:
        return [act("tool", int(_m.group("dmg")), "self",
                    {"kind": "dmg_plus",
                     "tail": ":target:ex" if _m.group("ex") else ""})]
    _m = re.search(r"^attacks used by the (?P<holder>[a-z][a-z' ]{2,24}?) "
                   r"pok[eé]mon this card is attached to" + _dp_tail, low)
    if _m:
        holder = _m.group("holder").strip()
        if holder != "poisoned":
            return []            # 其它持有者限定（卡名/阵营）暂不支持
        tail = ":holder:status:poison"
        if _m.group("ex"):
            tail += ":target:ex"
        return [act("tool", int(_m.group("dmg")), "self",
                    {"kind": "dmg_plus", "tail": tail})]
    _m = re.search(r"^attacks used by the (?P<name>[a-z][a-z' ]{1,24}?) \{ex\} "
                   r"this card is attached to" + _dp_tail, low)
    if _m:
        tail = f":holder:name:{_k(_m.group('name'))}"
        if _m.group("ex"):
            tail += ":target:ex"
        return [act("tool", int(_m.group("dmg")), "self",
                    {"kind": "dmg_plus", "tail": tail})]
    _m = re.search(r"^if the pok[eé]mon this card is attached to doesn'?t have a "
                   r"rule box, the attacks it uses do (?P<dmg>\d+) more damage to "
                   r"your opponent'?s active pok[eé]mon(?P<ex> \{ex\})?\s*"
                   r"\(before applying weakness and resistance\)\.", low)
    if _m:
        tail = ":holder:no_rule_box"
        if _m.group("ex"):
            tail += ":target:ex"
        return [act("tool", int(_m.group("dmg")), "self",
                    {"kind": "dmg_plus", "tail": tail})]
    # 道具 +HP：「The Pokémon this card is attached to gets +N HP」→ `tool:hp_plus:N`
    # ⚠ 必须用 `out.append`、**不能 return**：带**卡名/阵营限定**的写法
    # （「The Cynthia's / Team Rocket's Pokémon … gets +N HP」）只能给**该阵营**
    # 的持有者加血，而引擎 `_tool_value()` 无持有者归属元数据 → 落
    # `tool:hp_plus:N` 会给**任意**持有者加血（静默加强）。交给下方
    # 「卡名/阵营限定 → 整卡放弃」守卫如实拒收，才是唯一正确的处置。
    m = re.search(r"this card is attached to gets \+(\d+)\s*hp", low)
    if m:
        out.append(act("tool", int(m.group(1)), "self", {"kind": "hp_plus"}))
    # ---- S6 费用 / 撤退修正（2026-10-28）----
    # 这四张都带条件从句，必须**先于**下面的「有条件就整卡放弃」守卫识别。
    # · Sparkling Crystal：Tera 持有者招式费用 −N（Tera 是**类别**，引擎可判定）
    m = re.search(r"when the tera pok[eé]mon this card is attached to uses an "
                  r"attack, that attack costs (\d+) energy less", low)
    if m:
        return [act("tool", int(m.group(1)), "self",
                    {"kind": "cost_less", "cond": "tera"})]
    # · Counter Gain：我方剩余奖赏卡多于对手时费用 −1
    if re.search(r"if you have more prize cards remaining than your opponent, "
                 r"attacks used by the pok[eé]mon this card is attached to cost "
                 r"(?:\{c\}\s*)+less", low):
        return [act("tool", 1, "self",
                    {"kind": "cost_less", "cond": "behind"})]
    # · Gravity Gemstone：附着者在战斗位时，**双方战斗位**撤退费用 +N
    if re.search(r"as long as the pok[eé]mon this card is attached to is in the "
                 r"active spot, the retreat cost of both active pok[eé]mon is "
                 r"(?:\{c\}\s*)+more", low):
        return [act("tool", 1, "self",
                    {"kind": "retreat_plus", "scope": "both"})]
    # · Rescue Board：撤退 −N；剩余 HP ≤M 时**无撤退费用**（引擎按 HP 判定）
    if re.search(r"if that pok[eé]mon'?s remaining hp is \d+ or less, it has no "
                 r"retreat cost", low):
        m2 = re.search(r"retreat cost of the pok[eé]mon this card is attached to "
                       r"is ((?:\{\s*[a-z]\s*\}\s*)+)less", low)
        hp = re.search(r"remaining hp is (\d+) or less", low)
        if m2 and hp:
            n = len(re.findall(r"\{[a-z]\}", m2.group(1)))
            return [act("tool", n, "self",
                        {"kind": "retreat_less", "low_hp": int(hp.group(1))})]
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


# 招式击倒（2026-10-28 第九批）：**按目标范围**区分，顺序由具体到宽泛。
#   卡面原型：
#     Knock Out your opponent's Active Pokémon.              → opp_active
#     Knock Out your opponent's Active Basic Pokémon.        → opp_active:basic
#     Knock Out 1 of your opponent's Benched Basic Pokémon.  → opp_bench:basic
#     Knock Out 1 of your opponent's Pokémon that has
#       exactly 6 damage counters on it.                     → opp_counters:6
#     Knock Out 1 of your opponent's Pokémon.                → opp_any
#     Both Active Pokémon are Knocked Out.                   → both_active
#   其它写法（「if … is Knocked Out」触发从句、「Knock Out each …」）一律不认。
_KO_PATTERNS = (
    (r"both active pok[eé]mon are knocked out",
     lambda m: "both_active", False),
    (r"knock out your opponent'?s active basic pok[eé]mon",
     lambda m: "opp_active:basic", False),
    (r"knock out your opponent'?s active pok[eé]mon",
     lambda m: "opp_active", False),
    (r"knock out 1 of your opponent'?s benched basic pok[eé]mon",
     lambda m: "opp_bench:basic", False),
    (r"knock out 1 of your opponent'?s benched pok[eé]mon",
     lambda m: "opp_bench", False),
    (r"knock out 1 of your opponent'?s pok[eé]mon that has exactly "
     r"(\d+) damage counters? on it",
     lambda m: f"opp_counters:{int(m.group(1))}", False),
    (r"knock out 1 of your opponent'?s pok[eé]mon",
     lambda m: "opp_any", False),
    # 条件击倒（Haxorus「Axe Blast」）：「If your opponent's Active Pokémon is a
    # Basic Pokémon, it is Knocked Out.」—— 只在对手战斗位是**基础**时击倒，
    # 引擎 `knockout:opp_active:basic` 已支持该分支（无条件时会静默多击倒）。
    (r"if your opponent'?s active pok[eé]mon is a basic pok[eé]mon, "
     r"it is knocked out",
     lambda m: "opp_active:basic", False),
    # ---- 第五十五批（2026-11-04）：击倒族补齐三个限定 ----
    # 「If your opponent's Active Pokémon has exactly 6 damage counters on it,
    # that Pokémon is Knocked Out.」（Mega Absol ex「Terminal Period」）
    # ⚠ 指示物数 ×10 = 伤害值；引擎按 `fp.damage == n*10` 判，故这里传**个数**。
    (r"if your opponent'?s active pok[eé]mon has exactly (\d+) damage counters? "
     r"on it, that pok[eé]mon is knocked out",
     lambda m: f"opp_counters:{int(m.group(1))}", False),
    # 「… has any Special Energy attached, it is Knocked Out.」（Haxorus）
    (r"if your opponent'?s active pok[eé]mon has any special energy attached, "
     r"it is knocked out",
     lambda m: "opp_active:if:senergy", False),
    # 「Knock Out **each** of your opponent's Pokémon that has 50 HP or less
    # remaining.」（Yveltal ex「Soul Destroyer」）
    # ⚠ 「each」= 逐只全倒，不是「1 of」（`opp_any`）；落错会把多杀削成单杀。
    (r"knock out each of your opponent'?s pok[eé]mon that has (\d+) hp or less "
     r"remaining",
     lambda m: f"opp_all:if:hp_le:{int(m.group(1))}", False),
    # ---- 第八十六批（2026-11-05）：「least HP remaining」确定性击倒 ----
    # 「Choose a Pokémon in play (yours or your opponent's) that has the least HP
    # remaining, except for this Pokémon, and it is Knocked Out.」（Inteleon
    # 「Bring Down」）。引擎 `knockout:least_hp` 遍历双方场上、排除「this
    # Pokémon」、选剩余 HP 最小者击倒（确定性默认）。
    (r"choose a pok[eé]mon in play \(yours or your opponent'?s\) that has the "
     r"least hp remaining, except for this pok[eé]mon, and it is knocked out",
     lambda m: "least_hp", False),

    # 被动语态：「… your opponent's Active Pokémon is Knocked Out.」
    # ⚠ **必须**落在抛币分支里（Iono's Electrode「Flip a coin. If heads, …」）；
    # 否则它多半是**触发从句**（Togekiss「When your opponent's Active Pokémon is
    # Knocked Out, …」），把它当成主动击倒会凭空多击倒一只。
    (r"your opponent'?s active pok[eé]mon is knocked out",
     lambda m: "opp_active", True),
)


def _coin_branch_info(text: str, pos: int):
    """位置 ``pos`` 所属的抛币分支 → ``{"side","mode","n"}``；不在分支内返回 ``None``

    ``mode``：``single``（Flip a coin / 每面一次）或 ``all``（Flip N coins 全部命中）。
    """
    head = text[:pos]
    last = None
    for m in re.finditer(
            r"if\s+(?:both|all)(?:\s+of\s+them\s+are)?\s+(heads|tails)\b"
            r"|if\s+(heads|tails)\b", head, re.I):
        last = m
    if last is None or "." in head[last.end():]:
        return None
    side = (last.group(1) or last.group(2)).lower()
    is_all = bool(re.search(r"both|all", last.group(0), re.I))
    n = None
    if is_all:
        mm = re.search(r"flip (\d+) coins?\.\s*$", head[:last.start()], re.I)
        n = int(mm.group(1)) if mm else 2
    return {"side": side, "mode": "all" if is_all else "single", "n": n}


def _detect_knockout(text: str, kind: str) -> list:
    """招式击倒 → ``knockout:<scope>``（或抛币子 tag ``coin…:knockout:<scope>``）

    只认 `_KO_PATTERNS` 里的**主动击倒**句式；触发从句（「is Knocked Out」）与
    不可精确表达的形态一律不产出（宁可未建模）。
    """
    if kind != "attack":
        return []
    t = re.sub(r"\s+", " ", _norm_apostrophe(text or "")).strip()
    low = t.lower()
    out: list = []
    spans: list = []
    for pat, fmt, need_branch in _KO_PATTERNS:
        m = re.search(pat, low)
        if not m:
            continue
        if any(a <= m.start() < b for a, b in spans):
            continue                     # 更具体的句式已认领
        info = _coin_branch_info(t, m.start())
        if need_branch and info is None:
            continue                     # 多半是触发从句 → 不是主动击倒
        scope = fmt(m)
        spans.append(m.span())
        if info is None:
            out.append(act("knockout", None, "active_opponent",
                           {"scope": scope}))
        else:
            extra = {"side": info["side"], "mode": info["mode"],
                     "sub": f"knockout:{scope}"}
            if info["n"]:
                extra["counter"] = str(info["n"])
            out.append(act("coin_flip", None, "active_opponent", extra))
    return out


# ---------------- 被击倒触发 / 进化触发（2026-10-28 第十批） ----------------
_EVOLVE_TRIGGER_RE = re.compile(
    r"when you play this pok[eé]mon from your hand to evolve 1 of your pok[eé]mon",
    re.I)


# ---------------- 跨回合伤害修正（2026-10-29 第十六批） ----------------
#   · 「During your next turn, the Defending Pokémon takes N more damage from
#     attacks (**after** applying Weakness and Resistance).」   Vibrava / Stunfisk
#   · 「During your opponent's next turn, attacks used by the Defending Pokémon
#     do N less damage (**before** applying Weakness and Resistance).」
#     Hop's Rookidee / Sylveon ex / Buneary / Pawmi / Chikorita
#   · 「During your next turn, this Pokémon's <招式> attack does N more damage
#     (**before** applying Weakness and Resistance).」          Meloetta ex / Pecharunt
_NEXT_DEFENDER_PLUS_RE = re.compile(
    r"during your next turn, the defending pok[eé]mon takes (\d+) more damage "
    r"from attacks \(after applying weakness and resistance\)\.", re.I)
_NEXT_DEFENDER_MINUS_RE = re.compile(
    r"during your opponent'?s next turn, attacks used by the defending "
    r"pok[eé]mon do (\d+) less damage \(before applying weakness and "
    r"resistance\)\.", re.I)
_NEXT_SELF_PLUS_RE = re.compile(
    r"during your next turn, this pok[eé]mon'?s ([a-z][a-z'\- ]{1,30}?) attack "
    r"does (\d+) more damage \(before applying weakness and resistance\)\.", re.I)


def _detect_next_turn_dmg(text: str, kind: str) -> tuple:
    """跨回合伤害修正（招式侧，可与其他子句共存 → 逐个 search 而非整段 match）

    三类必须严格区分**作用对象**与**结算顺序**，否则会静默错算：
      · 受击方 +N（**弱抗之后**）      → ``dmg_taken_plus_next:N``
      · 受击方招式 -N（**弱抗之前**）  → ``atk_minus_next:N``
      · 自身具名招式 +N（**弱抗之前**）→ ``self_attack_plus_next:N:name:<key>``

    返回 ``(actions, 去掉已认领子句后的文本)``：已认领的句子必须从后续扫描中
    **移除**，否则「do N less damage」「does N more damage」还会被通用伤害缩放
    分支再解析一次，产出假的未建模项（实测：5 张卡因此整卡收不进池）。
    """
    if kind != "attack":
        # 只收招式侧：特性侧没有对应派发点（若产出 tag，准入层会因前缀不在
        # 特性白名单而整卡拒绝，但解析层会报"无未建模项" → 两份真相）
        return [], text
    t = re.sub(r"\s+", " ", _norm_apostrophe(text or "")).strip()
    out, spans = [], []
    for rx, mk in ((_NEXT_DEFENDER_PLUS_RE,
                    lambda m: act("dmg_taken_plus_next", int(m.group(1)),
                                  "active_opponent")),
                   (_NEXT_DEFENDER_MINUS_RE,
                    lambda m: act("atk_minus_next", int(m.group(1)),
                                  "active_opponent")),
                   (_NEXT_SELF_PLUS_RE,
                    lambda m: act("self_attack_plus_next", int(m.group(2)),
                                  "self", {"attack_key":
                                           m.group(1).replace(":", " ")
                                           .strip().lower()}))):
        m = rx.search(t)
        if m:
            out.append(mk(m))
            spans.append((m.start(), m.end()))
    if not spans:
        return [], text
    for s, e in sorted(spans, reverse=True):
        t = (t[:s] + " " + t[e:]).strip()
    return out, t


# ---------------- Pokémon Checkup 改写（2026-10-28 第十三批） ----------------
_CHECKUP_PLUS_RE = re.compile(
    r"^(?:during pok[eé]mon checkup, )?"
    r"(?:as long as this pok[eé]mon is in the active spot, )?"
    r"put (\d+) more damage counters? on your opponent'?s "
    r"(poisoned|burned) pok[eé]mon"
    r"(?: during pok[eé]mon checkup)?\.$", re.I)

# 招式侧改写：「During Pokémon Checkup, put 8 damage counters on that Pokémon
# instead of 1.」（Team Rocket's Nidoking ex「Tainted Horn」）
_CHECKUP_SET_RE = re.compile(
    r"during pok[eé]mon checkup, put (\d+) damage counters? on that "
    r"pok[eé]mon instead of 1", re.I)

# Checkup **群体**放置（2026-10-28 第十四批）：
#   · Froslass「During Pokémon Checkup, put 1 damage counter on each Pokémon that
#     has an Ability (both yours and your opponent's), except any Froslass.」
#   · Team Rocket's Tyranitar「During Pokémon Checkup, if this Pokémon is in the
#     Active Spot, put 2 damage counters on each of your opponent's Basic Pokémon.」
_CHECKUP_SPREAD_ABILITY_RE = re.compile(
    r"^during pok[eé]mon checkup, put (\d+) damage counters? on each "
    r"pok[eé]mon that has an ability \(both yours and your opponent'?s\), "
    r"except any ([a-z][a-z' ]{2,24})\.?$", re.I)
_CHECKUP_SPREAD_OPP_BASIC_RE = re.compile(
    r"^during pok[eé]mon checkup, (if this pok[eé]mon is in the active spot, )?"
    r"put (\d+) damage counters? on each of your opponent'?s basic "
    r"pok[eé]mon\.?$", re.I)


def _detect_checkup_spread(text: str, kind: str) -> list:
    """Checkup **群体**指示物放置（特性侧）

    → ``passive:checkup_spread:<dmg>:<scope>[:except:<key>][:active_only]``

    只认上述两种**整句**形态（Froslass / Team Rocket's Tyranitar）；其它
    「each …」写法（我方 / 带计数源 / 指定 N 只）一律不认。
    """
    if kind != "ability":
        return []
    t = re.sub(r"\s+", " ", _norm_apostrophe(text or "")).strip()
    m = _CHECKUP_SPREAD_ABILITY_RE.match(t)
    if m:
        return [act("passive", int(m.group(1)) * 10, "self",
                    {"kind": "checkup_spread", "scope": "ability_all",
                     "exclude": _k(m.group(2))})]
    m = _CHECKUP_SPREAD_OPP_BASIC_RE.match(t)
    if m:
        return [act("passive", int(m.group(2)) * 10, "self",
                    {"kind": "checkup_spread", "scope": "opp_basic",
                     "active_only": bool(m.group(1))})]
    return []


_ONCE_TURN_COUNTER_HEAD = re.compile(
    r"^once during your turn, you may$", re.I)
_EVOLVE_COUNTER_HEAD = re.compile(
    r"^when you play this pok[eé]mon from your hand to evolve 1 of your "
    r"pok[eé]mon during your turn, you may$", re.I)


def _detect_counters_on_active(text: str, kind: str) -> list:
    """「Place N damage counters on …」→ ``counters:N[:per:<c>][:scope:opp[:1]][:selfko]``

    ⚠ 与普通伤害不同：指示物**不吃弱点/抗性**，所以不能复用 `damage` 字段。
    覆盖两种目标口径：

      · ``your opponent's Active Pokémon``（Haunter / Alakazam）→ 战斗位
      · ``1 of your opponent's Pokémon``（Bramblin / Swirlix / Spectrier /
        Dusclops / Dusknoir / Golbat · 第二十批）→ 对手**任意** 1 只（含战斗位）

    只认**无条件**形态 + 可选「for each <计数源>」；带 if/when/任意分配/取代/
    后续动作（`Then, …`）的一律不认。特性侧只认两种固定前缀（每回合一次 /
    进化触发），自毁句「If you use this Ability, this Pokémon is Knocked Out.」
    落 ``:selfko``。
    """
    if kind not in ("attack", "ability"):
        return []
    t = re.sub(r"\s+", " ", _norm_apostrophe(text or "")).strip()
    low = t.lower()
    # 同一段文本里出现**多处**放置指示物 → 只建模一处会静默丢效果，整体不认
    if len(re.findall(r"(?:put|place) \d+ damage counters?", low)) > 1:
        return []
    # 「choose N of your opponent's Pokémon and put M damage counters on each of
    #  them」（Team Rocket's Crobat ex「Biting Spree」，第九十七批）
    # 引擎 `counters:M:scope:opp:N` 正是这个语义（选 N 只、每只各 M 个），
    # 且显式选择走 `action["snipe"]`（位置码），所以玩家仍能挑目标。
    # ⚠ 必须排在「on 1 of your opponent's Pokémon」之前：那条会把
    #   「on each of them」当成本句无目标而退回未建模。
    m = re.search(r"choose (\d+) of your opponent'?s? pok[eé]mon and "
                  r"(?:put|place) (\d+) damage counters? on each of them", low)
    if m:
        head = low[:m.start()].strip()
        if re.search(r"\bif\b|in any way|as you like|instead|then\b", head):
            return []
        if kind == "ability" and not (_ONCE_TURN_COUNTER_HEAD.match(head)
                                      or _EVOLVE_COUNTER_HEAD.match(head)):
            return []
        if kind == "attack" and re.search(r"\bif\b|\bwhen\b|\bonce\b", head):
            return []
        return [act("counters", int(m.group(2)), "active_opponent_bench",
                    {"counters_scope": "opp",
                     "counters_count": int(m.group(1))})]
    # 「If <cond>, place N damage counters on 1 of your opponent's **Benched**
    #  Pokémon.」（第九十五批，Mismagius「Assassin's Magic」）
    # 条件可由引擎 `_eval_condition` 精确求值 → `:gate:<cond>`；
    # 目标是**后备** → `:scope:opp_bench`（不能用 `opp`，那会先打战斗位）。
    m = re.fullmatch(r"if (.+?), (?:put|place) (\d+) damage counters? on 1 of "
                     r"your opponent'?s? benched pok[eé]mon\.?", low.strip())
    if m:
        cond = _condition_from_text(m.group(1))
        if not cond:
            return []
        return [act("counters", int(m.group(2)), "active_opponent_bench",
                    {"counters_scope": "opp_bench", "counters_count": 1,
                     "gate": cond})]
    m = re.search(r"(?:put|place) (\d+) damage counters? on 1 of your opponent'?s? "
                  r"pok[eé]mon([^.;,()]{0,60})", low)
    if m:
        tail, head = m.group(2), low[:m.start()].strip()
        # 「Then, shuffle those Energy cards into your deck」（Sinistcha ex）——
        # 这一句**可以**与指示物同卡：洗回由 `energy_to_deck:` 承载，属已建模。
        _shuffle_back = bool(re.search(
            r"then,?\s+shuffle those energy cards? into your deck", low))
        if re.search(r"in any way|as you like|instead|choose", tail) \
                or re.search(r"\band \d+|benched", tail) \
                or re.search(r"then\b", tail) \
                or (re.search(r"then,?\s+shuffle", low) and not _shuffle_back):
            return []
        if kind == "ability":
            # 只认两种触发前缀（其余「if / as long as」一律不认）
            if not (_ONCE_TURN_COUNTER_HEAD.match(head)
                    or _EVOLVE_COUNTER_HEAD.match(head)):
                return []
        elif re.search(r"\bif\b|\bwhen\b|\bonce\b|checkup|as long as", head):
            return []
        per = None
        if re.search(r"for each", tail):
            per = _counter_from_phrase(tail)
            if not per:
                return []
        # 「洗回牌库」只对「弃牌堆里的基本能量」计数成立（those Energy cards）
        if _shuffle_back and not str(per or "").startswith("own_discard_energy"):
            return []
        extra = {"counters_scope": "opp", "counters_count": 1}
        if per:
            extra["per_counter"] = per
        if kind == "ability" and re.search(
                r"if you use this ability, this pok[eé]mon is knocked out", low):
            extra["selfko"] = True
        return [act("counters", int(m.group(1)), "active_opponent_bench", extra)]
    # 「on **each Pokémon that has an Ability** (both yours and your opponent's)」
    # （Cofagrigus「Law of the Underworld」，2026-10-29 第二十八批）→ 双方逐个
    m = re.search(r"(?:put|place) (\d+) damage counters? on each pok[eé]mon that "
                  r"has an ability", low)
    if m:
        head = low[:m.start()].strip()
        if re.search(r"\bif\b|\bwhen\b|choose|instead|in any way", head + low):
            return []
        return [act("counters", int(m.group(1)), "any",
                    {"counters_scope": "ability", "counters_count": "all"})]
    # 「on **each** of your opponent's Pokémon」（Uxie）→ `:scope:opp:all`
    # 同样是指示物直放：落 `snipe:opp:…:all` 会让**战斗位**吃弱点/抗性（静默多算）。
    m = re.search(r"(?:put|place) (\d+) damage counters? on each of your "
                  r"opponent'?s? pok[eé]mon([^.;,()]{0,60})", low)
    if m:
        tail, head = m.group(2), low[:m.start()].strip()
        if re.search(r"in any way|as you like|instead|then\b|choose|benched", tail) \
                or re.search(r"then,?\s+shuffle", low):
            return []
        if kind == "ability":
            if not (_ONCE_TURN_COUNTER_HEAD.match(head)
                    or _EVOLVE_COUNTER_HEAD.match(head)):
                return []
        elif re.search(r"\bif\b|\bwhen\b|\bonce\b|checkup|as long as", head):
            return []
        per = None
        if re.search(r"for each", tail):
            per = _counter_from_phrase(tail)
            if not per:
                return []
        extra = {"counters_scope": "opp", "counters_count": "all"}
        if per:
            extra["per_counter"] = per
        return [act("counters", int(m.group(1)), "active_opponent_bench", extra)]
    # 「on **your opponent's Active** / **the Defending** Pokémon」（Haunter /
    # Alakazam / Glaceon「Permeating Chill」）→ 对手战斗位（攻击语境下 Defending
    # = 对手战斗位，与 `on your opponent's Active` 同口径；指示物不吃弱抗）。
    # ⚠ 时序「At the end of your opponent's next turn」只是**延迟**，引擎无递延
    # 指示物机制 —— 按解析侧一贯的「即时结算」约定落 `counters:N`（已在对账中标注）。
    m = re.search(r"(?:put|place) (\d+) damage counters? on (?:your opponent'?s "
                  r"active|the defending) pok[eé]mon([^.;,()]{0,48})", low)
    if m:
        tail = m.group(2)
        head = low[:m.start()]
        if re.search(r"\bif\b|\bwhen\b", head) \
                or re.search(r"in any way|as you like|choose|instead", tail):
            return []
        per = None
        if re.search(r"for each", tail):
            per = _counter_from_phrase(tail)
            if not per:
                return []
        return [act("counters", int(m.group(1)), "active_opponent",
                    {"per_counter": per} if per else {})]
    # 「on **this** Pokémon」→ 己方战斗位直放指示物（引擎 `:scope:self`）。
    # Feraligatr「Torrential Heart」· Team Rocket's Venture Bomb 等。
    # 与对手分支同口径：仅认无条件 / 可选「for each <计数源>」；带 if/when/任意
    # 分配/取代/后续动作一律不认。特性侧只认两种固定前缀（每回合一次 / 进化触发）。
    m = re.search(r"(?:put|place) (\d+) damage counters? on this pok[eé]mon"
                  r"([^.;,()]{0,60})", low)
    if m:
        tail, head = m.group(2), low[:m.start()].strip()
        if re.search(r"in any way|as you like|choose|instead", tail) \
                or re.search(r"\bif\b|\bwhen\b|as long as", head):
            return []
        if kind == "ability":
            if not (_ONCE_TURN_COUNTER_HEAD.match(head)
                    or _EVOLVE_COUNTER_HEAD.match(head)):
                return []
        elif re.search(r"\bif\b|\bwhen\b|\bonce\b|checkup|as long as", head):
            return []
        # 后续带**未建模从句**（「If you do, during this turn … +120」/
        # 「This attack does … for each counter placed in this way」）→ 整条放弃：
        # 只映射指示物会让「部分实现冒充已实现」（Feraligatr / Walking Wake 的
        # 另一半都是 B4）。`tail` 在标点处截断，故必须看匹配之后的**整段**。
        rest = low[m.end():]
        if re.search(r"\bif you do\b|during this turn|\bthen\b|this attack does",
                     rest):
            return []
        per = None
        if re.search(r"for each", tail):
            per = _counter_from_phrase(tail)
            if not per:
                return []
        extra = {"counters_scope": "self", "counters_count": 1}
        if per:
            extra["per_counter"] = per
        return [act("counters", int(m.group(1)), "self_active", extra)]
    return []



def _detect_checkup_plus(text: str, kind: str) -> list:
    """Checkup **追加**伤害（特性侧）→ ``passive:checkup_plus:<status>:<n>[:side:opp][:active_only]``

    原型：
      · Pecharunt「As long as this Pokémon is in the Active Spot, put 5 more
        damage counters on your opponent's Poisoned Pokémon during Pokémon Checkup.」
      · Magmortar「During Pokémon Checkup, put 3 more damage counters on your
        opponent's Burned Pokémon.」

    只认「**对手的** · 已中毒/已烧伤」两条限定。其它写法（我方、每个宝可梦、
    指定计数源）一律不认 —— 整卡如实未建模。
    """
    if kind != "ability":
        return []
    t = re.sub(r"\s+", " ", _norm_apostrophe(text or "")).strip()
    m = _CHECKUP_PLUS_RE.match(t)
    if not m:
        return []
    status = {"poisoned": "poison", "burned": "burn"}[m.group(2).lower()]
    return [act("passive", int(m.group(1)) * 10, "self",
                {"kind": "checkup_plus", "status": status,
                 "side_opp": True,
                 "active_only": "as long as this pok" in t.lower()})]


def _detect_checkup_set(text: str, kind: str) -> list:
    """Checkup **改写**伤害（招式侧）→ ``checkup_set:<status>:<n>``

    原型（Nidoking ex）：「Your opponent's Active Pokémon is now Poisoned.
    During Pokémon Checkup, put 8 damage counters on that Pokémon instead of 1.」
    → 中毒状态照常由状态分支产出；本函数只产出「检查期改为 80 点」的改写。
    """
    if kind != "attack":
        return []
    t = re.sub(r"\s+", " ", _norm_apostrophe(text or "")).strip()
    m = _CHECKUP_SET_RE.search(t)
    if not m:
        return []
    return [act("checkup_set", int(m.group(1)) * 10, "active_opponent",
                {"status": "poison"})]

_EVOLVE_EACH_BENCH_RE = re.compile(
    r"^for each of your benched pok[eé]mon, search your deck for a card that "
    r"evolves from that pok[eé]mon and put it onto that pok[eé]mon to evolve it\.?"
    r"(?: then, shuffle your deck\.?)?$", re.I)


_UT_HEAD = r"flip a coin until you get tails[.] "
# 卡面在「Then, shuffle your deck.」之前可能没有句号（Gholdengo 有）——统一可选
_UT_TAIL = r"(?: ?then, shuffle your deck[.])?$"
# ① 磨对手牌库：正面数 = 张数（Tyrantrum POR 45「Wreak Havoc」）
#    ⚠ 此前通用 mill 分支只看到「discard the top card of your opponent's deck」
#    → 产出 `mill:1:opp`（恒定磨 1 张，而卡面是每正面 1 张）——**静默削弱**，
#    不报错、测试不绿也没人发现（该卡因另一条特性未建模而未进池，掩盖了它）。
_UT_MILL = re.compile(
    _UT_HEAD + r"for each heads, discard the top card of your opponent'?s deck[.]"
    + _UT_TAIL, re.I)
# ② 随机弃对手手牌进其牌库（Houndstone MEG 66「Horrifying Bite」）
_UT_SHUFFLE_OPP_HAND = re.compile(
    _UT_HEAD + r"for each heads, choose a random card from your opponent'?s hand[.] "
    r"your opponent reveals those cards and shuffles them into their deck[.]"
    + _UT_TAIL, re.I)
# ③ 任意卡检索入手（Gholdengo MEG 99「All-You-Can-Grab」）
_UT_SEARCH = re.compile(
    _UT_HEAD + r"search your deck for a number of cards up to the number of heads "
    r"and put them into your hand[.]" + _UT_TAIL, re.I)
# ④ 对手战斗位按正面数弃能（LugiaEX PRE 82「Hyper Whirlpool」）
#    ⚠ 此前落成 `discard_energy:1:opp`（**恒定弃 1 个**）——静默削弱。
_UT_DISCARD = re.compile(
    _UT_HEAD + r"for each heads, discard an energy from your opponent'?s active "
    r"pok[eé]mon[.]" + _UT_TAIL, re.I)


_SHUFFLE_OPP_POKEMON_RE = re.compile(
    r"^flip a coin\. if heads, choose 1 of your opponent'?s? (benched )?"
    r"pok[eé]mon\. shuffle that pok[eé]mon and all attached cards into their "
    r"deck\.?$", re.I)


def _detect_shuffle_opp_pokemon(text: str) -> Optional[dict]:
    """「… choose 1 of your opponent's [Benched] Pokémon. Shuffle that Pokémon and
    all attached cards into their deck.」→ ``{"shuffle_opp_pokemon": scope}``

    卡面原型（2026-11-03 第四十二批）：
      · Sylveon「… Benched Pokémon …」→ ``opp_bench``
      · Shiftry「… Pokémon …」→ ``opp``（含战斗位）

    ⚠ **整句精确匹配**（含抛币门）：这是「把对手的宝可梦连同附着卡洗回其牌库」
    而不是击倒 —— 少了「choose 1」或「all attached cards」语义就变了；
    「shuffle **it** into your deck」（洗自己的）也不得认领。
    """
    t = re.sub(r"\s+", " ", _norm_apostrophe(text or "")).strip()
    m = _SHUFFLE_OPP_POKEMON_RE.match(t)
    if not m:
        return None
    return {"shuffle_opp_pokemon": "opp_bench" if m.group(1) else "opp"}


def _detect_until_tails_effect(text: str) -> list:
    """「Flip a coin until you get tails」+ **非伤害**子效果 → ``until_tails`` 动作

    张数 = 连续正面枚数（几何分布），**结算时**才知道。解析期折算成任何常数
    （期望值 / 上限 / 1）都是静默改强度，因此整段认领、由引擎现抛。

    已实现四种子效果（卡面原型见各正则注释）；近邻写法一律不认：
      · 伤害型（`dmg_plus` / `dmg_set`）走 `_detect_coin` 的老路，不在此处重复；
      · 「flip until **2** tails」/「up to the number of heads」以外的限定
        （如「up to the number of your Benched Pokémon」）不匹配 → 如实未建模。
    """
    t = re.sub(r"\s+", " ", _norm_apostrophe(text or "")).strip()
    if not t.lower().startswith("flip a coin until you get tails"):
        return []
    if _UT_MILL.match(t):
        return [act("until_tails", None, "active_opponent",
                    {"kind": "mill", "scope": "opp"})]
    if _UT_SHUFFLE_OPP_HAND.match(t):
        return [act("until_tails", None, "active_opponent",
                    {"kind": "shuffle_opp_hand"})]
    if _UT_SEARCH.match(t):
        return [act("until_tails", None, "active_opponent",
                    {"kind": "search", "what": "any"})]
    if _UT_DISCARD.match(t):
        return [act("until_tails", None, "active_opponent",
                    {"kind": "discard_energy", "scope": "opp"})]
    return []


_EVOLVE_EACH_PICK_RE = re.compile(
    r"^choose up to (\d+) of your (.+?)\.\s*for each of those pok[eé]mon, "
    r"search your deck for a card that evolves from that pok[eé]mon and put it "
    r"onto that pok[eé]mon to evolve it[.]?(?: ?then, shuffle your deck[.])?$",
    re.I)


def _detect_evolve_each_pick(text: str, kind: str) -> list:
    """「**选**至多 N 只，各进化一次」→ ``evolve_each_pick:<max>:<scope>``

    卡面原型（Team Rocket's Nidorina DRI 115「Dark Awakening」）：
      「Choose up to 2 of your {D} Pokémon. For each of those Pokémon, search your
        deck for a card that evolves from that Pokémon and put it onto that
        Pokémon to evolve it. Then, shuffle your deck.」

    与 `evolve_each_bench` 的唯一差别是目标集合：那边是**全部后备**，这边是
    **选出**的至多 N 只（可含战斗位）。两者不可代用 —— 拿 all-bench 顶替会让
    卡面「至多 2 只」变成「全部后备」（静默加强）。
    ⚠ 通用检索分支会先看到「for each」按「数量由计数源决定」拒绝，通用进化
    分支又按「招式驱动的进化需卡级 tag」拒绝 —— 双路都会让整卡进不了池，故整段认领。
    """
    if kind not in ("attack", "ability"):
        return []
    t = re.sub(r"\s+", " ", _norm_apostrophe(text or "")).strip()
    m = _EVOLVE_EACH_PICK_RE.match(t)
    if not m:
        return []
    scope = _spread_scope_of(m.group(2))
    if scope is None:
        return []                    # 目标限定判不出 → 不认领（宁缺勿错）
    mx = int(m.group(1))
    if mx < 1:
        return []
    return [act("evolve", None, "self", {"each_pick": mx, "scope": scope})]


def _detect_evolve_each_bench(text: str, kind: str) -> list:
    """「逐只后备各进化一次」→ ``evolve_each_bench``（Reuniclus「Cellular Ascension」）

    卡面原型：
      「For each of your Benched Pokémon, search your deck for a card that evolves
        from that Pokémon and put it onto that Pokémon to evolve it.
        Then, shuffle your deck.」

    ⚠ 通用检索分支看到「for each」会按「数量由计数源决定」拒绝，进化动作又会被
    当成「招式/特性驱动的进化需卡级 tag」——两处都会让整卡进不了池。本句式是
    「每只后备各进化一次」，引擎有精确对应（`battle._evolve_each_bench`），
    因此**整段认领**；近邻写法（限定 stage/属性/单只）一律不认。
    """
    if kind not in ("attack", "ability"):
        return []
    t = re.sub(r"\s+", " ", _norm_apostrophe(text or "")).strip()
    if not _EVOLVE_EACH_BENCH_RE.match(t):
        return []
    return [act("evolve", None, "bench", {"each_bench": True})]


def _detect_evolve_heal(text: str, kind: str) -> list:
    """进化触发 + 全量回复 +「in this way」后续 → ``heal`` + ``discard`` 动作

    卡面原型（Whimsicott / Dachsbun ex）：
      「When you play this Pokémon from your hand to evolve 1 of your Pokémon
        [during your turn], you may [use this Ability.] heal all damage from
        <X>. If you healed any damage in this way, discard all Energy from <Y>.」

    只收两组可精确表达的 <X>/<Y>：
      · ``your Active {G} Pokémon`` ↔ ``that Pokémon``（= 该战斗位）
      · ``each of your Evolution Pokémon`` ↔ ``those Pokémon``（= 己方全部进化宝可梦）
    其它写法（条件/其它范围/其它后续）一律不认 —— 整卡如实未建模。
    """
    if kind != "ability":
        return []
    t = re.sub(r"\s+", " ", _norm_apostrophe(text or "")).strip()
    low = t.lower()
    if not _EVOLVE_TRIGGER_RE.search(low):
        return []
    m = re.search(r"heal all damage from ([^.]{3,60})", low)
    if not m:
        return []
    clause = m.group(1).strip()
    if "each of your evolution pok" in clause:
        scope, ptype = "evolved", None
    else:
        scope, ptype = _heal_scope_of("from " + clause)
    if scope is None:
        return []
    acts = [act("heal", None, "self",
                {"heal_scope": scope, "heal_all": True,
                 **({"heal_type": ptype} if ptype else {})})]
    if re.search(r"if you healed any damage in this way", low):
        m2 = re.search(r"discard all energy from (that|those) pok[eé]mon", low)
        if not m2:
            return []                    # 有后续但认不出 → 不建模
        if scope == "evolved" and m2.group(1) == "those":
            acts.append(act("discard", None, "self",
                            {"card_type": "energy", "all": True,
                             "if_prev": True, "energy_scope": "evolved"}))
        elif scope == "active" and m2.group(1) == "that":
            acts.append(act("discard", None, "self",
                            {"card_type": "energy", "all": True,
                             "if_prev": True}))
        else:
            return []                    # 回复范围与后续对象不配套 → 不建模
    return acts


def _detect_ko_trigger(text: str, kind: str) -> list:
    """被击倒触发（特性）→ ``rule`` 动作（后续映射为引擎 trigger tag）

    三种可精确表达的形态：
      · Togekiss「When your opponent's Active Pokémon is Knocked Out, flip a coin.
        If heads, take 1 more Prize card.」→ ``ko_prize_plus:1:coin``
      · Maractus「If this Pokémon is in the Active Spot and is Knocked Out by
        damage from an attack …, put 6 damage counters on the Attacking Pokémon.」
        → ``on_ko:counter:60``
      · Huntail「When 1 of your {W} Pokémon is Knocked Out …, put all Basic {W}
        Energy attached to that Pokémon into your hand …」→ ``on_ko:energy_to_hand:water``
    """
    if kind != "ability":
        return []
    t = re.sub(r"\s+", " ", _norm_apostrophe(text or "")).strip()
    low = t.lower()
    m = re.search(r"when your opponent'?s active pok[eé]mon is knocked out, "
                  r"flip a coin\. if heads, take (\d+) more prize card", low)
    if m:
        return [act("rule", None, "any",
                    {"ko_prize_plus": int(m.group(1)), "coin": True})]
    # 第八十七批（2026-11-05）：「If your opponent's Basic Pokémon is Knocked Out
    # by damage from an attack used by this Pokémon, take 1 more Prize card.」
    # （Hydreigon ex「Greedy Eater」）。引擎 `_ko_prize_bonus` 新增 `opp_basic`
    # 限定：被击倒的是**对手基础**宝可梦（任意位置，含后备）才触发。
    m = re.search(r"if your opponent'?s basic pok[eé]mon is knocked out by "
                  r"damage from an attack used by this pok[eé]mon, take (\d+) "
                  r"more prize card", low)
    if m:
        return [act("rule", None, "any",
                    {"ko_prize_plus": int(m.group(1)), "opp_basic": True})]
    m = re.search(r"if this pok[eé]mon is in the active spot and is knocked out "
                  r"by damage from an attack from your opponent'?s pok[eé]mon, "
                  r"put (\d+) damage counters? on the attacking pok[eé]mon", low)
    if m:
        return [act("rule", None, "any",
                    {"on_ko_counter": int(m.group(1)) * 10})]
    m = re.search(r"when 1 of your \{([a-z])\} pok[eé]mon is knocked out by "
                  r"damage from an attack from your opponent'?s pok[eé]mon, you "
                  r"may put all basic \{([a-z])\} energy attached to that "
                  r"pok[eé]mon into your hand instead of the discard pile", low)
    if m and m.group(1).upper() == m.group(2).upper():
        color = _EN_SYMBOL_COLOR.get(m.group(1).upper())
        if color:
            return [act("rule", None, "any",
                        {"on_ko_energy": color})]
    return []


def _claimed_ability(text: str, kind: str) -> list:
    """**整段认领**的特性句式（被击倒触发 / 进化触发 + in this way）

    这类文本同时含触发从句与后续子句，若走通用解析会产出「无条件」动作
    （例如把「若回复了才弃能」落成必然弃能）。因此必须在最前面整段认领，
    认不出就**不产出**（整卡如实未建模）。
    """
    return _detect_evolve_heal(text, kind) or _detect_ko_trigger(text, kind)


def _detect_static_imm(text: str) -> list:
    """静态免伤（特性 / 卡级规则）→ ``passive:dmg_immune:*`` 动作

    只认 ``_IMMUNE_PATTERNS`` 里的**整句**形态。任何近似写法（缺来源限定、
    卡名/道具限定、百分号概率…）都**不产出**：退化成「无条件全额免疫」会让
    对局结果系统性偏向持有者 —— 属最危险的静默错算。
    """
    t = re.sub(r"\s+", " ", _norm_apostrophe(text or "")).strip()
    t = _RULE_BOX_NOTE_RE.sub(" ", t).strip()      # 规则复述括号（不是效果）
    low = t.lower()
    for pat, scope, qual, effects in _IMMUNE_PATTERNS:
        m = re.fullmatch(pat + r"\.?", low)
        if not m:
            continue
        extra = {"kind": "dmg_immune", "scope": scope}
        if qual == "min":
            extra["filter"] = "min:" + m.group(1)
        elif qual:
            extra["filter"] = qual
        if effects:
            extra["effects"] = True
        return [act("passive", None, scope, extra)]
    return []


def _shield_prevent_qual(text: str):
    """「prevent all damage …（下回合护盾）」的来源 / 门槛限定 → ``(qual, effects)``

    返回 ``None`` = **有限定但引擎未建模**：调用方必须如实登记，**不得**退化成
    无条件 ``shield:prevent_all``（否则「只挡基础宝可梦」会变成「挡全部」）。
    ``qual`` 为空串表示确无来源限定（全面免疫，属精确语义）。
    """
    low = _norm_apostrophe(text).lower()
    effects = bool(re.search(r"from\s+and\s+effects\s+of\s+attacks", low))
    m = re.search(r"if that damage is (\d+) or less", low)
    if m:
        return (f"if_damage:le:{int(m.group(1))}", effects)
    if re.search(r"by attacks from basic non-\{c\} pok[eé]mon", low):
        return ("from:basic_non_colorless", effects)
    if re.search(r"by attacks from basic pok[eé]mon", low):
        return ("from:basic", effects)
    if re.search(r"attacks?\s+from\s+|from\s+attacks?\s+from\s+", low):
        return None                      # 其它来源限定：不认
    return ("", effects)


# 「Once during your turn, you may put N damage counters on this Pokémon. If you
#  do, during this turn, attacks used by this Pokémon do M more damage …」
# （第九十五批，Feraligatr「Torrential Heart」）
# 两件事必须**同时**建模：自伤是**指示物**（不吃弱点/抗性），加伤是回合级旗标。
# 只落其中一件 = 静默失真（要么白付代价，要么凭空加伤）。
_SELF_BUFF_RE = re.compile(
    r"^once during your turn, you may put (\d+) damage counters? on this "
    r"pok[eé]mon\. if you do, during this turn, attacks used by this "
    r"pok[eé]mon do (\d+) more damage to your opponent'?s active "
    r"pok[eé]mon \(before applying weakness and resistance\)\.$", re.I)


# 「This Pokémon can't be Asleep.」（第一百一十一批，Hoothoot「Insomnia」）
_STATUS_IMMUNE_SELF_RE = re.compile(
    r"^this pok[eé]mon can'?t be (asleep|poisoned|burned|confused|paralyzed)[.]$",
    re.I)


def _detect_status_immune(text: str, kind: str) -> list:
    """卡面「This Pokémon can't be <状态>.」→ `passive:status_immune:<s>`

    ⚠ 与「cure（清除已中状态）」是**相反**语义：免疫是「永远施加不上」，
    clear 是「已经中了再解」。落错方向会让免疫者在被施加时**照样中招**。
    ⚠ 也不能落成 `status_self:`（那是「让自己陷入该状态」，方向反 180°）。
    """
    if kind != "ability":
        return []
    t = re.sub(r"\s+", " ", _norm_apostrophe(text or "")).strip()
    m = _STATUS_IMMUNE_SELF_RE.match(t)
    if not m:
        return []
    st = _STATUS_WORD_TO_TAG.get(m.group(1).lower())
    if not st:
        return []
    return [act("passive", None, "self",
                {"kind": "status_immune", "status": st})]


# 「switch 1 of your Benched {D} Pokémon, except any X {ex}, with your Active
#  Pokémon. If you do, the new Active Pokémon is now Poisoned.」
# （第一百一十一批，Pecharunt ex「Subjugating Chains」）
_SWITCH_CHOOSE_RE = re.compile(
    r"^once during your turn, you may switch 1 of your benched \{([a-z])\}.{0,4}?"
    r"pok[eé]mon, except any (?P<exc>[a-z][a-z .'-]*?) \{(?:ex|v)\}, with your "
    r"active pok[eé]mon[.]\s*if you do, the new active pok[eé]mon is now "
    r"(?P<st>poisoned|burned|asleep|confused|paralyzed)[.](?:\s*you can'?t use "
    r"more than 1 .*? each turn[.])?$", re.I)


def _detect_switch_choose(text: str, kind: str) -> list:
    """带属性/例外筛选的换位 + 换入后状态 → `switch_choose:<type>:...:then:<s>`"""
    if kind != "ability":
        return []
    t = re.sub(r"\s+", " ", _norm_apostrophe(text or "")).strip()
    m = _SWITCH_CHOOSE_RE.match(t)
    if not m:
        return []
    color = _EN_SYMBOL_COLOR.get(m.group(1).upper())
    exc = _tag_name_key(m.group("exc"))
    st = _STATUS_WORD_TO_TAG.get(m.group("st").lower())
    if not color or not exc or not st:
        return []
    return [act("switch_choose", None, "self",
                {"type": color, "except": exc, "then": st})]


# 「if you have Solrock in play, you may discard a Basic {F} Energy card from your
#  hand in order to use this Ability. Draw 3 cards.」（第一百一十一批，Lunatone）
_SOLROCK_DRAW_RE = re.compile(
    r"^once during your turn, if you have (?P<mon>[a-z][a-z' .-]{2,24}?) in play, "
    r"you may discard (?:a|an) basic \{(?P<e>[a-z])\} energy card from your hand "
    r"in order to use this ability[.]\s*draw (?P<n>\d+) cards?[.]"
    # 尾部常跟一句「You can't use more than 1 <名> Ability each turn.」——
    # 那是**额度**限制，不是效果；不吸收就会整段失配（Lunatone 实测）。
    r"(?:\s*you can'?t use more than 1 .*? each turn[.])?$", re.I)


def _detect_solrock_draw(text: str, kind: str) -> list:
    """具名在场条件 + 弃手牌能量代价 + 抽牌 → `([draw 动作], ability_cost)`

    条件写成 `draw:N:if:own_field_name:<key>`：引擎 `_draw_spec` 会求值，
    合法动作生成也会剔除（条件不成立时**根本不列**这个动作）。
    ⚠ 不能只产 `draw:N`：那等于「有没有 Solrock 都能抽」，静默加强。
    """
    if kind != "ability":
        return []
    t = re.sub(r"\s+", " ", _norm_apostrophe(text or "")).strip()
    m = _SOLROCK_DRAW_RE.match(t)
    if not m:
        return []
    key = _tag_name_key(m.group("mon"))
    color = _EN_ENERGY_LETTER.get(m.group("e").upper())
    if not key or not color:
        return []
    return ([act("draw", int(m.group("n")), "deck",
                 {"cond": f"own_field_name:{key}"})],
            f"discard_hand_energy:1:{color}")


# 「… attach a Basic {G} Energy card from your hand to 1 of your Pokémon. If you
#  attached Energy to a Pokémon in this way, heal 30 damage from that Pokémon.」
_ATTACH_HEAL_RE = re.compile(
    r"^once during your turn, you may attach a basic \{(?P<e>[a-z])\} energy "
    r"card from your hand to 1 of your pok[eé]mon[.]\s*if you attached energy "
    r"to a pok[eé]mon in this way, heal (?P<n>\d+) damage from that "
    r"pok[eé]mon[.]$", re.I)


def _detect_attach_heal(text: str, kind: str) -> list:
    """附能手牌能量 + 回复**被附能的那只** → 单条 `attach_energy:…:heal:N`

    ⚠ 「that Pokémon」指**被附能的目标**，不是特性持有者。分成两条 tag 会
      丢掉这层指代（`heal:` 默认回持有者），等于回错对象。
    """
    if kind != "ability":
        return []
    t = re.sub(r"\s+", " ", _norm_apostrophe(text or "")).strip()
    m = _ATTACH_HEAL_RE.match(t)
    if not m:
        return []
    color = _EN_ENERGY_LETTER.get(m.group("e").upper())
    if not color:
        return []
    # ⚠ 动作类型是 `attach`：发射层按「kind == ability 且非 on_evolve」自动前缀成
    #   `attach_energy:`。写成 `attach_energy` 会报「引擎未建模该动作类型」。
    return [act("attach", 1, "self",
                {"energy_type": color, "zone": "hand",
                 # 「to **1 of your** Pokémon」= 己方任意一只（含后备）；
                 # 省略 target 会默认附给特性持有者 → 回复目标也跟着错。
                 "target_scope": "any",
                 "heal": int(m.group("n"))})]


# 「As long as this Pokémon is in the Active Spot, whenever you attach an Energy
#  card from your hand to 1 of your Pokémon, heal 90 damage from that Pokémon.」
_HEAL_ON_ATTACH_RE = re.compile(
    r"^as long as this pok[eé]mon is in the active spot, whenever you attach an "
    r"energy card from your hand to 1 of your pok[eé]mon, heal (?P<n>\d+) damage "
    r"from that pok[eé]mon[.]$", re.I)


def _detect_heal_on_attach(text: str, kind: str) -> list:
    """附能即回复（常驻被动）→ `passive:heal_on_attach:<n>`

    ⚠ 是**被动钩子**，不是可发动特性：落成普通 field 特性会变成「每回合主动
      点一次回 90」，强度与触发时机双错。
    """
    if kind != "ability":
        return []
    t = re.sub(r"\s+", " ", _norm_apostrophe(text or "")).strip()
    m = _HEAL_ON_ATTACH_RE.match(t)
    if not m:
        return []
    return [act("passive", int(m.group("n")), "self",
                {"kind": "heal_on_attach"})]


# 「If your opponent has a Stadium in play, discard it. If you do, your opponent
#  can't play any Stadium cards from their hand during their next turn.」
_STADIUM_SMASH_RE = re.compile(
    r"^if your opponent has a stadium in play, discard it[.]\s*if you do, "
    r"your opponent can'?t play any stadium cards? from their hand during "
    r"their next turn[.]$", re.I)


def _detect_stadium_smash(text: str, kind: str) -> list:
    """弃对手场地 + 封对手场地 → **有序**两条动作（第一百零七批，Chi-Yu）

    顺序与依赖都必须精确：
      · 先 `discard_stadium`（真的把场地弃掉）；
      · 再 `play_lock:stadium:opp:turn:if_prev` —— `:if_prev` 保证「没弃到
        场地就不封锁」（卡面写 "If you do"）。少了它就会「空场也照样封」，
        多了顺序问题就会「先封后弃」，两者都是静默错算。
    """
    if kind != "attack":
        return []
    t = re.sub(r"\s+", " ", _norm_apostrophe(text or "")).strip()
    if not _STADIUM_SMASH_RE.match(t):
        return []
    return [act("rule", None, "any", {"discard_stadium": True}),
            act("play_lock", None, "active_opponent",
                {"play": "stadium", "until": "turn", "if_prev": True})]


# ---- 静态规则族（第一百一十二批起）----
# 三类**不是效果**而是规则改写 / 出招许可，必须整段认领，否则通用分支会把
# 它们读成「无条件效果」（例：把「ignore all Energy in this attack's cost」
# 当成免费出招=静默加强）。
_COST_SET_RE = re.compile(
    r"^(?:if (?P<cond>.+?), )?this pok[eé]mon can use the (?P<atk>.+?) attack "
    r"for \{(?P<e>[a-z])\}[.]$", re.I)
_COST_IGNORE_COLOR_RE = re.compile(
    r"^(?:if (?P<cond>.+?), )?ignore all \{(?P<e>[a-z])\} energy in the costs? "
    r"of attacks used by this pok[eé]mon[.]$", re.I)
_COST_IGNORE_ALL_RE = re.compile(
    r"^(?:if (?P<cond>.+?), )?ignore all energy in this attack[\u2019']?s "
    r"cost[.]$", re.I)
_COST_SET_SELF_RE = re.compile(
    r"^(?:if (?P<cond>.+?), )?this attack can be used for \{(?P<e>[a-z])\}[.]$",
    re.I)
_NO_ATTACK_UNLESS_RE = re.compile(
    r"^(?:if (?P<cond>.+?), )?this pok[eé]mon can[\u2019']?t attack"
    r"(?: unless (?P<unless>.+?))?[.]$", re.I)
_EVOLVE_FIRST_TURN_RE = re.compile(
    # 两种主语都要认：「this Pokémon can evolve…」与「**it** can evolve…」
    # （Eevee 的被动主语是 it，指代前半句的 this Pokémon）。
    r"^(?:as long as this pok[eé]mon is in the active spot, )?"
    r"(?:if (?P<cond>.+?), )?(?:this pok[eé]mon|it) can evolve during your "
    r"first turn or the turn you play it[.]$", re.I)


def _detect_cost_rules(text: str, kind: str) -> list:
    """费用改写 / 禁攻 / 当回合进化 —— 三类**规则**型文本

    返回的动作交给 to_engine_tags 变成：
      · ``fx:attack_cost:self_set:<n>:<c>[:if:<cond>][:name:<k>]``
      · ``fx:attack_cost:self_omit:<c>[:if:<cond>]``
      · ``passive:attack_block_unless:<cond>``
      · ``passive:evolve_same_turn[:if:<cond>][:active]``
    """
    t = re.sub(r"\s+", " ", _norm_apostrophe(text or "")).strip()

    m = _COST_SET_RE.match(t)
    if m:
        color = _EN_ENERGY_LETTER.get(m.group("e").upper())
        key = _tag_name_key(m.group("atk"))
        if color and key:
            cond = _static_cond(m.group("cond"))
            if m.group("cond") and not cond:
                return []
            return [act("fx", None, "self", {
                # `:name:` 必须排在 `:if:` **之前**：条件本身含冒号，
                # 放后面会被条件吞掉（引擎侧按「先切 if 再读 name」解析）。
                "tag": "fx:attack_cost:self_set:1:" + color
                       + f":name:{key}" + (f":if:{cond}" if cond else "")})]

    m = _COST_IGNORE_COLOR_RE.match(t)
    if m:
        color = _EN_ENERGY_LETTER.get(m.group("e").upper())
        if color:
            cond = _condition_from_text(m.group("cond")) \
                if m.group("cond") else None
            if m.group("cond") and not cond:
                return []
            return [act("fx", None, "self", {
                "tag": "fx:attack_cost:self_omit:" + color
                       + (f":if:{cond}" if cond else "")})]

    m = _COST_IGNORE_ALL_RE.match(t)
    if m:
        cond = _static_cond(m.group("cond"))
        if m.group("cond") and not cond:
            return []
        return [act("fx", None, "self", {
            "tag": "fx:attack_cost:self_set:0:colorless"
                   + (f":if:{cond}" if cond else "")})]

    m = _COST_SET_SELF_RE.match(t)
    if m:
        color = _EN_ENERGY_LETTER.get(m.group("e").upper())
        if color:
            cond = _condition_from_text(m.group("cond")) \
                if m.group("cond") else None
            if m.group("cond") and not cond:
                return []
            return [act("fx", None, "self", {
                "tag": "fx:attack_cost:self_set:1:" + color
                       + (f":if:{cond}" if cond else "")})]

    m = _NO_ATTACK_UNLESS_RE.match(t)
    if m:
        # 语义必须分清两种句式（写反会让「有 ex 才能打」变成「有 ex 反而不能打」）：
        #   · 「can't attack **unless** X」 → X 就是**许可条件**；
        #   · 「**If** Y, can't attack」     → 许可条件 = **not Y**。
        if m.group("unless"):
            cond = _condition_from_text("unless " + m.group("unless"))
        elif m.group("cond"):
            _y = _static_cond(m.group("cond"))
            # 条件本身可能已是 `not:…`（「has **no** ex」）—— 再加一层 `not:`
            # 虽然求值仍正确（双重否定），但 tag 会变成 `not:not:…`，
            # 对账与人工核对都难读。直接抵消。
            if not _y:
                cond = None
            elif _y.startswith("not:"):
                cond = _y[len("not:"):]
            else:
                cond = f"not:{_y}"
        else:
            cond = None
        if cond:
            return [act("passive", None, "self",
                        {"kind": "attack_block_unless", "cond": cond})]
        return []

    m = _EVOLVE_FIRST_TURN_RE.match(t)
    if m:
        cond = _static_cond(m.group("cond"))
        if m.group("cond") and not cond:
            return []
        active_only = bool(re.match(
            r"^as long as this pok[eé]mon is in the active spot",
            t, re.I))
        return [act("passive", None, "self",
                    {"kind": "evolve_same_turn", "cond": cond,
                     "active_only": active_only})]
    return []


# ---- 数值改写族（第一百一十九/一百二十批）----
_BASE_DAMAGE_SET_RE = re.compile(
    r"^(?:if (?P<cond>.+?), )?this attack[\u2019']?s base damage is "
    r"(?P<n>\d+)[.]$", re.I)
_NEXT_BASE_DAMAGE_RE = re.compile(
    r"^during your next turn, this pok[eé]mon[\u2019']?s (?P<atk>.+?) "
    r"attack[\u2019']?s base damage is (?P<n>\d+)[.]$", re.I)
_HEAL_EQUAL_RE = re.compile(
    r"^heal from this pok[eé]mon the same amount of damage you did to your "
    r"opponent[\u2019']?s active pok[eé]mon[.]$", re.I)


def _detect_value_rules(text: str, kind: str) -> list:
    """基础伤害 / 回复量的**数值改写**（第一百一十九/一百二十批）

    · 「If <cond>, this attack's **base damage is 30**」（Mega Mawile ex
      「Huge Bite」）→ ``dmg_set:30:if:<cond>``：条件不成立时**保持印刷伤害**。
    · 「During your next turn, this Pokémon's <招式> attack's **base damage is
      240**」（Watchog「Focus Energy」）→ ``self_attack_set_next:240:name:<k>``。
      ⚠ 是改写不是加伤：落成 plus 会让 240 变成「印刷 80 + 240」。
    · 「Heal from this Pokémon **the same amount of damage you did** …」
      （Virizion「Giga Drain」）→ ``heal:equal``。
    """
    t = re.sub(r"\s+", " ", _norm_apostrophe(text or "")).strip()

    m = _BASE_DAMAGE_SET_RE.match(t)
    if m:
        cond = _static_cond(m.group("cond"))
        if m.group("cond") and not cond:
            return []
        return [act("rule", None, "any", {
            "dmg_set_cond": (int(m.group("n")), cond)})]

    m = _NEXT_BASE_DAMAGE_RE.match(t)
    if m:
        key = _tag_name_key(m.group("atk"))
        if key:
            return [act("rule", None, "self", {
                "self_atk_set_next": (int(m.group("n")), key)})]
        return []

    if _HEAL_EQUAL_RE.match(t):
        return [act("heal", None, "self", {"heal_equal": True})]
    return []


def _detect_self_buff(text: str, kind: str) -> list:
    """自伤指示物 + 本回合自身加伤 → **单条**复合 tag（`a|b`，按卡面顺序）"""
    if kind != "ability":
        return []
    t = re.sub(r"\s+", " ", _norm_apostrophe(text or "")).strip()
    m = _SELF_BUFF_RE.match(t)
    if not m:
        return []
    return [act("rule", None, "self",
                {"multi": [f"counters:{int(m.group(1))}:scope:self",
                           f"self_dmg_bonus:{int(m.group(2))}:self"]})]


def _detect_modifiers(text: str, kind: str, name: str) -> list:
    """从卡面文本识别 v1.4 修正器 → 动作列表（未识别返回空表，不猜）"""
    t = _norm_apostrophe(text)
    # 跨回合伤害修正（2026-10-29 第十六批）：这三句已由 ``_detect_next_turn_dmg``
    # 在 parse_rule 里认领并落 tag；此处必须先把它们从文本里剥掉，否则
    # `_MORE_DMG_RE` 会把「During your next turn … does N more damage」当成
    # 「条件未识别」再登记一次 → 整卡被踢出卡池（实测 Meloetta ex / Pecharunt）。
    _, t = _detect_next_turn_dmg(t, kind)
    low = t.lower()
    out: list = []
    if not t.strip():
        return out

    # 双方洗回手牌 + 抛币分抽（第四十五批，Harlequin）：整段唯一语义，直接认领
    m_sb = _SHUFFLE_BOTH_COIN_DRAW_RE.search(t)
    if m_sb:
        out.append(act("shuffle_both_coin_draw", None, "both",
                       {"mine_heads": int(m_sb.group(1)),
                        "opp_heads": int(m_sb.group(2)),
                        "mine_tails": int(m_sb.group(3)),
                        "opp_tails": int(m_sb.group(4))}))
        return out

    # 全队限时减伤（第四十六批，Iron Defender）：整段唯一语义
    m_ts = _TEAM_SHIELD_RE.search(t)
    if m_ts:
        _col = _EN_SYMBOL_COLOR.get(m_ts.group(1).upper())
        if not _col:
            out.append(act("rule", None, "any",
                           {"note": "全队限时减伤：属性符号未识别（B5）"}))
        else:
            out.append(act("team_shield", int(m_ts.group(2)), "self",
                           {"color": _col}))
        return out

    # 单体限时护盾 + 奖赏数打出前置条件（第四十六批，Acerola's Mischief）
    m_sc = _SHIELD_CHOOSE_RE.search(t)
    if m_sc:
        out.append(act("shield_choose", None, "self",
                       {"kind": "prevent_all", "from": "ex", "effects": True,
                        "gate": f"opp_prizes_le:{int(m_sc.group(1))}"}))
        return out

    # 可选加伤 + 反伤（第四十七批）：整段唯一语义
    m_rec = _YOU_MAY_RECOIL_RE.match(t.strip())
    if m_rec and _YOU_MAY_RECOIL_RE.fullmatch(t.strip()):
        out.append(act("dmg_recoil", int(m_rec.group(1)), "active_opponent",
                       {"recoil": int(m_rec.group(2))}))
        return out

    # 抛币免击倒（第七十八批，Mega Hawlucha ex）：整段唯一语义，直接认领
    m_sv = _COIN_SURVIVE_RE.fullmatch(t.strip() or "")
    if m_sv:
        out.append(act("passive", None, "self",
                       {"kind": "coin_survive", "hp": int(m_sv.group(1))}))
        return out

    # 清除自身全部特殊状态（第八十一批）：整段唯一语义，直接认领。
    if _CURE_SELF_RE.fullmatch(t.strip() or ""):
        out.append(act("cure", None, "self", {"scope": "all"}))
        return out

    # 满 HP 免击倒·特性版（第八十批）：整段唯一语义，直接认领。
    # 与 coin_survive 并列但**反口径**：这里必须满 HP、不抛币、必定生效。
    m_fh = _SURVIVE_FULL_HP_RE.fullmatch(t.strip() or "")
    if m_fh:
        out.append(act("passive", None, "self",
                       {"kind": "survive", "hp": int(m_fh.group(1))}))
        return out

    # 1) 条件/计数伤害缩放（先算，便于抛币分支复用同一伤害修正语义）
    # 两个正则**分开**匹配：`_SCALING_BASE_RE` = 基础伤害按计数源重算，必须落
    # `dmg_set`；`_SCALING_MORE_RE` = 在印刷伤害上加，落 `dmg_plus`。早期用
    # `_SCALING_MORE_RE.search(t) and not …` 判断模式，若同一张卡另有 "more"
    # 子句，就会把基础伤害子句误判成加伤（本批次 47 条系统性错算的根因）。
    m_more = _SCALING_MORE_RE.search(t)
    m_base = _SCALING_BASE_RE.search(t)
    m = m_more or m_base
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
            mode = "set_per" if m is m_base else "plus_per"
            out.append(act("damage_mod", n, "active_opponent",
                           {"mode": mode, "per": counter,
                            "phrase": phrase.strip()[:40]}))
        else:
            out.append(act("rule", None, "active_opponent",
                           {"note": f"条件伤害缩放：计数源未识别（{phrase.strip()[:30]}）"}))
    elif not _COIN_RE.search(t) and _SCALING_LESS_RE.search(t):
        # 「does N less damage for each X」→ `dmg_minus:N:per:<counter>`
        mm = _SCALING_LESS_RE.search(t)
        n, phrase = int(mm.group(1)), mm.group(2)
        counter = _counter_from_phrase(phrase)
        if counter:
            out.append(act("damage_mod", n, "active_opponent",
                           {"mode": "minus_per", "per": counter,
                            "phrase": phrase.strip()[:40]}))
        else:
            out.append(act("rule", None, "active_opponent",
                           {"note": f"条件伤害缩放：计数源未识别"
                                    f"（{phrase.strip()[:30]}）"}))
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
            # Cetitan ex：「You may discard a Stadium in play. If you do, this attack
            # does 140 more damage.」——「if you do」指的就是**弃成了场地**，
            # 与「场上有场地」等价（有场地才能弃，弃了场地才加伤）。
            if not cond and re.search(r"discard (?:a|that) stadium", t, re.I) \
                    and re.search(r"if you do", seg, re.I):
                cond = "stadium_in_play"
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
    # 静态免伤（2026-10-28 第八批）：整句认领，先于后备免疫 / 护盾分支
    imm = _detect_static_imm(t)
    if imm:
        out.extend(imm)
    elif bench_immune:
        # 卡面两种写法必须分开：「… from and effects of attacks …」连**效果**一起挡；
        # Tera 规则「prevent all damage … by attacks」**只挡伤害**（效果照常生效）。
        # 统一成一个 tag 会静默放大防护范围。
        kind_ = ("bench_immune"
                 if re.search(r"from\s+and\s+effects\s+of\s+attacks", low)
                 else "bench_immune_dmg")
        out.append(act("passive", None, "self", {"kind": kind_}))
    elif coin_covers_shield:
        pass
    elif mred and _opp_next_turn(t):
        out.append(act("shield", int(mred.group(1)), "self",
                       {"kind": "reduce"}))
    elif mprev and _opp_next_turn(t):
        q = _shield_prevent_qual(t)
        if q is None:
            out.append(act("rule", None, "any",
                           {"note": "护盾来源限定未建模（B5）"}))
        else:
            qual, eff = q
            extra = {"kind": "prevent_all", "effects": eff}
            if qual:
                extra["qual"] = qual
            out.append(act("shield", None, "self", extra))

    # 3b) 静态修正（S1 静态修正层 · 2026-10-26）
    #     与上一段的 `shield:*` **必须严格区分**：
    #       · 「During your opponent's next turn, this Pokémon takes 50 less damage」
    #         → 时限制护盾 `shield:reduce:50`（在对手下个回合结束时失效）
    #       · 「This Pokémon takes 30 less damage from attacks (after applying
    #          Weakness and Resistance).」→ 常驻 `passive:dmg_reduce:30`
    #     把常驻写成护盾（或反之）都会让减伤在错误的时间窗生效 —— 静默错算。
    if not _opp_next_turn(t) and kind == "ability":
        # 抛币免伤被动（第四十五批，Fezandipiti「Adrena-Pheromone」）：
        # 「If this Pokémon has any {D} Energy attached and is damaged by an
        # attack, flip a coin. If heads, prevent that damage.」
        # ⚠ 不是 `passive:dmg_immune`（那是无条件/来源限定），也不是
        #   `on_damaged:`（伤害已加上去，事后无法撤销）→ 必须在**伤害结算内**抛币。
        mcprev = _COIN_PREVENT_PASSIVE_RE.search(low)
        if mcprev:
            _col = _EN_SYMBOL_COLOR.get(mcprev.group(1).upper())
            if _col:
                out.append(act("passive", None, "self",
                               {"kind": "coin_prevent", "color": _col}))
            else:
                out.append(act("rule", None, "any",
                               {"note": "抛币免伤：属性符号未识别（B5）"}))
        # 来源限定减伤（「… from attacks from your opponent's {R} or {W}
        # Pokémon」）：只接受**属性集合**可完整表达的形态，写成
        # `passive:dmg_reduce:N:from:type:<t1,t2>`；其它来源限定（道具/特性/
        # 特定卡名等）一律不产出常驻减伤，避免把「只挡某类攻击」放大成「挡所有」。
        mred_src = re.search(
            r"this pok[eé]mon takes (\d+) less damage from attacks? from "
            r"your opponent's ((?:\{[a-z]\}(?:\s+or\s+)?)+) pok[eé]mon", low)
        src_colors = []
        if mred_src:
            src_colors = [_EN_SYMBOL_COLOR.get(c.upper())
                          for c in re.findall(r"\{([a-z])\}", mred_src.group(2),
                                              re.I)]
        if mred_src and src_colors and all(src_colors):
            out.append(act("passive", int(mred_src.group(1)), "self",
                           {"kind": "dmg_reduce", "from_types": src_colors}))
        elif re.search(r"takes? \d+ less damage from attacks? "
                       r"(?:from|used by|by)\b", low):
            # 未建模的来源限定：显式登记，绝不退化成无条件减伤
            out.append(act("rule", None, "any",
                           {"note": "静态伤害增减（需引擎静态修正层，B4）"}))
        elif mred and re.search(r"this pok[eé]mon takes", low):
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
    # 同一枚币的双分支（先于单分支判定，2026-11-03 第四十四批）：
    # 只抛一次，两个子效果分别挂在 heads / tails 上。
    mfork = _COIN_FORK_COUNTERS_RE.search(text)
    if mfork:
        out.append(act("coin_flip", None, "active_opponent_bench",
                       {"side": "heads", "mode": "fork",
                        "sub": f"counters:{int(mfork.group(1))}:scope:opp:1",
                        "sub_tails": f"counters:{int(mfork.group(2))}:scope:self"}))
        return out
    # 逐目标各抛一枚（Mega Zygarde ex，第四十五批）：必须在 `_COIN_EACH_RE`
    # 之前认领 —— 后者会把「For each …, flip a coin.」吞成空结果。
    m_pt = _COIN_PER_TARGET_DMG_RE.search(text)
    if m_pt:
        out.append(act("coin_flip", None, "active_opponent_bench",
                       {"side": "heads", "mode": "per_target",
                        "sub_amount": int(m_pt.group(1))}))
        return out
    # 「Flip a coin **until you get tails**」必须最先判（否则会被下面的
    # 「Flip N coins … for each heads」吞成 1 枚币的一次性加伤）。
    m_until = _COIN_UNTIL_TAILS_RE.search(text)
    if m_until:
        # 有 "more" → 在印刷伤害上加；没有 → 基础伤害按正面数重算（0 正面 = 0）。
        # 与 `_COIN_PER_SIDE_RE` 分支同一条纪律：落反了就是静默改强度。
        out.append(act("coin_flip", None, "active_opponent",
                       {"side": "heads", "mode": "until_tails",
                        "sub": "dmg_plus" if m_until.group(2) else "dmg_set",
                        "sub_amount": int(m_until.group(1))}))
        return out
    # 「Flip a coin **for each** X」（2026-11-04 第四十八批）：币数由计数源决定。
    # 必须排在 `_COIN_PER_SIDE_RE` 之前 —— 后者会把「for each Energy attached…」
    # 当成 **1 枚币**（`(\d+)?` 留空），静默把「7 个能量 = 7 枚币」压成 1 枚。
    _norm_t = re.sub(r"\s+", " ", _norm_apostrophe(text or "")).strip()
    _mc = _COIN_EACH_SRC.match(_norm_t)
    if _mc:
        _src = _coin_each_src_of(_mc.group("src"), _norm_t)
        if _src:
            out.append(act("coin_flip", None, "active_opponent",
                           {"side": "heads", "mode": "each",
                            "counter": _src,
                            "sub": "dmg_plus" if _mc.group("more") else "dmg_set",
                            "sub_amount": int(_mc.group("n"))}))
            return out
    # 「Flip N coins … for each heads/tails」优先（否则会被 each 分支吞掉）
    mps = _COIN_PER_SIDE_RE.search(text)
    if mps:
        n = int(mps.group(1)) if mps.group(1) else 1
        # 有 "more" → 在印刷伤害上**加**；没有 → 基础伤害**按正面数重算**
        # （「Flip 3 coins. This attack does 30 damage for each heads.」在 0 正面时
        #  应造成 0，而不是印刷的 30）。后者用 `dmg_set`，不得落成 `dmg_plus`。
        sub = "dmg_plus" if mps.group(3) else "dmg_set"
        extra = {"side": mps.group(4).lower(), "mode": "each",
                 "counter": str(n), "sub": sub,
                 "sub_amount": int(mps.group(2))}
        # 多币条件 + 状态（Slurpuff / Butterfree）→ 共用同一次抛币的
        # `cond:` 复合子效果，**不得**拆成第二次抛币。
        cc = _coin_cond_of(text)
        if cc:
            extra["sub_more"] = [cc[0]]
        out.append(act("coin_flip", None, "active_opponent", extra))
        return out
    each = _COIN_EACH_RE.search(text)
    if each:
        # 「对手为每只后备各抛 1 枚，每个反面 …」→ 计数源 + 反面枚数生效
        m = re.search(r"does\s+(\d+)\s+(more\s+)?damage[^.]*?for\s+each\s+tails",
                      text, re.I)
        if m and "for each tails" in low:
            out.append(act("coin_flip", None, "active_opponent",
                           {"side": "tails", "mode": "each",
                            "counter": "opp_bench",
                            "sub": "dmg_plus" if m.group(2) else "dmg_set",
                            "sub_amount": int(m.group(1))}))
            return out
        return out
    m = _COIN_HEADS_MORE_DMG_RE.search(text)
    if m:
        act_ = act("coin_flip", None, "active_opponent",
                   {"side": "heads", "mode": "single",
                    "sub": "dmg_plus", "sub_amount": int(m.group(1))})
        # 同一 heads 分支里的第二个子效果（Floragato「+30 damage, **and heal
        # 30 damage from this Pokémon**」）→ 复合子效果 `dmg_plus:30|heal:30`；
        # 两个子效果**共用同一次抛币**，不得拆成两个独立 coin tag。
        mh = re.search(r"heal\s+(\d+)\s+damage\s+from\s+this\s+pok[eé]mon",
                       text, re.I)
        if mh:
            act_["extra"]["sub_more"] = [f"heal:{int(mh.group(1))}"]
        out.append(act_)
    # 同一枚币的两个状态分支（2026-11-04 第五十三批）——必须在单分支
    # `_COIN_OPP_STATUS_RE` 之前认领，否则只会命中 tails 那半、heads 静默消失。
    mforkst = _COIN_STATUS_FORK_RE.search(_norm_apostrophe(text))
    if mforkst:
        _hs = [_STATUS_WORD_TO_TAG[w.lower()] for w in
               re.split(r"\s+and\s+", mforkst.group("h"))]
        _ts = _STATUS_WORD_TO_TAG[mforkst.group("t").lower()]
        _sub = ("status_multi:" + "+".join(_hs)) if len(_hs) > 1 else _hs[0]
        out.append(act("coin_flip", None, "active_opponent",
                       {"side": "heads", "mode": "each", "counter": "1",
                        "sub": _sub,
                        "sub_more": [f"cond:all_tails:{_ts}"]}))
        return out
    mstat = _COIN_OPP_STATUS_RE.search(_norm_apostrophe(text))
    mall = re.search(r"flip (\d+) coins\.\s*if both of them are (heads|tails),",
                     text, re.I)
    if mall:
        # 「Flip 2 coins. If both of them are heads, this attack does N more
        # damage.」——**全部**为 side 才生效（与 coin_each 的「每正面一次」不同）
        mm = re.search(r"does\s+(\d+)\s+more\s+damage", text, re.I)
        if mm:
            out.append(act("coin_flip", None, "active_opponent",
                           {"side": mall.group(2).lower(), "mode": "all",
                            "counter": mall.group(1), "sub": "dmg_plus",
                            "sub_amount": int(mm.group(1))}))
            return out
        # 「If both of them are tails, this Pokémon also does N damage to itself.」
        # （Team Rocket's Raticate）→ coin_all 分支里的**反伤**子效果
        mtself = re.search(r"this\s+pok[eé]mon\s+also\s+does\s+(\d+)\s+damage\s+"
                           r"to\s+itself", text, re.I)
        if mtself:
            out.append(act("coin_flip", None, "self",
                           {"side": mall.group(2).lower(), "mode": "all",
                            "counter": mall.group(1), "sub": "self",
                            "sub_amount": int(mtself.group(1))}))
            return out
        # 同一条件句式下的**全量回复**（Miltank「Bellyful of Milk」）：
        # 子效果直接写成引擎 tag `heal:all[:scope]`，由 coin 层统一派发。
        if re.search(r"heal all damage", text, re.I):
            sc, pt = _heal_scope_of(text[mall.end():])
            if sc is not None:
                sub = "heal:all" + (f":{sc}" if sc else "") \
                    + (f":type:{pt}" if pt else "")
                out.append(act("coin_flip", None, "self",
                               {"side": mall.group(2).lower(), "mode": "all",
                                "counter": mall.group(1), "sub": sub}))
                return out
    if mstat:
        # 单币单状态：sub 直接是状态名（引擎 _apply_coin_non_damage 会用
        # `_apply_attack_effects` 执行 `paralyze` 等状态 tag）。
        out.append(act("coin_flip", None, "active_opponent",
                       {"side": mstat.group(1).lower(), "mode": "single",
                        # 规范状态名（paralyzed → paralyze）：引擎只认 STATUSES
                        "sub": _STATUS_WORD_TO_TAG[mstat.group(2).lower()]}))
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
    # 抛币分支里的护盾同样要区分是否连**效果**一起挡（见 _detect_modifiers）
    shield_sub = ("shield_prevent_all_effects"
                  if re.search(r"from\s+and\s+effects\s+of\s+attacks", low)
                  else "shield_prevent_all")
    if _COIN_HEADS_PREVENT_RE.search(text):
        out.append(act("coin_flip", None, "self",
                       {"side": "heads", "mode": "single", "sub": shield_sub}))
    elif _COIN_TAILS_PREVENT_RE.search(text):
        out.append(act("coin_flip", None, "self",
                       {"side": "tails", "mode": "single", "sub": shield_sub}))
    mh = _COIN_HEADS_COUNTERS_RE.search(text)
    if mh:
        # 指示物直放（不吃弱点/抗性）→ 必须落 `counters:`，不能落 `spread_free`
        # （后者只覆盖后备且是「任意分配」，这里是「1 只」的 2 个指示物）。
        out.append(act("coin_flip", None, "active_opponent_bench",
                       {"side": "heads", "mode": "single",
                        "sub": f"counters:{int(mh.group(1))}:scope:opp:1"}))
    mt = _COIN_TAILS_SELF_COUNTERS_RE.search(text)
    if mt:
        # 「put N damage counters on your Active Pokémon」是**指示物**、
        # 不是 `self:N`（后者是「受到 N 点伤害」，会走伤害修正）。
        out.append(act("coin_flip", None, "self",
                       {"side": "tails", "mode": "single",
                        "sub": f"counters:{int(mt.group(1))}:scope:self"}))
    mb = _COIN_HEADS_OPP_ACTIVE_ENERGY_RE.search(text)
    if mb:
        # 对手战斗位 1 个能量回**其手牌**（Dustox）→ `bounce:energy:opp_active:1`
        out.append(act("coin_flip", None, "active_opponent",
                       {"side": "heads", "mode": "single",
                        "sub": "bounce:energy:opp_active:1"}))
    ms = _COIN_STATUS_CHOOSE_RE.search(text)
    if ms:
        # 抛币选状态（Cradily「Selective Slime」）：三个状态**三选一**，且整体
        # 受抛币门控。引擎按 `status_choose:` 确定性取首个（可用 action 覆盖）。
        out.append(act("coin_flip", None, "active_opponent",
                       {"side": "heads", "mode": "single",
                        "sub": "status_choose:burn+confuse+poison"}))
    ma = _COIN_STATUS_CHOOSE_ANY_RE.match(text.strip())
    if ma and _COIN_STATUS_CHOOSE_ANY_RE.fullmatch(text.strip()):
        # 抛币选状态·全集（Grafaiai「Miraculous Paint」，第四十七批）：
        # 顺序取 `rules.STATUSES`；确定性默认 = 首个（poison）。
        out.append(act("coin_flip", None, "active_opponent",
                       {"side": "heads", "mode": "single",
                        "sub": "status_choose:poison+burn+sleep+paralyze+confuse"}))
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
    if sub in ("dmg_plus", "dmg_set"):
        return f"{sub}:{n}" if n else sub
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
    if sub == "shield_prevent_all_effects":
        return "shield:prevent_all:effects"
    if sub.startswith("shield:") or sub.startswith("dmg_plus:") \
            or sub.startswith("dmg_set:"):
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
    # 第九十二批（2026-11-05）：除显式 `extra.from_deck` 外，「**同一 spec 内
    # 先 search 牌库、再 evolve**」同样是「从牌库进化」——Salvatore TEF 160：
    # 「Search your deck for a card that has no Abilities and evolves from
    #  1 of your Pokémon, and put it onto that Pokémon to evolve it.」
    # 旧逻辑只看 `extra.from_deck`，该句漏判后落进 `candy`（Rare Candy **跳阶**）
    # 分支 —— 语义完全错误：Salvatore 是**正常阶段进化**，不跳阶。
    def _spec_evolves_from_deck(_actions) -> bool:
        if not any(a.get("type") == "evolve" for a in _actions):
            return False
        if any((a.get("extra") or {}).get("from_deck")
               for a in _actions if a.get("type") == "evolve"):
            return True
        return any(a.get("type") == "search" and a.get("target") == "deck"
                   for a in _actions)

    has_evolve_from_deck = any(
        _spec_evolves_from_deck(s0.get("actions") or ()) for s0 in (specs or []))
    for s in specs or []:
        kind = s.get("kind")
        trigger = s.get("trigger")
        _tag_start = len(tags)
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
                    # 2026-10-29 第十七批：可附 `:per:<counter>`（缩放）与
                    # `:if:<cond>`（条件不满足则整段不结算）
                    scope = extra["snipe_scope"]
                    cnt = extra.get("snipe_count")
                    tag = f"snipe:{scope}:{int(amt)}:{cnt if cnt else 1}"
                    if extra.get("snipe_per"):
                        tag += f":per:{extra['snipe_per']}"
                    if extra.get("snipe_target") == "damaged":
                        # 只挑身上有伤害指示物的目标（第二十七批）。
                        # ⚠ 顺序固定为 `:per:…:target:damaged[:if:…]`（`if` 里可能
                        #   自带冒号，必须放最后）。
                        tag += ":target:damaged"
                    if extra.get("snipe_ex"):
                        # 只挑对手的 Pokémon {ex}（第四十一批）。引擎按
                        # `pdef.is_ex` 判定（Mega ex 的 is_ex 也为 True，符合卡面
                        # 口径：「Pokémon {ex}」包含 Mega ex）。
                        tag += ":target:ex"
                    if extra.get("snipe_if"):
                        tag += f":if:{extra['snipe_if']}"
                    tags.append(tag)
                elif extra.get("recoil"):
                    # 反伤「This Pokémon does N damage to itself.」→ 引擎 `self:N`
                    # （语义即「攻击者自身受到 N 点伤害」，精确对应）。
                    # 全库 49 条此类招式，此前**完全没建模**：卡不进池，或更糟——
                    # 早期版本把它读成「打对手战斗位」，反伤静默消失。
                    if amt is None:
                        unsupported.append({"type": t, "reason": "反伤数值未识别"})
                    else:
                        tag = f"self:{int(amt)}"
                        if extra.get("per_counter"):
                            tag += f":per:{extra['per_counter']}"
                        tags.append(tag)
                else:
                    notes.append("damage 由招式 damage 字段承载，无需 tag")
            elif t == "status":
                st = extra.get("status")
                _src = str(s.get("source_text") or "")
                if st not in ENGINE_STATUS_TAGS:
                    unsupported.append({"type": t, "reason": f"未知状态 {st}"})
                elif extra.get("status2"):
                    # 「is now Burned **and** Confused」（第五十一批）：此前只落第一个
                    # 状态，第二个被**静默丢掉**（Dangerous Laser 只挂烧伤）。
                    _b = extra.get("status2")
                    if _b not in ENGINE_STATUS_TAGS:
                        unsupported.append({"type": t,
                                            "reason": f"未知状态 {_b}"})
                    else:
                        tags.append(f"status_multi:{st}+{_b}")
                elif tgt == "self":
                    # 「This Pokémon is now Asleep/Confused/Poisoned」= **施术者自己**
                    # 陷入状态。引擎用 `status_self:<s>` 精确作用于出招者，
                    # 与恒作用于对手战斗位的裸状态 tag 严格区分。
                    tags.append(f"status_self:{st}")
                elif kind == "ability" and re.search(
                        r"in order to use this ability", _src, re.I) \
                        and not s.get("ability_cost"):
                    # 卡面带发动代价，但**代价句形态没认出来** → 引擎侧无账可付，
                    # 直接放通等于免除代价（Volcarona 实测）。认出来的形态已转成
                    # `ability_cost` 字段（第四十九批），不再走这里。
                    unsupported.append(
                        {"type": t, "reason": "特性发动代价未建模（B4）"})
                else:
                    # 「If you discarded a Pokémon Tool in this way」→
                    # `<status>:if_pre_tool`（第二十五批，N's Joltik）
                    tags.append(st + (":if_pre_tool"
                                      if extra.get("if_pre_tool") else ""))
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
            elif t == "shuffle_both_coin_draw":
                # 双方洗回手牌 + 单次抛币分抽（第四十五批，Harlequin）：
                # 训练家只有**一个**卡级 tag → 四个张数全部塞进同一条。
                tags.append(
                    "shuffle_both_coin_draw:"
                    f"{int(extra.get('mine_heads') or 0)}:"
                    f"{int(extra.get('opp_heads') or 0)}:"
                    f"{int(extra.get('mine_tails') or 0)}:"
                    f"{int(extra.get('opp_tails') or 0)}")
            elif t == "dmg_recoil":
                # 可选加伤 + 反伤（第四十七批，Gurdurr / Dondozo ex）：
                # 加伤在 `_attack_mods` 计入、反伤在 `_apply_attack_effects`
                # 结算；同一 tag 保证「If you do」联动不会被拆散。
                tags.append(f"dmg_plus:{int(amt)}:recoil:{int(extra.get('recoil') or 0)}")
            elif t == "team_shield":
                # 全队限时减伤（第四十六批，Iron Defender）
                _col = str(extra.get("color") or "")
                if amt is None or not _col:
                    unsupported.append({"type": t,
                                        "reason": "全队限时减伤数值/属性未识别"})
                else:
                    tags.append(f"team_shield:reduce:{int(amt)}:type:{_col}")
            elif t == "shield_choose":
                # 单体限时护盾（第四十六批，Acerola's Mischief）：来源/效果/
                # 打出前置条件全部塞进同一条 tag（训练家只有一个卡级 tag）。
                _kind = str(extra.get("kind") or "")
                _from = str(extra.get("from") or "")
                _gate = str(extra.get("gate") or "")
                if _kind != "prevent_all" or not _from or not _gate:
                    unsupported.append({"type": t,
                                        "reason": "单体护盾形态/来源/前置条件未识别"})
                else:
                    _eff = ":effects" if extra.get("effects") else ""
                    tags.append(f"shield_choose:prevent_all:from:{_from}"
                                f"{_eff}:gate:{_gate}")
            elif t == "draw":
                _cond = extra.get("cond")
                if extra.get("split"):
                    # 双方洗回后**各抽不同张数**（第五十九批，Unfair Stamp）
                    _a, _b = extra["split"]
                    tags.append(f"shuffle_draw_split:{int(_a)}:{int(_b)}")
                elif extra.get("cond_extra") is not None:
                    # 条件抽牌（第五十八批）：`plus` / `set` **不可互换**
                    _mode = "shuffle" if extra.get("shuffle_hand") else "keep"
                    _way = "plus" if extra.get("cond_add") else "set"
                    tags.append(
                        f"draw_cond:{_mode}:{int(amt or 0)}:{_way}:"
                        f"{int(extra['cond_extra'])}:if:{_cond}")
                elif extra.get("discard_all"):
                    # 「Discard your hand and draw N cards.」（第五十一批）
                    # 引擎早就有 `research:N`，但解析侧从来没产出过 —— 以前落成
                    # `discard:all` + `draw:N` 两条，训练家多 tag 直接整卡不收。
                    tags.append(f"research:{int(amt or 1)}")
                elif extra.get("each_player"):
                    # B3：双方同抽（Chandelure「Alluring Light」）
                    tags.append(f"draw_both:{int(amt or 1)}")
                elif extra.get("shuffle_opp"):
                    # B3：对手洗回手牌后抽（Gothitelle / Vivillon）
                    tail = ":if_hand" if extra.get("if_hand") else ""
                    tags.append(f"opp_shuffle_draw:{int(amt or 1)}{tail}")
                elif extra.get("draw_to_branch"):
                    # 分支替换的「抽到 N 张」（第九十八批，Ariana）——
                    # 必须先于 `to_hand_size` 判断：本动作的 amount 是 None，
                    # 落到后面会被当成「抽牌量未识别」。
                    _b, _a = extra["draw_to_branch"]
                    tags.append(f"draw_to_branch:{int(_b)}:{int(_a)}"
                                f":if:{extra['cond']}")
                elif extra.get("to_hand_size") is not None:
                    tail = f":if:{_cond}" if _cond else ""
                    tags.append(f"draw_to:{int(extra['to_hand_size'])}{tail}")
                elif extra.get("per_counter"):
                    # B3：按计数源抽（Awakening Drum「for each Ancient Pokémon」）
                    tags.append(f"draw:{int(amt or 1)}:per:{extra['per_counter']}"
                                + (f":if:{_cond}" if _cond else ""))
                elif amt is None:
                    unsupported.append({"type": t, "reason": "抽牌量未识别"})
                elif kind == "ability":
                    if trigger == "on_knockout":
                        tags.append(f"ko_draw:{int(amt)}")
                    else:
                        tags.append(f"draw:{int(amt)}"
                                    + (f":if:{_cond}" if _cond else ""))
                else:
                    tags.append(f"draw:{int(amt)}"
                                + (f":if:{_cond}" if _cond else ""))
            elif t == "heal":
                if extra.get("heal_equal"):
                    tags.append("heal:equal")
                    continue
                if amt is None and extra.get("heal_all"):
                    # 全量回复：`heal:all[:<scope>][:type:<c>]`
                    sc = extra.get("heal_scope") or ""
                    pt = extra.get("heal_type")
                    tail = f":{sc}" if sc else ""
                    if pt:
                        tail += f":type:{pt}"
                    # `:energy` = 回复后把该目标的全部能量收回手牌（Wally's
                    # Compassion）。训练家只有单 tag，故与回复合并在一条 tag 里，
                    # `:energy` 隐含「仅当真的回复过才执行」。
                    if extra.get("maxleft"):
                        # 「… that has N HP or less remaining」（Bianca's Devotion）
                        tail += f":maxleft:{int(extra['maxleft'])}"
                    if extra.get("to_hand"):
                        tail += ":energy"
                    else:
                        tail += (":if_prev" if extra.get("if_prev") else "")
                    tags.append(f"heal:all{tail}")
                elif amt is None:
                    unsupported.append({"type": t, "reason": "回复量未识别"})
                else:
                    # 范围（全体/后备/战斗位/属性限定）必须写进 tag —— 省略 = 回**施术者自身**，
                    # 与「回己方全部」差着一个数量级（见 battle._apply_heal 的历史缺陷说明）
                    sc = extra.get("heal_scope") or ""
                    pt = extra.get("heal_type")
                    tail = f":{sc}" if sc else ""
                    if pt:
                        tail += f":type:{pt}"
                    # `:discard` = 真的回复后弃该目标 1 个能量（Super Potion）
                    # ⚠ 第六十二批：`alt` / `if_name` / `need` 必须排在 `:discard`
                    # **之前** —— `_apply_heal` 只把 `parts[-1]` 当尾缀标记。
                    if extra.get("heal_alt"):
                        tail += f":alt:{int(extra['heal_alt'])}"
                    if extra.get("heal_if_name"):
                        tail += f":if_name:{extra['heal_if_name']}"
                    if extra.get("heal_need"):
                        tail += f":need:{extra['heal_need']}"
                    if extra.get("discard_one"):
                        tail += ":discard"
                    tags.append(f"heal:{int(amt)}{tail}")
            elif t == "attach":
                n = int(amt) if amt is not None else 1
                et = extra.get("energy_type")
                zone = extra.get("zone")
                suffix = f":{et}" if et else ""
                zsuffix = f":{zone}" if zone else ""
                if extra.get("target_scope") == "bench":
                    zsuffix += ":target:bench"
                elif extra.get("target_scope"):
                    # `target:stage2` / `target:any` …,由 `_attach_target_fp` 解释
                    zsuffix += f":target:{extra['target_scope']}"
                if kind == "ability":
                    if trigger == "on_evolve":
                        tags.append(f"evolve_attach:{n}{suffix}"
                                    f"{zsuffix or ':discard'}")
                    else:
                        _heal_tail = (f":heal:{int(extra['heal'])}"
                                      if extra.get("heal") else "")
                        tags.append(f"attach_energy:{n}{suffix}{zsuffix}"
                                    f"{_heal_tail}")
                else:
                    tags.append(f"attach:{n}{suffix}{zsuffix}")
            elif t == "discard":
                n = int(amt) if amt is not None else 1
                if str(tgt or "") == "opponent_hand":
                    # 「Your opponent discards … from their hand」（第五十四批）：
                    # 选择权在**对手**手里 ⇒ 引擎按随机弃（`discard_opp_random`）
                    # 与卡面一致；`opp_hand:` 才是「我方选」，两者不可互换。
                    if extra.get("to") is not None:
                        tags.append(f"discard_opp_to:{int(extra['to'])}")
                    else:
                        tags.append(f"discard_opp_random:{n}")
                elif str(tgt or "") == "both_hand":
                    # 「Each player discards … until they have N cards」
                    # （Hand Trimmer）—— 双方同时弃到剩 N，**不是**只弃对手。
                    tags.append(f"discard_to_both:{int(extra.get('to') or 0)}")
                elif extra.get("from_deck"):
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
                    _het = extra.get("hand_energy_type")
                    if _het:
                        # B3：**带类型**手牌弃能（Lurantis / Ceruledge）
                        tail = f":type:{_het}"
                        if extra.get("required"):
                            tail += ":required"
                        tags.append(f"discard_hand_energy:{n}{tail}")
                    elif is_energy and str(tgt or "") == "hand":
                        # 「Discard (up to) N Energy cards from **your hand**」
                        # （2026-11-02）：`discard_energy:N` 的语义是「先弃战斗位
                        # 宝可梦身上的能量、不足再从手牌补」——用在「从手牌弃」上
                        # 会把代价落到**错误的宝可梦**身上（Wugtrio ex / Mawile /
                        # Mega Clefable ex 三张真实卡曾因此静默弃错对象）。
                        # `discard_hand_energy:N` 只动**手牌**、不足则弃现有的，
                        # 与卡面「up to N from your hand」完全一致。
                        # 必须排在 `all` 之前：「discard all Energy from your hand」
                        # 走 `discard_energy:all` 会去弃场上的能量。
                        tags.append(f"discard_hand_energy:"
                                    f"{99 if extra.get('all') else n}")
                    elif extra.get("all"):
                        if is_energy:
                            # 「discard all Energy from that/those Pokémon」：
                            # `:evolved` = 己方全部进化宝可梦；`:if_prev` = 仅当
                            # 前序动作（回复）真的发生时执行（「in this way」）。
                            tail = side
                            if extra.get("energy_scope"):
                                tail += f":{extra['energy_scope']}"
                            if extra.get("if_prev"):
                                tail += ":if_prev"
                            tags.append("discard_energy:all" + tail)
                        else:
                            tags.append("discard:all")
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
                if extra.get("draw_to") is not None:
                    # 「Switch … If you do, draw cards until you have N」（Surfer）
                    tags.append(f"switch_draw_to:{int(extra['draw_to'])}")
                elif extra.get("status"):
                    # 「Switch in 1 of your opponent's Benched … is now Confused」
                    _st2 = str(extra["status"])
                    if _st2 != "confuse":
                        unsupported.append(
                            {"type": t,
                             "reason": f"换位后施加的状态未建模：{_st2}"})
                    else:
                        tags.append("gust_confuse")
                elif tgt == "self":
                    # B3：后备特性自换位（Linoone「Excited Dash」）需要「场上已有
                    # Mega Evolution Pokémon {ex}」这一前置条件 —— 条件必须进 tag，
                    # 否则特性在没有任何 Mega 的卡组里也能白换位。
                    _src = str(s.get("source_text") or "")
                    tail = ""
                    if re.search(r"mega evolution pok[eé]mon", _src, re.I):
                        tail = ":if:own_mega_in_play"
                    tags.append("switch_self" + tail)
                else:
                    tags.append("gust")
            elif t == "evolve":
                if extra.get("each_bench"):
                    # 逐只后备各进化一次（Reuniclus「Cellular Ascension」）
                    tags.append("evolve_each_bench")
                elif extra.get("each_pick"):
                    # 「选至多 N 只、各进化一次」（Team Rocket's Nidorina）：
                    # `evolve_each_pick:<max>:<scope>` —— 目标是**选出**的 N 只，
                    # 用 each_bench 顶替会把「至多 2 只」变成「全部后备」。
                    _mx = extra.get("each_pick")
                    _sc = str(extra.get("scope") or "")
                    if not isinstance(_mx, int) or _mx < 1:
                        unsupported.append(
                            {"type": t, "reason": f"进化选取上限未识别：{_mx}"})
                    elif not _sc:
                        unsupported.append(
                            {"type": t, "reason": f"进化目标限定未识别：{_sc}"})
                    else:
                        tags.append(f"evolve_each_pick:{_mx}:{_sc}")
                elif extra.get("from_deck") or has_evolve_from_deck:
                    # 第九十二批（2026-11-05）：「a card that **has no Abilities**
                    # and evolves from …」（Salvatore TEF 160）→ 只允许检索**无特性**
                    # 的进化卡。不表达该限定就会搜到带特性的进化卡 = 静默放宽。
                    _src = str(s.get("source_text") or "").lower()
                    if re.search(r"has no abilit", _src):
                        tags.append("evolve_from_deck:no_ability")
                    else:
                        tags.append("evolve_from_deck")
                elif kind in ("item", "supporter"):
                    tags.append("candy")
                else:
                    unsupported.append(
                        {"type": t, "reason": "招式/特性驱动的进化需卡级 tag"})
            elif t == "opp_hand":
                # 对手手牌操作（2026-10-30 第三十二批）：
                # `opp_hand:<op>:<sel>[:<n>|:all]`
                # ⚠ 与 `discard_opp_random:N` / `discard_opp_to:N` /
                # `shuffle_opp_hand:N` **语义相反**：那三个是「对手自选 ⇒
                # 引擎随机」，本 tag 是「我方选 ⇒ 确定性默认（可选 hand_pick）」。
                _op = str(extra.get("op") or "")
                _sel = str(extra.get("sel") or "any")
                _n = extra.get("n", 1)
                if _op not in ("discard", "bottom"):
                    unsupported.append({"type": t,
                                        "reason": f"对手手牌操作未识别：{_op}"})
                elif not isinstance(_n, int) and _n != "all":
                    unsupported.append({"type": t,
                                        "reason": f"对手手牌张数未识别：{_n}"})
                else:
                    _tag = f"opp_hand:{_op}:{_sel}"
                    if _n == "all":
                        _tag += ":all"
                    elif int(_n) != 1:
                        _tag += f":{int(_n)}"
                    tags.append(_tag)
            elif t == "attach_spread":
                # 检索能量并自由分配附着（2026-10-30 第三十三批）：
                # `attach_spread:<n>:<etype>:<zone>[:target:<scope>]`
                _n = extra.get("n")
                _zone = str(extra.get("zone") or "deck")
                _scope = str(extra.get("scope") or "any")
                if not isinstance(_n, int) or _n < 1:
                    unsupported.append({"type": t,
                                        "reason": f"附能张数未识别：{_n}"})
                elif _zone not in ("hand", "discard", "deck", "any"):
                    unsupported.append({"type": t,
                                        "reason": f"附能来源未识别：{_zone}"})
                else:
                    _et = str(extra.get("etype") or "any")
                    _tag = f"attach_spread:{_n}:{_et}:{_zone}"
                    if _scope != "any":
                        _tag += f":target:{_scope}"
                    if extra.get("distinct"):
                        # 「of different types」= 检索约束（2026-11-02 第三十九批）：
                        # 拿到的 N 张必须**属性两两不同**。尾段，引擎先剥再解析 scope。
                        _tag += ":distinct"
                    if extra.get("selfko"):
                        # 「If you use this Ability, this Pokémon is Knocked Out.」
                        # 必须是**尾段**（引擎先剥 `:selfko` 再解析 scope）
                        _tag += ":selfko"
                    tags.append(_tag)
            elif t == "attach_spread_flip":
                # 抛币计数版自由分配附着（Mega Audino ex，2026-11-02 第三十八批）：
                # `attach_spread_flip:<coins>:<per>:<etype>:<zone>[:target:<scope>]`
                # ⚠ 与 `attach_spread:` 分开：前者的张数段是**常量**，本 tag 的
                # 张数由引擎抛 `coins` 枚币、按正面枚数 × `per` 现算。
                _coins = extra.get("coins")
                _per = extra.get("per")
                _zone = str(extra.get("zone") or "deck")
                _scope = str(extra.get("scope") or "any")
                if not isinstance(_coins, int) or _coins < 1 \
                        or not isinstance(_per, int) or _per < 1:
                    unsupported.append({"type": t,
                                        "reason": f"抛币张数未识别：{_coins}x{_per}"})
                elif _zone not in ("hand", "discard", "deck", "any"):
                    unsupported.append({"type": t,
                                        "reason": f"附能来源未识别：{_zone}"})
                else:
                    _et = str(extra.get("etype") or "any")
                    _tag = f"attach_spread_flip:{_coins}:{_per}:{_et}:{_zone}"
                    if _scope != "any":
                        _tag += f":target:{_scope}"
                    tags.append(_tag)
            elif t == "attach_each_bench":
                # 「每只后备各 per 张、绑定给那一只」（Mega Gardevoir ex）：
                # `attach_each_bench:<per>:<etype>:<zone>`
                _per = extra.get("per")
                _zone = str(extra.get("zone") or "deck")
                if not isinstance(_per, int) or _per < 1:
                    unsupported.append({"type": t,
                                        "reason": f"每只后备附能张数未识别：{_per}"})
                elif _zone not in ("hand", "discard", "deck", "any"):
                    unsupported.append({"type": t,
                                        "reason": f"附能来源未识别：{_zone}"})
                else:
                    _et = str(extra.get("etype") or "any")
                    tags.append(f"attach_each_bench:{_per}:{_et}:{_zone}")
            elif t == "attach_spread_multi":
                # 双色「or 1 of each」（Steven's Metagross ex）：
                # `attach_spread_multi:<per1>:<t1>:<per2>:<t2>:<zone>[:target:<scope>]`
                _kinds = extra.get("kinds") or []
                _zone = str(extra.get("zone") or "deck")
                _scope = str(extra.get("scope") or "any")
                if len(_kinds) != 2 or not all(
                        isinstance(k, (list, tuple)) and len(k) == 2
                        and k[0] and isinstance(k[1], int) and k[1] >= 1
                        for k in _kinds):
                    unsupported.append({"type": t,
                                        "reason": f"双色限额未识别：{_kinds}"})
                elif _zone not in ("hand", "discard", "deck", "any"):
                    unsupported.append({"type": t,
                                        "reason": f"附能来源未识别：{_zone}"})
                else:
                    _tag = (f"attach_spread_multi:{_kinds[0][1]}:{_kinds[0][0]}:"
                            f"{_kinds[1][1]}:{_kinds[1][0]}:{_zone}")
                    if _scope != "any":
                        _tag += f":target:{_scope}"
                    tags.append(_tag)
            elif t == "attach_each_pick":
                # 「选至多 N 只、每只各 per 张」（Janine's Secret Art）：
                # `attach_each_pick:<max>:<per>:<etype>:<zone>:target:<scope>`
                _mx = extra.get("max")
                _per = extra.get("per")
                _zone = str(extra.get("zone") or "deck")
                _scope = str(extra.get("scope") or "")
                if not isinstance(_mx, int) or _mx < 1 \
                        or not isinstance(_per, int) or _per < 1:
                    unsupported.append({"type": t,
                                        "reason": f"选取上限未识别：{_mx}x{_per}"})
                elif not _scope or _scope == "any":
                    # 卡面限定「of your {X} Pokémon」；目标集合为「任意己方」时
                    # 无法区分「选中哪几只」，如实拒绝而不是随便挑。
                    unsupported.append({"type": t,
                                        "reason": f"选取目标限定未识别：{_scope}"})
                elif _zone not in ("hand", "discard", "deck", "any"):
                    unsupported.append({"type": t,
                                        "reason": f"附能来源未识别：{_zone}"})
                else:
                    _et = str(extra.get("etype") or "any")
                    _tag = (f"attach_each_pick:{_mx}:{_per}:{_et}:{_zone}"
                            f":target:{_scope}")
                    if extra.get("poison_if_active"):
                        _tag += ":poison_if_active"
                    tags.append(_tag)
            elif t == "attach_flip_until":
                # 「抛到反面为止、按正面数附给自己」（Snorlax）：
                # `attach_flip_until:<etype>:<zone>`
                _zone = str(extra.get("zone") or "deck")
                if _zone not in ("hand", "discard", "deck", "any"):
                    unsupported.append({"type": t,
                                        "reason": f"附能来源未识别：{_zone}"})
                else:
                    tags.append(f"attach_flip_until:{extra.get('etype') or 'any'}"
                                f":{_zone}")
            elif t == "coin_gate_attach":
                # 训练家抛币门（Energy Coin）：`coin_gate_attach:<coins>:<n>:<etype>:<zone>`
                _coins = extra.get("coins")
                _n = extra.get("n")
                _zone = str(extra.get("zone") or "deck")
                if not isinstance(_coins, int) or _coins < 1 \
                        or not isinstance(_n, int) or _n < 1:
                    unsupported.append({"type": t,
                                        "reason": f"抛币门参数未识别：{_coins}x{_n}"})
                elif _zone not in ("hand", "discard", "deck", "any"):
                    unsupported.append({"type": t,
                                        "reason": f"附能来源未识别：{_zone}"})
                else:
                    tags.append(f"coin_gate_attach:{_coins}:{_n}:"
                                f"{extra.get('etype') or 'any'}:{_zone}")
            elif t == "ko_prize":
                # 「对手少拿 N 张奖赏卡」（2026-11-04 第五十批）：
                # `ko_prize_minus:<n|all>[:from_ex][:team_<color>][:if_named:<key>]`
                # ⚠ 与 `ko_prize_plus:`（多拿，挂在取奖赏方）方向相反。
                _n = extra.get("n")
                if _n not in ("all", 1, 2, 3):
                    unsupported.append({"type": t,
                                        "reason": f"奖赏减少张数未识别：{_n}"})
                else:
                    _tag = f"ko_prize_minus:{_n}"
                    if extra.get("from_ex"):
                        _tag += ":from_ex"
                    if extra.get("team"):
                        _tag += f":team_{extra['team']}"
                    if extra.get("if_named"):
                        _tag += f":if_named:{extra['if_named']}"
                    tags.append(_tag)
            elif t == "until_tails":
                # 「Flip a coin until you get tails」+ 非伤害子效果（2026-11-02 第四十批）：
                # `coin_until_tails:mill:opp` / `:shuffle_opp_hand` / `:search:any`
                # —— 张数由引擎在结算时按连续正面数决定，解析期**不折算**
                # （折算成期望值/上限/1 都是静默改强度）。
                _k = str(extra.get("kind") or "")
                if _k == "mill" and str(extra.get("scope") or "opp") == "opp":
                    tags.append("coin_until_tails:mill:opp")
                elif _k == "shuffle_opp_hand":
                    tags.append("coin_until_tails:shuffle_opp_hand")
                elif _k == "search":
                    tags.append("coin_until_tails:search:any")
                elif _k == "discard_energy" \
                        and str(extra.get("scope") or "opp") == "opp":
                    # 对手**战斗位**按正面数弃能（LugiaEX「Hyper Whirlpool」）：
                    # 只有这一种去向，别的 scope 引擎不支持 → 如实未建模。
                    tags.append("coin_until_tails:discard_energy:opp")
                else:
                    unsupported.append(
                        {"type": t, "reason": f"抛到反面为止的子效果未建模：{_k}"
                                              f"/{extra.get('scope')}"})
            elif t == "discard_tool_or_senergy":
                # 「弃对手道具/特殊能量 **或** 弃竞技场」二选一（Blowtorch）：
                # `discard_tool_or_senergy:<n>:<scope>` —— 两支都在引擎侧实现，
                # 因此**不能**拆成 `discard_tool:` + `discard_stadium` 两条
                # （训练家只认一个卡级 tag，多一条整卡就不收）。
                _n = extra.get("n")
                _sc = str(extra.get("scope") or "opp")
                if not isinstance(_n, int) or _n < 1:
                    unsupported.append({"type": t,
                                        "reason": f"弃置张数未识别：{_n}"})
                elif _sc != "opp":
                    unsupported.append({"type": t,
                                        "reason": f"弃置范围未建模：{_sc}"})
                else:
                    tags.append(f"discard_tool_or_senergy:{_n}:{_sc}")
            elif t == "recon":
                # 检视牌库顶（2026-10-30 第二十批；2026-11-02 第三十五批扩展）：
                # `recon:<N>:<K>:<sel>[:dest:<d>][:rest:<r>][:gate:<g>]`
                # 与旧 `recon:<N>`（随机取 1 入手）**不是**同一条语义路径 ——
                # 旧形态无第三段，引擎按老行为（随机 1 张、其余放底）处理。
                _lo = extra.get("look_top")
                _mx = extra.get("max")
                if not _lo or not _mx:
                    unsupported.append({"type": t,
                                        "reason": "检视牌库顶缺少张数/筛选项"})
                else:
                    _sel = str(extra.get("sel") or "any")
                    # `max == "all"` = 「any number of … you find there」：
                    # 备选集合被「牌库顶 N 张」封死，不需要交互层。
                    _k = "all" if _mx == "all" else str(int(_mx))
                    _tag = f"recon:{int(_lo)}:{_k}:{_sel}"
                    _dest = str(extra.get("dest") or "hand")
                    if _dest != "hand":
                        _tag += f":dest:{_dest}"
                    _rest = str(extra.get("rest") or "shuffle")
                    if _rest != "shuffle":
                        _tag += f":rest:{_rest}"
                    if extra.get("gate"):
                        _tag += f":gate:{extra['gate']}"
                    tags.append(_tag)
            elif t == "search":
                # 特性 / 训练家 / 招式**共用**同一套判定：先去向，再类别。
                # 特性侧特有的是「检视牌库顶 N 张取 1」（`recon`），与全牌库检索
                # 语义不同 —— 早期一律映射成 recon，把「Search your deck for a
                # Pokémon」变成"只能从牌堆顶碰运气"（池内实测 16 条）。
                src = str(s.get("source_text") or "")
                _mp = re.search(r"\bsearch\b", src, re.I)
                look_top = extra.get("look_top")
                if look_top and kind != "ability":
                    # ⚠ 训练家/招式卡面上的「Look at the top N cards … 入手」**不是**
                    # 全牌库检索。此前这条路径的 `look_top` 只在特性侧被读取，训练家
                    # 侧会静默退化成正牌库检索（实测 Explorer's Guidance 从「顶部 6
                    # 张取 2、其余弃置」变成「整副牌库任取 1 张、其余不动」）。
                    # 未被本批精确识别的形态一律如实拒绝，不得放大检索范围。
                    unsupported.append(
                        {"type": t,
                         "reason": "检视牌库顶（非特性侧未精确识别，B4）"})
                elif look_top:
                    tags.append(f"recon:{int(look_top)}")
                else:
                    # ⚠ 无 source_text（手工 spec / 上游已确定语义）时不做去向判定 ——
                    # 此时没有「卡面词序」可依据，硬判 unknown 会把明确的 spec 拒掉。
                    dest = (_search_destination(src, _mp.start() if _mp else 0)
                            if src else "hand")
                    what = extra.get("what") or "any"
                    n = amt if amt is not None else 1
                    dynamic_n = re.search(
                        r"\ba number of\b|\bany number of\b|"
                        r"up to the number of|for each", src, re.I)
                    _multi = _multi_search_alts(src)
                    if _multi:
                        # 第七十九批：逐类别各取 1 张（Secret Box）。
                        # 旧口径整卡拒绝；引擎 `search_multi:<sel>:1` 已实现。
                        tags.append(f"search_multi:{'+'.join(_multi)}:1")
                    elif _MULTI_KIND_SEARCH.search(_norm_prose(src)):
                        # 未识别的多类别并列：通用分支只取到第一个类别 →
                        # 张数与类别双错（静默削弱）→ 如实拒绝。
                        unsupported.append(
                            {"type": t, "reason": "多类别并列检索"
                                                  "（逐类别各取 1 张）未建模（B4）"})
                    elif dest == "bench" and re.search(
                            r"any number of basic pok[eé]mon and put them onto "
                            r"your bench", src, re.I):
                        # 第七十九批：Precious Trolley「任意数量基础宝可梦铺后备」。
                        # 备选集合被「牌库里的基础宝可梦」封死、落点受后备上限约束
                        # → 不需要交互层（同 Tatsugiri ex 的 `all` 口径）。
                        tags.append("search_bench:all:basic")
                    elif _mp and _in_coin_branch(src, _mp.start()):
                        # 抛币分支内的检索：应由 coin 层产出（coin:heads:<sub>），
                        # 不能退化成「必定检索」（否则强度翻倍）
                        unsupported.append(
                            {"type": t, "reason": "抛币分支内的检索（需 coin 嵌套，B4）"})
                    elif tgt == "discard":
                        unsupported.append(
                            {"type": t, "reason": "从弃牌堆检索（非回收）需引擎"
                                                  "新机制（B4）"})
                    elif _SEARCH_ANY_DISTINCT.search(src) and what == "energy" \
                            and dest == "hand":
                        # 「any number of Basic Energy cards **of different
                        # types**」（Energy Search Pro SSP 176）：卡面写「任意张」，
                        # 但**备选集合被封死**——每种属性至多 1 张，上限 = 牌库里
                        # 出现的基本能量属性种类数。因此不需要交互层，确定性默认
                        # 「每种可用属性各拿 1 张」就是卡面的最大合法取值。
                        # ⚠ 不能退化成 `search_energy:<n>:distinct`（n 是常数）：
                        # 牌库里有 3 种属性时 n=2 会少拿、n=8 会拿不到。
                        tags.append("search_energy:all:distinct")
                    elif _search_any_named(src) and dest in ("bench", "hand"):
                        # 「any number of <具名> …」（第一百/一百零一批）
                        _san = _search_any_named(src)
                        _tg = None
                        if dest == "bench":
                            _tg = ("search_bench:all"
                                   + (":basic" if _san["basic"] else "")
                                   + f":name_has:{_san['name_has']}")
                        elif _san["kind"] == "trainer":
                            _tg = f"search_trainer:all:name_has:{_san['name_has']}"
                        if _tg:
                            tags.append(_tg)
                        else:
                            unsupported.append(
                                {"type": t, "reason": "具名任意张检索去向未建模（B4）"})
                    elif dynamic_n:
                        # B2 剩余（2026-10-28）：「Search your deck for a number of
                        # cards up to the number of your Benched Pokémon」（Scrafty）
                        # 张数由**计数源**决定且是「至多」→ 可精确求值；
                        # 「any number of」是玩家自由选择 → 仍拒绝（交互层）。
                        _per = _search_per_counter(src)
                        _num_cards = bool(re.search(
                            r"search your deck for (?:up to )?(?:a |any )?"
                            r"number of cards", src, re.I))
                        if _per and _num_cards:
                            tags.append(f"search_any:{n}:per:{_per}")
                        else:
                            unsupported.append(
                                {"type": t, "reason": "检索数量由计数源决定（B4）"})
                    elif re.search(r"energy cards? and discard them", src, re.I):
                        # 「search your deck for up to 3 Basic {F} Energy cards
                        #  **and discard them**」（第一百批，Drilbur「Dig Dig
                        #  Dig」）—— 去向是弃牌堆。不筛选属性就会把任意 3 张
                        # 弃掉（弃错牌库 = 静默改资源）。
                        _tm = re.search(r"\{([a-z])\}", src, re.I)
                        _et = (_EN_ENERGY_LETTER.get(_tm.group(1).upper())
                               if _tm else None)
                        if _et is None:
                            unsupported.append(
                                {"type": t, "reason": "检索弃置的属性未识别（B4）"})
                        else:
                            tags.append(f"search_discard:{int(n)}:energy"
                                        f":type:{_et}")
                    elif dest == "evolve":
                        # 「检索可进化此宝可梦的卡并直接进化」——引擎已有该机制
                        if "evolve_from_deck" not in tags:
                            tags.append("evolve_from_deck")
                    elif dest == "hand":
                        # 类别之外可带**属性/阶段/HP**限定：宝可梦检索已支持这三类
                        # 筛选（引擎 `_poke_matches_filter`）；其它类别没有筛选层，
                        # 放通会放宽检索范围 → 仍拒绝。
                        seg = re.split(r"[.]", src[_mp.start():],
                                       maxsplit=1)[0].lower() if _mp else ""
                        has_type = bool(re.search(r"\{[a-z]\}", seg))
                        has_stage = bool(re.search(
                            r"\bbasic\s+(?:\{[a-z]\}\s+)?pok[eé]mon\b"
                            r"|\bstage 1\b|\bstage 2\b"
                            r"|\bevolution\s+(?:\{[a-z]\}\s+)?pok[eé]mon\b", seg))
                        if what == "pokemon":
                            # B2 剩余（2026-10-28）：`{F} Resistance` 是**抗性**筛选，
                            # 不是属性筛选 —— 必须先判，否则会被下面当成 ptype=fire。
                            rm = re.search(r"\{([a-z])\}\s*resistance", seg)
                            if rm:
                                rt = _EN_ENERGY_LETTER.get(rm.group(1).upper())
                                if rt is None:
                                    unsupported.append(
                                        {"type": t,
                                         "reason": "检索抗性符号未识别（B4）"})
                                else:
                                    tags.append(
                                        f"search_pokemon:{n}:resistance:{rt}")
                            else:
                                pm = re.search(r"\{([a-z])\}", seg)
                                pt = (_EN_ENERGY_LETTER.get(pm.group(1).upper())
                                      if pm else None)
                                if pm and pt is None:
                                    unsupported.append(
                                        {"type": t, "reason": "检索属性符号未识别（B4）"})
                                else:
                                    tail = f":type:{pt}" if pt else ""
                                    if re.search(r"\bbasic\s+(?:\{[a-z]\}\s+)?pok[eé]mon\b",
                                                 seg):
                                        tail += ":stage:basic"
                                    elif re.search(r"\bstage 1\b", seg):
                                        tail += ":stage:stage1"
                                    elif re.search(r"\bstage 2\b", seg):
                                        tail += ":stage:stage2"
                                    elif re.search(r"\bevolution\s+(?:\{[a-z]\}\s+)?"
                                                   r"pok[eé]mon\b", seg):
                                        tail += ":stage:evolution"
                                    hm = re.search(r"(\d+)\s*HP\s+or\s+less", seg, re.I)
                                    if hm:
                                        tail += f":hp:{hm.group(1)}"
                                    tags.append(f"search_pokemon:{n}{tail}")
                        elif what == "energy":
                            # B2 剩余：「up to 2 Basic {P} Energy cards」（Aromatisse）/
                            # 「up to 7 Basic {R} Energy cards」（Firebreather）
                            # —— 只认**紧邻 Energy** 的属性符号。
                            pm = re.search(r"\{([a-z])\}\s*energy", seg)
                            pt = (_EN_ENERGY_LETTER.get(pm.group(1).upper())
                                  if pm else None)
                            if pm and pt is None:
                                unsupported.append(
                                    {"type": t, "reason": "检索属性符号未识别（B4）"})
                            else:
                                tags.append(f"search_energy:{n}"
                                            + (f":type:{pt}" if pt else "")
                                            + (":distinct" if extra.get("distinct")
                                               else ""))
                        elif has_type or has_stage:
                            unsupported.append(
                                {"type": t,
                                 "reason": "检索带属性/阶段限定（引擎检索无该筛选，B4）"})
                        elif what == "trainer":
                            tags.append(f"search_trainer:{n}")
                        else:
                            tags.append(f"search_any:{n}")
                    elif dest == "bench":
                        # 「检索基础宝可梦直接铺后备」——引擎有该去向（search_bench）
                        seg = re.split(r"[.]", src[_mp.start():],
                                       maxsplit=1)[0].lower() if _mp else ""
                        pm = re.search(r"\{([a-z])\}", seg)
                        pt = _EN_ENERGY_LETTER.get(pm.group(1).upper()) if pm else None
                        if pm and pt is None:
                            unsupported.append(
                                {"type": t, "reason": "检索属性符号未识别（B4）"})
                        else:
                            tail = ":basic" if re.search(
                                r"\bbasic pok[eé]mon\b", seg) else ""
                            if pt:
                                tail += f":type:{pt}"
                            # 指定卡名（「a Shedinja」/「a Silcoon or a Cascoon」）
                            # 必须带进 tag —— 否则会从牌库拿**任意**基础宝可梦，
                            # 等于把精确检索放大成泛检索（静默拿错卡）。
                            _nm = re.search(
                                r"for (?:a|an) ([a-z][a-z'\u2019 ]{2,24}?)"
                                r"(?: or (?:a|an) ([a-z][a-z'\u2019 ]{2,24}?))?"
                                r" and put (?:it|them) onto (?:your|their) bench",
                                seg)
                            if _nm and not re.search(
                                    r"pok[eé]mon|energy|trainer|card",
                                    _nm.group(0)):
                                keys = [_tag_name_key(x)
                                        for x in (_nm.group(1), _nm.group(2))
                                        if x]
                                keys = [k for k in keys if k]
                                if keys:
                                    tail += ":name:" + "+".join(keys)
                            tags.append(f"search_bench:{n}{tail}")
                    elif dest == "attach":
                        # 「检索能量**并附着**」——去向是附着，不是入手。
                        # 必须把区域钉死为牌库（zone=deck）：默认 `any` 会先翻手牌，
                        # 从手牌拿一张能量"附"上去，等于放宽了代价。
                        seg = re.split(r"[.]", src[_mp.start():],
                                       maxsplit=1)[0].lower() if _mp else ""
                        # ⚠ 只认「{X} **Energy**」这种紧邻写法：卡面
                        # 「an Energy card and attach it to 1 of your Benched
                        #  {G} **Pokémon**」里的 {G} 修饰的是**目标宝可梦**，
                        # 不是能量属性——按 `\{.\}` 取会把它当成"草能量"。
                        pm = re.search(r"\{([a-z])\}\s*energy", seg)
                        pt = _EN_ENERGY_LETTER.get(pm.group(1).upper()) if pm else None
                        # 「If you attached Energy to a Pokémon in this way, place
                        # N damage counters on that Pokémon.」（Toxtricity）——
                        # 附能成功才放置，因此并入同一条 tag 的 `:counters:`。
                        _ctr = re.search(
                            r"if you attached energy to a pok[eé]mon in this "
                            r"way, place (\d+) damage counters? on that pok[eé]mon",
                            src, re.I)
                        _ctr_tail = (f":counters:{int(_ctr.group(1)) * 10}"
                                     if _ctr else "")
                        # 「an Energy card」（未写 Basic）= 任意能量卡（含特殊能量，
                        # Shaymin「Send Flowers」）→ `:anyenergy` 让引擎允许特殊能量。
                        if re.search(r"\ban energy card|\bany energy card", seg):
                            _ctr_tail += ":anyenergy"
                        # 前缀按**载体**区分：特性侧必须是 `attach_energy:`
                        # （特性白名单只收该前缀），招式侧用 `attach:`。
                        _ap = ("attach_energy" if kind == "ability" else "attach")
                        if pm and pt is None:
                            unsupported.append(
                                {"type": t, "reason": "附能属性符号未识别（B4）"})
                        elif re.search(r"\bbenched\b", seg):
                            # 「attach … to 1 of your **Benched** Pokémon」：
                            # 目标落点必须写进 tag（2026-10-28 第十五批）。
                            # 「Benched {G} Pokémon」还要带属性限定。
                            _bt = re.search(r"benched\s+\{([a-z])\}\s*pok",
                                            seg)
                            _color = (_EN_ENERGY_LETTER.get(_bt.group(1).upper())
                                      if _bt else None)
                            _extra_t = (f":target:bench_type:{_color}"
                                        if _color else ":target:bench")
                            if _bt and _color is None:
                                unsupported.append(
                                    {"type": t,
                                     "reason": "附能目标属性符号未识别（B4）"})
                            else:
                                tags.append(f"{_ap}:{n}"
                                            + (f":{pt}" if pt else "")
                                            + f":deck{_extra_t}{_ctr_tail}")
                        elif re.search(r"in any way you like|to your pok[eé]mon",
                                       seg) and not re.search(
                                           r"to (?:this|1 of your|your active)",
                                           seg):
                            # 「distribute among your Pokémon in any way you like」
                            # 要玩家分配 → 引擎无该选择界面
                            unsupported.append(
                                {"type": t,
                                 "reason": "检索附能需自行分配目标（B4）"})
                        else:
                            # 「attach it to 1 of your Pokémon」= 己方任意一只
                            # （战斗位或后备），位置码由调用方指定。
                            _to_any = bool(re.search(r"to 1 of your pok",
                                                     seg))
                            tags.append(
                                f"{_ap}:{n}" + (f":{pt}" if pt else "")
                                + ":deck" + (":target:any" if _to_any else "")
                                + _ctr_tail)
                    elif dest == "deck_top":
                        # 第七十九批：检索 N 张后**放回牌库顶**
                        # （Ciphermaniac's Codebreaking / Dialga「Time Manipulation」）。
                        # 去向必须与「入手」区分：拿进手牌会凭空多 N 张手牌（静默加强）。
                        # 只认明确写「on top of it/your deck」的形态；「放牌库底」
                        # 仍是未建模（引擎没有 bottom 检索）。
                        if re.search(r"put those cards on top of "
                                     r"(?:it|your deck)", src, re.I):
                            tags.append(f"search_top:{n}")
                        else:
                            unsupported.append(
                                {"type": t, "reason": "检索后放到牌库顶/底（B4）"})
                    else:
                        # 第八十三批：检索后**弃置**（去向 = 弃牌堆）。
                        # 只认训练家卡级 + 「up to N 任意卡」的整句，类别筛选
                        # （Basic {F} Energy）与上场触发钩子都不在此处冒充。
                        _sd = (None if kind not in ("item", "supporter")
                               or str(extra.get("what") or "any") != "any"
                               else _SEARCH_DISCARD_RE.fullmatch(
                                   _norm_prose(src)))
                        if _sd:
                            tags.append(f"search_discard:{int(_sd.group(1))}")
                        else:
                            unsupported.append(
                                {"type": t, "reason": "检索去向不可判（B4）"})
            elif t == "recover":
                if extra.get("coin_heads") is not None:
                    # B3：张数由抛币决定（Stoutland「Flip 3 coins …」）
                    tags.append(f"recover:heads:{int(extra['coin_heads'])}")
                else:
                    n = int(amt) if amt is not None else 1
                    kd = extra.get("kind") or "any"
                    _pt = extra.get("ptype")
                    tags.append(f"recover:{n}:{kd}"
                                + (f":type:{_pt}" if _pt else ""))
            elif t == "switch_choose":
                # `switch_choose:<type>[:except:<key>][:then:<status>]`
                # （第一百一十一批，Pecharunt ex）—— 目标由玩家指定
                # （`action["bench"]`），引擎按后备位置逐条列合法动作。
                _ty = str(extra.get("type") or "any")
                _tg = f"switch_choose:{_ty}"
                if extra.get("except"):
                    _tg += f":except:{extra['except']}"
                if extra.get("then"):
                    if extra["then"] not in ENGINE_STATUS_TAGS:
                        unsupported.append(
                            {"type": t, "reason": f"换入后状态未识别：{extra['then']}"})
                        _tg = None
                    else:
                        _tg += f":then:{extra['then']}"
                if _tg:
                    tags.append(_tg)
            elif t == "rule":
                # ---- 第一百三十四批（整句规则，先于其它 extra 判定）----
                if extra.get("info_reveal_hand"):
                    # 完美信息等价（不改变状态）：仍需产出 tag，否则
                    # 「整句展示手牌」会被当成未建模而整卡拒收（Hoothoot 三张）。
                    tags.append("info:reveal_hand")
                    continue
                if extra.get("counters_double"):
                    tags.append("counters_double:opp")
                    continue
                if extra.get("atk_energy_block") is not None:
                    tags.append(f"atk_energy_block:"
                                f"{int(extra['atk_energy_block'])}")
                    continue
                if extra.get("win_if_prizes") is not None:
                    tags.append(f"win_if:own_prizes_eq:"
                                f"{int(extra['win_if_prizes'])}")
                    continue
                if extra.get("optional_energy_cost"):
                    _n, _ty, _st = extra["optional_energy_cost"]
                    tags.append(f"choose:optional_energy_cost:{int(_n)}:"
                                f"{_ty}:then:{_st}")
                    continue
                if extra.get("choose_discard_then_draw_to") is not None:
                    # 「弃任意数量手牌」→ 先挂起选择，选完再抽到 N 张
                    tags.append(f"choose:discard_hand:any:then:draw_to:"
                                f"{int(extra['choose_discard_then_draw_to'])}")
                    continue
                if extra.get("recon_bottom") is not None:
                    tags.append(f"recon_bottom:{int(extra['recon_bottom'])}")
                    continue
                if extra.get("discard_tool_senergy") is not None:
                    tags.append(f"discard_tool_senergy:"
                                f"{int(extra['discard_tool_senergy'])}")
                    continue
                if extra.get("dmg_set_cond"):
                    _n0, _c0 = extra["dmg_set_cond"]
                    tags.append(f"dmg_set:{int(_n0)}"
                                + (f":if:{_c0}" if _c0 else ""))
                    continue
                if extra.get("self_atk_set_next"):
                    _n0, _k0 = extra["self_atk_set_next"]
                    tags.append(f"self_attack_set_next:{int(_n0)}:name:{_k0}")
                    continue
                if extra.get("discard_stadium"):
                    tags.append("discard_stadium")
                    continue
                if extra.get("use_only"):
                    tags.append(f"use_only:{extra['use_only']}")
                    continue
                if extra.get("use_not"):
                    tags.append(f"use_not:{extra['use_not']}")
                    continue
                if extra.get("dmg_plus_prev_taken"):
                    # 「this attack does that much more damage」（第一百零一批，
                    # Mega Heracross ex）—— 数值 = 攻击方**上回合**受到的招式伤害。
                    tags.append("dmg_plus_prev_taken")
                    continue
                # 复合动作（第九十五批）：一次卡面文本 = 多条 tag 的**有序**序列，
                # 用 `|` 拼成**单条** tag（训练家/特性都只认一个卡级 tag；
                # 拆成两条会被整卡拒收或只执行第一条）。
                if extra.get("multi"):
                    _m = [str(x) for x in extra["multi"] if x]
                    if _m:
                        tags.append("|".join(_m))
                    continue
                # 第八十二批：Sacred Ash 的整句认领。只替换**泛化占位**那条
                # rule（别的 rule 分支已带语义标志，不覆盖）；且必须是训练家
                # 卡级 spec，招式里的同形句式不在此处认领。
                _rsrc = _norm_prose(str(s.get("source_text") or ""))
                _is_placeholder = str(extra.get("note") or "").startswith(
                    "静态/未建模")
                _cth = (None if kind != "attack" else
                        _COUNTERS_TO_HP_RE.fullmatch(_rsrc))
                _ptd = (None if kind not in ("item", "supporter") else
                        _POKEMON_TO_DECK_RE.fullmatch(_rsrc))
                if _cth and _is_placeholder:
                    # 第八十四批：逐只放到「剩余 HP = N」。
                    _scope = ("opp_bench" if "benched" in _cth.group("scope")
                              else "opp_active")
                    tags.append(f"counters_to_hp:{int(_cth.group('hp'))}:"
                                f"{_scope}")
                elif _ptd and _is_placeholder:
                    tags.append(f"pokemon_to_deck:{int(_ptd.group(1))}")
                elif extra.get("prize_bonus"):
                    # 第八十九批：训练家本回合多拿奖赏（Briar）。`:tera` = 攻击
                    # 方须为 Tera 宝可梦；`:nteam` = 须为 N's 宝可梦。
                    tags.append(f"prize_bonus:{int(extra['prize_bonus'])}:"
                                f"{extra.get('scope') or 'tera'}")
                elif extra.get("ko_prize_plus"):
                    # 被击倒触发：多取奖赏（Togekiss「Wonder Kiss」/
                    # Hydreigon ex「Greedy Eater」）。`:opp_basic` = 被击倒的
                    # 是对手基础宝可梦（任意位置）才触发；`:coin` = 抛币命中。
                    _suf = ""
                    if extra.get("opp_basic"):
                        _suf += ":opp_basic"
                    if extra.get("coin"):
                        _suf += ":coin"
                    tags.append(f"ko_prize_plus:{int(extra['ko_prize_plus'])}"
                                + _suf)
                elif extra.get("on_ko_counter"):
                    # 自身被击倒时反制攻击方（Maractus）
                    tags.append(f"on_ko:counter:{int(extra['on_ko_counter'])}")
                elif extra.get("on_ko_energy"):
                    # 被击倒时把该宝可梦身上的基础能量收回手牌（Huntail）
                    tags.append(f"on_ko:energy_to_hand:{extra['on_ko_energy']}")
                elif extra.get("nullify_if"):
                    # 招式失败门：条件成立则整招不结算
                    tags.append(f"nullify_if:{extra['nullify_if']}")
                elif extra.get("borrow"):
                    # 借用招式（"use it as this attack"）：来源由 scope 限定
                    tags.append(f"borrow_attack:{extra['borrow']}")
                elif extra.get("attack_denial"):
                    # 「if … tries to use an attack, … coin. If tails, that attack
                    # doesn't happen」→ 下回合该宝可梦每次攻击前抛币
                    tags.append(f"attack_denial:{extra['attack_denial']}:turn")
                elif extra.get("attack_cost_up") or extra.get("retreat_cost_up"):
                    n1 = int(extra.get("attack_cost_up") or 0)
                    n2 = int(extra.get("retreat_cost_up") or 0)
                    if n1:
                        tags.append(f"attack_cost_up:opp:turn:{n1}")
                    if n2:
                        tags.append(f"retreat_cost_up:opp:turn:{n2}")
                elif extra.get("ignore_effects"):
                    tags.append("ignore_effects")
                elif extra.get("ignore_effects_wr"):
                    # 第六十五批：连弱点/抗性一起无视（与上面窄口径反义）
                    tags.append("ignore_effects:wr")
                elif extra.get("shuffle_top"):
                    # 「Look at the top N cards … put them back in any order」
                    tags.append(f"shuffle_top:{int(extra['shuffle_top'])}"
                                f":{extra.get('side') or 'own'}")
                elif extra.get("fossil"):
                    # 化石卡：当作 N-HP 基础宝可梦上场（`fossil:<hp>:<ptype>`）
                    tags.append(f"fossil:{int(extra.get('hp') or 0)}:"
                                f"{extra.get('ptype') or 'colorless'}")
                elif extra.get("discard_opp_random"):
                    tags.append(f"discard_opp_random:"
                                f"{int(extra['discard_opp_random'])}")
                elif extra.get("shuffle_opp_hand"):
                    tags.append(f"shuffle_opp_hand:"
                                f"{int(extra['shuffle_opp_hand'])}")
                elif extra.get("discard_opp_to"):
                    tags.append(f"discard_opp_to:{int(extra['discard_opp_to'])}")
                elif extra.get("opp_shuffle_same"):
                    tags.append("opp_shuffle_draw:same")
                elif extra.get("energy_move"):
                    # S6 能量移动族
                    tags.append(f"energy_move:{extra['energy_move']}")
                elif extra.get("bounce"):
                    # S6 回手族（回手 / 洗回牌库 / 弃牌堆铺后备）
                    tags.append(f"bounce:{extra['bounce']}")
                elif extra.get("discard_stadium"):
                    # S6 弃场地族
                    tags.append("discard_stadium")
                elif extra.get("energy_to_deck"):
                    # 弃牌堆基本能量洗回牌库（第二十八批，Kyogre / Sinistcha ex）
                    _ed = extra["energy_to_deck"]
                    tags.append(f"energy_to_deck:{_ed.get('color', 'any')}:"
                                f"{_ed.get('n', 'all')}")
                elif extra.get("energy_self_to_deck"):
                    # 自身附着能量洗回牌库（第四十一批，Mega Skarmory ex）
                    tags.append("energy_self_to_deck:all")
                elif extra.get("shuffle_opp_pokemon"):
                    # 对手宝可梦（连同附着卡）洗回其牌库（第四十二批 · 抛币门）：
                    # Sylveon「Benched」→ opp_bench；Shiftry（含战斗位）→ opp。
                    tags.append("coin:heads:shuffle_opp_pokemon:"
                                f"{extra['shuffle_opp_pokemon']}")
                elif extra.get("pre_discard_energy_special"):
                    # 伤害前阶段弃对手战斗位**特殊能量**（第二十五批，Mow Rotom）
                    _pe = extra["pre_discard_energy_special"]
                    tags.append(f"pre:discard_energy:{_pe.get('n', 'all')}:"
                                f"{_pe.get('side', 'opp')}:special")
                elif extra.get("pre_discard_tool"):
                    # 「Before doing damage, discard all Pokémon Tools …」
                    _pt = extra["pre_discard_tool"]
                    tags.append(f"pre:discard_tool:{_pt.get('n', 'all')}"
                                f":{_pt.get('scope', 'opp_active')}")
                elif extra.get("discard_energy_special"):
                    # 弃对手**特殊能量卡**（第二十四批）；`:any` = 目标含后备
                    _de = extra["discard_energy_special"]
                    tags.append(f"discard_energy:{_de.get('n', 1)}:"
                                f"{_de.get('side', 'opp')}:special"
                                + (":any" if _de.get("any") else ""))
                elif extra.get("discard_tool"):
                    _dt = extra["discard_tool"]
                    tags.append(f"discard_tool:{_dt.get('n', 1)}"
                                f":{_dt.get('scope', 'both')}")
                elif extra.get("weak_change"):
                    f = extra.get("filter")
                    tags.append("weak_change:" + str(extra["weak_change"])
                                + (f":{f}" if f else ""))
                elif extra.get("ignore_weakness") or \
                        extra.get("ignore_resistance"):
                    # 招式无视弱点/抗性（引擎在伤害结算时跳过对应步骤）
                    if kind != "attack":
                        unsupported.append(
                            {"type": t,
                             "reason": "弱点/抗性无视仅支持招式侧（B5）"})
                    else:
                        if extra.get("ignore_weakness"):
                            tags.append("ignore_weakness")
                        if extra.get("ignore_resistance"):
                            tags.append("ignore_resistance")
                else:
                    unsupported.append(
                        {"type": t,
                         "reason": (str(extra.get("note"))
                                    or "静态规则/元游戏效果（P1–P2 批次）")})
            # ---- v1.4 修正器（抛币 / 伤害缩放 / 护盾 / 被动） ----
            elif t == "coin_flip":
                side = extra.get("side") or "heads"
                sub = extra.get("sub") or ""
                def _comp(sub_tag: str) -> str:
                    # 复合子效果（共用同一次抛币）：`dmg_plus:30|heal:30`
                    more = extra.get("sub_more") or []
                    return "|".join([sub_tag] + [str(x) for x in more]) if more \
                        else sub_tag
                if extra.get("mode") == "until_tails":
                    # 「Flip a coin until you get tails … for each heads」
                    sub_tag = _comp(_coin_sub_tag(sub, extra.get("sub_amount")))
                    tags.append(f"coin_until_tails:{sub_tag}")
                elif extra.get("mode") == "fork":
                    # 同一枚币的双分支（2026-11-03 第四十四批）：
                    # `coin_flip:heads:<a>|tails:<b>` —— 只抛一次。
                    # ⚠ 招式侧没有「单次抛币双分支」的执行器（attack 的 coin 层
                    # 每个 tag 各抛一次），不能落成两条 `coin:`（= 两次抛币）。
                    sub_h = _coin_sub_tag(sub, None)
                    sub_t = _coin_sub_tag(extra.get("sub_tails") or "", None)
                    if not sub_h or not sub_t:
                        unsupported.append({"type": t,
                                            "reason": "抛币双分支子效果未识别"})
                    elif kind in ("item", "supporter", "ability"):
                        tags.append(f"coin_flip:heads:{sub_h}|tails:{sub_t}")
                    else:
                        unsupported.append({
                            "type": t,
                            "reason": "招式侧单次抛币双分支未建模（B4）"})
                elif extra.get("mode") == "per_target":
                    # 逐目标各抛一枚（第四十五批，Mega Zygarde ex）：
                    # 每个目标独立 50%，正面才打它 → `coin_per_target:N:scope:opp`。
                    _ptn = int(extra.get("sub_amount") or 0)
                    if _ptn <= 0:
                        unsupported.append({"type": t,
                                            "reason": "逐目标抛币伤害数值未识别"})
                    elif kind != "attack":
                        unsupported.append({"type": t,
                                            "reason": "非招式侧的逐目标抛币未建模（B4）"})
                    else:
                        tags.append(f"coin_per_target:{_ptn}:scope:opp")
                elif extra.get("mode") == "each":
                    counter = extra.get("counter") or "opp_bench"
                    sub_tag = _comp(_coin_sub_tag(sub, extra.get("sub_amount")))
                    tags.append(f"coin_each:{counter}:{side}:{sub_tag}")
                elif extra.get("mode") == "all":
                    # 「Flip N coins. If **both of them** are heads, …」（Bewear）
                    counter = extra.get("counter") or "2"
                    sub_tag = _comp(_coin_sub_tag(sub, extra.get("sub_amount")))
                    tags.append(f"coin_all:{counter}:{side}:{sub_tag}")
                elif sub:
                    sub_tag = _comp(_coin_sub_tag(sub, extra.get("sub_amount")))
                    tags.append(f"coin:{side}:{sub_tag}")
                else:
                    unsupported.append({"type": t, "reason": "抛币子效果未识别"})
            elif t == "damage_mod":
                mode = extra.get("mode") or "plus"
                if mode == "minus_per":
                    if not extra.get("per"):
                        unsupported.append({"type": t,
                                            "reason": "缩放计数源未识别"})
                    else:
                        # 「does N less damage for each X」→ `dmg_minus:N:per:<counter>`
                        tags.append(f"dmg_minus:{int(amt or 0)}:per:"
                                    f"{extra['per']}")
                elif mode == "plus_per":
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
                        # 基础伤害按计数源重算（不是加伤）：`dmg_set:N:per:<counter>`
                        tags.append(f"dmg_set:{int(amt or 0)}:per:"
                                    f"{extra['per']}")
                elif mode == "plus_if":
                    if not extra.get("cond"):
                        unsupported.append({"type": t, "reason": "条件未识别"})
                    else:
                        tags.append(f"dmg_plus:{int(amt or 0)}:if:"
                                    f"{extra['cond']}")
                else:
                    tags.append(f"dmg_plus:{int(amt or 0)}")
            elif t == "attack_lock":
                # 跨回合攻击封锁：`attack_lock:<scope>:<until>[:<attack>]`
                #   scope=all      自身全部招式
                #   scope=opp_all  对手战斗位全部招式
                #   scope=name     自身指定招式（until=turn/active）
                scope_ = extra.get("scope") or ""
                until_ = extra.get("until") or "turn"
                if until_ not in ("turn", "active"):
                    unsupported.append({"type": t, "reason": "攻击封锁时限未识别"})
                elif scope_ == "all":
                    tags.append("attack_lock:all:turn" if tgt == "self"
                                else "attack_lock:opp_all:turn")
                elif scope_ == "name":
                    atk_name = str(extra.get("attack") or "").strip()
                    if tgt != "self":
                        unsupported.append({"type": t,
                                            "reason": "对手指定招式的封锁（B4）"})
                    elif not atk_name:
                        unsupported.append({"type": t,
                                            "reason": "攻击封锁缺少招式名"})
                    else:
                        # tag 以 `:` 分段；招式名中的冒号极少见，替换成空格
                        safe = atk_name.replace(":", " ").strip()
                        tags.append(f"attack_lock:name:{until_}:{safe}")
                elif scope_ == "opp_name":
                    # 锁对手战斗位的**指定**招式；招式名在出招时由
                    # action['lock_attack'] 运行时选择 → 解析时无需携带。
                    if until_ != "turn":
                        unsupported.append({"type": t,
                                            "reason": "攻击封锁时限未识别"})
                    elif tgt != "active_opponent":
                        unsupported.append({"type": t,
                                            "reason": "攻击封锁目标未识别"})
                    else:
                        tags.append("attack_lock:opp_name:turn")
                else:
                    unsupported.append({"type": t, "reason": "攻击封锁范围未识别"})
            elif t == "retreat_lock":
                # `retreat_lock:<self|opp>:<until>`
                if tgt not in ("active_opponent", "self"):
                    unsupported.append({"type": t,
                                        "reason": "撤退封锁目标未识别"})
                elif extra.get("until") != "turn":
                    unsupported.append({"type": t,
                                        "reason": "撤退封锁时限未识别"})
                else:
                    side = "opp" if tgt == "active_opponent" else "self"
                    tags.append(f"retreat_lock:{side}:turn")
            elif t == "play_lock":
                # `play_lock:<item|evolution>:opp:turn`
                play_ = extra.get("play")
                if tgt != "active_opponent":
                    unsupported.append({"type": t,
                                        "reason": "手牌禁打目标未识别"})
                elif play_ not in ("item", "evolution", "supporter",
                                   "stadium"):
                    unsupported.append({"type": t,
                                        "reason": "手牌禁打类别未识别"})
                elif extra.get("until") != "turn":
                    unsupported.append({"type": t,
                                        "reason": "手牌禁打时限未识别"})
                else:
                    tags.append(f"play_lock:{play_}:opp:turn"
                                + (":if_prev" if extra.get("if_prev") else ""))
            elif t == "shield":
                kind_ = extra.get("kind")
                if kind_ == "reduce":
                    tags.append(f"shield:reduce:{int(amt or 0)}")
                elif kind_ == "prevent_all":
                    # 来源/门槛限定（`:from:…` / `:if_damage:le:N`）必须带进 tag：
                    # 丢掉限定等于把「只挡某类攻击」放大成「挡全部」（静默错算）。
                    tail = ""
                    if extra.get("qual"):
                        tail += ":" + str(extra["qual"])
                    if extra.get("effects"):
                        tail += ":effects"
                    tags.append("shield:prevent_all" + tail)
                else:
                    unsupported.append({"type": t, "reason": "护盾类型未识别"})
            elif t == "stadium":
                k = extra.get("kind")
                if k == "hp_plus" and amt is not None and extra.get("stage") is not None:
                    tags.append(f"stadium:hp_plus:{int(amt)}:stage:{extra['stage']}")
                elif k == "dmg_reduce" and amt is not None and extra.get("faction"):
                    # 阵营限定减伤（Granite Cave）：`stadium:dmg_reduce:30:faction:stevens`
                    tags.append(f"stadium:dmg_reduce:{int(amt)}:faction:"
                                f"{extra['faction']}")
                elif k == "attack_plus" and amt is not None and extra.get("faction"):
                    # 阵营限定加伤（Postwick）
                    tags.append(f"stadium:attack_plus:{int(amt)}:faction:"
                                f"{extra['faction']}")
                elif k == "no_retreat" and extra.get("faction"):
                    tags.append(f"stadium:no_retreat:faction:{extra['faction']}")
                elif k == "no_tool_effect":
                    tags.append("stadium:no_tool_effect")
                elif k == "status_immune" and extra.get("require") == "energy":
                    tags.append("stadium:status_immune:require:energy")
                elif k == "evolve_same_turn" and extra.get("ptype"):
                    tags.append(f"stadium:evolve_same_turn:type:{extra['ptype']}")
                elif k == "dmg_immune" and extra.get("box") and extra.get("src"):
                    tags.append(f"stadium:dmg_immune:box:{extra['box']}"
                                f":from:{extra['src']}")
                elif k == "bench_limit" and amt is None and extra.get("n"):
                    # Area Zero Underdepths：`stadium:bench_limit:8:require:tera[:trim:5]`
                    tail = f"stadium:bench_limit:{int(extra['n'])}"
                    if extra.get("require"):
                        tail += f":require:{extra['require']}"
                    if extra.get("trim"):
                        tail += f":trim:{int(extra['trim'])}"
                    tags.append(tail)
                elif k == "no_bench_counters":
                    tags.append("stadium:no_bench_counters")
                elif k == "tera_cost_up" and amt is not None:
                    # `stadium:tera_cost_up:1`（第一百零六批，Nighttime Mine）
                    tags.append(f"stadium:tera_cost_up:{int(amt)}")
                elif k == "keep_confuse":
                    # `stadium:keep_confuse`（第一百零六批，Dizzying Valley）
                    tags.append("stadium:keep_confuse")
                elif k == "dmg_reduce" and amt is not None and extra.get("ptype"):
                    tags.append(f"stadium:dmg_reduce:{int(amt)}:type:{extra['ptype']}")
                elif k in ("poison_extra", "on_bench_basic") and amt is not None \
                        and extra.get("ptype"):
                    tags.append(f"stadium:{k}:{int(amt)}:type:{extra['ptype']}")
                elif k == "use" and extra.get("use_kind"):
                    tail = ""
                    if amt is not None:
                        tail += f":{int(amt)}"
                    if extra.get("ptype"):
                        tail += f":type:{extra['ptype']}"
                    if extra.get("require"):
                        tail += f":require:{extra['require']}"
                    if extra.get("name_key"):
                        tail += f":name:{extra['name_key']}"
                    if extra.get("cost"):
                        tail += f":cost:{extra['cost']}"
                    if extra.get("end_turn"):
                        tail += ":end:1"
                    if extra.get("chain"):
                        tail += ":chain:1"
                    tags.append(f"stadium:use:{extra['use_kind']}{tail}")
                elif k == "ability_block" and extra.get("scope"):
                    # 「no Abilities」静态封锁（fx 族）：引擎靠 battle.stadium["name"]
                    # 在 ability_blocked 钩子里具体判定范围/例外
                    tags.append(f"stadium:ability_block:{extra['scope']}")
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
                elif k == "dmg_plus" and amt is not None:
                    # 道具静态加伤（Maximum Belt / Light Ball / Binding Mochi /
                    # Brave Bangle）：限定已由解析侧拼成 tail
                    tags.append(f"tool:dmg_plus:{int(amt)}{extra.get('tail') or ''}")
                elif k in ("hp_plus", "retreat_less") and amt is not None:
                    if k == "retreat_less" and extra.get("low_hp"):
                        tags.append(f"tool:retreat_less:{int(amt)}"
                                    f":lowhp:{int(extra['low_hp'])}")
                    elif k == "hp_plus" and extra.get("holder_name"):
                        # `tool:hp_plus:<n>:holder:name:<k>`（第一百三十四批）
                        # 只对**指定阵营**的持有者生效（Cynthia's Power Weight）。
                        tags.append(f"tool:hp_plus:{int(amt)}"
                                    f":holder:name:{extra['holder_name']}")
                    else:
                        tags.append(f"tool:{k}:{int(amt)}")
                elif k == "cost_less" and amt is not None:
                    tags.append(f"tool:cost_less:{int(amt)}"
                                f":{extra.get('cond') or 'any'}")
                elif k == "retreat_plus" and amt is not None:
                    tags.append(f"tool:retreat_plus:{int(amt)}"
                                f":{extra.get('scope') or 'both'}")
                elif k == "no_retreat":
                    tags.append("tool:no_retreat")
                elif k == "on_damaged_draw" and amt is not None:
                    tags.append(f"tool:on_damaged:draw:{int(amt)}")
                elif k == "on_damaged_status":
                    # `tool:on_damaged:status:<s>[:holder:faction:<k>]`
                    # （第一百零一批，Team Rocket's Hypnotizer）
                    _tail = ""
                    if extra.get("holder_faction"):
                        _tail = f":holder:faction:{extra['holder_faction']}"
                    tags.append(f"tool:on_damaged:status:"
                                f"{extra.get('status')}{_tail}")
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
            elif t == "on_damaged":
                # 受击反应：`on_damaged:<kind>:<arg>`
                # 只允许特性侧（道具侧是 `tool:on_damaged:*`，实现独立）；
                # `retaliate` 是**招式侧**的下回合反制（第二十九批）。
                k = extra.get("kind")
                if k == "retaliate":
                    if kind != "attack":
                        unsupported.append(
                            {"type": t, "reason": "下回合反制只在招式侧（B4）"})
                    elif extra.get("equal"):
                        tags.append("retaliate_next:equal")
                    elif amt:
                        tags.append(f"retaliate_next:{int(amt)}")
                    else:
                        unsupported.append(
                            {"type": t, "reason": "反制伤害数值未识别"})
                elif kind != "ability":
                    unsupported.append(
                        {"type": t, "reason": "受击反应仅实现特性侧（B4）"})
                elif k == "status" and extra.get("status") in ENGINE_STATUS_TAGS:
                    tags.append(f"on_damaged:status:{extra['status']}")
                elif k == "counter" and amt:
                    tags.append(f"on_damaged:counter:{int(amt)}")
                elif k == "counter_per" and extra.get("per_energy"):
                    # ``for each {M} Energy attached to this Pokémon``
                    tags.append(f"on_damaged:counter_per:{int(amt or 0) // 10}:"
                                f"{extra['per_energy']}")
                elif k == "search_bench":
                    # 「search your deck for up to N Pokémon that have "X" in
                    #  their name and put them onto your Bench」（第一百零一批，
                    #  Team Rocket's Koffing）—— 受击即检索铺场。
                    _n0 = extra.get("n")
                    _nk = str(extra.get("name_has") or "")
                    if not isinstance(_n0, int) or not _nk:
                        unsupported.append(
                            {"type": t, "reason": "受击检索形态未识别（B4）"})
                    else:
                        tags.append(f"on_damaged:search_bench:{_n0}"
                                    f":name_has:{_nk}")
                elif k == "discard_energy":
                    tags.append(f"on_damaged:discard_energy:"
                                f"{int(amt or 1)}")
                else:
                    unsupported.append(
                        {"type": t, "reason": f"受击反应形态未建模（{k}）"})
            elif t == "passive":
                kind_ = extra.get("kind")
                if kind_ == "hp_plus" and amt is not None:
                    # `passive:hp_plus:<n>[:if:<cond>]`（第一百三十四批）
                    # ⚠ 带条件的形态**不能**写成无条件的 `passive:hp_plus:N`：
                    #   那样会在「没有特殊能量」时也加血（静默多给上限）。
                    _c = str(extra.get("cond") or "")
                    tags.append(f"passive:hp_plus:{int(amt)}"
                                + (f":if:{_c}" if _c else ""))
                elif kind_ == "extra_attack":
                    # 「If Festival Grounds is in play, this Pokémon may use an
                    # attack it has twice.」（第六十一批，Festival Lead）
                    # 是被动**额度**，不是可发动特性 —— 进 legals 会让随机对局
                    # 报「未知 field tag」。
                    tags.append("passive:extra_attack")
                elif kind_ == "status_immune":
                    # 「This Pokémon can't be Asleep.」（第一百一十一批，Hoothoot）
                    st = str(extra.get("status") or "")
                    if st not in ENGINE_STATUS_TAGS:
                        unsupported.append({"type": t,
                                            "reason": f"状态免疫词未识别：{st}"})
                    else:
                        tags.append(f"passive:status_immune:{st}")
                elif kind_ == "attack_block_unless" and extra.get("cond"):
                    # `passive:attack_block_unless:<cond>`（第一百一十二批）
                    tags.append(f"passive:attack_block_unless:{extra['cond']}")
                elif kind_ == "evolve_same_turn":
                    # `passive:evolve_same_turn[:if:<cond>][:active]`
                    # （Eevee / Karrablast / Shelmet / Luxio）
                    _tail = ""
                    if extra.get("cond"):
                        _tail += f":if:{extra['cond']}"
                    elif extra.get("active_only"):
                        _tail += ":active"
                    tags.append("passive:evolve_same_turn" + _tail)
                elif kind_ == "heal_on_attach" and amt is not None:
                    # `passive:heal_on_attach:<n>`（第一百零四批，Magearna）
                    tags.append(f"passive:heal_on_attach:{int(amt)}")
                elif kind_ == "bench_immune":
                    tags.append("passive:bench_immune")
                elif kind_ == "bench_immune_dmg":
                    # Tera 规则「prevent all damage … by attacks」：只挡伤害，不挡效果
                    tags.append("passive:bench_immune_dmg")
                elif kind_ == "dmg_immune":
                    # 静态免伤（2026-10-28）：`passive:dmg_immune:<scope>[:<filter>][:effects]`
                    scope = str(extra.get("scope") or "self")
                    tag = f"passive:dmg_immune:{scope}"
                    filt = str(extra.get("filter") or "")
                    if filt:
                        tag += ":" + filt
                    if extra.get("effects"):
                        tag += ":effects"
                    tags.append(tag)
                elif kind_ == "dmg_reduce" and amt is not None:
                    # S1 静态修正：常驻减伤（卡面「after applying Weakness and
                    # Resistance」→ 引擎在弱抗之后结算，见 battle._do_attack）。
                    # 来源属性限定 → `:from:type:<t1,t2>`，引擎按攻击方属性判定。
                    from_types = extra.get("from_types") or []
                    tail = ""
                    if from_types:
                        tail = ":from:type:" + ",".join(
                            dict.fromkeys(str(x) for x in from_types))
                    tags.append(f"passive:dmg_reduce:{int(amt)}{tail}")
                elif kind_ == "hp_plus" and amt is not None:
                    tags.append(f"passive:hp_plus:{int(amt)}")
                elif kind_ == "no_weakness":
                    tags.append("passive:no_weakness")
                elif kind_ == "no_retreat":
                    tags.append("passive:no_retreat")
                elif kind_ == "coin_prevent":
                    # 抛币免伤（第四十五批，Fezandipiti）：`passive:coin_prevent:<color>`
                    _col = str(extra.get("color") or "")
                    if _col not in _EN_SYMBOL_COLOR.values():
                        unsupported.append({
                            "type": t, "reason": f"抛币免伤属性未识别（{_col}）"})
                    else:
                        tags.append(f"passive:coin_prevent:{_col}")
                elif kind_ == "coin_survive":
                    # 抛币免击倒（第七十八批，Mega Hawlucha ex）
                    _hp = extra.get("hp")
                    if not isinstance(_hp, int) or _hp <= 0:
                        unsupported.append({
                            "type": t, "reason": "抛币免击倒后的剩余 HP 未识别"})
                    else:
                        tags.append(f"passive:coin_survive:{_hp}")
                elif kind_ == "survive":
                    # 满 HP 免击倒·特性版（第八十批）。引擎 `_ability_survive`
                    # 只在「受伤前满 HP」时调用；这里产出 tag 后不能退化成
                    # `tool:survive`（会弃道具）或 `passive:coin_survive`（会抛币）。
                    _hp = extra.get("hp")
                    if not isinstance(_hp, int) or _hp <= 0:
                        unsupported.append({
                            "type": t, "reason": "满 HP 免击倒后的剩余 HP 未识别"})
                    else:
                        tags.append(f"passive:survive:{_hp}")
                elif kind_ == "prev_attacks":
                    # Relicanth「Memory Dive」：进化前形态的招式可用（照付其费用）
                    tags.append("passive:prev_attacks")
                elif kind_ == "checkup_plus":
                    # Checkup 追加伤害（Pecharunt / Magmortar）
                    _st = str(extra.get("status") or "")
                    tail = ":side:opp" if extra.get("side_opp") else ""
                    if extra.get("active_only"):
                        tail += ":active_only"
                    tags.append(f"passive:checkup_plus:{_st}:{int(amt or 0)}{tail}")
                elif kind_ == "checkup_spread":
                    # Checkup 群体放置（Froslass / Team Rocket's Tyranitar）
                    tail = ""
                    if extra.get("exclude"):
                        tail += f":except:{extra['exclude']}"
                    if extra.get("active_only"):
                        tail += ":active_only"
                    tags.append(f"passive:checkup_spread:{int(amt or 0)}"
                                f":{extra.get('scope') or ''}{tail}")
                else:
                    unsupported.append(
                        {"type": t,
                         "reason": "静态封锁（无特性/禁用道具等）需引擎新机制"
                                   "（未建模动作清单 B4）"})
            elif t == "dmg_taken_plus_next":
                # 跨回合：受击方在**我下个回合**多受 N（弱抗之后）
                tags.append(f"dmg_taken_plus_next:{int(amt)}")
            elif t == "atk_minus_next":
                # 跨回合：受击方在**它下个回合**的招式 -N（弱抗之前）
                tags.append(f"atk_minus_next:{int(amt)}")
            elif t == "self_attack_plus_next":
                key = str(extra.get("attack_key") or "")
                if not key:
                    unsupported.append(
                        {"type": t, "reason": "跨回合加伤缺少招式名（B4）"})
                else:
                    tags.append(f"self_attack_plus_next:{int(amt)}:name:{key}")
            elif t == "counters":
                # 「Place N damage counters on your opponent's Active Pokémon」
                # （Haunter / Alakazam）—— 指示物直放，不吃弱点/抗性。
                # 第二十批（2026-10-29）：`1 of your opponent's Pokémon` →
                # `:scope:opp:1`（任意对手宝可梦，含战斗位）；特性自毁 → `:selfko`。
                per = extra.get("per_counter")
                tag = f"counters:{int(amt)}"
                if per:
                    tag += f":per:{per}"
                if extra.get("counters_scope") == "opp":
                    _cnt = extra.get("counters_count")
                    tag += (":scope:opp:all" if _cnt == "all"
                            else f":scope:opp:{int(_cnt or 1)}")
                elif extra.get("counters_scope") == "ability":
                    # 双方全场「有特性」的逐个（Cofagrigus，第二十八批）
                    tag += ":scope:ability:all"
                elif extra.get("counters_scope") == "self":
                    # 己方战斗位直放指示物（Venture Bomb / Feraligatr，第七十二批）
                    tag += ":scope:self"
                elif extra.get("counters_scope") == "opp_bench":
                    # 只打**对手后备**（Mismagius「Assassin's Magic」，第九十五批）
                    # ⚠ 与 `:scope:opp` 不同：后者候选取「战斗位优先」，会把指示物
                    #   打到战斗位（卡面明写 Benched → 静默错位）。
                    _cnt = extra.get("counters_count")
                    tag += (":scope:opp_bench:all" if _cnt == "all"
                            else f":scope:opp_bench:{int(_cnt or 1)}")
                if extra.get("selfko"):
                    tag += ":selfko"
                # 条件门（第九十五批）：卡面「If <cond>, place …」——条件不成立时
                # **不放置**。放在最后，引擎按后缀剥。
                if extra.get("gate"):
                    tag += f":gate:{extra['gate']}"
                tags.append(tag)
            elif t == "team_boost":
                # 本回合全队增伤（第六十批）
                _n = extra.get("n")
                if not isinstance(_n, int) or _n < 1:
                    unsupported.append({"type": t,
                                        "reason": f"增伤值未识别：{_n}"})
                else:
                    _tag = f"team_boost:{_n}"
                    if extra.get("etype"):
                        _tag += f":type:{extra['etype']}"
                    if extra.get("cats"):
                        _tag += f":cat:{extra['cats']}"
                    tags.append(_tag)
            elif t == "bounce":
                # 附着能量收回手牌（第二十三批）→ `bounce:energy:<scope>:<n>[:type:<c>]`
                sc = str(extra.get("scope") or "self")
                n = int(amt or 1)
                if (extra.get("what") or "energy") != "energy" \
                        or sc not in ("self", "opp_active", "opp_any"):
                    unsupported.append(
                        {"type": t, "reason": f"回手范围/对象未识别：{sc}"})
                else:
                    tag = f"bounce:energy:{sc}:{n}"
                    if extra.get("energy_type"):
                        tag += f":type:{extra['energy_type']}"
                    tags.append(tag)
            elif t == "checkup_set":
                # 「During Pokémon Checkup, put 8 damage counters on that
                # Pokémon instead of 1.」→ `checkup_set:<status>:<n>`
                st = str(extra.get("status") or "")
                if st not in ("poison", "burn") or amt is None:
                    unsupported.append(
                        {"type": t, "reason": f"Checkup 改写形态未识别：{st}"})
                else:
                    tags.append(f"checkup_set:{st}:{int(amt)}")
            elif t == "cure":
                # 第八十一批：清除**出招者自身**的全部特殊状态。
                # 换对象 / 单数范围都不精确 → 如实拒绝，绝不猜。
                if tgt != "self" or str(extra.get("scope") or "") != "all":
                    unsupported.append({"type": t,
                                        "reason": "清除特殊状态的范围未识别（B4）"})
                else:
                    tags.append("cure:self")
            elif t == "knockout":
                # 招式击倒（2026-10-28 第九批）：`knockout:<scope>`
                # scope：opp_active[:basic|if:senergy] · opp_bench[:basic] ·
                #        opp_any · opp_all[:if:hp_le:<n>] ·
                #        opp_counters:<n> · both_active ·
                #        least_hp（第八十六批：剩余 HP 最少者，排除自身）
                scope = str(extra.get("scope") or "")
                if scope.split(":")[0] not in ("opp_active", "opp_bench",
                                               "opp_any", "opp_all",
                                               "opp_counters", "both_active",
                                               "least_hp"):
                    unsupported.append(
                        {"type": t, "reason": f"击倒目标范围未识别：{scope}"})
                else:
                    tags.append(f"knockout:{scope}")
            elif t == "fx":
                # 效果族插件（fx）：tag 必须由已注册的族产出，否则如实未建模
                tag = str(extra.get("tag") or "")
                if tag.startswith("senergy:"):
                    # 特殊能量（2026-10-28）：子系统在 engine/special_energy.py，
                    # 词表以该模块的 KINDS 为唯一来源（不借用 fx: 前缀）
                    if _senergy_known(tag):
                        tags.append(tag)
                    else:
                        unsupported.append(
                            {"type": t, "reason": f"特殊能量种类未建模：{tag}"})
                elif not tag.startswith("fx:"):
                    unsupported.append(
                        {"type": t, "reason": "fx 动作缺少合法 tag"})
                elif not _fx_known(tag):
                    unsupported.append(
                        {"type": t, "reason": f"fx 族未注册：{tag}"})
                else:
                    tags.append(tag)
            else:
                unsupported.append({"type": t, "reason": "引擎未建模该动作类型"})
        # 能量代价子句守卫（2026-11-02）：卡面「Discard / Shuffle … Energy …,
        # and this attack does …（或 and make your opponent …）」里的**代价**若
        # 没有被映射成任何动作，招式就退化成「不付代价白拿附加效果」——属于
        # 静默加强，比未建模危险。实测三例：
        #   · Mega Eelektross ex「You may discard 2 {L} Energy … and make … Paralyzed」
        #     → 只产出 `paralyze`，代价整句被吞；
        #   · Mega Skarmory ex「Shuffle all Energy attached to this Pokémon into
        #     your deck, and this attack does 220 damage …」→ 只剩 `snipe`；
        #   · Mega Charizard X ex「Discard any amount of {R} Energy from among your
        #     Pokémon, and this attack does 90 damage for each …」→ 只有计数源
        #     `own_discarded_this_turn`，却从不真的弃能量（伤害恒 0，反向削弱）。
        # 三种都无法用现有 tag 精确表达（「any amount」需交互层；「shuffle 回牌库」
        # 引擎只有 `energy_to_deck`（限弃牌堆基本能量）语义不符）→ 如实拒绝。
        if kind == "attack":
            _csrc = _norm_prose(s.get("source_text") or "")
            if _ENERGY_COST_CLAUSE.search(_csrc):
                _newt = [str(x) for x in tags[_tag_start:]]
                if not any(x.startswith(("discard_energy", "discard_hand_energy",
                                         "energy_to_deck", "energy_self_to_deck",
                                         "self:",
                                         # 第一百三十五批（S1b）：可选代价已落成
                                         # 决策分解 tag（`choose:optional_energy_cost:`）
                                         # —— 它**真的会弃能量**，不算未映射。
                                         "choose:"))
                           for x in _newt):
                    unsupported.append({
                        "type": "cost",
                        "reason": "能量代价子句未被映射（可选/任意数量代价，B4）"})
        # 进化触发特性（2026-10-28 第十批）：把本 spec 产出的 tag 合并成
        # **一条** `evolve_act:<tag1>|<tag2>`，由引擎在**进化那一刻**按序执行。
        # 为什么必须包裹：引擎的 field 特性是「每回合一次」，而这些特性卡面是
        # 「when you play this Pokémon from your hand to evolve …」——不包裹就等于
        # 每回合免费再用一次（Kadabra「Psychic Draw」实测）。
        if kind == "ability" and trigger == "on_bench":
            # 上场触发同样必须包一层（否则变成每回合一次的 field 特性）
            _nb = tags[_tag_start:]
            if _nb:
                del tags[_tag_start:]
                tags.append("on_bench_act:"
                            + "|".join(dict.fromkeys(str(x) for x in _nb)))
        if kind == "ability" and trigger == "on_evolve":
            new_tags = tags[_tag_start:]
            if new_tags and not all(str(x).startswith("evolve_attach:")
                                    for x in new_tags):
                del tags[_tag_start:]
                # 同一 spec 内重复 tag 只执行一次（否则会重复抽牌/重复进化）
                uniq_subs = list(dict.fromkeys(str(x) for x in new_tags))
                tags.append("evolve_act:" + "|".join(uniq_subs))
            _src0 = str(s.get("source_text") or "")
            if s.get("conditions") or re.search(
                    r"\bif you have\b|\bif your opponent has\b|\bunless\b",
                    _src0, re.I):
                # 进化触发特性的**前置条件**（第一百零二批，Noctowl「Jewel
                # Seeker」：「…, if you have any Tera Pokémon in play, …」）。
                # 能在引擎 `_eval_condition` 里精确求值的，写成
                # `evolve_act:…:gate:<cond>`；认不出的仍如实登记（整卡不收）
                # —— 绝不能无条件放通（那是「有没有 Tera 都能检索」）。
                _gm = re.search(
                    r"\bif (?P<c>you have [^.]+? in play|"
                    r"your opponent has [^.]+? in play)", _src0, re.I)
                _gc = (_condition_from_text("if " + _gm.group("c"))
                       if _gm else None)
                if _gc and tags and str(tags[-1]).startswith("evolve_act:"):
                    tags[-1] = f"{tags[-1]}:gate:{_gc}"
                else:
                    unsupported.append(
                        {"type": "condition",
                         "reason": "进化触发特性的前置条件未建模（B4）"})
            _src = str(s.get("source_text") or "")
            if re.search(r"you may also use this ability if", _src, re.I):
                # Flygon「…也可在**战斗位被击倒**时再用一次」：第二个触发时机用
                # `on_ko:replay_evolve_act` 表达（引擎在击倒瞬间重放 evolve_act）。
                # 其它「也可使用」的写法仍如实未建模。
                if re.search(r"active spot and is knocked out", _src, re.I):
                    tags.append("on_ko:replay_evolve_act")
                else:
                    unsupported.append(
                        {"type": "trigger",
                         "reason": "进化触发特性的**第二个触发时机**未建模（B4）"})
    # 第九十二批（2026-11-05）：`evolve_from_deck` 族**只保留最具体的一条**。
    # 同一句里 search(dest=evolve) 与 evolve 动作会各产一次，且
    # `has no Abilities` 变体与裸 tag 会同时出现 —— Salvatore 实测产出
    # ['evolve_from_deck', 'evolve_from_deck:no_ability']，下游 `build_trainer`
    # 按「多 tag → 整卡拒」处理，等于白做。
    # 归一化：有 `:no_ability` 就用它，否则保留裸 tag；位置取首次出现处。
    _ev_family = [t for t in tags if str(t).startswith("evolve_from_deck")]
    if len(_ev_family) > 1:
        _ev_keep = ("evolve_from_deck:no_ability"
                    if any(str(t).endswith(":no_ability") for t in _ev_family)
                    else "evolve_from_deck")
        _ev_done = False
        _collapsed = []
        for _t in tags:
            if str(_t).startswith("evolve_from_deck"):
                if _ev_done:
                    continue
                _ev_done = True
                _collapsed.append(_ev_keep)
            else:
                _collapsed.append(_t)
        tags = _collapsed
    # 第九十二批（2026-11-05）：`discard:all` + `search_multi:` 合成**一条**
    # 复合 tag（Larry's Skill PRE 115：「Discard your hand and search your deck
    # for a Pokémon, a Supporter card, and a Basic Energy card」）。
    # 训练家只认**一个**卡级 tag（`TrainerDef.tag` 单值、派发链只执行一条），
    # 两条独立 tag 会被 `build_trainer` 按「多 tag → 整卡拒」处理；
    # 而「只留第一条」会静默丢掉整个检索（更隐蔽）。
    _d_all = "discard:all" in tags
    _sm_tag = next((str(t) for t in tags if str(t).startswith("search_multi:")), None)
    if _d_all and _sm_tag:
        tags = [t for t in tags
                if t != "discard:all" and str(t) != _sm_tag]
        tags.append("discard_" + _sm_tag)   # → discard_search_multi:<sel>:<k>
    # 按出现顺序去重
    seen, uniq = set(), []
    for x in tags:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    # 打出代价汇总（2026-11-02 第三十六批）：多 spec 出现**互相矛盾**的代价时
    # 如实拒绝 —— 取其一（无论取哪个）都是静默改变卡的强度。
    _pcs = [str(s0.get("play_cost") or "") for s0 in (specs or [])]
    _pcs = [x for x in _pcs if x]
    play_cost = ""
    if _pcs:
        if len(set(_pcs)) > 1:
            unsupported.append({"type": "play_cost",
                                "reason": f"同一张卡出现多种打出代价：{sorted(set(_pcs))}"})
        else:
            play_cost = _pcs[0]
    # 特性发动代价（2026-11-04 第四十九批）：同一只宝可梦的**多个特性**各有各的
    # 代价，所以这里不做「多值冲突」判断 —— 每个特性条目各自携带自己的 cost。
    _acs = [str(s0.get("ability_cost") or "") for s0 in (specs or [])]
    _acs = [x for x in _acs if x]
    ability_cost = _acs[0] if _acs else ""
    if _acs and any(not _ABILITY_COST_SHAPE.match(x) for x in _acs):
        unsupported.append({"type": "ability_cost",
                            "reason": f"特性代价形态非法：{_acs}"})
    # 打出前置条件汇总（2026-11-04 第五十九批）：与 `play_cost` 同一纪律 ——
    # 多 spec 出现**矛盾**的条件时如实拒绝（取其一 = 静默改变门槛）。
    _pgs = [str(s0.get("play_gate") or "") for s0 in (specs or [])]
    _pgs = [x for x in _pgs if x]
    play_gate = ""
    if _pgs:
        if len(set(_pgs)) > 1:
            unsupported.append({"type": "play_gate",
                                "reason": f"同一张卡出现多种打出前置条件：{sorted(set(_pgs))}"})
        else:
            play_gate = _pgs[0]
        ability_cost = ""
    # 卡面带「in order to use this Ability」但**没抽出代价** → 必须拒收。
    # 这条是**兜底**：代价守卫原先只挂在 status 分支里，`draw:` 类特性带认不出的
    # 代价时会**静默免费发动**（第四十九批实测）。
    if not ability_cost and not unsupported:
        _needs_cost = any(
            re.search(r"in order to use this ability",
                      str(s0.get("source_text") or ""), re.I)
            for s0 in (specs or []))
        if _needs_cost:
            unsupported.append({"type": "ability_cost",
                                "reason": "特性发动代价形态未识别（B4）"})
    # 「有文本、零 tag、零理由」= 静默丢卡（2026-11-04 第四十八批）。
    # 卡池准入规则是「有效果文本但无 tag → 整卡不收」，但**不记录原因** ——
    # 于是这类卡在 `unmodeled_registry` 里只剩一个 `unmatched`，看不出到底缺
    # 什么机制（本轮实测 20 张，其中 4 张是「Flip a coin for each …」，
    # 其余也各自缺一条具体规则）。补一条兜底登记，让「沉默」变成「显式待办」。
    if not uniq and not unsupported:
        # ⚠ 只在「**一个动作都没解析出来**」时才登记：`damage` 类动作由招式的
        # damage 字段承载、本身不产 tag（提示词 few-shot 例 5 就是这种合法解析），
        # 一律按「未映射」判死会把正确结果误杀。
        _no_actions = not any((s0.get("actions") or []) for s0 in (specs or []))
        _txts = [str(s0.get("source_text") or "").strip() for s0 in (specs or [])]
        if _no_actions and any(_txts):
            unsupported.append({
                "type": "rule",
                "reason": "卡面文本未映射为任何可执行动作（需新规则形态，B4）"})
    return {"tags": uniq, "unsupported": unsupported, "notes": notes,
            "play_cost": play_cost, "ability_cost": ability_cost,
            "play_gate": play_gate}


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
