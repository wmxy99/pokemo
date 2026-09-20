# PokéLab AI · K1 验收报告（对规则基线 · 双盲协议）

- 生成：2026-09-18 10:53:17
- 口径：**双盲 200 局**（交替先后手）· 镜像卡组 `lightning` · seed0=0 · device=cpu
- 决策配置：**网络 argmax（无搜索）**
- KPI：**K1 对规则基线胜率 ≥ 0.70**（PRD）

## 结果

| 权重 | 决策 | 双盲胜率 | n | 95%CI | 单侧胜率（对照） | 耗时 |
|---|---|---:|---:|---|---:|---:|
| `packages/agent/artifacts/k1_rich.pt` | argmax | **0.790** ✅ | 200 | ±0.056 | 0.830 | 2.2s |
| `packages/agent/artifacts/k1_richfull.pt` | argmax | **0.800** ✅ | 200 | ±0.055 | 0.825 | 2.2s |
| `packages/agent/artifacts/k1_rich40k.pt` | argmax | **0.795** ✅ | 200 | ±0.056 | 0.845 | 2.0s |

- **基线自对局先手参照**：0.640（说明该引擎先手优势约 14pp；单侧口径会把这份优势算进策略战绩，故不能用于 K1 判定）
- 混合座位构成：100 局先手 + 100 局后手

## K1 判定

**结论：PASS ✅** —— 最佳双盲胜率 0.800（packages/agent/artifacts/k1_richfull.pt）
- 判据：双盲胜率 ≥ 0.70（PRD「200 局双盲口径」）；已满足
