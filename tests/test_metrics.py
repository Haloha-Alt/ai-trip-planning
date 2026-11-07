# @author: 助手
# @since: 2026-11-07
"""v1.1(C5) 结构化日志与 /api/metrics 的离线测试。

验收口径：每次请求一行 JSON 日志（ts/path/status/elapsed_ms）落 logs/requests.log；
metrics 出 P50/P95 与请求数；不影响业务结果。
跑法：python -m unittest tests.test_metrics -v
"""
import json
import unittest

from api.routes import ROUTES, _LATENCIES, dispatch_logged
from engine.models import PlanRequest
from engine.planner import plan


class TestMetrics(unittest.TestCase):
    def test_route_registered(self):
        paths = {p for _, p, _ in ROUTES}
        self.assertIn("/api/metrics", paths)

    def test_dispatch_logged_returns_same_result(self):
        """日志中间件不改变业务结果。"""
        s1, p1 = dispatch_logged("GET", "/api/health", {}, {})
        self.assertEqual(s1, 200)
        self.assertTrue(p1["ok"])

    def test_metrics_endpoint_after_traffic(self):
        _LATENCIES.clear()
        for _ in range(3):
            dispatch_logged("GET", "/api/health", {}, {})
        status, payload = dispatch_logged("GET", "/api/metrics", {}, {})
        self.assertEqual(status, 200)
        self.assertGreaterEqual(payload["requests"], 3)
        self.assertGreater(payload["p95_ms"], 0)
        self.assertLessEqual(payload["p50_ms"], payload["p95_ms"] + 1e-9)

    def test_log_file_has_json_line(self):
        import tempfile
        import api.routes as routes
        # 临时把日志路径指到 temp，避免污染项目 logs/
        old = routes._LOG_PATH
        routes._LOG_PATH = None  # type: ignore[assignment]
        try:
            import pathlib
            tmp = pathlib.Path(tempfile.gettempdir()) / "atp_test_requests.log"
            routes._LOG_PATH = tmp
            if tmp.exists():
                tmp.unlink()
            dispatch_logged("GET", "/api/health", {}, {})
            lines = tmp.read_text(encoding="utf-8").strip().splitlines()
            self.assertTrue(lines)
            rec = json.loads(lines[-1])
            self.assertIn(rec["path"], ("/api/health", "/api/metrics"))
            self.assertIn("elapsed_ms", rec)
        finally:
            routes._LOG_PATH = old


if __name__ == "__main__":
    unittest.main()
