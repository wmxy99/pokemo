# -*- coding: utf-8 -*-
"""读取 GitHub Actions 的真实运行结果（私有仓库需要一个 token）

为什么需要这个脚本：
  `scripts/ci_local.py` 只能在本地**复刻** CI 的每一步，无法证明 GitHub 上真的跑过。
  而本仓库是**私有**的 —— 匿名访问 `api.github.com` 一律 404，GitHub 连接器也读不到。
  所以远程结果要么装 `gh`，要么用这个脚本 + 一个 Personal Access Token。

用法：

    # ① 给 token：推荐写进文件（不进命令行历史、不进对话记录）
    mkdir -p ~/.workbuddy/secrets
    printf '%s' '<你的 PAT>' > ~/.workbuddy/secrets/gh_pat.txt   # 该路径会被自动探测

    # 也可以显式指定位置或走环境变量
    python scripts/ci_remote.py --token-file D:/somewhere/pat.txt
    export GITHUB_TOKEN=<你的 PAT>
    python scripts/ci_remote.py --token <你的 PAT>      # 会留在命令行历史，最不推荐

    # ② 看最近一次运行的全部 job 结论
    python scripts/ci_remote.py

    # ③ 指定某次运行 / 最近的 N 次
    python scripts/ci_remote.py --run 123456789
    python scripts/ci_remote.py --limit 5

    # ④ 运行中则等待结束（默认轮询 15s，最多 30 分钟）
    python scripts/ci_remote.py --watch

退出码：0 = 全部成功 · 1 = 有失败 · 2 = 读不到（无 token / 无运行 / 网络失败）

Token 需要的最小权限（两种任选）：
  · fine-grained PAT：Repository access 选 Only select repositories → wmxy99/pokemo；
    Permissions → Actions: Read-only（Metadata: Read-only 会自动带上）
  · classic PAT：勾 `repo`
脚本**从不回显 token 本身**，只打印来源（命令行 / 文件路径 / 环境变量名）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.github.com"
OK = "success"
# 跳过/中性**不是**失败 —— 条件 job（`if:`）在 GitHub 上就是 skipped，
# 且整次运行仍可能被判 success（踩过：把 skipped 当 FAIL，误报 cli/cli 那次运行失败）
SKIP = {"skipped", "neutral"}
BAD = {"failure", "cancelled", "timed_out", "action_required", "startup_failure"}
RUNNING = {"in_progress", "queued", "requested", "waiting", "pending"}

# 未显式给 token 时按序探测这些位置（**都不在仓库内**，避免误提交）。
# 推荐把 PAT 写进第一个：mkdir -p ~/.workbuddy/secrets && 写入 gh_pat.txt
TOKEN_FILES = (
    Path.home() / ".workbuddy" / "secrets" / "gh_pat.txt",
    Path.home() / ".workbuddy" / "gh_pat.txt",
    Path.home() / ".github_token",
    Path(tempfile.gettempdir()) / "github_token.txt",
)


def repo_from_git() -> str | None:
    """从 origin 的 URL 推断 owner/repo（支持 https 与 ssh 两种写法）"""
    try:
        url = subprocess.run(["git", "remote", "get-url", "origin"],
                             capture_output=True, text=True, timeout=15).stdout.strip()
    except Exception:
        return None
    if not url:
        return None
    m = re.search(r"github\.com[:/]+([^/]+)/([^/\s]+?)(?:\.git)?$", url)
    return f"{m.group(1)}/{m.group(2)}" if m else None


def call(path: str, token: str) -> tuple[int, object]:
    """GET api.github.com/<path>；返回 (状态码, 解析后的 JSON 或原始文本)

    token 为空时走匿名请求 —— 公开仓库可直接读（有速率限制），
    私有仓库会返回 404（GitHub 对无权访问的资源一律报"不存在"，不区分「仓库不存在」）。
    """
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "pokelab-ci-remote",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(API + path, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:200]
        return e.code, body
    except Exception as e:                       # 网络层失败
        return 0, f"{type(e).__name__}: {e}"


def load_runs(repo: str, token: str, limit: int) -> tuple[list, str | None]:
    code, data = call(f"/repos/{repo}/actions/runs?per_page={max(1, min(limit, 20))}", token)
    if code == 401:
        return [], "token 无效或已过期（401 Bad credentials）"
    if code == 403:
        return [], "token 权限不足（403）：私有仓库读 Actions 需 classic PAT 的 repo 范围，" \
                   "或 fine-grained PAT 的 Actions: Read"
    if code == 404:
        return [], f"读不到 {repo}（404）：仓库不存在、或 token 无权访问该私有仓库"
    if code != 200:
        return [], f"请求失败（HTTP {code}）：{str(data)[:160]}"
    return data.get("workflow_runs") or [], None


def jobs_of(repo: str, token: str, run_id: int) -> list:
    code, data = call(f"/repos/{repo}/actions/runs/{run_id}/jobs?per_page=100", token)
    if code != 200:
        return []
    return data.get("jobs") or []


def read_token_file(path: Path) -> str | None:
    """读 token 文件：取第一行「非空且非注释」，去掉首尾引号。不存在/为空 → None"""
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError:
        return None
    for line in raw.splitlines():
        line = line.strip().strip("'\"")
        if line and not line.startswith("#"):
            return line
    return None


def resolve_token(explicit: str | None, token_file: str | None) -> tuple[str, str]:
    """解析 token 来源，返回 (token, 来源说明)。**任何分支都不回显 token 本身**

    优先级：命令行 > 指定文件 > 环境变量 > 常见位置（TOKEN_FILES）
    """
    if explicit:
        return explicit.strip(), "命令行 --token"
    if token_file:
        p = Path(token_file).expanduser()
        t = read_token_file(p)
        return (t, f"文件 {p}") if t else ("", f"指定文件 {p}（不存在或为空）")
    for var in ("GITHUB_TOKEN", "GH_TOKEN"):
        if os.environ.get(var):
            return os.environ[var].strip(), f"环境变量 {var}"
    for p in TOKEN_FILES:
        t = read_token_file(p.expanduser())
        if t:
            return t, f"文件 {p}"
    return "", "未提供"


def fmt(concl: str | None) -> str:
    """把 GitHub 的 conclusion 归到四类；**skipped 归 SKIP，不算失败**"""
    if not concl:
        return "RUNNING"
    if concl == OK:
        return "PASS"
    if concl in SKIP:
        return "SKIP"
    if concl in BAD:
        return "FAIL"
    return str(concl).upper()


ICON = {"PASS": "✅", "SKIP": "⚠️ ", "FAIL": "❌", "RUNNING": "⏳"}


def show(repo: str, token: str, run: dict, verbose: bool) -> tuple[int, dict]:
    """打印一次运行的 job 明细；返回 (是否有失败, 各类计数)"""
    counts = {"PASS": 0, "SKIP": 0, "FAIL": 0, "RUNNING": 0}
    print(f"\n=== run #{run.get('run_number')} · {run.get('name') or '-'} · "
          f"{run.get('head_branch')} @ {str(run.get('head_sha'))[:8]} ===")
    print(f"    结论 {fmt(run.get('conclusion'))} · 事件 {run.get('event')} · "
          f"开始 {run.get('run_started_at') or run.get('created_at')}")
    print(f"    {run.get('html_url')}")

    js = jobs_of(repo, token, run["id"])
    if not js:
        # 读不到明细（权限粒度不足）→ 只能信运行级结论，如实说明
        k = fmt(run.get("conclusion"))
        counts[k] += 1
        print(f"    （读不到 job 明细，仅按运行级结论判定 · 权限可能只有 Actions: Read）")
        return (0 if k != "FAIL" else 1), counts

    worst = 0
    for j in sorted(js, key=lambda x: x.get("name") or ""):
        k = fmt(j.get("conclusion"))
        counts[k] += 1
        if k == "FAIL":
            worst = 1
        tail = ""
        if k == "SKIP":
            tail = "   （条件未满足，非失败）"
        elif k == "FAIL":
            tail = f"   ← {j.get('html_url')}"
        print(f"    {ICON[k]} {j.get('name'):<14} {k}{tail}")
        if k == "FAIL":
            for st in j.get("steps") or []:
                if fmt(st.get("conclusion")) == "FAIL":
                    print(f"         ↳ 失败步骤：{st.get('name')}")
        elif verbose:
            names = [st.get("name") for st in (j.get("steps") or []) if st.get("name")]
            print(f"         步骤：{' | '.join(names)}")
    return worst, counts


def main() -> int:
    ap = argparse.ArgumentParser(description="读取 GitHub Actions 的真实运行结果")
    ap.add_argument("--repo", help="owner/repo（默认从 git origin 推断）")
    ap.add_argument("--token", help="PAT（注意：会留在命令行历史里，优先用 --token-file）")
    ap.add_argument("--token-file",
                    help="从文件读 PAT（取第一行非空非注释）；不给时会自动探测 "
                         + " / ".join(str(p) for p in TOKEN_FILES))
    ap.add_argument("--run", type=int, help="指定 run id（默认最近一次）")
    ap.add_argument("--limit", type=int, default=1, help="显示最近 N 次运行（默认 1）")
    ap.add_argument("--watch", action="store_true", help="运行中则等待结束")
    ap.add_argument("-v", "--verbose", action="store_true", help="连成功 job 的步骤也列出")
    a = ap.parse_args()

    repo = a.repo or repo_from_git()
    if not repo:
        print("✗ 无法确定仓库：请在仓库目录内运行，或显式传 --repo owner/repo")
        return 2

    token, src = resolve_token(a.token, a.token_file)
    print(f"仓库 {repo} · token 来源：{src}")

    HINT = ("\n  本仓库是**私有**仓库，匿名读 api.github.com 一律 404。请任选其一：\n"
            "    ① 写入文件（推荐，不进命令行历史）：\n"
            "       mkdir -p ~/.workbuddy/secrets && "
            "printf '%s' '<你的 PAT>' > ~/.workbuddy/secrets/gh_pat.txt\n"
            "       然后直接 python scripts/ci_remote.py（会自动探测该路径）\n"
            "    ② export GITHUB_TOKEN=<你的 PAT>   # 需要 repo（classic）或 Actions: Read\n"
            "    ③ python scripts/ci_remote.py --token <你的 PAT>\n"
            "  或安装 gh CLI 后：gh run list -R " + repo)

    if a.run:
        code, data = call(f"/repos/{repo}/actions/runs/{a.run}", token)
        if code != 200:
            print(f"✗ 读 run {a.run} 失败（HTTP {code}）：{str(data)[:160]}"
                  + (HINT if code in (401, 403, 404) else ""))
            return 2
        runs = [data]
    else:
        runs, err = load_runs(repo, token, a.limit)
        if err:
            print(f"✗ {err}"
                  + (HINT if ("404" in err or "403" in err or "401" in err) else ""))
            return 2
        if not runs:
            print("✗ 该仓库没有任何 Actions 运行记录 —— 确认工作流文件已推送"
                  "（.github/workflows/ci.yml）且 Actions 已启用")
            return 2

    if a.watch:
        deadline = time.time() + 30 * 60
        while runs and runs[0].get("status") != "completed" and time.time() < deadline:
            print(f"    …运行中（{runs[0].get('status')}），15s 后重查")
            time.sleep(15)
            runs, err = load_runs(repo, token, a.limit)
            if err:
                print(f"✗ {err}")
                return 2
        if not runs or runs[0].get("status") != "completed":
            print("✗ 等待超时（30 分钟）—— 请稍后重跑本脚本")
            return 2

    worst = 0
    total = {"PASS": 0, "SKIP": 0, "FAIL": 0, "RUNNING": 0}
    for r in runs:
        w, c = show(repo, token, r, a.verbose)
        worst = max(worst, w)
        for k in total:
            total[k] += c[k]

    tail = f"（跳过 {total['SKIP']}）" if total["SKIP"] else ""
    if worst == 0:
        print(f"\n✅ 全部 job 成功{tail}：PASS {total['PASS']} · SKIP {total['SKIP']}"
              f" · FAIL {total['FAIL']}")
    else:
        print(f"\n❌ 存在失败 job：PASS {total['PASS']} · SKIP {total['SKIP']}"
              f" · FAIL {total['FAIL']} —— 详情见上方链接")
    return worst


if __name__ == "__main__":
    sys.exit(main())
