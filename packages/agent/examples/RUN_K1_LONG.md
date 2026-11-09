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
| 4 | ⭐ **动作特征缺"作用对象"是真正的瓶颈**：`--action-features rich`（16→33 维；历史产物是 14→31）使双盲 **0.695 → 0.790（+9.5pp）** | 同 seed 对照：rich 训练 10k 局时双盲 0.783 vs basic 同阶段 0.650；终评 n=200 |
| 5 | **价值头必须做胜率标定**，否则 MCTS 越搜越差：iters=100 → 0.650，300 → 0.525 | 实测价值头**系统性乐观 ~12pp**（均值 0.29 vs 实际胜率 0.52）；标定后 Brier 0.164→0.146 |
| 6 | **MCTS 必须多 ply**：1-ply 打不赢网络本身（0.665 vs 0.695）；**depth=2 → 0.700**；depth=3 → 0.685（更慢更差） | n=200 双盲 sweep（`k1_mcts_sweep.py`）—— 1-ply 时 63% 的"动作后局面"行动方仍是我方，信息增益≈0 |
| 7 | **加训练量/加参数都不动**：full 状态特征、自对弈细化、宽网络 128、多权重集成、高熵低 LR —— 双盲全部落 **0.685–0.700**（±0.005，统计不可区分） | 各自 n=200 双盲；`K1_selfplay_snaps.md`、`k1_full20k.md`、`k1_wide128.md` 等 |
| 8 | **不存在"结构性天花板"**：150 个 seed 中"坐哪都输"的比例 **0%** → 上限远高于 0.70 | 座位互换诊断（同 seed 跑两次先/后手） |
| 9 | 单侧口径读数为双盲 **+4~6pp**（0.790 双盲 ↔ 0.830 单侧） | 双盲 200 局 vs 单侧对照 |

## 四、当前最佳配置（已达 K1）

```bash
# 训练（rich 动作特征必须**从头训练**，不能续跑 basic 权重）
python examples/run_k1_longtrain.py \
  --action-features rich --stage1-episodes 20000 --stage2-episodes 0 \
  --lr 3e-4 --ent-coef 0.005 --clip 0.2 --reward-shaping 0.0 \
  --eval-every 10000 --games 60 --final-games 200 --device cpu --seed 707 \
  --save artifacts/k1_rich.pt --report artifacts/k1_rich.md

# 验收（唯一判据：双盲 200 局）
python examples/k1_accept.py artifacts/k1_rich.pt --games 200
```

**若还要更高，按此顺序**（都需先证净收益）：

1. **rich 特征下的更长训练**（当前 20k 局；rich 明显未饱和 —— 10k 局时已达 0.783）
2. **rich + full 状态特征组合**（59+33 维；历史产物 59+31）
3. **rich + MCTS depth 2**（搜索配置需为**该权重**重做标定：`calibrate_value.py`）
4. 每次改动都用 `examples/k1_accept.py --games 200` 复测，**只看双盲值**；
   并用 `--seed0 1000` 换一段独立 seed 复验（避免只在调参用的 seed 上生效）

## 四之二、2026-11-09 的复现（S1b：动词表加 `choose`/`stop` → 16/33 维）

改编码器后必须**同一个 PR** 完成「加动词 + 重训 + 重测 K1」（方案 §6.3）。
本次用的是上面**§四 的同一套配置**（`--features full --action-features rich
--stage1 20000 --stage2 0 --lr 3e-4 --ent-coef 0.005 --clip 0.2
--reward-shaping 0.0 --seed 707`）：

| 项 | 结果 |
|---|---|
| 训练期监控（单侧 n=60） | 10k 局 0.700 → 20k 局 0.750 |
| **K1 双盲 n=200（seed0=0）** | **0.780**（单侧 0.830、先手基线 0.640）|
| **独立 seed 段复验（seed0=1000）** | **0.795** |
| 旧权重回归（`k1_rich.pt`，7 动词/31 维）在新代码下 | **0.770**（与历史逐位一致）|

两条**踩坑记录**（都写进了代码守卫）：

1. **别用 2026-09-09 的旧默认跑**（lr 3e-3 / shaping 0.05 / clip 0.3 / ent 0.05 /
   stage2 24000）：同环境实测 stage1 跑到 10000 局，双盲只有 **0.350**（训练
   胜率也平在 0.38）。改回 §四 的配置后 10k 局即 0.700。
   → 脚本默认值已改成 §四 的配置，并加了守卫
   `test_train_defaults_are_the_documented_best_config`。
2. **旧权重不报废**：动作为 14/31 的 checkpoint 由
   `infer_config_from_state_dict` 反推出「7 动词」布局，`action_dim()=14/31`，
   行为与当年逐位一致。新训练才用 9 动词（16/33）。

## 五、上面 4 条"下一步"的实测结果（2026-09-18，全部双盲 n=200）

| 项 | seed0=0 | seed0=1000 | 判定 |
|---|---:|---:|---|
| `rich`（基线） | 0.790 | 0.775 | 已达标 |
| ① **rich + full 状态**（59+33；历史 59+31） | **0.800** | **0.790** | 高 1.0–1.5pp，**方向一致但噪声内** |
| ② rich 继续加量（+20k，共 40k） | 0.795 | 0.775 | **已饱和**（≈0） |
| ④ 换卡组：fire 镜像 | rich **0.815** / basic **0.720** | rich **0.780** / basic **0.675** | **+9.5pp / +10.5pp —— 跨卡组复现** |

③ **迁移性**（lightning 镜像，n=200）：rich **0.790 vs baseline** = rich **0.790 vs support**
（support 会用训练家/特性，决策逻辑与基线不同）→ 增益**不依赖某个对手的特性**；
`rich vs basic` 的 **head-to-head = 0.720**（双方都是学习策略、交替座位）→ 是**整体更强**，
不只是换了对手偏好。唯一要诚实标注的是 **vs random 只差 +3pp**（噪声内）——
对弱对手谈不上更强。

> 结论：**rich 是稳定的净增益**（跨卡组 +9.5/+10.5pp、跨 seed 段一致）→ 已设为默认；
> **加量已饱和**，不必再加训练量；rich+full 略优但不显著（工程上 rich 更省，state 51 vs 59 维）。
> 若还想提升，剩下的方向都属"**加信息**"而非"加力度"：扩大动作特征覆盖面
> （手牌能量种类、攻击后能量去向）、多卡组联合训练。


