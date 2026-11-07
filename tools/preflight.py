# @author: 甲
# @reviewer: 乙
# @since: 2026-11-01
"""上线前自检：**一条命令回答"现在能不能给别人用"**。

为什么不是再写一份文档
----------------------
部署到服务器 / 开穿透之前，真正会踩的坑就那七个，而且都不是"看文档能发现"的：
Key 没生效、口令没设、网关在云主机上访问不到、端口被占、数据没同步、兜底样例没带上。
文档写了没人逐条核对，工具跑了就一定有结论。

检查项（每项都会给出下一步该做什么）
    1. Python 与依赖          —— 换机器第一道坎
    2. .env 与三个开关        —— Key / 口令 / 监听地址
    3. 大模型网关可达性        —— **云服务器上最容易挂在这**
    4. 8000 端口              —— 在跑？被占？还是空着
    5. 城市数据完整性          —— POI 行数、矩阵边数
    6. 断网兜底样例            —— M6 的命根子
    7. 测试是否全绿            —— 用 --with-tests 才会真跑（慢 3 秒）

跑法：python -m tools.preflight [--with-tests] [--offline]
退出码：0 全部通过（warn 不算失败）；1 有 fail。
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from service.bootstrap import DATA_DIR, WEB_DIR, load_dotenv  # noqa: E402

load_dotenv()

PASS, WARN, FAIL = "[ ok ]", "[warn]", "[fail]"
_fails = 0
_warns = 0


def _say(tag: str, msg: str, hint: str = "") -> None:
    global _fails, _warns
    if tag == FAIL:
        _fails += 1
    elif tag == WARN:
        _warns += 1
    print("%s %s" % (tag, msg))
    if hint:
        print("       -> %s" % hint)


# ------------------------------------------------------------------ 各项检查

def check_python() -> None:
    v = sys.version_info
    if v >= (3, 11):
        _say(PASS, "Python %d.%d.%d" % (v.major, v.minor, v.micro))
    else:
        _say(FAIL, "Python %d.%d 太旧（需要 ≥3.11，tomllib 是标准库依赖）" % (v.major, v.minor),
             "装 3.11+ 后重建虚拟环境")

    try:
        import uvicorn  # noqa: F401
        _say(PASS, "uvicorn 可用（生产服务器）")
    except ModuleNotFoundError:
        _say(WARN, "没有 uvicorn，会退回标准库服务器（仅够演示）", "pip install uvicorn starlette")


def check_env() -> None:
    envf = ROOT / ".env"
    if not envf.exists():
        _say(FAIL, "缺 .env", "复制 .env.example 为 .env 再填值")
    else:
        _say(PASS, ".env 存在")

    if (os.environ.get("LLM_API_KEY") or "").strip():
        _say(PASS, "LLM_API_KEY 已设置（大模型可用）")
    else:
        _say(WARN, "LLM_API_KEY 为空：解说/解析走规则模板（**仍可演示**，这是合法配置）")

    if (os.environ.get("ACCESS_CODE") or "").strip():
        _say(PASS, "ACCESS_CODE 已设置（外人需要口令）")
    else:
        _say(WARN, "ACCESS_CODE 为空：只要对外开放，拿到地址的人都能用你的额度",
             "要在 .env 里写 ACCESS_CODE=xxx，或直接用 分享给他人.bat")

    host = (os.environ.get("WEB_HOST") or "127.0.0.1").strip()
    if host in ("0.0.0.0", "::"):
        _say(WARN, "WEB_HOST=%s：已经对外开放监听" % host, "确认口令设了再对外")
    else:
        _say(PASS, "WEB_HOST=%s（只监听本机）" % host)


def check_llm_online() -> None:
    key = (os.environ.get("LLM_API_KEY") or "").strip()
    base = (os.environ.get("LLM_BASE_URL") or "").strip().rstrip("/")
    if not key or not base:
        _say(WARN, "跳过网关检查（没配 Key）")
        return
    url = base + "/models"
    req = urllib.request.Request(url, headers={"Authorization": "Bearer %s" % key})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            obj = json.loads(r.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:
        _say(FAIL, "网关返回 HTTP %d：%s" % (e.code, url),
             "云服务器上出现 403 通常是网关只放行校园网 IP —— 那就别上公网，改走本机+穿透")
        return
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        _say(FAIL, "网关不可达：%s（%s）" % (url, str(e)[:60]),
             "先 curl 一次；若服务器在校外，确认网关是否限制来源 IP")
        return
    n = len(obj.get("data") or []) if isinstance(obj, dict) else 0
    _say(PASS, "网关可达，登记 %d 个模型" % n)


def check_port(port: int = 8000) -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1.5)
    try:
        s.connect(("127.0.0.1", port))
        _say(PASS, "127.0.0.1:%d 有服务在跑" % port)
    except OSError:
        _say(WARN, "127.0.0.1:%d 没有服务" % port, "启动：python -m api.server")
    finally:
        s.close()


def check_data() -> None:
    reg = DATA_DIR / "cities" / "registry.toml"
    if not reg.exists():
        _say(FAIL, "缺城市注册表 data/cities/registry.toml")
        return
    import re

    keys = re.findall(r"\[cities\.([a-z0-9_]+)\]", reg.read_text(encoding="utf-8"))
    if not keys:
        _say(FAIL, "注册表里没有城市")
        return

    for k in keys:
        poi = DATA_DIR / "cities" / k / "pois.csv"
        mtx = DATA_DIR / "cities" / k / "travel_matrix.csv"
        if not poi.exists():
            _say(FAIL, "%s 缺 pois.csv" % k)
            continue
        rows = max(0, len(poi.read_text(encoding="utf-8").splitlines()) - 1)
        if rows == 0:
            _say(FAIL, "%s 的 pois.csv 是空的" % k)
        elif mtx.exists():
            edges = max(0, len(mtx.read_text(encoding="utf-8").splitlines()) - 1)
            if edges:
                _say(PASS, "%s：%d 个 POI、%d 条实测边" % (k, rows, edges))
            else:
                _say(WARN, "%s：%d 个 POI，但矩阵为空（在途时间按直线估算）" % (k, rows))
        else:
            _say(WARN, "%s：%d 个 POI，没有 travel_matrix.csv（在途时间按直线估算）" % (k, rows))


def check_demo_outputs() -> None:
    d = ROOT / "docs" / "demo_outputs"
    n = len(list(d.glob("*.json"))) if d.exists() else 0
    if n >= 7:
        _say(PASS, "断网兜底样例 %d 份（docs/demo_outputs/）" % n)
    else:
        _say(FAIL, "兜底样例只有 %d 份（应 7 份）" % n,
             "python -m tools.make_demo_outputs")


def check_web() -> None:
    idx = WEB_DIR / "index.html"
    if not idx.exists():
        _say(FAIL, "缺前端页面 web/index.html")
    else:
        _say(PASS, "前端页面存在（%.0f KB）" % (idx.stat().st_size / 1024.0))


def check_tests() -> None:
    print("\n== 跑全量测试（约 3 秒）==")
    p = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    tail = (p.stderr or p.stdout or "").strip().splitlines()
    last = tail[-1] if tail else ""
    if "OK" in last:
        _say(PASS, "测试全绿（%s）" % last)
    else:
        _say(FAIL, "测试未通过：%s" % (last or "无输出"), "先修测试再谈上线")


# ------------------------------------------------------------------ 入口

def main(argv: list[str]) -> int:
    global _fails, _warns
    offline = "--offline" in argv

    print("== 1. 运行时 ==")
    check_python()

    print("\n== 2. 配置 ==")
    check_env()

    print("\n== 3. 大模型网关 ==")
    if offline:
        _say(WARN, "跳过（--offline）")
    else:
        check_llm_online()

    print("\n== 4. 端口 ==")
    check_port()

    print("\n== 5. 数据 ==")
    check_data()

    print("\n== 6. 兜底材料 ==")
    check_demo_outputs()
    check_web()

    if "--with-tests" in argv:
        check_tests()

    print("\n" + "-" * 52)
    if _fails:
        print("结论：%d 项失败、%d 项提醒 —— 先处理 [fail]" % (_fails, _warns))
        return 1
    print("结论：可以对外。%s" % ("（有 %d 项提醒，看一眼）" % _warns if _warns else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
