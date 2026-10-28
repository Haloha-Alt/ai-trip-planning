# @author: 甲
# @reviewer: 乙
# @since: 2026-10-28
"""应用层（service）：UI（Streamlit）、API（Starlette）、CLI 三种入口共用的同一份业务逻辑。

为什么要这层
------------
没有它的时候，每个入口都要自己写一遍"读配置 → 解析文本 → 调引擎 → 落盘"，
于是 Streamlit 改了一个默认值，命令行那边就对不上 —— 这正是
`docs/04-验收清单.md` AC-44 踩过的坑（界面默认算法和场景配置不一致）。

有了这层：
    ui/    ──►  service/  ──►  engine/  ──►  data/
    api/   ──►  service/  ──►  engine/  ──►  data/
    main.py ─►  service/  ──►  engine/  ──►  data/

三种入口只负责"把参数收上来、把结果画出去"，业务规则只有一份。

⚠️ 包名为什么叫 service 而不是 app
--------------------------------
因为 Streamlit 启动 `ui/app.py` 时，会把 `ui/` 插到 `sys.path` 的最前面。
如果本包也叫 `app`，那么 `import app.exporters` 会先命中 `ui/app.py` 这个脚本，
报 `No module named 'app.exporters'; 'app' is not a package`。
**包名与脚本名相同是 Python 的经典坑**，改名是最省事的根治办法：
入口路径 `ui/app.py` 保持不变（文档和习惯都不用改），包这边换一个不撞的名字。
"""
