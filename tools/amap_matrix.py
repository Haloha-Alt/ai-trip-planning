# @author: 甲
# @reviewer: 乙
# @since: 2026-10-28
"""把高德真实耗时写成 `travel_matrix.csv`（离线跑一次，运行时不联网）。

用法（项目根目录）：

    :: 先探一下 Key 能不能用（只跑 3 对，不写文件）
    .venv\\Scripts\\python.exe tools\\amap_matrix.py --mode walk --probe 3

    :: 生成步行耗时（只取直线 2km 内的点对，省配额）
    .venv\\Scripts\\python.exe tools\\amap_matrix.py --mode walk --max-km 2.0

    :: 生成公交耗时（公交接口要城市名，工具自己从 registry 取）
    .venv\\Scripts\\python.exe tools\\amap_matrix.py --mode transit --max-km 8.0

设计要点
--------
- **可重跑、可中断**：已经在 CSV 里的点对直接跳过；跑一半断了，再跑接着来。
- **只补不删**：手工校正过的行不会被覆盖（除非加 `--force`）。
- **配额友好**：先按直线距离排序，近的先取；`--max-km` 直接把远点对挡在门外。
- **失败即停**：连续 3 次失败（往往是 Key 平台错/未实名/配额尽）就收手并说清原因，
  不把 40 个点的错误刷满屏。
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from service.bootstrap import load_dotenv                      # noqa: E402

load_dotenv()                                                  # 必须在读 env 之前

from data import amap                                          # noqa: E402
from data.cities import city_info, default_city                # noqa: E402
from data.loader import load_matrix, load_pois, clear_cache    # noqa: E402
from engine.config import load_scenario                        # noqa: E402
from engine.geo import haversine_km                            # noqa: E402

# 我们的出行方式 → 矩阵里的 mode 值（矩阵的 mode 取值见 config/scenarios/*.toml 的速度表）
MATRIX_MODE = {"walk": "walk", "transit": "bus", "drive": "car"}

HEADER = ["from_id", "to_id", "mode", "minutes", "distance_km", "cost"]


def _matrix_file(city_key: str) -> Path:
    info = city_info(city_key)
    rel = info.matrix_file if info and info.matrix_file else "cities/%s/travel_matrix.csv" % city_key
    return ROOT / "data" / rel


def _load_existing(path: Path) -> list[dict]:
    if not path.exists():
        return []
    for enc in ("utf-8-sig", "gbk"):
        try:
            with path.open(encoding=enc, newline="") as fp:
                return list(csv.DictReader(fp))
        except UnicodeDecodeError:
            continue
        except OSError:
            return []
    return []


def _key(a: str, b: str) -> tuple[str, str]:
    """无向键：矩阵里 A→B 与 B→A 算同一条边（loader 也支持双向命中）。"""
    return (a, b) if a <= b else (b, a)


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=HEADER, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in HEADER})


def build_pairs(pois, max_km: float, min_km: float = 0.0):
    """按直线距离列出待查点对（近的先来，远的后补；太近的跳过）。

    `min_km` 的作用：校区里同一栋楼的两个点间距 10 米，高德会返回 0 分 0 米，
    写进矩阵等于白烧一次配额 —— 这些边让 `engine/geo.py` 按直线估算就够准。
    """
    pairs = []
    for i, a in enumerate(pois):
        for b in pois[i + 1:]:
            km = haversine_km(a.lat, a.lon, b.lat, b.lon)
            if min_km <= km <= max_km:
                pairs.append((km, a, b))
    pairs.sort(key=lambda x: x[0])
    return pairs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="用高德真实耗时生成 travel_matrix.csv")
    ap.add_argument("--city", default="", help="城市 key，默认取 registry 的 default")
    ap.add_argument("--mode", default="walk", choices=sorted(MATRIX_MODE),
                    help="walk / transit / drive")
    ap.add_argument("--max-km", type=float, default=2.0, help="只查直线距离内的点对")
    ap.add_argument("--min-km", type=float, default=0.08,
                    help="太近的点对跳过（同楼栋，高德会给 0 米，白烧配额）")
    ap.add_argument("--limit", type=int, default=0, help="最多查多少对（0 = 不限）")
    ap.add_argument("--sleep", type=float, default=0.25, help="每次请求后的间隔秒数（QPS 节流）")
    ap.add_argument("--probe", type=int, default=0, help="只探测 N 对，不写文件")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的边")
    ap.add_argument("--scenario", default="daily", help="读哪套场景的 POI 数据")
    args = ap.parse_args(argv)

    st = amap.status()
    print("高德状态：configured=%s key=***%s 缓存=%d 条"
          % (st["configured"], st["key_tail"], st["cached_points"]))
    if not st["configured"]:
        print("缺少 MAP_API_KEY —— 在项目根 .env 里写一行 MAP_API_KEY=你的Key")
        return 2

    city_key = args.city or default_city()
    info = city_info(city_key)
    city_name = info.name if info else city_key
    pois = [p for p in load_pois(load_scenario(args.scenario), city_key) if p.lat or p.lon]
    print("城市 %s（%s）· POI %d 个 · 方式 %s · 直线 ≤ %.1f km"
          % (city_key, city_name, len(pois), args.mode, args.max_km))

    path = _matrix_file(city_key)
    rows = _load_existing(path)
    have = {_key(str(r["from_id"]), str(r["to_id"])) for r in rows if r.get("from_id")}
    print("现有矩阵：%s（%d 行）" % (path.relative_to(ROOT), len(rows)))

    pairs = build_pairs(pois, args.max_km, args.min_km)
    todo = [(km, a, b) for km, a, b in pairs if args.force or _key(a.poi_id, b.poi_id) not in have]
    if args.probe:
        todo = todo[: args.probe]
    if args.limit:
        todo = todo[: args.limit]

    print("待查 %d 对（候选池 %d 对，已有 %d 对）" % (len(todo), len(pairs), len(have)))
    if not todo:
        print("没有需要补的点对。")
        return 0

    added, failed, streak = 0, 0, 0
    t0 = time.time()
    for idx, (km, a, b) in enumerate(todo, 1):
        got = amap.route((a.lat, a.lon), (b.lat, b.lon), mode=args.mode, city=city_name)
        if got is None:
            failed += 1
            streak += 1
            if streak >= 3:
                print("连续 %d 次失败，已停止（配额/Key/网络问题，先看上面那条提示）" % streak)
                break
        else:
            streak = 0
            row = {
                "from_id": a.poi_id, "to_id": b.poi_id,
                "mode": MATRIX_MODE[args.mode],
                "minutes": got["minutes"], "distance_km": got["distance_km"],
                "cost": got["cost"],
            }
            if args.probe:
                print("  %-14s → %-14s 直线%.2fkm 真实 %.1f 分 / %.2f km / %.1f 元%s"
                      % (a.name, b.name, km, got["minutes"], got["distance_km"],
                         got["cost"], "（缓存）" if got.get("cached") else ""))
            else:
                rows.append(row)
                added += 1
                if idx % 20 == 0 or idx == len(todo):
                    print("  进度 %d/%d（已写入 %d）· %.0fs"
                          % (idx, len(todo), added, time.time() - t0))
        if args.sleep:
            time.sleep(args.sleep)

    if args.probe:
        print("探测完成：成功 %d / 失败 %d（未写文件）" % (len(todo) - failed, failed))
        return 0 if failed == 0 else 1

    if added:
        _write(path, rows)
        clear_cache()
        print("已写入 %s：新增 %d 行，共 %d 行" % (path.relative_to(ROOT), added, len(rows)))
        print("提示：矩阵是**数据**，改完要重启服务（或点网页上的「清空数据缓存」）才生效")
    print("失败 %d 对（缺的边会由 engine/geo.py 的直线估算兜底，不影响运行）" % failed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
