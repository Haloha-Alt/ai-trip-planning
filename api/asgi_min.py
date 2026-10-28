# @author: 甲
# @reviewer: 乙
# @since: 2026-10-28
"""极简 ASGI 兜底：只依赖标准库。

**正常情况用不到它** —— `api/server.py` 会优先用 starlette。
它的存在只有一个理由：万一答辩机器上只有裸 Python，接口和页面仍然要能起来。

实现范围刻意做小：只支持 `api/routes.py` 里的那几条路由 + 静态文件。
不支持并发、不支持流式、不做缓存 —— 够演示就行。
"""

from __future__ import annotations

import json
import mimetypes
from pathlib import Path

from service.bootstrap import WEB_DIR
from api.routes import ROUTES, dispatch

_ROOT = Path(__file__).resolve().parent.parent


def build_min_app():
    async def app(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                msg = await receive()
                if msg["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif msg["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
            return

        if scope["type"] != "http":
            return

        method = scope.get("method", "GET").upper()
        path = scope.get("path", "/") or "/"
        query = _parse_query(scope.get("query_string", b""))

        if path.startswith("/api/"):
            body = await _read_body(receive)
            status, payload = dispatch(method, path, query, body)
            await _send_json(send, status, payload)
            return

        await _send_file(send, path)

    return app


# ---------------------------------------------------------------- 小工具


def _parse_query(raw: bytes) -> dict[str, str]:
    from urllib.parse import parse_qs

    out: dict[str, str] = {}
    for k, vs in parse_qs(raw.decode("utf-8", "replace")).items():
        out[k] = vs[0] if vs else ""
    return out


async def _read_body(receive) -> dict:
    chunks = []
    while True:
        msg = await receive()
        if msg["type"] != "http.request":
            break
        chunks.append(msg.get("body", b""))
        if not msg.get("more_body"):
            break
    raw = b"".join(chunks)
    if not raw:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


async def _send_json(send, status: int, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [(b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(body)).encode())],
    })
    await send({"type": "http.response.body", "body": body})


async def _send_file(send, path: str) -> None:
    rel = path.lstrip("/") or "index.html"
    file = (WEB_DIR / rel).resolve()
    if not str(file).startswith(str(WEB_DIR.resolve())):      # 防目录穿越
        file = WEB_DIR / "index.html"
    if not file.exists() or file.is_dir():
        file = WEB_DIR / "index.html"
    if not file.exists():
        await _send_json(send, 404, {"ok": False, "error": "缺少 web/index.html"})
        return

    data = file.read_bytes()
    ctype = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
    if file.suffix in (".html", ".css", ".js", ".json", ".md", ".svg"):
        ctype += "; charset=utf-8"
    await send({
        "type": "http.response.start",
        "status": 200,
        "headers": [(b"content-type", ctype.encode()),
                    (b"content-length", str(len(data)).encode())],
    })
    await send({"type": "http.response.body", "body": data})


def serve_stdlib(app) -> int:
    """用标准库 `http.server` 跑一个 ASGI 应用的极简适配器（单线程，仅够演示）。"""
    import asyncio
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):        # 不刷屏
            pass

        def _handle(self):
            length = int(self.headers.get("content-length") or 0)
            raw = self.rfile.read(length) if length else b""
            scope = {
                "type": "http",
                "method": self.command,
                "path": self.path.split("?", 1)[0],
                "query_string": self.path.split("?", 1)[1].encode() if "?" in self.path else b"",
                "headers": [(k.lower().encode(), v.encode()) for k, v in self.headers.items()],
            }
            sent = {"status": 200, "headers": [], "body": b""}

            async def receive():
                return {"type": "http.request", "body": raw, "more_body": False}

            async def send(msg):
                if msg["type"] == "http.response.start":
                    sent["status"] = msg["status"]
                    sent["headers"] = msg.get("headers", [])
                elif msg["type"] == "http.response.body":
                    sent["body"] += msg.get("body", b"")

            fut = asyncio.run_coroutine_threadsafe(app(scope, receive, send), loop)
            fut.result(timeout=30)

            self.send_response(sent["status"])
            for k, v in sent["headers"]:
                self.send_header(k.decode(), v.decode())
            self.end_headers()
            self.wfile.write(sent["body"])

        do_GET = _handle
        do_POST = _handle

    srv = ThreadingHTTPServer(("127.0.0.1", 8000), Handler)
    print("前端： http://127.0.0.1:8000/")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


def list_paths() -> list[str]:
    return [p for _, p, _ in ROUTES]
