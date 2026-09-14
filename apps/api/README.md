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

> 预设卡表来自 `packages/engine/engine/data/{cards_real,decklists}.json`（M1 卡池补齐）；
> 引擎未实现的卡在对战时以基础能量占位并在响应/报告中逐卡标注。

### 卡库浏览（F2 · M1 清洗入库 1267 张卡）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/cards` | 分页列表：`q`（中/英/日，忽略标点）、`supertype`、`type`、`subtype`、`series`、`engine_ready`、`sort`(id/name/hp/series)、`page`/`page_size`(≤200) |
| GET | `/cards/{id}` | 卡详情：招式（费用/伤害/效果）+ 特性 + 卡面元数据 + `rules_text` |
| GET | `/cards/stats` | 卡库总览：构成 / 系列分布 / 招式特性数 / **引擎覆盖率** |

```bash
curl "localhost:8000/cards?q=dragapult"                      # 英文
curl "localhost:8000/cards?q=%E5%A4%9A%E9%BE%99"             # 中文「多龙」
curl "localhost:8000/cards?supertype=Trainer&series=TWM"
curl "localhost:8000/cards?engine_ready=true"                # 仅引擎可对战（36 张）
curl "localhost:8000/cards/stats"
```

> - **`engine_ready` 如实暴露**：卡库 1267 张中仅 36 张引擎已建模，其余**可浏览/可组卡
>   但不可对战**（UI 与导入校验据此显示灰名单，PRD C5 差异透明）；
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
