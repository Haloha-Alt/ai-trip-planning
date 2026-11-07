# @author: 甲
# @reviewer: 乙
# @since: 2026-10-28
"""ASGI 应用组装：优先用 starlette，装不上就退回纯标准库实现。

启动（项目根目录）：
    .venv\\Scripts\\python.exe -m api.server            # 自动挑服务器
    .venv\\Scripts\\python.exe -m uvicorn api.server:app --reload

为什么留两条路
--------------
答辩机器的环境不受控。starlette 装好了就用它（路由、静态文件都现成）；
万一没有，`asgi_min.py` 用标准库也能把接口和页面跑起来 —— 掉档次，但不掉链子。
"""

from __future__ import annotations

import json
from pathlib import Path

from service.bootstrap import WEB_DIR, load_dotenv
from api.routes import dispatch_logged as dispatch

ROOT = Path(__file__).resolve().parent.parent

# 先把 .env 灌进环境变量，再让别的模块去读 MAP_API_KEY / LLM_API_KEY。
# 顺序不能反：有的模块在 import 阶段就会取一次环境变量。
load_dotenv()


def build_app():
    """返回一个 ASGI 应用。"""
    try:
        from starlette.applications import Starlette
        from starlette.requests import Request
        from starlette.responses import JSONResponse, PlainTextResponse, Response
        from starlette.routing import Mount, Route
        from starlette.staticfiles import StaticFiles
    except ModuleNotFoundError:
        from api.asgi_min import build_min_app

        return build_min_app()

    async def _json_endpoint(request: Request):
        body: dict = {}
        if request.method in ("POST", "PUT", "PATCH"):
            raw = await request.body()
            if raw:
                try:
                    body = json.loads(raw.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    return JSONResponse({"ok": False, "error": "请求体不是合法 JSON"},
                                        status_code=400)
        if not isinstance(body, dict):
            body = {}
        status, payload = dispatch(request.method, request.url.path,
                                   dict(request.query_params), body)
        return JSONResponse(payload, status_code=status)

    async def _index(request: Request):
        idx = WEB_DIR / "index.html"
        if not idx.exists():
            return PlainTextResponse(
                "缺少前端页面：%s（先创建 web/index.html）" % idx, status_code=404)
        return Response(idx.read_bytes(), media_type="text/html")

    routes = [
        Route("/api/{_:path}", _json_endpoint, methods=["GET", "POST"]),
        Route("/", _index),
    ]
    if WEB_DIR.exists():
        routes.append(Mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web"))

    return Starlette(routes=routes)


app = build_app()


def _lan_ip() -> str:
    """本机内网 IP（给别人用的那个地址）。逻辑在 service/bootstrap，这里只做转发。"""
    from service.bootstrap import lan_ip

    return lan_ip()


def main() -> int:
    """直接 `python -m api.server` 启动。优先 uvicorn，没有就用标准库兜底。"""
    import os
    import sys

    try:
        import uvicorn
    except ModuleNotFoundError:
        from api.asgi_min import serve_stdlib

        print("[warn] 没有 uvicorn，用标准库服务器兜底（仅够演示，别上生产）")
        return serve_stdlib(app)

    port = 8000
    # 默认只听本机：Key 在你机器上，随便开 0.0.0.0 等于把"无限额度"挂在墙上
    host = (os.environ.get("WEB_HOST") or "127.0.0.1").strip()
    for a in sys.argv[1:]:
        if a.startswith("--port="):
            try:
                port = int(a.split("=", 1)[1])
            except ValueError:
                pass
        elif a.startswith("--host="):
            host = a.split("=", 1)[1].strip() or host

    print("前端（本机）： http://127.0.0.1:%d/" % port)
    if host in ("0.0.0.0", "::"):
        lan = _lan_ip()
        code = (os.environ.get("ACCESS_CODE") or "").strip()
        print("")
        print("[注意] 已对外开放监听 —— 同一个 Wi-Fi 下的设备才连得上")
        print("       发给别人：http://%s:%d/%s" % (lan, port, ("?code=" + code) if code else ""))
        if code:
            print("       （上面这条已经带口令，对方直接打开就行，不用再输）")
        else:
            print("[注意] 没设 ACCESS_CODE：局域网内谁都能用你的大模型额度，建议配一个")
    uvicorn.run("api.server:app", host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
