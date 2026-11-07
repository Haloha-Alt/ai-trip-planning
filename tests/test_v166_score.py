# @author: 助手
# @since: 2026-11-07
"""v1.6.6 评分：date 场景新增「原话贴合」（anchor_fit）维度。

覆盖三件事：
1. 带可命中锚点的 date 行程，meta 里有 anchor_fit=1.0，且满意度因贴合被抬升；
2. 同请求去掉锚点不崩（无 anchor_fit 留痕，评分照常）；
3. daily 场景不引入 anchor_fit（meta 无留痕，指标结构不变）。

全程离线。跑法：
    python -m unittest tests.test_v166_score -v
"""

import unittest

from engine.models import PlanRequest
from engine.planner import plan


def _items(resp):
    return [i for d in resp.days for i in d.items]


class TestAnchorFit(unittest.TestCase):
    """date 场景：锚点贴合要体现在评分里。"""

    def test_anchor_fit_full_hit_recorded(self):
        """两个锚点都命中（poi 排入 + arrive 精确等时刻）→ anchor_fit=1.0。"""
        req = PlanRequest(city="dalian", zone="星海", start_time="09:30",
                          end_time="22:00", top_n=6,
                          anchors=[{"poi": "星海CGV影城", "time": "14:00",
                                    "duration_min": 120, "label": "电影"},
                                   {"poi": "星海渔港(星海广场店)", "time": "18:00",
                                    "duration_min": 90, "label": "晚餐"}])
        resp = plan(req, scenario="date", strategy="date")
        self.assertIn("anchor_fit", resp.meta, "锚点贴合必须留痕：%s" % resp.meta)
        self.assertEqual(resp.meta["anchor_fit"], 1.0,
                         "两个锚点都应精确命中：%s"
                         % [(i.name, i.arrive) for i in _items(resp)])

    def test_anchor_fit_raises_satisfaction(self):
        """同请求无锚点 vs 有锚点且全命中：有锚点的满意度不应更低。"""
        base = dict(city="dalian", zone="星海", start_time="09:30",
                    end_time="22:00", top_n=6)
        with_anchor = plan(PlanRequest(anchors=[
            {"poi": "星海CGV影城", "time": "14:00", "duration_min": 120},
            {"poi": "星海渔港(星海广场店)", "time": "18:00", "duration_min": 90},
        ], **base), scenario="date", strategy="date")
        without = plan(PlanRequest(**base), scenario="date", strategy="date")
        self.assertGreaterEqual(with_anchor.metrics.satisfaction,
                                without.metrics.satisfaction,
                                "完全贴合原话锚点的行程，满意度不该更低")

    def test_no_anchor_date_does_not_crash(self):
        """无锚点的 date 请求照常出分，不留 anchor_fit。"""
        resp = plan(PlanRequest(city="dalian", zone="西安路", start_time="10:00",
                                end_time="22:00", top_n=4),
                    scenario="date", strategy="date")
        self.assertTrue(_items(resp))
        self.assertNotIn("anchor_fit", resp.meta)

    def test_daily_has_no_anchor_fit(self):
        """daily 场景不引入 anchor_fit：meta 无留痕，metrics 无新键。"""
        resp = plan(PlanRequest(city="dalian", interests=["自习"],
                                start_time="08:00", end_time="12:00", top_n=2),
                    scenario="daily", strategy="greedy")
        self.assertTrue(_items(resp))
        self.assertNotIn("anchor_fit", resp.meta)
        self.assertNotIn("anchor_fit", resp.metrics.to_dict())


if __name__ == "__main__":
    unittest.main()
