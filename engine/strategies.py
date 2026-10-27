# @author: 甲
# @reviewer: 乙
# @since: 2026-10-27
"""所有规划算法的容器。

**换算法 = 写一个函数 + 往 STRATEGIES 里加一行。**
不用类继承，是因为我们的策略是纯函数（输入 PlanRequest + ScenarioConfig，输出 PlanResponse），
函数 + 字典对新手最友好，评测层也能一行遍历全部策略做对比。

新增算法的模板：

    def my_plan(req: PlanRequest, cfg: ScenarioConfig) -> PlanResponse:
        ...                                  # 你的算法
        return PlanResponse(request=req, days=[day])

    STRATEGIES["my"] = my_plan               # 注册完即可在 --strategy my 中使用
"""

from __future__ import annotations

import random
from typing import Callable

from data.loader import get_matrix_edge, load_pois
from engine.config import ScenarioConfig
from engine.constraints import expand_interests
from engine.geo import estimate_edge, to_hhmm, to_minutes
from engine.models import (
    DayPlan,
    ItineraryItem,
    PlanRequest,
    PlanResponse,
    POI,
    Transport,
)

PlannerFn = Callable[[PlanRequest, ScenarioConfig], PlanResponse]


# ---------------------------------------------------------------- 公共小工具


def _candidate_pool(req: PlanRequest, cfg: ScenarioConfig) -> tuple[list[POI], set[str]]:
    """按兴趣/排除条件筛出候选 POI。

    返回 (候选池, 展开后的兴趣标签集合)。
    一个都匹配不上时**放宽**到全部 POI —— 宁可给个不完美的结果，也不返回空行程。
    """
    pois = load_pois(cfg)
    wanted = expand_interests(req.interests, cfg)
    avoid = set(req.avoid or [])

    pool = [p for p in pois
            if (not wanted or (set(p.tags) & wanted))
            and not (set(p.tags) & avoid)]
    if not pool:
        pool = list(pois)
    return pool, wanted


def _match_score(p: POI, wanted: set[str]) -> float:
    """单个 POI 与兴趣的匹配度；没填兴趣时用数据自带的静态分。"""
    if not wanted:
        return p.score
    return len(set(p.tags) & wanted) / len(wanted) * 0.7 + p.score * 0.3


def _reason(p: POI, wanted: set[str]) -> str:
    hit = sorted(set(p.tags) & wanted)
    if hit:
        return "命中兴趣：" + "、".join(hit)
    if wanted:
        return "无兴趣命中，按质量分 %.2f 补位" % p.score
    return "按质量分 %.2f 选取" % p.score


def _fill_day(picked: list[POI], req: PlanRequest, cfg: ScenarioConfig,
              wanted: set[str]) -> DayPlan:
    """把选中的 POI 依次铺到时间轴上，产出 DayPlan。

    所有策略共用这段逻辑 —— 不同算法的区别只在"怎么挑、怎么排序"，
    铺时间轴的规则是一致的。
    """
    day = DayPlan(day_index=1)
    clock = to_minutes(req.start_time or cfg.day_start)
    limit = to_minutes(req.end_time or cfg.day_end)
    prev: POI | None = None

    for seq, p in enumerate(picked, start=1):
        if prev is None:
            edge = Transport(mode="start", minutes=0.0, distance_km=0.0, cost=0.0)
        else:
            edge = (get_matrix_edge(prev.poi_id, p.poi_id, cfg)
                    or estimate_edge(prev, p, cfg))

        arrive = clock + int(round(edge.minutes))
        leave = arrive + p.stay_min
        if leave > limit:                       # 装不下就停，越界是硬约束，不硬塞
            break

        day.items.append(ItineraryItem(
            seq=seq,
            poi_id=p.poi_id,
            name=p.name,
            lat=p.lat,
            lon=p.lon,
            arrive=to_hhmm(arrive),
            leave=to_hhmm(leave),
            stay_min=p.stay_min,
            cost=p.cost,
            transport=edge,
            reason=_reason(p, wanted),
        ))
        clock, prev = leave, p

    day.refresh_stats()
    return day


# ---------------------------------------------------------------- 策略实现


def stub_plan(req: PlanRequest, cfg: ScenarioConfig) -> PlanResponse:
    """占位策略：候选筛选 → 按匹配度排序 → 顺序铺时间轴。

    它今天就能出真结果。将来写真正的贪心 / DP / ILP 时，
    照抄这个骨架，把"排序方式"和"是否插入"换成你的逻辑即可。
    """
    pool, wanted = _candidate_pool(req, cfg)
    pool.sort(key=lambda p: (-_match_score(p, wanted), p.poi_id))
    top_n = min(req.top_n or cfg.max_poi_per_day, cfg.max_poi_per_day)
    picked = pool[:top_n]

    day = _fill_day(picked, req, cfg, wanted)
    return PlanResponse(request=req, days=[day])


def random_plan(req: PlanRequest, cfg: ScenarioConfig) -> PlanResponse:
    """评测基线：随机挑点 + 随机顺序。

    故意做得"笨"，用来在 eval/baseline.py 里衬托正式策略 —— 没有差的对照，
    就没法证明你的算法真的有用。
    """
    pool, wanted = _candidate_pool(req, cfg)
    shuffled = list(pool)
    random.shuffle(shuffled)
    top_n = min(req.top_n or cfg.max_poi_per_day, cfg.max_poi_per_day)
    picked = shuffled[:top_n]

    day = _fill_day(picked, req, cfg, wanted)
    return PlanResponse(request=req, days=[day])


# ---------------------------------------------------------------- 注册表

STRATEGIES: dict[str, PlannerFn] = {
    "stub": stub_plan,
    "random": random_plan,
    # 将来追加： "greedy": greedy_plan,  "dp": dp_plan,  "ilp": ilp_plan,
}


def available() -> list[str]:
    """列出所有已注册的策略名。"""
    return sorted(STRATEGIES)


def get(name: str) -> PlannerFn:
    """按名字取策略函数；未知名字给出可选列表，方便命令行排错。"""
    if name not in STRATEGIES:
        raise KeyError("未知策略 '%s'，可选：%s" % (name, available()))
    return STRATEGIES[name]
