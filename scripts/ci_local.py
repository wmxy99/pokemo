#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""本地复刻 CI 的全部 job（一条命令跑完，替代"只能靠 GitHub Actions 才知道"）

为什么需要它
------------
CI 结果只能在 GitHub 上看，而本项目的环境差异（Windows 沙箱 / conda 环境里
预装了 torch、cryptography、PyJWT）很容易造成「本地假绿、CI 真红」。
本脚本把 `.github/workflows/ci.yml` 里 8 个 job 的**每一步**在本地按顺序复刻，
并显式报告 SKIP 及原因（例如没装 torch 时 agent 的 172 项测试无法本地跑）。

用法
----
    python scripts/ci_local.py            # 全部 job
    python scripts/ci_local.py engine gym # 只跑指定 job
    python scripts/ci_local.py --list     # 列出 job

退出码：有任一 job FAIL → 1；全 PASS/SKIP → 0。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


@dataclass
class Step:
    name: str
    cmd: list
    cwd: Path
    env: dict = field(default_factory=dict)
    needs: str = ""          # 需要的模块/命令（缺失则整 job 跳过）


@dataclass
class Job:
    name: str
    steps: list


def _p(*parts) -> Path:
    return REPO.joinpath(*parts)


def _has_module(mod: str) -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


PY = sys.executable


def build_jobs() -> list:
    env_engine = {"PYTHONPATH": str(_p("packages", "engine"))}
    env_gym = {"PYTHONPATH": os.pathsep.join(
        [str(_p("packages", "engine")), str(_p("packages", "gym"))])}
    # ⚠ carddb 的 seed_demo 会**写库**。CI 上每次是全新 checkout 所以无所谓；
    #   本地若写进开发库会污染卡库（历史上就因此让 api 的检索断言失败）。
    #   这里用 POKELAB_DB 指向临时库，跑前删掉，保证与 CI 的「空库起步」等价且不污染。
    import tempfile
    probe_db = Path(tempfile.gettempdir()) / "pokelab_ci_local_probe.db"
    try:
        probe_db.unlink()
    except OSError:
        pass
    env_carddb = {"POKELAB_DB": str(probe_db)}
    # ⚠ api 的卡库类测试需要**真实卡库**（1267 张）。CI 上是全新 checkout → 库不存在，
    #   所以 ci.yml 的 api job 会先离线建库。这里同样复刻该步骤，只是把库落到临时路径
    #   并用 POKELAB_CARD_DB 指过去 —— 语义等价（都是从入库的 cards_challenge.json 重建），
    #   但不覆盖开发库。
    #   ⚠ 历史教训：api job 曾经「本地全绿、CI 必红」，就是因为本地有开发库兜着。
    api_db = Path(tempfile.gettempdir()) / "pokelab_ci_local_api.db"
    try:
        api_db.unlink()
    except OSError:
        pass
    npm = shutil.which("npm") or "npm"
    return [
        Job("engine", [Step("Settlement & mechanics tests", [PY, "-m", "unittest",
            "discover", "-s", "tests", "-v"], _p("packages", "engine"))]),
        Job("gym", [Step("Gym env tests (zero runtime deps)", [PY, "-m", "unittest",
            "discover", "-s", "tests", "-v"], _p("packages", "gym"), env_gym)]),
        Job("carddb", [
            Step("Static compile", [PY, "-m", "py_compile"] +
                 [str(p) for p in (_p("packages", "card-db", "pokelab")).glob("*.py")],
                 REPO),
            Step("Offline demo (self-authored data only, 临时库)",
                 [PY, "scripts/seed_demo.py"], _p("packages", "card-db"),
                 env_carddb, needs="pydantic"),
            Step("卡池解析对账（卡面 ↔ 引擎 tag）",
                 [PY, "packages/card-db/scripts/audit_pool_alignment.py"], REPO),
        ]),
        Job("agent", [Step("Agent tests (need torch)", [PY, "-m", "unittest",
            "discover", "-s", "tests"], _p("packages", "agent"), needs="torch")]),
        Job("api", [
            Step("Prepare card library DB (schema)", [PY, "-c",
                 "import sys; sys.path.insert(0,'.')\n"
                 "from pokelab.db import init_db\n"
                 "print('[db] 建表数:', init_db(r'%s'))" % api_db],
                 _p("packages", "card-db"), needs="pydantic"),
            Step("Prepare card library DB (离线灌入已清洗卡表)",
                 [PY, "scripts/import_challenge_dataset.py", "--db-only",
                  "--db", str(api_db)], _p("packages", "card-db")),
            Step("API tests（指向全新离线卡库）", [PY, "-m", "unittest", "discover", "-s",
                 "tests"], _p("apps", "api"), {"POKELAB_CARD_DB": str(api_db)},
                 needs="fastapi"),
        ]),
        Job("decl-check", [
            Step("api 入口可在无 torch 环境导入", [PY, "-c",
                 "import sys; sys.path.insert(0,'.');\n"
                 "from apps.api.app.main import app\n"
                 "m=('get','post','put','patch','delete')\n"
                 "n=sum(1 for v in app.openapi()['paths'].values() for k in v if k in m)\n"
                 "print(f'OK: api 入口导入成功，路由操作数 {n}')"], REPO),
            Step("agent 轻量模块可导入（torch 系保持惰性）", [PY, "-c",
                 "import agent; from agent import llmclient, effects, privacy, mcts\n"
                 "print('OK: agent 轻量模块导入成功')"], _p("packages", "agent")),
            Step("engine 零依赖可导入", [PY, "-c",
                 "import engine; print('OK: engine 导入成功（零三方依赖）')"],
                 _p("packages", "engine")),
        ]),
        Job("web-build", [
            Step("Build (vite)", [npm, "run", "build"], _p("apps", "web"),
                 needs="web-deps"),
            Step("i18n key parity", ["node", "e2e/check_i18n.cjs"], _p("apps", "web"),
                 needs="node"),
            Step("plays.js 目标语义对账", ["node", "e2e/plays_check.cjs"],
                 _p("apps", "web"), needs="node"),
        ]),
        Job("redline", [
            Step("Data-redline guard (文件名)", [PY, "-c",
                 "import subprocess,sys,re\n"
                 "files=subprocess.run(['git','ls-files'],capture_output=True,text=True,\n"
                 "                     cwd=r'%s').stdout.splitlines()\n"
                 "bad=[f for f in files if re.search(r'(^data/|\\.db$|EN_Card_Data|rulebook|compendium|\\.safetensors$|\\.faiss$|\\.index$)', f)]\n"
                 "print('违规文件:', bad) if bad else print('OK: 文件名守卫通过')\n"
                 "sys.exit(1 if bad else 0)" % REPO], REPO),
            Step("Data-redline guard (内容：本地路径/凭据)", [PY, "-c",
                 "import subprocess,sys,re\n"
                 "pat=r'[A-Za-z]:[\\\\/](Users|pokemo|anconda|anaconda)[\\\\/]'\n"
                 "cmd=['git','grep','--cached','-nE',pat,'--','*.md','*.py','*.js','*.jsx',\n"
                 "     '*.json','*.yml','*.txt','*.cjs',':(exclude)packages/agent/tests/test_privacy.py']\n"
                 "r=subprocess.run(cmd,capture_output=True,text=True,cwd=r'%s')\n"
                 "print('违规行:', r.stdout[:400]) if r.returncode==0 else print('OK: 本机路径守卫通过')\n"
                 "sys.exit(0 if r.returncode!=0 else 1)" % REPO], REPO),
            Step("文档计数守卫（计数类文档必须实证）", [PY,
                 "scripts/check_doc_counts.py"], REPO),
        ]),
    ]


def run_step(st: Step, timeout: int = 1800) -> tuple:
    env = {**os.environ, **st.env}
    try:
        r = subprocess.run(st.cmd, cwd=str(st.cwd), env=env, capture_output=True,
                           text=True, timeout=timeout)
    except FileNotFoundError as e:
        return False, f"命令不存在：{e}"
    except subprocess.TimeoutExpired:
        return False, f"超时（>{timeout}s）"
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode == 0, out


def main() -> int:
    ap = argparse.ArgumentParser(description="本地复刻 CI 全部 job")
    ap.add_argument("jobs", nargs="*", help="只跑指定 job（默认全部）")
    ap.add_argument("--list", action="store_true", help="列出 job")
    ap.add_argument("--tail", type=int, default=4, help="失败时打印的末尾行数")
    args = ap.parse_args()

    jobs = build_jobs()
    if args.list:
        for j in jobs:
            print(j.name, "→", " | ".join(s.name for s in j.steps))
        return 0
    if args.jobs:
        want = set(args.jobs)
        jobs = [j for j in jobs if j.name in want]
        unknown = want - {j.name for j in jobs}
        if unknown:
            print(f"未知 job：{sorted(unknown)}（可用：{[j.name for j in build_jobs()]}）")
            return 2

    print(f"本地 CI 复刻 · {len(jobs)} 个 job · python={PY}\n")
    results = []
    t0 = time.time()
    for job in jobs:
        # 依赖检查（缺失则整 job SKIP，并说明原因 —— 绝不当成 PASS 混过去）
        missing = [s.needs for s in job.steps if s.needs and not _needs_ok(s.needs)]
        if missing:
            print(f"[SKIP] {job.name} —— 缺少 {', '.join(sorted(set(missing)))}\n")
            results.append((job.name, "SKIP", f"缺少 {', '.join(sorted(set(missing)))}",
                            ""))
            continue
        print(f"[RUN ] {job.name}")
        ok_all, note = True, ""
        for st in job.steps:
            ok, out = run_step(st)
            mark = "ok " if ok else "FAIL"
            print(f"        {mark}  {st.name}")
            if not ok:
                ok_all, note = False, out
                break
        results.append((job.name, "PASS" if ok_all else "FAIL", note, ""))
        print()

    print("=" * 62)
    for name, status, note, _ in results:
        print(f"  {status:<5} {name}" + (f"  —— {note}" if status == "SKIP" else ""))
    failed = [r for r in results if r[1] == "FAIL"]
    print(f"\n耗时 {time.time() - t0:.0f}s · PASS "
          f"{sum(1 for r in results if r[1] == 'PASS')} · SKIP "
          f"{sum(1 for r in results if r[1] == 'SKIP')} · FAIL {len(failed)}")
    for name, _, out, _ in failed:
        print(f"\n----- {name} 末尾输出 -----")
        print("\n".join((out or "").splitlines()[-args.tail:]))
    return 1 if failed else 0


def _needs_ok(need: str) -> bool:
    if need == "node":
        return shutil.which("node") is not None
    if need == "web-deps":
        return (REPO / "apps" / "web" / "node_modules").is_dir()
    return _has_module(need)


if __name__ == "__main__":
    sys.exit(main())
