# @author: 甲
# @reviewer: 乙
# @since: 2026-10-31
"""模型体检：把网关上**声称支持**的模型逐个真打一次，看谁真的活着。

为什么必须有这个
----------------
`GET /models` 只说明网关"登记"了这些模型，**不代表后端真的加载了**。
2026-10-31 实测：`Qwen3.5-9B` 在列表里，但调用 90 秒超时；如果路由表里选了它，
表现是"解说每次都超时 → 静默回退规则 → 看起来像模型没配好"，极难排查。
换模型、换网关、答辩前一天，都先跑一遍这个。

跑法（项目根目录）：
    .venv\\Scripts\\python.exe -m tools.model_bench              # 只测路由表用到的 + 常用几个
    .venv\\Scripts\\python.exe -m tools.model_bench --all        # 测全部（慢，几十秒）
    .venv\\Scripts\\python.exe -m tools.model_bench --timeout 15 # 单项超时秒数

输出三列：耗时 / 结论 / 首句。带 `<think>` 的是思考模型，
做"必须回 JSON"的活（解析、裁判）要格外小心 —— 它的思考过程会混进正文。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from service.bootstrap import load_dotenv  # noqa: E402

load_dotenv()

from llm.client import LLMError, OpenAICompatClient  # noqa: E402

#: 默认只测这些：路由表里的 + 常用的
CORE = [
    "DeepSeek-V4-Flash-0731-W8A8",
    "Qwen3.5-122B-A10B",
    "Qwen3-Next-80B-A3B-Thinking",
    "GLM-4.6-W8A8",
    "DeepSeek-V3.1-W8A8",
    "Qwen3-8B",
]

#: 这些是向量 / 重排 / 视觉模型，走 /chat/completions 必然报错，跳过
SKIP_HINTS = ("embedding", "reranker", "bge", "-vl-", "vl-", "ocr")


def _verdict(ms: float, text: str) -> str:
    if not text.strip():
        return "空回复"
    if "<think>" in text or "思考" in text[:20]:
        return "可用(思考模型)"
    return "可用"


def main(argv: list[str]) -> int:
    key = (__import__("os").environ.get("LLM_API_KEY") or "").strip()
    base = (__import__("os").environ.get("LLM_BASE_URL") or "").strip()
    if not key or not base:
        print("没有配置 LLM_API_KEY / LLM_BASE_URL，先填 .env。")
        return 1

    timeout = 20.0
    if "--timeout" in argv:
        try:
            timeout = float(argv[argv.index("--timeout") + 1])
        except (IndexError, ValueError):
            print("--timeout 需要一个数字，例如 --timeout 15")
            return 1

    probe = OpenAICompatClient(api_key=key, base_url=base, model="x", timeout=timeout)
    try:
        listed = probe.list_models()
    except LLMError as e:
        print("取模型列表失败：%s" % e)
        return 1
    print("网关：%s\n登记模型：%d 个\n" % (base, len(listed)))

    if "--all" in argv:
        targets = listed
    else:
        targets = [m for m in CORE if m in listed]
        missing = [m for m in CORE if m not in listed]
        if missing:
            print("（这些常用模型不在列表里，可能名字变了：%s）\n" % "、".join(missing))

    print("%-34s %8s  %-14s %s" % ("模型", "耗时", "结论", "首句"))
    print("-" * 96)
    alive: list[str] = []
    for m in targets:
        if any(h in m.lower() for h in SKIP_HINTS):
            print("%-34s %8s  %-14s %s" % (m, "-", "跳过", "向量/视觉模型，不走对话接口"))
            continue
        c = OpenAICompatClient(api_key=key, base_url=base, model=m, timeout=timeout)
        t0 = time.time()
        try:
            text = c.chat("你是连通性测试端点。", "只回复两个字：可用", max_tokens=16) or ""
            ms = time.time() - t0
            v = _verdict(ms, text)
            if v.startswith("可用"):
                alive.append(m)
            print("%-34s %7.1fs  %-14s %s" % (m, ms, v, text.strip().replace("\n", " ")[:40]))
        except Exception as e:  # noqa: BLE001 - 体检工具要能把所有失败都报出来
            ms = time.time() - t0
            print("%-34s %7.1fs  %-14s %s" % (m, ms, "失败", str(e)[:60]))
        sys.stdout.flush()

    print("-" * 96)
    print("活着 %d / 测了 %d" % (len(alive), len(targets)))
    if alive:
        print("建议写进 llm/router.py 的 ROLE_MODELS：")
        for m in alive:
            print("    %s" % m)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
