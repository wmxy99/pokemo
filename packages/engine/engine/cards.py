# -*- coding: utf-8 -*-
"""PokéLab Engine · 卡牌模型（engine-native 定义层）

设计决策：
  - 引擎不直接解释 F8 的 effects_json（LLM 产物有解析缺陷风险），改用**程序化卡牌定义**，
    effects_json 仅作溯源旁证 —— 保证结算确定性（对应 TS v1.0「效果数据驱动 + 灰名单」）。
  - v1 简化子集：能量不分颜色（attached_energy 仅计数）；弱/抗按宝可梦 primary_type 判定；
    招式效果用 tag 表达（poison/burn/sleep/paralyze/confuse/spread/heal/self/draw）。
    未支持项（进化/特性/训练家牌）显式灰名单，见 README。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class AttackDef:
    """招式定义"""
    name: str
    cost: int                      # 所需能量数（v1 不分颜色，仅计数）
    damage: int = 0                # 主目标基础伤害
    effects: tuple = ()            # tag: "poison"/"burn"/"sleep"/"paralyze"/"confuse"/
                                   #      "spread:<n>:<per>"(n只后备各per伤)/
                                   #      "heal:<n>"(自愈) / "self:<n>"(反伤) / "draw:<k>"
    desc: str = ""


@dataclass(frozen=True)
class PokemonDef:
    """宝可梦物种定义（引擎原生；非官方实卡数据时 name 用自造代号）"""
    name: str
    hp: int
    ptype: str                     # primary type: grass/fire/water/lightning/psychic/
                                   #               fighting/darkness/metal/dragon/colorless
    weakness: Optional[str] = None # 弱点类型（伤害 ×2）
    resistance: Optional[str] = None  # 抗性类型（伤害 −30，最低 0）
    retreat: int = 1
    attacks: tuple = ()
    is_ex: bool = False            # ex 被击倒对手拿 2 奖赏
    desc: str = ""


@dataclass(frozen=True)
class EnergyDef:
    """能量牌（v1：通用能量，不分颜色）"""
    name: str = "Basic Energy"


# ---- 字段卡类型别名：牌库/手牌中的牌引用 ----
@dataclass
class PokeRef:
    """手牌/牌库中的宝可梦牌引用（上场时实例化为 FieldPokemon）"""
    pdef: PokemonDef


@dataclass
class EnergyRef:
    """手牌/牌库中的能量牌引用"""
    edef: EnergyDef = field(default_factory=EnergyDef)


CardRef = PokeRef | EnergyRef


@dataclass
class FieldPokemon:
    """场上的宝可梦实例（每张上场的牌一个实例）"""
    pdef: PokemonDef
    damage: int = 0
    energy: int = 0                    # 已附着能量数（v1 不分颜色）
    status: set = field(default_factory=set)  # subset of {poison,burn,sleep,paralyze,confuse}

    @property
    def name(self) -> str:
        return self.pdef.name

    @property
    def max_hp(self) -> int:
        return self.pdef.hp

    @property
    def hp_remaining(self) -> int:
        return self.max_hp - self.damage

    @property
    def is_ko(self) -> bool:
        return self.damage >= self.max_hp

    def clear_status(self) -> None:
        self.status.clear()

    def summary(self) -> dict:
        return {
            "name": self.name, "hp": self.max_hp, "dmg": self.damage,
            "energy": self.energy, "status": sorted(self.status),
        }


def make_pokemon(pdef: PokemonDef) -> FieldPokemon:
    return FieldPokemon(pdef=pdef)


# ---- 常用判定 ----
BASIC = 0
