# @author: 甲
# @reviewer: 乙
# @since: 2026-11-05
"""v1.6.2 提速专项测试：**全程不联网**。

锁住四件新行为：
1. 确定性失败（HTTP 4xx，`LLMError.fatal=True`）不再盲重试 —— 鉴权挂掉时
   直接省一整轮 30 秒超时；
2. 401/403 触发鉴权熔断：冷却期内所有调用立即走兜底，连 client 都不碰；
3. 被 `max_tokens` 截断的 JSON 能被 `_repair_truncated_json` 抢救回来；
4. `enhance_plan` 把解说与详细编排**并行**跑，且合并结果完整（meta / tips /
   warnings 去重），两个增强各自挂掉也不拖垮对方。

跑法（项目根目录）：
    .venv\\Scripts\\python.exe -m unittest tests.test_llm_speed -v
"""

import time
import unittest

from llm.client import LLMError, _safe_json
from llm.guard import clear_auth_block, guarded_json


class FakeClient:
    """可控的假 client：记录调用次数，按脚本逐次抛错或返回。"""

    enabled = True
    model = "fake"

    def __init__(self, replies=(), errors=()):
        self.replies = list(replies)
        self.errors = list(errors)
        self.calls = 0

    def chat(self, system, user, **kwargs):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        if self.replies:
            return self.replies.pop(0)
        return "{}"

    def json_chat(self, system, user, **kwargs):
        from llm.client import _safe_json

        text = self.chat(system, user, **kwargs)
        obj = _safe_json(text)
        if obj is None:
            raise LLMError("不是 JSON")
        return obj


class TestFatalNoRetry(unittest.TestCase):
    def setUp(self):
        clear_auth_block()

    def tearDown(self):
        clear_auth_block()

    def test_fatal_error_not_retried(self):
        """401 是确定性失败：retries=1 也只能调 1 次，不能白等第二轮。"""
        c = FakeClient(errors=[LLMError("HTTP 401：Invalid token", fatal=True)])
        out = guarded_json(c, "s", "u", fallback={"k": 1}, retries=1, use_cache=False)
        self.assertEqual(out, {"k": 1})
        self.assertEqual(c.calls, 1)

    def test_non_fatal_still_retries(self):
        """网络抖动（非 fatal）保持原有重试次数。"""
        c = FakeClient(errors=[LLMError("超时"), LLMError("超时")])
        out = guarded_json(c, "s", "u", fallback={"k": 2}, retries=1, use_cache=False)
        self.assertEqual(out, {"k": 2})
        self.assertEqual(c.calls, 2)

    def test_fatal_404_no_auth_block(self):
        """404 等非鉴权的 fatal 失败不触发熔断（模型名写错不该锁 60 秒）。"""
        from llm.guard import _auth_blocked

        c = FakeClient(errors=[LLMError("HTTP 404：not found", fatal=True)])
        guarded_json(c, "s", "u", fallback={"k": 1}, use_cache=False)
        self.assertFalse(_auth_blocked())

    def test_auth_401_triggers_block(self):
        from llm.guard import _auth_blocked

        c = FakeClient(errors=[LLMError("HTTP 401：Invalid token", fatal=True)])
        guarded_json(c, "s", "u", fallback={"k": 1}, use_cache=False)
        self.assertTrue(_auth_blocked())

    def test_auth_block_short_circuits_next_call(self):
        """熔断期内下一次调用直接走兜底，**连 client 都不碰**（calls 保持 0）。"""
        bad = FakeClient(errors=[LLMError("HTTP 401：Invalid token", fatal=True)])
        guarded_json(bad, "s", "u", fallback={"k": 1}, use_cache=False)

        warns = []
        good = FakeClient(replies=['{"ok": 1}'])
        out = guarded_json(good, "s2", "u2", fallback={"k": 0}, use_cache=False,
                           on_warn=warns.append)
        self.assertEqual(out, {"k": 0})
        self.assertEqual(good.calls, 0)
        self.assertTrue(any("冷却" in w or "鉴权" in w for w in warns))


class TestRepairTruncatedJson(unittest.TestCase):
    def test_repair_truncated_object(self):
        """被腰斩的 itinerary JSON：最后一站缺右括号，前几站必须救回来。"""
        truncated = (
            '{"model_active": true, "stops": ['
            '{"name": "图书馆", "what": "安静自习"}, '
            '{"name": "食堂", "what": "午餐"'
        )
        obj = _safe_json(truncated)
        self.assertIsInstance(obj, dict)
        self.assertTrue(obj.get("model_active"))
        self.assertEqual(len(obj.get("stops") or []), 2)

    def test_repair_truncated_in_string(self):
        """截断点落在字符串中间（引号没闭合）也要能补。"""
        truncated = '{"summary": "一整天很充实，先去图书馆看'
        obj = _safe_json(truncated)
        self.assertIsInstance(obj, dict)
        self.assertTrue(str(obj.get("summary", "")).startswith("一整天"))

    def test_repair_truncated_array_item(self):
        """数组最后一项停在引号后逗号前，也救得回来。"""
        truncated = '{"tips": ["第一条", "第二条"'
        obj = _safe_json(truncated)
        self.assertEqual(len(obj.get("tips") or []), 2)

    def test_complete_json_untouched(self):
        """完整 JSON 走正常解析，抢救逻辑不误伤。"""
        self.assertEqual(_safe_json('{"a": 1}'), {"a": 1})

    def test_total_garbage_still_none(self):
        self.assertIsNone(_safe_json("完全不是 JSON 的输出"))


class TestEnhancePlanParallel(unittest.TestCase):
    """enhance_plan：并行执行 + 结果合并 + 单侧失败不拖垮整体。"""

    @classmethod
    def setUpClass(cls):
        from service.usecases import plan_from_text

        cls.resp = plan_from_text("上午自习，中午吃饭，别太赶",
                                  scenario="daily", use_llm=False)

    def setUp(self):
        import service.usecases as uc

        self.uc = uc
        self._orig_narrate = uc.narrate_plan
        self._orig_detailed = uc.detailed_itinerary_plan

    def tearDown(self):
        self.uc.narrate_plan = self._orig_narrate
        self.uc.detailed_itinerary_plan = self._orig_detailed

    def _patch(self, narrate_fn, detailed_fn):
        self.uc.narrate_plan = narrate_fn
        self.uc.detailed_itinerary_plan = detailed_fn

    def test_parallel_runs_both_and_merges(self):
        """两个增强各睡 0.25s：并行总耗时必须明显小于串行（0.5s）。"""
        calls = {"n": 0, "d": 0}

        def fake_narrate(r, enabled=True, model=None):
            calls["n"] += 1
            time.sleep(0.25)
            r.meta["narrative"] = {"summary": "假解说"}
            r.warnings.append("[narrate] 假告警A")
            return r

        def fake_detailed(r, query="", enabled=True, model=None):
            calls["d"] += 1
            time.sleep(0.25)
            r.meta["detailed_itinerary"] = {"model_active": True}
            r.meta["visitor_briefing"] = {"lines": ["带身份证"]}
            r.meta["router"] = {"mode": "rules"}
            r.warnings.append("[itinerary] 假告警B")
            return r

        self._patch(fake_narrate, fake_detailed)
        base = self.uc.plan_from_text("上午自习", scenario="daily", use_llm=False)

        t0 = time.time()
        out = self.uc.enhance_plan(base, query="上午自习", enabled=True)
        elapsed = time.time() - t0

        self.assertEqual(calls, {"n": 1, "d": 1})       # 两边都跑了恰好一次
        self.assertLess(elapsed, 0.45)                  # 串行要 0.5s+，并行应 ≈0.25s
        self.assertEqual(out.meta.get("narrative"), {"summary": "假解说"})
        self.assertEqual(out.meta.get("detailed_itinerary"), {"model_active": True})
        self.assertEqual(out.meta.get("visitor_briefing"), {"lines": ["带身份证"]})
        self.assertEqual(out.meta.get("router"), {"mode": "rules"})
        warns = out.warnings
        self.assertIn("[narrate] 假告警A", warns)
        self.assertIn("[itinerary] 假告警B", warns)
        self.assertEqual(len(warns), len(set(warns)))   # 去重

    def test_one_side_failure_keeps_the_other(self):
        """解说挂了（抛异常），详细编排的产出必须原样合并回来。"""
        def broken_narrate(r, enabled=True, model=None):
            raise RuntimeError("解说炸了")

        def fake_detailed(r, query="", enabled=True, model=None):
            r.meta["detailed_itinerary"] = {"model_active": True}
            return r

        self._patch(broken_narrate, fake_detailed)
        base = self.uc.plan_from_text("上午自习", scenario="daily", use_llm=False)
        out = self.uc.enhance_plan(base, enabled=True)
        self.assertEqual(out.meta.get("detailed_itinerary"), {"model_active": True})

    def test_disabled_runs_sequentially_and_never_calls_models(self):
        """enabled=False（模型开/关对照）：走纯规则模板，两边照常产出。"""
        base = self.uc.plan_from_text("上午自习", scenario="daily", use_llm=False)
        out = self.uc.enhance_plan(base, enabled=False)
        self.assertIn("narrative", out.meta)
        self.assertIn("detailed_itinerary", out.meta)
        detail = out.meta.get("detailed_itinerary") or {}
        self.assertFalse(detail.get("model_active"))    # 关模型必须是规则版


if __name__ == "__main__":
    unittest.main()
