# @author: 乙
# @reviewer: 甲
# @since: 2026-10-27
"""Streamlit 页面：输入 -> 解析 -> 规划 -> 展示。

启动方式（项目根目录，**不要**对 ui/app.py 点绿色三角）：

    .venv\\Scripts\\python.exe -m streamlit run ui/app.py

或在 PyCharm 里建运行配置：
    Module name = streamlit
    Parameters  = run ui/app.py
    Working directory = 项目根目录

本文件只管交互：
- "怎么画" 在 ui/render.py
- "怎么算" 在 service/usecases.py（与命令行、API 共用同一份）

所以这里出现的每一个业务动作，都应该能在命令行里用一条命令复现。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 必须先把自己所在目录（ui/）从 sys.path 里摘掉，再把项目根放到最前面。
# 否则 `ui/app.py` 会以模块名 `app` 抢先命中，把同名的包挡住 ——
# 这正是之前 `No module named 'app.exporters'; 'app' is not a package` 的成因。
_HERE = str(Path(__file__).resolve().parent)
sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != Path(_HERE)]
sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from service.bootstrap import load_dotenv             # noqa: E402
from service.exporters import export                 # noqa: E402
from service.usecases import (                       # noqa: E402
    city_options,
    options,
    plan_from_text,
    reload_data,
    scenario_config,
)
from ui.render import render_plan                # noqa: E402

load_dotenv()                                    # .env 里的 Key 必须先灌进环境变量

st.set_page_config(page_title="AI 出行与规划", page_icon="🧭", layout="wide")

st.title("🧭 AI 出行与规划")
st.caption("多城市 · 可换算法 · 结果可导出")


# ---------------------------------------------------------------- 侧边栏

_TOUCH_PREFIX = "touched_"
_INPUT_KEYS = ("in_start", "in_end", "in_budget", "in_top_n", "in_pace",
               "in_strategy", "in_city")
_TOUCH_KEYS = ("start", "end", "budget", "top_n", "pace", "city")


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
    for k in _INPUT_KEYS:
        st.session_state.pop(k, None)
    for k in _TOUCH_KEYS:
        st.session_state.pop(_TOUCH_PREFIX + k, None)


def sidebar_inputs(cfg):
    """返回 (start, end, budget, top_n, pace)。默认全部"跟随需求文本"。"""
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


def city_picker(cities: list[dict], default_key: str) -> str:
    """城市下拉框。返回的是 registry 里的 key（不是中文名）。"""
    keys = [c["key"] for c in cities]
    label = {c["key"]: c["name"] for c in cities}
    idx = keys.index(default_key) if default_key in keys else 0
    key = st.selectbox(
        "城市",
        keys,
        index=idx,
        format_func=lambda k: "%s（%s）" % (label.get(k, k), k),
        key="in_city",
        on_change=_mark_touched,
        args=("city",),
        help="城市只是数据路由：换城市就读那份城市的 POI 表，引擎代码一行都不用改。",
    )
    return key


with st.sidebar:
    opts = options()
    # 默认场景必须取后端给的 default_scenario（daily），不能用 index=0 ——
    # 列表是字母序，第一项是 commute，一打开界面就站在"通勤"场景上了。
    _sc = opts["scenarios"]
    _sidx = _sc.index(opts["default_scenario"]) if opts["default_scenario"] in _sc else 0
    scenario = st.selectbox("场景", _sc, index=_sidx,
                            key="in_scenario", on_change=_reset_inputs)
    cfg = scenario_config(scenario)

    # 默认算法取场景配置的 default_strategy，不能用 index=0 ——
    # available() 是字母序，第一个是 greedy（真算法），会让页面悄悄换掉算法。
    _names = opts["strategies"]
    _default = cfg.default_strategy if cfg.default_strategy in _names else _names[0]
    strategy = st.selectbox("算法", _names, index=_names.index(_default), key="in_strategy")

    city_key = city_picker(opts["cities"], opts["default_city"])
    start_time, end_time, budget, top_n, pace = sidebar_inputs(cfg)

    st.divider()
    if st.button("清空数据缓存（改了 CSV 后点）"):
        reload_data()
        st.success("已清空，下次规划会重读 CSV")


# ---------------------------------------------------------------- 主区

query = st.text_area(
    "说说你想怎么玩",
    value="想走走自然类的地方，预算 100 元，别太赶",
    height=90,
    help="当前走规则解析：能识别时间、预算、天数、节奏，以及数据里出现过的标签词。",
)

if st.button("生成行程", type="primary"):
    # 只有"用户手动动过"的控件才进 overrides —— 没动过的保持解析结果。
    # 以前这里是无条件覆盖，导致文本框里写"预算 100 元"被默认值 0 抹成"不限制"。
    overrides = {}
    if _touched("start") and start_time:
        overrides["start_time"] = start_time
    if _touched("end") and end_time:
        overrides["end_time"] = end_time
    if _touched("budget"):
        overrides["daily_budget"] = budget
    if _touched("top_n"):
        overrides["top_n"] = int(top_n)
    if _touched("pace"):
        overrides["pace"] = pace
    if _touched("city") and city_key:
        overrides["city"] = city_key

    try:
        resp = plan_from_text(query, scenario=scenario, strategy=strategy,
                              cfg=cfg, overrides=overrides)
    except KeyError as e:                          # 未知算法名，plan_from_request 抛的
        st.error(str(e).strip('"'))
        st.stop()

    with st.expander("解析出的请求（PlanRequest）"):
        st.json(resp.request.to_dict())

    render_plan(resp)

    st.divider()
    st.subheader("导出")
    d1, d2, d3 = st.columns(3)
    d1.download_button("下载 JSON", export(resp, "json"),
                       file_name="plan.json", mime="application/json")
    d2.download_button("下载 Markdown 行程单", export(resp, "md"),
                       file_name="plan.md", mime="text/markdown")
    d3.download_button("下载 CSV 明细", export(resp, "csv"),
                       file_name="plan.csv", mime="text/csv")
else:
    st.info("填好需求后点「生成行程」。左侧可切换城市、场景与算法。")
