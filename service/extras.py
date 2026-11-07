# @author: 助手
"""四个"模型看得见"的附加能力，集中在这里，避免散落。

1. `reverse_hint`  反向规划：给定"最晚几点离校"，算出**最晚几点必须到校**（纯计算，零调用）
2. `negotiate`     预算谈判：超支时给三个可执行方案（数字由 Python 算，模型只写一句话）
3. `audience`      两种口吻：同一份行程，给"同学版 / 家长版"
4. `spiel`         讲解词：每站 60–100 字，可直接当录音稿

共同纪律：**数字一律来自本地计算，模型只负责措辞**；模型失败/无 Key 全部走模板兜底，
输出结构不变（这样前端不用判断"有没有模型"）。
"""

from __future__ import annotations

import json
from typing import Any


# ---------------------------------------------------------------- 工具

def _m(hhmm: str) -> int:
    try:
        h, m = str(hhmm).split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return -1


def _hhmm(minutes: int) -> str:
    minutes = max(0, min(24 * 60 - 1, int(minutes)))
    return "%02d:%02d" % (minutes // 60, minutes % 60)


def _items(plan: dict) -> list[dict]:
    out = []
    for d in plan.get("days", []) or []:
        for it in d.get("items", []) or []:
            out.append(it)
    return out


def _day_span(plan: dict) -> tuple[int, int, int, int]:
    """返回 (最早到达, 最晚离开, 停留合计, 在途合计) 分钟。"""
    items = _items(plan)
    if not items:
        return (-1, -1, 0, 0)
    arr = [_m(i.get("arrive")) for i in items if _m(i.get("arrive")) >= 0]
    lev = [_m(i.get("leave")) for i in items if _m(i.get("leave")) >= 0]
    stay = sum(int(i.get("stay_min") or 0) for i in items)
    travel = sum(int((d.get("total_travel_min") or 0)) for d in plan.get("days", []) or [])
    return (min(arr) if arr else -1, max(lev) if lev else -1, stay, travel)


# ---------------------------------------------------------------- 1. 反向规划

def reverse_hint(plan: dict, must_leave: str) -> dict:
    """给定最晚离校时间，算出最晚到校时间 + 是否需要砍站。

    做法：这一版行程的实际跨度 = 最晚离开 − 最早到达，把它整体平移到"以 must_leave 结束"，
    就得到新的到校时间。跨度放不进参观时段（09:00–must_leave）时，明确说要砍几站。
    """
    first, last, stay, travel = _day_span(plan)
    ml = _m(must_leave)
    if ml < 0 or first < 0 or last < 0:
        return {"ok": False, "note": "这份行程没有可用的时间轴"}

    span = last - first
    arrive_by = ml - span
    n = len(_items(plan))
    per_stop = span / max(1, n)          # 平均每站占用的真实分钟（含在途）

    day_start = 9 * 60                    # 访客场景的对外开放时段起点
    over = arrive_by < day_start
    cut = 0
    while arrive_by + (n - cut) * per_stop > ml or arrive_by < day_start:
        if cut >= n - 1:
            break
        cut += 1
        arrive_by = ml - (n - cut) * per_stop
        if arrive_by >= day_start and ml - arrive_by <= span:
            break

    return {
        "ok": True,
        "must_leave": must_leave,
        "arrive_by": _hhmm(arrive_by),
        "span_min": span,
        "stops": n,
        "over_budget": over,
        "cut": cut,
        "note": ("要 %s 前离校，最晚 %s 到校（全程约 %d 分钟）" % (must_leave, _hhmm(arrive_by), span)
                 if not over else
                 "要 %s 前离校，%d 站排不下；建议砍 %d 站，到校不晚于 %s"
                 % (must_leave, n, max(cut, 1), _hhmm(arrive_by))),
    }


# ---------------------------------------------------------------- 2. 预算谈判

def negotiate(plan: dict, client=None, enabled: bool = True) -> dict[str, Any]:
    """超支时给三个**能马上执行**的方案。每个方案都带 overrides，前端拿去重排即可。

    数字全部现算：省多少 = 现花费 − 预估花费；没有超支也返回（用于"还能更省"的场景）。
    """
    items = _items(plan)
    budget = (plan.get("request") or {}).get("daily_budget")
    cost = sum(float(i.get("cost") or 0) for i in items)
    over = budget is not None and budget > 0 and cost > float(budget)
    n = max(1, len(items))

    picked = sorted(items, key=lambda x: -float(x.get("cost") or 0))[:1]
    top = picked[0] if picked else {}
    top_cost = float(top.get("cost") or 0)

    # 三个方案：少一站 / 省钱档 / 缩短单站（时间换钱）
    options = [
        {"id": "fewer", "label": "少去一站",
         "save": round(top_cost, 1),
         "detail": "砍掉最贵的一站（%s，%g 元）" % (top.get("name", "—"), top_cost),
         "overrides": {"top_n": max(2, n - 1)}},
        {"id": "save_profile", "label": "切省钱档",
         "save": round(max(0.0, cost * 0.25), 1),
         "detail": "换用省钱权重（多选免费点、少选收费点）",
         "overrides": {"profile": "save"}},
        {"id": "relaxed", "label": "放慢节奏",
         "save": 0,
         "detail": "每站少停一会儿，把时间留给路上（不省钱，但不至于赶）",
         "overrides": {"pace": "relaxed"}},
    ]

    for o in options:
        o["after"] = round(max(0.0, cost - o["save"]), 1)

    out = {"over_budget": over, "budget": budget, "cost": round(cost, 1),
           "options": options, "source": "rules"}

    # 让模型给每个方案一句"人话"，数字不交给它
    if enabled and client is not None and getattr(client, "enabled", True):
        from llm.guard import guarded_json

        sys_p = ("你是行程助手。下面有三个已经算好的省钱方案，请给每个方案写一句"
                 "不超过 20 字的口语说明（只说代价，不要改数字）。只输出 JSON："
                 '{"fewer":"…","save_profile":"…","relaxed":"…"}')
        user = json.dumps({"cost": cost, "budget": budget,
                           "options": [{k: o[k] for k in ("id", "label", "save", "detail")}
                                       for o in options]}, ensure_ascii=False)
        data = guarded_json(client, sys_p, user, fallback=None, tag="negotiate")
        if isinstance(data, dict):
            for o in options:
                say = str(data.get(o["id"]) or "").strip()
                if say:
                    o["say"] = say[:40]
            out["source"] = "llm"
    return out


# ---------------------------------------------------------------- 3. 两种口吻

def _facts(plan: dict) -> dict:
    first, last, stay, travel = _day_span(plan)
    items = _items(plan)
    return {
        "stops": len(items),
        "start": _hhmm(first) if first >= 0 else "—",
        "end": _hhmm(last) if last >= 0 else "—",
        "stay": stay,
        "travel": travel,
        "cost": round(sum(float(i.get("cost") or 0) for i in items), 1),
        "names": [i.get("name") for i in items][:6],
        "first_name": items[0].get("name") if items else "—",
        "last_name": items[-1].get("name") if items else "—",
    }


def _audience_template(f: dict, audience: str) -> str:
    if audience == "parent":
        return ("今天从 %s 开始，%s 结束，一共 %d 站，路上大约 %d 分钟，花费 %g 元。"
                "全程都在校内步行，最后从 %s 离开；有情况我会提前说一声。"
                % (f["start"], f["end"], f["stops"], f["travel"], f["cost"], f["last_name"]))
    return ("%s 出发，%s 收工，%d 站、走路 %d 分钟、花 %g 元。先去 %s，最后从 %s 走。"
            % (f["start"], f["end"], f["stops"], f["travel"], f["cost"],
               f["first_name"], f["last_name"]))


def audience(plan: dict, which: str = "peer", client=None, enabled: bool = True) -> dict[str, Any]:
    """同一份行程两种说法：peer（同学）/ parent（家长）。"""
    f = _facts(plan)
    base = _audience_template(f, which)
    out = {"audience": which, "text": base, "source": "rules", "facts": f}
    if not (enabled and client is not None and getattr(client, "enabled", True)):
        return out

    from llm.guard import guarded_json

    sys_p = ("你是学生的助手。把给定的行程事实改写成一段话，受众是%s。"
             "硬规则：数字必须原样使用，不许新增地点或承诺；一段话，不超过 90 字，不要分点。"
             "只输出 JSON：{\"text\":\"…\"}" % ("家长（语气稳妥、交代安全与时间）"
                                            if which == "parent" else "同龄同学（口语、简短、可以带一点轻松）"))
    data = guarded_json(client, sys_p, json.dumps(f, ensure_ascii=False), fallback=None, tag="audience")
    if isinstance(data, dict) and str(data.get("text") or "").strip():
        out["text"] = str(data["text"]).strip()[:200]
        out["source"] = "llm"
    return out


# ---------------------------------------------------------------- 4. 讲解词

def _spiel_template(f: dict, plan: dict) -> list[dict]:
    out = []
    for it in _items(plan):
        reason = (it.get("reason") or "按行程顺序到达，留意路标。")
        text = "这里是%s。%s" % (it.get("name"), reason)
        out.append({
            "name": it.get("name"),
            "arrive": it.get("arrive"),
            "script": text.replace("命中兴趣：", "适合"),
        })
    return out


def spiel(plan: dict, client=None, enabled: bool = True) -> dict[str, Any]:
    """每站一段讲解词（60–100 字），可直接念；无模型给模板。"""
    stops = [{"name": i.get("name"), "reason": (i.get("reason") or "")[:40],
              "stay_min": i.get("stay_min")} for i in _items(plan)]
    out = {"stops": _spiel_template(_facts(plan), plan), "source": "rules"}
    if not stops:
        return out
    if not (enabled and client is not None and getattr(client, "enabled", True)):
        return out

    from llm.guard import guarded_json

    sys_p = ("你是校园讲解员。给每个站点写一段 60–100 字的口播词：说清这是什么地方、"
             "值得看什么、大概待多久。**不许编造史实、年代、人名**；不确定就用"
             "'据说是''以现场介绍为准'这类措辞。只输出 JSON："
             '{"stops":[{"name":"…","script":"…"}]}')
    data = guarded_json(client, sys_p, json.dumps({"stops": stops}, ensure_ascii=False),
                        fallback=None, tag="spiel")
    if isinstance(data, dict) and isinstance(data.get("stops"), list):
        got = {str(s.get("name")): str(s.get("script") or "").strip()
               for s in data["stops"] if isinstance(s, dict)}
        merged = []
        for s in out["stops"]:
            text = got.get(str(s["name"])) or s["script"]
            merged.append({"name": s["name"], "arrive": s["arrive"], "script": text[:220]})
        out["stops"] = merged
        out["source"] = "llm"
    return out
