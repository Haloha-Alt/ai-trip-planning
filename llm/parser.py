# @author: 甲
# @reviewer: 乙
# @since: 2026-10-27
"""自然语言 -> PlanRequest。

两条底线（无论接不接大模型都成立）：
1. **永不失败**：解析不出来就用场景配置的默认值兜底，绝不抛异常。
2. **签名稳定**：`parse(text, cfg, client)` —— 第 3 个参数永远是可选的。

工作方式是"**规则打底，模型增强**"：
- 先跑规则解析（离线、零延迟、可复现），得到一个一定可用的 PlanRequest；
- 若传了 client 且配了 Key，再让模型在规则结果上做**增量修正**；
- 模型超时/返回垃圾/断网 → 护栏返回规则结果，并在 warnings 里留一句话。

这样"有没有大模型"只是效果好不好的差别，不存在"能不能用"的差别。
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


def _rule_parse(text: str, cfg: ScenarioConfig | None = None) -> PlanRequest:
    """规则解析：把一句自然语言变成一个 PlanRequest。

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
        top_n=cfg.default_top_n or cfg.max_poi_per_day,
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


# ---------------------------------------------------------------- 对外入口


def _llm_overrides(text: str, cfg: ScenarioConfig, client) -> dict | None:
    """让模型输出一份"补丁字段"；拿不到就返回 None（调用方保持规则结果）。"""
    from llm.guard import guarded_json
    from llm.prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE

    cities = _city_names()
    vocab = _known_words(cfg)
    user = USER_PROMPT_TEMPLATE.format(
        query=text,
        cities="、".join(cities) if cities else "（无）",
        vocab="、".join(vocab) if vocab else "（无）",
    )
    return guarded_json(client, SYSTEM_PROMPT, user, fallback=None,
                        use_cache=True, tag="parse")


def _city_names() -> list[str]:
    """候选城市名（给提示词用）。城市相关的东西只从 registry 读。"""
    try:
        from data.cities import city_info, list_cities

        return [city_info(k).name for k in list_cities() if city_info(k)]
    except Exception:  # noqa: BLE001 - 提示词里少几个候选城市不影响解析
        return []


def _apply_llm_patch(req: PlanRequest, patch: dict | None, cfg: ScenarioConfig) -> PlanRequest:
    """把模型补丁套到规则结果上。

    **只填"规则没解析出来"或"模型明确给了"的字段**，
    且城市要走一遍 registry 规范化 —— 模型写"大连市"也要能落到 dalian。
    """
    if not isinstance(patch, dict) or not patch:
        return req

    city = patch.get("city")
    if city:
        try:
            from data.cities import resolve_city

            key = resolve_city(str(city))
        except Exception:  # noqa: BLE001
            key = None
        if key:
            req.city = key

    for field in ("days", "top_n"):
        v = patch.get(field)
        if isinstance(v, (int, float)) and v > 0:
            setattr(req, field, int(v))

    for field in ("start_time", "end_time"):
        v = patch.get(field)
        if isinstance(v, str) and re.match(r"^([01]\d|2[0-3]):[0-5]\d$", v.strip()):
            setattr(req, field, v.strip())

    v = patch.get("daily_budget")
    if isinstance(v, (int, float)):
        req.daily_budget = None if v <= 0 else float(v)

    v = patch.get("pace")
    if v in ("relaxed", "normal", "packed"):
        req.pace = v

    for field in ("interests", "avoid", "must_visit"):
        v = patch.get(field)
        if isinstance(v, list):
            words = [str(x).strip() for x in v if str(x).strip()]
            if words:
                setattr(req, field, words)

    # 时间窗自洽性：模型也可能给反
    if req.start_time and req.end_time and req.end_time <= req.start_time:
        req.start_time, req.end_time = cfg.day_start, cfg.day_end
    return req


def parse(text: str, cfg: ScenarioConfig | None = None, client=None) -> PlanRequest:
    """把一句自然语言变成一个 PlanRequest。

    :param client: 大模型 client；`None` 表示"用环境变量决定"（没 Key 就走纯规则）。
    :return:       PlanRequest，**永不返回 None、永不抛异常**
    """
    if cfg is None:
        from engine.config import load_scenario

        cfg = load_scenario()

    req = _rule_parse(text, cfg)

    if client is None:
        try:
            from llm.client import get_client

            client = get_client()
        except Exception:  # noqa: BLE001 - client 拿不到不影响规则解析
            client = None
    if client is None:
        return req

    patch = _llm_overrides(text, cfg, client)
    return _apply_llm_patch(req, patch, cfg)
