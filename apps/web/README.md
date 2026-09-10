# PokéLab AI · Web 客户端（apps/web）

React + Vite 前端，对接 `apps/api` FastAPI 服务。页面结构沿用 UI/UX 规范（灰阶 + 品牌红、
全屏不居中、等宽承载 seed/日志），当前实现 **对战 / 批量对战 / 统计报告** 三页 + 登录/注册。

> 原占位说明（P3 规划）：人机对战可视化（F5）/ 批量任务（F6）/ 数据看板（F7）；
> **页脚必须含三处版权声明之 C3b**（PRD C3）——已实现于全局 `footer.site`，不得移除。

## 1. 开发

```bash
# 终端 A：后端（127.0.0.1:8000）
cd <repo>/pokelab-ai && conda activate pokemo
python -m uvicorn apps.api.app.main:app --reload --port 8000

# 终端 B：前端（5173，/api → 8000 由 Vite proxy 转发）
cd <repo>/pokelab-ai/apps/web
npm install --registry=https://registry.npmmirror.com
npm run dev            # → http://127.0.0.1:5173
```

## 2. 生产/同源部署

```bash
npm run build          # 产出 apps/web/dist
# 后端启动时自动挂载 dist → 访问 http://127.0.0.1:8000/app/
python -m uvicorn apps.api.app.main:app --port 8000
```
- 同源模式下前端自动用**相对路径**调 API（`/battles` 等）；dev 模式用 `/api` 前缀走 proxy；
- 也可指定 `VITE_API_BASE=https://api.example.com` 构建以对接独立域名。

## 3. 页面与 API 对应

| 页面 | 关键调用 | 说明 |
|---|---|---|
| 对战 | `POST /battles` · `POST /battles/{id}/actions` · `GET /battles/{id}` | 游客可试玩；动作按钮 = 引擎 `legal_actions` 1:1；AI 强度 0=规则基线，>0=MCTS；显示 seed（可重放） |
| 批量对战 | `POST /batch/jobs` · `GET /batch/jobs` · `GET /batch/jobs/{id}` · `POST .../cancel` · `GET .../report?format=md\|csv\|json` | 进度轮询 1.2s；取消保留已完成（K4）；报告含 `Unofficial community data` |
| 统计报告 | `GET /batch/jobs` + 各任务 `result` / `report?format=csv` | 汇总累计对局与平均胜率；CSV 一键下载；胜率配色遵循中文惯例（红=我方占优） |
| 登录 | `POST /auth/register` · `/auth/login` · `GET /auth/me` | 真 JWT（localStorage 保存）；训练管理接口需登录 |

## 4. 已知边界

- 对局会话在服务内存：后端重启后需重开对局；
- 卡组为演示字典（`lightning/fire/...`），卡表文本解析属 P3；
- 统计页暂按任务聚合展示（跨卡组"胜率矩阵"待 P1 卡组池批量提交后补）；
- 受限环境下 `vite build` 已设 `emptyOutDir:false`（沙箱回收站不可用）；正常环境可移除该选项获得清理效果。
