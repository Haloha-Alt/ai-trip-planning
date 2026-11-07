# @author: 助手
# @since: 2026-11-07
"""v1.1(B2) 时间约束严格化的离线测试。

验收口径（docs/07 B2）：早于开门时间到达 → 出现"等待段"（wait_min>0），
且不报 CLOSED；等待分钟数不计入 stay_min。
全程离线，跑法：python -m unittest tests.test_v11_b2 -v
"""
import unittest

from engine.config import load_scenario
from engine.models import PlanRequest
from engine.planner import plan


class TestWaitOpenField(unittest.TestCase):
    """wait_min 字段：等开门单独计时。"""

    def test_early_start_yields_wait_not_closed(self):
        """06:20 出发、锚点钉 07:35 伯川图书馆（07:30 开门）→ wait_min>0，无 CLOSED。"""
        resp = plan(PlanRequest(city="dalian", start_time="06:20",
                                end_time="10:00", top_n=3,
                                anchors=[{"poi": "DL003", "time": "07:35",
                                          "duration_min": 60}]),
                    scenario="daily", strategy="greedy")
        items = [it for d in resp.days for it in d.items]
        self.assertTrue(items, "早出发也应产出行程")
        for it in items:
            self.assertNotIn("closed_today", it.flags or [],
                             "%s 不应标 CLOSED（等开门兜住了）" % it.name)
        waits = [it for it in items if (it.wait_min or 0) > 0]
        self.assertTrue(waits, "锚点 07:35 开门场景应出现等待段：%s"
                        % [(i.name, i.arrive) for i in items])
        for it in waits:
            self.assertLess(int(it.wait_min), 90,
                            "等待超过 90 分钟说明排程不合理：%s" % it.name)
            self.assertIn("wait_open", it.flags or [])

    def test_wait_not_counted_in_stay(self):
        """wait_min 不计入 stay_min：arrive+stay+wait 的关系要自洽。"""
        resp = plan(PlanRequest(city="dalian", start_time="06:20",
                                end_time="10:00", top_n=3),
                    scenario="daily", strategy="greedy")
        for d in resp.days:
            for it in d.items:
                if it.wait_min:
                    from engine.strategies import to_minutes
                    travel = int(round(it.transport.minutes))
                    arrive = to_minutes(it.arrive)
                    leave = to_minutes(it.leave)
                    # arrive = 出发时刻+在途+wait；leave = arrive + stay
                    self.assertEqual(leave - arrive, it.stay_min,
                                     "停留时长不应含等待")

    def test_normal_start_no_wait(self):
        """正常 09:00 出发（POI 都已开门）→ 全程 wait_min==0。"""
        resp = plan(PlanRequest(city="dalian", start_time="09:00",
                                end_time="18:00", top_n=3),
                    scenario="daily", strategy="greedy")
        for d in resp.days:
            for it in d.items:
                self.assertEqual(it.wait_min, 0,
                                 "%s 不该等开门（09:00 出发）" % it.name)


if __name__ == "__main__":
    unittest.main()
