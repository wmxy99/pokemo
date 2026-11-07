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

from . import special_energy

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
    # 发动代价（2026-11-04 第四十九批）：卡面「You must <代价> in order to use
    # this Ability.」—— 与 `TrainerDef.play_cost` 同性质，**不是 tag**：代价不是
    # 效果，做成 tag 会挤掉主效果，而且执行顺序会跑到效果之后。
    # 形态见 `battle._parse_ability_cost`：discard / discard_named /
    # discard_self_energy / discard_hand_energy / hand_bottom。
    cost: str = ""
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
    # 2026-10-28（B3）：数据集的 `rule` / `category` 两个分类字段进入引擎定义。
    # 为什么必须带进来：卡面条件「if you have any **Mega Evolution** Pokémon {ex}
    # in play」（Linoone）与计数源「for each of your **Ancient** Pokémon in play」
    # （Awakening Drum）都只能由这两个字段判定 —— 用卡名子串猜会误伤
    # （「Mega」出现在招式名里、「Ancient」出现在文本里）。
    is_mega: bool = False             # rule == "Mega Pokémon ex"
    category: str = ""                # Ancient / Future / Tera(...) / Trainer's Pokémon(...)
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
    """能量牌（v0.2 支持彩色；2026-10-28 支持特殊能量）

    `tag` 为空 = 基本能量（提供 `type` 一点）；
    `tag = "senergy:<kind>"` = 特殊能量，提供的能量与附加规则见
    `engine/special_energy.py`（按持有者实时计算）。
    """
    type: str = COLORLESS
    name: str = ""                    # 数据键；默认按 type 生成
    name_zh: str = ""                 # 中文展示名（如「基本火能量」）
    tag: str = ""                     # senergy:<kind>（特殊能量）· "" = 基本能量

    def __post_init__(self):
        if not self.name:
            object.__setattr__(self, "name",
                               f"{self.type.capitalize()} Energy")

    @property
    def display(self) -> str:
        return self.name_zh or self.name

    @property
    def is_special(self) -> bool:
        return str(self.tag or "").startswith("senergy:")


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
    kind: str = "item"                # item | supporter | tool | stadium
    name: str = ""                    # 数据键
    name_zh: str = ""                 # 中文展示名
    # 化石卡（2026-10-28）：卡面「Play this card as if it were a N-HP Basic {C}
    # Pokémon」。上场时按这份**宝可梦定义**实例化（含卡面特性），卡本身仍是
    # 训练家卡（从场上弃置/被击倒时按 TrainerRef 进弃牌堆）。
    fossil_pdef: Optional["PokemonDef"] = None
    # 打出代价（2026-11-02 第三十六批）：卡面
    #   「You can use this card only if you discard N other cards from your hand.」
    # 形态 `"discard:<n>"`，空串 = 无代价。
    #
    # ⚠ 与 tag 里的 `:gate:` **不是一回事**：gate 是纯条件检查（不满足不能打，
    # 满足时无状态变化）；代价是**打出前必须先付的账** —— 手牌真的少 N 张。
    # 引擎在 `_do_trainer` 派发**之前**执行，顺序与卡面一致（先付账、后效果）；
    # 付不出时该卡不进合法动作表（`_trainer_legals`），而不是打了才发现。
    play_cost: str = ""
    # 打出**前置条件**（2026-11-04 第五十九批）：卡面
    #   「You can use this card only if you have any Tera Pokémon in play.」
    # 与 `play_cost` 的分工（两者都表现为 `only if`，但语义相反）：
    #   · `play_cost` 要**真的付账**（手牌少 N 张）—— 有状态变化；
    #   · `play_gate` 只是**条件检查**（满足时什么都不发生）—— 无状态变化。
    # 把 gate 当 cost 实现 = 白扣玩家的牌；把 cost 当 gate 实现 = 免费。
    # 形态见 `battle._play_gate_ok`；空串 = 无条件。不满足时该卡不进合法动作表。
    play_gate: str = ""

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
    shield_prevent_all: bool = False             # 挡下招式**伤害**（是否也挡效果见下一字段）
    # 2026-10-28（第八批）：卡面必须区分「prevent all damage」与「prevent all damage
    # **from and effects of** attacks」—— 前者只挡伤害，后者连效果一起挡。统一按
    # 「全挡」处理会静默放大防护范围（例如 Metapod 只挡伤害，却把中毒也挡掉）。
    shield_prevent_effects: bool = False
    # 护盾的来源/门槛限定（`from:basic` / `from:basic_non_colorless` /
    # `if_damage:le:N`；空串 = 无限定）。无来源限定的形态才是真「全挡」。
    shield_prevent_qual: str = ""
    # ---- 静态修正（v1.5 · S1 静态修正层，2026-10-26）----
    # `passive:hp_plus:N`（**无条件的自身**加 HP）。为什么放在**实例**上而不是
    # 每次现算：`max_hp` 被 `is_ko` / 奖赏结算 / 前端展示广泛使用，而它拿不到
    # battle 上下文；条件式加 HP（「若有 {D} 能量则 +100」）因此**故意不支持**
    # （宁可未建模，也不做一个忽大忽小的血量上限）。条件式请走 `dmg_reduce`。
    bonus_hp: int = 0
    # ---- S2 道具（Pokémon Tool，2026-10-26）----
    # 附着在**本只**宝可梦上的道具 tag（`tool:<kind>[:N]`）。每只最多 1 张。
    # 存 tag 而不是卡对象：引擎只需要"它能提供什么修正"，不需要卡面文本。
    tool: Optional[str] = None
    tool_name: str = ""              # 展示用（中文优先由上层 fill）
    # S6 回手族（2026-10-28）：保留**道具卡对象**，使「宝可梦与附加卡一起回手
    # / 洗回牌库」能精确执行（只存 tag 的话道具只能进弃牌堆，等于少回一张卡）。
    tool_ref: Optional[object] = None
    # ---- 跨回合锁定（2026-10-27）----
    # 「During your next turn, this Pokémon can't use attacks / Giant Wave.」
    # 「During your opponent's next turn, the Defending Pokémon can't retreat.」
    # 用 owner 的 `turn_counts` 到期（见 battle._begin_turn 注释）：
    # `until = 施加时 owner 的回合数 + 2` → 只在 owner 的下一个回合内生效。
    attack_lock_all_until: int = 0
    attack_lock_names_until: dict = field(default_factory=dict)  # 名称小写 → until
    # 「again until it leaves the Active Spot」：离场/进化时清除
    attack_lock_names_active: set = field(default_factory=set)
    retreat_lock_until: int = 0
    # ---- 下回合限制（2026-10-28）----
    # 「During your opponent's next turn, if the Defending Pokémon tries to use an
    # attack, your opponent flips a coin. If tails, that attack doesn't happen.」
    # 到期口径同 `attack_lock_*`（持有者 turn_counts + 2）。
    attack_denial_until: int = 0
    # 「… attacks used by the Defending Pokémon cost {C} more, and its Retreat
    # Cost is {C} more.」（Rillaboom）
    attack_cost_up_until: int = 0
    attack_cost_up_n: int = 0
    retreat_up_until: int = 0
    retreat_up_n: int = 0
    # ---- 跨回合**伤害**修正（2026-10-29 第十六批）----
    # 「During your next turn, the Defending Pokémon takes N more damage from
    #  attacks (**after** applying Weakness and Resistance).」→ 记在**受击方**
    # 「During your opponent's next turn, attacks used by the Defending Pokémon
    #  do N less damage (**before** applying Weakness and Resistance).」→ 记在受击方
    # 「During your next turn, this Pokémon's <招式> attack does N more damage」
    #  → 记在**自己**身上，按招式名匹配
    # 到期口径同 `attack_lock_*`：`until = 施加时 owner 的 turn_counts + 2`。
    # ---- 受击历史（2026-11-06 第九十九批）----
    # 「If this Pokémon was damaged by an attack during your opponent's last turn,
    #  this attack does **that much** more damage.」（Mega Heracross ex）
    # `dmg_taken_mark` 累积「自本回合开始以来被**招式**打掉的伤害」；
    # 回合开始时滚到 `dmg_taken_prev` 并清零 —— 因此在**自己回合内**读到的
    # `dmg_taken_prev` 恰好是「对手上个回合打掉的血量」。
    # ⚠ 不能用单个字段 + 回合开始清零：清零会把**正要用的值**擦掉。
    dmg_taken_mark: int = 0
    dmg_taken_prev: int = 0
    dmg_taken_plus_next_n: int = 0
    dmg_taken_plus_next_until: int = 0
    # 到期口径按**施加者**（= 原攻击方）的 turn_counts；只有该方回合内生效。
    dmg_taken_plus_next_owner: int = -1
    atk_minus_next_n: int = 0
    atk_minus_next_until: int = 0
    self_atk_plus_next: dict = field(default_factory=dict)  # 招式键 → (n, until)
    # 「During your next turn, this Pokémon's <招式> attack's **base damage is N**」
    # （第一百一十九批，Watchog「Focus Energy」）—— 是**改写**不是加伤，
    # 与 `self_atk_plus_next` 分字段存，避免「240 变成 80+240」。
    self_atk_set_next: dict = field(default_factory=dict)   # 招式键 → (n, until)
    # ---- 下回合「受击反制」（2026-10-29 第二十九批）----
    # 「During your opponent's next turn, if **this Pokémon** is damaged by an
    #  attack (even if this Pokémon is Knocked Out), put N damage counters on the
    #  Attacking Pokémon [equal to the damage done to this Pokémon].」
    # —— 由**进攻方招式**挂在自己身上，窗口 = 对手的下一个回合。
    retaliate_next_n: int = 0        # 固定反制伤害（点数）；`equal` 时置 0
    retaliate_next_equal: bool = False   # 反制伤害 = 本次被打的伤害
    retaliate_until: int = 0         # 到期按**触发方**（= 对手）turn_counts 判定
    retaliate_owner: int = -1        # 该方回合内触发
    # ---- 回合历史（2026-10-28 第七批）----
    # 卡面「If this Pokémon moved from your Bench to the Active Spot **this turn**」
    # 「evolved from X **during this turn**」「was healed **during this turn**」
    # —— 只能靠回合级记账精确判定；记录**回合序号**（`battle.turn_number`），
    # 比较时用「等于当前回合」而不是布尔，避免跨回合残留。
    moved_active_turn: int = 0
    evolved_turn: int = 0
    evolved_from_name: str = ""
    healed_turn: int = 0
    # ---- Pokémon Checkup 伤害改写（2026-10-28 第十三批）----
    # 「During Pokémon Checkup, put N damage counters on that Pokémon instead of
    # 1.」（Team Rocket's Nidoking ex「Tainted Horn」）—— 由招式作用在**该只**
    # 身上，取代默认的中毒/烧伤检查期伤害。`{status: dmg}`（dmg 为点数，非指示物数）。
    checkup_set: dict = field(default_factory=dict)
    # ---- 化石卡（2026-10-28）----
    # `fossil=True` 表示这只「宝可梦」由训练家卡（Fossil）上场：不受任何特殊
    # 状态影响、不能撤退，且可在己方回合任意时刻弃置（`discard_fossil`）。
    # 进化后回到普通宝可梦（evolve_to 会清空这两个字段）。
    fossil: bool = False
    fossil_ref: Optional[object] = None   # 原始 TrainerRef（弃置/击倒时进弃牌堆）
    # ---- 特殊能量（2026-10-28）----
    # 附着在**本只**宝可梦上的特殊能量卡（EnergyRef 列表）。能量的**数量与属性**
    # 不落到 `energy` 里，而是按 `special_energy.provides()` 实时计算 —— 这样
    # 进化（Stage 1 → Stage 2 / 基础 → 进化）后 Neo Upper / Prism / Ignition 的
    # 供给会自动重算，不会残留旧值。
    senergy: list = field(default_factory=list)
    # Jamming Tower（2026-10-28）：道具**在场时失效**——仍附着但所有修正/触发都不生效。
    # 由 `battle._refresh_stadium_hp`（场地进入/被顶替/回合开始）统一刷新。
    tool_disabled: bool = False

    # ---- 基本属性 ----
    @property
    def name(self) -> str:
        return self.pdef.name

    @property
    def max_hp(self) -> int:
        # 道具的 +HP 在这里叠加（道具是**中途附着**的，不能在 make_pokemon 时固化）
        # 特殊能量（Grow Grass）同样中途附着，故一并在此计算
        return (self.pdef.hp + self.bonus_hp
                + _tool_hp_plus(self)
                + special_energy.hp_plus(self)
                # 条件式 +HP（第一百三十四批）：判据只依赖**本只身上**的附着卡
                # （自身能量 / 特殊能量），因此可以在这里现算，不需要 battle 上下文，
                # 也不会出现「刷新点漏一处 → 血量上限漂移」。
                # 队伍级（「all of your Pokémon get +N HP」）不在此列 —— 那种必须
                # 由 battle._refresh_stadium_hp 统一重算，见该函数注释。
                + cond_hp_bonus(self))

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
    def special_map(self) -> dict:
        """特殊能量提供的能量（实时计算：进化后自动按新物种重算）"""
        out: dict = {}
        for ref in self.senergy:
            for t, n in special_energy.provides(ref.edef, self).items():
                out[t] = out.get(t, 0) + n
        return out

    def energy_counts(self) -> dict:
        """支付用的能量池 = 基本能量 + 特殊能量（含 `any` / `flex:` 键）"""
        m = dict(self.energy)
        for t, n in self.special_map().items():
            m[t] = m.get(t, 0) + n
        return m

    def total_energy(self) -> int:
        return sum(self.energy_counts().values())

    def energy_of(self, t: str) -> int:
        return self.energy_counts().get(t, 0)

    def energy_of_type(self, t: str) -> int:
        """可支付 `{t}` 的能量点数（同色 + 双色任选含该色 + 任意属性）

        用途：卡面「for each {W} Energy attached」类计数。基本能量的结果与
        `energy_of` 完全一致（池内没有 flex/any 时不多算）。
        """
        m = self.energy_counts()
        n = m.get(t, 0) + m.get(special_energy.ANY, 0)
        for k, v in m.items():
            if k.startswith(special_energy.FLEX_PREFIX) \
                    and t in k[len(special_energy.FLEX_PREFIX):].split(","):
                n += v
        return n

    def add_energy(self, t: str, n: int = 1) -> None:
        self.energy[t] = self.energy.get(t, 0) + n

    def can_pay(self, cost: EnergyCost) -> bool:
        """能否支付招式能量需求（不消耗能量——攻击不弃能）

        规则：**彩色需求须同色满足**（可用「双色任选 / 任意属性」能量补足）；
        **无色需求可由任意能量支付**。
        """
        pool = self.energy_counts()
        need: dict = {}
        colorless = 0
        for t, n in cost:
            if t == COLORLESS:
                colorless += n
            else:
                need[t] = need.get(t, 0) + n
        for t, n in need.items():
            take = min(pool.get(t, 0), n)
            pool[t] = pool.get(t, 0) - take
            n -= take
            if n > 0:
                # 双色任选（Team Rocket's Energy）
                for key in list(pool):
                    if n <= 0:
                        break
                    if not key.startswith(special_energy.FLEX_PREFIX):
                        continue
                    if t not in key[len(special_energy.FLEX_PREFIX):].split(","):
                        continue
                    take = min(pool[key], n)
                    pool[key] = pool[key] - take
                    n -= take
            if n > 0:
                # 任意属性（Legacy / Prism / Neo Upper）
                take = min(pool.get(special_energy.ANY, 0), n)
                pool[special_energy.ANY] = pool.get(special_energy.ANY, 0) - take
                n -= take
            if n > 0:
                return False
        return sum(v for v in pool.values()) >= colorless

    def remove_any(self, n: int) -> bool:
        """移除 n 点能量（撤退/干扰用）：基本能量无色优先 → 其他基本色 →
        特殊能量卡（规则上弃的是**卡**，故整张移除）

        返回是否支付成功；特殊能量卡被移除时不会回到弃牌堆 —— 与既有的
        「基本能量卡不落弃牌堆」口径一致（引擎的能量是计数而非卡）。
        """
        if self.total_energy() < n:
            return False
        # 先无色（基本）
        c = self.energy.get(COLORLESS, 0)
        take_c = min(c, n)
        if take_c:
            self.energy[COLORLESS] = c - take_c
            n -= take_c
        # 其余基本能量按任意类型扣除
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
        # 最后消耗特殊能量卡（整张；后附先弃）
        while n > 0 and self.senergy:
            ref = self.senergy.pop()
            n -= sum(special_energy.provides(ref.edef, self).values())
        return True

    @staticmethod
    def _attack_key(name: str) -> str:
        return str(name or "").replace(":", " ").strip().lower()

    def attack_locked(self, attack_name: str, turn_count: int) -> bool:
        """该招式当前是否被封锁（全部招式 / 指定招式 / 离场前）"""
        if self.attack_lock_all_until > turn_count:
            return True
        key = self._attack_key(attack_name)
        return (self.attack_lock_names_until.get(key, 0) > turn_count
                or key in self.attack_lock_names_active)

    def retreat_locked(self, turn_count: int) -> bool:
        """是否处于「对手下个回合内不能撤退」的时限锁定"""
        return self.retreat_lock_until > turn_count

    def clear_active_locks(self) -> None:
        """离开战斗位时清除「直到离场」类攻击封锁"""
        self.attack_lock_names_active.clear()

    def clear_turn_locks(self) -> None:
        """进化时清除全部锁定（进化后的宝可梦不再是原效果的目标）"""
        self.attack_lock_all_until = 0
        self.attack_lock_names_until.clear()
        self.attack_lock_names_active.clear()
        self.retreat_lock_until = 0
        self.dmg_taken_plus_next_n = 0
        self.dmg_taken_plus_next_until = 0
        self.dmg_taken_plus_next_owner = -1
        self.atk_minus_next_n = 0
        self.atk_minus_next_until = 0
        self.self_atk_plus_next.clear()
        self.self_atk_set_next.clear()
        # 进化后不再是原效果的目标 → 下回合反制一并清除
        self.retaliate_next_n = 0
        self.retaliate_next_equal = False
        self.retaliate_until = 0
        self.retaliate_owner = -1

    def evolve_to(self, ndef: PokemonDef) -> None:
        """E2 进化：替换物种；保留能量与伤害，清除状态"""
        self.pdef = ndef
        self.status.clear()
        self.clear_turn_locks()
        # 检查期改写作用在「原来那只」身上；进化后按新卡面结算默认规则。
        self.checkup_set.clear()
        self.just_played = True
        # 化石被进化后就是普通宝可梦：不再享有「不受特殊状态 / 不可撤退 /
        # 可随时弃置」这三条化石特权（继续留着会变成**静默加强**）。
        self.fossil = False
        self.fossil_ref = None

    def summary(self) -> dict:
        return {
            "name": self.name, "name_zh": self.pdef.name_zh,
            "display": self.pdef.display,
            "hp": self.max_hp, "dmg": self.damage,
            "energy": self.total_energy(), "energy_map": dict(self.energy),
            "senergy": [ref.edef.name for ref in self.senergy],
            "status": sorted(self.status), "stage": self.stage,
        }


def _tool_value(tag: Optional[str], kind: str) -> int:
    """``tool:<kind>:<n>`` → n（不匹配返回 0，**不猜**）"""
    if not tag:
        return 0
    parts = str(tag).split(":")
    if len(parts) >= 3 and parts[0] == "tool" and parts[1] == kind \
            and parts[2].isdigit():
        return int(parts[2])
    return 0


def _hp_cond_ok(fp: "FieldPokemon", cond: str) -> bool:
    """条件式 +HP 的条件求值（只支持**只依赖本只附着卡**的形态）

    支持：
      · ``special_energy``       本只身上有任意特殊能量
      · ``energy:<type>``        本只身上有该属性的能量（基本 + 特殊合计）
    未知形态一律返回 False —— **宁可不加血，也不静默多给上限**（多给上限会让
    「被打到 0」变成「还活着」，是极难发现的错）。
    """
    c = str(cond or "").strip().lower()
    if not c:
        return True
    if c == "special_energy":
        return bool(fp.senergy)
    if c.startswith("energy:"):
        want = c.split(":", 1)[1].strip()
        return bool(want) and fp.energy_counts().get(want, 0) > 0
    return False


def cond_hp_bonus(fp: "FieldPokemon") -> int:
    """``passive:hp_plus:<n>:if:<cond>`` 的现值（无条件形态不在此处，见 bonus_hp）"""
    total = 0
    for ab in fp.pdef.abilities:
        tag = str(getattr(ab, "tag", "") or "")
        if not tag.startswith("passive:hp_plus:"):
            continue
        raw = tag.split(":")
        if len(raw) < 5 or raw[3] != "if":
            continue
        if not raw[2].isdigit():
            continue
        if _hp_cond_ok(fp, ":".join(raw[4:])):
            total += int(raw[2])
    return total


def _name_key_simple(name: str) -> str:
    """卡名归一化（只留小写字母数字）—— 道具 ``holder:name:<k>`` 比对用"""
    return "".join(ch for ch in str(name or "").lower() if ch.isalnum())


def _tool_hp_plus(fp: "FieldPokemon") -> int:
    """道具提供的 +HP，支持 ``holder:name:<k>`` 限定（第一百三十四批）

    · ``tool:hp_plus:N``                    → 无条件 +N
    · ``tool:hp_plus:N:holder:name:cynthia`` → 仅当**持有者**卡名以该键开头
      （Cynthia's Power Weight：「The **Cynthia's Pokémon** this card is attached
      to gets +70 HP.」—— 不判持有者就变成「谁挂上都 +70」，属静默加强）
    """
    tag = "" if fp.tool_disabled else fp.tool
    n = _tool_value(tag, "hp_plus")
    if not n:
        return 0
    parts = str(tag).split(":")
    if "holder" in parts:
        i = parts.index("holder")
        if i + 2 >= len(parts) or parts[i + 1] != "name":
            return 0            # 限定形态不认识 → **不生效**
        if not _name_key_simple(fp.pdef.name).startswith(
                _name_key_simple(parts[i + 2])):
            return 0
    return n


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
        # ⚠ 条件式（`…:if:<cond>`）**不进** bonus_hp：它随时可能因附着变化而变，
        #   固化进来就会在「能量被弃掉」之后仍然多给血（`max_hp` 里现算）。
        if tag.startswith("passive:hp_plus:") and ":if:" not in tag:
            raw = tag.split(":")
            if len(raw) > 2 and raw[2].isdigit():
                fp.bonus_hp += int(raw[2])
    return fp
