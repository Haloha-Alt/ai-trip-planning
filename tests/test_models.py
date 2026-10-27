# @author: 甲
# @reviewer: 乙
# @since: 2026-10-27
"""接口契约的单元测试：保证 to_dict / from_dict 往返一致。

跑法（项目根目录）：
    .venv\\Scripts\\python.exe -m unittest tests.test_models -v
"""

import unittest

from engine.models import (
    CONTRACT_VERSION,
    DayPlan,
    ItineraryItem,
    PlanMetrics,
    PlanRequest,
    PlanResponse,
    POI,
    Transport,
    Violation,
)


class TestPlanRequest(unittest.TestCase):
    def test_empty_request_is_valid(self):
        """空请求必须能构造 —— 这是'非法输入不崩'的前提。"""
        r = PlanRequest()
        self.assertEqual(r.days, 1)
        self.assertIsNone(r.daily_budget)
        self.assertEqual(r.interests, [])

    def test_from_dict_ignores_unknown_fields(self):
        r = PlanRequest.from_dict({"days": 2, "some_new_field": 123})
        self.assertEqual(r.days, 2)

    def test_roundtrip(self):
        r = PlanRequest(city="X", days=2, interests=["a", "b"], daily_budget=88.0)
        back = PlanRequest.from_dict(r.to_dict())
        self.assertEqual(back, r)


class TestPOI(unittest.TestCase):
    def test_tags_from_pipe_string(self):
        p = POI.from_dict(
            {"poi_id": "P001", "name": "n", "category": "c", "lat": "1.0",
             "lon": "2.0", "tags": "海景|拍照", "cost": "10", "stay_min": "30"}
        )
        self.assertEqual(p.tags, ["海景", "拍照"])
        self.assertIsInstance(p.cost, float)
        self.assertEqual(p.stay_min, 30)

    def test_tags_from_list(self):
        p = POI.from_dict(
            {"poi_id": "P002", "name": "n", "category": "c", "lat": 1.0,
             "lon": 2.0, "tags": ["x"]}
        )
        self.assertEqual(p.tags, ["x"])

    def test_defaults(self):
        p = POI(poi_id="P003", name="n", category="c", lat=0.0, lon=0.0)
        self.assertEqual(p.stay_min, 60)
        self.assertEqual(p.score, 0.5)


class TestDayPlan(unittest.TestCase):
    def _day(self):
        d = DayPlan(day_index=1)
        d.items = [
            ItineraryItem(seq=1, poi_id="P1", name="a", stay_min=60, cost=10.0,
                          transport=Transport(mode="start", minutes=0, distance_km=0, cost=0)),
            ItineraryItem(seq=2, poi_id="P2", name="b", stay_min=30, cost=5.0,
                          transport=Transport(mode="walk", minutes=15, distance_km=1.0, cost=2.0)),
        ]
        return d

    def test_refresh_stats(self):
        d = self._day()
        self.assertEqual(d.total_cost, 0.0)      # 刷新前是 0
        d.refresh_stats()
        self.assertEqual(d.total_cost, 17.0)     # 10 + 5 + 0 + 2
        self.assertEqual(d.total_stay_min, 90)
        self.assertEqual(d.total_travel_min, 15.0)
        self.assertEqual(d.total_distance_km, 1.0)

    def test_roundtrip(self):
        d = self._day()
        d.refresh_stats()
        back = DayPlan.from_dict(d.to_dict())
        self.assertEqual(len(back.items), 2)
        self.assertIsInstance(back.items[1].transport, Transport)
        self.assertEqual(back.items[1].transport.mode, "walk")


class TestPlanResponse(unittest.TestCase):
    def _resp(self):
        d = DayPlan(day_index=1)
        d.items = [ItineraryItem(seq=1, poi_id="P1", name="a")]
        d.refresh_stats()
        return PlanResponse(
            request=PlanRequest(days=1),
            days=[d],
            warnings=["NO_CANDIDATE: 无匹配结果，已放宽条件"],
            metrics=PlanMetrics(satisfaction=0.7, feasibility=1.0),
            strategy="stub",
            scenario="demo",
            generated_at="2026-10-27T00:00:00",
        )

    def test_json_roundtrip(self):
        r = self._resp()
        text = r.to_json()
        self.assertIn("P1", text)
        back = PlanResponse.from_json(text)
        self.assertEqual(back.strategy, "stub")
        self.assertEqual(back.days[0].items[0].poi_id, "P1")
        self.assertEqual(back.warnings, r.warnings)
        self.assertEqual(back.metrics.satisfaction, 0.7)

    def test_roundtrip_from_dict(self):
        r = self._resp()
        back = PlanResponse.from_dict(r.to_dict())
        self.assertEqual(back, r)


class TestMetrics(unittest.TestCase):
    def test_overall_without_weights(self):
        m = PlanMetrics(satisfaction=1.0, cost=1.0, time=1.0, distance=1.0, feasibility=1.0)
        self.assertAlmostEqual(m.overall(), 1.0, places=3)

    def test_feasibility_penalty(self):
        m = PlanMetrics(satisfaction=1.0, cost=1.0, time=1.0, distance=1.0, feasibility=0.5)
        self.assertAlmostEqual(m.overall(), 0.5, places=3)


class TestViolation(unittest.TestCase):
    def test_str_format(self):
        v = Violation(code="BUDGET_OVER", message="超预算", day_index=1)
        self.assertEqual(str(v), "[Day1] BUDGET_OVER: 超预算")

    def test_str_without_day(self):
        v = Violation(code="NO_CANDIDATE", message="无候选")
        self.assertEqual(str(v), "NO_CANDIDATE: 无候选")


class TestContract(unittest.TestCase):
    def test_version_pinned(self):
        self.assertEqual(CONTRACT_VERSION, "v1")


if __name__ == "__main__":
    unittest.main()
