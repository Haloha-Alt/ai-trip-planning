# @author: 甲
# @reviewer: 乙
# @since: 2026-10-31
"""访问口令测试：**不联网**。

为什么要有这一层
----------------
后端是"拿着学校无限额度 Key 的代理"。一旦 `--host=0.0.0.0` 或用内网穿透暴露出去，
没有口令就等于把 Key 借给所有拿到地址的人。`ACCESS_CODE` 是这个场景的唯一一道门。

验四件事：
1. 没配口令时**完全不影响**（本地开发、测试、答辩默认路径）；
2. 配了口令但没带 → 401，且带 `code_required` 标记（前端靠它决定要不要弹框）；
3. 口令错 → 401；
4. 口令对 → 正常返回。
"""

import os
import unittest
from unittest import mock

from api.routes import dispatch


def _call(query=None):
    return dispatch("GET", "/api/health", query or {}, {})


class TestAccessGate(unittest.TestCase):
    def test_no_code_configured_allows_all(self):
        with mock.patch.dict(os.environ, {"ACCESS_CODE": ""}):
            status, payload = _call()
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])

    def test_missing_code_is_denied(self):
        with mock.patch.dict(os.environ, {"ACCESS_CODE": "s3cret"}):
            status, payload = _call()
        self.assertEqual(status, 401)
        self.assertTrue(payload.get("code_required"))
        self.assertFalse(payload["ok"])

    def test_wrong_code_is_denied(self):
        with mock.patch.dict(os.environ, {"ACCESS_CODE": "s3cret"}):
            status, _ = _call({"code": "guess"})
        self.assertEqual(status, 401)

    def test_right_code_passes(self):
        with mock.patch.dict(os.environ, {"ACCESS_CODE": "s3cret"}):
            status, payload = _call({"code": "s3cret"})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])

    def test_blank_code_in_query_still_denied(self):
        with mock.patch.dict(os.environ, {"ACCESS_CODE": "s3cret"}):
            status, _ = _call({"code": "   "})
        self.assertEqual(status, 401)


if __name__ == "__main__":
    unittest.main()
