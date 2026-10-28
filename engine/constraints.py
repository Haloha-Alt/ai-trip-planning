# @author: 甲
# @reviewer: 乙
# @since: 2026-10-27
"""硬约束校验与评分。

铁律：**违规只返回 Violation 列表，绝不抛异常。**
引擎在任何输入下都必须返回一个结果对象；问题写进 resp.warnings 让用户看见。

违规码：BUDGET_OVER / TIME_WINDOW / CLOSED / DUPLICATE / NO_CANDIDATE / BAD_REQUEST
        ／ DEADLINE_MISS（v1.3，赶不上到达截止）／ WAYPOINT_MISS（v1.3，途经点漏排或被插队）
"""

from __future__ import annotations

from datetime import datetime

from engine.config import ScenarioConfig
from engine.models import (
    DayPlan,
    PlanMetrics,
    PlanRequest,
    PlanResponse,
    Violation,
)
from engine.geo import to_minutes

_WEEKDAY_CN = ("一", "二", "三", "四", "五", "六", "日")


def expand_interests(interests: list[str], cfg: ScenarioConfig) -> set[str]:
    """把用户输入的兴趣词展开成 POI 标签集合。

    映射关系来自场景配置的 [interest_alias]；
    没配映射的词就当作标签本身使用（这样骨架阶段不配 alias 也能跑出效果）。
    """
    out: set[str] = set()
    alias = cfg.interest_alias or {}
    for word in interests or []:
        w = str(word).strip()
        if not w:
            continue
        if w in alias:
            out.update(alias[w])
        else:
            out.add(w)
    return out


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


# ---------------------------------------------------------------- 请求校验


def validate_request(req: PlanRequest, cfg: ScenarioConfig) -> list[Violation]:
    """校验请求本身是否自洽。"""
    vs: list[Violation] = []
    s, e = to_minutes(req.start_time), to_minutes(req.end_time)

    if e <= s:
        vs.append(Violation("TIME_WINDOW", "结束时间 %s 不晚于开始时间 %s" % (req.end_time, req.start_time)))
    if req.days < 1:
        vs.append(Violation("BAD_REQUEST", "天数必须 >= 1，当前 %s" % req.days))
    if req.daily_budget is not None and req.daily_budget < 0:
        vs.append(Violation("BAD_REQUEST", "预算不能为负数，当前 %s" % req.daily_budget))
    if req.top_n < 1:
        vs.append(Violation("BAD_REQUEST", "期望站点数必须 >= 1，当前 %s" % req.top_n))
    if req.pace not in ("relaxed", "normal", "packed"):
        vs.append(Violation("BAD_REQUEST", "未知节奏 '%s'，可用 relaxed/normal/packed" % req.pace))
    if req.profile and req.profile not in (cfg.weight_presets or {}):
        vs.append(Violation("BAD_REQUEST",
                            "未知权重档位 '%s'，本场景可用：%s"
                            % (req.profile, "/".join(sorted(cfg.weight_presets or {})) or "（无）")))
    if req.deadline:
        try:                                  # to_minutes 解析失败会静默返回 0，这里单独判格式
            hh, mm = str(req.deadline).strip().split(":")
            int(hh)
            int(mm)
        except Exception:
            vs.append(Violation("BAD_REQUEST", "到达截止时刻 '%s' 不是合法的 HH:MM" % req.deadline))
    return vs


# ---------------------------------------------------------------- 行程校验


def validate_day(day: DayPlan, cfg: ScenarioConfig, req: PlanRequest | None = None) -> list[Violation]:
    """校验一天的行程是否满足硬约束。"""
    vs: list[Violation] = []
    if not day.items:
        vs.append(Violation("NO_CANDIDATE", "没有排入任何站点", day_index=day.day_index))
        return vs

    # 1) 时间窗越界
    limit = to_minutes((req.end_time if req else None) or cfg.day_end)
    first_arrive = to_minutes(day.items[0].arrive)
    if first_arrive < to_minutes((req.start_time if req else None) or cfg.day_start):
        vs.append(Violation("TIME_WINDOW", "首个站点早于出发时间", day_index=day.day_index))
    last_leave = to_minutes(day.items[-1].leave)
    if last_leave > limit:
        vs.append(Violation("TIME_WINDOW", "结束时间 %s 超出时间窗" % day.items[-1].leave,
                            day_index=day.day_index))

    # 2) 预算
    budget = (req.daily_budget if req and req.daily_budget is not None else cfg.daily_budget)
    if budget and budget > 0 and day.total_cost > budget:
        vs.append(Violation("BUDGET_OVER", "花费 %.2f 超出预算 %.2f" % (day.total_cost, budget),
                            day_index=day.day_index))

    # 3) 站点数上限
    if len(day.items) > cfg.max_poi_per_day:
        vs.append(Violation("BAD_REQUEST", "站点数 %d 超过上限 %d" % (len(day.items), cfg.max_poi_per_day),
                            day_index=day.day_index))

    # 4) 逐站校验
    weekday = None                            # 0=周一 … 6=周日；只有给了 date 才知道今天周几
    if req and req.date:
        try:
            weekday = datetime.strptime(str(req.date).strip(), "%Y-%m-%d").weekday()
        except Exception:
            weekday = None
    seen: set[str] = set()
    for it in day.items:
        if it.poi_id in seen:
            vs.append(Violation("DUPLICATE", "站点 %s 重复出现" % it.name,
                                day_index=day.day_index, poi_id=it.poi_id))
        seen.add(it.poi_id)

        poi = None
        try:
            from data.loader import get_poi

            poi = get_poi(it.poi_id, cfg, (req.city if req else ""))
        except Exception:
            poi = None
        if poi is None:
            continue
        arrive, leave = to_minutes(it.arrive), to_minutes(it.leave)
        if arrive < to_minutes(poi.open_time):
            vs.append(Violation("CLOSED", "%s 到达时未开放（%s 开门）" % (it.name, poi.open_time),
                                day_index=day.day_index, poi_id=it.poi_id))
        if leave > to_minutes(poi.close_time):
            vs.append(Violation("CLOSED", "%s 离开时已闭馆（%s 关门）" % (it.name, poi.close_time),
                                day_index=day.day_index, poi_id=it.poi_id))
        if weekday is not None and weekday in (poi.closed_weekdays or []):
            vs.append(Violation("CLOSED", "%s 当天固定闭馆（周%s）" % (it.name, _WEEKDAY_CN[weekday]),
                                day_index=day.day_index, poi_id=it.poi_id))

    # 5) 途经点：必须都排进来，且先后顺序与用户给的一致
    if req and req.waypoints:
        pos: dict[str, int] = {}
        for idx, it in enumerate(day.items):
            for w in req.waypoints:
                if it.poi_id == w or it.name == w:
                    pos.setdefault(w, idx)
        missing = [w for w in req.waypoints if w not in pos]
        if missing:
            vs.append(Violation("WAYPOINT_MISS", "途经点未排入行程：%s" % "、".join(missing),
                                day_index=day.day_index))
        placed = [pos[w] for w in req.waypoints if w in pos]
        if placed != sorted(placed):
            vs.append(Violation("WAYPOINT_MISS", "途经点顺序被插队，期望 %s"
                                % " → ".join(w for w in req.waypoints if w in pos),
                                day_index=day.day_index))

    # 6) 到达截止：有途经点看最后一个途经点（"必须先到哪"），没有就看当天末站
    if req and req.deadline:
        target = None
        if req.waypoints:
            for it in reversed(day.items):
                if any(it.poi_id == w or it.name == w for w in req.waypoints):
                    target = it
                    break
        elif day.items:
            target = day.items[-1]
        if target is not None and to_minutes(target.arrive) > to_minutes(req.deadline):
            vs.append(Violation("DEADLINE_MISS",
                                "%s 于 %s 抵达，晚于截止 %s"
                                % (target.name, target.arrive, req.deadline),
                                day_index=day.day_index, poi_id=target.poi_id))
    return vs


# ---------------------------------------------------------------- 评分


def score(resp: PlanResponse, cfg: ScenarioConfig) -> PlanMetrics:
    """按场景配置的权重算出四维指标（全部 0~1，越大越好）。

    - satisfaction：命中兴趣标签的比例（没填兴趣时退化为 POI 静态分均值）
    - cost：预算友好度（有预算看预算，没有预算就拿数据里的最大消费当标尺）
    - time：时间利用率 = 停留时间 / 可用时间窗
    - distance：路程紧凑度 = 1 - 在途时间占比
    """
    wanted = expand_interests(resp.request.interests, cfg)
    items = [it for d in resp.days for it in d.items]

    if not items:
        return PlanMetrics(satisfaction=0.0, cost=1.0, time=0.0, distance=0.0, feasibility=0.0)

    # --- satisfaction ---
    try:
        from data.loader import load_pois

        # 必须传 cfg 与 city，否则换场景/换城市后仍按 demo 的 CSV 打分
        pois = load_pois(cfg, resp.request.city)
        tag_of = {p.poi_id: set(p.tags) for p in pois}
        score_of = {p.poi_id: p.score for p in pois}
        max_cost = max([p.cost for p in pois] + [0.0]) or 1.0
    except Exception:
        tag_of, score_of, max_cost = {}, {}, 1.0

    sat_sum = 0.0
    for it in items:
        tags = tag_of.get(it.poi_id, set())
        if wanted:
            sat_sum += len(tags & wanted) / len(wanted)
        else:
            sat_sum += score_of.get(it.poi_id, 0.5)
    satisfaction = _clamp01(sat_sum / len(items))

    # --- cost ---
    total_cost = sum(d.total_cost for d in resp.days)
    budget = resp.request.daily_budget if resp.request.daily_budget is not None else cfg.daily_budget
    if budget and budget > 0:
        cost = _clamp01(1.0 - total_cost / budget)
    else:
        cost = _clamp01(1.0 - (total_cost / len(items)) / max_cost)

    # --- time ---
    window = max(1, to_minutes(resp.request.end_time or cfg.day_end)
                 - to_minutes(resp.request.start_time or cfg.day_start))
    total_stay = sum(d.total_stay_min for d in resp.days)
    time_util = _clamp01(total_stay / (window * max(1, len(resp.days))))

    # --- distance ---
    total_travel = sum(d.total_travel_min for d in resp.days)
    if total_stay + total_travel > 0:
        distance = _clamp01(1.0 - total_travel / (total_stay + total_travel))
    else:
        distance = 0.0

    # --- feasibility：每条违规扣 0.2，最低 0 ---
    n_bad = len(resp.warnings)
    feasibility = _clamp01(1.0 - 0.2 * n_bad)

    return PlanMetrics(
        satisfaction=round(satisfaction, 4),
        cost=round(cost, 4),
        time=round(time_util, 4),
        distance=round(distance, 4),
        feasibility=round(feasibility, 4),
    )
