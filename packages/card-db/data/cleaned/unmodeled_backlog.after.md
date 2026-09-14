# PokéLab AI · 未建模动作清单（backlog）

- 快照：`after` · 生成时间：2026-09-14 10:16:56
- 数据源：`packages/card-db/data/db/pokelab.db`（K2 解析后端：rule（离线确定性基线））

## 0. 总量

| 指标 | 值 |
|---|---|
| 卡库总卡数 | 1267 |
| 已跑解析卡数 | 1119 |
| 解析条目（item） | 1481 |
| 解析动作条目（spec.actions） | 1481 |
| 已映射引擎 tag 条目 | 555 |
| **未建模动作条目** | **854** |
| 含未建模条目的卡数 | 693 |
| 全效果可映射卡数 | 426 |
| 至少 1 条引擎 tag 的卡数 | 499 |

## 1. 批次总览（按优先级）

| 批次 | 主题 | 条目 | 影响卡数 | 占比 | 建模动作 |
|---|---|---:|---:|---:|---|
| **B1** | 预设卡组关键卡 | 0 | 0 | 0.0% | 为每张卡补引擎原生 tag（训练家 tag / 特性 tag），并补引擎派发 |
| **B2** | tag 家族扩展 | 42 | 42 | 4.9% | agent.effects.to_engine_tags 放通映射 + engine.battle 新增派发分支 |
| **B3** | 解析召回补齐（英文卡面） | 151 | 151 | 17.7% | agent.effects._parse_rule_en 补正则（弃牌堆回收/弃自身能量/抛币前缀/附能/检索） |
| **B4** | 需新引擎机制（P1 登记） | 212 | 209 | 24.8% | 登记为 P1 批次；本轮仅保证「如实标注不支持」，不假装支持 |
| **B5** | 不建模登记 | 449 | 419 | 52.6% | 永久登记在差异透明清单（C5），不做实现 |

## 2. B1 · 预设卡组关键卡（最高优先，逐卡建模）

> 「高频」= 出现在 A1–A4 预设真实卡表（2026 Worlds meta）中；副本数越多越常出现。

| 卡名 | 副本 | 状态 | 影响卡组 | 原因 |
|---|---:|---|---|---|

## 3. B2–B5 · 分组明细

### B2 · tag 家族扩展（42 条 / 42 卡）

- 目标：把引擎已有能力或低成本可扩展的 tag 家族补齐映射与派发
- 动作：agent.effects.to_engine_tags 放通映射 + engine.battle 新增派发分支

| 子类 | 动作类型 | 条目 | 卡数 | 未建模原因 |
|---|---|---:|---:|---|
| evolve_skip | evolve | 30 | 30 | 招式/特性驱动的进化需卡级 tag |
| ability_draw | draw | 12 | 12 | 抽到指定手牌数需引擎新机制（未建模动作清单 B3/B4） |

<details><summary>样例 · evolve_skip</summary>

- **Eevee**：`Search your deck for a card that evolves from this Pokémon and put it onto this Pokémon to evolve it. Then, shuffle your deck.`
- **Noctowl**：`Once during your turn, when you play this Pokémon from your hand to evolve 1 of your Pokémon, if you have any Tera Pokémon in play, you may search your deck for up to 2 Trainer cards, reveal them, and`
- **Exeggcute**：`If you go first, you can use this attack during your first turn. Search your deck for a card that evolves from this Pokémon and put it onto this Pokémon to evolve it. Then, shuffle your deck.`

</details>

<details><summary>样例 · ability_draw</summary>

- **Blissey ex**：`You may draw cards until you have 6 cards in your hand.`
- **Decidueye**：`Draw cards until you have 7 cards in your hand.`
- **Quaquaval**：`You must put a card from your hand on the bottom of your deck in order to use this Ability. Once during your turn, you may draw cards until you have 5 cards in your hand.`

</details>

### B3 · 解析召回补齐（英文卡面）（151 条 / 151 卡）

- 目标：英文卡面句式未被规则解析器识别，导致整条效果落进 rule 兜底
- 动作：agent.effects._parse_rule_en 补正则（弃牌堆回收/弃自身能量/抛币前缀/附能/检索）

| 子类 | 动作类型 | 条目 | 卡数 | 未建模原因 |
|---|---|---:|---:|---|
| knockout_condition | knockout | 38 | 38 | 击倒效果需引擎新机制（未建模动作清单 B4） |
| attach | rule | 31 | 31 | 静态/未建模（英文） |
| discard_self | rule | 29 | 29 | 静态/未建模（英文） |
| discard_energy_self | rule | 13 | 13 | 静态/未建模（英文） |
| discard_energy_self | rule | 11 | 11 | 静态规则修改（弱点/抗性/伤害减免/限制） |
| recover_from_discard | rule | 8 | 8 | 静态/未建模（英文） |
| attach | rule | 6 | 6 | 静态规则修改（弱点/抗性/伤害减免/限制） |
| switch | rule | 4 | 4 | 静态/未建模（英文） |
| attach | rule | 2 | 2 | 全量回复（需引擎新机制 · B4） |
| draw | rule | 2 | 2 | 静态/未建模（英文） |
| search | rule | 2 | 2 | 静态/未建模（英文） |
| spread | rule | 1 | 1 | 自身放置伤害指示物（需引擎新机制 · 未建模动作清单 B4） |
| spread | rule | 1 | 1 | 静态规则修改（弱点/抗性/伤害减免/限制） |
| discard_self | rule | 1 | 1 | 静态规则修改（弱点/抗性/伤害减免/限制） |
| search | rule | 1 | 1 | 静态规则修改（弱点/抗性/伤害减免/限制） |
| switch | rule | 1 | 1 | 静态规则修改（弱点/抗性/伤害减免/限制） |

<details><summary>样例 · knockout_condition</summary>

- **Legacy Energy**：`As long as this card is attached to a Pokémon, it provides every type of Energy but provides only 1 Energy at a time.

If the Pokémon this card is attached to is Knocked Out by damage from an attack f`
- **Iron Leaves**：`If any of your Pokémon were Knocked Out by damage from an attack during your opponent’s last turn, this attack does 60 more damage.`
- **Dusclops**（彷徨夜灵）：`Once during your turn, you may put 5 damage counters on 1 of your opponent’s Pokémon. If you use this Ability, this Pokémon is Knocked Out.`

</details>

<details><summary>样例 · attach</summary>

- **Shiftry**：`Put an Energy attached to this Pokémon into your hand.`
- **Galvantula**：`If this Pokémon has any {L} Energy attached, this attack does 80 more damage.`
- **Archaludon**：`All of your Pokémon that have {M} Energy attached have no Retreat Cost.`

</details>

<details><summary>样例 · discard_self</summary>

- **Team Rocket's Energy**：`This card can only be attached to a Team Rocket’s Pokémon. If this card is attached to anything other than a Team Rocket’s Pokémon, discard this card.

As long as this card is attached to a Pokémon, i`
- **Great Tusk**：`Discard the top card of your opponent’s deck. If you played an Ancient Supporter card from your hand during this turn, discard 3 more cards in this way.`
- **Slowking**：`Discard the top card of your deck, and if that card is a Pokémon that doesn’t have a Rule Box, choose 1 of its attacks and use it as this attack. (Pokémon {ex}, Pokémon {V}, etc. have Rule Boxes.)`

</details>

<details><summary>样例 · discard_energy_self</summary>

- **Deerling**：`Discard a {G} Energy from this Pokémon.`
- **Charcadet**：`Discard an Energy from this Pokémon.`
- **Slaking ex**：`Discard an Energy from this Pokémon.`

</details>

### B4 · 需新引擎机制（P1 登记）（212 条 / 209 卡）

- 目标：需要条件表达式 / RNG 钩子 / 静态修正层等新机制，不属本轮
- 动作：登记为 P1 批次；本轮仅保证「如实标注不支持」，不假装支持

| 子类 | 动作类型 | 条目 | 卡数 | 未建模原因 |
|---|---|---:|---:|---|
| static_lock | rule | 86 | 83 | 静态规则修改（弱点/抗性/伤害减免/限制） |
| coin_flip | rule | 54 | 54 | 静态/未建模（英文） |
| scaling_damage | rule | 49 | 49 | 静态/未建模（英文） |
| coin_flip | rule | 15 | 15 | 静态规则修改（弱点/抗性/伤害减免/限制） |
| static_lock | rule | 4 | 4 | 静态/未建模（英文） |
| scaling_damage | rule | 3 | 3 | 静态规则修改（弱点/抗性/伤害减免/限制） |
| coin_flip | rule | 1 | 1 | 自身放置伤害指示物（需引擎新机制 · 未建模动作清单 B4） |

<details><summary>样例 · static_lock</summary>

- **Poltchageist**：`As long as this Pokémon is on your Bench, prevent all damage from and effects of attacks from your opponent’s Pokémon done to this Pokémon.`
- **Magcargo ex**：`As long as this Pokémon is on your Bench, prevent all damage done to this Pokémon by attacks (both yours and your opponent’s).`
- **Greninja ex**：`As long as this Pokémon is on your Bench, prevent all damage done to this Pokémon by attacks (both yours and your opponent’s).`

</details>

<details><summary>样例 · coin_flip</summary>

- **Eevee**：`Flip a coin. If heads, this attack does 20 more damage.`
- **Gastly**：`Flip a coin. If heads, discard an Energy from your opponent’s Active Pokémon.`
- **Applin**：`Flip a coin. If heads, this attack does 20 more damage.`

</details>

<details><summary>样例 · scaling_damage</summary>

- **Magcargo ex**：`Discard the top card of each player’s deck. This attack does 140 more damage for each Energy card discarded in this way.`
- **Roaring Moon**：`This attack does 10 more damage for each Ancient card in your discard pile.`
- **Rabsca**：`This attack does 30 more damage for each Energy attached to your opponent’s Active Pokémon.`

</details>

<details><summary>样例 · coin_flip</summary>

- **Dunsparce**（土龙弟弟）：`Flip a coin. If heads, during your opponent’s next turn, prevent all damage from and effects of attacks done to this Pokémon.`
- **Flittle**：`Flip a coin. If heads, during your opponent’s next turn, prevent all damage from and effects of attacks done to this Pokémon.`
- **Altaria**：`Flip a coin. If heads, during your opponent’s next turn, prevent all damage done to this Pokémon by attacks.`

</details>

### B5 · 不建模登记（449 条 / 419 卡）

- 目标：真·静态规则与元游戏效果（如卡名依赖、双方限制、场地级规则）
- 动作：永久登记在差异透明清单（C5），不做实现

| 子类 | 动作类型 | 条目 | 卡数 | 未建模原因 |
|---|---|---:|---:|---|
| unclassified | rule | 357 | 330 | 静态/未建模（英文） |
| unclassified | rule | 87 | 84 | 静态规则修改（弱点/抗性/伤害减免/限制） |
| unclassified | rule | 5 | 5 | 全量回复（需引擎新机制 · B4） |

<details><summary>样例 · unclassified</summary>

- **Boomerang Energy**：`As long as this card is attached to a Pokémon, it provides {C} Energy.

If this card is discarded by an effect of an attack used by the Pokémon this card is attached to, attach this card from your dis`
- **Neo Upper Energy**：`As long as this card is attached to a Pokémon, it provides {C} Energy.

If this card is attached to a Stage 2 Pokémon, this card provides every type of Energy but provides only 2 Energy at a time.`
- **Mist Energy**：`As long as this card is attached to a Pokémon, it provides {C} Energy.

Prevent all effects of attacks used by your opponent’s Pokémon done to the Pokémon this card is attached to. (Existing effects a`

</details>

<details><summary>样例 · unclassified</summary>

- **Hippowdon**：`This attack also does 40 damage to each Benched Pokémon that has any damage counters on it (both yours and your opponent’s). (Don’t apply Weakness and Resistance for Benched Pokémon.)`
- **Girafarig**：`This attack also does 10 damage to 1 of your Benched Pokémon. (Don’t apply Weakness and Resistance for Benched Pokémon.)`
- **Ting-Lu**：`If a Stadium is in play, this attack also does 30 damage to each of your opponent’s Benched Pokémon, and discard that Stadium. (Don’t apply Weakness and Resistance for Benched Pokémon.)`

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
