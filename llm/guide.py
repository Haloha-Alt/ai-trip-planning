# @author: 助手
"""访客简报：把「入校须知（结构化事实）」+「行程」写成给外来者看的一份可执行说明。

设计原则（和 `llm/narrate.py` 一致）
------------------------------------
**模型只组织语言，不产生事实。** 预约渠道、开放时间、电话、公交线路全部来自
`data/cities/<city>/visitor_guide.json`，模型只许引用，不许新增；拿不准就写"需现场确认"。

为什么不用模型直接"讲一段参观攻略"
----------------------------------
因为那正是会编的东西：它会给你一个不存在的预约入口、一个错的闭馆日。
外来者按错的说明跑一趟，比没有说明更糟。所以这里把"事实"和"措辞"分开：
事实在 JSON 里可核查，措辞交给模型。

没有 Key 时走 `_fallback()`：同样输入同样输出，一条不改。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SYSTEM = """你是校园参观向导，只做一件事：把给定的**事实卡**和**行程**写成一段给人看的说明。

硬规则（违反即作废）：
1. 只能使用【事实卡】里出现的渠道名、时间、电话、公交线路、地点；**不许新增任何事实**。
2. 不许承诺"一定可以进""保证有位"；不含价格、不含"最佳""一定"这类绝对说法。
3. 事实卡里标了"需现场确认/以当日公告为准"的，你必须原样提示，不许替它下结论。
4. 时间必须来自【行程】里各站的 arrive/leave，不要自己编时间。
5. 只输出 JSON，不要解释，不要 markdown 代码块。

输出结构：
{"headline": "一句话（≤28字，说清这半天怎么走）",
 "before": ["出发前要做的事，按顺序，2–4 条"],
 "timeline": [{"time": "09:10", "what": "一句话，具体到门/楼"}],
 "per_stop": [{"name": "站点名", "say": "这一站看什么、为什么排在这（≤40字）"}],
 "tips": ["注意事项，2–4 条"],
 "verify": ["需要现场确认或打电话问的，1–3 条"]}"""


def load_guide(city: str = "dalian") -> dict[str, Any]:
    """读访客须知；没有这个城市的文件就返回空 dict（调用方自己降级）。

    v1.3 多校区扩展（docs/14）：优先读高校数据包
    ``data/campuses/<key>/visitor_guide.json``（哈工大、清华这些以高校
    身份注册的条目事实层放校区包里），读不到再回退老路径
    ``data/cities/<key>/visitor_guide.json``（大连的城市包事实层不动）。
    """
    root = Path(__file__).resolve().parents[1] / "data"
    for rel in (("campuses", city, "visitor_guide.json"),
                ("cities", city, "visitor_guide.json")):
        p = root.joinpath(*rel)
        if not p.exists():
            continue
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _plan_digest(resp) -> list[dict[str, str]]:
    out = []
    for d in resp.days:
        for it in d.items:
            out.append({
                "day": str(d.day_index),
                "name": it.name,
                "arrive": it.arrive,
                "leave": it.leave,
                "stay_min": str(it.stay_min),
                "cost": str(it.cost),
                "reason": (it.reason or "")[:40],
            })
    return out


def _facts_for_prompt(guide: dict, stops: list[dict]) -> dict:
    """只挑跟本次行程有关的事实，别把整份须知塞进 prompt（省 token 也更准）。"""
    e = guide.get("entry", {})
    names = " ".join(s["name"] for s in stops)
    spots = [s for s in guide.get("spots", []) if s.get("poi_hint", "")[:2] in names] \
        or guide.get("spots", [])[:3]
    return {
        "campus": guide.get("campus"),
        "address": guide.get("address"),
        "entry": {
            "channels": [c["name"] + "：" + c["how"] for c in e.get("channels", [])],
            "requires": e.get("requires", []),
            "approval": e.get("approval"),
            "on_arrival": e.get("on_arrival"),
            "lead_time": e.get("lead_time"),
            "vehicle": e.get("vehicle", {}),
            "transit": e.get("transit", []),
        },
        "spots": spots,
        "notes": guide.get("notes", [])[:6],
        "policy_note": guide.get("policy_note", ""),
    }


def _fallback(guide: dict, stops: list[dict], persona: str) -> dict:
    """无模型/模型失败时的确定性模板：一条事实换一句话，不添加任何东西。"""
    e = guide.get("entry", {})
    before = [c["how"] for c in e.get("channels", [])[:1]]
    if e.get("requires"):
        before.append("准备：" + "、".join(e["requires"]))
    if e.get("lead_time"):
        before.append(e["lead_time"])
    if e.get("on_arrival"):
        before.append(e["on_arrival"])

    timeline = [{"time": s["arrive"], "what": "到达 %s（停留 %s 分钟）" % (s["name"], s["stay_min"])}
                for s in stops]
    per_stop = [{"name": s["name"], "say": s.get("reason") or "按行程顺序到达"} for s in stops]
    return {
        "headline": "半天 %d 站，步行串联，按下面时间走即可" % len(stops),
        "before": before or ["先按学校当日公告确认入校要求"],
        "timeline": timeline,
        "per_stop": per_stop,
        "tips": guide.get("notes", [])[:3],
        "verify": [guide.get("policy_note", "以学校当日公告为准")],
    }


def briefing(resp, city: str = "dalian", persona: str = "visitor",
            client=None, enabled: bool = True) -> dict[str, Any]:
    """生成访客简报。失败一律回退模板 —— 永远有东西可看。"""
    guide = load_guide(city)
    stops = _plan_digest(resp)
    if not guide:
        return {"headline": "未配置该校的入校须知", "before": [], "timeline": [],
                "per_stop": [{"name": s["name"], "say": s.get("reason", "")} for s in stops],
                "tips": [], "verify": ["请到校方官网确认入校要求"]}

    if not enabled or client is None or not getattr(client, "enabled", True):
        return _fallback(guide, stops, persona)

    from llm.guard import guarded_json

    facts = _facts_for_prompt(guide, stops)
    user = ("【事实卡】\n" + json.dumps(facts, ensure_ascii=False, indent=1) +
            "\n\n【行程】\n" + json.dumps(stops, ensure_ascii=False, indent=1) +
            "\n\n【对象】%s（%s）\n" % (
                "外地来访的学生/家长" if persona == "visitor" else "本校学生",
                "要详细、可执行，写清从哪个门进、几点到" if persona == "visitor" else "要简洁，重点在安排是否合理"))

    data = guarded_json(client, SYSTEM, user, fallback=None, tag="guide")
    if not isinstance(data, dict) or "timeline" not in data:
        return _fallback(guide, stops, persona)

    # 兜底字段，防止模型少给键
    base = _fallback(guide, stops, persona)
    for k in ("headline", "before", "timeline", "per_stop", "tips", "verify"):
        if k not in data:
            data[k] = base[k]
    return data
