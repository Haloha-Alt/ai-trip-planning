# @author: 助手
# @since: 2026-11-04
"""校区 POI 裁剪：把高德抓回来的"全校名"点收敛成真正的校内点位。

为什么要裁
----------
`tools/new_city.py --fetch-pois` 按关键词抓，城市名 + 校名往往带回一堆
**不在主校区里的点**：二校区（隔城 4 km）、附属中小学、校外科技园、
同名连锁店……直接进包会让访客路线被拉成跨城通勤。

裁剪规则（全部是数据操作，不含业务语义）：
1. 只保留距校区中心 `--max-km` 半径内的点（haversine 直线距离）；
2. 名称命中 `--drop` 关键词的点直接剔除（如「二校区」「科技园」「附属」）；
3. 重名再去一次重（抓取时按名去重过，这里兜底跨关键词的重复）。

用法：
    python -m tools.trim_campus_pois --campus hit --max-km 1.6 --drop 二校区
    python -m tools.trim_campus_pois --campus tsinghua --max-km 1.5 --drop 科技园

中心坐标默认从 data/cities/registry.toml 读该 key 的 lat/lon（校区包的
registry 条目中心就是校园中心），也可用 --center-lat/--center-lon 覆盖。

退出码：0 正常；1 参数或文件问题。**改前先自动备份一份 .bak。**
"""

from __future__ import annotations

import argparse
import csv
import math
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CAMPUSES_DIR = ROOT / "data" / "campuses"
REGISTRY = ROOT / "data" / "cities" / "registry.toml"


def _center_from_registry(key: str) -> tuple[float, float] | None:
    try:
        text = REGISTRY.read_text(encoding="utf-8")
        # 先抠出整个 section（aliases 里也有方括号，不能只用 [^\[] 挡）
        m = re.search(r"\[cities\.%s\](.*?)(?=\n\[cities\.|\Z)" % key, text, re.S)
        if not m:
            return None
        sec = m.group(1)
        mlat = re.search(r"lat\s*=\s*([0-9.]+)", sec)
        mlon = re.search(r"lon\s*=\s*([0-9.]+)", sec)
        if mlat and mlon:
            return float(mlat.group(1)), float(mlon.group(1))
    except OSError:
        pass
    return None


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="校区 POI 裁剪（半径 + 名称黑名单）")
    ap.add_argument("--campus", required=True, help="校区 key，如 hit / tsinghua")
    ap.add_argument("--max-km", type=float, default=1.6, help="距校区中心的最大半径（km）")
    ap.add_argument("--drop", default="", help="名称黑名单关键词，逗号分隔，如 二校区,科技园")
    ap.add_argument("--center-lat", type=float, default=0.0, help="覆盖中心纬度")
    ap.add_argument("--center-lon", type=float, default=0.0, help="覆盖中心经度")
    a = ap.parse_args(argv)

    poi = CAMPUSES_DIR / a.campus / "pois.csv"
    if not poi.exists():
        print("[fail] 找不到 %s" % poi)
        return 1

    if a.center_lat and a.center_lon:
        center = (a.center_lat, a.center_lon)
    else:
        center = _center_from_registry(a.campus)
        if center is None:
            print("[fail] registry 里没有 %s 的中心坐标，请用 --center-lat/--center-lon 指定" % a.campus)
            return 1

    drops = [w.strip() for w in a.drop.split(",") if w.strip()]
    with poi.open("r", encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f)
        header = list(rd.fieldnames or [])
        rows = list(rd)
    if not header:
        print("[fail] %s 表头为空" % poi)
        return 1

    kept: list[dict] = []
    removed_far: list[str] = []
    removed_drop: list[str] = []
    seen: set[str] = set()
    for r in rows:
        name = (r.get("name") or "").strip()
        if any(w in name for w in drops):
            removed_drop.append("%s %s" % (r.get("poi_id"), name))
            continue
        try:
            lat, lon = float(r["lat"]), float(r["lon"])
        except (TypeError, ValueError):
            removed_drop.append("%s %s（坐标坏）" % (r.get("poi_id"), name))
            continue
        if _haversine_km(center[0], center[1], lat, lon) > a.max_km:
            removed_far.append("%s %s" % (r.get("poi_id"), name))
            continue
        if name in seen:
            removed_drop.append("%s %s（重名）" % (r.get("poi_id"), name))
            continue
        seen.add(name)
        kept.append(r)

    # 改前备份
    bak = poi.with_suffix(".csv.bak")
    shutil.copy2(poi, bak)

    buf = []
    w = csv.DictWriter(_StringIO(buf), fieldnames=header, lineterminator="\n")
    w.writeheader()
    for r in kept:
        w.writerow({k: r.get(k, "") for k in header})
    poi.write_text("".join(buf), encoding="utf-8")

    print("[ ok ] %s：保留 %d / 原 %d（半径 %.1f km，中心 %.4f,%.4f）"
          % (a.campus, len(kept), len(rows), a.max_km, center[0], center[1]))
    for line in removed_drop:
        print("   [drop] %s" % line)
    for line in removed_far:
        print("   [far ] %s" % line)
    print("[ ok ] 备份在 %s；不满意可用它恢复" % bak.relative_to(ROOT))
    return 0


class _StringIO:
    """极简 StringIO 替身：csv.DictWriter 只要 write() 就够。"""

    def __init__(self, sink: list[str]):
        self._sink = sink

    def write(self, s: str) -> int:
        self._sink.append(s)
        return len(s)


if __name__ == "__main__":
    raise SystemExit(main())
