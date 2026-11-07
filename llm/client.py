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
    """调用大模型失败。调用方应当降级，不要把异常抛给用户。

    `fatal=True` 表示「重试也没用」的错误（401/403/400/404 这类确定性失败），
    护栏看到它会立刻止损，不再白等一整轮超时 —— 鉴权挂掉时能省一半时间。
    """

    def __init__(self, message: str, *, fatal: bool = False):
        super().__init__(message)
        self.fatal = fatal


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
        # 内网网关（如 aigw.dlut.edu.cn）必须直连。环境变量里的 HTTP_PROXY
        # 会把请求拐到代理上，代理返回 401 会被误判成「Key 失效」——
        # 实测本机就踩过这个坑。确需代理时显式设 LLM_ALLOW_PROXY=1。
        self.allow_proxy = (os.environ.get("LLM_ALLOW_PROXY") or "").strip().lower() in (
            "1", "true", "yes", "on")

    def _opener(self):
        if self.allow_proxy:
            return urllib.request.build_opener()
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))

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
            with self._opener().open(req, timeout=self.timeout) as r:
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
        # 单次调用可覆盖超时/长度：长 JSON 的编排任务和一句话解说不该共用一个上限。
        timeout = float(kwargs.pop("timeout", self.timeout) or self.timeout)
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
            with self._opener().open(req, timeout=timeout) as r:
                ctype = r.headers.get("Content-Type", "") or ""
                raw = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")[:200]
            except Exception:  # noqa: BLE001 - 读错误体失败不影响主错误
                pass
            # 4xx 是确定性失败：换一次重试只会再等一轮，护栏会据此止损
            raise LLMError("HTTP %s：%s" % (e.code, body or e.reason),
                           fatal=400 <= e.code < 500) from e
        except urllib.error.URLError as e:
            raise LLMError("网络不可达：%s" % e.reason) from e
        except TimeoutError as e:
            raise LLMError("请求超时（%.0f 秒）" % timeout) from e

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


def _repair_truncated_json(text: str) -> dict[str, Any] | None:
    """抢救被 `max_tokens` 截断的 JSON。

    详细行程要求模型一次写出 5 站导览，网关默认 800 token 常常在最后一步
    把 JSON 腰斩（实测 39 秒里有两次都是这种「半截 JSON」）。整段丢弃再重试
    是最慢的做法：这里把半截字符串/数组/对象补齐闭合，前半段照常合并进结果，
    后半段由规则模板兜底 —— 用户看到的是「模型生效 + 规则补全」，而不是全退回。
    """
    s = (text or "").strip()
    start = s.find("{")
    if start < 0:
        return None
    s = s[start:]
    try:
        obj, _ = json.JSONDecoder().raw_decode(s)
        if isinstance(obj, dict):
            return obj
    except ValueError:
        pass

    def _close(frag: str) -> tuple[str, str]:
        stack: list[str] = []
        in_str = False
        esc = False
        for ch in frag:
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch in "{[":
                stack.append(ch)
            elif ch in "}]" and stack:
                stack.pop()
        fixed = frag + ('"' if in_str else "")
        closing = "".join("}" if c == "{" else "]" for c in reversed(stack))
        return fixed, closing

    cuts = [len(s)]
    for j in range(len(s) - 1, 0, -1):
        if s[j] in ",}]\"\n":
            cuts.append(j + 1)
        if len(cuts) >= 48:
            break
    for cut in cuts:
        frag = s[:cut].rstrip()
        if frag.endswith(","):
            frag = frag[:-1].rstrip()
        fixed, closing = _close(frag)
        try:
            obj = json.loads(fixed + closing)
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _safe_json(text: str) -> dict[str, Any] | None:
    """把模型输出里的 JSON 抠出来。

    模型经常裹一层 ```json fences，或者在 JSON 前后说两句废话；
    这里先按完整文本解，再按「第一对花括号」解，最后尝试抢救截断输出。
    """
    if not text:
        return None
    s = text.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.lower().startswith("json"):
            s = s[4:]
        s = s.strip()

    start = s.find("{")
    if start < 0:
        return None
    for candidate in (s[start:], s[start:s.rfind("}") + 1] if s.rfind("}") > start else ""):
        if not candidate:
            continue
        try:
            obj = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    return _repair_truncated_json(s)


def get_client() -> OpenAICompatClient | NullClient:
    """拿一个可用的 client：配了 Key 就走真模型，没配就返回空实现。"""
    c = OpenAICompatClient.from_env()
    return c if c is not None else NullClient()
