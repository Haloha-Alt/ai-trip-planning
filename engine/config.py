# @author: 甲
# @reviewer: 乙
# @since: 2026-10-27
"""场景配置加载：TOML 文件 -> ScenarioConfig 对象。

业务参数一律不准硬编码进 Python，只能写在 config/scenarios/*.toml 里。
本模块负责把它们读进来，并在文件缺失/字段缺失时给出兜底，保证永不崩溃。
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCENARIO_DIR = ROOT / "config" / "scenarios"
# 场景已锁定为「日常出行」（2026-10-28），所以默认场景是 daily。
# demo 只保留两个用途：骨架/占位数据和 `tests/test_golden.py` 的黄金样例——
# 它是 09:00–18:00 的"游客一日游"口径，与日常出行的时间窗不是一回事，
# 默认值若还指向 demo，网页/命令行打开时看到的会是"日常的点 + 游客的窗口"。
DEFAULT_SCENARIO = "daily"


@dataclass
class ScenarioConfig:
    """一个场景的全部可调参数。字段缺失时用这里的默认值兜底。"""

    name: str = DEFAULT_SCENARIO
    day_start: str = "09:00"
    day_end: str = "18:00"
    daily_budget: float | None = None      # None 或 <=0 表示"不限制预算"
    days: int = 1
    max_poi_per_day: int = 6
    default_pace: str = "normal"
    pace_factor: dict[str, float] = field(default_factory=dict)   # 节奏 -> 每天站数系数
    transport_speed_kmh: dict[str, float] = field(default_factory=dict)
    transport_cost_per_km: dict[str, float] = field(default_factory=dict)
    default_mode: str = "walk"
    weights: dict[str, float] = field(default_factory=dict)
    # 权重档位：{"save": {...}, "fast": {...}, "comfort": {...}}，由 PlanRequest.profile 选
    weight_presets: dict[str, dict[str, float]] = field(default_factory=dict)
    interest_alias: dict[str, list[str]] = field(default_factory=dict)
    poi_file: str = "pois.csv"
    matrix_file: str = "travel_matrix.csv"
    default_strategy: str = "stub"
    # 一句话需求里没说"每天几站"时的默认站数。0 = 用 max_poi_per_day（老行为）。
    # 之所以单独给一个字段：max_poi_per_day 是**上限**（校验用），
    # 拿上限当默认值等于"能塞多少塞多少"，一天会出现 8 站，日常出行没人这么过。
    default_top_n: int = 0

    # ---- v1.6 追加（访客多校区 / 详细行程；全部带默认值，旧场景逐字不变）----
    # 点位表路由口径："" = v1.5 老逻辑（场景显式 > 城市注册表 > 默认）；
    # "campus_visit" = 由高校注册表的 visit_poi 决定读哪份校内点位表。
    poi_routing: str = ""
    # 语义标签归一化：{语义标签: [点位名称/分类里出现的关键词]}。
    # 高德抓来的 tags 是「科教文化服务|学校|高等院校」这类原始分类，
    # 与用户说的「图书馆」「吃饭」对不上；配了这张表才做名称/分类匹配。
    # **不配 = 完全不启用**（老场景、老测试零回归）。
    tag_aliases: dict[str, list[str]] = field(default_factory=dict)
    # 候选池最少点数：命中兴趣的点太少时按质量分 + 类别多样性补足，
    # 避免"只命中 1 个点 → 只排 1 站"或"5 站全是图书馆"。
    # 0 = 不补位（老行为）。
    min_candidates: int = 0

    # ---- v1.6.5 追加：出租车计价（起步价制）。空 = 该场景不做出租车估算 ----
    # {"start_price": 10.0, "start_km": 3.0, "per_km": 2.0}，业务数值只写在场景 toml。
    taxi: dict[str, float] = field(default_factory=dict)
    # 访客停留上限：{POI 分类: 分钟}。高德抓来的点位默认 60/120 分钟，
    # 那是"进去自习/参观"的口径；访客只看外观、不进馆，照抄会一站占两小时。
    # 只压上限、不抬高：数据里本来就短的站保持原样。
    visit_stay_cap: dict[str, int] = field(default_factory=dict)
    visit_stay_default: int = 0       # 未列出分类的兜底上限；0 = 不限制
    # 候选池的"语义大类"：{大类: [分类/名称关键词]}。按大类限量才挡得住
    # 「中餐厅 / 快餐厅 / 星巴克咖啡」被当成 3 个不同类、结果 4 站都在吃饭。
    candidate_groups: dict[str, list[str]] = field(default_factory=dict)
    candidate_exclude_groups: list[str] = field(default_factory=list)
    candidate_exclude_names: list[str] = field(default_factory=list)


def list_scenarios() -> list[str]:
    """列出所有可用场景名（不含扩展名），UI 下拉框用。"""
    if not SCENARIO_DIR.exists():
        return [DEFAULT_SCENARIO]
    return sorted(p.stem for p in SCENARIO_DIR.glob("*.toml"))


def load_scenario(name: str | None = None) -> ScenarioConfig:
    """加载场景。优先级：显式参数 > 环境变量 TRIP_SCENARIO > DEFAULT_SCENARIO。

    找不到文件时回退到内置默认值，不抛异常 —— 骨架阶段宁可跑出默认结果，也不能崩。
    """
    name = name or os.getenv("TRIP_SCENARIO") or DEFAULT_SCENARIO
    path = SCENARIO_DIR / ("%s.toml" % name)

    if not path.exists():
        print("[warn] scenario '%s' not found, fallback to '%s'" % (name, DEFAULT_SCENARIO))
        name = DEFAULT_SCENARIO
        path = SCENARIO_DIR / ("%s.toml" % name)

    if not path.exists():
        print("[warn] scenario dir missing, use builtin defaults")
        return ScenarioConfig()

    with open(path, "rb") as f:            # tomllib 必须以二进制模式读取
        raw = tomllib.load(f)

    dfs = raw.get("data_files", {})
    budget = raw.get("daily_budget", 0.0)

    return ScenarioConfig(
        name=raw.get("name", name),
        day_start=raw.get("day_start", "09:00"),
        day_end=raw.get("day_end", "18:00"),
        daily_budget=None if (budget is None or budget <= 0) else float(budget),
        days=int(raw.get("days", 1)),
        max_poi_per_day=int(raw.get("max_poi_per_day", 6)),
        default_pace=raw.get("default_pace", "normal"),
        pace_factor={k: float(v) for k, v in (raw.get("pace_factor") or {}).items()},
        transport_speed_kmh=dict(raw.get("transport_speed_kmh", {})),
        transport_cost_per_km=dict(raw.get("transport_cost_per_km", {})),
        default_mode=raw.get("default_mode", "walk"),
        weights=dict(raw.get("weights", {})),
        weight_presets={k: {kk: float(vv) for kk, vv in (v or {}).items()}
                        for k, v in (raw.get("weight_presets") or {}).items()},
        interest_alias={k: list(v) for k, v in (raw.get("interest_alias") or {}).items()},
        poi_file=dfs.get("pois", "pois.csv"),
        matrix_file=dfs.get("matrix", "travel_matrix.csv"),
        default_strategy=raw.get("default_strategy", "stub"),
        default_top_n=int(raw.get("default_top_n", 0) or 0),
        poi_routing=str(dfs.get("poi_routing", "") or ""),
        tag_aliases={k: [str(x) for x in v]
                     for k, v in (raw.get("tag_aliases") or {}).items()},
        min_candidates=int(raw.get("min_candidates", 0) or 0),
        visit_stay_cap={k: int(v) for k, v in (raw.get("visit_stay_cap") or {}).items()},
        visit_stay_default=int(raw.get("visit_stay_default", 0) or 0),
        candidate_groups={k: [str(x) for x in v]
                          for k, v in (raw.get("candidate_groups") or {}).items()},
        candidate_exclude_groups=[str(x) for x in
                                  (raw.get("candidate_exclude") or {}).get("groups", [])],
        candidate_exclude_names=[str(x) for x in
                                 (raw.get("candidate_exclude") or {}).get("name_keywords", [])],
        taxi={k: float(v) for k, v in (raw.get("taxi") or {}).items()
              if not isinstance(v, str)},
    )
