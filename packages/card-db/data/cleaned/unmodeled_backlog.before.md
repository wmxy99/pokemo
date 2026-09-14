# PokéLab AI · 未建模动作清单（backlog）

- 快照：`before` · 生成时间：2026-09-14 09:59:18
- 数据源：`packages/card-db/data/db/pokelab.db`（K2 解析后端：rule（离线确定性基线））

## 0. 总量

| 指标 | 值 |
|---|---|
| 卡库总卡数 | 1267 |
| 已跑解析卡数 | 1119 |
| 解析条目（item） | 1481 |
| 解析动作条目（spec.actions） | 1481 |
| 已映射引擎 tag 条目 | 241 |
| **未建模动作条目** | **1144** |
| 含未建模条目的卡数 | 883 |
| 全效果可映射卡数 | 236 |
| 至少 1 条引擎 tag 的卡数 | 231 |

## 1. 批次总览（按优先级）

| 批次 | 主题 | 条目 | 影响卡数 | 占比 | 建模动作 |
|---|---|---:|---:|---:|---|
| **B1** | 预设卡组关键卡 | 10 | 10 | 0.9% | 为每张卡补引擎原生 tag（训练家 tag / 特性 tag），并补引擎派发 |
| **B2** | tag 家族扩展 | 268 | 267 | 23.4% | agent.effects.to_engine_tags 放通映射 + engine.battle 新增派发分支 |
| **B3** | 解析召回补齐（英文卡面） | 214 | 214 | 18.7% | agent.effects._parse_rule_en 补正则（弃牌堆回收/弃自身能量/抛币前缀/附能/检索） |
| **B4** | 需新引擎机制（P1 登记） | 215 | 212 | 18.8% | 登记为 P1 批次；本轮仅保证「如实标注不支持」，不假装支持 |
| **B5** | 不建模登记 | 447 | 403 | 39.1% | 永久登记在差异透明清单（C5），不做实现 |

## 2. B1 · 预设卡组关键卡（最高优先，逐卡建模）

> 「高频」= 出现在 A1–A4 预设真实卡表（2026 Worlds meta）中；副本数越多越常出现。

| 卡名 | 副本 | 状态 | 影响卡组 | 原因 |
|---|---:|---|---|---|
| Earthen Vessel | 7 | replaced | A1×2、A2×2、A3×1、A4×2 | 弃 1 张 → 检索 2 能量（需目标选择）→ 替换占位 |
| Munkidori | 6 | approximated | A1×2、A3×2、A4×2 | 特性「肾上腺素脑」（移动伤害指示物）引擎 v1 未实现 → 灰名单，保留招式 |
| Fezandipiti ex | 5 | approximated | A1×1、A2×1、A3×1、A4×2 | 特性「逆转局势」（被击倒时抽 3）引擎未实现 → 灰名单，保留招式 |
| Ogerpon ex | 4 | approximated | A4×4 | 特性「碧绿之舞」（附草能量）引擎未实现 → 灰名单，保留招式 |
| Clefairy ex | 3 | approximated | A4×3 | 特性「妖精领域」（改对手弱点）引擎未实现 → 灰名单，保留招式 |
| Night Stretcher | 3 | replaced | A1×1、A2×1、A3×1 | 弃牌堆回收（需目标选择）→ 替换占位 |
| Switch | 3 | replaced | A4×3 | 自换位（引擎撤退动作已覆盖，卡面动作未实现）→ 替换占位 |
| Blaziken ex | 2 | approximated | A2×2 | 特性「火焰之舞」（弃牌堆附能加速）引擎未实现 → 灰名单，保留招式 |
| Crispin | 2 | replaced | A2×2 | 检索 2 张不同类型能量（需目标选择）→ 替换占位 |
| Rare Candy | 2 | replaced | A1×2 | 跳阶进化：引擎 v1 无「基础直进化二阶」动作 → 替换占位 |

## 3. B2–B5 · 分组明细

### B2 · tag 家族扩展（268 条 / 267 卡）

- 目标：把引擎已有能力或低成本可扩展的 tag 家族补齐映射与派发
- 动作：agent.effects.to_engine_tags 放通映射 + engine.battle 新增派发分支

| 子类 | 动作类型 | 条目 | 卡数 | 未建模原因 |
|---|---|---:|---:|---|
| trainer_search | search | 133 | 133 | 训练家检索仅支持预设 tag |
| evolve_skip | evolve | 34 | 34 | 引擎未建模该动作类型 |
| attack_switch | switch | 33 | 33 | 引擎未建模该动作类型 |
| attack_discard | discard | 30 | 29 | 引擎未建模该动作类型 |
| spread_per | spread | 20 | 20 | 仅支持任意分配的自由式（spread_free:N） |
| ability_draw | draw | 13 | 13 | 特性抽牌未建 tag（可用 recon:N 近似） |
| attack_attach | attach | 3 | 3 | 引擎未建模该动作类型 |
| attack_move_damage | move_damage | 2 | 2 | 引擎未建模该动作类型 |

<details><summary>样例 · trainer_search</summary>

- **Telepath Psychic Energy**：`As long as this card is attached to a Pokémon, it provides {P} Energy.
When you attach this card from your hand to a {P} Pokémon, search your deck for up to 2 Basic {P} Pokémon and put them onto your `
- **Scrafty**：`Search your deck for a number of cards up to the number of your Benched Pokémon and put them into your hand. Then, shuffle your deck.`
- **Froakie**：`Search your deck for up to 2 Froakie and put them onto your Bench. Then, shuffle your deck.`

</details>

<details><summary>样例 · evolve_skip</summary>

- **Eevee**：`Search your deck for a card that evolves from this Pokémon and put it onto this Pokémon to evolve it. Then, shuffle your deck.`
- **Noctowl**：`Once during your turn, when you play this Pokémon from your hand to evolve 1 of your Pokémon, if you have any Tera Pokémon in play, you may search your deck for up to 2 Trainer cards, reveal them, and`
- **Exeggcute**：`If you go first, you can use this attack during your first turn. Search your deck for a card that evolves from this Pokémon and put it onto this Pokémon to evolve it. Then, shuffle your deck.`

</details>

<details><summary>样例 · attack_switch</summary>

- **Hippopotas**：`Switch out your opponent’s Active Pokémon to the Bench. (Your opponent chooses the new Active Pokémon.)`
- **Croconaw**：`Switch this Pokémon with 1 of your Benched Pokémon.`
- **Iron Leaves ex**：`When you play this Pokémon from your hand onto your Bench during your turn, you may switch it with your Active Pokémon. If you do, you may move any amount of Energy from your other Pokémon to this Pok`

</details>

<details><summary>样例 · attack_discard</summary>

- **Greninja ex**：`Discard 2 Energy from this Pokémon. This attack does 120 damage to 2 of your opponent’s Pokémon. (Don’t apply Weakness and Resistance for Benched Pokémon.)`
- **Raging Bolt ex**：`Discard your hand and draw 6 cards.`
- **Pikachu ex**：`Discard 3 Energy from this Pokémon.`

</details>

### B3 · 解析召回补齐（英文卡面）（214 条 / 214 卡）

- 目标：英文卡面句式未被规则解析器识别，导致整条效果落进 rule 兜底
- 动作：agent.effects._parse_rule_en 补正则（弃牌堆回收/弃自身能量/抛币前缀/附能/检索）

| 子类 | 动作类型 | 条目 | 卡数 | 未建模原因 |
|---|---|---:|---:|---|
| attach | rule | 64 | 64 | 引擎未建模该动作类型 |
| discard_energy_self | rule | 42 | 42 | 引擎未建模该动作类型 |
| knockout_condition | knockout | 38 | 38 | 引擎未建模该动作类型 |
| discard_self | rule | 30 | 30 | 引擎未建模该动作类型 |
| draw | rule | 12 | 12 | 引擎未建模该动作类型 |
| recover_from_discard | rule | 10 | 10 | 引擎未建模该动作类型 |
| switch | rule | 9 | 9 | 引擎未建模该动作类型 |
| search | rule | 6 | 6 | 引擎未建模该动作类型 |
| move_damage | rule | 2 | 2 | 引擎未建模该动作类型 |
| spread | rule | 1 | 1 | 引擎未建模该动作类型 |

<details><summary>样例 · attach</summary>

- **Leafeon**：`Attach a Basic {G} Energy card from your hand to 1 of your Benched Pokémon. If you do, heal all damage from that Pokémon.`
- **Shiftry**：`Put an Energy attached to this Pokémon into your hand.`
- **Wellspring Mask Ogerpon ex**：`You may shuffle 3 Energy attached to this Pokémon into your deck. If you do, this attack also does 120 damage to 1 of your opponent’s Benched Pokémon. (Don’t apply Weakness and Resistance for Benched `

</details>

<details><summary>样例 · discard_energy_self</summary>

- **Pinsir**：`Discard all Energy from this Pokémon. At the end of your opponent’s next turn, the Defending Pokémon will be Knocked Out.`
- **Greninja ex**：`Discard 2 Energy from this Pokémon. This attack does 120 damage to 2 of your opponent’s Pokémon. (Don’t apply Weakness and Resistance for Benched Pokémon.)`
- **Deerling**：`Discard a {G} Energy from this Pokémon.`

</details>

<details><summary>样例 · knockout_condition</summary>

- **Legacy Energy**：`As long as this card is attached to a Pokémon, it provides every type of Energy but provides only 1 Energy at a time.

If the Pokémon this card is attached to is Knocked Out by damage from an attack f`
- **Iron Leaves**：`If any of your Pokémon were Knocked Out by damage from an attack during your opponent’s last turn, this attack does 60 more damage.`
- **Dusclops**（彷徨夜灵）：`Once during your turn, you may put 5 damage counters on 1 of your opponent’s Pokémon. If you use this Ability, this Pokémon is Knocked Out.`

</details>

<details><summary>样例 · discard_self</summary>

- **Team Rocket's Energy**：`This card can only be attached to a Team Rocket’s Pokémon. If this card is attached to anything other than a Team Rocket’s Pokémon, discard this card.

As long as this card is attached to a Pokémon, i`
- **Wugtrio ex**：`Discard up to 3 Energy cards from your hand. This attack does 60 damage to 1 of your opponent’s Pokémon for each Energy card you discarded in this way. (Don’t apply Weakness and Resistance for Benched`
- **Great Tusk**：`Discard the top card of your opponent’s deck. If you played an Ancient Supporter card from your hand during this turn, discard 3 more cards in this way.`

</details>

### B4 · 需新引擎机制（P1 登记）（215 条 / 212 卡）

- 目标：需要条件表达式 / RNG 钩子 / 静态修正层等新机制，不属本轮
- 动作：登记为 P1 批次；本轮仅保证「如实标注不支持」，不假装支持

| 子类 | 动作类型 | 条目 | 卡数 | 未建模原因 |
|---|---|---:|---:|---|
| static_lock | rule | 90 | 87 | 引擎未建模该动作类型 |
| coin_flip | rule | 70 | 70 | 引擎未建模该动作类型 |
| scaling_damage | rule | 55 | 55 | 引擎未建模该动作类型 |

<details><summary>样例 · static_lock</summary>

- **Poltchageist**：`As long as this Pokémon is on your Bench, prevent all damage from and effects of attacks from your opponent’s Pokémon done to this Pokémon.`
- **Magcargo ex**：`As long as this Pokémon is on your Bench, prevent all damage done to this Pokémon by attacks (both yours and your opponent’s).`
- **Iron Thorns ex**：`As long as this Pokémon is in the Active Spot, Pokémon with a Rule Box in play (both yours and your opponent’s) have no Abilities, except for Future Pokémon. (Pokémon {ex}, Pokémon {V}, etc. have Rule`

</details>

<details><summary>样例 · coin_flip</summary>

- **Eevee**：`Flip a coin. If heads, this attack does 20 more damage.`
- **Gastly**：`Flip a coin. If heads, discard an Energy from your opponent’s Active Pokémon.`
- **Dunsparce**（土龙弟弟）：`Flip a coin. If heads, during your opponent’s next turn, prevent all damage from and effects of attacks done to this Pokémon.`

</details>

<details><summary>样例 · scaling_damage</summary>

- **Magcargo ex**：`Discard the top card of each player’s deck. This attack does 140 more damage for each Energy card discarded in this way.`
- **Roaring Moon**：`This attack does 10 more damage for each Ancient card in your discard pile.`
- **Rabsca**：`This attack does 30 more damage for each Energy attached to your opponent’s Active Pokémon.`

</details>

### B5 · 不建模登记（447 条 / 403 卡）

- 目标：真·静态规则与元游戏效果（如卡名依赖、双方限制、场地级规则）
- 动作：永久登记在差异透明清单（C5），不做实现

| 子类 | 动作类型 | 条目 | 卡数 | 未建模原因 |
|---|---|---:|---:|---|
| unclassified | rule | 447 | 403 | 引擎未建模该动作类型 |

<details><summary>样例 · unclassified</summary>

- **Boomerang Energy**：`As long as this card is attached to a Pokémon, it provides {C} Energy.

If this card is discarded by an effect of an attack used by the Pokémon this card is attached to, attach this card from your dis`
- **Neo Upper Energy**：`As long as this card is attached to a Pokémon, it provides {C} Energy.

If this card is attached to a Stage 2 Pokémon, this card provides every type of Energy but provides only 2 Energy at a time.`
- **Mist Energy**：`As long as this card is attached to a Pokémon, it provides {C} Energy.

Prevent all effects of attacks used by your opponent’s Pokémon done to the Pokémon this card is attached to. (Existing effects a`

</details>

## 4. 口径说明

- 「未建模条目」= effects_json.parse.unsupported 的条目数（一条效果可含多条动作）。
- B1 的「高频」锚点 = 出现在 A1–A4 预设真实卡表（2026 Worlds meta）中的卡；副本数越多 = 在真实对战中越常出现。
- B3/B4/B5 的 rule 桶归属由英文卡面关键词启发式判定（确定性，可复跑），故为**排期估算**而非精确实现清单。
- 诚实标注（C5）：未被映射的条目一律保留在 unsupported 中，不静默降级。
