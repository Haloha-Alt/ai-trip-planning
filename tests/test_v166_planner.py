# @author: 助手
# @since: 2026-11-07
"""v1.6.6：雨天户外末站自动换室内 + 免费站不进待核实清单。

全程离线。跑法：
    python -m unittest tests.test_v166_planner -v
"""

import unittest

from engine.context import PlanContext, RealtimeSnapshot, WeatherFact
from engine.models import PlanRequest
from engine.planner import plan


def _items(resp):
    return [i for d in resp.days for i in d.items]


def _snap(condition: str) -> RealtimeSnapshot:
    return RealtimeSnapshot(
        city="dalian",
        weather=(WeatherFact(date="2026-11-07", condition=condition,
                             temp_c=6.0, source="manual"),),
    )


def _plan(ctx):
    # prefer_indoor_on_rain=False 保留原排程，让换站逻辑（v1.6.6）自己处理户外末站；
    # 数据变动导致末站非户外时由调用方 skipTest。
    ctx = PlanContext(snapshot=ctx.snapshot, prefer_indoor_on_rain=False)
    return plan(PlanRequest(city="dalian", zone="星海", top_n=4,
                            start_time="12:00", end_time="23:00"),
                scenario="date", strategy="date", ctx=ctx)


class TestRainSwap(unittest.TestCase):
    """坏天气：最后一天的户外末站换成 Plan B 首选室内站。"""

    def test_rain_swaps_outdoor_last_stop(self):
        ctx = PlanContext(snapshot=_snap("rain"))
        resp = _plan(ctx)
        items = _items(resp)
        swap = resp.meta.get("rain_swap") or {}
        if not swap:
            if items and items[-1].category == "户外":
                self.fail("末站是户外却没有换站")
            self.skipTest("该随机种子末站不是户外站，无法验证换站")
        # 换站前末站名要留痕，换站目标必须是数据里的室内站
        self.assertEqual(swap.get("reason"), "当天有雨，户外末站自动换室内")
        from data.loader import load_pois
        from engine.config import load_scenario
        cat = {p.name: p.category for p in load_pois(load_scenario("date"), "dalian")}
        to_cat = cat.get(swap.get("to"))
        self.assertIsNotNone(to_cat, "换站目标必须是真实入库 POI")
        self.assertNotIn("户外", to_cat, "换站目标应为室内类目")
        self.assertTrue(any("自动换成室内" in w for w in resp.warnings),
                        "warnings 里应有人话说明：%s" % resp.warnings)
        # 总账应与换站后一致
        day = resp.days[-1]
        self.assertAlmostEqual(
            day.total_cost,
            round(sum(i.cost + i.transport.cost for i in day.items), 2))

    def test_clear_weather_no_swap(self):
        ctx = PlanContext(snapshot=_snap("clear"))
        resp = _plan(ctx)
        self.assertNotIn("rain_swap", resp.meta, "好天气不应换站")


class TestPendingFreePois(unittest.TestCase):
    """免费站（人均 0）不应出现在 meta['pending'] 待核实清单里。"""

    def test_free_pois_excluded_from_pending(self):
        from data.loader import load_pois

        cfg_free = {p.name for p in load_pois(None, "dalian")
                    if (p.cost or 0) <= 0}
        resp = _plan(PlanContext(snapshot=None))
        pending = resp.meta.get("pending") or []
        for line in pending:
            name = line.split("（")[0].strip()
            self.assertNotIn(name, cfg_free,
                             "免费站 %s 不该进待核实清单" % name)


if __name__ == "__main__":
    unittest.main()
