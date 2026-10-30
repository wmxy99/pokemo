# PokéLab AI · 未建模动作差异透明清单（**实现队列**）

> **性质**：本清单是项目对外的**规范性披露**（合规红线 **C5 差异透明**），同时也是**实现队列**。
> ⚠ 2026-10-26 起：**已取消「永久不建模」豁免** —— 下列条目全部纳入实现范围，按 S1–S6 机制族分批实现。在实现完成前，对应卡牌仍如实标记为 `engine_ready=false`，**绝不静默降级或假装支持**（披露义务不因「要做」而消失）。

- 生成时间：2026-10-30 16:53:58
- 数据源：`packages/card-db/data/db/pokelab.db`（解析后端 `rule（离线确定性基线）`）
- 全库 1267 张 · 未建模动作条目 **259** 条 · 已映射 tag 1334 条
- **引擎可对战卡（严格口径，与 `/cards/stats` 同源的 `engine_ready`）：974 张**
- 解析层面「无未建模动作」卡：869 张 —— **口径不同**：它只表示「解析链未产生未建模动作」，**不含**库内 `engine_ready` 标志的其它条件（近似/fidelity 等）；两者相差的部分即「解析通过但标志仍不可对战」的卡。**对外引用统一以严格口径（上一行）为准。**

## 批次图例

| 批次 | 含义 | 处置 |
|---|---|---|
| **B4** | 需新增引擎机制（条件伤害修正 / 抛币 / 伤害减免与免疫 / 指示物与计数 / 静态封锁） | **P1 批次**，按清单推进；当前不实现 |
| **B5** | 静态修正 / 道具 / 场地 / 特殊能量 / 元游戏与信息类（原「永久不建模」，现全部纳入实现范围） | **实现队列**：S1 静态修正层 → S2 道具 → S3 场地 → S4 特殊能量 → S5 元游戏 → S6 待拆解 |

## 1. 明细（按条目数降序）

| 批次 | 子类 | 动作类型 | 条目 | 影响卡数 | 原因 |
|---|---|---|---:|---:|---|
| B4 | coin_flip | `rule` | 6 | 6 | 静态/未建模（英文） |
| B4 | extra_damage_target | `damage` | 2 | 2 | 附加伤害目标未建模（itself） |
| B4 | static_lock | `rule` | 2 | 2 | 静态规则修改（弱点/抗性/伤害减免/限制） |
| B4 | scaling_damage | `rule` | 1 | 1 | 条件伤害缩放：计数源未识别（damage counter you placed in t） |
| B4 | static_lock | `rule` | 1 | 1 | 静态/未建模（英文） |
| B4 | extra_damage_target | `damage` | 1 | 1 | 附加伤害目标未建模（1 of your opponent’s Benched Pokémon {ex} or Benched Pokémon） |
| B4 | scaling_damage | `rule` | 1 | 1 | 条件伤害缩放：计数源未识别（Ancient card in your discard p） |
| B4 | extra_damage_target | `damage` | 1 | 1 | 附加伤害目标未建模（1 of your opponent’s Benched Pokémon） |
| B4 | coin_flip | `rule` | 1 | 1 | 状态词非主动施加（免疫/追加伤害/换位触发，B4） |
| B4 | type:condition | `condition` | 1 | 1 | 进化触发特性的前置条件未建模（B4） |
| B4 | extra_damage_target | `damage` | 1 | 1 | 附加伤害目标未建模（each of your opponent’s Pokémon {ex} and Pokémon {V}） |
| B4 | extra_damage_target | `damage` | 1 | 1 | 附加伤害目标未建模（each of your opponent’s Pokémon {ex}） |
| B4 | scaling_damage | `rule` | 1 | 1 | 条件伤害缩放：计数源未识别（{L} Energy attached to all of ） |
| B4 | extra_damage_target | `damage` | 1 | 1 | 附加伤害目标未建模（1 of your opponent’s Benched Pokémon {ex}） |
| B4 | coin_flip | `rule` | 1 | 1 | 抛币分支内的能量回手（需 coin 嵌套，B4） |
| B4 | coin_flip | `rule` | 1 | 1 | 受击/受击历史从句（is|was damaged by an attack）需引擎 on_damaged 或回合级历史（B4） |
| B4 | static_lock | `rule` | 1 | 1 | 静态伤害增减（需引擎静态修正层，B4） |
| B4 | extra_damage_target | `damage` | 1 | 1 | 附加伤害目标未建模（that Pokémon） |
| B4 | coin_flip | `rule` | 1 | 1 | 放置伤害指示物：目标/条件未识别（1 of your opponent’s Pokémon） |
| B4 | coin_flip | `rule` | 1 | 1 | 抛币分支内的抽牌（需 coin 嵌套，B4） |
| B4 | static_lock | `rule` | 1 | 1 | 打出前置条件（You can use this card only if …）未建模（B5） |
| B5 | S6 | `rule` | 61 | 61 | 静态/未建模（英文） |
| B5 | S5 | `rule` | 16 | 16 | 静态/未建模（英文） |
| B5 | S2 | `rule` | 7 | 7 | 静态/未建模（英文） |
| B5 | S6 | `rule` | 5 | 5 | 「any number of」自由数量（弃牌/附着/指示物移动，需交互层，B4） |
| B5 | S6 | `rule` | 3 | 3 | 条件伤害缩放：条件未识别（引擎无该条件） |
| B5 | S6 | `rule` | 3 | 3 | 无视目标身上效果（需效果免疫/清除层，B5） |
| B5 | S6 | `rule` | 3 | 3 | 静态伤害增减（需引擎静态修正层，B4） |
| B5 | S6 | `rule` | 3 | 3 | 手牌禁打（Supporter/Stadium/ACE SPEC/特性宝可梦等，需对手手牌禁用层，B5） |
| B5 | S2 | `rule` | 2 | 2 | 手牌禁打（Supporter/Stadium/ACE SPEC/特性宝可梦等，需对手手牌禁用层，B5） |
| B5 | S6 | `rule` | 2 | 2 | 首回合招式使用限制（需招式静态条件层，B5） |
| B5 | S1 | `rule` | 2 | 2 | 静态/未建模（英文） |
| B5 | S6 | `rule` | 2 | 2 | 状态词非主动施加（免疫/追加伤害/换位触发，B4） |
| B5 | S5 | `rule` | 2 | 2 | 打出前置条件（You can use this card only if …）未建模（B5） |
| B5 | S3 | `rule` | 2 | 2 | 场地形态未建模（S3 未覆盖） |
| B5 | S4 | `rule` | 1 | 1 | 静态/未建模（英文） |
| B5 | S6 | `rule` | 1 | 1 | 未建模的招式失败门（条件不满足则此招式无效，B5） |
| B5 | S6 | `rule` | 1 | 1 | 条件伤害缩放：计数源未识别（of your opponent's Pokémon {ex） |
| B5 | S1 | `rule` | 1 | 1 | 静态规则修改（弱点/抗性/伤害减免/限制） |
| B5 | S6 | `rule` | 1 | 1 | 静态规则修改（弱点/抗性/伤害减免/限制） |
| B5 | S3 | `rule` | 1 | 1 | 静态/未建模（英文） |
| B5 | S6 | `rule` | 1 | 1 | 抛币分支内的状态：多币条件/多状态/非对手战斗位，需扩展 coin 子效果（B4） |
| B5 | S3 | `rule` | 1 | 1 | 手牌禁打（Supporter/Stadium/ACE SPEC/特性宝可梦等，需对手手牌禁用层，B5） |
| B5 | S6 | `rule` | 1 | 1 | 受击/受击历史从句（is|was damaged by an attack）需引擎 on_damaged 或回合级历史（B4） |
| B5 | S2 | `rule` | 1 | 1 | 条件伤害缩放：计数源未识别（Pokémon Tool attached to all P） |
| B5 | S6 | `rule` | 1 | 1 | 放置伤害指示物：目标/条件未识别（1 of your opponent’s Benched Pokémon） |
| B5 | S6 | `rule` | 1 | 1 | 放置伤害指示物：目标/条件未识别（that Pokémon） |
| B5 | S6 | `rule` | 1 | 1 | 能量移动的源/目标未识别（B4） |
| B5 | S6 | `rule` | 1 | 1 | 条件伤害缩放：计数源未识别（card you revealed in this way） |
| B5 | S4 | `rule` | 1 | 1 | 弃置**特殊**能量（引擎不区分基本/特殊能量，B4） |
| B5 | S2 | `rule` | 1 | 1 | 同时弃道具与**特殊**能量（引擎不区分基本/特殊，B4） |
| B5 | S6 | `rule` | 1 | 1 | 回复范围/限定不可精确表达（B4） |
| B5 | S2 | `rule` | 1 | 1 | 打出前置条件（You can use this card only if …）未建模（B5） |
| B5 | S2 | `rule` | 1 | 1 | 受击/受击历史从句（is|was damaged by an attack）需引擎 on_damaged 或回合级历史（B4） |
| B5 | S2 | `rule` | 1 | 1 | 静态伤害增减（需引擎静态修正层，B4） |
| B5 | S6 | `rule` | 1 | 1 | 全量回复（范围/后续不可精确表达 · B4） |

## 2. 代表性卡面（逐子类抽样，便于核对）

**B4 · coin_flip**（6 条）

- `Sylveon`（仙子伊布）：Flip a coin. If heads, choose 1 of your opponent’s Benched Pokémon. Shuffle that Pokémon and all attached cards into their deck.
- `Ethan's Sudowoodo`（阿响的树才怪）：Flip a coin. If heads, choose 1 of your opponent’s Active Pokémon’s attacks and use it as this attack.
- `Shiftry`（狡猾天狗）：Flip a coin. If heads, choose 1 of your opponent’s Pokémon. Shuffle that Pokémon and all attached cards into their deck.

**B4 · extra_damage_target**（2 条）

- `Gurdurr`（铁骨土人）：You may do 30 more damage. If you do, this Pokémon also does 30 damage to itself.
- `Dondozo ex`（吃吼霸 ex）：You may do 120 more damage. If you do, this Pokémon also does 50 damage to itself.

**B4 · static_lock**（2 条）

- `Iron Defender`（铁壁防御）：During your opponent’s next turn, all of your {M} Pokémon take 30 less damage from attacks from your opponent’s Pokémon (after applying Weakness and Resistance)
- `Acerola's Mischief`（阿塞萝拉的恶作剧）：You can use this card only if your opponent has 2 or fewer Prize cards remaining.  Choose 1 of your Pokémon in play. During your opponent’s next turn, prevent a

**B4 · scaling_damage**（1 条）

- `Walking Wake`（波荡水）：Put up to 9 damage counters on this Pokémon. This attack does 20 damage for each damage counter you placed in this way.

**B4 · static_lock**（1 条）

- `Iron Thorns ex`（铁荆棘 ex）：As long as this Pokémon is in the Active Spot, Pokémon with a Rule Box in play (both yours and your opponent’s) have no Abilities, except for Future Pokémon. (P

**B4 · extra_damage_target**（1 条）

- `Shaymin`（谢米）：This attack does 60 damage to 1 of your opponent’s Benched Pokémon {ex} or Benched Pokémon {V}. (Don’t apply Weakness and Resistance for Benched Pokémon.)

**B4 · scaling_damage**（1 条）

- `Roaring Moon`（轰鸣月）：This attack does 10 more damage for each Ancient card in your discard pile.

**B4 · extra_damage_target**（1 条）

- `Wellspring Mask Ogerpon ex`（厄诡椪（井之面具）ex）：You may shuffle 3 Energy attached to this Pokémon into your deck. If you do, this attack also does 120 damage to 1 of your opponent’s Benched Pokémon. (Don’t ap

**B4 · coin_flip**（1 条）

- `Cradily`（摇篮百合）：Once during your turn, you may flip a coin. If heads, choose Burned, Confused, or Poisoned. Your opponent’s Active Pokémon is now affected by that Special Condi

**B4 · type:condition**（1 条）

- `Noctowl`（猫头夜鹰）：Once during your turn, when you play this Pokémon from your hand to evolve 1 of your Pokémon, if you have any Tera Pokémon in play, you may search your deck for

**B4 · extra_damage_target**（1 条）

- `Flygon ex`（沙漠蜻蜓 ex）：This attack does 100 damage to each of your opponent’s Pokémon {ex} and Pokémon {V}. This attack’s damage isn’t affected by Weakness or Resistance.

**B4 · extra_damage_target**（1 条）

- `Vaporeon ex`（水伊布 ex）：This attack does 60 damage to each of your opponent’s Pokémon {ex}. This attack’s damage isn’t affected by Weakness or Resistance.

**B4 · scaling_damage**（1 条）

- `Iono’s Voltorb`（奇树的霹雳电球）：This attack does 20 more damage for each {L} Energy attached to all of your Iono’s Pokémon.

**B4 · extra_damage_target**（1 条）

- `Zeraora`（捷拉奥拉）：Discard all Energy from this Pokémon, and this attack does 210 damage to 1 of your opponent’s Benched Pokémon {ex}. (Don’t apply Weakness and Resistance for Ben

**B4 · coin_flip**（1 条）

- `Dustox`（毒粉蛾）：Once during your turn, you may use this Ability. Flip a coin. If heads, put an Energy attached to your opponent’s Active Pokémon into their hand.

**B4 · coin_flip**（1 条）

- `Fezandipiti`（吉雉鸡）：If this Pokémon has any {D} Energy attached and is damaged by an attack, flip a coin. If heads, prevent that damage.

**B4 · static_lock**（1 条）

- `Aurorus`（冰雪巨龙）：All of your Pokémon that have any {W} Energy attached take 50 less damage from attacks from your opponent’s Pokémon (after applying Weakness and Resistance). Th

**B4 · extra_damage_target**（1 条）

- `Mega Zygarde ex`（超级基格尔德 ex）：For each of your opponent’s Pokémon, flip a coin. If heads, this attack does 150 damage to that Pokémon. (Don’t apply Weakness and Resistance for Benched Pokémo

**B4 · coin_flip**（1 条）

- `Team Rocket's Venture Bomb`（火箭队的冒险炸弹）：Flip a coin. If heads, put 2 damage counters on 1 of your opponent’s Pokémon. If tails, put 2 damage counters on your Active Pokémon.

**B4 · coin_flip**（1 条）

- `Harlequin`（小丑）：Each player shuffles their hand into their deck. Then, flip a coin. If heads, you draw 5 cards, and your opponent draws 3 cards. If tails, you draw 3 cards, and

**B4 · static_lock**（1 条）

- `Acerola's Mischief`（阿塞萝拉的恶作剧）：You can use this card only if your opponent has 2 or fewer Prize cards remaining.  Choose 1 of your Pokémon in play. During your opponent’s next turn, prevent a

**B5 · S6**（61 条）

- `Shiftry`（狡猾天狗）：Choose 3 of your opponent’s Benched Pokémon. If you do, shuffle all of your opponent’s Benched Pokémon that you didn’t choose, and all cards attached to those P
- `Dipplin`（裹蜜虫）：If Festival Grounds is in play, this Pokémon may use an attack it has twice. If the first attack Knocks Out your opponent’s Active Pokémon, you may attack again
- `Goldeen`（角金鱼）：If Festival Grounds is in play, this Pokémon may use an attack it has twice. If the first attack Knocks Out your opponent’s Active Pokémon, you may attack again

**B5 · S5**（16 条）

- `Hoothoot`（咕咕）：Your opponent reveals their hand.
- `Munkidori ex`（愿增猿 ex）：If this Pokémon is Knocked Out by damage from an attack from your opponent’s Pokémon, and if you have any Pecharunt {ex} in play, your opponent takes 1 fewer Pr
- `Espurr`（妙喵）：Your opponent reveals their hand.

**B5 · S2**（7 条）

- `Rotom`（洛托姆）：Your opponent reveals their hand. Discard all Item cards and Pokémon Tool cards you find there.
- `Rotom ex`（洛托姆 ex）：Each of your Pokémon that has "Rotom" in its name may have up to 2 Pokémon Tool cards attached. If this Ability goes away, discard Pokémon Tools from those Poké
- `Heavy Baton`（沉重接力棒）：If the Pokémon this card is attached to has a Retreat Cost of exactly 4, is in the Active Spot, and is Knocked Out by damage from an attack from your opponent’s

**B5 · S6**（5 条）

- `Lillie’s Ribombee`（莉莉艾的蝶结萌虻）：When you play this Pokémon from your hand to evolve 1 of your Pokémon during your turn, you may have your opponent reveal their hand and you put any number of B
- `Team Rocket's Honchkrow`（火箭队的乌鸦头头）：You may discard any number of Supporter cards that have “Team Rocket” in their name from your hand, and this attack does 60 damage for each card you discarded i
- `Doublade`（双剑鞘）：Reveal any number of Honedge, Doublade, and Aegislash from your hand, and this attack does 60 damage for each card you revealed in this way.

**B5 · S6**（3 条）

- `Team Rocket's Kangaskhan ex`（火箭队的袋兽 ex）：If you played a Supporter card that has “Team Rocket” in its name from your hand during this turn, this attack does 100 more damage.
- `Galvantula ex`（电蜘蛛 ex）：If your opponent’s Active Pokémon is a Pokémon {ex} or Pokémon {V}, this attack does 110 more damage.
- `Pangoro`（流氓熊猫）：If any of your Benched Pancham have any damage counters on them, this attack does 120 more damage.

**B5 · S6**（3 条）

- `Iron Crown ex`（铁头壳 ex）：This attack does 50 damage to 2 of your opponent’s Pokémon. This attack’s damage isn’t affected by Weakness or Resistance, or by any effects on those Pokémon.
- `Cornerstone Mask Ogerpon ex`（础石面具厄诡椪 ex）：This attack’s damage isn’t affected by Weakness or Resistance, or by any effects on your opponent’s Active Pokémon.
- `Mega Starmie ex`（超级宝石海星 ex）：This attack’s damage isn’t affected by Weakness or Resistance, or by any effects on your opponent’s Active Pokémon.

**B5 · S6**（3 条）

- `Iron Crown ex`（铁头壳 ex）：Attacks used by your Future Pokémon, except any Iron Crown {ex}, do 20 more damage to your opponent’s Active Pokémon (before applying Weakness and Resistance).
- `Premium Power Pro`（高级力量强化）：During this turn, attacks used by your {F} Pokémon do 30 more damage to your opponent’s Active Pokémon (before applying Weakness and Resistance).
- `Black Belt’s Training`（空手道的锻炼）：During this turn, attacks used by your Pokémon do 40 more damage to your opponent’s Active Pokémon {ex} (before applying Weakness and Resistance).

**B5 · S6**（3 条）

- `Tyranitar`（班基拉斯）：As long as this Pokémon is in the Active Spot, your opponent can’t play any Item cards from their hand.
- `Team Rocket's Arbok`（火箭队的阿柏怪）：As long as this Pokémon is in the Active Spot, your opponent can’t play any Pokémon that has an Ability from their hand, except for Team Rocket’s Pokémon.
- `Scream Tail ex`（吼叫尾 ex）：You can use this attack only if you go second, and only during your first turn. Your opponent can’t play any Supporter cards from their hand during their next t

**B5 · S2**（2 条）

- `Genesect`（盖诺赛克特）：If this Pokémon has a Pokémon Tool attached, your opponent can’t play any {ACE SPEC} cards from their hand.
- `Jellicent ex`（胖嘟嘟 ex）：As long as this Pokémon is in the Active Spot, your opponent can’t play any Item cards or Pokémon Tool cards from their hand.

**B5 · S6**（2 条）

- `Terapagos ex`（太乐巴戈斯 ex）：If you go second, you can’t use this attack during your first turn. This attack does 30 damage for each of your Benched Pokémon.
- `Scream Tail ex`（吼叫尾 ex）：You can use this attack only if you go second, and only during your first turn. Your opponent can’t play any Supporter cards from their hand during their next t

**B5 · S1**（2 条）

- `Latias ex`（拉帝亚斯 ex）：Your Basic Pokémon in play have no Retreat Cost.
- `Ludicolo`（乐天河童）：All of your Pokémon in play get +40 HP. The effect of Vibrant Dance doesn’t stack.

**B5 · S6**（2 条）

- `Hoothoot`（咕咕）：This Pokémon can’t be Asleep.
- `Mismagius ex`（梦妖魔 ex）：As long as this Pokémon is in the Active Spot, whenever your opponent’s Active Pokémon moves to the Bench during their turn, their new Active Pokémon is now Con

**B5 · S5**（2 条）

- `Briar`（白蕾）：You can use this card only if your opponent has exactly 2 Prize cards remaining.  During this turn, if your opponent’s Active Pokémon is Knocked Out by damage f
- `Anthea & Concordia`（安西娅与康可迪亚）：You can use this card only if you have N’s Darmanitan, N’s Zoroark {ex}, N’s Vanilluxe, N’s Klinklang, N’s Reshiram, and N’s Zekrom in play.  During this turn, 

**B5 · S3**（2 条）

- `Dizzying Valley`（眩晕山谷）：Confused Pokémon (both yours and your opponent’s) don’t recover from that Special Condition when they evolve or devolve.
- `Nighttime Mine`（夜间矿山）：Attacks used by each Tera Pokémon in play (both yours and your opponent’s) cost {C} more.

**B5 · S4**（1 条）

- `Boomerang Energy`（回旋镖能量）：As long as this card is attached to a Pokémon, it provides {C} Energy.  If this card is discarded by an effect of an attack used by the Pokémon this card is att

**B5 · S6**（1 条）

- `Decidueye`（狙射树枭）：Discard a Basic {G} Energy card from your hand. If you can’t, this attack does nothing.

**B5 · S6**（1 条）

- `Zoroark`（索罗亚克）：This attack does 60 damage for each of your opponent’s Pokémon {ex} and Pokémon {V} in play.

**B5 · S1**（1 条）

- `Archaludon ex`（铝钢桥龙 ex）：During your opponent’s next turn, this Pokémon has no Weakness.

**B5 · S6**（1 条）

- `Lillie’s Clefairy ex`（莉莉艾的皮皮 ex）：The Weakness of each of your opponent’s {N} Pokémon in play is now {P}. (Apply Weakness as ×2.)

**B5 · S3**（1 条）

- `Cetitan ex`（浩大鲸 ex）：Whenever your opponent plays an Item or Supporter card from their hand, prevent all effects of that card done to this Pokémon.

**B5 · S6**（1 条）

- `Vanilluxe`（双倍多多冰）：Flip 2 coins. This attack does 90 damage for each heads. If either of them is heads, your opponent’s Active Pokémon is now Paralyzed.

**B5 · S3**（1 条）

- `Chi-Yu`（古玉鱼）：If your opponent has a Stadium in play, discard it. If you do, your opponent can’t play any Stadium cards from their hand during their next turn.

**B5 · S6**（1 条）

- `Mega Heracross ex`（超级赫拉克罗斯 ex）：If this Pokémon was damaged by an attack during your opponent’s last turn, this attack does that much more damage.

**B5 · S2**（1 条）

- `Bronzong`（青铜钟）：This attack does 40 damage for each Pokémon Tool attached to all Pokémon.

**B5 · S6**（1 条）

- `Mismagius`（梦妖魔）：If your opponent’s Active Pokémon is affected by a Special Condition, place 6 damage counters on 1 of your opponent’s Benched Pokémon.

**B5 · S6**（1 条）

- `Team Rocket's Dugtrio`（火箭队的三地鼠）：Whenever your opponent’s Active Pokémon moves to the Bench during their turn, place 2 damage counters on that Pokémon.

**B5 · S6**（1 条）

- `Azumarill ex`（玛力露丽 ex）：As often as you like during your turn, you may use this Ability. Move an Energy from 1 of your other Pokémon to this Pokémon.

**B5 · S6**（1 条）

- `Doublade`（双剑鞘）：Reveal any number of Honedge, Doublade, and Aegislash from your hand, and this attack does 60 damage for each card you revealed in this way.

**B5 · S4**（1 条）

- `Enhanced Hammer`（增强锤）：Discard a Special Energy from 1 of your opponent’s Pokémon.

**B5 · S2**（1 条）

- `Megaton Blower`（百万吨吹风机）：Discard all Pokémon Tools and Special Energy from all of your opponent’s Pokémon, and discard a Stadium in play.

**B5 · S6**（1 条）

- `Arven's Sandwich`（派帕的三明治）：Heal 30 damage from your Active Pokémon. If that Pokémon is an Arven’s Pokémon, heal 100 damage from it instead.

**B5 · S2**（1 条）

- `Blowtorch`（喷灯）：You can use this card only if you discard a Basic {R} Energy card from your hand.  Discard a Pokémon Tool or Special Energy card from 1 of your opponent’s Pokém

**B5 · S2**（1 条）

- `Team Rocket’s Hypnotizer`（火箭队的催眠器）：If the Team Rocket’s Pokémon this card is attached to is in the Active Spot and is damaged by an attack from your opponent’s Pokémon (even if this Team Rocket’s

**B5 · S2**（1 条）

- `Hop’s Choice Band`（赫普的讲究头带）：Attacks used by the Hop’s Pokémon this card is attached to cost {C} less and do 30 more damage to your opponent’s Active Pokémon (before applying Weakness and R

**B5 · S6**（1 条）

- `Bianca’s Devotion`（白露的奉献）：Heal all damage from 1 of your Pokémon that has 30 HP or less remaining.

## 3. 与「已建模」的边界（避免误读）

- **已建模**：招式的伤害/费用/状态/放置指示物/抽弃/检索/回收/换位/附能/移动指示物/抛币/条件与计数伤害修正（含**带参能量条件**）/护盾与被动免疫/**反伤（`self:N`）**/**选定与全体目标的附加伤害（`snipe:<scope>:<per>:<count>`）**/**磨牌（`mill:N[:opp]`）**；特性的检视牌库顶（`recon:N`）、**全牌库检索（`search_pokemon|trainer|any|energy:N`）**、每回合抽牌、附能、被击倒抽牌、进化触发、弱点改写、后备免疫；训练家的检索/回收（`recover:N:pokemon|energy|any|trainer|supporter|item`）/换位/跳阶进化等（完整清单见 `packages/engine/engine/data/cards_real.json` → `meta.engine_tags`）。
- **未建模**：本清单 §1 全部条目。受影响卡牌**可浏览、可组卡、不可对战**（标为 `approximated` 时以近似规则参战）。
- **近似（approximated）**：卡牌可对战但个别副作用未建模（例：「附能并抽 1」只实现了附能）。此类差异逐卡记录在卡池条目的 `note` 字段。

## 4. 修订流程

1. 实现/放弃某机制 → 先改 `packages/agent/agent/effects.py`（识别）与 `packages/engine/engine/battle.py`（派发）；
2. 重新生成：`python scripts/coverage_backlog.py` 与 `python scripts/coverage_backlog.py --registry`；
3. 清单变更随代码一起提交（保证披露与实现同步，不出现「清单说支持、实现不支持」）。

---

*本清单纯声明「本引擎的边界」，不构成对官方规则的完整描述；以官方规则与卡面原文为准（合规 6.1）。*
