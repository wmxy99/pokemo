# -*- coding: utf-8 -*-
"""PokéLab AI · K1 长训一体化脚本（供本机运行）

一条命令跑完整课程并给出 K1(≥70% vs 规则基线) 判定报告：
  阶段0 BC 预热（自动复用缓存）→ 阶段1 vs 规则基线(PPO)
  → 阶段2 冻结快照自对弈 → 阶段3 MCTS soft 蒸馏
  → 结束高置信评估(n=--final-games) vs 基线/随机 + 基线自对局参照 → K1_report.md

特性：设备默认 GPU(auto，显存不足回退 CPU)；周期权重落盘与进度 ETA；
     --from-weights <pt> 可跳过 BC 续跑（断点续训的近似入口）。

用法（Windows Git Bash / PowerShell 均可在 packages/agent 下）：
  conda activate pokemo
  python -u examples/run_k1_longtrain.py                    # 默认课程（约 1.5–3h）
  python -u examples/run_k1_longtrain.py --stage1-episodes 5000 \
      --stage2-episodes 3000 --stage3-episodes 100 --final-games 40   # 短试跑
产物：artifacts/k1_policy.pt（最终权重）· artifacts/K1_report.md（判定报告）

默认值 = `examples/RUN_K1_LONG.md` §四「当前最佳配置」（2026-09-18 实测）：
  `--lr 3e-4 --ent-coef 0.005 --clip 0.2 --reward-shaping 0.0`
  `--stage1-episodes 20000 --stage2-episodes 0 --stage3-episodes 0`

⚠ **不要**改回 2026-09-09 的旧默认（lr 3e-3 / shaping 0.05 / clip 0.3 /
ent 0.05 / stage2 24000）。手册 §三 的结论 ②③ 已实测它们有害：
奖励塑造 0.05 让 4000 局胜率从 0.67 掉到 0.64、15k 局更会崩到 0.52–0.61。
2026-11-09 又复现了一次：用旧默认跑到 stage1 10000 局时双盲仍只有 **0.350**
（同环境改成上面的配置后 10k 局即回到 0.7+ 区间）。

预期总时长（RTX 4050 笔记本，引擎收集 CPU-bound）：
  BC 150 局 ≈ 1min · stage1 20000 局 ≈ 30–45min · 终评 200 局 ≈ 1–2min
"""
from __future__ import annotations
import argparse
import json
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
for pkg in ("engine", "agent"):
    sys.path.insert(0, str(_ROOT / "packages" / pkg))

from engine import decklib
from engine.agents import BaselineAI, RandomAgent
from agent.state_encoder import (EncoderConfig, action_dim, encoder_dim,
                                 set_active_encoder)
from agent.policy_net import make_net
from agent import bc, trainer, distill
from agent.utils import resolve_device
from agent.paths import portable_path


def main():
    ap = argparse.ArgumentParser(description="K1 长训一体化脚本")
    ap.add_argument("--decks", default="lightning")
    ap.add_argument("--bc-episodes", type=int, default=150)
    ap.add_argument("--bc-epochs", type=int, default=25)
    ap.add_argument("--stage1-episodes", type=int, default=20000)
    ap.add_argument("--stage2-episodes", type=int, default=0)
    ap.add_argument("--stage2-snapshot-every", type=int, default=2000)
    ap.add_argument("--stage3-episodes", type=int, default=0)
    ap.add_argument("--stage3-mcts-iters", type=int, default=10)
    ap.add_argument("--stage3-bc-epochs", type=int, default=6)
    ap.add_argument("--ppo-epochs", type=int, default=3)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--gamma", type=float, default=0.99)
    ap.add_argument("--clip", type=float, default=0.2)
    ap.add_argument("--ent-coef", type=float, default=0.005)
    ap.add_argument("--lr-decay", type=float, default=0.1)
    ap.add_argument("--lr-schedule", choices=["linear", "cosine"],
                    default="cosine",
                    help="lr 调度：cosine（手册推荐）/ linear")
    ap.add_argument("--hidden", type=int, default=128)
    ap.add_argument("--reward-shaping", type=float, default=0.0)
    ap.add_argument("--features", choices=["legacy", "full"], default="legacy")
    ap.add_argument("--action-features", choices=["basic", "rich"],
                    default="basic",
                    help="动作特征：basic=16 维（9 动词含 choose/stop + 下标归一化）；"
                         "rich=33 维，额外编码「被作用实体」的属性（HP/能量/阶级/"
                         "被打倒风险/招式伤害…）。实测 basic 下 attach 的 5 个候选"
                         "只差一个下标 → 策略**分不清该把能量附给谁**。"
                         "换 rich 必须从头训练（不能续跑旧权重）。")
    ap.add_argument("--eval-every", type=int, default=5000)
    ap.add_argument("--no-alternate", action="store_true",
                    help="关闭「交替先后手」训练（默认开启）—— 默认开启是因为"
                         "K1 双盲口径要求策略会打后手，只训先手会白丢一半对局")
    ap.add_argument("--games", type=int, default=30)
    ap.add_argument("--final-games", type=int, default=200,
                    help="终评样本量。**默认 200 = PRD 验收协议**（双盲口径）；"
                         "训练期调小可加快迭代")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--device", default="auto")
    ap.add_argument("--save", default="artifacts/k1_policy.pt")
    ap.add_argument("--report", default="artifacts/K1_report.md")
    ap.add_argument("--from-weights", default=None,
                    help="从已有权重续跑（跳过 BC；stage1..3 按给定局数继续）")
    args = ap.parse_args()
    args.device = resolve_device(args.device)
    t_all = time.time()

    da = decklib.make_deck(args.decks)
    db = list(da)
    _feats = ("hand", "meta") if args.features == "full" else ()
    enc_cfg = (EncoderConfig(features=_feats,
                             action_features=args.action_features)
               if (_feats or args.action_features != "basic") else None)
    set_active_encoder(enc_cfg)
    sdim = encoder_dim()
    adim = action_dim()
    print(f"[k1] decks={args.decks}(mirror) features={args.features}"
          f"+{args.action_features} state_dim={sdim} action_dim={adim} "
          f"device={args.device} seed={args.seed}")
    Path(args.save).parent.mkdir(parents=True, exist_ok=True)

    net = make_net(sdim, action_dim(), hidden=args.hidden, device=args.device)
    n_start = 1
    if args.from_weights:
        # 宽度从 checkpoint 推断（隐藏层数不一致时 load_state_dict 会直接报错，
        # 此前必须先知道训练时用的 --hidden；见 trainer.load_net 的同源修复）
        _sd = torch_load(args.from_weights, args.device)
        if "ctx.0.weight" in _sd:
            inf_hidden = int(_sd["ctx.0.weight"].shape[0])
            if inf_hidden != args.hidden:
                print(f"[k1] 检测到 checkpoint 宽度 hidden={inf_hidden}"
                      f"（命令行为 {args.hidden}）→ 以 checkpoint 为准")
                args.hidden = inf_hidden
                net = make_net(sdim, action_dim(), hidden=args.hidden,
                               device=args.device)
        # ⚠ 编码器换了（state/action 维度变）就不能续跑旧权重 —— 必须显式报错，
        # 否则 load_state_dict 的形状不匹配会给出难以定位的报错（或更糟：静默错位）。
        _sd_shape = _sd["ctx.0.weight"].shape[1]
        if int(_sd_shape) != sdim:
            raise SystemExit(
                f"权重 {args.from_weights} 的 state_dim={int(_sd_shape)} 与当前"
                f"编码配置的 {sdim} 不一致（features={args.features}"
                f"+{args.action_features}）→ 该配置必须**从头训练**，不能续跑。")
        _sd_adim = _sd["action_enc.0.weight"].shape[1]
        if int(_sd_adim) != adim:
            raise SystemExit(
                f"权重 {args.from_weights} 的 action_dim={int(_sd_adim)} 与当前"
                f"编码配置的 {adim} 不一致（S1b 之后动词表含 choose/stop，"
                f"旧权重是 14/31）→ 该配置必须**从头训练**，不能续跑。")
        net.load_state_dict(_sd)
        print(f"[k1] 续跑：已加载 {args.from_weights}（hidden={args.hidden}），跳过 BC")
    else:
        t0 = time.time()
        # 缓存名**含编码模式**：帧里存的是*编码后*向量（state_vec/feats_all），
        # 换模式复用会维度不符（故 base/full 之外还要区分 action_features）。
        # ⚠ 缓存名里带**动作维度**（S1b 起）：帧存的是编码后向量，动词表一变
        #   （14/31 → 16/33）缓存就作废。名字里带维度能让「为什么没命中缓存」
        #   一眼可见，而不是靠下面那句维度校验报错去猜。
        cache = (f"artifacts/bc_data_{args.decks}_mirror_"
                 f"{'full' if _feats else 'base'}"
                 f"{'_rich' if args.action_features == 'rich' else ''}"
                 f"_a{adim}.pt")
        # ⚠ 缓存有效性校验：只按「文件存在」复用会踩到**被截断的缓存** ——
        # 实测 `bc_data_lightning_mirror_full.pt` 只有 41 帧（冒烟残留），
        # 静默复用等于 BC 没做（先验≈弱随机），却**照样跑完不报错**。
        # 故低于阈值即判无效并重采。实测约 **57 帧/局**（3459 帧 / 60 局），
        # 取 `5×episodes` 作下限：宁可多采一次（秒级），也不要拿废缓存训一整轮。
        min_frames = max(50, args.bc_episodes * 5)
        frames = None
        if Path(cache).exists():
            loaded = bc.load_frames(cache)
            # 维度校验：帧里是编码后向量 → 必须与当前编码配置一致
            ok_dim = (loaded and len(loaded[0].get("state_vec") or []) == sdim
                      and len((loaded[0].get("feats_all") or [[]])[0]) == adim)
            if len(loaded) >= min_frames and ok_dim:
                frames = loaded
                print(f"[bc] cache hit {cache} ({len(frames)} frames)")
            elif not ok_dim:
                raise SystemExit(
                    f"BC 缓存 {cache} 的向量维度与当前编码配置不符"
                    f"（应为 state={sdim}/action={adim}）→ 请换缓存名或删除重采。")
            else:
                print(f"[bc] ⚠ 缓存 {cache} 仅 {len(loaded)} 帧"
                      f"（< {min_frames}）→ 判为无效，重新采集")
        if frames is None:
            frames = bc.collect_demonstration(da, db, BaselineAI("bA"),
                                              BaselineAI("bB"),
                                              episodes=args.bc_episodes,
                                              seed=args.seed)
            bc.save_frames(frames, cache)
        st = bc.train_bc(net, frames, epochs=args.bc_epochs, lr=args.lr,
                         device=args.device, seed=args.seed)
        print(f"[bc] frames={st['frames']} acc={st['final_acc']:.3f} "
              f"({time.time()-t0:.0f}s)")
        n_start = 1

    curve = {"eval": [], "total_s": None, "passed_k1": None}

    def _save():
        import torch
        torch.save(net.state_dict(), args.save)

    def _eval(tag):
        # **中途评估也用双盲**（交替先后手）：单侧口径会把先手优势（≈+6pp）
        # 算进战绩，导致训练期曲线与终评口径不可比（历史上因此误判过进度）。
        r = trainer.eval_winrate_blind(da, db, net, n=args.games,
                                       opponent="baseline", device=args.device)
        curve["eval"].append({"tag": tag, **r})
        _save()
        print(f"[k1:eval:{tag}] vs baseline 双盲 win_rate={r['win_rate']:.3f} "
              f"({r['wins']}/{r['n']}, 参照线 {r.get('ref_first_winrate', 0):.3f})"
              f" [{time.time()-t_all:.0f}s]")

    # ---- 阶段1：vs 规则基线（PPO）----
    if args.stage1_episodes > 0:
        t1 = time.time()
        st1 = trainer.train_ppo_vs_fixed(
            da, db, net, opponent=BaselineAI("baseline"),
            episodes=args.stage1_episodes, ppo_epochs=args.ppo_epochs,
            clip=args.clip, lr=args.lr, gamma=args.gamma,
            ent_coef=args.ent_coef, lr_decay=args.lr_decay,
            lr_schedule=args.lr_schedule,
            shaping=args.reward_shaping,
            alternate_sides=not args.no_alternate,
            eval_fn=lambda ep: _eval(f"stage1-{ep}"),
            eval_every=args.eval_every, seed=args.seed,
            device=args.device, save_path=args.save,
            log_every=max(1, args.stage1_episodes // 8))
        print(f"[k1] stage1 done {st1['episodes']} ep in "
              f"{time.time()-t1:.0f}s | wins={st1['wins']}"
              f"/{st1['episodes']} "
              f"({st1['wins']/max(1, st1['episodes']):.3f})")
    # ---- 阶段2：冻结快照自对弈 ----
    if args.stage2_episodes > 0:
        from agent.policy_agent import PolicyAgent
        seg = args.stage2_snapshot_every
        ran = 0
        while ran < args.stage2_episodes:
            n = min(seg, args.stage2_episodes - ran)
            t2 = time.time()
            frozen = trainer.clone_net(net, args.device)
            opp = PolicyAgent(frozen, device=args.device, name="frozen")
            trainer.train_ppo_vs_fixed(
                da, db, net, opponent=opp, episodes=n,
                ppo_epochs=args.ppo_epochs, clip=args.clip, lr=args.lr,
                gamma=args.gamma, ent_coef=args.ent_coef,
                lr_decay=args.lr_decay, lr_schedule=args.lr_schedule,
                shaping=args.reward_shaping,
                # ⚠ 与 stage1 同源：不接交替座位就**只训先手**，而 K1 有一半局是后手
                # （实测后手 0.620 vs 先手 0.680 —— 后手就是主缺口，必须一起训）。
                alternate_sides=not args.no_alternate,
                seed=args.seed, device=args.device, save_path=args.save,
                log_every=max(1, n // 4))
            ran += n
            _eval(f"stage2-{ran}")
            # 分段快照：`--save` 只保留**最后**状态，而自对弈可能中途变好、
            # 末尾漂移（实测自对弈段容易出现"越练越差"）。留下每段权重才能回选。
            snap = args.save.replace(".pt", f"_s2{ran}.pt")
            import torch as _t
            _t.save(net.state_dict(), snap)
            print(f"[k1] stage2 +{n} vs frozen in {time.time()-t2:.0f}s "
                  f"→ 快照 {snap}")
    # ---- 阶段3：MCTS soft 蒸馏 ----
    if args.stage3_episodes > 0 and args.stage3_mcts_iters > 0:
        seg = max(1, args.stage3_episodes // 4)
        ran3 = 0
        while ran3 < args.stage3_episodes:
            n = min(seg, args.stage3_episodes - ran3)
            t3 = time.time()
            frames = distill.collect_mcts_expert(
                da, db, opponent=BaselineAI("baseline"), episodes=n,
                iters=args.stage3_mcts_iters, seed=args.seed, soft=True)
            st3 = bc.train_bc_soft(net, frames,
                                   epochs=args.stage3_bc_epochs,
                                   lr=args.lr, device=args.device,
                                   seed=args.seed)
            ran3 += n
            _eval(f"stage3-{ran3}")
            print(f"[k1] stage3 +{n} mcts-soft demos in {time.time()-t3:.0f}s "
                  f"frames={st3['frames']} loss={st3['final_loss']:.4f}")
    _save()

    # ---- 结束高置信评估 + K1 判定 ----
    from engine.simulator import run_many
    ref = run_many(da, db, BaselineAI("bA"), BaselineAI("bB"),
                   n=args.final_games)
    # **K1 判定口径 = 双盲（交替先后手）**：本引擎先手优势显著（基线自对局先手约 0.56–0.61），
    # 单侧口径会把先手优势算进战绩 → 与基线打平的策略也能显示 ~0.61。
    ev_blind = trainer.eval_winrate_blind(da, db, net, n=args.final_games,
                                          opponent="baseline",
                                          device=args.device)
    ev_b = trainer.eval_winrate(da, db, net, n=args.final_games,
                                opponent="baseline", device=args.device)
    ev_r = trainer.eval_winrate(da, db, net,
                                n=max(20, args.final_games // 2),
                                opponent="random", device=args.device)
    p = ev_blind["win_rate"]
    ci_half = 1.96 * (p * (1 - p) / max(1, ev_blind["n"])) ** 0.5
    passed = p >= 0.70
    curve["total_s"] = round(time.time() - t_all, 1)
    curve["passed_k1"] = passed
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# PokéLab AI · K1 长训报告", "",
        f"- 时间：{time.time():.0f} 生成 · 总耗时 {curve['total_s']}s · "
        f"device {args.device} · seed {args.seed}",
        f"- 配置：decks={args.decks}(镜像) features={args.features} "
        f"state_dim={sdim} · stage1={args.stage1_episodes} "
        f"stage2={args.stage2_episodes} stage3={args.stage3_episodes}"
        f"(mcts{args.stage3_mcts_iters}) · eval_every={args.eval_every} · "
        f"交替训练={'否' if args.no_alternate else '是'}", "",
        "## 最终评估（高置信）", "",
        "| 对手 | n | 胜率 | 95%CI |",
        "|---|---|---|---|",
        f"| **规则基线 AI（双盲 · K1 判定口径）** | {ev_blind['n']} | "
        f"**{p:.3f}** | ±{ci_half:.3f} |",
        f"| 规则基线 AI（单侧先手，仅供与历史报告对照） | {ev_b['n']} | "
        f"{ev_b['win_rate']:.3f} | — |",
        f"| 随机 AI | {ev_r['n']} | {ev_r['win_rate']:.3f} | — |",
        f"| 基线AI 自对局参照（先手胜率，用于校准先手优势） | "
        f"{ev_blind['ref_n']} | win_rate_a={ev_blind['ref_first_winrate']:.3f} | — |",
        "",
        f"> 双盲口径：{ev_blind['as_first']} 局先手 + {ev_blind['as_second']} 局后手，"
        f"先手优势被对称抵消 —— **这才是相对强度**。",
        "> 读表：双盲 ≈0.50 与基线同强；≈0.56–0.61 只是「稳定赢先手」；"
        "**≥0.70 才代表真正强于规则基线**。",
        "", "## 训练过程 vs baseline（单侧口径，训练期监控用）", "",
        "| 阶段 | 胜率 |", "|---|---|"]
    for e in curve["eval"]:
        lines.append(f"| {e['tag']} | {e['win_rate']:.3f} "
                     f"({e['wins']}/{e['n']}) |")
    lines += ["", "## K1 判定（PRD：≥70%，**200 局双盲口径为准**）", ""]
    if passed:
        verdict = "PASS ✅"
    else:
        verdict = (f"未达 0.70（差 {0.70 - p:.3f}）—— 继续长训或调参"
                   f"（见 examples/RUN_K1_LONG.md）")
    lines += [f"**结论：{verdict}**",
              f"- 口径：双盲 n={ev_blind['n']}（{ev_blind['as_first']} 先手 / "
              f"{ev_blind['as_second']} 后手）· 95%CI ±{ci_half:.3f} · "
              f"**PRD 要求 200 局**"
              + ("（已达协议局数）" if ev_blind["n"] >= 200
                 else f"（当前 {ev_blind['n']} 局，未达协议）"),
              f"- 权重：{portable_path(args.save)}",
              f"- 曲线：{portable_path(args.report.replace('.md', '_curve.json'))} 已由各阶段输出"]
    Path(args.report).write_text("\n".join(lines), encoding="utf-8")
    (Path(args.report).parent / "curve.json").write_text(
        json.dumps(curve, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n".join(lines[-8:]))


def torch_load(path, device):
    import torch
    return torch.load(path, map_location=device)


if __name__ == "__main__":
    main()
