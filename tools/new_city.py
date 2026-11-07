# @author: 甲
# @reviewer: 乙
# @since: 2026-11-01
"""新城市接入流水线：一条命令把一座城市"装"进项目。

为什么要工具化
--------------
按 `data/cities/registry.toml` 的约定，加一座城市本该只动两处：
`data/cities/<key>/pois.csv` + registry 里一段 `[cities.<key>]`。
但手工做有四类坑，每一次都要重踩：

1. **表头漏列**：`pois.csv` 有 18 列，少一列不会报错，只会在某个字段上默默变默认值；
2. **id 撞车**：`poi_id` 是矩阵的引用键，重复了会让两站共用同一段耗时；
3. **city 字段写错**：`pois.csv` 里的 `city` 必须等于 key，否则 bbox 过滤会把整城筛掉；
4. **注册表漏登记**：目录建了但 registry 没加 —— 页面上根本看不到这座城市。

所以：**脚手架负责建对**，`--check` 负责查错，`--fetch-pois` 负责从高德抓真实 POI。

三个子命令
----------
    # 建骨架（不联网）：目录 + 带表头的空 pois.csv + registry 登记
    python -m tools.new_city --scaffold suzhou --name 苏州 --lat 31.2989 --lon 120.5853

    # 抓 POI（联网，需 MAP_API_KEY；先 --dry-run 看看会抓到什么）
    python -m tools.new_city --fetch-pois suzhou --keyword 图书馆 --keyword 食堂 --dry-run

    # 体检：registry 与目录是否一致、表头对不对、id 有没有撞、坐标是否越界
    python -m tools.new_city --check

抓到 POI 之后，再用现成的 `tools/amap_matrix.py` 拉真实路网：

    python -m tools.amap_matrix --city suzhou --mode walk --max-km 2.0

退出码：0 正常；1 有问题（`--check` 发现问题也算 1，方便挂 CI）。
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from service.bootstrap import DATA_DIR, load_dotenv  # noqa: E402

load_dotenv()

CITIES_DIR = DATA_DIR / "cities"
CAMPUSES_DIR = DATA_DIR / "campuses"
REGISTRY = CITIES_DIR / "registry.toml"

#: `pois.csv` 的列顺序 —— **改这里必须同步 data/cities.py 的读取逻辑**
POI_HEADER = [
    "poi_id", "name", "category", "lat", "lon", "open_time", "close_time",
    "stay_min", "cost", "tags", "score", "city", "address", "source", "url",
    "closed_weekdays", "cost_unit", "source_date",
]

#: 表头里必须有值的列（其余允许空，读取时会用默认值兜底）
REQUIRED_FIELDS = ["poi_id", "name", "category", "lat", "lon", "city"]

#: 不同类别给不同的默认停留时长（分钟）。抓来的 POI 未必带营业信息，先用常理值兜住。
DEFAULT_STAY = {
    "图书馆": 120, "自习室": 120, "教学楼": 90, "食堂": 45, "餐厅": 60,
    "咖啡厅": 45, "体育馆": 90, "公园": 90, "咖啡": 45, "书店": 60,
}
STAY_FALLBACK = 60


# ------------------------------------------------------------------ 小工具

def slug_ok(key: str) -> bool:
    """城市 key 只能是 `小写字母 + 数字 + 下划线`，和 registry 里现有的一致。"""
    return bool(re.fullmatch(r"[a-z][a-z0-9_]{1,30}", key or ""))


def city_dir(key: str) -> Path:
    return CITIES_DIR / key


def read_registry() -> str:
    try:
        return REGISTRY.read_text(encoding="utf-8")
    except OSError:
        return ""


def is_registered(key: str) -> bool:
    return ("[cities.%s]" % key) in read_registry()


def register_city(key: str, name: str, lat: float, lon: float,
                  aliases: list[str] | None = None) -> bool:
    """在 registry 里追加一段。已登记就返回 False（幂等，重复跑不会写两遍）。"""
    if is_registered(key):
        return False
    text = read_registry()
    if text and not text.endswith("\n"):
        text += "\n"
    al = aliases or [name, key]
    block = (
        "\n[cities.%s]\n"
        'name = "%s"\n'
        'aliases = [%s]\n'
        "lat = %.4f\n"
        "lon = %.4f\n"
        'poi = "cities/%s/pois.csv"\n'
        'matrix = "cities/%s/travel_matrix.csv"\n'
    ) % (key, name, ", ".join('"%s"' % a for a in al), lat, lon, key, key)
    REGISTRY.write_text(text + block, encoding="utf-8")
    return True


def write_csv(path: Path, rows: list[dict], header: list[str]) -> None:
    """按固定列序写 CSV；`newline=""` 防止 Windows 上多出空行。"""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=header, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in header})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(buf.getvalue(), encoding="utf-8")


def read_csv(path: Path) -> tuple[list[str], list[dict]]:
    """读 CSV。用 `utf-8-sig` 是为了吃掉 Windows 记事本/PyCharm 写出的 **BOM** ——
    否则首列表头会变成 `\\ufeffpoi_id`，一比较就全错（这个坑当场踩到过）。
    """
    if not path.exists():
        return [], []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f)
        return list(rd.fieldnames or []), list(rd)


# ------------------------------------------------------------------ 脚手架

def scaffold(key: str, name: str, lat: float, lon: float,
             aliases: list[str] | None = None) -> int:
    if not slug_ok(key):
        print("[fail] 城市 key 不合法：%s（只能小写字母开头 + 字母数字下划线）" % key)
        return 1
    d = city_dir(key)
    poi = d / "pois.csv"
    d.mkdir(parents=True, exist_ok=True)

    if poi.exists():
        print("[skip] %s 已存在，不覆盖（要重来请先手动备份）" % poi)
    else:
        write_csv(poi, [], POI_HEADER)
        print("[ ok ] 建了空骨架：%s" % poi.relative_to(ROOT))

    added = register_city(key, name, lat, lon, aliases)
    print("[ ok ] registry %s [cities.%s]" % ("已追加" if added else "已有，跳过", key))

    print("\n下一步（按顺序）：")
    print("  1) 抓 POI：python -m tools.new_city --fetch-pois %s --keyword 图书馆 --keyword 食堂" % key)
    print("  2) 拉路网：python -m tools.amap_matrix --city %s --mode walk --max-km 2.0" % key)
    print("  3) 自检：  python -m tools.new_city --check")
    return 0


# ------------------------------------------------------------------ 抓 POI

def _amap_get(path: str, params: dict[str, str], timeout: float = 15.0) -> dict:
    """调高德 Web 服务。**永不抛异常**：失败返回 {}，由上层报人话。"""
    import json
    import os

    key = (os.environ.get("MAP_API_KEY") or "").strip()
    if not key:
        return {}
    q = dict(params, key=key)
    url = "https://restapi.amap.com/v3/" + path + "?" + urllib.parse.urlencode(q)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            payload = json.loads(r.read().decode("utf-8", "replace") or "{}")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _category_of(amap_type: str) -> str:
    """"科教文化服务;图书馆;图书馆" → "图书馆"（取最细一级）。"""
    parts = [p for p in (amap_type or "").split(";") if p.strip()]
    return parts[-1].strip() if parts else "其他"


def _stay_of(category: str) -> int:
    return DEFAULT_STAY.get(category, STAY_FALLBACK)


def row_from_amap(poi: dict, key: str, seq: int, today: str | None = None) -> dict | None:
    """把高德返回的一条 POI 转成我们 `pois.csv` 的一行。拿不到坐标就返回 None。"""
    loc = (poi.get("location") or "").split(",")
    if len(loc) != 2:
        return None
    try:
        lon, lat = float(loc[0]), float(loc[1])
    except ValueError:
        return None
    name = (poi.get("name") or "").strip()
    if not name:
        return None

    cat = _category_of(poi.get("type", ""))
    biz = poi.get("biz_ext") or {}
    if isinstance(biz, list):          # 高德偶尔返回 []，坑过
        biz = {}

    return {
        "poi_id": "%s%03d" % (key[:2].upper(), seq),
        "name": name,
        "category": cat,
        "lat": "%.4f" % lat,
        "lon": "%.4f" % lon,
        "open_time": "",
        "close_time": "",
        "stay_min": str(_stay_of(cat)),
        "cost": str(biz.get("cost") or "0"),
        "tags": "|".join(dict.fromkeys([p for p in (poi.get("type") or "").split(";") if p.strip()])),
        "score": "0.60",
        "city": key,
        "address": (poi.get("address") or "").strip(),
        "source": "amap",
        "url": "",
        "closed_weekdays": "",
        "cost_unit": "per_person",
        "source_date": today or date.today().isoformat(),
    }


def fetch_pois(key: str, keywords: list[str], limit: int = 25,
               dry_run: bool = False, sleep: float = 0.3,
               city_cn: str = "", campus: bool = False) -> int:
    """抓 POI。两个多校区扩展参数（docs/14）：

    - ``city_cn``：高德 `city` 参数用的**城市名**。高校数据包的 registry name
      是校名（"哈尔滨工业大学"），拿它做 citylimit 查询什么都查不到，
      所以抓校区包时必须显式传 "哈尔滨"/"北京" 这类真城市名。
    - ``campus``：写 ``data/campuses/<key>/pois.csv``（校区包）而不是
      ``data/cities/<key>/pois.csv``。id 前缀与 city 字段仍用 key，
      与注册表、bbox 校验保持一致。
    """
    import os

    if not (os.environ.get("MAP_API_KEY") or "").strip():
        print("[fail] 没有 MAP_API_KEY，先填 .env（服务平台要选「Web服务」）")
        return 1
    if not is_registered(key):
        print("[fail] 城市 %s 还没登记，先跑 --scaffold" % key)
        return 1

    if not city_cn:
        text = read_registry()
        m = re.search(r"\[cities\.%s\][^\[]*?name\s*=\s*\"([^\"]+)\"" % key, text)
        city_cn = m.group(1) if m else key

    got: list[dict] = []
    seen: set[str] = set()
    for kw in keywords:
        payload = _amap_get("place/text", {
            "keywords": kw, "city": city_cn, "citylimit": "true",
            "offset": str(min(25, limit)), "page": "1", "extensions": "base",
        })
        infocode = str(payload.get("infocode", ""))
        if infocode and infocode != "10000":
            print("[warn] 「%s」查询失败：%s %s" % (kw, payload.get("info"), infocode))
            continue
        for p in (payload.get("pois") or []):
            nm = (p.get("name") or "").strip()
            if not nm or nm in seen:
                continue
            row = row_from_amap(p, key, len(got) + 1)
            if row:
                seen.add(nm)
                got.append(row)
        time.sleep(sleep)                      # QPS 节流，别把配额打光

    print("[ ok ] 抓到 %d 个 POI（去重后）" % len(got))
    if dry_run:
        for r in got[:10]:
            print("   %-10s %-16s %s,%s  停留%s分钟" %
                  (r["poi_id"], r["name"][:14], r["lat"], r["lon"], r["stay_min"]))
        if len(got) > 10:
            print("   … 另有 %d 个" % (len(got) - 10))
        print("\n[提示] --dry-run 不写文件。确认没问题去掉该参数即可。")
        return 0

    poi = (CAMPUSES_DIR if campus else CITIES_DIR) / key / "pois.csv"
    old_header, old_rows = read_csv(poi)
    merged = old_rows + got
    write_csv(poi, merged, POI_HEADER)
    print("[ ok ] 写入 %s（原有 %d + 新增 %d = %d 行）"
          % (poi.relative_to(ROOT), len(old_rows), len(got), len(merged)))
    print("\n下一步：python -m tools.amap_matrix --city %s --mode walk --max-km 2.0" % key)
    return 0


# ------------------------------------------------------------------ 体检

def check() -> int:
    """把"新人最容易踩的坑"逐条查一遍。返回问题数量。"""
    problems = 0
    text = read_registry()
    keys = re.findall(r"\[cities\.([a-z0-9_]+)\]", text)
    if not keys:
        print("[fail] registry 里一个城市都没有")
        return 1
    print("== registry 登记了 %d 座城市：%s ==" % (len(keys), "、".join(keys)))

    for key in keys:
        d = city_dir(key)
        poi = d / "pois.csv"
        if not poi.exists():
            print("[fail] %s：缺 pois.csv" % key)
            problems += 1
            continue

        header, rows = read_csv(poi)
        if header != POI_HEADER:
            missing = [c for c in POI_HEADER if c not in header]
            extra = [c for c in header if c not in POI_HEADER]
            # 只对**必需列**判死；可选列缺失给 warn —— 老城市（如北京）就是少列的历史数据，
            # 读取时会用默认值兜底，功能不受影响，不该因此判定"数据坏了"。
            hard_missing = [c for c in REQUIRED_FIELDS if c in missing]
            if hard_missing:
                print("[fail] %s：表头缺必需列 %s" % (key, hard_missing))
                problems += 1
            elif missing or extra:
                print("[warn] %s：表头与标准略有差异（缺 %s / 多 %s），读取时会用默认值兜底"
                      % (key, missing or "无", extra or "无"))

        ids: set[str] = set()
        for r in rows:
            miss = [f for f in REQUIRED_FIELDS if not (r.get(f) or "").strip()]
            if miss:
                print("[fail] %s：%s 缺字段 %s" % (key, r.get("poi_id") or r.get("name") or "?", miss))
                problems += 1
                break
            if r["poi_id"] in ids:
                print("[fail] %s：poi_id 重复 %s" % (key, r["poi_id"]))
                problems += 1
                break
            ids.add(r["poi_id"])
            if r.get("city") != key:
                print("[fail] %s：%s 的 city 字段是「%s」，应为「%s」" % (key, r["poi_id"], r.get("city"), key))
                problems += 1
                break
            try:
                lat, lon = float(r["lat"]), float(r["lon"])
            except (TypeError, ValueError):
                print("[fail] %s：%s 坐标不是数字" % (key, r["poi_id"]))
                problems += 1
                break
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                print("[fail] %s：%s 坐标越界 (%s, %s)" % (key, r["poi_id"], lat, lon))
                problems += 1
                break

        matrix = d / "travel_matrix.csv"
        if not matrix.exists():
            print("[warn] %s：没有 travel_matrix.csv（在途时间会按直线估算，页面上会如实说明）" % key)
        else:
            _, mrows = read_csv(matrix)
            print("[ ok ] %s：%d 个 POI、%d 条边" % (key, len(rows), len(mrows)))

    print("\n%s" % ("全部通过" if problems == 0 else "发现 %d 个问题" % problems))
    return problems


# ------------------------------------------------------------------ 命令行

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="新城市接入流水线")
    ap.add_argument("--scaffold", metavar="KEY", help="建城市骨架（不联网）")
    ap.add_argument("--name", default="", help="中文名，配合 --scaffold")
    ap.add_argument("--lat", type=float, default=0.0, help="城市中心纬度")
    ap.add_argument("--lon", type=float, default=0.0, help="城市中心经度")
    ap.add_argument("--aliases", default="", help="别名，逗号分隔")
    ap.add_argument("--fetch-pois", metavar="KEY", help="从高德抓 POI 写进 pois.csv")
    ap.add_argument("--keyword", action="append", default=[], help="搜索关键词，可重复")
    ap.add_argument("--limit", type=int, default=25, help="每个关键词最多抓多少条")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写文件")
    ap.add_argument("--city-cn", default="", help="高德 city 参数用的城市名（抓校区包时必须传，如 哈尔滨）")
    ap.add_argument("--campus", action="store_true", help="写入 data/campuses/<key>/pois.csv（高校数据包）")
    ap.add_argument("--check", action="store_true", help="体检所有已登记城市")
    a = ap.parse_args(argv)

    if a.check:
        return 1 if check() else 0
    if a.fetch_pois:
        if not a.keyword:
            print("[fail] --fetch-pois 至少要给一个 --keyword")
            return 1
        return fetch_pois(a.fetch_pois, a.keyword, a.limit, a.dry_run,
                          city_cn=a.city_cn, campus=a.campus)
    if a.scaffold:
        if not a.name or not (a.lat and a.lon):
            print("[fail] --scaffold 需要 --name / --lat / --lon")
            return 1
        al = [x.strip() for x in a.aliases.split(",") if x.strip()]
        return scaffold(a.scaffold, a.name, a.lat, a.lon, al)

    print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
