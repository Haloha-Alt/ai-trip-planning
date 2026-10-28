# @author: 甲
# @reviewer: 乙
# @since: 2026-10-28
"""局部调整：**只动被点名的那一站，其余不变**。

为什么不用「改请求 → 整体重排」
------------------------------
用户说"第 3 站太贵了，换一个"，如果他重排整条行程，第 1、2、4 站也会跟着变 ——
他刚看熟的计划全乱了，体验上等于"没听懂"。
所以这里的做法是：**在 PlanResponse 上打补丁**，然后只重铺受影响那天的时间轴。

补丁只有四种动作（与 `llm/prompts.py` 的 ADJUST_SYSTEM 字段一一对应）：

    drop     删掉某几站
    replace  把某站换成另一个点（换谁由 `suggest_replacement()` 给）
    add      加一站（加谁由 `suggest_addition()` 给）
    swap     两站互换顺序

三条纪律
--------
1. **纯函数**：输入输出都是值对象，不读网络、不读时间 —— 同样的补丁必然得到同样的结果。
2. **改完必须重新校验与重算分数**：否则页面上的总分还是改之前的，等于骗人。
3. **改不动就说清楚**：候选里找不到替代点时不硬凑，返回原方案 + 一条 warning。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from engine import constraints
from engine.config import ScenarioConfig, load_scenario
from engine.constraints import expand_interests
from engine.geo import resolve_mode
from engine.models import DayPlan, PlanResponse


@dataclass
class Patch:
    """最小改动补丁。字段名与提示词里的 JSON 键一致，便于模型直接产出。"""

    drop: list[str] = field(default_factory=list)          # poi_id
    add: list[str] = field(default_factory=list)           # poi_id，按顺序追加
    swap: list[list[str]] = field(default_factory=list)    # [[a, b], ...]
    replace: list[list[str]] = field(default_factory=list)  # [[old_id, new_id], ...]
    note: str = ""

    def is_empty(self) -> bool:
        return not (self.drop or self.add or self.swap or self.replace)

    @staticmethod
    def from_dict(d: dict[str, Any] | None) -> "Patch":
        d = d or {}

        def _ids(key: str) -> list[str]:
            v = d.get(key) or []
            return [str(x) for x in v if str(x).strip()]

        pairs = []
        for item in (d.get("swap") or []):
            if isinstance(item, (list, tuple)) and len(item) == 2:
                pairs.append([str(item[0]), str(item[1])])
        reps = []
        for item in (d.get("replace") or []):
            if isinstance(item, (list, tuple)) and len(item) == 2:
                reps.append([str(item[0]), str(item[1])])

        return Patch(drop=_ids("drop"), add=_ids("add"), swap=pairs,
                     replace=reps, note=str(d.get("note") or ""))


# ---------------------------------------------------------------- 候选点建议


def _current_ids(resp: PlanResponse) -> set[str]:
    return {it.poi_id for d in resp.days for it in d.items}


def _candidates(resp: PlanResponse, cfg: ScenarioConfig):
    """还没被排进去、且符合兴趣与排除条件的候选点。"""
    from engine.strategies import _candidate_pool

    pool, wanted = _candidate_pool(resp.request, cfg)
    used = _current_ids(resp)
    return [p for p in pool if p.poi_id not in used], wanted


def suggest_replacement(resp: PlanResponse, day_index: int, seq: int,
                        hint: str = "", cfg: ScenarioConfig | None = None) -> str:
    """给某一站找一个替代点，返回 poi_id（找不到返回空串）。

    :param hint: `cheap`（更省钱）/ `near`（更近）/ ``（按综合收益挑）
    排序口径与贪心同源：满意度高、停留收益大、花钱少、跑路少。
    """
    from engine.geo import haversine_km, to_minutes
    from engine.models import POI, Transport
    from engine.strategies import _edge_between, _utility, _weights

    cfg = cfg or load_scenario(resp.scenario or None)
    day = next((d for d in resp.days if d.day_index == day_index), None)
    if day is None:
        return ""
    item = next((it for it in day.items if it.seq == seq), None)
    if item is None:
        return ""

    cands, wanted = _candidates(resp, cfg)
    if not cands:
        return ""

    if hint == "cheap":
        cands.sort(key=lambda p: (p.cost, -p.score))
        cands = [p for p in cands if p.cost < item.cost] or cands

    prev = next((it for it in day.items if it.seq == seq - 1), None)
    prev_poi = None if prev is None else POI(
        poi_id=prev.poi_id, name=prev.name, category=prev.category,
        lat=prev.lat, lon=prev.lon, stay_min=prev.stay_min, cost=prev.cost)

    if hint == "near" and prev_poi is not None:
        cands.sort(key=lambda p: haversine_km(prev_poi.lat, prev_poi.lon, p.lat, p.lon))
        return cands[0].poi_id

    w = _weights(cfg, resp.request)
    day_len = max(1, to_minutes(resp.request.end_time or cfg.day_end)
                  - to_minutes(resp.request.start_time or cfg.day_start))

    best_id, best_u = "", None
    for p in cands:
        e = (_edge_between(prev_poi, p, resp.request, cfg) if prev_poi is not None
             else Transport(mode="start"))
        u = _utility(p, e, wanted, w, day_len, resp.request.daily_budget, 0.0, 0)
        if best_u is None or u > best_u:
            best_id, best_u = p.poi_id, u
    return best_id


def suggest_addition(resp: PlanResponse, cfg: ScenarioConfig | None = None,
                     hint: str = "") -> str:
    """给"再加一站"找一个点：优先离最后一站近、且装得下的。"""
    cfg = cfg or load_scenario(resp.scenario or None)
    cands, wanted = _candidates(resp, cfg)
    if not cands:
        return ""

    from engine.geo import to_minutes
    from engine.strategies import _edge_between, _utility, _weights
    from engine.models import POI

    tail = None
    for d in resp.days:
        if d.items:
            tail = d.items[-1]
    tail_poi = None if tail is None else POI(
        poi_id=tail.poi_id, name=tail.name, category=tail.category,
        lat=tail.lat, lon=tail.lon, stay_min=tail.stay_min, cost=tail.cost)

    w = _weights(cfg, resp.request)
    day_len = max(1, to_minutes(resp.request.end_time or cfg.day_end)
                  - to_minutes(resp.request.start_time or cfg.day_start))
    best_id, best_u = "", None
    for p in cands:
        e = _edge_between(tail_poi, p, resp.request, cfg)
        u = _utility(p, e, wanted, w, day_len, resp.request.daily_budget, 0.0, 0)
        if best_u is None or u > best_u:
            best_id, best_u = p.poi_id, u
    return best_id


# ---------------------------------------------------------------- 打补丁


def apply_patch(resp: PlanResponse, patch: Patch,
                cfg: ScenarioConfig | None = None,
                scenario: str | None = None) -> PlanResponse:
    """把补丁打到行程上，重铺时间轴，重新校验并重算分数。**不修改入参**。"""
    from data.loader import get_poi
    from engine.strategies import _fill_day

    cfg = cfg or load_scenario(scenario or resp.scenario or None)
    out = PlanResponse.from_dict(resp.to_dict())
    wanted = expand_interests(out.request.interests, cfg)

    if patch.is_empty():
        # 空补丁也要把"为什么没改"讲清楚，别让用户以为程序卡住了
        out.warnings.append("[调整] " + (patch.note or "没识别出要改什么，行程保持不变"))
        return out

    drops = set(patch.drop)
    repl = {a: b for a, b in patch.replace}

    changed_days = 0
    for day in out.days:
        ids = [it.poi_id for it in day.items]
        new_ids = [repl.get(i, i) for i in ids if i not in drops]

        for a, b in patch.swap:                   # 互换位置（只在该天内有效）
            if a in new_ids and b in new_ids:
                ia, ib = new_ids.index(a), new_ids.index(b)
                new_ids[ia], new_ids[ib] = new_ids[ib], new_ids[ia]

        if day is out.days[0]:                    # 新增点只往第一天加，避免越加越乱
            new_ids += [i for i in patch.add if i not in new_ids]

        if new_ids == ids:
            continue

        picked = [get_poi(i, cfg, out.request.city) for i in new_ids]
        picked = [p for p in picked if p is not None]
        fresh = _fill_day(picked, out.request, cfg, wanted, day_index=day.day_index)
        fresh.date, fresh.weather = day.date, day.weather
        day.items = fresh.items
        day.total_cost = fresh.total_cost
        day.total_stay_min = fresh.total_stay_min
        day.total_travel_min = fresh.total_travel_min
        day.total_distance_km = fresh.total_distance_km
        changed_days += 1

    if not changed_days:
        out.warnings.append("[调整] 补丁没有匹配到任何站点，行程保持不变")
        return out

    # 重新校验 + 重新打分（不重算就等于给用户看旧分数）
    out.warnings = [w for w in out.warnings if "[调整]" in w]
    for v in constraints.validate_request(out.request, cfg):
        out.warnings.append(str(v))
    for d in out.days:
        for v in constraints.validate_day(d, cfg, out.request):
            out.warnings.append(str(v))

    out.metrics = constraints.score(out, cfg)
    out.generated_at = datetime.now().isoformat(timespec="seconds")
    out.meta["adjust"] = {"note": patch.note, "changed_days": changed_days}
    if patch.note:
        out.warnings.append("[调整] " + patch.note)
    return out
