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
    mega_prizes: int = 3           # Mega ex 被击倒对手拿取数（2026-11-02）
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
    # S3: 本回合打出过的支援者**卡名**（供「名字含 X 的支援者」类场地上判定）
    #   为何要存名字而不只存标志：Team Rocket's Factory 之类只认名字含特定词的支援者，
    #   只有 used 标志无法区分"打的是不是那一张"。
    supporter_name: str = ""
    # v1.4「in this way」计数：本回合已弃置的牌数
    # （供 dmg_plus:N:per:own_discarded_this_turn / …_energy_this_turn）
    discarded_this_turn: int = 0
    discarded_energy_this_turn: int = 0
    # 跨回合手牌禁打（2026-10-27）：`until = 施放时 owner turn_counts + 2`，
    # 只在该玩家**下一个回合**生效（与 FieldPokemon 的 attack/retreat lock 同窗口）。
    item_lock_until: int = 0
    evolution_lock_until: int = 0
    # ---- 回合历史（2026-10-28 第七批）----
    # 「during your last turn」类条件：每回合开始把「本回合」滚到「上回合」
    turn_attacks: list = field(default_factory=list)      # 本回合用过的招式名
    last_turn_attacks: list = field(default_factory=list)  # 上回合用过的招式名
    # 「1 of your other Ancient Pokémon used an attack during your last turn」
    turn_ancient_attackers: list = field(default_factory=list)
    last_turn_ancient_attackers: list = field(default_factory=list)
    prizes_taken_this_turn: int = 0
    prizes_taken_last_turn: int = 0
    # ---- 本回合多拿奖赏（2026-11-05 第八十九批，Briar）----
    # 「During this turn, if your opponent's Active Pokémon is Knocked Out by
    #  damage from an attack used by your Tera Pokémon, take N more Prize card.」
    # 打出支援者时设为本回合旗标；`prize_bonus_until == self.turn_number`
    # 表示「仅本回合有效」（turn_number 每回合 +1，过期自动失效）。
    prize_bonus_n: int = 0
    prize_bonus_tera: bool = False     # True = 攻击方须为 Tera 宝可梦
    prize_bonus_until: int = 0
    # ---- 全队限时减伤（2026-11-03 第四十六批，Iron Defender）----
    # 「During your opponent's next turn, all of your {M} Pokémon take N less
    # damage … (This includes new Pokémon that come into play.)」
    # 记在**玩家**层面：逐只写 `shield_reduce` 会漏掉「之后上场的宝可梦」。
    # 到期口径：`team_shield_until = 施加时 owner turn_counts + 2`（同 shield）。
    team_shield_n: int = 0
    team_shield_type: str = ""
    team_shield_until: int = 0
    # ---- 全队限时增伤（2026-11-04 第六十批）----
    # 「During **this turn**, attacks used by your {F} Pokémon do 30 more damage
    #  to your opponent's Active Pokémon (before applying Weakness and
    #  Resistance).」（Premium Power Pro / Black Belt's Training）
    # 与 `team_shield_*` 对称：那边是「对方回合的减伤」，这边是「**本回合**的增伤」。
    # 到期口径与减伤**不同**：卡面写的是「this turn」而不是「next turn」，
    # 因此只在**施加当回合**有效（`team_boost_turn == 当前回合序号`），
    # 用 `until = turn + 2` 会多生效一个回合（对局里看不出来，只多打了伤害）。
    team_boost_n: int = 0
    team_boost_type: str = ""
    team_boost_cats: str = ""      # 逗号分隔的卡牌子类（ex / v …）
    team_boost_turn: int = 0
    # ---- 本回合已攻击次数（2026-11-04 第六十一批）----
    # 引擎原本不需要这个计数：攻击必然结束回合，所以「每回合一次」是结构性成立的。
    # Festival Lead（「If Festival Grounds is in play, this Pokémon may use an
    # attack it has twice.」）打破了这个前提 —— 必须显式记账，否则「续攻」与
    # 「无限攻」只差一个判断。
    attacks_this_turn: int = 0

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

    def attach_energy_to(self, target: FieldPokemon, cfg: GameConfig,
                         index: Optional[int] = None) -> bool:
        """将手牌一张能量附着到目标（成功返回 True）

        `index` 指定手牌卡位（特殊能量与基本能量必须能分开选）；缺省取手牌里
        最后一张能量卡（保持既有行为）。特殊能量**不落到 `energy` 计数**，
        而是把卡对象挂到 `target.senergy`（提供量实时计算）。
        """
        from .cards import EnergyRef
        if index is None:
            for i in reversed(range(len(self.hand))):
                if isinstance(self.hand[i], EnergyRef):
                    index = i
                    break
        if index is None or not (0 <= index < len(self.hand)):
            return False
        ref = self.hand[index]
        if not isinstance(ref, EnergyRef):
            return False
        self.hand.pop(index)
        if ref.edef.is_special:
            target.senergy.append(ref)
        else:
            target.add_energy(ref.edef.type)
        return True
