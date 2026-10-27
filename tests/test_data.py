# @author: 乙
# @reviewer: 甲
# @since: 2026-10-27
"""数据层测试：两个 CSV 能加载、字段齐全、编码正确。

写测试的两条原则（换真实数据后必须仍然全绿）：
1. **不锁死具体条数与具体 ID** —— 只校验"结构正确"和"约束成立"。
2. **保留硬约束** —— 至少 1 条 POI、ID 唯一、时间格式合法、字段类型正确。
   否则测试会退化成"永远绿"，失去意义。

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
        """至少读出 1 条。条数由数据决定，测试不锁死。"""
        self.assertGreater(len(self.pois), 0, "pois.csv 一条都没读出来（查编码/列名）")

    def test_field_types(self):
        """全表遍历，不是只看第 0 条。"""
        for p in self.pois:
            self.assertIsInstance(p.poi_id, str)
            self.assertIsInstance(p.name, str)
            self.assertIsInstance(p.lat, float)
            self.assertIsInstance(p.lon, float)
            self.assertIsInstance(p.stay_min, int)
            self.assertIsInstance(p.cost, float)
            self.assertIsInstance(p.tags, list)

    def test_name_not_empty(self):
        """名称不能为空。不检查"必须是中文" —— 英文数据同样合法。"""
        for p in self.pois:
            self.assertTrue(p.name.strip(), "存在空名称的 POI：%s" % p.poi_id)

    def test_tags_split_by_pipe(self):
        """tags 用竖线分隔，拆完之后不应该还剩竖线。"""
        for p in self.pois:
            self.assertNotIn("|", "".join(p.tags))

    def test_time_format(self):
        for p in self.pois:
            self.assertRegex(p.open_time, r"^\d{2}:\d{2}$")
            self.assertRegex(p.close_time, r"^\d{2}:\d{2}$")

    def test_ids_unique(self):
        ids = [p.poi_id for p in self.pois]
        self.assertEqual(len(ids), len(set(ids)), "poi_id 有重复")

    def test_coordinates_sane(self):
        """经纬度要在合理范围内 —— 防止 Excel 把坐标格式搞坏。"""
        for p in self.pois:
            self.assertTrue(-90 <= p.lat <= 90, "%s 纬度异常：%s" % (p.poi_id, p.lat))
            self.assertTrue(-180 <= p.lon <= 180, "%s 经度异常：%s" % (p.poi_id, p.lon))

    def test_get_poi(self):
        first = self.pois[0].poi_id
        self.assertIsNotNone(get_poi(first, self.cfg))
        self.assertIsNone(get_poi("__NOT_EXIST__", self.cfg))


class TestMatrix(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        clear_cache()
        cls.cfg = load_scenario("demo")
        cls.edges = load_matrix(cls.cfg)
        cls.ids = [p.poi_id for p in load_pois(cls.cfg)]

    def test_rows_parse(self):
        """矩阵允许为空（全部走估算兜底），所以只校验"有行时字段类型正确"。"""
        for e in self.edges:
            self.assertIsInstance(e.from_id, str)
            self.assertIsInstance(e.to_id, str)
            self.assertGreaterEqual(e.minutes, 0.0)
            self.assertGreaterEqual(e.distance_km, 0.0)
            self.assertGreaterEqual(e.cost, 0.0)

    def test_endpoints_exist_in_pois(self):
        """矩阵里的 ID 必须能在 POI 表里找到，否则是笔误。"""
        if not self.edges:
            self.skipTest("矩阵为空，跳过")
        known = set(self.ids)
        for e in self.edges:
            self.assertIn(e.from_id, known, "矩阵引用了不存在的 POI：%s" % e.from_id)
            self.assertIn(e.to_id, known, "矩阵引用了不存在的 POI：%s" % e.to_id)

    def test_known_edge_roundtrip(self):
        """拿矩阵里的第一条自证：查得到，且数值一致。"""
        if not self.edges:
            self.skipTest("矩阵为空，跳过")
        e = self.edges[0]
        got = get_matrix_edge(e.from_id, e.to_id, self.cfg)
        self.assertIsNotNone(got)
        self.assertAlmostEqual(got.minutes, e.minutes)

    def test_reverse_lookup(self):
        """矩阵里只写了一边，反查也要能命中。"""
        if not self.edges:
            self.skipTest("矩阵为空，跳过")
        e = self.edges[0]
        self.assertIsNotNone(get_matrix_edge(e.to_id, e.from_id, self.cfg))

    def test_missing_edge_returns_none(self):
        """动态找一对矩阵里没有的组合 —— 缺项必须返回 None（由 geo.py 估算兜底）。"""
        existing = set()
        for e in self.edges:
            existing.add((e.from_id, e.to_id))
            existing.add((e.to_id, e.from_id))
        pair = next(((a, b) for a in self.ids for b in self.ids
                     if a != b and (a, b) not in existing), None)
        if pair is None:
            self.skipTest("矩阵已覆盖所有两两组合，没有缺项可测")
        self.assertIsNone(get_matrix_edge(pair[0], pair[1], self.cfg))

    def test_same_id_returns_none(self):
        if not self.ids:
            self.skipTest("没有 POI，跳过")
        self.assertIsNone(get_matrix_edge(self.ids[0], self.ids[0], self.cfg))


if __name__ == "__main__":
    unittest.main()
