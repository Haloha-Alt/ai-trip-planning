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
DEFAULT_SCENARIO = "demo"


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
    transport_speed_kmh: dict[str, float] = field(default_factory=dict)
    transport_cost_per_km: dict[str, float] = field(default_factory=dict)
    default_mode: str = "walk"
    weights: dict[str, float] = field(default_factory=dict)
    interest_alias: dict[str, list[str]] = field(default_factory=dict)
    poi_file: str = "pois.csv"
    matrix_file: str = "travel_matrix.csv"
    default_strategy: str = "stub"


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
        transport_speed_kmh=dict(raw.get("transport_speed_kmh", {})),
        transport_cost_per_km=dict(raw.get("transport_cost_per_km", {})),
        default_mode=raw.get("default_mode", "walk"),
        weights=dict(raw.get("weights", {})),
        interest_alias={k: list(v) for k, v in (raw.get("interest_alias") or {}).items()},
        poi_file=dfs.get("pois", "pois.csv"),
        matrix_file=dfs.get("matrix", "travel_matrix.csv"),
        default_strategy=raw.get("default_strategy", "stub"),
    )
