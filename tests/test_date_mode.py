# @author: 助手
# @since: 2026-11-04
"""约会模式（date 场景）的离线测试。

覆盖六件事：
1. 场景配置能加载，默认策略是 date，数据路由到 pois_date.csv；
2. zone 过滤：req.zone 非空时候选只在商圈内；
3. date 策略：锚点钉死在时刻上（arrive=锚点时刻、leave=时刻+时长）；
4. date 策略：锚点之前的候选"玩得完还赶得上下一场"；
5. 锚点校验：没排入 / 时刻非法 / 赶路来不及 → ANCHOR_MISS，晚到只告警不拒绝；
6. 无锚点时退化为普通贪心的一天（有产出、不空）。

全程离线：不启服务器、不抓实时数据、不调大模型。
跑法：
    python -m unittest tests.test_date_mode -v
"""

import unittest

from engine.config import load_scenario
from engine.models import PlanRequest
from engine.planner import plan


def _brief(resp) -> list[str]:
    return [i.name for d in resp.days for i in d.items]


def _items(resp):
    return [i for d in resp.days for i in d.items]


class TestScenarioConfig(unittest.TestCase):
    """date.toml 必须能被引擎发现并加载。"""

    def test_scenario_listed_and_loaded(self):
        self.assertIn("date", __import__("engine.config", fromlist=["list_scenarios"]).list_scenarios())
        cfg = load_scenario("date")
        self.assertEqual(cfg.name, "date")
        self.assertEqual(cfg.default_strategy, "date")

    def test_data_routes_to_date_csv(self):
        """date 场景的数据文件必须是约会点位表（场景配置优先于城市注册表）。"""
        from data.loader import load_pois

        cfg = load_scenario("date")
        pois = load_pois(cfg, "dalian")
        self.assertTrue(pois, "pois_date.csv 没有被加载")
        zones = {p.zone for p in pois}
        self.assertIn("西安路", zones)
        self.assertIn("星海", zones)
        cats = {p.category for p in pois}
        self.assertIn("电影", cats, "约会数据里必须有电影院")


class TestZoneFilter(unittest.TestCase):
    """req.zone 非空时候选只在商圈内。"""

    def test_zone_confines_candidates(self):
        from data.loader import load_pois

        cfg = load_scenario("date")
        zones = {p.zone for p in load_pois(cfg, "dalian")}
        resp = plan(PlanRequest(city="dalian", zone="星海", top_n=5,
                                start_time="10:00", end_time="22:00"),
                    scenario="date", strategy="date")
        names = _brief(resp)
        self.assertTrue(names, "zone=星海 应该有产出")
        for it in _items(resp):
            self.assertNotEqual(it.poi_id, "", "行程项必须有 poi_id")

    def test_bad_zone_still_produces(self):
        """写错商圈名不该返回空行程 —— 放开 zone 过滤即可。"""
        resp = plan(PlanRequest(city="dalian", zone="不存在的商圈", top_n=3,
                                start_time="10:00", end_time="22:00"),
                    scenario="date", strategy="date")
        self.assertTrue(_brief(resp))


class TestDateStrategyAnchors(unittest.TestCase):
    """锚点（电影开场/餐厅订座）是约会的骨架。"""

    def test_anchor_pinned_to_time(self):
        """电影 14:30 开场 → 行程里这一站 arrive=14:30、leave=16:30，不漂移。"""
        req = PlanRequest(city="dalian", zone="星海", start_time="10:00",
                          end_time="22:00", top_n=5,
                          anchors=[{"poi": "星海CGV影城", "time": "14:30",
                                    "duration_min": 120, "label": "电影"}])
        resp = plan(req, scenario="date", strategy="date")
        hit = [i for i in _items(resp) if i.name == "星海CGV影城"]
        self.assertEqual(len(hit), 1, "锚点必须恰好排入一次：%s" % _brief(resp))
        self.assertEqual(hit[0].arrive, "14:30")
        self.assertEqual(hit[0].leave, "16:30")
        self.assertEqual(hit[0].stay_min, 120)

    def test_fill_ends_before_next_anchor(self):
        """锚点之前的候选：玩完还要来得及赶下一场（在途时间算进去）。"""
        req = PlanRequest(city="dalian", zone="星海", start_time="10:00",
                          end_time="22:00", top_n=6,
                          anchors=[{"poi": "星海CGV影城", "time": "14:00",
                                    "duration_min": 120, "label": "电影"}])
        resp = plan(req, scenario="date", strategy="date")
        items = _items(resp)
        idx = next(i for i, it in enumerate(items) if it.name == "星海CGV影城")
        self.assertEqual(items[idx].arrive, "14:00")
        if idx > 0:                       # 有前置填充站时，离开 + 在途必须 <= 锚点时刻
            from engine.geo import to_minutes
            from engine.strategies import _edge_between
            from data.loader import get_poi

            cfg = load_scenario("date")
            prev = items[idx - 1]
            pa = get_poi(prev.poi_id, cfg, "dalian")
            pb = get_poi(items[idx].poi_id, cfg, "dalian")
            e = _edge_between(pa, pb, req, cfg)
            slack = to_minutes("14:00") - (to_minutes(prev.leave) + e.minutes)
            self.assertGreaterEqual(slack, 0,
                                    "上一站 %s 离开后赶不上 14:00 的电影" % prev.name)

    def test_two_anchors_ordered(self):
        """电影 + 晚餐两个锚点：按时刻先后落位，互相不打架。"""
        req = PlanRequest(city="dalian", zone="星海", start_time="09:30",
                          end_time="22:00", top_n=6,
                          anchors=[{"poi": "星海渔港(星海广场店)", "time": "18:00",
                                    "duration_min": 90, "label": "晚餐"},
                                   {"poi": "星海CGV影城", "time": "14:00",
                                    "duration_min": 120, "label": "电影"}])
        resp = plan(req, scenario="date", strategy="date")
        items = _items(resp)
        pos = {it.name: i for i, it in enumerate(items)}
        self.assertIn("星海CGV影城", pos)
        self.assertIn("星海渔港(星海广场店)", pos)
        self.assertLess(pos["星海CGV影城"], pos["星海渔港(星海广场店)"],
                        "14:00 的电影应排在 18:00 的晚餐之前")
        self.assertEqual([i for i in items if i.name == "星海CGV影城"][0].arrive, "14:00")
        self.assertEqual([i for i in items if i.name == "星海渔港(星海广场店)"][0].arrive, "18:00")

    def test_no_anchor_falls_back_to_greedy(self):
        """没给锚点：date 策略退化为贪心，照样有产出，不返回空行程。"""
        resp = plan(PlanRequest(city="dalian", zone="西安路", start_time="10:00",
                                end_time="22:00", top_n=4),
                    scenario="date", strategy="date")
        self.assertTrue(_brief(resp), "无锚点也应排出行程")


class TestAnchorValidation(unittest.TestCase):
    """ANCHOR_MISS：晚到只告警不拒绝；没排入/格式非法要如实报。"""

    def test_late_travel_warns_not_rejects(self):
        """出发太晚 + 锚点太早 → 行程照出，但要有 ANCHOR_MISS 告警。

        给了 origin（黑石礁公园）才有在途可算：步行过去要半小时以上，
        14:00 出发赶 14:10 的电影必然迟到。
        """
        req = PlanRequest(city="dalian", zone="星海", start_time="14:00",
                          end_time="22:00", top_n=4, origin="黑石礁公园",
                          mobility="walk",
                          anchors=[{"poi": "星海CGV影城", "time": "14:10",
                                    "duration_min": 120, "label": "电影"}])
        resp = plan(req, scenario="date", strategy="date")
        self.assertTrue(_brief(resp), "晚到只告警，行程不该被拒绝")
        self.assertTrue(any("ANCHOR_MISS" in w for w in resp.warnings),
                        "赶不上电影必须报 ANCHOR_MISS：%s" % resp.warnings)

    def test_unresolvable_anchor_warns(self):
        """poi 认不出：报 ANCHOR_MISS，不抛异常。"""
        req = PlanRequest(city="dalian", start_time="10:00", end_time="22:00",
                          anchors=[{"poi": "不存在的地方", "time": "14:00"}])
        resp = plan(req, scenario="date", strategy="date")
        self.assertTrue(any("找不到对应地点" in w for w in resp.warnings))

    def test_bad_time_format_warns(self):
        req = PlanRequest(city="dalian", start_time="10:00", end_time="22:00",
                          anchors=[{"poi": "星海CGV影城", "time": "25:99"}])
        resp = plan(req, scenario="date", strategy="date")
        self.assertTrue(any("不是合法的 HH:MM" in w for w in resp.warnings))

    def test_valid_plan_has_no_anchor_warning(self):
        """排得开的锚点不该误报。"""
        req = PlanRequest(city="dalian", zone="星海", start_time="10:00",
                          end_time="22:00", top_n=4,
                          anchors=[{"poi": "星海CGV影城", "time": "15:00",
                                    "duration_min": 120, "label": "电影"}])
        resp = plan(req, scenario="date", strategy="date")
        anchor_warns = [w for w in resp.warnings if "ANCHOR_MISS" in w]
        self.assertEqual(anchor_warns, [], "排得开的锚点不该误报：%s" % anchor_warns)


class TestOldStrategiesUnaffected(unittest.TestCase):
    """契约只增不改：老策略在老场景下行为不变（zone/anchors 为空时不生效）。"""

    def test_daily_stub_still_works(self):
        resp = plan(PlanRequest(city="dalian", interests=["自习"],
                                start_time="08:00", end_time="12:00", top_n=2),
                    scenario="daily", strategy="stub")
        self.assertTrue(_brief(resp))
        self.assertFalse(any("ANCHOR_MISS" in w for w in resp.warnings))


# ================================================================ v1.6.3
# 约会模式独立化：城市口径 + 筛选细化 + 一句话优先


class TestQueryOverrides(unittest.TestCase):
    """一句话行程里的硬时刻/点名地点要被认出来 —— 且只认写明了的。"""

    def _q(self, text):
        from service.date_mode import query_overrides

        return query_overrides(text, city="dalian")

    def test_explicit_time_with_movie_keyword(self):
        q = self._q("晚上看19:40的电影，别太赶")
        self.assertEqual(q["movie_time"], "19:40")

    def test_chinese_time_half(self):
        q = self._q("下午2点半看电影")
        self.assertEqual(q["movie_time"], "14:30")

    def test_chinese_time_evening(self):
        q = self._q("晚上7点的电影")
        self.assertEqual(q["movie_time"], "19:00")

    def test_explicit_time_zero_padded(self):
        """9:30 必须补零成 09:30 —— 引擎只认 HH:MM。"""
        q = self._q("9:30的电影")
        self.assertEqual(q["movie_time"], "09:30")

    def test_dinner_time(self):
        q = self._q("晚上6点半订晚餐")
        self.assertEqual(q["dinner_time"], "18:30")

    def test_unrelated_time_not_picked(self):
        """没带电影/吃饭字样的时间不脑补到锚点上。"""
        q = self._q("18:00 前得到家")
        self.assertEqual(q["movie_time"], "")
        self.assertEqual(q["dinner_time"], "")

    def test_zone_from_text(self):
        q = self._q("今天在星海约会")
        self.assertEqual(q["zone"], "星海")

    def test_named_pois(self):
        """点名「星海渔港」要匹配到「星海渔港(星海广场店)」并当晚餐候选。"""
        q = self._q("晚上去星海渔港吃晚饭")
        self.assertEqual(q["dinner_poi"], "星海渔港(星海广场店)")

    def test_named_cinema(self):
        q = self._q("在星海CGV影城看电影")
        self.assertEqual(q["movie_poi"], "星海CGV影城")

    def test_empty_text_is_noop(self):
        q = self._q("")
        self.assertEqual(q["movie_time"], "")
        self.assertEqual(q["must_visit"], [])

    def test_movie_time_range_picks_start(self):
        """「看18:05到20:25的电影」是区间写法：场次是 18:05，不是散场的 20:25。"""
        q = self._q("然后去看18:05到20:25的电影")
        self.assertEqual(q["movie_time"], "18:05")

    def test_movie_time_range_fullwidth_colon(self):
        """全角冒号的区间同样认：18：05到20：25的电影 -> 18:05。"""
        q = self._q("然后去看18：05到20：25的电影")
        self.assertEqual(q["movie_time"], "18:05")

    def test_time_range_without_keyword_noop(self):
        """区间没挨着电影/吃饭关键字就别脑补。"""
        q = self._q("18:05到20:25都在忙")
        self.assertEqual(q["movie_time"], "")
        self.assertEqual(q["dinner_time"], "")


class TestCityDateRouting(unittest.TestCase):
    """约会模式以城市为地点：每个城市自己的约会数据包说了算，不借别人的。"""

    def test_dalian_routes_to_its_date_pack(self):
        from data.loader import load_pois

        pois = load_pois(load_scenario("date"), "dalian")
        self.assertTrue(pois)
        self.assertIn("西安路", {p.zone for p in pois})

    def test_city_without_date_pack_gets_empty_pool(self):
        """北京没建约会数据包：候选为空（宁可如实报空，不假装是大连的数据）。"""
        from data.loader import load_pois

        pois = load_pois(load_scenario("date"), "beijing")
        self.assertEqual(pois, [])

    def test_options_expose_date_cities(self):
        from service.usecases import options

        dc = options()["date_cities"]
        names = {c["key"]: c["name"] for c in dc}
        self.assertEqual(names.get("dalian"), "大连")   # 城市名，不是校名
        self.assertIn("beijing", names)


class TestQueryPriorityOverPanel(unittest.TestCase):
    """一句话行程与面板筛选冲突时，以一句话为准（走 /api/date/brief 全链路）。"""

    def _dispatch(self, body):
        from api.routes import dispatch

        return dispatch("POST", "/api/date/brief", {}, body)

    def test_text_time_beats_panel_time(self):
        """面板选 11:20 的场，一句话写 19:40 → 钉 19:40，且冲突留痕。"""
        status, payload = self._dispatch({
            "query": "晚上看19:40的电影", "city": "dalian", "zone": "西安路",
            "start_time": "10:00", "no_llm": True,
            "movie": {"poi": "DL205", "time": "11:20"},
        })
        self.assertEqual(status, 200, payload.get("error"))
        items = [i for d in payload["plan"]["days"] for i in d["items"]]
        cinema = [i for i in items if i["poi_id"] == "DL205"]
        self.assertTrue(cinema, "电影必须被排入")
        self.assertEqual(cinema[0]["arrive"], "19:40")
        qov = payload["plan"]["meta"].get("date_query_overrides") or {}
        self.assertTrue(any("19:40" in c for c in qov.get("conflicts", [])),
                        "冲突必须留痕：%s" % qov)

    def test_text_poi_beats_panel_poi(self):
        """面板选西安路万达（DL205），一句话点名星海CGV → 去星海CGV。"""
        status, payload = self._dispatch({
            "query": "在星海CGV影城看14:30的电影", "city": "dalian",
            "zone": "西安路", "start_time": "10:00", "no_llm": True,
            "movie": {"poi": "DL205", "time": "14:30"},
        })
        self.assertEqual(status, 200, payload.get("error"))
        items = [i for d in payload["plan"]["days"] for i in d["items"]]
        hit = [i for i in items if i["poi_id"] == "DL230"]
        self.assertTrue(hit, "一句话点名的影院应取代面板选择：%s"
                        % [i["name"] for i in items])
        self.assertEqual(hit[0]["arrive"], "14:30")

    def test_no_conflict_keeps_panel_choice(self):
        """一句话没写时刻时，面板选的场次原样生效 —— 不许误伤。"""
        status, payload = self._dispatch({
            "query": "想约会，轻松一点", "city": "dalian", "zone": "西安路",
            "start_time": "10:00", "no_llm": True,
            "movie": {"poi": "DL205", "time": "14:30"},
        })
        self.assertEqual(status, 200, payload.get("error"))
        items = [i for d in payload["plan"]["days"] for i in d["items"]]
        cinema = [i for i in items if i["poi_id"] == "DL205"]
        self.assertTrue(cinema)
        self.assertEqual(cinema[0]["arrive"], "14:30")

    def test_custom_anchor_passthrough(self):
        """自定义固定安排（面板外任意地点+时刻）与场次一样是硬锚点。"""
        status, payload = self._dispatch({
            "query": "随便走走", "city": "dalian", "zone": "星海",
            "start_time": "10:00", "no_llm": True,
            "anchors": [{"poi": "跨海大桥观景点", "time": "17:30",
                         "duration_min": 45, "label": "看落日"}],
        })
        self.assertEqual(status, 200, payload.get("error"))
        items = [i for d in payload["plan"]["days"] for i in d["items"]]
        hit = [i for i in items if i["name"] == "跨海大桥观景点"]
        self.assertTrue(hit)
        self.assertEqual(hit[0]["arrive"], "17:30")


class TestTipsFlow(unittest.TestCase):
    """v1.6：POI.tips（数据层"怎么玩"）一路流进行程项与导出物。"""

    def test_poi_loads_tips_column(self):
        from data.loader import load_pois

        pois = load_pois(load_scenario("date"), "dalian")
        d234 = next(p for p in pois if p.poi_id == "DL234")
        self.assertIn("凌滨路", d234.tips, "DL234 的 tips 应含真实到达方式")
        self.assertTrue(all(p.tips for p in pois),
                        "pois_date.csv 31 行 tips 都不能留空")

    def test_plan_items_carry_tips(self):
        resp = plan(PlanRequest(city="dalian", zone="星海", top_n=3,
                                start_time="10:00", end_time="22:00"),
                    scenario="date", strategy="date")
        items = _items(resp)
        self.assertTrue(items)
        self.assertTrue(all(i.tips for i in items),
                        "引擎排出的每站都应带上数据层 tips：%s"
                        % [(i.name, i.tips) for i in items])

    def test_markdown_contains_detailed_sections(self):
        from service.exporters import to_markdown

        resp = plan(PlanRequest(city="dalian", zone="星海", top_n=3,
                                start_time="10:00", end_time="22:00"),
                    scenario="date", strategy="date")
        md = to_markdown(resp)
        for section in ("### 逐段交通", "### 每站详解", "## 预算明细",
                        "## 数据口径", "怎么玩："):
            self.assertIn(section, md, "详细行程单缺一节：%s" % section)

    def test_markdown_lists_locked_anchors(self):
        from service.exporters import to_markdown

        resp = plan(PlanRequest(
            city="dalian", zone="西安路", start_time="14:00", end_time="23:00",
            anchors=[{"poi": "DL205", "time": "18:05", "duration_min": 140,
                      "label": "电影 18:05-20:25"}]),
            scenario="date", strategy="date")
        md = to_markdown(resp)
        self.assertIn("## 关键提醒（锁定时刻）", md)
        self.assertIn("18:05", md)

    def test_brief_fallback_uses_tips(self):
        """无模型时，简报"每站安排"优先说怎么玩，而不是干巴巴的排程理由。"""
        from llm.date_brief import briefing

        resp = plan(PlanRequest(city="dalian", zone="星海", top_n=3,
                                start_time="10:00", end_time="22:00"),
                    scenario="date", strategy="date")
        b = briefing(resp, zone="星海", client=None, enabled=False)
        stops = {i.name: i for i in _items(resp)}
        said = {s["name"]: s["say"] for s in b["per_stop"]}
        for name, it in stops.items():
            if it.tips:
                self.assertEqual(said.get(name), it.tips,
                                 "%s 的简报应直接用 tips" % name)


class TestDetailedExportV3(unittest.TestCase):
    """v1.6.4 v3：行程单向真实数据版模板看齐（标题日期/Plan B/收尾富余）。"""

    def test_plan_b_grouped_for_outdoor_stops(self):
        """同商圈多个户外站共用同一批室内备选时，Plan B 必须合并成一行。"""
        resp = plan(PlanRequest(city="dalian", zone="星海", top_n=5,
                                start_time="10:00", end_time="22:00"),
                    scenario="date", strategy="date")
        items = _items(resp)
        outdoor = [i for i in items if i.category == "户外"]
        if not outdoor:
            self.skipTest("该随机种子没排出户外站")
        pb = resp.meta.get("plan_b") or []
        self.assertTrue(pb, "有户外站就该有 Plan B：%s" % [i.name for i in outdoor])
        backup_sets = [tuple(b.get("backups") or []) for b in pb]
        self.assertEqual(len(backup_sets), len(set(backup_sets)),
                         "备份集合相同的 Plan B 必须去重合并")

    def test_markdown_title_carries_date_and_weekday(self):
        from service.exporters import to_markdown

        resp = plan(PlanRequest(city="dalian", zone="星海", top_n=2,
                                start_time="10:00", end_time="22:00",
                                date="2026-11-07"),
                    scenario="date", strategy="date")
        md = to_markdown(resp)
        self.assertIn("# 行程单（2026-11-07 · 周六）", md)

    def test_markdown_plan_b_and_what_column(self):
        from service.exporters import to_markdown

        resp = plan(PlanRequest(city="dalian", zone="星海", top_n=5,
                                start_time="10:00", end_time="22:00"),
                    scenario="date", strategy="date")
        md = to_markdown(resp)
        self.assertIn("这一站做什么", md, "总表必须有'做什么'列")
        if resp.meta.get("plan_b"):
            self.assertIn("## Plan B 速查", md)

    def test_markdown_origin_first_segment_and_buffer(self):
        """出发地如实上屏（不编在途分钟数）；收尾富余必须能算出来。"""
        from service.exporters import to_markdown

        resp = plan(PlanRequest(city="dalian", zone="星海", top_n=3,
                                start_time="10:00", end_time="22:00",
                                origin="大工凌水校区"),
                    scenario="date", strategy="date")
        md = to_markdown(resp)
        self.assertIn("大工凌水校区", md)
        self.assertIn("富余约", md)
        self.assertNotIn("DL207", md, "锚点/行程里不许再裸奔 poi_id")


class TestRealtimeDataV5(unittest.TestCase):
    """v1.6.5 实时数据源：城市级 landmarks / last_trains / 出租计价 / 导出消费。"""

    def test_loader_routes_landmarks_and_last_trains(self):
        """dalian 有两份新表，beijing 没有 → 空表，不借别的城市。"""
        from data.loader import load_landmarks, load_last_trains
        cfg = load_scenario("date")
        lm = load_landmarks(cfg, "dalian")
        self.assertTrue(any(r["name"] == "大工凌水校区" for r in lm))
        lt = load_last_trains(cfg, "dalian")
        self.assertTrue(any("星海广场" in (r["station"] or "") for r in lt))
        self.assertEqual(load_landmarks(cfg, "beijing"), [])
        self.assertEqual(load_last_trains(cfg, "beijing"), [])

    def test_scenario_taxi_pricing(self):
        cfg = load_scenario("date")
        self.assertEqual(cfg.taxi.get("start_price"), 10.0)
        self.assertEqual(cfg.taxi.get("start_km"), 3.0)
        self.assertEqual(cfg.taxi.get("per_km"), 2.0)

    def test_planner_origin_return_leg_and_last_trains(self):
        """origin 命中地标 → meta 出发/返程腿按 [taxi] 计价；末班按前两字兜底命中。"""
        resp = plan(PlanRequest(city="dalian", zone="星海", top_n=3,
                                start_time="14:00", end_time="23:00",
                                origin="大工凌水校区"),
                    scenario="date", strategy="date")
        oleg = resp.meta.get("origin_leg") or {}
        self.assertTrue(oleg, "origin 应命中 landmarks.csv 得到出发腿")
        self.assertGreater(oleg.get("fare", 0), 0)
        self.assertIn("出租车起步价", oleg.get("basis") or "")
        rleg = resp.meta.get("return_leg") or {}
        self.assertTrue(rleg, "应有返程腿")
        lt = resp.meta.get("last_trains") or []
        self.assertTrue(lt, "星海片区行程应兜底命中星海广场站末班")

    def test_markdown_weather_bad_and_origin_fare_and_trains(self):
        from service.exporters import to_markdown
        resp = plan(PlanRequest(city="dalian", zone="星海", top_n=3,
                                start_time="14:00", end_time="23:00",
                                origin="大工凌水校区"),
                    scenario="date", strategy="date")
        resp.realtime = {"weather": [{"date": "2026-11-07", "condition": "rain",
                                      "temp_c": 6.0, "precip_mm": 2.0,
                                      "wind_kmh": 15.0}]}
        md = to_markdown(resp)
        self.assertIn("当天预报有雨雪", md)
        self.assertIn("打车约", md)
        self.assertIn("¥", md)
        self.assertIn("末班", md)
        # v1.6.6 起末班会自动判定：赶得上 → 「散场即动身」；赶不上 → 「打车回校」
        self.assertTrue(("打车回校" in md) or ("散场即动身" in md),
                        "末班判定两条路都该有文案：%s" % md[-600:])

    def test_markdown_no_weather_no_trains_honest(self):
        """拿不到数据时如实说，不编。"""
        from service.exporters import to_markdown
        resp = plan(PlanRequest(city="dalian", zone="星海", top_n=2,
                                start_time="14:00", end_time="23:00"),
                    scenario="date", strategy="date")
        md = to_markdown(resp)
        self.assertIn("未获取实时数据", md)


if __name__ == "__main__":
    unittest.main()
