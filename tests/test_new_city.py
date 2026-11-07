# @author: 甲
# @reviewer: 乙
# @since: 2026-11-01
"""新城市接入 + 部署自检的测试：**不联网**。

盯的是"手工抄表最容易抄错"的那几处：
1. 城市 key 合法性（写错了 registry 会静默失效）；
2. `pois.csv` 列序（少一列不报错，只会让某字段悄悄变默认值）；
3. 高德返回 → 我们行格式的转换（坐标、类别、默认停留）；
4. 健康检查的判定口径（**401 必须算健康**，否则设了口令的容器会被反复重启）。
"""

import tempfile
import unittest
from pathlib import Path

from deploy.healthcheck import check as health_check
from tools.new_city import (
    POI_HEADER,
    _category_of,
    _stay_of,
    read_csv,
    row_from_amap,
    slug_ok,
    write_csv,
)


class TestSlug(unittest.TestCase):
    def test_ok(self):
        for k in ("suzhou", "hangzhou2", "beijing", "xian_city"):
            self.assertTrue(slug_ok(k), k)

    def test_bad(self):
        for k in ("", "Suzhou", "2city", "a", "苏州", "a-b", "a b"):
            self.assertFalse(slug_ok(k), k)


class TestMapping(unittest.TestCase):
    def test_category_takes_deepest(self):
        self.assertEqual(_category_of("科教文化服务;图书馆;图书馆"), "图书馆")
        self.assertEqual(_category_of("餐饮服务;中餐厅;中餐厅"), "中餐厅")
        self.assertEqual(_category_of(""), "其他")

    def test_stay_default_and_fallback(self):
        self.assertEqual(_stay_of("图书馆"), 120)
        self.assertEqual(_stay_of("不存在类别"), 60)

    def test_row_from_amap(self):
        poi = {
            "name": "令希图书馆",
            "type": "科教文化服务;图书馆;图书馆",
            "location": "121.5288,38.8831",
            "address": "凌水主校区",
            "biz_ext": {"cost": "0"},
        }
        r = row_from_amap(poi, "dalian", 7, today="2026-11-01")
        self.assertEqual(r["poi_id"], "DA007")
        self.assertEqual(r["name"], "令希图书馆")
        self.assertEqual(r["category"], "图书馆")
        self.assertEqual(r["lat"], "38.8831")
        self.assertEqual(r["lon"], "121.5288")
        self.assertEqual(r["city"], "dalian")
        self.assertEqual(r["stay_min"], "120")
        self.assertEqual(r["source_date"], "2026-11-01")
        self.assertEqual(list(r.keys()), POI_HEADER)      # 列序必须与表头一致

    def test_row_skips_bad_input(self):
        self.assertIsNone(row_from_amap({"name": "x"}, "d", 1))                      # 无坐标
        self.assertIsNone(row_from_amap({"name": "x", "location": "a,b"}, "d", 1))   # 非数字
        self.assertIsNone(row_from_amap({"name": "", "location": "1,2"}, "d", 1))    # 无名

    def test_biz_ext_list_does_not_crash(self):
        """高德偶尔把 biz_ext 返回成 []，坑过一次。"""
        r = row_from_amap({"name": "x", "location": "1,2", "biz_ext": []}, "d", 1)
        self.assertEqual(r["cost"], "0")


class TestCsv(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "pois.csv"
            rows = [{
                "poi_id": "DL001", "name": "图书馆", "category": "图书馆",
                "lat": "38.88", "lon": "121.52", "city": "dalian",
            }]
            write_csv(p, rows, POI_HEADER)
            header, back = read_csv(p)
            self.assertEqual(header, POI_HEADER)
            self.assertEqual(len(back), 1)
            self.assertEqual(back[0]["name"], "图书馆")
            self.assertEqual(back[0]["stay_min"], "")     # 未给的列写空，不写 None

    def test_header_has_18_columns(self):
        self.assertEqual(len(POI_HEADER), 18)


class TestHealthcheck(unittest.TestCase):
    def test_dead_port_is_unhealthy(self):
        import socket

        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        ok, msg = health_check("http://127.0.0.1:%d/api/health" % port, timeout=1.0)
        self.assertFalse(ok)
        self.assertTrue(msg)

    def test_alive_codes_include_401(self):
        """401 = 口令门在工作，服务是健康的。这条错了容器会被反复重启。"""
        from deploy.healthcheck import ALIVE_CODES

        self.assertIn(200, ALIVE_CODES)
        self.assertIn(401, ALIVE_CODES)


if __name__ == "__main__":
    unittest.main()
