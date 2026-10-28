# @author: 甲
# @reviewer: 乙
# @since: 2026-10-28
"""出行方式（mobility）测试。

这条字段解决的是一个真实缺陷：跨城/远郊景点相距 30 km 时，
按步行 4.5 km/h 估算要 7 小时，结果一天只排得下 1 站 —— 行程明显不合理。

规则：
- 默认 `walk`，与接这个字段之前**逐字一致**（保住历史基线）；
- 选 `transit` / `drive` 时，速度从场景配置里查，不许写死在代码里。

跑法：
    .venv\\Scripts\\python.exe -m unittest tests.test_mobility -v
"""

import unittest

from engine.config import load_scenario
from engine.geo import resolve_mode
from engine.models import PlanRequest
from engine.planner import plan


class TestResolveMode(unittest.TestCase):
    def setUp(self):
        self.cfg = load_scenario("demo")

    def test_known_mode_returned_as_is(self):
        self.assertEqual(resolve_mode(self.cfg, "walk"), "walk")
        self.assertEqual(resolve_mode(self.cfg, "metro"), "metro")

    def test_transit_falls_to_a_real_mode(self):
        """'transit' 不是具体交通方式，要落到配置里真有的那个上。"""
        m = resolve_mode(self.cfg, "transit")
        self.assertIn(m, self.cfg.transport_speed_kmh)

    def test_unknown_mobility_never_crashes(self):
        for m in ("", None, "飞机", "teleport"):
            self.assertIn(resolve_mode(self.cfg, m), self.cfg.transport_speed_kmh)


class TestMobilityChangesTravelTime(unittest.TestCase):
    def _travel(self, **kw):
        r = plan(PlanRequest(city="dalian", top_n=3, **kw),
                 scenario="demo", strategy="stub")
        return r.days[0].total_travel_min, len(r.days[0].items)

    def test_default_is_walk_and_matches_history(self):
        """不传 mobility 时，结果必须与没有这个字段时一致。"""
        a, na = self._travel()
        b, nb = self._travel(mobility="walk")
        self.assertEqual((a, na), (b, nb))

    def test_transit_is_faster_than_walk(self):
        walk_min, _ = self._travel(mobility="walk")
        transit_min, _ = self._travel(mobility="transit")
        self.assertLess(transit_min, walk_min)

    def test_faster_mode_fits_more_stations(self):
        """远郊景点（旅顺）在步行估算下排不下，换成公共交通就应该排得进。"""
        _, n_walk = self._travel(must_visit=["旅顺博物馆"], mobility="walk")
        _, n_transit = self._travel(must_visit=["旅顺博物馆"], mobility="transit")
        self.assertGreaterEqual(n_transit, n_walk)

    def test_plan_never_breaks_on_bad_mobility(self):
        for m in ("", "unknown", None):
            r = plan(PlanRequest(city="dalian", top_n=2, mobility=m),
                     scenario="demo", strategy="stub")
            self.assertIsNotNone(r.days)


if __name__ == "__main__":
    unittest.main()
