# @author: 甲
# @reviewer: 乙
# @since: 2026-11-01
"""一条命令开隧道，并把"能直接发人"的链接打出来。

为什么还要包一层
----------------
`cpolar http 8000` / `cloudflared tunnel --url ...` 本身能用，但有两个麻烦：

1. **地址藏在滚动日志里**，一屏红字夹着一行 Forwarding，眼睛找起来累；
2. 拿到地址后还要手动拼 `?code=口令` —— 忘了拼，对方打开就是"需要访问口令"，来回解释三轮。

所以这里做三件事：起隧道 → 从输出里**认出域名** → 直接打印拼好口令的完整链接。

两个工具都支持（自动挑先找到的那个）：
    cpolar          E:\\cpolar\\cpolar.exe（已配 authtoken 就能直接用）
    cloudflared     winget install --id Cloudflare.cloudflared

用法：
    python -m tools.tunnel                  # 自动挑工具，端口 8000
    python -m tools.tunnel --tool cpolar
    python -m tools.tunnel --port 8001

前置：**网站必须已经在跑**（另一个窗口 `python -m api.server` 或双击 `分享给他人.bat`）。
     隧道只是"门"，门后面没人，链接发出去也是 502。

Ctrl+C 结束隧道（会连带关掉子进程，不会留后台）。
"""

from __future__ import annotations

import argparse
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: 两个免费工具的域名白名单 —— 只认这些，避免把日志里的其他 URL 当成隧道地址
DOMAIN_RE = re.compile(
    r"https?://[A-Za-z0-9][A-Za-z0-9.\-]*\."
    r"(?:cpolar\.(?:cn|top|io|vip|cc)|trycloudflare\.com)"
    r"[A-Za-z0-9./\-_?=&%]*",
    re.IGNORECASE,
)

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def clean(text: str) -> str:
    """去掉终端颜色码与回车覆盖（cpolar 的进度条会狂刷 \\r）。"""
    return ANSI_RE.sub("", (text or "").replace("\r", "\n"))


def first_url(text: str) -> str:
    """从一段输出里认出第一个隧道地址。认不出返回空串。"""
    m = DOMAIN_RE.search(clean(text))
    return m.group(0).rstrip("/") if m else ""


def find_tool(prefer: str = "") -> tuple[str, list[str]]:
    """返回 (可执行文件, 启动参数)。找不到返回 ("", [])。"""
    cands: list[tuple[str, list[str]]] = []
    if shutil.which("cpolar"):
        cands.append(("cpolar", ["http"]))
    if shutil.which("cloudflared"):
        cands.append(("cloudflared", ["tunnel", "--url"]))
    if prefer:
        cands.sort(key=lambda c: 0 if c[0] == prefer else 1)
    return cands[0] if cands else ("", [])


def port_alive(port: int, timeout: float = 1.5) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(("127.0.0.1", int(port)))
        return True
    except OSError:
        return False
    finally:
        s.close()


def read_code() -> str:
    """从启动脚本里读口令（与 tools/share_link 同一套约定）。"""
    from tools.share_link import parse_bat_code

    return parse_bat_code(ROOT / "分享给他人.bat")


def cpolar_target_port(log_dir: Path | None = None) -> int:
    """从 cpolar 日志里读出"隧道实际转发到本机哪个端口"。

    为什么需要：cpolar 的隧道可能是**在网页后台配好的**（不是命令行参数说了算）。
    2026-11-01 实测踩过：命令行跑 `cpolar http 8000`，后台那条隧道却指向 8080，
    结果域名能访问、一律 502 —— 日志里只有一行 `Failed to open private leg ...:8080`，
    不看日志根本猜不到。这个函数把那行捞出来，提前把话说清楚。

    读不到返回 0。
    """
    import os

    d = log_dir or (Path(os.path.expanduser("~")) / ".cpolar" / "logs")
    if not d.exists():
        return 0
    logs = sorted(d.rglob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    for f in logs[:3]:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        hits = re.findall(r"private leg\s+http://(?:localhost|127\.0\.0\.1):(\d+)", text)
        if hits:
            return int(hits[-1])
    return 0


def build_share(url: str, code: str) -> str:
    return url + ("?code=" + quote(code, safe="") if code else "")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="开隧道并打印可分享链接")
    ap.add_argument("--tool", default="", choices=["", "cpolar", "cloudflared"])
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--timeout", type=float, default=45.0, help="等地址最多多少秒")
    a = ap.parse_args(argv)

    if not port_alive(a.port):
        print("[fail] 127.0.0.1:%d 上没有服务。" % a.port)
        print("       先在另一个窗口跑：.venv\\Scripts\\python.exe -m api.server")
        print("       或者双击 分享给他人.bat —— 隧道只是门，门后面得有人。")
        return 1

    exe, prefix = find_tool(a.tool)
    if not exe:
        print("[fail] 没找到 cpolar 也没找到 cloudflared。")
        print("       cpolar：去 cpolar.com 装，然后 cpolar authtoken 你的token")
        print("       cloudflared：winget install --id Cloudflare.cloudflared")
        return 1

    target = "http://127.0.0.1:%d" % a.port
    cmd = [exe] + prefix + [target]

    # cpolar 的隧道可能在网页后台配好了固定目标端口，命令行参数不一定生效 —— 先核对
    if exe == "cpolar":
        cfg_port = cpolar_target_port()
        if cfg_port and cfg_port != a.port:
            print("[fail] cpolar 里那条隧道指向 %d，不是 %d。" % (cfg_port, a.port))
            print("       这样开出来域名能访问、但一律 502（日志里是 Failed to open private leg）。")
            print("       二选一：")
            print("         ① 去 cpolar 后台把隧道目标端口改成 %d，然后重开（推荐，一次就好）" % a.port)
            print("         ② 改用 %d 启动本服务：python -m api.server --port=%d" % (cfg_port, cfg_port))
            return 1

    print("启动隧道：%s" % " ".join(cmd))
    print("（等地址，最多 %.0f 秒；Ctrl+C 结束）\n" % a.timeout)

    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
    )

    code = read_code()
    url = ""
    deadline = time.time() + a.timeout
    try:
        while time.time() < deadline:
            line = proc.stdout.readline() if proc.stdout else ""
            if not line:
                if proc.poll() is not None:
                    print("\n[fail] 隧道进程退出了（上面是它的输出）。常见原因：")
                    print("       · cpolar 没登录：cpolar authtoken 你的token")
                    print("       · 端口被占 / 免费额度用满")
                    return 1
                continue
            print("  " + clean(line).strip())
            if not url:
                cand = first_url(line)
                if cand:
                    url = cand
                    print("\n" + "=" * 62)
                    print("隧道地址：%s" % url)
                    print("发给别人：%s" % build_share(url, code))
                    if not code:
                        print("[warn] 没读到 ACCESS_CODE —— 任何人都能用，建议在 bat 里设一个")
                    print("=" * 62)
                    print("\n先自检再发人：python -m tools.share_link %s --check\n" % url)
                    sys.stdout.flush()
        if not url:
            print("\n[warn] 等了 %.0f 秒没认出地址。上面日志里若有 https:// 开头的行，手动复制即可。" % a.timeout)
    except KeyboardInterrupt:
        print("\n结束隧道。")
    finally:
        try:
            proc.terminate()
            time.sleep(0.5)
            if proc.poll() is None:
                proc.kill()
        except Exception:  # noqa: BLE001 - 清理失败不该影响退出码
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
