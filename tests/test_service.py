# @author: 甲
# @reviewer: 乙
# @since: 2026-10-28
"""应用层测试：三种入口（UI / API / CLI）共用的那一份业务逻辑。

重点验三件事：
1. **overrides 的语义** —— 没传的键一个字都不许改（这是界面"不动控件就跟随文本"的根）。
2. **导出可用** —— md / csv 里必须真的出现站点名，不能导出个空壳。
3. **落盘路径** —— 不传路径时要落到 outputs/ 下，且文件名符合 AC-10 的约定。

跑法：
    .venv\\Scripts\\python.exe -m unittest tests.test_app -v
"""

import unittest
from pathlib import Path

from service.exporters import default_name, export, to_csv, to_markdown
from service.usecases import apply_overrides, options, plan_from_text, save_plan
from engine.models import PlanRequest, PlanResponse


class TestOptions(unittest.TestCase):
    def test_has_all_kinds(self):
        o = options()
        self.assertGreaterEqual(len(o["cities"]), 2)
        self.assertIn("demo", o["scenarios"])
        self.assertIn("stub", o["strategies"])

    def test_city_items_have_key_and_name(self):
        for c in options()["cities"]:
            self.assertTrue(c["key"])
            self.assertTrue(c["name"])

    def test_default_city_is_registered(self):
        o = options()
        self.assertIn(o["default_city"], [c["key"] for c in o["cities"]])


class TestOverrides(unittest.TestCase):
    def test_empty_overrides_changes_nothing(self):
        r = PlanRequest(city="dalian", top_n=4, pace="relaxed", daily_budget=100.0)
        before = r.to_dict()
        apply_overrides(r, None)
        apply_overrides(r, {})
        self.assertEqual(r.to_dict(), before)

    def test_only_given_keys_change(self):
        r = PlanRequest(city="dalian", top_n=4, pace="relaxed")
        apply_overrides(r, {"top_n": 2})
        self.assertEqual(r.top_n, 2)
        self.assertEqual(r.pace, "relaxed")
        self.assertEqual(r.city, "dalian")

    def test_budget_zero_means_unlimited(self):
        r = PlanRequest(daily_budget=100.0)
        apply_overrides(r, {"daily_budget": 0})
        self.assertIsNone(r.daily_budget)
        apply_overrides(r, {"daily_budget": -5})
        self.assertIsNone(r.daily_budget)
        apply_overrides(r, {"daily_budget": 88})
        self.assertEqual(r.daily_budget, 88.0)

    def test_city_is_normalized_to_key(self):
        """传中文名也要规范化成 registry 的 key。"""
        r = PlanRequest()
        apply_overrides(r, {"city": "大连市"})
        self.assertEqual(r.city, "dalian")

    def test_list_fields_accept_comma_string(self):
        r = PlanRequest()
        apply_overrides(r, {"interests": "海景，徒步"})
        self.assertEqual(r.interests, ["海景", "徒步"])


class TestPlanFromText(unittest.TestCase):
    def test_runs_and_keeps_raw_query(self):
        resp = plan_from_text("想看海，别太赶", scenario="demo", strategy="stub",
                              overrides={"city": "dalian", "top_n": 2})
        self.assertIsInstance(resp, PlanResponse)
        self.assertEqual(resp.request.city, "dalian")
        self.assertIn("想看海", resp.request.raw_query)
        self.assertGreater(len(resp.days[0].items), 0)

    def test_unknown_strategy_raises_keyerror(self):
        """未知算法必须抛 KeyError —— 由入口层转成友好提示，不能静默换算法。"""
        with self.assertRaises(KeyError):
            plan_from_text("随便", scenario="demo", strategy="not_exist")


class TestExporters(unittest.TestCase):
    def _resp(self) -> PlanResponse:
        return plan_from_text("想看海", scenario="demo", strategy="stub",
                              overrides={"city": "dalian", "top_n": 2})

    def test_default_name_matches_ac10(self):
        r = self._resp()
        self.assertEqual(default_name(r, "json"), "plan_demo_stub.json")

    def test_markdown_contains_station_and_day(self):
        r = self._resp()
        md = to_markdown(r)
        self.assertIn("# 行程单", md)
        self.assertIn("第 1 天", md)
        self.assertIn(r.days[0].items[0].name, md)

    def test_csv_has_header_and_rows(self):
        r = self._resp()
        text = to_csv(r)
        lines = [x for x in text.splitlines() if x.strip()]
        self.assertIn("站点", lines[0])
        self.assertEqual(len(lines) - 1, sum(len(d.items) for d in r.days))

    def test_unknown_format_falls_back_to_json(self):
        r = self._resp()
        self.assertIn('"strategy"', export(r, "不存在的格式"))

    def test_save_plan_writes_file(self):
        r = self._resp()
        path = save_plan(r, None, "json")
        self.assertTrue(Path(path).exists())
        self.assertTrue(Path(path).name.startswith("plan_"))
        self.assertIn("outputs", str(Path(path).parent))

    def test_save_plan_accepts_custom_path(self):
        r = self._resp()
        path = save_plan(r, "outputs/_tmp_test_export.md", "md")
        self.assertTrue(Path(path).exists())
        self.assertIn("# 行程单", Path(path).read_text(encoding="utf-8"))
        Path(path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
