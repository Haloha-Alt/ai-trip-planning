# @author: 助手
# @since: 2026-11-07
"""v1.6.6 行程单导出改进测试。

覆盖四件事：
1. origin_leg 存在时逐段交通第 1 段消费打车数据，不再说"出发地未入库"；
2. 末班车自动判定赶不赶得上（收尾 bullet）；
3. 短距段（0 分钟 / <0.3 km）不再显示 metro；
4. 预算明细含"出发打车"，合计计入打车费。

跑法：python -m unittest tests.test_v166_export -v
"""

import unittest

from engine.models import PlanRequest
from engine.planner import plan
from service.exporters import to_markdown


class TestV166Export(unittest.TestCase):
    """date 场景行程单 v1.6.6 四项改进。"""

    @classmethod
    def setUpClass(cls):
        cls.resp = plan(PlanRequest(city="dalian", zone="星海", top_n=3,
                                    start_time="14:00", end_time="23:00",
                                    origin="大工凌水校区"),
                        scenario="date", strategy="date")
        cls.md = to_markdown(cls.resp)

    def test_first_segment_uses_origin_leg(self):
        oleg = self.resp.meta.get("origin_leg") or {}
        self.assertTrue(oleg, "前置：origin 应命中 landmarks 得到 origin_leg")
        self.assertIn("打车", self.md)
        seg1 = next(ln for ln in self.md.splitlines()
                    if ln.startswith("- 第 1 段"))
        self.assertIn("打车约", seg1)
        self.assertIn("车费", seg1)
        self.assertNotIn("出发地未入库", self.md)

    def test_tail_train_verdict(self):
        self.assertTrue(
            "末班（富余" in self.md or "打车回校" in self.md,
            "收尾必须给出末班车判定：%s" % [ln for ln in self.md.splitlines()
                                            if ln.startswith("- 收尾")])

    def test_short_segment_no_metro(self):
        self.assertNotIn("metro 约 0 分钟 / 0.", self.md)

    def test_budget_contains_origin_taxi_and_total_grows(self):
        self.assertIn("出发打车", self.md)
        # 合计 = 各天 total_cost 之和 + 打车费
        total_line = next(ln for ln in self.md.splitlines()
                          if ln.startswith("| **合计**"))
        shown = float(total_line.split("**")[3])
        base = sum(d.total_cost for d in self.resp.days)
        oleg = self.resp.meta.get("origin_leg") or {}
        rleg = self.resp.meta.get("return_leg") or {}
        expected = base + (oleg.get("fare") or 0)
        if not (self.resp.meta.get("last_trains") and rleg):
            expected += rleg.get("fare") or 0
        self.assertAlmostEqual(shown, expected, places=2,
                               msg="合计必须计入打车费")
        self.assertGreater(shown, base, "计入打车费后合计应大于站点+交通原合计")


if __name__ == "__main__":
    unittest.main()
