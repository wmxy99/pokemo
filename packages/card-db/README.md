# PokéLab Data · 卡牌数据集与规则知识库构建

阶段一（P1a–P1c）数据工程包。运行环境：**Python 3.12**（依赖见 `requirements.txt`；
本地开发常用 conda 环境，路径因人而异，**不写死**）。

## 目录结构

```
packages/card-db/
├─ pokelab/
│  ├─ schema.py     # pydantic：CardEffect/CardRecord/RuleChunk（对齐 DB v1.1）
│  ├─ db.py         # SQLite：执行 qrwdan/schema.sql 建 10 表 + 增删（cards/rule_chunks）
│  ├─ loader.py     # pokemontcg.io JSON / Kaggle CSV → CardRecord（清洗映射）
│  ├─ effects.py    # ⚠ LLM 路径已废弃（见下方「废弃登记」）；纯函数 extract_json/validate 仍可复用
│  └─ kb.py         # 规则条款化分块 + rule_chunks 入库 + 检索（向量化预留）
├─ scripts/
│  ├─ fetch_pokemontcg.py  # 【本机联网执行】拉 pokemontcg.io → JSONL
│  ├─ import_cards.py      # JSONL/CSV → cards 表（status=pending）
│  ├─ parse_effects.py     # ⚠ 已废弃（默认拒绝执行）；`--check` 可看空转证据
│  └─ seed_demo.py         # 离线 demo：建库+样例卡+规则条款（验证闭环）
└─ data/            # raw/（外部数据，不提交仓库）· db/pokelab.db
```

## 废弃登记（2026-09-15）

| 组件 | 状态 | 原因与替代 |
|---|---|---|
| `scripts/parse_effects.py` | **已废弃**（默认拒绝执行，需 `--force-deprecated`） | ① 筛 `status='pending'` 而现库 **1267 张全 active** → 恒空转；② 写入格式 `[{CardEffect}]` 与现库 `effects_json`（`{library,parse}`，下游 `coverage_backlog.py` 读 `.parse`）**不兼容，误跑会让 coverage 统计静默归零**；③ 已被 `agent.effects.parse_effects` 取代 |
| `pokelab/effects.py::llm_parse_text` | **已废弃** | 注入式客户端 + 内嵌 prompt 与产品提示词**不同步**（把枚举写成引号内 `a\|b\|c`，实测导致模型照抄字面量）。改用 `packages/agent/prompts/parse_effects_*.md` 单一提示词源 |

替代路径（现行）：

```bash
python apps/api/scripts/local_qwen.py serve                      # 起本地 OpenAI 兼容服务
POKELAB_LLM_BASE=http://127.0.0.1:9997/v1 \
    python packages/agent/examples/run_k2.py --backend llm        # 解析 + 评测（不落库）
```

零副作用自检：`python scripts/parse_effects.py --check`
（实测输出：status 全为 active、pending 命中 0、`effects_json` 结构分布）

## 使用（三步走）

```bash
# 0) 离线验证管道闭环（无需网络/真实数据）
python scripts/seed_demo.py
# 1) 真实数据（需网络；Kaggle 数据需账号下载）
python scripts/fetch_pokemontcg.py --set sv3 --limit 300 --out data/raw/pokemontcg.jsonl
python scripts/import_cards.py --jsonl data/raw/pokemontcg.jsonl
#    —— 或 Kaggle：python scripts/import_cards.py --kaggle-csv <EN_Card_Data.csv>
# 2) 效果解析（本地 Qwen3-4B 8bit；权重不入库，需先设 POKELAB_QWEN_PATH）
python scripts/parse_effects.py --card-id 1
```

## 卡牌数据集清洗与入库（M1 卡池补齐）

数据源：`pokemon-tcg-ai-battle-challenge-strategy.zip`（EN + JP Card Data.csv，
**一行一招式/特性**；zip 内 4 份卡 ID PDF 体积大，与本流程无关）。

```bash
cd packages/card-db
python scripts/import_challenge_dataset.py --zip <path.zip>      # 清洗 + 入库
python scripts/import_challenge_dataset.py --zip <path.zip> --dry-run   # 只看清洗结果
python scripts/import_challenge_dataset.py --csv-dir <解压目录>   # 用已解压 CSV
# 附 K2 效果解析（机器生成 → 带 AI 标识；合规 6.1）
python scripts/import_challenge_dataset.py --zip <path.zip> --parse-effects
```

**实测解析覆盖**（rule 后端）：解析卡 1119 · 条目 1481 · 可映射引擎 tag 241 ·
**未建模动作 1144**（= 引擎待建模清单，见 `clean_report.md` §4）。

**卡图（可选）**：在 `data/card_images.json` 提供 `{"TWM-130": "<url>"}` 或按卡名映射，
入库写入 `cards.image_url`；脚本**不抓取图像**，URL 由使用者提供并自负合规责任
（合规 6.1：可用但须标注权利方）。

产物：

| 产物 | 说明 |
|---|---|
| `data/cleaned/cards_challenge.json` | **规范化卡库**（入库源，可版本管理） |
| `data/cleaned/clean_report.md` | **清洗质量报告**（汇总 / 归一化项 / 问题清单 / 诚实标注） |
| `data/db/pokelab.db` · `cards` 表 | upsert（幂等，按 name+series+number）；同时清理骨架期占位行 |
| `data/cleaned/unmodeled_backlog.json` | **未建模动作清单**（机读快照，`coverage_backlog.py` 生成） |
| `data/cleaned/unmodeled_registry.md` | **C5 永久差异登记**（B4/B5 逐条：子类/条目数/影响卡数/原因） |
| `data/cleaned/coverage_report_*.md` | 覆盖率对比报告（`--compare before after` 产出增量） |
| `engine/data/cards_pool_ext.json` | 扩展卡池（`gen_engine_pool.py` 生成，见上节） |

**实测规模**：2022 行 → **1267 张卡**（宝可梦 1056 · 训练家 191 · 能量 20）·
招式 1559 · 特性 223。

### ⚠ 覆盖率有**两个口径**，对外引用请用严格口径

| 口径 | 数值（2026-09-22） | 定义 | 用在哪 |
|---|---:|---|---|
| **严格：`engine_ready`** | **541 / 1267** | 库内标志位（含近似/fidelity 条件） | `/cards/stats`、卡组校验、对局可用池 |
| 解析层面「无未建模动作」 | 611 / 1267 | 只表示解析链未产出未建模动作 | `unmodeled_registry.md` 的进展对比 |

两者**不是同一件事**，差值部分 = 「解析通过但标志仍不可对战」的卡。
对外（README / 发布说明 / KPI）**统一用 541**；`unmodeled_registry.md` 已同步标注两种口径。

### ⚠ `pokelab.db` 里的设计期表已被取代（勿误用）

`batch_tasks` · `decks` · `deck_cards` · `matches` · `model_registry` ·
`report_exports` · `training_tasks` · `users` —— 这 8 张表来自**早期 schema 设计**，
**业务已迁至 API 运行库**（`~/.pokelab/api.db`，见 `docs/architecture.md`），现均为空表。
本库现役只有两张表：`cards`（卡池）与 `rule_chunks`（规则知识库）。
新增功能请走 API 侧 `store.py`，**不要**往这些空表里写。

清洗规则要点（详见 `clean_report.md`）：

- `n/a` / 空串 / `-` → `null`（不再伪装成字符串）；
- 能量符号归一：`●`→无色、`{G}…{C}`→属性代码、`{A}`→`any`、`{Team Rocket}`→`team_rocket`；
- 费用 `●`+`{X}` 混排 → 有序能量表 + `cost_total`；
- 伤害：`120`→fixed、`20×`→multiplier、`-120`→variable（原值留在 `raw`）；
- `[Ability] X` → 特性；`[Tera]` → 规则文本（不误当招式）；
- 同 Card ID 多行 → 聚合为一卡；EN+JP 按行序合并 → `name` + `name_ja`；
- **名称匹配键 `name_key`**：数据集内撇号混用（`N’s` 53 张 vs `N's` 142 张），
  归一键让匹配不受标点影响；基础能量按属性生成 `aliases`（`Basic {P} Energy`
  ↔ `Basic Psychic Energy`）；
- 引擎卡池（人工池 + 扩展池）按 `name_key`/别名回填 `name_zh` 与 `engine_status`：
  **人工池** 40 张 → `mapped`（逐卡校对，其中 7 张靠别名命中）；
  **扩展池** 430 张 → `approximated`（见下节）。库内 `engine_ready` 合计
  **541 / 1267**（`mapped` 36 + `approximated` 505）。

### 扩展卡池生成（`scripts/gen_engine_pool.py`）

由清洗数据集 + K2 解析链自动生成 `engine/data/cards_pool_ext.json`，用于把
「可浏览」扩到「可对战」：

```bash
cd packages/card-db && python scripts/gen_engine_pool.py            # 生成
cd packages/card-db && python scripts/gen_engine_pool.py --dry-run  # 只统计不落盘
```

**准入与诚实口径**（比数量更重要）：

- **严格准入**：仅收录「全部效果条目均已映射到引擎 tag」的卡——任一处未建模即
  **整卡不收**（宁可少收，不留半可执行的卡）；
- **排除 Mega ex**：被击倒取 3 张奖赏，引擎只建模 1/2 张，收进来会算错；
- **`desc_zh` 留空**：源数据集为英文，**不把英文塞进中文字段**冒充翻译；
- **`provenance=dataset-derived`**：装载侧据此把状态标为 `approximated`，
  **不冒充**人工逐卡校对（人工池永远优先，同名时覆盖扩展池）；
- 产物逐卡实证：390 个招式 + 全部特性/训练家穷举可执行，含池内卡的随机对局零
  step 错误（见 `packages/engine/tests/test_v14_pool_ext.py`）。

## 数据与合规（红线）

- 仓库**不包含**任何卡牌/规则文本数据（Kaggle 竞赛数据禁再分发；规则书版权属 TPCi）；
- `data/raw/` 由你在可联网环境自行获取；`fetch_pokemontcg.py` 已与代码分离；
- cards 卡名/效果 `rules_text` 为双轨「原始文本」——入库前如需开源分发请转**自述式转写**（见 demo）。

## 设计要点

- 唯一键 `(series, number)`（对齐 DB v1.1）；`status` 状态机：pending（元数据已入，待解析）→ active（effects_json 可用）/ unsupported（灰名单）；
  - **现状（2026-09-15 实测）**：库里 **1267 张全为 `active`**，`pending` 为 0 ——
    即该状态机的「待解析」阶段已不再发生（卡由 ETL `import_challenge_dataset.py`
    一次性带 `effects_json` 导入）。因此 `parse_effects.py` 这条 pending→active 链路空转。
  - `effects_json` 的**实际结构是 ETL 写入的 `{"library":{…}, "parse":{…}}`**（并非
    `list[CardEffect]`）；1119/1267 张带 `parse` 键，下游 `coverage_backlog.py` 消费的
    正是 `effects_json.parse`（其余 148 张为仅 `{library}` 的条目）。
- 效果结构化 Schema：`list[CardEffect] = kind + trigger + conditions[] + actions[] + source_text + citations[]`；
  - 注：这是**早期设计**形态；现网运行的结构以上一条的 `{library,parse}` 为准。
    `list[CardEffect]` 仍用于 `pokelab/schema.py` 的类型定义（已随 LLM 路径一并降级为参考）。
- 规则知识库：按"章节/裁定条目"原子化分块 → `rule_chunks(source_ref, chunk_text)`；向量化（bge-m3 + FAISS）为后续步骤，`embedding_key` 已预留；
- ~~解析流水线：`rules_text → LLM(Qwen3-4B) → JSON → pydantic 校验 → 入库/转人工`~~
  → **已迁移**：解析与评测统一走 `packages/agent`（`parse_effects()` + `run_k2.py`），
  不再由本包承担入库式解析（详见「废弃登记」）。

## 排障笔记（已实测 2026-09-08）

> 以下两条是**踩坑结论**，对现行 `apps/api/scripts/local_qwen.py` 同样适用
> （该脚本已内置两处处理），故保留；但引用的 `effects.extract_json` 属已废弃模块的
> 同名函数，请对照 `agent.effects.extract_json`（含围栏/前后缀/thinking 残留容错）。

**Qwen3 解析不输出 JSON 的根因 = thinking 模式**：Qwen3 默认在 chat template 触发 `<think>` 思考链，
JSON 被包在思考后且 token 预算常不足 → 修复：`apply_chat_template(msgs, ..., enable_thinking=False)`。
⚠️ 该开关是 **template 渲染变量**，须传给 apply_chat_template；传给 `model.generate(enable_thinking=...)`
会报 `model_kwargs not used`。输出侧另留 `<think>...</think>` 剥离兜底。

**device_map 键格式**：8bit 加载 `max_memory` 须用整数键 `{0: "5.2GB"}`；`"cuda:0"` 键在 transformers 5.x 不被接受。

## 待办（本机联网步骤，需你在浏览器可达网络执行）
1. 下载 Kaggle EN_Card_Data.csv（竞赛 Data 页）或运行 fetch_pokemontcg.py；
2. 导入后如需复核解析质量，走 **agent 侧评测**（不落库）：
   `POKELAB_LLM_BASE=http://127.0.0.1:9997/v1 python packages/agent/examples/run_k2.py --backend llm`
   （~~`parse_effects.py --all`~~ 已废弃，见「废弃登记」）；
3. 官方规则书/Compendium 文本 → kb 工具入库（seed_demo 演示了格式）。
