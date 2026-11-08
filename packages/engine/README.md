# PokéLab Engine · 对战规则引擎与模拟器

PokéLab AI 的自研简化版宝可梦 TCG 对战规则引擎 + 模拟器（对应 PRD F1/F5/F6 与
《技术方案 v1.0》模块 1）。纯 Python 标准库实现，**确定性**（seed 驱动），无外部依赖。

```
<repo>/packages/engine/
├── engine/
│   ├── cards.py       卡牌模型（PokemonDef/AttackDef/EnergyDef + 场上实例 FieldPokemon）
│   ├── state.py       GameConfig（规则开关）/ PlayerState（牌库/手牌/场地/奖赏）
│   ├── rules.py       结算纯函数（弱点×2/抗性−30/状态检查）
│   ├── battle.py      对战裁判：回合状态机 + 动作合法性 + 结算闭环 + 事件日志
│   ├── agents.py      Agent 协议：RandomAgent / BaselineAI(规则基线) / HumanAgent
│   ├── decklib.py     示例卡组库（原创代名卡：Bolt/Thundrex ex/Flarex ex…，规避 IP）
│   └── simulator.py   run_match / run_many（批量统计，F6 本地内核）
├── tests/
│   ├── test_settlement.py   固定结算用例集（unit fixtures）
│   └── test_flow.py         对局流程用例（确定性/终局/批量/判平保护）
├── run_demo.py              单局复盘 / 批量统计 / JSONL 导出
└── README.md
```

## 运行

```bash
# 全量用例（1207 项，含 M1 真实卡池）
python -m unittest discover -s tests -v

# 单局复盘（中文事件流）
python run_demo.py single

# 批量统计 200 局（胜率/回合/先手/终局原因分布）
python run_demo.py batch 200

# 事件导出 JSONL（对齐 matches.events_log 形态）
python run_demo.py dump 20260908
```

## M1 · 真实卡池（v1.4 扩容）与预设对手池 A1–A4

真实卡名 + 60 卡真实卡表以 **数据文件**形式随引擎分发（数据与代码分离）。
卡池分两层——**人工池**（逐卡校对）与**扩展池**（解析链自动生成）：

| 文件 | 内容 |
|---|---|
| `engine/data/cards_real.json` | **人工池**：40 张（卡名/HP/属性/招式/特性 + 引擎映射 tag + 出处与 fidelity 标注，逐卡人工校对）|
| `engine/data/cards_pool_ext.json` | **扩展池**：1030 条目 / 899 唯一卡名，由 card-db 清洗数据集 + 解析链生成（`provenance=dataset-derived`，**非人工逐卡校对**）；条目带 `uid`（`<卡名>@<SET> <number>`）|
| `engine/data/decklists.json` | 预设对手池 A1–A4 的 **60 卡卡表**（PRD §10，逐卡来源见 meta；**逐卡锁定 uid** 以固定人工校对印刷）|
| `engine/reallib.py` | 加载器与组装：`catalog()` / `printings()` / `item_for()` / `build_deck(id)` / `coverage(id)` / `describe(id)` |

**规模与口径**：

| 项 | 数值 |
|---|---|
| `catalog()`（卡名 → **首选印刷**） | **923 张**（同名多印刷时取数据集最新印刷）|
| `printings()`（uid → 全部印刷） | **1070 条**（人工 40 + 扩展 1030）|
| 库内「引擎可对战」 | **1045 / 1267**（`mapped` 15 + `approximated` 1030，见 `GET /cards/stats`）|

> **印刷身份（2026-10-29 第二十一批）**：`uid = <卡名>@<SET> <number>`，卡名仍是
> `PokemonDef.name`（进化链与「同名」类规则不受影响）。卡组可写 uid 精确选印刷；
> 未指定时按卡名取首选印刷；同名 4 张上限按**卡名**合计（不同印刷不能绕过）。
> 预设卡表逐卡锁定人工校对印刷 → 同名新印刷不会改变预设行为。

> **近似 ≠ 等价**：扩展池条目标为 `approximated`（解析链推导、未逐卡校对），
> 与人工池的 `mapped` 在 C5 差异透明口径下**必须区分**，不可混为一谈。
> 生成器：`packages/card-db/scripts/gen_engine_pool.py`（严格准入——任一效果未建模
> 即整卡不收；排除 Mega ex；`desc_zh` 留空而非塞英文）。

```python
from engine import decklib, reallib

reallib.archetype_ids()            # ['A1','A2','A3','A4']
reallib.coverage('A1')             # {executable_rate: 0.9167, replaced: [...], battle_ready: True}
decklib.make_deck('A1')            # → 可直接进 Battle 的 60 张（真实卡名）

from engine.battle import Battle
b = Battle(decklib.make_deck('A1'), decklib.make_deck('A3'), seed=7)
```

**诚实标注（C5 差异透明）**：引擎尚未实现的卡（如 Rare Candy / Earthen Vessel /
Night Stretcher / Switch / Crispin）在 `build_deck` 时**以基础能量占位**，并在
`coverage()` 的 `replaced` 里逐卡给出原因；特性未落地但招式可用的卡（Munkidori /
Blaziken ex / Ogerpon ex / Clefairy ex / Fezandipiti ex）记入 `approximated`。
四套预设可执行率 **91.7%–96.7%**（验收口径 ≥90%）。

落库（可选，供 card-db 数据平台/报表使用）：

```bash
cd packages/card-db && python scripts/seed_archetypes.py --check   # 校验数据
cd packages/card-db && python scripts/seed_archetypes.py          # 写入 cards/decks/deck_cards
```

> 受限环境（既有文件写保护）会自动回退到用户目录并打印实际库路径。

## 规则引擎能力（v1.4 · E1–E5 + B4 机制）

| 机制 | 实现 |
|------|------|
| 回合状态机 | 开始（状态检查→抽卡）→ 主阶段 → 结束换边；攻击即结束回合（简化） |
| 动作 | 上基础/附能量(每回合1)/撤退/攻击/**进化(E2)**/**特性(E3)**/**训练家(E4)**/**目标选择(E5)**/结束；统一 `legal_actions` 合法性校验 |
| 伤害结算 | 基础伤害 → 弱点 ×2 → 抗性 −30（下限 0）→ **护盾减免**（见下） |
| **彩色能量(E1)** | 场上能量按 `{type:count}` 存储；招式 cost 支持 `[(fire,1),(psychic,1)]`；无色 cost 可被任意彩色能支付；攻击不弃能、撤退扣能无色优先 |
| **进化(E2)** | stage 0/1/2 + evolves_from 链；本回合刚放置/进化不可再进化；进化保留能量与伤害、清除特殊状态 |
| **特性(E3)** | `AbilityDef(location=field/hand)`：field 每回合一次（ability_used，回合重置）；hand 弃卡发动。首批 tag：`recon:n`(检视顶n取1放底) · `cursed:hp`(弃自身→指定对手宝可梦 hp 指示物伤) |
| **训练家(E4)** | `poffin`(检索2基础铺后备) · `ultra_ball`(弃2检索) · `hammer`(抛币弃对手能) · `boss`(拉后备，supporter)；**supporter 每回合一次，item 不限次** · **`coin_flip:heads:<a>\|tails:<b>`**（第四十四批：**同一枚币**的双分支只抛**一次**，正面/反面各派发一条子效果；拆成两条 `coin:` 会变成两次抛币 —— Team Rocket's Venture Bomb 正面指示物打对手任意 1 只、反面打己方战斗位） · **`shuffle_both_coin_draw:<mine_h>:<opp_h>:<mine_t>:<opp_t>`**（第四十五批：双方手牌洗回各自牌库 → 抛**一次**币 → 按结果给双方抽不同张数；Harlequin） · **`team_shield:reduce:<n>:type:<color>`**（第四十六批：**玩家级**全队限时减伤，`{color}` 宝可梦在对手下个回合少受 N 点；**新上场的宝可梦同样受益**，Iron Defender） · **`shield_choose:prevent_all[:from:<src>][:effects][:gate:<cond>]`**（第四十六批：选 1 只己方宝可梦上盾；`:gate:` 是「only if …」打出前置条件，如 `opp_prizes_le:2`，Acerola's Mischief） |
| **抛币(B4)** | `coin:heads:<tag>` / `coin:tails:<tag>` / `coin_each:N\|<counter>:<side>:<tag>` / `coin_all:N:<side>:<tag>` / **`coin_until_tails:<sub>`**（连续抛到反面，**正面数在结算时才知道**）：`dmg_plus:N`（在印刷伤害上 +N×正面数）· `dmg_set:N`（基础伤害**按正面数重算**，0 正面 = 0）· `mill:opp`（磨对手牌库 N 张）· `shuffle_opp_hand`（对手手牌随机 N 张洗回其牌库）· `search:any`（牌库顶 N 张入手，**含能量卡**）· `discard_energy:opp`（对手战斗位弃 N 个能量）；**一枚币只掷一次**并把结果复用于全部子效果（避免多次随机的语义偏差）。子效果可**复合**：`dmg_plus:30\|heal:30` 共用同一次抛币（Floragato）。**多币条件子效果**（第二十二批）：`coin_each:N:<side>:<sub>\|cond:<mode>[:<k>]:<sub>`，`mode` ∈ `all_tails` / `all_heads` / `at_least_heads:<k>` / `at_least_tails:<k>`，与伤害子效果**复用同一次抛币结果**（Slurpuff / Butterfree）；状态子效果只认规范名（`paralyze/burn/sleep/confuse/poison`），卡面英文词由引擎归一。**特性侧单分支**（第四十四批）：`coin:heads:<sub>` 由 `_do_ability` **抛一次**后派发（Dustox「Boisterous Wind」正面把对手战斗位的 1 个能量收回**其**手牌）——不是无条件执行。**逐目标各抛一枚**（第四十五批）：`coin_per_target:N:scope:opp` —— 每个对手宝可梦独立 50%，正面才打它（战斗位照常吃弱点/抗性、后备原始值；Mega Zygarde ex「Nullifying Zero」）；⚠ 落成 `snipe:opp:N:all` = 每只必中、落成 `coin:heads:snipe:` = 只抛一次，两者都是静默错算。**抛币三选一**：`status_choose:<s1>+<s2>+<s3>`（Cradily「Selective Slime」）确定性取卡面首个，可用 `action["status_choice"]` 覆盖 |
| **伤害修正(B4)** | `dmg_plus:N[:per:<counter>][:if:<cond>]` · **`dmg_plus:N:recoil:M`**（第四十七批：可选加伤与反伤**同一条** tag，保住「If you do」联动；Gurdurr / Dondozo ex） · `dmg_zero` · `ignore_weakness` · `ignore_resistance`；在**伤害结算之前**统一求值 |
| **跨回合伤害修正** | **`dmg_taken_plus_next:N`**（受击方在**我方下个回合**多受 N，弱抗**之后**）· **`atk_minus_next:N`**（受击方的招式在**它下个回合** −N，弱抗**之前**）· **`self_attack_plus_next:N:name:<招式>`**（自身**具名招式**在我方下个回合 +N，弱抗**之前**）。到期按**持有者** `turn_counts + 2` 判定（同 attack_lock 口径）；进化清除 |
| **伤害前阶段(第二十五批)** | **`pre:discard_tool:<n\|all>:opp_active`** · **`pre:discard_energy:<n\|all>:opp:special`** —— 「Before doing damage, …」在**伤害修正与伤害结算之前**执行（守方道具此时仍生效，放到伤害之后 = 本次仍吃道具效果）。配套条件后缀 **`<状态>:if_pre_tool`**（仅当伤害前阶段**真的弃到道具**才施加状态，N's Joltik；否则会变成无条件麻痹 = 静默加强） |
| **护盾(B4)** | `shield:reduce:N` · `shield:prevent_all[:from:<src>\|:if_damage:le:N][:effects]`（在弱抗**之后**结算；持续到该玩家下个回合开始自动失效）。**只写 prevent all damage 的护盾只挡伤害**，带 `:effects` 才连效果一起挡；**`:from:<src>` 限定同样作用于效果**（第四十六批，Acerola's Mischief 只挡对手 {ex}） |
| **被动静态(B4)** | `passive:bench_immune`（在后备时免疫招式伤害**与效果**）· `passive:bench_immune_dmg`（Tera 规则：只挡伤害）· `passive:dmg_immune:<self\|team_bench>[:from:<src>\|:min:<n>\|:no_rule_box][:effects]`（常驻免伤，来源限定只收 basic_ex / ex / tera / has_ability / special_energy；判不出来源时**不免疫**）· **`passive:coin_prevent:<color>`**（第四十五批：持有该色能量且被招式**伤害**时抛一枚，正面则本次伤害归零 —— 必须在伤害结算**内**执行，`on_damaged:` 是伤害已加上之后、无法撤销；Fezandipiti「Adrena-Pheromone」）· **`passive:checkup_plus:<status>:<n>[:side:opp][:active_only]`**（检查期追加伤害：Pecharunt / Magmortar）· **`passive:checkup_spread:<dmg>:<scope>[:except:<key>][:active_only]`**（检查期群体放置：Froslass 的 `ability_all` / Tyranitar 的 `opp_basic`） |
| **选定目标伤害(snipe)** | **`snipe:<opp\|opp_bench\|own_bench\|both_bench>:<per>:<count>[:per:<counter>][:target:damaged\|ex][:if:<cond>]`** —— `per` 每目标伤害、`count` = 1/N/all；`:per:` 按计数源缩放（Raging Bolt / Kyurem ex），`:if:` 条件不满足则**整段不结算**（Wo-Chien「deck ≤3」/ Ting-Lu「场上有场地」）。**`:target:damaged`**（第二十七批）：只挑**身上已有伤害指示物**的目标，且**先筛选再按 count 选**（Excadrill ex / Hippowdon，后者 `both_bench:all` 连己方合格目标一起打）。**`:per:target_counters`**：每目标按**它自己**的指示物数缩放（Team Rocket's Sneasel）——确定性默认挑伤得最重的；用全局计数源会多算别人的指示物。**`:target:ex`**（第四十一批）：只挑对手的 **Pokémon {ex}**（按 `pdef.is_ex`；Mega ex 也算），同样**先筛再选**（Vaporeon ex / Zeraora）；与 `{V}` 混写的形态仍如实未建模。⚠ **命中对手战斗位时吃弱点/抗性**，并接静态减伤 / 道具 / 场地 / 护盾（卡面括号只豁免**后备**）；后备按原始值 |
| **全队常驻减伤(第十九批)** | **`fx:team_reduce:<n>:basic:<color>[:other:<key>]` · `:energy:<color>` · `:faction:<key>[:source_bench]` `[:nonstack]`** —— 效果源在**守方阵营任意一只**的特性上（非受击者自身）→ 全队按类别 / 能量附着 / 阵营筛选，**弱抗之后**扣减；`other` = 需另一只同名牌在场，`source_bench` = 来源须在后备，`nonstack` 按 tag 去重（Curly Wall / Gear Coating / Stone Palace）；**能量限定可带 `:nonstack`**（Aurorus「Tundra Wall」，第四十六批） |
| **其他(B4)** | `draw_to:N`(抽到指定手牌数) · `discard:N\|all` · `move_damage:N\|all` · `attach:N[:type][:zone][:target:bench\|any\|bench_type:<c>][:counters:N]`（`target` 指定落点；`:counters` = 附能成功后的指示物）· `evolve_from_deck`(招式内检索进化) · **`evolve_each_bench`**（逐只后备各从牌库找下一代进化卡直接进化，Reuniclus）· **`evolve_each_pick:<max>:<scope>`**（**选**至多 N 只、各进化一次：目标是选出集合，可含战斗位；用 `evolve_each_bench` 顶替会把「至多 N 只」变成「全部后备」，Team Rocket's Nidorina）· `:anyenergy`（卡面「an Energy card」可附特殊能量） · **`search_energy:all[:distinct]`**（「any number of Basic Energy cards **of different types**」：上限 = 牌库出现的基本能量属性种类数，确定性取满；**只取基本能量**，特殊能量不算 Basic，Energy Search Pro） · **`coin:heads:shuffle_opp_pokemon:<opp\|opp_bench>`**（第四十二批：把对手 1 只宝可梦**连同附着卡**洗回其牌库 —— 不是击倒、**不取奖赏**；战斗位被洗走后必须从后备补位、无后备则判负；`opp` 含战斗位（Shiftry）、`opp_bench` 只后备（Sylveon）） |
| **回复/击倒/牌库顶(B4)** | `heal:N[:<scope>][:type:<c>][:discard]` · **`heal:all[:<scope>][:type:<c>][:energy]`（回满）** · `<scope>` 含 `mega`（只在己方 Mega 里选 1 只，Wally's Compassion）；尾缀 `:energy` = 回复后把该目标**全部能量收回手牌**，`:discard` = 回复后弃该目标 **1 个能量**（均隐含「仅当真的回复过才执行」）· **`self:N[:per:<counter>]`**（反伤；Palafin 按自身伤害指示物缩放）· **`knockout:<opp_active\|opp_bench\|opp_any\|opp_counters:N\|both_active>[:basic]`**（直接击倒，受效果免疫拦截）· **`shuffle_top:N[:opp]`**（只重排顶部 N 张）· **`counters:N[:per:<counter>][:scope:opp[:<count>\|all]\|:scope:self][:selfko]`**（指示物**直放**，**不吃弱点/抗性**，可击杀取奖赏：Haunter / Alakazam 打战斗位；`:scope:opp` = 对手任意 1 只（Bramblin / Spectrier / Dusclops / Golbat），`:all` = 全体（Uxie）；**`:scope:self`**（第四十四批）= **己方战斗位**（Venture Bomb 反面「put 2 damage counters on your Active Pokémon」——是指示物、不是 `self:N` 受伤）；**`:scope:ability:all`** = 双方全场**印有特性**的逐个（Cofagrigus，第二十八批；己方那侧被击倒也由对手取奖赏）；`:selfko` = 特性持有者随后自毁（Dusclops / Dusknoir「诅咒爆裂」））· **`energy_to_deck:<color|any>:<n\|all>`**（把弃牌堆的**基本能量**洗回牌库：Kyogre「Riptide」/ Sinistcha ex「Re-Brew」——只建模计数不洗回会让下次攻击重复计数）· **`energy_self_to_deck:all`**（第四十一批：把**自身附着**的全部能量还原成卡并洗回牌库 = 强制代价；基本能量按点数还原 1 张、特殊能量整张原样放回，Mega Skarmory ex「Sonic Ripper」——只清零计数等于「洗回」变「消失」）· **`bounce:energy:<self\|opp_active>:<n\|all>[:type:<c>]`**（附着能量收回**手牌**：无色优先、指定属性只取该色；基本能量不足才按整张卡取特殊能量；Chien-Pao / Dipplin / Landorus / Volcanion）· **`checkup_set:<status>:<n>`**（检查期伤害取代默认值，Nidoking ex） |
| **触发式特性(B4)** | **`evolve_act:<tag1>\|<tag2>`**（进化那一刻按序执行一次；不再每回合可发动）· **`on_ko:replay_evolve_act`**（第二触发时机：战斗位被击倒时重放进化特性）· **`ko_prize_plus:N[:coin]`**（对手战斗位被击倒多取奖赏，不叠加）· **`on_ko:counter:N`**（自身被击倒反制攻击方）· **`on_ko:energy_to_hand:<type>`**（被击倒者身上的基础能量收回手牌）· **`on_damaged:<status\|counter\|discard_energy>:<arg>`**（受击反应）· **`on_damaged:counter_per:<n>:<color>`**（每持有 1 个该色能量 → 攻击者额外吃 n×10，Orthworm ex）· `:if_prev` 后缀（「in this way」：仅当前序动作真的发生时才执行） · `attach:…:target:bench`（附到后备，供「附能后回复同一只」链） |
| **下回合反制(第二十九批)** | **`retaliate_next:<n\|equal>`** —— 招式挂在**出招者自己**身上，只在**对手的下一个回合**内生效：该 Pokémon 被攻击伤害时，攻击者吃 N 点（`equal` = 与所受伤害相同）；每次被攻击都可触发，进化清除（Bouffalant / Zamazenta） |
| **场地静态规则(S3)** | `stadium:hp_plus` / `dmg_reduce[:type·:faction]` · **`attack_plus:N:faction:<k>`** · **`no_retreat:faction:<k>`** · **`no_tool_effect`**（道具失效）· **`status_immune:require:energy`** · **`evolve_same_turn:type:<c>`** · **`dmg_immune:box:none:from:rulebox`** · **`bench_limit:8:require:tera[:trim:N]`**（Area Zero；失去 Tera 后裁回且不取奖赏）· **`no_bench_counters`**（Battle Cage：后备指示物拦截）· `stadium:use:*`（每回合一次；`evolve_from_deck[:chain:1]` = Grand Tree 链式进化） |
| **计数源/条件(B4)** | 计数源 22 个 + 条件 6 个（**只取公开信息**）；未知计数源**抛错**而非静默按 0。**`own_active_extra_energy_at_least:<n>`**（第十九批：战斗位总能量 − **本次招式费用** ≥ n）。第二十六批新增**限定后**的指示物计数源：**`own_bench_counters_type:<color>`**（后备中该属性宝可梦的指示物总数，Gigalith）· **`own_bench_counters_named:<key>`**（具名阵营后备，Cynthia's Spiritomb）· **`own_field_named_with_damage:<key>`**（具名且带指示物的**只数**，Paldean Tauros）——属性拼错直接抛错，绝不静默 0。第四十三批新增**`own_discard_category:<类别>`**（弃牌堆中**卡牌子类**为该类的卡数：Ancient 是 `category`，按卡名找恒 0、按总数又多算，Roaring Moon）· **`own_field_named_energy:<key>:<color>`**（具名阵营全队的该色能量数，三者缺一会多算，Iono's Voltorb）|
| 特殊状态 | 中毒(−10/回合) · 烧伤(−20/回合) · 睡眠(抛币醒) · 麻痹(检查阶段恢复) · 混乱(攻击抛币，反面自伤 30 攻击失败；正面攻击后解除)；睡/麻禁攻禁撤 |
| 击倒→奖赏 | 普 1 张 / ex 2 张；active 被击倒强制替补（无后备 → 判负）；**被招式击倒**的标记按方计数，供下回合条件加伤使用。**S3（2026-11-08）：奖赏卡已对象化** —— 开局从牌库顶真取 6 张（`PlayerState.prizes` = `list[PrizeSlot]`），取奖赏时**真卡进手牌**；`zones.py` 提供 `deck_bottom_take/put`、`put_on_top`、`peek_top/bottom/prizes`。⚠ 取哪一张：面朝下时对牌手不可区分（不构成决策）→ 引擎按「先面朝下、后面朝上」固定顺序并写日志；存在面朝上奖赏卡时的**选择**留 S6 |
| **事件/延迟(S2a)** | **`on_event:<kind>:<body>`**（订阅：特性 / 道具；每卡每事件每回合 1 次，抑制写日志）· **`queue:<when>:<tag>`**（延迟：`turn_end` / `next_turn_end` / `opp_next_turn_end`，`events.resolve_turn_end` 在 `_end_turn` 唯一结算点执行，**过期未结算抛错**）· 换位事件用「step 前快照 / step 后比对」检测，撤退·Switch·拉人·换位特性·回手全部自动覆盖 |
| 胜负三路径 | 奖赏拿完 · 对方无宝可梦 · 对方抽卡空牌库（官方：该抽而抽不到即判负）；超 `max_turns` 判平 |
| 招式效果 tag | 状态 5 种 · `spread_free:N`(任意分配) · `spread:N:per`(随机散布) · `heal:N` · `self:N`(反伤) · `draw:N` · `discard/…` · `switch_self` · `gust` · `attach:N[:type]` · `move_damage:N` · 以及上列 B4 各家族 |
| 确定性 | `random.Random(seed)` 全驱动：同 seed → 同事件序列（有单测） |
| 事件日志 | 结构化 dict 序列 → `matches.events_log` JSONL 可直接落库 |

> tag 全集以 `engine/data/cards_real.json` 的 `meta.engine_tags` 为准（该文件随代码
> 更新，是 tag 的**权威清单**）。覆盖面：未建模条目 1144 → 695（−39.2%），
> B4 批次 212 → 54；余量（特性封锁/场地/依赖引擎外信息的缩放）语义逐卡不同，
> 逐条登记在 `packages/card-db/data/cleaned/unmodeled_registry.md`。

## 招式/特性效果文本（中文，desc_zh）

`cards_real.json` v1.2 起为**全部 26 个招式**与 **8 个特性**补 `desc_zh`（中文自述转写，
严禁逐字复制官方文案），基本能量亦补「提供 N 点X能量」。

> **中文覆盖的诚实口径（重要）**：中文名/中文效果**只有人工池有**。
> 扩展池 397 条目的 `name_zh` / `desc_zh` 为**空**——源数据集
> （`pokemon-tcg-ai-battle-challenge-strategy`）本身是英文，我们没有第二份中文语料，
> 因此**不把英文塞进中文字段**冒充翻译。展示层按「中文优先、回退英文原文」处理
> （`display` / `display_desc`），卡库页对扩展池卡会显示英文原名与英文效果。
> 全库中文名覆盖：**36 / 1267**（即人工池命中的部分）。

| 层 | 字段 |
|---|---|
| 数据 | `attacks[].desc_zh` · `abilities[].desc_zh`（`desc` 为英文/旧字段兼容保留） |
| 卡定义 | `AttackDef.desc_zh` / `.display_desc`（中文优先回退） · `AbilityDef` 同 |
| 视图 | `view()` 的招式/特性条目带 `desc_zh` + `display_desc`；合法攻击动作亦带（供按钮提示） |

前端把 `display_desc` 接入招式按钮与指令候选的 `title`，玩家悬停即见中文效果；
同一份文本也是 **K2 解析语料**（见 `packages/agent` 的 `effects.py`）。

## 自由文本指令（中文招式名 / 卡名 → 动作）

`engine.commands` 把「人话」解析为 `legal_actions` 成员，让招式名/卡名本身可执行
（数据键仍是英文；中文既是展示名也是输入别名）。

| API | 作用 |
|---|---|
| `Battle.resolve_command(text, verb=None)` | 文本 → 合法动作（返回 `{ok, action, matched, candidates, ambiguous}`） |
| `Battle.step_command(text, verb=None)` | 直接按名称执行（解析失败返回 `ok=False`，不抛异常） |
| `Battle.command_catalog()` | 当前可用指令清单（中文 label + 对应动作），供 UI 提示/自动补全 |
| `commands.find_action(legal, text)` / `resolve_action(legal, action)` | 纯函数版本；后者用于把「带名称缺索引」的动作**补全**为合法动作 |

支持的写法（忽略大小写/空格/标点）：

| 输入 | 解析结果 |
|---|---|
| `幻影俯冲` · `Phantom Dive` | `{"verb":"attack","attack":1}` |
| `攻击 幻影俯冲` · `高级球` | 招式 / 训练家卡（中文或英文名） |
| `附能量到后备1` · `附能量 → 战斗位` | `{"verb":"attach","target":n}` |
| `撤退到后备1` · `结束回合` · `过` | `{"verb":"retreat"/"pass", ...}` |
| `{"verb":"attack","attack_name":"幻影俯冲"}` | 直接 `step()` 也可（无需索引） |

**不变量**：`find_action(legal, command_label(a))` 必须能还原 `a`（有单测守卫）；
同名卡的多张副本可互换（不报歧义），但**同名不同目标**必须报错要求指明，绝不静默改目标。

## i18n · 中文展示层（英文为数据键）

| 层 | 字段 | 说明 |
|---|---|---|
| 卡池数据 | `name`（英文） + `name_zh`（中文） | 英文是**数据键**（Limitless 文本匹配/文件字段/URL 参数）；中文用于展示 |
| 卡定义 | `PokemonDef/TrainerDef/EnergyDef.name_zh`、`.display`（中文优先，缺省回退英文） | `AttackDef.name_zh` / `AbilityDef.name_zh` 覆盖招式与特性 |
| 卡表 | `decklists.json` 的 `label`（英文原型） + `label_zh`（中文卡组名） | `reallib.archetypes()` 返回 `label`(中文) + `label_en` |
| 视图 | `view()` 的每条手牌/场上宝可梦/招式/特性都带 `name_zh` 与 `display` | 前端直接渲染 `display` 即全中文 |
| 卡组名 | `decklib.deck_label_zh(id)`（`A1` → 「A1 · 多龙巴鲁托ex / 黑夜魔灵」） | 演示卡组亦有中文名 |
| 注册表 | `cards.pokemon_def(name)` | 按英文键取定义（用于进化链中文名等） |

```python
from engine import reallib, decklib
reallib.display_name("Dragapult ex")      # '多龙巴鲁托ex'
reallib.archetypes()[0]["label"]          # '多龙巴鲁托ex / 黑夜魔灵'
decklib.deck_label_zh("draco")            # '龙系进化演示卡组'
```

### 中文名作为**输入**（不只展示）

| API | 作用 |
|---|---|
| `reallib.resolve_name(zh_or_en)` | 中文展示名 → 英文数据键（`'高级球'` → `'Ultra Ball'`；已是英文键则规范回填；未收录原样返回） |
| `reallib.zh_index()` | 中文展示名 → 英文键索引（`zh → en` 全量映射，含招式/特性） |
| `reallib.is_zh_name(s)` | 判断是否含中文（用于提示/归一化） |
| `decklib.resolve_deck_id(name)` | 卡组名 → 数据键，支持中文（`'A1 · 多龙巴鲁托ex / 黑夜魔灵'` / `'龙系进化演示卡组'` → `A1` / `draco`），大小写回正 |
| `decklib.make_deck(name)` | 直接接受中文卡组名 |

> 数据键**始终**是英文（Limitless 文本匹配 / 文件与库字段 / URL 参数不变）；
> 中文只是「输入别名 + 展示名」。

## ⚠️ 规则边界（与官方规则的已知差异，后续经 ptcg-engine oracle 校准）

1. ~~能量不分颜色~~ → **v0.2 已支持彩色**；特殊能量（宝可梦能量/双重能量）仍为灰名单；
2. **先手第一回合不禁攻**（`first_turn_no_attack` 默认 False；开关已留，单测覆盖 True 分支）；
3. **每回合都抽 1**（含先手首回合；官方差异待校准）；
4. **起手自动铺后备**（官方为玩家选择）；
5. **攻击后立即结束回合**（不做攻击后继续行动）；
6. 训练家**最小子集已实现（4 张）**；其余（物品池/场地/宝可梦道具）为灰名单；
7. ~~特性=灰名单~~ → **v0.3 已支持最小集**（`recon` / `cursed`，field/hand 两位置）；
   **多目标/移伤类特性（如 Adrena-Brain 需"我方→对手"双目标选择）仍灰名单**，依赖 E5 目标选择接口；
8. **伤害指示物任意分配（E5）待实现**（Phantom Dive 的 6 指示物当前按随机 spread tag 近似，
   任意分配需 Agent/UI 目标选择接口，P2 与决策流水线同期）；
9. 引擎卡用程序化 `PokemonDef` 定义，F8 effects_json 仅作溯源旁证，不直接进结算路径
   （规避 LLM 解析缺陷，符合 TS v1.0 D1 自研路线）。

## 验收证据（2026-09-08 本机执行）

```
python -m unittest discover -s tests        → Ran 58 tests OK（34 回归 + 16 v0.2 + 8 v0.3）
  覆盖：彩色能量支付/撤退扣能 · 进化链/刚放置禁进化/保留能量伤害 · poffin/ultra_ball/hammer/boss
        supporter 每回合一次 / item 不限次 · Draco Y 版卡组端到端 8 seeds 全终局
        E3：recon 每回合一次/重置、hand cursed 弃自身+指定目标伤害+后备KO奖赏、ability 卡组 smoke
run_demo.py single  (seed=20260908)         → baseline 对局 10 回合终局（含 ex 双奖赏/反伤/替补）
run_demo.py batch 100  lightning(baseline) vs psychic(random)
    win_rate 0.72 / avg_turns 25.6 / p95 45 / reason{no_pokemon:49, prizes:32, deckout:19}
```

## 后续接入

- **Agent**：RL/MCTS Agent 通过 `view()` 快照 + `legal_actions()` 接入（决策流水线见 TS v1.0 模块 2）；
- **批量服务**：`run_many` 已产出胜率矩阵输入；batch_tasks 调度与 DB 写入在 P1 上层服务实现；
- **卡池扩展**：新增实卡 = 在 engine 定义 `PokemonDef`（读 DB cards 基本属性，效果由人工确认的程序化
  定义落地，K2 解析结果经校对后转写）。
