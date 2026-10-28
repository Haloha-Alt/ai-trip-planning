# @author: 甲
# @reviewer: 乙
# @since: 2026-10-28
"""多算法对比：同一个请求，把几个策略各跑一遍，摆成一张能直接看的表。

为什么把它放在 service/ 而不是 eval/
--------------------------------------
`eval/baseline.py` 是**离线批量评测**：跑一批用例、写 `eval/results.md`、给人看。
本模块是**在线单次对比**：用户在网页上点一下「算法对比」，当场看"换个算法会差多少"。

两者共用同一套口径（指标取自 `constraints.score`，总分乘上场景权重），
但一个面向"报告"，一个面向"对话"，所以不合并 —— 合并会让两边都变难用。

三条纪律
--------
1. **只跑一遍实时数据**：天气按请求抓一次，所有策略共用同一份 `ctx`。
   否则"对比"就变成"对比天气"，算法之间的差异被环境噪声淹掉。
2. **不写文件**：纯函数，返回 dict。落盘是 `eval/baseline.py` 的事。
3. **未知策略名必须报错**，不许静默跳过 —— 跳过会让对比表少一行，而用户以为跑过了。
"""

from __future__ import annotations

from typing import Any

from engine.models import PlanRequest, PlanResponse
from engine.planner import plan
from engine.strategies import STRATEGIES, available

# 默认基线：故意做笨的随机策略。没有它，"我们的算法更好"就只是一句口号。
DEFAULT_BASELINE = "random"


def _overall(resp: PlanResponse) -> float:
    """总分按场景权重算；拿不到权重就用契约里的内置默认值。

    与 `engine/planner.py` 写进 `meta["weights"]` 的是同一份口径，
    所以网页上的"总分"、导出 JSON 里的"总分"、评测表里的"总分"永远是同一个数。
    """
    return resp.metrics.overall(resp.meta.get("weights") or None)


def plan_row(resp: PlanResponse) -> dict[str, Any]:
    """一个策略跑完之后的成绩单（前端表格的一行）。"""
    days = resp.days or []
    m = resp.metrics
    return {
        "strategy": resp.strategy,
        "items": sum(len(d.items) for d in days),
        "days": len(days),
        "cost": round(sum(d.total_cost for d in days), 2),
        "stay_min": sum(d.total_stay_min for d in days),
        "travel_min": round(sum(d.total_travel_min for d in days), 1),
        "distance_km": round(sum(d.total_distance_km for d in days), 2),
        "satisfaction": round(m.satisfaction, 4),
        "cost_score": round(m.cost, 4),
        "time_score": round(m.time, 4),
        "distance_score": round(m.distance, 4),
        "feasibility": round(m.feasibility, 4),
        "overall": _overall(resp),
        "warnings": len(resp.warnings),
        "warning_list": list(resp.warnings[:5]),
    }


def _pct(new: float, old: float) -> float | None:
    """相对变化（百分数）。基数为 0 时无意义，返回 None 而不是 inf。"""
    if not old:
        return None
    return round((new - old) / abs(old) * 100.0, 1)


def _win_count(row: dict[str, Any], base: dict[str, Any]) -> int:
    """这套方案在多少项上"更好"。

    注意方向：满意度/时间/路程/可行性是越大越好，**花费和在途时间是越小越好**。
    混着比会得出"更贵更慢 = 更好"的荒谬结论，所以这里显式分方向。
    """
    higher_better = ("satisfaction", "cost_score", "time_score", "distance_score")
    lower_better = ("cost", "travel_min", "warnings")
    wins = sum(1 for k in higher_better if row[k] > base[k] + 1e-9)
    wins += sum(1 for k in lower_better if row[k] < base[k] - 1e-9)
    return wins


def compare_plans(req: PlanRequest,
                  scenario: str | None = None,
                  strategies: list[str] | None = None,
                  baseline: str = DEFAULT_BASELINE,
                  ctx=None) -> dict[str, Any]:
    """把 `strategies` 里的每个策略在同一个 `req` 上跑一遍。

    :param strategies: 不传 = 注册表里的全部策略
    :param baseline:   基线的策略名；不在本次对比里就只出表、不出"提升"
    :param ctx:        实时快照（由调用方统一抓一次）
    :raises KeyError:  传了未注册的策略名
    """
    # 注意 `strategies or available()` 的陷阱：空列表是假值，会被悄悄当成"全部"。
    # 这里显式区分 None（没传 = 全部）与 []（传了空 = 调用方搞错了）。
    raw = available() if strategies is None else strategies
    names = [str(s).strip() for s in raw if str(s).strip()]
    if not names:                                   # 传了空列表 = 明确想要"没有"，这是调用方的错
        raise KeyError("至少要选一个算法，可选：%s" % available())

    unknown = [n for n in names if n not in STRATEGIES]
    if unknown:
        raise KeyError("未知策略 %s，可选：%s" % (unknown, available()))

    # 去重但保序：用户可能手滑传了 ["stub", "stub"]
    seen: set[str] = set()
    names = [n for n in names if not (n in seen or seen.add(n))]

    rows: list[dict[str, Any]] = []
    for name in names:
        resp = plan(req, scenario=scenario, strategy=name, ctx=ctx)
        row = plan_row(resp)
        rows.append(row)
        if not scenario:
            scenario = resp.scenario                 # 让结果里带上真实生效的场景名

    base = next((r for r in rows if r["strategy"] == baseline), None)
    best = max(rows, key=lambda r: (r["overall"], -r["cost"]))

    for r in rows:
        r["is_best"] = r is best
        r["wins"] = _win_count(r, base) if base and r is not base else 0
        r["vs_baseline"] = ({} if not base or r is base else {
            "overall_pct": _pct(r["overall"], base["overall"]),
            "cost_pct": _pct(r["cost"], base["cost"]),
            "travel_min_pct": _pct(r["travel_min"], base["travel_min"]),
            "wins": r["wins"],
        })

    rows.sort(key=lambda r: (-r["overall"], r["strategy"]))

    return {
        "scenario": scenario or "",
        "baseline": base["strategy"] if base else "",
        "best": best["strategy"],
        "rows": rows,
        "request": req.to_dict(),
        "summary": _summary(rows, base),
    }


def _summary(rows: list[dict[str, Any]], base: dict[str, Any] | None) -> str:
    """一句话结论，直接印在网页上 —— 答辩时最省口舌的一行。"""
    if not rows:
        return "没有可对比的算法。"
    best = max(rows, key=lambda r: (r["overall"], -r["cost"]))
    if base is None or best["strategy"] == base["strategy"]:
        return "「%s」总分最高（%.4f）。" % (best["strategy"], best["overall"])
    d = _pct(best["overall"], base["overall"])
    if d is None:
        return "「%s」总分 %.4f，高于基线「%s」的 %.4f。" % (
            best["strategy"], best["overall"], base["strategy"], base["overall"])
    return "「%s」总分 %.4f，比基线「%s」高 %.1f%%，在 %d 项指标上更优。" % (
        best["strategy"], best["overall"], base["strategy"], d, best["wins"])
