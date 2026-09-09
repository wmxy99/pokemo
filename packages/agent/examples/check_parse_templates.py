# -*- coding: utf-8 -*-
"""PokéLab Agent · 解析提示词模板 dry-run 结构回归（K2 工程侧）

不调用 LLM；对模板 v1.1 做结构自检，作为「解析错误率」工程基线的代理：
  每样例错误判定：
    E1 user 渲染后残留未替换变量（@@xxx@@）
    E2 system/user 模板含 markdown 代码块围栏（诱导模型输出 ```）
    E3 system 模板缺少关键 Schema 字段名（结构漂移）
    E4 user 渲染未包含卡名与效果文本（占位缺失）
报告写 artifacts/parse_check.md；结构错误率 = 出错样例数 / 样例数。
真正的 LLM 解析错误率（K2 ≥85%）需接入模型后以黄金集执行。
"""
from __future__ import annotations
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]     # 仓库根
for pkg in ("engine", "agent"):
    sys.path.insert(0, str(_ROOT / "packages" / pkg))
from agent import prompts  # noqa: E402

SAMPLES = [
    {"card_name": "Dragapult ex", "series": "TWM", "number": "130",
     "supertype": "Pokemon", "hp": 330,
     "rules_text": "招式 Phantom Dive：对对方战斗宝可梦造成 200 点伤害，"
                   "之后在对方后备宝可梦中以任意方式放置 6 个伤害指示物。"},
    {"card_name": "Dusknoir", "series": "TWM", "number": "141",
     "supertype": "Pokemon", "hp": 150,
     "rules_text": "特性 Cursed Blast：从手牌丢弃此卡，可在对方任意 1 只"
                   "宝可梦上放置 13 个伤害指示物。"},
    {"card_name": "Buddy-Buddy Poffin", "series": "TEF", "number": "144",
     "supertype": "Item", "hp": "",
     "rules_text": "从牌库检索最多 2 张基础宝可梦放入后备，然后洗牌。"},
]

SCHEMA_KEYS = ('"kind"', '"name"', '"trigger"', '"conditions"', '"actions"',
               '"type"', '"amount"', '"target"', '"source_text"',
               '"citations"')


def check() -> list:
    """返回每样例 {name, errs} 列表与全局问题"""
    sys_t = prompts.load("parse_effects_system")
    usr_t = prompts.load("parse_effects_user")
    defaults = {"attacks_meta": "", "retrieved_rules": ""}
    global_issues = []
    missing = [k for k in SCHEMA_KEYS if k not in sys_t]
    if missing:
        global_issues.append(f"E3 缺 Schema 字段: {','.join(missing)}")
    fence = "```" in sys_t or "```" in usr_t
    if fence:
        global_issues.append("E2 模板含代码块围栏")
    rows = []
    for s in SAMPLES:
        errs = []
        kw = {**defaults, **s}
        rendered = prompts.render("parse_effects_user", **kw)
        if re.search(r"@@[a-zA-Z_]+@@", rendered):
            errs.append("E1 占位未替换")
        if s["card_name"] and s["card_name"] not in rendered:
            errs.append("E4a 缺卡名")
        if s["rules_text"] and s["rules_text"] not in rendered:
            errs.append("E4b 缺效果文本")
        if fence:
            errs.append("E2 围栏")
        if missing:
            errs.append("E3 Schema 漂移")
        rows.append({"name": s["card_name"], "errs": errs})
    return {"rows": rows, "global": global_issues}


def main():
    import json
    res = check()
    err_total = sum(1 for r in res["rows"] if r["errs"])
    rate = err_total / max(1, len(res["rows"]))
    lines = [
        "# 解析提示词模板 · 结构回归（dry-run）", "",
        f"- 模板：parse_effects_system/user（v1.1）· 样例数：{len(res['rows'])}",
        f"- 说明：本检查为**结构自检**（占位/围栏/Schema 漂移），不调用 LLM；",
        f"  K2 ≥85% 解析准确率需黄金集 + 模型实测。", "",
        "| 样例 | 错误 |", "|---|---|"]
    for r in res["rows"]:
        lines.append(f"| {r['name']} | {' / '.join(r['errs']) if r['errs'] else '—'} |")
    lines += ["", f"**结构错误率：{rate:.0%}（{err_total}/{len(res['rows'])}）**"]
    if res["global"]:
        lines += ["", "全局问题：" + "；".join(res["global"])]
    text = "\n".join(lines)
    out = Path("artifacts/parse_check.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
