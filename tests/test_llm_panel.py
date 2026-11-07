# @author: 甲
# @reviewer: 乙
# @since: 2026-11-01
"""模型调用面板（E1）的测试：**不联网**。

面板要回答三个问题，测试就盯这三件事：
1. 通没通 —— 没配 Key 时必须老实说"未启用"，不能假装调过模型；
2. 记没记 —— 每次调用要留下 任务/模型/来源/耗时/成败；
3. 顺序对不对 —— 面板是"刚才发生了什么"，最新的必须排在最前面。
"""

import unittest

from api.routes import dispatch
from llm import router
from llm.router import Decision


def _get(path):
    return dispatch("GET", path, {}, {})


class TestLlmEndpoint(unittest.TestCase):
    def setUp(self):
        router.clear_recent()

    def test_shape(self):
        status, payload = _get("/api/llm")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        for key in ("enabled", "model", "router_mode", "roles", "judge", "recent"):
            self.assertIn(key, payload)

    def test_disabled_without_key(self):
        """测试环境钉死 LLM_API_KEY=""，这里必须如实报未启用。"""
        _, payload = _get("/api/llm")
        self.assertFalse(payload["enabled"])
        self.assertEqual(payload["recent"], [])

    def test_router_mode_reported(self):
        _, payload = _get("/api/llm")
        self.assertIn(payload["router_mode"], ("off", "rules", "llm"))

    def test_roles_exposed(self):
        _, payload = _get("/api/llm")
        self.assertIn("default", payload["roles"])


class TestRecentBuffer(unittest.TestCase):
    def setUp(self):
        router.clear_recent()

    def _d(self, model="M", source="rule"):
        return Decision("fast", model, source, "测试")

    def test_record_then_read(self):
        router.record("parse", self._d("GLM-4.6-W8A8"), 123.4, ok=True)
        rs = router.recent()
        self.assertEqual(len(rs), 1)
        self.assertEqual(rs[0]["model"], "GLM-4.6-W8A8")
        self.assertEqual(rs[0]["task"], "parse")
        self.assertEqual(rs[0]["ms"], 123)        # 取整，前端直接拼字符串
        self.assertTrue(rs[0]["ok"])
        self.assertRegex(rs[0]["time"], r"^\d{2}:\d{2}:\d{2}$")

    def test_newest_first(self):
        router.record("parse", self._d("A"), 1)
        router.record("narrate", self._d("B"), 2)
        rs = router.recent()
        self.assertEqual([r["model"] for r in rs], ["B", "A"])

    def test_failure_is_marked(self):
        router.record("narrate", self._d("C"), 30000, ok=False, error="请求超时")
        r = router.recent()[0]
        self.assertFalse(r["ok"])
        self.assertIn("超时", r["error"])

    def test_limit_and_cap(self):
        for i in range(router.MAX_RECENT + 5):
            router.record("parse", self._d("M%d" % i), i)
        self.assertEqual(len(router.recent(100)), router.MAX_RECENT)   # 有上限
        self.assertEqual(len(router.recent(3)), 3)
        self.assertEqual(router.recent(3)[0]["model"], "M%d" % (router.MAX_RECENT + 4))

    def test_clear(self):
        router.record("parse", self._d(), 1)
        self.assertEqual(router.clear_recent(), 1)
        self.assertEqual(router.recent(), [])

    def test_zero_limit(self):
        router.record("parse", self._d(), 1)
        self.assertEqual(router.recent(0), [])


if __name__ == "__main__":
    unittest.main()
