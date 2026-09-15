# PokéLab AI · K2 卡牌效果解析报告

- 生成时间：2026-09-15 22:56:18（耗时 674.7s）
- 后端：**llm**（`llm` 需配置 `POKELAB_LLM_BASE` / `POKELAB_LLM_KEY` / `POKELAB_LLM_MODEL`；`auto` 无配置自动回退规则）
- 黄金集：1.0-llm-failed-subset（34 条；手工标注真值，非解析器生成）
- K2 目标：严格准确率 ≥85%（PRD）

## 1. 指标汇总

| 指标 | 数值 | 说明 |
|---|---|---|
| **严格准确率（K2 主指标）** | **8.8%** | 全部已声明字段一致 |
| 解析成功率 parse_rate | 100.0% | 产出合法 Schema JSON 的比例 |
| kind 准确率 | 44.1% | 招式/特性/物品/支援者/能量/规则 |
| trigger 准确率 | 73.5% | on_use / static / on_evolve / on_knockout … |
| 动作类型集合 | 70.6% | 多重集比对 |
| 数值（伤害/张数） | 29.4% | 指示物 1 个 = 10 点 |
| 目标（target） | 35.3% | active_opponent / self / deck … |
| 每回合限制 | 40.0% | once_per_turn |

### 分组（诚实口径）

- **已知句式（seen，26 条）**：7.7% —— 开发期已覆盖的句式，仅说明回归稳定，不代表泛化能力；
- **留出句式（holdout，8 条）**：12.5% —— 与开发期**不同**的表述，未做针对性适配，是更可信的泛化估计。

**K2 判定（llm 后端）**：未达 ❌（8.8% vs 门槛 85%）

## 2. 逐例结果

| # | 用例 | 组 | kind | trigger | 动作集 | 数值 | 目标 | 限制 | 结论 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | atk_dragon_tail | seen | ✓ | ✓ | ✓ | ✗ | ✗ | — | ❌ |
| 2 | atk_nightmare | seen | ✓ | ✓ | ✓ | ✗ | ✓ | — | ❌ |
| 3 | atk_mind_bend | seen | ✓ | ✓ | ✓ | ✗ | ✓ | — | ❌ |
| 4 | atk_itchy_pollen | seen | ✓ | ✓ | ✓ | ✗ | ✓ | — | ❌ |
| 5 | atk_tantrum | seen | ✓ | ✓ | ✓ | ✗ | ✓ | — | ❌ |
| 6 | atk_fury_swipes | seen | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✅ |
| 7 | atk_corner | seen | ✓ | ✓ | ✓ | ✗ | ✗ | — | ❌ |
| 8 | ab_recon_scout | seen | ✗ | ✓ | ✗ | ✗ | ✗ | ✗ | ❌ |
| 9 | ab_run_draw | seen | ✗ | ✗ | ✓ | ✗ | ✗ | ✓ | ❌ |
| 10 | ab_cursed_blast | seen | ✗ | ✓ | ✗ | ✗ | ✗ | — | ❌ |
| 11 | ab_adrenaline | seen | ✗ | ✓ | ✗ | ✗ | ✗ | ✗ | ❌ |
| 12 | ab_verdant_dance | seen | ✓ | ✗ | ✓ | ✓ | ✓ | ✓ | ❌ |
| 13 | ab_flame_dance | seen | ✓ | ✗ | ✓ | ✓ | ✗ | — | ❌ |
| 14 | ab_reversal | seen | ✓ | ✓ | ✓ | ✓ | ✗ | — | ❌ |
| 15 | ab_fairy_domain | seen | ✗ | ✓ | ✓ | ✗ | ✗ | — | ❌ |
| 16 | it_ultra_ball | seen | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✅ |
| 17 | it_hammer | seen | ✗ | ✓ | ✓ | ✓ | ✗ | — | ❌ |
| 18 | it_switch | seen | ✗ | ✗ | ✓ | ✗ | ✗ | — | ❌ |
| 19 | it_night_stretcher | seen | ✓ | ✓ | ✗ | ✗ | ✗ | — | ❌ |
| 20 | it_rare_candy | seen | ✗ | ✓ | ✓ | ✗ | ✓ | — | ❌ |
| 21 | sp_research | seen | ✗ | ✓ | ✓ | ✗ | ✗ | — | ❌ |
| 22 | sp_boss | seen | ✗ | ✗ | ✗ | ✗ | ✗ | — | ❌ |
| 23 | sp_crispin | seen | ✗ | ✓ | ✗ | ✗ | ✗ | — | ❌ |
| 24 | sp_iono | seen | ✗ | ✗ | ✗ | ✗ | ✗ | — | ❌ |
| 25 | ru_energy_once | seen | ✗ | ✓ | ✗ | ✗ | ✗ | ✗ | ❌ |
| 26 | ru_prize | seen | ✓ | ✓ | ✗ | ✗ | ✗ | — | ❌ |
| 27 | ho_status_phrase | ho | ✗ | ✗ | ✓ | ✗ | ✓ | — | ❌ |
| 28 | ho_remove_energy | ho | ✗ | ✓ | ✓ | ✓ | ✗ | — | ❌ |
| 29 | ho_swap | ho | ✗ | ✓ | ✓ | ✗ | ✗ | — | ❌ |
| 30 | ho_heal_hp | ho | ✗ | ✗ | ✓ | ✓ | ✓ | — | ❌ |
| 31 | ho_draw_cards | ho | ✗ | ✓ | ✓ | ✓ | ✗ | — | ❌ |
| 32 | ho_discard_from_deck | ho | ✓ | ✓ | ✗ | ✗ | ✗ | — | ❌ |
| 33 | ho_opponent_discard | ho | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✅ |
| 34 | ho_sleep_phrase | ho | ✗ | ✗ | ✓ | ✗ | ✓ | — | ❌ |

## 3. 失败用例明细（= 待补句式清单）

- **atk_dragon_tail**：`造成 50 点伤害。对手将战斗宝可梦与后备宝可梦互换。`
  - amount: 期望 {'damage': [50], 'switch': [None]} ≠ 实际 {'damage': [50], 'switch': [1]}
  - target: 期望 {'damage': ['active_opponent'], 'switch': ['active_opponent_bench']} ≠ 实际 {'damage': ['active_opponent'], 'switch': ['bench']}
- **atk_nightmare**：`造成 60 点伤害。对手的战斗宝可梦陷入睡眠。`
  - amount: 期望 {'damage': [60], 'status': [None]} ≠ 实际 {'damage': [60], 'status': [1]}
- **atk_mind_bend**：`造成 60 点伤害。对手的战斗宝可梦陷入混乱。`
  - amount: 期望 {'damage': [60], 'status': [None]} ≠ 实际 {'damage': [60], 'status': [1]}
- **atk_itchy_pollen**：`造成 10 点伤害。对手的战斗宝可梦陷入中毒。`
  - amount: 期望 {'damage': [10], 'status': [None]} ≠ 实际 {'damage': [10], 'status': [1]}
- **atk_tantrum**：`造成 30 点伤害。此宝可梦随后陷入混乱。`
  - amount: 期望 {'damage': [30], 'status': [None]} ≠ 实际 {'damage': [30], 'status': [1]}
  - 备注：自身陷入混乱 → target=self
- **atk_corner**：`造成 30 点伤害。对手的战斗宝可梦下回合无法撤退。`
  - amount: 期望 {'damage': [30], 'rule': [None]} ≠ 实际 {'damage': [30], 'rule': [1]}
  - target: 期望 {'damage': ['active_opponent'], 'rule': ['any']} ≠ 实际 {'damage': ['active_opponent'], 'rule': ['self']}
- **ab_recon_scout**：`每回合一次：检视牌库顶 2 张取 1 入手`
  - kind: 期望 'ability' ≠ 实际 'rule'
  - action_set: 期望 ['search'] ≠ 实际 ['rule']
  - amount: 期望 {'search': [1]} ≠ 实际 {'rule': [1]}
  - target: 期望 {'search': ['deck']} ≠ 实际 {'rule': ['self']}
  - limit: 期望 'once_per_turn' ≠ 实际 None
- **ab_run_draw**：`每回合一次：抽至手牌 5 张（引擎近似检索）`
  - kind: 期望 'ability' ≠ 实际 'attack'
  - trigger: 期望 'static' ≠ 实际 'on_use'
  - amount: 期望 {'draw': [None]} ≠ 实际 {'draw': [5]}
  - target: 期望 {'draw': ['hand']} ≠ 实际 {'draw': ['self']}
  - 备注：「抽至手牌 N」→ to_hand_size
- **ab_cursed_blast**：`手牌弃此卡 → 对手 1 只宝可梦放 13 个指示物`
  - kind: 期望 'ability' ≠ 实际 'attack'
  - action_set: 期望 ['discard', 'spread'] ≠ 实际 ['spread']
  - amount: 期望 {'discard': [1], 'spread': [130]} ≠ 实际 {'spread': [130]}
  - target: 期望 {'discard': ['hand'], 'spread': ['active_opponent_bench']} ≠ 实际 {'spread': ['active_opponent_bench']}
- **ab_adrenaline**：`每回合一次：从自身移动至多 3 个伤害指示物到对手的战斗宝可梦。`
  - kind: 期望 'ability' ≠ 实际 'rule'
  - action_set: 期望 ['move_damage'] ≠ 实际 ['rule']
  - amount: 期望 {'move_damage': [3]} ≠ 实际 {'rule': [3]}
  - target: 期望 {'move_damage': ['active_opponent']} ≠ 实际 {'rule': ['self']}
  - limit: 期望 'once_per_turn' ≠ 实际 None
  - 备注：移动指示物两侧皆可，本表按对手侧标注
- **ab_verdant_dance**：`每回合一次：从手牌附 1 张基本草能量到此宝可梦（引擎子集：附带抽 1 未建模）。`
  - trigger: 期望 'static' ≠ 实际 'on_use'
- **ab_flame_dance**：`被动：由手牌进化此宝可梦时，可从弃牌堆至多取 2 张基本火能量附到后备宝可梦（引擎子集：附到自身）。`
  - trigger: 期望 'on_evolve' ≠ 实际 'static'
  - target: 期望 {'attach': ['self']} ≠ 实际 {'attach': ['bench']}
  - 备注：「进化时」是触发时机，不产出 evolve 动作
- **ab_reversal**：`被动：己方宝可梦被招式击倒时，抽 3 张牌。`
  - target: 期望 {'draw': ['deck']} ≠ 实际 {'draw': ['self']}
  - 备注：「被击倒」是触发时机，不产出 knockout 动作
- **ab_fairy_domain**：`被动：在场时，对手龙系宝可梦的弱点变为超。`
  - kind: 期望 'ability' ≠ 实际 'rule'
  - amount: 期望 {'rule': [None]} ≠ 实际 {'rule': [1]}
  - target: 期望 {'rule': ['any']} ≠ 实际 {'rule': ['self']}
- **it_hammer**：`抛硬币，正面则弃掉对手战斗宝可梦身上 1 个能量。`
  - kind: 期望 'item' ≠ 实际 'attack'
  - target: 期望 {'discard': ['active_opponent']} ≠ 实际 {'discard': ['opponent_hand']}
- **it_switch**：`将自己的战斗宝可梦与后备宝可梦互换。`
  - kind: 期望 'item' ≠ 实际 'rule'
  - trigger: 期望 'on_use' ≠ 实际 'static'
  - amount: 期望 {'switch': [None]} ≠ 实际 {'switch': [1]}
  - target: 期望 {'switch': ['self']} ≠ 实际 {'switch': ['bench']}
- **it_night_stretcher**：`从弃牌堆取 1 张宝可梦或基础能量放回手牌。`
  - action_set: 期望 ['search'] ≠ 实际 ['draw']
  - amount: 期望 {'search': [1]} ≠ 实际 {'draw': [1]}
  - target: 期望 {'search': ['discard']} ≠ 实际 {'draw': ['self']}
- **it_rare_candy**：`选择手牌 1 张 2 阶进化卡，直接进化自己的基础宝可梦。`
  - kind: 期望 'item' ≠ 实际 'ability'
  - amount: 期望 {'evolve': [None]} ≠ 实际 {'evolve': [1]}
- **sp_research**：`弃掉手牌，然后抽 7 张。`
  - kind: 期望 'supporter' ≠ 实际 'item'
  - amount: 期望 {'discard': [None], 'draw': [7]} ≠ 实际 {'discard': [1], 'draw': [7]}
  - target: 期望 {'discard': ['hand'], 'draw': ['deck']} ≠ 实际 {'discard': ['hand'], 'draw': ['self']}
- **sp_boss**：`对手将战斗宝可梦与后备宝可梦互换，由你选择。`
  - kind: 期望 'supporter' ≠ 实际 'rule'
  - trigger: 期望 'on_use' ≠ 实际 'static'
  - action_set: 期望 ['switch'] ≠ 实际 ['rule']
  - amount: 期望 {'switch': [None]} ≠ 实际 {'rule': [0]}
  - target: 期望 {'switch': ['active_opponent_bench']} ≠ 实际 {'rule': ['any']}
- **sp_crispin**：`从牌库检索至多 2 张基础能量，附着到自己的宝可梦上。`
  - kind: 期望 'supporter' ≠ 实际 'item'
  - action_set: 期望 ['attach', 'search'] ≠ 实际 ['search']
  - amount: 期望 {'search': [2], 'attach': [None]} ≠ 实际 {'search': [2]}
  - target: 期望 {'search': ['deck'], 'attach': ['self']} ≠ 实际 {'search': ['deck']}
  - 备注：检索 + 附着 两个动作
- **sp_iono**：`双方将手牌洗回牌库，然后各抽 6 张。`
  - kind: 期望 'supporter' ≠ 实际 'rule'
  - trigger: 期望 'on_use' ≠ 实际 'static'
  - action_set: 期望 ['draw'] ≠ 实际 ['rule']
  - amount: 期望 {'draw': [6]} ≠ 实际 {'rule': [0]}
  - target: 期望 {'draw': ['deck']} ≠ 实际 {'rule': ['any']}
- **ru_energy_once**：`每回合只能附着 1 次能量。`
  - kind: 期望 'rule' ≠ 实际 'item'
  - action_set: 期望 ['attach'] ≠ 实际 []
  - amount: 期望 {'attach': [None]} ≠ 实际 {}
  - target: 期望 {'attach': ['self']} ≠ 实际 {}
  - limit: 期望 'once_per_turn' ≠ 实际 None
  - 备注：限制由 limit 条件承载
- **ru_prize**：`对手宝可梦被击倒时，你获得 1 张奖赏卡。`
  - action_set: 期望 ['rule'] ≠ 实际 ['draw']
  - amount: 期望 {'rule': [None]} ≠ 实际 {'draw': [1]}
  - target: 期望 {'rule': ['any']} ≠ 实际 {'draw': ['self']}
- **ho_status_phrase**（holdout）：`使对手的战斗宝可梦陷入灼伤。`
  - kind: 期望 'attack' ≠ 实际 'ability'
  - trigger: 期望 'on_use' ≠ 实际 'static'
  - amount: 期望 {'status': [None]} ≠ 实际 {'status': [1]}
- **ho_remove_energy**（holdout）：`移除对手战斗宝可梦身上的 1 个能量。`
  - kind: 期望 'item' ≠ 实际 'attack'
  - target: 期望 {'discard': ['active_opponent']} ≠ 实际 {'discard': ['opponent_hand']}
  - 备注：「移除…能量」句式（引擎/基线未覆盖，预期失败 → 待补）
- **ho_swap**（holdout）：`将自己的后备宝可梦与战斗宝可梦交换。`
  - kind: 期望 'item' ≠ 实际 'supporter'
  - amount: 期望 {'switch': [None]} ≠ 实际 {'switch': [1]}
  - target: 期望 {'switch': ['self']} ≠ 实际 {'switch': ['bench']}
  - 备注：「交换」同义于「互换」（基线未覆盖 → 待补）
- **ho_heal_hp**（holdout）：`回复自己宝可梦 30 点 HP。`
  - kind: 期望 'attack' ≠ 实际 'ability'
  - trigger: 期望 'on_use' ≠ 实际 'static'
  - 备注：「回复X宝可梦 N 点 HP」句式（基线未覆盖 → 待补）
- **ho_draw_cards**（holdout）：`抽 3 张牌。`
  - kind: 期望 'item' ≠ 实际 'attack'
  - target: 期望 {'draw': ['deck']} ≠ 实际 {'draw': ['self']}
- **ho_discard_from_deck**（holdout）：`从弃牌堆选择 1 张基础宝可梦放到后备。`
  - action_set: 期望 ['search'] ≠ 实际 ['attach', 'discard']
  - amount: 期望 {'search': [1]} ≠ 实际 {'attach': [1], 'discard': [1]}
  - target: 期望 {'search': ['discard']} ≠ 实际 {'attach': ['bench'], 'discard': ['deck']}
- **ho_sleep_phrase**（holdout）：`使对手的战斗宝可梦陷入睡眠。`
  - kind: 期望 'attack' ≠ 实际 'ability'
  - trigger: 期望 'on_use' ≠ 实际 'static'
  - amount: 期望 {'status': [None]} ≠ 实际 {'status': [1]}

## 4. 真实卡池解析（desc_zh → spec → 引擎 tag）

跳过（-）。

## 5. 诚实标注（必读）

- **后端差异**：`rule` 是**离线规则基线**（零依赖、确定性），对开发期已覆盖句式接近满分；它**不能**代表 LLM 的泛化水平。LLM 后端的数字需配置 API Key 后实测（本报告不臆测）。
- **样本量**：黄金集仅 34 条（PRD 目标 200+，扩充属 P1）；留出集仅 8 条，置信区间较宽，勿据此下强结论。
- **seen vs holdout**：seen 高不代表可用——请以 **holdout 准确率**作为当前泛化水平的参考，并把「失败用例明细」当作下一轮补句式的 TODO。
- **Schema 扩展**：为表达中文效果，本实现相对提示词初始 Schema 增加了 `status`（状态）与 `rule`（静态规则修改）两种 action.type；`packages/agent/prompts/parse_effects_system.md` 已同步（见 commit）。
- 数据来源：`packages/engine/engine/data/cards_real.json` 的 `desc_zh`（**自述转写**，非官方文案；C2/C5）。

