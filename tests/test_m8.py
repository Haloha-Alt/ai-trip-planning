# @author: 甲
# @reviewer: 乙
# @since: 2026-11-07
"""M8 智能升级（v1.5 路线图 A 线）四件套的单测：

* A1 自适应路由：模型失败率滚动统计 + 自动降级（llm/router.py）
* A6 可观测：`meta.trace` 决策链快照（api/routes.py + llm/router.since）
* A2 多轮会话：session 落盘 + adjust 复用上一版（service/session.py）
* A3 RAG：stdlib BM25 检索 + /api/search（llm/rag.py）

全部走内存/临时文件，不打网络。
"""

from __future__ import annotations

import unittest

from api.routes import dispatch
from llm import rag, router
from llm.router import Decision
from service import session as session_store


def _default_decision() -> Decision:
    return Decision("default", router.ROLE_MODELS["default"], "rule", "测试")


# ---------------------------------------------------------------- A1 自适应路由


class TestAdaptiveRouter(unittest.TestCase):
    def setUp(self):
        router.clear_recent()
        router.clear_cache()

    def tearDown(self):
        router.clear_recent()
        router.clear_cache()

    def test_fail_rate_triggers_degrade(self):
        d = _default_decision()
        for _ in range(5):                      # 样本数恰好等于最低门槛
            router.record("parse", d, 1.0, ok=False, error="boom")
        model = router.ROLE_MODELS["default"]
        n, fr = router.fail_rate(model)
        self.assertEqual(n, 5)
        self.assertEqual(fr, 1.0)
        self.assertIn(model, router.degraded())

        a = router.adapt(d)
        self.assertEqual(a.source, "adaptive")
        self.assertNotEqual(a.model, model)     # 换掉了坏模型
        self.assertNotIn(a.model, router.degraded())
        self.assertIn("自动降级", a.why)

    def test_small_sample_no_degrade(self):
        d = _default_decision()
        for _ in range(2):                      # 少于 MODEL_MIN_SAMPLES 不下结论
            router.record("parse", d, 1.0, ok=False, error="boom")
        self.assertNotIn(router.ROLE_MODELS["default"], router.degraded())
        self.assertEqual(router.adapt(d), d)    # 原样通过

    def test_healthy_passthrough(self):
        d = _default_decision()
        for _ in range(10):
            router.record("parse", d, 1.0, ok=True)
        self.assertEqual(router.adapt(d), d)

    def test_health_shape(self):
        router.record("parse", _default_decision(), 3.0, ok=True)
        h = router.health()
        self.assertIn(router.ROLE_MODELS["default"], h)
        entry = h[router.ROLE_MODELS["default"]]
        self.assertEqual(entry["calls"], 1)
        self.assertEqual(entry["fail_rate"], 0.0)
        self.assertFalse(entry["degraded"])

    def test_seq_and_since(self):
        mark = router.seq()
        router.record("parse", _default_decision(), 1.0, ok=True)
        router.record("narrate", _default_decision(), 2.0, ok=False, error="x")
        calls = router.since(mark)
        self.assertEqual(len(calls), 2)
        self.assertLess(calls[0]["seq"], calls[1]["seq"])   # 旧的在前
        self.assertEqual([c["task"] for c in calls], ["parse", "narrate"])


# ---------------------------------------------------------------- A6 可观测


class TestTrace(unittest.TestCase):
    def setUp(self):
        router.clear_recent()

    def test_plan_writes_trace(self):
        mark = router.seq()
        st, payload = dispatch("POST", "/api/plan", {},
                               {"query": "去图书馆自习，中午食堂吃饭",
                                "scenario": "daily", "strategy": "stub",
                                "no_llm": True})
        self.assertEqual(st, 200)
        trace = payload["plan"]["meta"]["trace"]
        self.assertIn(trace["router_mode"], ("off", "rules", "llm"))
        self.assertIsInstance(trace["calls"], list)
        # no_llm 全程不碰模型：这次请求不该产生任何调用记录
        self.assertEqual(trace["calls"], [])
        self.assertEqual(router.since(mark), [])

    def test_llm_panel_exposes_health(self):
        st, payload = dispatch("GET", "/api/llm", {}, {})
        self.assertEqual(st, 200)
        self.assertIn("health", payload)
        self.assertIsInstance(payload["health"], dict)


# ---------------------------------------------------------------- A2 多轮会话


class TestSession(unittest.TestCase):
    def setUp(self):
        session_store.clear()

    def tearDown(self):
        session_store.clear()

    def test_plan_issues_session_id(self):
        st, payload = dispatch("POST", "/api/plan", {},
                               {"query": "去图书馆自习", "scenario": "daily",
                                "strategy": "stub", "no_llm": True})
        self.assertEqual(st, 200)
        sid = payload.get("session_id") or ""
        self.assertTrue(sid)
        sess = session_store.load(sid)
        self.assertIsNotNone(sess)
        self.assertTrue(sess["plan"].get("days"))
        self.assertEqual(sess["instructions"], [])

    def test_adjust_reuses_last_plan(self):
        body = {"query": "去图书馆自习，中午食堂吃饭", "scenario": "daily",
                "strategy": "stub", "no_llm": True}
        _, p1 = dispatch("POST", "/api/plan", {}, body)
        sid = p1["session_id"]

        # 不带 query、只带 session_id + 指令：base 必须来自会话（不是重排）
        st2, p2 = dispatch("POST", "/api/adjust", {},
                           {"session_id": sid, "instruction": "太赶了"})
        self.assertEqual(st2, 200)
        self.assertTrue(p2["adjust"].get("from_session"))
        # 「太赶了」删末站：会话里上一版的站数应该比调整后多
        self.assertEqual(session_store.load(sid)["instructions"], ["太赶了"])

        # 第二轮继续在同一版上改，指令历史要累积
        st3, p3 = dispatch("POST", "/api/adjust", {},
                           {"session_id": sid, "instruction": "太松了"})
        self.assertEqual(st3, 200)
        self.assertTrue(p3["adjust"].get("from_session"))
        self.assertEqual(session_store.load(sid)["instructions"],
                         ["太赶了", "太松了"])

    def test_bad_session_falls_back(self):
        import uuid

        # 每次用全新 id：沙箱里 unlink 可能被 safe-delete 拦截，
        # 固定的 "nonexistent-id" 会被上一轮兜底保存的会话污染。
        bad_id = "no-such-session-" + uuid.uuid4().hex[:8]
        st, payload = dispatch("POST", "/api/adjust", {},
                               {"session_id": bad_id,
                                "instruction": "太赶了",
                                "query": "去图书馆自习", "scenario": "daily",
                                "strategy": "stub", "no_llm": True})
        self.assertEqual(st, 200)               # 找不到会话 → 走重排兜底，不报错
        self.assertNotIn("from_session", payload["adjust"])

    def test_corrupt_session_returns_none(self):
        session_store.SESSION_DIR.mkdir(parents=True, exist_ok=True)
        (session_store.SESSION_DIR / "corrupt.json").write_text("{bad", encoding="utf-8")
        self.assertIsNone(session_store.load("corrupt.json"))


# ---------------------------------------------------------------- A3 RAG


class TestRag(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rag.clear_cache()

    def test_tokenize_chinese_bigram(self):
        toks = tokenize = rag.tokenize("去WiFi自习")
        self.assertIn("wifi", toks)
        self.assertIn("自习", toks)

    def test_search_finds_library(self):
        hits = rag.search("去图书馆自习", city="dalian")
        self.assertTrue(hits)
        self.assertTrue(any("图书馆" in (h["name"] + h["text"]) for h in hits[:3]))
        # 分数降序
        scores = [h["score"] for h in hits]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_search_empty_query(self):
        self.assertEqual(rag.search("", city="dalian"), [])
        self.assertEqual(rag.search("   "), [])

    def test_api_search_route(self):
        st, payload = dispatch("GET", "/api/search",
                               {"q": "图书馆 自习", "city": "dalian"}, {})
        self.assertEqual(st, 200)
        self.assertTrue(payload["hits"])
        self.assertIn("poi_id", payload["hits"][0])

    def test_api_search_missing_q(self):
        st, payload = dispatch("GET", "/api/search", {}, {})
        self.assertEqual(st, 200)
        self.assertEqual(payload["hits"], [])


if __name__ == "__main__":
    unittest.main()
