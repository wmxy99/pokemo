# PokéLab AI · Web 客户端（apps/web）

React 18 + Vite 前端，**严格对齐**《UI/UX 规范 v1.1》《PRD v1.2 F3/F5/F6/F7/F13》《技术方案 v1.0》
与高保真原型 `D:\pokemo\prototype\PokéLab_Web_Prototype.html`（token/组件/双层结构一致）。

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
| 对战 | UI §4.1.1 / PRD F5 | 三列 300/1fr/300；`legal_actions` 1:1 动作面板（SVG 图标，禁 emoji）；HP 血条绿满/红低；回合日志 `role=log`；seed 复制；K3 >2s 兜底提示；`<900px` 动作面板置底 sticky；**我方/对手可选 F3 已导入卡组**（deck_id 参战） |
| 批量对战 | UI §4.1.2 / F6 + F7 | 模式切换 **单对位 / 卡组池矩阵**（多选卡组池 × 每对位局数，直出 F7 矩阵）；配额条（≤500/并发≤2/日≤10）；乐观 ETA；**SSE 实时进度（EventSource，异常回退轮询）**；二次确认取消（保留已完成 K4）；cells 对位表 + 报告导出 |
| 统计报告 | UI §4.1.3 / F7 | KPI（含**卡组池任务/矩阵对位数**）+ **胜率矩阵热力图**（底色=胜率、饱和度=样本量、行/列聚合、图例、`tabIndex` 键盘可达 + `aria-label`）+ 导出条 CSV/MD/JSON/复制 + MD 预览 + `Unofficial community data` |
| 卡组管理 | UI §4.1.4 / F3 | mono textarea 导入 → 校验（60/同名4/含基础，逐条错误）+ 三色 pill + 我的卡组表（可**直接用于对战**）+ **预设 A1–A4 真实卡表（点开看逐卡 `可映射/近似/占位`）** + 起手 1,000 次模拟 |
| 后台仪表盘 M6 | UI §4.2.1 | 三 KPI + **K1–K4 看门狗表**（诚实标注 K1 未达成）+ 近 7 日 sparkline + F16 导出（beta 置灰） |
| 后台 RL 训练 | UI §4.2.6 / M2 | 真实 `/train/jobs` 监控：提交/状态/**SSE 实时日志与曲线 + 连接状态灯**/取消 + 冲 70% 手册折叠区 |
| 后台用户管理 M5 | UI §4.2.2 / F13 | 用户/角色/日配额表格 + 角色切换（admin）；越权隔离提示文案 |

## 3. 组件库（src/components）

| 文件 | 组件 |
|---|---|
| `ui.jsx` | Spinner · Pill · StatusBadge · ErrBar · EmptyState · StatCard · LogBlock · CopyButton · Toast/useToasts |
| `battle.jsx` | VerbIcon（SVG）· PokeChip 52×64/40×50 · HPRow（progressbar）· Zone · ActionPanel · TurnLog · SeedBar |
| `stats.jsx` | WinrateMatrix · ExportBar · KvTable |
| `tasks.jsx` | TaskCard（进度/ETA/取消确认）· TaskTable · QuotaHint |
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
→ 注：受限环境下 Node 写项目盘可能 EPERM，可输出到用户主目录（如 `C:/Users/<user>/pokelab_shots`）。

## 6. 已知边界

- 卡池为 demo 卡组（M1 卡池补齐后替换预设 A1–A4 的真实卡表，已在 UI 标注）；
- 对局会话驻留后端内存（重启需重开）；后台 M1/M2/M3 页面为"信息卡 + 链接"级别（P1 增补）；
- 受限环境构建已设 `emptyOutDir:false`（沙箱回收站不可用），正常环境可移除。
