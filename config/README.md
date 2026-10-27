# 场景配置怎么改

`config/scenarios/` 下的每一个 `.toml` 文件就是一个"场景"。
**所有业务参数都在这里，改这里不需要动任何 Python 代码。**

---

## 三种改法

| 你想做什么 | 怎么操作 |
|---|---|
| **调参数**（预算 / 时间窗 / 权重 / 交通方式） | 直接编辑 `demo.toml`，保存即可生效（网页刷新一下） |
| **换一整套场景** | 复制 `template.toml` → 改名成 `xxx.toml` → 命令行加 `--scenario xxx`，或在网页下拉框里选 |
| **换城市 / 换 POI 语义** | 改 `data/pois.csv` + 本文件里的 `[interest_alias]` 段，代码零改动 |

命令行指定场景：

```bash
.venv\Scripts\python.exe main.py --scenario xxx
```

也可以设环境变量 `TRIP_SCENARIO=xxx`，优先级：**命令行参数 > 环境变量 > 默认 demo**。

---

## 字段说明

见 `template.toml`（每个字段都有注释）。几个容易填错的：

- `daily_budget`：**填 0 或留空 = 不限制预算**，不是"预算 0 元"
- `day_start` / `day_end`：24 小时制 `"HH:MM"`，必须带引号
- `[weights]` 四个值建议之和为 1.0，不强制
- `[interest_alias]`：左边是用户可能输入的词，右边是 `data/pois.csv` 里 `tags` 列出现的值
- `[pace_factor]`：节奏 → **每天**站数系数。`relaxed = 0.7` 表示"轻松"时只排 70% 的站。
  实测（`--top-n 3`）：relaxed 出 2 站 / normal 出 3 站 / packed 出 4 站。
  不写这一段时引擎按 `1.0` 处理（= 节奏不生效），不会报错
- `max_poi_per_day`：**每天**站数上限，同时也是界面滑块的**最大值**。改成 1 会让滑块只剩一格
- `default_strategy`：用哪个算法，可选值见 `engine/strategies.py` 的 `STRATEGIES` 字典
- 注意 `template.toml` 也在这个目录，所以它会作为一个叫 `template` 的场景出现在下拉框和 `--scenario` 帮助里 —— 它是模板，不是真场景，别选它

---

## 铁律

**语义只准写在这个目录里。** 代码里出现任何具体的标签字面量、具体金额、具体时间，都算违规。
