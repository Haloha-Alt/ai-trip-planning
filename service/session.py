# @author: 甲
# @reviewer: 乙
# @since: 2026-11-07
"""多轮会话（A2）：让第二次调整基于"上一版"，而不是从头重排。

为什么需要
----------
`/api/adjust` 的老约定是"重新生成再打补丁"——这依赖 plan 的确定性：
同一份 body 必然得到同一份行程。但引入实时天气、随机策略（random）后
确定性被打破，连续两句「太赶了」会作用在**两份不同的原始行程**上，
第一句的效果被悄悄抹掉。会话把每轮的最终行程落盘，
下一轮直接拿它当 base，多轮调整就是真正的"在上一版上继续改"。

设计
----
* 每份会话是一个 JSON 文件：`.cache/session/<id>.json`，内容 =
  首轮的 plan body + 最近一次的行程（to_api_dict 口径）+ 指令历史。
* **只存 to_api_dict 的产物**，不存 PlanResponse 对象 —— 反序列化走
  `PlanResponse.from_dict`，契约演进（只增字段）天然兼容。
* 容量上限 + 过期清理：最多 200 份、24 小时过期，写满/过期静默淘汰。
  会话是便利品，不是审计数据，丢了就重新生成一次而已。
* 任何失败（磁盘只读、JSON 损坏）都返回 None / False，绝不连累主流程。
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from service.bootstrap import ROOT

SESSION_DIR = ROOT / ".cache" / "session"
MAX_SESSIONS = 200
TTL_SECONDS = 24 * 3600


def _path(session_id: str) -> Path:
    # id 由本模块生成（uuid hex），但对外是字符串，这里兜一层安全
    safe = "".join(c for c in str(session_id) if c.isalnum() or c in "-_")[:64]
    return SESSION_DIR / ("%s.json" % (safe or "anonymous"))


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def _remove(p: Path) -> bool:
    """删一个会话文件。沙箱/只读盘上 unlink 可能抛任意异常（本项目实测
    safe-delete 守卫抛的不是 OSError），失败一律吞掉 —— 会话是便利品。"""
    try:
        p.unlink()
        return True
    except Exception:                           # noqa: BLE001
        return False


def _prune_locked(now: float) -> None:
    """过期淘汰 + 超容量淘汰。调用方已确保目录存在。"""
    files = sorted(SESSION_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)
    keep: list[Path] = []
    for p in files:
        try:
            if now - p.stat().st_mtime > TTL_SECONDS:
                _remove(p)
                continue
        except OSError:
            continue
        keep.append(p)
    # 超容量的最老会话直接淘汰
    extra = len(keep) - MAX_SESSIONS
    for p in keep[:max(0, extra)]:
        _remove(p)


def save(session_id: str, *, body: dict[str, Any], plan: dict[str, Any],
         instructions: list[str] | None = None) -> bool:
    """落一份会话。`body` 是首轮 /api/plan 的请求体（可复现基线），
    `plan` 是最近一版行程（to_api_dict 口径），`instructions` 是历史调整指令。"""
    if not session_id:
        return False
    try:
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        doc = {
            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "ts": time.time(),
            "body": dict(body or {}),
            "plan": plan or {},
            "instructions": [str(x) for x in (instructions or [])],
        }
        _path(session_id).write_text(
            json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        _prune_locked(time.time())
        return True
    except Exception:                           # noqa: BLE001 - 会话是便利品
        return False


def load(session_id: str) -> dict[str, Any] | None:
    """取一份会话。不存在 / 损坏 / 过期返回 None，由调用方走重排兜底。"""
    if not session_id:
        return None
    p = _path(session_id)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:                           # noqa: BLE001
        return None
    if not isinstance(raw, dict) or not raw.get("plan"):
        return None
    if time.time() - float(raw.get("ts") or 0) > TTL_SECONDS:
        return None
    return raw


def clear() -> int:
    """清空全部会话（测试用）。返回删掉的份数。"""
    n = 0
    if SESSION_DIR.exists():
        for p in SESSION_DIR.glob("*.json"):
            if _remove(p):
                n += 1
    return n
