# PokéLab AI · K1 运行指引（训练 + 验收）

> **K1 定义**：对规则基线（BaselineAI）胜率 **≥ 0.70**，以 **200 局双盲口径**为准（PRD）。

## 〇、⚠️ 口径纠正（2026-09-17，重要）

**过去 K1 用的是「单侧口径」**：`trainer.eval_winrate` 恒把策略放在**先手位**，
再数 `winner == 0`。而本引擎**先手优势约 6pp**（基线自对局先手胜率实测 0.56–0.61）：
与基线打平的策略在这口径下也会显示 ~0.61 → **系统性高估**，且报告里的
「基线AI 自对局参照 win_rate_a=0.610」正是这个偏差的量化。

现在 K1 **只用双盲口径**：一半局数作先手、一半作后手，先手优势被对称抵消。

| 口径 | 函数 | 用途 |
|---|---|---|
| **双盲（K1 判定）** | `trainer.eval_winrate_blind` | **唯一**可用于 K1 判定的口径；同时给出基线自对局先手参照 |
| 单侧（先手固定） | `trainer.eval_winrate` | 仅供**与历史报告对照**；训练期监控 |

读表方式：双盲 ≈0.50 与基线同强；≈0.56–0.61 只是「稳定赢先手」；**≥0.70 才真正强于基线**。

## 一、验收（可重复 · 与训练解耦）

```bash
cd <repo>/packages/agent
python examples/k1_accept.py                          # 默认 artifacts/final_long.pt，双盲 200 局
python examples/k1_accept.py artifacts/a.pt artifacts/b.pt   # 多权重对比
python examples/k1_accept.py --games 200 --report artifacts/K1_accept.md
```

退出码：`0` 达标 / `1` 未达 / `2` 权重缺失。报告含：双盲胜率 + 95%CI + 单侧对照 + 先手参照。

## 二、训练（课程式 BC → vs 基线 PPO → 自对弈 → MCTS 蒸馏）

```bash
# 完整课程（BC 150 局 → 40k 局 vs 基线 → 24k 自对弈 → MCTS 蒸馏 → 双盲 200 局终评）
python -u examples/run_k1_longtrain.py

# 从已有权重续训（自动识别网络宽度；跳过 BC）
python -u examples/run_k1_longtrain.py --from-weights artifacts/final_long.pt \
    --stage1-episodes 20000 --lr 3e-4 --reward-shaping 0.0 --final-games 200

# 关闭「交替先后手」训练（**不推荐**：会白丢后手的一半对局）
python -u examples/run_k1_longtrain.py --no-alternate
```

关键参数：`--bc-episodes/--bc-epochs` · `--stage1-episodes`（vs 固定对手）·
`--stage2-episodes/--stage2-snapshot-every`（对冻结快照自对弈）·
`--stage3-episodes/--stage3-mcts-iters`（MCTS soft 蒸馏）· `--lr/--clip/--ent-coef/--lr-decay/--lr-schedule` ·
`--reward-shaping` · `--features legacy|full` · `--final-games`（**默认 200 = 协议**）·
`--no-alternate` · `--device` · `--save/--report`。

## 三、已实测的结论（改参数前先读）

| # | 结论 | 证据 |
|---|---|---|
| 1 | **训练必须交替先后手**（`--no-alternate` 关掉就只学会先手，而 K1 一半局是后手） | 本次为训练循环新增 `alternate_sides`（`trainer.train_ppo_vs_fixed`） |
| 2 | **奖励塑造（`--reward-shaping`）负作用**：默认 0.05 时 4000 局训练胜率 0.64，设 0 后同配置 **0.67** | 4000 局对照实验（seed=5，其余同参） |
| 3 | **长训会退化**：shaping=0.05 + 15k 局，训练胜率 0.69 → **0.61**，单侧评估 0.70 → 0.525 | 采用 **shaping=0** 后 20k 局期间稳定在 0.69（不再崩） |
| 4 | **价值头 MCTS 不如预期**：iters 100 → 0.650，iters 300 → **0.525**（深搜放大价值头偏差） | 价值头以折扣回报为目标、未做胜率标定；当前不宜靠加迭代提强度 |
| 5 | **MCTS(30) + 策略网 ≈ 网络本身**（0.65–0.73，n=20–30 噪声大），且 2–9 s/局 | 搜索不是当前瓶颈，**瓶颈在网络棋力** |
| 6 | 单侧口径读数为双盲 **+6pp 左右**（0.67 双盲 ↔ 0.73 单侧） | 双盲 200 局 vs 单侧对照 |
| 7 | 现有最佳权重：`artifacts/final_long.pt`（双盲 **0.670**，n=100，±0.092） | `k1_accept.py` |

## 四、想继续冲 0.70 的建议顺序

1. **更多 `--reward-shaping 0` 的 vs 基线长训**（本次实测不再退化，是最有据的方向）
2. **自对弈细化**（`--stage2-episodes` 与冻结快照，把"稳赢先手"变成"稳赢局面"）
3. **MCTS soft 蒸馏**（`--stage3-*`）—— 但注意结论 #4：**先给价值头做胜率标定**，否则蒸馏的是有偏信号
4. **特征升级**（`--features full`：手牌/元数据特征，59 维）—— 需重采 BC 缓存
5. 每次改动都用 `examples/k1_accept.py --games 200` 复测；**只看双盲值**
