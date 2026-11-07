# @author: 助手
# @since: 2026-11-04
"""多校区扩展（docs/14）的高校层测试。

覆盖四件事：
1. **高校注册**：hit / tsinghua 以 `kind = "campus"` 注册，出现在高校列表里，
   「北京」这类纯城市不再混进高校下拉；
2. **数据路由**：选一所高校就读那份校区数据包（≥30 个 POI，L1 达标线），
   poi_id 前缀隔离（HI/TS），不与城市数据撞车；
3. **事实层**：每所高校的 visitor_guide.json 能被 llm/guide.load_guide 读到，
   结构完整（entry + spots + sources）；
4. **简报兜底**：无模型时 briefing 走确定性模板，绝不含编造事实。

跑法：
    python -m unittest tests.test_campuses -v
"""

import types
import unittest

from data.cities import (
    city_info,
    default_city,
    ensure_city_data,
    list_campuses,
    list_cities,
    reload,
    resolve_city,
)
from data.loader import clear_cache, load_pois
from engine.config import load_scenario
from llm.guide import briefing, load_guide


CAMPUS_KEYS = ("hit", "tsinghua")


class TestCampusRegistry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reload()

    def test_campuses_registered(self):
        for key in CAMPUS_KEYS:
            self.assertIn(key, list_campuses(), "%s 应注册为高校数据包" % key)
            info = city_info(key)
            self.assertEqual(info.kind, "campus", "%s 缺 kind=campus" % key)
            self.assertTrue(info.display_name.strip(), "%s 缺显示名" % key)

    def test_dalian_is_a_campus_entry_with_label(self):
        """dalian 一个 key 承载城市+校区两层：高校列表里显示「大连理工大学」。"""
        self.assertIn("dalian", list_campuses())
        info = city_info("dalian")
        self.assertEqual(info.display_name, "大连理工大学")
        # 但 name 仍是「大连」：amap 抓取、语义解析都靠它
        self.assertEqual(info.name, "大连")

    def test_beijing_is_city_not_campus(self):
        """「北京」保留（老数据 + 语义解析），但不出现在高校下拉里。"""
        self.assertIn("beijing", list_cities())
        self.assertNotIn("beijing", list_campuses())

    def test_default_city_is_a_campus(self):
        self.assertIn(default_city(), list_campuses())

    def test_aliases_resolve(self):
        """用户口语「哈工大」「清华」「大连理工大学」都要能落到 key。"""
        self.assertEqual(resolve_city("哈工大"), "hit")
        self.assertEqual(resolve_city("哈尔滨工业大学"), "hit")
        self.assertEqual(resolve_city("清华"), "tsinghua")
        self.assertEqual(resolve_city("清华大学"), "tsinghua")
        self.assertEqual(resolve_city("大连理工大学"), "dalian")
        self.assertEqual(resolve_city("北京"), "beijing")     # 老城市不丢


class TestCampusRouting(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reload()
        clear_cache()
        cls.cfg = load_scenario("demo")

    def test_campus_data_exists_and_meets_l1(self):
        """L1 达标线：每所高校 ≥30 个 POI（docs/14）。"""
        for key in CAMPUS_KEYS:
            self.assertTrue(ensure_city_data(key), "%s 缺 pois.csv" % key)
            clear_cache()
            pois = load_pois(self.cfg, key)
            self.assertGreaterEqual(len(pois), 30, "%s POI 不足 30（L1 线）" % key)
            ids = {p.poi_id for p in pois}
            self.assertEqual(len(ids), len(pois), "%s poi_id 有重复" % key)

    def test_campus_poi_ids_isolated_from_city_data(self):
        """高校数据包的 poi_id 前缀（HI/TS）不得与城市数据撞车。"""
        clear_cache()
        hit_ids = {p.poi_id for p in load_pois(self.cfg, "hit")}
        clear_cache()
        tsu_ids = {p.poi_id for p in load_pois(self.cfg, "tsinghua")}
        clear_cache()
        default_ids = {p.poi_id for p in load_pois(self.cfg)}
        self.assertFalse(hit_ids & tsu_ids, "hit 与 tsinghua 的 poi_id 撞了")
        self.assertFalse(hit_ids & default_ids, "hit 与默认数据的 poi_id 撞了")
        self.assertTrue(all(i.startswith("HI") for i in hit_ids))
        self.assertTrue(all(i.startswith("TS") for i in tsu_ids))

    def test_campus_city_field_matches_key(self):
        """pois.csv 的 city 列必须等于 key（bbox/一致性约定）。"""
        import csv
        from pathlib import Path
        for key in CAMPUS_KEYS:
            p = Path("data/campuses") / key / "pois.csv"
            with p.open("r", encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))
            self.assertTrue(rows, "%s pois.csv 是空的" % key)
            bad = [r["poi_id"] for r in rows if r.get("city") != key]
            self.assertEqual(bad, [], "%s 有 city 字段错误的行：%s" % (key, bad[:3]))


class TestCampusGuides(unittest.TestCase):
    def test_guide_loads_from_campus_package(self):
        """visitor_guide.json 放校区包里，load_guide 按高校 key 能读到。"""
        for key in CAMPUS_KEYS:
            guide = load_guide(key)
            self.assertTrue(guide, "%s 读不到 visitor_guide.json" % key)
            self.assertIn("entry", guide)
            self.assertIn("spots", guide)
            self.assertTrue(guide["spots"], "%s 事实层没有点位" % key)
            self.assertTrue(guide.get("sources"), "%s 事实层没有来源（数据纪律）" % key)
            # 数据纪律：渠道、电话只能来自事实卡，不能空
            self.assertTrue(guide["entry"].get("channels"), "%s 缺入校渠道" % key)

    def test_dalian_guide_still_loads_from_city_package(self):
        """零回归：大连的城市包事实层路径不受影响。"""
        guide = load_guide("dalian")
        self.assertTrue(guide, "dalian 的 visitor_guide.json 读不到了")
        self.assertIn("entry", guide)

    def test_unknown_campus_returns_empty(self):
        self.assertEqual(load_guide("火星"), {})


class TestBriefingFallback(unittest.TestCase):
    """无模型时的确定性模板：一条事实换一句话，不许编。"""

    @staticmethod
    def _fake_resp(key: str):
        pois = load_pois(load_scenario("demo"), key)[:3]
        items = [types.SimpleNamespace(name=p.name, arrive="09:10", leave="10:00",
                                       stay_min=50, cost=0.0, reason="测试站点")
                 for p in pois]
        return types.SimpleNamespace(
            days=[types.SimpleNamespace(day_index=1, items=items)])

    def test_fallback_for_campuses(self):
        for key in CAMPUS_KEYS:
            clear_cache()
            out = briefing(self._fake_resp(key), city=key, client=None, enabled=True)
            self.assertIn("headline", out)
            self.assertTrue(out["timeline"], "%s 兜底简报没有时间线" % key)
            # 兜底的 before 必须来自事实卡的渠道/要求，不能是空话
            self.assertTrue(out["before"], "%s 兜底简报缺出发前事项" % key)

    def test_fallback_without_guide(self):
        out = briefing(self._fake_resp("hit"), city="火星", client=None, enabled=True)
        self.assertIn("未配置该校的入校须知", out["headline"])


if __name__ == "__main__":
    unittest.main()
