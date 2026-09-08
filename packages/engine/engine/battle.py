# -*- coding: utf-8 -*-
"""PokéLab Engine · 对战裁判（回合状态机 + 结算闭环）

v1 简化子集要点（与官方规则差异见 README「规则边界」）：
  - 回合：开始(检查状态→抽1) → 主阶段(上宝可梦/附能量/撤退/攻击/结束) → 换边
  - 攻击成功即结束回合（简化，不做攻击后继续行动）
  - 击倒 → 对手拿奖赏(ex=2)；无后备替补且 active 被击倒 → 判负
  - 抽卡遇空牌库 → 判负；超 max_turns → 判平
  - 事件日志：结构化 dict 追加 events，供复盘/JSONL 落库
"""
from __future__ import annotations
import random
from typing import Optional
from .cards import CardRef, PokeRef, EnergyRef, FieldPokemon, make_pokemon
from .state import GameConfig, PlayerState
from .rules import compute_damage, status_checkup, confusion_self_damage

WIN_REASONS = ("prizes", "no_pokemon", "deckout", "max_turns_draw")


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
            if any(isinstance(c, PokeRef) for c in p.hand):
                break
            # mulligan：整手洗回重抽
            p.deck.extend(p.hand)
            p.hand.clear()
            p.mulligan_count += 1
            self.rng.shuffle(p.deck)

    def _setup_openings(self) -> None:
        # 先 p0 后 p1（mulligan 补偿随后统一结算）
        for pi in (0, 1):
            self._draw_with_mulligan(pi)
            p = self.players[pi]
            basics = p.basic_in_hand()
            first = basics[0]
            p.hand.remove(first)
            p.active = make_pokemon(first.pdef)
            # 自动铺满后备（v1 简化：官方为玩家选择）
            for extra in basics[1:]:
                if len(p.bench) >= self.cfg.max_bench:
                    break
                p.hand.remove(extra)
                p.bench.append(make_pokemon(extra.pdef))
        # mulligan 补偿：对手补抽（简化：补 = 对方 mulligan 次数）
        if self.cfg.mulligan_compensation:
            self._safe_draw(1, self.players[0].mulligan_count, source="mulligan_comp")
            self._safe_draw(0, self.players[1].mulligan_count, source="mulligan_comp")

    # ---------- 抽卡与通用 ----------
    def _safe_draw(self, pi: int, n: int, source: str = "draw") -> int:
        """抽 n 张；遇空牌库立即判该玩家负（deckout）。返回实抽数。"""
        p = self.players[pi]
        if not p.deck and n > 0:
            self._finish(1 - pi, "deckout",
                         {"victim": pi, "need": n, "source": source})
            return 0
        got = p.draw(n)
        self._log("draw", {"player": pi, "n": got, "source": source})
        if len(p.hand) > self.cfg.max_hand:      # 手牌上限防御
            over = p.hand[self.cfg.max_hand:]
            p.discard.extend(c.pdef.name if isinstance(c, PokeRef) else c.edef.name
                             for c in over)
            del p.hand[self.cfg.max_hand:]
            self._log("hand_overflow", {"player": pi, "dropped": len(over)})
        return got

    def _log(self, etype: str, data: dict) -> None:
        self.events.append({"turn": self.turn_number, "player": self.current,
                            "type": etype, **data})

    def _finish(self, winner: Optional[int], reason: str, data: Optional[dict] = None) -> None:
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
        if not p.has_pokemon:
            self._finish(1 - self.current, "no_pokemon", {"loser": self.current})
            return
        # ① 宝可梦检查（当前方场上的状态结算；v1 仅 active）
        if p.active and not p.active.is_ko:
            for ev in status_checkup(p.active, self.rng):
                self._log("checkup", {**ev, "player": self.current})
            if p.active.is_ko:
                self._resolve_ko(self.current, p.active)
                if self.finished:
                    return
        # ② 回合开始抽卡
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
        """当前玩家（或指定玩家）主阶段合法动作列表"""
        pi = self.current if pi is None else pi
        if self.finished or pi != self.current or self.phase != "main":
            return []
        p = self.players[pi]
        acts = []
        for i, c in enumerate(p.hand):
            if isinstance(c, PokeRef):
                if len(p.bench) < self.cfg.max_bench:
                    acts.append({"verb": "play", "index": i})
        if p.energy_in_hand() > 0 and p.attached_energy_this_turn == 0 and p.has_pokemon:
            acts.append({"verb": "attach", "target": 0})
            for bi in range(len(p.bench)):
                acts.append({"verb": "attach", "target": bi + 1})
        if p.active is not None:
            blocked = {"sleep", "paralyzed"} & p.active.status
            if not blocked:
                for ai, atk in enumerate(p.active.pdef.attacks):
                    if p.active.energy >= atk.cost:
                        acts.append({"verb": "attack", "attack": ai})
                if p.active.energy >= p.active.pdef.retreat and len(p.bench) > 0:
                    acts.append({"verb": "retreat", "bench": 0})
        acts.append({"verb": "pass"})
        return acts

    # ---------- 动作执行 ----------
    def step(self, action: dict) -> dict:
        """执行一个动作。返回 {"ok":bool, "error":str|None, "event":dict|None}"""
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
            if verb == "pass":
                self._end_turn()
                return {"ok": True, "event": None}
            return {"ok": False, "error": f"unknown verb {verb}"}
        except ValueError as e:
            return {"ok": False, "error": str(e)}

    def _do_play(self, p: PlayerState, action: dict) -> dict:
        idx = int(action["index"])
        c = p.hand[idx]
        if not isinstance(c, PokeRef):
            raise ValueError("该牌不是宝可梦")
        if not p.bench_has_room(self.cfg):
            raise ValueError("后备区已满")
        p.hand.pop(idx)
        fp = make_pokemon(c.pdef)
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
                             "energy_now": target.energy})
        return {"ok": True, "event": self.events[-1]}

    def _do_retreat(self, p: PlayerState, action: dict) -> dict:
        if p.active is None or not p.bench:
            raise ValueError("无法撤退")
        if {"sleep", "paralyzed"} & p.active.status:
            raise ValueError("睡眠/麻痹中无法撤退")
        cost = p.active.pdef.retreat
        if p.active.energy < cost:
            raise ValueError("能量不足")
        bi = int(action.get("bench", 0))
        if bi >= len(p.bench):
            raise ValueError("替补目标越界")
        p.active.energy -= cost
        old = p.active
        old.clear_status()
        p.active = p.bench.pop(bi)
        p.bench.append(old)
        self._log("retreat", {"player": self.current, "out": old.name,
                              "in": p.active.name})
        return {"ok": True, "event": self.events[-1]}

    def _do_attack(self, p: PlayerState, action: dict) -> dict:
        if p.active is None:
            raise ValueError("无 active")
        if self.cfg.first_turn_no_attack and self.turn_counts[self.current] == 1:
            raise ValueError("先手第一回合不可攻击")
        blocked = {"sleep", "paralyzed"} & p.active.status
        if blocked:
            raise ValueError(f"状态 {sorted(blocked)} 禁止攻击")
        ai = int(action["attack"])
        if ai >= len(p.active.pdef.attacks):
            raise ValueError("招式越界")
        atk = p.active.pdef.attacks[ai]
        if p.active.energy < atk.cost:
            raise ValueError("能量不足")
        attacker = p.active
        self._log("attack", {"player": self.current, "attack": atk.name,
                             "from": attacker.name})
        # 混乱判定
        confused = "confuse" in attacker.status
        if confused and self.rng.random() < 0.5:
            # tails：自伤 30，攻击失败
            self._log("confuse_tails", {"player": self.current, "target": attacker.name})
            attacker.damage += confusion_self_damage(attacker)
            if attacker.is_ko:
                self._resolve_ko(self.current, attacker)
            if self.finished:
                return {"ok": True, "event": None}
            # 攻击未执行但占用行动 → 结束回合
            self._end_turn()
            return {"ok": True, "event": None}
        if confused:
            attacker.status.discard("confuse")
            self._log("confuse_heads", {"player": self.current})
        defender = self.players[1 - self.current].active
        if defender is None:
            self._finish(self.current, "no_pokemon",
                         {"loser": 1 - self.current})
            return {"ok": True, "event": None}
        # 主伤害
        if atk.damage > 0:
            dmg = compute_damage(attacker, defender, atk.damage)
            defender.damage += dmg
            self._log("damage", {"target": defender.name, "player": 1 - self.current,
                                 "dmg": dmg, "base": atk.damage})
        # 效果 tag
        self._apply_attack_effects(attacker, atk.effects)
        # 结算击倒
        if defender.is_ko:
            self._resolve_ko(1 - self.current, defender)
        if not self.finished:
            self._end_turn()   # 攻击即结束回合（v1 简化）
        return {"ok": True, "event": None}

    def _apply_attack_effects(self, attacker: FieldPokemon, effects: tuple) -> None:
        p = self.players[self.current]
        opp = self.players[1 - self.current]
        for tag in effects:
            if tag in ("poison", "burn", "sleep", "paralyze", "confuse"):
                if opp.active is not None and not opp.active.is_ko:
                    opp.active.status.add(tag)
                    self._log("status", {"player": 1 - self.current,
                                         "target": opp.active.name, "status": tag})
            elif tag.startswith("spread:"):
                _, n, per = tag.split(":")
                n, per = int(n), int(per)
                targets = list(opp.bench)
                self.rng.shuffle(targets)
                for fp in targets[:n]:
                    fp.damage += per
                    self._log("damage", {"target": fp.name, "player": 1 - self.current,
                                         "dmg": per, "base": per, "spread": True})
                    if fp.is_ko:
                        self._resolve_ko(1 - self.current, fp)
                        if self.finished:
                            return
            elif tag.startswith("heal:"):
                _, amt = tag.split(":")
                amt = int(amt)
                attacker.damage = max(0, attacker.damage - amt)
                self._log("heal", {"player": self.current, "target": attacker.name,
                                   "amt": amt})
            elif tag.startswith("self:"):
                _, amt = tag.split(":")
                attacker.damage += int(amt)
                self._log("damage", {"target": attacker.name, "player": self.current,
                                     "dmg": int(amt), "self": True})
                if attacker.is_ko:
                    self._resolve_ko(self.current, attacker)
                    if self.finished:
                        return
            elif tag.startswith("draw:"):
                _, k = tag.split(":")
                self._safe_draw(self.current, int(k), source="attack_effect")

    # ---------- 击倒/奖赏 ----------
    def _resolve_ko(self, owner: int, fp: FieldPokemon) -> None:
        """宝可梦被击倒：移出战场 → 对手拿奖赏 → 需替补则替补，无则判负"""
        p = self.players[owner]
        opp = self.players[1 - owner]
        pos = None
        if p.active is fp:
            pos = "active"
        elif fp in p.bench:
            pos = "bench"
        if pos is None:
            return  # 已被处理
        # 从战场移除
        if pos == "active":
            p.active = None
        else:
            p.bench.remove(fp)
        p.discard.append(fp.name)
        self._log("knockout", {"player": 1 - owner, "victim_owner": owner,
                               "card": fp.name, "pos": pos})
        # 奖赏
        give = self.cfg.ex_prizes if fp.pdef.is_ex else 1
        opp.prizes_left = max(0, opp.prizes_left - give)
        self._log("prize", {"player": 1 - owner, "take": give,
                            "left": opp.prizes_left})
        if opp.prizes_left == 0:
            self._finish(1 - owner, "prizes", {"taker": 1 - owner})
            return
        # active 被击倒 → 强制替补
        if pos == "active":
            if p.bench:
                p.active = p.bench.pop(0)
                p.active.clear_status()
                self._log("promote", {"player": owner, "to": p.active.name})
            else:
                self._finish(1 - owner, "no_pokemon", {"loser": owner})

    # ---------- 观测 ----------
    def view(self, pi: Optional[int] = None) -> dict:
        """构建对局视图快照（供 Agent/前端；只含己方手牌，双方场面公开）"""
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
            d["attacks"] = [{"name": a.name, "cost": a.cost, "damage": a.damage,
                             "desc": a.desc} for a in fp.pdef.attacks]
            return d

        hand = []
        for c in me.hand:
            if isinstance(c, PokeRef):
                hand.append({"type": "pokemon", "name": c.pdef.name,
                             "hp": c.pdef.hp})
            elif isinstance(c, EnergyRef):
                hand.append({"type": "energy", "name": c.edef.name})
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
