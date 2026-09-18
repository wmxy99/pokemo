# -*- coding: utf-8 -*-
"""MCTS 配置 × 双盲 200 局正式对比（K1 攻坚 · 只看双盲值）"""
import sys, time, json
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
# 包布局：packages/engine/engine/ 与 packages/agent/agent/ →
# 必须把 **packages/<pkg>** 加进 path（只加 packages 会 import 到命名空间目录）
_PKG = Path(__file__).resolve().parents[2]
for _sub in ("engine", "agent"):
    sys.path.insert(0, str(_PKG / _sub))
# 与 run_k1_longtrain.py / k1_accept.py 的路径处理保持一致
from engine import decklib
from agent import trainer
from agent.trainer import load_net
from agent.mcts import load_value_calib

da = decklib.make_deck("lightning"); db = list(da)
net = load_net("artifacts/k1_cont40k.pt", device="cpu")
cal = load_value_calib("artifacts/value_calib.json")
rows = []
# 网络单独（同 seeds 对照）
t0 = time.time()
r = trainer.eval_winrate_blind(da, db, net, n=200, device="cpu", seed0=0)
rows.append({"tag": "网络单独（对照）", **r, "secs": round(time.time()-t0, 1)})
print(f"[sweep] 网络单独 0.695? → {r['win_rate']:.3f} ({r['wins']}/200) "
      f"{time.time()-t0:.0f}s", flush=True)
for iters, depth in ((24, 1), (24, 2), (24, 3), (60, 2), (60, 3)):
    t0 = time.time()
    r = trainer.eval_winrate_blind_mcts(da, db, net, n=200, iters=iters,
                                       value_calib=cal, max_depth=depth,
                                       device="cpu", seed0=0)
    rows.append({"tag": f"MCTS iters={iters} depth={depth}", **r,
                 "secs": round(time.time()-t0, 1)})
    print(f"[sweep] iters={iters} depth={depth} → {r['win_rate']:.3f} "
          f"({r['wins']}/200) {time.time()-t0:.0f}s", flush=True)
Path("artifacts/k1_mcts_sweep.json").write_text(
    json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
print("[sweep] 完成 → artifacts/k1_mcts_sweep.json", flush=True)
