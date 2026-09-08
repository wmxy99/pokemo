# -*- coding: utf-8 -*-
"""PokéLab Data · 卡牌数据采集与清洗（loader）

输入源（二选一，均为「仓库外数据」，符合再分发红线）：
  1) Kaggle 模拟赛 EN_Card_Data.csv（用户本地下载，字段需人工对齐一次）
  2) pokemontcg.io v2 API（fetch_pokemontcg.py 拉取为 JSONL）

清洗规范：字段映射 → series/number 唯一键 → rules_text 双轨原文 → 入 cards 表(pending)。
"""
from __future__ import annotations
import json
from pathlib import Path
from .schema import CardRecord


def _text_of(items: list | None) -> str:
    if not items:
        return ""
    parts = []
    for it in items:
        name = it.get("name", "")
        txt = it.get("text", "") or ""
        parts.append(f"[{name}] {txt}".strip())
    return "\n".join(p for p in parts if p)


def pokemontcg_json_to_records(raw: list[dict]) -> list[CardRecord]:
    """pokemontcg.io v2 /v2/cards 的 data 数组 → CardRecord"""
    out = []
    for c in raw:
        rules_txt = "\n".join(
            p for p in [
                _text_of(c.get("abilities")),
                "\n".join(c.get("rules") or []),
                _text_of(c.get("attacks")),
            ] if p
        )
        types = json.dumps(c.get("types") or [], ensure_ascii=False)
        attacks = json.dumps([
            {"name": a.get("name"), "cost": a.get("cost", []),
             "damage": a.get("damage"), "text": a.get("text", "")}
            for a in (c.get("attacks") or [])
        ], ensure_ascii=False)
        st = c.get("set") or {}
        series = st.get("ptcgoCode") or st.get("id") or "unknown"
        rec = CardRecord(
            name=c.get("name", ""),
            series=series,
            number=str(c.get("number", "")),
            supertype=c.get("supertype"),
            types=types,
            hp=c.get("hp"),
            rules_text=rules_txt or None,
            attacks_json=attacks if attacks != "[]" else None,
            source=f"pokemontcg.io/{st.get('id','?')}",
        )
        if rec.name and rec.number:
            out.append(rec)
    return out


def kaggle_csv_to_records(path: Path) -> list[CardRecord]:
    """Kaggle EN_Card_Data.csv 适配（占位实现：列名以实际文件为准，首次运行需人工对齐映射）"""
    import csv
    out = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            # 常见列：name / set / num / type / HP / effect / attack...（按实际表头调整）
            rec = CardRecord(
                name=row.get("name", ""),
                series=row.get("set", "unknown"),
                number=str(row.get("num", "") or row.get("number", "")),
                supertype=row.get("supertype"),
                hp=int(row["hp"]) if str(row.get("hp", "") or "").isdigit() else None,
                rules_text=row.get("rules_text") or row.get("effect") or None,
                source="kaggle-en-card-data",
            )
            if rec.name and rec.number:
                out.append(rec)
    return out
