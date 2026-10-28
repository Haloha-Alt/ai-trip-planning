# @author: 甲
# @reviewer: 乙
# @since: 2026-10-28
"""实时数据抓取：**唯一允许对外发请求取数据的引擎侧模块**。

设计原则（与 llm/guard.py 同一套哲学）
--------------------------------------
1. **永不抛异常**：抓不到就返回空快照 + 一条 warning，引擎照常出结果。
2. **免 Key 优先**：天气用 Open-Meteo（免费、无需注册），这样"没申请 Key"也能演示。
3. **只出值对象**：返回 `RealtimeSnapshot`，不碰引擎内部状态。
4. **只用标准库**（urllib）。

降级链（每档失败只记 warning，绝不崩）：
    天气   Open-Meteo 预报 → 空（不猜天气，绝不用假数据糊弄）
    闭馆   POI.closed_weekdays × 出行日期  → 空
    路况   暂缺（后续接高德，接口位已留好）
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import date, timedelta

from engine.context import ClosureFact, RealtimeSnapshot, WeatherFact

OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
DEFAULT_TIMEOUT = 6.0

# Open-Meteo 的 weathercode → 我们能消费的几类
# 只分"影响出行决策"的几档，不做无谓的精细化
_CODE_MAP = {
    0: "clear", 1: "clear", 2: "cloudy", 3: "cloudy",
    45: "cloudy", 48: "cloudy",
    51: "rain", 53: "rain", 55: "rain", 56: "rain", 57: "rain",
    61: "rain", 63: "rain", 65: "rain", 66: "rain", 67: "rain",
    71: "snow", 73: "snow", 75: "snow", 77: "snow",
    80: "rain", 81: "rain", 82: "rain", 85: "snow", 86: "snow",
    95: "storm", 96: "storm", 99: "storm",
}


def _code_to_condition(code: int) -> str:
    return _CODE_MAP.get(int(code), "unknown")


def fetch_weather(lat: float, lon: float, days: int = 1,
                  start: date | None = None, timeout: float = DEFAULT_TIMEOUT,
                  warnings: list[str] | None = None) -> tuple[WeatherFact, ...]:
    """抓未来 N 天的日预报。**失败返回空元组**，不抛异常。"""
    start = start or date.today()
    if not lat or not lon:
        if warnings is not None:
            warnings.append("缺城市坐标，跳过天气")
        return ()

    url = ("%s?latitude=%.5f&longitude=%.5f&daily=weather_code,temperature_2m_max,"
           "precipitation_sum,wind_speed_10m_max&timezone=auto&start_date=%s&end_date=%s"
           % (OPEN_METEO, lat, lon, start.isoformat(),
              (start + timedelta(days=max(0, days - 1))).isoformat()))
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            raw = json.loads(r.read().decode("utf-8", "replace"))
        daily = raw.get("daily") or {}
        dates = daily.get("time") or []
        out = []
        for i, ds in enumerate(dates):
            out.append(WeatherFact(
                date=ds,
                condition=_code_to_condition((daily.get("weather_code") or [None] * len(dates))[i] or -1),
                temp_c=_nth(daily.get("temperature_2m_max"), i),
                precip_mm=_nth(daily.get("precipitation_sum"), i),
                wind_kmh=_nth(daily.get("wind_speed_10m_max"), i),
                source="open-meteo",
            ))
        return tuple(out)
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError,
            KeyError, IndexError, TimeoutError, OSError) as e:
        if warnings is not None:
            warnings.append("天气获取失败（%s），本次不按天气调整" % type(e).__name__)
        return ()


def _nth(arr, i: int):
    if not arr or i >= len(arr):
        return None
    v = arr[i]
    return None if v is None else float(v)


def closures_from_weekdays(pois, dates: list[str]) -> tuple[ClosureFact, ...]:
    """按 `POI.closed_weekdays` + 出行日期算出闭馆日（不需要联网）。

    `closed_weekdays` 用 0=周一 … 6=周日，与 Python `date.weekday()` 一致。
    """
    out: list[ClosureFact] = []
    for ds in dates:
        try:
            wd = date.fromisoformat(ds).weekday()
        except (ValueError, TypeError):
            continue
        for p in pois:
            if wd in (getattr(p, "closed_weekdays", None) or []):
                out.append(ClosureFact(poi_id=p.poi_id, date=ds,
                                       reason="每周固定闭馆", source="weekday"))
    return tuple(out)


def fetch_snapshot(city: str = "", lat: float = 0.0, lon: float = 0.0,
                   days: int = 1, start: date | None = None,
                   pois=None, timeout: float = DEFAULT_TIMEOUT) -> RealtimeSnapshot:
    """抓一份完整快照。**任何一步失败都只是少一个维度，不会整体失败。**"""
    from datetime import datetime

    warns: list[str] = []
    start = start or date.today()
    dates = [(start + timedelta(days=i)).isoformat() for i in range(max(1, days))]

    weather = fetch_weather(lat, lon, days=len(dates), start=start,
                            timeout=timeout, warnings=warns)
    closures = closures_from_weekdays(pois or [], dates)
    if not weather:
        warns.append("没有可用天气数据，行程按常规排（不会假装知道天气）")

    return RealtimeSnapshot(
        city=city,
        fetched_at=datetime.now().isoformat(timespec="seconds"),
        weather=weather,
        closures=closures,
        traffic=(),
        warnings=tuple(warns),
    )


def empty_snapshot(reason: str = "") -> RealtimeSnapshot:
    """明确表示"没有实时数据"的快照。用它而不是 None，调用方少写一层判断。"""
    from datetime import datetime

    return RealtimeSnapshot(fetched_at=datetime.now().isoformat(timespec="seconds"),
                            warnings=(reason,) if reason else ())
