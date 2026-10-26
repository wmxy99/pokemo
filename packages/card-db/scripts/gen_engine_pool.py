# -*- coding: utf-8 -*-
"""从清洗后的卡牌数据集生成**引擎扩展卡池**（卡池扩容）

背景
----
引擎原生卡池（`engine/data/cards_real.json`，40 张）是**人工逐卡校对**的，保真度高但
覆盖极小（全库 1267 张的 2.8%）。而清洗后的数据集已经带有：
  · 卡面数值（HP / 属性 / 弱点 / 抗性 / 撤退 / 阶段 / 进化前 / 招式费用与伤害）
  · K2 解析链产出的**引擎可执行 tag**（`parse.items[].engine_tags`）
因此可以把「解析链已能完整执行」的卡**批量转成引擎卡池条目**，把可对战卡池从 40 张
扩到数百张——这是「按未建模动作清单分批建模」的自然延伸。

严格准入（**只收无未建模项**的卡）
----------------------------------
1. 该卡有 `parse` 产物，且**所有** item 的 `unsupported` 均为空
   （任何一处未建模 → 整卡不收；避免「部分效果静默丢失」）；
2. 至少有一个招式/特性/卡级效果可执行（不做空壳）；
3. 卡名未与人工卡池重复（人工条目**永远优先**，不被自动条目覆盖）；
4. 宝可梦必须有 HP 与属性；训练家必须有受支持的训练家 tag。

诚实标注
--------
生成的条目带
  · `provenance: "dataset-derived"` + `source`

  · `fidelity: "rule-parser-derived"`（区别于人工卡池的 `engine-mapped`）
装载侧（`engine.reallib`）把扩展池卡标记为 `engine_status="approximated"`，
**不冒充**人工校对卡。

用法
----
    python scripts/gen_engine_pool.py            # 生成并写盘
    python scripts/gen_engine_pool.py --dry-run  # 只看统计
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parents[3]
CLEANED = REPO / "packages" / "card-db" / "data" / "cleaned" / "cards_challenge.json"
POOL_MAIN = REPO / "packages" / "engine" / "engine" / "data" / "cards_real.json"
POOL_EXT = REPO / "packages" / "engine" / "engine" / "data" / "cards_pool_ext.json"

# 引擎支持的训练家 tag 前缀（与 reallib._SUPPORTED_TRAINER_TAGS 对齐）
TRAINER_TAGS = ("poffin", "ultra_ball", "hammer", "boss", "research",
                "shuffle_draw", "switch_self", "recover",
                "discard_search_energy", "search_energy", "candy",
                "search_pokemon", "search_trainer", "search_any",
                # S2 道具（2026-10-26）：附着型训练家
                "tool:",
                # S3 场地（2026-10-27）：全场唯一
                "stadium:")

# 引擎支持的招式效果 tag 前缀（与 battle._apply_attack_effects 对齐）
ATTACK_TAG_PREFIXES = (
    "poison", "burn", "sleep", "paralyze", "confuse", "spread_free:", "spread:",
    "snipe:", "heal:", "self:", "draw:", "draw_to:", "discard:",
    "discard_energy:", "switch_self", "gust", "attach", "move_damage:",
    "recover:", "dmg_plus:", "dmg_zero", "ignore_weakness", "ignore_resistance",
    "coin:", "coin_each:", "shield:", "evolve_from_deck", "mill:",
)

# 引擎支持的被动/field 特性 tag 前缀（与 battle 派发对齐）
ABILITY_TAG_PREFIXES = (
    "recon:", "cursed:", "draw:", "move_damage:", "attach_energy:",
    "ko_draw:", "evolve_attach:", "weak_change:", "passive:",
    # 特性侧**全牌库检索**（2026-09-22）：与 recon（检视牌库顶 N 取 1）语义不同
    "search_pokemon:", "search_trainer:", "search_any:", "search_energy:",
)


def _supported_attack_tags(tags) -> list:
    out = []
    for t in tags or []:
        if any(str(t).startswith(p) for p in ATTACK_TAG_PREFIXES):
            out.append(t)
        else:
            return []          # 出现不支持 → 整条不收
    return out


def _supported_ability_tags(tags, location: str) -> list:
    out = []
    for t in tags or []:
        if any(str(t).startswith(p) for p in ABILITY_TAG_PREFIXES):
            out.append(t)
        else:
            return []
    return out


def _cost_of(cost_list) -> dict:
    out: dict = {}
    for c in cost_list or []:
        out[c] = out.get(c, 0) + 1
    return out


def _items_by_name(parse: dict) -> dict:
    out = {}
    for it in (parse or {}).get("items") or []:
        out.setdefault((it.get("target"), it.get("name")), it)
    return out


def _unsupported_any(parse: dict) -> list:
    bad = []
    for it in (parse or {}).get("items") or []:
        for u in it.get("unsupported") or []:
            bad.append(u)
    return bad


def build_pokemon(card: dict, items: dict, name_key: str) -> Optional[dict]:
    hp = card.get("hp")
    types = card.get("types") or []
    if hp is None or not types:
        return None
    attacks = []
    for a in card.get("attacks") or []:
        it = items.get(("attack", a.get("name")))
        tags = _supported_attack_tags((it or {}).get("engine_tags"))
        if (it or {}).get("unsupported"):
            return None
        effect = a.get("effect") or ""
        if effect and not tags:
            return None                     # 有效果文本但无可执行 tag → 不收
        attacks.append({
            "name": a.get("name"),
            "cost": _cost_of(a.get("cost")),
            "damage": int(a.get("damage") or 0),
            "effects": tags,
            "desc": effect,
            "name_zh": "",
            # desc_zh 留空：本池无人工中文转写，**不把英文塞进中文位**
            # （展示层 display_desc 会自动回退 desc）
            "desc_zh": "",
        })
    abilities = []
    for ab in card.get("abilities") or []:
        it = items.get(("ability", ab.get("name")))
        loc = "hand" if str(ab.get("effect") or "").lower().find("discard this") >= 0 \
            else "field"
        tags = _supported_ability_tags((it or {}).get("engine_tags"), loc)
        if (it or {}).get("unsupported"):
            return None
        if not tags:
            return None                     # 特性无法执行 → 不收（保真优先）
        abilities.append({
            "name": ab.get("name"),
            "name_zh": "",
            "tag": tags[0],
            "location": loc,
            "desc": ab.get("effect") or "",
            # desc_zh 留空（同招式：不把英文塞进中文位）
            "desc_zh": "",
        })
    if not attacks and not abilities:
        return None
    rule = str(card.get("rule") or "").lower()
    if "mega" in rule:
        # Mega ex 被击倒对手取 **3** 张奖赏卡，引擎只建模 1/2 → 不收（避免奖赏记账错误）
        return None
    is_ex = "ex" in rule or str(card.get("name") or "").rstrip().endswith(" ex")
    return {
        "name": card["name"],
        "name_zh": card.get("name_zh") or "",
        "supertype": "Pokemon",
        "stage": int(card.get("stage") or 0),
        "hp": int(hp),
        "types": types,
        "weakness": card.get("weakness"),
        "resistance": card.get("resistance"),
        "retreat": int(card.get("retreat") or 1),
        "is_ex": bool(is_ex),
        "evolves_from": card.get("evolves_from"),
        "attacks": attacks,
        "abilities": abilities,
        "real": {"set": card.get("series"), "number": card.get("number")},
        "provenance": "dataset-derived",
        "fidelity": "rule-parser-derived",
        "source_card_id": card.get("id"),
        "name_key": name_key,
    }


def build_trainer(card: dict, items: dict) -> Optional[dict]:
    it = items.get(("card", card["name"]))
    if (it or {}).get("unsupported"):
        return None
    tags = (it or {}).get("engine_tags") or []
    subtype = str(card.get("subtype") or "")
    is_tool = "tool" in subtype.lower()
    is_stadium = "stadium" in subtype.lower()
    tag = next((t for t in tags
                if any(str(t).startswith(p) for p in TRAINER_TAGS)), None)
    if not tag:
        return None
    if is_stadium:
        # 场地同理：必须**唯一 tag 且是 stadium:***（卡面常夹带触发式副效果）
        st = [t for t in tags if str(t).startswith("stadium:")]
        if len(st) != 1 or len(tags) != 1:
            return None
    if is_tool:
        # ⚠ 道具必须**只认 tool: tag**：道具卡面里常夹带「draw / search」等
        #   触发式子效果，若按普通训练家 tag 准入，就会变成**无条件生效**
        #   （静默加强）。这里要求：唯一的可执行 tag 必须是 tool:*。
        tool_tags = [t for t in tags if str(t).startswith("tool:")]
        if len(tool_tags) != 1 or len(tags) != 1:
            return None
    kind = ("tool" if is_tool
            else "stadium" if is_stadium
            else "supporter" if "supporter" in subtype.lower() else "item")
    return {
        "name": card["name"],
        "name_zh": card.get("name_zh") or "",
        "supertype": "Trainer",
        "kind": kind,
        "tag": tag,
        "desc": card.get("other_text") or "",
        "real": {"set": card.get("series"), "number": card.get("number")},
        "provenance": "dataset-derived",
        "fidelity": "rule-parser-derived",
        "source_card_id": card.get("id"),
    }


def build_energy(card: dict) -> Optional[dict]:
    """仅收**基本能量**（特殊能量含附加规则，引擎未建模）"""
    subtype = str(card.get("subtype") or "")
    if "basic energy" not in subtype.lower():
        return None
    etype = None
    types = card.get("types") or []
    if types:
        etype = types[0]
    else:
        import re as _re
        m = _re.search(r"basic\s+([a-z]+)\s+energy", card["name"], _re.I)
        if m:
            etype = m.group(1).lower()
    if not etype:
        return None
    return {
        "name": card["name"],
        "name_zh": card.get("name_zh") or "",
        "supertype": "Energy",
        "type": etype,
        "desc": "",
        "real": {"set": card.get("series"), "number": card.get("number")},
        "provenance": "dataset-derived",
        "fidelity": "rule-parser-derived",
        "source_card_id": card.get("id"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="生成引擎扩展卡池（卡池扩容）")
    ap.add_argument("--cleaned", type=Path, default=CLEANED)
    ap.add_argument("--pool", type=Path, default=POOL_MAIN, help="人工卡池（去重基准）")
    ap.add_argument("--out", type=Path, default=POOL_EXT)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.cleaned.exists():
        raise SystemExit(f"缺少清洗产物：{args.cleaned}")
    data = json.loads(args.cleaned.read_text(encoding="utf-8"))
    pool = json.loads(args.pool.read_text(encoding="utf-8"))
    manual = {c["name"] for c in pool.get("cards") or []}

    kept, skipped = [], Counter()
    by_kind = Counter()
    for card in data.get("cards") or []:
        name = card.get("name")
        if name in manual:
            skipped["人工卡池已有（人工优先）"] += 1
            continue
        parse = card.get("parse")
        if not parse:
            skipped["无解析产物"] += 1
            continue
        bad = _unsupported_any(parse)
        if bad:
            skipped["存在未建模项（整卡不收）"] += 1
            continue
        items = _items_by_name(parse)
        st = card.get("supertype")
        entry = None
        if st == "Pokemon":
            entry = build_pokemon(card, items, card.get("name_key") or "")
        elif st == "Trainer":
            entry = build_trainer(card, items)
        elif st == "Energy":
            entry = build_energy(card)
        if entry is None:
            skipped[f"{st} 不可执行"] += 1
            continue
        kept.append(entry)
        by_kind[st] += 1

    payload = {
        "meta": {
            "version": "1.0",
            "date": "2026-09-14",
            "milestone": "卡池扩容：由清洗数据集 + K2 解析链生成扩展卡池",
            "source": "packages/card-db/data/cleaned/cards_challenge.json",
            "generator": "packages/card-db/scripts/gen_engine_pool.py",
            "provenance": "dataset-derived",
            "fidelity": "rule-parser-derived",
            "admission": ("严格准入：仅收录「所有解析条目均已映射到引擎可执行 tag」的卡；"
                          "任一处未建模即整卡不收（避免部分效果静默丢失）"),
            "precedence": "人工卡池 cards_real.json 优先，同名卡不覆盖",
            "honesty": ("本池条目为**规则解析链推导**（非人工逐卡校对），装载侧标记为 "
                        "engine_status=approximated；卡面数值来自清洗数据集，"
                        "效果保真度取决于解析链召回率，差异见未建模动作清单"),
            "counts": {"kept": len(kept), **{f"by_{k}": v for k, v in by_kind.items()},
                       "skipped": dict(skipped)},
        },
        "cards": kept,
    }
    print(f"[pool] 收录 {len(kept)} 张"
          f"（{'宝可梦' if by_kind['Pokemon'] else ''} "
          f"{by_kind['Pokemon']} / 训练家 {by_kind['Trainer']} / "
          f"能量 {by_kind['Energy']}）")
    for k, v in skipped.most_common():
        print(f"[pool]   跳过 {k}：{v}")
    if args.dry_run:
        print("[pool] dry-run：未写盘")
        return 0
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    print(f"[out]  {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
