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
# 简化行：`数量 卡名`（无系列/编号；中文卡表/手写卡表常用）
SHORT_RE = re.compile(r"^\s*(\d+)\s+(\S.*?)\s*$")


# ---------------- 卡池（真实卡池 M1 + demo decks 全量去重 + card-db 补充） ----------------
_POOL: dict | None = None
_POOL_OBJ: dict = {}          # name(lower) -> 引擎卡**定义**（PokemonDef/TrainerDef/EnergyDef）


def _norm_key(name: str) -> str:
    """名称匹配键：小写 + 撇号归一 + 去非字母数字

    数据集内撇号混用（``N’s Zoroark ex`` vs ``N's Zoroark ex``），
    归一后才能与既有卡池/卡表对齐（与 ETL 的 ``name_key`` 同规则）。
    """
    s = (name or "").strip().lower()
    s = s.replace("’", "'").replace("‘", "'").replace("`", "'")
    return "".join(ch for ch in s if ch.isalnum())


def _ref_of(cdef):
    """卡定义 → 全新的引用实例（每张牌独立，避免多份共享同一 ref 对象）"""
    from engine.cards import (EnergyDef, EnergyRef, PokeRef, PokemonDef,
                              TrainerDef, TrainerRef)
    if isinstance(cdef, PokemonDef):
        return PokeRef(cdef)
    if isinstance(cdef, EnergyDef):
        return EnergyRef(cdef)
    if isinstance(cdef, TrainerDef):
        return TrainerRef(cdef)
    raise TypeError(f"未知卡定义 {type(cdef)}")


def _pool() -> dict:
    """name(lower) -> meta{kind,is_basic,name,stage,is_ex,engine_ready,source}"""
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

    def _put_pokemon(name, pdef, source, engine_ready=True, note="",
                     uid=None, printing=None):
        _meta = {
            "kind": "pokemon", "name": name,
            "name_zh": getattr(pdef, "name_zh", ""),
            "display": getattr(pdef, "display", name),
            "stage": getattr(pdef, "stage", 0),
            "is_ex": bool(getattr(pdef, "is_ex", False)),
            "hp": getattr(pdef, "hp", None),
            "is_basic": getattr(pdef, "stage", 0) == 0,
            "engine_ready": engine_ready, "note": note, "source": source,
            "uid": uid or name, "printing": printing or {}}
        out[(uid or name).lower()] = _meta
        _POOL_OBJ[(uid or name).lower()] = pdef

    # 1) 真实卡池（M1 · engine/data/cards_real.json + 扩展池）
    #    **按印刷 uid 建索引**（2026-10-29 第二十一批）：同名不同印刷可共存，
    #    卡组用 uid 精确选卡；只写卡名时落到「首选印刷」（数据集最新）。
    try:
        from engine import reallib
        for uid, item in reallib.printings().items():
            cdef, meta = item["def"], item["meta"]
            pr = item.get("printing") or {}
            if meta.get("supertype") == "Pokemon":
                _put_pokemon(item["name"], cdef, "real-pool",
                             engine_ready=not item["unsupported"],
                             note=item["reason"], uid=uid, printing=pr)
            elif meta.get("supertype") == "Energy":
                out[uid.lower()] = {"kind": "energy", "name": item["name"],
                                    "name_zh": getattr(cdef, "name_zh", ""),
                                    "display": getattr(cdef, "display", item["name"]),
                                    "is_basic": False, "is_basic_energy": True,
                                    "engine_ready": True, "source": "real-pool",
                                    "uid": uid, "printing": pr}
                _POOL_OBJ[uid.lower()] = cdef
            else:                                    # Trainer
                tag = getattr(cdef, "tag", "")
                out[uid.lower()] = {"kind": "trainer", "name": item["name"],
                                    "name_zh": getattr(cdef, "name_zh", ""),
                                    "display": getattr(cdef, "display", item["name"]),
                                    "is_basic": False, "tag": tag,
                                    "engine_ready": tag != "unsupported",
                                    "note": item["reason"],
                                    "source": "real-pool",
                                    "uid": uid, "printing": pr}
                _POOL_OBJ[uid.lower()] = cdef
        # 卡名别名 → 首选印刷（旧接口/未指定印刷的卡表仍按卡名可直接用）
        for name, item in reallib.catalog().items():
            k = name.lower()
            if k not in out:
                src = out.get(item["uid"].lower())
                if src:
                    out[k] = {**src, "via": "name-alias", "canonical": name}
                    _POOL_OBJ[k] = _POOL_OBJ.get(item["uid"].lower())
    except Exception:
        pass

    # 2) demo 卡组（原创代名卡，Y 版；不覆盖已存在的真实卡名）
    try:
        from engine import decklib
        for fn in [n for n in dir(decklib) if n.startswith("deck_")]:
            try:
                deck = getattr(decklib, fn)()
            except Exception:
                continue
            for c in deck:
                pdef = getattr(c, "pdef", None)
                tdef = getattr(c, "tdef", None)
                edef = getattr(c, "edef", None)
                if pdef is not None and pdef.name.lower() not in out:
                    _put_pokemon(pdef.name, pdef, "decklib")
                elif tdef is not None and tdef.name.lower() not in out:
                    out[tdef.name.lower()] = {
                        "kind": "trainer", "name": tdef.name,
                        "name_zh": getattr(tdef, "name_zh", ""),
                        "display": getattr(tdef, "display", tdef.name),
                        "is_basic": False, "engine_ready": True,
                        "source": "decklib"}
                    _POOL_OBJ[tdef.name.lower()] = tdef
                elif edef is not None:
                    nm = getattr(edef, "name", "Energy")
                    if nm.lower() not in out:
                        out[nm.lower()] = {
                            "kind": "energy", "name": nm,
                            "name_zh": getattr(edef, "name_zh", ""),
                            "display": getattr(edef, "display", nm),
                            "is_basic": False,
                            "is_basic_energy": True, "engine_ready": True,
                            "source": "decklib"}
                        _POOL_OBJ[nm.lower()] = edef
    except Exception:
        pass

    # 3) card-db 卡库补充（M1 清洗入库的 1267 张卡；仅补名称存在性与元数据）
    #    引擎已建模的卡（前面步骤已注册）**优先**，此处只补空缺。
    try:
        import json as _json
        import os as _os
        import sqlite3
        # 卡库位置：POKELAB_CARD_DB 覆盖 → 仓库内标准路径。
        # **不再回退到仓库外的旧数据目录**（仅 3 张卡、schema 已漂移）—— 那种回退会
        # 让卡组校验「看起来通过」却基于陈旧卡池，属静默降级。
        _env = _os.environ.get("POKELAB_CARD_DB")
        dbp = (Path(_env) if _env else
               repo / "packages" / "card-db" / "data" / "db" / "pokelab.db")
        if dbp.exists():
            con = sqlite3.connect(f"file:{dbp}?mode=ro", uri=True)
            for name, st, ej in con.execute(
                    "SELECT name,supertype,effects_json FROM cards"):
                key = (name or "").lower()
                if not key:
                    continue
                lib = {}
                try:
                    lib = (_json.loads(ej or "{}").get("library") or {})
                except Exception:                              # noqa: BLE001
                    lib = {}
                kind = lib.get("kind") or {
                    "Pokemon": "pokemon", "Trainer": "trainer",
                    "Energy": "energy"}.get(st, "unknown")
                meta = {
                    "kind": kind, "name": name, "source": "card-db",
                    "is_basic": bool(lib.get("is_basic")
                                     or (kind == "pokemon"
                                         and lib.get("stage") is None
                                         and st == "Pokemon"
                                         and not lib.get("subtype"))),
                    "is_basic_energy": bool(lib.get("is_basic_energy")),
                    "name_zh": lib.get("name_zh") or "",
                    "engine_ready": bool(lib.get("engine_ready")),
                    "note": ("" if lib.get("engine_ready")
                             else "卡库已收录，引擎未建模（不可对战）"),
                }
                # 主键 + 归一键 + 别名（撇号/基础能量命名差异）
                keys = [key, _norm_key(name)] + [
                    (al or "").lower() for al in (lib.get("aliases") or [])]
                keys += [_norm_key(al) for al in (lib.get("aliases") or [])]
                for k in keys:
                    if k and k not in out:
                        out[k] = meta
            con.close()
    except Exception:
        pass
    # 4) 中文别名索引：让「粘贴中文卡表」也能命中（数据键仍为英文）
    #    ⚠ 跳过印刷 uid（含 `@`）：中文名只指向**首选印刷**的卡名别名，避免同名多印刷时
    #    中文输入随机落到某一印刷。
    for key, meta in list(out.items()):
        if "@" in key:
            continue
        zh = meta.get("name_zh")
        if zh and zh not in out:
            out[zh] = {**meta, "via": "zh-alias", "canonical": meta.get("name")}
            _POOL_OBJ[zh] = _POOL_OBJ.get(key)
    _POOL = out
    return out


# ---------------- 解析与校验 ----------------
def _resolve_entry(e: dict) -> tuple:
    """卡组行 → (meta, 引擎卡对象)

    优先按**印刷 uid**（同名不同印刷可精确选择），未写/未收录时回退卡名
    （= 首选印刷，见 `reallib.catalog`）。
    """
    pool = _pool()
    uid = str(e.get("uid") or "").lower()
    if uid:
        m, o = pool.get(uid), _POOL_OBJ.get(uid)
        if m is not None and o is not None:
            return m, o
    raw = e.get("name") or ""
    for k in ((e.get("canonical") or raw).lower(), raw.strip()):
        if k and k in pool:
            return pool[k], _POOL_OBJ.get(k)
    return None, None


def _parse(text: str) -> tuple[list, list]:
    """→ (entries, errors)：entries = {count,name,set,num,url,meta,matched}"""
    entries, errs = [], []
    for i, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        m = LINE_RE.match(line)
        if not m:
            # 简化格式：`数量 卡名`（无系列/编号）—— 中文卡表常用写法，直接接受
            m2 = SHORT_RE.match(line)
            if m2:
                entries.append({"count": int(m2.group(1)),
                                "name": m2.group(2).strip(),
                                "set": None, "num": None, "raw": line,
                                "format": "short"})
                continue
            # 容错：只写卡名（默认 1 张）
            if re.match(r"^\d+\s+\S", line):
                errs.append(f"第 {i} 行无法解析（支持 `数量 卡名 [系列 编号]`）：{line}")
            else:
                entries.append({"count": 1, "name": line, "set": None,
                                "num": None, "raw": line, "format": "name-only"})
            continue
        cnt, name, setc, num = int(m.group(1)), m.group(2).strip(), m.group(3), m.group(4)
        entries.append({"count": cnt, "name": name, "set": setc, "num": num,
                        "raw": line})
    # 匹配卡池 + 外链
    pool = _pool()
    for e in entries:
        raw = e["name"]
        # 「数量 卡名 SET 编号」→ 先按**印刷 uid**精确匹配（同名多印刷可用）
        uid = None
        if e.get("set") and e.get("num"):
            uid = (f"{raw}@{str(e['set']).strip().upper()} "
                   f"{str(e['num']).strip()}")
        meta = ((pool.get(uid.lower()) if uid else None)
                or pool.get(raw.lower()) or pool.get(raw.strip())
                or pool.get(_norm_key(raw)))
        e["matched"] = bool(meta)
        e["meta"] = meta
        # 实际采用的印刷（未写印刷/该印刷未收录 → 首选印刷），供对战重建使用
        e["uid"] = (meta or {}).get("uid")
        if meta:
            # canonical = 英文数据键（同名上限等按它合并计数）；display = 中文展示名
            e["canonical"] = meta.get("name")
            e["display"] = meta.get("display") or meta.get("name_zh") or raw
            e["input_lang"] = "zh" if any("\u4e00" <= ch <= "\u9fff"
                                          for ch in raw) else "en"
        else:
            e["canonical"] = None
            e["display"] = raw
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
    by_name = Counter()          # canonical（英文键）→ 数量：中英混写合并
    display_of = {}
    meta_of = {}
    for e in entries:
        key = (e.get("canonical") or e["name"]).lower()
        by_name[key] += e["count"]
        display_of.setdefault(key, e.get("display") or e["name"])
        meta_of.setdefault(key, e["meta"])

    def _is_basic_energy(key: str) -> bool:
        m = meta_of.get(key) or {}
        if m.get("is_basic_energy"):
            return True
        return m.get("kind") == "energy" and "energy" in key

    for key, n in by_name.items():
        if n > MAX_SAME and not _is_basic_energy(key):
            errors.append(f"「{display_of.get(key, key)}」超过同名上限 "
                          f"{MAX_SAME}（当前 {n}）")
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

    unmatched = sorted({(display_of.get(k) if False else k)
                        for k, m in meta_of.items() if not m})
    if unmatched:
        warnings.append("未收录卡（灰名单，中英文均未命中卡池）：" +
                        "、".join(unmatched[:8]) +
                        ("…" if len(unmatched) > 8 else "") +
                        " → 含此类卡的卡组暂不可对战")

    # M1：卡名已收录但引擎未实现（tag=unsupported）→ 对战时可占位替换，显式告知
    #   注意：按 **canonical 键**统计（display 名与 by_name 键不同域，混用会漏算）
    not_impl_keys = {k for k, m in meta_of.items()
                     if m and not m.get("engine_ready", True)}
    not_impl = sorted({display_of.get(k, k) for k in not_impl_keys})
    if not_impl:
        warnings.append("引擎未实现卡（M1 灰名单，对战时以基础能量占位）：" +
                        "、".join(not_impl[:8]) +
                        ("…" if len(not_impl) > 8 else ""))

    kinds = Counter((meta_of[k] or {}).get("kind", "unknown") for k in by_name)
    summary = {
        "total": total, "unique": len(by_name),
        "pokemon": kinds.get("pokemon", 0),
        "trainer": kinds.get("trainer", 0),
        "energy": kinds.get("energy", 0),
        "unknown": kinds.get("unknown", 0),
        "basics": len(basics),
        "engine_ready": total - sum(by_name[k] for k in not_impl_keys),
        "not_implemented": sum(by_name[k] for k in not_impl_keys),
        "battle_ready": bool(basics) and not unmatched,
    }
    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "parsed": entries, "summary": summary}


# ---------------- F3 → F5 打通：按已入库卡组重建引擎卡组 ----------------
def _fallback_energy_type(cards: list) -> str:
    """占位能量属性：取卡组内出现最多的一种能量属性（缺省无色）"""
    kinds = Counter()
    for e in cards or []:
        m = (_resolve_entry(e)[0] or {})
        if m.get("kind") == "energy" and m.get("name"):
            nm = m["name"].lower()
            for t in ("psychic", "fire", "grass", "darkness", "water",
                      "lightning", "fighting", "metal"):
                if t in nm:
                    kinds[t] += int(e.get("count") or 0)
                    break
    return kinds.most_common(1)[0][0] if kinds else "colorless"


def deck_build_report(did: str) -> dict:
    """卡组 → 引擎可执行性报告（占位替换清单，诚实标注 C5）"""
    d = store.get_deck(did)
    if not d:
        raise ValueError(f"卡组 {did} 不存在")
    replaced, missing = [], []
    total = playable = 0
    for e in d.get("cards") or []:
        n = int(e.get("count") or 0)
        total += n
        m = _resolve_entry(e)[0]
        if m is None:
            missing.append({"name": e.get("name"), "count": n,
                            "reason": "卡池未收录"})
        elif not m.get("engine_ready", True):
            replaced.append({"name": e.get("name"), "count": n,
                             "reason": m.get("note") or "引擎未实现"})
        else:
            playable += n
    replaced_n = sum(r["count"] for r in replaced)
    return {"total": total, "playable": playable - 0,
            "replaced": replaced, "missing": missing,
            "executable_rate": round((total - replaced_n -
                                      sum(x["count"] for x in missing)) / total, 4)
            if total else 0.0}


def build_engine_deck(did: str) -> list:
    """读库内卡组 → 引擎卡对象列表（供对战）

    - 每张牌构造独立 ref 实例（避免多份共享同一对象导致 hand/deck 混同）；
    - 卡池未收录 → ValueError（灰名单，需先补齐卡池）；
    - 已收录但引擎未实现（如 Rare Candy）→ 以基础能量占位（报告见
      `deck_build_report`，差异透明 C5）。
    """
    d = store.get_deck(did)
    if not d:
        raise ValueError(f"卡组 {did} 不存在")
    cards = d.get("cards") or []
    fb_type = _fallback_energy_type(cards)
    from engine.cards import EnergyDef
    _EZH = {"psychic": "超", "fire": "火", "grass": "草", "darkness": "恶",
            "water": "水", "lightning": "雷", "fighting": "斗",
            "metal": "钢", "colorless": ""}
    deck: list = []
    missing: list = []
    for e in cards:
        meta, obj = _resolve_entry(e)
        n = int(e.get("count") or 0)
        if meta is None or obj is None:
            missing.append(e.get("name") or "?")
            continue
        if not meta.get("engine_ready", True):
            # 引擎未实现卡 → 基础能量占位（中文展示名同样给出，C5 透明）
            deck.extend(_ref_of(EnergyDef(
                fb_type, name=f"Basic {fb_type.title()} Energy",
                name_zh=f"基本{_EZH.get(fb_type, '')}能量")) for _ in range(n))
            continue
        deck.extend(_ref_of(obj) for _ in range(n))
    if missing:
        raise ValueError("含未收录卡，暂不可对战（灰名单/C5）：" +
                         "、".join(sorted(set(missing))[:6]))
    if len(deck) < 8:
        raise ValueError(f"卡组仅 {len(deck)} 张，不足以对战（至少 8 张）")
    return deck


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
    """预设 Meta 对手池 A1–A4 —— M1 起为**真实卡表**（引擎映射版，逐卡可追溯）"""
    import sys
    repo = Path(__file__).resolve().parents[3]
    pkg = repo / "packages" / "engine"
    if pkg.exists() and str(pkg) not in sys.path:
        sys.path.insert(0, str(pkg))
    try:
        from engine import reallib
        rows = reallib.archetypes()
    except Exception as e:                       # noqa: BLE001
        raise HTTPException(500, f"真实卡池不可用：{e}") from e
    return {
        "presets": [{"id": a["id"], "label": a["label"],
                     "label_en": a.get("label_en", ""), "role": a["role"],
                     "style": a["style"], "note": a["note"],
                     "cards_total": a["cards_total"],
                     "unique_cards": a["unique_cards"],
                     "executable_rate": a["executable_rate"],
                     "replaced": a["replaced"], "approximated": a["approximated"],
                     "battle_ready": a["battle_ready"],
                     "list_endpoint": f"/decks/presets/{a['id']}"}
                    for a in rows],
        "source": "engine/data/{cards_real,decklists}.json（M1 卡池补齐）",
        "disclaimer": "真实卡名 + 引擎映射数值；引擎未实现卡以基础能量占位，"
                      "逐卡清单见 /decks/presets/{id}（C5 差异透明）",
    }


@router.get("/presets/{aid}")
def preset_detail(aid: str, p: Principal = Depends(current_user)):
    """预设卡组逐卡清单 + 覆盖率报告（F3 透明度：映射/近似/占位三态）"""
    import sys
    repo = Path(__file__).resolve().parents[3]
    pkg = repo / "packages" / "engine"
    if pkg.exists() and str(pkg) not in sys.path:
        sys.path.insert(0, str(pkg))
    try:
        from engine import reallib
        d = reallib.describe(aid)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    except Exception as e:                       # noqa: BLE001
        raise HTTPException(500, f"真实卡池不可用：{e}") from e
    d["provenance"] = reallib.catalog_meta().get("provenance")
    d["decks_file_note"] = (reallib._decklists_raw()["meta"]  # noqa: SLF001
                            .get("fidelity_note"))
    return d


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
def export_deck(did: str, p: Principal = Depends(current_user),
                lang: str = Query("zh", pattern="^(zh|en)$")):
    """导出为 Limitless 兼容文本（往返一致）

    lang=zh（默认）：卡名输出**中文展示名**（如「4 多龙巴鲁托ex」），导入端亦接受中文；
    lang=en：输出英文规范名（`4 Dragapult ex TWM 130`），与原版 Limitless 文本互通。
    """
    d = store.get_deck(did)
    if not d:
        raise HTTPException(404, "卡组不存在")
    ensure_owner(d["owner"], p)
    lines = []
    for e in d["cards"] or []:
        nm = ((e.get("display") or e.get("name")) if lang == "zh"
              else (e.get("canonical") or e.get("name")))
        # 优先导出**实际采用的印刷**（uid 解析结果），保证往返后仍是同一印刷
        pr = (e.get("meta") or {}).get("printing") or {}
        pset = pr.get("set") or e.get("set")
        pnum = pr.get("number") or e.get("num")
        if pset and pnum:
            lines.append(f"{e['count']} {nm} {pset} {pnum}")
        else:
            lines.append(f"{e['count']} {nm}")
    return {"deck_id": did, "name": d["name"], "lang": lang,
            "text": "\n".join(lines), "count": len(lines),
            "note": "中文名可直接再导入（导入端中英文均识别）" if lang == "zh"
                    else "英文规范名，兼容 Limitless 原版文本"}
