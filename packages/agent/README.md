# packages/agent · AI 决策链路（v0.1）

宝可梦 TCG 对战决策模块 —— 状态编码 → 策略网络 → MCTS → 决策流水线（LangGraph 编排）
+ 提示词模板库（LLM 卡牌效果解析 / 对战解说）。对应技术方案 v1.0 模块 2/3 的 P2 起点。

## 结构

| 模块 | 职责 |
|------|------|
| `agent/state_encoder.py` | `view()` → 定长状态向量（**实测 51 维**）+ 动作特征（**14 维**）；**只编码公开信息** |
| `agent/policy_net.py` | PyTorch 小策略-价值网络（MLP：共享躯干 + 每候选打分头 + 价值头） |
| `agent/mcts.py` | 引擎内 MCTS：PUCT 选子 + 真实 rollout（随机/策略）；`mcts_with_stats` 审计 |
| `agent/pipeline.py` | 决策流水线：纯编排 `decide()` 与 LangGraph `build_langgraph_pipeline()` 双形态，节点一致 |
| `agent/policy_agent.py` | **PolicyAgent**：网络驱动的 Agent 协议实现（采样/argmax + E5 spread 分配）；`load_policy` |
| `agent/trainer.py` | **PPO 自对弈训练管线**：collect_episode（双玩家零和回报）→ clip 更新 → 权重保存 + `eval_winrate` |
| `agent/prompts.py` | 提示词加载/渲染（`@@var@@` 占位） |
| `prompts/*.md` | 模板库：parse_effects（system/user，JSON Schema+RAG 插槽）/ commentator |
| `tests/` · `examples/` | 用例 · demo_decision.py / **train_ppo.py** |

## 运行

```bash
# 用例
cd packages/engine && python -m unittest discover -s tests -v   # 66 项（含 E5）
cd packages/agent && python -m unittest discover -s tests -v   # 19 项（含 RL smoke）

# 决策演示（MCTS 30 迭代，输出 top3 审计与选中动作）
cd packages/agent && python examples/demo_decision.py 30

# RL 自对弈训练（PPO）与评估
cd packages/agent
python examples/train_ppo.py --episodes 60 --decks lightning --save artifacts/policy.pt
python examples/train_ppo.py --eval --weights artifacts/policy.pt --games 20
```

## 与引擎的接入契约

- 观测：`engine.Battle.view()`（含 `legal` 合法动作列表）→ `state_encoder.encode_state`
- 动作空间：`legal` 内动作 dict（verb + index/target/attack/tag）→ MCTS 根展开 / PolicyAgent
- **E5 目标选择**：带 `spread_free` 的攻击可携带 `targets:[{pos,dmg}]` 计划（由决策方指定）；
  `build_spread_plan` 用 `distribute_damage` 生成启发式分配；引擎缺失时确定性兜底
- 对局克隆：`copy.deepcopy(battle)`（引擎对象可深拷贝、确定性 seed）→ rollout
- 策略先验：policy_net 输出 softmax 先验注入 PUCT；未训练=均匀先验（演示可用）

## 边界与后续（P2）

- **RL 收敛需长训**：`train_ppo` 提供可运行闭环（smoke 10 局 3.4s），实际强度需数百局
  量级自对弈 + 奖励/熵调参（脚本参数开放）；BC 预热（模仿 BaselineAI）为后续可选加速；
- LangGraph 为可选依赖：纯编排 `decide()` 零依赖可本地跑；`pip install langgraph` 后可用
  StateGraph 形态（行为一致，有单测对齐）；
- LLM（本地 Qwen3-4B 8bit / 云端 OpenAI 兼容）接入见 `prompts/` 与 card-db 解析流水线
  （ADR-LLM-001：K2 不达标切云端保底）。
