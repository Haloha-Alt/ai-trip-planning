# @author: 甲
# @reviewer: 乙
# @since: 2026-10-27
"""接口契约 v1（当前修订 v1.2）—— 全项目唯一的数据结构定义。

改字段的规矩（见 docs/接口契约.md）：
1. 谁都不许单方面改；要改先在项目消息里说，双方同意后 v1 -> v2。
2. 改完必须同步：docs/接口契约.md、schemas/itinerary.schema.json、mock/*.json。
3. 只增字段、不改语义、不改字段名；新字段必须给默认值。

v1.2 的边界（2026-10-27）：只在每个 dataclass **末尾**追加带默认值的字段，
用于承载多城市 / 实时数据 / LLM 解说 / 导出这几件事，`CONTRACT_VERSION` 因此仍为 "v1"。
`PlanMetrics` 一个字段都没动 —— 保证 AC-31 的历史评分口径可比。

本文件只允许 import 标准库，保证零依赖。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

CONTRACT_VERSION = "v1"


# ---------------------------------------------------------------- 请求输入


@dataclass
class PlanRequest:
    """引擎唯一输入。所有字段都有默认值 —— 空请求也能跑通。"""

    city: str = ""                        # 骨架不绑定城市，留空即可
    days: int = 1
    start_time: str = "09:00"             # "HH:MM"
    end_time: str = "18:00"               # "HH:MM"
    daily_budget: float | None = None     # None 或 <=0 = 不限制预算
    interests: list[str] = field(default_factory=list)   # 正向偏好
    avoid: list[str] = field(default_factory=list)       # 负向偏好
    pace: str = "normal"                  # relaxed | normal | packed
    start_poi_id: str | None = None       # None = 由引擎决定起点
    top_n: int = 6                        # 期望站点数（软目标，不是硬约束）
    raw_query: str = ""                   # 用户原始输入，留痕用，不参与计算

    # ---- v1.2 追加（全部带默认值，旧 JSON 不受影响）----
    origin: str = ""                      # 出发地名称（酒店/家/车站）；空 = 由策略决定起点
    date: str = ""                        # "YYYY-MM-DD"；空 = 今天。天气与闭馆判断用
    travelers: int = 1                    # 出行人数，只用于预算换算，不参与排程
    budget_basis: str = "per_person"      # daily_budget 的口径：per_person | group
    must_visit: list[str] = field(default_factory=list)   # 必去点（名称或 poi_id），软约束
    mobility: str = "transit"             # walk | transit | drive，影响在途时间估算
    seed: int | None = None               # 随机种子；None = 用策略内置种子

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "PlanRequest":
        """只取认识的字段，未知的忽略 —— 保证旧 JSON 不会被新字段搞崩。"""
        d = d or {}
        known = {f: d[f] for f in PlanRequest.__dataclass_fields__ if f in d}
        return PlanRequest(**known)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------- 数据层对象


@dataclass(frozen=True)
class POI:
    """兴趣点。data/pois.csv 的一行对应一个 POI。"""

    poi_id: str                           # "P001"，全局唯一主键
    name: str
    category: str                         # 分类，取值口径见 data/README.md
    lat: float                            # 纬度
    lon: float                            # 经度
    open_time: str = "00:00"              # "HH:MM"，全天填 "00:00"
    close_time: str = "23:59"             # "HH:MM"
    stay_min: int = 60                    # 建议停留分钟数
    cost: float = 0.0                     # 人均花费（门票/餐饮），0 = 免费
    tags: list[str] = field(default_factory=list)   # 兴趣标签，与 request.interests 匹配
    score: float = 0.5                    # 静态质量分 0~1，由数据给出

    # ---- v1.2 追加 ----
    city: str = ""                        # 所属城市 key；多城市数据合并时用于区分
    address: str = ""                     # 人类可读地址，UI 展示用
    source: str = "seed"                  # 数据来源：seed | amap | osm | llm
    url: str = ""                         # 详情链接，前端可点
    closed_weekdays: list[int] = field(default_factory=list)   # 每周固定闭馆日，0=周一 … 6=周日

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "POI":
        d = dict(d or {})
        tags = d.pop("tags", [])
        if isinstance(tags, str):         # CSV 里写作 "海景|拍照"
            tags = [t for t in tags.split("|") if t]
        d["tags"] = list(tags)

        raw_days = d.pop("closed_weekdays", [])
        if raw_days is None:
            raw_days = []
        elif isinstance(raw_days, str):   # CSV 里写作 "1|3"
            raw_days = [x.strip() for x in raw_days.replace("|", ",").split(",") if x.strip()]
        elif not isinstance(raw_days, (list, tuple, set)):
            raw_days = [raw_days]         # pandas 把单值读成 1.0
        d["closed_weekdays"] = [int(float(x)) for x in raw_days]

        # 契约只增字段：数据里多出来的列在旧代码里应当被忽略，而不是让整行报废。
        # （与 PlanRequest.from_dict 同一套哲学）
        known = set(POI.__dataclass_fields__)
        d = {k: v for k, v in d.items() if k in known}

        for k in ("lat", "lon", "stay_min", "cost", "score"):
            if k in d and d[k] is not None and d[k] != "":
                d[k] = float(d[k]) if k in ("lat", "lon", "cost", "score") else int(float(d[k]))
        return POI(**d)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TravelEdge:
    """两点之间的已知交通信息；data/travel_matrix.csv 的一行。缺失时由 engine/geo.py 估算。"""

    from_id: str
    to_id: str
    mode: str = "walk"
    minutes: float = 0.0
    distance_km: float = 0.0
    cost: float = 0.0


@dataclass(frozen=True)
class Transport:
    """行程项上挂的"怎么去"信息。"""

    mode: str = "start"                   # 当天第一项固定为 "start"
    minutes: float = 0.0
    distance_km: float = 0.0
    cost: float = 0.0


# ---------------------------------------------------------------- 行程输出


@dataclass
class ItineraryItem:
    """行程中的一站。"""

    seq: int                              # 当天序号，从 1 开始
    poi_id: str
    name: str
    lat: float = 0.0                      # 由引擎从 POI 复制，UI 画地图用（避免 UI 直连数据层）
    lon: float = 0.0
    arrive: str = "09:00"                 # "HH:MM"
    leave: str = "10:00"                  # "HH:MM"
    stay_min: int = 60
    cost: float = 0.0
    transport: Transport = field(default_factory=Transport)   # 从上一站到本站
    reason: str = ""                      # 可解释性输出：为什么选它 / 为什么排这时

    # ---- v1.2 追加 ----
    category: str = ""                    # 分类，UI 按类型上色用
    address: str = ""                     # 人类可读地址
    tips: str = ""                        # 解说/贴士（LLM narrate 生成，可为空）
    flags: list[str] = field(default_factory=list)   # 风险标记：rain_risk / closed_today / queue_long
    url: str = ""                         # 详情链接

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "ItineraryItem":
        d = dict(d or {})
        d["transport"] = Transport(**(d.pop("transport", None) or {}))
        return ItineraryItem(**d)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DayPlan:
    """一天的行程。"""

    day_index: int = 1                    # 从 1 开始
    items: list[ItineraryItem] = field(default_factory=list)
    total_cost: float = 0.0
    total_stay_min: int = 0
    total_travel_min: float = 0.0
    total_distance_km: float = 0.0

    # ---- v1.2 追加 ----
    date: str = ""                        # "YYYY-MM-DD"
    weather: str = ""                     # 天气摘要，人类可读
    theme: str = ""                       # 当天主题（如"老城漫步"）

    def refresh_stats(self) -> None:
        """由 items 汇总当天统计。引擎生成完必须调一次。"""
        self.total_cost = round(sum(i.cost + i.transport.cost for i in self.items), 2)
        self.total_stay_min = sum(i.stay_min for i in self.items)
        self.total_travel_min = round(sum(i.transport.minutes for i in self.items), 1)
        self.total_distance_km = round(sum(i.transport.distance_km for i in self.items), 2)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "DayPlan":
        d = dict(d or {})
        d["items"] = [ItineraryItem.from_dict(x) for x in d.pop("items", [])]
        return DayPlan(**d)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PlanMetrics:
    """评分指标，全部归一到 0~1，越大越好。"""

    satisfaction: float = 0.0             # 兴趣/标签匹配度
    cost: float = 0.0                     # 预算友好度
    time: float = 0.0                     # 时间利用率
    distance: float = 0.0                 # 路程紧凑度
    feasibility: float = 1.0              # 1.0 = 零硬约束违规；有违规按条扣分

    def overall(self, weights: dict[str, float] | None = None) -> float:
        w = weights or {"satisfaction": 0.5, "cost": 0.2, "time": 0.2, "distance": 0.1}
        vals = {
            "satisfaction": self.satisfaction,
            "cost": self.cost,
            "time": self.time,
            "distance": self.distance,
        }
        s = sum(vals[k] * w.get(k, 0.0) for k in vals)
        return round(s * self.feasibility, 4)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "PlanMetrics":
        return PlanMetrics(**(d or {}))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PlanResponse:
    """引擎唯一输出。"""

    request: PlanRequest
    days: list[DayPlan] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)   # 人类可读的降级/违规说明
    metrics: PlanMetrics = field(default_factory=PlanMetrics)
    strategy: str = ""                    # 用了哪个策略（写进结果，便于复现）
    scenario: str = ""                    # 用了哪个场景配置
    generated_at: str = ""                # ISO 时间字符串

    # ---- v1.2 追加 ----
    meta: dict[str, Any] = field(default_factory=dict)      # 扩展口袋：数据来源/耗时/降级链留痕
    realtime: dict[str, Any] = field(default_factory=dict)  # 本次使用的实时快照，便于回放

    # ---- 序列化 ----
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "PlanResponse":
        d = d or {}
        return PlanResponse(
            request=PlanRequest.from_dict(d.get("request") or {}),
            days=[DayPlan.from_dict(x) for x in d.get("days", [])],
            warnings=list(d.get("warnings", [])),
            metrics=PlanMetrics.from_dict(d.get("metrics") or {}),
            strategy=d.get("strategy", ""),
            scenario=d.get("scenario", ""),
            generated_at=d.get("generated_at", ""),
            meta=dict(d.get("meta") or {}),
            realtime=dict(d.get("realtime") or {}),
        )

    @staticmethod
    def from_json(text: str) -> "PlanResponse":
        return PlanResponse.from_dict(json.loads(text))


# ---------------------------------------------------------------- 约束违规


@dataclass(frozen=True)
class Violation:
    """一条硬约束违规。constraints.py 产出，写进 warnings，不抛异常。"""

    code: str        # BUDGET_OVER / TIME_WINDOW / CLOSED / DUPLICATE / NO_CANDIDATE
    message: str
    day_index: int | None = None
    poi_id: str | None = None

    def __str__(self) -> str:             # 直接塞进 warnings 列表
        loc = "" if self.day_index is None else "[Day%s] " % self.day_index
        return "%s%s: %s" % (loc, self.code, self.message)
