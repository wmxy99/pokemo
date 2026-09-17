# -*- coding: utf-8 -*-
"""卡库浏览 API（PRD F2 卡库 / M1 卡池补齐）

数据源：``packages/card-db/data/db/pokelab.db`` 的 ``cards`` 表
（由 ``packages/card-db/scripts/import_challenge_dataset.py`` 清洗入库）。

设计要点
--------
- **只读**：卡库是数据资产，接口不做写入；导入走 ETL 脚本。
- **内存缓存 + mtime 失效**：1267 行全量驻留内存，按文件修改时间自动重载，
  避免每次请求解析 JSON。
- **名称匹配用 ``name_key``**：数据集内撇号混用（``N’s`` vs ``N's``），
  归一匹配键让搜索/筛选不受标点差异影响。
- **``engine_ready`` 如实暴露**：卡可浏览 ≠ 引擎可对战（1241 张未建模），
  前端据此显示灰名单，不误导用户。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from .auth import Principal, current_user
from .compliance import (AI_NOTICE, ATTRIBUTION, DATA_DECLARATION)

router = APIRouter(prefix="/cards", tags=["library"])

_REPO = Path(__file__).resolve().parents[3]          # pokelab-ai
_DB_CANDIDATES = (
    _REPO / "packages" / "card-db" / "data" / "db" / "pokelab.db",
)

_lock = threading.Lock()
_cache: dict = {"mtime": None, "cards": []}
_warned: set = set()


def _candidates() -> tuple:
    """卡库位置：`POKELAB_CARD_DB` 覆盖 → 仓库内标准路径

    历史：这里曾把**仓库外的旧数据目录**（`../pokelab-data/...`，仅 3 张卡且 schema 已漂移）
    作为回退。风险是**静默降级** —— 主库缺失时会「看起来正常但只有 3 张卡」。
    现改为：只认标准路径（或显式环境变量），缺失时**打出明确告警**而不是悄悄换源。
    """
    env = os.environ.get("POKELAB_CARD_DB")
    return ((Path(env),) if env else ()) + _DB_CANDIDATES


def _db_path() -> Optional[Path]:
    for p in _candidates():
        if p.exists():
            return p
    if "missing" not in _warned:
        _warned.add("missing")
        print("[library] ⚠ 未找到卡库数据库，/cards 将返回空列表。"
              f"期望路径：{[str(p) for p in _candidates()]}；"
              "可用 POKELAB_CARD_DB 指定，或按 packages/card-db/README.md 导入数据。",
              file=sys.stderr)
    return None


def _norm_key(name: str) -> str:
    """匹配键：小写、撇号归一、去非字母数字（与 ETL 的 name_key 同规则）"""
    s = (name or "").strip().lower()
    s = s.replace("’", "'").replace("‘", "'").replace("`", "'")
    return "".join(ch for ch in s if ch.isalnum())


def _load() -> list[dict]:
    """读 cards 表 → 规范化 dict 列表（带 mtime 缓存）"""
    dbp = _db_path()
    if dbp is None:
        return []
    mtime = dbp.stat().st_mtime
    with _lock:
        if _cache["mtime"] == mtime and _cache["cards"]:
            return _cache["cards"]
        con = sqlite3.connect(f"file:{dbp}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT id,name,series,number,supertype,types,hp,rules_text,"
            "attacks_json,effects_json,status,source FROM cards"
        ).fetchall()
        con.close()
        cards: list[dict] = []
        for r in rows:
            d = dict(r)
            d["types"] = json.loads(d.get("types") or "[]")
            d["hp"] = d.get("hp")
            lib = {}
            try:
                lib = (json.loads(d.get("effects_json") or "{}")
                       .get("library") or {})
            except Exception:                                  # noqa: BLE001
                lib = {}
            aj = {}
            try:
                aj = json.loads(d.get("attacks_json") or "{}") or {}
            except Exception:                                  # noqa: BLE001
                aj = {}
            d["library"] = lib
            # 合规 6.1：机器/AI 解析结果单独取出并带标识
            try:
                d["parse"] = (json.loads(d.get("effects_json") or "{}")
                              .get("parse")) or None
            except Exception:                                  # noqa: BLE001
                d["parse"] = None
            d["ai_generated"] = bool(d.get("parse"))
            d["image_url"] = (lib.get("image_url") or None)
            d["attacks"] = aj.get("attacks") or []
            d["abilities"] = aj.get("abilities") or []
            d["name_key"] = lib.get("name_key") or _norm_key(d["name"])
            d["name_zh"] = lib.get("name_zh") or ""
            d["name_ja"] = lib.get("name_ja") or ""
            d["subtype"] = lib.get("subtype") or ""
            d["engine_ready"] = bool(lib.get("engine_ready"))
            # 诚实口径（C5）：mapped | approximated | unsupported | unmatched
            d["engine_status"] = lib.get("engine_status") or (
                "mapped" if d["engine_ready"] else "unmatched")
            d["engine_note"] = lib.get("engine_note") or ""
            d["is_basic"] = bool(lib.get("is_basic"))
            d["is_basic_energy"] = bool(lib.get("is_basic_energy"))
            d["attacks_count"] = len(d["attacks"])
            d["abilities_count"] = len(d["abilities"])
            d["display"] = d["name_zh"] or d["name"]
            cards.append(d)
        _cache["mtime"] = mtime
        _cache["cards"] = cards
        return cards


def _brief(c: dict) -> dict:
    """列表项（瘦身：不含完整效果文本，减少载荷）"""
    return {
        "id": c["id"], "name": c["name"], "display": c["display"],
        "name_zh": c["name_zh"], "name_ja": c["name_ja"],
        "series": c["series"], "number": c["number"],
        "supertype": c["supertype"], "subtype": c["subtype"],
        "types": c["types"], "hp": c["hp"],
        "attacks_count": c["attacks_count"],
        "abilities_count": c["abilities_count"],
        "engine_ready": c["engine_ready"],
        "engine_status": c.get("engine_status", "unmatched"),
        "engine_note": c.get("engine_note", ""),
        "image_url": c.get("image_url"),
        "ai_generated": c["ai_generated"],
    }


def _detail(c: dict) -> dict:
    out = dict(_brief(c))
    out.update({
        "rules_text": c["rules_text"],
        "attacks": c["attacks"],
        "abilities": c["abilities"],
        "library": c["library"],
        "status": c["status"], "source": c["source"],
        "attribution": ATTRIBUTION,
    })
    if c.get("parse"):
        p_ = c["parse"]
        out["effects_parsed"] = {
            "ai_generated": True,
            "generated_by": p_.get("generated_by"),
            "ai_notice": p_.get("ai_notice") or AI_NOTICE,
            "specs": p_.get("specs") or [],
            "items": p_.get("items") or [],
            "engine_tags": p_.get("engine_tags") or [],
            "unsupported": p_.get("unsupported") or [],
            "errors": p_.get("errors") or [],
            "note": "机器/AI 解析，仅供研究参考；不代表官方规则",
        }
    else:
        out["effects_parsed"] = None
    return out


@router.get("")
def list_cards(q: str = Query("", description="卡名关键字（中/英/日，忽略标点）"),
               supertype: str = Query("", description="Pokemon|Trainer|Energy"),
               type_: str = Query("", alias="type", description="能量/属性代码"),
               series: str = Query("", description="系列代号，如 TWM"),
               subtype: str = Query("", description="Basic|Stage 1|Item|Supporter…"),
               engine_ready: Optional[bool] = Query(
                   None, description="true=仅引擎可对战；false=仅未建模"),
               sort: str = Query("id", description="id|name|hp|series"),
               page: int = Query(1, ge=1),
               page_size: int = Query(30, ge=1, le=200),
               p: Principal = Depends(current_user)):
    """卡库列表：关键字搜索 + 多维筛选 + 分页"""
    cards = _load()
    if not cards:
        raise HTTPException(503, "卡库为空：请先运行 "
                                 "packages/card-db/scripts/import_challenge_dataset.py")

    kw = _norm_key(q) if q else ""
    st = (supertype or "").strip()
    ty = (type_ or "").strip().lower()
    se = (series or "").strip().upper()
    sub = (subtype or "").strip().lower()

    def hit(c: dict) -> bool:
        if kw:
            hay = {_norm_key(c["name"]), _norm_key(c["name_zh"]),
                   _norm_key(c["name_ja"]), c["name_key"]}
            if not any(kw in h for h in hay if h):
                return False
        if st and c["supertype"] != st:
            return False
        if ty and ty not in [x.lower() for x in c["types"]]:
            return False
        if se and c["series"].upper() != se:
            return False
        if sub and sub not in c["subtype"].lower():
            return False
        if engine_ready is not None and c["engine_ready"] != engine_ready:
            return False
        return True

    rows = [c for c in cards if hit(c)]
    if sort == "name":
        rows.sort(key=lambda c: c["name"].lower())
    elif sort == "hp":
        rows.sort(key=lambda c: (c["hp"] is None, -(c["hp"] or 0)))
    elif sort == "series":
        rows.sort(key=lambda c: (c["series"], c["number"]))
    else:
        rows.sort(key=lambda c: c["id"])

    total = len(rows)
    start = (page - 1) * page_size
    items = [_brief(c) for c in rows[start:start + page_size]]
    return {
        "total": total, "page": page, "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
        "items": items,
        "filters": {"q": q, "supertype": supertype, "type": type_,
                    "series": series, "subtype": subtype,
                    "engine_ready": engine_ready, "sort": sort},
        "declaration": DATA_DECLARATION,
        "attribution": ATTRIBUTION,
    }


@router.get("/stats")
def library_stats(p: Principal = Depends(current_user)):
    """卡库总览：总量、构成、系列分布、引擎覆盖"""
    cards = _load()
    if not cards:
        raise HTTPException(503, "卡库为空：请先运行导入脚本")
    def _count(key):
        out: dict = {}
        for c in cards:
            k = c[key]
            if isinstance(k, list):
                for x in k:
                    out[x] = out.get(x, 0) + 1
            else:
                out[k or "（无）"] = out.get(k or "（无）", 0) + 1
        return dict(sorted(out.items(), key=lambda x: -x[1]))

    ready = sum(1 for c in cards if c["engine_ready"])
    by_status: dict = {}
    for c in cards:
        k = c.get("engine_status") or "unmatched"
        by_status[k] = by_status.get(k, 0) + 1
    return {
        "total": len(cards),
        "by_supertype": _count("supertype"),
        "by_subtype": _count("subtype"),
        "by_type": _count("types"),
        "by_series": dict(list(_count("series").items())[:30]),
        "attacks_total": sum(c["attacks_count"] for c in cards),
        "abilities_total": sum(c["abilities_count"] for c in cards),
        "engine_ready": ready,
        "engine_pending": len(cards) - ready,
        "engine_coverage": round(ready / len(cards), 4),
        # 细分口径（C5）：mapped=完整可对战 / approximated=灰名单近似 / unsupported / unmatched
        "engine_by_status": by_status,
        "with_zh": sum(1 for c in cards if c["name_zh"]),
        "note": ("engine_ready=false 的卡可浏览/可组卡但**不可对战**"
                 "（引擎未建模，PRD C5 差异透明）"),
        "ai_parsed_cards": sum(1 for c in cards if c.get("ai_generated")),
        "ai_notice": AI_NOTICE,
        "attribution": ATTRIBUTION,
        "with_image": sum(1 for c in cards if c.get("image_url")),
    }


@router.get("/{card_id}")
def card_detail(card_id: int, p: Principal = Depends(current_user)):
    """卡详情：招式（费用/伤害/效果）、特性、卡面元数据"""
    cards = _load()
    for c in cards:
        if c["id"] == card_id:
            return _detail(c)
    raise HTTPException(404, f"卡 {card_id} 不存在")
