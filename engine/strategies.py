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
from dataclasses import replace
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


def _weekday_of(date_str: str) -> int | None:
    """把 "YYYY-MM-DD" 换成 0=周一 … 6=周日；没给日期或格式不对就返回 None。"""
    key = (date_str or "").strip()
    if not key:
        return None
    try:
        from datetime import datetime

        return datetime.strptime(key, "%Y-%m-%d").weekday()
    except Exception:
        return None


def _closed_on(p: POI, weekday: int | None) -> bool:
    """这个点今天是否固定闭馆（数据里的 closed_weekdays）。"""
    return weekday is not None and weekday in (p.closed_weekdays or [])


def _closes_before(p: POI, leave_min: int) -> bool:
    """按这个时刻离开，是不是已经关门了。

    `close_time` 默认 23:59，所以绝大多数点是"永不关门"——
    真的会在 17:00 关门的食堂/场馆才需要这条。
    """
    return leave_min > to_minutes(p.close_time or "23:59")


def _origin_poi(req: PlanRequest, cfg: ScenarioConfig) -> POI | None:
    """出发地 → POI（按 poi_id 或名称匹配）。

    填了认不出来的名字就当没填 —— 与 `start_poi_id` 同一口径：
    不为一个笔误扣可行性分，也不让整条行程因为起点写错而崩。
    """
    key = (req.origin or "").strip()
    if not key:
        return None
    for p in load_pois(cfg, req.city):
        if p.poi_id == key or p.name == key:
            return p
    return None


# 单一分类在候选池里最多留几个（算法内部参数，与 _two_opt 的 max_rounds 同类）。
# 清华有 8 个图书馆，全放进池子贪心就会排出"图书馆一日游" ——
# 访客来看的是一所学校，不是同一类楼。
_CANDIDATES_PER_CATEGORY = 2


def _needs_semantic(cfg: ScenarioConfig, city: str) -> bool:
    """是否需要对 POI 做语义归一化。

    **只有高德抓来的校区数据包（`data/campuses/<key>/`）才需要。**
    它的 tags 是「科教文化服务|学校|高等院校」这类原始分类，跟用户说的
    「图书馆」「吃饭」「拍照」对不上；而项目自己维护的 `cities/dalian/pois.csv`
    标签本来就规范，走老逻辑逐字不变 —— 这是 294 个存量测试的零回归保证。
    """
    if not (getattr(cfg, "tag_aliases", None) or {}):
        return False
    if getattr(cfg, "poi_routing", "") == "campus_visit":
        return True
    if not city:
        return False
    try:
        from data.cities import poi_path

        return poi_path(city, "").startswith("campuses/")
    except Exception:                       # noqa: BLE001 - 注册表异常不该让规划崩
        return False


def _normalize_poi(p: POI, cfg: ScenarioConfig) -> POI:
    """给一个 POI 追加语义标签、按访客口径压停留上限。

    不改 CSV、不改冻结契约：用 `dataclasses.replace` 造一个新对象，
    原始 tags 全部保留，只在末尾追加命中的语义标签。

    压停留上限是"访客可执行"的关键：高德默认给图书馆 120 分钟，那是进去自习
    的口径；访客一般只能在楼前广场看外观，照抄会让一站吃掉整个上午。
    """
    text = " ".join([str(p.name or ""), str(p.category or ""),
                     "|".join(str(t) for t in (p.tags or []))])
    extra = [label for label, kws in (getattr(cfg, "tag_aliases", None) or {}).items()
             if any(str(k) in text for k in kws)]
    tags = list(p.tags or [])
    if extra:
        tags = list(dict.fromkeys(tags + extra))

    stay = p.stay_min
    caps = getattr(cfg, "visit_stay_cap", None) or {}
    cap = int(caps.get((p.category or "").strip(), 0) or 0)
    if not cap:
        cap = int(getattr(cfg, "visit_stay_default", 0) or 0)
    if cap and stay > cap:
        stay = cap

    if tags == list(p.tags or []) and stay == p.stay_min:
        return p
    return replace(p, tags=tags, stay_min=stay)


def _category_of(p: POI) -> str:
    return (p.category or "").strip() or "未分类"


def _group_of(p: POI, cfg: ScenarioConfig) -> str:
    """把高德的细分分类归并成"参观语义大类"（规则在场景配置里，不在代码里）。"""
    text = "%s %s" % (p.category or "", p.name or "")
    for group, kws in (getattr(cfg, "candidate_groups", None) or {}).items():
        if any(str(k) in text for k in kws):
            return group
    return _category_of(p)


def _diversify_and_pad(pool: list[POI], all_pois: list[POI], wanted: set[str],
                       avoid: set[str], weekday: int | None,
                       min_n: int, cfg: ScenarioConfig) -> list[POI]:
    """候选池既要够多、又要够杂。

    1. 按匹配度排序后每类最多留 3 个 —— 避免"5 站全是图书馆"；
    2. 命中点不足 `min_n` 时，按"新分类优先 + 质量分"从全量补足。
       补位点没命中兴趣，后续 `_reason` 会如实写"按质量分补位"，不假装匹配。
    """
    ranked = sorted(pool, key=lambda p: (-_match_score(p, wanted), p.poi_id))
    kept: list[POI] = []
    per_cat: dict[str, int] = {}
    for p in ranked:
        c = _group_of(p, cfg)
        if per_cat.get(c, 0) < _CANDIDATES_PER_CATEGORY:
            kept.append(p)
            per_cat[c] = per_cat.get(c, 0) + 1
    if len(kept) >= min_n:
        return kept

    ids = {p.poi_id for p in kept}
    rest = [p for p in all_pois
            if p.poi_id not in ids
            and not (set(p.tags or []) & avoid)
            and not _closed_on(p, weekday)]
    rest.sort(key=lambda p: (-p.score, p.poi_id))

    for p in rest:                          # 第一轮：只补没出现过的语义大类
        if len(kept) >= min_n:
            break
        c = _group_of(p, cfg)
        if per_cat.get(c, 0) == 0:
            kept.append(p)
            per_cat[c] = 1
            ids.add(p.poi_id)
    for p in rest:                          # 第二轮：还不够就按质量分补齐
        if len(kept) >= min_n:
            break
        if p.poi_id in ids:
            continue
        kept.append(p)
        c = _group_of(p, cfg)
        per_cat[c] = per_cat.get(c, 0) + 1
        ids.add(p.poi_id)
    return kept


def _candidate_pool(req: PlanRequest, cfg: ScenarioConfig) -> tuple[list[POI], set[str]]:
    """按兴趣/排除条件/商圈筛出候选 POI。

    返回 (候选池, 展开后的兴趣标签集合)。
    一个都匹配不上时**放宽**到全部 POI —— 宁可给个不完美的结果，也不返回空行程。
    `req.zone` 非空时只在商圈内挑（约会模式：选定中心辐射周边）；
    商圈里筛不出东西时放开过滤 —— 不因为一个写错的商圈名返回空行程。

    v1.6：`tag_aliases` 配了且当前是校区数据包时，先做语义归一化，
    再做"每类限量 + 最少补位"，保证换到任何一所高校都能排出多站且多样的行程。
    """
    pois = load_pois(cfg, req.city)       # city 是数据路由：不同城市读不同的 CSV
    semantic = _needs_semantic(cfg, req.city)
    if semantic:
        pois = [_normalize_poi(p, cfg) for p in pois]
        # 访客口径清洗：0/5 分钟的"路过点"（地铁站、宿舍楼下的窗口）
        # 不是一站，排进时间表只会出来一行"09:39–09:39"。全表都这么短时才放行。
        usable = [p for p in pois if p.stay_min >= 10]
        if usable:
            pois = usable
        bad_names = getattr(cfg, "candidate_exclude_names", None) or []
        bad_groups = set(getattr(cfg, "candidate_exclude_groups", None) or [])
        if bad_names or bad_groups:
            clean = [p for p in pois
                     if not any(k in (p.name or "") for k in bad_names)
                     and _group_of(p, cfg) not in bad_groups]
            if clean:                        # 别把整张表滤空
                pois = clean
    wanted = expand_interests(req.interests, cfg)
    avoid = set(req.avoid or [])
    weekday = _weekday_of(req.date)       # 有日期才能判断"今天闭不闭馆"

    pool = [p for p in pois
            if (not wanted or (set(p.tags) & wanted))
            and not (set(p.tags) & avoid)
            and not _closed_on(p, weekday)]
    if not pool:
        # 放宽时也要排除当天固定闭馆的点：那不是"找不到合适的"，是"今天根本不开门"
        pool = [p for p in pois if not _closed_on(p, weekday)] or list(pois)

    if semantic:
        min_n = int(getattr(cfg, "min_candidates", 0) or 0)
        if min_n > 0:
            pool = _diversify_and_pad(pool, pois, wanted, avoid, weekday, min_n, cfg)

    zone = (req.zone or "").strip()
    if zone:
        zpool = [p for p in pool if p.zone == zone]
        if zpool:
            pool = zpool
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


def _apply_must_visit(pool: list[POI], req: PlanRequest, cfg: ScenarioConfig) -> list[POI]:
    """把"必去点"提到前排（顺序不限，但必须排进来）。

    与 `waypoints` 的区别：途经点**规定先后**，必去点**只规定要有**。
    两者同时给时：起点 → 途经点（按序）→ 必去点（按给出的顺序补位）→ 其余。
    必去点被兴趣过滤掉时从全量数据里补回来；一个都对不上就原样返回，
    由 planner 统一报 MUST_VISIT_MISS（策略层不静默兜底）。
    """
    keys = [str(m).strip() for m in (req.must_visit or []) if str(m).strip()]
    if not keys:
        return pool

    allp = load_pois(cfg, req.city)
    by_id = {p.poi_id: p for p in allp}
    by_name = {p.name: p for p in allp}

    head: list[POI] = []
    ids: set[str] = set()
    for k in keys:
        p = by_id.get(k) or by_name.get(k)
        if p is not None and p.poi_id not in ids:
            head.append(p)
            ids.add(p.poi_id)
    if not head:
        return pool

    # 已经排在前面的是起点/途经点，必去点紧跟其后，不打断它们的硬顺序
    front = [(req.start_poi_id or "").strip()] + [str(w).strip() for w in (req.waypoints or [])]
    front = [f for f in front if f]
    n = 0
    while n < len(pool) and (pool[n].poi_id in front or pool[n].name in front):
        n += 1
    return pool[:n] + head + [p for p in pool[n:] if p.poi_id not in ids]


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


def _arrive_at(clock: int, p: POI, edge: Transport) -> int:
    """几点到站：到早了要**等开门**。

    不这么算就会出一个很蠢的结果：07:00 从宿舍出发，07:22 到了 07:30 才开门的图书馆，
    然后约束层判定"到了闭馆/未开门的点"，整条方案被判不可行。
    现实里你只会在门口站 8 分钟，而不是"博物馆没开门所以我不能去"。

    所有策略共用这一条，选点（`_fits`）与铺时间轴（`_fill_day`）才不会各算各的。
    """
    arrive = clock + int(round(edge.minutes))
    open_at = to_minutes(p.open_time or "00:00")
    return max(arrive, open_at)


def _fill_day(picked: list[POI], req: PlanRequest, cfg: ScenarioConfig,
              wanted: set[str], day_index: int = 1,
              start_from: POI | None = None) -> DayPlan:
    """把选中的 POI 依次铺到时间轴上，产出 DayPlan。

    所有策略共用这段逻辑 —— 不同算法的区别只在"怎么挑、怎么排序"，
    铺时间轴的规则是一致的。

    三件事以前没有，现在必须有：
    - **`start_from`**：`req.origin` 认出来是哪个 POI 时，第一段在途从它算起
      （以前不管用户说"从宿舍出发"，第一段永远是 0 分钟的 `start`）；
    - **关门就跳过**：`leave` 已经过了 `close_time` 的点不排进去。
      以前排进去再由约束层报 CLOSED —— 那等于"知道要关门还是硬安排"；
    - **当天固定闭馆的点不排**：靠 `closed_weekdays`，需要 `req.date` 才生效。
    """
    day = DayPlan(day_index=day_index)
    clock = to_minutes(req.start_time or cfg.day_start)
    limit = to_minutes(req.end_time or cfg.day_end)
    mode = resolve_mode(cfg, req.mobility)
    weekday = _weekday_of(req.date)
    prev: POI | None = start_from

    for p in picked:
        if prev is None:
            edge = Transport(mode="start", minutes=0.0, distance_km=0.0, cost=0.0)
        else:
            edge = (get_matrix_edge(prev.poi_id, p.poi_id, cfg, req.city, mode=mode)
                    or estimate_edge(prev, p, cfg, mode=mode))

        arrive = _arrive_at(clock, p, edge)
        leave = arrive + p.stay_min
        if leave > limit:                       # 装不下就停，越界是硬约束，不硬塞
            break
        if _closed_on(p, weekday) or _closes_before(p, leave):
            continue                            # 今天关门 / 来不及：跳过这一站，接着排下一站

        day.items.append(ItineraryItem(
            seq=len(day.items) + 1,
            poi_id=p.poi_id,
            name=p.name,
            lat=p.lat,
            lon=p.lon,
            arrive=to_hhmm(arrive),
            leave=to_hhmm(leave),
            stay_min=p.stay_min,
            cost=p.cost,
            transport=edge,
            reason=_reason(p, wanted) + (
                "；到早了等开门 %d 分钟" % (arrive - (clock + int(round(edge.minutes))))
                if arrive > clock + int(round(edge.minutes)) else ""),
            flags=["wait_open"] if arrive > clock + int(round(edge.minutes)) else [],
            # 分类/地址跟着复制：前端要按分类上色、解说要按分类起标题，
            # 让 UI 直连 data/ 去查又违反分层（UI 只允许 import engine）
            category=p.category,
            address=p.address,
            url=p.url,
            tips=p.tips,   # v1.6：数据层"怎么玩"随行；narrate 对已有 tips 不覆盖
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
    ranked = _apply_must_visit(ranked, req, cfg)  # 必去点紧随其后
    origin = _origin_poi(req, cfg)               # 出发地：只影响第 1 天的第一段在途
    return [
        _fill_day(ranked[i * per_day:(i + 1) * per_day], req, cfg, wanted,
                  day_index=i + 1, start_from=origin if i == 0 else None)
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
    mode = resolve_mode(cfg, req.mobility)
    return (get_matrix_edge(prev.poi_id, p.poi_id, cfg, req.city, mode=mode)
            or estimate_edge(prev, p, cfg, mode=mode))


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

    # 必去点：顺序不限，但必须排进来，所以跟在途经点后面一起先落位
    for m in (req.must_visit or []):
        key = str(m).strip()
        if not key:
            continue
        p = by_id.get(key) or by_name.get(key)
        if p is not None and p.poi_id not in seen:
            head.append(p)
            seen.add(p.poi_id)
    return head


def _utility(p: POI, e: Transport, wanted: set[str], w: dict[str, float],
             day_len: int, budget: float | None, spent: float,
             clock: int = 0) -> float:
    """这一步的"净收益"：满意度与停留时间算正，花钱、跑路、干等算负。

    三项都归一到 0~1 再加权，所以量纲不会互相欺负。
    距离不单独扣分：它已经通过"在途时间"进了目标，再扣一次等于重复惩罚。
    **干等也要扣**：06:30 出门却排一个 07:30 才开门的点，等于白等一小时。
    """
    sat = _match_score(p, wanted)
    cost_ratio = 0.0
    if budget:
        cost_ratio = min(1.0, (p.cost + e.cost) / max(budget - spent, 1e-6))
    wait = _arrive_at(clock, p, e) - (clock + int(round(e.minutes)))
    return (w.get("satisfaction", 0.5) * sat
            + w.get("time", 0.2) * (p.stay_min / day_len)
            - w.get("cost", 0.2) * cost_ratio
            - w.get("time", 0.2) * ((e.minutes + max(0, wait)) / day_len))


def _fits(clock: int, p: POI, e: Transport, limit: int, spent: float,
          budget: float | None) -> bool:
    """硬约束：装得进时间窗、不超预算、**别在人家关门之后才走**。

    贪心只在可行解里挑，不靠事后补救 —— 否则会出现
    "算法觉得划算、排进时间轴才发现已经闭馆"的荒唐结果。

    注意用的是 `_arrive_at`（含"等开门"），与真正铺时间轴时同一套算法。
    """
    arrive = _arrive_at(clock, p, e)
    if arrive + p.stay_min > limit:
        return False
    if _closes_before(p, arrive + p.stay_min):
        return False
    if budget is not None and spent + p.cost + e.cost > budget + 1e-9:
        return False
    return True


def _greedy_one_day(req: PlanRequest, cfg: ScenarioConfig, wanted: set[str],
                    remaining: list[POI], forced: list[POI], per_day: int,
                    day_len: int, budget: float | None,
                    start_from: POI | None = None) -> list[POI]:
    """排一天：先把硬顺序（起点/途经点/必去点）落位，再逐站挑净收益最高的。

    `budget` 传 None = 今天不看预算（降级用）；`start_from` = 出发地（只有第 1 天用）。
    """
    w = _weights(cfg, req)
    clock = to_minutes(req.start_time or cfg.day_start)
    limit = to_minutes(req.end_time or cfg.day_end)
    spent = 0.0
    prev: POI | None = start_from
    picked: list[POI] = []
    queue = list(forced)

    while True:
        if queue:
            p = queue.pop(0)
            e = _edge_between(prev, p, req, cfg)
            if _fits(clock, p, e, limit, spent, budget):
                picked.append(p)
                clock = _arrive_at(clock, p, e) + p.stay_min
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
        clock = _arrive_at(clock, best, e) + best.stay_min
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
    origin = _origin_poi(req, cfg)

    days: list[list[POI]] = []
    for di in range(n_days):
        forced = list(head) if di == 0 else []      # 硬顺序只在第一天消耗
        picked = _greedy_one_day(req, cfg, wanted, list(remaining), forced,
                                 per_day, day_len, budget,
                                 start_from=origin if di == 0 else None)

        if not picked and budget is not None:
            picked = _greedy_one_day(req, cfg, wanted, list(remaining), list(forced),
                                     per_day, day_len, None,
                                     start_from=origin if di == 0 else None)

        for p in picked:                            # 不被跨天重复选中
            if p in remaining:
                remaining.remove(p)
        days.append(picked)
    return days


def _fill_all(days: list[list[POI]], req: PlanRequest, cfg: ScenarioConfig,
              wanted: set[str]) -> list[DayPlan]:
    """把选好的点交给与其它策略共用的铺时间轴逻辑，保证统计口径一致。"""
    origin = _origin_poi(req, cfg)
    return [_fill_day(picked, req, cfg, wanted, day_index=i + 1,
                      start_from=origin if i == 0 else None)
            for i, picked in enumerate(days)]


def greedy_plan(req: PlanRequest, cfg: ScenarioConfig) -> PlanResponse:
    """贪心：每小时刻都挑"净收益最高且装得下"的一站。

    相比 stub 多了三件事：
    1. **预算硬约束** —— 超预算的候选直接不入选，不会排完再报 BUDGET_OVER；
    2. **在途惩罚** —— 同样的满意度，近的优先，避免为了一个点横穿校区；
    3. **目标函数与评分同源** —— 优化什么就是评什么，权重取自场景配置。

    （带锚点的请求由 planner.plan() 中枢委托给 date_plan，见 planner.py。）
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
    """贪心选点 + 2-opt 改序：通常比纯贪心少跑一点路，代价是慢几毫秒。

    （带锚点的请求由 planner.plan() 中枢委托给 date_plan，见 planner.py。）
    """
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


# ---------------------------------------------------------------- 真算法：date（约会模式）
#
# 约会的骨架不是"分数"，是**硬时刻**：电影 14:30 开场、餐厅 18:00 订座，
# 这些事不会因为你没赶到就改时间。所以先按锚点把一天切成段，
# 每段里"玩得完、还赶得上下一场"的候选里挑净收益最高的 —— 错过电影 = 约会失败，
# 这比少逛一个商场严重得多。


def _resolve_anchors(req: PlanRequest, cfg: ScenarioConfig) -> list[tuple[POI, int, int, str]]:
    """把 req.anchors 解析成 [(POI, 时刻分钟, 时长, 标签)]，按时刻升序。

    解析不了的条目（poi 认不出 / 时刻非法）直接跳过，不抛异常 ——
    未排入的锚点由约束层如实报 ANCHOR_MISS，策略层不静默编造。
    """
    out: list[tuple[POI, int, int, str]] = []
    for a in (req.anchors or []):
        if not isinstance(a, dict):
            continue
        key = str(a.get("poi") or "").strip()
        t = str(a.get("time") or "").strip()
        try:
            dur = int(float(a.get("duration_min") or 120))
        except (TypeError, ValueError):
            continue
        try:
            hh, mm = t.split(":")
            at = int(hh) * 60 + int(mm)
        except (TypeError, ValueError):
            continue
        if not key or not (0 <= at < 24 * 60):
            continue
        poi = None
        for p in load_pois(cfg, req.city):
            if p.poi_id == key or p.name == key:
                poi = p
                break
        if poi is None:
            continue
        out.append((poi, at, max(15, dur), str(a.get("label") or "")))
    out.sort(key=lambda x: x[1])
    return out


def date_plan(req: PlanRequest, cfg: ScenarioConfig) -> PlanResponse:
    """约会模式：锚点（电影开场/餐厅订座）切段，段内贪心填充。

    三条铁规矩：
    1. **锚点钉死在时刻上**：arrive = 锚点时刻、leave = 时刻 + 时长。
       电影不会因为你早到而提前开场，晚到也不改表 —— 赶不赶得上由约束层
       如实报 ANCHOR_MISS，前端拿告警提醒用户"这段路上要抓紧"。
    2. **段内候选必须"玩得完、还赶得上下一场"**：离开候选点的时间
       + 候选点到下一锚点的在途 <= 锚点时刻，否则不入选（硬约束）。
    3. **没有锚点时退化为普通贪心**：复用 `_greedy_one_day` 的选点逻辑，
       不会因为"约会模式"就变笨。
    """
    pool, wanted = _candidate_pool(req, cfg)
    anchors = _resolve_anchors(req, cfg)
    w = _weights(cfg, req)
    start = to_minutes(req.start_time or cfg.day_start)
    limit = to_minutes(req.end_time or cfg.day_end)
    origin = _origin_poi(req, cfg)
    weekday = _weekday_of(req.date)
    budget = req.daily_budget if (req.daily_budget or 0) > 0 else None
    day_len = max(1, limit - start)
    cap = max(1, _per_day_top_n(req, cfg))

    anchor_ids = {p.poi_id for p, _, _, _ in anchors}
    remaining = [p for p in pool if p.poi_id not in anchor_ids]
    remaining.sort(key=lambda p: (-_match_score(p, wanted), p.poi_id))

    day = DayPlan(day_index=1)
    state = {"clock": start, "prev": origin, "spent": 0.0}

    def emit(p: POI, e: Transport, arrive: int, leave: int, reason: str,
             stay: int | None = None) -> None:
        day.items.append(ItineraryItem(
            seq=len(day.items) + 1,
            poi_id=p.poi_id,
            name=p.name,
            lat=p.lat, lon=p.lon,
            arrive=to_hhmm(arrive), leave=to_hhmm(leave),
            stay_min=stay if stay is not None else p.stay_min,
            cost=p.cost,
            transport=e,
            reason=reason[:120],
            category=p.category, address=p.address, url=p.url,
            tips=p.tips,   # v1.6：数据层"怎么玩"随行（约会/日常两条排程路径都要带上）
        ))
        state["clock"], state["prev"] = leave, p
        state["spent"] += p.cost + e.cost

    def fill_until(deadline: int, back_to: POI | None = None) -> None:
        """在 [clock, deadline] 这一段里贪心填充，直到装不下或站数到顶。

        `back_to` 是本段结束后的锚点：候选必须"玩完还能赶到它"（离开时刻
        + 候选点 → 锚点的在途 <= deadline），否则不入选。
        末段（没有下一锚点）只看 deadline 本身。
        """
        while len(day.items) < cap and remaining:
            best, best_u, best_pack = None, 0.0, None
            for p in remaining:
                e = _edge_between(state["prev"], p, req, cfg)
                arrive = _arrive_at(state["clock"], p, e)
                leave = arrive + p.stay_min
                if leave > deadline:
                    continue
                if back_to is not None:
                    back = _edge_between(p, back_to, req, cfg)
                    if leave + back.minutes > deadline:
                        continue              # 赶不上这一场：硬约束，直接出局
                if _closes_before(p, leave) or _closed_on(p, weekday):
                    continue
                if budget is not None and state["spent"] + p.cost + e.cost > budget + 1e-9:
                    continue
                u = _utility(p, e, wanted, w, day_len, budget, state["spent"],
                             state["clock"])
                if best is None or u > best_u:
                    best, best_u, best_pack = p, u, (e, arrive, leave)
            if best is None:
                break
            e, arrive, leave = best_pack
            remaining.remove(best)
            tail = ("；须在 %s 前离开（锚点不等人）" % to_hhmm(deadline)) if back_to else ""
            emit(best, e, arrive, leave, _reason(best, wanted) + tail)

    # 有锚点：逐段填充 → 锚点钉死 → … → 最后一段填到 end_time
    if anchors:
        for pa, at, dur, label in anchors:
            fill_until(at, pa)                # 本段候选必须赶得上"当前"这个锚点
            e = _edge_between(state["prev"], pa, req, cfg)
            emit(pa, e, at, at + dur,
                 "锚点：%s，%s 开始（晚到不等人）" % (label or pa.category, to_hhmm(at)),
                 stay=dur)
        fill_until(limit, None)
    # 没有锚点：退化为贪心的一天（复用同一套选点，保证行为一致）
    else:
        head = _mandatory_head(req, cfg)
        picked = _greedy_one_day(req, cfg, wanted, list(remaining), head,
                                 cap, day_len, budget,
                                 start_from=origin)
        day = _fill_day(picked, req, cfg, wanted, day_index=1, start_from=origin)

    day.refresh_stats()
    return PlanResponse(request=req, days=[day])


# ---------------------------------------------------------------- 注册表

STRATEGIES: dict[str, PlannerFn] = {
    "stub": stub_plan,
    "random": random_plan,
    "greedy": greedy_plan,
    "two_opt": two_opt_plan,
    "date": date_plan,
}


def available() -> list[str]:
    """列出所有已注册的策略名。"""
    return sorted(STRATEGIES)


def get(name: str) -> PlannerFn:
    """按名字取策略函数；未知名字给出可选列表，方便命令行排错。"""
    if name not in STRATEGIES:
        raise KeyError("未知策略 '%s'，可选：%s" % (name, available()))
    return STRATEGIES[name]
