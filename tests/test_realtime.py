# @author: 甲
# @reviewer: 乙
# @since: 2026-10-28
"""实时数据与动态重排测试：**全程不联网**。

用手工构造的快照验行为 —— 这也正是"实时数据做成值对象"的好处：
不需要 mock 网络，也能把重排逻辑测透。

跑法：
    .venv\\Scripts\\python.exe -m unittest tests.test_realtime -v
"""

import unittest
from datetime import date, timedelta

from engine.config import load_scenario
from engine.context import (
    ClosureFact,
    PlanContext,
    RealtimeSnapshot,
    WeatherFact,
)
from engine.models import PlanRequest
from engine.planner import plan
from engine.realtime import closures_from_weekdays, empty_snapshot
from engine.replan import apply_snapshot, needs_replan, summary


class _P:
    """够用的 POI 替身：只需要 poi_id / tags / closed_weekdays。"""

    def __init__(self, poi_id, tags=(), closed=()):
        self.poi_id = poi_id
        self.tags = set(tags)
        self.closed_weekdays = list(closed)


class TestSnapshot(unittest.TestCase):
    def test_empty(self):
        s = empty_snapshot("没抓")
        self.assertTrue(s.is_empty())
        self.assertIn("没抓", s.warnings)

    def test_roundtrip(self):
        s = RealtimeSnapshot(city="dalian",
                             weather=(WeatherFact(date="2026-11-01", condition="rain"),),
                             closures=(ClosureFact(poi_id="DL001"),))
        back = RealtimeSnapshot.from_dict(s.to_dict())
        self.assertEqual(back.city, "dalian")
        self.assertEqual(back.weather[0].condition, "rain")
        self.assertEqual(back.closure_ids(), {"DL001"})

    def test_bad_weather_flag(self):
        self.assertTrue(WeatherFact(condition="rain").is_bad)
        self.assertTrue(WeatherFact(condition="storm").is_bad)
        self.assertFalse(WeatherFact(condition="clear").is_bad)


class TestClosureFromWeekdays(unittest.TestCase):
    def test_monday_closed(self):
        # 2026-11-02 是周一
        monday = date(2026, 11, 2)
        self.assertEqual(monday.weekday(), 0)
        pois = [_P("A", closed=[0]), _P("B")]
        got = closures_from_weekdays(pois, [monday.isoformat()])
        self.assertEqual([c.poi_id for c in got], ["A"])

    def test_other_day_open(self):
        tuesday = date(2026, 11, 3)
        pois = [_P("A", closed=[0])]
        self.assertEqual(len(closures_from_weekdays(pois, [tuesday.isoformat()])), 0)


class TestNeedsReplan(unittest.TestCase):
    def setUp(self):
        self.cfg = load_scenario("demo")
        self.resp = plan(PlanRequest(top_n=3), scenario="demo", strategy="stub")

    def _dates(self, n=1):
        d = date.today()
        return [(d + timedelta(days=i)).isoformat() for i in range(n)]

    def test_empty_snapshot_needs_nothing(self):
        self.assertEqual(needs_replan(self.resp, empty_snapshot()), [])

    def test_closed_poi_needs_replan(self):
        ds = self._dates()[0]
        pid = self.resp.days[0].items[0].poi_id
        snap = RealtimeSnapshot(closures=(ClosureFact(poi_id=pid, date=ds),))
        reasons = needs_replan(self.resp, snap)
        self.assertTrue(any("不可用" in r for r in reasons))


class TestApplySnapshot(unittest.TestCase):
    def setUp(self):
        self.cfg = load_scenario("demo")

    def test_offline_unchanged(self):
        """ctx=None 时结果必须与接实时数据之前完全一致。"""
        a = plan(PlanRequest(top_n=3), scenario="demo", strategy="stub")
        b = plan(PlanRequest(top_n=3), scenario="demo", strategy="stub",
                 ctx=PlanContext(snapshot=None))
        self.assertEqual(a.to_dict(), b.to_dict())

    def test_weather_written_into_day(self):
        ds = date.today().isoformat()
        snap = RealtimeSnapshot(
            weather=(WeatherFact(date=ds, condition="clear", temp_c=18.0,
                                 source="open-meteo"),))
        r = plan(PlanRequest(top_n=2), scenario="demo", strategy="stub",
                 ctx=PlanContext(snapshot=snap))
        self.assertIn("晴", r.days[0].weather)
        self.assertTrue(r.realtime)

    def test_closed_poi_is_flagged(self):
        """闭馆的点要么被打标记，要么在重排后被换掉 —— 绝不能悄悄留在行程里。"""
        ds = date.today().isoformat()
        first = plan(PlanRequest(top_n=2), scenario="demo", strategy="stub")
        pid = first.days[0].items[0].poi_id
        snap = RealtimeSnapshot(closures=(ClosureFact(poi_id=pid, date=ds),))
        r = plan(PlanRequest(top_n=2), scenario="demo", strategy="stub",
                 ctx=PlanContext(snapshot=snap))
        still_there = any(it.poi_id == pid for d in r.days for it in d.items)
        flagged = any("closed_today" in it.flags for d in r.days for it in d.items)
        self.assertTrue((not still_there) or flagged,
                        "闭馆的点既没被换掉也没被打标记")

    def test_replan_records_reason(self):
        ds = date.today().isoformat()
        first = plan(PlanRequest(top_n=2), scenario="demo", strategy="stub")
        pid = first.days[0].items[0].poi_id
        snap = RealtimeSnapshot(closures=(ClosureFact(poi_id=pid, date=ds),))
        r = plan(PlanRequest(top_n=2), scenario="demo", strategy="stub",
                 ctx=PlanContext(snapshot=snap))
        self.assertIn("replan_reasons", r.meta)

    def test_summary_is_human_readable(self):
        ds = date.today().isoformat()
        snap = RealtimeSnapshot(weather=(WeatherFact(date=ds, condition="rain"),),
                                closures=(ClosureFact(poi_id="A"),))
        self.assertIn("雨", summary(snap))
        self.assertIn("闭馆", summary(snap))
        self.assertEqual(summary(empty_snapshot()), "无实时数据")

    def test_apply_on_empty_snapshot_is_identity(self):
        r = plan(PlanRequest(top_n=2), scenario="demo", strategy="stub")
        out = apply_snapshot(r, empty_snapshot())
        self.assertIs(out, r)


if __name__ == "__main__":
    unittest.main()
