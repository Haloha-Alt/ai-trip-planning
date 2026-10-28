# @author: 甲
# @reviewer: 乙
# @since: 2026-10-27
"""数据层唯一出口。

铁律：只有本模块允许读 data/*.csv。engine 与 ui 都必须通过这里的函数拿数据，
这样将来把 CSV 换成数据库 / 网络接口时，改动只发生在这一个文件里。

对外只暴露四个函数：
    load_pois(cfg, city="")                     -> list[POI]
    load_matrix(cfg, city="")                   -> list[TravelEdge]
    get_poi(poi_id, cfg, city="")               -> POI | None
    get_matrix_edge(a_id, b_id, cfg, city="")   -> Transport | None   （查不到返回 None，由 engine/geo.py 估算兜底）

**city 是数据路由，不是代码分支**（v1.2）：
    city 传了且注册表里有 → 读 data/cities/<key>/pois.csv；
    city 为空 / 未注册 / 文件缺失 → 回退到场景配置里的 poi_file。
    所以存量调用（不传 city）的行为与改造前**逐字一致**，零回归。
"""

from __future__ import annotations

import csv
from pathlib import Path

from data.cities import matrix_path as _city_matrix_path
from data.cities import poi_path as _city_poi_path
from data.cities import resolve_city as _resolve_city
from engine.config import ScenarioConfig, load_scenario
from engine.models import POI, Transport, TravelEdge

ROOT = Path(__file__).resolve().parent          # data/
DATA_DIR = ROOT

# 模块级只读缓存：Streamlit 长驻进程下避免每次交互都重读磁盘。
# 骨架阶段改了 CSV 想立刻生效，调用 clear_cache() 即可（或重启 streamlit）。
# key 用"解析后的文件路径"，城市不同文件就不同，天然不串味。
_POI_CACHE: dict[str, list[POI]] = {}
_MATRIX_CACHE: dict[str, list[TravelEdge]] = {}

# 同一个未注册城市只提醒一次，避免每次交互刷屏
_UNKNOWN_CITY_WARNED: set[str] = set()


def clear_cache() -> None:
    """清空缓存。改了 CSV 之后调用它。"""
    _POI_CACHE.clear()
    _MATRIX_CACHE.clear()


def _resolve(cfg: ScenarioConfig | None) -> ScenarioConfig:
    return cfg if cfg is not None else load_scenario()


def _poi_file_for(city: str, cfg: ScenarioConfig) -> str:
    """按城市挑 POI 文件；城市未知时提醒一次并回退。"""
    resolved = _city_poi_path(city, cfg.poi_file)
    if city and resolved == cfg.poi_file and _resolve_city(city) is None:
        if city not in _UNKNOWN_CITY_WARNED:
            _UNKNOWN_CITY_WARNED.add(city)
            print("[warn] 城市 '%s' 未在 data/cities/registry.toml 注册，回退到默认数据 %s"
                  % (city, cfg.poi_file))
    return resolved


def _matrix_file_for(city: str, cfg: ScenarioConfig) -> str:
    """同上，但矩阵缺失是常态（走 geo 估算兜底），不打印提醒。"""
    return _city_matrix_path(city, cfg.matrix_file)


_ENCODINGS = ("utf-8-sig", "gbk")      # 顺序不能反：utf-8-sig 也能读无 BOM 的 UTF-8


def _read_csv(name: str) -> list[dict]:
    """按 utf-8-sig -> gbk 依次尝试，返回"每行一个 dict"的列表（绝不崩）。

    为什么用标准库 csv 而不是第三方库：
    数据层只读两张小表，csv 完全够用；少一个重依赖，
    就少一类"别人机器上装不上 / DLL 被策略拦"的现场事故 —— 答辩机器不受控，这点很值钱。

    为什么不让异常抛出去：data/README.md 承诺"不会让整个程序崩掉"，
    抛异常会让 Streamlit 页面直接白屏。所以兜底成空表，但错误信息要够响，
    并且在 load_pois / load_matrix 里再补一条"解析出 0 行"的提醒。
    """
    path = DATA_DIR / name
    if not path.exists():
        print("[error] 找不到数据文件：%s" % path)
        return []

    last_err: Exception | None = None
    for enc in _ENCODINGS:
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                return [dict(row) for row in csv.DictReader(f)]
        except UnicodeDecodeError as e:
            last_err = e
            print("[warn] %s 用 %s 解码失败，换下一种编码重试" % (path.name, enc))
        except (csv.Error, OSError) as e:
            print("[warn] %s 读取异常，按空表处理：%s" % (path.name, e))
            return []

    print("[error] %s 无法解码（已尝试 %s）。请用 Excel「另存为 → CSV UTF-8（带 BOM）」后重试。"
          % (path.name, "、".join(_ENCODINGS)))
    if last_err is not None:
        print("[error] 最后一次解码错误：%s" % last_err)
    return []


def _is_empty(v) -> bool:
    """csv 读出来全是字符串，所以"空"= None 或去掉空白后长度为 0。"""
    if v is None:
        return True
    if isinstance(v, str):
        return not v.strip()
    return False


def load_pois(cfg: ScenarioConfig | None = None, city: str = "") -> list[POI]:
    """读取 POI 表。字段缺失/空值一律走默认值，坏行跳过，绝不抛异常。

    city 非空时按 data/cities/registry.toml 路由到该城市的数据文件。
    """
    cfg = _resolve(cfg)
    file = _poi_file_for(city, cfg)
    key = file
    if key in _POI_CACHE:
        return _POI_CACHE[key]

    rows = _read_csv(file)
    pois: list[POI] = []
    for row in rows:
        # 去掉空值，让 dataclass 用默认值兜底
        d = {k: (None if _is_empty(v) else v) for k, v in row.items()}
        d = {k: v for k, v in d.items() if v is not None}
        for required in ("poi_id", "name", "category", "lat", "lon"):
            if required not in d:
                d[required] = "" if required != "lat" and required != "lon" else 0.0
        d["poi_id"] = str(d["poi_id"])
        try:
            pois.append(POI.from_dict(d))
        except Exception as e:                      # 坏行跳过，不拖垮整张表
            print("[warn] skip bad POI row %s: %s" % (d.get("poi_id"), e))

    if not pois:
        # 解析出 0 条通常不是"数据为空"，而是编码错了或列名被改过。
        # 这里不缓存，方便你改完 CSV 直接重跑就生效。
        print("[error] %s 解析出 0 条 POI，请检查编码与列名（对照 data/README.md）" % file)
        return pois

    _POI_CACHE[key] = pois
    return pois


def load_matrix(cfg: ScenarioConfig | None = None, city: str = "") -> list[TravelEdge]:
    """读取距离/耗时矩阵。允许不完整 —— 缺的边由 engine/geo.py 估算。

    city 非空时优先读该城市的矩阵；没有就回退到场景配置的 matrix_file。
    """
    cfg = _resolve(cfg)
    file = _matrix_file_for(city, cfg)
    key = file
    if key in _MATRIX_CACHE:
        return _MATRIX_CACHE[key]

    rows = _read_csv(file)
    edges: list[TravelEdge] = []
    for row in rows:
        d = {k: (None if _is_empty(v) else v) for k, v in row.items()}
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


def get_poi(poi_id: str, cfg: ScenarioConfig | None = None, city: str = "") -> POI | None:
    """按主键取单个 POI，取不到返回 None。"""
    for p in load_pois(cfg, city):
        if p.poi_id == poi_id:
            return p
    return None


def get_matrix_edge(a_id: str, b_id: str, cfg: ScenarioConfig | None = None,
                    city: str = "", mode: str = "") -> Transport | None:
    """查两个 POI 之间的已知交通边。

    - 双向都能查（矩阵里只写了一边也能命中）
    - 查不到返回 None，调用方应当回退到 `engine/geo.py` 的 `estimate_edge()`
    - `mode` 非空时只认该方式的边：矩阵里存的是"步行 112 分钟"，
      就不能拿它去顶"公共交通"方案 —— 那是两套完全不同的时间尺度。
      方式对不上时返回 None，让估算按用户选的方式重算。
    """
    if not a_id or not b_id or a_id == b_id:
        return None
    for e in load_matrix(cfg, city):
        if (e.from_id == a_id and e.to_id == b_id) or (e.from_id == b_id and e.to_id == a_id):
            if mode and e.mode != mode:
                continue
            return Transport(
                mode=e.mode,
                minutes=e.minutes,
                distance_km=e.distance_km,
                cost=e.cost,
            )
    return None
