# @author: 助手
# @since: 2026-11-04
"""「详细详细详细」行程编排（v1.6）的行为测试。

钉住五件事：
1. **三校都要详细**：dalian / hit / tsinghua × visit / daily 都能产出完整结构，
   每站都有看什么 / 怎么走 / 为什么 / 可达性 / 时间提示 / 备选 / 事实溯源；
2. **非破坏性**：详细行程一个数字都不改主行程（站点 / 时刻 / 停留 / 顺序）；
3. **事实层真正接进运行时**：入校渠道、来源、查阅日期、访客简报都出现在 meta；
4. **模型改不了事实**：模型输出只覆盖白名单文案，时刻 / 顺序 / 事实来源碰不到；
   模型挂了走同样详细的规则模板；
5. **零回归**：`/api/plan` 不传 `detailed` 时行为逐字不变。

跑法：
    python -m unittest tests.test_detailed_itinerary -v
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from api.routes import dispatch
from data.loader import clear_cache
from llm.guide import load_guide
from llm.itinerary import _merge_llm, build, enrich
from service.usecases import (
    build_request,
    detailed_itinerary_plan,
    plan_from_request,
)

QUERY = "上午去图书馆自习，中午在食堂吃饭，下午随便逛逛拍照"
CAMPUSES = ("dalian", "hit", "tsinghua")
SCENARIOS = ("visit", "daily")

_STEP_KEYS = {
    "seq", "day", "poi_id", "name", "category", "arrive", "leave", "stay_min",
    "cost", "transport", "what_to_see", "how_to_walk", "why_here", "access",
    "timing_tip", "backup", "facts", "source", "llm_guide", "llm_why_here",
    "llm_backup", "enhanced_by",
}


def _plan(scenario: str, city: str):
    clear_cache()
    req = build_request(QUERY, scenario=scenario,
                        overrides={"city": city, "top_n": 5}, use_llm=False)
    return plan_from_request(req, scenario=scenario)


def _snapshot(resp):
    return [(it.poi_id, it.arrive, it.leave, it.stay_min, it.seq)
            for d in resp.days for it in d.items]


class _FakeClient:
    """只提供 build() 需要的接口，绝不发网络请求。"""

    enabled = True
    model = "fake-itinerary-test"

    def json_chat(self, system, user, **kwargs):
        return {}


class TestDetailedStructure(unittest.TestCase):
    """规则兜底本身就要「详细详细详细」—— 没有 Key 也不能退化成一句话。"""

    def test_all_campuses_all_scenarios_are_complete(self):
        for sc in SCENARIOS:
            for city in CAMPUSES:
                with self.subTest(scenario=sc, city=city):
                    resp = _plan(sc, city)
                    before = _snapshot(resp)
                    detailed_itinerary_plan(resp, query=QUERY, enabled=False)
                    d = resp.meta.get("detailed_itinerary") or {}

                    self.assertEqual(before, _snapshot(resp),
                                     "%s/%s 详细行程改动了主行程骨架" % (sc, city))
                    self.assertEqual(d.get("generated_by"), "rules")
                    self.assertFalse(d.get("model_active"))
                    self.assertGreaterEqual(len(d.get("steps") or []), 5,
                                            "%s/%s 站点不足" % (sc, city))
                    self.assertEqual(len(d["steps"]), len(before))

                    for s in d["steps"]:
                        self.assertEqual(set(s.keys()), _STEP_KEYS)
                        self.assertGreaterEqual(len(s["what_to_see"]), 3,
                                                "%s 看什么太少" % s["name"])
                        for k in ("how_to_walk", "why_here", "timing_tip", "backup"):
                            self.assertTrue(str(s[k]).strip(), "%s 缺 %s" % (s["name"], k))
                        self.assertTrue(s["facts"], "%s 缺事实溯源" % s["name"])
                        self.assertIn("level", s["access"])
                        self.assertIn("label", s["access"])
                        self.assertIn("verified", s["source"])
                        self.assertEqual(s["enhanced_by"], "rules")

    def test_top_level_sections_present(self):
        resp = detailed_itinerary_plan(_plan("visit", "hit"), query=QUERY, enabled=False)
        d = resp.meta["detailed_itinerary"]
        for k in ("version", "model_status", "campus", "campus_key", "headline",
                  "generated_by", "model_active", "model", "query", "architecture",
                  "before_you_go", "overview", "steps", "alternatives", "verify",
                  "notes", "sources", "rules_vs_llm"):
            self.assertIn(k, d, "缺顶层字段 " + k)
        self.assertEqual(set(d["architecture"].keys()), {"engine", "facts", "llm"})
        self.assertIn("audience_hint", d["overview"])
        self.assertIn("window_note", d["overview"])
        self.assertIn("why_this_plan", d["overview"])
        self.assertIn("rules_only", d["rules_vs_llm"])
        self.assertTrue(d["rules_vs_llm"]["llm_adds"])
        self.assertTrue(d["alternatives"])
        self.assertTrue(d["sources"])


class TestFactLayerWiredIn(unittest.TestCase):
    """事实层不再是「只在测试里被调用」，而是真的进了 /api/plan 的返回值。"""

    def test_fact_card_fields_are_used(self):
        for city in CAMPUSES:
            with self.subTest(city=city):
                guide = load_guide(city)
                resp = detailed_itinerary_plan(_plan("visit", city), query=QUERY, enabled=False)
                d = resp.meta["detailed_itinerary"]
                b = d["before_you_go"]
                self.assertTrue(b["booking"], "%s 缺入校渠道" % city)
                self.assertEqual(len(b["booking"]), len(guide["entry"]["channels"]))
                self.assertTrue(b["requires"])
                self.assertTrue(b["on_arrival"])
                self.assertTrue(b["transit"])
                self.assertEqual(len(d["sources"]), len(guide["sources"]))
                self.assertTrue(d["verify"])
                self.assertTrue(d["notes"])
                self.assertEqual(d["architecture"]["facts"]["updated"], guide["updated"])

    def test_visitor_briefing_in_meta(self):
        resp = detailed_itinerary_plan(_plan("visit", "tsinghua"), query=QUERY, enabled=False)
        brief = resp.meta.get("visitor_briefing") or {}
        self.assertTrue(brief.get("headline"))
        self.assertTrue(brief.get("before"))
        self.assertTrue(brief.get("timeline"))
        # 简报走规则模板，不许把渠道/电话交给模型改写
        self.assertIn("参观清华", " ".join(brief["before"]))

    def test_poi_source_traceable(self):
        """高德包带查阅日期，种子包进待核实 —— 数据纪律要在界面上说清。"""
        hit = detailed_itinerary_plan(_plan("visit", "hit"), enabled=False)
        dl = detailed_itinerary_plan(_plan("visit", "dalian"), enabled=False)
        hit_src = {s["source"]["provider"] for s in hit.meta["detailed_itinerary"]["steps"]}
        self.assertIn("amap", hit_src)
        dl_verify = " ".join(dl.meta["detailed_itinerary"]["verify"])
        self.assertIn("未标注来源适用日期", dl_verify)


class TestModelCannotTouchFacts(unittest.TestCase):
    """模型优越性体现在措辞，不体现在「能改事实」。"""

    def _rule_detail(self):
        resp = _plan("visit", "hit")
        return build(resp, guide=load_guide("hit"), query=QUERY, city="hit",
                     client=None, enabled=False)

    def test_merge_only_overwrites_whitelisted_wording(self):
        base = self._rule_detail()
        original = {
            "arrive": base["steps"][0]["arrive"],
            "name": base["steps"][0]["name"],
            "facts": [dict(f) for f in base["steps"][0]["facts"]],
            "headline": base["headline"],
        }
        payload = {
            "headline": "航天馆打头，一路不折返",
            "why_this_plan": "先去最远的展馆，再顺着主路回校门，避开中午人流。",
            "steps": [{
                "seq": 1,
                "guide": "进校后先沿主路往南，看到苏式主楼立面再往科学园方向走；"
                         "这一站是全程最花时间的一站，建议把拍照留到光线好的时候。",
                "why_here": "放在首站，因为体力最好、且不受午后闭馆影响。",
                "backup": "若临时不开放，改为外观打卡并把时间顺延给下一站。",
                # 下面这些是模型想偷偷改的事实，必须被忽略
                "arrive": "99:99", "name": "编出来的地点",
                "telephone": "000-00000000", "facts": [{"label": "编的", "value": "编的"}],
            }],
            "alternatives": [{"when": "下雨", "do": "把户外站压缩到 10 分钟，先看室内展馆"}],
            "llm_adds": ["按「想拍照」把取景建议写进了导览", "解释了首站为什么放最花时间的一站"],
        }
        out = _merge_llm(base, payload)

        self.assertEqual(out["generated_by"], "rules+llm")
        self.assertTrue(out["model_active"])
        self.assertEqual(out["steps"][0]["enhanced_by"], "llm")
        self.assertEqual(out["steps"][0]["llm_guide"], payload["steps"][0]["guide"])
        self.assertEqual(out["steps"][0]["arrive"], original["arrive"])
        self.assertEqual(out["steps"][0]["name"], original["name"])
        self.assertEqual(out["steps"][0]["facts"], original["facts"])
        self.assertEqual(out["headline"], payload["headline"])
        self.assertEqual(out["overview"]["why_this_plan"], payload["why_this_plan"])
        self.assertEqual(out["alternatives"], payload["alternatives"])
        self.assertEqual(out["rules_vs_llm"]["llm_adds"], payload["llm_adds"])
        # 后续站点没被模型点名 → 保持规则版
        self.assertEqual(out["steps"][1]["enhanced_by"], "rules")

    def test_garbage_model_output_falls_back_to_full_rules(self):
        for bad in (None, [], "not a dict", {"steps": "oops"},
                    {"steps": [{"seq": 1, "guide": "x"}]}):   # guide 太短 → 丢弃
            with self.subTest(bad=repr(bad)):
                out = _merge_llm(self._rule_detail(), bad)
                self.assertEqual(out["generated_by"], "rules")
                self.assertFalse(out["model_active"])
                self.assertTrue(out["steps"])
                self.assertTrue(out["steps"][0]["what_to_see"])

    def test_build_with_model_client(self):
        """走完整 build() 链路（护栏被 patch，不发网络、不落缓存）。"""
        resp = _plan("visit", "tsinghua")
        payload = {"headline": "从二校门开始，半天走完清华园",
                   "steps": [{"seq": 1, "guide": "先在二校门前取一张全景，再沿中轴往大礼堂方向慢走，"
                                                "上午光线顺，拍照不用逆光；记得走人行通道别占车行道。"}],
                   "llm_adds": ["按「下午拍照」调整了取景顺序"]}
        with patch("llm.guard.guarded_json", return_value=payload):
            d = build(resp, guide=load_guide("tsinghua"), query=QUERY,
                      city="tsinghua", client=_FakeClient(), enabled=True)
        self.assertEqual(d["generated_by"], "rules+llm")
        self.assertTrue(d["model_active"])
        self.assertEqual(d["steps"][0]["enhanced_by"], "llm")

    def test_enrich_never_breaks_main_plan(self):
        resp = _plan("visit", "hit")
        before = _snapshot(resp)
        with patch("llm.itinerary.build", side_effect=RuntimeError("boom")):
            enrich(resp, enabled=False)
        self.assertEqual(before, _snapshot(resp))
        d = resp.meta["detailed_itinerary"]
        self.assertIn("error", d)
        self.assertIn("主行程", d["headline"])

    def test_unknown_city_does_not_crash(self):
        """纯城市 / 没配事实卡时，详细行程仍可生成（只是没有校方事实）。"""
        clear_cache()
        req = build_request("想看海", scenario="demo",
                            overrides={"city": "beijing", "top_n": 3}, use_llm=False)
        resp = plan_from_request(req, scenario="demo")
        detailed_itinerary_plan(resp, query="想看海", enabled=False)
        d = resp.meta["detailed_itinerary"]
        self.assertEqual(d["generated_by"], "rules")
        self.assertTrue(d["steps"])


class TestModelStatusVisible(unittest.TestCase):
    """模型没生效时，页面必须说清「为什么」，而不是让用户对着没变化的页面猜。

    起因：本机 .env 里的 LLM_API_KEY 已 401 失效，勾选「模型开」后
    generated_by 仍是 rules，但前端只看得到「模板兜底」四个字。
    """

    def _base(self):
        return _plan("visit", "tsinghua")

    def test_disabled_and_no_key(self):
        d = build(self._base(), guide=load_guide("tsinghua"), query=QUERY,
                  city="tsinghua", client=None, enabled=False)
        self.assertEqual(d["model_status"]["state"], "disabled")
        self.assertFalse(d["model_status"]["active"])

        d2 = build(self._base(), guide=load_guide("tsinghua"), query=QUERY,
                   city="tsinghua", client=None, enabled=True)
        self.assertEqual(d2["model_status"]["state"], "no_key")
        self.assertIn("LLM_API_KEY", d2["model_status"]["reason"])

    def test_failed_call_reports_reason_and_warning(self):
        def fake(client, system, user, **kw):
            cb = kw.get("on_warn")
            if cb:
                cb("[itinerary] 调用失败（已重试 1 次）：HTTP 401："
                   "Authentication Fails, Your api key: ****sczj is invalid → 已回退规则结果")
            return None

        warns = []
        with patch("llm.guard.guarded_json", side_effect=fake):
            d = build(self._base(), guide=load_guide("tsinghua"), query=QUERY,
                      city="tsinghua", client=_FakeClient(), enabled=True,
                      warnings=warns)
        ms = d["model_status"]
        self.assertEqual(ms["state"], "failed")
        self.assertFalse(ms["active"])
        self.assertIn("401", ms["reason"])
        self.assertTrue(ms["warnings"])
        self.assertEqual(warns, ms["warnings"], "告警要同时进 plan.warnings")
        self.assertIn("模型未生效", d["rules_vs_llm"]["llm_adds"][0])
        self.assertEqual(d["generated_by"], "rules")
        self.assertTrue(d["steps"])

    def test_garbage_output_is_ignored_but_explained(self):
        with patch("llm.guard.guarded_json", return_value={"steps": "oops"}):
            d = build(self._base(), guide=load_guide("tsinghua"), query=QUERY,
                      city="tsinghua", client=_FakeClient(), enabled=True)
        self.assertEqual(d["model_status"]["state"], "ignored")
        self.assertFalse(d["model_active"])
        self.assertIn("未通过校验", d["model_status"]["reason"])

    def test_active_model_reports_active(self):
        payload = {"headline": "从二校门开始，半天走完清华园",
                   "steps": [{"seq": 1, "guide": "先在二校门前取一张全景，再沿中轴往大礼堂方向慢走，"
                                                "上午光线顺，拍照不用逆光；记得走人行通道别占车行道。"}]}
        with patch("llm.guard.guarded_json", return_value=payload):
            d = build(self._base(), guide=load_guide("tsinghua"), query=QUERY,
                      city="tsinghua", client=_FakeClient(), enabled=True)
        self.assertEqual(d["model_status"]["state"], "active")
        self.assertTrue(d["model_status"]["active"])
        self.assertEqual(d["model"], _FakeClient.model)
        self.assertTrue(d["model_active"])

    def test_enrich_fallback_also_has_status(self):
        resp = self._base()
        with patch("llm.itinerary.build", side_effect=RuntimeError("boom")):
            enrich(resp, enabled=False)
        self.assertEqual(resp.meta["detailed_itinerary"]["model_status"]["state"], "failed")


class TestApiContract(unittest.TestCase):
    """detailed 是显式开关：不传时老行为逐字不变（测试/CLI/第三方零回归）。"""

    BODY = {
        "query": QUERY, "scenario": "visit", "strategy": "greedy",
        "overrides": {"city": "hit", "top_n": 5},
        "no_llm": True,
    }

    def test_detailed_true_returns_full_structure(self):
        status, body = dispatch("POST", "/api/plan", {}, dict(self.BODY, detailed=True))
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        meta = body["plan"]["meta"]
        d = meta["detailed_itinerary"]
        self.assertEqual(d["generated_by"], "rules")
        self.assertGreaterEqual(len(d["steps"]), 5)
        self.assertIn("state", d["model_status"])
        self.assertFalse(d["model_status"]["active"])
        self.assertTrue(meta["visitor_briefing"]["before"])
        self.assertTrue(body["plan"]["days"])

    def test_detailed_omitted_keeps_old_behavior(self):
        status, body = dispatch("POST", "/api/plan", {}, dict(self.BODY))
        self.assertEqual(status, 200)
        meta = body["plan"]["meta"]
        self.assertNotIn("detailed_itinerary", meta)
        self.assertNotIn("visitor_briefing", meta)

    def test_all_three_campuses_via_api(self):
        for city in CAMPUSES:
            with self.subTest(city=city):
                b = dict(self.BODY, detailed=True)
                b["overrides"] = {"city": city, "top_n": 5}
                status, body = dispatch("POST", "/api/plan", {}, b)
                self.assertEqual(status, 200)
                d = body["plan"]["meta"]["detailed_itinerary"]
                self.assertEqual(d["campus_key"], city)
                self.assertGreaterEqual(len(d["steps"]), 5)
                self.assertTrue(d["before_you_go"]["booking"])


class TestScenarioFork(unittest.TestCase):
    """v1.6.1 按身份分叉：学生（daily）不再看到访客入校须知。

    这是用户实测报出的退化：「为什么我选的是校内游览，出来的却是入校预约？」
    根因是 before_you_go 访客模板写死、无条件输出 —— 现在必须分叉。
    """

    def test_daily_has_no_visitor_entry_and_has_life_tips(self):
        resp = detailed_itinerary_plan(_plan("daily", "tsinghua"), query=QUERY, enabled=False)
        d = resp.meta["detailed_itinerary"]
        self.assertEqual(d.get("before_you_go"), {}, "daily 不该有访客入校须知")
        life = d.get("campus_life") or {}
        self.assertTrue(life.get("tips"), "daily 缺校园生活提示")
        self.assertEqual(life.get("audience"), "校内学生")
        self.assertEqual(d["overview"]["audience_hint"], "本校学生 · 校园日常")

    def test_daily_steps_never_say_visitor_gate(self):
        resp = detailed_itinerary_plan(_plan("daily", "tsinghua"), query=QUERY, enabled=False)
        d = resp.meta["detailed_itinerary"]
        first = d["steps"][0]
        self.assertNotIn("到校", first["how_to_walk"])
        self.assertNotIn("进校", first["how_to_walk"])
        for s in d["steps"]:
            self.assertEqual(s["access"]["label"], "校内开放（凭校园卡）",
                             "%s 学生视角不该出现访客可达性口径" % s["name"])
            self.assertNotIn("不对访客开放", s["access"]["note"])

    def test_visit_keeps_entry_and_has_no_life_section(self):
        resp = detailed_itinerary_plan(_plan("visit", "tsinghua"), query=QUERY, enabled=False)
        d = resp.meta["detailed_itinerary"]
        self.assertTrue(d["before_you_go"]["booking"], "visit 必须保留入校渠道")
        self.assertEqual(d.get("campus_life"), {}, "visit 不该有校园生活提示")

    def test_visitor_briefing_only_for_visit(self):
        daily = detailed_itinerary_plan(_plan("daily", "tsinghua"), query=QUERY, enabled=False)
        self.assertEqual(daily.meta.get("visitor_briefing"), {})
        visit = detailed_itinerary_plan(_plan("visit", "tsinghua"), query=QUERY, enabled=False)
        self.assertTrue((visit.meta.get("visitor_briefing") or {}).get("before"))

    def test_daily_campus_facts_no_visitor_keywords(self):
        """v1.6.1 追加：学生视图的 notes/verify/sources 也不得混入访客入校类事实。

        离线渲染验证曾抓到 ⑤注意事项/⑦事实来源 泄漏「参观清华小程序预约渠道、
        入校时段、警惕黄牛」等访客内容 —— 这里钉死回归。
        """
        resp = detailed_itinerary_plan(_plan("daily", "tsinghua"), query=QUERY, enabled=False)
        d = resp.meta["detailed_itinerary"]
        blob = (" ".join(d["notes"]) + " " + " ".join(d["verify"]) + " "
                + " ".join(s["fact"] for s in d["sources"]))
        for k in ("预约", "入校", "黄牛", "身份证", "访客"):
            self.assertNotIn(k, blob, "学生视图混入访客事实关键词: " + k)
        # 访客视图反向钉死：入校渠道来源必须还在
        visit = detailed_itinerary_plan(_plan("visit", "tsinghua"), query=QUERY, enabled=False)
        dv = visit.meta["detailed_itinerary"]
        vblob = (" ".join(dv["notes"]) + " "
                 + " ".join(s["fact"] for s in dv["sources"]))
        self.assertIn("预约", vblob, "访客视图应保留入校渠道相关来源")


class TestStudentAnchors(unittest.TestCase):
    """v1.6.1 课表锚点：daily 场景 + anchors，锚点时刻被钉死。

    约会策略的「按锚点切段、段内贪心」是通用能力：上课 08:00 开讲与
    电影 14:30 开场对排程是同一件事。greedy/two_opt 遇到锚点委托给切段逻辑。
    """

    def test_anchor_is_pinned_in_daily(self):
        base = _plan("daily", "tsinghua")
        names = [it.name for day in base.days for it in day.items]
        self.assertTrue(names)
        req = build_request(QUERY, scenario="daily",
                            overrides={"city": "tsinghua",
                                       "anchors": [{"poi": names[0], "time": "12:00",
                                                    "duration_min": 45, "label": "午饭"}]})
        resp = plan_from_request(req, scenario="daily")
        items = [it for day in resp.days for it in day.items]
        hit = [it for it in items if it.name == names[0]]
        self.assertTrue(hit, "锚点点位没被排入")
        self.assertEqual(hit[0].arrive, "12:00", "锚点时刻必须钉死")
        self.assertEqual(hit[0].stay_min, 45)
        self.assertIn("锚点", hit[0].reason)

    def test_two_opt_also_delegates_when_anchors(self):
        base = _plan("daily", "hit")
        names = [it.name for day in base.days for it in day.items]
        req = build_request(QUERY, scenario="daily",
                            overrides={"city": "hit",
                                       "anchors": [{"poi": names[0], "time": "12:00",
                                                    "duration_min": 45}]})
        resp = plan_from_request(req, scenario="daily", strategy="two_opt")
        items = [it for day in resp.days for it in day.items]
        hit = [it for it in items if it.name == names[0]]
        self.assertTrue(hit)
        self.assertEqual(hit[0].arrive, "12:00")

    def test_no_anchors_keeps_plain_greedy(self):
        req = build_request(QUERY, scenario="daily", overrides={"city": "tsinghua"})
        resp = plan_from_request(req, scenario="daily")
        self.assertTrue(resp.days and resp.days[0].items, "无锚点时贪心行为不变")


if __name__ == "__main__":
    unittest.main()
