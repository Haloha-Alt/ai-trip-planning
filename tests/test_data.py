# @author: 乙
# @reviewer: 甲
# @since: 2026-10-27
"""数据层测试：两个 CSV 能加载、字段齐全、编码正确。

跑法（项目根目录）：
    .venv\\Scripts\\python.exe -m unittest tests.test_data -v
"""

import unittest

from data.loader import (
    clear_cache,
    get_matrix_edge,
    get_poi,
    load_matrix,
    load_pois,
)
from engine.config import load_scenario


class TestPois(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        clear_cache()
        cls.cfg = load_scenario("demo")
        cls.pois = load_pois(cls.cfg)

    def test_loaded(self):
        self.assertEqual(len(self.pois), 8)

    def test_field_types(self):
        p = self.pois[0]
        self.assertIsInstance(p.poi_id, str)
        self.assertIsInstance(p.name, str)
        self.assertIsInstance(p.lat, float)
        self.assertIsInstance(p.lon, float)
        self.assertIsInstance(p.stay_min, int)
        self.assertIsInstance(p.cost, float)
        self.assertIsInstance(p.tags, list)

    def test_chinese_not_broken(self):
        """编码正确的最朴素检查：名称里应当有中文字符。"""
        self.assertTrue(any(ord(ch) > 127 for ch in self.pois[0].name))

    def test_tags_split_by_pipe(self):
        self.assertGreater(len(self.pois[0].tags), 0)
        self.assertNotIn("|", "".join(self.pois[0].tags))

    def test_time_format(self):
        for p in self.pois:
            self.assertRegex(p.open_time, r"^\d{2}:\d{2}$")
            self.assertRegex(p.close_time, r"^\d{2}:\d{2}$")

    def test_ids_unique(self):
        ids = [p.poi_id for p in self.pois]
        self.assertEqual(len(ids), len(set(ids)))

    def test_get_poi(self):
        self.assertIsNotNone(get_poi("P001", self.cfg))
        self.assertIsNone(get_poi("NOPE", self.cfg))


class TestMatrix(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        clear_cache()
        cls.cfg = load_scenario("demo")
        cls.edges = load_matrix(cls.cfg)

    def test_loaded(self):
        self.assertGreater(len(self.edges), 0)

    def test_known_edge(self):
        e = get_matrix_edge("P001", "P002", self.cfg)
        self.assertIsNotNone(e)
        self.assertEqual(e.mode, "walk")
        self.assertGreater(e.minutes, 0)

    def test_reverse_lookup(self):
        """矩阵里只写了一边，反查也要能命中。"""
        self.assertIsNotNone(get_matrix_edge("P002", "P001", self.cfg))

    def test_missing_edge_returns_none(self):
        """缺项返回 None —— 由 engine/geo.py 估算兜底。"""
        self.assertIsNone(get_matrix_edge("P003", "P006", self.cfg))

    def test_same_id_returns_none(self):
        self.assertIsNone(get_matrix_edge("P001", "P001", self.cfg))


if __name__ == "__main__":
    unittest.main()
