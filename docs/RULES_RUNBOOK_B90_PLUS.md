# 规则建模运行档案 · 第九十批起（权威执行依据）

> **本文件是自动生成的**，数据源为 `docs/runbook_batches.json` + 数据库实测。
> 要改批次、卡清单或进度，**改 JSON 后重跑渲染脚本**，不要手改本文件：
> `python packages/card-db/scripts/render_runbook.py`（`--check` 只校验不写）。
> 手工改动会被 `packages/card-db/tests/test_runbook_consistency.py` 判为不一致。

---

## 开工 30 秒速读

1. 读 §0 基线（**滚动值，勿凭记忆**）。
2. 在 §9 台账里找**第一条状态为「待办」**的批次号 N。
3. 读该批小节（§2–§6）的卡表与备注，记下每张卡的 `(series, number)`——**卡名有重名**。
4. 按 §1 的 7 步工作流推进，逐条过 DoD 自查。
5. 完工后改 JSON 的 `status` / `actual_pool` / `actual_refused`，重跑渲染 → 守卫 → CI，再提交。

---

## 0. 基线（2026-11-06 实测，不得凭记忆填写）

| 指标 | 数值 | 取数方式 |
|---|---|---|
| 卡牌总数 | **1267** | `cards_challenge.json` 的 `cards` 长度 |
| `engine_ready` | **1138（89.8%）** | `sum(1 for c in cards if c.get("engine_ready"))` |
| 未建模卡 | **129** | 上者取反（**口径 = 卡数**，≠ `unsupported` 条目 131） |
| 引擎池 | **1123** | `cards_pool_ext.json` 的 `cards` 长度 |
| 对账类目 | **44 类全绿** | `scripts/audit_pool_alignment.py` |
| 测试总数 | 见 **README 第 7–8 行** | 由 `scripts/check_doc_counts.py` 在 CI 校验（本档案不重复记账，避免加测试即漂移） |
| CI | **8/8 PASS** | `scripts/ci_local.py`（必须用 `/d/anconda/envs/pokemo/python.exe`） |
| K1 双盲 | **0.795**（200 局，`artifacts/k1_rich.pt`） | `examples/k1_accept.py artifacts/k1_rich.pt --games 200`（权重是位置参数） |

**口径校验（本文件自洽的判据）**：未 ready **129** = 批次内待做 **121** + 结构性边界 **6** + 待新能力 **2** = **129** ✅。

**规划**：第 90–133 批共 **44 批**；已处理 **8** 批（完成 7 · 整批受阻 1），剩余**未开工** **36** 批。

**终局预期**：`engine_ready` **1259 / 1267 = 99.4%**（当前 1138 + 可做 121）；结构性 6 张与待新能力 2 张永久留在池外，需引擎重构或新增承载字段才可能归零。

---

## 1. 每批固定工作流（7 步，缺一步视为未完成）

1. **建档**：读本文件对应批次小节，记下每张卡的 `(series, number)`（同名不同印刷必须以编号区分）。
2. **查引擎现状**：先翻 `battle.py` 的派发点、`reallib` 白名单、`gen_engine_pool` 三处白名单。**先翻引擎再猜句式**——多批收益来自「引擎已实现、解析没产出」。
3. **改解析**（`packages/agent/agent/effects.py`）：产出 tag；判不出必须回 `None`，禁止兜底猜测。
4. **改引擎**（仅当缺钩子）：新增派发分支。**同一 tag 的招式侧与训练家侧要分别检查**（`_apply_attack_effects` vs `_do_trainer`）。
5. **白名单同步**：`ATTACK_TAG_PREFIXES` / `ABILITY_TAG_PREFIXES` / `TRAINER_TAGS`（`gen_engine_pool.py`）+ `reallib` 相应元组。**漏一处 = 卡在池外凭空消失且对账全绿**。
6. **补测试（正向 + 负向）**：解析 `tests/test_batchNN_*.py`、引擎 `tests/test_batchNN_*.py`、对账 `test_audit_alignment.py` 追加 `TestBatchNN`。**负向用例必须写**，且必须验证它真的会变红。
7. **重跑链 + 验收**（顺序不可错）：

   ```
   import_challenge_dataset.py --reparse-only --parse-effects --parse-backend rule
   → gen_engine_pool.py
   → import_challenge_dataset.py --reparse-only
   → coverage_backlog.py --registry
   → audit_pool_alignment.py        # 必须 44 类全绿
   ```

**每批 DoD（全部满足才算完成）**：

- 目标卡在 `cards_pool_ext.json` 中以**正确 tag** 出现（逐卡打印核对，不认「产了 tag 的卡数」）
- `audit_pool_alignment.py` 无新增报错
- 三包测试全绿；测试计数同步 README 第 7–8 行
- 新增 tag 族时补 `apps/web/src/cardtext.js` 的 `TRAINER_TAG_DESC`（`node apps/web/e2e/cardtext_check.cjs` 必须通过）
- `git add` 后 `scripts/ci_local.py` **8/8 PASS**（redline 读暂存区，不 add 会假绿）
- 回填 `runbook_batches.json` 的 `status` / `actual_pool` / `actual_refused`，重跑本渲染脚本

---

## 2. 阶段 P1 · A 档：引擎已有，纯建池逻辑（第 90–92 批，3 批，6 槽位）

不需要新引擎机制，修归属/白名单/复合 tag 即可。 已完成 3/3 批。

### 第 90 批 · 被动 tag 归属修复（3 张）· ✅ 已完成

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| PalossandEX | SSP | 91 | done（已完成） |  |
| Miraidon ex | ASC | 73 | done（已完成） |  |
| Koraidon ex | ASC | 121 | done（已完成） |  |

**实际结果**：进池 **3** 张，如实拒收 **0** 张。

> 被动 tag 归属：上游把 Tera 规则文本归进 `attacks[]`，引擎只读 `ability.tag`。修法=建池时把「attack 上全为 `passive:`」转写为卡级被动；**禁止**把 `passive:` 放进攻击白名单。

### 第 91 批 · 训练家白名单补（1 张）· ✅ 已完成

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Deduction Kit | SSP | 171 | done（已完成） |  |

**实际结果**：进池 **1** 张，如实拒收 **0** 张。

> 补 `TRAINER_TAGS` 的 `shuffle_top:`，并确认 `_do_trainer` 有派发（此前只在 `_apply_attack_effects`）。

### 第 92 批 · 训练家多动作合成复合 tag（2 张）· ✅ 已完成

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Salvatore | TEF | 160 | done（已完成） |  |
| Larry’s Skill | PRE | 115 | done（已完成） |  |

**实际结果**：进池 **3** 张，如实拒收 **0** 张。

> 解析侧合成**单条**复合 tag（训练家只认一个卡级 tag）；禁止「只落第一条」或建池放行多 tag。 **实际进池 3 张 = 本批 2 张 + `Duosion`(BLK 38) 顺带解禁**（原列批 102，现已划掉）。

---

## 3. 阶段 P2 · B 档大族：伤害缩放 / 自由数量 / 指示物（第 93–97 批，5 批，31 槽位）

收益最集中的一段；`any number of` 风险最高。 已完成 4/5 批。

### 第 93 批 · 静态伤害缩放：条件型（4 张）· ✅ 已完成

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Team Rocket's Kangaskhan ex | ASC | 162 | done（已完成） |  |
| Galvantula ex | SCR | 51 | done（已完成） |  |
| Pangoro | ASC | 140 | done（已完成） |  |
| Walking Wake | TWM | 63 | B（有具体缺口·可归族） |  |

**实际结果**：进池 **3** 张，如实拒收 **1** 张。

> **落地 3 张**：Galvantula ex（`opp_active_is_ex_or_v`）、Team Rocket's Kangaskhan ex（`played_supporter_named:teamrocket`，读 `PlayerState.supporter_name`）、Pangoro（`own_bench_named_has_damage:pancham`）。**如实拒收 1 张**：Walking Wake「up to 9 damage counters on this Pokémon，按放置数缩放」——「up to」是玩家选择，且指示物与 `fp.damage` 合并，无法精确表达。

### 第 94 批 · 静态伤害缩放：计数源型（5 张）· ✅ 已完成

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Zoroark | SFA | 32 | done（已完成） |  |
| Misty's Gyarados | DRI | 49 | B（有具体缺口·可归族） |  |
| Mega Abomasnow ex | MEG | 36 | B（有具体缺口·可归族） |  |
| Bronzong | PFL | 72 | done（已完成） |  |
| Doublade | POR | 57 | B（有具体缺口·可归族） | 跨批 → 96 |

**实际结果**：进池 **2** 张，如实拒收 **3** 张。

> **落地 2 张**：Zoroark（`dmg_set:60:per:opp_ex_or_v`）、Bronzong（`dmg_set:40:per:both_field_tools`，引擎既有计数源首次被解析产出）。**如实拒收 3 张**：Misty's Gyarados / Mega Abomasnow ex（「弃牌库顶 N 张，按其中被弃的 X 数量缩放」需磨牌结果与伤害耦合，引擎 `mill:` 不回传计数）、Doublade（`any number of` 揭示，属批 96 交互层）。

### 第 95 批 · 静态修正层（6 张）· ✅ 已完成

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Iron Crown ex | TEF | 81 | done（已完成） |  |
| Lillie’s Clefairy ex | JTG | 56 | B（有具体缺口·可归族） |  |
| Hop’s Choice Band | JTG | 148 | B（有具体缺口·可归族） |  |
| Kieran | TWM | 154 | B（有具体缺口·可归族） |  |
| Archaludon ex | SSP | 130 | B（有具体缺口·可归族） |  |
| Feraligatr | TEF | 41 | done（已完成） | 跨批 → 97 |

**实际结果**：进池 **2** 张，如实拒收 **4** 张。

> **落地 2 张**：Iron Crown ex（`fx:static_dmg:add:20:team_category:future:except:ironcrown` —— 静态修正层新增**卡牌子类**范围与 `:except:` 扩展；旧拒绝理由「数据模型无 Future」已过期）、Feraligatr（`counters:5:scope:self|self_dmg_bonus:120:self` —— 新增**回合级加伤旗标** `dmg_bonus_until`，按持有者对象同一性判定范围，换位后同伴不沾光、跨回合自动过期）。**如实拒收 4 张**：Lillie's Clefairy ex（全局弱点层）、Hop's Choice Band（道具同时改费用+加伤，`fp.tool` 是单字符串，需复合道具承载）、Kieran（双模式 choose 1 + 回合级队伍加伤）、Archaludon ex（无弱点 + 弃牌堆自由分配附着）。

### 第 96 批 · `any number of` 自由数量（9 张）· ⛔ 受阻 · 整批如实拒收

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Alolan Exeggutor ex | SSP | 133 | B（有具体缺口·可归族） |  |
| Alakazam | TWM | 82 | B（有具体缺口·可归族） |  |
| Lillie’s Ribombee | JTG | 67 | B（有具体缺口·可归族） |  |
| Gorebyss | DRI | 56 | B（有具体缺口·可归族） |  |
| Team Rocket's Honchkrow | ASC | 127 | B（有具体缺口·可归族） |  |
| Accompanying Flute | TWM | 142 | B（有具体缺口·可归族） |  |
| Strange Timepiece | MEG | 128 | B（有具体缺口·可归族） |  |
| Naveen | POR | 79 | B（有具体缺口·可归族） |  |
| Doublade | POR | 57 | B（有具体缺口·可归族） | 跨批 → 94 |

**实际结果**：进池 **0** 张，如实拒收 **9** 张。

> **整批受阻（0 张）**：`any number of` 全部需要**交互层**（玩家自选张数），现有 `choose` 类一律确定性默认。按纪律**不以确定性默认冒充**——对攻/弃牌类（Doublade / TR Honchkrow）默认取「全部」会误弃整手，取「0」则伤害归零，两种默认都会实质改变对局结果，属静默错映射。

### 第 97 批 · 指示物目标 / 条件（7 张）· ✅ 已完成

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Feraligatr | TEF | 41 | done（已完成） | 跨批 → 95 |
| Sinistcha | TWM | 22 | B（有具体缺口·可归族） |  |
| Team Rocket's Ampharos | DRI | 74 | B（有具体缺口·可归族） |  |
| Team Rocket's Crobat ex | DRI | 122 | done（已完成） |  |
| Pachirisu | MEG | 51 | B（有具体缺口·可归族） |  |
| Mismagius | ASC | 86 | done（已完成） |  |
| Team Rocket's Dugtrio | ASC | 101 | B（有具体缺口·可归族） |  |

**实际结果**：进池 **3** 张，如实拒收 **4** 张。

> **落地 3 张**：Team Rocket's Crobat ex（`evolve_act:counters:2:scope:opp:2`）、Mismagius（`counters:6:scope:opp_bench:1:gate:opp_active_has_status` —— 新增条件门 `:gate:` 与后备专用范围 `:scope:opp_bench`）、Feraligatr（复合特性 `counters:5:scope:self|self_dmg_bonus:120:self`）。**如实拒收 4 张**：Sinistcha（需**分配**语义，`counters:` 只能每目标各 N 个）、Team Rocket's Ampharos / Pachirisu / Team Rocket's Dugtrio（触发点是**对手**的进化 / 附能 / 撤退，引擎无对应事件钩子）。

---

## 4. 阶段 P3 · B 档小族（一族一批）（第 98–111 批，14 批，41 槽位）

族太散，是 P2 之后最慢的一段。 已完成 0/14 批。

### 第 98 批 · 附加伤害目标扩展（3 张）· 待办

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Shaymin | TEF | 13 | B（有具体缺口·可归族） |  |
| Wellspring Mask Ogerpon ex | TWM | 64 | B（有具体缺口·可归族） |  |
| Flygon ex | SSP | 106 | B（有具体缺口·可归族） |  |

### 第 99 批 · 检索去向 / 数量由计数定（6 张）· 待办

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Drilbur | TEF | 85 | B（有具体缺口·可归族） |  |
| Palafin | TWM | 60 | B（有具体缺口·可归族） |  |
| Lillie’s Comfey | JTG | 68 | B（有具体缺口·可归族） |  |
| Musharna | BLK | 36 | B（有具体缺口·可归族） |  |
| Rotom | ASC | 92 | B（有具体缺口·可归族） |  |
| Grumpig | MEG | 63 | B（有具体缺口·可归族） |  |

### 第 100 批 · 抛币嵌套子效果（3 张）· 待办

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Vanilluxe | WHT | 29 | B（有具体缺口·可归族） |  |
| Team Rocket's Great Ball | DRI | 175 | B（有具体缺口·可归族） |  |
| Lucian | TWM | 157 | B（有具体缺口·可归族） | 跨批 → 110 |

### 第 101 批 · 受击历史 `on_damaged`（3 张）· 待办

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Team Rocket's Koffing | DRI | 125 | B（有具体缺口·可归族） |  |
| Mega Heracross ex | PFL | 4 | B（有具体缺口·可归族） |  |
| Team Rocket’s Hypnotizer | ASC | 206 | B（有具体缺口·可归族） |  |

### 第 102 批 · 特殊进化驱动 / 进化触发特性（2 张）· 待办

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Archeops | WHT | 51 | B（有具体缺口·可归族） |  |
| Noctowl | SCR | 115 | B（有具体缺口·可归族） |  |

### 第 103 批 · 能量代价 / 提供规则（3 张）· 待办

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Mega Charizard X ex | PFL | 13 | B（有具体缺口·可归族） |  |
| Mega Eelektross ex | ASC | 61 | B（有具体缺口·可归族） |  |
| Dialga | MEG | 95 | B（有具体缺口·可归族） |  |

### 第 104 批 · 回复范围 / 全量回复（3 张）· 待办

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Hydrapple ex | SCR | 14 | B（有具体缺口·可归族） |  |
| Magearna | JTG | 107 | B（有具体缺口·可归族） |  |
| Bianca’s Devotion | TEF | 142 | B（有具体缺口·可归族） |  |

### 第 105 批 · 区分基本 / 特殊能量（2 张）· 待办

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Enhanced Hammer | TWM | 148 | B（有具体缺口·可归族） |  |
| Megaton Blower | SSP | 182 | B（有具体缺口·可归族） |  |

> 引擎当前不区分基本/特殊能量；压成「任意能量」= 静默放宽。须先补 `is_special` 判定，否则如实拒收。

### 第 106 批 · 场地形态（S3 扩展）（2 张）· 待办

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Dizzying Valley | PFL | 88 | B（有具体缺口·可归族） |  |
| Nighttime Mine | ASC | 197 | B（有具体缺口·可归族） |  |

### 第 107 批 · 对手手牌禁打层（3 张）· 待办

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Genesect | SFA | 40 | B（有具体缺口·可归族） |  |
| Chi-Yu | MEG | 31 | B（有具体缺口·可归族） |  |
| Scream Tail ex | TWM | 94 | B（有具体缺口·可归族） | 跨批 → 108 |

### 第 108 批 · 首回合 / 招式静态条件（2 张）· 待办

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Terapagos ex | SCR | 128 | B（有具体缺口·可归族） |  |
| Scream Tail ex | TWM | 94 | B（有具体缺口·可归族） | 跨批 → 107 |

### 第 109 批 · 打出前置 gate（2 张）· 待办

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Team Rocket's Archer | DRI | 170 | B（有具体缺口·可归族） |  |
| Rosa's Encouragement | POR | 84 | B（有具体缺口·可归族） |  |

> **Anthea & Concordia 警示**：gate「6 张具名宝可梦在场」未建模；`_split_play_gate` 只剥认识的 gate，不认识的会留在 text → 认领前必须检查 `can use this card only if` 是否残留，残留即整卡拒收（否则 = 无条件 +3 奖赏的静默 buff）。 **（Anthea & Concordia 已改列结构性边界 §7，本批不再计入槽位）**

### 第 110 批 · 抽牌分支 / 延迟队列（3 张）· 待办

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Team Rocket's Ariana | DRI | 171 | B（有具体缺口·可归族） |  |
| Amarys | PRE | 93 | B（有具体缺口·可归族） |  |
| Lucian | TWM | 157 | B（有具体缺口·可归族） | 跨批 → 100 |

### 第 111 批 · 状态 / 免疫类 / 特性代价（4 张）· 待办

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Pecharunt ex | SFA | 39 | B（有具体缺口·可归族） |  |
| Hoothoot | PRE | 77 | B（有具体缺口·可归族） |  |
| Mismagius ex | PFL | 36 | B（有具体缺口·可归族） |  |
| Lunatone | MEG | 74 | B（有具体缺口·可归族） |  |

---

## 5. 阶段 P4 · D 档「差最后一块」（第 112–120 批，9 批，39 槽位）

杠杆最高：多数子句已能单独产 tag，前面族就位后会批量收敛。 已完成 0/9 批。

### 第 112 批 · 自身弃能代价（5 张）· 待办

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Kyurem | SFA | 47 | D（兜底·复合句） |  |
| Klinklang | SCR | 101 | D（兜底·复合句） |  |
| Rotom ex | PFL | 29 | D（兜底·复合句） |  |
| Slaking ex | SSP | 147 | D（兜底·复合句） |  |
| Core Memory | POR | 70 | D（兜底·复合句） |  |

> `discard_energy:all` 与 `discard_energy:N` 是不同口径；`selfko` 早已存在，做代价前先搜引擎。

### 第 113 批 · 自伤（2 张）· 待办

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Azumarill | SSP | 74 | D（兜底·复合句） |  |
| Eevee | SSP | 143 | D（兜底·复合句） |  |

### 第 114 批 · 自身治疗 / 自身状态（2 张）· 待办

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Zarude | SSP | 11 | D（兜底·复合句） |  |
| Conkeldurr | TWM | 105 | D（兜底·复合句） |  |

### 第 115 批 · 静态伤害缩放 D 组（7 张）· 待办

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Scovillain ex | SSP | 37 | D（兜底·复合句） |  |
| Lycanroc | JTG | 85 | D（兜底·复合句） |  |
| Golduck | ASC | 40 | D（兜底·复合句） |  |
| Gengar | POR | 50 | D（兜底·复合句） |  |
| Mega Mawile ex | MEG | 94 | D（兜底·复合句） |  |
| Team Rocket's Articuno | DRI | 51 | D（兜底·复合句） |  |
| Carbink | ASC | 117 | D（兜底·复合句） |  |

### 第 116 批 · 对手下回合限制（6 张）· 待办

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Sylveon ex | SSP | 86 | D（兜底·复合句） |  |
| Team Rocket's Moltres ex | DRI | 31 | D（兜底·复合句） |  |
| Ethan's Sudowoodo | DRI | 93 | D（兜底·复合句） |  |
| Walrein | SSP | 45 | D（兜底·复合句） |  |
| Palafin ex | TWM | 61 | D（兜底·复合句） |  |
| Revavroom ex | SFA | 15 | D（兜底·复合句） |  |

> `retreat_lock` / `attack_lock` / 减伤是三个不同字段，勿混用 `dmg_reduce` 与 `shield:reduce`。

### 第 117 批 · 特殊进化 / 对手交互（3 张）· 待办

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Eevee ex | PRE | 75 | D（兜底·复合句） |  |
| Team Rocket's Persian ex | DRI | 150 | D（兜底·复合句） |  |
| Espeon ex | PRE | 34 | D（兜底·复合句） |  |

### 第 118 批 · 能量附着 / 移动（3 张）· 待办

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Cinderace | MEG | 28 | D（兜底·复合句） |  |
| Forretress | DRI | 140 | D（兜底·复合句） |  |
| Dedenne | POR | 29 | D（兜底·复合句） |  |

### 第 119 批 · 牌库顶 / 底操作（3 张）· 待办

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Fraxure | SFA | 45 | D（兜底·复合句） |  |
| Watchog | WHT | 73 | D（兜底·复合句） |  |
| Meloetta ex | BLK | 44 | D（兜底·复合句） |  |

### 第 120 批 · 杂项收尾（8 张）· 待办

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Cetitan ex | DRI | 65 | D（兜底·复合句） |  |
| Team Rocket's Mewtwo ex | DRI | 81 | D（兜底·复合句） |  |
| Virizion | WHT | 10 | D（兜底·复合句） |  |
| Tyrantrum | POR | 45 | D（兜底·复合句） |  |
| Decidueye ex | POR | 12 | D（兜底·复合句） |  |
| Shiftry | TEF | 5 | D（兜底·复合句） |  |
| Iron Thorns ex | TWM | 77 | D（兜底·复合句） |  |
| Antique Sail Fossil | POR | 69 | D（兜底·复合句） |  |

---

## 6. 阶段 P5 · C 档逐句判读 + 结构性建档（第 121–133 批，13 批，23 槽位）

兜底 reason 说不清缺什么，需人工判读；最后收尾。 已完成 0/13 批。

### 第 121 批 · 特殊进化条件（3 张）· 待办

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Karrablast | BLK | 9 | C（兜底·单句） |  |
| Shelmet | WHT | 8 | C（兜底·单句） |  |
| Luxio | POR | 27 | C（兜底·单句） |  |

### 第 122 批 · 揭示对手手牌（4 张）· 待办

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Hoothoot | TEF | 126 | C（兜底·单句） |  |
| Espurr | SSP | 84 | C（兜底·单句） |  |
| Pidove | BLK | 71 | C（兜底·单句） |  |
| Mandibuzz | WHT | 64 | C（兜底·单句） |  |

### 第 123 批 · 奖赏区操作（4 张）· 待办

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| N’s Sigilyph | JTG | 64 | C（兜底·单句） |  |
| Team Rocket's Blipbug | DRI | 15 | C（兜底·单句） |  |
| Redeemable Ticket | JTG | 156 | C（兜底·单句） |  |
| Team Rocket's Bother-Bot | DRI | 172 | C（兜底·单句） |  |

### 第 124 批 · 能量提供规则（3 张）· 待办

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Boomerang Energy | TWM | 166 | C（兜底·单句） |  |
| Meganium | MEG | 10 | C（兜底·单句） |  |
| Smeargle | PFL | 80 | C（兜底·单句） |  |

### 第 125 批 · 牌库底 / 顶检视（3 张）· 待办

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Misty's Psyduck | DRI | 45 | C（兜底·单句） |  |
| Dusk Ball | SSP | 175 | C（兜底·单句） |  |
| Gumshoos | MEG | 110 | C（兜底·单句） |  |

### 第 126 批 · 道具触发式（2 张）· 待办

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Heavy Baton | TEF | 151 | C（兜底·单句） |  |
| Ruffian | JTG | 157 | C（兜底·单句） |  |

### 第 127 批 · 条件费用 / 特性触发（2 张）· 待办

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Crawdaunt | MEG | 85 | C（兜底·单句） |  |
| Latios | MEG | 101 | C（兜底·单句） |  |

### 第 128 批 · 延迟到回合末（1 张）· 待办

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Team Rocket's Grimer | DRI | 123 | C（兜底·单句） |  |

### 第 129 批 · 条件 +HP（单只）（1 张）· 待办

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Okidogi | TWM | 111 | C（兜底·单句） |  |

### 第 130 批 · 结构性建档 · 全队 +HP（Ludicolo / Cynthia's Power Weight）（0 张）· 待办

本批不承载卡清单，只做结构性边界建档（见 §7）。

> 两张均因 `max_hp` 无统一重算入口；建档并标注为已知边界。

### 第 131 批 · 结构性建档 · 指示物翻倍（N's Vanilluxe）（0 张）· 待办

本批不承载卡清单，只做结构性边界建档（见 §7）。

> `fp.damage` 合并承载攻击伤害与指示物，翻倍会连攻击伤害一起翻。

### 第 132 批 · 结构性建档 · 全局规则层（Psyduck / Milotic）（0 张）· 待办

本批不承载卡清单，只做结构性边界建档（见 §7）。

> 需要「场上特性禁用」「区域流转禁止」两类全局规则层。

### 第 133 批 · 结构性建档 · gate 类（Anthea & Concordia）+ 边界收尾（0 张）· 待办

本批不承载卡清单，只做结构性边界建档（见 §7）。

> gate 未建模；同时在文档与 UI 统一标注「已知边界」，使 backlog 读数可解释。

---

## 7. 结构性边界与待新能力（永久池外，需引擎重构）

这 6 张不是「能力不足」，而是**引擎架构限制**：强行建模会产生静默错映射。**不为它们造假 tag**，在 DB 标 `engine_status="structural_limit"`，并在文档与 UI 标注为「已知边界」，使 backlog 读数可解释地停在这里。

| 卡 | series | number | 引擎限制 |
|---|---|---|---|
| Ludicolo | JTG | 37 | 全队 +HP：`_refresh_stadium_hp` 仅回合开始/场地变更时重算，场上可改变 `max_hp` 的约 30 处变更无统一入口 → 会延迟/残留 |
| Cynthia's Power Weight | DRI | 162 | 同上（+70 HP） |
| N's Vanilluxe | ASC | 51 | 指示物翻倍：`fp.damage` 同时承载攻击伤害与指示物，翻倍会连攻击伤害一起翻 |
| Psyduck | ASC | 39 | 需要全局规则层（场上所有宝可梦失去特性） |
| Milotic | TWM | 50 | 需要全局规则层（对手区域流转禁止） |
| Anthea & Concordia | ASC | 182 | 6 张具名宝可梦 gate 未建模；`_split_play_gate` 不认识会静默丢弃 → 会变成无条件 +3 奖赏 |

> 要真正做到 100%，需三项独立工程（各自立项，不属本档案）：
> ① 拆分 `fp.damage` 为 `attack_damage` + `damage_counters`；② 统一 `max_hp` 重算入口；③ 新增全局规则层（特性禁用 / 区域流转）。

**待新能力（2 张）**：效果为回合末 / 被击倒**触发式**，不是 `tool:` 常驻，通过道具检查会让它在构筑期无条件生效（静默加强）→ 必须保持拒收，直到 `TrainerDef` 具备触发式承载字段。

| 卡 | series | number | 结论 |
|---|---|---|---|
| Powerglass | SFA | 63 | 效果为回合末/被击倒**触发式**，非 `tool:` 常驻；按 `build_trainer` 道具纪律保持拒收。需先给 `TrainerDef` 增「触发式道具」承载字段。 |
| Amulet of Hope | SSP | 162 | 效果为回合末/被击倒**触发式**，非 `tool:` 常驻；按 `build_trainer` 道具纪律保持拒收。需先给 `TrainerDef` 增「触发式道具」承载字段。 |

### 跨批卡登记（进度只记一次）

| 卡 | 涉及批次 | 处理规则 |
|---|---|---|
| Doublade (POR 57) | 94 + 96 | 两族都就位才进池，进度只记一次 |
| Scream Tail ex (TWM 94) | 107 + 108 | 同上 |
| Lucian (TWM 157) | 100 + 110 | 同上 |
| Feraligatr (TEF 41) | 95 + 97 | 两批都要改：静态修正层（95）+ 指示物（97）；两处各记 1 槽，进度只记一次 |

---

## 8. 偏差处理规则（严格执行，不得临场放宽）

1. **做不了就如实拒收**并记录原因；不得为让卡进池而放宽白名单、压窄语义或改测试期望值。
2. **静默错映射 > 未建模**：宁可卡在池外，也不上「不报错、测试全绿、只有卡面↔tag 对账看得出」的错映射。
3. **改存量测试前先判断**：它断言的是「旧口径」还是「纪律承重墙」。前者可改并注明原因，后者必须改代码。
4. **每批结束改 JSON 回填**，不要手改本文件（会被守卫抓到）。
5. 某批实际收益低于规划时，**不要用后续批次的卡凑数**；宁可该批只进 1 张。
6. **负向自测必须真能变红**：写完守卫后注入错误写法跑一次，确认测试**失败**；守卫若因测试布局而永远通过，等于没写。

---

## 8.5 实战陷阱（已踩过，后续批次直接复用）

1. **「招式有、训练家没有」已出现三处**：`energy_to_deck`（历史）、`shuffle_top`（批 91）、`evolve_from_deck`（批 92）。`battle.py` 里出现一次**不算支持**——须同时确认 `_do_trainer` 有分支、`TRAINER_TAGS` 有前缀、`reallib` 有登记。
2. **上游数据集会把卡面规则分类错**：Pokémon ex 的 Tera 规则文本被归进 `attacks[]`。修法是在建池侧转写为卡级被动；**禁止**把 `passive:*` 放进攻击白名单（= 出招才免伤的反转）。
3. **`reallib._SUPPORTED_TRAINER_TAGS` 是死代码**（仅定义、零引用）。仍要同步以免注释撒谎，但**别指望它拦卡**——真正生效的是 `gen_engine_pool` 的三处白名单。
4. **测试布局会让守卫变假**（P1 抓到两个）：
   - `_evolve_from_deck` 从**牌尾往前**扫；把「不该被选中」的卡放数组前面，过滤失效也会选对 → 守卫永远绿。
   - 混合 tag 反例里掺入**本就不在白名单**的前缀（如 `draw:`），拒收原因变成那条非法前缀，掩盖了真正要测的越界 → 守卫永远绿。反例必须用**白名单内**前缀。
5. **`candy` 是 Rare Candy 的跳阶语义**，不是「从牌库进化」的近似：误产会让引擎跳过一阶直接放二阶。判据是「同一 spec 内先 search(deck) 再 evolve」→ `evolve_from_deck`。
6. **`basic energy card` 必须排在 `energy card` 之前**判定，否则丢掉 basic 限定 → 连特殊能量一起检索（静默放宽）。
7. **早退认领会吞掉未识别的 gate**：`_split_play_gate` 只剥它认识的 gate，不认识的留在 text 里。认领前必须检查 `can use this card only if` 是否残留，残留即整卡拒收（第八十九批 Anthea & Concordia 就是这样被静默加成 +3 奖赏的）
8. **对账口径要与解析口径对齐**：真实卡池存在 `\xa0`（NBSP），对账 `_norm` 若不归一，会出现「解析产了 tag、对账报不符」。**动解析链必跑 `audit_pool_alignment.py`**。

---

## 9. 进度台账（由 JSON 自动生成）

| 批 | 阶段 | 主题 | 计划张数 | 实际进池 | 如实拒收 | 状态 |
|---|---|---|---|---|---|---|
| 90 | P1 | 被动 tag 归属修复 | 3 | 3 | 0 | ✅ 完成 |
| 91 | P1 | 训练家白名单补 | 1 | 1 | 0 | ✅ 完成 |
| 92 | P1 | 训练家多动作合成复合 tag | 2 | 3 | 0 | ✅ 完成 |
| 93 | P2 | 静态伤害缩放：条件型 | 4 | 3 | 1 | ✅ 完成 |
| 94 | P2 | 静态伤害缩放：计数源型 | 5 | 2 | 3 | ✅ 完成 |
| 95 | P2 | 静态修正层 | 6 | 2 | 4 | ✅ 完成 |
| 96 | P2 | `any number of` 自由数量 | 9 | 0 | 9 | ⛔ 受阻 |
| 97 | P2 | 指示物目标 / 条件 | 7 | 3 | 4 | ✅ 完成 |
| 98 | P3 | 附加伤害目标扩展 | 3 |  |  | 待办 |
| 99 | P3 | 检索去向 / 数量由计数定 | 6 |  |  | 待办 |
| 100 | P3 | 抛币嵌套子效果 | 3 |  |  | 待办 |
| 101 | P3 | 受击历史 `on_damaged` | 3 |  |  | 待办 |
| 102 | P3 | 特殊进化驱动 / 进化触发特性 | 2 |  |  | 待办 |
| 103 | P3 | 能量代价 / 提供规则 | 3 |  |  | 待办 |
| 104 | P3 | 回复范围 / 全量回复 | 3 |  |  | 待办 |
| 105 | P3 | 区分基本 / 特殊能量 | 2 |  |  | 待办 |
| 106 | P3 | 场地形态（S3 扩展） | 2 |  |  | 待办 |
| 107 | P3 | 对手手牌禁打层 | 3 |  |  | 待办 |
| 108 | P3 | 首回合 / 招式静态条件 | 2 |  |  | 待办 |
| 109 | P3 | 打出前置 gate | 2 |  |  | 待办 |
| 110 | P3 | 抽牌分支 / 延迟队列 | 3 |  |  | 待办 |
| 111 | P3 | 状态 / 免疫类 / 特性代价 | 4 |  |  | 待办 |
| 112 | P4 | 自身弃能代价 | 5 |  |  | 待办 |
| 113 | P4 | 自伤 | 2 |  |  | 待办 |
| 114 | P4 | 自身治疗 / 自身状态 | 2 |  |  | 待办 |
| 115 | P4 | 静态伤害缩放 D 组 | 7 |  |  | 待办 |
| 116 | P4 | 对手下回合限制 | 6 |  |  | 待办 |
| 117 | P4 | 特殊进化 / 对手交互 | 3 |  |  | 待办 |
| 118 | P4 | 能量附着 / 移动 | 3 |  |  | 待办 |
| 119 | P4 | 牌库顶 / 底操作 | 3 |  |  | 待办 |
| 120 | P4 | 杂项收尾 | 8 |  |  | 待办 |
| 121 | P5 | 特殊进化条件 | 3 |  |  | 待办 |
| 122 | P5 | 揭示对手手牌 | 4 |  |  | 待办 |
| 123 | P5 | 奖赏区操作 | 4 |  |  | 待办 |
| 124 | P5 | 能量提供规则 | 3 |  |  | 待办 |
| 125 | P5 | 牌库底 / 顶检视 | 3 |  |  | 待办 |
| 126 | P5 | 道具触发式 | 2 |  |  | 待办 |
| 127 | P5 | 条件费用 / 特性触发 | 2 |  |  | 待办 |
| 128 | P5 | 延迟到回合末 | 1 |  |  | 待办 |
| 129 | P5 | 条件 +HP（单只） | 1 |  |  | 待办 |
| 130 | P5 | 结构性建档 · 全队 +HP（Ludicolo / Cynthia's Power Weight） | 0 |  |  | 待办 |
| 131 | P5 | 结构性建档 · 指示物翻倍（N's Vanilluxe） | 0 |  |  | 待办 |
| 132 | P5 | 结构性建档 · 全局规则层（Psyduck / Milotic） | 0 |  |  | 待办 |
| 133 | P5 | 结构性建档 · gate 类（Anthea & Concordia）+ 边界收尾 | 0 |  |  | 待办 |

> 台账的「计划张数」只计**卡槽**；第 130–133 批是结构性建档批（0 槽），涉及的 6 张统一列在 §7，不重复计入槽位。

**口径核对**：

| 项 | 张数 | 说明 |
|---|---|---|
| 批次槽位合计 | 140 | 跨批卡在每批各记 1 次 |
| 批次唯一卡 | 136 | 其中已完成 15 张 |
| 批次内待做 | 121 | 全部未 ready |
| 结构性边界 | 6 | 不承载 tag，仅建档 |
| 待新能力 | 2 | 需新增承载字段 |
| **合计（= 未 ready 卡数）** | **129** | 应等于基线 129 |

---

## 10. 维护方式与文档关系

| 文件 | 角色 |
|---|---|
| `docs/runbook_batches.json` | **数据真相**：批次 → 卡 → 状态。人工维护 |
| `packages/card-db/scripts/render_runbook.py` | 渲染器：JSON + 实测 → 本文件 |
| `docs/RULES_RUNBOOK_B90_PLUS.md`（本文件） | **执行依据**（自动生成，勿手改） |
| `packages/card-db/tests/test_runbook_consistency.py` | 守卫：JSON ↔ DB ↔ 本文件一致性 |
| `docs/RULES_COMPLETION_PLAN.md` | 历史成果台账（第八十五~八十九批及之前），只追加 |
| `packages/card-db/data/cleaned/unmodeled_registry.md` | 机器生成的未建模清单（数据真相） |

**改批次的正确姿势**：

```bash
# 1) 改 JSON（增删卡 / 改 status / 填 actual_pool）
python packages/card-db/scripts/render_runbook.py          # 2) 重渲染
python -m unittest tests.test_runbook_consistency -v       # 3) 守卫（card-db 目录下）
python scripts/ci_local.py                                  # 4) CI 8/8
```

若 registry 与本文件冲突，**以 registry 为准并修正 JSON**——registry 由脚本从 DB 生成，本文件是计划编排，允许滞后但不允许自相矛盾。

