# @author: 助手
# @since: 2026-11-04
"""详细行程编排（v1.6）：规则骨架 + 校区事实层 + 大模型，三层各司其职。

为什么不是「让模型直接写一份参观攻略」
--------------------------------------
那正是会翻车的做法：模型会顺手补一个不存在的预约入口、一个错的闭馆日、
一条根本没有的公交线路。访客照着跑一趟，比没有攻略更糟。

本项目把它拆成三层，每层只做自己能负责的那件事::

    规则引擎 engine/          → 站点顺序、时刻、停留、在途、成本、约束校验（可复现、可验证）
    校区事实层 visitor_guide  → 预约渠道、校门动作、可达性、点位看什么、来源与查阅日期（可核查）
    大模型 llm/               → 为什么这样排、每站怎么看、怎么走、备选与取舍、导览措辞（个性化）

**模型不许改时刻、不许改顺序、不许新增渠道/电话/开放时间/地点/公交线路。**
它拿到的只是措辞权。事实字段一律由规则层从「事实卡 + 行程数据」拼好，
模型输出只覆盖白名单里的文案字段，且逐条做类型与长度校验。

为什么无模型时也必须「详细」
----------------------------
用户要的是"详细详细详细"，不是"有模型才详细"。所以 `_rule_detail()` 本身
就用事实卡 + POI 溯源 + 行程时间轴拼出逐站六段式说明（看什么 / 怎么走 /
为什么在这 / 可达性 / 备选 / 事实溯源）。`NullClient`、断网、模型返回垃圾，
界面上依然是同一份完整结构，只是 `generated_by` 从 "rules+llm" 变成 "rules"，
每站 `enhanced_by` 从 "llm" 变成 "rules" —— 这正好成了「模型开/关」对照的实证。

写进哪里（冻结契约一个字段都不加）
----------------------------------
只写 `resp.meta["detailed_itinerary"]`。`engine/models.py` 是三方签字契约，
`service/dto.py::to_api_dict()` 已原样透传 `meta`，所以前后端都不用改契约。

可达性为什么不删点
------------------
`service/usecases.py::apply_visitor_rules()` 会按 `access_rules` 压缩停留甚至剔除站点。
那是给「按可达性重排」的调用方用的；这里**不调用**它：访客打卡本来就是看外观，
把图书馆/食堂删掉只会让行程重新变得空荡。可达性在这里以 `step.access` 提示呈现
（"仅外观，馆内需校园卡"），既尊重事实，又保住完整动线。
"""

from __future__ import annotations

import json
import os
from typing import Any

# 单次编排调用的 token / 超时上限。默认 800 token 装不下 5 站导览，会把 JSON
# 腰斩；护栏再重试一次就是两轮 10–30 秒（实测 39.4 秒）。这里一次给足并禁止
# 盲目重试，同时仍可用环境变量微调（换更快的模型时调小）。
ITINERARY_MAX_TOKENS = int(os.environ.get("LLM_ITINERARY_MAX_TOKENS") or 1600)
ITINERARY_TIMEOUT = float(os.environ.get("LLM_ITINERARY_TIMEOUT") or 25.0)

# ---------------------------------------------------------------- 提示词（资产，单独可读可改）

SYSTEM = """你是校园参观行程的「导览编排层」。你拿到的【行程骨架】由规则引擎算出，【事实卡】是校方公开信息；二者都是既定事实。

硬规则（违反即作废）：
1. 不许新增任何预约渠道、电话、开放时间、地点名称、公交线路、价格、历史年代；只能引用【事实卡】与【行程骨架】里出现过的内容。
2. 不许改时刻、不许改站点顺序、不许改停留时长。你只写"怎么说"，不写"去哪、几点"。
3. 不许承诺"一定可以进""保证有位"；事实卡里写了"需现场确认/以当日公告为准"的，必须原样带上，不许替它下结论。
4. 结合【用户需求】调整详略：提到拍照就多讲取景与光线，提到吃饭就多讲用餐节奏，提到带老人小孩就多讲歇脚与步行强度，提到赶时间就多讲取舍。
5. 备选方案只能给流程性建议（压缩停留、改为外观、顺延时间），**不许编一个替代地点**。
6. 只输出 JSON，不要解释，不要 markdown 代码块。

输出结构：
{"headline":"≤28字，一句话说清这趟怎么走",
 "why_this_plan":"2–3句：为什么按这个顺序排、时间窗怎么被用满、为什么先这站后那站",
 "steps":[{"seq":1,"guide":"60–130字：这一站具体看什么、怎么走过去、现场注意什么","why_here":"≤50字：为什么排在这个时刻","backup":"≤45字：临时不开放怎么办（只能是流程性建议）"}],
 "alternatives":[{"when":"≤20字：什么情况下","do":"≤45字：怎么调整"}],
 "llm_adds":["相对纯规则表格，本层额外贡献了什么，3–4条，每条≤40字"]}"""

# ---------------------------------------------------------------- 规则层的语义映射（数据，不是分支）

# 访客可达性规则的 key → 站点名 / 高德原始分类里可能出现的词。
# 高德分类是「中餐厅 / 科教文化场所 / 风景名胜」，事实卡写的是「食堂 / 展馆」，
# 不做这层归一化的话，事实层规则对校区包一条都命不中。
_RULE_ALIASES: dict[str, tuple[str, ...]] = {
    "图书馆": ("图书馆", "阅览"),
    "自习室": ("自习室", "自习区"),
    "食堂": ("食堂", "餐厅", "餐饮", "咖啡", "星巴克", "星咖", "快餐厅", "中餐厅"),
    "体育馆": ("体育馆", "游泳馆", "球场", "田径", "运动", "健身", "操场", "运动场所"),
    "宿舍": ("宿舍", "公寓", "住宅", "商务住宅"),
    "教学楼": ("教学楼", "学院", "主楼", "校部楼", "科研机构", "写字楼", "学校", "高等院校"),
    "校门": ("校门", "大门", "二校门"),
    "展馆": ("航天馆", "展览馆", "纪念馆", "会展中心"),
    "博物馆": ("博物馆", "校史馆", "艺术博物馆", "美术馆"),
}

# 具体建筑（图书馆 / 展馆 / 校门）比泛类（食堂 / 教学楼）更能说明"这个点是什么"。
# 解决一个真实翻车：「星巴克(清华大学图书馆店)」同时含「图书馆」和「星巴克」，
# 纯按关键词长度排序会把它判成「食堂 → 不对访客开放」，而它其实是图书馆旁的咖啡店。
_RULE_PRIORITY: dict[str, int] = {
    "图书馆": 3, "博物馆": 3, "展馆": 3, "校门": 3, "自习室": 2, "宿舍": 2,
    "食堂": 1, "体育馆": 1, "教学楼": 1,
}

# 类别 → 规则兜底的「看什么」。刻意只写动作、不写任何具体事实（年代/门票/开放时间）。
_CATEGORY_ACTIONS: dict[str, list[str]] = {
    "图书馆": ["看建筑外观与楼前广场，按规则只作外观打卡", "在门厅外看当日公告栏，确认是否对访客开放"],
    "食堂": ["先看门口的支付方式与是否接待访客标识", "按行程给出的停留时间安排用餐，避开高峰排队"],
    "展馆": ["按展厅顺序走一圈，重点看常设展", "进门先确认当日是否闭馆、是否需要另行预约"],
    "博物馆": ["按展厅顺序走一圈，重点看校史常设展", "进门先确认当日是否闭馆、是否需要另行预约"],
    "体育馆": ["看建筑外观与周边广场，场馆内按规则不进入", "在开阔处稍作歇脚，补足步行间隙"],
    "运动": ["沿场地外围走一段，看校园日常活动", "注意不要进入正在上课或训练的区域"],
    "景观": ["沿步道慢走，选开阔处取景", "留意脚下与自行车道，拍照别占用通道"],
    "教学": ["看楼体立面与周边标识，公共区域可以走", "上课时段不要在教室门口停留或张望"],
}
_DEFAULT_ACTIONS = ["按行程时刻到达，先看门口当日开放标识", "沿校内主路步行到点，不进入非开放区域"]

# 校内学生视角的动作建议：不涉及访客可达性，全是「这一天怎么过得顺」。
# 与上面一样刻意只写动作、不写任何具体事实（开馆时刻/高峰时段等交给生活提示层）。
_CATEGORY_ACTIONS_STUDENT: dict[str, list[str]] = {
    "图书馆": ["凭校园卡刷卡入馆，先看入口处的座位与开放区指引", "找好座位再摊开东西，离开时把桌面清干净"],
    "自习室": ["凭校园卡进入，安静自习，手机调静音", "离开时带走个人物品，把座位留给下一位"],
    "食堂": ["先看窗口人流再决定吃哪家，避开排队最长的高峰窗口", "吃完把餐盘送到回收处，给后面的同学腾座位"],
    "展馆": ["带校园卡按馆内指引参观，重点看常设展", "留意学生专场或活动公告，感兴趣可以后续再来"],
    "博物馆": ["凭学生身份按馆内指引参观，重点看常设展", "留意学生专场或活动公告，感兴趣可以后续再来"],
    "体育馆": ["按场馆预约/开放规则进场，先确认当天哪些场地可用", "运动前热身，结束后把器材归位"],
    "运动": ["按场地开放情况选择锻炼方式，避开正在上课的时段", "运动前热身，结束后把器材归位"],
    "景观": ["顺路慢走放松一下，别占用主干道", "拍照注意身边骑车的同学"],
    "教学": ["提前几分钟到教室，确认课表教室有没有临时调整", "下课时等门口人流散一散再进出"],
}
_DEFAULT_ACTIONS_STUDENT = ["按行程时刻到达即可，校内日常点位凭校园卡正常使用",
                            "遇临时活动管制或占座满了，就近换同类点位并顺延时间"]

# 校内学生的通用生活提示：没有校方文件背书的常识性节奏建议，
# 一律标「通用提示」，绝不冒充校方事实 —— 这是与访客事实卡（带来源）的最大区别。
# 有本校 visitor_guide.json 的学校可在其 life_tips 字段里放带来源的本校版本，
# 两者会在 campus_life.tips 里合并展示，各条自带 source 标注。
_GENERIC_LIFE_TIPS: list[dict] = [
    {"tip": "食堂用餐高峰一般在 11:30–12:30 与 17:30–18:30，能错开就错开，或先挑人少的窗口",
     "source": "通用提示（非校方公告，以现场为准）"},
    {"tip": "图书馆/自习室座位紧张时，先看入口屏或小程序的余座信息再找位子；离开超过半小时按馆规带走物品",
     "source": "通用提示（非校方公告，以各馆馆规为准）"},
    {"tip": "体育馆/操场多数时段优先教学与训练，对外开放时段以场馆当日公告为准",
     "source": "通用提示（非校方公告，以场馆公告为准）"},
    {"tip": "快递驿站取件高峰在午饭后与晚饭后，顺路取件比专程跑一趟省时间",
     "source": "通用提示（非校方公告，以驿站现场为准）"},
]

# v1.6.1 访客入校类事实关键词：学生视图的 notes/sources/verify（及模型事实卡）
# 命中即剔除 —— 预约渠道、黄牛提醒这类内容对学生既无关也误导。
_VISITOR_FACT_KEYWORDS: tuple[str, ...] = (
    "入校", "预约", "访客", "参观", "黄牛", "渠道", "身份证", "审批")


def _student_fact(t: str) -> bool:
    """该条校级事实是否适合出现在学生视图（不含访客入校类关键词）。"""
    return not any(k in t for k in _VISITOR_FACT_KEYWORDS)


def _student_access(note: str) -> dict:
    """校内学生的可达性口径：访客 access_rules（仅外观/不对外开放）不适用于学生，
    全部改写为「凭校园卡正常使用 + 现场公告兜底」，避免学生视角看到错误限制。"""
    clean_note = _clean(note)
    return {
        "level": "open",
        "rule_key": "student",
        "note": clean_note if clean_note else "校内日常点位，凭校园卡正常使用；如遇活动临时管制以现场公告为准",
        "label": "校内开放（凭校园卡）",
    }


def _clean(text: Any) -> str:
    """事实卡里有 **加粗**，前端是 esc 纯文本渲染，先去掉标记。"""
    return str(text or "").replace("**", "").strip()


_MODEL_LABELS = {
    "active": "模型导览已生效",
    "disabled": "只用规则（手动关闭）",
    "no_key": "未配置模型 Key",
    "failed": "模型调用失败 · 已回退规则",
    "ignored": "模型输出未通过校验 · 已回退规则",
}


def _status(state: str, reason: str, *, warnings: list[str] | None = None,
            hint: str = "") -> dict:
    """本次「大模型这一层」到底发生了什么，显式写进 meta 供前端展示。

    以前模型失败只有 plan.warnings 里一行，用户勾选了「模型开」却看不到任何变化，
    会以为是功能坏了。这里把状态、原因、修复提示都摊开，规则兜底反而成了可解释的卖点。
    """
    return {
        "state": state,
        "active": state == "active",
        "label": _MODEL_LABELS.get(state, "规则模板"),
        "reason": reason,
        "hint": hint,
        "warnings": list(warnings or []),
    }


def _failure_reason(warns: list[str]) -> str:
    """把护栏的告警翻译成人话，别把一长串 HTTP 报文直接糊到页面上。"""
    text = " ".join(warns)
    low = text.lower()
    if "401" in text or "authentication" in low or "api key" in low and "invalid" in low:
        return "模型网关鉴权失败（HTTP 401）：.env 里的 LLM_API_KEY 无效或已过期。"
    if "403" in text or "forbidden" in low:
        return "模型网关拒绝访问（HTTP 403）：Key 无该模型权限或额度已用尽。"
    if "超时" in text or "timed out" in low or "timeout" in low:
        return "模型网关响应超时，本轮未拿到结果。"
    if "不是 JSON" in text or "json" in low:
        return "模型输出未通过 JSON 校验（多半被 max_tokens 截断），本次已回退规则版。"
    if "未启用大模型" in text:
        return "客户端未启用大模型。"
    tail = text.strip()
    return ("模型调用失败：" + tail[-140:]) if tail else "模型调用失败，未返回可用结果。"


def _hint_for(reason: str) -> str:
    """失败提示必须对准真实原因：以前无论什么错都写「修好 Key」，
    结果 JSON 被截断也被误报成「Key 失效」，用户白白去换 Key。"""
    if "鉴权" in reason:
        return "检查项目根目录 .env 的 LLM_API_KEY 是否有效，并重启服务（.env 只在启动时读一次）。"
    if "403" in reason:
        return "检查该 Key 是否有此模型权限、额度是否用尽；或换一个 /models 里列出的模型。"
    if "超时" in reason:
        return "网关排队较久；可重试一次，或调小 LLM_ITINERARY_MAX_TOKENS / 关掉模型路由。"
    if "JSON" in reason:
        return "本页规则版已是完整逐站说明，不影响使用；可重试一次，或调大 LLM_ITINERARY_MAX_TOKENS。"
    return "本页规则版完整可用，不影响行程；稍后重试即可叠加模型导览。"


def _mark_rules_fallback(detail: dict, reason: str) -> None:
    """模型没生效时，把「规则 vs 模型」区的第一句改成真实原因。"""
    rv = detail.setdefault("rules_vs_llm", {})
    adds = rv.get("llm_adds")
    first = "本次模型未生效：%s 以下逐站说明全部由规则模板 + 事实卡生成" % reason
    if isinstance(adds, list) and adds:
        adds[0] = first
    else:
        rv["llm_adds"] = [first]


def _actions_for(name: str, category: str, student: bool = False) -> list[str]:
    """规则兜底的看什么：按名字/分类挑一组动作，绝不掺具体事实。

    `student=True` 走校内学生视角（凭卡正常使用、别占座），否则走访客视角
    （外观打卡、确认是否对访客开放）—— 同一个点位，两种身份看到的是两套话。
    """
    text = (name or "") + " " + (category or "")
    if student:
        table = (
            ("图书馆", _CATEGORY_ACTIONS_STUDENT["图书馆"]),
            ("自习", _CATEGORY_ACTIONS_STUDENT["自习室"]),
            ("航天馆", _CATEGORY_ACTIONS_STUDENT["展馆"]),
            ("博物馆", _CATEGORY_ACTIONS_STUDENT["博物馆"]),
            ("校史馆", _CATEGORY_ACTIONS_STUDENT["博物馆"]),
            ("美术馆", _CATEGORY_ACTIONS_STUDENT["博物馆"]),
            ("食堂", _CATEGORY_ACTIONS_STUDENT["食堂"]),
            ("餐厅", _CATEGORY_ACTIONS_STUDENT["食堂"]),
            ("咖啡", _CATEGORY_ACTIONS_STUDENT["食堂"]),
            ("星巴克", _CATEGORY_ACTIONS_STUDENT["食堂"]),
            ("体育馆", _CATEGORY_ACTIONS_STUDENT["体育馆"]),
            ("游泳", _CATEGORY_ACTIONS_STUDENT["体育馆"]),
            ("球场", _CATEGORY_ACTIONS_STUDENT["体育馆"]),
            ("田径", _CATEGORY_ACTIONS_STUDENT["运动"]),
            ("运动", _CATEGORY_ACTIONS_STUDENT["运动"]),
            ("公园", _CATEGORY_ACTIONS_STUDENT["景观"]),
            ("广场", _CATEGORY_ACTIONS_STUDENT["景观"]),
            ("风景", _CATEGORY_ACTIONS_STUDENT["景观"]),
            ("河畔", _CATEGORY_ACTIONS_STUDENT["景观"]),
            ("荷塘", _CATEGORY_ACTIONS_STUDENT["景观"]),
            ("教学楼", _CATEGORY_ACTIONS_STUDENT["教学"]),
            ("学院", _CATEGORY_ACTIONS_STUDENT["教学"]),
            ("主楼", _CATEGORY_ACTIONS_STUDENT["教学"]),
        )
        for kw, actions in table:
            if kw in text:
                return list(actions)
        return list(_DEFAULT_ACTIONS_STUDENT)
    table = (
        ("图书馆", _CATEGORY_ACTIONS["图书馆"]),
        ("航天馆", _CATEGORY_ACTIONS["展馆"]),
        ("博物馆", _CATEGORY_ACTIONS["博物馆"]),
        ("校史馆", _CATEGORY_ACTIONS["博物馆"]),
        ("美术馆", _CATEGORY_ACTIONS["博物馆"]),
        ("食堂", _CATEGORY_ACTIONS["食堂"]),
        ("餐厅", _CATEGORY_ACTIONS["食堂"]),
        ("咖啡", _CATEGORY_ACTIONS["食堂"]),
        ("星巴克", _CATEGORY_ACTIONS["食堂"]),
        ("体育馆", _CATEGORY_ACTIONS["体育馆"]),
        ("游泳", _CATEGORY_ACTIONS["体育馆"]),
        ("球场", _CATEGORY_ACTIONS["体育馆"]),
        ("田径", _CATEGORY_ACTIONS["运动"]),
        ("运动", _CATEGORY_ACTIONS["运动"]),
        ("公园", _CATEGORY_ACTIONS["景观"]),
        ("广场", _CATEGORY_ACTIONS["景观"]),
        ("风景", _CATEGORY_ACTIONS["景观"]),
        ("河畔", _CATEGORY_ACTIONS["景观"]),
        ("荷塘", _CATEGORY_ACTIONS["景观"]),
        ("教学楼", _CATEGORY_ACTIONS["教学"]),
        ("学院", _CATEGORY_ACTIONS["教学"]),
        ("主楼", _CATEGORY_ACTIONS["教学"]),
    )
    for kw, actions in table:
        if kw in text:
            return list(actions)
    return list(_DEFAULT_ACTIONS)


def _rule_key(name: str, category: str, rules: dict) -> str | None:
    """把站点映射到事实卡 access_rules 的 key；命不中就返回 None（用 default）。

    名字命中优先于分类命中，同类里按关键词长度优先 —— 「清华大学图书馆」
    应当命中「图书馆」，而不是被「学校」带去「教学楼」。
    """
    hits: list[tuple[int, int, int, str]] = []
    for key, kws in _RULE_ALIASES.items():
        if key not in rules:
            continue
        pri = _RULE_PRIORITY.get(key, 1)
        matched = False
        for kw in kws:
            if kw in (name or ""):
                hits.append((2, pri, len(kw), key))
                matched = True
                break
        if not matched:
            for kw in kws:
                if kw in (category or ""):
                    hits.append((1, pri, len(kw), key))
                    break
    if not hits:
        return None
    hits.sort(reverse=True)
    return hits[0][3]


def _match_spot(name: str, spots: list[dict]) -> dict | None:
    """站点名 ↔ 事实卡 spots[].poi_hint 的宽松匹配，取最长命中。"""
    best, best_len = None, 0
    for s in spots or []:
        hint = _clean(s.get("poi_hint"))
        if not hint:
            continue
        if hint in (name or "") or (name and name in hint) or (len(hint) >= 2 and hint[:2] in (name or "")):
            if len(hint) > best_len:
                best, best_len = s, len(hint)
    return best


# ---------------------------------------------------------------- 数据来源

def _poi_meta(city: str, scenario: str) -> dict[str, dict]:
    """按本次生效的场景读 POI 表，拿来源/查阅日期/地址，用于逐站溯源。

    visit 场景走 campus_visit 路由，这里读到的就是该校自己的点位表。
    """
    try:
        from data.loader import load_pois
        from engine.config import load_scenario

        cfg = load_scenario(scenario or None)
        return {
            p.poi_id: {
                "source": p.source or "未标注",
                "source_date": p.source_date or "",
                "address": p.address or "",
                "open_time": p.open_time or "",
                "close_time": p.close_time or "",
                "url": p.url or "",
            }
            for p in load_pois(cfg, city)
        }
    except Exception:                        # noqa: BLE001 - 溯源是加分项，不能拖垮主流程
        return {}


def _display_campus(city: str, guide: dict) -> str:
    try:
        from data.cities import city_info

        info = city_info(city)
        if info and info.display_name:
            return info.display_name
    except Exception:                        # noqa: BLE001
        pass
    return _clean(guide.get("campus")) or city or "本校"


# ---------------------------------------------------------------- 规则版详细行程（模型兜底，本身就要详细）

def _campus_life(guide: dict) -> dict:
    """学生视角的校园生活提示：本校事实（带来源）优先，通用提示补足并明确标注。

    为什么单独一层而不是塞进 facts：访客事实卡的价值在于「可核查」，
    学生生活提示多数没有校方文件背书 —— 硬塞进同一个桶里就会鱼目混珠。
    所以每条 tip 自带 source 与 verified，通用条目 verified=False，前端分色展示。
    """
    tips: list[dict] = []
    for t in guide.get("life_tips") or []:
        if not isinstance(t, dict):
            continue
        tip = _clean(t.get("tip"))
        if not tip:
            continue
        src = _clean(t.get("source"))
        tips.append({"tip": tip, "source": src or "本校事实卡 life_tips", "verified": bool(src)})
    tips.extend({**g, "verified": False} for g in _GENERIC_LIFE_TIPS)
    return {
        "audience": "校内学生",
        "anchors_hint": "有固定的课表/打卡安排？在左侧「校园行程」里添加课表锚点，"
                        "行程会把这些时刻钉死，其余安排围绕它们展开。",
        "tips": tips[:8],
        "source_note": "「本校」条目来自该校事实卡 life_tips；「通用提示」无校方背书，仅供节奏参考。",
    }


def _transport_text(it) -> str:
    mode = (it.transport.mode or "").strip()
    if mode in ("start", ""):
        return "当天起点"
    cn = {"walk": "步行", "transit": "公交", "drive": "车行", "bike": "骑行"}.get(mode, mode)
    return "从上一站%s约 %.0f 分钟（%.2f km）" % (cn, it.transport.minutes, it.transport.distance_km)


def _rule_steps(resp, guide: dict, poi_meta: dict, entry: dict,
                rules: dict, default: dict, student: bool = False) -> list[dict]:
    spots = guide.get("spots") or []
    transit = entry.get("transit") or []
    on_arrival = _clean(entry.get("on_arrival"))
    out: list[dict] = []

    for day in resp.days:
        items = day.items or []
        for idx, it in enumerate(items):
            first = (idx == 0)
            spot = _match_spot(it.name, spots)
            key = _rule_key(it.name, it.category, rules)
            rule = rules.get(key, default) if key else default
            pm = poi_meta.get(it.poi_id, {})

            # --- 看什么：事实卡打底 + 规则动作补足，保证每站至少 3 条可执行说明
            what: list[str] = []
            if spot:
                why = _clean(spot.get("why"))
                if why:
                    what.append(why)
                loc = _clean(spot.get("location"))
                if loc:
                    what.append("位置：" + loc)
                gs = _clean(spot.get("guide_service"))
                if gs:
                    what.append("讲解：" + gs)
                if spot.get("duration_min"):
                    what.append("事实卡建议在这儿停留 %s 分钟" % spot["duration_min"])
            else:
                what.append("该点未在校方事实卡中单列，以下为规则引擎给出的动作建议")
            for a in _actions_for(it.name, it.category, student=student):
                if a not in what:
                    what.append(a)
            if not spot:
                what.append("以上为规则建议，具体以现场公告为准")

            # --- 怎么走：学生从校内直接出发（没有到校/进校环节）；访客才需要校门动作
            if student:
                how = ("当天从校内出发，%s 直接前往「%s」，"
                       "按校内主路/导航步行即可。" % (it.arrive, it.name))
            elif first:
                bits = []
                if transit:
                    bits.append("到校：" + "；".join(_clean(x) for x in transit[:1] if x))
                if on_arrival:
                    bits.append("进校：" + on_arrival)
                bits.append("入校后按指示牌走向「%s」，本行程 %s 从这里开始。" % (it.name, it.arrive))
                how = "  ".join(bits) or ("%s 从起点出发前往「%s」。" % (it.arrive, it.name))
            else:
                how = _transport_text(it) + "；沿校内主路步行，到点后先看门口当日开放标识。"

            # --- 为什么排在这：规则给的 reason + 时间轴位置（不编因果）
            pos = "首站" if first else ("末站" if idx == len(items) - 1 else "第 %d 站" % (idx + 1))
            why_here = "%s：%s。安排在 %s–%s，落在 %s 的可执行时间窗内。" % (
                pos, _clean(it.reason) or "按候选池质量分与距离补位",
                it.arrive, it.leave,
                _clean(getattr(resp.request, "start_time", "")) + "–" +
                _clean(getattr(resp.request, "end_time", "")))
            if not first:
                why_here += "上一站到这里的在途已计入，避免来回折返。"

            # --- 可达性：访客走事实卡 access_rules（提示而非删点）；
            #     学生不走访客规则 —— 「仅外观/不对访客开放」对学生是错误信息
            if student:
                access = _student_access("")
            else:
                level = _clean(rule.get("level")) or "open"
                note = _clean(rule.get("note"))
                access = {
                    "level": level,
                    "rule_key": key or "default",
                    "note": note or "事实卡未对该类点位单列限制，按现场公告为准",
                    "label": {"open": "可进入", "view_only": "仅外观", "need_card": "需校园卡",
                              "exclude": "不对访客开放"}.get(level, "按现场公告"),
                }

            # --- 时间提示：优先事实卡的开放/建议时长，否则用行程停留
            timing: list[str] = []
            if spot and _clean(spot.get("open")):
                timing.append("开放：" + _clean(spot["open"]))
            if spot and spot.get("duration_min"):
                timing.append("事实卡建议停留 %s 分钟" % spot["duration_min"])
            if pm.get("open_time") and pm.get("close_time"):
                timing.append("点位表标注 %s–%s（数据口径，非校方公告）" %
                              (pm["open_time"], pm["close_time"]))
            timing.append("本行程安排停留 %d 分钟（%s–%s）" % (it.stay_min, it.arrive, it.leave))
            if rule.get("stay_min") is not None and it.stay_min > rule["stay_min"]:
                timing.append("可达性规则建议该类点位压到 %s 分钟，现场请以实际开放情况为准" %
                              rule["stay_min"])

            # --- 事实溯源：每一条都写明来自哪里，方便核查
            facts: list[dict] = []
            if spot:
                for field, label in (("open", "开放时间"), ("booking", "预约方式"),
                                     ("guide_service", "讲解服务"), ("tel", "咨询电话"),
                                     ("location", "具体位置"), ("verify", "需现场确认")):
                    val = _clean(spot.get(field))
                    if val:
                        facts.append({"label": label, "value": val,
                                      "from": "事实卡 spots[%s]" % _clean(spot.get("poi_hint"))})
                if spot.get("duration_min"):
                    facts.append({"label": "建议停留", "value": "%s 分钟" % spot["duration_min"],
                                  "from": "事实卡 spots[%s].duration_min" %
                                          _clean(spot.get("poi_hint"))})
            facts.append({
                "label": "点位数据",
                "value": "来源 %s%s" % (pm.get("source", "未标注"),
                                      ("，核对于 " + pm["source_date"]) if pm.get("source_date")
                                      else "，未标注来源适用日期（列入待核实）"),
                "from": "POI 表 pois.csv",
            })
            if it.address or pm.get("address"):
                facts.append({"label": "地址", "value": it.address or pm["address"],
                              "from": "POI 表 address"})

            # --- 备选：只给流程性建议，绝不编替代地点
            verify_spot = _clean(spot.get("verify")) if spot else ""
            if student:
                backup = ("备选：若此站人多满座或临时管制，就近换同类点位并顺延时间；"
                          "站点顺序与整体时间窗不变。")
            elif verify_spot:
                backup = "备选：%s" % verify_spot
            else:
                backup = ("备选：若此站临时不开放或排队过长，改为外观打卡约 5–10 分钟，"
                          "把剩余时间顺延给下一站；站点顺序与整体时间窗不变。")

            out.append({
                "seq": it.seq,
                "day": day.day_index,
                "poi_id": it.poi_id,
                "name": it.name,
                "category": it.category or "",
                "arrive": it.arrive,
                "leave": it.leave,
                "stay_min": it.stay_min,
                "cost": it.cost,
                "transport": {
                    "mode": it.transport.mode,
                    "minutes": it.transport.minutes,
                    "distance_km": it.transport.distance_km,
                    "text": _transport_text(it),
                },
                "what_to_see": what,
                "how_to_walk": how,
                "why_here": why_here,
                "access": access,
                "timing_tip": "；".join(timing),
                "backup": backup,
                "facts": facts,
                "source": {
                    "provider": pm.get("source", "未标注"),
                    "source_date": pm.get("source_date", ""),
                    "address": it.address or pm.get("address", ""),
                    "verified": bool(pm.get("source_date")),
                },
                "llm_guide": "",
                "llm_why_here": "",
                "llm_backup": "",
                "enhanced_by": "rules",
            })
    return out


def _rule_detail(resp, guide: dict, query: str, city: str, poi_meta: dict) -> dict:
    """无模型 / 模型失败时的完整详细行程。同样逐站六段式，不是一句话。

    v1.6.1 按身份分叉：访客（visit）才有「出发前 / 进校动作」，学生（daily）
    拿到的是「今日节奏 + 校园生活提示」—— 之前 daily 也全量输出访客入校须知，
    用户选「校内游览」却看到预约渠道与身份证要求，这是本次修掉的退化。
    """
    is_visit = (getattr(resp, "scenario", "") or "").strip() == "visit"
    student = not is_visit
    entry = guide.get("entry") or {}
    ar = guide.get("access_rules") or {}
    rules = ar.get("rules") or {}
    default = ar.get("default") or {"level": "open", "stay_min": None, "note": ""}
    steps = _rule_steps(resp, guide, poi_meta, entry, rules, default, student=student)

    days = resp.days or []
    first_arrive = (days[0].items[0].arrive if days and days[0].items else "")
    last_leave = (days[-1].items[-1].leave if days and days[-1].items else "")
    total_stay = sum(d.total_stay_min for d in days)
    total_travel = sum(d.total_travel_min for d in days)
    total_dist = sum(d.total_distance_km for d in days)
    total_cost = sum(d.total_cost for d in days)
    m = resp.metrics

    def _minute(x: Any) -> int:
        try:
            h, mi = str(x).split(":")
            return int(h) * 60 + int(mi)
        except Exception:
            return 0

    spare = max(0, _minute(resp.request.end_time) - _minute(last_leave))
    window_note = (
        "场景时间窗 %s–%s，规则引擎已排到 %s，之后还有约 %d 分钟："
        "可按下方备选方案给某站延长停留，或自由活动，不必重新生成。"
        % (resp.request.start_time, resp.request.end_time, last_leave or "—", spare)
    ) if spare > 0 else (
        "行程已排满场景时间窗 %s–%s。" % (resp.request.start_time, resp.request.end_time))

    # 待确认清单：事实卡里所有「需确认/以…为准」的字段 + 未标来源日期的点位
    verify: list[str] = []

    def _add_v(x: Any) -> None:
        t = _clean(x)
        if t and t not in verify:
            verify.append(t)

    # v1.6.1 校级 notes/sources/verify 也按身份过滤：访客入校类事实（预约渠道、
    # 黄牛提醒、身份证要求…）不进学生视图 —— 这些内容对学生既无关也误导。
    guide_notes = [_clean(n) for n in guide.get("notes") or [] if _clean(n)]
    if student:
        guide_notes = [n for n in guide_notes if _student_fact(n)]

    if is_visit:
        _add_v(guide.get("policy_note"))
    for s in guide.get("spots") or []:
        t = _clean(s.get("verify"))
        if t and (is_visit or _student_fact(t)):
            _add_v(t)
    for n in guide_notes:
        if any(k in n for k in ("确认", "为准", "待核实", "别把", "避开")):
            _add_v(n)
    unverified = [s["name"] for s in steps if not s["source"]["verified"]]
    if unverified:
        _add_v("以下点位未标注来源适用日期，出发前建议再确认：%s" % "、".join(unverified[:6]))

    raw_sources = [{"fact": _clean(s.get("fact")), "url": _clean(s.get("url")),
                    "date": _clean(s.get("date"))} for s in guide.get("sources") or []]
    sources = ([s for s in raw_sources if _student_fact(s["fact"])]
               if student else raw_sources)

    campus = _display_campus(city, guide)
    headline = "%s · %s–%s · %d 站可执行行程" % (campus, first_arrive or "—", last_leave or "—", len(steps))

    return {
        "version": "1.0",
        "model_status": _status("disabled", "本次按「只用规则」生成，未调用大模型。",
                                hint="取消左侧「只用规则」勾选后重新生成，即可看到模型导览增量。"),
        "campus": campus,
        "campus_key": city,
        "headline": headline,
        "generated_by": "rules",
        "model_active": False,
        "model": "",
        "query": _clean(query),
        "architecture": {
            "engine": {
                "label": "规则引擎 · engine/",
                "does": ["按语义标签与质量分选点，并按类别做多样化补位",
                         "算时间窗、在途时间、停留、成本与站点顺序",
                         "对硬约束做校验与评分，结果可复现"],
                "guarantees": ["同一输入 → 同一行程", "时刻/距离/成本来自计算，不是模型生成",
                               "时间窗 %s–%s 内闭合" % (resp.request.start_time, resp.request.end_time)],
            },
            "facts": {
                "label": "校区事实层 · visitor_guide.json",
                "file": "data/campuses/%s/visitor_guide.json（或 cities/%s/）" % (city, city),
                "updated": _clean(guide.get("updated")),
                "address": _clean(guide.get("address")),
                "does": (["今日节奏与校园生活提示（通用条目明确标注，不冒充校方事实）",
                          "各类点位的校内使用建议（凭校园卡正常使用）",
                          "逐点看什么、开放与讲解信息", "每条事实的来源与查阅日期"]
                         if student else
                         ["入校预约渠道与到校动作", "各类点位的访客可达性",
                          "逐点看什么、开放与讲解信息", "每条事实的来源与查阅日期"]),
                "counts": {"channels": len(entry.get("channels") or []),
                           "spots": len(guide.get("spots") or []),
                           "sources": len(sources)},
            },
            "llm": {
                "label": "大模型编排层 · llm/",
                "does": ["解释为什么这样排", "把事实写成逐站导览", "按用户措辞调整详略",
                         "给备选与取舍"],
                "forbidden": ["新增预约渠道/电话/开放时间/地点/公交线路",
                              "改时刻、改顺序、改停留时长", "替代地点（备选只给流程性建议）"],
                "note": "入校须知（渠道/电话/时段）坚持走规则模板，不交给模型改写 —— 最敏感的事实不允许被措辞层污染。",
            },
        },
        "before_you_go": ({} if not is_visit else {
            "need_booking": bool(entry.get("need_booking")),
            "booking": [{"name": _clean(c.get("name")), "how": _clean(c.get("how")),
                         "note": _clean(c.get("note"))} for c in entry.get("channels") or []],
            "requires": [_clean(x) for x in entry.get("requires") or [] if _clean(x)],
            "approval": _clean(entry.get("approval")),
            "on_arrival": _clean(entry.get("on_arrival")),
            "lead_time": _clean(entry.get("lead_time")),
            "vehicle": entry.get("vehicle") or {},
            "transit": [_clean(x) for x in entry.get("transit") or [] if _clean(x)],
            "policy_note": _clean(guide.get("policy_note")),
        }),
        "campus_life": {} if is_visit else _campus_life(guide),
        "overview": {
            "start": first_arrive, "end": last_leave,
            "stops": len(steps), "days": len(days),
            "stay_min": total_stay, "travel_min": round(total_travel, 1),
            "distance_km": round(total_dist, 2), "cost": round(total_cost, 2),
            "satisfaction": m.satisfaction, "feasibility": m.feasibility,
            "overall": m.overall(resp.meta.get("weights") or None),
            "audience_hint": "校外访客" if is_visit else "本校学生 · 校园日常",
            "window_note": window_note,
            "why_this_plan": (
                "规则引擎在 %s–%s 的时间窗内，按兴趣标签与质量分选出 %d 个点，"
                "再按在途距离串成一条不折返的动线：停留合计 %d 分钟、在途约 %.0f 分钟，"
                "全程 %.2f km、花费 %.0f 元。每一站的时刻、成本与可达性都有数据或事实来源，"
                "不依赖模型判断。" % (first_arrive or "—", last_leave or "—", len(steps),
                                     total_stay, total_travel, total_dist, total_cost)),
        },
        "steps": steps,
        "alternatives": ([
            {"when": "课连堂 / 有实验", "do": "只保留锚点与饭点，中间各站压缩到 10 分钟或删掉，时间窗不变"},
            {"when": "食堂人太多", "do": "该站压缩停留顺延给下一站，或把吃饭挪到错峰时段重新生成"},
            {"when": "图书馆满座", "do": "就近换同类自习点位并顺延时间，站点顺序不变"},
            {"when": "想多运动 / 多自习", "do": "在需求文本里点名（如「多安排运动」），候选池按语义标签重新加权"},
        ] if not is_visit else [
            {"when": "想少走 / 赶时间", "do": "保留首站与末站，中间各站改为外观打卡压到 10 分钟；顺序不变"},
            {"when": "遇到闭馆或排队", "do": "该站改外观 5–10 分钟，剩余时间顺延给下一站，仍在时间窗内"},
            {"when": "想更偏某类点位", "do": "在需求文本里点名（如「多看展馆」「多拍照」）重新生成，候选池按语义标签重新加权"},
            {"when": "入校时段有变", "do": "以事实卡里的入校时段为准调整到达时刻，重新生成让规则引擎重算时间轴"},
        ]),
        "verify": verify,
        "notes": guide_notes,
        "sources": sources,
        "rules_vs_llm": {
            "rules_only": "规则引擎只能给出一张表：站点顺序、时刻、停留、在途距离、成本、命中了哪些兴趣标签 —— 可执行，但不解释、不导览、不照顾你的措辞。",
            "llm_adds": ["本次未启用大模型：以下逐站说明全部由规则模板 + 事实卡生成",
                         "开启模型后，每站会多出一段结合你需求的导览词",
                         "模型会解释「为什么这样排」并给取舍与备选",
                         "时刻、渠道、电话、来源始终由规则层给出，模型碰不到"],
            "compare_hint": "勾选左侧「只用规则」再生成一次：站点与时刻一个字都不会变，但导览词、编排解释与备选会退回模板 —— 差别就是模型带来的增量。",
            "fact_boundary": "事实（渠道/时间/电话/地点/来源）与措辞（导览/解释/取舍）在界面上分开展示，任何一句都能追溯到出处。",
        },
    }


# ---------------------------------------------------------------- 模型增强（只覆盖白名单文案字段）

def _s(v: Any, lo: int, hi: int) -> str:
    """只接受这个长度区间的字符串，其余一律丢弃，保住规则版。"""
    if not isinstance(v, str):
        return ""
    t = v.strip()
    return t if lo <= len(t) <= hi else ""


def _llm_digest(detail: dict) -> str:
    """给模型的行程骨架：只给事实字段，不给规则版文案，避免它照抄。"""
    return json.dumps([
        {"seq": s["seq"], "name": s["name"], "category": s["category"],
         "arrive": s["arrive"], "leave": s["leave"], "stay_min": s["stay_min"],
         "transport": s["transport"]["text"],
         "access": s["access"]["label"] + "：" + s["access"]["note"],
         "spot_facts": [f["label"] + "：" + f["value"] for f in s["facts"][:4]]}
        for s in detail["steps"]
    ], ensure_ascii=False, indent=1)


def _facts_digest(guide: dict, student: bool = False) -> str:
    """给模型的事实卡摘要：只给事实字段，不给规则版文案，避免它照抄。

    学生视角不给 entry（入校渠道是访客事实，给了模型就会顺着写预约），
    换成 life_tips，让导览词往「这一天怎么过得顺」上靠。
    """
    e = guide.get("entry") or {}
    payload: dict[str, Any] = {
        "campus": _clean(guide.get("campus")),
        "spots": [{"name": _clean(s.get("poi_hint")), "why": _clean(s.get("why")),
                   "open": _clean(s.get("open")), "duration_min": s.get("duration_min"),
                   "verify": _clean(s.get("verify"))} for s in guide.get("spots") or []],
        "notes": ([_clean(n) for n in guide.get("notes") or []]
                  if not student else
                  [t for n in guide.get("notes") or []
                   if (t := _clean(n)) and _student_fact(t)]),
        "policy_note": "" if student else _clean(guide.get("policy_note")),
    }
    if student:
        payload["life_tips"] = [_clean(t.get("tip")) for t in guide.get("life_tips") or []
                                if isinstance(t, dict) and _clean(t.get("tip"))]
        payload["note_student"] = "用户是校内学生：不要提及入校预约/身份证/审批等访客内容。"
    else:
        payload["entry"] = {"channels": [_clean(c.get("name")) + "：" + _clean(c.get("how"))
                                        for c in e.get("channels") or []],
                            "requires": [_clean(x) for x in e.get("requires") or []],
                            "on_arrival": _clean(e.get("on_arrival")),
                            "lead_time": _clean(e.get("lead_time")),
                            "transit": [_clean(x) for x in e.get("transit") or []]}
    return json.dumps(payload, ensure_ascii=False, indent=1)


def _merge_llm(detail: dict, data: dict) -> dict:
    """把模型输出合进规则版。事实字段一律不动，只写白名单文案。"""
    if not isinstance(data, dict):
        return detail

    headline = _s(data.get("headline"), 4, 40)
    why = _s(data.get("why_this_plan"), 10, 420)
    if headline:
        detail["headline"] = headline
    if why:
        detail["overview"]["why_this_plan"] = why

    # steps 按 seq 对齐；模型漏给/多给/给错类型都不影响骨架
    by_seq = {}
    if isinstance(data.get("steps"), list):
        for row in data["steps"]:
            if isinstance(row, dict) and isinstance(row.get("seq"), int):
                by_seq[row["seq"]] = row
    enhanced = 0
    for s in detail["steps"]:
        row = by_seq.get(s["seq"])
        if not row:
            continue
        guide = _s(row.get("guide"), 20, 260)
        why_here = _s(row.get("why_here"), 4, 140)
        backup = _s(row.get("backup"), 4, 140)
        if guide:
            s["llm_guide"] = guide
        if why_here:
            s["llm_why_here"] = why_here
        if backup:
            s["llm_backup"] = backup
        if guide or why_here or backup:
            s["enhanced_by"] = "llm"
            enhanced += 1

    alts = []
    if isinstance(data.get("alternatives"), list):
        for a in data["alternatives"][:4]:
            if not isinstance(a, dict):
                continue
            when, do = _s(a.get("when"), 2, 60), _s(a.get("do"), 4, 160)
            if when and do:
                alts.append({"when": when, "do": do})
    if alts:
        detail["alternatives"] = alts

    adds = []
    if isinstance(data.get("llm_adds"), list):
        for x in data["llm_adds"][:6]:
            t = _s(x, 4, 120)
            if t:
                adds.append(t)
    if adds:
        detail["rules_vs_llm"]["llm_adds"] = adds

    if enhanced or headline or why or alts or adds:
        detail["generated_by"] = "rules+llm"
        detail["model_active"] = True
    return detail


def build(resp, guide: dict | None = None, query: str = "", city: str = "",
          client=None, enabled: bool = True, warnings: list[str] | None = None) -> dict:
    """生成详细行程结构（纯函数式：不修改 resp，返回要写进 meta 的 dict）。"""
    from llm.guide import load_guide

    city = city or (resp.request.city if resp and resp.request else "") or "dalian"
    guide = guide if guide is not None else load_guide(city)
    poi_meta = _poi_meta(city, getattr(resp, "scenario", "") or "")
    detail = _rule_detail(resp, guide, query, city, poi_meta)

    if not enabled:
        detail["model_status"] = _status(
            "disabled", "本次按「只用规则」生成，未调用大模型。",
            hint="取消左侧「只用规则」勾选后重新生成，即可看到模型导览增量。")
        return detail
    if client is None or not getattr(client, "enabled", False):
        detail["model_status"] = _status(
            "no_key", "未配置可用的 LLM_API_KEY，本次自动使用规则模板。",
            hint="在项目根目录 .env 里配好有效的 LLM_API_KEY 后重启服务。")
        return detail

    from llm.guard import guarded_json

    local_warns: list[str] = []

    def on_warn(msg: str) -> None:
        local_warns.append(msg)
        if warnings is not None:
            try:
                warnings.append(msg)
            except Exception:                  # noqa: BLE001 - 告警失败不能拖垮主流程
                pass

    scenario = (getattr(resp, "scenario", "") or "").strip()
    student = (scenario != "visit")
    identity = ("校内学生（校园日常，不涉及入校预约；导览词不要提及预约渠道/身份证/审批）"
                if student else "校外访客（涉及入校预约与校门动作）")
    user = ("【用户需求】\n" + _clean(query or (resp.request.raw_query if resp and resp.request else "")) +
            "\n（没写具体需求就按上面身份的通用情况处理）\n\n【用户身份】\n" + identity +
            "\n\n【事实卡】\n" + _facts_digest(guide, student=student) +
            "\n\n【行程骨架】\n" + _llm_digest(detail) +
            "\n\n请只输出导览措辞 JSON。切记：不许改时刻与顺序，不许新增事实。")
    data = guarded_json(client, SYSTEM, user, fallback=None,
                        on_warn=on_warn, tag="itinerary",
                        retries=0,                       # 确定性失败不重试，省一整轮
                        client_kwargs={"max_tokens": ITINERARY_MAX_TOKENS,
                                       "timeout": ITINERARY_TIMEOUT})

    if data is None:
        reason = _failure_reason(local_warns)
        detail["model_status"] = _status(
            "failed", reason, warnings=local_warns, hint=_hint_for(reason))
        _mark_rules_fallback(detail, reason)
        return detail

    detail = _merge_llm(detail, data)
    if detail["model_active"]:
        detail["model"] = str(getattr(client, "model", "") or "")
        detail["model_status"] = _status(
            "active", "模型导览已生效：逐站导览词、编排解释与备选已由模型生成。",
            warnings=local_warns,
            hint="对照实验：勾选「只用规则」再生成一次，站点与时刻一个字不变，导览词会退回模板。")
    else:
        reason = "模型返回内容未通过校验（字段缺失或长度不合规），已完整保留规则版。"
        detail["model_status"] = _status(
            "ignored", reason, warnings=local_warns,
            hint="可重试一次；规则版本身已是完整逐站说明，不影响使用。")
        _mark_rules_fallback(detail, reason)
    return detail


def enrich(resp, guide: dict | None = None, query: str = "", city: str = "",
           client=None, enabled: bool = True) -> object:
    """就地写回 `resp.meta["detailed_itinerary"]` 并返回同一个对象（便于链式调用）。

    **永不抛异常**：详细行程是增强层，挂了也必须让主行程照常返回。
    """
    try:
        detail = build(resp, guide=guide, query=query, city=city,
                       client=client, enabled=enabled,
                       warnings=getattr(resp, "warnings", None))
        resp.meta["detailed_itinerary"] = detail
    except Exception as e:                    # noqa: BLE001 - 增强层不能拖垮主流程
        resp.meta["detailed_itinerary"] = {
            "version": "1.0", "generated_by": "rules", "model_active": False,
            "model_status": _status("failed", "详细行程增强层异常，已回退，主行程不受影响。",
                                    hint="查看下方主行程表；此问题不影响规划结果。"),
            "headline": "详细行程生成失败，主行程不受影响",
            "error": "%s: %s" % (type(e).__name__, e),
            "steps": [], "sources": [], "verify": [], "notes": [], "alternatives": [],
            "architecture": {}, "before_you_go": {}, "overview": {}, "rules_vs_llm": {},
            "campus": city, "campus_key": city, "model": "", "query": _clean(query),
        }
    return resp
