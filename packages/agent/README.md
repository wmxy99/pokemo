# packages/agent · AI 决策链路（v0.1）

宝可梦 TCG 对战决策模块 —— 状态编码 → 策略网络 → MCTS → 决策流水线（LangGraph 编排）
+ 提示词模板库（LLM 卡牌效果解析 / 对战解说）。对应技术方案 v1.0 模块 2/3 的 P2 起点。

## 结构

| 模块 | 职责 |
|------|------|
| `agent/state_encoder.py` | `view()` → 定长状态向量（**基础 51 维**）+ 动作特征（基础 **14 维**；`action_features="rich"` → **31 维**，额外编码"被作用实体"的属性）；`EncoderConfig`/`set_active_encoder`；`action_dim()`（构网用它，非常量）；`infer_config_from_state_dict()`（从权重反推编码配置）|
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
#   ⇒ policy ≈ 略强于基线；**K1 已于 2026-09-18 达标（双盲 0.790）**，见下节「K1 · 已达标」。
# 用法：
#   python examples/train_pipeline.py --ppo-episodes 3500 --stage2-episodes 2000 \
#       --stage2-snapshot-every 500 --save artifacts/final_k1.pt
```

## K1 · 对规则基线胜率（**已达标**，2026-09-18）

**判据（唯一）：双盲 200 局（交替先后手）= 0.790**（`artifacts/K1_accept_rich.md`，
95%CI ±0.056，下界 0.734 > 0.70）。关键改动是 **`--action-features rich`**
（动作特征 14 → 31 维）：

| 权重 | 动作特征 | 双盲 n=200（seed0=0） | 独立 seed 段（seed0=1000） |
|---|---|---:|---:|
| `k1_cont40k` | basic 14 维 | 0.695 | 0.655 |
| `k1_basic707`（**同 seed 对照**） | basic 14 维 | 0.695 | — |
| **`k1_rich`** | **rich 31 维** | **0.790 ✅** | **0.775 ✅** |
| `k1_richfull`（+full 状态 59+31） | rich 31 维 | **0.800**（点估计最高） | 0.790 |

原 14 维里 `index/attack/target` 只是**原始下标归一化**，而下标与卡牌身份无关 ——
同一局面 `attach` 的 5 个候选**只差一个下标位**，网络无从知道"能量该附给谁"。
rich 块把「被作用实体」的公开属性（剩余 HP / 能量数 / 阶级 / 被打倒风险 / 招式伤害…）
编码进去后，**同 seed 同超参下 +9.5pp**，且：

- **换卡组复现**：fire 镜像上 rich **0.815** vs basic **0.720**（seed0=0）、
  rich **0.780** vs basic **0.675**（seed0=1000）→ **+9.5 / +10.5pp**
- **换对手保持**：rich vs `support`（会用训练家/特性的强化规则 AI）= **0.790**，
  与 vs `baseline` 完全相同
- **是真强度不是对手偏好**：`rich vs basic` head-to-head（双盲、双方都是学习策略）
  = **0.720**；唯一例外是 vs `random` 只差 +3pp（噪声内）

**已成否定性结论（不必重复投入）**：加训练量（含 rich 续训到 40k 局）、full 状态特征
单独使用、自对弈细化、宽网络 hidden=128、高熵低 LR、多权重集成 —— 双盲全部落
**0.685–0.700**（统计不可区分）；**搜索（MCTS）无增益**：价值头标定 + 多 ply 之后
最好也只追平网络（0.700），rich 下甚至更低（0.780 < 0.790）→ 产品默认"网络直接出招"
（延迟也最优）。详见 `examples/RUN_K1_LONG.md`（含全部实测表格与判据）。

迁移性验证可复现：`python examples/k1_migrate.py --games 200`
（两次独立运行逐项完全一致 → 评估链路确定性）。

复现与验收：

```bash
python examples/run_k1_longtrain.py --action-features rich --stage1-episodes 20000 \
    --reward-shaping 0.0 --eval-every 10000 --games 60 --final-games 200 \
    --save artifacts/k1_rich.pt --report artifacts/k1_rich.md
python examples/k1_accept.py artifacts/k1_rich.pt --games 200    # 只看双盲值
python examples/k1_accept.py artifacts/k1_rich.pt --games 200 --seed0 1000   # 独立 seed 复验
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
- **K1 ≥70%（2026-09-18 已达标：双盲 0.790）** —— 上面这份"按投入排序"的清单已被实测
  逐项验证，结论与当时的猜测并不一致（保留原文以便对照）：
  1. 大样本评估降噪 ✅ 必要（且**必须双盲**：单侧口径会把先手优势算成战绩，虚高 4–6pp）；
  2. 更久训练 ❌ 无效（20k→40k 局仍是 0.695，且 15k 局曾因 shaping 退化到 0.61）；
  3. 超参 ❌ 无效（lr/entropy/clip 各组合双盲全落 0.685–0.700）；
  4. 容量 ⚠️ 部分无效：hidden 64→128 无增益；**真正有效的是"动作特征"**
     （`--action-features rich`，14→31 维，**+9.5pp**）—— 原清单只想到"状态特征"，
     漏掉了"动作特征没给作用对象"这个更根本的缺口；
  5. 课程扩展 ❌ 自对弈细化中性（7 个快照双盲 0.685–0.700）；
  6. 验收口径 ✅ 已落地为 `examples/k1_accept.py`（双盲 200 局、退出码 0/1/2、
     可 `--seed0` 换段复验，并支持 `--mcts-*` 评估搜索配置）。
  完整实测表与判据见 `examples/RUN_K1_LONG.md`。
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
