# docs · 架构、ADR 与边界文档

| 文档 | 内容 | 状态 |
|------|------|------|
| [`architecture.md`](architecture.md) | **架构总览**：monorepo 依赖图 / 请求链路 / 运行时数据与配置 / LLM 数据链路 / 测试与 CI | ✅ 已补齐 |
| [`RULES_RUNBOOK_B90_PLUS.md`](RULES_RUNBOOK_B90_PLUS.md) | **规则建模运行档案（第九十批起）**：44 批编排、每批卡清单 `(series, number)`、7 步固定工作流、DoD、偏差处理、结构性边界 6 张。**自动生成，勿手改** | 🔵 现行 · **执行依据** |
| [`runbook_batches.json`](runbook_batches.json) | **运行档案的数据真相**：批次 → 卡 → 状态（`status` / `actual_pool` / `actual_refused`）。改进度只改这里，再跑 `render_runbook.py` | 🔵 现行 · 单一数据源 |
| [`RULES_COMPLETION_PLAN.md`](RULES_COMPLETION_PLAN.md) | **规则建模历史台账**：逐批落地卡、静默缺陷修复记录、指标变化（第八十九批及之前） | ✅ 只追加 |
| [`COMPLIANCE.md`](COMPLIANCE.md) | 合规 6.1（内容）/ 6.2（数据隐私）实现状态、脱敏与审计、上线前自查清单 | ✅ 现行 |
| 引擎规则边界 | v1 简化子集与官方规则差异清单 | 见 `packages/engine/README.md`「规则边界」（**不迁入 docs/**，避免两处维护） |
| ADR-LLM-001 | 本地 Qwen3-4B（8bit）+ bge-m3，解析/解说统一本地，K2 不达标切云端保底 | 已确认（技术方案 v1.0）· **保底已启用并验证**，见 `COMPLIANCE.md` |
| ADR-DB-001 | `decks` 归档语义 / `matches.batch_task_id` 反查 | 已确认（数据库设计 v1.1） |

> **ADR 正文约定**：新增 ADR 请在本目录建 `adr-NNN-<slug>.md`，并在此表登记一行。
> 当前两条 ADR 的正文随决策链根文档存放（见下），本表摘要即仓库内可见的权威记录。
>
> 决策链根文档（可行性分析 → 立项 → MVP → 战略 v0.2 → 技术方案 v1.0）为产品/研发
> 评审基线，存储于仓库外；`docs/` 收录工程侧文档：**架构、ADR 摘要、运行边界与合规**。
