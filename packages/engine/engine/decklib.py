# -*- coding: utf-8 -*-
"""PokéLab Engine · 示例卡组库（engine-native 定义；原创代名，规避第三方 IP）

v1 简化：卡组仅含宝可梦 + 通用能量（训练家/支援者/特性 = 灰名单）。
"""
from __future__ import annotations
from .cards import (PokemonDef, AttackDef, PokeRef, EnergyRef, EnergyDef,
                    CardRef)

_E = EnergyDef()  # v1 通用能量牌模板

# ---------- 招式 ----------
T1 = AttackDef("Spark Punch", cost=1, damage=30, desc="30")
T2 = AttackDef("Thunderbolt", cost=3, damage=90, desc="90")
T3 = AttackDef("Raid Volt", cost=2, damage=60,
               effects=("spread:1:20",), desc="60 + 对手1只后备20")
T4 = AttackDef("Leaf Cutter", cost=1, damage=40, desc="40")
T5 = AttackDef("Solar Beam", cost=3, damage=100, desc="100")
T6 = AttackDef("Ember", cost=1, damage=30, effects=("burn",), desc="30+烧伤")
T7 = AttackDef("Inferno", cost=3, damage=110,
               effects=("self:20",), desc="110+自身20")
T8 = AttackDef("Toxic Bite", cost=2, damage=40, effects=("poison",), desc="40+中毒")
T9 = AttackDef("Sleep Powder", cost=2, damage=20, effects=("sleep",), desc="20+睡眠")
T10 = AttackDef("Psy Bolt", cost=2, damage=50, effects=("confuse",), desc="50+混乱")
T11 = AttackDef("Paralyze Fang", cost=2, damage=40,
                effects=("paralyze",), desc="40+麻痹")
T12 = AttackDef("Drain Fang", cost=2, damage=50, effects=("heal:20",), desc="50+自愈20")
T13 = AttackDef("Draw Surge", cost=1, damage=10,
                effects=("draw:2",), desc="10+抽2")
T14 = AttackDef("Double Claw", cost=2, damage=60, desc="60")

# ---------- 宝可梦 ----------
BOLT = PokemonDef("Bolt", hp=70, ptype="lightning", weakness="fighting",
                  attacks=(T1, T2), desc="demo 基础雷系")
THUNDREX = PokemonDef("Thundrex ex", hp=180, ptype="lightning", weakness="fighting",
                      retreat=1, attacks=(T3,), is_ex=True, desc="demo ex 主攻手")
SEEDLING = PokemonDef("Seedling", hp=60, ptype="grass", weakness="fire",
                      resistance="water", attacks=(T4,))
SUNFLORA = PokemonDef("Sunflora", hp=120, ptype="grass", weakness="fire",
                      resistance="water", attacks=(T5,))
EMBER = PokemonDef("Emberling", hp=70, ptype="fire", weakness="water",
                   attacks=(T6,))
FLAREX = PokemonDef("Flarex ex", hp=190, ptype="fire", weakness="water",
                    retreat=2, attacks=(T7,), is_ex=True)
TOXIBUG = PokemonDef("Toxibug", hp=80, ptype="psychic", weakness="darkness",
                     attacks=(T8,))
DREAMOTH = PokemonDef("Dreamoth", hp=90, ptype="psychic", weakness="darkness",
                      attacks=(T9, T10))
FANGPUPPY = PokemonDef("Fangpuppy", hp=90, ptype="fighting", weakness="psychic",
                       attacks=(T11, T12))
SPARKIT = PokemonDef("Sparkit", hp=60, ptype="lightning", weakness="fighting",
                     attacks=(T13,))
CLAWMON = PokemonDef("Clawmon", hp=100, ptype="colorless", weakness="fighting",
                     attacks=(T14,))


def _deck(*items: tuple[object, int]) -> list:
    """[(卡def, 数量), ...] → 60 张 CardRef 列表"""
    out: list[CardRef] = []
    for item, n in items:
        out.extend([_ref(item) for _ in range(n)])
    return out


def _ref(item):
    return PokeRef(item) if isinstance(item, PokemonDef) else EnergyRef()


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


ALL_DECKS = {
    "lightning": deck_lightning_demo,
    "grass": deck_grass_demo,
    "fire": deck_fire_demo,
    "psychic": deck_psychic_demo,
    "status": deck_status_demo,
}


def make_deck(name: str) -> list:
    return ALL_DECKS[name]()
