# -*- coding: utf-8 -*-
"""卡池解析对账审计 —— 「卡面文本 ↔ 引擎 tag」一致性守卫

为什么必须有这个脚本
--------------------
项目实测过一类**最危险的缺陷**：**静默错映射** —— 局面正常、无报错、测试全绿，
只有把「卡面原文」与「引擎 tag」逐条对账才看得出来。历史战果：

  · 阵营反转    `for each of **your** Benched Pokémon` → 按**对手**后备数算伤害
  · 口径反转    `Prize card … has taken` → 按**剩余**奖赏卡算（方向反）
  · 弃错对象    `Discard an Energy from your **opponent's** Active` → 弃了自家能量
  · 凭空加伤    条件无法表达时退化成无条件 `dmg_plus:N`
  · 附加伤害目标 `does N damage to itself` / `… Benched Pokémon` → 记成打**战斗位**
  · 检索目标    整段文本里出现 Pokémon → `search_pokemon`（实际检索的是能量卡）
  · 规则提醒括号 `(Don't apply Weakness and Resistance …)` 被当成"静态规则修改"效果
                → 整卡不可对战（84 张纯假阳性）

所以每次动解析链 / 引擎 tag，**都要跑这个对账**。本脚本只读**已入库的卡池 JSON**
（`cards_pool_ext.json`），零依赖、秒级，因此可以直接进 CI。

用法::

    python packages/card-db/scripts/audit_pool_alignment.py        # 审计（0 通过 / 1 有问题）

口径说明
--------
本脚本审计的是**可对战卡池**（真正会进引擎的卡）。理由：池外的卡即便解析有误也
打不了；而池内的卡一旦错了就是**真实的错误行为**。池子的准入是「整卡不收」，
所以「有效果子句却无对应 tag」= 静默丢效果，必须拦下。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
POOL = REPO / "packages" / "engine" / "engine" / "data" / "cards_pool_ext.json"

# 规则提醒括号：不是效果（详见 agent/effects.py 的 _RULES_PAREN_RE）
PAREN = re.compile(r"\((?:don[\u2019']?t\s+apply|before\s+applying)[^)]*?"
                   r"(?:weakness|resistance)[^)]*?\)", re.I)
# 附加伤害子句
DMG_TO = re.compile(r"does\s+(\d+)\s+(more\s+)?damage\s+to\s+([^.;,()]{0,90})",
                    re.I)
# 放置伤害指示物子句（与「造成伤害」等价：1 指示物 = 10 点）
PLACE_COUNTERS = re.compile(r"(?:put|place)\s+(\d+)\s+damage\s+counters?\s+"
                            r"(?:on|onto)\s+([^.;,()]{0,90})", re.I)
# 检索子句
SEARCH_FOR = re.compile(r"search\s+(?:your\s+deck|it)\s+for\s+(.{0,120}?)"
                        r"(?:,| and | then |\.|\()", re.I)
# 检视牌库顶子句
LOOK_TOP = re.compile(r"look\s+at\s+the\s+top\s+(\d+)\s+cards?", re.I)
# 磨牌子句
MILL = re.compile(r"discard\s+the\s+top\s+(?:(\d+)\s+)?cards?\s+of\s+"
                  r"(your\s+opponent[\u2019']?s?|your)\s+deck", re.I)
# 抛币形态（2026-10-29 第十八批）
_COIN_UNTIL_RE = re.compile(
    r"flip a coin until you get tails[^.]{0,40}?\.\s*this attack does (\d+) more "
    r"damage for each heads", re.I)
_COIN_COMPOSITE_RE = re.compile(
    r"flip a coin[^.]*?\.\s*if heads[^.]*?does (\d+) more damage, and heal "
    r"(\d+) damage from this pok[eé]mon", re.I)
# 条件 / 全队伤害修正（2026-10-29 第十九批）
_TEAM_REDUCE_RE = re.compile(
    r"all of your [^.]{0,90}? take (\d+) less damage from attacks from your "
    r"opponent", re.I)
_EXTRA_ENERGY_RE = re.compile(
    r"has at least (\d+) extra energy attached \(in addition to this attack'?s "
    r"cost\), this attack does (\d+) more damage", re.I)
# 属性符号 → 引擎色代码（静态减伤的来源限定用）
_TYPE_COLOR = {"G": "grass", "R": "fire", "W": "water", "L": "lightning",
               "P": "psychic", "F": "fighting", "D": "darkness",
               "M": "metal", "C": "colorless", "N": "dragon"}
# 能量代价子句（2026-11-02 · 第三十一类）：**独立实现**，不 import 解析包的
# 同名正则 —— 对账的意义就是「用另一套写法重算一遍」，共用同一个正则等于自己
# 给自己盖章（解析写错时会一起错，对账永远通过）。
_ENERGY_COST_CLAUSE = re.compile(
    r"(?:you may )?(?:discard|shuffle)[^.,]*?\benergy\b[^.,]*?"
    r"(?:,\s*and this attack does|and this attack does|and make your opponent)",
    re.I)
# 限定后的指示物计数源（2026-10-29 第二十六批 · 第二十四类）
_BENCH_CNT_TYPE_RE = re.compile(
    r"damage\s+counters?\s+on\s+all\s+of\s+your\s+benched\s+\{([a-z])\}\s+"
    r"pok[eé]mon", re.I)
_BENCH_CNT_NAMED_RE = re.compile(
    r"damage\s+counters?\s+on\s+all\s+of\s+your\s+benched\s+"
    r"([a-z][a-z\u2019' ]{1,20})\u2019?s\s+pok[eé]mon", re.I)
_FIELD_NAMED_DMG_RE = re.compile(
    r"each\s+of\s+your\s+pok[eé]mon\s+that\s+has\s+[\"\u201c]([a-z ]{2,20})[\"\u201d]"
    r"\s+in\s+its\s+name\s+that\s+has\s+any\s+damage\s+counters?\s+on\s+it", re.I)


def _norm(s: str) -> str:
    return (s or "").replace("\u2019", "'").lower()


def _want_snipe(tgt: str, per: int):
    """目标子句 → 期望的 snipe tag 前缀（``None`` = 无需 tag 或不该进池）"""
    t = _norm(tgt)
    # 「攻击方」= 受击反应特性的专属目标（2026-10-27）。它**只能**由
    # `on_damaged:counter:<dmg>` 表达：走 snipe/self 都会打错对象
    # （attack 结算时攻击方＝自己，退化成反伤或打自己战斗位）。
    if re.search(r"attacking\s+pok", t):
        return "on_damaged:counter"
    # 「During Pokémon Checkup, put N damage counters on that Pokémon **instead
    # of 1**」（Nidoking ex）——不是普通指示物，而是检查期伤害改写
    if re.search(r"instead of 1", t):
        return "checkup_set"
    # 缩放（`for each …`）**不再一律拒绝**（2026-10-29 第十七批）：只要**目标部分**
    # 能精确表达，就落 `:per:<counter>`。真正不可表达的是目标部分的子集限定
    # （{ex} / that has …），那部分留在 `t` 里继续走下面的 unmodeled 判定。
    t = re.split(r"\s+for each\b", t)[0].strip()
    # 「that has any damage counters on it」→ 引擎 `:target:damaged` 目标筛选
    # （2026-10-29 第二十七批）——**可精确执行**，不算「不可表达子集」。
    # 去掉限定词后继续按目标形态解析（1 of … benched / each benched …）。
    t = re.sub(r"\s*that (?:has|have) any damage counters? on it", "", t)
    if re.search(r"\bitself\b", t) or t.startswith("this pok"):
        return "self"
    if "bench" not in t and ("active pok" in t or "defending pok" in t):
        return "none"                        # 战斗位由招式 damage 字段承载
    if re.search(r"\{|that (?:has|have|is|are|don|doesn)|named", t):
        return "unmodeled"                   # 子集限定 → 不该进池
    if re.search(r"each of your opponent'?s? benched", t):
        return "snipe:opp_bench"
    if re.search(r"each of your opponent'?s? pok", t):
        return "snipe:opp"
    if re.search(r"each of your (?:own )?benched", t):
        return "snipe:own_bench"
    if re.search(r"each benched", t):
        return "snipe:both_bench"
    if re.search(r"\d+\s+of your opponent'?s? benched", t):
        return "snipe:opp_bench"
    if re.search(r"\d+\s+of your opponent'?s? pok", t):
        return "snipe:opp"
    if re.search(r"\d+\s+of your (?:own )?benched", t):
        return "snipe:own_bench"
    # 自由分配：把伤害/指示物「任意方式」放到**对手后备** → 引擎 spread_free 精确支持
    # （E5 任意分配：opp.bench 任意分配，位置码由调用方 plan 指定）。spread_free 只覆盖
    # 后备、不覆盖战斗位，且不含「that has / which / with a / for each / named」等子集
    # 条件 —— 否则仍是未建模（落到下面 unmodeled）。放在 each/digit 之后：纯「in any way」
    # 自由分配才命中，带「each of / N of」的均按 snipe 处理。
    if ("bench" in t and "opponent" in t
            and re.search(
                r"in any way|any way you|however you like|as you like|however you choose",
                t)
            and not re.search(
                r"that (?:has|have|is|are|don|doesn)|which |with a |for each|named|\beach\b",
                t)):
        return "spread_free"
    return "unmodeled"


def _expand_evolve_act(effects: list) -> list:
    """把 ``evolve_act:<a>|<b>`` 展开成 ``[原 tag, a, b]`` 供逐条对账

    进化触发特性把整段效果包在一条 tag 里（引擎在进化时按序执行）；对账若只看
    外层 tag，会误判「检索/磨牌/反制」缺 tag。
    """
    out: list = []
    for t in effects or []:
        s = str(t)
        if s.startswith("evolve_act:"):
            out.append(s)
            out.extend(x for x in s[len("evolve_act:"):].split("|") if x)
        else:
            out.append(s)
    return out


def audit_extra_damage(card_name: str, item_name: str, desc: str,
                       effects: list) -> list:
    """附加伤害 / 放置指示物子句 → 必须有对应 tag（self:N / snipe:…），否则报错"""
    errs = []
    d = PAREN.sub(" ", desc or "")
    ko_ctx = bool(re.search(r"is knocked out by damage", _norm(desc)))
    for m in DMG_TO.finditer(d):
        if m.group(2):                      # 「more damage」属修正器范畴
            continue
        errs += _check_clause(card_name, item_name, m.group(3), m.group(1),
                              effects, m.group(0), ko_ok=ko_ctx)
    for m in PLACE_COUNTERS.finditer(d):
        _cl = _norm(m.group(2)).lower()
        # 「each Pokémon that has an Ability (both yours and your opponent's)」
        # → `counters:N:scope:ability:all`（Cofagrigus，第二十八批）。
        # ⚠ Checkup 群体放置（Froslass）写法相同，但由 `passive:checkup_spread:`
        #   承载，必须先排除，否则会被误报「缺 counters」。
        if re.search(r"each pok[eé]mon that has an ability", _cl) \
                and "checkup" not in _norm(desc or "").lower():
            hits = [str(e) for e in effects
                    if str(e).startswith(f"counters:{int(m.group(1))}:")
                    and ":scope:ability:all" in str(e)]
            if not hits:
                errs.append(f"{card_name} · {item_name}：`{m.group(0)[:60]}` "
                            "缺 `counters:N:scope:ability:all`"
                            "（双方「有特性者」逐个，现为 "
                            f"{[e for e in effects if str(e).startswith('counters')]}）")
            continue
        # 「If you attached Energy to a Pokémon in this way, place N damage
        # counters on that Pokémon」→ 由 `attach:…:counters:N` 承载（Toxtricity）
        if "attached energy to a pok" in _norm(desc or "").lower():
            if not any(":counters:" in str(e) for e in effects):
                errs.append(f"{card_name} · {item_name}：`{m.group(0)[:60]}` "
                            "缺 `:counters:`（附能后的指示物未建模）")
            continue
        # 指示物**直放**对手战斗位（不吃弱/抗）→ `counters:`（Haunter / Alakazam）
        if "bench" not in _cl and "active pok" in _cl and "each" not in _cl:
            if not any(str(e).startswith("counters:") for e in effects):
                errs.append(f"{card_name} · {item_name}：`{m.group(0)[:60]}` "
                            "缺 `counters:` tag（指示物直放未建模）")
            continue
        # 「N damage counters on **1 of your opponent's Pokémon** / on **each of**
        # your opponent's Pokémon」（含战斗位 · 2026-10-29 第二十批）→
        # `counters:N:scope:opp:1` / `counters:N:scope:opp:all`。
        # ⚠ **不能**落 `snipe:opp`：snipe 打战斗位会吃弱点/抗性，而指示物不吃
        # （Bramblin / Spectrier / Uxie 曾因此静默多算）。
        _many = bool(re.search(r"\beach of your opponent'?s? pok", _cl))
        if "bench" not in _cl and re.search(
                r"\b(?:\d+|each) of your opponent'?s? pok", _cl):
            _suffix = ":scope:opp:all" if _many else ":scope:opp:1"
            hits = [str(e) for e in effects
                    if str(e).startswith(f"counters:{int(m.group(1))}:")
                    and _suffix in str(e)]
            if not hits:
                errs.append(f"{card_name} · {item_name}：`{m.group(0)[:60]}` "
                            f"缺 `counters:{int(m.group(1))}:…{_suffix}` tag"
                            "（指示物可放对手任意宝可梦，且不吃弱/抗）")
            elif re.search(r"\bfor each\b", _cl) \
                    and not any(":per:" in h for h in hits):
                errs.append(f"{card_name} · {item_name}：指示物 `{m.group(0)[:60]}` "
                            "是**按计数源缩放**的，缺 `:per:<counter>`"
                            f"（现为 {hits}）")
            continue
        # Checkup 群体放置 → `passive:checkup_spread:`（Froslass / Tyranitar）
        if "checkup" in _norm(desc or "").lower() and "each" in _cl:
            if not any(str(e).startswith("passive:checkup_spread:")
                       for e in effects):
                errs.append(f"{card_name} · {item_name}：`{m.group(0)[:60]}` "
                            "缺 `passive:checkup_spread:` tag（群体放置未建模）")
            continue
        errs += _check_clause(card_name, item_name, m.group(2),
                              int(m.group(1)) * 10, effects, m.group(0),
                              ko_ok=ko_ctx)
    return errs


def _check_clause(card_name: str, item_name: str, clause: str, per,
                  effects: list, raw: str, ko_ok: bool = False) -> list:
    want = _want_snipe(clause, per)
    scale = bool(re.search(r"\bfor each\b", _norm(clause)))
    if want == "none":
        return []
    if want == "unmodeled":
        return [f"{card_name} · {item_name}：不可精确表达的目标 "
                f"`{raw[:60]}` **却在池内**（应整卡不收）"]
    if want == "self":
        hits = [str(e) for e in effects
                if str(e) == f"self:{per}" or str(e).startswith(f"self:{per}:")]
        # 抛币分支里的反伤（Team Rocket's Raticate：`coin_all:2:tails:self:90`）
        hits += [str(e) for e in effects
                 if str(e).startswith(("coin:", "coin_each:", "coin_all:",
                                       "coin_until_tails:"))
                 and f":self:{per}" in str(e)]
        if not hits:
            return [f"{card_name} · {item_name}：反伤 `{raw[:60]}` "
                    f"缺 `self:{per}` tag"]
        if scale and not any(":per:" in h for h in hits):
            return [f"{card_name} · {item_name}：反伤 `{raw[:60]}` 是**按计数源"
                    f"缩放**的，缺 `:per:<counter>`（现为 {hits}）"]
        return []
    if want == "checkup_set":
        if not any(str(e).startswith("checkup_set:") for e in effects):
            return [f"{card_name} · {item_name}：Checkup 改写 `{raw[:60]}` "
                    f"缺 `checkup_set:` tag（检查期伤害会被算成默认值）"]
        return []
    if want == "on_damaged:counter":
        # 被**击倒**时反制（Maractus）走 `on_ko:counter:N`；其余受击反应走
        # `on_damaged:counter:N`。两者数值都必须**逐字对上**。
        alt = f"on_ko:counter:{per}"
        if ko_ok and alt in effects:
            return []
        # 第二十九批：受击反制的两种**新形态**（数值校验见 `audit_on_damaged`）
        if any(str(e).startswith(("retaliate_next:", "on_damaged:counter_per:"))
               for e in effects):
            return []
        # 数值必须**逐字对上**：多打/少打一个指示物都是静默错映射
        if f"on_damaged:counter:{per}" not in effects:
            return [f"{card_name} · {item_name}：受击反应 `{raw[:60]}` "
                    f"缺 `on_damaged:counter:{per}` tag（现为 "
                    f"{[e for e in effects if str(e).startswith('on_damaged')]}）"]
        return []
    hits = [str(t) for t in effects if str(t).startswith(want + ":")]
    if not hits:
        return [f"{card_name} · {item_name}：`{raw[:60]}` 缺 `{want}:…` tag"]
    if scale and not any(":per:" in h for h in hits):
        return [f"{card_name} · {item_name}：`{raw[:60]}` 是**按计数源缩放**的，"
                f"缺 `:per:<counter>`（现为 {hits}）"]
    low_clause = _norm(clause)
    # 「that has any damage counters on it」→ 必须有目标筛选（第二十七批）：
    # 缺了就会打「没有指示物」的目标 —— 卡面本不允许，属**打错对象**。
    if re.search(r"that (?:has|have) any damage counters? on it", low_clause) \
            and not any(h.endswith(":target:damaged") for h in hits):
        return [f"{card_name} · {item_name}：`{raw[:60]}` 缺 `:target:damaged`"
                f"（只允许选**已有指示物**的目标，现为 {hits}）"]
    # 「for each damage counter **on that Pokémon**」→ 必须逐目标缩放：
    # 用全局计数源会把**其它**宝可梦的指示物也算进来（静默多算）。
    if re.search(r"damage counters? on that pok", low_clause) \
            and not any(":per:target_counters" in h for h in hits):
        return [f"{card_name} · {item_name}：`{raw[:60]}` 必须逐目标缩放 "
                f"`:per:target_counters`（现为 {hits}）"]
    return []


def audit_dmg_layer(card_name: str, item_name: str, desc: str,
                    effects: list) -> list:
    """条件/全队伤害修正（2026-10-29 第十九批 · 第十七类对账）

    · 「all of your X take N less damage from attacks from your opponent's Pokémon」
      → 必须 `fx:team_reduce:N:…`（源头是己方任意一只的特性，不能写成
      `passive:dmg_reduce` 挂在受击者身上 —— 那只会减自己）。
    · 「has at least N extra Energy attached (**in addition to this attack's
      cost**), this attack does M more damage」→ 必须 `dmg_plus:M:if:
      own_active_extra_energy_at_least:N`（写成无条件/普通能量条件都会静默多算）。
    """
    errs = []
    d = PAREN.sub(" ", desc or "").replace("\u2019", "'")
    for m in _TEAM_REDUCE_RE.finditer(d):
        n = int(m.group(1))
        if not any(str(e).startswith(f"fx:team_reduce:{n}:") for e in effects):
            errs.append(f"{card_name} · {item_name}：全队减伤 `{m.group(0)[:60]}` "
                        f"缺 `fx:team_reduce:{n}:…` tag")
    for m in _EXTRA_ENERGY_RE.finditer(d):
        n, dmg = int(m.group(1)), int(m.group(2))
        want = f"dmg_plus:{dmg}:if:own_active_extra_energy_at_least:{n}"
        if want not in [str(e) for e in effects]:
            errs.append(f"{card_name} · {item_name}：额外能量加伤 "
                        f"`{m.group(0)[:60]}` 缺 `{want}` tag")
    return errs


def audit_coin_forms(card_name: str, item_name: str, desc: str,
                     effects: list) -> list:
    """抛币形态（2026-10-29 第十八批 · 第十六类对账）

    · 「Flip a coin until you get tails … for each heads」→ 必须 `coin_until_tails:dmg_plus:N`
      （若落成 `coin_each`/`dmg_plus` 就把「连续正面累加」压成「一次 +N」）
    · 「If heads, this attack does N more damage, **and heal M**」→ 两个子效果必须在
      **同一条** `coin:heads:` tag 里（`dmg_plus:N|heal:M`），拆成两条会变成两次抛币。
    """
    errs = []
    d = PAREN.sub(" ", desc or "")
    for m in _COIN_UNTIL_RE.finditer(d):
        want = f"coin_until_tails:dmg_plus:{int(m.group(1))}"
        if want not in [str(e) for e in effects]:
            errs.append(f"{card_name} · {item_name}：`{m.group(0)[:60]}` "
                        f"缺 `{want}` tag（连续正面累加被压成一次加伤）")
    for m in _COIN_COMPOSITE_RE.finditer(d):
        want_d = f"dmg_plus:{int(m.group(1))}"
        want_h = f"heal:{int(m.group(2))}"
        coin = [str(e) for e in effects if str(e).startswith("coin:heads:")]
        if not any(want_d in t and want_h in t for t in coin):
            errs.append(f"{card_name} · {item_name}：`{m.group(0)[:60]}` 的加伤与"
                        f"回复必须在**同一条** coin tag（现为 {coin}）")
    return errs


def audit_look_top(card_name: str, item_name: str, desc: str,
                   effects: list) -> list:
    """「look at the top N cards」→ 去向必须是**入手**才可用 `recon:N`

    引擎 `recon:N` = 顶部 N 张取 1 入手。若卡面去向是「弃置 / 附着 / 放回牌库」
    却映射成 recon，引擎会**白送一张手牌**（凭空手牌优势）。实测抓出 5 张
    （Morpeko / Metang / Deduction Kit / Grimsley's Move / Waitress）。
    """
    errs = []
    m = LOOK_TOP.search(desc or "")
    if not m:
        return errs
    tail = _norm((desc or "")[m.end():])
    recon = [str(t) for t in effects if str(t).startswith("recon:")]
    if not recon:
        return errs
    if not re.search(r"(?:into|to)\s+your\s+hand", tail):
        errs.append(f"{card_name} · {item_name}：`{m.group(0)}` 去向非「入手」"
                    f"（{tail.strip()[:40]}），不该映射成 {recon}")
        return errs
    want = f"recon:{int(m.group(1))}"
    if want not in recon:
        errs.append(f"{card_name} · {item_name}：`{m.group(0)}` 应为 `{want}`，"
                    f"实际 {recon}")
    return errs


def audit_ability_search(card_name: str, item_name: str, desc: str,
                         effects: list) -> list:
    """特性侧「search your deck for …」**不可**映射成 `recon`（语义不同）"""
    errs = []
    m = SEARCH_FOR.search(desc or "")
    if not m:
        return errs
    recon = [str(t) for t in effects if str(t).startswith("recon:")]
    if recon:
        errs.append(f"{card_name} · {item_name}：全牌库检索被映射成 {recon}"
                    f"（recon = 顶部 N 张随机取 1，会拿错东西）")
    return errs


def audit_mill(card_name: str, item_name: str, desc: str,
               effects: list) -> list:
    """磨牌子句 → 必须有 `mill:N[:opp]`"""
    errs = []
    for m in MILL.finditer(desc or ""):
        n = int(m.group(1)) if m.group(1) else 1
        side = "opp" if "opponent" in _norm(m.group(2)) else "own"
        want = f"mill:{n}" + (":opp" if side == "opp" else "")
        if want not in effects:
            errs.append(f"{card_name} · {item_name}：`{m.group(0)[:60]}` "
                        f"缺 `{want}` tag")
    return errs


# 「Choose 1 of your opponent's Active Pokémon's attacks … that Pokémon can't use
# that attack.」（Klefki「Memory Lock」/ Team Rocket's Murkrow「Torment」）
_CHOOSE_OPP_ATK = re.compile(
    r"choose 1 of your opponent'?s active pok[eé]mon'?s attacks?\.?\s*"
    r"during your opponent'?s next turn,?\s*that pok[eé]mon "
    r"can'?t use that attack", re.I)


def audit_attack_lock(card_name: str, item_name: str, desc: str,
                      effects: list) -> list:
    """对手**指定**招式封锁对账（2026-10-30）：语义是「运行时选一个对手招式」。

    · 「choose 1 … that attack」必须产出 `attack_lock:opp_name:turn`；
    · **禁止**把指代词「that attack」当具体卡名生成 `attack_lock:name:…` ——
      那样锚不到任何真实招式，锁**静默不生效**（比不进池更糟）。
    """
    errs = []
    d = _norm(desc or "")
    if _CHOOSE_OPP_ATK.search(d) and "attack_lock:opp_name:turn" not in effects:
        errs.append(f"{card_name} · {item_name}：「choose 1 … that attack」"
                    f"缺 `attack_lock:opp_name:turn`")
    for t in effects or []:
        if str(t).startswith("attack_lock:name:"):
            atk = str(t).split(":", 3)[-1].strip().lower()
            if atk == "that attack":
                errs.append(f"{card_name} · {item_name}：把指代词「that attack」"
                            f"当具体卡名（{t}），锁定将静默失效")
    return errs


# 「Look at the top N cards of your deck …」（2026-10-30 第二十批）
_RECON_TOP = re.compile(r"look at the top\s+(\d+)\s+cards? of your deck", re.I)


def audit_recon_top(card_name: str, item_name: str, desc: str,
                    effects: list) -> list:
    """检视牌库顶**去向与余牌**对账（2026-10-30 · 第二十八类）

    与既有「检视牌库顶」子句类（只判「是否被当成整库检索」）不同，这一类判**去向**：

    1. **张数不符**：卡面「top 7」却落成 `recon:8` —— 多看一张 = 凭空放宽；
    2. **去向不符**：卡面「onto your Bench」却落成 `dest:hand`（或反之）
       —— 牌进了错的地方，手牌凭空多一张；
    3. **余牌不符**：卡面「Discard the other cards」却落成洗回牌库
       —— 弃牌堆少 4 张，后续「弃牌堆回收」类效果凭空少材料。

    另加一条**反向**守卫：卡面写了「look at the top N」，却落成 `search_*`
    （整副牌库检索）—— 检索范围被放大，等于凭空任取。
    """
    errs = []
    d = _norm(desc or "")
    m = _RECON_TOP.search(d)
    if not m:
        return errs
    n = int(m.group(1))
    recon_tags = [str(t) for t in (effects or [])
                  if str(t).startswith("recon:")]
    search_tags = [str(t) for t in (effects or [])
                   if str(t).startswith(("search_pokemon:", "search_trainer:",
                                         "search_any:", "search_energy:"))]
    if search_tags:
        errs.append(f"{card_name} · {item_name}：卡面是「look at the top {n}」"
                    f"（牌库顶局部），却落成整库检索 {search_tags}"
                    f"—— 检索范围被放大")
    if not recon_tags:
        return errs                       # 未建模（如实拒绝）不算错映射
    for t in recon_tags:
        parts = t.split(":")
        if len(parts) < 2 or not parts[1].isdigit():
            continue                      # 形态非法由引擎侧抛错兜住
        if int(parts[1]) != n:
            errs.append(f"{card_name} · {item_name}：卡面是「top {n}」却落成 "
                        f"`{t}`（张数不符）")
        want_dest = "hand"
        if re.search(r"onto your bench", d, re.I):
            want_dest = "bench"
        elif re.search(r"attach .{0,40}?to 1 of your pok[eé]mon", d, re.I):
            want_dest = "attach"
        got_dest = "hand"
        for i, x in enumerate(parts):
            if x == "dest" and i + 1 < len(parts):
                got_dest = parts[i + 1]
        if got_dest != want_dest:
            errs.append(f"{card_name} · {item_name}：卡面去向是 `{want_dest}`，"
                        f"`{t}` 却是 `{got_dest}`")
        want_rest = "shuffle"
        if re.search(r"put them on the bottom of your deck", d, re.I):
            want_rest = "bottom"
        elif re.search(r"discard the other cards", d, re.I):
            want_rest = "discard"
        got_rest = "shuffle"
        for i, x in enumerate(parts):
            if x == "rest" and i + 1 < len(parts):
                got_rest = parts[i + 1]
        if got_rest != want_rest:
            errs.append(f"{card_name} · {item_name}：余牌去向应是 `{want_rest}`，"
                        f"`{t}` 却是 `{got_rest}`")
    return errs


def audit_opp_hand(card_name: str, item_name: str, desc: str,
                   effects: list) -> list:
    """对手手牌操作**方向/张数/类别/去向**对账（2026-10-30 · 第二十九类）

    这一族最危险的不是「没建模」，而是**建错了但看起来正常**：

    1. **方向反转**：卡面是「you discard a card you find there」（**使用者**选），
       却落成随机口径 —— 引擎池里 `discard_opp_random` / `discard_opp_to` /
       `shuffle_opp_hand` 都是「对手自选 ⇒ 随机」，写法相邻、语义相反；
    2. **张数不符**：卡面「up to 2」却落成 1（效果变弱）或 `all`（凭空变强）；
    3. **类别不符**：卡面限定 Item / Energy，却落成 `any` —— 拿错牌；
    4. **去向不符**：卡面「bottom of their deck」却落成 `discard`（或反之）
       —— 对手要么少一张牌、要么多一次弃牌堆回收的材料。

    另加**反向**守卫：卡面没有「reveals their hand」却出现 `opp_hand:` tag
    （多半是句首锚定失效、把别的句式吃进来了）。
    """
    errs = []
    d = _norm(desc or "")
    has_reveal = bool(re.search(r"reveals? their hand", d, re.I))
    tags = [str(t) for t in (effects or []) if str(t).startswith("opp_hand:")]
    if not has_reveal:
        if tags:
            errs.append(f"{card_name} · {item_name}：卡面没有「reveals their "
                        f"hand」，却出现 {tags} —— 认领越界")
        return errs
    if not tags:
        # 只揭示、没有后续操作的同族卡：如实未建模，不算错映射。
        # 但「有后续操作却没 tag」是漏映射 —— 注意 Rotom 是**祈使句**
        # （"Discard all Item cards …"），没有 "you"，所以不能按 "you discard"
        # 判，否则最典型的漏映射反而抓不到。
        if re.search(r"\b(?:discard|put|choose)\b", d, re.I):
            wrong = [str(t) for t in (effects or [])
                     if str(t).startswith(("discard_opp_random:",
                                           "discard_opp_to:",
                                           "shuffle_opp_hand:"))]
            if wrong:
                errs.append(
                    f"{card_name} · {item_name}：卡面是「揭示了手牌后**由使用者**"
                    f"选牌」，却落成随机口径 {wrong} —— 把一个可选决策退化成碰运气"
                    f"（方向反转）")
            else:
                errs.append(
                    f"{card_name} · {item_name}：卡面有「揭示手牌 + 后续操作」，"
                    f"却没有 `opp_hand:` tag（漏映射）")
        return errs

    # 卡面期望值
    want_op = "bottom" if re.search(r"bottom of their deck", d, re.I) else "discard"
    if re.search(r"\bitem cards?\b.*\bpok[eé]mon tool cards?\b", d, re.I):
        want_sel = "item+tool"
    elif re.search(r"\bitem cards?\b", d, re.I):
        want_sel = "item"
    elif re.search(r"\bspecial energy card\b", d, re.I):
        want_sel = "special_energy"
    elif re.search(r"\bbasic energy card\b", d, re.I):
        want_sel = "basic_energy"
    elif re.search(r"\benergy card\b", d, re.I):
        want_sel = "energy"
    elif re.search(r"\bsupporter card\b", d, re.I):
        want_sel = "supporter"
    else:
        want_sel = "any"
    m_up = re.search(r"discard up to (\d+)|choose up to (\d+)", d, re.I)
    if m_up:
        want_n = str(int(m_up.group(1) or m_up.group(2)))
    elif re.search(r"\bdiscard all\b", d, re.I):
        want_n = "all"
    else:
        want_n = "1"

    for t in tags:
        parts = t.split(":")
        got_op = parts[1] if len(parts) > 1 else ""
        got_sel = parts[2] if len(parts) > 2 else ""
        got_n = parts[3] if len(parts) > 3 else "1"
        if got_op != want_op:
            errs.append(f"{card_name} · {item_name}：卡面操作是 `{want_op}`，"
                        f"`{t}` 却是 `{got_op}`（去向不符）")
        if got_sel != want_sel:
            errs.append(f"{card_name} · {item_name}：卡面筛选是 `{want_sel}`，"
                        f"`{t}` 却是 `{got_sel}`（类别不符）")
        if got_n != want_n:
            errs.append(f"{card_name} · {item_name}：卡面张数是 `{want_n}`，"
                        f"`{t}` 却是 `{got_n}`（张数不符）")
    return errs


def audit_attach_spread(card_name: str, item_name: str, desc: str,
                        effects: list) -> list:
    """检索能量**自由分配附着**对账（2026-10-30 · 第三十类）

    「Search your deck for up to N Basic Energy cards and attach them to your
    Pokémon in any way you like」——引擎 `attach_spread:` 已能执行，但这一族
    最容易被写成下面四种**看起来正常**的错：

    1. **落成整库检索/单手**：卡面是「检索后**附着**」，却产出 `search_*`
       （进手牌）或普通 `attach`（只附 1 只）—— 少执行一步分配，手牌凭空
       多 N 张能量；
    2. **张数不符**：卡面「up to 2」落成 3（凭空变强）/ 落成 1（变弱）；
    3. **属性不符**：卡面限定 `{G}` 却落成 `any`（拿错颜色，等于任意配能）；
    4. **目标不符**：卡面「Benched Pokémon」落成 `any`（把能量附给战斗位）。

    另加**反向**守卫：卡面没有「in any way you like」却出现 `attach_spread:`
    （多半是句首锚定失效，把普通附能句式吃进来了）。
    """
    errs = []
    d = _norm(desc or "")
    has_spread = bool(re.search(r"in any way you like", d, re.I))
    tags = [str(t) for t in (effects or []) if str(t).startswith("attach_spread:")]
    if not has_spread:
        if tags:
            errs.append(f"{card_name} · {item_name}：卡面没有「in any way you "
                        f"like」，却出现 {tags} —— 认领越界")
        return errs
    if not tags:
        # 「in any way you like」却没有多目标 tag → 要么落成单手 attach，要么
        # 落成检索入手，两者都是少执行一步分配
        wrong = [str(t) for t in (effects or [])
                 if str(t).startswith(("search_", "attach:", "attach_energy:"))]
        if wrong:
            errs.append(f"{card_name} · {item_name}：卡面要求**自由分配**附着，"
                        f"却落成 {wrong} —— 少执行一步分配（能量进手牌或只附一只）")
        return errs

    # 卡面期望值（Joltik 这类双限额会有两条 tag，逐条比对同一套期望）
    want_target = "bench" if re.search(r"to your benched", d, re.I) else None
    if want_target is None:
        m_own = re.search(r"to your ([a-z\u2019']+)['\u2019]s pok[eé]mon", d, re.I)
        if m_own:
            want_target = f"cat-{m_own.group(1).lower()}"
        elif re.search(r"to your future pok[eé]mon", d, re.I):
            want_target = "cat-future"
    # 张数：单一形态取第二组；双形态按出现顺序各取一次
    counts = [int(x) for x in re.findall(r"search your deck for up to (\d+)", d, re.I)]
    letters = [x.lower() for x in
               re.findall(r"basic\s*\{([a-z])\}\s*energy", d, re.I)]
    for idx, t in enumerate(tags):
        parts = t.split(":")
        got_n = parts[1] if len(parts) > 1 else ""
        got_et = parts[2] if len(parts) > 2 else ""
        got_scope = "any"
        if "target" in parts:
            i = parts.index("target")
            got_scope = parts[i + 1] if i + 1 < len(parts) else ""
        if idx < len(counts) and got_n != str(counts[idx]):
            errs.append(f"{card_name} · {item_name}：卡面第 {idx + 1} 段是 up to "
                        f"{counts[idx]}，`{t}` 却是 {got_n}（张数不符）")
        if letters:
            if idx < len(letters):
                want_et = _TYPE_COLOR.get(letters[idx].upper(), letters[idx])
            else:
                want_et = "any"
        else:
            want_et = "any"
        if got_et != want_et:
            errs.append(f"{card_name} · {item_name}：卡面能量限定是 `{want_et}`，"
                        f"`{t}` 却是 `{got_et}`（属性不符）")
        if (want_target or "any") != got_scope:
            errs.append(f"{card_name} · {item_name}：卡面目标限定是 "
                        f"`{want_target or 'any'}`，`{t}` 却是 `{got_scope}`"
                        f"（目标不符）")
    return errs


def audit_energy_cost(card_name: str, item_name: str, desc: str,
                      effects: list) -> list:
    """能量代价**去向**对账（2026-11-02 · 第三十一类）

    招式里「弃能量」的代价有四种去向，引擎用不同 tag 表达，混用即静默算错：

      · ``discard_energy:N``        → 先弃**战斗位宝可梦身上**的能量，不足再
                                      从手牌补（对应「from this Pokémon」）
      · ``discard_energy:N:opp``    → 弃**对手**战斗位
      · ``discard_hand_energy:N``   → **只**动手牌（对应「from your hand」）

    最危险的错法是**去向反转**：卡面写「from your hand」，却落成
    ``discard_energy:N`` —— 对局里会去拔自家场上宝可梦的能量、手牌纹丝不动
    （Wugtrio ex / Mawile / Mega Clefable ex 三张真实卡曾如此）。

    另查**代价缺失**：卡面「Discard/Shuffle … Energy …, and this attack does …」
    里代价若没被映射，招式就白拿附加效果（Mega Eelektross ex 只产出 paralyze、
    Mega Skarmory ex 只剩 snipe）。这类必须如实未建模，不能进池。
    """
    errs = []
    d = _norm(desc or "")
    tags = [str(t) for t in (effects or [])]
    hand_tags = [t for t in tags if t.startswith("discard_hand_energy:")]
    field_tags = [t for t in tags if t.startswith("discard_energy:")]

    has_hand = bool(re.search(
        r"discard[^.]{0,50}?\benergy\b[^.]{0,24}?from your hand", d, re.I))
    has_self = bool(re.search(
        r"discard[^.]{0,50}?\benergy\b[^.]{0,26}?from (?:this|that) pok", d, re.I))

    # ① 卡面「从手牌弃」却落成「从场上弃」（方向反转，损失最大）
    if has_hand and field_tags:
        errs.append(f"{card_name} · {item_name}：卡面是从**手牌**弃能量，却落成 "
                    f"{field_tags} —— 对局会拔自家场上能量、手牌不动")
    # ② 卡面「从自身弃」却落成「只动手牌」
    if has_self and hand_tags and not re.search(r"from your hand", d, re.I):
        errs.append(f"{card_name} · {item_name}：卡面是从**该宝可梦**弃能量，却落成 "
                    f"{hand_tags} —— 代价落到手牌上")

    # ③ 代价子句被吞（`and` 绑定式）
    if _ENERGY_COST_CLAUSE.search(d):
        cost_tags = [t for t in tags if t.startswith(
            ("discard_energy", "discard_hand_energy", "energy_to_deck", "self:"))]
        if not cost_tags:
            errs.append(f"{card_name} · {item_name}：卡面有能量代价子句（and 绑定），"
                        f"却没有任何代价 tag —— 招式会白拿附加效果")

    # ④ 张数不符（只比对单一 up to N 形态）
    m = re.search(r"discard up to (\d+) energy", d, re.I)
    if m and (hand_tags or field_tags):
        want = int(m.group(1))
        for t in (hand_tags or field_tags):
            parts = t.split(":")
            got = 99 if parts[1] == "all" else (int(parts[1])
                                               if parts[1].isdigit() else -1)
            if got != want:
                errs.append(f"{card_name} · {item_name}：卡面「up to {want}」"
                            f"却落成 {t}")

    # ⑤ 反向守卫：卡面完全没有弃能量文本，却出现手牌弃能 tag
    if hand_tags and not re.search(r"\benergy\b", d, re.I):
        errs.append(f"{card_name} · {item_name}：卡面无能量文本，却出现 "
                    f"{hand_tags} —— 认领越界")
    return errs


def audit_search_kind(card_name: str, item_name: str, desc: str,
                      effects: list) -> list:
    """检索的**去向**与**类别**两级对账（2026-10-27 扩展）

    此前只对账类别，于是两类错映射都能混过去：
      · 「search for a Basic Pokémon **and put it onto your Bench**」被放通成
        ``search_pokemon``（入手）—— 实际去向是**后备**（`search_bench`）；
      · 「search for a **Pokémon Tool** card」被判成 ``search_pokemon`` ——
        Tool 属于**训练家卡**（`search_trainer`）。
    现在先判去向（evolve / attach / bench），只有「入手」才继续判类别。
    """
    errs = []
    # 附能**去向**：`:target:bench` 必须与卡面「Benched」一致
    for t in effects:
        if str(t).startswith("attach") and ":target:bench" in str(t):
            if "benched" not in (desc or "").lower():
                errs.append(f"{card_name} · {item_name}：`{t}` 声称附到后备，"
                            f"卡面却未写 Benched")
    m = SEARCH_FOR.search(desc or "")
    if not m:
        return errs
    seg = _norm(m.group(1))
    clause = _norm(re.split(r"[.]", (desc or "")[m.start():], maxsplit=1)[0])
    got = [t for t in effects if str(t).startswith(("search_", "attach",
                                                    "evolve_from_deck",
                                                    "evolve_each_bench"))]

    # ---- ① 去向 ----
    if "to evolve it" in clause or "to evolve that" in clause:
        # 「For each of your Benched Pokémon, …」= 逐只后备各进化一次
        # （Reuniclus「Cellular Ascension」）→ `evolve_each_bench`
        # ⚠ 「for each …」在 SEARCH_FOR 匹配点**之前**，必须看整段卡面。
        if re.search(r"for each of your benched", _norm(desc or "")):
            if not any(str(t) == "evolve_each_bench" for t in effects):
                errs.append(f"{card_name} · {item_name}：逐只后备**各进化一次** "
                            f"→ 应为 `evolve_each_bench`，实际 {got}")
            return errs
        if not any(str(t).startswith("evolve_from_deck") for t in effects):
            errs.append(f"{card_name} · {item_name}：检索后**直接进化** "
                        f"→ 应为 `evolve_from_deck`，实际 {got}")
        return errs
    if re.search(r"\band attach\b", clause):
        if not any(str(t).startswith("attach") for t in effects):
            errs.append(f"{card_name} · {item_name}：检索后**附着**（非入手）"
                        f"→ 应为 `attach:…`，实际 {got}")
        return errs
    if "onto your bench" in clause or "onto their bench" in clause:
        if not any(str(t).startswith("search_bench") for t in effects):
            errs.append(f"{card_name} · {item_name}：检索后**铺到后备**"
                        f"→ 应为 `search_bench:…`，实际 {got}")
        return errs

    # ---- ② 类别（仅「入手」形态）----
    # 「a number of cards up to the number of your Benched Pokémon」（Scrafty）：
    # 检索对象是**任意卡**，句中的 "Benched Pokémon" 是**计数源**、不是检索类别
    # —— 必须先判，否则会被下面的 pokemon 关键词抢走。
    if re.search(r"\b(?:a|any) number of cards\b", seg):
        want = "search_any"
    elif re.search(r"\btool card\b|\bitem card\b|\bstadium card\b|"
                   r"\bsupporter card\b|\btrainer card\b", seg):
        want = "search_trainer"
    elif "energy" in seg:
        want = "search_energy"
    elif "pokemon" in seg or "pok\u00e9mon" in seg:
        want = "search_pokemon"
    elif "trainer" in seg:
        want = "search_trainer"
    else:
        return errs
    got_hand = [t for t in effects if str(t).startswith("search_")]
    if got_hand and not any(str(t).startswith(want) for t in got_hand):
        errs.append(f"{card_name} · {item_name}：检索子句是 `{seg[:50]}` "
                    f"→ 应为 `{want}`，实际 {got_hand}")
    return errs


def audit_energy_target(card_name: str, item_name: str, desc: str,
                        effects: list) -> list:
    """「弃**对手**战斗位能量」必须带 `:opp`，否则会弃到自家（静默弃错对象）"""
    errs = []
    for m in re.finditer(r"discard\s+(?:an?|1|\d+)\s+(?:basic\s+|special\s+)?"
                         r"(?:\{\s*[A-Za-z]\s*\}\s*)?energy(?:\s+card)?\s+from\s+"
                         r"([^.;,]{0,40})", desc or "", re.I):
        seg = _norm(m.group(1))
        if "opponent" not in seg:
            continue
        bad = [t for t in effects
               if str(t).startswith(("discard_energy:", "coin"))
               and "discard_energy" in str(t) and ":opp" not in str(t)]
        if bad:
            errs.append(f"{card_name} · {item_name}：弃**对手**能量却用了弃自身的 "
                        f"tag（{bad}）")
    return errs


# 跨回合伤害修正（2026-10-29 第十六批）：这三句是**本回合打出去的修正**，
# 不是常驻静态层 —— 静态伤害对账必须让位，否则会被误判「缺 fx:static_dmg」。
_NEXT_TURN_CLAUSES = (
    (re.compile(r"the Defending Pok[eé]mon takes (\d+) more damage from "
                r"attacks \(after applying Weakness and Resistance\)", re.I),
     1, "dmg_taken_plus_next"),
    (re.compile(r"attacks used by the Defending Pok[eé]mon do (\d+) less "
                r"damage \(before applying Weakness and Resistance\)", re.I),
     1, "atk_minus_next"),
    (re.compile(r"this Pok[eé]mon[\u2019']?s ([A-Za-z][A-Za-z'\- ]{1,30}?) attack "
                r"does (\d+) more damage \(before applying Weakness and "
                r"Resistance\)", re.I),
     2, "self_attack_plus_next"),
)


def _next_turn_spans(desc: str) -> list:
    # 不换行空格（\xa0）：「damage\xa0(before applying …)」实测存在于 Pawmi。
    # 只做 1:1 字符替换，**不改变偏移**，`m.start()` 仍可与原文对齐。
    desc = (desc or "").replace("\u00a0", " ")
    spans = []
    for rx, _ng, _p in _NEXT_TURN_CLAUSES:
        for m in rx.finditer(desc):
            spans.append((m.start(), m.end()))
    return spans


def audit_next_turn_dmg(card_name: str, item_name: str, desc: str,
                        effects: list) -> list:
    """跨回合伤害修正：卡面写了这三句就必须有对应 tag（第十五类对账）

    这三句的语义是「本次招式结算后留下一个**只在下一回合有效**的修正」，
    与恒定的 `fx:static_dmg` 完全不同；漏映射会静默少算/多算一整回合的伤害。
    """
    errs = []
    desc = (desc or "").replace("\u00a0", " ")
    for rx, ng, prefix in _NEXT_TURN_CLAUSES:
        for m in rx.finditer(desc):
            want = f"{prefix}:{int(m.group(ng))}"
            if not any(str(t) == want or str(t).startswith(want + ":")
                       for t in effects):
                errs.append(f"{card_name} · {item_name}：跨回合伤害修正 "
                            f"`{m.group(0)[:60]}` 缺 `{want}` tag")
    return errs


def audit_static_damage(card_name: str, item_name: str, desc: str,
                        effects: list) -> list:
    """静态伤害增减：卡面写了「attacks used by … do N more/less damage」
    就必须有对应的 `fx:static_dmg:*`；来源限定的减伤不得退化成无条件减伤。

    历史风险：`This Pokémon takes 30 less damage from attacks from your
    opponent's {R} or {W} Pokémon` 曾被解析成无条件的
    `passive:dmg_reduce:30` —— 对所有属性都减伤（静默放大）。
    """
    errs = []
    seen_static = set()
    next_spans = _next_turn_spans(desc)
    for m in re.finditer(r"attacks used by [^.]{0,140}? do(?:es)? (\d+) "
                         r"(more|less) damage", desc or "", re.I):
        if any(s <= m.start() < e for s, e in next_spans):
            continue                     # 跨回合修正句，由第十五类接管
        n = int(m.group(1))
        direction = "add" if m.group(2).lower() == "more" else "sub"
        want = f"fx:static_dmg:{direction}:{n}:"
        seen_static.add(m.start())
        if not any(str(t).startswith(want) for t in effects):
            errs.append(f"{card_name} · {item_name}：静态伤害 "
                        f"`{m.group(0)[:60]}` 缺 `{want}…` tag")
    for m in re.finditer(r"takes? (\d+) less damage from attacks? "
                         r"(?:from|used by|by)\b", desc or "", re.I):
        if m.start() in seen_static:
            continue
        n = int(m.group(1))
        head = f"passive:dmg_reduce:{n}"
        typed = [t for t in effects
                 if str(t).startswith(head + ":from:type:")]
        colors = [_TYPE_COLOR.get(c.upper()) for c in re.findall(
            r"\{([a-z])\}", (desc or "")[m.start():m.start() + 160], re.I)]
        colors = [c for c in colors if c]
        if colors:
            want = head + ":from:type:" + ",".join(sorted(set(colors)))
            got = [t for t in effects if str(t).startswith(head)]
            if not any(str(t) == want for t in got):
                errs.append(f"{card_name} · {item_name}：来源属性限定的减伤 "
                            f"`{m.group(0)[:60]}` 应为 `{want}`，实际 {got}")
        elif typed:
            errs.append(f"{card_name} · {item_name}：来源限定减伤不得退化为 "
                        f"无条件/仅属性 tag（{typed}）")
    return errs


_IMM_SRC_TEXT = {
    # tag 限定 → 卡面必须出现的短语（正则）。顺序敏感：basic_ex 在 ex 之前判。
    "basic_ex": r"basic pok[eé]mon \{ex\}",
    "ex": r"pok[eé]mon \{ex\}",
    "tera": r"tera pok[eé]mon",
    "has_ability": r"pok[eé]mon that (?:have|has) an ability",
    "special_energy": (r"pok[eé]mon that (?:have|has) any "
                       r"special energy attached"),
    "basic": r"attacks? from basic pok[eé]mon",
    "basic_non_colorless": r"basic non-\{c\} pok[eé]mon",
}
_IMM_TAG_MARKS = ("passive:dmg_immune:", "passive:bench_immune",
                  "shield:prevent_all")


def audit_static_immune(card_name: str, item_name: str, desc: str,
                        effects: list) -> list:
    """静态免伤 / 护盾的来源限定 ↔ 卡面（2026-10-28 第八批）

    两个方向都查：
      ① **tag → 卡面**：`passive:dmg_immune:…from:<src>` / `shield:prevent_all:…`
         里的每一条限定，都必须在卡面原文里找得到对应短语。否则就是「引擎按
         A 来源免伤、卡面写的是 B」的静默错映射。
      ② **卡面 → tag**：卡面写了「prevent all damage …」却没有一条免伤/护盾 tag
         → 效果被静默丢弃（或该卡本不该进池）。
    """
    errs: list = []
    low = _norm(desc)
    tag_blob = [str(t) for t in effects if any(m in str(t) for m in _IMM_TAG_MARKS)]

    for tag in tag_blob:
        # 只解释被动免伤与护盾两族；bench_immune 无参数
        if "dmg_immune" in tag:
            parts = tag.split(":")
            scope = parts[2] if len(parts) > 2 else ""
            if scope == "team_bench":
                if "to your benched pok" not in low:
                    errs.append(f"{card_name} · {item_name}：`{tag}` 是全队后备免伤，"
                                f"卡面却未写 to your Benched Pokémon")
            elif "to this pok" not in low:
                errs.append(f"{card_name} · {item_name}：`{tag}` 是自身免伤，"
                            f"卡面却未写 to this Pokémon")
            rest = ":".join(parts[3:])
            for src, rx in _IMM_SRC_TEXT.items():
                if f"from:{src}" not in tag:
                    continue
                if not re.search(rx, low):
                    errs.append(f"{card_name} · {item_name}：`{tag}` 的来源限定 "
                                f"`{src}` 与卡面不符")
                break
            m = re.search(r":min:(\d+)", tag)
            if m and f"if that damage is {m.group(1)} or more" not in low:
                errs.append(f"{card_name} · {item_name}：`{tag}` 的伤害门槛与卡面不符")
            if "no_rule_box" in tag and "rule box" not in low:
                errs.append(f"{card_name} · {item_name}：`{tag}` 限定无规则框，"
                            f"卡面却未写 Rule Box")
        elif "shield:prevent_all" in tag:
            for src, rx in _IMM_SRC_TEXT.items():
                if f"from:{src}" not in tag:
                    continue
                if not re.search(rx, low):
                    errs.append(f"{card_name} · {item_name}：`{tag}` 的来源限定 "
                                f"`{src}` 与卡面不符")
                break
            m = re.search(r":if_damage:le:(\d+)", tag)
            if m and f"if that damage is {m.group(1)} or less" not in low:
                errs.append(f"{card_name} · {item_name}：`{tag}` 的伤害门槛与卡面不符")
        # 「effects」= 连效果一起挡：卡面必须写 from and effects of attacks
        if tag.endswith(":effects") and "from and effects of attacks" not in low:
            errs.append(f"{card_name} · {item_name}：`{tag}` 连效果一起挡，"
                        f"卡面却未写 from and effects of attacks")

    # ② 卡面写了免伤却没有对应 tag
    if "prevent all damage" in low and not tag_blob:
        errs.append(f"{card_name} · {item_name}：卡面写了 prevent all damage，"
                    f"却没有免伤/护盾 tag（效果被静默丢弃）")
    return errs


_KO_TEXT = {
    "both_active": r"both active pok[eé]mon are knocked out",
    "opp_active:basic": (r"knock out your opponent'?s active basic pok[eé]mon"
                         # 条件被动语态（Haxorus「Axe Blast」）
                         r"|your opponent'?s active pok[eé]mon is a basic "
                         r"pok[eé]mon, it is knocked out"),
    "opp_active": (r"knock out your opponent'?s active pok[eé]mon"
                   r"|your opponent'?s active pok[eé]mon is knocked out"),
    "opp_bench:basic": r"knock out 1 of your opponent'?s benched basic pok[eé]mon",
    "opp_bench": r"knock out 1 of your opponent'?s benched pok[eé]mon",
    "opp_any": r"knock out 1 of your opponent'?s pok[eé]mon",
}


def audit_knockout(card_name: str, item_name: str, desc: str,
                   effects: list) -> list:
    """招式击倒的**目标范围** ↔ 卡面（2026-10-28 第九批）

    击倒一只宝可梦直接决定奖赏与胜负 —— 范围错了就是**静默改变胜负**：
      · `knockout:opp_active` 却写的是「1 of your opponent's Pokémon」→ 少打了后备；
      · `knockout:opp_counters:6` 的指示物数必须与卡面逐字一致。
    """
    errs: list = []
    low = _norm(desc)
    kos = [str(t) for t in effects if "knockout:" in str(t)]
    for tag in kos:
        scope = tag.split("knockout:", 1)[1]
        if scope.startswith("opp_counters:"):
            n = scope.split(":")[1]
            if f"exactly {n} damage counters" not in low:
                errs.append(f"{card_name} · {item_name}：`{tag}` 的指示物数"
                            f"与卡面不符")
            continue
        rx = _KO_TEXT.get(scope)
        if rx is None:
            errs.append(f"{card_name} · {item_name}：未识别的击倒范围 `{scope}`")
            continue
        if not re.search(rx, low):
            errs.append(f"{card_name} · {item_name}：`{tag}` 与卡面目标不符")
    if re.search(r"knock out", low) and not kos:
        errs.append(f"{card_name} · {item_name}：卡面写了 Knock Out，"
                    f"却没有 knockout tag（击倒被静默丢弃）")
    # 被击倒**触发**（第十批）：tag 与卡面必须逐项对上
    for tag in (str(t) for t in effects):
        if tag.startswith("ko_prize_plus:"):
            m = re.search(r"take (\d+) more prize card", low)
            if not m or f"ko_prize_plus:{int(m.group(1))}" not in tag:
                errs.append(f"{card_name} · {item_name}：`{tag}` 与卡面不符")
        elif tag.startswith("on_ko:counter:"):
            m = re.search(r"put (\d+) damage counters? on the attacking pok",
                          low)
            if not m or f"on_ko:counter:{int(m.group(1)) * 10}" != tag:
                errs.append(f"{card_name} · {item_name}：`{tag}` 与卡面不符")
        elif tag.startswith("on_ko:energy_to_hand:"):
            if "basic" not in low or "into your hand" not in low:
                errs.append(f"{card_name} · {item_name}：`{tag}` 与卡面不符")
    # 全量回复：范围必须写进 tag
    if "heal all damage" in low:
        # 抛币分支里的全量回复由子 tag 承载：`coin_all:2:heads:heal:all:choose`
        heals = [str(t) for t in effects if "heal:all" in str(t)]
        if not heals:
            errs.append(f"{card_name} · {item_name}：卡面写了 heal all damage，"
                        f"却没有 `heal:all…` tag")
        elif not any("heal:all:" in t for t in heals):
            # 只有 `heal:all`（施术者自身）——仅当卡面是 "from this Pokémon" 才合法
            if "from this pok" not in low:
                errs.append(f"{card_name} · {item_name}：全量回复缺范围"
                            f"（{heals}），会压成只回自身")
    return errs


_FACTION_TEXT = {"ns": "n'?s", "hops": "hop'?s", "stevens": "steven'?s",
                 "marnies": "marnie'?s", "erikas": "erika'?s"}


def audit_stadium_form(card_name: str, desc: str, tag: str) -> list:
    """场地静态规则 ↔ 卡面（2026-10-28 第九批）

    `stadium:<kind>[:…:faction:<k>]` 的每个限定都必须在卡面里找得到对应短语；
    阵营键（`ns`/`hops`/`stevens`）尤其危险 —— 写错一个字母就会把
    「N 的宝可梦无撤退费用」变成「所有宝可梦」或「没有宝可梦」。
    """
    errs: list = []
    if not str(tag).startswith("stadium:"):
        return errs
    low = _norm(desc)
    kind = str(tag).split(":")[1] if len(str(tag).split(":")) > 1 else ""
    need = {
        "no_tool_effect": r"have no effect",
        "status_immune": r"special conditions",
        "evolve_same_turn": r"can evolve into",
        "dmg_immune": r"rule box",
        "no_retreat": r"have no retreat cost",
        "attack_plus": r"more damage",
        "dmg_reduce": r"less damage",
        # 第十一批：复杂场地
        "bench_limit": r"tera pok[eé]mon in play can have up to \d+ pok[eé]mon",
        "no_bench_counters": r"damage counters from being placed on benched",
        "use": r"",
    }.get(kind)
    if kind in ("no_tool_effect", "status_immune", "evolve_same_turn",
                "dmg_immune", "no_retreat", "attack_plus", "dmg_reduce",
                "bench_limit", "no_bench_counters"):
        if not need or not re.search(need, low):
            errs.append(f"{card_name}：`{tag}` 与卡面不符")
    m = re.search(r"faction:([a-z0-9]+)", str(tag))
    if m:
        key = m.group(1)
        rx = _FACTION_TEXT.get(key)
        if rx is None:
            errs.append(f"{card_name}：未识别的阵营键 `{key}`")
        elif not re.search(rx, low):
            errs.append(f"{card_name}：`{tag}` 的阵营与卡面不符")
    if kind == "evolve_same_turn":
        m = re.search(r"\{([a-z])\}", low)
        if not m or f"type:{_TYPE_COLOR.get(m.group(1).upper())}" not in str(tag):
            errs.append(f"{card_name}：`{tag}` 的属性与卡面不符")
    if str(tag).startswith("stadium:use:evolve_from_deck"):
        if "stage 1 pok" not in low or "basic pok" not in low:
            errs.append(f"{card_name}：`{tag}` 与卡面不符（缺 Stage 1 / Basic）")
        if "chain:1" in str(tag) and "stage 2 pok" not in low:
            errs.append(f"{card_name}：`{tag}` 标了链式进化，卡面却没有 Stage 2")
    return errs


def audit_counter_side(card_name: str, item_name: str, desc: str,
                       effects: list) -> list:
    """计数源**阵营**不得反转（`your` ↔ `your opponent's`）

    历史战果（2026-09-21）：`_COUNTER_PHRASES` 原是一张不分阵营的纯子串表，
    于是 `for each of **your** Benched Pokémon` 命中 `benched pok` → 被算成**对手**
    后备数；`for each Prize card your opponent **has taken**` 被算成**剩余**奖赏卡。
    实测池内 48 条缩放招式里 10 条映射错。
    """
    errs = []
    m = re.search(r"for each\s+([^.]{4,90})", desc or "", re.I)
    phrase = _norm(m.group(1)) if m else ""
    if not phrase:
        return errs
    has_opp = "opponent" in phrase
    has_own = "your" in phrase
    for t in effects:
        t = str(t)
        if not t.startswith(("dmg_plus:", "dmg_set:")) or ":per:" not in t:
            continue
        counter = t.split(":per:")[1]
        if has_opp and counter.startswith("own_"):
            errs.append(f"{card_name} · {item_name}：`{phrase[:50]}` 是**对手**侧，"
                        f"却用了 `{counter}`（阵营反转）")
        if has_own and not has_opp and counter.startswith("opp_"):
            errs.append(f"{card_name} · {item_name}：`{phrase[:50]}` 是**我方**侧，"
                        f"却用了 `{counter}`（阵营反转）")
    return errs


def audit_set_per(card_name: str, item_name: str, desc: str,
                  effects: list) -> list:
    """「does N damage for each X」必须是**基础伤害重算**（`dmg_set`）

    历史缺陷（2026-10-27）：解析器把 `mode=set_per` 也落成
    `dmg_plus:N:per:<counter>`，引擎于是算成「印刷伤害 + N×X」。
    Passimian 印刷 20、`dmg_plus:20:per:own_basic` → 场上 3 只基础宝可梦时
    打 80；正确是 60（0 只时更明显：应为 0，旧逻辑固定打 20）。
    """
    errs = []
    for m in re.finditer(r"does\s+(\d+)\s+damage\s+for\s+each\s+([^.]{4,90})",
                         desc or "", re.I):
        n = int(m.group(1))
        phrase = _norm(m.group(2))
        if re.fullmatch(r"(heads|tails)\s*\.?", phrase, re.I):
            side = phrase.split(".")[0].strip().lower()
            want = f":{side}:dmg_set:{n}"
            got = [str(t) for t in effects
                   if str(t).startswith("coin_each:") and want in str(t)]
            if not got:
                errs.append(f"{card_name} · {item_name}：`{m.group(0)[:60]}` "
                            f"应按正面/反面数重算基础伤害 `{want[1:]}…`，"
                            f"实际 {[str(t) for t in effects if 'dmg_' in str(t)]}")
            continue
        got = [str(t) for t in effects
               if str(t).startswith(f"dmg_set:{n}:per:")]
        if not got:
            wrong = [str(t) for t in effects
                     if str(t).startswith(f"dmg_plus:{n}:per:")]
            errs.append(f"{card_name} · {item_name}：`{m.group(0)[:60]}` "
                        f"应按计数源重算基础伤害 `dmg_set:{n}:per:…`，"
                        f"实际 {wrong or [str(t) for t in effects if 'dmg_' in str(t)]}")
    return errs


def audit_fossil(card_name: str, desc: str, tag: str, payload: dict) -> list:
    """化石卡（Fossil）：卡面 ↔ ``fossil:<hp>:<ptype>`` + 上场形态元数据

    卡面原型：「Play this card as if it were a N-HP Basic {C} Pokémon. This card
    can't be affected by any Special Conditions and can't retreat. …」

    引擎按这三条建模（`_trainer_play_fossil` / `_status_immune` / `_do_retreat`），
    因此卡面必须逐字写明；HP 与属性必须与 tag 一致，且 `fossil.payload.hp`
    与实际上场形态一致（否则「上场是 60HP」会变成另一种数值）。
    """
    errs: list = []
    m = re.search(r"play this card as if it were a (\d+)-hp basic \{([a-z])\} "
                  r"pok[eé]mon", _norm(desc), re.I)
    if not m:
        return [f"[化石] {card_name}：有 fossil tag 但卡面没有 play-as-Pokémon 句"]
    parts = str(tag).split(":")
    if len(parts) != 3 or not parts[1].isdigit():
        return [f"[化石] {card_name}：fossil tag 形态非法（{tag}）"]
    hp, color = int(parts[1]), parts[2]
    want_color = _TYPE_COLOR.get(m.group(2).upper())
    if int(m.group(1)) != hp:
        errs.append(f"[化石] {card_name}：卡面 HP {m.group(1)} ↔ tag {hp}")
    if want_color != color:
        errs.append(f"[化石] {card_name}：卡面属性 {want_color} ↔ tag {color}")
    if "can't retreat" not in _norm(desc):
        errs.append(f"[化石] {card_name}：卡面未写 can't retreat（引擎按不可撤退执行）")
    if "special conditions" not in _norm(desc):
        errs.append(f"[化石] {card_name}：卡面未写特殊状态免疫（引擎按免疫执行）")
    if int((payload or {}).get("hp") or 0) != hp:
        errs.append(f"[化石] {card_name}：上场形态 HP {(payload or {}).get('hp')} "
                    f"↔ tag {hp}")
    return errs


_SENERGY_REQUIRED = {
    "mist": ("provides {c} energy", "prevent all effects of attacks",
             "damage is not an effect"),
    "rock_fighting": ("provides {f} energy", "prevent all effects of attacks",
                      "the {f} pokemon"),
    "grow_grass": ("provides {g} energy", "+20 hp", "the {g} pokemon"),
    "enriching": ("provides {c} energy", "draw 4 cards"),
    "spiky": ("provides {c} energy", "damaged by an attack",
              "put 2 damage counters on the attacking pokemon"),
    "legacy": ("provides every type of energy", "takes 1 fewer prize card",
               "once per game"),
    "neo_upper": ("provides {c} energy", "stage 2 pokemon",
                  "provides only 2 energy"),
    "prism": ("provides {c} energy", "basic pokemon", "provides only 1 energy"),
    "ignition": ("discard it at the end of your turn", "provides {c} energy",
                 "{c}{c}{c}", "evolution pokemon"),
    "team_rocket": ("team rocket's pokemon",
                    "provides 2 in any combination of {p} energy and {d} energy"),
    "telepath": ("provides {p} energy",
                 "search your deck for up to 2 basic {p} pokemon"),
}


def audit_senergy(card_name: str, desc: str, tag: str) -> list:
    """特殊能量：卡面必须包含该 kind 的**全部**关键子句

    特殊能量是"一段一张卡"的机制：kind 决定了提供量、触发与限制。若卡面只包含
    其中一部分（例如只写"provides {C} energy"），引擎却按 kind 执行全套规则，
    就是**多算了没有的规则**（静默错算）。
    """
    parts = str(tag).split(":", 1)
    kind = parts[1] if len(parts) > 1 else ""
    need = _SENERGY_REQUIRED.get(kind)
    if need is None:
        return [f"[特殊能量] {card_name}：引擎未建模的 kind（{tag}）"]
    low = _norm(desc).replace("é", "e")
    missing = [s for s in need if s not in low]
    if missing:
        return [f"[特殊能量] {card_name}：{tag} 缺少卡面子句 {missing}"]
    return []


# 抛币状态（2026-10-29 第二十二批 · 第二十类）
_COIN_PREFIXES = ("coin:", "coin_each:", "coin_all:", "coin_until_tails:")
_STATUS_NAMES = ("poison", "burn", "sleep", "paralyze", "confuse")
_STATUS_WORD_TO_TAG = {"poisoned": "poison", "burned": "burn",
                       "asleep": "sleep", "paralyzed": "paralyze",
                       "confused": "confuse"}
_COND_MODES = ("all_tails", "all_heads", "at_least_heads", "at_least_tails")


def audit_coin_status(card_name: str, item_name: str, desc: str,
                      effects: list) -> list:
    """抛币状态子效果对账（第二十类）

    两类会**静默不生效/静默少施加**的形态必须拦下：

      · 状态名用了卡面英文词（``coin:heads:paralyzed``）—— 引擎效果派发只认
        `STATUSES`（paralyze/burn/…），旧拼写会走到「未知 tag」被忽略；
      · 多币条件（``cond:``）的形态/状态名非法 —— `_coin_cond_result` 未命中时
        会抛错或什么都不做。
    """
    errs: list = []
    low = _norm(desc or "")
    has_canonical_status = False
    for e in effects:
        tag = str(e)
        if not tag.startswith(_COIN_PREFIXES):
            continue
        parts = tag.split(":")
        if tag.startswith("coin_until_tails:"):
            subs = [":".join(parts[1:])]
        else:
            subs = [":".join(parts[3:])] if tag.startswith(
                ("coin_each:", "coin_all:")) else [":".join(parts[2:])]
        for chunk in "|".join(subs).split("|"):
            if not chunk:
                continue
            if chunk.startswith("cond:"):
                body = chunk[len("cond:"):].split(":")
                if body[0] not in _COND_MODES:
                    errs.append(f"{card_name} · {item_name}：`{tag}` 的多币条件"
                                f" `{body[0]}` 引擎不支持")
                    continue
                # 状态在模式（以及可能有的阈值）之后
                st = body[-1]
                if st in _STATUS_WORD_TO_TAG:
                    errs.append(f"{card_name} · {item_name}：`{tag}` 的状态名"
                                f" `{st}` 非规范（应为 "
                                f"{_STATUS_WORD_TO_TAG[st]}）")
                elif st in _STATUS_NAMES:
                    has_canonical_status = True
                continue
            if chunk in _STATUS_WORD_TO_TAG:
                errs.append(f"{card_name} · {item_name}：`{tag}` 的状态名 "
                            f"`{chunk}` 非规范（引擎不识别，状态会静默不生效）")
            elif chunk in _STATUS_NAMES:
                has_canonical_status = True
    # 卡面写了「抛币后陷入状态」但没有任何 coin 状态子效果 → 静默少施加
    if re.search(r"flip\s+(?:a\s+)?(?:\d+\s+)?coins?", low) \
            and re.search(r"is\s+now\s+(?:asleep|burned|confused|paralyzed|"
                          r"poisoned)", low) and not has_canonical_status:
        errs.append(f"{card_name} · {item_name}：卡面写有抛币状态，但没有任何 "
                    "`coin…:<规范状态名>` 子效果（状态会静默不施加）")
    return errs


# 附着能量收回手牌（2026-10-29 第二十三批 · 第二十一类）
_ENERGY_TO_HAND_RE = re.compile(
    r"put\s+(?:an?|\d+)\s+(?:\{[a-z]\}\s+)?energy\s+attached\s+to\s+"
    r"(?:this\s+pok[eé]mon|your\s+opponent[\u2019']?s\s+active\s+pok[eé]mon)\s+"
    r"into\s+(?:your|their)\s+hand", re.I)


# 弃置对手战斗位特殊能量（2026-10-29 第二十四批 · 第二十二类）
_SP_DISCARD_RE = re.compile(
    r"(?:discard|remove)\s+(?:a|an|1|all)\s+special\s+energy\s+from\s+"
    r"your\s+opponent[\u2019']?s\s+active\s+pok[eé]mon", re.I)

# 伤害前阶段（2026-10-29 第二十五批 · 第二十三类）
_PRE_TOOL_RE = re.compile(
    r"before\s+doing\s+damage,\s*discard\s+all\s+pok[eé]mon\s+tools?"
    r"(?:\s+and\s+special\s+energy)?\s+from\s+your\s+opponent[\u2019']?s\s+"
    r"active\s+pok[eé]mon", re.I)
_IF_PRE_TOOL_STATUS_RE = re.compile(
    r"if\s+you\s+discarded\s+a\s+pok[eé]mon\s+tool\s+in\s+this\s+way", re.I)


def audit_special_energy_discard(card_name: str, item_name: str, desc: str,
                                 effects: list) -> list:
    """「Discard a Special Energy from your opponent's Active Pokémon」→ `:special`

    ⚠ 必须落 `discard_energy:…:opp:special`：普通 `discard_energy:…:opp` 走
    `remove_any`（无色/基本优先），对手只贴基本能量时会把**本不该生效**的效果
    生效掉（静默错算）。
    """
    errs = []
    d = PAREN.sub(" ", desc or "")
    for m in _SP_DISCARD_RE.finditer(d):
        ok = any(str(e).startswith("discard_energy:") and ":special" in str(e)
                 for e in effects)
        if not ok:
            errs.append(f"{card_name} · {item_name}：`{m.group(0)[:60]}` "
                        "缺 `discard_energy:…:opp:special`（普通弃能会弃错基本能量）")
    return errs


def audit_energy_to_hand(card_name: str, item_name: str, desc: str,
                         effects: list) -> list:
    """「Put N Energy attached to X into your/their hand」→ 必须 `bounce:energy:`

    ⚠ 与「弃置能量」（`discard_energy:`）不同：回手是**把卡收回手牌**，
    写错成弃置会静默丢掉一张可再用的能量卡。
    """
    errs = []
    d = PAREN.sub(" ", desc or "")
    for m in _ENERGY_TO_HAND_RE.finditer(d):
        if not any(str(e).startswith("bounce:energy:") for e in effects):
            errs.append(f"{card_name} · {item_name}：`{m.group(0)[:60]}` "
                        "缺 `bounce:energy:` tag（能量回手未建模）")
    return errs


def audit_on_damaged(card_name: str, item_name: str, desc: str,
                     effects: list) -> list:
    """受击反应：按能量数 / 下回合反制（2026-10-29 第二十九批 · 第二十六类对账）

    · 特性侧「… put N damage counters on the Attacking Pokémon **for each {M}
      Energy attached to this Pokémon**」→ 必须 `on_damaged:counter_per:<n>:<color>`
      （写成固定 `on_damaged:counter:` 会**少算**：能量越多反制越强）。
    · 招式侧「**During your opponent's next turn**, if this Pokémon is damaged by an
      attack …, put N damage counters on the Attacking Pokémon[ equal to the damage
      done]」→ 必须 `retaliate_next:<N*10|equal>`（只写普通 `self:`/无 tag 等于
      这一整段效果丢失）。
    """
    errs = []
    d = PAREN.sub(" ", _norm(desc or ""))
    tags = [str(e) for e in effects]
    if "damaged by an attack" not in d:
        return errs
    m = re.search(r"(?:put|place)\s+(\d+)\s+damage\s+counters?\s+on\s+(?:the\s+)?"
                  r"attacking\s+pok[eé]mon\s+for\s+each\s+\{([a-z])\}\s+energy\s+"
                  r"attached\s+to\s+this\s+pok[eé]mon", d)
    if m:
        color = _TYPE_COLOR.get(m.group(2).upper())
        want = f"on_damaged:counter_per:{int(m.group(1))}:{color}"
        if not any(t == want for t in tags):
            errs.append(f"{card_name} · {item_name}：`{m.group(0)[:60]}` 缺 "
                        f"`{want}`（固定值会少算：能量越多反制越强）")
        return errs
    if "during your opponent's next turn" in d:
        if re.search(r"equal to the damage done to this pok[eé]mon", d):
            if "retaliate_next:equal" not in tags:
                errs.append(f"{card_name} · {item_name}：下回合「反制 = 所受伤害」"
                            "缺 `retaliate_next:equal`")
            return errs
        m = re.search(r"(?:put|place)\s+(\d+)\s+damage\s+counters?\s+on\s+"
                      r"(?:the\s+)?attacking\s+pok[eé]mon", d)
        if m:
            want = f"retaliate_next:{int(m.group(1)) * 10}"
            if want not in tags:
                errs.append(f"{card_name} · {item_name}：下回合反制 `{m.group(0)[:60]}` "
                            f"缺 `{want}`（整段效果丢失）")
    return errs


def audit_energy_to_deck(card_name: str, item_name: str, desc: str,
                         effects: list) -> list:
    """弃牌堆基本能量洗回牌库（2026-10-29 第二十八批 · 第二十五类对账）

    卡面原型：「This attack does 20 damage for each Basic {W} Energy card in your
    discard pile. **Then, shuffle those cards into your deck.**」（Kyogre）/
    「Put 2 damage counters … for each Basic {G} Energy card … Then, shuffle those
    Energy cards into your deck.」（Sinistcha ex）

    ⚠ 只建模「按弃牌堆能量计数」而**不洗回**，下一次攻击会把同一批能量再数一遍
    —— 静默加强（实测 Kyogre 在池内就是这种状态，本批修复）。
    """
    errs = []
    d = PAREN.sub(" ", desc or "")
    if not re.search(r"shuffle those (?:energy )?cards? into your deck", d, re.I):
        return errs
    colors = {_TYPE_COLOR.get(m.group(1).upper()) for m in
              re.finditer(r"for each basic \{([a-z])\} energy card in your discard "
                          r"pile", d, re.I)}
    colors.discard(None)
    if not colors:
        errs.append(f"{card_name} · {item_name}：卡面写了「shuffle those … cards "
                    "into your deck」，但计数源不是「弃牌堆基本能量」→ 不该进池")
        return errs
    for color in sorted(colors):
        want = f"energy_to_deck:{color}:all"
        if want not in [str(e) for e in effects]:
            errs.append(f"{card_name} · {item_name}：缺 `{want}`"
                        "（不洗回会让下次攻击重复计数，静默加强）")
    return errs


def audit_counter_subset(card_name: str, item_name: str, desc: str,
                         effects: list) -> list:
    """限定后的**指示物计数源**（2026-10-29 第二十六批 · 第二十四类对账）

    卡面原型：
      · 「for each damage counter on all of your **Benched {F} Pokémon**」（Gigalith）
      · 「… all of your **Benched Cynthia's Pokémon**」（Cynthia's Spiritomb）
      · 「for each of your Pokémon that has **“Tauros” in its name** that has any
         damage counters on it」（Paldean Tauros）

    ⚠ 三句都带**限定词**，按全体后备 `own_bench_counters` 计数会**多算伤害**
    （把别的属性/别的阵营的指示物也算进来）—— 属静默错算，必须在准入前拦下。
    """
    errs = []
    d = PAREN.sub(" ", desc or "")
    tags = [str(t) for t in effects]

    for m in _BENCH_CNT_TYPE_RE.finditer(d):
        color = _TYPE_COLOR.get(m.group(1).upper())
        if not color:
            errs.append(f"{card_name} · {item_name}：`{m.group(0)[:50]}` "
                        f"属性符号未识别")
            continue
        want = f"own_bench_counters_type:{color}"
        if not any(want in t for t in tags):
            errs.append(f"{card_name} · {item_name}：`{m.group(0)[:50]}` "
                        f"缺 `{want}`（按全体后备计数会多算别的属性）")

    for m in _BENCH_CNT_NAMED_RE.finditer(d):
        key = re.sub(r"[^a-z0-9]", "", _norm(m.group(1)).lower())
        want = f"own_bench_counters_named:{key}"
        if key and not any(want in t for t in tags):
            errs.append(f"{card_name} · {item_name}：`{m.group(0)[:50]}` "
                        f"缺 `{want}`（按全体后备计数会多算别的阵营）")

    for m in _FIELD_NAMED_DMG_RE.finditer(d):
        key = re.sub(r"[^a-z0-9]", "", _norm(m.group(1)).lower())
        want = f"own_field_named_with_damage:{key}"
        if key and not any(want in t for t in tags):
            errs.append(f"{card_name} · {item_name}：`{m.group(0)[:50]}` "
                        f"缺 `{want}`（具名+带指示物才对，笼统数会多算）")
    return errs


def audit_pre_phase(card_name: str, item_name: str, desc: str,
                    effects: list) -> list:
    """伤害前阶段（2026-10-29 第二十五批 · 第二十三类对账）

    卡面原型：「**Before doing damage**, discard all Pokémon Tools [and Special
    Energy] from your opponent's Active Pokémon.」——弃置必须发生在**伤害之前**：
    守方道具（减伤/免疫）此刻仍生效，落到伤害之后 = 先吃一次道具再弃（静默少做）。

    第二段：「If you discarded a Pokémon Tool **in this way**, … is now
    Paralyzed.」——状态以「真的弃到道具」为前提，缺 `:if_pre_tool` 就是无条件
    施加状态（**静默加强**，数值方向朝对手不利，最危险的一类）。
    """
    errs = []
    d = PAREN.sub(" ", desc or "")
    for m in _PRE_TOOL_RE.finditer(d):
        low = m.group(0).lower()
        if not any(str(e).startswith("pre:discard_tool:") for e in effects):
            errs.append(f"{card_name} · {item_name}：`{m.group(0)[:60]}` "
                        "缺 `pre:discard_tool:`（伤害后才弃 = 本次仍吃道具效果）")
        if any(str(e).startswith("discard_tool:") for e in effects):
            errs.append(f"{card_name} · {item_name}：同一条效果落成了伤害后 "
                        "`discard_tool:`（必须走 `pre:` 伤害前阶段）")
        if "special energy" in low and not any(
                str(e).startswith("pre:discard_energy:")
                and str(e).endswith(":special") for e in effects):
            errs.append(f"{card_name} · {item_name}：卡面同时弃**特殊能量**，"
                        "缺 `pre:discard_energy:…:opp:special`")
    if _IF_PRE_TOOL_STATUS_RE.search(d):
        if not any(str(e).endswith(":if_pre_tool") for e in effects):
            errs.append(f"{card_name} · {item_name}：卡面写有「If you discarded a "
                        "Pokémon Tool in this way」，但状态缺 `:if_pre_tool`"
                        "（会变成无条件施加 = 静默加强）")
    return errs


def audit_printing_identity(manual_cards: list | None = None,
                            dataset_cards: list | None = None) -> list:
    """印刷身份对账（2026-10-29 第二十一批 · 第十九类）

    印刷键 = ``(SET, number)``。同一印刷被**不同卡名**占用即为脏数据：
    它会让 `(set, number)` 精确匹配把 A 卡的效果/名字算到 B 卡头上（静默错映射），
    也会让「同名不同印刷」的共存逻辑把两张无关的卡混成同一条身份。

    实测事故：人工池 Crispin 误写 ``SSP 164``，而数据集 SSP 164 是
    ``Brilliant Blender`` → Brilliant Blender 永远无法按编号匹配。
    """
    import json as _json

    def _load(path: Path, key: str) -> list:
        try:
            return _json.loads(path.read_text(encoding="utf-8")).get(key) or []
        except Exception:                                 # noqa: BLE001
            return []

    manual = (manual_cards if manual_cards is not None
              else _load(REPO / "packages" / "engine" / "engine" / "data"
                         / "cards_real.json", "cards"))
    dataset = (dataset_cards if dataset_cards is not None
               else _load(REPO / "packages" / "card-db" / "data" / "cleaned"
                          / "cards_challenge.json", "cards"))

    def _nk(name) -> str:
        return re.sub(r"[^a-z0-9]", "", str(name or "").lower())

    def _same_card(nk: str, names: set) -> bool:
        """同名判定：完全相同，或「同卡不同命名」的后缀包含

        后缀包含用于人工池与数据集的命名差异（实测：人工 ``Ogerpon ex`` ↔
        数据集 ``Teal Mask Ogerpon ex``）。要求较短者**至少 5 字符**且较长者
        **以它结尾** —— 「Meowth ex」不会被「Meowth」放过。
        """
        if nk in names:
            return True

        def _ok(short: str, long: str) -> bool:
            return (len(short) >= 5 and len(long) > len(short)
                    and long.endswith(short))

        return any(_ok(nk, n) or _ok(n, nk) for n in names)

    ds_by_sn: dict = {}
    for c in dataset:
        s = str(c.get("series") or "").strip().upper()
        n = str(c.get("number") or "").strip()
        if s and n:
            ds_by_sn.setdefault((s, n), set()).add(_nk(c.get("name")))

    errs: list = []
    for key, names in ds_by_sn.items():
        if len(names) > 1:
            errs.append(f"[印刷冲突] 数据集 {key[0]} {key[1]} 被多张卡共用："
                        f"{sorted(names)}")
    seen: dict = {}
    for c in manual:
        real = c.get("real") or {}
        s = str(real.get("set") or "").strip().upper()
        n = str(real.get("number") or "").strip()
        if not (s and n):
            continue
        key, key_nk = (s, n), _nk(c.get("name"))
        if key in seen and seen[key] != key_nk:
            errs.append(f"[印刷冲突] 人工池 {c.get('name')} 与同名记录共用 {s} {n}")
        seen[key] = key_nk
        names = ds_by_sn.get(key) or set()
        if names and not _same_card(key_nk, names):
            errs.append(f"[印刷冲突] 人工池 {c.get('name')} 的 {s} {n} "
                        f"在数据集里是 {sorted(names)}（编号撞号 → 精确匹配会错映射）")
    return errs


def main() -> int:
    if not POOL.exists():
        print(f"[error] 找不到卡池：{POOL}", file=sys.stderr)
        return 2
    pool = json.loads(POOL.read_text(encoding="utf-8"))
    errs: list = audit_printing_identity()
    n_items = 0
    for c in pool.get("cards") or []:
        for kind in ("attacks", "abilities"):
            for it in c.get(kind) or []:
                n_items += 1
                desc = it.get("desc") or ""
                eff = list(it.get("effects") or [])
                if it.get("tag"):
                    eff.append(it["tag"])
                eff = _expand_evolve_act(eff)
                errs += audit_extra_damage(c["name"], it.get("name"), desc, eff)
                errs += audit_search_kind(c["name"], it.get("name"), desc, eff)
                errs += audit_energy_target(c["name"], it.get("name"), desc, eff)
                errs += audit_counter_side(c["name"], it.get("name"), desc, eff)
                errs += audit_look_top(c["name"], it.get("name"), desc, eff)
                errs += audit_ability_search(c["name"], it.get("name"), desc, eff)
                errs += audit_mill(c["name"], it.get("name"), desc, eff)
                errs += audit_attack_lock(c["name"], it.get("name"), desc, eff)
                errs += audit_recon_top(c["name"], it.get("name"), desc, eff)
                errs += audit_opp_hand(c["name"], it.get("name"), desc, eff)
                errs += audit_attach_spread(c["name"], it.get("name"), desc, eff)
                errs += audit_energy_cost(c["name"], it.get("name"), desc, eff)
                errs += audit_static_damage(c["name"], it.get("name"), desc, eff)
                errs += audit_next_turn_dmg(c["name"], it.get("name"), desc, eff)
                errs += audit_coin_forms(c["name"], it.get("name"), desc, eff)
                errs += audit_dmg_layer(c["name"], it.get("name"), desc, eff)
                errs += audit_coin_status(c["name"], it.get("name"), desc, eff)
                errs += audit_energy_to_hand(c["name"], it.get("name"), desc, eff)
                errs += audit_special_energy_discard(c["name"], it.get("name"),
                                                     desc, eff)
                errs += audit_pre_phase(c["name"], it.get("name"), desc, eff)
                errs += audit_counter_subset(c["name"], it.get("name"), desc, eff)
                errs += audit_energy_to_deck(c["name"], it.get("name"), desc, eff)
                errs += audit_on_damaged(c["name"], it.get("name"), desc, eff)
                errs += audit_set_per(c["name"], it.get("name"), desc, eff)
                errs += audit_static_immune(c["name"], it.get("name"), desc, eff)
                errs += audit_knockout(c["name"], it.get("name"), desc, eff)
        # 化石卡：训练家条目带 `fossil` 上场形态（其特性挂在 fossil.abilities 下）
        fossil = c.get("fossil") or {}
        for it in fossil.get("abilities") or []:
            n_items += 1
            desc = it.get("desc") or ""
            eff = list(it.get("effects") or [])
            if it.get("tag"):
                eff.append(it["tag"])
            errs += audit_static_damage(c["name"], it.get("name"), desc, eff)
            errs += audit_static_immune(c["name"], it.get("name"), desc, eff)
            errs += audit_extra_damage(c["name"], it.get("name"), desc, eff)
        tag = str(c.get("tag") or "")
        if tag.startswith("fossil:"):
            n_items += 1
            errs += audit_fossil(c["name"], c.get("desc") or "", tag, fossil)
        if tag.startswith("senergy:"):
            n_items += 1
            errs += audit_senergy(c["name"], c.get("desc") or "", tag)
        if tag.startswith("stadium:"):
            n_items += 1
            errs += audit_stadium_form(c["name"], c.get("desc") or "", tag)

    print(f"对账范围：扩展池 {len(pool.get('cards') or [])} 张 / {n_items} 个招式·特性")
    if errs:
        print(f"::error::卡面 ↔ tag 对账发现 {len(errs)} 处不一致")
        for e in errs:
            print(f"  - {e}")
        return 1
    print("OK: 附加伤害/指示物目标 · 检索类别 · 弃能对象 · 计数源阵营 · "
          "检视牌库顶 · 特性检索 · 磨牌 · 静态伤害增减 · 缩放基础伤害 · 化石上场形态 · "
          "特殊能量种类 · 静态免伤来源 · 招式击倒范围 · 场地静态规则 · 跨回合伤害修正 · "
          "抛币形态 · 条件/全队伤害修正 · 指示物任意目标 · 印刷身份 · 抛币状态 · "
          "能量回手 · 特殊能量弃置 · 伤害前阶段 · 限定指示物计数 · 能量洗回牌库 · "
          "受击反制 · 对手指定招式封锁 · 检视牌库顶去向 · 对手手牌操作 · "
          "检索能量自由分配 · 能量代价去向 三十一类对账全部一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
