# @author: 甲
# @reviewer: 乙
# @since: 2026-10-30
"""出行规则的测试：关门、闭馆日、必去点、出发地、默认站数。

这五条以前都是"字段存在但引擎不消费"，或者"排进去再报错"。
现在的要求是**排之前就判断**：

1. 到站已经过 `close_time` 的点不排进去（不是排完再报 CLOSED）；
2. `date` 的星期命中 `closed_weekdays` 的点直接不进候选池；
3. `must_visit` 必去点先落位，没排进去要报 MUST_VISIT_MISS；
4. `origin` 认得出具体 POI 时，第 1 天第一段在途从它算起（不再是 0 分钟）；
5. 没说站数时用场景的 `default_top_n`，不是 `max_poi_per_day` 上限。

跑法：
    .venv\\Scripts\\python.exe -m unittest tests.test_daily_rules -v
"""

import unittest

from engine.config import load_scenario
from engine.models import PlanRequest
from engine.planner import plan


def _brief(resp) -> list[str]:
    return [i.name for d in resp.days for i in d.items]


class TestClosingTime(unittest.TestCase):
    """17:00 关门的点，16:50 出发就不该再排（40 分钟停留会跨过关门时刻）。"""

    def test_late_start_skips_closed_poi(self):
        resp = plan(PlanRequest(city="dalian", top_n=4, start_time="16:50",
                                end_time="21:30", daily_budget=100,
                                interests=["看病", "邮政"]),
                    scenario="daily", strategy="stub")
        names = _brief(resp)
        self.assertNotIn("校医院", names, "17:00 关门的校医院被排进了 %s" % names)
        self.assertNotIn("邮政服务点", names)
        closed = [w for w in resp.warnings if "CLOSED" in w]
        self.assertEqual(closed, [], "关门应该跳过，而不是排进去再报 CLOSED：%s" % closed)

    def test_early_start_can_still_visit(self):
        """同样的点，白天出发就能去 —— 说明不是被数据误伤。"""
        resp = plan(PlanRequest(city="dalian", top_n=4, start_time="08:00",
                                end_time="18:00", interests=["看病"]),
                    scenario="daily", strategy="greedy")
        self.assertIn("校医院", _brief(resp))

    def test_closed_share_of_day_is_never_scheduled(self):
        """任何策略、任何时段，都不该出现"离开时间晚于关门时间"的站点。"""
        from data.loader import load_pois

        cfg = load_scenario("daily")
        close_of = {p.name: p.close_time for p in load_pois(cfg, "dalian")}
        for strategy in ("stub", "greedy", "two_opt"):
            resp = plan(PlanRequest(city="dalian", top_n=6, start_time="14:00",
                                    end_time="21:00", daily_budget=100),
                        scenario="daily", strategy=strategy)
            for d in resp.days:
                for it in d.items:
                    self.assertLessEqual(it.leave, close_of.get(it.name, "23:59"),
                                         "%s 在 %s 关门后仍在（%s 离开）"
                                         % (it.name, close_of.get(it.name), it.leave))


class TestClosedWeekday(unittest.TestCase):
    def test_closed_weekday_excluded(self):
        """大连自然博物馆周一闭馆（数据里的 closed_weekdays），周一的行程里不该有它。"""
        from data.loader import load_pois

        cfg = load_scenario("daily")
        closed = [p for p in load_pois(cfg, "dalian") if p.closed_weekdays]
        if not closed:
            self.skipTest("数据里没有配置 closed_weekdays 的点")
        poi = closed[0]
        weekday = poi.closed_weekdays[0]

        # 造一个"那一天就是该星期 X"的日期
        from datetime import date, timedelta

        base = date(2026, 11, 2)                    # 2026-11-02 是周一
        d = base + timedelta(days=weekday)
        resp = plan(PlanRequest(city="dalian", top_n=6, start_time="08:00",
                                end_time="20:00", date=d.isoformat()),
                    scenario="daily", strategy="stub")
        self.assertNotIn(poi.name, _brief(resp), "%s 在闭馆日被排进了行程" % poi.name)


class TestMustVisit(unittest.TestCase):
    def test_must_visit_is_scheduled(self):
        resp = plan(PlanRequest(city="dalian", top_n=3, start_time="08:00",
                                end_time="18:00", must_visit=["校医院"]),
                    scenario="daily", strategy="greedy")
        self.assertIn("校医院", _brief(resp))
        self.assertEqual([w for w in resp.warnings if "MUST_VISIT" in w], [])

    def test_missing_must_visit_warns(self):
        resp = plan(PlanRequest(city="dalian", top_n=3, start_time="08:00",
                                end_time="18:00", must_visit=["不存在的地方"]),
                    scenario="daily", strategy="stub")
        self.assertTrue([w for w in resp.warnings if "MUST_VISIT_MISS" in w],
                        "认不出的必去点必须报出来，不能静默忽略：%s" % resp.warnings)

    def test_must_visit_out_of_interest_is_still_scheduled(self):
        """必去点是硬要求，不因为"没命中兴趣"被过滤掉。"""
        resp = plan(PlanRequest(city="dalian", top_n=3, start_time="08:00",
                                end_time="18:00", interests=["自习"],
                                must_visit=["校医院"]),
                    scenario="daily", strategy="greedy")
        self.assertIn("校医院", _brief(resp))


class TestOrigin(unittest.TestCase):
    def test_origin_becomes_first_leg(self):
        """给了出发地，第 1 天第一段就是"从出发地走过去"，不再是 0 分钟的 start。"""
        resp = plan(PlanRequest(city="dalian", top_n=3, start_time="08:00",
                                end_time="18:00", origin="西山生活区广场"),
                    scenario="daily", strategy="stub")
        first = resp.days[0].items[0].transport
        self.assertNotEqual(first.mode, "start", "出发地没生效，第一段还是 start")
        self.assertGreater(first.minutes, 0.0)

    def test_unknown_origin_falls_back(self):
        """认不出的出发地当没填：不报错、不扣分（与 start_poi_id 同口径）。"""
        resp = plan(PlanRequest(city="dalian", top_n=3, start_time="08:00",
                                end_time="18:00", origin="火星基地"),
                    scenario="daily", strategy="stub")
        self.assertTrue(resp.days[0].items)
        self.assertEqual(resp.days[0].items[0].transport.mode, "start")


class TestDefaultTopN(unittest.TestCase):
    def test_scenario_default_top_n(self):
        cfg = load_scenario("daily")
        self.assertGreater(cfg.default_top_n, 0)
        self.assertLess(cfg.default_top_n, cfg.max_poi_per_day,
                        "默认站数应当小于上限，否则等于没配")

    def test_parse_uses_default_top_n(self):
        from service.usecases import build_request

        cfg = load_scenario("daily")
        req = build_request("上午自习，中午吃饭", scenario="daily")
        self.assertEqual(req.top_n, cfg.default_top_n)


class TestCityMatrix(unittest.TestCase):
    def test_city_without_matrix_estimates_everything(self):
        """北京没有自己的矩阵：不许借用别的城市的占位边，直接报 0 条。"""
        from data.cities import has_matrix
        from data.loader import load_matrix

        if has_matrix("beijing"):
            self.skipTest("北京已经有矩阵了")
        cfg = load_scenario("daily")
        self.assertEqual(load_matrix(cfg, "beijing"), [])

        resp = plan(PlanRequest(city="beijing", top_n=2, start_time="09:00",
                                end_time="17:00"), scenario="daily", strategy="stub")
        self.assertEqual(resp.meta["data"]["matrix_edges"], 0)
        self.assertTrue(resp.days[0].items, "没有矩阵也必须能出行程（全靠估算）")


if __name__ == "__main__":
    unittest.main()
