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
| 对战 | UI §4.1.1 / PRD F5 | 三列 300/1fr/300；`legal_actions` 1:1 动作面板（SVG 图标，禁 emoji）；HP 血条绿满/红低；回合日志 `role=log`；seed 复制；K3 >2s 兜底提示；`<900px` 动作面板置底 sticky |
| 批量对战 | UI §4.1.2 / F6 | 配额条（≤500/并发≤2/日≤10）；乐观 ETA；二次确认取消（保留已完成 K4）；历史任务报告导出 |
| 统计报告 | UI §4.1.3 / F7 | 3 KPI + 胜率矩阵（对角线 muted、hover 95%CI/n）+ 导出条 CSV/MD/JSON/复制 + MD 预览 + `Unofficial community data` |
| 卡组管理 | UI §4.1.4 / F3 | mono textarea 导入 → 校验（60/同名4/含基础，逐条错误）+ 三色 pill + 我的卡组表 + 预设 A1–A4（Worlds 依据）+ 起手 1,000 次模拟 |
| 后台仪表盘 M6 | UI §4.2.1 | 三 KPI + **K1–K4 看门狗表**（诚实标注 K1 未达成）+ 近 7 日 sparkline + F16 导出（beta 置灰） |
| 后台 RL 训练 | UI §4.2.6 / M2 | 真实 `/train/jobs` 监控：提交/状态/曲线（stdout 解析）/日志 tail/取消 + 冲 70% 手册折叠区 |
| 后台用户管理 M5 | UI §4.2.2 / F13 | 用户/角色/日配额表格 + 角色切换（admin）；越权隔离提示文案 |

## 3. 组件库（src/components）

| 文件 | 组件 |
|---|---|
| `ui.jsx` | Spinner · Pill · StatusBadge · ErrBar · EmptyState · StatCard · LogBlock · CopyButton · Toast/useToasts |
| `battle.jsx` | VerbIcon（SVG）· PokeChip 52×64/40×50 · HPRow（progressbar）· Zone · ActionPanel · TurnLog · SeedBar |
| `stats.jsx` | WinrateMatrix · ExportBar · KvTable |
| `tasks.jsx` | TaskCard（进度/ETA/取消确认）· TaskTable · QuotaHint |
| `verbs.js` / `i18n.js` | 动词表（与引擎同源，F8.2）· i18n 键位（F14，中文为主 + EN 键） |

## 4. 合规与无障碍

- 页脚**恒定**复刻 C3 三段声明 + C1 非商用 + C2 无卡图 + C5 差异透明（不得在 view 层隐藏）；
- a11y：chip `aria-label`、血条 `role=progressbar`、日志 `role=log aria-live`、表格 `th scope`、
  焦点环 3px、触摸目标 ≥44px、`prefers-reduced-motion` 关闭动效；
- 数字一律等宽（HP/牌库/百分比/seed/局数）；红=我方占优（中文数据惯例）。

## 5. 已知边界

- 卡池为 demo 卡组（M1 卡池补齐后替换预设 A1–A4 的真实卡表，已在 UI 标注）；
- 对局会话驻留后端内存（重启需重开）；后台 M1/M2/M3 页面为"信息卡 + 链接"级别（P1 增补）；
- 受限环境构建已设 `emptyOutDir:false`（沙箱回收站不可用），正常环境可移除。
