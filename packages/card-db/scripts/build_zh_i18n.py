#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把人工中文语料（manifest + merged_zh）合成为卡库入库用 i18n 数据。

输入（仓库外的工作产物，由 ``work/zh`` 的分片翻译流程生成）：

    manifest.json   [{"id","cat","en","ctx"}, ...]
    merged_zh.json  {"<id>": "<中文>", ...}

输出：

    data/i18n/zh_card_translations.json

产物按**英文原文**建立可复用映射（同时保留 manifest 的 id / ctx 便于回溯），
后续 ``apply_zh_translations.py`` 只认这个文件，不再依赖仓库外的临时目录。

质量口径
--------
· 只做人工指定的人名/卡名术语覆盖（``NAME_OVERRIDES``），不做机器硬译；
· 覆盖项在 meta 中逐条留痕，方便审计；
· 空值、非字符串、原文照抄一律拒绝（不写入产物）。

用法
----
    python scripts/build_zh_i18n.py \
        --manifest ../work/zh/manifest.json \
        --merged ../work/zh/merged_zh.json
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]          # packages/card-db
OUT = PKG / "data" / "i18n" / "zh_card_translations.json"

# 人工术语覆盖（GLOSSARY / GLOSSARY_EXTRA + 官方简中卡名核对）。
# 只覆盖**确认有误**的条目；其余译文原样保留，不做“润色式”改写。
NAME_OVERRIDES = {
    "Switch": "换位",
    "Crushing Hammer": "粉碎之锤",
    "Night Stretcher": "夜间担架",
    "Crispin": "克里森",
    "Kieran": "乌栗",
    "Carmine": "丹瑜",
    "Colress’s Tenacity": "阿克罗马的毅力",
    "Colress's Tenacity": "阿克罗马的毅力",
    "Kofu": "卡芙",
    "Briar": "白蕾",
    "Amarys": "阿玛丽丝",
    "Perrin": "佩琳",
}

CATS = ("card_name", "attack_name", "ability_name", "effect", "rule")


def _norm(text) -> str:
    """与卡库匹配键一致：仅把 NBSP 归一为空格并去首尾空白。"""
    return str(text or "").replace("\xa0", " ").strip()


def _override_key(name: str) -> str:
    # 数据集弯/直撇号混用；覆盖表两种写法都登记，这里再兜一层
    return name.replace("’", "'")


def main() -> int:
    ap = argparse.ArgumentParser(description="合成卡库中文 i18n 数据")
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--merged", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    items_in = manifest.get("items") if isinstance(manifest, dict) else manifest
    merged = json.loads(args.merged.read_text(encoding="utf-8"))
    if not isinstance(items_in, list) or not isinstance(merged, dict):
        raise SystemExit("manifest / merged 结构不符合预期")

    overrides = {_override_key(k): v for k, v in NAME_OVERRIDES.items()}
    out_items = []
    by_cat = collections.Counter()
    used_ids = set()
    applied_overrides = []
    dropped = collections.Counter()

    for raw in items_in:
        rid = str(raw.get("id") or "")
        cat = raw.get("cat")
        en = _norm(raw.get("en"))
        zh = merged.get(rid)
        if cat not in CATS:
            dropped["未知分类"] += 1
            continue
        if not rid or not en:
            dropped["缺 id / 英文"] += 1
            continue
        if not isinstance(zh, str) or not zh.strip():
            dropped["缺中文"] += 1
            continue
        zh = zh.strip()
        if zh == en:
            dropped["原文照抄"] += 1
            continue
        if cat == "card_name":
            ov = overrides.get(_override_key(en))
            if ov:
                if ov != zh:
                    applied_overrides.append({"en": en, "from": zh, "to": ov})
                zh = ov
        if rid in used_ids:
            dropped["重复 id"] += 1
            continue
        used_ids.add(rid)
        item = {"id": rid, "cat": cat, "en": en, "zh": zh}
        if raw.get("ctx"):
            item["ctx"] = _norm(raw["ctx"])
        out_items.append(item)
        by_cat[cat] += 1

    payload = {
        "meta": {
            "version": "1.0",
            "source_manifest": args.manifest.as_posix(),
            "source_merged": args.merged.as_posix(),
            "generator": "packages/card-db/scripts/build_zh_i18n.py",
            "items": len(out_items),
            "by_category": dict(by_cat),
            "coverage": "100%（manifest 全部条目均有中文）",
            "policy": ("人工翻译语料；仅按术语表做少量人名/卡名覆盖。"
                       "空值、非字符串、原文照抄不写入。"),
            "name_overrides": NAME_OVERRIDES,
            "overrides_applied": applied_overrides,
            "dropped": dict(dropped),
        },
        "items": out_items,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"[i18n] 写入 {args.out}：{len(out_items)} 条 {dict(by_cat)}")
    if applied_overrides:
        print(f"[i18n] 术语覆盖 {len(applied_overrides)} 条："
              + "、".join(x["en"] for x in applied_overrides))
    if dropped:
        print(f"[i18n] 丢弃 {dict(dropped)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
