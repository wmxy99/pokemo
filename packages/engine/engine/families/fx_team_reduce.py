# -*- coding: utf-8 -*-
"""fx 族：**全队静态减伤**（引擎侧 · 2026-10-29 第十九批）

与 `fx_static_dmg.py` 相反方向：效果源是**己方场上任意一只**的特性，作用对象
是「己方全队（可按类别 / 属性 / 阵营筛选）」——因此不能挂在受击者自己的
`passive:` 上（那只能表达「自身减伤」），必须由 `battle` 在守方阵营里扫描。

tag 形态（逐字解析，未知形态抛 ValueError）：
    fx:team_reduce:<n>:basic:<color>[:other:<namekey>][:nonstack]
    fx:team_reduce:<n>:energy:<color>
    fx:team_reduce:<n>:faction:<namekey>[:source_bench][:nonstack]

语义要点
--------
· 减伤发生在**弱点/抗性之后**（卡面括号），与 `passive:dmg_reduce` 同口径；
· ``other:<key>``：来源之外**至少还有 1 只**卡名含该键的宝可梦在场（Curly Wall）；
· ``source_bench``：特性来源必须在**后备**（Stone Palace）；
· ``nonstack``：同名效果不叠加（按 tag 去重）。
"""
from __future__ import annotations

import re

PREFIXES = ("fx:team_reduce:",)

_TYPES = {"grass", "fire", "water", "lightning", "psychic", "fighting",
          "darkness", "metal", "colorless", "dragon"}
_TARGETS = {"basic", "energy", "faction", "any"}


def _name_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(name or "").lower())


def _side_of(battle, fp) -> int | None:
    for i, p in enumerate(battle.players):
        if fp is p.active or any(fp is b for b in p.bench):
            return i
    return None


def _parse_tag(tag: str) -> dict:
    parts = str(tag).split(":")
    if len(parts) < 5 or parts[0] != "fx" or parts[1] != "team_reduce":
        raise ValueError(f"非法 team_reduce tag：{tag}")
    if not parts[2].isdigit():
        raise ValueError(f"team_reduce 缺少数值：{tag}")
    target = parts[3]
    arg = parts[4]
    if target not in _TARGETS or not arg:
        raise ValueError(f"未识别的 team_reduce 目标：{tag}")
    if target in ("basic", "energy") and arg not in _TYPES:
        raise ValueError(f"team_reduce 属性非法：{tag}")
    info = {"n": int(parts[2]), "target": target, "arg": arg,
            "other": None, "source_bench": False, "nonstack": False}
    i = 5
    while i < len(parts):
        k = parts[i]
        if k == "other" and i + 1 < len(parts) and parts[i + 1]:
            info["other"] = parts[i + 1]
            i += 2
        elif k == "source_bench":
            info["source_bench"] = True
            i += 1
        elif k == "nonstack":
            info["nonstack"] = True
            i += 1
        else:
            raise ValueError(f"未识别的 team_reduce 限定：{tag}")
    return info


def _target_ok(info: dict, defender) -> bool:
    if info["target"] == "any":
        return True
    if info["target"] == "basic":
        return (defender.pdef.stage == 0
                and str(defender.pdef.ptype).lower() == info["arg"])
    if info["target"] == "energy":
        return int((defender.energy or {}).get(info["arg"], 0)) > 0
    if info["target"] == "faction":
        return _name_key(defender.pdef.name).startswith(info["arg"])
    return False


def static_dmg_minus(battle, defender, attacker=None) -> int:
    """守方本次受到的**全队常驻减伤**（≥0）；无来源返回 0"""
    if defender is None:
        return 0
    side = _side_of(battle, defender)
    if side is None:
        return 0
    p = battle.players[side]
    field = [fp for fp in (p.active, *p.bench) if fp is not None]
    total = 0
    seen: set = set()
    for src in field:
        for ab in getattr(src.pdef, "abilities", ()):
            tag = str(getattr(ab, "tag", "") or "")
            if not tag.startswith(PREFIXES[0]):
                continue
            info = _parse_tag(tag)
            if not _target_ok(info, defender):
                continue
            if info["source_bench"] and not any(src is b for b in p.bench):
                continue                 # 「as long as this Pokémon is on your Bench」
            if info["other"] and not any(
                    fp is not src
                    and info["other"] in _name_key(fp.pdef.name)
                    for fp in field):
                continue                 # 「at least 1 other <name> in play」
            if info["nonstack"]:
                if tag in seen:
                    continue
                seen.add(tag)
            total += info["n"]
    return total
