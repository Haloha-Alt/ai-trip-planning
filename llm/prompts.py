# @author: 乙
# @reviewer: 甲
# @since: 2026-10-27
"""提示词模板。

骨架阶段**不被调用** —— llm/parser.py 走的是规则解析。
等接真实大模型时，把这里的模板填进 parser 的 API 调用即可。

把提示词单独放一个文件的好处：改提示词不用动代码逻辑，
而且提示词本身是要写进报告和答辩里的"资产"。
"""

SYSTEM_PROMPT = """你是一个出行规划助手的输入解析器。
用户会用自然语言描述出行需求，你的任务是把它解析成结构化 JSON，不要回答、不要解释。

输出字段（缺失就用 null）：
- days: 整数，天数
- start_time / end_time: "HH:MM" 24 小时制
- daily_budget: 数字或 null（null 表示不限制）
- interests: 字符串数组，正向偏好
- avoid: 字符串数组，负向偏好
- pace: "relaxed" | "normal" | "packed"
- top_n: 整数，期望站点数

只输出 JSON，不要任何额外文字。"""

USER_PROMPT_TEMPLATE = """用户需求：{query}

可选的兴趣词（只能从中挑选，不要自创）：{vocab}

请输出 JSON。"""

# 接 API 时的示例（parser.py 里替换 _call_llm 用得到）
EXAMPLE_IO = {
    "input": "TODO: 场景锁定后填一条真实的用户说法",
    "output": {
        "days": 1,
        "start_time": "09:00",
        "end_time": "18:00",
        "daily_budget": None,
        "interests": [],
        "avoid": [],
        "pace": "normal",
        "top_n": 5,
    },
}
