# 引擎 / 卡库补漏报告（2026-11-09 实测）

> 口径：一切数字都从**代码与数据现算**得出，不从文档抄。复现命令写在每节末尾。
> 用途有两个：① 对外披露「哪些卡还不能打、为什么」；② 作为实现队列 —— 每一项都
> 写清「缺什么能力、影响多少张、怎么算做完」。

## 一、总览

| 项 | 实测 | 说明 |
|---|---|---|
| 卡库总量 | **1267 张** | `cards_challenge.json`（清洗后全量） |
| 引擎可对战（严格口径） | **1267 张 · 100%** | `engine_ready=true`，与 `GET /cards/stats` 同源 |
| 引擎卡池条目 | **1252 条** | `packages/engine/engine/data/cards_pool_ext.json` |
| 未进池 | **0 张** | F1–F8 全部清零（累计 45 张从不进池转为可对战） |
| 解析对账 | **57 类全绿** | `scripts/audit_pool_alignment.py` |
| 规则一致性矩阵 | **41 行（41 ✅ / 0 ⚠️）** | `docs/RULE_CONFORMANCE.md`（CI 机械核对） |
| 测试 | **2927**（engine 1641 / agent 1129 / api 145 / gym 12） | |

复现：

```bash
python packages/card-db/scripts/audit_pool_alignment.py          # 57 类
python scripts/check_conformance.py                              # 矩阵
python -c "import json;d=json.load(open('packages/card-db/data/cleaned/cards_challenge.json',encoding='utf-8'));cs=d['cards'];print(sum(1 for c in cs if c.get('engine_ready')),'/',len(cs))"
```

## 二、口径澄清（三个数字为什么不一样）

| 数字 | 含义 | 差异来源 |
|---|---|---|
| **1267** `engine_ready` | **严格口径**：卡真的能被 AI 选中并正确结算 | 对外只用这个 |
| **1252** 池条目 | 引擎侧的卡定义**条数**（同一张卡的多印刷各算一条） | 条目数 < 卡数的部分，是人工卡池已收录同一印刷时被去重 |
| **0** 未进池 | 解析链产生未建模动作 → 建池时**整卡不收** | 已清零（见第四节：F8 最后 2 张的落点） |

**为什么必须三个都披露**：只报 1252 会让人以为「池里有的都能打」（池条目与卡库
不是一一对应）；只报 1267 而不说 0，读者无从判断「剩下的是做不了还是没做」。
三个数一起给，差异才是可解释的。

复现：

```bash
python -c "import json;d=json.load(open('packages/card-db/data/cleaned/cards_challenge.json',encoding='utf-8'));cs=d['cards'];print(sum(1 for c in cs if c.get('engine_ready')),'/',len(cs))"
python -c "import json;print(len(json.load(open('packages/engine/engine/data/cards_pool_ext.json',encoding='utf-8'))['cards']))"
```

## 三、0 张未进池卡（F1–F8 全部清零）

> 卡库 **1267 张全部可对战**。这张表保留下来是因为它记录了「每一族缺的是**什么
> 能力**」—— 以后新卡池进来时，先按这张表对一遍比从零分析快得多。

| 族 | 曾缺什么能力 | 卡数（曾） | 现在由什么支撑 |
|---|---|---:|---|
| **F1 · Tera 备战位免伤** | `passive:bench_immune_dmg` | 6 | 建池转写（Tera 规则进 `passive:`） |
| **F2 · 退化 devolve** | 退回手牌/牌库、本体降级、当回合不能再进化 | 2 | `devolve:`（D3）+ `:each` / `:to:deck`（F8） |
| **F3 · 任意数量 / 自由分配** | 交互层：`任意张 + 任意分配` | 12 | `phase="choice"` + `pre:b4:` / `choose:alloc:`（B4） |
| **F4 · 多条件组合门槛** | `play_gate` 支持**合取** | 2 | `play_gate` 合取 + `all_in_play:`（D2） |
| **F5 · 静态属性改写** | 改弱点（带属性过滤）、能量单点增值 | 2 | `weak_change:<new>:<filter>` · `passive:energy_double:` |
| **F6 · 能力抑制 / 规则框** | 抑制「需自身击倒」的特性、抑制 Rule Box 特性 | 3 | `fx:ability_blocked:*`（D4） |
| **F7 · 道具 / 特殊能量形态** | 工具改费用+加伤、工具提供招式、全场弃道具+特殊能量 | 3 | 复合 `a\|b` · `TrainerDef.tool_attack` · `discard_tool_senergy_all:` |
| **F8 · 规则改写 / 逐卡机制** | 逐卡 | 13 | 见下表 |

### F8 · 已清零的十五张

| 卡 | 落成的 tag | 关键点 |
|---|---|---|
| Revavroom ex | `discard_self:all` | **不是击倒**：不取奖赏、不发 `ko` 事件；无替补则判负 |
| Espeon ex | `devolve:opp:1:each:to:deck` | `:each` 全退、`:to:deck` 洗回牌库 |
| Milotic | `passive:no_hand_return` | 回手与能量回手两处都收口 |
| Gengar | `passive:ko_to_hand` | 只对「被对手**招式**击倒」成立 |
| Klinklang | `hand_bench:opp_stage2` | 手牌发动直接铺后备 |
| Mandibuzz | `opp_hand:bench:basic_pokemon@hp70:1` | 铺到**对手**后备；`@hp` 限定不能丢 |
| Lillie's Ribombee | `evolve_act:opp_hand:bench:basic_pokemon:all` | 进化触发必须包 `evolve_act:` |
| Okidogi | `passive:energy_static:100:100:darkness` | 同一条件两段效果合成一条 |
| Kieran | `mode_pick:switch_self\|team_boost:30:cat:ex+v` | 两个模式**互斥**：legals 各列一条带 `mode` |
| Team Rocket's Moltres ex | `discard_named_energy:teamrockets:then:discard_defender` | 「If you do」是**条件后续**；弃置不是击倒 |
| Eevee ex | `passive:evolve_as:eevee:ex` | 绕过进化链，但只放行 `{ex}` |
| Wellspring Mask Ogerpon ex | `pre:b4:shuffle_energy:3:then:snipe:opp_bench:120:1` | 可选代价 + 「If you do」**门控**的附加狙击；没付→整段不存在 |
| Cinderace | `passive:setup_active` | 开局**从手牌面朝下上场**：setup 原本全自动，新增一次「you may」决策 |

## 四、最后 2 张的实现要点（F8b / F8c，2026-11-09）

### 1. Wellspring Mask Ogerpon ex「Torrential Pump」

卡面：`You may shuffle 3 Energy attached to this Pokémon into your deck. If you do,
this attack also does 120 damage to 1 of your opponent's Benched Pokémon.
(Don't apply Weakness and Resistance for Benched Pokémon.)`

四处错法都不报错：

| 错法 | 后果 |
|---|---|
| 只认后半句 → 落成裸 `snipe:opp_bench:120:1` | **无条件**打 120（白送一整段伤害） |
| 只认前半句 | 代价白付 / 卡面效果丢失 |
| 落成 `dmg_plus:120` | 变成打**战斗位**（目标全错） |
| 代价数字写错（3→2） | 代价变便宜，静默加强 |

实现：`_b4_open` 的 `shuffle_energy` 分支在**算伤害之前**问一次「付不付 + 打哪只」
（引擎不允许嵌套挂起，两件事必须在同一次选择里问完）；付了才由
`_apply_choice_result` 置 `_b4_then`，`_attack_resolve` 在主伤害与效果之后结算
`snipe:`。洗回顺序「基本能量先、特殊能量最后」是**支配解**（本池特殊能量都是
「基本 + 额外效果」的严格上位），与 `pre:attach_all_basic` 的论证同一套。

### 2. Cinderace「Explosiveness」

卡面：`If this Pokémon is in your hand when you are setting up to play, you may put
it face down in the Active Spot.`

这是**唯一**打破「setup 全自动」的卡：`_setup_openings` 原本直接挑第一只 Basic
上场，没有决策层。现在改为：

1. 常规铺场照旧做完（引擎任何时刻都有合法局面）；
2. 手里有 `passive:setup_active` 的卡 → 挂一次 `setup_active` 选择（双方各自
   一次，排队逐个问，不嵌套）；
3. 选「是」：这张成为战斗位，原战斗位那只进后备（后备满则回手）；
4. 选择全部闭合后才 `_finish_setup()`（发奖赏 / 抛硬币 / `_begin_turn` /
   `just_played`）——否则选择会落到回合中途。

面朝下（face down）本身**未建模**：本引擎在 setup 阶段双方同时摆场、信息对称，
翻面与否不影响任何决策与结算；这一点写在 `_setup_apply` 的注释里（属显式简化，
不是遗漏）。

## 五、非卡面缺口（规则层）

| 编号 | 缺口 | 现状 | 处置 |
|---|---|---|---|
| **D1** | mulligan 补偿是**自动抽**，官方为「可以抽」 | ✅ `mulligan_draw` 选择（对手决定抽 0..N 张） | **已完成（2026-11-09）**：复用 F8c 的 setup 决策队列，官方顺序「先摆场、后补抽」；矩阵 0 ⚠️ |
| **D2** | 多条件组合门槛 | ✅ `play_gate` 支持合取 + `all_in_play:` | 已完成 |
| **D3** | 退化 devolve | ✅ `FieldPokemon.under` + `devolve:<scope>:<n\|any>` | 已完成 |
| — | 任意数量交互层（F3） | ✅ `phase="choice"` + `pre:b4:` / `choose:alloc:` | 已完成（B4） |
| **F7** | 道具形态（复合静态 / 提供招式 / 全场弃牌） | ✅ 复合 `a\|b` + `TrainerDef.tool_attack` | 已完成 |
| **F5** | 静态属性改写 | ✅ `weak_change:<new>:<filter>` + `passive:energy_double:<type>` | 已完成 |
| **F8** | 规则/去向改写 + 逐卡机制 | ✅ `discard_self:` / `:each:to:deck` / `passive:no_hand_return` / `passive:ko_to_hand` / `hand_bench:` / `opp_hand:bench` / `passive:energy_static` / `mode_pick:` / `discard_named_energy:` / `passive:evolve_as:` / `pre:b4:shuffle_energy:` / `passive:setup_active` | **已清零（13 张）** |
| — | 效果施加路径唯一性（`fp.damage` 直写扫描） | 未做 AST 守卫 | 建议下一步 |
| — | 静态规则**禁令**族（「对手的卡不能回手」类） | ✅ `passive:no_hand_return`（回手 + 能量回手两处收口） | 已完成（F8 第一批） |

## 六、做完的判据（每族通用）

1. 卡进池（`engine_ready=true`，池条目 +N），并**加到 `docs/RULE_CONFORMANCE.md`**
   对应行（矩阵覆盖卡数会跟着变，写错 CI 红）；
2. 每条卡面语句都有 tag → 对账类目 +1（若引入新 tag 族）；
3. 正反向用例各一条（「能做到」+「不该做到时不做到」）；
4. **注入式负向自测**：把实现改坏，指定的用例必须变红（不红=假守卫）；
5. 计数守卫与 CI 8/8 通过。
