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
# 同一枚币的**双分支**（第四十四批）：必须落在单条 `coin_flip:`（一次抛币）
_COIN_FORK_COUNTERS_RE = re.compile(
    r"flip a coin\.\s*if heads,\s*put (\d+) damage counters?\s*on 1 of your "
    r"opponent[\u2019']?s pok[eé]mon\.\s*if tails,\s*put (\d+) damage counters?\s*on "
    r"your active pok[eé]mon", re.I)
# 逐目标各抛一枚（第四十五批，Mega Zygarde ex）
_COIN_PER_TARGET_RE = re.compile(
    r"for each of your opponent[\u2019']?s pok[eé]mon,\s*flip a coin\.\s*"
    r"if heads,\s*this attack does (\d+) damage to that pok[eé]mon", re.I)
# 抛币选状态（第四十五批，Cradily）——三选一必须是一条 status_choose 子效果
_COIN_STATUS_CHOOSE_RE = re.compile(
    r"flip a coin\.\s*if heads,\s*choose burned,\s*confused,\s*or poisoned\.\s*"
    r"your opponent[\u2019']?s active pok[eé]mon is now affected by that "
    r"special condition", re.I)
# 「choose a Special Condition」全集（第四十七批，Grafaiai）
_COIN_STATUS_CHOOSE_ANY_RE = re.compile(
    r"flip a coin\.\s*if heads,\s*choose a special condition\.\s*"
    r"your opponent[\u2019']?s active pok[eé]mon is now affected by that "
    r"special condition", re.I)
# 抛币免击倒（第七十八批，Mega Hawlucha ex）
_COIN_SURVIVE_RE = re.compile(
    r"if this pok[eé]mon would be knocked out by damage from an attack,\s*"
    r"flip a coin\.\s*if heads,\s*this pok[eé]mon is not knocked out,\s*and its "
    r"remaining hp becomes (\d+)", re.I)
# 抛币免伤被动（第四十五批，Fezandipiti）——必须在伤害结算内抛币
_COIN_PREVENT_PASSIVE_RE = re.compile(
    r"has any \{([a-z])\} energy attached and is damaged by an attack,\s*"
    r"flip a coin\.\s*if heads,\s*prevent that damage", re.I)
# 双方洗回手牌 + 抛币分抽（第四十五批，Harlequin）
_SHUFFLE_BOTH_COIN_DRAW_RE = re.compile(
    r"each player shuffles their hand into their deck\.\s*then,\s*flip a coin\.\s*"
    r"if heads,\s*you draw (\d+) cards?,\s*and your opponent draws (\d+) cards?\.\s*"
    r"if tails,\s*you draw (\d+) cards?,\s*and your opponent draws (\d+) cards?",
    re.I)
# 条件 / 全队伤害修正（2026-10-29 第十九批）
_TEAM_REDUCE_RE = re.compile(
    r"all of your [^.]{0,90}? take (\d+) less damage from attacks from your "
    r"opponent", re.I)
# 单体限时护盾 + 奖赏数打出前置条件（第四十六批，Acerola's Mischief）
_SHIELD_CHOOSE_RE = re.compile(
    r"you can use this card only if your opponent has (\d+) or fewer prize "
    r"cards remaining\.\s*choose 1 of your pok[eé]mon in play\.\s*"
    r"during your opponent[\u2019']?s next turn, prevent all damage from and "
    r"effects of attacks done to that pok[eé]mon by your opponent[\u2019']?s "
    r"pok[eé]mon \{ex\}", re.I)
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
# 打出代价（2026-11-02 · 第三十二类）：同样是**独立实现**。
# 只认三种精确形态（数字 other cards / another card / a card）——
# 「discard a Basic {R} Energy card from your hand」（Blowtorch）**故意不匹配**：
# 它是「限定基本火能量」，落成 `discard:1` 就是放宽成任意牌（静默放宽代价）。
_PLAY_COST_SHAPE = re.compile(
    r"you can use this card only if you discard\s+"
    r"(?:(?P<n>\d+)\s+other\s+cards?|another\s+card|an?\s+card)\s+from your hand",
    re.I)
# 限定属性的**基本能量**代价（2026-11-02 第三十八批 · Blowtorch）：
# 「You can use this card only if you discard a Basic {R} Energy card from your
#  hand.」→ `play_cost="discard_energy:1:fire"`。
# ⚠ 独立正则：与 `_PLAY_COST_SHAPE`（任意 N 张牌）是**两种代价**，共用一条会
# 让「限定放宽成任意牌」这种错法一起通过。
_PLAY_COST_ENERGY_SHAPE = re.compile(
    r"you can use this card only if you discard\s+"
    r"(?:(?P<n>\d+)\s+other\s+|another\s+|an?\s+)?basic\s+\{(?P<c>[a-z])\}\s+"
    r"energy cards?\s+from your hand",
    re.I)
_PLAY_COST_VALUE = re.compile(
    r"(?:discard:[1-9]\d*|discard_energy:[1-9]\d*:[a-z]+)")
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
    # 还原弯撇号（’→'）与**不间断空格**（\xa0 → 普通空格）：真实卡池里
    # 「in play\xa0(yours …」这类 NBSP 会让整句正则静默失配（Inteleon「Bring
    # Down」实测），表现为「明明写了规则却对不上账」。
    return ((s or "").replace("\u2019", "'").replace("\u00a0", " ")
            .lower())


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
    # 「…Pokémon {ex}」→ 引擎 `:target:ex` 目标筛选（2026-11-03 第四十一批）。
    # ⚠ 只认「**仅** {ex}」：与 {V} 混写（「{ex} or Pokémon {V}」/「{ex} and
    #   Pokémon {V}」）在引擎无法表达 → 继续走下面的 unmodeled 判定。
    # 「{ex}」以及「{ex} or/and {V}」都按 `:target:ex` 处理（第九十八批）：
    # 本卡池无 Pokémon V，并集与 {ex} 等价；先剥 `{V}` 整段再去 `{ex}`，
    # 否则残留的 `and pokémon {v}` 会掉进下面的「不可表达子集」判定。
    if re.search(r"pok[eé]mon \{ex\}", t):
        t = re.sub(r"\s*(?:or|and)?\s*pok[eé]mon\s*\{v\}", "", t, flags=re.I)
        t = re.sub(r"\s*(?:or|and)\s*$", "", t)
        t = re.sub(r"\s*\{ex\}", "", t)
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
        # 第一百批起还有**上场触发**包装层 `on_bench_act:`（同构于 evolve_act）
        _wrap = next((w for w in ("evolve_act:", "on_bench_act:")
                      if s.startswith(w)), None)
        if _wrap:
            out.append(s)
            out.extend(x for x in s[len(_wrap):].split("|") if x)
        else:
            out.append(s)
    return out


def _expand_coin_subs(effects: list) -> list:
    """展开抛币 tag 的**子效果**，供「指示物 / 能量回手」类对账逐条核对

    外层 tag 同时保留（其它检查仍要看原始形态）。第四十四批起
    ``coin_flip:heads:<a>|tails:<b>`` 是**同一枚币的双分支**。
    """
    out: list = []
    for t in effects or []:
        s = str(t)
        out.append(s)
        if s.startswith("coin_flip:"):
            for seg in s[len("coin_flip:"):].split("|"):
                if seg.startswith(("heads:", "tails:")):
                    out.append(seg.split(":", 1)[1])
        elif s.startswith("coin:"):
            out.append(":".join(s.split(":")[2:]))
        elif s.startswith(("coin_each:", "coin_all:")):
            out.append(":".join(s.split(":")[3:]))
    return out


def audit_extra_damage(card_name: str, item_name: str, desc: str,
                       effects: list) -> list:
    """附加伤害 / 放置指示物子句 → 必须有对应 tag（self:N / snipe:…），否则报错"""
    errs = []
    d = PAREN.sub(" ", desc or "")
    # 抛币分支里的指示物由子效果承载 → 对账时展开（第四十四批）
    eff = _expand_coin_subs(effects)
    # 逐目标抛币（第四十五批）：`that Pokémon` 子句由 `coin_per_target:` 承载
    _per_tgt = any(str(e).startswith("coin_per_target:") for e in effects)
    ko_ctx = bool(re.search(r"is knocked out by damage", _norm(desc)))
    for m in DMG_TO.finditer(d):
        if m.group(2):                      # 「more damage」属修正器范畴
            continue
        if _per_tgt and _norm(m.group(3)).strip() == "that pokémon":
            continue
        # 可选加伤 + 反伤（第四十七批，Gurdurr / Dondozo ex）：
        # 「You may do N more damage. If you do, this Pokémon also does M damage
        #  to itself.」→ 必须**同一条** `dmg_plus:N:recoil:M`（联动不被拆散）。
        if _norm(m.group(3)).strip() == "itself":
            mm = re.search(r"you may do (\d+) more damage", d, re.I)
            if mm:
                want = f"dmg_plus:{int(mm.group(1))}:recoil:{int(m.group(1))}"
                if want not in [str(e) for e in effects]:
                    errs.append(f"{card_name} · {item_name}：可选反伤 "
                                f"`{m.group(0)[:60]}` 必须落 `{want}`"
                                f"（现为 {[str(e) for e in effects]}）")
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
            hits = [str(e) for e in eff
                    if str(e).startswith(f"counters:{int(m.group(1))}:")
                    and ":scope:ability:all" in str(e)]
            if not hits:
                errs.append(f"{card_name} · {item_name}：`{m.group(0)[:60]}` "
                            "缺 `counters:N:scope:ability:all`"
                            "（双方「有特性者」逐个，现为 "
                            f"{[e for e in eff if str(e).startswith('counters')]}）")
            continue
        # 「If you attached Energy to a Pokémon in this way, place N damage
        # counters on that Pokémon」→ 由 `attach:…:counters:N` 承载（Toxtricity）
        if "attached energy to a pok" in _norm(desc or "").lower():
            if not any(":counters:" in str(e) for e in eff):
                errs.append(f"{card_name} · {item_name}：`{m.group(0)[:60]}` "
                            "缺 `:counters:`（附能后的指示物未建模）")
            continue
        # 指示物**直放**对手战斗位（不吃弱/抗）→ `counters:`（Haunter / Alakazam）
        if "bench" not in _cl and "active pok" in _cl and "each" not in _cl:
            if not any(str(e).startswith("counters:") for e in eff):
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
            hits = [str(e) for e in eff
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


def _expand_compound(effects: list) -> list:
    """把复合 tag（`a|b`）展开成单条，供各类对账按单 tag 断言

    第九十五批起特性 tag 可以是**一条**复合串（`counters:5:scope:self|
    self_dmg_bonus:120:self`）。对账函数都是按单 tag 前缀判断的，不展开就会
    报「缺 tag」的假错误。`coin:` / `coin_flip:` / `evolve_act:` 里的 `|`
    是**分支分隔符**，不能拆。
    """
    out: list = []
    for e in effects or []:
        t = str(e)
        # **触发器包装层**（第一百批/一百零二批）：`evolve_act:a|b` /
        # `on_bench_act:a|b` 里的 `|` 是子效果分隔符，包装前缀只是「什么时候
        # 执行」。不剥掉包装，所有按前缀判断的规则都会报「缺 tag」的假错误
        # （实测：Farfetch'd 的 attach / Durant ex 的 mill / TR Koffing 的
        #  search_bench 三张卡同时被误报）。
        for _wrap in ("evolve_act:", "on_bench_act:"):
            if t.startswith(_wrap):
                out.extend(x for x in t[len(_wrap):].split("|") if x)
                break
        else:
            if "|" in t and not t.startswith("coin"):
                out.extend(x for x in t.split("|") if x)
            else:
                out.append(t)
    return out


def _check_clause(card_name: str, item_name: str, clause: str, per,
                  effects: list, raw: str, ko_ok: bool = False) -> list:
    effects = _expand_compound(effects)
    # 「choose N of your opponent's Pokémon and put M damage counters on **each of
    #  them**」（第九十七批，Team Rocket's Crobat ex）
    # ⚠ 目标与只数都在**前一句**（choose N of …），本函数只看到尾句 `each of them`，
    #   按 `_want_snipe` 会判成"不可精确表达"。引擎 `counters:M:scope:opp:N`
    #   恰好就是「选 N 只、每只各 M 个」，因此按 **tag 形态**校验：
    #   缺 scope/只数（如退化成 `counters:M` 打战斗位）才算不可表达。
    if "each of them" in _norm(clause or raw or ""):
        ok = re.compile(r"^counters:\d+:scope:opp:\d+$")
        if any(ok.match(str(e)) for e in effects):
            return []
        return [f"{card_name} · {item_name}：目标 `each of them` 需要 "
                f"`counters:M:scope:opp:N`（选 N 只各 M 个），实际 {list(effects)}"]
    want = _want_snipe(clause, per)
    scale = bool(re.search(r"\bfor each\b", _norm(clause)))
    if want == "none":
        return []
    if want == "unmodeled":
        # 第一百三十五批（S1c）：决策分解把「任意分配（含**战斗位**）」变成
        # 可精确表达 → `choose:counters_any:N`。只在**整句**「in any way you like」
        # 且确有该 tag 时放行；带子集条件（that has / for each …）的写法仍然未建模。
        if re.search(r"in any way you like", _norm(raw or clause or "")) and \
                any(str(e).startswith("choose:counters_any:") for e in effects):
            return []
        # S2（2026-11-08）：`that Pokémon` 在**换位订阅**里是精确定义的 ——
        # 指「刚被换下去的那一只」。`on_event:switch_out:counters:N:on:leaving`
        # 就是它，不能因为字面是 "that Pokémon" 就判成不可表达。
        if any(str(e).startswith("on_event:switch_out:counters:")
               and ":on:leaving" in str(e) for e in effects):
            return []
        # S2b：「that Pokémon」在**进化订阅**里同样是精确定义的 —— 指「刚进化出来
        # 的那一只」（Team Rocket's Ampharos「Darkest Impulse」）。
        if any(str(e).startswith("on_event:evolve:counters:")
               and ":on:entering" in str(e) for e in effects):
            return []
        # S2d：限时订阅里的「that Pokémon」= **出招时的防御宝可梦**（建档时快照），
        # `on:target` + `match:event_target` 把它钉死成精确目标
        # （Pachirisu「place 8 damage counters on that Pokémon」）。
        if any(str(e).startswith("arm:") and ":on:target" in str(e)
               and ":match:event_target" in str(e) for e in effects):
            return []
        return [f"{card_name} · {item_name}：不可精确表达的目标 "
                f"`{raw[:60]}` **却在池内**（应整卡不收）"]
    if want == "self":
        # 「put N damage counters on this Pokémon」也可能落精确的
        # `counters:N:scope:self`（第九十五批，Feraligatr）—— 指示物不吃弱点/抗性，
        # 比 `self:N`（伤害通道）更精确，视为满足。
        # 一个指示物 = 10 点伤害，所以 `counters:<per/10>:scope:self`
        _ctxt = _norm(f"{clause} {raw or ''}")
        if isinstance(per, int) and per > 0 \
                and re.search(r"damage counters?", _ctxt) and any(
                    str(e).startswith(f"counters:{max(1, per // 10)}:scope:self")
                    for e in effects):
            return []
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
    # 「place N damage counters on …」可能落 `counters:…:scope:<same>`（第九十五批起）：
    # 指示物**不吃弱点/抗性**，比 `snipe:`（走伤害通道）更精确 → 视为满足该目标口径。
    # 只对「damage counters」子句生效；伤害子句仍必须走 snipe。
    _ctr_txt = _norm(f"{clause} {raw or ''}")
    if want.startswith("snipe:") and re.search(r"damage counters?", _ctr_txt):
        scope_want = want.split(":", 1)[1]
        if any(str(t).startswith("counters:")
               and f":scope:{scope_want}" in str(t) for t in effects):
            return []
    hits = [str(t) for t in effects if str(t).startswith(want + ":")]
    if not hits:
        return [f"{card_name} · {item_name}：`{raw[:60]}` 缺 `{want}:…` tag"]
    if scale and not any(":per:" in h for h in hits):
        return [f"{card_name} · {item_name}：`{raw[:60]}` 是**按计数源缩放**的，"
                f"缺 `:per:<counter>`（现为 {hits}）"]
    low_clause = _norm(clause)
    # 「Pokémon {ex}」→ 必须有 `:target:ex`（第四十一批）：缺了就会打**非 ex**
    # 的目标（卡面本不允许），属静默超范围。
    if re.search(r"pok[eé]mon \{ex\}", low_clause) \
            and not any(":target:ex" in h for h in hits):
        return [f"{card_name} · {item_name}：`{raw[:60]}` 缺 `:target:ex`"
                f"（只允许选对手的 ex 宝可梦，现为 {hits}）"]
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
        if re.search(r"during your opponent'?s next turn", d, re.I):
            # 第四十六批（Iron Defender）：**时限制**全队减伤不是 `fx:team_reduce`
            # （那是常驻特性）→ 必须落 `team_shield:reduce:N:type:<color>`。
            ok = any(str(e).startswith(f"team_shield:reduce:{n}:type:")
                     for e in effects)
            if not ok:
                errs.append(f"{card_name} · {item_name}：全队限时减伤 "
                            f"`{m.group(0)[:60]}` 缺 `team_shield:reduce:{n}:type:…`")
            continue
        if not any(str(e).startswith(f"fx:team_reduce:{n}:") for e in effects):
            errs.append(f"{card_name} · {item_name}：全队减伤 `{m.group(0)[:60]}` "
                        f"缺 `fx:team_reduce:{n}:…` tag")
        elif "doesn't stack" in d.lower() and not any(
                str(e).startswith(f"fx:team_reduce:{n}:") and ":nonstack" in str(e)
                for e in effects):
            # 「The effect of … doesn't stack.」漏标 → 多只同族在场时会重复减伤
            errs.append(f"{card_name} · {item_name}：卡面写明「doesn't stack」，"
                        f"`fx:team_reduce:{n}` 缺 `:nonstack`")
    for m in _SHIELD_CHOOSE_RE.finditer(d):
        # 单体限时护盾：来源限定（{ex}）/ 连效果一起挡 / 奖赏数 gate 三者缺一
        # 都会让这张卡的效果或可用性静默改变 → 逐项核对整条 tag。
        n = int(m.group(1))
        want = (f"shield_choose:prevent_all:from:ex:effects:"
                f"gate:opp_prizes_le:{n}")
        if want not in [str(e) for e in effects]:
            errs.append(f"{card_name} · {item_name}：`{m.group(0)[:60]}` 必须落 "
                        f"`{want}`（现为 {[str(e) for e in effects]}）")
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
    for m in _COIN_FORK_COUNTERS_RE.finditer(d):
        # 「Flip a coin. If heads, … If tails, …」——同一枚币只能有一个分支生效。
        # 拆成 `coin:heads:` + `coin:tails:` 会抛**两次**（可能两个都生效/都不
        # 生效），因此必须是一条 `coin_flip:heads:<a>|tails:<b>`。
        want = (f"coin_flip:heads:counters:{int(m.group(1))}:scope:opp:1"
                f"|tails:counters:{int(m.group(2))}:scope:self")
        if want not in [str(e) for e in effects]:
            errs.append(f"{card_name} · {item_name}：`{m.group(0)[:60]}` 的双分支"
                        "必须落在**同一条** `coin_flip:`（一次抛币；现为 "
                        f"{[str(e) for e in effects]}）")
    for m in _COIN_PER_TARGET_RE.finditer(d):
        # 逐目标各抛一枚：落成 `snipe:opp:N:all` = 每只必中；落成
        # `coin:heads:snipe:…` = 只抛一次（全中/全不中）。
        want = f"coin_per_target:{int(m.group(1))}:scope:opp"
        if want not in [str(e) for e in effects]:
            errs.append(f"{card_name} · {item_name}：`{m.group(0)[:60]}` 必须落 "
                        f"`{want}`（逐目标独立抛币；现为 "
                        f"{[str(e) for e in effects]}）")
    for m in _COIN_STATUS_CHOOSE_RE.finditer(d):
        # 三选一必须是一条 `status_choose:` 子效果（拆成三条 = 三种状态全中）。
        want = "coin:heads:status_choose:burn+confuse+poison"
        if want not in [str(e) for e in effects]:
            errs.append(f"{card_name} · {item_name}：`{m.group(0)[:60]}` 必须落 "
                        f"`{want}`（三选一不能拆成多条状态 tag）")
    for m in _COIN_STATUS_CHOOSE_ANY_RE.finditer(d):
        # 「choose a Special Condition」全集（Grafaiai，第四十七批）：
        # 五种状态必须一条 tag；顺序固定为 `rules.STATUSES`（默认首个 poison）。
        want = ("coin:heads:status_choose:"
                "poison+burn+sleep+paralyze+confuse")
        if want not in [str(e) for e in effects]:
            errs.append(f"{card_name} · {item_name}：`{m.group(0)[:60]}` 必须落 "
                        f"`{want}`（全集五选一；现为 "
                        f"{[str(e) for e in effects]}）")
    for m in _COIN_PREVENT_PASSIVE_RE.finditer(d):
        # 抛币免伤必须在伤害结算内触发；写成 `on_damaged:` 时伤害已加上。
        col = _TYPE_COLOR.get(m.group(1).upper())
        want = f"passive:coin_prevent:{col}" if col else None
        if not want or want not in [str(e) for e in effects]:
            errs.append(f"{card_name} · {item_name}：`{m.group(0)[:60]}` 必须落 "
                        f"`{want or 'passive:coin_prevent:<color>'}`"
                        "（伤害结算内抛币；事后无法撤销）")
    for m in _COIN_SURVIVE_RE.finditer(d):
        # 抛币免击倒：必须是特性被动（不是道具 `tool:survive`、不要求满 HP）。
        want = f"passive:coin_survive:{int(m.group(1))}"
        if want not in [str(e) for e in effects]:
            errs.append(f"{card_name} · {item_name}：`{m.group(0)[:60]}` 必须落 "
                        f"`{want}`（现为 {[str(e) for e in effects]}）")
    for m in _SHUFFLE_BOTH_COIN_DRAW_RE.finditer(d):
        # 训练家只有一个卡级 tag：洗牌 + 单次抛币 + 双方不同张数必须合并。
        want = (f"shuffle_both_coin_draw:{int(m.group(1))}:{int(m.group(2))}:"
                f"{int(m.group(3))}:{int(m.group(4))}")
        if want not in [str(e) for e in effects]:
            errs.append(f"{card_name} · {item_name}：`{m.group(0)[:60]}` 必须落 "
                        f"`{want}`（四条信息不能拆成多条 tag）")
    return errs


def audit_look_top(card_name: str, item_name: str, desc: str,
                   effects: list) -> list:
    """**旧口径** `recon:N`（顶 N 张取 1 入手）的方向守卫

    引擎 `recon:N` 只表示「取 1 张**入手**」。若卡面去向是「弃置 / 附着 / 放回
    牌库」却映射成 `recon:N`，引擎会**白送一张手牌**（凭空手牌优势）。实测抓出
    5 张（Morpeko / Metang / Deduction Kit / Grimsley's Move / Waitress）。

    ⚠ 2026-11-02 收窄：扩展口径 ``recon:<N>:<K>:<sel>[:dest:][:rest:]`` 已能
    表达 bench / attach / attach_spread / discard 四种去向，它们的正确性由
    `audit_recon_top`（第二十八类）逐项核对。本函数**只看 2 段形态**
    （``recon:<N>``），否则会把所有合法的扩展口径卡误判成「白送手牌」。
    """
    errs = []
    m = LOOK_TOP.search(desc or "")
    if not m:
        return errs
    recon = [str(t) for t in effects if str(t).startswith("recon:")]
    # 只保留旧口径（`recon:<N>`，恰好 2 段）
    legacy = [t for t in recon if len(t.split(":")) == 2]
    if not legacy:
        return errs
    tail = _norm((desc or "")[m.end():])
    if not re.search(r"(?:into|to)\s+your\s+hand", tail):
        errs.append(f"{card_name} · {item_name}：`{m.group(0)}` 去向非「入手」"
                    f"（{tail.strip()[:40]}），不该映射成 {legacy}")
        return errs
    want = f"recon:{int(m.group(1))}"
    if want not in legacy:
        errs.append(f"{card_name} · {item_name}：`{m.group(0)}` 应为 `{want}`，"
                    f"实际 {legacy}")
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
    """磨牌子句 → 必须有 `mill:N[:opp]`

    ⚠ 2026-11-02 收窄：``flip a coin until you get tails`` 的磨牌张数是**正面数**
    （随机变量），正确形态是 `coin_until_tails:mill:opp`，由本批新类逐项核对；
    若在此仍然要求常数 `mill:1:opp`，就把「每正面磨 1 张」压成了「恒磨 1 张」。
    """
    errs = []
    if re.search(r"flip a coin until you get tails", desc or "", re.I):
        return errs
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
# 「look at the top N cards of your deck」——也认「the top **card**」（单数无数字，
# Morpeko / Rockruff / Litwick 的「看顶 1 张、可弃」形态）。
_RECON_TOP = re.compile(
    r"look at the top\s+(?:(\d+)\s+cards?|card)\s+of your deck", re.I)


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
    n = int(m.group(1)) if m.group(1) else 1      # 「the top card」= 1 张
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
        # ⚠ 判定顺序：`attach_spread`（多只、自由分配）必须在 `attach`（单只）之前，
        # 否则「attach any number … to your Pokémon in any way you like」会被当成单目标。
        if re.search(r"onto your bench", d, re.I):
            want_dest = "bench"
        elif re.search(r"attach any number of .{0,60}?in any way you like", d, re.I):
            want_dest = "attach_spread"
        elif re.search(r"attach .{0,40}?to 1 of your pok[eé]mon", d, re.I):
            want_dest = "attach"
        elif re.search(r"you may discard that card", d, re.I):
            want_dest = "discard"
        got_dest = "hand"
        for i, x in enumerate(parts):
            if x == "dest" and i + 1 < len(parts):
                got_dest = parts[i + 1]
        if got_dest != want_dest:
            errs.append(f"{card_name} · {item_name}：卡面去向是 `{want_dest}`，"
                        f"`{t}` 却是 `{got_dest}`")
        want_rest = "shuffle"
        # 同理：`shuffle_bottom`（只洗余牌再放底）必须在 `bottom` 之前判。
        if re.search(r"shuffle the other cards and put them on the bottom", d, re.I):
            want_rest = "shuffle_bottom"
        elif re.search(r"put them on the bottom of your deck", d, re.I):
            want_rest = "bottom"
        elif re.search(r"discard the other cards", d, re.I):
            want_rest = "discard"
        elif not re.search(r"shuffle", d, re.I):
            # 卡面**完全没有**洗牌句（Morpeko / Rockruff / Litwick 的「看顶 1 张、
            # 可弃」）→ 不得洗牌。写成 `shuffle` 会顺手重洗整副牌库。
            want_rest = "none"
        got_rest = "shuffle"
        for i, x in enumerate(parts):
            if x == "rest" and i + 1 < len(parts):
                got_rest = parts[i + 1]
        # 「shuffle the other cards and put them on the bottom」用 `bottom`（不洗余牌、
        # 保持原序放底）是**已知的精度差异**，不是方向性错误：两者都把余牌放到
        # 牌库底部，区别只在余牌之间的相对顺序 → 不报错（避免把已验收的卡打回）。
        _rest_ok = got_rest == want_rest or (
            want_rest == "shuffle_bottom" and got_rest == "bottom")
        if not _rest_ok:
            errs.append(f"{card_name} · {item_name}：余牌去向应是 `{want_rest}`，"
                        f"`{t}` 却是 `{got_rest}`")
        # K：卡面「any number of … you find there」= 取全部匹配（tag 里是 `all`）；
        # 写成固定张数会**凭空限制**取牌数量（少拿 = 静默削弱）。
        if re.search(r"any number of", d, re.I):
            if len(parts) > 2 and parts[2] != "all":
                errs.append(f"{card_name} · {item_name}：卡面是「any number of」，"
                            f"`{t}` 却写成固定张数 `{parts[2]}`")
        elif len(parts) > 2 and parts[2] == "all":
            errs.append(f"{card_name} · {item_name}：卡面无「any number of」，"
                        f"`{t}` 却取全部（`all`）")
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
    # 抛币计数（flip）与「每只后备各一份」（each bench）由
    # `audit_attach_variants` 单独核对 —— 它们的张数口径与本族不同
    # （heads×per / 每只各 per），两套期望同时作用会互相打架。
    # 这里直接跳过；反向守卫也在那边。
    if re.search(r"\bflip \d+ coins?\b", d, re.I) \
            or re.search(r"for each of your benched pok[eé]mon", d, re.I):
        return errs
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
        elif re.search(r"to your tera pok[eé]mon", d, re.I):
            # Terapagos SSP 161「your **Tera** Pokémon」= 卡牌子类（category）
            want_target = "cat-tera"
        else:
            # 「your {L} Pokémon」= **属性**限定（Magneton SSP 59）。
            # 与 `cat-*` 是两套依据：属性看 `pdef.ptype`，category 看卡牌子类。
            m_ty = re.search(r"to your \{([a-z])\} pok[eé]mon", d, re.I)
            if m_ty:
                want_target = "type-" + _TYPE_COLOR.get(
                    m_ty.group(1).upper(), m_ty.group(1).lower())
    # 自爆（第三十七批）：「If you use this Ability, this Pokémon is Knocked Out.」
    # ⚠ 只认**这一句**；全库另有 19 处「even if this Pokémon is Knocked Out」
    # （受击反制），那是完全不同的语义，正则必须以 `if you use this ability` 起头。
    want_selfko = bool(re.search(
        r"if you use this ability,? this pok[eé]mon is knocked out", d, re.I))
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
        # 自爆核对：两个方向都危险 —— 漏标 = 免费使用（凭空加强）；多标 =
        # 把普通加速变成自杀技（静默削弱）
        got_selfko = str(t).endswith(":selfko")
        if got_selfko != want_selfko:
            errs.append(f"{card_name} · {item_name}：卡面"
                        f"{'有' if want_selfko else '没有'}「用掉自爆」句，"
                        f"`{t}` 却{'带' if got_selfko else '不带'} `:selfko`")
        # 「of different types」核对（第三十九批）：这是**检索约束**，双向都危险。
        # 漏标 → 允许拿 N 张同色（静默加强，Eevee SFA 50 实际如此）；多标 →
        # 把普通加速削成「不能同色」（静默削弱）。
        want_dist = bool(re.search(r"basic energy cards? of different types", d, re.I))
        got_dist = "distinct" in parts
        if got_dist != want_dist:
            errs.append(f"{card_name} · {item_name}：卡面"
                        f"{'有' if want_dist else '没有'}「of different types」，"
                        f"`{t}` 却{'带' if got_dist else '不带'} `:distinct`")
    return errs


def audit_attach_variants(card_name: str, item_name: str, desc: str,
                          effects: list) -> list:
    """自由分配附着的**两个变体**对账（2026-11-02 · 第三十三类）

    与 `audit_attach_spread` 的分工：那个查「n 张任意分配」，本类查两个
    **张数口径不同**的近亲（卡面都含「in any way you like」，但语义不同）。

    ① ``attach_spread_flip:<coins>:<per>:<etype>:<zone>[:target:<scope>]``
       「Flip N coins. For each heads, search your deck for up to K Basic Energy
        cards and attach them to your Pokémon in any way you like.」
       → 必须核对 **N（抛币枚数）与 K（每正面上限）两个常量**。
       把 N×K 折算成一个固定整数是最典型也最难发现的错：上限与方差全变，
       而「看起来能附 6 张」的表面行为还在。

    ② ``attach_each_bench:<per>:<etype>:<zone>``
       「For each of your Benched Pokémon, search your deck for a Basic {P}
        Energy card and attach it to **that** Pokémon.」
       → 每只后备各一份、**绑定给那一只**。落成 `attach_spread:` 会静默放宽
       分配（允许漏掉某只、或把多张堆在同一只上），卡面都不允许。

    反向守卫：出现这两类 tag 但卡面无对应句式 → 认领越界（多半是句首锚定失效）。
    """
    errs = []
    d = _norm(desc or "")
    tags = [str(t) for t in (effects or [])]
    flips = [t for t in tags if t.startswith("attach_spread_flip:")]
    eachb = [t for t in tags if t.startswith("attach_each_bench:")]

    def _scope_of(parts: list) -> str:
        if "target" in parts:
            i = parts.index("target")
            return parts[i + 1] if i + 1 < len(parts) else ""
        return "any"

    # ---------------- ① 抛币计数 ----------------
    m_f = re.search(r"\bflip (\d+) coins?\b", d, re.I)
    wants_flip = bool(m_f) and bool(re.search(r"in any way you like", d, re.I))
    if wants_flip:
        m_k = re.search(r"for each heads,?\s*search your deck for up to (\d+)",
                        d, re.I)
        if not m_k:
            errs.append(f"{card_name} · {item_name}：抛币形态但「每正面 up to N」"
                        f"未识别 —— 对账正则需同步，否则本类空转")
        if not flips:
            wrong = [t for t in tags
                     if t.startswith(("attach_spread:", "attach:", "attach_energy:",
                                      "search_"))]
            errs.append(f"{card_name} · {item_name}：卡面「Flip N coins. For each "
                        f"heads …」应产出 `attach_spread_flip:`，实际 "
                        f"{wrong or '无附能 tag'} —— 张数口径丢失")
        else:
            coins = int(m_f.group(1))
            per = int(m_k.group(1)) if m_k else -1
            m_c = re.search(r"basic\s*\{([a-z])\}\s*energy", d, re.I)
            want_et = (_TYPE_COLOR.get(m_c.group(1).upper(), m_c.group(1).lower())
                       if m_c else "any")
            for t in flips:
                parts = t.split(":")
                got_c = parts[1] if len(parts) > 1 else ""
                got_p = parts[2] if len(parts) > 2 else ""
                got_et = parts[3] if len(parts) > 3 else ""
                got_zone = parts[4] if len(parts) > 4 else ""
                if got_c != str(coins):
                    errs.append(f"{card_name} · {item_name}：卡面抛 {coins} 枚币，"
                                f"`{t}` 写的是 {got_c}（抛币枚数不符）")
                if per > 0 and got_p != str(per):
                    errs.append(f"{card_name} · {item_name}：卡面每正面 up to "
                                f"{per} 张，`{t}` 写的是 {got_p}"
                                f"（每正面上限不符）")
                if got_et != want_et:
                    errs.append(f"{card_name} · {item_name}：卡面能量限定是 "
                                f"`{want_et}`，`{t}` 却是 `{got_et}`")
                if got_zone != "deck":
                    errs.append(f"{card_name} · {item_name}：卡面从**牌库**检索，"
                                f"`{t}` 的来源是 `{got_zone}`")
    elif flips:
        errs.append(f"{card_name} · {item_name}：卡面没有「Flip N coins. For each "
                    f"heads … in any way you like」，却出现 {flips} —— 认领越界")

    # ---------------- ② 每只后备各一份 ----------------
    m_e = re.search(r"for each of your benched pok[eé]mon,?\s*search your deck "
                    r"for (?:a|an|up to (\d+)) basic", d, re.I)
    if m_e:
        want_per = int(m_e.group(1)) if m_e.group(1) else 1
        m_c = re.search(r"basic\s*\{([a-z])\}\s*energy", d, re.I)
        want_et = (_TYPE_COLOR.get(m_c.group(1).upper(), m_c.group(1).lower())
                   if m_c else "any")
        if not eachb:
            wrong = [t for t in tags
                     if t.startswith(("attach_spread:", "attach:", "attach_energy:",
                                      "search_"))]
            errs.append(f"{card_name} · {item_name}：卡面「For each of your "
                        f"Benched Pokémon … attach it to **that** Pokémon」应产出 "
                        f"`attach_each_bench:`，实际 {wrong or '无附能 tag'}"
                        f" —— 每只各一份的绑定关系丢失")
        else:
            for t in eachb:
                parts = t.split(":")
                got_per = parts[1] if len(parts) > 1 else ""
                got_et = parts[2] if len(parts) > 2 else ""
                got_zone = parts[3] if len(parts) > 3 else ""
                if got_per != str(want_per):
                    errs.append(f"{card_name} · {item_name}：卡面每只后备 "
                                f"{want_per} 张，`{t}` 写的是 {got_per}")
                if got_et != want_et:
                    errs.append(f"{card_name} · {item_name}：卡面能量限定是 "
                                f"`{want_et}`，`{t}` 却是 `{got_et}`")
                if got_zone != "deck":
                    errs.append(f"{card_name} · {item_name}：卡面从**牌库**检索，"
                                f"`{t}` 的来源是 `{got_zone}`")
    elif eachb:
        errs.append(f"{card_name} · {item_name}：卡面没有「For each of your "
                    f"Benched Pokémon … attach it to that Pokémon」，却出现 "
                    f"{eachb} —— 认领越界")

    # ---------------- ③ 双色「or 1 of each」 ----------------
    multi = [t for t in tags if t.startswith("attach_spread_multi:")]
    m_m = re.search(
        r"a basic \{([a-z])\} energy card,? a basic \{([a-z])\} energy card,? "
        r"or 1 of each", d, re.I)
    if m_m:
        want = [_TYPE_COLOR.get(m_m.group(1).upper(), m_m.group(1).lower()),
                _TYPE_COLOR.get(m_m.group(2).upper(), m_m.group(2).lower())]
        if not multi:
            errs.append(f"{card_name} · {item_name}：卡面是「{want[0]} / {want[1]} "
                        f"**or 1 of each**」三选一，却没有 `attach_spread_multi:`"
                        f" —— 拆成两条 `attach_spread:` 会强制每类各拿 1 张")
        else:
            for t in multi:
                part = t.split(":")
                if len(part) < 6:
                    errs.append(f"{card_name} · {item_name}：`{t}` 字段不足")
                    continue
                got = [part[2], part[4]]
                if got != want:
                    errs.append(f"{card_name} · {item_name}：卡面两类是 {want}，"
                                f"`{t}` 却是 {got}（属性次序/内容不符）")
                if part[1] != "1" or part[3] != "1":
                    errs.append(f"{card_name} · {item_name}：卡面每类「or 1 of "
                                f"each」= 至多 1 张，`{t}` 写的是 "
                                f"{part[1]}/{part[3]}")
    elif multi:
        errs.append(f"{card_name} · {item_name}：卡面没有「a Basic {{X}} Energy "
                    f"card, a Basic {{Y}} Energy card, or 1 of each」，却出现 "
                    f"{multi} —— 认领越界")

    # ---------------- ④ 「Choose up to N of your {X} Pokémon」逐只各附 ----------------
    pick = [t for t in tags if t.startswith("attach_each_pick:")]
    m_p = re.search(r"choose up to (\d+) of your \{([a-z])\} pok[eé]mon[.] "
                    r"for each of those pok[eé]mon, search your deck for a basic "
                    r"\{[a-z]\} energy card and attach it", d, re.I)
    if m_p:
        want_mx = m_p.group(1)
        want_et = _TYPE_COLOR.get(m_p.group(2).upper(), m_p.group(2).lower())
        if not pick:
            errs.append(f"{card_name} · {item_name}：卡面「Choose up to {want_mx} "
                        f"of your {{{m_p.group(2)}}} Pokémon … each」应产出 "
                        f"`attach_each_pick:`，实际无 —— 选取范围丢失")
        else:
            for t in pick:
                part = t.split(":")
                if len(part) < 7 or part[1] != want_mx:
                    errs.append(f"{card_name} · {item_name}：卡面「up to "
                                f"{want_mx} 只」，`{t}` 的选取上限不符")
                    continue
                if part[3] != want_et:
                    errs.append(f"{card_name} · {item_name}：卡面能量限定是 "
                                f"`{want_et}`，`{t}` 却是 `{part[3]}`")
                if part[6] != f"type-{want_et}":
                    errs.append(f"{card_name} · {item_name}：卡面目标限定是 "
                                f"`type-{want_et}`，`{t}` 却是 `{part[6]}`")
                # 条件性自我中毒：只有「If you attached Energy to your Active
                # Pokémon in this way」这一句才允许（缺 = 条件被吞成无条件）
                want_poison = bool(re.search(
                    r"if you attached energy to your active pok[eé]mon in this "
                    r"way,? it is now poisoned", d, re.I))
                got_poison = "poison_if_active" in part
                if got_poison != want_poison:
                    errs.append(f"{card_name} · {item_name}：卡面"
                                f"{'有' if want_poison else '没有'}条件自我中毒句，"
                                f"`{t}` 却{'带' if got_poison else '不带'}"
                                f" `:poison_if_active`")
    elif pick:
        errs.append(f"{card_name} · {item_name}：卡面没有「Choose up to N of "
                    f"your {{X}} Pokémon … each」，却出现 {pick} —— 认领越界")

    # ---------------- ⑤ 「Flip a coin until you get tails」按正面数附能 ----------------
    fu = [t for t in tags if t.startswith("attach_flip_until:")]
    m_fu = re.search(r"flip a coin until you get tails[.] search your deck for an "
                     r"amount of basic", d, re.I)
    if m_fu and not fu:
        errs.append(f"{card_name} · {item_name}：卡面「up to the number of heads and "
                    f"attach it to this Pokémon」应产出 `attach_flip_until:`，"
                    f"实际无 —— 张数口径丢失")
    elif fu and not m_fu:
        errs.append(f"{card_name} · {item_name}：卡面没有「Flip a coin until you "
                    f"get tails … attach it to this Pokémon」，却出现 {fu}"
                    f" —— 认领越界")

    # ---------------- ⑥ 训练家抛币门 + 检索附能（Energy Coin） ----------------
    gate = [t for t in tags if t.startswith("coin_gate_attach:")]
    m_g = re.search(r"flip (\d+) coins[.] if both of them are heads, search your "
                    r"deck for", d, re.I)
    if m_g:
        if not gate:
            errs.append(f"{card_name} · {item_name}：卡面「Flip {m_g.group(1)} "
                        f"coins. If both of them are heads, … attach」应产出 "
                        f"`coin_gate_attach:`，实际无 —— 抛币门被吞")
        else:
            for t in gate:
                part = t.split(":")
                if len(part) < 5 or part[1] != m_g.group(1):
                    errs.append(f"{card_name} · {item_name}：卡面抛 {m_g.group(1)} "
                                f"枚，`{t}` 的抛币数不符")
    elif gate:
        errs.append(f"{card_name} · {item_name}：卡面没有「Flip N coins. If both "
                    f"of them are heads … attach」，却出现 {gate} —— 认领越界")
    return errs


def audit_discard_tool_or_senergy(card_name: str, item_name: str, desc: str,
                                  effects: list) -> list:
    """「弃对手道具/特殊能量 **或** 弃竞技场」二选一对账（2026-11-02 · 第三十四类）

    卡面（Blowtorch PFL 86）：
        Discard a Pokémon Tool or Special Energy card from 1 of your opponent's
        Pokémon, **or** discard a Stadium in play.

    这条最危险的错法是「只做后半句」—— 而它**不报错**：`discard_stadium` 是
    既有能力，产出它看起来就像「已经实现了」。实际后果是这张卡在「对手没有
    场地」的一半局面上打出去**什么都不发生**（静默削弱）。

    所以本类必须做**双向**核对：
      · 卡面有这句 → 必须有 `discard_tool_or_senergy:` tag；只出现
        `discard_stadium` / `discard_tool:` 之一 → 报「只做了一支」；
      · 卡面没有这句 → 不得出现该 tag（认领越界）。
    """
    errs = []
    d = _norm(desc or "")
    tags = [str(t) for t in (effects or [])]
    got = [t for t in tags if t.startswith("discard_tool_or_senergy:")]
    # 独立正则：不 import 解析包的同名正则（共用会一起错）
    want = bool(re.search(
        r"discard an? pok[eé]mon tool or special energy card from 1 of your "
        r"opponent's pok[eé]mon,?\s*or discard a stadium in play", d, re.I))
    if want and not got:
        partial = [t for t in tags
                   if t.startswith(("discard_stadium", "discard_tool:"))]
        errs.append(f"{card_name} · {item_name}：卡面是「道具/特殊能量 **或** "
                    f"竞技场」二选一，却没有 `discard_tool_or_senergy:`；"
                    f"实际 {partial or '无弃置 tag'}"
                    f" —— 只做了其中一支（另一半局面下这张卡空转）")
    elif got and not want:
        errs.append(f"{card_name} · {item_name}：卡面没有「Pokémon Tool or "
                    f"Special Energy … or discard a Stadium」，却出现 {got}"
                    f" —— 认领越界")
    elif got:
        m_n = re.search(r"discard (?:a|an|\d+) pok[eé]mon tool", d, re.I)
        if m_n:
            for t in got:
                parts = t.split(":")
                if len(parts) > 2 and parts[2] != "opp":
                    errs.append(f"{card_name} · {item_name}：卡面目标是**对手**"
                                f"宝可梦，`{t}` 的范围却是 `{parts[2]}`")
    return errs


def audit_search_energy_distinct(card_name: str, item_name: str, desc: str,
                                 effects: list) -> list:
    """检索**入手**侧的「of different types」对账（2026-11-02 · 第三十五类）

    卡面：「Search your deck for up to 3 Basic Energy cards **of different
    types**, reveal them, and put them into your hand.」（Eevee SFA 50 /
    Energy Search Pro SSP 176）

    ⚠ 这条限制**此前完全没有建模**：解析链忽略 `of different types`，产出
    `search_energy:3` → 允许拿 3 张**同色**能量（静默加强），而卡一直躺在池里。
    这正是「未建模能看见、静默加强看不见」的典型：卡能打、不报错、测试全绿。
    所以本类必须**双向**核对。
    """
    errs = []
    d = _norm(desc or "")
    tags = [str(t) for t in (effects or []) if str(t).startswith("search_energy:")]
    want = bool(re.search(r"basic energy cards? of different types", d, re.I)) \
        and bool(re.search(r"put (?:them|those cards) into your hand", d, re.I))
    if not want:
        for t in tags:
            if "distinct" in t.split(":"):
                errs.append(f"{card_name} · {item_name}：卡面没有「Basic Energy "
                            f"cards of different types（入手）」却出现 `{t}`"
                            f" —— 认领越界（凭空虚削）")
        return errs
    if not tags:
        errs.append(f"{card_name} · {item_name}：卡面要求「different types」的检索"
                    f"入手，却没有 `search_energy:` —— 整句未建模")
        return errs
    for t in tags:
        if "distinct" not in t.split(":"):
            errs.append(f"{card_name} · {item_name}：卡面限定「of different "
                        f"types」，`{t}` 却没带 `:distinct` —— 允许拿同色能量"
                        f"（静默加强）")
    return errs


def audit_play_cost(card_name: str, desc: str, play_cost: str) -> list:
    """打出代价对账（2026-11-02 · 第三十二类）

    卡面：``You can use this card only if you discard N other cards from your
    hand.`` → 池条目 ``play_cost="discard:N"``（``TrainerDef.play_cost``）。

    三类错法，后果不同但都**看不出来**：

      ① 卡面有代价句、条目**没有** ``play_cost`` → 这张卡**免费**使用
         （静默加强，最危险：手牌一张不少就把效果拿了）；
      ② 张数不符（卡面 3、条目 2）→ 强度错，同样不报错；
      ③ 条目有 ``play_cost``、卡面**没有**该句 → 凭空加代价（静默削弱）。

    另守**限定类别的代价**：``discard a Basic {R} Energy card from your hand``
    （Blowtorch）不是「任意 N 张」—— 第三十六批时本对账**故意不认**它（宁可
    停在未建模）。第三十八批解析侧新增了 ``discard_energy:<n>:<etype>`` 形态，
    这里改为用**独立正则**核对：卡面写 Basic {R} 却落成 ``discard:1``，说明
    「只能弃基本火能量」被放宽成「弃任意牌」= 代价被静默放宽。

    ⚠ 正则必须在本脚本内**独立实现**，不 import 解析包的同名正则：共用同一个
    正则时，解析写错 → 对账一起错 → 永远通过。对账的全部价值就在「换一套写法
    重算一遍」。

    为什么单独一条而不是塞进 ``audit_energy_cost``：那个查的是**招式效果里**的
    能量代价（副作用），本类查的是**打出这张卡的门槛**，字段层级都不同
    （``play_cost`` 在卡上，不在 effects 里）。
    """
    errs = []
    d = _norm(desc or "")
    m_en = _PLAY_COST_ENERGY_SHAPE.search(d)
    m = None if m_en else _PLAY_COST_SHAPE.search(d)
    want = ""
    if m_en:
        want = (f"discard_energy:{int(m_en.group('n')) if m_en.group('n') else 1}:"
                f"{_TYPE_COLOR.get(m_en.group('c').upper(), m_en.group('c').lower())}")
    elif m:
        want = f"discard:{int(m.group('n')) if m.group('n') else 1}"
    got = str(play_cost or "").strip()
    if got and not _PLAY_COST_VALUE.fullmatch(got):
        # 形态先说：形态非法时下面「张数不符 / 凭空加代价」的判断都是噪音
        errs.append(f"{card_name}：`play_cost={got}` 形态非法（应为 "
                    f"`discard:<n>` 或 `discard_energy:<n>:<etype>`，n ≥ 1）")
    elif want and not got:
        errs.append(f"{card_name}：卡面要求打出代价 `{want}`，条目却没有 "
                    f"`play_cost` —— 这张卡会**免费**使用（静默加强）")
    elif want and got != want:
        errs.append(f"{card_name}：卡面打出代价是 `{want}`，条目写的是 "
                    f"`{got}`（张数/属性不符）")
    elif got and not want:
        errs.append(f"{card_name}：条目有 `play_cost={got}`，但卡面"
                    f"没有「only if you discard …」句（凭空加代价）")
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
        # 第一百三十五批（S1b）：`choose:optional_energy_cost:` 也是**真的付账**
        # （引擎挂起 confirm，选中 yes 才弃能量）→ 与其它代价 tag 同等看待。
        cost_tags = [t for t in tags if t.startswith(
            ("discard_energy", "discard_hand_energy", "energy_to_deck",
             "energy_self_to_deck", "self:", "choose:optional_energy_cost:"))]
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
    # ⑥ 属性不符（第八十八批）：卡面写「Basic {G} Energy card from your hand」
    # 却落成 `discard_hand_energy:N:type:fire` —— 会弃错属性的能量（静默算错）。
    for t in hand_tags:
        mt = re.search(r":type:([a-z]+)", t)
        if not mt:
            continue
        got_type = mt.group(1).lower()
        ms = re.search(r"basic \{([a-z])\} energy", d, re.I)
        if ms:
            want = _EN_LETTER.get(ms.group(1).upper())
            if want and want != got_type:
                errs.append(f"{card_name} · {item_name}：`{t}` 属性 {got_type} "
                            f"与卡面 {{{ms.group(1)}}} 不符")
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
        # 「Choose up to N of your … For each of those … evolve it」= **选** N 只
        # 各进化一次（Team Rocket's Nidorina，第四十批）→ `evolve_each_pick:<N>:…`
        # ⚠ 必须排在 `evolve_from_deck` / `evolve_each_bench` 之前：本句的检索
        #   主语是「those Pokémon」这个**选出的集合**，不是「你的牌库随便拿一张」。
        m_pick = re.search(r"choose up to (\d+) of your", _norm(desc or ""))
        if m_pick:
            if not any(str(t).startswith("evolve_each_pick:") for t in effects):
                errs.append(f"{card_name} · {item_name}：卡面「Choose up to "
                            f"{m_pick.group(1)} of your … … evolve it」→ 应为 "
                            f"`evolve_each_pick:{m_pick.group(1)}:…`，实际 {got}")
            return errs
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
        # 第一百零一批起还有**受击检索铺场**（`on_damaged:search_bench:…`，
        # Team Rocket's Koffing）—— 用 `startswith` 会把它判成「缺 tag」。
        if not any("search_bench" in str(t) for t in effects):
            errs.append(f"{card_name} · {item_name}：检索后**铺到后备**"
                        f"→ 应为 `search_bench:…`，实际 {got}")
        return errs

    # ---- ①b 去向是**弃牌堆**（search_discard）----
    # 「search your deck for up to N … **and discard them**」（Drilbur）：
    # 分类规则只覆盖「入手」形态，拿它要求 `search_energy` 是错的口径 ——
    # 弃置去向必须显式写在 tag 里（`search_discard:N:energy:type:<t>`）。
    if re.search(r"and discard them", _norm(desc or ""), re.I):
        if not any(str(t).startswith("search_discard") for t in effects):
            errs.append(f"{card_name} · {item_name}：检索后**弃置**→ 应为 "
                        f"`search_discard:…`，实际 {got_hand}")
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
    effects = _expand_compound(effects)
    seen_static = set()
    next_spans = _next_turn_spans(desc)
    for m in re.finditer(r"attacks used by [^.]{0,140}? do(?:es)? (\d+) "
                         r"(more|less) damage", desc or "", re.I):
        if any(s <= m.start() < e for s, e in next_spans):
            continue                     # 跨回合修正句，由第十五类接管
        n = int(m.group(1))
        # 「during this turn, attacks used by this Pokémon do N more damage」
        # （Feraligatr「Torrential Heart」）—— **回合级**加伤，不是常驻静态：
        # 由 `self_dmg_bonus:N` 承载（对账在第九十五批前只认 fx，会误报）。
        _tail = (desc or "")[max(0, m.start() - 140): m.end() + 80].lower()
        if "during this turn" in _tail and any(
                str(t).startswith(f"self_dmg_bonus:{n}") for t in effects):
            continue
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
            # ⚠ 必须**从长到短**匹配：`from:basic` 是 `from:basic_non_colorless`
            #   的**子串**，按字典顺序先命中 `basic` 就会拿错卡面短语
            #   （Terapagos ex「Basic non-{C} Pokémon」被误报来源不符）。
            for src, rx in sorted(_IMM_SRC_TEXT.items(),
                                  key=lambda kv: -len(kv[0])):
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
            for src, rx in sorted(_IMM_SRC_TEXT.items(),
                                  key=lambda kv: -len(kv[0])):
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
    # 第五十五批：限定式。⚠ 与无条件写法必须严格区分 ——
    # `opp_active` 的通用正则只认「主动语态」与「… is knocked out」的被动语态，
    # 而这两条各带**自己的前提从句**，拿通用正则顶替会把「条件击倒」当无条件。
    "opp_active:if:senergy": (
        r"if your opponent'?s active pok[eé]mon has any special energy attached, "
        r"it is knocked out"),
    # 第八十六批：确定性「剩余 HP 最少者」（Inteleon「Bring Down」）。
    # （normalize 后撇号为直撇、空白折叠；括号按字面转义。）
    "least_hp": (
        r"choose a pok[eé]mon in play \(yours or your opponent'?s\) that has the "
        r"least hp remaining, except for this pok[eé]mon, and it is knocked out"),
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
        if scope.startswith("opp_all:if:hp_le:"):
            # 第五十五批：「Knock Out **each** … that has N HP or less remaining」
            # ⚠ 「each」= 逐只全倒；落成 `opp_any`（只倒一只）是把多杀削成单杀，
            # 而两者在卡面文本上只差一个词，对账必须逐字核对 HP 阈值。
            _n = scope.rsplit(":", 1)[-1]
            if not re.search(
                    r"knock out each of your opponent'?s pok[eé]mon that has "
                    + re.escape(_n) + r" hp or less remaining", low):
                errs.append(f"{card_name} · {item_name}：`{tag}` 的「each … "
                            f"{_n} HP or less remaining」与卡面不符")
            continue
        rx = _KO_TEXT.get(scope)
        if rx is None:
            errs.append(f"{card_name} · {item_name}：未识别的击倒范围 `{scope}`")
            continue
        if not re.search(rx, low):
            errs.append(f"{card_name} · {item_name}：`{tag}` 与卡面目标不符")
    if re.search(r"knock out", low) and not kos:
        # D4（2026-11-08）：Damp 族（Psyduck / Golduck）的「requires the Pokémon
        # using it to Knock Out itself」是**特性的前置代价描述**，不是击倒效果 ——
        # 把它当「击倒被静默丢弃」是假阳性。屏蔽这一句（等长空格，位置守恒），
        # 其余任何提到 Knock Out 的写法仍然要求有 knockout tag。
        _damp_masked = re.sub(
            r"requires the pok[eé]mon using it to knock out itself", " " * 54,
            low)
        if re.search(r"knock out", _damp_masked):
            errs.append(f"{card_name} · {item_name}：卡面写了 Knock Out，"
                        f"却没有 knockout tag（击倒被静默丢弃）")
    # 第八十六批：确定性「least HP remaining」击倒句式（Inteleon「Bring Down」）
    # 不带 tag 时无法被通用「knock out」正则捕获（卡面用「… and it is Knocked
    # Out」被动语态）→ 单独守卫，避免该句式被静默丢效果。
    if (re.search(r"choose a pok[eé]mon in play.*least hp remaining.*"
                  r"and it is knocked out", low)
            and not any("least_hp" in t for t in kos)):
        errs.append(f"{card_name} · {item_name}：卡面「least HP remaining」击倒"
                    f"句式未产出 knockout:least_hp tag（击倒被静默丢弃）")
    # 被击倒**触发**（第十批）：tag 与卡面必须逐项对上
    for tag in (str(t) for t in effects):
        if tag.startswith("ko_prize_plus:"):
            m = re.search(r"take (\d+) more prize card", low)
            if not m or f"ko_prize_plus:{int(m.group(1))}" not in tag:
                errs.append(f"{card_name} · {item_name}：`{tag}` 与卡面不符")
            elif "opp_basic" in tag and "basic pokémon" not in low:
                # 第八十七批：Hydreigon ex「Greedy Eater」限定「对手基础宝可梦」
                errs.append(f"{card_name} · {item_name}：`{tag}` 含 opp_basic "
                            f"但卡面无「Basic Pokémon」")
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
            if not got and re.search(r"flip a coin until you get tails",
                                     desc or "", re.I):
                # 「Flip a coin until you get tails」：正面数是**连续掷币**的累计，
                # 形态是 `coin_until_tails:dmg_set:N`（第四十批），不是 `coin_each`。
                got = [str(t) for t in effects
                       if str(t) == f"coin_until_tails:dmg_set:{n}"]
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
    # S2d：两句都不能少 —— 少了第二句就是「普通无色能量」，
    # 少了「after attacking」则变成「攻击中途就回附」（能重复支付）。
    "boomerang": ("provides {c} energy", "discarded by an effect of an attack",
                  "after attacking"),
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
    eff = _expand_coin_subs(effects)     # 抛币分支内的回手（Dustox，第四十四批）
    for m in _ENERGY_TO_HAND_RE.finditer(d):
        if not any(str(e).startswith("bounce:energy:") for e in eff):
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
    tags = [str(e) for e in _expand_compound(effects)]
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


def audit_energy_self_to_deck(card_name: str, item_name: str, desc: str,
                              effects: list) -> list:
    """自身附着能量洗回牌库（2026-11-03 第四十一批 · 第三十七类对账）

    卡面原型（Mega Skarmory ex POR 55「Sonic Ripper」）：
    「Shuffle all Energy attached to this Pokémon into your deck, and this attack
    does 220 damage to 1 of your opponent's Pokémon.」

    ⚠ 这是**强制代价**：只建模伤害而不洗回能量 = 「不付代价白打」（静默加强）；
    反过来卡面没有该句却带 tag = 凭空削掉自己全部能量（认领越界）。双向都要拦。
    """
    errs: list = []
    d = PAREN.sub(" ", desc or "")
    # ⚠ 只认同句带「, and this attack does」的**强制**代价形态；Dialga MEG 95
    # 的「You may … and have this attack do 80 more damage」是可选代价，不在此列。
    has_sent = bool(re.search(
        r"shuffle all energy attached to this pok[eé]mon into your deck, "
        r"and this attack does", d, re.I))
    tags = [str(e) for e in _expand_compound(effects)]
    if has_sent and "energy_self_to_deck:all" not in tags:
        errs.append(f"{card_name} · {item_name}：卡面「Shuffle all Energy attached "
                    "to this Pokémon into your deck」缺 `energy_self_to_deck:all`"
                    "（= 不付代价白打，静默加强）")
    if not has_sent and any(t.startswith("energy_self_to_deck:")
                            for t in tags):
        errs.append(f"{card_name} · {item_name}：卡面没有「Shuffle all Energy "
                    "attached to this Pokémon …」，却出现 "
                    "`energy_self_to_deck:` —— 认领越界")
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

    # ---- 第四十三批（2026-11-03）：两个新的限定计数源 ----
    # 「for each Ancient card in your discard pile」（Roaring Moon）：Ancient 是
    # **卡牌子类**，按卡名（`own_discard_named:ancient`）恒为 0、按弃牌堆总数又多算。
    if re.search(r"ancient card in your discard pile", _norm(d)):
        if not any("own_discard_category:ancient" in t for t in tags):
            errs.append(f"{card_name} · {item_name}：`Ancient card in your discard "
                        "pile` 缺 `own_discard_category:ancient`（按卡名恒 0、按总数多算）")
    # 「for each {L} Energy attached to all of your Iono's Pokémon」（Iono's Voltorb）
    # ——必须是「阵营 + 属性 + 具名」三重限定；落成 `<zone>_energy_<color>` 会把
    # 别的宝可梦身上的能量也数进来。
    for m in re.finditer(r"energy attached to all of your "
                         r"((?!(?:opponent|benched)\b)[a-z][a-z' ]{1,20}?)'s "
                         r"pok[eé]mon", _norm(d)):
        key = re.sub(r"[^a-z0-9]", "", m.group(1).lower())
        colors = {_TYPE_COLOR.get(x.upper()) for x in
                  re.findall(r"\{([a-z])\}", d, re.I)}
        colors.discard(None)
        if not key or not colors:
            errs.append(f"{card_name} · {item_name}：`{m.group(0)[:50]}` "
                        "阵营/属性判不出 → 不该进池")
            continue
        for color in sorted(colors):
            want = f"own_field_named_energy:{key}:{color}"
            if not any(want in t for t in tags):
                errs.append(f"{card_name} · {item_name}：`{m.group(0)[:50]}` "
                            f"缺 `{want}`（笼统数全队能量会多算）")
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


# ==========================================================================
# 第四十批（2026-11-02 · 第三十六类）：连续抛币计数源 / 选 N 只进化 / 不同属性检索
# --------------------------------------------------------------------------
# 三个族的共同风险是「张数（= 随机变量）被折算成常数」，而折算**不报错**：
#   · 「Flip a coin until you get tails」+ 磨牌/弃手牌/检索 → 落成 `mill:1` /
#     `discard_opp_random:1` / `search_any:1`（每正面 1 张变成恒定 1 张）；
#   · 「Choose up to N of your …」逐只进化 → 落成 `evolve_each_bench`（至多 N 只
#     变成**全部后备**）；
#   · 「any number of Basic Energy cards of different types」→ 落成常数
#     `search_energy:N:distinct`（牌库属性种类数一变就少拿/拿不到）。
# 下面每条正则都**独立实现**（刻意不 import 解析包的同名正则）：共用同一个正则
# 等于自己给自己盖章 —— 解析写错时对账会一起错、永远通过。
_UTAIL_SENT = re.compile(r"flip a coin until you get tails", re.I)
_UTAIL_DMG = re.compile(
    r"flip a coin until you get tails[^.]{0,40}?\.\s*this attack does (\d+) "
    r"(more )?damage for each heads", re.I)
_UTAIL_MILL = re.compile(
    r"flip a coin until you get tails[^.]{0,40}?\.\s*for each heads, discard "
    r"the top card of your opponent", re.I)
_UTAIL_HAND = re.compile(
    r"flip a coin until you get tails[^.]{0,40}?\.\s*for each heads, choose a "
    r"random card from your opponent", re.I)
_UTAIL_SEARCH = re.compile(
    r"flip a coin until you get tails[^.]{0,40}?\.\s*search your deck for a "
    r"number of cards up to the number of heads", re.I)
_UTAIL_DISCARD = re.compile(
    r"flip a coin until you get tails[^.]{0,40}?\.\s*for each heads, discard an "
    r"energy from your opponent", re.I)
_EACHPICK = re.compile(
    r"choose up to (\d+) of your (.+?)\.\s*for each of those pok[eé]mon, search "
    r"your deck for a card that evolves from that pok[eé]mon and put it onto "
    r"that pok[eé]mon to evolve it", re.I)
_SEARCH_ANY_DISTINCT = re.compile(
    r"search your deck for any number of basic energy cards of different types",
    re.I)


def audit_shuffle_opp_pokemon(card_name: str, item_name: str, desc: str,
                              effects: list) -> list:
    """对手宝可梦连同附着卡洗回其牌库（2026-11-03 第四十二批 · 第三十八类）

    卡面原型：「Flip a coin. If heads, choose 1 of your opponent's [Benched]
    Pokémon. Shuffle that Pokémon and all attached cards into their deck.」
    （Sylveon / Shiftry）

    两类静默错误都要拦：
      · 卡面有该句却**没有** tag → 效果整段丢失（打出去什么都不发生）；
      · 卡面没有该句却有 tag → 认领越界（会把别的效果误当成「洗回牌库」）。
    ⚠ 并要核对 scope：Benched 限定必须有 `:opp_bench`（落成 `:opp` 会把战斗位
      也纳入可选集合 = 打错对象）。
    """
    errs: list = []
    d = _norm(PAREN.sub(" ", desc or ""))     # ’ → ' 归一（否则 `opponent's` 不匹配）
    tags = [str(e) for e in _expand_compound(effects)]
    has = [t for t in tags if "shuffle_opp_pokemon:" in t]
    m = re.search(
        r"flip a coin[.] if heads, choose (\d+) of your opponent'?s? (benched )?"
        r"pok[eé]mon[.] shuffle that pok[eé]mon and all attached cards into "
        r"their deck", d, re.I)
    if m:
        n = int(m.group(1))
        if n != 1:
            errs.append(f"{card_name} · {item_name}：卡面写「choose {n}」，"
                        "而本机制的确定性目标只支持 1 只 → 不该进池")
            return errs
        want = ("coin:heads:shuffle_opp_pokemon:"
                + ("opp_bench" if m.group(2) else "opp"))
        if want not in tags:
            errs.append(f"{card_name} · {item_name}：卡面「Shuffle that Pokémon "
                        f"and all attached cards into their deck」应为 `{want}`，"
                        f"实际 {has or '无该 tag'}")
    elif has:
        errs.append(f"{card_name} · {item_name}：卡面没有「… Shuffle that Pokémon "
                    "and all attached cards into their deck」，却出现 "
                    f"{has} —— 认领越界")
    return errs


def audit_batch40(card_name: str, item_name: str, desc: str,
                  effects: list) -> list:
    """连续抛币计数源 / 选 N 只进化 / 不同属性检索（第三十六类）"""
    errs: list = []
    d = PAREN.sub(" ", desc or "")
    tags = [str(e) for e in _expand_compound(effects)]

    # ① 「does N [more] damage for each heads」（连续掷币）
    m = _UTAIL_DMG.search(d)
    if m:
        n = int(m.group(1))
        want = (f"coin_until_tails:dmg_plus:{n}" if m.group(2)
                else f"coin_until_tails:dmg_set:{n}")
        have = [t for t in tags if t.startswith("coin_until_tails:")]
        if want not in have:
            errs.append(f"{card_name} · {item_name}：`{m.group(0)[:60]}` 应为 "
                        f"`{want}`（{'加在印刷伤害上' if m.group(2) else '按正面数重算基础伤害'}），"
                        f"实际 {have or '无 coin_until_tails tag'}")

    # ② 非伤害三形态（张数 = 正面数）
    for rx, want, what in ((_UTAIL_MILL, "coin_until_tails:mill:opp", "磨对手牌库"),
                           (_UTAIL_HAND, "coin_until_tails:shuffle_opp_hand",
                            "对手手牌洗回牌库"),
                           (_UTAIL_SEARCH, "coin_until_tails:search:any",
                            "牌库顶按正面数入手"),
                           (_UTAIL_DISCARD,
                            "coin_until_tails:discard_energy:opp",
                            "对手战斗位按正面数弃能")):
        if rx.search(d) and want not in tags:
            errs.append(f"{card_name} · {item_name}：卡面「{what}」按正面数结算，"
                        f"应为 `{want}`，实际 {tags}（张数被折成常数）")

    # ③ 未认领守门：卡面写了连续掷币、却没有任何本族 tag
    if _UTAIL_SENT.search(d) \
            and not any(t.startswith("coin_until_tails:") for t in tags) \
            and not any(t.startswith("attach_flip_until:") for t in tags):
        errs.append(f"{card_name} · {item_name}：卡面有「Flip a coin until you "
                    f"get tails」，却没有任何 `coin_until_tails:` / "
                    f"`attach_flip_until:` tag（张数会静默丢失或被折成常数）")

    # ④ 选 N 只各进化一次
    m = _EACHPICK.search(d)
    if m:
        n = int(m.group(1))
        pick = [t for t in tags if t.startswith("evolve_each_pick:")]
        if not pick:
            if "evolve_each_bench" in tags:
                errs.append(f"{card_name} · {item_name}：卡面「Choose up to {n} "
                            f"of your …」落成 `evolve_each_bench` —— 「至多 {n} 只」"
                            f"被放大成「全部后备」")
            else:
                errs.append(f"{card_name} · {item_name}：卡面「Choose up to {n} "
                            f"of your … … evolve it」缺 `evolve_each_pick:{n}:…`")
        else:
            part = pick[0].split(":")
            if len(part) < 3 or not part[1].isdigit() or int(part[1]) != n:
                errs.append(f"{card_name} · {item_name}：卡面选取上限是 {n}，"
                            f"`{pick[0]}` 不符")

    # ⑤ 「any number of Basic Energy cards of different types」
    if _SEARCH_ANY_DISTINCT.search(d):
        if "search_energy:all:distinct" not in tags:
            const = [t for t in tags if t.startswith("search_energy:")
                     and t.split(":")[1].isdigit()] if any(
                t.startswith("search_energy:") for t in tags) else []
            errs.append(f"{card_name} · {item_name}：卡面「any number … of "
                        f"different types」应为 `search_energy:all:distinct`，"
                        f"实际 {const or '无该 tag'}（张数被折成常数）")
    return errs


# --------------------------------------------------------------------------
# 2026-11-04 第五十三批对账：`ko_prize_minus` / `status_multi` / `switch_draw_to` /
# `gust_confuse` / `research` / 特性发动代价 `AbilityDef.cost`。
#
# 这六族的共同风险都是「**少一个状态变化，卡照样能打**」：
#   · 少 `ko_prize_minus:` → 该少拿的奖赏照样拿满（白送对手 1 张，全程不报错）；
#   · `status_multi:` 只落一个状态 → 两个异常削成一个（静默变弱，测试全绿）；
#   · `switch_draw_to:` 落成 `switch` + `draw:5` → 不是「补到 5 张」而是「多 5 张」；
#   · `gust_confuse` 落成裸 `gust` → 拉上来的那只不混乱；
#   · `research:` 拆成 `discard:all` + `draw:N` → 训练家只认一个卡级 tag，
#     拆两条 = 整卡拒收且**不记录原因**（9 张训练家曾这样消失）；
#   · 特性代价字段漏写 → 特性**免费发动**，比不建模更糟（强度凭空变高）。
#
# 正则同样**独立实现**，不 import 解析包的同名正则。
_KO_PM_IF_NAMED = re.compile(
    r"if this pok[eé]mon is knocked out by damage from an attack from your "
    r"opponent'?s? pok[eé]mon, and if you have any (.+?) in play, your opponent "
    r"takes (\d+) fewer prize card", re.I)
_KO_PM_EX_ALL = re.compile(
    r"if this pok[eé]mon is knocked out by damage from an attack from your "
    r"opponent'?s? pok[eé]mon \{ex\}, your opponent can'?t take any prize cards",
    re.I)
_KO_PM_TEAM = re.compile(
    r"if 1 of your \{(\w)\} pok[eé]mon is knocked out by damage from an attack "
    r"from your opponent'?s? pok[eé]mon \{ex\}, that player takes (\d+) fewer "
    r"prize card", re.I)
_STATUS_PAIR = re.compile(
    r"opponent'?s? active pok[eé]mon is now (\w+) and (\w+)", re.I)
_SWITCH_DRAW_TO = re.compile(
    r"switch your active pok[eé]mon with 1 of your benched pok[eé]mon[.]\s*"
    r"if you do, draw cards until you have (\d+) cards in your hand", re.I)
_GUST_CONFUSE = re.compile(
    r"switch in 1 of your opponent'?s? benched .+? to the active spot[.]?\s*"
    r"if you do, the new active pok[eé]mon is now (\w+)", re.I)
_RESEARCH = re.compile(r"discard your hand and draw (\d+) cards?", re.I)
_ABILITY_COST_SENT = re.compile(
    # `must` 与 `may` 都是**代价**：may 只表示「可以选择不发动」，不是
    # 「可以免费发动」。解析侧同步支持（第一百一十一批，Lunatone）。
    r"you (?:must|may) (.+?) in order to use this ability", re.I)

_STATUS_WORDS = {"asleep": "sleep", "burned": "burn", "confused": "confuse",
                 "paralyzed": "paralyze", "poisoned": "poison"}
_ENERGY_LETTER = {"G": "grass", "R": "fire", "W": "water", "L": "lightning",
                  "P": "psychic", "F": "fighting", "D": "darkness",
                  "M": "metal", "C": "colorless", "N": "dragon",
                  "Y": "psychic"}


def _name_key(s: str) -> str:
    """卡名 → `if_named` / `discard_named` 用的键（小写、去非字母数字）

    ⚠ 必须先剥掉 `{ex}` 这类**卡面标记**：解析侧 `_tag_name_key` 是在剥离之后
    归一化的，照抄原文会把键算成 `pecharuntex`，于是「标签正确」也会被判不符。
    """
    s = re.sub(r"\{[^}]*\}", "", s or "")
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _want_ability_cost(cost_clause: str):
    """「You must … in order to use this Ability」子句 → 期望的 cost 字段

    五种形态与引擎 `_parse_ability_cost` 一一对应；返回 ``None`` = 该子句
    落在已建模形态之外（应当未建模，不该出现 cost 字段）。
    """
    c = _norm(cost_clause).strip()
    # 顺序不可换：`from this Pokémon` 必须先于 `from your hand` 判定，
    # 否则「discard a Basic {L} Energy from this Pokémon」会被当成手牌代价。
    if re.search(r"put a card from your hand on the bottom of (?:your|the) deck",
                 c):
        return "hand_bottom:1"
    if re.search(r"from this pok[eé]mon", c):
        m = re.search(r"discard a basic \{(\w)\} energy", c)
        return (f"discard_self_energy:1:{_ENERGY_LETTER[m.group(1).upper()]}"
                if m else None)
    if re.search(r"from your hand", c):
        m = re.search(r"discard a basic \{(\w)\} energy", c)
        if m:
            return f"discard_hand_energy:1:{_ENERGY_LETTER[m.group(1).upper()]}"
        # 「a card」是**任意**一张（不是具名卡）→ `discard:1`。必须先于具名分支
        # 判定：否则「discard a card from your hand」里没有可捕获的卡名，会落到
        # 「不在已建模形态内」的误判。
        if re.search(r"discard a card from your hand", c):
            return "discard:1"
        m = re.search(r"discard a (.+?) card from your hand", c)
        if m:
            return f"discard_named:1:{_name_key(m.group(1))}"
        m = re.search(r"discard (\d+) cards? from your hand", c)
        if m:
            return f"discard:{int(m.group(1))}"
    return None


def audit_batch53(card_name: str, item_name: str, desc: str,
                  effects: list, cost: str | None = None) -> list:
    """奖赏减少 / 双状态 / 换位补抽 / 拉人+混乱 / 弃手重抽 / 特性代价（第三十九类）

    ``cost`` 为 ``None`` 表示该条目不是特性（不做代价对账）；空字符串表示
    「特性但没解析出代价」——后者是本节最想抓的静默加强。
    """
    errs: list = []
    d = _norm(PAREN.sub(" ", desc or ""))
    tags = [str(e) for e in _expand_compound(effects)]

    # ① 被击倒时少给奖赏
    m = _KO_PM_IF_NAMED.search(d)
    if m:
        want = f"ko_prize_minus:{int(m.group(2))}:if_named:{_name_key(m.group(1))}"
        have = [t for t in tags if t.startswith("ko_prize_minus:")]
        if want not in have:
            errs.append(f"{card_name} · {item_name}：卡面「… takes {m.group(2)} "
                        f"fewer Prize card」+「if you have any {m.group(1)} in "
                        f"play」应为 `{want}`，实际 {have or '无该 tag'}")
        return errs
    m = _KO_PM_TEAM.search(d)
    if m:
        want = (f"ko_prize_minus:{int(m.group(2))}:from_ex:"
                f"team_{_ENERGY_LETTER[m.group(1).upper()]}")
        have = [t for t in tags if t.startswith("ko_prize_minus:")]
        if want not in have:
            errs.append(f"{card_name} · {item_name}：卡面「1 of your "
                        f"{{{m.group(1)}}} Pokémon … takes {m.group(2)} fewer "
                        f"Prize card」应为 `{want}`（阵营 + 受击者限定都要在），"
                        f"实际 {have or '无该 tag'}")
        return errs
    if _KO_PM_EX_ALL.search(d) and "ko_prize_minus:all:from_ex" not in tags:
        errs.append(f"{card_name} · {item_name}：卡面「… opponent can't take any "
                    "Prize cards for it」应为 `ko_prize_minus:all:from_ex`，"
                    f"实际 {[t for t in tags if 'ko_prize' in t] or '无该 tag'}")

    # ② 一句挂两个异常状态
    m = _STATUS_PAIR.search(d)
    if m:
        _pair = [_STATUS_WORDS[w.lower()] for w in m.groups()
                 if w.lower() in _STATUS_WORDS]
        want = "status_multi:" + "+".join(_pair)
        # ⚠ 不能只比 `startswith("status_multi:")`：双状态也可能**嵌在抛币子效果
        # 里**（`coin_each:1:heads:status_multi:paralyze+poison|cond:all_tails:…`，
        # Lilligant「Bemusing Aroma」）。用子串判定，两种形态都覆盖。
        _joined = " ".join(tags)
        _hit = bool(_pair) and (want in _joined
                                or ("status_multi:" + "+".join(reversed(_pair)))
                                in _joined)
        have = [t for t in tags if "status_multi:" in t]
        if _pair and not _hit:
            single = [t for t in tags if t in _STATUS_WORDS.values()]
            errs.append(f"{card_name} · {item_name}：卡面「is now {m.group(1)} "
                        f"and {m.group(2)}」应为 `{want}`，"
                        f"实际 {have or single or '无状态 tag'}"
                        f"（两个异常削成一个 = 静默变弱）")

    # ③ 换位后「补到手牌 N 张」
    m = _SWITCH_DRAW_TO.search(d)
    if m:
        want = f"switch_draw_to:{int(m.group(1))}"
        if want not in tags:
            hint = [t for t in tags if t.startswith(("switch", "draw"))]
            errs.append(f"{card_name} · {item_name}：卡面「draw cards until you "
                        f"have {m.group(1)} cards in your hand」应为 `{want}`，"
                        f"实际 {hint or '无该 tag'}（拆成 switch + draw:N 会把"
                        f"「补到 N」变成「多抽 N」）")

    # ④ 拉对手后备 + 新战斗位混乱
    m = _GUST_CONFUSE.search(d)
    if m:
        st = _STATUS_WORDS.get(m.group(1).lower())
        if st == "confuse":
            if "gust_confuse" not in tags:
                errs.append(f"{card_name} · {item_name}：卡面「… the new Active "
                            "Pokémon is now Confused」应为 `gust_confuse`，"
                            f"实际 {[t for t in tags if 'gust' in t] or '无 gust'}"
                            "（裸 gust 拉上来的那只不混乱）")
        else:
            errs.append(f"{card_name} · {item_name}：本机制只支持「拉人 + 混乱」，"
                        f"卡面挂的是 {m.group(1)} → 不该进池")

    # ⑤ 弃光手牌再抽 N 张（训练家/招式都只认**一条**复合 tag）
    m = _RESEARCH.search(d)
    if m:
        want = f"research:{int(m.group(1))}"
        if want not in tags:
            split = [t for t in tags if t.startswith(("discard:all", "draw:"))]
            errs.append(f"{card_name} · {item_name}：卡面「Discard your hand and "
                        f"draw {m.group(1)} cards」应为 `{want}`，"
                        f"实际 {split or '无该 tag'}"
                        f"（拆成两条 = 训练家整卡拒收，招式侧顺序也可能反）")

    # ⑥ 特性发动代价：漏字段 = 免费发动
    if cost is not None:
        m = _ABILITY_COST_SENT.search(d)
        if m:
            want = _want_ability_cost(m.group(1))
            if want is None:
                errs.append(f"{card_name} · {item_name}：代价子句「{m.group(1)[:50]}」"
                            "不在已建模的五形态内，却仍进池（应有 cost 或不出现在池中）")
            elif cost != want:
                errs.append(f"{card_name} · {item_name}：卡面代价「{m.group(1)[:50]}」"
                            f"应为 `{want}`，实际 {cost or '空（特性免费发动）'}")
        elif cost:
            errs.append(f"{card_name} · {item_name}：卡面没有「You must … in order "
                        f"to use this Ability」，却写了 cost=`{cost}` → 认领越界")
    return errs


_OPP_DISCARD_TEXT = re.compile(
    r"^your opponent discards (\d+|a) cards? from their hand$", re.I)
_OPP_DISCARD_TO_TEXT = re.compile(
    r"^discard random cards? from your opponent'?s hand until they have "
    r"(\d+) cards? in their hand$", re.I)
_DISCARD_BOTH_TEXT = re.compile(
    r"^each player discards cards? from their hand until they have "
    r"(\d+) cards? in their hand", re.I)
_HACKING_TEXT = re.compile(
    r"^discard a card from your hand[.]\s*if you do, your opponent discards a "
    r"card from their hand$", re.I)
_RETREAT_FREE_ENERGY = re.compile(
    r"all of your pok[eé]mon that have (?:any )?\{(\w)\} energy attached have "
    r"no retreat cost", re.I)
_RETREAT_FREE_BASIC = re.compile(
    r"your basic pok[eé]mon in play have no retreat cost", re.I)
_RETREAT_FREE_SELF = re.compile(
    r"if this pok[eé]mon has no energy attached, it has no retreat cost", re.I)
_PLAY_BLOCK_ITEM = re.compile(
    r"as long as this pok[eé]mon is in the active spot, your opponent can'?t "
    r"play any item cards from their hand", re.I)
_PLAY_BLOCK_ITEM_TOOL = re.compile(
    r"as long as this pok[eé]mon is in the active spot, your opponent can'?t "
    r"play any item cards or pok[eé]mon tool cards from their hand", re.I)
_PLAY_BLOCK_ABILITY = re.compile(
    r"as long as this pok[eé]mon is in the active spot, your opponent can'?t "
    r"play any pok[eé]mon that has an ability from their hand, except for "
    r"(.+?) pok[eé]mon", re.I)
_EN_LETTER = {"G": "grass", "R": "fire", "W": "water", "L": "lightning",
              "P": "psychic", "F": "fighting", "D": "darkness", "M": "metal",
              "C": "colorless", "N": "dragon", "Y": "psychic"}


def audit_batch54(card_name: str, item_name: str, desc: str,
                  effects: list) -> list:
    """对手手牌弃置 / 撤退费归零 / 常驻禁打（第四十类 · 第五十四~五十七批）

    三族的共同风险都是「**少一个限制，卡照样能打**」：
      · `discard_opp_random:N` 的 N 少写 → 少弃对手资源（静默变弱）；
      · `discard_to_both:N` 只弃对手 → 我方手牌没减，「双方」削成「单方」；
      · `fx:retreat_free:` 落成 `passive:no_retreat`（只管自己）→ 全队免费撤退消失；
      · `fx:play_block:` 漏了 `:active` → 封锁**永久生效**（离场也不解除）；
      · 限时形态（During your opponent's next turn）被误当常驻 → 永久封锁。
    """
    errs: list = []
    d = _norm(PAREN.sub(" ", desc or "")).strip().rstrip(".")
    tags = [str(e) for e in _expand_compound(effects)]
    blob = " ".join(tags)

    # ① 对手弃手牌 / 双方弃到剩 N
    m = _HACKING_TEXT.match(d)
    if m:
        if "discard_opp_random:1" not in tags or "discard:1" not in tags:
            errs.append(f"{card_name} · {item_name}：卡面「Discard a card from "
                        f"your hand. If you do, your opponent discards a card "
                        f"from their hand.」应为 `discard:1` + "
                        f"`discard_opp_random:1`，实际 {tags}")
        return errs
    m = _OPP_DISCARD_TO_TEXT.match(d)
    if m:
        want = f"discard_opp_to:{int(m.group(1))}"
        if want not in tags:
            errs.append(f"{card_name} · {item_name}：卡面「discard random cards "
                        f"… until they have {m.group(1)} cards」应为 `{want}`，"
                        f"实际 {[t for t in tags if 'discard' in t] or '无该 tag'}")
        return errs
    m = _DISCARD_BOTH_TEXT.match(d)
    if m:
        want = f"discard_to_both:{int(m.group(1))}"
        if want not in tags:
            hint = "（只弃对手 = 「双方」削成「单方」）" \
                if any(t.startswith("discard_opp_to:") for t in tags) else ""
            errs.append(f"{card_name} · {item_name}：卡面「Each player discards "
                        f"… until they have {m.group(1)} cards」应为 `{want}`，"
                        f"实际 {[t for t in tags if 'discard' in t] or '无该 tag'}"
                        f"{hint}")
        return errs
    m = _OPP_DISCARD_TEXT.match(d)
    if m:
        raw = m.group(1).lower()
        n = 1 if not raw.isdigit() else int(raw)
        want = f"discard_opp_random:{n}"
        if want not in tags:
            errs.append(f"{card_name} · {item_name}：卡面「Your opponent discards "
                        f"{m.group(1)} card(s) from their hand」应为 `{want}`，"
                        f"实际 {[t for t in tags if 'discard' in t] or '无该 tag'}")
        return errs

    # ② 撤退费用归零
    m = _RETREAT_FREE_ENERGY.search(d)
    if m:
        want = f"fx:retreat_free:energy:{_EN_LETTER.get(m.group(1).upper())}"
        if want not in tags:
            errs.append(f"{card_name} · {item_name}：卡面「All of your Pokémon "
                        f"that have {{{m.group(1)}}} Energy attached have no "
                        f"Retreat Cost」应为 `{want}`，实际 {tags}")
        return errs
    if _RETREAT_FREE_BASIC.search(d):
        if "fx:retreat_free:basic" not in tags:
            errs.append(f"{card_name} · {item_name}：卡面「Your Basic Pokémon in "
                        "play have no Retreat Cost」应为 `fx:retreat_free:basic`，"
                        f"实际 {tags}")
        return errs
    if _RETREAT_FREE_SELF.search(d):
        if "fx:retreat_free:self:if:no_energy" not in tags:
            errs.append(f"{card_name} · {item_name}：卡面「If this Pokémon has no "
                        "Energy attached, it has no Retreat Cost」应为 "
                        f"`fx:retreat_free:self:if:no_energy`，实际 {tags}")
        return errs

    # ③ 常驻禁打
    for rx, want in ((_PLAY_BLOCK_ITEM_TOOL, "fx:play_block:item+tool:active"),
                     (_PLAY_BLOCK_ITEM, "fx:play_block:item:active")):
        if rx.search(d):
            if want not in tags:
                errs.append(f"{card_name} · {item_name}：卡面「… can't play any "
                            f"…」应为 `{want}`，"
                            f"实际 {[t for t in tags if 'play_block' in t] or '无该 tag'}")
            return errs
    m = _PLAY_BLOCK_ABILITY.search(d)
    if m:
        key = re.sub(r"[^a-z0-9]", "", _norm(m.group(1)))
        want = f"fx:play_block:ability_pokemon:active:except:{key}"
        if want not in tags:
            errs.append(f"{card_name} · {item_name}：卡面「… except for "
                        f"{m.group(1)} Pokémon」应为 `{want}`，"
                        f"实际 {[t for t in tags if 'play_block' in t] or '无该 tag'}")
        return errs

    # ④ 未认领守门：常驻禁打的卡面却**没有任何**对应 tag（含限时形态误落常驻）
    if re.search(r"as long as this pok[eé]mon is in the active spot, your "
                 r"opponent can'?t play any", d) and "fx:play_block:" not in blob:
        errs.append(f"{card_name} · {item_name}：卡面是**常驻**禁打"
                    f"（As long as … in the Active Spot），却没有任何 "
                    f"`fx:play_block:` tag → 封锁整段丢失")
    return errs


# ----------------------------------------------------------------------------
# 第五十八~六十二批：新增机制的对账（第四十一类）
#   覆盖 draw_cond / shuffle_draw_split / team_boost / heal:need: / heal:alt: /
#        play_gate / passive:extra_attack
# 重点抓「方向写反 / 量纲错 / 范围漏」三类静默加强。
# ⚠ 每条检查都**先确认 tag 已在池中**（或文本有特定句式），再比对——不在池中的
#   卡（如 Choose-1 双选项、Kieran）不会被误报。
# ----------------------------------------------------------------------------
_DRAW_COND_RE = re.compile(
    r"draw (?P<base>\d+) cards\.?\s*(then, )?if (?P<who>you|your opponent) "
    r"(?:has|have) (?:(?P<ex>\d+) exactly |(?P<n>\d+) or (?P<cmp>more|fewer) ).*?"
    r"draw (?P<extra>\d+) (?P<direction>more|instead) cards", re.I)
_SHUFFLE_SPLIT_RE = re.compile(
    r"you draw (\d+) cards?,? and your opponent draws (\d+) cards", re.I)
_TEAM_BOOST_RE = re.compile(r"do (\d+) more damage", re.I)
_HEAL_RE = re.compile(r"heal (\d+) damage from your active pok[eé]mon", re.I)
_HEAL_NEED_RE = re.compile(r"that has (\d+) or more energy attached", re.I)
_HEAL_ALT_RE = re.compile(
    r"if that pok[eé]mon is an (.+?) pok[eé]mon, heal (\d+) damage .* instead", re.I)
_PLAY_GATE_OK = {"opp_ko_last_turn", "tera_in_play", "went_second_first_turn",
                 "own_behind_prizes", "own_ko_last_turn",
                 # 第一百零九批（TR Archer）：只算 Team Rocket's 宝可梦被击倒。
                 # ⚠ 不能并进 `own_ko_last_turn` —— 门槛范围不同。
                 "own_ko_tr_last_turn"}


def _draw_cond_key(m) -> str | None:
    if m.group("ex"):
        return (f"own_prizes_exactly:{m.group('ex')}" if m.group("who") == "you"
                else f"opp_prizes_exactly:{m.group('ex')}")
    who, num, cmp_ = m.group("who"), m.group("n"), m.group("cmp")
    if cmp_ == "more":
        return (f"own_hand_at_least:{num}" if who == "you"
                else f"opp_hand_at_least:{num}")
    return (f"own_prizes_at_most:{num}" if who == "you"
            else f"opp_prizes_at_most:{num}")


def audit_batch58_62(card_name: str, item_name: str, desc: str,
                     effects: list, play_gate: str | None = None) -> list:
    errs: list = []
    d = _norm(PAREN.sub(" ", desc or ""))
    tags = [str(e) for e in _expand_compound(effects)]

    # ① draw_cond：keep/shuffle 与 plus/set 方向
    if any(t.startswith("draw_cond:") for t in tags):
        m = _DRAW_COND_RE.search(d)
        if not m:
            return errs          # 文本形态不在已识别范围 → 不误报
        base, extra = int(m.group("base")), int(m.group("extra"))
        direction = "plus" if m.group("direction") == "more" else "set"
        shuffle = "shuffle" if "shuffle your hand into your deck" in d else "keep"
        cond = _draw_cond_key(m)
        if cond is None:
            return errs
        want = f"draw_cond:{shuffle}:{base}:{direction}:{extra}:if:{cond}"
        if want not in tags:
            have = [t for t in tags if t.startswith("draw_cond:")]
            flipped = (f"draw_cond:{shuffle}:{base}:"
                       f"{'set' if direction == 'plus' else 'plus'}:{extra}:if:{cond}")
            hint = "（疑似 plus↔set 方向写反）" if flipped in tags else ""
            errs.append(f"{card_name} · {item_name}：卡面「draw {base} … "
                        f"draw {extra} {direction}」应为 `{want}`，实际 "
                        f"{have or '无'}{hint}")

    # ② shuffle_draw_split：双方各抽不同张数
    if any(t.startswith("shuffle_draw_split:") for t in tags):
        m = _SHUFFLE_SPLIT_RE.search(d)
        if m:
            want = f"shuffle_draw_split:{m.group(1)}:{m.group(2)}"
            if want not in tags:
                errs.append(f"{card_name} · {item_name}：卡面「you draw "
                            f"{m.group(1)} / opponent {m.group(2)}」应为 "
                            f"`{want}`，实际 "
                            f"{[t for t in tags if 'shuffle_draw' in t] or '无'}")

    # ③ team_boost：本回合全队增伤（量 + 范围）
    if any(t.startswith("team_boost:") for t in tags):
        m = _TEAM_BOOST_RE.search(d)
        if m:
            amt = int(m.group(1))
            scope = "all"
            if "{ex}" in d and "{v}" in d:
                scope = "cat:ex+v"
            elif "{ex}" in d:
                scope = "cat:ex"
            else:
                lt = re.search(r"your \{(\w)\} pok[eé]mon", d)
                if lt:
                    scope = f"type:{_ENERGY_LETTER.get(lt.group(1).upper(), lt.group(1).lower())}"
            # tag 里 all 不写前缀；其余必须精确
            want = f"team_boost:{amt}:{scope}" if scope != "all" else \
                f"team_boost:{amt}"
            if want not in tags:
                errs.append(f"{card_name} · {item_name}：卡面「do {amt} more "
                            f"damage」范围应为 `{scope}`，实际 "
                            f"{[t for t in tags if t.startswith('team_boost')] or '无'}")

    # ④ heal:need: / heal:alt: 两种相反方向
    if any(t.startswith("heal:") for t in tags):
        m = _HEAL_RE.search(d)
        if m:
            amt = int(m.group(1))
            mn = _HEAL_NEED_RE.search(d)
            if mn:
                want = f"heal:{amt}:active:need:own_active_energy_at_least:{mn.group(1)}"
                if want not in tags:
                    errs.append(f"{card_name} · {item_name}：卡面「heal {amt} … "
                                f"that has {mn.group(1)} or more Energy」应为 "
                                f"`{want}`，实际 "
                                f"{[t for t in tags if t.startswith('heal')] or '无'}"
                                "（need 落成无条件/alt 都会改强度）")
            ma = _HEAL_ALT_RE.search(d)
            if ma:
                key = _name_key(ma.group(1))
                want = f"heal:{amt}:active:alt:{ma.group(2)}:if_name:{key}"
                if want not in tags:
                    errs.append(f"{card_name} · {item_name}：卡面「heal {amt} … if "
                                f"that Pokémon is {ma.group(1)}, heal "
                                f"{ma.group(2)} instead」应为 `{want}`，实际 "
                                f"{[t for t in tags if t.startswith('heal')] or '无'}"
                                "（alt 落成加值=多回一倍，落成无条件=非本卡也回满）")

    # ⑤ play_gate 形状校验（card-level 字段）
    if play_gate:
        # D2（2026-11-08）：**多具名卡合取** `all_in_play:A|B|…`。
        # 这里不只放行前缀、还要校验**形状**（≥2 张、每张是具名卡）：
        # 否则「把描述性短语当卡名」会被静默放过，而它的后果是门槛**永远判不
        # 满足**（卡被锁死，对局里只说「条件不满足」，查不出是解析写错）。
        _m_all = re.fullmatch(r"all_in_play:(.+)", play_gate)
        if _m_all:
            _names = [x.strip() for x in _m_all.group(1).split("|") if x.strip()]
            if len(_names) < 2 or not all(
                    ("'s" in n or "\u2019s" in n or "{ex}" in n) for n in _names):
                errs.append(f"{card_name} · {item_name}：play_gate=`{play_gate}` "
                            "的具名卡列表形状不对（需 ≥2 张且每张带 's 或 {ex}）")
        elif play_gate not in _PLAY_GATE_OK and \
                not re.match(r"opp_prizes_exactly:\d+", play_gate) and \
                not re.match(r"own_hand_at_most:\d+", play_gate):
            errs.append(f"{card_name} · {item_name}：play_gate=`{play_gate}` "
                        "不是已识别的门槛形态")
        # 第一百三十五批：门槛有两种合法句式 ——
        #   ①「You can use this card only if …」（历史形态）
        #   ② Naveen 的括注「(If you can't draw any cards in this way, you can't
        #      use this card.)」—— 同样是**使用前置条件**，只是写法不同。
        # ⚠ 仍要求卡面确有一句「不能用」的依据；否则「有 gate 无依据」= 凭空限制。
        _gate_src = ("you can use this card only if" in d) or (
            "you can't use this card" in d and "in this way" in d)
        if not _gate_src:
            errs.append(f"{card_name} · {item_name}：有 play_gate=`{play_gate}` "
                        "但卡面没有「You can use this card only if …」"
                        "（门槛静默消失 = 任何时机都能打）")

    # ⑥ passive:extra_attack（Festival Lead）
    if "festival grounds is in play" in d and "passive:extra_attack" not in tags:
        errs.append(f"{card_name} · {item_name}：卡面「If Festival Grounds is in "
                    "play, this Pokémon may use an attack it has twice」应落 "
                    "`passive:extra_attack`，实际无（续攻能力丢失）")
    return errs


# ---- 第六十三~六十七批（2026-11-04）：五族卡面 ↔ tag 独立复算 ----
# 正则刻意**重写一遍**，不 import 解析包 —— 复用解析侧的判定等于橡皮图章。
_B63_CANT_ATTACK = re.compile(
    r"during your next turn,?\s+this pok[eé]mon can'?t "
    r"(?:use attacks|attack\b)", re.I)
_B63_NAMED_LOCK = re.compile(
    r"during your next turn,?\s+this pok[eé]mon can'?t use "
    r"(?!attacks\b)(?!that attack\b)[a-z][a-z'\- ]{1,30}", re.I)
_B64_OPP_CHOOSE = re.compile(
    r"your opponent chooses (\d+) cards? from their hand and shuffles "
    r"those cards into their deck", re.I)
_B65_WR = re.compile(
    r"this attack's damage isn't affected by weakness or resistance,?\s*"
    r"or by any effects on", re.I)
_B65_NARROW = re.compile(
    r"this attack's damage isn't affected by any effects on", re.I)
_B66_KOFU = re.compile(
    r"attacks used by this pok[eé]mon cost \{c\} less for each kofu card "
    r"in your discard pile", re.I)
_B67_MOVE = re.compile(
    r"move an? energy from (?P<src>[^.]{0,70}?)\s+to\s+"
    r"(?P<dst>[^.]{0,70})", re.I)


def audit_batch63_67(card_name: str, item_name: str, desc: str,
                     effects: list) -> list:
    """第六十三~六十七批五族对账（**两侧都查**：缺 tag 与口径写反）

    ① `attack_lock:all:turn`：自身跨回合禁攻（`can't attack` 与
       `can't use attacks` 是同一语义的两种动词）；指定招式句式落 `all`
       会**扩大**封锁范围，必须抓出。
    ② `shuffle_opp_hand:N`：对手自选 ⇒ 引擎随机；N 必须与卡面一致。
    ③ `ignore_effects` / `ignore_effects:wr` **反口径**：写了 Weakness or
       Resistance 就必须带 `:wr`（否则弱抗那一半静默漏掉）；只写「any
       effects」却带 `:wr` 是静默加强。
    ④ `own_discard_named:kofu`：按弃牌堆 Kofu 卡数减费；落成固定值/别的
       计数源都会静默改变费用。
    ⑤ `energy_move:1:from:<src>:to:<dst>`：新补的三种区域别名；阵营写反
       （`opp_bench` 顶替 `bench`）必须抓出。
    """
    errs: list = []
    d = _norm(PAREN.sub(" ", desc or ""))
    tags = [str(e) for e in _expand_compound(effects)]

    # ① 自身跨回合禁攻
    if _B63_CANT_ATTACK.search(d) and not _B63_NAMED_LOCK.search(d):
        if "attack_lock:all:turn" not in tags:
            errs.append(f"{card_name} · {item_name}：卡面「During your next "
                        "turn, this Pokémon can't attack / use attacks」应为 "
                        "`attack_lock:all:turn`，实际 "
                        f"{[t for t in tags if t.startswith('attack_lock')] or '无'}"
                        "（封锁整段消失）")
    if _B63_NAMED_LOCK.search(d) and "attack_lock:all:turn" in tags:
        errs.append(f"{card_name} · {item_name}：卡面只锁**指定招式**却落 "
                    "`attack_lock:all:turn`（全部招式被封 = 静默加强），"
                    "应为 `attack_lock:name:turn:<招式>`")

    # ② 对手自选洗回牌库
    m = _B64_OPP_CHOOSE.search(d)
    if m:
        want = f"shuffle_opp_hand:{int(m.group(1))}"
        if want not in tags:
            have = [t for t in tags if t.startswith("shuffle_opp_hand")]
            hint = ""
            if have:
                hint = "（张数与卡面不符）"
            errs.append(f"{card_name} · {item_name}：卡面「Your opponent "
                        f"chooses {m.group(1)} cards … shuffles … into their "
                        f"deck」应为 `{want}`，实际 {have or '无'}{hint}")

    # ③ 无视效果的两档**反口径**
    wr_have = "ignore_effects:wr" in tags
    narrow_have = "ignore_effects" in tags
    if _B65_WR.search(d):
        if not wr_have:
            hint = "（疑似用了窄口径 `ignore_effects`：弱抗那一半会静默漏掉）" \
                if narrow_have else ""
            errs.append(f"{card_name} · {item_name}：卡面写了「Weakness or "
                        "Resistance, or by any effects」应为 "
                        f"`ignore_effects:wr`，实际 "
                        f"{[t for t in tags if t.startswith('ignore_effects')] or '无'}"
                        f"{hint}")
    elif _B65_NARROW.search(d):
        if wr_have:
            errs.append(f"{card_name} · {item_name}：卡面只写「any effects」"
                        "却落 `ignore_effects:wr`（连弱抗一起无视 = 静默加强）")
    elif wr_have:
        errs.append(f"{card_name} · {item_name}：卡面无「无视弱点/抗性」句却带 "
                    "`ignore_effects:wr`（认领越界）")

    # ④ Kofu 计数源减费
    kofu_tag = "fx:attack_cost:self:less:per:own_discard_named:kofu"
    if _B66_KOFU.search(d):
        if kofu_tag not in tags:
            have = [t for t in tags if t.startswith("fx:attack_cost:self")]
            errs.append(f"{card_name} · {item_name}：卡面「cost {{C}} less for "
                        "each Kofu card in your discard pile」应为 "
                        f"`{kofu_tag}`，实际 {have or '无'}")
    elif kofu_tag in tags:
        errs.append(f"{card_name} · {item_name}：卡面无 Kofu 计数源句却带 "
                    f"`{kofu_tag}`（认领越界）")

    # ⑤ 能量移动区域别名（只对本轮新认领的三种别名复算，不误伤旧句式）
    mm = _B67_MOVE.search(d)
    if mm:
        src, dst = mm.group("src"), mm.group("dst")
        want_src = want_dst = None
        if "new benched pok" in dst or "your benched pok" in dst:
            want_dst = "bench"
        elif "this pok" in dst:
            want_dst = "active"
        if "other pok" in src:
            want_src = "any"
        elif "this pok" in src:
            want_src = "active"
        if want_src and want_dst:
            want = f"energy_move:1:from:{want_src}:to:{want_dst}"
            if want not in tags:
                have = [t for t in tags if t.startswith("energy_move:")]
                errs.append(f"{card_name} · {item_name}：卡面能量移动区域应为 "
                            f"`{want}`，实际 {have or '无'}"
                            "（阵营/战斗位写反会搬错能量）")
    return errs


# ---- 第六十八~七十七批（2026-11-04）：五族卡面 ↔ tag 独立复算 ----
# 正则刻意**重写一遍**，不 import 解析包 —— 复用解析侧的判定等于橡皮图章。
_B68_EACH_PLAYER = re.compile(
    r"each player shuffles their hand into their deck and draws (\d+) "
    r"cards", re.I)
_B69_HP_PLUS = re.compile(
    r"the (?:(?P<faction>[a-z][a-z' ]{0,22}?)'s )?pok[eé]mon this card is "
    r"attached to gets \+(?P<n>\d+)\s*hp", re.I)
_B70_RECYCLE = re.compile(
    r"shuffle up to (\d+) basic energy cards from your discard pile into "
    r"your deck", re.I)
_B71_CTR_DEFENDING = re.compile(
    r"put (\d+) damage counters? on the defending pok[eé]mon", re.I)
_B72_CTR_THIS = re.compile(
    r"put (\d+) damage counters? on this pok[eé]mon", re.I)


def audit_batch68_77(card_name: str, item_name: str, desc: str,
                     effects: list) -> list:
    """第六十八~七十七批五族对账（独立复算，两侧都查：缺 tag 与口径写反）

    ① `shuffle_draw_split:N:N`：双方各洗回手牌各抽 N（Judge）——派发必须在
       `shuffle_draw:` 之前，否则会被吞成单方洗抽。
    ② `tool:hp_plus:N`：道具 +HP（Cynthia's Power Weight）——纯加值，不得被
       「卡名/阵营限定 → 整卡放弃」守卫误杀（此前 4 张 +HP 道具整卡消失）。
    ③ `energy_to_deck:any:N`：弃牌堆基本能量洗回牌库（Energy Recycler，训练家侧）
       —— 复用招式侧 Kyogre 的 `_energy_to_deck`；白名单/派发漏接即卡不可执行。
    ④ `counters:N`：指示物直放 the Defending Pokémon（Glaceon）——指示物不吃
       弱点/抗性，误走 damage/snipe 会静默多算弱抗。
    ⑤ `counters:N:scope:self`：指示物直放 this Pokémon（Feraligatr）——放到
       自己战斗位，落成对手口径是阵营反转。
    """
    errs: list = []
    d = _norm(PAREN.sub(" ", desc or ""))
    tags = [str(e) for e in _expand_compound(effects)]

    m = _B68_EACH_PLAYER.search(d)
    if m:
        want = f"shuffle_draw_split:{int(m.group(1))}:{int(m.group(1))}"
        if want not in tags:
            errs.append(f"{card_name} · {item_name}：卡面「Each player shuffles "
                        f"… draws {m.group(1)}」应为 `{want}`，实际 "
                        f"{[t for t in tags if 'shuffle_draw' in t] or '无'}")

    # ② tool:hp_plus:N —— 阵营限定**必须带 `holder:name:` 限定**
    #    （第一百三十四批：引擎已补 `_tool_hp_plus()` 持有者判定；此前是
    #     「无归属元数据 → 整卡不得落」的硬拒收，现在放通**带限定**的形态，
    #     但**仍然拒绝**无条件形态 —— 那会给任意持有者加血（静默加强）。）
    m = _B69_HP_PLUS.search(d)
    if m:
        n = int(m.group("n"))
        has = any("tool:hp_plus" in t for t in tags)
        faction = m.group("faction")
        _qualified = any(
            t.startswith(f"tool:hp_plus:{n}:holder:name:")
            for t in tags)
        if faction and has and not _qualified:
            errs.append(f"{card_name} · {item_name}：卡面是**阵营限定** +HP"
                        f"（the {faction}'s Pokémon），落 `tool:hp_plus` 却不带 "
                        f"`holder:name:` → 会给任意持有者加血（静默加强）")
        elif faction and not any("tool:hp_plus" in t for t in tags):
            errs.append(f"{card_name} · {item_name}：卡面「the {faction}'s "
                        f"Pokémon … gets +{n} HP」应为 "
                        f"`tool:hp_plus:{n}:holder:name:"
                        f"{re.sub(r'[^a-z0-9]', '', str(faction).lower())}`，"
                        f"实际 {[t for t in tags if 'hp_plus' in t] or '无'}")
        elif not faction and not has:
            errs.append(f"{card_name} · {item_name}：卡面「… gets +{n} HP」应为 "
                        f"`tool:hp_plus:{n}`，实际 "
                        f"{[t for t in tags if 'hp_plus' in t] or '无'}")

    m = _B70_RECYCLE.search(d)
    if m:
        want = f"energy_to_deck:any:{int(m.group(1))}"
        if want not in tags:
            errs.append(f"{card_name} · {item_name}：卡面「Shuffle up to "
                        f"{m.group(1)} Basic Energy … into your deck」应为 "
                        f"`{want}`，实际 "
                        f"{[t for t in tags if 'energy_to_deck' in t] or '无'}")

    m = _B71_CTR_DEFENDING.search(d)
    if m:
        want = f"counters:{int(m.group(1))}"
        if want not in tags:
            wrong = [t for t in tags
                     if t.startswith(("self:", "snipe:", "damage", "counters"))]
            errs.append(f"{card_name} · {item_name}：卡面「put {m.group(1)} "
                        f"damage counters on the Defending Pokémon」应为 "
                        f"`{want}`，实际 {wrong or '无'}"
                        "（指示物误走 damage/snipe 会静默吃弱抗）")

    # ⑤ counters:N:scope:self —— **复合句必须整条拒绝**（后半段 B4 未建模）
    m = _B72_CTR_THIS.search(d)
    if m:
        n = int(m.group(1))
        # 允许子串命中（Venture Bomb 的 tails 分支把 `counters:N:scope:self`
        # 嵌在 `coin_flip:…|tails:…` 里，不是独立 tag）。
        has = any(f"counters:{n}:scope:self" in t for t in tags)
        _rest = d[m.end():]
        compound = bool(re.search(
            r"\bif you do\b|during this turn|\bthen\b|this attack does", _rest))
        # 第九十五批起「+N 伤害 / 按放置数重算」有精确承载：
        # `self_dmg_bonus:N`（回合级加伤）→ 不再算部分实现。
        if compound and has and not any(
                str(t).startswith("self_dmg_bonus:") for t in tags):
            errs.append(f"{card_name} · {item_name}：卡面「put {n} damage "
                        "counters on this Pokémon」后接未建模从句（+120 / 按放置"
                        "数重算）→ 只映射指示物属「部分实现冒充已实现」，应整条" 
                        "放弃")
        elif not compound and not has:
            wrong = [t for t in tags
                     if "counters" in t
                     or t.startswith(("self:", "snipe:", "damage"))]
            errs.append(f"{card_name} · {item_name}：卡面「put {n} damage "
                        f"counters on this Pokémon」应为 `counters:{n}:scope:self`"
                        f"，实际 {wrong or '无'}（放自己战斗位却落对手口径 = 阵营反转）")
    return errs


_B79_ROTO = re.compile(
    r"look at the top (\d+) cards? of your deck. you may reveal any number of "
    r"supporter cards you find there and put them into your hand", re.I)
_B79_SECRET = re.compile(
    r"search your deck for an? item card, an? pok[eé]mon tool card, an? supporter "
    r"card, and an? stadium card", re.I)
_B79_TROLLEY = re.compile(
    r"search your deck for any number of basic pok[eé]mon and put them onto "
    r"your bench", re.I)
_B79_TOP = re.compile(
    r"search your deck for (\d+) cards?[^.]*?then put those cards on top of "
    r"(?:it|your deck)", re.I)
_B79_BOTTOM = re.compile(
    r"then put (?:those cards|them) on (?:the )?bottom of", re.I)


def audit_batch79(card_name: str, item_name: str, desc: str,
                  effects: list) -> list:
    """第七十九批搜索族对账（张数 / 去向 / 类别逐项独立复算）

    ① Roto-Stick：「top N 里任意数量 Supporter → 入手」必须
       `recon:N:all:supporter`。落成 `recon:N`（旧口径随机 1 张）会少拿。
    ② Secret Box：四类别**各 1 张** → `search_multi:item+tool+supporter+stadium:1`；
       落成 `search_trainer:1` 是「张数 4→1、类别任一」双错。
    ③ Precious Trolley：任意数量基础宝可梦铺后备 → `search_bench:all:basic`；
       落成 `search_bench:1` 会少铺。
    ④ 检索后放牌库顶 → `search_top:N`；落成 `search_any:N` 会凭空多 N 张手牌。
       反向守卫：「放牌库底」不得落 `search_top`（方向相反）。
    """
    errs: list = []
    d = _norm(desc or "")
    tags = [str(e) for e in _expand_compound(effects)]

    m = _B79_ROTO.search(d)
    if m:
        want = f"recon:{int(m.group(1))}:all:supporter"
        if want not in tags:
            errs.append(f"{card_name} · {item_name}：卡面「top {m.group(1)} 任意"
                        f"数量 Supporter 入手」应为 `{want}`，实际 "
                        f"{[t for t in tags if t.startswith('recon:')] or '无'}")

    m = _B79_SECRET.search(d)
    if m:
        want = "search_multi:item+tool+supporter+stadium:1"
        if want not in tags:
            errs.append(f"{card_name} · {item_name}：卡面「四类别各取 1 张」应为 "
                        f"`{want}`，实际 "
                        f"{[t for t in tags if t.startswith('search')] or '无'}"
                        "（退化成任一类别 = 静默少拿 3 张）")

    m = _B79_TROLLEY.search(d)
    if m:
        want = "search_bench:all:basic"
        if want not in tags:
            errs.append(f"{card_name} · {item_name}：卡面「任意数量基础宝可梦铺"
                        f"后备」应为 `{want}`，实际 "
                        f"{[t for t in tags if t.startswith('search')] or '无'}")

    m = _B79_TOP.search(d)
    if m:
        want = f"search_top:{int(m.group(1))}"
        if want not in tags:
            errs.append(f"{card_name} · {item_name}：卡面「检索 {m.group(1)} 张放回"
                        f"牌库顶」应为 `{want}`，实际 "
                        f"{[t for t in tags if t.startswith('search')] or '无'}"
                        "（落成入手 = 静默多抽）")
    if _B79_BOTTOM.search(d) and any(t.startswith("search_top:") for t in tags):
        errs.append(f"{card_name} · {item_name}：卡面是「放牌库**底**」却落成 "
                    "`search_top:`（方向相反）")
    return errs


# ---- 第八十~八十四批对账（卡面 ↔ tag，正则独立实现） --------------------
_B80_FULL_HP = re.compile(
    r"has\s+full\s+hp\s+and\s+would\s+be\s+knocked\s+out\s+by\s+damage\s+from\s+"
    r"an\s+attack,\s*it\s+is\s+not\s+knocked\s+out,\s*and\s+its\s+remaining\s+"
    r"hp\s+becomes\s+(\d+)", re.I)
_B81_CURE = re.compile(
    r"this\s+pok[eé]mon\s+recovers\s+from\s+all\s+special\s+conditions", re.I)
_B82_PTD = re.compile(
    r"shuffle\s+up\s+to\s+(\d+)\s+pok[eé]mon\s+from\s+your\s+discard\s+pile\s+"
    r"into\s+your\s+deck", re.I)
_B82_ALL = re.compile(
    r"shuffle\s+all\s+pok[eé]mon\s+from\s+your\s+discard\s+pile", re.I)
_B83_SD = re.compile(
    r"search\s+your\s+deck\s+for\s+up\s+to\s+(\d+)\s+cards?\s+and\s+discard\s+"
    r"them", re.I)
_B83_HAND = re.compile(
    r"search\s+your\s+deck\s+for\s+up\s+to\s+\d+\s+cards?\s+and\s+put\s+"
    r"them\s+into\s+your\s+hand", re.I)
_B84_CTH = re.compile(
    r"(?:put|place)\s+damage\s+counters\s+on\s+"
    r"(each\s+of\s+your\s+opponent's\s+benched\s+pok[eé]mon|"
    r"your\s+opponent's\s+active\s+pok[eé]mon)\s+until\s+its\s+remaining\s+"
    r"hp\s+is\s+(\d+)", re.I)
_B84_FIXED = re.compile(
    r"(?:put|place)\s+(\d+)\s+damage\s+counters\s+on", re.I)


def audit_batch80_84(card_name: str, item_name: str, desc: str,
                     effects: list) -> list:
    """第八十~八十四批对账：**反口径守卫**比「有 tag」更重要

    ① 满 HP 免击倒 → `passive:survive:N`，写成 `passive:coin_survive` 是
       把「必定生效且要求满血」变成「抛币且不要求满血」（反向冒充）。
    ② 清除自身状态 → `cure:self`。
    ③ 弃牌堆宝可梦洗回 → `pokemon_to_deck:N`，写成 `energy_to_deck` 是换来源；
       「shuffle all …」本批明确拒收，落 `pokemon_to_deck` 就是静默缩小。
    ④ 检索后弃置 → `search_discard:N`，写成 `search_any` 是凭空多 N 张手牌；
       反向：入手句式不得落 `search_discard`。
    ⑤ 指示物打到剩余 HP → `counters_to_hp:N:<scope>`，写成固定 `counters:M`
       会随目标 HP 不同而错算；反向：固定个数句式不得落 `counters_to_hp`。
    """
    errs: list = []
    d = _norm(desc or "")
    tags = [str(e) for e in _expand_compound(effects)]

    m = _B80_FULL_HP.search(d)
    if m:
        want = f"passive:survive:{int(m.group(1))}"
        if want not in tags:
            errs.append(f"{card_name} · {item_name}：卡面「满 HP 免击倒」应为 "
                        f"`{want}`，实际 {tags}")
        if any(t.startswith("passive:coin_survive:") for t in tags):
            errs.append(f"{card_name} · {item_name}：满 HP 免击倒被写成抛币版 "
                        "`passive:coin_survive`（口径相反）")

    if _B81_CURE.search(d) and "cure:self" not in tags:
        errs.append(f"{card_name} · {item_name}：卡面「清除自身全部特殊状态」"
                    "应有 `cure:self`，实际 " + str(tags))

    m = _B82_PTD.search(d)
    if m:
        want = f"pokemon_to_deck:{int(m.group(1))}"
        if want not in tags:
            errs.append(f"{card_name} · {item_name}：卡面「弃牌堆至多 "
                        f"{m.group(1)} 张宝可梦洗回」应为 `{want}`，实际 {tags}")
        if any(t.startswith("energy_to_deck:") for t in tags):
            errs.append(f"{card_name} · {item_name}：宝可梦洗回被写成能量洗回 "
                        "`energy_to_deck`（来源不同）")
    if _B82_ALL.search(d) and any(t.startswith("pokemon_to_deck:") for t in tags):
        errs.append(f"{card_name} · {item_name}：卡面是「shuffle all Pokémon」，"
                    "本批只认 `up to N` → 不得落 `pokemon_to_deck`")

    m = _B83_SD.search(d)
    if m:
        want = f"search_discard:{int(m.group(1))}"
        if want not in tags:
            errs.append(f"{card_name} · {item_name}：卡面「检索 {m.group(1)} "
                        f"张并弃置」应为 `{want}`，实际 {tags}"
                        "（落成入手 = 凭空多牌）")
        if any(t.startswith(("search_any:", "search_top:", "search_pokemon:"))
               for t in tags):
            errs.append(f"{card_name} · {item_name}：检索弃置被写成入手/放顶 "
                        "（去向相反）")
    if _B83_HAND.search(d) and any(t.startswith("search_discard:") for t in tags):
        errs.append(f"{card_name} · {item_name}：卡面是「入手」却落 "
                    "`search_discard`（去向相反）")

    m = _B84_CTH.search(d)
    if m:
        scope = "opp_bench" if "benched" in m.group(1) else "opp_active"
        want = f"counters_to_hp:{int(m.group(2))}:{scope}"
        if want not in tags:
            errs.append(f"{card_name} · {item_name}：卡面「放到剩余 HP "
                        f"{m.group(2)}」应为 `{want}`，实际 {tags}")
        if any(t.startswith("counters:") for t in tags):
            errs.append(f"{card_name} · {item_name}：按剩余 HP 放置被写成固定"
                        "个数 `counters:`（逐只差额会错算）")
    if _B84_FIXED.search(d) and any(t.startswith("counters_to_hp:")
                                    for t in tags):
        errs.append(f"{card_name} · {item_name}：卡面是「放固定个指示物」却落 "
                    "`counters_to_hp`（口径不同）")
    return errs


_B85_KPM = re.compile(
    r"knocked out by damage from an attack from your opponent'?s pok[eé]mon"
    r"(?:, and if you have any .+? in play,)?,? "
    r"(?:your opponent|that player) "
    r"(?:takes? 1 fewer prize cards?|can'?t take any prize cards?)", re.I)


def audit_batch85(card_name: str, item_name: str, desc: str,
                  effects: list) -> list:
    """第八十五批对账：对手宝可梦被击倒→少拿奖赏，必须产 `ko_prize_minus:*`

    反向守卫：卡面命中「by damage from an attack from your opponent's Pokémon …
    that player takes 1 fewer Prize card」句式，却落 `unsupported`（或别的 tag）
    就是静默丢效果。引擎 `_ko_prize_reduction` 的 tool 形态已承载
    `ko_prize_minus:1`（Lillie's Pearl）。
    """
    errs: list = []
    d = _norm(desc or "")
    tags = [str(e) for e in _expand_compound(effects)]
    if _B85_KPM.search(d) and not any(t.startswith("ko_prize_minus:")
                                      for t in tags):
        errs.append(f"{card_name} · {item_name}：卡面「对手宝可梦被击倒→少拿"
                    f"奖赏」应产 `ko_prize_minus:*` tag，实际 {tags}")
    return errs


_B89_PB = re.compile(
    r"during this turn, if your opponent'?s active pok[eé]mon is knocked out by "
    r"damage from an attack used by your (tera|n'?s) pok[eé]mon, take (\d+) "
    r"more prize card", re.I)


def audit_batch89(card_name: str, item_name: str, desc: str,
                  effects: list) -> list:
    """第八十九批对账：训练家「本回合多拿奖赏」→ `prize_bonus:N:<scope>`

    卡面：「During this turn, if your opponent's Active Pokémon is Knocked Out by
    damage from an attack used by your (Tera|N's) Pokémon, take N more Prize
    card.」（Briar / Anthea & Concordia）。落后即整卡丢效果（少拿奖赏）。
    """
    errs: list = []
    d = _norm(desc or "")
    tags = [str(e) for e in _expand_compound(effects)]
    m = _B89_PB.search(d)
    if not m:
        return errs
    scope = "tera" if m.group(1).lower() == "tera" else "nteam"
    want = f"prize_bonus:{int(m.group(2))}:{scope}"
    if want not in tags:
        errs.append(f"{card_name} · {item_name}：卡面「本回合多拿奖赏」应产 "
                    f"`{want}`，实际 {tags}")
    return errs


_B93_EXV = re.compile(
    r"opponent'?s active pok[eé]mon is a pok[eé]mon \{?\s*ex\s*\}?\s*or\s*"
    r"pok[eé]mon \{?\s*v\s*\}?", re.I)
_B93_SUP = re.compile(
    r"played a supporter card that has [\"“]([^\"”]{2,24})[\"”] in its name from "
    r"your hand during this turn", re.I)
_B93_PAN = re.compile(
    r"if any of your benched ([a-z][a-z' ]{2,24}?) have any damage counters on them",
    re.I)
_B94_EXV = re.compile(
    r"for each of your opponent'?s pok[eé]mon \{?\s*ex\s*\}?(?:\s*and\s*pok[eé]mon "
    r"\{?\s*v\s*\})?\s*in play", re.I)
_B94_TOOL = re.compile(
    r"for each pok[eé]mon tool attached to all pok[eé]mon", re.I)


def audit_batch93_94(card_name: str, item_name: str, desc: str,
                     effects: list) -> list:
    """第九十三/九十四批对账：条件型 / 计数源型伤害缩放

    这五条句式此前一律落 `unsupported`（整卡被踢出池）。引擎侧本来就缺的是
    **短语映射**，条件与计数源早就在 `_CONDITIONS` / `_COUNTERS` 里
    （`opp_active_is_ex` / `opp_ex` / `both_field_tools`），本批新增了两个
    显式命名的变体（`:ex_or_v`、`played_supporter_named:`、
    `own_bench_named_has_damage:`）。对账在这里钉住「卡面有这句 → 必须产对应 tag」，
    否则就是静默丢效果（伤害少算，且无从察觉）。
    """
    errs: list = []
    d = _norm(desc or "")
    tags = [str(e) for e in _expand_compound(effects)]
    if _B93_EXV.search(d) and not any("opp_active_is_ex_or_v" in t for t in tags):
        errs.append(f"{card_name} · {item_name}：卡面「对手战斗位是 ex/V → 加伤」"
                    f"应产 `dmg_plus:*:if:opp_active_is_ex_or_v`，实际 {tags}")
    m = _B93_SUP.search(d)
    if m:
        key = re.sub(r"[^a-z0-9]", "", m.group(1).lower())
        if not any(f"played_supporter_named:{key}" in t for t in tags):
            errs.append(f"{card_name} · {item_name}：卡面「本回合打过名字含"
                        f"「{m.group(1)}」的支援者 → 加伤」应产 "
                        f"`dmg_plus:*:if:played_supporter_named:{key}`，实际 {tags}")
    m = _B93_PAN.search(d)
    if m:
        key = re.sub(r"[^a-z0-9]", "", m.group(1).lower())
        if not any(f"own_bench_named_has_damage:{key}" in t for t in tags):
            errs.append(f"{card_name} · {item_name}：卡面「后备 {m.group(1)} 有指示物"
                        f" → 加伤」应产 `dmg_plus:*:if:own_bench_named_has_damage:"
                        f"{key}`，实际 {tags}")
    if _B94_EXV.search(d):
        # 「{ex} and {V}」→ 独立命名的 `opp_ex_or_v`；只写 {ex} → 既有 `opp_ex`。
        # 两者引擎侧当前同值，但**不可混用**：混用会让将来加 V 支持时，只写 {ex}
        # 的卡跟着变错（对账在这里钉住命名，而不是只钉数值）。
        want = "opp_ex_or_v" if re.search(r"\{v\}", d, re.I) else "opp_ex"
        if not any(want in t for t in tags):
            errs.append(f"{card_name} · {item_name}：卡面「按对手 ex/V 只数缩放」应产 "
                        f"`dmg_set:*:per:{want}`，实际 {tags}")
    if _B94_TOOL.search(d) and not any("both_field_tools" in t for t in tags):
        errs.append(f"{card_name} · {item_name}：卡面「按场上道具总数缩放」应产 "
                    f"`dmg_set:*:per:both_field_tools`，实际 {tags}")
    return errs


# ---------------- S3（2026-11-08）：奖赏卡 / 牌库底 / 对手牌库 ----------------
# 独立实现（不 import 解析包）：对账的意义是「用另一套写法重算一遍」。
_S3_REDEAL = re.compile(
    r"count your prize cards.{0,30}?put them on the bottom of your deck", re.I)
_S3_FLIP = re.compile(
    r"turn 1 of your opponent[’']?s face-down prize cards? face up", re.I)
_S3_SWITCH = re.compile(
    r"you may have your opponent switch those cards", re.I)
_S3_FLUTE = re.compile(
    r"reveal the top (\d+) cards of your opponent[’']?s deck", re.I)
_S3_FLUTE_BENCH = re.compile(
    r"any number of basic pok[eé]mon.{0,60}?onto their bench", re.I)
_S3_PEEK = re.compile(
    r"look at 1 of your opponent[’']?s face-down prize cards?", re.I)
_S3_BOTTOM_SELF = re.compile(
    r"discard the bottom card of your deck", re.I)
_S3_SELF_TOP = re.compile(
    r"discard all cards from this pok[eé]mon and put this pok[eé]mon on top of "
    r"your deck", re.I)


def audit_s3_zones(card_name: str, item_name: str, desc: str,
                   effects: list) -> list:
    """S3 对账：奖赏卡对象化 / 牌库底 / 对手牌库

    这一族最容易出的错是**半句认领**：卡面有「弃牌库底」，但后半句
    「并把本只放回牌库顶」被漏掉 —— 那样卡片会把牌弃掉却留在场上（净亏一张，
    对局里看不出来）。所以这里要求两半**同时**出现在 tag 里。
    """
    errs: list = []
    d = _norm(desc or "")
    tags = [str(e) for e in _expand_compound(effects)]
    joined = "|".join(tags)
    if _S3_REDEAL.search(d) and not any(t.startswith("prize:redeal")
                                       for t in tags):
        errs.append(f"{card_name} · {item_name}：卡面「奖赏卡洗回牌库底后重发」"
                    f"应产 `prize:redeal`，实际 {tags}")
    if _S3_FLIP.search(d):
        if not any(t.startswith("prize:flip_opp") for t in tags):
            errs.append(f"{card_name} · {item_name}：卡面「翻面对手奖赏卡」"
                        f"应产 `prize:flip_opp`，实际 {tags}")
        # 「You may have your opponent switch those cards.」是可选项，
        # 落成第二个 confirm 选择；漏掉它 = 少一个效果分支。
        if _S3_SWITCH.search(d) and not any(
                t.startswith("prize:flip_opp") for t in tags):
            errs.append(f"{card_name} · {item_name}：卡面「可交换这两张」"
                        f"必须并入 `prize:flip_opp`（引擎内置确认选择），"
                        f"实际 {tags}")
    m = _S3_FLUTE.search(d)
    if m and _S3_FLUTE_BENCH.search(d):
        want = f"flute:{int(m.group(1))}"
        if not any(t.startswith("flute:") for t in tags):
            errs.append(f"{card_name} · {item_name}：卡面「亮出对手牌库顶 "
                        f"{m.group(1)} 张，任意数量基础宝可梦上其后备」应产 "
                        f"`{want}`，实际 {tags}")
        elif want not in tags:
            # 张数错了等于看了不同数量的牌（信息量与可选范围都变）
            errs.append(f"{card_name} · {item_name}：`flute:` 张数与卡面不符"
                        f"（应 {want}），实际 {tags}")
    if _S3_PEEK.search(d) and not any("info:peek_prize" in t for t in tags):
        errs.append(f"{card_name} · {item_name}：卡面「查看对手 1 张背面奖赏卡」"
                    f"应产 `info:peek_prize:opp:1`（完美信息等价，需有日志），"
                    f"实际 {tags}")
    if _S3_BOTTOM_SELF.search(d):
        # 两半必须同一条复合 tag 里都出现
        if "deck_bottom_discard" not in joined:
            errs.append(f"{card_name} · {item_name}：卡面「弃牌库底 1 张」应产 "
                        f"`deck_bottom_discard:1`，实际 {tags}")
        if _S3_SELF_TOP.search(d) and "self_to_deck_top" not in joined:
            errs.append(f"{card_name} · {item_name}：卡面「弃掉本只所有卡并放回"
                        f"牌库顶」应产 `self_to_deck_top`（与前半句同一条复合 tag），"
                        f"实际 {tags}")
    return errs


# ---------------- S2（2026-11-08）：延迟队列 / 事件订阅 ----------------
# 独立实现（不 import 解析包）。
_S2_DELAY = re.compile(
    r"at the end of this turn,? if you have (\d+) or more cards in your hand,? "
    r"discard your hand", re.I)
_S2_WATCH = re.compile(
    r"whenever your opponent[’']?s active pok[eé]mon moves to the bench during "
    r"their turn,? (?:place|put) (\d+) damage counters? on that pok[eé]mon", re.I)


_S2_EVOLVE_WATCH = re.compile(
    r"whenever your opponent plays? a pok[eé]mon from .{0,12}hand to evolve 1 of "
    r".{0,12}pok[eé]mon,? (?:put|place) (\d+) damage counters? on that "
    r"pok[eé]mon", re.I)
_S2_SWIRL = re.compile(
    r"as long as this pok[eé]mon is in the active spot,? whenever your "
    r"opponent[’']?s active pok[eé]mon moves to the bench during their turn,? "
    r"their new active pok[eé]mon is now confused", re.I)


_S2C_KO_SEARCH = re.compile(
    r"is knocked out by damage from an attack from your opponent[’']?s "
    r"pok[eé]mon,? search your deck for up to (\d+) cards?", re.I)
_S2C_TURN_END_ATTACH = re.compile(
    r"at the end of your turn \(after your attack\),? if the pok[eé]mon this "
    r"card is attached to is in the active spot,? you may attach a basic energy",
    re.I)
_S2C_DELAY_DISCARD = re.compile(
    r"at the end of your opponent[’']?s next turn,? discard the defending "
    r"pok[eé]mon and all attached cards", re.I)


# ---------------- S2d（2026-11-08）：限时订阅 / 撤退费条件 / 具名能量 ---
# 独立实现（不 import 解析包）。
_S2D_ARM = re.compile(
    r"whenever they attach an energy card from their hand to the defending "
    r"pok[eé]mon,? (?:place|put) (\d+) damage counters? on that pok[eé]mon", re.I)
_S2D_BATON = re.compile(
    r"has a retreat cost of exactly (\d+),? is in the active spot,? and is "
    r"knocked out by damage from an attack .{0,40}?move up to (\d+) basic energy",
    re.I)
_S2D_NAMED = re.compile(
    r"you may attach up to (\d+) ((?:[a-z][a-z'\-]* )+?)energy cards? from your "
    r"discard pile to this pok[eé]mon", re.I)


def audit_s2d_events(card_name: str, item_name: str, desc: str,
                     effects: list) -> list:
    """S2d 对账：限时订阅 / 撤退费+战斗位 / 具名能量附着

    这三族的错法都是「**丢了限定词**」：
      · 限时订阅丢了窗口 → 变成**永久**订阅（每附一张能量就打 80，整局）；
      · 丢了 `match:event_target` → 对手附给**任何**宝可梦都打 80；
      · Heavy Baton 丢了 `require:retreat:N` → 任何宝可梦被击倒都搬能量；
      · `attach_named` 丢了卡名（退化成按属性）→ 拿错卡。
    所以这里逐条要求限定词在场，而不是只看族前缀。
    """
    errs: list = []
    d = _norm(desc or "")
    tags = [str(e) for e in _expand_compound(effects)]
    joined = "|".join(tags)
    m = _S2D_ARM.search(d)
    if m:
        want = f"arm:attach_energy:counters:{int(m.group(1))}"
        if want not in joined:
            errs.append(f"{card_name} · {item_name}：卡面「对手下回合附能到防御"
                        f"宝可梦时放 {m.group(1)} 个指示物」应产 `{want}…`，"
                        f"实际 {tags}")
        elif ":until:" not in joined:
            errs.append(f"{card_name} · {item_name}：限时订阅缺 `:until:` —— "
                        f"会变成**永久**订阅（静默加强）")
        elif ":match:event_target" not in joined:
            errs.append(f"{card_name} · {item_name}：限时订阅缺 "
                        f"`match:event_target` —— 对手附给别的宝可梦也会触发")
    m = _S2D_BATON.search(d)
    if m:
        want = f"on_event:ko:move_energy_to_bench:{int(m.group(2))}"
        if want not in joined:
            errs.append(f"{card_name} · {item_name}：卡面「撤退费恰好 "
                        f"{m.group(1)} 且被击倒时搬 {m.group(2)} 张基本能量」应产 "
                        f"`{want}…`，实际 {tags}")
        elif f"require:retreat:{int(m.group(1))}" not in joined:
            errs.append(f"{card_name} · {item_name}：缺 `require:retreat:"
                        f"{int(m.group(1))}` —— 任何宝可梦被击倒都会搬能量")
        elif "require:was_active" not in joined:
            errs.append(f"{card_name} · {item_name}：缺 `require:was_active` —— "
                        f"卡面限定「在战斗位」，后备被击倒不该触发")
    m = _S2D_NAMED.search(d)
    if m:
        _en = " ".join(w.capitalize() for w in m.group(2).split())
        want = f"attach_named:{int(m.group(1))}:{_en} Energy"
        if want not in joined:
            errs.append(f"{card_name} · {item_name}：卡面「从弃牌堆附着 "
                        f"{m.group(1)} 张 {_en} Energy」应产 `{want}…`，"
                        f"实际 {tags}")
    return errs


# ---------------- S2e（2026-11-08）：可选触发 / 原地替换 / 只能由效果上场 ----
_S2E_PICK = re.compile(
    r"when your (?P<name>[a-z][a-z' \-]*?)(?: \{ex\}| ex)? moves from your bench "
    r"to the active spot,? you may use this ability\.? move any amount of "
    r"energy from your benched pok[eé]mon to your active pok[eé]mon", re.I)
_S2E_REPLACE = re.compile(
    r"when this pok[eé]mon moves from the active spot to the bench,? you may "
    r"search your deck for a (?P<name>[a-z][a-z' \-]*?)(?: \{ex\}| ex)? and "
    r"switch it with this pok[eé]mon\. any attached cards", re.I)
_S2E_ONLY_BY = re.compile(
    r"put this pok[eé]mon into play only with the effect of (?P<src>.+?) ability",
    re.I)


def audit_s2e_events(card_name: str, item_name: str, desc: str,
                     effects: list) -> list:
    """S2e 对账：可选触发 / 原地替换 / 只能由效果上场

    这三族的错法都是「**把选择权拿走了**」：
      · 丢了 `:may` → 引擎替玩家决定「用不用」（S1 的不变量被破坏）；
      · 丢了 `match:event_name:` → 任意宝可梦上场/下场都触发；
      · 丢了 `passive:no_evolve_into` → Palafin ex 可以从手牌直接进化上场，
        整条 Zero to Hero 的设计被绕过。
    """
    errs: list = []
    d = _norm(desc or "")
    tags = [str(e) for e in _expand_compound(effects)]
    joined = "|".join(tags)
    m = _S2E_PICK.search(d)
    if m:
        _en = " ".join(w.capitalize() for w in m.group("name").split())
        if " {ex}" in desc.lower() or " ex" in m.group(0).lower()[:200]:
            _en = f"{_en} ex" if not _en.lower().endswith(" ex") else _en
        want = "on_event:switch_in:pick_energy:bench:active"
        if want not in joined:
            errs.append(f"{card_name} · {item_name}：卡面「指定宝可梦上场时可选搬能量」"
                        f"应产 `{want}:…`，实际 {tags}")
        elif f"match:event_name:{_en}" not in joined:
            errs.append(f"{card_name} · {item_name}：缺 `match:event_name:{_en}` "
                        f"—— 任意宝可梦上场都会触发，实际 {tags}")
        elif ":may" not in joined:
            errs.append(f"{card_name} · {item_name}：缺 `:may` —— 卡面是"
                        f"「you may」，引擎不得替玩家决定")
    m = _S2E_REPLACE.search(d)
    if m:
        _en = " ".join(w.capitalize() for w in m.group("name").split())
        want = f"on_event:switch_out:replace_self:{_en}"
        if want not in joined:
            errs.append(f"{card_name} · {item_name}：卡面「本只下场时可选换成 "
                        f"{_en}」应产 `{want}:to_deck:…`，实际 {tags}")
        elif "match:event_leaving" not in joined:
            errs.append(f"{card_name} · {item_name}：缺 `match:event_leaving` "
                        f"—— 别的宝可梦下场也会触发")
        elif ":may" not in joined:
            errs.append(f"{card_name} · {item_name}：缺 `:may` —— 卡面是"
                        f"「you may」，引擎不得替玩家决定")
    if _S2E_ONLY_BY.search(d):
        if "passive:no_evolve_into" not in joined:
            errs.append(f"{card_name} · {item_name}：卡面「只能由指定效果上场」应产 "
                        f"`passive:no_evolve_into`，实际 {tags}")
    return errs


# ---------------- S4（2026-11-08）：效果免疫的团队范围 / 训练家来源 ----------------
_S4_TEAM_TR = re.compile(
    r"prevent all effects of attacks used by your opponent[’']?s pok[eé]mon "
    r"done to your basic team rocket[’']?s pok[eé]mon", re.I)
_S4_TRAINER = re.compile(
    r"whenever your opponent plays an? (item or supporter|supporter) card from "
    r"their hand,? prevent all effects of that card done to this pok[eé]mon",
    re.I)


def audit_s4_immune(card_name: str, item_name: str, desc: str,
                    effects: list) -> list:
    """S4 对账：效果免疫的范围与来源

    三处最容易「认了半句」的错法：
      · 团队范围落成 `:self` → 只有持有者自己免疫（少保护整队）；
      · 训练家子类别写宽（只写 Supporter 的卡落成 `item+supporter`）→
        对手的 Item 也被挡（静默加强）；
      · 训练家来源落成 `attack` → 完全不生效（免疫形同不存在）。
    """
    errs: list = []
    d = _norm(desc or "").replace("é", "e")
    tags = [str(e) for e in _expand_compound(effects)]
    joined = "|".join(tags)
    if _S4_TEAM_TR.search(d):
        want = "fx:effect_immune:attack:team:basic_teamrocket"
        if want not in joined:
            errs.append(f"{card_name} · {item_name}：卡面「我方基础火箭队宝可梦」"
                        f"应产 `{want}`，实际 {tags}")
    m = _S4_TRAINER.search(d)
    if m:
        kinds = "item+supporter" if m.group(1).startswith("item") else "supporter"
        want = f"fx:effect_immune:trainer:{kinds}:self"
        if want not in joined:
            errs.append(f"{card_name} · {item_name}：卡面「对手打出的 "
                        f"{m.group(1)}」应产 `{want}`，实际 {tags}")
    return errs


def audit_s5_borrow(card_name: str, item_name: str, desc: str,
                    effects: list) -> list:
    """S5 对账：抛币门借用 / 牌库顶借用 / 抛币分岔检索 / 放牌库底逐人抛币

    这四族的共同错法是「丢了限定词」——而且每个都**不会报错**：
      · 抛币门丢了币 → 变成「一定借用」（静默加强）；
      · 币写进来源里（`borrow_attack:opp_active:coin`）→ 来源永不匹配 = 死卡；
      · 牌库顶借用丢了张数（`opp_deck_top`）→ 无法定位翻开的牌 = 死卡；
      · 抛币分岔检索退化成 `search_pokemon:` → 少了 stage 与阵营两道限定；
      · 放牌库底错认成洗回牌库（`shuffle_both_coin_draw`）→ 牌库顺序完全不同。
    正则与解析包**各自独立实现**（不 import 同一个正则，否则一起错、永远全绿）。
    """
    errs: list = []
    d = _norm(desc or "")
    tags = [str(e) for e in _expand_compound(effects)]
    joined = "|".join(tags)

    m = _S5_BORROW_COIN.search(d)
    if m:
        want = "coin:heads:borrow_attack:opp_active"
        if want not in tags:
            errs.append(f"{card_name} · {item_name}：卡面「抛币正面才借用对手"
                        f"战斗位招式」应产 `{want}`，实际 {tags}")
    m = _S5_BORROW_DECK_TOP.search(d)
    if m:
        want = f"borrow_attack:opp_deck_top:{int(m.group(1))}"
        if want not in tags:
            errs.append(f"{card_name} · {item_name}：卡面「翻对手牌库顶 "
                        f"{int(m.group(1))} 张借招式」应产 `{want}`，实际 {tags}")
    m = _S5_COIN_SEARCH.search(d)
    if m:
        _k = _S5_CAT_KEY(m.group("cat"))
        want = f"coin_search:evolution:basic:category:{_k}"
        if want not in tags:
            errs.append(f"{card_name} · {item_name}：卡面「正面找进化 / 反面找"
                        f"基础」应产 `{want}`，实际 {tags}")
    m = _S5_BOTTOM_COIN_DRAW.search(d)
    if m:
        want = f"shuffle_both_bottom_coin_draw:{int(m.group(1))}:{int(m.group(2))}"
        if want not in tags:
            errs.append(f"{card_name} · {item_name}：卡面「手牌放牌库底 + 每人各抛"
                        f"一枚币」应产 `{want}`，实际 {tags}")
    return errs


# S5 的四条独立正则（刻意不复用解析包的实现）
_S5_BORROW_COIN = re.compile(
    r"flip a coin\. if heads, choose 1 of your opponent's active "
    r"pok[eé]mon's attacks? and use it as this attack", re.I)
_S5_BORROW_DECK_TOP = re.compile(
    r"reveal the top (\d+) cards of your opponent's deck\. you may choose an "
    r"attack from a pok[eé]mon you find there", re.I)
_S5_COIN_SEARCH = re.compile(
    r"flip a coin\. if heads, search your deck for an? evolution "
    r"(?P<cat>[a-z'.\- ]+?) pok[eé]mon.*?if tails, search your deck for an? "
    r"basic ", re.I | re.S)
_S5_BOTTOM_COIN_DRAW = re.compile(
    r"each player shuffles their hand and puts it on the bottom of their "
    r"deck\..*?each player flips a coin\. if heads, that player draws (\d+) "
    r"cards?\..*?if tails, they draw (\d+) cards?", re.I | re.S)


def _S5_CAT_KEY(raw: str) -> str:
    """阵营名归一（与引擎 `_category_key` 同口径：去所有格、只留小写字母数字）"""
    s = str(raw or "").lower().replace("’", "'")
    s = re.sub(r"'s\b", "", s)
    return re.sub(r"[^a-z0-9]+", "", s)


def audit_devolve(card_name: str, item_name: str, desc: str,
                  effects: list) -> list:
    """D3 对账：退化 devolve

    两个族的共同错法都是**丢了限定词**，而且都不会报错：

      · 对手/自己搞反（`opp` ↔ `self`）—— 变成「退自己的」或「退对手的」；
      · 「any number」（自己侧）写成 `1` —— 静默削弱（只能退一阶）；
      · 丢 `{P}` 类型限定 —— 任何属性都能退（卡面只允许超能）；
      · 丢 `active_only` —— 备战位的 Archeops 也能发动。
    """
    errs: list = []
    d = _norm(desc or "")
    d = d.replace("’", "'")
    joined = "|".join(str(e) for e in _expand_compound(effects))
    if "devolve" not in d:
        return errs
    if "devolve:" not in joined:
        errs.append(f"{card_name} · {item_name}：卡面有「devolve」却没产 "
                    f"`devolve:` tag，实际 {effects}")
        return errs
    tag = next((e for e in _expand_compound(effects)
                if str(e).startswith("devolve:")), "")
    parts = str(tag).split(":")
    scope = parts[1] if len(parts) > 1 else ""
    amt = parts[2] if len(parts) > 2 else ""
    opp = "your opponent's evolved" in d
    mine = "your evolved" in d
    if opp and scope != "opp":
        errs.append(f"{card_name} · {item_name}：卡面退化**对手**的宝可梦，"
                    f"tag 写成 scope={scope!r}（{tag}）")
    if mine and scope != "self":
        errs.append(f"{card_name} · {item_name}：卡面退化**自己**的宝可梦，"
                    f"tag 写成 scope={scope!r}（{tag}）")
    if "any number of evolution cards" in d and amt != "any":
        errs.append(f"{card_name} · {item_name}：卡面「any number of Evolution "
                    f"cards」应产 `:any`（数量由玩家选），实际 {tag}")
    if "highest stage evolution card" in d and amt != "1":
        errs.append(f"{card_name} · {item_name}：卡面「the highest Stage Evolution "
                    f"card」= 固定 1 阶，实际 {tag}")
    m_type = re.search(r"your evolved \{([a-z])\}", d)
    if m_type and ":type:" not in tag:
        errs.append(f"{card_name} · {item_name}：卡面限定 {{{m_type.group(1).upper()}}} "
                    f"属性，tag 丢 `:type:`（{tag}）")
    if "in the active spot" in d and "active_only" not in tag:
        errs.append(f"{card_name} · {item_name}：卡面限定「in the Active Spot」，"
                    f"tag 丢 `:active_only`（{tag}）")
    return errs


def audit_s6_first_turn(card_name: str, item_name: str, desc: str,
                        effects: list) -> list:
    """S6 对账：先手第一回合的**例外许可**

    卡面两句（写法不同、都是「例外」，不是效果）：
      · 招式 —— 「If you go first, you can use this **attack** during your first
        turn.」（Volbeat / Exeggcute / Delibird / Tapu Koko，4 张）
      · 训练家 —— 「If you go first, you may use this **card** during your first
        turn.」（Carmine / Team Rocket's Proton，都是 Supporter，2 张）

    S6a 把「先手 T1 禁攻 / 禁 Supporter」打开后，这两句就是**唯一的放行口**：
    漏产 `use_any:first_turn` = 这 6 张在开局回合各少一个可用动作（静默削弱），
    而且因为后半句照常解析，`unsupported` 为空、**任何别的守卫都看不出来**。
    """
    errs: list = []
    d = _norm(desc or "")
    joined = "|".join(str(e) for e in _expand_compound(effects))
    if _S6_T1_ATTACK.search(d) and "use_any:first_turn" not in joined:
        errs.append(f"{card_name} · {item_name}：卡面「If you go first, you can use "
                    f"this attack during your first turn」应产 "
                    f"`use_any:first_turn`，实际 {effects}")
    if _S6_T1_CARD.search(d) and "use_any:first_turn" not in joined:
        errs.append(f"{card_name} · {item_name}：卡面「If you go first, you may use "
                    f"this card during your first turn」应产 "
                    f"`use_any:first_turn`（卡级 tag 的一段），实际 {effects}")
    return errs


# S6 的两条独立正则（刻意不复用解析包的 `_TURN1_PERMISSION`）
_S6_T1_ATTACK = re.compile(
    r"if you go first, you can use this attack during your first turn", re.I)
_S6_T1_CARD = re.compile(
    r"if you go first, you may use this card during your first turn", re.I)


def audit_s2_events(card_name: str, item_name: str, desc: str,
                    effects: list) -> list:
    """S2 对账：延迟生效 / 换位订阅

    这两族的错法都是「认了半句」：
      · 只认「弃手牌」不认「回合末」→ 变成**立即**弃（强度天差地别）；
      · 只认「放指示物」不认「对手换下」→ 变成**无条件**每回合放。
    所以这里要求 tag 里同时含时点与条件。
    """
    errs: list = []
    d = _norm(desc or "")
    tags = [str(e) for e in _expand_compound(effects)]
    joined = "|".join(tags)
    m = _S2_DELAY.search(d)
    if m:
        want = (f"queue:turn_end:discard_hand:all:if:own_hand_at_least:"
                f"{int(m.group(1))}")
        if want not in joined:
            errs.append(f"{card_name} · {item_name}：卡面「回合末手牌 ≥"
                        f"{m.group(1)} 则弃手牌」应产 `{want}`，实际 {tags}")
    m = _S2_WATCH.search(d)
    if m:
        want = f"on_event:switch_out:counters:{int(m.group(1))}:on:leaving"
        if want not in joined:
            errs.append(f"{card_name} · {item_name}：卡面「对手回合换下时放 "
                        f"{m.group(1)} 个指示物」应产 `{want}`，实际 {tags}")
    m = _S2_EVOLVE_WATCH.search(d)
    if m:
        want = f"on_event:evolve:counters:{int(m.group(1))}:on:entering"
        if want not in joined:
            errs.append(f"{card_name} · {item_name}：卡面「对手进化时放 "
                        f"{m.group(1)} 个指示物」应产 `{want}`，实际 {tags}")
    if _S2_SWIRL.search(d):
        # 「As long as … in the Active Spot」必须带 `while:active`，否则放后备也生效
        want = "on_event:switch_in:status:confuse:on:entering"
        if want not in joined:
            errs.append(f"{card_name} · {item_name}：卡面「对手换上的新战斗位混乱」"
                        f"应产 `{want}:scope:opp_their_turn:while:active`，实际 {tags}")
        elif ":while:active" not in joined:
            errs.append(f"{card_name} · {item_name}：订阅缺 `while:active` —— "
                        f"卡面限定「在战斗位时」，否则后备也生效（静默加强）")
    # 限时无弱点（Archaludon ex）
    if re.search(r"during your opponent[’']?s next turn,? this pok[eé]mon has no "
                 r"weakness", d, re.I):
        if "shield:no_weakness" not in tags:
            errs.append(f"{card_name} · {item_name}：卡面「限时无弱点」应产 "
                        f"`shield:no_weakness`，实际 {tags}")
    # S2c：击倒订阅 / 回合末被动道具 / 延迟弃置
    m = _S2C_KO_SEARCH.search(d)
    if m:
        want = f"on_event:ko:search_any:{int(m.group(1))}:on:leaving"
        if want not in joined:
            errs.append(f"{card_name} · {item_name}：卡面「被招式击倒时检索 "
                        f"{m.group(1)} 张」应产 `{want}:scope:own_by_attack`，"
                        f"实际 {tags}")
        elif "own_by_attack" not in joined:
            errs.append(f"{card_name} · {item_name}：击倒订阅缺 "
                        f"`scope:own_by_attack`（非招式击倒不得触发），实际 {tags}")
    if _S2C_TURN_END_ATTACH.search(d):
        want = "on_event:turn_end:attach_basic:on:holder"
        if want not in joined:
            errs.append(f"{card_name} · {item_name}：卡面「回合末可附着基本能量」"
                        f"应产 `{want}:scope:own_turn:while:active`，实际 {tags}")
        elif ":while:active" not in joined:
            errs.append(f"{card_name} · {item_name}：缺 `while:active` —— "
                        f"卡面限定「在战斗位时」，否则后备也生效")
    if _S2C_DELAY_DISCARD.search(d):
        # ⚠ 用 `discard_defender` 而不是 `knockout:` —— 后者在本项目里专指
        # 「击倒并**取奖赏**」，这里是「弃置、不取奖赏」，混用会让对账误判
        # （实测：`knockout:queued` 被对账当成未知的击倒范围）。
        want = "queue:opp_next_turn_end:discard_defender"
        if want not in joined:
            errs.append(f"{card_name} · {item_name}：卡面「对手下回合末弃置战斗位」"
                        f"应产 `{want}:all`，实际 {tags}")
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
                eff = _expand_compound(list(it.get("effects") or []))
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
                errs += audit_attach_variants(c["name"], it.get("name"), desc, eff)
                errs += audit_search_energy_distinct(c["name"], it.get("name"),
                                                     desc, eff)
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
                errs += audit_energy_self_to_deck(c["name"], it.get("name"),
                                                  desc, eff)
                errs += audit_on_damaged(c["name"], it.get("name"), desc, eff)
                errs += audit_set_per(c["name"], it.get("name"), desc, eff)
                errs += audit_batch40(c["name"], it.get("name"), desc, eff)
                # `cost` 只对特性有意义（招式没有发动代价字段）
                errs += audit_batch53(c["name"], it.get("name"), desc, eff,
                                      it.get("cost") if kind == "abilities"
                                      else None)
                errs += audit_batch54(c["name"], it.get("name"), desc, eff)
                errs += audit_shuffle_opp_pokemon(c["name"], it.get("name"),
                                                  desc, eff)
                errs += audit_static_immune(c["name"], it.get("name"), desc, eff)
                errs += audit_batch58_62(c["name"], it.get("name"), desc, eff)
                errs += audit_batch63_67(c["name"], it.get("name"), desc, eff)
                errs += audit_batch68_77(c["name"], it.get("name"), desc, eff)
                errs += audit_batch79(c["name"], it.get("name"), desc, eff)
                errs += audit_batch80_84(c["name"], it.get("name"), desc, eff)
                errs += audit_batch85(c["name"], it.get("name"), desc, eff)
                errs += audit_batch93_94(c["name"], it.get("name"), desc, eff)
                errs += audit_s3_zones(c["name"], it.get("name"), desc, eff)
                errs += audit_s2_events(c["name"], it.get("name"), desc, eff)
                errs += audit_s2d_events(c["name"], it.get("name"), desc, eff)
                errs += audit_s2e_events(c["name"], it.get("name"), desc, eff)
                errs += audit_s4_immune(c["name"], it.get("name"), desc, eff)
                errs += audit_s5_borrow(c["name"], it.get("name"), desc, eff)
                errs += audit_s6_first_turn(c["name"], it.get("name"), desc, eff)
                errs += audit_devolve(c["name"], it.get("name"), desc, eff)
                errs += audit_knockout(c["name"], it.get("name"), desc, eff)
        # 训练家卡级（2026-11-02 第三十六批）：
        # ⚠ 上面的循环只走 `attacks` / `abilities`，而训练家卡的效果文本挂在
        #   条目**自身**的 `desc` 上 → 训练家侧的卡级 tag 与卡面**从来没被对账**过
        #   （盲区）。本节补齐，载入本批新增的「打出代价」。
        _ctag = str(c.get("tag") or "")
        if str(c.get("supertype")) == "Trainer" \
                and not _ctag.startswith(("fossil:", "senergy:", "stadium:")):
            n_items += 1
            _cdesc = c.get("desc") or ""
            errs += audit_play_cost(c["name"], _cdesc, c.get("play_cost") or "")
            # 卡级 tag 的对账（第三十八批起）：`discard_tool_or_senergy` 是训练家
            # 卡级 tag，不在 attacks/abilities 里，上面的循环看不到它。
            errs += audit_discard_tool_or_senergy(
                c["name"], c["name"], _cdesc, [_ctag] if _ctag else [])
            errs += audit_batch40(c["name"], c["name"], _cdesc,
                                  [_ctag] if _ctag else [])
            errs += audit_batch53(c["name"], c["name"], _cdesc,
                                  [_ctag] if _ctag else [])
            errs += audit_batch54(c["name"], c["name"], _cdesc,
                                  [_ctag] if _ctag else [])
            errs += audit_batch58_62(c["name"], c["name"], _cdesc,
                                    [_ctag] if _ctag else [],
                                    c.get("play_gate") or None)
            errs += audit_batch63_67(c["name"], c["name"], _cdesc,
                                     [_ctag] if _ctag else [])
            errs += audit_batch68_77(c["name"], c["name"], _cdesc,
                                     [_ctag] if _ctag else [])
            errs += audit_batch79(c["name"], c["name"], _cdesc,
                                  [_ctag] if _ctag else [])
            errs += audit_s3_zones(c["name"], c["name"], _cdesc,
                                   [_ctag] if _ctag else [])
            errs += audit_s2_events(c["name"], c["name"], _cdesc,
                                    [_ctag] if _ctag else [])
            errs += audit_s2d_events(c["name"], c["name"], _cdesc,
                                     [_ctag] if _ctag else [])
            errs += audit_s2e_events(c["name"], c["name"], _cdesc,
                                     [_ctag] if _ctag else [])
            errs += audit_s5_borrow(c["name"], c["name"], _cdesc,
                                    [_ctag] if _ctag else [])
            errs += audit_s6_first_turn(c["name"], c["name"], _cdesc,
                                        [_ctag] if _ctag else [])
            errs += audit_devolve(c["name"], c["name"], _cdesc,
                                  [_ctag] if _ctag else [])
            errs += audit_batch80_84(c["name"], c["name"], _cdesc,
                                     [_ctag] if _ctag else [])
            errs += audit_batch85(c["name"], c["name"], _cdesc,
                                 [_ctag] if _ctag else [])
            errs += audit_batch89(c["name"], c["name"], _cdesc,
                                 [_ctag] if _ctag else [])
        # 化石卡：训练家条目带 `fossil` 上场形态（其特性挂在 fossil.abilities 下）
        fossil = c.get("fossil") or {}
        for it in fossil.get("abilities") or []:
            n_items += 1
            desc = it.get("desc") or ""
            eff = _expand_compound(list(it.get("effects") or []))
            if it.get("tag"):
                eff.append(it["tag"])
            errs += audit_static_damage(c["name"], it.get("name"), desc, eff)
            errs += audit_static_immune(c["name"], it.get("name"), desc, eff)
            errs += audit_extra_damage(c["name"], it.get("name"), desc, eff)
            errs += audit_s4_immune(c["name"], it.get("name"), desc, eff)
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
          "检索能量自由分配 · 自由分配两变体（抛币/每只后备） · 能量代价去向 · "
          "打出代价 · 弃对手道具或场地 · 附着四形态（双色/选N只/抛到反面/抛币门） · "
          "检索不同属性 · 连续抛币计数源/选N只进化/任意张检索 · "
          "{ex} 目标筛选/自身能量洗回牌库 · 对手宝可梦洗回牌库 · "
          "奖赏减少/双状态/换位补抽/拉人混乱/弃手重抽/特性发动代价 · "
          "对手弃手牌/撤退费归零/常驻禁打 · "
          "自身跨回合禁攻/对手自选洗回/无视弱抗两档/Kofu 减费计数源/能量移动区域 · "
          "双方洗抽/道具+HP/训练家能量洗回/指示物Defending/指示物this · "
          "搜索族（牌库顶筛支援者/四类别各取1/任意数量铺后备/检索放回牌库顶） · "
          "满HP免击倒/清除自身状态/宝可梦洗回/检索弃置/按剩余HP放指示物 · "
          "奖赏卡重发/翻面对手奖赏卡/亮出对手牌库顶/查看对手奖赏卡/弃牌库底收自身 · "
          "回合末延迟弃手牌/对手换下时放指示物/对手进化时放指示物/战斗位订阅/限时无弱点 · "
          "限时订阅/撤退费+战斗位搬能量/具名能量附着 · "
          "可选触发/原地替换/只能由效果上场 · "
          "效果免疫（团队范围/训练家来源） · "
          "抛币门借用/牌库顶借用/抛币分岔检索/放牌库底逐人抛币 · "
          "先手首回合例外许可（招式/训练家） · "
          "退化 devolve（方向/阶数/属性/战斗位） "
          "五十二类对账全部一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
