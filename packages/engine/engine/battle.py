# -*- coding: utf-8 -*-
"""PokéLab Engine · 对战裁判（回合状态机 + 结算闭环）· v0.2

v0.2 新增（E1 彩色能量 / E2 进化 / E4 训练家最小集）：
  - 能量分色支付：攻击校验 can_pay(cost)；撤退/干扰 remove_any（无色优先）；攻击不弃能
  - evolve 动作：手牌 stage+1 且 evolves_from==场上目标；本回合刚放置/进化不可再进化；
    进化保留能量与伤害、清除特殊状态
  - trainer 动作：item（不限次）与 supporter（每回合一次）；tag 派发
    poffin / ultra_ball / hammer / boss
v1 沿用：回合开始检查→抽卡→主阶段→攻击即结束回合；确定性 seed；事件日志。
"""
from __future__ import annotations
import random
import re
from dataclasses import fields
from typing import Optional
from .cards import (PokeRef, EnergyRef, EnergyDef, TrainerRef, FieldPokemon,
                    make_pokemon, _tool_value, COLORLESS, TYPES)
from .state import GameConfig, PlayerState
from .choice import PendingChoice
# S3（2026-11-08）区域对象化：奖赏卡槽 / 牌库底 / 只读检视
from . import zones
# S2（2026-11-08）事件总线 + 延迟队列
from . import events
from .rules import (compute_damage, status_checkup, confusion_self_damage,
                    distribute_damage, apply_shield, STATUSES)
# 特殊能量子系统（2026-10-28）：提供量/附着限制/静态效果集中在一处
from . import special_energy
# 名称型字段的优先顺序（与 engine.commands 同源，避免两处漂移）
from .commands import NAME_KEYS as _NAME_KEYS
# 效果族插件层（fx · 2026-10-27）：长尾机制按族拆到 engine/families/fx_*.py
from . import families

WIN_REASONS = ("prizes", "no_pokemon", "deckout", "max_turns_draw")

TRAINER_TAGS = ("poffin", "ultra_ball", "hammer", "boss")

# 代价**已内建在自身 helper 里**的训练家 tag（2026-11-02 第三十六批）。
#
# `cards_real.json` 是**人工维护**的卡池，其条目没有 `play_cost` 字段：
# Ultra Ball 的「弃 2 张手牌」写死在 `_trainer_ultra_ball` 内部。而解析链会为
# 同一张卡（`other_text` 含「only if you discard 2 other cards」）产出
# `play_cost="discard:2"` —— 若 `_do_trainer` 再统一执行一次，就会**弃 4 张**
# （双重扣牌，玩家侧是净损失、AI 侧是错误强度）。这里显式排除。
#
# ⚠ 新增「内建代价」的 tag 必须登记到这里；反过来，若把某张卡的代价从 helper
# 迁移到 `play_cost`，也要同时把它从本表删除。
_BUILTIN_COST_TAGS = ("ultra_ball",)

# 属于「伤害修正层」的 tag 前缀（由 _attack_mods 结算，不得在效果层重复执行）
_DAMAGE_MOD_PREFIXES = ("dmg_plus:", "dmg_set:", "dmg_minus:", "dmg_zero",
                        "ignore_weakness", "ignore_resistance")

# 第六十五批（2026-11-04）：无视效果的**两档反口径**，两处伤害计算共用。
#   ignore_effects      只无视守方身上的效果（护盾/减伤/道具/场地），弱抗照常
#   ignore_effects:wr   连印在卡上的弱点/抗性一并无视
# 未知后缀必须抛错：静默当成窄口径会漏掉弱抗那一半（测试全绿但强度错了）。
_IGNORE_EFFECTS_FORMS = ("ignore_effects", "ignore_effects:wr")


def _ignore_effects_flags(effects) -> tuple:
    """→ ``(ignore_eff, ignore_wr)``；未知 ``ignore_effects*`` 形态抛 ValueError"""
    seq = tuple(str(e) for e in (effects or ()))
    for e in seq:
        if e.startswith("ignore_effects") and e not in _IGNORE_EFFECTS_FORMS:
            raise ValueError(f"未识别的 ignore_effects 形态：{e}")
    return ("ignore_effects" in seq or "ignore_effects:wr" in seq,
            "ignore_effects:wr" in seq)

# 抛币子效果里的**卡面英文状态词** → 引擎规范状态名（2026-10-29 第二十二批）。
# 历史数据（`coin:heads:paralyzed`）此前会走到效果派发的「未知 tag」分支被**静默
# 忽略**（状态永不施加）—— 现在统一在这里归一，解析侧也只产出规范名。
_COIN_STATUS_ALIASES = {"paralyzed": "paralyze", "burned": "burn",
                        "confused": "confuse", "poisoned": "poison",
                        "asleep": "sleep"}

# 禁止攻击 / 撤退的状态 —— **取值必须与 `rules.STATUSES` 和招式 tag 名一致**。
# 2026-10-26 修：此前这里写的是 `"paralyzed"`，而招式 tag 落地的是 `"paralyze"`，
# 导致**麻痹完全不生效**（既不阻止攻击、也永不自动恢复），且单元测试手写
# `status.add("paralyzed")` 反而把错误拼写固化了 —— 真实对局里没有任何一条
# 路径会写入 `"paralyzed"`。现统一到 `"paralyze"`，并在 `rules.checkup` 里
# 兼容旧拼写（历史回放/外部构造的状态仍能被清除）。
_NO_ATTACK_STATUS = frozenset({"sleep", "paralyze"})


class Battle:
    """对战裁判（自管理字段；seed 驱动的确定性）"""

    # 类级默认值（v1.4）：测试与外部工具惯用 ``object.__new__(Battle)`` 手工装配
    # 战场，绕过 ``__init__``；把这两个字段做成类级默认 + 惰性实例化，使两种构造
    # 方式都安全（否则手工装配的实例会 AttributeError）。
    # 注意：``_ko_by_attack`` 的类级值**必须是 None**，可变默认值会在实例间共享。
    _ko_by_attack = None
    _ko_from_attack = False
    # Legacy Energy（2026-10-28）：其「少拿 1 张奖赏卡」每局只生效一次
    _legacy_used = False
    # 正在结算的招式（2026-10-29 第十九批）：条件
    # `own_active_extra_energy_at_least` 需要「本次招式费用」才能算「额外能量」。
    _resolving_attack = None
    # 伤害前阶段（2026-10-29 第二十五批）：本次招式在「Before doing damage」里
    # **实际弃掉的道具数**。`<status>:if_pre_tool` 依赖它判定（N's Joltik）。
    # 类级默认值同 `_ko_by_attack` 的理由 —— 测试常用 `object.__new__(Battle)`。
    _pre_tools_discarded = 0
    # S3 场地：全场只有 1 张（打出新的即顶替旧的）。类级默认值同 `_ko_by_attack`
    # 的理由 —— 测试常用 `object.__new__(Battle)` 手工装配，没有 `__init__`。
    stadium = None            # {"name": str, "tag": str, "owner": int} | None
    # S3：场地「每回合一次」的使用计数（按玩家；回合开始清零）
    _stadium_used = None      # list[int] 惰性创建，兼容 object.__new__ 装配

    def _ko_tr_flags(self) -> list:
        """按所属方计数的「**Team Rocket's** 宝可梦被招式击倒」标记

        第一百零九批（Team Rocket's Archer）：「only if any of your Team
        Rocket's Pokémon were Knocked Out during your opponent's last turn.」
        ⚠ 不能复用 `_ko_flags`（那是**任意**己方宝可梦）—— 用它会放宽门槛，
          让这张卡在本不该能打的时候能打（静默加强）。
        """
        v = getattr(self, "_ko_tr_by_attack", None)
        if v is None:
            v = [0, 0]
            self._ko_tr_by_attack = v
        return v

    def _ko_flags(self) -> list:
        """按宝可梦所属方计数的「被招式击倒」标记（惰性创建，实例隔离）"""
        v = self._ko_by_attack
        if v is None:
            v = [0, 0]
            self._ko_by_attack = v
        return v

    def __init__(self, deck_a: list, deck_b: list, seed: int = 0,
                 cfg: Optional[GameConfig] = None,
                 names: tuple = ("Player A", "Player B")):
        self.cfg = cfg or GameConfig()
        self.seed = seed
        self.rng = random.Random(seed)
        self.players: list = [
            PlayerState(name=names[0], deck=list(deck_a), prizes_left=self.cfg.prizes),
            PlayerState(name=names[1], deck=list(deck_b), prizes_left=self.cfg.prizes),
        ]
        self.current: int = 0
        self.turn_number: int = 0
        self.turn_counts: list = [0, 0]
        self.phase: str = "main"
        # 决策分解（S1a）：挂起中的选择（None = 无）。字段在 `__init__` 显式初始化，
        # 避免 `getattr(self, "_pending", None)` 在某些装配路径下读到**上一次**的值。
        self._pending: Optional[PendingChoice] = None
        self._defer_end_turn: bool = False
        # S2：延迟队列 + 事件订阅记账（每张卡每事件每回合 1 次）+ 换位快照
        self._queue = events.EffectQueue()
        self._watch_fired: dict = {}
        self._switch_snap = None
        self.finished: bool = False
        self.winner: Optional[int] = None
        self.win_reason: Optional[str] = None
        self.events: list = []
        # v1.4：上个**对手回合**中「被招式击倒」的标记（条件加伤用，如
        # 「if any of your Pokémon were Knocked Out ... during your opponent's
        # last turn, this attack does N more damage」）。
        # 语义：按**宝可梦所属方**计数；在该方回合开始时清零（见 _begin_turn）。
        self._ko_by_attack: list = [0, 0]
        # 结算上下文：当前这次击倒是否由「招式」造成（中毒/烧伤检查与混乱自伤不算）
        self._ko_from_attack: bool = False
        self._legacy_used: bool = False
        # S6a：**开局回合**标记 —— 由 `_setup_openings` 在真的发完起手/铺完场后置位。
        # 「先手第一回合不能攻击 / 不能打 Supporter」只对**走过 setup 的对局**成立
        # （没有 setup 的合成对局不存在「开局回合」，见 `GameConfig` 的注释）。
        self.setup_done: bool = False
        # S6b：挂起「取哪张奖赏卡」期间**不能再处理下一只被击倒的宝可梦**
        # （多只同时被击倒：先排队，等选择闭合后按序补做）。
        # 不入队就会在 `_open_choice` 处撞「不允许嵌套挂起」。
        self._ko_queue: list = []
        self._setup_openings()
        # 先手：硬币
        self.current = 0 if self.rng.random() < 0.5 else 1
        self._log("setup", {"first": self.current, "seed": seed,
                            "mulligan": [p.mulligan_count for p in self.players]})
        self._begin_turn()
        # S6a（2026-11-08）：**setup 上场的宝可梦当回合不能进化**（官方
        # Battle Academy 2024：「Neither player can evolve any of the Pokémon
        # that they played during setup」）。
        # 复用既有 `just_played` 语义、在 `_begin_turn` **之后**置位：
        #   · 先手 T1 → 仍带标记 → 不能进化（官方口径，不置位就是静默加强）；
        #   · 后手 T2 → 其 `_begin_turn` 已清零 → 可以进化（与实战一致）。
        for _pi in (0, 1):
            for _fp in self.players[_pi].all_field():
                _fp.just_played = True

    # ---------- 开局 ----------
    def _draw_with_mulligan(self, pi: int) -> None:
        p = self.players[pi]
        self.rng.shuffle(p.deck)
        while True:
            if len(p.deck) < self.cfg.opening_hand:
                raise ValueError(f"牌库过小（{len(p.deck)}）无法完成起手")
            for _ in range(self.cfg.opening_hand):
                p.hand.append(p.deck.pop())
            if any(isinstance(c, PokeRef) and c.pdef.stage == 0 for c in p.hand):
                break
            p.deck.extend(p.hand)
            p.hand.clear()
            p.mulligan_count += 1
            self.rng.shuffle(p.deck)

    def _setup_openings(self) -> None:
        for pi in (0, 1):
            self._draw_with_mulligan(pi)
            p = self.players[pi]
            basics = [c for c in p.basic_in_hand() if c.pdef.stage == 0]
            first = basics[0]
            p.hand.remove(first)
            p.active = make_pokemon(first.pdef)
            for extra in basics[1:]:
                if len(p.bench) >= self.cfg.max_bench:
                    break
                p.hand.remove(extra)
                p.bench.append(make_pokemon(extra.pdef))
        if self.cfg.mulligan_compensation:
            self._safe_draw(1, self.players[0].mulligan_count, source="mulligan_comp")
            self._safe_draw(0, self.players[1].mulligan_count, source="mulligan_comp")
        # S3（2026-11-08）：奖赏卡**真发牌**。官方 setup：从牌库取 6 张面朝下
        # 作为奖赏卡 —— 此前引擎「凭空计数」，牌库根本没少（那 6 张还能被抽到）。
        # 顺序放在放完战斗位/后备之后，与官方步骤一致，且不影响起手评估。
        if self.cfg.real_prizes:
            for _pi in (0, 1):
                zones.deal_prizes(self, _pi, self.cfg.prizes)
        self.setup_done = True
        # ⚠ 「setup 上场的宝可梦当回合不能进化」的标记**不能**在这里置
        # （见 `__init__` 里 `_begin_turn()` 之后那段）：`_begin_turn` 会把
        # 当前玩家场上的 `just_played` 清零，在这里置位等于白置。

    # ---------- 抽卡与通用 ----------
    @staticmethod
    def _to_discard(p: PlayerState, ref) -> str:
        """把一张牌送入弃牌堆（**文本层与类型化层同步推进**）

        返回入堆标签（英文数据键名）。所有弃牌都必须走这里，
        否则「弃牌堆回收」类效果（recover / 从弃牌堆检索）会取不到真实卡对象。
        同时维护 v1.4 的「本回合弃置计数」（卡面「discarded in this way」用）。
        """
        label = _card_label(ref)
        p.discard.append(label)
        p.discard_refs.append(ref)
        p.discarded_this_turn += 1
        if isinstance(ref, EnergyRef):
            p.discarded_energy_this_turn += 1
        return label

    @staticmethod
    def _rebuild_ref(ref):
        """把弃牌堆里的卡对象安全重建为可入手的 ref（避免多局共享实例）

        - PokeRef / TrainerRef 的 def 是 frozen dataclass → 直接换壳即可
        - EnergyRef 的 edef 也是 frozen → 同上
        """
        from .cards import EnergyRef, PokeRef, TrainerRef
        if isinstance(ref, PokeRef):
            return PokeRef(ref.pdef)
        if isinstance(ref, TrainerRef):
            return TrainerRef(ref.tdef)
        if isinstance(ref, EnergyRef):
            return EnergyRef(ref.edef)
        return ref

    def _safe_draw(self, pi: int, n: int, source: str = "draw") -> int:
        p = self.players[pi]
        if not p.deck and n > 0:
            self._finish(1 - pi, "deckout", {"victim": pi, "need": n, "source": source})
            return 0
        got = p.draw(n)
        self._log("draw", {"player": pi, "n": got, "source": source})
        # 官方**无手牌上限** → `max_hand = 0` 表示不设限（默认）；
        # 历史防御式上限（>0）仍保留用于对拍/压力测试。
        if self.cfg.max_hand > 0 and len(p.hand) > self.cfg.max_hand:
            over = p.hand[self.cfg.max_hand:]
            for c in over:
                self._to_discard(p, c)
            del p.hand[self.cfg.max_hand:]
            self._log("hand_overflow", {"player": pi, "dropped": len(over)})
        return got

    def _log(self, etype: str, data: dict) -> None:
        self.events.append({"turn": self.turn_number, "player": self.current,
                            "type": etype, **data})

    def _finish(self, winner: Optional[int], reason: str,
                data: Optional[dict] = None) -> None:
        if self.finished:
            return
        self.finished = True
        self.winner = winner
        self.win_reason = reason
        self._log("finish", {"winner": winner, "reason": reason, **(data or {})})

    # ---------- 回合流转 ----------
    def _begin_turn(self) -> None:
        if self.finished:
            return
        self.turn_number += 1
        if self.turn_number > self.cfg.max_turns:
            self._finish(None, "max_turns_draw", {"turns": self.turn_number})
            return
        self.turn_counts[self.current] += 1
        p = self.players[self.current]
        self.phase = "main"
        events.reset_turn_counters(self)      # S2：订阅「每回合 N 次」记账归零
        p.attached_energy_this_turn = 0
        p.supporter_used = 0
        p.supporter_name = ""                     # S3：在场地上判定的支援者卡名
        p.discarded_this_turn = 0                 # v1.4「in this way」按回合重置
        p.discarded_energy_this_turn = 0
        p.attacks_this_turn = 0                   # 第六十一批：续攻额度按回合重置
        # 回合历史（2026-10-28）：「本回合」滚到「上回合」，供
        # 「during your last turn」类条件（用过某招 / 对手上回合取了几张奖赏）
        p.last_turn_attacks = list(p.turn_attacks)
        p.turn_attacks = []
        p.last_turn_ancient_attackers = list(p.turn_ancient_attackers)
        p.turn_ancient_attackers = []
        p.prizes_taken_last_turn = p.prizes_taken_this_turn
        p.prizes_taken_this_turn = 0
        self._refresh_stadium_hp()                # S3：场地对上限的修正在新回合重算
        # 场地「每回合一次」按**回合归属方**重置（不是使用者）
        self._stadium_used_flags()[self.current] = 0
        for fp in p.all_field():          # E2/E3: 新回合解除"刚放置"并重置特性
            fp.just_played = False
            fp.ability_used.clear()
            # 受击历史（第九十九批）：把「本回合累计」滚成「对手上回合累计」。
            # ⚠ 顺序不能反 —— 先清零再快照会让 Mega Heracross ex 永远读到 0。
            fp.dmg_taken_prev = fp.dmg_taken_mark
            fp.dmg_taken_mark = 0
        # v1.4 护盾：语义为「（在对手的）下个回合内有效」→ 该玩家**本回合开始**时失效。
        # 若在 A 的回合给 A 上盾，则 B 回合期间盾仍在，A 再次开始时清除（正确）。
        for fp in p.all_field():
            if fp.shield_reduce or fp.shield_prevent_all:
                self._log("shield_expire", {"player": self.current,
                                            "target": fp.name})
            fp.shield_reduce = 0
            fp.shield_prevent_all = False
            fp.shield_prevent_effects = False
            fp.shield_prevent_qual = ""
            fp.shield_no_weakness = False
        # v1.4「上回合被击倒」标记：该方回合开始即清零（卡面为「对手的上个回合」）
        self._ko_flags()[self.current] = 0
        self._ko_tr_flags()[self.current] = 0
        if not p.has_pokemon:
            self._finish(1 - self.current, "no_pokemon", {"loser": self.current})
            return
        # Festival Grounds：「附着了能量的宝可梦恢复所有特殊状态」（状态检查前）
        self._recover_stadium_statuses()
        if p.active and not p.active.is_ko:
            # Pokémon Checkup 改写（2026-10-28 第十三批）：场上常驻
            # `passive:checkup_plus:*` 追加、招式设置的 `checkup_set` 取代。
            _bonus, _override = self._checkup_mods(self.current)
            for ev in status_checkup(p.active, self.rng, bonus=_bonus,
                                     override=_override):
                self._log("checkup", {**ev, "player": self.current})
            self._stadium_checkup_extra(p.active)
        # Checkup **群体**放置（2026-10-28 第十四批）：Froslass / Tyranitar。
        # 每个来源只在**其拥有者**的回合开始时结算一次，避免一轮内被算两次。
        self._apply_checkup_spreads(self.current)
        # 统一击倒结算（状态 / 场地追加 / 群体放置 任一来源，判定前先全部放置）
        for _side in (self.current, 1 - self.current):
            for _fp in list(self.players[_side].all_field()):
                if _fp is not None and _fp.is_ko:
                    self._resolve_ko(_side, _fp)
                    if self.finished:
                        return
        first_round = self.turn_counts[self.current] == 1
        if not (first_round and self.cfg.first_turn_no_draw):
            self._safe_draw(self.current, 1)
            if self.finished:
                return
        self._log("turn_start", {"player": self.current, "turn": self.turn_number})

    def _end_turn(self) -> None:
        if self.finished:
            return
        # 决策分解（S1b）：招式结算里挂起的「可选代价」还没选完时**不得换手** ——
        # 否则选择会落到对手回合（owner 错、窗口错），对局结果被静默改动。
        # 由 `_close_choice` 在闭合后补做这次 `_end_turn`。
        if getattr(self, "_pending", None) is not None:
            self._defer_end_turn = True
            self._log("turn_end_deferred", {"player": self.current,
                                            "choice": self._pending.kind})
            return
        # 回合结束检查：麻痹只在本回合结束才恢复（见 rules.status_checkup 的 phase 说明）。
        # 放在切换 current **之前**，作用于「刚结束回合的这一方」。
        cur = self.players[self.current]
        if cur.active is not None and not cur.active.is_ko:
            for ev in status_checkup(cur.active, self.rng, phase="end"):
                self._log("checkup", {**ev, "player": self.current})
        # 2026-10-28：Ignition Energy「discard it at the end of your turn」
        special_energy.discard_at_end_of_turn(self, cur)
        # S2（2026-11-08）：**唯一**的延迟效果结算点。放在 `turn_end` 日志之前、
        # 换手之前 —— 卡面写的是「At the end of **this** turn」，此时 current
        # 仍是结束回合的这一方，`_run_queued_tag` 的作用域天然正确。
        # S2c：先发 `turn_end` 事件（Powerglass 一类「回合末」被动道具挂在这里），
        # 再结算队列（一次性延迟效果）。
        events.emit(self, "turn_end", {"player": self.current,
                                       "turn_owner": self.current})
        if self.finished:
            return
        events.resolve_turn_end(self, self.current)
        if self.finished:
            return
        # S2c：回合末的被动道具可能**挂起选择**（Powerglass「you may attach …」）。
        # 此时绝不能换手 —— 否则选择会落到对手回合（owner 错、窗口错）。
        # 与招式侧同一手法：置 `_defer_end_turn`，由 `_close_choice` 补做换手。
        if getattr(self, "_pending", None) is not None:
            self._defer_end_turn = True
            self._log("turn_end_deferred", {"player": self.current,
                                            "choice": self._pending.kind})
            return
        self._log("turn_end", {"player": self.current})
        self.current = 1 - self.current
        self._begin_turn()

    # ---------- 合法动作 ----------
    def legal_actions(self, pi: Optional[int] = None) -> list:
        pi = self.current if pi is None else pi
        if self.finished or pi != self.current:
            return []
        # 决策分解（S1a）：挂起期间合法动作**只有**选择项与「结束选择」。
        if self.phase == "choice":
            return self._choice_legals()
        if self.phase != "main":
            return []
        p = self.players[pi]
        opp = self.players[1 - pi]
        acts = []
        # 上基础
        for i, c in enumerate(p.hand):
            if isinstance(c, PokeRef) and c.pdef.stage == 0:
                if len(p.bench) < self.cfg.max_bench:
                    acts.append({"verb": "play", "index": i,
                                 "name": c.pdef.name,
                                 "name_zh": c.pdef.name_zh,
                                 "display": c.pdef.display})
        # 附能量
        if p.energy_in_hand() > 0 and p.attached_energy_this_turn == 0 \
                and p.has_pokemon:
            choices = _hand_energy_kinds(p)
            for tpos in [0] + [bi + 1 for bi in range(len(p.bench))]:
                tgt = None if tpos == 0 else p.bench[tpos - 1]
                if tpos == 0:
                    tgt = p.active
                    if tgt is None:
                        continue
                for ch in choices:
                    # 附着限制（火箭队的能量）：非法目标不列，避免「合法但必然抛错」
                    if ch["edef"].is_special \
                            and not special_energy.attach_allowed(ch["edef"], tgt):
                        continue
                    a = {"verb": "attach", "target": tpos}
                    if len(choices) > 1:
                        # 手牌有多种能量时必须能选（特殊能量与基本能量语义不同）
                        a["energy"] = ch["index"]
                        a["energy_name"] = ch["edef"].display
                    acts.append(a)
        # 招式 / 撤退（总能量计无色门槛）
        if p.active is not None:
            blocked = _NO_ATTACK_STATUS & p.active.status
            # 「… this Pokémon can't attack」(第一百一十二批，Slaking ex「Born to
            # Slack」/ Team Rocket's Mewtwo ex「Power Saver」）—— 卡面写的是
            # **能不能出招**的条件，不是某一招的附加效果，故在动作生成处整只拦下。
            if _passive_attack_blocked(self, p.active):
                blocked = True
            # S6a（官方）：先手第一回合不能攻击。**必须在这里也拦** —— 只拦
            # `_do_attack` 会让 AI 反复提出一个必然被拒的动作（对局噪声 +
            # 训练信号被污染），正是「门禁必须两处同挡」的既有纪律。
            # ⚠ 例外卡（`use_any:first_turn`）是**按招式**放行的，所以不能把整只
            #   `blocked`（那会连例外招式一起拦掉）—— 见下面枚举处的逐招判断。
            _first_turn_banned = (self.cfg.first_turn_no_attack
                                  and self._opening_turn())
            turn_count = self.turn_counts[pi]
            # 按附着能量数全队禁攻（第一百三十四批，Walrein「Frigid Fangs」）：
            # 卡面「Pokémon that have 2 or less Energy attached can't use attacks」
            # —— 判据是**这只身上**的能量数，被封锁方为整名玩家（含新上场者）。
            _eab = getattr(p, "atk_energy_block_n", 0)
            if _eab and getattr(p, "atk_energy_block_until", 0) > turn_count \
                    and p.active.total_energy() <= _eab:
                blocked = True
            if not blocked:
                for ai, atk in enumerate(p.active.pdef.attacks):
                    # 跨回合攻击封锁（`attack_lock:*`）：即使能量足够也不得出手
                    if p.active.attack_locked(atk.name, turn_count):
                        continue
                    # 招式**静态使用条件**（第一百零七/一百零八批）：
                    #   `use_only:<cond>` 只在条件成立时可用（Scream Tail ex
                    #   「only if you go second, and only during your first turn」）
                    #   `use_not:<cond>`  条件成立时不可用（Terapagos ex
                    #   「If you go second, you can't use this attack during
                    #   your first turn」）
                    # 不在合法动作表里剔除，AI 就会在首回合打出规则上不可用的招式。
                    if not _attack_usable(self, atk):
                        continue
                    # 先手第一回合：只有带 `use_any:first_turn` 的招式可以出手
                    if _first_turn_banned and not _first_turn_attack_ok(atk):
                        continue
                    _scope = _borrow_scope(atk)
                    _opts = self._borrow_options(p, opp, p.active, atk)
                    if _scope is not None:
                        # 借用招式：必须逐个列出「可借哪个招式」（裸动作 damage=0，
                        # 用了等于空过 —— 绝不列出）
                        _pay = self._can_pay_attack(p.active, atk)
                        for _o in _opts:
                            if not _pay:
                                continue
                            acts.append(self._borrow_action(ai, atk, _o))
                        # S5：卡面写「**You may** choose an attack …」时可以放弃。
                        # 不列裸动作 = 引擎替玩家决定「一定借」（S1 不变量）；
                        # 也保证「牌库顶 10 张里没有宝可梦」时这一招仍可打出。
                        if _pay and _borrow_optional(_scope):
                            acts.append(_plain_attack_action(ai, atk))
                        continue
                    if self._can_pay_attack(p.active, atk):
                        acts.append(_plain_attack_action(ai, atk))
                    # Relicanth（`passive:prev_attacks`）：额外列出进化前形态的招式
                    for _o in _opts:
                        if not self._can_pay_attack(p.active, _o["atk"]):
                            continue
                        acts.append(self._borrow_action(ai, atk, _o))
                if not p.active.retreat_locked(turn_count) \
                        and not getattr(p.active, "fossil", False) \
                        and p.active.total_energy() >= _retreat_cost(
                            p.active, self) and len(p.bench) > 0:
                    acts.append({"verb": "retreat", "bench": 0})
        # E2 进化：手牌 x → 场上同名 evolves_from 目标
        for hi, c in enumerate(p.hand):
            if isinstance(c, PokeRef) and c.pdef.stage > 0 \
                    and not _no_evolve_into(c.pdef) \
                    and p.evolution_lock_until <= self.turn_counts[pi]:
                for tpos, tgt in _field_iter(p):
                    if tgt is not None \
                            and (not tgt.just_played
                                 or self._same_turn_evolve_ok(tgt, c, pi)) \
                            and tgt.pdef.name == c.pdef.evolves_from \
                            and tgt.stage + 1 == c.pdef.stage:
                        acts.append({"verb": "evolve", "hand": hi,
                                     "target": tpos,
                                     "name": c.pdef.name,
                                     "name_zh": c.pdef.name_zh,
                                     "display": c.pdef.display,
                                     # 被进化对象（场上）的名称，便于生成可读指令
                                     "onto": tgt.pdef.display,
                                     "onto_name": tgt.pdef.name})
        # E4 训练家
        acts += self._trainer_legals(p, opp)
        # 化石卡：「At any time during your turn, you may discard this card from
        # play.」→ 己方场上任意位置的化石都可弃置。战斗位且无替补时不列
        # （弃掉会直接造成「场上无宝可梦」的判负，属引擎强制的非法动作）。
        for tpos, fp in _field_iter(p):
            if fp is None or not getattr(fp, "fossil", False):
                continue
            if tpos == 0 and not p.bench:
                continue
            acts.append({"verb": "discard_fossil", "target": tpos,
                         "name": fp.name, "name_zh": fp.pdef.name_zh,
                         "display": fp.pdef.display})
        # E3 特性（field 每回合一次 / hand 弃卡发动）
        acts += self._ability_legals(p, opp)
        acts.append({"verb": "pass"})
        return acts

    def _ability_legals(self, p: PlayerState, opp: PlayerState) -> list:
        out = []
        for tpos, fp in _field_iter(p):
            for ab in fp.pdef.abilities:
                if ab.location != "field" or ab.name in fp.ability_used:
                    continue
                # 静态封印（「场上的宝可梦没有特性」等，效果族提供）
                if families.ability_blocked(self, fp, ab):
                    continue
                # 决策分解型特性的**动作前置条件**（S1c）：手牌/牌库为空时无法交换，
                # 必须**不进**合法动作表 —— 否则 AI 会反复尝试必然抛错的动作。
                if ab.tag.startswith("choose:swap_hand_top") \
                        and (not p.hand or not p.deck):
                    continue
                # 发动代价付不出 → 不可发动（2026-11-04 第四十九批）。放在这里而不是
                # 等 `_do_ability` 抛错：否则 AI 会把「打不出的动作」当成合法动作。
                if getattr(ab, "cost", "") and not _ability_cost_ok(p, fp, ab.cost):
                    continue
                # 被动型 field 特性不进合法动作表（由结算路径自动触发）
                if ab.tag.startswith(("ko_draw:", "weak_change:", "evolve_attach:",
                                      "evolve_act:", "on_bench_act:",
                                      # S2：`on_event:` 是**订阅**（等别人触发），
                                      # 不是可主动发动的特性 —— 进 legals 会让随机
                                      # 对局报「未知 field tag」。
                                      "on_event:",
                                      "passive:", "on_damaged:",
                                      "on_ko:", "ko_prize_plus:",
                                      # 第五十批：被击倒时的奖赏减少也是**被动**钩子
                                      # （在 `_resolve_ko` 里读），不可主动发动。
                                      "ko_prize_minus:")):
                    continue
                # 效果族纯静态钩子（无 act 一次性动作，如 ability_blocked）→ 被动，
                # 不进合法动作表（封锁由结算路径在 ability_blocked 自动施加）
                if ab.tag.startswith("fx:") and not families.has_act(ab.tag):
                    continue
                if ab.tag.startswith("recon:") and not p.deck:
                    continue
                # S3：Misty's Psyduck「Flustered Leap」——「if this Pokémon is on
                # your Bench」是**发动条件**（战斗位不能发），且牌库为空时
                # 「if you do」根本不会成立 → 两者都必须从合法动作表里摘掉。
                if ab.tag.startswith("deck_bottom_discard:"):
                    if not p.deck or tpos == 0:
                        continue
                # 全牌库检索（`search_pokemon:N` / `search_trainer:N` /
                # `search_any:N` / `search_energy:N`）—— 牌库无对应类别则不可发动
                if ab.tag.startswith("search_"):
                    if not _zone_search_available(p, ab.tag):
                        continue
                if ab.tag.startswith("draw:"):
                    if not p.deck:
                        continue
                    # B3：条件式抽牌（`draw:N:if:<cond>`）条件不满足则不可发动
                    _dn, _dc = _draw_spec(self, ab.tag)
                    if _dc is not None and not _eval_condition(self, _dc):
                        continue
                if ab.tag.startswith("draw_both:") and not p.deck:
                    continue
                if ab.tag.startswith("opp_shuffle_draw:"):
                    if "if_hand" in ab.tag.split(":")[2:] and not opp.hand:
                        continue
                if ab.tag.startswith("switch_self"):
                    # 后备特性自换位：持有者在战斗位时不可发动
                    if tpos == 0 or p.active is None:
                        continue
                    _sp = ab.tag.split(":")
                    _sc = (":".join(_sp[2:])
                           if len(_sp) > 2 and _sp[1] == "if" else None)
                    if _sc is not None and not _eval_condition(self, _sc):
                        continue
                if ab.tag.startswith("switch_choose:"):
                    # 「switch 1 of your Benched {D} Pokémon, except any X, with
                    #  your Active Pokémon」（第一百一十一批，Pecharunt ex）——
                    # 需玩家指定换上哪只：逐个**合法**后备位置各列一条。
                    # ⚠ 不能只列一条「后备第一只」：换错一只等于位置全乱，
                    #   而且属性/例外筛选会让部分位置非法。
                    _sc2 = ab.tag.split(":")
                    _sty2 = _sc2[1].lower() if len(_sc2) > 1 else ""
                    _exc2 = (_sc2[_sc2.index("except") + 1]
                             if "except" in _sc2 else None)
                    if p.active is None:
                        continue
                    _ok_bi = [i for i, x in enumerate(p.bench)
                              if (_sty2 in ("any", "")
                                  or str(x.pdef.ptype).lower() == _sty2)
                              and not (_exc2 and _norm_ex_key(x) == _exc2)]
                    if not _ok_bi:
                        continue
                    for _bi2 in _ok_bi:
                        out.append({"verb": "ability", "name": ab.name,
                                    "name_zh": ab.name_zh,
                                    "display": ab.name_zh or ab.name,
                                    "pos": tpos, "tag": ab.tag,
                                    "bench": _bi2})
                    continue
                if ab.tag.startswith(("discard_opp_random:", "shuffle_opp_hand:")):
                    if not opp.hand:
                        continue
                if ab.tag.startswith("gust") and not opp.bench:
                    # 特性侧拉后备（第五十二批，Meowstic「Beckoning Tail」）
                    continue
                if ab.tag.startswith("discard_opp_to:"):
                    if len(opp.hand) <= int(ab.tag.split(":")[1]):
                        continue
                if ab.tag.startswith("energy_move:"):
                    # 特性侧能量移动：持有者自身有能量且己方有后备（当前池内
                    # 只有「自身 → 后备」形态；Dewgong 的「任意次」仍拒绝）
                    _ep = ab.tag.split(":")
                    _et = (_ep[_ep.index("type") + 1]
                           if "type" in _ep else None)
                    if not p.bench:
                        continue
                    if _et:
                        if fp.energy_of(_et) <= 0:
                            continue
                    elif fp.total_energy() <= 0:
                        continue
                    # 每个后备位置都列一条（缺省 target 会落到第一个，但显式列出
                    # 才能让 AI 选择目标）
                    for bi in range(len(p.bench)):
                        out.append({"verb": "ability", "name": ab.name,
                                    "name_zh": ab.name_zh,
                                    "display": ab.name_zh or ab.name,
                                    "pos": tpos, "tag": ab.tag,
                                    "target": bi + 1})
                    continue
                if ab.tag.startswith("bounce:"):
                    _bp = ab.tag.split(":")
                    _bscope = _bp[2] if len(_bp) > 2 else ""
                    if _bscope == "bench" and not p.bench:
                        continue
                    if _bscope == "choose" and not _bounce_legal_targets(p):
                        continue
                    if len(_bp) > 1 and _bp[1] == "discard_to_bench" and \
                            not _bounce_discard_candidates(
                                p, ab.tag, self.cfg.max_bench):
                        continue
                if ab.tag.startswith("coin:"):
                    # 特性侧抛币（第四十四批，Dustox）：正面要收回**对手战斗位**
                    # 的 1 个能量；对手那里没有能量时空转 → 不列动作。
                    if "bounce:energy:opp_active" in ab.tag \
                            and (opp.active is None
                                 or opp.active.total_energy() <= 0):
                        continue
                if ab.tag.startswith("discard_tool:"):
                    _sc = (ab.tag.split(":")[2]
                           if len(ab.tag.split(":")) > 2 else "both")
                    if _sc == "opp_active":
                        _pool = [opp.active] if opp.active is not None else []
                    elif _sc == "opp":
                        _pool = opp.all_field()
                    elif _sc == "own":
                        _pool = p.all_field()
                    else:
                        _pool = p.all_field() + opp.all_field()
                    if not any(fp.tool for fp in _pool):
                        continue
                if ab.tag == "discard_stadium" and not self.stadium:
                    continue
                if ab.tag.startswith("attach_each_bench:"):
                    # 每只后备各 per 张（第三十八批）：无后备 / 无能量 → 不列动作
                    try:
                        _bn, _bet, _bz = _parse_attach_each_bench_tag(ab.tag)
                        if not p.bench or not _zone_energy_available(p, _bet, _bz):
                            continue
                    except ValueError:
                        continue
                if ab.tag.startswith("attach_spread_multi:"):
                    # 双色 or 1 of each（第三十九批）：两类都无能量 → 空转，不列
                    try:
                        _mp = str(ab.tag).split(":")
                        _msc = "any"
                        if "target" in _mp:
                            _mi = _mp.index("target")
                            _msc = _mp[_mi + 1] if _mi + 1 < len(_mp) else ""
                        if not _spread_targets(p, _msc):
                            continue
                        if not (_zone_energy_available(p, _mp[2], _mp[5])
                                or _zone_energy_available(p, _mp[4], _mp[5])):
                            continue
                    except ValueError:
                        continue
                if ab.tag.startswith("attach_spread:"):
                    # 多张自由分配附着（2026-11-02 第三十七批补合法动作表）：
                    # 无可附能量 / 无合法目标 → 不列动作。带 `:selfko` 时这一步
                    # 尤其重要 —— 否则 AI 会挑一个「附 0 张还把自己爆掉」的动作。
                    try:
                        _sn, _set, _szn, _ssc, _sko = \
                            _parse_attach_spread_tag(ab.tag)
                        _sp_tgts = _spread_targets(p, _ssc)
                        if not _sp_tgts:
                            continue
                        if _sko and not [x for x in _sp_tgts if x is not fp]:
                            # 自爆且**只有自己**是合法目标 → 能量附给自己后立刻
                            # 随自爆进弃牌堆，净效果为零还白丢 1 张奖赏 → 不列
                            continue
                        if not _zone_energy_available(p, _set, _szn):
                            continue
                    except ValueError:
                        # 形态非法 → 不列（宁可不可用，也不静默走错路径）
                        continue
                if ab.tag.startswith("attach_energy:"):
                    _n, _et, _zn = _parse_attach_tag(ab.tag)
                    if not _zone_energy_available(
                            p, _et, _zn, allow_special=":anyenergy" in ab.tag):
                        continue
                    # 附能目标不可满足（无后备 / 无指定属性的后备）→ 不列动作
                    if _attach_target_fp(ab.tag, p, {}, fp) is None:
                        continue
                if ab.tag.startswith("move_damage:"):
                    if fp.damage < 10 or opp.active is None:
                        continue
                # 主动施加状态（`burn`/`poison`/…）：对手无战斗位则不可发动
                if ab.tag in STATUSES and (opp.active is None or opp.active.is_ko):
                    continue
                out.append({"verb": "ability", "name": ab.name,
                            "name_zh": ab.name_zh,
                            "display": ab.name_zh or ab.name,
                            "pos": tpos, "tag": ab.tag})
        for hi, c in enumerate(p.hand):
            if isinstance(c, PokeRef):
                for ab in c.pdef.abilities:
                    if ab.location != "hand":
                        continue
                    if ab.tag.startswith("cursed:"):
                        for tpos in range(1 + len(opp.bench)):
                            out.append({"verb": "ability", "name": ab.name,
                                        "name_zh": ab.name_zh,
                                        "display": ab.name_zh or ab.name,
                                        "hand": hi, "tag": ab.tag,
                                        "target": tpos})
        return out

    def _opening_turn(self) -> bool:
        """当前是否处于**先手玩家的开局回合**（S6a）

        判据两条**同时**成立：
          · `setup_done` —— 这局真的走过开局（起手/铺场/发奖赏）。合成对局
            （单元测试直接摆场）没有「开局回合」这个概念，规则不适用；
          · `turn_number == 1` —— 全剧第 1 回合，只可能是先手玩家的第一个回合。
        ⚠ 不能用 `turn_counts[current] == 1`：后手玩家的第一个回合是**第 2 回合**，
        官方允许其攻击与打支援者（旧实现这么写，旗标打开后会误锁后手）。
        """
        return bool(getattr(self, "setup_done", False)) and self.turn_number == 1

    def _trainer_legals(self, p: PlayerState, opp: PlayerState) -> list:
        out = []
        for i, c in enumerate(p.hand):
            if not isinstance(c, TrainerRef):
                continue
            full_tag, kind = c.tdef.tag, c.tdef.kind
            tag = full_tag
            # S2（2026-11-08）：**复合 tag**（`a|b`）的合法性只看**第一段** ——
            # 第一段合法就能打出；后续段是同一张卡的追加效果，不该把卡变成摆设。
            # 动作里仍带**完整** tag（`base["tag"]`），由 `_do_trainer` 逐段执行。
            # `coin:` / `coin_flip:` 的 `|` 是同一枚币的两面，不能拆。
            if "|" in tag and not tag.startswith(("coin:", "coin_flip:")):
                tag = tag.split("|")[0]
            # 跨回合手牌禁打（Budew / Frillish / Galvantula ex 的 Item 封锁）
            # 官方规则中 Pokémon Tool 属于 Item，故一并封锁。
            if kind in ("item", "tool") \
                    and p.item_lock_until > self.turn_counts[self.current]:
                continue
            if kind == "supporter" and p.supporter_used:
                continue
            # S6a：先手第一回合不能打 Supporter —— 合法动作表同步拦
            # （只拦派发侧会让 AI 反复尝试 / 人类看到不可用的选项）
            if kind == "supporter" and self.cfg.first_turn_no_supporter \
                    and self._opening_turn() and not _card_first_turn_ok(c):
                continue
            # 跨回合手牌禁打（第一百零七批）
            _tc = self.turn_counts[self.current]
            if kind == "supporter" and getattr(p, "supporter_lock_until", 0) > _tc:
                continue
            if kind == "stadium" and getattr(p, "stadium_lock_until", 0) > _tc:
                continue
            if _is_acespec(c) and getattr(p, "acespec_lock_until", 0) > _tc:
                continue
            # 静态禁令（「对手不能从手牌打出物品卡」等）
            if families.blocked_play(self, p, c):
                continue
            # 打出代价（2026-11-02 第三十六批）：付不出就**不进**合法动作表。
            # 卡面写的是「You can use this card **only if** you discard …」——
            # 这是**打出条件**，不是「打了以后才发现付不出」。若放进候选表，
            # AI 会反复尝试一张必然失败的卡，且「打得出」本身已是规则错误。
            try:
                if not _play_cost_ok(p, getattr(c.tdef, "play_cost", "")):
                    continue
            except ValueError:
                # 代价形态不认识 → 不收（宁可这张卡不可用，也不免费放行）
                continue
            # 打出**前置条件**（2026-11-04 第五十九批）：「You can use this card
            # only if …」。与上面 `play_cost` 相反 —— 这里**没有状态变化**，
            # 只是能不能打。不满足时该卡不进合法动作表，否则 AI 会反复尝试一张
            # 必然失败的卡，而「打得出」本身就已是规则错误。
            # ⚠ 复用既有的 `Battle._play_gate_ok`（`recon:` 那条路也走它）：
            # 早先只把 gate 塞在 tag 的 `:gate:` 段里，训练家卡级没有统一入口，
            # 于是同一句卡面在不同族里被实现两遍（一处漏改就静默放行）。
            if not self._play_gate_ok(
                    p, getattr(c.tdef, "play_gate", "")):
                continue
            # 统一带上标识（英文键 name / 中文 name_zh / 展示名 display / tag），
            # 供前端、AI 与「按名称下达指令」（engine.commands.find_action）使用。
            base = {"verb": "trainer", "hand": i,
                    "name": c.tdef.name, "name_zh": c.tdef.name_zh,
                    "display": c.tdef.display, "tag": full_tag, "kind": kind}
            if tag == "poffin":
                if len(p.bench) < self.cfg.max_bench:
                    out.append(dict(base))
            elif tag == "ultra_ball":
                if len(p.hand) >= 3:   # 自身 + 弃 2
                    out.append(dict(base))
            elif tag.startswith("choose:"):
                # 决策分解（S1a）：打出后**挂起选择**。合法性只取决于上面的
                # `play_gate`（Naveen 是「手牌 < 5」）；不在这里按「有没有可弃的牌」
                # 过滤 —— 卡面允许弃 0 张（"any number"），过滤掉会漏掉合法对局。
                out.append(dict(base))
            elif tag == "hammer":
                if opp.active is not None and opp.active.total_energy() > 0:
                    out.append(dict(base))
            elif tag == "boss":
                if opp.bench:
                    for bi in range(len(opp.bench)):
                        out.append({**base, "target": bi})
            elif tag.startswith("shuffle_draw_split:"):
                # 双方洗回后**各抽不同张数**（第五十九批，Unfair Stamp）：
                # 洗回后牌库必有牌，只要不是「手牌空且牌库空」就可用。
                if p.deck or len(p.hand) > 1:
                    out.append(dict(base))
            elif tag.startswith("research:") or tag.startswith("shuffle_draw:"):
                # 抽牌型支援者（Professor's Research / Iono）：需牌库非空
                if p.deck:
                    out.append(dict(base))
            elif tag == "switch_self":
                # 自换位（Switch）：需有后备
                for bi in range(len(p.bench)):
                    out.append({**base, "target": bi})
            elif tag.startswith("switch_draw_to:"):
                # 「换位并抽到 N」（第五十一批，Surfer）：同样需要后备
                for bi in range(len(p.bench)):
                    out.append({**base, "target": bi})
            elif tag.startswith("status_multi:"):
                # 多状态（Dangerous Laser）：无目标，对手有战斗位即可
                if opp.active is not None and not opp.active.is_ko:
                    out.append(dict(base))
            elif tag == "gust_confuse":
                # 拉对手后备 + 混乱（Lisia's Appeal）：候选与 gust 相同
                for bi in range(len(opp.bench)):
                    out.append({**base, "target": bi})
            elif tag.startswith("opp_hand:"):
                # 对手手牌操作（2026-10-30 第三十二批）：对手手里**没有**符合
                # 筛选条件的牌时，这张卡打出去只会「什么都不发生」——必须从
                # 合法动作表里摘掉，否则 AI 会反复尝试一张空转的卡。
                if any(_opp_hand_matches(c, _parse_opp_hand_tag(tag)[1])
                       for c in opp.hand):
                    out.append(dict(base))
            elif tag.startswith("recon:"):
                # 检视牌库顶（2026-10-30 第二十批）：牌库非空且**前置条件满足**才可打出。
                # 条件不满足时这张卡必须**不出现在合法动作表**里 —— 否则 AI 会反复
                # 尝试一张打不出的卡，且「打得出」本身就是规则错误。
                if p.deck and self._recon_gate_ok(p,
                                                  _parse_recon_tag(tag)["gate"]):
                    out.append(dict(base))
            elif tag.startswith("recover:"):
                # 弃牌堆回收（Night Stretcher）：需弃牌堆有可回收卡
                if tag.split(":")[1] == "heads":
                    # B3：`recover:heads:N` 数量由硬币决定，无目标可选
                    if p.discard_refs:
                        out.append(dict(base))
                else:
                    cands = _recover_candidates(p, tag)
                    for di in cands:
                        out.append({**base, "target": di})
            elif tag.startswith("discard_search_energy:"):
                # 弃 1 手牌 → 检索 n 能量（Earthen Vessel）：检索源是**牌库**
                if len(p.hand) >= 2 and _zone_energy_available(p, None, "deck"):
                    out.append(dict(base))
            elif tag.startswith("search_energy:"):
                # 检索 n 张能量（Crispin）：检索源是**牌库**；支持 :type:<c> 筛选
                _se = tag.split(":")
                _et = (_se[3].lower()
                       if len(_se) > 3 and _se[2] == "type" else None)
                if _zone_energy_available(p, _et, "deck"):
                    out.append(dict(base))
            elif tag == "candy":
                # 跳阶进化（Rare Candy）：手牌有 stage>=2 且场上同名链基础/一阶
                if _candy_legals(p):
                    out.append(dict(base))
            elif tag.startswith(("search_pokemon:", "search_trainer:",
                                 "search_any:")):
                # 通用检索（检索 n 张指定类别入手）——牌库需有可检索对象
                if _deck_has_kind(p, _search_kind_of(tag), _search_filter(tag)):
                    out.append(dict(base))
            elif tag.startswith("coin_search:"):
                # S5（TR Great Ball）：正面/反面找**不同 stage** 的同一阵营宝可梦
                # → 只要牌库里还有该阵营宝可梦就打得出去（不必两侧都有，
                # 反面对应那一侧为空时只是检索失败，卡面写的是 up to 语义）。
                _csr = str(tag).split(":")
                _cf = {}
                _i = 3
                while _i + 1 < len(_csr):
                    if _csr[_i] in ("category", "type", "hp", "resistance"):
                        _cf[_csr[_i]] = _csr[_i + 1]
                    _i += 2
                if _cf and _deck_has_kind(p, "pokemon", _cf):
                    out.append(dict(base))
            elif tag.startswith("stadium:"):
                # S3 场地：无目标，手上有就能打（全场唯一，顶替旧场地）
                out.append(dict(base))
            elif tag.startswith("fossil:"):
                # 化石卡：当作基础宝可梦**铺到后备**（卡面即「Play this card as if
                # it were a N-HP Basic Pokémon」）；后备已满则不可打出
                if len(p.bench) < self.cfg.max_bench:
                    out.append(dict(base))
            elif tag.startswith("tool:"):
                # S2 道具：列出**尚未附着道具**的己方位置（每只最多 1 张）
                targets = []
                if p.active is not None and not p.active.tool:
                    targets.append(0)
                targets += [i + 1 for i, fp in enumerate(p.bench) if not fp.tool]
                for tpos in targets:
                    out.append(dict(base, target=tpos))
            elif tag.startswith("on_event:") and kind == "tool":
                # S2c：订阅式道具可以附着（每只最多 1 张，与 tool: 同规则）
                targets = []
                if p.active is not None and not p.active.tool:
                    targets.append(0)
                targets += [i + 1 for i, fp in enumerate(p.bench) if not fp.tool]
                for tpos in targets:
                    out.append(dict(base, target=tpos))
            elif tag.startswith("ko_prize_minus:"):
                # 第八十五批的 Lillie's Pearl：**kind 是 tool、tag 却不是 `tool:`**
                # （引擎按 `fp.tool.startswith("ko_prize_minus:")` 读被动奖赏修正）。
                # 2026-11-08 S3 发现：`_do_trainer` 只认 `tool:` 前缀 → 这张卡
                # 打出去会抛「未知训练家 tag」，合法动作表也不列它 = **彻底死卡**
                # （在池内、可被抽到、却无法使用）。
                targets = []
                if p.active is not None and not p.active.tool:
                    targets.append(0)
                targets += [i + 1 for i, fp in enumerate(p.bench) if not fp.tool]
                for tpos in targets:
                    out.append(dict(base, target=tpos))
            # ---- B3（2026-10-28）：训练家侧抽牌 / 回复 / 附能 / 弃牌 / 磨牌 ----
            # 这些 tag 的**派发**早已存在（见 `_do_trainer`），但合法动作表一直
            # 没有对应分支 → AI 永远选不到这些卡（对局里表现为「摆设」）。
            elif tag.startswith("draw_both:") or tag.startswith("opp_shuffle_draw:"):
                out.append(dict(base))
            elif tag.startswith("draw_cond:"):
                # 条件抽牌（第五十八批）：条件不满足时仍抽 base 张，故只看牌库非空。
                # ⚠ 不能因「条件不满足」而摘掉 —— 那是把卡变成摆设（卡面没写 only if）。
                if p.deck:
                    out.append(dict(base))
            elif tag.startswith("draw_to:") or tag.startswith("draw:"):
                # 条件式抽牌即使条件不满足也可打出（抽 0 张），但牌库为空时无意义
                if p.deck:
                    out.append(dict(base))
            elif tag.startswith("heal:"):
                # 与 `_apply_heal` 的 scope 语义**逐项对齐**：不可达的目标不列动作，
                # 否则「合法但必然抛错」的假动作会进入 AI 候选集
                #（实测 Dragon Elixir「active 且限龙属性」/ Jacinthe「choose 且限超能」）。
                _hp = tag.split(":")
                # 尾缀 `:energy` / `:discard` 是「回复后的后续动作」标记，
                # 不是范围 token（Wally's Compassion / Super Potion）。
                if len(_hp) > 2 and _hp[-1] in ("energy", "discard"):
                    _hp = _hp[:-1]
                # 第六十二批：`need:<cond>` 是**目标前提**（Jumbo Ice Cream 的
                # 「has 3 or more Energy attached」），不满足时这张卡本来就不能打。
                if "need" in _hp:
                    _ni = _hp.index("need")
                    _ncond = ":".join(_hp[_ni + 1:])
                    _hp = _hp[:_ni]
                    try:
                        if not _eval_condition(self, _ncond):
                            continue
                    except ValueError:
                        continue
                _scope, _pt = "", None
                _i = 2
                while _i < len(_hp):
                    if _hp[_i] == "type" and _i + 1 < len(_hp):
                        _pt = _hp[_i + 1].lower()
                        _i += 2
                        continue
                    if _hp[_i]:
                        _scope = _hp[_i]
                    _i += 1

                def _hok(fp) -> bool:
                    return fp is not None and (
                        not _pt or str(fp.pdef.ptype).lower() == _pt)

                if _scope in ("", "active"):
                    if _hok(p.active):
                        out.append(dict(base))
                elif _scope == "choose":
                    for tpos, _fp in _field_iter(p):
                        if _hok(_fp):
                            out.append({**base, "target": tpos})
                elif _scope == "bench_one":
                    for bi, _fp in enumerate(p.bench):
                        if _hok(_fp):
                            out.append({**base, "target": bi + 1})
                elif _scope == "mega":
                    # 只在己方 Mega 里选（Wally's Compassion）；无 Mega → 不可打
                    for tpos, _fp in _field_iter(p):
                        if getattr(_fp.pdef, "is_mega", False) and _hok(_fp):
                            out.append({**base, "target": tpos})
                elif _scope in ("all", "bench", "basic"):
                    _pool = list(p.bench) if _scope == "bench" else p.all_field()
                    if _scope == "basic":
                        _pool = [fp for fp in _pool if fp.stage == 0]
                    if any(_hok(fp) for fp in _pool):
                        out.append(dict(base))
            elif tag.startswith("gust"):
                for bi in range(len(opp.bench)):
                    out.append({**base, "target": bi})
            elif tag.startswith(("attach_spread:", "attach_spread_flip:",
                                 "attach_each_bench:", "attach_spread_multi:",
                                 "attach_each_pick:", "attach_flip_until:",
                                 "coin_gate_attach:")):
                # 附着族（2026-11-02 第三十八/三十九批）：**必须**排在下面
                # `elif tag.startswith("attach")` **之前** —— 那一支会拿
                # "spread" / "each" / "gate" 当能量属性名去查牌库，恒判「无能量」，
                # 卡于是永远不进合法动作表（打不出、AI 也测不到，机制白做）。
                # 可用性只要求「牌库里至少还有 1 张能量」：具体颜色/张数/目标
                # 是否符合卡面，由结算时的 `_do_*` 兜底（取不满不算错）。
                if _zone_energy_available(p, None, "any"):
                    out.append(dict(base))
            elif tag.startswith("coin_flip:"):
                # 同一枚币的双分支（第四十四批，Venture Bomb）：正面/反面都能落
                # 到指示物（对手任意 1 只 / 己方战斗位）→ 总是可打。
                # 目标选择沿用 `action["snipe"]`（与 `counters:` 同一口径）。
                out.append(dict(base))
            elif tag.startswith("shuffle_both_coin_draw:"):
                # 双方洗回手牌后抽（第四十五批，Harlequin）：洗牌后自己牌库为空
                # 再抽会直接判负 → 该卡不进合法动作表（避免 AI 自选自杀动作）。
                _sbp = str(tag).split(":")
                if len(_sbp) == 5 and all(x.isdigit() for x in _sbp[1:]) \
                        and len(p.deck) + len(p.hand) - 1 > 0:
                    out.append(dict(base))
            elif tag.startswith("shuffle_both_bottom_coin_draw:"):
                # S5（Lucian）：手牌放牌库**底**后再抽 → 同理，抽空即判负。
                # 但这里**不洗牌**：放底后的牌库 = 底(手牌) + 原牌库，
                # 只要「手牌-1 + 牌库」还有牌就抽得动。
                _sbp = str(tag).split(":")
                if len(_sbp) == 3 and all(x.isdigit() for x in _sbp[1:]) \
                        and len(p.deck) + len(p.hand) - 1 > 0:
                    out.append(dict(base))
            elif tag.startswith("shield_choose:"):
                # 单体限时护盾 + 打出前置条件（第四十六批，Acerola's Mischief）
                try:
                    _scspec = _parse_shield_choose_tag(tag)
                    if not self._play_gate_ok(p, _scspec["gate"]):
                        continue
                except ValueError:
                    continue
                for tpos, _fp in _field_iter(p):
                    out.append({**base, "target": tpos})
            elif tag.startswith("team_boost:"):
                # 全队本回合增伤（第六十批）：无目标，手上有就能打
                out.append(dict(base))
            elif tag.startswith("attach"):
                _n, _et, _zn = _parse_attach_tag(tag)
                if _zone_energy_available(p, _et, _zn or "any"):
                    for tpos, _fp in _field_iter(p):
                        out.append({**base, "target": tpos})
            elif tag.startswith("energy_to_deck:"):
                # 弃牌堆基本能量洗回牌库（第七十批，Energy Recycler）：弃牌堆没有
                # 基本能量时这张卡打出去只会空转 → 不进合法动作表（同 discard_tool:）。
                _ed = tag.split(":")
                _ecolor = _ed[1] if len(_ed) > 1 else "any"
                if any(isinstance(c, EnergyRef)
                       and not getattr(c.edef, "is_special", False)
                       and (_ecolor == "any"
                            or str(getattr(c.edef, "type", "")) == _ecolor)
                       for c in p.discard_refs):
                    out.append(dict(base))
            elif tag.startswith("pokemon_to_deck:"):
                # 弃牌堆没有宝可梦时不进动作表（打出去只会空转）。
                if any(isinstance(c, PokeRef) for c in p.discard_refs):
                    out.append(dict(base))
            elif tag.startswith("search_discard:"):
                # 牌库空了就没什么可检索/可弃的 → 不进动作表。
                if p.deck:
                    out.append(dict(base))
            elif tag.startswith("energy_move:"):
                # S6 能量移动族：按 from/to 生成可选目标（至多 N，源上须有能量）
                _ep = tag.split(":")
                _from = "active"
                _to = "bench"
                _et = None
                _i = 2
                while _i < len(_ep):
                    if _ep[_i] == "from" and _i + 1 < len(_ep):
                        _from = _ep[_i + 1]
                        _i += 2
                        continue
                    if _ep[_i] == "to" and _i + 1 < len(_ep):
                        _to = _ep[_i + 1]
                        _i += 2
                        continue
                    if _ep[_i] == "type" and _i + 1 < len(_ep):
                        _et = _ep[_i + 1]
                        _i += 2
                        continue
                    _i += 1

                def _has(fp, et):
                    return fp is not None and (
                        fp.total_energy() > 0 if not et else fp.energy_of(et) > 0)

                if _from in ("active",):
                    _src_ok = _has(p.active, _et)
                    if _src_ok and _to == "bench":
                        for bi in range(len(p.bench)):
                            out.append({**base, "target": bi + 1})
                elif _from == "bench":
                    if p.active is not None:
                        for bi, _fp in enumerate(p.bench):
                            if _has(_fp, _et):
                                out.append({**base, "target": bi + 1})
                elif _from == "any":
                    # 源/目标都要选（Energy Switch）：source + target 成对列出
                    for spos, sfp in _field_iter(p):
                        if not _has(sfp, _et):
                            continue
                        for tpos, tfp in _field_iter(p):
                            if tfp is not sfp:
                                out.append({**base, "source": spos,
                                            "target": tpos})
                elif _from == "opp_active":
                    if _has(opp.active, _et):
                        for bi in range(len(opp.bench)):
                            out.append({**base, "target": bi + 1})
                elif _from == "opp_any":
                    for spos, sfp in _field_iter(opp):
                        if not _has(sfp, _et):
                            continue
                        for tpos, tfp in _field_iter(opp):
                            if tfp is not sfp:
                                out.append({**base, "source": spos,
                                            "target": tpos})
            elif tag.startswith("bounce:"):
                _bp = tag.split(":")
                _bscope = _bp[2] if len(_bp) > 2 else ""
                if _bscope == "choose":
                    for tpos in _bounce_legal_targets(p):
                        out.append({**base, "target": tpos})
                elif _bscope == "bench":
                    for bi in range(len(p.bench)):
                        out.append({**base, "target": bi + 1})
                elif len(_bp) > 1 and _bp[1] == "discard_to_bench":
                    if _bounce_discard_candidates(p, tag, self.cfg.max_bench):
                        out.append(dict(base))
            elif tag.startswith(("discard_opp_random:", "shuffle_opp_hand:")):
                if opp.hand:
                    out.append(dict(base))
            elif tag.startswith("discard_opp_to:"):
                if len(opp.hand) > int(tag.split(":")[1]):
                    out.append(dict(base))
            elif tag.startswith("discard_tool_or_senergy:"):
                # 「弃对手道具/特殊能量 或 弃竞技场」（Blowtorch）：两支都不可用
                # 时这张卡打出去只会空转 → 不进合法动作表（同 `discard_tool:`）。
                if self.stadium or any(
                        fp.tool or fp.senergy for fp in opp.all_field()):
                    out.append(dict(base))
            elif tag.startswith("discard_tool:"):
                _sc = tag.split(":")[2] if len(tag.split(":")) > 2 else "both"
                if _sc == "opp_active":
                    _pool = [opp.active] if opp.active is not None else []
                elif _sc == "opp":
                    _pool = opp.all_field()
                elif _sc == "own":
                    _pool = p.all_field()
                else:
                    _pool = p.all_field() + opp.all_field()
                if any(fp.tool for fp in _pool):
                    out.append(dict(base))
            elif tag == "discard_stadium":
                if self.stadium:
                    out.append(dict(base))
            elif tag.startswith("discard:"):
                _raw = tag.split(":")[1]
                _need = 1 if _raw == "all" else (int(_raw) if _raw.isdigit() else 0)
                if len(p.hand) - 1 >= _need:
                    out.append(dict(base))
            elif tag.startswith("mill:"):
                out.append(dict(base))
            elif tag.startswith("queue:"):
                # 延迟效果本身总能入队（结算时再判条件，条件不满足记 no_effect）
                out.append(dict(base))
            elif tag.startswith("prize:redeal"):
                # S3：有奖赏卡才能重发（对象化模式；只计数模式下必然抛错 → 不许进表）
                if zones.has_real_prizes(p) and p.prizes and p.deck:
                    out.append(dict(base))
            elif tag.startswith("prize:flip_opp"):
                # S3：对手必须有背面朝上的奖赏卡 + 至少 1 张手牌（随机看一张）
                if any(not s.face_up for s in opp.prizes) and opp.hand:
                    out.append(dict(base))
            elif tag.startswith("flute:"):
                # S3：对手牌库非空且后备有空位（否则亮牌后无处可放 = 空转）
                if opp.deck and len(opp.bench) < self.cfg.max_bench:
                    out.append(dict(base))
            elif tag.startswith("recon_bottom:"):
                # ⚠ 第一百三十四批的漏网之鱼（2026-11-08 S3 发现）：Dusk Ball
                # 在 `_do_trainer` 里有派发、也能进池，但 `_trainer_legals`
                # **从未**为它列过动作 → 卡在池内却永远打不出（AI 看不到、只有
                # 手工构造动作才能用）。纪律 #19 的「四处同查」漏了这第五处。
                if p.deck:
                    out.append(dict(base))
            elif tag.startswith("discard_tool_senergy:"):
                # 同上（Ruffian）：必须有一只**同时**带道具与特殊能量。
                if any(fp.tool and fp.senergy for fp in opp.all_field()):
                    out.append(dict(base))
            # ---- 2026-11-08（S3 顺带修）：一批**池内卡永远打不出**的漏网族 ----
            # 这些 tag 在 `_do_trainer` 里都有派发、也都被白名单放行、卡片在池内，
            # 但 `_trainer_legals` 从来没有为它们列过动作 —— `legal_actions()`
            # 不会给出它们，AI 一辈子选不到，人类玩家手里的卡是**死的**。
            # 实测 12 张（Enhanced Hammer / Hand Trimmer / Secret Box /
            # Deduction Kit / Hop's Bag / Precious Trolley / Iron Defender /
            # Ciphermaniac's Codebreaking / Salvatore / Briar / Larry's Skill /
            # Team Rocket's Ariana）。守卫见 `tests/test_legals_coverage.py`
            # （静态比对 `_do_trainer` 与 `_trainer_legals` 的 tag 前缀）。
            elif tag.startswith("discard_energy:"):
                # 「Discard a Special Energy from 1 of your opponent's Pokémon」
                _dp = tag.split(":")
                _scope = _dp[2] if len(_dp) > 2 else "opp_active"
                _pool = ([opp.active] if _scope == "opp_active"
                         and opp.active is not None else opp.all_field())
                _need_special = "special" in _dp[3:]
                if any((any(getattr(r.edef, "is_special", False)
                            for r in fp.senergy) if _need_special
                        else fp.total_energy() > 0) for fp in _pool):
                    out.append(dict(base))
            elif tag.startswith("discard_to_both:"):
                # 双方各弃到剩 N：两边都已 ≤N 时打出去只会空转
                _n51 = int(tag.split(":")[1])
                if len(p.hand) - 1 > _n51 or len(opp.hand) > _n51:
                    out.append(dict(base))
            elif tag.startswith("shuffle_top:"):
                # 看牌库顶 N 张后重排：牌库为空则无事可做
                if p.deck:
                    out.append(dict(base))
            elif tag.startswith("search_top:"):
                # 检索后放回牌库顶：牌库为空则无从检索
                if p.deck:
                    out.append(dict(base))
            elif tag.startswith("search_multi:"):
                # 逐类别各取 1 张（Secret Box / Larry's Skill）：牌库非空即可
                if p.deck:
                    out.append(dict(base))
            elif tag.startswith("discard_search_multi:"):
                if p.deck:
                    out.append(dict(base))
            elif tag.startswith("search_bench:"):
                # 检索**铺场**：牌库要有宝可梦，且后备有空位
                if p.deck and len(p.bench) < self.cfg.max_bench \
                        and any(isinstance(c, PokeRef) for c in p.deck):
                    out.append(dict(base))
            elif tag.startswith("evolve_from_deck"):
                # 从牌库检索进化卡**当场进化**：牌库必须有能接在场上的进化卡
                _names = {fp.pdef.name for fp in p.all_field() if fp is not None}
                if any(isinstance(c, PokeRef) and c.pdef.evolves_from in _names
                       and c.pdef.stage > 0 for c in p.deck):
                    out.append(dict(base))
            elif tag.startswith("team_shield:"):
                # 全队限时减伤：场上没有宝可梦就没有作用对象
                if p.has_pokemon:
                    out.append(dict(base))
            elif tag.startswith("prize_bonus:"):
                # 本回合多拿奖赏（Briar）：需要场上有宝可梦才可能击倒对手
                if p.has_pokemon:
                    out.append(dict(base))
            elif tag.startswith("draw_to_branch:"):
                # 「抽到 5 张；若条件成立则改为抽到 8 张」——手牌已 ≥8 且目标
                # 牌库空时是空转，其余情况都有实际效果。
                _db = tag.split(":")
                _alt = int(_db[2]) if len(_db) > 2 and _db[2].isdigit() else 0
                if p.deck and len(p.hand) - 1 < max(_alt, 1):
                    out.append(dict(base))
        # S3 场地「每回合一次」——与手牌无关，单独列动作
        #（switch_typed 这类要选目标 → 每个合法替补一行）
        base_su = {"verb": "stadium_use",
                   "name": (self.stadium or {}).get("name", ""),
                   "display": (self.stadium or {}).get("name", "")}
        for extra in self._stadium_use_actions(p):
            out.append({**base_su, **extra})
        return out

    # ---------- 自由文本指令（中文别名 → 合法动作） ----------
    def command_catalog(self, pi: Optional[int] = None) -> list:
        """当前可用指令清单（label 为中文可读文案，action 为可直接 step 的动作）"""
        from .commands import command_catalog
        return command_catalog(self.legal_actions(pi))

    def resolve_command(self, text: str, verb: Optional[str] = None) -> dict:
        """把「招式名 / 卡名 / 动词」文本解析成合法动作

        支持中文与英文名称、忽略大小写与标点；返回 {ok, action, matched, ...}。
        """
        from .commands import find_action
        return find_action(self.legal_actions(), text, verb=verb)

    def step_command(self, text: str, verb: Optional[str] = None) -> dict:
        """按名称直接出招/用卡（解析失败 → {"ok": False, "error": ...}）"""
        res = self.resolve_command(text, verb=verb)
        if not res.get("ok"):
            return {"ok": False, "error": res.get("error", "指令无法解析"),
                    "candidates": res.get("candidates", []),
                    "matched": res.get("matched")}
        out = self.step(res["action"])
        out["matched"] = res.get("matched")
        out["action"] = res["action"]
        return out

    def step_sequence(self, text, actor: Optional[int] = None,
                      max_steps: Optional[int] = None) -> dict:
        """执行**组合指令**（多步序列），每步按最新 legal_actions 重新解析

        语义（与前端/API 约定一致）：
          - 依次执行；某步解析失败或引擎拒绝 → **停止**并回报 `steps`（已执行的保留）；
          - `结束回合`（或任何使回合移交的动作）后 → 停止，剩余步骤计入 `remaining`；
          - 对局结束 / 非主阶段 / 超过步数上限 → 停止并说明原因；
          - 不会替对方走棋（`actor` 默认为当前行动方，回合移交即停）。

        返回 `{ok, steps[], executed, remaining, stopped, view}`
        """
        from .commands import split_sequence, MAX_SEQUENCE_STEPS
        parts = split_sequence(text)
        if not parts:
            return {"ok": False, "error": "空指令", "steps": [],
                    "executed": 0, "remaining": []}
        if actor is None:
            actor = self.current
        limit = min(int(max_steps or MAX_SEQUENCE_STEPS), MAX_SEQUENCE_STEPS)

        steps: list = []
        stopped = None
        for i, part in enumerate(parts):
            if i >= limit:
                stopped = f"超过单次最多 {limit} 步（剩余未执行）"
                break
            if self.finished:
                stopped = "对局已结束"
                break
            if self.phase != "main":
                stopped = f"当前阶段为 {self.phase}，无法继续执行"
                break
            if self.current != actor:
                stopped = "回合已移交对手"
                break
            res = self.resolve_command(part)
            if not res.get("ok"):
                steps.append({"input": part, "ok": False,
                              "error": res.get("error"),
                              "ambiguous": bool(res.get("ambiguous")),
                              "candidates": res.get("candidates") or []})
                stopped = res.get("error")
                break
            out = self.step(res["action"])
            if not out.get("ok"):
                steps.append({"input": part, "ok": False,
                              "role": None, "error": out.get("error")})
                stopped = out.get("error")
                break
            steps.append({"input": part, "ok": True,
                          "matched": res.get("matched"),
                          "used_action": res["action"],
                          # 该步**执行后**的局面 → 供前端「我方多步指令」逐帧演出。
                          # 视角必须显式给 actor（默认参数是"当前行动方"，回合移交后会串视角）。
                          "view": self.view(actor)})
            if self.finished:
                stopped = "对局已结束"
                break
            if self.current != actor:
                stopped = "回合已移交对手"
                break

        executed = sum(1 for s in steps if s.get("ok"))
        remaining = parts[executed:] if stopped and executed < len(parts) else []
        return {"ok": executed > 0 or (len(parts) > 0 and not stopped),
                "steps": steps, "executed": executed,
                "planned": len(parts), "remaining": remaining,
                "stopped": stopped, "view": self.view()}

    # ---------- 动作执行 ----------
    def step(self, action: dict) -> dict:
        if self.finished:
            return {"ok": False, "error": "game_over"}
        # 决策分解（S1a）：挂起期间只接受「选第 i 个 / 结束选择」两类动作。
        # ⚠ 这里**不能**回落到普通动作：一旦回落，「任意数量」就会被「先做别的动作」
        #   绕过去（选择被跳过 = 引擎自选空集），正是要禁止的静默默认。
        if self.phase == "choice":
            return self._step_choice(action)
        if self.phase != "main":
            return {"ok": False, "error": f"phase={self.phase}"}
        # S2（2026-11-08）：换位事件的**唯一**检测点 —— 分发前快照、分发后比对。
        # 这样撤退 / Switch / 拉人 / 换位特性 / 回手 等所有换位路径自动被覆盖，
        # 不必在 10 个 helper 里各写一次 emit（写必有漏，漏即静默丢效果）。
        p = self.players[self.current]
        events.snapshot_switch(self)
        try:
            res = self._step_main(p, action.get("verb"), action)
        except ValueError as e:
            res = {"ok": False, "error": str(e)}
        events.emit_switch_diff(self)
        return res

    def _step_main(self, p: PlayerState, verb, action: dict) -> dict:
        """主阶段的动词分发（`step` 只负责事件快照/比对，逻辑留在这里）"""
        if verb == "play":
            return self._do_play(p, action)
        if verb == "attach":
            return self._do_attach(p, action)
        if verb == "attack":
            return self._do_attack(p, action)
        if verb == "retreat":
            return self._do_retreat(p, action)
        if verb == "evolve":
            return self._do_evolve(p, action)
        if verb == "ability":
            return self._do_ability(p, action)
        if verb == "trainer":
            return self._do_trainer(p, action)
        if verb == "stadium_use":
            return self._do_stadium_use(p, action)
        if verb == "discard_fossil":
            return self._do_discard_fossil(p, action)
        if verb == "pass":
            self._end_turn()
            return {"ok": True, "event": None}
        return {"ok": False, "error": f"unknown verb {verb}"}

    def _do_play(self, p: PlayerState, action: dict) -> dict:
        idx = _hand_index_from_action(p.hand, action, want="basic")
        if idx is None:
            known = "、".join(
                c.pdef.display for c in p.hand
                if isinstance(c, PokeRef) and c.pdef.stage == 0)
            raise ValueError(
                f"无法识别要上场的基础宝可梦「{_name_hint(action)}」"
                f"（手牌可用：{known or '无'}）")
        c = p.hand[idx]
        if not isinstance(c, PokeRef) or c.pdef.stage != 0:
            raise ValueError("该牌不是基础宝可梦")
        if not self._bench_has_room(p):
            raise ValueError("后备区已满")
        p.hand.pop(idx)
        fp = make_pokemon(c.pdef)
        fp.just_played = True
        p.bench.append(fp)
        self._log("play", {"player": self.current, "card": fp.name})
        self._on_bench_placed(self.current, fp)
        return {"ok": True, "event": self.events[-1]}

    def _do_attach(self, p: PlayerState, action: dict) -> dict:
        if p.attached_energy_this_turn >= 1:
            raise ValueError("本回合已附能量")
        t = int(action.get("target", 0))
        if t == 0:
            if p.active is None:
                raise ValueError("无 active")
            target = p.active
        else:
            bi = t - 1
            if bi >= len(p.bench):
                raise ValueError("后备目标越界")
            target = p.bench[bi]
        idx = action.get("energy")
        if idx is not None:
            idx = int(idx)
            if not (0 <= idx < len(p.hand)) or \
                    not isinstance(p.hand[idx], EnergyRef):
                raise ValueError("能量卡位非法")
            ref = p.hand[idx]
        else:
            ref = None
            for i in reversed(range(len(p.hand))):
                if isinstance(p.hand[i], EnergyRef):
                    ref = p.hand[i]
                    break
            if ref is None:
                raise ValueError("手牌无能量")
        # 特殊能量：先校验附着限制（火箭队的能量只能附着于火箭队宝可梦），
        # 不合法时**不得**先移除手牌 —— 否则等于白扔一张卡
        if ref.edef.is_special and not special_energy.attach_allowed(
                ref.edef, target):
            raise ValueError(f"{ref.edef.display} 只能附着于火箭队的宝可梦")
        if not p.attach_energy_to(target, self.cfg, index=idx):
            raise ValueError("手牌无能量")
        p.attached_energy_this_turn = 1
        # 「As long as this Pokémon is in the Active Spot, whenever you attach an
        #  Energy card from your hand to 1 of your Pokémon, heal N damage from that
        #  Pokémon.」（第一百零四批，Magearna「Auto Heal」）
        # ⚠ 目标是**被附能的那只**，不是持有者；且持有者必须在**战斗位**。
        for _h in p.all_field():
            if _h is not p.active:
                continue
            for _ab in (_h.pdef.abilities or ()):
                _tg = str(getattr(_ab, "tag", "") or "")
                if not _tg.startswith("passive:heal_on_attach:"):
                    continue
                _n = int(_tg.split(":")[2]) if len(_tg.split(":")) > 2 else 0
                _before = target.damage
                target.damage = max(0, target.damage - _n)
                if _before != target.damage:
                    self._log("heal", {"player": self.current,
                                       "target": target.name,
                                       "n": _before - target.damage,
                                       "by": "passive_attach"})
        detail = special_energy.on_attach(self, p, target, ref.edef)
        self._log("attach", {"player": self.current, "target": target.name,
                             "etype": (ref.edef.tag or ref.edef.type),
                             "energy_now": target.total_energy(),
                             **({"detail": detail} if detail else {})})
        # S2d（2026-11-08）：**附能事件**。只从**手牌**附着才发（卡面
        # 「attach an Energy card from their hand」）—— 从牌库/弃牌堆的效果附着
        # 走 `_attach_energy_from_zones`，不属于这个事件点（漏判 = 静默多触发）。
        events.emit(self, "attach_energy",
                    {"player": self.current, "target": target,
                     "etype": (ref.edef.tag or ref.edef.type),
                     "from_hand": True, "turn_owner": self.current})
        return {"ok": True, "event": self.events[-1]}

    # ---------- 借用招式（2026-10-28） ----------
    def _borrow_action(self, ai: int, atk, opt: dict) -> dict:
        """构造「借用招式」动作（`attack` 仍指向本招式，`borrow` 指定来源）"""
        src = opt["atk"]
        act_ = {"verb": "attack", "attack": ai,
                "name": src.name, "name_zh": src.name_zh,
                "display": src.name_zh or src.name,
                "damage": src.damage,
                "cost_map": list((opt["atk"] if opt["src"] == "prev" else atk).cost),
                "desc_zh": src.desc_zh, "display_desc": src.display_desc,
                "via": atk.name, "via_zh": atk.name_zh,
                "borrow": {"src": opt["src"], "attack": opt["index"]}}
        if opt["src"] == "ally" or _borrow_scope_kind(opt["src"]) == \
                "opp_deck_top":
            act_["borrow"]["pos"] = opt["pos"]
        return act_

    def _prev_attacks_allowed(self, p: PlayerState, attacker) -> bool:
        """Relicanth「Memory Dive」：己方场上有该被动，且攻击方是进化宝可梦"""
        if int(getattr(attacker.pdef, "stage", 0)) < 1:
            return False
        return any(any(str(ab.tag) == "passive:prev_attacks"
                       for ab in fp.pdef.abilities)
                   for fp in p.all_field())

    def _borrow_options(self, p: PlayerState, opp: PlayerState,
                        attacker, atk) -> list:
        """当前可借用的招式来源列表（空 = 无可用来源）"""
        scope = _borrow_scope(atk)
        out: list = []

        def _push(src: str, pos, fp) -> None:
            for k, a2 in enumerate(fp.pdef.attacks):
                out.append({"src": src, "pos": pos, "index": k, "atk": a2})

        if scope == "opp_active":
            if opp.active is not None and not opp.active.is_ko:
                _push("opp_active", None, opp.active)
        elif scope == "opp_active_tera":
            # 「your opponent's Active **Tera** Pokémon's attacks」：非 Tera 不可借
            if opp.active is not None and not opp.active.is_ko \
                    and str(opp.active.pdef.category or "").startswith("Tera"):
                _push("opp_active", None, opp.active)
        elif scope == "bench_ally_n":
            # 「1 of your Benched N's Pokémon's attacks」
            for bi, fp in enumerate(p.bench):
                if str(fp.pdef.category or "") != "Trainer's Pokémon(N)":
                    continue
                _push("ally", bi, fp)
        elif _borrow_scope_kind(scope) == "opp_deck_top":
            # S5（2026-11-08）：TR Persian ex「Haughty Order」——「Reveal the top
            # N cards of your opponent's deck. You **may** choose an attack from
            # a Pokémon you find there and use it as this attack.」
            # `pos` = 在**检视列表**里的下标（列表首 = 牌库顶），不是牌库下标：
            # 牌库可能在「生成动作」与「结算」之间被洗动（本例不会，但下标语义
            # 必须与来源绑定，否则会指到另一张卡上）。
            n = _borrow_deck_top_n(scope) or 0
            for k, ref in enumerate(reversed(list(opp.deck[-n:]))):
                if not isinstance(ref, PokeRef):
                    continue
                if not ref.pdef.attacks:
                    continue
                for j, a2 in enumerate(ref.pdef.attacks):
                    out.append({"src": scope, "pos": k, "index": j,
                                "atk": a2, "card": ref})
        elif scope is None and self._prev_attacks_allowed(p, attacker):
            for k, a2 in enumerate(_prev_form_attacks(attacker.pdef)):
                out.append({"src": "prev", "pos": None, "index": k, "atk": a2})
        return out

    def _resolve_borrow(self, p: PlayerState, opp: PlayerState,
                        attacker, base_atk, action: dict):
        """校验并解析 `action["borrow"]` → {atk, pay, src} 或 None（普通招式）"""
        req = action.get("borrow")
        if not req:
            return None
        if not isinstance(req, dict):
            raise ValueError(f"{base_atk.name} 必须指定借用的招式")
        for o in self._borrow_options(p, opp, attacker, base_atk):
            if str(req.get("src")) != o["src"]:
                continue
            if o["src"] in ("ally",) or _borrow_scope_kind(o["src"]) == \
                    "opp_deck_top":
                if int(req.get("pos", -1)) != o["pos"]:
                    continue
            if int(req.get("attack", -1)) != o["index"]:
                continue
            return {"atk": o["atk"],
                    # 复制招式付**本招式**的费用；进化前招式付它自己的费用
                    "pay": o["atk"] if o["src"] == "prev" else base_atk,
                    "src": o["src"]}
        raise ValueError(f"{base_atk.name}：借用的招式不在可选范围内")

    def _do_retreat(self, p: PlayerState, action: dict) -> dict:
        if p.active is None or not p.bench:
            raise ValueError("无法撤退")
        # 化石卡（Fossil）：卡面明文「can't retreat」（不受状态影响、弃置另有动作）
        if getattr(p.active, "fossil", False):
            raise ValueError("化石卡不能撤退")
        if _NO_ATTACK_STATUS & p.active.status:
            raise ValueError("睡眠/麻痹中无法撤退")
        if p.active.retreat_locked(self.turn_counts[self.current]):
            raise ValueError("该宝可梦当前无法撤退")
        cost = _retreat_cost(p.active, self)
        if not p.active.remove_any(cost):
            raise ValueError("能量不足")
        bi = int(action.get("bench", 0))
        if bi >= len(p.bench):
            raise ValueError("替补目标越界")
        old = p.active
        old.clear_status()
        old.clear_active_locks()
        p.active = p.bench.pop(bi)
        p.bench.append(old)
        p.active.moved_active_turn = self.turn_number
        self._log("retreat", {"player": self.current, "out": old.name,
                              "in": p.active.name})
        return {"ok": True, "event": self.events[-1]}

    def _same_turn_evolve_ok(self, tgt: FieldPokemon, card,
                             pi: Optional[int] = None) -> bool:
        """Forest of Vitality：「{G} 宝可梦可在上场当回合进化（首个回合除外）」

        只放宽「本回合**从手牌放置**」这一种；本回合已经进化过的目标不再放宽
        （卡面是 "during the turn they **play** those Pokémon"）。
        """
        owner = self.current if pi is None else pi
        # ① 宝可梦**自身**的被动（第一百一十二/一百二十一批）：
        #    「… can evolve during your first turn or the turn you play it.」
        #    （Eevee / Karrablast / Shelmet / Luxio）——**含首个回合**，
        #    与 Forest of Vitality 的「except during their first turn」相反。
        for ab in (tgt.pdef.abilities or ()):
            tag = str(getattr(ab, "tag", "") or "")
            if not tag.startswith("passive:evolve_same_turn"):
                continue
            rest = tag[len("passive:evolve_same_turn"):].lstrip(":")
            cond, need_active = None, False
            if rest.startswith("if:"):
                cond = rest[len("if:"):]
            elif rest == "active":
                # 「As long as this Pokémon is in the **Active Spot**, it can
                # evolve during your first turn…」（Eevee）—— 后备不算。
                need_active = True
            elif rest:
                raise ValueError(f"evolve_same_turn 未识别限定：{tag}")
            if need_active and tgt is not self.players[owner].active:
                continue
            if cond and not _eval_condition(self, cond):
                continue
            if tgt.evolved_turn != self.turn_number:
                return True
        # ② 场地（Forest of Vitality）——**不含**首个回合
        m = self._stadium_meta()
        return (m["kind"] == "evolve_same_turn"
                and m["type"] is not None
                and str(tgt.pdef.ptype).lower() == m["type"]
                and str(card.pdef.ptype).lower() == m["type"]
                and tgt.evolved_turn != self.turn_number
                and self.turn_counts[owner] > 1)

    def _do_evolve(self, p: PlayerState, action: dict) -> dict:
        if p.evolution_lock_until > self.turn_counts[self.current]:
            raise ValueError("本回合无法从手牌进化（进化封锁）")
        hi = _hand_index_from_action(p.hand, action, want="stage")
        if hi is None:
            known = "、".join(
                c.pdef.display for c in p.hand
                if isinstance(c, PokeRef) and c.pdef.stage > 0)
            raise ValueError(
                f"无法识别要进化的卡「{_name_hint(action)}」"
                f"（手牌可用：{known or '无'}）")
        c = p.hand[hi]
        if not isinstance(c, PokeRef) or c.pdef.stage <= 0:
            raise ValueError("该牌不可进化")
        tpos = int(action.get("target", 0))
        tgt = _field_at(p, tpos)
        if tgt is None:
            raise ValueError("进化目标为空")
        if tgt.just_played and not self._same_turn_evolve_ok(tgt, c):
            raise ValueError("本回合刚放置/进化，不可再进化")
        if tgt.pdef.name != c.pdef.evolves_from \
                or tgt.stage + 1 != c.pdef.stage:
            raise ValueError("进化链不匹配")
        # S2e（2026-11-08）：「Put this Pokémon into play only with the effect of
        # Palafin's Zero to Hero Ability.」（Palafin ex「Hero's Spirit」）——
        # 这类卡**不能**从手牌进化上场，只能由指定效果放进场。
        # ⚠ 只挡进化不够：`legal_actions` 也得挡，否则 AI 会一直提非法动作。
        if _no_evolve_into(c.pdef):
            raise ValueError(
                f"{c.pdef.display} 只能由卡面指定的效果上场（不能从手牌进化）")
        old_name = tgt.name
        p.hand.pop(hi)
        # Dizzying Valley：「Confused Pokémon don't recover from that Special
        # Condition when they evolve or devolve.」（第一百零六批）
        # `evolve_to` 会清空全部状态，因此**先记再补**；只补混乱，别的状态照常清。
        _keep_confuse = ("confuse" in tgt.status
                         and self._stadium_meta()["kind"] == "keep_confuse")
        tgt.evolve_to(c.pdef)
        if _keep_confuse:
            tgt.status.add("confuse")
            self._log("status_kept", {"target": tgt.name, "status": "confuse",
                                      "by": "stadium"})
        tgt.evolved_turn = self.turn_number
        tgt.evolved_from_name = old_name
        self._log("evolve", {"player": self.current, "from": old_name,
                             "to": tgt.name, "pos": tpos})
        self._trigger_evolve_attach(tgt)
        return {"ok": True, "event": self.events[-1]}

    def _owner_of(self, fp: FieldPokemon) -> int:
        """反查某只宝可梦属于哪一方（进化事件的承载者需要它）"""
        for pi in (0, 1):
            if fp in self.players[pi].all_field():
                return pi
        return self.current

    def _trigger_evolve_attach(self, fp: FieldPokemon) -> None:
        """进化完成后的统一钩子：**进化事件** + 附能特性

        S2b（2026-11-08）：这里同时是所有「进化刚完成」的**唯一**落点 ——
        4 处 `evolve_to` 之后都会调用本函数，因此事件不会漏发。
        ⚠ 谁进化了就报谁的 `player`：Team Rocket's Ampharos「Darkest Impulse」
          看的是**对手**进化，方向搞反会变成「自己进化时给自己放指示物」。

        下面是既有的附能钩子说明：对应 Blaziken ex「火焰之舞」——从手牌进化时，
        从弃牌堆取 n 张指定色基础能量附到自身（引擎子集：附到自身而非后备，
        差异见 cards_real.json note）。
        """
        _who = self._owner_of(fp)
        events.emit(self, "evolve", {"player": _who, "entering": fp,
                                     "turn_owner": self.current})
        p = self.players[self.current]
        for ab in fp.pdef.abilities:
            tag = str(getattr(ab, "tag", "") or "")
            if tag.startswith("evolve_attach:"):
                n, etype, zone = _parse_attach_tag(tag)
                got = _attach_energy_from_zones(self, p, fp, n, etype,
                                                zone or "discard")
                self._log("ability", {"player": self.current, "name": ab.name,
                                      "from": fp.name, "trigger": "on_evolve",
                                      "detail": f"attach {got} {etype or 'any'} "
                                                f"from {zone or 'discard'}"})
            elif tag.startswith("evolve_act:"):
                # 进化触发特性的**任意效果序列**（2026-10-28 第十批）：
                # `evolve_act:<tag1>|<tag2>` 按序执行（`|` 分隔），由
                # `_apply_attack_effects` 统一结算（含 `:if_prev` 条件后续）。
                _ea_body = tag[len("evolve_act:"):]
                # 进化触发特性的**前置条件**（第一百零二批，Noctowl「Jewel
                # Seeker」：「…, if you have any Tera Pokémon in play, you may
                # search…」）。不满足时整条不执行 —— 解析侧只在条件可精确求值
                # 时才产出 `:gate:`，认不出的一律整卡拒收。
                _ea_gate = None
                if ":gate:" in _ea_body:
                    _ea_body, _ea_gate = _ea_body.split(":gate:", 1)
                if _ea_gate and not _eval_condition(self, _ea_gate):
                    self._log("ability", {"player": self.current, "name": ab.name,
                                          "from": fp.name, "trigger": "on_evolve",
                                          "detail": f"gate_false:{_ea_gate}"})
                    continue
                subs = tuple(s for s in _ea_body.split("|") if s)
                if not subs:
                    continue
                self._log("ability", {"player": self.current, "name": ab.name,
                                      "from": fp.name, "trigger": "on_evolve",
                                      "detail": "|".join(subs)})
                self._apply_attack_effects(fp, subs, action={})

    def _ability_coin(self, fp: FieldPokemon, tag: str, action: dict) -> str:
        """特性侧抛币（2026-11-03 第四十四批）——**只抛一次**后派发子效果

        · ``coin:<side>:<sub>``                单分支（Dustox「Boisterous Wind」）
        · ``coin_flip:heads:<a>[|tails:<b>]``  双分支**共用同一次**抛币

        子效果交给 `_apply_attack_effects`（bounce / counters 等已有实现），
        **不重新抛币**。除上述两种形态外一律抛错，绝不静默忽略。
        """
        if tag.startswith("coin_flip:"):
            branches = _parse_coin_flip_tag(tag)
            res = "heads" if self.rng.random() < 0.5 else "tails"
            self._log("coin", {"player": self.current, "result": res, "tag": tag})
            sub = branches.get(res)
            if not sub:
                return f"coin {res}: no effect"
            self._apply_attack_effects(
                fp, tuple(s for s in sub.split("|") if s), action=action)
            return f"coin {res}: {sub}"
        parts = str(tag).split(":", 2)
        if len(parts) != 3 or parts[1] not in ("heads", "tails"):
            raise ValueError(f"特性抛币 tag 非法：{tag}")
        _, side, sub = parts
        res = "heads" if self.rng.random() < 0.5 else "tails"
        self._log("coin", {"player": self.current, "result": res, "tag": tag})
        if res != side:
            return f"coin {res}: no effect"
        self._apply_attack_effects(
            fp, tuple(s for s in sub.split("|") if s), action=action)
        return f"coin {res}: {sub}"

    def _do_ability(self, p: PlayerState, action: dict) -> dict:
        """特性发动（E3）：field=场上每回合一次；hand=手牌弃卡发动"""
        name, tag = action["name"], action.get("tag", "")
        if "pos" in action:
            # ---- field 特性 ----
            fp = _field_at(p, int(action["pos"]))
            if fp is None:
                raise ValueError("特性宝可梦不在场上")
            ab = next((x for x in fp.pdef.abilities
                       if x.name == name and x.location == "field"), None)
            if ab is None:
                raise ValueError(f"无 field 特性 {name}")
            if name in fp.ability_used:
                raise ValueError("该特性本回合已发动")
            fp.ability_used.add(name)
            # 发动代价（2026-11-04 第四十九批）：**先付账再结算效果**。
            # 顺序反了会变成「先看效果、付不出也照发」（静默加强）。
            if getattr(ab, "cost", ""):
                self._pay_ability_cost(p, fp, ab.cost, action)
            # 复合特性（第九十五批）：`<tag1>|<tag2>` 按**卡面顺序**执行。
            # 例：Feraligatr「Torrential Heart」= 先自伤 5 个指示物（代价）
            # + 本回合自身加伤 120。顺序反了会变成「先加伤、代价可省」。
            # ⚠ 必须在各 `startswith` 分支**之前**：`counters:…|…` 会先被
            #   counters 分支吃掉，把 `self|self_dmg_bonus` 当成非法 scope。
            # ⚠ 但 `coin:` / `coin_flip:` / `evolve_act:` 里的 `|` 是**分支分隔符**
            #   而非复合动作，不能拆。
            if "|" in tag and not tag.startswith(("coin:", "coin_flip:",
                                                  "evolve_act:")):
                subs = tuple(x for x in tag.split("|") if x)
                self._log("ability", {"player": self.current, "name": name,
                                      "from": fp.name, "detail": "|".join(subs)})
                self._apply_attack_effects(fp, subs, action)
                return {"ok": True, "event": self.events[-1]}
            if tag.startswith("choose:swap_hand_top"):
                # 「Switch a card from your hand with the top card of your deck.」
                # （Gumshoos「Evidence Gathering」）—— 选哪张手牌由牌手决定，
                # 引擎不自选（选错＝换错牌，属于实质改结果）。
                _opts = [{"ref": c, "display": _card_label(c)}
                         for c in p.hand]
                if not _opts:
                    raise ValueError("手牌为空，无法交换")
                if not p.deck:
                    raise ValueError("牌库为空，无法交换")
                self._open_choice("swap_hand_top", _opts, min_pick=1,
                                  max_pick=1, tag=tag)
                return {"ok": True, "event": self.events[-1]}
            if tag.startswith("recon:"):
                _rspec = _parse_recon_tag(tag)
                if not _rspec["legacy"]:
                    # 扩展口径（2026-10-30 第二十批）：筛选项 + 去向 + 前置条件
                    self._log("ability", {
                        "player": self.current, "name": name,
                        "from": fp.name,
                        "detail": self._do_recon(p, tag, action)})
                    return {"ok": True, "event": self.events[-1]}
                n = _rspec["look_top"]
                if not p.deck:
                    raise ValueError("牌库为空")
                top = [p.deck.pop() for _ in range(min(n, len(p.deck)))]
                pick = self.rng.randrange(len(top))
                chosen = top.pop(pick)
                p.hand.append(chosen)
                for c in reversed(top):       # 其余放回牌库底
                    p.deck.insert(0, c)
                self._log("ability", {"player": self.current, "name": name,
                                      "from": fp.name, "detail": f"top{n} pick1",
                                      "got": _card_label(chosen)})
                return {"ok": True, "event": self.events[-1]}
            if tag.startswith("draw_both:"):
                # 「have each player draw a card」（Chandelure「Alluring Light」）
                n = int(tag.split(":")[1])
                for _pi in (self.current, 1 - self.current):
                    self._safe_draw(_pi, n, source="ability")
                    if self.finished:
                        break
                self._log("ability", {"player": self.current, "name": name,
                                      "from": fp.name, "detail": f"both draw {n}"})
                return {"ok": True, "event": self.events[-1]}
            if tag.startswith("opp_shuffle_draw:"):
                detail = self._opp_shuffle_draw(tag)
                self._log("ability", {"player": self.current, "name": name,
                                      "from": fp.name, "detail": detail})
                return {"ok": True, "event": self.events[-1]}
            if tag.startswith("switch_choose:"):
                # 「switch 1 of your Benched {D} Pokémon, except any Pecharunt
                #  {ex}, with your Active Pokémon. If you do, the new Active
                #  Pokémon is now Poisoned.」（第一百一十一批，Pecharunt ex）
                # 形态：`switch_choose:<type|any>[:except:<namekey>][:then:<status>]`
                _sc = tag.split(":")
                _sty = _sc[1].lower()
                _exc, _then = None, None
                _i = 2
                while _i + 1 < len(_sc):
                    if _sc[_i] == "except":
                        _exc = _sc[_i + 1]
                    elif _sc[_i] == "then":
                        _then = _sc[_i + 1]
                    else:
                        raise ValueError(f"switch_choose 未识别限定：{tag}")
                    _i += 2
                if _then is not None and _then not in STATUSES:
                    raise ValueError(f"switch_choose 状态非法：{tag}")
                if p.active is None:
                    raise ValueError("无战斗位")
                # 目标必须由玩家指定（action["bench"]），无默认值 —— 换错一只
                # 等于把整盘位置换乱，不能拿「后备第一只」冒充。
                _bi = action.get("bench") if isinstance(action, dict) else None
                if _bi is None:
                    raise ValueError("switch_choose 需要指定后备位置")
                _bi = int(_bi)
                if not (0 <= _bi < len(p.bench)):
                    raise ValueError("换位目标越界")
                _inc = p.bench[_bi]
                if _sty not in ("any", "") \
                        and str(_inc.pdef.ptype).lower() != _sty:
                    raise ValueError("换上的宝可梦属性不符")
                if _exc:
                    # 卡面「except any Pecharunt {ex}」：解析侧给的键是
                    # `pecharunt`（`{ex}` 在花括号里，不属于名字），而引擎卡名
                    # 键是 `pecharuntex` → 必须剥掉尾部 `ex` 再比，否则例外失效。
                    _nk = _name_key(_inc.pdef.name)
                    if _nk.endswith("ex"):
                        _nk = _nk[:-2]
                    if _nk == _exc:
                        raise ValueError("该宝可梦被例外排除")
                _old = p.active
                _old.clear_status()
                _old.clear_active_locks()
                p.active = p.bench.pop(_bi)
                p.bench.append(_old)
                p.active.moved_active_turn = self.turn_number
                self._log("switch", {"player": self.current, "name": name,
                                     "from": fp.name, "out": _old.name,
                                     "in": p.active.name, "side": "ability"})
                if _then and not self._status_blocked(p.active, _then):
                    p.active.status.add(_then)
                    self._log("status", {"player": self.current,
                                         "target": p.active.name,
                                         "status": _then,
                                         "from": name})
                return {"ok": True, "event": self.events[-1]}
            if tag.startswith("switch_self"):
                # 后备特性自换位（Linoone「Excited Dash」）：持有者必须在后备。
                # 代价/条件写在 tag 上：`switch_self:if:<cond>`（如「场上已有
                # Mega Evolution Pokémon {ex}」）——条件不满足即**不可发动**。
                _sp = tag.split(":")
                _cond = (":".join(_sp[2:])
                         if len(_sp) > 2 and _sp[1] == "if" else None)
                if _cond is not None and not _eval_condition(self, _cond):
                    raise ValueError("特性发动条件不满足")
                if p.active is fp:
                    raise ValueError("该特性只能在**后备**发动")
                bi = next((i for i, x in enumerate(p.bench) if x is fp), None)
                if bi is None or p.active is None:
                    raise ValueError("无战斗位可换入")
                old = p.active
                old.clear_status()
                old.clear_active_locks()
                p.active = p.bench.pop(bi)
                p.bench.append(old)
                p.active.moved_active_turn = self.turn_number
                self._log("switch", {"player": self.current, "name": name,
                                     "from": fp.name, "out": old.name,
                                     "in": p.active.name, "forced": True,
                                     "side": "ability"})
                return {"ok": True, "event": self.events[-1]}
            if tag.startswith("mill:"):
                # 特性侧磨牌（第五十二批，Durant ex「Sudden Shearing」）：
                # 与训练家/招式侧同一实现，不另写一份。
                detail = self._trainer_mill(tag)
                self._log("ability", {"player": self.current, "name": name,
                                      "from": fp.name, "detail": detail})
                return {"ok": True, "event": self.events[-1]}
            if tag.startswith("gust"):
                # 特性侧把对手后备拉为战斗位（第五十二批，Meowstic）
                _opp = self.players[1 - self.current]
                detail = self._trainer_boss(_opp, int(action.get("target", 0)))
                self._log("ability", {"player": self.current, "name": name,
                                      "from": fp.name, "detail": detail})
                return {"ok": True, "event": self.events[-1]}
            if tag.startswith("draw_to:"):
                # 「draw cards until you have N cards in your hand」（第四十九批）。
                # ⚠ 与训练家侧的 `_trainer_draw_to` 差一张：训练家卡此刻**还在手上**
                # （`need = N - (len(hand) - 1)`），特性没有这张消耗，直接按手牌数算。
                target_n = int(tag.split(":")[1])
                need = max(0, target_n - len(p.hand))
                if need and not p.deck:
                    raise ValueError("牌库为空")
                got = (self._safe_draw(self.current, need, source="ability")
                       if need else 0)
                self._log("ability", {"player": self.current, "name": name,
                                      "from": fp.name,
                                      "detail": f"draw_to {target_n}",
                                      "drew": got})
                return {"ok": True, "event": self.events[-1]}
            if tag.startswith("draw:"):
                # 每回合一次抽 n（`draw:N` / `draw:per:<counter>` / `draw:N:if:<cond>`）
                n, cond = _draw_spec(self, tag)
                if cond is not None and not _eval_condition(self, cond):
                    raise ValueError("特性发动条件不满足")
                if not p.deck:
                    raise ValueError("牌库为空")
                got = self._safe_draw(self.current, n, source="ability")
                self._log("ability", {"player": self.current, "name": name,
                                      "from": fp.name, "detail": f"draw {n}",
                                      "drew": got})
                return {"ok": True, "event": self.events[-1]}
            if tag.startswith("heal:"):
                # 特性回复：范围语义与招式/训练家共用 `_apply_heal`；
                # 无 scope 时回**特性持有者**（卡面「from this Pokémon」）
                healed, n_tgt = _apply_heal(self, p, tag, caster=fp,
                                            action=action)
                self._log("ability", {"player": self.current, "name": name,
                                      "from": fp.name, "detail": f"heal {healed}",
                                      "targets": n_tgt})
                return {"ok": True, "event": self.events[-1]}
            if tag.startswith("search_bench:"):
                detail = self._search_bench(p, tag)
                self._log("ability", {"player": self.current, "name": name,
                                      "from": fp.name, "detail": detail})
                return {"ok": True, "event": self.events[-1]}
            if tag.startswith("search_"):
                # 特性侧**全牌库检索**（2026-09-22 新增）
                # 与 `recon:N` 的区别：recon 只能从牌库顶 N 张里**随机**拿 1 张；
                # 这里是「从整副牌库检索指定类别」——两者语义不同，早期被混用，
                # 16 条真实特性因此把「想搜什么搜什么」变成"从牌堆顶碰运气"。
                if tag.startswith("search_energy:"):
                    detail = self._trainer_search_energy(p, tag)
                else:
                    detail = self._trainer_search_kind(p, tag)
                self._log("ability", {"player": self.current, "name": name,
                                      "from": fp.name, "detail": detail})
                return {"ok": True, "event": self.events[-1]}
            if tag.startswith("move_damage:"):
                # 移动伤害指示物：自身 → 对手 active（Munkidori「肾上腺素脑」）
                raw = tag.split(":")[1]
                n = 999 if raw == "all" else int(raw)
                opp = self.players[1 - self.current]
                dst = opp.active
                if dst is None:
                    raise ValueError("对手无 active 目标")
                if families.effect_immune(self, dst, "ability"):
                    self._log("blocked", {"target": dst.name,
                                          "by": "effect_immune",
                                          "effect": "ability_move_damage"})
                    return {"ok": True, "event": self.events[-1]}
                moved = 0
                for _ in range(n):
                    if fp.damage < 10:
                        break
                    fp.damage -= 10
                    dst.damage += 10
                    moved += 1
                self._log("ability", {"player": self.current, "name": name,
                                      "from": fp.name, "target": dst.name,
                                      "detail": f"move {moved} counters"})
                if dst.is_ko:
                    self._resolve_ko(1 - self.current, dst)
                return {"ok": True, "event": self.events[-1]}
            if tag.startswith("attach_spread:"):
                # 多张自由分配附着（2026-10-30 第三十三批）：排在
                # `attach_energy:` 之前，避免被同前缀分支吞掉。
                # 传 `fp` 是为了 `:selfko`（Magneton SSP 59「用掉就自爆」）
                detail = self._do_attach_spread(p, tag, action, holder=fp)
                self._log("ability", {"player": self.current, "name": name,
                                      "from": fp.name, "detail": detail})
                return {"ok": True, "event": self.events[-1]}
            if tag.startswith("attach_spread_flip:"):
                # 抛币计数版自由分配（2026-11-02 第三十八批，Mega Audino ex）
                detail = self._do_attach_spread_flip(p, tag, action, holder=fp)
                self._log("ability", {"player": self.current, "name": name,
                                      "from": fp.name, "detail": detail})
                return {"ok": True, "event": self.events[-1]}
            if tag.startswith("attach_each_bench:"):
                # 每只后备各 per 张（2026-11-02 第三十八批，Mega Gardevoir ex）
                detail = self._do_attach_each_bench(p, tag, action)
                self._log("ability", {"player": self.current, "name": name,
                                      "from": fp.name, "detail": detail})
                return {"ok": True, "event": self.events[-1]}
            if tag.startswith("attach_spread_multi:"):
                # 双色 or 1 of each（第三十九批，Steven's Metagross ex「X-Boot」）
                detail = self._do_attach_spread_multi(p, tag, action)
                self._log("ability", {"player": self.current, "name": name,
                                      "from": fp.name, "detail": detail})
                return {"ok": True, "event": self.events[-1]}
            if tag.startswith("attach_energy:"):
                # 附能（如「碧绿之舞」从手牌 / 「火焰之舞」从弃牌堆附 n 个指定色能量）
                n, etype, zone = _parse_attach_tag(tag)
                _tfp = _attach_target_fp(tag, p, action, fp)
                if _tfp is None:
                    self._log("no_effect", {"tag": tag, "reason": "无附能目标"})
                    return {"ok": True, "event": self.events[-1]}
                got = _attach_energy_from_zones(self, p, _tfp, n, etype, zone,
                                                allow_special=":anyenergy" in tag)
                if not got:
                    raise ValueError("无可附着的能量（指定区域为空）")
                _hl = _attach_heal_of(tag)
                if _hl:
                    # 附能成功才回复（卡面 "If you attached Energy …"）
                    _before = _tfp.damage
                    _tfp.damage = max(0, _tfp.damage - _hl)
                    if _before != _tfp.damage:
                        self._log("heal", {"player": self.current,
                                           "target": _tfp.name,
                                           "n": _before - _tfp.damage,
                                           "by": "attach"})
                _ac = _attach_counters_of(tag)
                if _ac:
                    # Toxtricity：「If you attached Energy …, place 2 damage
                    # counters on that Pokémon」——附能成功才放置。
                    _tfp.damage += _ac
                    self._log("damage", {"player": self.current,
                                         "target": _tfp.name, "dmg": _ac,
                                         "counters": True})
                    if _tfp.is_ko:
                        self._resolve_ko(self.current, _tfp)
                        if self.finished:
                            return {"ok": True, "event": self.events[-1]}
                self._log("ability", {"player": self.current, "name": name,
                                      "from": fp.name, "target": _tfp.name,
                                      "detail": f"attach {got} "
                                                f"{etype or 'any'} from {zone}"})
                return {"ok": True, "event": self.events[-1]}
            if tag.startswith("evolve_attach:"):
                raise ValueError("evolve_attach 为被动触发特性，不可主动发动")
            if tag.startswith("discard_opp_random:"):
                detail = self._discard_opp_random(int(tag.split(":")[1]))
                self._log("ability", {"player": self.current, "name": name,
                                      "from": fp.name, "detail": f"opp discard {detail}"})
                return {"ok": True, "event": self.events[-1]}
            if tag.startswith("shuffle_opp_hand:"):
                detail = self._shuffle_opp_hand(int(tag.split(":")[1]))
                self._log("ability", {"player": self.current, "name": name,
                                      "from": fp.name, "detail": f"opp shuffle {detail}"})
                return {"ok": True, "event": self.events[-1]}
            if tag.startswith("energy_move:"):
                # source="ability"：目标若免疫**特性**效果（`fx:effect_immune:ability`）
                # 则整条不执行 —— 与招式侧同一守卫，只是来源不同。
                detail = self._apply_energy_move(tag, fp, action,
                                                 source="ability")
                self._log("ability", {"player": self.current, "name": name,
                                      "from": fp.name, "detail": detail})
                return {"ok": True, "event": self.events[-1]}
            if tag.startswith(("coin:", "coin_flip:")):
                # 特性侧抛币（2026-11-03 第四十四批）：Dustox「Boisterous Wind」
                # 「Flip a coin. If heads, put an Energy attached to your
                # opponent's Active Pokémon into their hand.」
                detail = self._ability_coin(fp, tag, action)
                self._log("ability", {"player": self.current, "name": name,
                                      "from": fp.name, "detail": detail})
                return {"ok": True, "event": self.events[-1]}
            if tag.startswith("bounce:"):
                # S6 回手族（如 Alomomola「弃牌堆基础宝可梦铺后备」/ Duskull）
                detail = self._apply_bounce(tag, fp, action, source="ability")
                self._log("ability", {"player": self.current, "name": name,
                                      "from": fp.name, "detail": detail})
                return {"ok": True, "event": self.events[-1]}
            if tag.startswith("discard_tool:"):
                self._apply_discard_tool(tag, "ability")
                self._log("ability", {"player": self.current, "name": name,
                                      "from": fp.name, "detail": "discard tool"})
                return {"ok": True, "event": self.events[-1]}
            if tag == "discard_stadium":
                self._apply_discard_stadium()
                self._log("ability", {"player": self.current, "name": name,
                                      "from": fp.name, "detail": "discard stadium"})
                return {"ok": True, "event": self.events[-1]}
            if tag.startswith("status_self:"):
                st = tag.split(":", 1)[1]
                if st not in STATUSES:
                    raise ValueError(f"未知状态：{st}")
                fp.status.add(st)
                self._log("status", {"player": self.current, "name": name,
                                     "from": fp.name, "target": fp.name,
                                     "status": st, "side": "self"})
                return {"ok": True, "event": self.events[-1]}
            if tag in STATUSES:
                # 主动施加特殊状态（2026-10-27）：「make your opponent's Active
                # Pokémon Burned」。目标恒为对手战斗位。
                # ⚠ 只有解析侧确认过「正向施加」措辞的特性才会带这类 tag ——
                # 免疫 / checkup 追加伤害 / 换位触发 在解析时已被拒绝。
                opp = self.players[1 - self.current]
                if opp.active is None or opp.active.is_ko:
                    raise ValueError("对手无战斗位可施加状态")
                if families.effect_immune(self, opp.active, "ability"):
                    self._log("blocked", {"target": opp.active.name,
                                          "by": "effect_immune",
                                          "effect": "ability_status"})
                    return {"ok": True, "event": self.events[-1]}
                if self._status_blocked(opp.active, tag):
                    self._log("blocked", {"target": opp.active.name,
                                          "by": "status_immune",
                                          "effect": "status"})
                    return {"ok": True, "event": self.events[-1]}
                opp.active.status.add(tag)
                self._log("status", {"player": 1 - self.current, "name": name,
                                     "from": fp.name, "target": opp.active.name,
                                     "status": tag})
                return {"ok": True, "event": self.events[-1]}
            if tag.startswith("counters:"):
                # 特性直放伤害指示物（2026-10-29 第二十批）：Dusclops / Dusknoir
                # 「诅咒爆裂」——`:scope:opp` 任意对手宝可梦 + `:selfko` 自毁。
                _done = self._apply_counters(tag, action, holder=fp)
                self._log("ability", {"player": self.current, "name": name,
                                      "from": fp.name,
                                      "detail": f"counters: {tag}"
                                                + (" (self KO)" if _done else "")})
                return {"ok": True, "event": self.events[-1]}
            if tag.startswith("fx:"):
                # 效果族（fx）：一次性动作，语义由 engine/families/fx_*.py 定义
                detail = families.apply_action(
                    self, tag, {"kind": "ability", "holder": fp,
                                "action": action, "player": self.current})
                self._log("ability", {"player": self.current, "name": name,
                                      "from": fp.name, "detail": detail})
                return {"ok": True, "event": self.events[-1]}
            raise ValueError(f"未知 field tag {tag}")
        if "hand" in action:
            # ---- hand 特性（弃自身） ----
            hi = int(action["hand"])
            c = p.hand[hi]
            if not isinstance(c, PokeRef):
                raise ValueError("手牌目标非宝可梦")
            ab = next((x for x in c.pdef.abilities
                       if x.name == name and x.location == "hand"), None)
            if ab is None:
                raise ValueError(f"无 hand 特性 {name}")
            p.hand.pop(hi)
            self._to_discard(p, c)
            if tag.startswith("cursed:"):
                hp = int(tag.split(":")[1])
                opp = self.players[1 - self.current]
                tgt = _field_at(opp, int(action.get("target", 0)))
                if tgt is None:
                    raise ValueError("对手无该目标宝可梦")
                # Battle Cage：后备不受对手效果放置的伤害指示物
                if tgt is not opp.active and self._no_bench_counters():
                    self._log("blocked", {"target": tgt.name,
                                          "by": "no_bench_counters"})
                    return {"ok": True, "event": self.events[-1]}
                tgt.damage += hp            # 指示物伤害：不走弱抗
                self._log("ability", {"player": self.current, "name": name,
                                      "from": c.pdef.name, "target": tgt.name,
                                      "dmg": hp})
                if tgt.is_ko:
                    self._resolve_ko(1 - self.current, tgt)
                return {"ok": True, "event": self.events[-1]}
            if tag.startswith("fx:"):
                detail = families.apply_action(
                    self, tag, {"kind": "ability_hand", "card": c,
                                "action": action, "player": self.current})
                self._log("ability", {"player": self.current, "name": name,
                                      "from": c.pdef.name, "detail": detail})
                return {"ok": True, "event": self.events[-1]}
            raise ValueError(f"未知 hand tag {tag}")
        raise ValueError("ability 动作缺少 pos/hand")

    # ---------- 检视牌库顶（2026-10-30 第二十批） ----------
    def _recon_gate_ok(self, p: PlayerState, gate: Optional[str]) -> bool:
        """「You can use this card only if …」前置条件；未知条件**抛错**（不默认放行）。"""
        if not gate:
            return True
        if gate == "not_first_turn":
            # 「You can't use this card during your first turn」= **持有者自己**的首回合
            return self.turn_counts[self.current] > 1
        if gate == "ko_last_turn":
            fn = _CONDITIONS.get("own_ko_last_turn")
            if fn is None:
                raise ValueError("recon 前置条件未登记：ko_last_turn")
            return bool(fn(self, p, self.players[1 - self.current]))
        raise ValueError(f"未知 recon 前置条件：{gate}")

    def _play_gate_ok(self, p: PlayerState, gate: Optional[str]) -> bool:
        """卡面「You can use this card only if …」前置条件

        与 `_recon_gate_ok` 同一纪律：未知条件**抛错**（不默认放行）。
        条件语法复用 `_eval_condition`（如 `opp_prizes_le:2`）。
        """
        if not gate:
            return True
        return _eval_condition(self, gate)

    # ---------- 决策分解层（S1a，2026-11-08） ----------
    # 为什么放在 battle 里而不是 choice.py：选择必须能**读状态、改状态**（弃牌、
    # 放指示物），所以「选项构造」与「结果落地」天然属于 Battle；choice.py 只放
    # 纯数据结构（PendingChoice），便于回放序列化与单测。

    def _open_choice(self, kind: str, options: list, *, min_pick: int = 0,
                     max_pick: int = -1, tag: str = "", then: str = "",
                     allow_repeat: bool = False,
                     ctx: Optional[dict] = None) -> None:
        """挂起一次选择（把 phase 切到 choice）

        ⚠ 不允许多项挂起：本引擎的效果是**串行**的，两层待决会让「谁的选优先」
          变成隐式规则（改对局结果）。真需要嵌套时（S1b）再引入栈，并把排序
          写成显式 choice。
        """
        if getattr(self, "_pending", None) is not None:
            raise ValueError("已有未完成的决策（不允许嵌套挂起）")
        self._pending = PendingChoice(
            kind=str(kind), owner=self.current, options=list(options),
            min=int(min_pick), max=int(max_pick), tag=str(tag), then=str(then),
            allow_repeat=bool(allow_repeat), ctx=dict(ctx or {}))
        self.phase = "choice"
        self._log("choice_open", {"player": self.current, "kind": kind,
                                  "n_options": len(self._pending.options),
                                  "min": int(min_pick), "max": int(max_pick),
                                  "tag": str(tag)})

    def _choice_legals(self) -> list:
        ch = getattr(self, "_pending", None)
        if ch is None:
            return []
        out = []
        for i in ch.available():
            opt = ch.options[i]
            out.append({"verb": "choose", "pick": i, "index": i,
                        "display": opt.get("display") or f"选项 {i}",
                        "option": opt, "kind": ch.kind})
        if ch.can_stop():
            out.append({"verb": "stop", "display": "结束选择",
                        "kind": ch.kind, "picked": len(ch.picked)})
        return out

    def _step_choice(self, action: dict) -> dict:
        ch = getattr(self, "_pending", None)
        if ch is None:
            return {"ok": False, "error": "no_pending_choice"}
        verb = action.get("verb")
        if verb == "choose":
            try:
                ch.pick(int(action.get("pick", -1)))
            except (TypeError, ValueError) as e:
                return {"ok": False, "error": f"bad_pick: {e}"}
            self._log("choice_pick", {"player": self.current, "kind": ch.kind,
                                      "pick": ch.picked[-1],
                                      "option": ch.options[ch.picked[-1]]})
            if ch.is_full():
                return self._close_choice()
            return {"ok": True, "event": self.events[-1]}
        if verb == "stop":
            if not ch.can_stop():
                return {"ok": False,
                        "error": f"choose at least {ch.min} before stop"}
            return self._close_choice()
        # ⚠ 明确拒绝其它动词：挂起期间做别的事 = 跳过选择（静默默认）
        return {"ok": False, "error": f"phase=choice 只接受 choose/stop（收到 {verb}）"}

    def _close_choice(self) -> dict:
        ch = self._pending
        self._pending = None
        self.phase = "main"
        self._log("choice_close", {"player": self.current, "kind": ch.kind,
                                   "picked": len(ch.picked)})
        self._apply_choice_result(ch)
        # S6b：挂起期间被拦下的击倒 → 现在按序补做（可能再开一次选择）
        self._drain_ko_queue()
        if ch.then:
            for t in [x for x in str(ch.then).split("|") if x]:
                self._run_simple_tag(t)
        # 招式侧挂起时回合结束被延后（见 `_end_turn`）→ 这里补上。
        # ⚠ S6b：补做击倒后又可能**再挂一个选择**（多只 + 面朝上奖赏卡），
        #   此时不能换手 —— 否则选择会落到对手回合。保持延后标记，等它闭合。
        if getattr(self, "_defer_end_turn", False) and self._pending is None:
            self._defer_end_turn = False
            if not self.finished:
                self._end_turn()
        return {"ok": True, "event": self.events[-1] if self.events else None}

    def _apply_choice_result(self, ch) -> None:
        """把选择结果落到状态上（每种 kind 一个分支，未知即抛错）"""
        p = self.players[ch.owner]
        if ch.kind == "pick_prize":
            # S6b：「取哪张奖赏卡」。选面朝上的那张 → 指定槽位；选背面 → 随机。
            taker = int(ch.ctx.get("taker", ch.owner))
            n = int(ch.ctx.get("n", 1))
            opt = ch.chosen()[0]
            slot = opt.get("slot") if opt.get("mode") == "up" else None
            taken = zones.take_prizes(self, taker, n, first_face_up=slot)
            self._log("prize_pick", {"player": taker, "mode": opt.get("mode"),
                                     "slot": slot,
                                     "card": opt.get("label"),
                                     "taken": taken})
            ko_state = ch.ctx.get("ko")
            if ko_state:
                self._ko_after_prizes(ko_state, taken)
            return
        if ch.kind == "discard_hand":
            # 「you may discard any number of cards from your hand」—— 逐张弃。
            # 选项里存的是**手牌下标快照**：弃牌会让后面的下标失效，因此按
            # **卡对象身份**定位（`_card_identity`），不按下标。
            for opt in ch.chosen():
                ref = opt.get("ref")
                idx = next((i for i, c in enumerate(p.hand) if c is ref), None)
                if idx is None:
                    raise ValueError("选择的手牌已不在手中（内部状态异常）")
                self._to_discard(p, p.hand.pop(idx))
        elif ch.kind == "swap_hand_top":
            opt = ch.chosen()[0]
            ref = opt.get("ref")
            idx = next((i for i, c in enumerate(p.hand) if c is ref), None)
            if idx is None or not p.deck:
                raise ValueError("交换目标已失效（内部状态异常）")
            card = p.hand.pop(idx)
            top = p.deck.pop()            # deck[-1] = 牌库顶
            p.hand.append(top)
            p.deck.append(card)           # 原手牌放到牌库顶
            self._log("swap_hand_top", {"player": ch.owner,
                                        "put": _card_label(card),
                                        "got": _card_label(top)})
        elif ch.kind == "may_use":
            # S2e：可选触发的「使用 / 不使用」。选「使用」→ 跑子效果（可能再开
            # 一层选择；此时上一层已经关闭，不构成嵌套挂起）。
            opt = ch.chosen()[0] if ch.chosen() else {"yes": False}
            if not opt.get("yes"):
                self._log("optional_declined", {"player": ch.owner,
                                                "tag": ch.tag})
                return
            self._run_simple_tag(str(ch.ctx.get("body") or ""),
                                 holder=ch.ctx.get("fp"))
        elif ch.kind == "pick_energy":
            # S2e：Latios「Move **any amount** of Energy from your Benched
            # Pokémon to your Active Pokémon」——逐点搬（每个选项 = 从某只搬 1 点
            # 某色，或搬走 1 张特殊能量卡）。玩家选几次就搬几次，`stop` 结束。
            dst = p.active
            moved = 0
            for opt in ch.chosen():
                fp = _field_at(p, int(opt.get("pos", 0)))
                if fp is None or dst is None or fp is dst:
                    continue
                if opt.get("sref") is not None:
                    sref = opt["sref"]
                    if any(r is sref for r in fp.senergy):
                        fp.senergy.remove(sref)
                        dst.senergy.append(sref)
                        moved += 1
                    continue
                et = str(opt.get("etype") or "")
                if fp.energy.get(et, 0) > 0:
                    fp.energy[et] -= 1
                    if fp.energy[et] <= 0:
                        del fp.energy[et]
                    dst.add_energy(et, 1)
                    moved += 1
            self._log("energy_move", {"player": ch.owner, "n": moved,
                                      "from": "bench", "to": "active",
                                      "by": "may_use"})
        elif ch.kind == "confirm":
            opt = ch.chosen()[0] if ch.chosen() else {"yes": False}
            if not opt.get("yes"):
                self._log("optional_declined", {"player": ch.owner,
                                                "tag": ch.tag})
                return
            fp = opt.get("fp")
            n = int(opt.get("n") or 0)
            etype = str(opt.get("etype") or "")
            if fp is None or fp.total_energy() < n:
                # 付不出 → 选择作废（卡面「You may … and …」按不支付处理），
                # 但必须记日志：静默吞掉会让「AI 选了却什么都没发生」无法追查。
                self._log("optional_unpaid", {"player": ch.owner,
                                              "n": n, "etype": etype})
                return
            left = n
            for _ in range(n):
                if fp.energy.get(etype, 0) > 0:
                    fp.energy[etype] -= 1
                    if fp.energy[etype] <= 0:
                        fp.energy.pop(etype, None)
                    left -= 1
                elif fp.senergy:
                    fp.senergy.pop()
                    left -= 1
            self._log("optional_paid", {"player": ch.owner, "n": n - left,
                                        "etype": etype})
            self._run_simple_tag(str(opt.get("then_tag") or ""))
        elif ch.kind == "counters_any_opp":
            # 「Put N damage counters on your opponent's Pokémon in any way you
            # like」—— 指示物**不吃弱点/抗性**，直接加 damage（10/枚）。
            opp = self.players[1 - ch.owner]
            for opt in ch.chosen():
                tgt = _field_at(opp, int(opt["target"]))
                if tgt is None:
                    raise ValueError("指示物目标已不在场上（内部状态异常）")
                tgt.damage += 10
                self._log("counters_choice", {"player": ch.owner,
                                              "target": tgt.name, "n": 1})
        elif ch.kind == "prize_flip_opp":
            # S3：Team Rocket's Bother-Bot 第一段 —— 把对手 1 张背面朝上的奖赏卡
            # 翻到正面（**哪一张由牌手选**，不是引擎挑），然后随机看对手 1 张手牌，
            # 再挂起「要不要交换」的确认选择。
            opt = ch.chosen()[0]
            opp = self.players[1 - ch.owner]
            si = int(opt["slot"])
            if not (0 <= si < len(opp.prizes)):
                raise ValueError("奖赏卡位已失效（内部状态异常）")
            opp.prizes[si].face_up = True
            self._log("prize_flip", {"player": 1 - ch.owner, "slot": si,
                                     "card": _card_label(opp.prizes[si].ref)})
            if not opp.hand:
                self._log("prize_swap", {"player": ch.owner,
                                         "skipped": "对手手牌为空"})
                return
            hi = self.rng.randrange(len(opp.hand))     # 官方：**随机**一张
            self._log("hand_reveal", {"player": 1 - ch.owner, "index": hi,
                                      "card": _card_label(opp.hand[hi])})
            self._open_choice(
                "prize_swap_confirm",
                [{"display": "交换这两张卡", "yes": True},
                 {"display": "不交换", "yes": False}],
                min_pick=1, max_pick=1, tag=ch.tag,
                ctx={"slot": si, "hand_index": hi})
        elif ch.kind == "attach_basic_confirm":
            # S2c：Powerglass —— 由牌手选**哪个颜色**的能量附着，或干脆不附着
            opt = ch.chosen()[0] if ch.chosen() else {"pass": True}
            side = int(ch.ctx.get("side", ch.owner))
            tgt = ch.ctx.get("fp")
            p = self.players[side]
            if opt.get("pass"):
                self._log("optional_declined", {"player": side,
                                                "tag": ch.tag})
                return
            et = str(opt.get("etype") or "")
            idx = next((i for i, ref in enumerate(p.discard_refs)
                        if isinstance(ref, EnergyRef)
                        and not getattr(ref.edef, "is_special", False)
                        and str(ref.edef.type) == et), None)
            if idx is None or tgt is None or tgt not in p.all_field():
                self._log("no_effect", {"player": side, "tag": ch.tag,
                                        "reason": "目标或能量已失效"})
                return
            ref = p.discard_refs.pop(idx)
            p.discard = [x for x in p.discard]      # 文本层后续补一条即可
            p.discard.append(_card_label(ref))
            tgt.add_energy(et, 1)
            self._log("attach_energy", {"player": side, "target": tgt.name,
                                        "etype": et, "by": "on_event"})
            return
        elif ch.kind == "prize_swap_confirm":
            opt = ch.chosen()[0] if ch.chosen() else {"yes": False}
            opp = self.players[1 - ch.owner]
            si = int(ch.ctx.get("slot", -1))
            hi = int(ch.ctx.get("hand_index", -1))
            if not opt.get("yes"):
                self._log("prize_swap", {"player": ch.owner, "swapped": False})
                return
            if not (0 <= si < len(opp.prizes)) or not (0 <= hi < len(opp.hand)):
                raise ValueError("交换目标已失效（内部状态异常）")
            slot = opp.prizes[si]
            hand_card = opp.hand[hi]
            # 奖赏卡与手牌互换。**该奖赏位保持正面朝上**（卡面
            # 「That Prize card remains face up for the rest of the game.」——
            # 已翻开的奖赏位不会因为换了卡而重新盖上），因此换进去的卡可见。
            opp.prizes[si] = zones.PrizeSlot(hand_card, face_up=True)
            opp.hand[hi] = slot.ref
            self._log("prize_swap", {"player": ch.owner, "swapped": True,
                                     "prize_now": _card_label(hand_card),
                                     "hand_now": _card_label(slot.ref)})
        elif ch.kind == "opp_bench_from_deck":
            # S3：Accompanying Flute —— 从对手牌库顶亮出的 N 张里**任意数量**
            # 基础宝可梦放到对手后备；余牌洗回对手牌库。
            opp = self.players[1 - ch.owner]
            revealed = list(ch.ctx.get("revealed") or ())
            chosen_ids = {id(o["ref"]) for o in ch.chosen()}
            rest = []
            placed = 0
            for ref in revealed:
                if id(ref) in chosen_ids:
                    if len(opp.bench) >= self.cfg.max_bench:
                        # 后备满了 → 该牌回到余牌（绝不静默丢牌）
                        rest.append(ref)
                        continue
                    fnew = make_pokemon(ref.pdef)
                    fnew.just_played = True
                    opp.bench.append(fnew)
                    placed += 1
                    self._log("bench_from_deck", {"player": 1 - ch.owner,
                                                  "card": fnew.name})
                else:
                    rest.append(ref)
            if rest:
                opp.deck.extend(rest)          # 先整叠放回，再洗 → 位置不可预测
                self.rng.shuffle(opp.deck)
            self._log("flute_done", {"player": ch.owner, "placed": placed,
                                     "shuffled": len(rest)})
        else:
            raise ValueError(f"未实现的 choice kind：{ch.kind}")

    def _run_simple_tag(self, tag: str, holder=None) -> None:
        """执行「选择之后」的收尾 tag（未知形态**抛错**）

        ⚠ 绝不静默跳过：`then` 里的动作如果没执行，卡面就少做了一半，
          而对局里看不出来（正是历史「整卡已建模」误判的来源）。

        `holder`（S2e）：可选触发的载体宝可梦（`may_use` 选择里存下来的那只）。
        `replace_self` / `pick_energy` 这类效果要知道「是谁」在做事。
        """
        p = self.players[self.current]
        t = str(tag)
        if t.startswith("pick_energy:"):
            # S2e：Latios「Move any amount of Energy from your Benched Pokémon to
            # your Active Pokémon」——**逐点选**，玩家 `stop` 才结束。
            # 选项 = 每个「后备位置 × 可搬的能量」；特殊能量按**卡**单独一项
            # （卡面是「Energy」，特殊能量是卡、基本能量是点数）。
            opts: list = []
            for bi, fp in enumerate(p.bench, start=1):
                if fp is None:
                    continue
                for et in sorted(fp.energy):
                    if fp.energy.get(et, 0) <= 0:
                        continue
                    opts.append({"display": f"从 {fp.name} 搬 1 点 {et} 能量到战斗位",
                                 "pos": bi, "etype": et})
                for ref in list(fp.senergy):
                    opts.append({"display": f"从 {fp.name} 搬 1 张特殊能量到战斗位",
                                 "pos": bi, "sref": ref})
            if not opts or p.active is None:
                self._log("no_effect", {"tag": t, "reason": "后备没有能量可搬"})
                return
            self._open_choice("pick_energy", opts, min_pick=0, max_pick=-1,
                              tag=t, allow_repeat=True,
                              ctx={"by": "may_use"})
            return
        if t.startswith("replace_self:"):
            # S2e：Palafin「Zero to Hero」——检索指定卡，**原地替换**（附着卡 /
            # 伤害指示物 / 特殊状态 / 在场回合数全部保留），旧卡洗回牌库。
            self._do_replace_self(p, holder, t)
            return
        if t.startswith("paralyze") or t == "confuse" or t == "sleep":
            t = {"paralyzed": "paralyze", "confused": "confuse",
                 "asleep": "sleep"}.get(t, t)
            # 「… and make your opponent's Active Pokémon Paralyzed.」
            opp = self.players[1 - self.current]
            if opp.active is None or opp.active.is_ko:
                return
            opp.active.status.add(t)
            self._log("status", {"player": self.current,
                                 "target": opp.active.name, "status": t})
            return
        if t.startswith("draw_to:"):
            n = int(t.split(":")[1])
            self._safe_draw(self.current, max(0, n - len(p.hand)),
                            source="choice")
            return
        raise ValueError(f"未实现的收尾 tag：{t}")

    def _do_replace_self(self, p: PlayerState, holder, tag: str) -> None:
        """把 `holder` 与牌库里指定的卡**原地对调**（S2e，Palafin「Zero to Hero」）

        tag：``replace_self:<卡名>:to_deck``

        卡面：「search your deck for a Palafin {ex} and switch it with this
        Pokémon. **Any attached cards, damage counters, Special Conditions,
        turns in play, and any other effects remain on the new Pokémon.** If you
        switched a Pokémon in this way, put this card into your deck. Then,
        shuffle your deck.」

        ⚠ 三条容易做错的地方：
          1. **原地**替换：新卡顶到旧卡的**那个位置**（旧卡在备位就还是备位，
             绝不能让新卡跑去战斗位或后备末尾 —— 那会改变战斗位）。
          2. **全量继承状态**：不只是能量，还有伤害、特殊状态、道具、护盾、
             跨回合锁定、在场回合数、已用特性……逐字段抄（见 `_inherit_state`）。
          3. 旧卡**洗回牌库**而不是进弃牌堆，然后**洗牌**。
        """
        parts = str(tag).split(":")
        want = parts[1] if len(parts) > 1 else ""
        mode = parts[2] if len(parts) > 2 else "to_deck"
        if mode != "to_deck":
            raise ValueError(f"replace_self 只支持 to_deck：{tag}")
        if holder is None or holder not in p.all_field():
            self._log("no_effect", {"tag": tag, "reason": "载体不在场上"})
            return
        key = _name_key(want)
        di = next((i for i, ref in enumerate(p.deck)
                   if isinstance(ref, PokeRef) and _name_key(ref.pdef.name) == key),
                  None)
        if di is None:
            self._log("no_effect", {"tag": tag, "reason": "牌库没有该卡",
                                    "want": want})
            return
        ref = p.deck.pop(di)
        new = make_pokemon(ref.pdef)
        _inherit_state(holder, new)
        if p.active is holder:
            p.active = new
            pos = "active"
        else:
            bi = p.bench.index(holder)
            p.bench[bi] = new
            pos = f"bench{bi + 1}"
        p.deck.append(PokeRef(holder.pdef))
        self.rng.shuffle(p.deck)
        self._log("replace_self", {"player": self.current, "from": holder.name,
                                   "to": new.name, "pos": pos,
                                   "into": "deck", "shuffle": True})

    # ---------- S2（2026-11-08）：延迟效果 / 事件订阅的执行体 ----------

    def _run_queued_tag(self, it) -> None:
        """执行一条**到期**的延迟效果（由 `events.resolve_turn_end` 调用）

        ⚠ 未知形态一律抛错：延迟效果「到点了却没执行」是最隐蔽的静默丢效果，
          而对局里完全看不出来（卡面说做了，实际没做）。
        """
        tag = str(it.tag)
        p = self.players[it.owner]
        parts = [x for x in tag.split(":") if x]
        if parts[:1] == ["discard_hand"]:
            # 「At the end of this turn, if you have 5 or more cards in your hand,
            #   discard your hand.」（Amarys）
            mode = parts[1] if len(parts) > 1 else "all"
            cond = ":".join(parts[parts.index("if") + 1:]) if "if" in parts else ""
            if cond and not _eval_condition(self, cond):
                self._log("no_effect", {"tag": tag, "reason": "条件不满足",
                                        "player": it.owner})
                return
            n = len(p.hand) if mode == "all" else int(mode)
            n = max(0, min(n, len(p.hand)))
            dropped = list(p.hand[:n])
            for c in dropped:
                self._to_discard(p, c)
            del p.hand[:n]
            self._log("discard_hand", {"player": it.owner, "n": len(dropped),
                                       "by": it.source, "tag": tag})
            return
        if parts[:1] == ["discard_defender"]:
            # S2c：「discard the Defending Pokémon and all attached cards」
            # （TR Grimer）。**这是「弃置」不是「击倒」** → 不取奖赏
            # （取奖赏会凭空给对手 1~3 张，属于实质改变胜负）。
            side = int(it.ctx.get("side", 1))
            tgt = it.ctx.get("target")
            q = self.players[side]
            if tgt is None or tgt not in q.all_field():
                self._log("no_effect", {"tag": tag, "player": side,
                                        "reason": "目标已不在场上"})
                return
            was_active = q.active is tgt
            for _et, _cnt in list(tgt.energy.items()):
                for _ in range(_cnt):
                    q.discard.append(f"{_et} energy")
            for _sref in list(tgt.senergy):
                self._to_discard(q, _sref)
            if tgt.tool:
                if tgt.tool_ref is not None:
                    self._to_discard(q, tgt.tool_ref)
                else:
                    q.discard.append(tgt.tool)
            tgt.energy.clear()
            tgt.senergy.clear()
            _drop_tool(tgt)
            if was_active:
                q.active = None
            else:
                q.bench = [x for x in q.bench if x is not tgt]
            self._to_discard(q, PokeRef(tgt.pdef))
            self._log("discard_from_field", {"player": side,
                                             "card": tgt.name,
                                             "by": it.source,
                                             "active": was_active})
            if was_active:
                if q.bench:
                    q.active = q.bench.pop(0)
                    q.active.moved_active_turn = self.turn_number
                    q.active.clear_status()
                    self._log("promote", {"player": side, "to": q.active.name})
                else:
                    self._finish(1 - side, "no_pokemon", {"loser": side})
            return
        raise ValueError(f"未实现的延迟 tag：{tag}")

    def _effect_blocked(self, target, source: str, actor: int,
                        kinds: str = "") -> bool:
        """统一守卫（S4）：``actor`` 施加给 ``target`` 的 ``source`` 类效果是否被免疫

        三条判定，缺一不可：
          ① **自己对自己做的事不算**「对手的效果」——否则「自己用招式弃自己
             能量」会被自己的免疫挡住（`actor == target 的拥有者` 直接放行）；
          ② 特性侧 `fx:effect_immune:*`（含团队范围）；
          ③ 特殊能量侧（Mist / Rock Fighting 只挡招式效果）。

        `source` ∈ attack / ability / trainer；`kinds` 只在 trainer 用
        （`item` / `supporter`），对应卡面「plays an Item or Supporter card」。
        """
        if target is None:
            return False
        # ⚠ 训练家来源**必须**给出卡类别：忘传会让免疫静默失效（对手的卡照常
        #   生效 = 静默加强），因此这里直接抛错而不是猜一个默认值。
        if source == "trainer" and not kinds:
            raise ValueError("trainer 免疫判定必须给出卡类别（item / supporter）")
        if int(actor) == self._owner_of(target):
            return False
        if families.effect_immune(self, target, source, kinds):
            return True
        return special_energy.effect_immune(target, source)

    def _trainer_poke_blocked(self, fp, kind: str) -> bool:
        """训练家卡（Item / Supporter）对 ``fp`` 的效果是否被免疫（S4）

        只认「从手牌打出的 Item / Supporter」——场地/特性触发的效果不走这里
        （卡面写死 `plays an Item or Supporter card from their hand`）。
        """
        if not fp:
            return False
        if not kind:
            # `kind` 为空 = **不是**「对手从手牌打出的 Item/Supporter」：
            # 特性触发的同类效果（如特性版拉人）与内部直调都属于这一类，
            # 免疫不适用。正式派发（`_dispatch_trainer_tag`）一定带真实卡类别。
            return False
        if self._effect_blocked(fp, "trainer", self.current, kind):
            self._log("blocked", {"target": getattr(fp, "name", None),
                                  "by": "effect_immune", "effect": "trainer",
                                  "kind": kind})
            return True
        return False

    def _run_watch_body(self, owner: int, fp, name: str, body: str,
                        kind: str, ctx: dict, fixed: Optional[dict] = None) -> None:
        """执行一条 `on_event:<kind>:<body>` 订阅

        body 文法（未知形态一律抛错，绝不静默跳过）::

            counters:<N>:on:<leaving|entering|holder|target>[:scope:<...>]
            status:<st>:on:<...>[:scope:<...>]
            search_any:<N>:on:<...>
            attach_basic:on:<...>
            move_energy_to_bench:<N>:on:<leaving>[:require:<cond>]

        `scope`：
          · `opp_their_turn`（默认）—— 换位方必须是**订阅者的对手**，且这次换位
            发生在**换位方自己的回合**（卡面「during their turn」）；
          · `own_by_attack` —— 己方被**招式伤害**击倒（S2c）；
          · `own_turn` —— 事件发生在订阅者自己的回合（S2c/S2d）；
          · `any` —— 不限定（卡面没写「during their turn」时用）。

        `fixed`（S2d）：**限时订阅**在建立时存的快照。此时 `fp` 为 None
        （订阅没有载体卡），目标要取自 `fixed["target"]` —— 到触发时那只可能
        早已进化/换下，绝不能重新猜。`match:event_target` 进一步要求「这次事件
        就发生在快照的那只身上」（Pachirisu：附着目标必须是当时的防御宝可梦）。

        `require:`（S2d）：附加前置条件，目前两种 ——
          · `require:was_active`        事件对象当时在战斗位
          · `require:retreat:<N>`       事件对象的撤退费**恰好** N
        """
        parts = [x for x in str(body).split(":") if x]
        if "on" not in parts:
            raise ValueError(f"on_event 订阅缺少 on: 段：{body}")
        subj = parts[parts.index("on") + 1]
        scope = (parts[parts.index("scope") + 1]
                 if "scope" in parts else "opp_their_turn")
        who = int(ctx.get("player", -1))
        turn_owner = int(ctx.get("turn_owner", -1))
        if scope == "opp_their_turn":
            if who == owner or turn_owner != who:
                self._log("event_scope_skip", {"kind": kind, "by": name,
                                               "owner": owner, "mover": who,
                                               "turn_owner": turn_owner})
                return
        elif scope == "own_by_attack":
            # S2c：「**我的**宝可梦被对手宝可梦的招式伤害击倒」（道具载体）。
            # 两个条件都不能省：① 被击倒的是订阅者自己那侧；② 必须是**招式伤害**
            # （中毒 / 烧伤 / 自爆击倒时不触发 —— 卡面写死 by damage from an attack）。
            if who != owner or not ctx.get("by_attack"):
                self._log("event_scope_skip", {"kind": kind, "by": name,
                                               "owner": owner, "mover": who,
                                               "by_attack": ctx.get("by_attack")})
                return
        elif scope == "own_turn":
            # S2c：「At the end of **your** turn …」—— 回合归属必须是订阅者自己
            if who != owner or turn_owner != owner:
                self._log("event_scope_skip", {"kind": kind, "by": name,
                                               "owner": owner, "mover": who,
                                               "turn_owner": turn_owner})
                return
        elif scope != "any":
            raise ValueError(f"未知 on_event scope：{scope}")
        head = parts[0]
        if head == "search_any":
            # S2c：「search your deck for up to N cards and put them into your
            # hand. Then, shuffle your deck.」（Amulet of Hope）—— 与目标无关，
            # 因此**在**目标存在性检查之前处理（被击倒的那只已经下场了）。
            n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
            # ⚠ 必须用 `_take_any_from_deck`（连能量卡也算）。`_kind_matches(c,
            # "any")` 在既有实现里**排除能量**，用 `search_any:` 会静默少检索
            # （实测：牌库里 12 张能量、一张都拿不到）。卡面写的是「any cards」。
            pq = self.players[owner]
            got = _take_any_from_deck(pq, n)
            if got:
                self.rng.shuffle(pq.deck)
            self._log("event_search", {"kind": kind, "by": name,
                                       "owner": owner, "n": got,
                                       "shuffled": bool(got)})
            return
        if subj == "holder":
            tgt = fp                       # 载体自己（Powerglass 的持有者）
        elif subj == "target":
            # S2d：限时订阅的**快照目标**（Pachirisu 的「当时的防御宝可梦」）
            tgt = (fixed or {}).get("target")
        elif subj == "leaving":
            tgt = ctx.get("leaving")
        else:
            tgt = ctx.get("entering")
        if tgt is None:
            return
        # `match:event_target`（S2d）——「这次事件就发生在快照的那只身上」。
        # Pachirisu 写的是「attach … **to the Defending Pokémon**」：对手附给
        # 别的宝可梦不该触发（漏判 = 无条件每附一张就打 80）。
        if "match" in parts and parts[parts.index("match") + 1] == "event_target":
            if (fixed or {}).get("target") is not ctx.get("target"):
                self._log("event_scope_skip", {"kind": kind, "by": name,
                                               "owner": owner,
                                               "reason": "非快照目标"})
                return
        # `match:event_leaving`（S2e）——「**这只**从战斗位下场」
        # （Palafin「Zero to Hero」：`when this Pokémon moves from the Active Spot
        # to the Bench`）。不判就会变成「任何一只换下都触发」。
        if "match" in parts:
            _mk = parts[parts.index("match") + 1]
            if _mk == "event_leaving" and ctx.get("leaving") is not tgt:
                self._log("event_scope_skip", {"kind": kind, "by": name,
                                               "owner": owner,
                                               "reason": "不是本次换下的那只"})
                return
            if _mk == "event_name" and len(parts) > parts.index("match") + 2:
                _want = _name_key(parts[parts.index("match") + 2])
                _ev_fp = (ctx.get("entering") if ctx.get("entering") is not None
                          else ctx.get("leaving"))
                _got = _name_key(getattr(_ev_fp, "name", ""))
                if _got != _want:
                    self._log("event_scope_skip", {"kind": kind, "by": name,
                                                   "owner": owner,
                                                   "reason": "事件对象名称不符",
                                                   "want": _want, "got": _got})
                    return
        # `require:` 前置条件（S2d）：写清每种判定，未知条件直接抛错
        _req = {}
        _i = 0
        while _i < len(parts):
            if parts[_i] == "require" and _i + 1 < len(parts):
                _k = parts[_i + 1]
                if _k == "retreat":
                    if _i + 2 >= len(parts) or not parts[_i + 2].isdigit():
                        raise ValueError(f"require:retreat 缺数值：{body}")
                    _req["retreat"] = int(parts[_i + 2])
                    _i += 3
                    continue
                _req[_k] = True
                _i += 2
                continue
            _i += 1
        if _req.get("was_active") and not ctx.get("was_active"):
            self._log("event_scope_skip", {"kind": kind, "by": name,
                                           "owner": owner,
                                           "reason": "不在战斗位"})
            return
        if "retreat" in _req and _retreat_cost(tgt, self) != _req["retreat"]:
            self._log("event_scope_skip", {"kind": kind, "by": name,
                                           "owner": owner,
                                           "reason": "撤退费不符",
                                           "retreat": _retreat_cost(tgt, self),
                                           "want": _req["retreat"]})
            return
        if tgt not in self.players[who].all_field():
            # 已经离场（回手 / 洗回牌库）→ 放置类效果无对象，记日志而不是假装做了
            self._log("no_effect", {"kind": kind, "by": name, "owner": owner,
                                    "reason": "目标已离场", "target": tgt.name})
            return
        # `may`（S2e）：「you may use this Ability / you may search your deck …」
        # —— **必须由牌手决定**。引擎自选 = 替玩家做决定（S1 定下的不变量）。
        # 选项只有「使用 / 不使用」；具体怎么做交给子效果（关掉这次选择后再开
        # 下一次选择，绝不嵌套挂起）。
        if "may" in parts:
            _sub = ":".join([x for x in parts if x != "may"])
            self._open_choice(
                "may_use",
                [{"display": "使用该效果", "yes": True},
                 {"display": "不使用", "yes": False}],
                min_pick=1, max_pick=1, tag=f"on_event:{kind}",
                ctx={"body": _sub, "fp": tgt})
            return
        if head == "move_energy_to_bench":
            # S2d：Heavy Baton「move up to N Basic Energy cards from that Pokémon
            # to your Benched Pokémon in any way you like」。
            # 「in any way you like」沿用 `attach_spread` 的既有口径：**轮转**
            # 分配（可复现，且不会把 N 张全堆在一只身上）。
            # ⚠ 这是**弃置前**的抢救：`_resolve_ko` 里附着能量不进 `discard_refs`，
            #   错过这一瞬间就永久丢了（S3 已登记的边界）。
            _n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
            _side = who
            _q = self.players[_side]
            _pool = [x for x in _q.bench if x is not tgt]
            if not _pool or not tgt.energy:
                self._log("no_effect", {"kind": kind, "by": name,
                                        "owner": owner,
                                        "reason": "无可搬移目标或能量",
                                        "bench": len(_pool)})
                return
            _moved = 0
            for _k in range(max(0, _n)):
                _et = next((t for t, c in tgt.energy.items() if c > 0), None)
                if _et is None:
                    break
                tgt.energy[_et] -= 1
                if tgt.energy[_et] <= 0:
                    del tgt.energy[_et]
                _dst = _pool[_moved % len(_pool)]
                _dst.add_energy(_et, 1)
                _moved += 1
            self._log("energy_move", {"player": _side, "n": _moved,
                                      "from": tgt.name,
                                      "to": "bench", "by": name,
                                      "event": kind})
            return
        if head == "attach_basic":
            # S2c：Powerglass「you may attach a Basic Energy card from your
            # discard pile to it」—— **可选**：落成 confirm 选择，绝不默认附着。
            kinds = sorted({str(ref.edef.type) for ref in self.players[owner].discard_refs
                            if isinstance(ref, EnergyRef)
                            and not getattr(ref.edef, "is_special", False)})
            if not kinds:
                self._log("no_effect", {"kind": kind, "by": name,
                                        "reason": "弃牌堆没有基本能量"})
                return
            self._open_choice(
                "attach_basic_confirm",
                [{"display": f"附着 1 个{_k}能量", "etype": _k} for _k in kinds]
                + [{"display": "不附着", "pass": True}],
                min_pick=1, max_pick=1, tag=f"on_event:{kind}",
                ctx={"side": owner, "fp": tgt})
            return
        if head == "counters":
            n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
            # 指示物**不吃弱点/抗性**（与 counters: 族同口径）
            tgt.damage += n * 10
            self._log("damage", {"player": owner, "target": tgt.name,
                                 "dmg": n * 10, "counters": True,
                                 "by": name, "event": kind})
            if tgt.is_ko:
                self._resolve_ko(who, tgt)
            return
        if head == "status":
            st = parts[1] if len(parts) > 1 else ""
            if st not in STATUSES:
                raise ValueError(f"on_event status 状态词非法：{st}")
            if self._status_blocked(tgt, st):
                self._log("blocked", {"target": tgt.name, "by": "status_immune",
                                      "effect": st})
                return
            tgt.status.add(st)
            self._log("status", {"player": owner, "target": tgt.name,
                                 "status": st, "by": name, "event": kind})
            return
        raise ValueError(f"未实现的 on_event 效果：{body}")

    def _do_recon(self, p: PlayerState, tag: str, action: dict) -> str:
        """检视牌库顶 N 张 → 按筛选取 ≤K 张 → 入手 / 铺场 / 附着，其余放回牌库。

        旧口径 ``recon:<N>``（无第三段）保持历史行为：**随机**取 1 张入手、其余按
        原序放牌库底。扩展口径才有筛选项与去向，两者不共用选择策略。
        """
        spec = _parse_recon_tag(tag)
        if not self._recon_gate_ok(p, spec["gate"]):
            raise ValueError("不满足该卡的打出前置条件")
        if not p.deck:
            raise ValueError("牌库为空")
        n = min(spec["look_top"], len(p.deck))
        top = [p.deck.pop() for _ in range(n)]      # top[0] = 牌库顶
        if spec["legacy"]:
            pick = self.rng.randrange(len(top))
            chosen = top.pop(pick)
            p.hand.append(chosen)
            for c in reversed(top):                 # 其余放回牌库底
                p.deck.insert(0, c)
            return f"top{spec['look_top']} pick1"

        sel, dest, k = spec["sel"], spec["dest"], spec["max"]
        fp = None
        _plan: list = []
        _targets: list = []
        if dest == "attach":
            tpos = action.get("target")
            fp = (_field_at(p, int(tpos)) if tpos is not None else None) \
                or p.active
            if fp is None:
                raise ValueError("没有可附着的宝可梦")
        elif dest == "attach_spread":
            # 「attach any number of Energy cards you find there to your Pokémon
            # **in any way you like**」（2026-11-02 第三十五批：Lapras ex /
            # Metang）。分配口径与 `attach_spread:` **完全一致**：显式
            # `attach_plan` 或 round-robin，绝不把全部能量堆在 active 上 ——
            # 那等于把「自由分配」塌缩成一个特定选择。
            _targets = _spread_targets(p, "any")
            if not _targets:
                raise ValueError("没有可附着的宝可梦")
            _p = action.get("attach_plan") if isinstance(action, dict) else None
            if _p is not None:
                if not isinstance(_p, (list, tuple)):
                    raise ValueError(f"attach_plan 必须是位置码列表：{_p!r}")
                for pos in _p:
                    if not isinstance(pos, int) or isinstance(pos, bool):
                        raise ValueError(f"attach_plan 含非整数位置码：{pos!r}")
                    _fp = _field_at(p, pos)
                    if _fp is None:
                        raise ValueError(f"attach_plan 位置码无效：{pos}")
                    _plan.append(_fp)

        picked: list = []
        seen: set = set()
        alts = sel.split("@")[0].split("+")
        each = "@each" in sel
        if each:
            # 每种各 1 张（「a Pokémon and a Trainer card」）。**不能**退化成
            # 「任意 2 张」：那样可能拿 2 只宝可梦 = 静默拿错牌。
            for alt in alts:
                for idx, c in enumerate(top):
                    if idx not in seen and _recon_one(c, alt):
                        seen.add(idx)
                        picked.append(idx)
                        break
        else:
            for idx, c in enumerate(top):
                if len(picked) >= k:
                    break
                if idx in seen or not _recon_matches(c, sel):
                    continue
                if dest == "bench" and not isinstance(c, PokeRef):
                    continue
                if dest == "attach" and not isinstance(c, EnergyRef):
                    continue
                seen.add(idx)
                picked.append(idx)

        # ⚠ `applied` 只记**真正执行成功**的牌：铺场时后备已满、或后续目标不可用，
        # 该牌必须回到牌库（进 `rest`），否则它会**从牌库消失**（既不在手、不在场、
        # 也不在弃牌堆）—— 那是静默丢牌，比不执行更糟。
        applied: set = set()
        got = 0
        for idx in picked:
            c = top[idx]
            if dest == "hand":
                p.hand.append(c)
                applied.add(idx)
                got += 1
            elif dest == "bench":
                if not self._bench_has_room(p):
                    continue
                fnew = make_pokemon(c.pdef)
                fnew.just_played = True
                p.bench.append(fnew)
                self._on_bench_placed(self.current, fnew)
                applied.add(idx)
                got += 1
            elif dest == "discard":
                # 「You may discard that card」（Morpeko / Rockruff / Litwick）：
                # 取到的那张直接进弃牌堆 —— 与余牌去向 `rest:discard`
                # （Explorer's Guidance「discard the other cards」）**不是一回事**：
                # 这里弃的是**被选中的**那张，不是剩下的。
                self._to_discard(p, c)
                applied.add(idx)
                got += 1
            else:                                   # attach（仅基本能量）
                if dest == "attach_spread":
                    if _plan:
                        if got >= len(_plan):
                            continue                 # 计划用尽 → 余牌回 rest
                        _dst = _plan[got]
                    else:
                        _dst = _targets[got % len(_targets)]
                    _dst.add_energy(str(c.edef.type).lower(), 1)
                else:
                    fp.add_energy(str(c.edef.type).lower(), 1)
                applied.add(idx)
                got += 1

        rest = [c for idx, c in enumerate(top) if idx not in applied]
        if spec["rest"] == "bottom":
            for c in reversed(rest):
                p.deck.insert(0, c)
        elif spec["rest"] == "shuffle_bottom":
            # 「Shuffle the other cards and put them on the bottom of your deck」
            # （Metang TEF 114）：**只洗余牌**再整叠放底 —— 用 `shuffle` 会把
            # 余牌混进整副牌库（卡面不符），用 `bottom` 又完全不洗。
            self.rng.shuffle(rest)
            for c in reversed(rest):
                p.deck.insert(0, c)
        elif spec["rest"] == "discard":
            # 「Discard the other cards」（Explorer's Guidance）—— 必须真进弃牌堆，
            # 否则「弃置」被静默改成「洗回」，后续「弃牌堆回收」类效果会凭空多出材料。
            for c in rest:
                self._to_discard(p, c)
        elif spec["rest"] == "shuffle":
            if rest:
                # 卡面只要求「shuffle **the other** cards」：没有余牌就不该动牌库。
                # 早期实现无条件 `shuffle(p.deck)`，会让「看顶 1 张、弃掉它」这类
                # 卡顺手把整副牌重洗一遍（卡面根本没有洗牌句）。
                p.deck.extend(rest)
                self.rng.shuffle(p.deck)
        elif spec["rest"] == "none":
            # 「Look at the top card. You may discard that card.」—— 卡面**没有**
            # 洗牌句，没被取走的牌必须**原序回到牌库顶**（Morpeko / Rockruff /
            # Litwick 三张）。
            for c in reversed(rest):
                p.deck.append(c)
        else:
            raise ValueError(f"未知 recon 余牌去向：{spec['rest']}")
        return f"top{n} {dest} {got}/{sel}"

    def _check_play_locks(self, p: PlayerState, c) -> None:
        """手牌禁打（跨回合）—— `_trainer_legals` 与 `_do_trainer` 共用

        ⚠ 两处都要查：只在 legals 查，非法动作仍可能被外部直接构造出来；
        只在 `_do_trainer` 查，AI 会把「必然失败」的动作当成合法动作反复尝试。
        """
        _tc = self.turn_counts[self.current]
        kind = str(getattr(getattr(c, "tdef", None), "kind", "") or "")
        if kind == "supporter" and getattr(p, "supporter_lock_until", 0) > _tc:
            raise ValueError("本回合无法打出支援者（手牌禁打）")
        if kind == "stadium" and getattr(p, "stadium_lock_until", 0) > _tc:
            raise ValueError("本回合无法打出场地（手牌禁打）")
        if _is_acespec(c) and getattr(p, "acespec_lock_until", 0) > _tc:
            raise ValueError("本回合无法打出 ACE SPEC（手牌禁打）")

    def _dispatch_trainer_tag(self, p: PlayerState, opp: PlayerState, c, tag: str,
                             kind: str, action: dict, hi: int) -> str:
        """单个训练家 tag 的派发（`_do_trainer` 负责代价、弃置与复合 tag 拆分）

        抽成独立方法是为了让**复合 tag**（`a|b`）能按卡面顺序逐段执行：
        代价只付一次、卡也只弃一次，但两段效果都要落地。
        """
        if tag == "poffin":
            placed = self._trainer_poffin(p)
        elif tag == "ultra_ball":
            placed = self._trainer_ultra_ball(p, hi)
        elif tag == "hammer":
            placed = self._trainer_hammer(opp, kind)
        elif tag == "boss":
            placed = self._trainer_boss(opp, int(action.get("target", 0)),
                                        kind)
        elif tag.startswith("research:"):
            # 弃全部手牌（不含自身）→ 抽 n
            n = int(tag.split(":")[1])
            others = [c for i, c in enumerate(p.hand) if i != hi]
            # ⚠ 循环变量**不能**叫 `c`：函数末尾靠 `c` 的**身份**移除「已使用的训练家卡」
            # （`p.hand = [x for x in p.hand if x is not c]`）。这里一旦复用 `c`，
            # 末尾就会拿「最后一张被弃的牌」当成本卡 → 支援者留在手牌里，
            # 还会被重复弃一次（真实卡池的 Professor's Research 会踩到）。
            for _dropped in others:
                self._to_discard(p, _dropped)
            p.hand = [x for i, x in enumerate(p.hand) if i == hi]
            self._safe_draw(self.current, n, source="trainer")
            placed = f"discard {len(others)} draw {n}"
        elif tag.startswith("shuffle_draw_split:"):
            # 「双方洗回 + 我方 N / 对手 M」（第五十九批，Unfair Stamp）。
            # 必须排在 `shuffle_draw:` 之前 —— 虽然 `shuffle_draw_split:` 第 15 位
            # 是 `_`、`startswith("shuffle_draw:")` 不会命中，但按「更具体者先」排。
            placed = self._trainer_shuffle_draw_split(p, tag, hi)
        elif tag.startswith("shuffle_draw:"):
            # 手牌洗回牌库（不含自身）→ 抽 n
            n = int(tag.split(":")[1])
            others = [c for i, c in enumerate(p.hand) if i != hi]
            p.deck.extend(others)
            self.rng.shuffle(p.deck)
            p.hand = [c for i, c in enumerate(p.hand) if i == hi]
            self._safe_draw(self.current, n, source="trainer")
            placed = f"shuffle {len(others)} draw {n}"
        elif tag == "switch_self":
            placed = self._trainer_switch(p, int(action.get("target", 0)))
        elif tag.startswith("recover:"):
            placed = self._trainer_recover(p, tag, int(action.get("target", 0)))
        elif tag.startswith("discard_search_energy:"):
            placed = self._trainer_discard_search_energy(p, tag, hi)
        elif tag.startswith("search_energy:"):
            placed = self._trainer_search_energy(p, tag)
        elif tag.startswith("choose:"):
            # 决策分解（S1a）：「you may discard any number of cards from your
            # hand」类。挂起选择，选完由 `_close_choice` 落盘并跑 `then`。
            placed = self._trainer_choose(p, tag, hi)
        elif tag.startswith("prize:flip_opp"):
            # S3：翻面对手奖赏卡 + 随机看手牌 + 可选交换（Bother-Bot）
            placed = self._trainer_prize_flip(p, opp)
        elif tag.startswith("prize:redeal"):
            # S3：奖赏卡洗回牌库底后重发（Redeemable Ticket）
            placed = self._trainer_prize_redeal(p)
        elif tag.startswith("prize:"):
            raise ValueError(f"未知 prize 操作：{tag}")
        elif tag.startswith("flute:"):
            # S3：亮出对手牌库顶 N 张，任意数量基础宝可梦上对手后备
            # （Accompanying Flute）
            placed = self._trainer_flute(p, opp, tag)
        elif tag.startswith("recon_bottom:"):
            # 「Look at the bottom N cards of your deck. You may reveal a
            #  Pokémon you find there and put it into your hand. Shuffle the
            #  other cards back into your deck.」（Dusk Ball）
            # ⚠ 与 `recon:`（**牌库顶**）方向相反。前缀 `recon_bottom:` **不**被
            #   `startswith("recon:")` 命中（第 6 位是 `_` 不是 `:`），必须独立分支。
            placed = self._trainer_recon_bottom(p, tag)
        elif tag.startswith("discard_tool_senergy:"):
            # 「Discard a Pokémon Tool and a Special Energy from 1 of your
            #  opponent's Pokémon.」（Ruffian）
            # ⚠ 两个条件必须**落在同一只**身上（卡面「from 1 of …」）；拆成两次
            #   递归挑选会变成「可以分跨两只」—— 静默加强。
            placed = self._trainer_discard_tool_senergy(opp, kind)
        elif tag.startswith("recon:"):
            # 检视牌库顶（2026-10-30 第二十批）：Pokégear 3.0 / Bug Catching Set /
            # Drayton / Hassel / Grimsley's Move / Waitress
            placed = self._do_recon(p, tag, action)
        elif tag.startswith("opp_hand:"):
            # 对手手牌操作（2026-10-30 第三十二批）：Eri / Energy Swatter
            placed = self._do_opp_hand(p, tag, action)
        elif tag == "candy":
            placed = self._trainer_candy(p, action)
        elif tag.startswith(("search_pokemon:", "search_trainer:",
                             "search_any:")):
            placed = self._trainer_search_kind(p, tag)
        elif tag.startswith("search_bench:"):
            placed = self._search_bench(p, tag)
        elif tag.startswith("search_top:"):
            # 第七十九批：检索后放回牌库顶（Ciphermaniac's Codebreaking）
            placed = self._search_top(p, tag)
        elif tag.startswith("search_multi:"):
            # 第七十九批：逐类别各取 1 张（Secret Box）
            placed = self._search_multi(p, tag)
        elif tag.startswith("search_discard:"):
            # 第八十三批：检索至多 N 张直接弃置（Brilliant Blender）。
            # 派发须排在 `search_*` 通用族之后但早于 `tool:` 等无关分支。
            _sraw = tag.split(":", 1)[1] if ":" in tag else "1"
            placed = f"search discard {_search_discard(self, p, _sraw)}"
        elif tag.startswith("tool:"):
            placed = self._trainer_attach_tool(p, c, tag,
                                               int(action.get("target", 0)))
        elif tag.startswith("ko_prize_minus:"):
            # Lillie's Pearl：kind=tool 但 tag 走被动奖赏修正前缀（见 legals 注释）。
            # 不接这一支，这张卡打出去就是「未知训练家 tag」。
            placed = self._trainer_attach_tool(p, c, tag,
                                               int(action.get("target", 0)))
        elif tag.startswith("on_event:") and kind == "tool":
            # S2c：**订阅式道具**（Amulet of Hope / Heavy Baton / Powerglass）——
            # 与普通道具一样「附着到 1 只己方宝可梦」，效果由 `events.emit` 触发。
            placed = self._trainer_attach_tool(p, c, tag,
                                               int(action.get("target", 0)))
        elif tag.startswith("stadium:"):
            placed = self._trainer_play_stadium(p, c, tag)
        elif tag.startswith("fossil:"):
            placed = self._trainer_play_fossil(p, c, tag)
        # ---- 2026-10-27：训练家/整卡级「非检索类」效果 ----
        # 这些能力引擎本来就有（招式侧早已实现），只是训练家派发链没接。
        # 顺序注意：`draw_to:` 与 `draw:`、`discard:` 与 `discard_search_energy:`
        # 前缀互不包含（第 5/8 位是 `_` 而非 `:`），但显式按「更具体者先」排列。
        elif tag.startswith("draw_both:"):
            # 「Each player shuffles their hand into their deck. Then, you draw 5
            # cards, and your opponent draws 2 cards.」（Unfair Stamp）的抽牌段
            n = int(tag.split(":")[1])
            for _pi in (self.current, 1 - self.current):
                self._safe_draw(_pi, n, source="trainer")
                if self.finished:
                    break
            placed = f"both draw {n}"
        elif tag.startswith("opp_shuffle_draw:"):
            placed = self._opp_shuffle_draw(tag)
        elif tag.startswith("draw_cond:"):
            # 条件抽牌（2026-11-04 第五十八批）。必须排在 `draw:` 之前 ——
            # 虽然 `draw_cond:` 第 5 位是 `_`，`startswith("draw:")` 不会命中，
            # 但排列上仍按「更具体者先」，避免以后加 `draw_*` 变体时被吞。
            placed = self._trainer_draw_cond(p, tag, hi)
        elif tag.startswith("draw_to_branch:"):
            # 「Draw cards until you have N … If <cond>, draw cards until you have
            #  M **instead**.」（第九十八批，Team Rocket's Ariana）
            # 两条 draw_to 语义上可叠加（只补到目标数、不弃牌），所以
            # 「先到 base，条件成立再到 alt」与卡面等价，且不需要新状态。
            placed = self._trainer_draw_to_branch(p, tag, hi)
        elif tag.startswith("draw_to:"):
            placed = self._trainer_draw_to(p, tag, hi)
        elif tag.startswith("draw:"):
            placed = self._trainer_draw(p, tag)
        elif tag.startswith("heal:"):
            placed = self._trainer_heal(p, tag, action)
        elif tag == "gust_confuse":
            # 拉对手后备 + 新战斗位混乱（第五十一批，Lisia's Appeal）。
            # 必须排在 `startswith("gust")` 之前 —— 否则会被它吞掉，混乱静默丢失。
            placed = self._trainer_boss(opp, int(action.get("target", 0)),
                                        kind)
            # S4：被拉上来的那只若免疫「支援者卡效果」，混乱也不施加
            # （整张卡的效果都不作用于它）。`_trainer_boss` 被挡时不会换位，
            # 因此这里判的还是**原来那只**——它没被换下，也不该被混乱。
            if opp.active is not None \
                    and not self._trainer_poke_blocked(opp.active, kind):
                opp.active.status.add("confuse")
                placed += " + confuse"
        elif tag.startswith("status_multi:"):
            placed = self._trainer_status_multi(opp, tag, kind)
        elif tag.startswith("switch_draw_to:"):
            placed = self._trainer_switch(p, int(action.get("target", 0)))
            _n = int(tag.split(":")[1])
            # 训练家卡此刻**还在手上**，抽到 N 的口径要减掉它自己（同 draw_to）
            _need = max(0, _n - (len(p.hand) - 1))
            _got = (self._safe_draw(self.current, _need, source="trainer")
                    if _need else 0)
            placed += f" + draw {_got}"
        elif tag.startswith("gust"):
            placed = self._trainer_boss(opp, int(action.get("target", 0)),
                                        kind)
        elif tag.startswith("attach_spread:"):
            # 多张自由分配附着（2026-10-30 第三十三批）：排在 `attach` 之前
            placed = self._do_attach_spread(p, tag, action)
        elif tag.startswith("attach_spread_flip:"):
            # 抛币计数版（2026-11-02 第三十八批）
            placed = self._do_attach_spread_flip(p, tag, action)
        elif tag.startswith("attach_each_bench:"):
            # 每只后备各 per 张（2026-11-02 第三十八批）
            placed = self._do_attach_each_bench(p, tag, action)
        elif tag.startswith("attach_spread_multi:"):
            # 双色 or 1 of each（第三十九批）
            placed = self._do_attach_spread_multi(p, tag, action)
        elif tag.startswith("attach_each_pick:"):
            # 选至多 N 只、每只各 per 张（第三十九批，Janine's Secret Art）
            placed = self._do_attach_each_pick(p, tag, action)
        elif tag.startswith("attach_flip_until:"):
            # 抛到反面为止（第三十九批）
            placed = self._do_attach_flip_until(p, tag, action)
        elif tag.startswith("coin_gate_attach:"):
            # 训练家抛币门（第三十九批，Energy Coin）
            placed = self._trainer_coin_gate_attach(p, tag, action)
        elif tag.startswith("coin_flip:"):
            # 同一枚币的**双分支**（2026-11-03 第四十四批，Venture Bomb）：
            # `coin_flip:heads:<a>|tails:<b>` —— 训练家侧自己抛一次并派发。
            placed = self._trainer_coin_flip(p, tag, action)
        elif tag.startswith("shuffle_both_coin_draw:"):
            # 双方洗回手牌 + 单次抛币分抽（第四十五批，Harlequin）
            placed = self._trainer_shuffle_both_coin_draw(p, tag, c, action)
        elif tag.startswith("shuffle_both_bottom_coin_draw:"):
            # S5（2026-11-08）：双方手牌放**牌库底**（不洗牌）+ 每人**各**抛一枚币
            # 分抽（Lucian）。与上一条差两处：① 放底、**不洗牌**；② 币是每人
            # 各一枚（卡面「each player flips a coin … **that player** draws」）
            # —— 拿单币版顶替会让双方抽牌数完全相关，概率分布不同。
            placed = self._trainer_shuffle_both_bottom_coin_draw(p, tag, c)
        elif tag.startswith("use_any:"):
            # S6a：**合法性例外**标记，不是效果 —— 复合 tag 逐段派发时会走到它，
            # 必须显式 no-op（否则抛「未知训练家 tag」：卡打得出但 step 报错）。
            placed = ""
        elif tag.startswith("coin_search:"):
            # S5（2026-11-08）：抛币分岔检索（Team Rocket's Great Ball）——
            # 正面找进化、反面找基础，类别由 `category:` 限定。
            placed = self._trainer_coin_search(p, tag)
        elif tag.startswith("team_boost:"):
            # 全队本回合增伤（第六十批，Premium Power Pro / Black Belt's Training）
            placed = self._trainer_team_boost(p, tag)
        elif tag.startswith("team_shield:"):
            # 全队限时减伤（第四十六批，Iron Defender）
            placed = self._trainer_team_shield(p, tag)
        elif tag.startswith("shield_choose:"):
            # 单体限时护盾 + 打出前置条件（第四十六批，Acerola's Mischief）
            placed = self._trainer_shield_choose(p, tag, action)
        elif tag.startswith("attach"):
            placed = self._trainer_attach_energy(p, tag, action)
        elif tag.startswith("discard_opp_random:"):
            placed = f"opp discard {self._discard_opp_random(int(tag.split(':')[1]))}"
        elif tag.startswith("shuffle_opp_hand:"):
            placed = f"opp shuffle {self._shuffle_opp_hand(int(tag.split(':')[1]))}"
        elif tag.startswith("discard_opp_to:"):
            placed = f"opp discard to {self._discard_opp_to(int(tag.split(':')[1]))}"
        elif tag.startswith("discard_to_both:"):
            # 双方各弃到剩 N（第五十四批，Hand Trimmer）
            _a, _b = self._discard_to_both(int(tag.split(":")[1]))
            placed = f"discard to both: opp {_a} / me {_b}"
        elif tag.startswith("energy_to_deck:"):
            # 弃牌堆基本能量洗回牌库（第七十批，Energy Recycler）——复用招式侧
            # Kyogre「Riptide」的同一 helper；tag 形如 `energy_to_deck:any:5`。
            _ed = tag.split(":")
            _color = _ed[1] if len(_ed) > 1 else "any"
            _raw = _ed[2] if len(_ed) > 2 else "all"
            placed = f"energy to deck {_energy_to_deck(self, p, _color, _raw)}"
        elif tag.startswith("pokemon_to_deck:"):
            # 第八十二批：Sacred Ash，弃牌堆宝可梦洗回牌库。
            _pd = tag.split(":")
            _praw = _pd[1] if len(_pd) > 1 else "all"
            placed = f"pokemon to deck {_pokemon_to_deck(self, p, _praw)}"
        elif tag.startswith("energy_move:"):
            placed = self._apply_energy_move(tag, None, action,
                                             source="trainer", kinds=kind)
        elif tag.startswith("bounce:"):
            placed = self._apply_bounce(tag, None, action,
                                        source="trainer", kinds=kind)
        elif tag.startswith("discard_senergy:"):
            # 「Discard a Special Energy from 1 of your opponent's Pokémon.」
            # （第九十八批，Enhanced Hammer）—— `senergy` 是**卡**列表，
            # 引擎本来就能精确区分特殊/基本；此前训练家侧只有「道具**或**
            # 特殊能量」二选一的复合形态，单点弃特殊能量没出口。
            # 目标选择：战斗位优先，战斗位没有特殊能量再按后备顺序找。
            _n = int(tag.split(":")[1])
            _opp = self.players[1 - self.current]
            _cands = ([_opp.active] if _opp.active is not None else []) \
                + list(_opp.bench)
            _done = 0
            _immune_all = True
            _has_target = False
            for _fp in _cands:
                if _fp is None or _fp.is_ko:
                    continue
                # 只有**身上有特殊能量**的才算候选 —— 否则「对手场上根本没有
                # 特殊能量」会被误判成「全被免疫」，把该报的错吞掉。
                if not _fp.senergy:
                    continue
                _has_target = True
                # S4：免疫「道具/支援者卡效果」的宝可梦不受影响
                if self._trainer_poke_blocked(_fp, kind):
                    continue
                _immune_all = False
                _done = _discard_special_energy(self, _opp, _fp, _n)
                if _done:
                    self._log("discard_energy", {"player": self.current,
                                                "target": _fp.name,
                                                "kind": "special"})
                    break
            if not _done and _has_target and _immune_all:
                # 全部候选都免疫 → 卡**打得出**、效果不结算（不是错误）
                self._log("blocked", {"by": "effect_immune", "effect": "discard",
                                      "scope": "all"})
                placed = "blocked: effect_immune"
            elif not _done:
                raise ValueError("对手场上没有特殊能量可弃")
            else:
                placed = f"discard special {_done}"
        elif tag.startswith("discard_energy:") and ":special" in tag:
            # 「Discard a Special Energy from 1 of your opponent's Pokémon.」
            # （第九十八批，Enhanced Hammer）——训练家侧此前没有 `discard_energy:`
            # 派发分支（只有招式侧有），卡进池也会在打出时报「未知训练家 tag」。
            # 目标：战斗位优先，其次后备（`:any`）；只写 `:special` 的组合视为
            # 同样语义（单点弃特殊能量，不存在「只打战斗位」的卡面）。
            _dp2 = tag.split(":")
            _n2 = 999 if _dp2[1] == "all" else int(_dp2[1])
            _opp2 = self.players[1 - self.current]
            _cands2 = ([_opp2.active] if _opp2.active is not None else []) \
                + list(_opp2.bench)
            _done2 = 0
            _immune_all2 = True
            _has_target2 = False
            for _fp2 in _cands2:
                if _fp2 is None or _fp2.is_ko:
                    continue
                if not _fp2.senergy:
                    continue
                _has_target2 = True
                if self._trainer_poke_blocked(_fp2, kind):
                    continue
                _immune_all2 = False
                _done2 = _discard_special_energy(self, _opp2, _fp2, _n2)
                if _done2:
                    self._log("discard_energy", {"player": self.current,
                                                "target": _fp2.name,
                                                "kind": "special"})
                    break
            if not _done2 and _has_target2 and _immune_all2:
                self._log("blocked", {"by": "effect_immune", "effect": "discard",
                                      "scope": "all"})
                placed = "blocked: effect_immune"
            elif not _done2:
                raise ValueError("对手场上没有特殊能量可弃")
            else:
                placed = f"discard special {_done2}"
        elif tag.startswith("discard_tool_or_senergy:"):
            # 「弃对手道具/特殊能量 或 弃竞技场」二选一（2026-11-02 第三十八批）：
            # 必须排在 `discard_tool:` 之前 —— 前缀相近，将来若有一方放宽成
            # `startswith("discard_tool")` 就会互相吞掉（同类事故已发生过一次）。
            placed = self._trainer_discard_tool_or_senergy(p, tag, action,
                                                          kind)
        elif tag.startswith("discard_tool:"):
            placed = (f"discard tool "
                      f"{self._apply_discard_tool(tag, 'trainer', kind)}")
        elif tag == "discard_stadium":
            placed = f"discard stadium {self._apply_discard_stadium()}"
        elif tag.startswith("discard_search_multi:"):
            # 第九十二批（2026-11-05）：先弃**全部**手牌，再逐类别各取 1 张
            # （Larry's Skill PRE 115）。两条独立动作由解析侧合成一条复合 tag
            # —— 训练家 `TrainerDef.tag` 单值、派发链只执行一条，拆两条会被
            # `build_trainer` 整卡拒；只留第一条则会静默丢掉整个检索。
            _d = self._trainer_discard_all(p, "discard:all", hi)
            _s = self._search_multi(p, "search_multi:" + tag.split(":", 1)[1])
            placed = f"{_d}; {_s}"
        elif tag.startswith("discard:"):
            placed = self._trainer_discard_all(p, tag, hi)
        elif tag.startswith("mill:"):
            placed = self._trainer_mill(tag)
        elif tag.startswith("shuffle_top:"):
            # 第九十一批（2026-11-05）：检视牌库顶后重排（Deduction Kit）。
            # 此前只在 `_apply_attack_effects` 派发，训练家侧缺失 → 卡在池外。
            placed = self._trainer_shuffle_top(tag)
        elif tag.startswith("evolve_from_deck"):
            # 第九十二批（2026-11-05）：从牌库检索并当场进化（Salvatore）。
            # 此前三处全缺：只在攻击白名单、只在攻击侧派发、无训练家分支。
            placed = self._trainer_evolve_from_deck(p, tag, action)
        elif tag.startswith("prize_bonus:"):
            # 第八十九批（2026-11-05）：本回合多拿奖赏（Briar「白蕾」）。
            # 设为**本回合**旗标（`prize_bonus_until == turn_number`），在
            # `_resolve_ko` 结算奖赏时按条件消费；turn_number 每回合 +1 → 过期
            # 自动失效（无需显式清）。
            _pb = tag.split(":")
            p.prize_bonus_n = (int(_pb[1]) if len(_pb) > 1 and _pb[1].isdigit()
                               else 0)
            p.prize_bonus_tera = bool(len(_pb) > 2 and _pb[2] == "tera")
            p.prize_bonus_until = self.turn_number
            placed = f"prize bonus {p.prize_bonus_n} this turn"
        elif tag.startswith("attach:"):
            # 训练家附能（第一百零九批，Rosa's Encouragement）：
            # `attach:<n>:<type>:<zone>[:target:<scope>]`。
            # 引擎此前只有特性侧（`attach_energy:`）与招式侧，训练家侧**没有**
            # 这一支 —— 卡能进池，打出时报「未知训练家 tag」。
            _an, _aet, _azn = _parse_attach_tag(tag)
            _atfp = _attach_target_fp(tag, p, action, None)
            if _atfp is None:
                placed = "attach 0 (no target)"
            else:
                _got = _attach_energy_from_zones(self, p, _atfp, _an, _aet,
                                                 _azn or "discard")
                placed = f"attach {_got} -> {_atfp.name}"
                self._log("attach", {"player": self.current,
                                     "target": _atfp.name, "n": _got})
        elif tag.startswith("fx:"):
            placed = families.apply_action(
                self, tag, {"kind": "trainer", "card": c, "action": action,
                            "player": self.current, "hand_index": hi})
        elif tag.startswith("queue:"):
            # S2：**延迟效果**入队（Amarys「At the end of this turn, …」）
            placed = self._trainer_queue(p, tag)
        else:
            raise ValueError(f"未知训练家 tag={tag}")
        return placed

    def _do_trainer(self, p: PlayerState, action: dict) -> dict:
        hi = _hand_index_from_action(p.hand, action)
        if hi is None:
            known = "、".join(
                c.tdef.display for c in p.hand if isinstance(c, TrainerRef))
            raise ValueError(
                f"无法识别训练家卡「{_name_hint(action)}」"
                f"（手牌可用：{known or '无'}）")
        c = p.hand[hi]
        if not isinstance(c, TrainerRef):
            raise ValueError("该牌不是训练家")
        tag, kind = c.tdef.tag, c.tdef.kind
        if kind in ("item", "tool") \
                and p.item_lock_until > self.turn_counts[self.current]:
            raise ValueError("本回合无法打出手牌中的物品卡（物品封锁）")
        # 第一百零七批：支援者 / 场地 / ACE SPEC 三类跨回合手牌禁打
        self._check_play_locks(p, c)
        # 静态禁令（「你的对手不能从手牌打出物品卡」等，效果族提供）
        if families.blocked_play(self, p, c):
            raise ValueError("场上效果禁止打出该卡")
        if kind == "supporter":
            if p.supporter_used:
                raise ValueError("本回合已使用支援者")
            # S6a（官方 2020-02 起）：**先手第一回合不能打 Supporter**
            # （卡面自带「may use this card during your first turn」的除外）
            if self.cfg.first_turn_no_supporter and self._opening_turn() \
                    and not _card_first_turn_ok(c):
                raise ValueError("先手第一回合不能使用支援者")
        opp = self.players[1 - self.current]
        # 打出代价（2026-11-02 第三十六批）：**先付账、后效果**。
        #
        # 顺序必须与卡面一致：先做效果再弃牌，会让「手牌其实不够付账」的情形
        # 也把效果先拿到手（`_pay_play_cost` 随后才失败，但效果已经发生）。
        # 付账会改动手牌 → 之后各 helper 用到的 `hi` 必须**按身份重算**，
        # 不能沿用旧索引（`p.hand.pop` 会让它指向别的牌）。
        cost_log = ""
        if tag not in _BUILTIN_COST_TAGS:
            cost_log = self._pay_play_cost(p, c, hi, action)
            if cost_log:
                hi = next((i for i, x in enumerate(p.hand) if x is c), None)
                if hi is None:      # 代价搬家把本卡弄丢了 → 绝不继续派发
                    raise ValueError("打出代价后找不到本卡（内部状态异常）")
        # S2（2026-11-08）：**复合 tag**（一句两动作）按卡面顺序逐段执行。
        # 训练家卡只认一个卡级 tag（纪律 #11），因此 `a|b` 必须在这里拆开跑，
        # 而不是让某一支把另一支吃掉。`coin:` / `coin_flip:` 里的 `|` 是**分支**
        # 分隔符（同一枚币的两面），绝不能拆。
        _subs = ([x for x in tag.split("|") if x]
                 if "|" in tag and not tag.startswith(("coin:", "coin_flip:"))
                 else [tag])
        placed = ""
        for _t in _subs:
            _d = self._dispatch_trainer_tag(p, opp, c, _t, kind, action, hi)
            # 子效果可能改动牌序 → 按**身份**重算本卡位置（与代价支付同口径）
            hi = next((i for i, x in enumerate(p.hand) if x is c), hi)
            if _d:
                placed = f"{placed} | {_d}" if placed else _d
        # 按身份移除已使用的训练家卡（helper 内部弃牌会造成 hand 索引漂移，
        # 用 pop(hi) 会误删——真实卡池的高频物品 Ultra Ball 会踩到）
        p.hand = [x for x in p.hand if x is not c]
        # S2 道具 / S3 场地 / 化石卡：**留在场上**，不进弃牌堆
        #（场地被顶替时才把旧场地送进弃牌堆，见 `_trainer_play_stadium`；
        #  化石卡作为宝可梦上场，弃置走 `discard_fossil`）
        if not tag.startswith(("tool:", "stadium:", "fossil:")):
            self._to_discard(p, c)
        if kind == "supporter":
            p.supporter_used = 1
            p.supporter_name = c.tdef.name
        self._log("trainer", {"player": self.current, "tag": tag,
                              "kind": kind, "detail": placed,
                              **({"cost": cost_log} if cost_log else {})})
        return {"ok": True, "event": self.events[-1]}

    # --- E4 训练家行为 ---
    def _pay_play_cost(self, p: PlayerState, c, self_hi: int,
                       action: dict) -> str:
        """付出「打出代价」（``TrainerDef.play_cost``），返回日志串

        卡面「You can use this card only if you discard N other cards from your
        hand.」—— 本卡自己**不能**当代价（`other`）。

        选哪几张：动作可带 ``discard_pick``（手牌索引，int 或 list）；
        缺省从**末尾**往前取 —— 与既有的 `_trainer_ultra_ball` /
        `_trainer_discard_search_energy` 口径一致（那两张卡同样「弃 N 张手牌
        换检索」）。选牌口径必须与既有实现一致，否则同类卡在同一个 AI 下会
        表现出不同强度。
        """
        spec = _parse_play_cost(getattr(c.tdef, "play_cost", ""))
        if spec is None:
            return ""
        op, n, etype = spec
        if op == "discard_energy":
            # 卡面限定「Basic {X} Energy card」→ 候选**只有**手牌里同属性的
            # 基本能量。用「任意 N 张手牌」的候选集会允许弃掉别的牌（代价被
            # 静默放宽），而 AI 也会挑到与卡面不符的那张。
            cands = [i for i in range(len(p.hand))
                     if isinstance(p.hand[i], EnergyRef)
                     and not getattr(p.hand[i].edef, "is_special", False)
                     and str(p.hand[i].edef.type) == etype]
        elif op == "discard":
            cands = [i for i in range(len(p.hand)) if i != self_hi]
        else:
            raise ValueError(f"未知打出代价操作：{op}")
        if len(cands) < n:
            _what = (f"基本 {etype} 能量" if op == "discard_energy" else "手牌")
            raise ValueError(f"打出代价付不出（需弃 {n} 张{_what}，"
                             f"可选 {len(cands)} 张）")
        raw = action.get("discard_pick")
        if raw is None:
            picks = cands[::-1][:n]              # 缺省：从末尾往前
        else:
            picks = [raw] if isinstance(raw, int) else list(raw)
            for i in picks:
                if not isinstance(i, int) or i not in cands:
                    # 指到本卡 / 越界 / 重复 → 一律抛错，绝不悄悄换成别的牌
                    raise ValueError(f"打出代价的 discard_pick 非法：{raw}")
            if len(set(picks)) != len(picks):
                raise ValueError(f"打出代价的 discard_pick 有重复：{raw}")
            if len(picks) != n:
                raise ValueError(f"打出代价需弃 {n} 张，discard_pick 给了 {len(picks)} 张")
        dropped = []
        for i in sorted(picks, reverse=True):
            dropped.append(_card_label(p.hand[i]))
            self._to_discard(p, p.hand.pop(i))
        return f"cost discard {len(dropped)} ({', '.join(dropped)})"

    def _pay_ability_cost(self, p: PlayerState, fp: FieldPokemon, pc: str,
                          action: dict) -> str:
        """支付特性发动代价（2026-11-04 第四十九批）—— 在效果**之前**调用

        缺省选择与其它「任意 N 张」同口径（从手牌末尾往前、具名卡按手牌顺序），
        可用 ``action['cost_cards']`` 显式指定下标（非法一律抛错，绝不悄悄换牌）。

        ⚠ 付不出时抛错而不是静默跳过：静默跳过 = 免费发动，是这类机制最危险的
        形态。合法动作表在 `_ability_legals` 里已先行过滤，正常路径不会走到这里。
        """
        spec = _parse_ability_cost(pc)
        if spec is None:
            return ""
        if not _ability_cost_ok(p, fp, pc):
            raise ValueError(f"特性代价付不出：{pc}")
        op, n, arg = spec
        dropped: list = []
        if op in ("discard", "hand_bottom"):
            cands = list(range(len(p.hand)))
            raw = action.get("cost_cards")
            if raw is None:
                picks = cands[::-1][:n]
            else:
                picks = [raw] if isinstance(raw, int) else list(raw)
                for i in picks:
                    if not isinstance(i, int) or i not in cands:
                        raise ValueError(f"特性代价的 cost_cards 非法：{raw}")
                if len(set(picks)) != len(picks) or len(picks) != n:
                    raise ValueError(f"特性代价需 {n} 张，cost_cards 给了 {len(picks)} 张")
            for i in sorted(picks, reverse=True):
                card = p.hand.pop(i)
                dropped.append(_card_label(card))
                if op == "discard":
                    self._to_discard(p, card)
                else:
                    p.deck.insert(0, card)      # 「bottom of your deck」= 列表头
            self._log("ability_cost", {"player": self.current, "op": op,
                                       "n": len(dropped), "cards": dropped})
            return f"cost {op} {len(dropped)}"
        if op == "discard_named":
            idxs = _named_hand_cards(p, str(arg))[:n]
            for i in sorted(idxs, reverse=True):
                dropped.append(_card_label(p.hand[i]))
                self._to_discard(p, p.hand.pop(i))
            self._log("ability_cost", {"player": self.current, "op": op,
                                       "n": len(dropped), "cards": dropped})
            return f"cost discard {len(dropped)}"
        if op == "discard_hand_energy":
            idxs = [i for i, c in enumerate(p.hand)
                    if isinstance(c, EnergyRef)
                    and not getattr(c.edef, "is_special", False)
                    and str(c.edef.type) == arg][:n]
            for i in sorted(idxs, reverse=True):
                dropped.append(_card_label(p.hand[i]))
                self._to_discard(p, p.hand.pop(i))
            self._log("ability_cost", {"player": self.current, "op": op,
                                       "n": len(dropped), "cards": dropped})
            return f"cost discard {len(dropped)} energy"
        if op == "discard_self_energy":
            for _ in range(n):
                fp.energy[str(arg)] -= 1
                if fp.energy[str(arg)] <= 0:
                    fp.energy.pop(str(arg), None)
                self._to_discard(p, EnergyRef(EnergyDef(str(arg))))
                dropped.append(str(arg))
            self._log("ability_cost", {"player": self.current, "op": op,
                                       "from": fp.name, "n": len(dropped)})
            return f"cost discard {n} {arg} energy from self"
        raise ValueError(f"未知特性代价形态：{pc}")

    def _trainer_draw(self, p: PlayerState, tag: str) -> str:
        """训练家/整卡级抽牌（``draw:N[:per:<counter>][:if:<cond>]``）"""
        n, cond = _draw_spec(self, tag)
        if cond is not None and not _eval_condition(self, cond):
            self._log("no_effect", {"tag": tag, "reason": "条件不满足"})
            return "no effect"
        got = self._safe_draw(self.current, n, source="trainer")
        return f"draw {got}"

    def _opp_shuffle_draw(self, tag: str) -> str:
        """对手把手牌洗回牌库后抽 N（``opp_shuffle_draw:N[:if_hand]``）

        · Gothitelle「have your opponent shuffle their hand into their deck and
          draw 3 cards」→ 恒抽（手牌为空时洗 0 张也抽 3）
        · Vivillon「… puts it on the bottom of their deck. If they put any cards
          on the bottom … they draw 4 cards」→ ``:if_hand``（手牌为空则不抽）
        """
        parts = tag.split(":")
        oi = 1 - self.current
        o = self.players[oi]
        if len(parts) > 1 and parts[1] == "same":
            # Meddling Memo：抽回**同样张数**（手牌内容被随机化，张数不变）
            n = len(o.hand)
        else:
            n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        require_hand = "if_hand" in parts[2:]
        if require_hand and not o.hand:
            self._log("no_effect", {"tag": tag, "reason": "对手手牌为空"})
            return "opp hand empty"
        o.deck.extend(o.hand)
        o.hand.clear()
        self.rng.shuffle(o.deck)
        got = self._safe_draw(oi, n, source="opp_shuffle_draw")
        return f"opp shuffle draw {got}"

    def _trainer_draw_to_branch(self, p: PlayerState, tag: str,
                                hi: int) -> str:
        """``draw_to_branch:<base>:<alt>:if:<cond>`` —— 分支替换的抽到 N 张

        卡面（Ariana）：「Draw cards until you have 5 cards in your hand. If all
        of your Pokémon in play are Team Rocket's Pokémon, draw cards until you
        have 8 cards in your hand **instead**.」
        语义校验：`draw_to` 只补到目标数、从不弃牌 → 先到 5 再到 8 与「直接到 8」
        终局等价；条件不成立时第二次调用被跳过。故无需新增回合状态。
        """
        _dp = tag.split(":")
        if len(_dp) < 4 or not _dp[1].isdigit() or not _dp[2].isdigit() \
                or _dp[3] != "if":
            raise ValueError(f"draw_to_branch tag 非法：{tag}")
        base, alt = int(_dp[1]), int(_dp[2])
        cond = ":".join(_dp[4:])
        if not cond:
            raise ValueError(f"draw_to_branch 缺条件：{tag}")
        first = self._trainer_draw_to(p, f"draw_to:{base}", hi)
        if _eval_condition(self, cond):
            second = self._trainer_draw_to(p, f"draw_to:{alt}", hi)
            return f"{first} + （条件成立）{second}"
        return first

    def _trainer_draw_to(self, p: PlayerState, tag: str, hi: int) -> str:
        """抽到指定手牌数（``draw_to:N``）——已达上限则不再抽

        ⚠ 本卡此时**仍在手牌里**（派发结束后才按身份移除并弃置），所以按
        「结算后的手牌数」计算时要先扣掉它；否则 ``draw_to:5`` 会只抽 4 张
        （实测：手牌 1 张（本卡）→ need=4 → 移除本卡后只剩 4）。
        """
        _dp = tag.split(":")
        n = int(_dp[1])
        cond = (":".join(_dp[3:]) if len(_dp) > 3 and _dp[2] == "if" else None)
        if cond is not None and not _eval_condition(self, cond):
            self._log("no_effect", {"tag": tag, "reason": "条件不满足"})
            return f"draw to {n} skipped"
        need = n - (len(p.hand) - 1)
        got = self._safe_draw(self.current, need, source="trainer") if need > 0 else 0
        return f"draw to {n} (drew {got})"

    def _trainer_draw_cond(self, p: PlayerState, tag: str, hi: int) -> str:
        """条件抽牌（``draw_cond:<keep|shuffle>:<base>:<plus|set>:<n>:if:<cond>``）

        两种**方向相反**的卡面形态（2026-11-04 第五十八批）：
          · 「Draw 2 cards. Then, if you have 10 or more cards in your hand,
            draw 2 **more** cards.」（Billy & O'Nare / Emcee's Hype）→ ``plus``
          · 「Shuffle your hand into your deck. Then, draw 4 cards. If your
            opponent has 3 or fewer Prize cards remaining, draw 8 cards
            **instead**.」（Lacey / Lillie's Determination）→ ``set``

        ⚠ 两者只差 ``more`` / ``instead`` 一个词，但落反方向就是静默改强度：
        ``plus`` 当 ``set`` = 少抽；``set`` 当 ``plus`` = 多抽。解析侧必须按词判定，
        这里只做「按 tag 执行」，不做任何猜测性兜底。

        洗回手牌时**不含自身** —— 本卡此刻仍在手上（派发末尾才弃置），
        与 `shuffle_draw:` / `shuffle_both_coin_draw:` 同一口径。
        """
        _dp = tag.split(":")
        if len(_dp) < 7 or _dp[0] != "draw_cond" or _dp[5] != "if":
            raise ValueError(f"draw_cond tag 非法：{tag}")
        mode, raw_base, act, raw_n = _dp[1], _dp[2], _dp[3], _dp[4]
        if mode not in ("keep", "shuffle"):
            raise ValueError(f"draw_cond 未知模式：{tag}")
        if act not in ("plus", "set"):
            raise ValueError(f"draw_cond 未知叠加方式：{tag}")
        if not raw_base.isdigit() or not raw_n.isdigit():
            raise ValueError(f"draw_cond 数值非法：{tag}")
        cond = ":".join(_dp[6:])
        base, n = int(raw_base), int(raw_n)
        if mode == "shuffle":
            others = [c for i, c in enumerate(p.hand) if i != hi]
            p.deck.extend(others)
            self.rng.shuffle(p.deck)
            p.hand = [c for i, c in enumerate(p.hand) if i == hi]
            self._log("hand_shuffle", {"player": self.current,
                                       "n": len(others), "side": "self"})
        hit = _eval_condition(self, cond)
        if not hit:
            want = base
        elif act == "plus":
            want = base + n
        else:
            want = n
        got = (self._safe_draw(self.current, want, source="trainer")
               if want > 0 else 0)
        self._log("draw_cond", {"tag": tag, "cond": cond, "hit": hit,
                                "want": want, "drew": got})
        return f"draw_cond {want} (cond={'Y' if hit else 'N'})"

    def _trainer_heal(self, p: PlayerState, tag: str, action: dict) -> str:
        """回复（``heal:N[:<scope>][:type:<c>]``），范围语义见 `_apply_heal`"""
        healed, n_tgt = _apply_heal(self, p, tag, action=action)
        return f"heal {healed} (x{n_tgt})"

    def _energy_to_hand(self, tag: str) -> str:
        """把前序回复目标的**全部能量**收回手牌（``energy_to_hand:all``）

        卡面原型（Wally's Compassion）：「If you healed any damage in this way,
        put all Energy attached to that Pokémon into your hand.」
        对象来自 `_apply_heal` 记录的 `_last_heal_targets`；没有前序回复时
        由 `:if_prev` 在派发层挡住（本方法不重复判）。
        """
        targets = list(getattr(self, "_last_heal_targets", ()) or ())
        p = self.players[self.current]
        from .cards import EnergyDef, EnergyRef
        moved = 0
        for fp in targets:
            if fp is None or fp.is_ko:
                continue
            for t, n in list(fp.energy.items()):
                for _ in range(n):
                    p.hand.append(EnergyRef(EnergyDef(t)))
                    moved += 1
            fp.energy.clear()
            for ref in list(fp.senergy):
                p.hand.append(ref)
                moved += 1
            fp.senergy.clear()
        return f"energy to hand {moved}"

    def _trainer_attach_energy(self, p: PlayerState, tag: str,
                               action: dict) -> str:
        """从指定区域附能（``attach:N[:<type>][:<zone>][:target:<pos>]``）

        `target` 段不参与 `_parse_attach_tag`（它把未知 token 当属性名），
        先剥掉再交给它解析，避免把位置码误读成能量属性。
        """
        parts, clean, i = tag.split(":"), [], 0
        while i < len(parts):
            if parts[i] == "target" and i + 1 < len(parts):
                i += 2
                continue
            clean.append(parts[i])
            i += 1
        n, etype, zone = _parse_attach_tag(":".join(clean))
        fp = _field_at(p, int(action.get("target", 0)))
        if fp is None:
            raise ValueError("附能目标无效")
        got = _attach_energy_from_zones(self, p, fp, n, etype, zone or "any")
        if not got:
            raise ValueError("指定区域无可附着的能量")
        return f"attach {got} {etype or 'any'} to {fp.name}"

    def _trainer_discard_all(self, p: PlayerState, tag: str, hi: int) -> str:
        """弃手牌（``discard:all`` 或 ``discard:N``，均不含本卡自身）

        ⚠ 循环变量**不能**叫 `c`：函数末尾靠 `c` 的身份移除已使用的训练家卡。
        """
        raw = tag.split(":")[1]
        others = [x for i, x in enumerate(p.hand) if i != hi]
        if raw == "all":
            dropped = others
            detail = f"discard {len(dropped)}"
        else:
            n = int(raw) if raw.isdigit() else 0
            if len(others) < n:
                raise ValueError(f"手牌不足（需弃 {n} 张）")
            dropped = others[:n]
            detail = f"discard {len(dropped)}"
        for _dropped in dropped:
            self._to_discard(p, _dropped)
        # 按**身份**保留本卡（同名卡用 == 会误删）
        p.hand = [x for i, x in enumerate(p.hand)
                  if i == hi or not any(x is d for d in dropped)]
        return detail

    def _apply_pre_attack_effect(self, spec: str) -> None:
        """伤害前阶段（``pre:``）—— 在伤害修正与伤害结算**之前**执行

        卡面原型（2026-10-29 第二十五批）：
          · 「Before doing damage, discard all Pokémon Tools from your opponent's
            Active Pokémon.」（N's Joltik / Arven's Skwovet / Larvesta / Spearow /
            Raticate）→ ``pre:discard_tool:all:opp_active``
          · 「Before doing damage, discard all Pokémon Tools **and Special Energy**
            from your opponent's Active Pokémon.」（Mow Rotom）→ 上面两条 + 本条的
            ``pre:discard_energy:all:opp:special``

        只支持这两个**精确**形态；其余一律抛错（宁可打不出，不可静默算错）。
        """
        parts = spec.split(":")
        head = parts[0]
        if head == "discard_tool":
            if len(parts) != 3 or parts[2] != "opp_active":
                raise ValueError(f"未知伤害前效果：pre:{spec}")
            # 复用普通弃道具实现（`discard_tool:<n>:<scope>`），并记账
            self._pre_tools_discarded += self._apply_discard_tool(spec)
            return
        if head == "discard_energy":
            if len(parts) < 4 or parts[2] != "opp" or parts[3] != "special":
                raise ValueError(f"未知伤害前效果：pre:{spec}")
            raw = parts[1]
            n = 999 if raw == "all" else int(raw)
            opp = self.players[1 - self.current]
            done = 0
            if opp.active is not None:
                # 效果免疫（Mist / Rock Fighting 等）照常拦截：弃能是**招式效果**
                if families.effect_immune(self, opp.active, "attack") \
                        or special_energy.effect_immune(opp.active, "attack"):
                    self._log("blocked", {"target": opp.active.name,
                                          "by": "effect_immune",
                                          "effect": "pre_discard_energy"})
                else:
                    done = _discard_special_energy(self, opp, opp.active, n)
            self._log("discard_energy", {
                "player": self.current,
                "target": opp.active.name if opp.active else None,
                "n": done, "side": "opp", "special": True, "phase": "pre"})
            return
        raise ValueError(f"未知伤害前效果：pre:{spec}")

    def _apply_discard_tool(self, tag: str, source: str = "attack",
                            kinds: str = "") -> int:
        """弃道具（``discard_tool:N[:<scope>]``）→ 实际弃置数（S6 弃道具族）

        scope：``opp_active``（对手战斗位，Arven's Skwovet / Larvesta / Spearow /
        Raticate 的「Before doing damage, discard all Pokémon Tools from your
        opponent's Active Pokémon」）· ``opp`` · ``own`` · ``both``（Tool Scrapper
        的「yours or your opponent's」）。
        """
        parts = tag.split(":")
        raw = parts[1] if len(parts) > 1 else "1"
        n = 999 if raw == "all" else (int(raw) if raw.isdigit() else 1)
        scope = parts[2] if len(parts) > 2 else "both"
        p = self.players[self.current]
        opp = self.players[1 - self.current]
        if scope == "opp_active":
            pairs = [(opp, opp.active)] if opp.active is not None else []
        elif scope == "opp":
            pairs = [(opp, fp) for fp in opp.all_field()]
        elif scope == "own":
            pairs = [(p, fp) for fp in p.all_field()]
        else:
            pairs = ([(p, fp) for fp in p.all_field()]
                     + [(opp, fp) for fp in opp.all_field()])
        done = 0
        for owner, fp in pairs:
            if done >= n:
                break
            if not fp.tool:
                continue
            # S4：**对手的**道具也受「道具/支援者卡效果免疫」保护（自己拆自己的不算）
            if owner is opp and self._effect_blocked(fp, source, self.current,
                                                    kinds):
                self._log("blocked", {"target": fp.name, "by": "effect_immune",
                                      "effect": "discard_tool",
                                      "source": source})
                continue
            name = fp.tool_name or fp.tool
            ref = fp.tool_ref
            _drop_tool(fp)
            if ref is not None:
                self._to_discard(owner, ref)
            else:
                # 手工装配 / 旧回放没有卡对象 → 至少保持文本层同步
                owner.discard.append(str(name or "Pokémon Tool"))
            self._log("discard_tool", {"player": self.current,
                                       "owner": owner.name, "target": fp.name,
                                       "tool": name})
            done += 1
        return done

    def _apply_discard_stadium(self) -> Optional[str]:
        """弃置场上场地（``discard_stadium``，Mow Rotom / Eternatus / Cetitan ex）
        → 被弃场地名（无场地返回 None）

        场地是双方共用的，简化记到当前玩家弃牌堆（与 `_trainer_play_stadium`
        顶替旧场地时的处理一致）；弃置后立刻重算上限修正。
        """
        st = self.stadium
        if not st:
            return None
        name = str(st.get("name") or "Stadium")
        self.stadium = None
        self._refresh_stadium_hp()
        self.players[self.current].discard.append(name)
        self._log("discard_stadium", {"player": self.current, "name": name})
        return name


    # ---------- S6 回手 / 洗回牌库（2026-10-28） ----------
    def _take_off_field(self, owner: int, fp: FieldPokemon):
        """把场上宝可梦移出战斗位/后备 → ``(PokeRef, [附加卡 refs])``

        附加卡（能量 / 道具）**随宝可梦一起返回**，由调用方决定入手还是洗回牌库
        （卡面「and all attached cards」）。战斗位空出时由后备**自动顶上**，
        顶上的宝可梦**不清状态**（它没有离场）。
        ⚠ 战斗位且无后备时**拒绝**（官方：不能把战斗位回手到无替补）；
        直接判负会改变胜负，属实质错误。
        """
        from .cards import EnergyDef, EnergyRef, PokeRef
        p = self.players[owner]
        if p.active is fp and not p.bench:
            raise ValueError("战斗位无替补，不能回手/洗回")
        extras = []
        for t, n in list(fp.energy.items()):
            for _ in range(n):
                extras.append(EnergyRef(EnergyDef(t)))
        if fp.tool:
            if fp.tool_ref is not None:
                extras.append(fp.tool_ref)
            else:
                p.discard.append(fp.tool_name or fp.tool)
            _drop_tool(fp)
        fp.energy.clear()
        if p.active is fp:
            p.active = p.bench.pop(0)
            p.active.moved_active_turn = self.turn_number
        else:
            p.bench = [x for x in p.bench if x is not fp]
        return PokeRef(fp.pdef), extras

    def _apply_bounce(self, tag: str, fp: Optional[FieldPokemon] = None,
                      action: Optional[dict] = None,
                      source: str = "attack", kinds: str = "") -> str:
        """回手 / 洗回牌库 / 弃牌堆铺后备（S6 回手族）

        tag：
          ``bounce:to_hand:self[:discard_attached]``  自身回手（附加卡随行/弃置）
          ``bounce:shuffle_self[:discard_attached]``  自身洗回牌库
          ``bounce:to_hand:bench``                    选 1 只后备回手
          ``bounce:to_hand:choose``                   选 1 只己方回手
          ``bounce:discard_to_bench:N[:name:<key>][:hp:<n>]`` 弃牌堆宝可梦铺后备
        """
        action = action or {}
        parts = tag.split(":")
        mode = parts[1] if len(parts) > 1 else ""
        if mode == "energy":
            # 能量收回手牌（2026-10-29 第二十三批）：
            # `bounce:energy:<self|opp_active>:<n|all>[:type:<c>]`
            return self._bounce_energy(tag, fp, source, kinds)
        if mode == "discard_to_bench":
            return self._bounce_from_discard(tag)
        scope = parts[2] if len(parts) > 2 else ""
        discard_attached = "discard_attached" in parts[1:]
        p = self.players[self.current]
        dest = "deck" if mode == "shuffle_self" else "hand"
        if mode == "shuffle_self" or scope == "self":
            target = fp if fp is not None else p.active
            if target is None:
                raise ValueError("无回手目标")
        elif scope == "bench":
            if not p.bench:
                # 官方：效果无合法目标时**只是不执行**（招式照常可用）
                self._log("no_effect", {"tag": tag, "reason": "己方无后备"})
                return "no target"
            want = int(action.get("target", 0))
            if not (1 <= want <= len(p.bench)):
                want = 1
            target = p.bench[want - 1]
        elif scope == "choose":
            target = _field_at(p, int(action.get("target", 0)))
            if target is None:
                raise ValueError("回手目标无效")
        else:
            raise ValueError(f"未知回手范围：{tag}")
        if target is p.active and not p.bench:
            # 战斗位回手后必须有人顶上；无后备时**不执行**（不得直接判负）
            self._log("no_effect", {"tag": tag, "reason": "战斗位无替补"})
            return "no bench"
        name = target.name
        self._bounce_owner(self.current, target, dest, discard_attached)
        self._log("bounce", {"player": self.current, "target": name,
                             "to": dest, "discard_attached": discard_attached})
        return f"bounce {name} -> {dest}"

    def _bounce_owner(self, owner: int, fp: FieldPokemon, dest: str,
                      discard_attached: bool = False) -> None:
        """把场上宝可梦（含附加卡）移到手牌或牌库；牌库形态会洗牌"""
        ref, extras = self._take_off_field(owner, fp)
        p = self.players[owner]
        if dest == "hand":
            p.hand.append(ref)
        else:
            p.deck.append(ref)
        for x in extras:
            if discard_attached:
                self._to_discard(p, x)
            elif dest == "hand":
                p.hand.append(x)
            else:
                p.deck.append(x)
        if dest == "deck":
            self.rng.shuffle(p.deck)

    def _bounce_from_discard(self, tag: str) -> str:
        """弃牌堆宝可梦铺到后备（``bounce:discard_to_bench:N[:name:<key>][:hp:<n>]``）"""
        p = self.players[self.current]
        picked = _bounce_discard_candidates(p, tag, self.cfg.max_bench)
        moved = 0
        for idx in sorted(picked, reverse=True):
            ref = self._rebuild_ref(p.discard_refs.pop(idx))
            label = _card_label(ref)
            for k in range(len(p.discard) - 1, -1, -1):
                if p.discard[k] == label:
                    p.discard.pop(k)
                    break
            p.bench.append(make_pokemon(ref.pdef))
            moved += 1
        return f"put {moved} onto bench"

    def _bounce_energy(self, tag: str, fp: Optional[FieldPokemon] = None,
                       source: str = "attack", kinds: str = "") -> str:
        """能量收回**持有者手牌**（``bounce:energy:<self|opp_active>:<n|all>[:type:<c>]``）

        卡面原型（2026-10-29 第二十三批）：「Put an Energy attached to this Pokémon
        into your hand.」（Shiftry / Chien-Pao / Dipplin / Landorus）、
        「Put 2 {R} Energy attached to this Pokémon into your hand.」（Volcanion）。

        选择口径（引擎无交互层 → **确定性默认**）：指定属性时只取该色；否则
        **无色优先**、其次按属性名排序；基本能量不足时才按整张卡取特殊能量。
        """
        parts = tag.split(":")
        scope = parts[2] if len(parts) > 2 else "self"
        raw_n = parts[3] if len(parts) > 3 else "1"
        etype = None
        for i, x in enumerate(parts):
            if x == "type" and i + 1 < len(parts):
                etype = parts[i + 1].lower()
        n = 999 if raw_n == "all" else (int(raw_n) if raw_n.isdigit() else 1)
        if scope == "self":
            owner = self.current
            target = fp or self.players[self.current].active
        elif scope == "opp_active":
            owner = 1 - self.current
            target = self.players[1 - self.current].active
        elif scope == "opp_any":
            # 「Put an Energy attached to **1 of your opponent's Pokémon** into
            # their hand.」（Chill Teaser Toy SFA 63，第五十九批）——目标是
            # 对手**任意 1 只**（含后备），不是固定的战斗位。
            # ⚠ 落成 `opp_active` = 只能拔战斗位；对手能量在后备时效果静默不生效。
            owner = 1 - self.current
            op = self.players[owner]
            pool = ([op.active] if op.active is not None else []) + list(op.bench)
            if not pool:
                self._log("no_effect", {"tag": tag, "reason": "对手无宝可梦"})
                return "no target"
            with_energy = [fp_ for fp_ in pool if fp_.total_energy() > 0]
            if not with_energy:
                self._log("no_effect", {"tag": tag, "reason": "对手无能量"})
                return "no target"
            # 确定性默认：战斗位优先（顺序与 `_spread_targets` 一致）
            target = with_energy[0]
        else:
            raise ValueError(f"未知能量回手范围：{tag}")
        if target is None:
            self._log("no_effect", {"tag": tag, "reason": "无目标"})
            return "no target"
        got = _energy_to_hand(self, owner, target, n, etype)
        self._log("bounce", {"player": owner, "target": target.name,
                             "to": "hand", "energy": got,
                             "type": etype or "any"})
        return f"energy {got} -> hand"

    def _apply_energy_move(self, tag: str, fp: Optional[FieldPokemon] = None,
                           action: Optional[dict] = None,
                           source: str = "attack", kinds: str = "") -> str:
        """能量移动（S6 能量移动族 · 2026-10-28）

        tag：``energy_move:N[:type:<c>]:from:<src>:to:<dst>``
        src ∈ ``active`` / ``bench`` / ``any`` / ``opp_active`` / ``opp_any``
        dst ∈ ``bench`` / ``active`` / ``any`` / ``opp_bench`` / ``opp_any``

        「至多 N」（N's Plan「Move up to 2」）→ 取 min(N, 源上可用能量)；
        指定属性时只移动该色（Frosmoth「a {W} Energy」），否则无色优先。
        """
        action = action or {}
        parts = tag.split(":")
        n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
        etype, src_kind, dst_kind = None, "active", "bench"
        i = 2
        while i < len(parts):
            if parts[i] == "type" and i + 1 < len(parts):
                etype = parts[i + 1].lower()
                i += 2
                continue
            if parts[i] == "from" and i + 1 < len(parts):
                src_kind = parts[i + 1].lower()
                i += 2
                continue
            if parts[i] == "to" and i + 1 < len(parts):
                dst_kind = parts[i + 1].lower()
                i += 2
                continue
            i += 1
        p = self.players[self.current]
        opp = self.players[1 - self.current]
        want = int(action.get("target", 0))
        src_pos = int(action.get("source",
                                 want if src_kind in ("bench", "any") else 0))
        # ---- 源 ----
        if src_kind == "active":
            src = fp if fp is not None else p.active
        elif src_kind == "bench":
            if not p.bench:
                return "no target"
            if not (1 <= src_pos <= len(p.bench)):
                src_pos = 1
            src = p.bench[src_pos - 1]
        elif src_kind == "any":
            src = _field_at(p, src_pos) or p.active
        elif src_kind == "opp_active":
            src = opp.active
        else:                                    # opp_any
            src = _field_at(opp, src_pos) or opp.active
        if src is None:
            self._log("no_effect", {"tag": tag, "reason": "无能量源"})
            return "no target"
        # S4：从**对手**身上搬能量时，目标免疫「道具/支援者卡效果」则整条不执行
        if src_kind.startswith("opp") and self._effect_blocked(src, source,
                                                               self.current,
                                                               kinds):
            self._log("blocked", {"target": src.name, "by": "effect_immune",
                                  "effect": "energy_move", "source": source})
            return "blocked: effect_immune"
        # ---- 目标（缺省 target 时取第一个合法位置，保证「无选择动作」也能执行）----
        if dst_kind == "bench":
            if not p.bench:
                return "no target"
            dst = p.bench[(want - 1) if 1 <= want <= len(p.bench) else 0]
        elif dst_kind == "opp_bench":
            if not opp.bench:
                return "no target"
            dst = opp.bench[(want - 1) if 1 <= want <= len(opp.bench) else 0]
        elif dst_kind == "active":
            dst = p.active
        elif dst_kind == "any":
            dst = _field_at(p, want)
        else:                                    # opp_any
            dst = _field_at(opp, want)
        if dst is None or dst is src:
            _pl = opp if dst_kind.startswith("opp") else p
            dst = next((x for x in _pl.all_field() if x is not src), None)
        if dst is None:
            # 场上没有第二个目标（如对手只有 1 只）→ 效果**不执行**，不抛错
            self._log("no_effect", {"tag": tag, "reason": "无第二目标"})
            return "no target"
        if src is None or src is dst:
            raise ValueError("能量移动目标无效")
        moved = 0
        for _ in range(max(0, n)):
            if not _move_one_energy(src, dst, etype):
                break
            moved += 1
        self._log("energy_move", {"player": self.current, "n": moved,
                                  "from": src.name, "to": dst.name,
                                  "etype": etype})
        return f"move {moved} {etype or 'any'} {src.name} -> {dst.name}"

    # ---------- S5 对手手牌信息族（2026-10-28） ----------
    def _discard_opp_random(self, n: int) -> int:
        """**随机**弃对手 n 张手牌（卡面本就写 random / 对手自选 → 见族注释）"""
        o = self.players[1 - self.current]
        done = 0
        for _ in range(max(0, n)):
            if not o.hand:
                break
            i = self.rng.randrange(len(o.hand))
            self._to_discard(o, o.hand.pop(i))
            done += 1
        self._log("discard_opp_random", {"player": 1 - self.current, "n": done})
        return done

    def _shuffle_opp_pokemon(self, scope: str) -> int:
        """把对手 1 只宝可梦**连同附着卡**洗回其牌库 → 移动的卡数（0 = 无目标）

        卡面原型（2026-11-03 第四十二批）：
          · Sylveon「… choose 1 of your opponent's **Benched** Pokémon. Shuffle that
            Pokémon and all attached cards into their deck.」→ ``opp_bench``
          · Shiftry「… choose 1 of your opponent's Pokémon. …」→ ``opp``（含战斗位）

        **不是击倒**：不取奖赏、不触发被击倒特性、不进弃牌堆 —— 卡与附着卡都回
        **对手的牌库**。战斗位被洗走 → 对手必须从后备补位（无后备则直接判负，
        与「无宝可梦」同一路径）。牌库洗牌用 `self.rng`（可复现）。

        确定性目标：``opp`` 取战斗位（位置码 0 的口径），``opp_bench`` 取后备第 0 只。
        """
        opp = self.players[1 - self.current]
        fp = None
        if scope == "opp_bench":
            if opp.bench:
                fp = opp.bench[0]
        else:
            fp = opp.active if opp.active is not None else (
                opp.bench[0] if opp.bench else None)
        if fp is None:
            self._log("no_effect", {"tag": f"shuffle_opp_pokemon:{scope}",
                                    "reason": "对手无合法目标"})
            return 0
        was_active = fp is opp.active
        moved = 0
        # 宝可梦卡本体（化石卡本体是训练家卡，必须放回 TrainerRef）
        opp.deck.append(getattr(fp, "fossil_ref", None) or PokeRef(fp.pdef))
        moved += 1
        # 附着的基本能量：引擎里是计数 → 按点数各还原 1 张能量卡
        for t, n in list(fp.energy.items()):
            for _ in range(int(n)):
                opp.deck.append(EnergyRef(EnergyDef(str(t))))
                moved += 1
        fp.energy.clear()
        # 特殊能量：整张原样（不拆成提供量）
        for ref in list(fp.senergy):
            opp.deck.append(ref)
            moved += 1
        fp.senergy.clear()
        # 道具
        if getattr(fp, "tool_ref", None) is not None:
            opp.deck.append(fp.tool_ref)
            moved += 1
            fp.tool_ref = None
            fp.tool = None
            fp.tool_name = ""
        # 移出场上
        if was_active:
            opp.active = None
        else:
            opp.bench.remove(fp)
        self.rng.shuffle(opp.deck)
        self._log("shuffle_opp_pokemon", {"player": 1 - self.current,
                                          "card": fp.name,
                                          "pos": "active" if was_active else "bench",
                                          "n": moved})
        if was_active:
            if opp.bench:
                opp.active = opp.bench.pop(0)
                opp.active.moved_active_turn = self.turn_number
                opp.active.clear_status()
                self._log("promote", {"player": 1 - self.current,
                                      "to": opp.active.name})
            else:
                self._finish(self.current, "no_pokemon",
                             {"loser": 1 - self.current})
        return moved

    def _shuffle_opp_hand(self, n: int) -> int:
        """**随机**选对手 n 张手牌洗回其牌库（Snorunt / Rotom / Aipom）"""
        o = self.players[1 - self.current]
        moved = 0
        for _ in range(max(0, n)):
            if not o.hand:
                break
            i = self.rng.randrange(len(o.hand))
            o.deck.append(o.hand.pop(i))
            moved += 1
        if moved:
            self.rng.shuffle(o.deck)
        self._log("shuffle_opp_hand", {"player": 1 - self.current, "n": moved})
        return moved

    def _discard_opp_to(self, n: int) -> int:
        """对手弃手牌**直到剩 n 张**（Furfrou / Xerosic's Machinations）

        ⚠ 官方是**对手自选**弃哪张；引擎按随机弃（与既有
        `discard:N:opponent_hand` 同一口径）——**数量精确、内容随机**。
        """
        o = self.players[1 - self.current]
        done = 0
        while len(o.hand) > n:
            i = self.rng.randrange(len(o.hand))
            self._to_discard(o, o.hand.pop(i))
            done += 1
            if done > 60:
                break
        self._log("discard_opp_to", {"player": 1 - self.current, "n": done,
                                     "left": len(o.hand)})
        return done

    def _discard_to_both(self, n: int) -> tuple:
        """**双方**各弃手牌直到剩 ``n`` 张（Hand Trimmer TEF 150）

        卡面：「Each player discards cards from their hand until they have 5 cards
        in their hand. Your opponent discards first. (If a player has 5 or fewer
        cards in their hand, they do not discard.)」

        · 顺序按卡面**对手先弃**（此处结果与顺序无关，但保持卡面口径便于复盘）；
        · 手牌 ≤ ``n`` 的一侧**弃 0 张**（卡面括号里写明），不能为了「凑数」
          反手弃掉几张；
        · 选择权各自在自己手里 ⇒ 与 `discard_opp_to` 同口径，按**随机**弃。
        """
        done = []
        for who in (1 - self.current, self.current):
            pl = self.players[who]
            moved = 0
            while len(pl.hand) > n:
                i = self.rng.randrange(len(pl.hand))
                self._to_discard(pl, pl.hand.pop(i))
                moved += 1
                if moved > 60:
                    break
            self._log("discard_to_both", {"player": who, "n": moved,
                                          "left": len(pl.hand)})
            done.append(moved)
        return tuple(done)

    def _trainer_choose(self, p: PlayerState, tag: str, hi: int) -> str:
        """``choose:<kind>:<arg>:then:<后续 tag>`` —— 挂起一次选择后返回

        目前只实现 ``choose:discard_hand:any:then:…``（Naveen）：
        选项 = **本卡以外**的手牌（卡面写的是 "discard any number of cards
        from your hand"，本卡此时还没进弃牌堆）。

        ⚠ 选项存的是**卡对象身份**（`ref`）而不是下标：弃牌会移动下标，
          按下标落地会弃错牌（对局里只表现为「AI 弃了不该弃的牌」）。
        """
        parts = str(tag).split(":")
        if len(parts) < 3:
            raise ValueError(f"choose tag 非法：{tag}")
        kind = parts[1]
        then = ""
        if "then" in parts:
            i = parts.index("then")
            then = ":".join(parts[i + 1:])
        if kind != "discard_hand":
            raise ValueError(f"未实现的 choose kind：{kind}")
        options = [{"kind": "hand", "hand_index": i,
                    "display": _card_label(c), "ref": c}
                   for i, c in enumerate(p.hand) if i != hi]
        # 数量约束：`any` = 不限（直到 stop）；数字 = 上限（达到即自动闭合）
        _cap = parts[2] if len(parts) > 2 else "any"
        max_pick = int(_cap) if str(_cap).isdigit() else -1
        self._open_choice("discard_hand", options, min_pick=0,
                          max_pick=max_pick, tag=tag, then=then)
        return f"choose {len(options)} cards (then {then or '无'})"

    def _trainer_recon_bottom(self, p: PlayerState, tag: str) -> str:
        """检视**牌库底** N 张，取 1 张宝可梦入手，其余洗回（Dusk Ball）

        牌库表示法：`deck[-1]` 是**顶**，`deck[0]` 是**底** —— 因此「底部 N 张」
        是 `deck[:n]`。取走后其余牌按卡面「shuffle the other cards back into
        your deck」整库洗牌（不是放回底部：这两者在「已知底部信息」上不同）。
        """
        parts = str(tag).split(":")
        n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 7
        n = min(n, len(p.deck))
        if n <= 0:
            raise ValueError("牌库为空")
        bottom = p.deck[:n]
        top = p.deck[n:]
        pick = next((c for c in bottom if isinstance(c, PokeRef)), None)
        if pick is None:
            # 「You may」—— 没有宝可梦就是**不做**：牌序保持原样（不洗库）。
            self._log("recon_bottom", {"player": self.current, "look": n,
                                       "got": None})
            return f"bottom{n} none"
        bottom.remove(pick)
        p.hand.append(pick)
        p.deck = top + bottom
        self.rng.shuffle(p.deck)
        self._log("recon_bottom", {"player": self.current, "look": n,
                                   "got": _card_label(pick)})
        return f"bottom{n} got 1"

    def _trainer_queue(self, p: PlayerState, tag: str) -> str:
        """`queue:<when>:<tag>` —— 把后续效果**存到未来某时点**再执行

        `when`（S2a 接线两种）：
          · `turn_end`            → 本回合结束（owner = 自己，延迟 0）
          · `opp_next_turn_end`   → 对手的下个回合结束（owner = 对手，延迟 1）
        到期由 `events.resolve_turn_end` 在 `_end_turn` 的固定点结算；
        过期未结算会**抛错**（`EffectQueue.stale`）。未知 `when` 直接抛错。
        """
        parts = str(tag).split(":")
        if len(parts) < 3 or not parts[2]:
            raise ValueError(f"queue tag 非法（缺延迟体）：{tag}")
        when = parts[1]
        body = ":".join(parts[2:])
        if when == "turn_end":
            owner, delay = self.current, 0
        elif when == "next_turn_end":
            owner, delay = self.current, 1
        elif when == "opp_next_turn_end":
            owner, delay = 1 - self.current, 1
        else:
            raise ValueError(f"未知延迟时点：{when}")
        events.push(self, owner, delay, body, when="turn_end",
                    source=str(tag))
        return f"queue {when}"

    # ---------- S3（2026-11-08）：奖赏卡 / 牌库底 / 对手区域 ----------

    def _trainer_prize_redeal(self, p: PlayerState) -> str:
        """数出奖赏卡 → 洗匀放牌库**底** → 从**顶**取同样张数作新奖赏卡

        原型：Redeemable Ticket「Count your Prize cards, shuffle them, and put
        them on the bottom of your deck. Then, take that many cards from the top
        of your deck and put them face down as your Prize cards.」

        ⚠ 只有在奖赏卡**对象化**之后才有意义：只计数模式下「奖赏卡」不是具体
          的牌，无从洗回牌库 —— 这时必须抛错，绝不能退化成「什么都不发生」。
        """
        if not zones.has_real_prizes(p):
            raise ValueError("奖赏卡未对象化（real_prizes=False），无法重发奖赏")
        n = len(p.prizes)
        if n <= 0:
            raise ValueError("没有奖赏卡可重发")
        refs = [s.ref for s in p.prizes]
        p.prizes = []
        self.rng.shuffle(refs)
        zones.deck_bottom_put(p, refs)
        self._log("prize_redeal_bottom", {"player": self.current, "n": n})
        got = zones.deal_prizes(self, self.current, n)
        return f"redeal {got}"

    def _trainer_prize_flip(self, p: PlayerState, opp: PlayerState) -> str:
        """把对手 1 张**背面朝上**的奖赏卡翻到正面，再看 1 张随机手牌

        原型：Team Rocket's Bother-Bot。两段都是「谁来做」的问题：
          · 翻**哪一张**奖赏卡 —— 由打出这张卡的牌手选（决策分解）；
          · 看**哪一张**手牌 —— 卡面写死「random」，因此由 `rng` 决定；
          · 要不要**交换** —— 「You may」，由牌手选（第二个 confirm 选择）。
        面朝下时「翻哪张」看起来无所谓，但对手可能有已翻面的奖赏卡，
        候选集必须只含背面朝上的那些（卡面限定 face-down）。
        """
        slots = [i for i, s in enumerate(opp.prizes) if not s.face_up]
        if not slots:
            raise ValueError("对手没有背面朝上的奖赏卡")
        if not opp.hand:
            raise ValueError("对手手牌为空")
        self._open_choice(
            "prize_flip_opp",
            [{"slot": i, "display": f"对手奖赏卡 #{i + 1}"} for i in slots],
            min_pick=1, max_pick=1, tag="prize:flip_opp")
        return "choice:prize_flip_opp"

    def _trainer_flute(self, p: PlayerState, opp: PlayerState, tag: str) -> str:
        """亮出对手牌库顶 N 张 → 任意数量**基础**宝可梦放到对手后备 → 余牌洗回

        原型：Accompanying Flute。「任意数量」必须真由牌手选（min=0），
        上限还要受对手后备空位限制 —— 引擎绝不替玩家挑。
        """
        n = int(tag.split(":")[1]) if tag.split(":")[1].isdigit() else 5
        if not opp.deck:
            raise ValueError("对手牌库为空")
        room = self.cfg.max_bench - len(opp.bench)
        k = min(n, len(opp.deck))
        revealed = [opp.deck.pop() for _ in range(k)]     # top[0] = 牌库顶
        self._log("flute_reveal", {"player": 1 - self.current, "n": k,
                                   "cards": [_card_label(c) for c in revealed]})
        basics = [c for c in revealed
                  if isinstance(c, PokeRef) and c.pdef.stage == 0]
        if not basics or room <= 0:
            # 没有基础宝可梦（或对手后备没有空位）→ 亮出的牌原样洗回。
            # ⚠ 不能「什么都不做」把牌揣在手里：那就是静默吞牌。
            opp.deck.extend(revealed)
            self.rng.shuffle(opp.deck)
            self._log("flute_done", {"player": self.current, "placed": 0,
                                     "shuffled": k})
            return f"flute{k} none"
        self._open_choice(
            "opp_bench_from_deck",
            [{"ref": c, "display": _card_label(c)} for c in basics],
            min_pick=0, max_pick=min(len(basics), room), tag=tag,
            ctx={"revealed": revealed})
        return f"choice:flute{len(basics)}/{k}"

    def _trainer_discard_tool_senergy(self, opp: PlayerState,
                                      kind: str = "") -> str:
        """弃对手**同一只**身上的 1 张道具 + 1 张特殊能量（Ruffian）

        没有任一只同时满足「有道具 且 有特殊能量」时**什么都不做**（卡面要求
        两者都能弃才有效果；拆开弃会被静默加强）。
        """
        for fp in opp.all_field():
            if not fp.tool or not fp.senergy:
                continue
            if self._trainer_poke_blocked(fp, kind):
                continue
            name = fp.tool_name or fp.tool
            ref = fp.tool_ref
            _drop_tool(fp)
            if ref is not None:
                self._to_discard(opp, ref)
            else:
                opp.discard.append(str(name or "Pokémon Tool"))
            _discard_special_energy(self, opp, fp, 1)
            self._log("discard_tool_senergy", {"player": self.current,
                                               "owner": opp.name,
                                               "target": fp.name,
                                               "tool": name})
            return f"{fp.name}:tool+senergy"
        self._log("discard_tool_senergy", {"player": self.current,
                                          "owner": opp.name, "target": None})
        return "none"

    def _do_opp_hand(self, p: PlayerState, tag: str, action: dict) -> str:
        """对手手牌操作（``opp_hand:<op>:<sel>[:<n>|:all]``）—— **我方选**口径

        卡面形如「Your opponent reveals their hand, and you discard a card you
        find there.」：选择权在使用者手里，因此默认按**手牌顺序**取（确定性、
        可复现），并支持 ``action['hand_pick']`` 显式指定下标（供策略侧挑最优）。

        与 ``discard_opp_random`` 的关键差别：那里卡面写的是对手自选，引擎按
        随机；这里卡面是**我**选，用随机口径等于把一个可选决策退化成碰运气。

        返回执行摘要（无匹配牌时返回空串：规则上「效果不执行」，不是错误）。
        """
        op, sel, n = _parse_opp_hand_tag(tag)
        o = self.players[1 - self.current]
        cand = [i for i, c in enumerate(o.hand) if _opp_hand_matches(c, sel)]

        want = action.get("hand_pick") if isinstance(action, dict) else None
        if want:
            if not isinstance(want, (list, tuple)):
                raise ValueError(f"opp_hand hand_pick 必须是下标列表：{want!r}")
            picked: list = []
            for i in want:
                if not isinstance(i, int) or isinstance(i, bool):
                    raise ValueError(f"opp_hand hand_pick 含非整数下标：{i!r}")
                if i < 0 or i >= len(o.hand):
                    raise ValueError(f"opp_hand hand_pick 越界：{i}")
                if not _opp_hand_matches(o.hand[i], sel):
                    # 显式选择与筛选条件冲突 → 抛错而不是悄悄换成别的牌：
                    # 悄悄替换等于「照做了但做的是另一件事」，最难发现。
                    raise ValueError(
                        f"opp_hand hand_pick 第 {i} 张不符合筛选条件 {sel}")
                picked.append(i)
            cand = picked
        if n != "all":
            cand = cand[:int(n)]
        if not cand:
            return ""

        moved = 0
        # 从后往前 pop，避免下标偏移
        for i in sorted(cand, reverse=True):
            c = o.hand.pop(i)
            if op == "discard":
                self._to_discard(o, c)
            else:                        # bottom：牌库顶是列表末尾，故插入前端
                o.deck.insert(0, c)
            moved += 1
        self._log("opp_hand", {"player": 1 - self.current, "op": op,
                               "sel": sel, "n": moved})
        return f"opp {op} {moved}"

    def _do_attach_named(self, p: PlayerState, holder, tag: str) -> str:
        """从**弃牌堆**取 N 张**具名**能量附着到 holder（S2d）

        tag：``attach_named:<n>:<英文卡名>:<zone>:<dst>``
        （zone 目前只支持 `discard`，dst 只支持 `self`；其它形态抛错）

        ⚠ 必须按**卡名**匹配，不能退化成按属性：Spiky Energy 提供 `{C}`，
          按属性取会捞到一张基本无色能量 —— 效果"看似做了"，实际拿错卡。
        """
        parts = str(tag).split(":")
        if len(parts) < 3:
            raise ValueError(f"attach_named tag 非法：{tag}")
        n = int(parts[1]) if parts[1].isdigit() else 1
        want = parts[2]
        zone = parts[3] if len(parts) > 3 and parts[3] else "discard"
        dst = parts[4] if len(parts) > 4 and parts[4] else "self"
        if zone != "discard":
            raise ValueError(f"attach_named 只支持 discard：{tag}")
        if dst != "self":
            raise ValueError(f"attach_named 只支持 self：{tag}")
        if holder is None:
            raise ValueError(f"attach_named 缺少持有者：{tag}")
        key = _name_key(want)
        moved = 0
        label = ""
        for ref in list(p.discard_refs):
            if moved >= n:
                break
            if not isinstance(ref, EnergyRef):
                continue
            if _name_key(ref.edef.name) != key:
                continue
            p.discard_refs.remove(ref)
            label = _card_label(ref)
            if label in p.discard:
                p.discard.remove(label)
            if ref.edef.is_special:
                holder.senergy.append(ref)
            else:
                holder.add_energy(ref.edef.type)
            moved += 1
        if moved:
            self._log("attach", {"player": self.current,
                                 "target": holder.name, "etype": want,
                                 "energy_now": holder.total_energy(),
                                 "by": "attach_named", "n": moved})
        return f"attach {moved} {want} from discard"

    def _do_attach_spread(self, p: PlayerState, tag: str,
                          action: dict,
                          holder: Optional[FieldPokemon] = None) -> str:
        """多张能量**自由分配**附着（``attach_spread:...``）

        卡面原型：「Search your deck for up to 3 Basic Energy cards and attach
        them to your Pokémon **in any way you like**. Then, shuffle your deck.」

        分配口径（引擎无交互选择系统，沿用项目既有 choose 类做法）：
          · ``action['attach_plan']`` 给位置码列表（0 = active，1..n = 后备）；
          · 缺省按**轮转**依次落到「active → bench[0] → bench[1] → …」，
            保证可复现，且不会把 n 张全堆在 active 上（那等于把「自由分配」
            塌缩成一个特定选择）。

        取不到符合条件的基本能量、或目标集合为空 → 效果不执行（规则上不是
        错误）。卡面写「Then, shuffle your deck」→ 动过牌库就洗。

        ``holder`` + 末尾 ``:selfko``（Magneton SSP 59，2026-11-02 第三十七批）：
        卡面「If you use this Ability, this Pokémon is Knocked Out.」——
        **用掉就付**，与效果是否真的附着成功无关（玩家明知没能量就不会用；
        合法动作表已把「无能量/无目标」的情况排除，见 `_ability_legal`）。
        写成「moved > 0 才自爆」等于给了一次免费试探，是静默削弱代价。
        """
        from .cards import EnergyRef          # noqa: F401（zone 校验用）
        n, etype, zone, scope, selfko = _parse_attach_spread_tag(tag)
        targets = _spread_targets(p, scope)
        moved = 0
        # 「of different types」（第三十九批，Terapagos SSP 161）：每张必须换一个
        # 颜色。`avoid` 是跨张累积的集合，由本次调用独占 —— 不能做成实例字段，
        # 否则会泄漏到下一次结算。
        avoid: Optional[set] = set() if "distinct" in str(tag).split(":") else None
        # 缺省分配池（`attach_plan` 未指定时用）：
        # 带 `:selfko` 时**排除持有者自己** —— 它马上要被击倒，附给它的能量会
        # 随它进弃牌堆，等于这一张白附（净效果为零、还白丢 1 张奖赏）。
        # 显式 `attach_plan` 仍允许指定持有者（规则上合法），但缺省不该替他做
        # 这个明显亏本的选择。排除后为空时退回全体（否则整条效果无法分配）。
        default_pool = targets
        if selfko and holder is not None:
            _rest = [fp for fp in targets if fp is not holder]
            if _rest:
                default_pool = _rest
        if targets:
            plan = action.get("attach_plan") if isinstance(action, dict) else None
            seq: list = []
            if plan is not None:
                if not isinstance(plan, (list, tuple)):
                    raise ValueError(f"attach_plan 必须是位置码列表：{plan!r}")
                for pos in plan:
                    if not isinstance(pos, int) or isinstance(pos, bool):
                        raise ValueError(f"attach_plan 含非整数位置码：{pos!r}")
                    fp = _field_at(p, pos)
                    if fp is None:
                        raise ValueError(f"attach_plan 位置码无效：{pos}")
                    if fp not in targets:
                        # 显式指定了不符合卡面限定的宝可梦 → 抛错，不得悄悄改附别人
                        raise ValueError(
                            f"attach_plan 位置 {pos} 不符合目标限定 {scope}")
                    seq.append(fp)

            for k in range(n):
                if seq:
                    if k >= len(seq):
                        break
                    fp = seq[k]
                else:
                    fp = default_pool[k % len(default_pool)]
                if _attach_energy_from_zones(self, p, fp, 1, etype, zone,
                                             avoid=avoid) <= 0:
                    break
                moved += 1
            if moved:
                if zone in ("deck", "any"):
                    # 卡面固定后缀「Then, shuffle your deck.」——动过牌库就必须洗
                    self.rng.shuffle(p.deck)
                self._log("attach_spread", {"player": self.current, "n": moved,
                                            "etype": etype, "zone": zone,
                                            "scope": scope})
        detail = (f"attach {moved} {etype or 'any'} spread over {len(targets)}"
                  if moved else "")
        if selfko:
            if holder is None:
                # tag 说「用完自爆」却没人可爆 → 内部接线错误，绝不静默跳过
                raise ValueError(f"attach_spread 带 :selfko 但缺少持有者：{tag}")
            if not holder.is_ko:
                self._resolve_self_ko(holder)
                detail = (detail + " + self KO") if detail else "self KO"
        return detail

    def _do_attach_spread_flip(self, p: PlayerState, tag: str,
                               action: dict,
                               holder: Optional[FieldPokemon] = None) -> str:
        """抛币计数版多张自由分配（``attach_spread_flip:<coins>:<per>:...``）

        卡面原型（Mega Audino ex「Kaleidowaltz」）：
        「Flip 3 coins. For each heads, search your deck for up to 2 Basic Energy
         cards and attach them to your Pokémon in any way you like. Then, shuffle
         your deck.」

        张数 = 正面枚数 × ``per``，在**结算时**现算；抛完之后把结果交给
        `_do_attach_spread` 的统一分配逻辑（`attach_plan` / 轮转缺省），
        保证与固定张数的 `attach_spread:` 分配口径**完全一致** —— 两套分配
        实现迟早会漂（同一张卡换个形态就换一种强度）。
        """
        coins, per, etype, zone, scope = _parse_attach_spread_flip_tag(tag)
        heads = 0
        for _ in range(coins):
            if self.rng.random() < 0.5:
                heads += 1
        self._log("coin", {"player": self.current,
                           "result": f"{heads}/{coins} heads", "tag": tag})
        n = heads * per
        if n < 1:
            # 全反面 → 没有能量可检索。不是错误（卡面允许），也不必洗牌。
            return ""
        sub = f"attach_spread:{n}:{etype or 'any'}:{zone}"
        if scope != "any":
            sub += f":target:{scope}"
        return self._do_attach_spread(p, sub, action, holder=holder)

    def _do_attach_each_bench(self, p: PlayerState, tag: str,
                              action: dict) -> str:
        """「每只后备**各** per 张、绑定给那一只」（``attach_each_bench:...``）

        卡面原型（Mega Gardevoir ex「Overflowing Wishes」）：
        「For each of your Benched Pokémon, search your deck for a Basic {P} Energy
         card and attach it to that Pokémon. Then, shuffle your deck.」

        ⚠ 与 `attach_spread` 的差别是**分配不可选**：每只后备各取 ``per`` 张、
        附给**它自己**。不能漏掉某只，也不能把多张堆在同一只上 —— 所以本形态
        **不读** `attach_plan`（显式给了就抛错，免得调用方以为能选，实际被忽略）。
        """
        per, etype, zone = _parse_attach_each_bench_tag(tag)
        if isinstance(action, dict) and action.get("attach_plan") is not None:
            raise ValueError("attach_each_bench 的分配不可选，不接受 attach_plan")
        moved = 0
        for fp in p.bench:                 # 后备顺序固定 → 同局面必得同结果
            for _ in range(per):
                if _attach_energy_from_zones(self, p, fp, 1, etype, zone) <= 0:
                    break                  # 该只取不满 → 只影响这一只，继续下一只
                moved += 1
        if moved and zone in ("deck", "any"):
            # 卡面固定后缀「Then, shuffle your deck.」（动过牌库才洗，同 spread）
            self.rng.shuffle(p.deck)
        if not moved:
            return ""
        return f"attach {moved} {etype or 'any'} to {len(p.bench)} bench"

    def _do_attach_spread_multi(self, p: PlayerState, tag: str,
                                action: dict) -> str:
        """双色「**or 1 of each**」检索并自由分配（``attach_spread_multi:...``）

        卡面原型（Steven's Metagross ex「X-Boot」）：
        「search your deck for a Basic {P} Energy card, a Basic {M} Energy card,
         **or 1 of each** and attach them to your {P} Pokémon and {M} Pokémon in
         any way you like.」

        语义是**三选一**：只 P / 只 M / 各 1。⇔「每类**至多** per 张、总量可少」。
        ⚠ 与 Joltik 的「up to 2 {G} **and** up to 2 {L}」不同：那里两条独立
        `attach_spread:` 是「各取各的」，本形态若也拆成两条就会**强制每类各拿 1 张**
        （比卡面强）。所以这里由**一个**循环按 `(颜色, 上限)` 逐类取，取不满不算错。

        分配口径与 `attach_spread` 完全一致（`attach_plan` 优先，否则轮转）。
        """
        parts = str(tag).split(":")
        if len(parts) < 6:
            raise ValueError(f"attach_spread_multi tag 字段不足：{tag}")
        if not parts[1].isdigit() or int(parts[1]) < 1 \
                or not parts[3].isdigit() or int(parts[3]) < 1:
            raise ValueError(f"attach_spread_multi 张数非法：{tag}")
        order = [(parts[2], int(parts[1])), (parts[4], int(parts[3]))]
        if any(not et or et == "any" for et, _ in order):
            raise ValueError(f"attach_spread_multi 必须指定两种属性：{tag}")
        zone = parts[5]
        if zone not in ("hand", "discard", "deck", "any"):
            raise ValueError(f"attach_spread_multi 区域非法：{zone}")
        scope = "any"
        if "target" in parts:
            i = parts.index("target")
            scope = parts[i + 1] if i + 1 < len(parts) else ""
            if not scope:
                raise ValueError(f"attach_spread_multi 缺少目标限定：{tag}")
        targets = _spread_targets(p, scope)
        if not targets:
            return ""
        plan = action.get("attach_plan") if isinstance(action, dict) else None
        seq: list = []
        if plan is not None:
            if not isinstance(plan, (list, tuple)):
                raise ValueError(f"attach_plan 必须是位置码列表：{plan!r}")
            for pos in plan:
                if not isinstance(pos, int) or isinstance(pos, bool):
                    raise ValueError(f"attach_plan 含非整数位置码：{pos!r}")
                fp = _field_at(p, pos)
                if fp is None:
                    raise ValueError(f"attach_plan 位置码无效：{pos}")
                if fp not in targets:
                    raise ValueError(
                        f"attach_plan 位置 {pos} 不符合目标限定 {scope}")
                seq.append(fp)
        moved, pos = 0, 0
        for et, want in order:
            for _ in range(want):
                if seq and pos >= len(seq):
                    break                  # 计划用尽 → 后续张数不再落位
                fp = seq[pos] if seq else targets[pos % len(targets)]
                if _attach_energy_from_zones(self, p, fp, 1, et, zone) <= 0:
                    break                  # 该色取不满 → 只影响这一类
                moved += 1
                pos += 1
        if moved and zone in ("deck", "any"):
            self.rng.shuffle(p.deck)
        if not moved:
            return ""
        return f"attach {moved} ({order[0][0]}+{order[1][0]}) over {len(targets)}"

    def _do_attach_each_pick(self, p: PlayerState, tag: str,
                             action: dict) -> str:
        """「**选**至多 N 只自家宝可梦，每只各 per 张」（``attach_each_pick:...``）

        卡面原型（Janine's Secret Art SFA 59）：
        「Choose up to 2 of your {D} Pokémon. For each of those Pokémon, search
         your deck for a Basic {D} Energy card and attach it to that Pokémon.
         Then, shuffle your deck. If you attached Energy to your **Active**
         Pokémon in this way, it is now Poisoned.」

        与 `attach_each_bench` 的两处差别：
          · 目标是**选出**的至多 N 只（不是全部后备）→ 确定性默认按
            active→bench 顺序取前 N 只；
          · 末尾**条件性**自我中毒：只有真的附到了战斗位才中毒。无条件
            `status_self:poison` 是错的（会白给自家战斗位挂状态）。
        """
        parts = str(tag).split(":")
        if len(parts) < 7:
            raise ValueError(f"attach_each_pick tag 字段不足：{tag}")
        if not parts[1].isdigit() or int(parts[1]) < 1 \
                or not parts[2].isdigit() or int(parts[2]) < 1:
            raise ValueError(f"attach_each_pick 张数非法：{tag}")
        mx, per = int(parts[1]), int(parts[2])
        etype = None if parts[3] in ("any", "") else parts[3]
        zone = parts[4]
        if zone not in ("hand", "discard", "deck", "any"):
            raise ValueError(f"attach_each_pick 区域非法：{zone}")
        # ⚠ scope 可能**自带冒号**（`bench:type-colorless` = Glass Trumpet 的
        # 「后备 + 属性」双限定）。用 `parts[6]` 单段取会把后半截丢掉，目标集合
        # 悄然放宽成「全部后备」—— 静默加强且不报错（第五十九批实测踩到）。
        _tail = [x for x in parts[6:] if x != "poison_if_active"]
        want_poison = len(_tail) != len(parts) - 6
        scope = ":".join(_tail)
        picked = _spread_targets(p, scope)[:mx]      # 至多 N 只，顺序确定
        moved, hit_active = 0, False
        for fp in picked:
            for _ in range(per):
                if _attach_energy_from_zones(self, p, fp, 1, etype, zone) <= 0:
                    break                            # 该只取不满，继续下一只
                moved += 1
                if p.active is fp:
                    hit_active = True
        if moved and zone in ("deck", "any"):
            self.rng.shuffle(p.deck)
        detail = (f"attach {moved} {etype or 'any'} to {len(picked)} picked"
                  if moved else "")
        if want_poison and hit_active:
            # 「If you attached Energy to your Active Pokémon in this way, it is
            # now Poisoned.」—— 前提是**真的附上了**，不是无条件挂状态。
            assert p.active is not None
            p.active.status.add("poison")
            self._log("status", {"player": self.current,
                                 "target": p.active.name, "status": "poison",
                                 "side": "self"})
            detail = (detail + " + poison") if detail else "poison"
        return detail

    def _do_attach_flip_until(self, p: PlayerState, tag: str,
                              action: dict) -> str:
        """抛币到反面为止、按正面数检索附能（``attach_flip_until:<etype>:<zone>``）

        卡面原型（Snorlax POR 63「Gormandizer」）：
        「Flip a coin until you get tails. Search your deck for an amount of Basic
         Energy up to the number of heads and attach it to **this Pokémon**.」

        全部附给**战斗位**（卡面写 this Pokémon，不是自由分配）；张数 = 正面枚数
        （几何分布，期望 1）。上限 100 次抛币防死循环（概率 2^-100 可忽略）。
        """
        parts = str(tag).split(":")
        etype = None if len(parts) < 2 or parts[1] in ("any", "") else parts[1]
        zone = parts[2] if len(parts) > 2 else "deck"
        if zone not in ("hand", "discard", "deck", "any"):
            raise ValueError(f"attach_flip_until 区域非法：{zone}")
        target = p.active
        if target is None:
            return ""
        heads = 0
        for _ in range(100):
            res = "heads" if self.rng.random() < 0.5 else "tails"
            self._log("coin", {"player": self.current, "result": res, "tag": tag})
            if res == "tails":
                break
            heads += 1
        if heads < 1:
            return ""
        got = _attach_energy_from_zones(self, p, target, heads, etype, zone)
        if got and zone in ("deck", "any"):
            self.rng.shuffle(p.deck)
        return f"flip until tails -> {heads} heads, attach {got}"

    def _trainer_coin_gate_attach(self, p: PlayerState, tag: str,
                                  action: dict) -> str:
        """训练家抛币门（``coin_gate_attach:<coins>:<n>:<etype>:<zone>``）

        卡面原型（Energy Coin BLK 81）：
        「Flip 2 coins. If **both of them** are heads, search your deck for a Basic
         Energy card and attach it to 1 of your Pokémon.」

        ⚠ 招式侧的同名语义走 `_attack_mods` / `_apply_coin_non_damage`（那条链
        负责「不重复抛币」）；训练家没有那条链，这里**自己抛一次**并直接派发。
        门不通过 = 合法但无效果（卡已打出，进弃牌堆）。
        """
        parts = str(tag).split(":")
        if len(parts) < 5 or not parts[1].isdigit() or not parts[2].isdigit():
            raise ValueError(f"coin_gate_attach tag 非法：{tag}")
        coins, n = int(parts[1]), int(parts[2])
        etype = None if parts[3] in ("any", "") else parts[3]
        zone = parts[4]
        if zone not in ("hand", "discard", "deck", "any"):
            raise ValueError(f"coin_gate_attach 区域非法：{zone}")
        results = [("heads" if self.rng.random() < 0.5 else "tails")
                   for _ in range(max(0, coins))]
        ok = bool(results) and all(r == "heads" for r in results)
        self._log("coin", {"player": self.current,
                           "result": f"{results.count('heads')}/{coins} heads",
                           "tag": tag})
        if not ok:
            return "no effect (coin gate failed)"
        fp = _field_at(p, int((action or {}).get("target", 0)))
        if fp is None:
            raise ValueError("附能目标无效")
        got = _attach_energy_from_zones(self, p, fp, n, etype, zone)
        if got and zone in ("deck", "any"):
            self.rng.shuffle(p.deck)
        return f"coin gate passed, attach {got} {etype or 'any'} to {fp.name}"

    def _trainer_coin_flip(self, p: PlayerState, tag: str,
                           action: dict) -> str:
        """``coin_flip:heads:<a>[|tails:<b>]`` —— 训练家**只抛一次**再派发

        卡面原型（Team Rocket's Venture Bomb）：
        「Flip a coin. If heads, put 2 damage counters on 1 of your opponent's
         Pokémon. If tails, put 2 damage counters on your Active Pokémon.」
        同一枚币只可能有一个分支生效 —— 拆成两条 `coin:` tag 会抛两次
        （两分支可能同时生效或都不生效），属**静默错算**。

        目前只承接 `counters:` 子效果（指示物直放，含 `:scope:self`）。
        其它子效果直接抛错 —— 新增形态时在此补齐，绝不静默忽略。
        """
        branches = _parse_coin_flip_tag(tag)
        res = "heads" if self.rng.random() < 0.5 else "tails"
        self._log("coin", {"player": self.current, "result": res, "tag": tag})
        sub = branches.get(res)
        if not sub:
            return f"coin {res}: no effect"
        for one in sub.split("|"):
            if not one:
                continue
            if one.startswith("counters:"):
                self._apply_counters(one, action)
            else:
                raise ValueError(f"coin_flip 训练家子效果未建模：{one}")
        return f"coin {res}: {sub}"

    def _trainer_shuffle_both_coin_draw(self, p: PlayerState, tag: str,
                                        card, action: dict) -> str:
        """``shuffle_both_coin_draw:<mine_h>:<opp_h>:<mine_t>:<opp_t>``（Harlequin）

        卡面：「Each player shuffles their hand into their deck. Then, flip a
        coin. If heads, you draw 5 cards, and your opponent draws 3 cards. If
        tails, you draw 3 cards, and your opponent draws 5 cards.」

        顺序必须与卡面一致：**先双方洗回手牌**，再抛**一次**币，再按结果给双方
        抽不同张数。⚠ 不能拆成「对手洗牌 + 我方抽」两条动作：训练家只有一个
        卡级 tag，拆开只能保留一半（另一半静默丢失）。
        自己那张支援者卡在结算中，**不算**「手牌」被洗回（同 `shuffle_draw:`）。
        """
        parts = str(tag).split(":")
        if len(parts) != 5 or not all(x.isdigit() for x in parts[1:]):
            raise ValueError(f"shuffle_both_coin_draw tag 非法：{tag}")
        mine_h, opp_h, mine_t, opp_t = (int(x) for x in parts[1:])
        opp = self.players[1 - self.current]
        # 双方手牌洗回**各自**牌库（自己的本卡除外）
        own_others = [x for x in p.hand if x is not card]
        p.hand = [x for x in p.hand if x is card]
        p.deck.extend(own_others)
        opp_n = len(opp.hand)
        opp.deck.extend(opp.hand)
        opp.hand = []
        self.rng.shuffle(p.deck)
        self.rng.shuffle(opp.deck)
        self._log("hand_shuffle", {"player": self.current,
                                   "n": len(own_others), "side": "both"})
        self._log("hand_shuffle", {"player": 1 - self.current,
                                   "n": opp_n, "side": "both"})
        res = "heads" if self.rng.random() < 0.5 else "tails"
        self._log("coin", {"player": self.current, "result": res, "tag": tag})
        mine, theirs = ((mine_h, opp_h) if res == "heads"
                        else (mine_t, opp_t))
        got_mine = self._safe_draw(self.current, mine, source="trainer")
        got_opp = 0
        if not self.finished:
            got_opp = self._safe_draw(1 - self.current, theirs,
                                      source="trainer")
        return f"coin {res}: draw {got_mine}/{got_opp}"

    def _trainer_shuffle_both_bottom_coin_draw(self, p: PlayerState, tag: str,
                                               card) -> str:
        """``shuffle_both_bottom_coin_draw:<heads>:<tails>``（S5，Lucian）

        卡面：「Each player shuffles their hand **and puts it on the bottom of
        their deck**. If either player put any cards on the bottom of their deck
        in this way, each player flips a coin. If heads, that player draws 6
        cards. If tails, they draw 3 cards.」

        ⚠ 三处**不能**照抄 `shuffle_both_coin_draw`（Harlequin）：
          1. 去向是牌库**底**且**不洗牌**（Harlequin 是洗回整副）—— 洗了就等于
             把「底下是什么」这条信息抹掉，牌库顺序完全不同。
          2. 币是**每人各一枚**：四条分支（正/正、正/反、反/正、反/反）都要可达；
             单币版只有两条，抽牌数完全相关。
          3. 触发条件是「**任一**玩家真的放了牌到底」；双方手牌都空时**不抛币**。
        自己那张支援者卡仍在手上结算，**不**参与放底（同 `shuffle_draw:`）。
        """
        parts = str(tag).split(":")
        if len(parts) != 3 or not all(x.isdigit() for x in parts[1:]):
            raise ValueError(f"shuffle_both_bottom_coin_draw tag 非法：{tag}")
        heads_n, tails_n = int(parts[1]), int(parts[2])
        opp = self.players[1 - self.current]
        own_others = [x for x in p.hand if x is not card]
        p.hand = [x for x in p.hand if x is card]
        own_n = zones.deck_bottom_put(p, own_others)
        opp_n = len(opp.hand)
        zones.deck_bottom_put(opp, opp.hand)
        opp.hand = []
        self._log("hand_bottom", {"player": self.current, "n": own_n})
        self._log("hand_bottom", {"player": 1 - self.current, "n": opp_n})
        if not own_n and not opp_n:
            # 「If **either** player put any cards on the bottom」不成立 → 不抛币
            return "no cards to bottom: no flip"
        got = {}
        for side in (self.current, 1 - self.current):
            res = "heads" if self.rng.random() < 0.5 else "tails"
            self._log("coin", {"player": side, "result": res, "tag": tag})
            want = heads_n if res == "heads" else tails_n
            got[side] = self._safe_draw(side, want, source="trainer")
            if self.finished:
                break
        return (f"bottom {own_n}/{opp_n}; draw "
                f"{got.get(self.current, 0)}/{got.get(1 - self.current, 0)}")

    def _trainer_coin_search(self, p: PlayerState, tag: str) -> str:
        """``coin_search:<heads_stage>:<tails_stage>[:category:<c>]``（S5）

        卡面（Team Rocket's Great Ball）：「Flip a coin. If heads, search your deck
        for an Evolution Team Rocket's Pokémon, reveal it, and put it into your
        hand. If tails, search your deck for a Basic Team Rocket's Pokémon …
        Then, shuffle your deck.」

        ⚠ 两侧**类别不同**：把 tag 压成单一的 `search_pokemon:1` 会静默变成
        「随便找一张宝可梦」（少了 stage 与类别两道限定）。洗牌无论是否找到
        都必须做（卡面「Then, shuffle your deck.」是无条件的）。
        """
        parts = str(tag).split(":")
        if len(parts) < 3:
            raise ValueError(f"coin_search tag 非法：{tag}")
        h_stage, t_stage = parts[1], parts[2]
        flt: dict = {}
        i = 3
        while i + 1 < len(parts):
            if parts[i] in ("stage", "category", "type", "hp", "resistance"):
                flt[parts[i]] = parts[i + 1]
            i += 2
        if not flt.get("category"):
            # 卡面类别限定是这张卡的**全部意义**（「Team Rocket's Pokémon」）；
            # 缺了就退化成「任意宝可梦」，属静默放宽 —— 直接报错而不是照做。
            raise ValueError(f"coin_search 缺少类别限定：{tag}")
        res = "heads" if self.rng.random() < 0.5 else "tails"
        self._log("coin", {"player": self.current, "result": res, "tag": tag})
        flt = dict(flt, stage=(h_stage if res == "heads" else t_stage))
        found = _take_kind_from_deck(p, 1, "pokemon", flt)
        self.rng.shuffle(p.deck)
        self._log("search", {"player": self.current, "kind": "pokemon",
                             "n": found, "stage": flt["stage"],
                             "category": flt["category"], "tag": tag})
        return f"coin {res}: search {found} ({flt['stage']}/{flt['category']})"

    def _trainer_shuffle_draw_split(self, p: PlayerState, tag: str,
                                    hi: int) -> str:
        """双方洗回手牌后**各抽不同张数**（``shuffle_draw_split:<mine>:<theirs>``）

        卡面（Unfair Stamp SFA 71）：「Each player shuffles their hand into their
        deck. Then, you draw 5 cards, and your opponent draws 2 cards.」

        ⚠ 与 `draw_both:N` 差一个量纲 —— 那是「双方**各**抽 N」（同数），本条是
        「我方 N / 对手 M」。拿 `draw_both` 顶替会静默改掉对手的手牌数，
        而对局里既没有报错也没有测试会红。
        自己那张卡此刻仍在手上（派发末尾才弃置），**不**参与洗回（同 `shuffle_draw:`）。
        """
        parts = str(tag).split(":")
        if len(parts) != 3 or not all(x.isdigit() for x in parts[1:]):
            raise ValueError(f"shuffle_draw_split tag 非法：{tag}")
        mine, theirs = int(parts[1]), int(parts[2])
        opp = self.players[1 - self.current]
        others = [c for i, c in enumerate(p.hand) if i != hi]
        p.deck.extend(others)
        p.hand = [c for i, c in enumerate(p.hand) if i == hi]
        opp_n = len(opp.hand)
        opp.deck.extend(opp.hand)
        opp.hand = []
        self.rng.shuffle(p.deck)
        self.rng.shuffle(opp.deck)
        self._log("hand_shuffle", {"player": self.current,
                                   "n": len(others), "side": "both"})
        self._log("hand_shuffle", {"player": 1 - self.current,
                                   "n": opp_n, "side": "both"})
        got_mine = self._safe_draw(self.current, mine, source="trainer")
        got_opp = 0
        if not self.finished:
            got_opp = self._safe_draw(1 - self.current, theirs,
                                      source="trainer")
        return f"shuffle draw {got_mine}/{got_opp}"

    def _trainer_team_boost(self, p: PlayerState, tag: str) -> str:
        """``team_boost:<n>[:type:<color>][:cat:<c1,c2>]`` —— 全队**本回合**增伤

        卡面（2026-11-04 第六十批）：
          「During this turn, attacks used by your **{F}** Pokémon do 30 more
           damage to your opponent's Active Pokémon (before applying Weakness
           and Resistance).」（Premium Power Pro）
          「… attacks used by your Pokémon do 40 more damage to your opponent's
           Active Pokémon **{ex}** …」（Black Belt's Training）

        与既有机制的分工：
          · `dmg_plus:` 是**这一招自己**的加伤，本条的修正作用于**全队**；
          · `team_shield` 是「对方回合**减**伤」，本条是「**本回合增**伤」，方向与窗口都相反。
        记在**玩家**层面：逐只写会漏掉之后上场的宝可梦（同 `team_shield`）。

        ⚠ 到期口径：卡面是「this turn」不是「next turn」—— 只在**施加当回合**有效。
        照抄减伤的 `until = turn + 2` 会多生效一个回合（对局里看不出来，只多打了伤害）。
        """
        parts = str(tag).split(":")
        if len(parts) < 2 or not parts[1].isdigit():
            raise ValueError(f"team_boost tag 非法：{tag}")
        n = int(parts[1])
        etype, cats = "", ""
        i = 2
        while i < len(parts):
            if parts[i] == "type" and i + 1 < len(parts):
                etype = parts[i + 1].lower()
                i += 2
                continue
            if parts[i] == "cat" and i + 1 < len(parts):
                cats = parts[i + 1].lower()
                i += 2
                continue
            raise ValueError(f"team_boost 未知段：{tag}")
        if etype and etype not in TYPES:
            raise ValueError(f"team_boost 未识别的属性：{tag}")
        p.team_boost_n = n
        p.team_boost_type = etype
        p.team_boost_cats = cats
        p.team_boost_turn = self.turn_counts[self.current]
        self._log("team_boost", {"player": self.current, "n": n,
                                 "type": etype or "any",
                                 "cats": cats or "any"})
        return f"team boost +{n} ({etype or 'any'}/{cats or 'any'})"

    def _trainer_team_shield(self, p: PlayerState, tag: str) -> str:
        """``team_shield:reduce:<n>:type:<color>`` —— 全队限时减伤

        卡面原型（Iron Defender）：「During your opponent's next turn, all of
        your {M} Pokémon take 30 less damage from attacks from your opponent's
        Pokémon (after applying Weakness and Resistance). (This includes new
        Pokémon that come into play.)」

        记在**玩家**层面（`PlayerState.team_shield_*`）：逐只写 shield 会漏掉
        「之后上场的宝可梦」。到期 = 施加方 `turn_counts + 2`（同 shield 口径）。
        """
        parts = str(tag).split(":")
        if len(parts) != 5 or not parts[2].isdigit() \
                or parts[3] != "type" or parts[4] not in TYPES:
            raise ValueError(f"team_shield tag 非法：{tag}")
        n, color = int(parts[2]), parts[4]
        if n <= 0:
            raise ValueError(f"team_shield 数值非法：{tag}")
        p.team_shield_n = n
        p.team_shield_type = color
        p.team_shield_until = self.turn_counts[self.current] + 2
        self._log("team_shield", {"player": self.current, "n": n,
                                  "type": color})
        return f"team shield {color} -{n}"

    def _trainer_shield_choose(self, p: PlayerState, tag: str,
                               action: dict) -> str:
        """``shield_choose:prevent_all[:from:<src>][:effects][:gate:<cond>]``

        卡面原型（Acerola's Mischief）：「You can use this card only if your
        opponent has 2 or fewer Prize cards remaining. Choose 1 of your Pokémon
        in play. During your opponent's next turn, prevent all damage from and
        effects of attacks done to that Pokémon by your opponent's Pokémon {ex}.」

        - 目标：`action["target"]` 位置码（0=战斗位，i≥1=后备 i-1），缺省战斗位；
        - 护盾沿用既有字段，到期在**持有者**下个回合开始时清除（= 覆盖对手的
          下一个回合）；
        - `:gate:` 前置条件不满足 → 直接抛错（合法动作表已提前过滤）。
        """
        spec = _parse_shield_choose_tag(tag)
        if not self._play_gate_ok(p, spec["gate"]):
            raise ValueError("不满足该卡的打出前置条件")
        fp = _field_at(p, int((action or {}).get("target", 0)))
        if fp is None:
            raise ValueError("护盾目标无效")
        fp.shield_prevent_all = True
        fp.shield_prevent_effects = bool(spec["effects"])
        fp.shield_prevent_qual = spec["qual"]
        self._log("shield", {"player": self.current, "target": fp.name,
                             "kind": "prevent_all", "qual": spec["qual"],
                             "effects": bool(spec["effects"])})
        return f"shield {fp.name}"

    def _trainer_discard_tool_or_senergy(self, p: PlayerState, tag: str,
                                         action: dict, kind: str = "") -> str:
        """「弃对手 1 只宝可梦身上的道具**或**特殊能量，**或**弃场上竞技场」

        卡面（Blowtorch PFL 86）是**二选一**。引擎没有交互选择层 → 缺省取
        「对手身上的道具 / 特殊能量」（对使用者总是不劣：拿掉对手的减伤或加速），
        只有在它不存在、或动作显式给了 ``mode="stadium"`` 时才弃竞技场。

        ⚠ 两支都必须实现。只做竞技场那一支，这张卡在「对手没有场地」的一半
        局面上打出去**什么都不发生**（静默削弱），而且对账脚本查不出来 ——
        它只核对已产出的 tag 与卡面是否一致，不核对「卡面有几句、产出了几条」。

        选择口径（确定性）：先战斗位后后备；同一只身上**先道具、再特殊能量**
        （道具是常驻修正、特殊能量是资源，先拿常驻修正对进攻方更有利）。
        """
        parts = str(tag).split(":")
        n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
        opp = self.players[1 - self.current]
        want_stadium = str((action or {}).get("mode") or "") == "stadium"
        done: list = []
        if not want_stadium:
            field = ([opp.active] if opp.active is not None else []) + list(opp.bench)
            for fp in field:
                if len(done) >= n:
                    break
                if self._trainer_poke_blocked(fp, kind):
                    continue
                if fp.tool:
                    name = fp.tool_name or fp.tool
                    ref = fp.tool_ref
                    _drop_tool(fp)
                    if ref is not None:
                        self._to_discard(opp, ref)
                    else:
                        # 手工装配 / 旧回放没有卡对象 → 至少保持文本层同步
                        opp.discard.append(str(name or "Pokémon Tool"))
                    self._log("discard_tool", {"player": self.current,
                                              "owner": opp.name,
                                              "target": fp.name, "tool": name})
                    done.append(f"tool:{name}")
                    continue
                if fp.senergy and _discard_special_energy(self, opp, fp, 1):
                    self._log("discard_energy", {"player": self.current,
                                                "target": fp.name,
                                                "kind": "special"})
                    done.append(f"senergy:{fp.name}")
        if not done:
            name = self._apply_discard_stadium()
            if name is None:
                raise ValueError("对手场上既无可弃道具/特殊能量，也无场地可弃")
            done.append(f"stadium:{name}")
        return "discard " + ", ".join(done)

    def _trainer_mill(self, tag: str) -> str:
        """磨牌（``mill:N[:opp]``）——默认磨自己（已存在于招式侧的同一语义）"""
        parts = tag.split(":")
        n = int(parts[1])
        victim = (1 - self.current
                  if len(parts) > 2 and parts[2] == "opp" else self.current)
        vp = self.players[victim]
        moved = 0
        for _ in range(n):
            if not vp.deck:
                break
            self._to_discard(vp, vp.deck.pop())
            moved += 1
        return f"mill {moved}"

    def _trainer_shuffle_top(self, tag: str) -> str:
        """训练家「检视牌库顶 N 张后重排」（``shuffle_top:N[:opp]``）

        第九十一批（2026-11-05）：该 tag 原先**只在招式侧**派发
        （``_apply_attack_effects``），训练家侧缺失 → Deduction Kit（SSP 171，
        `shuffle_top:3:own`）即便解析产出也进不了池。与 `energy_to_deck`
        属同一类陷阱：判定「引擎支持」不能只看 `battle.py` 里出现过一次。

        ⚠ **语义取舍（显式记录，不假装完整实现）**：Deduction Kit 卡面是
        「Look at the top 3 cards … put them back in any order, **or** shuffle
        them and put them on the bottom of your deck.」—— 第二个分支
        （洗回牌库底）**未建模**，按 choose 类「确定性默认」取第一分支
        （只重排顶部、不移动卡）。两者差异只在 deck 顺序（不涉手牌/弃牌/
        伤害/奖赏），但确实少了「把牌塞到牌库底」这一玩法。
        """
        parts = tag.split(":")
        n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
        victim = (1 - self.current
                  if len(parts) > 2 and parts[2] == "opp" else self.current)
        vp = self.players[victim]
        k = min(n, len(vp.deck))
        if k > 1:
            top = vp.deck[-k:]
            self.rng.shuffle(top)
            vp.deck[-k:] = top
        self._log("shuffle_top", {"player": victim, "n": k})
        return f"shuffle top {k}"

    def _trainer_evolve_from_deck(self, p: PlayerState, tag: str,
                                  action: Optional[dict] = None) -> str:
        """训练家「从牌库检索并当场进化」（``evolve_from_deck[:no_ability]``）

        第九十二批（2026-11-05）：Salvatore（TEF 160）。该 tag 此前**三处全缺**
        —— 只在 `ATTACK_TAG_PREFIXES`、只在 `_apply_attack_effects` 派发、
        `_do_trainer` 无分支 → 卡必然在池外。与前两批复盘的两处
        （``energy_to_deck`` / ``shuffle_top``）是同一类陷阱。

        目标选择：卡面「evolves from 1 of your Pokémon」→ choose 类
        **确定性默认**：按「战斗位 → 后备顺序」取第一个有合法进化源的目标。

        ``:no_ability`` = 卡面「a card that has no Abilities and evolves from …」，
        只允许检索无特性的进化卡。
        """
        no_ability = str(tag).endswith(":no_ability")
        target = None
        for fp in [p.active, *p.bench]:
            if fp is None or getattr(fp, "is_ko", False):
                continue
            if no_ability and not any(
                    isinstance(c, PokeRef) and c.pdef.stage > 0
                    and c.pdef.evolves_from == fp.pdef.name
                    and fp.pdef.stage + 1 == c.pdef.stage
                    and not c.pdef.abilities
                    for c in p.deck):
                continue
            target = fp
            break
        if target is None:
            raise ValueError("牌库无可进化目标")
        old = target.name
        self._evolve_from_deck(p, target, no_ability)
        return f"evolve {old} -> {target.name}"

    def _trainer_play_stadium(self, p: PlayerState, card, tag: str) -> str:
        """打出场馆：全局唯一，顶替旧场地（旧场地进弃牌堆）

        规则要点：场地是**双方共用**的，不归属打出方；顶替时旧场地送去**其拥有者**的
        弃牌堆（简化：统一记到当前玩家弃牌堆并在日志标注 owner，弃牌堆不参与胜负判定）。
        """
        old = self.stadium
        self.stadium = {"name": card.tdef.name, "tag": tag,
                        "owner": p.name}
        self._refresh_stadium_hp()
        detail = "stadium set"
        if old:
            # 旧场地进弃牌堆（简化：记到打出方弃牌堆；弃牌堆不参与胜负判定）
            detail = f"replace {old.get('name')}"
            self._log("stadium_replace", {"old": old.get("name"),
                                          "new": card.tdef.name,
                                          "by": p.name})
        self._log("stadium", {"name": card.tdef.name, "tag": tag,
                              "by": p.name})
        return detail

    def _trainer_play_fossil(self, p: PlayerState, card, tag: str) -> str:
        """化石卡（``fossil:<hp>:<ptype>``）：把训练家卡当**基础宝可梦**铺到后备

        卡面：「Play this card as if it were a N-HP Basic {C} Pokémon. This card
        can't be affected by any Special Conditions and can't retreat. At any time
        during your turn, you may discard this card from play.」

        引擎侧实现要点：
          · 上场后是基础宝可梦（stage 0、撤退费 0），可被「evolves_from = 卡名」
            的进化卡进化（进化后 `fossil` 标记随之清除）；
          · `fossil=True` 使**特殊状态施加**与**撤退**两道守卫拒绝它；
          · 弃置走 `discard_fossil` 动词（本卡**不进弃牌堆**，留在场上）；
          · 被击倒时按宝可梦结算奖赏卡，弃牌堆里放回**训练家卡对象**（`fossil_ref`）。
        """
        pdef = getattr(card.tdef, "fossil_pdef", None)
        if pdef is None:
            raise ValueError(f"化石卡缺少宝可梦定义：{card.tdef.name}")
        if not self._bench_has_room(p):
            raise ValueError("后备区已满")
        fp = make_pokemon(pdef)
        fp.fossil = True
        fp.fossil_ref = card
        fp.just_played = True
        p.bench.append(fp)
        self._log("play", {"player": self.current, "card": fp.name,
                           "fossil": True, "hp": fp.max_hp})
        self._on_bench_placed(self.current, fp)
        return f"fossil {fp.name} hp={fp.max_hp}"

    def _do_discard_fossil(self, p: PlayerState, action: dict) -> dict:
        """「At any time during your turn, you may discard this card from play.」

        战斗位的化石弃置后由后备顶上（与击倒/回手的补位语义一致）；
        战斗位且**无替补**时拒绝（不得把「场上无宝可梦」的判负强加给玩家）。
        """
        tpos = int(action.get("target", 0))
        fp = _field_at(p, tpos)
        if fp is None or not getattr(fp, "fossil", False):
            raise ValueError("目标不是场上的化石卡")
        if tpos == 0 and not p.bench:
            raise ValueError("战斗位没有替补，不能弃置化石")
        if tpos == 0:
            p.active = p.bench.pop(0)
            p.active.moved_active_turn = self.turn_number
            self._log("promote", {"player": self.current, "to": p.active.name})
        else:
            p.bench.remove(fp)
        ref = getattr(fp, "fossil_ref", None)
        self._to_discard(p, ref if ref is not None else PokeRef(fp.pdef))
        self._log("discard_fossil", {"player": self.current, "card": fp.name,
                                     "pos": tpos})
        return {"ok": True, "event": self.events[-1]}

    def _trainer_attach_tool(self, p: PlayerState, card, tag: str,
                             target: int) -> str:
        """把道具附着到己方 1 只宝可梦（每只最多 1 张，S2）

        目标位置码与 `attach.target` 一致：0 = 战斗位，i+1 = 后备 i。
        """
        if target == 0:
            if p.active is None:
                raise ValueError("无 active，无法附着道具")
            fp = p.active
        else:
            bi = target - 1
            if bi >= len(p.bench):
                raise ValueError("道具目标越界")
            fp = p.bench[bi]
        if fp.tool:
            raise ValueError(f"{fp.name} 已附着道具（每只最多 1 张）")
        fp.tool = tag
        fp.tool_name = getattr(card.tdef, "display", "") or card.tdef.name
        fp.tool_ref = card
        # Jamming Tower 在场时，**新附着**的道具同样立即失效
        fp.tool_disabled = self._stadium_meta()["kind"] == "no_tool_effect"
        self._log("attach_tool", {"player": self.current, "target": fp.name,
                                  "tool": fp.tool_name, "tag": tag})
        return f"attach tool {fp.tool_name} -> {fp.name}"

    def _search_bench(self, p: PlayerState, tag: str) -> str:
        """从牌库检索 N 张宝可梦**直接铺到后备**（``search_bench:N[:basic][:type:<c>]``）

        卡面：「Search your deck for up to N Basic Pokémon and **put them onto your
        Bench**」——去向是**后备**，不是手牌。此前一律映射成 ``search_pokemon:N``
        （拿进手牌），于是「少铺一只、手里多一张」，局面上不报错、也看不出来。

        后备已满 / 牌库无合法目标时**不执行**（官方规则：招式照常可用）。
        """
        parts = tag.split(":")
        raw_n = parts[1] if len(parts) > 1 else "1"
        if raw_n == "all":
            # 第七十九批：Precious Trolley「any number of Basic Pokémon」
            # —— 备选集合与后备空位共同封死上限，不需要交互层。
            n = 999
        elif raw_n.isdigit():
            n = int(raw_n)
        else:
            raise ValueError(f"search_bench 张数非法：{tag}")
        basic_only = "basic" in parts[2:]
        ptype = None
        names = None
        name_has = None
        for i, x in enumerate(parts):
            if x == "type" and i + 1 < len(parts):
                ptype = parts[i + 1].lower()
            if x == "name" and i + 1 < len(parts):
                # 指定卡名（可 `+` 并联，如 "Silcoon or a Cascoon"）
                names = [s for s in parts[i + 1].split("+") if s]
            if x == "name_has" and i + 1 < len(parts):
                # 「Pokémon that have "Rotom" in their name」/「Basic Lillie's
                # Pokémon」/「Pokémon that have "Koffing" in their name」
                # （第一百/一百零一批）—— **子串**匹配，不是精确名。
                name_has = parts[i + 1]
        placed = 0
        for i in reversed(range(len(p.deck))):
            if placed >= n or not self._bench_has_room(p):
                break
            c = p.deck[i]
            if not isinstance(c, PokeRef):
                continue
            if basic_only and c.pdef.stage != 0:
                continue
            if ptype and str(c.pdef.ptype).lower() != ptype:
                continue
            if names and _name_key(c.pdef.name) not in names:
                continue
            if name_has and name_has not in _name_key(c.pdef.name):
                continue
            fp = make_pokemon(p.deck.pop(i).pdef)
            fp.just_played = True
            p.bench.append(fp)
            self._on_bench_placed(self.current, fp)
            placed += 1
        self.rng.shuffle(p.deck)
        return f"bench {placed}"

    def _trainer_poffin(self, p: PlayerState) -> str:
        """检索牌库 2 张基础宝可梦放入后备并洗牌"""
        found = 0
        for i in reversed(range(len(p.deck))):
            if found >= 2 or not self._bench_has_room(p):
                break
            if isinstance(p.deck[i], PokeRef) and p.deck[i].pdef.stage == 0:
                fp = make_pokemon(p.deck.pop(i).pdef)
                fp.just_played = True
                p.bench.append(fp)
                found += 1
        self.rng.shuffle(p.deck)   # 检索后洗牌
        return f"put {found} basic to bench"

    def _trainer_ultra_ball(self, p: PlayerState, self_hi: int) -> str:
        """弃手牌 2 张（不含自身）→ 检索任意宝可梦入手牌"""
        if len(p.hand) < 3:
            raise ValueError("手牌不足（需弃 2）")
        # 从末尾丢弃 2 张（跳过自身所在位置）
        removed = 0
        for j in range(len(p.hand) - 1, -1, -1):
            if removed >= 2:
                break
            if j == self_hi:
                continue
            self._to_discard(p, p.hand[j])
            p.hand.pop(j)
            removed += 1
            if j < self_hi:
                self_hi -= 1
        if removed < 2:
            raise ValueError("手牌不足（需弃 2）")
        # 检索：从牌库底向顶取第一张宝可梦 → 入手
        found = None
        for i in range(len(p.deck) - 1, -1, -1):
            if isinstance(p.deck[i], PokeRef):
                found = p.deck.pop(i)
                break
        self.rng.shuffle(p.deck)
        if found is None:
            return "search failed (no pokemon in deck)"
        p.hand.append(found)
        return f"search {found.pdef.name} to hand"

    def _trainer_hammer(self, opp: PlayerState, kind: str = "") -> str:
        """抛币正面 → 弃对手 active 1 能量（S4：效果免疫者不结算）"""
        if opp.active is None or opp.active.total_energy() <= 0:
            raise ValueError("对手 active 无能量")
        if self._trainer_poke_blocked(opp.active, kind):
            return "blocked: effect_immune"
        if self.rng.random() < 0.5:
            opp.active.remove_any(1)
            return "heads: discard 1 energy"
        return "tails: no effect"

    def _trainer_status_multi(self, opp: PlayerState, tag: str,
                              kind: str = "") -> str:
        """训练家多状态（``status_multi:<a>+<b>``，第五十一批 · Dangerous Laser）

        卡面「Your opponent's Active Pokémon is now Burned **and** Confused.」——
        此前只落第一个状态，第二个被**静默丢掉**（卡能打、测试不红）。
        免疫判定与招式侧同口径（`_status_blocked` / 化石卡 / Festival Grounds）。
        """
        parts = str(tag).split(":", 1)[1].split("+")
        applied = []
        if opp.active is None or opp.active.is_ko:
            return ""
        # 逐状态判定（第一百一十一批）：被动免疫可能**只针对某一个状态**
        # （Hoothoot 只免疫睡眠）。整条拦下会把「灼伤 + 睡眠」一起丢掉，
        # 反过来整条放行又会把睡眠静默施加到免疫者身上 —— 两种都不行。
        # S4：道具/支援者卡的**整个效果**被免疫时不施加任何状态
        if self._trainer_poke_blocked(opp.active, kind):
            return "no effect"
        _blocked = {st for st in parts if self._status_blocked(opp.active, st)}
        if _blocked and len(_blocked) == len(parts):
            self._log("blocked", {"target": opp.active.name,
                                  "by": "status_immune"})
            return "no effect"
        for st in parts:
            if st not in STATUSES:
                raise ValueError(f"未知状态：{st}")
            if st in _blocked:
                self._log("blocked", {"target": opp.active.name,
                                      "by": "status_immune", "status": st})
                continue
            opp.active.status.add(st)
            applied.append(st)
        self._log("status", {"player": 1 - self.current,
                             "target": opp.active.name,
                             "status": "+".join(applied)})
        return f"status {'+'.join(applied)}"

    def _trainer_boss(self, opp: PlayerState, bi: int, kind: str = "") -> str:
        """将对手 1 只后备拉为 active（换位）

        S4：被拉的那只若免疫「道具/支援者卡效果」（Unnerve / Protective Sail），
        这次换位不成立 —— 卡面说的是「**that card** 的所有效果」，拉人也是效果。
        """
        if not opp.bench or bi >= len(opp.bench):
            raise ValueError("对手无可拉后备")
        if self._trainer_poke_blocked(opp.bench[bi], kind):
            return "blocked: effect_immune"
        new_active = opp.bench.pop(bi)
        if opp.active is not None:
            opp.active.clear_active_locks()
            opp.bench.append(opp.active)
        opp.active = new_active
        new_active.moved_active_turn = self.turn_number
        return f"drag {new_active.name} to active"

    # --- v1.3 训练家行为（B1 预设卡组关键卡） ---
    def _trainer_switch(self, p: PlayerState, bi: int) -> str:
        """自换位（Switch）：active ↔ 后备 bi。不消耗撤退能量、不受睡眠/麻痹限制"""
        if p.active is None or not p.bench:
            raise ValueError("无法换位（无 active 或后备）")
        if not (0 <= bi < len(p.bench)):
            raise ValueError("换位目标越界")
        old = p.active
        old.clear_status()               # 官方规则：换下即解除特殊状态
        old.clear_active_locks()
        p.active = p.bench.pop(bi)
        p.bench.append(old)
        p.active.moved_active_turn = self.turn_number
        return f"switch {old.name} -> {p.active.name}"

    def _trainer_recover(self, p: PlayerState, tag: str,
                         di: int) -> str:
        """弃牌堆回收（Night Stretcher）：取 1 张入手（仅弃牌堆**顶部**可选）

        B3：``recover:heads:N``（Stoutland）数量由硬币决定 → 无目标可选，
        按正面枚数从弃牌堆取回（不足则取空的张数）。
        """
        parts = tag.split(":")
        if len(parts) > 2 and parts[1] == "heads":
            flips = int(parts[2]) if parts[2].isdigit() else 0
            heads = sum(1 for _ in range(flips) if self.rng.random() < 0.5)
            self._log("coin", {"player": self.current,
                               "result": f"{heads}/{flips} heads", "tag": tag})
            cands = _recover_candidates(p, f"recover:{heads}:any")
            got = _recover_to_hand(self, p, cands) if cands else 0
            return f"recover {got} (heads {heads})"
        cands = _recover_candidates(p, tag)
        if not cands:
            raise ValueError("弃牌堆无可回收的卡")
        if di not in cands:
            di = cands[0]
        ref = self._rebuild_ref(p.discard_refs.pop(di))
        # 文本层同步移除（与类型化层保持同序）
        label = _card_label(ref)
        for k in range(min(len(p.discard), di + 1) - 1, -1, -1):
            if p.discard[k] == label:
                p.discard.pop(k)
                break
        p.hand.append(ref)
        return f"recover {label} to hand"

    def _trainer_discard_search_energy(self, p: PlayerState, tag: str,
                                       self_hi: int) -> str:
        """弃 1 张手牌（不含自身）→ 从牌库检索 n 张能量入手（Earthen Vessel）"""
        parts = tag.split(":")
        n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 2
        if len(p.hand) < 2:
            raise ValueError("手牌不足（需弃 1）")
        other = next((i for i in range(len(p.hand) - 1, -1, -1)
                      if i != self_hi), None)
        if other is None:
            raise ValueError("手牌不足（需弃 1）")
        dropped = _card_label(p.hand[other])
        self._to_discard(p, p.hand.pop(other))
        if other < self_hi:
            self_hi -= 1
        found = _take_energy_from_deck(p, n, None)
        if found:
            self.rng.shuffle(p.deck)
        return f"discard {dropped}, search {found} energy"

    def _trainer_search_energy(self, p: PlayerState, tag: str) -> str:
        """从牌库检索 n 张能量入手（Crispin 的检索侧子集）

        B2 剩余（2026-10-28）：支持 ``search_energy:N:type:<c>`` —— 卡面
        「Search your deck for up to 2 Basic {P} Energy cards」（Aromatisse）/
        「up to 7 Basic {R} Energy cards」（Firebreather）必须按**属性**筛选，
        否则会把其它颜色的能量一并拿到手（静默超范围）。

        ``search_energy:all[:distinct]``（2026-11-02 第四十批）：卡面「any number
        of Basic Energy cards **of different types**」（Energy Search Pro SSP 176）
        —— 数量是自由选择，但备选集合被「不同属性」封死，上限 = 牌库里出现的基本
        能量属性种类数，因此**不需要交互层**，确定性默认「取满」就是卡面最大值。
        ⚠ 不能退化成常数 N：牌库只有 2 种属性时 N=3 拿不满、N=8 又拿不到。
        """
        parts = tag.split(":")
        _raw = parts[1] if len(parts) > 1 else ""
        n = 999 if _raw == "all" else (int(_raw) if _raw.isdigit() else 2)
        etype = (parts[3].lower()
                 if len(parts) > 3 and parts[2] == "type" else None)
        # 「of different types」：属性两两不同（Eevee SFA 50，第三十九批）
        distinct = "distinct" in parts[2:]
        found = _take_energy_from_deck(p, n, etype, distinct=distinct)
        if not found:
            # 官方规则：「Search your deck for **up to** N …」——找不到也**合法**，
            # 只是没拿到卡。此前直接抛错会让「可支付但牌库无能量」的招式在
            # `legal_actions` 里成为合法动作、step 却失败（2026-10-28 随机对局实测）。
            self._log("search_energy", {"player": self.current, "n": 0,
                                        "etype": etype, "reason": "无目标"})
            return "search 0 energy to hand"
        self.rng.shuffle(p.deck)
        return f"search {found} energy to hand"

    def _trainer_search_kind(self, p: PlayerState, tag: str) -> str:
        """通用检索（search_pokemon:N / search_trainer:N / search_any:N）

        从牌库检索 n 张指定类别卡入手并洗牌（官方规则；引擎统一约定）。

        ``n`` 支持 ``<固定数>`` 或 ``<固定数>:per:<counter>``（B2 剩余 · 2026-10-28）：
        「Search your deck for a number of cards up to the number of your Benched
        Pokémon」（Scrafty）→ 张数由计数源决定，必须**精确求值**而不是取固定 1。
        """
        n, _cond = _draw_spec(self, tag)
        if n <= 0:
            # 计数源求值为 0（如「至多后备数」而后备为空）→ 合法但拿不到卡
            self._log("search", {"player": self.current, "n": 0,
                                 "kind": _search_kind_of(tag), "reason": "n=0"})
            return f"search 0 {_search_kind_of(tag)} to hand"
        flt = _search_filter(tag)
        # 「any number of Fennel cards」（第一百批，Musharna「Dream Calling」）：
        # 按**卡名子串**筛选。不筛就变成「任意 N 张训练家」——静默放宽范围。
        _nh = None
        _tp = str(tag).split(":")
        if "name_has" in _tp:
            _nh = _tp[_tp.index("name_has") + 1]
        if _nh:
            flt = dict(flt)
            flt["name_has"] = _nh
        found = _take_kind_from_deck(p, n, _search_kind_of(tag), flt)
        if not found:
            # 同上：「up to N」失败也算合法（招式/特性照常结算）
            self._log("search", {"player": self.current, "n": 0,
                                 "kind": _search_kind_of(tag), "reason": "无目标"})
            return f"search 0 {_search_kind_of(tag)} to hand"
        self.rng.shuffle(p.deck)
        return f"search {found} {_search_kind_of(tag)} to hand"

    def _search_top(self, p: PlayerState, tag: str) -> str:
        """检索 N 张 → 洗牌 → **放回牌库顶**（第七十九批）

        卡面：「Search your deck for 2 cards, shuffle your deck, then put those
        cards on top of it in any order.」（Ciphermaniac's Codebreaking / Dialga
        「Time Manipulation」）。去向是牌库顶，**不是手牌** —— 拿进手牌会凭空
        多 N 张手牌，是静默加强。

        选哪 N 张：引擎无交互层，确定性取**当前牌库顶 N 张**（与 `recon` 的
        选牌口径一致）；洗牌后按原选取顺序放回顶部（卡面允许任意顺序）。
        """
        n = int(tag.split(":")[1])
        take = min(n, len(p.deck))
        picked = [p.deck.pop() for _ in range(take)]   # picked[0] = 原牌库顶
        self.rng.shuffle(p.deck)
        # 放回顶部：列表末尾是牌库顶 → 逆序 extend 后 picked[0] 在最顶。
        p.deck.extend(reversed(picked))
        return f"search {take} to deck top"

    def _search_multi(self, p: PlayerState, tag: str) -> str:
        """逐类别各取 K 张入手（第七十九批）

        卡面：「Search your deck for an Item card, a Pokémon Tool card, a
        Supporter card, and a Stadium card, reveal them, and put them into your
        hand.」（Secret Box）——四个类别**各 1 张**；不能退化成「任意类别共 1
        张」（通用 `search_trainer:1` 的口径会少拿 3 张，是静默削弱）。

        tag 形态 ``search_multi:<sel1>+<sel2>…:<k>``；筛选项语义复用
        `_recon_one`（item/tool/supporter/stadium/energy/trainer/pokemon），
        未知筛选项抛错（不猜、不静默放行）。
        """
        parts = tag.split(":")
        if len(parts) < 3 or not parts[2].isdigit():
            raise ValueError(f"search_multi tag 字段非法：{tag}")
        alts = [a for a in parts[1].split("+") if a]
        k = int(parts[2])
        picked_idx: list = []
        seen: set = set()
        for alt in alts:
            got = 0
            for i in range(len(p.deck) - 1, -1, -1):
                if got >= k:
                    break
                if i in seen:
                    continue
                if _recon_one(p.deck[i], alt):
                    picked_idx.append(i)
                    seen.add(i)
                    got += 1
        picked = [p.deck.pop(i) for i in sorted(picked_idx, reverse=True)]
        p.hand.extend(picked)
        self.rng.shuffle(p.deck)
        return f"search {len(picked)} ({'+'.join(alts)}) to hand"

    def _trainer_candy(self, p: PlayerState, action: dict) -> str:
        """跳阶进化（Rare Candy）：手牌二阶卡直接放置到链根基础宝可梦上"""
        cands = _candy_legals(p)
        if not cands:
            raise ValueError("无可用跳阶进化目标")
        want_hand = action.get("hand_index")
        want_pos = action.get("target")
        pick = None
        for hi, tpos, nm in cands:
            if want_hand is not None and int(want_hand) != hi:
                continue
            if want_pos is not None and int(want_pos) != tpos:
                continue
            pick = (hi, tpos, nm)
            break
        if pick is None:
            pick = cands[0]
        hi, tpos, nm = pick
        c = p.hand[hi]
        tgt = _field_at(p, tpos)
        if tgt is None:
            raise ValueError("跳阶进化目标为空")
        old = tgt.name
        p.hand.pop(hi)
        tgt.evolve_to(c.pdef)
        tgt.evolved_turn = self.turn_number
        tgt.evolved_from_name = old
        self._log("evolve", {"player": self.current, "from": old,
                             "to": tgt.name, "pos": tpos, "candy": True})
        self._trigger_evolve_attach(tgt)
        return f"rare candy: {old} -> {tgt.name}"

    # ---------- 攻击 ----------
    def _can_pay_attack(self, fp: FieldPokemon, atk) -> bool:
        """招式费用校验（含效果族的费用改写：`attack_cost`）"""
        mod = families.attack_cost(self, fp, atk)
        if mod.get("ignore_all"):
            return True
        if mod.get("set") is not None:
            # 费用**整体改写**（第一百一十二批）：引擎攻击不消耗能量
            # （见文件头「攻击不弃能」），只在校验处改写即可，不会出现
            # 「校验按新费用、支付按旧费用」的不一致。
            return fp.can_pay(tuple(mod["set"]))
        cost = _cheaper_cost(atk.cost, _attack_cost_delta(self, fp))
        if mod.get("omit"):
            cost = tuple((t, n) for t, n in cost if t != mod["omit"])
        delta = int(mod.get("colorless") or 0)
        # 「During your opponent's next turn, attacks used by the Defending Pokémon
        # cost {C} more.」（Rillaboom）—— 按持有者回合计数到期
        if fp.attack_cost_up_n:
            owner = next((i for i, pl in enumerate(self.players)
                          if pl.active is fp), None)
            if owner is not None \
                    and fp.attack_cost_up_until > self.turn_counts[owner]:
                delta += fp.attack_cost_up_n
        if delta:
            # 费用抬高的增量**必须能新增一条无色需求**：招式费用全是有色时
            # （如 `[('fire',1)]`），旧写法只对已存在的无色项加值，等于把
            # 「+1 无色」静默吃掉（fx:attack_cost 族实测踩到）。
            merged: dict = {}
            for t, n in cost:
                merged[t] = merged.get(t, 0) + n
            merged[COLORLESS] = merged.get(COLORLESS, 0) + delta
            cost = tuple((t, n) for t, n in merged.items())
        cost = tuple(x for x in cost if x[1] > 0)
        return fp.can_pay(cost)

    def _do_attack(self, p: PlayerState, action: dict) -> dict:
        if p.active is None:
            raise ValueError("无 active")
        if self.cfg.first_turn_no_attack and self._opening_turn():
            # 例外卡（「If you go first, you can use this attack during your
            # first turn.」）不受限；其余整只拦下（官方）。
            _ai0 = _attack_index_from_action(p.active.pdef.attacks, action)
            if _ai0 is None or _ai0 >= len(p.active.pdef.attacks) \
                    or not _first_turn_attack_ok(p.active.pdef.attacks[_ai0]):
                raise ValueError("先手第一回合不可攻击")
        if _NO_ATTACK_STATUS & p.active.status:
            raise ValueError("睡眠/麻痹中禁止攻击")
        # 攻击次数额度（第六十一批）：Festival Lead 让这一招能在一回合里用两次，
        # 额度用完后必须挡住 —— 否则「续攻」会变成「无限攻」。
        if p.attacks_this_turn >= self._attack_allowance(p.active):
            raise ValueError("本回合已用完攻击次数")
        ai = _attack_index_from_action(p.active.pdef.attacks, action)
        if ai is None:
            known = "、".join(
                (a.name_zh or a.name) for a in p.active.pdef.attacks)
            raise ValueError(
                f"无法识别招式「{_name_hint(action)}」（可用：{known}）")
        if ai >= len(p.active.pdef.attacks):
            raise ValueError("招式越界")
        atk = p.active.pdef.attacks[ai]
        if p.active.attack_locked(atk.name, self.turn_counts[self.current]):
            raise ValueError("该宝可梦当前不能使用此招式")
        # 「if the Defending Pokémon tries to use an attack, … flips a coin. If
        # tails, that attack doesn't happen.」（Sand Attack 家族）：每次尝试抛币，
        # 反面前功尽弃（不消耗能量、不结束回合，可再做别的动作）
        if p.active.attack_denial_until > self.turn_counts[self.current]:
            heads = self.rng.random() < 0.5
            self._log("attack_denial_flip", {"player": self.current,
                                             "target": p.active.name,
                                             "heads": heads})
            if not heads:
                self._log("attack_denied", {"player": self.current,
                                            "target": p.active.name})
                return {"ok": True, "event": self.events[-1]}
        # 借用招式（2026-10-28）：`borrow_attack:<scope>` / Relicanth 的进化前招式。
        # 费用的支付方**取决于来源**：复制招式付本招式的费用；进化前招式付它自己的。
        # S5（2026-11-08）：**抛币门**必须先抛 —— 卡面「Flip a coin. **If heads**,
        # choose 1 of … attacks and use it as this attack.」：反面时 action 里带的
        # `borrow` 整条作废，本招按印刷效果结算（0 伤害、无附加）。
        # ⚠ 不能把币留给 `_attack_mods`：那里抛币发生在借用解析**之后**，而且
        #   `_apply_coin_non_damage` 只消费 `pre_mods` 里已记录的币 → 会静默变成
        #   「一定借用」（= 静默加强）。
        _gate_tag = _borrow_coin_gate(atk)
        _gate_ok = True
        if _gate_tag is not None:
            _gres = "heads" if self.rng.random() < 0.5 else "tails"
            self._log("coin", {"player": self.current, "result": _gres,
                               "tag": _gate_tag, "borrow_gate": True})
            _gate_ok = _gres == "heads"
            if not _gate_ok:
                self._log("borrow_skipped", {"player": self.current,
                                             "attack": atk.name,
                                             "reason": "抛币反面"})
            elif not action.get("borrow"):
                # 正面就必须真的借 —— 引擎不得替玩家挑一个招式（S1 不变量）
                raise ValueError(f"{atk.name}：抛币正面后必须指定借用的招式")
        borrow = (self._resolve_borrow(p, self.players[1 - self.current],
                                       p.active, atk, action)
                  if _gate_ok else None)
        pay_atk = borrow["pay"] if borrow else atk
        if borrow:
            atk = borrow["atk"]
        # S5（2026-11-08）：`opp_deck_top:N` 借用 —— 先记「翻开了哪 N 张」
        # （卡面明文 Reveal，双方可见），**洗回**要等招式结算完再做（见下方），
        # 否则复制的招式看牌库时会看到「已经洗过」的另一副牌。
        _base_scope = _borrow_scope(p.active.pdef.attacks[ai])
        _deck_top_n = _borrow_deck_top_n(_base_scope)
        if _deck_top_n:
            _ropp = self.players[1 - self.current]
            _rtop = list(reversed(list(_ropp.deck[-_deck_top_n:])))
            self._log("reveal", {"player": 1 - self.current, "from": "deck_top",
                                 "n": len(_rtop),
                                 "cards": [_card_label(x) for x in _rtop],
                                 "via": p.active.pdef.attacks[ai].name})
        if not self._can_pay_attack(p.active, pay_atk):
            raise ValueError("能量不足（无法支付招式费用）")
        attacker = p.active
        _log = {"player": self.current, "attack": atk.name,
                "from": attacker.name, "cost": dict(pay_atk.cost)}
        if borrow:
            _log["via"] = p.active.pdef.attacks[ai].name
            _log["borrow_src"] = borrow["src"]
        self._log("attack", _log)
        # 攻击额度记账（第六十一批）：放在「真的打出去了」之后 —— 前面的
        # 能量不足 / 目标无效都会抛错，那些不该消耗额度。
        p.attacks_this_turn += 1
        # 回合历史（2026-10-28）：「during your last turn」类条件需要知道用过哪些招
        p.turn_attacks.append(atk.name)
        if str(attacker.pdef.category or "") == "Ancient":
            p.turn_ancient_attackers.append(attacker.name)
        confused = "confuse" in attacker.status
        if confused and self.rng.random() < 0.5:
            self._log("confuse_tails", {"player": self.current,
                                        "target": attacker.name})
            attacker.damage += confusion_self_damage(attacker)
            if attacker.is_ko:
                self._resolve_ko(self.current, attacker)
            if self.finished:
                return {"ok": True, "event": None}
            self._end_turn()
            return {"ok": True, "event": None}
        if confused:
            attacker.status.discard("confuse")
            self._log("confuse_heads", {"player": self.current})
        defender = self.players[1 - self.current].active
        if defender is None:
            self._finish(self.current, "no_pokemon", {"loser": 1 - self.current})
            return {"ok": True, "event": None}
        # 伤害前阶段（2026-10-29 第二十五批）：「Before doing damage, …」必须在
        # **伤害修正与伤害结算之前**执行 —— 守方道具（减伤/免疫）此时还挂着，
        # 若放到 `_apply_attack_effects`（伤害之后）就白弃了，属静默少做。
        # 同时记录本次真的弃掉几张道具（`:if_pre_tool` 条件要用）。
        self._pre_tools_discarded = 0
        for _tag in atk.effects or ():
            if str(_tag).startswith("pre:"):
                self._apply_pre_attack_effect(str(_tag)[4:])
        # v1.4 伤害修正层：抛币（coin:）与条件/计数缩放（dmg_plus:）必须先于伤害结算，
        # 因为它们会改写基础伤害；`dmg_zero` 则直接把本次伤害归零。
        # 让「额外能量」条件能取到**本次招式**的费用（Electivire ex /
        # Jellicent ex / Tangrowth「in addition to this attack's cost」）。
        _prev_atk = self._resolving_attack
        self._resolving_attack = atk
        try:
            mods = self._attack_mods(attacker, atk.effects, action, defender, atk)
        finally:
            self._resolving_attack = _prev_atk
        # `set` 非 None → 基础伤害按卡面「does N damage for each X」重算；
        # 否则用印刷伤害。`plus`（固定/条件/静态加伤）始终在基础值上叠加。
        eff_base = atk.damage if mods["set"] is None else mods["set"]
        # 场地加伤（Postwick）：卡面写「before applying Weakness and Resistance」
        # → 直接加在基础伤害上，与 W/R 无关。
        stad_bonus = 0 if mods["zero"] else self._stadium_attack_bonus(attacker)
        base = 0 if mods["zero"] else max(
            0, eff_base + stad_bonus + mods["plus"] - mods.get("minus", 0))
        # 「被招式击倒」标记：仅本段内（含招式附加效果）的击倒计入
        prev_ko_flag = self._ko_from_attack
        self._ko_from_attack = True
        # S2 第二阶段：Survival Brace 的前置条件是「受伤前满 HP」，必须在加伤害前取快照
        was_full = defender.damage == 0
        damage_dealt = 0
        try:
            if base > 0:
                # 「This attack's damage isn't affected by any effects on your
                # opponent's Active Pokémon.」（Koraidon「Shred」）—— 无视的是
                # **效果**（护盾/减伤/道具/场地），不是弱点抗性（那印在卡上）
                # 第六十五批：`ignore_effects:wr` 是**宽口径** —— 卡面多写了
                # 「Weakness or Resistance」，连印在卡上的弱抗也一起无视。
                # 两者不能混用：用窄口径接宽句式会静默漏掉弱抗那一半。
                ignore_eff, ignore_wr = _ignore_effects_flags(atk.effects)
                # 静态修正（S1）：`passive:no_weakness` 让弱点不生效；
                # 带来源属性的减伤需要攻击方，才能按卡面限定结算
                static = _static_mods(defender, attacker)
                dmg = compute_damage(attacker, defender, base,
                                     weakness_override=self._weakness_override(
                                         attacker, defender),
                                     ignore_weakness=(mods["ignore_weakness"]
                                                      or static["no_weakness"]
                                                      # S2b：**限时**无弱点
                                                      # （`shield:no_weakness`）
                                                      or defender.shield_no_weakness
                                                      or ignore_wr),
                                     ignore_resistance=(mods["ignore_resistance"]
                                                        or ignore_wr))
                # 「During your next turn, the Defending Pokémon takes N more
                # damage from attacks (**after** applying Weakness and
                # Resistance).」（Vibrava / Stunfisk）—— 在弱抗之后、其它减伤与
                # 护盾之前叠加。只在**施加者自己的下个回合**内生效：
                # 到期按施加者的 turn_counts 比较，且当前回合方必须是施加者。
                if defender.dmg_taken_plus_next_n \
                        and defender.dmg_taken_plus_next_owner == self.current \
                        and defender.dmg_taken_plus_next_until > \
                        self.turn_counts[self.current]:
                    dmg += defender.dmg_taken_plus_next_n
                    self._log("dmg_taken_plus_next_applied", {
                        "target": defender.name,
                        "n": defender.dmg_taken_plus_next_n})
                # 静态免伤（2026-10-28 第八批）：卡面「Prevent all damage … by
                # attacks from <来源>」是**常驻**被动，不是护盾；按弱抗后的伤害
                # 判定门槛（`if that damage is N or more`）。「无视效果」的招式
                # 同样无视这条（它是守方身上的效果）。
                imm = (None if ignore_eff
                       else _self_immune(defender, attacker, dmg))
                # 场地免伤（Neutralization Zone）——同样是守方身上的效果
                if imm is None and not ignore_eff \
                        and self._stadium_immune(defender, attacker):
                    imm = "stadium:dmg_immune"
                # 卡面写「after applying Weakness and Resistance」→ 静态减伤在弱抗之后
                if static["dmg_reduce"] and not ignore_eff:
                    dmg = max(0, dmg - static["dmg_reduce"])
                # 全队限时减伤（第四十六批，Iron Defender）：玩家级，同样在弱抗之后
                if not ignore_eff:
                    ts_red = self._team_shield_reduce(defender, attacker)
                    if ts_red:
                        dmg = max(0, dmg - ts_red)
                        self._log("team_shield_reduce", {
                            "target": defender.name, "n": ts_red})
                # 全队常驻减伤（2026-10-29 第十九批）：效果源在**守方阵营**
                # 任意一只身上（Curly Wall / Gear Coating / Stone Palace），
                # 因此不能挂在受击者自己的 passive 上；同口径在弱抗之后。
                if not ignore_eff:
                    team_red = families.static_dmg_minus(self, defender, attacker)
                    if team_red:
                        dmg = max(0, dmg - team_red)
                tool_red = 0 if ignore_eff else self._tool_reduce_vs(
                    defender, attacker)
                if tool_red:
                    dmg = max(0, dmg - tool_red)
                stad_red = 0 if ignore_eff else self._stadium_damage_reduce(
                    defender)
                if stad_red:
                    dmg = max(0, dmg - stad_red)
                shield_block = None
                if not ignore_eff:
                    shield_block = _shield_blocks(defender, attacker, dmg)
                    dmg = apply_shield(defender, dmg,  # 护盾在弱抗之后
                                       block=shield_block)
                # 抛币免伤被动（第四十五批，Fezandipiti）：被招式伤害时抛一枚，
                # 正面则本次伤害归零。必须在**伤害结算内**（事后无法撤销）。
                if dmg > 0 and not ignore_eff and self._coin_prevent_of(defender):
                    dmg = 0
                    self._log("blocked", {"target": defender.name,
                                          "by": "coin_prevent"})
                if imm:
                    dmg = 0
                    self._log("blocked", {"target": defender.name,
                                          "by": imm})
                damage_dealt = dmg or 0
                if dmg:
                    defender.damage += dmg
                    # 受击历史（第九十九批）：只记**招式**造成的伤害
                    # （自伤/指示物走别的通道，不计入「damaged by an attack」）。
                    defender.dmg_taken_mark += dmg
                self._log("damage", {"target": defender.name,
                                     "player": 1 - self.current,
                                     "dmg": dmg, "base": eff_base,
                                     "printed": atk.damage,
                                     "bonus": mods["plus"],
                                     "stadium_bonus": stad_bonus,
                                     "static_reduce": static["dmg_reduce"],
                                     "blocked": bool(eff_base and dmg == 0)})
            elif atk.damage > 0 or mods["set"] is not None:
                self._log("damage", {"target": defender.name,
                                     "player": 1 - self.current,
                                     "dmg": 0, "base": eff_base,
                                     "printed": atk.damage,
                                     "bonus": mods["plus"], "nullified": True})
            # 「Heal from this Pokémon the same amount of damage you did …」
            # （第一百二十批，Virizion「Giga Drain」）—— 效果在伤害之后执行，
            # 因此把本次实际伤害记在这里供 `heal:equal` 取用。
            self._last_damage_dealt = damage_dealt
            # S2d：记下「本招结算前两方弃牌堆的水位」，招式效果跑完后取切片 ——
            # 「被本招式效果弃置的卡」由此得到（Boomerang Energy 的回收条件）。
            # 用差分而不是在每个弃置点埋钩子：弃置路径有十几处，埋钩必漏。
            _disc_before = [(len(self.players[k].discard_refs)) for k in (0, 1)]
            if not mods.get("nullify_all"):
                self._apply_attack_effects(attacker, atk.effects,
                                           plan=action.get("targets"),
                                           action=action, pre_mods=mods)
            # S5：`opp_deck_top:N` 借用的收尾 ——「Shuffle the revealed cards into
            # your opponent's deck.」翻开的牌从未离开牌库，等价于洗整副。
            # 放在效果之后是卡面顺序（reveal → choose → use → shuffle）。
            if _deck_top_n:
                self.rng.shuffle(_ropp.deck)
                self._log("deck_shuffle", {"player": 1 - self.current,
                                           "n": len(_ropp.deck),
                                           "reason": "borrow_deck_top"})
            # S2 第二阶段：道具触发钩子（卡面写 even if Knocked Out → 先于击倒结算）
            self._trigger_tool_on_damaged(defender, attacker, damage_dealt)
            # 2026-10-27：受击反应**特性**（`on_damaged:*`），同一时点
            self._trigger_ability_on_damaged(defender, attacker, damage_dealt)
            # 2026-10-28：受击反应**特殊能量**（Spiky Energy）
            self._trigger_energy_on_damaged(defender, attacker, damage_dealt)
            # 2026-10-29 第二十九批：下回合「受击反制」（Bouffalant / Zamazenta）
            self._trigger_retaliate(defender, attacker, damage_dealt)
            # 免击倒优先级：道具 → 特性（同一击只生效一次，`or` 短路保证）。
            # 两者都要求「受伤前满 HP」；抛币版不要求满 HP，故单独一档。
            if defender.is_ko and was_full and (
                    self._tool_survive(defender)
                    or self._ability_survive(defender)):   # 第八十批
                pass                      # 免击倒：伤害已改为「剩 M 点」
            elif defender.is_ko and self._ability_coin_survive(defender):
                pass                      # 抛币免击倒（第七十八批，Mega Hawlucha ex）
            if defender.is_ko:
                self._resolve_ko(1 - self.current, defender)
        finally:
            self._ko_from_attack = prev_ko_flag
        if self.finished:
            return {"ok": True, "event": None}
        # S2d（2026-11-08）：**攻击结束事件**（「after attacking」）。
        # 放在击倒/免击倒结算之后、结束回合之前；`discarded` = 本招效果弃置的卡
        # 对象（差分得到），Boomerang Energy 靠它判断「是不是被自己的招式弃掉的」。
        _discarded = []
        for _k in (0, 1):
            _pl = self.players[_k]
            if len(_pl.discard_refs) > _disc_before[_k]:
                _discarded += list(_pl.discard_refs[_disc_before[_k]:])
        events.emit(self, "attack_done",
                    {"player": self.current, "attacker": attacker,
                     "defender": defender, "discarded": _discarded,
                     "turn_owner": self.current})
        special_energy.on_attack_done(self, attacker, _discarded)
        if self.finished:
            return {"ok": True, "event": None}
        # 「If Festival Grounds is in play, this Pokémon may use an attack it has
        #  twice.」（Festival Lead，第六十一批）—— 用过 1 次后**不结束回合**，
        # 让玩家还能再攻；额度用完或条件不满足才正常结束。
        if self._attack_allowance(attacker) > 1 \
                and p.attacks_this_turn < self._attack_allowance(attacker):
            self._log("extra_attack", {"player": self.current,
                                       "from": attacker.name,
                                       "used": p.attacks_this_turn})
            return {"ok": True, "event": None}
        self._end_turn()
        return {"ok": True, "event": None}

    # ---------- S3 场地（Stadium，2026-10-27） ----------
    def _attack_allowance(self, fp) -> int:
        """本回合该宝可梦允许攻击几次（Festival Lead，第六十一批）

        卡面：「If Festival Grounds is in play, this Pokémon may use an attack it
        has twice. If the first attack Knocks Out your opponent's Active Pokémon,
        you may attack again after your opponent chooses a new Active Pokémon.」
        → 场地在场 **且** 持有该被动 ⇒ 2 次；否则 1 次。

        ⚠ 场地判定用**卡名**：Festival Grounds 的 tag 是
        `stadium:status_immune:require:energy`，双攻条款在 tag 里没有位置。
        改成按 tag 判定需要给场地 tag 加段（属另一族的事），这里先用稳定的数据键名
        —— 卡名是该数据集的主键，不随语言/印刷变化。
        """
        if fp is None:
            return 1
        if not any(str(ab.tag) == "passive:extra_attack"
                   for ab in fp.pdef.abilities):
            return 1
        name = str((self.stadium or {}).get("name") or "").strip().lower()
        return 2 if name == "festival grounds" else 1

    def _stadium_meta(self) -> dict:
        """当前场地 tag → 结构化效果（`stadium:<kind>:<n>[:stage:<k>|:type:<c>]`）

        只支持**全场静态修正**两类：
          · `stadium:hp_plus:<n>:stage:<k>`  某阶段宝可梦上限 +n（负值即 −n）
          · `stadium:dmg_reduce:<n>:type:<c>` 某属性宝可梦受招式伤害 −n
        每回合可选动作 / checkup 触发 / 规则改写等形态**尚未实现**（如实登记）。
        """
        out = {"kind": "", "n": 0, "stage": None, "type": None,
               "faction": None, "require": None, "box": None, "src": None,
               "trim": None}
        tag = (self.stadium or {}).get("tag") or ""
        if not tag.startswith("stadium:"):
            return out
        parts = tag.split(":")
        out["kind"] = parts[1] if len(parts) > 1 else ""
        i = 2
        if i < len(parts) and parts[i].lstrip("-").isdigit():
            out["n"] = int(parts[i])
            i += 1
        while i + 1 < len(parts):
            tok, val = parts[i], parts[i + 1]
            if tok == "stage":
                out["stage"] = int(val) if val.isdigit() else None
            elif tok == "trim":
                out["trim"] = int(val) if val.isdigit() else None
            elif tok in ("type", "faction", "require", "box"):
                out[tok] = val
            elif tok == "from":
                out["src"] = val
            i += 2
        return out

    def _stadium_used_flags(self) -> list:
        if self._stadium_used is None:
            self._stadium_used = [0, 0]
        return self._stadium_used

    def _stadium_use_spec(self) -> dict:
        """场地「每回合一次」形态 → ``{"kind","n","type","require","name","cost","end_turn"}``

        tag：``stadium:use:<kind>[:<n>][:type:<c>][:require:<r>][:name:<key>]
        [:cost:<c>][:end:1]``
          · ``top_deck``           把手牌 1 张放到牌库顶（Academy at Night）
          · ``recover_energy``     弃牌堆取 ≤n 张基本能量入手（Levincia）
          · ``heal_all``           己方每只回复 n 点（Community Center）
          · ``switch_typed``       己方战斗位 ↔ 后备中**同属性**的一只（Surfing Beach）
          · ``draw_to_count``      弃 1 张手牌能量 → 抽到「手牌数 = 该属性在场数」（Mystery Garden）
          · ``search_basic_bench`` 牌库检索 1 张基础宝可梦**直接放后备**（Lumiose City）
          · ``search_name``        牌库检索 ≤n 张名字含 <key> 的卡入手（Spikemuth Gym）
          · ``draw``               抽 n 张（要求本回合用过指定支援者时用 ``require``）

        ``name`` 是**归一化卡名子串**（只保留 a-z0-9，见 `_name_key`）——
        卡名里有空格/撇号（"Team Rocket"、"Marnie's"），而 tag 是紧凑字符串，
        若原样塞进去会让解析层和审计脚本的 ``split(":")`` 语义变脆。
        """
        out = {"kind": "", "n": 0, "type": None, "require": None,
               "name": None, "cost": None, "end_turn": False,
               "chain": False}
        tag = (self.stadium or {}).get("tag") or ""
        if not tag.startswith("stadium:use:"):
            return out
        parts = tag.split(":")
        # stadium:use:<kind>[:<n>][:type:<c>][:require:<r>][:name:<k>][:cost:<c>][:end:1]
        out["kind"] = parts[2] if len(parts) > 2 else ""
        i = 3
        if i < len(parts) and parts[i].lstrip("-").isdigit():
            out["n"] = int(parts[i])
            i += 1
        while i + 1 < len(parts):
            if parts[i] == "type":
                out["type"] = parts[i + 1]
            elif parts[i] == "require":
                out["require"] = parts[i + 1]
            elif parts[i] == "name":
                out["name"] = parts[i + 1]
            elif parts[i] == "cost":
                out["cost"] = parts[i + 1]
            elif parts[i] == "end":
                out["end_turn"] = parts[i + 1] in ("1", "true", "turn")
            elif parts[i] == "chain":
                out["chain"] = parts[i + 1] in ("1", "true")
            i += 2
        return out

    def _stadium_use_actions(self, p: PlayerState) -> list:
        """当前场地在本回合能做什么 → 合法动作列表（空 = 不可使用）

        返回列表而不是布尔：``switch_typed`` 这类要**选目标**，每个候选是一行动作。
        """
        spec = self._stadium_use_spec()
        kind = spec["kind"]
        if not kind:
            return []
        if self._stadium_used_flags()[self.current]:
            return []
        # 使用条件（本回合打出过支援者 / 打出过**名字含 <key> 的**支援者）
        if spec["require"] == "supporter" and not p.supporter_used:
            return []
        if spec["require"] == "supporter_name":
            if not p.supporter_used or not spec["name"]:
                return []
            if spec["name"] not in _name_key(p.supporter_name):
                return []
        if kind == "top_deck":
            return [{}] if p.hand else []
        if kind == "recover_energy":
            ok = any(isinstance(c, EnergyRef) and
                     (not spec["type"] or
                      getattr(c.edef, "type", None) == spec["type"])
                     for c in p.discard_refs)
            return [{}] if ok else []
        if kind == "heal_all":
            return [{}] if any(fp.damage > 0 for fp in p.all_field()) else []
        if kind == "switch_typed":
            if p.active is None:
                return []
            if spec["type"] and str(p.active.pdef.ptype).lower() != spec["type"]:
                return []
            return [{"bench": i} for i, fp in enumerate(p.bench)
                    if not spec["type"]
                    or str(fp.pdef.ptype).lower() == spec["type"]]
        if kind == "draw_to_count":
            if spec["cost"] != "discard_energy" or not spec["type"]:
                return []                # 未知代价形态 → 不建模（宁可不用）
            if p.energy_in_hand() <= 0:
                return []
            # 弃 1 张之后手牌数仍小于该属性在场数，才有牌可抽；
            # 否则「先弃 1 再抽 0」严格劣于不使用 —— 不列进合法动作。
            return [{}] if _typed_in_play(p, spec["type"]) > len(p.hand) - 1 else []
        if kind == "search_basic_bench":
            if not self._bench_has_room(p):
                return []
            return [{}] if _deck_has_basic(p) else []
        if kind == "evolve_from_deck":
            # Grand Tree：每只「牌库里有可进化 Stage 1」的基础宝可梦各一行
            return [{"target": pos} for pos, fp in _field_iter(p)
                    if fp is not None and fp.stage == 0
                    and _deck_has_evolution(p, fp)]
        if kind == "search_name":
            if not spec["name"]:
                return []
            return [{}] if _deck_name_hits(p, spec["name"]) else []
        if kind == "draw":
            return [{}] if (p.deck and spec["n"] > 0) else []
        return []

    def _stadium_use_available(self, p: PlayerState) -> bool:
        return bool(self._stadium_use_actions(p))

    def _do_stadium_use(self, p: PlayerState, action: dict) -> dict:
        """场地「每回合一次」的主动使用（S3 2026-10-27）"""
        spec = self._stadium_use_spec()
        if not self._stadium_use_available(p):
            raise ValueError("该场地本回合不可使用（未满足条件或已用过）")
        kind = spec["kind"]
        if kind == "top_deck":
            hi = int(action.get("hand", 0))
            if not (0 <= hi < len(p.hand)):
                hi = 0
            card = p.hand.pop(hi)
            p.deck.append(card)          # 牌库顶 = 列表末尾（见 `_safe_draw`）
            detail = f"put {_card_label(card)} on top of deck"
        elif kind == "recover_energy":
            got = 0
            for di in reversed(range(len(p.discard_refs))):
                if got >= spec["n"]:
                    break
                c = p.discard_refs[di]
                if not isinstance(c, EnergyRef):
                    continue
                if spec["type"] and getattr(c.edef, "type", None) != spec["type"]:
                    continue
                p.discard_refs.pop(di)
                p.hand.append(self._rebuild_ref(c))
                got += 1
            detail = f"recover {got} energy"
        elif kind == "heal_all":
            healed = 0
            for fp in p.all_field():
                if fp.damage:
                    fp.damage = max(0, fp.damage - spec["n"])
                    healed += 1
            detail = f"heal {spec['n']} x{healed}"
        elif kind == "switch_typed":
            bi = int(action.get("bench", 0))
            if p.active is None or not (0 <= bi < len(p.bench)):
                raise ValueError("换位目标越界")
            incoming = p.bench[bi]
            if spec["type"] and str(incoming.pdef.ptype).lower() != spec["type"]:
                raise ValueError("换上的宝可梦属性不符")
            old = p.active
            old.clear_status()           # 官方规则：换下即解除特殊状态（同 Switch）
            old.clear_active_locks()
            p.active = p.bench.pop(bi)
            p.bench.append(old)
            p.active.moved_active_turn = self.turn_number
            detail = f"switch {old.name} -> {p.active.name}"
        elif kind == "draw_to_count":
            hi = action.get("hand")
            idx = None
            if hi is not None and 0 <= int(hi) < len(p.hand) \
                    and isinstance(p.hand[int(hi)], EnergyRef):
                idx = int(hi)
            if idx is None:
                idx = next((i for i in reversed(range(len(p.hand)))
                            if isinstance(p.hand[i], EnergyRef)), None)
            if idx is None:
                raise ValueError("手牌无能量可弃（代价无法支付）")
            dropped = _card_label(p.hand[idx])
            self._to_discard(p, p.hand.pop(idx))
            target = _typed_in_play(p, spec["type"])
            got = self._safe_draw(self.current, max(0, target - len(p.hand)),
                                  source="stadium")
            detail = f"discard {dropped}, draw {got} (to {target})"
        elif kind == "search_basic_bench":
            if not self._bench_has_room(p):
                raise ValueError("后备区已满")
            ref = _take_basic_from_deck(p)
            if ref is None:
                raise ValueError("牌库无基础宝可梦")
            self.rng.shuffle(p.deck)
            fp = make_pokemon(ref.pdef)
            fp.just_played = True
            p.bench.append(fp)
            self._on_bench_placed(self.current, fp)   # 与「手牌上场」走同一钩子
            detail = f"search {fp.name} to bench"
        elif kind == "evolve_from_deck":
            tpos = int(action.get("target", 0))
            fp = _field_at(p, tpos)
            if fp is None or fp.stage != 0:
                raise ValueError("进化目标为空或不是基础宝可梦")
            self._evolve_from_deck(p, fp)
            detail = f"evolve {fp.name}"
            if spec["chain"]:
                # 「If that Pokémon was evolved in this way, … Stage 2 …」
                self._evolve_from_deck(p, fp)
                detail += f" -> {fp.name}"
            self.rng.shuffle(p.deck)
        elif kind == "search_name":
            got = _take_name_from_deck(p, spec["n"] or 1, spec["name"])
            if not got:
                raise ValueError("牌库无可检索目标")
            self.rng.shuffle(p.deck)
            detail = f"search {got} by name to hand"
        elif kind == "draw":
            got = self._safe_draw(self.current, spec["n"], source="stadium")
            detail = f"draw {got}"
        else:
            raise ValueError(f"未知场地上使用形态：{kind}")
        self._stadium_used_flags()[self.current] = 1
        self._log("stadium_use", {"player": self.current,
                                  "name": (self.stadium or {}).get("name"),
                                  "kind": kind, "detail": detail})
        ev = self.events[-1]
        if spec["end_turn"]:
            # 「If a player searches their deck in this way, their turn ends.」
            # 必须放在日志之后：`_end_turn` 会切换 current，再记日志就记到对手头上了。
            self._end_turn()
        return {"ok": True, "event": ev}

    def _stadium_hp_delta(self, fp: FieldPokemon) -> int:
        m = self._stadium_meta()
        if m["kind"] != "hp_plus":
            return 0
        if m["stage"] is not None and fp.pdef.stage != m["stage"]:
            return 0
        return m["n"]

    def _refresh_stadium_hp(self) -> None:
        """把「特性静态 + 场地静态」的 +HP 一起重算到 `bonus_hp`

        为什么必须重算而不是增量：场地会被顶替/移走，增量减法一旦漏一处就会
        永久漂移（血量上限这种字段出错极难发现）。重算的代价是每回合 O(场上数量)。
        """
        tools_off = self._stadium_meta()["kind"] == "no_tool_effect"
        for pi in (0, 1):
            for fp in self.players[pi].all_field():
                base = 0
                for ab in fp.pdef.abilities:
                    tag = str(getattr(ab, "tag", "") or "")
                    # 条件式 +HP（`:if:`）由 `cards.cond_hp_bonus` 在 `max_hp`
                    # 里现算 —— 这里再算一遍会**重复计**（上限突然翻倍）。
                    if tag.startswith("passive:hp_plus:") and ":if:" not in tag:
                        raw = tag.split(":")
                        if len(raw) > 2 and raw[2].isdigit():
                            base += int(raw[2])
                fp.bonus_hp = base + self._stadium_hp_delta(fp)
                # Jamming Tower：道具失效标志（与 +HP 同一次重算，避免漂移）
                fp.tool_disabled = tools_off
        # Area Zero：失去 Tera 宝可梦后把后备裁到上限（不取奖赏）
        self._enforce_bench_limit()

    def _stadium_damage_reduce(self, defender: FieldPokemon) -> int:
        m = self._stadium_meta()
        if m["kind"] != "dmg_reduce" or not m["n"]:
            return 0
        if m["type"] and str(defender.pdef.ptype).lower() != m["type"].lower():
            return 0
        if m["faction"] and not _name_key(defender.name).startswith(m["faction"]):
            return 0
        return m["n"]

    def _stadium_attack_bonus(self, attacker: FieldPokemon) -> int:
        """场地「某阵营招式 +N」（Postwick）—— 加在**弱点/抗性之前**的基础伤害上"""
        m = self._stadium_meta()
        if m["kind"] != "attack_plus" or not m["n"]:
            return 0
        if m["faction"] and not _name_key(attacker.name).startswith(m["faction"]):
            return 0
        return m["n"]

    def _stadium_immune(self, defender: FieldPokemon,
                        attacker: Optional[FieldPokemon]) -> bool:
        """场地免伤（Neutralization Zone）：无规则框目标 ← 规则框来源

        tag：``stadium:dmg_immune:box:none:from:rulebox``
        卡面：「Prevent all damage done to Pokémon that don't have a Rule Box
        (both yours and your opponent's) by attacks from the opponent's Pokémon
        {ex} and Pokémon {V}.」——本卡池的规则框只有「Pokémon ex / Mega ex」，
        用 ``is_ex`` 精确判定（V 不存在于本数据集）。
        """
        m = self._stadium_meta()
        if m["kind"] != "dmg_immune" or m["box"] != "none" \
                or m["src"] != "rulebox":
            return False
        if defender is None or bool(defender.pdef.is_ex):
            return False
        return attacker is not None and bool(attacker.pdef.is_ex)

    def _bench_cap(self, p: PlayerState) -> int:
        """后备上限（Area Zero Underdepths：有 Tera 宝可梦时可到 8）"""
        m = self._stadium_meta()
        if m["kind"] == "bench_limit" and m["n"] and m["require"] == "tera":
            has_tera = any(str(getattr(fp.pdef, "category", "") or "")
                           .startswith("Tera") for fp in p.all_field())
            if has_tera:
                return m["n"]
        return self.cfg.max_bench

    def _bench_has_room(self, p: PlayerState) -> bool:
        return len(p.bench) < self._bench_cap(p)

    def _enforce_bench_limit(self) -> None:
        """Area Zero：失去 Tera 宝可梦后，把后备裁到上限（**不取奖赏**）"""
        m = self._stadium_meta()
        if m["kind"] != "bench_limit" or not m["n"]:
            return
        for pi in (0, 1):
            p = self.players[pi]
            cap = self._bench_cap(p)
            while len(p.bench) > cap:
                fp = p.bench.pop()
                p.discard.append(fp.name)
                p.discard_refs.append(PokeRef(fp.pdef))
                self._log("bench_trim", {"player": pi, "card": fp.name,
                                         "cap": cap})

    def _no_bench_counters(self) -> bool:
        """Battle Cage：后备不受对手效果放置的伤害指示物"""
        return self._stadium_meta()["kind"] == "no_bench_counters"

    def _stadium_status_immune(self, fp: Optional[FieldPokemon]) -> bool:
        """场地：附着了任意能量的宝可梦不受特殊状态（Festival Grounds）"""
        m = self._stadium_meta()
        if m["kind"] != "status_immune" or m["require"] != "energy":
            return False
        return fp is not None and fp.total_energy() > 0

    def _status_blocked(self, fp: Optional[FieldPokemon],
                        status: Optional[str] = None) -> bool:
        """特殊状态是否**不能施加**到该宝可梦

        三个来源：
          · 化石卡（不能受任何特殊状态）
          · Festival Grounds（附着了能量的宝可梦不受特殊状态）
          · 卡面常驻被动 ``passive:status_immune:<status>|all``
            （第一百一十一批，Hoothoot「Insomnia」= ``sleep``）

        ``status=None`` 表示「调用方没指定具体状态」，此时只有 ``all`` 形态算数 ——
        绝不能把「只免疫睡眠」当成「免疫全部」。
        """
        if fp is None:
            return False
        if getattr(fp, "fossil", False) or self._stadium_status_immune(fp):
            return True
        for ab in (getattr(fp.pdef, "abilities", None) or ()):
            tag = str(getattr(ab, "tag", "") or "")
            if not tag.startswith("passive:status_immune:"):
                continue
            what = tag.split(":")[2].lower()
            if what == "all":
                return True
            if status and what == str(status).lower():
                return True
        return False

    def _recover_stadium_statuses(self) -> None:
        """Festival Grounds：「recovers from all Special Conditions」

        在该玩家回合开始（状态检查之前）把**附着能量者**的状态全部清除。
        """
        m = self._stadium_meta()
        if m["kind"] != "status_immune" or m["require"] != "energy":
            return
        for pi in (0, 1):
            for fp in self.players[pi].all_field():
                if fp.status and fp.total_energy() > 0:
                    self._log("status_recover", {"player": pi,
                                                 "target": fp.name,
                                                 "statuses": sorted(fp.status)})
                    fp.status.clear()

    def _stadium_checkup_extra(self, fp: FieldPokemon) -> None:
        """场地：checkup 阶段对特定状态/属性追加伤害（Perilous Jungle）

        tag：``stadium:poison_extra:<n>:type:<c>``
        「put 2 more damage counters on each Poisoned non-{D} Pokémon」
        → 中毒的、且属性**不是** <c> 的，额外受 n 点（在常规中毒 10 点之后结算）。
        这是**追加**而非替换：卡面写 "2 more damage counters"，不是 "instead"。
        """
        m = self._stadium_meta()
        if m["kind"] != "poison_extra" or not m["n"]:
            return
        if "poison" not in fp.status:
            return
        if m["type"] and str(fp.pdef.ptype).lower() == m["type"].lower():
            return                     # 该属性免疫此追加伤害
        fp.damage += m["n"]
        self._log("checkup", {"type": "stadium_poison_extra",
                              "target": fp.name, "dmg": m["n"],
                              "player": self.current})

    def _checkup_mods(self, side: int) -> tuple:
        """汇总 Pokémon Checkup 的追加/改写（2026-10-28 第十三批）

        tag：``passive:checkup_plus:<status>:<n>[:side:opp][:active_only]``
          · ``side:opp``      作用于**持有者的对手**（卡面「your opponent's
            Poisoned/Burned Pokémon」，Pecharunt / Magmortar）
          · ``active_only``   特性持有者必须在战斗位（Pecharunt）
        招式设置的改写读自被检查宝可梦身上的 `checkup_set`
        （Nidoking ex「put 8 damage counters … instead of 1」）。

        返回 ``(bonus, override)``，语义见 `rules.status_checkup`。
        """
        bonus = {"poison": 0, "burn": 0}
        for si, pl in enumerate(self.players):
            for src in (pl.active, *pl.bench):
                if src is None:
                    continue
                for ab in src.pdef.abilities:
                    tag = str(getattr(ab, "tag", "") or "")
                    if not tag.startswith("passive:checkup_plus:"):
                        continue
                    parts = tag.split(":")
                    if len(parts) < 4 or not parts[3].isdigit():
                        raise ValueError(f"checkup_plus tag 字段非法：{tag}")
                    status = parts[2]
                    if status not in bonus:
                        raise ValueError(f"checkup_plus 状态非法：{tag}")
                    opts = parts[4:]
                    if "active_only" in opts and pl.active is not src:
                        continue
                    want = (1 - si) if "opp" in opts else si
                    if want != side:
                        continue
                    bonus[status] += int(parts[3])
        active = self.players[side].active
        override = dict(getattr(active, "checkup_set", {}) or {})
        return bonus, override

    def _apply_checkup_spreads(self, owner: int) -> None:
        """Checkup **群体**指示物放置（2026-10-28 第十四批）

        tag：``passive:checkup_spread:<dmg>:<scope>[:except:<key>][:active_only]``
          · ``ability_all``  **双方**场上所有「有特性」的宝可梦，可用
            ``:except:<name_key>`` 排除某卡名（Froslass 自己）
          · ``opp_basic``    对手场上所有**基础**宝可梦（Tyranitar）
        只在**特性拥有者**的回合开始时结算一次（每轮一次）；伤害直接加到
        `damage` 上，击倒由 `_begin_turn` 的统一结算处理。
        """
        me = self.players[owner]
        opp = self.players[1 - owner]
        for src in (me.active, *me.bench):
            if src is None:
                continue
            for ab in src.pdef.abilities:
                tag = str(getattr(ab, "tag", "") or "")
                if not tag.startswith("passive:checkup_spread:"):
                    continue
                parts = tag.split(":")
                if len(parts) < 4 or not parts[2].isdigit():
                    raise ValueError(f"checkup_spread tag 字段非法：{tag}")
                dmg = int(parts[2])
                scope = parts[3]
                opts = parts[4:]
                if "active_only" in opts and me.active is not src:
                    continue
                if scope == "ability_all":
                    excl = ""
                    if "except" in opts:
                        _i = opts.index("except")
                        excl = opts[_i + 1] if _i + 1 < len(opts) else ""
                    targets = [(owner, fp) for fp in me.all_field()] \
                        + [(1 - owner, fp) for fp in opp.all_field()]
                    targets = [(s, fp) for s, fp in targets
                               if fp.pdef.abilities
                               and (not excl or excl not in _name_key(fp.pdef.name))]
                elif scope == "opp_basic":
                    targets = [(1 - owner, fp) for fp in opp.all_field()
                               if fp.pdef.stage == 0]
                else:
                    raise ValueError(f"未识别的 checkup_spread 范围：{tag}")
                for _s, fp in targets:
                    fp.damage += dmg
                    self._log("checkup", {"type": "checkup_spread",
                                          "target": fp.name, "dmg": dmg,
                                          "player": owner})

    def _on_bench_placed(self, owner: int, fp: FieldPokemon) -> None:
        # 上场触发特性先执行（与场地伤害无关，不能受下面 `return` 影响）
        self._trigger_bench_abilities(owner, fp)
        """场地：宝可梦被放到后备时触发的伤害（Risky Ruins）

        tag：``stadium:on_bench_basic:<n>:type:<c>``
        「Whenever any player puts a Basic non-{D} Pokémon onto their Bench during
        their turn, place 2 damage counters on that Pokémon.」
        ⚠ 只在**放到后备**时触发（不是战斗位）；属性 <c> 的豁免。
        """
        m = self._stadium_meta()
        if m["kind"] != "on_bench_basic" or not m["n"]:
            return
        if fp.pdef.stage != 0:
            return                     # 只限基础宝可梦
        if m["type"] and str(fp.pdef.ptype).lower() == m["type"].lower():
            return
        fp.damage += m["n"]
        self._log("stadium_trigger", {"kind": "on_bench_basic",
                                      "player": owner, "target": fp.name,
                                      "dmg": m["n"]})
        if fp.is_ko:
            self._resolve_ko(owner, fp)

    def _trigger_bench_abilities(self, owner: int, fp: FieldPokemon) -> None:
        """「When you play this Pokémon from your hand onto your Bench during your
        turn, you may …」（第一百批，Drilbur「Dig Dig Dig」）

        tag：``on_bench_act:<tag1>|<tag2>`` —— 与 ``evolve_act:`` 同构：只在
        **放到后备那一刻**执行一次。

        ⚠ 为什么必须包一层：不包就是普通 field 特性 → **每回合都能发动一次**，
        一张「上场检索弃 3 能量」的卡会变成「每回合白弃 3 张牌库」。
        """
        if owner != self.current:
            return
        for ab in (fp.pdef.abilities or ()):
            tag = str(getattr(ab, "tag", "") or "")
            if not tag.startswith("on_bench_act:"):
                continue
            subs = tuple(x for x in tag[len("on_bench_act:"):].split("|") if x)
            if not subs:
                continue
            self._log("ability", {"player": owner, "name": ab.name,
                                  "from": fp.name, "trigger": "on_bench",
                                  "detail": "|".join(subs)})
            self._apply_attack_effects(fp, subs, action={})

    # ---------- S2 第二阶段：道具触发钩子 ----------
    def _tool_reduce_vs(self, defender: FieldPokemon,
                        attacker: FieldPokemon) -> int:
        """道具减伤（**按攻击方判定**）—— `tool:dmg_reduce:N[:from:…][:holder:…]`

        为什么要单独一个函数而不是塞进 `_static_mods`：卡面常写
        「takes 30 less damage from attacks from your opponent's Pokémon **that
        have an Ability**」/「**{G},{R},{W},{L} Pokémon**」—— 同一个减伤值只对
        特定攻击方成立，静态层拿不到攻击方上下文，无条件累加会变成**全类型减伤**。
        `discard`（浆果类一次性道具）在**生效后**弃置。
        """
        tag = _tool_tag(defender)
        if not tag.startswith("tool:dmg_reduce:"):
            return 0
        meta = _tool_meta(tag)
        if not meta["n"]:
            return 0
        if not _qual_ok(meta["holder"], defender):
            return 0
        if not _qual_ok(meta["from"], attacker):
            return 0
        if meta["discard"]:
            name = _drop_tool(defender)
            self._log("tool_discard", {"player": 1 - self.current, "tool": name,
                                       "target": defender.name})
        return meta["n"]

    def _trigger_energy_on_damaged(self, defender: FieldPokemon,
                                   attacker: FieldPokemon, dmg: int) -> None:
        """持有者（对手战斗位）被招式伤害后触发的**特殊能量**（Spiky Energy）

        卡面：「… is in the Active Spot and is damaged by an attack …, put 2 damage
        counters on the Attacking Pokémon（even if this Pokémon is Knocked Out）」
        → 与道具/特性同一时点（击倒结算**之前**）。
        """
        if dmg <= 0 or defender is None or attacker is None:
            return
        n = special_energy.on_damaged_counter(defender)
        if not n:
            return
        attacker.damage += n
        self._log("energy_on_damaged", {"player": 1 - self.current,
                                        "target": attacker.name,
                                        "dmg": n, "from": "spiky"})

    def _trigger_tool_on_damaged(self, defender: FieldPokemon,
                                 attacker: FieldPokemon, dmg: int) -> None:
        """持有者被招式伤害后触发道具（``tool:on_damaged:<kind>:<n>[:discard]``）

        卡面限定「is in the Active Spot and is damaged by an attack」：
          · `defender` 必为对手战斗位（调用点就是 `opp.active`）；
          · `dmg > 0` 才算"被伤害"（被护盾完全挡下不触发）；
          · 卡面写 "even if this Pokémon is Knocked Out" → 先于击倒结算执行。

        支持 kind：`draw`（抽 n 张，归属持有者）/ `counter`（攻击方受 n 点伤害）。
        """
        tag = _tool_tag(defender)
        if dmg <= 0 or not tag.startswith("tool:on_damaged:"):
            return
        meta = _tool_meta(tag)
        # 持有者限定（例：`the {D} Pokémon this card is attached to`）
        if not _qual_ok(meta["holder"], defender):
            return
        parts = tag.split(":")
        kind = parts[2] if len(parts) > 2 else ""
        n = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        owner = 1 - self.current          # defender 属于对手
        if kind == "draw" and n > 0:
            self._safe_draw(owner, n, source="tool")
            self._log("tool_trigger", {"player": owner, "tool": defender.tool_name,
                                       "kind": "draw", "n": n,
                                       "target": defender.name})
        elif kind == "move_energy" and n > 0:
            # 攻击方的 n 个能量 → 攻击方自己的后备（卡面 "your opponent's Benched
            # Pokémon" 是相对**道具持有者**说的，即攻击方一侧）
            ap = self.players[self.current]
            moved = 0
            if ap.bench:
                for _ in range(n):
                    # 选哪个能量：与 `remove_any` 同口径 —— **无色优先**，其次任意色
                    av = {k: v for k, v in attacker.energy.items() if v > 0}
                    if not av:
                        break
                    etype = next((t for t in av if t == COLORLESS), next(iter(av)))
                    attacker.energy[etype] -= 1
                    if attacker.energy[etype] <= 0:
                        del attacker.energy[etype]
                    ap.bench[0].add_energy(etype)
                    moved += 1
                self._log("tool_trigger", {"player": owner, "tool": defender.tool_name,
                                           "kind": "move_energy", "n": moved,
                                           "target": defender.name})
        elif kind == "status" and parts[3] in STATUSES:
            # 「the Attacking Pokemon is now Asleep」（第一百零一批，
            # Team Rocket's Hypnotizer）—— 目标恒为**攻击方**（不是持有者），
            # 且必须过一遍状态免疫判定（否则会把状态硬塞给免疫者）。
            _st = parts[3]
            if self._status_blocked(attacker, _st) \
                    or families.effect_immune(self, attacker, "ability"):
                self._log("blocked", {"target": attacker.name,
                                      "by": "status_immune",
                                      "effect": "tool_status"})
            else:
                attacker.status.add(_st)
                self._log("tool_trigger", {
                    "player": owner, "tool": defender.tool_name,
                    "kind": "status", "status": _st,
                    "target": attacker.name})
        elif kind == "counter" and n > 0:
            attacker.damage += n
            self._log("damage", {"target": attacker.name, "player": self.current,
                                 "dmg": n, "tool": defender.tool_name})
            self._log("tool_trigger", {"player": owner, "tool": defender.tool_name,
                                       "kind": "counter", "n": n,
                                       "target": defender.name})
            if attacker.is_ko:
                self._resolve_ko(self.current, attacker)
        # ⚠ 不能用 `parts[4] == "discard"`：加了 holder 限定后 discard 位置会变
        if meta["discard"]:
            name = _drop_tool(defender)
            self._log("tool_discard", {"player": owner, "tool": name,
                                       "target": defender.name})

    def _trigger_ability_on_damaged(self, defender: FieldPokemon,
                                    attacker: FieldPokemon, dmg: int) -> None:
        """受击反应特性（``on_damaged:<kind>:<arg>``，2026-10-27）

        卡面原型：「If this Pokémon is in the Active Spot and is damaged by an
        attack from your opponent's Pokémon (even if this Pokémon is Knocked Out),
        the Attacking Pokémon is now Burned.」

        这是**被动**特性，不进合法动作表（见 `_ability_legals` 的跳过项）：
          · `defender` 必为对手战斗位（调用点即「被打的那只」）；
          · `dmg > 0` 才算被伤害（护盾全挡住不触发）；
          · 卡面写 "even if … Knocked Out" → 调用点在 `_resolve_ko` **之前**。
        支持 kind：`status`（攻击者陷入状态）/ `counter`（攻击者吃伤害）/
        `discard_energy`（攻击者弃能量）/
        `counter_per`（``counter_per:<n>:<color>``：攻击者吃 n×10×**持有者该色能量数**
        点伤害，Orthworm ex，2026-10-29 第二十九批）。
        """
        if dmg <= 0:
            return
        owner = 1 - self.current
        for ab in defender.pdef.abilities:
            tag = str(ab.tag or "")
            if not tag.startswith("on_damaged:"):
                continue
            parts = tag.split(":")
            # `on_damaged:<kind>:<arg>` —— 下标从 1 起（没有 `ability:` 前缀，
            # 与 cards_real.json 其余特性 tag 一致：recon:3 / ko_draw:3 …）
            kind = parts[1] if len(parts) > 1 else ""
            arg = parts[2] if len(parts) > 2 else ""
            if kind == "status" and arg in STATUSES:
                attacker.status.add(arg)
                self._log("ability_trigger", {
                    "player": owner, "ability": ab.name, "kind": "status",
                    "status": arg, "target": attacker.name})
            elif kind == "counter" and arg.isdigit():
                n = int(arg)
                attacker.damage += n
                self._log("damage", {"target": attacker.name,
                                     "player": self.current, "dmg": n,
                                     "ability": ab.name})
                self._log("ability_trigger", {
                    "player": owner, "ability": ab.name, "kind": "counter",
                    "n": n, "target": attacker.name})
                if attacker.is_ko:
                    self._resolve_ko(self.current, attacker)
            elif kind == "counter_per":
                # 「… for each {M} Energy attached to this Pokémon」（Orthworm ex）
                # —— 数值随**持有者**的能量数变化；用 `energy_of_type` 精确计色。
                _pp = tag.split(":")
                if len(_pp) < 4 or not _pp[2].isdigit() or _pp[3] not in TYPES:
                    raise ValueError(f"on_damaged counter_per 形态非法：{tag}")
                cnt = defender.energy_of_type(_pp[3])
                n = int(_pp[2]) * 10 * cnt
                if n > 0:
                    attacker.damage += n
                    self._log("damage", {"target": attacker.name,
                                         "player": self.current, "dmg": n,
                                         "ability": ab.name})
                    self._log("ability_trigger", {
                        "player": owner, "ability": ab.name,
                        "kind": "counter_per", "n": n,
                        "per": int(_pp[2]) * 10, "count": cnt})
                    if attacker.is_ko:
                        self._resolve_ko(self.current, attacker)
            elif kind == "search_bench":
                # 「search your deck for up to N Pokémon that have "Koffing" in
                #  their name and put them onto your Bench」（第一百零一批，
                #  Team Rocket's Koffing）—— 复用 `_search_bench` 的
                #  `:name:<key>` 精确名与新增的 `:name_has:<key>` 子串两种筛选。
                _sb = "search_bench:" + ":".join(parts[2:])
                detail = self._search_bench(self.players[owner], _sb)
                self._log("ability_trigger", {
                    "player": owner, "ability": ab.name,
                    "kind": "search_bench", "detail": detail})
            elif kind == "discard_energy" and arg.isdigit():
                n = int(arg)
                avail = min(n, attacker.total_energy())
                done = bool(avail) and attacker.remove_any(avail)
                self._log("ability_trigger", {
                    "player": owner, "ability": ab.name,
                    "kind": "discard_energy", "n": n if done else 0,
                    "target": attacker.name})

    def _trigger_retaliate(self, defender: FieldPokemon,
                           attacker: FieldPokemon, dmg: int) -> None:
        """下回合「受击反制」（``retaliate_next:<n|equal>``，2026-10-29 第二十九批）

        卡面：「During your opponent's next turn, if this Pokémon is damaged by an
        attack (even if this Pokémon is Knocked Out), put N damage counters on the
        Attacking Pokémon[ equal to the damage done to this Pokémon].」
        —— 由**招式**挂在出招者自己身上，只在**对手的下一个回合**内触发；
        每次被攻击都可触发（不是每回合一次）。
        """
        if dmg <= 0 or defender is None or attacker is None:
            return
        if defender.retaliate_owner != self.current:
            return                       # 只在挂载者**对手**的回合内生效
        if defender.retaliate_until <= self.turn_counts[self.current]:
            return                       # 已过窗口
        n = (dmg // 10) * 10 if defender.retaliate_next_equal \
            else defender.retaliate_next_n
        if n <= 0:
            return
        attacker.damage += n
        self._log("damage", {"target": attacker.name, "player": self.current,
                             "dmg": n, "retaliate": True})
        self._log("retaliate", {"player": 1 - self.current,
                                "target": attacker.name, "n": n,
                                "source": defender.name})
        if attacker.is_ko:
            self._resolve_ko(self.current, attacker)

    def _tool_survive(self, defender: FieldPokemon) -> bool:
        """``tool:survive:<m>`` —— 满 HP 时免于被招式击倒，剩 m 点 HP，道具自身弃置

        只在**伤害结算后 `is_ko` 成立**时调用（调用点已保证"受伤前满 HP"）。
        返回 True 表示已免击倒（调用方不应再走 `_resolve_ko`）。
        """
        tag = _tool_tag(defender)
        if not tag.startswith("tool:survive:"):
            return False
        try:
            m = int(tag.split(":")[2])
        except (IndexError, ValueError):
            return False
        defender.damage = max(0, defender.max_hp - m)
        name = _drop_tool(defender)
        self._log("tool_survive", {"player": 1 - self.current, "target":
                                   defender.name, "tool": name, "hp": m})
        return True

    # ---------- v1.4 伤害修正层（抛币 / 计数缩放 / 条件加伤） ----------
    def _attack_mods(self, attacker: FieldPokemon, effects: tuple,
                     action: Optional[dict],
                     defender: Optional[FieldPokemon] = None,
                     atk=None) -> dict:
        """解析招式效果里的伤害修正，返回

        ``{"plus": int, "set": Optional[int], "zero": bool,
           "ignore_weakness": bool, "ignore_resistance": bool, "coins": [dict]}``

        支持的 tag
          ``dmg_plus:N``                       固定加伤
          ``dmg_plus:N:per:<counter>``          按计数源 ×N 加伤
          ``dmg_plus:N:if:<cond>``              条件满足则加伤
          ``dmg_set:N[:per:<counter>]``          基础伤害**改为** N（或 N×计数源）
          ``dmg_zero``                          本次伤害归零（配合 ``coin:tails:``）
          ``ignore_weakness`` / ``ignore_resistance``
          ``coin:heads:<tag>`` / ``coin:tails:<tag>``  抛 1 枚，按结果决定是否生效
          ``coin_each:N:tails:<tag>``           抛 N 枚，按反面枚数重复生效

        ``set`` 与 ``plus`` 的区别：卡面「This attack does 30 damage for each X」
        是**基础伤害重算**（0 个 X → 0 伤害），不是「印刷伤害 + 30×X」。
        早期误落成 ``dmg_plus``，导致池内 47 条缩放招式系统性多算印刷伤害。

        设计要点：抛币与缩放在**同一次攻击内**只掷/算一次，结果写事件日志（可复现：
        用 ``self.rng``，不引入额外随机源）。
        """
        out = {"plus": 0, "minus": 0, "set": None, "zero": False,
               "nullify_all": False,
               "ignore_weakness": False, "ignore_resistance": False, "coins": []}
        for tag in effects or ():
            if tag == "dmg_plus_prev_taken":
                # 「If this Pokémon was damaged by an attack during your
                # opponent's last turn, this attack does **that much** more
                # damage.」（第一百零一批，Mega Heracross ex）
                # 取**攻击方自己**的受击量，读 `prev`（对手上回合）而非 `mark`。
                out["plus"] += attacker.dmg_taken_prev
                self._log("dmg_prev_taken", {"target": attacker.name,
                                             "n": attacker.dmg_taken_prev})
            elif tag == "dmg_zero":
                out["zero"] = True
            elif tag.startswith("nullify_if:"):
                # 招式失败门（2026-10-28）：「If <条件> / If you don't …, this
                # attack does nothing.」——**伤害与效果都不发生**（卡面明文）；
                # 条件为假时照常结算。解析侧只在条件可精确求值时产出该 tag。
                cond = tag[len("nullify_if:"):]
                if not cond:
                    raise ValueError(f"{tag}：缺少条件")
                if _eval_condition(self, cond):
                    out["zero"] = True
                    out["nullify_all"] = True
                    self._log("attack_nullified", {"tag": tag})
            elif tag == "ignore_weakness":
                out["ignore_weakness"] = True
            elif tag == "ignore_resistance":
                out["ignore_resistance"] = True
            elif tag.startswith("dmg_plus:"):
                self._accum_dmg_plus(tag, out)
            elif tag.startswith("dmg_minus:"):
                self._accum_dmg_minus(tag, out)
            elif tag.startswith("dmg_set:"):
                # `dmg_set:N:if:<cond>`（第一百二十批，Mega Mawile ex
                # 「If your opponent's Active Pokémon already has any damage
                # counters on it, this attack's base damage is 30.」）——
                # 条件不成立时**保持印刷伤害**，不是「改为 0」。
                _dp = tag.split(":")
                if "if" in _dp:
                    _i = _dp.index("if")
                    if _eval_condition(self, ":".join(_dp[_i + 1:])):
                        out["set"] = self._dmg_set_value(":".join(_dp[:_i]))
                else:
                    out["set"] = self._dmg_set_value(tag)
            elif tag.startswith(_BORROW_COIN_GATE_PREFIX):
                # S5：抛币门的借用招式已在 `_do_attack` 开头抛过（**必须**早于
                # 借用来源解析）。这里再抛一次会得到第二枚互不相干的币，日志里
                # 两枚矛盾且不可复现；子效果由 `_apply_coin_non_damage` 处理 ——
                # 它只消费 `pre_mods` 里记录的币，这里不记 → 自然什么都不做。
                continue
            elif tag.startswith("coin:") or tag.startswith("coin_each:"):
                self._resolve_coin_tag(attacker, tag, out)
            elif tag.startswith("coin_all:"):
                self._resolve_coin_all_tag(attacker, tag, out)
            elif tag.startswith("coin_until_tails:"):
                # 「Flip a coin until you get tails. This attack does N more
                # damage for each heads.」（Breloom / Tyrogue / Voltorb ex）
                self._resolve_coin_until_tails(attacker, tag, out)
            elif tag.startswith("discard_hand_energy:") and "required" in tag.split(":"):
                # B3 失败门：「Discard N Basic {X} Energy cards from your hand.
                # If you can't discard N cards in this way, this attack does nothing.」
                # 必须在**伤害结算之前**判定（`_attack_mods` 早于 `_apply_attack_effects`），
                # 否则会先打出伤害再发现弃不出来。
                _n, _et, _req = _parse_hand_energy_tag(tag)
                _p = self.players[self.current]
                if _req and _hand_energy_of(_p, _et) < _n:
                    out["zero"] = True
                    self._log("attack_nullified", {"tag": tag,
                                                   "reason": "手牌能量不足"})
        # 效果族静态加伤（「attacks used by your X do N more damage …」）
        # 在弱点/抗性**之前**叠加（卡面通常写明 before applying Weakness…）。
        # 只在**本身有基础伤害**的招式上生效：0 伤害招式不会因常驻加伤凭空
        # 打出伤害（卡面「attacks ... do N more damage」以招式有伤害为前提）。
        if atk is None or atk.damage > 0:
            out["plus"] += families.static_dmg_plus(self, attacker, defender)
            # 道具侧静态加伤（Maximum Belt / Light Ball / Binding Mochi）
            out["plus"] += _tool_dmg_plus(attacker, defender)
            # 本回合限时加伤（第九十五批，Feraligatr「Torrential Heart」）
            # `dmg_bonus_until == turn_number` → 仅本回合；与静态加伤一样**在弱点之前**。
            _bl = self.players[self.current]
            if (_bl.dmg_bonus_n and _bl.dmg_bonus_until == self.turn_number
                    and (_bl.dmg_bonus_scope == "team"
                         or (_bl.dmg_bonus_scope == "self"
                             and attacker is _bl.dmg_bonus_src))):
                out["plus"] += _bl.dmg_bonus_n
        # 跨回合修正（2026-10-29 第十六批）—— 都在弱点/抗性**之前**。
        #   · 「During your opponent's next turn, attacks used by the Defending
        #     Pokémon do N less damage (before …)」→ 受击方身上的 `atk_minus_next`
        #   · 「During your next turn, this Pokémon's <招式> attack does N more
        #     damage (before …)」→ 自身 `self_atk_plus_next`（按招式名匹配）
        # 到期比较用**持有者**的 `turn_counts`（与 attack_lock 同口径）。
        now = self.turn_counts[self.current]
        if attacker.atk_minus_next_until > now and attacker.atk_minus_next_n:
            out["minus"] += attacker.atk_minus_next_n
            self._log("atk_minus_next_applied", {
                "target": attacker.name, "n": attacker.atk_minus_next_n})
        if atk is not None and attacker.self_atk_set_next:
            _s = attacker.self_atk_set_next.get(_atk_tag_key(atk.name))
            if _s and _s[1] > now:
                out["set"] = _s[0]
                self._log("self_atk_set_next_applied", {
                    "target": attacker.name, "attack": atk.name, "n": _s[0]})
        if atk is not None and attacker.self_atk_plus_next:
            _hit = attacker.self_atk_plus_next.get(attacker._attack_key(atk.name))
            if _hit and _hit[1] > now and _hit[0]:
                out["plus"] += _hit[0]
                self._log("self_atk_plus_next_applied", {
                    "target": attacker.name, "attack": atk.name, "n": _hit[0]})
        # 全队**本回合**增伤（2026-11-04 第六十批）：只对**攻击方所属**玩家生效，
        # 且只在施加当回合（卡面「During this turn」）。属性 / 卡牌子类限定按
        # 攻击方与防守方分别判定 —— 两个限定是**与**关系，不是或。
        _tb = self.players[self.current]
        if _tb.team_boost_n \
                and _tb.team_boost_turn == self.turn_counts[self.current]:
            _tb_ok = True
            if _tb.team_boost_type and \
                    str(attacker.pdef.ptype).lower() != _tb.team_boost_type:
                _tb_ok = False
            if _tb_ok and _tb.team_boost_cats:
                _ok_cat = False
                _cdef = str((defender.pdef.category if defender else "")
                            or "").lower()
                for _want in (x.strip() for x in _tb.team_boost_cats.split(",")):
                    if not _want:
                        continue
                    if _want == "ex":
                        # {ex} 同时涵盖 Mega（`is_ex` 对 Mega 也为 True）与
                        # Tera ex（category 以 "Tera" 开头）——三者都是 ex 卡。
                        if bool(getattr(defender.pdef, "is_ex", False)) \
                                or _cdef.startswith("ex") \
                                or _cdef.startswith("tera"):
                            _ok_cat = True
                    elif _want in _cdef:
                        _ok_cat = True
                _tb_ok = _ok_cat
            if _tb_ok:
                out["plus"] += _tb.team_boost_n
                self._log("team_boost_applied", {
                    "target": attacker.name, "n": _tb.team_boost_n})
        return out

    def _accum_dmg_plus(self, tag: str, out: dict) -> None:
        parts = tag.split(":")
        n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        kind = parts[2] if len(parts) > 2 else None
        if kind is None:
            out["plus"] += n
        elif kind == "per":
            # ⚠ 计数源本身可能**带参数**（`own_discard_named:ethan…` /
            #   `both_field_named_any:a+b`）→ 必须取 `parts[3:]` 全量拼接，
            #   只取 parts[3] 会把参数截掉（2026-10-28 实测：伤害按 0 个算）
            counter = ":".join(parts[3:]) if len(parts) > 3 else ""
            if not counter:
                raise ValueError(f"{tag}：缺少计数源（per:<counter>）")
            out["plus"] += n * _eval_counter(self, counter)
        elif kind == "if":
            # ⚠ 用 `":".join(parts[3:])` 而不是 `parts[3]`：**带参数条件**（如
            #   `if:own_active_energy_type:lightning`）本身含冒号，只取 parts[3]
            #   会把颜色丢掉 → 变成查不到的 `own_active_energy_type` 而抛错。
            cond = ":".join(parts[3:]) if len(parts) > 3 else ""
            if not cond:
                raise ValueError(f"{tag}：缺少条件（if:<cond>）")
            if _eval_condition(self, cond):
                out["plus"] += n
        elif kind == "recoil":
            # ``dmg_plus:N:recoil:M``（第四十七批，Gurdurr / Dondozo ex）：
            # 加伤 N 在伤害修正层计入；M 由 `_apply_attack_effects` 结算反伤。
            if len(parts) < 4 or not parts[3].isdigit():
                raise ValueError(f"{tag}：反伤数值缺失/非法")
            out["plus"] += n
        else:
            raise ValueError(f"未知 dmg_plus 形式：{tag}")

    def _accum_dmg_minus(self, tag: str, out: dict) -> None:
        """``dmg_minus:N`` / ``dmg_minus:N:per:<counter>`` → 减伤累加

        卡面「This attack does 60 less damage for each Energy attached to your
        opponent's Active Pokémon.」（Tinkaton）——与 `dmg_plus` 对称，
        但**下限为 0**（在 `_do_attack` 里统一 max(0, …)）。
        """
        parts = tag.split(":")
        n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        kind = parts[2] if len(parts) > 2 else None
        if kind is None:
            out["minus"] += n
        elif kind == "per":
            counter = ":".join(parts[3:]) if len(parts) > 3 else ""
            if not counter:
                raise ValueError(f"{tag}：缺少计数源（per:<counter>）")
            out["minus"] += n * _eval_counter(self, counter)
        else:
            raise ValueError(f"未知 dmg_minus 形式：{tag}")

    def _dmg_set_value(self, tag: str) -> int:
        """``dmg_set:N`` / ``dmg_set:N:per:<counter>`` → 基础伤害值

        与 ``dmg_plus`` 分开实现，避免「set 语义被当成加伤」再次出现。
        """
        parts = tag.split(":")
        n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        kind = parts[2] if len(parts) > 2 else None
        if kind is None:
            return n
        if kind == "per":
            counter = ":".join(parts[3:]) if len(parts) > 3 else ""
            if not counter:
                raise ValueError(f"{tag}：缺少计数源（per:<counter>）")
            return n * _eval_counter(self, counter)
        raise ValueError(f"未知 dmg_set 形式：{tag}")

    def _resolve_coin_tag(self, attacker: FieldPokemon, tag: str,
                          out: dict) -> None:
        """抛币 tag 结算（``coin:<side>:<effect>`` / ``coin_each:<n>:<side>:<eff>``）

        结果同时写入事件日志（``coin``）与 ``out['coins']``——后者供
        ``_apply_coin_non_damage`` 执行**非伤害**子效果，**不重复抛币**。
        """
        if tag.startswith("coin_each:"):
            _, raw_n, side, sub = tag.split(":", 3)
            n = int(raw_n) if raw_n.isdigit() else _eval_counter(self, raw_n)
            # 子效果可带**多币条件**（第二十二批）：
            #   `coin_each:2:heads:dmg_set:90|cond:all_tails:confuse`
            #   `coin_each:4:heads:dmg_set:60|cond:at_least_heads:2:paralyze`
            # `cond:` 段与伤害子效果**共用同一次抛币结果**（绝不重掷）。
            segs = [s for s in str(sub).split("|") if s]
            plain = [s for s in segs if not s.startswith("cond:")]
            conds = [s for s in segs if s.startswith("cond:")]
            hits = 0
            for _ in range(max(0, n)):
                res = "heads" if self.rng.random() < 0.5 else "tails"
                self._log("coin", {"player": self.current, "result": res,
                                   "tag": tag, "each_of": n})
                if res == side:
                    hits += 1
            out["coins"].append({"tag": tag, "side": side,
                                 "sub": "|".join(plain),
                                 "result": side if hits else "none",
                                 "hits": hits})
            if hits:
                self._apply_coin_sub(attacker, "|".join(plain), out,
                                     repeat=hits)
            elif any(x.startswith("dmg_set:") for x in plain):
                # 全反面 + 「does N damage for each heads」→ 伤害必须是 **0**。
                # 不调用的话 `mods["set"]` 保持 None，引擎会回落到印刷伤害
                # （第七十八批实测 31 个招式在全反面时打出满伤）。
                self._apply_coin_sub(attacker, "|".join(plain), out, repeat=0)
            if conds:
                heads = hits if side == "heads" else max(0, n - hits)
                for cond in conds:
                    ok, csub = _coin_cond_result(cond, n, heads)
                    self._log("coin_cond", {"player": self.current,
                                            "cond": cond, "ok": ok,
                                            "heads": heads, "n": n})
                    if ok and csub:
                        # 条件命中的非伤害子效果 → 由 `_apply_coin_non_damage`
                        # 按「同一次抛币」执行一次（side/result 均为 `cond`）。
                        out["coins"].append({"tag": tag, "side": "cond",
                                             "sub": csub, "result": "cond",
                                             "hits": 1, "cond": cond})
            return
        _, side, sub = tag.split(":", 2)
        res = "heads" if self.rng.random() < 0.5 else "tails"
        self._log("coin", {"player": self.current, "result": res, "tag": tag})
        out["coins"].append({"tag": tag, "side": side, "sub": sub,
                             "result": res, "hits": 1 if res == side else 0})
        if res == side:
            self._apply_coin_sub(attacker, sub, out, repeat=1)

    def _resolve_coin_all_tag(self, attacker: FieldPokemon, tag: str,
                              out: dict) -> None:
        """``coin_all:N:<side>:<sub>`` —— N 枚硬币**全部**为 side 才生效

        卡面原型 Bewear「Flip 2 coins. If both of them are heads, this attack
        does 100 more damage.」；与 ``coin_each``（每枚正面各生效一次）不同。
        """
        _, raw_n, side, sub = tag.split(":", 3)
        n = int(raw_n) if raw_n.isdigit() else _eval_counter(self, raw_n)
        results = []
        for _ in range(max(0, n)):
            res = "heads" if self.rng.random() < 0.5 else "tails"
            results.append(res)
            self._log("coin", {"player": self.current, "result": res, "tag": tag})
        ok = bool(results) and all(r == side for r in results)
        out["coins"].append({"tag": tag, "side": side, "sub": sub,
                             "result": side if ok else "none",
                             "hits": len(results) if ok else 0})
        if ok:
            self._apply_coin_sub(attacker, sub, out, repeat=1)

    def _resolve_coin_until_tails(self, attacker: FieldPokemon, tag: str,
                                  out: dict) -> None:
        """``coin_until_tails:<kind>[:<param>]`` —— 连续抛币直到反面

        卡面原型：「Flip a coin until you get tails. … for each heads」
        **正面数在结算时才知道**（几何分布），解析期折算成任何常数都是静默改强度，
        所以这里是唯一的掷币点。

        kind（2026-11-02 第四十批把「伤害唯一形态」扩成通用计数源）：
          · ``dmg_plus:<per>``          在印刷伤害上**加** per × 正面数
                                       （Breloom / Tyrogue / Voltorb ex）
          · ``dmg_set:<per>``           印刷伤害**按正面数重算**，0 正面 = 0
                                       （Marill TEF 64 / Mudbray / Beartic / Jigglypuff）
          · ``mill:opp``                磨对手牌库 正面数 张（Tyrantrum POR 45）
          · ``shuffle_opp_hand``        对手手牌随机正面数 张洗回其牌库（Houndstone）
          · ``search:any``              牌库顶 正面数 张入手（Gholdengo MEG 99）

        非伤害三种只把正面数**记进 out**，真正执行在 `_apply_attack_effects`
        （那里才有「效果是否被免疫」的上下文；在伤害修正层直接动手会绕过护盾判断）。
        上限 100 次，防极端死循环（概率 2^-100 可忽略，且日志完整可复现）。
        """
        parts = tag.split(":")
        kind = parts[1] if len(parts) > 1 else ""
        per = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        if kind in ("dmg_plus", "dmg_set"):
            if per <= 0:
                raise ValueError(f"coin_until_tails tag 非法：{tag}")
        elif kind == "mill":
            if len(parts) < 3 or parts[2] not in ("opp", "self"):
                raise ValueError(f"coin_until_tails tag 非法：{tag}")
        elif kind == "shuffle_opp_hand":
            if len(parts) != 2:
                raise ValueError(f"coin_until_tails tag 非法：{tag}")
        elif kind == "search":
            if len(parts) < 3 or parts[2] != "any":
                raise ValueError(f"coin_until_tails tag 非法：{tag}")
        elif kind == "discard_energy":
            # 「for each heads, discard an Energy from your opponent's Active
            # Pokémon」（LugiaEX PRE 82「Hyper Whirlpool」）——弃能落在**对手**
            # 战斗位，按正面数计次；落到常数 1 就是静默削弱。
            if len(parts) != 3 or parts[2] != "opp":
                raise ValueError(f"coin_until_tails tag 非法：{tag}")
        else:
            raise ValueError(f"coin_until_tails 未支持的子效果：{tag}")
        heads = 0
        for _ in range(100):
            res = "heads" if self.rng.random() < 0.5 else "tails"
            self._log("coin", {"player": self.current, "result": res,
                               "tag": tag})
            if res == "tails":
                break
            heads += 1
        out["coins"].append({"tag": tag, "side": "heads", "sub": "",
                             "result": "heads" if heads else "none",
                             "hits": heads, "until_tails": True})
        if kind == "dmg_plus":
            if heads:
                out["plus"] += per * heads
        elif kind == "dmg_set":
            # 覆盖基础伤害：0 正面必须是 0（写成 `if heads:` 会让 0 正面保留
            # 印刷伤害，正是「does N damage for each heads」最容易落错的地方）
            out["set"] = per * heads
        else:
            out.setdefault("until_tails", []).append(
                {"tag": tag, "heads": heads})

    def _apply_coin_sub(self, attacker: FieldPokemon, sub: str, out: dict,
                        repeat: int = 1) -> None:
        """抛币命中的子效果 → 归入伤害修正层（其余子效果留待 _apply_attack_effects）

        子效果可复合（``a|b``，Floragato「+30 damage, and heal 30」）——逐段结算，
        但**共用同一次抛币结果**（绝不重复抛币）。
        """
        if sub.startswith("dmg_set:"):
            # set 语义不是「重复叠加」：coin_each 命中 N 枚 → 基础伤害 = N × 命中数。
            # 必须放在循环外，否则 repeat 次循环会变成 N × repeat²。
            # ⚠ `repeat` 可以是 **0**（全反面）：2026-11-04 第七十八批修 —— 此前
            # 这里写 `max(1, repeat)` 且 coin_each 只在命中时才调用，于是「Flip 3
            # coins, 30 damage for each heads」在 0 正面时回落到印刷伤害 30
            # （应为 0），实测 31 个招式受影响。
            out["set"] = (out["set"] or 0) + self._dmg_set_value(sub) * max(0, repeat)
            return
        for _ in range(max(1, repeat)):
            for one in str(sub).split("|"):
                if not one:
                    continue
                if one == "dmg_zero":
                    out["zero"] = True
                elif one.startswith("dmg_plus:"):
                    self._accum_dmg_plus(one, out)
                elif one == "ignore_weakness":
                    out["ignore_weakness"] = True
                elif one == "ignore_resistance":
                    out["ignore_resistance"] = True
            # 非伤害类子效果（discard_energy / shield: / status…）由
            # _apply_attack_effects 的 coin 分支统一处理，这里不重复执行

    def _apply_until_tails_effect(self, attacker: FieldPokemon, tag: str,
                                  pre_mods: Optional[dict],
                                  plan: Optional[list] = None,
                                  action: Optional[dict] = None) -> None:
        """「Flip a coin until you get tails」的**非伤害**子效果执行点

        卡面（2026-11-02 第四十批）：
          · ``mill:opp``           磨对手牌库（Tyrantrum POR 45「Wreak Havoc」）
          · ``shuffle_opp_hand``   对手手牌随机 N 张洗回其牌库（Houndstone）
          · ``search:any``         牌库顶 N 张入手（Gholdengo MEG 99）
          · ``discard_energy:opp`` 对手战斗位按正面数弃能（LugiaEX PRE 82）

        与 `_apply_coin_non_damage` 同一条纪律：**不得重新抛币**。抛币已在
        `_attack_mods` 阶段完成（同一次攻击只掷一次），这里只消费记录下来的正面数。
        找不到记录时才现抛（直接调 `_apply_attack_effects` 的路径，如特性 / 测试）。

        全反面 = 「for each heads」为 0 次 → 合法无效果，**不报错**（与官方一致）。
        """
        parts = str(tag).split(":")
        kind = parts[1] if len(parts) > 1 else ""
        if kind in ("dmg_plus", "dmg_set"):
            # 伤害类已在 `_attack_mods` → `_resolve_coin_until_tails` 并入伤害层，
            # 这里再执行一次就是**翻倍**。明确跳过并留注释，避免以后被当成
            # 「未知 tag 被静默忽略」而误加回来。
            return
        rec = None
        for it in ((pre_mods or {}).get("until_tails") or []):
            if str(it.get("tag")) == tag:
                rec = it
                break
        if rec is not None:
            heads = int(rec.get("heads") or 0)
        else:
            heads = 0
            for _ in range(100):
                res = "heads" if self.rng.random() < 0.5 else "tails"
                self._log("coin", {"player": self.current, "result": res,
                                   "tag": tag})
                if res == "tails":
                    break
                heads += 1
        if heads <= 0:
            self._log("no_effect", {"tag": tag, "reason": "无正面"})
            return
        if kind == "mill":
            victim = (1 - self.current
                      if len(parts) > 2 and parts[2] == "opp" else self.current)
            vp = self.players[victim]
            moved = 0
            for _ in range(heads):
                if not vp.deck:
                    break
                self._to_discard(vp, vp.deck.pop())
                moved += 1
            self._log("mill", {"player": victim, "n": moved,
                               "left": len(vp.deck)})
        elif kind == "shuffle_opp_hand":
            self._shuffle_opp_hand(heads)
        elif kind == "search":
            p = self.players[self.current]
            got = _take_any_from_deck(p, heads)
            self.rng.shuffle(p.deck)     # 卡面「Then, shuffle your deck」
            self._log("search", {"player": self.current, "n": got,
                                 "kind": "any", "heads": heads})
        elif kind == "discard_energy":
            # 复用既有 `discard_energy:<n>:opp` 的**整条派发**（含护盾 / 效果免疫 /
            # 后备免疫判定与日志），不另写一份弃能逻辑 —— 两份实现迟早会漂。
            self._apply_attack_effects(attacker,
                                       (f"discard_energy:{heads}:opp",),
                                       plan, action, pre_mods)
        else:
            raise ValueError(f"coin_until_tails 未支持的子效果：{tag}")

    def _apply_coin_non_damage(self, attacker: FieldPokemon, tag: str,
                               pre_mods: Optional[dict],
                               plan: Optional[list] = None,
                               action: Optional[dict] = None) -> None:
        """执行抛币命中的**非伤害**子效果

        关键：**不得重新抛币**——否则同一 tag 会掷两次、结果不一致且不可复现。
        这里只消费 ``_attack_mods`` 已记录在 ``pre_mods['coins']`` 里的结果。
        """
        flips = (pre_mods or {}).get("coins") or []
        hit = [f for f in flips if f["tag"] == tag and f["result"] == f["side"]]
        if not hit:
            return
        for f in hit:
            sub = f["sub"]
            # `coin_all` 记录的是「抛了几枚」，但子效果**只执行一次**；
            # `coin_each` 才是「每命中一枚各执行一次」。
            repeat = 1 if str(f["tag"]).startswith("coin_all:") else \
                max(1, f["hits"])
            for one in str(sub).split("|"):
                if not one or one.startswith(_DAMAGE_MOD_PREFIXES):
                    continue                # 伤害类已并入伤害结算，勿重复
                # 卡面英文状态词 → 引擎规范状态名（`paralyzed` → `paralyze` 等）。
                # 不归一的话会派发到「未知 tag」分支被**静默忽略**（状态永不生效）。
                one = _COIN_STATUS_ALIASES.get(one, one)
                for _ in range(repeat):
                    # 递归深度恒为 1：子效果不含 coin: 前缀（构造时不生成嵌套抛币）
                    self._apply_attack_effects(attacker, (one,), plan, action)

    def _apply_coin_per_target(self, attacker: FieldPokemon, tag: str,
                               action: Optional[dict],
                               pre_mods: Optional[dict] = None) -> bool:
        """``coin_per_target:<per>:scope:opp`` —— 每个对手宝可梦**各抛一枚**

        卡面原型（Mega Zygarde ex「Nullifying Zero」）：「For each of your
        opponent's Pokémon, flip a coin. If heads, this attack does N damage to
        that Pokémon. (Don't apply Weakness and Resistance for Benched Pokémon.)」

        ⚠ 两种错误折叠：
          · `snipe:opp:N:all` → 每只**必中**（静默加强）；
          · `coin:heads:snipe:…` → 只抛一次，全中或全不中（概率分布完全不同）。
        命中集合交给 `_apply_snipe` 执行，**复用**其「战斗位走弱抗 / 后备原始值 /
        免疫与护盾拦截」口径，不另写一份伤害逻辑。

        返回 ``True`` 表示对局已结束。
        """
        parts = str(tag).split(":")
        if len(parts) != 4 or not parts[1].isdigit() \
                or parts[2] != "scope" or parts[3] != "opp":
            raise ValueError(f"coin_per_target tag 非法：{tag}")
        per = int(parts[1])
        if per <= 0:
            raise ValueError(f"coin_per_target 伤害非法：{tag}")
        cands = self._snipe_candidates("opp")
        hits = []
        for pos, fp in cands:
            res = "heads" if self.rng.random() < 0.5 else "tails"
            self._log("coin", {"player": self.current, "result": res,
                               "tag": tag, "target": fp.name})
            if res == "heads":
                hits.append(pos)
        if not hits:
            self._log("no_effect", {"tag": tag, "reason": "全部反面"})
            return False
        # `:all` + `action["snipe"]` = 只在**抛出正面**的目标上结算（不重掷）。
        return self._apply_snipe(f"snipe:opp:{per}:all", {"snipe": hits},
                                  (), pre_mods)

    def _coin_prevent_of(self, fp: Optional[FieldPokemon]) -> bool:
        """``passive:coin_prevent:<color>`` —— 本次伤害是否被抛币挡下

        卡面原型（Fezandipiti「Adrena-Pheromone」）：「If this Pokémon has any
        {D} Energy attached and is damaged by an attack, flip a coin. If heads,
        prevent that damage.」

        只在**伤害应用点**调用（主伤害 / spread / spread_free / snipe），
        且调用方必须已确认本次伤害 > 0 —— 伤害为 0 不算「被伤害」，不该抛币。
        伤害指示物（`counters:`）不是「damage from an attack」，**不触发**。
        """
        if fp is None:
            return False
        for ab in fp.pdef.abilities:
            tag = str(ab.tag or "")
            if not tag.startswith("passive:coin_prevent:"):
                continue
            parts = tag.split(":")
            if len(parts) != 3 or parts[2] not in TYPES:
                raise ValueError(f"passive:coin_prevent 形态非法：{tag}")
            if fp.energy_of_type(parts[2]) <= 0:
                continue
            res = "heads" if self.rng.random() < 0.5 else "tails"
            self._log("coin", {"player": 1 - self.current, "result": res,
                               "tag": tag, "ability": ab.name})
            if res == "heads":
                self._log("ability_trigger", {
                    "player": 1 - self.current, "ability": ab.name,
                    "kind": "coin_prevent", "target": fp.name})
                return True
        return False

    def _ability_coin_survive(self, defender: FieldPokemon) -> bool:
        """``passive:coin_survive:<hp>`` —— 抛币免击倒（第七十八批）

        卡面（Mega Hawlucha ex「Tenacious Body」）：「If this Pokémon would be
        Knocked Out by damage from an attack, flip a coin. If heads, this Pokémon
        is not Knocked Out, and its remaining HP becomes N.」

        与 `tool:survive:N` 的区别：**不要求受伤前满 HP**、不是道具（不弃置）、
        且正面才生效（反面照常击倒）。只在 `_do_attack` 的击倒结算点调用。
        """
        for ab in defender.pdef.abilities:
            tag = str(ab.tag or "")
            if not tag.startswith("passive:coin_survive:"):
                continue
            parts = tag.split(":")
            if len(parts) != 3 or not parts[2].isdigit():
                raise ValueError(f"passive:coin_survive 形态非法：{tag}")
            hp = int(parts[2])
            if hp <= 0:
                raise ValueError(f"passive:coin_survive 剩余 HP 非法：{tag}")
            res = "heads" if self.rng.random() < 0.5 else "tails"
            self._log("coin", {"player": 1 - self.current, "result": res,
                               "tag": tag, "ability": ab.name})
            if res != "heads":
                continue
            defender.damage = max(0, defender.max_hp - hp)
            self._log("ability_trigger", {
                "player": 1 - self.current, "ability": ab.name,
                "kind": "coin_survive", "target": defender.name, "hp": hp})
            return True
        return False

    def _ability_survive(self, defender: FieldPokemon) -> bool:
        """``passive:survive:<hp>`` —— 满 HP 时免于被招式击倒（第八十批）

        卡面（Pikachu ex「Resolute Heart」/ Crustle「Mysterious Rock Inn」）：
        「If this Pokémon has **full HP** and would be Knocked Out by damage
        from an attack, it is not Knocked Out, and its remaining HP becomes N.」

        与两个相邻机制的边界（**不可互换**）：
        · ``tool:survive:N``：挂在**道具**上，触发后道具弃置；
        · ``passive:coin_survive:N``：不要求满 HP，且要抛币、反面照常击倒；
        · 本 tag：**特性**、**必定生效**、但**只在受伤前满 HP 时**（由调用点的
          ``was_full`` 保证），且不弃置任何卡牌。

        只在 `_do_attack` 击倒结算点调用；改 damage 后 `is_ko` 自动回退。
        """
        for ab in defender.pdef.abilities:
            tag = str(ab.tag or "")
            if not tag.startswith("passive:survive:"):
                continue
            parts = tag.split(":")
            if len(parts) != 3 or not parts[2].isdigit():
                raise ValueError(f"passive:survive 形态非法：{tag}")
            hp = int(parts[2])
            if hp <= 0:
                raise ValueError(f"passive:survive 剩余 HP 非法：{tag}")
            defender.damage = max(0, defender.max_hp - hp)
            self._log("ability_trigger", {
                "player": 1 - self.current, "ability": ab.name,
                "kind": "survive", "target": defender.name, "hp": hp})
            return True
        return False

    def _team_shield_reduce(self, fp: Optional[FieldPokemon],
                            attacker: Optional[FieldPokemon] = None) -> int:
        """``team_shield:reduce:<n>:type:<color>`` —— 己方全队限时减伤（≥0）

        卡面（Iron Defender）：「During your opponent's next turn, all of your
        {M} Pokémon take 30 less damage from attacks … (This includes new
        Pokémon that come into play.)」——玩家级状态，**新上场的宝可梦同样受益**。
        到期：`team_shield_until <= 持有方 turn_counts` 时失效。
        ⚠ 卡面限定「from attacks from **your opponent's** Pokémon」→ 己方招式
        给自己宝可梦造成的伤害**不减免**（snipe/spread 的自方目标）。
        """
        if fp is None:
            return 0
        owner = None
        for i, pl in enumerate(self.players):
            if pl.active is fp or any(fp is b for b in pl.bench):
                owner = i
                break
        if owner is None:
            return 0
        if attacker is not None:
            atk_side = next((i for i, pl in enumerate(self.players)
                             if pl.active is attacker
                             or any(attacker is b for b in pl.bench)), None)
            if atk_side == owner:
                return 0
        p = self.players[owner]
        if not p.team_shield_n or not p.team_shield_type:
            return 0
        if p.team_shield_until <= self.turn_counts[owner]:
            return 0
        if str(fp.pdef.ptype).lower() != p.team_shield_type:
            return 0
        return p.team_shield_n

    def _evolve_from_deck(self, p: PlayerState, fp: FieldPokemon,
                          no_ability: bool = False) -> None:
        """``evolve_from_deck``：从牌库检索「可进化此宝可梦的卡」直接进化

        卡面原型：「Search your deck for a card that evolves from this Pokémon and
        put it onto this Pokémon to evolve it.」——属**招式内进化**。
        语义：「可以」→ 牌库无目标时**不算失败**（不抛错，仅记录无效果）。

        ``no_ability=True``（第九十二批，Salvatore TEF 160）：「a card that
        **has no Abilities** and evolves from …」——只允许检索**无特性**的进化卡。
        不区分就会搜到带特性的进化卡，是静默放宽。
        """
        if fp is None or p is None:
            return
        idx = None
        for i in range(len(p.deck) - 1, -1, -1):
            c = p.deck[i]
            if isinstance(c, PokeRef) and c.pdef.stage > 0 \
                    and c.pdef.evolves_from == fp.pdef.name \
                    and fp.pdef.stage + 1 == c.pdef.stage:
                if no_ability and c.pdef.abilities:
                    continue                # 「has no Abilities」限定
                idx = i
                break
        if idx is None:
            self._log("evolve", {"player": self.current, "from": fp.name,
                                 "to": None, "reason": "牌库无可进化卡"})
            return
        ref = p.deck.pop(idx)
        old = fp.name
        fp.evolve_to(ref.pdef)
        fp.evolved_turn = self.turn_number
        fp.evolved_from_name = old
        self.rng.shuffle(p.deck)            # 检索后洗牌（引擎统一约定）
        self._log("evolve", {"player": self.current, "from": old,
                             "to": fp.name, "from_zone": "deck"})
        self._trigger_evolve_attach(fp)

    def _evolve_fp_from_deck(self, p: PlayerState, fp: FieldPokemon) -> bool:
        """在牌库中找「可进化**这一只**」的卡并直接进化 → 是否进化成功

        逐只进化的**公共实现**（`_evolve_each_bench` / `_evolve_each_pick` 共用）。
        判据：`evolves_from` 与当前卡名一致、且阶数恰好高一阶。
        找不到 → 返回 False（卡面是「可以」，牌库没有目标就跳过，不抛错）。

        ⚠ 不要在两个调用方里各写一份检索：两份实现迟早会漂（一边改了判据、另一边
        没改），而「进化到了错的东西」在日志里长得跟正常进化一模一样。
        """
        want = fp.pdef.name
        idx = None
        for j in range(len(p.deck) - 1, -1, -1):
            c = p.deck[j]
            if isinstance(c, PokeRef) and c.pdef.stage > 0 \
                    and c.pdef.evolves_from == want \
                    and fp.pdef.stage + 1 == c.pdef.stage:
                idx = j
                break
        if idx is None:
            return False
        ref = p.deck.pop(idx)
        old = fp.name
        fp.evolve_to(ref.pdef)
        fp.evolved_turn = self.turn_number
        fp.evolved_from_name = old
        self._log("evolve", {"player": self.current, "from": old,
                             "to": fp.name, "from_zone": "deck"})
        self._trigger_evolve_attach(fp)
        return True

    def _evolve_each_bench(self, p: PlayerState) -> int:
        """逐只后备各进化一次（``evolve_each_bench`` · Reuniclus「Cellular Ascension」）

        卡面：「For each of your Benched Pokémon, search your deck for a card that
        evolves from that Pokémon and put it onto that Pokémon to evolve it.
        Then, shuffle your deck.」

        对**每一只**后备：在牌库中找 `evolves_from` 与它卡名一致、且阶数恰好
        高一阶的进化卡，直接进化。牌库没有目标的后备只是不进化（官方「可以」），
        不抛错。最后洗牌（卡面「Then, shuffle your deck」）。
        """
        done = 0
        for fp in list(p.bench):
            if self._evolve_fp_from_deck(p, fp):
                done += 1
        if done:
            self.rng.shuffle(p.deck)        # 检索后洗牌（引擎统一约定）
        return done

    def _evolve_each_pick(self, p: PlayerState, mx: int, scope: str) -> int:
        """「**选**至多 N 只，各进化一次」（``evolve_each_pick:<max>:<scope>``）

        卡面原型（Team Rocket's Nidorina DRI 115「Dark Awakening」）：
        「Choose up to 2 of your {D} Pokémon. For each of those Pokémon, search
         your deck for a card that evolves from that Pokémon and put it onto that
         Pokémon to evolve it. Then, shuffle your deck.」

        与 `_evolve_each_bench` 的**唯一**差别是目标集合：这边是「选出」的至多 N
        只（可含战斗位），确定性默认按 active→bench 顺序取前 N 只（与
        `_do_attach_each_pick` 同一口径）。进化本身的检索/落位走同一 helper。
        ⚠ 拿 `_evolve_each_bench`（全部后备）顶替会让「至多 2 只」变成「全部后备」。
        """
        picked = _spread_targets(p, scope)[:max(0, int(mx))]
        done = 0
        for fp in picked:
            if self._evolve_fp_from_deck(p, fp):
                done += 1
        if done:
            self.rng.shuffle(p.deck)
        return done

    def _apply_attack_effects(self, attacker: FieldPokemon, effects: tuple,
                              plan: Optional[list] = None,
                              action: Optional[dict] = None,
                              pre_mods: Optional[dict] = None) -> None:
        """结算招式附加效果

        ``plan``：E5 任意分配的目标计划 [{"pos": bench索引, "dmg": ...}, ...]
        ``action``：原始动作（读取 ``bench``/``target`` 等显式选择，缺省时取确定性默认值）
        ``pre_mods``：``_attack_mods`` 的结果（含抛币结果），用于执行抛币命中的**非伤害**
        子效果（弃能 / 护盾 / 状态等）——伤害类子效果已在那一步并入伤害。

        tag 家族（v1.3 扩展 · v1.4 续扩，见 cards_real.json meta.engine_tags）：
          · 状态：poison / burn / sleep / paralyze / confuse
          · 放置：spread_free:N（任意分配）· spread:N:per（随机 n 只各 per 点）·
            snipe:<scope>:<per>[:<count>]（**选定**目标各 per 点 · 2026-09-22 新增）
          · 自伤 / 回复：self:N（含卡面反伤「does N damage to itself」）· heal:N
          · 抽牌：draw:N · draw_to:N（抽到指定手牌数）
          · 弃牌：discard:N（弃自身手牌）· discard_energy:N（弃自身 active 能量）
          · 换位：switch_self（自己换下）· gust（把对手后备拉为 active）
          · 附能：attach:N[:type][:zone]（弃牌堆优先、无则牌库）
          · 指示物：move_damage:N[:all]（自身 → 对手 active）
          · 回收：recover:N[:kind]（弃牌堆取入手）
          · 进化：evolve_from_deck（从牌库检索进化卡直接进化）
          · 护盾：shield:reduce:N · shield:prevent_all（v1.4）
          · 抛币：coin:heads:<tag> · coin:tails:<tag> · coin_each:N:tails:<tag>（v1.4）

        **被动免疫**：``passive:bench_immune`` 的宝可梦位于后备时，免疫对其造成的
        招式伤害（见 spread 分支）与效果。
        """
        p = self.players[self.current]
        opp = self.players[1 - self.current]
        action = action or {}
        # 守方是否处于「免疫招式伤害与效果」状态（护盾或后备被动）
        d_active = opp.active

        def _opp_blocked(fp: Optional[FieldPokemon], on_bench: bool = False) -> bool:
            """目标是否免疫指令效果（护盾 / 后备被动 / 效果免疫 / 静态免伤）"""
            if fp is None:
                return True
            # 护盾：只有卡面写明「from and effects of attacks」的才挡效果；
            # `from:<src>` 限定同样作用于效果（Acerola's Mischief 只挡 {ex}）
            if _shield_blocks_effects(fp, attacker):
                return True
            if families.effect_immune(self, fp, "attack"):
                return True
            # 特殊能量（Mist / Rock Fighting）：招式**效果**不作用于持有者
            if special_energy.effect_immune(fp, "attack"):
                return True
            if on_bench and _is_bench_effect_immune(fp):
                return True
            # 静态免伤被动里写明「effects」的（Milotic / Carracosta / Rabsca）
            if _self_immune(fp, attacker, None, effects_only=True):
                return True
            if on_bench and _team_bench_immune(opp, fp, attacker, None,
                                               effects_only=True):
                return True
            return False

        # 「in this way」后续（2026-10-28 第十批）：`:if_prev` 后缀的 tag 只有在
        # **前一个可计数动作真的发生了**时才执行（回复 > 0 / 附能 > 0 / 弃牌 > 0）。
        prev_ok = True
        for raw_tag in effects:
            tag = raw_tag
            if str(tag).startswith("fx:attack_cost:"):
                # 静态费用改写（第一百一十二批）：由 `families.attack_cost`
                # 在**合法动作校验**时读取，不是一次性动作 —— 落到
                # `families.apply_action` 会报「未知 fx tag（无实现）」。
                continue
            if str(tag).startswith(("use_only:", "use_not:")):
                # 招式**使用条件**，不是效果 —— 由 `_attack_usable` 在合法动作
                # 生成时判定；落到这里若不跳过会报「未知效果」。
                continue
            if str(tag).startswith("pre:"):
                # 已在 `_do_attack` 的**伤害前阶段**执行过（2026-10-29 第二十五批）。
                # 明确跳过，避免以后误当成「未知 tag 被静默忽略」。
                continue
            if tag == "discard_stadium":
                # 「If your opponent has a Stadium in play, discard it.」
                # （第一百零七批，Chi-Yu「Scorching Earth」）——招式侧此前没有
                # 这一支（只有特性/训练家侧有），不补就会「只锁不弃」。
                _nm = self._apply_discard_stadium()
                self._log("discard_stadium", {"player": self.current,
                                              "name": _nm})
                continue
            if str(tag).endswith(":if_pre_tool"):
                # 「If you discarded a Pokémon Tool in this way, …」（N's Joltik）——
                # 只有伤害前阶段**真的弃掉了道具**才执行。此前该从句被整段忽略，
                # 状态变成无条件施加（静默加强）。
                tag = str(tag)[:-len(":if_pre_tool")]
                if not self._pre_tools_discarded:
                    self._log("no_effect", {"tag": raw_tag,
                                            "reason": "伤害前阶段未弃置道具"})
                    continue
            if str(tag).endswith(":if_prev"):
                tag = str(tag)[:-len(":if_prev")]
                if not prev_ok:
                    self._log("no_effect", {"tag": raw_tag,
                                            "reason": "前序动作未发生"})
                    continue
            # 默认「本动作算发生了」；会报告数量的分支自行覆盖
            prev_ok = True
            if tag.startswith("coin:") or tag.startswith("coin_each:") \
                    or tag.startswith("coin_all:"):
                self._apply_coin_non_damage(attacker, tag, pre_mods, plan, action)
            elif tag.startswith("coin_until_tails:"):
                # 「Flip a coin until you get tails」+ 非伤害子效果
                # （2026-11-02 第四十批）：抛币在 `_attack_mods` 已做，这里只执行
                self._apply_until_tails_effect(attacker, tag, pre_mods, plan, action)
            elif tag.startswith("status_self:"):
                # 自身陷入状态（「This Pokémon is now Confused」）
                st = tag.split(":", 1)[1]
                if st not in STATUSES:
                    raise ValueError(f"未知状态：{st}")
                attacker.status.add(st)
                self._log("status", {"player": self.current,
                                     "target": attacker.name, "status": st,
                                     "side": "self"})
            elif tag.startswith("status_multi:"):
                # 招式侧多状态（第五十一批）：「is now Burned **and** Confused」。
                # ⚠ 此前只落第一个状态，第二个被静默丢掉（5 个招式受影响）。
                if opp.active is not None and not opp.active.is_ko:
                    if _opp_blocked(opp.active):
                        self._log("blocked", {"target": opp.active.name,
                                              "by": "prevent_all"})
                        continue
                    # 逐状态判定（第一百一十一批）：同训练家侧口径
                    _ms = str(tag).split(":", 1)[1].split("+")
                    _mb = {x for x in _ms if self._status_blocked(opp.active, x)}
                    if _mb and len(_mb) == len(_ms):
                        self._log("blocked", {"target": opp.active.name,
                                              "by": "status_immune"})
                        continue
                    _ap = []
                    for _st0 in _ms:
                        if _st0 not in STATUSES:
                            raise ValueError(f"未知状态：{_st0}")
                        if _st0 in _mb:
                            continue
                        opp.active.status.add(_st0)
                        _ap.append(_st0)
                    self._log("status", {"player": 1 - self.current,
                                         "target": opp.active.name,
                                         "status": "+".join(_ap)})
            elif tag in ("poison", "burn", "sleep", "paralyze", "confuse"):
                if opp.active is not None and not opp.active.is_ko:
                    if _opp_blocked(opp.active):
                        self._log("blocked", {"target": opp.active.name,
                                              "by": "prevent_all"})
                        continue
                    if self._status_blocked(opp.active, tag):
                        # 化石卡 / Festival Grounds：「can't be affected by any
                        # Special Conditions」；或卡面被动（只免疫某一个）
                        self._log("blocked", {"target": opp.active.name,
                                              "by": "status_immune"})
                        continue
                    opp.active.status.add(tag)
                    self._log("status", {"player": 1 - self.current,
                                         "target": opp.active.name,
                                         "status": tag})
            elif tag.startswith("opp_hand:"):
                # 对手手牌操作（2026-10-30 第三十二批）：Rotom / Luxray ex /
                # N's Purrloin / Whimsicott ex 之外的招式形态。
                # 无「目标免疫」检查：改的是手牌，与战斗位免疫无关。
                self._do_opp_hand(p, tag, action)
            elif tag.startswith("attack_lock:"):
                if tag.startswith("attack_lock:opp") \
                        and _opp_blocked(opp.active):
                    self._log("blocked", {"target": opp.active.name,
                                          "by": "prevent_all"})
                    continue
                self._apply_attack_lock_tag(attacker, tag, action=action)
            elif tag.startswith("retreat_lock:"):
                # 只对**对手**的撤退锁定检查目标免疫；`retreat_lock:self` 是
                # 攻击方自己的限制，不能因对手战斗位的免疫而失效。
                if tag.startswith("retreat_lock:opp") and _opp_blocked(opp.active):
                    self._log("blocked", {"target": opp.active.name,
                                          "by": "prevent_all"})
                    continue
                self._apply_retreat_lock_tag(tag)
            elif tag.startswith("play_lock:"):
                self._apply_play_lock_tag(tag)
            elif tag.startswith("attack_denial:"):
                # 「if the Defending Pokémon tries to use an attack, … coin. If
                # tails, that attack doesn't happen.」—— 挂在**对手战斗位**身上，
                # 覆盖其下一个回合（到期口径同 attack_lock）
                if opp.active is None:
                    continue
                if _opp_blocked(opp.active):
                    self._log("blocked", {"target": opp.active.name,
                                          "by": "prevent_all"})
                    continue
                opp.active.attack_denial_until = self._lock_until(1 - self.current)
                self._log("attack_denial", {"player": 1 - self.current,
                                            "target": opp.active.name,
                                            "mode": tag.split(":")[1]})
            elif tag.startswith(("attack_cost_up:", "retreat_cost_up:")):
                # 「… attacks used by the Defending Pokémon cost {C} more, and its
                # Retreat Cost is {C} more.」（Rillaboom）
                parts = tag.split(":")
                if len(parts) != 4 or not parts[3].isdigit():
                    raise ValueError(f"非法下回合费用 tag：{tag}")
                if opp.active is None:
                    continue
                if _opp_blocked(opp.active):
                    self._log("blocked", {"target": opp.active.name,
                                          "by": "prevent_all"})
                    continue
                n = int(parts[3])
                if parts[0] == "attack_cost_up":
                    opp.active.attack_cost_up_n = n
                    opp.active.attack_cost_up_until = self._lock_until(
                        1 - self.current)
                else:
                    opp.active.retreat_up_n = n
                    opp.active.retreat_up_until = self._lock_until(
                        1 - self.current)
                self._log(parts[0], {"player": 1 - self.current,
                                     "target": opp.active.name, "n": n})
            elif tag.startswith("shield:"):
                # v1.4：护盾持续到该玩家下个回合开始（见 _begin_turn）
                parts = tag.split(":")
                if parts[1] == "reduce":
                    n = int(parts[2]) if len(parts) > 2 else 0
                    attacker.shield_reduce = n
                    self._log("shield", {"player": self.current,
                                         "target": attacker.name,
                                         "kind": "reduce", "n": n})
                elif parts[1] == "no_weakness":
                    # S2b（2026-11-08）：「During your opponent's next turn, this
                    # Pokémon has no Weakness.」（Archaludon ex「Metal Defender」）
                    # ⚠ 与常驻的 `passive:no_weakness` 是两回事：写进静态层会
                    #   变成整局都没弱点（静默加强）。到期窗口与 shield_reduce 相同。
                    attacker.shield_no_weakness = True
                    self._log("shield", {"player": self.current,
                                         "target": attacker.name,
                                         "kind": "no_weakness"})
                elif parts[1] == "prevent_all":
                    # 卡面两种语义必须分开：只写「prevent all damage」的护盾
                    # **不挡效果**；写明「from and effects of attacks」的才连效果一起挡。
                    qual, eff = _shield_tag_parts(parts[2:])
                    attacker.shield_prevent_all = True
                    attacker.shield_prevent_effects = eff
                    attacker.shield_prevent_qual = qual
                    self._log("shield", {"player": self.current,
                                         "target": attacker.name,
                                         "kind": "prevent_all",
                                         "qual": qual, "effects": eff})
                else:
                    raise ValueError(f"未知 shield 形式：{tag}")
            elif tag.startswith("draw_to:"):
                # v1.4：抽到指定手牌数（已达标则不再抽）
                # B3：支持 `draw_to:N:if:<cond>`（「若…则改为抽到 N」的 instead 形态
                # 由解析层拆成「基准 draw_to + 条件 draw_to」两条，顺序执行等价）
                _dp = tag.split(":")
                target_n = int(_dp[1])
                _dcond = (":".join(_dp[3:])
                          if len(_dp) > 3 and _dp[2] == "if" else None)
                if _dcond is not None and not _eval_condition(self, _dcond):
                    self._log("no_effect", {"tag": tag, "reason": "条件不满足"})
                else:
                    need = target_n - len(p.hand)
                    got = self._safe_draw(self.current, need,
                                          source="attack_effect") if need > 0 else 0
                    self._log("draw_to", {"player": self.current, "size": target_n,
                                          "drew": got})
            elif tag.startswith("evolve_from_deck"):
                self._evolve_from_deck(p, attacker)
            elif tag == "evolve_each_bench":
                # 逐只后备各进化一次（Reuniclus「Cellular Ascension」）
                n = self._evolve_each_bench(p)
                self._log("evolve_each_bench", {"player": self.current, "n": n})
            elif tag.startswith("evolve_each_pick:"):
                # 「选至多 N 只、各进化一次」（2026-11-02 第四十批 ·
                # Team Rocket's Nidorina「Dark Awakening」）
                parts = tag.split(":")
                if len(parts) < 3 or not parts[1].isdigit() \
                        or int(parts[1]) < 1:
                    raise ValueError(f"evolve_each_pick tag 非法：{tag}")
                n = self._evolve_each_pick(p, int(parts[1]), parts[2])
                self._log("evolve_each_pick", {"player": self.current, "n": n})
            elif tag.startswith("coin_per_target:"):
                # 逐目标各抛一枚（第四十五批，Mega Zygarde ex「Nullifying Zero」）
                if self._apply_coin_per_target(attacker, tag, action, pre_mods):
                    return
            elif tag.startswith("status_choose:"):
                # 抛币选状态（第四十五批，Cradily「Selective Slime」）：
                # 卡面「choose Burned, Confused, or Poisoned」三选一；引擎无
                # 交互层 → 确定性默认取**卡面首个**，可用 action 覆盖。
                _opts = [x for x in tag.split(":", 1)[1].split("+") if x]
                if not _opts or any(x not in STATUSES for x in _opts):
                    raise ValueError(f"status_choose tag 非法：{tag}")
                _pick = action.get("status_choice") \
                    if isinstance(action, dict) else None
                if _pick not in _opts:
                    _pick = _opts[0]
                if opp.active is None or opp.active.is_ko:
                    continue
                if _opp_blocked(opp.active):
                    self._log("blocked", {"target": opp.active.name,
                                          "by": "prevent_all"})
                    continue
                if self._status_blocked(opp.active, _pick):
                    self._log("blocked", {"target": opp.active.name,
                                          "by": "status_immune",
                                          "status": _pick})
                    continue
                opp.active.status.add(_pick)
                self._log("status", {"player": 1 - self.current,
                                     "target": opp.active.name,
                                     "status": _pick, "chosen_from": _opts})
            elif tag.startswith("spread_free:"):
                total = int(tag.split(":")[1])
                # 真 bug 修复（v1.4）：对手**后备为空**时该效果无对象可放置，
                # 属「无效果」而非错误。此前 distribute_damage(0, 60) 返回空表，
                # 与 total 不符 → 直接抛错，使 Dragapult ex 幻影俯冲之类招式
                # 在对手空场时无法使用（随机对局中可复现）。
                if not opp.bench:
                    self._log("no_effect", {"tag": tag,
                                            "reason": "对手后备为空"})
                    continue
                plan = plan or [{"pos": i, "dmg": d} for i, d in enumerate(
                    distribute_damage(len(opp.bench), total))]
                if sum(x["dmg"] for x in plan) != total:
                    raise ValueError("spread_free 分配总和与效果不符")
                # 真 bug 修复（v1.3）：伤害指示物**同时**放置，击倒须在全部
                # 放置完成后统一结算——否则中途 _resolve_ko 会移除后备元素，
                # 使后续 pos 越界（6 指示物分配到后备时概率性触发）。
                killed = []
                for item in plan:
                    pos, dmg = int(item["pos"]), int(item["dmg"])
                    if not (0 <= pos < len(opp.bench)):
                        raise ValueError("spread_free 目标越界")
                    fp = opp.bench[pos]
                    if _target_dmg_blocked(self, opp, fp, attacker, dmg,
                                           on_bench=True):
                        self._log("blocked", {"target": fp.name,
                                              "by": "dmg_immune"})
                        continue
                    dmg = max(0, dmg - self._team_shield_reduce(fp, attacker))
                    if dmg <= 0:
                        self._log("blocked", {"target": fp.name,
                                              "by": "team_shield"})
                        continue
                    if self._coin_prevent_of(fp):
                        self._log("blocked", {"target": fp.name,
                                              "by": "coin_prevent"})
                        continue
                    fp.damage += dmg
                    self._log("damage", {"target": fp.name,
                                         "player": 1 - self.current,
                                         "dmg": dmg, "base": dmg,
                                         "spread_free": True})
                    if fp.is_ko:
                        killed.append(fp)
                for fp in killed:
                    self._resolve_ko(1 - self.current, fp)
                    if self.finished:
                        return
            elif tag.startswith("spread:"):
                _, n, per = tag.split(":")
                per = int(per)
                targets = [fp for fp in opp.bench
                           if not _target_dmg_blocked(self, opp, fp, attacker,
                                                      per, on_bench=True)]
                self.rng.shuffle(targets)
                picked = targets[:int(n)]
                killed = []
                for fp in picked:
                    per_hit = max(0, per - self._team_shield_reduce(fp, attacker))
                    if per_hit <= 0:
                        self._log("blocked", {"target": fp.name,
                                              "by": "team_shield"})
                        continue
                    if self._coin_prevent_of(fp):
                        self._log("blocked", {"target": fp.name,
                                              "by": "coin_prevent"})
                        continue
                    fp.damage += per_hit
                    self._log("damage", {"target": fp.name,
                                         "player": 1 - self.current,
                                         "dmg": per_hit, "base": per,
                                         "spread": True})
                    if fp.is_ko:
                        killed.append(fp)
                for fp in killed:
                    self._resolve_ko(1 - self.current, fp)
                    if self.finished:
                        return
            elif tag.startswith("snipe:"):
                if self._apply_snipe(tag, action, effects, pre_mods):
                    return
            elif tag.startswith("mill:"):
                # 磨牌：牌库顶 N 张进弃牌堆（2026-09-22 新增）。
                # 牌库被磨空**不立即判负** —— 与官方一致：判负发生在「该抽牌却抽不到」时
                # （见 `_safe_draw` 的 deckout 分支），所以这里允许磨到 0。
                parts = tag.split(":")
                k = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
                victim = 1 - self.current if (len(parts) > 2 and parts[2] == "opp") \
                    else self.current
                vp = self.players[victim]
                moved = 0
                for _ in range(k):
                    if not vp.deck:
                        break
                    self._to_discard(vp, vp.deck.pop())
                    moved += 1
                self._log("mill", {"player": victim, "n": moved,
                                   "left": len(vp.deck)})
            elif tag.startswith("research:"):
                # 招式侧「Discard your hand and draw N cards」（第五十二批，
                # Raging Bolt ex「Burst Roar」）—— 与训练家侧同语义：弃**全部**
                # 手牌再抽 N（招式本身不在手牌里，无需排除自己）。
                _n = int(tag.split(":")[1])
                _dropped = len(p.hand)
                for _c0 in list(p.hand):
                    self._to_discard(p, _c0)
                p.hand.clear()
                _got = self._safe_draw(self.current, _n, source="attack")
                self._log("research", {"player": self.current,
                                       "discard": _dropped, "draw": _got})
            elif tag.startswith("shuffle_top:"):
                # 「Look at the top N cards … put them back in any order」
                # （Team Rocket's Dottler）—— 只重排**顺序**，不移动任何卡。
                _sp = tag.split(":")
                _n = int(_sp[1]) if len(_sp) > 1 and _sp[1].isdigit() else 1
                _victim = (1 - self.current
                           if len(_sp) > 2 and _sp[2] == "opp" else self.current)
                _vp = self.players[_victim]
                _k = min(_n, len(_vp.deck))
                if _k > 1:
                    _top = _vp.deck[-_k:]
                    self.rng.shuffle(_top)
                    _vp.deck[-_k:] = _top
                self._log("shuffle_top", {"player": _victim, "n": _k})
            elif tag == "cure:self":
                # 第八十一批：「This Pokémon recovers from all Special Conditions.」
                # 只清**出招者自身**；`clear_status()` 与「换下战斗位解状态」
                # 同源。对手的状态、我方后备的状态一律不动。
                had = sorted(str(x) for x in attacker.status)
                attacker.clear_status()
                self._log("cure", {"player": self.current,
                                   "target": attacker.name, "cleared": had})
            elif tag.startswith("deck_bottom_discard:"):
                # S3：弃置牌库**底** N 张（Misty's Psyduck「Flustered Leap」第一段）
                # 「if you do」的条件由合法性保证（牌库非空才可发动），这里再兜一次：
                # 一张都没弃到就**不能**继续执行后续 tag（复合 tag 的 `|if_prev`）。
                _n = int(tag.split(":")[1]) if tag.split(":")[1].isdigit() else 1
                _got = zones.deck_bottom_take(p, _n)
                for _c in _got:
                    self._to_discard(p, _c)
                self._log("deck_bottom_discard", {
                    "player": self.current, "n": len(_got),
                    "cards": [_card_label(c) for c in _got]})
            elif tag == "self_to_deck_top":
                # S3：把**本只**宝可梦（含其附着卡）收回牌库顶
                # 「discard all cards from this Pokémon and put this Pokémon on
                #   top of your deck.」（Misty's Psyduck）
                # ⚠ 只允许后备：卡面限定「if this Pokémon is on your Bench」，
                #   战斗位离场要走「换位 + 补位」而不是直接抽走（否则对手白拿奖赏）。
                if attacker is None or p.active is attacker:
                    raise ValueError("self_to_deck_top 只能作用于后备宝可梦")
                _dropped = sum(attacker.energy.values()) + len(attacker.senergy)
                for _et, _cnt in list(attacker.energy.items()):
                    for _ in range(_cnt):
                        # 基本能量在引擎里只是**计数**（无卡对象），因此只进
                        # 文本层弃牌堆 —— 与击倒结算的口径一致（见 zones 文档）。
                        p.discard.append(f"{_et} energy")
                for _sref in list(attacker.senergy):
                    self._to_discard(p, _sref)
                if attacker.tool:
                    if attacker.tool_ref is not None:
                        self._to_discard(p, attacker.tool_ref)
                    else:
                        p.discard.append(attacker.tool)
                attacker.energy.clear()
                attacker.senergy.clear()
                _drop_tool(attacker)
                if attacker in p.bench:
                    p.bench.remove(attacker)
                zones.put_on_top(p, [PokeRef(attacker.pdef)])
                self._log("self_to_deck_top", {"player": self.current,
                                               "card": attacker.name,
                                               "dropped": _dropped})
            elif tag.startswith("arm:"):
                # S2d：**限时订阅**（Pachirisu「Electrified Incisors」：「During
                # your opponent's next turn, whenever they attach an Energy card
                # from their hand to the Defending Pokémon, place 8 damage
                # counters on that Pokémon.」）
                # 形态：`arm:<事件kind>:<body>:until:<窗口>`，窗口目前只有
                # `opp_next_turn`（对手的下个回合）。
                # ⚠ 目标**此刻**快照：到对手回合时防御宝可梦可能已进化/换下，
                #   重新猜目标就是错打（`match:event_target` 再钉一层）。
                _arm = str(tag).split(":until:")
                if len(_arm) != 2:
                    raise ValueError(f"arm tag 缺 until: 段：{tag}")
                _body_parts = [x for x in _arm[0].split(":") if x]
                if len(_body_parts) < 3:
                    raise ValueError(f"arm tag 缺事件体：{tag}")
                _ev_kind = _body_parts[1]
                _ev_body = ":".join(_body_parts[2:])
                _win = _arm[1]
                if _win == "opp_next_turn":
                    _wo = 1 - self.current
                    _until = int(self.turn_counts[_wo]) + 1
                elif _win == "own_next_turn":
                    _wo = self.current
                    _until = int(self.turn_counts[_wo]) + 1
                else:
                    raise ValueError(f"未知 arm 窗口：{_win}")
                events.add_temp_watch(
                    self, _wo, _ev_kind, _ev_body, until_count=_until,
                    ctx={"target": opp.active}, name="temp_watch",
                    source=str(tag))
            elif tag.startswith("attach_named:"):
                # S2d：从弃牌堆附着**具名能量**（Lycanroc「Spike-Clad」：
                # 「attach up to 2 **Spiky Energy** cards from your discard pile
                # to this Pokémon」）。按**卡名**取，不是按属性 —— Spiky Energy
                # 提供的是无色，按属性取会拿错卡（静默错映射）。
                self._log("attach_named", {
                    "player": self.current,
                    "detail": self._do_attach_named(p, attacker, str(tag))})
            elif tag.startswith("queue:"):
                # S2c：招式侧的**延迟效果**（Team Rocket's Grimer「Corrosive
                # Sludge」：「At the end of your opponent's next turn, discard the
                # Defending Pokémon and all attached cards.」）。
                # ⚠ 目标必须在**入队时快照**：到对方回合末时「Defending Pokémon」
                #   早已不是当时那只（甚至已换下），延迟结算不能重新猜目标。
                _qp = str(tag).split(":")
                if len(_qp) < 3 or not _qp[2]:
                    raise ValueError(f"queue tag 非法（缺延迟体）：{tag}")
                _when, _body = _qp[1], ":".join(_qp[2:])
                if _when == "opp_next_turn_end":
                    _owner, _delay = 1 - self.current, 1
                elif _when == "next_turn_end":
                    _owner, _delay = self.current, 1
                elif _when == "turn_end":
                    _owner, _delay = self.current, 0
                else:
                    raise ValueError(f"未知延迟时点：{_when}")
                events.push(self, _owner, _delay, _body, source=str(tag),
                            ctx={"target": opp.active,
                                 "side": 1 - self.current})
            elif tag.startswith("recon:") and not _parse_recon_tag(tag)["legacy"]:
                # S2b（2026-11-08）：**进化触发**里的「看牌库顶 N 张 → 附着/铺场」
                # （Grumpig「Energized Steps」：`evolve_act:recon:4:all:basic_energy:
                #  dest:attach_spread:rest:shuffle`）。
                # ⚠ 只开放**扩展口径**：旧口径 `recon:N`（随机取 1 张入手）是训练家
                #   与主动特性的语义，放进招式/进化路径会变成「每次进化白拿一张」。
                self._log("ability", {"player": self.current,
                                      "name": str(tag),
                                      "trigger": "on_evolve",
                                      "detail": self._do_recon(p, tag, action)})
            elif tag.startswith("info:peek_prize:"):
                # S3：查看对手 1 张**背面朝上**的奖赏卡（Blipbug「Searching Eyes」）
                # 本引擎是完美信息的，「查看」不改变任何状态 —— 与 `info:reveal_hand`
                # 同一条口径：**显式建模的等价**（有派发 + 有日志，且日志里记下
                # 看的是哪张、是什么），而不是「未建模当无事发生」。
                _v = tag.split(":")
                _side = (1 - self.current) if len(_v) > 2 and _v[2] == "opp" \
                    else self.current
                _tp = self.players[_side]
                _i = next((i for i, s in enumerate(_tp.prizes)
                           if not s.face_up), None)
                if _i is None:
                    self._log("info", {"player": self.current,
                                       "what": "peek_prize",
                                       "target": "none_face_down"})
                else:
                    self._log("info", {"player": self.current,
                                       "what": "peek_prize",
                                       "owner": _side, "slot": _i,
                                       "card": _card_label(_tp.prizes[_i].ref)})
            elif tag.startswith("info:"):
                # 纯信息类效果（第一百三十四批）：
                # 「Your opponent reveals their hand.」（Hoothoot / Espurr / Pidove）
                # 本引擎是**完美信息**的：双方牌库/手牌本来对决策方完全可见，
                # 「展示手牌」不改变任何状态 —— 因此等价于「无状态变化」。
                # ⚠ 这是**显式建模的等价**，不是「未建模当无事发生」：解析层只有在
                #   整句恰好是这一句时才产出本 tag（见 `_detect_reveal_hand`），
                #   带任何附加动作的写法一律不认领（宁可整卡拒收）。
                self._log("info", {"player": self.current,
                                   "target": attacker.name,
                                   "what": tag[len("info:"):]})
            elif tag.startswith("choose:counters_any:"):
                # 「Put N damage counters on your opponent's Pokémon in any way
                #  you like.」（Sinistcha「Cursed Drop」）
                # 目标**含战斗位**（既有 `spread_free:` 只覆盖后备），且允许
                # 重复选同一只 —— 用决策分解，由牌手逐枚决定放哪。
                _v = tag.split(":")
                if len(_v) < 3 or not _v[2].isdigit():
                    raise ValueError(f"choose:counters_any tag 非法：{tag}")
                _n = int(_v[2])
                _opts = []
                if opp.active is not None and not opp.active.is_ko:
                    _opts.append({"target": 0, "display": opp.active.name})
                for _bi, _fp in enumerate(opp.bench, start=1):
                    if not _fp.is_ko:
                        _opts.append({"target": _bi, "display": _fp.name})
                if not _opts:
                    continue
                self._open_choice("counters_any_opp", _opts,
                                  min_pick=_n, max_pick=_n,
                                  allow_repeat=True, tag=tag)
            elif tag.startswith("choose:optional_energy_cost:"):
                # 「You may discard N {T} Energy from this Pokémon and <后续>」
                # （Mega Eelektross ex「Disaster Shock」）
                # **可选** = 由牌手决定付不付：落成 confirm 选择（yes/no），
                # 绝不默认「付」或「不付」（两种默认都会实质改结果）。
                _v = tag.split(":")
                # choose : optional_energy_cost : <n> : <type> : then : <tag>
                if len(_v) < 6 or not _v[2].isdigit() or _v[4] != "then":
                    raise ValueError(f"optional_energy_cost tag 非法：{tag}")
                _n, _ty, _then = int(_v[2]), _v[3], ":".join(_v[5:])
                self._open_choice(
                    "confirm",
                    [{"display": f"弃 {_n} 个 {_ty} 能量并执行 {_then}",
                      "yes": True, "n": _n, "etype": _ty, "then_tag": _then,
                      "fp": attacker},
                     {"display": "不支付（本招按原效果结束）", "yes": False}],
                    min_pick=0, max_pick=1, tag=tag)
            elif tag.startswith("counters_double"):
                # 「Double the number of damage counters on each of your
                #  opponent's Pokémon.」（N's Vanilluxe「Snow Coating」）
                # 指示物数 = 伤害/10；必须**按指示物翻倍**再乘回 10，直接
                # `dmg * 2` 会把「非 10 的余数」也翻倍（本引擎里招式伤害都是
                # 10 的倍数，但仍按卡面口径精确计算，避免以后引入非整十伤害时静默错）。
                _cd = 0
                for _f in opp.all_field():
                    _d = int(_f.damage)
                    _new = (_d // 10) * 2 * 10 + _d % 10
                    if _new != _d:
                        _f.damage = _new
                        _cd += 1
                self._log("counters_double", {"player": self.current,
                                              "targets": _cd})
            elif tag.startswith("atk_energy_block:"):
                # 「During your opponent's next turn, Pokémon that have N or
                #  less Energy attached can't use attacks. (This includes new
                #  Pokémon that come into play.)」（Walrein「Frigid Fangs」）
                _v = tag.split(":")
                if len(_v) < 2 or not _v[1].isdigit():
                    raise ValueError(f"atk_energy_block tag 非法：{tag}")
                opp.atk_energy_block_n = int(_v[1])
                opp.atk_energy_block_until = self._lock_until(1 - self.current)
                self._log("atk_energy_block", {
                    "player": 1 - self.current, "n": int(_v[1])})
            elif tag.startswith("win_if:own_prizes_eq:"):
                # 「If you use this attack when you have exactly 1 Prize card
                #  remaining, you win this game.」（N's Sigilyph「Victory Symbol」）
                # ⚠ 只在**恰好**该奖赏数时成立；条件不成立时本招什么都不做
                #   （卡面没有别的效果）—— 绝不当成「按普通招式结算」。
                _n = int(tag.split(":")[-1])
                if p.prizes_left == _n:
                    self._finish(self.current, "victory_symbol",
                                 {"attack": attacker.name, "prizes": _n})
                    return
                self._log("win_if_skip", {"player": self.current,
                                          "need": _n, "have": p.prizes_left})
            elif tag.startswith("heal:"):
                # 范围（全体/后备/属性限定）由 `_apply_heal` 统一解释；
                # 无 scope 时回复攻击方自身（历史语义，保持不变）
                healed, n_tgt = _apply_heal(self, p, tag, caster=attacker,
                                            action=action)
                self._log("heal", {"player": self.current,
                                   "target": attacker.name, "amt": healed,
                                   "targets": n_tgt})
                prev_ok = healed > 0       # 「If you healed any damage in this way」

            elif tag.startswith("counters_to_hp:"):
                # 第八十四批：逐只放到「剩余 HP = N」。必须排在
                # `counters:` 之前（虽不重名，但语义族相邻，顺序显式化）。
                _cth = tag.split(":")
                if len(_cth) != 3 or not _cth[1].isdigit():
                    raise ValueError(f"counters_to_hp tag 非法：{tag}")
                self._apply_counters_to_hp(int(_cth[1]), _cth[2])
            elif tag.startswith("counters:"):
                # 「Place N damage counters on your opponent's Active Pokémon」
                # （Haunter / Alakazam）—— 指示物**直放**：不吃弱点/抗性，
                # 因此不走 damage 字段。`:per:<counter>` 按计数源缩放。
                # 第二十批（2026-10-29）：范围扩展到**任意**对手宝可梦
                # （`:scope:opp`）与特性自毁（`:selfko`），见 `_apply_counters`。
                if self._apply_counters(tag, action, holder=attacker):
                    return
            elif tag.startswith("dmg_taken_plus_next:"):
                # 「During your next turn, the Defending Pokémon takes N more
                # damage from attacks (after applying Weakness and Resistance).」
                # —— 记在**受击方**身上，到期 = 攻击方（= 我）的下个回合结束。
                _v = tag.split(":")
                if len(_v) < 2 or not _v[1].isdigit():
                    raise ValueError(f"dmg_taken_plus_next tag 非法：{tag}")
                if opp.active is None:
                    continue
                if _opp_blocked(opp.active):
                    self._log("blocked", {"target": opp.active.name,
                                          "by": "prevent_all"})
                    continue
                opp.active.dmg_taken_plus_next_n = int(_v[1])
                opp.active.dmg_taken_plus_next_until = self._lock_until(
                    self.current)
                opp.active.dmg_taken_plus_next_owner = self.current
                self._log("dmg_taken_plus_next", {
                    "target": opp.active.name, "n": int(_v[1])})
            elif tag.startswith("retaliate_next:"):
                # 「During your opponent's next turn, if **this Pokémon** is
                # damaged by an attack …, put N damage counters on the Attacking
                # Pokémon [equal to the damage done to this Pokémon].」
                # （Bouffalant / Zamazenta，2026-10-29 第二十九批）
                # 记在**出招者自己**身上；窗口 = 对手的下一个回合（同 `_lock_until`）。
                _v = tag.split(":")
                arg = _v[1] if len(_v) > 1 else ""
                if arg != "equal" and not arg.isdigit():
                    raise ValueError(f"retaliate_next tag 非法：{tag}")
                attacker.retaliate_next_n = 0 if arg == "equal" else int(arg)
                attacker.retaliate_next_equal = (arg == "equal")
                attacker.retaliate_until = self._lock_until(1 - self.current)
                attacker.retaliate_owner = 1 - self.current
                self._log("retaliate_next", {"target": attacker.name,
                                             "n": attacker.retaliate_next_n,
                                             "equal": attacker.retaliate_next_equal})
            elif tag.startswith("atk_minus_next:"):
                # 「During your opponent's next turn, attacks used by the Defending
                # Pokémon do N less damage (before applying Weakness/Resistance).」
                _v = tag.split(":")
                if len(_v) < 2 or not _v[1].isdigit():
                    raise ValueError(f"atk_minus_next tag 非法：{tag}")
                if opp.active is None:
                    continue
                if _opp_blocked(opp.active):
                    self._log("blocked", {"target": opp.active.name,
                                          "by": "prevent_all"})
                    continue
                opp.active.atk_minus_next_n = int(_v[1])
                opp.active.atk_minus_next_until = self._lock_until(1 - self.current)
                self._log("atk_minus_next", {
                    "target": opp.active.name, "n": int(_v[1])})
            elif tag.startswith("self_attack_set_next:"):
                # ⚠ 招式名键用 `_atk_tag_key`（去掉所有非字母数字）：
                #   解析侧 `_tag_name_key` 给的是 `hyperfang`，而 `_attack_key`
                #   保留空格得到 `hyper fang` —— 两者不归一就永远取不到
                #   （实测：伤害仍是印刷值，且没有任何报错）。
                # 「During your next turn, this Pokémon's Hyper Fang attack's base
                #  damage is 240.」（第一百一十九批，Watchog「Focus Energy」）
                # ⚠ 与 plus 分开：落成加伤会让 240 变成「印刷 80 + 240」。
                _v = tag.split(":")
                if len(_v) < 4 or not _v[1].isdigit() or _v[2] != "name":
                    raise ValueError(f"self_attack_set_next tag 非法：{tag}")
                key = _atk_tag_key(":".join(_v[3:]))
                attacker.self_atk_set_next[key] = (
                    int(_v[1]), self._lock_until(self.current))
                self._log("self_attack_set_next", {
                    "target": attacker.name, "attack": ":".join(_v[3:]),
                    "n": int(_v[1])})
            elif tag.startswith("self_attack_plus_next:"):
                # 「During your next turn, this Pokémon's <招式> attack does N more
                # damage (before applying Weakness and Resistance).」
                _v = tag.split(":")
                if len(_v) < 4 or not _v[1].isdigit() or _v[2] != "name":
                    raise ValueError(f"self_attack_plus_next tag 非法：{tag}")
                key = attacker._attack_key(":".join(_v[3:]))
                attacker.self_atk_plus_next[key] = (
                    int(_v[1]), self._lock_until(self.current))
                self._log("self_attack_plus_next", {
                    "target": attacker.name, "attack": ":".join(_v[3:]),
                    "n": int(_v[1])})
            elif tag.startswith("checkup_set:"):
                # 「During Pokémon Checkup, put 8 damage counters on that Pokémon
                # instead of 1.」（Team Rocket's Nidoking ex）—— 改写**该只**的
                # 检查期伤害；目标就是对手战斗位。
                _cp = tag.split(":")
                if len(_cp) < 3 or _cp[1] not in STATUSES \
                        or not _cp[2].isdigit():
                    raise ValueError(f"checkup_set tag 非法：{tag}")
                if opp.active is not None and not opp.active.is_ko:
                    if _opp_blocked(opp.active):
                        self._log("blocked", {"target": opp.active.name,
                                              "by": "prevent_all"})
                    else:
                        opp.active.checkup_set[_cp[1]] = int(_cp[2])
                        self._log("checkup_set", {"target": opp.active.name,
                                                  "status": _cp[1],
                                                  "dmg": int(_cp[2])})

            elif tag.startswith("knockout:"):
                # 招式击倒（2026-10-28 第九批）
                if self._apply_knockout(tag, action, _opp_blocked, attacker):
                    return
            elif tag.startswith("self_dmg_bonus:"):
                # 「during this turn, attacks used by this Pokémon do N more damage」
                # （第九十五批，Feraligatr）—— 只设旗标，伤害在 `_attack_mods` 叠加。
                _sp = tag.split(":")
                if len(_sp) < 2 or not _sp[1].isdigit():
                    raise ValueError(f"self_dmg_bonus tag 非法：{tag}")
                _scope = _sp[2] if len(_sp) > 2 else "self"
                if _scope not in ("self", "team"):
                    raise ValueError(f"self_dmg_bonus 范围非法：{tag}")
                _p = self.players[self.current]
                _p.dmg_bonus_n = int(_sp[1])
                _p.dmg_bonus_scope = _scope
                _p.dmg_bonus_until = self.turn_number
                _p.dmg_bonus_src = attacker if _scope == "self" else None
                self._log("dmg_bonus", {"player": self.current,
                                        "n": _p.dmg_bonus_n, "scope": _scope})
            elif tag.startswith("dmg_plus:") and ":recoil:" in tag:
                # 可选加伤 + 反伤（第四十七批，Gurdurr / Dondozo ex）：
                # 「You may do N more damage. If you do, this Pokémon also does M
                #  damage to itself.」——加伤已在 `_attack_mods` 计入，这里按
                # 「If you do」的联动结算反伤（与 `self:N` 同口径：不吃弱抗、可自爆）。
                _rp = str(tag).split(":")
                if len(_rp) != 4 or not _rp[3].isdigit():
                    raise ValueError(f"dmg_plus:recoil tag 非法：{tag}")
                _ramt = int(_rp[3])
                attacker.damage += _ramt
                self._log("damage", {"target": attacker.name,
                                     "player": self.current,
                                     "dmg": _ramt, "self": True, "recoil": True})
                if attacker.is_ko:
                    self._resolve_ko(self.current, attacker)
                    if self.finished:
                        return
            elif tag.startswith("self:"):
                # 「This Pokémon also does N damage to itself.」→ `self:N`
                # 2026-10-29 第十七批：`:per:<counter>` 每份伤害按计数源缩放
                # （Palafin「for each damage counter on it」）。
                _sp = tag.split(":")
                _amt = int(_sp[1])
                if len(_sp) > 3 and _sp[2] == "per":
                    _amt *= _eval_counter(self, ":".join(_sp[3:]))
                attacker.damage += _amt
                self._log("damage", {"target": attacker.name,
                                     "player": self.current,
                                     "dmg": _amt, "self": True})
                if attacker.is_ko:
                    self._resolve_ko(self.current, attacker)
                    if self.finished:
                        return
            elif tag.startswith("draw_both:"):
                # 「each player draws N cards」（Chandelure / Unfair Stamp 的抽牌段）
                n = int(tag.split(":")[1])
                for pi in (self.current, 1 - self.current):
                    self._safe_draw(pi, n, source="effect_both")
                    if self.finished:
                        return
            elif tag.startswith("opp_shuffle_draw:"):
                detail = self._opp_shuffle_draw(tag)
                self._log("opp_shuffle_draw", {"player": 1 - self.current,
                                               "detail": detail})
            elif tag.startswith("draw:"):
                # B3：`draw:N` / `draw:per:<counter>` / `draw:N:if:<cond>`
                n, cond = _draw_spec(self, tag)
                if cond is not None and not _eval_condition(self, cond):
                    self._log("no_effect", {"tag": tag, "reason": "条件不满足"})
                else:
                    self._safe_draw(self.current, n, source="attack_effect")
            elif tag.startswith("discard_hand_energy:"):
                # B3：「Discard N Basic {X} Energy cards from your hand」
                # `:required` = 卡面「If you can't discard N cards in this way,
                # this attack does nothing」——不足则**不弃且伤害归零**
                # （归零在 `_attack_mods` 已处理；这里只负责不弃）。
                _n, _et, _req = _parse_hand_energy_tag(tag)
                _avail = _hand_energy_of(p, _et)
                if _req and _avail < _n:
                    self._log("no_effect", {"tag": tag,
                                            "reason": f"手牌仅 {_avail} 张指定能量"})
                else:
                    _done = _discard_hand_energy(self, p, _n, _et)
                    self._log("discard_energy",
                              {"player": self.current, "target": attacker.name,
                               "n": _done, "side": "own", "from": "hand",
                               "etype": _et})
            elif tag.startswith("discard_energy:"):
                parts = tag.split(":")
                raw = parts[1]
                n = 999 if raw == "all" else int(raw)
                # `:opp` = 弃**对手**战斗位的能量。
                # 为什么要有：卡面常见「Discard an Energy from your opponent's Active
                # Pokémon」，而早期只有"弃自身"一种语义 → 解析只能映射成弃自身，
                # 4 张真实卡（Gastly/Larvitar/Accelgor/Croconaw）在真实对局里**弃错对象**
                # （把自家能量丢掉、对手毫发无损）。这是**静默算错**，必须修。
                if len(parts) > 2 and parts[2] == "evolved":
                    # 「discard all Energy from **those** Pokémon」——己方全部进化宝可梦
                    done = 0
                    for _fp in p.all_field():
                        if _fp.stage <= 0:
                            continue
                        avail = _fp.total_energy()
                        if avail:
                            done += _discard_energy_from(self, p, _fp, avail)
                    self._log("discard_energy",
                              {"player": self.current, "target": attacker.name,
                               "n": done, "side": "own", "scope": "evolved"})
                elif len(parts) > 2 and parts[2] == "opp":
                    done = 0
                    if opp.active is not None:
                        if _opp_blocked(opp.active):
                            self._log("blocked", {
                                "target": opp.active.name,
                                "by": "effect_immune"})
                            continue
                        if len(parts) > 3 and parts[3] == "special" \
                                and len(parts) > 4 and parts[4] == "any":
                            # 「Discard a Special Energy from **1 of your
                            # opponent's Pokémon**」（第九十八批，Enhanced Hammer）：
                            # 目标含后备 → 战斗位优先，其次后备顺序；
                            # 不能让它退化成「只弃战斗位」（对手把特殊能量放后备时
                            # 该效果会静默不生效）。
                            for _fp in ([opp.active] if opp.active else []) \
                                    + list(opp.bench):
                                if _fp is None or _fp.is_ko:
                                    continue
                                done = _discard_special_energy(self, opp, _fp, n)
                                if done:
                                    break
                        elif len(parts) > 3 and parts[3] == "special":
                            # 「Discard a Special Energy from your opponent's Active
                            # Pokémon.」（2026-10-29 第二十四批）——按**特殊能量卡**
                            # 弃置（`senergy`）；普通 `remove_any` 会先弃基本能量，
                            # 对手只贴基本能量时就会**本不该生效却生效**（静默错算）。
                            done = _discard_special_energy(self, opp, opp.active, n)
                        else:
                            avail = min(n, opp.active.total_energy())
                            if avail:
                                done = _discard_energy_from(self, opp, opp.active,
                                                            avail)
                    self._log("discard_energy",
                              {"player": self.current,
                               "target": (opp.active.name if opp.active else None),
                               "n": done, "side": "opp",
                               "special": len(parts) > 3 and parts[3] == "special"})
                else:
                    # 优先弃自身 active；无 active 则弃手牌能量
                    done = 0
                    if p.active is not None:
                        avail = min(n, p.active.total_energy())
                        if avail:
                            done = _discard_energy_from(self, p, p.active, avail)
                    if done < n:
                        done += _discard_hand_energy(self, p, n - done)
                    self._log("discard_energy", {"player": self.current,
                                                 "target": attacker.name, "n": done,
                                                 "side": "own"})
            elif tag.startswith("energy_to_deck:"):
                # 「Then, shuffle those Energy cards into your deck」（Kyogre /
                # Sinistcha ex，2026-10-29 第二十八批）——把**弃牌堆里的基本能量**
                # 洗回牌库。⚠ 顺序即卡面顺序：先被 `dmg_set:…:per:` / `counters:`
                # 计数，再洗回；**不洗回**会让下一次攻击重复计数（静默加强）。
                _ep = tag.split(":")
                color = _ep[1] if len(_ep) > 1 else "any"
                raw = _ep[2] if len(_ep) > 2 else "all"
                done = _energy_to_deck(self, p, color, raw)
                self._log("energy_to_deck", {"player": self.current,
                                             "color": color, "n": done})
            elif tag.startswith("energy_self_to_deck:"):
                # 「Shuffle all Energy attached to this Pokémon into your deck」
                # （Mega Skarmory ex「Sonic Ripper」，2026-11-03 第四十一批）——
                # **强制代价**：附着的基本能量按点数还原成能量卡、特殊能量按整张
                # 原样放回牌库（引擎的能量是计数，卡级来源在 `senergy` 里），
                # 然后洗牌。⚠ 只计数不动牌库会让牌库少 N 张（「洗回」变「消失」）。
                n = _energy_self_to_deck(self, p, attacker)
                self._log("energy_self_to_deck", {"player": self.current,
                                                  "from": attacker.name, "n": n})
            elif tag.startswith("shuffle_opp_pokemon:"):
                # 「Shuffle that Pokémon and all attached cards into their deck」
                # （2026-11-03 第四十二批 · Sylveon / Shiftry，抛币门内的子效果）
                self._shuffle_opp_pokemon(tag.split(":", 1)[1])
            elif tag.startswith("energy_move:"):
                # S6 能量移动族（自身↔后备 / 对手战斗位→后备 / 双向任选）
                self._apply_energy_move(tag, attacker, action)
            elif tag.startswith("discard_opp_random:"):
                self._discard_opp_random(int(tag.split(":")[1]))
            elif tag.startswith("shuffle_opp_hand:"):
                self._shuffle_opp_hand(int(tag.split(":")[1]))
            elif tag.startswith("discard_opp_to:"):
                self._discard_opp_to(int(tag.split(":")[1]))
            elif tag.startswith("discard_to_both:"):
                # 双方各弃到剩 N（第五十四批）—— 招式侧同样可达
                self._discard_to_both(int(tag.split(":")[1]))
            elif tag.startswith("bounce:"):
                self._apply_bounce(tag, attacker, action)
            elif tag.startswith("discard_tool:"):
                self._apply_discard_tool(tag)
            elif tag == "discard_stadium":
                # S6 弃场地族（Mow Rotom / Eternatus / Cetitan ex）
                self._apply_discard_stadium()
            elif tag.startswith("discard:"):
                raw = tag.split(":")[1]
                n = len(p.hand) if raw == "all" else int(raw)
                dropped = _discard_from_hand(self, p, n)
                self._log("discard", {"player": self.current, "n": dropped,
                                      "from": "hand"})
            elif tag == "switch_self":
                if p.bench:
                    bi = int(action.get("bench", 0))
                    bi = 0 if not (0 <= bi < len(p.bench)) else bi
                    old = p.active
                    if old is not None:
                        old.clear_status()
                        old.clear_active_locks()
                        p.active = p.bench.pop(bi)
                        p.bench.append(old)
                        self._log("switch", {"player": self.current,
                                             "out": old.name,
                                             "in": p.active.name,
                                             "forced": True})
            elif tag == "gust":
                if opp.bench:
                    bi = int(action.get("gust", action.get("target", 0)))
                    bi = 0 if not (0 <= bi < len(opp.bench)) else bi
                    if _opp_blocked(opp.bench[bi]):
                        self._log("blocked", {"target": opp.bench[bi].name,
                                              "by": "effect_immune",
                                              "effect": "gust"})
                        continue
                    new_act = opp.bench.pop(bi)
                    if opp.active is not None:
                        opp.active.clear_active_locks()
                        opp.bench.append(opp.active)
                    opp.active = new_act
                    self._log("gust", {"player": self.current,
                                       "target": new_act.name, "pos": bi})
            elif tag.startswith("attach_spread:"):
                # 多张自由分配附着（2026-10-30 第三十三批）：必须排在
                # `attach` 之前 —— 否则被 `startswith("attach")` 吞掉
                detail = self._do_attach_spread(p, tag, action)
                self._log("attach_spread", {"player": self.current,
                                            "detail": detail})
            elif tag.startswith("attach_spread_flip:"):
                # 抛币计数版（2026-11-02 第三十八批，Mega Audino ex）
                detail = self._do_attach_spread_flip(p, tag, action)
                self._log("attach_spread_flip", {"player": self.current,
                                                 "detail": detail})
            elif tag.startswith("attach_each_bench:"):
                # 每只后备各 per 张（2026-11-02 第三十八批，Mega Gardevoir ex）
                detail = self._do_attach_each_bench(p, tag, action)
                self._log("attach_each_bench", {"player": self.current,
                                                "detail": detail})
            elif tag.startswith("attach_spread_multi:"):
                # 双色「or 1 of each」（第三十九批，Steven's Metagross ex）
                detail = self._do_attach_spread_multi(p, tag, action)
                self._log("attach_spread_multi", {"player": self.current,
                                                  "detail": detail})
            elif tag.startswith("attach_each_pick:"):
                # 选至多 N 只、每只各 per 张（第三十九批，Janine's Secret Art）
                detail = self._do_attach_each_pick(p, tag, action)
                self._log("attach_each_pick", {"player": self.current,
                                               "detail": detail})
            elif tag.startswith("attach_flip_until:"):
                # 抛到反面为止、按正面数附给自己（第三十九批，Snorlax）
                detail = self._do_attach_flip_until(p, tag, action)
                self._log("attach_flip_until", {"player": self.current,
                                                "detail": detail})
            elif tag.startswith("attach"):
                n, etype, zone = _parse_attach_tag(tag)
                # 目标：`:target:bench|any|bench_type:<c>`（2026-10-28 第十五批）；
                # 省略时仍附给攻击方（历史语义）。
                _tfp = _attach_target_fp(tag, p, action, attacker)
                if _tfp is None:
                    self._log("no_effect", {"tag": tag, "reason": "无附能目标"})
                    prev_ok = False
                else:
                    got = _attach_energy_from_zones(self, p, _tfp, n, etype,
                                                    zone or "any",
                                                    allow_special=":anyenergy" in tag)
                    self._log("attach", {"player": self.current,
                                         "target": _tfp.name, "n": got,
                                         "etype": etype, "from": "effect",
                                         "zone": zone or "any"})
                    prev_ok = got > 0     # 「If you do, heal …」
                    _ac = _attach_counters_of(tag)
                    if got > 0 and _ac:
                        # Toxtricity：「If you attached Energy …, place 2 damage
                        # counters on that Pokémon」——附能成功才放置。
                        _tfp.damage += _ac
                        self._log("damage", {"player": self.current,
                                             "target": _tfp.name,
                                             "dmg": _ac, "counters": True})
                        if _tfp.is_ko:
                            self._resolve_ko(self.current, _tfp)
                            if self.finished:
                                return
            elif tag.startswith("search_bench:"):
                detail = self._search_bench(p, tag)
                self._log("search_bench", {"player": self.current,
                                           "detail": detail})
            elif tag.startswith("search_discard:"):
                # 「search … and discard them」（第一百批，Drilbur）——
                # ⚠ 必须排在通用 `search_` 之前：`search_discard` 也以
                #   `search_` 开头，落到通用分支会变成**入手**（静默加强）。
                detail = f"search discard {_search_discard(self, p, tag.split(':', 1)[1])}"
                self._log("search_discard", {"player": self.current,
                                             "detail": detail})
            elif tag.startswith("search_top:"):
                # 第七十九批：招式侧「检索 2 张放回牌库顶」（Dialga）
                detail = self._search_top(p, tag)
                self._log("search_top", {"player": self.current,
                                          "detail": detail})
            elif tag.startswith("search_"):
                # 招式侧**全牌库检索**（2026-10-27）：与特性/训练家侧同一语义
                # （从整副牌库检索指定类别入手，不是 `recon` 的「牌库顶 N 取 1」）。
                # 规则：牌库无合法目标时**效果不执行**，招式仍可使用 → 不外抛。
                try:
                    detail = (self._trainer_search_energy(p, tag)
                              if tag.startswith("search_energy:")
                              else self._trainer_search_kind(p, tag))
                except ValueError as e:
                    if "无可检索" not in str(e):
                        raise
                    detail = "no target"
                self._log("search", {"player": self.current, "detail": detail})
            elif tag.startswith("recover:"):
                # 弃牌堆回收（招式侧）：取 n 张（kind=pokemon|energy|any）入手
                # B3：`recover:heads:N` —— 抛 N 枚硬币，按正面枚数**最多**取 N 张
                # （Stoutland「Flip 3 coins. Put a number of cards up to the number
                #   of heads from your discard pile into your hand.」）
                _rp = tag.split(":")
                if len(_rp) > 2 and _rp[1] == "heads":
                    _flips = int(_rp[2]) if _rp[2].isdigit() else 0
                    _heads = sum(1 for _ in range(_flips)
                                 if self.rng.random() < 0.5)
                    self._log("coin", {"player": self.current,
                                       "result": f"{_heads}/{_flips} heads",
                                       "tag": tag})
                    cands = _recover_candidates(p, f"recover:{_heads}:any")
                else:
                    cands = _recover_candidates(p, tag)
                got = _recover_to_hand(self, p, cands)
                self._log("recover", {"player": self.current, "n": got,
                                      "from": "discard"})
            elif tag.startswith("move_damage:"):
                raw = tag.split(":")[1]
                n = 999 if raw == "all" else int(raw)
                moved = 0
                src = p.active
                dst = opp.active
                if dst is not None and _opp_blocked(dst):
                    self._log("blocked", {"target": dst.name,
                                          "by": "effect_immune",
                                          "effect": "move_damage"})
                    continue
                if src is not None and dst is not None:
                    for _ in range(n):
                        if src.damage < 10:
                            break
                        src.damage -= 10
                        dst.damage += 10
                        moved += 1
                self._log("move_damage", {"player": self.current,
                                          "n": moved,
                                          "from": src.name if src else None,
                                          "to": dst.name if dst else None})
                if dst is not None and dst.is_ko:
                    self._resolve_ko(1 - self.current, dst)
            elif tag.startswith("fx:"):
                # 效果族（fx）：一次性动作，语义由 engine/families/fx_*.py 定义
                detail = families.apply_action(
                    self, tag, {"kind": "attack", "attacker": attacker,
                                "action": action, "player": self.current})
                self._log("fx", {"player": self.current, "tag": tag,
                                 "detail": detail})
            elif tag.startswith("borrow_attack:"):
                # 借用招式是**招式选择权限**，不是执行效果：真正被借用的招式
                # 已在 `_do_attack` 开头解析（见 `_resolve_borrow`），此处无动作。
                # 显式写出分支，避免以后误当成"未知 tag 被静默忽略"。
                pass

    # ---------- 跨回合锁定（attack_lock / retreat_lock，2026-10-27） ----------
    def _lock_until(self, owner: int) -> int:
        """锁定的到期回合数：owner 的「下一个回合」仍 < until

        `turn_counts[owner]` 在 owner 每次回合开始时 +1。施加时记 +2，
        则 owner 的下一个回合（count+1）仍被锁，再下一个回合（count+2）解除。
        """
        return self.turn_counts[owner] + 2

    def _apply_attack_lock_tag(self, attacker: FieldPokemon, tag: str,
                               action: Optional[dict] = None) -> None:
        """``attack_lock:<scope>:<until>[:<attack name>]``

        scope ∈ all / opp_all / name / opp_name：
        · ``opp_name``：锁对手战斗位的**指定**招式，招式名由出招动作
          ``action['lock_attack']`` 在运行时指定（参考 ``snipe`` 的
          ``action['snipe']`` 选择机制）。未指定时取**确定性默认**（对手战斗位
          的首个招式），与 snipe「无选择→确定性默认」口径一致，避免静默不生效。
        """
        parts = tag.split(":")
        scope = parts[1] if len(parts) > 1 else ""
        until = parts[2] if len(parts) > 2 else "turn"
        if scope == "all":
            target, owner = attacker, self.current
        elif scope == "opp_all":
            target, owner = self.players[1 - self.current].active, 1 - self.current
        elif scope == "name":
            target, owner = attacker, self.current
        elif scope == "opp_name":
            target, owner = self.players[1 - self.current].active, 1 - self.current
        else:
            raise ValueError(f"未知 attack_lock 范围：{tag}")
        if target is None:
            return
        name = ""
        if scope == "opp_name":
            chosen = ((action or {}).get("lock_attack")
                      if action else None)
            if not chosen and target.pdef and target.pdef.attacks:
                # 确定性默认：对手战斗位首个招式（与 snipe 默认口径一致）
                chosen = target.pdef.attacks[0].name
            name = str(chosen or "")
            if not name:
                return
        else:
            name = ":".join(parts[3:]).strip() if len(parts) > 3 else ""
            if scope == "name" and not name:
                raise ValueError(f"{tag}：缺少招式名")
        if until == "active":
            target.attack_lock_names_active.add(target._attack_key(name))
        elif until == "turn":
            until_turn = self._lock_until(owner)
            if scope in ("name", "opp_name"):
                key = target._attack_key(name)
                target.attack_lock_names_until[key] = max(
                    target.attack_lock_names_until.get(key, 0), until_turn)
            else:
                target.attack_lock_all_until = max(
                    target.attack_lock_all_until, until_turn)
        else:
            raise ValueError(f"未知 attack_lock 时限：{tag}")
        self._log("attack_lock", {
            "player": self.current, "target": target.name,
            "scope": scope, "until": until,
            "attack": name or None})

    def _apply_retreat_lock_tag(self, tag: str) -> None:
        """``retreat_lock:<self|opp>:turn`` —— 目标在其下个回合内不能撤退"""
        parts = tag.split(":")
        if len(parts) < 2 or parts[1] not in ("self", "opp"):
            raise ValueError(f"未知 retreat_lock 形式：{tag}")
        owner = self.current if parts[1] == "self" else 1 - self.current
        target = self.players[owner].active
        if target is None:
            return
        target.retreat_lock_until = max(
            target.retreat_lock_until, self._lock_until(owner))
        self._log("retreat_lock", {"player": self.current,
                                   "target": target.name})

    def _apply_play_lock_tag(self, tag: str) -> None:
        """``play_lock:<item|evolution>:opp:turn``

        锁定对手**手牌**（不是场上宝可梦），因此状态挂在 `PlayerState`。
        """
        parts = tag.split(":")
        if len(parts) < 4 or parts[2] != "opp" or parts[3] != "turn":
            raise ValueError(f"未知 play_lock 形式：{tag}")
        kind = parts[1]
        owner = 1 - self.current
        target = self.players[owner]
        until = self._lock_until(owner)
        _field = {"item": "item_lock_until",
                  "evolution": "evolution_lock_until",
                  # 第一百零七批：Chi-Yu 封场地 / Scream Tail ex 封支援者 /
                  # Genesect 封 ACE SPEC（后者在解析侧另有常量形态）
                  "supporter": "supporter_lock_until",
                  "stadium": "stadium_lock_until",
                  "acespec": "acespec_lock_until"}.get(kind)
        if _field is None:
            raise ValueError(f"未知 play_lock 类别：{tag}")
        setattr(target, _field, max(getattr(target, _field), until))
        self._log("play_lock", {"player": self.current, "target": owner,
                                "kind": kind})

    # ---------- 选定目标伤害（snipe） ----------
    def _snipe_candidates(self, scope: str) -> list:
        """``snipe`` 的候选目标 → ``[(位置码, FieldPokemon)]``

        位置码约定（与 `attach.target` 一致）：
          ``opp``        0 = 对手战斗位；1..k = 对手后备 (i-1)
          ``opp_bench``  i = 对手后备 i
          ``own_bench``  i = 己方后备 i
          ``both_bench`` 0..len(己方后备)-1 = 己方后备；其后 = 对手后备
        """
        p = self.players[self.current]
        opp = self.players[1 - self.current]
        if scope == "opp":
            out = []
            if opp.active is not None:
                out.append((0, opp.active))
            out += [(i + 1, fp) for i, fp in enumerate(opp.bench)]
            return out
        if scope == "opp_bench":
            return [(i, fp) for i, fp in enumerate(opp.bench)]
        if scope == "own_bench":
            return [(i, fp) for i, fp in enumerate(p.bench)]
        if scope == "both_bench":
            base = len(p.bench)
            return ([(i, fp) for i, fp in enumerate(p.bench)]
                    + [(base + i, fp) for i, fp in enumerate(opp.bench)])
        raise ValueError(f"未知 snipe scope：{scope}")

    def _snipe_active_damage(self, defender: FieldPokemon, base: int,
                             attacker: Optional[FieldPokemon],
                             effects: tuple = (),
                             pre_mods: Optional[dict] = None) -> int:
        """选定目标命中**对手战斗位**时的最终伤害（2026-10-29 第十七批）

        卡面括号只写「Don't apply Weakness and Resistance **for Benched Pokémon**」
        —— 战斗位照常吃弱点/抗性，也会被静态减伤 / 道具 / 场地 / 护盾削减。
        此前 `snipe` 对战斗位一律按原始值结算 → 打战斗位时**静默少算/多算**。
        """
        mods = pre_mods or {}
        # 第六十五批：snipe 走同一套口径 —— `ignore_effects:wr` 连弱抗一起
        # 无视（Iron Crown ex「Twin Shotels」打后备时卡面同样写了这句）。
        # 漏改这里会让战斗位与后备的同一条 tag 表现不一致。
        ignore_eff, ignore_wr = _ignore_effects_flags(effects)
        static = _static_mods(defender, attacker) if attacker is not None else {}
        dmg = compute_damage(
            attacker, defender, base,
            weakness_override=(self._weakness_override(attacker, defender)
                               if attacker is not None else None),
            ignore_weakness=(bool(mods.get("ignore_weakness"))
                             or bool(static.get("no_weakness")) or ignore_wr),
            ignore_resistance=(bool(mods.get("ignore_resistance")) or ignore_wr))
        if ignore_eff:
            return dmg
        if static.get("dmg_reduce"):
            dmg = max(0, dmg - static["dmg_reduce"])
        team_red = families.static_dmg_minus(self, defender, attacker)
        if team_red:
            dmg = max(0, dmg - team_red)
        tool_red = self._tool_reduce_vs(defender, attacker)
        if tool_red:
            dmg = max(0, dmg - tool_red)
        stad_red = self._stadium_damage_reduce(defender)
        if stad_red:
            dmg = max(0, dmg - stad_red)
        dmg = apply_shield(defender, dmg,
                           block=_shield_blocks(defender, attacker, dmg))
        return dmg

    def _apply_snipe(self, tag: str, action: Optional[dict],
                     effects: tuple = (),
                     pre_mods: Optional[dict] = None) -> bool:
        """``snipe:<scope>:<per>:<count>[:per:<counter>][:if:<cond>]``

        count：``1``（默认）| ``<n>`` | ``all``。
        目标选择：动作里带 ``snipe``（位置码列表，见 `_snipe_candidates`）时按其指定；
        否则取候选表前 ``n`` 个（**确定性默认**，与 `switch_self` 的 `bench` 同思路）。
        「对手」侧的候选受护盾 / 后备免疫拦截；「己方」侧不拦（那是自己的效果）。

        ``:per:<counter>``：每份伤害按计数源缩放（Kyurem ex / Raging Bolt）；
        ``:per:target_counters``：每目标按**它自己**的伤害指示物数缩放
        （Team Rocket's Sneasel「for each damage counter **on that Pokémon**」）。
        ``:target:damaged``：只挑**身上有伤害指示物**的目标（Excadrill ex /
        Hippowdon「… that has any damage counters on it」，2026-10-29 第二十七批）；
        ``:if:<cond>``：条件不满足时**整段不结算**（卡面「If …, this attack also …」）。

        返回 ``True`` 表示对局已结束（调用方直接 return）。
        """
        body = tag
        # 目标筛选后缀先摘掉：放在 `:per:` / `:if:` 之后时也能正确解析
        need_damaged = ":target:damaged" in body
        if need_damaged:
            body = body.replace(":target:damaged", "", 1)
        # `:target:ex`：只挑对手的 Pokémon {ex}（2026-11-03 第四十一批）。
        # ⚠ 按 `pdef.is_ex` 判定 —— Mega ex 的 is_ex 也为 True，符合卡面口径
        #   （「Pokémon {ex}」包含 Mega Evolution ex）。
        need_ex = ":target:ex" in body
        if need_ex:
            body = body.replace(":target:ex", "", 1)
        cond = None
        if ":if:" in body:
            body, cond = body.split(":if:", 1)
        perc = None
        if ":per:" in body:
            body, perc = body.split(":per:", 1)
        parts = body.split(":")
        scope = parts[1] if len(parts) > 1 else ""
        per = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        raw = parts[3] if len(parts) > 3 and parts[3] else "1"
        if cond is not None and not _eval_condition(self, cond):
            self._log("no_effect", {"tag": tag, "reason": "条件不满足"})
            return False
        per_target = False
        if perc == "target_counters":
            # ⚠ 不能用全局计数源：那会把**其它**宝可梦身上的指示物也算进来
            per_target = True
        elif perc:
            per *= _eval_counter(self, perc)
        if per <= 0:
            self._log("no_effect", {"tag": tag, "reason": "缩放后伤害为 0"})
            return False
        cands = self._snipe_candidates(scope)
        if need_damaged:
            # 先筛目标再选数量：卡面「1 of … that has any damage counters」的
            # 「1」是在**合格目标里**选 1 个，不是先取前 1 个再看它有没有指示物。
            cands = [(pos, fp) for pos, fp in cands if fp.damage > 0]
        if need_ex:
            # 同理：`{ex}` 也是**先筛再选**（「1 of your opponent's Benched
            # Pokémon {ex}」= 在 ex 后备里选 1 只）。
            cands = [(pos, fp) for pos, fp in cands
                     if bool(getattr(fp.pdef, "is_ex", False))]
        if per_target:
            # 确定性默认：按**自身指示物数降序**（同分保持候选顺序）——等价于
            # 玩家总会挑伤得最重的那只（打 0 指示物目标等于白打）
            cands = sorted(cands, key=lambda x: -x[1].damage)
        if not cands:
            self._log("no_effect", {"tag": tag, "reason": "无可选目标"})
            return False
        n = len(cands) if raw == "all" else max(1, int(raw))
        want = action.get("snipe") if isinstance(action, dict) else None
        if isinstance(want, (list, tuple)) and want:
            wanted = {int(x) for x in want}
            picked = [fp for pos, fp in cands if pos in wanted]
            if not picked:                       # 指定了非法位置 → 退回默认
                picked = [fp for _, fp in cands[:n]]
        else:
            picked = [fp for _, fp in cands[:n]]
        opp = self.players[1 - self.current]
        opp_ids = {id(x) for x in opp.all_field()}
        _atk = self.players[self.current].active
        killed = []
        for fp in picked:
            # 逐目标缩放：每目标 = per × **它自己**的指示物数（0 即 0 伤害）
            deal = per * (fp.damage // 10) if per_target else per
            if deal <= 0:
                self._log("no_effect", {"tag": tag, "target": fp.name,
                                        "reason": "该目标无伤害指示物"})
                continue
            if id(fp) in opp_ids:
                owner = 1 - self.current
                on_bench = fp is not opp.active
                attacker = self.players[self.current].active
                if _target_dmg_blocked(self, opp, fp, attacker, deal,
                                       on_bench=on_bench):
                    self._log("blocked", {"target": fp.name,
                                          "by": "dmg_immune"})
                    continue
                if not on_bench:
                    deal = self._snipe_active_damage(fp, deal, attacker,
                                                     effects, pre_mods)
                    if deal <= 0:
                        self._log("blocked", {"target": fp.name,
                                              "by": "reduce"})
                        continue
            else:
                owner = self.current
            # 全队限时减伤（第四十六批，Iron Defender）：逐目标在弱抗之后扣减
            deal = max(0, deal - self._team_shield_reduce(fp, _atk))
            if deal <= 0:
                self._log("blocked", {"target": fp.name, "by": "team_shield"})
                continue
            # 抛币免伤被动（第四十五批，Fezandipiti）：逐个目标独立触发
            if deal > 0 and self._coin_prevent_of(fp):
                self._log("blocked", {"target": fp.name, "by": "coin_prevent"})
                continue
            fp.damage += deal
            self._log("damage", {"target": fp.name, "player": owner,
                                 "dmg": deal, "base": deal, "snipe": scope})
            if fp.is_ko:
                killed.append((owner, fp))
        for owner, fp in killed:
            self._resolve_ko(owner, fp)
            if self.finished:
                return True
        return False

    def _resolve_self_ko(self, holder: Optional[FieldPokemon]) -> bool:
        """「If you use this Ability, this Pokémon is Knocked Out.」—— 特性自爆

        卡面原型（全库仅 3 张，措辞完全相同）：
          · Dusclops SFA 19 / Dusknoir SFA 20「Cursed Blast」（放伤害指示物）
          · Magneton SSP 59「Overvolt Discharge」（弃牌堆附能）

        与「被攻击击倒」的**三处区别**（每一处弄错都会静默改数值）：
          ① 不进 `_ko_flags` —— 那不是「对手击倒了你」，不该喂
             `dmg_plus:*:if:opp_ko_last_turn` 一类条件；
          ② 不触发 `special_energy.prize_delta`（Legacy Energy 限「被对手
             **招式伤害**击倒」）；
          ③ 走 `_resolve_ko` 的**统一路径**：奖赏照给对手（这就是代价）、
             触发被击倒特性、补位、判负 —— 都与其他击倒也一致。

        返回 ``True`` 表示对局已结束。
        """
        if holder is None or holder.is_ko:
            return False
        # ⚠ 用「填满伤害」表达「直接击倒」——与 `_apply_knockout` 的 `_ko_one`
        # 一致（全仓口径）。`_ko_from_attack` 此时为 False，故不会污染击倒标记。
        holder.damage = holder.max_hp
        self._log("knockout_effect", {"player": self.current,
                                      "target": holder.name, "scope": "selfko"})
        self._resolve_ko(self.current, holder)
        return self.finished

    def _apply_counters_to_hp(self, hp: int, scope: str) -> None:
        """``counters_to_hp:<hp>:<opp_bench|opp_active>``（第八十四批）

        卡面：「Put damage counters on each of your opponent's Benched
        Pokémon until its remaining HP is 100.」（PalossandEX）/「Place damage
        counters on your opponent's Active Pokémon until its remaining HP is
        10.」（Scolipede）。

        与 `counters:<个数>` 的差别：这里给的是**目标剩余 HP**，要逐只按
        `max_hp - hp - 当前 damage` 算差额；直接放固定个数会随目标 HP 不同而
        错算。指示物**不吃弱抗**，因此直接改 `damage`（与 `_apply_counters`
        同源口径），不走 damage/snipe 结算。

        ⚠ 剩余 HP 已 ≤ 门槛的宝可梦**不放**（官方规则不能靠指示物回血）；
        目标 HP 恒 > 0，故本效应不会击倒任何宝可梦，不触发 KO 钩子。
        """
        if hp <= 0:
            raise ValueError(f"counters_to_hp 剩余 HP 非法：{hp}")
        if scope not in ("opp_bench", "opp_active"):
            raise ValueError(f"counters_to_hp 范围非法：{scope}")
        opp = self.players[1 - self.current]
        targets = ([opp.active] if scope == "opp_active"
                   else list(opp.bench))
        touched = 0
        for fp in targets:
            if fp is None:
                continue
            goal = fp.max_hp - hp
            if goal <= fp.damage:
                continue
            delta = goal - fp.damage
            fp.damage = goal
            touched += 1
            self._log("counters", {"player": 1 - self.current,
                                   "target": fp.name, "dmg": delta,
                                   "remaining_hp": hp})
        self._log("counters_to_hp", {"hp": hp, "scope": scope,
                                     "targets": touched})

    def _apply_counters(self, tag: str, action: Optional[dict] = None,
                        holder: Optional[FieldPokemon] = None) -> bool:
        """``counters:<n>[:per:<counter>][:scope:opp[:<count>]|:scope:self][:selfko]``

        伤害指示物**直放**（2026-10-29 第二十批）。指示物不吃弱点/抗性、也不触发
        「受击反应」以外的伤害修正，因此**不能**复用 `damage`/`snipe` 字段。

        · 无 `:scope:` → 对手**战斗位**（历史语义：Haunter / Alakazam）
        · `:scope:opp[:<count>]` → 对手**任意**宝可梦（战斗位优先，与 snipe 的
          确定性默认同口径）；显式选择用 ``action["snipe"]`` 的位置码
          （0 = 战斗位，i≥1 = 后备 i-1，见 `_snipe_candidates`）
        · `:scope:self` → **己方**战斗位（Venture Bomb 的反面分支，第四十四批）
        · `:scope:ability:all` → **双方**全场「拥有特性」的宝可梦逐个各吃 N
          （Cofagrigus「Law of the Underworld」，2026-10-29 第二十八批）
        · `:selfko` → 结算完成后**持有者自身**被击倒（对手照常取奖赏；
          Dusclops / Dusknoir「诅咒爆裂」）

        返回 ``True`` 表示对局已结束。
        """
        body = str(tag)
        # 条件门（第九十五批）：`:gate:<cond>` 放在**最外层**，先剥。
        # 条件不成立 → 整条效果不执行（不是「放置 0 个」，而是不触发）。
        gate = None
        if ":gate:" in body:
            body, gate = body.split(":gate:", 1)
            if not _eval_condition(self, gate):
                self._log("no_effect", {"tag": tag, "reason": "gate_false"})
                return False
        selfko = body.endswith(":selfko")
        if selfko:
            body = body[: -len(":selfko")]
        scope, count = None, 1
        if ":scope:" in body:
            body, spec = body.split(":scope:", 1)
            _sp = spec.split(":")
            scope = _sp[0]
            if len(_sp) > 1:
                if _sp[1] == "all":
                    count = None                 # 全部目标（Uxie）
                elif _sp[1].isdigit():
                    count = max(1, int(_sp[1]))
        per = None
        if ":per:" in body:
            body, per = body.split(":per:", 1)
        parts = body.split(":")
        if len(parts) < 2 or not parts[1].isdigit():
            raise ValueError(f"counters tag 非法：{tag}")
        amt = int(parts[1]) * 10
        if per:
            amt *= _eval_counter(self, per)
        opp = self.players[1 - self.current]
        me = self.players[self.current]
        if scope in (None, ""):
            targets = [opp.active] if opp.active is not None else []
        elif scope == "opp":
            cands = self._snipe_candidates("opp")
            want = action.get("snipe") if isinstance(action, dict) else None
            if isinstance(want, (list, tuple)) and want:
                wanted = {int(x) for x in want}
                targets = [fp for pos, fp in cands if pos in wanted]
                if not targets:                  # 非法位置 → 退回确定性默认
                    targets = [fp for _, fp in (cands if count is None
                                                else cands[:count])]
            else:
                targets = [fp for _, fp in (cands if count is None
                                            else cands[:count])]
        elif scope == "opp_bench":
            # 只打**对手后备**（Mismagius「Assassin's Magic」，第九十五批）。
            # ⚠ 不能复用 `opp`：那条候选取「战斗位优先」，会把指示物放到战斗位，
            #   而卡面明写 Benched —— 静默错位（且战斗位吃弱点，伤害不同）。
            cands = [(pos, fp) for pos, fp in self._snipe_candidates("opp")
                     if pos != 0]
            want = action.get("snipe") if isinstance(action, dict) else None
            if isinstance(want, (list, tuple)) and want:
                wanted = {int(x) for x in want}
                targets = [fp for pos, fp in cands if pos in wanted]
                if not targets:
                    targets = [fp for _, fp in cands[:count]]
            else:
                targets = [fp for _, fp in (cands if count is None
                                            else cands[:count])]
        elif scope == "self":
            # 「put N damage counters on **your** Active Pokémon」
            # （Team Rocket's Venture Bomb 的反面分支，2026-11-03 第四十四批）
            # —— 指示物放在**自己**战斗位；不吃弱点/抗性，也不走 `self:N`
            # （后者是「受到 N 点伤害」，两者在「受击反应」之外仍要区分）。
            targets = [me.active] if me.active is not None else []
        elif scope == "ability":
            # 「each Pokémon that has an Ability (both yours and your opponent's)」
            # （Cofagrigus）——双方全场逐个筛选；判定口径与静态限定
            # `has_ability` 一致：卡面**印有**特性即算（被特性封锁不影响）。
            if count is not None:
                raise ValueError(f"counters 的 ability 范围只支持 all：{tag}")
            targets = [fp for fp in me.all_field() + opp.all_field()
                       if fp.pdef.abilities]
        else:
            raise ValueError(f"未识别的 counters 范围：{tag}")
        own_ids = {id(x) for x in me.all_field()}
        placed = False
        for fp in targets:
            if fp is None or fp.is_ko or amt <= 0:
                continue
            # Battle Cage：后备不受效果放置的伤害指示物（双方通用）
            if fp is not opp.active and self._no_bench_counters():
                self._log("blocked", {"target": fp.name,
                                      "by": "no_bench_counters"})
                continue
            # 「双方」范围下必须按**目标归属**结算击倒：己方那侧被击倒时
            # 是对手取奖赏（此前硬写死成「对手」，己方目标会算错奖赏方）
            owner = self.current if id(fp) in own_ids else 1 - self.current
            fp.damage += amt
            placed = True
            self._log("damage", {"player": owner, "target": fp.name,
                                 "dmg": amt, "counters": True,
                                 "scope": scope or "active"})
            if fp.is_ko:
                self._resolve_ko(owner, fp)
                if self.finished:
                    return True
        if not placed:
            self._log("no_effect", {"tag": tag, "reason": "无可放置目标"})
        if selfko and holder is not None and not holder.is_ko:
            return self._resolve_self_ko(holder)
        return False

    def _apply_knockout(self, tag: str, action: Optional[dict], blocked,
                         attacker: Optional["FieldPokemon"] = None) -> bool:
        """``knockout:<scope>`` —— 招式**直接击倒**（2026-10-28 第九批）

        scope：
          ``opp_active[:basic]``   对手战斗位
          ``opp_bench[:basic]``    对手后备 1 只
          ``opp_any``              对手任意 1 只
          ``opp_all``              **每一只**满足限定的对手宝可梦（第五十五批）
          ``opp_counters:<n>``     对手「恰好 n 个伤害指示物」的 1 只
          ``both_active``          双方战斗位同时被击倒
          ``least_hp``             场上（己方+对手）**剩余 HP 最少**者，
                                   排除「this Pokémon」(attacker)（第八十六批，
                                   Inteleon「Bring Down」）。确定性默认：并列时
                                   优先对手战斗位；其次对手后备，再次己方。

        限定（可叠加在 opp_active / opp_bench / opp_any / opp_all 后）：
          ``basic``            只挑基础宝可梦
          ``if:senergy``       该宝可梦身上有**特殊**能量（Haxorus「Bring Down the Axe」）
          ``if:hp_le:<n>``     **剩余** HP ≤ n（Yveltal ex「Soul Destroyer」）

        目标选择：``action["ko"]`` 位置码（0=对手战斗位，i≥1=对手后备 i-1）优先；
        否则取**确定性默认**（战斗位优先，其次后备顺序）——与 snipe 的默认口径一致。
        返回 ``True`` 表示对局已结束。
        """
        parts = tag.split(":")
        scope = parts[1] if len(parts) > 1 else ""
        opp = self.players[1 - self.current]
        me = self.players[self.current]
        want = action.get("ko") if isinstance(action, dict) else None
        wanted = {int(x) for x in want} if isinstance(want, (list, tuple)) else set()

        def _ko_one(owner: int, fp: FieldPokemon) -> None:
            fp.damage = fp.max_hp
            self._log("knockout_effect", {"player": self.current,
                                          "target": fp.name, "scope": scope})
            self._resolve_ko(owner, fp)

        if scope == "both_active":
            for owner, pl in ((1 - self.current, opp), (self.current, me)):
                fp = pl.active
                if fp is None or fp.is_ko:
                    continue
                if owner != self.current and blocked(fp):
                    self._log("blocked", {"target": fp.name, "by": "knockout"})
                    continue
                _ko_one(owner, fp)
                if self.finished:
                    return True
            return False

        if scope == "least_hp":
            # 「Choose a Pokémon in play (yours or your opponent's) that has the
            #  least HP remaining, except for this Pokémon, and it is Knocked
            #  Out.」（Inteleon「Bring Down」，第八十六批）。遍历双方场上，
            #  排除「this Pokémon」(attacker)，选 remaining HP 最小者击倒。
            #  确定性默认：并列时优先对手战斗位→对手后备→己方（先遍历 opp）。
            _cands = []
            for owner, pl in ((1 - self.current, opp), (self.current, me)):
                fp = pl.active
                if fp is not None and not fp.is_ko:
                    _cands.append((owner, fp))
                for fp in pl.bench:
                    if fp is not None and not fp.is_ko:
                        _cands.append((owner, fp))
            if attacker is not None:
                _cands = [(o, fp) for o, fp in _cands
                          if id(fp) != id(attacker)]
            if not _cands:
                self._log("no_effect", {"tag": tag,
                                        "reason": "除自身外无场上宝可梦"})
                return False
            _cands.sort(key=lambda op: (op[1].hp_remaining,
                                        0 if op[0] == 1 - self.current else 1))
            _owner, fp = _cands[0]
            if _owner != self.current and blocked(fp):
                self._log("blocked", {"target": fp.name, "by": "knockout"})
                return False
            _ko_one(_owner, fp)
            return self.finished

        if scope == "opp_active":
            pool = [(0, opp.active)] if opp.active is not None else []
            qual = ":".join(parts[2:])
        elif scope == "opp_bench":
            pool = [(i + 1, fp) for i, fp in enumerate(opp.bench)]
            qual = ":".join(parts[2:])
        elif scope == "opp_any":
            pool = self._snipe_candidates("opp")
            qual = ":".join(parts[2:])
        elif scope == "opp_all":
            # 第五十五批：**每一只**满足限定的对手宝可梦都被击倒
            # （Yveltal ex「Soul Destroyer」）。与 `opp_any`（只倒 1 只）必须分开。
            pool = self._snipe_candidates("opp")
            qual = ":".join(parts[2:])
        elif scope == "opp_counters":
            n = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else -1
            pool = [(pos, fp) for pos, fp in self._snipe_candidates("opp")
                    if fp.damage == n * 10]
            qual = ""
        else:
            raise ValueError(f"未识别的 knockout 范围：{tag}")
        if qual == "basic":
            pool = [(pos, fp) for pos, fp in pool if fp.stage == 0]
        elif qual == "if:senergy":
            # 「If your opponent's Active Pokémon has any Special Energy attached,
            # it is Knocked Out.」（Haxorus「Bring Down the Axe」）
            # ⚠ 特殊能量挂在 `senergy` 上（`energy` 只是基本能量的计数字典），
            # 用 `energy_counts()` 判会把**基本能量也算进去** ⇒ 无条件击倒。
            pool = [(pos, fp) for pos, fp in pool if fp.senergy]
        elif qual.startswith("if:hp_le:"):
            # 「… that has 50 HP or less **remaining**」——比的是剩余 HP，
            # 不是上限（用 max_hp 会把已经打残的错过、把满血的误伤）。
            _raw = qual.split(":")[2] if len(qual.split(":")) > 2 else ""
            if not _raw.isdigit():
                raise ValueError(f"未识别的 knockout 限定：{tag}")
            _lim = int(_raw)
            pool = [(pos, fp) for pos, fp in pool if fp.hp_remaining <= _lim]
        elif qual:
            raise ValueError(f"未识别的 knockout 限定：{tag}")
        if wanted:
            narrowed = [(pos, fp) for pos, fp in pool if pos in wanted]
            if narrowed:
                pool = narrowed
        if not pool:
            self._log("no_effect", {"tag": tag, "reason": "无合法击倒目标"})
            return False
        if scope == "opp_all":
            # 逐只结算（不是「倒第一只就算完」）。每倒一只都可能直接结束对局
            # （拿完奖赏），因此每次都要检查 `finished` 后再继续。
            for _pos, fp in list(pool):
                if fp.is_ko:
                    continue
                if blocked(fp):
                    self._log("blocked", {"target": fp.name, "by": "knockout"})
                    continue
                _ko_one(1 - self.current, fp)
                if self.finished:
                    return True
            return False
        fp = pool[0][1]
        if fp.is_ko:
            return False
        if blocked(fp):
            self._log("blocked", {"target": fp.name, "by": "knockout"})
            return False
        _ko_one(1 - self.current, fp)
        return self.finished

    # ---------- 静态效果（弱点改写） ----------
    def _weakness_override(self, attacker: FieldPokemon,
                           defender: FieldPokemon) -> Optional[str]:
        """攻击方场上 ``weak_change:<type>[:<filter>]`` 特性 → 守方生效弱点

        filter：``all``（默认）| 属性名（如 ``dragon``，仅对该属性守方生效）。
        返回 ``None`` 表示无改写（用守方卡面弱点）。
        """
        for fp in self.players[self.current].all_field():
            if fp is None:
                continue
            for ab in fp.pdef.abilities:
                if not ab.tag.startswith("weak_change:"):
                    continue
                parts = ab.tag.split(":")
                newt = parts[1] if len(parts) > 1 else ""
                filt = parts[2] if len(parts) > 2 else "all"
                if filt != "all" and defender.pdef.ptype != filt:
                    continue
                return newt
        return None

    # ---------- 击倒/奖赏 ----------
    def _resolve_ko(self, owner: int, fp: FieldPokemon) -> None:
        if getattr(self, "_pending", None) is not None:
            # S6b：正在等「取哪张奖赏卡」的选择 → 本只排队（见 `_drain_ko_queue`）。
            # ⚠ 不能按 `fp.is_ko` 过滤：那是「伤害 ≥ HP」（待击倒）的判据，
            #   正要处理的这只**恰好**满足它 —— 用它当「已处理」会把这只好几只
            #   全部丢掉（实测双 KO 时队列恒为 0，第二只永远不结算）。
            #   重复入队无害：补做时若已被处理，`pos is None` 会直接返回。
            if fp is not None:
                self._ko_q().append((owner, fp))
            return
        p = self.players[owner]
        opp = self.players[1 - owner]
        pos = None
        if p.active is fp:
            pos = "active"
        elif fp in p.bench:
            pos = "bench"
        if pos is None:
            return
        # 被击倒触发所需的**击倒瞬间**快照（2026-10-28 第十批）：
        # 攻击方（反制目标）与被击倒者身上的基础能量（Huntail 收回手牌）。
        _was_active = pos == "active"
        _attacker = opp.active if self._ko_from_attack else None
        _energy_snapshot = dict(fp.energy)
        _ptype = str(fp.pdef.ptype).lower()
        # S2c（2026-11-08）：**击倒事件**。必须在本体离开场上**之前**发出 ——
        # 道具订阅（Amulet of Hope / Heavy Baton）挂在被击倒的那只身上，
        # 一旦先移除就再也扫不到它了。
        _ko_ctx = {"player": owner, "leaving": fp,
                   "by_attack": bool(self._ko_from_attack),
                   "was_active": pos == "active",
                   "turn_owner": self.current}
        # ⚠ 事件必须在**移出场上之前**发出：订阅可能挂在被击倒那只的**道具**上
        #   （Amulet of Hope / Heavy Baton），一旦先移除，`_watchers` 就扫不到它了。
        #   （第一版把 emit 放在移除之后 → 道具订阅永远收不到，测试实测 0 张入手。）
        events.emit(self, "ko", _ko_ctx)
        if pos == "active":
            p.active = None
        else:
            p.bench.remove(fp)
        p.discard.append(fp.name)
        # 供弃牌堆回收类效果重建：化石卡**本体是训练家卡**，必须放回 TrainerRef，
        # 否则回收类效果会把它当成一张宝可梦卡（卡库里没有这张宝可梦）。
        p.discard_refs.append(getattr(fp, "fossil_ref", None)
                              or PokeRef(fp.pdef))
        self._log("knockout", {"player": 1 - owner, "victim_owner": owner,
                               "card": fp.name, "pos": pos})
        # v1.4：记录「本次击倒是否来自招式」——供条件加伤
        # （dmg_plus:N:if:opp_ko_last_turn）使用；在受害者回合开始清零。
        if self._ko_from_attack:
            self._ko_flags()[owner] += 1
            if _name_key(fp.name).startswith("teamrocket"):
                self._ko_tr_flags()[owner] += 1
        # 2026-11-02：Mega ex 被击倒时对手取 **3** 张（此前一律按 ex 的 2 张，
        # 导致卡池准入直接把整个 Mega 族拒收）。判定顺序必须先 mega 再 ex ——
        # Mega ex 的 `is_ex` 同样为 True（它确实是 ex 的一种，条件 tag 仍要算 ex）。
        if fp.pdef.is_mega:
            give = self.cfg.mega_prizes
        elif fp.pdef.is_ex:
            give = self.cfg.ex_prizes
        else:
            give = 1
        # 效果族奖赏卡修正（「你的对手少拿 1 张奖赏卡」/「多拿 1 张」）
        give = max(0, give + families.prize_delta(self, opp, fp))
        # 被击倒方的「少拿」钩子（2026-11-04 第五十批）：Shedinja / Mega Gengar /
        # Munkidori / Lillie's Pearl。放在 bonus 之前算——先扣减少、再多拿。
        give = max(0, give - self._ko_prize_reduction(owner, fp))
        # 特殊能量修正（Legacy Energy）：仅限「被对手招式伤害击倒」，每局一次
        if self._ko_from_attack:
            give = max(0, give + special_energy.prize_delta(self, fp))
        # 被击倒触发：多取奖赏（Togekiss「Wonder Kiss」仅战斗位 / Hydreigon ex
        # 「Greedy Eater」对手基础宝可梦任意位置，第八十七批）
        give = max(0, give + self._ko_prize_bonus(owner, fp, _was_active))
        # 第八十九批：本回合训练家奖赏加成（Briar「Tera」）
        give = max(0, give + self._trainer_prize_bonus(owner, _attacker,
                                                       _was_active))
        # S3（2026-11-08）：取奖赏走**真实卡对象**（进手牌），不再是「计数器 -N」。
        # S6b（2026-11-08）：有**面朝上**奖赏卡且由本人取时，先挂起「取哪张」的
        # 选择（真决策）；其余情况确定性取。尾段（KO 触发 / 胜负 / 补位）抽到
        # `_ko_after_prizes`，两条路径共用 —— 挂起时由 `_apply_choice_result` 恢复。
        _ko_state = {"owner": owner, "fp": fp, "pos": pos,
                     "was_active": _was_active, "attacker": _attacker,
                     "energy": _energy_snapshot, "ptype": _ptype}
        self._take_prizes(1 - owner, give, _ko_state)

    def _take_prizes(self, taker: int, n: int, ko_state: dict) -> None:
        """取奖赏（S6b）：面朝上 + 本人取 → 挂起「取哪张」；否则确定性取

        判据三条**同时**成立才挂起：
          · 有**面朝上**的奖赏卡（面朝下的互相不可区分，选它们不构成决策）；
          · 取奖赏方 == 当前行动方（对手取奖赏时代理没有「自己选」的入口，
            与 `shuffle_opp_hand` 等「对手选择由引擎确定性代理」的既有口径一致）；
          · 只取 **1 张**。取多张时「逐张选择」要连开多次选择，当前实现按
            **面朝下优先**确定性取并写 `prize_pick_auto` 日志（**显式记录的简化**，
            不是静默决定）。
        """
        p = self.players[taker]
        ups = zones.face_up_slots(p)
        if ups and taker == self.current and int(n) == 1:
            opts = [{"mode": "up", "slot": i,
                     "display": f"（正面）{_card_label(p.prizes[i].ref)}",
                     "label": _card_label(p.prizes[i].ref)}
                    for i in ups]
            opts.append({"mode": "down", "display": "面朝下的奖赏卡（随机）"})
            self._open_choice("pick_prize", opts, min_pick=1, max_pick=1,
                              tag="prize_pick",
                              ctx={"taker": int(taker), "n": int(n),
                                   "ko": ko_state})
            return
        if ups and int(n) > 1:
            self._log("prize_pick_auto", {"player": taker, "n": int(n),
                                          "faces_up": len(ups),
                                          "reason": "多张取奖赏：逐张选择未建模"
                                                    "（面朝下优先）"})
        taken = zones.take_prizes(self, taker, int(n))
        self._ko_after_prizes(ko_state, taken)

    def _ko_after_prizes(self, ko_state: dict, taken: int) -> None:
        """取完奖赏后的 KO 尾段（S6b 从 `_resolve_ko` 抽出，供两条路径复用）"""
        owner = int(ko_state["owner"])
        fp = ko_state["fp"]
        pos = ko_state["pos"]
        p = self.players[owner]
        opp = self.players[1 - owner]
        _was_active = ko_state["was_active"]
        _attacker = ko_state["attacker"]
        _energy_snapshot = ko_state["energy"]
        _ptype = ko_state["ptype"]
        opp.prizes_taken_this_turn += taken   # 「上回合取走几张」记账
        self._log("prize", {"player": 1 - owner, "take": taken,
                            "left": opp.prizes_left})
        # v1.3：击倒触发型特性（ko_draw:N，如 Fezandipiti ex「逆转局势」）
        # 在被击倒方的**剩余场上**扫描；先结算抽牌再判定胜负/补位
        self._trigger_ko_draw(owner)
        self._trigger_ko_abilities(owner, fp, _was_active, _attacker,
                                   _energy_snapshot, _ptype)
        # Area Zero：被击倒的若是 Tera 宝可梦，后备上限可能立刻回落
        self._enforce_bench_limit()
        if opp.prizes_left == 0:
            self._finish(1 - owner, "prizes", {"taker": 1 - owner})
            return
        if pos == "active":
            if p.bench:
                p.active = p.bench.pop(0)
                p.active.moved_active_turn = self.turn_number
                p.active.clear_status()
                self._log("promote", {"player": owner, "to": p.active.name})
            else:
                self._finish(1 - owner, "no_pokemon", {"loser": owner})

    def _ko_q(self) -> list:
        """挂起期间待补做的击倒队列（惰性创建，兼容 `object.__new__` 装配的夹具）"""
        v = getattr(self, "_ko_queue", None)
        if v is None:
            v = []
            self._ko_queue = v
        return v

    def _drain_ko_queue(self) -> None:
        """补做挂起期间被拦下的击倒（S6b）

        每处理一只都可能**再开**一次「取哪张」选择 → 一旦挂起就停下，
        剩下的留到下次 `_close_choice` 继续（顺序 = 入队顺序）。
        """
        q = self._ko_q()
        while q and self._pending is None and not self.finished:
            owner, fp = q.pop(0)
            self._resolve_ko(owner, fp)

    def _trigger_ko_draw(self, owner: int) -> None:
        """己方宝可梦被击倒时触发 ``ko_draw:N`` 特性（每回合一次限制不适用）"""
        p = self.players[owner]
        for fp in p.all_field():
            if fp is None:
                continue
            for ab in fp.pdef.abilities:
                if not ab.tag.startswith("ko_draw:"):
                    continue
                n = int(ab.tag.split(":")[1])
                got = self._safe_draw(owner, n, source=f"ko_draw:{ab.name}")
                self._log("ability", {"player": owner, "name": ab.name,
                                      "from": fp.name, "trigger": "on_knockout",
                                      "detail": f"ko draw {n}", "drew": got})
                if self.finished:
                    return

    def _ko_prize_bonus(self, owner: int, fp: FieldPokemon,
                         was_active: bool) -> int:
        """被击倒触发：**取奖赏方**因对手宝可梦被击倒而多取 N 张

        卡面原型：
          · Togekiss「Wonder Kiss」（plain / ``coin``）：仅对手**战斗位**被击倒时。
          · Hydreigon ex「Greedy Eater」（``opp_basic``，第八十七批）：被击倒的是
            对手**基础**宝可梦（任意位置，含后备）即触发。

        ``fp`` 为被击倒的宝可梦（属 ``owner`` 方，即取奖赏方的对手）；``was_active``
        标记其是否处于战斗位。只取**第一条**命中的特性（不叠加）。
        """
        taker = 1 - owner
        for holder in self.players[taker].all_field():
            if holder is None:
                continue
            for ab in holder.pdef.abilities:
                tag = str(ab.tag)
                if not tag.startswith("ko_prize_plus:"):
                    continue
                parts = tag.split(":")
                n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
                if n <= 0:
                    continue
                conds = parts[2:]
                if "opp_basic" in conds:
                    # 对手基础宝可梦被击倒（任意位置，含后备）才触发
                    if getattr(fp.pdef, "stage", 0) != 0:
                        continue
                else:
                    # 默认（Togekiss「Wonder Kiss」）：仅对手战斗位被击倒
                    if not was_active:
                        continue
                if "coin" in conds:
                    res = "heads" if self.rng.random() < 0.5 else "tails"
                    self._log("coin", {"player": taker, "result": res,
                                       "tag": tag})
                    if res != "heads":
                        continue
                self._log("ko_prize_plus", {"player": taker,
                                            "from": holder.name, "n": n})
                return n
        return 0

    def _trainer_prize_bonus(self, owner: int, attacker: Optional[FieldPokemon],
                              was_active: bool) -> int:
        """本回合训练家奖赏加成（Briar「白蕾」，第八十九批）

        「During this turn, if your opponent's Active Pokémon is Knocked Out by
         damage from an attack used by your Tera Pokémon, take N more Prize
         card.」—— 只在**本回合**（`prize_bonus_until == turn_number`）、被击倒
        的是对手**战斗位**、且攻击方为 Tera 宝可梦时触发。
        """
        taker = 1 - owner
        t = self.players[taker]
        n = int(getattr(t, "prize_bonus_n", 0) or 0)
        if n <= 0 or int(getattr(t, "prize_bonus_until", -1)) != self.turn_number:
            return 0
        if not was_active:
            return 0
        if getattr(t, "prize_bonus_tera", False):
            if attacker is None or "tera" not in str(
                    getattr(attacker.pdef, "category", "") or "").lower():
                return 0
        self._log("prize_bonus", {"player": taker, "n": n})
        return n

    def _ko_prize_reduction(self, owner: int, fp: FieldPokemon) -> int:
        """被击倒方的「对手**少拿**奖赏卡」钩子 → 减少张数（第五十批）

        卡面四种形态（差别全在条件，见 `agent/effects._KO_PRIZE_MINUS`）：
          · ``ko_prize_minus:all:from_ex``              一张也不给（Shedinja）
          · ``ko_prize_minus:1:from_ex:team_darkness``  同队 {D} 被击倒（Mega Gengar）
          · ``ko_prize_minus:1:if_named:pecharunt``     且场上有该卡（Munkidori）
          · ``ko_prize_minus:1``                        道具所附宝可梦（Lillie's Pearl）

        ⚠ 与 `_ko_prize_bonus` 方向相反、挂载方也不同：那边挂在**取奖赏方**，
        这边挂在**被击倒方**（含同队与道具）。同一条 `_resolve_ko` 里两者都要算。
        """
        p = self.players[owner]
        attacker = (self.players[1 - owner].active
                    if self._ko_from_attack else None)
        # Mega 的 `is_ex` 同样为 True —— 卡面写「Pokémon {ex}」时它也算
        from_ex = bool(attacker is not None
                       and getattr(attacker.pdef, "is_ex", False))

        def _eval(tag: str) -> int:
            parts = str(tag).split(":")
            raw = parts[1] if len(parts) > 1 else ""
            conds = parts[2:]
            if "from_ex" in conds and not from_ex:
                return 0
            if any(c.startswith("team_") for c in conds):
                # 「1 of your {X} Pokémon」= 同队判定：被击倒的那只必须是指定属性
                color = next(c[len("team_"):] for c in conds
                             if c.startswith("team_"))
                if str(fp.pdef.ptype).lower() != color:
                    return 0
            if "if_named" in conds:
                i = conds.index("if_named")
                key = conds[i + 1] if len(conds) > i + 1 else ""
                if not key or not any(
                        key in _name_key(x.pdef.name) for x in p.all_field()):
                    return 0
            if raw == "all":
                return 10 ** 6            # 「can't take any Prize cards」
            return int(raw) if raw.isdigit() else 0

        best = 0
        for ab in fp.pdef.abilities:
            t = str(getattr(ab, "tag", "") or "")
            if t.startswith("ko_prize_minus:"):
                best = max(best, _eval(t))
        # 同队形态（`team_*`）：特性可能挂在**没被击倒**的那只身上
        for other in p.all_field():
            if other is None or other is fp:
                continue
            for ab in other.pdef.abilities:
                t = str(getattr(ab, "tag", "") or "")
                if t.startswith("ko_prize_minus:") and "team_" in t:
                    best = max(best, _eval(t))
        # 道具形态：所附宝可梦被击倒（道具不在场时它已经进弃牌堆了）
        tool = "" if getattr(fp, "tool_disabled", False) else str(fp.tool or "")
        if tool.startswith("ko_prize_minus:"):
            best = max(best, _eval(tool))
        return best

    def _trigger_ko_abilities(self, owner: int, fp: FieldPokemon,
                              was_active: bool,
                              attacker: Optional[FieldPokemon],
                              energy: dict, ptype: str) -> None:
        """被击倒触发（2026-10-28 第十批）

        ① ``on_ko:counter:N``（Maractus）：**自身**在战斗位被招式击倒时，
           对攻击方放 N 点伤害（「even if this Pokémon is Knocked Out」）。
        ② ``on_ko:energy_to_hand:<type>``（Huntail）：己方 ``<type>`` 宝可梦被
           招式击倒时，把**该宝可梦身上的基础能量**收回手牌（而非弃牌堆）。
        卡面的「may」按**自动使用**处理（与 ko_draw / on_damaged 同口径：
        使用可选增益效果属于合法选择，不改变规则结算）。
        """
        p = self.players[owner]
        if was_active and attacker is not None:
            # ②-b 双触发时机（Flygon）：战斗中位被击倒时**重放**同一条进化特性
            for ab in fp.pdef.abilities:
                if str(ab.tag) != "on_ko:replay_evolve_act":
                    continue
                for ab2 in fp.pdef.abilities:
                    tag2 = str(ab2.tag)
                    if not tag2.startswith("evolve_act:"):
                        continue
                    subs = tuple(x for x in tag2[len("evolve_act:"):].split("|")
                                 if x)
                    if subs:
                        self._log("ko_trigger", {
                            "player": owner, "ability": ab2.name,
                            "from": fp.name, "kind": "replay_evolve_act",
                            "detail": "|".join(subs)})
                        # 重放时必须**站在特性拥有者一侧**结算：此时代码里的
                        # `self.current` 仍是攻击方，直接执行会把「opp」类效果
                        # （`mill:2:opp`）算到错误的玩家头上。
                        _prev = self.current
                        self.current = owner
                        try:
                            self._apply_attack_effects(fp, subs, action={})
                        finally:
                            self.current = _prev
            for ab in fp.pdef.abilities:
                tag = str(ab.tag)
                if not tag.startswith("on_ko:counter:"):
                    continue
                parts = tag.split(":")
                n = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
                if n <= 0:
                    continue
                attacker.damage += n
                self._log("ko_trigger", {"player": owner, "ability": ab.name,
                                         "from": fp.name, "kind": "counter",
                                         "n": n, "target": attacker.name})
                if attacker.is_ko and not self.finished:
                    self._resolve_ko(1 - owner, attacker)
        for holder in p.all_field():
            if holder is None:
                continue
            for ab in holder.pdef.abilities:
                tag = str(ab.tag)
                if not tag.startswith("on_ko:energy_to_hand:"):
                    continue
                parts = tag.split(":")
                want = parts[2] if len(parts) > 2 else ""
                if not want or ptype != want:
                    continue
                n = int(energy.get(want, 0))
                if n <= 0:
                    continue
                from .cards import EnergyDef, EnergyRef
                for _ in range(n):
                    p.hand.append(EnergyRef(EnergyDef(want)))
                self._log("ko_trigger", {"player": owner, "ability": ab.name,
                                         "from": holder.name,
                                         "kind": "energy_to_hand",
                                         "n": n, "etype": want})

    # ---------- 观测 ----------
    def view(self, pi: Optional[int] = None) -> dict:
        pi = self.current if pi is None else pi
        me, opp = self.players[pi], self.players[1 - pi]

        def atk(a):
            """招式序列化（场上与手牌共用，保证卡面数据一致）"""
            return {"name": a.name, "name_zh": a.name_zh,
                    "display": a.name_zh or a.name,
                    "cost": a.cost_total,
                    "cost_map": list(a.cost), "damage": a.damage,
                    "tags": list(a.effects),
                    "desc": a.desc, "desc_zh": a.desc_zh,
                    "display_desc": a.display_desc}

        def abil(a):
            """特性序列化（场上与手牌共用）"""
            return {"name": a.name, "name_zh": a.name_zh,
                    "display": a.name_zh or a.name,
                    "tag": a.tag, "location": a.location,
                    "desc": a.desc, "desc_zh": a.desc_zh,
                    "display_desc": a.display_desc}

        def pkmn(fp: Optional[FieldPokemon]):
            if fp is None:
                return None
            d = fp.summary()
            d["type"] = "pokemon"                 # 显式类型：前端据此选择卡面渲染分支
            d["ptype"] = fp.pdef.ptype            # 属性：卡面配色与筛选依据
            d["weakness"] = fp.pdef.weakness
            d["resistance"] = fp.pdef.resistance
            d["retreat"] = fp.pdef.retreat
            d["ex"] = fp.pdef.is_ex
            d["evolves_from"] = fp.pdef.evolves_from
            d["ability_used"] = sorted(fp.ability_used)
            # 化石卡：场上形态是训练家卡变来的「宝可梦」（前端标卡片类型）
            d["fossil"] = bool(getattr(fp, "fossil", False))
            # i18n：英文名保留（数据键），新增 name_zh / display（中文展示，前端优先用）
            d["name_zh"] = fp.pdef.name_zh
            d["display"] = fp.pdef.display
            d["abilities"] = [abil(a) for a in fp.pdef.abilities]
            d["attacks"] = [atk(a) for a in fp.pdef.attacks]
            return d

        hand = []
        for c in me.hand:
            if isinstance(c, PokeRef):
                # 手牌同样带**完整卡面数据**（属性/招式/弱点/撤退）：前端按真实卡牌渲染，
                # 印在卡面上的信息不该因为「牌还在手上」而缺失
                hand.append({"type": "pokemon", "name": c.pdef.name,
                             "name_zh": c.pdef.name_zh,
                             "display": c.pdef.display,
                             "hp": c.pdef.hp, "stage": c.pdef.stage,
                             "ptype": c.pdef.ptype,
                             "weakness": c.pdef.weakness,
                             "resistance": c.pdef.resistance,
                             "retreat": c.pdef.retreat,
                             "ex": c.pdef.is_ex,
                             "evolves_from_zh": _evolves_from_zh(
                                 c.pdef.evolves_from),
                             "evolves_from": c.pdef.evolves_from,
                             "abilities": [abil(a) for a in c.pdef.abilities],
                             "attacks": [atk(a) for a in c.pdef.attacks]})
            elif isinstance(c, EnergyRef):
                hand.append({"type": "energy", "name": c.edef.name,
                             "name_zh": c.edef.name_zh,
                             "display": c.edef.display,
                             "etype": c.edef.type})
            elif isinstance(c, TrainerRef):
                hand.append({"type": c.tdef.kind, "name": c.tdef.name,
                             "name_zh": c.tdef.name_zh,
                             "display": c.tdef.display,
                             "tag": c.tdef.tag})
        return {
            "me": pi,
            "turn": self.turn_number,
            "phase": self.phase,
            "me_state": {
                "hand": hand,
                "active": pkmn(me.active),
                "bench": [pkmn(b) for b in me.bench],
                "prizes_left": me.prizes_left,
                "deck_count": len(me.deck),
                "discard_count": len(me.discard),
                "attached_energy_this_turn": me.attached_energy_this_turn,
                "supporter_used": me.supporter_used,
            },
            "opp_state": {
                "hand_count": len(opp.hand),
                "active": pkmn(opp.active),
                "bench": [pkmn(b) for b in opp.bench],
                "prizes_left": opp.prizes_left,
                "deck_count": len(opp.deck),
                "discard_count": len(opp.discard),
            },
            "legal": self.legal_actions(pi),
            "finished": self.finished,
        }


# ---------- 工具 ----------


def _coin_cond_result(cond: str, n: int, heads: int) -> tuple:
    """``cond:<mode>[:<k>]:<sub>`` → ``(是否满足, 子效果)``

    多币条件（2026-10-29 第二十二批）：与伤害子效果共用同一次抛币的枚数结果。
      · ``all_tails:<sub>``        全部为反面（正面数 = 0）
      · ``all_heads:<sub>``        全部为正面
      · ``at_least_heads:<k>:<sub>`` 正面数 ≥ k
      · ``at_least_tails:<k>:<sub>`` 反面数 ≥ k

    未知形态**抛错**（禁止静默不生效）。
    """
    body = str(cond)[5:] if str(cond).startswith("cond:") else ""
    parts = body.split(":")
    mode = parts[0] if parts else ""
    if mode == "all_tails":
        return heads == 0, ":".join(parts[1:])
    if mode == "all_heads":
        return n > 0 and heads == n, ":".join(parts[1:])
    if mode in ("at_least_heads", "at_least_tails"):
        if len(parts) < 2 or not parts[1].isdigit():
            raise ValueError(f"未知抛币条件：{cond}")
        k = int(parts[1])
        got = heads if mode == "at_least_heads" else max(0, n - heads)
        return got >= k, ":".join(parts[2:])
    raise ValueError(f"未知抛币条件：{cond}")


def _name_hint(action: dict) -> str:
    """从动作里取出用户想用的名称（用于报错提示）"""
    for k in _NAME_KEYS:
        if action.get(k):
            return str(action[k])
    return str(action.get("attack", "?"))


def _candidates_from(objs, keys) -> list:
    """把对象序列的若干名称字段摊平成候选名字列表"""
    out = []
    for o in objs:
        for k in keys:
            v = getattr(o, k, None) if not isinstance(o, dict) else o.get(k)
            if v:
                out.append(v)
    return out


def _attack_index_from_action(attacks, action: dict):
    """招式动作 → 下标：优先 `attack` 数字，否则按**中文/英文招式名**解析

    支持的中文指令示例：`{"verb":"attack","attack_name":"幻影俯冲"}`、
    `{"verb":"attack","name":"Phantom Dive"}`、`{"verb":"attack","display":"幻影俯冲"}`。
    """
    if action.get("attack") is not None:
        return int(action["attack"])
    from .commands import best_match
    labels = _candidates_from(attacks, ("name_zh", "name"))
    q = _name_hint(action)
    idx = best_match(labels, q)
    return idx


def _hand_index_from_action(hand, action: dict, want: str = "trainer"):
    """手牌动作 → 手牌下标：优先 `hand`/`index` 数字，否则按**卡名**解析

    want 指定候选范围：
      `trainer`（默认，训练家卡）· `basic`（基础宝可梦，供 play）·
      `stage`（可进化宝可梦，供 evolve）· `any`（任意卡）。
    """
    if action.get("hand") is not None:
        return int(action["hand"])
    if action.get("index") is not None:
        return int(action["index"])
    from .commands import best_match

    def _ok(c) -> bool:
        if want == "basic":
            return isinstance(c, PokeRef) and c.pdef.stage == 0
        if want == "stage":
            return isinstance(c, PokeRef) and c.pdef.stage > 0
        if want == "any":
            return True
        return isinstance(c, TrainerRef)

    def _label(c) -> str:
        if isinstance(c, TrainerRef):
            return c.tdef.display
        if isinstance(c, PokeRef):
            return c.pdef.display
        return getattr(getattr(c, "edef", None), "display", "") or "能量"

    idxs = [i for i, c in enumerate(hand) if _ok(c)]
    labels = [_label(hand[i]) for i in idxs]
    pos = best_match(labels, _name_hint(action))
    return None if pos is None else idxs[pos]


def _evolves_from_zh(name):
    """进化前卡的中文展示名（未登记则空串）"""
    from .cards import pokemon_def
    pd = pokemon_def(name)
    return pd.name_zh if pd is not None else ""


# ---------------- 效果派发通用工具（v1.3 tag 家族） ----------------
def _discard_from_hand(battle, p: PlayerState, n: int) -> int:
    """从手牌末尾弃 n 张（文本层与类型化层同步）→ 返回实际弃置数"""
    dropped = 0
    for _ in range(max(0, n)):
        if not p.hand:
            break
        battle._to_discard(p, p.hand.pop())
        dropped += 1
    return dropped


def _discard_hand_energy(battle, p: PlayerState, n: int,
                         etype: Optional[str] = None) -> int:
    """从手牌弃 n 张能量（可限定属性）→ 实际弃置数

    ``etype``（B3 · 2026-10-28）：卡面「Discard 2 Basic {G} Energy cards from
    your hand」要求**指定属性**；此前只能弃任意能量 → 会把火能量当草能量弃掉
    （静默算错）。限定属性时按 ``EnergyRef.edef.type`` 精确过滤。
    """
    from .cards import EnergyRef
    dropped = 0
    for i in range(len(p.hand) - 1, -1, -1):
        if dropped >= n:
            break
        c = p.hand[i]
        if not isinstance(c, EnergyRef):
            continue
        if etype and str(c.edef.type).lower() != etype.lower():
            continue
        battle._to_discard(p, p.hand.pop(i))
        dropped += 1
    return dropped


def _hand_energy_of(p: PlayerState, etype: Optional[str] = None) -> int:
    """手牌中的**基本**能量张数（``etype`` 非空时只数该属性）

    特殊能量不计入：调用点都是「discard a Basic {X} Energy」这类代价。
    """
    from .cards import EnergyRef
    return sum(1 for c in p.hand
               if isinstance(c, EnergyRef)
               and not getattr(c.edef, "is_special", False)
               and (not etype or str(c.edef.type).lower() == etype.lower()))


def _parse_hand_energy_tag(tag: str) -> tuple:
    """``discard_hand_energy:N[:type:<c>][:required]`` → ``(n, etype, required)``"""
    parts = tag.split(":")
    n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    etype, required = None, False
    i = 2
    while i < len(parts):
        if parts[i] == "type" and i + 1 < len(parts):
            etype = parts[i + 1].lower()
            i += 2
            continue
        if parts[i] == "required":
            required = True
        i += 1
    return n, etype, required


def _parse_coin_flip_tag(tag: str) -> dict:
    """``coin_flip:heads:<a>[|tails:<b>]`` → ``{"heads": a, "tails": b}``

    每段必须以 ``heads:`` / ``tails:`` 开头 —— 子效果自身含冒号，只有显式
    分支前缀能无歧义切分。未知段 → 抛错（绝不静默丢效果）。
    """
    body = str(tag)[len("coin_flip:"):]
    out: dict = {}
    for seg in body.split("|"):
        seg = seg.strip()
        if not seg:
            continue
        if seg.startswith("heads:"):
            out["heads"] = seg[len("heads:"):]
        elif seg.startswith("tails:"):
            out["tails"] = seg[len("tails:"):]
        else:
            raise ValueError(f"coin_flip 分支前缀非法：{tag}")
    if not out:
        raise ValueError(f"coin_flip tag 非法：{tag}")
    return out


def _draw_spec(battle, tag: str) -> tuple:
    """``draw:N`` / ``draw:per:<counter>`` / ``draw:N:if:<cond>`` → ``(n, cond)``

    ``cond`` 为 ``None`` 表示无条件。条件本身可带冒号参数
    （``own_hand_at_least:10``），因此按 ``if`` 的位置整体截取，不能只取 parts[2]。
    """
    parts = tag.split(":")
    cond = None
    if "if" in parts[1:]:
        i = parts.index("if", 1)
        cond = ":".join(parts[i + 1:]) or None
        parts = parts[:i]
    if len(parts) > 3 and parts[2] == "per":
        n = int(parts[1]) * _eval_counter(battle, parts[3])
    else:
        n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    return max(0, n), cond



def _apply_heal(battle, p: PlayerState, tag: str, *,
                caster: Optional[FieldPokemon] = None,
                action: Optional[dict] = None) -> tuple:
    """回复（``heal:N[:<scope>][:type:<c>]``）→ ``(实际回复总量, 目标数)``

    **scope 是语义的一部分，不是可选修饰**（2026-10-27 修）：
      · 省略           回复 ``caster``（无 caster 时取己方 active）
      · ``active``     回复己方**战斗位**（卡面「your Active Pokémon」）
      · ``choose``     回复己方 1 只（``action["target"]`` 位置码：0=战斗位）
      · ``all``        回复己方**全体**
      · ``bench``      回复己方**后备全体**
      · ``basic``      回复己方全体**基础**宝可梦
      · ``bench_one``  回复己方后备中 1 只（可带 ``type:<c>`` 属性限定）
      · ``mega``       回复己方 1 只 **Mega**（Wally's Compassion）

    历史缺陷（真实 bug，非假设）：此函数的前身是 ``attacker.damage -= N``，
    于是卡面「回复**己方全部**」被压成「只回**攻击方自己**」。池内实测 5 处：
    Leafeon ex（后备全部）/ Ethan's Ho-Oh ex（己方全部）/ Leavanny（基本全部）/
    Meloetta（后备 {P} 一只）/ Floragato（抛币分支内却记成无条件）。
    判不出精确范围时**解析层必须返回 None**，引擎绝不兜底——宁可未建模。
    """
    parts = tag.split(":")
    # 2026-10-28（第十二批）：尾缀 `:energy` = 「回复后把该目标的全部能量收回
    # 手牌」（Wally's Compassion）；尾缀 `:discard` = 「回复后弃该目标 1 个能量」
    # （Super Potion）。两者都隐含「仅当真的回复过才执行」。
    energy_to_hand = len(parts) > 2 and parts[-1] == "energy"
    discard_one = len(parts) > 2 and parts[-1] == "discard"
    if energy_to_hand or discard_one:
        parts = parts[:-1]
    # 2026-10-28（第九批）：`heal:all[:<scope>]` = 回满（数值位置写 `all`）。
    # 单目标/群体范围语义与 `heal:N` 完全一致，只有「回复量」不同。
    full = len(parts) > 1 and parts[1] == "all"
    equal = len(parts) > 1 and parts[1] == "equal"
    amt = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    if equal:
        # 「the same amount of damage you did to your opponent's Active Pokémon」
        amt = int(getattr(battle, "_last_damage_dealt", 0) or 0)
    # 第六十二批（2026-11-04）：条件回复的两个新段
    #   · `alt:<m>` + `if_name:<key>`：「… heal 100 damage from it **instead**」
    #     （Arven's Sandwich）—— 目标是具名阵营时改用更大的值，**不是**额外再回一次。
    #   · `need:<cond>`：把条件写成**目标前提**（Jumbo Ice Cream 的「has 3 or more
    #     Energy attached」）。不满足时这张卡本来就不该能打，走到这里是调用方漏了
    #     legals 过滤 —— 回 0，而不是照常回血。
    alt_n, need_c, name_key = 0, None, None
    maxleft = 0
    scope, ptype = "", None
    i = 2
    while i < len(parts):
        if parts[i] == "type" and i + 1 < len(parts):
            ptype = parts[i + 1].lower()
            i += 2
            continue
        if parts[i] == "alt" and i + 1 < len(parts):
            alt_n = int(parts[i + 1]) if parts[i + 1].isdigit() else 0
            i += 2
            continue
        if parts[i] == "if_name" and i + 1 < len(parts):
            name_key = re.sub(r"[^a-z0-9]", "", parts[i + 1].lower())
            i += 2
            continue
        if parts[i] == "maxleft" and i + 1 < len(parts):
            maxleft = int(parts[i + 1]) if parts[i + 1].isdigit() else 0
            i += 2
            continue
        if parts[i] == "need":
            need_c = ":".join(parts[i + 1:])
            break
        if parts[i]:
            scope = parts[i]
        i += 1
    if need_c and not _eval_condition(battle, need_c):
        battle._log("no_effect", {"tag": tag, "reason": "目标前提不满足"})
        return 0, 0

    def _ok(fp: FieldPokemon) -> bool:
        return not ptype or str(fp.pdef.ptype).lower() == ptype

    field = p.all_field()
    if scope == "all":
        targets = [fp for fp in field if _ok(fp)]
    elif scope == "evolved":
        # 「each of your Evolution Pokémon」= 己方场上全部进化形态（stage ≥ 1）
        targets = [fp for fp in field if fp.stage > 0 and _ok(fp)]
    elif scope == "bench":
        targets = [fp for fp in p.bench if _ok(fp)]
    elif scope == "basic":
        targets = [fp for fp in field if fp.stage == 0 and _ok(fp)]
    elif scope == "active":
        fp = p.active
        if fp is None or not _ok(fp):
            raise ValueError("回复目标无效（战斗位为空或属性不符）")
        targets = [fp]
    elif scope == "choose":
        fp = _field_at(p, int((action or {}).get("target", 0)))
        if fp is None or not _ok(fp):
            raise ValueError("回复目标无效（未指定或属性不符）")
        # 「… that has N HP or less remaining.」（第一百零四批，Bianca's Devotion）
        # 剩余 HP 超出上限 → 该卡本来就不该能打；回 0 而不是照常回血
        # （与 `:need:` 同口径：调用方漏了过滤时**不产生效果**）。
        if maxleft and fp.max_hp - fp.damage > maxleft:
            battle._log("no_effect", {"tag": tag, "reason": "目标剩余 HP 超出上限"})
            return 0, 0
        targets = [fp]
    elif scope == "bench_one":
        cands = [fp for fp in p.bench if _ok(fp)]
        if not cands:
            # 官方规则：效果无合法目标时**只是不执行**，招式照常可用
            #（与 spread_free 在「对手后备为空」时的处理一致）。
            # 不能抛错——否则 Meloetta「回复后备 {P} 一只」在无 {P} 后备时
            # 整张招式不可用（池级执行用例实测捕获）。
            return 0, 0
        want = int((action or {}).get("target", -1))
        if 1 <= want <= len(p.bench) and _ok(p.bench[want - 1]):
            targets = [p.bench[want - 1]]
        else:
            targets = [cands[0]]
    elif scope == "mega":
        # Wally's Compassion：「Heal all damage from 1 of your Mega Evolution
        # Pokémon {ex}」——**只**在己方 Mega 里选 1 只。无 Mega 时不执行
        # （官方：效果无合法目标只是不执行；`energy_to_hand` 的 `:if_prev`
        #  也随之不触发）。
        cands = [fp for fp in field if getattr(fp.pdef, "is_mega", False)
                 and _ok(fp)]
        if not cands:
            return 0, 0
        want = int((action or {}).get("target", -1))
        _sel = _field_at(p, want) if want >= 0 else None
        if _sel is not None and any(_sel is c for c in cands):
            targets = [_sel]
        elif 1 <= want <= len(cands):
            targets = [cands[want - 1]]
        else:
            targets = [cands[0]]
    elif scope == "":
        fp = caster if caster is not None else p.active
        if fp is None:
            raise ValueError("无回复目标")
        targets = [fp]
    else:
        raise ValueError(f"未知回复范围：{scope}")

    healed = 0
    for fp in targets:
        before = fp.damage
        # 条件替换值（第六十二批）：「If that Pokémon is an Arven's Pokémon,
        # heal 100 damage from it **instead**」—— instead 是**换值**，不是加值。
        _amt = amt
        if alt_n and name_key and name_key in re.sub(
                r"[^a-z0-9]", "", str(fp.pdef.name or "").lower()):
            _amt = alt_n
        fp.damage = 0 if full else max(0, fp.damage - _amt)
        got = before - fp.damage
        healed += got
        if got:
            # 「If this Pokémon was healed during this turn」类条件的记账
            fp.healed_turn = battle.turn_number
    # 「If you healed any damage in this way, put all Energy attached to **that**
    # Pokémon into your hand.」（Wally's Compassion）—— 后续动作需要知道
    # 前序回复的**目标**，因此把本次回复的目标记在 battle 上。
    battle._last_heal_targets = list(targets)
    if energy_to_hand and healed > 0:
        # 只有真的回复过才执行（「If you healed any damage in this way」）
        battle._energy_to_hand("energy_to_hand:all")
    if discard_one and healed > 0:
        # Super Potion：「discard an Energy from that Pokémon」——弃到弃牌堆
        from .cards import EnergyDef, EnergyRef
        for fp in targets:
            keys = [k for k in fp.energy if fp.energy[k] > 0]
            if not keys:
                continue
            k = COLORLESS if fp.energy.get(COLORLESS, 0) > 0 else keys[0]
            fp.energy[k] -= 1
            if fp.energy[k] == 0:
                fp.energy.pop(k, None)
            battle._to_discard(p, EnergyRef(EnergyDef(k)))
            break
    return healed, len(targets)


def _attach_energy_from_zones(battle, p: PlayerState, target: FieldPokemon,
                              n: int, etype: Optional[str],
                              zone: str = "any",
                              allow_special: bool = False,
                              avoid: Optional[set] = None) -> int:
    """从指定区域检索 n 张能量附着到 target（招式/特性加速用）

    ``zone``：``hand`` | ``discard`` | ``deck`` | ``any``（= hand→discard→deck）。
    与「手牌附能」不同：效果加速不受 `attached_energy_this_turn` 限制。
    etype 为 None 时接受任意能量；指定类型时只取该色。

    ``avoid``（2026-11-02 第三十九批）：**已用过的属性集合**，命中则跳过，并在
    成功附着后把该色加入。用于卡面「of different types」（Terapagos SSP 161）
    —— 拿到的 N 张必须**两两不同色**。传可变 set 由调用方持有即可跨次累积；
    不传 = 无此约束（**绝不能默认开启**，否则所有普通加速都会被削成「不能同色」）。
    """
    from .cards import EnergyRef
    order = {"hand": ("hand",),
             "discard": ("discard_refs",),
             "deck": ("deck",),
             "any": ("hand", "discard_refs", "deck")}.get(zone,
                                                          ("hand", "discard_refs",
                                                           "deck"))
    got = 0
    for zn in order:
        if got >= n:
            break
        lst = getattr(p, zn)
        for i in range(len(lst) - 1, -1, -1):
            if got >= n:
                break
            c = lst[i]
            if not isinstance(c, EnergyRef):
                continue
            # 这些效果默认只检索/附着**基本**能量；`:anyenergy`（卡面「an Energy
            # card」未限定 Basic）才允许特殊能量整张附着。
            if getattr(c.edef, "is_special", False):
                if not allow_special:
                    continue
                if avoid is not None and getattr(c.edef, "type", None) in avoid:
                    continue
                lst.pop(i)
                target.senergy.append(c)
                got += 1
                if avoid is not None:
                    avoid.add(getattr(c.edef, "type", "colorless"))
                continue
            if etype and getattr(c.edef, "type", None) != etype:
                continue
            if avoid is not None and getattr(c.edef, "type", None) in avoid:
                continue
            lst.pop(i)
            target.add_energy(getattr(c.edef, "type", "colorless"), 1)
            got += 1
            if avoid is not None:
                avoid.add(getattr(c.edef, "type", "colorless"))
            if zn == "discard_refs":
                # 文本层同步移除（保持两层次序一致）
                for k in range(len(p.discard) - 1, -1, -1):
                    if p.discard[k] == c.edef.name:
                        p.discard.pop(k)
                        break
    return got


def _passive_attack_blocked(battle, fp: Optional[FieldPokemon]) -> bool:
    """``passive:attack_block_unless:<cond>`` —— 条件不成立时**整只不能出招**

    卡面原型（第一百一十二批）：
      · Slaking ex「Born to Slack」：「If your opponent has no Pokémon {ex} or
        Pokémon {V} in play, this Pokémon can't attack.」→ `use` 侧的**反面**
        条件，落成 `attack_block_unless:opp_has_ex_or_v`；
      · Team Rocket's Mewtwo ex「Power Saver」：「This Pokémon can't attack
        unless you have 4 or more Team Rocket's Pokémon in play.」→
        `attack_block_unless:own_field_name_at_least:teamrocket:4`。

    ⚠ 不能落成 `use_not:`：那是**单招**条件，而这里是「整只宝可梦不能出招」，
      挂在每一招上会漏掉以后新增的招式（尤其 `passive:extra_attack` 借来的）。
    """
    if fp is None:
        return False
    for ab in (fp.pdef.abilities or ()):
        tag = str(getattr(ab, "tag", "") or "")
        if not tag.startswith("passive:attack_block_unless:"):
            continue
        cond = tag[len("passive:attack_block_unless:"):]
        if not cond:
            raise ValueError(f"attack_block_unless 缺条件：{tag}")
        if not _eval_condition(battle, cond):
            return True
    return False


def _atk_tag_key(name) -> str:
    """招式名 → tag 侧归一化键（只留 a-z0-9，与解析侧 `_tag_name_key` 一致）

    ⚠ 不能复用 `FieldPokemon._attack_key`：那个保留空格（`hyper fang`），
      而 tag 里存的是 `hyperfang` —— 两者不归一，`self_attack_set_next`
      会静默取不到值（伤害停在印刷值，无报错、测试若不看数值也不会红）。
    """
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


def _attack_usable(battle, atk) -> bool:
    """招式静态使用条件（`use_only:` / `use_not:`）是否满足

    未知前缀一律**忽略**（不是条件）；条件不可求值时抛错（`_eval_condition`
    的既有纪律：绝不当成 False，否则卡会静默变成「永远不可用」）。
    """
    for tag in (getattr(atk, "effects", None) or ()):
        t = str(tag)
        if t.startswith("use_only:"):
            if not _eval_condition(battle, t[len("use_only:"):]):
                return False
        elif t.startswith("use_not:"):
            if _eval_condition(battle, t[len("use_not:"):]):
                return False
    return True


def _card_first_turn_ok(card) -> bool:
    """该**训练家卡**是否带「先手第一回合也能用」的例外许可（S6a）

    卡面：「If you go first, you may use this card during your first turn.」
    （Carmine / Team Rocket's Proton —— 都是 Supporter，而先手 T1 禁止打 Supporter）。
    解析侧把它并成复合 tag（`research:5|use_any:first_turn`），这里按**分段**
    匹配：只用前缀判断会漏（那是第一段的事）。
    """
    tdef = getattr(card, "tdef", None)
    return any(str(seg) == "use_any:first_turn"
               for seg in str(getattr(tdef, "tag", "") or "").split("|"))


def _first_turn_attack_ok(atk) -> bool:
    """该招式是否**无视「先手第一回合不能攻击」**（S6a）

    卡面原型（Volbeat「Quick Sign」/ Exeggcute「Precocious Evolution」/
    Delibird「Quick Gift」/ Tapu Koko「Fast Flight」）：
    「**If you go first**, you can use this attack during your first turn.」

    ⚠ 不建模这一句 = 这 4 张卡在开局回合**少一个可用动作**（静默削弱）；
    解析侧若漏产 `use_any:first_turn`，同样安静无声。故解析、引擎、白名单、
    对账四处都要有（对账第 51 类专门盯这一句）。
    """
    return any(str(t) == "use_any:first_turn"
               for t in (getattr(atk, "effects", None) or ()))


def _is_acespec(card) -> bool:
    """是否 ACE SPEC 卡（第一百零七批）

    当前数据集**没有任何 ACE SPEC 卡**（`subtype` 只有 Item / Pokémon Tool /
    Supporter / Stadium），因此本函数恒为 False —— 这是**有前提的等价**：
    `test_batch107_engine` 有一条守卫，一旦数据集出现 ACE SPEC 就立刻失败，
    强制回来补真实判定，而不是让「禁打 ACE SPEC」静默变成「什么都禁不了」。
    """
    tdef = getattr(card, "tdef", None)
    return "acespec" in str(getattr(tdef, "subtype", "") or "").lower().replace(
        " ", "")


def _search_kind_of(tag: str) -> str:
    """search_* tag → 检索类别（pokemon|trainer|any）"""
    if tag.startswith("search_pokemon:"):
        return "pokemon"
    if tag.startswith("search_trainer:"):
        return "trainer"
    return "any"


def _kind_matches(c, kind: str) -> bool:
    from .cards import EnergyRef, PokeRef, TrainerRef
    if kind == "pokemon":
        return isinstance(c, PokeRef)
    if kind == "trainer":
        return isinstance(c, TrainerRef)
    if kind == "energy":
        return isinstance(c, EnergyRef)
    # any：排除能量（与「检索任意卡」的常见卡面语义一致）
    return not isinstance(c, EnergyRef)


def _deck_has_kind(p: PlayerState, kind: str, flt: dict | None = None) -> bool:
    return any(_kind_matches(c, kind)
               and (not flt or kind != "pokemon" or _poke_matches_filter(c, flt))
               for c in p.deck)


_NAME_KEY_RE = re.compile(r"[^a-z0-9]+")


def _inherit_state(old, new) -> None:
    """把 `old` 身上的**全部**状态搬到 `new`（S2e，`replace_self` 用）

    「Any attached cards, damage counters, Special Conditions, turns in play, and
    any other effects remain on the new Pokémon.」—— 卡面把「一切」都说死了，
    所以这里**逐字段**抄（跳过身份字段 `pdef`），而不是列举几个常见字段：
    列举法每加一个新字段（护盾 / 跨回合锁定 / 受击历史…）就会静默漏一次。
    """
    import copy as _copy
    for f in fields(old):
        if f.name == "pdef":
            continue
        val = getattr(old, f.name)
        if isinstance(val, dict):
            val = _copy.deepcopy(val)
        elif isinstance(val, list):
            val = list(val)
        elif isinstance(val, set):
            val = set(val)
        setattr(new, f.name, val)


def _no_evolve_into(pdef) -> bool:
    """该物种是否**禁止**从手牌进化上场（S2e，「Put this Pokémon into play only
    with the effect of …」）

    判据是它自己的特性 tag `passive:no_evolve_into`。放在特性上而不是卡级字段：
    卡池条目对宝可梦只有 abilities / attacks 两个效果位，加字段要动 gen_engine_pool
    与 reallib 两处结构（S2b 的 `shield:no_weakness` 已证明「能塞进 tag 就别加字段」）。
    """
    return any(str(getattr(ab, "tag", "")) == "passive:no_evolve_into"
               for ab in (getattr(pdef, "abilities", ()) or ()))


def _name_key(name) -> str:
    """卡名 → 归一化检索键（小写 + 只留 a-z0-9）

    为什么需要：场地/道具的 tag 是 ``:`` 分隔的紧凑字符串，而卡名里可能有空格、
    撇号、非 ASCII（"Team Rocket's Factory"、"N's Castle"）。把卡名归一化成
    ``teamrocketsfactory`` 之后既能安全放进 tag，也天然实现「忽略大小写/标点」的
    子串匹配（官方卡面说的是 "has "Team Rocket" in its name"）。
    """
    return _NAME_KEY_RE.sub("", str(name or "").lower())


def _card_name_key(c) -> str:
    from .cards import PokeRef, TrainerRef
    if isinstance(c, PokeRef):
        return _name_key(c.pdef.name)
    if isinstance(c, TrainerRef):
        return _name_key(c.tdef.name)
    return ""


def _deck_name_hits(p: PlayerState, key: str) -> bool:
    return bool(key) and any(key in _card_name_key(c) for c in p.deck)


def _take_name_from_deck(p: PlayerState, n: int, key: str) -> int:
    """从牌库检索 ≤n 张「归一化卡名含 key」的卡入手 → 实际入手数"""
    if not key:
        return 0
    got = 0
    for i in range(len(p.deck) - 1, -1, -1):
        if got >= n:
            break
        if key in _card_name_key(p.deck[i]):
            p.hand.append(p.deck.pop(i))
            got += 1
    return got


def _deck_has_basic(p: PlayerState) -> bool:
    from .cards import PokeRef
    return any(isinstance(c, PokeRef) and c.pdef.stage == 0 for c in p.deck)


def _deck_has_evolution(p: PlayerState, fp: FieldPokemon) -> bool:
    """牌库里是否有能**直接进化该宝可梦**的下一阶段（Grand Tree 用）"""
    return any(isinstance(c, PokeRef)
               and c.pdef.stage == fp.pdef.stage + 1
               and c.pdef.evolves_from == fp.pdef.name
               for c in p.deck)


def _take_basic_from_deck(p: PlayerState):
    """从牌库取出 1 张基础宝可梦（调用方负责洗牌与落位）→ PokeRef | None"""
    from .cards import PokeRef
    for i in range(len(p.deck) - 1, -1, -1):
        c = p.deck[i]
        if isinstance(c, PokeRef) and c.pdef.stage == 0:
            return p.deck.pop(i)
    return None


def _typed_in_play(p: PlayerState, ptype) -> int:
    """己方场上（战斗位 + 后备）指定属性的宝可梦数量"""
    if not ptype:
        return 0
    return sum(1 for fp in p.all_field()
               if str(fp.pdef.ptype).lower() == str(ptype).lower())


def _zone_search_available(p: PlayerState, tag: str) -> bool:
    """特性侧全牌库检索 tag 是否可发动（牌库里有对应类别）"""
    if tag.startswith("search_energy:"):
        _se = tag.split(":")
        _et = (_se[3].lower()
               if len(_se) > 3 and _se[2] == "type" else None)
        return _zone_energy_available(p, _et, "deck")
    return _deck_has_kind(p, _search_kind_of(tag), _search_filter(tag))


def _take_kind_from_deck(p: PlayerState, n: int, kind: str,
                         flt: dict | None = None) -> int:
    """从牌库检索 n 张指定类别卡入手 → 实际入手数（洗牌由调用方执行）

    ``flt`` 仅用于宝可梦：``type`` / ``stage`` / ``hp`` 三个可选筛选。
    """
    got = 0
    for i in range(len(p.deck) - 1, -1, -1):
        if got >= n:
            break
        c = p.deck[i]
        if not _kind_matches(c, kind):
            continue
        if flt and kind == "pokemon" and not _poke_matches_filter(c, flt):
            continue
        if flt and flt.get("name_has"):
            if flt["name_has"] not in _card_name_key(c):
                continue
        p.hand.append(p.deck.pop(i))
        got += 1
    return got


def _take_any_from_deck(p: PlayerState, n: int) -> int:
    """从牌库取 n 张**任意**卡入手的确定性默认（无交互层）→ 实际入手数

    「Search your deck for a number of cards … put them into your hand」
    （Gholdengo MEG 99「All-You-Can-Grab」）不限定类别，**连能量卡也算**。

    ⚠ 不能复用 `_take_kind_from_deck(p, n, "any")`：那里的 ``any`` 明确**
    排除能量**（`_kind_matches` 的既有约定）—— 用它会静默少检索。
    洗牌由调用方执行（卡面「Then, shuffle your deck」）。
    """
    got = 0
    for _ in range(max(0, n)):
        if not p.deck:
            break
        p.hand.append(p.deck.pop())
        got += 1
    return got


def _search_filter(tag: str) -> dict:
    """``search_pokemon:N[:type:<c>][:stage:<s>][:hp:<n>][:resistance:<c>]
    [:category:<c>]`` → 筛选字典"""
    parts = tag.split(":")
    flt = {}
    i = 2
    while i + 1 < len(parts):
        if parts[i] in ("type", "stage", "hp", "resistance", "category"):
            flt[parts[i]] = parts[i + 1]
        i += 2
    return flt


def _category_key(cat) -> str:
    """`category` 的比对键：括号里的**阵营名**归一（S5）

    「Trainer's Pokémon(Team Rocket)」→ ``teamrocket``；「N」这类无括号的
    整体归一。⚠ 不能用「子串包含」比对：归一后几乎每个类别都含 ``n``
    （``trainerspokemon…``），任何 ``category:n`` 都会命中全部阵营。
    """
    s = str(cat or "")
    m = re.search(r"\(([^)]*)\)", s)
    key = m.group(1) if m else s
    return _NAME_KEY_RE.sub("", key.lower())


def _poke_matches_filter(c, flt: dict) -> bool:
    from .cards import PokeRef
    if not isinstance(c, PokeRef):
        return False
    pdef = c.pdef
    t = flt.get("type")
    if t and getattr(pdef, "ptype", None) != t:
        return False
    st = flt.get("stage")
    stage = int(getattr(pdef, "stage", 0) or 0)
    if st == "basic" and stage != 0:
        return False
    if st == "stage1" and stage != 1:
        return False
    if st == "stage2" and stage != 2:
        return False
    if st == "evolution" and stage < 1:
        return False
    hp = flt.get("hp")
    if hp and hp.isdigit() and int(getattr(pdef, "hp", 0) or 0) > int(hp):
        return False
    # B2 剩余（2026-10-28）：「Search your deck for up to 2 Pokémon with {F}
    # Resistance」（Fletchling）—— 抗性筛选，按 `pdef.resistance` 精确匹配。
    res = flt.get("resistance")
    if res and str(getattr(pdef, "resistance", "") or "").lower() != res.lower():
        return False
    # S5：阵营限定（「an Evolution Team Rocket's Pokémon」）—— 只比括号里的
    # 阵营名，不碰卡名（卡名前缀不是判据：阵营外的卡也可能带同样的词）。
    cat = flt.get("category")
    if cat and _category_key(getattr(pdef, "category", "")) != \
            _category_key(cat):
        return False
    return True



def _attach_target_fp(tag: str, p: PlayerState,
                      action: Optional[dict], default):
    """``:target:bench|any|bench_type:<c>`` → 附能目标（不可满足返回 None）

    2026-10-28 第十五批：卡面「attach … to 1 of your **Benched** {G} Pokémon」/
    「to 1 of your Pokémon」需要区分落点；省略 target 时仍附给 ``default``
    （沿用历史语义：攻击方 / 特性持有者）。
    """
    parts = str(tag or "").split(":")
    if "target" not in parts:
        return default
    i = parts.index("target")
    kind = parts[i + 1] if i + 1 < len(parts) else ""
    want = int((action or {}).get("target", -1))
    if kind == "bench":
        if not p.bench:
            return None
        return p.bench[want - 1] if 1 <= want <= len(p.bench) else p.bench[0]
    if kind == "any":
        fp = _field_at(p, want) if want >= 0 else None
        return fp if fp is not None else (p.active or (p.bench[0] if p.bench else None))
    if kind == "stage2":
        # 「attach … to 1 of your **Stage 2** Pokémon.」（第一百零九批，
        # Rosa's Encouragement）—— 候选限 stage == 2；没有则该效果不执行。
        cands = [fp for fp in p.all_field() if fp.stage == 2]
        if not cands:
            return None
        fp = _field_at(p, want) if want >= 0 else None
        if fp is not None and fp.stage == 2:
            return fp
        return cands[0]
    if kind == "bench_type" and i + 2 < len(parts):
        color = parts[i + 2].lower()
        cands = [b for b in p.bench if str(b.pdef.ptype).lower() == color]
        if not cands:
            return None
        return cands[want - 1] if 1 <= want <= len(cands) else cands[0]
    return None


def _attach_heal_of(tag: str) -> int:
    """``:heal:<n>`` = 附能成功后**回复该目标** n 点（第一百零四批，Hydrapple ex）

    卡面：「attach a Basic {G} Energy card from your hand to 1 of your Pokémon.
    **If you attached Energy to a Pokémon in this way, heal 30 damage from that
    Pokémon.**」——「那个目标」是关键：不能回成持有者自己。
    """
    parts = str(tag or "").split(":")
    if "heal" in parts:
        i = parts.index("heal")
        if i + 1 < len(parts) and parts[i + 1].isdigit():
            return int(parts[i + 1])
    return 0


def _attach_counters_of(tag: str) -> int:
    """``:counters:<dmg>`` = 附能成功后在该目标身上放置指示物（Toxtricity）"""
    parts = str(tag or "").split(":")
    if "counters" in parts:
        i = parts.index("counters")
        if i + 1 < len(parts) and parts[i + 1].isdigit():
            return int(parts[i + 1])
    return 0


def _parse_recon_tag(tag: str) -> dict:
    """解析「检视牌库顶」tag → dict（2026-10-30 第二十批）

    形态::

        recon:<N>                                  旧口径（随机取 1 张入手，其余放牌库底）
        recon:<N>:<K>:<sel>[:dest:<d>][:rest:bottom][:gate:<g>]

    ``<sel>``：``any`` / ``supporter`` / ``item`` / ``stadium`` / ``trainer`` /
    ``pokemon`` / ``pokemon-<type>`` / ``energy`` / ``basic_energy`` /
    ``energy-<type>`` / ``basic_energy-<type>``，并集用 ``+``，
    ``@each`` 后缀表示「每种各 1 张」（Drayton「a Pokémon and a Trainer card」）。
    ``<d>``：``hand``（默认）/ ``bench`` / ``attach``。
    ``<g>``：``ko_last_turn`` / ``not_first_turn``。

    ``len(parts) == 2`` 即旧口径（``legacy``），引擎保持原行为不变 —— 池内已有
    多张卡依赖它，改语义会静默改变既有对局。
    """
    parts = tag.split(":")
    if len(parts) < 2 or not parts[1].isdigit():
        raise ValueError(f"recon tag 缺少张数：{tag}")
    out = {"look_top": int(parts[1]), "max": 1, "sel": "any",
           "dest": "hand", "rest": "shuffle", "gate": None,
           "legacy": len(parts) == 2}
    if not out["legacy"]:
        # `all` = 「any number of … you find there」（2026-11-02 第三十五批）：
        # 取牌库顶 N 张里**所有**符合筛选的牌。它不是「任意 K 张」的开放式选择 ——
        # 备选集合被 N 张上限封死，所以不需要交互层，用 `max` 取满即可。
        if parts[2] == "all":
            out["max"] = 999
        elif not parts[2].isdigit():
            raise ValueError(f"recon tag 缺少筛选张数：{tag}")
        else:
            out["max"] = int(parts[2])
    i = 3
    while i < len(parts):
        if parts[i] in ("dest", "rest", "gate") and i + 1 < len(parts):
            out[parts[i]] = parts[i + 1]
            i += 2
            continue
        if parts[i]:
            out["sel"] = parts[i]
        i += 1
    return out


def _recon_one(c, alt: str) -> bool:
    """单个筛选项是否命中卡牌引用；未知筛选项**抛错**（不猜、不静默放行）。"""
    kind, _, arg = alt.partition("-")
    arg = arg.lower()
    if kind == "pokemon":
        if not isinstance(c, PokeRef):
            return False
        return not arg or str(c.pdef.ptype).lower() == arg
    if kind == "trainer":
        return isinstance(c, TrainerRef)
    if kind in ("supporter", "item", "stadium", "tool"):
        return (isinstance(c, TrainerRef)
                and str(c.tdef.kind).lower() == kind)
    if kind in ("energy", "basic_energy"):
        if not isinstance(c, EnergyRef):
            return False
        if kind == "basic_energy" and c.edef.is_special:
            return False
        return not arg or str(c.edef.type).lower() == arg
    raise ValueError(f"未知 recon 筛选项：{alt}")


def _recon_matches(c, sel: str) -> bool:
    """`+` 并集匹配（`@each` 只影响选择策略，不影响是否命中）。"""
    base = str(sel or "any").split("@")[0]
    if base in ("", "any"):
        return True
    return any(_recon_one(c, alt) for alt in base.split("+") if alt)


def _parse_opp_hand_tag(tag: str) -> tuple:
    """解析 ``opp_hand:<op>:<sel>[:<n>|:all]`` → ``(op, sel, n)``

    ``op`` ∈ ``discard``（弃进对手弃牌堆）| ``bottom``（放到对手牌库底）；
    ``sel`` 为 ``+`` 连接的筛选式并集；``n`` 为 ``int`` 或 ``"all"``（缺省 1）。

    ⚠ 与 ``discard_opp_random:N`` / ``discard_opp_to:N`` / ``shuffle_opp_hand:N``
    是**相反口径**：那三个是「对手自选 ⇒ 引擎按随机」，本 tag 是「我方选
    ⇒ 确定性默认 + 可选 ``action['hand_pick']``」。混用会造成「我方明明能挑
    最有利的那张，却被随机弃」——属于方向性错误，不是近似。
    """
    parts = str(tag).split(":")
    if len(parts) < 3:
        raise ValueError(f"opp_hand tag 字段不足：{tag}")
    op, sel = parts[1], parts[2]
    if op not in ("discard", "bottom"):
        raise ValueError(f"未知 opp_hand 操作：{op}")
    if not sel:
        raise ValueError(f"opp_hand 缺少筛选项：{tag}")
    n: object = 1
    if len(parts) > 3:
        raw = parts[3]
        if raw == "all":
            n = "all"
        else:
            if not raw.isdigit() or int(raw) < 1:
                raise ValueError(f"opp_hand 张数非法：{raw}")
            n = int(raw)
    return op, sel, n


def _opp_hand_one(ref, alt: str) -> bool:
    """单条筛选项匹配（``sel`` 已按 ``+`` 切开）"""
    from .cards import EnergyRef, PokeRef, TrainerRef
    alt = str(alt or "").strip()
    if alt in ("", "any"):
        return True
    if alt in ("item", "tool", "stadium", "supporter"):
        return isinstance(ref, TrainerRef) and str(ref.tdef.kind) == alt
    if alt == "trainer":
        return isinstance(ref, TrainerRef)
    if alt == "energy":
        return isinstance(ref, EnergyRef)
    if alt == "basic_energy":
        return isinstance(ref, EnergyRef) and not ref.edef.is_special
    if alt == "special_energy":
        return isinstance(ref, EnergyRef) and ref.edef.is_special
    if alt == "pokemon":
        return isinstance(ref, PokeRef)
    if alt == "basic_pokemon":
        return isinstance(ref, PokeRef) and ref.pdef.stage == 0
    if alt == "evolution":
        return isinstance(ref, PokeRef) and ref.pdef.stage > 0
    # 未知筛选词必须抛错：静默按「任意牌」处理 = 效果凭空变宽（拿错牌）。
    raise ValueError(f"未知 opp_hand 筛选项：{alt}")


def _opp_hand_matches(ref, sel: str) -> bool:
    base = str(sel or "any").split("@")[0]
    return any(_opp_hand_one(ref, alt) for alt in base.split("+") if alt)


def _parse_ability_cost(pc: str):
    """解析 ``AbilityDef.cost`` → ``(op, n, arg)`` | None

    卡面原型（2026-11-04 第四十九批）：「**You must** <代价> **in order to use
    this Ability.**」——代价必须真的付掉，当 gate 实现就是**免费发动**。

    形态：
      · ``discard:<n>``               弃 n 张手牌（Porygon-Z「Reconstitute」）
      · ``discard_named:<n>:<key>``   弃 n 张**具名**手牌（Meowstic：Chill Teaser Toy）
      · ``discard_self_energy:<n>:<c>`` 弃**自身身上** n 个**基本**能量（Kilowattrel）
      · ``discard_hand_energy:<n>:<c>`` 弃**手牌** n 张**基本**能量（Volcarona）
      · ``hand_bottom:<n>``           手牌 n 张放牌库底（Quaquaval）

    ⚠ 两个能量形态**不可互换**：`self_energy` 动的是场上能量、`hand_energy`
    动的是手牌。卡面写「from this Pokémon」却按手牌付账 = 静默少付/多付。
    ⚠ 特殊能量**不是**合法代价（卡面写 Basic）。

    未知形态**抛错**（不静默当作无代价）。
    """
    s = str(pc or "").strip()
    if not s:
        return None
    parts = s.split(":")
    op = parts[0]
    if op in ("discard", "hand_bottom") and len(parts) >= 2 and parts[1].isdigit():
        n = int(parts[1])
        if n < 1:
            raise ValueError(f"特性代价张数非法：{pc}")
        return (op, n, None)
    if op in ("discard_self_energy", "discard_hand_energy") \
            and len(parts) >= 3 and parts[1].isdigit():
        n = int(parts[1])
        et = str(parts[2] or "")
        if n < 1 or not et:
            raise ValueError(f"特性代价形态非法：{pc}")
        return (op, n, et)
    if op == "discard_named" and len(parts) >= 3 and parts[1].isdigit():
        n = int(parts[1])
        key = ":".join(parts[2:])
        if n < 1 or not key:
            raise ValueError(f"特性代价形态非法：{pc}")
        return (op, n, key)
    raise ValueError(f"未知特性代价形态：{pc}")


def _named_hand_cards(p, key: str) -> list:
    """手牌里卡名（归一化）**包含** key 的下标（`_name_key` 与解析侧同规则）"""
    out = []
    for i, c in enumerate(p.hand):
        label = ""
        if isinstance(c, TrainerRef):
            label = str(getattr(c.tdef, "name", "") or "")
        elif isinstance(c, PokeRef):
            label = str(getattr(c.pdef, "name", "") or "")
        elif isinstance(c, EnergyRef):
            label = str(getattr(c.edef, "name", "") or "")
        if key and key in _name_key(label):
            out.append(i)
    return out


def _ability_cost_ok(p, fp, pc: str) -> bool:
    """特性代价付得出吗（付不出 → 该特性**不进合法动作表**）"""
    spec = _parse_ability_cost(pc)
    if spec is None:
        return True
    op, n, arg = spec
    if op == "discard":
        return len(p.hand) >= n
    if op == "hand_bottom":
        return len(p.hand) >= n
    if op == "discard_named":
        return len(_named_hand_cards(p, str(arg))) >= n
    if op == "discard_hand_energy":
        return sum(1 for c in p.hand
                   if isinstance(c, EnergyRef)
                   and not getattr(c.edef, "is_special", False)
                   and str(c.edef.type) == arg) >= n
    if op == "discard_self_energy":
        # ⚠ 只数 `fp.energy`（**基本**能量）。用 `energy_of` / `total_energy`
        # 会把特殊能量算进来 —— 卡面写的是 Basic，特殊能量不能当代价。
        return int(fp.energy.get(str(arg), 0)) >= n
    raise ValueError(f"未知特性代价形态：{pc}")


def _parse_play_cost(pc: str):
    """解析 ``TrainerDef.play_cost`` → ``(op, n, etype)`` | None

    形态：
      · ``"discard:<n>"`` —— 卡面「You can use this card only if you discard N
        **other cards** from your hand.」—— ``other`` 意味着**本卡自己不能当代价**
        （见 ``_play_cost_ok`` 的 ``self_in_hand``）。
      · ``"discard_energy:<n>:<etype>"`` —— 卡面「You can use this card only if you
        discard a **Basic {R} Energy** card from your hand.」（Blowtorch PFL 86，
        2026-11-02 第三十八批）。代价**限定属性**且必须是**基本**能量 ——
        落成 ``discard:<n>`` 会允许弃掉任意一张手牌（静默放宽代价）。

    未知形态**抛错**，不静默当作「无代价」—— 那会让一张要付账的卡变成免费
    （静默加强，正是本机制要防的那类错误）。
    """
    s = str(pc or "").strip()
    if not s:
        return None
    parts = s.split(":")
    if parts[0] == "discard" and len(parts) >= 2 and parts[1].isdigit():
        n = int(parts[1])
        if n < 1:
            raise ValueError(f"打出代价张数非法：{pc}")
        return ("discard", n, None)
    if parts[0] == "discard_energy" and len(parts) >= 3 and parts[1].isdigit():
        n = int(parts[1])
        et = str(parts[2] or "")
        if n < 1 or not et:
            raise ValueError(f"打出代价形态非法：{pc}")
        return ("discard_energy", n, et)
    raise ValueError(f"未知打出代价形态：{pc}")


def _play_cost_ok(p, pc: str, self_in_hand: bool = True) -> bool:
    """代价付得出吗（``self_in_hand``：本卡此刻是否占着一张手牌）"""
    spec = _parse_play_cost(pc)
    if spec is None:
        return True
    op, n, et = spec
    if op == "discard_energy":
        # 只数**基本**、且属性相符的能量卡：卡面写的是 Basic {X} Energy。
        # `self_in_hand` 在这里无意义 —— 本卡是训练家，不可能是能量卡。
        return sum(1 for c in p.hand
                   if isinstance(c, EnergyRef)
                   and not getattr(c.edef, "is_special", False)
                   and str(c.edef.type) == et) >= n
    avail = len(p.hand) - (1 if self_in_hand else 0)
    return avail >= n


def _parse_attach_spread_tag(tag: str) -> tuple:
    """解析 ``attach_spread:<n>:<etype>:<zone>[:target:<scope>]`` → 四元组

    「Search your deck for up to N Basic Energy cards and attach them to your
    Pokémon **in any way you like**」——与 ``attach`` 的区别是**多张分给多只**：
    ``attach`` 只能附给单一目标，本族卡面允许自由分配。

    ``etype``：``any`` 或小写属性名（``grass`` / ``fire`` …）；
    ``zone``：``hand`` / ``discard`` / ``deck`` / ``any``；
    ``scope``：``any``（任意己方）| ``bench``（后备）| ``type-<属性>``
    （按 ``pdef.ptype`` 判定，如 Magneton 的「your {L} Pokémon」）|
    ``cat-<子串>``（按 ``pdef.category`` **包含**判定，如 ``cat-future``）；
    ``selfko``：末尾 `:selfko` → 结算后持有者自身被击倒（Magneton SSP 59）。

    ⚠ 调用方必须把 ``attach_spread:`` 分支排在既有的 ``attach`` /
    ``attach_energy:`` 分支**之前** —— ``tag.startswith("attach")`` 会把它一并
    吞掉，随后 ``_parse_attach_tag`` 会把 ``spread`` 当成能量属性名（静默失效）。
    """
    parts = str(tag).split(":")
    # `:selfko` 是**尾段**，必须先剥掉再解析 scope —— 否则 `target:` 后面
    # 那一格会被读成 `type-lightning:selfko`（属性名认不出 → 抛错）。
    selfko = bool(parts) and parts[-1] == "selfko"
    if selfko:
        parts = parts[:-1]
    if len(parts) < 4:
        raise ValueError(f"attach_spread tag 字段不足：{tag}")
    raw_n, etype, zone = parts[1], parts[2], parts[3]
    if not raw_n.isdigit() or int(raw_n) < 1:
        raise ValueError(f"attach_spread 张数非法：{raw_n}")
    if zone not in ("hand", "discard", "deck", "any"):
        raise ValueError(f"attach_spread 区域非法：{zone}")
    scope = "any"
    if "target" in parts:
        i = parts.index("target")
        scope = parts[i + 1] if i + 1 < len(parts) else ""
    if not scope:
        raise ValueError(f"attach_spread 缺少目标限定：{tag}")
    return (int(raw_n), (None if etype in ("any", "") else etype), zone,
            scope, selfko)


def _parse_attach_spread_flip_tag(tag: str) -> tuple:
    """解析 ``attach_spread_flip:<coins>:<per>:<etype>:<zone>[:target:<scope>]``
    → ``(coins, per, etype, zone, scope)``

    卡面原型（Mega Audino ex「Kaleidowaltz」）：
    「Flip 3 coins. For each heads, search your deck for up to 2 Basic Energy cards
     and attach them to your Pokémon in any way you like. Then, shuffle your deck.」

    ``coins``/``per`` 是**常量**，实际张数 ``heads * per`` 在结算时现算 ——
    解析期折算成任何固定值都是静默改变强度（期望 3 张，上限 6 张）。
    """
    parts = str(tag).split(":")
    if len(parts) < 5:
        raise ValueError(f"attach_spread_flip tag 字段不足：{tag}")
    raw_c, raw_p, etype, zone = parts[1], parts[2], parts[3], parts[4]
    if not raw_c.isdigit() or int(raw_c) < 1:
        raise ValueError(f"attach_spread_flip 抛币数非法：{raw_c}")
    if not raw_p.isdigit() or int(raw_p) < 1:
        raise ValueError(f"attach_spread_flip 每正面张数非法：{raw_p}")
    if zone not in ("hand", "discard", "deck", "any"):
        raise ValueError(f"attach_spread_flip 区域非法：{zone}")
    scope = "any"
    if "target" in parts:
        i = parts.index("target")
        scope = parts[i + 1] if i + 1 < len(parts) else ""
    if not scope:
        raise ValueError(f"attach_spread_flip 缺少目标限定：{tag}")
    return (int(raw_c), int(raw_p),
            (None if etype in ("any", "") else etype), zone, scope)


def _parse_attach_each_bench_tag(tag: str) -> tuple:
    """解析 ``attach_each_bench:<per>:<etype>:<zone>`` → ``(per, etype, zone)``

    卡面原型（Mega Gardevoir ex「Overflowing Wishes」）：
    「For each of your Benched Pokémon, search your deck for a Basic {P} Energy
     card and attach it to that Pokémon. Then, shuffle your deck.」

    与 ``attach_spread`` 的区别是**没有 scope 段**：目标恒为「每只后备各一份」，
    不存在「任意分配」的可选空间（见 `_do_attach_each_bench`）。
    """
    parts = str(tag).split(":")
    if len(parts) != 4:
        raise ValueError(f"attach_each_bench tag 字段数不对：{tag}")
    raw_per, etype, zone = parts[1], parts[2], parts[3]
    if not raw_per.isdigit() or int(raw_per) < 1:
        raise ValueError(f"attach_each_bench 张数非法：{raw_per}")
    if zone not in ("hand", "discard", "deck", "any"):
        raise ValueError(f"attach_each_bench 区域非法：{zone}")
    return (int(raw_per), (None if etype in ("any", "") else etype), zone)


def _spread_targets(p: PlayerState, scope: str) -> list:
    """「in any way you like」的目标集合，顺序固定为 **active → bench**

    顺序必须确定：默认分配按 round-robin 依次落位，顺序一变同一局面就会
    出现两种结果（不可复现）。
    """
    all_fp = ([p.active] if p.active is not None else []) + list(p.bench)
    if scope == "any":
        return all_fp
    if scope == "bench":
        return list(p.bench)
    if "+" in scope:
        # 并集（Steven's Metagross ex 的「your {P} Pokémon **and** {M} Pokémon」）：
        # 两类都能当目标，不是「各附一份」。顺序 = 各段内 active→bench 拼接后去重，
        # 必须确定（默认分配按 round-robin，顺序一变同局面就有两种结果）。
        out: list = []
        seen: set = set()
        for one in scope.split("+"):
            for fp in _spread_targets(p, one):
                if id(fp) not in seen:
                    seen.add(id(fp))
                    out.append(fp)
        return out
    if scope.startswith("bench:"):
        # 「your **Benched** {C} Pokémon」（Glass Trumpet SFA 62，第五十九批）=
        # 后备 ∩ 属性，**两个限定同时成立**。
        # ⚠ 与裸 `type-<c>` 的区别：那只按属性筛，**含战斗位** —— 用它顶替就会
        # 把能量附到战斗位（卡面只许后备），静默加强且没有任何报错。
        sub = scope[len("bench:"):]
        want = {id(x) for x in _spread_targets(p, sub)}
        return [fp for fp in p.bench if id(fp) in want]
    if scope.startswith("type-"):
        # `type-lightning` → 「your {L} Pokémon」（Magneton SSP 59）。
        # 按 `pdef.ptype` 判定；属性名认不出时**抛错**（空集合会被当成
        # 「没有合法目标 ⇒ 效果不执行」，那就是静默不生效）。
        key = scope[5:].lower()
        if not key:
            raise ValueError("attach_spread 的 type- 限定为空")
        if key not in TYPES:
            raise ValueError(f"未识别的宝可梦属性：{scope}")
        return [fp for fp in all_fp
                if str(fp.pdef.ptype or "").lower() == key]
    if scope.startswith("cat-"):
        # `cat-team-rocket` → 子串 "team rocket"（category 原文含空格）
        key = scope[4:].replace("-", " ").lower()
        if not key:
            raise ValueError("attach_spread 的 cat- 限定为空")
        return [fp for fp in all_fp
                if key in str(getattr(fp.pdef, "category", "") or "").lower()]
    raise ValueError(f"未知 attach_spread 目标限定：{scope}")


def _parse_attach_tag(tag: str) -> tuple:
    """解析附能 tag → (n, etype|None, zone|None)

    统一形如 ``attach_energy:<n>[:<type>][:<zone>]`` / ``attach:<n>[:<type>]`` /
    ``evolve_attach:<n>[:<type>][:<zone>]``（zone ∈ hand|discard|deck|any）。
    非法/缺省部分回退默认（n=1, type=None, zone=None）。
    """
    parts = [x for x in tag.split(":")]
    n = 1
    if len(parts) > 1 and parts[1].isdigit():
        n = int(parts[1])
    known_zones = ("hand", "discard", "deck", "any")
    etype = None
    zone = None
    for x in parts[2:]:
        if x == "target":
            break                       # `:target:bench` 是**去向**，不是能量属性
        if x in known_zones:
            zone = x
        elif x and etype is None:
            etype = x
    return n, etype, zone


def _take_energy_from_deck(p: PlayerState, n: int,
                           etype: Optional[str],
                           distinct: bool = False,
                           basic_only: bool = True) -> int:
    """从牌库检索 n 张（指定色的）能量入手 → 实际入手数

    洗牌由调用方用 ``battle.rng`` 执行（保持全局可复现性，勿在 helper 内自造 RNG）。

    ``distinct``（2026-11-02 第三十九批）：卡面「Basic Energy cards **of different
    types**」（Eevee SFA 50 / Energy Search Pro SSP 176 / Terapagos SSP 161）——
    拿到的每张**属性必须两两不同**。不加这个约束会允许拿 3 张同色能量
    （静默加强，实测这两张已进池的卡一直如此）。默认 **False**：多数检索没有
    这个限制，默认开启等于凭空虚削。

    ``basic_only``（2026-11-02 第四十批）：本 helper 只服务于卡面写
    「**Basic** Energy cards」的检索（全库 9 张 `search_energy:` 逐张核对，无例外）。
    特殊能量在牌库里同样是 `EnergyRef`（`edef.tag = "senergy:…"`），不加这道过滤
    会把它们一并拿到手 —— 那是**静默超范围**（Basic 检索拿了 Special 能量），
    实测 Energy Search Pro 会因此拿到「任意张」特殊能量。默认 **True**。
    """
    from .cards import EnergyRef
    got = 0
    used: set = set()
    for i in range(len(p.deck) - 1, -1, -1):
        if got >= n:
            break
        c = p.deck[i]
        if not isinstance(c, EnergyRef):
            continue
        if basic_only and getattr(c.edef, "tag", ""):
            continue                     # 特殊能量（tag = senergy:…）不算 Basic
        if etype and getattr(c.edef, "type", None) != etype:
            continue
        if distinct and getattr(c.edef, "type", None) in used:
            continue
        used.add(getattr(c.edef, "type", None))
        p.hand.append(p.deck.pop(i))
        got += 1
    return got


# S5（2026-11-08）：**抛币门**的借用招式（Ethan's Sudowoodo「Try to Imitate」
# 「Flip a coin. If heads, choose 1 of your opponent's Active Pokémon's attacks
#  and use it as this attack.」）。币必须在**解析借用来源之前**抛，且
# `_attack_mods` 不得为同一条 tag 再抛一次（否则同一次攻击里有两枚互不相干的币）。
_BORROW_COIN_GATE_PREFIX = "coin:heads:borrow_attack:"


def _borrow_scope(atk) -> Optional[str]:
    """招式的借用来源（`borrow_attack:<scope>`）；普通招式返回 None

    也认抛币门形态 `coin:heads:borrow_attack:<scope>`（scope 相同）。
    """
    for t in getattr(atk, "effects", ()) or ():
        t = str(t)
        if t.startswith("borrow_attack:"):
            return t.split(":", 1)[1]
        if t.startswith(_BORROW_COIN_GATE_PREFIX):
            return t[len(_BORROW_COIN_GATE_PREFIX):]
    return None


def _borrow_coin_gate(atk) -> Optional[str]:
    """抛币门的 tag 形态（`coin:heads:borrow_attack:<scope>`）；无门返回 None"""
    for t in getattr(atk, "effects", ()) or ():
        t = str(t)
        if t.startswith(_BORROW_COIN_GATE_PREFIX):
            return t
    return None


def _borrow_scope_kind(scope: Optional[str]) -> str:
    """scope 的**形态**（去掉参数）：`opp_deck_top:10` → `opp_deck_top`"""
    return str(scope or "").split(":")[0]


# 「**You may** choose an attack … and use it as this attack.」—— 可以放弃。
# 只有牌库顶形态这么印（TR Persian ex）；其余借用（对手战斗位 / 进化前形态）
# 都是「choose 1 of …」的强制语义。判据按**卡面措辞**区分，不能按引擎实现猜。
_OPTIONAL_BORROW_KINDS = ("opp_deck_top",)


def _borrow_optional(scope: Optional[str]) -> bool:
    return _borrow_scope_kind(scope) in _OPTIONAL_BORROW_KINDS


def _borrow_deck_top_n(scope: Optional[str]) -> Optional[int]:
    """`opp_deck_top:<n>` → n；其它形态返回 None"""
    if _borrow_scope_kind(scope) != "opp_deck_top":
        return None
    parts = str(scope).split(":")
    if len(parts) < 2 or not parts[1].isdigit():
        raise ValueError(f"opp_deck_top 借用缺少张数：{scope}")
    return int(parts[1])


def _plain_attack_action(ai: int, atk) -> dict:
    """「按本招式的印刷效果结算」的裸动作（不借用）

    抽成函数是为了让「可放弃的借用」（S5 · you may）能给同一个动作形态：
    两处各抄一份后一旦字段漂移（例如以后加 `borrow_optional` 标记），
    AI 选到的动作与人类点出来的动作就会不同。
    """
    return {"verb": "attack", "attack": ai,
            "name": atk.name, "name_zh": atk.name_zh,
            "display": atk.name_zh or atk.name,
            "damage": atk.damage, "cost_map": list(atk.cost),
            "desc_zh": atk.desc_zh, "display_desc": atk.display_desc}


def _prev_form_attacks(pdef) -> list:
    """沿 `evolves_from` 链回溯，收集**进化前所有形态**的招式（按近→远）"""
    from .cards import pokemon_def
    out: list = []
    seen = set()
    cur = getattr(pdef, "evolves_from", None)
    while cur and cur not in seen:
        seen.add(cur)
        d = pokemon_def(cur)
        if d is None:
            break
        out.extend(d.attacks)
        cur = getattr(d, "evolves_from", None)
    return out


def _hand_energy_kinds(p: PlayerState) -> list:
    """手牌中的能量**种类**（同种去重）→ [{index, edef}]

    基本能量按属性去重（3 张火能量只需 1 个选项）；特殊能量按 tag 去重。
    只用于「合法动作表」：手牌有多种能量时必须让玩家/AI 能选，否则引擎会
    替玩家决定附哪一张（特殊能量与基本能量语义完全不同，属于静默错算）。
    """
    out: list = []
    seen = set()
    for i, c in enumerate(p.hand):
        if not isinstance(c, EnergyRef):
            continue
        key = str(getattr(c.edef, "tag", "") or c.edef.type)
        if key in seen:
            continue
        seen.add(key)
        out.append({"index": i, "edef": c.edef})
    return out


def _zone_energy_available(p: PlayerState, etype: Optional[str],
                          zone: str = "any",
                          allow_special: bool = False) -> bool:
    """手牌 / 弃牌堆 / 牌库中是否存在（指定色的）能量牌

    ``zone`` 指定时只查该区域（hand|discard|deck|any）。
    ``allow_special`` 对应卡面「an Energy card」（未限定 Basic，Shaymin）。
    """
    from .cards import EnergyRef
    zones = {"hand": ("hand",), "discard": ("discard_refs",),
             "deck": ("deck",),
             "any": ("hand", "discard_refs", "deck")}.get(
                 zone, ("hand", "discard_refs", "deck"))
    for zn in zones:
        for c in getattr(p, zn):
            if not isinstance(c, EnergyRef):
                continue
            # 「检索/附着**基本能量**」类效果：特殊能量不算（卡面明文 Basic）；
            # 卡面写「an Energy card」时（allow_special）特殊能量也算。
            if getattr(c.edef, "is_special", False) and not allow_special:
                continue
            if etype and getattr(c.edef, "type", None) != etype:
                continue
            return True
    return False


def _recover_candidates(p: PlayerState, tag: str) -> list:
    """弃牌堆回收候选（recover:N[:kind]）→ 弃牌堆下标列表（最多 N 个）

    kind：``pokemon`` | ``energy`` | ``trainer`` | ``supporter`` | ``item`` |
    ``any``（默认 any，对应 Night Stretcher 的「宝可梦或基本能量」）。
    仅返回**类型化层**中存在的卡（文本层不参与判定）。

    ``trainer``/``supporter``/``item`` 为 2026-09-22 新增：真实卡面有
    「Put a **Trainer** card from your discard pile into your hand.」（Dedenne）、
    「Put up to 2 **Supporter** cards …」（Miracle Headset）两类写法。
    此前只能登记为未建模 —— 而引擎本来就有 TrainerRef，补三个分支即可精确表达。
    """
    from .cards import EnergyRef, PokeRef, TrainerRef
    parts = tag.split(":")
    n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
    kind = parts[2] if len(parts) > 2 and parts[2] else "any"
    # ---- B3（2026-10-28）：**子集组合**回收 ----
    # 卡面「Put up to 3 in any combination of Pokémon that don't have a Rule Box
    # and Basic Energy cards from your discard pile into your hand.」（Lana's Aid）
    # /「… of {F} Pokémon and Basic {F} Energy cards …」（Tarragon）
    # 既不是 `pokemon` 也不是 `energy`，也不是 `any`（会取到带 Rule Box 的 ex）。
    # 语法：``recover:N:combo:<a>+<b>[:type:<c>]``；`type` 同时限定宝可梦属性与能量属性。
    combo: Optional[list] = None
    ptype: Optional[str] = None
    if len(parts) > 3 and parts[2] == "combo":
        combo = [x for x in parts[3].split("+") if x]
        for i, x in enumerate(parts):
            if x == "type" and i + 1 < len(parts):
                ptype = parts[i + 1].lower()

    def _match(c) -> bool:
        if isinstance(c, PokeRef):
            if combo is not None:
                if ptype and str(c.pdef.ptype).lower() != ptype:
                    return False
                if "pokemon" in combo:
                    return True
                if "no_rule_pokemon" in combo:
                    return not c.pdef.is_ex
                return False
            return kind in ("any", "pokemon")
        if isinstance(c, EnergyRef):
            if combo is not None:
                if "basic_energy" not in combo:
                    return False
                return not ptype or str(c.edef.type).lower() == ptype
            return kind in ("any", "energy")
        if isinstance(c, TrainerRef):
            # ⚠ `kind` 在 `TrainerRef.tdef` 上（TrainerRef 本身没有该字段）——
            #   直接 `getattr(c, "kind", "")` 会永远拿到空串，使 supporter/item
            #   过滤静默失效（全当"不是该类别"）。用 tdef.kind。
            sub = str(getattr(getattr(c, "tdef", None), "kind", "") or "")
            if combo is not None:
                return False
            if kind == "trainer":
                return True
            return kind in ("supporter", "item") and sub == kind
        return False

    out = []
    for i, c in enumerate(p.discard_refs):
        if len(out) >= n:
            break
        if _match(c):
            out.append(i)
    return out


def _energy_to_deck(battle, p: PlayerState, color: str, raw: str) -> int:
    """弃牌堆的**基本能量**洗回牌库（``energy_to_deck:<color|any>:<n|all>``）

    卡面原型：「… for each Basic {W} Energy card in your discard pile. Then, shuffle
    those cards into your deck.」（Kyogre「Riptide」/ Sinistcha ex「Re-Brew」）

    · 只取 `EnergyRef` 且**非特殊能量**（卡面写的是 Basic）；
    · ``color`` 限定属性，``any`` = 任意基本能量；
    · 洗回后**立刻洗牌**（与检索后洗牌同一约定，`self.rng` 驱动、可复现）。
    """
    n = 999 if raw == "all" else int(raw)
    picked: list = []
    for i in range(len(p.discard_refs) - 1, -1, -1):
        c = p.discard_refs[i]
        if not isinstance(c, EnergyRef) or getattr(c.edef, "is_special", False):
            continue
        if color != "any" and str(c.edef.type) != color:
            continue
        picked.append(i)
        if len(picked) >= n:
            break
    moved = 0
    for i in picked:
        ref = battle._rebuild_ref(p.discard_refs.pop(i))
        label = _card_label(ref)
        # 文本层同步（discard 是名字列表）
        for k in range(len(p.discard) - 1, -1, -1):
            if p.discard[k] == label:
                p.discard.pop(k)
                break
        p.deck.append(ref)
        moved += 1
    if moved:
        battle.rng.shuffle(p.deck)
    return moved


def _pokemon_to_deck(battle, p: PlayerState, raw: str) -> int:
    """弃牌堆里的**宝可梦**洗回牌库（``pokemon_to_deck:<n|all>``，第八十二批）

    卡面（Sacred Ash）：「Shuffle up to 5 Pokémon from your discard pile into
    your deck.」——与 `energy_to_deck` 是**两种来源**（宝可梦 vs 基本能量），
    不可互代。``up to N`` 由引擎确定性取至多 N 张（choose 类默认口径，
    从弃牌堆尾部倒序），不会凭空变卡、也不会把能量一起洗回去。

    洗回后**立刻洗牌**（与检索后洗牌同一约定）；没移动任何卡就不洗
    —— 空集合的「顺手洗牌」也是静默改状态。
    """
    n = 999 if raw == "all" else int(raw)
    picked: list = []
    for i in range(len(p.discard_refs) - 1, -1, -1):
        if not isinstance(p.discard_refs[i], PokeRef):
            continue
        picked.append(i)
        if len(picked) >= n:
            break
    moved = 0
    for i in picked:
        ref = battle._rebuild_ref(p.discard_refs.pop(i))
        label = _card_label(ref)
        for k in range(len(p.discard) - 1, -1, -1):
            if p.discard[k] == label:
                p.discard.pop(k)
                break
        p.deck.append(ref)
        moved += 1
    if moved:
        battle.rng.shuffle(p.deck)
    return moved


def _search_discard(battle, p: PlayerState, raw: str) -> int:
    """检索至多 N 张 → **弃置**（``search_discard:<n|all>``，第八十三批）

    卡面（Brilliant Blender）：「Search your deck for up to 5 cards and
    discard them. Then, shuffle your deck.」——去向是弃牌堆，**不是手牌**：
    拿进手牌会凭空多 N 张（静默加强），落成 `search_top` 则等于没弃。

    选哪 N 张：引擎无交互层，确定性取**当前牌库顶 N 张**（与 `recon` /
    `search_top` 同口径）；`up to N` 取卡面允许的最大合法值。取到卡才洗牌
    —— 空集合的「顺手洗牌」也是静默改状态。
    """
    parts = str(raw).split(":")
    n = 999 if parts[0] == "all" else int(parts[0])
    # 类别/属性筛选（第一百/一百零一批，Drilbur「Dig Dig Dig」）：
    # `search_discard:3:energy:type:fighting` —— 卡面明写「Basic {F} Energy」，
    # 不筛选就会把任意 3 张弃掉（弃错牌库 = 静默改资源）。
    kind, ptype = "any", None
    i = 1
    while i < len(parts):
        if parts[i] == "type" and i + 1 < len(parts):
            ptype = parts[i + 1].lower()
            i += 2
            continue
        if parts[i] in ("energy", "pokemon", "trainer", "any") and i == 1:
            kind = parts[i]
            i += 1
            continue
        raise ValueError(f"search_discard 参数非法：{raw}")
    from .cards import EnergyRef, PokeRef, TrainerRef
    picked = []
    for idx in reversed(range(len(p.deck))):
        if len(picked) >= n:
            break
        c = p.deck[idx]
        if kind == "energy" and not isinstance(c, EnergyRef):
            continue
        if kind == "pokemon" and not isinstance(c, PokeRef):
            continue
        if kind == "trainer" and not isinstance(c, TrainerRef):
            continue
        if ptype and kind == "energy" \
                and str(getattr(c.edef, "type", "")).lower() != ptype:
            continue
        picked.append(p.deck.pop(idx))
    for ref in picked:
        battle._to_discard(p, ref)
    if picked:
        battle.rng.shuffle(p.deck)
    return len(picked)


def _energy_self_to_deck(battle, p: PlayerState,
                         fp: Optional[FieldPokemon]) -> int:
    """把 fp 身上附着的**全部能量**还原成卡并洗回牌库 → 移动张数

    卡面原型（Mega Skarmory ex POR 55「Sonic Ripper」，2026-11-03 第四十一批）：
    「Shuffle all Energy attached to this Pokémon into your deck, and this attack
    does 220 damage to 1 of your opponent's Pokémon.」

    · 基本能量在引擎里是**计数**（`fp.energy`）→ 按点数各还原 1 张能量卡；
    · 特殊能量是**卡**（`fp.senergy`）→ 整张原样放回（不拆成提供量）；
    · 洗回后**立刻洗牌**（与检索后洗牌同一约定，`battle.rng` 驱动、可复现）。

    ⚠ 只把计数清零而不往牌库放卡，等于「洗回」变成「消失」（牌库少 N 张，
    抽空的胜负判定会提前）—— 必须真的建卡。卡名按属性生成基本能量定义。
    """
    if fp is None:
        return 0
    moved = 0
    for t, n in list(fp.energy.items()):
        for _ in range(int(n)):
            p.deck.append(EnergyRef(EnergyDef(str(t))))
            moved += 1
    fp.energy.clear()
    for ref in list(fp.senergy):
        p.deck.append(ref)
        moved += 1
    fp.senergy.clear()
    if moved:
        battle.rng.shuffle(p.deck)
    return moved


def _recover_to_hand(battle, p: PlayerState, cands: list) -> int:
    """按弃牌堆下标把卡取回手牌（文本层与类型化层同步）→ 实际取回数"""
    got = 0
    for di in sorted(cands, reverse=True):
        ref = battle._rebuild_ref(p.discard_refs.pop(di))
        label = _card_label(ref)
        for k in range(len(p.discard) - 1, -1, -1):
            if p.discard[k] == label:
                p.discard.pop(k)
                break
        p.hand.append(ref)
        got += 1
    return got


def _move_one_energy(src: FieldPokemon, dst: FieldPokemon,
                     etype: Optional[str] = None) -> bool:
    """从 src 移 1 点能量到 dst（指定属性优先；无色优先，其次任意色）

    特殊能量按**卡**迁移（整张从 src.senergy 移到 dst.senergy）—— 提供量仍按
    新的持有者实时计算（Neo Upper 换到 Stage 2 上就会变成任意 2 点）。
    """
    if src is None or dst is None or src is dst:
        return False
    if etype:
        if src.energy.get(etype, 0) > 0:
            src.energy[etype] -= 1
            if src.energy[etype] == 0:
                src.energy.pop(etype, None)
            dst.add_energy(etype, 1)
            return True
        for ref in reversed(src.senergy):
            prov = special_energy.provides(ref.edef, src)
            if etype in prov or special_energy.ANY in prov or any(
                    k.startswith(special_energy.FLEX_PREFIX)
                    and etype in k[len(special_energy.FLEX_PREFIX):].split(",")
                    for k in prov):
                src.senergy.remove(ref)
                dst.senergy.append(ref)
                return True
        return False
    if src.energy:
        c = src.energy.get(COLORLESS, 0)
        color = COLORLESS if c > 0 else next(iter(src.energy))
        src.energy[color] -= 1
        if src.energy[color] == 0:
            src.energy.pop(color, None)
        dst.add_energy(color, 1)
        return True
    if src.senergy:
        dst.senergy.append(src.senergy.pop())
        return True
    return False


def _discard_energy_from(battle, owner_pl, fp: FieldPokemon, n: int) -> int:
    """从 ``fp`` 弃置 n 点能量 → 实际弃置数（**特殊能量卡进弃牌堆**）

    与 `FieldPokemon.remove_any` 的差别只在最后一步：`remove_any` 把特殊能量
    从 `senergy` 里 pop 掉就结束，**卡不进弃牌堆**。对「弃置」类效果这是错的 ——
    特殊能量是**有身份的卡**：
      · Boomerang Energy 的整条规则就是「被弃置后从弃牌堆回附」，卡不进弃牌堆
        就永远回不来（实测：`discard_energy:all` 打完，`discard_refs` 里一张没有）；
      · 任何「从弃牌堆回收能量」的效果都会看不到它。
    因此这里基本能量沿用 `remove_any` 的顺序（无色优先 → 其它基本色），
    特殊能量改走 `_discard_special_energy`（`_to_discard`，文本层与类型化层同步）。
    """
    if fp.total_energy() < n:
        return 0
    done = 0
    # ① 基本能量：无色优先，其次按属性名（与 `remove_any` 同口径）
    order = [COLORLESS] + [t for t in sorted(fp.energy) if t != COLORLESS]
    for t in order:
        if done >= n:
            break
        have = fp.energy.get(t, 0)
        if have <= 0:
            continue
        k = min(have, n - done)
        fp.energy[t] = have - k
        if fp.energy[t] <= 0:
            del fp.energy[t]
        done += k
    # ② 还不够 → 弃特殊能量**卡**
    if done < n:
        done += _discard_special_energy(battle, owner_pl, fp, n - done)
    return done


def _discard_special_energy(battle, owner_pl, fp: FieldPokemon, n: int) -> int:
    """弃置 ``fp`` 上的**特殊能量卡**（按卡，不按提供点数）→ 实际弃置数

    特殊能量存在 `senergy`（EnergyRef 列表），因此引擎能精确区分「特殊 / 基本」。
    """
    done = 0
    while done < n and fp.senergy:
        ref = fp.senergy.pop()
        battle._to_discard(owner_pl, ref)
        done += 1
    return done


def _energy_to_hand(battle, owner: int, fp: FieldPokemon, n: int,
                    etype: Optional[str] = None) -> int:
    """把 ``fp`` 上的 n 点能量作为**卡**收回 ``owner`` 的手牌（返回实际张数）

    选择口径（确定性）：
      · 指定属性 → 只取该色基本能量（不足则停，不拿别的颜色顶替）；
      · 未指定 → **无色优先**，其次按属性名排序；基本能量不足时才按整张卡
        取特殊能量（特殊能量按卡回收，不拆分其提供量）。
    """
    p = battle.players[owner]
    got = 0
    while got < n:
        color = None
        if etype:
            if fp.energy.get(etype, 0) > 0:
                color = etype
        elif fp.energy:
            color = (COLORLESS if fp.energy.get(COLORLESS, 0) > 0
                     else sorted(fp.energy)[0])
        if color is None:
            break
        fp.energy[color] -= 1
        if fp.energy[color] <= 0:
            fp.energy.pop(color, None)
        p.hand.append(EnergyRef(EnergyDef(color)))
        got += 1
    while got < n and not etype and fp.senergy:
        p.hand.append(fp.senergy.pop())
        got += 1
    return got


def _bounce_filter_of(tag: str) -> tuple:
    """``bounce:discard_to_bench:N[:name:<key>][:hp:<n>]`` → (n, name_key, hp)"""
    parts = tag.split(":")
    n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
    name_key, hp = None, None
    i = 2
    while i < len(parts):
        if parts[i] == "name" and i + 1 < len(parts):
            name_key = _name_key(parts[i + 1])
            i += 2
            continue
        if parts[i] == "hp" and i + 1 < len(parts):
            hp = int(parts[i + 1]) if parts[i + 1].isdigit() else None
            i += 2
            continue
        i += 1
    return n, name_key, hp


def _bounce_discard_candidates(p: PlayerState, tag: str,
                               max_bench: int) -> list:
    """弃牌堆里可铺到后备的宝可梦下标（与执行侧**同一口径**）"""
    from .cards import PokeRef
    n, name_key, hp = _bounce_filter_of(tag)
    picked = []
    for idx in range(len(p.discard_refs) - 1, -1, -1):
        if len(picked) >= n or len(p.bench) + len(picked) >= max_bench:
            break
        c = p.discard_refs[idx]
        if not isinstance(c, PokeRef):
            continue
        if name_key and name_key not in _name_key(c.pdef.name):
            continue
        if hp is not None and int(c.pdef.hp) > hp:
            continue
        picked.append(idx)
    return picked


def _bounce_legal_targets(p: PlayerState) -> list:
    """可回手/洗回的己方位置（战斗位**无替补时不可选**：不能回手到空场）"""
    out = []
    for tpos, _fp in _field_iter(p):
        if tpos == 0 and not p.bench:
            continue
        out.append(tpos)
    return out


def _chain_root(pdef):
    """沿 evolves_from 回溯到阶段 0 的卡名（未登记链 → None）"""
    from .cards import pokemon_def
    cur = pdef
    guard = 0
    while getattr(cur, "stage", 0) > 0 and guard < 8:
        prev = pokemon_def(getattr(cur, "evolves_from", None))
        if prev is None:
            return None
        cur = prev
        guard += 1
    return cur.name if getattr(cur, "stage", 0) == 0 else None


def _candy_legals(p: PlayerState) -> list:
    """跳阶进化（Rare Candy）候选 → [(hand_index, 场上位置, 目标卡名)]

    规则子集：手牌 stage≥2 的卡可直接放置到**其进化链的根基础宝可梦**上
    （跳过中间阶段）。链根判定：沿 ``evolves_from`` 回溯到 stage==0。
    """
    from .cards import PokeRef
    out = []
    for hi, c in enumerate(p.hand):
        if not isinstance(c, PokeRef) or c.pdef.stage < 2:
            continue
        root = _chain_root(c.pdef)
        if root is None:
            continue
        for tpos, fp in _field_iter(p):
            if fp is None or fp.just_played:
                continue
            if fp.pdef.stage != 0 or fp.pdef.name != root:
                continue
            out.append((hi, tpos, c.pdef.name))
    return out


# ---------------- v1.4 伤害修正：计数源与条件 ----------------
# 设计原则：**只允许公开信息**（双方可见的场上/弃牌堆/手牌数量），
# 且必须确定性可算；未知名称一律抛错——静默按 0 计算会让「条件加伤」变成
# 永久 0 加伤，属于最难发现的口径漂移。
_COUNTERS = {
    "own_bench": lambda b, p, o: len(p.bench),
    "opp_bench": lambda b, p, o: len(o.bench),
    "both_bench": lambda b, p, o: len(p.bench) + len(o.bench),
    "own_active_energy": lambda b, p, o: p.active.total_energy() if p.active else 0,
    "opp_active_energy": lambda b, p, o: o.active.total_energy() if o.active else 0,
    "both_active_energy": lambda b, p, o: ((p.active.total_energy() if p.active else 0)
                                          + (o.active.total_energy() if o.active else 0)),
    "own_all_energy": lambda b, p, o: sum(fp.total_energy() for fp in p.all_field()),
    "opp_all_energy": lambda b, p, o: sum(fp.total_energy() for fp in o.all_field()),
    "own_bench_counters": lambda b, p, o: sum(fp.damage // 10 for fp in p.bench),
    "opp_bench_counters": lambda b, p, o: sum(fp.damage // 10 for fp in o.bench),
    "own_active_counters": lambda b, p, o: (p.active.damage // 10) if p.active else 0,
    "opp_active_counters": lambda b, p, o: (o.active.damage // 10) if o.active else 0,
    "own_discard_energy": lambda b, p, o: sum(1 for c in p.discard_refs
                                              if isinstance(c, EnergyRef)),
    "own_discard_count": lambda b, p, o: len(p.discard_refs),
    "own_hand": lambda b, p, o: len(p.hand),
    "own_prizes_left": lambda b, p, o: p.prizes_left,
    "opp_prizes_left": lambda b, p, o: o.prizes_left,
    # 「已取得」奖赏卡数 —— 与 `*_prizes_left`（剩余）**口径相反**，不可混用。
    # 为什么单列：卡面常见 "for each Prize card your opponent has taken"，
    # 早期解析把它按 prizes_left（剩余）映射 → 伤害随对手取卡而**减少**（方向反了）。
    # 二者数量上互补，但语义相反，混用是**静默算错**（本轮审计发现 4 张卡受影响）。
    "own_prizes_taken": lambda b, p, o: b.cfg.prizes - p.prizes_left,
    "opp_prizes_taken": lambda b, p, o: b.cfg.prizes - o.prizes_left,
    "own_field": lambda b, p, o: len(p.all_field()),
    "opp_field": lambda b, p, o: len(o.all_field()),
    "own_bench_stage2": lambda b, p, o: sum(1 for fp in p.bench if fp.pdef.stage == 2),
    # 类别限定的「在场」计数（阶段可直接判定，非子集近似）
    "own_basic": lambda b, p, o: sum(1 for fp in p.all_field()
                                     if fp.pdef.stage == 0),
    "own_evolution": lambda b, p, o: sum(1 for fp in p.all_field()
                                         if fp.pdef.stage > 0),
    # B3（2026-10-28）：按数据集分类字段计数
    # 「Draw a card for each of your Ancient Pokémon in play.」（Awakening Drum）
    "own_ancient": lambda b, p, o: sum(
        1 for fp in p.all_field() if str(fp.pdef.category) == "Ancient"),
    # S6 计数缩放（2026-10-28）：
    # · 「for each of your opponent's Pokémon {ex} in play」（Dudunsparce ex）
    # · 「for each Pokémon Tool attached to all Pokémon in play」（Bronzong）
    # · 「for each {C} in your opponent's Active Pokémon's Retreat Cost」（Throh）
    #   撤退费用用**有效值**（含场地/道具/特性修正），不能用印刷值。
    "own_ex": lambda b, p, o: sum(1 for fp in p.all_field() if fp.pdef.is_ex),
    "opp_ex": lambda b, p, o: sum(1 for fp in o.all_field() if fp.pdef.is_ex),
    # 「for each of your opponent's Pokémon {ex} and Pokémon {V} in play」
    # （第九十四批，Zoroark SFA 32「Illusory Hijacking」）
    # 同上：本卡池无 V 类卡，与 `opp_ex` 当前同值；独立起名以便将来加 V 时定点修改。
    "opp_ex_or_v": lambda b, p, o: sum(1 for fp in o.all_field() if fp.pdef.is_ex),
    "both_field_tools": lambda b, p, o: sum(
        1 for fp in p.all_field() + o.all_field() if fp.tool),
    "opp_active_retreat": lambda b, p, o: (
        _retreat_cost(o.active, b) if o.active is not None else 0),
    # 对手手牌**张数**（公开信息；内容仍隐藏）
    "opp_hand": lambda b, p, o: len(o.hand),
    # 对手全场伤害指示物总数 / 战斗位特殊状态数
    "opp_field_counters": lambda b, p, o: sum(fp.damage // 10
                                               for fp in o.all_field()),
    "opp_active_status_count": lambda b, p, o: (len(o.active.status)
                                                if o.active else 0),
    # 自身全场的 Pokémon Tool 数量（每只最多 1 张）
    "own_field_tools": lambda b, p, o: sum(1 for fp in p.all_field() if fp.tool),
    # 「in this way」类：本回合已弃置的牌数（含本次招式结算中弃的）
    "own_discarded_this_turn": lambda b, p, o: p.discarded_this_turn,
    "own_discarded_energy_this_turn":
        lambda b, p, o: p.discarded_energy_this_turn,
}
# 能量计数源：``<zone>_energy[_<color>]`` —— 支持「附在此宝可梦上的水能量」这类
# **带颜色筛选**的精确计数（笼统按总能量数会系统性多算伤害）
_ENERGY_ZONES = {
    "own_active": lambda b, p, o: ([p.active] if p.active else []),
    "opp_active": lambda b, p, o: ([o.active] if o.active else []),
    "both_active": lambda b, p, o: [x for x in (p.active, o.active) if x],
    "own_all": lambda b, p, o: p.all_field(),
    "opp_all": lambda b, p, o: o.all_field(),
}
_ENERGY_COUNTER_RE = re.compile(
    r"^(own_active|opp_active|both_active|own_all|opp_all)_energy(?:_([a-z]+))?$")

_CONDITIONS = {
    # 「if any of your Pokémon were Knocked Out ... during your opponent's last turn」
    "opp_ko_last_turn": lambda b, p, o: b._ko_flags()[1 - b.current] > 0,
    "own_ko_last_turn": lambda b, p, o: b._ko_flags()[b.current] > 0,
    # 第一百零九批：只统计 **Team Rocket's** 宝可梦被击倒（TR Archer）
    "own_ko_tr_last_turn": lambda b, p, o: b._ko_tr_flags()[b.current] > 0,
    # 第一百一十二批（Slaking ex「Born to Slack」）：「opponent has **no** Pokémon
    # {ex} or {V} in play」—— 用**正向**条件 + `not:` 前缀表达更清楚，
    # 但这里直接给正向布尔，避免调用方漏写 `not:`。
    "opp_has_ex_or_v": lambda b, p, o: any(
        fp.pdef.is_ex for fp in o.all_field()),
    # 奖励落后（对手已取得更多奖赏卡 = 我方落后）
    "own_behind_prizes": lambda b, p, o: p.prizes_left > o.prizes_left,
    "own_prizes_full": lambda b, p, o: p.prizes_left == b.cfg.prizes,
    "opp_has_bench": lambda b, p, o: len(o.bench) > 0,
    "own_has_bench": lambda b, p, o: len(p.bench) > 0,
    # ---- 2026-10-26 第四批：条件伤害缩放的高频条件（全部为「当下局面可直接读出」，
    #      不依赖本回合历史，因此无需新增 turn 级状态位）----
    # 「if your opponent's Active Pokémon already has any damage counters on it」
    "opp_active_has_damage": lambda b, p, o: o.active is not None and o.active.damage > 0,
    # 「if this Pokémon has any damage counters on it」
    "own_active_has_damage": lambda b, p, o: p.active is not None and p.active.damage > 0,
    # 「if your opponent's Active Pokémon is a Pokémon {ex}」
    "opp_active_is_ex": lambda b, p, o: o.active is not None and bool(o.active.pdef.is_ex),
    # 「if your opponent's Active Pokémon is a Pokémon {ex} or Pokémon {V}」
    # （第九十三批，Galvantula ex「Charged Web」）
    # ⚠ 本卡池（1267 张）**没有 Pokémon V**——`is_v` 这个判定在引擎里也不存在。
    #   单独起名而**不复用** `opp_active_is_ex`，是为了让「将来加入 V 类卡」这件事
    #   显式暴露：目前两个名字求值相同，谁改 V 支持就必须同步改这里，
    #   而不会像复用那样把一个已经"看起来正确"的条件悄悄变错。
    "opp_active_is_ex_or_v": lambda b, p, o: (
        o.active is not None and bool(o.active.pdef.is_ex)),
    # 「if your opponent's Active Pokémon is an Evolution Pokémon」
    "opp_active_is_evolution": lambda b, p, o: o.active is not None and o.active.pdef.stage > 0,
    # ---- 2026-10-27 第五批：仍是「当下局面可直接读出」的条件 ----
    # 「If a Stadium is in play」/「If you have a Stadium in play」——场地双方共用
    "stadium_in_play": lambda b, p, o: bool((b.stadium or {}).get("tag")),
    # 「If this Pokémon has a Pokémon Tool attached」
    "own_active_has_tool": lambda b, p, o: p.active is not None and bool(p.active.tool),
    # 「If this Pokémon has no damage counters on it」（= 满 HP）
    "own_active_full_hp": lambda b, p, o: p.active is not None and p.active.damage == 0,
    # 「If your opponent's Active Pokémon is affected by a Special Condition」
    "opp_active_has_status": lambda b, p, o: o.active is not None and bool(o.active.status),
    # 「If your Benched Pokémon have any damage counters on them」
    "own_bench_has_damage": lambda b, p, o: any(fp.damage > 0 for fp in p.bench),
    # 「If you have the same number of cards in your hand as your opponent」
    "own_hand_eq_opp_hand": lambda b, p, o: len(p.hand) == len(o.hand),
    # 「If this Pokémon and your opponent's Active Pokémon have the same amount of
    #   Energy attached」
    "own_opp_active_energy_equal": lambda b, p, o: (
        p.active is not None and o.active is not None
        and p.active.total_energy() == o.active.total_energy()),
    # B3（2026-10-28）：「if you have any Mega Evolution Pokémon {ex} in play」
    # 只能由数据集的 `rule` 判定（`is_mega`），不能靠卡名子串猜。
    "own_mega_in_play": lambda b, p, o: any(
        bool(getattr(fp.pdef, "is_mega", False)) for fp in p.all_field()),
    # ---- 第六批（2026-10-28）：仍为「当下局面可直接读出」的条件 ----
    # 「If your opponent's Active Pokémon is a Tera Pokémon」（Regigigas）
    "opp_active_tera": lambda b, p, o: (
        o.active is not None
        and str(o.active.pdef.category or "").startswith("Tera")),
    # 「If you have any Tera Pokémon on your Bench」（Ho-Oh）
    "own_bench_tera": lambda b, p, o: any(
        str(fp.pdef.category or "").startswith("Tera") for fp in p.bench),
    # ---- 第五十九批（2026-11-04）：打出前置条件的两个新判定 ----
    # 「you have any Tera Pokémon **in play**」= 场上任意位置（含后备），
    # 不是只看后备 —— 与上面 `own_bench_tera` 的区别就在这里。
    "tera_in_play": lambda b, p, o: any(
        str(fp.pdef.category or "").startswith("Tera") for fp in p.all_field()),
    # 「you go second, and only during your first turn」——先手是 index 0
    # （`Battle.current` 从 0 起交替），后手恒为 1；「first turn」按**该玩家
    # 自己的**回合计数，不能用 `turn_number`（那是全局计数，后手首回合 = 2）。
    "went_second_first_turn": lambda b, p, o: (
        b.current == 1 and b.turn_counts[1] == 1),
    # 「If this Pokémon has any Special Energy attached」（Stoutland）
    "own_active_special_energy": lambda b, p, o: (
        p.active is not None and bool(p.active.senergy)),
    # 「If any of your Pokémon in play are the same type as any of your
    #  opponent's Pokémon in play」（Enamorus）
    "same_type_in_play": lambda b, p, o: any(
        a.pdef.ptype == c.pdef.ptype
        for a in p.all_field() for c in o.all_field()),
    # ---- 第七批（2026-10-28）：回合历史（只能靠回合记账，记录的是**回合序号**）----
    # 「If this Pokémon moved from your Bench to the Active Spot this turn」
    "moved_to_active": lambda b, p, o: (
        p.active is not None and p.active.moved_active_turn == b.turn_number),
    # 「If this Pokémon evolved during this turn」
    "evolved_this_turn": lambda b, p, o: (
        p.active is not None and p.active.evolved_turn == b.turn_number),
    # 「If this Pokémon was healed during this turn」
    "healed_this_turn": lambda b, p, o: (
        p.active is not None and p.active.healed_turn == b.turn_number),
    # 「If 1 of your other Ancient Pokémon used an attack during your last turn」
    "own_other_ancient_attacked_last_turn": lambda b, p, o: any(
        p.active is None or n != p.active.name
        for n in p.last_turn_ancient_attackers),
}

# 带参数的**条件**（与 `_CONDITIONS` 同样进白名单，只是需要参数）。
# ⚠ 与「计数源」区分：这里回答的是**是/否**，不是「几个」。
# 拼写必须与 `rules.STATUSES`（poison/burn/sleep/paralyze/confuse）以及
# `engine.cards.TYPES`（grass/fire/...）一致 —— 参数值不做任何兜底猜测。
_CONDITION_PARAMS = {
    "opp_active_status": "special condition name ∈ rules.STATUSES",
    "opp_active_type": "energy/type name ∈ engine.cards.TYPES",
    "opp_bench_at_least": "整数 n ≥ 0",
    "own_all_energy_at_least": "整数 n ≥ 0",
    # ---- 2026-10-27 第五批（全部为当下局面可判定）----
    "own_active_status": "special condition name ∈ rules.STATUSES",
    "opp_hand_at_most": "整数 n ≥ 0（对手手牌数 ≤ n）",
    "opp_prizes_at_most": "整数 n ≥ 0（对手剩余奖赏 ≤ n）",
    "own_prizes_exactly": "整数 n ≥ 0（我方剩余奖赏 = n）",
    "opp_active_is_stage": "0/1/2（对手战斗位进化阶段）",
    "own_active_hp_at_most": "整数（自身剩余 HP ≤ n）",
    # 2026-10-29 第十七批：牌库剩余张数（Wo-Chien「If there are 3 or fewer
    # cards in your deck」）
    "own_deck_at_most": "整数 n ≥ 0（我方牌库剩余 ≤ n）",
    "own_active_energy_type_at_least": "<color>:<n>（自身战斗位该色能量 ≥ n）",
    # 2026-10-29 第十九批：额外能量（Electivire ex / Jellicent ex / Tangrowth）
    "own_active_extra_energy_at_least": "整数 n ≥ 0（自身战斗位能量数 − 本次招式费用 ≥ n）",
    "own_all_energy_type_at_least": "<color>:<n>（全场该色能量 ≥ n）",
    "opp_active_resistance": "energy/type name ∈ engine.cards.TYPES",
    "own_bench_name": "卡名归一化键（小写、去非字母数字）",
    # B3（2026-10-28）：抽牌完整性用到的两个**当下可判定**条件
    "own_hand_at_least": "整数 n ≥ 0（我方手牌数 ≥ n）",
    "own_all_name": "卡名归一化子串（我方场上全部宝可梦都含该子串）",
    # ---- 第六批（2026-10-28）----
    "own_active_energy_tag": "具名能量（如 teamrocket，对应 senergy:team_rocket）",
    "own_discard_basic_energy_at_least": "<color>:<n>（弃牌堆该色能量 ≥ n）",
    "own_bench_stage2_type": "energy/type name ∈ engine.cards.TYPES",
    "own_active_status_any": "状态名列表（逗号分隔，任一命中即可）",
    "own_discard_has_named": "卡名归一化子串（弃牌堆中存在）",
    "opp_discard_has_named": "卡名归一化子串（**对手**弃牌堆中存在）",
    "opp_hand_exactly": "整数 n ≥ 0（对手手牌数**恰好** = n）",
    "own_field_name_at_least": "卡名归一化子串:整数 n（我方场上该名字数 ≥ n）",
    # ---- 第七批（2026-10-28）----
    "evolved_from": "卡名归一化键（本回合由该宝可梦进化而来）",
    "used_attack_last_turn": "招式名归一化键（我上回合用过）",
    "own_bench_at_most": "整数 n ≥ 0（我方后备数 ≤ n）",
    "own_hand_exactly": "整数 n ≥ 0（我方手牌数 = n）",
    "own_hand_at_most": "整数 n ≥ 0（我方手牌数 ≤ n）",
    "opp_prizes_in": "剩余奖赏数列表（逗号分隔，任一命中）",
    "own_bench_named_all": "卡名归一化键（+ 分隔，需**全部**在后备）",
    # ---- 第九十三批（2026-11-05）----
    # 说明文字刻意写短：`_CONDITION_PARAMS` 会整体注入 LLM 提示词，
    # 提示词有 9400 字符预算（见 agent/tests/test_agent.py 的论证）。
    "own_bench_named_has_damage": "卡名子串（我方后备该名带指示物）",
    "played_supporter_named": "卡名子串（本回合打出过该支援者）",
}


def dmg_vocab() -> dict:
    """伤害修正层可用的**取值域**（供提示词注入 —— 避免文档与引擎漂移）

    为什么要有这个公开访问器：K2 提示词需要告诉模型 `dmg_plus:N:per:<counter>`
    里 `<counter>` 能填什么。若把取值**硬编码进提示词**，引擎加了新计数源而提示词
    忘了同步，就会出现「模型按提示词填了、引擎却抛错」的错配（本项目已因提示词与
    代码不同步踩过一次：枚举写成引号内 `a|b|c` 导致模型照抄）。
    → 故取值域只有**引擎这一处定义**，提示词渲染时动态注入。

    返回的 `counters` 只含具名计数源；`<zone>_energy[_<color>]` 形式由
    `energy_zones` 与颜色组合而成（见 `_ENERGY_COUNTER_RE`）。
    """
    return {
        "counters": sorted(_COUNTERS),
        "conditions": sorted(_CONDITIONS),
        # 带参条件的**形态说明**（提示词/文档用；不进无参 `conditions` 列表，
        # 避免模型把 `opp_active_type:` 这种半截字符串当无参条件照抄）
        "condition_params": [f"{k}:<{v}>" for k, v in sorted(_CONDITION_PARAMS.items())],
        "energy_zones": sorted(_ENERGY_ZONES),
        # 静态被动白名单（2026-10-28 第八批新增 dmg_immune / bench_immune_dmg）
        "passive_tags": ["bench_immune", "bench_immune_dmg", "prev_attacks",
                         "dmg_immune:<self|team_bench>[:from:<src>|:min:<n>|"
                         "no_rule_box][:effects]"],
    }


# 带参数的**动态计数源**（2026-10-28）：形态固定、参数是键名。
# 供 `is_known_counter()` 与测试守卫共用，避免"词表在测试里另写一份"。
_DYNAMIC_COUNTER_RE = re.compile(
    r"^(?:"
    r"(?:own|opp)_field_has_attack:[a-z0-9]+"
    r"|own_discard_has_attack:[a-z0-9]+"
    r"|own_discard_named:[a-z0-9]+"
    r"|opp_discard_named:[a-z0-9]+"
    r"|own_discard_named_supporter:[a-z0-9]+"
    r"|both_field_named_any:[a-z0-9]+(?:\+[a-z0-9]+)*"
    r"|opp_hand_kind:(?:pokemon|trainer|energy)"
    r"|opp_prizes_taken_last_turn"
    # 第二十六批（2026-10-29）：按**属性/具名**限定的后备指示物数，以及
    # 「带指示物的具名宝可梦」**只数只数**（Gigalith / Cynthia's Spiritomb /
    # Paldean Tauros）。与 `own_bench_counters`（全体后备）严格区分。
    r"|own_bench_counters_type:[a-z]+"
    r"|own_bench_counters_named:[a-z0-9]+"
    r"|own_field_named_with_damage:[a-z0-9]+"
    # 第四十三批（2026-11-03）：按**卡牌子类**（Ancient）的弃牌堆计数，以及
    # 「具名阵营 + 属性」的全队能量数（Roaring Moon / Iono's Voltorb）。
    r"|own_discard_category:[a-z0-9]+"
    r"|own_field_named_energy:[a-z0-9]+:[a-z]+"
    r")$")


def is_known_counter(name: str) -> bool:
    """计数源是否被引擎认识（词表唯一来源；`test_pool_tags` 也调用它）"""
    c = str(name or "")
    if c.isdigit() or c in _COUNTERS:
        return True
    if _ENERGY_COUNTER_RE.match(c) or _DYNAMIC_COUNTER_RE.match(c):
        return True
    return bool(re.match(r"^(?:own|opp)_named_[a-z0-9]+$", c)) \
        or bool(re.match(r"^(?:own|opp)_discard_energy(?:_[a-z]+)?$", c)) \
        or c == "opp_discard_item"


def _eval_counter(battle, counter: str) -> int:
    """计数源求值（未知 → 抛错，禁止静默 0）"""
    if counter.isdigit():
        return int(counter)
    p = battle.players[battle.current]
    o = battle.players[1 - battle.current]
    # S6 计数缩放（2026-10-28）：按**卡名子串**或**弃牌堆类别**计数。
    # 卡名归一化规则与 `agent.effects._tag_name_key` / `_name_key` 一致。
    if counter.startswith(("own_named_", "opp_named_")):
        side, _, key = counter.partition("_named_")
        pl = p if side == "own" else o
        return sum(1 for fp in pl.all_field() if key and key in _name_key(fp.pdef.name))
    m = re.match(r"^(own|opp)_discard_energy(?:_([a-z]+))?$", counter)
    if m:
        pl = p if m.group(1) == "own" else o
        color = m.group(2)
        return sum(1 for c in pl.discard_refs
                   if isinstance(c, EnergyRef)
                   and (not color or str(c.edef.type) == color))
    if counter == "opp_discard_item":
        return sum(1 for c in o.discard_refs
                   if isinstance(c, TrainerRef)
                   and str(getattr(c.tdef, "kind", "")) == "item")
    # ---- 第六批（2026-10-28）：带限定词的**子集**计数（逐项精确，不用总数近似）----
    # 「for each of your Pokémon in play that has the Round attack」（Round 四张）
    m = re.match(r"^(own|opp)_field_has_attack:([a-z0-9]+)$", counter)
    if m:
        pl = p if m.group(1) == "own" else o
        key = m.group(2)
        return sum(1 for fp in pl.all_field()
                   if any(_name_key(a.name) == key for a in fp.pdef.attacks))
    # 「for each Pokémon in play that has “Koffing” or “Weezing” in its name
    #  (both yours and your opponent's)」
    m = re.match(r"^both_field_named_any:([a-z0-9]+(?:\+[a-z0-9]+)*)$", counter)
    if m:
        keys = m.group(1).split("+")
        return sum(1 for fp in p.all_field() + o.all_field()
                   if any(k and k in _name_key(fp.pdef.name) for k in keys))
    # 「for each Pokémon in your discard pile that has the United Wings attack」
    m = re.match(r"^own_discard_has_attack:([a-z0-9]+)$", counter)
    if m:
        key = m.group(1)
        return sum(1 for c in p.discard_refs
                   if isinstance(c, PokeRef)
                   and any(_name_key(a.name) == key for a in c.pdef.attacks))
    # 「for each Ethan's Adventure card in your discard pile」/「… Supporter card
    #  that has “Team Rocket” in its name in your discard pile」
    m = re.match(r"^own_discard_named:([a-z0-9]+)$", counter)
    if m:
        key = m.group(1)
        return sum(1 for c in p.discard_refs
                   if key in _name_key(_card_label(c)))
    m = re.match(r"^opp_discard_named:([a-z0-9]+)$", counter)
    if m:
        key = m.group(1)
        return sum(1 for c in o.discard_refs
                   if key in _name_key(_card_label(c)))
    m = re.match(r"^own_discard_named_supporter:([a-z0-9]+)$", counter)
    if m:
        key = m.group(1)
        return sum(1 for c in p.discard_refs
                   if isinstance(c, TrainerRef)
                   and str(getattr(c.tdef, "kind", "")) == "supporter"
                   and key in _name_key(c.tdef.name))
    # 「for each Prize card your opponent took during their last turn」（Okidogi）
    if counter == "opp_prizes_taken_last_turn":
        return o.prizes_taken_last_turn
    # 「This attack does 50 damage for each Trainer card you find there」
    # （对手手牌按**类别**计数；引擎本就持有 `o.hand`，与既有 `opp_hand` 同口径）
    m = re.match(r"^opp_hand_kind:(pokemon|trainer|energy)$", counter)
    if m:
        kind = m.group(1)
        if kind == "pokemon":
            return sum(1 for c in o.hand if isinstance(c, PokeRef))
        if kind == "energy":
            return sum(1 for c in o.hand if isinstance(c, EnergyRef))
        return sum(1 for c in o.hand if isinstance(c, TrainerRef))
    # ---- 第二十六批（2026-10-29）：限定后**指示物**计数源 ----
    # 卡面：「for each damage counter on all of your Benched {F} Pokémon」
    # （Gigalith）/「… all of your Benched Cynthia's Pokémon」（Cynthia's
    # Spiritomb）—— 按**子集**求和；用全体后备 `own_bench_counters` 会多算。
    m = re.match(r"^own_bench_counters_type:([a-z]+)$", counter)
    if m:
        color = m.group(1)
        if color not in TYPES:
            # 属性拼错（如 rainbow）会让伤害静默变 0 —— 必须抛错
            raise ValueError(f"未知宝可梦属性 {color}（{counter}）")
        return sum(fp.damage // 10 for fp in p.bench
                   if str(fp.pdef.ptype) == color)
    m = re.match(r"^own_bench_counters_named:([a-z0-9]+)$", counter)
    if m:
        key = m.group(1)
        return sum(fp.damage // 10 for fp in p.bench
                   if key and key in _name_key(fp.pdef.name))
    # 「for each of your Pokémon that has “Tauros” in its name that has any
    #  damage counters on it」（Paldean Tauros）—— 数的是**只数**，不是指示物数。
    m = re.match(r"^own_field_named_with_damage:([a-z0-9]+)$", counter)
    if m:
        key = m.group(1)
        return sum(1 for fp in p.all_field()
                   if key and key in _name_key(fp.pdef.name) and fp.damage > 0)
    # ---- 第四十三批（2026-11-03）：两个限定计数源 ----
    # 「for each Ancient card in your discard pile」（Roaring Moon）——
    # Ancient 是**卡牌子类**（`category`），不是卡名子串：按卡名找会恒为 0
    # （静默少算），按弃牌堆总数又会把别的卡算进来（静默多算）。
    m = re.match(r"^own_discard_category:([a-z0-9]+)$", counter)
    if m:
        cat = m.group(1)

        def _cat_of(c):
            d = getattr(c, "pdef", None) or getattr(c, "tdef", None)
            return str(getattr(d, "category", "") or "").lower()

        return sum(1 for c in p.discard_refs if cat and cat in _cat_of(c))
    # 「for each {L} Energy attached to all of your Iono's Pokémon」
    # （Iono's Voltorb）—— 阵营 + **属性** + 具名三重限定；用全队
    # `own_field_energy_<color>` 会把别的宝可梦身上的能量也算进来。
    m = re.match(r"^own_field_named_energy:([a-z0-9]+):([a-z]+)$", counter)
    if m:
        key, color = m.group(1), m.group(2)
        if color not in TYPES:
            raise ValueError(f"未知宝可梦属性 {color}（{counter}）")
        return sum(fp.energy_of_type(color) for fp in p.all_field()
                   if key and key in _name_key(fp.pdef.name))
    # ---- 第七十八批（2026-11-04）：按**属性**数在场宝可梦 ----
    # 「Flip a coin for each {D} Pokémon you have in play」（Scrafty WHT 58）——
    # 数的是**只数**、且限定属性。用 `own_field`（全队只数）会多算别的属性，
    # 用 `own_all_energy` 更是完全不同的量纲（能量 vs 只数）。
    # ⚠ 计数源名用**下划线**不用冒号：`coin_each:<counter>:<side>:<sub>` 里
    # counter 是中间段，带冒号会被 `split(":", 3)` 截断（`own_field_type` 而丢掉
    # 属性 → 「未知计数源」）。冒号形态保留给 `per:<counter>` 收尾的位置使用。
    m = re.match(r"^own_field_type[:_]([a-z]+)$", counter)
    if m:
        color = m.group(1)
        if color not in TYPES:
            # 属性拼错会让币数静默变 0（整招归零）—— 必须抛错
            raise ValueError(f"未知宝可梦属性 {color}（{counter}）")
        return sum(1 for fp in p.all_field() if str(fp.pdef.ptype) == color)
    fn = _COUNTERS.get(counter)
    if fn is not None:
        p = battle.players[battle.current]
        o = battle.players[1 - battle.current]
        return int(fn(battle, p, o))
    m = _ENERGY_COUNTER_RE.match(counter)
    if m:
        zone, color = m.group(1), m.group(2)
        p = battle.players[battle.current]
        o = battle.players[1 - battle.current]
        fps = _ENERGY_ZONES[zone](battle, p, o)
        # 带颜色时用 `energy_of_type`：特殊能量的「双色任选 / 任意属性」也能
        # 支付该色，按 `energy_of` 精确相等会系统性少算（卡面「for each {W} Energy」）
        return sum(fp.total_energy() if not color else fp.energy_of_type(color)
                   for fp in fps)
    raise ValueError(
        f"未知计数源 {counter}（可用：{'、'.join(sorted(_COUNTERS))} 或 "
        f"<zone>_energy[_<color>]，zone ∈ {'/'.join(_ENERGY_ZONES)}）")


def _eval_condition(battle, cond: str) -> bool:
    """条件求值（未知 → 抛错，禁止静默 False）

    **带参数条件**（2026-09-22 新增）：条件名后可接参数，用冒号分隔 ——
      ``own_active_energy_type:<color>``   自身战斗位有该色能量（≥1 个）
      ``own_active_energy_at_least:<n>``   自身战斗位能量总数 ≥ n
    对应卡面「If this Pokémon has any {L} Energy attached, this attack does 80
    more damage」一类 —— 引擎此前的条件是无参白名单，这类只能落进未建模。
    """
    p = battle.players[battle.current]
    o = battle.players[1 - battle.current]
    # ⑦ 取反（2026-10-28）：「If you **don't** have … this attack does nothing」
    if cond.startswith("not:"):
        return not _eval_condition(battle, cond[4:])
    fn = _CONDITIONS.get(cond)
    if fn is not None:
        return bool(fn(battle, p, o))
    if cond.startswith("own_active_energy_type:"):
        color = cond.split(":", 1)[1]
        return _eval_counter(battle, f"own_active_energy_{color}") >= 1
    if cond.startswith("own_active_energy_at_least:"):
        try:
            n = int(cond.split(":", 1)[1])
        except ValueError as e:                              # noqa: F841
            raise ValueError(f"条件参数非整数：{cond}") from None
        return _eval_counter(battle, "own_active_energy") >= n
    # ---- 第四批带参条件（2026-10-26）----
    name, _, arg = cond.partition(":")
    # ---- 第六批带参条件（2026-10-28）----
    if name == "own_active_energy_tag":
        # 「If this Pokémon has any Team Rocket's Energy attached」
        want = "senergy" + re.sub(r"[^a-z0-9]", "", arg.lower())
        return p.active is not None and any(
            re.sub(r"[^a-z0-9]", "", str(ref.edef.tag)) == want
            for ref in p.active.senergy)
    if name == "own_field_name":
        # 「if you have **Solrock** in play」（第一百一十一批，Lunatone
        # 「Lunar Cycle」）—— 与 `own_bench_name`（只看后备）不同：这里是
        # 「in play」= 战斗位 + 后备。用**子串**匹配卡名归一化键，与
        # `_search_bench` 的 `name_has` 同口径（"Solrock" 也命中 "Solrock ex"）。
        key = arg.strip().lower()
        if not key:
            raise ValueError(f"条件缺参数：{cond}")
        return any(key in _name_key(fp.pdef.name) for fp in p.all_field())
    if name == "own_discard_basic_energy_at_least":
        color, _, raw_n = arg.partition(":")
        if not raw_n.isdigit():
            raise ValueError(f"条件参数非整数：{cond}")
        return _eval_counter(battle, f"own_discard_energy_{color}") >= int(raw_n)
    if name == "opp_prizes_le":
        # 卡面「You can use this card only if your opponent has N or fewer Prize
        # cards remaining.」（第四十六批，Acerola's Mischief / Briar）
        # ⚠ 是**剩余**奖赏（`prizes_left`），不是已取得数。
        if not arg.isdigit():
            raise ValueError(f"条件参数非整数：{cond}")
        return o.prizes_left <= int(arg)
    if name == "opp_prizes_exactly":
        # 「You can use this card only if your opponent has **exactly** N Prize
        # cards remaining.」（第五十九批，Briar）⚠ 与上面的 `le` 差一个词：
        # 写成 `le` 会把「奖赏已少于 N」的残局也放进来（门槛静默放宽）。
        if not arg.isdigit():
            raise ValueError(f"条件参数非整数：{cond}")
        return o.prizes_left == int(arg)
    if name == "own_bench_stage2_type":
        return any(fp.pdef.stage == 2 and str(fp.pdef.ptype) == arg
                   for fp in p.bench)
    if name == "own_active_status_any":
        want = {x.strip() for x in arg.split(",") if x.strip()}
        return p.active is not None and bool(set(p.active.status) & want)
    if name == "own_discard_has_named":
        # 「If Rosa's Encouragement is in your discard pile」
        return _eval_counter(battle, f"own_discard_named:{arg}") >= 1
    # ---- 第七批带参条件（2026-10-28）----
    if name == "evolved_from":
        # 「If this Pokémon evolved from Gimmighoul during this turn」
        return (p.active is not None
                and p.active.evolved_turn == battle.turn_number
                and _name_key(p.active.evolved_from_name) == arg)
    if name == "used_attack_last_turn":
        return any(_name_key(x) == arg for x in p.last_turn_attacks)
    if name == "own_bench_at_most":
        if not arg.isdigit():
            raise ValueError(f"条件参数非整数：{cond}")
        return len(p.bench) <= int(arg)
    if name == "own_hand_exactly":
        if not arg.isdigit():
            raise ValueError(f"条件参数非整数：{cond}")
        return len(p.hand) == int(arg)
    # 「If you can't draw any cards in this way, you can't use this card.」
    # 等价于「打出时手牌数 < N」（抽到 N 张；若已 ≥ N 则一张都抽不到）。
    if name == "own_hand_at_most":
        if not arg.isdigit():
            raise ValueError(f"条件参数非整数：{cond}")
        return len(p.hand) <= int(arg)
    if name == "opp_prizes_in":
        want = {int(x) for x in arg.split(",") if x.strip().isdigit()}
        if not want:
            raise ValueError(f"条件参数非法：{cond}")
        return o.prizes_left in want
    if name == "own_bench_named_all":
        keys = [k for k in arg.split("+") if k]
        if not keys:
            raise ValueError(f"条件参数非法：{cond}")
        have = [_name_key(fp.pdef.name) for fp in p.bench]
        return all(any(k in h for h in have) for k in keys)
    # ---- 第九十三批带参条件（2026-11-05）----
    if name == "own_bench_named_has_damage":
        # 「If any of your Benched **Pancham** have any damage counters on them,
        #  this attack does 120 more damage.」（Pangoro ASC 140）
        # 数的是**只数**且限定卡名子串。不能复用 `own_bench_has_damage`
        # （任意后备有伤）——后备上坐着别的受伤宝可梦时会**凭空加伤**。
        if not arg:
            raise ValueError(f"条件参数非法：{cond}")
        return any(fp.damage > 0 and arg in _name_key(fp.pdef.name)
                   for fp in p.bench)
    if name == "played_supporter_named":
        # 「If you played a Supporter card that has “Team Rocket” in its name from
        #  your hand during this turn, this attack does 100 more damage.」
        # （Team Rocket's Kangaskhan ex ASC 162）
        # `supporter_name` 在打出支援者时写入、**回合开始清空**（battle.py:252），
        # 因此天然就是「本回合」语义；空串 = 本回合没打支援者。
        if not arg:
            raise ValueError(f"条件参数非法：{cond}")
        return bool(p.supporter_name) and arg in _name_key(p.supporter_name)
    if name == "opp_active_status":
        # 参数用**标准状态名**；非标准名直接抛错（拼错就等于静默不生效）
        from .rules import STATUSES as _STATUSES
        if arg not in _STATUSES:
            raise ValueError(f"{cond}：未知特殊状态 {arg!r}（可用：{'/'.join(_STATUSES)}）")
        st = set(o.active.status) if o.active is not None else set()
        # 兼容历史拼写：`paralyzed` 与标准 `paralyze` 视为同一状态
        if arg == "paralyze":
            return bool(st & {"paralyze", "paralyzed"})
        return arg in st
    if name == "opp_active_type":
        if o.active is None:
            return False
        if not arg:
            raise ValueError(f"{cond}：缺少属性参数")
        return str(o.active.pdef.ptype).lower() == arg.lower()
    if name in ("opp_bench_at_least", "own_all_energy_at_least"):
        try:
            n = int(arg)
        except (TypeError, ValueError):
            raise ValueError(f"条件参数非整数：{cond}") from None
        if name == "opp_bench_at_least":
            return len(o.bench) >= n
        return _eval_counter(battle, "own_all_energy") >= n
    # ---- 第五批带参条件（2026-10-27）----
    if name == "opp_hand_exactly":
        # 「If your opponent has exactly 4 cards in their hand」（第一百二十批，
        # Decidueye ex「Sniper's Eye」）。⚠ 只有 `_at_most` 不够：卡面是**恰好**。
        if not arg.isdigit():
            raise ValueError(f"条件参数非整数：{cond}")
        return len(o.hand) == int(arg)
    if name == "opp_discard_has_named":
        # 「If your opponent has any cards in their discard pile that have
        #  "Colress" in the name」（第一百一十二批，Kyurem「Plasma Bane」）
        # —— 与 `own_discard_has_named` 同口径，只是换一侧。
        return _eval_counter(battle, f"opp_discard_named:{arg}") >= 1
    if name == "own_field_name_at_least":
        # 「unless you have 4 or more Team Rocket's Pokémon in play」
        # （第一百二十批，Team Rocket's Mewtwo ex「Power Saver」）
        key, _, raw = arg.partition(":")
        if not raw.isdigit():
            raise ValueError(f"条件参数非整数：{cond}")
        return sum(1 for fp in p.all_field()
                   if key in _name_key(fp.pdef.name)) >= int(raw)
    if name in ("opp_hand_at_most", "opp_prizes_at_most", "own_prizes_exactly",
                "opp_active_is_stage", "own_active_hp_at_most",
                "own_deck_at_most"):
        try:
            n = int(arg)
        except (TypeError, ValueError):
            raise ValueError(f"条件参数非整数：{cond}") from None
        if name == "opp_hand_at_most":
            return len(o.hand) <= n
        if name == "opp_prizes_at_most":
            return o.prizes_left <= n
        if name == "own_prizes_exactly":
            return p.prizes_left == n
        if name == "opp_active_is_stage":
            return o.active is not None and o.active.pdef.stage == n
        if name == "own_deck_at_most":
            # Wo-Chien「If there are 3 or fewer cards in your deck」
            return len(p.deck) <= n
        # own_active_hp_at_most
        return p.active is not None and p.active.hp_remaining <= n
    if name == "own_active_status":
        from .rules import STATUSES as _STATUSES
        if arg not in _STATUSES:
            raise ValueError(f"{cond}：未知特殊状态 {arg!r}（可用：{'/'.join(_STATUSES)}）")
        st = set(p.active.status) if p.active is not None else set()
        if arg == "paralyze":
            return bool(st & {"paralyze", "paralyzed"})
        return arg in st
    if name in ("own_active_energy_type_at_least", "own_all_energy_type_at_least"):
        color, _, raw = arg.partition(":")
        if not color:
            raise ValueError(f"{cond}：缺少属性参数")
        try:
            n = int(raw)
        except (TypeError, ValueError):
            raise ValueError(f"条件参数非整数：{cond}") from None
        zone = "own_active_energy" if name.startswith("own_active") \
            else "own_all_energy"
        return _eval_counter(battle, f"{zone}_{color}") >= n
    if name == "own_active_extra_energy_at_least":
        # 「If this Pokémon has at least N extra Energy attached (**in addition
        # to this attack's cost**), this attack does M more damage.」
        # 正在结算的招式由 `_do_attack` 临时写入 `_resolving_attack`。
        try:
            n = int(arg)
        except (TypeError, ValueError):
            raise ValueError(f"条件参数非整数：{cond}") from None
        atk = getattr(battle, "_resolving_attack", None)
        cost = int(getattr(atk, "cost_total", 0) or 0) if atk is not None else 0
        if p.active is None:
            return False
        return p.active.total_energy() - cost >= n
    if name == "opp_active_resistance":
        if o.active is None:
            return False
        if not arg:
            raise ValueError(f"{cond}：缺少属性参数")
        return str(o.active.pdef.resistance or "").lower() == arg.lower()
    if name == "own_bench_name":
        key = _name_key(arg)
        if not key:
            raise ValueError(f"{cond}：缺少卡名参数")
        # 卡面「a Pokémon that has "Nidoking" in its name」是**子串**语义 →
        # 归一化后按 in 判定（"Team Rocket's Nidoking" 也应命中）
        return any(key in _name_key(fp.pdef.name) for fp in p.bench)
    # ---- B3（2026-10-28）：抽牌完整性 ----
    if name == "own_hand_at_least":
        try:
            n = int(arg)
        except (TypeError, ValueError):
            raise ValueError(f"条件参数非整数：{cond}") from None
        return len(p.hand) >= n
    if name == "own_all_name":
        key = _name_key(arg)
        if not key:
            raise ValueError(f"{cond}：缺少卡名参数")
        # 「If all of your Pokémon in play are Team Rocket's Pokémon」（Ariana）
        field = p.all_field()
        return bool(field) and all(key in _name_key(fp.pdef.name) for fp in field)
    raise ValueError(
        f"未知条件 {cond}（可用：{'、'.join(sorted(_CONDITIONS))}；"
        f"带参形式：own_active_energy_type:<color> / "
        f"own_active_energy_at_least:<n> / "
        f"{' / '.join(_CONDITION_PARAMS)}）")


def _drop_tool(fp: FieldPokemon) -> Optional[str]:
    """卸下并"丢弃"道具（触发后自弃，如 Deluxe Bomb / Survival Brace）"""
    name = fp.tool_name
    fp.tool = None
    fp.tool_name = ""
    fp.tool_ref = None
    return name


def _static_mods(fp: Optional[FieldPokemon],
                 attacker: Optional[FieldPokemon] = None) -> dict:
    """静态修正层（S1）—— 从**卡面常驻被动**读出减伤 / 无弱点 / 无撤退

    支持的 tag（写在特性上，`tag` 字段）：
      ``passive:dmg_reduce:N``  受到的招式伤害 −N（在弱点/抗性之后结算，与卡面一致）
      ``passive:dmg_reduce:N:from:type:<t1,t2>``
                                仅来自指定属性攻击方时 −N（攻击方未知时不生效）
      ``passive:no_weakness``   弱点不生效
      ``passive:no_retreat``    撤退费用视为 0
      ``passive:hp_plus:N``     上限 +N（在 `make_pokemon` 落盘，见 FieldPokemon.bonus_hp）

    ⚠ **只认"无条件/属性来源"形态**：带其它条件的静态修正（「若有 {M} 能量则…」/
    「你的其它 Bouffalant 在场时…」/「在对手的下个回合内…」）一律不映射 —— 这类
    条件要么需要跨实例统计、要么是时限制（应走 `shield:*`），在解析层就被拒绝，
    不会走到这里。这样做的代价是覆盖少，收益是**不会出现"有时生效有时不生效"的
    静默错算**。
    """
    out = {"dmg_reduce": 0, "no_weakness": False, "no_retreat": False}
    if fp is None:
        return out
    for ab in fp.pdef.abilities:
        tag = str(getattr(ab, "tag", "") or "")
        if not tag.startswith("passive:"):
            continue
        parts = tag.split(":")
        kind = parts[1] if len(parts) > 1 else ""
        if kind == "dmg_reduce" and len(parts) > 2 and parts[2].isdigit():
            reduce = int(parts[2])
            i = 3
            while i + 1 < len(parts):
                if parts[i] == "from" and parts[i + 1] == "type" \
                        and i + 2 < len(parts):
                    types = set(parts[i + 2].split(","))
                    if attacker is None or attacker.pdef.ptype not in types:
                        reduce = 0
                    i += 3
                else:
                    raise ValueError(f"未识别的 passive:dmg_reduce 限定：{tag}")
            out["dmg_reduce"] += reduce
        elif kind == "no_weakness":
            out["no_weakness"] = True
        elif kind == "no_retreat":
            out["no_retreat"] = True
    # 道具的 `dmg_reduce` 需要**按攻击方**判定（可能带 from/holder 限定），
    # 不能在 `_static_mods` 里无条件累加 —— 见 `Battle._tool_reduce_vs`。
    # 这里只处理与攻击方无关的两类。
    tool = fp.tool
    if tool:
        tparts = str(tool).split(":")
        tkind = tparts[1] if len(tparts) > 1 else ""
        if tkind == "no_weakness":
            out["no_weakness"] = True
        elif tkind == "no_retreat":
            out["no_retreat"] = True
    return out


def _tool_meta(tag: Optional[str]) -> dict:
    """``tool:<kind>:<n>[:from:…][:holder:…][:target:…][:discard]`` → 结构化字段

    限定语法的唯一权威定义（解析层只负责拼串，语义解释在这里）：
      · ``from:type:a,b``  攻击方属性属于集合才生效
      · ``from:ability``   攻击方**有特性**才生效
      · ``from:name:<k>``  攻击方卡名（归一化前缀）匹配才生效
      · ``from:status:<s>`` 攻击方处于该特殊状态才生效
      · ``from:faction:<k>`` 攻击方卡名属于该阵营（前缀）才生效
      · ``holder:…``       同上，但判定对象是**道具持有者**
      · ``target:ex`` / ``target:basic``  只对指定形态的**受击方**生效
                           （Maximum Belt / Light Ball 的「{ex}」）
      · ``discard``        生效后弃置本道具（一次性道具）
    """
    out = {"kind": "", "n": 0, "from": None, "holder": None,
           "target": None, "discard": False}
    if not tag:
        return out
    parts = str(tag).split(":")
    out["kind"] = parts[1] if len(parts) > 1 else ""
    out["n"] = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    i = 3
    while i < len(parts):
        tok = parts[i]
        if tok == "from":
            if i + 2 < len(parts) and parts[i + 1] == "type":
                out["from"] = ("type", parts[i + 2].split(","))
                i += 3
            elif i + 2 < len(parts) and parts[i + 1] in _QUAL_ARGS:
                out["from"] = (parts[i + 1], [parts[i + 2]])
                i += 3
            elif i + 1 < len(parts) and parts[i + 1] == "ability":
                out["from"] = ("ability", None)
                i += 2
            else:
                # ⚠ 出现了 from 但形态不认识 → 标记 unknown，**必须不生效**。
                #   若这里直接忽略，该道具会退化成"无条件减伤"（静默加强）。
                out["from"] = ("unknown", None)
                i += 1
        elif tok == "holder":
            if i + 2 < len(parts) and parts[i + 1] == "type":
                out["holder"] = ("type", parts[i + 2].split(","))
                i += 3
            elif i + 2 < len(parts) and parts[i + 1] in _QUAL_ARGS:
                out["holder"] = (parts[i + 1], [parts[i + 2]])
                i += 3
            elif i + 1 < len(parts) and parts[i + 1] == "no_rule_box":
                # Brave Bangle：「doesn't have a Rule Box」的持有者
                out["holder"] = ("no_rule_box", None)
                i += 2
            else:
                out["holder"] = ("unknown", None)
                i += 1
        elif tok == "target":
            if i + 1 < len(parts) and parts[i + 1] in ("ex", "basic"):
                out["target"] = (parts[i + 1], None)
                i += 2
            else:
                out["target"] = ("unknown", None)
                i += 1
        elif tok in ("discard", "once"):
            out["discard"] = True
            i += 1
        else:
            i += 1
    return out


_QUAL_ARGS = ("name", "status", "faction")


def _norm_ex_key(fp) -> str:
    """卡名归一化键去掉尾部 `ex`（「Pecharunt {ex}」→ `pecharunt`）

    卡面写 `{ex}` 时它属于**规则框**而非名字，解析侧产出的键因此不带 `ex`；
    而引擎里卡名本身就是「Pecharunt ex」。凡是要跟解析侧键对齐的地方都用这个。
    """
    k = _name_key(fp.pdef.name)
    return k[:-2] if k.endswith("ex") else k


def _qual_ok(qual, fp: Optional[FieldPokemon]) -> bool:
    """限定条件是否成立（None = 无限定 → 成立；未知形态 → **不成立**，宁可不生效）"""
    if not qual:
        return True
    if fp is None:
        return False
    kind, val = qual
    if kind == "type":
        return str(fp.pdef.ptype).lower() in [v.strip().lower() for v in val]
    if kind == "ability":
        return bool(fp.pdef.abilities)
    if kind == "name":
        return _name_key(fp.pdef.name).startswith(str(val[0]).lower())
    if kind == "status":
        return str(val[0]).lower() in fp.status
    if kind == "faction":
        return _name_key(fp.pdef.name).startswith(str(val[0]).lower())
    if kind == "no_rule_box":
        # 本卡池的「规则框」只有 ex / Mega ex 两种，按 is_ex 精确判定
        return not bool(getattr(fp.pdef, "is_ex", False))
    return False


def _target_ok(qual, fp: Optional[FieldPokemon]) -> bool:
    """``target:<kind>`` 限定（判定**受击方**；未知形态 → 不生效）"""
    if not qual:
        return True
    if fp is None:
        return False
    kind, _ = qual
    if kind == "ex":
        return bool(getattr(fp.pdef, "is_ex", False))
    if kind == "basic":
        return fp.pdef.stage == 0
    return False


def _tool_dmg_plus(attacker: Optional[FieldPokemon],
                   defender: Optional[FieldPokemon]) -> int:
    """道具提供的**静态加伤**（``tool:dmg_plus:N[:from:…][:holder:…][:target:…]``）

    卡面原型：
      · Maximum Belt「Attacks used by the Pokémon this card is attached to do
        50 more damage to your opponent's Active Pokémon {ex}」→ ``target:ex``
      · Light Ball「… the Pikachu {ex} this card is attached to … {ex}」
        → ``target:ex:holder:name:pikachu``
      · Binding Mochi「Attacks used by the **Poisoned** Pokémon this card is
        attached to do 40 more damage …」→ ``holder:status:poison``

    与 `families.static_dmg_plus` 同口径：只在招式**本身有基础伤害**时生效
    （调用点已判定），且加伤发生在弱点/抗性之前。
    """
    tag = _tool_tag(attacker)
    if not tag.startswith("tool:dmg_plus:"):
        return 0
    meta = _tool_meta(tag)
    if not meta["n"]:
        return 0
    if not _qual_ok(meta["holder"], attacker):
        return 0
    if not _qual_ok(meta["from"], attacker):
        return 0
    if not _target_ok(meta["target"], defender):
        return 0
    return meta["n"]


def _cheaper_cost(cost, n: int):
    """费用减少 n：先扣**无色**需求，再按彩色需求顺序扣（不足则减到 0）"""
    if n <= 0:
        return tuple(cost)
    items = [[t, c] for t, c in cost]
    remain = n
    for it in items:
        if it[0] == COLORLESS and remain:
            take = min(it[1], remain)
            it[1] -= take
            remain -= take
    if remain:
        for it in items:
            if it[0] != COLORLESS and it[1] and remain:
                take = min(it[1], remain)
                it[1] -= take
                remain -= take
    return tuple((t, c) for t, c in items if c > 0)


def _attack_cost_delta(battle, fp) -> int:
    """道具提供的招式费用**减免**（S6 2026-10-28：Sparkling Crystal / Counter Gain）"""
    tag = _tool_tag(fp)
    if not tag.startswith("tool:cost_less:"):
        return 0
    parts = tag.split(":")
    n = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    cond = parts[3] if len(parts) > 3 else ""
    if cond == "tera":
        # Tera 是数据集 `category`（形如 "Tera(Fire)"），引擎可精确判定
        return n if str(fp.pdef.category).startswith("Tera") else 0
    if cond == "behind" and battle is not None:
        owner = next((i for i, pl in enumerate(battle.players)
                      if any(x is fp for x in pl.all_field())), None)
        if owner is None:
            return 0
        me, other = battle.players[owner], battle.players[1 - owner]
        return n if me.prizes_left > other.prizes_left else 0
    return 0


def _retreat_cost(fp: Optional[FieldPokemon], battle=None) -> int:
    """撤退费用（含 `passive:no_retreat`、道具修正与效果族覆盖，下限 0）"""
    if fp is None:
        return 0
    over = families.retreat_cost(battle, fp) if battle is not None else None
    if over is not None:
        return max(0, int(over))
    if _static_mods(fp)["no_retreat"]:
        return 0
    tag = _tool_tag(fp)
    # 「剩余 HP ≤M 时它没有撤退费用」（Rescue Board）
    m = re.match(r"^tool:retreat_less:\d+:lowhp:(\d+)$", tag)
    if m and fp.hp_remaining <= int(m.group(1)):
        return 0
    base = int(fp.pdef.retreat) - _tool_value(_tool_tag(fp), "retreat_less")
    # 「附着者在战斗位时，双方战斗位撤退费用 +N」（Gravity Gemstone）
    if battle is not None:
        for pl in battle.players:
            act_fp = pl.active
            if act_fp is not None and _tool_tag(act_fp).startswith(
                    "tool:retreat_plus:"):
                base += _tool_value(_tool_tag(act_fp), "retreat_plus")
    # N's Castle：阵营宝可梦无撤退费用（2026-10-28）
    if battle is not None:
        m = battle._stadium_meta()
        if m["kind"] == "no_retreat" and m["faction"] \
                and _name_key(fp.name).startswith(m["faction"]):
            return 0
    # 「During your opponent's next turn, … its Retreat Cost is {C} more.」
    # （Rillaboom「Drum Beating」）—— 按**持有者**的回合计数到期
    if battle is not None and fp.retreat_up_n:
        owner = next((i for i, pl in enumerate(battle.players)
                      if pl.active is fp), None)
        if owner is not None and fp.retreat_up_until > battle.turn_counts[owner]:
            base += fp.retreat_up_n
    return max(0, base)


def _tool_tag(fp: Optional[FieldPokemon]) -> str:
    """道具 tag；若道具**失效**（Jamming Tower）则返回空串

    只影响"道具提供的修正/触发"，不影响"是否附着道具"这类存在性判定
    （道具卡仍在身上，弃道具/计数类效果照常看得到它）。
    """
    if fp is None or bool(getattr(fp, "tool_disabled", False)):
        return ""
    return str(getattr(fp, "tool", "") or "")


def _status_immune(fp: Optional[FieldPokemon]) -> bool:
    """化石卡：不受任何特殊状态影响（卡面明文「can't be affected by any
    Special Conditions」）。攻击/特性两条状态施加路径都调用这里。"""
    return fp is not None and bool(getattr(fp, "fossil", False))


def _is_bench_immune(fp: Optional[FieldPokemon]) -> bool:
    """被动后备免伤：在后备时免疫招式**伤害**

    卡面原型：
      · 「As long as this Pokémon is on your Bench, prevent all damage from and
        effects of attacks ... done to this Pokémon」→ ``passive:bench_immune``
      · Tera 规则「… prevent all damage done to this Pokémon by attacks」
        → ``passive:bench_immune_dmg``（**只挡伤害**）

    位于战斗位时**不生效**。
    """
    return any(ab.tag in ("passive:bench_immune", "passive:bench_immune_dmg")
               for ab in (fp.pdef.abilities if fp else ()))


def _is_bench_effect_immune(fp: Optional[FieldPokemon]) -> bool:
    """被动后备**效果**免疫：只有卡面写明「from and effects of attacks」的才成立"""
    return any(ab.tag == "passive:bench_immune"
               for ab in (fp.pdef.abilities if fp else ()))


# ---------- 静态免伤（2026-10-28 第八批） ----------
def _imm_meta(tag: str) -> Optional[dict]:
    """``passive:dmg_immune:<scope>[:from:<src>|:min:<n>|:no_rule_box][:effects]``

    scope：``self``（只护持有者）| ``team_bench``（护己方全部后备）。
    来源限定只解释**可精确判定**的四种：basic_ex / ex / tera / has_ability /
    special_energy；``min:<n>`` 是伤害门槛；``no_rule_box`` 只作用在 team_bench。
    """
    if not str(tag or "").startswith("passive:dmg_immune:"):
        return None
    parts = str(tag).split(":")
    out = {"scope": parts[2] if len(parts) > 2 else "",
           "src": None, "min": None, "no_rule_box": False, "effects": False}
    i = 3
    while i < len(parts):
        tok = parts[i]
        if tok == "from" and i + 1 < len(parts):
            out["src"] = parts[i + 1]
            i += 2
        elif tok == "min" and i + 1 < len(parts) and parts[i + 1].isdigit():
            out["min"] = int(parts[i + 1])
            i += 2
        elif tok == "no_rule_box":
            out["no_rule_box"] = True
            i += 1
        elif tok == "effects":
            out["effects"] = True
            i += 1
        else:
            raise ValueError(f"未识别的 passive:dmg_immune 限定：{tag}")
    if out["scope"] not in ("self", "team_bench"):
        raise ValueError(f"未识别的 passive:dmg_immune 范围：{tag}")
    return out


def _src_matches(src: Optional[str], attacker: Optional[FieldPokemon]) -> bool:
    """来源限定是否命中**攻击方**（未知攻击方 → 不命中，绝不凭空扩大免疫）"""
    if not src:
        return True
    if attacker is None:
        return False
    pdef = attacker.pdef
    if src == "basic_ex":
        return pdef.stage == 0 and bool(pdef.is_ex)
    if src == "ex":
        return bool(pdef.is_ex)
    if src == "tera":
        return str(pdef.category or "").startswith("Tera")
    if src == "has_ability":
        return bool(pdef.abilities)
    if src == "special_energy":
        return bool(attacker.senergy)
    if src == "basic":
        return pdef.stage == 0
    if src == "basic_non_colorless":
        return pdef.stage == 0 and pdef.ptype != "colorless"
    raise ValueError(f"未识别的静态免伤来源：{src}")


def _imm_hit(meta: dict, attacker: Optional[FieldPokemon],
             dmg: Optional[int]) -> bool:
    """一条 dmg_immune 记录是否对本次（攻击方 / 伤害）成立"""
    if meta["min"] is not None:
        # 伤害门槛只能在**已知伤害**时判定；判定不了就不免（不静默扩大）
        if dmg is None or dmg < meta["min"]:
            return False
    return _src_matches(meta["src"], attacker)


def _self_immune(fp: Optional[FieldPokemon], attacker: Optional[FieldPokemon],
                 dmg: Optional[int], effects_only: bool = False) -> Optional[str]:
    """目标**自身**的静态免伤 → 命中时返回原因串（供日志），否则 ``None``

    ``effects_only``：只关心「是否连效果一起挡」，供 `_opp_blocked` 使用。
    """
    if fp is None:
        return None
    for ab in fp.pdef.abilities or ():
        meta = _imm_meta(str(getattr(ab, "tag", "") or ""))
        if not meta or meta["scope"] != "self":
            continue
        if effects_only and not meta["effects"]:
            continue
        if _imm_hit(meta, attacker, dmg):
            return f"dmg_immune:{ab.tag}"
    return None


def _team_bench_immune(owner, fp: Optional[FieldPokemon],
                       attacker: Optional[FieldPokemon], dmg: Optional[int],
                       effects_only: bool = False) -> Optional[str]:
    """己方场上**任意**宝可梦持有的 ``team_bench`` 免伤 → 是否覆盖 ``fp``

    Rabsca「Prevent all damage from and effects of attacks … done to your
    Benched Pokémon」/ Shaymin「… to your Benched Pokémon that don't have a
    Rule Box」。只对**后备**生效；``no_rule_box`` 要求目标本身没有规则框
    （本卡池只有 ex / Mega ex 两种规则框，用 ``is_ex`` 精确判定）。
    """
    if owner is None or fp is None or fp is owner.active:
        return None
    for holder in owner.all_field():
        if holder is None:
            continue
        for ab in holder.pdef.abilities or ():
            meta = _imm_meta(str(getattr(ab, "tag", "") or ""))
            if not meta or meta["scope"] != "team_bench":
                continue
            if effects_only and not meta["effects"]:
                continue
            if meta["no_rule_box"] and fp.pdef.is_ex:
                continue
            if _imm_hit(meta, attacker, dmg):
                return f"dmg_immune:{ab.tag}"
    return None


def _target_dmg_blocked(battle, owner, fp: Optional[FieldPokemon],
                        attacker: Optional[FieldPokemon], dmg: int,
                        on_bench: bool = False) -> bool:
    """招式对某目标的**伤害**是否被完全挡下（后备/现役通用）

    覆盖：护盾（含来源/门槛限定）、效果免疫族、特殊能量效果免疫、
    后备被动免伤、自身静态免伤、全队后备免伤。
    """
    if fp is None:
        return True
    if fp.shield_prevent_all and _shield_blocks(fp, attacker, dmg):
        return True
    if families.effect_immune(battle, fp, "attack"):
        return True
    if special_energy.effect_immune(fp, "attack"):
        return True
    if on_bench and _is_bench_immune(fp):
        return True
    if _self_immune(fp, attacker, dmg):
        return True
    if on_bench and _team_bench_immune(owner, fp, attacker, dmg):
        return True
    # Battle Cage：后备不受对手效果放置的伤害指示物（招式伤害照常）
    if on_bench and battle is not None and battle._no_bench_counters():
        return True
    return False


def _shield_tag_parts(rest: list) -> tuple[str, bool]:
    """``shield:prevent_all`` 的后缀 token → ``(qual, effects)``

    后缀形态：``from:<src>`` · ``if_damage:le:<n>`` · ``effects``（可组合）。
    """
    qual: list = []
    effects = False
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok == "effects":
            effects = True
            i += 1
        elif tok == "from" and i + 1 < len(rest):
            qual += ["from", rest[i + 1]]
            i += 2
        elif tok == "if_damage" and i + 2 < len(rest):
            qual += ["if_damage", rest[i + 1], rest[i + 2]]
            i += 3
        else:
            raise ValueError(f"未识别的 shield:prevent_all 限定：{rest}")
    return ":".join(qual), effects


def _shield_blocks(fp: Optional[FieldPokemon], attacker: Optional[FieldPokemon],
                   dmg: Optional[int]) -> bool:
    """护盾是否挡下本次伤害（按卡面来源/门槛限定判）"""
    if fp is None or not fp.shield_prevent_all:
        return False
    qual = str(getattr(fp, "shield_prevent_qual", "") or "")
    if not qual:
        return True
    parts = qual.split(":")
    if parts[0] == "from" and len(parts) > 1:
        return _src_matches(parts[1], attacker)
    if parts[0] == "if_damage" and len(parts) > 2 and parts[1] == "le":
        return dmg is not None and dmg <= int(parts[2])
    raise ValueError(f"未识别的护盾限定：{qual}")


def _parse_shield_choose_tag(tag: str) -> dict:
    """``shield_choose:prevent_all[:from:<src>][:effects][:gate:<cond>]`` → dict

    `:gate:` 必须**先**切掉：条件本身带冒号（`opp_prizes_le:2`），
    留在 `_shield_tag_parts` 里会被当成未知限定。
    """
    body = str(tag)[len("shield_choose:"):]
    gate = None
    if ":gate:" in body:
        body, gate = body.split(":gate:", 1)
    parts = body.split(":")
    if not parts or parts[0] != "prevent_all":
        raise ValueError(f"shield_choose 形态非法：{tag}")
    qual, effects = _shield_tag_parts(parts[1:])
    return {"qual": qual, "effects": effects, "gate": gate}


def _shield_blocks_effects(fp: Optional[FieldPokemon],
                           attacker: Optional[FieldPokemon]) -> bool:
    """护盾是否挡**效果**：`from:<src>` 限定同样作用于效果（Acerola 只挡 {ex}）

    `if_damage` 限定沿用历史口径（效果不看伤害门槛）。
    """
    if fp is None or not (fp.shield_prevent_all and fp.shield_prevent_effects):
        return False
    qual = str(getattr(fp, "shield_prevent_qual", "") or "")
    if not qual:
        return True
    if qual.startswith("from:"):
        return _shield_blocks(fp, attacker, None)
    return True


def _has_passive(fp: Optional[FieldPokemon], tag: str) -> bool:
    if fp is None:
        return False
    return any(ab.tag == tag for ab in fp.pdef.abilities)


def _card_label(c) -> str:
    if isinstance(c, PokeRef):
        return c.pdef.name
    if isinstance(c, EnergyRef):
        return c.edef.name
    if isinstance(c, TrainerRef):
        return c.tdef.name
    return str(c)


def _field_iter(p: PlayerState):
    """返回 [(0, active), (1..n, bench_i-1)] 位置迭代（bench 用 1-based 目标编码）"""
    if p.active is not None:
        yield 0, p.active
    for bi, fp in enumerate(p.bench):
        yield bi + 1, fp


def _field_at(p: PlayerState, tpos: int) -> Optional[FieldPokemon]:
    if tpos == 0:
        return p.active
    bi = tpos - 1
    return p.bench[bi] if 0 <= bi < len(p.bench) else None
