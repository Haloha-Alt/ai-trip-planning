# AI 出行与规划

> 大连理工大学 Python 课程项目 · 2 人小组
> 当前阶段：**工程骨架**（业务场景待定，所有业务参数外置，代码零改动即可换场景）

---

## 一、环境要求

- Python **≥ 3.11**（用到标准库 `tomllib`）；本项目开发用 **3.14.6**
- 依赖：`streamlit`、`pandas`、`requests`、`python-dotenv`（见 `requirements.txt`）

---

## 二、三条命令跑起来

在 PyCharm 底部 Terminal（工作目录自动是项目根）里执行：

```bash
# 1) 命令行跑通整条链路
.venv\Scripts\python.exe main.py --scenario demo

# 2) 打开网页界面
.venv\Scripts\python.exe -m streamlit run ui/app.py

# 3) 跑测试，确认项目健康
.venv\Scripts\python.exe -m unittest discover -s tests -t . -v

# 4) 跑基线对比（所有算法 × 所有用例，结果写入 eval/results.md）
.venv\Scripts\python.exe -m eval.baseline
```

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
├── config\scenarios\       ★ 业务参数全在这里（换场景只改这里）
├── data\                   ★ POI 与距离矩阵（换数据只改这里）
├── engine\
│   ├── models.py           ★★ 接口契约（唯一的类定义，冻结文件）
│   ├── config.py           场景配置加载
│   ├── geo.py              距离 / 时间 / 费用估算
│   ├── constraints.py      硬约束校验 + 评分
│   ├── strategies.py       所有规划算法（新增算法 = 一个函数 + 一行字典）
│   └── planner.py          ★ 唯一对外入口 plan()
├── llm\                    自然语言解析（当前是规则 stub）
├── ui\                     Streamlit 页面（app.py 管交互，render.py 管展示）
├── mock\                   请求样例（测试与演示用）
├── eval\                   基线对比脚本与结果
├── tests\                  单元测试
├── schemas\                接口契约的 JSON 文档版（给人看）
└── docs\                   接口契约 / 分工 / 使用手册 / 需求说明书
```

`outputs/` 由 `main.py` 自动创建，存放生成的行程 JSON，不进版本库。

---

## 四、文档索引

| 文档 | 什么时候看 |
|---|---|
| `docs/使用手册.md` | **第一次上手先看这个**：怎么跑、改东西在哪改、报错怎么修 |
| `docs/接口契约.md` | 要动数据结构时看（改字段必须同步这里） |
| `docs/分工与所有权.md` | 分活、避免两人改同一个文件 |
| `docs/需求说明书.md` | 待定 —— 等助教答复评分细则后填 |
| `docs/演示脚本.md` | 答辩前 |
| `config/README.md` | 换场景 |
| `data/README.md` | 换 POI 数据 |
| `eval/results.md` | 算法对比结果（自动生成） |

## 五、分工与文件所有权

见 `docs/分工与所有权.md`。核心规则：**共同文件（★）改之前必须互相知会**。

## 六、当前进度

- [x] 工程骨架：目录、依赖、gitignore、git 本地仓库
- [x] 接口契约 v1（冻结）
- [x] 数据层 + 占位数据
- [x] 规划引擎 + 策略注册表（stub / random）
- [x] Streamlit 端到端跑通
- [x] 基线对比 + 文档
- [ ] **等助教答复后**：锁定场景 → 填 `config/scenarios/` + `data/` → 写真实算法进 `strategies.py`

---

## 七、换场景怎么改

| 想改什么 | 改哪里 |
|---|---|
| 预算、时间窗、权重、交通方式 | `config/scenarios/demo.toml` |
| 换一整套场景 | 复制 `template.toml` → 改名 → `--scenario xxx` |
| POI 清单、标签、坐标 | `data/pois.csv` |
| 兴趣词 → 标签的映射 | `demo.toml` 的 `[interest_alias]` 段 |

**Python 代码里不允许出现任何具体业务数值。**

---

## 八、以后怎么接 GitHub（现在还没接）

当前只有本地 git 提交，没有远程仓库。等你建好 GitHub 仓库后执行：

```bash
git remote add origin https://github.com/<你的用户名>/<仓库名>.git
git branch -M main
git push -u origin main
```

如果提示要登录，用 GitHub Desktop 登录一次，或配 Personal Access Token。
