# @author: 甲
# @reviewer: 乙
# @since: 2026-10-28
"""动态重排：拿到实时数据之后，决定"要不要改行程、改哪几站"。

这里回答两个问题：

    needs_replan(resp, snap) -> list[str]     为什么需要改（空列表 = 不用改）
    apply_snapshot(resp, snap) -> PlanResponse  把实时信息写进行程（打标记 + 必要时重排）

重排的三条策略（按代价从小到大）
------------------------------
1. **打标记**：给受影响的站加 `flags`（`closed_today` / `rain_risk`），行程顺序不动。
   —— 信息透明，用户自己决定。
2. **剔除**：把当天闭馆的点从候选里去掉，重新排一次。
   —— 只动"必须动"的部分。
3. **换类型**：雨天优先室内（把"室内"当成隐性兴趣加进去再排）。
   —— 影响较大，所以只在 `prefer_indoor_on_rain=True` 时做。

**重排也是纯函数**：输入的 snapshot 是值对象，同样的 (请求, 快照) 必然得到同样的结果。
"""

from __future__ import annotations

from engine.config import ScenarioConfig, load_scenario
from engine.context import PlanContext, RealtimeSnapshot
from engine.models import DayPlan, PlanRequest, PlanResponse

# 雨天优先的隐性兴趣词：来自数据里的 tags，不是写死的业务规则
INDOOR_HINT = "室内"


def _dates_of(resp: PlanResponse, snap: RealtimeSnapshot) -> list[str]:
    """每天对应的日期：DayPlan.date 优先，其次按快照的天气日期顺序排。"""
    ds = [d.date for d in resp.days if d.date]
    if len(ds) == len(resp.days):
        return ds
    return [w.date for w in snap.weather] or [""] * len(resp.days)


def needs_replan(resp: PlanResponse, snap: RealtimeSnapshot,
                 ctx: PlanContext | None = None) -> list[str]:
    """列出"必须改"的理由。没有理由就别重排 —— 少动就是少错。"""
    if snap is None or snap.is_empty():
        return []

    reasons: list[str] = []
    dates = _dates_of(resp, snap)

    for i, day in enumerate(resp.days):
        ds = dates[i] if i < len(dates) else ""
        closed = snap.closure_ids(ds)
        hit = sorted({it.poi_id for it in day.items} & closed)
        if hit:
            reasons.append("第 %d 天有 %d 个站点当天不可用" % (day.day_index, len(hit)))

        w = snap.weather_on(ds) if ds else None
        if w is not None and w.is_bad and (ctx is None or ctx.prefer_indoor_on_rain):
            outdoor = [it.name for it in day.items
                       if "室内" not in _tags_of(resp, it.poi_id)]
            if outdoor:
                reasons.append("第 %d 天%s，%d 个室外站点受影响"
                               % (day.day_index, _cond_cn(w.condition), len(outdoor)))
    return reasons


def _cond_cn(cond: str) -> str:
    return {"rain": "有雨", "snow": "有雪", "storm": "有雷雨",
            "cloudy": "多云", "clear": "晴"}.get(cond, cond)


def _tags_of(resp: PlanResponse, poi_id: str) -> set[str]:
    """从结果里反查标签。取不到就当空集合 —— 没有信息不等于有问题。"""
    try:
        from data.loader import get_poi

        p = get_poi(poi_id, load_scenario(resp.scenario or None), resp.request.city)
        return set(p.tags) if p else set()
    except Exception:  # noqa: BLE001
        return set()


def apply_snapshot(resp: PlanResponse, snap: RealtimeSnapshot,
                   ctx: PlanContext | None = None,
                   scenario: str | None = None,
                   strategy: str | None = None) -> PlanResponse:
    """把实时信息写进行程；必要时重排一次。

    **永远不会返回 None**，最差情况就是把原样行程加上标记返回。
    """
    if snap is None or snap.is_empty():
        return resp

    ctx = ctx or PlanContext(snapshot=snap)
    dates = _dates_of(resp, snap)

    # ---- 第 1 步：把天气与风险写进 DayPlan / ItineraryItem ----
    for i, day in enumerate(resp.days):
        ds = dates[i] if i < len(dates) else ""
        if ds:
            day.date = ds
        w = snap.weather_on(ds) if ds else None
        if w is not None:
            day.weather = "%s %s%s" % (
                ds, _cond_cn(w.condition),
                "" if w.temp_c is None else " %.0f℃" % w.temp_c)

        closed = snap.closure_ids(ds)
        for it in day.items:
            if it.poi_id in closed:
                if "closed_today" not in it.flags:
                    it.flags.append("closed_today")
            elif w is not None and w.is_bad and "室内" not in _tags_of(resp, it.poi_id):
                if "rain_risk" not in it.flags:
                    it.flags.append("rain_risk")

    resp.realtime = snap.to_dict()
    resp.meta["realtime_applied"] = True

    # ---- 第 2 步：真的需要改才重排 ----
    reasons = needs_replan(resp, snap, ctx)
    if not reasons or not ctx.auto_replan:
        resp.meta["replan_reasons"] = reasons
        return resp

    req = PlanRequest.from_dict(resp.request.to_dict())

    # 剔除闭馆的：加进 avoid（软约束：排不进去也不至于空手而归）
    drop = sorted({p for ds in dates for p in snap.closure_ids(ds)})
    bad_weather = any((snap.weather_on(ds) or None) and snap.weather_on(ds).is_bad
                      for ds in dates if ds)
    if drop:
        req.avoid = list(dict.fromkeys(list(req.avoid) + drop))
    if bad_weather and ctx.prefer_indoor_on_rain and INDOOR_HINT not in req.interests:
        req.interests = list(req.interests) + [INDOOR_HINT]

    from engine.planner import plan as _plan

    try:
        new_resp = _plan(req, scenario=scenario or resp.scenario or None,
                         strategy=strategy or resp.strategy or None)
    except KeyError:
        return resp                                  # 算法名失效就维持原方案

    # 重排完再补一次标记，并把"改了什么"讲清楚
    new_resp = apply_snapshot(new_resp, snap,
                              PlanContext(snapshot=snap,
                                          prefer_indoor_on_rain=ctx.prefer_indoor_on_rain,
                                          auto_replan=False),
                              scenario=scenario, strategy=strategy)
    new_resp.meta["replanned"] = True
    new_resp.meta["replan_reasons"] = reasons
    for r in reasons:
        new_resp.warnings.append("[实时] %s，已重新规划" % r)
    return new_resp


def summary(snap: RealtimeSnapshot) -> str:
    """给人看的一行摘要（界面提示条 / 命令行都用它）。"""
    if snap is None or snap.is_empty():
        return "无实时数据"
    parts = []
    for w in snap.weather[:3]:
        parts.append("%s %s" % (w.date, _cond_cn(w.condition)))
    if snap.closures:
        parts.append("闭馆 %d 处" % len(snap.closures))
    return " ｜ ".join(parts)
