# PokéLab AI · 未建模动作差异透明清单（**实现队列**）

> **性质**：本清单是项目对外的**规范性披露**（合规红线 **C5 差异透明**），同时也是**实现队列**。
> ⚠ 2026-10-26 起：**已取消「永久不建模」豁免** —— 下列条目全部纳入实现范围，按 S1–S6 机制族分批实现。在实现完成前，对应卡牌仍如实标记为 `engine_ready=false`，**绝不静默降级或假装支持**（披露义务不因「要做」而消失）。

- 生成时间：2026-11-08 22:31:43
- 数据源：`packages/card-db/data/db/pokelab.db`（解析后端 `rule（离线确定性基线）`）
- 全库 1267 张 · 未建模动作条目 **18** 条 · 已映射 tag 1563 条
- **引擎可对战卡（严格口径，与 `/cards/stats` 同源的 `engine_ready`）：1249 张**
- 解析层面「无未建模动作」卡：1101 张 —— **口径不同**：它只表示「解析链未产生未建模动作」，**不含**库内 `engine_ready` 标志的其它条件（近似/fidelity 等）；两者相差的部分即「解析通过但标志仍不可对战」的卡。**对外引用统一以严格口径（上一行）为准。**

## 批次图例

| 批次 | 含义 | 处置 |
|---|---|---|
| **B4** | 需新增引擎机制（条件伤害修正 / 抛币 / 伤害减免与免疫 / 指示物与计数 / 静态封锁） | **P1 批次**，按清单推进；当前不实现 |
| **B5** | 静态修正 / 道具 / 场地 / 特殊能量 / 元游戏与信息类（原「永久不建模」，现全部纳入实现范围） | **实现队列**：S1 静态修正层 → S2 道具 → S3 场地 → S4 特殊能量 → S5 元游戏 → S6 待拆解 |

## 1. 明细（按条目数降序）

| 批次 | 子类 | 动作类型 | 条目 | 影响卡数 | 原因 |
|---|---|---|---:|---:|---|
| B4 | extra_damage_target | `damage` | 1 | 1 | 附加伤害目标未建模（1 of your opponent’s Benched Pokémon） |
| B5 | S6 | `rule` | 7 | 7 | 静态/未建模（英文） |
| B5 | S6 | `rule` | 1 | 1 | 静态规则修改（弱点/抗性/伤害减免/限制） |
| B5 | S6 | `rule` | 1 | 1 | 「any number of」自由数量（弃牌/附着/指示物移动，需交互层，B4） |
| B5 | S5 | `rule` | 1 | 1 | 静态/未建模（英文） |
| B5 | S2 | `rule` | 1 | 1 | 同时弃道具与**特殊**能量（引擎不区分基本/特殊，B4） |
| B5 | S2 | `rule` | 1 | 1 | 静态伤害增减（需引擎静态修正层，B4） |
| B5 | S2 | `rule` | 1 | 1 | 静态/未建模（英文） |

## 2. 代表性卡面（逐子类抽样，便于核对）

**B4 · extra_damage_target**（1 条）

- `Wellspring Mask Ogerpon ex`（厄诡椪（井之面具）ex）：You may shuffle 3 Energy attached to this Pokémon into your deck. If you do, this attack also does 120 damage to 1 of your opponent’s Benched Pokémon. (Don’t ap

**B5 · S6**（7 条）

- `Milotic`（美纳斯）：Your opponent’s Pokémon in play and all attached cards can’t be put into your opponent’s hand.
- `Revavroom ex`（普隆隆姆 ex）：Discard this Pokémon and all attached cards.
- `Klinklang`（齿轮怪）：Once during your turn, if this Pokémon is in your hand and your opponent has any Stage 2 Pokémon in play, you may put this Pokémon onto your Bench.

**B5 · S6**（1 条）

- `Lillie’s Clefairy ex`（莉莉艾的皮皮 ex）：The Weakness of each of your opponent’s {N} Pokémon in play is now {P}. (Apply Weakness as ×2.)

**B5 · S6**（1 条）

- `Lillie’s Ribombee`（莉莉艾的蝶结萌虻）：When you play this Pokémon from your hand to evolve 1 of your Pokémon during your turn, you may have your opponent reveal their hand and you put any number of B

**B5 · S5**（1 条）

- `Mandibuzz`（秃鹰娜）：Once during your turn, you may use this Ability. Your opponent reveals their hand, and you put a Basic Pokémon with 70 HP or less that you find there onto your 

**B5 · S2**（1 条）

- `Megaton Blower`（百万吨吹风机）：Discard all Pokémon Tools and Special Energy from all of your opponent's Pokémon, and discard a Stadium in play.

**B5 · S2**（1 条）

- `Hop’s Choice Band`（赫普的讲究头带）：Attacks used by the Hop's Pokémon this card is attached to cost {C} less and do 30 more damage to your opponent's Active Pokémon (before applying Weakness and R

**B5 · S2**（1 条）

- `Core Memory`（核心记忆）：The Mega Zygarde {ex} this card is attached to can use the attack on this card. (You still need the necessary Energy to use this attack.)

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
