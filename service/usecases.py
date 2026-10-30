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

from service.bootstrap import OUTPUT_DIR
from engine.config import ScenarioConfig, list_scenarios, load_scenario
from engine.models import PlanRequest, PlanResponse
from engine.planner import clear_caches, plan
from engine.strategies import available


# ---------------------------------------------------------------- 选项


def city_options() -> list[dict[str, Any]]:
    """下拉框要的城市列表：`[{key, name}]`。"""
    from data.cities import city_info, list_cities

    out = []
    for k in list_cities():
        info = city_info(k)
        out.append({"key": k, "name": info.name if info else k})
    return out


def options() -> dict[str, Any]:
    """一次性给齐所有可选项，UI 与 /api/options 共用。

    `default_strategy` 必须由后端给：`available()` 是**字母序**，
    第一个是 `greedy`（真算法），而场景配置里默认是 `stub`。
    前端如果拿"第一个"当默认值，就等于悄悄换了算法 —— 页面的站点顺序
    会和命令行对不上，还容易被误判成算法有 bug（见 `04` AC-44）。
    """
    from data.cities import default_city

    cfg = load_scenario()
    scenarios = list_scenarios()

    # 每个场景各自的默认算法：切场景时前端跟着切，但用户手动选过就不再覆盖
    per_scenario: dict[str, str] = {}
    for name in scenarios:
        try:
            per_scenario[name] = load_scenario(name).default_strategy
        except Exception:                       # noqa: BLE001 - 单个场景坏了不拖垮选项
            per_scenario[name] = cfg.default_strategy

    return {
        "cities": city_options(),
        "scenarios": scenarios,
        "strategies": available(),
        "default_city": default_city(),
        "default_scenario": cfg.name,
        "default_strategy": per_scenario.get(cfg.name, cfg.default_strategy),
        "scenario_strategies": per_scenario,
    }


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
                  "date", "travelers", "budget_basis", "mobility", "start_poi_id"):
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

    return req


def build_request(text: str = "",
                  cfg: ScenarioConfig | None = None,
                  scenario: str | None = None,
                  overrides: dict[str, Any] | None = None) -> PlanRequest:
    """文本 + 控件值 → 一个确定可用的 PlanRequest（不规划，只解析）。

    单独抽出来是因为**实时数据要用它**：抓天气得先知道去哪个城市、几天。
    如果先规划再抓天气，就等于"排完了才问天气预报"，顺序反了。
    """
    from llm.parser import parse

    cfg = cfg or load_scenario(scenario)
    req = parse(text or "", cfg)
    req.raw_query = text or req.raw_query
    req = apply_overrides(req, overrides)

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
    return plan(req, scenario=scenario, strategy=strategy, ctx=ctx)


def plan_from_text(text: str,
                   scenario: str | None = None,
                   strategy: str | None = None,
                   cfg: ScenarioConfig | None = None,
                   overrides: dict[str, Any] | None = None,
                   realtime: bool = False,
                   ctx=None) -> PlanResponse:
    """一句自然语言 → 行程。

    :param realtime: 是否顺带抓实时天气/闭馆（会多花 1 秒左右；断网自动降级）
    :param ctx:      已经准备好的 PlanContext（传了就不再自己抓）
    """
    req = build_request(text, cfg, scenario, overrides)
    if ctx is None and realtime:
        ctx = plan_context_for(req, enabled=True)
    return plan_from_request(req, scenario=scenario, strategy=strategy, ctx=ctx)


# ---------------------------------------------------------------- 解说与局部调整


def llm_client():
    """取大模型客户端。没有 Key 时返回 NullClient（调用方不用判断）。"""
    from llm.client import get_client

    return get_client()


def narrate_plan(resp: PlanResponse, enabled: bool = True) -> PlanResponse:
    """给行程补上主题 / 每站解说 / 一句话汇总。

    `enabled=False` 或没有 Key 时走规则模板 —— **永远有解说，不会因为模型失败就空白**。
    """
    from llm.narrate import narrate

    return narrate(resp, client=llm_client() if enabled else None, use_llm=enabled)


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
