# @author: 甲
# @reviewer: 乙
# @since: 2026-10-31
"""测试包的公共前置：**把测试钉在"离线"状态**。

为什么需要这一行
----------------
`api/server.py` 在 import 时就会 `load_dotenv()`，把项目根 `.env` 里的
`LLM_API_KEY` / `LLM_ROUTER` 灌进 `os.environ`。于是 `tests.test_api` 一跑，
本该"全程不联网"的测试就会真的打到校内网关 —— 慢（整包 15s 变 60s+）、
耗额度、而且没网时会红。这类测试最忌讳"本机能过、CI 上挂"。

`service.bootstrap.load_dotenv()` 的约定是"不覆盖已存在的环境变量"，
所以在这里先把两个开关占住，后面谁再 load 都改不动：

- `LLM_API_KEY=""`     →  `get_client()` 返回 NullClient，全部走规则实现
- `LLM_ROUTER="off"`   →  不包路由，行为与接入路由之前完全一致

要跑真模型的测试，永远用 `tools/llm_probe.py`，不要在单元测试里做。
"""

import os

os.environ["LLM_API_KEY"] = ""
os.environ["LLM_ROUTER"] = "off"
# 访问口令同理：配在 .env 里之后，接口测试会全变 401
os.environ["ACCESS_CODE"] = ""
