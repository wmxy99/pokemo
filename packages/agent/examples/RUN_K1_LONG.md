# PokéLab AI · K1 长训运行指引（用户本机执行）

> 背景：沙箱会话无法跨轮维持数小时后台进程（两次尝试均被环境清理）。
> 本文件为**在本机终端**运行长训的命令与说明。产物权重与曲线在用户本机留存。

## 一、推荐命令（镜像同卡组 · 课程式 BC→vs基线→冻结自对弈）

```bash
cd <repo>/packages/agent
# 使用项目 conda 环境（已装 GPU torch）
conda activate pokemo

# 阶段1（vs 规则基线 8 万局）+ 阶段2（对冻结快照自对弈 4 万局）
python -u examples/train_pipeline.py \
  --decks lightning \
  --bc-episodes 150 --bc-epochs 25 \
  --ppo-episodes 80000 --ppo-epochs 3 \
  --lr 3e-3 --ent-coef 0.05 --clip 0.3 --hidden 128 --lr-decay 0.1 \
  --eval-every 20000 --games 30 \
  --device cuda \
  --save artifacts/final_gpu_k1.pt \
  --stage2-episodes 40000 --stage2-snapshot-every 20000 \
  --seed 31 > artifacts/train_gpu_k1.log 2>&1 &

# 查看进度
tail -f packages/agent/artifacts/train_gpu_k1.log
# 每 2 万局会打印一次 [eval:...] vs baseline win_rate
```

## 二、说明与建议

| 项 | 说明 |
|----|------|
| BC 缓存 | `artifacts/bc_data_lightning_mirror.pt`（3459 帧）可直接复用（cache hit） |
| 实测样本（本沙箱已产出） | BC 3459 帧 · 25 epochs → **acc 0.995**；eval vs baseline **0.60 (18/30)** —— 起点高于前轮 0.50 版本，长训冲 0.70 更有基础 |
| 引擎 CPU-bound | 数据收集是 Python 引擎循环，GPU 仅加速网络前向/反向；加速比约 1.3–2× |
| 预计时长 | 8 万+4 万局：CPU ~1.2–2h / GPU ~40–70min（本机 RTX 4050） |
| 评估噪声 | 每轮 eval games=30；最终看 **n=40–100**（可另跑 `--eval` 模式） |
| 产物 | 权重 `artifacts/final_gpu_k1.pt`；曲线 `artifacts/curve.json`（`.gitignore` 已排除） |

## 三、评估单独命令

```bash
python examples/train_pipeline.py --decks lightning --mirror \
  --eval --weights artifacts/final_gpu_k1.pt --games 60 --opponent baseline
```

## 四、判断是否跨过 0.70

看日志最后的 `[eval:ppo-done] vs baseline win_rate`（games 建议 ≥60 再下结论），
并对照同参数 `baseline vs baseline` 基准行 `[ref] ... win_rate_a`（镜像应为 ~0.5±噪声）。
