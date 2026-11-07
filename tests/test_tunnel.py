# @author: 甲
# @reviewer: 乙
# @since: 2026-11-01
"""隧道助手测试：**不联网**（只喂文本）。

三件事必须对，错一件就会让人白折腾半小时：
1. 从一屏彩色日志里认准隧道域名（cpolar/cloudflared 的域名不同）；
2. 口令要拼进链接，否则对方打开就是"需要访问口令"；
3. **认出隧道实际指向的本机端口** —— 命令行参数和后台配置不一致时，
   表现为域名能访问但一律 502，只看日志猜不出来。
"""

import tempfile
import unittest
from pathlib import Path

from tools.tunnel import build_share, clean, cpolar_target_port, first_url


class TestParse(unittest.TestCase):
    def test_cpolar_url(self):
        line = "Forwarding   https://253e175f.r8.cpolar.cn -> http://localhost:8080"
        self.assertEqual(first_url(line), "https://253e175f.r8.cpolar.cn")

    def test_cloudflare_url(self):
        line = "|  https://funny-cat-blue.trycloudflare.com  |"
        self.assertEqual(first_url(line), "https://funny-cat-blue.trycloudflare.com")

    def test_ansi_and_cr_are_stripped(self):
        line = "\x1b[32mForwarding\x1b[0m https://abc123.r3.cpolar.cn -> 8000\r"
        self.assertEqual(first_url(line), "https://abc123.r3.cpolar.cn")
        self.assertNotIn("\x1b", clean(line))

    def test_no_url(self):
        self.assertEqual(first_url("connecting..."), "")
        self.assertEqual(first_url(""), "")

    def test_ignores_unrelated_urls(self):
        """日志里可能有别家的 URL，不能认错。"""
        line = "see https://docs.example.com/help and https://x.r8.cpolar.cn"
        self.assertEqual(first_url(line), "https://x.r8.cpolar.cn")


class TestShare(unittest.TestCase):
    def test_with_code(self):
        self.assertEqual(build_share("https://a.cpolar.cn", "k1"),
                         "https://a.cpolar.cn?code=k1")

    def test_without_code(self):
        self.assertEqual(build_share("https://a.cpolar.cn", ""), "https://a.cpolar.cn")


class TestCpolarPort(unittest.TestCase):
    def _mk(self, text: str) -> Path:
        d = Path(tempfile.mkdtemp())
        (d / "cpolar_service.log").write_text(text, encoding="utf-8")
        return d

    def test_reads_private_leg(self):
        d = self._mk(
            'level=warning msg="Failed to open private leg http://localhost:8080: dial tcp"\n'
            'level=warning msg="Failed to open private leg http://localhost:8080: dial tcp"\n'
        )
        self.assertEqual(cpolar_target_port(d), 8080)

    def test_last_one_wins(self):
        d = self._mk(
            "private leg http://localhost:8000 ok\n"
            "private leg http://localhost:8080 fail\n"
        )
        self.assertEqual(cpolar_target_port(d), 8080)

    def test_missing_dir(self):
        self.assertEqual(cpolar_target_port(Path("no-such-dir-xyz")), 0)

    def test_no_hit(self):
        d = self._mk("connecting...\n")
        self.assertEqual(cpolar_target_port(d), 0)


if __name__ == "__main__":
    unittest.main()
