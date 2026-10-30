#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把中文语料写入已清洗卡表（``cards_challenge.json``）。

产物
----
· 卡表 JSON：补齐 ``name_zh`` / 招式与特性的 ``name_zh`` ``effect_zh`` /
  ``other_text_zh`` / ``rule_zh``，并刷新 ``stats`` / ``meta.i18n``；
· 中文报告：``data/cleaned/zh_translation_report.md``（人读，含冲突与校验）。

不做的事
--------
· 不重跑 K2 解析链（解析产物与中文互相独立；解析升级请走
  ``import_challenge_dataset.py --reparse-only``）；
· 不写 SQLite（由 ``import_challenge_dataset.py --db-only`` 灌库）；
· 缺译文不硬译：留空由展示层回退英文，并在报告中列出。

用法
----
    python packages/card-db/scripts/apply_zh_translations.py
    python packages/card-db/scripts/apply_zh_translations.py --dry-run
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from collections import Counter
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]          # packages/card-db
sys.path.insert(0, str(Path(__file__).resolve().parent))

from zh_i18n import I18N_FILE, apply_and_report, validate_cards  # noqa: E402

DEFAULT_JSON = PKG / "data" / "cleaned" / "cards_challenge.json"
DEFAULT_REPORT = PKG / "data" / "cleaned" / "zh_translation_report.md"


def _counts(cards: list[dict]) -> dict:
    return {
        "card_name": sum(1 for c in cards if (c.get("name_zh") or "").strip()),
        "attack_name": sum(1 for c in cards for a in c.get("attacks") or []
                           if (a.get("name_zh") or "").strip()),
        "attack_effect": sum(1 for c in cards for a in c.get("attacks") or []
                             if (a.get("effect_zh") or "").strip()),
        "ability_name": sum(1 for c in cards for a in c.get("abilities") or []
                            if (a.get("name_zh") or "").strip()),
        "ability_effect": sum(1 for c in cards for a in c.get("abilities") or []
                              if (a.get("effect_zh") or "").strip()),
        "other_text": sum(1 for c in cards
                          if (c.get("other_text_zh") or "").strip()),
        "rule": sum(1 for c in cards if (c.get("rule_zh") or "").strip()),
    }


def write_report(path: Path, payload: dict, applied: dict, validation: dict,
                 before: dict, after: dict, i18n_meta: dict) -> None:
    st = applied["stats"]
    vst = validation["stats"]
    L = [
        "# 卡库中文本地化报告",
        "",
        f"- 生成时间：{_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "- 卡表：`data/cleaned/cards_challenge.json`",
        "- 语料：`data/i18n/zh_card_translations.json`"
        f"（{i18n_meta.get('items', '?')} 条；来源 manifest + merged_zh）",
        "- 生成器：`packages/card-db/scripts/apply_zh_translations.py`",
        "",
        "## 1. 字段覆盖",
        "",
        "| 字段 | 写入前 | 写入后 | 本次写入 | 缺译文 |",
        "|---|---:|---:|---:|---:|",
    ]
    rows = [
        ("卡名 `name_zh`", "card_name", "card_name", "card_name_missing"),
        ("招式名 `attacks[].name_zh`", "attack_name", "attack_name", "attack_name_missing"),
        ("招式效果 `attacks[].effect_zh`", "attack_effect", "attack_effect", "attack_effect_missing"),
        ("特性名 `abilities[].name_zh`", "ability_name", "ability_name", "ability_name_missing"),
        ("特性效果 `abilities[].effect_zh`", "ability_effect", "ability_effect", "ability_effect_missing"),
        ("其他文本 `other_text_zh`", "other_text", "other_text", "other_text_missing"),
        ("规则文本 `rule_zh`", "rule", "rule", "rule_missing"),
    ]
    for label, bkey, akey, mkey in rows:
        L.append(f"| {label} | {before.get(bkey, 0)} | {after.get(akey, 0)} | "
                 f"{st.get(akey, 0)} | {st.get(mkey, 0)} |")
    L += [
        "",
        "> 说明：英文字段仍是数据键；缺译文时前端回退英文，**不会**把英文塞进中文位。",
        "",
        "## 2. 术语覆盖（人工指定）",
        "",
    ]
    ov = i18n_meta.get("overrides_applied") or []
    if ov:
        L += ["| 英文 | 语料原译 | 统一为 |", "|---|---|---|"]
        for x in ov:
            L.append(f"| {x.get('en')} | {x.get('from')} | {x.get('to')} |")
    else:
        L.append("（本次无语料覆盖）")
    L += [
        "",
        "## 3. 与既有中文名的差异（已按语料统一）",
        "",
    ]
    conflicts = applied.get("conflicts") or []
    if conflicts:
        L += ["| 卡名 | 原值 | 新值 | 系列/编号 |", "|---|---|---|---|"]
        for x in conflicts[:80]:
            L.append(f"| {x.get('card')} | {x.get('old')} | {x.get('new')} | "
                     f"{x.get('set')} {x.get('number')} |")
        if len(conflicts) > 80:
            L.append(f"| ……其余 {len(conflicts) - 80} 条见 JSON 统计 | | | |")
    else:
        L.append("（无差异）")
    L += [
        "",
        "## 4. 校验",
        "",
        f"- 已检查字段：**{vst.get('checked', 0)}**",
        f"- 空译文：{vst.get('empty', 0)}",
        f"- 无中文字符：{vst.get('no_cjk', 0)}",
        f"- 丢失英文数字：{vst.get('lost_numbers', 0)}",
        f"- `{{X}}` 符号多重集不一致：{vst.get('symbol_mismatch', 0)}",
        "",
        "> 符号不一致多为中文习惯写法（如 `宝可梦 ex` 不再带花括号），"
        "不等于规则错误；数字校验只要求英文中出现的数字在中文里仍存在，"
        "不把「a/an → 1 张」这类合理补足算作异常。",
        "",
    ]
    problems = validation.get("problems") or []
    if problems:
        L += ["### 4.1 需人工复核的条目（最多 80 条）", "",
              "| 类型 | 字段 | 卡名 | 详情 |", "|---|---|---|---|"]
        for p in problems[:80]:
            detail = p.get("kind")
            if p.get("kind") == "lost_numbers":
                detail = f"缺少数字 {p.get('lost')}"
            elif p.get("kind") == "symbol_mismatch":
                detail = f"原文 {p.get('en')} / 译文 {p.get('zh')}"
            elif p.get("zh"):
                detail = str(p.get("zh"))[:60]
            elif p.get("en"):
                detail = "英文：" + str(p.get("en"))[:60]
            L.append(f"| {p.get('kind')} | {p.get('field')} | {p.get('card')} | {detail} |")
        if len(problems) > 80:
            L.append(f"| …… | | | 其余 {len(problems) - 80} 条 |")
        L.append("")
    else:
        L += ["**全部字段通过校验。**", ""]

    L += [
        "## 5. 已知源语料质量提示（未自动改动）",
        "",
        "源语料 `merge_report.md` 记录的质量异常（英文残留、`{ex}` 写法、"
        "数字多出、术语表差异等）不会被本脚本静默改写：",
        "",
        "- 英文残留多为专有名词（`EX` / `PP` / `DNA` / `ACE SPEC`），可接受；",
        "- `宝可梦 ex` 等中文写法与英文 `{ex}` 的花括号差异属展示风格；",
        "- 数字多出通常是中文量词补足（`a card` → `1 张卡`）；",
        "- 术语表冲突按 `GLOSSARY_EXTRA.md` 优先，已覆盖的 10 条见 §2。",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="把中文写入卡表 JSON")
    ap.add_argument("--in-json", type=Path, default=DEFAULT_JSON)
    ap.add_argument("--out-json", type=Path, default=None,
                    help="默认原地写回 --in-json")
    ap.add_argument("--i18n", type=Path, default=I18N_FILE)
    ap.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    src = args.in_json
    if not src.exists():
        raise SystemExit(f"卡表不存在：{src}")
    payload = json.loads(src.read_text(encoding="utf-8"))
    cards = payload.get("cards") or []
    if not cards:
        raise SystemExit(f"卡表没有 cards：{src}")

    before = _counts(cards)
    res = apply_and_report(cards, args.i18n)
    after = _counts(cards)
    applied = res["applied"]
    validation = res["validation"]

    # 刷新统计（不改 parse 结果）
    stats = dict(payload.get("stats") or {})
    stats["with_zh"] = after["card_name"]
    stats["with_zh_effects"] = (
        after["attack_effect"] + after["ability_effect"] + after["other_text"])
    stats["i18n"] = {
        "source": "packages/card-db/data/i18n/zh_card_translations.json",
        "cards_with_name_zh": after["card_name"],
        "attack_names_zh": after["attack_name"],
        "attack_effects_zh": after["attack_effect"],
        "ability_names_zh": after["ability_name"],
        "ability_effects_zh": after["ability_effect"],
        "other_texts_zh": after["other_text"],
        "rules_zh": after["rule"],
        "missing": applied["stats"],
        "validation": validation["stats"],
    }
    payload["stats"] = stats
    meta = dict(payload.get("meta") or {})
    meta["i18n"] = {
        "version": (res["i18n"].get("meta") or {}).get("version"),
        "items": (res["i18n"].get("meta") or {}).get("items"),
        "source": "packages/card-db/data/i18n/zh_card_translations.json",
        "applied_at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "policy": "英文为数据键；缺译文回退英文，不硬译",
    }
    payload["meta"] = meta

    write_report(args.report, payload, applied, validation, before, after,
                 res["i18n"].get("meta") or {})
    if args.dry_run:
        print("[zh] dry-run：未写盘")
    else:
        out = args.out_json or src
        out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8")
        print(f"[zh] 写入 {out}")
    print(f"[zh] 卡名 {after['card_name']}/{len(cards)} · "
          f"招式效果 {after['attack_effect']} · 特性效果 {after['ability_effect']} · "
          f"其他文本 {after['other_text']} · 规则 {after['rule']}")
    print(f"[zh] 校验：检查 {validation['stats'].get('checked', 0)} 字段 · "
          f"空 {validation['stats'].get('empty', 0)} · "
          f"丢数字 {validation['stats'].get('lost_numbers', 0)}")
    if applied["conflicts"]:
        print(f"[zh] 统一既有中文名 {len(applied['conflicts'])} 处（见报告）")
    print(f"[out] {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
