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
# 检索子句
SEARCH_FOR = re.compile(r"search\s+(?:your\s+deck|it)\s+for\s+(.{0,120}?)"
                        r"(?:,| and | then |\.|\()", re.I)


def _norm(s: str) -> str:
    return (s or "").replace("\u2019", "'").lower()


def audit_extra_damage(card_name: str, item_name: str, desc: str,
                       effects: list) -> list:
    """附加伤害子句 → 必须有对应 tag（self:N / spread:all:N），否则报「静默丢效果」"""
    errs = []
    d = PAREN.sub(" ", desc or "")
    for m in DMG_TO.finditer(d):
        if m.group(2):                      # 「more damage」属修正器范畴
            continue
        tgt = _norm(m.group(3))
        n = m.group(1)
        if re.search(r"\bitself\b", tgt):
            if f"self:{n}" not in effects:
                errs.append(f"{card_name} · {item_name}：反伤 "
                            f"`{m.group(0)[:60]}` 缺 `self:{n}` tag")
        elif re.search(r"each of your opponent'?s? benched", tgt):
            if f"spread:all:{n}" not in effects:
                errs.append(f"{card_name} · {item_name}：全体后备伤害 "
                            f"`{m.group(0)[:60]}` 缺 `spread:all:{n}` tag")
        elif re.search(r"your opponent'?s? active pok", tgt):
            pass                            # 打战斗位 = 招式 damage 字段承载，无需 tag
        elif re.search(r"bench|each of|\d+ of your|your pok", tgt):
            errs.append(f"{card_name} · {item_name}：不可表达的附加伤害目标 "
                        f"`{m.group(0)[:60]}` **却在池内**（应整卡不收）")
    return errs


def audit_search_kind(card_name: str, item_name: str, desc: str,
                      effects: list) -> list:
    """检索类别只看「search … for …」子句（不能因全文出现 Pokémon 就判成宝可梦）"""
    errs = []
    m = SEARCH_FOR.search(desc or "")
    if not m:
        return errs
    seg = _norm(m.group(1))
    if "energy" in seg:
        want = "search_energy"
    elif "pokemon" in seg or "pok\u00e9mon" in seg:
        want = "search_pokemon"
    elif "trainer" in seg:
        want = "search_trainer"
    else:
        return errs
    got = [t for t in effects if str(t).startswith("search_")]
    if got and not any(str(t).startswith(want) for t in got):
        errs.append(f"{card_name} · {item_name}：检索子句是 `{seg[:50]}` "
                    f"→ 应为 `{want}`，实际 {got}")
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

    print(f"对账范围：扩展池 {len(pool.get('cards') or [])} 张 / {n_items} 个招式·特性")
    if errs:
        print(f"::error::卡面 ↔ tag 对账发现 {len(errs)} 处不一致")
        for e in errs:
            print(f"  - {e}")
        return 1
    print("OK: 附加伤害目标 / 检索类别 / 弃能对象 / 计数源阵营 四类对账全部一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
