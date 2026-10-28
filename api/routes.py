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

from service import exporters
from service.compare import DEFAULT_BASELINE, compare_plans
from service.dto import to_api_dict, to_map_points
from service.usecases import (
    adjust_plan,
    build_request,
    narrate_plan,
    options,
    plan_context_for,
    plan_from_request,
    reload_data,
)
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
    from data import amap
    from data.cities import list_cities

    a = amap.status()
    return _ok({
        "service": "ai-trip-planner",
        "cities": len(list_cities()),
        "strategies": options()["strategies"],
        # 只暴露"配没配/缓存多少条"，不暴露 Key 本身
        "amap": {"configured": a["configured"], "cached_points": a["cached_points"]},
    })


def get_options(_req: HttpRequest) -> tuple[int, dict[str, Any]]:
    """前端启动时要拉一次：城市 / 场景 / 算法 的可选项与默认值。"""
    return _ok(options())


def post_plan(req: HttpRequest) -> tuple[int, dict[str, Any]]:
    """生成行程。

    body 形如：
        {"query": "想看海，预算150", "scenario": "demo", "strategy": "stub",
         "realtime": true,
         "overrides": {"city": "dalian", "top_n": 3}}

    也可以直接给结构化请求：
        {"request": {...PlanRequest...}, "scenario": "demo", "strategy": "stub"}

    `realtime` 默认 **false**：接口默认行为必须确定、可复现（也方便测试）。
    前端会显式传 `true`，因为网页上"看一眼真实天气"才是卖点。
    抓不到就降级成"没有实时数据"，绝不会因此报错。

    `narrate` 默认 **false**：解说写进 `theme/tips/meta.narrative`；
    没配 LLM Key 时用规则模板（同样确定），配了才多一次模型调用。
    """
    body = req.get("body") or {}
    try:
        r = _run_plan(body, body.get("scenario"), body.get("strategy"),
                      body.get("realtime", False))
        if body.get("narrate"):
            r = narrate_plan(r)
    except KeyError as e:                       # 未知算法名
        return _err(str(e).strip('"'), 400)
    except Exception as e:                      # noqa: BLE001 - 接口层兜底，不能让 500 裸奔
        return _err("规划失败：%s" % e, 500)

    return _ok({"plan": to_api_dict(r), "map": to_map_points(r)})


def post_adjust(req: HttpRequest) -> tuple[int, dict[str, Any]]:
    """局部调整：同一份需求 + 一句话，只改被点名的那一站。

    body 与 `/api/plan` 一样，额外多一个 `instruction`：

        {"query": "去图书馆自习，中午食堂吃饭", "scenario": "daily",
         "strategy": "greedy", "instruction": "第2站太贵了，换个便宜的"}

    为什么"重新生成再打补丁"而不是"把整份 plan 传回来"：
    `/api/plan` 在 `realtime=false` 时是确定性的，同一份 body 必然得到同一份行程，
    所以两者等价，但请求体小得多，也不用担心前后端契约版本不一致。
    """
    body = req.get("body") or {}
    text = str(body.get("instruction") or "").strip()
    if not text:
        return _err("缺少 instruction（例如：第2站太贵了换一个）", 400)

    try:
        base = _run_plan(body, body.get("scenario"), body.get("strategy"),
                         body.get("realtime", False))
        if body.get("narrate"):
            base = narrate_plan(base)
        new = adjust_plan(base, text)
    except KeyError as e:
        return _err(str(e).strip('"'), 400)
    except Exception as e:                      # noqa: BLE001
        return _err("调整失败：%s" % e, 500)

    changed = new.meta.get("adjust") or {}
    return _ok({
        "plan": to_api_dict(new),
        "map": to_map_points(new),
        "before": to_api_dict(base),
        "adjust": {"instruction": text, "note": changed.get("note", ""),
                   "changed_days": changed.get("changed_days", 0)},
    })


def _prepare(body: dict[str, Any], scenario: str | None, use_realtime: bool):
    """「造请求 → 造上下文」。顺序很重要：**先有请求（知道去哪个城市、几天），才抓天气**。

    反过来的话就成了"排完行程再问天气预报"，重排也就无从谈起。
    """
    if body.get("request"):
        req = PlanRequest.from_dict(body["request"])
    else:
        req = build_request(text=body.get("query", ""), scenario=scenario,
                            overrides=body.get("overrides") or {})

    ctx = plan_context_for(req, enabled=bool(use_realtime))
    return req, ctx


def _run_plan(body: dict[str, Any], scenario: str | None, strategy: str | None,
              use_realtime: bool):
    """统一的「造请求 → 造上下文 → 规划」。三个接口都要用，抽出来避免口径漂移。"""
    req, ctx = _prepare(body, scenario, use_realtime)
    return plan_from_request(req, scenario=scenario, strategy=strategy, ctx=ctx)


def post_export(req: HttpRequest) -> tuple[int, dict[str, Any]]:
    """把行程导出成 json / md / csv，直接返回文本，前端拿去下载。"""
    body = req.get("body") or {}
    fmt = (body.get("fmt") or "json").lower()
    try:
        r = _run_plan(body, body.get("scenario"), body.get("strategy"),
                      body.get("realtime", False))
    except Exception as e:                      # noqa: BLE001
        return _err("导出失败：%s" % e, 400)

    return _ok({"fmt": fmt, "filename": exporters.default_name(r, fmt),
                "content": exporters.export(r, fmt)})


def post_compare(req: HttpRequest) -> tuple[int, dict[str, Any]]:
    """同一份需求，多个算法各跑一遍 —— 网页「算法对比」页签用。

    body 形如：
        {"query": "去图书馆自习，别太赶",
         "strategies": ["stub", "random"],      # 不传 = 全部已注册算法
         "baseline": "random",                  # 不传 = 默认基线 random
         "scenario": "daily", "realtime": false}

    `realtime` 同样默认 false：对比要的是"算法之间的差异"，
    天气每次都变的话，测出来的差异就不全是算法造成的了。
    """
    body = req.get("body") or {}
    try:
        req_obj, ctx = _prepare(body, body.get("scenario"), body.get("realtime", False))
        result = compare_plans(
            req_obj,
            scenario=body.get("scenario"),
            strategies=body.get("strategies"),
            baseline=body.get("baseline") or DEFAULT_BASELINE,
            ctx=ctx,
        )
    except KeyError as e:                       # 未知算法名
        return _err(str(e).strip('"'), 400)
    except Exception as e:                      # noqa: BLE001 - 接口层兜底
        return _err("对比失败：%s" % e, 500)

    return _ok(result)


def post_reload(_req: HttpRequest) -> tuple[int, dict[str, Any]]:
    """改了 CSV / registry 之后调用，清缓存。"""
    reload_data()
    return _ok({"reloaded": True})


# ---------------------------------------------------------------- 路由表

ROUTES: list[tuple[str, str, Handler]] = [
    ("GET", "/api/health", health),
    ("GET", "/api/options", get_options),
    ("POST", "/api/plan", post_plan),
    ("POST", "/api/adjust", post_adjust),
    ("POST", "/api/compare", post_compare),
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
