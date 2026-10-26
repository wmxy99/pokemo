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
    desc: str = ""                  # 英文/旧字段（兼容保留）
    name_zh: str = ""               # 中文展示名（i18n：英文为数据键，中文用于前端/日志）
    desc_zh: str = ""               # 中文效果文本（自述转写；前端/报告优先展示）

    def __post_init__(self):
        object.__setattr__(self, "cost", _norm_cost(self.cost))

    @property
    def display_desc(self) -> str:
        """效果文本展示：中文优先，缺省回退英文"""
        return self.desc_zh or self.desc

    @property
    def cost_total(self) -> int:
        return sum(n for _, n in self.cost)


@dataclass(frozen=True)
class AbilityDef:
    """特性定义（E3 最小机制）

    location:
      field —— 场上发动、每只每回合一次（ability_used 标记，回合开始重置）
      hand  —— 手牌发动（如 Dusknoir 弃自身），用后弃掉该牌，天然每张一次
    tag（引擎原生派发，v1.3 全集）：
      "recon:<n>"             检视牌库顶 n 取 1 入手，其余放回牌库底
      "cursed:<hp>"           弃自身 → 对指定 1 只对手宝可梦放 <hp>/10 个伤害指示物
      "draw:<n>"              每回合一次抽 n（field）
      "move_damage:<n>"       每回合一次移动至多 n 个伤害指示物（自身 → 对手 active）
      "attach_energy:<n>[:<type>]"  每回合一次从弃牌堆/牌库附 n 个（指定色）能量
      "ko_draw:<n>"           己方宝可梦被击倒时抽 n（**被动**，不进合法动作表）
      "weak_change:<type>[:<filter>]"  在场时改写对手弱点（**被动**）
      "unsupported"           引擎未建模（灰名单；招式仍可用）
    """
    name: str
    tag: str = "recon:2"
    location: str = "field"       # field | hand
    desc: str = ""                # 兼容保留（历史数据此字段为中文）
    name_zh: str = ""             # 中文展示名
    desc_zh: str = ""             # 中文效果文本（自述转写）

    @property
    def display_desc(self) -> str:
        """效果文本展示：中文优先（desc_zh → desc）"""
        return self.desc_zh or self.desc


# 卡定义注册表（名称 → PokemonDef）：用于按名取中文展示名（如进化链来源）
POKEMON_REGISTRY: dict = {}


def pokemon_def(name):
    """按英文名查已定义的宝可梦（未登记 → None）"""
    return POKEMON_REGISTRY.get(name) if name else None


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
    name_zh: str = ""                 # 中文展示名

    def __post_init__(self):
        POKEMON_REGISTRY.setdefault(self.name, self)

    @property
    def display(self) -> str:
        """展示名：优先中文，缺省回退英文"""
        return self.name_zh or self.name


@dataclass(frozen=True)
class EnergyDef:
    """能量牌（v0.2 支持彩色）"""
    type: str = COLORLESS
    name: str = ""                    # 数据键；默认按 type 生成
    name_zh: str = ""                 # 中文展示名（如「基本火能量」）

    def __post_init__(self):
        if not self.name:
            object.__setattr__(self, "name",
                               f"{self.type.capitalize()} Energy")

    @property
    def display(self) -> str:
        return self.name_zh or self.name


@dataclass(frozen=True)
class TrainerDef:
    """训练家牌定义（E4 最小集 + v1.3 扩展，行为由 tag 派发）

    v1.3 tag 全集（与 battle._trainer_legals/_do_trainer 对齐）：
      "poffin"                     检索牌库 2 张基础宝可梦铺后备
      "ultra_ball"                 弃手牌 2 张 → 检索任意宝可梦入手
      "hammer"                     抛币正面 → 弃对手 active 1 能量
      "boss"                       将对手 1 只后备拉为 active（需 target）
      "research:<n>"               弃全部手牌 → 抽 n
      "shuffle_draw:<n>"           手牌洗回牌库 → 抽 n
      "switch_self"                自换位（不耗撤退能量、无视睡眠/麻痹；需 target）
      "recover:<n>[:<kind>]"       弃牌堆回收 <n> 张（kind=pokemon|energy|any）
      "discard_search_energy:<n>"  弃 1 张手牌 → 检索 n 张能量入手
      "search_energy:<n>"          检索 n 张能量入手
      "candy"                      跳阶进化（手牌二阶 → 链根基础）
      "unsupported"                引擎未建模（替换占位，如实标注）
    """
    tag: str                          # poffin / ultra_ball / hammer / boss ...
    kind: str = "item"                # item | supporter
    name: str = ""                    # 数据键
    name_zh: str = ""                 # 中文展示名

    @property
    def display(self) -> str:
        return self.name_zh or self.name

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
    # ---- v1.4 护盾（shields）----
    # 「下个对手回合」类防护：由招式/特性施加，在该玩家**下个回合开始**时清除
    # （见 battle._begin_turn）。注意与 `passive:*` 被动特性区分：被动写在卡面上、
    # 常驻生效，不占这两个字段。
    shield_reduce: int = 0                       # 受到的招式伤害减少 N（下限 0）
    shield_prevent_all: bool = False             # 免疫招式伤害**与效果**
    # ---- 静态修正（v1.5 · S1 静态修正层，2026-10-26）----
    # `passive:hp_plus:N`（**无条件的自身**加 HP）。为什么放在**实例**上而不是
    # 每次现算：`max_hp` 被 `is_ko` / 奖赏结算 / 前端展示广泛使用，而它拿不到
    # battle 上下文；条件式加 HP（「若有 {D} 能量则 +100」）因此**故意不支持**
    # （宁可未建模，也不做一个忽大忽小的血量上限）。条件式请走 `dmg_reduce`。
    bonus_hp: int = 0

    # ---- 基本属性 ----
    @property
    def name(self) -> str:
        return self.pdef.name

    @property
    def max_hp(self) -> int:
        return self.pdef.hp + self.bonus_hp

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
        """能否支付招式能量需求（不消耗能量——攻击不弃能）

        规则：**彩色需求须同色满足**；**无色需求可由任意能量支付**
        （先按同色占用扣除彩色需求，再看剩余总能量是否够无色部分）。
        例：`[('psychic',1),('colorless',1)]` 可由「超 + 火」支付。
        """
        need: dict = {}
        colorless = 0
        for t, n in cost:
            if t == COLORLESS:
                colorless += n
            else:
                need[t] = need.get(t, 0) + n
        used = 0
        for t, n in need.items():
            if self.energy_of(t) < n:
                return False
            used += n
        return (self.total_energy() - used) >= colorless

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
            "name": self.name, "name_zh": self.pdef.name_zh,
            "display": self.pdef.display,
            "hp": self.max_hp, "dmg": self.damage,
            "energy": self.total_energy(), "energy_map": dict(self.energy),
            "status": sorted(self.status), "stage": self.stage,
        }


def make_pokemon(pdef: PokemonDef) -> FieldPokemon:
    """把物种定义实例化到场上（并落**静态、无条件**的特性修正）

    只处理「入场即生效、无任何条件」的静态修正（S1 静态修正层的子集）：
      · `passive:hp_plus:N`   上限 +N（写入 `bonus_hp`，见 FieldPokemon 说明）
    其余静态修正（减伤 / 无弱点 / 无撤退）需要**攻防双方上下文**，在 battle 里现算
    （见 `battle._static_mods`），不在实例化时固化。
    """
    fp = FieldPokemon(pdef=pdef)
    for ab in pdef.abilities:
        tag = str(getattr(ab, "tag", "") or "")
        if tag.startswith("passive:hp_plus:"):
            raw = tag.split(":")
            if len(raw) > 2 and raw[2].isdigit():
                fp.bonus_hp += int(raw[2])
    return fp
