# @author: 助手
# @since: 2026-11-07
"""E3 碎片时间任务（v1.6.7）的离线测试。

验收口径（docs/07 E3）：移动片/等待片装 ears-only/none 微任务，不重复，
缺数据零影响，task_usage ∈ [0,1]；POI 序列不动。
全程离线，跑法：python -m unittest tests.test_tasks -v
"""
import unittest

from engine.config import load_scenario
from engine.models import PlanRequest
from engine.planner import plan
from engine.tasks import attach_tasks


class TestAttachTasks(unittest.TestCase):
    def _plan_dalian(self, top_n=5):
        return plan(PlanRequest(city="dalian", start_time="14:00",
                                end_time="23:00", top_n=top_n),
                    scenario="date", strategy="date")

    def test_dalian_has_task_data_and_planner_fills(self):
        """dalian 有 tasks.csv；date 行程里应出现装填好的任务。"""
        from data.loader import load_tasks
        rows = load_tasks(load_scenario("date"), "dalian")
        self.assertGreaterEqual(len(rows), 3)
        resp = self._plan_dalian()
        filled = [(it, t) for d in resp.days for it in d.items for t in it.tasks]
        if not any(5 <= int(round(i.transport.minutes or 0)) <= 20
                   for d in resp.days for i in d.items):
            self.skipTest("该随机种子没有 5-20 分钟的移动片")
        self.assertTrue(filled, "有移动片就该装上任务")
        usage = resp.meta.get("task_usage") or {}
        self.assertTrue(0.0 <= usage.get("usage", -1) <= 1.0)

    def test_rules(self):
        """装填规则：移动片只装 ears-only；等待片可装 none；不超时长；不重复。"""
        from engine.models import PlanResponse, DayPlan, ItineraryItem, Transport
        resp = PlanResponse(
            request=PlanRequest(city="dalian"),
            scenario="date", strategy="date",
            days=[DayPlan(day_index=1, items=[
                ItineraryItem(seq=1, poi_id="A", name="甲",
                              transport=Transport(mode="walk", minutes=15.0)),
                ItineraryItem(seq=2, poi_id="B", name="乙",
                              transport=Transport(mode="walk", minutes=3.0)),
            ])])
        rows = [
            {"task_id": "T1", "name": "听播客", "duration_min": "20", "consumes": "ears-only"},
            {"task_id": "T2", "name": "回消息", "duration_min": "10", "consumes": "none"},
            {"task_id": "T3", "name": "太短", "duration_min": "3", "consumes": "ears-only"},
            {"task_id": "T4", "name": "听单词音频", "duration_min": "10", "consumes": "ears-only"},
            {"task_id": "T5", "name": "要动手", "duration_min": "10", "consumes": "attention"},
        ]
        usage = attach_tasks(resp, rows)
        items = resp.days[0].items
        self.assertEqual(len(items[0].tasks), 1, "15 分钟移动片装 1 个 ears-only 短任务")
        # 15 分钟移动片装不下 20 分钟的 T1，选 10 分钟的 ears-only T4；
        # none（T2）/attention（T5）都不能进移动片
        self.assertEqual(items[0].tasks[0]["task_id"], "T4")
        self.assertEqual(items[1].tasks, [], "3 分钟在途太短，不装")
        self.assertEqual(usage["filled_min"], sum(
            t["duration_min"] for it in items for t in it.tasks))
        self.assertEqual(resp.days[0].task_min, usage["filled_min"])

    def test_no_duplicate_tasks(self):
        """多个移动片不重复装同一个任务。"""
        from engine.models import PlanResponse, DayPlan, ItineraryItem, Transport
        resp = PlanResponse(
            request=PlanRequest(city="dalian"),
            scenario="date", strategy="date",
            days=[DayPlan(day_index=1, items=[
                ItineraryItem(seq=i + 1, poi_id=str(i), name="站%d" % i,
                              transport=Transport(mode="walk", minutes=15.0))
                for i in range(5)])])
        rows = [{"task_id": "T1", "name": "听播客", "duration_min": "15",
                 "consumes": "ears-only"}]
        attach_tasks(resp, rows)
        ids = [t["task_id"] for it in resp.days[0].items for t in it.tasks]
        self.assertEqual(len(ids), len(set(ids)), "任务全天最多用一次")

    def test_missing_city_zero_impact(self):
        """北京没有 tasks.csv → 行为与从前一致，meta 无 task_usage。"""
        from data.loader import load_tasks
        self.assertEqual(load_tasks(load_scenario("date"), "beijing"), [])
        resp = plan(PlanRequest(city="beijing", start_time="10:00",
                                end_time="18:00", top_n=3),
                    scenario="daily", strategy="greedy")
        self.assertNotIn("task_usage", resp.meta)
        for d in resp.days:
            for it in d.items:
                self.assertEqual(it.tasks, [])


if __name__ == "__main__":
    unittest.main()
