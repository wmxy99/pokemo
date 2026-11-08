# 引擎 / 卡库补漏报告（2026-11-08 实测）

> 口径：一切数字都从**代码与数据现算**得出，不从文档抄。复现命令写在每节末尾。
> 用途有两个：① 对外披露「哪些卡还不能打、为什么」；② 作为实现队列 —— 每一项都
> 写清「缺什么能力、影响多少张、怎么算做完」。

## 一、总览

| 项 | 实测 | 说明 |
|---|---|---|
| 卡库总量 | **1267 张** | `cards_challenge.json`（清洗后全量） |
| 引擎可对战（严格口径） | **1265 张 · 99.8%** | `engine_ready=true`，与 `GET /cards/stats` 同源 |
| 引擎卡池条目 | **1250 条** | `packages/engine/engine/data/cards_pool_ext.json` |
| 未进池 | **2 张** | 全部为「如实拒收」（D2/D3/D4/B4/F5/F7/F8 累计让 43 张进池） |
| 解析对账 | **56 类全绿** | `scripts/audit_pool_alignment.py` |
| 规则一致性矩阵 | **39 行（38 ✅ / 1 ⚠️）** | `docs/RULE_CONFORMANCE.md`（CI 机械核对） |
| 测试 | **2887**（engine 1618 / agent 1115 / api 142 / gym 12） | |

复现：

```bash
python packages/card-db/scripts/audit_pool_alignment.py          # 56 类
python scripts/check_conformance.py                              # 矩阵
python -c "import json;d=json.load(open('packages/card-db/data/cleaned/cards_challenge.json',encoding='utf-8'));cs=d['cards'];print(sum(1 for c in cs if c.get('engine_ready')),'/',len(cs))"
```

## 二、口径澄清（三个数字为什么不一样）

| 数字 | 含义 | 差异来源 |
|---|---|---|
| **1265** `engine_ready` | **严格口径**：卡真的能被 AI 选中并正确结算 | 对外只用这个 |
| **1250** 池条目 | 引擎侧的卡定义**条数**（同一张卡的多印刷各算一条） | 条目数 < 卡数的部分，是人工卡池已收录同一印刷时被去重 |
| **2** 未进池 | 解析链产生未建模动作 → 建池时**整卡不收** | 逐卡原因见附录（每张只有一条） |

**为什么必须三个都披露**：只报 1250 会让人以为「池里有的都能打」（池条目与卡库
不是一一对应）；只报 1265 而不说 2，读者无从判断「剩下的是做不了还是没做」。
三个数一起给，差异才是可解释的。

复现：

```bash
python -c "import json;d=json.load(open('packages/card-db/data/cleaned/cards_challenge.json',encoding='utf-8'));cs=d['cards'];print(sum(1 for c in cs if c.get('engine_ready')),'/',len(cs))"
python -c "import json;print(len(json.load(open('packages/engine/engine/data/cards_pool_ext.json',encoding='utf-8'))['cards']))"
```

## 三、2 张未进池卡

> **F1–F7 已整族清零**；F8 已处理 11 张，剩 2 张（其中 1 张是结构性）。

| 族 | 缺什么能力 | 卡数 | 工作量 | 状态 |
|---|---|---:|---|---|
| **F1 · Tera 备战位免伤** | `passive:bench_immune_dmg` | ~~6~~ **0** | S | **✅ 已落地（建池转写）** |
| **F2 · 退化 devolve** | 退回到手牌/牌库、本体降级、当回合不能再进化 | ~~2~~ **0** | M | **✅ 已落地（D3/F8）** |
| **F3 · 任意数量 / 自由分配** | 交互层：`任意张 + 任意分配` | ~~12~~ **0** | L | **✅ 已落地（B4）** |
| **F4 · 多条件组合门槛** | `play_gate` 支持**合取** | ~~2~~ **0** | M | **✅ 已落地（D2）** |
| **F5 · 静态属性改写** | 改弱点（带属性过滤）、能量单点增值 | ~~2~~ **0** | M | **✅ 已落地（F5）** |
| **F6 · 能力抑制 / 规则框** | 抑制「需要自身击倒」的特性、抑制 Rule Box 宝可梦的特性 | ~~3~~ **0** | M | **✅ 已落地（D4）** |
| **F7 · 道具 / 特殊能量形态** | 工具改费用+加伤、工具提供招式、全场弃道具+特殊能量 | ~~3~~ **0** | M | **✅ 已落地（F7）** |
| **F8 · 规则改写 / 逐卡机制** | 逐卡见附录 | 2 | M–L | 已进 11 张 |

### F8 · 已清零的十三张

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
| Kieran | `mode_pick:switch_self|team_boost:30:cat:ex+v` | 两个模式**互斥**：legals 各列一条带 `mode` |
| Team Rocket's Moltres ex | `discard_named_energy:teamrockets:then:discard_defender` | 「If you do」是**条件后续**；弃置不是击倒 |
| Eevee ex | `passive:evolve_as:eevee:ex` | 绕过进化链，但只放行 `{ex}` 目标；**只走这条显式被动** |

## 四、2 张逐卡附录（缺口唯一一条，逐条可复核）

| 卡 | 卡面关键句（未建模的那一句） | 缺什么 | 族 |
|---|---|---|---|
| Wellspring Mask Ogerpon ex | `You may shuffle 3 Energy attached to this Pokémon into your deck. If you do, this attack also does 120 damage to 1 of your opponent's Benched Pokémon` | **代价门控的附加伤害**：可选代价（洗回 3 个能量）**且**「已支付才追加一段狙击」。B4 的 `pre:b4:` 已支持「伤害前决策」，但还没有「代价付了才追加一段狙击」这一组合形态（需要把 `pre:` 的续跑接到狙击路径上） | F8 |
| Cinderace | `If this Pokémon is in your hand when you are setting up to play, you may put it face down in the Active Spot` | **开局上场规则**（结构性）：`_setup_openings()` 目前**全自动**铺场，没有「从手牌选谁上场」的决策层；要做得先给 setup 加选择动作，会牵动 agent 的动作空间（`action_dim`） | F8 |

## 五、非卡面缺口（规则层）

| 编号 | 缺口 | 现状 | 处置 |
|---|---|---|---|
| **D1** | mulligan 补偿是**自动抽**，官方为「可以抽」 | 矩阵 ⚠️ 行 | 需要把 `_begin_turn()` 推迟到 setup 选择闭合之后；风险 > 收益，暂缓（做法已写在矩阵 D1） |
| **D2** | 多条件组合门槛 | ✅ `play_gate` 支持合取 + `all_in_play:` | 已完成 |
| **D3** | 退化 devolve | ✅ `FieldPokemon.under` + `devolve:<scope>:<n\|any>` | 已完成 |
| — | 任意数量交互层（F3） | ✅ `phase="choice"` + `pre:b4:` / `choose:alloc:` | 已完成（B4） |
| **F7** | 道具形态（复合静态 / 提供招式 / 全场弃牌） | ✅ 复合 `a\|b` + `TrainerDef.tool_attack` | 已完成 |
| **F5** | 静态属性改写 | ✅ `weak_change:<new>:<filter>` + `passive:energy_double:<type>` | 已完成 |
| **F8** | 规则/去向改写 + 逐卡机制 | ✅ `discard_self:` / `:each:to:deck` / `passive:no_hand_return` / `passive:ko_to_hand` / `hand_bench:` / `opp_hand:bench` / `passive:energy_static` / `mode_pick:` / `discard_named_energy:` / `passive:evolve_as:` | 已完成 11 张，剩 2 张（见附录） |
| — | 效果施加路径唯一性（`fp.damage` 直写扫描） | 未做 AST 守卫 | 建议下一步 |
| — | 静态规则**禁令**族（「对手的卡不能回手」类） | 无统一前置判 | 剩余 15 张里有 1 张（Milotic）卡在这里，做它等于做一族 |

## 六、做完的判据（每族通用）

1. 卡进池（`engine_ready=true`，池条目 +N），并**加到 `docs/RULE_CONFORMANCE.md`**
   对应行（矩阵覆盖卡数会跟着变，写错 CI 红）；
2. 每条卡面语句都有 tag → 对账类目 +1（若引入新 tag 族）；
3. 正反向用例各一条（「能做到」+「不该做到时不做到」）；
4. **注入式负向自测**：把实现改坏，指定的用例必须变红（不红=假守卫）；
5. 计数守卫与 CI 8/8 通过。
