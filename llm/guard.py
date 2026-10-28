# @author: 甲
# @reviewer: 乙
# @since: 2026-10-28
"""大模型护栏：**任何一次模型调用都必须经过这里**。

为什么要有护栏
--------------
模型是本项目里唯一"可能很慢、可能很贵、可能返回垃圾"的环节。
如果每个调用点各写各的 try/except，一定会出现这几种现场事故：

- 答辩现场断网，页面转圈 30 秒才报错
- 模型返回 ```json ... ``` 包裹，解析直接崩
- 同一个问题反复问，钱白花、时间白等
- 超时设成了 60 秒，用户以为程序死了

护栏把这几件事一次性收口：

    双超时 (3 秒连上, 8 秒读完) → 失败重试 1 次 → JSON 安全解析
        → 命中磁盘缓存直接返回 → 还是不行就返回 fallback + 一条 warning

**护栏永不抛异常。** 它返回的永远是"一个能用的结果"，
调用方不需要写 try/except，只需要决定 fallback 是什么。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from llm.client import LLMError, NullClient

CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "llm"

# 磁盘缓存：同一个 (system, user) 只问一次模型。
# 为什么落盘而不是放内存：Streamlit 每次交互都是新进程态，内存缓存活不过一次重跑。
_CACHE_ENABLED = True


def cache_path(system: str, user: str, model: str = "") -> Path:
    key = hashlib.sha256(("%s|%s|%s" % (model, system, user)).encode("utf-8")).hexdigest()
    return CACHE_DIR / ("%s.json" % key[:32])


def _read_cache(p: Path) -> dict[str, Any] | None:
    if not _CACHE_ENABLED or not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def _write_cache(p: Path, obj: dict[str, Any]) -> None:
    if not _CACHE_ENABLED:
        return
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def clear_cache() -> int:
    """清空模型缓存，返回删掉的文件数。改了提示词之后要用。"""
    if not CACHE_DIR.exists():
        return 0
    n = 0
    for f in CACHE_DIR.glob("*.json"):
        try:
            f.unlink()
            n += 1
        except OSError:
            pass
    return n


def guarded_json(client,
                 system: str,
                 user: str,
                 *,
                 fallback: dict[str, Any] | None = None,
                 retries: int = 1,
                 use_cache: bool = True,
                 on_warn: Callable[[str], None] | None = None,
                 tag: str = "llm") -> dict[str, Any] | None:
    """调一次模型拿 JSON；拿不到就返回 fallback。

    :param client:     client 对象（NullClient 会立刻走 fallback）
    :param system/user:提示词
    :param fallback:   失败时返回什么（通常就是规则解析的结果）
    :param retries:    额外重试次数（总尝试次数 = retries + 1）
    :param use_cache:  是否用磁盘缓存
    :param on_warn:    告警回调，通常传 `resp.warnings.append`
    :return:           解析出的 dict，或 fallback（**不会抛异常**）
    """
    def warn(msg: str) -> None:
        if on_warn:
            try:
                on_warn("[%s] %s" % (tag, msg))
            except Exception:  # noqa: BLE001 - 告警失败不能拖垮主流程
                pass

    if client is None or isinstance(client, NullClient) or not getattr(client, "enabled", False):
        warn("未启用大模型（缺 LLM_API_KEY），直接用规则结果")
        return fallback

    model = getattr(client, "model", "")
    p = cache_path(system, user, model)
    if use_cache:
        hit = _read_cache(p)
        if hit is not None:
            return hit

    last = ""
    for attempt in range(retries + 1):
        try:
            obj = client.json_chat(system, user)
        except LLMError as e:
            last = str(e)
        except Exception as e:  # noqa: BLE001 - 任何意外都算调用失败
            last = "%s: %s" % (type(e).__name__, e)
        else:
            if use_cache:
                _write_cache(p, obj)
            return obj

    warn("调用失败（已重试 %d 次）：%s → 已回退规则结果" % (retries, last[:160]))
    return fallback


def guarded_text(client, system: str, user: str, *,
                 fallback: str = "", retries: int = 1,
                 use_cache: bool = True,
                 on_warn: Callable[[str], None] | None = None,
                 tag: str = "llm") -> str:
    """同上，但要的是纯文本（解说、贴士这类）。

    缓存 key 与 `guarded_json` 分开，避免"文本结果"被当成 JSON 读。
    """
    def warn(msg: str) -> None:
        if on_warn:
            try:
                on_warn("[%s] %s" % (tag, msg))
            except Exception:  # noqa: BLE001
                pass

    if client is None or isinstance(client, NullClient) or not getattr(client, "enabled", False):
        return fallback

    model = getattr(client, "model", "")
    p = cache_path("TEXT|" + system, user, model)
    if use_cache:
        hit = _read_cache(p)
        if hit is not None and isinstance(hit.get("text"), str):
            return hit["text"]

    last = ""
    for attempt in range(retries + 1):
        try:
            text = client.chat(system, user)
        except LLMError as e:
            last = str(e)
        except Exception as e:  # noqa: BLE001
            last = "%s: %s" % (type(e).__name__, e)
        else:
            if use_cache:
                _write_cache(p, {"text": text})
            return text

    warn("调用失败（已重试 %d 次）：%s → 已回退默认文案" % (retries, last[:160]))
    return fallback
