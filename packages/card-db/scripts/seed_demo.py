# -*- coding: utf-8 -*-
"""内置样例导入 + 建库 + 规则条款 demo（离线可运行，验证管道闭环）

样例卡的 rules_text 为「自述式转写」（示意管道；真实文本请用 fetch_pokemontcg.py /
Kaggle CSV 导入后覆盖）。运行：
  python scripts/seed_demo.py
"""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pokelab.db import init_db, connect, upsert_card, add_rule_chunks
from pokelab.schema import CardRecord
from pokelab.kb import chunk_rules_text, search_keyword

DEMO_CARDS = [
    CardRecord(
        name="Dragapult ex", series="demo", number="1",
        supertype="Pokemon", types='["dragon"]', hp=330,
        rules_text=(
            "Ability 'Recon Directive' (once per turn): 检视牌库顶 2 张并任取 1 张入手牌，其余放回。"
            "Attack 'Phantom Dive' [P][P][C] 200: 对对手 2 只后备宝可梦各放置 6 个伤害指示物。"
        ),
        source="demo-mock",
    ),
    CardRecord(
        name="Dusknoir", series="demo", number="2",
        supertype="Pokemon", types='["psychic"]', hp=130,
        rules_text=(
            "Ability 'Cursed Blast' (once per turn): 从手牌弃掉此卡，在对手 1 只宝可梦上放 13 个伤害指示物。"
        ),
        source="demo-mock",
    ),
    CardRecord(
        name="Buddy-Buddy Poffin", series="demo", number="3",
        supertype="Trainer", types="[]",
        rules_text=(
            "Item card: 从牌库检索 2 张基础宝可梦放入后备区并洗牌。"
        ),
        source="demo-mock",
    ),
]

DEMO_RULES = (
    "1. 对局目标：先取完 6 张奖赏卡、或使对手场上无宝可梦、或使对手回合开始无牌可抽者获胜。\n"
    "2. 回合流程：抽 1 张牌；每回合可做（顺序不限）：给场上宝可梦附着 1 张能量、"
    "进化 1 次、出任意数量物品卡、出 1 张支援者卡、出 1 张竞技场卡、使用特性、撤退、攻击。\n"
    "3. 先手玩家第一回合不能攻击、不能使用支援者。\n"
    "4. 攻击伤害计算：基础伤害 + 弱点修正（相克 ×2） - 抗性（30）。\n"
    "5. ex 宝可梦被击倒时，对手多拿 1 张奖赏卡。\n"
    "6. 同名卡在卡组中最多 4 张（基础能量除外）。"
)


def main():
    n = init_db()
    print(f"[db] 建库完成：{n} 张物理表")
    con = connect()
    ids = []
    for rec in DEMO_CARDS:
        cid = upsert_card(con, rec)
        ids.append(cid)
    con.commit()
    print(f"[cards] 样例入库 {len(ids)} 张（id={ids}，status=pending）")

    chunks = chunk_rules_text(DEMO_RULES, source_ref="demo:turn_structure")
    add_rule_chunks(con, chunks)
    con.commit()
    print(f"[kb] 规则条款入库 {len(chunks)} 条")
    hits = search_keyword(con, "弱点")
    print(f"[kb] 关键字检索'弱点' top-1 → {hits[0]['source_ref'] if hits else '无'}")
    con.close()


if __name__ == "__main__":
    main()
