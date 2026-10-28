# @author: 甲
# @reviewer: 乙
# @since: 2026-10-28
"""路由表：框架无关的处理器集合。

每个处理器的签名统一是：
    handler(req: HttpRequest) -> (status: int, payload: dict)

`HttpRequest` 是一个纯字典（`{"method","path","query","body"}`），
这样同一份业务逻辑既能挂在 starlette 上，也能挂在 `asgi_min.py` 的极简实现上。
"""

from __future__ import annotations

from typing import Any, Callable

from app import exporters
from app.dto import to_api_dict, to_map_points
from app.usecases import options, plan_from_request, plan_from_text, reload_data
from engine.models import PlanRequest

HttpRequest = dict[str, Any]
Handler = Callable[[HttpRequest], tuple[int, dict[str, Any]]]


def _ok(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    return 200, {"ok": True, **payload}


def _err(message: str, status: int = 400) -> tuple[int, dict[str, Any]]:
    return status, {"ok": False, "error": message}


# ---------------------------------------------------------------- 处理器


def health(_req: HttpRequest) -> tuple[int, dict[str, Any]]:
    """健康检查：回答"为什么跑不起来"最快的入口。"""
    from data.cities import list_cities

    return _ok({
        "service": "ai-trip-planner",
        "cities": len(list_cities()),
        "strategies": options()["strategies"],
    })


def get_options(_req: HttpRequest) -> tuple[int, dict[str, Any]]:
    """前端启动时要拉一次：城市 / 场景 / 算法 的可选项与默认值。"""
    return _ok(options())


def post_plan(req: HttpRequest) -> tuple[int, dict[str, Any]]:
    """生成行程。

    body 形如：
        {"query": "想看海，预算150", "scenario": "demo", "strategy": "stub",
         "overrides": {"city": "dalian", "top_n": 3}}

    也可以直接给结构化请求：
        {"request": {...PlanRequest...}, "scenario": "demo", "strategy": "stub"}
    """
    body = req.get("body") or {}
    scenario = body.get("scenario") or None
    strategy = body.get("strategy") or None

    try:
        if body.get("request"):
            r = plan_from_request(PlanRequest.from_dict(body["request"]),
                                  scenario=scenario, strategy=strategy)
        else:
            r = plan_from_text(body.get("query", ""), scenario=scenario,
                               strategy=strategy, overrides=body.get("overrides") or {})
    except KeyError as e:                       # 未知算法名
        return _err(str(e).strip('"'), 400)
    except Exception as e:                      # noqa: BLE001 - 接口层兜底，不能让 500 裸奔
        return _err("规划失败：%s" % e, 500)

    return _ok({"plan": to_api_dict(r), "map": to_map_points(r)})


def post_export(req: HttpRequest) -> tuple[int, dict[str, Any]]:
    """把行程导出成 json / md / csv，直接返回文本，前端拿去下载。"""
    body = req.get("body") or {}
    fmt = (body.get("fmt") or "json").lower()
    try:
        if body.get("request"):
            r = plan_from_request(PlanRequest.from_dict(body["request"]),
                                  scenario=body.get("scenario"),
                                  strategy=body.get("strategy"))
        else:
            r = plan_from_text(body.get("query", ""), scenario=body.get("scenario"),
                               strategy=body.get("strategy"),
                               overrides=body.get("overrides") or {})
    except Exception as e:                      # noqa: BLE001
        return _err("导出失败：%s" % e, 400)

    return _ok({"fmt": fmt, "filename": exporters.default_name(r, fmt),
                "content": exporters.export(r, fmt)})


def post_reload(_req: HttpRequest) -> tuple[int, dict[str, Any]]:
    """改了 CSV / registry 之后调用，清缓存。"""
    reload_data()
    return _ok({"reloaded": True})


# ---------------------------------------------------------------- 路由表

ROUTES: list[tuple[str, str, Handler]] = [
    ("GET", "/api/health", health),
    ("GET", "/api/options", get_options),
    ("POST", "/api/plan", post_plan),
    ("POST", "/api/export", post_export),
    ("POST", "/api/reload", post_reload),
]


def dispatch(method: str, path: str, query: dict[str, Any] | None,
             body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
    """按路由表分发。找不到就 404，并列出可用的路径 —— 排错时省一轮猜测。"""
    for m, p, fn in ROUTES:
        if m == method.upper() and p == path:
            return fn({"method": method, "path": path,
                       "query": query or {}, "body": body or {}})
    return _err("未知接口 %s %s，可用：%s" % (method, path, [p for _, p, _ in ROUTES]), 404)
