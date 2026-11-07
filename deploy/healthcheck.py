# @author: 甲
# @reviewer: 乙
# @since: 2026-11-01
"""容器/服务器健康检查：**只判断"服务活着"，不判断"功能全对"**。

为什么单独一个脚本（而不是 Dockerfile 里一行 python -c）
--------------------------------------------------------
1. 设了 `ACCESS_CODE` 之后 `/api/health` 会返回 **401** —— 那是口令门在工作，
   服务其实是健康的。一行式检查会把这种情况误判成 unhealthy，然后容器被反复重启。
2. 多行 python -c 在 Dockerfile 里可读性极差，出错也没法单测。

判定口径：
    200 / 401  → 健康（能应答就是活着）
    其它       → 不健康
退出码 0 健康，1 不健康。也顺手给 systemd / 监控脚本用。

用法：
    python deploy/healthcheck.py [--url http://127.0.0.1:8000/api/health]
"""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request

#: 这两个状态码都说明"进程在、路由通"
ALIVE_CODES = (200, 401)


def check(url: str, timeout: float = 4.0) -> tuple[bool, str]:
    """返回 (是否健康, 说明)。**不抛异常**。"""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return (r.status in ALIVE_CODES), "HTTP %d" % r.status
    except urllib.error.HTTPError as e:
        if e.code in ALIVE_CODES:
            return True, "HTTP %d（需要口令，属正常）" % e.code
        return False, "HTTP %d" % e.code
    except urllib.error.URLError as e:
        return False, "连不上：%s" % str(e.reason)[:60]
    except TimeoutError:
        return False, "超时 %.0f 秒" % timeout


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="服务健康检查")
    ap.add_argument("--url", default="http://127.0.0.1:8000/api/health")
    ap.add_argument("--timeout", type=float, default=4.0)
    a = ap.parse_args(argv)

    ok, msg = check(a.url, a.timeout)
    print(("健康  " if ok else "不健康") + " " + a.url + "  " + msg)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
