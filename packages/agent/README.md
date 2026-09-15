# packages/agent · AI 决策链路（v0.1）

宝可梦 TCG 对战决策模块 —— 状态编码 → 策略网络 → MCTS → 决策流水线（LangGraph 编排）
+ 提示词模板库（LLM 卡牌效果解析 / 对战解说）。对应技术方案 v1.0 模块 2/3 的 P2 起点。

## 结构

| 模块 | 职责 |
|------|------|
| `agent/state_encoder.py` | `view()` → 定长状态向量（**基础 51 维**）+ 动作特征（**14 维**）；`EncoderConfig`/`set_active_encoder` 特征域（hand/meta→59 维）|
| `agent/policy_net.py` | PyTorch 小策略-价值网络（MLP：共享躯干 + 每候选打分头 + 价值头） |
| `agent/mcts.py` | 引擎内 MCTS：PUCT 选子 + 真实 rollout（随机/策略）；`mcts_with_stats` 审计 |
| `agent/pipeline.py` | 决策流水线：纯编排 `decide()` 与 LangGraph `build_langgraph_pipeline()` 双形态，节点一致 |
| `agent/policy_agent.py` | **PolicyAgent**：网络驱动的 Agent 协议实现（采样/argmax + E5 spread 分配）；`load_policy` |
| `agent/bc.py` | **BC 预热**：BaselineAI 演示数据采集 + masked-CE 预训练（先验从"合理动作"开始） |
| `agent/reward.py` | **局面得分**（公开信息静态估值）——reward shaping / 价值监督 |
| `agent/trainer.py` | **PPO 训练管线**：自对弈（交替回报）与 vs 固定对手（单侧回报）+ **shaping** 开关 + 权重保存/评估 |
| `agent/dqn.py` | **DQN 对照**：值网（复用打分头）+ ε-greedy + MC 目标 + 目标网刷新（TS：PPO 主选/DQN 对照）|
| `agent/distill.py` | **MCTS 教练蒸馏**（阶段 3）：MCTS 专家决策 → 监督帧 → bc.train_bc 蒸馏进策略 |
| `agent/prompts.py` | 提示词加载/渲染（`@@var@@` 占位） |
| `prompts/*.md` | 模板库：parse_effects（system/user v1.1：防 thinking 污染 + few-shot）/ commentator |
| `tests/` · `examples/` | 用例 · demo_decision / train_ppo / train_dqn / train_pipeline / eval_matrix / check_parse_templates |

**训练课程（TS：BC → PPO → MCTS/冻结快照）**：阶段1 vs 基线（PPO）→ 阶段2 冻结快照自对弈
→ 阶段3 **MCTS 教练蒸馏**（`--stage3-mcts-iters`，expert-iteration 式；可用 BC 同款 masked-CE 学习）。
特征域对比（bad-case 迭代）：`--features full`（59 维，含 hand/meta），训练与评估须同配置。

## K2 · 卡牌效果解析（中文语料 → 结构化 spec → 引擎 tag）

把卡面中文效果文本（`desc_zh`，自述转写）转写为结构化 JSON 并映射到引擎可执行 tag。

| 模块 | 作用 |
|---|---|
| `agent/effects.py` · `parse_rule()` | **离线规则基线**：零依赖、确定性，覆盖伤害/指示物分配/抽牌/弃牌（含手牌·能量）/检索（牌库·弃牌堆）/回复/附能量/状态/互换/进化/静态规则 + 「每回合一次」限制 |
| `agent/effects.py` · `parse_llm()` | **LLM 后端**：OpenAI 兼容 `/chat/completions`，复用 `prompts/parse_effects_*.md`；输出经 `extract_json` 容错（围栏/前后缀/thinking 残留） |
| `parse_effects(text, backend=rule\|llm\|auto)` | 统一入口；`auto` 有 `POKELAB_LLM_BASE` 走 LLM，否则回退规则 |
| `validate_specs()` | Schema 校验（枚举/字段/数值为整数，伤害以 HP 计） |
| `to_engine_tags()` | spec → 引擎 tag；**引擎未建模的动作如实列入 `unsupported`**（不假装支持） |
| `data/k2_golden.json` | **手工标注**黄金集（50 条 = 26 真实卡池 + 14 合成 + 10 留出）；`expect` 只声明关心的字段 |
| `evaluate(backend)` | 逐字段准确率 + **seen / holdout 分组**（留出句式才是泛化估计） |

### 合规：AI 标识与数据隐私

| 项 | 实现 |
|---|---|
| **AI 标识（6.1）** | `parse_effects()` 返回 `ai_generated=True` / `generated_by=rule\|llm` / `ai_notice`；报告与 API 复用同一文案，UI 显示徽标 |
| **外发脱敏（6.2）** | `agent/privacy.py` → `desensitize()`：邮箱·手机·证件·卡号·API 密钥·`Authorization`·本地路径·自定义敏感词 |
| **本地模式** | `POKELAB_LOCAL_ONLY=1` → `check_endpoint()` 拒绝外部端点（`PrivacyError`），解析回退规则基线并**如实报告**（`rule(privacy-blocked)`） |
| **调用审计** | 每次外部 LLM 调用写 JSONL（端点/模型/字符数/脱敏计数/**不含原文**），`POKELAB_AUDIT=0` 可关闭，`/compliance/audit` 可查 |
| **语言分流** | 文本含中日韩字符 → 中文句式解析；否则走 **英文卡面路径**（真实卡池为英文，`_parse_rule_en`） |

```bash
POKELAB_LOCAL_ONLY=1 python examples/run_k2.py --backend llm   # 外部端点被拒 → 自动回退 rule
python -c "from agent import privacy as p; print(p.desensitize('邮箱 a@b.com 手机 13812345678'))"
```

### 评测（K2 主指标：严格准确率）

```bash
cd packages/agent
python examples/run_k2.py                       # 规则基线 + 真实卡池解析
python examples/run_k2.py --no-pool --strict     # 只跑黄金集，未达 85% 退出码 1
# LLM 后端（任选其一：DeepSeek / 本地 OpenAI 兼容服务）
POKELAB_LLM_BASE=https://api.deepseek.com/v1 POKELAB_LLM_KEY=sk-xxx \
POKELAB_LLM_MODEL=deepseek-chat python examples/run_k2.py --backend llm
```

产物：`artifacts/k2_report.md`（人读：指标表 / 逐例 ✓✗ / 失败用例=待补句式 / 真实卡池 tag 映射 / 诚实标注）
+ `artifacts/k2_report.json`（机读，**M6 看门狗 K2 直接读取**）。

> 指标口径见报告 §1；`seen` 高只说明回归稳定，请以 **holdout** 作为泛化参考。
> 当前规则基线：严格 94%（seen 100% / holdout 70%，n=50）；**LLM 数字需配 Key 实测**，报告不臆测。

## 运行

```bash
# 用例
cd packages/engine && python -m unittest discover -s tests -v   # 66 项（含 E5）
cd packages/agent && python -m unittest discover -s tests -v   # 19 项（含 RL smoke）

-- 所有训练/评估 CLI：--device 默认 auto → 有 CUDA 自动用 GPU（RTX 4050），无卡/显存<1.2GB 自动回退 CPU（agent.utils.resolve_device）

# 决策演示（MCTS 30 迭代，输出 top3 审计与选中动作）
cd packages/agent && python examples/demo_decision.py 30

# RL 自对弈训练（PPO）与评估
cd packages/agent
python examples/train_ppo.py --episodes 60 --decks lightning --save artifacts/policy.pt
python examples/train_ppo.py --eval --weights artifacts/policy.pt --games 20

# 两阶段训练：BC 预热 → PPO 续训 → 周期 vs baseline 评估（推荐路径）
cd packages/agent
python examples/train_pipeline.py --bc-episodes 40 --ppo-episodes 200 --save artifacts/final.pt
#   产物：artifacts/final.pt（权重）+ artifacts/curve.json（winrate 曲线）

# 局面得分 shaping（稀疏 ±1 + 每步公开信息估值；reward.state_reward）
python examples/train_pipeline.py --ppo-episodes 500 --reward-shaping 0.1 \
    --save artifacts/ppo_shape.pt

# DQN 对照训练（技术方案：PPO 主选 / DQN 对照；MC 目标 + 目标网）
python examples/train_dqn.py --episodes 300 --opponent baseline --save artifacts/dqn.pt
python examples/train_dqn.py --eval --weights artifacts/dqn.pt --games 20

# 交叉评测矩阵：胜率(vs 基线/随机) + 决策延迟 K3（mean/P95/max）
python examples/eval_matrix.py --games 24 --mcts 5 --mcts 20 \
    --weights artifacts/final_k1.pt --out artifacts/eval_matrix.md

# 解析提示词模板结构回归（dry-run，K2 工程侧基线）
python examples/check_parse_templates.py

# 特征对比实验（legacy 51d vs full 59d，同预算；产物 eval_matrix_full.md）
python examples/compare_features.py --ppo-episodes 1500

# 阶段3：MCTS 教练蒸馏（阶段2 后进阶；MCTS 决策蒸馏进策略）
python examples/train_pipeline.py --ppo-episodes 2000 --stage2-episodes 1000 \
    --stage3-episodes 300 --stage3-mcts-iters 8 --stage3-bc-epochs 6 \
    --save artifacts/stage3.pt

# 特征域对比实验（bad-case 迭代）：full = 51→59 维（hand+meta），训练/评估须同配置
python examples/train_pipeline.py --features full --bc-episodes 150 \
    --ppo-episodes 8000 --save artifacts/policy_full.pt
python examples/eval_matrix.py --features full --weights artifacts/policy_full.pt \
    --games 30 --out artifacts/eval_matrix_full.md
```

# 长训实测（2026-09-08，镜像同卡组 lightning，CPU）：
#   BC acc 0.92 → eval vs baseline 0.46
#   vs-fixed 3000 局（58s）in-run 0.48→0.57 → eval vs baseline 0.62 > 基线基准 0.54
#   ⇒ 对手已"成长"到稳定超越基线 AI；发布级 ≥70% 需更长训练/更强课程（数千至上万局）。
# 诊断提示：跨卡组对位（如 lightning vs fire）先看 baseline vs baseline 基准——
#   fire 天然优开时 policy 0.17 ≈ 卡组天花板（基线仅 0.33），勿误判为策略退化。
# 阶段2 课程实测（3500 vs baseline + 2000 vs 冻结快照，seed 21）：
#   vs frozen 首段 win 0.70（新策略快速压过冻结旧策略 → 学习机制有效）；
#   eval vs baseline 逐段 0.50→0.55→0.60→0.60；n=40 高置信：policy 0.625 vs 基线基准 0.600。
#   ⇒ policy ≈ 略强于基线；K1 ≥70% 尚未达成（诚实结论），继续方向见 README「冲 70% 手册」。
# 用法：
#   python examples/train_pipeline.py --ppo-episodes 3500 --stage2-episodes 2000 \
#       --stage2-snapshot-every 500 --save artifacts/final_k1.pt
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
- **冲 K1 ≥70% 手册（按投入排序）**：
  1. 大样本评估降噪：eval n≥100（基线基准 ±0.05 噪声内勿下结论）；
  2. 更久训练：`--ppo-episodes 10000+ --stage2-episodes 5000+`（CPU ~20ms/局，可后台数小时）；
  3. 超参：lr 余弦衰减 / entropy 更高(0.05)防局部 / clip 0.3；
  4. 容量：网络 hidden 64→128、状态编码补入更多特征（hand 构成统计等）；
  5. 课程扩展：冻结快照刷新更频繁（AlphaZero 迭代式），阶段3 引入 MCTS 教练（stochastic
     rollouts 指导）；
  6. 验收口径对齐：K1 定义"vs 规则基线 ≥70%"需固定基线实现版本与 200 局双盲协议
     （技术方案 §1），跨卡组须用公平对位（镜像/轮换先手）。
- LangGraph 为可选依赖：纯编排 `decide()` 零依赖可本地跑；`pip install langgraph` 后可用
  StateGraph 形态（行为一致，有单测对齐）；
- LLM（本地 Qwen3-4B 8bit / 云端 OpenAI 兼容）接入见 `prompts/`、`llmclient.py`
  与 `commentary.py`；**统一出口是 `llmclient.chat()`**（端点校验 → 脱敏 → 调用 → 审计），
  任何调用点都不得绕过它直连外部端点。

  **本地 Qwen3-4B 落地方式**（ADR-LLM-001：解析/解说统一本地，K2 不达标切云端保底）：
  起一个 OpenAI 兼容的本地端点即可，无需改本包代码——

  ```bash
  cd apps/api && python scripts/local_qwen.py serve          # 127.0.0.1:9997
  export POKELAB_LLM_BASE=http://127.0.0.1:9997/v1           # 或后台「模型配置」页填
  ```

  本机端点被 `privacy.is_external()` 判为**非外发**，故 `POKELAB_LOCAL_ONLY=1`
  （完全离线）下**依然可用**——这是 6.2「支持本地化部署」的实际路径。
  量化档位（8bit 默认 / 4bit 可选）与三层精度对照见 `apps/api/README.md` §4.10。
