# @author: 甲
# @reviewer: 乙
# @since: 2026-10-27
"""城市层测试：城市是"数据路由"，不是代码分支。

三条必须守住的性质：
1. **零回归**：不传 city 时，行为与只有一个城市时**完全一致**。
2. **路由生效**：传了已注册城市，读到的就是那个城市的数据。
3. **绝不崩**：未注册的城市、空城市、坏 registry 都只回退，不抛异常。

跑法：
    .venv\\Scripts\\python.exe -m unittest tests.test_cities -v
"""

import unittest

from data.cities import (
    city_info,
    default_city,
    ensure_city_data,
    list_cities,
    matrix_path,
    poi_path,
    reload,
    resolve_city,
)
from data.loader import clear_cache, get_poi, load_pois
from engine.config import load_scenario


class TestRegistry(unittest.TestCase):
    def test_has_cities(self):
        self.assertGreaterEqual(len(list_cities()), 2, "至少要注册两个城市，否则多城市没被验证")

    def test_default_city_registered(self):
        self.assertIn(default_city(), list_cities())

    def test_info_fields(self):
        for key in list_cities():
            info = city_info(key)
            self.assertIsNotNone(info, key)
            self.assertTrue(info.name.strip(), "%s 缺 name" % key)
            self.assertTrue(-90 <= info.lat <= 90, "%s 纬度异常" % key)
            self.assertTrue(-180 <= info.lon <= 180, "%s 经度异常" % key)


class TestResolveCity(unittest.TestCase):
    def test_empty_and_none(self):
        self.assertIsNone(resolve_city(""))
        self.assertIsNone(resolve_city(None))

    def test_unknown_city(self):
        self.assertIsNone(resolve_city("不存在的城市XYZ"))

    def test_key_and_name_and_alias(self):
        """key / 中文名 / 别名 三种写法都要解析到同一个 key。"""
        for key in list_cities():
            info = city_info(key)
            self.assertEqual(resolve_city(key), key)
            self.assertEqual(resolve_city(info.name), key)
            for a in info.aliases:
                self.assertEqual(resolve_city(a), key, "别名 %s 没解析到 %s" % (a, key))

    def test_case_and_space_insensitive(self):
        key = list_cities()[0]
        self.assertEqual(resolve_city("  %s  " % key.upper()), key)

    def test_substring_in_sentence(self):
        """'我想去<城市>玩两天' 这种整句也要能认出来。"""
        for key in list_cities():
            info = city_info(key)
            self.assertEqual(resolve_city("我想去%s玩两天" % info.name), key)


class TestRouting(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reload()
        clear_cache()
        cls.cfg = load_scenario("demo")

    def test_unknown_city_falls_back(self):
        self.assertEqual(poi_path("不存在的城市XYZ", self.cfg.poi_file), self.cfg.poi_file)
        self.assertEqual(matrix_path("不存在的城市XYZ", self.cfg.matrix_file), self.cfg.matrix_file)

    def test_registered_city_uses_own_file(self):
        for key in list_cities():
            if not ensure_city_data(key):
                continue
            self.assertNotEqual(poi_path(key, self.cfg.poi_file), self.cfg.poi_file,
                                "城市 %s 有数据却没被路由到" % key)

    def test_load_pois_without_city_is_unchanged(self):
        """零回归：不传 city 与传空串，结果必须和改造前一致（同一份默认数据）。"""
        clear_cache()
        a = [p.poi_id for p in load_pois(self.cfg)]
        clear_cache()
        b = [p.poi_id for p in load_pois(self.cfg, "")]
        self.assertEqual(a, b)
        self.assertGreater(len(a), 0)

    def test_each_city_loads_its_own_pois(self):
        seen: set[str] = set()
        for key in list_cities():
            if not ensure_city_data(key):
                continue
            clear_cache()
            pois = load_pois(self.cfg, key)
            self.assertGreater(len(pois), 0, "城市 %s 读出 0 条 POI" % key)
            for p in pois:
                self.assertTrue(p.poi_id.strip(), "城市 %s 有空 poi_id" % key)
            ids = {p.poi_id for p in pois}
            self.assertEqual(len(ids), len(pois), "城市 %s 的 poi_id 有重复" % key)
            self.assertFalse(seen & ids, "不同城市之间 poi_id 撞了：%s" % sorted(seen & ids)[:3])
            seen |= ids

    def test_get_poi_respects_city(self):
        """同一个 poi_id 在默认数据里查得到，在城市数据里应当查不到（数据隔离）。"""
        for key in list_cities():
            if not ensure_city_data(key):
                continue
            clear_cache()
            city_ids = {p.poi_id for p in load_pois(self.cfg, key)}
            clear_cache()
            default_ids = {p.poi_id for p in load_pois(self.cfg)}
            only_default = sorted(default_ids - city_ids)
            if not only_default:
                continue
            clear_cache()
            self.assertIsNone(get_poi(only_default[0], self.cfg, key))
            clear_cache()
            self.assertIsNotNone(get_poi(only_default[0], self.cfg))
            return
        self.skipTest("没有可对比的差异 ID，跳过")

    def test_unknown_city_still_returns_pois(self):
        """未注册城市必须回退出结果，而不是空列表或异常。"""
        clear_cache()
        self.assertGreater(len(load_pois(self.cfg, "火星")), 0)


if __name__ == "__main__":
    unittest.main()
