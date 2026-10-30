# PokéLab AI · 未建模动作清单（backlog）

- 快照：`current` · 生成时间：2026-10-29 17:44:07
- 数据源：`packages/card-db/data/db/pokelab.db`（K2 解析后端：rule（离线确定性基线））

## 0. 总量

| 指标 | 值 |
|---|---|
| 卡库总卡数 | 1267 |
| 已跑解析卡数 | 1119 |
| 解析条目（item） | 1481 |
| 解析动作条目（spec.actions） | 1481 |
| 已映射引擎 tag 条目 | 1327 |
| **未建模动作条目** | **268** |
| 含未建模条目的卡数 | 258 |
| 全效果可映射卡数 | 861 |
| 至少 1 条引擎 tag 的卡数 | 997 |

## 1. 批次总览（按优先级）

| 批次 | 主题 | 条目 | 影响卡数 | 占比 | 建模动作 |
|---|---|---:|---:|---:|---|
| **B1** | 预设卡组关键卡 | 0 | 0 | 0.0% | 为每张卡补引擎原生 tag（训练家 tag / 特性 tag），并补引擎派发 |
| **B2** | tag 家族扩展 | 34 | 34 | 12.7% | agent.effects.to_engine_tags 放通映射 + engine.battle 新增派发分支 |
| **B3** | 解析召回补齐（英文卡面） | 70 | 70 | 26.1% | agent.effects._parse_rule_en 补正则（弃牌堆回收/弃自身能量/抛币前缀/附能/检索/附加伤害目标子句） |
| **B4** | 需新引擎机制（P1 登记） | 30 | 30 | 11.2% | 登记为 P1 批次；本轮仅保证「如实标注不支持」，不假装支持 |
| **B5** | 静态规则与新卡种（**实现队列**） | 134 | 134 | 50.0% | S1 静态修正层（HP/减伤/弱点/撤退）· S2 道具挂载 · S3 场地 · S4 特殊能量 · S5 元游戏与信息类 · S6 待拆解（需人工判定） |

## 2. B1 · 预设卡组关键卡（最高优先，逐卡建模）

> 「高频」= 出现在 A1–A4 预设真实卡表（2026 Worlds meta）中；副本数越多越常出现。

| 卡名 | 副本 | 状态 | 影响卡组 | 原因 |
|---|---:|---|---|---|

## 3. B2–B5 · 分组明细

### B2 · tag 家族扩展（34 条 / 34 卡）

- 目标：把引擎已有能力或低成本可扩展的 tag 家族补齐映射与派发
- 动作：agent.effects.to_engine_tags 放通映射 + engine.battle 新增派发分支

| 子类 | 动作类型 | 条目 | 卡数 | 未建模原因 |
|---|---|---:|---:|---|
| trainer_search | search | 12 | 12 | 检索数量由计数源决定（B4） |
| trainer_search | search | 7 | 7 | 检索去向不可判（B4） |
| trainer_search | search | 7 | 7 | 检索附能需自行分配目标（B4） |
| evolve_skip | evolve | 3 | 3 | 招式/特性驱动的进化需卡级 tag |
| trainer_search | search | 2 | 2 | 检索后放到牌库顶/底（B4） |
| trainer_search | search | 2 | 2 | 抛币分支内的检索（需 coin 嵌套，B4） |
| status_unknown | status | 1 | 1 | 特性发动代价未建模（B4） |

<details><summary>样例 · trainer_search</summary>

- **Lillie’s Comfey**（莉莉艾的花疗环环）：`You may search your deck for any number of Basic Lillie’s Pokémon and put them onto your Bench. Then, shuffle your deck.`
- **Team Rocket's Nidorina**（火箭队的尼多娜）：`Choose up to 2 of your {D} Pokémon. For each of those Pokémon, search your deck for a card that evolves from that Pokémon and put it onto that Pokémon to evolve it. Then, shuffle your deck.`
- **Musharna**（梦梦蚀）：`You may search your deck for any number of Fennel cards, reveal them, and put them into your hand. Then, shuffle your deck.`

</details>

<details><summary>样例 · trainer_search</summary>

- **Drilbur**（螺钉地鼠）：`When you play this Pokémon from your hand onto your Bench during your turn, you may search your deck for up to 3 Basic {F} Energy cards and discard them. Then, shuffle your deck.`
- **Palafin**（海豚侠）：`Once during your turn, when this Pokémon moves from the Active Spot to the Bench, you may search your deck for a Palafin {ex} and switch it with this Pokémon. Any attached cards, damage counters, Spec`
- **Bug Catching Set**（捕虫套装）：`Look at the top 7 cards of your deck. You may reveal up to 2 in any combination of {G} Pokémon and Basic {G} Energy cards you find there and put them into your hand. Shuffle the other cards back into `

</details>

<details><summary>样例 · trainer_search</summary>

- **Miraidon**（密勒顿）：`Search your deck for up to 2 Basic Energy cards and attach them to your Future Pokémon in any way you like. Then, shuffle your deck.`
- **Joltik**（电电虫）：`Search your deck for up to 2 Basic {G} Energy cards and up to 2 Basic {L} Energy cards and attach them to your Pokémon in any way you like. Then, shuffle your deck.`
- **Altaria**（七夕青鸟）：`Search your deck for up to 2 Basic Energy cards and attach them to your Pokémon in any way you like. Then, shuffle your deck.`

</details>

<details><summary>样例 · evolve_skip</summary>

- **Team Rocket's Nidorina**（火箭队的尼多娜）：`Choose up to 2 of your {D} Pokémon. For each of those Pokémon, search your deck for a card that evolves from that Pokémon and put it onto that Pokémon to evolve it. Then, shuffle your deck.`
- **Duosion**（双卵细胞球）：`Search your deck for a card that evolves from 1 of your Pokémon and put it onto that Pokémon to evolve it. Then, shuffle your deck.`
- **Archeops**（始祖大鸟）：`Once during your turn, if this Pokémon is in the Active Spot, you may devolve 1 of your opponent’s evolved Pokémon by putting the highest Stage Evolution card on it into your opponent’s hand.`

</details>

### B3 · 解析召回补齐（英文卡面）（70 条 / 70 卡）

- 目标：英文卡面句式未被规则解析器识别，导致整条效果落进 rule 兜底
- 动作：agent.effects._parse_rule_en 补正则（弃牌堆回收/弃自身能量/抛币前缀/附能/检索/附加伤害目标子句）

| 子类 | 动作类型 | 条目 | 卡数 | 未建模原因 |
|---|---|---:|---:|---|
| attach | rule | 13 | 13 | 静态/未建模（英文） |
| draw | rule | 6 | 6 | 抽牌句式不可精确表达（按计数/对手同抽/条件追加/分支替换，B4） |
| search | rule | 5 | 5 | 检视牌库顶：去向非「入手」（弃置/附着等），引擎 recon 仅支持取 1 入手（B4） |
| attach | rule | 4 | 4 | 检视牌库顶：去向非「入手」（弃置/附着等），引擎 recon 仅支持取 1 入手（B4） |
| discard_self | rule | 4 | 4 | 静态/未建模（英文） |
| draw | rule | 4 | 4 | 打出前置条件（You can use this card only if …）未建模（B5） |
| attach | rule | 3 | 3 | 回复范围/限定不可精确表达（B4） |
| attach | rule | 3 | 3 | 打出前置条件（You can use this card only if …）未建模（B5） |
| search | rule | 3 | 3 | 打出前置条件（You can use this card only if …）未建模（B5） |
| attach | rule | 2 | 2 | 「any number of」自由数量（弃牌/附着/指示物移动，需交互层，B4） |
| switch | rule | 2 | 2 | 静态/未建模（英文） |
| discard_self | rule | 2 | 2 | 打出前置条件（You can use this card only if …）未建模（B5） |
| spread | rule | 1 | 1 | 放置伤害指示物：目标/条件未识别（the Defending Pokémon） |
| spread | rule | 1 | 1 | 放置伤害指示物：目标/条件未识别（this Pokémon） |
| spread | rule | 1 | 1 | 静态伤害增减（需引擎静态修正层，B4） |
| spread | rule | 1 | 1 | 放置伤害指示物：任意分配含**战斗位**，引擎 spread_free 仅覆盖后备（B4） |
| search | rule | 1 | 1 | 能量移动的源/目标未识别（B4） |
| switch | rule | 1 | 1 | 状态词非主动施加（免疫/追加伤害/换位触发，B4） |
| move_damage | rule | 1 | 1 | 「any number of」自由数量（弃牌/附着/指示物移动，需交互层，B4） |
| discard_self | rule | 1 | 1 | 条件伤害缩放：计数源未识别（Misty's Pokémon that you disca） |
| discard_energy_self | rule | 1 | 1 | 静态/未建模（英文） |
| spread | rule | 1 | 1 | 放置伤害指示物：目标/条件未识别（that Pokémon） |
| spread | rule | 1 | 1 | 放置伤害指示物：目标/条件未识别（each of them） |
| search | rule | 1 | 1 | 受击/受击历史从句（is|was damaged by an attack）需引擎 on_damaged 或回合级历史（B4） |
| discard_self | rule | 1 | 1 | 条件伤害缩放：计数源未识别（Basic {W} Energy card that you） |
| attach | rule | 1 | 1 | 放置伤害指示物：目标/条件未识别（that Pokémon） |
| switch | rule | 1 | 1 | 静态伤害增减（需引擎静态修正层，B4） |
| discard_self | rule | 1 | 1 | 回合末才执行的弃手牌（需延迟队列，B4） |
| draw | rule | 1 | 1 | 抽到指定张数带分支替换（B4） |
| draw | rule | 1 | 1 | 抛币分支内的抽牌（需 coin 嵌套，B4） |
| draw | rule | 1 | 1 | 「any number of」自由数量（弃牌/附着/指示物移动，需交互层，B4） |

<details><summary>样例 · attach</summary>

- **Okidogi**（够赞狗）：`If this Pokémon has any {D} Energy attached, it gets +100 HP, and the attacks it uses do 100 more damage to your opponent’s Active Pokémon (before applying Weakness and Resistance).`
- **Archaludon**（铝钢桥龙）：`All of your Pokémon that have {M} Energy attached have no Retreat Cost.`
- **Zarude**（萨戮德）：`You may put all Energy attached to this Pokémon into your hand to have this attack do 80 more damage.`

</details>

<details><summary>样例 · draw</summary>

- **Unfair Stamp**（不公平印章）：`You can use this card only if any of your Pokémon were Knocked Out during your opponent’s last turn.

Each player shuffles their hand into their deck. Then, you draw 5 cards, and your opponent draws 2`
- **Billy & O'Nare**（比利与奈亚）：`Draw 2 cards. Then, if you have 10 or more cards in your hand, draw 2 more cards.`
- **Lacey**（蕾西）：`Shuffle your hand into your deck. Then, draw 4 cards. If your opponent has 3 or fewer Prize cards remaining, draw 8 cards instead.`

</details>

<details><summary>样例 · search</summary>

- **Morpeko**（莫鲁贝可）：`Once during your turn, you may look at the top card of your deck. You may discard that card.`
- **Tatsugiri ex**（米立龙 ex）：`Look at the top 10 cards of your deck. You may put any number of Pokémon you find there onto your Bench. Shuffle the other cards back into your deck.`
- **Rockruff**（岩狗狗）：`Look at the top card of your deck. You may discard that card.`

</details>

<details><summary>样例 · attach</summary>

- **Metang**（金属怪）：`Once during your turn, you may look at the top 4 cards of your deck and attach any number of Basic {M} Energy cards you find there to your Pokémon in any way you like. Shuffle the other cards and put `
- **Lapras ex**（拉普拉斯 ex）：`Look at the top 20 cards of your deck and attach any number of Energy cards you find there to your Pokémon in any way you like. Shuffle the other cards back into your deck.`
- **Grumpig**（噗噗猪）：`Once during your turn, when you play this Pokémon from your hand to evolve 1 of your Pokémon, you may use this Ability. Look at the top 4 cards of your deck and attach any number of Basic Energy cards`

</details>

### B4 · 需新引擎机制（P1 登记）（30 条 / 30 卡）

- 目标：需要条件表达式 / RNG 钩子 / 静态修正层 / 多目标选择等新机制，不属本轮
- 动作：登记为 P1 批次；本轮仅保证「如实标注不支持」，不假装支持

| 子类 | 动作类型 | 条目 | 卡数 | 未建模原因 |
|---|---|---:|---:|---|
| coin_flip | rule | 6 | 6 | 静态/未建模（英文） |
| extra_damage_target | damage | 2 | 2 | 附加伤害目标未建模（itself） |
| type:attack_lock | attack_lock | 2 | 2 | 对手指定招式的封锁（B4） |
| static_lock | rule | 2 | 2 | 静态规则修改（弱点/抗性/伤害减免/限制） |
| scaling_damage | rule | 1 | 1 | 条件伤害缩放：计数源未识别（damage counter you placed in t） |
| static_lock | rule | 1 | 1 | 静态/未建模（英文） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（1 of your opponent’s Benched Pokémon {ex} or Benched Pokémon） |
| scaling_damage | rule | 1 | 1 | 条件伤害缩放：计数源未识别（Ancient card in your discard p） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（1 of your opponent’s Benched Pokémon） |
| coin_flip | rule | 1 | 1 | 状态词非主动施加（免疫/追加伤害/换位触发，B4） |
| type:condition | condition | 1 | 1 | 进化触发特性的前置条件未建模（B4） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（each of your opponent’s Pokémon {ex} and Pokémon {V}） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（each of your opponent’s Pokémon {ex}） |
| scaling_damage | rule | 1 | 1 | 条件伤害缩放：计数源未识别（{L} Energy attached to all of ） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（1 of your opponent’s Benched Pokémon {ex}） |
| coin_flip | rule | 1 | 1 | 抛币分支内的能量回手（需 coin 嵌套，B4） |
| coin_flip | rule | 1 | 1 | 受击/受击历史从句（is|was damaged by an attack）需引擎 on_damaged 或回合级历史（B4） |
| static_lock | rule | 1 | 1 | 静态伤害增减（需引擎静态修正层，B4） |
| extra_damage_target | damage | 1 | 1 | 附加伤害目标未建模（that Pokémon） |
| coin_flip | rule | 1 | 1 | 放置伤害指示物：目标/条件未识别（1 of your opponent’s Pokémon） |
| coin_flip | rule | 1 | 1 | 抛币分支内的抽牌（需 coin 嵌套，B4） |
| static_lock | rule | 1 | 1 | 打出前置条件（You can use this card only if …）未建模（B5） |

<details><summary>样例 · coin_flip</summary>

- **Sylveon**（仙子伊布）：`Flip a coin. If heads, choose 1 of your opponent’s Benched Pokémon. Shuffle that Pokémon and all attached cards into their deck.`
- **Ethan's Sudowoodo**（阿响的树才怪）：`Flip a coin. If heads, choose 1 of your opponent’s Active Pokémon’s attacks and use it as this attack.`
- **Shiftry**（狡猾天狗）：`Flip a coin. If heads, choose 1 of your opponent’s Pokémon. Shuffle that Pokémon and all attached cards into their deck.`

</details>

<details><summary>样例 · extra_damage_target</summary>

- **Gurdurr**（铁骨土人）：`You may do 30 more damage. If you do, this Pokémon also does 30 damage to itself.`
- **Dondozo ex**（吃吼霸 ex）：`You may do 120 more damage. If you do, this Pokémon also does 50 damage to itself.`

</details>

<details><summary>样例 · type:attack_lock</summary>

- **Team Rocket's Murkrow**（火箭队的黑暗鸦）：`Choose 1 of your opponent’s Active Pokémon’s attacks. During your opponent’s next turn, that Pokémon can’t use that attack.`
- **Klefki**（钥圈儿）：`Choose 1 of your opponent’s Active Pokémon’s attacks. During your opponent’s next turn, that Pokémon can’t use that attack.`

</details>

<details><summary>样例 · static_lock</summary>

- **Iron Defender**（铁壁防御）：`During your opponent’s next turn, all of your {M} Pokémon take 30 less damage from attacks from your opponent’s Pokémon (after applying Weakness and Resistance). (This includes new Pokémon that come i`
- **Acerola's Mischief**（阿塞萝拉的恶作剧）：`You can use this card only if your opponent has 2 or fewer Prize cards remaining.

Choose 1 of your Pokémon in play. During your opponent’s next turn, prevent all damage from and effects of attacks do`

</details>

### B5 · 静态规则与新卡种（**实现队列**）（134 条 / 134 卡）

- 目标：原「永久登记」的静态规则 / 新卡种 / 元游戏效果 —— 现全部纳入实现范围，按机制族（S1–S6）分批实现
- 动作：S1 静态修正层（HP/减伤/弱点/撤退）· S2 道具挂载 · S3 场地 · S4 特殊能量 · S5 元游戏与信息类 · S6 待拆解（需人工判定）

| 子类 | 动作类型 | 条目 | 卡数 | 未建模原因 |
|---|---|---:|---:|---|
| S6 | rule | 61 | 61 | 静态/未建模（英文） |
| S5 | rule | 16 | 16 | 静态/未建模（英文） |
| S2 | rule | 7 | 7 | 静态/未建模（英文） |
| S6 | rule | 5 | 5 | 「any number of」自由数量（弃牌/附着/指示物移动，需交互层，B4） |
| S6 | rule | 3 | 3 | 条件伤害缩放：条件未识别（引擎无该条件） |
| S6 | rule | 3 | 3 | 无视目标身上效果（需效果免疫/清除层，B5） |
| S6 | rule | 3 | 3 | 静态伤害增减（需引擎静态修正层，B4） |
| S6 | rule | 3 | 3 | 手牌禁打（Supporter/Stadium/ACE SPEC/特性宝可梦等，需对手手牌禁用层，B5） |
| S2 | rule | 2 | 2 | 手牌禁打（Supporter/Stadium/ACE SPEC/特性宝可梦等，需对手手牌禁用层，B5） |
| S6 | rule | 2 | 2 | 首回合招式使用限制（需招式静态条件层，B5） |
| S1 | rule | 2 | 2 | 静态/未建模（英文） |
| S6 | rule | 2 | 2 | 状态词非主动施加（免疫/追加伤害/换位触发，B4） |
| S5 | rule | 2 | 2 | 打出前置条件（You can use this card only if …）未建模（B5） |
| S3 | rule | 2 | 2 | 场地形态未建模（S3 未覆盖） |
| S4 | rule | 1 | 1 | 静态/未建模（英文） |
| S6 | rule | 1 | 1 | 未建模的招式失败门（条件不满足则此招式无效，B5） |
| S6 | rule | 1 | 1 | 条件伤害缩放：计数源未识别（of your opponent's Pokémon {ex） |
| S1 | rule | 1 | 1 | 静态规则修改（弱点/抗性/伤害减免/限制） |
| S6 | rule | 1 | 1 | 静态规则修改（弱点/抗性/伤害减免/限制） |
| S3 | rule | 1 | 1 | 静态/未建模（英文） |
| S6 | rule | 1 | 1 | 抛币分支内的状态：多币条件/多状态/非对手战斗位，需扩展 coin 子效果（B4） |
| S3 | rule | 1 | 1 | 手牌禁打（Supporter/Stadium/ACE SPEC/特性宝可梦等，需对手手牌禁用层，B5） |
| S6 | rule | 1 | 1 | 受击/受击历史从句（is|was damaged by an attack）需引擎 on_damaged 或回合级历史（B4） |
| S2 | rule | 1 | 1 | 条件伤害缩放：计数源未识别（Pokémon Tool attached to all P） |
| S6 | rule | 1 | 1 | 放置伤害指示物：目标/条件未识别（1 of your opponent’s Benched Pokémon） |
| S6 | rule | 1 | 1 | 放置伤害指示物：目标/条件未识别（that Pokémon） |
| S6 | rule | 1 | 1 | 能量移动的源/目标未识别（B4） |
| S6 | rule | 1 | 1 | 条件伤害缩放：计数源未识别（card you revealed in this way） |
| S4 | rule | 1 | 1 | 弃置**特殊**能量（引擎不区分基本/特殊能量，B4） |
| S2 | rule | 1 | 1 | 同时弃道具与**特殊**能量（引擎不区分基本/特殊，B4） |
| S6 | rule | 1 | 1 | 回复范围/限定不可精确表达（B4） |
| S2 | rule | 1 | 1 | 打出前置条件（You can use this card only if …）未建模（B5） |
| S2 | rule | 1 | 1 | 受击/受击历史从句（is|was damaged by an attack）需引擎 on_damaged 或回合级历史（B4） |
| S2 | rule | 1 | 1 | 静态伤害增减（需引擎静态修正层，B4） |
| S6 | rule | 1 | 1 | 全量回复（范围/后续不可精确表达 · B4） |

<details><summary>样例 · S6</summary>

- **Shiftry**（狡猾天狗）：`Choose 3 of your opponent’s Benched Pokémon. If you do, shuffle all of your opponent’s Benched Pokémon that you didn’t choose, and all cards attached to those Pokémon, into their deck.`
- **Dipplin**（裹蜜虫）：`If Festival Grounds is in play, this Pokémon may use an attack it has twice. If the first attack Knocks Out your opponent’s Active Pokémon, you may attack again after your opponent chooses a new Activ`
- **Goldeen**（角金鱼）：`If Festival Grounds is in play, this Pokémon may use an attack it has twice. If the first attack Knocks Out your opponent’s Active Pokémon, you may attack again after your opponent chooses a new Activ`

</details>

<details><summary>样例 · S5</summary>

- **Hoothoot**（咕咕）：`Your opponent reveals their hand.`
- **Munkidori ex**（愿增猿 ex）：`If this Pokémon is Knocked Out by damage from an attack from your opponent’s Pokémon, and if you have any Pecharunt {ex} in play, your opponent takes 1 fewer Prize card.`
- **Espurr**（妙喵）：`Your opponent reveals their hand.`

</details>

<details><summary>样例 · S2</summary>

- **Rotom**（洛托姆）：`Your opponent reveals their hand. Discard all Item cards and Pokémon Tool cards you find there.`
- **Rotom ex**（洛托姆 ex）：`Each of your Pokémon that has "Rotom" in its name may have up to 2 Pokémon Tool cards attached. If this Ability goes away, discard Pokémon Tools from those Pokémon until only 1 remains on each.`
- **Heavy Baton**（沉重接力棒）：`If the Pokémon this card is attached to has a Retreat Cost of exactly 4, is in the Active Spot, and is Knocked Out by damage from an attack from your opponent’s Pokémon, move up to 3 Basic Energy card`

</details>

<details><summary>样例 · S6</summary>

- **Lillie’s Ribombee**（莉莉艾的蝶结萌虻）：`When you play this Pokémon from your hand to evolve 1 of your Pokémon during your turn, you may have your opponent reveal their hand and you put any number of Basic Pokémon you find there onto their B`
- **Team Rocket's Honchkrow**（火箭队的乌鸦头头）：`You may discard any number of Supporter cards that have “Team Rocket” in their name from your hand, and this attack does 60 damage for each card you discarded in this way.`
- **Doublade**（双剑鞘）：`Reveal any number of Honedge, Doublade, and Aegislash from your hand, and this attack does 60 damage for each card you revealed in this way.`

</details>

## 4. 口径说明

- 「未建模条目」= effects_json.parse.unsupported 的条目数（一条效果可含多条动作）。
- B1 的「高频」锚点 = 出现在 A1–A4 预设真实卡表（2026 Worlds meta）中的卡；副本数越多 = 在真实对战中越常出现。
- B3/B4/B5 的 rule 桶归属由英文卡面关键词启发式判定（确定性，可复跑），故为**排期估算**而非精确实现清单。
- 诚实标注（C5）：未被映射的条目一律保留在 unsupported 中，不静默降级。
