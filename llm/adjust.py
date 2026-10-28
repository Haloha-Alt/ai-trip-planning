# @author: 乙
# @reviewer: 甲
# @since: 2026-10-28
"""局部调整：把"第 3 站太贵了，换一个"翻译成一个最小改动补丁。

分工
----
    llm/adjust.py  ── 只负责**听懂**（自然语言 → Patch）
    engine/adjust.py ── 只负责**改对**（Patch → 新的 PlanResponse，含重新校验与打分）

为什么不把两件事写在一起：听懂可以退化成规则、可以换模型、可以换提示词；
改行程必须严格守约束。混在一起以后，改提示词就会改到约束逻辑上。

规则优先
--------
没有 `LLM_API_KEY` 时靠关键词 + 序数解析，覆盖最高频的几种说法：
删一站 / 换一站（贵了、远了）/ 加一站 / 太赶了 / 太松了。**不依赖网络**。
有 Key 时再让模型补一条更细的补丁，模型给的补丁同样要过 `engine/adjust.py` 的校验。
"""

from __future__ import annotations

import re
from typing import Any

from engine.adjust import Patch, apply_patch, suggest_addition, suggest_replacement
from engine.models import PlanResponse
from llm.prompts import ADJUST_SYSTEM, ADJUST_USER

# 中文数字 → 阿拉伯数字（只到十，够用了）
_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

_DROP_WORDS = ("删", "去掉", "不要", "移除", "取消")
_SWAP_WORDS = ("换顺序", "调换", "互换", "顺序反", "顺序调")
_REPLACE_WORDS = ("换", "替换", "改成", "改为", "改去")
_ADD_WORDS = ("加", "多去", "再去", "增加", "补一站")


def _num(tok: str) -> int | None:
    tok = (tok or "").strip()
    if not tok:
        return None
    if tok.isdigit():
        return int(tok)
    if tok == "末" or tok == "最后":
        return -1
    if tok in _CN_NUM:
        return _CN_NUM[tok]
    if len(tok) == 2 and tok[0] == "十" and tok[1] in _CN_NUM:
        return 10 + _CN_NUM[tok[1]]
    return None


def _parse_ordinal(text: str) -> tuple[int | None, int | None]:
    """从文本里抽 (第几天, 第几站)。没写天数默认第 1 天；`最后一站` → -1。"""
    day = None
    seq = None

    m = re.search(r"第\s*([一二两三四五六七八九十\d末]{1,3})\s*天", text)
    if m:
        day = _num(m.group(1))

    m = re.search(r"第\s*([一二两三四五六七八九十\d末]{1,3})\s*(?:站|个|项|点|处)", text)
    if m:
        seq = _num(m.group(1))

    if seq is None:
        m = re.search(r"(最后|末)(?:一站|一个|那站)", text)
        if m:
            seq = -1
    if seq is None and re.search(r"(第一站|起点|出发那站)", text):
        seq = 1
    return day, seq


def _hint(text: str) -> str:
    if any(w in text for w in ("贵", "省钱", "便宜", "花钱多", "预算")):
        return "cheap"
    if any(w in text for w in ("远", "近", "跑太远")):
        return "near"
    return ""


def parse_rule(text: str, resp: PlanResponse) -> Patch:
    """规则版"听懂"：把一句话翻译成补丁（认不出来就返回空补丁，绝不瞎改）。"""
    t = (text or "").strip()
    if not t:
        return Patch()

    day_no, seq = _parse_ordinal(t)
    day_index = day_no if day_no and day_no > 0 else 1
    day = next((d for d in resp.days if d.day_index == day_index), None) or (
        resp.days[0] if resp.days else None)
    if day is None or not day.items:
        return Patch(note="当前行程是空的，没有可调整的站点")

    if seq == -1:
        seq = len(day.items)
    if seq is None:
        seq = 1

    target = next((it for it in day.items if it.seq == seq), None)
    hint = _hint(t)

    # ---- 删一站 ----
    if any(w in t for w in _DROP_WORDS) and target is not None:
        return Patch(drop=[target.poi_id],
                     note="删掉第 %d 天的第 %d 站「%s」" % (day.day_index, seq, target.name))

    # ---- 换顺序 ----
    if any(w in t for w in _SWAP_WORDS):
        if len(day.items) >= 2:
            a, b = day.items[0], day.items[-1]
            return Patch(swap=[[a.poi_id, b.poi_id]],
                         note="把第 %d 天的首尾两站互换顺序" % day.day_index)
        return Patch(note="这一天只有一站，没有可换的顺序")

    # ---- 换一站（贵了 / 远了 / 泛泛地换）----
    if any(w in t for w in _REPLACE_WORDS):
        if target is None:
            return Patch(note="没找到第 %d 站" % seq)
        new_id = suggest_replacement(resp, day.day_index, seq, hint=hint)
        if not new_id:
            return Patch(note="候选里找不到合适的替代点，行程保持不变")
        return Patch(replace=[[target.poi_id, new_id]],
                     note="第 %d 天的第 %d 站「%s」已换成更%s的点"
                          % (day.day_index, seq, target.name,
                             {"cheap": "便宜", "near": "近"}.get(hint, "合适")))

    # ---- 加一站 ----
    if any(w in t for w in _ADD_WORDS):
        new_id = suggest_addition(resp)
        if not new_id:
            return Patch(note="候选里没有可加的点（都被过滤或已排进行程）")
        return Patch(add=[new_id], note="在第 %d 天末尾加一站" % day.day_index)

    # ---- 太赶 / 太松：删掉或补上最后一站 ----
    if any(w in t for w in ("太赶", "赶", "累", "轻松点", "少一点", "减一站")):
        last = day.items[-1]
        return Patch(drop=[last.poi_id],
                     note="行程偏紧，先去掉第 %d 天最后一站「%s」" % (day.day_index, last.name))
    if any(w in t for w in ("太松", "空", "多点", "再加点")):
        new_id = suggest_addition(resp)
        if new_id:
            return Patch(add=[new_id], note="行程偏松，补一站")
        return Patch(note="没有可补的点")

    return Patch(note="没听懂要改哪一站（可以说「删掉第2站」「第3站太贵了换一个」）")


def _llm_patch(resp: PlanResponse, instruction: str, client) -> dict[str, Any] | None:
    """让模型给一条补丁（可选）。失败返回 None，由规则补丁兜底。"""
    from llm.guard import guarded_json

    from engine.adjust import _candidates
    from engine.config import load_scenario

    cands, _ = _candidates(resp, load_scenario(resp.scenario or None))
    lines = []
    for d in resp.days:
        for it in d.items:
            lines.append("第%d天 第%d站 %s（poi_id=%s，%.0f 元）"
                         % (d.day_index, it.seq, it.name, it.poi_id, it.cost))
    cand_text = "、".join("%s(%s)" % (p.name, p.poi_id) for p in cands[:20]) or "（无）"

    return guarded_json(
        client, ADJUST_SYSTEM,
        ADJUST_USER.format(items="\n".join(lines), instruction=instruction,
                           candidates=cand_text),
        fallback=None, on_warn=resp.warnings.append, tag="adjust")


def adjust(resp: PlanResponse, instruction: str, client=None,
           use_llm: bool = True) -> PlanResponse:
    """按一句话调整行程，返回**新对象**（入参不被修改）。"""
    patch = parse_rule(instruction, resp)

    if use_llm and patch.is_empty():
        got = _llm_patch(resp, instruction, client)
        if isinstance(got, dict):
            cand = Patch.from_dict(got)
            if not cand.is_empty():
                patch = cand
            elif isinstance(got.get("note"), str):
                patch = Patch(note=got["note"])

    if patch.is_empty() and not patch.note:
        patch = Patch(note="没听懂要改什么（试试「删掉第2站」「第3站太贵了换一个」）")

    return apply_patch(resp, patch, scenario=resp.scenario or None)
