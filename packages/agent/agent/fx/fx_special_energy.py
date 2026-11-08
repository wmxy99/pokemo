# -*- coding: utf-8 -*-
"""解析链 fx 族：**特殊能量**（2026-10-28）

12 张特殊能量卡 → 12 个 `senergy:<kind>` 种类（整段**逐字**认领）。

为什么整段认领 + 逐字匹配
------------------------
特殊能量的规则几乎全部写在「As long as …」之后：提供量随持有者变化、附着触发、
受击反应、奖赏卡修正、回合结束弃置。只认第一段（提供 {C}）会把后面整段规则
**静默丢掉**，卡进池后就是一个"普通能量"——比未建模危险得多。
因此这里对每张卡写**完整**的归一化文本匹配：多一句、少一句都不认。

**S2d 补记**：`Boomerang Energy` 原本故意不认（"能量卡身份 + 攻击后从弃牌堆回附"
当时无法表达）。S2d 之后有了两件东西 —— `_discard_energy_from`（弃能量时特殊能量
**卡**进 `discard_refs`）与 `attack_done` 的差分快照（`discarded`）—— 于是整段
可以精确表达，改为认领。

tag 由 `engine/special_energy.py` 逐字解析；未知 kind 会抛错。
"""
from __future__ import annotations

import re

from . import normalize

PREFIXES = ("senergy:",)

# 归一化（小写、弯引号、é→e、空白折叠）后的**整段**文本 → kind
_PATTERNS = {
    "as long as this card is attached to a pokemon, it provides {c} energy. "
    "if this card is attached to a stage 2 pokemon, this card provides every "
    "type of energy but provides only 2 energy at a time.": "neo_upper",

    "as long as this card is attached to a pokemon, it provides {c} energy. "
    "prevent all effects of attacks used by your opponent's pokemon done to "
    "the pokemon this card is attached to. (existing effects are not removed. "
    "damage is not an effect.)": "mist",

    "as long as this card is attached to a pokemon, it provides every type of "
    "energy but provides only 1 energy at a time. if the pokemon this card is "
    "attached to is knocked out by damage from an attack from your opponent's "
    "pokemon, that player takes 1 fewer prize card. this effect of your legacy "
    "energy only works once per game.": "legacy",

    "as long as this card is attached to a pokemon, it provides {c} energy. "
    "when you attach this card from your hand to a pokemon, draw 4 cards.":
        "enriching",

    "as long as this card is attached to a pokemon, it provides {c} energy. "
    "if the pokemon this card is attached to is in the active spot and is "
    "damaged by an attack from your opponent's pokemon (even if this pokemon "
    "is knocked out), put 2 damage counters on the attacking pokemon.": "spiky",

    "this card can only be attached to a team rocket's pokemon. if this card "
    "is attached to anything other than a team rocket's pokemon, discard this "
    "card. as long as this card is attached to a pokemon, it provides 2 in any "
    "combination of {p} energy and {d} energy.": "team_rocket",

    "as long as this card is attached to a pokemon, it provides {c} energy. "
    "if this card is attached to a basic pokemon, this card provides every "
    "type of energy but provides only 1 energy at a time.": "prism",

    "if this card is attached to 1 of your pokemon, discard it at the end of "
    "your turn. as long as this card is attached to a pokemon, it provides "
    "{c} energy. if this card is attached to an evolution pokemon, it provides "
    "{c}{c}{c} energy instead.": "ignition",

    "as long as this card is attached to a pokemon, it provides {g} energy. "
    "the {g} pokemon this card is attached to gets +20 hp.": "grow_grass",

    "as long as this card is attached to a pokemon, it provides {p} energy. "
    "when you attach this card from your hand to a {p} pokemon, search your "
    "deck for up to 2 basic {p} pokemon and put them onto your bench. then, "
    "shuffle your deck.": "telepath",

    "as long as this card is attached to a pokemon, it provides {f} energy. "
    "prevent all effects of attacks used by your opponent's pokemon done to "
    "the {f} pokemon this card is attached to. (existing effects are not "
    "removed. damage is not an effect.)": "rock_fighting",

    # S2d：被**持有者自己的招式效果**弃置时，攻击结束后从弃牌堆回附
    "as long as this card is attached to a pokemon, it provides {c} energy. "
    "if this card is discarded by an effect of an attack used by the pokemon "
    "this card is attached to, attach this card from your discard pile to that "
    "pokemon after attacking.": "boomerang",
}

# 已知但**未建模**的特殊能量文本 → 明确拒绝（写出来是为了让"不建模"可被检索）
# S2d（2026-11-08）：`boomerang` 已建模（攻击结束差分 + 卡对象身份），移入
# `_PATTERNS`。表保留为空 —— 以后再出现"认得但做不到"的特殊能量放这里。
_UNMODELED: dict = {}


def detect(text: str, kind: str = "attack", name: str = "") -> dict | None:
    """返回 None（不是本族句式 / 未建模）或完整 spec。仅能量卡；只认整段。"""
    if kind != "energy":
        return None
    t = normalize(text).replace("é", "e")
    t = re.sub(r"\s+", " ", t).strip()
    key = _PATTERNS.get(t)
    if key is None:
        return None
    tag = f"senergy:{key}"
    return {
        "trigger": "static",
        "actions": [{
            "type": "fx", "target": None, "amount": None,
            "extra": {"tag": tag, "family": "special_energy"},
        }],
        "family": "special_energy",
    }


def unmodeled_kind(text: str) -> str:
    """该文本是否为「已知但未建模」的特殊能量（供测试/审计使用）"""
    t = normalize(text).replace("é", "e")
    return _UNMODELED.get(re.sub(r"\s+", " ", t).strip(), "")
