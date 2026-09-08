# -*- coding: utf-8 -*-
"""PokéLab CardDB · SQLite 存取层（执行包内 schema.sql 建 10 表）

路径均相对包位置解析，支持环境变量覆盖：
  - POKELAB_DB：数据库文件位置（默认 <repo>/packages/card-db/data/db/pokelab.db，被 gitignore）
  - schema.sql 随包分发（packages/card-db/schema.sql）
"""
from __future__ import annotations
import os
import sqlite3
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parent.parent          # packages/card-db
SCHEMA_SQL = _PKG_ROOT / "schema.sql"
DEFAULT_DB = Path(os.environ.get(
    "POKELAB_DB", str(_PKG_ROOT / "data" / "db" / "pokelab.db")))


def connect(db_path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init_db(db_path: Path | str = DEFAULT_DB) -> int:
    """建库：执行 schema.sql；返回物理表数"""
    con = connect(db_path)
    try:
        con.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
        con.commit()
        n = con.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()[0]
        return n
    finally:
        con.close()


def upsert_card(con: sqlite3.Connection, rec) -> int:
    """按 UNIQUE(series, number) 插入或忽略；返回 card id（已存在则查询返回）"""
    cur = con.execute(
        """INSERT OR IGNORE INTO cards
           (name, series, number, supertype, types, hp, rules_text, attacks_json, status, source)
           VALUES (?,?,?,?,?,?,?,?, 'pending', ?)""",
        (rec.name, rec.series, rec.number, rec.supertype, rec.types, rec.hp,
         rec.rules_text, rec.attacks_json, rec.source),
    )
    row = con.execute(
        "SELECT id FROM cards WHERE series=? AND number=?", (rec.series, rec.number)
    ).fetchone()
    return row["id"]


def add_rule_chunks(con: sqlite3.Connection, chunks: list, card_id: int | None = None) -> int:
    n = 0
    for c in chunks:
        con.execute(
            """INSERT INTO rule_chunks (card_id, source_ref, chunk_text, status)
               VALUES (?,?,?, 'active')""",
            (c.card_id if c.card_id is not None else card_id, c.source_ref, c.chunk_text),
        )
        n += 1
    return n
