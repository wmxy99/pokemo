# PokéLab AI · 未建模动作清单（backlog）

- 快照：`current` · 生成时间：2026-10-27 00:29:44
- 数据源：`packages/card-db/data/db/pokelab.db`（K2 解析后端：rule（离线确定性基线））

## 0. 总量

| 指标 | 值 |
|---|---|
| 卡库总卡数 | 1267 |
| 已跑解析卡数 | 1119 |
| 解析条目（item） | 1481 |
| 解析动作条目（spec.actions） | 1481 |
| 已映射引擎 tag 条目 | 969 |
| **未建模动作条目** | **577** |
| 含未建模条目的卡数 | 505 |
| 全效果可映射卡数 | 614 |
| 至少 1 条引擎 tag 的卡数 | 802 |

## 1. 批次总览（按优先级）

| 批次 | 主题 | 条目 | 影响卡数 | 占比 | 建模动作 |
|---|---|---:|---:|---:|---|
| **B1** | 预设卡组关键卡 | 0 | 0 | 0.0% | 为每张卡补引擎原生 tag（训练家 tag / 特性 tag），并补引擎派发 |
| **B2** | tag 家族扩展 | 9 | 9 | 1.6% | agent.effects.to_engine_tags 放通映射 + engine.battle 新增派发分支 |
| **B3** | 解析召回补齐（英文卡面） | 99 | 99 | 17.2% | agent.effects._parse_rule_en 补正则（弃牌堆回收/弃自身能量/抛币前缀/附能/检索/附加伤害目标子句） |
| **B4** | 需新引擎机制（P1 登记） | 67 | 67 | 11.6% | 登记为 P1 批次；本轮仅保证「如实标注不支持」，不假装支持 |
| **B5** | 静态规则与新卡种（**实现队列**） | 402 | 386 | 69.7% | S1 静态修正层（HP/减伤/弱点/撤退）· S2 道具挂载 · S3 场地 · S4 特殊能量 · S5 元游戏与信息类 · S6 待拆解（需人工判定） |

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

### B3 · 解析召回补齐（英文卡面）（99 条 / 99 卡）

- 目标：英文卡面句式未被规则解析器识别，导致整条效果落进 rule 兜底
- 动作：agent.effects._parse_rule_en 补正则（弃牌堆回收/弃自身能量/抛币前缀/附能/检索/附加伤害目标子句）

| 子类 | 动作类型 | 条目 | 卡数 | 未建模原因 |
|---|---|---:|---:|---|
| attach | rule | 21 | 21 | 静态/未建模（英文） |
| knockout_condition | knockout | 12 | 12 | 击倒效果需引擎新机制（未建模动作清单 B4） |
| discard_self | rule | 10 | 10 | 静态/未建模（英文） |
| search | rule | 8 | 8 | 检视牌库顶：去向非「入手」（弃置/附着等），引擎 recon 仅支持取 1 入手（B4） |
| attach | rule | 8 | 8 | 条件伤害缩放：条件未识别（引擎无该条件） |
| attach | rule | 4 | 4 | 检视牌库顶：去向非「入手」（弃置/附着等），引擎 recon 仅支持取 1 入手（B4） |
| switch | rule | 4 | 4 | 静态/未建模（英文） |
| spread | rule | 3 | 3 | 放置伤害指示物：目标/条件未识别（1 of your opponent’s Pokémon） |
| spread | rule | 3 | 3 | 放置伤害指示物：目标/条件未识别（the Attacking Pokémon） |
| attach | rule | 2 | 2 | 全量回复（需引擎新机制 · B4） |
| spread | rule | 2 | 2 | 放置伤害指示物：目标/条件未识别（each Pokémon that has an Ability） |
| attach | rule | 2 | 2 | 放置伤害指示物：目标/条件未识别（that Pokémon） |
| draw | rule | 2 | 2 | 静态/未建模（英文） |
| search | rule | 2 | 2 | 静态/未建模（英文） |
| spread | rule | 1 | 1 | 放置伤害指示物：目标/条件未识别（1 of your opponent’s Pokémon for each Ba） |
| spread | rule | 1 | 1 | 放置伤害指示物：目标/条件未识别（the Defending Pokémon） |
| spread | rule | 1 | 1 | 放置伤害指示物：目标/条件未识别（this Pokémon） |
| spread | rule | 1 | 1 | 放置伤害指示物：任意分配含**战斗位**，引擎 spread_free 仅覆盖后备（B4） |
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

### B4 · 需新引擎机制（P1 登记）（67 条 / 67 卡）

- 目标：需要条件表达式 / RNG 钩子 / 静态修正层 / 多目标选择等新机制，不属本轮
- 动作：登记为 P1 批次；本轮仅保证「如实标注不支持」，不假装支持

| 子类 | 动作类型 | 条目 | 卡数 | 未建模原因 |
|---|---|---:|---:|---|
| static_lock | rule | 18 | 18 | 静态规则修改（弱点/抗性/伤害减免/限制） |
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

### B5 · 静态规则与新卡种（**实现队列**）（402 条 / 386 卡）

- 目标：原「永久登记」的静态规则 / 新卡种 / 元游戏效果 —— 现全部纳入实现范围，按机制族（S1–S6）分批实现
- 动作：S1 静态修正层（HP/减伤/弱点/撤退）· S2 道具挂载 · S3 场地 · S4 特殊能量 · S5 元游戏与信息类 · S6 待拆解（需人工判定）

| 子类 | 动作类型 | 条目 | 卡数 | 未建模原因 |
|---|---|---:|---:|---|
| S6 | rule | 219 | 208 | 静态/未建模（英文） |
| S6 | rule | 39 | 39 | 条件伤害缩放：条件未识别（引擎无该条件） |
| S2 | rule | 25 | 25 | 静态/未建模（英文） |
| S5 | rule | 24 | 20 | 静态/未建模（英文） |
| S6 | rule | 16 | 16 | 静态规则修改（弱点/抗性/伤害减免/限制） |
| S3 | rule | 11 | 11 | 静态/未建模（英文） |
| S4 | rule | 10 | 10 | 静态/未建模（英文） |
| S3 | rule | 7 | 7 | 条件伤害缩放：条件未识别（引擎无该条件） |
| S1 | rule | 7 | 6 | 静态/未建模（英文） |
| S6 | rule | 5 | 5 | 全量回复（需引擎新机制 · B4） |
| S2 | rule | 5 | 5 | 条件伤害缩放：计数源未识别（Pokémon Tool attached to all o） |
| S5 | rule | 3 | 3 | 条件伤害缩放：条件未识别（引擎无该条件） |
| S6 | rule | 2 | 2 | 条件伤害缩放：计数源未识别（card in your opponent's hand） |
| S6 | rule | 2 | 2 | 条件伤害缩放：计数源未识别（of your opponent's Pokémon {ex） |
| S6 | rule | 2 | 2 | 条件伤害缩放：计数源未识别（Special Condition affecting yo） |
| S1 | rule | 2 | 2 | 静态规则修改（弱点/抗性/伤害减免/限制） |
| S6 | rule | 2 | 2 | 条件伤害缩放：计数源未识别（Supporter card that has “Team ） |
| S2 | rule | 2 | 2 | 条件伤害缩放：条件未识别（引擎无该条件） |
| S6 | rule | 2 | 2 | 放置伤害指示物：目标/条件未识别（the Attacking Pokémon） |
| S6 | rule | 1 | 1 | 条件伤害缩放：计数源未识别（of your Ancient Pokémon in pla） |
| S6 | rule | 1 | 1 | 条件伤害缩放：计数源未识别（Pokémon in your discard pile t） |
| S6 | rule | 1 | 1 | 条件伤害缩放：计数源未识别（Basic Energy card in your oppo） |
| S6 | rule | 1 | 1 | 条件伤害缩放：计数源未识别（of your Team Rocket's Pokémon ） |
| S6 | rule | 1 | 1 | 条件伤害缩放：计数源未识别（Item card in your opponent's d） |
| S5 | rule | 1 | 1 | 条件伤害缩放：计数源未识别（Trainer card you find there） |
| S6 | rule | 1 | 1 | 条件伤害缩放：计数源未识别（Basic {W} Energy card in your ） |
| S6 | rule | 1 | 1 | 放置伤害指示物：目标/条件未识别（your opponent’s Active Pokémon for each ） |
| S6 | rule | 1 | 1 | 条件伤害缩放：计数源未识别（of your Pokémon that has “Taur） |
| S2 | rule | 1 | 1 | 条件伤害缩放：计数源未识别（Pokémon Tool attached to all P） |
| S5 | rule | 1 | 1 | 条件伤害缩放：计数源未识别（Energy card you find there） |
| S6 | rule | 1 | 1 | 放置伤害指示物：目标/条件未识别（1 of your opponent’s Benched Pokémon） |
| S6 | rule | 1 | 1 | 放置伤害指示物：目标/条件未识别（that Pokémon） |
| S6 | rule | 1 | 1 | 条件伤害缩放：计数源未识别（of your Erika's Pokémon in pla） |
| S6 | rule | 1 | 1 | 条件伤害缩放：计数源未识别（of your Basic Pokémon in play） |
| S6 | rule | 1 | 1 | 放置伤害指示物：仅对手战斗位，需引擎新机制（B4） |
| S6 | rule | 1 | 1 | 条件伤害缩放：计数源未识别（card you revealed in this way） |

<details><summary>样例 · S6</summary>

- **Iron Thorns ex**：`Move an Energy from this Pokémon to 1 of your Benched Pokémon.`
- **Gouging Fire ex**：`This Pokémon can’t use Blaze Blitz again until it leaves the Active Spot.`
- **Totodile**：`During your opponent’s next turn, the Defending Pokémon can’t retreat.`

</details>

<details><summary>样例 · S6</summary>

- **Team Rocket's Kangaskhan ex**：`If you played a Supporter card that has “Team Rocket” in its name from your hand during this turn, this attack does 100 more damage.`
- **Enamorus**：`If any of your Pokémon in play are the same type as any of your opponent’s Pokémon in play, this attack does 120 more damage.`
- **Volbeat**：`If Illumise is on your Bench, this attack does 60 more damage.`

</details>

<details><summary>样例 · S2</summary>

- **Genesect**：`If this Pokémon has a Pokémon Tool attached, your opponent can’t play any {ACE SPEC} cards from their hand.`
- **Mow Rotom**：`Before doing damage, discard all Pokémon Tools and Special Energy from your opponent’s Active Pokémon.`
- **Rotom**：`Your opponent reveals their hand. Discard all Item cards and Pokémon Tool cards you find there.`

</details>

<details><summary>样例 · S5</summary>

- **Bloodmoon Ursaluna ex**（血月月月熊ex）：`During your next turn, this Pokémon can’t use attacks.`
- **Bloodmoon Ursaluna ex**（血月月月熊ex）：`Blood Moon used by this Pokémon costs {C} less for each Prize card your opponent has taken.`
- **Hoothoot**：`Your opponent reveals their hand.`

</details>

## 4. 口径说明

- 「未建模条目」= effects_json.parse.unsupported 的条目数（一条效果可含多条动作）。
- B1 的「高频」锚点 = 出现在 A1–A4 预设真实卡表（2026 Worlds meta）中的卡；副本数越多 = 在真实对战中越常出现。
- B3/B4/B5 的 rule 桶归属由英文卡面关键词启发式判定（确定性，可复跑），故为**排期估算**而非精确实现清单。
- 诚实标注（C5）：未被映射的条目一律保留在 unsupported 中，不静默降级。
