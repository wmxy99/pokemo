# -*- coding: utf-8 -*-
"""M1 · 卡池补齐：把真实卡池与预设对手池 A1–A4 落库（card-db / SQLite）

数据来源（单一数据源 = 引擎侧 JSON，避免两处维护）：
  packages/engine/engine/data/cards_real.json   真实卡池（卡名 + 引擎映射数值）
  packages/engine/engine/data/decklists.json    A1–A4 真实 60 卡卡表

落库内容：
  cards       —— 卡池条目（status=active/pending/unsupported，effects_json 存引擎映射）
  decks       —— is_preset=1 的预设卡组（meta_archetype 标注对位原型）
  deck_cards  —— 卡组逐卡数量（引擎不支持的卡以 fallback 能量占位，note 见 cards.source）

用法：
  python scripts/seed_archetypes.py                 # 默认写 data/db/pokelab.db
  python scripts/seed_archetypes.py --db /path/x.db  # 指定库
  python scripts/seed_archetypes.py --check          # 只校验不写库
说明：写保护环境（协同盘/沙箱）下自动回退到用户数据目录并打印实际路径。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG = HERE.parent                                   # packages/card-db
REPO = PKG.parents[1]                               # pokelab-ai
ENGINE_DATA = REPO / "packages" / "engine" / "engine" / "data"
DEFAULT_DB = PKG / "data" / "db" / "pokelab.db"
SCHEMA = PKG / "schema.sql"


def _load(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _pick_db(target: Path) -> tuple[sqlite3.Connection, Path]:
    """目标库可写则用目标库；否则回退用户目录（写保护环境兼容）

    受限环境（协同盘/沙箱）常对**已存在文件**拒绝写入 → 最后兜底为
    带时间戳的新文件（新建文件可写，与 apps/api/app/store.py 同策略）。
    """
    import time as _t
    cands = [target,
             Path.home() / ".pokelab" / target.name,
             Path.home() / ".pokelab" /
             f"{target.stem}_{_t.strftime('%Y%m%d_%H%M%S')}.db"]
    for cand in cands:
        try:
            cand.parent.mkdir(parents=True, exist_ok=True)
            con = sqlite3.connect(str(cand))
            con.execute("PRAGMA journal_mode=MEMORY")
            con.execute("CREATE TABLE IF NOT EXISTS _probe(x INTEGER)")
            con.execute("INSERT INTO _probe VALUES (1)")
            con.execute("DROP TABLE _probe")
            con.commit()
            return con, cand
        except sqlite3.Error as e:                   # pragma: no cover
            print(f"[warn] {cand} 不可写（{e}）→ 尝试备用路径")
    raise SystemExit("无可写数据库路径（可用 --db 显式指定）")


def seed(con: sqlite3.Connection, *, verbose: bool = True) -> dict:
    schema = SCHEMA.read_text(encoding="utf-8")
    con.executescript(schema)

    cards = _load(ENGINE_DATA / "cards_real.json")
    decks = _load(ENGINE_DATA / "decklists.json")
    card_meta = cards["meta"]
    deck_meta = decks["meta"]

    # ---- 1) 卡池 ----
    name2id: dict = {}
    n_new = n_upd = 0
    for c in cards["cards"]:
        real = c.get("real") or {}
        series = real.get("set") or "POKELAB"
        # 无 official 编号的（基础能量等）用卡名做编号，避免 (series,number) 冲突
        number = str(real.get("number") or c["name"])
        unsupported = (c.get("supertype") == "Trainer"
                       and c.get("tag") == "unsupported")
        status = "unsupported" if unsupported else (
            "active" if c.get("supertype") != "Trainer" or c.get("tag") else "pending")
        row = con.execute("SELECT id FROM cards WHERE name=? AND series=? "
                          "AND number=?", (c["name"], series, number)).fetchone()
        payload = (
            json.dumps(c.get("types") or [], ensure_ascii=False),
            c.get("hp"),
            c.get("note") or c.get("desc") or "",
            json.dumps(c.get("attacks") or [], ensure_ascii=False),
            json.dumps({"engine": {"supertype": c.get("supertype"),
                                   "stage": c.get("stage"),
                                   "evolves_from": c.get("evolves_from"),
                                   "ability_tags": [a.get("tag") for a in
                                                    (c.get("abilities") or [])],
                                   "trainer_tag": c.get("tag"),
                                   "energy_type": c.get("type"),
                                   "is_ex": bool(c.get("is_ex"))},
                        "fidelity": c.get("fidelity", "engine-mapped"),
                        "provenance": card_meta.get("provenance")},
                       ensure_ascii=False),
            status,
            f"{card_meta.get('milestone')} · {card_meta.get('source')} · "
            f"{card_meta.get('ip_note')}",
        )
        if row:
            con.execute("UPDATE cards SET types=?, hp=?, rules_text=?, "
                        "attacks_json=?, effects_json=?, status=?, source=?, "
                        "updated_at=datetime('now','localtime') WHERE id=?",
                        (*payload, row[0]))
            name2id[c["name"]] = row[0]
            n_upd += 1
        else:
            cur = con.execute(
                "INSERT INTO cards(name,series,number,supertype,types,hp,"
                "rules_text,attacks_json,effects_json,status,source) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (c["name"], series, number, c.get("supertype"), *payload))
            name2id[c["name"]] = cur.lastrowid
            n_new += 1

    # ---- 2) 预设卡组 A1–A4 ----
    deck_ids = {}
    for a in decks["archetypes"]:
        label = f"{a['id']} · {a['label']}"
        meta_archetype = a["label"].lower().replace(" ", "_").replace("/", "_")
        raw_text = "\n".join(f"{c['count']} {c['name']}" for c in a["cards"])
        row = con.execute("SELECT id FROM decks WHERE is_preset=1 AND name=?",
                          (label,)).fetchone()
        if row:
            did = row[0]
            con.execute("UPDATE decks SET meta_archetype=?, deck_text_raw=?, "
                        "updated_at=datetime('now','localtime') WHERE id=?",
                        (meta_archetype, raw_text, did))
            con.execute("DELETE FROM deck_cards WHERE deck_id=?", (did,))
        else:
            cur = con.execute(
                "INSERT INTO decks(owner_id,name,format,legal,is_preset,"
                "meta_archetype,deck_text_raw) VALUES(NULL,?,?,1,1,?,?)",
                (label, "standard", meta_archetype, raw_text))
            did = cur.lastrowid
        deck_ids[a["id"]] = did
        for c in a["cards"]:
            con.execute("INSERT INTO deck_cards(deck_id,card_id,quantity) "
                        "VALUES(?,?,?)", (did, name2id[c["name"]], int(c["count"])))
    con.commit()

    out = {"cards_new": n_new, "cards_updated": n_upd,
           "cards_total": len(cards["cards"]),
           "decks": {k: v for k, v in deck_ids.items()}}
    if verbose:
        print(f"[seed] 卡池：新增 {n_new} · 更新 {n_upd} · 合计 "
              f"{len(cards['cards'])} 张（source={card_meta.get('source')}）")
        for a in decks["archetypes"]:
            print(f"[seed] 预设 {a['id']} · {a['label']} → deck_id={deck_ids[a['id']]}"
                  f"（{sum(c['count'] for c in a['cards'])} 张 / 来源 {deck_meta.get('fidelity_note', 'reconstructed')[:24]}…）")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="M1 卡池补齐落库")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--check", action="store_true", help="只校验数据，不写库")
    args = ap.parse_args()

    if args.check:
        cards = _load(ENGINE_DATA / "cards_real.json")
        decks = _load(ENGINE_DATA / "decklists.json")
        names = {c["name"] for c in cards["cards"]}
        bad = [(a["id"], c["name"]) for a in decks["archetypes"]
               for c in a["cards"] if c["name"] not in names]
        for a in decks["archetypes"]:
            total = sum(c["count"] for c in a["cards"])
            flag = "OK " if total == 60 else "BAD"
            print(f"[check] {flag} {a['id']} {a['label']}: {total} 张 / "
                  f"{len(a['cards'])} 种")
        print("[check] 未收录卡名:", bad or "无")
        return 1 if bad else 0

    con, used = _pick_db(Path(args.db))
    try:
        seed(con)
        print(f"[seed] 完成 → {used}")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
