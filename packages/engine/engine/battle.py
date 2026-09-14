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
from typing import Optional
from .cards import (CardRef, PokeRef, EnergyRef, TrainerRef, FieldPokemon,
                    make_pokemon, COLORLESS)
from .state import GameConfig, PlayerState
from .rules import (compute_damage, status_checkup, confusion_self_damage,
                    distribute_damage)
# 名称型字段的优先顺序（与 engine.commands 同源，避免两处漂移）
from .commands import NAME_KEYS as _NAME_KEYS

WIN_REASONS = ("prizes", "no_pokemon", "deckout", "max_turns_draw")

TRAINER_TAGS = ("poffin", "ultra_ball", "hammer", "boss")


class Battle:
    """对战裁判（自管理字段；seed 驱动的确定性）"""

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
        """
        label = _card_label(ref)
        p.discard.append(label)
        p.discard_refs.append(ref)
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
        for fp in p.all_field():          # E2/E3: 新回合解除"刚放置"并重置特性
            fp.just_played = False
            fp.ability_used.clear()
        if not p.has_pokemon:
            self._finish(1 - self.current, "no_pokemon", {"loser": self.current})
            return
        if p.active and not p.active.is_ko:
            for ev in status_checkup(p.active, self.rng):
                self._log("checkup", {**ev, "player": self.current})
            if p.active.is_ko:
                self._resolve_ko(self.current, p.active)
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
            acts.append({"verb": "attach", "target": 0})
            for bi in range(len(p.bench)):
                acts.append({"verb": "attach", "target": bi + 1})
        # 招式 / 撤退（总能量计无色门槛）
        if p.active is not None:
            blocked = {"sleep", "paralyzed"} & p.active.status
            if not blocked:
                for ai, atk in enumerate(p.active.pdef.attacks):
                    if p.active.can_pay(atk.cost):
                        acts.append({"verb": "attack", "attack": ai,
                                     "name": atk.name,
                                     "name_zh": atk.name_zh,
                                     "display": atk.name_zh or atk.name,
                                     "damage": atk.damage,
                                     "cost_map": list(atk.cost),
                                     "desc_zh": atk.desc_zh,
                                     "display_desc": atk.display_desc})
                if p.active.total_energy() >= p.active.pdef.retreat \
                        and len(p.bench) > 0:
                    acts.append({"verb": "retreat", "bench": 0})
        # E2 进化：手牌 x → 场上同名 evolves_from 目标
        for hi, c in enumerate(p.hand):
            if isinstance(c, PokeRef) and c.pdef.stage > 0:
                for tpos, tgt in _field_iter(p):
                    if tgt is not None and not tgt.just_played \
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
                # 被动型 field 特性不进合法动作表（由结算路径自动触发）
                if ab.tag.startswith(("ko_draw:", "weak_change:", "evolve_attach:")):
                    continue
                if ab.tag.startswith("recon:") and not p.deck:
                    continue
                if ab.tag.startswith("draw:") and not p.deck:
                    continue
                if ab.tag.startswith("attach_energy:"):
                    _n, _et, _zn = _parse_attach_tag(ab.tag)
                    if not _zone_energy_available(p, _et, _zn):
                        continue
                if ab.tag.startswith("move_damage:"):
                    if fp.damage < 10 or opp.active is None:
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
            if kind == "supporter" and p.supporter_used:
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
            elif tag.startswith("recover:"):
                # 弃牌堆回收（Night Stretcher）：需弃牌堆有可回收卡
                cands = _recover_candidates(p, tag)
                for di in cands:
                    out.append({**base, "target": di})
            elif tag.startswith("discard_search_energy:"):
                # 弃 1 手牌 → 检索 n 能量（Earthen Vessel）：检索源是**牌库**
                if len(p.hand) >= 2 and _zone_energy_available(p, None, "deck"):
                    out.append(dict(base))
            elif tag.startswith("search_energy:"):
                # 检索 n 张能量（Crispin）：检索源是**牌库**
                if _zone_energy_available(p, None, "deck"):
                    out.append(dict(base))
            elif tag == "candy":
                # 跳阶进化（Rare Candy）：手牌有 stage>=2 且场上同名链基础/一阶
                if _candy_legals(p):
                    out.append(dict(base))
            elif tag.startswith(("search_pokemon:", "search_trainer:",
                                 "search_any:")):
                # 通用检索（检索 n 张指定类别入手）——牌库需有可检索对象
                if _deck_has_kind(p, _search_kind_of(tag)):
                    out.append(dict(base))
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
                          "used_action": res["action"]})
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
        if not p.bench_has_room(self.cfg):
            raise ValueError("后备区已满")
        p.hand.pop(idx)
        fp = make_pokemon(c.pdef)
        fp.just_played = True
        p.bench.append(fp)
        self._log("play", {"player": self.current, "card": fp.name})
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
        if not p.attach_energy_to(target, self.cfg):
            raise ValueError("手牌无能量")
        p.attached_energy_this_turn = 1
        self._log("attach", {"player": self.current, "target": target.name,
                             "etype": list(target.energy)[-1],
                             "energy_now": target.total_energy()})
        return {"ok": True, "event": self.events[-1]}

    def _do_retreat(self, p: PlayerState, action: dict) -> dict:
        if p.active is None or not p.bench:
            raise ValueError("无法撤退")
        if {"sleep", "paralyzed"} & p.active.status:
            raise ValueError("睡眠/麻痹中无法撤退")
        cost = p.active.pdef.retreat
        if not p.active.remove_any(cost):
            raise ValueError("能量不足")
        bi = int(action.get("bench", 0))
        if bi >= len(p.bench):
            raise ValueError("替补目标越界")
        old = p.active
        old.clear_status()
        p.active = p.bench.pop(bi)
        p.bench.append(old)
        self._log("retreat", {"player": self.current, "out": old.name,
                              "in": p.active.name})
        return {"ok": True, "event": self.events[-1]}

    def _do_evolve(self, p: PlayerState, action: dict) -> dict:
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
        if tgt.just_played:
            raise ValueError("本回合刚放置/进化，不可再进化")
        if tgt.pdef.name != c.pdef.evolves_from \
                or tgt.stage + 1 != c.pdef.stage:
            raise ValueError("进化链不匹配")
        old_name = tgt.name
        p.hand.pop(hi)
        tgt.evolve_to(c.pdef)
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
            if not ab.tag.startswith("evolve_attach:"):
                continue
            n, etype, zone = _parse_attach_tag(ab.tag)
            got = _attach_energy_from_zones(self, p, fp, n, etype,
                                            zone or "discard")
            self._log("ability", {"player": self.current, "name": ab.name,
                                  "from": fp.name, "trigger": "on_evolve",
                                  "detail": f"attach {got} {etype or 'any'} "
                                            f"from {zone or 'discard'}"})

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
                n = int(tag.split(":")[1])
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
            if tag.startswith("draw:"):
                # 每回合一次抽 n（如「碧绿之舞」变体 / 抽牌型特性）
                n = int(tag.split(":")[1])
                if not p.deck:
                    raise ValueError("牌库为空")
                got = self._safe_draw(self.current, n, source="ability")
                self._log("ability", {"player": self.current, "name": name,
                                      "from": fp.name, "detail": f"draw {n}",
                                      "drew": got})
                return {"ok": True, "event": self.events[-1]}
            if tag.startswith("move_damage:"):
                # 移动伤害指示物：自身 → 对手 active（Munkidori「肾上腺素脑」）
                raw = tag.split(":")[1]
                n = 999 if raw == "all" else int(raw)
                opp = self.players[1 - self.current]
                dst = opp.active
                if dst is None:
                    raise ValueError("对手无 active 目标")
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
            if tag.startswith("attach_energy:"):
                # 附能（如「碧绿之舞」从手牌 / 「火焰之舞」从弃牌堆附 n 个指定色能量）
                n, etype, zone = _parse_attach_tag(tag)
                got = _attach_energy_from_zones(self, p, fp, n, etype, zone)
                if not got:
                    raise ValueError("无可附着的能量（指定区域为空）")
                self._log("ability", {"player": self.current, "name": name,
                                      "from": fp.name, "target": fp.name,
                                      "detail": f"attach {got} "
                                                f"{etype or 'any'} from {zone}"})
                return {"ok": True, "event": self.events[-1]}
            if tag.startswith("evolve_attach:"):
                raise ValueError("evolve_attach 为被动触发特性，不可主动发动")
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
                tgt.damage += hp            # 指示物伤害：不走弱抗
                self._log("ability", {"player": self.current, "name": name,
                                      "from": c.pdef.name, "target": tgt.name,
                                      "dmg": hp})
                if tgt.is_ko:
                    self._resolve_ko(1 - self.current, tgt)
                return {"ok": True, "event": self.events[-1]}
            raise ValueError(f"未知 hand tag {tag}")
        raise ValueError("ability 动作缺少 pos/hand")

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
        if kind == "supporter":
            if p.supporter_used:
                raise ValueError("本回合已使用支援者")
        opp = self.players[1 - self.current]
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
            for c in others:
                self._to_discard(p, c)
            p.hand = [c for i, c in enumerate(p.hand) if i == hi]
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
        elif tag == "candy":
            placed = self._trainer_candy(p, action)
        elif tag.startswith(("search_pokemon:", "search_trainer:",
                             "search_any:")):
            placed = self._trainer_search_kind(p, tag)
        else:
            raise ValueError(f"未知训练家 tag={tag}")
        # 按身份移除已使用的训练家卡（helper 内部弃牌会造成 hand 索引漂移，
        # 用 pop(hi) 会误删——真实卡池的高频物品 Ultra Ball 会踩到）
        p.hand = [x for x in p.hand if x is not c]
        self._to_discard(p, c)
        if kind == "supporter":
            p.supporter_used = 1
        self._log("trainer", {"player": self.current, "tag": tag,
                              "kind": kind, "detail": placed})
        return {"ok": True, "event": self.events[-1]}

    # --- E4 训练家行为 ---
    def _trainer_poffin(self, p: PlayerState) -> str:
        """检索牌库 2 张基础宝可梦放入后备并洗牌"""
        found = 0
        for i in reversed(range(len(p.deck))):
            if found >= 2 or not p.bench_has_room(self.cfg):
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
            opp.bench.append(opp.active)
        opp.active = new_active
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
        p.active = p.bench.pop(bi)
        p.bench.append(old)
        return f"switch {old.name} -> {p.active.name}"

    def _trainer_recover(self, p: PlayerState, tag: str,
                         di: int) -> str:
        """弃牌堆回收（Night Stretcher）：取 1 张入手（仅弃牌堆**顶部**可选）"""
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
        """从牌库检索 n 张能量入手（Crispin 的检索侧子集）"""
        parts = tag.split(":")
        n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 2
        found = _take_energy_from_deck(p, n, None)
        if not found:
            raise ValueError("牌库无能量可检索")
        self.rng.shuffle(p.deck)
        return f"search {found} energy to hand"

    def _trainer_search_kind(self, p: PlayerState, tag: str) -> str:
        """通用检索（search_pokemon:N / search_trainer:N / search_any:N）

        从牌库检索 n 张指定类别卡入手并洗牌（官方规则；引擎统一约定）。
        """
        parts = tag.split(":")
        n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
        found = _take_kind_from_deck(p, n, _search_kind_of(tag))
        if not found:
            raise ValueError("牌库无可检索的目标卡")
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
        self._log("evolve", {"player": self.current, "from": old,
                             "to": tgt.name, "pos": tpos, "candy": True})
        self._trigger_evolve_attach(tgt)
        return f"rare candy: {old} -> {tgt.name}"

    # ---------- 攻击 ----------
    def _do_attack(self, p: PlayerState, action: dict) -> dict:
        if p.active is None:
            raise ValueError("无 active")
        if self.cfg.first_turn_no_attack and self.turn_counts[self.current] == 1:
            raise ValueError("先手第一回合不可攻击")
        if {"sleep", "paralyzed"} & p.active.status:
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
        if not p.active.can_pay(atk.cost):
            raise ValueError("能量不足（无法支付招式费用）")
        attacker = p.active
        self._log("attack", {"player": self.current, "attack": atk.name,
                             "from": attacker.name,
                             "cost": dict(atk.cost)})
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
        if atk.damage > 0:
            dmg = compute_damage(attacker, defender, atk.damage,
                                 weakness_override=self._weakness_override(
                                     attacker, defender))
            defender.damage += dmg
            self._log("damage", {"target": defender.name,
                                 "player": 1 - self.current,
                                 "dmg": dmg, "base": atk.damage})
        self._apply_attack_effects(attacker, atk.effects,
                                   plan=action.get("targets"), action=action)
        if defender.is_ko:
            self._resolve_ko(1 - self.current, defender)
        if not self.finished:
            self._end_turn()
        return {"ok": True, "event": None}

    def _apply_attack_effects(self, attacker: FieldPokemon, effects: tuple,
                              plan: Optional[list] = None,
                              action: Optional[dict] = None) -> None:
        """结算招式附加效果

        ``plan``：E5 任意分配的目标计划 [{"pos": bench索引, "dmg": ...}, ...]
        ``action``：原始动作（读取 ``bench``/``target`` 等显式选择，缺省时取确定性默认值）

        tag 家族（v1.3 扩展，见 cards_real.json meta.engine_tags）：
          · 状态：poison / burn / sleep / paralyze / confuse
          · 放置：spread_free:N（任意分配）· spread:N:per（随机 n 只各 per 点）
          · 自伤 / 回复：self:N · heal:N
          · 抽牌：draw:N
          · 弃牌：discard:N（弃自身手牌）· discard_energy:N（弃自身 active 能量）
          · 换位：switch_self（自己换下）· gust（把对手后备拉为 active）
          · 附能：attach:N[:type]（从弃牌堆优先、无则牌库）
          · 指示物：move_damage:N（自身 → 对手 active）
        """
        p = self.players[self.current]
        opp = self.players[1 - self.current]
        action = action or {}
        for tag in effects:
            if tag in ("poison", "burn", "sleep", "paralyze", "confuse"):
                if opp.active is not None and not opp.active.is_ko:
                    opp.active.status.add(tag)
                    self._log("status", {"player": 1 - self.current,
                                         "target": opp.active.name,
                                         "status": tag})
            elif tag.startswith("spread_free:"):
                total = int(tag.split(":")[1])
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
                n, per = int(n), int(per)
                targets = list(opp.bench)
                self.rng.shuffle(targets)
                killed = []
                for fp in targets[:n]:
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
            elif tag.startswith("heal:"):
                _, amt = tag.split(":")
                amt = int(amt)
                attacker.damage = max(0, attacker.damage - amt)
                self._log("heal", {"player": self.current,
                                   "target": attacker.name, "amt": amt})
            elif tag.startswith("self:"):
                _, amt = tag.split(":")
                attacker.damage += int(amt)
                self._log("damage", {"target": attacker.name,
                                     "player": self.current,
                                     "dmg": int(amt), "self": True})
                if attacker.is_ko:
                    self._resolve_ko(self.current, attacker)
                    if self.finished:
                        return
            elif tag.startswith("draw:"):
                _, k = tag.split(":")
                self._safe_draw(self.current, int(k), source="attack_effect")
            elif tag.startswith("discard_energy:"):
                raw = tag.split(":")[1]
                n = 999 if raw == "all" else int(raw)
                # 优先弃自身 active；无 active 则弃手牌能量
                done = 0
                if p.active is not None:
                    avail = min(n, p.active.total_energy())
                    if avail and p.active.remove_any(avail):
                        done = avail
                if done < n:
                    done += _discard_hand_energy(self, p, n - done)
                self._log("discard_energy", {"player": self.current,
                                             "target": attacker.name, "n": done})
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
                    new_act = opp.bench.pop(bi)
                    if opp.active is not None:
                        opp.bench.append(opp.active)
                    opp.active = new_act
                    self._log("gust", {"player": self.current,
                                       "target": new_act.name, "pos": bi})
            elif tag.startswith("attach"):
                n, etype, zone = _parse_attach_tag(tag)
                got = _attach_energy_from_zones(self, p, attacker, n, etype,
                                                zone or "any")
                self._log("attach", {"player": self.current,
                                     "target": attacker.name, "n": got,
                                     "etype": etype, "from": "effect",
                                     "zone": zone or "any"})
            elif tag.startswith("recover:"):
                # 弃牌堆回收（招式侧）：取 n 张（kind=pokemon|energy|any）入手
                cands = _recover_candidates(p, tag)
                got = 0
                for di in sorted(cands, reverse=True):
                    ref = self._rebuild_ref(p.discard_refs.pop(di))
                    label = _card_label(ref)
                    for k in range(len(p.discard) - 1, -1, -1):
                        if p.discard[k] == label:
                            p.discard.pop(k)
                            break
                    p.hand.append(ref)
                    got += 1
                self._log("recover", {"player": self.current, "n": got,
                                      "from": "discard"})
            elif tag.startswith("move_damage:"):
                raw = tag.split(":")[1]
                n = 999 if raw == "all" else int(raw)
                moved = 0
                src = p.active
                dst = opp.active
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
        if pos == "active":
            p.active = None
        else:
            p.bench.remove(fp)
        p.discard.append(fp.name)
        p.discard_refs.append(PokeRef(fp.pdef))   # 供弃牌堆回收类效果重建
        self._log("knockout", {"player": 1 - owner, "victim_owner": owner,
                               "card": fp.name, "pos": pos})
        give = self.cfg.ex_prizes if fp.pdef.is_ex else 1
        opp.prizes_left = max(0, opp.prizes_left - give)
        self._log("prize", {"player": 1 - owner, "take": give,
                            "left": opp.prizes_left})
        # v1.3：击倒触发型特性（ko_draw:N，如 Fezandipiti ex「逆转局势」）
        # 在被击倒方的**剩余场上**扫描；先结算抽牌再判定胜负/补位
        self._trigger_ko_draw(owner)
        if opp.prizes_left == 0:
            self._finish(1 - owner, "prizes", {"taker": 1 - owner})
            return
        if pos == "active":
            if p.bench:
                p.active = p.bench.pop(0)
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

    # ---------- 观测 ----------
    def view(self, pi: Optional[int] = None) -> dict:
        pi = self.current if pi is None else pi
        me, opp = self.players[pi], self.players[1 - pi]

        def pkmn(fp: Optional[FieldPokemon]):
            if fp is None:
                return None
            d = fp.summary()
            d["weakness"] = fp.pdef.weakness
            d["resistance"] = fp.pdef.resistance
            d["retreat"] = fp.pdef.retreat
            d["ex"] = fp.pdef.is_ex
            d["evolves_from"] = fp.pdef.evolves_from
            d["ability_used"] = sorted(fp.ability_used)
            # i18n：英文名保留（数据键），新增 name_zh / display（中文展示，前端优先用）
            d["name_zh"] = fp.pdef.name_zh
            d["display"] = fp.pdef.display
            d["abilities"] = [{"name": a.name, "name_zh": a.name_zh,
                               "display": a.name_zh or a.name,
                               "tag": a.tag, "location": a.location,
                               "desc": a.desc, "desc_zh": a.desc_zh,
                               "display_desc": a.display_desc}
                              for a in fp.pdef.abilities]
            d["attacks"] = [{"name": a.name, "name_zh": a.name_zh,
                             "display": a.name_zh or a.name,
                             "cost": a.cost_total,
                             "cost_map": list(a.cost), "damage": a.damage,
                             "tags": list(a.effects),
                             "desc": a.desc, "desc_zh": a.desc_zh,
                             "display_desc": a.display_desc}
                            for a in fp.pdef.attacks]
            return d

        hand = []
        for c in me.hand:
            if isinstance(c, PokeRef):
                hand.append({"type": "pokemon", "name": c.pdef.name,
                             "name_zh": c.pdef.name_zh,
                             "display": c.pdef.display,
                             "hp": c.pdef.hp, "stage": c.pdef.stage,
                             "evolves_from_zh": _evolves_from_zh(
                                 c.pdef.evolves_from),
                             "evolves_from": c.pdef.evolves_from,
                             "abilities": [{"name": a.name, "name_zh": a.name_zh,
                                            "display": a.name_zh or a.name,
                                            "tag": a.tag,
                                            "location": a.location,
                                            "desc": a.desc, "desc_zh": a.desc_zh,
                                            "display_desc": a.display_desc}
                                           for a in c.pdef.abilities]})
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


def _discard_hand_energy(battle, p: PlayerState, n: int) -> int:
    """从手牌弃 n 张能量（用于能量不足时的招式消耗）→ 实际弃置数"""
    from .cards import EnergyRef
    dropped = 0
    for i in range(len(p.hand) - 1, -1, -1):
        if dropped >= n:
            break
        if isinstance(p.hand[i], EnergyRef):
            battle._to_discard(p, p.hand.pop(i))
            dropped += 1
    return dropped


def _attach_energy_from_zones(battle, p: PlayerState, target: FieldPokemon,
                              n: int, etype: Optional[str],
                              zone: str = "any") -> int:
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


def _deck_has_kind(p: PlayerState, kind: str) -> bool:
    return any(_kind_matches(c, kind) for c in p.deck)


def _take_kind_from_deck(p: PlayerState, n: int, kind: str) -> int:
    """从牌库检索 n 张指定类别卡入手 → 实际入手数（洗牌由调用方执行）"""
    got = 0
    for i in range(len(p.deck) - 1, -1, -1):
        if got >= n:
            break
        if _kind_matches(p.deck[i], kind):
            p.hand.append(p.deck.pop(i))
            got += 1
    return got


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
        if x in known_zones:
            zone = x
        elif x:
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


def _zone_energy_available(p: PlayerState, etype: Optional[str],
                          zone: str = "any") -> bool:
    """手牌 / 弃牌堆 / 牌库中是否存在（指定色的）能量牌

    ``zone`` 指定时只查该区域（hand|discard|deck|any）。
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
            if etype and getattr(c.edef, "type", None) != etype:
                continue
            return True
    return False


def _recover_candidates(p: PlayerState, tag: str) -> list:
    """弃牌堆回收候选（recover:N[:kind]）→ 弃牌堆下标列表（最多 N 个）

    kind：``pokemon`` | ``energy`` | ``any``（默认 any，对应 Night Stretcher 的
    「宝可梦或基本能量」）。仅返回**类型化层**中存在的卡（文本层不参与判定）。
    """
    from .cards import EnergyRef, PokeRef
    parts = tag.split(":")
    n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
    kind = parts[2] if len(parts) > 2 and parts[2] else "any"
    out = []
    for i, c in enumerate(p.discard_refs):
        if len(out) >= n:
            break
        if kind in ("any", "pokemon") and isinstance(c, PokeRef):
            out.append(i)
        elif kind in ("any", "energy") and isinstance(c, EnergyRef):
            out.append(i)
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
