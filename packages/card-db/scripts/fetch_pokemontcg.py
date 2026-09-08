# -*- coding: utf-8 -*-
"""拉取 pokemontcg.io 卡牌数据（在【可联网的本机】运行；沙箱网络不可达）
用法：
  python scripts/fetch_pokemontcg.py --set sv3  --out data/raw/pokemontcg.jsonl [--limit 200]
说明：输出 JSONL 供 import_cards.py 导入；不写入仓库、数据与代码分离（再分发红线）。
"""
import argparse, json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE = "https://api.pokemontcg.io/v2/cards"


def fetch(set_id: str | None, limit: int, page_size: int = 100) -> list[dict]:
    import requests
    q = []
    if set_id:
        q.append(f"set.id:{set_id}")
    params = {"q": " ".join(q) or None, "pageSize": page_size, "page": 1, "select":
              "name,set,number,supertype,types,hp,rules,abilities,attacks"}
    out, page = [], 1
    while len(out) < limit:
        params["page"] = page
        r = requests.get(BASE, params=params, timeout=30)
        r.raise_for_status()
        data = r.json().get("data", [])
        if not data:
            break
        out.extend(data)
        if len(data) < page_size:
            break
        page += 1
        time.sleep(0.25)  # 限速礼貌
    return out[:limit]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default=None, help="系列 id，如 sv3；缺省拉全部")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--out", default="data/raw/pokemontcg.jsonl")
    a = ap.parse_args()
    cards = fetch(a.set, a.limit)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for c in cards:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(f"[fetch] {len(cards)} 张 → {out.resolve()}")


if __name__ == "__main__":
    main()
