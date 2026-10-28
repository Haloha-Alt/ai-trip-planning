# @author: 甲
# @reviewer: 乙
# @since: 2026-10-28
"""真算法（greedy / two_opt）的测试。

这里**不测"分数好不好看"**（那由 eval/results.md 说话），只测三条硬性质：

1. **守约束**：不超时间窗、不超每日预算、途经点顺序不乱；
2. **比基线强**：同一份请求上，贪心的总分必须高于随机基线；
3. **可复现**：同样的输入跑两次，结果完全一致（没有隐藏的随机性）。

跑法：
    .venv\\Scripts\\python.exe -m unittest tests.test_strategies -v
"""

import unittest

from engine.geo import to_minutes
from engine.models import PlanRequest
from engine.planner import plan
from engine.strategies import STRATEGIES, _path_minutes, _two_opt, available

CITY = "dalian"
SCENARIO = "daily"


def _req(**kw) -> PlanRequest:
    base = {
        "city": CITY, "days": 1, "top_n": 4,
        "start_time": "07:30", "end_time": "20:00",
        "daily_budget": 60,
        "interests": ["图书馆", "吃饭"],
        "pace": "normal",
    }
    base.update(kw)
    return PlanRequest.from_dict(base)


def _poi_ids() -> list[str]:
    """真实数据里的 POI 主键。

    刻意不硬编码 `P00x`：大连的 id 是 `DLxxx`，换城市又换一套，
    硬编码会让"换数据"变成"测试全红"（`docs/04` AC-20 踩过的坑）。
    """
    from data.loader import load_pois
    from engine.config import load_scenario

    return [p.poi_id for p in load_pois(load_scenario(SCENARIO), CITY)]


class TestRegistration(unittest.TestCase):
    def test_both_registered(self):
        self.assertIn("greedy", STRATEGIES)
        self.assertIn("two_opt", STRATEGIES)
        self.assertIn("greedy", available())

    def test_callable_through_plan(self):
        for name in ("greedy", "two_opt"):
            resp = plan(_req(), scenario=SCENARIO, strategy=name)
            self.assertEqual(resp.strategy, name)
            self.assertTrue(resp.days)


class TestConstraints(unittest.TestCase):
    def test_respects_time_window(self):
        req = _req(days=2)
        for name in ("greedy", "two_opt"):
            resp = plan(req, scenario=SCENARIO, strategy=name)
            for d in resp.days:
                for it in d.items:
                    self.assertLessEqual(to_minutes(it.leave), to_minutes(req.end_time),
                                         "%s 第%d天 %s 超出时间窗" % (name, d.day_index, it.name))

    def test_respects_daily_budget(self):
        req = _req(days=2, daily_budget=30)
        for name in ("greedy", "two_opt"):
            resp = plan(req, scenario=SCENARIO, strategy=name)
            for d in resp.days:
                self.assertLessEqual(d.total_cost, 30 + 1e-9,
                                     "%s 第%d天超预算：%.2f" % (name, d.day_index, d.total_cost))

    def test_start_poi_first(self):
        """起点是指定的硬要求：必须排在第一天第一站。"""
        ids = _poi_ids()
        resp = plan(_req(start_poi_id=ids[1]), scenario=SCENARIO, strategy="greedy")
        first = resp.days[0].items[0]
        self.assertEqual(first.poi_id, ids[1])

    def test_waypoints_keep_order(self):
        """途经点按用户给的先后强制顺序。"""
        ids = _poi_ids()
        req = _req(waypoints=[ids[2], ids[0]], top_n=5)
        resp = plan(req, scenario=SCENARIO, strategy="greedy")
        got = [it.poi_id for it in resp.days[0].items]
        self.assertIn(ids[2], got)
        self.assertIn(ids[0], got)
        self.assertLess(got.index(ids[2]), got.index(ids[0]))

    def test_no_duplicate_stops(self):
        resp = plan(_req(days=2, top_n=5), scenario=SCENARIO, strategy="greedy")
        ids = [it.poi_id for d in resp.days for it in d.items]
        self.assertEqual(len(ids), len(set(ids)))


class TestBeatsBaseline(unittest.TestCase):
    def test_greedy_beats_random(self):
        """贪心必须比"故意做笨"的基线强 —— 否则这个算法没有存在的意义。"""
        req = _req(days=1, daily_budget=60)
        g = plan(req, scenario=SCENARIO, strategy="greedy")
        r = plan(req, scenario=SCENARIO, strategy="random")
        self.assertGreater(g.metrics.overall(g.meta.get("weights")),
                           r.metrics.overall(r.meta.get("weights")))

    def test_greedy_within_budget_not_worse_than_stub(self):
        """stub 不看预算，贪心看 —— 有硬预算时贪心不该更差。"""
        req = _req(days=1, daily_budget=30)
        g = plan(req, scenario=SCENARIO, strategy="greedy")
        s = plan(req, scenario=SCENARIO, strategy="stub")
        self.assertLessEqual(g.days[0].total_cost, s.days[0].total_cost + 1e-9)

    def test_two_opt_not_worse_than_greedy_on_travel(self):
        """2-opt 只管一件事：把在途时间压下去。它压不下去就是白做。"""
        req = _req(days=1, top_n=5)
        g = plan(req, scenario=SCENARIO, strategy="greedy")
        t = plan(req, scenario=SCENARIO, strategy="two_opt")
        self.assertLessEqual(round(t.days[0].total_travel_min, 1),
                             round(g.days[0].total_travel_min, 1) + 0.05)


class TestOpenHours(unittest.TestCase):
    """A 修复：早出发要"等开门"，而不是把还没开门的点排进去再报 CLOSED。"""

    def _open_map(self):
        from data.loader import load_pois
        from engine.config import load_scenario

        return {p.poi_id: p.open_time for p in load_pois(load_scenario(SCENARIO), CITY)}

    def test_arrive_never_before_opening(self):
        open_of = self._open_map()
        for name in ("stub", "greedy"):
            resp = plan(_req(start_time="06:00"), scenario=SCENARIO, strategy=name)
            for d in resp.days:
                for it in d.items:
                    self.assertGreaterEqual(
                        to_minutes(it.arrive), to_minutes(open_of[it.poi_id]),
                        "%s 把 %s 排在开门前（%s）" % (name, it.name, it.arrive))

    def test_no_closed_warning_when_leaving_early(self):
        for name in ("stub", "greedy"):
            resp = plan(_req(start_time="06:00"), scenario=SCENARIO, strategy=name)
            self.assertFalse([w for w in resp.warnings if "CLOSED" in w],
                             "%s 仍在报闭馆：%s" % (name, resp.warnings))

    def test_wait_is_flagged_and_explained(self):
        resp = plan(_req(start_time="06:00"), scenario=SCENARIO, strategy="stub")
        waited = [it for d in resp.days for it in d.items if "wait_open" in it.flags]
        self.assertTrue(waited, "早出发却没有一条 wait_open 标记")
        self.assertIn("等开门", waited[0].reason)

    def test_arrive_at_helper(self):
        from data.loader import load_pois
        from engine.config import load_scenario
        from engine.models import Transport
        from engine.strategies import _arrive_at

        cfg = load_scenario(SCENARIO)
        poi = next(p for p in load_pois(cfg, CITY)
                   if to_minutes(p.open_time) > to_minutes("07:00"))
        edge = Transport(mode="walk", minutes=1.0, distance_km=0.1, cost=0.0)
        self.assertEqual(_arrive_at(to_minutes("06:00"), poi, edge), to_minutes(poi.open_time))
        # 到得比开门晚时，不该被"提前"到开门时刻
        late = to_minutes(poi.open_time) + 60
        self.assertEqual(_arrive_at(late, poi, edge), late + 1)


class TestTwoOpt(unittest.TestCase):
    def test_two_opt_never_makes_path_longer(self):
        from data.loader import load_pois
        from engine.config import load_scenario

        cfg = load_scenario(SCENARIO)
        pois = load_pois(cfg, CITY)[:5]
        req = _req()
        before = _path_minutes(pois, req, cfg)
        after = _path_minutes(_two_opt(pois, req, cfg), req, cfg)
        self.assertLessEqual(after, before + 1e-9)

    def test_short_path_passthrough(self):
        from data.loader import load_pois
        from engine.config import load_scenario

        cfg = load_scenario(SCENARIO)
        pois = load_pois(cfg, CITY)[:2]
        self.assertEqual(_two_opt(pois, _req(), cfg), pois)


class TestDeterminism(unittest.TestCase):
    def test_same_input_same_output(self):
        for name in ("greedy", "two_opt"):
            a = plan(_req(days=2), scenario=SCENARIO, strategy=name)
            b = plan(_req(days=2), scenario=SCENARIO, strategy=name)
            self.assertEqual(
                [(it.name, it.arrive) for d in a.days for it in d.items],
                [(it.name, it.arrive) for d in b.days for it in d.items])


class TestDegenerate(unittest.TestCase):
    def test_empty_request_does_not_crash(self):
        resp = plan(PlanRequest(), scenario=SCENARIO, strategy="greedy")
        self.assertTrue(resp.days)

    def test_tight_window_yields_empty_day_not_exception(self):
        resp = plan(_req(start_time="17:00", end_time="17:10"),
                    scenario=SCENARIO, strategy="greedy")
        self.assertEqual(resp.days[0].items, [])

    def test_impossible_budget_yields_empty_day(self):
        resp = plan(_req(daily_budget=1), scenario=SCENARIO, strategy="greedy")
        self.assertLessEqual(resp.days[0].total_cost, 1 + 1e-9)


if __name__ == "__main__":
    unittest.main()
