# @author: 助手
"""行程体检：让模型以"带队老师"的眼光挑出会真正出问题的地方。

为什么规则做不到（也正因为这样才值得用模型）
--------------------------------------------
规则能算出"两点之间 18 分钟，你只留了 10 分钟"，但说不出
"11:30 的食堂是人最多的时候""这站只是路过看一眼，不值得停 45 分钟"。
前者是算术，后者是常识 —— 常识才是模型的位置。

约束：只读既有行程，**不重新规划、不改数字**；每条问题必须引用具体数字作为证据，
否则一律丢弃（防止它说"可以更充实"这类空话）。
"""

from __future__ import annotations

from typing import Any

SYSTEM = """你是带学生出行的带队老师，负责在出发前挑毛病。
规则：
1. 只挑**会导致行程失败或体验明显变差**的问题：时间不够、来回折返、饭点错位、
   闭馆/关门风险、单站停留明显不合理、预算紧张。
2. 每条必须给**证据**（引用行程里的具体时间或数字），没证据的不要写。
3. 最多 5 条，按严重度从高到低。不要写"可以更充实""建议多留时间"这种空话。
4. 只输出 JSON：
{"summary":"一句话总评（≤30字）",
 "issues":[{"severity":"高|中|低","problem":"问题（≤30字）","evidence":"引用的数字/时间","fix":"一条可执行的改法（≤30字）"}]}"""


def _digest(plan: dict) -> dict:
    days = []
    for d in plan.get("days", []) or []:
        items = [{
            "name": i.get("name"),
            "arrive": i.get("arrive"),
            "leave": i.get("leave"),
            "stay_min": i.get("stay_min"),
            "cost": i.get("cost"),
        } for i in (d.get("items") or [])]
        days.append({
            "day": d.get("day"),
            "start": d.get("start_time") or "",
            "end": d.get("end_time") or "",
            "total_cost": d.get("total_cost"),
            "total_stay_min": d.get("total_stay_min"),
            "total_travel_min": d.get("total_travel_min"),
            "items": items,
        })
    return {"city": plan.get("city"), "scenario": plan.get("scenario"),
            "budget": (plan.get("request") or {}).get("daily_budget"),
            "days": days}


def _m(hhmm: str) -> int:
    try:
        h, m = str(hhmm).split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return -1


def _fallback(digest: dict) -> dict:
    """没有模型时的确定性检查：只报能算出来的硬问题。"""
    issues = []
    for d in digest["days"]:
        items = d["items"]
        for a, b in zip(items, items[1:]):
            if _m(b["arrive"]) >= 0 and _m(a["leave"]) >= 0 and _m(b["arrive"]) < _m(a["leave"]):
                issues.append({"severity": "高", "problem": "两站时间重叠",
                               "evidence": "%s 离开于 %s，下一站 %s 就到达" % (a["name"], a["leave"], b["name"]),
                               "fix": "把后一站顺延，或缩短前一站停留"})
        if len(items) >= 7:
            issues.append({"severity": "中", "problem": "一天站数偏多",
                           "evidence": "共 %d 站" % len(items), "fix": "砍掉最不顺路的一站"})
        if len(items) >= 3 and (d.get("total_travel_min") or 0) > 90:
            issues.append({"severity": "中", "problem": "在途时间过长",
                           "evidence": "在途 %d 分钟" % d["total_travel_min"],
                           "fix": "按区域聚类重排，或换自行车"})
        names = " ".join(i["name"] or "" for i in items)
        if items and ("食堂" not in names and "餐" not in names):
            issues.append({"severity": "低", "problem": "没有安排吃饭",
                           "evidence": "全天 %d 站里没有餐饮点" % len(items),
                           "fix": "在 11:30–13:00 之间插一个食堂"})
    return {"summary": "按硬规则查了一轮，共 %d 条" % len(issues),
            "issues": issues[:5], "source": "rules"}


def checkup(plan: dict, client=None, enabled: bool = True) -> dict[str, Any]:
    digest = _digest(plan or {})
    if not enabled or client is None or not getattr(client, "enabled", True):
        return _fallback(digest)

    import json

    from llm.guard import guarded_json

    user = ("行程（JSON）：\n" + json.dumps(digest, ensure_ascii=False, indent=1) +
            "\n\n请按规则体检。")
    data = guarded_json(client, SYSTEM, user, fallback=None, tag="checkup")
    if not isinstance(data, dict) or not isinstance(data.get("issues"), list):
        return _fallback(digest)

    clean = []
    for it in data["issues"][:6]:
        if not isinstance(it, dict):
            continue
        prob = str(it.get("problem") or "").strip()
        ev = str(it.get("evidence") or "").strip()
        if not prob or not ev:
            continue
        sev = str(it.get("severity") or "中").strip()
        clean.append({"severity": sev if sev in ("高", "中", "低") else "中",
                      "problem": prob[:60], "evidence": ev[:80],
                      "fix": str(it.get("fix") or "").strip()[:60]})
    out = {"summary": str(data.get("summary") or "")[:40],
           "issues": clean[:5], "source": "llm"}
    if not clean:
        fb = _fallback(digest)
        fb["summary"] = out["summary"] or fb["summary"]
        return fb
    return out
