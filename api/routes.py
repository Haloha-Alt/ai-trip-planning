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

import json
import os
from pathlib import Path
from typing import Any, Callable

from service import exporters
from service.compare import DEFAULT_BASELINE, compare_plans
from service.dto import to_api_dict, to_map_points
from service.usecases import (
    adjust_plan,
    build_request,
    detailed_itinerary_plan,
    enhance_plan,
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
    from service.bootstrap import lan_ip

    a = amap.status()
    return _ok({
        "service": "ai-trip-planner",
        "cities": len(list_cities()),
        "strategies": options()["strategies"],
        # 只暴露"配没配/缓存多少条"，不暴露 Key 本身
        "amap": {"configured": a["configured"], "cached_points": a["cached_points"]},
        # 本机内网 IP：前端拿它拼"发给别人"的地址，省得用户去翻黑窗口
        "lan": lan_ip(),
    })


def get_options(_req: HttpRequest) -> tuple[int, dict[str, Any]]:
    """前端启动时要拉一次：城市 / 场景 / 算法 的可选项与默认值。"""
    return _ok(options())


def get_llm(_req: HttpRequest) -> tuple[int, dict[str, Any]]:
    """大模型调用面板的数据源。

    回答三个问题：**通没通**（enabled）、**怎么选的模型**（router_mode + 路由表）、
    **刚才发生了什么**（recent：任务 / 模型 / 来源 / 耗时 / 是否失败）。

    刻意不做成"日志接口"：`recent` 只在内存里留最近 20 条，进程重启即清空 ——
    面板要的是"现在"，不是审计。
    """
    from llm import router
    from llm.client import get_client

    c = get_client()
    return _ok({
        "enabled": bool(getattr(c, "enabled", False)),
        "model": str(getattr(c, "model", "") or ""),
        "router_mode": router.router_mode(),
        "roles": router.ROLE_MODELS,
        "judge": router.JUDGE_MODEL,
        "recent": router.recent(8),
    })


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

    `no_llm` 默认 **false**：置 true 时**强制规则解析 + 规则解说**，一个模型都不调 ——
    网页上的「模型开/关」对照就是靠它；同一句话开与关的差别（比如"别太赶"能不能被听懂）
    可以直接摆在一起看，也顺手证明了没有模型系统照样跑得动。

    `model` 可选：指定模型名则**绕过自动路由**、整次请求都用它；不传就自动挑。

    `detailed` 默认 **false**（老调用方 / 测试行为逐字不变）。前端默认勾选，
    置 true 时把「规则骨架 + 校区事实层 + 大模型」三层编排写进
    `plan.meta["detailed_itinerary"]`，并把入校须知写进 `meta["visitor_briefing"]`。
    只增 meta 键，不动 `days/items`，因此与 `no_llm` 组合正好构成
    「模型开 / 关」对照：站点与时刻不变，措辞层有 / 无增量。
    """
    body = req.get("body") or {}
    no_llm = bool(body.get("no_llm"))
    model = (body.get("model") or "").strip() or None
    if no_llm:
        model = None
    must_leave = (body.get("must_leave") or "").strip() or None
    try:
        r = _run_plan(body, body.get("scenario"), body.get("strategy"),
                      body.get("realtime", False), use_llm=not no_llm, model=model)
        if body.get("narrate") and body.get("detailed"):
            # v1.6.2 提速：两个增强都开时并行跑（各一次模型调用，串行要等两倍时间）
            r = enhance_plan(r, query=str(body.get("query") or ""),
                             enabled=not no_llm, model=model)
        elif body.get("narrate"):
            r = narrate_plan(r, enabled=not no_llm, model=model)
        elif body.get("detailed"):
            r = detailed_itinerary_plan(r, query=str(body.get("query") or ""),
                                        enabled=not no_llm, model=model)
    except KeyError as e:                       # 未知算法名
        return _err(str(e).strip('"'), 400)
    except Exception as e:                      # noqa: BLE001 - 接口层兜底，不能让 500 裸奔
        return _err("规划失败：%s" % e, 500)

    payload = {"plan": to_api_dict(r), "map": to_map_points(r)}
    if must_leave:                              # 反向规划：告诉用户"最晚几点到校"
        from service.extras import reverse_hint

        payload["reverse"] = reverse_hint(payload["plan"], must_leave)
    return _ok(payload)


def post_adjust(req: HttpRequest) -> tuple[int, dict[str, Any]]:
    """局部调整：同一份需求 + 一句话，只改被点名的那一站。

    body 与 `/api/plan` 一样，额外多一个 `instruction`：

        {"query": "去图书馆自习，中午食堂吃饭", "scenario": "daily",
         "strategy": "greedy", "instruction": "第2站太贵了，换个便宜的"}

    为什么"重新生成再打补丁"而不是"把整份 plan 传回来"：
    `/api/plan` 在 `realtime=false` 时是确定性的，同一份 body 必然得到同一份行程，
    所以两者等价，但请求体小得多，也不用担心前后端契约版本不一致。

    **连续调整**用 `instructions: ["删掉第3站", "第1站换成近的"]`：
    服务端按顺序重放（先 plan，再逐条打补丁）。不这么做的话，第二次调整会从
    "原始行程"重新开始，第一次的结果被静默吞掉 —— 而用户看到的提示却是"已调整"。
    """
    body = req.get("body") or {}
    raw = body.get("instructions")
    texts = [str(t).strip() for t in raw if str(t).strip()] if isinstance(raw, list) else []
    one = str(body.get("instruction") or "").strip()
    if not texts and one:
        texts = [one]
    if not texts:
        return _err("缺少 instruction（例如：第2站太贵了换一个）", 400)

    try:
        base = _run_plan(body, body.get("scenario"), body.get("strategy"),
                         body.get("realtime", False))
        if body.get("narrate"):
            base = narrate_plan(base)
        new = base
        notes: list[str] = []
        for t in texts:
            new = adjust_plan(new, t)
            n = (new.meta.get("adjust") or {}).get("note", "")
            if n:
                notes.append(n)
    except KeyError as e:
        return _err(str(e).strip('"'), 400)
    except Exception as e:                      # noqa: BLE001
        return _err("调整失败：%s" % e, 500)

    changed = new.meta.get("adjust") or {}
    return _ok({
        "plan": to_api_dict(new),
        "map": to_map_points(new),
        "before": to_api_dict(base),
        "adjust": {"instruction": texts[-1], "instructions": texts,
                   "note": "；".join(notes),
                   "changed_days": changed.get("changed_days", 0)},
    })


def _prepare(body: dict[str, Any], scenario: str | None, use_realtime: bool,
             use_llm: bool = True, model: str | None = None):
    """「造请求 → 造上下文」。顺序很重要：**先有请求（知道去哪个城市、几天），才抓天气**。

    反过来的话就成了"排完行程再问天气预报"，重排也就无从谈起。
    """
    if body.get("request"):
        req = PlanRequest.from_dict(body["request"])
    else:
        req = build_request(text=body.get("query", ""), scenario=scenario,
                            overrides=body.get("overrides") or {},
                            use_llm=use_llm, model=model)

    ctx = plan_context_for(req, enabled=bool(use_realtime))
    return req, ctx


def _run_plan(body: dict[str, Any], scenario: str | None, strategy: str | None,
              use_realtime: bool, use_llm: bool = True, model: str | None = None):
    """统一的「造请求 → 造上下文 → 规划」。三个接口都要用，抽出来避免口径漂移。"""
    req, ctx = _prepare(body, scenario, use_realtime, use_llm=use_llm, model=model)
    return plan_from_request(req, scenario=scenario, strategy=strategy, ctx=ctx)


def post_checkup(req: HttpRequest) -> tuple[int, dict[str, Any]]:
    """行程体检：把已经排好的行程交给模型挑毛病（不重新规划、不改数字）。

    body: {"plan": {...to_api_dict 的返回...}, "model": "可选，锁定模型"}

    设计上宁可少报也不许瞎报：每条问题必须带 evidence，否则在 `llm/checkup.py` 里被丢掉。
    """
    body = req.get("body") or {}
    plan = body.get("plan")
    if not isinstance(plan, dict):
        return _err("缺少 plan（把 /api/plan 返回的 plan 原样传进来）", 400)
    try:
        from llm.checkup import checkup
        from service.usecases import llm_client

        model = (body.get("model") or "").strip() or None
        out = checkup(plan, client=llm_client(model), enabled=True)
    except Exception as e:                      # noqa: BLE001
        return _err("体检失败：%s" % e, 500)
    return _ok(out)


def post_extras(req: HttpRequest) -> tuple[int, dict[str, Any]]:
    """四个附加能力共用一个端点，按 `action` 分派。

    body: {"action": "negotiate|audience|spiel|reverse", "plan": {...}, ...}
      · negotiate  → 超支时给三个可执行方案（每个带 overrides，前端拿去重排）
      · audience   → {"audience": "peer|parent"}，同一份行程两种说法
      · spiel      → 每站 60–100 字讲解词
      · reverse    → {"must_leave": "16:00"}，算最晚到校时间

    全部"数字本地算、模型只写话"，模型挂了一律回模板，前端不用判断有没有 Key。
    """
    from service import extras
    from service.usecases import llm_client

    body = req.get("body") or {}
    action = (body.get("action") or "").strip()
    plan = body.get("plan")
    if not isinstance(plan, dict):
        return _err("缺少 plan（把 /api/plan 返回的 plan 原样传进来）", 400)

    model = (body.get("model") or "").strip() or None
    client = llm_client(model)
    enabled = not bool(body.get("no_llm"))
    try:
        if action == "negotiate":
            out = extras.negotiate(plan, client=client, enabled=enabled)
        elif action == "audience":
            out = extras.audience(plan, which=(body.get("audience") or "peer"),
                                  client=client, enabled=enabled)
        elif action == "spiel":
            out = extras.spiel(plan, client=client, enabled=enabled)
        elif action == "reverse":
            out = extras.reverse_hint(plan, (body.get("must_leave") or "").strip())
        else:
            return _err("未知 action：%s" % action, 400)
    except Exception as e:                      # noqa: BLE001
        return _err("处理失败：%s" % e, 500)
    return _ok(out)


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


# ---------------------------------------------------------------- 约会模式（v1.5）

def get_date_info(req: HttpRequest) -> tuple[int, dict[str, Any]]:
    """约会模式面板初始数据：商圈、影院、场次（演示数据口径原样透传）。

    query 可带 ?city=dalian。
    """
    from service.date_mode import date_info

    city = str((req.get("query") or {}).get("city") or "dalian").strip() or "dalian"
    try:
        return _ok(date_info(city=city))
    except Exception as e:                      # noqa: BLE001
        return _err("约会信息读取失败：%s" % e, 500)


def post_date_locate(req: HttpRequest) -> tuple[int, dict[str, Any]]:
    """按坐标找最近的商圈（网页「我在这里」按钮）。

    body: {"lat": 38.87, "lon": 121.56, "city": "dalian"}
    距离按各商圈 POI 质心的直线距离估算，离线可复现，不调外部定位 API。
    """
    from service.date_mode import locate

    body = req.get("body") or {}
    try:
        lat = float(body.get("lat"))
        lon = float(body.get("lon"))
    except (TypeError, ValueError):
        return _err("缺少或非法的 lat/lon", 400)
    city = str(body.get("city") or "dalian").strip() or "dalian"
    try:
        return _ok(locate(lat, lon, city=city))
    except Exception as e:                      # noqa: BLE001
        return _err("定位失败：%s" % e, 500)


def post_date_brief(req: HttpRequest) -> tuple[int, dict[str, Any]]:
    """约会模式一站式入口：排程 + 简报。

    body 形如::

        {"query": "想约会", "start_time": "13:00", "zone": "西安路",
         "city": "dalian", "origin": "黑石礁公园", "strategy": "date",
         "movie":  {"poi": "DL205", "time": "14:30"},
         "dinner": {"poi": "星海渔港", "time": "18:00"},
         "anchors": [ ...自定义固定安排（v1.6.3，原样透传）... ],
         "no_llm": false, "model": ""}

    - movie / dinner 是面板勾选结果，服务端转成引擎锚点（硬时刻）
    - 一句话行程（query）里**写明了**的时刻/地点优先于面板勾选（v1.6.3）：
      冲突时以文本为准，冲突过程写进 plan.meta["date_query_overrides"]，
      前端如实展示"哪里被一句话改掉了"
    - 简报与行程同一次返回：模型只措辞，时刻全部来自行程事实
    """
    from llm.date_brief import briefing as date_briefing
    from service.date_mode import anchors_from_body, query_overrides
    from service.usecases import llm_client

    body = req.get("body") or {}
    no_llm = bool(body.get("no_llm"))
    model = (body.get("model") or "").strip() or None
    zone = str(body.get("zone") or "").strip()
    city = str(body.get("city") or "").strip()

    overrides: dict[str, Any] = dict(body.get("overrides") or {})
    if city:
        overrides["city"] = city
    if zone:
        overrides["zone"] = zone
    if (body.get("start_time") or "").strip():
        overrides["start_time"] = body["start_time"].strip()
    if (body.get("origin") or "").strip():
        overrides["origin"] = body["origin"].strip()
    if (body.get("date") or "").strip():
        overrides["date"] = body["date"].strip()

    # ---- 一句话优先（v1.6.3）----
    # 文本里写明的商圈 / 场次时间 / 地点覆盖面板勾选；没写的维度照旧走面板。
    city_for_parse = city or "dalian"
    qov = query_overrides(str(body.get("query") or ""), city=city_for_parse)
    conflicts: list[str] = []
    if qov.get("zone") and qov["zone"] != zone:
        if zone:
            conflicts.append("商圈以一句话为准：「%s」（面板选的是「%s」）"
                             % (qov["zone"], zone))
        overrides["zone"] = qov["zone"]
        zone = qov["zone"]

    def _merge(kind: str, label: str) -> dict | None:
        """面板勾选 ∪ 一句话：文本写了的维度覆盖面板，其余照旧。"""
        sel = body.get(kind) if isinstance(body.get(kind), dict) else {}
        poi = qov.get(kind + "_poi") or str(sel.get("poi") or "").strip()
        time = qov.get(kind + "_time") or str(sel.get("time") or "").strip()
        if not poi or not time:
            return None
        sel_time = str(sel.get("time") or "").strip()
        if qov.get(kind + "_time") and sel_time and sel_time != qov[kind + "_time"]:
            conflicts.append("%s时间以一句话为准：%s（面板选的是 %s）"
                             % (label, qov[kind + "_time"], sel_time))
        return {"poi": poi, "time": time}

    merged_movie = _merge("movie", "电影开场")
    merged_dinner = _merge("dinner", "晚餐订座")
    if merged_movie:
        body = dict(body)
        body["movie"] = merged_movie
    if merged_dinner:
        body = dict(body)
        body["dinner"] = merged_dinner

    anchors = anchors_from_body(body)
    if anchors:
        overrides["anchors"] = anchors
    if qov.get("must_visit"):
        extra = list(overrides.get("must_visit") or [])
        extra += [m for m in qov["must_visit"] if m not in extra]
        overrides["must_visit"] = extra
    # 关键：_run_plan -> _prepare 只认 body["overrides"]，
    # 这里必须把面板拼出的约束合并回去，否则全部被静默丢弃。
    body = dict(body)
    body["overrides"] = overrides

    try:
        r = _run_plan(body, body.get("scenario") or "date",
                      body.get("strategy"), body.get("realtime", False),
                      use_llm=not no_llm, model=model)
    except KeyError as e:                       # 未知算法名
        return _err(str(e).strip('"'), 400)
    except Exception as e:                      # noqa: BLE001
        return _err("约会规划失败：%s" % e, 500)

    # 冲突留痕：改了什么、为什么改，全写进 meta —— 界面与导出都能如实回放
    if conflicts or qov.get("picked"):
        r.meta["date_query_overrides"] = {
            "conflicts": conflicts,
            "picked": qov.get("picked", []),
        }

    try:
        brief = date_briefing(r, zone=zone,
                              client=llm_client(model), enabled=not no_llm)
    except Exception as e:                      # noqa: BLE001 - 简报失败不拖垮行程
        brief = {"headline": "简报生成失败（行程本身有效）：%s" % e}

    return _ok({"plan": to_api_dict(r), "map": to_map_points(r), "brief": brief})


# ---------------------------------------------------------------- 路由表

ROUTES: list[tuple[str, str, Handler]] = [
    ("GET", "/api/health", health),
    ("GET", "/api/options", get_options),
    ("GET", "/api/llm", get_llm),
    ("POST", "/api/plan", post_plan),
    ("POST", "/api/adjust", post_adjust),
    ("POST", "/api/compare", post_compare),
    ("POST", "/api/checkup", post_checkup),
    ("POST", "/api/extras", post_extras),
    ("POST", "/api/export", post_export),
    ("POST", "/api/reload", post_reload),
    ("GET", "/api/metrics", lambda req: get_metrics(req)),
    ("GET", "/api/date/info", get_date_info),
    ("POST", "/api/date/locate", post_date_locate),
    ("POST", "/api/date/brief", post_date_brief),
]


# ---------------------------------------------------------------- C5 结构化日志与指标

_LOG_PATH = Path(__file__).resolve().parent.parent / "logs" / "requests.log"
_LATENCIES: list[float] = []          # 内存滚动窗口（最近 500 次），/api/metrics 用


def _metrics_middleware(method: str, path: str, query: dict, body: dict
                        ) -> tuple[int, dict[str, Any]]:
    """包一层 dispatch：一行 JSON 日志 + 延迟采样。失败不连累业务结果。"""
    import time
    t0 = time.perf_counter()
    status, payload = 500, {"ok": False, "error": "internal"}
    try:
        status, payload = dispatch(method, path, query, body)
        return status, payload
    finally:
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
        try:
            _LATENCIES.append(elapsed_ms)
            if len(_LATENCIES) > 500:
                del _LATENCIES[:-500]
            line = json.dumps({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "method": method, "path": path,
                "status": status,
                "elapsed_ms": elapsed_ms,
            }, ensure_ascii=False)
            try:
                _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
                with open(_LOG_PATH, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                pass        # 日志写不进去（只读盘等）不影响服务
            print(line, flush=True)
        except Exception:
            pass


def get_metrics(_req: HttpRequest) -> tuple[int, dict[str, Any]]:
    """C5：延迟分位数与请求数（内存滚动窗口，重启清零）。"""
    xs = sorted(_LATENCIES)

    def _pct(p: float) -> float:
        if not xs:
            return 0.0
        i = min(len(xs) - 1, max(0, int(round(p * (len(xs) - 1)))))
        return xs[i]

    return _ok({
        "requests": len(xs),
        "p50_ms": _pct(0.50),
        "p95_ms": _pct(0.95),
        "window": 500,
        "log_path": str(_LOG_PATH),
    })


def dispatch_logged(method: str, path: str, query: dict[str, Any] | None,
                    body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
    """带日志/指标的 dispatch —— server.py 用这个，测试可以直接用 dispatch。"""
    return _metrics_middleware(method, path, query or {}, body or {})


def _gate(query: dict[str, Any]) -> tuple[int, dict[str, Any]] | None:
    """访问口令：把站点暴露到局域网/公网时挡一道（默认关闭）。

    为什么需要：本项目的后端是"拿着学校无限额度 Key 的代理"，一旦地址被别人知道，
    谁都能白嫖。默认只监听 127.0.0.1 就是为了这个；真要给别人用，就配 `ACCESS_CODE`，
    并把 `?code=xxx` 发给对方（前端会记住，只在第一次问）。

    没配 `ACCESS_CODE` 时**完全不影响本地开发**，返回 None 表示放行。
    """
    want = (os.environ.get("ACCESS_CODE") or "").strip()
    if not want:
        return None
    got = str((query or {}).get("code") or "").strip()
    if got == want:
        return None
    return 401, {"ok": False, "code_required": True, "error": "需要访问口令"}


def dispatch(method: str, path: str, query: dict[str, Any] | None,
             body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
    """按路由表分发。找不到就 404，并列出可用的路径 —— 排错时省一轮猜测。"""
    denied = _gate(query or {})
    if denied is not None:
        return denied
    for m, p, fn in ROUTES:
        if m == method.upper() and p == path:
            return fn({"method": method, "path": path,
                       "query": query or {}, "body": body or {}})
    return _err("未知接口 %s %s，可用：%s" % (method, path, [p for _, p, _ in ROUTES]), 404)
