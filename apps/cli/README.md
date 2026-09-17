# apps/cli · 命令行入口

无第三方依赖（复用 `packages/engine`，把引擎注入模块路径），用于**不启动 Web 平台**时
跑单局复盘与批量统计。**在仓库根目录执行**：

```bash
python apps/cli/run_demo.py single          # 单局：逐回合中文事件日志 + 结果
python apps/cli/run_demo.py batch           # 批量：默认 200 局，统计先后手胜率 / 平均回合
python apps/cli/run_demo.py batch 50        # 指定局数
python apps/cli/run_demo.py dump <seed>     # 导出某一 seed 的完整事件 JSONL（复现用）
```

（`run_demo.py` 顶部 docstring 为准；无其它旗标参数。）

## 与 Web 平台的分工

| | CLI | Web 平台（`apps/api` + `apps/web`） |
|---|---|---|
| 定位 | 快速验证 / 脚本化 / CI 友好 | 交互式对战、批量任务与 SSE 进度、统计矩阵、卡组与卡库管理 |
| 依赖 | 仅引擎（标准库） | FastAPI + React（见根 README 快速开始） |
| 数据落盘 | 无（打印到 stdout / JSONL） | `~/.pokelab/api.db` |

引擎的**确定性**约定（同 seed → 同事件序列）在两者一致；`single` + `dump` 组合适合做
回归对比与 bug 复现（与 `packages/engine/tests` 的 fixture 互补）。
