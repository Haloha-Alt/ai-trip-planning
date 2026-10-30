# demo_outputs · 断网兜底样例

> 用途：**M6 彩排 / 答辩现场的兜底**。服务起不来、没网、Key 失效时，打开这里任意一份 JSON，
> 内容与网页上会看到的完全一致，可以照着念。
>
> ⚠️ 这些文件**不能**放 `outputs/`：那个目录被 `.gitignore` 排除，队友 clone 下来是空的。

## 一、为什么是这几份

它们一一对应 `docs/submission/答辩讲稿.md` §2 的三条演示主线。

| 文件 | 对应演示 | 你会看到什么 |
|---|---|---|
| `demo_daily.json` | 主线 A | 默认那句话（`daily` + `stub`）。「别太赶」→ relaxed，**故意只排 2 站**：伯川图书馆 / 令希图书馆 |
| `demo_multiday.json` | 主线 B 地图 | 2 天 6 站，两天两种颜色，用来讲「地图按天分色、站名不叠」 |
| `demo_wait_open.json` | 主线 B 时间轴 | 06:20 出发 → 第一站前有**橙色「等开门」段**（AC-45 第 5 条） |
| `demo_close_skip.json` | 主线 B 闭馆 | 16:50 出发、兴趣「看病/邮政」→ 17:00 关门的点**不进候选**，`warnings` 里没有 `CLOSED`（AC-59） |
| `demo_short_window.json` | 主线 B 空窗 | 17:00–17:30 太短 → **0 站** + `NO_CANDIDATE`，不崩（AC-42） |
| `demo_no_matrix.json` | AC-64 | 城市切北京 → `meta.data.matrix_edges = 0`，「在途时间按直线估算」 |
| `demo_compare.json` | 主线 C | 四算法对比结论：`「greedy」总分 0.5807，比基线「random」高 13.5%，在 2 项指标上更优` |

每份的结构都是 `{_note, _how, request_body, response}`：
`_note` 是给答辩人看的一句话说明，`request_body` 是原样的请求体（可直接 `curl` 复现），
`response` 就是 `/api/plan`（或 `/api/compare`）的完整返回。

## 二、怎么重新生成

```bat
cd /d D:\projects\ai-trip-planner
.venv\Scripts\python.exe -m tools.make_demo_outputs --verify    :: 只校验，不改文件
.venv\Scripts\python.exe -m tools.make_demo_outputs             :: 重新生成
```

`--verify` 会拿当前引擎重跑一遍再和文件比对（**剔除 `generated_at`**，它是唯一每次都变的字段），
报 `DIFF` 时会指出具体字段。**彩排前一天跑一次**：全部 `[ ok ]` 才算兜底还有效。

## 三、断网时照着讲

1. 关掉 Wi-Fi / 停掉 `api.server`，打开 `demo_daily.json` 的 `response.plan`；
2. 指标看 `metrics`，站点看 `days[0].items[*]`（`name / arrive / leave / reason / cost`）；
3. 被问「这是编的吧」—— 现场再跑一次 `--verify`，`[ ok ]` 说明文件就是引擎刚算出来的；
4. 想看完整页面而不是 JSON：`.venv\Scripts\python.exe main.py --request mock\case_multiday.json`
   仍然能出两日行程（命令行兜底路径）。

## 四、已知局限（被问就照实说，别现场找理由）

1. **没有同类去重**：`demo_multiday` 里出现两个图书馆、三个食堂。
   引擎按「净收益最高」逐站选点，没有"同类别计数"约束 —— 这是真实缺陷，不是笔误。
   演示时优先用 `demo_daily` / `demo_close_skip` 讲，或把 `top_n` 降到 2；
   如果你想把这条修掉，属于引擎行为变更（要重跑 `eval`、改 `04` AC-49 的数字），不要临场改。
2. **「顺便取快递」排不进去**：4 个快递点（`菜鸟驿站(西山)` / `京东快递点` / `邮政服务点` / `快递柜(北山)`）
   的净收益都输给食堂。要演示"必去点真的必去"，用 `must_visit`（AC-61）。
3. **北京只有 2 站**：`data/cities/beijing/pois.csv` 12 条全是景点，与「日常出行」定位不符，
   这份样例只用来证明"换城市不崩 + 无实测路网会如实说"，不代表北京能规划。
4. **`generated_at`** 每次生成都不同，属正常；比对时已剔除。
