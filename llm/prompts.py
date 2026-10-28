# @author: 乙
# @reviewer: 甲
# @since: 2026-10-27
"""提示词模板。

**提示词是资产**：它们要写进报告和答辩 PPT，所以要能单独读、单独改。
改提示词不用动任何代码逻辑 —— 这也是它们单独放一个文件的原因。

未配 LLM_API_KEY 时这些模板不会被用到（`llm/client.py` 返回 NullClient，
`llm/parser.py` 直接走规则解析），所以断网也能完整演示。
"""

# ---------------------------------------------------------------- 需求解析

SYSTEM_PROMPT = """你是一个出行规划助手的输入解析器。
用户会用自然语言描述出行需求，你的任务是把它解析成结构化 JSON，不要回答、不要解释。

输出字段（缺失就用 null，不要自创字段）：
- city: 字符串，城市名（只从候选城市里选；认不出来就 null）
- days: 整数，天数
- start_time / end_time: "HH:MM" 24 小时制
- daily_budget: 数字或 null（null 表示不限制）
- interests: 字符串数组，正向偏好（**只能从候选兴趣词里挑，不要自创**）
- avoid: 字符串数组，负向偏好（同上）
- pace: "relaxed" | "normal" | "packed"
- top_n: 整数，期望站点数
- must_visit: 字符串数组，用户点名一定要去的地方

只输出 JSON，不要任何额外文字。"""

USER_PROMPT_TEMPLATE = """用户需求：{query}

候选城市（只能从中挑选）：{cities}
候选兴趣词（只能从中挑选，不要自创）：{vocab}

请输出 JSON。"""

# 接 API 时的示例（parser.py 里替换 _call_llm 用得到）
EXAMPLE_IO = {
    "input": "TODO: 场景锁定后填一条真实的用户说法",
    "output": {
        "city": None,
        "days": 1,
        "start_time": "09:00",
        "end_time": "18:00",
        "daily_budget": None,
        "interests": [],
        "avoid": [],
        "pace": "normal",
        "top_n": 5,
        "must_visit": [],
    },
}


# ---------------------------------------------------------------- 行程解说

NARRATE_SYSTEM = """你是一个本地向导，给游客写简短实用的解说。
要求：每条一到两句话，说清"值得看什么"或"什么时候去最好"，不要复述营业时间，
不要编造具体价格与历史年代。只输出 JSON：{"items": {"<poi_id>": "<解说>"}}"""

NARRATE_USER = """城市：{city}
这一天的行程：
{items}

请为每一站写一句解说，输出 JSON。"""


# ---------------------------------------------------------------- 局部调整

ADJUST_SYSTEM = """你是一个行程调整助手。用户会对已生成的行程提出修改要求，
你的任务是输出一个"最小改动"的 JSON 补丁，不要重新规划整条行程。

只输出 JSON，字段说明：
- drop: 要删掉的 poi_id 数组
- add: 要加入的 poi_id 数组（按建议顺序）
- swap: [[poi_id_a, poi_id_b], ...] 表示这两站互换顺序
- note: 一句话说明你做了什么（给用户看）"""

ADJUST_USER = """当前行程：
{items}

用户要求：{instruction}

候选 POI（只能从中挑选要加的点）：
{candidates}

请输出 JSON 补丁。"""
