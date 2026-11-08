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

## 0. 基线（2026-11-08 实测，不得凭记忆填写）

| 指标 | 数值 | 取数方式 |
|---|---|---|
| 卡牌总数 | **1267** | `cards_challenge.json` 的 `cards` 长度 |
| `engine_ready` | **1222（96.4%）** | `sum(1 for c in cards if c.get("engine_ready"))` |
| 未建模卡 | **45** | 上者取反（**口径 = 卡数**，≠ `unsupported` 条目 46） |
| 引擎池 | **1207** | `cards_pool_ext.json` 的 `cards` 长度 |
| 对账类目 | **51 类全绿** | `scripts/audit_pool_alignment.py` |
| 测试总数 | 见 **README 第 7–8 行** | 由 `scripts/check_doc_counts.py` 在 CI 校验（本档案不重复记账，避免加测试即漂移） |
| CI | **8/8 PASS** | `scripts/ci_local.py`（必须用 `/d/anconda/envs/pokemo/python.exe`） |
| K1 双盲 | **0.770**（200 局双盲 = 100 先手 + 100 后手，`artifacts/k1_rich.pt`；单侧对照 0.815、先手基线 0.640）· ⚠ S6a 打开官方先手规则后由 0.795 → 0.770（差值 0.025 < CI ±0.058，仍 ≥0.70 判据；权重是在旧规则下训练的，重训可回收） | `examples/k1_accept.py artifacts/k1_rich.pt --games 200`（权重是位置参数） |

**口径校验（本文件自洽的判据）**：未 ready **45** = 批内如实拒收 **41** + 结构性边界 **4** + 待新能力 **0** = **45** ✅。

**规划**：第 90–133 批共 **44 批**；已处理 **44** 批（完成 39 · 整批受阻 5），剩余**未开工** **0** 批。

**终局**：`engine_ready` **1222 / 1267 = 96.4%**（44 批全部处理完毕）。剩余 41 张已逐批**如实拒收**（原因见各批说明），另 4 张结构性边界与 0 张待新能力卡需引擎重构（退化 devolve · 多条件组合门槛（gate）· 任意数量交互层 · 官方规则对齐 S6）才可能归零。

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
   → audit_pool_alignment.py        # 必须 51 类全绿
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

收益最集中的一段；`any number of` 风险最高。 已完成 5/5 批。

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
| Archaludon ex | SSP | 130 | done（已完成） |  |
| Feraligatr | TEF | 41 | done（已完成） | 跨批 → 97 |

**实际结果**：进池 **2** 张，如实拒收 **4** 张。

> **落地 2 张**：Iron Crown ex（`fx:static_dmg:add:20:team_category:future:except:ironcrown` —— 静态修正层新增**卡牌子类**范围与 `:except:` 扩展；旧拒绝理由「数据模型无 Future」已过期）、Feraligatr（`counters:5:scope:self|self_dmg_bonus:120:self` —— 新增**回合级加伤旗标** `dmg_bonus_until`，按持有者对象同一性判定范围，换位后同伴不沾光、跨回合自动过期）。**如实拒收 4 张**：Lillie's Clefairy ex（全局弱点层）、Hop's Choice Band（道具同时改费用+加伤，`fp.tool` 是单字符串，需复合道具承载）、Kieran（双模式 choose 1 + 回合级队伍加伤）、Archaludon ex（无弱点 + 弃牌堆自由分配附着）。

### 第 96 批 · `any number of` 自由数量（9 张）· ✅ 已完成

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Alolan Exeggutor ex | SSP | 133 | B（有具体缺口·可归族） |  |
| Alakazam | TWM | 82 | B（有具体缺口·可归族） |  |
| Lillie’s Ribombee | JTG | 67 | B（有具体缺口·可归族） |  |
| Gorebyss | DRI | 56 | B（有具体缺口·可归族） |  |
| Team Rocket's Honchkrow | ASC | 127 | B（有具体缺口·可归族） |  |
| Accompanying Flute | TWM | 142 | done（已完成） |  |
| Strange Timepiece | MEG | 128 | B（有具体缺口·可归族） |  |
| Naveen | POR | 79 | done（已完成） |  |
| Doublade | POR | 57 | B（有具体缺口·可归族） | 跨批 → 94 |

**实际结果**：进池 **1** 张，如实拒收 **8** 张。

> **部分完成（1 张进池 / 8 张如实拒收）· 第一百三十五批（S1a）**：交互层（决策分解）落地 —— 引擎新增 `phase="choice"` + `PendingChoice`，「弃任意数量手牌」不再需要默认值，由牌手逐张选、随时可 `stop`。Naveen 因此进池（`choose:discard_hand:any:then:draw_to:5`）。其余 8 张仍需各自的 choice kind（可选**能量代价**、多目标伤害分配、跨区选择：Alakazam / Dialga / Mega Charizard X ex / Honchkrow / Doublade / Alolan Exeggutor ex / Strange Timepiece / Lillie's Ribombee），属 S1b 范围。

### 第 97 批 · 指示物目标 / 条件（7 张）· ✅ 已完成

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Feraligatr | TEF | 41 | done（已完成） | 跨批 → 95 |
| Sinistcha | TWM | 22 | done（已完成） |  |
| Team Rocket's Ampharos | DRI | 74 | done（已完成） |  |
| Team Rocket's Crobat ex | DRI | 122 | done（已完成） |  |
| Pachirisu | MEG | 51 | done（已完成） |  |
| Mismagius | ASC | 86 | done（已完成） |  |
| Team Rocket's Dugtrio | ASC | 101 | done（已完成） |  |

**实际结果**：进池 **4** 张，如实拒收 **3** 张。

> **落地 3 张**：Team Rocket's Crobat ex（`evolve_act:counters:2:sc … **S1c 补记**：「Put N damage counters on your opponent’s Pokémon in any way you like」已由决策分解表达（`choose:counters_any:N`，目标**含战斗位**、允许重复同一只，min=max=N）。Sinistcha 因此进池。

---

## 4. 阶段 P3 · B 档小族（一族一批）（第 98–111 批，14 批，41 槽位）

族太散，是 P2 之后最慢的一段。 已完成 14/14 批。

### 第 98 批 · 附加伤害目标扩展（3 张）· ✅ 已完成

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Shaymin | TEF | 13 | done（已完成） |  |
| Wellspring Mask Ogerpon ex | TWM | 64 | B（有具体缺口·可归族） |  |
| Flygon ex | SSP | 106 | done（已完成） |  |

**实际结果**：进池 **2** 张，如实拒收 **1** 张。

> **落地 2 张**：Shaymin（`snipe:opp_bench:60:1:target:ex`）、Flygon ex（`snipe:opp:100:all:target:ex` + 无视弱抗）——引擎第四十一批就有 `:target:ex` 目标筛选，缺的是「{ex} or/and {V}」并集写法的**短语映射**。**如实拒收 1 张**：Wellspring Mask Ogerpon ex（「可以洗回 3 个能量，若如此做才对后备 +120」= 可选代价与效果耦合，需交互层）。

### 第 99 批 · 检索去向 / 数量由计数定（6 张）· ✅ 已完成

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Drilbur | TEF | 85 | done（已完成） |  |
| Palafin | TWM | 60 | done（已完成） |  |
| Lillie’s Comfey | JTG | 68 | done（已完成） |  |
| Musharna | BLK | 36 | done（已完成） |  |
| Rotom | ASC | 92 | done（已完成） |  |
| Grumpig | MEG | 63 | done（已完成） |  |

**实际结果**：进池 **4** 张，如实拒收 **2** 张。

> **落地 4 张**：Drilbur（`on_bench_act:search_discard:3:energy:type:fighting` —— 新增**上场触发**包装层，与 `evolve_act:` 同构；不包就是普通 field 特性，会变成「每回合白弃 3 张牌库」）、Lillie's Comfey（`search_bench:all:basic:name_has:lillies`）、Musharna（`search_trainer:all:name_has:fennel`）、Rotom（`search_bench:all:name_has:rotom`）—— 后三张补齐 `any number of` 的**具名**形态与 `:name_has:` 子串筛选。**如实拒收 2 张**：Palafin（「检索 Palafin ex 并**替换自身**，保留附着卡/伤害/状态」需场上实例替换，引擎没有该机制）、Grumpig（「检视牌库顶 4 张，把其中任意张 Basic Energy **自由分配**附着」需分配界面，`recon:` 的 `dest:attach` 只支持单目标）。

### 第 100 批 · 抛币嵌套子效果（3 张）· ✅ 已完成

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Vanilluxe | WHT | 29 | done（已完成） |  |
| Team Rocket's Great Ball | DRI | 175 | done（已完成） |  |
| Lucian | TWM | 157 | done（已完成） | 跨批 → 110 |

**实际结果**：进池 **1** 张，如实拒收 **2** 张。

> **落地 1 张**：Vanilluxe（`coin_each:2:heads:dmg_set:90|cond:at_least_heads:1:paralyze`）——引擎早已支持多币阈值，缺的是「If **either** of them is heads」这句措辞（此前只认 both/all/at least）。**如实拒收 2 张**：Team Rocket's Great Ball（抛币两侧各检索不同类别，需 coin 嵌套检索）、Lucian（双手牌洗回 + 抛币抽牌，多段复合）。

### 第 101 批 · 受击历史 `on_damaged`（3 张）· ✅ 已完成

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Team Rocket's Koffing | DRI | 125 | done（已完成） |  |
| Mega Heracross ex | PFL | 4 | done（已完成） |  |
| Team Rocket’s Hypnotizer | ASC | 206 | done（已完成） |  |

**实际结果**：进池 **3** 张，如实拒收 **0** 张。

> **落地 3 张**：Team Rocket's Koffing（`on_damaged:search_bench:2:name_has:koffing`）、Mega Heracross ex（`dmg_plus_prev_taken`）、Team Rocket's Hypnotizer（`tool:on_damaged:status:sleep:holder:faction:teamrocket`）。新增三件：`FieldPokemon.dmg_taken_prev/mark`（回合开始把上回合累计量滚过来，**先快照再清零**）、`on_damaged` 的 `search_bench` kind、道具受击的 `status` kind + 阵营持有者限定。

### 第 102 批 · 特殊进化驱动 / 进化触发特性（2 张）· ✅ 已完成

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Archeops | WHT | 51 | B（有具体缺口·可归族） |  |
| Noctowl | SCR | 115 | done（已完成） |  |

**实际结果**：进池 **1** 张，如实拒收 **1** 张。

> **落地 1 张**：Noctowl（`evolve_act:search_trainer:2:gate:tera_in_play` —— 进化触发特性新增**前置门** `:gate:`，条件不满足整条不执行）。**如实拒收 1 张**：Archeops（「devolve 1 of your opponent's evolved Pokémon」需退化机制，引擎只有正向进化 `evolve_to`，反向要重建物种与附着关系）。

### 第 103 批 · 能量代价 / 提供规则（3 张）· ✅ 已完成

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Mega Charizard X ex | PFL | 13 | B（有具体缺口·可归族） |  |
| Mega Eelektross ex | ASC | 61 | done（已完成） |  |
| Dialga | MEG | 95 | B（有具体缺口·可归族） |  |

**实际结果**：进池 **1** 张，如实拒收 **2** 张。

> **部分完成（1 张进池 / 2 张如实拒收）· 第一百三十五批（S1b）**：可选能量代价落成决策分解 `choose:optional_energy_cost:<n>:<type>:then:<status>` —— 挂起 confirm，付不付由牌手决定；付不出时记 `optional_unpaid` 日志且**不**施加后续效果。Mega Eelektross ex 因此进池；其余卡仍缺各自的 choice kind（多目标分配 / 跨区选择 / coin 嵌套），属 S1c/S5。

### 第 104 批 · 回复范围 / 全量回复（3 张）· ✅ 已完成

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Hydrapple ex | SCR | 14 | done（已完成） |  |
| Magearna | JTG | 107 | done（已完成） |  |
| Bianca’s Devotion | TEF | 142 | done（已完成） |  |

**实际结果**：进池 **3** 张，如实拒收 **0** 张。

> **落地 3 张**：Hydrapple ex（`attach_energy:1:grass:hand:target:any:heal:30` —— 附能后回复**同一目标**，`:heal:` 挂在 attach tag 上保住了「that Pokémon」的指代）、Magearna（`passive:heal_on_attach:90` —— 手牌附能时的被动钩子，持有者须在战斗位）、Bianca's Devotion（`heal:all:choose:maxleft:30` —— `heal:all` 新增 `:maxleft:` 目标上限，超出时引擎回 0 而不是照常回满）。

### 第 105 批 · 区分基本 / 特殊能量（2 张）· ✅ 已完成

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Enhanced Hammer | TWM | 148 | done（已完成） |  |
| Megaton Blower | SSP | 182 | B（有具体缺口·可归族） |  |

**实际结果**：进池 **1** 张，如实拒收 **1** 张。

> **落地 1 张**：Enhanced Hammer（`discard_energy:1:opp:special:any`）——`senergy` 是**卡**列表，引擎本就能区分特殊/基本；缺的是训练家侧出口：该前缀此前只在 `ATTACK_TAG_PREFIXES`，训练家白名单没有 → `build_trainer` 整卡拒收（进池后还会报「未知训练家 tag」）。**如实拒收 1 张**：Megaton Blower（同时弃**全部**道具 + **全部**特殊能量 + 一张场地，需复合训练家 tag）。

### 第 106 批 · 场地形态（S3 扩展）（2 张）· ✅ 已完成

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Dizzying Valley | PFL | 88 | done（已完成） |  |
| Nighttime Mine | ASC | 197 | done（已完成） |  |

**实际结果**：进池 **2** 张，如实拒收 **0** 张。

> **落地 2 张**：Nighttime Mine（`stadium:tera_cost_up:1` —— 场地抬高**双方** Tera 宝可梦招式 `{C}` 费用，接进 `families.attack_cost`）、Dizzying Valley（`stadium:keep_confuse` —— 进化时按场地**保留混乱**；`evolve_to` 会清空全部状态，故先记再补）。

### 第 107 批 · 对手手牌禁打层（3 张）· ✅ 已完成

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Genesect | SFA | 40 | done（已完成） |  |
| Chi-Yu | MEG | 31 | done（已完成） |  |
| Scream Tail ex | TWM | 94 | done（已完成） | 跨批 → 108 |

**实际结果**：进池 **3** 张，如实拒收 **0** 张。

> **落地 3 张**：Genesect（`fx:play_block:acespec:tool` —— 禁打层新增 ACE SPEC 类别 + 「持有者贴道具」生效时机）、Chi-Yu（`discard_stadium` + `play_lock:stadium:opp:turn:if_prev` —— 招式侧新增弃场地，且**没弃到就不封锁**）、Scream Tail ex（`play_lock:supporter:opp:turn` + `use_only:went_second_first_turn`）。新增 3 个手牌禁打字段（supporter/stadium/acespec）与招式**静态使用条件** `use_only:`/`use_not:`。⚠ ACE SPEC 当前数据集一张都没有 → 该锁定恒不生效，这是**有前提的等价**，由 `test_batch107_engine` 的数据集守卫钉住。

### 第 108 批 · 首回合 / 招式静态条件（2 张）· ✅ 已完成

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Terapagos ex | SCR | 128 | done（已完成） |  |
| Scream Tail ex | TWM | 94 | done（已完成） | 跨批 → 107 |

**实际结果**：进池 **2** 张，如实拒收 **0** 张。

> **落地 2 张**：Terapagos ex（`use_not:went_second_first_turn` + 既有 `shield:` / `passive:bench_immune_dmg`）、Scream Tail ex（同上 `use_only:`，跨批与 107 共用）。

### 第 109 批 · 打出前置 gate（2 张）· ✅ 已完成

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Team Rocket's Archer | DRI | 170 | done（已完成） |  |
| Rosa's Encouragement | POR | 84 | done（已完成） |  |

**实际结果**：进池 **2** 张，如实拒收 **0** 张。

> **落地 2 张**：Team Rocket's Archer（`shuffle_draw_split:5:3` + 新门槛 `own_ko_tr_last_turn` —— 只统计 **Team Rocket's** 宝可梦被击倒，不能并进 `own_ko_last_turn`，否则门槛被放宽）、Rosa's Encouragement（`attach:2:any:discard:target:stage2` + 既有 `own_behind_prizes` 门槛；训练家侧此前**没有** `attach:` 派发分支）。

### 第 110 批 · 抽牌分支 / 延迟队列（3 张）· ✅ 已完成

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Team Rocket's Ariana | DRI | 171 | done（已完成） |  |
| Amarys | PRE | 93 | done（已完成） |  |
| Lucian | TWM | 157 | done（已完成） | 跨批 → 100 |

**实际结果**：进池 **1** 张，如实拒收 **2** 张。

> **落地 1 张**：Team Rocket's Ariana（`draw_to_branch:5:8:if:own_all_name:teamrockets`）——新增「分支替换的抽到 N 张」：两条 `draw_to` 叠加与卡面等价（只补不弃），不需要回合状态。**如实拒收 2 张**：Amarys（回合末延迟弃手牌，需延迟队列）、Lucian（抛币嵌套抽牌）。

### 第 111 批 · 状态 / 免疫类 / 特性代价（4 张）· ✅ 已完成

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Pecharunt ex | SFA | 39 | done（已完成） |  |
| Hoothoot | PRE | 77 | done（已完成） |  |
| Mismagius ex | PFL | 36 | done（已完成） |  |
| Lunatone | MEG | 74 | done（已完成） |  |

**实际结果**：进池 **3** 张，如实拒收 **1** 张。

> **落地 3 张**：Pecharunt ex（`switch_choose:darkness:except:pecharunt:then:poison` —— 带属性/例外筛选的换位 + 换入后状态，目标由 `action["bench"]` 指定）、Hoothoot（`passive:status_immune:sleep` —— 状态免疫按**具体状态**判定，`status=None` 时只有 `all` 形态算数）、Lunatone（`draw:3:if:own_field_name:solrock` + 代价 `discard_hand_energy:1:fighting` —— 代价句从「只认 must」扩到 `may`，并新增具名在场条件）。**如实拒收 1 张**：Mismagius ex（「对手战斗位**撤退到后备**时，其新战斗位混乱」需对手撤退事件钩子）。

---

## 5. 阶段 P4 · D 档「差最后一块」（第 112–120 批，9 批，39 槽位）

杠杆最高：多数子句已能单独产 tag，前面族就位后会批量收敛。 已完成 7/9 批。

### 第 112 批 · 自身弃能代价（5 张）· ✅ 已完成

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Kyurem | SFA | 47 | done（已完成） |  |
| Klinklang | SCR | 101 | D（兜底·复合句） |  |
| Rotom ex | PFL | 29 | D（兜底·复合句） |  |
| Slaking ex | SSP | 147 | done（已完成） |  |
| Core Memory | POR | 70 | D（兜底·复合句） |  |

**实际结果**：进池 **2** 张，如实拒收 **3** 张。

> **落地 2 张**：Kyurem（`fx:attack_cost:self_set:1:colorless:name:trifrost:if:opp_discard_has_named:colress` —— 费用**整体改写**，新增 `self_set` 与对手弃牌堆具名计数）、Slaking ex（`passive:attack_block_unless:opp_has_ex_or_v` —— 新增「整只不能出招」被动）。**如实拒收 3 张**：Klinklang（「在手牌时直接放到后备」需手牌→场上动作）、Rotom ex（「同名宝可梦可贴 2 个道具」需道具上限字段）、Core Memory（「道具让附着者获得招式」需道具授予招式层）。

### 第 113 批 · 自伤（2 张）· ✅ 已完成

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Azumarill | SSP | 74 | done（已完成） |  |
| Eevee | SSP | 143 | done（已完成） |  |

**实际结果**：进池 **2** 张，如实拒收 **0** 张。

> **落地 2 张**：Azumarill（`fx:attack_cost:self_set:1:psychic:name:doubleedge:if:tera_in_play`）、Eevee（`passive:evolve_same_turn:active` —— 当回合/首回合进化，限定战斗位）。

### 第 114 批 · 自身治疗 / 自身状态（2 张）· ✅ 已完成

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Zarude | SSP | 11 | D（兜底·复合句） |  |
| Conkeldurr | TWM | 105 | done（已完成） |  |

**实际结果**：进池 **1** 张，如实拒收 **1** 张。

> **落地 1 张**：Conkeldurr（招式内 `fx:attack_cost:self_set:0:colorless:if:own_active_status_any` —— 引擎 `attack_cost` 新增读取**招式自身 effects** 的来源）。**如实拒收 1 张**：Zarude（「可把自身全部能量收回手牌，使本招 +80」= 可选代价与效果耦合，需交互层）。

### 第 115 批 · 静态伤害缩放 D 组（7 张）· ✅ 已完成

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Scovillain ex | SSP | 37 | D（兜底·复合句） |  |
| Lycanroc | JTG | 85 | done（已完成） |  |
| Golduck | ASC | 40 | D（兜底·复合句） |  |
| Gengar | POR | 50 | D（兜底·复合句） |  |
| Mega Mawile ex | MEG | 94 | done（已完成） |  |
| Team Rocket's Articuno | DRI | 51 | done（已完成） |  |
| Carbink | ASC | 117 | D（兜底·复合句） |  |

**实际结果**：进池 **1** 张，如实拒收 **6** 张。

> **落地 1 张**：Mega Mawile ex（`dmg_set:80:per:own_prizes_taken` + `dmg_set:30:if:opp_active_has_damage` —— 新增**条件基础伤害**）。**如实拒收 6 张**：Scovillain ex / Carbink（`Double Type` 双属性改写需类型层）、Lycanroc（进化触发贴 Spiky 能量需特殊能量按名附着）、Golduck（Damp：屏蔽「自爆型」特性需特性属性标记）、Gengar（被击倒时回手需击倒后去向改写）、Team Rocket's Articuno（拦「攻击效果」而非伤害，需效果免疫层）。

### 第 116 批 · 对手下回合限制（6 张）· ✅ 已完成

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Sylveon ex | SSP | 86 | D（兜底·复合句） |  |
| Team Rocket's Moltres ex | DRI | 31 | D（兜底·复合句） |  |
| Ethan's Sudowoodo | DRI | 93 | done（已完成） |  |
| Walrein | SSP | 45 | done（已完成） |  |
| Palafin ex | TWM | 61 | done（已完成） |  |
| Revavroom ex | SFA | 15 | D（兜底·复合句） |  |

**实际结果**：进池 **1** 张，如实拒收 **5** 张。

> **部分完成（1 张进池 / 5 张如实拒收）**：Walrein 已进池 —— 「下回合 ≤2 能量的宝可梦不能出招」落成 `atk_energy_block:2`（**玩家级**、含之后上场者；逐只写会漏掉换上来的那只）。其余 5 张仍缺事件/延迟层：Sylveon ex（选 2 只后备洗回 + 上回合招式记忆）、TR Moltres ex（按 tag 筛能量 + `if_prev` 击倒）、Ethan's Sudowoodo（coin 嵌套的复制招式）、Palafin ex（登场限制，结构性）、Revavroom ex（自身离场动作）。

### 第 117 批 · 特殊进化 / 对手交互（3 张）· ⛔ 受阻 · 整批如实拒收

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Eevee ex | PRE | 75 | D（兜底·复合句） |  |
| Team Rocket's Persian ex | DRI | 150 | done（已完成） |  |
| Espeon ex | PRE | 34 | D（兜底·复合句） |  |

**实际结果**：进池 **0** 张，如实拒收 **3** 张。

> **整批受阻（0 张）**：Eevee ex（「可进化成任意 Eevee 系 ex」需进化链白名单层）、TR Persian ex（「看对手牌库顶 10 张并选其中一招使用」需跨牌库选择）、Espeon ex（`Amazez` 全体退化 —— 引擎只有正向 `evolve_to`，退化要重建物种与附着关系，与 102 批 Archeops 同一缺口）。

### 第 118 批 · 能量附着 / 移动（3 张）· ⛔ 受阻 · 整批如实拒收

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Cinderace | MEG | 28 | D（兜底·复合句） |  |
| Forretress | DRI | 140 | D（兜底·复合句） |  |
| Dedenne | POR | 29 | D（兜底·复合句） |  |

**实际结果**：进池 **0** 张，如实拒收 **3** 张。

> **整批受阻（0 张）**：Cinderace（`Explosiveness` 是开局上场规则，属结构性边界）、Forretress（「任意数量 {M} 能量在己方之间**自由分配**」需分配界面）、Dedenne（附着张数 = 对手全部能量数，且要自由分配给己方 {L} 宝可梦 —— 两个动态量叠加）。

### 第 119 批 · 牌库顶 / 底操作（3 张）· ✅ 已完成

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Fraxure | SFA | 45 | done（已完成） |  |
| Watchog | WHT | 73 | done（已完成） |  |
| Meloetta ex | BLK | 44 | D（兜底·复合句） |  |

**实际结果**：进池 **1** 张，如实拒收 **2** 张。

> **落地 1 张**：Watchog（`self_attack_set_next:240:name:hyperfang` —— 下回合**基础伤害改为**，与既有 `self_attack_plus_next` 分开，避免「240 变 80+240」）。**如实拒收 2 张**：Fraxure（`Unnerve` 拦「对手道具/支援者对本只的效果」，需效果免疫层）、Meloetta ex（`Debut Performance` 是「先攻首回合可出招」的规则豁免）。

### 第 120 批 · 杂项收尾（8 张）· ✅ 已完成

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Cetitan ex | DRI | 65 | done（已完成） |  |
| Team Rocket's Mewtwo ex | DRI | 81 | done（已完成） |  |
| Virizion | WHT | 10 | done（已完成） |  |
| Tyrantrum | POR | 45 | done（已完成） |  |
| Decidueye ex | POR | 12 | done（已完成） |  |
| Shiftry | TEF | 5 | D（兜底·复合句） |  |
| Iron Thorns ex | TWM | 77 | D（兜底·复合句） |  |
| Antique Sail Fossil | POR | 69 | done（已完成） |  |

**实际结果**：进池 **4** 张，如实拒收 **4** 张。

> **落地 3 张**：Team Rocket's Mewtwo ex（`passive:attack_block_unless:own_field_name_at_least:teamrocket:4`）、Virizion（`heal:equal` —— 回复量 = 本次实际伤害）、Decidueye ex（`fx:attack_cost:self_omit:colorless:if:opp_hand_exactly:4` —— 新增**恰好 N 张手牌**条件与「忽略某色费用」）。**如实拒收 5 张**：Cetitan ex / Antique Sail Fossil（拦对手道具效果）、Tyrantrum（条件 +150 HP，引擎刻意不支持条件式上限）、Shiftry（「选 3 只后备、其余洗回牌库」需选择界面）、Iron Thorns ex（`Initialization`：规则框宝可梦无特性 except Future，需 rule_box 判定扩到场地外）。 第一百三十四批补充：Tyrantrum 补 `passive:hp_plus:150:if:special_energy`（条件式 +HP，判据只依赖**本只**附着卡，故在 `max_hp` 现算、特殊能量被弃即回落）。

---

## 6. 阶段 P5 · C 档逐句判读 + 结构性建档（第 121–133 批，13 批，23 槽位）

兜底 reason 说不清缺什么，需人工判读；最后收尾。 已完成 10/13 批。

### 第 121 批 · 特殊进化条件（3 张）· ✅ 已完成

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Karrablast | BLK | 9 | done（已完成） |  |
| Shelmet | WHT | 8 | done（已完成） |  |
| Luxio | POR | 27 | done（已完成） |  |

**实际结果**：进池 **3** 张，如实拒收 **0** 张。

> **落地 3 张**：Karrablast / Shelmet（`passive:evolve_same_turn:if:own_field_name:shelmet|karrablast`）、Luxio（`…:if:opp_active_is_ex`）。全部复用本阶段新增的**当回合进化**被动（含首个回合，与 Forest of Vitality 的「except first turn」相反）。

### 第 122 批 · 揭示对手手牌（4 张）· ✅ 已完成

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Hoothoot | TEF | 126 | done（已完成） |  |
| Espurr | SSP | 84 | done（已完成） |  |
| Pidove | BLK | 71 | done（已完成） |  |
| Mandibuzz | WHT | 64 | C（兜底·单句） |  |

**实际结果**：进池 **3** 张，如实拒收 **1** 张。

> **部分完成（3 张进池 / 1 张拒收）**：Hoothoot / Espurr / Pidove 的「对手展示手牌」落成 `info:reveal_hand`。**改判理由（第一百三十四批）**：引擎是完美信息的，展示不改变任何状态 —— 旧口径「一律不认领」会让这三张卡永远在池外；现在落成**显式建模的等价**（引擎有派发与日志、对账可查），与「未建模当无事发生」可区分。Mandibuzz 仍缺「对手手牌 → 放到对手后备」的动作。

### 第 123 批 · 奖赏区操作（4 张）· ✅ 已完成

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| N’s Sigilyph | JTG | 64 | done（已完成） |  |
| Team Rocket's Blipbug | DRI | 15 | done（已完成） |  |
| Redeemable Ticket | JTG | 156 | done（已完成） |  |
| Team Rocket's Bother-Bot | DRI | 172 | done（已完成） |  |

**实际结果**：进池 **1** 张，如实拒收 **3** 张。

> **部分完成（1 张进池 / 3 张拒收）**：N's Sigilyph「Victory Symbol」落成 `win_if:own_prizes_eq:1`（恰好剩 1 张奖赏时使用即获胜；条件不成立时本招**什么都不做**，不得按普通招式结算）。其余 3 张仍需：对手牌库顶 10 张里取招式（TR Persian ex）、翻阅对手面朝下奖赏卡（Blipbug / Bother-Bot）、奖赏卡洗回牌库重发（Redeemable Ticket）。

### 第 124 批 · 能量提供规则（3 张）· ⛔ 受阻 · 整批如实拒收

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Boomerang Energy | TWM | 166 | done（已完成） |  |
| Meganium | MEG | 10 | C（兜底·单句） |  |
| Smeargle | PFL | 80 | C（兜底·单句） |  |

**实际结果**：进池 **0** 张，如实拒收 **3** 张。

> **整批受阻（0 张）**：Boomerang Energy（被招式效果弃置时回到手牌，需特殊能量「离场后去向」钩子）、Meganium（基础 {G} 能量提供 {G}{G}，需能量提供倍率层）、Smeargle（抛 3 币、按正面数从弃牌堆附着并自由分配）。

### 第 125 批 · 牌库底 / 顶检视（3 张）· ✅ 已完成

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Misty's Psyduck | DRI | 45 | done（已完成） |  |
| Dusk Ball | SSP | 175 | done（已完成） |  |
| Gumshoos | MEG | 110 | done（已完成） |  |

**实际结果**：进池 **2** 张，如实拒收 **1** 张。

> **部分完成（1 张进池 / 2 张拒收）**：Dusk Ball 落成 `recon_bottom:7`（看**牌库底** 7 张取 1 张宝可梦，其余洗回；与 `recon:` 的牌库顶方向相反，前缀不互相覆盖）。Misty's Psyduck（弃牌库底 + 自身放回牌库顶）与 Gumshoos（手牌与牌库顶互换）仍需「牌库底」写入动作 + 选择层。 **S1c 补记**：Gumshoos「Evidence Gathering」已由决策分解表达（`choose:swap_hand_top`，选哪张手牌由牌手定）。

### 第 126 批 · 道具触发式（2 张）· ✅ 已完成

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Heavy Baton | TEF | 151 | done（已完成） |  |
| Ruffian | JTG | 157 | done（已完成） |  |

**实际结果**：进池 **1** 张，如实拒收 **1** 张。

> **部分完成（1 张进池 / 1 张拒收）**：Ruffian 落成 `discard_tool_senergy:1` —— **同一只**身上同时弃道具与特殊能量，缺一即不做（拆成两次挑选会变成「可跨两只」，静默加强）。Heavy Baton 仍需道具 `on_ko` 钩子 + 撤退费=4 的持有者限定。

### 第 127 批 · 条件费用 / 特性触发（2 张）· ✅ 已完成

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Crawdaunt | MEG | 85 | done（已完成） |  |
| Latios | MEG | 101 | done（已完成） |  |

**实际结果**：进池 **1** 张，如实拒收 **1** 张。

> **落地 1 张**：Crawdaunt（招式内 `fx:attack_cost:self_set:1:darkness:if:own_active_has_damage`）。**如实拒收 1 张**：Latios（「Mega Latias ex 从后备换到战斗位时」触发，需对手/己方换位事件钩子）。

### 第 128 批 · 延迟到回合末（1 张）· ⛔ 受阻 · 整批如实拒收

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Team Rocket's Grimer | DRI | 123 | done（已完成） |  |

**实际结果**：进池 **0** 张，如实拒收 **1** 张。

> **整批受阻（0 张）**：Team Rocket's Grimer「对手下回合**结束时**弃掉守方及全部附着卡」需**延迟结算队列**（本批主题即「延迟到回合末」）。

### 第 129 批 · 条件 +HP（单只）（1 张）· ⛔ 受阻 · 整批如实拒收

| 卡 | series | number | 分层 | 备注 |
|---|---|---|---|---|
| Okidogi | TWM | 111 | C（兜底·单句） |  |

**实际结果**：进池 **0** 张，如实拒收 **1** 张。

> **整批受阻（0 张）**：Okidogi 的 `+100 HP` 是**条件式**上限（有 {D} 能量时）。引擎刻意不支持条件式 `max_hp`（`max_hp` 被击倒/奖赏/前端广泛使用且拿不到 battle 上下文），只有无条件 `passive:hp_plus` 与场地重算两档；本批正是「条件 +HP」这一缺口的建档点。

### 第 130 批 · 结构性建档 · 全队 +HP（Ludicolo / Cynthia's Power Weight）（0 张）· ✅ 已完成

本批不承载卡清单，只做结构性边界建档（见 §7）。

**实际结果**：进池 **0** 张，如实拒收 **0** 张。

> **结构性建档（无卡）**：全队 +HP（Ludicolo / Cynthia's Pokémon 一族）。引擎只有**单只**无条件 `passive:hp_plus`，全队条件式上限需重写 `max_hp` 求值链（同 129）。

### 第 131 批 · 结构性建档 · 指示物翻倍（N's Vanilluxe）（0 张）· ✅ 已完成

本批不承载卡清单，只做结构性边界建档（见 §7）。

**实际结果**：进池 **0** 张，如实拒收 **0** 张。

> **结构性建档（无卡）**：指示物翻倍（N's Vanilluxe）。引擎所有指示物放置点都是 `damage += n`，翻倍需统一乘子在**放置入口**而非逐处改。

### 第 132 批 · 结构性建档 · 全局规则层（Psyduck / Milotic）（0 张）· ✅ 已完成

本批不承载卡清单，只做结构性边界建档（见 §7）。

**实际结果**：进池 **0** 张，如实拒收 **0** 张。

> **结构性建档（无卡）**：全局规则层（Misty's Psyduck / Milotic）。需要「场上存在即改写某类结算」的接管点，当前只有 `fx:` 静态族覆盖了费用/免伤/禁打三类。

### 第 133 批 · 结构性建档 · gate 类（Anthea & Concordia）+ 边界收尾（0 张）· ✅ 已完成

本批不承载卡清单，只做结构性边界建档（见 §7）。

**实际结果**：进池 **0** 张，如实拒收 **0** 张。

> **结构性建档（无卡）**：gate 类（Anthea & Concordia）+ 边界。该卡需要「多条件组合门槛」承载字段；池外 Tera 规则（PalossandEX / Miraidon ex / Koraidon ex）同属结构性边界。

---

## 7. 结构性边界与待新能力（永久池外，需引擎重构）

这 6 张不是「能力不足」，而是**引擎架构限制**：强行建模会产生静默错映射。**不为它们造假 tag**，在 DB 标 `engine_status="structural_limit"`，并在文档与 UI 标注为「已知边界」，使 backlog 读数可解释地停在这里。

| 卡 | series | number | 引擎限制 |
|---|---|---|---|
| Ludicolo | JTG | 37 | 全队 +HP：`_refresh_stadium_hp` 仅回合开始/场地变更时重算，场上可改变 `max_hp` 的约 30 处变更无统一入口 → 会延迟/残留 |
| Psyduck | ASC | 39 | 需要全局规则层（场上所有宝可梦失去特性） |
| Milotic | TWM | 50 | 需要全局规则层（对手区域流转禁止） |
| Anthea & Concordia | ASC | 182 | 6 张具名宝可梦 gate 未建模；`_split_play_gate` 不认识会静默丢弃 → 会变成无条件 +3 奖赏 |

> 要真正做到 100%，需三项独立工程（各自立项，不属本档案）：
> ① 拆分 `fp.damage` 为 `attack_damage` + `damage_counters`；② 统一 `max_hp` 重算入口；③ 新增全局规则层（特性禁用 / 区域流转）。

**待新能力（2 张）**：效果为回合末 / 被击倒**触发式**，不是 `tool:` 常驻，通过道具检查会让它在构筑期无条件生效（静默加强）→ 必须保持拒收，直到 `TrainerDef` 具备触发式承载字段。

| 卡 | series | number | 结论 |
|---|---|---|---|

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
| 96 | P2 | `any number of` 自由数量 | 9 | 1 | 8 | ✅ 完成 |
| 97 | P2 | 指示物目标 / 条件 | 7 | 4 | 3 | ✅ 完成 |
| 98 | P3 | 附加伤害目标扩展 | 3 | 2 | 1 | ✅ 完成 |
| 99 | P3 | 检索去向 / 数量由计数定 | 6 | 4 | 2 | ✅ 完成 |
| 100 | P3 | 抛币嵌套子效果 | 3 | 1 | 2 | ✅ 完成 |
| 101 | P3 | 受击历史 `on_damaged` | 3 | 3 | 0 | ✅ 完成 |
| 102 | P3 | 特殊进化驱动 / 进化触发特性 | 2 | 1 | 1 | ✅ 完成 |
| 103 | P3 | 能量代价 / 提供规则 | 3 | 1 | 2 | ✅ 完成 |
| 104 | P3 | 回复范围 / 全量回复 | 3 | 3 | 0 | ✅ 完成 |
| 105 | P3 | 区分基本 / 特殊能量 | 2 | 1 | 1 | ✅ 完成 |
| 106 | P3 | 场地形态（S3 扩展） | 2 | 2 | 0 | ✅ 完成 |
| 107 | P3 | 对手手牌禁打层 | 3 | 3 | 0 | ✅ 完成 |
| 108 | P3 | 首回合 / 招式静态条件 | 2 | 2 | 0 | ✅ 完成 |
| 109 | P3 | 打出前置 gate | 2 | 2 | 0 | ✅ 完成 |
| 110 | P3 | 抽牌分支 / 延迟队列 | 3 | 1 | 2 | ✅ 完成 |
| 111 | P3 | 状态 / 免疫类 / 特性代价 | 4 | 3 | 1 | ✅ 完成 |
| 112 | P4 | 自身弃能代价 | 5 | 2 | 3 | ✅ 完成 |
| 113 | P4 | 自伤 | 2 | 2 | 0 | ✅ 完成 |
| 114 | P4 | 自身治疗 / 自身状态 | 2 | 1 | 1 | ✅ 完成 |
| 115 | P4 | 静态伤害缩放 D 组 | 7 | 1 | 6 | ✅ 完成 |
| 116 | P4 | 对手下回合限制 | 6 | 1 | 5 | ✅ 完成 |
| 117 | P4 | 特殊进化 / 对手交互 | 3 | 0 | 3 | ⛔ 受阻 |
| 118 | P4 | 能量附着 / 移动 | 3 | 0 | 3 | ⛔ 受阻 |
| 119 | P4 | 牌库顶 / 底操作 | 3 | 1 | 2 | ✅ 完成 |
| 120 | P4 | 杂项收尾 | 8 | 4 | 4 | ✅ 完成 |
| 121 | P5 | 特殊进化条件 | 3 | 3 | 0 | ✅ 完成 |
| 122 | P5 | 揭示对手手牌 | 4 | 3 | 1 | ✅ 完成 |
| 123 | P5 | 奖赏区操作 | 4 | 1 | 3 | ✅ 完成 |
| 124 | P5 | 能量提供规则 | 3 | 0 | 3 | ⛔ 受阻 |
| 125 | P5 | 牌库底 / 顶检视 | 3 | 2 | 1 | ✅ 完成 |
| 126 | P5 | 道具触发式 | 2 | 1 | 1 | ✅ 完成 |
| 127 | P5 | 条件费用 / 特性触发 | 2 | 1 | 1 | ✅ 完成 |
| 128 | P5 | 延迟到回合末 | 1 | 0 | 1 | ⛔ 受阻 |
| 129 | P5 | 条件 +HP（单只） | 1 | 0 | 1 | ⛔ 受阻 |
| 130 | P5 | 结构性建档 · 全队 +HP（Ludicolo / Cynthia's Power Weight） | 0 | 0 | 0 | ✅ 完成 |
| 131 | P5 | 结构性建档 · 指示物翻倍（N's Vanilluxe） | 0 | 0 | 0 | ✅ 完成 |
| 132 | P5 | 结构性建档 · 全局规则层（Psyduck / Milotic） | 0 | 0 | 0 | ✅ 完成 |
| 133 | P5 | 结构性建档 · gate 类（Anthea & Concordia）+ 边界收尾 | 0 | 0 | 0 | ✅ 完成 |

> 台账的「计划张数」只计**卡槽**；第 130–133 批是结构性建档批（0 槽），涉及的 4 张统一列在 §7，不重复计入槽位。

**口径核对**：

| 项 | 张数 | 说明 |
|---|---|---|
| 批次槽位合计 | 140 | 跨批卡在每批各记 1 次 |
| 批次唯一卡 | 136 | 其中已完成 95 张 |
| 批次内待做 | 41 | 全部未 ready |
| 结构性边界 | 4 | 不承载 tag，仅建档 |
| 待新能力 | 0 | 需新增承载字段 |
| **合计（= 未 ready 卡数）** | **45** | 应等于基线 45 |

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

