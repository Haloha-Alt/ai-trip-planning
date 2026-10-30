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

    def test_default_strategy_comes_from_scenario_not_alphabet(self):
        """AC-44：默认算法必须是场景配置里的值，不能是 `available()` 的第一个。

        `available()` 是字母序（greedy / random / stub / two_opt），
        前端如果拿第一个当默认，页面就悄悄换成了 greedy，站点顺序与命令行对不上。
        """
        from engine.config import load_scenario

        o = options()
        cfg = load_scenario(o["default_scenario"])
        self.assertEqual(o["default_strategy"], cfg.default_strategy)
        self.assertNotEqual(o["default_strategy"], sorted(o["strategies"])[0])

    def test_every_scenario_reports_its_own_default(self):
        """切场景时前端要跟着切算法，所以每个场景都得有一份默认值。"""
        o = options()
        self.assertEqual(sorted(o["scenario_strategies"]), sorted(o["scenarios"]))
        self.assertEqual(o["scenario_strategies"][o["default_scenario"]],
                         o["default_strategy"])


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

    def test_city_falls_back_to_registry_default(self):
        """文本不提城市时不能留空：空城市会掉回 8 条占位数据（data/pois.csv）。"""
        from data.cities import default_city
        from service.usecases import build_request

        req = build_request("上午自习，中午吃饭", scenario="daily")
        self.assertEqual(req.city, default_city())

        resp = plan_from_text("上午自习，中午吃饭", scenario="daily", strategy="stub")
        names = [i.name for i in resp.days[0].items]
        self.assertTrue(names)
        self.assertFalse([n for n in names if n.startswith("占位点")],
                         "默认数据还是占位的：%s" % names)

    def test_pending_list_is_reported(self):
        """任务书要求区分"已核实"与"待确认"：用到的种子数据必须进 meta.pending。"""
        resp = plan_from_text("上午自习，中午吃饭", scenario="daily", strategy="stub")
        self.assertIn("pending", resp.meta)
        self.assertTrue(resp.meta["pending"])


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
