# PokéLab AI · Web 客户端（apps/web）

React 18 + Vite 前端，**严格对齐**《UI/UX 规范 v1.1》《PRD v1.2 F3/F5/F6/F7/F13》《技术方案 v1.0》
与高保真原型（`prototype/PokéLab_Web_Prototype.html`，随决策链根文档存放于仓库外）
的 token / 组件 / 双层结构一致；规范化实现见 `src/styles.css`。

## 1. 运行

```bash
# 后端（同时托管前端构建产物：/app）
cd <repo>/pokelab-ai && conda activate pokemo
python -m uvicorn apps.api.app.main:app --reload --port 8000
# → 前端 http://127.0.0.1:8000/app/   · API 文档 http://127.0.0.1:8000/docs

# 前端开发模式（5173，/api → 8000 proxy）
cd apps/web && npm install --registry=https://registry.npmmirror.com && npm run dev
# 生产构建（产物 apps/web/dist，后端自动挂载）
npm run build
```

## 2. 页面 ↔ 文档映射

| 页面 | 规范出处 | 关键实现 |
|---|---|---|
| 对战（含批量） | UI §4.1.1–4.1.2 / PRD F5–F7 | 页内分段标签切换「单局对战 / 批量对战」（**现为路由 `/battles` ↔ `/batch`**，见 §2.5）。单局：三列 300/1fr/300；`legal_actions` 1:1 动作面板（SVG 图标，禁 emoji）；HP 血条绿满/红低；回合日志 `role=log`；seed 复制；K3 >2s 兜底提示；`<900px` 动作面板置底 sticky。批量：配额条（≤500/并发≤2/日≤10）、乐观 ETA、**SSE 实时进度**、二次确认取消（保留已完成 K4）、cells 对位表 + 报告导出 |
| 卡组与卡库 | UI §4.1.4 / §3.7 / PRD F2–F3 | 页内分段标签切换「我的卡组 / 卡库」（**现为路由 `/decks` ↔ `/library`**，卡库筛选全在 query string）。卡组：**自由编排**（搜索卡库 → 加减张数 → 60 张 / 同名 4 张 / 基础宝可梦校验）+ 我的卡组表 + 起手 1,000 次模拟。卡库：统计条 + 多维筛选 + 分页表格 + 详情面板（招式/特性/弱点/规则文本） |
| 后台 RL 训练 | UI §4.2.6 / M2 + M6 | 原仪表盘并入本页：关键指标（累计对局 / 批量任务 / 平均回合 / 训练任务）+ 近 7 日批量任务可视化；下方为真实 `/train/jobs` 监控：提交/状态/**SSE 实时日志与曲线 + 连接状态灯**/取消 + 冲 70% 手册折叠区 |

## 2.5 路由与深链接（URL 是唯一真相）

前端不是「一堆按钮切 state」，而是**真路由**（`src/router.jsx`，零依赖，History API）。
页面里凡是「别人也需要看到同一屏」的状态都写进 URL：

| 路由 | 页面 / 状态 | 可分享的例子 |
|---|---|---|
| `/battles` | 对战中心 · 单局（配置区） | `/app/battles` |
| `/battles/:id` | 对局详情（刷新 / 发给别人都回到这一局） | `/app/battles/b_ab12cd34` |
| `/batch` · `/batch/:id` | 批量对战 · 任务详情 | `/app/batch/job_x9` |
| `/decks` · `/decks/:id` | 我的卡组（`:id` 直接把该卡组铺进编排区） | `/app/decks/deck_7f3a` |
| `/library?q=&supertype=&type=&subtype=&series=&engine_ready=&sort=&page=&card=` | 卡库筛选 + 卡片详情 | `/app/library?q=皮卡丘&type=fire` |
| `/train` · `/train/:id` | RL 训练 · 任务监控 | `/app/train/t_42` |
| `/model` | 模型配置 | `/app/model` |

约定：

- **不装路由库**：本应用只有 11 条路由、没有 loader / 嵌套布局，自己写反而能把
  `BASE_URL`（同源部署在 `/app`）剥离逻辑与 vite `base` 严格对齐。
- **筛选类参数用 `replace`**：翻页/输入不该在历史里堆几十条记录；导航与打开任务用 `push`，
  这样浏览器后退才是「回到上一屏」而不是「回退一个筛选项」。
- **深链接能打开靠后端**：`apps/api/app/main.py` 的 `_spa_entry` 对无后缀路径回退
  `index.html`，真实产物按文件返回，**缺失的 `.js` 仍真 404**（回退成 HTML 会被当脚本执行）。
- **链接目标必须命中路由表**：`node e2e/routes_check.cjs`（CI 会跑），拼错 `/decksx`
  这种一字之差会当场变红 —— 否则只有人点到才发现，落到 404 页。

## 3. 组件库（src/components）

| 文件 | 组件 |
|---|---|
| `ui.jsx` | Spinner · Pill · StatusBadge · SegTabs · ErrBar · EmptyState · StatCard · LogBlock · CopyButton · Toast/useToasts |
| `battle.jsx` | VerbIcon（SVG）· PokeChip 52×64/40×50 · HPRow（progressbar）· Zone · ActionPanel · TurnLog · SeedBar |
| `tasks.jsx` | TaskCard（进度/ETA/取消确认）· TaskTable · QuotaHint |
| `cardart.jsx` | `cardArtUri` / `CardArt`：按类别·属性·编号确定性生成原创 SVG 卡面（离线、零外链、同一张卡稳定复现） |
| `verbs.js` / `i18n.js` | 动词表（与引擎同源，F8.2）· i18n 键位（F14，中文为主 + EN 键） |
| `sse.js` | `openJobStream`：EventSource 封装（心跳看门狗 + 游标 sessionStorage 续传 + 失败降级轮询）· `SSE_STATE` 文案映射 |

### SSE 连接状态灯（`SsePill`）

任务运行中在详情卡显示 `<SsePill state=…>`（`.sse-pill[data-sse-state]`），四态：

| state | 文案 | 触发 |
|---|---|---|
| `connecting` | 连接中… | 刚建立 EventSource |
| `live` | 实时 | `onopen`（绿点脉冲动画）|
| `reconnecting` | 重连中 · 自动续传 | onerror 且未超 `maxRetry(4)`；浏览器自动带 `Last-Event-ID` 回放 |
| `polling` | 轮询兜底 | 连续失败 >4 次或心跳静默 >45s → 降级 2s REST 轮询 |

游标持久化在 `sessionStorage`（`pokelab_sse_cursor_<jobId>`）→ **刷新页面也不丢进度**（重连时以
`?last_event_id=` 续传）。

## 3.5 中文展示（i18n）

### 3.6 指令输入（CommandBar）

- 招式按钮与指令候选的 `title` 显示**中文效果文本**（引擎 `display_desc`），
  如「攻击：惊吓 · 造成 10 点伤害。对手从手牌随机弃 1 张。」；
- 组合指令的候选/歧义项统一渲染为**可读文案**（引擎 `options` / 候选自带 `label`），
  避免把动作对象当文本渲染（曾触发 React #31）。


对战页动作面板上方新增**指令输入栏**（`components/battle.jsx` → `CommandBar`）：

- 直接输入中文招式名 / 卡名 / 位置：`幻影俯冲`、`高级球`、`附能量到后备1`、`结束回合`；
- `datalist` 自动补全 + 候选 chips（数据来自引擎 `command_catalog`，与合法动作同源）；
- 歧义或无法识别时展示**候选项点选**（引擎 422 的结构化 `detail`），并在回合日志记录未执行原因；
- 回合日志会回显「指令「X」→ 命中标签」便于追溯。


- **中文卡名可作输入**：卡组管理页示例卡表为中文（A1 真实卡表 60 张），
  解析后展示「解析明细」表（张数 / 中文展示名 / 英文数据键 / 输入语言 / 状态）；
  导出可切换**导出中文**（默认，可直接再导入）与**导出英文**（Limitless 互通）。


- `src/names.js`：`disp(o)`（`display` → `name_zh` → `name` 回退）、`actionContext(view)`（把合法动作里的
  索引解析成中文招式/卡名）、`shortDeckLabel`；
- `verbs.js` 的 `verbLabel(a, ctx)`：动作按钮显示**中文招式名 / 卡名**（如「攻击：幻影俯冲」、
  「训练家：奇妙之月」「进化：多龙奇 → 多龙巴鲁托ex」）；
- 对战棋盘 `PokeChip`、手牌列表、回合日志、批量对位表、胜率矩阵（含 `aria-label`）、预设逐卡表
  全部走中文；预设逐卡表保留英文小字用于追溯；
- 卡组下拉（`api.js` 的 `DECKS`）与短标签 `shortDeck()` 亦为中文。

### 3.7 卡库页（F2 · M1）

- 统计条：总量 / 宝可梦 / 训练家 / 能量 / 招式总数 / **引擎覆盖率**；
- 筛选：搜索（中英日，忽略标点）+ 超类型 + 属性 + 子类型 + 系列 + 引擎可用性 + 排序；
- 表格：卡面缩略图（程序生成原创美术）/ 卡名（中文优先，英文副标）/ 系列编号 / 类型 / 属性筹码 / HP / 招式数 / 引擎徽标；
- 详情面板：**原创卡面大图** + 招式（**能量筹码 + 伤害 + 效果文本**）、特性、弱点/抗性/撤退、其他规则文本；
- 分页 30 条/页；`未建模` 徽标如实标注不可对战的卡（C5 差异透明）。

### 3.8 合规展示（6.1 / 6.2）

- **全站页脚**（恒定可见，不得隐藏）：6.1 内容合规声明要点（研究用途 / 责任由使用者承担 /
  权利方标注 / **AI 生成内容已标识**）+ 6.2 数据隐私声明要点（HTTPS / 存储加密 / 本地化 /
  脱敏 / 不向第三方提供）+ `/compliance` 完整声明链接；
- **卡库页**：合规提示条（权利方 + 非官方无关联 + AI 解析说明）；列表项「AI 解析」徽标；
  详情面板「AI 生成」徽标 + 后端名 + 免责提示 + `spec` 展示 + 未建模动作清单；
- **卡图**：`image_url` 存在时展示，并**紧邻标注权利方**（合规 6.1 要求）。

## 4. 合规与无障碍

- 页脚**恒定**复刻 C3 三段声明 + C1 非商用 + C2 无卡图 + C5 差异透明（不得在 view 层隐藏）；
- a11y：chip `aria-label`、血条 `role=progressbar`、日志 `role=log aria-live`、表格 `th scope`、
  焦点环 3px、触摸目标 ≥44px、`prefers-reduced-motion` 关闭动效；
- 数字一律等宽（HP/牌库/百分比/seed/局数）；红=我方占优（中文数据惯例）。

## 5. 黑屏（空白页）排查手册

> 现象：打开 `/app/` 整页空白、只显示背景色。**根因多为资源 404 或运行时异常**，按序排查：

| # | 检查 | 判据 / 修复 |
|---|---|---|
| 1 | 后端是否为**最新进程** | `netstat -ano \| findstr :8000` 查 PID；若 `GET /batch/jobs` 返回 405，说明跑的是旧代码 → 杀掉旧 PID 重启 |
| 2 | 构建资源前缀 | `/app/` 下必须引用 `/app/assets/*`（`vite.config` build `base='/app/'`）；若引用 `/assets/*` → 全部 404 → 黑屏 |
| 3 | 资源可达性 | `curl -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/app/assets/<hash>.js` 应为 200 |
| 4 | 运行时异常 | `node check_render.cjs http://127.0.0.1:8000/app/ <out.png>` → 看 `errors` / `failed`；`React #31` 类错误=把对象当 React child 渲染 |
| 5 | 浏览器缓存 | `Ctrl+F5` 强刷（index.html 引用新 hash 后旧缓存无害） |

**构建输出目录**：Windows 上 `dist` 若被进程占用（EPERM）→ 用 `VITE_OUTDIR=dist3 npm run build`，
后端按 `dist3 → dist2 → dist` **顺序挂载第一个存在的目录**（`apps/api/app/main.py`）。

**验证工具**：`check_render.cjs`（单页渲染+console 采集）、`shoot_pages.cjs`（前台 4 页+后台 3 页批量截图）
→ 注：受限环境下 Node 写项目盘可能 EPERM，可输出到用户主目录（如 `$POKELAB_SHOTS（默认系统临时目录/pokelab_shots）`）。

## 6. 已知边界

- 卡池为 demo 卡组（M1 卡池补齐后替换预设 A1–A4 的真实卡表，已在 UI 标注）；
- 对局会话驻留后端内存（重启需重开）；后台 M1/M2/M3 页面为"信息卡 + 链接"级别（P1 增补）；
- 受限环境构建已设 `emptyOutDir:false`（沙箱回收站不可用），正常环境可移除。
