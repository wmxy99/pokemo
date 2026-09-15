# PokéLab AI · 后端 API 服务文档（FastAPI）

> 自研规则引擎（engine）+ AI 决策链路（agent，BC/PPO/DQN/MCTS/蒸馏）封装为 FastAPI 服务。
> 覆盖：人机对战（F5）、批量自动对战（F6）、平衡性报告导出（F7）、训练任务管理（M2/M6）。
> 对齐 PRD v1.2 / 技术方案 v1.0（M4 对战与数据分析；训练监控支撑模块 2 迭代）。
> `Unofficial community data · 非官方数据（PRD C1）· 无官方卡图（C2）· 非商用（C1）`

## 0. 快速开始

```bash
cd <repo>/pokelab-ai
conda activate pokemo
pip install -i https://mirrors.aliyun.com/pypi/simple -r apps/api/requirements.txt

# 启动（开发热载）
python -m uvicorn apps.api.app.main:app --reload --port 8000
# 或
python -m uvicorn apps.api.app.main:app --host 0.0.0.0 --port 8000

# 交互式文档：http://127.0.0.1:8000/docs   （OpenAPI: /openapi.json）
# 测试
python -m unittest apps.api.tests.test_api -v
```

## 1. 认证与授权（真 JWT · PRD F13/M5）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/auth/register` | body `{name, password}` → `{name, role, token}`；**首个注册者为 admin**；重名 409 |
| POST | `/auth/login` | 校验 PBKDF2 哈希；错误/不存在统一 **401**（不泄露用户存在性） |
| GET | `/auth/me` | 返回当前身份（需 Bearer） |
| GET | `/admin/users` | 用户列表（**admin**） |
| PATCH | `/admin/users/{name}?role=user\|admin` | 调整角色（**admin**） |

- 调用受保护资源带 `Authorization: Bearer <JWT>`；**无令牌 = guest**（游客可试玩对战/批量，配额独立）；
- 设置环境变量 `POKELAB_REQUIRE_AUTH=1` 可强制登录（无令牌一律 401）；
- 口令存储：`pbkdf2_sha256$200000$<salt>$<hash>`（HMAC-SHA256，20 万迭代，无明文）；
- 令牌：HS256 JWT（sub/role/iat/exp，默认 24h 有效）；密钥取 `POKELAB_JWT_SECRET`，
  否则自动生成于 `apps/api/data/.jwt_secret`（已 gitignore）；
- **RBAC**：admin/user/guest；`/train/*` 需登录（guest 403）；任务/对局按 owner 隔离，
  跨用户访问 403（admin 例外）——满足 PRD F13「越权访问被拒」验收方向。

## 2. 人机对战（PRD F5）

### 创建对局
```
POST /battles
{ "deck_a_id": "deck_...", "deck_b": "draco", "seed": null,
  "ai_iters": 0, "player_side": 0 }
```
| 字段 | 说明 |
|---|---|
| deck_a / deck_b | 演示卡组名（lightning/fire/psychic/draco/ability_demo 等，经 `engine.decklib.make_deck`）|
| **deck_a_id / deck_b_id** | **F3 已导入卡组 id**（优先于名称）；owner 隔离；卡组含未收录卡/张数不足 → 422（"粘贴卡表 → 直接开打"闭环）|
| seed | 确定性种子；留空随机。同一 seed 可重放（F10 数据基础） |
| ai_iters | 对手强度：0=规则基线 AI（白盒，发布门槛参照）；>0=MCTS 迭代数（引擎 rollout 教师级） |
| player_side | 人类操作方：0=deck_a（默认） |

响应 201：
```json
{ "id": "btl_...", "seed": 20260909, "player_side": 0,
  "turn": 1, "phase": "main", "winner": null,
  "ai_auto_first": [],            // 若对手先手，服务端已自动走完的 AI 动作
  "view": { "legal": [...], "me_state": {...}, "opp_state": {...}, ... } }
```

### 提交人类动作
```
POST /battles/{id}/actions
{ "action": { "verb": "pass" } }      // 必须是 view.legal 中合法动作
```
- 服务端执行动作 → 自动代走 AI 回合（若轮 AI）直至**再次轮到你或终局**；
- 响应含 `winner` 与最新 `view`；
- 错误：`404` 对局不存在（内存会话，服务重启后需重建）· `409` 非你回合/已结束 · `422` 非法动作（动作必须=引擎 legal_actions，不一致即 bug，PRD F5）。

### 查看对局
```
GET /battles/{id}     → { id, seed, turn, phase, finished, winner, view }
```

## 3. 批量自动对战（PRD F6，异步）

### 提交批量任务
```
POST /batch/jobs
{ "deck_a": "lightning", "deck_b": "fire", "games": 200,
  "seed_start": 20260909, "ai_iters": 0 }
```
- 后台线程逐局执行（AI vs AI，两侧规则基线；结果含先手胜负口径 A=deck_a）；
- **配额**：单任务 `games ≤500` · 全局并发 `≤2` · 每用户日配额 `≤10`（PRD §5.2；超限 429/422）；
- 响应 202：`{ "id": "batch_...", "status": "running" }`。

### 查询进度 / 取消 / 报告
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/batch/jobs/{id}` | 状态 + 进度 `{done,n}` + result（done 时） |
| POST | `/batch/jobs/{id}/cancel` | **可中断且保留已完成局（K4 验收）** |
| GET | `/batch/jobs/{id}/report?format=md` | 平衡性报告（format：`md`（默认）/`csv`/`json`）；含先后手胜率与平均回合 + `Unofficial community data` 声明（F7/C1） |

任务状态机：`queued → running → done | failed | cancelled`。

### 卡组池矩阵任务（F7 胜率矩阵，直出对位数据）
```
POST /batch/pool-jobs
{ "decks_a": ["lightning","fire"], "decks_b": ["draco","psychic"],
  "games_each": 60, "seed_start": 20260910 }
```
- 语义：`decks_a × decks_b` **全对位**各跑 `games_each` 局（上限：池任务总局数 2000；
  并发/日配额与单对位任务共用）；
- 响应 202：`{ id, status, pairs, total_games }`；
- 完成的 `result.cells`：`[{deck_a, deck_b, n, wins_a, losses_a, draws, win_rate_a, ci95, avg_turns}]`
  —— **统计页胜率矩阵直接消费**（`GET /stats/summary` 的 `matrix` 会展开并聚合多任务 cells）；
- 报告：`/batch/jobs/{id}/report?format=md|csv|json`（md 含对位明细表格；csv 每行一个对位）。

### 自由指令（中文招式名 / 卡名）

`POST /battles/{id}/actions` 的 body 支持三种写法（**三选一**）：

| 写法 | 说明 |
|---|---|
| `{"action": {...}}` | 引擎 legal 动作（与 `view.legal` 严格 1:1，PRD F5） |
| `{"action": {"verb":"attack","attack_name":"幻影俯冲"}}` | 只给名称/部分字段 → 引擎补全为 legal 动作后执行 |
| `{"command": "幻影俯冲"}` | **中文自然指令**：招式名 / 卡名 / `附能量到后备1` / `结束回合` |

- 响应新增 `matched`（命中的指令文案）与 `used_action`（实际执行的引擎动作）；
- 指令无法解析或存在歧义 → **422**，`detail` 为结构化对象：
  `{error, matched, ambiguous, candidates[], legal[]}`（前端据此展示候选项）；
- `GET /battles/{id}/commands`：当前可用指令清单（中文 label + 对应动作）；
  创建对局与每次动作的响应里也带 `commands`。

```bash
curl -X POST localhost:8000/battles/$BID/actions -H "Authorization: Bearer $TK" \
  -H 'Content-Type: application/json' -d '{"command":"幻影俯冲"}'
```

### 实时进度（SSE，替代轮询 · 心跳 + 断线续传）
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/batch/jobs/{id}/stream` | `text/event-stream`：`event: update`（状态+进度+result）/`event: progress`（业务事件，带 `id`）/`event: replay`（续传回放）/`event: end` / `event: timeout` |
| GET | `/train/jobs/{id}/stream?lines=60` | 同上，附带 `log_tail` 与 `curve`（实时训练曲线） |

- **鉴权**：EventSource 不能自定义请求头 → 支持 `?token=<JWT>`（无 token 时按 guest/header 逻辑）。
- **心跳**：空闲每 15s 发送 SSE 注释行 `: ping <ts>`，穿过反代/网关的空闲超时，避免连接被静默掐断；
  首行 `retry: 3000` 指定浏览器重连间隔。
- **断线续传（Last-Event-ID）**：业务事件带 `id: <seq>`（服务端每任务保留最近 500 条），
  浏览器重连自动回传 `Last-Event-ID`，服务端先回放 `seq > last` 的事件（`event: replay`）再续推；
  也可用 `?last_event_id=<seq>` 手工续传（前端把游标另存 sessionStorage，**刷新页面也不丢进度**）。
- 前端 `src/sse.js` 封装：连接状态机 `connecting → live → reconnecting → polling`（连续失败 >4 次自动
  降级为 2s 轮询兜底），界面以「实时 / 重连中 · 自动续传 / 轮询兜底」状态灯呈现。

### 卡组与预设（F3 · M1 真实卡池）
| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/decks/import` | 粘贴 Limitless 文本 → 解析 + 校验（≤60 / 同名≤4（基本能量豁免）/ 含基础宝可梦）→ 落库 |
| GET | `/decks/presets` | 预设对手池 A1–A4：**60 卡真实卡表** + 可执行率 + 占位清单 |
| GET | `/decks/presets/{id}` | 预设逐卡清单：每卡 `mapped / approximated / replaced` 三态 + 覆盖率（C5 差异透明）|
| GET | `/decks` · `GET /decks/{id}` · `POST /decks/{id}/opening` · `GET /decks/{id}/export` | 我的卡组 / 起手模拟 / 导出往返 |

> 预设卡表来自 `packages/engine/engine/data/{cards_real,pool_ext,decklists}.json`
> （M1 卡池补齐 + v1.4 扩容）；
> 引擎未实现的卡在对战时以基础能量占位并在响应/报告中逐卡标注。

### 卡库浏览（F2 · M1 清洗入库 1267 张卡）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/cards` | 分页列表：`q`（中/英/日，忽略标点）、`supertype`、`type`、`subtype`、`series`、`engine_ready`、`sort`(id/name/hp/series)、`page`/`page_size`(≤200) |
| GET | `/cards/{id}` | 卡详情：招式（费用/伤害/效果）+ 特性 + 卡面元数据 + `rules_text` |
| GET | `/cards/stats` | 卡库总览：构成 / 系列分布 / 招式特性数 / **引擎覆盖率**（含 `engine_by_status`） |

```bash
curl "localhost:8000/cards?q=dragapult"                      # 英文
curl "localhost:8000/cards?q=%E5%A4%9A%E9%BE%99"             # 中文「多龙」
curl "localhost:8000/cards?supertype=Trainer&series=TWM"
curl "localhost:8000/cards?engine_ready=true"                # 仅引擎可对战（426 张）
curl "localhost:8000/cards/stats"
```

> - **`engine_ready` 如实暴露**：卡库 1267 张中 **426 张**引擎已建模，其余**可浏览/可组卡
>   但不可对战**（UI 与导入校验据此显示灰名单，PRD C5 差异透明）；
> - **426 内部还要再分三态**（`/cards/stats` 的 `engine_by_status`）：
>   `mapped` **36**（人工池，逐卡校对）· `approximated` **390**（扩展池，解析链推导
>   **未逐卡校对**）· `unmatched` **841**。**近似 ≠ 等价**，展示时不可混为一谈；
> - **中文覆盖同样如实暴露**：`with_zh` = **36**（全库仅 36 张有中文名）。源数据集为
>   英文，**不把英文塞进中文字段**冒充翻译；列表/详情的展示名按「中文优先、回退英文」
>   处理，因此绝大多数卡片显示英文原名，属**已知且有意为之**的状态，非缺陷；
> - 数据源为 `packages/card-db/data/db/pokelab.db`，**只读** + mtime 缓存；
>   更新走 ETL 脚本（见 `packages/card-db/README.md`）；
> - 卡池匹配（`/decks/import`）已接入该卡库并按 `name_key`/别名归一，
>   F3 可识别卡名从 40 张提升到 1267 张。

## 4. 训练任务管理（M2/M6）

> 训练在**独立子进程**执行仓库内 `examples/run_k1_longtrain.py`（BC→PPO vs 基线→冻结快照→MCTS 蒸馏 课程），API 仅做编排/监控/取消，训练产物隔离到数据目录 `train_jobs/`（见 §5）。

### 提交训练
```
POST /train/jobs
{ "stage1_episodes": 2000, "stage2_episodes": 0, "stage3_episodes": 0,
  "bc_episodes": 40, "lr": 0.003, "features": "legacy", "note": "…" }
```
- 并发 ≤2（429）；每任务权重 `policy_{id}.pt` + 报告 `K1_report_{id}.md` 输出到 jobs 目录；
- 响应 202：`{ "id": "train_...", "status": "running", "log": "<路径>" }`。

### 监控
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/train/jobs` | 该 owner 训练任务列表 |
| GET | `/train/jobs/{id}` | 状态 + 最近日志 40 行 + result（done 时含 `curve`=解析出的 vs baseline 胜率曲线、`exit_code`、权重路径） |
| GET | `/train/jobs/{id}/log?lines=100` | 原始日志尾部 |
| POST | `/train/jobs/{id}/cancel` | 终止子进程 → `cancelled` |

- 进度曲线来源：`[k1:eval:*] vs baseline win_rate=*` 行的 stdout 解析；
- **边界**：训练为长任务（小时级），API 不阻塞；服务重启后进程随重启终止（任务状态留在 SQLite，重启不自动恢复——文档声明）。

## 4.5 展示层本地化（i18n，中文优先）

> 原则：**英文名是数据键**（Limitless 文本匹配、文件/库字段、路径参数都用英文），
> 对外展示（响应展示字段 / 报告 / 矩阵）一律优先中文。

| 接口 | 中文字段 |
|---|---|
| `GET /decks/presets` | `label`（中文）+ `label_en`（英文原型追溯） |
| `GET /decks/presets/{id}` | 每卡 `display`（中文）+ `name`（英文键） |
| `POST /decks/import` | `parsed[].meta.name_zh/display`（校验提示渲染用） |
| `POST /battles` · `GET /battles/{id}` | `view` 内手牌/场上/招式/特性均带 `display`（中文） |
| `GET /batch/jobs/{id}/report` | md：卡组名中文；csv：**表头与卡组名均中文**；json：保留英文键 + `deck_a_zh`/`deck_b_zh` |
| `GET /stats/summary` | `matrix[].deck_a_zh/deck_b_zh` |

### 中文卡名作为输入（粘贴中文卡表即可）

- `POST /decks/import`：**卡名可用中文**，系列/编号可省略（`4 多龙巴鲁托ex`）；
  解析结果每条附 `canonical`（英文数据键）· `display`（中文展示）· `input_lang`（`zh`/`en`）；
- **中英混写同一张卡按同一张合并计数**（`4 Dragapult ex` + `3 多龙巴鲁托ex` = 7，超 4 张上限报错）；
- `GET /decks/{id}/export?lang=zh|en`：`zh`（默认）输出中文卡表，`en` 输出 Limitless 规范名；
  **中文输出可直接再导入**（往返一致）；
- `POST /battles` 的 `deck_a`/`deck_b` 亦接受中文卡组名（如 `A1 · 多龙巴鲁托ex / 黑夜魔灵`、`龙系进化演示卡组`）。

实现见 `apps/api/app/labels.py`（`deck_label_zh` / `card_display`；未识别的用户自建卡组名原样保留）。

## 4.8 M6 看门狗数据源

| KPI | 数据源 |
|---|---|
| K1 | `artifacts/curve.json`（训练曲线最近一次评估） |
| **K2** | **`packages/agent/artifacts/k2_report.json`**（`examples/run_k2.py` 产出；含 strict / seen / holdout / 卡池 tag 映射率） |
| K3 / K4 | 引擎内建基准与批量任务实测 |

K2 未评测时看门狗显示「尚未评测」并给出运行命令，不显示臆测数字。

## 4.9 合规与隐私（6.1 / 6.2）

> 权威文本见 **`docs/COMPLIANCE.md`**；代码侧以 `apps/api/app/compliance.py` 提供机器可读版本，二者必须一致。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/compliance` | 6.1 内容合规 + 6.2 数据隐私声明 + 红线 C1–C5 + **实现状态**（AI 标识 / 脱敏规则 / 加密 / 本地模式 / 审计） |
| GET | `/compliance/audit?limit=50` | 外部 LLM 调用审计（**admin**）：仅端点/模型/字符数/脱敏计数，**不含原文** |

**AI 生成内容标识（6.1）**：解析结果带 `ai_generated` / `generated_by` / `ai_notice`；
`/cards/{id}.effects_parsed`、`/cards` 列表项 `ai_generated`、`/cards/stats.ai_parsed_cards`
均如实暴露；UI 显示「AI 生成 / AI 解析」徽标。

**数据隐私（6.2）**：

| 措施 | 开关 / 位置 |
|---|---|
| HTTPS 传输 | `uvicorn --ssl-keyfile/--ssl-certfile`；自签助手 `apps/api/scripts/make_dev_cert.py` |
| 存储加密 | 口令 PBKDF2-SHA256(20 万次)；字段加密 `POKELAB_FIELD_ENC=1`（Fernet，`apps/api/app/crypto.py`） |
| 本地化部署 | `POKELAB_LOCAL_ONLY=1` → **禁止一切外部网络调用**（外部 LLM 端点被拒并回退规则基线） |
| 外发前脱敏 | `packages/agent/agent/privacy.py`：邮箱/手机/证件/卡号/密钥/本地路径/自定义敏感词 |
| 不用于服务外用途 | 本地模式可完全离线；外发仅发送脱敏文本；全量调用可审计 |

```bash
curl localhost:8000/compliance | jq '.content_compliance.statement, .data_privacy.statement'
curl localhost:8000/compliance | jq '.red_lines[] | {id, statement}'   # C2 已修订为「可用+标注」
```

## 4.10 本地 Qwen3-4B 量化服务（离线推理 · ADR-LLM-001）

> ADR-LLM-001：**解析/解说统一本地 Qwen3-4B（8bit）**，K2 不达标切云端保底。
> 本节是把该决策落成「可被 PokéLab 直接调用的端点」的运维说明。

| 脚本 | 作用 |
|---|---|
| `scripts/local_qwen.py` | 量化加载 + **OpenAI 兼容服务**（`serve` / `info` / `generate`） |
| `scripts/qwen_precision.py` | **三层精度对照**（权重级 / 困惑度 / 端到端）+ 档位对照，产出 `artifacts/qwen_precision_report*.{md,json}` |
| `scripts/k2_local_eval.py` | **K2 达标率实测**（本地模型真跑卡池解析），产出 `artifacts/k2_local_eval.{md,json}` |

**为什么是 8bit 而不是 4bit**（**已实测两档，非推断**）：本机 RTX 4050 **6GB**
放不下 bf16（≈8.05GB）；8bit(LLM.int8) 实测 footprint **4.11GB**，且 **`lm_head`
不被量化**（logits 仍 bf16 计算，token 选择不受 int8 权重误差影响）。

4bit(NF4+双量化) 只要 **2.42GB**、且**快约 3.9 倍**，但代价是**精度明显退化**：

| 档位 | 权重占用 | PPL 偏差 | 第 1 层权重误差 | 生成速度 | 验收 |
|---|---:|---:|---:|---:|---|
| **8bit**（默认） | 4.11 GB | **−0.147%** ✅ | 1.01% | 4.6 tok/s | **通过** |
| 4bit | 2.42 GB | **+21.8%** ❌ | 9.28%（×9.2） | **17.98 tok/s** | 未通过 |

故**默认 8bit**（`POKELAB_QWEN_QUANT`）——与「确保数据精度」的验收口径一致。
4bit 仅作**大批量离线解析**的候选（须先验 K2 抽取达标率）。完整对照见
`artifacts/qwen_precision_compare.md`。

> 4bit 快 3.9 倍不是笔误：两档生成的 token 数相同，差异来自解码是 **memory-bound**
> ——4bit 权重少一半（读得少），而 8bit 的 `MatMul8bitLt` 每次前向还要做**离群值分解**，
> batch=1 时这笔固定开销占比很高。

### 起服务（约 20s 加载 4.11GB）

```bash
python scripts/local_qwen.py serve                 # 默认 127.0.0.1:9997
curl http://127.0.0.1:9997/healthz                 # {"ok":true,...}
curl http://127.0.0.1:9997/v1/models
```

然后在后台 **「模型配置」** 页选预设 `本地 vLLM/Ollama` 或直接填
`http://127.0.0.1:9997/v1`（Key 留空），保存即生效。

**合规**：`127.0.0.1` 属本机端点，`privacy.is_external()` 判定为**非外发**——
因此 `POKELAB_LOCAL_ONLY=1`（完全离线）下**依然可用**，这正是 6.2「支持本地化部署」的落地路径。
调用仍走 `agent.llmclient.chat()` 统一出口（端点校验 → 脱敏 → 调用 → 审计），不绕过合规链路。

### 三层精度对照

```bash
python scripts/qwen_precision.py --layer 1     # 权重级（≈1 分钟）
python scripts/qwen_precision.py --layer 2     # 困惑度（含 bf16 参考，较慢）
python scripts/qwen_precision.py --layer 3     # 端到端
python scripts/qwen_precision.py --layer all   # 全部（建议后台跑）

python scripts/qwen_precision.py --layer all --quant 4bit   # 换档位（写 *_4bit.* 不覆盖 8bit）
python scripts/qwen_precision.py --layer compare            # 两档对照（纯读数，不占 GPU）
python scripts/qwen_precision.py --layer report             # 只重渲染 Markdown（改措辞后免重跑）
```

三层各自的含义与「为什么需要」写在 `qwen_precision.py` 的模块 docstring 里；
**结论数字以 `artifacts/qwen_precision_report.md`（8bit）与
`artifacts/qwen_precision_compare.md`（两档对照）为准**（可随时复跑覆盖）。

> `--layer report` / `--layer compare` **不碰 GPU**：改了报告措辞或判据后，
> 用它们从已有 JSON 重渲染即可，不必再花几分钟重跑量化对照。

### K2 达标率实测（本地模型 · 决定是否启用云端保底）

`rule` 基线在卡池上的 support_rate = **99.7%**，但那是**离线规则基线**的数字，
不能代表 LLM 后端。ADR-LLM-001 规定「K2 不达标 → 切云端保底」，故必须**真跑**：

```bash
python scripts/k2_local_eval.py --dry-run                # 只跑 rule + 抽样，秒级验证口径
python scripts/k2_local_eval.py --sample 60              # 抽样实跑（8bit 约 35–60 分钟）
python scripts/k2_local_eval.py --sample 0               # 全量 328 条（8bit 约 3–5 小时）
```

**口径要点（否则会把数字读高）**：

1. **不能直接用 `support_rate`** —— 解析失败（无 spec）时 `to_engine_tags` 会被短路成
   `{tags:[], unsupported:[]}`，`not unsupported` 为真 → **失败被计入「完全支持」**；
   且 `parse_effects` 的 llm 分支在 **LLM 空返回时用 rule 结果兜底**，而 `backend`
   仍报 `"llm"`。两者叠加会让 LLM 达标率**系统性虚高**。
2. 改用 `raw` 字段判断（它保留的是 **LLM 原始输出**，非兜底结果），严格三分：
   **A 解析失败 / B 解析成功但引擎未建模 / C 完全映射**；B 类**不算**解析失败。
3. **按语料语言分组**：卡池 328 条里 **294 条（89.6%）是纯英文**（扩展池无 `desc_zh`，
   `display_desc` 回退英文），仅 34 条中文；而提示词是中文撰写的，两者难度不同。
4. 报告给 **Wilson 95% 置信区间**（小样本比正态近似稳），并列出 A 类失败明细。

**实测结果（n=60 抽样，seed=42，已跑完）**：

| 指标 | 8bit（默认） | 4bit |
|---|---:|---:|
| **LLM 可用率**（B+C） | **96.7%**（95%CI 88.6–99.1%）**PASS ✅** | 83.3%（72.0–90.7%）**未达 ❌** |
| LLM 空返回率 | **1.7%**（1/60） | **16.7%**（10/60） |
| 完全映射率（C） | 70.0% | 60.0% |
| 对照 rule 同样本 | 98.3% | 98.3% |
| 均耗时 | 41.4 s/条（全量 ≈3.8 小时） | 6.8 s/条（全量 ≈0.6 小时） |

**结论：8bit 下 K2 达标（96.7% ≥ 85%）→ 不需要启用 ADR-LLM-001 的「云端保底」。**
4bit 虽快 6 倍，但空返回率是 8bit 的 10 倍、可用率低于门槛 → **不可用于 K2 抽取**。

**关于「完全映射率 70% vs rule 98.3%」（重要，别误读）**：这不是 LLM 比 rule 差 28 个点。
B 类 16 条里 **rule 后端都识别出了具体机制**（`coin_flip`/`shield`/`status`/`damage_mod`
—— 这些**引擎已建模**），而 LLM 把它们**笼统合并成了 `rule`**（静态规则修改，引擎未建模
该类型）。即这 16 条反映的是 **LLM 解析粒度不足（丢失可执行语义）**，而非引擎没做。
**可操作的改进点**：提示词的 `action.type` 枚举缺「抛币」这类机制级取值（只有
`damage/draw/attach/heal/discard/switch/search/evolve/spread/move_damage/knockout/status/rule`），
模型只能退回 `rule`。补枚举 + few-shot 是提升完全映射率最直接的一步。

> ⚠ **口径区分（必读）**：本节测的是**卡池解析可用率**（能否交付合法结果 + 映射到
> 引擎 tag），与 rule 的 99.7% support_rate **同口径**。它**不等于语义准确率** ——
>
> **黄金集（50 条人工真值）实测已跑完：LLM 后端 strict 32.0%**（seen 35% / holdout 20%），
> **未达 85% 门槛 → 按 ADR-LLM-001 应启用「云端保底」**。两组数字**不矛盾**：
> `parse_rate` 98%（格式没问题）但**数值准确率仅 46%**、kind 64% → 短板在语义。
> 对照 rule 后端在同一黄金集上 strict **100%**。报告：`artifacts/k2_report_llm.md`；
> 改动前基线对照见 `artifacts/k2_report_llm_before.json`。
>
> **耗时已变化**：提示词补入机制级枚举与 few-shot 后（渲染 6105 字符），单条从 41.4 s
> 涨到 **141 s** → 全量 328 条约 **13 小时**（精简提示词已列为 P0）。

> **bf16 参考怎么来的**：6GB 显存装不下 bf16，故用 `device_map="auto"` + `max_memory`
> 把部分层放 CPU 计算——**计算精度仍是 bf16**，只是慢。比「拿 4bit 当参考」严谨得多：
> 后者只能说明两个量化档的差异，说明不了与原始模型的差距。

### 已知边界

| 项 | 现状 |
|---|---|
| 并发 | **单卡串行**（服务内加锁），不支持批量并发；请求会排队 |
| 显存余量 | 8bit 后仅余 ≈0.6–0.9GB 给 KV cache → 上下文建议 ≤4K；与 RL 训练**务必错峰** |
| 首字延迟 | 6GB 卡上约 3–4 tok/s（贪心），长输出请调 `POKELAB_QWEN_MAX_NEW` |
| 4bit 档 | 已实现（`POKELAB_QWEN_QUANT=4bit`）但**非默认**；如需长上下文/并行训练可切，需接受精度退化 |

## 5. 存储与数据目录

**数据目录自动探测**（首个可写者胜，避免协同/沙箱写保护导致 `readonly database`）：
```
POKELAB_DATA_DIR  →  ~/.pokelab  →  <系统临时目录>/pokelab  →  apps/api/data
```
若探测到的目录里 `api.db` 因既有文件写保护不可写，会自动回退到 `api_<时间戳>.db`
（可写但**不继承历史数据**——仅受限环境兜底；正常环境始终使用 `api.db`）。

| 位置 | 内容 |
|---|---|
| `<数据目录>/api.db` | SQLite：`battles` 元数据 / `jobs`（batch/train 状态、params、result）/ `users` / `decks`（F3 已导入卡组与解析结果） |
| `<数据目录>/train_jobs/` | 训练权重 + K1 报告 + 子进程日志 |
| `<数据目录>/.jwt_secret` | JWT 密钥（0600，gitignore） |
| 内存 | 对局引擎会话（Battle 对象，重启清空）+ 批量进度 + 训练 Popen 句柄 |

## 6. 已知边界与后续（诚实标注）

| 项 | 现状 | 后续 |
|---|---|---|
| 认证 | ✅ 真 JWT + PBKDF2 + RBAC（admin/user/guest） | 后续：refresh token / 登录限流 / 配额管理面板（P1） |
| 卡组 | 演示卡组名（make_deck） | 文本粘贴解析 + 校验 + 预设池（F3 Web 交付） |
| 对局会话 | 进程内存，重启即失 | SQLite 事件日志 + seed 重放引擎（F10） |
| 批量对手 | 规则基线 AI | 可加载策略权重（policy 权重复用 eval_matrix 路径） |
| 训练恢复 | 无（重启后任务终止标记保留在 DB） | 断点续跑（--from-weights）由 API 重新提交触发 |
| 数据库 | sqlite 单文件 | 向量/卡库分离存储随 M3 演进 |

## 7. curl 速查（Windows PowerShell 换行用 `` ` ``）

```bash
BASE=http://127.0.0.1:8000
# 注册
curl -s -X POST $BASE/auth/register -H "Content-Type: application/json" \
     -d '{"name":"alice","password":"pass1234"}'
# 建对局并走一步
curl -s -X POST $BASE/battles -H "Content-Type: application/json" \
     -d '{"deck_a":"lightning","deck_b":"fire","ai_iters":0}'
curl -s -X POST $BASE/battles/<id>/actions -H "Content-Type: application/json" \
     -d '{"action":{"verb":"pass"}}'
# 批量 200 局 → 轮询 → 报告
curl -s -X POST $BASE/batch/jobs -H "Content-Type: application/json" \
     -d '{"deck_a":"lightning","deck_b":"fire","games":200}'
curl -s $BASE/batch/jobs/<id>/report?format=md
# 训练任务
curl -s -X POST $BASE/train/jobs -H "Content-Type: application/json" \
     -d '{"stage1_episodes":2000}'
curl -s $BASE/train/jobs/<id>/log?lines=50
```
