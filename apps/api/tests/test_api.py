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
    def test_batch_and_report(self):
        r = client.post("/batch/jobs", json={"deck_a": "lightning",
                                             "deck_b": "fire",
                                             "games": 3,
                                             "seed_start": 999})
        self.assertEqual(r.status_code, 202, r.text)
        jid = r.json()["id"]
        done = _wait_job(jid)
        self.assertEqual(done["status"], "done")
        self.assertEqual(done["result"]["n"], 3)
        rep = client.get(f"/batch/jobs/{jid}/report?format=md")
        self.assertEqual(rep.status_code, 200)
        self.assertIn("非官方数据", rep.text)
        jrep = client.get(f"/batch/jobs/{jid}/report?format=json").json()
        self.assertIn("win_rate_a", jrep)

    def test_cancel_keeps_partial(self):
        r = client.post("/batch/jobs", json={"deck_a": "lightning",
                                             "deck_b": "fire",
                                             "games": 300,
                                             "seed_start": 7})
        self.assertEqual(r.status_code, 202, r.text)
        jid = r.json()["id"]
        time.sleep(0.4)
        client.post(f"/batch/jobs/{jid}/cancel")
        end = _wait_job(jid)
        self.assertIn(end["status"], ("cancelled", "done"))
        if end["status"] == "cancelled":
            self.assertIn("n", end["result"])


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
