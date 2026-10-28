# @author: 甲
# @reviewer: 乙
# @since: 2026-10-28
"""应用层：UI（Streamlit）、API（FastAPI）、CLI 三种入口共用的同一份业务逻辑。

为什么要这层
------------
没有它的时候，每个入口都要自己写一遍"读配置 → 解析文本 → 调引擎 → 落盘"，
于是 Streamlit 改了一个默认值，命令行那边就对不上 —— 这正是
`docs/04-验收清单.md` AC-44 踩过的坑（界面默认算法和场景配置不一致）。

有了这层：
    ui/   ──►  app/  ──►  engine/  ──►  data/
    api/  ──►  app/  ──►  engine/  ──►  data/
    main.py ─► app/  ──►  engine/  ──►  data/

三种入口只负责"把参数收上来、把结果画出去"，业务规则只有一份。
"""
