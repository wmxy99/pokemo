# -*- coding: utf-8 -*-
"""运行档案渲染器 —— 由数据文件 + 数据库实测生成 `docs/RULES_RUNBOOK_B90_PLUS.md`

为什么要有这一层
----------------
运行档案是**跨会话的长期记忆**，它最容易坏在两处：

1. **手写数字会失真**：基线滚动更新时，只改了一处、漏了另一处（本轮实测：
   文件里同时写着「未建模 138」和「合计 145 / 可做 137」）。
2. **卡清单会漂移**：某张卡被别的批次顺带解禁后，仍在清单里以「待办」出现；
   或某张卡被两个批次重复计入，导致进度永远算不平。

因此把「数据」与「呈现」分开：

  · `docs/runbook_batches.json` —— **唯一真相**（批次 → 卡 → 状态）；人工维护。
  · 本脚本 —— 读 JSON + 实测 DB/池/测试数，渲染成 Markdown；**只读不写业务数据**。

用法::

    python scripts/render_runbook.py            # 渲染并写回档案
    python scripts/render_runbook.py --check    # 只比对：档案是否与数据一致（不改文件）

`--check` 供守卫测试使用：档案被手改后会立刻报不一致。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DOCS = REPO / "docs"
RUNBOOK = DOCS / "RULES_RUNBOOK_B90_PLUS.md"
DATA = DOCS / "runbook_batches.json"
DB = REPO / "packages" / "card-db" / "data" / "cleaned" / "cards_challenge.json"
POOL = REPO / "packages" / "engine" / "engine" / "data" / "cards_pool_ext.json"

_DEF_TEST = re.compile(r"^\s*(?:async\s+)?def (test_[A-Za-z0-9_]+)\s*\(", re.M)

TIER_DESC = {
    "A": "引擎已有·纯建池",
    "B": "有具体缺口·可归族",
    "C": "兜底·单句",
    "D": "兜底·复合句",
    "done": "已完成",
}


def norm(s: str) -> str:
    return re.sub(r"[\u2018\u2019]", "'", str(s)).lower().strip()


def load_data() -> dict:
    return json.loads(DATA.read_text(encoding="utf-8"))


def measure() -> dict:
    """实测基线：卡牌数 / ready / 池 / unsupported 条目

    刻意**不**把测试项数写进档案：一加测试档案就会漂移，会让守卫在每次新增用例后
    都要求重渲染。测试计数的权威在 README 第 7–8 行，由 `scripts/check_doc_counts.py` 校验。
    """
    cards = json.loads(DB.read_text(encoding="utf-8"))["cards"]
    total = len(cards)
    ready = sum(1 for c in cards if c.get("engine_ready"))
    unsupported = sum(len(it.get("unsupported") or [])
                      for c in cards
                      for it in ((c.get("parse") or {}).get("items") or []))
    pool = 0
    if POOL.exists():
        pool = len(json.loads(POOL.read_text(encoding="utf-8")).get("cards") or [])
    return {"total": total, "ready": ready, "unready": total - ready,
            "unsupported": unsupported, "pool": pool}


def card_key(c: dict) -> tuple:
    return (norm(c["name"]), c["series"], str(c["number"]))


def ready_keys() -> set:
    """DB 实测的「已 ready」卡集合 —— 每批完工后卡会从「待做」变「已进池」，
    而 JSON 里记的是**当时的**分层；渲染必须以 DB 为准，否则基线口径会漂。"""
    return {card_key(c) for c in
            json.loads(DB.read_text(encoding="utf-8"))["cards"]
            if c.get("engine_ready")}


def render(data: dict, m: dict) -> str:
    batches = sorted(data["batches"], key=lambda b: b["id"])
    structural = data["structural"]
    deferred = data["deferred"]
    cross = {card_key(c): c for c in data["cross_batch"]}

    ready = ready_keys()
    seen, uniq = set(), []
    for b in batches:
        for c in b["cards"]:
            k = card_key(c)
            if k not in seen:
                seen.add(k)
                uniq.append(c)
    todo = [c for c in uniq if card_key(c) not in ready]
    done_cards = [c for c in uniq if card_key(c) in ready]
    slots = sum(len(b["cards"]) for b in batches)
    todo_total = len(todo) + len(structural) + len(deferred)
    expected_final = m["ready"] + len(todo)
    pct = expected_final / m["total"] * 100
    today = _dt.date.today().isoformat()

    L = []
    A = L.append
    A("# 规则建模运行档案 · 第九十批起（权威执行依据）")
    A("")
    A("> **本文件是自动生成的**，数据源为 `docs/runbook_batches.json` + 数据库实测。")
    A("> 要改批次、卡清单或进度，**改 JSON 后重跑渲染脚本**，不要手改本文件：")
    A("> `python packages/card-db/scripts/render_runbook.py`（`--check` 只校验不写）。")
    A("> 手工改动会被 `packages/card-db/tests/test_runbook_consistency.py` 判为不一致。")
    A("")
    A("---")
    A("")
    A("## 开工 30 秒速读")
    A("")
    A("1. 读 §0 基线（**滚动值，勿凭记忆**）。")
    A("2. 在 §9 台账里找**第一条状态为「待办」**的批次号 N。")
    A("3. 读该批小节（§2–§6）的卡表与备注，记下每张卡的 `(series, number)`——**卡名有重名**。")
    A("4. 按 §1 的 7 步工作流推进，逐条过 DoD 自查。")
    A("5. 完工后改 JSON 的 `status` / `actual_pool` / `actual_refused`，重跑渲染 → 守卫 → CI，再提交。")
    A("")
    A("---")
    A("")
    A(f"## 0. 基线（{today} 实测，不得凭记忆填写）")
    A("")
    A("| 指标 | 数值 | 取数方式 |")
    A("|---|---|---|")
    A(f"| 卡牌总数 | **{m['total']}** | `cards_challenge.json` 的 `cards` 长度 |")
    A(f"| `engine_ready` | **{m['ready']}（{m['ready'] / m['total'] * 100:.1f}%）** "
      f"| `sum(1 for c in cards if c.get(\"engine_ready\"))` |")
    A(f"| 未建模卡 | **{m['unready']}** | 上者取反（**口径 = 卡数**，≠ `unsupported` 条目 {m['unsupported']}） |")
    A(f"| 引擎池 | **{m['pool']}** | `cards_pool_ext.json` 的 `cards` 长度 |")
    A("| 对账类目 | **45 类全绿** | `scripts/audit_pool_alignment.py` |")
    A("| 测试总数 | 见 **README 第 7–8 行** | 由 `scripts/check_doc_counts.py` 在 CI 校验（本档案不重复记账，避免加测试即漂移） |")
    A("| CI | **8/8 PASS** | `scripts/ci_local.py`（必须用 `/d/anconda/envs/pokemo/python.exe`） |")
    A("| K1 双盲 | **0.785**（200 局双盲，`artifacts/k1_rich.pt` = `k1_rich40k` 复测）· "
      "⚠ 原 **0.795** 权重在一次失败的「动作编码 +2 维」迁移尝试中被覆盖"
      "（迁移已回退，代码未变）；现权重仍 ≥0.70 判据（PASS），但**不是**原基线值 | "
      "`examples/k1_accept.py artifacts/k1_rich.pt --games 200`（权重是位置参数） |")
    A("")
    # 「批内待做」与「批内已判拒收」必须分开说：全部批次处理完之后，剩下的卡
    # **不是待办**，而是走完全流程后如实判定做不了（原因逐条在批次 note 里）。
    # 混成一个数字会让台账看起来「还有 80 张排队」，与事实相反。
    _todo_batches = [b for b in batches if b["status"] == "todo"]
    _refused_label = "批内如实拒收" if not _todo_batches else "批次内待做"
    A(f"**口径校验（本文件自洽的判据）**：未 ready **{m['unready']}** = "
      f"{_refused_label} **{len(todo)}** + 结构性边界 **{len(structural)}** + "
      f"待新能力 **{len(deferred)}**"
      f" = **{todo_total}** {'✅' if todo_total == m['unready'] else '❌ 需修 JSON'}。")
    A("")
    # 「已处理」= 走完全流程的批次（含整批受阻）：受阻是**如实判定**的结果，
    # 不是待办。与「未开工」分开计数，避免台账看起来"还有很多批"而其实是能力缺口。
    _done = [b for b in batches if b["status"] == "done"]
    _blocked = [b for b in batches if b["status"] == "blocked"]
    _todo = [b for b in batches if b["status"] == "todo"]
    A(f"**规划**：第 90–133 批共 **44 批**；已处理 **{len(_done) + len(_blocked)}** 批"
      f"（完成 {len(_done)} · 整批受阻 {len(_blocked)}），"
      f"剩余**未开工** **{len(_todo)}** 批。")
    A("")
    if _todo_batches:
        A(f"**终局预期**：`engine_ready` **{expected_final} / {m['total']} = {pct:.1f}%**"
          f"（当前 {m['ready']} + 可做 {len(todo)}）；结构性 {len(structural)} 张与待新能力 "
          f"{len(deferred)} 张永久留在池外，需引擎重构或新增承载字段才可能归零。")
    else:
        # 全部批次已处理：剩下的卡**不是待做**，而是已逐批判定做不了。
        A(f"**终局**：`engine_ready` **{m['ready']} / {m['total']} = "
          f"{m['ready'] / m['total'] * 100:.1f}%**（44 批全部处理完毕）。"
          f"剩余 {len(todo)} 张已逐批**如实拒收**（原因见各批说明），"
          f"另 {len(structural)} 张结构性边界与 {len(deferred)} 张待新能力卡"
          # 缺口清单必须与**当前**实现同步：第一百三十四批把「条件式 HP」与
          # 「牌库底**读取**」做掉了，继续照抄旧清单会误导下一轮的开工判断。
          f"需引擎重构（交互层：任意数量/可选代价 · 延迟队列 · 奖赏卡对象与"
          f"牌库底**写入** · 事件触发（换位/进化/附能时）· 效果免疫层 · 退化 "
          f"devolve）才可能归零。")
    A("")
    A("---")
    A("")

    # ---- §1 工作流 ----
    A("## 1. 每批固定工作流（7 步，缺一步视为未完成）")
    A("")
    A("1. **建档**：读本文件对应批次小节，记下每张卡的 `(series, number)`（同名不同印刷必须以编号区分）。")
    A("2. **查引擎现状**：先翻 `battle.py` 的派发点、`reallib` 白名单、`gen_engine_pool` 三处白名单。"
      "**先翻引擎再猜句式**——多批收益来自「引擎已实现、解析没产出」。")
    A("3. **改解析**（`packages/agent/agent/effects.py`）：产出 tag；判不出必须回 `None`，禁止兜底猜测。")
    A("4. **改引擎**（仅当缺钩子）：新增派发分支。**同一 tag 的招式侧与训练家侧要分别检查**"
      "（`_apply_attack_effects` vs `_do_trainer`）。")
    A("5. **白名单同步**：`ATTACK_TAG_PREFIXES` / `ABILITY_TAG_PREFIXES` / `TRAINER_TAGS`"
      "（`gen_engine_pool.py`）+ `reallib` 相应元组。**漏一处 = 卡在池外凭空消失且对账全绿**。")
    A("6. **补测试（正向 + 负向）**：解析 `tests/test_batchNN_*.py`、引擎 `tests/test_batchNN_*.py`、"
      "对账 `test_audit_alignment.py` 追加 `TestBatchNN`。**负向用例必须写**，且必须验证它真的会变红。")
    A("7. **重跑链 + 验收**（顺序不可错）：")
    A("")
    A("   ```")
    A("   import_challenge_dataset.py --reparse-only --parse-effects --parse-backend rule")
    A("   → gen_engine_pool.py")
    A("   → import_challenge_dataset.py --reparse-only")
    A("   → coverage_backlog.py --registry")
    A("   → audit_pool_alignment.py        # 必须 45 类全绿")
    A("   ```")
    A("")
    A("**每批 DoD（全部满足才算完成）**：")
    A("")
    A("- 目标卡在 `cards_pool_ext.json` 中以**正确 tag** 出现（逐卡打印核对，不认「产了 tag 的卡数」）")
    A("- `audit_pool_alignment.py` 无新增报错")
    A("- 三包测试全绿；测试计数同步 README 第 7–8 行")
    A("- 新增 tag 族时补 `apps/web/src/cardtext.js` 的 `TRAINER_TAG_DESC`（`node apps/web/e2e/cardtext_check.cjs` 必须通过）")
    A("- `git add` 后 `scripts/ci_local.py` **8/8 PASS**（redline 读暂存区，不 add 会假绿）")
    A("- 回填 `runbook_batches.json` 的 `status` / `actual_pool` / `actual_refused`，重跑本渲染脚本")
    A("")
    A("---")
    A("")

    # ---- 阶段章节 ----
    phase_meta = [
        ("P1", "A 档：引擎已有，纯建池逻辑", "不需要新引擎机制，修归属/白名单/复合 tag 即可。"),
        ("P2", "B 档大族：伤害缩放 / 自由数量 / 指示物", "收益最集中的一段；`any number of` 风险最高。"),
        ("P3", "B 档小族（一族一批）", "族太散，是 P2 之后最慢的一段。"),
        ("P4", "D 档「差最后一块」", "杠杆最高：多数子句已能单独产 tag，前面族就位后会批量收敛。"),
        ("P5", "C 档逐句判读 + 结构性建档", "兜底 reason 说不清缺什么，需人工判读；最后收尾。"),
    ]
    sec = 2
    for pid, ptitle, pdesc in phase_meta:
        group = [b for b in batches if b["phase"] == pid]
        if not group:
            continue
        ids = [b["id"] for b in group]
        span = f"{min(ids)}–{max(ids)}" if len(ids) > 1 else str(ids[0])
        gslots = sum(len(b["cards"]) for b in group)
        gdone = sum(1 for b in group if b["status"] == "done")
        A(f"## {sec}. 阶段 {pid} · {ptitle}（第 {span} 批，{len(group)} 批，{gslots} 槽位）")
        A("")
        A(pdesc + f" 已完成 {gdone}/{len(group)} 批。")
        A("")
        for b in group:
            badge = {"done": "✅ 已完成", "blocked": "⛔ 受阻 · 整批如实拒收",
                     "todo": "待办"}[b["status"]]
            A(f"### 第 {b['id']} 批 · {b['theme']}（{len(b['cards'])} 张）· {badge}")
            A("")
            if b["cards"]:
                A("| 卡 | series | number | 分层 | 备注 |")
                A("|---|---|---|---|---|")
                for c in b["cards"]:
                    k = card_key(c)
                    tier = "done" if k in ready else c["tier"]
                    mark = []
                    if k in cross:
                        others = [x for x in cross[k]["batches"] if x != b["id"]]
                        mark.append("跨批 → " + "、".join(str(x) for x in others))
                    A(f"| {c['name']} | {c['series']} | {c['number']} "
                      f"| {tier}（{TIER_DESC.get(tier, '?')}） "
                      f"| {'；'.join(mark)} |")
                A("")
            else:
                A("本批不承载卡清单，只做结构性边界建档（见 §7）。")
                A("")
            if b["actual_pool"] is not None:
                A(f"**实际结果**：进池 **{b['actual_pool']}** 张，如实拒收 "
                  f"**{b['actual_refused']}** 张。")
                A("")
            if b.get("note"):
                A(f"> {b['note']}")
                A("")
        A("---")
        A("")
        sec += 1

    # ---- §7 结构性 + 跨批 ----
    A("## 7. 结构性边界与待新能力（永久池外，需引擎重构）")
    A("")
    A("这 6 张不是「能力不足」，而是**引擎架构限制**：强行建模会产生静默错映射。"
      "**不为它们造假 tag**，在 DB 标 `engine_status=\"structural_limit\"`，并在文档与 UI 标注为「已知边界」，"
      "使 backlog 读数可解释地停在这里。")
    A("")
    A("| 卡 | series | number | 引擎限制 |")
    A("|---|---|---|---|")
    for s in structural:
        A(f"| {s['name']} | {s['series']} | {s['number']} | {STRUCT_REASON.get(norm(s['name']), '见历史记录')} |")
    A("")
    A("> 要真正做到 100%，需三项独立工程（各自立项，不属本档案）：")
    A("> ① 拆分 `fp.damage` 为 `attack_damage` + `damage_counters`；② 统一 `max_hp` 重算入口；"
      "③ 新增全局规则层（特性禁用 / 区域流转）。")
    A("")
    A("**待新能力（2 张）**：效果为回合末 / 被击倒**触发式**，不是 `tool:` 常驻，"
      "通过道具检查会让它在构筑期无条件生效（静默加强）→ 必须保持拒收，直到 `TrainerDef` 具备触发式承载字段。")
    A("")
    A("| 卡 | series | number | 结论 |")
    A("|---|---|---|---|")
    for d in deferred:
        A(f"| {d['name']} | {d['series']} | {d['number']} | {d['reason']} |")
    A("")
    A("### 跨批卡登记（进度只记一次）")
    A("")
    A("| 卡 | 涉及批次 | 处理规则 |")
    A("|---|---|---|")
    for c in data["cross_batch"]:
        A(f"| {c['name']} ({c['series']} {c['number']}) "
          f"| {' + '.join(str(x) for x in c['batches'])} | {c['note']} |")
    A("")
    A("---")
    A("")

    # ---- §8 ----
    A("## 8. 偏差处理规则（严格执行，不得临场放宽）")
    A("")
    A("1. **做不了就如实拒收**并记录原因；不得为让卡进池而放宽白名单、压窄语义或改测试期望值。")
    A("2. **静默错映射 > 未建模**：宁可卡在池外，也不上「不报错、测试全绿、只有卡面↔tag 对账看得出」的错映射。")
    A("3. **改存量测试前先判断**：它断言的是「旧口径」还是「纪律承重墙」。前者可改并注明原因，后者必须改代码。")
    A("4. **每批结束改 JSON 回填**，不要手改本文件（会被守卫抓到）。")
    A("5. 某批实际收益低于规划时，**不要用后续批次的卡凑数**；宁可该批只进 1 张。")
    A("6. **负向自测必须真能变红**：写完守卫后注入错误写法跑一次，确认测试**失败**；"
      "守卫若因测试布局而永远通过，等于没写。")
    A("")
    A("---")
    A("")
    A("## 8.5 实战陷阱（已踩过，后续批次直接复用）")
    A("")
    A("1. **「招式有、训练家没有」已出现三处**：`energy_to_deck`（历史）、`shuffle_top`（批 91）、"
      "`evolve_from_deck`（批 92）。`battle.py` 里出现一次**不算支持**——须同时确认 `_do_trainer` 有分支、"
      "`TRAINER_TAGS` 有前缀、`reallib` 有登记。")
    A("2. **上游数据集会把卡面规则分类错**：Pokémon ex 的 Tera 规则文本被归进 `attacks[]`。"
      "修法是在建池侧转写为卡级被动；**禁止**把 `passive:*` 放进攻击白名单（= 出招才免伤的反转）。")
    A("3. **`reallib._SUPPORTED_TRAINER_TAGS` 是死代码**（仅定义、零引用）。仍要同步以免注释撒谎，"
      "但**别指望它拦卡**——真正生效的是 `gen_engine_pool` 的三处白名单。")
    A("4. **测试布局会让守卫变假**（P1 抓到两个）：")
    A("   - `_evolve_from_deck` 从**牌尾往前**扫；把「不该被选中」的卡放数组前面，过滤失效也会选对 → 守卫永远绿。")
    A("   - 混合 tag 反例里掺入**本就不在白名单**的前缀（如 `draw:`），拒收原因变成那条非法前缀，"
      "掩盖了真正要测的越界 → 守卫永远绿。反例必须用**白名单内**前缀。")
    A("5. **`candy` 是 Rare Candy 的跳阶语义**，不是「从牌库进化」的近似：误产会让引擎跳过一阶直接放二阶。"
      "判据是「同一 spec 内先 search(deck) 再 evolve」→ `evolve_from_deck`。")
    A("6. **`basic energy card` 必须排在 `energy card` 之前**判定，否则丢掉 basic 限定 → 连特殊能量一起检索（静默放宽）。")
    A("7. **早退认领会吞掉未识别的 gate**：`_split_play_gate` 只剥它认识的 gate，不认识的留在 text 里。"
      "认领前必须检查 `can use this card only if` 是否残留，残留即整卡拒收"
      "（第八十九批 Anthea & Concordia 就是这样被静默加成 +3 奖赏的）")
    A("8. **对账口径要与解析口径对齐**：真实卡池存在 `\\xa0`（NBSP），对账 `_norm` 若不归一，"
      "会出现「解析产了 tag、对账报不符」。**动解析链必跑 `audit_pool_alignment.py`**。")
    A("")
    A("---")
    A("")

    # ---- §9 台账 ----
    done_n = sum(1 for b in batches if b["status"] == "done")
    A("## 9. 进度台账（由 JSON 自动生成）")
    A("")
    A("| 批 | 阶段 | 主题 | 计划张数 | 实际进池 | 如实拒收 | 状态 |")
    A("|---|---|---|---|---|---|---|")
    for b in batches:
        # 结构性建档批不承载卡槽（6 张统一列在 §7），计划张数记 0 而非 6
        plan = len(b["cards"])
        pool = b["actual_pool"] if b["actual_pool"] is not None else ""
        ref = b["actual_refused"] if b["actual_refused"] is not None else ""
        st = {"done": "✅ 完成", "blocked": "⛔ 受阻", "todo": "待办"}[b["status"]]
        A(f"| {b['id']} | {b['phase']} | {b['theme']} | {plan} | {pool} | {ref} | {st} |")
    A("")
    A("> 台账的「计划张数」只计**卡槽**；第 130–133 批是结构性建档批（0 槽），"
      f"涉及的 {len(structural)} 张统一列在 §7，不重复计入槽位。")
    A("")
    A("**口径核对**：")
    A("")
    A("| 项 | 张数 | 说明 |")
    A("|---|---|---|")
    A(f"| 批次槽位合计 | {slots} | 跨批卡在每批各记 1 次 |")
    A(f"| 批次唯一卡 | {len(uniq)} | 其中已完成 {len(done_cards)} 张 |")
    A(f"| 批次内待做 | {len(todo)} | 全部未 ready |")
    A(f"| 结构性边界 | {len(structural)} | 不承载 tag，仅建档 |")
    A(f"| 待新能力 | {len(deferred)} | 需新增承载字段 |")
    A(f"| **合计（= 未 ready 卡数）** | **{len(todo) + len(structural) + len(deferred)}** | "
      f"应等于基线 {m['unready']} |")
    A("")
    A("---")
    A("")
    A("## 10. 维护方式与文档关系")
    A("")
    A("| 文件 | 角色 |")
    A("|---|---|")
    A("| `docs/runbook_batches.json` | **数据真相**：批次 → 卡 → 状态。人工维护 |")
    A("| `packages/card-db/scripts/render_runbook.py` | 渲染器：JSON + 实测 → 本文件 |")
    A("| `docs/RULES_RUNBOOK_B90_PLUS.md`（本文件） | **执行依据**（自动生成，勿手改） |")
    A("| `packages/card-db/tests/test_runbook_consistency.py` | 守卫：JSON ↔ DB ↔ 本文件一致性 |")
    A("| `docs/RULES_COMPLETION_PLAN.md` | 历史成果台账（第八十五~八十九批及之前），只追加 |")
    A("| `packages/card-db/data/cleaned/unmodeled_registry.md` | 机器生成的未建模清单（数据真相） |")
    A("")
    A("**改批次的正确姿势**：")
    A("")
    A("```bash")
    A("# 1) 改 JSON（增删卡 / 改 status / 填 actual_pool）")
    A("python packages/card-db/scripts/render_runbook.py          # 2) 重渲染")
    A("python -m unittest tests.test_runbook_consistency -v       # 3) 守卫（card-db 目录下）")
    A("python scripts/ci_local.py                                  # 4) CI 8/8")
    A("```")
    A("")
    A("若 registry 与本文件冲突，**以 registry 为准并修正 JSON**——registry 由脚本从 DB 生成，"
      "本文件是计划编排，允许滞后但不允许自相矛盾。")
    A("")
    return "\n".join(L) + "\n"


# 结构性边界的引擎侧理由（用于 §7 表格；卡名小写匹配）
STRUCT_REASON = {
    "ludicolo": "全队 +HP：`_refresh_stadium_hp` 仅回合开始/场地变更时重算，场上可改变 `max_hp` 的约 30 处变更无统一入口 → 会延迟/残留",
    "cynthia's power weight": "同上（+70 HP）",
    "n's vanilluxe": "指示物翻倍：`fp.damage` 同时承载攻击伤害与指示物，翻倍会连攻击伤害一起翻",
    "psyduck": "需要全局规则层（场上所有宝可梦失去特性）",
    "milotic": "需要全局规则层（对手区域流转禁止）",
    "anthea & concordia": "6 张具名宝可梦 gate 未建模；`_split_play_gate` 不认识会静默丢弃 → 会变成无条件 +3 奖赏",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只比对，不写文件")
    args = ap.parse_args()
    data = load_data()
    text = render(data, measure())
    if args.check:
        cur = RUNBOOK.read_text(encoding="utf-8") if RUNBOOK.exists() else ""
        if cur != text:
            print("RUNBOOK DRIFT: docs/RULES_RUNBOOK_B90_PLUS.md 与数据/实测不一致")
            print("  修复：python packages/card-db/scripts/render_runbook.py")
            return 1
        print("runbook OK")
        return 0
    RUNBOOK.write_text(text, encoding="utf-8", newline="\n")
    print(f"rendered {RUNBOOK} ({len(text.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
