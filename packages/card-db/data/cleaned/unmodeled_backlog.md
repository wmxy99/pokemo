# PokéLab AI · 未建模动作清单（backlog）

- 快照：`current` · 生成时间：2026-09-22 21:45:11
- 数据源：`packages/card-db/data/db/pokelab.db`（K2 解析后端：rule（离线确定性基线））

## 0. 总量

| 指标 | 值 |
|---|---|
| 卡库总卡数 | 1267 |
| 已跑解析卡数 | 1119 |
| 解析条目（item） | 1481 |
| 解析动作条目（spec.actions） | 1481 |
| 已映射引擎 tag 条目 | 925 |
| **未建模动作条目** | **627** |
| 含未建模条目的卡数 | 550 |
| 全效果可映射卡数 | 569 |
| 至少 1 条引擎 tag 的卡数 | 765 |

## 1. 批次总览（按优先级）

| 批次 | 主题 | 条目 | 影响卡数 | 占比 | 建模动作 |
|---|---|---:|---:|---:|---|
| **B1** | 预设卡组关键卡 | 0 | 0 | 0.0% | 为每张卡补引擎原生 tag（训练家 tag / 特性 tag），并补引擎派发 |
| **B2** | tag 家族扩展 | 9 | 9 | 1.4% | agent.effects.to_engine_tags 放通映射 + engine.battle 新增派发分支 |
| **B3** | 解析召回补齐（英文卡面） | 103 | 103 | 16.4% | agent.effects._parse_rule_en 补正则（弃牌堆回收/弃自身能量/抛币前缀/附能/检索/附加伤害目标子句） |
| **B4** | 需新引擎机制（P1 登记） | 76 | 76 | 12.1% | 登记为 P1 批次；本轮仅保证「如实标注不支持」，不假装支持 |
| **B5** | 不建模登记 | 439 | 423 | 70.0% | 永久登记在差异透明清单（C5），不做实现 |

## 2. B1 · 预设卡组关键卡（最高优先，逐卡建模）

> 「高频」= 出现在 A1–A4 预设真实卡表（2026 Worlds meta）中；副本数越多越常出现。

| 卡名 | 副本 | 状态 | 影响卡组 | 原因 |
|---|---:|---|---|---|

## 3. B2–B5 · 分组明细

### B2 · tag 家族扩展（9 条 / 9 卡）

- 目标：把引擎已有能力或低成本可扩展的 tag 家族补齐映射与派发
- 动作：agent.effects.to_engine_tags 放通映射 + engine.battle 新增派发分支

| 子类 | 动作类型 | 条目 | 卡数 | 未建模原因 |
|---|---|---:|---:|---|
| trainer_search | search | 5 | 5 | 检索并**附着**（非入手）需引擎新机制（B4） |
| evolve_skip | evolve | 4 | 4 | 招式/特性驱动的进化需卡级 tag |

<details><summary>样例 · trainer_search</summary>

- **Farfetch'd**：`When you play this Pokémon from your hand onto your Bench during your turn, you may search your deck for a Pokémon Tool card and attach it to this Pokémon. Then, shuffle your deck.`
- **Yanmega ex**：`Once during your turn, when this Pokémon moves from your Bench to the Active Spot, you may search your deck for up to 3 Basic {G} Energy cards and attach them to this Pokémon. Then, shuffle your deck.`
- **Marnie's Grimmsnarl ex**：`When you play this Pokémon from your hand to evolve 1 of your Pokémon during your turn, you may search your deck for up to 5 Basic {D} Energy cards and attach them to your Marnie’s Pokémon in any way `

</details>

<details><summary>样例 · evolve_skip</summary>

- **Team Rocket's Nidorina**：`Choose up to 2 of your {D} Pokémon. For each of those Pokémon, search your deck for a card that evolves from that Pokémon and put it onto that Pokémon to evolve it. Then, shuffle your deck.`
- **Duosion**：`Search your deck for a card that evolves from 1 of your Pokémon and put it onto that Pokémon to evolve it. Then, shuffle your deck.`
- **Reuniclus**：`For each of your Benched Pokémon, search your deck for a card that evolves from that Pokémon and put it onto that Pokémon to evolve it. Then, shuffle your deck.`

</details>

### B3 · 解析召回补齐（英文卡面）（103 条 / 103 卡）

- 目标：英文卡面句式未被规则解析器识别，导致整条效果落进 rule 兜底
- 动作：agent.effects._parse_rule_en 补正则（弃牌堆回收/弃自身能量/抛币前缀/附能/检索/附加伤害目标子句）

| 子类 | 动作类型 | 条目 | 卡数 | 未建模原因 |
|---|---|---:|---:|---|
| attach | rule | 22 | 22 | 静态/未建模（英文） |
| knockout_condition | knockout | 13 | 13 | 击倒效果需引擎新机制（未建模动作清单 B4） |
| discard_self | rule | 10 | 10 | 静态/未建模（英文） |
| search | rule | 8 | 8 | 检视牌库顶：去向非「入手」（弃置/附着等），引擎 recon 仅支持取 1 入手（B4） |
| attach | rule | 8 | 8 | 条件伤害缩放：条件未识别（引擎无该条件） |
| attach | rule | 4 | 4 | 检视牌库顶：去向非「入手」（弃置/附着等），引擎 recon 仅支持取 1 入手（B4） |
| spread | rule | 4 | 4 | 放置伤害指示物：目标/条件未识别（the Attacking Pokémon） |
| switch | rule | 4 | 4 | 静态/未建模（英文） |
| spread | rule | 3 | 3 | 放置伤害指示物：目标/条件未识别（1 of your opponent’s Pokémon） |
| attach | rule | 2 | 2 | 全量回复（需引擎新机制 · B4） |
| spread | rule | 2 | 2 | 放置伤害指示物：目标/条件未识别（each Pokémon that has an Ability） |
| attach | rule | 2 | 2 | 放置伤害指示物：目标/条件未识别（that Pokémon） |
| draw | rule | 2 | 2 | 静态/未建模（英文） |
| search | rule | 2 | 2 | 静态/未建模（英文） |
| spread | rule | 1 | 1 | 放置伤害指示物：目标/条件未识别（1 of your opponent’s Pokémon for each Ba） |
| spread | rule | 1 | 1 | 放置伤害指示物：目标/条件未识别（the Defending Pokémon） |
| spread | rule | 1 | 1 | 放置伤害指示物：目标/条件未识别（this Pokémon） |
| spread | rule | 1 | 1 | 放置伤害指示物：任意分配含**战斗位**，引擎 spread_free 仅覆盖后备（B4） |
| discard_energy_self | rule | 1 | 1 | 条件伤害缩放：条件未识别（引擎无该条件） |
| discard_self | rule | 1 | 1 | 条件伤害缩放：计数源未识别（Misty's Pokémon that you disca） |
| discard_energy_self | rule | 1 | 1 | 静态/未建模（英文） |
| spread | rule | 1 | 1 | 放置伤害指示物：目标/条件未识别（that Pokémon） |
| spread | rule | 1 | 1 | 放置伤害指示物：目标/条件未识别（each of your opponent’s Basic Pokémon） |
| spread | rule | 1 | 1 | 放置伤害指示物：目标/条件未识别（that Pokémon instead of 1） |
| spread | rule | 1 | 1 | 放置伤害指示物：目标/条件未识别（each of them） |
| recover_from_discard | rule | 1 | 1 | 弃牌堆回收：回收数量未识别 |
| discard_self | rule | 1 | 1 | 条件伤害缩放：计数源未识别（Basic {W} Energy card that you） |
| attach | rule | 1 | 1 | 放置伤害指示物：目标/条件未识别（the Attacking Pokémon for each {M} Energ） |
| search | rule | 1 | 1 | 静态规则修改（弱点/抗性/伤害减免/限制） |
| recover_from_discard | rule | 1 | 1 | 弃牌堆回收：限定子集未识别（in any combination of Pokémon ） |
| recover_from_discard | rule | 1 | 1 | 弃牌堆回收：限定子集未识别（in any combination of {F} Poké） |

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

<details><summary>样例 · discard_self</summary>

- **Team Rocket's Energy**：`This card can only be attached to a Team Rocket’s Pokémon. If this card is attached to anything other than a Team Rocket’s Pokémon, discard this card.

As long as this card is attached to a Pokémon, i`
- **Team Rocket's Porygon**：`Discard a card from your hand. If you do, your opponent discards a card from their hand.`
- **Mega Absol ex**：`Your opponent reveals their hand, and you discard a card you find there.`

</details>

<details><summary>样例 · search</summary>

- **Morpeko**：`Once during your turn, you may look at the top card of your deck. You may discard that card.`
- **Tatsugiri ex**：`Look at the top 10 cards of your deck. You may put any number of Pokémon you find there onto your Bench. Shuffle the other cards back into your deck.`
- **Rockruff**：`Look at the top card of your deck. You may discard that card.`

</details>

### B4 · 需新引擎机制（P1 登记）（76 条 / 76 卡）

- 目标：需要条件表达式 / RNG 钩子 / 静态修正层 / 多目标选择等新机制，不属本轮
- 动作：登记为 P1 批次；本轮仅保证「如实标注不支持」，不假装支持

| 子类 | 动作类型 | 条目 | 卡数 | 未建模原因 |
|---|---|---:|---:|---|
| static_lock | rule | 27 | 27 | 静态规则修改（弱点/抗性/伤害减免/限制） |
| coin_flip | rule | 11 | 11 | 静态/未建模（英文） |
| static_lock | rule | 4 | 4 | 静态/未建模（英文） |
| scaling_damage | rule | 4 | 4 | 静态/未建模（英文） |
| extra_damage_target | damage | 3 | 3 | 附加伤害目标未建模（itself） |
| scaling_damage | rule | 3 | 3 | 条件伤害缩放：计数源未识别（damage counter on all of your ） |
| extra_damage_target | damage | 2 | 2 | 附加伤害目标未建模（1 of your opponent’s Pokémon for each Energy attached to thi） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（each Benched Pokémon that has any damage counters on it） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（each of your opponent’s Benched Pokémon） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（1 of your opponent’s Benched Pokémon {ex} or Benched Pokémon） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（itself for each damage counter on it） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（1 of your opponent’s Pokémon for each Energy card you discar） |
| scaling_damage | rule | 1 | 1 | 条件伤害缩放：计数源未识别（Ancient card in your discard p） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（1 of your opponent’s Benched Pokémon） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（each of your opponent’s Pokémon {ex} and Pokémon {V}） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（2 of your opponent’s Benched Pokémon） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（each of your opponent’s Pokémon {ex}） |
| scaling_damage | rule | 1 | 1 | 条件伤害缩放：计数源未识别（{L} Energy attached to all of ） |
| scaling_damage | rule | 1 | 1 | 条件伤害缩放：计数源未识别（Ethan's Adventure card in your） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（1 of your opponent’s Benched Pokémon {ex}） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（1 of your opponent’s Benched Pokémon for each damage counter） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（each of your opponent’s Benched Pokémon for each Prize card ） |
| scaling_damage | rule | 1 | 1 | 条件伤害缩放：计数源未识别（of your Evolution Pokémon in p） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（1 of your opponent’s Benched Pokémon that has any damage cou） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（1 of your opponent’s Pokémon for each {G} Energy attached to） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（1 of your opponent’s Pokémon） |
| scaling_damage | rule | 1 | 1 | 条件伤害缩放：计数源未识别（Prize card your opponent took ） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（that Pokémon） |
| coin_flip | rule | 1 | 1 | 放置伤害指示物：目标/条件未识别（1 of your opponent’s Pokémon） |

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

<details><summary>样例 · static_lock</summary>

- **Iron Thorns ex**：`As long as this Pokémon is in the Active Spot, Pokémon with a Rule Box in play (both yours and your opponent’s) have no Abilities, except for Future Pokémon. (Pokémon {ex}, Pokémon {V}, etc. have Rule`
- **Flutter Mane**：`As long as this Pokémon is in the Active Spot, your opponent’s Active Pokémon has no Abilities, except for Midnight Fluttering.`
- **Gastrodon**：`As long as this Pokémon is on your Bench, Benched Stage 2 Pokémon (both yours and your opponent’s) have no Abilities.`

</details>

<details><summary>样例 · scaling_damage</summary>

- **Breloom**：`Flip a coin until you get tails. This attack does 50 more damage for each heads.`
- **Tyrogue**：`Flip a coin until you get tails. This attack does 30 more damage for each heads.`
- **Mega Kangaskhan ex**：`Flip a coin until you get tails. This attack does 50 more damage for each heads.`

</details>

### B5 · 不建模登记（439 条 / 423 卡）

- 目标：真·静态规则与元游戏效果（如卡名依赖、双方限制、场地级规则）
- 动作：永久登记在差异透明清单（C5），不做实现

| 子类 | 动作类型 | 条目 | 卡数 | 未建模原因 |
|---|---|---:|---:|---|
| unclassified | rule | 303 | 287 | 静态/未建模（英文） |
| unclassified | rule | 78 | 78 | 条件伤害缩放：条件未识别（引擎无该条件） |
| unclassified | rule | 18 | 18 | 静态规则修改（弱点/抗性/伤害减免/限制） |
| unclassified | rule | 5 | 5 | 全量回复（需引擎新机制 · B4） |
| unclassified | rule | 5 | 5 | 条件伤害缩放：计数源未识别（Pokémon Tool attached to all o） |
| unclassified | rule | 4 | 4 | 放置伤害指示物：目标/条件未识别（the Attacking Pokémon） |
| unclassified | rule | 2 | 2 | 条件伤害缩放：计数源未识别（card in your opponent's hand） |
| unclassified | rule | 2 | 2 | 条件伤害缩放：计数源未识别（of your opponent's Pokémon {ex） |
| unclassified | rule | 2 | 2 | 条件伤害缩放：计数源未识别（Special Condition affecting yo） |
| unclassified | rule | 2 | 2 | 条件伤害缩放：计数源未识别（Supporter card that has “Team ） |
| unclassified | rule | 2 | 2 | 放置伤害指示物：目标/条件未识别（that Pokémon） |
| unclassified | rule | 1 | 1 | 条件伤害缩放：计数源未识别（of your Ancient Pokémon in pla） |
| unclassified | rule | 1 | 1 | 条件伤害缩放：计数源未识别（Pokémon in your discard pile t） |
| unclassified | rule | 1 | 1 | 条件伤害缩放：计数源未识别（Basic Energy card in your oppo） |
| unclassified | rule | 1 | 1 | 条件伤害缩放：计数源未识别（of your Team Rocket's Pokémon ） |
| unclassified | rule | 1 | 1 | 条件伤害缩放：计数源未识别（Item card in your opponent's d） |
| unclassified | rule | 1 | 1 | 条件伤害缩放：计数源未识别（Trainer card you find there） |
| unclassified | rule | 1 | 1 | 条件伤害缩放：计数源未识别（Basic {W} Energy card in your ） |
| unclassified | rule | 1 | 1 | 放置伤害指示物：目标/条件未识别（your opponent’s Active Pokémon for each ） |
| unclassified | rule | 1 | 1 | 条件伤害缩放：计数源未识别（of your Pokémon that has “Taur） |
| unclassified | rule | 1 | 1 | 条件伤害缩放：计数源未识别（Pokémon Tool attached to all P） |
| unclassified | rule | 1 | 1 | 条件伤害缩放：计数源未识别（Energy card you find there） |
| unclassified | rule | 1 | 1 | 放置伤害指示物：目标/条件未识别（1 of your opponent’s Benched Pokémon） |
| unclassified | rule | 1 | 1 | 条件伤害缩放：计数源未识别（of your Erika's Pokémon in pla） |
| unclassified | rule | 1 | 1 | 条件伤害缩放：计数源未识别（of your Basic Pokémon in play） |
| unclassified | rule | 1 | 1 | 放置伤害指示物：仅对手战斗位，需引擎新机制（B4） |
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
