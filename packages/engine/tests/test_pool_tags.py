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
from engine.cards import TYPES as CARD_TYPES
from engine.families import fx_ability_block as FX_AB
from engine.families import known as fx_known

POOL = (Path(__file__).resolve().parents[2] / "engine" / "engine" / "data"
        / "cards_pool_ext.json")

# `dmg_plus:<n>:per:<counter>` / `dmg_plus:<n>:if:<cond>`（2026-10-28 起同样校验
# `dmg_set` / `dmg_minus` 的缩放参数 —— 它们与 dmg_plus 共用同一套条件/计数源）
_DMG_PLUS = re.compile(r"^dmg_(?:plus|set|minus):-?\d+:(per|if):(.+)$")


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
    if B._ENERGY_COUNTER_RE.match(counter):
        return True
    # 带参数的动态计数源（2026-10-28）：词表以引擎 `is_known_counter` 为准
    return bool(B.is_known_counter(counter))


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
            # 化石卡（2026-10-28）：上场形态的特性挂在 `fossil.abilities` 下
            for it in (c.get("fossil") or {}).get("abilities") or []:
                if it.get("tag"):
                    yield c.get("name"), it.get("name"), str(it["tag"])
            # 场地卡 / 特殊能量：顶层 tag 也参与词汇表一致性检查
            if c.get("kind") == "stadium" and c.get("tag"):
                yield c.get("name"), c.get("name"), str(c["tag"])
            if c.get("supertype") == "Energy" and c.get("tag"):
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
        """受击反应 tag 的形态必须严格是 `<kind>:<arg>[:<extra>]`（错位会静默不触发）"""
        allowed = {"status", "counter", "discard_energy"}
        bad = []
        for name, item, tag in self._iter_tags():
            if not tag.startswith("on_damaged:"):
                continue
            parts = tag.split(":")
            # 第二十九批：`counter_per:<n>:<color>`（按持有者该色能量数反制）
            if parts[1:2] == ["counter_per"]:
                if len(parts) != 4 or not parts[2].isdigit() \
                        or parts[3] not in CARD_TYPES:
                    bad.append(f"{name} · {item}：{tag}（counter_per 形态非法）")
                continue
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

    def test_fossil_tag_shape(self):
        """化石卡：`fossil:<hp>:<ptype>` 必须与上场形态一致（HP / 属性合法）"""
        bad = []
        for c in self.cards:
            tag = str(c.get("tag") or "")
            if not tag.startswith("fossil:"):
                continue
            parts = tag.split(":")
            if len(parts) != 3 or not parts[1].isdigit() \
                    or int(parts[1]) <= 0:
                bad.append(f"{c.get('name')}：{tag}（形态非法）")
                continue
            if parts[2] not in CARD_TYPES:
                bad.append(f"{c.get('name')}：{tag}（属性非法）")
            payload = c.get("fossil") or {}
            if int(payload.get("hp") or 0) != int(parts[1]):
                bad.append(f"{c.get('name')}：{tag} ↔ 上场形态 "
                           f"HP {payload.get('hp')}")
        self.assertEqual(bad, [], "化石卡 tag ↔ 上场形态不一致：\n" + "\n".join(bad))

    def test_senergy_kind_known(self):
        """特殊能量的 `senergy:<kind>` 必须是子系统已建模的种类"""
        from engine import special_energy as SE
        bad = []
        for c in self.cards:
            tag = str(c.get("tag") or "")
            if not tag.startswith("senergy:"):
                continue
            kind = tag.split(":", 1)[1]
            if kind not in SE.KINDS:
                bad.append(f"{c.get('name')}：{tag}（子系统未建模）")
        self.assertEqual(bad, [], "池内出现未建模的特殊能量：\n" + "\n".join(bad))

    def test_attack_limit_tag_shapes(self):
        """攻击复用 / 下回合限制 / 无视效果：tag 形态必须与引擎解析一致"""
        bad = []
        for name, item, tag in self._iter_tags():
            if tag.startswith("borrow_attack:"):
                if tag.split(":", 1)[1] not in (
                        "opp_active", "opp_active_tera", "bench_ally_n"):
                    bad.append(f"{name} · {item}：{tag}（借用来来源非法）")
            elif tag.startswith("attack_denial:"):
                parts = tag.split(":")
                if len(parts) != 3 or parts[1] != "coin" or parts[2] != "turn":
                    bad.append(f"{name} · {item}：{tag}（denial 形态非法）")
            elif tag.startswith(("attack_cost_up:", "retreat_cost_up:")):
                parts = tag.split(":")
                if len(parts) != 4 or parts[1] != "opp" or parts[2] != "turn" \
                        or not parts[3].isdigit() or int(parts[3]) <= 0:
                    bad.append(f"{name} · {item}：{tag}（费用/撤退增量非法）")
            elif tag.startswith("ignore_effects"):
                if tag != "ignore_effects":
                    bad.append(f"{name} · {item}：{tag}")
        self.assertEqual(bad, [], "攻击限制 tag 形态非法：\n" + "\n".join(bad))

    def test_counters_tag_shapes(self):
        """`counters:` tag 形态：``<n>[:per:<counter>][:scope:opp[:<n>]][:selfko]``

        第二十批（2026-10-29）：`counters:` 从「只打战斗位」扩展到
        `:scope:opp`（任意对手宝可梦，含战斗位）+ `:selfko`（特性自毁）。
        形态错位会让 `_apply_counters` 抛错（卡进池却打不出）或静默打错目标。
        """
        bad = []
        for name, item, tag in self._iter_tags():
            subs = ([p for p in tag[len("evolve_act:"):].split("|") if p]
                    if tag.startswith("evolve_act:") else [tag])
            for t in subs:
                if not str(t).startswith("counters:"):
                    continue
                body = str(t)[len("counters:"):]
                if body.endswith(":selfko"):
                    body = body[: -len(":selfko")]
                if ":scope:" in body:
                    body, _sc = body.split(":scope:", 1)
                    _sp = _sc.split(":")
                    # 第二十八批：`:scope:ability:all`（双方「有特性者」逐个）
                    if _sp[0] == "ability":
                        if len(_sp) != 2 or _sp[1] != "all":
                            bad.append(f"{name} · {item}：{t}（ability 范围只支持 all）")
                        continue
                    if _sp[0] != "opp" or (len(_sp) > 1
                                           and _sp[1] != "all"
                                           and not _sp[1].isdigit()):
                        bad.append(f"{name} · {item}：{t}（counters 范围非法）")
                        continue
                if ":per:" in body:
                    body, per = body.split(":per:", 1)
                    if not _known_counter(per):
                        bad.append(f"{name} · {item}：{t}（计数源未建模）")
                        continue
                if not body.isdigit():
                    bad.append(f"{name} · {item}：{t}（指示物数非法）")
        self.assertEqual(bad, [], "counters tag 形态非法：\n" + "\n".join(bad))

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

    def test_pre_phase_tag_shapes(self):
        """`pre:` **伤害前阶段**只接受两种精确形态（第二十五批）

        ``pre:discard_tool:<n|all>:<scope>`` · ``pre:discard_energy:<n|all>:opp:special``
        ——形态错位会让 `_apply_pre_attack_effect` 抛错（卡进池却打不出招），
        或被 `_apply_discard_tool` 静默按错误范围弃置。
        """
        bad = []
        for name, item, tag in self._iter_tags():
            if not tag.startswith("pre:"):
                continue
            parts = tag.split(":")
            head = parts[1] if len(parts) > 1 else ""
            if head == "discard_tool":
                if len(parts) < 4 or parts[3] != "opp_active" \
                        or not (parts[2] == "all" or parts[2].isdigit()):
                    bad.append(f"{name} · {item}：{tag}（伤害前弃道具形态非法）")
            elif head == "discard_energy":
                if len(parts) != 5 or parts[3] != "opp" or parts[4] != "special" \
                        or not (parts[2] == "all" or parts[2].isdigit()):
                    bad.append(f"{name} · {item}：{tag}（伤害前弃能形态非法）")
            else:
                bad.append(f"{name} · {item}：{tag}（引擎未实现的伤害前效果）")
        self.assertEqual(bad, [], "pre: tag 形态非法：\n" + "\n".join(bad))


if __name__ == "__main__":
    unittest.main()
