# -*- coding: utf-8 -*-
"""扩展卡池 ↔ 引擎 tag 词汇表 一致性守卫（2026-10-27）

为什么需要
----------
解析链（`packages/agent`）与结算层（`packages/engine`）各维护一份「条件名 /
计数源名」的词表。两边一旦漂移，表现是**运行时抛错**或**静默不生效**：

  · 解析侧产出 `dmg_plus:60:if:stadium_in_play`，引擎不认识 → 出手即 `ValueError`
    （卡进了池却打不出招）；
  · 反之引擎支持而解析侧不产出 → 只是少建模，不报错也看不出来。

本测试**只做静态词汇表比对**（不构造对局、不评估条件），因此秒级且无副作用；
覆盖面是「扩展池里真实存在的 tag」，比逐条手写用例更不容易漏。
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from engine import battle as B
from engine.families import fx_ability_block as FX_AB
from engine.families import known as fx_known

POOL = (Path(__file__).resolve().parents[2] / "engine" / "engine" / "data"
        / "cards_pool_ext.json")

# `dmg_plus:<n>:per:<counter>` / `dmg_plus:<n>:if:<cond>`
_DMG_PLUS = re.compile(r"^dmg_plus:\d+:(per|if):(.+)$")


# 引擎在 `_eval_condition` 开头**单独分支**处理的两个带参条件（不在
# `_CONDITION_PARAMS` 里 —— 后者会进提示词，这两个刻意不进，避免模型照抄半截串）
_SPECIAL_CONDITIONS = ("own_active_energy_type", "own_active_energy_at_least")


def _known_condition(cond: str) -> bool:
    if cond in B._CONDITIONS:
        return True
    name = cond.partition(":")[0]
    return name in B._CONDITION_PARAMS or name in _SPECIAL_CONDITIONS


def _known_counter(counter: str) -> bool:
    if counter.isdigit():
        return True
    if counter in B._COUNTERS:
        return True
    return bool(B._ENERGY_COUNTER_RE.match(counter))


class TestPoolTagVocabulary(unittest.TestCase):
    """池内每一个 `dmg_plus` 的条件/计数源都必须被引擎认识"""

    @classmethod
    def setUpClass(cls):
        data = json.loads(POOL.read_text(encoding="utf-8"))
        cls.cards = data.get("cards") or []

    def _iter_tags(self):
        for c in self.cards:
            for kind in ("attacks", "abilities"):
                for it in c.get(kind) or []:
                    tags = list(it.get("effects") or [])
                    if it.get("tag"):
                        tags.append(it["tag"])
                    for t in tags:
                        yield c.get("name"), it.get("name"), str(t)
            # 场地卡：顶层 tag 也参与词汇表一致性检查
            if c.get("kind") == "stadium" and c.get("tag"):
                yield c.get("name"), c.get("name"), str(c["tag"])

    def test_pool_exists_and_nonempty(self):
        self.assertGreater(len(self.cards), 0, "扩展池为空 → 守卫失效（假绿）")

    def test_dmg_plus_conditions_are_known(self):
        bad = []
        for name, item, tag in self._iter_tags():
            m = _DMG_PLUS.match(tag)
            if not m:
                continue
            mode, arg = m.group(1), m.group(2)
            ok = _known_condition(arg) if mode == "if" else _known_counter(arg)
            if not ok:
                bad.append(f"{name} · {item}：{tag}（引擎无此{mode}）")
        self.assertEqual(bad, [], "解析侧产出了引擎不认识的条件/计数源：\n"
                                  + "\n".join(bad))

    def test_no_unknown_ability_passive_tags(self):
        """受击反应 tag 的形态必须严格是 `<kind>:<arg>`（错位会静默不触发）"""
        allowed = {"status", "counter", "discard_energy"}
        bad = []
        for name, item, tag in self._iter_tags():
            if not tag.startswith("on_damaged:"):
                continue
            parts = tag.split(":")
            if len(parts) != 3 or parts[1] not in allowed:
                bad.append(f"{name} · {item}：{tag}")
                continue
            if parts[1] == "status" and parts[2] not in B.STATUSES:
                bad.append(f"{name} · {item}：{tag}（状态名非法）")
            if parts[1] in ("counter", "discard_energy") and not parts[2].isdigit():
                bad.append(f"{name} · {item}：{tag}（缺数值）")
        self.assertEqual(bad, [], "受击反应 tag 形态非法：\n" + "\n".join(bad))

    def test_fx_tags_recognized_by_engine(self):
        """解析侧产出的 `fx:` tag 必须被引擎「效果族」词表认识

        漂移后果：卡进了池却出手即 `ValueError`（卡打不出招），或静默不生效。
        `families.known` 是解析链准入与引擎派发的共用入口，逐字比对。
        """
        bad = []
        for name, item, tag in self._iter_tags():
            if not tag.startswith("fx:"):
                continue
            if not fx_known(tag):
                bad.append(f"{name} · {item}：{tag}（引擎无对应效果族）")
        self.assertEqual(bad, [], "解析侧产出了引擎不认识的 fx tag：\n"
                                  + "\n".join(bad))

    def test_stadium_ability_block_scope_known(self):
        """`stadium:ability_block:<scope>` 的 scope 必须落在引擎识别范围内

        范围词表由 `engine/families/fx_ability_block.py` 的
        `_STADIUM_ABILITY_BLOCK_SCOPES` 单一来源，解析侧不得超出。
        """
        prefix = "stadium:ability_block:"
        bad = []
        for name, item, tag in self._iter_tags():
            if not tag.startswith(prefix):
                continue
            scope = tag[len(prefix):]
            if not scope or scope not in FX_AB._STADIUM_ABILITY_BLOCK_SCOPES:
                bad.append(f"{name} · {item}：{tag}（引擎未识别的场地封锁范围）")
        self.assertEqual(bad, [], "场地封锁范围超出引擎词表：\n" + "\n".join(bad))


if __name__ == "__main__":
    unittest.main()
