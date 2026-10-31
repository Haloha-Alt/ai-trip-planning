# @author: 甲
# @reviewer: 乙
# @since: 2026-10-31
"""大模型连通性自检：`.env` 配好之后先跑这个，别在答辩台上第一次真调用。

为什么要单独有个探针
--------------------
没配 Key 时项目**什么都能跑**（规则解说、规则解析），所以"页面看起来正常"不能证明 Key 有效 ——
真到台上第一次调用才发现 401 / 模型名写错 / 余额不足，那就来不及了。

跑法（项目根目录）：
    .venv\\Scripts\\python.exe -m tools.llm_probe              # 只测连通（一次极小请求）
    .venv\\Scripts\\python.exe -m tools.llm_probe --narrate    # 再跑一遍"plan → narrate"全链路

退出码：0 = 可用（或明确告知"未启用"，属正常配置）；1 = 配了但调用失败。
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from service.bootstrap import load_dotenv  # noqa: E402

load_dotenv()


def _mask(v: str) -> str:
    if not v:
        return "(空)"
    return v[:6] + "…" + v[-4:] if len(v) > 12 else "已设置(过短)"


def main(argv: list[str]) -> int:
    print("== 环境变量 ==")
    for name in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "LLM_TIMEOUT"):
        raw = (os.environ.get(name) or "").strip()
        shown = _mask(raw) if name == "LLM_API_KEY" else (raw or "(用默认值)")
        print("  %-13s %s" % (name, shown))

    from llm.client import LLMError, get_client

    client = get_client()
    print("\n== 客户端 ==")
    print("  %s" % client)

    if getattr(client, "enabled", False) is False:
        print("\n结论：**未启用大模型**（这是合法配置，不是故障）。")
        print("  解说 / 解析 / 一句话调整全部走规则实现，页面照样完整。")
        print("  要接入：在项目根目录的 .env 里填 LLM_API_KEY（详见 .env.example），")
        print("  然后**重启** python -m api.server（.env 只在进程启动时读一次）。")
        return 0

    print("\n== 连通性（一次极小请求）==")
    t0 = time.time()
    try:
        reply = client.chat("你是一个连通性测试端点。", "只回复两个字：可用", max_tokens=8)
    except LLMError as e:
        print("  [失败] %s" % e)
        print("\n常见原因：Key 无效/过期（401）、模型名写错（404）、余额不足（402）、网络或代理不通。")
        return 1
    cost = time.time() - t0
    print("  [ok] 用时 %.2fs，回复：%s" % (cost, (reply or "").strip()[:40]))

    if "--narrate" in argv:
        print("\n== 全链路（plan → narrate）==")
        from service.usecases import narrate_plan, plan_from_text

        resp = plan_from_text("上午自习，中午吃饭，别太赶", scenario="daily",
                              strategy="stub", realtime=False)
        resp = narrate_plan(resp, enabled=True)
        theme = resp.days[0].theme
        tips = [it.tips for d in resp.days for it in d.items]
        warn = [w for w in resp.warnings if "[narrate]" in str(w)]
        print("  主题：%s" % (theme or "(空)"))
        print("  每站 tips：%d 条，首条 = %s" % (len(tips), (tips[0] if tips else "(空)")))
        print("  [narrate] 告警：%s" % (warn or "无（说明模型真的生效了）"))
        print("  汇总：%s" % (resp.meta.get("narrative", {}).get("summary", ""))[:80])
        if warn:
            print("  ⚠ 仍走了规则兜底 —— 说明这次调用失败了，往上翻错误原因。")

    print("\n结论：可以用了。演示前记得**重启** api.server，页面上的 [narrate] 提示会消失。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
