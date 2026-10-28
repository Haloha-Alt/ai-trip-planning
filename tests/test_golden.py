# @author: 甲
# @reviewer: 乙
# @since: 2026-10-27
"""黄金样例测试：把"契约键集合" + "stub 策略的行程骨架" + "评分基线"钉死。

为什么需要它
------------
`test_models.py` / `test_engine.py` 验的是"结构正确、不崩"，没验"输出长什么样"。
而后续改造（多城市、实时数据、换前端、换算法）最容易悄悄改掉的三件事正是：

1. **键集合** —— 加字段没同步文档，前端读不到 / 读错；
2. **站点顺序与时刻** —— 改了排序或铺时间轴，结果悄悄变了；
3. **评分基线** —— `PlanMetrics` 被动了一根手指，AC-31 的历史口径就对不上。

本文件把这三件钉死。**它是机器可读版的 AC-50 / AC-51 / AC-31。**

跑法
----
    .venv\\Scripts\\python.exe -m unittest tests.test_golden -v

基线怎么更新（**确认变化是有意的之后再跑**）
----
    .venv\\Scripts\\python.exe -c "from tests.test_golden import regenerate; regenerate()"

   然后必须把改动写进提交信息，并在 `docs/04-验收清单.md` 的 AC-31 表格里更新数字。

设计取舍
--------
- 基线里**不放** `generated_at`、`meta`、`realtime`：这些每次运行都变/都是留痕，
  放进去只会制造噪声红。它们由 `test_response_key_set` 保证"存在且只多不少"。
- 基线里也**不放** `reason` / `tips`：文案会随 LLM 与提示词迭代，
  属于"允许变化"的部分；骨架（去哪、几点、多久、多少钱）才是必须稳的。
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from engine.models import (
    CONTRACT_VERSION,
    PlanMetrics,
    PlanRequest,
    PlanResponse,
)
from engine.planner import plan

ROOT = Path(__file__).resolve().parents[1]
GOLDEN_DIR = ROOT / "tests" / "golden"
MOCK = ROOT / "mock" / "case_basic.json"
GOLDEN = GOLDEN_DIR / "case_basic_stub.json"
SCHEMA = ROOT / "schemas" / "itinerary.schema.json"


# ---------------------------------------------------------------- 期望键集合（= AC-50）

EXPECT_TOP_KEYS = {
    "days", "generated_at", "meta", "metrics", "realtime",
    "request", "scenario", "strategy", "warnings",
}
EXPECT_REQUEST_KEYS = {
    "avoid", "budget_basis", "city", "daily_budget", "date", "days",
    "deadline", "profile", "waypoints",            # v1.3 追加
    "end_time", "interests", "mobility", "must_visit", "origin", "pace",
    "raw_query", "seed", "start_poi_id", "start_time", "top_n", "travelers",
}
EXPECT_DAY_KEYS = {
    "date", "day_index", "items", "theme", "total_cost",
    "total_distance_km", "total_stay_min", "total_travel_min", "weather",
}
EXPECT_ITEM_KEYS = {
    "address", "arrive", "category", "cost", "flags", "lat", "leave", "lon",
    "name", "poi_id", "reason", "seq", "stay_min", "tips", "transport", "url",
}


# ---------------------------------------------------------------- 工具


def _req_from_mock() -> PlanRequest:
    d = json.loads(MOCK.read_text(encoding="utf-8"))
    return PlanRequest.from_dict(d.get("request") or {})


def _resp() -> PlanResponse:
    """确定性入口：固定场景 demo + 固定策略 stub。"""
    return plan(_req_from_mock(), scenario="demo", strategy="stub")


def _round(v: float) -> float:
    return round(float(v), 4)


def _fingerprint(resp: PlanResponse) -> dict:
    """只留业务骨架：去哪、几点、多久、多少钱、总分。"""
    return {
        "strategy": resp.strategy,
        "scenario": resp.scenario,
        "metrics": {k: _round(v) for k, v in resp.metrics.to_dict().items()},
        "days": [
            {
                "day_index": d.day_index,
                "total_cost": _round(d.total_cost),
                "total_stay_min": d.total_stay_min,
                "total_travel_min": _round(d.total_travel_min),
                "total_distance_km": _round(d.total_distance_km),
                "items": [
                    {
                        "seq": i.seq,
                        "poi_id": i.poi_id,
                        "name": i.name,
                        "arrive": i.arrive,
                        "leave": i.leave,
                        "stay_min": i.stay_min,
                        "cost": _round(i.cost),
                        "transport_mode": i.transport.mode,
                    }
                    for i in d.items
                ],
            }
            for d in resp.days
        ],
    }


def regenerate() -> None:
    """重写黄金基线。确认行为变化是有意的之后再调用。"""
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    text = json.dumps(_fingerprint(_resp()), ensure_ascii=False, indent=2)
    GOLDEN.write_text(text + "\n", encoding="utf-8")
    print("已重生成：%s" % GOLDEN)


# ---------------------------------------------------------------- 测试


class TestKeySet(unittest.TestCase):
    """机器可读版的 AC-50 / AC-51。"""

    def _out(self) -> dict:
        return _resp().to_dict()

    def test_top_keys(self):
        self.assertEqual(sorted(self._out().keys()), sorted(EXPECT_TOP_KEYS))

    def test_request_keys(self):
        self.assertEqual(sorted(self._out()["request"].keys()), sorted(EXPECT_REQUEST_KEYS))

    def test_day_keys(self):
        self.assertEqual(sorted(self._out()["days"][0].keys()), sorted(EXPECT_DAY_KEYS))

    def test_item_keys(self):
        self.assertEqual(sorted(self._out()["days"][0]["items"][0].keys()), sorted(EXPECT_ITEM_KEYS))

    def test_schema_request_keys_match_models(self):
        """AC-51：schemas/*.json 的 request 键 == 运行时 request 键。"""
        s = json.loads(SCHEMA.read_text(encoding="utf-8"))
        got = sorted(s["properties"]["request"]["properties"].keys())
        self.assertEqual(got, sorted(EXPECT_REQUEST_KEYS))

    def test_contract_version_still_v1(self):
        self.assertEqual(CONTRACT_VERSION, "v1")


class TestMetricsFrozen(unittest.TestCase):
    """PlanMetrics 一个字段都不能动 —— 动了 AC-31 的历史口径就不可比。"""

    def test_fields_are_exactly_five(self):
        self.assertEqual(
            sorted(PlanMetrics.__dataclass_fields__),
            ["cost", "distance", "feasibility", "satisfaction", "time"],
        )

    def test_overall_baseline(self):
        """case_basic + stub 的总分基线（见 docs/04-验收清单.md AC-31）。"""
        self.assertAlmostEqual(_resp().metrics.overall(), 0.8309, places=3)


class TestGoldenItinerary(unittest.TestCase):
    def test_golden_file_exists(self):
        self.assertTrue(GOLDEN.exists(), "缺黄金基线：%s（用 regenerate() 生成）" % GOLDEN)

    def test_itinerary_matches_golden(self):
        if not GOLDEN.exists():
            self.skipTest("黄金基线不存在，先跑 regenerate()")
        expect = json.loads(GOLDEN.read_text(encoding="utf-8"))
        self.assertEqual(_fingerprint(_resp()), expect)

    def test_golden_has_stations(self):
        """兜底：基线本身不能是空行程 —— 空的基线等于没锁住任何东西。"""
        if not GOLDEN.exists():
            self.skipTest("黄金基线不存在")
        expect = json.loads(GOLDEN.read_text(encoding="utf-8"))
        self.assertGreater(len(expect["days"][0]["items"]), 0)


if __name__ == "__main__":
    unittest.main()
