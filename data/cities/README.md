# 城市数据目录

> 城市 = **数据路由**，不是代码分支。
> 新增城市只需要加数据 + 加一段 registry，**不碰任何 Python 逻辑**。

---

## 一、目录结构

```
data/cities/
├── registry.toml          # 城市注册表：key / 名称 / 别名 / 中心坐标 / 数据文件路径
├── README.md              # 本文件
├── dalian/
│   └── pois.csv           # 该城市的 POI 表（列名与 data/pois.csv 完全一致）
└── beijing/
    └── pois.csv
```

---

## 二、怎么加一个新城市

1. 建目录 `data/cities/<key>/`，放一份 `pois.csv`
   - 列名与 `data/README.md` 的 `pois.csv` 口径一致（`poi_id,name,category,lat,lon,...`）
   - 多出来的列（如 `address` / `closed_weekdays`）会被自动识别；**不认识的列会被忽略**（不会让整行报废）
   - 编码：**UTF-8 with BOM**（Excel 另存选「CSV UTF-8（带 BOM）」）
   - `poi_id` 建议带城市前缀（`DL001` / `BJ001`），避免跨城市撞 ID

2. 在 `registry.toml` 里加一段：

```toml
[cities.<key>]
name = "城市中文名"
aliases = ["别名1", "pinyin"]      # 用于把用户输入解析到 key
lat = 0.0                          # 城市中心纬度（地图初始视野用）
lon = 0.0                          # 城市中心经度
poi = "cities/<key>/pois.csv"
matrix = "cities/<key>/travel_matrix.csv"   # 允许文件不存在，缺边走 geo 估算
```

3. 跑测试确认：`python -m unittest tests.test_cities -v`

---

## 三、解析规则（`data/cities.py`）

匹配顺序：key 全等 → 中文名全等 → 别名全等 → 子串命中（取最长）。
都认不出来返回 `None` —— 调用方回退到默认数据，**不报错**。

| 用户输入 | 解析结果 |
|---|---|
| `dalian` / `DALIAN` / `  dalian  ` | `dalian` |
| `大连` / `大连市` | `dalian` |
| `我想去大连玩两天` | `dalian` |
| `火星` | `None` → 回退默认数据 + 一条 warn |

---

## 四、当前数据的可信度

`source = "seed"` 表示**人工整理的种子数据**：

- 坐标是**近似值**（取景点中心点量级），用于算距离与画地图，**不能当导航用**
- 门票价格是**常见票价**，淡旺季/优惠政策会变
- `closed_weekdays` 只填了"周一闭馆"这类**长期固定**的规则

计划中的降级链（见 `docs/00-总纲与路线图.md`）会用高德 Web 服务 / LLM 去校正这些值，
校正结果仍写回本目录的 `pois.csv`，并把 `source` 改成对应的来源。
