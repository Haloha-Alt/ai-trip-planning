# @author: 甲
# @reviewer: 乙
# @since: 2026-10-30
"""生成 `docs/demo_outputs/` 的断网兜底样例（M6 彩排用）。

为什么要有这个目录：
    `outputs/` 被 `.gitignore` 排除，队友 clone 下来是空的 —— 现场断网/服务起不来时
    手里什么都没有。所以把"照着念也讲得通"的几份响应固化进版本控制，
    放在 `docs/demo_outputs/`（在库里，能 clone 到）。

为什么用 `dispatch` 而不是 `main.py`：
    走的是网页 `/api/plan` 同一条路径，导出的 JSON 就是评委在页面上会看到的同一份数据，
    不会出现"兜底文件和屏幕对不上"的尴尬。

跑法（项目根目录）：
    .venv\\Scripts\\python.exe -m tools.make_demo_outputs            # 重新生成
    .venv\\Scripts\\python.exe -m tools.make_demo_outputs --verify   # 只校验，不改文件

约定：全部 `realtime=false` —— 同输入必得同输出。**唯一会变的是 `generated_at`**，
所以校验时把它剔掉再比对（`--verify` 就是干这个的）：
彩排前一天跑一次，若报 DIFF 说明引擎行为改了，兜底文件必须一起重生成。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.routes import dispatch  # noqa: E402  必须在 sys.path 补好之后导入

OUT_DIR = ROOT / "docs" / "demo_outputs"

# (文件名, 给答辩人看的说明, 送给 /api/plan 的 body)
PLAN_CASES = [
    ("demo_daily.json",
     "主线A：默认文本框那句话（daily + stub）。「别太赶」= relaxed，故意只排 2 站。",
     {"query": "上午去图书馆自习，中午食堂吃饭，顺便取快递，别太赶",
      "scenario": "daily", "strategy": "stub", "narrate": True}),
    ("demo_multiday.json",
     "主线B：两日行程，每天 3 站。讲地图两色、时间轴两天两条。",
     {"query": "两天，每天三个地方，上午自习中午吃饭",
      "overrides": {"days": 2, "top_n": 3, "pace": "normal"},
      "scenario": "daily", "strategy": "stub", "narrate": True}),
    ("demo_wait_open.json",
     "主线B-3：出发 06:20。第一站前面出现橙色「等开门」段（AC-45 第 5 条）。",
     {"query": "上午去图书馆自习，中午食堂吃饭，别太赶",
      "overrides": {"start_time": "06:20", "pace": "normal", "top_n": 3},
      "scenario": "daily", "strategy": "stub", "narrate": True}),
    ("demo_close_skip.json",
     "主线B-5：16:50 出发、兴趣看病/邮政。17:00 关门的点不进候选（AC-59），无 CLOSED。",
     {"request": {"city": "dalian", "top_n": 4, "start_time": "16:50",
                  "end_time": "21:30", "interests": ["看病", "邮政"]},
      "scenario": "daily", "strategy": "stub", "narrate": True}),
    ("demo_short_window.json",
     "主线B-4：17:00–17:30 太短。页面/命令行显示空行程 + NO_CANDIDATE，不崩（AC-42）。",
     {"query": "随便走走",
      "overrides": {"start_time": "17:00", "end_time": "17:30", "top_n": 3},
      "scenario": "daily", "strategy": "stub", "narrate": True}),
    ("demo_no_matrix.json",
     "AC-64：切到北京。无实测路网，meta.data.matrix_edges = 0，页面写明按直线估算。",
     {"query": "想看人文类的地方",
      "overrides": {"city": "beijing", "top_n": 2, "daily_budget": 200},
      "scenario": "daily", "strategy": "stub", "narrate": True}),
]

COMPARE_BODY = {
    "query": "上午自习，中午吃饭，顺便取快递，别太赶",
    "scenario": "daily",
    "realtime": False,
}


def _dump(path: Path, note: str, how: str, body: dict, payload: dict) -> None:
    doc = {"_note": note, "_how": how, "request_body": body, "response": payload}
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def _strip_time(payload: dict) -> dict:
    """去掉 `generated_at` 再比 —— 它是唯一每次都会变的东西。"""
    out = json.loads(json.dumps(payload, ensure_ascii=False))   # 深拷贝
    plan = out.get("plan")
    if isinstance(plan, dict):
        plan.pop("generated_at", None)
    return out


def _first_diff(a, b, path: str = "", limit: int = 5) -> list[str]:
    """报差异时把"哪几个字段不一样"说出来，否则只写 DIFF 等于没说。"""
    out: list[str] = []

    def walk(x, y, p):
        if len(out) >= limit:
            return
        if type(x) is not type(y):
            out.append("%s: %r -> %r" % (p, x, y))
            return
        if isinstance(x, dict):
            for k in sorted(set(x) | set(y)):
                if k not in x or k not in y:
                    out.append("%s/%s: 只在一侧存在" % (p, k))
                else:
                    walk(x[k], y[k], p + "/" + k)
        elif isinstance(x, list):
            if len(x) != len(y):
                out.append("%s: 长度 %d -> %d" % (p, len(x), len(y)))
            for i, (i1, i2) in enumerate(zip(x, y)):
                walk(i1, i2, "%s/%d" % (p, i))
        elif x != y:
            out.append("%s: %r -> %r" % (p, x, y))

    walk(a, b, path)
    return out


def verify() -> int:
    """只校验，不改文件：兜底文件是否仍与当前引擎产出的一致。"""
    bad = 0
    jobs = [(n, b, "/api/plan", _strip_time) for n, _, b in PLAN_CASES]
    jobs.append(("demo_compare.json", COMPARE_BODY, "/api/compare", lambda p: p))

    for name, body, route, norm in jobs:
        path = OUT_DIR / name
        if not path.exists():
            print("[miss] %s 不存在，先跑一次不带 --verify 的生成" % name)
            bad += 1
            continue
        saved = json.loads(path.read_text(encoding="utf-8")).get("response") or {}
        status, fresh = dispatch("POST", route, {}, body)
        if status != 200 or not fresh.get("ok"):
            print("[err ] %s 重新生成失败：%s %s" % (name, status, fresh))
            bad += 1
            continue
        if json.dumps(norm(saved), ensure_ascii=False, sort_keys=True) == \
                json.dumps(norm(fresh), ensure_ascii=False, sort_keys=True):
            print("[ ok ] %s 与当前引擎一致" % name)
        else:
            print("[DIFF] %s 与当前引擎不一致 —— 引擎改过了，请重新生成并说明原因" % name)
            for line in _first_diff(norm(saved), norm(fresh)):
                print("        %s" % line)
            bad += 1

    print("\n%s" % ("全部一致" if not bad else "有 %d 个文件需要处理" % bad))
    return 0 if not bad else 1


def main(argv: list[str]) -> int:
    if "--verify" in argv:
        return verify()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[tuple[str, str, str]] = []

    for name, note, body in PLAN_CASES:
        status, payload = dispatch("POST", "/api/plan", {}, body)
        if status != 200 or not payload.get("ok"):
            print("[err] %s -> %s %s" % (name, status, payload))
            return 1
        _dump(OUT_DIR / name, note,
              "api.routes.dispatch POST /api/plan，realtime=false", body, payload)

        plan = payload["plan"]
        items = sum(len(d["items"]) for d in plan["days"])
        names = " / ".join(i["name"] for d in plan["days"] for i in d["items"]) or "（空行程）"
        rows.append((name, "%d 天 %d 站" % (len(plan["days"]), items), names))

    status, payload = dispatch("POST", "/api/compare", {}, COMPARE_BODY)
    if status != 200 or not payload.get("ok"):
        print("[err] demo_compare.json -> %s %s" % (status, payload))
        return 1
    _dump(OUT_DIR / "demo_compare.json",
          "主线C：同一份需求跑全部算法的对比结论（★ 最优行 + 相对基线的百分比）。",
          "api.routes.dispatch POST /api/compare，baseline=random",
          COMPARE_BODY, payload)
    rows.append(("demo_compare.json", "对比", str(payload.get("summary") or "")))

    for name, size, detail in rows:
        print("%-24s %-10s %s" % (name, size, detail))
    print("\n已写入 %s（%d 个文件）" % (OUT_DIR, len(rows)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
