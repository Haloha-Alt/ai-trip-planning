# @author: 助手
# @since: 2026-11-07
"""v1.1(B1) 导出全家桶：ics / geojson / 单文件 html。

验收口径（docs/07 B1）：三种新格式各有单测；单文件 HTML 无外部依赖、
断网可打开；未知格式回退 json 不崩。
全程离线，跑法：python -m unittest tests.test_export_v11 -v
"""
import json
import unittest

from engine.config import load_scenario
from engine.models import PlanRequest
from engine.planner import plan
from service.exporters import export, to_geojson, to_html_single, to_ics


def _plan():
    return plan(PlanRequest(city="dalian", start_time="14:00", end_time="23:00",
                            top_n=3, date="2026-11-07"),
                scenario="date", strategy="date")


class TestIcs(unittest.TestCase):
    def setUp(self):
        self.resp = _plan()
        self.ics = to_ics(self.resp)

    def test_structure(self):
        self.assertTrue(self.ics.startswith("BEGIN:VCALENDAR"))
        self.assertIn("END:VCALENDAR", self.ics)
        self.assertEqual(self.ics.count("BEGIN:VEVENT"),
                         self.ics.count("END:VEVENT"))
        self.assertGreater(self.ics.count("BEGIN:VEVENT"), 0, "每站一个 VEVENT")

    def test_event_fields(self):
        first = self.ics.split("BEGIN:VEVENT")[1]
        self.assertIn("DTSTART:20261107T", first)
        self.assertIn("DTEND:20261107T", first)
        self.assertIn("SUMMARY:", first)
        self.assertIn("UID:", first)

    def test_export_dispatch(self):
        self.assertIn("BEGIN:VCALENDAR", export(self.resp, "ics"))


class TestGeojson(unittest.TestCase):
    def setUp(self):
        self.resp = _plan()
        self.gj = json.loads(to_geojson(self.resp))

    def test_feature_collection(self):
        self.assertEqual(self.gj["type"], "FeatureCollection")
        self.assertGreater(len(self.gj["features"]), 0)

    def test_coordinates_are_lon_lat(self):
        f = self.gj["features"][0]
        self.assertEqual(f["geometry"]["type"], "Point")
        lon, lat = f["geometry"]["coordinates"]
        self.assertGreater(lon, 119, "大连经度应 >119（GeoJSON 是 [经度,纬度]）")
        self.assertLess(lat, 42, "大连纬度应 <42")

    def test_export_dispatch(self):
        self.assertIn("FeatureCollection", export(self.resp, "geojson"))


class TestHtmlSingle(unittest.TestCase):
    def setUp(self):
        self.resp = _plan()
        self.html = to_html_single(self.resp)

    def test_no_external_deps(self):
        self.assertNotIn("http://", self.html.replace("http://www.w3.org", ""))
        self.assertNotIn("https://", self.html)
        self.assertNotIn("<script", self.html.lower(), "离线单文件不许引外部脚本")

    def test_content_survives(self):
        self.assertIn("<h1>", self.html)
        self.assertIn("<table>", self.html)
        self.assertIn("行程单", self.html)
        self.assertIn("charset", self.html)

    def test_export_dispatch(self):
        self.assertIn("<!DOCTYPE html>", export(self.resp, "html"))

    def test_unknown_fmt_falls_back(self):
        self.assertTrue(export(self.resp, "nope").startswith("{"),
                        "未知格式回退 json，不抛异常")


if __name__ == "__main__":
    unittest.main()
