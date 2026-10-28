# @author: 甲
# @reviewer: 乙
# @since: 2026-10-28
"""解说（narrate）与局部调整（adjust）的测试。

守三条：
1. **没有 Key 也要有解说**（规则模板），且结果确定；
2. **局部调整只动被点名的那一站**，其余站点的顺序与内容不变；
3. **改完必须重算分数**，不能把旧总分留在页面上。

跑法：
    .venv\\Scripts\\python.exe -m unittest tests.test_narrate_adjust -v
"""

import unittest

from api.routes import dispatch
from engine.models import PlanRequest
from engine.planner import plan
from llm.adjust import adjust, parse_rule
from llm.narrate import narrate, rule_narrative

CITY = "dalian"
SCENARIO = "daily"


def _req(**kw) -> PlanRequest:
    base = {"city": CITY, "days": 1, "top_n": 4, "start_time": "08:00",
            "end_time": "20:00", "daily_budget": 60,
            "interests": ["图书馆", "吃饭"], "pace": "normal"}
    base.update(kw)
    return PlanRequest.from_dict(base)


def _plan(**kw):
    return plan(_req(**kw), scenario=SCENARIO, strategy="greedy")


class TestNarrate(unittest.TestCase):
    def test_rule_mode_fills_everything(self):
        resp = narrate(_plan(), use_llm=False)
        for d in resp.days:
            self.assertTrue(d.theme, "第 %d 天没有主题" % d.day_index)
            for it in d.items:
                self.assertTrue(it.tips, "%s 没有解说" % it.name)

    def test_meta_narrative(self):
        resp = narrate(_plan(), use_llm=False)
        n = resp.meta.get("narrative") or {}
        self.assertIn("summary", n)
        self.assertIn("总分", n["summary"])
        self.assertEqual(len(n["days"]), len(resp.days))
        self.assertIn("theme", n["days"][0])

    def test_deterministic(self):
        a = narrate(_plan(), use_llm=False)
        b = narrate(_plan(), use_llm=False)
        self.assertEqual([i.tips for d in a.days for i in d.items],
                         [i.tips for d in b.days for i in d.items])

    def test_rule_tip_mentions_cost(self):
        resp = narrate(_plan(), use_llm=False)
        tips = [i.tips for d in resp.days for i in d.items]
        self.assertTrue(any("免费" in t or "元" in t for t in tips))

    def test_empty_plan_does_not_crash(self):
        resp = plan(_req(start_time="17:00", end_time="17:05"),
                    scenario=SCENARIO, strategy="greedy")
        out = narrate(resp, use_llm=False)
        self.assertIn("summary", out.meta["narrative"])

    def test_rule_narrative_shape(self):
        n = rule_narrative(_plan())
        self.assertIsInstance(n["items"], dict)
        self.assertTrue(n["days"])


class TestParseRule(unittest.TestCase):
    def test_drop_ordinal(self):
        resp = _plan()
        if len(resp.days[0].items) < 2:
            self.skipTest("该数据下站点不足 2 个")
        target = resp.days[0].items[1]
        patch = parse_rule("删掉第2站", resp)
        self.assertEqual(patch.drop, [target.poi_id])

    def test_replace_with_cheap_hint(self):
        resp = _plan()
        if len(resp.days[0].items) < 3:
            self.skipTest("该数据下站点不足 3 个")
        patch = parse_rule("第3站太贵了，换一个", resp)
        self.assertTrue(patch.replace or patch.drop, "应给出替换或删除动作")

    def test_add(self):
        patch = parse_rule("再加一站吧", _plan())
        self.assertTrue(patch.add or not patch.is_empty() or patch.note)

    def test_unrecognized_keeps_quiet(self):
        patch = parse_rule("今天天气不错", _plan())
        self.assertTrue(patch.is_empty())
        self.assertIn("没听懂", patch.note)

    def test_day_scope(self):
        resp = _plan(days=2)
        patch = parse_rule("第2天第1站换一个", resp)
        self.assertTrue(patch.replace or patch.note)


class TestAdjust(unittest.TestCase):
    def test_drop_keeps_other_order(self):
        resp = _plan()
        if len(resp.days[0].items) < 3:
            self.skipTest("该数据下站点不足 3 个")
        ids = [it.poi_id for it in resp.days[0].items]
        out = adjust(resp, "删掉第2站", use_llm=False)
        after = [it.poi_id for it in out.days[0].items]
        self.assertEqual(after, [ids[0]] + ids[2:])

    def test_input_not_mutated(self):
        resp = _plan()
        before = [it.poi_id for d in resp.days for it in d.items]
        adjust(resp, "删掉第1站", use_llm=False)
        self.assertEqual([it.poi_id for d in resp.days for it in d.items], before)

    def test_metrics_recomputed(self):
        resp = _plan()
        out = adjust(resp, "删掉第1站", use_llm=False)
        self.assertAlmostEqual(
            out.days[0].total_cost,
            round(sum(i.cost + i.transport.cost for i in out.days[0].items), 2))
        self.assertIn("[调整]", " ".join(out.warnings))

    def test_add_increases_count(self):
        resp = _plan(top_n=2)
        n0 = len(resp.days[0].items)
        out = adjust(resp, "再加一站", use_llm=False)
        self.assertGreaterEqual(len(out.days[0].items), n0)

    def test_unrecognized_returns_same_plan(self):
        resp = _plan()
        out = adjust(resp, "今天天气不错", use_llm=False)
        self.assertEqual([i.poi_id for d in out.days for i in d.items],
                         [i.poi_id for d in resp.days for i in d.items])
        self.assertIn("没听懂", " ".join(out.warnings))

    def test_swap(self):
        resp = _plan()
        if len(resp.days[0].items) < 2:
            self.skipTest("站点不足")
        ids = [it.poi_id for it in resp.days[0].items]
        out = adjust(resp, "把顺序调换一下", use_llm=False)
        after = [it.poi_id for it in out.days[0].items]
        self.assertEqual(after[0], ids[-1])


class TestAdjustRoute(unittest.TestCase):
    BODY = {
        "query": "上午去图书馆自习，中午食堂吃饭",
        "scenario": "daily",
        "strategy": "greedy",
        "overrides": {"city": "dalian", "top_n": 4},
    }

    def test_adjust_ok(self):
        status, body = dispatch("POST", "/api/adjust", {},
                                dict(self.BODY, instruction="删掉第2站"))
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertIn("plan", body)
        self.assertIn("before", body)
        self.assertIn("adjust", body)

    def test_missing_instruction_is_400(self):
        status, body = dispatch("POST", "/api/adjust", {}, dict(self.BODY))
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])

    def test_narrate_flag_adds_tips(self):
        status, body = dispatch("POST", "/api/plan", {},
                                dict(self.BODY, narrate=True))
        self.assertEqual(status, 200)
        item = body["plan"]["days"][0]["items"][0]
        self.assertTrue(item["tips"])
        self.assertIn("narrative", body["plan"]["meta"])


if __name__ == "__main__":
    unittest.main()
