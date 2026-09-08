# PokéLab Engine · 对战规则引擎与模拟器

PokéLab AI 的自研简化版宝可梦 TCG 对战规则引擎 + 模拟器（对应 PRD F1/F5/F6 与
《技术方案 v1.0》模块 1）。纯 Python 标准库实现，**确定性**（seed 驱动），无外部依赖。

```
D:\pokemo\pokelab-engine\
├── engine/
│   ├── cards.py       卡牌模型（PokemonDef/AttackDef/EnergyDef + 场上实例 FieldPokemon）
│   ├── state.py       GameConfig（规则开关）/ PlayerState（牌库/手牌/场地/奖赏）
│   ├── rules.py       结算纯函数（弱点×2/抗性−30/状态检查）
│   ├── battle.py      对战裁判：回合状态机 + 动作合法性 + 结算闭环 + 事件日志
│   ├── agents.py      Agent 协议：RandomAgent / BaselineAI(规则基线) / HumanAgent
│   ├── decklib.py     示例卡组库（原创代名卡：Bolt/Thundrex ex/Flarex ex…，规避 IP）
│   └── simulator.py   run_match / run_many（批量统计，F6 本地内核）
├── tests/
│   ├── test_settlement.py   固定结算用例集（unit fixtures）
│   └── test_flow.py         对局流程用例（确定性/终局/批量/判平保护）
├── run_demo.py              单局复盘 / 批量统计 / JSONL 导出
└── README.md
```

## 运行

```bash
# 全量用例（34 项）
python -m unittest discover -s tests -v

# 单局复盘（中文事件流）
python run_demo.py single

# 批量统计 200 局（胜率/回合/先手/终局原因分布）
python run_demo.py batch 200

# 事件导出 JSONL（对齐 matches.events_log 形态）
python run_demo.py dump 20260908
```

## 规则引擎能力（v0.2 · E1/E2/E4 已并入）

| 机制 | 实现 |
|------|------|
| 回合状态机 | 开始（状态检查→抽卡）→ 主阶段 → 结束换边；攻击即结束回合（简化） |
| 动作 | 上基础/附能量(每回合1)/撤退/攻击/**进化(E2)**/**训练家(E4)**/结束；统一 `legal_actions` 合法性校验 |
| 伤害结算 | 基础伤害 → 弱点 ×2 → 抗性 −30（下限 0） |
| **彩色能量(E1)** | 场上能量按 `{type:count}` 存储；招式 cost 支持 `[(fire,1),(psychic,1)]`；无色 cost 可被任意彩色能支付；攻击不弃能、撤退扣能无色优先 |
| **进化(E2)** | stage 0/1/2 + evolves_from 链；本回合刚放置/进化不可再进化；进化保留能量与伤害、清除特殊状态 |
| **训练家(E4)** | `poffin`(检索2基础铺后备) · `ultra_ball`(弃2检索) · `hammer`(抛币弃对手能) · `boss`(拉后备，supporter)；**supporter 每回合一次，item 不限次** |
| 特殊状态 | 中毒(−10/回合) · 烧伤(−20/回合) · 睡眠(抛币醒) · 麻痹(检查阶段恢复) · 混乱(攻击抛币，反面自伤 30 攻击失败；正面攻击后解除)；睡/麻禁攻禁撤 |
| 击倒→奖赏 | 普 1 张 / ex 2 张；active 被击倒强制替补（无后备 → 判负） |
| 胜负三路径 | 奖赏拿完 · 对方无宝可梦 · 对方抽卡空牌库；超 `max_turns` 判平 |
| 招式效果 tag | poison/burn/sleep/paralyze/confuse · spread:n:per(散布) · heal · self(反伤) · draw |
| 确定性 | `random.Random(seed)` 全驱动：同 seed → 同事件序列（有单测） |
| 事件日志 | 结构化 dict 序列 → `matches.events_log` JSONL 可直接落库 |

## ⚠️ 规则边界（与官方规则的已知差异，后续经 ptcg-engine oracle 校准）

1. ~~能量不分颜色~~ → **v0.2 已支持彩色**；特殊能量（宝可梦能量/双重能量）仍为灰名单；
2. **先手第一回合不禁攻**（`first_turn_no_attack` 默认 False；开关已留，单测覆盖 True 分支）；
3. **每回合都抽 1**（含先手首回合；官方差异待校准）；
4. **起手自动铺后备**（官方为玩家选择）；
5. **攻击后立即结束回合**（不做攻击后继续行动）；
6. 训练家**最小子集已实现（4 张）**；其余（物品池/场地/宝可梦道具）为灰名单；
7. **进化已实现（E2）**；**特性机制待 E3 批次**（Recon Directive / Cursed Blast 等 once-per-turn
   特性暂以近似攻击型替代，见 decklib Draco 线注记）；
8. **伤害指示物任意分配（E5）待实现**（Phantom Dive 的 6 指示物当前按随机 spread tag 近似，
   任意分配需 Agent/UI 目标选择接口，P2 与决策流水线同期）；
9. 引擎卡用程序化 `PokemonDef` 定义，F8 effects_json 仅作溯源旁证，不直接进结算路径
   （规避 LLM 解析缺陷，符合 TS v1.0 D1 自研路线）。

## 验收证据（2026-09-08 本机执行）

```
python -m unittest discover -s tests        → Ran 50 tests OK（34 回归 + 16 v0.2 新增）
  覆盖：彩色能量支付/撤退扣能 · 进化链/刚放置禁进化/保留能量伤害 · poffin/ultra_ball/hammer/boss
        supporter 每回合一次 / item 不限次 · Draco Y 版卡组端到端 8 seeds 全终局
run_demo.py single  (seed=20260908)         → baseline 对局 10 回合终局（含 ex 双奖赏/反伤/替补）
run_demo.py batch 100  lightning(baseline) vs psychic(random)
    win_rate 0.72 / avg_turns 25.6 / p95 45 / reason{no_pokemon:49, prizes:32, deckout:19}
```

## 后续接入

- **Agent**：RL/MCTS Agent 通过 `view()` 快照 + `legal_actions()` 接入（决策流水线见 TS v1.0 模块 2）；
- **批量服务**：`run_many` 已产出胜率矩阵输入；batch_tasks 调度与 DB 写入在 P1 上层服务实现；
- **卡池扩展**：新增实卡 = 在 engine 定义 `PokemonDef`（读 DB cards 基本属性，效果由人工确认的程序化
  定义落地，K2 解析结果经校对后转写）。
