# @author: 甲
# @reviewer: 乙
# @since: 2026-10-31
"""模型路由测试：**全程不联网**。

验四件事：
1. 规则该认的认对（短需求→fast，长需求→reasoning，长行程→long）；
2. 规则拿不准时，裁判说的话只认 `fast/default/reasoning/long` 四个角色，别的一律不认；
3. 裁判挂了 / 返回垃圾 / 网络断，必须安静回退 default —— 路由不能拖垮主流程；
4. `LLM_ROUTER` 不配时，`wrap()` 原样返回，**行为与接入路由之前一模一样**。

跑法（项目根目录）：
    .venv\\Scripts\\python.exe -m unittest tests.test_router -v
"""

import os
import unittest
from unittest import mock

from llm import router
from llm.router import (
    DEFAULT_ROLE,
    ROLE_MODELS,
    RoutedClient,
    decide,
    rule_role,
    router_mode,
    wrap,
)


AMBIG = "想去图书馆看看书顺便吃个饭，下午再找个安静点的地方坐一会儿休息一下"


class FakeBase:
    """假 client：既能当裁判，也能当被调用的模型。"""

    enabled = True
    model = "fake"

    def __init__(self, reply='{"role": "reasoning", "why": "约束多"}', fail=False):
        self.reply = reply
        self.fail = fail
        self.calls = []

    def chat(self, system, user, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("网关炸了")
        return self.reply

    def json_chat(self, system, user, **kwargs):
        self.chat(system, user, **kwargs)
        return {"ok": True}


class TestRules(unittest.TestCase):
    def test_short_parse_is_fast(self):
        self.assertEqual(rule_role("parse", "上午自习，中午吃饭")[0], "fast")

    def test_long_parse_is_reasoning(self):
        text = "我想三天两晚，预算必须控制在 200 以内，不要爬山，必须有安静的咖啡馆" * 4
        self.assertEqual(rule_role("parse", text)[0], "reasoning")

    def test_many_constraints_is_reasoning(self):
        self.assertEqual(rule_role("parse", "预算 150，必须安静，不要去人多的地方")[0], "reasoning")

    def test_narrate_short_and_long(self):
        self.assertEqual(rule_role("narrate", "第1天 图书馆 120 分钟")[0], "fast")
        self.assertEqual(rule_role("narrate", "第1天 图书馆 120 分钟" * 120)[0], "long")

    def test_unknown_task_left_to_judge(self):
        self.assertEqual(rule_role("whatever", "随便一句话"), (None, ""))

    def test_medium_text_is_ambiguous(self):
        """中等长度、约束不多 —— 规则故意不表态，留给裁判。"""
        text = "想去图书馆看看书顺便吃个饭，下午再找个安静点的地方坐一会儿休息一下"
        self.assertTrue(30 < len(text) < 120)
        self.assertEqual(rule_role("parse", text)[0], None)


class TestDecide(unittest.TestCase):
    def setUp(self):
        router.clear_cache()

    def test_rule_wins_without_extra_call(self):
        base = FakeBase()
        d = decide("parse", "上午自习", client=base, mode="llm")
        self.assertEqual(d.role, "fast")
        self.assertEqual(d.source, "rule")
        self.assertEqual(base.calls, [])          # 规则能定就不请裁判

    def test_judge_decides_when_rules_abstain(self):
        base = FakeBase('{"role": "long", "why": "文本很长"}')
        d = decide("parse", AMBIG, client=base, mode="llm")
        self.assertEqual(d.role, "long")
        self.assertEqual(d.model, ROLE_MODELS["long"])
        self.assertEqual(d.source, "judge")
        self.assertEqual(base.calls[0]["model"], router.JUDGE_MODEL)

    def test_judge_garbage_falls_back(self):
        base = FakeBase("我觉得应该用大模型")
        d = decide("parse", AMBIG, client=base, mode="llm")
        self.assertEqual(d.role, DEFAULT_ROLE)
        self.assertEqual(d.source, "fallback")

    def test_judge_unknown_role_falls_back(self):
        base = FakeBase('{"role": "gpt-4o", "why": "瞎编"}')
        d = decide("parse", AMBIG, client=base, mode="llm")
        self.assertEqual(d.role, DEFAULT_ROLE)

    def test_judge_crash_falls_back(self):
        base = FakeBase(fail=True)
        d = decide("parse", AMBIG, client=base, mode="llm")
        self.assertEqual(d.role, DEFAULT_ROLE)
        self.assertEqual(d.source, "fallback")

    def test_rules_mode_never_calls_out(self):
        base = FakeBase()
        d = decide("parse", AMBIG, client=base, mode="rules")
        self.assertEqual(d.role, DEFAULT_ROLE)
        self.assertEqual(base.calls, [])

    def test_cache_makes_second_call_free(self):
        base = FakeBase('{"role": "long", "why": "长"}')
        decide("parse", AMBIG, client=base, mode="llm")
        d2 = decide("parse", AMBIG, client=base, mode="llm")
        self.assertEqual(d2.source, "cache")
        self.assertEqual(len(base.calls), 1)       # 只问了一次裁判


class TestWrap(unittest.TestCase):
    def setUp(self):
        router.clear_cache()

    def test_off_returns_original(self):
        base = FakeBase()
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LLM_ROUTER", None)
            self.assertIs(wrap(base), base)
            self.assertEqual(router_mode(), "off")

    def test_bad_value_treated_as_off(self):
        with mock.patch.dict(os.environ, {"LLM_ROUTER": "yes-please"}):
            self.assertEqual(router_mode(), "off")

    def test_llm_mode_wraps(self):
        base = FakeBase()
        with mock.patch.dict(os.environ, {"LLM_ROUTER": "llm"}):
            wrapped = wrap(base)
        self.assertIsInstance(wrapped, RoutedClient)
        self.assertTrue(wrapped.enabled)
        self.assertEqual(wrapped.model, "auto")    # guard 的缓存 key 要稳定

    def test_null_client_not_wrapped(self):
        class Nullish:
            enabled = False

        with mock.patch.dict(os.environ, {"LLM_ROUTER": "llm"}):
            self.assertIsInstance(wrap(Nullish()), Nullish)


class TestRoutedClient(unittest.TestCase):
    def setUp(self):
        router.clear_cache()

    def test_chat_switches_model_by_task(self):
        base = FakeBase("解说文本")
        c = RoutedClient(base, mode="rules")

        c.chat("sys", "上午自习，中午吃饭", task="parse")
        self.assertEqual(base.calls[-1]["model"], ROLE_MODELS["fast"])

        c.chat("sys", "第1天 " + "很长" * 700, task="narrate")
        self.assertEqual(base.calls[-1]["model"], ROLE_MODELS["long"])

    def test_task_kwarg_not_leaked_to_base(self):
        base = FakeBase('{"a": 1}')
        c = RoutedClient(base, mode="rules")
        c.json_chat("sys", "上午自习", task="parse")
        self.assertNotIn("task", base.calls[-1])

    def test_records_last_route(self):
        base = FakeBase("x")
        c = RoutedClient(base, mode="rules")
        c.chat("sys", "上午自习", task="parse")
        self.assertEqual(c.last_routes[-1].role, "fast")
        self.assertEqual(c.last_routes[-1].to_dict()["model"], ROLE_MODELS["fast"])

    def test_json_chat_raises_on_garbage(self):
        from llm.client import LLMError

        base = FakeBase("我不是 JSON")
        c = RoutedClient(base, mode="rules")
        with self.assertRaises(LLMError):
            c.json_chat("sys", "上午自习", task="parse")


if __name__ == "__main__":
    unittest.main()
