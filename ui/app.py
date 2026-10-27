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

from engine.config import list_scenarios, load_scenario  # noqa: E402
from engine.planner import clear_caches, plan     # noqa: E402
from engine.strategies import available           # noqa: E402
from llm.parser import parse                      # noqa: E402
from ui.render import render_plan                 # noqa: E402

st.set_page_config(page_title="AI 出行与规划", page_icon="🧭", layout="wide")

st.title("🧭 AI 出行与规划")
st.caption("工程骨架 · 场景与数据均为占位，可整体替换")


# ---------------------------------------------------------------- 侧边栏

_TOUCH_PREFIX = "touched_"


def _mark_touched(name: str) -> None:
    """控件回调：只有用户真的操作了这个控件才会触发，程序赋值不会。"""
    st.session_state[_TOUCH_PREFIX + name] = True


def _touched(name: str) -> bool:
    """这个控件当前的值，是"用户手动设过"还是"还是默认值"。"""
    return bool(st.session_state.get(_TOUCH_PREFIX + name, False))


def _reset_inputs() -> None:
    """切场景时清空控件值与"动过"标记，让新场景的默认值重新生效。

    Streamlit 的规则：一旦给控件加了 key，后续 rerun 里 value= 会被忽略。
    所以换场景时必须手动把 session_state 里的旧值清掉，否则会残留上一个场景的值。
    """
    for k in ("in_start", "in_end", "in_budget", "in_top_n", "in_pace", "in_strategy"):
        st.session_state.pop(k, None)
    for k in ("start", "end", "budget", "top_n", "pace"):
        st.session_state.pop(_TOUCH_PREFIX + k, None)


def sidebar_inputs(cfg):
    st.header("参数")
    st.caption("默认全部「跟随需求文本」；你手动改过哪一项，才覆盖哪一项。")
    col1, col2 = st.columns(2)
    with col1:
        start = st.text_input("出发时间", value=cfg.day_start, key="in_start",
                              on_change=_mark_touched, args=("start",))
    with col2:
        end = st.text_input("返回时间", value=cfg.day_end, key="in_end",
                            on_change=_mark_touched, args=("end",))

    budget = st.number_input("预算（0 = 不限制；不动则跟随文本）", min_value=0.0,
                             value=0.0, step=10.0, key="in_budget",
                             on_change=_mark_touched, args=("budget",))
    top_n = st.slider("每天站点数", min_value=1, max_value=max(1, cfg.max_poi_per_day),
                      value=min(cfg.max_poi_per_day, 5), key="in_top_n",
                      on_change=_mark_touched, args=("top_n",))
    pace = st.selectbox("节奏", ["relaxed", "normal", "packed"],
                        index=["relaxed", "normal", "packed"].index(cfg.default_pace)
                        if cfg.default_pace in ("relaxed", "normal", "packed") else 1,
                        key="in_pace", on_change=_mark_touched, args=("pace",))
    return start, end, budget, top_n, pace



with st.sidebar:
    scenario = st.selectbox("场景", list_scenarios(), index=0,
                            key="in_scenario", on_change=_reset_inputs)
    cfg = load_scenario(scenario)

    # 默认算法取场景配置的 default_strategy（demo.toml 里是 stub），
    # 不能用 index=0 —— available() 是字母序，第一个是 random（故意做笨的对照组）。
    _names = available()
    _default = cfg.default_strategy if cfg.default_strategy in _names else _names[0]
    strategy = st.selectbox("算法", _names, index=_names.index(_default), key="in_strategy")

    start_time, end_time, budget, top_n, pace = sidebar_inputs(cfg)
    st.divider()
    if st.button("清空数据缓存（改了 CSV 后点）"):
        clear_caches()
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

    # 只有「用户手动动过」的控件才覆盖解析结果。
    # 以前这里是无条件覆盖，导致文本框里写"预算 100 元"被默认值 0 抹成"不限制"。
    if _touched("start") and start_time:
        req.start_time = start_time
    if _touched("end") and end_time:
        req.end_time = end_time
    if _touched("budget"):
        req.daily_budget = None if not budget else float(budget)
    if _touched("top_n"):
        req.top_n = int(top_n)
    if _touched("pace"):
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
