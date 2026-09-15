# -*- coding: utf-8 -*-
"""PokéLab Agent · 决策链路用例（encoder / policy / MCTS / pipeline / prompts）

运行：cd packages/agent && python -m unittest discover -s tests -v
"""
from __future__ import annotations
import sys
import unittest
from pathlib import Path

# 注入同级 engine（monorepo 未装包时的本地运行方式）
_ENGINE = Path(__file__).resolve().parents[3] / "packages" / "engine"
sys.path.insert(0, str(_ENGINE))

from engine import decklib
from engine.battle import Battle

from agent.state_encoder import (encode_state, encode_action, STATE_DIM,
                                 ACTION_DIM, legal_actions_from_view)
from agent.policy_net import SmallPolicyValueNet
from agent.mcts import mcts_select_action, mcts_with_stats
from agent.pipeline import decide, build_langgraph_pipeline, decide_langgraph
from agent import prompts


def _battle(seed: int = 1) -> Battle:
    return Battle(decklib.deck_lightning_demo(), decklib.deck_fire_demo(),
                  seed=seed)


class TestStateEncoder(unittest.TestCase):
    def test_state_dim_stable(self):
        b = _battle()
        v = b.view()
        s = encode_state(v)
        self.assertEqual(len(s), STATE_DIM)
        self.assertGreater(STATE_DIM, 50)      # 定长且信息量足够
        self.assertTrue(all(isinstance(x, float) for x in s))

    def test_action_dim_stable(self):
        for verb in ("play", "attach", "attack", "retreat", "evolve",
                     "trainer", "pass"):
            a = encode_action({}, {"verb": verb})
            self.assertEqual(len(a), ACTION_DIM)

    def test_legal_proxy(self):
        b = _battle()
        v = b.view()
        self.assertEqual(legal_actions_from_view(v), v["legal"])


class TestPolicyNet(unittest.TestCase):
    def test_forward_shapes(self):
        import torch
        net = SmallPolicyValueNet(STATE_DIM, ACTION_DIM)
        B, A = 2, 6
        state = torch.randn(B, STATE_DIM)
        feats = torch.randn(B, A, ACTION_DIM)
        logits, value = net(state, feats)
        self.assertEqual(logits.shape, (B, A))
        self.assertEqual(value.shape, (B,))

    def test_priors_are_prob(self):
        import torch
        net = SmallPolicyValueNet(STATE_DIM, ACTION_DIM)
        state = torch.randn(1, STATE_DIM)
        feats = torch.randn(1, 4, ACTION_DIM)
        p = net.action_priors(state, feats)
        self.assertAlmostEqual(p.sum().item(), 1.0, places=4)

    def test_torch_cuda_if_available(self):
        import torch
        if torch.cuda.is_available():
            net = SmallPolicyValueNet(STATE_DIM, ACTION_DIM).cuda()
            self.assertEqual(next(net.parameters()).device.type, "cuda")
        self.assertTrue(True)   # 环境无关：无卡时跳过


class TestMCTS(unittest.TestCase):
    def test_returns_legal_action(self):
        b = _battle(seed=7)
        legal = b.legal_actions()
        act = mcts_select_action(b, iters=6, seed=7)
        self.assertIn(act, legal)

    def test_deterministic_same_seed(self):
        a1 = mcts_select_action(_battle(seed=9), iters=8, seed=99)
        a2 = mcts_select_action(_battle(seed=9), iters=8, seed=99)
        self.assertEqual(a1, a2)

    def test_stats_report(self):
        b = _battle(seed=3)
        act, stats = mcts_with_stats(b, iters=5, seed=3)
        self.assertIn(act, b.legal_actions())
        self.assertGreaterEqual(len(stats["candidates"]), 1)


class TestPipeline(unittest.TestCase):
    def test_decide_pure(self):
        b = _battle(seed=11)
        out = decide(b, iters=4, seed=11)
        self.assertIn(out["action"], b.legal_actions())
        self.assertEqual(len(out["state_vec"]), STATE_DIM)
        self.assertTrue(out["audit"])

    def test_langgraph_matches_pure(self):
        try:
            build_langgraph_pipeline()      # 探测是否安装
        except ImportError:
            self.skipTest("langgraph 未安装")
        b = _battle(seed=13)
        pure = decide(b, iters=4, seed=13)["action"]
        lg = decide_langgraph(_battle(seed=13), iters=4, seed=13)["action"]
        self.assertEqual(pure, lg)


class TestPrompts(unittest.TestCase):
    def test_templates_exist(self):
        names = prompts.templates()
        self.assertIn("parse_effects_system", names)
        self.assertIn("parse_effects_user", names)
        self.assertIn("commentator_system", names)

    def test_parse_system_prompt_has_no_inline_enum_list(self):
        """回归守卫：Schema 里**不得**把枚举写成引号内的 `a|b|c`

        踩过的真 bug（本地 Qwen3-4B 实测）：原提示词写
            "target": "active_opponent|self|bench|…"
        模型把这个字面量抄了下来，输出 `"target":"active|bench"` → Schema 校验判非法
        （`action.target 非法：active|bench`），整条解析作废。
        枚举必须放进**独立枚举表**并明确「只能填一个」，否则小模型极易照抄。
        """
        import re
        text = prompts.load("parse_effects_system")
        for field in ("kind", "trigger", "type", "target", "op"):
            pat = rf'"{field}"\s*:\s*"[^"]*\|[^"]*"'
            self.assertIsNone(
                re.search(pat, text),
                f"提示词里 {field} 的枚举又写成引号内 `a|b|c` 了（模型会照抄，见本用例说明）")
        self.assertIn("只能填", text, "缺少「枚举只能填一个取值」的硬性要求")

    def test_render_replaces_tokens(self):
        out = prompts.render(
            "parse_effects_user", card_name="Dragapult ex",
            series="TWM", number="130", supertype="Pokemon", hp=330,
            rules_text="200 伤害…", attacks_meta="")
        self.assertIn("Dragapult ex", out)
        self.assertNotIn("@@card_name@@", out)


class TestSystemPromptInjection(unittest.TestCase):
    """系统提示词的「枚举/取值域注入」必须与代码同步

    背景：实测发现 LLM 把「抛币 / 护盾 / 伤害缩放」这类效果笼统写成 `rule`，
    导致引擎映射不到（`unsupported: rule`）——而根因**不是模型能力**，是提示词的
    `action.type` 枚举表**只列到 `rule`**，而校验层 `ACTION_TYPES` 早已支持
    `coin_flip`/`damage_mod`/`shield`/`passive`/`recover`。模型只能按提示词填。

    → 修法不是「再抄一遍枚举到 .md」（那正是漂移的来源），而是**从代码常量与引擎
    动态注入**。本组用例守住这条：注入漏了、或有人往提示词里塞了不可映射的示例，
    都会在这里失败——**不用等真跑 40 分钟 GPU 才发现**。
    """

    def setUp(self):
        from agent import effects as E
        self.E = E

    def test_enum_lists_injected_from_constants(self):
        """渲染后必须包含全部枚举取值（单一来源 = 代码常量）"""
        import re
        sp = self.E.render_system_prompt()
        self.assertIsNone(re.search(r"@@\w+@@", sp), "提示词有未替换的占位符")
        for label, vals in (("kind", self.E.KINDS),
                            ("trigger", self.E.TRIGGERS),
                            ("action.type", self.E.ACTION_TYPES),
                            ("target", self.E.TARGETS)):
            for v in vals:
                self.assertIn(v, sp, f"{label} 的取值 {v} 未注入提示词")

    def test_mechanism_types_present(self):
        """机制级 type 必须在枚举里（否则模型只能退回 rule）"""
        sp = self.E.render_system_prompt()
        for v in ("coin_flip", "damage_mod", "shield", "passive"):
            self.assertIn(v, sp, f"机制级 type {v} 未出现在提示词枚举中")

    def test_engine_vocab_injected(self):
        """引擎取值域必须注入（计数源/条件来自 engine.battle.dmg_vocab）"""
        sp = self.E.render_system_prompt()
        vocab = self.E.engine_vocab()
        self.assertTrue(vocab.get("counters"), "未能从引擎读到计数源取值域")
        self.assertIn(vocab["counters"][0], sp)
        self.assertIn(vocab["conditions"][0], sp)

    def test_fewshot_examples_map_to_engine(self):
        """提示词里的 few-shot 示例**必须能被引擎映射**

        这是本组最关键的一条：示例是模型要模仿的样板。若示例本身映射不到引擎
        （如 `mode` 写了个引擎不认的值 → 落到 else 分支变成「固定加伤」，语义全错），
        模型就会学着产出一批「看起来合法、其实映射错」的结果，而且**很难察觉**。
        """
        import json
        import re
        sp = self.E.render_system_prompt()
        blocks = re.findall(r"^(\[\{.*\}\])$", sp, re.M)
        self.assertGreaterEqual(len(blocks), 4,
                                "示例数量少于预期（应含多动作/抛币/护盾/缩放）")
        for i, b in enumerate(blocks, 1):
            specs = json.loads(b)                      # 示例必须是合法 JSON
            errs = self.E.validate_specs(specs)        # 且通过 Schema 校验
            self.assertEqual(errs, [], f"例{i} 未通过 Schema 校验：{errs}")
            tg = self.E.to_engine_tags(specs)          # 且能映射到引擎 tag
            self.assertEqual(tg["unsupported"], [],
                             f"例{i} 含未建模动作：{tg['unsupported']}")
            self.assertTrue(tg["tags"], f"例{i} 未产出任何引擎 tag")


if __name__ == "__main__":
    unittest.main(verbosity=2)
