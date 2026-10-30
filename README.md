# AI 出行与规划

> 大连理工大学《AI 大模型及其运用》课程项目 · 2 人小组
> 当前状态：**功能完成**（M0–M4 收尾，M5 交付物在 `docs/submission/`）
> 场景已锁定：**日常出行**（校园生活圈 46 个点，不是游客一日游）

---

## 一、环境要求

- Python **≥ 3.11**（用到标准库 `tomllib`）；本项目开发用 **3.14.6**
- 依赖：`streamlit`、`requests`、`python-dotenv`（见 `requirements.txt`）
- **引擎与数据层零第三方依赖**：只用标准库（`csv` / `tomllib` / `dataclasses` / `unittest`）。
  2026-10-28 起移除了 pandas —— 数据层改标准库 `csv`，换机器不会因为 DLL 被策略拦截而跑不起来。

---

## 二、五条命令跑起来

在 PyCharm 底部 Terminal（工作目录自动是项目根）里执行：

```bash
# 1) 命令行跑通整条链路（默认场景 daily / 默认算法 stub / 默认城市 dalian）
.venv\Scripts\python.exe main.py --request mock\case_multiday.json

# 2) 打开网页界面（四页签：行程表 / 时间轴 / 地图 / 算法对比）
.venv\Scripts\python.exe -m api.server        # 然后打开 http://127.0.0.1:8000/

# 3) 另一个界面（Streamlit，答辩时可作为对照）
.venv\Scripts\python.exe -m streamlit run ui/app.py

# 4) 跑测试，确认项目健康（当前 210 个）
.venv\Scripts\python.exe -m unittest discover -s tests -t .

# 5) 跑基线对比（4 个算法 × 6 个用例，结果写入 eval/results.md）
.venv\Scripts\python.exe -m eval.baseline --scenario daily

# 6) 断网兜底样例：校验 7 份 JSON 是否仍与当前引擎一致（M6 必跑）
.venv\Scripts\python.exe -m tools.make_demo_outputs --verify

# 开发期可选：不开浏览器验证前端画得对不对（13 项断言 + 生成静态预览页）
node _render_check.js
```

> ⚠️ **第 7 条（前端自检）需要 Node.js，本机不一定装**。PyCharm 的终端不继承 WorkBuddy 的 PATH，
> 所以直接敲 `node` 会报 `CommandNotFoundException: 无法将"node"项识别为 cmdlet` —— 这不是项目坏了。
> 三种处置，任选：① 跳过（它只是开发期自检，**不在评分项里**）；
> ② 用绝对路径跑：`C:\Users\18055\.workbuddy\binaries\node\versions\22.22.2\node.exe _render_check.js`；
> ③ 装一次 Node LTS 后重开终端。
> **答辩机建议按 ① 处理**：真正必跑的是 Python 那几条（测试 / options / 兜底校验）。

> 三种入口用的是**同一份**业务逻辑（`service/usecases.py`）：
> 命令行 `main.py` ｜ Streamlit `ui/app.py` ｜ 网页 `api.server` + `web/index.html`。
> 所以"网页上看到的结果"和"命令行跑出来的"必然一致，不会出现两套口径。

> ⚠️ **不要用终端裸跑 `pip install`**，很可能装到别的 Python 上，然后 PyCharm 里依然报 `ModuleNotFoundError`。
> 装库一律走：`Settings (Ctrl+Alt+S) → Project → Python Interpreter → "+" → Install Package`。
>
> ⚠️ **不要对 `ui/app.py` 点绿色三角**，页面不会出来。Streamlit 必须用上面第 2 条命令启动，
> 或在 PyCharm 里新建运行配置：Module name = `streamlit`，Parameters = `run ui/app.py`，Working directory = 项目根。

详细操作见 `docs/使用手册.md`。

---

## 三、目录结构

```
.
├── main.py                 命令行入口：读场景 → 解析 → 规划 → 打印 + 落盘 JSON
├── config\scenarios\       ★ 业务参数全在这里（daily / commute / errand / rainy / tight-budget）
├── data\                   ★ POI 与交通矩阵；data\cities\<key>\ 按城市分目录
├── engine\
│   ├── models.py           ★★ 接口契约（唯一的类定义，冻结文件，v1.4）
│   ├── config.py           场景配置加载
│   ├── geo.py              距离 / 时间 / 费用估算
│   ├── constraints.py      硬约束校验 + 评分
│   ├── strategies.py       所有规划算法（stub / random / greedy / two_opt）
│   ├── adjust.py           局部调整（打补丁 → 重铺时间轴 → 重算分）
│   └── planner.py          ★ 唯一对外入口 plan()
├── service\                用例层：三种入口共用（usecases / dto / exporters / compare）
├── api\                    HTTP 接口与服务启动（routes / server / asgi_min）
├── web\index.html          ★ 四页签前端（无构建、离线渲染）
├── llm\                    自然语言解析 / 解说 / 局部调整听懂
├── ui\                     Streamlit 页面（app.py 管交互，render.py 管展示）
├── mock\                   请求样例（6 个用例，评测与演示用）
├── eval\                   基线对比脚本与结果
├── tests\                  单元测试（210 个）
├── schemas\                接口契约的 JSON 文档版（给人看）
├── tools\                  高德步行耗时抓取（生成 travel_matrix.csv）
└── docs\                   接口契约 / 验收清单 / 需求说明书 / submission 交付物
```

`outputs/` 由 `main.py` 自动创建，存放生成的行程 JSON，不进版本库。

---

## 四、文档索引

**先看这三份**（新增的执行手册，按顺序读）：

| 文档 | 什么时候看 |
|---|---|
| `docs/00-总纲与路线图.md` | **每次开工前 2 分钟**：现在在哪、下一步做什么、分支清单 |
| `docs/03-分阶段操作手册.md` | **每条分支开工时**：一步步怎么做（命令权威源） |
| `docs/04-验收清单.md` | **每次 AI 交活后**：怎么判断能不能收（验收权威源） |

**配套三份**：

| 文档 | 什么时候看 |
|---|---|
| `docs/02-提示词手册.md` | 给 AI 说什么话（T00～T15 可复制任务卡） |
| `docs/01-环境与工具.md` | 环境/IDE/插件/报错（换电脑、装插件时） |
| `docs/05-LearnBuddy协作节奏.md` | 什么时候开新对话、开场怎么写 |

**参考文档**：

| 文档 | 什么时候看 |
|---|---|
| `docs/使用手册.md` | 第一次上手：怎么跑、改东西在哪改、报错怎么修 |
| `docs/接口契约.md` | 要动数据结构时看（改字段必须同步这里） |
| `docs/分工与所有权.md` | 分活、避免两人改同一个文件 |
| `docs/需求说明书.md` | 课程要求与评分口径（已锁定，含差距清单） |
| `docs/场景定义.md` | 日常出行场景的边界、六种情形、已知缺口 |
| `docs/数据来源.md` | 46 个点的来源与核实纪律 |
| `docs/submission/课程报告.md` | **交付物**：课程报告 |
| `docs/submission/答辩讲稿.md` | **交付物**：15 分钟讲稿 + 演示步骤 + 翻车预案 |
| `docs/submission/AI出行规划器-答辩.pptx` | **交付物**：12 页答辩 PPT |
| `config/README.md` | 换场景 |
| `data/README.md` | 换 POI 数据 |
| `eval/results.md` | 算法对比结果（自动生成） |

## 五、分工与文件所有权

见 `docs/分工与所有权.md`。核心规则：**共同文件（★）改之前必须互相知会**。

## 六、当前进度

- [x] 工程骨架：目录、依赖、gitignore、git 仓库
- [x] 接口契约 v1 → **v1.4**（只增字段，`PlanMetrics` 零改动）
- [x] 数据层 + 大连凌水生活圈 **46 个点** + **145 条高德步行实测耗时**
- [x] 规划引擎 + 策略注册表（`stub` / `random` / `greedy` / `two_opt`）
- [x] 三个入口：CLI / Streamlit / 网页（四页签 + 一句话调整 + 算法对比）
- [x] 基线对比 + **210 个 unittest 全绿**，结果可复现（两次逐字节一致）
- [x] **已推送到 GitHub**：https://github.com/Haloha-Alt/ai-trip-planning
- [x] 出行规则打通：关门跳过、闭馆日、必去点、出发地、默认站数、无路网如实报
- [x] 交付物：课程报告 / 答辩讲稿 / 答辩 PPT（`docs/submission/`）
- [ ] **待办**：用户手动 push（本地领先 origin/main）；彩排（M6）与 `v1.0` tag

---

## 七、换场景怎么改

| 想改什么 | 改哪里 |
|---|---|
| 预算、时间窗、权重、交通方式、默认站数 | `config/scenarios/daily.toml` |
| 换一整套场景 | 复制 `template.toml` → 改名 → `--scenario xxx` |
| POI 清单、标签、坐标、开关门 | `data/cities/dalian/pois.csv` |
| 交通矩阵（实测耗时） | `data/cities/dalian/travel_matrix.csv`，或用 `tools/amap_matrix.py` 重抓 |
| 兴趣词 → 标签的映射 | `daily.toml` 的 `[interest_alias]` 段 |
| 加一座城市 | `data/cities/<key>/pois.csv` + `data/cities/registry.toml` 加一段 |

**Python 代码里不允许出现任何具体业务数值。**

---

## 八、GitHub 仓库（已接通）

远端：`https://github.com/Haloha-Alt/ai-trip-planning`，本地 `main` 跟踪 `origin/main`。

日常三条命令：

```bash
git pull                 # 开工先同步
git push                 # 收工就推（每完成一条分支立刻推，不要攒）
git log --oneline -5     # 看最近做了什么
```

分支 / 冲突 / PR / 回滚的完整流程见 `docs/03-分阶段操作手册.md` 第十二章。
认证已配好（Git Credential Manager + GitHub Desktop），正常 push 不会反复弹登录。
