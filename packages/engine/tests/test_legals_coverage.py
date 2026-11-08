# -*- coding: utf-8 -*-
"""守卫 · 池内训练家卡必须**真的能打**（S3 发现的结构性漏洞）

为什么需要这条守卫
------------------
2026-11-08 做 S3 时发现：有一批训练家 tag 是这样进来的 ——

  · 解析链产出 tag                  ✅
  · `gen_engine_pool.build_trainer` 白名单放行  ✅
  · `_do_trainer` 有派发分支        ✅
  · `_trainer_legals` **没有分支**   ❌ ← 漏的就是这一处

后果：卡片堂堂正正进了池子（`engine_ready=true`、能抽到、能统计覆盖率），
但 `legal_actions()` 永远不会给出它 —— AI 一辈子选不到，人类玩家手里是**死卡**。
实测命中 13 张：Dusk Ball（recon_bottom）· Ruffian（discard_tool_senergy）·
Enhanced Hammer（discard_energy:）· Hand Trimmer · Secret Box · Deduction Kit ·
Hop's Bag · Precious Trolley · Iron Defender · Ciphermaniac's Codebreaking ·
Salvatore · Briar · Larry's Skill · Team Rocket's Ariana · Lillie's Pearl。

历史纪律 #19 说「判定引擎支持必须四处同查」，但列的四处里**没有**
`_trainer_legals` —— 这正是本条守卫要钉死的东西。

守卫做法
--------
静态比对（不构造对局、不受状态影响）：
  1. 从 `battle.py` 的 `_trainer_legals` 里抽出所有 `tag.startswith("…")` /
     `tag == "…"` 的字面前缀；
  2. 取池子里**每条训练家卡**的 `tag`；
  3. 断言每条 tag 都能被某个前缀覆盖（`tag.startswith(prefix)`）。

前缀匹配是**安全的**（不是精确相等）：`elif tag.startswith("discard:")` 确实
覆盖 `discard:2`，所以不需要逐条登记变体。

允许清单 `_ALLOW` 只放「确实不该出现在合法动作表里」的 tag，且必须写明理由。
"""
from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BATTLE = REPO / "packages" / "engine" / "engine" / "battle.py"
POOL = REPO / "packages" / "engine" / "engine" / "data" / "cards_pool_ext.json"

# tag → 理由（**只能放真的不该可打的**；放「暂时懒得修」等于把守卫变成摆设）
_ALLOW: dict = {}


def _func(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"battle.py 里找不到函数 {name}")


def _tag_prefixes(fn) -> set:
    """抽出函数体里与 `tag` 相关的字面前缀

    识别两种写法（本项目实际使用的全部形态）：
      · `tag.startswith("x")` / `tag.startswith(("x", "y"))`
      · `tag == "x"` / `"x" == tag`
    """
    out = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "startswith" and node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                out.add(arg.value)
            elif isinstance(arg, (ast.Tuple, ast.List)):
                out.update(e.value for e in arg.elts
                           if isinstance(e, ast.Constant)
                           and isinstance(e.value, str))
        if isinstance(node, ast.Compare) and len(node.comparators) == 1:
            left, right = node.left, node.comparators[0]
            for a, b in ((left, right), (right, left)):
                if isinstance(a, ast.Name) and a.id == "tag" \
                        and isinstance(b, ast.Constant) \
                        and isinstance(b.value, str):
                    out.add(b.value)
    return {x for x in out if x}


class TestPoolTrainersArePlayable(unittest.TestCase):
    def test_every_pool_trainer_tag_has_a_legals_branch(self):
        tree = ast.parse(BATTLE.read_text(encoding="utf-8"))
        prefixes = _tag_prefixes(_func(tree, "_trainer_legals"))
        self.assertTrue(prefixes, "没抽到任何前缀 —— 守卫本身失效了")

        pool = json.loads(POOL.read_text(encoding="utf-8"))["cards"]
        missing = []
        for c in pool:
            if str(c.get("supertype")) != "Trainer":
                continue
            tag = str(c.get("tag") or "")
            if not tag or tag in _ALLOW:
                continue
            if not any(tag.startswith(p) for p in prefixes):
                missing.append(f"{c['name']}（{tag}）")
        self.assertEqual(
            missing, [],
            "以下池内训练家 tag 在 `_trainer_legals` 里没有任何分支 → "
            "`legal_actions()` 永不给出 = 池内死卡：\n  " + "\n  ".join(missing))

    def test_guard_itself_can_fail(self):
        """★ 负向自测：喂一个**不在**前缀集里的 tag，守卫必须报错

        这条不测产品代码，测的是「守卫逻辑真的会红」——
        历史上有过「假守卫」（数据布局让错误也命中正确结果）。
        """
        tree = ast.parse(BATTLE.read_text(encoding="utf-8"))
        prefixes = _tag_prefixes(_func(tree, "_trainer_legals"))
        fake = "definitely_not_a_real_tag:1"
        self.assertFalse(any(fake.startswith(p) for p in prefixes),
                         "负向样例必须真的不被覆盖，否则这条自测是假的")
        # 再反向确认：把一个**真实存在**的 tag 前缀当反例，必须能命中
        self.assertTrue(any("discard:2:any".startswith(p) for p in prefixes))


if __name__ == "__main__":
    unittest.main()
