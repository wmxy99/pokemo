# PokéLab AI

> 面向宝可梦 TCG 的 AI 对战研究与训练开源平台：自研简化规则引擎 + 卡牌效果结构化数据管道
> （LLM/RAG 解析）+ 强化学习 Agent + 可交互 Web 平台。宝可梦为**第一验证域** ——
> 沉淀的「规则引擎 + 自对弈 RL + 卡牌理解」能力可迁移其它卡牌游戏。

**状态**：alpha · 引擎 **v1.4**（结算与机制测试 **891** 项）· 全仓 **1714** 项测试全绿
（engine 891 · agent 676 · api 135 · gym 12）
<!-- 上面这行由 scripts/check_doc_counts.py 在 CI 中核对（计数类文档必须实证） -->

| | |
|---|---|
| 许可 | 核心代码 **Apache-2.0**；`packages/content-pokemon` **PolyForm Noncommercial 1.0.0** |
| 仓库 | `packages/`（engine · card-db · agent · gym）+ `apps/`（api · web · cli） |
| 卡池 | 收录 **1267** 张，其中引擎可对战 **1005** 张（79.3%，**差异逐一披露**，见下「已知边界」） |
| K2（卡牌效果解析） | 本地 4B **40%**（不可用）→ 云端 **100%**（50 条工程基准集，**含过拟合提醒**，见下） |

## 快速开始

```bash
# ① 引擎：无需三方依赖，装好 Python 3.12 即可
cd packages/engine && python -m unittest discover -s tests      # 788 项

# ② 平台（后端 = FastAPI，同时托管前端构建产物到 /app）
pip install -r apps/api/requirements.txt
cd apps/web && npm ci && npm run build && cd ..
python -m uvicorn apps.api.app.main:app --host 127.0.0.1 --port 8000
# → 前端 http://127.0.0.1:8000/app/   · 接口文档 http://127.0.0.1:8000/docs

# ③ CLI：单局复盘 / 批量对局统计
cd apps/cli && python run_demo.py single
cd apps/cli && python run_demo.py batch 100
```

> ⚠ **测试命令必须 `cd` 进包目录再 `discover`** —— 从仓库根跑会 import 失败（CI 亦如此）。
> 运行数据落在 `~/.pokelab/`（可用 `POKELAB_DATA_DIR` 覆盖），**不在仓库内**，
> 备份与 `.field_key` 注意事项见 `docs/architecture.md`。

**一条命令本地复刻 CI 的全部 8 个 job**（CI 结果只能上 GitHub 看，而本机 conda 环境
预装了 torch / cryptography / PyJWT，极易造成「本地假绿、CI 真红」）：

```bash
python scripts/ci_local.py              # 全部 job；缺依赖的 job 会明确 SKIP 并说明原因
python scripts/ci_local.py engine api   # 只跑指定 job（--list 可看全部）
```

## 仓库地图

| 路径 | 内容 | 状态 | 许可 |
|------|------|:---:|:---:|
| `packages/engine` | 自研简化版对战规则引擎（回合状态机 / 裁判 / 结算闭环 / 事件日志 / 真实卡池） | v1.4 · 486 测 | Apache-2.0 |
| `packages/card-db` | 卡牌数据管道 + 规则知识库（代码与 schema 可发布；**数据运行时本地导入**） | 1267 卡入库 | Apache-2.0 |
| `packages/agent` | 决策链路（状态编码 → 策略网 → MCTS → LangGraph）+ RL 训练（BC→PPO）+ K2 提示词库 | 383 测 | Apache-2.0 |
| `packages/gym` | 单智能体博弈环境（gymnasium 风格 `reset/step`，零运行时依赖，含对手 benchmark）+ 可选张量适配层（`pokegym.torch_obs`） | 12 测 | Apache-2.0 |
| `apps/api` | FastAPI 后端：对战 / 批量 / 训练 / 卡组 / 卡库 / 统计 / 合规 / 模型配置（**43 路由 / 39 路径**） | 132 测 | Apache-2.0 |
| `apps/web` | React 18 + Vite 前端：4 个页面（对战·含批量 / 卡组与卡库 / RL 训练·含数据可视化 / 模型配置，页内分段标签切换子页） | 已实现 | Apache-2.0 |
| `apps/cli` | 单局 / 批量对局命令行入口 | 可用 | Apache-2.0 |
| `packages/content-pokemon` | 宝可梦内容样例（**自述转写，无官方图/原文**） | — | **PolyForm NC** |

## 平台能力（apps/api + apps/web）

- **对战**：单局与批量合并为一个入口（页内分段标签切换）。规则基线 / MCTS / 策略网三类 Agent，
  逐回合事件日志与中文指令面板（动作面板 = 引擎 `legal_actions` **1:1 映射**，不一致即 bug）；
  批量任务可中断且**保留已完成局**，SSE 实时进度（含断线续传回放），平衡性报告
  （先后手胜率 / 平均回合，md / csv / json）
- **卡组与卡库**：卡组自由编排（搜索卡库 → 加减张数 → 60 张/同名上限/基础宝可梦校验 · 起手模拟 · 保存后可直接对战）
  与卡库检索（1267 张中/英/日检索、`engine_ready` 筛选、覆盖率总览）合并为一个入口
- **合规展示**：全站页脚恒定呈现内容合规 / 数据隐私要点，完整机器可读声明见 `/compliance` JSON
- **模型配置页**：DeepSeek 端点、密钥加密存储、外发白名单与强制审计、连通性测试

## K2 · 卡牌效果解析（当前结论）

| 后端 | 黄金集 strict（50 条人工真值） | 说明 |
|---|---:|---|
| `rule`（离线确定性基线） | **100%** | 关键词匹配，零成本、可复现 |
| 本地 Qwen3-4B 8bit | **40%** | 已确认**不足以承担 K2**（4bit 更差：空返回率 10×） |
| **云端 deepseek-chat** | **100%** | **保底启用后达标**；证据 `artifacts/k2_report_cloud6.md` |

> **⚠️ 诚实标注**：云端 100% 是**工程基准集**成绩，该集经 6 轮提示词迭代（**存在过拟合风险**），
> 独立证据仅 holdout（n=10，全对）。**扩集至 200+ 条（P1）前不应作为生产结论。**
> 过程与逐条归因见 `artifacts/k2_p1_validation_report.md` §8.9–8.11。

## 已知边界（**如实披露，绝不静默降级**）

| 边界 | 数值 | 披露位置 |
|---|---|---|
| 卡池引擎覆盖率 | 1005 / 1267 = **79.3%** | `packages/card-db/data/cleaned/unmodeled_registry.md`（永久登记 · C5） |
| 未建模动作条目 | **268** 条（**全部纳入实现范围**，见 `docs/RULES_COMPLETION_PLAN.md`） | 同上（含批次图例与处置） |
| 规则简化子集 | 先手不禁攻（开关已留）· 每回合抽 1 · 起手自动铺后备 · 攻击即结束回合 | `packages/engine/README.md`「规则边界」 |
| 引擎未实现卡 | 对战时以基础能量**占位**并在 UI 显式告知 | 卡组页 / 预设清单三态标注 |

## 数据获取指引（**红线**）

本仓库**不包含**任何禁止再分发的内容，CI 有守卫拦截：

- ❌ 不入库：Kaggle 竞赛卡牌数据 · 官方卡图 · 官方规则书 / Compendium 原文 · 个人本地路径
- ✅ 可入库：自述转写（非官方文案）的效果文本 · 卡牌元数据（名称/系列/编号，作数据键）·
  自造样例（原创代名）
- 数据在**运行时**由使用者本地导入；`packages/card-db/README.md` 说明导入脚本与流程

## 贡献

见 [CONTRIBUTING.md](CONTRIBUTING.md)（含**可直接复制运行**的测试与启动命令）。
架构与运行时数据、ADR 见 [docs/](docs/)。安全披露见 [SECURITY.md](SECURITY.md)。

## 许可与免责

核心代码：Apache License 2.0。宝可梦内容包（`packages/content-pokemon`）：PolyForm
Noncommercial 1.0.0 —— **严禁任何商业利用其中的宝可梦内容**。

Pokémon and Pokémon TCG are © Nintendo / Creatures Inc. / GAME FREAK inc. /
The Pokémon Company. PokéLab AI is an unofficial, non-commercial fan project
and is not affiliated with, endorsed by, or sponsored by The Pokémon Company
or Nintendo.
