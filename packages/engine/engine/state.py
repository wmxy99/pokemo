# -*- coding: utf-8 -*-
"""PokéLab Engine · 游戏状态模型"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
from .cards import CardRef, FieldPokemon


@dataclass
class GameConfig:
    """规则配置（简化子集的开关，便于未来与 oracle 校准差异）"""
    prizes: int = 6                # 奖赏卡总数
    ex_prizes: int = 2             # ex 被击倒对手拿取数
    max_bench: int = 5
    opening_hand: int = 7
    first_turn_no_attack: bool = False   # 官方现行规则先手第一回合不可攻击；v1 默认关（简化）
    first_turn_no_draw: bool = False     # v1 默认每回合都抽 1（与官方差异见 README）
    mulligan_compensation: bool = True   # 对手因我方 mulligan 补抽 1
    max_turns: int = 300                 # 超限判平（防死循环）
    max_hand: int = 30                   # 手牌上限防御（超出丢弃，简易保护）


@dataclass
class PlayerState:
    """单名玩家的全部状态"""
    name: str
    deck: list = field(default_factory=list)          # 牌库（-1 为顶）
    hand: list = field(default_factory=list)          # 手牌（CardRef）
    discard: list = field(default_factory=list)       # 弃牌堆（str 描述即可）
    discard_refs: list = field(default_factory=list)  # 弃牌堆**类型化**副本（CardRef）
    #   为何需要两份：`discard` 是历史展示层（str，前端/report 直接读），
    #   `discard_refs` 供「弃牌堆回收」类效果（Night Stretcher / 从弃牌堆检索）
    #   重建真实卡对象。两者由 Battle._to_discard() 保证同步推进。
    active: Optional[FieldPokemon] = None
    bench: list = field(default_factory=list)         # list[FieldPokemon]
    prizes_left: int = 0                              # 剩余奖赏数（0 → 胜）
    mulligan_count: int = 0
    # 回合内一次性资源
    attached_energy_this_turn: int = 0
    supporter_used: int = 0                           # E4: 每回合支援者使用标志

    # ---- 便捷查询 ----
    @property
    def has_pokemon(self) -> bool:
        return self.active is not None or len(self.bench) > 0

    def all_field(self) -> list:
        out = [self.active] if self.active else []
        out.extend(self.bench)
        return out

    def basic_in_hand(self) -> list:
        from .cards import PokeRef
        return [c for c in self.hand if isinstance(c, PokeRef)]

    def energy_in_hand(self) -> int:
        from .cards import EnergyRef
        return sum(1 for c in self.hand if isinstance(c, EnergyRef))

    def draw(self, n: int = 1) -> int:
        """从牌库顶抽 n 张，返回实际抽取张数（不足则抽空）"""
        got = 0
        while self.deck and got < n:
            self.hand.append(self.deck.pop())
            got += 1
        return got

    def bench_has_room(self, cfg: GameConfig) -> bool:
        return len(self.bench) < cfg.max_bench

    def attach_energy_to(self, target: FieldPokemon, cfg: GameConfig) -> bool:
        """将手牌一张能量附着到目标（按能量类型计入 energy dict）；成功返回 True"""
        if self.energy_in_hand() <= 0:
            return False
        from .cards import EnergyRef
        for i in reversed(range(len(self.hand))):
            if isinstance(self.hand[i], EnergyRef):
                ref = self.hand.pop(i)
                break
        target.add_energy(ref.edef.type)
        return True
