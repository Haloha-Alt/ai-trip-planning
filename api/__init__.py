# @author: 甲
# @reviewer: 乙
# @since: 2026-10-28
"""HTTP 接口层：把 app/usecases.py 的用例暴露成 HTTP。

刻意**不新装 FastAPI**：starlette（FastAPI 的底座）已经装好了，够用；
而 pydantic 这类带二进制扩展的包，正是这次让 pandas 现场翻车的同一类风险。
少一个依赖，答辩机器上就少一个"跑不起来"的可能。
"""
