# @author: 甲
# @reviewer: 乙
# @since: 2026-10-31
"""分享链接工具的测试：**不联网**（只在本机开一个临时端口）。

这个工具的价值全在"别发错链接"，所以测的也是这件事：
1. 地址怎么写都能收拾干净（缺协议、多斜杠、带引号）；
2. 口令要正确拼进去并转义，空口令不能拼出一个假的 `?code=`；
3. 从 bat 里读口令要读得准，遇到变量写法要认得出来是"读不到"；
4. 端口在不在听要判断准 —— 这是"链接发出去打不开"的第一嫌疑。
"""

import socket
import tempfile
import unittest
from pathlib import Path

from tools.share_link import (
    build_url,
    check_health,
    is_listening,
    normalize_base,
    parse_bat_code,
)


class TestNormalize(unittest.TestCase):
    def test_adds_scheme(self):
        self.assertEqual(normalize_base("abcd.r3.cpolar.cn"), "https://abcd.r3.cpolar.cn")

    def test_strips_trailing_slash(self):
        self.assertEqual(normalize_base("http://10.0.0.2:8000/"), "http://10.0.0.2:8000")

    def test_strips_quotes_and_spaces(self):
        self.assertEqual(normalize_base('  "https://a.b/c/"  '), "https://a.b/c")

    def test_drops_query_and_fragment(self):
        self.assertEqual(normalize_base("https://a.b/?code=x#top"), "https://a.b")

    def test_empty(self):
        self.assertEqual(normalize_base(""), "")
        self.assertEqual(normalize_base("   "), "")


class TestBuildUrl(unittest.TestCase):
    def test_with_code(self):
        self.assertEqual(build_url("http://1.2.3.4:8000", "trip-8261"),
                         "http://1.2.3.4:8000/?code=trip-8261")

    def test_without_code(self):
        self.assertEqual(build_url("http://1.2.3.4:8000", ""), "http://1.2.3.4:8000/")

    def test_code_is_encoded(self):
        self.assertEqual(build_url("https://a.b", "a b&c"),
                         "https://a.b/?code=a%20b%26c")

    def test_custom_path(self):
        self.assertEqual(build_url("https://a.b/", "k", "/api/health"),
                         "https://a.b/api/health?code=k")

    def test_empty_base(self):
        self.assertEqual(build_url("", "k"), "")


class TestParseBat(unittest.TestCase):
    def _write(self, text: str) -> Path:
        f = tempfile.NamedTemporaryFile("w", suffix=".bat", delete=False, encoding="utf-8")
        f.write(text)
        f.close()
        return Path(f.name)

    def test_reads_code(self):
        p = self._write("@echo off\nset WEB_HOST=0.0.0.0\nset ACCESS_CODE=trip-8261\n")
        self.assertEqual(parse_bat_code(p), "trip-8261")

    def test_reads_quoted_code(self):
        p = self._write('set ACCESS_CODE="my code"\n')
        self.assertEqual(parse_bat_code(p), "my code")

    def test_variable_is_not_a_code(self):
        p = self._write("set ACCESS_CODE=%SOMETHING%\n")
        self.assertEqual(parse_bat_code(p), "")

    def test_blank_code(self):
        p = self._write("set ACCESS_CODE=\n")
        self.assertEqual(parse_bat_code(p), "")

    def test_missing_file(self):
        self.assertEqual(parse_bat_code(Path("definitely-not-here.bat")), "")


class TestPort(unittest.TestCase):
    def test_listening_and_closed(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        try:
            self.assertTrue(is_listening(port))
        finally:
            s.close()
        self.assertFalse(is_listening(port, timeout=0.5))


class TestCheckHealth(unittest.TestCase):
    def test_closed_port_reports_failure(self):
        """没起服务时必须**明确失败**，不能假装成功。"""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        ok, msg = check_health("http://127.0.0.1:%d" % port, "", timeout=1.0)
        self.assertFalse(ok)
        self.assertTrue(msg)

    def test_empty_base(self):
        ok, msg = check_health("", "")
        self.assertFalse(ok)
        self.assertIn("地址", msg)


if __name__ == "__main__":
    unittest.main()
