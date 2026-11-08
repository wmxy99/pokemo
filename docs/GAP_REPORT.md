# 引擎 / 卡库补漏报告（2026-11-08 实测）

> 口径：一切数字都从**代码与数据现算**得出，不从文档抄。复现命令写在每节末尾。
> 用途有两个：① 对外披露「哪些卡还不能打、为什么」；② 作为实现队列 —— 每一项都
> 写清「缺什么能力、影响多少张、怎么算做完」。

## 一、总览

| 项 | 实测 | 说明 |
|---|---|---|
| 卡库总量 | **1267 张** | `cards_challenge.json`（清洗后全量） |
| 引擎可对战（严格口径） | **1260 张 · 99.4%** | `engine_ready=true`，与 `GET /cards/stats` 同源 |
| 引擎卡池条目 | **1245 条** | `packages/engine/engine/data/cards_pool_ext.json` |
| 未进池 | **7 张** | 全部为「如实拒收」（D2/D3/D4/B4/F7/F8 累计让 38 张进池） |
| 解析对账 | **55 类全绿** | `scripts/audit_pool_alignment.py` |
| 规则一致性矩阵 | **39 行（38 ✅ / 1 ⚠️）** | `docs/RULE_CONFORMANCE.md`（CI 机械核对） |
| 测试 | **2856**（engine 1598 / agent 1104 / api 142 / gym 12） | |

复现：

```bash
python packages/card-db/scripts/audit_pool_alignment.py          # 55 类
python scripts/check_conformance.py                              # 矩阵
python -c "import json;d=json.load(open('packages/card-db/data/cleaned/cards_challenge.json',encoding='utf-8'));cs=d['cards'];print(sum(1 for c in cs if c.get('engine_ready')),'/',len(cs))"
```

## 二、口径澄清（三个数字为什么不一样）

| 数字 | 含义 | 差异来源 |
|---|---|---|
| **1260** `engine_ready` | **严格口径**：卡真的能被 AI 选中并正确结算 | 对外只用这个 |
| **1245** 池条目 | 引擎侧的卡定义**条数**（同一张卡的多印刷各算一条） | 条目数 < 卡数的部分，是人工卡池已收录同一印刷时被去重 |
| **7** 未进池 | 解析链产生未建模动作 → 建池时**整卡不收** | 逐卡原因见附录（每张只有一条） |

**为什么必须三个都披露**：只报 1245 会让人以为「池里有的都能打」（池条目与卡库
不是一一对应）；只报 1260 而不说 7，读者无从判断「剩下的是做不了还是没做」。
三个数一起给，差异才是可解释的。

复现：

```bash
python -c "import json;d=json.load(open('packages/card-db/data/cleaned/cards_challenge.json',encoding='utf-8'));cs=d['cards'];print(sum(1 for c in cs if c.get('engine_ready')),'/',len(cs))"
python -c "import json;print(len(json.load(open('packages/engine/engine/data/cards_pool_ext.json',encoding='utf-8'))['cards']))"
```

## 三、7 张未进池卡：按缺失能力分族

> 每张卡的缺口都是**唯一**一条：解析链逐句认领，认不出的那一句就如实登记，
> 整卡不进池。因此下面「卡数」可直接当作剩余工作量。

| 族 | 缺什么能力 | 卡数 | 工作量 | 状态 |
|---|---|---:|---|---|
| **F1 · Tera 备战位免伤** | `passive:bench_immune_dmg` | ~~6~~ **0** | S | **✅ 已落地（建池转写）** |
| **F2 · 退化 devolve** | 退回到手牌/牌库、本体降级、当回合不能再进化 | ~~2~~ **0** | M | **✅ 已落地（D3/F8）** |
| **F3 · 任意数量 / 自由分配** | 交互层：`任意张 + 任意分配` | ~~12~~ **0** | L | **✅ 已落地（B4）** |
| **F4 · 多条件组合门槛** | `play_gate` 支持**合取** | ~~2~~ **0** | M | **✅ 已落地（D2）** |
| **F5 · 静态属性改写** | **改弱点**、**能量增值 `{G}{G}`** | 2 | M | 待做 |
| **F6 · 能力抑制 / 规则框** | 抑制「需要自身击倒」的特性、抑制 Rule Box 宝可梦的特性 | ~~3~~ **0** | M | **✅ 已落地（D4）** |
| **F7 · 道具 / 特殊能量形态** | 工具改费用+加伤、工具提供招式、全场弃道具+特殊能量 | ~~3~~ **0** | M | **✅ 已落地（F7）** |
| **F8 · 规则改写 / 逐卡机制** | 逐卡见附录（代价门控狙击、模式二选一、具名能量代价、开局上场…） | 5 | M–L | 部分完成（已进 8 张） |

> 「工作量」S = 单点 tag + 对账即可；M = 需要新的小机制或交互形态；L = 需要通用
> 交互层（任意数量），改的是「引擎如何表达决策」，而不是某一族 tag。

### F5 · 静态属性改写（剩 2 张）

- **Lillie's Clefairy ex**（`Fairy Zone`）：`The Weakness of each of your opponent's
  {N} Pokémon in play is now {P}.` —— 场上**改弱点**。引擎有 `weak_change:<type>`，
  缺的是「限定对手 {N} 属性」这一层过滤（不限定 = 把对手全队的弱点都改了）。
- **Meganium**（`Wild Growth`）：`Each Basic {G} Energy attached to all of your
  Pokémon provides {G}{G}` —— **能量单点增值**。这不只是显示：`total_energy()`、
  招式费用校验、撤退费三处都要按增值后的点数算，任一处漏改就是「有时够有时不够」。

### F8 · 已清零的八张

| 卡 | 卡面 | 落成的 tag | 关键点 |
|---|---|---|---|
| Revavroom ex | `Discard this Pokémon and all attached cards.` | `discard_self:all` | **不是击倒**：不取奖赏、不发 `ko` 事件；无替补则判负 |
| Espeon ex | `Devolve each … into their deck` | `devolve:opp:1:each:to:deck` | `:each` 全退、`:to:deck` 洗回牌库（不是手牌） |
| Milotic | `… can't be put into your opponent's hand` | `passive:no_hand_return` | 收口在回手与能量回手两处（只挡本体是半个禁令） |
| Gengar | `… put it into your hand instead of the discard pile` | `passive:ko_to_hand` | 只对「被对手**招式**击倒」成立；仍是击倒 |
| Klinklang | `if this Pokémon is in your hand and … Stage 2` | `hand_bench:opp_stage2` | 手牌发动直接铺后备；门槛 + 后备空间两处都挡 |
| Mandibuzz | `… put a Basic Pokémon with 70 HP or less … onto your opponent's Bench` | `opp_hand:bench:basic_pokemon@hp70:1` | 铺到**对手**后备（不是弃、不是入手）；`@hp` 限定不能丢 |
| Lillie's Ribombee | `… you put any number of Basic Pokémon … onto their Bench` | `evolve_act:opp_hand:bench:basic_pokemon:all` | 进化触发必须包 `evolve_act:`（否则变成每回合一次的特性） |
| Okidogi | `… it gets +100 HP, and the attacks it uses do 100 more damage` | `passive:energy_static:100:100:darkness` | 同一条件两段效果合成一条 tag（拆开必有一处读取点漏读） |

## 四、7 张逐卡附录（缺口唯一一条，逐条可复核）

| 卡 | 卡面关键句（未建模的那一句） | 缺什么 | 族 |
|---|---|---|---|
| Wellspring Mask Ogerpon ex | `shuffle 3 Energy … If you do, this attack also does 120 damage to 1 of your opponent's Benched Pokémon` | **代价门控的附加狙击**（可选代价 + 已支付才追加伤害） | F8 |
| Eevee ex | `This Pokémon can evolve into any Pokémon {ex} that evolves from Eevee` | **规则改写**：绕过进化链 | F8 |
| Lillie's Clefairy ex | `The Weakness of each of your opponent's {N} Pokémon in play is now {P}` | 改弱点 + 属性过滤 | F5 |
| Team Rocket's Moltres ex | `Discard a Team Rocket's Energy from this Pokémon. If you do, discard your opponent's Active Pokémon and all attached cards.` | 具名能量代价 → **弃对手战斗位**（不取奖赏） | F8 |
| Cinderace | `If this Pokémon is in your hand when you are setting up to play, you may put it face down in the Active Spot` | **开局上场规则**：setup 现在是**全自动**铺场，没有「从手牌选谁上场」的决策层（结构性） | F8 |
| Meganium | `Each Basic {G} Energy attached to all of your Pokémon provides {G}{G}` | 能量**单点增值**（三处消耗点都要按增值算） | F5 |
| Kieran | `Choose 1: … switch … / … attacks do 30 more damage … {ex} and {V}` | 支援者**二选一模式** + 对 {ex}/{V} 的静态加伤 | F8 |

## 五、非卡面缺口（规则层）

| 编号 | 缺口 | 现状 | 处置 |
|---|---|---|---|
| **D1** | mulligan 补偿是**自动抽**，官方为「可以抽」 | 矩阵 ⚠️ 行 | 需要把 `_begin_turn()` 推迟到 setup 选择闭合之后；风险 > 收益，暂缓（做法已写在矩阵 D1） |
| **D2** | 多条件组合门槛 | ✅ `play_gate` 支持合取 + `all_in_play:` | 已完成 |
| **D3** | 退化 devolve | ✅ `FieldPokemon.under` + `devolve:<scope>:<n\|any>` | 已完成 |
| — | 任意数量交互层（F3） | ✅ `phase="choice"` + `pre:b4:` / `choose:alloc:` | 已完成（B4） |
| **F7** | 道具形态（复合静态 / 提供招式 / 全场弃牌） | ✅ 复合 `a\|b` + `TrainerDef.tool_attack` | 已完成 |
| **F8** | 规则/去向改写 + 逐卡机制 | ✅ `discard_self:` / `:each:to:deck` / `passive:no_hand_return` / `passive:ko_to_hand` / `hand_bench:` / `opp_hand:bench` / `passive:energy_static` | 已完成 8 张，剩 5 张（见附录） |
| — | 效果施加路径唯一性（`fp.damage` 直写扫描） | 未做 AST 守卫 | 建议下一步 |
| — | 静态规则**禁令**族（「对手的卡不能回手」类） | 无统一前置判 | 剩余 15 张里有 1 张（Milotic）卡在这里，做它等于做一族 |

## 六、做完的判据（每族通用）

1. 卡进池（`engine_ready=true`，池条目 +N），并**加到 `docs/RULE_CONFORMANCE.md`**
   对应行（矩阵覆盖卡数会跟着变，写错 CI 红）；
2. 每条卡面语句都有 tag → 对账类目 +1（若引入新 tag 族）；
3. 正反向用例各一条（「能做到」+「不该做到时不做到」）；
4. **注入式负向自测**：把实现改坏，指定的用例必须变红（不红=假守卫）；
5. 计数守卫与 CI 8/8 通过。
