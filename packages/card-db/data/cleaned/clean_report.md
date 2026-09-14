# 卡牌数据集清洗报告

- 数据源：`pokemon-tcg-ai-battle-challenge-strategy.zip`（EN + JP Card Data.csv）
- 产物：`data/cleaned/cards_challenge.json`（入库源）· `data/db/pokelab.db`（cards 表）
- 合规：仅清洗与存储**卡名 / 数值 / 规则文本**，**不使用官方卡图**（C2）；效果文本为功能机制引用，非美术素材。

## 1. 清洗结果汇总

| 项 | 数值 |
|---|---|
| 输入数据行 | 2022 |
| **聚合为卡牌数** | **1267** |
| 招式总数 | 1559 |
| 特性总数 | 223 |
| 多招式卡（≥2 招式） | 499 |
| 无招式卡（训练家/能量/规则） | 205 |
| 引擎可直接对战（已建模） | 36 |
| 回填中文名 | 36 |

### 卡牌构成

| 超类型 | 数量 |
|---|---|
| Pokemon | 1056 |
| Trainer | 191 |
| Energy | 20 |

| 子类型 | 数量 |
|---|---|
| Basic | 595 |
| Stage 1 | 345 |
| Stage 2 | 116 |
| Item | 77 |
| Supporter | 61 |
| Pokémon Tool | 27 |
| Stadium | 26 |
| Special Energy | 12 |
| Basic Energy | 8 |

| 系列（Top 12） | 数量 |
|---|---|
| DRI | 178 |
| MEG | 126 |
| ASC | 124 |
| TWM | 95 |
| SSP | 95 |
| PFL | 94 |
| JTG | 85 |
| WHT | 85 |
| BLK | 84 |
| POR | 81 |
| TEF | 68 |
| SCR | 53 |

### 规范化动作统计

| 伤害类型 | 数量 | 说明 |
|---|---|---|
| fixed | 1173 | 固定伤害 |
| multiplier | 110 | `N×` 每份 N |
| variable | 2 | `-N` 变值（含减伤/条件） |
| other | 0 | 非常规写法（保留 raw） |

费用符号（归一后）：colorless×1464、fighting×218、water×218、darkness×207、psychic×197、grass×193、fire×187、lightning×137、metal×123

## 2. 已归一化的项（清洗动作）

| 原数据 | 归一为 |
|---|---|
| `n/a` / 空串 / `-` | `null`（不再伪装成字符串） |
| `●`、`{G}`… | 有序能量代码表（`colorless` / `grass` …）+ `cost_total` |
| `{A}` | `any`（任意能量，如 Prism/Legacy Energy） |
| `{Team Rocket}` | `team_rocket` |
| `20×` | `{base:20, kind:'multiplier'}` |
| `-120` | `{base:120, kind:'variable'}`（原值保留在 `raw`） |
| `[Ability] X` | 拆入 `abilities[]`（与招式分离） |
| `[Tera]` 等 | 归入 `other_text`（规则说明，非招式） |
| 同 Card ID 多行 | 聚合为一张卡 |
| EN + JP | `name`(EN 数据键) + `name_ja` |
| 引擎自撰卡池命中 | 回填 `name_zh` + `engine_ready=true` |

### 名称规范差异（重要）

- 数据集内撇号混用：弯撇号 `’` **53 张** vs 直撇号 `'` 其余 —— 统一由 `name_key`（小写 + 撇号归一 + 去符号）承担匹配，**原始卡名保持不变**；
- 基础能量命名差异：数据集用 `Basic {P} Energy`，既有卡池用 `Basic Psychic Energy` —— 由 `aliases[]` 生成英文全名别名（共 8 张带别名）；其中 7 张**靠别名**匹配上引擎卡池；
- 数据集为「AI Battle Challenge」**卡池子集**（1267 张）：`Professor's Research` / `Counter Catcher` / `Earthen Vessel` / `Nest Ball` 等**不在其中**，既有卡表中这些卡仍以引擎自撰数据为准（见 §3 诚实标注）。

## 3. 质量问题清单（已如实记录，未静默丢弃）

- 宝可梦缺 HP：0
- 宝可梦缺属性：0
- 缺日文名：0
- 无招式且无效果文本：8 —— Basic {G} Energy, Basic {R} Energy, Basic {W} Energy, Basic {L} Energy, Basic {P} Energy, Basic {F} Energy…
- 未知能量符号：无
- (卡名,系列,编号) 重复：0
- 进化卡缺「进化前」：0
- 引擎自撰卡池中**不在本数据集**的卡：7 —— Clefairy ex, Counter Catcher, Earthen Vessel, Iono, Nest Ball, Ogerpon ex, Professor's Research

## 4. 诚实标注

- 本数据集提供**卡面机制文本**（官方英文原文），项目**不使用官方卡图**（C2 红线）；
- 引擎未建模的卡（1231 张）**可浏览/可组卡但不可对战**，由 `engine_ready` 标记，UI 与导入校验据此显示灰名单；
- 中文名仅回填自引擎自撰卡池（36 张）；其余卡的中文名属**待译项**，不做机器硬译以免误导。

