# -*- coding: utf-8 -*-
"""PokéLab Engine · 游戏状态模型"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
from .cards import CardRef, FieldPokemon


@dataclass
class GameConfig:
    """规则配置（默认按**官方规则**；开关仅用于回滚对拍与 oracle 校准）"""
    prizes: int = 6                # 奖赏卡总数
    ex_prizes: int = 2             # ex 被击倒对手拿取数
    mega_prizes: int = 3           # Mega ex 被击倒对手拿取数（2026-11-02）
    max_bench: int = 5
    opening_hand: int = 7
    # ⚠ 三条先手规则的**事实核对**（2026-11-08，官方 Q&A + Battle Academy 2024）：
    #   · 先手第一回合**不能攻击**（2013 起）→ 官方口径，S6 起默认 **True**
    #     （此前默认 False = 允许攻击，属简化偏差）；
    #   · 先手第一回合**不能打 Supporter**（2020-02 现行规则；其它训练家可以）
    #     → S6 新增，默认 **True**；
    #   · 先手第一回合**要抽牌**（官方 Q&A：「always draw a card at the beginning
    #     of your turn」）→ 默认 False 已符合官方，**不是**偏差。保留该字段只为
    #     与历史对拍，**不得**改为 True（那是伪需求）。
    #
    # 「先手第一回合」的判据 = **对局真的走过 setup**（`Battle.setup_done`）且
    # `turn_number == 1`。⚠ 不能用 `turn_counts[pi] == 1`：那会把**后手**玩家的
    # 第一个回合（第 2 回合）也一起锁住，而后手第一回合是可以攻击/打支援者的。
    # 合成对局（单元测试直接 `object.__new__` 摆场、没有 setup）不存在「开局回合」，
    # 因此规则不适用 —— 这是语义，不是绕过：没有 setup 就没有「先手第一回合」。
    first_turn_no_attack: bool = True    # 官方：先手 T1 不能攻击（Meloetta 类除外卡待建模）
    first_turn_no_supporter: bool = True  # 官方：先手 T1 不能打 Supporter（2020-02 起）
    first_turn_no_draw: bool = False     # 恒 False（官方每回合都抽，含先手 T1）
    mulligan_compensation: bool = True   # 对手因我方 mulligan 补抽 1
    max_turns: int = 300                 # 超限判平（防死循环）
    # 手牌上限：**官方无上限**（官方 Q&A 明确）。0 = 不设限（默认）。
    # >0 仅用于历史对拍/压力测试的防御式上限（超出丢弃并写 `hand_overflow` 日志）。
    max_hand: int = 0
    # S3（2026-11-08）：奖赏卡**真发牌**（官方 setup 第 7 步：从牌库顶取 6 张面朝下）
    #   True  → `PlayerState.prizes` 是 `list[PrizeSlot]`，牌库开局少 6 张
    #           （`deck_count` 上限 47），击倒时取的是**真卡**（进手牌）。
    #   False → 历史「只计数」口径（`prizes_left` 从 6 递减、牌库不减少）。
    # ⚠ 关闭时行为与 S3 之前**逐字节一致**，用于回滚对拍；默认已按官方规则打开。
    real_prizes: bool = True
    # S3（2026-11-08）：奖赏卡**真发牌**（官方 setup 第 7 步：从牌库顶取 6 张面朝下）
    #   True  → `PlayerState.prizes` 是 `list[PrizeSlot]`，牌库开局少 6 张
    #           （`deck_count` 上限 47），击倒时取的是**真卡**（进手牌）。
    #   False → 历史「只计数」口径（`prizes_left` 从 6 递减、牌库不减少）。
    # ⚠ 关闭时行为与 S3 之前**逐字节一致**，用于回滚对拍；默认已按官方规则打开。
    real_prizes: bool = True


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
    # S3（2026-11-08）：奖赏卡**对象层**——每张是真卡（`PrizeSlot.ref`），
    # 可翻面（`face_up`，翻到正面后保持到对局结束）。空 list = 「只计数」兼容
    # 模式（历史夹具直接装配 `prizes_left`），此时 `prizes_left` 仍是唯一真相，
    # 两者的一致性由 `engine/zones.py::_sync` 与 `test_zones_s3.py` 守护。
    prizes: list = field(default_factory=list)        # list[PrizeSlot]
    # 是否「奖赏卡已对象化」的**显式旗标**（`zones.deal_prizes` 置位）。
    # ⚠ 不能用 `bool(prizes)` 代替：最后一张被取走时列表会变空，模式判断会翻转，
    #   导致 `prizes_left` 停在上一次的值（实测整局错位）。
    prizes_real: bool = False
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
    # 第一百零七批（2026-11-06）：手牌禁打的另外三类
    # （Chi-Yu 封场地 / Scream Tail ex 封支援者 / Genesect 封 ACE SPEC）。
    supporter_lock_until: int = 0
    stadium_lock_until: int = 0
    acespec_lock_until: int = 0
    # ---- 回合历史（2026-10-28 第七批）----
    # 「during your last turn」类条件：每回合开始把「本回合」滚到「上回合」
    turn_attacks: list = field(default_factory=list)      # 本回合用过的招式名
    last_turn_attacks: list = field(default_factory=list)  # 上回合用过的招式名
    # 「1 of your other Ancient Pokémon used an attack during your last turn」
    turn_ancient_attackers: list = field(default_factory=list)
    last_turn_ancient_attackers: list = field(default_factory=list)
    prizes_taken_this_turn: int = 0
    prizes_taken_last_turn: int = 0
    # ---- 本回合限时加伤（2026-11-06 第九十五批，Feraligatr）----
    # 「… during this turn, attacks used by this Pokémon do N more damage …」
    # 与 `prize_bonus_until` 同用 turn_number 过期：`dmg_bonus_until == turn_number`
    # 即「仅本回合有效」，避免再加一个每回合清零的钩子。
    dmg_bonus_n: int = 0
    dmg_bonus_scope: str = "self"       # self | team
    dmg_bonus_until: int = 0
    # 「this Pokémon」的**具体持有者**：范围 self 时必须按对象同一性判定，
    # 不能用 `active` —— 换位后 active 会变成别的宝可梦，加伤会跟着"沾光"。
    dmg_bonus_src: object = None
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
    # ---- 按「附着能量数」全队禁攻（2026-11-08 第一百三十四批）----
    # 「During your opponent's next turn, Pokémon that have 2 or less Energy
    #  attached can't use attacks. (This includes new Pokémon that come into
    #  play.)」（Walrein「Frigid Fangs」）
    # 挂在**被封锁方**的玩家状态上而不是逐只 `attack_lock_all_until`：
    # 卡面明确「包括之后上场的宝可梦」，逐只写会漏掉换上来的那只（静默削弱）。
    # 到期口径同其它跨回合锁：`until = 施加时该方 turn_counts + 2`。
    atk_energy_block_n: int = 0
    atk_energy_block_until: int = 0

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
