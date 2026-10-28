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

from app.bootstrap import OUTPUT_DIR
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
    """一次性给齐所有可选项，UI 与 /api/options 共用。"""
    from data.cities import default_city

    return {
        "cities": city_options(),
        "scenarios": list_scenarios(),
        "strategies": available(),
        "default_city": default_city(),
        "default_scenario": load_scenario().name,
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
                  "date", "travelers", "budget_basis", "mobility"):
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


def plan_from_request(req: PlanRequest,
                      scenario: str | None = None,
                      strategy: str | None = None) -> PlanResponse:
    """结构化请求 → 行程。未知算法名会抛 KeyError，由调用方转成友好提示。"""
    return plan(req, scenario=scenario, strategy=strategy)


def plan_from_text(text: str,
                   scenario: str | None = None,
                   strategy: str | None = None,
                   cfg: ScenarioConfig | None = None,
                   overrides: dict[str, Any] | None = None) -> PlanResponse:
    """一句自然语言 → 行程。

    解析走 `llm/parser.parse`（当前是规则解析，步 4 会接大模型）；
    之后用 overrides 覆盖"用户手动设过"的那些值。
    """
    from llm.parser import parse

    cfg = cfg or load_scenario(scenario)
    req = parse(text or "", cfg)
    req.raw_query = text or req.raw_query
    apply_overrides(req, overrides)
    return plan_from_request(req, scenario=scenario, strategy=strategy)


# ---------------------------------------------------------------- 落盘与缓存


def save_plan(resp: PlanResponse, out_path: str | None = None,
              fmt: str = "json") -> str:
    """导出并落盘，返回写入的绝对路径。目录不存在会自动建。"""
    from pathlib import Path

    from app.exporters import default_name, export

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
