# @author: 甲
# @reviewer: 乙
# @since: 2026-10-27
"""数据层唯一出口。

铁律：只有本模块允许读 data/*.csv。engine 与 ui 都必须通过这里的函数拿数据，
这样将来把 CSV 换成数据库 / 网络接口时，改动只发生在这一个文件里。

对外只暴露四个函数：
    load_pois(cfg)                     -> list[POI]
    load_matrix(cfg)                   -> list[TravelEdge]
    get_poi(poi_id, cfg)               -> POI | None
    get_matrix_edge(a_id, b_id, cfg)   -> Transport | None   （查不到返回 None，由 engine/geo.py 估算兜底）
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from engine.config import ScenarioConfig, load_scenario
from engine.models import POI, Transport, TravelEdge

ROOT = Path(__file__).resolve().parent          # data/
DATA_DIR = ROOT

# 模块级只读缓存：Streamlit 长驻进程下避免每次交互都重读磁盘。
# 骨架阶段改了 CSV 想立刻生效，调用 clear_cache() 即可（或重启 streamlit）。
_POI_CACHE: dict[str, list[POI]] = {}
_MATRIX_CACHE: dict[str, list[TravelEdge]] = {}


def clear_cache() -> None:
    """清空缓存。改了 CSV 之后调用它。"""
    _POI_CACHE.clear()
    _MATRIX_CACHE.clear()


def _resolve(cfg: ScenarioConfig | None) -> ScenarioConfig:
    return cfg if cfg is not None else load_scenario()


def _read_csv(name: str) -> pd.DataFrame:
    """统一以 utf-8-sig 读取，兼容 Excel 另存的带 BOM 文件。"""
    path = DATA_DIR / name
    if not path.exists():
        print("[warn] data file not found: %s" % path)
        return pd.DataFrame()
    return pd.read_csv(path, encoding="utf-8-sig")


def _is_empty(v) -> bool:
    try:
        return v is None or pd.isna(v)
    except (TypeError, ValueError):
        return v is None


def load_pois(cfg: ScenarioConfig | None = None) -> list[POI]:
    """读取 POI 表。字段缺失/空值一律走默认值，坏行跳过，绝不抛异常。"""
    cfg = _resolve(cfg)
    key = cfg.poi_file
    if key in _POI_CACHE:
        return _POI_CACHE[key]

    df = _read_csv(cfg.poi_file)
    pois: list[POI] = []
    for _, row in df.iterrows():
        d = row.to_dict()
        # 去掉空值，让 dataclass 用默认值兜底
        d = {k: (None if _is_empty(v) else v) for k, v in d.items()}
        d = {k: v for k, v in d.items() if v is not None}
        for required in ("poi_id", "name", "category", "lat", "lon"):
            if required not in d:
                d[required] = "" if required != "lat" and required != "lon" else 0.0
        d["poi_id"] = str(d["poi_id"])
        try:
            pois.append(POI.from_dict(d))
        except Exception as e:                      # 坏行跳过，不拖垮整张表
            print("[warn] skip bad POI row %s: %s" % (d.get("poi_id"), e))

    _POI_CACHE[key] = pois
    return pois


def load_matrix(cfg: ScenarioConfig | None = None) -> list[TravelEdge]:
    """读取距离/耗时矩阵。允许不完整 —— 缺的边由 engine/geo.py 估算。"""
    cfg = _resolve(cfg)
    key = cfg.matrix_file
    if key in _MATRIX_CACHE:
        return _MATRIX_CACHE[key]

    df = _read_csv(cfg.matrix_file)
    edges: list[TravelEdge] = []
    for _, row in df.iterrows():
        d = {k: (None if _is_empty(v) else v) for k, v in row.to_dict().items()}
        d = {k: v for k, v in d.items() if v is not None}
        if "from_id" not in d or "to_id" not in d:
            continue
        try:
            edges.append(
                TravelEdge(
                    from_id=str(d["from_id"]),
                    to_id=str(d["to_id"]),
                    mode=str(d.get("mode", "walk")),
                    minutes=float(d.get("minutes", 0.0)),
                    distance_km=float(d.get("distance_km", 0.0)),
                    cost=float(d.get("cost", 0.0)),
                )
            )
        except Exception as e:
            print("[warn] skip bad matrix row: %s" % e)

    _MATRIX_CACHE[key] = edges
    return edges


def get_poi(poi_id: str, cfg: ScenarioConfig | None = None) -> POI | None:
    """按主键取单个 POI，取不到返回 None。"""
    for p in load_pois(cfg):
        if p.poi_id == poi_id:
            return p
    return None


def get_matrix_edge(a_id: str, b_id: str, cfg: ScenarioConfig | None = None) -> Transport | None:
    """查两个 POI 之间的已知交通边。

    - 双向都能查（矩阵里只写了一边也能命中）
    - 查不到返回 None，调用方应当回退到 engine/geo.py 的 estimate_edge()
    """
    if not a_id or not b_id or a_id == b_id:
        return None
    for e in load_matrix(cfg):
        if (e.from_id == a_id and e.to_id == b_id) or (e.from_id == b_id and e.to_id == a_id):
            return Transport(
                mode=e.mode,
                minutes=e.minutes,
                distance_km=e.distance_km,
                cost=e.cost,
            )
    return None
