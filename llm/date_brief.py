# @author: 助手
"""约会简报：把「约会行程 + 锚点约束」写成一份给两个人看的可执行安排。

设计原则（与 `llm/guide.py` 完全一致）
------------------------------------
**模型只组织语言，不产生事实。** 时间、地点、花费全部来自行程本身
（arrive/leave/stay_min/cost）与请求里的锚点（电影开场时刻、订座时刻），
模型只许引用，不许新增；拿不准就写"建议提前确认"。

为什么不用模型直接"编一段约会攻略"
----------------------------------
它会给你编一个不存在的排队攻略、一个错的开场时间。约会是要按时刻执行的
（电影不等人），事实错了比没有简报更糟。所以事实与措辞分离：
行程数字可核查，措辞交给模型。

没有 Key 时走 `_fallback()`：同样输入同样输出，一条不改。
输出键与访客简报（guide.py）对齐 —— 前端一套渲染逻辑两种模式共用。
"""

from __future__ import annotations

import json
from typing import Any

SYSTEM = """你是约会行程向导，只做一件事：把给定的**行程**和**锚点（硬时刻）**写成给两个人看的一份安排。

硬规则（违反即作废）：
1. 只能使用【行程】里出现的地点、时间、花费；**不许新增任何地点或事实**。
2. 锚点（如电影开场、订座）的时刻是硬约束，必须原样引用，不许提前或推后。
3. 不含"保证有位""一定来得及"这类绝对说法；赶路提示写"建议提前 N 分钟出发"时要基于行程里已有的在途时间。
4. 时间必须来自【行程】各站的 arrive/leave，不要自己编。
5. 语气轻松自然（这是给两个人的约会安排），但不许牺牲准确性。
6. 只输出 JSON，不要解释，不要 markdown 代码块。

输出结构：
{"headline": "一句话（≤28字，说清这场约会的主线）",
 "before": ["出发前要确认/预订的事，按顺序，2–4 条"],
 "timeline": [{"time": "14:30", "what": "一句话，具体到做什么"}],
 "per_stop": [{"name": "站点名", "say": "这一站做什么、为什么排在这（≤40字）"}],
 "tips": ["让约会更顺的小提醒，2–4 条"],
 "verify": ["需要提前确认的（场次/订座/营业时间），1–3 条"]}"""


def _plan_digest(resp) -> list[dict[str, str]]:
    """行程 -> 简洁事实卡（每站一行）。与 guide.py 同口径。

    v1.6：带上数据层的 tips（怎么玩）—— 这是"事实"不是措辞，
    模型可以引用它把每站安排写具体，fallback 也直接可用。
    """
    out = []
    for d in resp.days:
        for it in d.items:
            out.append({
                "name": it.name,
                "arrive": it.arrive,
                "leave": it.leave,
                "stay_min": str(it.stay_min),
                "cost": str(it.cost),
                "transport": "%s %s分钟" % (it.transport.mode, it.transport.minutes)
                if it.transport else "",
                "reason": (it.reason or "")[:40],
                "tips": (it.tips or "")[:80],
            })
    return out


def _anchors_of(resp) -> list[dict[str, str]]:
    """请求里的锚点原样成卡（含 label），模型据此知道哪些时刻不能动。"""
    out = []
    for a in (resp.request.anchors or []):
        if isinstance(a, dict):
            out.append({
                "poi": str(a.get("poi") or ""),
                "time": str(a.get("time") or ""),
                "duration_min": str(a.get("duration_min") or ""),
                "label": str(a.get("label") or ""),
            })
    return out


def _fallback(stops: list[dict], anchors: list[dict], zone: str) -> dict:
    """无模型/模型失败时的确定性模板：一条事实换一句话。"""
    anchored = {a["poi"] for a in anchors}
    before = []
    for a in anchors:
        if a["label"]:
            before.append("%s：%s 已锁定，建议提前确认预订" % (a["label"], a["time"]))
    if not before:
        before.append("如需订座/购票，建议出发前确认")

    timeline = [{"time": s["arrive"], "what": "到达 %s（停留 %s 分钟）" % (s["name"], s["stay_min"])}
                for s in stops]
    per_stop = [{"name": s["name"],
                 "say": s.get("tips") or s.get("reason") or "按行程顺序到达"}
                for s in stops]

    lead = []
    for a in anchors:
        for s in stops:
            if s["name"] == a["poi"]:
                t = _to_min(s.get("transport", ""))
                if t > 0:
                    lead.append("去%s在途约%d分钟，建议提前出发" % (a["poi"], t))
    tips = lead[:2] or ["站与站之间按行程时间走即可"]
    where = ("%s一带" % zone) if zone else "各站点"
    return {
        "headline": "%s的 %d 站安排，锚点时刻已锁定" % (where, len(stops)),
        "before": before,
        "timeline": timeline,
        "per_stop": per_stop,
        "tips": tips,
        "verify": ["电影场次与餐厅订座请以商家当日确认为准"],
    }


def _to_min(transport: str) -> int:
    """"walk 12分钟" -> 12；解析不了返回 0。"""
    try:
        return int(transport.split()[-1].replace("分钟", ""))
    except Exception:
        return 0


def briefing(resp, zone: str = "", client=None, enabled: bool = True) -> dict[str, Any]:
    """生成约会简报。失败一律回退模板 —— 永远有东西可看。"""
    stops = _plan_digest(resp)
    anchors = _anchors_of(resp)

    if not enabled or client is None or not getattr(client, "enabled", True):
        return _fallback(stops, anchors, zone)

    from llm.guard import guarded_json

    facts = {"zone": zone or "", "anchors": anchors, "stops": stops}
    user = ("【行程】\n" + json.dumps(stops, ensure_ascii=False, indent=1) +
            "\n\n【锚点（硬时刻，不许改动）】\n" + json.dumps(anchors, ensure_ascii=False, indent=1) +
            "\n\n【商圈】%s\n" % (zone or "未指定"))

    data = guarded_json(client, SYSTEM, user, fallback=None, tag="date_brief")
    if not isinstance(data, dict) or "timeline" not in data:
        return _fallback(stops, anchors, zone)

    # 兜底字段，防止模型少给键
    base = _fallback(stops, anchors, zone)
    for k in ("headline", "before", "timeline", "per_stop", "tips", "verify"):
        if k not in data or not data[k]:
            data[k] = base[k]
    return data
