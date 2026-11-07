# -*- coding: utf-8 -*-
"""PokéLab AI API · 冒烟测试（TestClient，无外网依赖）

运行：cd <repo> && conda activate pokemo
      python -m unittest apps.api.tests.test_api -v
覆盖：认证 · 人机对局闭环（F5）· 批量任务+报告（F6/F7）· 训练任务最小运行（M6）
注意：train 用例会真实启动子进程跑最小课程（约 15–40s）。
"""
from __future__ import annotations
import importlib.util
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

# ---- 测试隔离：把 API 的 SQLite 指向**临时目录**（必须在 import app 之前设置）----
# 为什么必须做：本套件会真实注册用户/建对局/建任务，此前直接写在开发库里——
# 单轮即可产生上百个 u_*/adm_*/d_* 残留账号（且多为弱口令），既污染开发环境
# 又带来安全隐患。store._pick_data_dir() 已支持 POKELAB_DATA_DIR 覆盖，这里利用它。
_TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="pokelab_api_test_"))
os.environ["POKELAB_DATA_DIR"] = str(_TEST_DATA_DIR)

# engine/agent 由 apps.api.app.main 自行注入 path
from fastapi.testclient import TestClient  # noqa: E402
from apps.api.app.main import app  # noqa: E402
from apps.api.app import store  # noqa: E402

client = TestClient(app)
_TOKEN = {"v": None}


def user_headers():
    """注册一次性用户并返回 Bearer 头（首个注册者为 admin，后续为 user）"""
    import uuid
    if _TOKEN["v"]:
        return _TOKEN["v"]
    name = "u_" + uuid.uuid4().hex[:8]
    r = client.post("/auth/register", json={"name": name, "password": "pw123456"})
    assert r.status_code == 200, r.text
    _TOKEN["v"] = {"Authorization": "Bearer " + r.json()["token"]}
    _TOKEN["name"] = name
    _TOKEN["role"] = r.json()["role"]
    return _TOKEN["v"]


def _wait_job(job_id: str, timeout: float = 120, kind: str = "batch",
              headers=None):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = client.get(f"/{kind}/jobs/{job_id}", headers=headers)
        assert r.status_code == 200, r.text
        st = r.json()["status"]
        if st in ("done", "failed", "cancelled"):
            return r.json()
        time.sleep(0.5)
    raise AssertionError(f"job {job_id} 超时未完成")


class TestAuth(unittest.TestCase):
    def test_register_login(self):
        name = f"tester_{int(time.time())}"
        r = client.post("/auth/register",
                        json={"name": name, "password": "x1234"})
        self.assertEqual(r.status_code, 200, r.text)
        tok = r.json()["token"]
        self.assertEqual(tok.count("."), 2)                         # JWT 三段
        self.assertIn("role", r.json())
        r2 = client.post("/auth/login", json={"name": name,
                                              "password": "x1234"})
        self.assertEqual(r2.status_code, 200)                       # 正确口令可登录
        self.assertEqual(r2.json()["role"], r.json()["role"])
        bad = client.post("/auth/login", json={"name": name,
                                               "password": "wrong123"})
        self.assertEqual(bad.status_code, 401)                      # 错误口令拒绝
        r3 = client.post("/auth/login", json={"name": "no_such_user",
                                              "password": "x1234"})
        self.assertEqual(r3.status_code, 401)                       # 不泄露用户存在性
        # 重复注册 → 409
        r4 = client.post("/auth/register", json={"name": name,
                                                 "password": "x1234"})
        self.assertEqual(r4.status_code, 409)


class TestBattles(unittest.TestCase):
    def test_battle_roundtrip(self):
        r = client.post("/battles", json={"deck_a": "lightning",
                                          "deck_b": "fire", "ai_iters": 0})
        self.assertEqual(r.status_code, 201, r.text)
        j = r.json()
        bid = j["id"]
        self.assertIn("view", j)
        self.assertEqual(j["player_side"], 0)
        # 人类逐动作推进，直至终局或步数上限
        guard = 0
        while guard < 60:
            guard += 1
            st = client.get(f"/battles/{bid}").json()
            if st.get("finished"):
                break
            legal = (st["view"] or {}).get("legal") or []
            act = next((a for a in legal if a.get("verb") == "pass"), None) \
                or (legal[0] if legal else {"verb": "pass"})
            ar = client.post(f"/battles/{bid}/actions", json={"action": act})
            self.assertEqual(ar.status_code, 200, ar.text)
            if ar.json()["winner"] is not None:
                break
        # 非法动作被拒（F5：动作=legal，不一致即 bug）
        bad = client.post(f"/battles/{bid}/actions",
                          json={"action": {"verb": "not_a_verb"}})
        self.assertIn(bad.status_code, (409, 422))
        # 复制 seed 以重放（F10 数据基础）
        self.assertTrue(isinstance(j["seed"], int))


class TestBattleViewPerspective(unittest.TestCase):
    """视角守卫：接口返回的 `view` 必须是**玩家视角**。

    历史缺陷：`battle.view()` 不带参数时取 `battle.current`；对局若在 AI 回合结束
    （结束时 current 仍是 AI），返回里 `me` 指向 AI、`me_state.hand` 装的是**对手手牌**，
    且前端会左右互换棋盘。合成复现率 10/83。**这是信息泄露，不只是显示问题。**
    """

    def _assert_perspective(self, payload, player_side, where):
        v = payload["view"]
        self.assertEqual(v["me"], player_side,
                         f"{where}: view.me={v['me']}，不是玩家侧 {player_side}（视角泄露）")
        self.assertIn("hand", v["me_state"], f"{where}: 我方应带手牌明细")
        self.assertIn("hand_count", v["opp_state"], f"{where}: 对手应带手牌**数量**")
        self.assertNotIn("hand", v["opp_state"],
                         f"{where}: 对手不得带手牌明细（泄露）")

    def test_perspective_all_the_way_to_game_over(self):
        """玩家只 pass（最坏路径）打到终局，**每一次响应**都校验视角。"""
        r = client.post("/battles", json={"deck_a": "lightning", "deck_b": "fire",
                                          "ai_iters": 0, "player_side": 0})
        self.assertEqual(r.status_code, 201, r.text)
        j = r.json()
        bid, side = j["id"], j["player_side"]
        self._assert_perspective(j, side, "create")
        finished = False
        for i in range(1, 201):
            st = client.get(f"/battles/{bid}").json()
            self._assert_perspective(st, side, f"get_state#{i}")
            if st.get("finished"):
                finished = True
                break
            legal = (st["view"] or {}).get("legal") or []
            act = next((a for a in legal if a.get("verb") == "pass"), None) \
                or (legal[0] if legal else {"verb": "pass"})
            ar = client.post(f"/battles/{bid}/actions", json={"action": act})
            self.assertEqual(ar.status_code, 200, ar.text)
            aj = ar.json()
            self._assert_perspective(aj, side, f"act#{i}")
            if aj["winner"] is not None:
                finished = True
                break
        self.assertTrue(finished, "对局未在 200 步内结束（测试前提失效）")

    def test_finished_on_ai_turn_returns_player_view(self):
        """确定性覆盖「AI 打完最后一击 → 对局结束且 current 仍是 AI」这一态。"""
        r = client.post("/battles", json={"deck_a": "lightning", "deck_b": "fire",
                                          "ai_iters": 0, "player_side": 0})
        bid, side = r.json()["id"], r.json()["player_side"]
        b = store.battle_sessions[bid]["battle"]
        b.finished = True                 # 复刻：对局在 AI 回合结束
        b.current = 1 - side              # 此时行动方仍是 AI 侧
        self._assert_perspective(client.get(f"/battles/{bid}").json(), side,
                                 "get_state(finished on AI turn)")

    def test_ai_steps_carry_intermediate_views(self):
        """AI 回应需带逐步中间态（供前端逐帧回放）；中间态不含 legal（控体积）。"""
        r = client.post("/battles", json={"deck_a": "lightning", "deck_b": "fire",
                                          "ai_iters": 0, "player_side": 0})
        bid = r.json()["id"]
        seen = False
        for _ in range(60):
            st = client.get(f"/battles/{bid}").json()
            if st.get("finished"):
                break
            legal = (st["view"] or {}).get("legal") or []
            act = next((a for a in legal if a.get("verb") == "pass"), None) \
                or (legal[0] if legal else {"verb": "pass"})
            aj = client.post(f"/battles/{bid}/actions", json={"action": act}).json()
            self.assertIn("ai_steps", aj)
            self.assertIn("player_view", aj)
            # 旧字段与新字段必须一致（避免两套说法漂移）
            self.assertEqual([x["action"] for x in aj["ai_steps"]], aj["ai_actions"])
            pv = aj["player_view"]
            self.assertEqual(pv["me"], aj["view"]["me"])
            self.assertNotIn("legal", pv, "player_view 是中间帧，不应携带 legal")
            for stp in aj["ai_steps"]:
                if stp["view"] is not None:
                    seen = True
                    self.assertNotIn("legal", stp["view"], "中间态不应携带 legal")
                    self.assertEqual(stp["view"]["me"], aj["view"]["me"],
                                     "中间态也必须用玩家视角（否则泄露对手手牌）")
            if aj["winner"] is not None:
                break
        self.assertTrue(seen, "整局都没拿到 AI 中间态（逐帧回放会退化成只播报日志）")

    def test_api_layer_has_no_bare_view_call(self):
        """静态守卫：接口层不得出现不带视角参数的 `battle.view()`。

        唯一例外是 AI 决策（`ai.decide(battle.view())`）——它本就该拿自己的视角。
        """
        import inspect
        from apps.api.app import battles as mod
        bad = [ln.strip() for ln in inspect.getsource(mod).splitlines()
               if ".view()" in ln and "decide(" not in ln
               and not ln.strip().startswith("#")]
        self.assertEqual(bad, [],
                         f"接口层存在不带视角的 view() 调用（会泄露对手手牌）：{bad}")


class TestAuthZ(unittest.TestCase):
    """真 JWT/RBAC：guest 禁训练 · 跨用户越权 403 · admin 用户管理"""

    def test_guest_cannot_train(self):
        r = client.post("/train/jobs", json={"stage1_episodes": 1})
        self.assertEqual(r.status_code, 403, r.text)

    def test_me_and_token_invalid(self):
        h = user_headers()
        me = client.get("/auth/me", headers=h)
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["name"], _TOKEN["name"])
        self.assertIn("role", me.json())
        bad = client.get("/auth/me", headers={"Authorization": "Bearer forged"})
        self.assertEqual(bad.status_code, 401)

    def test_cross_user_job_denied(self):
        # A 提交批量任务
        ra = client.post("/batch/jobs", json={"games": 30, "seed_start": 3},
                         headers=user_headers())
        jid = ra.json()["id"]
        # B 用另一账号访问 → 403
        import uuid
        rb = client.post("/auth/register",
                         json={"name": "b_" + uuid.uuid4().hex[:8],
                               "password": "pw123456"})
        hb = {"Authorization": "Bearer " + rb.json()["token"]}
        r = client.get(f"/batch/jobs/{jid}", headers=hb)
        self.assertEqual(r.status_code, 403, r.text)

    def test_admin_users_list(self):
        h = user_headers()
        if _TOKEN.get("role") != "admin":
            self.skipTest("首个用户非 admin（已有历史用户）")
        r = client.get("/admin/users", headers=h)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(len(r.json()["users"]) >= 1)

    def test_password_not_plaintext(self):
        from apps.api.app import store
        u = store.get_user(_TOKEN.get("name"))
        if u:
            self.assertTrue((u.get("password_hash") or "").startswith(
                "pbkdf2_sha256$"))
            self.assertNotEqual(u.get("password_hash"), "pw123456")


class TestBatch(unittest.TestCase):
    """批量任务（F6）。

    注意：批量任务有**每日配额**（guest 与注册用户各自独立），且 guest 是
    全局共享账号 → 用例必须用新注册用户，否则同日重复跑会被 429 拦住
    （配额本身的行为由 TestQuota 单独覆盖）。
    """

    def test_batch_and_report(self):
        r = client.post("/batch/jobs", headers=user_headers(),
                        json={"deck_a": "lightning",
                              "deck_b": "fire",
                              "games": 3,
                              "seed_start": 999})
        self.assertEqual(r.status_code, 202, r.text)
        jid = r.json()["id"]
        done = _wait_job(jid, headers=user_headers())
        self.assertEqual(done["status"], "done")
        self.assertEqual(done["result"]["n"], 3)
        rep = client.get(f"/batch/jobs/{jid}/report?format=md",
                         headers=user_headers())
        self.assertEqual(rep.status_code, 200)
        self.assertIn("非官方数据", rep.text)
        jrep = client.get(f"/batch/jobs/{jid}/report?format=json",
                          headers=user_headers()).json()
        self.assertIn("win_rate_a", jrep)

    def test_cancel_keeps_partial(self):
        r = client.post("/batch/jobs", headers=user_headers(),
                        json={"deck_a": "lightning",
                              "deck_b": "fire",
                              "games": 300,
                              "seed_start": 7})
        self.assertEqual(r.status_code, 202, r.text)
        jid = r.json()["id"]
        time.sleep(0.4)
        client.post(f"/batch/jobs/{jid}/cancel", headers=user_headers())
        end = _wait_job(jid, headers=user_headers())
        self.assertIn(end["status"], ("cancelled", "done"))
        if end["status"] == "cancelled":
            self.assertIn("n", end["result"])


class TestGuestQuota(unittest.TestCase):
    """配额口径：guest 可提交（不强制登录）；日配额用尽 → 429（PRD F6）

    为避免污染共享的 guest 配额，这里用一次性用户把配额打满再验证 429，
    同时顺带断言 guest 提交路径本身可用（返回 202/429 均属正常业务响应）。
    """

    def test_guest_can_submit(self):
        r = client.post("/batch/jobs", json={"games": 2, "seed_start": 1})
        self.assertIn(r.status_code, (202, 429), r.text)

    def test_daily_quota_exhausted(self):
        import uuid
        name = "q_" + uuid.uuid4().hex[:8]
        h = {"Authorization": "Bearer " + client.post(
            "/auth/register", json={"name": name, "password": "pw123456"}
        ).json()["token"]}
        codes = []
        for i in range(11):
            rr = client.post("/batch/jobs", headers=h,
                             json={"games": 1, "seed_start": 100 + i})
            codes.append(rr.status_code)
        self.assertIn(202, codes)
        self.assertEqual(codes[-1], 429, f"第 11 次应被配额拦下：{codes}")


class TestDayBoundary(unittest.TestCase):
    """日配额边界必须落在**本地**零点（2026-10-27 修时区混用缺陷）

    历史缺陷：`datetime.now().date()`（本地日期）+ `tzinfo=utc`（UTC 零点）混用，
    在 UTC+8 的 00:00–08:00 之间边界落在未来 → 当日用量恒为 0 → 配额失效。
    """

    def test_day_start_is_local_midnight(self):
        import time
        from app.batch import _day_start_ts
        now = time.time()
        start = _day_start_ts(now)
        self.assertLessEqual(start, now, "日界不得晚于当前时刻（否则配额失效）")
        self.assertLess(now - start, 86400 + 60, "日界应在 24h 以内")
        # 同一自然日内，任意时刻的日界一致
        self.assertEqual(start, _day_start_ts(start + 60))


class TestDecks(unittest.TestCase):
    """F3：导入解析/校验（60 张·同名4·含基础）· 起手模拟 · 导出往返"""

    TEXT = "\n".join(["4 Dreepy TST 1", "3 Drakloak TST 2", "3 Dragapult ex TST 3",
                       "4 Poffin TST 4", "4 Ultra Ball TST 5", "10 Energy TST 6",
                       "4 Seedling TST 7", "4 Sunflora TST 8", "4 Emberling TST 9",
                       "4 Flarex ex TST 10", "4 Toxibug TST 11", "4 Dreamoth TST 12",
                       "4 Fangpuppy TST 13"])

    def test_import_presets_opening_export(self):
        h = user_headers()
        r = client.post("/decks/import", headers=h,
                        json={"text": self.TEXT, "name": "单测卡组"})
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()
        self.assertIn("summary", d)
        self.assertTrue(d["parsed"])
        # 起手模拟（引擎统计口径，不依赖对战）
        op = client.post(f"/decks/{d['id']}/opening?trials=200", headers=h)
        self.assertEqual(op.status_code, 200, op.text)
        o = op.json()
        self.assertGreaterEqual(o["brick_rate"], 0.0)
        self.assertLessEqual(o["brick_rate"], 1.0)
        self.assertEqual(sum(o["basics_hist"].values()) if isinstance(o["basics_hist"], dict) else 200, 200)
        # 导出往返一致（行数=唯一种数）
        ex = client.get(f"/decks/{d['id']}/export", headers=h).json()
        self.assertEqual(ex["count"], len(d["parsed"]))
        # 预设 A1–A4
        pr = client.get("/decks/presets", headers=h).json()["presets"]
        self.assertEqual([x["id"] for x in pr], ["A1", "A2", "A3", "A4"])

    def test_validation_catches_over_limit(self):
        h = user_headers()
        bad = "\n".join(["12 Bolt TST 1", "11 Thundrex ex TST 2"] * 4)
        r = client.post("/decks/import", headers=h, json={"text": bad})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["ok"])
        self.assertTrue(any("同名上限" in e for e in r.json()["errors"]))

    def test_cross_user_deck_denied(self):
        h = user_headers()
        d = client.post("/decks/import", headers=h,
                        json={"text": self.TEXT}).json()
        import uuid
        rb = client.post("/auth/register", json={"name": "d_" + uuid.uuid4().hex[:8],
                                                 "password": "pw123456"})
        hb = {"Authorization": "Bearer " + rb.json()["token"]}
        self.assertEqual(client.get(f"/decks/{d['id']}", headers=hb).status_code, 403)


class TestStats(unittest.TestCase):
    """F7：统计聚合 + K1–K4 看门狗"""

    def test_summary_and_watchdog(self):
        h = user_headers()
        s = client.get("/stats/summary", headers=h).json()
        self.assertIn("kpi", s)
        self.assertIn("matrix", s)
        self.assertEqual(len(s["recent7d"]), 7)
        self.assertIn("Unofficial", s["declaration"])
        w = client.get("/stats/watchdog", headers=h).json()
        self.assertEqual([r["id"] for r in w["rows"]], ["K1", "K2", "K3", "K4"])
        # 说明文案不得写死达标状态（K1 会随训练更新，写死必然过期）：
        # 只断言「如实标注」的口径存在，以及不复现历史措辞
        self.assertIn("如实标注", w["disclaimer"])
        self.assertNotIn("尚未达成", w["disclaimer"])


@unittest.skipUnless(importlib.util.find_spec("torch"),
                     "训练子进程需要 torch：CI 的 api job 只装 apps/api/requirements.txt，"
                     "未装 torch；本地装有 torch 时本用例正常执行")
class TestTrain(unittest.TestCase):
    def test_train_minimal_run(self):
        r = client.post("/train/jobs", headers=user_headers(), json={
            "stage1_episodes": 4, "stage2_episodes": 0,
            "stage3_episodes": 0, "bc_episodes": 40})
        self.assertEqual(r.status_code, 202, r.text)
        jid = r.json()["id"]
        done = _wait_job(jid, timeout=180, kind="train", headers=user_headers())
        self.assertEqual(done["status"], "done", done.get("error"))
        self.assertIn("curve", done["result"])
        # 子进程日志可读
        lg = client.get(f"/train/jobs/{jid}/log?lines=20", headers=user_headers())
        self.assertEqual(lg.status_code, 200)
        self.assertTrue(any("stage1 done" in x for x in lg.json()["lines"])
                        or any("k1" in x for x in lg.json()["lines"]))


# ---------------- F3 打通 / 池矩阵 / SSE ----------------
def _legal_deck_text() -> str:
    """从引擎卡池构造 15 种 × 4 张 = 60 张合法卡表（含基础宝可梦）"""
    from engine import decklib
    seen = {}
    for fn in [n for n in dir(decklib) if n.startswith("deck_")]:
        try:
            deck = getattr(decklib, fn)()
        except Exception:
            continue
        for c in deck:
            pdef = getattr(c, "pdef", None)
            tdef = getattr(c, "tdef", None)
            edef = getattr(c, "edef", None)
            if pdef is not None:
                nm, basic = pdef.name, getattr(pdef, "stage", 0) == 0
            elif tdef is not None:
                nm, basic = tdef.name, False
            elif edef is not None:
                nm, basic = getattr(edef, "name", "Energy"), False
            else:
                continue
            seen[nm] = basic
    basics = [n for n, b in seen.items() if b]
    others = [n for n in seen if n not in basics]
    picks = (basics[:4] + others)[:15]
    return "\n".join(f"4 {n} TST 1" for n in picks)


class TestF3Battle(unittest.TestCase):
    """F3 → F5：粘贴卡表 → 校验 → 直接用于对战"""

    def test_imported_deck_plays(self):
        h = user_headers()
        text = _legal_deck_text()
        d = client.post("/decks/import", headers=h,
                        json={"text": text, "name": "F3 对战卡组"}).json()
        self.assertTrue(d["summary"]["battle_ready"], d.get("errors"))
        r = client.post("/battles", headers=h,
                        json={"deck_a_id": d["id"], "deck_b": "fire",
                              "ai_iters": 0})
        self.assertEqual(r.status_code, 201, r.text)
        bid = r.json()["id"]
        st = client.get(f"/battles/{bid}", headers=h).json()
        legal = (st["view"] or {}).get("legal") or []
        act = next((a for a in legal if a.get("verb") == "pass"),
                   legal[0] if legal else {"verb": "pass"})
        ar = client.post(f"/battles/{bid}/actions", headers=h,
                         json={"action": act})
        self.assertEqual(ar.status_code, 200, ar.text)

    def test_unmatched_deck_blocked(self):
        h = user_headers()
        d = client.post("/decks/import", headers=h,
                        json={"text": "60 Unknown Card TST 1"}).json()
        r = client.post("/battles", headers=h,
                        json={"deck_a_id": d["id"], "deck_b": "fire"})
        self.assertIn(r.status_code, (400, 422), r.text)

    def test_printing_uid_selects_exact_printing(self):
        """「数量 卡名 SET 编号」→ 按**印刷 uid**精确选卡（同名不同印刷可分别使用）"""
        from apps.api.app import decks as deck_mod
        h = user_headers()
        text = "\n".join(["4 Dreepy TWM 128", "4 Dusknoir SIT 70",
                          "4 Dusclops SFA 19", "8 Basic Psychic Energy SVE 5"])
        r = client.post("/decks/import", headers=h,
                        json={"text": text, "name": "印刷选择用例"})
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()
        self.assertTrue(d["ok"], d.get("errors"))
        rows = {e["name"]: e for e in d["parsed"]}
        self.assertEqual(rows["Dusknoir"]["uid"], "Dusknoir@SIT 70")
        self.assertEqual(rows["Dusclops"]["uid"], "Dusclops@SFA 19")
        deck = deck_mod.build_engine_deck(d["id"])
        dusk = [c for c in deck if getattr(getattr(c, "pdef", None),
                                           "name", "") == "Dusknoir"][0]
        self.assertEqual(dusk.pdef.abilities[0].tag, "cursed:130",
                         "必须执行 SIT 70 的 Cursed Blast 手牌特性")

    def test_name_only_defaults_to_newest_printing(self):
        """未写印刷 → 卡名默认取数据集最新印刷（SFA 20 = counters:13）"""
        from apps.api.app import decks as deck_mod
        h = user_headers()
        text = "\n".join(["4 Dreepy", "4 Dusknoir",
                          "8 Basic Psychic Energy"])
        d = client.post("/decks/import", headers=h,
                        json={"text": text, "name": "默认印刷用例"}).json()
        self.assertTrue(d["ok"], d.get("errors"))
        deck = deck_mod.build_engine_deck(d["id"])
        dusk = [c for c in deck if getattr(getattr(c, "pdef", None),
                                           "name", "") == "Dusknoir"][0]
        self.assertEqual(dusk.pdef.abilities[0].tag,
                         "counters:13:scope:opp:1:selfko")

    def test_same_name_limit_aggregates_across_printings(self):
        """同名上限按**卡名**合计：不同印刷不能绕过 4 张"""
        h = user_headers()
        text = "\n".join(["4 Dreepy TWM 128", "4 Dusknoir SIT 70",
                          "1 Dusknoir SFA 20", "8 Basic Psychic Energy"])
        d = client.post("/decks/import", headers=h,
                        json={"text": text, "name": "同名上限用例"}).json()
        self.assertFalse(d["ok"])
        self.assertTrue(any("同名上限" in e for e in d["errors"]), d["errors"])


class TestPoolMatrix(unittest.TestCase):
    """F7：卡组池批量任务 → cells → 统计矩阵聚合"""

    def test_pool_job_and_matrix(self):
        h = user_headers()
        r = client.post("/batch/pool-jobs", headers=h, json={
            "decks_a": ["lightning", "fire"], "decks_b": ["psychic"],
            "games_each": 3, "seed_start": 11})
        self.assertEqual(r.status_code, 202, r.text)
        jid = r.json()["id"]
        self.assertEqual(r.json()["pairs"], 2)
        done = _wait_job(jid, headers=h, timeout=120)
        self.assertEqual(done["status"], "done", done.get("error"))
        cells = done["result"]["cells"]
        self.assertEqual(len(cells), 2)
        self.assertEqual(sum(c["n"] for c in cells), 6)
        self.assertIn("ci95", cells[0])
        rep = client.get(f"/batch/jobs/{jid}/report?format=csv", headers=h).text
        # 报告面向人阅读 → 表头与卡组名输出中文（英文仍为 JSON/数据键）
        self.assertIn("我方卡组,对手卡组", rep)
        self.assertIn("演示卡组", rep)
        self.assertIn("雷电演示卡组,超能演示卡组", rep)     # 首个对位行（中文）
        self.assertGreaterEqual(len(rep.strip().splitlines()), 3)
        s = client.get("/stats/summary", headers=h).json()
        self.assertGreaterEqual(len(s["matrix"]), 2)
        self.assertGreaterEqual(s["kpi"]["pool_jobs"], 1)

    def test_avg_turns_not_silently_zero(self):
        """回归：报告「平均回合」曾恒为 0（b.turn 字段名写错 + hasattr 静默兜底）

        守卫方式：跑一个**必然打到分胜负**的短局对位，断言 avg_turns > 0
        且等于逐局 turn_number 的均值——字段名再被打错就会立刻失败。
        """
        h = user_headers()
        r = client.post("/batch/pool-jobs", headers=h, json={
            "decks_a": ["lightning"], "decks_b": ["fire"],
            "games_each": 3, "seed_start": 21})
        self.assertEqual(r.status_code, 202, r.text)
        jid = r.json()["id"]
        done = _wait_job(jid, headers=h, timeout=120)
        res = done["result"]
        self.assertGreater(res["avg_turns"], 0,
                           f"平均回合不应为 0：{res.get('avg_turns')}")
        self.assertGreater(res["cells"][0]["avg_turns"], 0,
                           "逐对位 avg_turns 不应为 0")
        # 与引擎直接跑同一对位的结果交叉校验（口径一致）
        from engine.simulator import run_match
        from engine.agents import BaselineAI
        from engine import decklib
        turns = []
        for i in range(3):
            b = run_match(decklib.make_deck("lightning"), decklib.make_deck("fire"),
                          BaselineAI("a"), BaselineAI("b"), seed=21 + i)
            turns.append(b.turn_number)
        self.assertAlmostEqual(res["avg_turns"], sum(turns) / len(turns), places=2)


class TestSSE(unittest.TestCase):
    """SSE：批量/训练进度流（替代轮询）"""

    def test_batch_stream_events(self):
        h = user_headers()
        r = client.post("/batch/jobs", headers=h, json={
            "deck_a": "lightning", "deck_b": "fire", "games": 6,
            "seed_start": 3})
        jid = r.json()["id"]
        events = []
        with client.stream("GET", f"/batch/jobs/{jid}/stream",
                           headers=h) as resp:
            self.assertEqual(resp.status_code, 200)
            self.assertIn("text/event-stream", resp.headers["content-type"])
            for line in resp.iter_lines():
                if line.startswith("event:"):
                    events.append(line.strip())
                if "event: end" in line or len(events) > 8:
                    break
        self.assertTrue(any("update" in e for e in events), events)

    def test_train_stream_requires_job(self):
        h = user_headers()
        r = client.get("/train/jobs/no_such_job/stream", headers=h)
        self.assertEqual(r.status_code, 404)


class TestCompliance(unittest.TestCase):
    """合规 6.1（内容）与 6.2（隐私）：声明机器可读 + 实现状态如实暴露"""

    def test_content_statement(self):
        r = client.get("/compliance")
        self.assertEqual(r.status_code, 200, r.text)
        j = r.json()
        c = j["content_compliance"]
        self.assertEqual(c["id"], "6.1")
        # 原文关键要素
        for kw in ("教学研究与算法演示", "归属其权利方", "显著方式予以标识",
                   "责任由使用者承担"):
            self.assertIn(kw, c["statement"])
        self.assertTrue(c["research_only"])
        self.assertTrue(c["no_endorsement"])
        self.assertTrue(c["attribution_required"])
        self.assertIn("Nintendo", c["attribution_template"])
        self.assertIn("The Pokémon Company", c["attribution_template"])

    def test_material_policy_allows_images_with_attribution(self):
        """C2 v1.1：卡名/规则/图像可用（须标注），但不得声称关联"""
        c = client.get("/compliance").json()["content_compliance"]
        self.assertEqual(c["usage_policy"]["card_images"],
                         "permitted_with_attribution")
        self.assertEqual(c["usage_policy"]["decklist_text"],
                         "permitted_with_attribution")
        red = {x["id"]: x for x in client.get("/compliance").json()["red_lines"]}
        self.assertIn("使用", red["C2"]["statement"])
        self.assertIn("标注权利方", red["C2"]["statement"])
        self.assertIn("revised_from", red["C2"])

    def test_ai_labeling_policy(self):
        c = client.get("/compliance").json()["content_compliance"]
        lab = c["ai_labeling"]
        self.assertEqual(sorted(lab["required_fields"]),
                         ["ai_generated", "ai_notice", "generated_by"])
        self.assertTrue(c["ai_status"]["enforced"])

    def test_privacy_statement_and_impl(self):
        j = client.get("/compliance").json()
        p = j["data_privacy"]
        self.assertEqual(p["id"], "6.2")
        for kw in ("HTTPS", "数据库存储加密", "本地化", "脱敏", "第三方"):
            self.assertIn(kw, p["statement"])
        self.assertTrue(p["transport"]["https_supported"])
        self.assertTrue(p["local_deployment"]["supported"])
        self.assertEqual(p["local_deployment"]["local_only_env"],
                         "POKELAB_LOCAL_ONLY=1")
        self.assertTrue(p["llm_call"]["desensitize_before_send"])
        self.assertTrue(p["llm_call"]["rules"], "应列出脱敏规则")
        self.assertIn("field_encryption", p["storage"])
        self.assertIn("live", p["storage"])
        self.assertTrue(p["no_secondary_use"]["statement"])

    def test_audit_endpoint_requires_admin(self):
        # guest（无令牌）
        self.assertEqual(client.get("/compliance/audit").status_code, 403)
        # 普通用户
        h = user_headers()
        if _TOKEN.get("role") == "admin":
            self.skipTest("首个用户为 admin，无法用普通用户验证")
        self.assertEqual(client.get("/compliance/audit", headers=h).status_code,
                         403)

    def test_root_advertises_compliance(self):
        j = client.get("/").json()
        self.assertIn("compliance_detail", j)
        self.assertIn("ai_notice", j)


class TestAiLabeledCardPayload(unittest.TestCase):
    """6.1：卡库中的机器/AI 解析结果必须带显著标识"""

    def _parsed_card(self):
        h = user_headers()
        r = client.get("/cards?q=dragapult", headers=h).json()
        self.assertTrue(r["items"])
        cid = r["items"][0]["id"]
        d = client.get(f"/cards/{cid}", headers=h).json()
        return d

    def test_detail_has_attribution_and_ai_fields(self):
        d = self._parsed_card()
        self.assertIn("attribution", d)
        self.assertIn("Nintendo", d["attribution"])
        self.assertIn("effects_parsed", d)
        self.assertIn("ai_generated", d)

    def test_parsed_effects_labeled(self):
        d = self._parsed_card()
        ep = d["effects_parsed"]
        self.assertIsNotNone(ep, "该卡应有解析结果（ETL --parse-effects 已跑）")
        self.assertTrue(ep["ai_generated"])
        self.assertIn(ep["generated_by"], ("rule", "llm"))
        self.assertIn("研究参考", ep["ai_notice"])
        self.assertTrue(ep["specs"], "应产出 spec")
        self.assertIn("unsupported", ep)

    def test_list_items_flag_ai_and_stats_report_notice(self):
        h = user_headers()
        lst = client.get("/cards?page_size=5", headers=h).json()
        self.assertIn("ai_generated", lst["items"][0])
        self.assertIn("attribution", lst)
        s = client.get("/cards/stats", headers=h).json()
        self.assertGreater(s["ai_parsed_cards"], 0)
        self.assertIn("研究参考", s["ai_notice"])
        self.assertIn("Nintendo", s["attribution"])
        self.assertIn("with_image", s)


class TestFieldEncryption(unittest.TestCase):
    """6.2：数据库存储加密（Fernet 字段级加密，可选启用）"""

    def test_status_reports_availability(self):
        from apps.api.app import crypto
        st = crypto.status()
        self.assertIn("available", st)
        self.assertIn("enabled", st)
        self.assertIn("POKELAB_FIELD_ENC=1", st["switch_env"])
        self.assertTrue(st["mixed_safe"])

    def test_roundtrip_when_enabled(self):
        import os
        from apps.api.app import crypto
        if not crypto.available():
            self.skipTest("cryptography 不可用")
        saved = os.environ.get("POKELAB_FIELD_ENC")
        os.environ["POKELAB_FIELD_ENC"] = "1"
        try:
            plain = "4 Dreepy TWM 128\n3 Dragapult ex TWM 130"
            enc = crypto.encrypt(plain)
            self.assertTrue(crypto.is_encrypted(enc))
            self.assertNotIn("Dragapult", enc)          # 密文不含明文
            self.assertEqual(crypto.decrypt(enc), plain)
            # 历史明文仍可读（混合状态安全）
            self.assertEqual(crypto.decrypt("plain text"), "plain text")
        finally:
            if saved is None:
                os.environ.pop("POKELAB_FIELD_ENC", None)
            else:
                os.environ["POKELAB_FIELD_ENC"] = saved

    def test_disabled_by_default_keeps_plaintext(self):
        import os
        from apps.api.app import crypto
        saved = os.environ.pop("POKELAB_FIELD_ENC", None)
        try:
            self.assertEqual(crypto.encrypt("hello"), "hello")
        finally:
            if saved is not None:
                os.environ["POKELAB_FIELD_ENC"] = saved


class TestPresetsRealPool(unittest.TestCase):
    """M1：真实卡池预设 A1–A4（60 卡真实卡表 + 覆盖率报告 + 直接对战）"""

    def test_presets_are_real_60card_decks(self):
        h = user_headers()
        r = client.get("/decks/presets", headers=h)
        self.assertEqual(r.status_code, 200, r.text)
        rows = r.json()["presets"]
        self.assertEqual([x["id"] for x in rows], ["A1", "A2", "A3", "A4"])
        for x in rows:
            self.assertEqual(x["cards_total"], 60)
            self.assertGreaterEqual(x["executable_rate"], 0.90)
            self.assertTrue(x["battle_ready"])
            self.assertIn("list_endpoint", x)

    def test_preset_detail_marks_each_card(self):
        h = user_headers()
        d = client.get("/decks/presets/A1", headers=h).json()
        self.assertEqual(sum(c["count"] for c in d["cards"]), 60)
        statuses = {c["status"] for c in d["cards"]}
        self.assertIn("mapped", statuses)
        self.assertTrue(statuses <= {"mapped", "approximated", "replaced"})
        # Dragapult 线在真实卡池中必须完整可映射
        drag = [c for c in d["cards"] if c["name"] == "Dragapult ex"][0]
        self.assertEqual(drag["count"], 3)
        self.assertEqual(drag["status"], "mapped")

    def test_preset_battle_runs(self):
        h = user_headers()
        r = client.post("/battles", headers=h,
                        json={"deck_a": "A1", "deck_b": "A3", "seed": 7})
        self.assertEqual(r.status_code, 201, r.text)
        body = r.json()
        self.assertGreaterEqual(len(body["view"]["legal"]), 1)
        # 未知预设名 → 400 且提示可用范围
        bad = client.post("/battles", headers=h, json={"deck_a": "Z9"})
        self.assertEqual(bad.status_code, 400)

    def test_import_real_decklist_is_matched(self):
        """F3：粘贴真实卡表（Limitless 行格式）→ 卡名须能在真实卡池命中"""
        h = user_headers()
        text = "\n".join(["4 Dreepy TWM 128", "4 Drakloak TWM 129",
                          "3 Dragapult ex TWM 130", "4 Buddy-Buddy Poffin TEF 144",
                          "4 Ultra Ball SVI 196", "8 Basic Psychic Energy SVE 5"])
        r = client.post("/decks/import", headers=h,
                        json={"text": text, "name": "真实卡表用例"})
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()
        matched = {e["name"] for e in d["parsed"] if e["matched"]}
        self.assertIn("Dragapult ex", matched)
        self.assertIn("Buddy-Buddy Poffin", matched)
        self.assertGreaterEqual(d["summary"]["engine_ready"], 15)


class TestSSEResume(unittest.TestCase):
    """SSE 心跳 + 断线续传（Last-Event-ID 重放）"""

    def _post_batch(self, games=60):
        r = client.post("/batch/jobs",
                        json={"games": games, "seed_start": 11},
                        headers=user_headers())
        self.assertIn(r.status_code, (200, 202), r.text)
        return r.json()["id"]

    def test_stream_head_and_event_ids(self):
        jid = self._post_batch(60)
        got_ids, kinds = [], []
        with client.stream("GET", f"/batch/jobs/{jid}/stream",
                           headers=user_headers()) as resp:
            self.assertEqual(resp.status_code, 200)
            self.assertTrue(resp.headers["content-type"]
                            .startswith("text/event-stream"))
            self.assertTrue(resp.headers.get("cache-control"))
            for line in resp.iter_lines():
                if line.startswith("id:"):
                    got_ids.append(int(line.split(":", 1)[1]))
                elif line.startswith("event:"):
                    kinds.append(line.split(":", 1)[1].strip())
                    if len(kinds) >= 2:
                        break
        self.assertIn("update", kinds)
        self.assertTrue(all(b > a for a, b in zip(got_ids, got_ids[1:]))
                        if len(got_ids) > 1 else True, "seq 必须单调递增")

    def test_last_event_id_replays_history(self):
        """先断开（模拟网络抖动），再带 Last-Event-ID 重连 → 收到 replay 回放"""
        jid = self._post_batch(120)
        last = 0
        with client.stream("GET", f"/batch/jobs/{jid}/stream",
                           headers=user_headers()) as resp:
            for line in resp.iter_lines():
                if line.startswith("id:"):
                    last = max(last, int(line.split(":", 1)[1]))
                    break
        time.sleep(1.5)                       # 让任务继续产生事件
        replays = []
        with client.stream("GET", f"/batch/jobs/{jid}/stream?last_event_id={last}",
                           headers={**user_headers(), "Last-Event-ID": str(last)}) as resp:
            self.assertEqual(resp.status_code, 200)
            for line in resp.iter_lines():
                if line.startswith("event:"):
                    k = line.split(":", 1)[1].strip()
                    replays.append(k)
                    if len(replays) >= 3:
                        break
        self.assertIn("replay", replays, "带 Last-Event-ID 重连须回放历史事件")

    def test_bad_last_event_id_is_ignored(self):
        jid = self._post_batch(20)
        with client.stream("GET", f"/batch/jobs/{jid}/stream?last_event_id=abc",
                           headers=user_headers()) as resp:
            self.assertEqual(resp.status_code, 200)
            first = next(l for l in resp.iter_lines() if l.startswith("event:"))
        self.assertIn("update", first)

    def test_sse_parser(self):
        from apps.api.app.sse import parse_last_event_id
        self.assertEqual(parse_last_event_id(None, None), 0)
        self.assertEqual(parse_last_event_id(None, "5"), 5)
        self.assertEqual(parse_last_event_id(None, "bad"), 0)
        self.assertEqual(parse_last_event_id(None, "-3"), 0)


class TestSSEHeartbeat(unittest.IsolatedAsyncioTestCase):
    """心跳注释行（`: ping`）+ 终态收尾（直接单测事件生成器）"""

    async def test_heartbeat_and_end(self):
        from apps.api.app.sse import event_stream
        state = {"n": 0}

        def snap():
            state["n"] += 1
            if state["n"] >= 3:
                return {"status": "done"}
            return {"status": "running", "progress": {"done": state["n"]}}

        chunks = []
        async for c in event_stream(snap, interval=0.02, heartbeat_s=0.01):
            chunks.append(c)
            if len(chunks) > 40:
                break
        text = "".join(chunks)
        self.assertIn("retry: 3000", text)
        self.assertIn("event: update", text)
        self.assertIn("event: end", text)
        self.assertIn(": ping", text, "空闲等待须发送心跳注释行")

    async def test_replay_prepended(self):
        from apps.api.app.sse import event_stream

        def snap():
            return {"status": "done"}

        def history(seq):
            return [{"seq": seq + 1, "kind": "progress", "data": {"done": 10}}]

        chunks = []
        async for c in event_stream(snap, interval=0.01, heartbeat_s=99,
                                    history=history, last_event_id=3):
            chunks.append(c)
        text = "".join(chunks)
        self.assertIn("event: replay", text)
        self.assertIn("id: 4", text)
        self.assertLess(text.index("event: replay"), text.index("event: update"))


class TestI18nZhLabels(unittest.TestCase):
    """i18n：卡名 / 招式 / 卡组名对外展示一律中文（英文仍为数据键）"""

    def test_presets_label_is_chinese(self):
        h = user_headers()
        rows = client.get("/decks/presets", headers=h).json()["presets"]
        self.assertEqual(rows[0]["label"], "多龙巴鲁托ex / 黑夜魔灵")
        self.assertEqual(rows[0]["label_en"], "Dragapult ex / Dusknoir")
        self.assertEqual(rows[3]["label"], "基础盒（厄诡椪ex / 皮皮ex）")

    def test_preset_detail_cards_have_zh_display(self):
        h = user_headers()
        d = client.get("/decks/presets/A1", headers=h).json()
        by_en = {c["name"]: c for c in d["cards"]}
        self.assertEqual(by_en["Dragapult ex"]["display"], "多龙巴鲁托ex")
        self.assertEqual(by_en["Ultra Ball"]["display"], "高级球")
        self.assertEqual(by_en["Basic Fire Energy"]["display"], "基本火能量")
        self.assertTrue(all(c.get("display") for c in d["cards"]))
        # 英文键保持不变（数据/追溯用）
        self.assertIn("Dragapult ex", by_en)

    def test_battle_view_carries_zh_names(self):
        h = user_headers()
        r = client.post("/battles", headers=h,
                        json={"deck_a": "A1", "deck_b": "A3", "seed": 7})
        self.assertEqual(r.status_code, 201, r.text)
        view = r.json()["view"]
        hand = view["me_state"]["hand"]
        self.assertTrue(all(c.get("display") for c in hand))
        self.assertTrue(any(not c["display"].isascii() for c in hand))
        legal = view["legal"]
        self.assertTrue(legal)
        # 招式/训练家等合法动作应能在前端解析为中文（引擎提供 name_zh/display 依据）
        fp = view["me_state"]["active"] or (view["me_state"]["bench"] or [None])[0]
        if fp:
            for atk in fp["attacks"]:
                self.assertTrue(atk["display"])

    def test_report_and_matrix_are_chinese(self):
        """池任务报告（md/csv）与统计矩阵输出中文卡组名"""
        h = user_headers()
        r = client.post("/batch/pool-jobs", headers=h,
                        json={"decks_a": ["A1"], "decks_b": ["A3"],
                              "games_each": 4, "seed_start": 31})
        self.assertIn(r.status_code, (200, 202), r.text)
        jid = r.json()["id"]
        done = _wait_job(jid, headers=h, timeout=120)
        self.assertEqual(done["status"], "done", done)
        md = client.get(f"/batch/jobs/{jid}/report?format=md", headers=h).text
        self.assertIn("多龙巴鲁托ex", md)
        self.assertIn("N的索罗亚克ex", md)
        csv = client.get(f"/batch/jobs/{jid}/report?format=csv", headers=h).text
        self.assertIn("我方卡组", csv.splitlines()[0])
        self.assertIn("多龙巴鲁托ex", csv)
        js = client.get(f"/batch/jobs/{jid}/report?format=json", headers=h).json()
        cell = js["cells"][0]
        self.assertEqual(cell["deck_a"], "A1")            # 英文键不变
        self.assertEqual(cell["deck_a_zh"], "A1 · 多龙巴鲁托ex / 黑夜魔灵")
        self.assertEqual(cell["deck_b_zh"], "A3 · N的索罗亚克ex")
        m = client.get("/stats/summary", headers=h).json()["matrix"]
        self.assertTrue(m)
        self.assertTrue(all("deck_a_zh" in x and "deck_b_zh" in x for x in m))

    def test_import_warning_uses_zh_card_name(self):
        h = user_headers()
        text = "\n".join(["4 Dragapult ex TWM 130"] * 5)   # 超同名上限
        r = client.post("/decks/import", headers=h,
                        json={"text": text, "name": "中文校验用例"})
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()
        self.assertFalse(d["ok"])
        # 校验错误里出现的是英文名（用户输入原文），但卡池 meta 附带中文展示名供前端渲染
        meta = next(e["meta"] for e in d["parsed"] if e["matched"])
        self.assertEqual(meta["display"], "多龙巴鲁托ex")
        self.assertEqual(meta["name"], "Dragapult ex")


class TestZhCardNameInput(unittest.TestCase):
    """中文卡名/卡组名可作为**输入**（粘贴中文卡表即可导入并对战；数据键仍英文）"""

    ZH_TEXT = "\n".join([
        "4 多龙梅西亚", "4 多龙奇", "3 多龙巴鲁托ex",
        "2 夜巡灵", "1 彷徨夜灵", "1 黑夜魔灵", "2 愿增猿",
        "4 好朋友宝芬", "4 高级球", "3 破坏之锤", "2 老大的指令",
        "2 反攻捕捉器", "3 博士的研究", "2 奇树", "2 神奇糖果",
        "8 基本超能量", "5 基本火能量",
    ])

    def test_import_pure_chinese_text(self):
        h = user_headers()
        r = client.post("/decks/import", headers=h,
                        json={"text": self.ZH_TEXT, "name": "中文卡表"})
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()
        self.assertTrue(d["ok"], d["errors"])
        self.assertEqual(d["summary"]["total"], 52)
        # 每条都归一到英文数据键，并标记输入语言
        by_zh = {e["name"]: e for e in d["parsed"]}
        self.assertEqual(by_zh["多龙巴鲁托ex"]["canonical"], "Dragapult ex")
        self.assertEqual(by_zh["高级球"]["canonical"], "Ultra Ball")
        self.assertEqual(by_zh["多龙巴鲁托ex"]["input_lang"], "zh")
        self.assertTrue(all(e["input_lang"] == "zh" for e in d["parsed"]))

    def test_chinese_deck_is_battle_ready(self):
        h = user_headers()
        d = client.post("/decks/import", headers=h,
                        json={"text": self.ZH_TEXT, "name": "中文卡表"}).json()
        self.assertTrue(d["summary"]["battle_ready"])
        r = client.post("/battles", headers=h,
                        json={"deck_a_id": d["id"],
                              "deck_b": "A3 · N的索罗亚克ex", "seed": 3})
        self.assertEqual(r.status_code, 201, r.text)     # 中文卡组名也解析成功
        view = r.json()["view"]
        self.assertTrue(view["legal"])
        self.assertTrue(all(c.get("display") for c in view["me_state"]["hand"]))

    def test_mixed_language_same_card_merges_count(self):
        """「Dragapult ex」与「多龙巴鲁托ex」按同一张卡合并计数（超上限要报错）"""
        h = user_headers()
        text = "\n".join(["4 Dragapult ex TWM 130", "3 多龙巴鲁托ex TWM 130",
                           "4 好朋友宝芬", "4 高级球"])
        r = client.post("/decks/import", headers=h, json={"text": text})
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()
        self.assertFalse(d["ok"])
        self.assertTrue(any("多龙巴鲁托ex" in e and "同名上限" in e
                            for e in d["errors"]), d["errors"])

    def test_chinese_over_limit_reports_chinese_name(self):
        h = user_headers()
        text = "\n".join(["5 多龙巴鲁托ex", "4 好朋友宝芬"])
        r = client.post("/decks/import", headers=h, json={"text": text})
        d = r.json()
        self.assertFalse(d["ok"])
        self.assertTrue(any("多龙巴鲁托ex" in e for e in d["errors"]), d["errors"])

    def test_export_lang_roundtrip(self):
        h = user_headers()
        did = client.post("/decks/import", headers=h,
                          json={"text": self.ZH_TEXT, "name": "往返"}).json()["id"]
        ex_zh = client.get(f"/decks/{did}/export", headers=h).json()
        self.assertEqual(ex_zh["lang"], "zh")
        self.assertTrue(ex_zh["text"].startswith("4 多龙梅西亚"))
        ex_en = client.get(f"/decks/{did}/export?lang=en", headers=h).json()
        self.assertTrue(ex_en["text"].startswith("4 Dreepy"))
        # 中文导出文本可再导入（往返一致）
        again = client.post("/decks/import", headers=h,
                            json={"text": ex_zh["text"], "name": "往返2"}).json()
        self.assertTrue(again["ok"])
        self.assertEqual(again["summary"]["total"], ex_zh["count"] and 52)

    def test_zh_deck_name_accepted_in_presets_route(self):
        h = user_headers()
        r = client.post("/battles", headers=h,
                        json={"deck_a": "A1 · 多龙巴鲁托ex / 黑夜魔灵",
                              "deck_b": "龙系进化演示卡组", "seed": 5})
        self.assertEqual(r.status_code, 201, r.text)


class TestBattleCommandInput(unittest.TestCase):
    """自由文本指令：中文招式名 / 卡名 / 位置 直接作为 command 提交（F5 增强）"""

    def _new_battle(self, seed=7):
        h = user_headers()
        r = client.post("/battles", headers=h,
                        json={"deck_a": "A1", "deck_b": "A3", "seed": seed})
        self.assertEqual(r.status_code, 201, r.text)
        return h, r.json()

    def test_create_returns_command_catalog(self):
        _, d = self._new_battle()
        self.assertTrue(d["commands"])
        labels = [c["label"] for c in d["commands"]]
        self.assertTrue(any("结束回合" == x for x in labels), labels)
        self.assertTrue(all(c["action"].get("verb") for c in d["commands"]))
        # 招式的展示名随合法动作下发（供前端/指令使用）
        for a in d["view"]["legal"]:
            if a["verb"] == "attack":
                self.assertTrue(a.get("display"))

    def test_command_pass(self):
        h, d = self._new_battle()
        r = client.post(f"/battles/{d['id']}/actions", headers=h,
                        json={"command": "结束回合"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["used_action"]["verb"], "pass")
        self.assertEqual(r.json()["matched"], "结束回合")

    def test_every_label_round_trips_via_api(self):
        """把指令清单里的 label 原样提交，必须全部被接受"""
        h, d = self._new_battle(seed=7)
        bid = d["id"]
        ok = 0
        for _ in range(5):
            st = client.get(f"/battles/{bid}", headers=h).json()
            if st["finished"] or not st["commands"]:
                break
            label = st["commands"][0]["label"]
            r = client.post(f"/battles/{bid}/actions", headers=h,
                            json={"command": label})
            self.assertEqual(r.status_code, 200,
                             f"label「{label}」无法回投：{r.text}")
            self.assertEqual(r.json()["matched"], label)
            ok += 1
        self.assertGreaterEqual(ok, 3)

    def test_command_by_chinese_name(self):
        """用中文卡名（而非索引/卡位）下达指令"""
        h, d = self._new_battle(seed=11)
        bid = d["id"]
        for _ in range(4):
            st = client.get(f"/battles/{bid}", headers=h).json()
            if st["finished"]:
                break
            tr = [c for c in st["commands"] if c["verb"] == "trainer"]
            if tr:
                nm = tr[0]["action"]["display"]
                r = client.post(f"/battles/{bid}/actions", headers=h,
                                json={"command": nm})
                self.assertEqual(r.status_code, 200, r.text)
                self.assertEqual(r.json()["used_action"]["verb"], "trainer")
                return
            first = st["commands"][0]["label"]
            client.post(f"/battles/{bid}/actions", headers=h,
                        json={"command": first})
        self.skipTest("未在若干回合内出现可用训练家卡")

    def test_unknown_command_returns_422_with_candidates(self):
        h, d = self._new_battle()
        r = client.post(f"/battles/{d['id']}/actions", headers=h,
                        json={"command": "这并不是一个动作"})
        self.assertEqual(r.status_code, 422)
        det = r.json()["detail"]
        self.assertIn("error", det)
        self.assertTrue(det["candidates"], "应返回可用候选")
        self.assertIn("legal", det)

    def test_missing_both_fields_returns_422(self):
        h, d = self._new_battle()
        r = client.post(f"/battles/{d['id']}/actions", headers=h, json={})
        self.assertEqual(r.status_code, 422)
        self.assertIn("action", str(r.json()["detail"]))

    def test_named_action_without_index(self):
        """动作里用名称代替索引（如 attack_name）也应被引擎接受"""
        h, d = self._new_battle(seed=7)
        bid = d["id"]
        for _ in range(120):
            st = client.get(f"/battles/{bid}", headers=h).json()
            if st["finished"]:
                break
            atk = [a for a in (st["view"]["legal"] or [])
                   if a["verb"] == "attack"]
            if atk:
                r = client.post(f"/battles/{bid}/actions", headers=h,
                                json={"action": {"verb": "attack",
                                                 "attack_name": atk[0]["display"]}})
                self.assertEqual(r.status_code, 200, r.text)
                self.assertEqual(r.json()["used_action"]["verb"], "attack")
                return
            client.post(f"/battles/{bid}/actions", headers=h,
                        json={"command": st["commands"][0]["label"]})
        self.skipTest("未在若干回合内出现可用招式")

    def test_commands_endpoint(self):
        h, d = self._new_battle()
        r = client.get(f"/battles/{d['id']}/commands", headers=h)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["commands"])

    def test_ambiguous_same_card_different_target_requires_slot(self):
        """同名卡打到不同目标时，仅凭卡名应提示歧义（不得静默选目标）"""
        h, d = self._new_battle(seed=13)
        bid = d["id"]
        for _ in range(120):
            st = client.get(f"/battles/{bid}", headers=h).json()
            if st["finished"]:
                break
            boss = [c for c in st["commands"]
                    if c["verb"] == "trainer" and
                    len({x["action"].get("target") for x in st["commands"]
                         if x["verb"] == "trainer"}) > 1]
            if boss:
                nm = boss[0]["action"]["display"]
                rr = client.post(f"/battles/{bid}/actions", headers=h,
                                 json={"command": nm})
                if rr.status_code == 422:
                    det = rr.json()["detail"]
                    self.assertTrue(det.get("ambiguous"))
                    self.assertTrue(det["candidates"])
                return
            client.post(f"/battles/{bid}/actions", headers=h,
                        json={"command": st["commands"][0]["label"]})
        self.skipTest("未构造出同名多目标局面")


class TestZhDisplayGuard(unittest.TestCase):
    """守卫：真实卡组（A1–A4）对局视图中**所有**卡名/招式/特性都必须带中文展示名

    起因：曾出现「附能量」日志显示英文能量名（Basic Psychic Energy）的疑点，
    此处以自动化断言关闭该不确定性——任何卡池/视图缺 display 都会被抓住。
    """

    NON_ASCII_SAMPLE = 12   # 至少应有多数条目为中文（少量卡可能暂缺中文）

    def _assert_zh(self, objs, where):
        miss = [o.get("name") for o in objs
                if o.get("name") and not (o.get("display") or o.get("name_zh"))]
        self.assertFalse(miss, f"{where} 缺中文展示名（回退英文）：{miss[:6]}")

    def test_all_presets_view_is_fully_chinese(self):
        h = user_headers()
        for aid in ("A1", "A2", "A3", "A4"):
            with self.subTest(archetype=aid):
                r = client.post("/battles", headers=h,
                                json={"deck_a": aid, "deck_b": "A1", "seed": 5})
                self.assertEqual(r.status_code, 201, r.text)
                v = r.json()["view"]
                me = v["me_state"]
                self._assert_zh(me.get("hand") or [], f"{aid} 手牌")
                fp = me.get("active")
                if fp:
                    self._assert_zh([fp], f"{aid} 战斗位")
                    for atk in fp.get("attacks") or []:
                        self.assertTrue(atk.get("display"),
                                        f"{aid} 招式缺 display：{atk}")
                    for ab in fp.get("abilities") or []:
                        self.assertTrue(ab.get("display"),
                                        f"{aid} 特性缺 display：{ab}")
                # 能量卡也要有中文名（本次疑点所在）
                energies = [c for c in (me.get("hand") or [])
                            if c.get("type") == "energy"]
                for e in energies:
                    self.assertTrue(e.get("display") and not e["display"].isascii(),
                                    f"{aid} 能量卡缺中文名：{e}")

    def test_hand_energy_display_matches_catalog(self):
        h = user_headers()
        r = client.post("/battles", headers=h,
                        json={"deck_a": "A1", "deck_b": "A3", "seed": 7})
        v = r.json()["view"]
        energies = [c for c in v["me_state"]["hand"] if c.get("type") == "energy"]
        self.assertTrue(energies, "手牌应至少有一张能量（A1 有 13 张）")
        for e in energies:
            self.assertIn(e["display"], ("基本超能量", "基本火能量", "基本能量"),
                          f"能量展示名异常：{e}")


class TestSequenceCommands(unittest.TestCase):
    """组合指令（多步批量提交）：然后/接着/; 组合串 与 commands 数组"""

    def _battle(self, seed=7):
        h = user_headers()
        r = client.post("/battles", headers=h,
                        json={"deck_a": "A1", "deck_b": "A3", "seed": seed})
        self.assertEqual(r.status_code, 201, r.text)
        return h, r.json()["id"]

    def _first(self, h, bid, pred=lambda lbl: True):
        st = client.get(f"/battles/{bid}", headers=h).json()
        for c in st.get("commands") or []:
            if pred(c["label"]):
                return st, c["label"]
        return st, None

    def test_steps_carry_slim_player_view(self):
        """每步带「该步执行后」的玩家视角局面（已精简掉 legal）→ 供前端逐帧演出

        视角必须锚在玩家侧：若用引擎默认的 `view()`，在「结束回合」移交后会串成对手视角
        （对手手牌泄露）。中间帧不带 `legal` 是有意的（控体积，回放期不用）。
        """
        h, bid = self._battle()
        st, lbl = self._first(h, bid, lambda x: x.startswith("附能量"))
        self.assertIsNotNone(lbl, f"应可用附能量：{st.get('commands')}")
        side = st["view"]["me"]
        r = client.post(f"/battles/{bid}/actions", headers=h,
                        json={"command": f"{lbl} 然后 结束回合"})
        self.assertEqual(r.status_code, 200, r.text)
        steps = [s for s in r.json()["steps"] if s.get("ok")]
        self.assertGreaterEqual(len(steps), 2, "应至少执行两步")
        for s in steps:
            self.assertIsInstance(s.get("view"), dict, f"步骤 {s['input']} 缺 view")
            self.assertEqual(s["view"]["me"], side, "逐步局面视角错（会泄露对手手牌）")
            self.assertNotIn("hand", s["view"]["opp_state"])
            self.assertNotIn("legal", s["view"], "中间帧不应携带 legal")

    def test_two_step_command_string(self):
        h, bid = self._battle()
        st, lbl = self._first(h, bid, lambda x: x.startswith("附能量"))
        self.assertIsNotNone(lbl, f"应可用附能量：{st.get('commands')}")
        r = client.post(f"/battles/{bid}/actions", headers=h,
                        json={"command": f"{lbl} 然后 结束回合"})
        self.assertEqual(r.status_code, 200, r.text)
        j = r.json()
        self.assertEqual(j["planned"], 2)
        self.assertEqual(j["executed"], 2)
        self.assertEqual(len(j["steps"]), 2)
        self.assertTrue(all(x["ok"] for x in j["steps"]))
        self.assertEqual(j["steps"][1]["input"], "结束回合")
        self.assertIn("然后", j["matched"])
        self.assertTrue(j["sequence_hint"])

    def test_two_step_commands_array(self):
        h, bid = self._battle()
        st, lbl = self._first(h, bid, lambda x: x.startswith("附能量"))
        r = client.post(f"/battles/{bid}/actions", headers=h,
                        json={"commands": [lbl, "结束回合"]})
        self.assertEqual(r.status_code, 200, r.text)
        j = r.json()
        self.assertEqual([x["input"] for x in j["steps"]], [lbl, "结束回合"])
        self.assertEqual(j["executed"], 2)

    def test_handover_stops_and_reports_remaining(self):
        """结束回合在前 → 后续步骤属于对手，不执行且如实回报"""
        h, bid = self._battle()
        st, lbl = self._first(h, bid, lambda x: x.startswith("附能量"))
        r = client.post(f"/battles/{bid}/actions", headers=h,
                        json={"commands": ["结束回合", lbl]})
        self.assertEqual(r.status_code, 200, r.text)
        j = r.json()
        self.assertEqual(j["executed"], 1)
        self.assertEqual(j["remaining"], [lbl])
        self.assertIn("回合", j["stopped"] or "")

    def test_first_step_invalid_returns_422_without_side_effect(self):
        """首步非法 → 422 且整单不执行（不留半执行状态）"""
        h, bid = self._battle()
        st = client.get(f"/battles/{bid}", headers=h).json()
        before = st["turn"]
        r = client.post(f"/battles/{bid}/actions", headers=h,
                        json={"command": "这不是一个动作 然后 结束回合"})
        self.assertEqual(r.status_code, 422, r.text)
        det = r.json()["detail"]
        self.assertIn("error", det)
        self.assertEqual(det["steps"], ["这不是一个动作", "结束回合"])
        self.assertTrue(det.get("hint"))
        after = client.get(f"/battles/{bid}", headers=h).json()
        self.assertEqual(after["turn"], before, "422 不应推进对局")

    def test_later_step_failure_returns_200_partial(self):
        """后续步骤失败 → 200 + 部分执行（已执行动作保留）"""
        h, bid = self._battle()
        st, lbl = self._first(h, bid, lambda x: x.startswith("附能量"))
        r = client.post(f"/battles/{bid}/actions", headers=h,
                        json={"command": f"{lbl} 然后 毫不相关的句子"})
        self.assertEqual(r.status_code, 200, r.text)
        j = r.json()
        self.assertEqual(j["executed"], 1)
        self.assertFalse(j["steps"][1]["ok"])
        self.assertEqual(j["remaining"], ["毫不相关的句子"])

    def test_single_command_response_shape_is_uniform(self):
        """单步也应返回 steps 数组（前后端形状一致）"""
        h, bid = self._battle()
        r = client.post(f"/battles/{bid}/actions", headers=h,
                        json={"command": "结束回合"})
        self.assertEqual(r.status_code, 200, r.text)
        j = r.json()
        self.assertEqual(j["planned"], 1)
        self.assertEqual(len(j["steps"]), 1)
        self.assertEqual(j["steps"][0]["input"], "结束回合")

    def test_separators_variants(self):
        """「;」「接着」「再」等分隔词同样生效"""
        for sep in (";", "接着", "然后"):
            h, bid = self._battle(seed=11)
            st, lbl = self._first(h, bid, lambda x: x.startswith("附能量"))
            if not lbl:
                self.skipTest("该种子无附能量指令")
            r = client.post(f"/battles/{bid}/actions", headers=h,
                            json={"command": f"{lbl}{sep}结束回合"})
            self.assertEqual(r.status_code, 200, f"{sep}: {r.text}")
            self.assertEqual(r.json()["planned"], 2, sep)

    def test_max_steps_guard_via_api(self):
        h, bid = self._battle()
        r = client.post(f"/battles/{bid}/actions", headers=h,
                        json={"commands": ["结束回合"] * 12})
        self.assertEqual(r.status_code, 200, r.text)
        j = r.json()
        self.assertLessEqual(j["planned"] or 12, 8 + 12)   # planned 可为原始步数
        self.assertLessEqual(j["executed"], 8)
        self.assertTrue(j["stopped"])


class TestCardLibrary(unittest.TestCase):
    """卡库浏览（F2/M1）：清洗入库的 1267 张卡 —— 列表/搜索/筛选/详情/统计"""

    def test_list_pagination(self):
        h = user_headers()
        r = client.get("/cards?page_size=10", headers=h)
        self.assertEqual(r.status_code, 200, r.text)
        j = r.json()
        self.assertGreater(j["total"], 1000)
        self.assertEqual(len(j["items"]), 10)
        self.assertEqual(j["page"], 1)
        self.assertGreaterEqual(j["pages"], 100)
        self.assertIn("Unofficial", j["declaration"])
        item = j["items"][0]
        for k in ("id", "name", "supertype", "types", "engine_ready"):
            self.assertIn(k, item)

    def test_page_2_differs(self):
        h = user_headers()
        p1 = client.get("/cards?page_size=10&page=1", headers=h).json()
        p2 = client.get("/cards?page_size=10&page=2", headers=h).json()
        self.assertNotEqual([x["id"] for x in p1["items"]],
                            [x["id"] for x in p2["items"]])

    def test_search_en_zh_and_normalized(self):
        h = user_headers()
        en = client.get("/cards?q=dragapult", headers=h).json()
        self.assertEqual(en["total"], 1)
        self.assertEqual(en["items"][0]["name"], "Dragapult ex")
        zh = client.get("/cards?q=多龙", headers=h).json()
        self.assertGreaterEqual(zh["total"], 3)
        # 归一匹配：撇号/空格差异不影响（N’s → nszoroarkex）
        nm = client.get("/cards?q=nszoroarkex", headers=h).json()
        self.assertEqual(nm["total"], 1)
        self.assertIn("Zoroark", nm["items"][0]["name"])

    def test_filters(self):
        h = user_headers()
        tr = client.get("/cards?supertype=Trainer&page_size=5", headers=h).json()
        self.assertTrue(all(x["supertype"] == "Trainer" for x in tr["items"]))
        self.assertGreater(tr["total"], 100)
        ps = client.get("/cards?type=psychic&supertype=Pokemon&page_size=5",
                        headers=h).json()
        self.assertTrue(all("psychic" in x["types"] for x in ps["items"]))
        ready = client.get("/cards?engine_ready=true&page_size=5",
                           headers=h).json()
        self.assertTrue(all(x["engine_ready"] for x in ready["items"]))
        self.assertGreater(ready["total"], 10)
        pend = client.get("/cards?engine_ready=false", headers=h).json()
        self.assertEqual(ready["total"] + pend["total"],
                         client.get("/cards", headers=h).json()["total"])

    def test_sort_and_invalid_page(self):
        h = user_headers()
        by_name = client.get("/cards?sort=name&page_size=5", headers=h).json()
        names = [x["name"].lower() for x in by_name["items"]]
        self.assertEqual(names, sorted(names))
        bad = client.get("/cards?page=0", headers=h)
        self.assertEqual(bad.status_code, 422)          # 分页参数校验

    def test_detail_and_404(self):
        h = user_headers()
        lst = client.get("/cards?q=dragapult", headers=h).json()["items"][0]
        d = client.get(f"/cards/{lst['id']}", headers=h).json()
        self.assertEqual(d["name"], "Dragapult ex")
        self.assertEqual(d["hp"], 320)
        self.assertEqual(len(d["attacks"]), 2)
        atk = next(a for a in d["attacks"] if a["name"] == "Phantom Dive")
        self.assertEqual(sorted(atk["cost"]), ["fire", "psychic"])
        self.assertEqual(atk["damage"], 200)
        self.assertEqual(client.get("/cards/99999999", headers=h).status_code, 404)

    def test_stats(self):
        h = user_headers()
        s = client.get("/cards/stats", headers=h).json()
        self.assertGreater(s["total"], 1000)
        self.assertEqual(s["total"], sum(s["by_supertype"].values()))
        self.assertGreater(s["attacks_total"], 1000)
        self.assertGreater(s["abilities_total"], 100)
        self.assertLess(s["engine_coverage"], 1.0)
        self.assertIn("不可对战", s["note"])

    def test_deck_import_matches_library_only_card(self):
        """仅卡库收录（引擎未建模）的卡应被**识别**并如实标注为灰名单"""
        h = user_headers()
        text = "\n".join([
            "4 多龙梅西亚", "4 多龙奇", "3 多龙巴鲁托ex",
            # 样本卡随建模推进更换（历史上换过两次：Brilliant Blender 第八十三批
            # 落地 → Dusk Ball → **Heavy Baton**，第一百三十四批把 Dusk Ball 的
            # `recon_bottom:` 也建了）。选「长期未建模」的卡才能守住本用例的语义：
            # Heavy Baton 需要道具 `on_ko` 钩子，属结构性缺口。
            "4 Heavy Baton", "4 Hyper Aroma", "8 Basic {P} Energy",
            "8 Basic {R} Energy", "4 好朋友宝芬", "4 高级球", "4 破坏之锤",
            "2 老大的指令", "3 博士的研究", "4 奇树", "2 神奇糖果",
            "4 Night Stretcher", "4 Counter Catcher",
        ])
        r = client.post("/decks/import", headers=h, json={"text": text})
        self.assertEqual(r.status_code, 200, r.text)
        j = r.json()
        by = {e["name"]: e for e in j["parsed"]}
        self.assertTrue(by["Heavy Baton"]["matched"], "卡库独有卡应被识别")
        self.assertFalse(by["Heavy Baton"]["meta"]["engine_ready"])
        self.assertEqual(by["Heavy Baton"]["meta"]["source"], "card-db")
        self.assertTrue(any("引擎未实现卡" in w for w in j["warnings"]), j["warnings"])
        # engine_ready 统计不再恒等于总数（此前的漏算已修）
        self.assertGreater(j["summary"]["not_implemented"], 0)
        self.assertLess(j["summary"]["engine_ready"], j["summary"]["total"])

    def test_basic_energy_alias_matches_both_namings(self):
        """`Basic {P} Energy`（数据集命名）与 `Basic Psychic Energy`（既有命名）都能命中"""
        h = user_headers()
        for nm in ("Basic {P} Energy", "Basic Psychic Energy"):
            r = client.post("/decks/import", headers=h,
                            json={"text": f"4 {nm}\n4 多龙梅西亚\n4 好朋友宝芬"})
            j = r.json()
            hit = [e for e in j["parsed"] if e["name"] == nm]
            self.assertTrue(hit and hit[0]["matched"], f"{nm} 未命中：{j['parsed']}")
            meta = hit[0]["meta"] or {}
            self.assertTrue(meta.get("is_basic_energy"), f"{nm} 未标记为基本能量")


class TestLLMAdminConfig(unittest.TestCase):
    """后台「云端模型」配置（admin）：权限 / 本地模式硬禁 / 密钥加密与脱敏 /
    连通性失败提示 / 清除回退环境变量 / 后台优先于环境变量

    说明：既有 `user_headers()` 依赖「首个注册者即 admin」的历史状态，不稳定；
    这里直接建一个确定的 admin 账号（store.create_user + auth.create_token）。
    """

    ENV_KEYS = ("POKELAB_LLM_BASE", "POKELAB_LLM_KEY", "POKELAB_LLM_MODEL",
                "POKELAB_LOCAL_ONLY")

    @classmethod
    def setUpClass(cls):
        import uuid
        from apps.api.app import auth, llm_admin
        cls.llm_admin = llm_admin
        cls._save_env = {k: os.environ.get(k) for k in cls.ENV_KEYS}
        name = "adm_" + uuid.uuid4().hex[:8]
        store.create_user(name, auth.hash_password("pw123456"), "admin")
        cls.admin = {"Authorization": "Bearer " + auth.create_token(name, "admin")}
        cls._clear_saved()
        for k in cls.ENV_KEYS:
            os.environ.pop(k, None)

    @classmethod
    def _clear_saved(cls):
        from apps.api.app import llm_admin
        for k in (llm_admin.K_BASE, llm_admin.K_MODEL, llm_admin.K_KEY,
                  llm_admin.K_TIMEOUT):
            store.delete_setting(k)
        llm_admin.install_provider()

    @classmethod
    def tearDownClass(cls):
        cls._clear_saved()
        for k, v in cls._save_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        cls.llm_admin.install_provider()

    def setUp(self):
        for k in self.ENV_KEYS:
            os.environ.pop(k, None)
        self._clear_saved()

    def test_requires_admin(self):
        """无令牌 → guest 身份被 require_roles 拒（403）；普通用户同样 403

        注：默认 ``POKELAB_REQUIRE_AUTH=0`` 时无令牌会被解析为 ``guest``，
        因此是 403（已认证但无权限）而非 401（未认证）——两者都属正确拒绝。
        """
        self.assertIn(client.get("/admin/llm/config").status_code, (401, 403))
        u = user_headers()
        if _TOKEN.get("role") != "admin":
            self.assertEqual(
                client.get("/admin/llm/config", headers=u).status_code, 403)

    def test_config_shape(self):
        d = client.get("/admin/llm/config", headers=self.admin).json()
        self.assertIn(d["source"], ("runtime", "env", "none"))
        self.assertEqual([x["id"] for x in d["presets"]], ["deepseek"],
                         "当前阶段仅开放 DeepSeek 预设")
        self.assertIn("compliance", d)
        self.assertIn("local_only", d["compliance"])
        self.assertTrue(all({"id", "label", "base", "model"} <= set(x)
                            for x in d["presets"]))

    def test_put_encrypts_key_and_masks_on_read(self):
        r = client.put("/admin/llm/config", headers=self.admin, json={
            "base": "https://api.deepseek.com/v1", "model": "deepseek-chat",
            "key": "sk-abcdef123456", "timeout": 30})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["source"], "runtime")
        # 库里必须是密文（无条件加密，不依赖 POKELAB_FIELD_ENC 开关）
        row = store.get_setting("llm.key")
        self.assertEqual(row["encrypted"], 1)
        self.assertTrue(row["value"].startswith("enc:v1:"))
        self.assertNotIn("sk-abcdef123456", row["value"])
        # 接口只回显尾 4 位，且不回显中间段
        d = client.get("/admin/llm/config", headers=self.admin).json()
        self.assertTrue(d["effective"]["has_key"])
        self.assertTrue(d["effective"]["key_hint"].endswith("3456"))
        self.assertNotIn("abcdef", d["effective"]["key_hint"])
        self.assertTrue(d["effective"]["external"])

    def test_put_keeps_key_when_blank(self):
        client.put("/admin/llm/config", headers=self.admin, json={
            "base": "https://api.deepseek.com/v1", "model": "deepseek-chat",
            "key": "sk-keep9999", "timeout": 30})
        client.put("/admin/llm/config", headers=self.admin, json={
            "base": "https://api.deepseek.com/v1", "model": "deepseek-reasoner",
            "key": "", "timeout": 30})
        d = client.get("/admin/llm/config", headers=self.admin).json()
        self.assertEqual(d["effective"]["model"], "deepseek-reasoner")
        self.assertTrue(d["effective"]["key_hint"].endswith("9999"),
                        "留空 key 应沿用已存密钥")

    def test_local_only_blocks_external_endpoint(self):
        """6.2：本地化部署 → 硬禁外部云端端点，且提示明确"""
        os.environ["POKELAB_LOCAL_ONLY"] = "1"
        r = client.put("/admin/llm/config", headers=self.admin, json={
            "base": "https://api.deepseek.com/v1", "model": "deepseek-chat",
            "key": "sk-x", "timeout": 30})
        self.assertEqual(r.status_code, 403, r.text)
        self.assertIn("本地化部署", r.json()["detail"])
        # 本机/内网端点仍允许（自建 vLLM / Ollama，数据不出本地）
        r2 = client.put("/admin/llm/config", headers=self.admin, json={
            "base": "http://127.0.0.1:9997/v1", "model": "local", "key": "",
            "timeout": 30})
        self.assertEqual(r2.status_code, 200, r2.text)
        self.assertFalse(r2.json()["external"])

    def test_put_validates_base_and_model(self):
        r = client.put("/admin/llm/config", headers=self.admin, json={
            "base": "api.deepseek.com", "model": "m", "timeout": 30})
        self.assertEqual(r.status_code, 422)
        r2 = client.put("/admin/llm/config", headers=self.admin, json={
            "base": "https://api.deepseek.com/v1", "model": "  ", "timeout": 30})
        self.assertEqual(r2.status_code, 422)

    def test_test_endpoint_reports_failure_with_hint(self):
        """连通性测试：不可达端点 → 200 + ok=false + 可读提示（不抛 500）"""
        client.put("/admin/llm/config", headers=self.admin, json={
            "base": "http://127.0.0.1:9/v1", "model": "x", "key": "",
            "timeout": 10})
        r = client.post("/admin/llm/test", headers=self.admin,
                        json={"prompt": "ping"})
        self.assertEqual(r.status_code, 200, r.text)
        j = r.json()
        self.assertFalse(j["ok"])
        self.assertTrue(j["error"])
        self.assertTrue(j["hint"])

    def test_test_endpoint_blocked_in_local_mode_for_external(self):
        """本地模式 + 外部端点：连通性测试同样被隐私策略拒绝（403）"""
        os.environ["POKELAB_LLM_BASE"] = "https://api.deepseek.com/v1"
        os.environ["POKELAB_LOCAL_ONLY"] = "1"
        self.llm_admin.install_provider()
        r = client.post("/admin/llm/test", headers=self.admin,
                        json={"prompt": "p"})
        self.assertEqual(r.status_code, 403, r.text)
        self.assertIn("隐私策略", r.json()["detail"])

    def test_delete_falls_back_to_env(self):
        client.put("/admin/llm/config", headers=self.admin, json={
            "base": "https://api.deepseek.com/v1", "model": "m", "key": "k",
            "timeout": 30})
        r = client.delete("/admin/llm/config", headers=self.admin)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["source"], "none")
        d = client.get("/admin/llm/config", headers=self.admin).json()
        self.assertFalse(d["configured"])
        # 环境变量存在时回退使用
        os.environ["POKELAB_LLM_BASE"] = "http://127.0.0.1:9/v1"
        os.environ["POKELAB_LLM_MODEL"] = "env-model"
        self.llm_admin.install_provider()
        d2 = client.get("/admin/llm/config", headers=self.admin).json()
        self.assertEqual(d2["source"], "env")
        self.assertEqual(d2["effective"]["model"], "env-model")

    def test_backend_priority_runtime_over_env(self):
        """优先级：后台配置 > 环境变量（两者同时存在）"""
        os.environ["POKELAB_LLM_BASE"] = "http://127.0.0.1:9/v1"
        os.environ["POKELAB_LLM_MODEL"] = "env-model"
        client.put("/admin/llm/config", headers=self.admin, json={
            "base": "http://127.0.0.1:8/v1", "model": "runtime-model",
            "key": "", "timeout": 30})
        from agent import llmclient
        self.assertEqual(llmclient.source(), "runtime")
        self.assertEqual(llmclient.config()["model"], "runtime-model")

    def test_agent_effects_llm_config_uses_runtime(self):
        """K2 效果解析走的也是后台配置（不是只改 UI 展示）"""
        client.put("/admin/llm/config", headers=self.admin, json={
            "base": "http://127.0.0.1:9/v1", "model": "runtime-model",
            "key": "", "timeout": 10})
        from agent import effects
        self.assertEqual(effects.llm_config()["model"], "runtime-model")
        # llm 后端会去调用不可达端点 → 如实回退规则基线并报告错误
        r = effects.parse_effects("造成 30 点伤害。", kind="attack",
                                  backend="llm")
        self.assertIn("fallback", r["backend"])
        self.assertTrue(r["specs"])


class TestCloudSuccessPath(unittest.TestCase):
    """云端**成功路径**端到端：mock OpenAI 兼容服务（127.0.0.1，不依赖公网）

    前面 `TestLLMAdminConfig` 只覆盖了失败/拦截路径；本用例证明配置真的能被
    K2 解析与对战解说用起来（否则「加了接口」只是表面功夫）。
    """

    @classmethod
    def setUpClass(cls):
        import threading
        import uuid
        import importlib.util
        from apps.api.app import auth, llm_admin
        cls._env = {k: os.environ.get(k)
                    for k in ("POKELAB_LOCAL_ONLY", "POKELAB_LLM_BASE")}
        for k in cls._env:
            os.environ.pop(k, None)
        # 起 mock 服务（复用 scripts/mock_openai.py，避免两套实现漂移）
        spec = importlib.util.spec_from_file_location(
            "mock_openai", REPO / "apps" / "api" / "scripts" / "mock_openai.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        from http.server import HTTPServer
        cls.srv = HTTPServer(("127.0.0.1", 0), mod.Handler)   # 0=自动选端口
        cls.port = cls.srv.server_address[1]
        cls.th = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.th.start()
        name = "cld_" + uuid.uuid4().hex[:8]
        store.create_user(name, auth.hash_password("pw123456"), "admin")
        cls.h = {"Authorization": "Bearer " + auth.create_token(name, "admin")}
        for k in (llm_admin.K_BASE, llm_admin.K_MODEL, llm_admin.K_KEY,
                  llm_admin.K_TIMEOUT):
            store.delete_setting(k)
        llm_admin.install_provider()

    @classmethod
    def tearDownClass(cls):
        from apps.api.app import llm_admin
        cls.srv.shutdown()
        cls.srv.server_close()      # 显式关闭监听 socket（避免 ResourceWarning）
        for k in (llm_admin.K_BASE, llm_admin.K_MODEL, llm_admin.K_KEY,
                  llm_admin.K_TIMEOUT):
            store.delete_setting(k)
        llm_admin.install_provider()
        for k, v in cls._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def setUp(self):
        r = client.put("/admin/llm/config", headers=self.h, json={
            "base": f"http://127.0.0.1:{self.port}/v1", "model": "mock-model",
            "key": "sk-mock-9", "timeout": 15})
        assert r.status_code == 200, r.text

    def test_connectivity_test_succeeds(self):
        j = client.post("/admin/llm/test", headers=self.h,
                        json={"prompt": "请只回复四个字：连通正常"}).json()
        self.assertTrue(j["ok"], j)
        self.assertEqual(j["model"], "mock-model")
        self.assertFalse(j["external"], "本机端点不应标记为外部")
        self.assertIn("连通", j["reply_preview"])
        self.assertTrue(j["audited"])

    def test_k2_parsing_uses_cloud(self):
        """后台配置的云端模型必须真的被 K2 效果解析用上"""
        from agent import effects, llmclient
        self.assertEqual(llmclient.source(), "runtime")
        r = effects.parse_effects("This attack does 42 damage.",
                                  kind="attack", name="Mock Card",
                                  backend="llm")
        self.assertEqual(r["backend"], "llm", r.get("errors"))
        self.assertEqual(r["model"], "mock-model")
        acts = (r["specs"] or [{}])[0].get("actions") or []
        self.assertTrue(any(a.get("type") == "damage" for a in acts), acts)

    def test_commentary_uses_cloud(self):
        b = client.post("/battles", headers=self.h,
                        json={"deck_a": "lightning", "deck_b": "fire",
                              "ai_iters": 0})
        bid = b.json()["id"]
        j = client.post(f"/battles/{bid}/commentary", headers=self.h,
                        json={"backend": "llm"}).json()
        self.assertEqual(j["generated_by"], "llm", j.get("errors"))
        self.assertEqual(j["model"], "mock-model")
        self.assertTrue(j["text"])
        self.assertTrue(j["ai_generated"], "C4 标识不可缺")

    def test_audit_written_without_plaintext(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ["POKELAB_AUDIT_LOG"] = str(Path(d) / "audit.jsonl")
            try:
                client.post("/admin/llm/test", headers=self.h,
                            json={"prompt": "秘密邮箱 leak@example.com"})
                p = Path(d) / "audit.jsonl"
                self.assertTrue(p.exists(), "外部调用必须写审计")
                body = p.read_text(encoding="utf-8")
                self.assertNotIn("leak@example.com", body, "审计不得含原文")
                self.assertIn("llm_test", body)
            finally:
                os.environ.pop("POKELAB_AUDIT_LOG", None)


class TestCommentary(unittest.TestCase):
    """对战解说（F5 内）：离线降级可用 + C4 标识齐备 + 不泄露隐藏信息"""

    def _battle(self):
        h = user_headers()
        r = client.post("/battles", headers=h,
                        json={"deck_a": "lightning", "deck_b": "fire",
                              "ai_iters": 0})
        assert r.status_code in (200, 201), r.text
        return h, r.json()["id"]

    def test_commentary_offline_fallback(self):
        h, bid = self._battle()
        c = client.post(f"/battles/{bid}/commentary", headers=h,
                        json={"backend": "rule"})
        self.assertEqual(c.status_code, 200, c.text)
        j = c.json()
        self.assertTrue(j["text"])
        self.assertTrue(j["ai_generated"], "C4：必须带 AI 生成标识")
        self.assertEqual(j["generated_by"], "rule(offline)")
        self.assertTrue(j["ai_notice"])

    def test_commentary_hides_opponent_hand(self):
        h, bid = self._battle()
        j = client.post(f"/battles/{bid}/commentary", headers=h,
                        json={"backend": "rule"}).json()
        self.assertIn("手牌 ? 张", j["board_summary"])
        for card in ("Ember", "Flarex"):
            self.assertNotIn(card, j["board_summary"])

    def test_commentary_unknown_battle_404(self):
        h = user_headers()
        r = client.post("/battles/nope/commentary", headers=h, json={})
        self.assertEqual(r.status_code, 404)


class TestPasswordHashGuard(unittest.TestCase):
    """口令哈希写前校验

    背景：`auth.verify_password` 只认 `pbkdf2_sha256$…`，遇到明文/空串恒返回
    False —— 也就是**传错不报错、只造出一个永远登录不了的账号**。实例里若再没有
    别的管理员，就等于把自己锁在门外。`adm1` 曾以明文 `pw123456` 落库、静默失效
    （登录一律「用户名或密码错误」，从现象看不出是数据坏了），改密时才发现。
    故在 `store` 写入侧主动拒绝，并给引导脚本加 `--audit` 体检。
    """

    def test_reject_plaintext_hash(self):
        for bad in ("pw123456", "ad123456"):
            with self.assertRaises(ValueError):
                store.create_user("zz_guard_" + bad, bad, "user")

    def test_reject_empty_and_none_hash(self):
        for bad in ("", None, "   "):
            with self.assertRaises(ValueError):
                store.create_user("zz_guard_empty", bad, "user")

    def test_reject_malformed_pbkdf2(self):
        # 前缀对但结构残缺：缺段 / 迭代数非数字 / 非十六进制 / 空段
        for bad in ("pbkdf2_sha256$200000$abcd",
                    "pbkdf2_sha256$abc$aa$bb",
                    "pbkdf2_sha256$200000$zz$bb",
                    "pbkdf2_sha256$200000$aa$",
                    "pbkdf2_sha256$200000$$bb"):
            with self.assertRaises(ValueError):
                store.create_user("zz_guard_bad", bad, "user")

    def test_accept_real_hash(self):
        import uuid
        from apps.api.app import auth as _auth
        name = "zz_guard_ok_" + uuid.uuid4().hex[:6]
        store.create_user(name, _auth.hash_password("ad123456"), "user")
        u = store.get_user(name)
        self.assertTrue(store.is_valid_password_hash(u["password_hash"]))
        self.assertTrue(_auth.verify_password("ad123456", u["password_hash"]))
        store.delete_user(name)

    def test_set_user_password_also_guarded(self):
        import uuid
        from apps.api.app import auth as _auth
        name = "zz_guard_set_" + uuid.uuid4().hex[:6]
        store.create_user(name, _auth.hash_password("ad123456"), "user")
        # 明文被拒 → 原口令仍可用（一次失败不会把账号改坏）
        with self.assertRaises(ValueError):
            store.set_user_password(name, "newpassword")
        self.assertTrue(_auth.verify_password(
            "ad123456", store.get_user(name)["password_hash"]))
        # 合法哈希被接受
        store.set_user_password(name, _auth.hash_password("newpassword"))
        self.assertTrue(_auth.verify_password(
            "newpassword", store.get_user(name)["password_hash"]))
        store.delete_user(name)

    def test_audit_cli_flags_locked_account(self):
        """`set_role.py --audit` 必须点名哈希非法的账号（且对管理员给警示）"""
        import subprocess
        import uuid
        name = "zz_locked_" + uuid.uuid4().hex[:6]
        # 绕过守卫直接写坏数据，模拟历史遗留状态
        store._conn.execute(
            "INSERT INTO users(name,role,quota_day,password_hash,created) "
            "VALUES(?,?,10,?,?)", (name, "admin", "pw123456", time.time()))
        store._conn.commit()
        try:
            script = (Path(__file__).resolve().parents[1]
                      / "scripts" / "set_role.py")
            out = subprocess.run(
                [sys.executable, str(script), "--audit"],
                capture_output=True, text=True, encoding="utf-8",
                env={**os.environ, "POKELAB_DATA_DIR": str(_TEST_DATA_DIR)},
                cwd=str(REPO))
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertIn(name, out.stdout, "体检未点名失效账号")
            self.assertIn("永远无法登录", out.stdout)
            self.assertIn("含管理员", out.stdout, "未对管理员失效给出警示")
        finally:
            store.delete_user(name)


class TestSecureDelete(unittest.TestCase):
    """凭据不得以明文残留在数据库文件里

    背景：SQLite 的 `UPDATE`/`DELETE` 默认**只改 btree 指针、不擦旧字节**
    （`secure_delete` 默认为 0）。实测：给 `adm1` 改密后，旧明文 `pw123456`
    仍原样躺在数据页里，`strings api.db` 即可捞出——属于凭据泄漏。
    两层防线：① 连接打开时 `secure_delete=ON`（管**今后**的写入）；
    ② `store.vacuum()` 重建库（清**已经存在**的残留）。
    """

    def test_connection_has_secure_delete_on(self):
        val = store._conn.execute("PRAGMA secure_delete").fetchone()[0]
        self.assertEqual(val, 1, "secure_delete 未开启：改密/删号会留下明文残留")

    def test_changed_password_leaves_no_plaintext(self):
        """改密后，旧口令不得在数据库原始字节中可被检索到"""
        import uuid
        from apps.api.app import auth as _auth
        secret_old = "OLDsecretPLAIN" + uuid.uuid4().hex[:6]
        secret_new = "NEWsecretPLAIN" + uuid.uuid4().hex[:6]
        name = "zz_sec_" + uuid.uuid4().hex[:6]
        store.create_user(name, _auth.hash_password(secret_old), "user")
        store.set_user_password(name, _auth.hash_password(secret_new))
        store.delete_user(name)
        store._conn.commit()
        raw = store.DB_PATH.read_bytes()
        self.assertNotIn(secret_old.encode(), raw,
                         "改密后旧口令仍残留在数据库文件中（明文泄漏）")
        self.assertNotIn(secret_new.encode(), raw,
                         "新口令以明文形式残留（不应被存储）")

    def test_vacuum_removes_preexisting_residue(self):
        """VACUUM 必须能清掉**已经存在**的残留（secure_delete 管不到的历史数据）

        不依赖「UPDATE 是否恰好留下残留」——微观库里 SQLite 常在同页原地覆盖，
        残留不稳定复现（初次写成这样时测试前提就失败了）。改为**直接把明文注入
        文件字节**，稳定构造出「库里已存在明文残留」这一状态。
        """
        import sqlite3
        import uuid
        d = Path(tempfile.mkdtemp(prefix="pokelab_vacuum_"))
        p = d / "api.db"
        secret = "LEGACYplain" + uuid.uuid4().hex[:6]
        con = sqlite3.connect(str(p))
        con.execute("CREATE TABLE users(name TEXT, password_hash TEXT)")
        con.execute("INSERT INTO users VALUES('x', 'v')")
        con.commit()
        page_size = con.execute("PRAGMA page_size").fetchone()[0]
        con.close()

        # 把明文写进第 2 页的空白区（模拟历史残留的真实形态）
        raw = bytearray(p.read_bytes())
        off = page_size * 2 + 200
        raw[off:off + len(secret)] = secret.encode()
        p.write_bytes(bytes(raw))
        self.assertIn(secret.encode(), p.read_bytes(), "注入失败，测试前提不成立")

        # `set_role.py --vacuum` 的底层动作
        con = sqlite3.connect(str(p))
        con.execute("VACUUM")
        con.commit()
        con.close()
        self.assertNotIn(secret.encode(), p.read_bytes(),
                         "VACUUM 未清除历史残留字节")
        # 且数据仍在
        con = sqlite3.connect(str(p))
        self.assertEqual(con.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1)
        con.close()

    def test_vacuum_api_runs(self):
        """store.vacuum() 可调用且不破坏数据"""
        n_before = store._conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        store.vacuum()
        self.assertEqual(
            store._conn.execute("SELECT COUNT(*) FROM users").fetchone()[0],
            n_before)
        self.assertEqual(
            store._conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")


class TestDataDirProbe(unittest.TestCase):
    """可写性探测不得在数据目录留下任何文件

    背景：旧实现「建 `_wprobe.db` 写一行再删」，但删除这一步不可控——
      · Windows 上 SQLite 释放句柄有延迟，unlink 抛 PermissionError，
        而异常被 `except: pass` 吞掉 → 每次探测都留下 8 KB 垃圾文件；
      · 本项目沙箱更狠：unlink 被安全层拦截后**先把文件截断为 0** 再 fail-closed
        抛错 → 留下 0 字节的 `_wprobe.db`（实测复现，日志
        `SAFE_DELETE_FAIL_CLOSED / windows-sandbox-recycle-bin-unavailable`）。
    结论：「探测的副作用必须能 100% 撤销」在有拦截的环境不成立，
    故改为**完全不创建文件**（只 mkdir + 权限检查），真正的落库探测交给
    `_open_db()` 对真实数据库做。
    """

    def test_probe_leaves_no_file_at_all(self):
        d = Path(tempfile.mkdtemp(prefix="pokelab_probe_"))
        self.assertTrue(store._writable_dir(d))
        self.assertEqual(list(d.iterdir()), [],
                         "探测产生了文件（应完全不创建文件）")

    def test_probe_specific_no_wprobe_leftover(self):
        """回归守卫：`_wprobe.db` 这个名字不该再出现"""
        d = Path(tempfile.mkdtemp(prefix="pokelab_probe_"))
        store._writable_dir(d)
        self.assertFalse((d / "_wprobe.db").exists(),
                         "_wprobe.db 又被留下了")

    def test_probe_reports_unwritable_dir(self):
        """不可写目录应返回 False 而不是抛异常"""
        d = Path(tempfile.mkdtemp(prefix="pokelab_probe_")) / "sub"
        d.parent.joinpath("sub").write_text("x")     # 用文件占住目录名
        self.assertFalse(store._writable_dir(d))

    def test_probe_repeated_calls_stay_clean(self):
        d = Path(tempfile.mkdtemp(prefix="pokelab_probe_"))
        for _ in range(5):
            self.assertTrue(store._writable_dir(d))
        self.assertEqual(list(d.iterdir()), [], "重复探测累积了残留")

    def test_real_data_dir_has_no_probe_leftover(self):
        """当前实际数据目录里不应有探测残留"""
        self.assertFalse((Path(store.DATA_DIR) / "_wprobe.db").exists(),
                         f"{store.DATA_DIR} 里仍有 _wprobe.db 残留")


if __name__ == "__main__":
    unittest.main(verbosity=2)
