# Contributing to PokéLab AI

感谢贡献！三条主路径（对应 monorepo 模块边界，新手指引按难度排序）：

## 1. 数据管道 · packages/card-db（入门）
- 补卡效果结构化样例（`pokelab/effects.py` 校验器 + 自述转写）
- 规则知识库 chunker / 检索改进
- **红线**：不得提交任何 Kaggle 数据、官方卡图或规则书原文（见根 README「数据获取指引」）

## 2. 规则引擎 · packages/engine（进阶）
- 新增 `PokemonDef`（引擎原生卡定义，程序化效果）
- 修复结算 bug → **必须先补一条 fixture**（`tests/test_settlement.py`）再改代码
- 规则差异修正 → 同步更新 `packages/engine/README.md`「规则边界」
- 引擎任何行为变更需保持确定性（同 seed → 同事件序列）

## 3. Agent / 平台（专业）
- RL/MCTS Agent、LangGraph 决策流水线（P2 开启，见 docs/architecture.md）

## 流程
1. Fork → 分支 `feat/xxx`
2. 提交信息规范：`feat|fix|docs|refactor|test: 描述`
3. 本地全绿：`python -m unittest discover -s packages/engine/tests`
4. 开 PR；CI（engine 单测 + card-db 离线 demo + 红线守卫）全过后合并

## 行为准则
公开、尊重、无商业利用宝可梦内容（内容包 PolyForm NC）。
