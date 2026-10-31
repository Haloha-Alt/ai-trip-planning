# @author: 甲
# @reviewer: 乙
# @since: 2026-10-31
"""模型路由：**按任务挑模型**，而不是所有请求都压在一个模型上。

为什么需要它
------------
校内网关一个 Key 后面挂着 25 个模型，能力强弱和价格差十倍以上：
让 9B 的小模型去啃"三天两晚带预算约束"的需求会乱编，
让 122B 的大模型去写一句"建议停留 120 分钟"又是纯浪费。
所以先用**规则**判断难度，规则拿不准时再让一个**廉价小模型当裁判**做决定。

三条硬规矩
----------
1. **规则优先**：短文本、单站解说这类一眼能判断的，直接定，**不花一次调用**。
   只有落在"说不准"的区间（中等长度、约束词不上不下）才请裁判出手。
2. **裁判只输出角色，不输出模型名**：它只能说 `fast / default / reasoning / long`，
   真正的模型名由本文件的 `ROLE_MODELS` 翻译。这样杜绝"模型名拼错"和"裁判瞎编"。
3. **永不抛异常**：裁判超时、返回垃圾、网络断 —— 一律回退 `default` 角色并记下原因。
   路由是"锦上添花"，它挂了不能影响行程能不能生成。

开关
----
    LLM_ROUTER=off     默认。完全不分流，行为和接入路由之前一模一样（测试就靠这个）
    LLM_ROUTER=rules   只走规则，零额外调用，纯本地、绝对确定
    LLM_ROUTER=llm     规则 + 小模型裁判（演示推荐）

决策按 (任务, 文本) 缓存在内存里：同一句话第二次进来直接命中，
保证"同一个输入两次演示，结果一致"。
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any

from llm.client import _safe_json

# ------------------------------------------------------------------ 路由表

#: 角色 → 网关上的真实模型名（都在「模型广场」里，名字大小写敏感）
#:
#: 2026-10-31 用 `python -m tools.model_bench` 反复实测后定的表。
#: **校内网关的模型可用性会漂移**，同一天里就遇到：
#:   · Qwen3.5-9B      列表里有，但 90 秒超时（后端没加载）
#:   · Qwen3.5-122B    第一次 0.3s、十分钟后再测就 20 秒超时
#:   · DeepSeek-V3.1   直接 503 openai_error
#: 所以这里只放**连续多次实测都稳**的模型。答辩前一天务必重跑 model_bench。
ROLE_MODELS: dict[str, str] = {
    "fast": "GLM-4.6-W8A8",                        # 0.3s，输出干净无 think 标签
    "default": "DeepSeek-V4-Flash-0731-W8A8",      # 0.4s，主力，大多数情况
    "reasoning": "Qwen3-Next-80B-A3B-Thinking",    # 0.8s，带思考链，复杂约束/多步推理
    "long": "DeepSeek-V4-Flash-0731-W8A8",         # 长文本仍用主力：122B 间歇性超时，等它稳了再换
}

DEFAULT_ROLE = "default"

#: 当裁判用的模型。**必须稳、必须回纯 JSON**，所以不用思考模型（思考过程会污染 JSON）
JUDGE_MODEL = "DeepSeek-V4-Flash-0731-W8A8"

#: 触发"复杂"判断的词（约束类，出现越多越该用强模型）
_HARD_WORDS = ("必须", "一定", "不要", "别", "预算", "几天", "多天", "之前", "之后",
               "为什么", "比较", "而不是", "至少", "最多", "如果")


@dataclass(frozen=True)
class Decision:
    """一次路由决定。`source` 说明这个结论是怎么来的，方便答辩时解释。"""

    role: str
    model: str
    source: str   # rule / judge / cache / fallback
    why: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "model": self.model, "source": self.source, "why": self.why}


# ------------------------------------------------------------------ 规则

def _hardness(text: str) -> int:
    """约束词计数：衡量需求"绕不绕"，比字数更靠谱。"""
    return sum(text.count(w) for w in _HARD_WORDS)


def rule_role(task: str, text: str) -> tuple[str | None, str]:
    """纯规则判断。返回 (角色, 理由)；判断不了返回 (None, "")。"""
    n = len(text or "")
    hard = _hardness(text or "")

    if task == "parse":
        if n >= 120 or hard >= 2:
            return "reasoning", "需求长或约束多（%d 字 / %d 个约束词），要更强的指令理解" % (n, hard)
        if n <= 30 and hard == 0:
            return "fast", "需求很短（%d 字）且无约束，小模型足够" % n

    elif task == "narrate":
        if n >= 1200:
            return "long", "行程文本很长（%d 字），先用长上下文模型保证不漏站" % n
        if n <= 300:
            return "fast", "站点少（%d 字），短解说用不着大模型" % n

    elif task == "adjust":
        if n >= 300 or hard >= 1:
            return "reasoning", "调整指令要理解「局部改动」的边界，交给推理模型"
        if n <= 120:
            return "fast", "指令很短，小模型足够"

    return None, ""


# ------------------------------------------------------------------ 裁判

_JUDGE_SYSTEM = """你是模型路由器。给定"任务类型"和"用户文本"，判断该用哪一档模型。

可选角色（只能四选一）：
- fast       短、简单、几乎不需要推理（如一句话需求、两三个站的短解说）
- default    常规情况，默认选它
- reasoning  约束多、需要多步推理、要理解言外之意
- long       文本很长，需要长上下文一次读完

只输出一行 JSON，不要解释：{"role":"default","why":"十个字以内的理由"}"""


def judge_role(client, task: str, text: str, timeout_hint: str = "") -> tuple[str | None, str]:
    """请小模型裁决。任何失败都返回 (None, 原因)，由上层回退。"""
    if client is None:
        return None, "没有可用客户端"
    snippet = (text or "").strip().replace("\n", " ")[:600]
    user = "任务类型：%s\n用户文本：%s" % (task or "unknown", snippet)
    try:
        reply = client.chat(_JUDGE_SYSTEM, user,
                            model=JUDGE_MODEL, temperature=0.0, max_tokens=48)
    except Exception as e:  # noqa: BLE001 - 裁判失败绝不能影响主流程
        return None, "%s: %s" % (type(e).__name__, str(e)[:80])

    obj = _safe_json(reply or "")
    if not obj:
        return None, "裁判返回的不是 JSON：%s" % (reply or "")[:60]
    role = str(obj.get("role") or "").strip().lower()
    if role not in ROLE_MODELS:
        return None, "裁判给了未知角色「%s」" % role
    why = str(obj.get("why") or "").strip()[:40] or "裁判判断"
    return role, why


# ------------------------------------------------------------------ 决策（带缓存）

_CACHE: dict[str, Decision] = {}


def _key(task: str, text: str) -> str:
    pool = "|".join("%s=%s" % (k, v) for k, v in sorted(ROLE_MODELS.items()))
    raw = "%s|%s|%s" % (task, pool, text)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def decide(task: str, text: str, *, client=None, mode: str = "llm") -> Decision:
    """给出这次调用该用哪个模型的结论。**永不抛异常。**

    :param task: 任务类型，通常直接复用 guard 的 tag（parse / narrate / adjust）
    :param text: 用来判断难度的文本（system+user 拼起来即可）
    :param client: 用来当裁判的 client；None 表示不请裁判
    :param mode: rules 只走规则；llm 规则 + 裁判
    """
    k = _key(task, text)
    hit = _CACHE.get(k)
    if hit is not None:
        return Decision(hit.role, hit.model, "cache", hit.why)

    role, why = rule_role(task, text)
    source = "rule"

    if role is None:
        if mode == "llm" and client is not None:
            role, why = judge_role(client, task, text)
            source = "judge" if role else "fallback"
        else:
            role, why = None, "规则未覆盖，且未启用裁判"

    if role not in ROLE_MODELS:
        role = DEFAULT_ROLE
        source = "fallback" if source != "rule" else source
        why = why or "回退默认模型"

    d = Decision(role, ROLE_MODELS[role], source, why)
    _CACHE[k] = d
    return d


def clear_cache() -> int:
    """清空路由决策缓存（改了 ROLE_MODELS 之后要调）。"""
    n = len(_CACHE)
    _CACHE.clear()
    return n


# ------------------------------------------------------------------ 包装成 client

class RoutedClient:
    """把"选模型"这件事塞进 client 里，调用方（parser/narrate/adjust）一行都不用改。

    对外的接口与 `OpenAICompatClient` 完全一致，只是多认一个 `task=` 关键字参数
    （guard 会把 tag 透传进来）。
    """

    enabled = True

    def __init__(self, base, mode: str = "llm"):
        self._base = base
        self.mode = mode
        # 给 guard 当缓存 key 用：路由后模型会变，这里固定成 "auto"，
        # 好处是同一句输入永远命中同一条缓存（演示可复现）
        self.model = "auto"
        self.last_routes: list[Decision] = []

    # -- 内部 --

    def _pick(self, task: str, system: str, user: str) -> Decision:
        d = decide(task, "%s\n%s" % (system or "", user or ""),
                   client=self._base if self.mode == "llm" else None,
                   mode=self.mode)
        self.last_routes.append(d)
        if len(self.last_routes) > 50:
            del self.last_routes[:-20]
        return d

    # -- 与外层 client 一致 --

    def chat(self, system: str, user: str, **kwargs) -> str:
        task = str(kwargs.pop("task", "") or "")
        d = self._pick(task, system, user)
        kwargs["model"] = d.model
        return self._base.chat(system, user, **kwargs)

    def json_chat(self, system: str, user: str, **kwargs) -> dict[str, Any]:
        text = self.chat(system, user, **kwargs)
        obj = _safe_json(text)
        if obj is None:
            from llm.client import LLMError

            raise LLMError("模型返回的不是 JSON：%s" % text[:200])
        return obj

    def __repr__(self) -> str:
        return "RoutedClient(%s, base=%r)" % (self.mode, self._base)


def router_mode() -> str:
    """读环境变量：off / rules / llm（非法值按 off 处理）。"""
    v = (os.environ.get("LLM_ROUTER") or "off").strip().lower()
    return v if v in ("rules", "llm") else "off"


def wrap(client):
    """按 LLM_ROUTER 决定要不要包一层。关着的时候原样返回，行为零变化。"""
    mode = router_mode()
    if mode == "off" or client is None:
        return client
    if not getattr(client, "enabled", False):
        return client
    return RoutedClient(client, mode=mode)
