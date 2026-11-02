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
from typing import Optional
from .cards import (PokeRef, EnergyRef, EnergyDef, TrainerRef, FieldPokemon,
                    make_pokemon, _tool_value, COLORLESS, TYPES)
from .state import GameConfig, PlayerState
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
        self._setup_openings()
        # 先手：硬币
        self.current = 0 if self.rng.random() < 0.5 else 1
        self._log("setup", {"first": self.current, "seed": seed,
                            "mulligan": [p.mulligan_count for p in self.players]})
        self._begin_turn()

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
        if len(p.hand) > self.cfg.max_hand:
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
        p.attached_energy_this_turn = 0
        p.supporter_used = 0
        p.supporter_name = ""                     # S3：在场地上判定的支援者卡名
        p.discarded_this_turn = 0                 # v1.4「in this way」按回合重置
        p.discarded_energy_this_turn = 0
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
        # v1.4「上回合被击倒」标记：该方回合开始即清零（卡面为「对手的上个回合」）
        self._ko_flags()[self.current] = 0
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
        # 回合结束检查：麻痹只在本回合结束才恢复（见 rules.status_checkup 的 phase 说明）。
        # 放在切换 current **之前**，作用于「刚结束回合的这一方」。
        cur = self.players[self.current]
        if cur.active is not None and not cur.active.is_ko:
            for ev in status_checkup(cur.active, self.rng, phase="end"):
                self._log("checkup", {**ev, "player": self.current})
        # 2026-10-28：Ignition Energy「discard it at the end of your turn」
        special_energy.discard_at_end_of_turn(self, cur)
        self._log("turn_end", {"player": self.current})
        self.current = 1 - self.current
        self._begin_turn()

    # ---------- 合法动作 ----------
    def legal_actions(self, pi: Optional[int] = None) -> list:
        pi = self.current if pi is None else pi
        if self.finished or pi != self.current or self.phase != "main":
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
            turn_count = self.turn_counts[pi]
            if not blocked:
                for ai, atk in enumerate(p.active.pdef.attacks):
                    # 跨回合攻击封锁（`attack_lock:*`）：即使能量足够也不得出手
                    if p.active.attack_locked(atk.name, turn_count):
                        continue
                    _scope = _borrow_scope(atk)
                    _opts = self._borrow_options(p, opp, p.active, atk)
                    if _scope is not None:
                        # 借用招式：必须逐个列出「可借哪个招式」（裸动作 damage=0，
                        # 用了等于空过 —— 绝不列出）
                        for _o in _opts:
                            if not self._can_pay_attack(p.active, atk):
                                continue
                            acts.append(self._borrow_action(ai, atk, _o))
                        continue
                    if self._can_pay_attack(p.active, atk):
                        acts.append({"verb": "attack", "attack": ai,
                                     "name": atk.name,
                                     "name_zh": atk.name_zh,
                                     "display": atk.name_zh or atk.name,
                                     "damage": atk.damage,
                                     "cost_map": list(atk.cost),
                                     "desc_zh": atk.desc_zh,
                                     "display_desc": atk.display_desc})
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
                # 被动型 field 特性不进合法动作表（由结算路径自动触发）
                if ab.tag.startswith(("ko_draw:", "weak_change:", "evolve_attach:",
                                      "evolve_act:", "passive:", "on_damaged:",
                                      "on_ko:", "ko_prize_plus:")):
                    continue
                # 效果族纯静态钩子（无 act 一次性动作，如 ability_blocked）→ 被动，
                # 不进合法动作表（封锁由结算路径在 ability_blocked 自动施加）
                if ab.tag.startswith("fx:") and not families.has_act(ab.tag):
                    continue
                if ab.tag.startswith("recon:") and not p.deck:
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
                if ab.tag.startswith(("discard_opp_random:", "shuffle_opp_hand:")):
                    if not opp.hand:
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

    def _trainer_legals(self, p: PlayerState, opp: PlayerState) -> list:
        out = []
        for i, c in enumerate(p.hand):
            if not isinstance(c, TrainerRef):
                continue
            tag, kind = c.tdef.tag, c.tdef.kind
            # 跨回合手牌禁打（Budew / Frillish / Galvantula ex 的 Item 封锁）
            # 官方规则中 Pokémon Tool 属于 Item，故一并封锁。
            if kind in ("item", "tool") \
                    and p.item_lock_until > self.turn_counts[self.current]:
                continue
            if kind == "supporter" and p.supporter_used:
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
            # 统一带上标识（英文键 name / 中文 name_zh / 展示名 display / tag），
            # 供前端、AI 与「按名称下达指令」（engine.commands.find_action）使用。
            base = {"verb": "trainer", "hand": i,
                    "name": c.tdef.name, "name_zh": c.tdef.name_zh,
                    "display": c.tdef.display, "tag": tag, "kind": kind}
            if tag == "poffin":
                if len(p.bench) < self.cfg.max_bench:
                    out.append(dict(base))
            elif tag == "ultra_ball":
                if len(p.hand) >= 3:   # 自身 + 弃 2
                    out.append(dict(base))
            elif tag == "hammer":
                if opp.active is not None and opp.active.total_energy() > 0:
                    out.append(dict(base))
            elif tag == "boss":
                if opp.bench:
                    for bi in range(len(opp.bench)):
                        out.append({**base, "target": bi})
            elif tag.startswith("research:") or tag.startswith("shuffle_draw:"):
                # 抽牌型支援者（Professor's Research / Iono）：需牌库非空
                if p.deck:
                    out.append(dict(base))
            elif tag == "switch_self":
                # 自换位（Switch）：需有后备
                for bi in range(len(p.bench)):
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
            # ---- B3（2026-10-28）：训练家侧抽牌 / 回复 / 附能 / 弃牌 / 磨牌 ----
            # 这些 tag 的**派发**早已存在（见 `_do_trainer`），但合法动作表一直
            # 没有对应分支 → AI 永远选不到这些卡（对局里表现为「摆设」）。
            elif tag.startswith("draw_both:") or tag.startswith("opp_shuffle_draw:"):
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
            elif tag.startswith("attach"):
                _n, _et, _zn = _parse_attach_tag(tag)
                if _zone_energy_available(p, _et, _zn or "any"):
                    for tpos, _fp in _field_iter(p):
                        out.append({**base, "target": tpos})
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
        if self.phase != "main":
            return {"ok": False, "error": f"phase={self.phase}"}
        p = self.players[self.current]
        verb = action.get("verb")
        try:
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
        except ValueError as e:
            return {"ok": False, "error": str(e)}

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
        detail = special_energy.on_attach(self, p, target, ref.edef)
        self._log("attach", {"player": self.current, "target": target.name,
                             "etype": (ref.edef.tag or ref.edef.type),
                             "energy_now": target.total_energy(),
                             **({"detail": detail} if detail else {})})
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
        if opt["src"] == "ally":
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
            if o["src"] == "ally" and int(req.get("pos", -1)) != o["pos"]:
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
        m = self._stadium_meta()
        owner = self.current if pi is None else pi
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
        old_name = tgt.name
        p.hand.pop(hi)
        tgt.evolve_to(c.pdef)
        tgt.evolved_turn = self.turn_number
        tgt.evolved_from_name = old_name
        self._log("evolve", {"player": self.current, "from": old_name,
                             "to": tgt.name, "pos": tpos})
        self._trigger_evolve_attach(tgt)
        return {"ok": True, "event": self.events[-1]}

    def _trigger_evolve_attach(self, fp: FieldPokemon) -> None:
        """进化时触发的附能特性（``evolve_attach:<n>[:<type>][:<zone>]``）

        对应 Blaziken ex「火焰之舞」：从手牌进化时，从弃牌堆取 n 张指定色
        基础能量附到自身（引擎子集：附到自身而非后备，差异见 cards_real.json note）。
        """
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
                subs = tuple(s for s in tag[len("evolve_act:"):].split("|") if s)
                if not subs:
                    continue
                self._log("ability", {"player": self.current, "name": ab.name,
                                      "from": fp.name, "trigger": "on_evolve",
                                      "detail": "|".join(subs)})
                self._apply_attack_effects(fp, subs, action={})

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
                # `attach_energy:` 之前，避免被同前缀分支吞掉
                detail = self._do_attach_spread(p, tag, action)
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
                detail = self._apply_energy_move(tag, fp, action)
                self._log("ability", {"player": self.current, "name": name,
                                      "from": fp.name, "detail": detail})
                return {"ok": True, "event": self.events[-1]}
            if tag.startswith("bounce:"):
                # S6 回手族（如 Alomomola「弃牌堆基础宝可梦铺后备」/ Duskull）
                detail = self._apply_bounce(tag, fp, action)
                self._log("ability", {"player": self.current, "name": name,
                                      "from": fp.name, "detail": detail})
                return {"ok": True, "event": self.events[-1]}
            if tag.startswith("discard_tool:"):
                self._apply_discard_tool(tag)
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
                if self._status_blocked(opp.active):
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
        # 静态禁令（「你的对手不能从手牌打出物品卡」等，效果族提供）
        if families.blocked_play(self, p, c):
            raise ValueError("场上效果禁止打出该卡")
        if kind == "supporter":
            if p.supporter_used:
                raise ValueError("本回合已使用支援者")
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
        # 执行（tag 派发）
        if tag == "poffin":
            placed = self._trainer_poffin(p)
        elif tag == "ultra_ball":
            placed = self._trainer_ultra_ball(p, hi)
        elif tag == "hammer":
            placed = self._trainer_hammer(opp)
        elif tag == "boss":
            placed = self._trainer_boss(opp, int(action.get("target", 0)))
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
        elif tag.startswith("tool:"):
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
        elif tag.startswith("draw_to:"):
            placed = self._trainer_draw_to(p, tag, hi)
        elif tag.startswith("draw:"):
            placed = self._trainer_draw(p, tag)
        elif tag.startswith("heal:"):
            placed = self._trainer_heal(p, tag, action)
        elif tag.startswith("gust"):
            placed = self._trainer_boss(opp, int(action.get("target", 0)))
        elif tag.startswith("attach_spread:"):
            # 多张自由分配附着（2026-10-30 第三十三批）：排在 `attach` 之前
            placed = self._do_attach_spread(p, tag, action)
        elif tag.startswith("attach"):
            placed = self._trainer_attach_energy(p, tag, action)
        elif tag.startswith("discard_opp_random:"):
            placed = f"opp discard {self._discard_opp_random(int(tag.split(':')[1]))}"
        elif tag.startswith("shuffle_opp_hand:"):
            placed = f"opp shuffle {self._shuffle_opp_hand(int(tag.split(':')[1]))}"
        elif tag.startswith("discard_opp_to:"):
            placed = f"opp discard to {self._discard_opp_to(int(tag.split(':')[1]))}"
        elif tag.startswith("energy_move:"):
            placed = self._apply_energy_move(tag, None, action)
        elif tag.startswith("bounce:"):
            placed = self._apply_bounce(tag, None, action)
        elif tag.startswith("discard_tool:"):
            placed = f"discard tool {self._apply_discard_tool(tag)}"
        elif tag == "discard_stadium":
            placed = f"discard stadium {self._apply_discard_stadium()}"
        elif tag.startswith("discard:"):
            placed = self._trainer_discard_all(p, tag, hi)
        elif tag.startswith("mill:"):
            placed = self._trainer_mill(tag)
        elif tag.startswith("fx:"):
            placed = families.apply_action(
                self, tag, {"kind": "trainer", "card": c, "action": action,
                            "player": self.current, "hand_index": hi})
        else:
            raise ValueError(f"未知训练家 tag={tag}")
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
        op, n = spec
        if op != "discard":
            raise ValueError(f"未知打出代价操作：{op}")
        cands = [i for i in range(len(p.hand)) if i != self_hi]
        if len(cands) < n:
            raise ValueError(f"打出代价付不出（需弃 {n} 张，手牌只有 {len(cands)} 张可选）")
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

    def _apply_discard_tool(self, tag: str) -> int:
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
                      action: Optional[dict] = None) -> str:
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
            return self._bounce_energy(tag, fp)
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

    def _bounce_energy(self, tag: str, fp: Optional[FieldPokemon] = None) -> str:
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
                           action: Optional[dict] = None) -> str:
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

    def _do_attach_spread(self, p: PlayerState, tag: str,
                          action: dict) -> str:
        """多张能量**自由分配**附着（``attach_spread:...``）

        卡面原型：「Search your deck for up to 3 Basic Energy cards and attach
        them to your Pokémon **in any way you like**. Then, shuffle your deck.」

        分配口径（引擎无交互选择系统，沿用项目既有 choose 类做法）：
          · ``action['attach_plan']`` 给位置码列表（0 = active，1..n = 后备）；
          · 缺省按**轮转**依次落到「active → bench[0] → bench[1] → …」，
            保证可复现，且不会把 n 张全堆在 active 上（那等于把「自由分配」
            塌缩成一个特定选择）。

        取不到符合条件的基本能量、或目标集合为空 → 返回空串（规则上效果不
        执行，不是错误）。卡面写「Then, shuffle your deck」→ 动过牌库就洗。
        """
        from .cards import EnergyRef          # noqa: F401（zone 校验用）
        n, etype, zone, scope = _parse_attach_spread_tag(tag)
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
                    # 显式指定了不符合卡面限定的宝可梦 → 抛错，不得悄悄改附别人
                    raise ValueError(
                        f"attach_plan 位置 {pos} 不符合目标限定 {scope}")
                seq.append(fp)

        moved = 0
        for k in range(n):
            if seq:
                if k >= len(seq):
                    break
                fp = seq[k]
            else:
                fp = targets[k % len(targets)]
            if _attach_energy_from_zones(self, p, fp, 1, etype, zone) <= 0:
                break
            moved += 1
        if not moved:
            return ""
        if zone in ("deck", "any"):
            # 卡面固定后缀「Then, shuffle your deck.」——动过牌库就必须洗
            self.rng.shuffle(p.deck)
        self._log("attach_spread", {"player": self.current, "n": moved,
                                    "etype": etype, "zone": zone,
                                    "scope": scope})
        return f"attach {moved} {etype or 'any'} spread over {len(targets)}"

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
        n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
        basic_only = "basic" in parts[2:]
        ptype = None
        names = None
        for i, x in enumerate(parts):
            if x == "type" and i + 1 < len(parts):
                ptype = parts[i + 1].lower()
            if x == "name" and i + 1 < len(parts):
                # 指定卡名（可 `+` 并联，如 "Silcoon or a Cascoon"）
                names = [s for s in parts[i + 1].split("+") if s]
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

    def _trainer_hammer(self, opp: PlayerState) -> str:
        """抛币正面 → 弃对手 active 1 能量"""
        if opp.active is None or opp.active.total_energy() <= 0:
            raise ValueError("对手 active 无能量")
        if self.rng.random() < 0.5:
            opp.active.remove_any(1)
            return "heads: discard 1 energy"
        return "tails: no effect"

    def _trainer_boss(self, opp: PlayerState, bi: int) -> str:
        """将对手 1 只后备拉为 active（换位）"""
        if not opp.bench or bi >= len(opp.bench):
            raise ValueError("对手无可拉后备")
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
        """
        parts = tag.split(":")
        n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 2
        etype = (parts[3].lower()
                 if len(parts) > 3 and parts[2] == "type" else None)
        found = _take_energy_from_deck(p, n, etype)
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
        found = _take_kind_from_deck(p, n, _search_kind_of(tag), _search_filter(tag))
        if not found:
            # 同上：「up to N」失败也算合法（招式/特性照常结算）
            self._log("search", {"player": self.current, "n": 0,
                                 "kind": _search_kind_of(tag), "reason": "无目标"})
            return f"search 0 {_search_kind_of(tag)} to hand"
        self.rng.shuffle(p.deck)
        return f"search {found} {_search_kind_of(tag)} to hand"

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
        cost = _cheaper_cost(atk.cost, _attack_cost_delta(self, fp))
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
        if self.cfg.first_turn_no_attack and self.turn_counts[self.current] == 1:
            raise ValueError("先手第一回合不可攻击")
        if _NO_ATTACK_STATUS & p.active.status:
            raise ValueError("睡眠/麻痹中禁止攻击")
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
        borrow = self._resolve_borrow(p, self.players[1 - self.current],
                                      p.active, atk, action)
        pay_atk = borrow["pay"] if borrow else atk
        if borrow:
            atk = borrow["atk"]
        if not self._can_pay_attack(p.active, pay_atk):
            raise ValueError("能量不足（无法支付招式费用）")
        attacker = p.active
        _log = {"player": self.current, "attack": atk.name,
                "from": attacker.name, "cost": dict(pay_atk.cost)}
        if borrow:
            _log["via"] = p.active.pdef.attacks[ai].name
            _log["borrow_src"] = borrow["src"]
        self._log("attack", _log)
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
                ignore_eff = "ignore_effects" in atk.effects
                # 静态修正（S1）：`passive:no_weakness` 让弱点不生效；
                # 带来源属性的减伤需要攻击方，才能按卡面限定结算
                static = _static_mods(defender, attacker)
                dmg = compute_damage(attacker, defender, base,
                                     weakness_override=self._weakness_override(
                                         attacker, defender),
                                     ignore_weakness=(mods["ignore_weakness"]
                                                      or static["no_weakness"]),
                                     ignore_resistance=mods["ignore_resistance"])
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
                if imm:
                    dmg = 0
                    self._log("blocked", {"target": defender.name,
                                          "by": imm})
                damage_dealt = dmg or 0
                if dmg:
                    defender.damage += dmg
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
            if not mods.get("nullify_all"):
                self._apply_attack_effects(attacker, atk.effects,
                                           plan=action.get("targets"),
                                           action=action, pre_mods=mods)
            # S2 第二阶段：道具触发钩子（卡面写 even if Knocked Out → 先于击倒结算）
            self._trigger_tool_on_damaged(defender, attacker, damage_dealt)
            # 2026-10-27：受击反应**特性**（`on_damaged:*`），同一时点
            self._trigger_ability_on_damaged(defender, attacker, damage_dealt)
            # 2026-10-28：受击反应**特殊能量**（Spiky Energy）
            self._trigger_energy_on_damaged(defender, attacker, damage_dealt)
            # 2026-10-29 第二十九批：下回合「受击反制」（Bouffalant / Zamazenta）
            self._trigger_retaliate(defender, attacker, damage_dealt)
            if defender.is_ko and was_full and self._tool_survive(defender):
                pass                      # 免击倒：伤害已改为「剩 M 点」
            if defender.is_ko:
                self._resolve_ko(1 - self.current, defender)
        finally:
            self._ko_from_attack = prev_ko_flag
        if not self.finished:
            self._end_turn()
        return {"ok": True, "event": None}

    # ---------- S3 场地（Stadium，2026-10-27） ----------
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
                    if tag.startswith("passive:hp_plus:"):
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

    def _status_blocked(self, fp: Optional[FieldPokemon]) -> bool:
        """特殊状态是否**不能施加**到该宝可梦（化石 / Festival Grounds）"""
        if fp is None:
            return False
        return bool(getattr(fp, "fossil", False)) or self._stadium_status_immune(fp)

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
            if tag == "dmg_zero":
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
                out["set"] = self._dmg_set_value(tag)
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
        if atk is not None and attacker.self_atk_plus_next:
            _hit = attacker.self_atk_plus_next.get(attacker._attack_key(atk.name))
            if _hit and _hit[1] > now and _hit[0]:
                out["plus"] += _hit[0]
                self._log("self_atk_plus_next_applied", {
                    "target": attacker.name, "attack": atk.name, "n": _hit[0]})
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
        """``coin_until_tails:dmg_plus:<per>`` —— 连续抛币直到反面

        卡面原型 Breloom / Tyrogue / Voltorb ex：「Flip a coin until you get
        tails. This attack does N more damage for each heads.」
        每个正面 +per，直到出现反面为止（上限 100 次，防极端死循环；
        概率 2^-100 可忽略，且日志完整可复现）。
        """
        parts = tag.split(":")
        kind = parts[1] if len(parts) > 1 else ""
        per = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        if kind != "dmg_plus" or per <= 0:
            raise ValueError(f"coin_until_tails tag 非法：{tag}")
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
        if heads:
            out["plus"] += per * heads

    def _apply_coin_sub(self, attacker: FieldPokemon, sub: str, out: dict,
                        repeat: int = 1) -> None:
        """抛币命中的子效果 → 归入伤害修正层（其余子效果留待 _apply_attack_effects）

        子效果可复合（``a|b``，Floragato「+30 damage, and heal 30」）——逐段结算，
        但**共用同一次抛币结果**（绝不重复抛币）。
        """
        if sub.startswith("dmg_set:"):
            # set 语义不是「重复叠加」：coin_each 命中 N 枚 → 基础伤害 = N × 命中数。
            # 必须放在循环外，否则 repeat 次循环会变成 N × repeat²。
            out["set"] = (out["set"] or 0) + self._dmg_set_value(sub) * max(1, repeat)
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

    def _evolve_from_deck(self, p: PlayerState, fp: FieldPokemon) -> None:
        """``evolve_from_deck``：从牌库检索「可进化此宝可梦的卡」直接进化

        卡面原型：「Search your deck for a card that evolves from this Pokémon and
        put it onto this Pokémon to evolve it.」——属**招式内进化**。
        语义：「可以」→ 牌库无目标时**不算失败**（不抛错，仅记录无效果）。
        """
        if fp is None or p is None:
            return
        idx = None
        for i in range(len(p.deck) - 1, -1, -1):
            c = p.deck[i]
            if isinstance(c, PokeRef) and c.pdef.stage > 0 \
                    and c.pdef.evolves_from == fp.pdef.name \
                    and fp.pdef.stage + 1 == c.pdef.stage:
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
        for i in range(len(p.bench)):
            fp = p.bench[i]
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
                continue
            ref = p.deck.pop(idx)
            old = fp.name
            fp.evolve_to(ref.pdef)
            fp.evolved_turn = self.turn_number
            fp.evolved_from_name = old
            done += 1
            self._log("evolve", {"player": self.current, "from": old,
                                 "to": fp.name, "from_zone": "deck"})
            self._trigger_evolve_attach(fp)
        if done:
            self.rng.shuffle(p.deck)        # 检索后洗牌（引擎统一约定）
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
            # 护盾：只有卡面写明「from and effects of attacks」的才挡效果
            if fp.shield_prevent_all and fp.shield_prevent_effects:
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
            if str(tag).startswith("pre:"):
                # 已在 `_do_attack` 的**伤害前阶段**执行过（2026-10-29 第二十五批）。
                # 明确跳过，避免以后误当成「未知 tag 被静默忽略」。
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
            elif tag.startswith("status_self:"):
                # 自身陷入状态（「This Pokémon is now Confused」）
                st = tag.split(":", 1)[1]
                if st not in STATUSES:
                    raise ValueError(f"未知状态：{st}")
                attacker.status.add(st)
                self._log("status", {"player": self.current,
                                     "target": attacker.name, "status": st,
                                     "side": "self"})
            elif tag in ("poison", "burn", "sleep", "paralyze", "confuse"):
                if opp.active is not None and not opp.active.is_ko:
                    if _opp_blocked(opp.active):
                        self._log("blocked", {"target": opp.active.name,
                                              "by": "prevent_all"})
                        continue
                    if self._status_blocked(opp.active):
                        # 化石卡 / Festival Grounds：「can't be affected by any
                        # Special Conditions」
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
                    fp.damage += per
                    self._log("damage", {"target": fp.name,
                                         "player": 1 - self.current,
                                         "dmg": per, "base": per,
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
            elif tag.startswith("heal:"):
                # 范围（全体/后备/属性限定）由 `_apply_heal` 统一解释；
                # 无 scope 时回复攻击方自身（历史语义，保持不变）
                healed, n_tgt = _apply_heal(self, p, tag, caster=attacker,
                                            action=action)
                self._log("heal", {"player": self.current,
                                   "target": attacker.name, "amt": healed,
                                   "targets": n_tgt})
                prev_ok = healed > 0       # 「If you healed any damage in this way」

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
                if self._apply_knockout(tag, action, _opp_blocked):
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
                        if avail and _fp.remove_any(avail):
                            done += avail
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
                        if len(parts) > 3 and parts[3] == "special":
                            # 「Discard a Special Energy from your opponent's Active
                            # Pokémon.」（2026-10-29 第二十四批）——按**特殊能量卡**
                            # 弃置（`senergy`）；普通 `remove_any` 会先弃基本能量，
                            # 对手只贴基本能量时就会**本不该生效却生效**（静默错算）。
                            done = _discard_special_energy(self, opp, opp.active, n)
                        else:
                            avail = min(n, opp.active.total_energy())
                            if avail and opp.active.remove_any(avail):
                                done = avail
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
                        if avail and p.active.remove_any(avail):
                            done = avail
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
            elif tag.startswith("energy_move:"):
                # S6 能量移动族（自身↔后备 / 对手战斗位→后备 / 双向任选）
                self._apply_energy_move(tag, attacker, action)
            elif tag.startswith("discard_opp_random:"):
                self._discard_opp_random(int(tag.split(":")[1]))
            elif tag.startswith("shuffle_opp_hand:"):
                self._shuffle_opp_hand(int(tag.split(":")[1]))
            elif tag.startswith("discard_opp_to:"):
                self._discard_opp_to(int(tag.split(":")[1]))
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
        if kind == "item":
            target.item_lock_until = max(target.item_lock_until, until)
        elif kind == "evolution":
            target.evolution_lock_until = max(target.evolution_lock_until,
                                              until)
        else:
            raise ValueError(f"未知 play_lock 类别：{tag}")
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
        ignore_eff = "ignore_effects" in (effects or ())
        static = _static_mods(defender, attacker) if attacker is not None else {}
        dmg = compute_damage(
            attacker, defender, base,
            weakness_override=(self._weakness_override(attacker, defender)
                               if attacker is not None else None),
            ignore_weakness=(bool(mods.get("ignore_weakness"))
                             or bool(static.get("no_weakness"))),
            ignore_resistance=bool(mods.get("ignore_resistance")))
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

    def _apply_counters(self, tag: str, action: Optional[dict] = None,
                        holder: Optional[FieldPokemon] = None) -> bool:
        """``counters:<n>[:per:<counter>][:scope:opp[:<count>]][:selfko]``

        伤害指示物**直放**（2026-10-29 第二十批）。指示物不吃弱点/抗性、也不触发
        「受击反应」以外的伤害修正，因此**不能**复用 `damage`/`snipe` 字段。

        · 无 `:scope:` → 对手**战斗位**（历史语义：Haunter / Alakazam）
        · `:scope:opp[:<count>]` → 对手**任意**宝可梦（战斗位优先，与 snipe 的
          确定性默认同口径）；显式选择用 ``action["snipe"]`` 的位置码
          （0 = 战斗位，i≥1 = 后备 i-1，见 `_snipe_candidates`）
        · `:scope:ability:all` → **双方**全场「拥有特性」的宝可梦逐个各吃 N
          （Cofagrigus「Law of the Underworld」，2026-10-29 第二十八批）
        · `:selfko` → 结算完成后**持有者自身**被击倒（对手照常取奖赏；
          Dusclops / Dusknoir「诅咒爆裂」）

        返回 ``True`` 表示对局已结束。
        """
        body = str(tag)
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
            holder.damage = holder.max_hp
            self._log("knockout_effect", {"player": self.current,
                                          "target": holder.name,
                                          "scope": "selfko"})
            self._resolve_ko(self.current, holder)
            return self.finished
        return False

    def _apply_knockout(self, tag: str, action: Optional[dict], blocked) -> bool:
        """``knockout:<scope>`` —— 招式**直接击倒**（2026-10-28 第九批）

        scope：
          ``opp_active[:basic]``   对手战斗位
          ``opp_bench[:basic]``    对手后备 1 只
          ``opp_any``              对手任意 1 只
          ``opp_counters:<n>``     对手「恰好 n 个伤害指示物」的 1 只
          ``both_active``          双方战斗位同时被击倒

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

        if scope == "opp_active":
            pool = [(0, opp.active)] if opp.active is not None else []
            qual = parts[2] if len(parts) > 2 else ""
        elif scope == "opp_bench":
            pool = [(i + 1, fp) for i, fp in enumerate(opp.bench)]
            qual = parts[2] if len(parts) > 2 else ""
        elif scope == "opp_any":
            pool = self._snipe_candidates("opp")
            qual = ""
        elif scope == "opp_counters":
            n = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else -1
            pool = [(pos, fp) for pos, fp in self._snipe_candidates("opp")
                    if fp.damage == n * 10]
            qual = ""
        else:
            raise ValueError(f"未识别的 knockout 范围：{tag}")
        if qual == "basic":
            pool = [(pos, fp) for pos, fp in pool if fp.stage == 0]
        elif qual:
            raise ValueError(f"未识别的 knockout 限定：{tag}")
        if wanted:
            narrowed = [(pos, fp) for pos, fp in pool if pos in wanted]
            if narrowed:
                pool = narrowed
        if not pool:
            self._log("no_effect", {"tag": tag, "reason": "无合法击倒目标"})
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
        # 特殊能量修正（Legacy Energy）：仅限「被对手招式伤害击倒」，每局一次
        if self._ko_from_attack:
            give = max(0, give + special_energy.prize_delta(self, fp))
        # 被击倒触发：多取奖赏（Togekiss「Wonder Kiss」，只在**战斗位**被击倒时）
        if _was_active:
            give = max(0, give + self._ko_prize_bonus(owner))
        opp.prizes_left = max(0, opp.prizes_left - give)
        opp.prizes_taken_this_turn += give     # 「上回合取走几张」记账
        self._log("prize", {"player": 1 - owner, "take": give,
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

    def _ko_prize_bonus(self, owner: int) -> int:
        """被击倒触发：**取奖赏方**因对手战斗位被击倒而多取 N 张

        卡面原型（Togekiss「Wonder Kiss」）：「When your opponent's Active Pokémon
        is Knocked Out, flip a coin. If heads, take 1 more Prize card. The effect
        doesn't stack.」→ 只取**第一条**命中的特性（不叠加）。
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
                if len(parts) > 2 and parts[2] == "coin":
                    res = "heads" if self.rng.random() < 0.5 else "tails"
                    self._log("coin", {"player": taker, "result": res,
                                       "tag": tag})
                    if res != "heads":
                        continue
                self._log("ko_prize_plus", {"player": taker,
                                            "from": holder.name, "n": n})
                return n
        return 0

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
    amt = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    scope, ptype = "", None
    i = 2
    while i < len(parts):
        if parts[i] == "type" and i + 1 < len(parts):
            ptype = parts[i + 1].lower()
            i += 2
            continue
        if parts[i]:
            scope = parts[i]
        i += 1

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
        fp.damage = 0 if full else max(0, fp.damage - amt)
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
                              allow_special: bool = False) -> int:
    """从指定区域检索 n 张能量附着到 target（招式/特性加速用）

    ``zone``：``hand`` | ``discard`` | ``deck`` | ``any``（= hand→discard→deck）。
    与「手牌附能」不同：效果加速不受 `attached_energy_this_turn` 限制。
    etype 为 None 时接受任意能量；指定类型时只取该色。
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
                lst.pop(i)
                target.senergy.append(c)
                got += 1
                continue
            if etype and getattr(c.edef, "type", None) != etype:
                continue
            lst.pop(i)
            target.add_energy(getattr(c.edef, "type", "colorless"), 1)
            got += 1
            if zn == "discard_refs":
                # 文本层同步移除（保持两层次序一致）
                for k in range(len(p.discard) - 1, -1, -1):
                    if p.discard[k] == c.edef.name:
                        p.discard.pop(k)
                        break
    return got


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
        p.hand.append(p.deck.pop(i))
        got += 1
    return got


def _search_filter(tag: str) -> dict:
    """``search_pokemon:N[:type:<c>][:stage:<s>][:hp:<n>][:resistance:<c>]`` → 筛选字典"""
    parts = tag.split(":")
    flt = {}
    i = 2
    while i + 1 < len(parts):
        if parts[i] in ("type", "stage", "hp", "resistance"):
            flt[parts[i]] = parts[i + 1]
        i += 2
    return flt


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
    if kind == "bench_type" and i + 2 < len(parts):
        color = parts[i + 2].lower()
        cands = [b for b in p.bench if str(b.pdef.ptype).lower() == color]
        if not cands:
            return None
        return cands[want - 1] if 1 <= want <= len(cands) else cands[0]
    return None


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


def _parse_play_cost(pc: str):
    """解析 ``TrainerDef.play_cost``（``"discard:<n>"``）→ ``("discard", n)`` | None

    卡面「You can use this card only if you discard N other cards from your
    hand.」—— ``other`` 意味着**本卡自己不能当代价**，所以「付得出」的判据是
    ``手牌数 - 1 >= n``（见 ``_play_cost_ok``）。

    未知形态**抛错**，不静默当作「无代价」—— 那会让一张要付账的卡变成免费
    （静默加强，正是本机制要防的那类错误）。
    """
    s = str(pc or "").strip()
    if not s:
        return None
    parts = s.split(":")
    if parts[0] != "discard" or len(parts) < 2 or not parts[1].isdigit():
        raise ValueError(f"未知打出代价形态：{pc}")
    n = int(parts[1])
    if n < 1:
        raise ValueError(f"打出代价张数非法：{pc}")
    return ("discard", n)


def _play_cost_ok(p, pc: str, self_in_hand: bool = True) -> bool:
    """代价付得出吗（``self_in_hand``：本卡此刻是否占着一张手牌）"""
    spec = _parse_play_cost(pc)
    if spec is None:
        return True
    _, n = spec
    avail = len(p.hand) - (1 if self_in_hand else 0)
    return avail >= n


def _parse_attach_spread_tag(tag: str) -> tuple:
    """解析 ``attach_spread:<n>:<etype>:<zone>[:target:<scope>]`` → 四元组

    「Search your deck for up to N Basic Energy cards and attach them to your
    Pokémon **in any way you like**」——与 ``attach`` 的区别是**多张分给多只**：
    ``attach`` 只能附给单一目标，本族卡面允许自由分配。

    ``etype``：``any`` 或小写属性名（``grass`` / ``fire`` …）；
    ``zone``：``hand`` / ``discard`` / ``deck`` / ``any``；
    ``scope``：``any``（任意己方）| ``bench``（后备）| ``cat-<子串>``
    （按 ``pdef.category`` **包含**判定，如 ``cat-future`` / ``cat-marnie``）。

    ⚠ 调用方必须把 ``attach_spread:`` 分支排在既有的 ``attach`` /
    ``attach_energy:`` 分支**之前** —— ``tag.startswith("attach")`` 会把它一并
    吞掉，随后 ``_parse_attach_tag`` 会把 ``spread`` 当成能量属性名（静默失效）。
    """
    parts = str(tag).split(":")
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
    return (int(raw_n), (None if etype in ("any", "") else etype), zone, scope)


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
                           etype: Optional[str]) -> int:
    """从牌库检索 n 张（指定色的）能量入手 → 实际入手数

    洗牌由调用方用 ``battle.rng`` 执行（保持全局可复现性，勿在 helper 内自造 RNG）。
    """
    from .cards import EnergyRef
    got = 0
    for i in range(len(p.deck) - 1, -1, -1):
        if got >= n:
            break
        c = p.deck[i]
        if not isinstance(c, EnergyRef):
            continue
        if etype and getattr(c.edef, "type", None) != etype:
            continue
        p.hand.append(p.deck.pop(i))
        got += 1
    return got


def _borrow_scope(atk) -> Optional[str]:
    """招式的借用来源（`borrow_attack:<scope>`）；普通招式返回 None"""
    for t in getattr(atk, "effects", ()) or ():
        t = str(t)
        if t.startswith("borrow_attack:"):
            return t.split(":", 1)[1]
    return None


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
    # ---- 第七批（2026-10-28）----
    "evolved_from": "卡名归一化键（本回合由该宝可梦进化而来）",
    "used_attack_last_turn": "招式名归一化键（我上回合用过）",
    "own_bench_at_most": "整数 n ≥ 0（我方后备数 ≤ n）",
    "own_hand_exactly": "整数 n ≥ 0（我方手牌数 = n）",
    "opp_prizes_in": "剩余奖赏数列表（逗号分隔，任一命中）",
    "own_bench_named_all": "卡名归一化键（+ 分隔，需**全部**在后备）",
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
    if name == "own_discard_basic_energy_at_least":
        color, _, raw_n = arg.partition(":")
        if not raw_n.isdigit():
            raise ValueError(f"条件参数非整数：{cond}")
        return _eval_counter(battle, f"own_discard_energy_{color}") >= int(raw_n)
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
