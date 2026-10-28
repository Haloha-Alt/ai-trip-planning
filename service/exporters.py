# @author: 甲
# @reviewer: 乙
# @since: 2026-10-28
"""导出：把 PlanResponse 变成能带走的东西。

当前支持：json / md（Markdown 行程单） / csv（表格）。
后续（步 8）会补：ics（日历）、geojson（地图）、html（单文件离线行程单）。

统一约定：
- 每个导出函数都返回**字符串**，由调用方决定写到哪（CLI 落盘、API 当响应体）。
- 文件名统一由 `default_name()` 生成，避免三处各写各的。
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime

from engine.models import PlanResponse

FORMATS = ("json", "md", "csv")


def default_name(resp: PlanResponse, fmt: str = "json") -> str:
    """输出文件名：`plan_<场景>_<策略>.<ext>`。

    **刻意不带时间戳**：AC-10 / AC-11 的验收命令要求每次都落盘到同一个
    `outputs/plan_demo_stub.json`，带上时间戳会让"改一行重跑再 diff"变得很难受。
    真要留多份，显式传 `--out` 即可。
    """
    return "plan_%s_%s.%s" % (resp.scenario or "demo", resp.strategy or "stub", fmt)


def timestamped_name(resp: PlanResponse, fmt: str = "json") -> str:
    """需要留多份结果时用的文件名：`plan_<场景>_<策略>_<时间戳>.<ext>`。"""
    ts = (resp.generated_at or datetime.now().isoformat(timespec="seconds"))
    ts = ts.replace(":", "").replace("-", "")
    return "plan_%s_%s_%s.%s" % (resp.scenario or "demo", resp.strategy or "stub", ts, fmt)


def to_json(resp: PlanResponse) -> str:
    return resp.to_json()


def to_markdown(resp: PlanResponse) -> str:
    """人能直接读、能粘进微信/文档的行程单。"""
    m = resp.metrics
    out: list[str] = []
    out.append("# 行程单")
    out.append("")
    out.append("- 场景：`%s` ｜ 算法：`%s`" % (resp.scenario, resp.strategy))
    if resp.request.city:
        out.append("- 城市：`%s`" % resp.request.city)
    out.append("- 生成时间：%s" % resp.generated_at)
    out.append("- 总分：**%.4f**（满意度 %.2f ｜ 预算友好 %.2f ｜ 时间利用 %.2f "
               "｜ 路程紧凑 %.2f ｜ 可行性 %.2f）"
               % (m.overall(resp.meta.get("weights") or None),
                  m.satisfaction, m.cost, m.time, m.distance, m.feasibility))
    out.append("")

    for d in resp.days:
        title = "## 第 %d 天" % d.day_index
        if d.date:
            title += "（%s）" % d.date
        if d.weather:
            title += " · 天气：%s" % d.weather
        if d.theme:
            title += " · 主题：%s" % d.theme
        out.append(title)
        out.append("")
        if not d.items:
            out.append("（空行程）")
            out.append("")
            continue
        out.append("| # | 时间 | 站点 | 停留 | 花费 | 交通 |")
        out.append("|---|---|---|---|---|---|")
        for it in d.items:
            t = it.transport
            move = "起点" if t.mode == "start" else "%s %.0f 分钟 / %.2f km" % (
                t.mode, t.minutes, t.distance_km)
            out.append("| %d | %s-%s | %s | %d 分钟 | %.0f | %s |"
                       % (it.seq, it.arrive, it.leave, it.name, it.stay_min, it.cost, move))
        out.append("")
        out.append("合计：花费 %.2f ｜ 停留 %d 分钟 ｜ 在途 %.1f 分钟 ｜ 距离 %.2f km"
                   % (d.total_cost, d.total_stay_min, d.total_travel_min, d.total_distance_km))
        out.append("")

    if resp.warnings:
        out.append("## 提示")
        out.append("")
        for w in resp.warnings:
            out.append("- %s" % w)
        out.append("")

    return "\n".join(out)


def to_csv(resp: PlanResponse) -> str:
    """Excel 能直接打开的明细表。"""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["天", "序号", "站点", "到达", "离开", "停留(分)", "花费",
                "交通方式", "在途(分)", "距离(km)", "纬度", "经度", "理由"])
    for d in resp.days:
        for it in d.items:
            w.writerow([d.day_index, it.seq, it.name, it.arrive, it.leave, it.stay_min,
                        it.cost, it.transport.mode, it.transport.minutes,
                        it.transport.distance_km, it.lat, it.lon, it.reason])
    return buf.getvalue()


def export(resp: PlanResponse, fmt: str = "json") -> str:
    """按格式导出；未知格式回退 json（不抛异常，页面不能因为导出崩掉）。"""
    fmt = (fmt or "json").lower()
    if fmt == "md" or fmt == "markdown":
        return to_markdown(resp)
    if fmt == "csv":
        return to_csv(resp)
    if fmt == "json":
        return to_json(resp)
    return to_json(resp)


def dumps_meta(resp: PlanResponse) -> str:
    """给日志/提示条用的一行摘要。"""
    return "场景 %s | 策略 %s | 站点 %d | 总分 %.4f" % (
        resp.scenario, resp.strategy,
        sum(len(d.items) for d in resp.days), resp.metrics.overall(),
    )


def _json_safe(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)
