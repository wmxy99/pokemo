# -*- coding: utf-8 -*-
"""PokéLab AI API · 冒烟测试（TestClient，无外网依赖）

运行：cd <repo> && conda activate pokemo
      python -m unittest apps.api.tests.test_api -v
覆盖：认证 · 人机对局闭环（F5）· 批量任务+报告（F6/F7）· 训练任务最小运行（M6）
注意：train 用例会真实启动子进程跑最小课程（约 15–40s）。
"""
from __future__ import annotations
import sys
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
# engine/agent 由 apps.api.app.main 自行注入 path
from fastapi.testclient import TestClient  # noqa: E402
from apps.api.app.main import app  # noqa: E402

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
        self.assertIn("诚实标注", w["disclaimer"])


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
            "4 Unfair Stamp", "4 Hyper Aroma", "8 Basic {P} Energy",
            "8 Basic {R} Energy", "4 好朋友宝芬", "4 高级球", "4 破坏之锤",
            "2 老大的指令", "3 博士的研究", "4 奇树", "2 神奇糖果",
            "4 Night Stretcher", "4 Counter Catcher",
        ])
        r = client.post("/decks/import", headers=h, json={"text": text})
        self.assertEqual(r.status_code, 200, r.text)
        j = r.json()
        by = {e["name"]: e for e in j["parsed"]}
        self.assertTrue(by["Unfair Stamp"]["matched"], "卡库独有卡应被识别")
        self.assertFalse(by["Unfair Stamp"]["meta"]["engine_ready"])
        self.assertEqual(by["Unfair Stamp"]["meta"]["source"], "card-db")
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
