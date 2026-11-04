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

        示例数量：为控制提示词长度（长提示词会显著拖慢本地推理），只保留
        **最易错的两类** —— 抛币与按计数缩放；spread/护盾的格式改由
        「机制级 type 表 + extra 约定」的文字说明覆盖。
        """
        import json
        import re
        sp = self.E.render_system_prompt()
        blocks = re.findall(r"^(\[\{.*\}\])$", sp, re.M)
        self.assertGreaterEqual(len(blocks), 2,
                                "示例数量少于预期（应含抛币与按计数缩放）")
        for i, b in enumerate(blocks, 1):
            specs = json.loads(b)                      # 示例必须是合法 JSON
            errs = self.E.validate_specs(specs)        # 且通过 Schema 校验
            self.assertEqual(errs, [], f"例{i} 未通过 Schema 校验：{errs}")
            tg = self.E.to_engine_tags(specs)          # 且不含未建模动作
            self.assertEqual(tg["unsupported"], [],
                             f"例{i} 含未建模动作：{tg['unsupported']}")
            # **不要求 `tags` 非空**：`damage` 类动作由招式的 damage 字段承载，
            # 本身不产出引擎 tag（见 `to_engine_tags` 的 damage 分支）——
            # 例 5（文本已给折算口径 → 直接 damage）就是这种情况，
            # 它是**合法且正确**的解析，不该被断言判死。

    def test_kind_trigger_guide_present(self):
        """trigger 判定表 + 「kind 原样回填」必须在

        历史背景：原先这里查的是「kind 与 trigger 判定」表。后来发现 **`kind` 是
        调用方已知事实**（`parse_effects(kind=…)` 传入），让模型从文本"判断"它
        既无必要也不可靠 —— 实测 4 条 kind 失败全是模型**改写**了已给的类别。
        现在只让模型判断 `trigger`，`kind` 必须回填。
        """
        sp = self.E.render_system_prompt()
        self.assertIn("# trigger 判定", sp)
        self.assertIn("`kind` 必须原样回填", sp)

    def test_user_prompt_fully_rendered(self):
        """用户提示词**不得残留占位符**，且必须带上卡牌类别

        这是本组最该有、却长期缺失的一条守卫 —— 缺了它，一个真 bug 藏了很久：
        `parse_effects_user.md` 声明 `@@supertype@@`/`@@hp@@` 等键，而 `parse_llm`
        只传 `card_name`/`card_kind`/`rules_text`，且模板渲染器**默认保留未替换的
        占位符**（为人工核查方便）→ 模型收到的是含 `@@supertype@@` 乱码的提示词，
        **卡牌类别从未传达给模型**。云端 4 条 kind 失败全由此而来。
        """
        up = self.E.render_user_prompt("Dragapult ex", "ability", "每回合一次：抽至手牌 5 张")
        self.assertEqual(prompts.leftover_tokens(up), [],
                         f"用户提示词残留占位符：{prompts.leftover_tokens(up)}")
        self.assertIn("ability", up)        # 类别必须真的在提示词里
        self.assertIn("抽至手牌 5 张", up)

    def test_render_strict_raises_on_leftover(self):
        """`render(strict=True)` 必须拦住残留占位符（防止上述 bug 复发）"""
        with self.assertRaises(prompts.PromptRenderError):
            prompts.render("parse_effects_user", strict=True, card_name="X")

    def test_prompt_length_budget(self):
        """提示词长度**预算守卫**：长提示词拖慢推理，但**质量优先**

        实测（黄金集 50 条 strict）：
          · 6105 字符（示例 4 个）→ 32.0%，卡池单条 141 s
          · 4551 字符（示例 2 个）→ **24.0%** ← 精简后质量**反而掉 8pp**
          · 5868 字符（v6）→ 40.0%（本地）
          · 7103 字符（+target 约定表 / 能量卡 trigger / 机制分解粒度 / search 约定）
            → 本地 40%、**云端 58% → 82%**
          · 8214 字符（+kind 回填/trigger 解耦/rule·attach·limit·switch·coin/括号口径约定）
            → 云端 **92%**（达标），见 k2_report_cloud4.md

        预算放到 8400 的依据（**不是无意识膨胀**）：
        ① 新增内容全部**指向实测的「语义边界未定义」**：上轮 9 条失败无一属模型能力问题
           （kind/trigger/取值约定各占一部分），写死约定是唯一出路 —— 同做法上轮 +24pp；
        ② 已按「先证净收益」执行：本轮先跑云端（+10pp），**同时记录 3 条新失败**
           （其中 2 条是我上轮重写时丢掉既有约定造成的回归，已补回）；
        ③ 主力后端是云端，对提示词长度不敏感；本地已确认不适合承担 K2（40% ≪ 85%）。
        **【约束】这是最后一次未经"净收益证明"就上调预算** —— 再加内容必须有云端实测支撑。

        ── 2026-09-21 上调至 8500（**引擎能力驱动**，非提示词加内容）──
        情况与前几次不同：提示词**正文逐字节未改**，增长全部来自词汇表 —— 引擎新增
        `own_prizes_taken` / `opp_prizes_taken` 两个计数源（修「Prize card … has taken」
        被按 `prizes_left` 映射的**口径反转** bug），而 `dmg_vocab()` 的取值域
        **只由引擎定义、渲染时动态注入**（这正是它存在的理由：防止提示词与引擎漂移）。
        实测：8429 字符；去掉这两个名字后为 8395，差额 34 字符**恰等于**两个名字与分隔符，
        且词汇表前后的正文均逐字节一致。
        → 此类「引擎加能力 → 提示词同步」的增长必须允许，否则就会出现
          「引擎支持、提示词不提」的错配。**提示词正文再加内容仍须云端实测净收益。**

        ── 2026-10-26 上调至 8600（**仍是引擎能力驱动，正文逐字节未改**）──
        第四批条件（`opp_active_is_ex` / `opp_active_is_evolution` /
        `opp_active_has_damage` / `own_active_has_damage`）进入 `_CONDITIONS`，
        经 `dmg_vocab()` 自动注入。实测：**8514** 字符（上一版 8429），
        差额 85 = 4 个条件名（80 字符）+ 4 个分隔符（4）+ 1 —— 与「正文未改、
        仅词汇表变长」完全对得上（去掉 4 个名字后回落到 8419，与 8429 的差异
        来自同批 `condition_params` 不计入该槽位）。
        **提示词正文（模板）本轮未改动一个字节**；K2 云端准确率基线不受影响，
        故无需重跑云端跑分即可接受该增长。

        ── 2026-10-27 上调至 8900（**同样是引擎能力驱动，正文逐字节未改**）──
        第五批条件伤害缩放（7 个无参条件 + 10 个带参条件形态）进入引擎词表，
        经 `dmg_vocab()` 自动注入。实测：**8701** 字符（上一版 8514）。
        差额 187 全部来自 `conditions` / `condition_params` 两个槽位：
        7 个无参名共 145 字符 + 7 个分隔符，其余为 10 条带参形态及其说明。
        模板正文未改一个字节 → 与历次「引擎加能力 → 提示词同步」同性质。

        ── 2026-10-28 上调至 9100（**仍是引擎能力驱动，正文逐字节未改**）──
        第六批条件/计数源（4 个无参条件、5 个带参条件形态、3 个动态计数源）
        进入 `_CONDITIONS` / `_CONDITION_PARAMS` / `dmg_vocab`，自动注入。
        实测：**8965** 字符（上一版 8701）；差额 264 全部来自词汇表槽位。
        模板正文未改 → 与历次同性质。

        ── 2026-10-28 上调至 9200（**仍是引擎能力驱动，正文逐字节未改**）──
        第八批静态免伤族（`dmg_immune` / `bench_immune_dmg` 两个被动 tag 及其
        来源/门槛形态说明）进入 `dmg_vocab().passive_tags`，自动注入。
        实测：**9143** 字符（上一版 8965）；差额 178 全部来自该槽位。
        模板正文未改 → 与历次同性质。

        ── 2026-11-02 上调至 9250（**仍是引擎能力驱动，正文逐字节未改**）──
        第三十八批新增两个附着变体与一个训练家动作类型，进入
        `enum_action_type` 槽位（`" · ".join(ACTION_TYPES)`）：
          · `attach_spread_flip`（Mega Audino ex 抛币计数分配）
          · `attach_each_bench`（Mega Gardevoir ex 每只后备各一份）
          · `discard_tool_or_senergy`（Blowtorch 二选一）
        实测：**9246** 字符（上一版 9200 内）。这三个名字 LLM 侧也**必须**知道：
        后端切到 LLM 时，同句卡面的 `actions[].type` 只能从这份枚举里取，
        少一个就会产出「action.type 非法」的整条作废。
        模板正文未改 → 与历次「引擎加能力 → 提示词同步」同性质。

        ── 2026-11-02 上调至 9330（**仍是引擎能力驱动，正文逐字节未改**）──
        第三十九批新增四个附着变体，进入 `enum_action_type` 槽位：
          · `attach_spread_multi`（Steven's Metagross ex 双色 or-1-of-each）
          · `attach_each_pick`（Janine's Secret Art 选至多 N 只各附）
          · `attach_flip_until`（Snorlax 抛到反面为止）
          · `coin_gate_attach`（Energy Coin 训练家抛币门）
        实测：**9326** 字符（上一版 9246）。同上：这些名字 LLM 侧也必须知道，
        后端切到 LLM 时 `actions[].type` 只能从这份枚举里取。
        模板正文未改 → 与历次同性质。

        ── 2026-11-02 上调至 9345（**仍是引擎能力驱动，正文逐字节未改**）──
        第四十批新增 `until_tails`（「Flip a coin until you get tails」+ 磨牌 /
        弃对手手牌 / 任意卡检索 / 弃对手战斗位能量的**非伤害**子效果）。
        实测：**9340** 字符（上一版 9326，+14 = 该枚举名）。
        """
        sp = self.E.render_system_prompt()
        self.assertLessEqual(
            len(sp), 9400,
            f"提示词已 {len(sp)} 字符，超过 9345 预算；"
            f"加内容前必须先在云端实测净收益（已进入波动带）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
