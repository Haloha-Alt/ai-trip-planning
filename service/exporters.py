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


def _hhmm_plus_min(hhmm: str, minutes: int) -> int:
    """把 "HH:MM" 解析成当天分钟数再加 minutes（跨 24 点不处理）。"""
    try:
        h, mi = hhmm.split(":")
        return int(h) * 60 + int(mi) + int(minutes or 0)
    except Exception:
        return 0


def _last_train_ok(resp: PlanResponse) -> bool:
    """判定散场打车能否赶上末班车：末站离开 + 返程在途 <= 最早一班末车。"""
    items = [it for d in resp.days for it in d.items]
    trains = resp.meta.get("last_trains") or []
    rleg = resp.meta.get("return_leg") or {}
    if not items or not trains or rleg.get("minutes") is None:
        return False
    arrive = _hhmm_plus_min(items[-1].leave, rleg["minutes"])
    times = [_hhmm_plus_min(t.get("last_time") or "", 0) for t in trains
             if t.get("last_time")]
    if not times:
        return False
    return arrive <= min(times)


def to_markdown(resp: PlanResponse) -> str:
    """人能直接读、能粘进微信/文档的行程单（v1.6.4 详细版 v3）。

    参照真实数据版行程单的骨架：
      标题（日期星期）+ 原话导语 → 每日总表（含"这一站做什么"）→ 逐段交通
      → 每站详解（怎么玩/为什么排这）→ 预算明细 → 关键提醒（天气/出发/锚点/收尾）
      → Plan B 速查（引擎从候选池推导的室内备选）→ 数据口径与来源。
    所有数字仍全部来自行程与 meta，不新增任何事实；拿不到的数据如实说拿不到。
    """
    m = resp.metrics
    req = resp.request
    out: list[str] = []

    head = "# 行程单"
    if req.date:
        try:
            wd = "周" + "一二三四五六日"[
                datetime.strptime(req.date, "%Y-%m-%d").weekday()]
            head += "（%s · %s）" % (req.date, wd)
        except ValueError:
            head += "（%s）" % req.date
    out.append(head)
    out.append("")
    if req.raw_query:
        out.append("> 按你的原话编排：“%s”" % req.raw_query)
        out.append("")
    out.append("- 场景：`%s` ｜ 算法：`%s`" % (resp.scenario, resp.strategy))
    if req.city:
        out.append("- 城市：`%s`" % req.city)
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

        # ---- 1) 行程总表：多一列"这一站做什么"，照抄数据层 tips（截断）----
        out.append("| # | 时间 | 站点 | 这一站做什么 | 停留 | 花费 | 交通 |")
        out.append("|---|---|---|---|---|---|---|")
        for it in d.items:
            t = it.transport
            move = "起点" if t.mode == "start" else "%s %.0f 分钟 / %.2f km" % (
                t.mode, t.minutes, t.distance_km)
            tips = (it.tips or "").strip()
            if len(tips) > 42:
                tips = tips[:42] + "…"
            out.append("| %d | %s-%s | %s | %s | %d 分钟 | %.0f | %s |"
                       % (it.seq, it.arrive, it.leave, it.name, tips,
                          it.stay_min, it.cost, move))
        out.append("")
        out.append("合计：花费 %.2f ｜ 停留 %d 分钟 ｜ 在途 %.1f 分钟 ｜ 距离 %.2f km"
                   % (d.total_cost, d.total_stay_min, d.total_travel_min, d.total_distance_km))
        out.append("")

        # ---- 2) 逐段交通（真实数据版"交通与费用"列的展开）----
        out.append("### 逐段交通")
        out.append("")
        oleg_md = resp.meta.get("origin_leg") or {}
        prev_name = req.origin or "出发地"
        for it in d.items:
            t = it.transport
            if t.mode == "start":
                if req.origin and oleg_md.get("km") is not None:
                    out.append("- 第 1 段：从 **%s** 打车约 %.1f km / %d 分钟，"
                               "车费 %.0f 元（%s），%s 到达第 1 站 %s"
                               % (req.origin, oleg_md["km"],
                                  oleg_md.get("minutes") or 0,
                                  oleg_md.get("fare") or 0,
                                  oleg_md.get("basis") or "估算口径",
                                  it.arrive, it.name))
                elif req.origin:
                    # 出发地不在点位库里时引擎算不出在途：如实展示，不编分钟数
                    out.append("- 第 1 段：从 **%s** 出发 → **%s**（%s 到达；"
                               "出发地未入库，第 1 段在途未计入合计）"
                               % (req.origin, it.name, it.arrive))
                else:
                    out.append("- 第 1 段：当天从 **%s** 开始（%s 到达）" % (it.name, it.arrive))
            elif t.minutes == 0 or t.distance_km < 0.3:
                out.append("- %s → %s：同商圈步行即达（约 %.1f km），%s 到达"
                           % (prev_name, it.name, t.distance_km, it.arrive))
            else:
                fare = "，车费 %.0f 元" % t.cost if t.cost else ""
                out.append("- %s → %s：%s 约 %.0f 分钟 / %.2f km%s，%s 到达"
                           % (prev_name, it.name, t.mode, t.minutes, t.distance_km,
                              fare, it.arrive))
            prev_name = it.name
        out.append("")

        # ---- 3) 每站详解：怎么玩（数据层 tips）+ 为什么排这（引擎 reason）----
        rain_swap = resp.meta.get("rain_swap") or {}
        out.append("### 每站详解")
        out.append("")
        for it in d.items:
            out.append("- **%s–%s %s**（%s，停留 %d 分钟）" % (
                it.arrive, it.leave, it.name, it.category or "未分类", it.stay_min))
            if it.address:
                out.append("  - 地址：%s" % it.address)
            if it.tips:
                out.append("  - 怎么玩：%s" % it.tips)
            if rain_swap and it.name == rain_swap.get("to"):
                out.append("  - 为什么排这：因雨由「%s」自动换入的室内备选（原站时刻不变）"
                           % (rain_swap.get("from") or "户外站"))
            elif it.reason:
                out.append("  - 为什么排这：%s" % it.reason)
        out.append("")

    # ---- 4) 预算明细（按分类汇总站点花费，交通单列；人均口径跟随请求）----
    stops_by_cat: dict[str, float] = {}
    transport_cost = 0.0
    total_cost = 0.0
    for d in resp.days:
        for it in d.items:
            stops_by_cat[it.category or "未分类"] = \
                stops_by_cat.get(it.category or "未分类", 0.0) + it.cost
            transport_cost += it.transport.cost
        total_cost += d.total_cost
    # 打车费：出发腿必加；返程腿只有"末班车判定赶得上"时不加（赶不上时是备用支出）
    oleg_b = resp.meta.get("origin_leg") or {}
    rleg_b = resp.meta.get("return_leg") or {}
    origin_fare = oleg_b.get("fare") or 0.0
    return_fare = 0.0 if _last_train_ok(resp) else (rleg_b.get("fare") or 0.0)
    if total_cost or stops_by_cat:
        ppl = max(1, resp.request.travelers or 1)
        out.append("## 预算明细")
        out.append("")
        out.append("| 项目 | 金额（元） |")
        out.append("|---|---|")
        for cat in sorted(stops_by_cat, key=lambda c: -stops_by_cat[c]):
            out.append("| %s（站点） | %.2f |" % (cat, stops_by_cat[cat]))
        out.append("| 交通 | %.2f |" % transport_cost)
        if origin_fare:
            out.append("| 出发打车（出租车估算） | %.2f |" % origin_fare)
        if return_fare:
            out.append("| 返程打车·赶不上末班时备用（出租车估算） | %.2f |" % return_fare)
        out.append("| **合计** | **%.2f** |" % (total_cost + origin_fare + return_fare))
        if ppl > 1 and resp.request.budget_basis == "per_person":
            out.append("| 人均（%d 人） | %.2f |"
                       % (ppl, (total_cost + origin_fare + return_fare) / ppl))
        out.append("")
        out.append("> 口径：站点花费为 POI 数据里的人均估算，交通费来自实测矩阵或配置单价，"
                   "均为预估，请以实际为准。")
        out.append("")

    # ---- 5) 关键提醒：天气 / 出发 / 锚点硬时刻 / 收尾富余，全是能算能查的事实 ----
    anchors = [a for a in (req.anchors or []) if isinstance(a, dict)]
    weather = (resp.realtime or {}).get("weather") or []
    if anchors or req.origin or weather or req.end_time:
        # 锚点里写的可能是 poi_id（如 DL207）：能对上行程站点就换成人类可读的名字
        known = {}
        for d in resp.days:
            for it in d.items:
                known[it.poi_id] = it.name
                known.setdefault(it.name, it.name)
        out.append("## 关键提醒（锁定时刻）")
        out.append("")

        if weather:
            cond = {"clear": "晴", "cloudy": "多云", "rain": "雨",
                    "snow": "雪", "storm": "雷雨"}
            txt = "；".join(
                "%s %s%s" % (w.get("date", ""),
                             cond.get(w.get("condition"), w.get("condition", "")),
                             "" if w.get("temp_c") is None
                             else " %.0f℃" % w["temp_c"])
                for w in weather[:3])
            bad = any(w.get("condition") in ("rain", "snow", "storm")
                      for w in weather[:3])
            if bad:
                out.append("- 天气（实时快照）：%s。**当天预报有雨雪**，户外段务必备伞，"
                           "可参考下方 Plan B 速查改室内。" % txt)
            else:
                out.append("- 天气（实时快照）：%s。有户外段时备伞和防风外套。" % txt)
        else:
            out.append("- 天气：本次未获取实时数据，出发前请自行查当天预报；"
                       "行程含户外段时建议备伞和防风外套。")

        if req.origin:
            first = next((it for d in resp.days for it in d.items), None)
            if first is not None:
                oleg = resp.meta.get("origin_leg") or {}
                if oleg.get("km") is not None:
                    out.append("- 出发：从 **%s** 打车约 %.1f km / %d 分钟（约 ¥%.0f，"
                               "口径：%s，以实际为准），%s 到达第 1 站 %s。"
                               % (req.origin, oleg["km"], oleg.get("minutes") or 0,
                                  oleg.get("fare") or 0, oleg.get("basis") or "估算",
                                  first.arrive, first.name))
                else:
                    out.append("- 出发：从 **%s** 出发，%s 到达第 1 站 %s"
                               "（第 1 段在途见逐段交通）。" % (req.origin, first.arrive, first.name))

        for a in anchors:
            label = a.get("label") or "预订"
            poi = known.get(a.get("poi") or "", a.get("poi") or "")
            out.append("- **%s %s**：%s（停留 %s 分钟）—— 时刻已锁定，"
                       "建议按行程在途时间提前出发" % (
                           label, a.get("time") or "", poi,
                           a.get("duration_min") or ""))

        if req.end_time:
            items = [it for d in resp.days for it in d.items]
            if items:
                last = items[-1]

                def _min(hhmm: str) -> int:
                    try:
                        h, mi = hhmm.split(":")
                        return int(h) * 60 + int(mi)
                    except Exception:
                        return 0

                limit = _min(req.deadline or req.end_time)
                end = _min(last.leave)
                trains = resp.meta.get("last_trains") or []
                rleg = resp.meta.get("return_leg") or {}
                tail = ""
                if trains:
                    parts = []
                    for t in trains[:3]:
                        parts.append("%s%s %s %s 末班 %s（核对于 %s）" % (
                            t.get("kind") or "", t.get("line") or "",
                            t.get("station") or "", t.get("direction") or "",
                            t.get("last_time") or "", t.get("source_date") or ""))
                    tail = "；附近末班：" + "；".join(parts)
                    if _last_train_ok(resp) and rleg.get("minutes") is not None:
                        arrive = _hhmm_plus_min(last.leave, rleg["minutes"])
                        earliest = min(
                            _hhmm_plus_min(t.get("last_time") or "", 0)
                            for t in trains if t.get("last_time"))
                        tail += ("。打车约 %d 分钟到站，赶 %s 的末班（富余 %d 分钟），"
                                 "建议散场即动身"
                                 % (rleg["minutes"],
                                    min((t.get("last_time") or "" for t in trains
                                         if t.get("last_time")),
                                        key=lambda s: _hhmm_plus_min(s, 0)),
                                    earliest - arrive))
                    elif rleg.get("fare") is not None:
                        tail += "。赶不上末班则打车回校约 ¥%.0f（%s，以实际为准）。" % (
                            rleg["fare"], rleg.get("basis") or "估算")
                elif rleg.get("fare") is not None:
                    tail = "；赶时间可打车回校约 ¥%.0f。" % rleg["fare"]
                else:
                    tail = "；公共交通末班车时刻暂未入库，赶末班车请提前查证。"
                if limit >= end:
                    out.append("- 收尾：末站 %s 于 %s 结束，距 %s 的硬约束富余约 %d 分钟%s"
                               % (last.name, last.leave, req.deadline or req.end_time,
                                  limit - end, tail))
                else:
                    out.append("- 收尾：末站 %s 于 %s 结束，已超出 %s 的硬约束，"
                               "请压缩前面的停留。" % (last.name, last.leave,
                                                      req.deadline or req.end_time))
        out.append("")

    # ---- 5.4) 碎片时间建议（E3）：有任务被装填才写这一节，没有就不写 ----
    task_usage = resp.meta.get("task_usage") or {}
    task_items = [(it, t) for d in resp.days for it in d.items
                  for t in (it.tasks or [])]
    if task_items:
        out.append("## 碎片时间建议")
        out.append("")
        out.append("> 口径：只在移动片（在途 5-20 分钟）与等待片（等开门）里装"
                   "「不占手不占眼」的事，不占地点停留；有效利用时间占比 %.0f%%。"
                   % (100.0 * (task_usage.get("usage") or 0)))
        for it, t in task_items:
            where = ("在途 %s→%s" % ("上一站", it.name)
                     if it.transport.mode != "start" else "出发段")
            if it.wait_min:
                where = "%s（等开门 %d 分钟）" % (it.name, it.wait_min)
            out.append("- %s（约 %d 分钟）：**%s**——%s"
                       % (where, t.get("duration_min") or 0,
                          t.get("name") or "", t.get("desc") or ""))
        out.append("")

    # ---- 5.5) Plan B 速查：引擎从候选池推导的室内备选，查不到就不写这一节 ----
    plan_b = resp.meta.get("plan_b") or []
    if plan_b:
        out.append("## Plan B 速查")
        out.append("")
        for b in plan_b:
            out.append("- %s：**%s** 改为 %s"
                       % (b.get("reason", "天气差时"),
                          "、".join(b.get("instead_of") or []),
                          " 或 ".join(b.get("backups") or [])))
        out.append("")

    if resp.warnings:
        out.append("## 提示")
        out.append("")
        for w in resp.warnings:
            out.append("- %s" % w)
        out.append("")

    # ---- 6) 数据口径与来源：在途多少是实测的、站点数据从哪来、哪些还没核实 ----
    data_meta = resp.meta.get("data") or {}
    pending = resp.meta.get("pending") or []
    scope = "该城市" if resp.scenario == "date" else "该场景"
    out.append("## 数据口径")
    out.append("")
    if data_meta.get("matrix_edges"):
        out.append("- 在途时间：实测路网 %d 条边（%s），其余为直线距离 + 配置时速估算。"
                   % (data_meta["matrix_edges"],
                      "、".join(data_meta.get("matrix_modes") or ["未知方式"])))
    else:
        out.append("- 在途时间：%s暂无实测路网，全部为直线距离 + 配置时速估算。" % scope)
    out.append("- 站点数据（位置/开放时间/价格/怎么玩）：来自项目内置数据包%s，"
               "“怎么玩”一栏随数据维护更新，不是模型现编的。"
               % ("（%s）" % data_meta["city"] if data_meta.get("city") else ""))
    if pending:
        out.append("- 待核实 %d 项：下列站点的开放时间与价格取自种子数据，出发前建议再确认："
                   % len(pending))
        out.extend("  - %s" % x for x in pending)
    else:
        out.append("- 待核实：本次用到的站点均带来源适用日期。")
    out.append("")
    out.append("> 注：锚点场次与订座以商家当日实际为准；交通费为估算，高峰/恶劣天气可能上浮。")
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


def to_ics(resp: PlanResponse) -> str:
    """v1.1(B1)：iCalendar，可直接导进日历（Google/Outlook/手机日历）。"""
    def _dt(date: str, hhmm: str) -> str:
        d = (date or "19700101").replace("-", "")
        return "%sT%s00" % (d, (hhmm or "00:00").replace(":", ""))

    now = _dt("19700101", "00:00")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0",
             "PRODID:-//ai-trip-planner//v1.1//CN", "CALSCALE:GREGORIAN"]
    for d in resp.days:
        # day.date 引擎不一定填（黄金基线锁死），导出层兜底用请求日期
        day_date = d.date or getattr(resp.request, "date", "") or ""
        for it in d.items:
            summary = it.name + ("（%s）" % it.category if it.category else "")
            desc_parts = []
            if it.reason:
                desc_parts.append("为什么排这：%s" % it.reason)
            if it.tips:
                desc_parts.append("怎么玩：%s" % it.tips)
            if it.wait_min:
                desc_parts.append("含等开门 %d 分钟" % it.wait_min)
            lines += [
                "BEGIN:VEVENT",
                "UID:%s-%d-%d@ai-trip-planner" % (day_date or "na", d.day_index, it.seq),
                "DTSTAMP:%s" % now,
                "DTSTART:%s" % _dt(day_date, it.arrive),
                "DTEND:%s" % _dt(day_date, it.leave),
                "SUMMARY:%s" % summary.replace(",", "\\,"),
                "LOCATION:%s" % (it.address or "").replace(",", "\\,"),
                "DESCRIPTION:%s" % "；".join(desc_parts).replace(",", "\\,"),
                "END:VEVENT",
            ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def to_geojson(resp: PlanResponse) -> str:
    """v1.1(B1)：GeoJSON FeatureCollection，能直接贴进地图工具看点位与顺序。"""
    features = []
    for d in resp.days:
        for it in d.items:
            if not it.lat and not it.lon:
                continue
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point",
                             "coordinates": [it.lon, it.lat]},  # GeoJSON 是 [经度,纬度]
                "properties": {
                    "day": d.day_index, "seq": it.seq, "name": it.name,
                    "arrive": it.arrive, "leave": it.leave,
                    "category": it.category, "cost": it.cost,
                    "mode": it.transport.mode,
                },
            })
    return _json_safe({"type": "FeatureCollection", "features": features})


def _md_inline_html(md: str) -> str:
    """极简 md→HTML（标题/表格/列表/粗体），零依赖，只为单文件行程单够用。"""
    import re
    out: list[str] = []
    in_table = False
    for raw in md.splitlines():
        line = raw.rstrip()
        esc = (line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
        esc = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", esc)
        if line.startswith("|") and line.endswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(set(c) <= set("-: ") and c for c in cells):
                continue  # 表头分隔行
            if not in_table:
                out.append("<table>")
                in_table = True
                out.append("<tr>" + "".join("<th>%s</th>" % c for c in cells) + "</tr>")
            else:
                out.append("<tr>" + "".join("<td>%s</td>" % c for c in cells) + "</tr>")
            continue
        if in_table:
            out.append("</table>")
            in_table = False
        if line.startswith("### "):
            out.append("<h3>%s</h3>" % esc[4:])
        elif line.startswith("## "):
            out.append("<h2>%s</h2>" % esc[3:])
        elif line.startswith("# "):
            out.append("<h1>%s</h1>" % esc[2:])
        elif line.startswith("- "):
            out.append("<li>%s</li>" % esc[2:])
        elif line.startswith("  - "):
            out.append('<li class="sub">%s</li>' % esc[4:])
        elif line.startswith("> "):
            out.append("<blockquote>%s</blockquote>" % esc[2:])
        elif not line:
            continue
        else:
            out.append("<p>%s</p>" % esc)
    if in_table:
        out.append("</table>")
    return "\n".join(out)


def to_html_single(resp: PlanResponse) -> str:
    """v1.1(B1)：单文件离线 HTML 行程单——零外部依赖，断网双击就能打开。"""
    body = _md_inline_html(to_markdown(resp))
    return (
        "<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        "<title>行程单</title>\n<style>\n"
        "body{font-family:'Microsoft YaHei',sans-serif;max-width:860px;margin:0 auto;"
        "padding:16px;line-height:1.6;color:#222;background:#fafafa}\n"
        "table{border-collapse:collapse;width:100%;margin:8px 0;font-size:14px}\n"
        "th,td{border:1px solid #ccc;padding:5px 8px;text-align:left}\n"
        "th{background:#f0f0f0}\nblockquote{border-left:4px solid #8ab;"
        "margin:8px 0;padding:4px 12px;color:#555;background:#f4f8fa}\n"
        "li{margin:3px 0}li.sub{list-style:circle;margin-left:20px}\n"
        "h1{font-size:22px}h2{font-size:18px;border-bottom:1px solid #ddd;"
        "padding-bottom:4px}h3{font-size:16px}\n"
        "</style>\n</head>\n<body>\n" + body + "\n"
        "<hr><p style=\"color:#999;font-size:12px\">由 ai-trip-planner 生成 · "
        "单文件离线版 · 数据口径见文末</p>\n</body>\n</html>\n"
    )


def export(resp: PlanResponse, fmt: str = "json") -> str:
    """按格式导出；未知格式回退 json（不抛异常，页面不能因为导出崩掉）。"""
    fmt = (fmt or "json").lower()
    if fmt == "md" or fmt == "markdown":
        return to_markdown(resp)
    if fmt == "csv":
        return to_csv(resp)
    if fmt == "ics":
        return to_ics(resp)
    if fmt == "geojson":
        return to_geojson(resp)
    if fmt == "html":
        return to_html_single(resp)
    if fmt == "json":
        return to_json(resp)
    return to_json(resp)


def dumps_meta(resp: PlanResponse) -> str:
    """给日志/提示条用的一行摘要。"""
    return "场景 %s | 策略 %s | 站点 %d | 总分 %.4f" % (
        resp.scenario, resp.strategy,
        sum(len(d.items) for d in resp.days),
        resp.metrics.overall(resp.meta.get("weights") or None),
    )


def _json_safe(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)
