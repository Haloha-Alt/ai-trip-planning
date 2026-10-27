# @author: 甲
# @reviewer: 乙
# @since: 2026-10-27
"""自然语言 -> PlanRequest。

骨架阶段是**规则解析 stub**，不联网、不调大模型。设计目标只有两个：
1. 永不失败：解析不出来就用场景配置的默认值兜底，绝不抛异常。
2. 签名稳定：将来换成真的大模型 API 时，只改函数体，`parse(text, cfg)` 不动。

接 API 时的改法：

    def parse(text, cfg):
        try:
            return _call_llm(text, cfg)      # 你的 API 调用
        except Exception:
            return _rule_parse(text, cfg)    # 失败降级回规则解析
"""

from __future__ import annotations

import re

from engine.config import ScenarioConfig
from engine.models import PlanRequest

# 中文数字与阿拉伯数字的极简转换（只覆盖 1~10，够骨架用）
CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
          "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

# 否定词：出现在标签前面表示"不想去"
NEG_WORDS = ("不想", "不要", "不想去", "避开", "别去", "拒绝", "讨厌", "不喜")


def _to_int(token: str) -> int | None:
    token = token.strip()
    if token.isdigit():
        return int(token)
    return CN_NUM.get(token)


def _known_words(cfg: ScenarioConfig) -> list[str]:
    """可供匹配的兴趣词：场景配置的 alias 键 + 数据里出现过的所有标签。

    全部来自配置和数据，代码里不写死任何业务词。
    """
    words = set(cfg.interest_alias.keys())
    try:
        from data.loader import load_pois

        for p in load_pois(cfg):
            words.update(p.tags)
            words.add(p.category)
    except Exception:
        pass
    return sorted(words, key=len, reverse=True)


def _extract_time(text: str) -> tuple[str | None, str | None]:
    """抽出文本里的 HH:MM，最多两个，小的当开始、大的当结束。"""
    found = re.findall(r"([01]?\d|2[0-3]):([0-5]\d)", text)
    if not found:
        return None, None
    mins = sorted(int(h) * 60 + int(m) for h, m in found)
    fmt = lambda v: "%02d:%02d" % (v // 60, v % 60)  # noqa: E731
    if len(mins) == 1:
        return fmt(mins[0]), None
    return fmt(mins[0]), fmt(mins[-1])


def _extract_budget(text: str) -> float | None:
    """识别 "预算 100"、"100 元"、"100块" 这类写法。"""
    m = re.search(r"(?:预算|不超过|控制在)?\s*(\d+(?:\.\d+)?)\s*(?:元|块|rmb|RMB)", text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    m = re.search(r"预算\s*(\d+(?:\.\d+)?)", text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def _extract_days(text: str) -> int | None:
    m = re.search(r"([0-9一二三四五六七八九十两]+)\s*天", text)
    if m:
        return _to_int(m.group(1))
    return None


def _extract_pace(text: str) -> str | None:
    if any(k in text for k in ("轻松", "悠闲", "别太累", "慢一点", "别太赶", "不赶", "宽松")):
        return "relaxed"
    if any(k in text for k in ("紧凑", "多逛", "充实", "尽可能多", "赶一点")):
        return "packed"
    return None


def parse(text: str, cfg: ScenarioConfig | None = None) -> PlanRequest:
    """把一句自然语言变成一个 PlanRequest。

    解析不到的字段一律用 cfg（或 PlanRequest 默认值）兜底，保证后续流程永远有东西可跑。
    """
    text = (text or "").strip()

    if cfg is None:
        from engine.config import load_scenario

        cfg = load_scenario()

    req = PlanRequest(
        days=cfg.days,
        start_time=cfg.day_start,
        end_time=cfg.day_end,
        daily_budget=cfg.daily_budget,
        pace=cfg.default_pace,
        top_n=cfg.max_poi_per_day,
        raw_query=text,
    )
    if not text:
        return req

    # 时间
    s, e = _extract_time(text)
    if s:
        req.start_time = s
    if e:
        req.end_time = e
    if req.end_time <= req.start_time:      # 抽出来的顺序不对就恢复默认
        req.start_time, req.end_time = cfg.day_start, cfg.day_end

    # 预算 / 天数 / 节奏
    budget = _extract_budget(text)
    if budget is not None:
        req.daily_budget = None if budget <= 0 else budget
    days = _extract_days(text)
    if days:
        req.days = max(1, days)
    pace = _extract_pace(text)
    if pace:
        req.pace = pace

    # 兴趣与排除
    interests: list[str] = []
    avoid: list[str] = []
    for w in _known_words(cfg):
        idx = text.find(w)
        if idx < 0 or not w:
            continue
        prefix = text[max(0, idx - 4):idx]
        if any(n in prefix for n in NEG_WORDS):
            avoid.append(w)
        else:
            interests.append(w)

    req.interests = interests
    req.avoid = avoid
    return req


# 保留一个别名，方便将来写"规则兜底"时引用
_rule_parse = parse
