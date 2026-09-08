# PokéLab Data · 卡牌数据集与规则知识库构建

阶段一（P1a–P1c）数据工程包。运行环境：`D:\anconda\envs\pokemo`（Python 3.12）。

## 目录结构

```
pokelab-data/
├─ pokelab/
│  ├─ schema.py     # pydantic：CardEffect/CardRecord/RuleChunk（对齐 DB v1.1）
│  ├─ db.py         # SQLite：执行 qrwdan/schema.sql 建 10 表 + 增删（cards/rule_chunks）
│  ├─ loader.py     # pokemontcg.io JSON / Kaggle CSV → CardRecord（清洗映射）
│  ├─ effects.py    # 效果 JSON 提取 + pydantic 校验 + Qwen3-4B 解析客户端
│  └─ kb.py         # 规则条款化分块 + rule_chunks 入库 + 检索（向量化预留）
├─ scripts/
│  ├─ fetch_pokemontcg.py  # 【本机联网执行】拉 pokemontcg.io → JSONL
│  ├─ import_cards.py      # JSONL/CSV → cards 表（status=pending）
│  ├─ parse_effects.py     # 本地 Qwen3-4B 8bit 解析 pending 卡 → effects_json
│  └─ seed_demo.py         # 离线 demo：建库+样例卡+规则条款（验证闭环）
└─ data/            # raw/（外部数据，不提交仓库）· db/pokelab.db
```

## 使用（三步走）

```bash
$py = "D:\anconda\envs\pokemo\python.exe"
# 0) 离线验证管道闭环（无需网络/真实数据）
& $py scripts/seed_demo.py
# 1) 真实数据（本机浏览器可达网络时执行；Kaggle 数据需账号下载）
& $py scripts/fetch_pokemontcg.py --set sv3 --limit 300 --out data/raw/pokemontcg.jsonl
& $py scripts/import_cards.py --jsonl data/raw/pokemontcg.jsonl
#    —— 或 Kaggle：& $py scripts/import_cards.py --kaggle-csv <EN_Card_Data.csv>
# 2) 效果解析（本地 Qwen3-4B 8bit，单卡约 1 分钟级）
& $py scripts/parse_effects.py --card-id 1
```

## 数据与合规（红线）

- 仓库**不包含**任何卡牌/规则文本数据（Kaggle 竞赛数据禁再分发；规则书版权属 TPCi）；
- `data/raw/` 由你在可联网环境自行获取；`fetch_pokemontcg.py` 已与代码分离；
- cards 卡名/效果 `rules_text` 为双轨「原始文本」——入库前如需开源分发请转**自述式转写**（见 demo）。

## 设计要点

- 唯一键 `(series, number)`（对齐 DB v1.1）；`status` 状态机：pending（元数据已入，待解析）→ active（effects_json 可用）/ unsupported（灰名单）；
- 效果结构化 Schema：`list[CardEffect] = kind + trigger + conditions[] + actions[] + source_text + citations[]`；
- 规则知识库：按"章节/裁定条目"原子化分块 → `rule_chunks(source_ref, chunk_text)`；向量化（bge-m3 + FAISS）为后续步骤，`embedding_key` 已预留；
- 解析流水线：`rules_text → LLM(Qwen3-4B) → JSON → pydantic 校验 → 入库/转人工`（引擎可执行合法性由引擎层校验，此处只保 Schema）。

## 排障笔记（已实测 2026-09-08）

**Qwen3 解析不输出 JSON 的根因 = thinking 模式**：Qwen3 默认在 chat template 触发 `<think>` 思考链，
JSON 被包在思考后且 token 预算常不足 → 修复：`apply_chat_template(msgs, ..., enable_thinking=False)`。
⚠️ 该开关是 **template 渲染变量**，须传给 apply_chat_template；传给 `model.generate(enable_thinking=...)`
会报 `model_kwargs not used`。输出侧另留 `<think>...</think>` 剥离兜底（effects.extract_json 已内置）。

**device_map 键格式**：8bit 加载 `max_memory` 须用整数键 `{0: "5.2GB"}`；`"cuda:0"` 键在 transformers 5.x 不被接受。

## 待办（本机联网步骤，需你在浏览器可达网络执行）
1. 下载 Kaggle EN_Card_Data.csv（竞赛 Data 页）或运行 fetch_pokemontcg.py；
2. 导入后批量 parse（`--all`）；
3. 官方规则书/Compendium 文本 → kb 工具入库（seed_demo 演示了格式）。
