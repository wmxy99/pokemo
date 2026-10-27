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


def _norm(s: str) -> str:
    return (s or "").replace("\u2019", "'").lower()


def _want_snipe(tgt: str, per: int):
    """目标子句 → 期望的 snipe tag 前缀（``None`` = 无需 tag 或不该进池）"""
    t = _norm(tgt)
    if re.search(r"\bitself\b", t) or "this pok" in t:
        return "self"
    if "bench" not in t and ("active pok" in t or "defending pok" in t):
        return "none"                        # 战斗位由招式 damage 字段承载
    if re.search(r"\{|that (?:has|have|is|are|don|doesn)|named|for each|in any way",
                 t):
        return "unmodeled"                   # 子集/条件/缩放 → 不该进池
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
    return "unmodeled"


def audit_extra_damage(card_name: str, item_name: str, desc: str,
                       effects: list) -> list:
    """附加伤害 / 放置指示物子句 → 必须有对应 tag（self:N / snipe:…），否则报错"""
    errs = []
    d = PAREN.sub(" ", desc or "")
    for m in DMG_TO.finditer(d):
        if m.group(2):                      # 「more damage」属修正器范畴
            continue
        errs += _check_clause(card_name, item_name, m.group(3), m.group(1),
                              effects, m.group(0))
    for m in PLACE_COUNTERS.finditer(d):
        errs += _check_clause(card_name, item_name, m.group(2),
                              int(m.group(1)) * 10, effects, m.group(0))
    return errs


def _check_clause(card_name: str, item_name: str, clause: str, per,
                  effects: list, raw: str) -> list:
    want = _want_snipe(clause, per)
    if want == "none":
        return []
    if want == "unmodeled":
        return [f"{card_name} · {item_name}：不可精确表达的目标 "
                f"`{raw[:60]}` **却在池内**（应整卡不收）"]
    if want == "self":
        if f"self:{per}" not in effects:
            return [f"{card_name} · {item_name}：反伤 `{raw[:60]}` "
                    f"缺 `self:{per}` tag"]
        return []
    if not any(str(t).startswith(want + ":") for t in effects):
        return [f"{card_name} · {item_name}：`{raw[:60]}` 缺 `{want}:…` tag"]
    return []


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
    m = SEARCH_FOR.search(desc or "")
    if not m:
        return errs
    seg = _norm(m.group(1))
    clause = _norm(re.split(r"[.]", (desc or "")[m.start():], maxsplit=1)[0])
    got = [t for t in effects if str(t).startswith(("search_", "attach",
                                                    "evolve_from_deck"))]

    # ---- ① 去向 ----
    if "to evolve it" in clause or "to evolve that" in clause:
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
    if re.search(r"\btool card\b|\bitem card\b|\bstadium card\b|"
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
        if not t.startswith("dmg_plus:") or ":per:" not in t:
            continue
        counter = t.split(":per:")[1]
        if has_opp and counter.startswith("own_"):
            errs.append(f"{card_name} · {item_name}：`{phrase[:50]}` 是**对手**侧，"
                        f"却用了 `{counter}`（阵营反转）")
        if has_own and not has_opp and counter.startswith("opp_"):
            errs.append(f"{card_name} · {item_name}：`{phrase[:50]}` 是**我方**侧，"
                        f"却用了 `{counter}`（阵营反转）")
    return errs


def main() -> int:
    if not POOL.exists():
        print(f"[error] 找不到卡池：{POOL}", file=sys.stderr)
        return 2
    pool = json.loads(POOL.read_text(encoding="utf-8"))
    errs: list = []
    n_items = 0
    for c in pool.get("cards") or []:
        for kind in ("attacks", "abilities"):
            for it in c.get(kind) or []:
                n_items += 1
                desc = it.get("desc") or ""
                eff = list(it.get("effects") or [])
                if it.get("tag"):
                    eff.append(it["tag"])
                errs += audit_extra_damage(c["name"], it.get("name"), desc, eff)
                errs += audit_search_kind(c["name"], it.get("name"), desc, eff)
                errs += audit_energy_target(c["name"], it.get("name"), desc, eff)
                errs += audit_counter_side(c["name"], it.get("name"), desc, eff)
                errs += audit_look_top(c["name"], it.get("name"), desc, eff)
                errs += audit_ability_search(c["name"], it.get("name"), desc, eff)
                errs += audit_mill(c["name"], it.get("name"), desc, eff)

    print(f"对账范围：扩展池 {len(pool.get('cards') or [])} 张 / {n_items} 个招式·特性")
    if errs:
        print(f"::error::卡面 ↔ tag 对账发现 {len(errs)} 处不一致")
        for e in errs:
            print(f"  - {e}")
        return 1
    print("OK: 附加伤害/指示物目标 · 检索类别 · 弃能对象 · 计数源阵营 · "
          "检视牌库顶 · 特性检索 · 磨牌 七类对账全部一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
