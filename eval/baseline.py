# @author: 甲
# @reviewer: 乙
# @since: 2026-10-27
"""基线对比：把所有策略在所有用例上跑一遍，出指标对照表。

跑法（项目根目录）：

    .venv\\Scripts\\python.exe -m eval.baseline
    .venv\\Scripts\\python.exe -m eval.baseline --scenario demo
    .venv\\Scripts\\python.exe -m eval.baseline --cases mock

输出：终端打印对照表 + 覆盖写入 eval/results.md

**为什么要做这个**：没有对照组，就没法证明你的算法真的有用。
random 就是那个"故意很笨"的对照。等你写好真算法，加进 STRATEGIES 后
这里会自动把它纳入对比，一行代码都不用改。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.models import PlanRequest          # noqa: E402
from engine.planner import plan                # noqa: E402
from engine.strategies import available        # noqa: E402

DEFAULT_CASE_DIR = ROOT / "mock"
RESULT_FILE = ROOT / "eval" / "results.md"

COLUMNS = ["用例", "策略", "站点", "花费", "停留", "在途", "距离km",
           "满意度", "预算", "时间", "路程", "可行性", "总分", "提示数"]


def load_cases(case_dir: Path) -> list[tuple[str, PlanRequest]]:
    """读目录下所有 *.json，支持 {"request": {...}} 或直接是请求体两种写法。"""
    cases = []
    for p in sorted(case_dir.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            req = PlanRequest.from_dict(data.get("request", data))
            cases.append((p.stem, req))
        except Exception as e:
            print("[warn] skip %s: %s" % (p.name, e))
    return cases


def run(scenario: str | None, case_dir: Path) -> list[list]:
    cases = load_cases(case_dir)
    if not cases:
        print("[warn] 没找到任何用例，检查目录：%s" % case_dir)
        return []

    rows: list[list] = []
    for case_name, req in cases:
        for strategy in available():
            resp = plan(req, scenario=scenario, strategy=strategy)
            d = resp.days[0] if resp.days else None
            m = resp.metrics
            rows.append([
                case_name,
                strategy,
                len(d.items) if d else 0,
                round(d.total_cost, 2) if d else 0.0,
                d.total_stay_min if d else 0,
                round(d.total_travel_min, 1) if d else 0.0,
                round(d.total_distance_km, 2) if d else 0.0,
                "%.2f" % m.satisfaction,
                "%.2f" % m.cost,
                "%.2f" % m.time,
                "%.2f" % m.distance,
                "%.2f" % m.feasibility,
                "%.4f" % m.overall(),
                len(resp.warnings),
            ])
    return rows


def print_table(rows: list[list]) -> None:
    if not rows:
        return
    widths = [max(len(str(c)) for c in col) for col in zip(COLUMNS, *rows)]
    fmt = " | ".join("%%-%ds" % w for w in widths)

    print()
    print(fmt % tuple(COLUMNS))
    print("-+-".join("-" * w for w in widths))
    for r in rows:
        print(fmt % tuple(str(x) for x in r))
    print()


def write_md(rows: list[list], scenario: str | None) -> None:
    if not rows:
        return
    head = "| " + " | ".join(COLUMNS) + " |"
    sep = "|" + "|".join(["---"] * len(COLUMNS)) + "|"
    body = ["| " + " | ".join(str(x) for x in r) + " |" for r in rows]

    text = "\n".join([
        "# 基线对比结果",
        "",
        "> 本文件由 `python -m eval.baseline` **自动生成**，不要手改。",
        "> 场景参数：`%s`（None 表示用默认场景）" % (scenario or "默认"),
        "",
        head,
        sep,
        *body,
        "",
        "## 怎么读这张表",
        "",
        "- **总分** = 四维加权 × 可行性，越大越好",
        "- **提示数** 越多说明硬约束违规越多，可行性会被扣分",
        "- `random` 是故意做笨的对照组；你的算法应当全面优于它",
        "",
    ])
    RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)
    RESULT_FILE.write_text(text, encoding="utf-8")
    print("已写入：%s" % RESULT_FILE)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="基线对比：所有策略 × 所有用例")
    ap.add_argument("--scenario", default=None, help="场景名，默认用 demo")
    ap.add_argument("--cases", default=str(DEFAULT_CASE_DIR), help="用例目录，默认 mock/")
    args = ap.parse_args(argv)

    case_dir = Path(args.cases)
    if not case_dir.is_absolute():
        case_dir = ROOT / case_dir

    print("策略：%s" % available())
    print("用例目录：%s" % case_dir)

    rows = run(args.scenario, case_dir)
    print_table(rows)
    write_md(rows, args.scenario)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
