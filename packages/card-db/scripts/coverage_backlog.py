# -*- coding: utf-8 -*-
"""PokéLab AI · 未建模动作清单（backlog）生成器

把「引擎/解析链无法映射的卡牌效果条目」按**建模批次**归类、按**真实对战频次**
排优先级，产出机器可读 (JSON) 与人读 (Markdown) 两份清单，供分批建模排期。

数据来源（全部只读）
  - `packages/card-db/data/db/pokelab.db` → `cards.effects_json.parse`
    （由 `import_challenge_dataset.py --parse-effects` 写入的 K2 解析产物，
     其中 `unsupported[]` 即「未建模动作」条目）
  - `packages/engine/engine/data/decklists.json` + `engine.reallib.coverage()`
    → A1–A4 **预设真实卡表**的 replaced/approximated 逐卡清单
    （= 真实对战里**一定会用到**的卡，作为「高频」的客观锚点）

批次口径（确定性、可复跑）
  B1 预设卡组关键卡    — 在 A1–A4 中出现、当前不可执行或仅灰名单的卡（逐卡建模）
  B2 tag 家族扩展      — 引擎已有能力/低成本可扩展的 tag（招式侧·特性侧·训练家侧）
  B3 解析召回补齐      — 英文卡面未被规则解析器识别的句式（补正则即可映射到 B2 tag）
  B4 需新引擎机制      — 条件伤害缩放 / 抛币 / 伤害减免免疫 / 指示物计数（P1 登记）
  B5 不建模登记        — 真·静态规则与元游戏效果（诚实登记，不假装支持）

用法::

    python scripts/coverage_backlog.py                      # 写 unmodeled_backlog.{json,md}
    python scripts/coverage_backlog.py --snapshot before    # 额外存 unmodeled_backlog.before.json
    python scripts/coverage_backlog.py --compare a.json b.json   # 两个快照对比（覆盖率增量）

退出码：0 正常；2 数据源缺失。
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import re
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG = HERE.parent                       # packages/card-db
REPO = PKG.parents[1]                   # pokelab-ai
DB = PKG / "data" / "db" / "pokelab.db"
OUT_DIR = PKG / "data" / "cleaned"

for p in (str(REPO / "packages" / "engine"), str(REPO / "packages" / "agent")):
    if p not in sys.path:
        sys.path.insert(0, p)


# ---------------- 批次定义（顺序即优先级） ----------------
BATCHES = [
    {"id": "B1", "name": "预设卡组关键卡",
     "goal": "A1–A4 预设真实卡表中不可执行/仅灰名单的卡逐卡建模",
     "fix": "为每张卡补引擎原生 tag（训练家 tag / 特性 tag），并补引擎派发",
     "priority": 1},
    {"id": "B2", "name": "tag 家族扩展",
     "goal": "把引擎已有能力或低成本可扩展的 tag 家族补齐映射与派发",
     "fix": "agent.effects.to_engine_tags 放通映射 + engine.battle 新增派发分支",
     "priority": 2},
    {"id": "B3", "name": "解析召回补齐（英文卡面）",
     "goal": "英文卡面句式未被规则解析器识别，导致整条效果落进 rule 兜底",
     "fix": "agent.effects._parse_rule_en 补正则（弃牌堆回收/弃自身能量/抛币前缀/附能/检索）",
     "priority": 3},
    {"id": "B4", "name": "需新引擎机制（P1 登记）",
     "goal": "需要条件表达式 / RNG 钩子 / 静态修正层等新机制，不属本轮",
     "fix": "登记为 P1 批次；本轮仅保证「如实标注不支持」，不假装支持",
     "priority": 4},
    {"id": "B5", "name": "不建模登记",
     "goal": "真·静态规则与元游戏效果（如卡名依赖、双方限制、场地级规则）",
     "fix": "永久登记在差异透明清单（C5），不做实现",
     "priority": 5},
]

# ---------------- rule 桶细分（英文卡面关键词 → 归属） ----------------
_RULE_RULES = [
    # (归属批次, 子类 key, 说明, 正则)
    ("B4", "scaling_damage", "条件伤害缩放（for each …）",
     r"more damage for each|does .{0,40}more damage for each|for each .{0,30}damage"),
    ("B4", "coin_flip", "抛币判定（Flip a coin）", r"flip a coin"),
    ("B4", "static_lock", "静态锁/免疫/伤害减免",
     r"prevent all damage|can'?t use attacks|cannot use attacks|"
     r"have no abilit|has no abilit|damage is not affected|"
     r"takes? \d+ less damage|no effect on this pok"),
    ("B3", "recover_from_discard", "弃牌堆回收入手（Put … from your discard pile into your hand）",
     r"discard pile into your hand|discard pile.{0,30}into your hand|"
     r"from your discard pile to your hand"),
    ("B3", "discard_energy_self", "弃自身能量（Discard a {X} Energy from this Pokémon）",
     r"discard .{0,30}energ.{0,20}from this pok"),
    ("B3", "discard_self", "弃自身/弃手牌（含对手）",
     r"discard (?:up to )?\d+|discard your (?:whole )?hand|discard a card|"
     r"discard this card|discard the top"),
    ("B3", "attach", "附能量（attach … energy）", r"attach .{0,40}energ|energ.{0,30}attach"),
    ("B3", "draw", "抽牌（draw … card）", r"\bdraw\b"),
    ("B3", "search", "检索（search / look at）", r"\bsearch\b|look at the top"),
    ("B3", "switch", "换位（switch / into the Active Spot）", r"\bswitch\b"),
    ("B3", "move_damage", "移动伤害指示物", r"move .{0,30}damage counter"),
    ("B3", "spread", "放置伤害指示物", r"put \d+ damage counter"),
    ("B5", "unclassified", "未分类（需人工判定）", r""),
]


def _classify_rule(text: str) -> tuple:
    t = (text or "").lower()
    for batch, key, desc, pat in _RULE_RULES:
        if pat and re.search(pat, t):
            return batch, key, desc
    return "B5", "unclassified", "未分类（需人工判定）"


# ---------------- 数据读取 ----------------
def load_engine_deck_gaps() -> dict:
    """A1–A4 预设卡表的 replaced / approximated 逐卡清单（真实高频锚点）"""
    from engine import reallib                              # noqa: WPS433
    out = {"archetypes": [], "cards": {}}
    for aid in reallib.archetype_ids():
        cov = reallib.coverage(aid)
        desc = reallib.describe(aid)
        out["archetypes"].append({
            "id": aid, "label": cov["label"], "label_en": cov["label_en"],
            "total": cov["total"], "playable": cov["playable"],
            "executable_rate": cov["executable_rate"],
            "battle_ready": cov["battle_ready"],
            "replaced_n": len(cov["replaced"]),
            "approximated_n": len(cov["approximated"]),
        })
        for kind, rows in (("replaced", cov["replaced"]),
                           ("approximated", cov["approximated"])):
            for r in rows:
                e = out["cards"].setdefault(r["name"], {
                    "name": r["name"], "copies": 0, "decks": [],
                    "status": kind, "reason": r["reason"]})
                e["copies"] += int(r["count"])
                e["decks"].append({"arch": aid, "count": int(r["count"]),
                                   "status": kind})
                # replaced 优先级高于 approximated
                if kind == "replaced":
                    e["status"] = "replaced"
                    e["reason"] = r["reason"]
    return out


def load_parse_gaps() -> dict:
    """从 DB 读 K2 解析产物，抽取 unsupported 条目并归类"""
    if not DB.exists():
        return {}
    con = sqlite3.connect(str(DB))
    cur = con.cursor()
    totals = {"cards": 0, "cards_parsed": 0, "items": 0, "specs": 0,
              "engine_tags": 0, "unsupported": 0,
              "cards_with_unsupported": 0, "cards_fully_mapped": 0,
              "cards_with_supported_tag": 0}
    # 分组容器：key → {batch, sub, desc, entries, cards:set, samples[]}
    groups: dict = {}
    cur.execute("select id, name, effects_json from cards")
    for cid, name, ej in cur.fetchall():
        totals["cards"] += 1
        try:
            d = json.loads(ej) if ej else {}
        except Exception:                                    # noqa: BLE001
            d = {}
        pr = (d or {}).get("parse")
        if not pr:
            continue
        totals["cards_parsed"] += 1
        totals["items"] += len(pr.get("items", []))
        totals["specs"] += len(pr.get("specs", []))
        tags = pr.get("engine_tags", [])
        totals["engine_tags"] += len(tags)
        if tags:
            totals["cards_with_supported_tag"] += 1
        unsup = pr.get("unsupported", [])
        totals["unsupported"] += len(unsup)
        zh = (d.get("library") or {}).get("name_zh") or ""
        if unsup:
            totals["cards_with_unsupported"] += 1
        else:
            totals["cards_fully_mapped"] += 1
        seen = set()
        for it in pr.get("items", []):
            for u in it.get("unsupported", []):
                t = u.get("type")
                reason = u.get("reason") or ""
                src = ""
                for s in it.get("specs", []):
                    src = s.get("source_text") or src
                    break
                if t == "rule":
                    batch, sub, desc = _classify_rule(src)
                else:
                    batch, sub, desc = _TYPE_BATCH.get(
                        t, ("B4", f"type:{t}", f"未登记动作类型 {t}"))
                key = (batch, sub, t, reason)
                g = groups.setdefault(key, {
                    "batch": batch, "sub": sub, "action_type": t,
                    "reason": reason, "desc": desc, "entries": 0,
                    "cards": set(), "samples": []})
                g["entries"] += 1
                g["cards"].add(cid)
                if len(g["samples"]) < 5 and src:
                    g["samples"].append({"card": name, "name_zh": zh,
                                         "text": src[:200]})
                seen.add(cid)
    con.close()
    return {"totals": totals, "groups": groups}


_TYPE_BATCH = {
    "search": ("B2", "trainer_search", "训练家检索（需通用 search tag）"),
    "spread": ("B2", "spread_per", "非任意分配的多目标放置（spread:N:per）"),
    "draw": ("B2", "ability_draw", "特性抽牌（draw:N on ability）"),
    "discard": ("B2", "attack_discard", "招式/特性弃牌（discard:N）"),
    "switch": ("B2", "attack_switch", "招式/效果换位（switch_self / gust）"),
    "attach": ("B2", "attack_attach", "招式/特性附能（attach:N:type）"),
    "move_damage": ("B2", "attack_move_damage", "移动伤害指示物（move_damage:N）"),
    "evolve": ("B2", "evolve_skip", "跳阶进化（Rare Candy 类）"),
    "knockout": ("B3", "knockout_condition", "击倒条件误判（需解析修正）"),
    "status": ("B2", "status_unknown", "未知状态词"),
    "heal": ("B2", "heal_unknown", "回复量未识别"),
    "damage": ("B2", "damage_meta", "伤害元数据（无需 tag）"),
}


# ---------------- 报告组装 ----------------
def build_report(snapshot: str = "current") -> dict:
    gaps = load_parse_gaps()
    decks = load_engine_deck_gaps()
    if not gaps:
        return {}
    groups = gaps["groups"]
    totals = gaps["totals"]

    # B1：卡级（逐卡）
    b1_cards = sorted(decks["cards"].values(),
                      key=lambda x: (-x["copies"], x["name"]))
    b1_entries = len(b1_cards)

    items = []
    batch_count = collections.Counter()
    batch_entries = collections.Counter()
    batch_cards = collections.Counter()
    for (batch, sub, t, reason), g in groups.items():
        items.append({
            "batch": batch, "sub": sub, "desc": g["desc"],
            "action_type": t, "reason": reason,
            "entries": g["entries"], "cards": len(g["cards"]),
            "samples": g["samples"],
        })
        batch_count[batch] += 1
        batch_entries[batch] += g["entries"]
        batch_cards[batch] += len(g["cards"])
    items.sort(key=lambda x: (x["batch"], -x["entries"]))

    batches = []
    for b in BATCHES:
        bid = b["id"]
        if bid == "B1":
            entries, cards = b1_entries, b1_entries
        else:
            entries, cards = batch_entries[bid], batch_cards[bid]
        batches.append({**b, "groups": batch_count[bid],
                        "entries": entries, "cards": cards,
                        "share": round(entries / totals["unsupported"], 4)
                        if totals["unsupported"] else 0.0})

    return {
        "snapshot": snapshot,
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": {
            "db": str(DB.relative_to(REPO)).replace("\\", "/"),
            "decklists": "packages/engine/engine/data/decklists.json",
            "parse_backend": "rule（离线确定性基线）",
        },
        "parse_totals": totals,
        "deck_baseline": decks["archetypes"],
        "batches": batches,
        "items": items,
        "b1_cards": b1_cards,
        "notes": [
            "「未建模条目」= effects_json.parse.unsupported 的条目数（一条效果可含多条动作）。",
            "B1 的「高频」锚点 = 出现在 A1–A4 预设真实卡表（2026 Worlds meta）中的卡；"
            "副本数越多 = 在真实对战中越常出现。",
            "B3/B4/B5 的 rule 桶归属由英文卡面关键词启发式判定（确定性，可复跑），"
            "故为**排期估算**而非精确实现清单。",
            "诚实标注（C5）：未被映射的条目一律保留在 unsupported 中，不静默降级。",
        ],
    }


def to_markdown(rep: dict) -> str:
    t = rep["parse_totals"]
    L = []
    L.append("# PokéLab AI · 未建模动作清单（backlog）")
    L.append("")
    L.append(f"- 快照：`{rep['snapshot']}` · 生成时间：{rep['generated_at']}")
    L.append(f"- 数据源：`{rep['source']['db']}`（K2 解析后端："
             f"{rep['source']['parse_backend']}）")
    L.append("")
    L.append("## 0. 总量")
    L.append("")
    L.append("| 指标 | 值 |")
    L.append("|---|---|")
    L.append(f"| 卡库总卡数 | {t['cards']} |")
    L.append(f"| 已跑解析卡数 | {t['cards_parsed']} |")
    L.append(f"| 解析条目（item） | {t['items']} |")
    L.append(f"| 解析动作条目（spec.actions） | {t['specs']} |")
    L.append(f"| 已映射引擎 tag 条目 | {t['engine_tags']} |")
    L.append(f"| **未建模动作条目** | **{t['unsupported']}** |")
    L.append(f"| 含未建模条目的卡数 | {t['cards_with_unsupported']} |")
    L.append(f"| 全效果可映射卡数 | {t['cards_fully_mapped']} |")
    L.append(f"| 至少 1 条引擎 tag 的卡数 | {t['cards_with_supported_tag']} |")
    L.append("")
    L.append("## 1. 批次总览（按优先级）")
    L.append("")
    L.append("| 批次 | 主题 | 条目 | 影响卡数 | 占比 | 建模动作 |")
    L.append("|---|---|---:|---:|---:|---|")
    for b in rep["batches"]:
        L.append(f"| **{b['id']}** | {b['name']} | {b['entries']} | {b['cards']} | "
                 f"{b['share'] * 100:.1f}% | {b['fix']} |")
    L.append("")
    L.append("## 2. B1 · 预设卡组关键卡（最高优先，逐卡建模）")
    L.append("")
    L.append("> 「高频」= 出现在 A1–A4 预设真实卡表（2026 Worlds meta）中；"
             "副本数越多越常出现。")
    L.append("")
    L.append("| 卡名 | 副本 | 状态 | 影响卡组 | 原因 |")
    L.append("|---|---:|---|---|---|")
    for c in rep["b1_cards"]:
        decks = "、".join(f"{d['arch']}×{d['count']}" for d in c["decks"])
        L.append(f"| {c['name']} | {c['copies']} | {c['status']} | {decks} | "
                 f"{c['reason']} |")
    L.append("")
    L.append("## 3. B2–B5 · 分组明细")
    L.append("")
    for b in rep["batches"]:
        if b["id"] == "B1":
            continue
        rows = [x for x in rep["items"] if x["batch"] == b["id"]]
        if not rows:
            continue
        L.append(f"### {b['id']} · {b['name']}（{b['entries']} 条 / {b['cards']} 卡）")
        L.append("")
        L.append(f"- 目标：{b['goal']}")
        L.append(f"- 动作：{b['fix']}")
        L.append("")
        L.append("| 子类 | 动作类型 | 条目 | 卡数 | 未建模原因 |")
        L.append("|---|---|---:|---:|---|")
        for r in rows:
            L.append(f"| {r['sub']} | {r['action_type']} | {r['entries']} | "
                     f"{r['cards']} | {r['reason']} |")
        L.append("")
        sample_rows = [r for r in rows if r["samples"]][:4]
        for r in sample_rows:
            L.append(f"<details><summary>样例 · {r['sub']}</summary>")
            L.append("")
            for s in r["samples"][:3]:
                L.append(f"- **{s['card']}**"
                         f"{'（' + s['name_zh'] + '）' if s['name_zh'] else ''}："
                         f"`{s['text']}`")
            L.append("")
            L.append("</details>")
            L.append("")
    L.append("## 4. 口径说明")
    L.append("")
    for n in rep["notes"]:
        L.append(f"- {n}")
    L.append("")
    return "\n".join(L)


# ---------------- 快照对比 ----------------
def compare(a_path: str, b_path: str, report: str | None = None) -> None:
    a = json.loads(Path(a_path).read_text(encoding="utf-8"))
    b = json.loads(Path(b_path).read_text(encoding="utf-8"))
    ta, tb = a["parse_totals"], b["parse_totals"]
    rows = []
    for k, label in (("unsupported", "未建模动作条目"),
                     ("engine_tags", "已映射 tag 条目"),
                     ("cards_with_unsupported", "含未建模条目的卡"),
                     ("cards_fully_mapped", "全效果可映射卡")):
        va, vb = ta.get(k, 0), tb.get(k, 0)
        d = vb - va
        pct = (d / va * 100) if va else 0.0
        rows.append((label, va, vb, d, pct))

    print(f"=== 覆盖率对比  {a['snapshot']} → {b['snapshot']} ===")
    print(f"{'指标':<24}{'前':>10}{'后':>10}{'变化':>10}{'幅度':>10}")
    for label, va, vb, d, pct in rows:
        print(f"{label:<24}{va:>10}{vb:>10}{d:>+10}{pct:>+9.1f}%")
    print()
    print("=== 各卡组可执行率 ===")
    ma = {x["id"]: x for x in a["deck_baseline"]}
    mb = {x["id"]: x for x in b["deck_baseline"]}
    deck_rows = []
    for aid in mb:
        ra = ma.get(aid, {}).get("executable_rate", 0)
        rb = mb[aid]["executable_rate"]
        deck_rows.append((aid, mb[aid]["label"], ra, rb))
        print(f"  {aid} {mb[aid]['label']:<24} {ra:.4f} → {rb:.4f}  "
              f"({(rb - ra) * 100:+.2f}pp)")

    if report:
        L = ["# PokéLab AI · 卡牌覆盖率提升报告（未建模动作分批建模）", ""]
        L.append(f"- 对比：`{a['snapshot']}` → `{b['snapshot']}`"
                 f"（数据源 {b['source']['db']}，解析后端 "
                 f"`{b['source']['parse_backend']}`）")
        L.append(f"- 生成时间：{b['generated_at']}")
        L.append("")
        L.append("## 1. 效果映射覆盖（解析链 → 引擎可执行 tag）")
        L.append("")
        L.append("| 指标 | 前 | 后 | 变化 | 幅度 |")
        L.append("|---|---:|---:|---:|---:|")
        for label, va, vb, d, pct in rows:
            L.append(f"| {label} | {va} | {vb} | {d:+d} | {pct:+.1f}% |")
        L.append("")
        L.append("## 2. 预设真实卡组可执行率（引擎原生卡池覆盖）")
        L.append("")
        L.append("| 卡组 | 名称 | 前 | 后 | 变化 |")
        L.append("|---|---|---:|---:|---:|")
        for aid, label, ra, rb in deck_rows:
            L.append(f"| {aid} | {label} | {ra:.4f} | {rb:.4f} | "
                     f"{(rb - ra) * 100:+.2f}pp |")
        L.append("")
        L.append("## 3. 批次处置")
        L.append("")
        L.append("| 批次 | 主题 | 前 | 后 | 本轮处置 |")
        L.append("|---|---|---:|---:|---|")
        ba = {x["id"]: x for x in a["batches"]}
        bb = {x["id"]: x for x in b["batches"]}
        for bid in bb:
            L.append(f"| {bid} | {bb[bid]['name']} | {ba.get(bid, {}).get('entries', 0)} "
                     f"| {bb[bid]['entries']} | {bb[bid]['fix']} |")
        L.append("")
        L.append("## 4. 口径与诚实标注")
        L.append("")
        L.append("- **两个口径要分开看**：①「效果映射覆盖」= 解析链能否把卡面效果"
                 "映射为引擎可执行 tag（本报告 §1）；②「预设卡组可执行率」= 引擎原生"
                 "卡池里是否已有该卡（§2）。前者是**能力**，后者是**建模进度**。")
        L.append("- 未建模条目**不做静默降级**：无法映射的动作一律保留在 "
                 "`unsupported` 并给出理由（PRD C5 差异透明）。")
        L.append("- B3/B4/B5 的归属由英文卡面关键词启发式判定（确定性可复跑），"
                 "属**排期估算**；实际实现清单以 `unsupported[].reason` 为准。")
        L.append(f"- 残余 B4（{bb.get('B4', {}).get('entries', 0)} 条）需新引擎机制"
                 "（条件伤害缩放 / 抛币 / 伤害减免免疫 / 指示物计数），"
                 "本轮**明确不建模**，登记为 P1 批次。")
        L.append("")
        Path(report).write_text("\n".join(L) + "\n", encoding="utf-8")
        print(f"[ok] 报告写入 {report}")


def main() -> int:
    ap = argparse.ArgumentParser(description="未建模动作清单生成器")
    ap.add_argument("--snapshot", default="current", help="快照名（写入文件名后缀）")
    ap.add_argument("--out-dir", default=None, help="输出目录（默认 data/cleaned）")
    ap.add_argument("--compare", nargs=2, metavar=("A", "B"),
                    help="对比两个 JSON 快照并打印覆盖率增量")
    ap.add_argument("--report", default=None,
                    help="配合 --compare：把对比结果写成 Markdown 报告")
    args = ap.parse_args()

    if args.compare:
        compare(*args.compare, report=args.report)
        return 0

    rep = build_report(snapshot=args.snapshot)
    if not rep:
        print(f"[error] 数据源缺失：{DB}", file=sys.stderr)
        return 2

    out = Path(args.out_dir) if args.out_dir else OUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    suffix = "" if args.snapshot in ("current", "") else f".{args.snapshot}"
    jp = out / f"unmodeled_backlog{suffix}.json"
    mp = out / f"unmodeled_backlog{suffix}.md"
    jp.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    mp.write_text(to_markdown(rep), encoding="utf-8")
    # 始终刷新「当前」快照，便于前端/API 读取
    if suffix:
        (out / "unmodeled_backlog.json").write_text(
            json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    t = rep["parse_totals"]
    print(f"[ok] {jp.name} / {mp.name}")
    print(f"     未建模条目 {t['unsupported']} · 已映射 tag {t['engine_tags']} · "
          f"含未建模卡 {t['cards_with_unsupported']}")
    for b in rep["batches"]:
        print(f"     {b['id']} {b['name']:<22} {b['entries']:>5} 条 / "
              f"{b['cards']:>4} 卡   {b['share'] * 100:5.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
