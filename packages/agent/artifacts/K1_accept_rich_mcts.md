# PokéLab AI · K1 验收报告（对规则基线 · 双盲协议）

- 生成：2026-09-18 10:28:23
- 口径：**双盲 200 局**（交替先后手）· 镜像卡组 `lightning` · seed0=0 · device=cpu
- 决策配置：**MCTS iters=24 × depth=2（价值头标定 D:/pokemo/pokelab-ai/packages/agent/artifacts/value_calib_rich.json）**
- KPI：**K1 对规则基线胜率 ≥ 0.70**（PRD）

## 结果

| 权重 | 决策 | 双盲胜率 | n | 95%CI | 单侧胜率（对照） | 耗时 |
|---|---|---:|---:|---|---:|---:|
| `D:/pokemo/pokelab-ai/packages/agent/artifacts/k1_rich.pt` | MCTS24×d2 | **0.780** ✅ | 200 | ±0.057 | — | 121.6s |

- **基线自对局先手参照**：0.640（说明该引擎先手优势约 14pp；单侧口径会把这份优势算进策略战绩，故不能用于 K1 判定）
- 混合座位构成：100 局先手 + 100 局后手

## K1 判定

**结论：PASS ✅** —— 最佳双盲胜率 0.780（D:/pokemo/pokelab-ai/packages/agent/artifacts/k1_rich.pt）
- 判据：双盲胜率 ≥ 0.70（PRD「200 局双盲口径」）；已满足
