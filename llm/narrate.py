# @author: 乙
# @reviewer: 甲
# @since: 2026-10-28
"""行程解说：把一张时间表变成"人能读的一段话"。

两种模式，**永远有结果**
------------------------
- 没有 `LLM_API_KEY`：用规则模板拼（说清"去哪、待多久、花多少钱、为什么排这时"），
  断网、答辩机器没配 Key 都能完整演示。
- 有 Key：交给模型写更自然的解说，但**必须经过 `llm/guard.py`**，
  拿不到就退回规则文本 —— 页面不会因为模型抽风而空白。

写进哪里（契约只增字段，这里一个字段都不用加）
---------------------------------------------
- `DayPlan.theme`     ← 当天主题（如"自习 · 用餐"）
- `ItineraryItem.tips`← 每站一句话
- `resp.meta["narrative"]` ← `{"summary": "...", "days": [{"day","theme","note"}]}`
"""

from __future__ import annotations

from typing import Any

from engine.models import PlanResponse
from llm.prompts import NARRATE_SYSTEM, NARRATE_USER

# 单次解说最多带多少站给模型（太多会超 token，且收益递减）
MAX_ITEMS_FOR_LLM = 12


def _theme_of(day) -> str:
    """当天主题：取各站分类的前两个，拼成"图书馆 · 食堂"。"""
    cats: list[str] = []
    for it in day.items:
        c = (it.category or "").strip()
        if c and c not in cats:
            cats.append(c)
    return " · ".join(cats[:2]) or ("第 %d 天" % day.day_index)


def _rule_tip(it) -> str:
    """规则解说：一句话说清"这站值不值"。"""
    parts = []
    if it.stay_min:
        parts.append("建议停留 %d 分钟" % it.stay_min)
    parts.append("免费" if not it.cost else "人均 %.0f 元" % it.cost)
    if it.reason:
        parts.append(it.reason)
    if it.transport and it.transport.mode not in ("start", ""):
        parts.append("从上一站 %s 约 %.0f 分钟"
                     % (it.transport.mode, it.transport.minutes))
    return "，".join(parts) + "。"


def _day_note(day) -> str:
    if not day.items:
        return "这一天是空的 —— 多半是时间窗太短，或候选都被过滤掉了。"
    head = day.items[0]
    tail = day.items[-1]
    return ("%s 出发，%s 收尾，共 %d 站，停留 %d 分钟、在途 %.0f 分钟，花费 %.0f 元。"
            % (head.arrive, tail.leave, len(day.items), day.total_stay_min,
               day.total_travel_min, day.total_cost))


def rule_narrative(resp: PlanResponse) -> dict[str, Any]:
    """不依赖模型也能用的解说（也是模型失败时的兜底）。"""
    days = []
    for d in resp.days:
        days.append({"day": d.day_index, "theme": _theme_of(d), "note": _day_note(d)})

    m = resp.metrics
    total_items = sum(len(d.items) for d in resp.days)
    total_cost = sum(d.total_cost for d in resp.days)
    summary = ("%d 天 %d 站，总花费 %.0f 元，满意度 %.2f、可行性 %.2f，总分 %.4f。"
               % (len(resp.days), total_items, total_cost,
                  m.satisfaction, m.feasibility,
                  m.overall(resp.meta.get("weights") or None)))
    return {"summary": summary, "days": days,
            "items": {it.poi_id: _rule_tip(it) for d in resp.days for it in d.items}}


def _items_text(resp: PlanResponse) -> str:
    lines = []
    for d in resp.days:
        lines.append("第 %d 天：" % d.day_index)
        for it in d.items:
            lines.append("  %s %s（%s，停留 %d 分钟，花费 %.0f）"
                         % (it.arrive, it.name, it.category or "未分类",
                            it.stay_min, it.cost))
    return "\n".join(lines[:MAX_ITEMS_FOR_LLM * 2])


def narrate(resp: PlanResponse, client=None, use_llm: bool = True) -> PlanResponse:
    """给行程加上主题、解说与汇总。**就地写回并返回同一个对象**（便于链式调用）。

    :param client: 大模型客户端；传 None 或未配置 Key 时自动走规则
    :param use_llm: 显式关掉模型（测试里用，保证结果确定）
    """
    base = rule_narrative(resp)
    data = base

    if use_llm:
        from llm.guard import guarded_json

        try:
            got = guarded_json(
                client, NARRATE_SYSTEM,
                NARRATE_USER.format(city=resp.request.city or "", items=_items_text(resp)),
                fallback=base, on_warn=resp.warnings.append, tag="narrate")
        except Exception:                       # noqa: BLE001 - 解说永远不能拖垮主流程
            got = base
        if isinstance(got, dict) and isinstance(got.get("items"), dict):
            # 只接收"看起来像解说"的句子，其余一律用规则文本，避免半截输出
            merged = dict(base["items"])
            for k, v in got["items"].items():
                if isinstance(v, str) and 4 <= len(v) <= 120:
                    merged[str(k)] = v
            data = dict(base, items=merged)
            if isinstance(got.get("summary"), str) and got["summary"].strip():
                data["summary"] = got["summary"].strip()

    tips = data.get("items") or {}
    for d in resp.days:
        d.theme = d.theme or _theme_of(d)
        for it in d.items:
            it.tips = it.tips or tips.get(it.poi_id, "") or _rule_tip(it)

    resp.meta["narrative"] = {
        "summary": data.get("summary") or base["summary"],
        "days": data.get("days") or base["days"],
    }
    return resp


def summary_line(resp: PlanResponse) -> str:
    """一行解说（命令行 / 日志用）。"""
    n = (resp.meta.get("narrative") or {}).get("summary")
    return n or rule_narrative(resp)["summary"]
