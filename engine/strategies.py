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
from engine.geo import estimate_edge, resolve_mode, to_hhmm, to_minutes
from engine.models import (
    DayPlan,
    ItineraryItem,
    PlanRequest,
    PlanResponse,
    POI,
    Transport,
)

PlannerFn = Callable[[PlanRequest, ScenarioConfig], PlanResponse]

# random 基线的固定种子：只为让评测结果可复现，不是业务参数。
RANDOM_SEED = 20261027


# ---------------------------------------------------------------- 公共小工具


def _candidate_pool(req: PlanRequest, cfg: ScenarioConfig) -> tuple[list[POI], set[str]]:
    """按兴趣/排除条件筛出候选 POI。

    返回 (候选池, 展开后的兴趣标签集合)。
    一个都匹配不上时**放宽**到全部 POI —— 宁可给个不完美的结果，也不返回空行程。
    """
    pois = load_pois(cfg, req.city)       # city 是数据路由：不同城市读不同的 CSV
    wanted = expand_interests(req.interests, cfg)
    avoid = set(req.avoid or [])

    pool = [p for p in pois
            if (not wanted or (set(p.tags) & wanted))
            and not (set(p.tags) & avoid)]
    if not pool:
        pool = list(pois)
    return pool, wanted


def _apply_start(pool: list[POI], req: PlanRequest, cfg: ScenarioConfig) -> list[POI]:
    """把 start_poi_id 指定的点置顶。

    起点是"硬指定"，所以允许突破兴趣过滤（即使它没命中也被排在第一个）。
    填了不存在的 ID 就当没填 —— 不写 warning，避免为一个笔误扣掉可行性分。
    """
    sid = (req.start_poi_id or "").strip()
    if not sid:
        return pool
    for i, p in enumerate(pool):
        if p.poi_id == sid:
            return [p] + pool[:i] + pool[i + 1:]
    for p in load_pois(cfg, req.city):
        if p.poi_id == sid:
            return [p] + pool
    return pool


def _apply_waypoints(pool: list[POI], req: PlanRequest, cfg: ScenarioConfig) -> list[POI]:
    """把途经点按用户给的先后顺序排进去。

    语义（与 docs/场景定义.md 一致）：
    - 指定了起点（start_poi_id）时：起点 → 途经点（按顺序）→ 其余
    - 没指定起点时：途经点 → 其余
    - 途经点被兴趣过滤掉时，从全量数据里补回来 —— "路上顺便取快递"是硬要求，不是偏好
    - 一个都匹配不上就原样返回，由 constraints 报 WAYPOINT_MISS，策略层不静默兜底
    """
    wanted_seq = [str(w).strip() for w in (req.waypoints or []) if str(w).strip()]
    if not wanted_seq:
        return pool

    rest = list(pool)
    prefix: list[POI] = []
    sid = (req.start_poi_id or "").strip()
    if sid and rest and rest[0].poi_id == sid:      # _apply_start 已经把它置顶了
        prefix, rest = [rest[0]], rest[1:]

    by_id = {p.poi_id: p for p in rest}
    by_name = {p.name: p for p in rest}
    all_pois: list[POI] | None = None

    head: list[POI] = []
    head_ids: set[str] = set()
    for w in wanted_seq:
        p = by_id.get(w) or by_name.get(w)
        if p is None:
            if all_pois is None:
                all_pois = load_pois(cfg, req.city)
            p = next((x for x in all_pois if x.poi_id == w or x.name == w), None)
        if p is not None and p.poi_id not in head_ids:
            head.append(p)
            head_ids.add(p.poi_id)

    if not head:
        return pool
    return prefix + head + [p for p in rest if p.poi_id not in head_ids]


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
              wanted: set[str], day_index: int = 1) -> DayPlan:
    """把选中的 POI 依次铺到时间轴上，产出 DayPlan。

    所有策略共用这段逻辑 —— 不同算法的区别只在"怎么挑、怎么排序"，
    铺时间轴的规则是一致的。
    """
    day = DayPlan(day_index=day_index)
    clock = to_minutes(req.start_time or cfg.day_start)
    limit = to_minutes(req.end_time or cfg.day_end)
    prev: POI | None = None

    for seq, p in enumerate(picked, start=1):
        if prev is None:
            edge = Transport(mode="start", minutes=0.0, distance_km=0.0, cost=0.0)
        else:
            edge = (get_matrix_edge(prev.poi_id, p.poi_id, cfg, req.city)
                    or estimate_edge(prev, p, cfg,
                                     mode=resolve_mode(cfg, req.mobility)))

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


def _per_day_top_n(req: PlanRequest, cfg: ScenarioConfig) -> int:
    """每天放几站。

    节奏系数来自场景配置的 [pace_factor]，取不到就当 1.0（= 节奏不生效）。
    代码里不写任何业务数值 —— 系数只准写在 config/scenarios/*.toml 里。
    """
    base = int(req.top_n or cfg.max_poi_per_day)
    factor = 1.0
    if cfg.pace_factor:
        factor = float(cfg.pace_factor.get(req.pace, 1.0))
    return max(1, min(int(round(base * factor)), cfg.max_poi_per_day))


def _build_days(ranked: list[POI], req: PlanRequest, cfg: ScenarioConfig,
                wanted: set[str]) -> list[DayPlan]:
    """按天数切段，每段独立铺一次时间轴。

    切完不够就留空天 —— 空天会由 constraints.validate_day 产出 NO_CANDIDATE 提示，
    而不是在这里静默吞掉。
    """
    n_days = max(1, int(req.days or 1))
    per_day = _per_day_top_n(req, cfg)
    ranked = _apply_start(ranked, req, cfg)      # 起点置顶必须放在排序之后
    ranked = _apply_waypoints(ranked, req, cfg)  # 途经点紧跟起点，保持用户给的先后
    return [
        _fill_day(ranked[i * per_day:(i + 1) * per_day], req, cfg, wanted, day_index=i + 1)
        for i in range(n_days)
    ]


# ---------------------------------------------------------------- 策略实现


def stub_plan(req: PlanRequest, cfg: ScenarioConfig) -> PlanResponse:
    """占位策略：候选筛选 → 按匹配度排序 → 顺序铺时间轴。

    它今天就能出真结果。将来写真正的贪心 / DP / ILP 时，
    照抄这个骨架，把"排序方式"和"是否插入"换成你的逻辑即可。
    """
    pool, wanted = _candidate_pool(req, cfg)
    pool.sort(key=lambda p: (-_match_score(p, wanted), p.poi_id))
    n_days = max(1, int(req.days or 1))
    picked = pool[: _per_day_top_n(req, cfg) * n_days]

    return PlanResponse(request=req, days=_build_days(picked, req, cfg, wanted))


def random_plan(req: PlanRequest, cfg: ScenarioConfig) -> PlanResponse:
    """评测基线：随机挑点 + 随机顺序。

    故意做得"笨"，用来在 eval/baseline.py 里衬托正式策略 —— 没有差的对照，
    就没法证明你的算法真的有用。

    注意：这里用**固定种子**，不是全局 random。否则同一份 eval/results.md
    每跑一次都不一样，既没法比较算法，也没法用 git diff 判断有没有回归。
    """
    pool, wanted = _candidate_pool(req, cfg)
    shuffled = list(pool)
    random.Random(RANDOM_SEED).shuffle(shuffled)
    n_days = max(1, int(req.days or 1))
    picked = shuffled[: _per_day_top_n(req, cfg) * n_days]

    return PlanResponse(request=req, days=_build_days(picked, req, cfg, wanted))


# ---------------------------------------------------------------- 真算法：贪心
#
# 与 stub 的区别，一句话：**stub 只挑"最想去的"，贪心挑"此刻最划算的"**。
# 后者每放一站都算一次"满意度涨多少 / 多花钱多跑路值不值"，所以不会像 stub 那样
# 在预算 60 的场景里排出 70 块钱的行程（对比页上 stub 输给随机基线就是这个原因）。


def _weights(cfg: ScenarioConfig, req: PlanRequest) -> dict[str, float]:
    """贪心的目标函数权重 = **评分函数同一套权重**。

    这是刻意的：如果"优化的"和"打分的"是两套口径，就会出现
    "算法觉得自己更好了、分数却更低"的荒唐局面。
    """
    if req.profile and cfg.weight_presets.get(req.profile):
        return cfg.weight_presets[req.profile]
    return cfg.weights or {"satisfaction": 0.5, "cost": 0.2, "time": 0.2, "distance": 0.1}


def _edge_between(prev: POI | None, p: POI, req: PlanRequest,
                  cfg: ScenarioConfig) -> Transport:
    """上一站到这一站怎么走。与 `_fill_day` 用的是同一套取边规则，否则贪心算的时间和真排出来会对不上。"""
    if prev is None:
        return Transport(mode="start", minutes=0.0, distance_km=0.0, cost=0.0)
    return (get_matrix_edge(prev.poi_id, p.poi_id, cfg, req.city)
            or estimate_edge(prev, p, cfg, mode=resolve_mode(cfg, req.mobility)))


def _mandatory_head(req: PlanRequest, cfg: ScenarioConfig) -> list[POI]:
    """起点 + 途经点：用户指定的硬顺序，必须先落位。

    从**全量数据**里找，不受兴趣过滤影响 —— "路过取个快递"是硬要求，不是偏好。
    """
    allp = load_pois(cfg, req.city)
    by_id = {p.poi_id: p for p in allp}
    by_name = {p.name: p for p in allp}

    head: list[POI] = []
    seen: set[str] = set()

    sid = (req.start_poi_id or "").strip()
    if sid:
        p = by_id.get(sid) or by_name.get(sid)
        if p is not None:
            head.append(p)
            seen.add(p.poi_id)

    for w in (req.waypoints or []):
        key = str(w).strip()
        if not key:
            continue
        p = by_id.get(key) or by_name.get(key)
        if p is not None and p.poi_id not in seen:
            head.append(p)
            seen.add(p.poi_id)
    return head


def _utility(p: POI, e: Transport, wanted: set[str], w: dict[str, float],
             day_len: int, budget: float | None, spent: float) -> float:
    """这一步的"净收益"：满意度与停留时间算正，花钱与跑路算负。

    三项都归一到 0~1 再加权，所以量纲不会互相欺负。
    距离不单独扣分：它已经通过"在途时间"进了目标，再扣一次等于重复惩罚。
    """
    sat = _match_score(p, wanted)
    cost_ratio = 0.0
    if budget:
        cost_ratio = min(1.0, (p.cost + e.cost) / max(budget - spent, 1e-6))
    return (w.get("satisfaction", 0.5) * sat
            + w.get("time", 0.2) * (p.stay_min / day_len)
            - w.get("cost", 0.2) * cost_ratio
            - w.get("time", 0.2) * (e.minutes / day_len))


def _fits(clock: int, p: POI, e: Transport, limit: int, spent: float,
          budget: float | None) -> bool:
    """硬约束：装得进时间窗、不超预算。贪心只在可行解里挑，不靠事后补救。"""
    if clock + int(round(e.minutes)) + p.stay_min > limit:
        return False
    if budget is not None and spent + p.cost + e.cost > budget + 1e-9:
        return False
    return True


def _greedy_one_day(req: PlanRequest, cfg: ScenarioConfig, wanted: set[str],
                    remaining: list[POI], forced: list[POI], per_day: int,
                    day_len: int, budget: float | None) -> list[POI]:
    """排一天：先把硬顺序（起点/途经点）落位，再逐站挑净收益最高的。

    `budget` 传 None = 今天不看预算（降级用）。
    """
    w = _weights(cfg, req)
    clock = to_minutes(req.start_time or cfg.day_start)
    limit = to_minutes(req.end_time or cfg.day_end)
    spent = 0.0
    prev: POI | None = None
    picked: list[POI] = []
    queue = list(forced)

    while True:
        if queue:
            p = queue.pop(0)
            e = _edge_between(prev, p, req, cfg)
            if _fits(clock, p, e, limit, spent, budget):
                picked.append(p)
                clock += int(round(e.minutes)) + p.stay_min
                spent += p.cost + e.cost
                prev = p
            # 装不下就跳过：不静默塞进去，也不在这里编 warning（交给约束层统一报）
            continue

        if len(picked) >= per_day or not remaining:
            break

        best: POI | None = None
        best_u = 0.0
        for p in remaining:
            e = _edge_between(prev, p, req, cfg)
            if not _fits(clock, p, e, limit, spent, budget):
                continue
            u = _utility(p, e, wanted, w, day_len, budget, spent)
            if best is None or u > best_u:
                best, best_u = p, u

        if best is None:                            # 剩下的都装不下，今天到此为止
            break

        e = _edge_between(prev, best, req, cfg)
        picked.append(best)
        remaining.remove(best)
        clock += int(round(e.minutes)) + best.stay_min
        spent += best.cost + e.cost
        prev = best

    return picked


def _greedy_days(req: PlanRequest, cfg: ScenarioConfig, wanted: set[str],
                 pool: list[POI], head: list[POI]) -> list[list[POI]]:
    """逐天贪心：每一步在"装得下"的候选里挑净收益最高的那个。

    **降级**：预算卡得太死时（比如预算 5 元，候选全都要钱），硬约束过滤会筛出空行程。
    空行程是最差的结果 —— 用户宁可看到"这一站略超预算，已提示"，
    也不要看到"今天没地方去"。所以此时放开预算重排一次，超支由约束层如时报出。
    """
    day_len = max(1, to_minutes(req.end_time or cfg.day_end)
                  - to_minutes(req.start_time or cfg.day_start))
    per_day = _per_day_top_n(req, cfg)
    budget = req.daily_budget if (req.daily_budget or 0) > 0 else None
    n_days = max(1, int(req.days or 1))

    head_ids = {p.poi_id for p in head}
    remaining = [p for p in pool if p.poi_id not in head_ids]

    days: list[list[POI]] = []
    for di in range(n_days):
        forced = list(head) if di == 0 else []      # 硬顺序只在第一天消耗
        picked = _greedy_one_day(req, cfg, wanted, list(remaining), forced,
                                 per_day, day_len, budget)

        if not picked and budget is not None:
            picked = _greedy_one_day(req, cfg, wanted, list(remaining), list(forced),
                                     per_day, day_len, None)

        for p in picked:                            # 不被跨天重复选中
            if p in remaining:
                remaining.remove(p)
        days.append(picked)
    return days


def _fill_all(days: list[list[POI]], req: PlanRequest, cfg: ScenarioConfig,
              wanted: set[str]) -> list[DayPlan]:
    """把选好的点交给与其它策略共用的铺时间轴逻辑，保证统计口径一致。"""
    return [_fill_day(picked, req, cfg, wanted, day_index=i + 1)
            for i, picked in enumerate(days)]


def greedy_plan(req: PlanRequest, cfg: ScenarioConfig) -> PlanResponse:
    """贪心：每小时刻都挑"净收益最高且装得下"的一站。

    相比 stub 多了三件事：
    1. **预算硬约束** —— 超预算的候选直接不入选，不会排完再报 BUDGET_OVER；
    2. **在途惩罚** —— 同样的满意度，近的优先，避免为了一个点横穿校区；
    3. **目标函数与评分同源** —— 优化什么就是评什么，权重取自场景配置。
    """
    pool, wanted = _candidate_pool(req, cfg)
    pool.sort(key=lambda p: (-_match_score(p, wanted), p.poi_id))   # 同分时让结果稳定
    head = _mandatory_head(req, cfg)
    return PlanResponse(request=req, days=_fill_all(
        _greedy_days(req, cfg, wanted, pool, head), req, cfg, wanted))


# ---------------------------------------------------------------- 真算法：2-opt
#
# 贪心负责"选哪些点"，2-opt 负责"按什么顺序走"。
# 选点错了只能整段重来，顺序错了翻一段就行 —— 所以两个问题分开治。


def _path_minutes(path: list[POI], req: PlanRequest, cfg: ScenarioConfig) -> float:
    """一条访问序列的总在途分钟数（不含停留）。"""
    total = 0.0
    prev: POI | None = None
    for p in path:
        total += _edge_between(prev, p, req, cfg).minutes
        prev = p
    return total


def _two_opt(path: list[POI], req: PlanRequest, cfg: ScenarioConfig) -> list[POI]:
    """经典 2-opt：反复翻转一段子路径，直到没有改进为止。

    `max_rounds` 是安全阀 —— 局部搜索必须有明确上界，否则答辩现场卡住就难看了。
    """
    best = list(path)
    if len(best) < 4:
        return best
    best_cost = _path_minutes(best, req, cfg)
    max_rounds = 20
    for _ in range(max_rounds):
        improved = False
        for i in range(len(best) - 2):
            for j in range(i + 2, len(best)):
                cand = best[:i + 1] + list(reversed(best[i + 1:j + 1])) + best[j + 1:]
                c = _path_minutes(cand, req, cfg)
                if c + 1e-9 < best_cost:
                    best, best_cost, improved = cand, c, True
        if not improved:
            break
    return best


def two_opt_plan(req: PlanRequest, cfg: ScenarioConfig) -> PlanResponse:
    """贪心选点 + 2-opt 改序：通常比纯贪心少跑一点路，代价是慢几毫秒。"""
    pool, wanted = _candidate_pool(req, cfg)
    pool.sort(key=lambda p: (-_match_score(p, wanted), p.poi_id))
    head = _mandatory_head(req, cfg)
    days = _greedy_days(req, cfg, wanted, pool, head)
    if head:                                  # 第一天的硬顺序前缀不能被翻乱
        keep = min(len(head), len(days[0]))
        days = [days[0][:keep] + _two_opt(days[0][keep:], req, cfg)] + days[1:]
    else:
        days = [_two_opt(d, req, cfg) for d in days]
    return PlanResponse(request=req, days=_fill_all(days, req, cfg, wanted))


# ---------------------------------------------------------------- 注册表

STRATEGIES: dict[str, PlannerFn] = {
    "stub": stub_plan,
    "random": random_plan,
    "greedy": greedy_plan,
    "two_opt": two_opt_plan,
}


def available() -> list[str]:
    """列出所有已注册的策略名。"""
    return sorted(STRATEGIES)


def get(name: str) -> PlannerFn:
    """按名字取策略函数；未知名字给出可选列表，方便命令行排错。"""
    if name not in STRATEGIES:
        raise KeyError("未知策略 '%s'，可选：%s" % (name, available()))
    return STRATEGIES[name]
