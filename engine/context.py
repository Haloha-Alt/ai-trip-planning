# @author: 甲
# @reviewer: 乙
# @since: 2026-10-28
"""实时上下文的值对象：把"外界现在是什么情况"变成可传递、可落盘、可回放的数据。

为什么是值对象而不是让引擎直接去查天气
--------------------------------------
引擎一旦内部发网络请求，就同时失去三样东西：
- **可复现**：同一份输入每次结果不一样，评测没法比；
- **可回放**：出问题时没法还原"当时看到了什么"；
- **可测试**：单测要么联网，要么得 mock。

所以规矩是：**`plan()` 内部零 IO**。实时数据先在外部抓成一个 `RealtimeSnapshot`，
再通过 `ctx=` 注入引擎。这样：
- 断网时 ctx 为空，引擎行为与以前完全一致；
- 联网时把 ctx 塞进 `PlanResponse.realtime`，落盘即可回放。

本文件只 import 标准库。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class WeatherFact:
    """一次天气观测/预报。poi_id 为空表示"整个城市"。"""

    date: str = ""                    # "YYYY-MM-DD"
    poi_id: str = ""                  # 空 = 城市级
    condition: str = ""               # clear | cloudy | rain | snow | storm | unknown
    temp_c: float | None = None
    precip_mm: float | None = None
    wind_kmh: float | None = None
    source: str = ""                  # open-meteo | manual | none

    @property
    def is_bad(self) -> bool:
        return self.condition in ("rain", "snow", "storm")


@dataclass(frozen=True)
class ClosureFact:
    """某个 POI 在某天不可用。"""

    poi_id: str
    date: str = ""
    reason: str = ""                  # 周一闭馆 / 临时闭园 / 整修
    source: str = ""                  # weekday | manual | api


@dataclass(frozen=True)
class TrafficFact:
    """两点之间的实时路况（分钟）。拿不到就别填，引擎会用估算值。"""

    from_id: str
    to_id: str
    minutes: float | None = None
    source: str = ""


@dataclass(frozen=True)
class RealtimeSnapshot:
    """某一时刻抓到的全部实时信息。**整体不可变**，抓完就定格。"""

    city: str = ""
    fetched_at: str = ""
    weather: tuple[WeatherFact, ...] = ()
    closures: tuple[ClosureFact, ...] = ()
    traffic: tuple[TrafficFact, ...] = ()
    warnings: tuple[str, ...] = ()    # 抓取过程中的降级说明

    def weather_on(self, date: str) -> WeatherFact | None:
        for w in self.weather:
            if w.date == date and not w.poi_id:
                return w
        return None

    def closure_ids(self, date: str = "") -> set[str]:
        out = set()
        for c in self.closures:
            if date and c.date and c.date != date:
                continue
            out.add(c.poi_id)
        return out

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "RealtimeSnapshot":
        d = d or {}
        return RealtimeSnapshot(
            city=d.get("city", ""),
            fetched_at=d.get("fetched_at", ""),
            weather=tuple(WeatherFact(**w) for w in d.get("weather", [])),
            closures=tuple(ClosureFact(**c) for c in d.get("closures", [])),
            traffic=tuple(TrafficFact(**t) for t in d.get("traffic", [])),
            warnings=tuple(d.get("warnings", [])),
        )

    def is_empty(self) -> bool:
        return not (self.weather or self.closures or self.traffic)


@dataclass(frozen=True)
class PlanContext:
    """传给 `plan()` 的上下文。**只增不改**：加新维度就加字段给默认值。"""

    snapshot: RealtimeSnapshot | None = None
    # 雨天是否优先室内（None = 跟场景配置走）
    prefer_indoor_on_rain: bool = True
    # 是否在发现闭馆/恶劣天气时真的重排（False = 只打标记不重排）
    auto_replan: bool = True
    extra: dict[str, Any] = field(default_factory=dict)
