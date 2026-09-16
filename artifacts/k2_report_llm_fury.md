# PokéLab AI · K2 卡牌效果解析报告

- 生成时间：2026-09-16 14:14:39（耗时 1171.2s）
- 后端：**llm**（`llm` 需配置 `POKELAB_LLM_BASE` / `POKELAB_LLM_KEY` / `POKELAB_LLM_MODEL`；`auto` 无配置自动回退规则）
- 黄金集：1.0-fury-fix-subset（8 条；手工标注真值，非解析器生成）
- K2 目标：严格准确率 ≥85%（PRD）

## 1. 指标汇总

| 指标 | 数值 | 说明 |
|---|---|---|
| **严格准确率（K2 主指标）** | **37.5%** | 全部已声明字段一致 |
| 解析成功率 parse_rate | 87.5% | 产出合法 Schema JSON 的比例 |
| kind 准确率 | 75.0% | 招式/特性/物品/支援者/能量/规则 |
| trigger 准确率 | 87.5% | on_use / static / on_evolve / on_knockout … |
| 动作类型集合 | 75.0% | 多重集比对 |
| 数值（伤害/张数） | 62.5% | 指示物 1 个 = 10 点 |
| 目标（target） | 62.5% | active_opponent / self / deck … |
| 每回合限制 | 100.0% | once_per_turn |

### 分组（诚实口径）

- **已知句式（seen，7 条）**：42.9% —— 开发期已覆盖的句式，仅说明回归稳定，不代表泛化能力；
- **留出句式（holdout，1 条）**：0.0% —— 与开发期**不同**的表述，未做针对性适配，是更可信的泛化估计。

**K2 判定（llm 后端）**：未达 ❌（37.5% vs 门槛 85%）

## 2. 逐例结果

| # | 用例 | 组 | kind | trigger | 动作集 | 数值 | 目标 | 限制 | 结论 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | atk_phantom_dive | seen | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✅ |
| 2 | atk_fury_swipes | seen | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✅ |
| 3 | atk_corner | seen | ✓ | ✓ | ✓ | ✗ | ✗ | — | ❌ |
| 4 | ab_recon_scout | seen | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✅ |
| 5 | en_psychic | seen | ✓ | ✗ | ✓ | ✓ | ✓ | — | ❌ |
| 6 | it_ultra_ball | seen | ✓ | ✓ | ✗ | ✗ | ✗ | — | ❌ |
| 7 | it_hammer | seen | ✗ | ✓ | ✗ | ✗ | ✗ | — | ❌ |
| 8 | ho_status_phrase | ho | ✗ | ✓ | ✓ | ✓ | ✓ | — | ❌ |

## 3. 失败用例明细（= 待补句式清单）

- **atk_corner**：`造成 30 点伤害。对手的战斗宝可梦下回合无法撤退。`
  - amount: 期望 {'damage': [30], 'rule': [None]} ≠ 实际 {'damage': [30], 'rule': [1]}
  - target: 期望 {'damage': ['active_opponent'], 'rule': ['any']} ≠ 实际 {'damage': ['active_opponent'], 'rule': ['opponent']}
  - Schema 错误：['[0] action.target 非法：opponent']
- **en_psychic**：`提供 1 点超能量。`
  - trigger: 期望 'on_use' ≠ 实际 'static'
- **it_ultra_ball**：`弃 2 张手牌，从牌库检索 1 张宝可梦。`
  - action_set: 期望 ['discard', 'search'] ≠ 实际 ['discard']
  - amount: 期望 {'discard': [2], 'search': [1]} ≠ 实际 {'discard': [2]}
  - target: 期望 {'discard': ['hand'], 'search': ['deck']} ≠ 实际 {'discard': ['hand']}
- **it_hammer**：`抛硬币，正面则弃掉对手战斗宝可梦身上 1 个能量。`
  - kind: 期望 'item' ≠ 实际 'attack'
  - action_set: 期望 ['discard'] ≠ 实际 ['coin_flip']
  - amount: 期望 {'discard': [1]} ≠ 实际 {'coin_flip': [None]}
  - target: 期望 {'discard': ['active_opponent']} ≠ 实际 {'coin_flip': ['self']}
- **ho_status_phrase**（holdout）：`使对手的战斗宝可梦陷入灼伤。`
  - kind: 期望 'attack' ≠ 实际 'ability'

## 4. 真实卡池解析（desc_zh → spec → 引擎 tag）

跳过（-）。

## 5. 诚实标注（必读）

- **后端差异**：`rule` 是**离线规则基线**（零依赖、确定性），对开发期已覆盖句式接近满分；它**不能**代表 LLM 的泛化水平。LLM 后端的数字需配置 API Key 后实测（本报告不臆测）。
- **样本量**：黄金集仅 8 条（PRD 目标 200+，扩充属 P1）；留出集仅 1 条，置信区间较宽，勿据此下强结论。
- **seen vs holdout**：seen 高不代表可用——请以 **holdout 准确率**作为当前泛化水平的参考，并把「失败用例明细」当作下一轮补句式的 TODO。
- **Schema 扩展**：为表达中文效果，本实现相对提示词初始 Schema 增加了 `status`（状态）与 `rule`（静态规则修改）两种 action.type；`packages/agent/prompts/parse_effects_system.md` 已同步（见 commit）。
- 数据来源：`packages/engine/engine/data/cards_real.json` 的 `desc_zh`（**自述转写**，非官方文案；C2/C5）。

