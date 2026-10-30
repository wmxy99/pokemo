# -*- coding: utf-8 -*-
"""解析链 fx 族：静态伤害增减（S1 静态修正层 · 2026-10-27）

只认领**整段卡面可完整表达**的常驻特性句式：

  · ``Attacks used by this Pokémon do N more damage to your opponent's Active
    Pokémon (before applying Weakness and Resistance).``
  · ``Attacks used by your Pokémon …``（全队）
  · ``Attacks used by your {G} Pokémon and {R} Pokémon …``（属性子集）
  · ``Attacks used by your Evolution {R} Pokémon …``（进化 + 属性）
  · ``Attacks used by your Cynthia's / Hop's / Team Rocket's Pokémon …``
  · ``… that has an Ability`` / ``… Active Evolution Pokémon``（受击方限定）
  · ``If this Pokémon has 2 or more damage counters on it, …``（自身条件）
  · ``… for each Prize card your opponent has taken``（按已取奖赏缩放）
  · ``As long as this Pokémon is in the Active Spot, attacks used by your
    opponent's Active Pokémon do N less damage …``（压制对手战斗位）

**故意拒绝**以下近邻写法（返回 None，让整卡如实落未建模）：
  · ``Future Pokémon, except any Iron Crown {ex}`` —— 数据模型无 Future；
  · ``during this turn``（Feraligatr 的发动型加伤）—— 需要回合级 buff 状态；
  · ``During your opponent's next turn, attacks used by the Defending Pokémon
    do N less damage`` —— 临时减伤，应走跨回合 debuff 层，不是常驻静态；
  · ``The effect of X doesn't stack`` 只允许出现在**本族精确识别**的卡面里，
    其余附加条件一律不认领。

tag 形态（引擎侧 `engine/families/fx_static_dmg.py` 逐字解析）：
    fx:static_dmg:add:<n>:self
    fx:static_dmg:add:<n>:self_if_damage:<counters>
    fx:static_dmg:add:<n>:self_vs_ability
    fx:static_dmg:add:<n>:self_if_mega_type:<t>
    fx:static_dmg:add:<n>:self_per_prize
    fx:static_dmg:add:<n>:team
    fx:static_dmg:add:<n>:team_type:<t1,t2>
    fx:static_dmg:add:<n>:team_evolution_type:<t>
    fx:static_dmg:add:<n>:team_vs_evolution
    fx:static_dmg:add:<n>:team_name:<key>[:nonstack]
    fx:static_dmg:sub:<n>:opp_active
"""
from __future__ import annotations

import re

from . import normalize

PREFIXES = ("fx:static_dmg:",)

_TYPE = {"G": "grass", "R": "fire", "W": "water", "L": "lightning",
         "P": "psychic", "F": "fighting", "D": "darkness", "M": "metal",
         "C": "colorless", "N": "dragon", "Y": "psychic"}

_POKE = r"pok[eé]mon"
_TYPE_TOKEN = r"\{[a-z]\}"

# 加伤：整段认领（前缀条件 / 范围 / 受击方限定 / 按奖赏缩放 / 括号 / 不叠加）
_ADD_RE = re.compile(
    r"^(?:if this " + _POKE + r" has (?P<counters>\d+) or more damage counters "
    r"on it, )?"
    r"attacks used by (?P<scope>"
    r"this " + _POKE + r"|"
    r"your " + _POKE + r"|"
    r"your evolution (?P<evo_type>" + _TYPE_TOKEN + r") " + _POKE + r"|"
    r"your (?P<type1>" + _TYPE_TOKEN + r") " + _POKE + r""
    r"(?: and (?P<type2>" + _TYPE_TOKEN + r") " + _POKE + r")?|"
    r"your (?P<faction>[a-z][a-z .'-]*?)'s " + _POKE + r""
    r")"
    r" do (?P<n>\d+) more damage to your opponent's active (?:"
    r"(?P<target_ability>" + _POKE + r" that has an ability)|"
    r"(?P<target_evo>evolution " + _POKE + r")|"
    r"(?P<target_plain>" + _POKE + r")"
    r")"
    r"(?P<per> for each prize card your opponent has taken)?"
    r" \(before applying weakness and resistance\)\."
    r"(?P<nonstack> the effect of [^.]+ doesn't stack\.)?$"
)

# 加伤·条件式（2026-10-28 第十二批）：Seviper「Excited Power」
#   「If you have any {D} Mega Evolution Pokémon {ex} in play, attacks used by
#     this Pokémon do 120 more damage to your opponent's Active Pokémon
#     (before applying Weakness and Resistance).」
# 条件（己方场上有**该属性**的 Mega）可精确求值 → `self_if_mega_type:<t>`。
_ADD_IF_RE = re.compile(
    r"^if you have any (?P<type>" + _TYPE_TOKEN + r") mega evolution "
    + _POKE + r" \{ex\} in play, attacks used by this " + _POKE
    + r" do (?P<n>\d+) more damage to your opponent's active " + _POKE
    + r" \(before applying weakness and resistance\)\.$"
)

# 减伤：自身在战斗位时，压制对手战斗位的招式伤害（常驻，不是 next turn）
_SUB_RE = re.compile(
    r"^as long as this " + _POKE + r" is in the active spot, attacks used by "
    r"your opponent's active " + _POKE + r" do (?P<n>\d+) less damage "
    r"\(before applying weakness and resistance\)\.$"
)


def _type_of(token: str) -> str | None:
    if not token or len(token) != 3 or token[0] != "{" or token[2] != "}":
        return None
    return _TYPE.get(token[1].upper())


def _ascii(text: str) -> str:
    """匹配用：去掉 é 重音（卡面同时存在 ``Pokémon`` / ``Pokemon`` 写法）"""
    return str(text or "").replace("é", "e").replace("É", "E")


def _name_key(name: str) -> str:
    """卡名/阵营前缀 → tag 安全键（与引擎侧同规则：小写、只留 a-z0-9）"""
    return re.sub(r"[^a-z0-9]+", "", str(name or "").lower())


def _tag(kind: str, n: int, scope: str, arg: str | None = None,
         nonstack: bool = False) -> str:
    tag = f"fx:static_dmg:{kind}:{n}:{scope}"
    if arg:
        tag += f":{arg}"
    if nonstack:
        tag += ":nonstack"
    return tag


def _spec(tag: str) -> dict:
    return {
        "trigger": "static",
        "actions": [{
            "type": "fx", "target": None, "amount": None,
            "extra": {"tag": tag, "family": "static_dmg"},
        }],
        "family": "static_dmg",
    }


def detect(text: str, kind: str = "attack", name: str = "") -> dict | None:
    """返回 None（不是本族句式）或完整 spec。仅特性侧；只认整段。"""
    if kind != "ability":
        return None
    t = normalize(text)

    m = _SUB_RE.match(t)
    if m:
        return _spec(_tag("sub", int(m.group("n")), "opp_active"))

    m = _ADD_IF_RE.match(t)
    if m:
        ptype = _type_of(m.group("type"))
        if ptype is None:
            return None
        return _spec(_tag("add", int(m.group("n")), "self_if_mega_type", ptype))

    m = _ADD_RE.match(t)
    if not m:
        return None
    scope_txt = _ascii(m.group("scope"))
    n = int(m.group("n"))
    target = _ascii(m.group("target_ability") or m.group("target_evo") or "")
    per = bool(m.group("per"))
    counters = m.group("counters")
    nonstack = bool(m.group("nonstack"))

    # ---- 自身范围 ----
    if scope_txt == "this pokemon":
        if counters:
            if target or per:
                return None
            return _spec(_tag("add", n, "self_if_damage", counters))
        if target == "pokemon that has an ability":
            if per:
                return None
            return _spec(_tag("add", n, "self_vs_ability"))
        if target == "evolution pokemon":
            return None
        if per:
            return _spec(_tag("add", n, "self_per_prize"))
        return _spec(_tag("add", n, "self"))

    # ---- 阵营 / 属性 / 阶段 ----
    faction = m.group("faction")
    if faction:
        # "your opponent's …" 不能当成"你的某阵营"
        if faction.strip().startswith("opponent"):
            return None
        if target or per or counters:
            return None
        # faction 捕获到 "hop"/"cynthia"（撇号在组外）；补回所有格 s，
        # 否则 "Hop" 会把 Hoppip 等无关卡名也算进阵营。
        return _spec(_tag("add", n, "team_name", _name_key(faction + "s"),
                          nonstack=nonstack))

    evo_type = m.group("evo_type")
    if evo_type:
        ptype = _type_of(evo_type)
        if ptype is None or target or per or counters or nonstack:
            return None
        return _spec(_tag("add", n, "team_evolution_type", ptype))

    type1 = m.group("type1")
    type2 = m.group("type2")
    if type1:
        p1, p2 = _type_of(type1), (_type_of(type2) if type2 else None)
        if p1 is None or (type2 and p2 is None):
            return None
        if target or per or counters or nonstack:
            return None
        types = ",".join(dict.fromkeys(x for x in (p1, p2) if x))
        return _spec(_tag("add", n, "team_type", types))

    # ---- 全队 ----
    if target:
        if target != "evolution pokemon" or per or counters or nonstack:
            return None
        return _spec(_tag("add", n, "team_vs_evolution"))
    if per or counters or nonstack:
        return None
    return _spec(_tag("add", n, "team"))
