# apps/web/e2e · 端到端脚本

真实浏览器驱动的 UI 验证脚本。技术栈是 **puppeteer-core + Chrome DevTools Protocol**
（**不是 Playwright** —— 仓库未引入 playwright，请勿按 Playwright API 编写）。

## 依赖与前置条件

- 依赖：`puppeteer-core`（已列入 `apps/web/package.json` 的 `devDependencies`）。
  `npm ci` 后即可用，无需额外安装。
- 本机浏览器：脚本内置候选路径并取**第一个存在**的，其次回退到 Edge：
  `C:\Program Files\Google\Chrome\Application\chrome.exe` →
  `C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`
  （`puppeteer-core` 不下载 Chromium，必须复用本机已装浏览器）
- 被测服务：**后端需已启动**，默认 `http://127.0.0.1:8000`（该端口同时托管前端构建产物到 `/app`）；
  走 Vite dev 时把地址作为参数传入。
- 部分脚本会落盘截图，默认输出到 `apps/web/dist/`（可用参数改）。

## 用法

```bash
cd apps/web && npm ci
npm run build                                   # 需要 dist/ 才能由后端 /app 托管

# 后端另开一个终端
python -m uvicorn apps.api.app.main:app --host 127.0.0.1 --port 8000

# 跑单个脚本（在 apps/web 目录下）
node e2e/webui_e2e.cjs                          # 默认 base=http://127.0.0.1:8000
node e2e/check_render.cjs http://127.0.0.1:8000 out.png
node e2e/shoot_pages.cjs http://127.0.0.1:8000 dist/shots
```

个别脚本支持环境变量覆盖，例如 `POKELAB_DECK_TEXT` 指定卡表文本路径
（默认 `e2e/fixtures/deck_text.txt`）。

## 脚本清单

| 脚本 | 覆盖内容 | 需真实浏览器 |
|---|---|:---:|
| `check_i18n.cjs` | zh/en 键一致性守卫（**CI 会跑，无需浏览器**） | 否 |
| `plays_check.cjs` | `plays.js` 目标语义对账（引擎 target 语义 ↔ UI 高亮/文案）+ 训练家/特性 tag 文案覆盖（**CI 会跑，无需浏览器**） | 否 |
| `battle_target_check.cjs` | 目标语义端到端：点「老大的指令」→ 高亮的卡必须就是实际换上场的卡 | 是 |
| `replay_frames_check.cjs` | 逐帧回放：**回放条可见期间**棋盘对手区出现 ≥2 个不同状态（旧实现此时恒为 1） | 是 |
| `check_render.cjs` | `/app/` 渲染自检：控制台/网络/运行时错误收集 + 截图 | 是 |
| `webui_e2e.cjs` | F3→F5：注册登录 → 卡组校验 → 建局 → 合法动作按钮 | 是 |
| `library_e2e.cjs` | F2 卡库：统计条 / 搜索（EN·中文·归一）/ 筛选 / 翻页 / 详情 | 是 |
| `zh_i18n_e2e.cjs` | 中文展示：棋盘 / 手牌 / 动作按钮 / 报告与矩阵 | 是 |
| `zh_input_e2e.cjs` | 中文卡名：导入 / 解析明细 / 导出 | 是 |
| `cmd_input_e2e.cjs` | 中文指令栏 + 候选 chips 执行 | 是 |
| `seq_e2e.cjs` | 组合指令（多步批量提交）预览与逐步日志 | 是 |
| `sse_matrix_e2e.cjs` | 矩阵热力图 + SSE 实时进度（心跳 / 续传状态灯） | 是 |
| `sse_focus_e2e.cjs` | SSE 状态灯（断线重连 / 轮询兜底）+ 预设逐卡映射 | 是 |
| `compliance_e2e.cjs` | 合规 6.1 / 6.2：页脚声明 → 卡库提示 | 是 |
| `compliance_page_e2e.cjs` | F9 合规详情页全量要点 | 是 |
| `model_config_e2e.cjs` | 后台云端模型配置 + 对战解说 | 是 |
| `shoot_pages.cjs` | 逐页截图 + 错误采集（前台 4 页 / 后台 3 页） | 是 |
| `save_shots.py` | 截图落盘辅助（Python） | — |

## 与 CI 的关系

CI（`.github/workflows/ci.yml` 的 `web-build` job）执行 **`check_i18n.cjs` 与
`plays_check.cjs`** 两个纯 Node 文本/逻辑检查（不装浏览器、不依赖被测服务）。
其余脚本是本地/手动验证工具：CI 环境没有本机 Chrome，且这些脚本需要前后端同时在线，
因此不纳入自动流水线。改动 UI 时请在本地跑一遍受影响的脚本，并把结论写进 PR 描述。

## 写脚本时的两个坑（都踩过）

1. **`(async () => {})()` 前必须留分号**（写成 `;(async () => {}()`）。上一行是箭头函数
   表达式时，缺分号会让 ASI 把 IIFE 解析成「对上一表达式求值结果的调用」，
   脚本**退出码 0、零输出**，极难排查。
2. **受控 `<select>` 驱动不了 React 状态**：`page.select()` 或「原生 value setter +
   `dispatchEvent`」都只会改 DOM，React state 不变（表现为建局请求里仍是默认卡组）。
   可靠做法是取元素上的 `__reactProps$` 句柄直接调用 `onChange`，见
   `battle_target_check.cjs` 的 `setReactSelect`。
   另外**顺序很重要**：应用默认停在对战页，要先选卡组再点建局。
