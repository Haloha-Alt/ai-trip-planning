# @author: 助手
"""约会模式的服务层：给网页/接口提供「选商圈、选场次、定位、组锚点」的数据。

职责边界
--------
这里只回答三类"用户能说出来的问题"，不做任何排程：

    date_info()          -> 有哪些商圈 / 哪几家影院 / 各有什么场次
    locate(lat, lon)     -> 我在这，离哪个商圈最近（网页"定位"按钮用）
    anchors_from_body()  -> 把界面勾选的电影场次/晚餐订座拼成引擎锚点
    query_overrides()    -> 从一句话行程里抽出用户写明的时刻/地点（v1.6.3：
                            与面板筛选冲突时，一句话优先）

排程本身仍走 `usecases.plan_from_request`（scenario="date" + strategy="date"），
简报走 `llm.date_brief`。本模块返回的都是纯 dict —— 与引擎契约解耦，
前端不需要知道 POI dataclass 长什么样。

事实来源：`data/cities/<city>/pois_date.csv`（商圈/影院）与
`data/cities/<city>/showtimes.json`（场次）。场次是**演示数据**，这里把
`source` 字段透传出去，前端与答辩口径统一为"演示数据"。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from engine.config import load_scenario
from engine.geo import haversine_km
from engine.models import POI

DEFAULT_CITY = "dalian"
DEFAULT_SCENARIO = "date"


def _pois(city: str = DEFAULT_CITY, scenario: str = DEFAULT_SCENARIO) -> list[POI]:
    from data.loader import load_pois

    return load_pois(load_scenario(scenario), city)


def _showtimes(city: str = DEFAULT_CITY) -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1] / "data" / "cities" / city
    p = root / "showtimes.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _poi_out(p: POI) -> dict[str, Any]:
    """POI -> 前端要的最小字段集（与 service.dto.to_map_points 口径对齐）。"""
    return {
        "poi_id": p.poi_id,
        "name": p.name,
        "category": p.category,
        "lat": p.lat,
        "lon": p.lon,
        "zone": p.zone,
        "stay_min": p.stay_min,
        "cost": p.cost,
        "tags": p.tags,
    }


def date_info(city: str = DEFAULT_CITY, scenario: str = DEFAULT_SCENARIO) -> dict[str, Any]:
    """约会模式面板的初始数据：商圈、影院与场次。

    返回结构::

        {"city", "scenario", "source", "updated",
         "zones":  ["西安路", "星海"],                  # 去重保序
         "centers":     [商圈类 POI（约会中心，面板"从哪开始"默认值）],
         "restaurants": [餐饮类 POI（晚餐订座候选）],
         "cinemas": [{"poi_id", "name", "zone", "shows": [{time,title,hall,price}]}]}
    """
    pois = _pois(city, scenario)
    st = _showtimes(city)
    shows = st.get("cinemas") or {}

    zones: list[str] = []
    centers: list[dict] = []
    restaurants: list[dict] = []
    cinemas: list[dict] = []
    for p in pois:
        if p.zone and p.zone not in zones:
            zones.append(p.zone)
        if p.category == "商圈":
            centers.append(_poi_out(p))
        if p.category == "餐饮":
            restaurants.append(_poi_out(p))
        if p.category == "电影":
            cinemas.append({**_poi_out(p),
                            "shows": shows.get(p.poi_id, [])})

    return {
        "city": city,
        "scenario": scenario,
        "source": st.get("source", ""),
        "updated": st.get("updated", ""),
        "zones": zones,
        "centers": centers,
        "restaurants": restaurants,
        "cinemas": cinemas,
    }


def locate(lat: float, lon: float,
           city: str = DEFAULT_CITY, scenario: str = DEFAULT_SCENARIO) -> dict[str, Any]:
    """按坐标找最近的商圈：网页「我在这里」按钮的后端。

    距离用各商圈 POI 的**质心**算（不依赖任何外部定位 API，离线可复现）。
    返回里带 `center`（该商圈的商圈类 POI），前端直接拿它当"约会中心"默认值。
    """
    pois = _pois(city, scenario)
    by_zone: dict[str, list[POI]] = {}
    for p in pois:
        if p.zone:
            by_zone.setdefault(p.zone, []).append(p)
    if not by_zone:
        return {"city": city, "zone": "", "distance_km": None, "center": None}

    best: tuple[str, float] | None = None
    for zone, group in by_zone.items():
        c_lat = sum(x.lat for x in group) / len(group)
        c_lon = sum(x.lon for x in group) / len(group)
        d = haversine_km(float(lat), float(lon), c_lat, c_lon)
        if best is None or d < best[1]:
            best = (zone, d)

    zone, dist = best
    centers = [p for p in by_zone[zone] if p.category == "商圈"]
    return {
        "city": city,
        "zone": zone,
        "distance_km": round(dist, 2),
        "center": _poi_out(centers[0]) if centers else _poi_out(by_zone[zone][0]),
    }


def anchors_from_body(body: dict[str, Any],
                      city: str = DEFAULT_CITY,
                      scenario: str = DEFAULT_SCENARIO) -> list[dict]:
    """把面板勾选的「电影场次 / 晚餐订座」拼成引擎锚点列表。

    body 里认三种东西（都可选，凑不出就返回空表，引擎退化为贪心）：

        {"movie":  {"poi": "DL205", "time": "14:30"}}       # 电影开场
        {"dinner": {"poi": "星海渔港", "time": "18:00"}}    # 晚餐订座
        {"anchors": [ ...直接给好的锚点... ]}               # 高级用法，原样透传

    `poi` 写 poi_id 或名称都行（引擎两侧都认）。duration 不用前端填：
    按 POI 表的 stay_min 取（电影 120、晚餐 60~90），取不到回退 120。
    """
    direct = body.get("anchors")
    if isinstance(direct, (list, tuple)):
        return [dict(a) for a in direct if isinstance(a, dict)]

    from service.usecases import sanitize_anchors

    pois = _pois(city, scenario)
    stay_of: dict[str, int] = {}
    for p in pois:
        stay_of[p.poi_id] = p.stay_min or 120
        stay_of[p.name] = p.stay_min or 120

    out: list[dict] = []
    for key, label in (("movie", "电影开场"), ("dinner", "晚餐订座")):
        sel = body.get(key)
        if not isinstance(sel, dict):
            continue
        poi = str(sel.get("poi") or "").strip()
        time = str(sel.get("time") or "").strip()
        if not poi or not time:
            continue
        out.append({
            "poi": poi,
            "time": time,
            "duration_min": stay_of.get(poi, 120),
            "label": str(sel.get("label") or label),
        })
    return sanitize_anchors(out)


# ---------------------------------------------------------------- 一句话优先（v1.6.3）

_MOVIE_KW = ("电影", "场次", "看场", "影院", "影城", "imax")
_DINNER_KW = ("晚餐", "晚饭", "午餐", "午饭", "订座", "吃饭", "聚餐", "吃")
_PM_WORDS = ("中午", "午后", "下午", "傍晚", "晚上", "夜里", "今晚")
_AM_WORDS = ("凌晨", "早上", "早晨", "上午")


def _chinese_time_to_hhmm(text: str) -> list[tuple[str, int]]:
    """把「下午2点半」「晚上7点30」这类中文时刻抽成 (HH:MM, 位置)。

    为什么手写规则而不是让模型抽：这是**硬时刻**，错一分钟就是错过一场电影。
    规则可测、离线、逐字可复现 —— 模型抽出来的时间只能当参考，不能当锚点。
    """
    out: list[tuple[str, int]] = []
    for m in re.finditer(r"(凌晨|早上|早晨|上午|中午|午后|下午|傍晚|晚上|夜里|今晚)?\s*(\d{1,2})\s*点\s*(半|([0-5]?\d))?",
                         text):
        word, hour, part = m.group(1) or "", int(m.group(2)), m.group(3)
        if not (0 <= hour <= 23):
            continue
        if word in _PM_WORDS and hour < 12:
            hour += 12
        minute = 30 if part == "半" else (int(part) if part and part.isdigit() and int(part) < 60 else 0)
        out.append(("%02d:%02d" % (hour, minute), m.start()))
    return out


def query_overrides(text: str, city: str = DEFAULT_CITY,
                    scenario: str = DEFAULT_SCENARIO) -> dict[str, Any]:
    """从一句话行程里抽出与面板筛选可能冲突的信息 —— **文本说了算**。

    返回::

        {"movie_time", "movie_poi", "dinner_time", "dinner_poi",
         "zone", "must_visit": [...], "picked": [...一句里认出了什么...]}

    只认「写明确的」：写出的具体时刻、点名的地点。没写就是没说，
    绝不脑补 —— 没写的维度照旧由面板筛选决定。
    """
    text = (text or "").strip()
    out: dict[str, Any] = {"movie_time": "", "movie_poi": "", "dinner_time": "",
                           "dinner_poi": "", "zone": "", "must_visit": [],
                           "picked": []}
    if not text:
        return out

    pois = _pois(city, scenario)
    zones = sorted({p.zone for p in pois if p.zone}, key=len, reverse=True)
    for z in zones:
        if z in text:
            out["zone"] = z
            out["picked"].append("商圈「%s」" % z)
            break

    # 时间候选：显式 HH:MM 优先（它比中文时刻精确），同一段话里都有就都要
    times: list[tuple[str, int]] = [
        ("%02d:%02d" % (int(m.group(1)), int(m.group(2))), m.start())
        for m in re.finditer(r"([01]?\d|2[0-3])[:：]([0-5]\d)", text)]
    times += _chinese_time_to_hhmm(text)

    # 「看18：05到20：25的电影」这种**区间写法**：关键字往往跟在结束时刻后面，
    # 但场次/订座认的都是开始时间 —— 把区间终点映射回区间起点，别认成散场时间。
    range_end_to_start: dict[int, str] = {}
    for m in re.finditer(
            r"([01]?\d|2[0-3])[:：]([0-5]\d)\s*(?:到|至|~|～|-|—|–)\s*"
            r"([01]?\d|2[0-3])[:：]([0-5]\d)", text):
        range_end_to_start[m.start(3)] = "%02d:%02d" % (int(m.group(1)), int(m.group(2)))

    def near_kw(pos: int, kws: tuple[str, ...]) -> bool:
        lo, hi = max(0, pos - 12), min(len(text), pos + 12)
        return any(k in text[lo:hi] for k in kws)

    for t, pos in times:
        eff = range_end_to_start.get(pos, t)
        tag = "" if eff == t else "（区间起点）"
        if near_kw(pos, _MOVIE_KW) and not out["movie_time"]:
            out["movie_time"] = eff
            out["picked"].append("电影场次 %s%s" % (eff, tag))
        elif near_kw(pos, _DINNER_KW) and not out["dinner_time"]:
            out["dinner_time"] = eff
            out["picked"].append("用餐时间 %s%s" % (eff, tag))

    # 点名的地点：点位表里认得出来的名字才算数（防止"去个安静的地方"被硬安到某店）。
    # 带分店后缀的名称（"星海渔港(星海广场店)"）用括号前的短名做匹配，
    # 用户只会写"星海渔港"，不会把分店名抄全。
    for p in pois:
        short = re.split(r"[（(]", p.name)[0].strip()
        hit_name = ""
        if len(p.name) >= 3 and p.name in text:
            hit_name = p.name
        elif len(short) >= 3 and short in text:
            hit_name = short
        if not hit_name:
            continue
        if p.category == "电影" and not out["movie_poi"]:
            out["movie_poi"] = p.name
        elif p.category == "餐饮" and not out["dinner_poi"]:
            out["dinner_poi"] = p.name
        elif p.name not in out["must_visit"]:
            out["must_visit"].append(p.name)
        if ("地点「%s」" % hit_name) not in out["picked"]:
            out["picked"].append("地点「%s」" % hit_name)
    return out
