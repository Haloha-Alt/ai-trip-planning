# @author: 甲
# @reviewer: 乙
# @since: 2026-10-28
"""对外数据形状（Data Transfer Object）：API / 前端与引擎之间的翻译层。

为什么不直接把 PlanResponse 丢给前端
----------------------------------
PlanResponse 是**契约**，改一个字段要三方签字；而前端要的是"好用的形状"
（比如地图只要 `[{lat, lon, name}]`，不要一整个 dataclass）。
让前端直接依赖契约，会导致"前端想加一个字段"变成"改契约" —— 权力倒挂。

所以：
- 前端 / API 只认 `to_api_dict()` 的输出；
- 契约想改就改，只要同步改这里的一个函数，前端不动。
"""

from __future__ import annotations

from typing import Any

from engine.models import DayPlan, PlanResponse


def item_to_api(it) -> dict[str, Any]:
    """单站点的前端形状。"""
    return {
        "seq": it.seq,
        "poi_id": it.poi_id,
        "name": it.name,
        "category": it.category,
        "address": it.address,
        "lat": it.lat,
        "lon": it.lon,
        "arrive": it.arrive,
        "leave": it.leave,
        "stay_min": it.stay_min,
        "cost": it.cost,
        "transport": {
            "mode": it.transport.mode,
            "minutes": it.transport.minutes,
            "distance_km": it.transport.distance_km,
            "cost": it.transport.cost,
        },
        "reason": it.reason,
        "tips": it.tips,
        "flags": list(it.flags),
        "url": it.url,
    }


def day_to_api(day: DayPlan) -> dict[str, Any]:
    """一天的行程。"""
    return {
        "day_index": day.day_index,
        "date": day.date,
        "weather": day.weather,
        "theme": day.theme,
        "total_cost": day.total_cost,
        "total_stay_min": day.total_stay_min,
        "total_travel_min": day.total_travel_min,
        "total_distance_km": day.total_distance_km,
        "items": [item_to_api(it) for it in day.items],
    }


def to_api_dict(resp: PlanResponse) -> dict[str, Any]:
    """PlanResponse → 前端/API 的字典。

    顶层永远带 `contract`（契约版本）与 `meta`，方便前端判断要不要兼容。
    """
    m = resp.metrics
    return {
        "strategy": resp.strategy,
        "scenario": resp.scenario,
        "generated_at": resp.generated_at,
        "city": resp.request.city,
        "request": resp.request.to_dict(),
        "days": [day_to_api(d) for d in resp.days],
        "warnings": list(resp.warnings),
        "metrics": {
            "satisfaction": m.satisfaction,
            "cost": m.cost,
            "time": m.time,
            "distance": m.distance,
            "feasibility": m.feasibility,
            "overall": m.overall(),
        },
        "meta": dict(resp.meta),
        "realtime": dict(resp.realtime),
    }


def to_map_points(resp: PlanResponse) -> list[dict[str, Any]]:
    """地图只要这些字段 —— 前端拿它直接打点，不用理解整个行程结构。"""
    pts: list[dict[str, Any]] = []
    for d in resp.days:
        for it in d.items:
            if it.lat or it.lon:
                pts.append({
                    "day": d.day_index,
                    "seq": it.seq,
                    "name": it.name,
                    "lat": it.lat,
                    "lon": it.lon,
                    "arrive": it.arrive,
                    "category": it.category,
                })
    return pts
