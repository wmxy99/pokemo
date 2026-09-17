# PokéLab AI · 未建模动作差异透明清单（永久登记）

> **性质**：本清单是项目对外的**规范性披露**（合规红线 **C5 差异透明**），不是待办列表。
> 凡列于此的卡面机制，本引擎**不实现**（或明确留待后续批次）；对应卡牌会被如实标记为 `engine_ready=false` / `engine_status=approximated`，**绝不静默降级或假装支持**。

- 生成时间：2026-09-17 15:33:00
- 数据源：`packages/card-db/data/db/pokelab.db`（解析后端 `rule（离线确定性基线）`）
- 全库 1267 张 · 未建模动作条目 **695** 条 · 已映射 tag 812 条
- **引擎可对战卡（严格口径，与 `/cards/stats` 同源的 `engine_ready`）：426 张**
- 解析层面「无未建模动作」卡：530 张 —— **口径不同**：它只表示「解析链未产生未建模动作」，**不含**库内 `engine_ready` 标志的其它条件（近似/fidelity 等）；两者相差的部分即「解析通过但标志仍不可对战」的卡。**对外引用统一以严格口径（上一行）为准。**

## 批次图例

| 批次 | 含义 | 处置 |
|---|---|---|
| **B4** | 需新增引擎机制（条件伤害修正 / 抛币 / 伤害减免与免疫 / 指示物与计数 / 静态封锁） | **P1 批次**，按清单推进；当前不实现 |
| **B5** | 真静态规则 / 元游戏效果 / 依赖引擎外信息（对手手牌、特定子类检索、场地与工具机制） | **本项目不做**（永久登记） |

## 1. 明细（按条目数降序）

| 批次 | 子类 | 动作类型 | 条目 | 影响卡数 | 原因 |
|---|---|---|---:|---:|---|
| B4 | static_lock | `rule` | 27 | 27 | 静态规则修改（弱点/抗性/伤害减免/限制） |
| B4 | coin_flip | `rule` | 14 | 14 | 静态/未建模（英文） |
| B4 | static_lock | `rule` | 4 | 4 | 静态/未建模（英文） |
| B4 | scaling_damage | `rule` | 4 | 4 | 静态/未建模（英文） |
| B4 | scaling_damage | `rule` | 1 | 1 | 条件伤害缩放：计数源未识别（Ancient card in your discard p） |
| B4 | scaling_damage | `rule` | 1 | 1 | 条件伤害缩放：计数源未识别（Stage 2 Pokémon on your Bench） |
| B4 | scaling_damage | `rule` | 1 | 1 | 静态规则修改（弱点/抗性/伤害减免/限制） |
| B4 | scaling_damage | `rule` | 1 | 1 | 条件伤害缩放：计数源未识别（of your Evolution Pokémon in p） |
| B4 | coin_flip | `rule` | 1 | 1 | 静态规则修改（弱点/抗性/伤害减免/限制） |
| B5 | unclassified | `rule` | 365 | 336 | 静态/未建模（英文） |
| B5 | unclassified | `rule` | 87 | 84 | 静态规则修改（弱点/抗性/伤害减免/限制） |
| B5 | unclassified | `rule` | 5 | 5 | 全量回复（需引擎新机制 · B4） |
| B5 | unclassified | `rule` | 5 | 5 | 条件伤害缩放：计数源未识别（Pokémon Tool attached to all o） |
| B5 | unclassified | `rule` | 4 | 4 | 条件伤害缩放：计数源未识别（of your Pokémon in play that h） |
| B5 | unclassified | `rule` | 2 | 2 | 条件伤害缩放：计数源未识别（card in your opponent's hand） |
| B5 | unclassified | `rule` | 2 | 2 | 条件伤害缩放：计数源未识别（of your opponent's Pokémon {ex） |
| B5 | unclassified | `rule` | 2 | 2 | 条件伤害缩放：计数源未识别（Special Condition affecting yo） |
| B5 | unclassified | `rule` | 2 | 2 | 条件伤害缩放：计数源未识别（Supporter card that has “Team ） |
| B5 | unclassified | `rule` | 1 | 1 | 条件伤害缩放：计数源未识别（of your Ancient Pokémon in pla） |
| B5 | unclassified | `rule` | 1 | 1 | 条件伤害缩放：计数源未识别（Pokémon in your discard pile t） |
| B5 | unclassified | `rule` | 1 | 1 | 条件伤害缩放：计数源未识别（Basic Energy card in your oppo） |
| B5 | unclassified | `rule` | 1 | 1 | 条件伤害缩放：计数源未识别（of your Team Rocket's Pokémon ） |
| B5 | unclassified | `rule` | 1 | 1 | 条件伤害缩放：计数源未识别（Pokémon in play that has “Koff） |
| B5 | unclassified | `rule` | 1 | 1 | 条件伤害缩放：计数源未识别（Item card in your opponent's d） |
| B5 | unclassified | `rule` | 1 | 1 | 条件伤害缩放：计数源未识别（Trainer card you find there） |
| B5 | unclassified | `rule` | 1 | 1 | 条件伤害缩放：计数源未识别（Basic {W} Energy card in your ） |
| B5 | unclassified | `rule` | 1 | 1 | 条件伤害缩放：计数源未识别（of your Pokémon that has “Taur） |
| B5 | unclassified | `rule` | 1 | 1 | 条件伤害缩放：计数源未识别（Pokémon Tool attached to all P） |
| B5 | unclassified | `rule` | 1 | 1 | 条件伤害缩放：计数源未识别（Energy card you find there） |
| B5 | unclassified | `rule` | 1 | 1 | 条件伤害缩放：计数源未识别（of your Erika's Pokémon in pla） |
| B5 | unclassified | `rule` | 1 | 1 | 条件伤害缩放：计数源未识别（of your Basic Pokémon in play） |
| B5 | unclassified | `rule` | 1 | 1 | 条件伤害缩放：计数源未识别（card you revealed in this way） |

## 2. 代表性卡面（逐子类抽样，便于核对）

**B4 · static_lock**（27 条）

- `Rabsca`：Prevent all damage from and effects of attacks from your opponent’s Pokémon done to your Benched Pokémon.
- `Farigiraf ex`：Prevent all damage done to this Pokémon by attacks from your opponent’s Basic Pokémon {ex}.
- `Cornerstone Mask Ogerpon ex`：Prevent all damage from attacks done to this Pokémon by your opponent’s Pokémon that have an Ability.

**B4 · coin_flip**（14 条）

- `Sylveon`：Flip a coin. If heads, choose 1 of your opponent’s Benched Pokémon. Shuffle that Pokémon and all attached cards into their deck.
- `Alolan Exeggutor ex`：Flip a coin. If heads, Knock Out your opponent’s Active Basic Pokémon. If tails, Knock Out 1 of your opponent’s Benched Basic Pokémon.
- `LugiaEX`：Flip a coin until you get tails. For each heads, discard an Energy from your opponent’s Active Pokémon.

**B4 · static_lock**（4 条）

- `Iron Thorns ex`：As long as this Pokémon is in the Active Spot, Pokémon with a Rule Box in play (both yours and your opponent’s) have no Abilities, except for Future Pokémon. (P
- `Flutter Mane`：As long as this Pokémon is in the Active Spot, your opponent’s Active Pokémon has no Abilities, except for Midnight Fluttering.
- `Gastrodon`：As long as this Pokémon is on your Bench, Benched Stage 2 Pokémon (both yours and your opponent’s) have no Abilities.

**B4 · scaling_damage**（4 条）

- `Breloom`：Flip a coin until you get tails. This attack does 50 more damage for each heads.
- `Tyrogue`：Flip a coin until you get tails. This attack does 30 more damage for each heads.
- `Mega Kangaskhan ex`：Flip a coin until you get tails. This attack does 50 more damage for each heads.

**B4 · scaling_damage**（1 条）

- `Roaring Moon`：This attack does 10 more damage for each Ancient card in your discard pile.

**B4 · scaling_damage**（1 条）

- `Mamoswine ex`：This attack does 40 more damage for each Stage 2 Pokémon on your Bench.

**B4 · scaling_damage**（1 条）

- `Team Rocket's Sneasel`：This attack does 20 damage to 1 of your opponent’s Benched Pokémon for each damage counter on that Pokémon. (Don’t apply Weakness and Resistance for Benched Pok

**B4 · scaling_damage**（1 条）

- `Reuniclus`：This attack does 40 more damage for each of your Evolution Pokémon in play.

**B4 · coin_flip**（1 条）

- `Mega Zygarde ex`：For each of your opponent’s Pokémon, flip a coin. If heads, this attack does 150 damage to that Pokémon. (Don’t apply Weakness and Resistance for Benched Pokémo

**B5 · unclassified**（365 条）

- `Boomerang Energy`：As long as this card is attached to a Pokémon, it provides {C} Energy.  If this card is discarded by an effect of an attack used by the Pokémon this card is att
- `Neo Upper Energy`：As long as this card is attached to a Pokémon, it provides {C} Energy.  If this card is attached to a Stage 2 Pokémon, this card provides every type of Energy b
- `Mist Energy`：As long as this card is attached to a Pokémon, it provides {C} Energy.  Prevent all effects of attacks used by your opponent’s Pokémon done to the Pokémon this 

**B5 · unclassified**（87 条）

- `Hippowdon`：This attack also does 40 damage to each Benched Pokémon that has any damage counters on it (both yours and your opponent’s). (Don’t apply Weakness and Resistanc
- `Girafarig`：This attack also does 10 damage to 1 of your Benched Pokémon. (Don’t apply Weakness and Resistance for Benched Pokémon.)
- `Ting-Lu`：If a Stadium is in play, this attack also does 30 damage to each of your opponent’s Benched Pokémon, and discard that Stadium. (Don’t apply Weakness and Resista

**B5 · unclassified**（5 条）

- `Whimsicott`：When you play this Pokémon from your hand to evolve 1 of your Pokémon during your turn, you may heal all damage from your Active {G} Pokémon. If you healed any 
- `Miltank`：Flip 2 coins. If both of them are heads, heal all damage from 1 of your Pokémon.
- `Whimsicott`：Heal all damage from 1 of your Benched Pokémon.

**B5 · unclassified**（5 条）

- `Rotom`：This attack does 30 damage for each Pokémon Tool attached to all of your Pokémon.
- `Mow Rotom`：This attack does 30 damage for each Pokémon Tool attached to all of your Pokémon.
- `Heat Rotom`：This attack does 30 damage for each Pokémon Tool attached to all of your Pokémon.

**B5 · unclassified**（4 条）

- `Tympole`：This attack does 20 damage for each of your Pokémon in play that has the Round attack.
- `Palpitoad`：This attack does 40 damage for each of your Pokémon in play that has the Round attack.
- `Seismitoad`：This attack does 70 damage for each of your Pokémon in play that has the Round attack.

**B5 · unclassified**（2 条）

- `Chandelure`：This attack does 30 damage for each card in your opponent’s hand.
- `Mega Froslass ex`：This attack does 50 damage for each card in your opponent’s hand.

**B5 · unclassified**（2 条）

- `Zoroark`：This attack does 60 damage for each of your opponent’s Pokémon {ex} and Pokémon {V} in play.
- `Dudunsparce ex`：This attack does 60 damage for each of your opponent’s Pokémon {ex} in play.

**B5 · unclassified**（2 条）

- `Cradily`：This attack does 100 damage for each Special Condition affecting your opponent’s Active Pokémon.
- `Team Rocket's Muk`：This attack does 100 damage for each Special Condition affecting your opponent’s Active Pokémon.

**B5 · unclassified**（2 条）

- `Team Rocket's Porygon2`：This attack does 20 damage for each Supporter card that has “Team Rocket” in its name in your discard pile.
- `Team Rocket's Porygon-Z`：This attack does 20 damage for each Supporter card that has “Team Rocket” in its name in your discard pile.

**B5 · unclassified**（1 条）

- `Koraidon`：This attack does 30 damage for each of your Ancient Pokémon in play.

**B5 · unclassified**（1 条）

- `Dartrix`：This attack does 20 damage for each Pokémon in your discard pile that has the United Wings attack.

**B5 · unclassified**（1 条）

- `N’s Darmanitan`：This attack does 30 damage for each Basic Energy card in your opponent’s discard pile.

**B5 · unclassified**（1 条）

- `Team Rocket's Spidops`：This attack does 30 damage for each of your Team Rocket’s Pokémon in play.

**B5 · unclassified**（1 条）

- `Team Rocket's Weezing`：This attack does 40 damage for each Pokémon in play that has “Koffing” or “Weezing” in its name (both yours and your opponent’s).

**B5 · unclassified**（1 条）

- `Tirtouga`：This attack does 30 damage for each Item card in your opponent’s discard pile.

**B5 · unclassified**（1 条）

- `Whimsicott ex`：Your opponent reveals their hand. This attack does 50 damage for each Trainer card you find there.

**B5 · unclassified**（1 条）

- `Kyogre`：This attack does 20 damage for each Basic {W} Energy card in your discard pile. Then, shuffle those cards into your deck.

**B5 · unclassified**（1 条）

- `Paldean Tauros`：This attack does 40 damage for each of your Pokémon that has “Tauros” in its name that has any damage counters on it.

**B5 · unclassified**（1 条）

- `Bronzong`：This attack does 40 damage for each Pokémon Tool attached to all Pokémon.

**B5 · unclassified**（1 条）

- `Beautifly`：Your opponent reveals their hand, and this attack does 80 damage for each Energy card you find there.

**B5 · unclassified**（1 条）

- `Erika's Victreebel`：This attack does 40 damage for each of your Erika’s Pokémon in play.

**B5 · unclassified**（1 条）

- `Passimian`：This attack does 20 damage for each of your Basic Pokémon in play.

**B5 · unclassified**（1 条）

- `Doublade`：Reveal any number of Honedge, Doublade, and Aegislash from your hand, and this attack does 60 damage for each card you revealed in this way.

## 3. 与「已建模」的边界（避免误读）

- **已建模**：招式的伤害/费用/状态/放置指示物/抽弃/检索/回收/换位/附能/移动指示物/抛币/条件与计数伤害修正/护盾与被动免疫；特性的检视牌库、每回合抽牌、附能、被击倒抽牌、进化触发、弱点改写、后备免疫；训练家的检索/回收/换位/跳阶进化等（完整清单见 `packages/engine/engine/data/cards_real.json` → `meta.engine_tags`）。
- **未建模**：本清单 §1 全部条目。受影响卡牌**可浏览、可组卡、不可对战**（标为 `approximated` 时以近似规则参战）。
- **近似（approximated）**：卡牌可对战但个别副作用未建模（例：「附能并抽 1」只实现了附能）。此类差异逐卡记录在卡池条目的 `note` 字段。

## 4. 修订流程

1. 实现/放弃某机制 → 先改 `packages/agent/agent/effects.py`（识别）与 `packages/engine/engine/battle.py`（派发）；
2. 重新生成：`python scripts/coverage_backlog.py` 与 `python scripts/coverage_backlog.py --registry`；
3. 清单变更随代码一起提交（保证披露与实现同步，不出现「清单说支持、实现不支持」）。

---

*本清单纯声明「本引擎的边界」，不构成对官方规则的完整描述；以官方规则与卡面原文为准（合规 6.1）。*
