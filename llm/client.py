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

两种返回体都要吃得下
--------------------
公网服务商（DeepSeek 官方等）返回一整个 JSON；而校内网关（vLLM 后端）
**即使请求里写 stream=false，也照样返回 SSE 流**（`data: {...}` 一行行推）。
所以本文件不假设返回格式，按响应头 + 正文首字符判断，两条路都能走通。
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

    def list_models(self) -> list[str]:
        """取网关**声称**支持的模型名（给 tools/model_bench.py 用）。

        注意：出现在列表里 ≠ 真的能用。校内网关就有过"模型在列表里、调用 90 秒超时"的
        情况（Qwen3.5-9B，2026-10-31 实测），所以换模型前先跑 `model_bench` 体检。
        """
        req = urllib.request.Request(
            self.base_url + "/models",
            headers={"Authorization": "Bearer %s" % self.api_key},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                obj = json.loads(r.read().decode("utf-8", "replace") or "{}")
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError) as e:
            raise LLMError("取模型列表失败：%s" % e)
        data = obj.get("data") if isinstance(obj, dict) else None
        if not isinstance(data, list):
            return []
        return [str(m.get("id")) for m in data if isinstance(m, dict) and m.get("id")]

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
                ctype = r.headers.get("Content-Type", "") or ""
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

        # 网关强制流式：先按 SSE 拼，拼不出来再按整包 JSON 解
        if _looks_like_sse(ctype, raw):
            text = _content_from_sse(raw)
            if text:
                return text
            raise LLMError("流式返回里没有正文；原文前 200 字：%s" % raw[:200])

        try:
            obj = json.loads(raw)
            return obj["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as e:
            # 少数网关既不给 event-stream 头、正文又是 SSE，这里兜一次再放弃
            text = _content_from_sse(raw)
            if text:
                return text
            raise LLMError("返回体解析失败：%s；原文前 200 字：%s" % (e, raw[:200]))

    def json_chat(self, system: str, user: str, **kwargs) -> dict[str, Any]:
        """发一次对话并**尽力**解析成 dict。解析不出来抛 LLMError。"""
        text = self.chat(system, user, **kwargs)
        obj = _safe_json(text)
        if obj is None:
            raise LLMError("模型返回的不是 JSON：%s" % text[:200])
        return obj


def _looks_like_sse(content_type: str, raw: str) -> bool:
    """返回体是不是 SSE 流。校内网关（vLLM）永远走这条。"""
    if "text/event-stream" in (content_type or "").lower():
        return True
    return (raw or "").lstrip().startswith("data:")


def _content_from_sse(raw: str) -> str:
    """把 SSE 流里所有片段拼成一整段文本。

    兼容两种写法：
      - chat.completion.chunk → choices[0].delta.content（网关 / vLLM 用这个）
      - 个别实现只在最后一帧给 choices[0].message.content，也一并收下

    坏帧（半截 JSON、心跳、注释行）一律跳过：一条坏帧不该毁掉整次调用。
    """
    parts: list[str] = []
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            obj = json.loads(data)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        choices = obj.get("choices")
        if not isinstance(choices, list) or not choices:
            continue
        head = choices[0]
        if not isinstance(head, dict):
            continue
        delta = head.get("delta")
        if isinstance(delta, dict) and isinstance(delta.get("content"), str):
            parts.append(delta["content"])
            continue
        message = head.get("message")
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            parts.append(message["content"])
    return "".join(parts)


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
