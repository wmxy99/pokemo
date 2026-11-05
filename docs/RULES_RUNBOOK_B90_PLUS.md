# 规则建模运行档案 · 第九十批起（权威执行依据）

> **本文件是第九十批及之后所有建模批次的唯一执行依据。**
> 每次开工前先读本文件对应批次行；完工后回填「实际结果」并更新状态列。
> 与 `RULES_COMPLETION_PLAN.md`（历史成果台账）分工：**计划看后者，执行看本文件**。

---

## 0. 基线（2026-11-05 **第九十二批完工后**实测，不得凭记忆填写）

| 指标 | 数值 | 取数方式 |
|---|---|---|
| 卡牌总数 | **1267** | `cards_challenge.json` 的 `cards` 长度 |
| `engine_ready` | **1129（89.1%）** | `sum(1 for c in cards if c.get("engine_ready"))` |
| 未建模卡 | **138** | 上者取反 |
| 引擎池 | **1114** | `cards_pool_ext.json` 的 `cards` 长度 |
| 对账类目 | **44 类全绿** | `audit_pool_alignment.py` |
| 测试总数 | **2351**（eng 1231 / agent 970 / card-db 275 / api 138 / gym 12） | 三包 `unittest discover` |
| CI | **8/8 PASS** | `scripts/ci_local.py`（必须用 `/d/anconda/envs/pokemo/python.exe`） |
| K1 双盲 | **0.795**（200 局，`artifacts/k1_rich.pt`，无回归） | `examples/k1_accept.py artifacts/k1_rich.pt --games 200` |

> 上表是**滚动基线**：每批完工后更新，开工前必须以它为准，不要沿用旧数字。

**总批次规划：第 90 批 – 第 133 批，共 44 批**（5 个阶段）。P1 已完成 3 批（90–92）。

**终局预期**：`engine_ready` **1259 / 1267 = 99.4%**（可做 130 张 + 结构性 6 张 + 待新能力 2 张 = 138，剩余 41 批）。
若后续补「道具触发式」承载能力（`Powerglass` / `Amulet of Hope`），可达 **1261 / 1267 = 99.5%**。

---

## 1. 每批固定工作流（7 步，缺一步视为未完成）

任何一批都必须按此顺序走完，**顺序不可调换**：

1. **建档**：读本文件对应批次行，把目标卡的 `(series, number)` 记下来（卡名有重名，如 Hoothoot 有 TEF 126 / PRE 77 两张）。
2. **查引擎现状**：先确认引擎是否已有该机制的钩子 —— 翻 `battle.py` 的派发点、`reallib._SUPPORTED_*_TAGS`、`gen_engine_pool` 白名单。**先翻引擎再猜句式**。
3. **改解析**（`packages/agent/agent/effects.py`）：产出 tag；判不出必须回 `None`，禁止兜底猜测。
4. **改引擎**（仅当引擎缺钩子）：新增派发分支；**同一 tag 的招式侧与训练家侧要分别检查**（`_apply_attack_effects` vs `_do_trainer`）。
5. **三处白名单同步**：`ATTACK_TAG_PREFIXES` / `ABILITY_TAG_PREFIXES` / `TRAINER_TAGS`（`gen_engine_pool.py`）+ `reallib._SUPPORTED_TRAINER_TAGS`。**漏一处 = 卡在池外凭空消失且对账全绿**。
6. **补测试（正向 + 负向各一条）**：
   - 解析测试：`packages/agent/tests/test_batchNN_*.py`
   - 引擎测试：`packages/engine/tests/test_batchNN_*.py`
   - 对账测试：`packages/card-db/tests/test_audit_alignment.py` 追加 `TestBatchNN`
   - **负向用例必须写**：错误句式 / 语义相反句式必须被判为未建模（防认领越界）。
7. **重跑链 + 验收**（顺序不可错）：
   ```
   import_challenge_dataset.py --reparse-only --parse-effects --parse-backend rule
   → gen_engine_pool.py
   → import_challenge_dataset.py --reparse-only
   → coverage_backlog.py --registry
   → audit_pool_alignment.py        # 必须 44 类全绿
   ```

**每批完工的门槛（DoD，全部满足才算完成）**：

- 目标卡在 `cards_pool_ext.json` 中以**正确 tag** 出现（逐卡打印核对，不认「产了 tag 的卡数」）
- `audit_pool_alignment.py` 无新增报错
- 三包测试全绿，测试计数同步进 README 第 7–8 行
- 若新增 tag 族：`apps/web/src/cardtext.js` 的 `TRAINER_TAG_DESC` 补中英说明（`node apps/web/e2e/cardtext_check.cjs` 必须通过）
- `git add` 后跑 `scripts/ci_local.py` **8/8 PASS**（redline 用 `git grep --cached`，不 add 会假绿）

---

## 2. 阶段 P1 · A 档：引擎已有，纯建池逻辑（第 90–92 批，3 批）· ✅ **已完成**

**实际结果（2026-11-05）**：3 批全部完工，**进池 7 张**（6 张目标 + `Duosion` 顺带解禁），
如实判定 2 张（`Powerglass` / `Amulet of Hope` 保持拒收）。`engine_ready` 1122 → **1129**，
零回归（无任何卡失去 ready），对账 44 类全绿，CI 8/8，K1 0.795 无回归。

**目标**：6 张进池 + 2 张如实判定。本阶段**不需要新引擎机制**。

### 第 90 批 · 被动 tag 归属修复（3 张）

| 卡 | series | number | 现状 |
|---|---|---|---|
| PalossandEX | SSP | 91 | `passive:bench_immune_dmg` 落在 **attack** item |
| Miraidon ex | ASC | 73 | 同上 |
| Koraidon ex | ASC | 121 | 同上 |

**根因（已核实，勿再按旧结论当"解析挂错"）**：上游数据集把 Pokémon ex 的 **Tera 规则文本**（`As long as this Pokémon is on your Bench, prevent all damage...`）错分类进 `attacks[]`。引擎读被动的位置是 `battle.py:10367` 的 `ab.tag`（**只认 ability**），`passive_tags` 白名单在 `battle.py:9659` 已含 `bench_immune_dmg`。

**修法**：`build_pokemon` 中，若 attack item 的 tag 全部以 `passive:` 开头，则**不写入 attacks.effects**，而是合成一条卡级被动条目（`tag=passive:*`, `location="field"`）交由引擎被动钩子消费。**禁止**把 `passive:*` 加进 `ATTACK_TAG_PREFIXES`（那是静默错位：攻击时触发被动）。

**验收**：3 张进池；`Miraidon ex` 的 `dmg_plus:120:if:opp_active_is_ex`、`Koraidon ex` 的 `attack_lock:name:turn:Impact Blow` 也一并进池；后备免伤在引擎测试中生效（对手 snipe 后备 → 伤害为 0）。

### 第 91 批 · 训练家白名单补（1 张）

| 卡 | series | number | tag | 缺口 |
|---|---|---|---|---|
| Deduction Kit | SSP | 171 | `shuffle_top:3:own` | `TRAINER_TAGS` 缺 `shuffle_top:`（该前缀当前只在 `ATTACK_TAG_PREFIXES` 第 187 行） |

**必查**：`_do_trainer` 是否有 `shuffle_top:` 派发分支 —— 若只在 `_apply_attack_effects`（`battle.py:6322`）有，必须给训练家侧补派发（memory 第 16 条同类陷阱：`energy_to_deck` 就是招式有、训练家没有）。

### 第 92 批 · 训练家多动作合成复合 tag（2 张）

| 卡 | series | number | 现产 tag | 问题 |
|---|---|---|---|---|
| Salvatore | TEF | 160 | `evolve_from_deck` + `candy` | 两条独立 tag → 建池按纪律整卡拒 |
| Larry's Skill | PRE | 115 | `discard:all` + `search_pokemon:1` | 同上 |

**修法**：解析侧合成**一条**复合 tag（参照 Wally's Compassion 的 `heal:all:mega:energy` 形态），引擎侧补对应派发。**不得**改成「只落第一条」（静默少做后半段）或「建池放行多 tag」（派发链只执行一条）。

### A 档的如实判定（不计入进池承诺）

| 卡 | series | number | 结论 |
|---|---|---|---|
| Powerglass | SFA | 63 | tag `attach:1:discard` 是**回合末触发式**，非 `tool:` 常驻 → 按 `build_trainer` 道具纪律**保持拒收**。若要收，须先给 TrainerDef 增「触发式道具」承载字段（另立批次，不在本阶段） |
| Amulet of Hope | SSP | 162 | 同上（`search_any:3` 实为「被击倒时」触发）→ **保持拒收** |

> **本节是对先前"2–3 批 8 张"估算的修正**：可承诺进池为 **6 张**，另 2 张需新增道具触发式承载能力才可做。不要为了让数字好看而放宽道具检查 —— 那是无条件生效的静默加强。

---

## 3. 阶段 P2 · B 档大族（第 93–97 批，5 批，28 张唯一卡）

### 第 93 批 · 静态伤害缩放：条件型（4 张）

| 卡 | series | number | 缺口 |
|---|---|---|---|
| Team Rocket's Kangaskhan ex | ASC | 162 | 条件伤害缩放：条件未识别 |
| Galvantula ex | SCR | 51 | 条件伤害缩放：条件未识别 |
| Pangoro | ASC | 140 | 条件伤害缩放：条件未识别（己方后备 Pancham 有指示物） |
| Walking Wake | TWM | 63 | 计数源未识别（本回合自己放的指示物） |

**修法方向**：扩展 `_eval_condition` 的条件名 + `dmg_set/dmg_plus:<n>:per:<source>` 的计数源枚举。**新增条件名必须让未知条件抛错**（`play_gate` 的既有纪律），不得静默放行。

### 第 94 批 · 静态伤害缩放：计数源型（5 张）

| 卡 | series | number | 计数源 |
|---|---|---|---|
| Zoroark | SFA | 32 | 对手 Pokémon ex 数量 |
| Misty's Gyarados | DRI | 49 | 本回合弃掉的 Misty's Pokémon |
| Mega Abomasnow ex | MEG | 36 | 弃掉的基本 {W} 能量 |
| Bronzong | PFL | 72 | 附在全部 Pokémon 上的 Pokémon Tool |
| Doublade | POR | 57 | 本方式揭示的卡（**跨批**，另需第 96 批的 any number） |

### 第 95 批 · 静态修正层（5 张）

| 卡 | series | number | 缺口 |
|---|---|---|---|
| Iron Crown ex | TEF | 81 | 静态伤害增减（需静态修正层） |
| Lillie's Clefairy ex | JTG | 56 | 静态规则修改（弱点/抗性/减伤） |
| Hop's Choice Band | JTG | 148 | 费用 -{C} 且 +30 伤害 |
| Kieran | TWM | 154 | 静态伤害增减 |
| Archaludon ex | SSP | 130 | 自由分配附着（句式未精确识别）+ 静态规则修改（下回合无弱点） |

**注意**：`fx` 层已有 `static_dmg_plus/minus`、`attack_cost`、`effect_immune` hooks（见 `engine/families/fx_*`）。**先复用**，不要写第二套。

### 第 96 批 · `any number of` 自由数量（9 张）

Alolan Exeggutor ex(SSP 133) · Alakazam(TWM 82) · Lillie's Ribombee(JTG 67) · Gorebyss(DRI 56) · Team Rocket's Honchkrow(ASC 127) · Accompanying Flute(TWM 142) · Strange Timepiece(MEG 128) · Naveen(POR 79) · Doublade(POR 57 跨批)

**这是本阶段风险最高的一批**：`any number of` 需要交互层（玩家自选数量）。现有引擎的 choose 类**一律确定性默认**。若无法在不引入交互层的前提下精确建模，**允许如实拒收并记录原因**，但必须先证明「确定性默认」会造成语义偏差（写成测试用例），不得直接跳过。

### 第 97 批 · 指示物目标 / 条件（7 张）

Feraligatr(TEF 41) · Sinistcha(TWM 22) · Team Rocket's Ampharos(DRI 74) · Team Rocket's Crobat ex(DRI 122) · Pachirisu(MEG 51) · Mismagius(ASC 86) · Team Rocket's Dugtrio(ASC 101)

**注意**：`Sinistcha` 是「任意分配含**战斗位**」，现有 `spread_free` 只覆盖后备 → 需扩目标域，不能压成后备。指示物与攻击伤害共用 `fp.damage` 的架构限制仍成立（见 §6），本批只做**放置**，不碰翻倍。

---

## 4. 阶段 P3 · B 档小族（第 98–111 批，14 批，37 张唯一卡）

| 批 | 主题 | 卡（series number） |
|---|---|---|
| 98 | 附加伤害目标扩展 | Shaymin(TEF 13) · Wellspring Mask Ogerpon ex(TWM 64) · Flygon ex(SSP 106) |
| 99 | 检索去向 / 数量由计数定 | Drilbur(TEF 85) · Palafin(TWM 60) · Lillie's Comfey(JTG 68) · Musharna(BLK 36) · Rotom(ASC 92) · Grumpig(MEG 63) |
| 100 | 抛币嵌套子效果 | Vanilluxe(WHT 29) · Team Rocket's Great Ball(DRI 175) · Lucian(TWM 157 跨) |
| 101 | 受击历史 `on_damaged` | Team Rocket's Koffing(DRI 125) · Mega Heracross ex(PFL 4) · Team Rocket's Hypnotizer(ASC 206) |
| 102 | 特殊进化驱动 / 进化触发特性 | ~~Duosion(BLK 38)~~（批 92 已进池）· Archeops(WHT 51) · Noctowl(SCR 115) |
| 103 | 能量代价 / 提供规则 | Mega Charizard X ex(PFL 13) · Mega Eelektross ex(ASC 61) · Dialga(MEG 95) |
| 104 | 回复范围 / 全量回复 | Hydrapple ex(SCR 14) · Magearna(JTG 107) · Bianca's Devotion(TEF 142) |
| 105 | 区分基本 / 特殊能量 | Enhanced Hammer(TWM 148) · Megaton Blower(SSP 182) |
| 106 | 场地形态（S3 扩展） | Dizzying Valley(PFL 88) · Nighttime Mine(ASC 197) |
| 107 | 对手手牌禁打层 | Genesect(SFA 40) · Chi-Yu(MEG 31) · Scream Tail ex(TWM 94 跨) |
| 108 | 首回合 / 招式静态条件 | Terapagos ex(SCR 128) · Scream Tail ex(TWM 94 跨) |
| 109 | 打出前置 gate | Team Rocket's Archer(DRI 170) · Anthea & Concordia(ASC 182) · Rosa's Encouragement(POR 84) |
| 110 | 抽牌分支 / 延迟队列 | Team Rocket's Ariana(DRI 171) · Amarys(PRE 93) · Lucian(TWM 157 跨) |
| 111 | 状态 / 免疫类 / 特性代价 | Pecharunt ex(SFA 39) · Hoothoot(PRE 77) · Mismagius ex(PFL 36) · Lunatone(MEG 74) |

**第 109 批特别警示（Anthea & Concordia）**：其 gate「6 张具名宝可梦在场」未建模。`_split_play_gate` 只剥它认识的 gate，**不认识的会留在 text 里**。认领前必须检查 `can use this card only if` 是否残留，残留即整卡拒收 —— 否则会变成**无条件 +3 奖赏的静默 buff**（第八十九批已实证此坑）。

**第 105 批注意**：引擎当前**不区分基本/特殊能量**。「弃置特殊能量」若压成「弃置任意能量」，会让玩家弃基本能量也满足条件 → 静默放宽。必须先在引擎补 `is_special` 判定，否则如实拒收。

---

## 5. 阶段 P4 · D 档「差最后一块」（第 112–120 批，9 批，39 张）

> **本阶段杠杆最高**：这 39 张的多数子句已能单独产 tag（实测 224 子句中 108 个可达），只差同卡上另一个子句。P2/P3 的机制族就位后，本阶段会批量收敛。

| 批 | 主题 | 卡 |
|---|---|---|
| 112 | 自身弃能代价 | Kyurem(SFA 47) · Klinklang(SCR 101) · Rotom ex(PFL 29) · Slaking ex(SSP 147) · Core Memory(POR 70) |
| 113 | 自伤 | Azumarill(SSP 74) · Eevee(SSP 143) |
| 114 | 自身治疗 / 自身状态 | Zarude(SSP 11) · Conkeldurr(TWM 105) |
| 115 | 静态伤害缩放 D 组 | Scovillain ex(SSP 37) · Lycanroc(JTG 85) · Golduck(ASC 40) · Gengar(POR 50) · Mega Mawile ex(MEG 94) · Team Rocket's Articuno(DRI 51) · Carbink(ASC 117) |
| 116 | 对手下回合限制 | Sylveon ex(SSP 86) · Team Rocket's Moltres ex(DRI 31) · Ethan's Sudowoodo(DRI 93) · Walrein(SSP 45) · Palafin ex(TWM 61) · Revavroom ex(SFA 15) |
| 117 | 特殊进化 / 对手交互 | Eevee ex(PRE 75) · Team Rocket's Persian ex(DRI 150) · Espeon ex(PRE 34) |
| 118 | 能量附着 / 移动 | Cinderace(MEG 28) · Forretress(DRI 140) · Dedenne(POR 29) |
| 119 | 牌库顶 / 底操作 | Fraxure(SFA 45) · Watchog(WHT 73) · Meloetta ex(BLK 44) |
| 120 | 杂项收尾 | Cetitan ex(DRI 65) · Team Rocket's Mewtwo ex(DRI 81) · Virizion(WHT 10) · Tyrantrum(POR 45) · Decidueye ex(POR 12) · Shiftry(TEF 5) · Iron Thorns ex(TWM 77) · Antique Sail Fossil(POR 69) |

**第 112 批必查**：`discard_energy:all` 与 `discard_energy:N` 是不同口径；`selfko` 早已存在（做代价前先搜引擎，别写第二套）。

**第 116 批必查**：`retreat_lock` / `attack_lock` / 减伤三者是不同字段，勿混用 `dmg_reduce` 与 `shield:reduce`。

---

## 6. 阶段 P5 · C 档 + 结构性收尾（第 121–133 批，13 批）

C 档 27 张的 reason 是解析器自己的兜底文案「静态/未建模（英文）」—— **它连自己缺什么都说不清**，需逐卡人工判读。届时先重测：部分已被 P2/P3 机制覆盖。

| 批 | 主题 | 卡 |
|---|---|---|
| 121 | 特殊进化条件 | Karrablast(BLK 9) · Shelmet(WHT 8) · Luxio(POR 27) |
| 122 | 揭示对手手牌 | Hoothoot(TEF 126) · Espurr(SSP 84) · Pidove(BLK 71) · Mandibuzz(WHT 64) |
| 123 | 奖赏区操作 | N's Sigilyph(JTG 64) · Team Rocket's Blipbug(DRI 15) · Redeemable Ticket(JTG 156) · Team Rocket's Bother-Bot(DRI 172) |
| 124 | 能量提供规则 | Boomerang Energy(TWM 166) · Meganium(MEG 10) · Smeargle(PFL 80) |
| 125 | 牌库底 / 顶检视 | Misty's Psyduck(DRI 45) · Dusk Ball(SSP 175) · Gumshoos(MEG 110) |
| 126 | 道具触发式 | Heavy Baton(TEF 151) · Ruffian(JTG 157) |
| 127 | 条件费用 / 特性触发 | Crawdaunt(MEG 85) · Latios(MEG 101) |
| 128 | 延迟到回合末 | Team Rocket's Grimer(DRI 123) |
| 129 | 条件 +HP（单只） | Okidogi(TWM 111) |
| 130–133 | **结构性不做：建档 + 明确标注** | 见下表 |

### 结构性不做（6 张，永久池外，需在文档与 UI 中标注为「已知边界」）

| 卡 | series | number | 引擎限制 |
|---|---|---|---|
| Ludicolo | JTG | 37 | 全队 +HP：`_refresh_stadium_hp` 仅在回合开始/场地变更重算，场上能改变 `max_hp` 的约 30 处变更无统一入口 → 会延迟/残留 |
| Cynthia's Power Weight | DRI | 162 | 同上（+70 HP） |
| N's Vanilluxe | ASC | 51 | 指示物翻倍：`fp.damage` 同时承载攻击伤害与指示物，翻倍会连攻击伤害一起翻 |
| Psyduck | ASC | 39 | 需要全局规则层（场上所有宝可梦失去特性） |
| Milotic | TWM | 50 | 需要全局规则层（对手区域流转禁止） |
| Anthea & Concordia | ASC | 182 | 6 张具名宝可梦 gate 未建模；`_split_play_gate` 不认识会静默丢弃 |

**处理方式**：不为这 6 张造假 tag。在 `RULES_COMPLETION_PLAN.md` 与 `unmodeled_registry.md` 中单列「已知结构性边界」小节，并在 DB 中标记 `engine_status="structural_limit"`，使 backlog 读数可解释地停在 6。

> 若要真正做到 100%，需要三项引擎重构（各自独立立项，不属本档案）：
> ① 拆分 `fp.damage` 为 `attack_damage` + `damage_counters`；② 统一 `max_hp` 重算入口；③ 新增全局规则层（特性禁用 / 区域流转）。

---

## 7. 跨批卡登记（勿重复计算进度）

| 卡 | 跨批 | 处理规则 |
|---|---|---|
| Doublade (POR 57) | 94 + 96 | 两批都改，**只有两族都就位才进池**；进度只记一次 |
| Scream Tail ex (TWM 94) | 107 + 108 | 同上 |
| Lucian (TWM 157) | 100 + 110 | 同上 |
| Feraligatr (TEF 41) | 95 + 97 | 同上 |

---

## 8. 偏差处理规则（严格执行，不得临场放宽）

1. **做不了就如实拒收**并记录原因，**不得**为了让卡进池而放宽白名单、压窄语义、或改测试期望值。
2. **静默错映射 > 未建模**。宁可卡在池外，也不上「不报错、测试全绿、只有卡面↔tag 对账看得出」的错映射。
3. **改存量测试前先判断**：该测试断言的是「旧口径」还是「纪律承重墙」。前者可改并注明原因；后者必须改代码。
4. **每批结束如实回填**本文件的「实际结果」列（进池张数 / 如实拒收张数 / 新增测试数 / CI 状态）。
5. 若某批实际收益低于规划，**不要用后续批次的卡来凑数** —— 按批次主题推进，宁可该批只进 1 张。
6. **负向自测必须真能变红**：写完守卫后注入错误写法跑一次，确认测试**失败**。
   P1 阶段实测两个「假守卫」（见 §8.5 第 4 条），若不验证会一直绿到线上。

---

## 8.5 P1 实证的陷阱（后续批次直接复用，别重踩）

1. **「招式有、训练家没有」已连续出现三处**：`energy_to_deck`（历史）、`shuffle_top`
   （批 91）、`evolve_from_deck`（批 92）。判定「引擎支持」时，`battle.py` 里出现一次
   **不算**——必须同时确认 `_do_trainer` 有分支、`TRAINER_TAGS` 有前缀、`reallib` 有登记。
2. **上游数据集会把卡面规则分类错**：Pokémon ex 的 **Tera 规则文本**被归进 `attacks[]`。
   修法是在建池侧转写为卡级被动，**禁止**把 `passive:*` 放进攻击白名单（= 出招才免伤的反转）。
3. **`_SUPPORTED_TRAINER_TAGS`（reallib.py:40）是死代码** —— 全仓仅定义、无任何引用。
   仍要与 `gen_engine_pool.TRAINER_TAGS` 同步（否则注释在撒谎），但**别指望它拦卡**；
   真正生效的是 `gen_engine_pool` 的那三处白名单。
4. **测试布局会让守卫变假**（P1 抓到两个）：
   - `_evolve_from_deck` 从**牌尾往前**扫 —— 若把「不该被选中」的卡放在数组前面，
     即使过滤失效也会选到正确的卡 → 守卫永远绿。必须把它放在**末尾**。
   - 混合 tag 的反例里若掺入**本就不在白名单**的前缀（如 `draw:`），整卡拒收的原因
     变成了那条非法前缀，掩盖「`passive:` 被误放行」→ 守卫永远绿。反例必须用**白名单内**前缀。
5. **`candy` 是 Rare Candy 的跳阶语义**，别把它当成「从牌库进化」的近似：
   Salvatore 被误产 `candy` 会让引擎把二阶卡直接放上去（跳过一阶）。判据是
   「同一 spec 内先 search(deck) 再 evolve」→ `evolve_from_deck`。
6. **`Basic Energy card` 的判定顺序**：`basic energy card` 必须排在 `energy card` **之前**，
   否则丢掉 basic 限定 → 连特殊能量一起检索（静默放宽）。

---

## 9. 进度台账（每批完工后回填）

| 批 | 阶段 | 主题 | 计划张数 | 实际进池 | 如实拒收 | 状态 |
|---|---|---|---|---|---|---|
| 90 | P1 | 被动 tag 归属 | 3 | **3** | 0 | ✅ 完成 |
| 91 | P1 | shuffle_top 训练家侧 | 1 | **1** | 0 | ✅ 完成 |
| 92 | P1 | 多动作复合 tag | 2 | **2 + 1 顺带** | 0 | ✅ 完成 |
| 93 | P2 | 伤害缩放·条件型 | 4 | | | 待办 |
| 94 | P2 | 伤害缩放·计数源 | 5 | | | 待办 |
| 95 | P2 | 静态修正层 | 5 | | | 待办 |
| 96 | P2 | any number of | 9 | | | 待办 |
| 97 | P2 | 指示物目标 | 7 | | | 待办 |
| 98 | P3 | 附加伤害目标 | 3 | | | 待办 |
| 99 | P3 | 检索去向/计数 | 6 | | | 待办 |
| 100 | P3 | 抛币嵌套 | 3 | | | 待办 |
| 101 | P3 | on_damaged | 3 | | | 待办 |
| 102 | P3 | 特殊进化驱动/进化触发 | 2 | | | 待办（Duosion 已由批 92 解禁） |
| 103 | P3 | 能量代价/提供 | 3 | | | 待办 |
| 104 | P3 | 回复范围 | 3 | | | 待办 |
| 105 | P3 | 基本/特殊能量区分 | 2 | | | 待办 |
| 106 | P3 | 场地形态 | 2 | | | 待办 |
| 107 | P3 | 手牌禁打层 | 3 | | | 待办 |
| 108 | P3 | 首回合静态条件 | 2 | | | 待办 |
| 109 | P3 | 打出前置 gate | 3 | | | 待办 |
| 110 | P3 | 抽牌分支/延迟 | 3 | | | 待办 |
| 111 | P3 | 状态/免疫/特性代价 | 4 | | | 待办 |
| 112 | P4 | 自身弃能代价 | 5 | | | 待办 |
| 113 | P4 | 自伤 | 2 | | | 待办 |
| 114 | P4 | 自身治疗/状态 | 2 | | | 待办 |
| 115 | P4 | 伤害缩放 D 组 | 7 | | | 待办 |
| 116 | P4 | 下回合限制 | 6 | | | 待办 |
| 117 | P4 | 特殊进化/交互 | 3 | | | 待办 |
| 118 | P4 | 能量附着/移动 | 3 | | | 待办 |
| 119 | P4 | 牌库顶底 | 3 | | | 待办 |
| 120 | P4 | 杂项收尾 | 8 | | | 待办 |
| 121 | P5 | 特殊进化条件 | 3 | | | 待办 |
| 122 | P5 | 揭示手牌 | 4 | | | 待办 |
| 123 | P5 | 奖赏区操作 | 4 | | | 待办 |
| 124 | P5 | 能量提供规则 | 3 | | | 待办 |
| 125 | P5 | 牌库顶底检视 | 3 | | | 待办 |
| 126 | P5 | 道具触发式 | 2 | | | 待办 |
| 127 | P5 | 条件费用/特性触发 | 2 | | | 待办 |
| 128 | P5 | 延迟回合末 | 1 | | | 待办 |
| 129 | P5 | 条件 +HP | 1 | | | 待办 |
| 130–133 | P5 | 结构性不做建档 | 6 | 0 | 6 | 待办 |

**合计口径（与 DB 实测对齐，已用脚本校验 145/145 全覆盖）**：

| 项 | 张数 |
|---|---|
| 可做（本档案 44 批覆盖） | **137** |
| 结构性不做（§6 末表） | **6** |
| 待新能力（Powerglass / Amulet of Hope） | **2** |
| **合计** | **145** |

台账「计划张数」列按**批次数**计（跨批卡在每批各记 1 次，故列和 = 149，比唯一卡数多 4，对应 §7 的 4 张跨批卡）。

---

## 10. 与历史文档的关系

- `docs/RULES_COMPLETION_PLAN.md`：历史成果台账（第八十五~八十九批及之前），**只追加，不改写**。
- 本文件：第九十批起的执行档案，**执行以本文件为准**。
- `packages/card-db/data/cleaned/unmodeled_registry.md`：机器生成的未建模清单，每次跑 `coverage_backlog.py --registry` 刷新，是**数据真相**；本文件的卡清单是**计划编排**，若两者冲突以 registry 为准并修正本文件。
