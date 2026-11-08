# 规则一致性矩阵（Rule Conformance）

> 一句话：**这张表是可以被推翻的**。每一行都由 `scripts/check_conformance.py`
> 机械核对 —— 实现点符号必须能在文件里搜到、守卫测试必须真实存在、
> 带 tag 前缀的行其「覆盖卡数」必须与 `cards_pool_ext.json` 现算结果一致。
> 写得漂亮但核不上，CI 直接红。

## 怎么读

| 列 | 含义 |
|---|---|
| **规则** | 一条**可判定**的规则（能说清「什么情况下允许/禁止什么」），不是功能名 |
| **实现点** | `文件::符号`，指向真正的落点；多条用 `、` 分隔 |
| **守卫测试** | `文件::类.方法`，改坏这条必须变红的用例 |
| **tag 前缀** | 该规则对应的卡面 tag 前缀（没有就写 `—`），用于**现算**覆盖卡数 |
| **覆盖卡数** | 池内命中该前缀的**卡**数（同一张卡只计一次）；`—` = 不适用 |
| **状态** | `✅` 完整 · `⚠️` 有已知差异（见文末「已知差异」）· `❌` 未建模 |

## 矩阵

| 规则 | 实现点 | 守卫测试 | tag 前缀 | 覆盖卡数 | 状态 |
|---|---|---|---|---|---|
| 开局：洗牌、起手 7 张、无基础则重抽（mulligan）、战斗位 + 后备铺场 | `packages/engine/engine/battle.py::_draw_with_mulligan`、`packages/engine/engine/battle.py::_setup_openings` | `packages/engine/tests/test_flow.py::TestFlow.test_opening_always_has_active` | — | — | ✅ |
| mulligan 补偿：对手每有 1 次 mulligan 可抽 1 张 | `packages/engine/engine/battle.py::_setup_openings` | `packages/engine/tests/test_flow.py::TestFlow.test_opening_always_has_active` | — | — | ⚠️ 自动抽（官方为可选，见差异 D1） |
| 先手第 1 回合**不能攻击** | `packages/engine/engine/battle.py::_opening_turn` | `packages/engine/tests/test_s6a_rules.py::TestFirstTurnNoAttack.test_legal_actions_have_no_attack` | — | — | ✅ |
| 先手第 1 回合**不能使用 Supporter** | `packages/engine/engine/battle.py::_card_first_turn_ok` | `packages/engine/tests/test_s6a_rules.py::TestFirstTurnNoSupporter.test_legal_actions_hide_supporter` | — | — | ✅ |
| 例外许可：卡面「If you go first, you can use this attack / may use this card during your first turn」 | `packages/engine/engine/battle.py::_first_turn_attack_ok` | `packages/engine/tests/test_s6a_rules.py::TestFirstTurnBypass.test_bypass_attack_is_legal_on_opening_turn` | `use_any:first_turn` | 5 | ✅ |
| setup 上场的宝可梦当回合不能进化 | `packages/engine/engine/battle.py::_setup_openings` | `packages/engine/tests/test_s6a_rules.py::TestSetupPokemonCannotEvolve.test_first_player_cannot_evolve_on_turn_one` | — | — | ✅ |
| 无手牌上限 | `packages/engine/engine/battle.py::_safe_draw` | `packages/engine/tests/test_s6a_rules.py::TestNoHandLimit.test_no_limit_by_default` | — | — | ✅ |
| 每回合只能从手牌附着 1 张能量 | `packages/engine/engine/battle.py::_do_attach` | `packages/engine/tests/test_s6d_turn_limits.py::TestTurnLimits.test_one_energy_attach_per_turn` | — | — | ✅ |
| 每回合只能使用 1 张支援者 | `packages/engine/engine/battle.py::_trainer_legals` | `packages/engine/tests/test_s6d_turn_limits.py::TestTurnLimits.test_one_supporter_per_turn` | — | — | ✅ |
| 每只宝可梦最多附着 1 张宝可梦道具 | `packages/engine/engine/battle.py::_do_play` | `packages/engine/tests/test_s6d_turn_limits.py::TestTurnLimits.test_one_tool_per_pokemon` | — | — | ✅ |
| 场上同时只有 1 张场地卡（新的顶替旧的，旧的进拥有者弃牌堆） | `packages/engine/engine/battle.py::_trainer_play_stadium` | `packages/engine/tests/test_s6d_turn_limits.py::TestTurnLimits.test_only_one_stadium_in_play` | — | — | ✅ |
| 进化：解除全部特殊状态、不能当回合进化两次 | `packages/engine/engine/cards.py::evolve_to` | `packages/engine/tests/test_s6d_turn_limits.py::TestEvolutionClearsStatus.test_evolve_clears_all_special_conditions` | — | — | ✅ |
| 撤退：支付撤退费用、睡眠/麻痹不能撤退 | `packages/engine/engine/battle.py::_do_retreat` | `packages/engine/tests/test_v02.py::TestColorEnergy.test_retreat_colorless_priority` | — | — | ✅ |
| 出招后本回合结束 | `packages/engine/engine/battle.py::_do_attack` | `packages/engine/tests/test_flow.py::TestFlow.test_single_match_terminates` | — | — | ✅ |
| 伤害顺序：基础 → ×弱点 → −抗性 → 减伤 → 护盾 → 免疫 | `packages/engine/engine/battle.py::_damage_pipeline` | `packages/engine/tests/test_s6c_rules.py::TestDamagePipelineOrder.test_full_order_matches_official_sequence` | — | — | ✅ |
| 弱点 ×2 / 抗性 −30 | `packages/engine/engine/rules.py::compute_damage` | `packages/engine/tests/test_s6c_rules.py::TestDamagePipelineOrder.test_resistance_applies_after_weakness` | — | — | ✅ |
| 静态减伤（「受到的招式伤害 −N」，在弱抗之后） | `packages/engine/engine/battle.py::_static_mods` | `packages/engine/tests/test_s6c_rules.py::TestDamagePipelineOrder.test_full_order_matches_official_sequence` | `passive:dmg_reduce:` | 4 | ✅ |
| 限时护盾（`shield:*`：本回合/下回合内减伤或免疫） | `packages/engine/engine/rules.py::apply_shield` | `packages/engine/tests/test_s6c_rules.py::TestDamagePipelineOrder.test_full_order_matches_official_sequence` | `shield:` | 23 | ✅ |
| 特殊状态构成：中毒/灼伤可共存；睡眠/混乱/麻痹三选一 | `packages/engine/engine/rules.py::apply_status_set` | `packages/engine/tests/test_s6c_rules.py::TestStatusComposition.test_orient_replaces_orient` | — | — | ✅ |
| 一次效果施加多个状态（卡面「now A and B」） | `packages/engine/engine/battle.py::_set_status` | `packages/engine/tests/test_s6c_rules.py::TestStatusComposition.test_multi_status_via_card_both_apply` | `status_multi:` | 7 | ✅ |
| 宝可梦检查期：中毒 → 灼伤（先 20 伤害再抛币治愈）→ 睡眠 → 麻痹 | `packages/engine/engine/rules.py::status_checkup` | `packages/engine/tests/test_s6c_rules.py::TestBurnCheckup.test_heads_damages_then_cures` | — | — | ✅ |
| 混乱：攻击前抛币，反面自伤 30 且攻击失败（状态不解除） | `packages/engine/engine/battle.py::_do_attack` | `packages/engine/tests/test_settlement.py::TestSpecialConditions.test_confuse_heads_attack_and_keep` | — | — | ✅ |
| 击倒 → 从**自己**的奖赏区取奖赏（逐张取，有面朝上则由牌手选） | `packages/engine/engine/battle.py::_take_prizes`、`packages/engine/engine/zones.py::take_prizes` | `packages/engine/tests/test_s6b_prizes.py::TestPickPrizeChoice.test_multi_prize_take_is_one_by_one` | — | — | ✅ |
| 面朝下的奖赏卡不可查看（`view()` 只给计数） | `packages/engine/engine/zones.py::face_up_slots` | `packages/engine/tests/test_s6b_prizes.py::TestPrizeInfoRule.test_view_has_no_face_down_identities` | — | — | ✅ |
| 奖赏不足时取剩余即胜 | `packages/engine/engine/zones.py::take_prizes` | `packages/engine/tests/test_s6c_rules.py::TestPrizeShortage.test_last_prize_wins_even_if_ko_gives_more` | — | — | ✅ |
| 奖赏卡真发牌（从牌库取 6 张，牌库相应减少） | `packages/engine/engine/zones.py::deal_prizes` | `packages/engine/tests/test_zones_s3.py::TestPrizeObjects.test_deal_takes_six_from_deck` | `prize:` | 2 | ✅ |
| 回合开始抽不到牌（牌库空）判负 | `packages/engine/engine/battle.py::_safe_draw` | `packages/engine/tests/test_zones_s3.py::TestDeckOut.test_draw_from_empty_deck_loses` | — | — | ✅ |
| 战斗位空缺且后备无人判负 | `packages/engine/engine/battle.py::_finish` | `packages/engine/tests/test_flow.py::TestFlow.test_single_match_terminates` | — | — | ✅ |
| 效果免疫（团队范围 / 训练家来源 / 特殊能量） | `packages/engine/engine/battle.py::_effect_blocked` | `packages/engine/tests/test_effect_immune_s4.py::TestTeamImmunity.test_basic_teamrocket_is_protected` | — | — | ✅ |
| 事件总线 + 延迟队列（订阅 / 回合末唯一结算点 / 过期即抛错） | `packages/engine/engine/events.py::emit` | `packages/engine/tests/test_events_s2.py::TestDelayQueue.test_stale_item_raises_instead_of_silently_dropping` | `on_event:` | 8 | ✅ |
| 决策分解：引擎**从不**替玩家选择（挂起即冻结 / `then` 必须执行） | `packages/engine/engine/choice.py::PendingChoice` | `packages/engine/tests/test_choice_protocol.py::TestChoiceOpens.test_play_opens_choice_and_freezes_state` | — | — | ✅ |
| 复制对手招式（含抛币门 / 牌库顶来源；深度恒 1） | `packages/engine/engine/battle.py::_borrow_options` | `packages/engine/tests/test_borrow_s5.py::TestBorrowCoinGate.test_copied_attack_does_not_borrow_again` | `borrow_attack:` | 5 | ✅ |
| 训练家卡类别门禁（item / supporter / stadium / tool 的出场条件） | `packages/engine/engine/battle.py::_trainer_legals` | `packages/engine/tests/test_batch58_59_trainer_gate.py::TestPlayGate` | — | — | ✅ |
| 退化 devolve：把最高阶进化卡退回手牌、本体降级、当回合不能再进化 | `packages/engine/engine/cards.py::devolve`、`packages/engine/engine/battle.py::_apply_devolve` | `packages/engine/tests/test_s6d_devolve.py::TestDevolveBasics.test_one_step_returns_highest_card_to_hand` | `devolve:` | 2 | ✅ |
| 打出前置条件（含**多个**具名卡同时在场） | `packages/engine/engine/battle.py::_play_gate_ok`、`packages/engine/engine/battle.py::_gate_name_norm` | `packages/engine/tests/test_s6d_gate.py::TestAllInPlayGate.test_gate_blocks_when_one_missing` | — | — | ✅ |
| 静态被动族：队伍 +HP / 双属性 / 能力封锁（Rule Box · 自身击倒代价）/ 工具上限 | `packages/engine/engine/battle.py::_global_ability_blocked`、`packages/engine/engine/battle.py::_refresh_stadium_hp`、`packages/engine/engine/rules.py::attacker_types` | `packages/engine/tests/test_d4_static_passives.py::TestTeamHpPlus.test_team_hp_applies_to_all_and_does_not_stack` | — | — | ✅ |
| 「任意数量」伤害前决策：数量由玩家定、**定完才**算伤害（挂起 → 续跑） | `packages/engine/engine/battle.py::_b4_open`、`packages/engine/engine/battle.py::_attack_resume`、`packages/engine/engine/battle.py::_b4_spec` | `packages/engine/tests/test_b4_interaction.py::TestSelfCounters.test_choice_count_drives_damage` | `pre:b4:` | 5 | ✅ |
| 「任意数量」分配 / 多目标：目标不唯一时由玩家指（搬指示物 / 搬能量 / 贴能量 / 选 N 只洗回） | `packages/engine/engine/battle.py::_alloc_open`、`packages/engine/engine/battle.py::_alloc_apply` | `packages/engine/tests/test_b4_interaction.py::TestAlloc.test_shuffle_opp_bench_kept` | `choose:alloc:` | 5 | ✅ |
| 池内每张卡的每个训练家 tag 都必须有可执行落点（池内死卡守卫） | `packages/engine/tests/test_legals_coverage.py::_tag_prefixes` | `packages/engine/tests/test_legals_coverage.py::TestPoolTrainersArePlayable` | — | — | ✅ |

## 已知差异（`⚠️` 行的展开）

**D1 · mulligan 补偿是自动的，官方是「可以抽」**
官方（SM7 规则书「FULL DETAILS OF TAKING A MULLIGAN」）：对手**may draw 1
card** for each mulligan。引擎直接按次数抽满。

为什么暂时不改：这条差异要落在 `__init__` 的 setup 阶段（开局第一个选择），
需要把 `_begin_turn()` 推迟到选择闭合之后 —— 那是回合流转最关键的一段，
收益（极端局面下想拒抽）远小于风险。真要改，做法是把 `_setup_openings` 末尾
改成「挂起 `mulligan_draw` 选择 + 置 `_setup_pending_begin`，由 `_close_choice`
补做 `_begin_turn`」，并补 3 条真实对局用例（双方都 mulligan / 只有一方 / 开关关闭）。

> D2（多条件门槛）与 D3（退化 devolve）已在 **2026-11-08** 落地：前者支持
> `all_in_play:<名1>|<名2>|…` 的合取（Anthea & Concordia 进池），后者有完整
> 退化链（Archeops / Strange Timepiece 进池）。二者的守卫是上表对应行 +
> `tests/test_s6d_devolve.py` / `tests/test_s6d_gate.py`。

## 维护方式

1. 改引擎/加卡 → **同步改这张表**（新增行或改状态），否则 CI 红；
2. 本地跑 `python scripts/check_conformance.py` 自检；`--counts` 可打印各 tag
   前缀的现算覆盖卡数（填表用）；
3. 表里的数字**不要手算**：覆盖卡数由脚本从 `cards_pool_ext.json` 现算比对，
   写错立刻红 —— 这也是它跟「手写进度表」的根本区别。
