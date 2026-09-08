# -*- coding: utf-8 -*-
"""PokéLab CLI · 演示入口（单局复盘 / 批量统计 / JSONL 导出）

用法（仓库根目录执行）：
  python apps/cli/run_demo.py single
  python apps/cli/run_demo.py batch [n=200]
  python apps/cli/run_demo.py dump <seed>
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

# 注入 packages/engine 到模块路径（未安装包时的本地运行方式）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent
                       / "packages" / "engine"))

from engine import decklib                                    # noqa: E402
from engine.state import GameConfig                           # noqa: E402
from engine.agents import BaselineAI, RandomAgent             # noqa: E402
from engine.simulator import run_match, run_many              # noqa: E402

EVENT_ZH = {
    "setup": "开局", "turn_start": "回合开始", "turn_end": "回合结束",
    "draw": "抽卡", "play": "上场", "attach": "附能量", "attack": "攻击",
    "damage": "伤害", "status": "附加状态", "heal": "回复",
    "knockout": "击倒", "prize": "拿奖赏", "promote": "替补登场",
    "retreat": "撤退", "checkup": "状态检查", "finish": "终局",
    "confuse_tails": "混乱自伤", "confuse_heads": "混乱恢复",
    "hand_overflow": "手牌溢出", "sleep_wake": "苏醒",
    "paralyze_recover": "麻痹恢复",
}


def fmt_event(e: dict) -> str:
    t = EVENT_ZH.get(e["type"], e["type"])
    d = {k: v for k, v in e.items() if k not in ("type", "turn", "player")}
    parts = " ".join(f"{k}={v}" for k, v in d.items())
    return f"  [T{e['turn']:>3} P{e['player']}] {t} | {parts}"


def do_single():
    da, db = decklib.deck_lightning_demo(), decklib.deck_fire_demo()
    b = run_match(da, db, BaselineAI("baseline-A"), BaselineAI("baseline-B"),
                  seed=20260908)
    print(f"=== 单局复盘 seed={b.seed}  winner={b.winner} "
          f"({b.players[b.winner].name if b.winner is not None else 'DRAW'}) "
          f"reason={b.win_reason} turns={b.turn_number} ===")
    for e in b.events:
        print(fmt_event(e))
    for i, p in enumerate(b.players):
        act = p.active.summary() if p.active else None
        print(f"P{i} {p.name}: active={act} bench={len(p.bench)} "
              f"prizes={p.prizes_left} deck={len(p.deck)} hand={len(p.hand)}")


def do_batch(n: int = 200):
    da, db = decklib.deck_lightning_demo(), decklib.deck_psychic_demo()
    print(f"=== 批量对局 n={n}：lightning(baseline) vs psychic(random) ===")
    st = run_many(da, db, BaselineAI("baseline"), RandomAgent("random", seed=7),
                  n=n, cfg=GameConfig(max_turns=120))
    for k, v in st.items():
        print(f"  {k:<20} {v}")


def do_dump(seed: int):
    da, db = decklib.deck_lightning_demo(), decklib.deck_fire_demo()
    b = run_match(da, db, BaselineAI("A"), BaselineAI("B"), seed=seed)
    path = Path(f"match_{seed}.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for e in b.events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    print(f"events={len(b.events)} -> {path} (winner={b.winner} "
          f"reason={b.win_reason})")


if __name__ == "__main__":
    argv = sys.argv[1:]
    cmd = argv[0] if argv else "single"
    if cmd == "single":
        do_single()
    elif cmd == "batch":
        do_batch(int(argv[1]) if len(argv) > 1 else 200)
    elif cmd == "dump":
        do_dump(int(argv[1]) if len(argv) > 1 else 20260908)
    else:
        print(__doc__)
