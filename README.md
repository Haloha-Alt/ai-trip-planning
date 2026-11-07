# AI 出行与规划

> 大连理工大学《AI 大模型及其运用》课程项目 · 2 人小组
> 当前状态：**功能完成**（M0–M4 收尾，M5 交付物在 `docs/submission/`）
> 场景已锁定：**日常出行**（校园生活圈 46 个点，不是游客一日游）
> v1.5 新增：**城市约会模式**（商圈辐射 + 电影开场/订座锚点钉死，网页三模式切换）—— 见 `docs/24-约会模式设计.md`

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

# 4) 跑测试，确认项目健康（当前 320 个，全程不联网，5 秒）
.venv\Scripts\python.exe -m unittest discover -s tests -t .

# 5) 跑基线对比（4 个算法 × 6 个用例，结果写入 eval/results.md）
.venv\Scripts\python.exe -m eval.baseline --scenario daily

# 6) 断网兜底样例：校验 7 份 JSON 是否仍与当前引擎一致（M6 必跑）
.venv\Scripts\python.exe -m tools.make_demo_outputs --verify

# 7) 大模型自检（配了 LLM_API_KEY 才需要跑；不配也能演示，只是解说走规则）
.venv\Scripts\python.exe -m tools.llm_probe --narrate

# 8) 模型体检：网关上哪些模型**真的能用**（列表里有 ≠ 后端加载了）
.venv\Scripts\python.exe -m tools.model_bench

# 9) 生成"发给别人"的链接（自动从 bat 读口令；--check 会真打一次接口）
.venv\Scripts\python.exe -m tools.share_link
.venv\Scripts\python.exe -m tools.share_link https://你的穿透域名 --check

# 10) 加一座新城市（脚手架 → 抓 POI → 拉矩阵 → 体检，详见 docs/08）
.venv\Scripts\python.exe -m tools.new_city --scaffold suzhou --name 苏州 --lat 31.2989 --lon 120.5853
.venv\Scripts\python.exe -m tools.new_city --fetch-pois suzhou --keyword 图书馆 --keyword 食堂 --dry-run
.venv\Scripts\python.exe -m tools.new_city --check

# 11) 上线前自检（部署 / 开穿透之前跑，逐项告诉你卡在哪一层）
.venv\Scripts\python.exe -m tools.preflight --with-tests

# 12) 开隧道并直接打印"能发人"的链接（认得出 cpolar / cloudflared）
.venv\Scripts\python.exe -m tools.tunnel

# 开发期可选：不开浏览器验证前端画得对不对（13 项断言 + 生成静态预览页）
node _render_check.js
```

> 🔑 **大模型用的是校内网关**（`.env` 已是可用配置）：`LLM_BASE_URL=http://aigw.dlut.edu.cn/v1`、
> `LLM_MODEL=DeepSeek-V4-Flash-0731-W8A8`。两个必须知道的特性：
> ① 网关**只有 http、没有 https**，所以后端直连没问题，但**不能从前端页面直接调**；
> ② 网关**强制流式返回** —— 请求里写 `stream=false` 也照样回 `data: {...}`，`llm/client.py`
> 已内置 SSE 拼接（`_content_from_sse`），换回公网服务商（返回整包 JSON）同样能跑。

> 🧭 **多模型路由**（`LLM_ROUTER=llm`，`llm/router.py`）：一个 Key 就能用全部 25 个模型，
> **不需要额外申请 Key**。"用哪个模型"是请求体里的一个字段，不是另一个账号。
> 规则先判难度（短需求→fast、长/约束多→reasoning、长行程→long），规则拿不准才请小模型裁决；
> 裁判只允许回答四个角色之一，坏帧/超时一律回退默认模型。页面上会多一个「模型」标签，
> 悬停看得到选它的理由（rule / judge / fallback）。`LLM_ROUTER=off` 即完全恢复原行为。

> ⚠️ **最后一条（前端自检）需要 Node.js，本机不一定装**。PyCharm 的终端不继承 WorkBuddy 的 PATH，
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

## 二·五、让别人也能打开（局域网 / 公网）

默认**只有本机能开**：服务只监听 `127.0.0.1`，而且大模型 Key 只在你机器的 `.env` 里。
"别人打开"取决于你选哪条路：

| 方式 | 命令 / 做法 | 适用 | 注意 |
|---|---|---|---|
| **A. 局域网**（同 Wi-Fi） | `python -m api.server --host=0.0.0.0`，别人访问 `http://<你的内网IP>:8000/` | 课堂、宿舍、答辩同屋 | 校园网若开了"终端隔离"，同 SSID 也连不上；Windows 防火墙要放行 |
| **B. 内网穿透** | 本机照常启动，再用 cpolar / frp / ngrok 把 8000 映射成公网地址 | 发给不在同一网络的同学 | 免费版限速、临时域名会变；**务必设 ACCESS_CODE** |
| **C. 部署到服务器** | 整个项目跑在校内服务器 / 云主机上，Key 放服务器的环境变量 | 长期可用、要给别人看 | 服务器必须**能访问 `aigw.dlut.edu.cn`**；若网关限校园网，就得部署在校内 |

三条路有一个共同点：**Key 始终留在"跑后端的那台机器"上**，不会进到任何人的浏览器。

### 穿透起来之后：一条命令算好链接（别手抄）

```bash
# 不带参数：只输出内网链接，顺带告诉你 8000 在不在跑、口令是什么
.venv\Scripts\python.exe -m tools.share_link

# 穿透给了你域名，直接喂给它，会自动拼上口令并真打一次接口验证
.venv\Scripts\python.exe -m tools.share_link https://abcd.r3.cpolar.cn --check
```

它会输出类似：

```
== 本机服务 ==
  127.0.0.1:8000  [在跑]
  访问口令         trip-8261
== 可以发出去的链接 ==
  同一 Wi-Fi：http://192.168.43.8:8000/?code=trip-8261
  公网：      https://abcd.r3.cpolar.cn/?code=trip-8261
== 自检 ==
  公网    https://abcd.r3.cpolar.cn    [OK] HTTP 200，城市 2 个
```

`--check` 失败时会告诉你卡在哪一层：服务没跑、口令不对（401）、隧道没开、域名抄错。
**发人之前跑一次，比发出去被问"打不开"省事得多。**

### 最省事：双击 `分享给他人.bat`

文件开头两行就是要改的全部：

```bat
set WEB_HOST=0.0.0.0
set ACCESS_CODE=trip-8261
```

双击后窗口里会打印两行，直接抄给对方：

```
[注意] 已对外开放监听 —— 同一个 Wi-Fi 下的设备才连得上
       发给别人：http://10.15.49.87:8000/?code=trip-8261
       （上面这条已经带口令，对方直接打开就行，不用再输）
```

内网地址每次换网络都会变（连热点和连校园网就不是一个），以窗口里打印的为准。
页面「接口状态」下面也会显示同一条链接，点「复制」即可发人。

> **同学打不开？先分清是哪一类：**
>
> 1. **防火墙**：第一次启动 uvicorn 弹"是否允许访问"时点了取消。
>    管理员 PowerShell 放行一次：`netsh advfirewall firewall add rule name="ai-trip 8000" dir=in action=allow protocol=TCP localport=8000`
> 2. **校园网终端隔离**（更容易中招）：2026-10-31 实测，本机连 `DLUT-LingShui 2`
>    （NetworkCategory=Public）时，防火墙对 `.venv\Scripts\python.exe` 已有放行规则，
>    手机连同一个 Wi-Fi 仍然打不开 —— 交换机层就不让客户端互访，改本地设置都没用。
>    **只能绕开**：手机开热点、电脑连热点（一般 192.168.43.x 段，互通）；
>    或者直接走 B（内网穿透），一条公网地址谁都能开。

### ⚠️ GitHub Pages 为什么不行（重要）

**Pages 只能托管静态文件，不能跑 Python。** 这个项目的 `/api/plan` 是 Python 后端，
`engine/`、`data/` 全在服务端，Pages 上没有东西能执行它们。

那"改成纯前端、让每个人自己填 Key"呢？**同样走不通**，三条都是硬约束：

1. 网关是 **http**，Pages 是 **https** → 浏览器直接按"混合内容"拦掉请求，跨域（CORS）也不会放行；
2. Key 写进前端 = 公开给你的每一个访问者；
3. 所以 Key 必须待在服务端 —— 而 Pages 没有服务端。

GitHub 仍然有用，但用途是**代码托管和备份**（clone 下来自己跑、或作为部署到服务器的源），
不是"别人打开就能用"。要"别人打开就能用"，还是 A（局域网）/ B（内网穿透）/ C（服务器）。

### 访问口令（开放出去之前一定要设）

```bash
# PowerShell
$env:ACCESS_CODE="你自己定的口令"
.venv\Scripts\python.exe -m api.server --host=0.0.0.0
```

- 配了之后，`/api/*` 都需要 `?code=口令`，否则返回 401（带 `code_required` 标记）
- 前端第一次打开会弹框问一次，之后存在 `localStorage`，不用反复输
- **不配 = 任何拿到地址的人都能白嫖学校的大模型额度**，别偷这个懒
- 本地开发时不用配，`ACCESS_CODE` 留空就是关闭

---

## 三、目录结构

```
.
├── main.py                 命令行入口：读场景 → 解析 → 规划 → 打印 + 落盘 JSON
├── config\scenarios\       ★ 业务参数全在这里（daily / visit / date / commute / errand / rainy / tight-budget）
├── data\                   ★ POI 与交通矩阵；data\cities\<key>\ 按城市分目录
├── engine\
│   ├── models.py           ★★ 接口契约（唯一的类定义，冻结文件，v1.5：新增 zone / anchors）
│   ├── config.py           场景配置加载
│   ├── geo.py              距离 / 时间 / 费用估算
│   ├── constraints.py      硬约束校验 + 评分（v1.5 新增 ANCHOR_MISS）
│   ├── strategies.py       所有规划算法（stub / random / greedy / two_opt / date）
│   ├── adjust.py           局部调整（打补丁 → 重铺时间轴 → 重算分）
│   └── planner.py          ★ 唯一对外入口 plan()
├── service\                用例层：三种入口共用（usecases / dto / exporters / compare / date_mode）
├── api\                    HTTP 接口与服务启动（routes / server / asgi_min；v1.5 新增 /api/date/* 三端点）
├── web\index.html          ★ 页签前端（无构建、离线渲染；三模式：校内游览 / 访客参观 / 城市约会）
├── llm\                    自然语言解析 / 解说 / 局部调整听懂 / 访客与约会简报（date_brief）
├── ui\                     Streamlit 页面（app.py 管交互，render.py 管展示）
├── mock\                   请求样例（6 个用例，评测与演示用）
├── eval\                   基线对比脚本与结果
├── tests\                  单元测试（320 个，含约会模式 test_date_mode.py）
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
| `docs/06-内网穿透与分享.md` | 要把网页给别人看（cpolar 实操 / 故障对照 / 安全底线） |
| `docs/07-v2.0路线图.md` | **下一步做什么**：四条主线 + 编号任务 + 里程碑 |
| `docs/08-部署上线.md` | 买域名、备案、ECS、Nginx、HTTPS（**上线前必读第零节**） |
| `docs/09-上线操作清单.md` | **要你亲自做的事**：勾选式清单，每步带预期结果 |
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
- [x] 基线对比 + **262 个 unittest 全绿**，全程离线（约 2 秒），结果可复现（两次逐字节一致）
- [x] **已推送到 GitHub**：https://github.com/Haloha-Alt/ai-trip-planning
- [x] 出行规则打通：关门跳过、闭馆日、必去点、出发地、默认站数、无路网如实报
- [x] 交付物：课程报告 / 答辩讲稿 / 答辩 PPT（`docs/submission/`）
- [x] **大模型真接入**（校内网关 + 强制流式兼容 + 多模型路由 + 模型体检工具）
- [x] **对外分享**：访问口令 + `分享给他人.bat` + `tools/share_link.py`
- [x] **M6 彩排完成**：262 测试全绿、7 份兜底样例 `全部一致`、断网演示通过、`v1.0` tag 已打
- [ ] **待办**：只差推 tag —— `git push origin main --follow-tags`
  （分支已与 `origin/main` 同步；**GitHub Desktop 在没有待推提交时不会推 tag**，
  按钮只显示 "Fetch origin"，所以要用命令行或网页 Release）
- [ ] 下一步：按 `docs/07-v2.0路线图.md` 推进 v2.0

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
