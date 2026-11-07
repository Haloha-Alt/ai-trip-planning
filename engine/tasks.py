# @author: 助手
# @since: 2026-11-07
"""E3 碎片时间任务：把移动片（在途 5-20 分钟）与等待片（等开门）利用起来。

设计（docs/07 E3 节）：
- 一天里存在不同性质的时间片：连续块（进馆/吃饭）、移动片（只能做
  "不占手不占眼"的事）、等待片（排队/等开门，适合微任务）。
- 任务数据在 data/cities/<key>/tasks.csv，consumes 标注注意力需求：
  ears-only（走路能做）/ none（排队也能做）。attention 类任务不自动装填
  （那需要停下，属于 POI 范畴）。
- 排程 = 生成 day 之后扫描时间片贪心装填，**不动 POI 序列**，失败静默
  （数据层异常不连累主结果）。
- 指标：meta["task_usage"] = 任务占用分钟 / 行程总时长，∈[0,1]。
"""
from __future__ import annotations

from engine.models import PlanResponse


def _usable_tasks(rows: list[dict]) -> list[dict]:
    """只保留 ears-only / none 两种 consumes，按短到长排（先填小碎片）。"""
    out = []
    for r in rows:
        c = (r.get("consumes") or "").strip().lower()
        if c not in ("ears-only", "none"):
            continue
        try:
            dur = int(r.get("duration_min") or 0)
        except (TypeError, ValueError):
            continue
        if dur < 5:
            continue
        out.append({"task_id": r.get("task_id") or "",
                    "name": r.get("name") or "",
                    "duration_min": dur,
                    "consumes": c,
                    "desc": r.get("desc") or ""})
    out.sort(key=lambda t: t["duration_min"])
    return out


def attach_tasks(resp: PlanResponse, task_rows: list[dict]) -> dict:
    """扫描时间片并装填任务。返回 meta["task_usage"] 指标 dict。

    规则：
    - 移动片：item.transport.minutes ∈ [5, 20] 且不是 start 段 → 只装
      ears-only 任务，且任务时长 <= 在途分钟；
    - 等待片：item.wait_min >= 5 → 可装 ears-only 或 none；
    - 每个时间片最多装 1 个任务；每个任务全天最多用 1 次（不重复）。
    """
    tasks = _usable_tasks(task_rows)
    used_ids: set[str] = set()
    filled = 0

    def _pick(slot_min: int, allow_none: bool) -> dict | None:
        for t in tasks:
            if t["task_id"] in used_ids or t["duration_min"] > slot_min:
                continue
            if t["consumes"] == "none" and not allow_none:
                continue
            used_ids.add(t["task_id"])
            return t
        return None

    for d in resp.days:
        for it in d.items:
            travel = int(round(it.transport.minutes or 0))
            if it.transport.mode != "start" and 5 <= travel <= 20:
                t = _pick(travel, allow_none=False)
                if t:
                    it.tasks.append(t)
                    filled += t["duration_min"]
            if (it.wait_min or 0) >= 5:
                t = _pick(it.wait_min, allow_none=True)
                if t:
                    it.tasks.append(t)
                    filled += t["duration_min"]
        d.task_min = sum(t["duration_min"]
                         for it in d.items for t in it.tasks)

    total_min = 0
    for d in resp.days:
        for it in d.items:
            total_min += it.stay_min + (it.wait_min or 0) \
                + int(round(it.transport.minutes or 0))
    usage = round(filled / total_min, 4) if total_min > 0 else 0.0
    return {"filled_min": filled, "total_min": total_min, "usage": usage,
            "tasks_available": len(tasks)}
