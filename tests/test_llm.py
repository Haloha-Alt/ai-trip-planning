# @author: 甲
# @reviewer: 乙
# @since: 2026-10-28
"""大模型接入测试：**全程不联网**。

用假 client 验的是三件事：
1. 没 Key 时行为与接模型之前**完全一致**（这是断网答辩的底线）；
2. 模型返回垃圾 / 超时 / 非 JSON 时，都要安静地退回规则结果；
3. 模型返回正常补丁时，补丁要真的生效。

跑法（项目根目录）：
    .venv\\Scripts\\python.exe -m unittest tests.test_llm -v
"""

import unittest

from engine.config import load_scenario
from engine.models import PlanRequest
from llm import parser
from llm.client import LLMError, NullClient, _safe_json
from llm.guard import guarded_json, guarded_text


class FakeClient:
    """可控的假 client：想让它返回什么就给什么。"""

    enabled = True
    model = "fake"

    def __init__(self, reply="{}", raise_error=None):
        self.reply = reply
        self.raise_error = raise_error
        self.calls = 0

    def chat(self, system, user, **kwargs):
        self.calls += 1
        if self.raise_error:
            raise self.raise_error
        return self.reply

    def json_chat(self, system, user, **kwargs):
        from llm.client import _safe_json

        text = self.chat(system, user, **kwargs)
        obj = _safe_json(text)
        if obj is None:
            raise LLMError("不是 JSON")
        return obj


class TestSafeJson(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(_safe_json('{"a": 1}'), {"a": 1})

    def test_fenced(self):
        self.assertEqual(_safe_json('```json\n{"a": 1}\n```'), {"a": 1})

    def test_with_chatter(self):
        self.assertEqual(_safe_json('好的，结果是：{"a": 1} 就是这样'), {"a": 1})

    def test_garbage(self):
        self.assertIsNone(_safe_json("我不是 JSON"))
        self.assertIsNone(_safe_json(""))


class TestNullClient(unittest.TestCase):
    def test_raises_llmerror(self):
        with self.assertRaises(LLMError):
            NullClient().chat("s", "u")

    def test_not_enabled(self):
        self.assertFalse(NullClient().enabled)


class TestGuard(unittest.TestCase):
    def test_null_client_returns_fallback(self):
        warns = []
        out = guarded_json(NullClient(), "s", "u", fallback={"k": 1},
                           use_cache=False, on_warn=warns.append)
        self.assertEqual(out, {"k": 1})
        self.assertTrue(warns)

    def test_bad_json_falls_back(self):
        c = FakeClient("我不是 JSON")
        warns = []
        out = guarded_json(c, "s", "u", fallback={"k": 1}, retries=1,
                           use_cache=False, on_warn=warns.append)
        self.assertEqual(out, {"k": 1})
        self.assertEqual(c.calls, 2)          # 默认重试 1 次 = 共 2 次

    def test_timeout_falls_back(self):
        c = FakeClient(raise_error=LLMError("超时"))
        out = guarded_json(c, "s", "u", fallback={"k": 2}, retries=1, use_cache=False)
        self.assertEqual(out, {"k": 2})

    def test_good_json_passthrough(self):
        c = FakeClient('{"days": 3}')
        out = guarded_json(c, "s", "u", fallback=None, use_cache=False)
        self.assertEqual(out, {"days": 3})
        self.assertEqual(c.calls, 1)          # 成功就不重试

    def test_text_guard(self):
        c = FakeClient("这是一句解说")
        self.assertEqual(guarded_text(c, "s", "u", fallback="兜底", use_cache=False),
                         "这是一句解说")
        bad = FakeClient(raise_error=LLMError("挂了"))
        self.assertEqual(guarded_text(bad, "s", "u", fallback="兜底", use_cache=False), "兜底")


class TestParserWithLLM(unittest.TestCase):
    def setUp(self):
        self.cfg = load_scenario("demo")

    def test_no_client_keeps_rule_result(self):
        """不传 client（且没配 Key）时，结果必须与规则解析一致。"""
        got = parser.parse("想走走自然类的地方，预算 100 元，别太赶", self.cfg)
        want = parser._rule_parse("想走走自然类的地方，预算 100 元，别太赶", self.cfg)
        self.assertEqual(got.to_dict(), want.to_dict())

    def test_null_client_keeps_rule_result(self):
        got = parser.parse("预算 100 元", self.cfg, NullClient())
        want = parser._rule_parse("预算 100 元", self.cfg)
        self.assertEqual(got.to_dict(), want.to_dict())

    def test_model_patch_applies(self):
        c = FakeClient('{"city": "大连市", "days": 3, "top_n": 4, "pace": "packed"}')
        got = parser.parse("随便走走", self.cfg, c)
        self.assertEqual(got.city, "dalian")       # 中文名要被规范化成 key
        self.assertEqual(got.days, 3)
        self.assertEqual(got.top_n, 4)
        self.assertEqual(got.pace, "packed")

    def test_model_garbage_keeps_rule_result(self):
        c = FakeClient("抱歉我做不到")
        got = parser.parse("预算 100 元，别太赶", self.cfg, c)
        want = parser._rule_parse("预算 100 元，别太赶", self.cfg)
        self.assertEqual(got.to_dict(), want.to_dict())

    def test_bad_time_from_model_is_ignored(self):
        """模型给了非法时间，不能把规则结果搞坏。"""
        c = FakeClient('{"start_time": "99:99", "end_time": "早上"}')
        got = parser.parse("9点到18点", self.cfg, c)
        self.assertRegex(got.start_time, r"^\d{2}:\d{2}$")
        self.assertGreater(got.end_time, got.start_time)

    def test_inverted_window_is_repaired(self):
        c = FakeClient('{"start_time": "18:00", "end_time": "09:00"}')
        got = parser.parse("随便", self.cfg, c)
        self.assertGreater(got.end_time, got.start_time)

    def test_must_visit_from_model(self):
        c = FakeClient('{"must_visit": ["星海广场"]}')
        got = parser.parse("想去星海广场", self.cfg, c)
        self.assertEqual(got.must_visit, ["星海广场"])

    def test_parse_never_raises_on_empty(self):
        for text in ("", None, "   ", "?????"):
            self.assertIsInstance(parser.parse(text, self.cfg, FakeClient("x")), PlanRequest)


if __name__ == "__main__":
    unittest.main()
