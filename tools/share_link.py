# @author: 甲
# @reviewer: 乙
# @since: 2026-10-31
"""分享链接生成器：把"该发哪条链接给同学"这件事从记忆里挪到命令里。

为什么要这个工具
----------------
走到内网穿透这一步，最容易犯的错不是配置错，而是**发错链接**：

- 把自己浏览器地址栏的 `127.0.0.1:8000` 发给同学（在他们机器上指他们自己）
- 用了旧的内网 IP（换个网络就变）
- 忘了带 `?code=xxx`，对方打开全是"需要访问口令"
- 穿透地址抄错一个字母

这个工具做四件事：**读口令 → 拼链接 → 检查服务在不在 → 可选地真打一次接口**。
穿透用哪家都行（cpolar / frp / 花生壳 / ngrok），它只管最终那个公网域名。

跑法（项目根目录）：

    # 只生成内网链接（同一 Wi-Fi 用）
    .venv\\Scripts\\python.exe -m tools.share_link

    # 穿透之后，把公网地址给它，会一起拼上口令
    .venv\\Scripts\\python.exe -m tools.share_link https://abcd.r3.cpolar.cn

    # 顺手验证公网地址真的通（会打一次 /api/health）
    .venv\\Scripts\\python.exe -m tools.share_link https://abcd.r3.cpolar.cn --check

    # 口令不在 bat 里时手动给
    .venv\\Scripts\\python.exe -m tools.share_link https://abcd.r3.cpolar.cn --code mycode

退出码：0 = 生成的链接都可用（或未要求检查）；1 = 检查失败。
"""

from __future__ import annotations

import json
import re
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BAT = ROOT / "分享给他人.bat"


# ------------------------------------------------------------------ 纯函数（好测）

def normalize_base(raw: str) -> str:
    """把用户随手粘的地址整理成可用的 origin。

    容忍：缺协议、结尾多斜杠、带路径、带空格、带引号。
    """
    s = (raw or "").strip().strip('"').strip("'")
    if not s:
        return ""
    if "://" not in s:
        s = "https://" + s
    s = s.split("?")[0].split("#")[0].rstrip("/")
    return s


def build_url(base: str, code: str = "", path: str = "/") -> str:
    """拼出带口令的链接。口令为空就不带参数。"""
    b = normalize_base(base)
    if not b:
        return ""
    p = path if path.startswith("/") else "/" + path
    url = b + p
    if code:
        url += "?code=" + quote(code, safe="")
    return url


def parse_bat_code(path: Path) -> str:
    """从启动脚本里读 ACCESS_CODE。

    只认 `set ACCESS_CODE=xxx` 这一种写法；写成变量（含 %）或空值就当没有。
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    for line in text.splitlines():
        m = re.match(r"^\s*set\s+ACCESS_CODE\s*=\s*(.*)$", line, re.IGNORECASE)
        if not m:
            continue
        v = m.group(1).strip().strip('"').strip("'")
        if not v or "%" in v:
            return ""
        return v
    return ""


def is_listening(port: int, timeout: float = 1.5) -> bool:
    """本机 8000 有没有人在听。**这是整条链路上最容易忘的前提。**"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(("127.0.0.1", int(port)))
        return True
    except OSError:
        return False
    finally:
        s.close()


def check_health(base: str, code: str = "", timeout: float = 10.0) -> tuple[bool, str]:
    """真打一次 /api/health，确认这条路是通的。返回 (是否可用, 说明)。"""
    url = build_url(base, code, "/api/health")
    if not url:
        return False, "地址为空"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            status = r.status
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return False, "401：口令不对或没带口令（检查 --code / bat 里的 ACCESS_CODE）"
        return False, "HTTP %d" % e.code
    except urllib.error.URLError as e:
        return False, "连不上：%s（穿透没开？域名抄错了？）" % e.reason
    except TimeoutError:
        return False, "超时 %.0f 秒（隧道慢或被墙）" % timeout

    try:
        obj = json.loads(raw)
    except ValueError:
        return False, "返回的不是 JSON，可能是穿透服务的提示页：%s" % raw[:80]
    if not isinstance(obj, dict) or not obj.get("ok"):
        return False, "接口回了 ok=false：%s" % str(obj)[:80]
    return True, "HTTP %d，城市 %s 个" % (status, obj.get("cities"))


# ------------------------------------------------------------------ 命令行

def _usage() -> str:
    return (
        "用法：python -m tools.share_link [公网地址] [--code 口令] [--port 8000] [--check]\n"
        "      不给公网地址 = 只输出内网链接\n"
        "例：  python -m tools.share_link https://abcd.r3.cpolar.cn --check"
    )


def main(argv: list[str]) -> int:
    public = ""
    code = ""
    port = 8000
    want_check = "--check" in argv

    args = [a for a in argv if a != "--check"]
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--code" and i + 1 < len(args):
            code = args[i + 1]
            i += 2
            continue
        if a == "--port" and i + 1 < len(args):
            try:
                port = int(args[i + 1])
            except ValueError:
                print("--port 需要一个数字")
                return 1
            i += 2
            continue
        if a in ("-h", "--help"):
            print(_usage())
            return 0
        if not a.startswith("-"):
            public = a
        i += 1

    if not code:
        code = parse_bat_code(BAT)

    print("== 本机服务 ==")
    alive = is_listening(port)
    print("  127.0.0.1:%d  %s" % (port, "[在跑]" if alive else "[没在跑] 先双击 分享给他人.bat"))
    print("  %-12s %s" % ("访问口令", code or "(没设，任何人拿到地址都能用)"))

    from service.bootstrap import lan_ip

    lan = lan_ip()
    print("\n== 可以发出去的链接 ==")
    if lan and not lan.startswith("127."):
        print("  同一 Wi-Fi：%s" % build_url("http://%s:%d" % (lan, port), code))
    else:
        print("  同一 Wi-Fi：(没取到内网 IP，可能没连网络)")
    if public:
        print("  公网：      %s" % build_url(public, code))
    else:
        print("  公网：      (还没给穿透地址。穿透起来后：python -m tools.share_link https://你的穿透域名)")

    if not want_check:
        print("\n提示：加 --check 可以真打一次 /api/health，确认打通了再发人。")
        return 0

    print("\n== 自检 ==")
    targets = []
    if lan and not lan.startswith("127."):
        targets.append(("内网", "http://%s:%d" % (lan, port)))
    if public:
        targets.append(("公网", public))
    if not targets:
        print("  没有可检查的地址")
        return 1

    ok_all = True
    for label, base in targets:
        ok, msg = check_health(base, code)
        ok_all = ok_all and ok
        print("  %-5s %-40s %s %s" % (label, normalize_base(base), "[OK]" if ok else "[失败]", msg))

    if not ok_all:
        print("\n排查顺序：服务在不在跑 -> 口令对不对 -> 隧道开没开 -> 域名抄没抄错")
        return 1
    if public:
        print("\n都通了，直接把上面那条「公网」链接发人（谁都能开，不挑网络）。")
    else:
        print("\n都通了。上面那条内网链接只能给**同一个 Wi-Fi** 下的人。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
