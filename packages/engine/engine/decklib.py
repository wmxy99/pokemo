# -*- coding: utf-8 -*-
"""PokéLab Engine · 示例卡组库（engine-native 定义；原创代名，规避第三方 IP）

v0.2：新增 Y 版 Draco 进化线（对应 Dragapult 原型，E2 进化 + E1 彩色能量演示）
      与训练家最小集（E4：poffin/ultra_ball/hammer/boss）。
v1 简化注记：特性机制（Recon Directive / Cursed Blast 等）待引擎 E3 批次实现，
Y 版近似攻击型先行。
"""
from __future__ import annotations
from .cards import (PokemonDef, AttackDef, PokeRef, EnergyRef, EnergyDef,
                    TrainerRef, TrainerDef, AbilityDef, CardRef)

_E = EnergyDef(name_zh="基本能量")   # v1 通用无色能量
_EF = EnergyDef("fire", name_zh="基本火能量")
_EP = EnergyDef("psychic", name_zh="基本超能量")

# ---------- 招式 ----------
T1 = AttackDef("Spark Punch", name_zh="电击拳", cost=1, damage=30, desc="30")
T2 = AttackDef("Thunderbolt", name_zh="十万伏特", cost=3, damage=90, desc="90")
T3 = AttackDef("Raid Volt", name_zh="突袭伏特", cost=2, damage=60,
               effects=("spread:1:20",), desc="60 + 对手1只后备20")
T4 = AttackDef("Leaf Cutter", name_zh="叶刃", cost=1, damage=40, desc="40")
T5 = AttackDef("Solar Beam", name_zh="日光束", cost=3, damage=100, desc="100")
T6 = AttackDef("Ember", name_zh="火花", cost=1, damage=30, effects=("burn",), desc="30+烧伤")
T7 = AttackDef("Inferno", name_zh="炼狱", cost=3, damage=110,
               effects=("self:20",), desc="110+自身20")
T8 = AttackDef("Toxic Bite", name_zh="毒咬", cost=2, damage=40, effects=("poison",), desc="40+中毒")
T9 = AttackDef("Sleep Powder", name_zh="催眠粉", cost=2, damage=20, effects=("sleep",), desc="20+睡眠")
T10 = AttackDef("Psy Bolt", name_zh="精神弹", cost=2, damage=50, effects=("confuse",), desc="50+混乱")
T11 = AttackDef("Paralyze Fang", name_zh="麻痹牙", cost=2, damage=40,
                effects=("paralyze",), desc="40+麻痹")
T12 = AttackDef("Drain Fang", name_zh="吸血牙", cost=2, damage=50, effects=("heal:20",), desc="50+自愈20")
T13 = AttackDef("Draw Surge", name_zh="抽牌涌流", cost=1, damage=10,
                effects=("draw:2",), desc="10+抽2")
T14 = AttackDef("Double Claw", name_zh="双重爪", cost=2, damage=60, desc="60")

# ---------- 宝可梦 ----------
BOLT = PokemonDef("Bolt", name_zh="电光鼠", hp=70, ptype="lightning", weakness="fighting",
                  attacks=(T1, T2), desc="demo 基础雷系")
THUNDREX = PokemonDef("Thundrex ex", name_zh="雷克斯ex", hp=180, ptype="lightning", weakness="fighting",
                      retreat=1, attacks=(T3,), is_ex=True, desc="demo ex 主攻手")
SEEDLING = PokemonDef("Seedling", name_zh="幼芽", hp=60, ptype="grass", weakness="fire",
                      resistance="water", attacks=(T4,))
SUNFLORA = PokemonDef("Sunflora", name_zh="向日花", hp=120, ptype="grass", weakness="fire",
                      resistance="water", attacks=(T5,))
EMBER = PokemonDef("Emberling", name_zh="火苗兽", hp=70, ptype="fire", weakness="water",
                   attacks=(T6,))
FLAREX = PokemonDef("Flarex ex", name_zh="焰克斯ex", hp=190, ptype="fire", weakness="water",
                    retreat=2, attacks=(T7,), is_ex=True)
TOXIBUG = PokemonDef("Toxibug", name_zh="毒虫", hp=80, ptype="psychic", weakness="darkness",
                     attacks=(T8,))
DREAMOTH = PokemonDef("Dreamoth", name_zh="梦蛾", hp=90, ptype="psychic", weakness="darkness",
                      attacks=(T9, T10))
FANGPUPPY = PokemonDef("Fangpuppy", name_zh="牙犬", hp=90, ptype="fighting", weakness="psychic",
                       attacks=(T11, T12))
SPARKIT = PokemonDef("Sparkit", name_zh="电花", hp=60, ptype="lightning", weakness="fighting",
                     attacks=(T13,))
CLAWMON = PokemonDef("Clawmon", name_zh="爪兽", hp=100, ptype="colorless", weakness="fighting",
                     attacks=(T14,))


def _deck(*items: tuple[object, int]) -> list:
    """[(卡def, 数量), ...] → 60 张 CardRef 列表"""
    out: list[CardRef] = []
    for item, n in items:
        out.extend([_ref(item) for _ in range(n)])
    return out


def _ref(item):
    if isinstance(item, PokemonDef):
        return PokeRef(item)
    if isinstance(item, EnergyDef):
        return EnergyRef(item)
    if isinstance(item, TrainerDef):
        return TrainerRef(item)
    raise TypeError(f"未知卡类型 {type(item)}")


# ---------- v0.2 · Y 版 Draco 进化线（≈ Dragapult 原型，P0-A） ----------
D1 = AttackDef("Tackle", name_zh="撞击", cost=1, damage=20, desc="20")
D2 = AttackDef("Slam", name_zh="猛撞", cost=2, damage=60, desc="60")
D3 = AttackDef("Phantom Rush", name_zh="幻影突袭", cost=(("fire", 1), ("psychic", 1)), damage=200,
               effects=("spread_free:60",),
               desc="200 + 对手后备共 60 点(6指示物) 任意分配")
RECON_AB = AbilityDef("Recon Scout", name_zh="侦察哨戒", tag="recon:2", location="field",
                      desc="每回合一次：检视牌库顶2取1入手（≈Recon Directive）")
CURSED_AB = AbilityDef("Cursed Blast", name_zh="诅咒爆破", tag="cursed:130", location="hand",
                       desc="手牌弃此卡 → 对手1只宝可梦放13个指示物（≈Cursed Blast）")
DRAKLING = PokemonDef("Drakling", name_zh="幼龙", hp=60, ptype="dragon", weakness="darkness",
                      attacks=(D1,), stage=0, desc="Y版 基础（≈Dreepy）")
DRACO = PokemonDef("Draco", name_zh="龙兽", hp=120, ptype="dragon", weakness="darkness",
                   attacks=(D2,), stage=1, evolves_from="Drakling",
                   abilities=(RECON_AB,), desc="Y版 一阶（≈Drakloak，特性已实现 E3）")
DRACO_EX = PokemonDef("Draco ex", name_zh="龙兽ex", hp=200, ptype="dragon", weakness="darkness",
                      retreat=1, attacks=(D3,), is_ex=True, stage=2,
                      evolves_from="Draco", desc="Y版 二阶ex（≈Dragapult ex）")
SPECTOR = PokemonDef("Spector", name_zh="幽灵客", hp=110, ptype="psychic", weakness="darkness",
                     attacks=(AttackDef("Shadow Punch", name_zh="暗影拳", cost=1, damage=30),
                              ),
                     abilities=(CURSED_AB,),
                     desc="Y版 手牌特性组件（≈Dusknoir，E3）")
# E4 训练家（Y 版首批）
T_POFFIN = TrainerDef("poffin", kind="item", name="Buddy Ball", name_zh="伙伴球")
T_UBALL = TrainerDef("ultra_ball", kind="item", name="Hyper Ball", name_zh="超级球")
T_HAMMER = TrainerDef("hammer", kind="item", name="Energy Crush", name_zh="能量粉碎")
T_BOSS = TrainerDef("boss", kind="supporter", name="Orders", name_zh="指令")

# ---------- 原 demo 卡组 ----------
def deck_lightning_demo() -> list:
    """雷电 demo：低费快速，验证基础闭环"""
    return _deck((BOLT, 12), (THUNDREX, 6), (SPARKIT, 10), (_E, 32))


def deck_grass_demo() -> list:
    return _deck((SEEDLING, 14), (SUNFLORA, 8), (_E, 38))


def deck_fire_demo() -> list:
    return _deck((EMBER, 12), (FLAREX, 8), (_E, 40))


def deck_psychic_demo() -> list:
    return _deck((TOXIBUG, 12), (DREAMOTH, 8), (_E, 40))


def deck_status_demo() -> list:
    """状态流：毒/睡/麻/混演示"""
    return _deck((TOXIBUG, 8), (DREAMOTH, 8), (FANGPUPPY, 6), (_E, 38))


def deck_draco_demo() -> list:
    """Y 版 Draco ex 卡组（≈ A1 Dragapult 映射版）：进化线 + 训练家 + 彩色能量

    60 张 = 宝可梦 30（14 基础/10 一阶/6 二阶ex）+ 训练家 8 + 能量 22（火12/超10）
    """
    return _deck((DRAKLING, 14), (DRACO, 10), (DRACO_EX, 6),
                 (T_POFFIN, 3), (T_UBALL, 2), (T_HAMMER, 2), (T_BOSS, 1),
                 (_EF, 12), (_EP, 10))


def deck_ability_demo() -> list:
    """E3 特性演示：Draco(场上 Recon) + Spector(手牌 Cursed) 组件"""
    return _deck((DRAKLING, 8), (DRACO, 6), (SPECTOR, 6), (_E, 40))


ALL_DECKS = {
    "lightning": deck_lightning_demo,
    "grass": deck_grass_demo,
    "fire": deck_fire_demo,
    "psychic": deck_psychic_demo,
    "status": deck_status_demo,
    "draco": deck_draco_demo,
    "ability": deck_ability_demo,
}


# ---------- M1 · X 版真实卡池预设对手池 A1–A4 ----------
# 真实卡名 + 60 卡卡表定义在 engine/data/{cards_real,decklists}.json，
# 由 reallib 组装（引擎不支持的卡以基础能量占位，逐卡记录于 reallib.coverage）。
def _real_deck(arch_id: str):
    def _build() -> list:
        from . import reallib
        return reallib.build_deck(arch_id)
    return _build


for _aid in ("A1", "A2", "A3", "A4"):
    ALL_DECKS[_aid] = _real_deck(_aid)
    ALL_DECKS[_aid.lower()] = ALL_DECKS[_aid]


# 卡组中文展示名（前端/报告优先展示；英文 id 保持不变作为数据键）
DECK_LABELS_ZH = {
    "lightning": "雷电演示卡组",
    "grass": "草系演示卡组",
    "fire": "火系演示卡组",
    "psychic": "超能演示卡组",
    "status": "状态流演示卡组",
    "draco": "龙系进化演示卡组",
    "ability": "特性演示卡组",
    "A1": "A1 · 多龙巴鲁托ex / 黑夜魔灵",
    "A2": "A2 · 多龙巴鲁托ex / 火焰鸡ex",
    "A3": "A3 · N的索罗亚克ex",
    "A4": "A4 · 基础盒（厄诡椪ex / 皮皮ex）",
}


def deck_label_zh(name: str) -> str:
    """卡组中文展示名（无法识别时回退原名）"""
    key = (name or "").strip()
    if key in DECK_LABELS_ZH:
        return DECK_LABELS_ZH[key]
    if key.upper() in DECK_LABELS_ZH:
        return DECK_LABELS_ZH[key.upper()]
    try:
        from . import reallib
        a = reallib._arch_raw(key)          # noqa: SLF001
        return f"{a['id']} · {a.get('label_zh') or a['label']}"
    except Exception:
        return key


def archetype_ids() -> list:
    """可在 make_deck 中直接使用的真实预设 id（A1–A4）"""
    from . import reallib
    return reallib.archetype_ids()


def resolve_deck_id(name: str) -> str:
    """卡组名 → 数据键（支持中文展示名：'A1 · 多龙巴鲁托ex / 黑夜魔灵' → 'A1'）

    解析顺序：规范名（大小写回正，'a1' → 'A1'）→ 原始键（'lightning'）→
    中文展示名（DECK_LABELS_ZH / reallib.archetypes 的 label · label_en）。
    """
    key = (name or "").strip()
    if key.upper() in ALL_DECKS:          # 规范名优先（a1/A1 统一回 'A1'）
        return key.upper()
    if key in ALL_DECKS:
        return key
    for did, zh in DECK_LABELS_ZH.items():
        if key == zh or key.startswith(zh) or zh in key:
            return did
    try:
        from . import reallib
        for a in reallib.archetypes():
            if key == a["label"] or key == a.get("label_en") or a["label"] in key:
                return a["id"]
    except Exception:                                # noqa: BLE001
        pass
    return key


def make_deck(name: str) -> list:
    did = resolve_deck_id(name)
    if did not in ALL_DECKS:
        raise KeyError(f"未知卡组 {name}（可用：{'/'.join(sorted(ALL_DECKS))}；"
                       f"亦支持中文卡组名）")
    return ALL_DECKS[did]()
