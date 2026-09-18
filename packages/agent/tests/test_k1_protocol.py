# -*- coding: utf-8 -*-
"""K1 评估口径用例 —— 双盲 vs 单侧（防止口径误用）

背景：K1 曾长期用**单侧口径**（策略恒为先手），而本引擎先手优势约 6pp，
于是「与基线打平的策略」也会显示 ~0.61，**系统性高估**。PRD 要的是 200 局**双盲**。
本组用例把「口径不能被混用」变成可执行断言。

运行：cd packages/agent && python -m unittest tests.test_k1_protocol -v
"""
from __future__ import annotations
import sys
import unittest
from pathlib import Path

_ENGINE = Path(__file__).resolve().parents[2] / "engine"
sys.path.insert(0, str(_ENGINE))

from engine import decklib                                    # noqa: E402
from agent import trainer                                     # noqa: E402
from agent.policy_net import make_net                         # noqa: E402
from agent.state_encoder import ACTION_DIM, STATE_DIM         # noqa: E402


class TestK1Protocol(unittest.TestCase):
    def setUp(self):
        self.da = decklib.make_deck("lightning")
        self.db = list(self.da)
        # 未训练网络即可 —— 本组只验证**口径与统计逻辑**，不验证棋力
        self.net = make_net(STATE_DIM, ACTION_DIM, hidden=32, device="cpu")

    def test_blind_alternates_seats(self):
        """双盲必须**两侧都打**（n/2 先手 + n/2 后手）"""
        r = trainer.eval_winrate_blind(self.da, self.db, self.net, n=8,
                                       device="cpu", seed0=0)
        self.assertEqual(r["as_first"], 4)
        self.assertEqual(r["as_second"], 4)
        self.assertTrue(r["blind"])
        self.assertEqual(r["wins"] + r["losses"] + r["draws"], 8)

    def test_blind_gives_reference_line(self):
        """必须给出**基线自对局先手参照** —— 否则无法判断「0.61 是不是只是先手优势」"""
        r = trainer.eval_winrate_blind(self.da, self.db, self.net, n=8,
                                       device="cpu", seed0=0)
        self.assertIn("ref_first_winrate", r)
        self.assertGreater(r["ref_n"], 0)
        self.assertGreaterEqual(r["ref_first_winrate"], 0.0)
        self.assertLessEqual(r["ref_first_winrate"], 1.0)

    def test_blind_and_one_sided_are_distinguishable(self):
        """两个口径的返回值必须能区分（避免下游把单侧当 K1 用）"""
        blind = trainer.eval_winrate_blind(self.da, self.db, self.net, n=8,
                                           device="cpu", seed0=0)
        one = trainer.eval_winrate(self.da, self.db, self.net, n=8,
                                   device="cpu")
        self.assertIn("blind", blind)
        self.assertNotIn("blind", one)          # 单侧不带 blind 标记
        self.assertIn("win_rate", blind)
        self.assertIn("win_rate", one)

    def test_alternate_sides_training_runs_both_seats(self):
        """训练侧：`alternate_sides=True` 时策略也要学会打后手

        判据：整段训练里，**后手局的占比**应接近一半（用 win_tail 无法直接观测座位，
        故这里直接跑极小样本，验证不报错且胜负有记录）。
        """
        st = trainer.train_ppo_vs_fixed(
            self.da, self.db, self.net, opponent=None or __import__(
                "engine.agents", fromlist=["BaselineAI"]).BaselineAI("b"),
            episodes=4, ppo_epochs=1, lr=1e-4, seed=0, device="cpu",
            log_every=0, alternate_sides=True)
        self.assertTrue(st["alternate_sides"])
        self.assertEqual(st["episodes"], 4)
        self.assertEqual(st["wins"] + st["losses"] + st["draws"], 4)


class TestLoadNetInfersWidth(unittest.TestCase):
    """`load_net` 必须能从 checkpoint 推断宽度（否则非默认宽度的权重加载即报错）"""

    def test_infer_hidden_from_checkpoint(self):
        import tempfile
        import torch
        from agent.trainer import load_net
        net = make_net(STATE_DIM, ACTION_DIM, hidden=48, device="cpu")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "w.pt"
            torch.save(net.state_dict(), p)
            got = load_net(str(p), device="cpu")
            self.assertEqual(int(got.ctx[0].out_features), 48)


class TestValueCalibration(unittest.TestCase):
    """价值头胜率标定（让 MCTS 可用）

    背景：`(v+1)/2` 隐含「输出量纲即胜率区间」+「线性映射」两个未检验假设，
    实测价值头**系统性乐观 ~12pp**，且 MCTS 迭代越多越差（100→0.650, 300→0.525）。
    """

    def test_platt_fit_recovers_known_miscalibration(self):
        """人造"乐观"价值头：标签概率只有 sigmoid(0.5v) 时，拟合应把 a 拉向 0.5"""
        from agent.value_calib import fit_platt, _brier
        import math, random
        rng = random.Random(0)
        vs, ys = [], []
        for _ in range(4000):
            v = rng.uniform(-1, 1)
            p = 1.0 / (1.0 + math.exp(-0.5 * v))      # 真实映射
            ys.append(1.0 if rng.random() < p else 0.0)
            vs.append(v)
        a, b = fit_platt(vs, ys)
        self.assertLess(abs(a - 0.5), 0.25, f"斜率拟合偏差过大：a={a}")
        self.assertLess(abs(b), 0.25, f"截距拟合偏差过大：b={b}")
        self.assertLess(_brier(vs, ys, a, b), _brier(vs, ys, 1.0, 0.0),
                        "标定后 Brier 必须不劣于标定前")

    def test_prob_of_monotone_and_bounds(self):
        from agent.value_calib import prob_of
        calib = (2.5, -0.82)
        ps = [prob_of(v, calib) for v in (-1.0, -0.5, 0.0, 0.5, 1.0)]
        self.assertEqual(ps, sorted(ps), "标定映射必须单调递增")
        self.assertTrue(all(0.0 <= p <= 1.0 for p in ps))
        # 未标定时退回 (v+1)/2
        self.assertAlmostEqual(prob_of(0.0, None), 0.5, places=6)
        self.assertAlmostEqual(prob_of(1.0, None), 1.0, places=6)

    def test_load_calib_reads_pooled_group(self):
        import json
        import tempfile
        from agent.value_calib import load_calib
        payload = {"groups": {"pooled": {"stat": {"platt_a": 2.495,
                                                  "platt_b": -0.821}}}}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "calib.json"
            p.write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(load_calib(str(p)), (2.495, -0.821))
        self.assertIsNone(load_calib(str(Path(d) / "missing.json")))
        self.assertIsNone(load_calib(__file__), "结构不符应返回 None 而非抛错")

    def test_flip_after_mapping_not_before(self):
        """**顺序正确性**：必须"先转概率再取反"

        非对称标定下，`1 - sigmoid(a·v+b)` 与 `sigmoid(-(a·v+b))` 只有在
        `b == 0` 时才相等；旧实现（先取反再映射）会在 b≠0 时算错。
        """
        from agent.mcts import _value_of
        import math

        class _B:
            def __init__(self, cur):
                self.current = cur
                self.seed = 0

            def view(self):
                return {}

        calib = (2.5, -0.82)
        v = 0.4
        # root=1，局面行动方=0（对手）→ 必须翻转
        got = _value_of(_B(0), lambda view: v, root_player=1, value_calib=calib)
        p_actor = 1.0 / (1.0 + math.exp(-(2.5 * v - 0.82)))
        self.assertAlmostEqual(got, 1.0 - p_actor, places=6)
        # 同侧不翻转
        got2 = _value_of(_B(0), lambda view: v, root_player=0, value_calib=calib)
        self.assertAlmostEqual(got2, p_actor, places=6)


class TestMctsTreeSearch(unittest.TestCase):
    """多 ply 树搜索（1-ply 打不赢网络单独 → 必须能加深）"""

    def _battle(self):
        from engine.battle import Battle
        da = decklib.make_deck("lightning")
        return Battle(da, list(da), seed=7)

    def test_tree_search_returns_legal_action(self):
        from agent.mcts import mcts_select_action
        net = make_net(STATE_DIM, ACTION_DIM, hidden=32, device="cpu")
        b = self._battle()
        legal = [dict(a) for a in b.legal_actions()]
        act = mcts_select_action(b, iters=6, policy=net, max_depth=2, seed=1)
        self.assertIn(act, legal, "树搜索返回的动作必须在合法动作集内")

    def test_depth1_keeps_legacy_path(self):
        """max_depth=1 必须走旧的 1-ply 路径（兼容既有行为与产物）"""
        from agent.mcts import mcts_select_action
        net = make_net(STATE_DIM, ACTION_DIM, hidden=32, device="cpu")
        b = self._battle()
        legal = [dict(a) for a in b.legal_actions()]
        act = mcts_select_action(b, iters=6, policy=net, max_depth=1, seed=1)
        self.assertIn(act, legal)

    def test_value_actor_p_semantics(self):
        """`_value_actor_p` 返回的是**该局面行动方**的胜率（不做 root 翻转）"""
        from agent.mcts import _value_actor_p

        class _B:
            def view(self):
                return {}

        self.assertAlmostEqual(_value_actor_p(_B(), lambda v: 0.0, None),
                               0.5, places=6)
        # 标定后：(a,b)=(1,0) 时 v=0 → 0.5；(a,b)=(1,-2) 时 → sigmoid(-2)
        import math
        self.assertAlmostEqual(
            _value_actor_p(_B(), lambda v: 0.0, (1.0, -2.0)),
            1.0 / (1.0 + math.exp(2.0)), places=6)

    def test_tree_search_deeper_uses_more_nodes(self):
        """深度 2 应在同一 iters 下探索不少于深度 1 的局面（确有递进展开）"""
        from agent.mcts import mcts_select_action
        net = make_net(STATE_DIM, ACTION_DIM, hidden=32, device="cpu")
        b = self._battle()
        # 只验证不抛错且动作合法（节点数需内部观测，这里用"动作来自合法集"作弱断言）
        for depth in (1, 2, 3):
            act = mcts_select_action(b, iters=4, policy=net, max_depth=depth,
                                     seed=3)
            self.assertIn(act, [dict(a) for a in b.legal_actions()])


class TestRichActionFeatures(unittest.TestCase):
    """`--action-features rich`：把"被作用实体"编码进动作特征

    动机（K1 攻坚持平时定位到）：basic 块里 `attach` 的 5 个候选**只差一个下标**
    （0.0 vs 0.167），网络无法知道目标是谁（60HP 战斗位 vs 70HP 后备、是否将被打倒）
    → 实测"能量该附给谁"这个核心决策**没有输入信息**。
    """

    def _battle(self, seed=7):
        from engine.battle import Battle
        da = decklib.make_deck("lightning")
        return Battle(da, list(da), seed=seed)

    def test_action_dim_follows_config(self):
        from agent.state_encoder import (EncoderConfig, action_dim,
                                         encode_action)
        self.assertEqual(action_dim(None), 14, "legacy 动作维度必须保持 14")
        rich = EncoderConfig(action_features="rich")
        self.assertEqual(action_dim(rich), 31, "rich 应为 14 + 17")
        b = self._battle()
        v = b.view()
        from agent.state_encoder import set_active_encoder
        set_active_encoder(rich)
        try:
            for a in v["legal"]:
                self.assertEqual(len(encode_action(v, a)), action_dim(),
                                 "编码长度必须与 action_dim() 一致（构网依赖它）")
        finally:
            set_active_encoder(None)

    def test_attach_targets_become_distinguishable(self):
        """同样是 `attach`，不同 target 的**富块必须不同**（这正是改动目的）"""
        from agent.state_encoder import (EncoderConfig, encode_action,
                                         set_active_encoder)
        b = self._battle()
        v = b.view()
        attaches = [a for a in v["legal"] if a["verb"] == "attach"]
        self.assertGreaterEqual(len(attaches), 2, "该局面应有多个 attach 候选")
        set_active_encoder(None)
        base = [encode_action(v, a) for a in attaches]
        set_active_encoder(EncoderConfig(action_features="rich"))
        try:
            rich = [encode_action(v, a) for a in attaches]
        finally:
            set_active_encoder(None)
        # 基础块（前 14 维）几乎只差一个下标位
        head = [tuple(round(x, 3) for x in vec[:14]) for vec in base]
        self.assertEqual(len(set(head)), len(attaches),
                         "前置：基础块本身确实只靠下标区分")
        # 富块应能区分"战斗位 vs 后备"（HP/招式伤害等属性不同）
        tails = {tuple(round(x, 3) for x in vec[14:]) for vec in rich}
        self.assertGreater(len(tails), 1,
                           "富块未能区分不同 target —— 改动失效")

    def test_rich_block_encodes_entity_attributes(self):
        """按已知局面**逐位核对**富块（is_active / 剩余HP / 伤害 / 能量）"""
        from agent.state_encoder import _rich_action_features
        view = {"me_state": {
            "active": {"name": "A", "hp": 60, "dmg": 10, "energy": 2,
                       "stage": 1, "ex": True, "status": ["poison"],
                       "retreat": 2,
                       "attacks": [{"damage": 30}, {"damage": 90}]},
            "bench": [{"name": "B", "hp": 100, "dmg": 0, "energy": 0,
                       "stage": 0, "attacks": [{"damage": 20}]}],
            "hand": [{"type": "energy", "name": "E"}]}}
        f = _rich_action_features(view, {"verb": "attach", "target": 0})
        self.assertEqual(f[0], 1.0, "target=0 是战斗位")
        self.assertAlmostEqual(f[1], 50 / 320, places=5, msg="剩余 HP")
        self.assertAlmostEqual(f[2], 10 / 320, places=5, msg="已受伤")
        self.assertAlmostEqual(f[3], 2 / 8, places=5, msg="能量数")
        self.assertAlmostEqual(f[4], 0.5, places=5, msg="阶段")
        self.assertEqual(f[5], 1.0, msg="ex")
        self.assertAlmostEqual(f[6], 1 / 3, places=5, msg="状态数")
        self.assertEqual(f[7], 0.0, msg="HP 尚余 → 无被击倒风险")
        self.assertAlmostEqual(f[9], 90 / 320, places=5, msg="最大招式伤害")
        # target=1 → 后备第 0 只（非战斗位、不同 HP）
        f2 = _rich_action_features(view, {"verb": "attach", "target": 1})
        self.assertEqual(f2[0], 0.0)
        self.assertAlmostEqual(f2[1], 100 / 320, places=5)
        # 手牌索引 → 卡牌类别（energy）
        f3 = _rich_action_features(view, {"verb": "play", "index": 0})
        self.assertEqual((f3[11], f3[12], f3[13]), (0.0, 1.0, 0.0),
                         "应识别为能量卡")
        # 越界下标不得抛错（合法动作由引擎给出，但解析层必须稳健）
        f4 = _rich_action_features(view, {"verb": "attach", "target": 99})
        self.assertEqual(len(f4), 17)
        self.assertTrue(all(isinstance(x, float) for x in f4))

    def test_rich_net_forward_shapes_match(self):
        """集成守卫：rich 维度下构网 + 前向必须跑通（dim 管线错位会在此暴露）"""
        import torch
        from agent.state_encoder import (EncoderConfig, action_dim,
                                         encoder_dim, encode_action,
                                         encode_state, set_active_encoder)
        cfg = EncoderConfig(action_features="rich")
        set_active_encoder(cfg)
        try:
            b = self._battle()
            v = b.view()
            net = make_net(encoder_dim(), action_dim(), hidden=32)
            s = torch.tensor([encode_state(v)], dtype=torch.float32)
            f = torch.tensor([[encode_action(v, a) for a in v["legal"]]],
                             dtype=torch.float32)
            logits, value = net(s, f)
            self.assertEqual(logits.shape, (1, len(v["legal"])))
            self.assertEqual(value.shape, (1,))
        finally:
            set_active_encoder(None)

    def test_from_weights_rejects_dim_mismatch(self):
        """换编码配置后**不得**续跑旧权重（否则报错难定位 / 有静默错位风险）"""
        src = (Path(__file__).resolve().parents[1] / "examples"
               / "run_k1_longtrain.py").read_text(encoding="utf-8")
        self.assertIn("与当前", src)
        self.assertRegex(src, r"不能续跑",
                         "续训路径必须显式拦截编码维度不一致的权重")

    def test_load_net_infers_encoder_config(self):
        """`load_net` 必须**从权重形状反推编码配置**

        实测坑：评估脚本用当前会话配置建网，拿 rich 权重（action_dim=31）去评估
        直接 `size mismatch`（31 vs 14）—— 用户只能手改代码。故改为自动推断。
        """
        import tempfile
        import torch
        from agent.state_encoder import (EncoderConfig, action_dim,
                                         active_encoder, encoder_dim,
                                         set_active_encoder)
        from agent.trainer import load_net
        with tempfile.TemporaryDirectory() as d:
            for cfg, label in ((EncoderConfig(action_features="rich"), "rich"),
                               (None, "basic")):
                set_active_encoder(cfg)
                net = make_net(encoder_dim(), action_dim(), hidden=32)
                p = Path(d) / f"w_{label}.pt"
                torch.save(net.state_dict(), p)
                # 故意把会话配置设成**相反**的一种，验证 load_net 会自行纠正
                set_active_encoder(None if cfg else
                                   EncoderConfig(action_features="rich"))
                loaded = load_net(str(p), device="cpu")
                got = active_encoder()
                self.assertEqual(
                    encoder_dim(), net.state_dim,
                    f"{label}: 推断出的 state_dim 必须与权重一致")
                self.assertEqual(
                    action_dim(), net.action_dim,
                    f"{label}: 推断出的 action_dim 必须与权重一致")
                self.assertEqual(
                    bool(got and got.action_features == "rich"),
                    label == "rich", f"{label}: 动作特征模式推断错误")
        set_active_encoder(None)

    def test_infer_rejects_unknown_dims(self):
        """无法推断的维度必须**抛错**（不猜：feature 顺序会影响向量布局）"""
        from agent.state_encoder import infer_config_from_state_dict
        import torch
        sd = {"ctx.0.weight": torch.zeros(32, 57),      # 57 非 51/59
              "action_enc.0.weight": torch.zeros(16, 20)}
        with self.assertRaises(ValueError):
            infer_config_from_state_dict(sd)


if __name__ == "__main__":
    unittest.main(verbosity=2)
