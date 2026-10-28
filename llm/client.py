# @author: 甲
# @reviewer: 乙
# @since: 2026-10-28
"""大模型客户端：OpenAI 兼容协议的极简封装 + 一个"什么都不做"的空实现。

三条硬规矩
----------
1. **全项目只有本文件允许发 HTTP 请求给大模型。**
   别的模块想调模型，只能拿一个 client 对象，不许自己拼 URL。
   这样换服务商（DeepSeek → 通义 → 智谱 → 本地 Ollama）只改这里和环境变量。
2. **没有 Key 就当没这回事**：`from_env()` 返回 `None`，调用方走规则解析。
   断网答辩时不会出现"卡在请求上 30 秒然后 500"。
3. **只用标准库**（urllib）。requests 没问题时用着方便，但这类带二进制/代理逻辑的库
   在陌生机器上翻车概率更高 —— 本项目已经因为 DLL 吃过一次亏。

环境变量
--------
    LLM_API_KEY      必填；不填 = 不启用大模型
    LLM_BASE_URL     默认 https://api.deepseek.com/v1
    LLM_MODEL        默认 deepseek-chat
    LLM_TIMEOUT      默认 8（秒）
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


class LLMError(RuntimeError):
    """调用大模型失败。调用方应当降级，不要把异常抛给用户。"""


class NullClient:
    """空实现：永远返回一个可识别的失败。

    用它而不是 `None`，好处是调用方不用到处写 `if client is not None`，
    行为上也更明确 —— "我没配 Key"和"Key 配错了"是两回事，这里明确是前者。
    """

    enabled = False

    def __init__(self, reason: str = "未配置 LLM_API_KEY"):
        self.reason = reason

    def chat(self, system: str, user: str, **kwargs) -> str:
        raise LLMError(self.reason)

    def __repr__(self) -> str:
        return "NullClient(%s)" % self.reason


class OpenAICompatClient:
    """OpenAI 兼容的 /chat/completions 客户端。"""

    enabled = True

    def __init__(self,
                 api_key: str,
                 base_url: str = "https://api.deepseek.com/v1",
                 model: str = "deepseek-chat",
                 timeout: float = 8.0,
                 temperature: float = 0.2,
                 max_tokens: int = 800):
        if not api_key:
            raise ValueError("api_key 不能为空")
        self.api_key = api_key
        self.base_url = (base_url or "").rstrip("/")
        self.model = model or "deepseek-chat"
        self.timeout = float(timeout or 8.0)
        self.temperature = temperature
        self.max_tokens = max_tokens

    # ---- 配置入口 ----

    @classmethod
    def from_env(cls) -> "OpenAICompatClient | None":
        """按环境变量构造。**没有 Key 返回 None**（不是抛异常）。"""
        key = (os.environ.get("LLM_API_KEY") or "").strip()
        if not key:
            return None
        try:
            return cls(
                api_key=key,
                base_url=(os.environ.get("LLM_BASE_URL") or "https://api.deepseek.com/v1").strip(),
                model=(os.environ.get("LLM_MODEL") or "deepseek-chat").strip(),
                timeout=float(os.environ.get("LLM_TIMEOUT") or 8.0),
            )
        except (ValueError, TypeError):
            return None

    # ---- 调用 ----

    def chat(self, system: str, user: str, **kwargs) -> str:
        """发一次对话，返回**纯文本**（assistant 的 content）。

        失败一律抛 LLMError，绝不返回半截内容 —— 半截内容比明确的失败更难查。
        """
        url = "%s/chat/completions" % self.base_url
        payload = {
            "model": kwargs.get("model") or self.model,
            "messages": [
                {"role": "system", "content": system or ""},
                {"role": "user", "content": user or ""},
            ],
            "temperature": kwargs.get("temperature", self.temperature),
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "stream": False,
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer %s" % self.api_key,
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")[:200]
            except Exception:  # noqa: BLE001 - 读错误体失败不影响主错误
                pass
            raise LLMError("HTTP %s：%s" % (e.code, body or e.reason))
        except urllib.error.URLError as e:
            raise LLMError("网络不可达：%s" % e.reason)
        except TimeoutError:
            raise LLMError("请求超时（%.0f 秒）" % self.timeout)

        try:
            obj = json.loads(raw)
            return obj["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise LLMError("返回体解析失败：%s；原文前 200 字：%s" % (e, raw[:200]))

    def json_chat(self, system: str, user: str, **kwargs) -> dict[str, Any]:
        """发一次对话并**尽力**解析成 dict。解析不出来抛 LLMError。"""
        text = self.chat(system, user, **kwargs)
        obj = _safe_json(text)
        if obj is None:
            raise LLMError("模型返回的不是 JSON：%s" % text[:200])
        return obj


def _safe_json(text: str) -> dict[str, Any] | None:
    """把模型输出里的 JSON 抠出来。

    模型经常裹一层 ```json  fences，或者在 JSON 前后说两句废话。
    这里的策略是"找第一对花括号"，够用且不脆弱。
    """
    if not text:
        return None
    s = text.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.lower().startswith("json"):
            s = s[4:]
        s = s.strip()

    start, end = s.find("{"), s.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(s[start:end + 1])
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def get_client() -> OpenAICompatClient | NullClient:
    """拿一个可用的 client：配了 Key 就走真模型，没配就返回空实现。"""
    c = OpenAICompatClient.from_env()
    return c if c is not None else NullClient()
