# Contributing to PokéLab AI

感谢贡献！下面是**可跑通**的本地流程（命令均在仓库根执行，除标注 `cd` 的）。

## 0. 本地起环境（先做这一步）

```bash
# Python：引擎与 Agent 无三方依赖即可跑测试；后端/Agent 需按各自 requirements 装
pip install -r apps/api/requirements.txt
pip install -r packages/agent/requirements.txt     # 含 torch，体积较大

# 前端（Node ≥ 18）
cd apps/web && npm ci && npm run build && cd ..
```

**测试命令（务必 `cd` 进包目录再 discover —— 从根目录跑会 import 失败）**

```bash
cd packages/engine && python -m unittest discover -s tests      # 221 项
cd packages/agent  && python -m unittest discover -s tests      # 172 项（需 torch）
cd packages/gym    && PYTHONPATH=../engine python -m unittest discover -s tests  # 12 项（零依赖；4 项需 torch 自动跳过）
cd apps/api        && python -m unittest discover -s tests      # 131 项
```

**本地复刻 CI（一条命令跑完 8 个 job）**

```bash
python scripts/ci_local.py              # 全部；缺依赖的 job 会 SKIP 并说明原因
python scripts/ci_local.py engine api   # 只跑指定 job（--list 列出全部）
```

CI 结果只能在 GitHub 上看，且本机 conda 环境预装了 torch / cryptography / PyJWT
（漏声明依赖在本地完全看不出来）→ **改完代码先本地复刻一遍再推**。
注意 `scripts/ci_local.py` 的 carddb 步骤会把 `POKELAB_DB` 指向临时库，
不会污染开发库（历史上 `seed_demo.py` 写进开发库曾让 api 的卡库检索断言失败）。

**读远程 CI 的真实结果**（本仓库是私有仓库：匿名读 `api.github.com` 一律 404，
GitHub 连接器同样读不到 → 需要一个 PAT）

```bash
# ① 给 token：写进文件最安全（不被自动探测以外的任何地方读取，脚本也不回显它）
mkdir -p ~/.workbuddy/secrets
printf '%s' '<PAT>' > ~/.workbuddy/secrets/gh_pat.txt     # 该路径会被自动探测
# 也可用：export GITHUB_TOKEN=<PAT> · --token-file <路径> · --token <PAT>（最不推荐）

# ② 读结果
python scripts/ci_remote.py             # 最近一次运行的全部 job 结论
python scripts/ci_remote.py --watch     # 运行中则等待结束（15s 轮询，上限 30 分钟）
python scripts/ci_remote.py --run <id>  # 指定某次运行
```

PAT 最小权限：**fine-grained**（Settings → Developer settings → Personal access tokens →
Fine-grained tokens）→ Repository access 选 `Only select repositories` + `wmxy99/pokemo`；
Permissions → **Actions: Read-only**（`Metadata: Read-only` 会自动带上）。
classic PAT 则勾 **`repo`**（权限更大，能不用就不用）。**用完即撤销**（Revoke）。

退出码：`0` 全部成功 · `1` 有失败 job · `2` 读不到（无 token / 无运行 / 网络失败）。
`skipped` 计为 SKIP 而非失败（条件 job 的常态）。装了 `gh` 也可 `gh run list -R wmxy99/pokemo`。
仓库根 `.gitignore` 已兜底忽略 `gh_pat.txt` / `.gh_token` / `.github_token` / `*_pat.txt`。


**本地启动**（前后端一体）

```bash
# 后端（同时托管前端 dist 到 /app）
python -m uvicorn apps.api.app.main:app --host 127.0.0.1 --port 8000
# 前端开发模式（Vite，代理 /api → 8000）
cd apps/web && npm run dev
```

> 运行数据落在 `~/.pokelab/`（可用 `POKELAB_DATA_DIR` 覆盖），**不在仓库内**。
> 详见 `docs/architecture.md`「运行时数据与配置」。

## 1. 数据管道 · packages/card-db（入门）
- 补卡效果结构化样例（`packages/card-db/pokelab/effects.py` 校验器 + 自述转写）
- 规则知识库 chunker / 检索改进
- **红线**：不得提交任何 Kaggle 数据、官方卡图或规则书原文（见根 README「数据获取指引」）

## 2. 规则引擎 · packages/engine（进阶）
- 新增 `PokemonDef`（引擎原生卡定义，程序化效果）
- 修结算 bug → **必须先补一条 fixture**（`packages/engine/tests/test_settlement.py`）再改代码
- 规则差异修正 → 同步更新 `packages/engine/README.md`「规则边界」
- 引擎行为变更必须保持确定性（同 seed → 同事件序列）

## 3. 卡牌理解（K2）· packages/agent + apps/api（进阶）
- 解析提示词与约定：`packages/agent/prompts/parse_effects_system.md`（**枚举/取值域由代码与引擎注入**，勿在模板里手抄）
- 黄金集真值：`packages/agent/data/k2_golden.json`（改真值须在 `meta` 记录依据）
- 评测：`apps/api/scripts/k2_local_eval.py`（卡池可用率，本地）· `apps/api/scripts/k2_cloud_eval.py`（云端严格准确率，**带合规闸门，需 `--yes`**）
- **改提示词后必须云端复测**并逐条归因（历史上有过「修好 A 弄坏 B」的波动）

## 4. 平台 · apps/api + apps/web（专业）
- 后端：`apps/api/app/`（FastAPI，8 个 router）；改接口 → 补 `apps/api/tests/test_api.py` 用例
- 前端：`apps/web/src/`（React + Vite）；界面文案改动 → **同步 `src/i18n.js` 的 zh/en 两份键**
- 端到端脚本：`apps/web/e2e/*.cjs`（**puppeteer-core + CDP** 驱动本机 Chrome，非 Playwright；
  需后端与前端已启动，依赖见 `apps/web/package.json` devDependencies，用法见 `apps/web/e2e/README.md`）

## 流程
1. Fork → 分支 `feat/xxx`
2. 提交信息规范：`feat|fix|docs|refactor|test: 描述`
3. 本地全绿：上面第 0 节三条测试命令 + `cd apps/web && npm run build`
4. 开 PR；CI（engine 单测 · agent 静态编译 · card-db 离线 demo · **api 单测** · **web 构建** · 红线守卫）全过后合并

## 行为准则
公开、尊重、无商业利用宝可梦内容（内容包 PolyForm NC）。
