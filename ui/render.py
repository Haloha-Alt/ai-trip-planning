# @author: 乙
# @reviewer: 甲
# @since: 2026-10-27
"""展示层：把 PlanResponse 画成表格 / 指标 / 地图 / 提示。

app.py 只管交互，所有"怎么画"都在这里。这样两人可以并行改：
甲改引擎不影响这里，乙改排版不碰引擎。
"""

from __future__ import annotations

import streamlit as st

from engine.models import PlanResponse

# 这里**刻意不用第三方表格库**：数据层已经是标准库 csv 了（见 data/loader.py 的说明），
# 展示层跟着只传 list[dict]，Streamlit 的 dataframe / map 都能直接吃。
# 少一个重依赖，就少一类"答辩机器上装不上"的现场事故。


def render_metrics(resp: PlanResponse) -> None:
    """五个数 + 总分，一行摆开。"""
    m = resp.metrics
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("满意度", "%.2f" % m.satisfaction)
    c2.metric("预算友好", "%.2f" % m.cost)
    c3.metric("时间利用", "%.2f" % m.time)
    c4.metric("路程紧凑", "%.2f" % m.distance)
    c5.metric("可行性", "%.2f" % m.feasibility)
    c6.metric("总分", "%.4f" % m.overall(resp.meta.get("weights") or None))


def _day_to_rows(day) -> list[dict]:
    rows = []
    for it in day.items:
        t = it.transport
        rows.append({
            "序号": it.seq,
            "到达": it.arrive,
            "离开": it.leave,
            "站点": it.name,
            "停留(分)": it.stay_min,
            "花费": round(it.cost, 2),
            "交通方式": "" if t.mode == "start" else t.mode,
            "在途(分)": 0 if t.mode == "start" else round(t.minutes, 1),
            "距离(km)": 0 if t.mode == "start" else round(t.distance_km, 2),
            "理由": it.reason,
        })
    return rows


def render_day(day) -> None:
    st.markdown("### 第 %d 天" % day.day_index)
    st.caption("站点 %d 个 · 花费 %.2f · 停留 %d 分钟 · 在途 %.1f 分钟 · 距离 %.2f km"
               % (len(day.items), day.total_cost, day.total_stay_min,
                  day.total_travel_min, day.total_distance_km))

    if not day.items:
        st.warning("这一天的行程是空的 —— 通常是时间窗太短，或候选被兴趣/排除条件过滤光了。")
        return

    st.dataframe(_day_to_rows(day), width="stretch", hide_index=True)

    # 路线图：坐标来自 ItineraryItem，UI 不直连数据层
    pts = [{"lat": it.lat, "lon": it.lon} for it in day.items if it.lat or it.lon]
    if pts:
        st.map(pts, width="stretch")


def render_warnings(resp: PlanResponse) -> None:
    if not resp.warnings:
        st.success("无异常提示")
        return
    st.warning("共 %d 条提示：" % len(resp.warnings))
    for w in resp.warnings:
        st.write("- " + w)


def render_raw_json(resp: PlanResponse) -> None:
    """折叠区放原始 JSON，演示和排错时用。"""
    with st.expander("查看原始 JSON（接口契约 v1）"):
        st.code(resp.to_json(), language="json")


def render_pending(resp: PlanResponse) -> None:
    """待核实清单：任务书要求"区分已核实信息与待确认信息"。

    引擎在 `meta["pending"]` 里列了所有"没标来源适用日期"的站点，
    这里必须显示出来 —— 只在报告里说"数据可能有误"不算做到。
    """
    pending = (resp.meta or {}).get("pending") or []
    if not pending:
        st.caption("数据核对：本次用到的站点均带来源适用日期。")
        return
    with st.expander("待核实 %d 项（种子数据，出发前请再确认）" % len(pending), expanded=False):
        for p in pending:
            st.write("- " + p)


def render_data_note(resp: PlanResponse) -> None:
    """数据覆盖情况：没有实测路网就说清楚"在途是直线估算"，别装作量过。"""
    d = (resp.meta or {}).get("data") or {}
    edges = d.get("matrix_edges")
    if edges is None:
        return
    if not edges:
        st.caption("该城市暂无实测路网：在途时间全部按直线距离 + 配置时速估算。")
        return
    modes = "/".join(d.get("matrix_modes") or []) or "未知方式"
    st.caption("实测路网 %d 条边（%s），其余在途时间为直线估算。" % (edges, modes))


def render_plan(resp: PlanResponse) -> None:
    """一次画完整个结果。"""
    st.caption("场景 `%s` · 策略 `%s` · 生成于 %s"
               % (resp.scenario, resp.strategy, resp.generated_at))
    render_metrics(resp)
    st.divider()
    for day in resp.days:
        render_day(day)
    st.divider()
    render_warnings(resp)
    render_data_note(resp)
    render_pending(resp)
    render_raw_json(resp)
