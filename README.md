# PokéLab AI

> 面向宝可梦 TCG 的 AI 对战研究与训练开源平台：自研简化规则引擎 + 卡牌效果结构化
> 数据管道（LLM+RAG 解析）+ 强化学习 Agent（规划中）。宝可梦为**第一验证域**——
> 沉淀的「规则引擎 + 自对弈 RL + 卡牌理解」能力可迁移其它卡牌游戏。

**状态**：alpha · 引擎 v0.1（结算用例 34/34 ✅）

| | |
|---|---|
| 许可 | 核心代码 **Apache-2.0**；`packages/content-pokemon` **PolyForm Noncommercial 1.0.0** |
| 仓库 | packages（engine / card-db / agent / gym）+ apps（cli / web）+ content-pokemon |

## 快速开始

```bash
# 引擎：结算用例（34 项）
cd packages/engine && python -m unittest discover -s tests -v

# CLI：单局复盘 / 批量对局统计
cd apps/cli && python run_demo.py single
cd apps/cli && python run_demo.py batch 100
```

## 仓库地图

| 路径 | 内容 | 许可 |
|------|------|:---:|
| `packages/engine` | 自研简化版对战规则引擎（回合状态机/裁判/结算闭环/事件日志） | Apache-2.0 |
| `packages/card-db` | 卡牌数据管道 + 规则知识库（代码/schema 可发布；**数据运行时本地导入**） | Apache-2.0 |
| `packages/agent` | RL/MCTS Agent（P2） | Apache-2.0 |
| `packages/gym` | 博弈智能教学环境（P3，规划） | Apache-2.0 |
| `apps/cli` | 单局/批量对局命令行入口 | Apache-2.0 |
| `apps/web` | Web 前端（P3，规划） | Apache-2.0 |
| `packages/content-pokemon` | 宝可梦内容样例（自述转写，无官方图/原文） | PolyForm NC |

## 规则边界（v1 简化子集）

能量不分颜色 · 先手不禁攻（开关已留）· 每回合抽 1 · 起手自动铺后备 · 攻击即结束回合 ·
训练家/进化/特性为灰名单。详见 `packages/engine/README.md`「规则边界」。

## 贡献

见 [CONTRIBUTING.md](CONTRIBUTING.md)（引擎 / 卡包 / Agent 三条路径）。安全披露见
[SECURITY.md](SECURITY.md)。

## 许可与免责

核心代码：Apache License 2.0。宝可梦内容包（`packages/content-pokemon`）：PolyForm
Noncommercial 1.0.0 —— **严禁任何商业利用其中的宝可梦内容**。

Pokémon and Pokémon TCG are © Nintendo / Creatures Inc. / GAME FREAK inc. /
The Pokémon Company. PokéLab AI is an unofficial, non-commercial fan project
and is not affiliated with, endorsed by, or sponsored by The Pokémon Company
or Nintendo.
