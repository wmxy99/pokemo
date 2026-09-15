# K2 卡池解析达标率 · 本地模型实跑

- 生成时间：2026-09-15 12:12:21
- 模型：本地 Qwen3-4B · **8bit**（`POKELAB_LLM_BASE` → 进程内服务）
- 卡池条目总数：328；样本：**60**（seed=42，分层抽样）
- K2 目标：≥85%（PRD）

## 1. 主结果（**口径见文末**）

| 分类 | 条数 | 占比 |
|---|---:|---:|
| **A 解析失败**（LLM 空返回或有 errors） | 2 | 3.3% |
| **B 解析成功但动作未建模** | 16 | 26.7% |
| **C 解析成功且完全映射** | 42 | 70.0% |

| 指标 | 数值 | 95% 置信区间 |
|---|---:|---|
| **LLM 可用率**（B+C） | **96.7%** | [88.6%, 99.1%] |
| 完全映射率（C） | 70.0% | [57.5%, 80.1%] |
| rule 兜底率（LLM 空返回） | 1.7% | — |
| 带 schema errors 的比例 | 1.7% | — |

**对照**：rule 后端在**同一样本**上的完全映射率 = 98.3%；**引擎 tag 一致率**（llm vs rule，可比 31 条）= 51.6%（比的是**映射后的引擎 tag**，不是原始动作 type——两个后端的动作命名空间本就不同，比原始 type 会得到很低且无意义的数字）

**K2 判定**：PASS ✅（96.7% vs 门槛 85%）

耗时：41.4s/条 → 全量 328 条约 3.8 小时

### 按语料语言分组（**重要**）

K2 卡池 328 条中 **294 条（89.6%）是纯英文**（扩展池无中文效果文本，`display_desc` 回退英文），仅 34 条中文；而提示词是中文撰写的。两者难度不同，故分组统计：

| 语言 | n | 可用率 | 95% CI | A 失败 | B 未建模 | C 完全映射 |
|---|---:|---:|---|---:|---:|---:|
| 中文 | 8 | **100.0%** | [67.6%, 100.0%] | 0 | 3 | 5 |
| 英文 | 52 | **96.2%** | [87.0%, 98.9%] | 2 | 13 | 37 |

## 2. A 类失败明细（= 解析链待补清单）

**错误类型分布**（按 error 条目计，一条记录可能有多条）：

| 类型 | 条数 |
|---|---:|
| 枚举/取值非法 | 2 |
| 其他 | 1 |

逐条明细：

| # | 名称 | kind | 语言 | 现象 | 文本 |
|---|---|---|---|---|---|
| 1 | Heat Burn | attack | en | LLM 空返回→rule 兜底 | Your opponent’s Active Pokémon is now Burned |
| 2 | Drag Off | attack | en | errors: ['[0] action.target 非法：opponent_bench', '[0] action.target 非 | Switch in 1 of your opponent’s Benched Pokém |

## 3. B 类明细（解析成功，但动作映射不到引擎 tag）

> **B 类不是解析失败**，但**也未必是引擎缺口**——先看最后一列的对照，它是本报告最容易误读的一处：

> 实测（8bit）16 条 B 类里，**rule 后端都识别出了具体机制**（`coin_flip` / `shield` / `status` / `damage_mod` / `move_damage` …），**这些引擎已建模**；而 LLM 把它们笼统合并成了 `rule`（静态规则修改，引擎未建模该类型）。**即：这 16 条主要反映 LLM 解析粒度不足（丢失可执行语义），而不是引擎没做。**

> **可操作的改进点**：提示词的 `action.type` 枚举里没有「抛币」这类机制级取值（只有 `damage/draw/attach/heal/discard/switch/search/evolve/spread/move_damage/knockout/status/rule`），模型只能退回 `rule` 兜底。补上机制级枚举 + 少量 few-shot，是提升「完全映射率」最直接的一步（本报告只记录、未擅自改提示词——那会影响既有测试与行为，需评审）。

| # | 名称 | 语言 | LLM 得到的动作 | rule 后端识别 | 未建模动作 |
|---|---|---|---|---|---|
| 1 | Corner | zh | damage、rule | damage、rule | rule |
| 2 | Adrena-Brain | zh | rule | move_damage | rule |
| 3 | Dive | en | rule | coin_flip | rule |
| 4 | Best Punch | en | rule | coin_flip | rule |
| 5 | Protecting Steel | en | rule | shield | rule |
| 6 | Poison Ring | en | rule | status | rule |
| 7 | Recon Directive | zh | rule | search | rule |
| 8 | Hard Gears | en | rule | shield | rule |
| 9 | Energized Shell | en | damage、rule | damage、damage_mod | rule |
| 10 | Surprise Attack | en | rule | coin_flip | rule |
| 11 | Raging Curse | en | damage、rule | damage、damage_mod | rule |
| 12 | Coated Attack | en | rule | shield | rule |
| 13 | Perplex | en | rule | status | rule |
| 14 | Raging Amethyst | en | rule | discard | rule |
| 15 | Hide | en | rule | coin_flip | rule |
| 16 | Crown Opal | en | rule | shield | rule |

## 4. 口径说明（**必读**，否则会误读上面的数字）

1. **没有直接使用 `support_rate`**。原始定义是「无 unsupported 的比例」，
   但解析失败（无 spec）时 `to_engine_tags` 会被短路成 `{tags:[], unsupported:[]}`，
   `not unsupported` 为真 → **失败被计入「完全支持」**；
   且 `parse_effects` 的 llm 分支在 **LLM 空返回时会用 rule 结果兜底**（`specs = [...] or [parse_rule(...)]`）而 `backend` 仍报 `"llm"`。
   两者叠加会让 LLM 的达标率**系统性虚高**。
2. 本报告改用 `raw` 字段判断（它保留的是 **LLM 原始输出**，非兜底结果），
   并严格三分 A/B/C；**B 类不算解析失败**——解析是对的，缺的是引擎建模。
3. **抽样估计**：n 有限，请结合 95% 置信区间读；区间用 **Wilson 区间**（小样本下比正态近似稳，不会给出负数下界）。`rule 兜底率` 越低，
   说明 LLM 越能独立产出结果。
4. 语料为 `cards_real.json` 的 `desc_zh`（**自述转写**，非官方文案；C2/C5）。

