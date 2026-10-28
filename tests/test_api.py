# @author: 甲
# @reviewer: 乙
# @since: 2026-10-28
"""接口层测试：**不启服务器**也能验路由 —— 直接调 api/routes.dispatch。

这样测的好处：不依赖 uvicorn/starlette 能不能起来，
真正验的是"给定输入 → 返回什么"，正是接口要做的事。

跑法：
    .venv\\Scripts\\python.exe -m unittest tests.test_api -v
"""

import unittest

from api.routes import ROUTES, dispatch


class TestDispatch(unittest.TestCase):
    def test_health(self):
        status, body = dispatch("GET", "/api/health", {}, None)
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertIn("cities", body)

    def test_options(self):
        status, body = dispatch("GET", "/api/options", {}, None)
        self.assertEqual(status, 200)
        self.assertIn("cities", body)
        self.assertIn("strategies", body)

    def test_plan_from_query(self):
        status, body = dispatch("POST", "/api/plan", {}, {
            "query": "想看海，别太赶",
            "scenario": "demo",
            "strategy": "stub",
            "overrides": {"city": "dalian", "top_n": 2},
        })
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertGreater(len(body["plan"]["days"]), 0)
        self.assertEqual(body["plan"]["request"]["city"], "dalian")
        self.assertIsInstance(body["map"], list)

    def test_plan_from_structured_request(self):
        status, body = dispatch("POST", "/api/plan", {}, {
            "request": {"city": "beijing", "top_n": 2, "interests": ["人文"]},
            "scenario": "demo",
            "strategy": "stub",
        })
        self.assertEqual(status, 200)
        self.assertEqual(body["plan"]["request"]["city"], "beijing")

    def test_unknown_strategy_is_400_not_500(self):
        status, body = dispatch("POST", "/api/plan", {}, {
            "query": "随便", "strategy": "not_exist",
        })
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])

    def test_unknown_path_is_404(self):
        status, body = dispatch("GET", "/api/nope", {}, None)
        self.assertEqual(status, 404)
        self.assertIn("可用", body["error"])

    def test_export_returns_content(self):
        status, body = dispatch("POST", "/api/export", {}, {
            "query": "想看海", "overrides": {"city": "dalian", "top_n": 2},
            "fmt": "md",
        })
        self.assertEqual(status, 200)
        self.assertIn("# 行程单", body["content"])
        self.assertTrue(body["filename"].endswith(".md"))

    def test_reload(self):
        status, body = dispatch("POST", "/api/reload", {}, None)
        self.assertEqual(status, 200)
        self.assertTrue(body["reloaded"])

    def test_routes_declared(self):
        self.assertGreaterEqual(len(ROUTES), 5)

    def test_realtime_off_by_default(self):
        """接口默认不联网 —— 保证脚本与测试行为确定、可复现。"""
        status, body = dispatch("POST", "/api/plan", {}, {
            "query": "想看海", "overrides": {"city": "dalian", "top_n": 2},
        })
        self.assertEqual(status, 200)
        self.assertEqual(body["plan"]["realtime"], {})

    def test_realtime_on_degrades_gracefully(self):
        """显式开实时也不许因为网络问题失败（抓不到就是空快照，不是报错）。"""
        status, body = dispatch("POST", "/api/plan", {}, {
            "query": "想看海", "realtime": True,
            "overrides": {"city": "dalian", "top_n": 2},
        })
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])


class TestAppObject(unittest.TestCase):
    def test_build_app_returns_callable(self):
        """ASGI 应用本身要能构造出来（starlette 在就用 starlette，不在就用兜底）。"""
        from api.server import build_app

        app = build_app()
        self.assertTrue(callable(app))


if __name__ == "__main__":
    unittest.main()
