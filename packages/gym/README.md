# packages/gym · 博弈智能教学环境

宝可梦 TCG 对战的**单智能体** RL/教学环境。把对局封装成标准 `reset/step` 接口，
智能体只在自己的回合决策，对手由内置 AI 自动走子。

- **零运行时依赖**：仅依赖 `pokelab-engine`（不依赖 gymnasium / numpy / torch）。
- **动作空间**：当前合法动作的**离散下标**（`legal_actions()` 的长度动态变化）。
- **观测**：`Battle.view(side)` 玩家视角 dict（含手牌/场面/合法动作，对手手牌只给数量）。

## 快速开始

```python
from pokegym import PokelabEnv, run_episode

env = PokelabEnv(deck_a="lightning", deck_b="fire", opponent="baseline", seed=7)
obs, info = env.reset()
done = False
while not done:
    idx = env.sample_action_index()          # 用你自己的策略替换
    obs, reward, terminated, truncated, info = env.step(idx)
    done = terminated or truncated
print(info)                                   # {winner, turn, reward, ...}
```

- `deck_a`/`deck_b` 可传卡组 id 字符串（`"lightning"` / `"A1"` …，见
  `engine.decklib.make_deck`）或引擎卡组列表。
- `opponent`：`"baseline"`（K1 参照 AI）/ `"random"` / `"support"`（会用训练家与特性）。
- `step` 也接受引擎动作 dict（必须 ∈ 当前 `legal_actions()`）。

## 对手 benchmark

```bash
cd packages/gym
python examples/run_benchmark.py 100    # 内置对手两两胜率矩阵 + 先手参照线
```

## 测试

```bash
cd packages/gym && python -m unittest discover -s tests
```

## 设计说明（为何不直接用 gymnasium）

本仓定位是「可离线自证、少依赖」。gymnasium 引入一整套 numpy/云 pickle 依赖链，
对本环境（观测是结构化 dict、动作是变长离散下标）收益很小。因此这里只**对齐**
gymnasium 的 `reset/step` 五元组约定，方便日后一层薄适配器迁入 gymnasium；
不引入其运行时依赖。

## 许可

Apache-2.0（与 engine 一致）。宝可梦相关素材/命名属权利方所有，本包仅用
引擎内置的自述卡名与机制，不含官方卡图（合规 C1/C5）。
