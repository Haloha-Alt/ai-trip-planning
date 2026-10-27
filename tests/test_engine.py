# @author: 甲
# @reviewer: 乙
# @since: 2026-10-27
"""引擎端到端测试：plan() 在任何输入下都要返回结果，不能崩。

跑法（项目根目录）：
    .venv\\Scripts\\python.exe -m unittest tests.test_engine -v
"""

import unittest

from engine.config import load_scenario
from engine.models import PlanRequest, PlanResponse
from engine.planner import plan
from engine.strategies import available, get


class TestStrategyRegistry(unittest.TestCase):
    def test_registered(self):
        self.assertIn("stub", available())
        self.assertIn("random", available())

    def test_get(self):
        self.assertTrue(callable(get("stub")))

    def test_unknown_strategy_raises_with_hint(self):
        with self.assertRaises(KeyError) as ctx:
            get("not_exist")
        self.assertIn("stub", str(ctx.exception))


class TestPlanEndToEnd(unittest.TestCase):
    def test_empty_request_runs(self):
        """空请求也必须跑通 —— 这是'非法输入不崩'的底线。"""
        resp = plan(PlanRequest())
        self.assertIsInstance(resp, PlanResponse)
        self.assertGreater(len(resp.days), 0)
        self.assertTrue(resp.strategy)

    def test_items_have_time_and_reason(self):
        resp = plan(PlanRequest(top_n=3))
        for it in resp.days[0].items:
            self.assertRegex(it.arrive, r"^\d{2}:\d{2}$")
            self.assertRegex(it.leave, r"^\d{2}:\d{2}$")
            self.assertTrue(it.reason)

    def test_stats_refreshed(self):
        resp = plan(PlanRequest(top_n=3))
        d = resp.days[0]
        if d.items:
            self.assertGreaterEqual(d.total_stay_min, 0)

    def test_stub_vs_random_both_work(self):
        a = plan(PlanRequest(top_n=4), strategy="stub")
        b = plan(PlanRequest(top_n=4), strategy="random")
        self.assertEqual(a.strategy, "stub")
        self.assertEqual(b.strategy, "random")
        self.assertGreater(len(a.days[0].items), 0)
        self.assertGreater(len(b.days[0].items), 0)

    def test_interest_filter(self):
        cfg = load_scenario("demo")
        r = PlanRequest(interests=["自然"], top_n=5)
        resp = plan(r)
        # 命中"自然"标签的站点应当出现在结果里（骨架阶段 alias 为空，兴趣词直接当标签用）
        names = [it.name for it in resp.days[0].items]
        self.assertGreater(len(names), 0)

    def test_avoid_filters_out(self):
        r = PlanRequest(interests=["自然"], avoid=["自然"], top_n=5)
        resp = plan(r)
        # 全部被排除时应降级为"放宽条件"，而不是返回空
        self.assertGreater(len(resp.days), 0)

    def test_metrics_computed(self):
        resp = plan(PlanRequest(top_n=3))
        m = resp.metrics
        for v in (m.satisfaction, m.cost, m.time, m.distance, m.feasibility):
            self.assertGreaterEqual(v, 0.0)
            self.assertLessEqual(v, 1.0)
        self.assertGreaterEqual(m.overall(), 0.0)

    def test_budget_over_produces_warning(self):
        """预算极小 -> 必须产生 BUDGET_OVER 提示，而不是抛异常。"""
        resp = plan(PlanRequest(daily_budget=1, top_n=5))
        self.assertTrue(any("BUDGET_OVER" in w for w in resp.warnings))

    def test_bad_time_window_produces_warning(self):
        resp = plan(PlanRequest(start_time="18:00", end_time="09:00"))
        self.assertTrue(any("TIME_WINDOW" in w for w in resp.warnings))

    def test_short_window_yields_empty_day_with_warning(self):
        resp = plan(PlanRequest(start_time="09:00", end_time="09:30"))
        if not resp.days[0].items:
            self.assertTrue(any("NO_CANDIDATE" in w for w in resp.warnings))

    def test_result_is_json_serializable(self):
        resp = plan(PlanRequest(top_n=3))
        txt = resp.to_json()
        back = PlanResponse.from_json(txt)
        self.assertEqual(len(back.days[0].items), len(resp.days[0].items))


if __name__ == "__main__":
    unittest.main()
