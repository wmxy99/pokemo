# -*- coding: utf-8 -*-
"""把 raw 数据（pokemontcg JSONL / Kaggle CSV）导入 cards 表
用法：
  python scripts/import_cards.py --jsonl data/raw/pokemontcg.jsonl
  python scripts/import_cards.py --kaggle-csv <path>
"""
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pokelab.db import connect, upsert_card
from pokelab.loader import pokemontcg_json_to_records, kaggle_csv_to_records


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl", default=None, help="pokemontcg.io JSONL 路径")
    ap.add_argument("--kaggle-csv", default=None, help="Kaggle EN_Card_Data.csv 路径")
    a = ap.parse_args()
    if not (a.jsonl or a.kaggle_csv):
        ap.error("需 --jsonl 或 --kaggle-csv")
    if a.jsonl:
        raw = [json.loads(line) for line in open(a.jsonl, encoding="utf-8") if line.strip()]
        recs = pokemontcg_json_to_records(raw)
    else:
        recs = kaggle_csv_to_records(Path(a.kaggle_csv))
    con = connect()
    ids = [upsert_card(con, r) for r in recs]
    con.commit()
    n_active_meta = con.execute("SELECT count(*) FROM cards WHERE status='pending'").fetchone()[0]
    con.close()
    print(f"[import] {len(ids)} 张入库/已存在 | 待效果解析(pending): {n_active_meta}")


if __name__ == "__main__":
    main()
