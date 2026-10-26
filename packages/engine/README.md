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
# 全量用例（77 项，含 M1 真实卡池）
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
| `engine/data/cards_pool_ext.json` | **扩展池**：397 条目 / 371 唯一名，由 card-db 清洗数据集 + 解析链生成（`provenance=dataset-derived`，**非人工逐卡校对**）|
| `engine/data/decklists.json` | 预设对手池 A1–A4 的 **60 卡卡表**（PRD §10，逐卡来源见 meta）|
| `engine/reallib.py` | 加载器与组装：`catalog()` / `build_deck(id)` / `coverage(id)` / `describe(id)` |

**规模与口径**（`reallib.catalog()`）：

| 项 | 数值 |
|---|---|
| `catalog()` 可加载卡 | **431 张**（人工 40 + 扩展 420 条目；扩展池内重名条目＝同卡不同印刷批次，按名去重）|
| 数据文件条目合计 | 460（40 + 420）|
| 库内「引擎可对战」 | **531 / 1267**（`mapped` 36 + `approximated` 495，见 `GET /cards/stats`）|

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
| **训练家(E4)** | `poffin`(检索2基础铺后备) · `ultra_ball`(弃2检索) · `hammer`(抛币弃对手能) · `boss`(拉后备，supporter)；**supporter 每回合一次，item 不限次** |
| **抛币(B4)** | `coin:heads:<tag>` / `coin:tails:<tag>` / `coin_each:N\|<counter>:<side>:<tag>`；**一枚币只掷一次**并把结果复用于全部子效果（避免多次随机的语义偏差） |
| **伤害修正(B4)** | `dmg_plus:N[:per:<counter>][:if:<cond>]` · `dmg_zero` · `ignore_weakness` · `ignore_resistance`；在**伤害结算之前**统一求值 |
| **护盾(B4)** | `shield:reduce:N` · `shield:prevent_all`（在弱抗**之后**结算；持续到该玩家下个回合开始自动失效） |
| **被动静态(B4)** | `passive:bench_immune`（仅在后备时免疫招式伤害与效果） |
| **其他(B4)** | `draw_to:N`(抽到指定手牌数) · `discard:N\|all` · `move_damage:N\|all` · `attach:N[:type][:zone]` · `evolve_from_deck`(招式内检索进化) |
| **计数源/条件(B4)** | 计数源 22 个 + 条件 6 个（**只取公开信息**）；未知计数源**抛错**而非静默按 0 |
| 特殊状态 | 中毒(−10/回合) · 烧伤(−20/回合) · 睡眠(抛币醒) · 麻痹(检查阶段恢复) · 混乱(攻击抛币，反面自伤 30 攻击失败；正面攻击后解除)；睡/麻禁攻禁撤 |
| 击倒→奖赏 | 普 1 张 / ex 2 张；active 被击倒强制替补（无后备 → 判负）；**被招式击倒**的标记按方计数，供下回合条件加伤使用 |
| 胜负三路径 | 奖赏拿完 · 对方无宝可梦 · 对方抽卡空牌库；超 `max_turns` 判平 |
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
