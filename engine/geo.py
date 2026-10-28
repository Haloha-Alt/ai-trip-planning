# @author: 甲
# @reviewer: 乙
# @since: 2026-10-27
"""度量换算：距离、时间、费用的估算，以及 "HH:MM" <-> 分钟 的互转。

本模块是"估算逻辑"的唯一归属地。将来要接真实路径规划 API，只改这一个文件即可。
"""

from __future__ import annotations

import math

from engine.config import ScenarioConfig
from engine.models import POI, Transport


# ---------------------------------------------------------------- 时间换算


def to_minutes(hhmm: str, default: int = 0) -> int:
    """"09:30" -> 570。解析失败时返回 default，绝不抛异常。"""
    try:
        h, m = str(hhmm).strip().split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return default


def to_hhmm(minutes: float) -> str:
    """570 -> "09:30"。超过 24 小时的部分自动折回当天。"""
    total = int(round(minutes)) % (24 * 60)
    return "%02d:%02d" % (total // 60, total % 60)


# ---------------------------------------------------------------- 距离与在途


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """两点球面距离（km）。"""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def estimate_minutes(distance_km: float, mode: str, cfg: ScenarioConfig) -> float:
    """距离 + 交通方式 -> 在途分钟数。速度来自场景配置，未知方式按 default_mode 走。"""
    speed = cfg.transport_speed_kmh.get(mode) or cfg.transport_speed_kmh.get(cfg.default_mode)
    if not speed or speed <= 0:
        speed = 4.5                        # 最后兜底：步行速度
    return distance_km / speed * 60.0


def estimate_cost(distance_km: float, mode: str, cfg: ScenarioConfig) -> float:
    """距离 + 交通方式 -> 交通费用。单价来自场景配置。"""
    unit = cfg.transport_cost_per_km.get(mode) or cfg.transport_cost_per_km.get(cfg.default_mode)
    return round(distance_km * (unit or 0.0), 2)


# 出行方式 -> 具体交通方式的候选顺序。
# 为什么是"候选顺序"：场景配置里不一定配了 metro / car，
# 缺哪个就往下找，找不到才回退 default_mode。
# 这里只有**键名**，没有任何速度/价格 —— 那些是业务数值，只准写在 config/scenarios/*.toml。
_MOBILITY_FALLBACKS: dict[str, tuple[str, ...]] = {
    "walk": ("walk",),
    "transit": ("metro", "bus"),
    "bike": ("bike",),
    "drive": ("car",),
}


def resolve_mode(cfg: ScenarioConfig, mobility: str | None = None) -> str:
    """把用户选的出行方式翻译成场景配置里确实存在的交通方式。

    "transit" 这种说法本身不是一种交通方式，它得落到 metro / bus 上
    才能查到速度。查不到就退回 cfg.default_mode —— 宁可慢一点，也不许报错。
    """
    key = (mobility or "").strip() or cfg.default_mode
    speeds = cfg.transport_speed_kmh or {}
    if key in speeds:
        return key
    for cand in _MOBILITY_FALLBACKS.get(key, ()):
        if cand in speeds:
            return cand
    return cfg.default_mode if cfg.default_mode in speeds else (
        next(iter(speeds), cfg.default_mode))


def estimate_edge(a: POI, b: POI, cfg: ScenarioConfig, mode: str | None = None) -> Transport:
    """两个 POI 之间的一段行程（距离矩阵缺项时的兜底估算）。"""
    m = mode or cfg.default_mode
    km = haversine_km(a.lat, a.lon, b.lat, b.lon)
    return Transport(
        mode=m,
        minutes=round(estimate_minutes(km, m, cfg), 1),
        distance_km=round(km, 3),
        cost=estimate_cost(km, m, cfg),
    )
