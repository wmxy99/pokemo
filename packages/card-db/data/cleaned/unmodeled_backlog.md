# PokéLab AI · 未建模动作清单（backlog）

- 快照：`current` · 生成时间：2026-09-22 09:20:50
- 数据源：`packages/card-db/data/db/pokelab.db`（K2 解析后端：rule（离线确定性基线））

## 0. 总量

| 指标 | 值 |
|---|---|
| 卡库总卡数 | 1267 |
| 已跑解析卡数 | 1119 |
| 解析条目（item） | 1481 |
| 解析动作条目（spec.actions） | 1481 |
| 已映射引擎 tag 条目 | 891 |
| **未建模动作条目** | **675** |
| 含未建模条目的卡数 | 572 |
| 全效果可映射卡数 | 547 |
| 至少 1 条引擎 tag 的卡数 | 744 |

## 1. 批次总览（按优先级）

| 批次 | 主题 | 条目 | 影响卡数 | 占比 | 建模动作 |
|---|---|---:|---:|---:|---|
| **B1** | 预设卡组关键卡 | 0 | 0 | 0.0% | 为每张卡补引擎原生 tag（训练家 tag / 特性 tag），并补引擎派发 |
| **B2** | tag 家族扩展 | 25 | 25 | 3.7% | agent.effects.to_engine_tags 放通映射 + engine.battle 新增派发分支 |
| **B3** | 解析召回补齐（英文卡面） | 93 | 93 | 13.8% | agent.effects._parse_rule_en 补正则（弃牌堆回收/弃自身能量/抛币前缀/附能/检索/附加伤害目标子句） |
| **B4** | 需新引擎机制（P1 登记） | 119 | 119 | 17.6% | 登记为 P1 批次；本轮仅保证「如实标注不支持」，不假装支持 |
| **B5** | 不建模登记 | 438 | 422 | 64.9% | 永久登记在差异透明清单（C5），不做实现 |

## 2. B1 · 预设卡组关键卡（最高优先，逐卡建模）

> 「高频」= 出现在 A1–A4 预设真实卡表（2026 Worlds meta）中；副本数越多越常出现。

| 卡名 | 副本 | 状态 | 影响卡组 | 原因 |
|---|---:|---|---|---|

## 3. B2–B5 · 分组明细

### B2 · tag 家族扩展（25 条 / 25 卡）

- 目标：把引擎已有能力或低成本可扩展的 tag 家族补齐映射与派发
- 动作：agent.effects.to_engine_tags 放通映射 + engine.battle 新增派发分支

| 子类 | 动作类型 | 条目 | 卡数 | 未建模原因 |
|---|---|---:|---:|---|
| evolve_skip | evolve | 25 | 25 | 招式/特性驱动的进化需卡级 tag |

<details><summary>样例 · evolve_skip</summary>

- **Noctowl**：`Once during your turn, when you play this Pokémon from your hand to evolve 1 of your Pokémon, if you have any Tera Pokémon in play, you may search your deck for up to 2 Trainer cards, reveal them, and`
- **Archaludon ex**：`When you play this Pokémon from your hand to evolve 1 of your Pokémon during your turn, you may attach up to 2 Basic {M} Energy cards from your discard pile to your {M} Pokémon in any way you like.`
- **Whimsicott**：`When you play this Pokémon from your hand to evolve 1 of your Pokémon during your turn, you may heal all damage from your Active {G} Pokémon. If you healed any damage in this way, discard all Energy f`

</details>

### B3 · 解析召回补齐（英文卡面）（93 条 / 93 卡）

- 目标：英文卡面句式未被规则解析器识别，导致整条效果落进 rule 兜底
- 动作：agent.effects._parse_rule_en 补正则（弃牌堆回收/弃自身能量/抛币前缀/附能/检索/附加伤害目标子句）

| 子类 | 动作类型 | 条目 | 卡数 | 未建模原因 |
|---|---|---:|---:|---|
| discard_self | rule | 29 | 29 | 静态/未建模（英文） |
| attach | rule | 22 | 22 | 静态/未建模（英文） |
| knockout_condition | knockout | 13 | 13 | 击倒效果需引擎新机制（未建模动作清单 B4） |
| attach | rule | 10 | 10 | 条件伤害缩放：条件未识别（引擎无该条件） |
| switch | rule | 4 | 4 | 静态/未建模（英文） |
| attach | rule | 2 | 2 | 全量回复（需引擎新机制 · B4） |
| draw | rule | 2 | 2 | 静态/未建模（英文） |
| search | rule | 2 | 2 | 静态/未建模（英文） |
| spread | rule | 1 | 1 | 自身放置伤害指示物（需引擎新机制 · 未建模动作清单 B4） |
| discard_energy_self | rule | 1 | 1 | 条件伤害缩放：条件未识别（引擎无该条件） |
| discard_self | rule | 1 | 1 | 条件伤害缩放：计数源未识别（Misty's Pokémon that you disca） |
| discard_energy_self | rule | 1 | 1 | 静态/未建模（英文） |
| recover_from_discard | rule | 1 | 1 | 弃牌堆回收：回收数量未识别 |
| discard_self | rule | 1 | 1 | 条件伤害缩放：计数源未识别（Basic {W} Energy card that you） |
| search | rule | 1 | 1 | 静态规则修改（弱点/抗性/伤害减免/限制） |
| recover_from_discard | rule | 1 | 1 | 弃牌堆回收：限定子集未识别（in any combination of Pokémon ） |
| recover_from_discard | rule | 1 | 1 | 弃牌堆回收：限定子集未识别（in any combination of {F} Poké） |

<details><summary>样例 · discard_self</summary>

- **Team Rocket's Energy**：`This card can only be attached to a Team Rocket’s Pokémon. If this card is attached to anything other than a Team Rocket’s Pokémon, discard this card.

As long as this card is attached to a Pokémon, i`
- **Great Tusk**：`Discard the top card of your opponent’s deck. If you played an Ancient Supporter card from your hand during this turn, discard 3 more cards in this way.`
- **Slowking**：`Discard the top card of your deck, and if that card is a Pokémon that doesn’t have a Rule Box, choose 1 of its attacks and use it as this attack. (Pokémon {ex}, Pokémon {V}, etc. have Rule Boxes.)`

</details>

<details><summary>样例 · attach</summary>

- **Shiftry**：`Put an Energy attached to this Pokémon into your hand.`
- **Okidogi**：`If this Pokémon has any {D} Energy attached, it gets +100 HP, and the attacks it uses do 100 more damage to your opponent’s Active Pokémon (before applying Weakness and Resistance).`
- **Archaludon**：`All of your Pokémon that have {M} Energy attached have no Retreat Cost.`

</details>

<details><summary>样例 · knockout_condition</summary>

- **Togekiss**：`When your opponent’s Active Pokémon is Knocked Out, flip a coin. If heads, take 1 more Prize card. The effect of Wonder Kiss doesn’t stack.`
- **Annihilape**：`Both Active Pokémon are Knocked Out.`
- **Maractus**：`If this Pokémon is in the Active Spot and is Knocked Out by damage from an attack from your opponent’s Pokémon, put 6 damage counters on the Attacking Pokémon.`

</details>

<details><summary>样例 · attach</summary>

- **Galvantula**：`If this Pokémon has any {L} Energy attached, this attack does 80 more damage.`
- **Electivire ex**：`If this Pokémon has at least 2 extra Energy attached (in addition to this attack’s cost), this attack does 100 more damage.`
- **Team Rocket's Articuno**：`If this Pokémon has any Team Rocket’s Energy attached, this attack does 60 more damage.`

</details>

### B4 · 需新引擎机制（P1 登记）（119 条 / 119 卡）

- 目标：需要条件表达式 / RNG 钩子 / 静态修正层 / 多目标选择等新机制，不属本轮
- 动作：登记为 P1 批次；本轮仅保证「如实标注不支持」，不假装支持

| 子类 | 动作类型 | 条目 | 卡数 | 未建模原因 |
|---|---|---:|---:|---|
| static_lock | rule | 27 | 27 | 静态规则修改（弱点/抗性/伤害减免/限制） |
| coin_flip | rule | 13 | 13 | 静态/未建模（英文） |
| extra_damage_target | damage | 5 | 5 | 附加伤害目标未建模（opp_bench_one：does 30 damage to 1 of your opponent’s Benched Pokémon） |
| static_lock | rule | 4 | 4 | 静态/未建模（英文） |
| extra_damage_target | damage | 4 | 4 | 附加伤害目标未建模（opp_bench_one：does 50 damage to 1 of your opponent’s Benched Pokémon） |
| scaling_damage | rule | 4 | 4 | 静态/未建模（英文） |
| extra_damage_target | damage | 3 | 3 | 附加伤害目标未建模（opp_n：does 50 damage to 2 of your opponent’s Pokémon） |
| scaling_damage | rule | 3 | 3 | 条件伤害缩放：计数源未识别（damage counter on all of your ） |
| extra_damage_target | damage | 2 | 2 | 附加伤害目标未建模（opp_bench_one：does 20 damage to 1 of your opponent’s Benched Pokémon） |
| extra_damage_target | damage | 2 | 2 | 附加伤害目标未建模（opp_n：does 120 damage to 1 of your opponent’s Pokémon） |
| extra_damage_target | damage | 2 | 2 | 附加伤害目标未建模（opp_n：does 60 damage to each of 2 of your opponent’s Pokémon） |
| extra_damage_target | damage | 2 | 2 | 附加伤害目标未建模（own_bench_all：does 30 damage to each of your Benched Pokémon） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（both_bench_all：does 40 damage to each Benched Pokémon that has any damage c） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（own_bench_one：does 10 damage to 1 of your Benched Pokémon） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（opp_n：does 120 damage to 2 of your opponent’s Pokémon） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（opp_bench_all_conditional：does 30 damage to each of your opponent’s Benched Pokémon, a） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（opp_bench_one：does 60 damage to 1 of your opponent’s Benched Pokémon {ex} ） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（self_conditional：does 10 damage to itself for each damage counter on it） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（opp_n_conditional：does 60 damage to 1 of your opponent’s Pokémon for each Ener） |
| scaling_damage | rule | 1 | 1 | 条件伤害缩放：计数源未识别（Ancient card in your discard p） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（opp_bench_one_conditional：does 120 damage to 1 of your opponent’s Benched Pokémon） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（self_conditional：does 30 damage to itself） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（opp_n：does 100 damage to 1 of your opponent’s Pokémon） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（opp_n：does 110 damage to 3 of your opponent’s Pokémon） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（opp_n：does 180 damage to 1 of your opponent’s Pokémon） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（opp_n_conditional：does 30 damage to 1 of your opponent’s Pokémon for each Ener） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（opp_all：does 100 damage to each of your opponent’s Pokémon {ex} and ） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（unknown_conditional：does 120 damage to 2 of your opponent’s Benched Pokémon） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（unknown：does 130 damage to 2 of your opponent’s Benched Pokémon） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（opp_all：does 60 damage to each of your opponent’s Pokémon {ex}） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（opp_bench_one：does 90 damage to 1 of your opponent’s Benched Pokémon） |
| scaling_damage | rule | 1 | 1 | 条件伤害缩放：计数源未识别（{L} Energy attached to all of ） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（own_bench_all：does 20 damage to each of your Benched Pokémon） |
| scaling_damage | rule | 1 | 1 | 条件伤害缩放：计数源未识别（Ethan's Adventure card in your） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（unknown：does 30 damage to 2 of your opponent’s Benched Pokémon） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（self_conditional：does 50 damage to itself） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（own_bench_one：does 40 damage to 1 of your Benched Pokémon） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（opp_bench_one：does 210 damage to 1 of your opponent’s Benched Pokémon {ex}） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（opp_bench_one：does 120 damage to 1 of your opponent’s Benched Pokémon） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（opp_all：does 30 damage to each of your opponent’s Pokémon） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（opp_bench_one_conditional：does 20 damage to 1 of your opponent’s Benched Pokémon for e） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（self_conditional：does 90 damage to itself） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（opp_bench_all_conditional：does 10 damage to each of your opponent’s Benched Pokémon fo） |
| scaling_damage | rule | 1 | 1 | 条件伤害缩放：计数源未识别（of your Evolution Pokémon in p） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（opp_bench_one：does 60 damage to 1 of your opponent’s Benched Pokémon that ） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（own_bench_all：does 10 damage to each of your Benched Pokémon） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（opp_n：does 70 damage to 1 of your opponent’s Pokémon） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（opp_n_conditional：does 20 damage to 1 of your opponent’s Pokémon for each Ener） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（opp_n：does 40 damage to 1 of your opponent’s Pokémon） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（opp_all：does 20 damage to each of your opponent’s Pokémon） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（both_bench_all：does 20 damage to each Benched Pokémon ） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（opp_n_conditional：does 20 damage to 1 of your opponent’s Pokémon for each {G} ） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（opp_bench_one：does 70 damage to 1 of your opponent’s Benched Pokémon） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（opp_n_conditional：does 140 damage to 1 of your opponent’s Pokémon） |
| scaling_damage | rule | 1 | 1 | 条件伤害缩放：计数源未识别（Prize card your opponent took ） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（opp_n：does 280 damage to 1 of your opponent’s Pokémon） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（opp_n：does 50 damage to 1 of your opponent’s Pokémon） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（opp_n：does 90 damage to 1 of your opponent’s Pokémon） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（dynamic_conditional：does 150 damage to that Pokémon） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（opp_n：does 220 damage to 1 of your opponent’s Pokémon） |

<details><summary>样例 · static_lock</summary>

- **Rabsca**：`Prevent all damage from and effects of attacks from your opponent’s Pokémon done to your Benched Pokémon.`
- **Farigiraf ex**：`Prevent all damage done to this Pokémon by attacks from your opponent’s Basic Pokémon {ex}.`
- **Cornerstone Mask Ogerpon ex**：`Prevent all damage from attacks done to this Pokémon by your opponent’s Pokémon that have an Ability.`

</details>

<details><summary>样例 · coin_flip</summary>

- **Sylveon**：`Flip a coin. If heads, choose 1 of your opponent’s Benched Pokémon. Shuffle that Pokémon and all attached cards into their deck.`
- **Alolan Exeggutor ex**：`Flip a coin. If heads, Knock Out your opponent’s Active Basic Pokémon. If tails, Knock Out 1 of your opponent’s Benched Basic Pokémon.`
- **Ethan's Sudowoodo**：`Flip a coin. If heads, choose 1 of your opponent’s Active Pokémon’s attacks and use it as this attack.`

</details>

<details><summary>样例 · extra_damage_target</summary>

- **Farigiraf ex**：`This attack also does 30 damage to 1 of your opponent’s Benched Pokémon. (Don’t apply Weakness and Resistance for Benched Pokémon.)`
- **Glaceon ex**：`This attack also does 30 damage to 1 of your opponent’s Benched Pokémon. (Don’t apply Weakness and Resistance for Benched Pokémon.)`
- **Hop’s Zacian ex**：`This attack also does 30 damage to 1 of your opponent’s Benched Pokémon. (Don’t apply Weakness and Resistance for Benched Pokémon.)`

</details>

<details><summary>样例 · static_lock</summary>

- **Iron Thorns ex**：`As long as this Pokémon is in the Active Spot, Pokémon with a Rule Box in play (both yours and your opponent’s) have no Abilities, except for Future Pokémon. (Pokémon {ex}, Pokémon {V}, etc. have Rule`
- **Flutter Mane**：`As long as this Pokémon is in the Active Spot, your opponent’s Active Pokémon has no Abilities, except for Midnight Fluttering.`
- **Gastrodon**：`As long as this Pokémon is on your Bench, Benched Stage 2 Pokémon (both yours and your opponent’s) have no Abilities.`

</details>

### B5 · 不建模登记（438 条 / 422 卡）

- 目标：真·静态规则与元游戏效果（如卡名依赖、双方限制、场地级规则）
- 动作：永久登记在差异透明清单（C5），不做实现

| 子类 | 动作类型 | 条目 | 卡数 | 未建模原因 |
|---|---|---:|---:|---|
| unclassified | rule | 311 | 295 | 静态/未建模（英文） |
| unclassified | rule | 78 | 78 | 条件伤害缩放：条件未识别（引擎无该条件） |
| unclassified | rule | 18 | 18 | 静态规则修改（弱点/抗性/伤害减免/限制） |
| unclassified | rule | 5 | 5 | 全量回复（需引擎新机制 · B4） |
| unclassified | rule | 5 | 5 | 条件伤害缩放：计数源未识别（Pokémon Tool attached to all o） |
| unclassified | rule | 2 | 2 | 条件伤害缩放：计数源未识别（card in your opponent's hand） |
| unclassified | rule | 2 | 2 | 条件伤害缩放：计数源未识别（of your opponent's Pokémon {ex） |
| unclassified | rule | 2 | 2 | 条件伤害缩放：计数源未识别（Special Condition affecting yo） |
| unclassified | rule | 2 | 2 | 条件伤害缩放：计数源未识别（Supporter card that has “Team ） |
| unclassified | rule | 1 | 1 | 条件伤害缩放：计数源未识别（of your Ancient Pokémon in pla） |
| unclassified | rule | 1 | 1 | 条件伤害缩放：计数源未识别（Pokémon in your discard pile t） |
| unclassified | rule | 1 | 1 | 条件伤害缩放：计数源未识别（Basic Energy card in your oppo） |
| unclassified | rule | 1 | 1 | 条件伤害缩放：计数源未识别（of your Team Rocket's Pokémon ） |
| unclassified | rule | 1 | 1 | 条件伤害缩放：计数源未识别（Item card in your opponent's d） |
| unclassified | rule | 1 | 1 | 条件伤害缩放：计数源未识别（Trainer card you find there） |
| unclassified | rule | 1 | 1 | 条件伤害缩放：计数源未识别（Basic {W} Energy card in your ） |
| unclassified | rule | 1 | 1 | 条件伤害缩放：计数源未识别（of your Pokémon that has “Taur） |
| unclassified | rule | 1 | 1 | 条件伤害缩放：计数源未识别（Pokémon Tool attached to all P） |
| unclassified | rule | 1 | 1 | 条件伤害缩放：计数源未识别（Energy card you find there） |
| unclassified | rule | 1 | 1 | 条件伤害缩放：计数源未识别（of your Erika's Pokémon in pla） |
| unclassified | rule | 1 | 1 | 条件伤害缩放：计数源未识别（of your Basic Pokémon in play） |
| unclassified | rule | 1 | 1 | 条件伤害缩放：计数源未识别（card you revealed in this way） |

<details><summary>样例 · unclassified</summary>

- **Boomerang Energy**：`As long as this card is attached to a Pokémon, it provides {C} Energy.

If this card is discarded by an effect of an attack used by the Pokémon this card is attached to, attach this card from your dis`
- **Neo Upper Energy**：`As long as this card is attached to a Pokémon, it provides {C} Energy.

If this card is attached to a Stage 2 Pokémon, this card provides every type of Energy but provides only 2 Energy at a time.`
- **Mist Energy**：`As long as this card is attached to a Pokémon, it provides {C} Energy.

Prevent all effects of attacks used by your opponent’s Pokémon done to the Pokémon this card is attached to. (Existing effects a`

</details>

<details><summary>样例 · unclassified</summary>

- **Team Rocket's Kangaskhan ex**：`If you played a Supporter card that has “Team Rocket” in its name from your hand during this turn, this attack does 100 more damage.`
- **Chi-Yu**：`If a Stadium is in play, this attack does 60 more damage. Then, discard that Stadium.`
- **Enamorus**：`If any of your Pokémon in play are the same type as any of your opponent’s Pokémon in play, this attack does 120 more damage.`

</details>

<details><summary>样例 · unclassified</summary>

- **Iron Crown ex**：`This attack does 50 damage to 2 of your opponent’s Pokémon. This attack’s damage isn’t affected by Weakness or Resistance, or by any effects on those Pokémon.`
- **Cornerstone Mask Ogerpon ex**：`This attack’s damage isn’t affected by Weakness or Resistance, or by any effects on your opponent’s Active Pokémon.`
- **Vibrava**：`During your next turn, the Defending Pokémon takes 50 more damage from attacks (after applying Weakness and Resistance).`

</details>

<details><summary>样例 · unclassified</summary>

- **Whimsicott**：`When you play this Pokémon from your hand to evolve 1 of your Pokémon during your turn, you may heal all damage from your Active {G} Pokémon. If you healed any damage in this way, discard all Energy f`
- **Miltank**：`Flip 2 coins. If both of them are heads, heal all damage from 1 of your Pokémon.`
- **Whimsicott**：`Heal all damage from 1 of your Benched Pokémon.`

</details>

## 4. 口径说明

- 「未建模条目」= effects_json.parse.unsupported 的条目数（一条效果可含多条动作）。
- B1 的「高频」锚点 = 出现在 A1–A4 预设真实卡表（2026 Worlds meta）中的卡；副本数越多 = 在真实对战中越常出现。
- B3/B4/B5 的 rule 桶归属由英文卡面关键词启发式判定（确定性，可复跑），故为**排期估算**而非精确实现清单。
- 诚实标注（C5）：未被映射的条目一律保留在 unsupported 中，不静默降级。
