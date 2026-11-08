# 引擎 / 卡库补漏报告（2026-11-08 实测）

> 口径：一切数字都从**代码与数据现算**得出，不从文档抄。复现命令写在每节末尾。
> 用途有两个：① 对外披露「哪些卡还不能打、为什么」；② 作为实现队列 —— 每一项都
> 写清「缺什么能力、影响多少张、怎么算做完」。

## 一、总览

| 项 | 实测 | 说明 |
|---|---|---|
| 卡库总量 | **1267 张** | `cards_challenge.json`（清洗后全量） |
| 引擎可对战（严格口径） | **1249 张 · 98.6%** | `engine_ready=true`，与 `GET /cards/stats` 同源 |
| 引擎卡池条目 | **1234 条** | `packages/engine/engine/data/cards_pool_ext.json` |
| 未进池 | **18 张** | = 14 张「如实拒收」+ 4 张「结构性」（D2/D3/D4/B4 累计让 27 张进池） |
| 解析对账 | **51 类全绿** | `scripts/audit_pool_alignment.py` |
| 规则一致性矩阵 | **39 行（38 ✅ / 1 ⚠️）** | `docs/RULE_CONFORMANCE.md`（CI 机械核对） |
| 测试 | **2804**（engine 1569 / agent 1081 / api 142 / gym 12） | |

复现：

```bash
python packages/card-db/scripts/audit_pool_alignment.py          # 51 类
python scripts/check_conformance.py                              # 矩阵
python -c "import json;d=json.load(open('packages/card-db/data/cleaned/cards_challenge.json',encoding='utf-8'));cs=d['cards'];print(sum(1 for c in cs if c.get('engine_ready')),'/',len(cs))"
```

## 二、口径澄清（三个数字为什么不一样）

| 数字 | 含义 | 差异来源 |
|---|---|---|
| 1222 `engine_ready` | **严格口径**：卡真的能被 AI 选中并正确结算 | 对外只用这个 |
| 1074「解析无未建模动作」 | 解析链没产生未建模动作 | 不含 `engine_ready` 的其它条件（近似/fidelity） |
| 1207 池条目 | 引擎侧的卡定义条数（同名多印刷算多条） | 与 1267 的差是「拒收 + 结构性」 |

**为什么必须三个都披露**：只报最大那个（1207 或 1267）会让人以为「都能打」；
只报最小那个（1074）会让人以为「解析坏了」。三个数一起给，读者才能判断差在哪。

## 三、18 张未进池卡：按缺失能力分族

| 族 | 缺什么能力 | 卡数 | 工作量 | 本批处置 |
|---|---|---:|---|---|
| **F1 · Tera 备战位免伤** | `passive:no_bench_damage`：在**备战位**时免疫招式伤害（含对手狙击、也含自家全场 AoE） | 6 | S | 待做 |
| **F2 · 退化 devolve** | 把最高阶进化卡退回手牌、宝可梦本体降级、当回合不能再进化 | ~~2~~ **0** | M | **✅ 已落地（D3）** |
| **F3 · 任意数量 / 自由分配** | 交互层：`任意张 + 任意分配`（移指示物、移/附/弃能量、展示任意张、选 3 只后备其余洗回…） | 12 | L | 待做 |
| **F4 · 多条件组合门槛** | `play_gate` 支持**合取**（多张具名卡同时在场） | ~~2~~ **1** | M | **✅ 已落地（D2）** |
| **F5 · 静态属性改写** | 改类型（双属性 ✅）、改弱点、能量增值（`{G}{G}`） | 4 → 2 | M | **部分完成（D4）** |
| **F6 · 能力抑制 / 规则框** | 抑制「需要自身击倒」的特性、抑制 Rule Box 宝可梦的特性 | 3 → 0 | M | **✅ 已落地（D4）** |
| **F7 · 道具 / 特殊能量形态** | 工具改招式费用、工具**提供招式**、弃全部工具+特殊能量 | 3 | M | 待做 |
| **F8 · 单卡机制** | 逐卡见附录（计数源、模式选择、静态禁止、双工具上限…） | 13 | M–L | 待做 |

> 「工作量」S = 单点 tag + 对账即可；M = 需要新的小机制或交互形态；L = 需要通用
> 交互层（任意数量），改的是「引擎如何表达决策」，而不是某一族 tag。

### F1 · Tera 备战位免伤（6 张）

Alolan Exeggutor ex · Eevee ex · Espeon ex · Revavroom ex · Sylveon ex ·
Wellspring Mask Ogerpon ex

卡面同一句：`As long as this Pokémon is on your Bench, prevent all damage done to
this Pokémon by attacks (both yours and your opponent's).`

现有 `_self_immune` 只处理**战斗位**的「prevent all damage」；备战位这一条要在
狙击/扩散/全场 AoE 三条路径上分别判（不能只在 `_do_attack` 里判一次 —— 狙击不走
那条路）。**判据**：三路径各有一条「正面用例 + 反向用例（放到战斗位就正常受伤）」。

### F2 · 退化 devolve（2 张，本批做）

- **Archeops**：`Once during your turn, if this Pokémon is in the Active Spot, you
  may devolve 1 of your opponent's evolved Pokémon by putting the highest Stage
  Evolution card on it into their hand.`
- **Strange Timepiece**（道具）：`Devolve 1 of your evolved {P} Pokémon by putting
  any number of Evolution cards on it into your hand. (That Pokémon can't evolve
  this turn.)`

### F3 · 任意数量 / 自由分配（12 张）

Alakazam（移动任意数量指示物）· Forretress（移动任意 Metal 能量）· Gorebyss（手牌
水能量任意附着）· Cinderace（检索 ≤3 基础能量任意分配）· Dedenne（按对手总能量数
从弃牌堆附着）· Mega Charizard X ex（任意弃火能换伤害）· Smeargle（按正面数附能量）·
Walking Wake（自己放 ≤9 指示物）· Zarude（能量收回手牌换 +80）· Doublade（展示任意
张换伤害）· Team Rocket's Honchkrow（弃任意张 TR 支援者）· Shiftry（选 3 只后备，
其余连同附着卡洗回牌库）

共同点：**「数量由玩家决定 + 分配也由玩家决定」**。当前 `PendingChoice` 支持
「选 k 个」（`min_pick/max_pick`），但不支持「数量本身也是决策」的一次性表达；
勉强可以「先选数量再选对象」，但那是两次选择，且中间状态需要冻结 —— 属于交互层改造，
不是加一个 tag。**这是剩余工作量最大的一族。**

### F4 · 多条件组合门槛（2 张，本批做）

- **Anthea & Concordia**：`You can use this card only if you have N's Darmanitan,
  N's Zoroark {ex}, N's Vanilluxe, N's Klinklang, N's Reshiram, and N's Zekrom in
  play.` —— **6 个具名条件同时成立**。
- **Klinklang**：`Once during your turn, if this Pokémon is in your hand and your
  opponent has any Stage 2 Pokémon in play, you may put this Pokémon onto your
  Bench.` —— 手牌 + 对手场上的**合取**。

现在 `play_gate:<cond>` 只支持**单条件**，解析层遇到「A, B, and C」只能整卡拒收
（不猜一个条件照做）。本批把 gate 改成**条件列表**（合取），并让解析层把具名卡列表
转成 `all_in_play:`。

### F5 · 静态属性改写（4 张）

Carbink（`As long as this Pokémon is in play, it is {F} and {P} type`）·
Scovillain ex（同上 G+R）· Lillie's Clefairy ex（改写对手 N 宝可梦的弱点）·
Meganium（每张基础草能量提供 `{G}{G}`）

### F6 · 能力抑制 / 规则框（3 张）

Psyduck（在场时，**需要自身击倒**的特性全部失效）· Golduck（在场时，**需要弃自身
能量**的特性失效）· Iron Thorns ex（在场时，Rule Box 宝可梦的特性全部失效）

### F7 · 道具 / 特殊能量形态（3 张）

Hop's Choice Band（工具：招式费用 −1 且 +30 伤害）· Core Memory（工具**提供招式**：
附着对象可使用卡面上的招式）· Megaton Blower（弃对手全部工具 + 特殊能量 + 场上场地）

### F8 · 单卡机制（13 张，逐卡见附录）

Mega Abomasnow ex / Misty's Gyarados（弃顶 N 张按其中符合条件的张数算伤害）·
Okidogi（按对手上回合取走的奖赏数加伤；另一印刷为「有恶能量则 +100 HP 与 +100 伤害」）·
Meloetta ex（下回合自身招式 +80；动作类型 `self_attack_plus_next` 未登记）·
Sylveon ex（`attacks_minus_next` 未登记）· Milotic（对手的卡**不能**回到手牌）·
Rotom ex（名字含 Rotom 的可附 2 张工具）· Team Rocket's Moltres ex（下回合 −50 伤害 +
弃 TR 能量）· Ludicolo（全队 +40 HP / 有条件回血 60）· Kieran（二选一模式）·
Gengar（按对手后备数加伤 + 被击倒时的连击效果）· Lillie's Ribombee（进化时把对手
手牌里的基础宝可梦铺到自己后备）· Dialga（顶 2 张任意顺序）

## 四、18 张逐卡附录

| 卡 | 卡面关键句（首句） | 族 |
|---|---|---|
| Alakazam | Place 2 damage counters on your opponent's Active Pokémon for each card in your hand | F3 |
| Alolan Exeggutor ex | As long as this Pokémon is on your Bench, prevent all damage done to this Pokémon by attacks | F1 |
| Anthea & Concordia | You can use this card only if you have N's Darmanitan, N's Zoroark {ex}, … | F4 |
| Archeops | Once during your turn, if this Pokémon is in the Active Spot, you may devolve 1 of your opponent's evolved Pokémon | F2 |
| Carbink | If your opponent has 2 or fewer Prize cards remaining, this attack does 100 more damage | F5 |
| Cinderace | Search your deck for up to 3 Basic Energy cards and attach them to your Benched Pokémon in any way you like | F3 |
| Core Memory | The Mega Zygarde {ex} this card is attached to can use the attack on this card | F7 |
| Dedenne | Choose Basic {L} Energy cards from your discard pile up to the amount of Energy attached to all of your opponent's Pokémon | F3 |
| Dialga | You may shuffle all Energy attached to this Pokémon into your deck and have this attack do 80 more damage | F8 |
| Doublade | Reveal any number of Honedge, Doublade, and Aegislash from your hand, and this attack does 60 damage for each card you revealed | F3 |
| Eevee ex | As long as this Pokémon is on your Bench, prevent all damage done to this Pokémon by attacks | F1 |
| Espeon ex | As long as this Pokémon is on your Bench, prevent all damage done to this Pokémon by attacks | F1 |
| Forretress | You may move any amount of {M} Energy from your Pokémon to your other Pokémon in any way you like | F3 |
| Gengar | This attack does 30 more damage for each of your opponent's Benched Pokémon | F8 |
| Golduck | This attack does 20 more damage for each {W} Energy attached to this Pokémon | F6 |
| Gorebyss | This attack does 30 damage for each {W} Energy attached to this Pokémon | F3 |
| Hop's Choice Band | Attacks used by the Hop's Pokémon this card is attached to cost {C} less and do 30 more damage | F7 |
| Iron Thorns ex | Move an Energy from this Pokémon to 1 of your Benched Pokémon | F6 |
| Kieran | Choose 1: • Switch your Active Pokémon with 1 of your Benched Pokémon | F8 |
| Klinklang | All of your Pokémon that have any {M} Energy attached take 20 less damage from attacks | F4 |
| Lillie's Clefairy ex | This attack does 20 more damage for each Benched Pokémon (both yours and your opponent's) | F5 |
| Lillie's Ribombee | When you play this Pokémon from your hand to evolve 1 of your Pokémon during your turn, you may have your opponent reveal their hand … | F8 |
| Ludicolo | Once during your turn, if you have any {G} Mega Evolution Pokémon {ex} in play, you may use this Ability | F8 |
| Mandibuzz | Once during your turn, you may use this Ability … | F8 |
| Mega Abomasnow ex | Discard the top 6 cards of your deck, and this attack does 100 damage for each Basic {W} Energy card that you discarded | F8 |
| Mega Charizard X ex | Discard any amount of {R} Energy from among your Pokémon, and this attack does 90 damage for each card you discarded | F3 |
| Meganium | Each Basic {G} Energy attached to all of your Pokémon provides {G}{G} Energy | F5 |
| Megaton Blower | Discard all Pokémon Tools and Special Energy from all of your opponent's Pokémon, and discard a Stadium in play | F7 |
| Meloetta ex | During your next turn, this Pokémon's Echoed Voice attack does 80 more damage | F8 |
| Milotic | Your opponent's Pokémon in play and all attached cards can't be put into your opponent's hand | F8 |
| Misty's Gyarados | Discard the top 7 cards of your deck, and this attack does 70 damage for each Misty's Pokémon that you discarded | F8 |
| Okidogi | This attack does 60 more damage for each Prize card your opponent took during their last turn | F8 |
| Psyduck | Pokémon in play (both yours and your opponent's) lose any Ability that requires the Pokémon using it to Knock Out itself | F6 |
| Revavroom ex | As long as this Pokémon is on your Bench, prevent all damage done to this Pokémon by attacks | F1 |
| Rotom ex | Discard all Energy from this Pokémon | F8 |
| Scovillain ex | This attack does 70 more damage for each damage counter on this Pokémon | F5 |
| Shiftry | Flip a coin. If heads, choose 1 of your opponent's Pokémon. Shuffle that Pokémon and all attached cards into their deck | F3 |
| Smeargle | Flip 3 coins. Attach an amount of Basic Energy up to the number of heads from your discard pile to your Benched Pokémon | F3 |
| Strange Timepiece | Devolve 1 of your evolved {P} Pokémon by putting any number of Evolution cards on it into your hand | F2 |
| Sylveon ex | As long as this Pokémon is on your Bench, prevent all damage done to this Pokémon by attacks | F1 |
| Team Rocket's Honchkrow | You may discard any number of Supporter cards that have "Team Rocket" in their name from your hand | F3 |
| Team Rocket's Moltres ex | During your opponent's next turn, this Pokémon takes 50 less damage from attacks | F8 |
| Walking Wake | Heal 20 damage from this Pokémon | F3 |
| Wellspring Mask Ogerpon ex | As long as this Pokémon is on your Bench, prevent all damage done to this Pokémon by attacks | F1 |
| Zarude | Heal 20 damage from this Pokémon | F3 |

## 五、非卡面缺口（规则层）

| 编号 | 缺口 | 现状 | 处置 |
|---|---|---|---|
| **D1** | mulligan 补偿是**自动抽**，官方为「可以抽」 | 矩阵 ⚠️ 行 | 需要把 `_begin_turn()` 推迟到 setup 选择闭合之后；风险 > 收益，暂缓（做法已写在矩阵 D1） |
| **D2** | 多条件组合门槛 | 解析层整卡拒收 → 不进池（不会算错） | **本批做** |
| **D3** | 退化 devolve | 无此机制 | **本批做** |
| — | 任意数量交互层（F3） | 无 | 最大的一块，见 F3 |
| — | 效果施加路径唯一性（`fp.damage` 直写扫描） | 未做 AST 守卫 | 建议下一步 |

## 六、做完的判据（每族通用）

1. 卡进池（`engine_ready=true`，池条目 +N），并**加到 `docs/RULE_CONFORMANCE.md`**
   对应行（矩阵覆盖卡数会跟着变，写错 CI 红）；
2. 每条卡面语句都有 tag → 对账类目 +1（若引入新 tag 族）；
3. 正反向用例各一条（「能做到」+「不该做到时不做到」）；
4. **注入式负向自测**：把实现改坏，指定的用例必须变红（不红=假守卫）；
5. 计数守卫与 CI 8/8 通过。
