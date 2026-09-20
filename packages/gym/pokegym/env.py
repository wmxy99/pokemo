# -*- coding: utf-8 -*-
"""单智能体宝可梦 TCG 环境（gymnasium 风格最小接口，零运行时依赖）。

智能体只在自己的回合决策，对手由内置 AI 自动走子。动作空间 = 当前合法动作的
离散下标；观测 = 玩家视角 view dict（含手牌/场面/legal）。"""

from __future__ import annotations

import copy
import random as _random
from dataclasses import dataclass
from typing import Optional

from engine import decklib
from engine.agents import Agent
from engine.battle import Battle

from .agents import make_agent

# 防御性步数上限（对局理论上有限；触发即 truncated）
DEFAULT_MAX_STEPS = 2000


@dataclass
class EnvConfig:
    """环境超参（教学/课程用，可直接改后传入 PokelabEnv(cfg=...)）。"""
    side: int = 0                       # 智能体座位（0 = 先手）
    opponent: str = "baseline"          # 对手名（见 agents.make_agent）
    max_steps: int = DEFAULT_MAX_STEPS
    reward_win: float = 1.0
    reward_loss: float = -1.0
    reward_draw: float = 0.0


def _resolve_deck(deck):
    """接受「卡组 id 字符串」（'lightning' / 'A1' …）或「引擎卡组列表」。"""
    if isinstance(deck, str):
        return decklib.make_deck(deck)
    return list(deck)


class PokelabEnv:
    """单智能体宝可梦 TCG 环境。

    - ``reset(seed) -> (obs, info)``
    - ``step(action) -> (obs, reward, terminated, truncated, info)``
    - ``action``：int（``legal_actions()`` 的下标）或 dict（引擎动作，必须 ∈ legal）
    - ``obs``：玩家视角 view dict（含 ``legal``）
    """

    def __init__(self, deck_a, deck_b, opponent: str = "baseline", side: int = 0,
                 seed: Optional[int] = None, max_steps: int = DEFAULT_MAX_STEPS,
                 cfg: Optional[EnvConfig] = None):
        self.cfg = cfg or EnvConfig(side=side, opponent=opponent,
                                    max_steps=max_steps)
        self.deck_a = _resolve_deck(deck_a)
        self.deck_b = _resolve_deck(deck_b)
        self._opp: Agent = make_agent(self.cfg.opponent)
        self._battle: Optional[Battle] = None
        self._steps = 0
        self.seed = seed if seed is not None else _random.randrange(1 << 30)
        self.reset()

    # ---- 生命周期 ----
    def reset(self, seed: Optional[int] = None):
        if seed is not None:
            self.seed = seed
        self._battle = Battle(self.deck_a, self.deck_b, seed=self.seed)
        self._steps = 0
        self._auto_opponent()
        return self.observation(), self.info()

    def _auto_opponent(self):
        """对手自动走子，直至轮到智能体或对局结束。"""
        b = self._battle
        guard = 0
        while (not b.finished and b.current != self.cfg.side
               and guard < self.cfg.max_steps):
            guard += 1
            self._steps += 1
            act = self._opp.decide(b.view())
            if not b.step(copy.deepcopy(act))["ok"]:
                if not b.step({"verb": "pass"})["ok"]:
                    break

    # ---- 观测 / 动作 ----
    def observation(self):
        return self._battle.view(self.cfg.side)

    def legal_actions(self):
        return [copy.deepcopy(a)
                for a in self._battle.legal_actions(self.cfg.side)]

    def sample_action(self):
        acts = self.legal_actions()
        return copy.deepcopy(_random.choice(acts)) if acts else {"verb": "pass"}

    def sample_action_index(self):
        n = len(self.legal_actions())
        return _random.randrange(n) if n else 0

    @property
    def action_space(self):
        """离散：下标 ∈ [0, len(legal_actions()))"""
        return {"type": "discrete", "n": len(self.legal_actions())}

    @property
    def observation_space(self):
        """结构化 dict（见 Battle.view）"""
        return {"type": "dict", "shape": "view"}

    # ---- 主循环 ----
    def step(self, action):
        """``action``：int（legal 下标）或 dict（引擎动作）。"""
        b = self._battle
        if b.finished:
            return self._finish()
        if isinstance(action, int):
            acts = self.legal_actions()
            if not acts:
                act = {"verb": "pass"}
            elif 0 <= action < len(acts):
                act = acts[action]
            else:
                raise IndexError(
                    f"动作下标 {action} 越界：当前合法动作 {len(acts)} 个")
        else:
            act = copy.deepcopy(action)
            if act not in self.legal_actions():
                raise ValueError("非法动作：必须是当前 legal_actions 之一")
        if not b.step(copy.deepcopy(act))["ok"]:
            b.step({"verb": "pass"})
        self._steps += 1
        self._auto_opponent()
        return self._finish()

    def _finish(self):
        b = self._battle
        terminated = b.finished
        truncated = (not terminated) and self._steps >= self.cfg.max_steps
        reward = self._reward() if terminated else 0.0
        return (self.observation(), reward, terminated, truncated, self.info())

    def _reward(self):
        w = self._battle.winner
        if w is None:
            return self.cfg.reward_draw
        return self.cfg.reward_win if w == self.cfg.side else self.cfg.reward_loss

    def info(self):
        b = self._battle
        return {
            "seed": b.seed, "turn": b.turn_number, "phase": b.phase,
            "winner": b.winner, "finished": b.finished, "steps": self._steps,
            "my_side": self.cfg.side,
            "prizes_left": [p.prizes_left for p in b.players],
        }

    # ---- 辅助 ----
    def render(self, mode="text"):
        b = self._battle
        return "\n".join([
            f"seed={b.seed} turn={b.turn_number} phase={b.phase}",
            f"  P0 奖赏 {b.players[0].prizes_left}  P1 奖赏 {b.players[1].prizes_left}",
            f"  当前行动方={b.current}  结束={b.finished}  胜者={b.winner}",
            f"  智能体座位={self.cfg.side}（{'先手' if self.cfg.side == 0 else '后手'}）",
        ])


def run_episode(env: PokelabEnv, policy=None, seed: Optional[int] = None) -> dict:
    """跑一局：``policy(obs) -> action_index``（默认随机）。返回 info + 累计奖励。"""
    env.reset(seed=seed)
    total = 0.0
    done = False
    while not done:
        a = policy(env.observation()) if policy else env.sample_action_index()
        _, r, term, trunc, info = env.step(a)
        total += r
        done = term or trunc
    info["reward"] = total
    return info
