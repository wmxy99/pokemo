# -*- coding: utf-8 -*-
"""PokéLab Engine · 卡牌模型（engine-native 定义层）· v0.2

v0.2 升级（E1/E2/E4）：
  - E1 彩色能量：FieldPokemon.energy 改分色 dict；AttackDef.cost 支持彩色（int 自动转无色）；
    EnergyDef 带 type；撤退/干扰扣能按无色优先。
  - E2 进化：PokemonDef 增 stage / evolves_from；场上实例保留能量与伤害、清除特殊状态。
  - E4 训练家最小集：TrainerRef(kind=item/supporter, tag=...)；supporter 每回合一次。

设计决策不变：引擎不解释 F8 effects_json，全部程序化定义；效果 tag 体系沿用。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

COLORLESS = "colorless"
TYPES = ("grass", "fire", "water", "lightning", "psychic", "fighting",
         "darkness", "metal", "dragon", "colorless")

EnergyCost = tuple[tuple[str, int], ...]


def _norm_cost(cost) -> EnergyCost:
    """cost: int（无色 n）或 [(type,n), ...] → 归一化 EnergyCost"""
    if isinstance(cost, int):
        return ((COLORLESS, cost),) if cost > 0 else ()
    return tuple((t, int(n)) for t, n in cost)


@dataclass(frozen=True)
class AttackDef:
    """招式定义"""
    name: str
    cost: object = 1                # int=无色数；或 [(type,n),...] 彩色需求
    damage: int = 0
    effects: tuple = ()
    desc: str = ""

    def __post_init__(self):
        object.__setattr__(self, "cost", _norm_cost(self.cost))

    @property
    def cost_total(self) -> int:
        return sum(n for _, n in self.cost)


@dataclass(frozen=True)
class AbilityDef:
    """特性定义（E3 最小机制）

    location:
      field —— 场上发动、每只每回合一次（ability_used 标记，回合开始重置）
      hand  —— 手牌发动（如 Dusknoir 弃自身），用后弃掉该牌，天然每张一次
    tag（引擎原生派发，v0.3 首批）：
      "recon:<n>"       检视牌库顶 n 取 1 入手，其余放回牌库底
      "cursed:<hp>"     弃自身 → 对指定 1 只对手宝可梦放 <hp>/10 个伤害指示物
    """
    name: str
    tag: str = "recon:2"
    location: str = "field"       # field | hand
    desc: str = ""


@dataclass(frozen=True)
class PokemonDef:
    """宝可梦物种定义"""
    name: str
    hp: int
    ptype: str
    weakness: Optional[str] = None
    resistance: Optional[str] = None
    retreat: int = 1
    attacks: tuple = ()
    is_ex: bool = False
    stage: int = 0                    # E2: 0=基础 1=一阶 2=二阶 ...
    evolves_from: Optional[str] = None  # E2: 由谁进化而来
    abilities: tuple = ()             # E3: tuple[AbilityDef]
    desc: str = ""


@dataclass(frozen=True)
class EnergyDef:
    """能量牌（v0.2 支持彩色）"""
    type: str = COLORLESS
    name: str = ""                    # 展示用；默认按 type 生成

    def __post_init__(self):
        if not self.name:
            object.__setattr__(self, "name",
                               f"{self.type.capitalize()} Energy")


@dataclass(frozen=True)
class TrainerDef:
    """训练家牌定义（E4 最小集，行为由 tag 派发）"""
    tag: str                          # poffin / ultra_ball / hammer / boss ...
    kind: str = "item"                # item | supporter
    name: str = ""

    def __post_init__(self):
        if not self.name:
            object.__setattr__(self, "name", self.tag.replace("_", " ").title())


# ---- 手牌/牌库牌引用 ----
@dataclass
class PokeRef:
    pdef: PokemonDef


@dataclass
class EnergyRef:
    edef: EnergyDef = field(default_factory=lambda: EnergyDef())


@dataclass
class TrainerRef:
    tdef: TrainerDef


CardRef = PokeRef | EnergyRef | TrainerRef


@dataclass
class FieldPokemon:
    """场上宝可梦实例"""
    pdef: PokemonDef
    damage: int = 0
    energy: dict = field(default_factory=dict)   # E1: {type: count}
    status: set = field(default_factory=set)
    just_played: bool = False                    # E2: 本回合刚放置/进化（禁再进化）
    ability_used: set = field(default_factory=set)  # E3: 本回合已发动的 field 特性名

    # ---- 基本属性 ----
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

    @property
    def stage(self) -> int:
        return self.pdef.stage

    def clear_status(self) -> None:
        self.status.clear()

    # ---- E1 能量操作 ----
    def total_energy(self) -> int:
        return sum(self.energy.values())

    def energy_of(self, t: str) -> int:
        return self.energy.get(t, 0)

    def add_energy(self, t: str, n: int = 1) -> None:
        self.energy[t] = self.energy.get(t, 0) + n

    def can_pay(self, cost: EnergyCost) -> bool:
        """能否支付招式能量需求（不消耗能量——攻击不弃能）"""
        for t, n in cost:
            if self.energy_of(t) < n:
                return False
        return True

    def remove_any(self, n: int) -> bool:
        """移除 n 点能量（撤退/干扰用）：无色优先，其次任意颜色"""
        if self.total_energy() < n:
            return False
        # 先无色
        c = self.energy.get(COLORLESS, 0)
        take_c = min(c, n)
        if take_c:
            self.energy[COLORLESS] = c - take_c
            n -= take_c
        # 其余按任意类型扣除
        for t in list(self.energy):
            if n <= 0:
                break
            if t == COLORLESS:
                continue
            k = min(self.energy[t], n)
            self.energy[t] -= k
            n -= k
            if self.energy[t] == 0:
                del self.energy[t]
        if self.energy.get(COLORLESS, 0) == 0:
            self.energy.pop(COLORLESS, None)
        return True

    def evolve_to(self, ndef: PokemonDef) -> None:
        """E2 进化：替换物种；保留能量与伤害，清除状态"""
        self.pdef = ndef
        self.status.clear()
        self.just_played = True

    def summary(self) -> dict:
        return {
            "name": self.name, "hp": self.max_hp, "dmg": self.damage,
            "energy": self.total_energy(), "energy_map": dict(self.energy),
            "status": sorted(self.status), "stage": self.stage,
        }


def make_pokemon(pdef: PokemonDef) -> FieldPokemon:
    return FieldPokemon(pdef=pdef)
