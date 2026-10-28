# @author: 甲
# @reviewer: 乙
# @since: 2026-10-28
"""高德 Web 服务适配层：真实步行 / 公交 / 驾车耗时。

为什么放在 `data/` 而不是 `engine/`
----------------------------------
引擎有一条铁律：**零 IO**（`plan()` 里不碰网络），否则"同样输入 → 同样输出"就不成立，
评测没法比、问题没法回放。

所以本模块的定位是**离线取数工具**：
    data/amap.py  ──(一次)──►  data/cities/<city>/travel_matrix.csv  ──►  engine 只读 CSV
真实耗时落成数据之后，运行时完全不联网，答辩断网也不影响。

三条纪律
--------
1. **永不抛异常**：拿不到就返回 None，由调用方回退到 `engine/geo.py` 的直线估算。
2. **磁盘缓存**：同一个 (起, 讫, 方式) 只请求一次，重跑不重复烧配额。
3. **错误说人话**：高德返回 `status=0` 时把 `info/infocode` 翻成人能懂的一句，
   尤其是"Key 平台类型错 / 未实名 / 配额用尽"这三种——现场最容易踩。

依赖：只用标准库 `urllib`（与全项目"能少一个依赖就少一个"一致）。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / ".cache" / "amap"

BASE = "https://restapi.amap.com/v3/direction"

# 我们的出行方式 → 高德的接口路径
MODE_PATHS = {
    "walk": "walking",
    "transit": "transit/integration",     # 公交/地铁，需要 city 参数
    "drive": "driving",
}

# 高德常见错误码 → 人话
INFO_HINTS = {
    "INVALID_USER_KEY": "Key 无效：检查是否复制完整、是否已生效（新建 Key 有几分钟延迟）",
    "INVALID_USER_SCODE": "Key 与「服务平台」不匹配：本项目要选 Web服务，不是 Web端(JS API)",
    "USERKEY_PLAT_NOMATCH": "Key 平台类型错：本模块调用的是 Web 服务接口，Key 必须属于 Web服务",
    "SERVICE_NOT_AVAILABLE": "服务不可用：多半是账号未完成实名认证",
    "DAILY_QUERY_OVER_LIMIT": "当日配额用尽：明天再跑，或改用缓存里的既有结果",
    "ACCESS_TOO_FREQUENT": "请求过于频繁：降低 QPS 后重试",
    "INVALID_PARAMS": "参数不合法：检查经纬度顺序（高德要 经度,纬度）",
    "NO_ROADS_NEARBY": "起点/终点附近没有可通行道路：坐标可能落到了建筑内部",
}


def api_key() -> str:
    """读 Key。只认环境变量（`.env` 由各入口用 `service.bootstrap.load_dotenv()` 灌进来）。"""
    return (os.environ.get("MAP_API_KEY") or "").strip()


def available() -> bool:
    return bool(api_key())


def _cache_path(origin: tuple[float, float], dest: tuple[float, float],
                mode: str, city: str) -> Path:
    raw = "%.6f,%.6f|%.6f,%.6f|%s|%s" % (origin[0], origin[1], dest[0], dest[1], mode, city)
    key = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    return CACHE_DIR / ("%s.json" % key)


def _lonlat(lat: float, lon: float) -> str:
    """高德的顺序是 **经度,纬度** —— 写反了会跑到海里，且不报错。"""
    return "%.6f,%.6f" % (lon, lat)


def _request(path: str, params: dict[str, str], timeout: float) -> dict[str, Any]:
    # 用 system 的证书与代理设置；只发一次，重试交给上层
    url = BASE + "/" + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "ai-trip-planner/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _explain(payload: dict[str, Any]) -> str:
    code = str(payload.get("infocode") or "")
    info = str(payload.get("info") or "")
    hint = INFO_HINTS.get(info) or INFO_HINTS.get(code)
    return "%s（%s/%s）%s" % (info or "未知错误", code, payload.get("status"),
                             ("→ " + hint) if hint else "")


def route(origin: tuple[float, float], dest: tuple[float, float],
          mode: str = "walk", city: str = "",
          timeout: float = 6.0, retry: int = 1,
          use_cache: bool = True, verbose: bool = False) -> dict[str, Any] | None:
    """查两点之间的真实路线。

    :param origin/dest: `(lat, lon)`，与项目里的坐标顺序一致（高德要经度在前，本函数内部换算）
    :param mode:        `walk` | `transit` | `drive`
    :param city:        公交需要城市名（如 "大连"）；步行/驾车忽略
    :return: `{"mode","minutes","distance_km","cost","source"}`；失败返回 None（永不抛异常）
    """
    mode = (mode or "walk").strip()
    path = MODE_PATHS.get(mode)
    if path is None:
        _warn("不支持的出行方式 %r（可选 %s）" % (mode, sorted(MODE_PATHS)))
        return None

    key = api_key()
    if not key:
        return None

    cp = _cache_path(origin, dest, mode, city)
    if use_cache and cp.exists():
        try:
            cached = json.loads(cp.read_text(encoding="utf-8"))
            cached["cached"] = True
            return cached
        except (ValueError, OSError):
            pass

    params = {"key": key, "origin": _lonlat(*origin), "destination": _lonlat(*dest)}
    if mode == "transit" and city:
        params["city"] = city
        params["cityd"] = city

    last = ""
    for attempt in range(retry + 1):
        try:
            payload = _request(path, params, timeout)
        except Exception as e:                     # noqa: BLE001 - 网络层什么都可能发生
            last = "请求失败：%s" % e
        else:
            if str(payload.get("status")) == "1":
                out = _parse(payload, mode)
                if out is None:
                    last = "返回里没有可用路线"
                else:
                    if use_cache:
                        _write_cache(cp, out)
                    return out
            else:
                last = _explain(payload)
                # 配额/Key 类错误重试没有意义，直接退出
                if str(payload.get("infocode") or "") in (
                        "10001", "10002", "10003", "10004", "10009", "10044"):
                    break
        if attempt < retry:
            time.sleep(0.6 * (attempt + 1))

    _warn("%s→%s (%s) 取不到：%s" % (origin, dest, mode, last))
    return None


def _parse(payload: dict[str, Any], mode: str) -> dict[str, Any] | None:
    """从三种不同的返回结构里抽出 (分钟, 公里, 票价)。单位：高德给米与秒。"""
    routes = (payload.get("route") or {})
    if mode == "transit":
        legs = routes.get("transits") or []
        if not legs:
            return None
        best = legs[0]
        seconds = float(best.get("duration") or 0)
        meters = float(best.get("distance") or 0)
        try:
            cost = float(best.get("cost") or 0)
        except (TypeError, ValueError):
            cost = 0.0
    else:
        paths = routes.get("paths") or []
        if not paths:
            return None
        best = paths[0]
        seconds = float(best.get("duration") or 0)
        meters = float(best.get("distance") or 0)
        cost = 0.0

    if seconds <= 0 and meters <= 0:
        return None
    return {
        "mode": mode,
        "minutes": round(seconds / 60.0, 1),
        "distance_km": round(meters / 1000.0, 2),
        "cost": round(cost, 2),
        "source": "amap",
        "cached": False,
    }


def _write_cache(p: Path, obj: dict[str, Any]) -> None:
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _warn(msg: str) -> None:
    print("[amap] " + msg)


def status() -> dict[str, Any]:
    """一眼看清楚"为什么没生效"：Key 有没有、缓存里攒了多少条。"""
    n = len(list(CACHE_DIR.glob("*.json"))) if CACHE_DIR.exists() else 0
    return {"configured": available(), "key_tail": api_key()[-4:] if api_key() else "",
            "cached_points": n, "modes": sorted(MODE_PATHS)}
