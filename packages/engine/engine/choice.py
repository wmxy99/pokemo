# -*- coding: utf-8 -*-
"""决策分解层（S1a）· 把「选什么」变成动作空间里的合法动作

为什么需要它
------------
引擎原来的 `step()` 必须**一次把效果做完**，于是「弃任意数量」「可选代价」
这类卡面只有两条路：

  · 引擎替玩家挑一个默认值 → 实质改变对局结果，且对局里看不出来（造假）；
  · 整卡拒收 → 剩余 78 张卡里有 18 张卡在这里。

本模块引入 ``PendingChoice``：效果执行到「需要选择」时**挂起**，`phase` 变
``"choice"``，`legal_actions()` 只返回「选第 i 个」与「结束选择」。选择完成后
再继续执行（``then`` 里的后续 tag）。

⚠ 三条不变量（由 `tests/test_choice_protocol.py` 守护）
  1. **引擎从不自选**：挂起期间没有任何选项被自动采用；
  2. **挂起即冻结**：`phase == "choice"` 时除 `choose` / `stop` 外不得改变状态；
  3. **不吞后续**：`then` 里的 tag 必须被执行；无法执行时**抛错**，不当无事发生。

复杂度
------
「弃任意数量手牌」：全子集枚举 = 2^n 选项；本协议 = n+1 步 × 每步 ≤n 选项。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PendingChoice:
    """一次挂起的选择

    options: 选项列表（每项是**可序列化**的 dict，直接进回放）
    picked:  已选选项的**下标**（允许重复选择同一目标时由 kind 决定语义）
    min/max: 选择数量约束；``max = -1`` 表示不限（直到玩家 `stop`）
    then:    选完后继续执行的 tag（`|` 分隔，可为空）
    """
    kind: str
    owner: int
    options: list = field(default_factory=list)
    picked: list = field(default_factory=list)
    min: int = 0
    max: int = -1
    tag: str = ""
    then: str = ""
    # 「任意分配指示物」这类允许**对同一目标重复选**（两枚都放战斗位）；
    # 「逐张弃手牌」则不允许同一张被弃两次。默认 False = 不重复。
    allow_repeat: bool = False

    def available(self) -> list:
        """还可选的选项下标（默认已选过的不再出现）"""
        if self.allow_repeat:
            return list(range(len(self.options)))
        return [i for i in range(len(self.options)) if i not in self.picked]

    def is_full(self) -> bool:
        return self.max >= 0 and len(self.picked) >= self.max

    def can_stop(self) -> bool:
        return len(self.picked) >= self.min

    def pick(self, i: int) -> None:
        if i not in self.available():
            raise ValueError(f"选项下标非法或已选过：{i}")
        self.picked.append(int(i))

    def chosen(self) -> list:
        return [self.options[i] for i in self.picked]
