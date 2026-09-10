# -*- coding: utf-8 -*-
"""PokéLab AI API · 卡组管理路由（PRD F3 · M1/M4）

- POST /decks/import  ：粘贴 Limitless 兼容文本 → 解析 + 合法性校验（60 张上限 /
  同名 ≤4（基本能量豁免）/ 必须含基础宝可梦）→ 落库（owner 隔离）
- GET  /decks         ：我的卡组
- GET  /decks/presets ：预设 Meta 对手池 A1–A4（PRD §10，含数据依据标注）
- POST /decks/{id}/opening ：起手 1,000 次模拟（卡手率/分布）
- GET  /decks/{id}/export  ：导出解析后的卡表文本（可再导入，保证往返一致）

校验能力边界（诚实标注）：
  卡名匹配 `engine.decklib` 演示卡池（v0.4：能量/训练家/宝可梦 stage 字段齐全），
  卡库（card-db, 3 张 P0 样本）作为补充来源；未收录卡 → warning 并标记不可对战
  （差异透明，PRD C5）。无官方卡图，全部附 Limitless 外链（PRD C2）。
"""
from __future__ import annotations
import json
import random
import re
from collections import Counter
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from . import store
from .auth import Principal, current_user, ensure_owner

router = APIRouter(prefix="/decks", tags=["deck"])

MAX_CARDS = 60
MAX_SAME = 4
# Limitless 行格式：`4 Dreepy TWM 128` / `3 Ultra Ball SVI 196 PH`
LINE_RE = re.compile(r"^\s*(\d+)\s+(.+?)\s+([A-Z]{2,5})\s+(\d+[a-zA-Z]?)\s*(\*|PH)?\s*$")


# ---------------- 卡池（demo decks 全量去重 + card-db 补充） ----------------
_POOL: dict | None = None


def _pool() -> dict:
    """name(lower) -> meta{kind,is_basic,name,stage,is_ex}"""
    global _POOL
    if _POOL is not None:
        return _POOL
    import sys
    repo = Path(__file__).resolve().parents[3]
    for pkg in ("engine", "card-db"):
        p = repo / "packages" / pkg
        if p.exists():
            sys.path.insert(0, str(p))
    out: dict = {}
    try:
        from engine import decklib
        names = [n for n in dir(decklib) if n.startswith("deck_")]
        for fn in names:
            try:
                deck = getattr(decklib, fn)()
            except Exception:
                continue
            for c in deck:
                pdef = getattr(c, "pdef", None)
                tdef = getattr(c, "tdef", None)
                edef = getattr(c, "edef", None)
                if pdef is not None:
                    out[pdef.name.lower()] = {
                        "kind": "pokemon", "name": pdef.name,
                        "stage": getattr(pdef, "stage", 0),
                        "is_ex": bool(getattr(pdef, "is_ex", False)),
                        "hp": getattr(pdef, "hp", None),
                        "is_basic": getattr(pdef, "stage", 0) == 0,
                        "source": "decklib",
                    }
                elif tdef is not None:
                    out[tdef.name.lower()] = {
                        "kind": "trainer", "name": tdef.name,
                        "is_basic": False, "source": "decklib"}
                elif edef is not None:
                    nm = getattr(edef, "name", "Energy")
                    out[nm.lower()] = {
                        "kind": "energy", "name": nm, "is_basic": False,
                        "is_basic_energy": True, "source": "decklib"}
    except Exception:
        pass
    # card-db 补充（P0 仅 3 张样本，命中即用其 supertype）
    try:
        import sqlite3
        dbp = repo.parent / "pokelab-data" / "data" / "db" / "pokelab.db"
        if dbp.exists():
            con = sqlite3.connect(str(dbp))
            for name, st in con.execute("SELECT name,supertype FROM cards"):
                key = (name or "").lower()
                if key and key not in out:
                    kind = {"Pokemon": "pokemon", "Trainer": "trainer",
                            "Energy": "energy"}.get(st, "unknown")
                    out[key] = {"kind": kind, "name": name, "source": "card-db",
                                "is_basic": kind == "pokemon"}
            con.close()
    except Exception:
        pass
    _POOL = out
    return out


# ---------------- 解析与校验 ----------------
def _parse(text: str) -> tuple[list, list]:
    """→ (entries, errors)：entries = {count,name,set,num,url,meta,matched}"""
    entries, errs = [], []
    for i, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        m = LINE_RE.match(line)
        if not m:
            # 容错：只写卡名（默认 1 张）
            if re.match(r"^\d+\s+\S", line):
                errs.append(f"第 {i} 行无法解析（格式：`数量 卡名 系列 编号`）：{line}")
            else:
                entries.append({"count": 1, "name": line, "set": None,
                                "num": None, "raw": line})
            continue
        cnt, name, setc, num = int(m.group(1)), m.group(2).strip(), m.group(3), m.group(4)
        entries.append({"count": cnt, "name": name, "set": setc, "num": num,
                        "raw": line})
    # 匹配卡池 + 外链
    pool = _pool()
    for e in entries:
        meta = pool.get(e["name"].lower())
        e["matched"] = bool(meta)
        e["meta"] = meta
        if e.get("set") and e.get("num"):
            e["url"] = f"https://limitlesstcg.com/cards/{e['set']}/{e['num']}"
        else:
            e["url"] = "https://limitlesstcg.com/cards?q=" + \
                e["name"].replace(" ", "+")
    return entries, errs


def validate(text: str) -> dict:
    """F3 校验：返回 {ok, errors[], warnings[], parsed[], summary{}}"""
    entries, errors = _parse(text)
    warnings = []
    if not entries:
        return {"ok": False, "errors": ["卡表为空或格式无法识别"],
                "warnings": [], "parsed": [], "summary": {}}
    total = sum(e["count"] for e in entries)
    by_name = Counter()
    for e in entries:
        by_name[e["name"].lower()] += e["count"]
    meta_of = {e["name"].lower(): e["meta"] for e in entries}

    def _is_basic_energy(key: str) -> bool:
        m = meta_of.get(key) or {}
        if m.get("is_basic_energy"):
            return True
        return m.get("kind") == "energy" and "energy" in key

    for key, n in by_name.items():
        if n > MAX_SAME and not _is_basic_energy(key):
            errors.append(f"「{key}」超过同名上限 {MAX_SAME}（当前 {n}）")
    if total > MAX_CARDS:
        errors.append(f"张数 {total} 超过上限 {MAX_CARDS}")
    elif total < MAX_CARDS:
        warnings.append(f"张数 {total} < {MAX_CARDS}（标准构筑为 60，可继续但建议补齐）")

    basics = [k for k in by_name
              if (meta_of.get(k) or {}).get("is_basic")]
    if not basics:
        unknown_pkm = [k for k in by_name
                       if not (meta_of.get(k) or {}).get("matched")
                       or (meta_of.get(k) or {}).get("kind") == "pokemon"]
        if unknown_pkm:
            warnings.append("未能确认基础宝可梦（卡库未收录，判定为启发式；"
                            "请人工确认，PRD C5 差异透明）")
        else:
            errors.append("卡组必须含至少 1 张基础宝可梦")

    unmatched = sorted({k for k, m in meta_of.items() if not m})
    if unmatched:
        warnings.append("未收录卡（灰名单/卡库未接入 F2）：" +
                        "、".join(unmatched[:8]) +
                        ("…" if len(unmatched) > 8 else "") +
                        " → 含此类卡的卡组暂不可对战")

    kinds = Counter((meta_of[k] or {}).get("kind", "unknown") for k in by_name)
    summary = {
        "total": total, "unique": len(by_name),
        "pokemon": kinds.get("pokemon", 0),
        "trainer": kinds.get("trainer", 0),
        "energy": kinds.get("energy", 0),
        "unknown": kinds.get("unknown", 0),
        "basics": len(basics),
        "battle_ready": bool(basics) and not unmatched,
    }
    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "parsed": entries, "summary": summary}


# ---------------- 路由 ----------------
class ImportReq(BaseModel):
    text: str = Field(min_length=2, max_length=8000)
    name: str = Field(default="我的卡组", max_length=40)


@router.post("/import")
def import_deck(req: ImportReq, p: Principal = Depends(current_user)):
    res = validate(req.text)
    did = store.new_id("deck")
    store.save_deck({"id": did, "owner": p.name, "name": req.name,
                     "text": req.text, "valid": res["ok"],
                     "cards": res["parsed"], "summary": res["summary"],
                     "created": store.now()})
    return {"id": did, "owner": p.name, "name": req.name, **res}


@router.get("")
def my_decks(p: Principal = Depends(current_user)):
    return {"decks": store.list_decks(p.name)}


@router.get("/presets")
def presets(p: Principal = Depends(current_user)):
    """预设 Meta 对手池 A1–A4（PRD §10；卡池待 M1 补齐，当前以 demo 卡组代表）"""
    return {"presets": [
        {"id": "A1", "label": "Dragapult ex / Dusknoir", "demo_deck": "draco",
         "note": "Worlds 使用率 43.0% · 赛期 Meta 49.2%（Limitless）",
         "battle_ready": True},
        {"id": "A2", "label": "Dragapult ex / Blaziken ex", "demo_deck": "draco",
         "note": "2026.7–8 Tier1 最强变体（tcgcontender）", "battle_ready": True},
        {"id": "A3", "label": "N's Zoroark ex", "demo_deck": "ability_demo",
         "note": "赛期 Meta 8.0%；主流中少数优开 Dragapult", "battle_ready": True},
        {"id": "A4", "label": "Basic Box（Ogerpon/Clefairy）",
         "demo_deck": "psychic", "note": "Worlds 使用率 9.4% · 胜率 49.9%",
         "battle_ready": True},
    ], "disclaimer": "卡池以 demo 卡组代表（M1 卡池补齐后替换）；差异见 C5 声明"}


@router.get("/{did}")
def get_deck(did: str, p: Principal = Depends(current_user)):
    d = store.get_deck(did)
    if not d:
        raise HTTPException(404, "卡组不存在")
    ensure_owner(d["owner"], p)
    return d


@router.post("/{did}/opening")
def opening(did: str, trials: int = Query(1000, ge=10, le=10000),
            seed: int = Query(20260910), p: Principal = Depends(current_user)):
    """起手模拟：抽 7 张，统计卡手率（无基础宝可梦）与分布（F3）"""
    d = store.get_deck(did)
    if not d:
        raise HTTPException(404, "卡组不存在")
    ensure_owner(d["owner"], p)
    cards = d["cards"] or []
    if not cards:
        raise HTTPException(422, "卡表为空")
    pool = []
    for e in cards:
        meta = e.get("meta") or {}
        pool += [(e["name"], bool(meta.get("is_basic")),
                  meta.get("kind", "unknown"))] * e["count"]
    if len(pool) < 7:
        raise HTTPException(422, f"卡组仅 {len(pool)} 张，无法抽 7 张起手")
    rng = random.Random(seed)
    brick = 0
    basic_hist = Counter()
    energy_hist = Counter()
    for _ in range(trials):
        hand = rng.sample(pool, 7)
        nb = sum(1 for _, b, _ in hand if b)
        ne = sum(1 for _, _, k in hand if k == "energy")
        basic_hist[nb] += 1
        energy_hist[ne] += 1
        if nb == 0:
            brick += 1
    return {"deck_id": did, "trials": trials, "seed": seed,
            "brick_rate": round(brick / trials, 4),
            "avg_basics": round(sum(k * v for k, v in basic_hist.items())
                                / trials, 3),
            "avg_energy": round(sum(k * v for k, v in energy_hist.items())
                                / trials, 3),
            "basics_hist": dict(sorted(basic_hist.items())),
            "energy_hist": dict(sorted(energy_hist.items())),
            "note": "统计口径：卡手 = 起手 7 张无基础宝可梦（PRD F3）"}


@router.get("/{did}/export")
def export_deck(did: str, p: Principal = Depends(current_user)):
    """导出为 Limitless 兼容文本（往返一致）"""
    d = store.get_deck(did)
    if not d:
        raise HTTPException(404, "卡组不存在")
    ensure_owner(d["owner"], p)
    lines = []
    for e in d["cards"] or []:
        if e.get("set") and e.get("num"):
            lines.append(f"{e['count']} {e['name']} {e['set']} {e['num']}")
        else:
            lines.append(f"{e['count']} {e['name']}")
    return {"deck_id": did, "name": d["name"],
            "text": "\n".join(lines), "count": len(lines)}
