# @author: 甲
# @reviewer: 乙
# @since: 2026-10-28
"""多算法对比的测试：**不启服务器**，直接验用例与路由。

验四件事：
1. 表本身的形状对（行数 = 策略数、按总分降序、恰好一行 is_best）
2. 相对基线的话术对（方向不能搞反：更便宜/更少在途 = 更好）
3. 错误输入要 400 而不是 500（未知算法名是最常见的现场事故）
4. 结果可复现（同一请求跑两次，数字必须一模一样）

跑法：
    .venv\\Scripts\\python.exe -m unittest tests.test_compare -v
"""

import unittest

from api.routes import dispatch
from engine.models import PlanRequest
from engine.strategies import available
from service.compare import DEFAULT_BASELINE, _pct, _win_count, compare_plans


def _req(**kw) -> PlanRequest:
    base = {"city": "dalian", "top_n": 3, "days": 1}
    base.update(kw)
    return PlanRequest.from_dict(base)


class TestCompareShape(unittest.TestCase):
    def setUp(self):
        self.out = compare_plans(_req(), scenario="daily")

    def test_row_per_strategy(self):
        self.assertEqual(len(self.out["rows"]), len(available()))

    def test_sorted_by_overall_desc(self):
        vals = [r["overall"] for r in self.out["rows"]]
        self.assertEqual(vals, sorted(vals, reverse=True))

    def test_exactly_one_best(self):
        self.assertEqual(sum(1 for r in self.out["rows"] if r["is_best"]), 1)
        self.assertEqual(self.out["best"], self.out["rows"][0]["strategy"])

    def test_baseline_row_has_no_delta(self):
        base = next(r for r in self.out["rows"] if r["strategy"] == DEFAULT_BASELINE)
        self.assertEqual(base["vs_baseline"], {})

    def test_other_rows_carry_delta(self):
        for r in self.out["rows"]:
            if r["strategy"] == DEFAULT_BASELINE:
                continue
            self.assertIn("overall_pct", r["vs_baseline"])
            self.assertIn("wins", r["vs_baseline"])

    def test_summary_is_sentence(self):
        self.assertTrue(self.out["summary"])
        self.assertIn("总分", self.out["summary"])

    def test_request_echoed(self):
        self.assertEqual(self.out["request"]["city"], "dalian")


class TestCompareInput(unittest.TestCase):
    def test_subset_of_strategies(self):
        out = compare_plans(_req(), scenario="daily", strategies=["stub"])
        self.assertEqual([r["strategy"] for r in out["rows"]], ["stub"])
        # 基线不在本次对比里 → 不编造"提升"
        self.assertEqual(out["baseline"], "")
        self.assertEqual(out["rows"][0]["vs_baseline"], {})

    def test_duplicate_names_collapsed(self):
        out = compare_plans(_req(), scenario="daily", strategies=["stub", "stub"])
        self.assertEqual(len(out["rows"]), 1)

    def test_unknown_strategy_raises(self):
        with self.assertRaises(KeyError):
            compare_plans(_req(), scenario="daily", strategies=["not_exist"])

    def test_empty_list_raises(self):
        with self.assertRaises(KeyError):
            compare_plans(_req(), scenario="daily", strategies=[])

    def test_deterministic(self):
        a = compare_plans(_req(), scenario="daily")
        b = compare_plans(_req(), scenario="daily")
        self.assertEqual([(r["strategy"], r["overall"], r["cost"]) for r in a["rows"]],
                         [(r["strategy"], r["overall"], r["cost"]) for r in b["rows"]])


class TestHelpers(unittest.TestCase):
    def test_pct(self):
        self.assertEqual(_pct(110, 100), 10.0)
        self.assertEqual(_pct(90, 100), -10.0)
        self.assertIsNone(_pct(1, 0))          # 基数为 0 不返回 inf

    def test_win_direction(self):
        """更便宜、在途更少算赢；更贵更慢算输 —— 方向搞反会让结论完全相反。"""
        base = {"satisfaction": 0.5, "cost_score": 0.5, "time_score": 0.5,
                "distance_score": 0.5, "cost": 100.0, "travel_min": 60.0, "warnings": 2}
        better = {"satisfaction": 0.6, "cost_score": 0.6, "time_score": 0.6,
                  "distance_score": 0.6, "cost": 80.0, "travel_min": 40.0, "warnings": 0}
        worse = {"satisfaction": 0.4, "cost_score": 0.4, "time_score": 0.4,
                 "distance_score": 0.4, "cost": 120.0, "travel_min": 80.0, "warnings": 3}
        self.assertEqual(_win_count(better, base), 7)
        self.assertEqual(_win_count(worse, base), 0)


class TestCompareRoute(unittest.TestCase):
    def test_compare_ok(self):
        status, body = dispatch("POST", "/api/compare", {}, {
            "query": "去图书馆自习", "scenario": "daily",
            "overrides": {"city": "dalian", "top_n": 2},
        })
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertGreaterEqual(len(body["rows"]), 1)
        self.assertIn("best", body)
        self.assertEqual(body["rows"][0]["is_best"], True)

    def test_compare_explicit_strategies(self):
        status, body = dispatch("POST", "/api/compare", {}, {
            "query": "随便走走", "scenario": "daily",
            "strategies": ["stub", "random"], "baseline": "random",
            "overrides": {"city": "dalian", "top_n": 2},
        })
        self.assertEqual(status, 200)
        self.assertEqual({r["strategy"] for r in body["rows"]}, {"stub", "random"})
        self.assertEqual(body["baseline"], "random")

    def test_compare_unknown_strategy_is_400(self):
        status, body = dispatch("POST", "/api/compare", {}, {
            "query": "随便走走", "strategies": ["not_exist"],
        })
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertIn("未知策略", body["error"])

    def test_compare_realtime_off_by_default(self):
        status, body = dispatch("POST", "/api/compare", {}, {
            "query": "去食堂", "overrides": {"city": "dalian", "top_n": 2},
        })
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])


if __name__ == "__main__":
    unittest.main()
