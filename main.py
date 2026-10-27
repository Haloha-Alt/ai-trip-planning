# @author: 甲
# @reviewer: 乙
# @since: 2026-10-27
"""命令行入口：跑通整条链路（请求 -> 规划 -> 打印 -> 落盘 JSON）。

常用命令（项目根目录）：

    .venv\\Scripts\\python.exe main.py
    .venv\\Scripts\\python.exe main.py --scenario demo --strategy random
    .venv\\Scripts\\python.exe main.py --request mock/case_basic.json
    .venv\\Scripts\\python.exe main.py --interests 自然 --budget 80
    .venv\\Scripts\\python.exe main.py --query "想走走自然类的地方，别太赶"

结果同时打印到终端，并落盘到 outputs/plan_<场景>_<策略>.json。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.config import list_scenarios, load_scenario  # noqa: E402
from engine.models import PlanRequest             # noqa: E402
from engine.planner import plan                   # noqa: E402
from engine.strategies import available           # noqa: E402


def _split(s: str | None) -> list[str]:
    if not s:
        return []
    return [x.strip() for x in s.replace("，", ",").split(",") if x.strip()]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="main.py",
        description="AI 出行与规划 · 命令行入口",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：python main.py --scenario demo --strategy stub --interests 自然",
    )
    p.add_argument("--scenario", default=None, help="场景名，可选：%s" % list_scenarios())
    p.add_argument("--strategy", default=None, help="算法名，可选：%s" % available())
    p.add_argument("--request", default=None, help="请求 JSON 文件路径，例如 mock/case_basic.json")
    p.add_argument("--query", default=None, help="自然语言需求（骨架阶段仅留痕，不解析）")
    p.add_argument("--days", type=int, default=None)
    p.add_argument("--start", dest="start_time", default=None, help='开始时间 "HH:MM"')
    p.add_argument("--end", dest="end_time", default=None, help='结束时间 "HH:MM"')
    p.add_argument("--budget", type=float, default=None, help="预算；0 或省略 = 不限制")
    p.add_argument("--top-n", dest="top_n", type=int, default=None, help="期望站点数")
    p.add_argument("--interests", default=None, help="兴趣词，逗号分隔")
    p.add_argument("--avoid", default=None, help="排除词，逗号分隔")
    p.add_argument("--pace", default=None, choices=["relaxed", "normal", "packed"])
    p.add_argument("--out", default=None, help="输出 JSON 路径，默认 outputs/plan_<场景>_<策略>.json")
    return p


def make_request(args) -> PlanRequest:
    """构建请求。优先级：--query（走 llm/parser）> --request 文件 > 默认值，
    最后再用命令行单项参数覆盖 —— 单项参数永远最大。"""
    if args.query:
        from llm.parser import parse

        req = parse(args.query, load_scenario(args.scenario))
    elif args.request:
        path = Path(args.request)
        if not path.is_absolute():
            path = ROOT / path
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        req = PlanRequest.from_dict(data.get("request", data))
    else:
        req = PlanRequest()

    # 命令行单项参数覆盖
    for field, value in (
        ("days", args.days),
        ("start_time", args.start_time),
        ("end_time", args.end_time),
        ("top_n", args.top_n),
        ("pace", args.pace),
    ):
        if value is not None:
            setattr(req, field, value)
    if args.budget is not None:
        req.daily_budget = None if args.budget <= 0 else args.budget
    if args.interests is not None:
        req.interests = _split(args.interests)
    if args.avoid is not None:
        req.avoid = _split(args.avoid)
    return req


def print_response(resp) -> None:
    print("=" * 60)
    print("场景 %s | 策略 %s | 生成于 %s"
          % (resp.scenario, resp.strategy, resp.generated_at))
    print("=" * 60)

    for d in resp.days:
        print("\n[第 %d 天] 站点 %d 个 | 花费 %.2f | 停留 %d 分钟 | 在途 %.1f 分钟 | 距离 %.2f km"
              % (d.day_index, len(d.items), d.total_cost, d.total_stay_min,
                 d.total_travel_min, d.total_distance_km))
        if not d.items:
            print("  （空行程 —— 通常意味着时间窗太短或候选被过滤光了）")
        for it in d.items:
            t = it.transport
            move = "" if t.mode == "start" else "（%s %.1f 分钟 / %.2f km）" % (
                t.mode, t.minutes, t.distance_km)
            print("  %d. %s-%s  %s%s  花费%.2f" % (
                it.seq, it.arrive, it.leave, it.name, move, it.cost))
            print("     理由：%s" % it.reason)

    m = resp.metrics
    print("\n[指标] 满意度 %.2f | 预算友好 %.2f | 时间利用 %.2f | 路程紧凑 %.2f | 可行性 %.2f"
          % (m.satisfaction, m.cost, m.time, m.distance, m.feasibility))
    print("[总分] %.4f" % m.overall())

    if resp.warnings:
        print("\n[提示] 共 %d 条：" % len(resp.warnings))
        for w in resp.warnings:
            print("  - %s" % w)
    else:
        print("\n[提示] 无")


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    req = make_request(args)

    try:
        resp = plan(req, scenario=args.scenario, strategy=args.strategy)
    except KeyError as e:
        print("[错误] %s" % e)
        return 2

    print_response(resp)

    out = Path(args.out) if args.out else Path("outputs") / (
        "plan_%s_%s.json" % (resp.scenario, resp.strategy))
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(resp.to_json(), encoding="utf-8")
    print("\n已保存：%s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
