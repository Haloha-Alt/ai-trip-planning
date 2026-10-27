# @author: 乙
# @reviewer: 甲
# @since: 2026-10-27
"""Streamlit 页面骨架：输入 -> 解析 -> 规划 -> 展示。

启动方式（项目根目录，**不要**对ui/app.py 点绿色三角）：

    .venv\\Scripts\\python.exe -m streamlit run ui/app.py

或在 PyCharm 里建运行配置：
    Module name = streamlit
    Parameters  = run ui/app.py
    Working directory = 项目根目录

本文件只管交互，"怎么画"都在 ui/render.py 里。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from data.loader import clear_cache               # noqa: E402
from engine.config import list_scenarios, load_scenario  # noqa: E402
from engine.planner import plan                   # noqa: E402
from engine.strategies import available           # noqa: E402
from llm.parser import parse                      # noqa: E402
from ui.render import render_plan                 # noqa: E402

st.set_page_config(page_title="AI 出行与规划", page_icon="🧭", layout="wide")

st.title("🧭 AI 出行与规划")
st.caption("工程骨架 · 场景与数据均为占位，可整体替换")


# ---------------------------------------------------------------- 侧边栏


def sidebar_inputs(cfg):
    st.header("参数")
    col1, col2 = st.columns(2)
    with col1:
        start = st.text_input("出发时间", value=cfg.day_start)
    with col2:
        end = st.text_input("返回时间", value=cfg.day_end)

    budget = st.number_input("预算（0 = 不限制）", min_value=0.0, value=0.0, step=10.0)
    top_n = st.slider("期望站点数", min_value=1, max_value=max(1, cfg.max_poi_per_day),
                      value=min(cfg.max_poi_per_day, 5))
    pace = st.selectbox("节奏", ["relaxed", "normal", "packed"],
                        index=["relaxed", "normal", "packed"].index(cfg.default_pace)
                        if cfg.default_pace in ("relaxed", "normal", "packed") else 1)
    return start, end, budget, top_n, pace


with st.sidebar:
    scenario = st.selectbox("场景", list_scenarios(), index=0)
    strategy = st.selectbox("算法", available(), index=0)
    cfg = load_scenario(scenario)
    start_time, end_time, budget, top_n, pace = sidebar_inputs(cfg)
    st.divider()
    if st.button("清空数据缓存（改了 CSV 后点）"):
        clear_cache()
        st.success("已清空，下次规划会重读 CSV")


# ---------------------------------------------------------------- 主区

query = st.text_area(
    "说说你想怎么玩",
    value="想走走自然类的地方，预算 100 元，别太赶",
    height=90,
    help="骨架阶段走规则解析：能识别时间、预算、天数、节奏，以及数据里出现过的标签词。",
)

if st.button("生成行程", type="primary"):
    req = parse(query, cfg)

    # 侧边栏参数覆盖解析结果
    req.start_time = start_time or cfg.day_start
    req.end_time = end_time or cfg.day_end
    req.daily_budget = None if not budget else float(budget)
    req.top_n = top_n
    req.pace = pace

    with st.expander("解析出的请求（PlanRequest）"):
        st.json(req.to_dict())

    try:
        resp = plan(req, scenario=scenario, strategy=strategy)
    except KeyError as e:
        st.error(str(e).strip('"'))
        st.stop()

    render_plan(resp)
else:
    st.info("填好需求后点「生成行程」。左侧可切换场景与算法。")
