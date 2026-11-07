# @author: 甲
# @reviewer: 乙
# @since: 2026-10-27
"""城市注册表读写：城市 = 数据路由，不是代码分支。

为什么要有这一层
----------------
用户提的第 2 条意见是"旅游地点不局限于大连，要能通用于全国各地"。
最省事的写法是在 engine 里 `if city == "大连": ... else: ...`，
那样每加一个城市都要改引擎 —— 正是本项目明令禁止的"业务语义进代码"。

所以：**城市名/别名/坐标/数据文件路径全部外置到 data/cities/registry.toml**，
engine 与 ui 只认 `city` 这个字符串 key，不认任何具体城市。

对外只暴露这些函数：
    list_cities()                     -> list[str]          可选城市 key（给下拉框用）
    default_city()                    -> str                注册表里 default 指向的 key
    city_info(key)                    -> CityInfo | None    单个城市的元信息
    resolve_city(text)                -> str | None         任意说法 -> key（"大连市"、"dalian"、"去大连玩"）
    poi_path(city, fallback)          -> str                POI 数据文件路径（相对 data/）
    visit_poi_path(city, fallback)    -> str                访客场景的校内点位表（v1.6）
    matrix_path(city, fallback)       -> str                交通矩阵路径（同上）
    save_city_csv(key, rows)          -> str                写入一个城市的 POI 表，返回路径

铁律：
- 读不到注册表、注册表写错 → 打印 warn 并返回空/回退值，**绝不抛异常**（页面不能白屏）。
- 本模块不 import engine/，只依赖标准库 + 本目录的 toml。
"""

from __future__ import annotations

import csv
import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent          # data/
CITY_DIR = ROOT / "cities"
REGISTRY = CITY_DIR / "registry.toml"


@dataclass(frozen=True)
class CityInfo:
    """一个城市的元信息。坐标是城市中心点，不是某个 POI。

    v1.3 起多校区扩展（docs/14）：下拉框的维度从「城市」改成「高校」——
    - kind = "campus" 表示这是**一所高校**的数据包（哈工大、清华……），
      没写 kind 的是城市（如北京），仍可被语义解析命中，但不再出现在高校下拉里；
    - label 是给 UI 看的显示名（dalian 的数据里混着城市与校区两层，label 固定为校名），
      没写 label 就用 name。
    """

    key: str
    name: str = ""
    label: str = ""
    kind: str = ""
    aliases: tuple[str, ...] = ()
    lat: float = 0.0
    lon: float = 0.0
    poi_file: str = ""
    matrix_file: str = ""
    # v1.6 访客多校区：访客场景专用的"校内点位表"，空 = 直接用 poi_file。
    # 放在注册表里而不是场景配置里，才不会出现"换一所高校却读上一所的数据"。
    visit_poi_file: str = ""

    @property
    def display_name(self) -> str:
        """UI 显示名：优先 label（校名），退回 name。"""
        return self.label or self.name


# ---------------------------------------------------------------- 注册表加载

_TABLE: dict[str, CityInfo] | None = None
_DEFAULT: str = ""
_LOADED_MSG = ""


def _norm(text: str) -> str:
    """归一化：去空白、转小写。用于别名匹配，不区分大小写与中英文。"""
    return (text or "").strip().lower()


def _table() -> dict[str, CityInfo]:
    """懒加载注册表。任何异常都退化成空表 + 一条 warn，绝不让调用方崩。"""
    global _TABLE, _DEFAULT, _LOADED_MSG
    if _TABLE is not None:
        return _TABLE

    out: dict[str, CityInfo] = {}
    _DEFAULT = ""
    if not REGISTRY.exists():
        print("[warn] 城市注册表不存在：%s（将只使用默认数据）" % REGISTRY)
        _TABLE = out
        return out

    try:
        with open(REGISTRY, "rb") as f:          # tomllib 必须二进制模式
            raw = tomllib.load(f)
    except Exception as e:                        # noqa: BLE001 - 兜底优先
        print("[warn] 城市注册表解析失败：%s（%s）" % (REGISTRY.name, e))
        _TABLE = out
        return out

    _DEFAULT = str(raw.get("default", "") or "")
    for key, sec in (raw.get("cities") or {}).items():
        try:
            out[str(key)] = CityInfo(
                key=str(key),
                name=str(sec.get("name", key)),
                label=str(sec.get("label", "") or ""),
                kind=str(sec.get("kind", "") or ""),
                aliases=tuple(str(a) for a in sec.get("aliases", []) or []),
                lat=float(sec.get("lat", 0.0) or 0.0),
                lon=float(sec.get("lon", 0.0) or 0.0),
                poi_file=str(sec.get("poi", "") or ""),
                matrix_file=str(sec.get("matrix", "") or ""),
                visit_poi_file=str(sec.get("visit_poi", "") or ""),
            )
        except Exception as e:                    # noqa: BLE001 - 单个城市写错不拖垮整张表
            print("[warn] skip bad city '%s': %s" % (key, e))

    if _DEFAULT and _DEFAULT not in out:
        _DEFAULT = ""
    _TABLE = out
    return out


def reload() -> None:
    """清空注册表缓存。改了 registry.toml 或新增城市数据后调用。"""
    global _TABLE
    _TABLE = None


# ---------------------------------------------------------------- 查询


def list_cities() -> list[str]:
    """列出所有已注册城市 key（字母序），UI 下拉框用。"""
    return sorted(_table())


def list_campuses() -> list[str]:
    """列出所有**高校**数据包的 key（字母序）。

    v1.3 起 UI 的选择维度从「城市」换成「高校」：只返回 registry 里
    `kind = "campus"` 的条目。整个注册表一个 campus 都没有时返回全量
    （老 registry 不用改一字也能工作，行为与以前一致）。
    """
    t = _table()
    campuses = sorted(k for k, v in t.items() if v.kind == "campus")
    return campuses if campuses else sorted(t)


def default_city() -> str:
    """注册表 default 指向的 key；没配就取第一个；都没有返回空串。"""
    t = _table()
    if _DEFAULT:
        return _DEFAULT
    return sorted(t)[0] if t else ""


def city_info(key: str) -> CityInfo | None:
    """按 key 取城市元信息；先解析再取，所以传"大连市"也能命中。"""
    k = resolve_city(key)
    return _table().get(k) if k else None


def resolve_city(text: str) -> str | None:
    """把用户的任意说法解析成城市 key。匹配顺序：key → name → alias（含子串）。

    - "dalian" / "DALIAN" / " 大连 " → "dalian"
    - "大连市"、"我想去大连玩两天" → "dalian"
    - 认不出来返回 None（调用方应当回退到默认数据，而不是报错）
    """
    t = _table()
    if not t:
        return None
    raw = (text or "").strip()
    if not raw:
        return None
    low = _norm(raw)

    if low in t:                                   # 1) 直接就是 key
        return low

    for key, info in t.items():                    # 2) 城市名全等
        if _norm(info.name) == low:
            return key
    for key, info in t.items():                    # 3) 别名全等
        if any(_norm(a) == low for a in info.aliases):
            return key

    hits = []                                      # 4) 子串命中：取最长的那个，短词优先让给长词
    for key, info in t.items():
        for cand in (info.name, *info.aliases):
            c = _norm(cand)
            if len(c) >= 2 and c in low:
                hits.append((len(c), key))
                break
    if hits:
        hits.sort(reverse=True)
        return hits[0][1]
    return None


def _exists(rel: str) -> bool:
    return bool(rel) and (ROOT / rel).exists()


def poi_path(city: str, fallback: str) -> str:
    """城市对应的 POI 文件路径（相对 data/）。城市未知或文件缺失 → 返回 fallback。"""
    info = city_info(city)
    if info is None or not info.poi_file:
        return fallback
    return info.poi_file if _exists(info.poi_file) else fallback


def visit_poi_path(city: str, fallback: str = "") -> str:
    """**访客场景**的点位表路径（v1.6）。

    与 `poi_path` 的区别：访客只在校区内活动，所以优先读注册表里的
    `visit_poi`（校内点位表）；没配就退回 `poi`（高校包本身就是校内点）；
    两级都缺或文件不存在才返回 `fallback`。

    这条路是"高校注册表说了算"：`visit` 场景不再持有任何一所学校的具体路径，
    新增高校只要在注册表登记，访客行程自动跟着换数据。
    """
    info = city_info(city)
    if info is None:
        return fallback
    for rel in (info.visit_poi_file, info.poi_file):
        if _exists(rel):
            return rel
    return fallback


def matrix_path(city: str, fallback: str) -> str:
    """城市对应的交通矩阵路径（相对 data/）。矩阵允许缺失，缺失就用 fallback。"""
    info = city_info(city)
    if info is None or not info.matrix_file:
        return fallback
    return info.matrix_file if _exists(info.matrix_file) else fallback


def ensure_city_data(key: str) -> bool:
    """该城市是否已经有自己的 POI 数据文件。没有就该走降级链去抓取。"""
    info = city_info(key)
    return bool(info and info.poi_file and _exists(info.poi_file))


def has_matrix(key: str) -> bool:
    """该城市是否有**自己的**交通矩阵。

    没有就不要借用别的城市的矩阵：两边的 poi_id 根本不是一套，能命中的概率是 0，
    却会让"这座城市到底有多少实测路网"再也看不出来。宁可全部走直线估算，
    并由 `resp.meta.data.matrix_edges` 如实报 0。
    """
    info = city_info(key)
    return bool(info and info.matrix_file and _exists(info.matrix_file))


# ---------------------------------------------------------------- 写入


# 落盘时的列顺序：先契约必填列，再 v1.2 追加列。未知列排在最后。
_PREFERRED_COLS = (
    "poi_id", "name", "category", "lat", "lon",
    "open_time", "close_time", "stay_min", "cost", "tags", "score",
    "city", "address", "source", "url", "closed_weekdays",
)


def city_csv_path(key: str) -> Path:
    """城市 POI 表的落盘位置：data/cities/<key>/pois.csv。"""
    return CITY_DIR / str(key or "").strip() / "pois.csv"


def save_city_csv(key: str, rows: list[dict]) -> str:
    """写入一个城市的 POI 表（UTF-8 with BOM，与 data/README.md 的约定一致）。

    用于降级链里"抓到了新数据就落盘"，下次直接读本地，不再联网。
    返回写入的绝对路径；rows 为空时不写文件，返回空串。
    """
    if not key or not rows:
        return ""
    path = city_csv_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)

    cols: list[str] = [c for c in _PREFERRED_COLS if any(c in r for r in rows)]
    for r in rows:                                 # 补上没预设到的列，保证不丢数据
        for c in r:
            if c not in cols:
                cols.append(c)

    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})
    reload()
    return str(path)
