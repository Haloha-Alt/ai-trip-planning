# @author: 甲
# @reviewer: 乙
# @since: 2026-10-28
"""用例层：三种入口（Streamlit / FastAPI / CLI）共用的业务动作。

这里的每个函数都对应一个"用户能说出来的动作"，而不是一个技术步骤：

    options()                -> 有哪些城市 / 场景 / 算法可以选
    plan_from_text()         -> 用一句自然语言生成行程
    plan_from_request()      -> 用一个结构化的 PlanRequest 生成行程
    apply_overrides()        -> 用界面/接口上的控件值覆盖解析结果
    save_plan()              -> 落盘

全部返回引擎的契约对象（PlanResponse），不掺任何展示细节；
"怎么画"由 ui/render.py 与 web/ 各自决定。
"""

from __future__ import annotations

from typing import Any

from service.bootstrap import OUTPUT_DIR, env
from engine.config import ScenarioConfig, list_scenarios, load_scenario
from engine.models import PlanRequest, PlanResponse
from engine.planner import clear_caches, plan
from engine.strategies import available


# ---------------------------------------------------------------- 选项


def city_options() -> list[dict[str, Any]]:
    """下拉框要的**高校**列表：`[{key, name}]`。

    v1.3 起选择维度从「城市」换成「高校」（docs/14 多校区扩展）：
    只列 `kind = "campus"` 的数据包，显示名优先用 label（大连理工大学这类
    「一个 key 承载城市+校区两层」的条目必须显示校名而不是城市名）。
    """
    from data.cities import city_info, list_campuses

    out = []
    for k in list_campuses():
        info = city_info(k)
        out.append({"key": k, "name": info.display_name if info else k})
    return out


def options() -> dict[str, Any]:
    """一次性给齐所有可选项，UI 与 /api/options 共用。

    `default_strategy` 必须由后端给：`available()` 是**字母序**，
    第一个是 `greedy`（真算法），而场景配置里默认是 `stub`。
    前端如果拿"第一个"当默认值，就等于悄悄换了算法 —— 页面的站点顺序
    会和命令行对不上，还容易被误判成算法有 bug（见 `04` AC-44）。
    """
    from data.cities import city_info, default_city, list_cities

    cfg = load_scenario()
    scenarios = list_scenarios()

    # v1.6.3 约会模式的城市下拉：**城市口径**（显示 name 而不是 label）。
    # 与 city_options() 的区别：那边只列 kind="campus" 且显示校名；
    # 约会以城市为地点，注册表里所有城市都该能选（没有约会数据包的会如实报空）。
    date_cities = []
    for k in list_cities():
        info = city_info(k)
        date_cities.append({"key": k, "name": (info.name if info else "") or k})

    # 每个场景各自的默认算法：切场景时前端跟着切，但用户手动选过就不再覆盖
    per_scenario: dict[str, str] = {}
    for name in scenarios:
        try:
            per_scenario[name] = load_scenario(name).default_strategy
        except Exception:                       # noqa: BLE001 - 单个场景坏了不拖垮选项
            per_scenario[name] = cfg.default_strategy

    return {
        "cities": city_options(),
        "date_cities": date_cities,
        "scenarios": scenarios,
        "strategies": available(),
        "default_city": default_city(),
        "default_scenario": cfg.name,
        "default_strategy": per_scenario.get(cfg.name, cfg.default_strategy),
        "scenario_strategies": per_scenario,
        # 高德「Web端(JS API)」Key：**可以公开给前端**（它是域名白名单校验，不是密钥）。
        # 留空 = 地图保持离线 SVG 手绘（断网也能画）；填了 = 用真底图，加载失败自动退回 SVG。
        # security_code 是 2.0 SDK 的安全密钥（2021-12 后新建 Key 必配），前端要在加载 SDK
        # 之前塞进 window._AMapSecurityConfig，否则会报 INVALID_USER_SCODE 白屏。
        "map": {
            "js_key": env("AMAP_JS_KEY", ""),
            "security_code": env("AMAP_SECURITY_CODE", ""),
        },
        # 模型：前端菜单里的「自动 / 手动指定」就是靠这两项驱动的。
        # models 只给角色映射后的真实模型名（不暴露 Key），default=True 的那个是主力。
        "models": _model_choices(),
        "router_mode": _router_mode(),
    }


def _model_choices() -> list[dict]:
    """可选模型清单：**优先问网关要全量**（一个 Key 后面挂着 20+ 个模型），
    网关问不到才回落到 `ROLE_MODELS` 的那几个。

    每项带 group：`推荐` 是路由实际会用的（fast/default/reasoning/long），
    `全部模型` 是网关里其余那些能手动指定、但路由不会自动挑的。
    """
    try:
        from llm.router import DEFAULT_ROLE, ROLE_MODELS
    except Exception:
        return []

    role_by_name: dict[str, list[str]] = {}
    for role, name in ROLE_MODELS.items():
        role_by_name.setdefault(name, []).append(role)

    names = _gateway_models() or list(role_by_name)
    out = []
    for n in names:
        roles = sorted(role_by_name.get(n, []))
        low = n.lower()
        chat = not any(k in low for k in ("embedding", "reranker", "bge-", "ocr"))
        out.append({
            "name": n,
            "roles": roles,
            "chat": chat,
            "default": DEFAULT_ROLE in roles,
            "group": "推荐（路由会用到）" if roles else "全部模型（仅手动指定）",
            "label": n + ("（%s）" % "/".join(roles) if roles else ("，非对话" if not chat else "")),
        })
    out.sort(key=lambda x: (0 if x["roles"] else 1, not x["default"], not x["chat"], x["name"]))
    return out


_MODEL_CACHE: dict = {"at": 0.0, "names": []}


def _gateway_models() -> list[str]:
    """问网关 `/v1/models` 拿全量模型名，缓存 5 分钟（失败就返回空表，不拖慢页面）。"""
    import time

    if _MODEL_CACHE["names"] and time.time() - _MODEL_CACHE["at"] < 300:
        return _MODEL_CACHE["names"]
    names: list[str] = []
    try:
        from llm.client import get_client

        c = get_client()
        if getattr(c, "enabled", False) and hasattr(c, "list_models"):
            names = [str(x) for x in (c.list_models() or []) if x]
    except Exception:
        names = []
    if names:
        _MODEL_CACHE.update(at=time.time(), names=names)
    return names


def _router_mode() -> str:
    try:
        from llm.router import router_mode

        return router_mode()
    except Exception:
        return "unknown"


def scenario_config(name: str | None = None) -> ScenarioConfig:
    return load_scenario(name)


# ---------------------------------------------------------------- 规划


def apply_overrides(req: PlanRequest, overrides: dict[str, Any] | None) -> PlanRequest:
    """用"用户手动改过的值"覆盖解析结果。

    **只有传进来的键才覆盖** —— 这是界面上"不动控件就跟随文本"的实现基础。
    传 `None` / 空字典 = 一个字都不改。

    会做两件额外的事：
    - `daily_budget` 传 0 / 负数 → 视为"不限制预算"（None），与 CLI 口径一致
    - `city` 会走一遍城市解析，把"大连市"规范化成 key
    """
    if not overrides:
        return req

    city = overrides.get("city")
    if city:
        from data.cities import resolve_city

        req.city = resolve_city(str(city)) or str(city)

    for field in ("days", "start_time", "end_time", "top_n", "pace", "origin",
                  "date", "travelers", "budget_basis", "mobility", "start_poi_id",
                  "zone"):
        v = overrides.get(field)
        if v not in (None, ""):
            setattr(req, field, v)

    if "daily_budget" in overrides:
        b = overrides.get("daily_budget")
        try:
            b = float(b) if b is not None and b != "" else 0.0
        except (TypeError, ValueError):
            b = 0.0
        req.daily_budget = None if b <= 0 else b

    for field in ("interests", "avoid", "must_visit"):
        v = overrides.get(field)
        if v is not None:
            if isinstance(v, str):
                v = [x.strip() for x in v.replace("，", ",").split(",") if x.strip()]
            setattr(req, field, list(v))

    # v1.5 约会模式：锚点/预订约束（电影开场、订座等硬时刻）。
    # 这里只做"形状清洗"，**不做语义裁决**：时间写错、地点认不出等
    # 一律原样传给引擎，由 engine/constraints.validate_anchors 以
    # ANCHOR_MISS 告警的形式暴露 —— 与"违规进 warnings、绝不抛异常"
    # 的口径一致，也让「直接构造 PlanRequest」和「走接口」两条路径行为相同。
    if "anchors" in overrides:
        req.anchors = sanitize_anchors(overrides.get("anchors"))

    return req


def sanitize_anchors(raw: Any) -> list[dict]:
    """把界面/接口传来的锚点列表洗成引擎认识的形状。

    只清形状，不判对错：
    - 非 dict 条目、连 poi 都没写的条目丢弃（引擎无从告警，留着只会噪声）
    - time / duration_min / label 原样或空缺传递 —— 是否合法由引擎判定并告警
    """
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        poi = str(item.get("poi") or item.get("name") or "").strip()
        if not poi:
            continue
        out.append({
            "poi": poi,
            "time": str(item.get("time") or "").strip(),
            "duration_min": item.get("duration_min"),
            "label": str(item.get("label") or "").strip(),
        })
    return out


def build_request(text: str = "",
                  cfg: ScenarioConfig | None = None,
                  scenario: str | None = None,
                  overrides: dict[str, Any] | None = None,
                  use_llm: bool = True,
                  model: str | None = None) -> PlanRequest:
    """文本 + 控件值 → 一个确定可用的 PlanRequest（不规划，只解析）。

    单独抽出来是因为**实时数据要用它**：抓天气得先知道去哪个城市、几天。
    如果先规划再抓天气，就等于"排完了才问天气预报"，顺序反了。

    :param use_llm: False 时**强制走规则解析**，一个模型都不调。
        这是页面「模型开/关」对照的底座：同一句话，开着能听懂"别太赶"，
        关掉就只剩关键词匹配 —— 差别当场可见，也顺手证明了"没有模型也跑得动"。
    """
    from llm.parser import parse

    cfg = cfg or load_scenario(scenario)
    if use_llm:
        req = parse(text or "", cfg, client=(llm_client(model) if model else None))
    else:
        from llm.parser import _rule_parse

        req = _rule_parse(text or "", cfg)
    req.raw_query = text or req.raw_query
    req = apply_overrides(req, overrides)

    # v1.6 访客场景：校门开放时段是**硬事实**，不是偏好。
    # 规则解析会把「上午」抽成 07:00，可 visit.toml 写的是 09:00 放行、17:00 前离校；
    # 07:00 到门口也进不去，只会排出一条从"在校门外等着"开始的假行程。
    # 所以把时间窗钳进场景配置的范围里（事实层约束规则层，答辩时很好讲）。
    if cfg.name == "visit":
        if req.start_time and req.start_time < cfg.day_start:
            req.start_time = cfg.day_start
        if req.end_time and req.end_time > cfg.day_end:
            req.end_time = cfg.day_end

    # 文本里没提城市 → 用注册表的默认城市。
    # 不留空是必须的：城市是**数据路由**，空城市会让 `poi_path()` 掉回场景配置里的
    # 兜底文件（`data/pois.csv`，8 条占位数据），于是网页/命令行默认看到的是
    # "占位点01"而不是真实的凌水生活圈 —— 演示第一眼就露馅。
    if not (req.city or "").strip():
        from data.cities import default_city

        req.city = default_city()
    return req


def plan_context_for(req: PlanRequest,
                     enabled: bool = True,
                     timeout: float = 6.0):
    """按请求去抓一份实时快照，包成 PlanContext。

    **任何失败都只是"没有实时数据"**，不会让规划失败：
    没开 / 城市没坐标 / 断网 —— 一律返回不含快照的 PlanContext，
    引擎行为退回完全离线的老路径。
    """
    from engine.context import PlanContext

    if not enabled:
        return PlanContext(snapshot=None)

    try:
        from data.cities import city_info
        from data.loader import load_pois
        from engine.realtime import fetch_snapshot

        info = city_info(req.city)
        if info is None or not info.lat or not info.lon:
            return PlanContext(snapshot=None)

        start = None
        if req.date:
            from datetime import date

            try:
                start = date.fromisoformat(req.date)
            except (ValueError, TypeError):
                start = None

        cfg = load_scenario()
        snap = fetch_snapshot(
            city=info.key, lat=info.lat, lon=info.lon,
            days=max(1, int(req.days or 1)), start=start,
            pois=load_pois(cfg, info.key), timeout=timeout,
        )
        return PlanContext(snapshot=snap)
    except Exception:                                  # noqa: BLE001 - 实时数据永远可选
        return PlanContext(snapshot=None)


def plan_from_request(req: PlanRequest,
                      scenario: str | None = None,
                      strategy: str | None = None,
                      ctx=None) -> PlanResponse:
    """结构化请求 → 行程。未知算法名会抛 KeyError，由调用方转成友好提示。"""
    cfg = load_scenario(scenario)
    # 与 build_request 同一条硬事实，再兜一次：直接构造 PlanRequest 的入口
    # （CLI / 测试 / 第三方调用）没有经过文本解析，时间窗也得守校门开放时段。
    if cfg.name == "visit":
        if req.start_time and req.start_time < cfg.day_start:
            req.start_time = cfg.day_start
        if req.end_time and req.end_time > cfg.day_end:
            req.end_time = cfg.day_end
    return plan(req, scenario=scenario, strategy=strategy, ctx=ctx)


def plan_from_text(text: str,
                   scenario: str | None = None,
                   strategy: str | None = None,
                   cfg: ScenarioConfig | None = None,
                   overrides: dict[str, Any] | None = None,
                   realtime: bool = False,
                   ctx=None,
                   use_llm: bool = True,
                   model: str | None = None) -> PlanResponse:
    """一句自然语言 → 行程。

    :param realtime: 是否顺带抓实时天气/闭馆（会多花 1 秒左右；断网自动降级）
    :param ctx:      已经准备好的 PlanContext（传了就不再自己抓）
    :param use_llm:  False = 只跑规则解析（「模型开/关」对照用）
    """
    req = build_request(text, cfg, scenario, overrides, use_llm=use_llm, model=model)
    if ctx is None and realtime:
        ctx = plan_context_for(req, enabled=True)
    return plan_from_request(req, scenario=scenario, strategy=strategy, ctx=ctx)


# ---------------------------------------------------------------- 访客可达性


def apply_visitor_rules(resp: PlanResponse, guide: dict | None = None) -> PlanResponse:
    """按「访客可达性规则」修正行程：只有学生能进的点降级为外观，宿舍直接剔除。

    为什么必须有这一步
    ------------------
    POI 表只描述"这个地方是什么"，不描述"**谁能进**"。于是访客需求会被排进图书馆自习区
    并停留两小时 —— 那是内部人员的行为，访客做不到。这条规则把"可进入程度"补上：
      · `view_only`  → 停留压到规则给的分钟数（默认 10），理由里注明"仅外观"
      · `exclude`    → 从行程里去掉，并写一条 warning 说明为什么
      · `need_card`  → 保留但压时长，提示"能否用餐请到门口确认"

    规则来自 `data/cities/<city>/visitor_guide.json` 的 `access_rules`（数据，不是代码）。
    时间轴：被压缩的分钟数**顺延**给后面的站（整体前移），arrive/leave 仍自洽。
    """
    from engine.geo import to_hhmm

    if not guide:
        from llm.guide import load_guide

        guide = load_guide(resp.request.city or "dalian")
    ar = (guide or {}).get("access_rules") or {}
    rules = ar.get("rules") or {}
    default = ar.get("default") or {"level": "open", "stay_min": None}
    if not rules:
        return resp

    cat_of: dict[str, str] = {}
    try:
        from data.loader import load_pois
        from engine.config import load_scenario

        cfg = load_scenario(resp.scenario)
        for p in load_pois(cfg, resp.request.city or None):
            cat_of[p.poi_id] = p.category
    except Exception:
        return resp

    def _m(hhmm: str) -> int:
        try:
            h, m = str(hhmm).split(":")
            return int(h) * 60 + int(m)
        except Exception:
            return 0

    notes: list[str] = []
    for day in resp.days:
        kept, shift = [], 0
        for it in day.items:
            rule = rules.get(cat_of.get(it.poi_id, ""), default)
            if rule.get("level") == "exclude":
                notes.append("%s：访客不开放，已从行程中去掉" % it.name)
                continue
            arrive = _m(it.arrive) - shift
            stay = it.stay_min
            cap = rule.get("stay_min")
            if cap is not None and stay > cap:
                shift += stay - cap
                stay = cap
            it.arrive = to_hhmm(arrive)
            it.stay_min = stay
            it.leave = to_hhmm(arrive + stay)
            if rule.get("note"):
                it.reason = ((it.reason or "") + "｜" + rule["note"])[:80]
            kept.append(it)
        for i, it in enumerate(kept, 1):
            it.seq = i
        day.items = kept
        day.total_stay_min = sum(i.stay_min for i in kept)
        day.total_cost = sum(i.cost for i in kept)

    if notes:
        resp.warnings = list(resp.warnings) + notes
    return resp


# ---------------------------------------------------------------- 解说与局部调整


def llm_client(model: str | None = None):
    """取大模型客户端。没有 Key 时返回 NullClient（调用方不用判断）。

    :param model: 手动指定模型名。给了就**绕过自动路由**、整次请求都用它；
        不给则按 `LLM_ROUTER` 走路由（自动挑）。前端菜单的「自动 / 手动」就是这条分叉。

    手动指定时用浅拷贝改 `model`，不去动全局单例 —— 否则两个人同时用会互相覆盖。
    """
    import copy

    from llm.client import get_client
    from llm.router import wrap

    c = get_client()
    if model:
        if not getattr(c, "enabled", False):
            return c
        c = copy.copy(c)
        c.model = model
        return c
    return wrap(c)


def narrate_plan(resp: PlanResponse, enabled: bool = True,
                 model: str | None = None) -> PlanResponse:
    """给行程补上主题 / 每站解说 / 一句话汇总。

    `enabled=False` 或没有 Key 时走规则模板 —— **永远有解说，不会因为模型失败就空白**。
    开了模型路由时，把这次的选型结论写进 `meta["router"]`，答辩时能当场解释"为什么用这个模型"。
    """
    from llm.narrate import narrate

    client = llm_client(model) if enabled else None
    resp = narrate(resp, client=client, use_llm=enabled)

    routes = getattr(client, "last_routes", None)
    if routes and isinstance(getattr(resp, "meta", None), dict):
        resp.meta["router"] = routes[-1].to_dict()
    return resp


def detailed_itinerary_plan(resp: PlanResponse, query: str = "",
                            enabled: bool = True,
                            model: str | None = None) -> PlanResponse:
    """v1.6 详细行程编排：把「规则骨架 + 校区事实层 + 大模型」接进主链路。

    这是本项目区别于「让模型直接写一份攻略」的地方，三层各司其职：

    * **规则引擎**（已经跑完 `plan()`）：站点顺序、时刻、停留、在途、成本、
      约束评分 —— 可复现、可验证，这一层一个数字都不动；
    * **校区事实层** `visitor_guide.json`：预约渠道、校门动作、可达性、
      逐点看什么、来源与查阅日期。这里做两件事：
        - `briefing()` 生成入校须知速览写进 `meta["visitor_briefing"]`；
          刻意 `enabled=False` 走规则模板 —— 渠道 / 电话 / 时段是最敏感的
          事实，不交给措辞层改写，避免模型把「参观清华」写成别的小程序。
        - `llm/itinerary.py` 读取它生成逐站事实、溯源与可达性提示；
      **不调用 `apply_visitor_rules()`**：那会删点/压时间，把访客本就不多
      的可逛点再砍一遍。可达性在详细行程里以「提示」呈现，保住完整动线。
    * **大模型**：只写导览措辞、为什么这样排、备选与取舍，写进
      `meta["detailed_itinerary"]`。没有 Key / `enabled=False` / 模型失败时
      `llm/itinerary.py` 用规则模板产出**同样详细**的结构 —— 于是「模型开/关」
      可以在界面上直接对照：站点与时刻不变，措辞层有/无增量。

    全程只写 `meta`，不改 `days/items`，不改冻结契约 `engine/models.py`。
    永不抛异常：增强层挂了，主行程照常可用。
    """
    from llm.guide import briefing, load_guide
    from llm.itinerary import enrich

    city = resp.request.city or "dalian"
    guide = load_guide(city)
    text = query or (resp.request.raw_query or "")

    # 访客简报只写给访客（visit）。之前 daily 也全量输出，学生选「校内游览」
    # 却看到入校预约渠道与身份证要求 —— 正是「两个场景没区别」观感的来源之一。
    # 学生带校外亲友的需求后续走「身份=访客」再生成，不在学生日常里混排。
    if (getattr(resp, "scenario", "") or "").strip() == "visit":
        try:
            resp.meta["visitor_briefing"] = briefing(
                resp, city=city, client=None, enabled=False)
        except Exception:                      # noqa: BLE001 - 简报挂了不影响行程
            resp.meta["visitor_briefing"] = {}
    else:
        resp.meta["visitor_briefing"] = {}

    client = llm_client(model) if enabled else None
    resp = enrich(resp, guide=guide, query=text, city=city,
                  client=client, enabled=enabled)

    detail = resp.meta.get("detailed_itinerary") or {}
    if detail.get("model_active") and not detail.get("model"):
        detail["model"] = str(getattr(client, "model", "") or "")

    # 与 narrate_plan 同口径：把本次模型选型写进 meta，前端能解释「为什么用这个模型」
    routes = getattr(client, "last_routes", None)
    if routes and isinstance(resp.meta, dict) and not resp.meta.get("router"):
        resp.meta["router"] = routes[-1].to_dict()
    return resp


def enhance_plan(resp: PlanResponse, query: str = "",
                 enabled: bool = True,
                 model: str | None = None) -> PlanResponse:
    """v1.6.2 提速：解说（narrate）与详细编排（detailed）**并行**跑。

    为什么要并行
    ------------
    老链路里两个增强串行执行，各自一次模型调用（约 3s + 8s），页面要白等
    两倍时间；两者只写 `meta` 与 `tips`，互不读对方输出，天然可并行。
    线程池各拿一份 `deepcopy` 副本独立增强，结束后合并回原对象 ——
    失败的一侧保持原样（模板/规则版），绝不拖垮另一侧。

    合并口径（与各函数自身写入的字段一一对应）：
    * narrate 侧：`days[].theme`、`items[].tips`、`meta["narrative"]`；
    * detailed 侧：`meta["detailed_itinerary"]`、`meta["visitor_briefing"]`、
      `meta["router"]`（优先取 detailed 的选型结论，其次 narrate 的）；
    * `warnings` 去重合并，保持出现顺序。
    """
    if not enabled:
        # 关模型的对照路径很便宜（纯规则模板），顺序跑即可，行为逐字不变
        resp = narrate_plan(resp, enabled=False)
        return detailed_itinerary_plan(resp, query=query, enabled=False)

    import copy
    from concurrent.futures import ThreadPoolExecutor

    a, b = copy.deepcopy(resp), copy.deepcopy(resp)

    def _narrate():
        try:
            return narrate_plan(a, enabled=True, model=model)
        except Exception:                       # noqa: BLE001 - 解说挂了不拖垮编排
            return a

    def _detailed():
        try:
            return detailed_itinerary_plan(b, query=query, enabled=True, model=model)
        except Exception:                       # noqa: BLE001 - 编排挂了不拖垮解说
            return b

    with ThreadPoolExecutor(max_workers=2) as pool:
        f_n = pool.submit(_narrate)
        f_d = pool.submit(_detailed)
        na, de = f_n.result(), f_d.result()

    # ---- narrate 侧合并 ----
    for d_src, d_dst in zip(na.days, resp.days):
        if getattr(d_dst, "theme", "") in ("", None) and d_src.theme:
            d_dst.theme = d_src.theme
        for it_src, it_dst in zip(d_src.items, d_dst.items):
            if getattr(it_src, "tips", "") and not getattr(it_dst, "tips", ""):
                it_dst.tips = it_src.tips
    if na.meta.get("narrative"):
        resp.meta["narrative"] = na.meta["narrative"]

    # ---- detailed 侧合并 ----
    for key in ("detailed_itinerary", "visitor_briefing"):
        if de.meta.get(key):
            resp.meta[key] = de.meta[key]
    router = de.meta.get("router") or na.meta.get("router")
    if router and not resp.meta.get("router"):
        resp.meta["router"] = router

    # ---- warnings 去重合并 ----
    seen: set[str] = set()
    merged: list[str] = []
    for w in list(resp.warnings) + list(na.warnings) + list(de.warnings):
        if w and w not in seen:
            seen.add(w)
            merged.append(w)
    resp.warnings = merged
    return resp


def adjust_plan(resp: PlanResponse, instruction: str,
                enabled: bool = True) -> PlanResponse:
    """按一句话局部调整行程（只动被点名的那一站，其余不变）。"""
    from llm.adjust import adjust

    return adjust(resp, instruction, client=llm_client() if enabled else None,
                  use_llm=enabled)


# ---------------------------------------------------------------- 落盘与缓存


def save_plan(resp: PlanResponse, out_path: str | None = None,
              fmt: str = "json") -> str:
    """导出并落盘，返回写入的绝对路径。目录不存在会自动建。"""
    from pathlib import Path

    from service.exporters import default_name, export

    path = Path(out_path) if out_path else (OUTPUT_DIR / default_name(resp, fmt))
    if not path.is_absolute():
        path = OUTPUT_DIR.parent / path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(export(resp, fmt), encoding="utf-8")
    return str(path)


def reload_data() -> None:
    """改了 CSV / registry 之后调用：清数据层缓存 + 城市注册表缓存。"""
    clear_caches()
    from data.cities import reload

    reload()
