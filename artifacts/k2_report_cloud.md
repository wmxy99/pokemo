# PokéLab AI · K2 卡牌效果解析报告

- 生成时间：2026-09-16 16:26:42（耗时 118.0s）
- 后端：**llm**（`llm` 需配置 `POKELAB_LLM_BASE` / `POKELAB_LLM_KEY` / `POKELAB_LLM_MODEL`；`auto` 无配置自动回退规则）
- 黄金集：1.0（50 条；手工标注真值，非解析器生成）
- K2 目标：严格准确率 ≥85%（PRD）

## 1. 指标汇总

| 指标 | 数值 | 说明 |
|---|---|---|
| **严格准确率（K2 主指标）** | **58.0%** | 全部已声明字段一致 |
| 解析成功率 parse_rate | 100.0% | 产出合法 Schema JSON 的比例 |
| kind 准确率 | 94.0% | 招式/特性/物品/支援者/能量/规则 |
| trigger 准确率 | 94.0% | on_use / static / on_evolve / on_knockout … |
| 动作类型集合 | 92.0% | 多重集比对 |
| 数值（伤害/张数） | 88.0% | 指示物 1 个 = 10 点 |
| 目标（target） | 68.0% | active_opponent / self / deck … |
| 每回合限制 | 100.0% | once_per_turn |

### 分组（诚实口径）

- **已知句式（seen，40 条）**：57.5% —— 开发期已覆盖的句式，仅说明回归稳定，不代表泛化能力；
- **留出句式（holdout，10 条）**：60.0% —— 与开发期**不同**的表述，未做针对性适配，是更可信的泛化估计。

**K2 判定（llm 后端）**：未达 ❌（58.0% vs 门槛 85%）

## 2. 逐例结果

| # | 用例 | 组 | kind | trigger | 动作集 | 数值 | 目标 | 限制 | 结论 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | atk_bite | seen | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✅ |
| 2 | atk_dragon_headbutt | seen | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✅ |
| 3 | atk_dragon_tail | seen | ✓ | ✓ | ✓ | ✓ | ✗ | — | ❌ |
| 4 | atk_jet_headbutt | seen | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✅ |
| 5 | atk_phantom_dive | seen | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✅ |
| 6 | atk_astonish | seen | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✅ |
| 7 | atk_nightmare | seen | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✅ |
| 8 | atk_mind_bend | seen | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✅ |
| 9 | atk_itchy_pollen | seen | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✅ |
| 10 | atk_tantrum | seen | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✅ |
| 11 | atk_fury_swipes | seen | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✅ |
| 12 | atk_corner | seen | ✓ | ✓ | ✓ | ✓ | ✗ | — | ❌ |
| 13 | atk_blood_moon | seen | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✅ |
| 14 | atk_night_daze | seen | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✅ |
| 15 | atk_blaze_kick | seen | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✅ |
| 16 | atk_wondrous_moon | seen | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✅ |
| 17 | ab_recon_scout | seen | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✅ |
| 18 | ab_run_draw | seen | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ | ❌ |
| 19 | ab_cursed_blast | seen | ✗ | ✓ | ✓ | ✓ | ✗ | — | ❌ |
| 20 | ab_adrenaline | seen | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✅ |
| 21 | ab_verdant_dance | seen | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✅ |
| 22 | ab_flame_dance | seen | ✓ | ✓ | ✓ | ✓ | ✗ | — | ❌ |
| 23 | ab_reversal | seen | ✓ | ✓ | ✓ | ✓ | ✗ | — | ❌ |
| 24 | ab_fairy_domain | seen | ✓ | ✓ | ✓ | ✓ | ✗ | — | ❌ |
| 25 | en_psychic | seen | ✓ | ✗ | ✓ | ✓ | ✓ | — | ❌ |
| 26 | en_grass | seen | ✓ | ✗ | ✓ | ✓ | ✓ | — | ❌ |
| 27 | it_poffin | seen | ✓ | ✓ | ✓ | ✓ | ✗ | — | ❌ |
| 28 | it_ultra_ball | seen | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✅ |
| 29 | it_nest_ball | seen | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✅ |
| 30 | it_hammer | seen | ✓ | ✓ | ✗ | ✗ | ✗ | — | ❌ |
| 31 | it_switch | seen | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✅ |
| 32 | it_night_stretcher | seen | ✓ | ✓ | ✗ | ✗ | ✗ | — | ❌ |
| 33 | it_rare_candy | seen | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✅ |
| 34 | sp_research | seen | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✅ |
| 35 | sp_boss | seen | ✓ | ✓ | ✓ | ✓ | ✗ | — | ❌ |
| 36 | sp_crispin | seen | ✓ | ✓ | ✓ | ✗ | ✓ | — | ❌ |
| 37 | sp_iono | seen | ✓ | ✓ | ✓ | ✓ | ✗ | — | ❌ |
| 38 | ru_energy_once | seen | ✓ | ✓ | ✗ | ✗ | ✗ | ✓ | ❌ |
| 39 | ru_prize | seen | ✓ | ✗ | ✓ | ✗ | ✗ | — | ❌ |
| 40 | ru_first_turn | seen | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✅ |
| 41 | ho_damage_variant | ho | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✅ |
| 42 | ho_status_phrase | ho | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✅ |
| 43 | ho_search_phrase | ho | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✅ |
| 44 | ho_remove_energy | ho | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✅ |
| 45 | ho_swap | ho | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✅ |
| 46 | ho_heal_hp | ho | ✗ | ✓ | ✓ | ✓ | ✓ | — | ❌ |
| 47 | ho_draw_cards | ho | ✓ | ✓ | ✓ | ✓ | ✗ | — | ❌ |
| 48 | ho_discard_from_deck | ho | ✓ | ✓ | ✗ | ✗ | ✗ | — | ❌ |
| 49 | ho_opponent_discard | ho | ✗ | ✓ | ✓ | ✓ | ✓ | — | ❌ |
| 50 | ho_sleep_phrase | ho | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✅ |

## 3. 失败用例明细（= 待补句式清单）

- **atk_dragon_tail**：`造成 50 点伤害。对手将战斗宝可梦与后备宝可梦互换。`
  - target: 期望 {'damage': ['active_opponent'], 'switch': ['active_opponent_bench']} ≠ 实际 {'damage': ['active_opponent'], 'switch': ['active_opponent']}
- **atk_corner**：`造成 30 点伤害。对手的战斗宝可梦下回合无法撤退。`
  - target: 期望 {'damage': ['active_opponent'], 'rule': ['any']} ≠ 实际 {'damage': ['active_opponent'], 'rule': ['active_opponent']}
- **ab_run_draw**：`每回合一次：抽至手牌 5 张（引擎近似检索）`
  - target: 期望 {'draw': ['hand']} ≠ 实际 {'draw': ['self']}
  - 备注：「抽至手牌 N」→ to_hand_size
- **ab_cursed_blast**：`手牌弃此卡 → 对手 1 只宝可梦放 13 个指示物`
  - kind: 期望 'ability' ≠ 实际 'item'
  - target: 期望 {'discard': ['hand'], 'spread': ['active_opponent_bench']} ≠ 实际 {'discard': ['self'], 'spread': ['active_opponent']}
- **ab_flame_dance**：`被动：由手牌进化此宝可梦时，可从弃牌堆至多取 2 张基本火能量附到后备宝可梦（引擎子集：附到自身）。`
  - target: 期望 {'attach': ['self']} ≠ 实际 {'attach': ['bench']}
  - 备注：「进化时」是触发时机，不产出 evolve 动作
- **ab_reversal**：`被动：己方宝可梦被招式击倒时，抽 3 张牌。`
  - target: 期望 {'draw': ['deck']} ≠ 实际 {'draw': ['self']}
  - 备注：「被击倒」是触发时机，不产出 knockout 动作
- **ab_fairy_domain**：`被动：在场时，对手龙系宝可梦的弱点变为超。`
  - target: 期望 {'rule': ['any']} ≠ 实际 {'rule': ['active_opponent']}
- **en_psychic**：`提供 1 点超能量。`
  - trigger: 期望 'on_use' ≠ 实际 'static'
- **en_grass**：`提供 1 点草能量。`
  - trigger: 期望 'on_use' ≠ 实际 'static'
- **it_poffin**：`从牌库选择至多 2 张基础宝可梦放至后备。`
  - target: 期望 {'search': ['deck']} ≠ 实际 {'search': ['bench']}
- **it_hammer**：`抛硬币，正面则弃掉对手战斗宝可梦身上 1 个能量。`
  - action_set: 期望 ['discard'] ≠ 实际 ['coin_flip', 'discard']
  - amount: 期望 {'discard': [1]} ≠ 实际 {'coin_flip': [None], 'discard': [1]}
  - target: 期望 {'discard': ['active_opponent']} ≠ 实际 {'coin_flip': ['active_opponent'], 'discard': ['active_opponent']}
- **it_night_stretcher**：`从弃牌堆取 1 张宝可梦或基础能量放回手牌。`
  - action_set: 期望 ['search'] ≠ 实际 ['recover']
  - amount: 期望 {'search': [1]} ≠ 实际 {'recover': [1]}
  - target: 期望 {'search': ['discard']} ≠ 实际 {'recover': ['discard']}
- **sp_boss**：`对手将战斗宝可梦与后备宝可梦互换，由你选择。`
  - target: 期望 {'switch': ['active_opponent_bench']} ≠ 实际 {'switch': ['active_opponent']}
- **sp_crispin**：`从牌库检索至多 2 张基础能量，附着到自己的宝可梦上。`
  - amount: 期望 {'search': [2], 'attach': [None]} ≠ 实际 {'search': [2], 'attach': [2]}
  - 备注：检索 + 附着 两个动作
- **sp_iono**：`双方将手牌洗回牌库，然后各抽 6 张。`
  - target: 期望 {'draw': ['deck']} ≠ 实际 {'draw': ['any']}
- **ru_energy_once**：`每回合只能附着 1 次能量。`
  - action_set: 期望 ['attach'] ≠ 实际 ['rule']
  - amount: 期望 {'attach': [None]} ≠ 实际 {'rule': [None]}
  - target: 期望 {'attach': ['self']} ≠ 实际 {'rule': ['self']}
  - 备注：限制由 limit 条件承载
- **ru_prize**：`对手宝可梦被击倒时，你获得 1 张奖赏卡。`
  - trigger: 期望 'on_knockout' ≠ 实际 'static'
  - amount: 期望 {'rule': [None]} ≠ 实际 {'rule': [1]}
  - target: 期望 {'rule': ['any']} ≠ 实际 {'rule': ['self']}
- **ho_heal_hp**（holdout）：`回复自己宝可梦 30 点 HP。`
  - kind: 期望 'attack' ≠ 实际 'item'
  - 备注：「回复X宝可梦 N 点 HP」句式（基线未覆盖 → 待补）
- **ho_draw_cards**（holdout）：`抽 3 张牌。`
  - target: 期望 {'draw': ['deck']} ≠ 实际 {'draw': ['self']}
- **ho_discard_from_deck**（holdout）：`从弃牌堆选择 1 张基础宝可梦放到后备。`
  - action_set: 期望 ['search'] ≠ 实际 ['recover']
  - amount: 期望 {'search': [1]} ≠ 实际 {'recover': [1]}
  - target: 期望 {'search': ['discard']} ≠ 实际 {'recover': ['bench']}
- **ho_opponent_discard**（holdout）：`对手弃 2 张手牌。`
  - kind: 期望 'attack' ≠ 实际 'item'

## 4. 真实卡池解析（desc_zh → spec → 引擎 tag）

跳过（-）。

## 5. 诚实标注（必读）

- **后端差异**：`rule` 是**离线规则基线**（零依赖、确定性），对开发期已覆盖句式接近满分；它**不能**代表 LLM 的泛化水平。LLM 后端的数字需配置 API Key 后实测（本报告不臆测）。
- **样本量**：黄金集仅 50 条（PRD 目标 200+，扩充属 P1）；留出集仅 10 条，置信区间较宽，勿据此下强结论。
- **seen vs holdout**：seen 高不代表可用——请以 **holdout 准确率**作为当前泛化水平的参考，并把「失败用例明细」当作下一轮补句式的 TODO。
- **Schema 扩展**：为表达中文效果，本实现相对提示词初始 Schema 增加了 `status`（状态）与 `rule`（静态规则修改）两种 action.type；`packages/agent/prompts/parse_effects_system.md` 已同步（见 commit）。
- 数据来源：`packages/engine/engine/data/cards_real.json` 的 `desc_zh`（**自述转写**，非官方文案；C2/C5）。

