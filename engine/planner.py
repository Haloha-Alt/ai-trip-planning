# @author: 甲
# @reviewer: 乙
# @since: 2026-10-27
"""引擎门面：全项目唯一的规划入口。

调用链：请求 -> 选策略 -> 出行程 -> 约束校验 -> 评分 -> 补 warnings

UI / main.py / eval 都只调这里的 plan()，谁都不许直接 import strategies。
"""

from __future__ import annotations

from datetime import datetime

from engine import constraints, strategies
from engine.config import ScenarioConfig, load_scenario
from engine.context import PlanContext
from engine.models import PlanRequest, PlanResponse, Violation


def plan(req: PlanRequest,
         scenario: str | None = None,
         strategy: str | None = None,
         ctx: "PlanContext | None" = None) -> PlanResponse:
    """生成一个行程方案。

    :param req:       请求（PlanRequest，字段全有默认值，空请求也能跑）
    :param scenario:  场景名，None 表示用环境变量或默认场景
    :param strategy:  策略名，None 表示用场景配置里的 default_strategy
    :param ctx:       实时上下文（PlanContext）。**None = 完全离线行为**，
                      传了才会把天气/闭馆信息写进结果，并在必要时重排一次。
    :return:          PlanResponse，**永不返回 None、永不抛业务异常**

    ⚠️ 引擎内部**零 IO**：实时数据必须先由 `engine/realtime.py` 抓成快照，
    再通过 ctx 注入。只有这样才能保证"同样的输入 → 同样的输出"，
    评测能比、出问题能回放。
    """
    if req is None:
        req = PlanRequest()

    cfg: ScenarioConfig = load_scenario(scenario)
    name = strategy or cfg.default_strategy
    # v1.6.1 锚点中枢分发：任何场景只要给了锚点（课表/固定安排/电影场次），
    # 切段逻辑就优于其它算法 —— 上课 08:00 开讲与电影 14:30 开场对排程是
    # 同一件事：到点必须已经开始。stub/random/贪心/2-opt 都不认锚点，
    # 统一委托给 date 的切段策略；没有锚点时行为逐字不变。
    # resp.strategy 如实写 "date"，不冒充用户没选的算法。
    if getattr(req, "anchors", None) and name != "date":
        name = "date"
    fn = strategies.get(name)                    # 换算法只影响这一行

    resp = fn(req, cfg)
    resp.strategy = name
    resp.scenario = cfg.name
    resp.generated_at = datetime.now().isoformat(timespec="seconds")

    # 约束检查：只往 warnings 里写，绝不抛异常
    for v in constraints.validate_request(req, cfg):
        resp.warnings.append(str(v))
    for d in resp.days:
        for v in constraints.validate_day(d, cfg, req):
            resp.warnings.append(str(v))

    # 必去点：**跨天**判断。放在 day 级校验里会把"排在第 2 天"误报成"没排进来"。
    if req.must_visit:
        placed_ids = {it.poi_id for d in resp.days for it in d.items}
        placed_names = {it.name for d in resp.days for it in d.items}
        missing = [str(m).strip() for m in req.must_visit
                   if str(m).strip() and str(m).strip() not in placed_ids
                   and str(m).strip() not in placed_names]
        if missing:
            resp.warnings.append(str(Violation(
                "MUST_VISIT_MISS", "必去点未排入行程：%s" % "、".join(missing))))

    # 锚点（约会模式）：**跨天**核对格式/排入/迟到风险，与 must_visit 同理放在 day 级之外。
    if req.anchors:
        for v in constraints.validate_anchors(req, resp, cfg):
            resp.warnings.append(str(v))

    resp.metrics = constraints.score(resp, cfg)

    # 数据覆盖情况：让界面能如实说"这个城市没有实测路网，在途时间是直线估算"，
    # 而不是让用户以为地图上的分钟数是量出来的。
    try:
        from data.loader import load_matrix

        edges = load_matrix(cfg, req.city)
        resp.meta["data"] = {
            "matrix_edges": len(edges),
            "matrix_modes": sorted({e.mode for e in edges}),
            "city": req.city,
        }
    except Exception:                       # 数据层异常不能连累主结果
        pass

    # 权重档位：profile 优先 → 场景 [weights] → 都没有就交给 PlanMetrics.overall 的内置默认值。
    # 写进 meta 是为了让 UI / 导出 / 评测用同一套权重算总分，不各算各的。
    weights = None
    if req.profile and cfg.weight_presets.get(req.profile):
        weights = cfg.weight_presets[req.profile]
    elif cfg.weights:
        weights = cfg.weights
    if weights:
        resp.meta["weights"] = dict(weights)

    # 待确认清单：数据里没填"来源适用日期"的点，明确列出来 —— 任务要求"区分核实与待确认"，
    # 与其在报告里说"数据可能有误"，不如让应用自己说出来。
    pending: list[str] = []
    try:
        from data.loader import get_poi

        for d in resp.days:
            for it in d.items:
                p = get_poi(it.poi_id, cfg, req.city)
                if (p is not None and not (p.source_date or "").strip()
                        and not (p.cost or 0) <= 0):   # 免费站（人均0）没有花费风险，不进待核实
                    pending.append("%s（%s，%s）" % (p.name, p.category or "未分类", p.cost_unit))
    except Exception:                       # 数据层异常不能连累主结果
        pending = []
    if pending:
        resp.meta["pending"] = sorted(set(pending))

    # Plan B 备选（v1.6.4）：户外/海景站碰上坏天气改去哪 —— 从同一候选池里找
    # 同商圈的室内站（带"室内"标签，且没被排进行程）。纯规则推导，不编事实：
    # 每条备选都是真实入库的 POI，写明分类和人均。查不到就不写，不硬凑。
    try:
        from data.loader import load_pois

        placed = {it.poi_id for d in resp.days for it in d.items}
        pool = load_pois(cfg, req.city)
        zone_of = {p.poi_id: (p.zone or "") for p in pool}
        indoor = [p for p in pool
                  if p.poi_id not in placed and "室内" in (p.tags or [])]
        # 同商圈的户外站往往共用同一批室内备选 —— 按备份集合分组，避免 Plan B 里
        # 出现两行一模一样的建议（星海广场/跨海大桥都指向圣亚+海边咖啡）。
        grouped: dict[tuple, dict] = {}
        for d in resp.days:
            for it in d.items:
                if it.category != "户外" and "户外" not in (it.name or ""):
                    continue
                alts = sorted(
                    (p for p in indoor if zone_of.get(p.poi_id) == zone_of.get(it.poi_id)),
                    key=lambda p: -p.score)[:2]
                if alts:
                    key = tuple(p.poi_id for p in alts)
                    entry = grouped.setdefault(key, {
                        "instead_of": [],
                        "reason": "下雨/大风时改室内",
                        "backups": ["%s（%s，人均%.0f元）" % (p.name, p.category, p.cost)
                                    for p in alts],
                    })
                    entry["instead_of"].append(it.name)
        plan_b = list(grouped.values())
        if plan_b:
            resp.meta["plan_b"] = plan_b
    except Exception:                       # 数据层异常不能连累主结果
        pass

    # v1.6.5 出发/返程腿 + 末班时刻：全部来自 data/cities/<key>/ 的 landmarks.csv、
    # last_trains.csv 与场景 [taxi] 计价 —— 数据说话，缺哪样就少哪样，不编。
    try:
        from data.loader import load_landmarks, load_last_trains
        from engine.geo import estimate_cost, estimate_minutes, haversine_km

        items = [it for d in resp.days for it in d.items]
        if items:
            info = None
            try:
                from data.cities import city_info
                info = city_info(req.city)
            except Exception:
                info = None
            ckey = info.key if info is not None else str(req.city or "").strip()

            lms = load_landmarks(cfg, ckey)
            lm = None
            want = (req.origin or "").strip()
            if want:
                for cand in lms:
                    nm = cand.get("name") or ""
                    if nm == want or nm in want or want in nm:
                        lm = cand
                        break

            taxi = getattr(cfg, "taxi", None) or {}

            def _leg(frm_lat: float, frm_lon: float, frm_name: str, to_it) -> dict:
                km = haversine_km(frm_lat, frm_lon, to_it.lat, to_it.lon)
                mins = round(estimate_minutes(km, "car", cfg))
                if taxi.get("start_price") and taxi.get("per_km"):
                    extra_km = max(0.0, km - float(taxi.get("start_km", 0.0)))
                    fare = float(taxi["start_price"]) + extra_km * float(taxi["per_km"])
                    basis = "出租车起步价+里程（场景 [taxi] 口径，白天）"
                else:
                    fare = estimate_cost(km, "car", cfg)
                    basis = "场景每公里单价"
                return {"from": frm_name, "to": to_it.name, "km": round(km, 2),
                        "minutes": mins, "fare": round(fare, 1), "basis": basis}

            if lm is not None:
                try:
                    resp.meta["origin_leg"] = _leg(
                        float(lm["lat"]), float(lm["lon"]), lm.get("name", ""), items[0])
                    resp.meta["return_leg"] = _leg(
                        float(lm["lat"]), float(lm["lon"]), lm.get("name", ""), items[-1])
                except Exception:
                    pass

            trains = load_last_trains(cfg, ckey)
            if trains:
                # 只挑和本次行程真有关系的：站点名（去掉"站"字）命中站点名/地址
                texts = []
                for it in items:
                    texts.append(it.name or "")
                    if it.address:
                        texts.append(it.address)

                def _hit(t: dict) -> bool:
                    st = (t.get("station") or "").rstrip("站")
                    if not st:
                        return False
                    if any(st in x for x in texts):
                        return True
                    # 兜底：站名前两字（如「星海广场」→「星海」）命中，覆盖
                    # 「星海广场站 vs 星海湾大桥」这类同片区不同后缀的情况
                    return len(st) >= 2 and any(st[:2] in x for x in texts)

                matched = [t for t in trains if _hit(t)]
                if matched:
                    resp.meta["last_trains"] = matched
    except Exception:                       # 数据层异常不能连累主结果
        pass

    # 实时数据：只在这里统一生效一次，策略内部一律不碰它
    if ctx is not None and getattr(ctx, "snapshot", None) is not None:
        try:
            from engine.replan import apply_snapshot

            resp = apply_snapshot(resp, ctx.snapshot, ctx,
                                  scenario=cfg.name, strategy=name)
        except Exception as e:                  # noqa: BLE001 - 重排失败不能连累主结果
            resp.warnings.append("[实时] 处理失败，已保留原方案：%s" % e)

    # v1.6.6 雨天换站：坏天气时，最后一天的户外末站自动换成 Plan B 的首选室内站。
    # 放在 plan_b 计算之后、返回之前；全程 try/except，数据异常不连累主结果。
    try:
        snap = getattr(ctx, "snapshot", None) if ctx is not None else None
        if snap is not None and snap.weather:
            _bad = {"rain", "snow", "storm"}
            bad_weather = any(
                (getattr(w, "condition", "") in _bad) or getattr(w, "is_bad", False)
                for w in snap.weather)
            if bad_weather and resp.days:
                from data.loader import get_poi as _get_poi, load_pois as _load_pois

                day = resp.days[-1]
                if day.items:
                    last = day.items[-1]
                    anchor_names = set()
                    for a in (getattr(req, "anchors", None) or []):
                        v = str((a or {}).get("poi", "") or "").strip()
                        if v:
                            anchor_names.add(v)
                    is_anchor = (last.name in anchor_names
                                 or last.poi_id in anchor_names
                                 or any(n in last.name or last.name in n
                                        for n in anchor_names))
                    outdoor = (last.category == "户外"
                               or "户外" in (last.name or ""))
                    if outdoor and not is_anchor:
                        # 从 plan_b 里找覆盖这个末站的备份组，取首选室内站
                        cand_name = ""
                        for entry in (resp.meta.get("plan_b") or []):
                            if last.name in (entry.get("instead_of") or []):
                                bks = entry.get("backups") or []
                                if bks:
                                    cand_name = (bks[0] or "").split("（")[0].strip()
                                break
                        new_p = None
                        if cand_name:
                            for p in _load_pois(cfg, req.city):
                                if p.name == cand_name:
                                    new_p = p
                                    break
                            if new_p is None:
                                new_p = _get_poi(cand_name, cfg, req.city)
                        if new_p is not None:
                            old_name = last.name
                            last.poi_id = new_p.poi_id
                            last.name = new_p.name
                            last.lat, last.lon = new_p.lat, new_p.lon
                            last.category = new_p.category
                            last.address = new_p.address or ""
                            last.tips = new_p.tips or ""
                            last.url = new_p.url or ""
                            last.cost = float(new_p.cost or 0)
                            last.flags = []
                            # 交通重算复杂，沿用原交通并在告警里如实说明
                            day.refresh_stats()
                            resp.meta["rain_swap"] = {
                                "from": old_name, "to": new_p.name,
                                "reason": "当天有雨，户外末站自动换室内",
                            }
                            note = ("[实时] 当天有雨，户外末站「%s」已自动换成室内「%s」"
                                    "（在途时间沿用原交通估算）" % (old_name, new_p.name))
                            resp.warnings.append(note)
    except Exception:                       # 数据层异常不能连累主结果
        pass

    return resp


def clear_caches() -> None:
    """清空数据层缓存（改了 CSV 之后调用）。

    放在这里是为了保持分层单向：UI 只允许 import engine，不允许直接 import data。
    """
    from data.loader import clear_cache

    clear_cache()
